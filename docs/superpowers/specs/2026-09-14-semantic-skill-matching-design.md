# Semantic skill matching and a Python-computed match score — design

Date: 2026-09-14
Status: implemented on branch claude/github-issue-2-d2b846
Issue: https://github.com/Tiqni/sira/issues/2

## 1. Goal

1. `compute_gap_analysis` must treat a job skill as **covered** when the CV
   expresses the same concept, even with different words. Example: the job
   skill "Technical leadership and mentorship" is covered by a CV bullet
   "mentor to ~30 engineers".
2. Match against the **whole original CV text** (summary, skills, experience
   bullets, projects, certifications, publications, education), not only the
   `skills` list.
3. `match_score` and `overall_recommendation` become **bounded, explainable
   numbers computed in Python** from coverage percentages, with the formula
   written down in `ARCHITECTURE.md`. The report LLM no longer subtracts
   penalties.
4. A regression test reproduces the symptom from the issue (score collapsing
   to 0/100 while the CV clearly covers the concepts) and asserts hard-skill
   coverage ≥ 50 % and `match_score` ≥ 50.

## 2. Decisions already made

| Decision | Choice | Why |
|---|---|---|
| How "same concept" is decided | One LLM **judge** call (a small pydantic-ai agent that answers covered / not covered with evidence) | Works with whatever `--model` the user already picked (OpenAI, Ollama, Groq, …); no new dependency; DBOS-checkpointed for free; gives a human-readable reason for each match. Rejected: embeddings (need a second model choice per provider, Groq has no embeddings endpoint, threshold to tune, no explanation), local sentence-transformers (torch + ~100 MB download), synonym dictionary (does not solve the issue's examples). |
| Scope | Hard and soft **skills** only. ATS keywords stay literal | `keyword_coverage_percent` models what an applicant tracking system does: exact text search on the tailored CV. A keyword that is only implied will not be found by an ATS, so the literal number stays honest. |
| Which CV the judge reads | The **original** CV | Gaps describe what the candidate actually has, not how the writer phrased it. Unchanged from today. |
| Where the score is computed | Pure Python (`sira/utils/cv_diff.py`) | Bounded and explainable; testable without a model. |
| Who writes `overall_recommendation` | Pure Python, from the score | Today the LLM applies thresholds to the same lists; moving it keeps score and verdict consistent. |
| Failure mode of the judge | Never fails the run; undecided skills stay "missing" (today's behaviour) and a warning is logged | Same graceful-fallback pattern as the quality gate. |
| Determinism | Given the judge's answer, every number is deterministic. The judge itself is an LLM call and is not bit-for-bit deterministic | Accepted trade-off; documented in `ARCHITECTURE.md`. |

## 3. Facts verified against the code

- Gap detection today is exact lowercase equality against `original.skills`
  only (`sira/utils/cv_diff.py`, `compute_gap_analysis`). Keyword coverage is a
  case-insensitive substring search over `tailored.model_dump_json()`.
- `match_score` and `overall_recommendation` are produced by `report_agent`
  (`sira/workflows/agents.py`) from a prompt rule: coverage % − 5 per missing
  hard skill − 2 per missing soft skill; verdict thresholds on keyword coverage
  and missing-hard-skill count.
- `compute_gap_analysis` has one call site: the report phase of
  `ResumeTailorWorkflow` (`sira/workflows/__init__.py`). `tailor` and
  `re-tailor` both go through it.
- Every agent is built with `capabilities=[_durability()]` (`DBOSDurability`),
  so an agent run inside `tailor_workflow` is a checkpointed DBOS step. Agent
  runs inside one workflow must be sequential (no `asyncio.gather`).
- `run_agent(agent, prompt, *, agent_label, usage, usage_limits)` resolves the
  model tier through `_AGENT_TIERS[agent_label]` and emits reporter events.
- Tests never hit a real model: `tests/conftest.py` sets
  `models.ALLOW_MODEL_REQUESTS = False`; existing agent tests use
  `agent.override(model=TestModel(...))` / `FunctionModel`.
- The MkDocs site under `docs/` (`docs/agents.md`, `docs/output.md`,
  `docs/extending.md`, `docs/project-layout.md`) also documents
  `GapAnalysis` and the report agent, and CI builds it with `--strict`.

## 4. Components

### 4.1 Output models (`sira/models/agents/output.py`)

```python
class SkillMatch(BaseModel):
    skill: str                      # exactly as given in the job analysis
    covered: bool
    evidence: str = ""              # short quote from the CV; "" when not covered

class SkillMatchResult(BaseModel):
    matches: list[SkillMatch]

class GapAnalysis(BaseModel):       # existing model, new fields added
    missing_hard_skills: list[str] = []
    missing_soft_skills: list[str] = []
    covered_hard_skills: list[str] = []          # NEW
    covered_soft_skills: list[str] = []          # NEW
    skill_evidence: dict[str, str] = {}          # NEW: skill -> CV quote ("" for literal hits)
    hard_skill_coverage_percent: float = 0.0     # NEW
    soft_skill_coverage_percent: float = 0.0     # NEW
    covered_keywords: list[str] = []
    missing_keywords: list[str] = []
    keyword_coverage_percent: float = 0.0

class ReportNarrative(BaseModel):   # NEW: what report_agent now produces
    suggestions_to_strengthen: list[str] = []
    audit_summary: str
    recommendation_rationale: str
```

`FinalReport` keeps its current shape. All new `GapAnalysis` fields have
defaults so stored reports and existing tests keep validating.

### 4.2 Pure-Python helpers (`sira/utils/skill_matching.py`, new)

- `render_cv_text(cv: CV) -> str` — one plain-text block with labelled
  sections (`Summary:`, `Skills:`, `Experience:` with `company — role (dates)`
  and one bullet per line, `Projects:`, `Education:`, `Certifications:`,
  `Publications:`). Used both for the literal pre-pass and as the judge's
  input, so both see the same text.
- `literal_matches(skills: Sequence[str], cv_text: str) -> dict[str, SkillMatch]`
  — case-insensitive whole-term check (the normalised skill must be bounded by
  non-alphanumeric characters or the text edge; `C++`, `.NET`, `Node.js` still
  match themselves). A plain substring check let one-letter skills such as `C`,
  `R`, `Go` match almost any CV and inflate hard-skill coverage. Returns a
  `SkillMatch(covered=True, evidence="")` for every hit. This is the pre-pass:
  hits never reach the LLM.

### 4.3 Judge agent (`sira/workflows/agents.py`)

- `skill_matcher_agent = Agent(_DEFAULT_MODEL, name="sira.skill_matcher",
  model_settings=MODEL_SETTINGS, output_type=SkillMatchResult, retries=3,
  capabilities=[_durability()])`.
- `_AGENT_TIERS["Skill Matcher"] = "fast"`.
- System prompt (rules, in this order):
  1. You receive a candidate's CV as plain text and a numbered list of job
     skills. For each skill decide whether the CV shows the candidate has done
     or used that concept.
  2. Covered means the same concept is present even when the wording differs
     (abbreviations, synonyms, a bullet that describes the activity). Examples:
     "K8s" covers "Kubernetes"; "mentor to ~30 engineers" covers "Technical
     leadership and mentorship".
  3. Not covered means the CV gives no evidence. When unsure, answer not
     covered. Never invent evidence.
  4. `evidence` must be a short quote (≤ 200 characters) copied from the CV
     text; empty string when not covered.
  5. Return exactly one entry per input skill, using the skill text exactly as
     given, in the same order.
- `@skill_matcher_agent.output_validator` `_validate_skill_matches`: reads the
  expected skill list from `ctx.deps` (a `tuple[str, ...]`, so the agent's
  `deps_type=tuple[str, ...]`); raises `ModelRetry` naming the missing or
  unexpected skills when the sets differ; truncates `evidence` to 200
  characters; forces `evidence=""` when `covered` is false. No quality gate.
- User prompt: `CV:\n<cv_text>\n\nSkills to judge:\n1. <skill>\n2. <skill>…`.

### 4.4 Orchestration (`sira/workflows/skill_matching.py`, new)

```python
async def match_skills(
    original: CV,
    job: JobAnalysis,
    *,
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
) -> dict[str, SkillMatch]:
```

1. `skills = dedupe(job.hard_skills + job.soft_skills)` — de-duplication by the
   normalised key (whitespace-collapsed casefold; `skill_key` in
   `sira/utils/skill_matching.py`), first spelling kept, order kept. A skill
   listed as both hard and soft, or under different case/whitespace (e.g.
   "Leadership" and "leadership"), is judged once under whichever spelling
   appeared first. The returned mapping carries every ORIGINAL spelling a job
   posting used, not just the judged one — `compute_gap_analysis` looks each
   job skill up by its exact string, so the single verdict is fanned back out
   to every spelling that shares its key.
2. `cv_text = render_cv_text(original)`; `matches = literal_matches(skills, cv_text)`.
3. `pending = [s for s in skills if s not in matches]`. If empty, return
   `matches` — no LLM call.
4. `result = await run_agent(skill_matcher_agent, prompt, agent_label="Skill
   Matcher", usage=usage, usage_limits=usage_limits, deps=tuple(pending))`
   (`run_agent` gains a `deps` keyword forwarded to `agent.run`).
5. Merge the judge's entries into `matches`. Return.
6. On `UnexpectedModelBehavior` or `UsageLimitExceeded`, or DBOS step-retry
   exhaustion (`DBOSMaxStepRetriesExceeded`) inside the durable workflow: log a
   warning through the active reporter ("Skill matcher unavailable — falling
   back to literal matching") and return `matches` from step 2 (pending
   skills stay missing).

Called sequentially in the report phase, before `compute_gap_analysis`.

### 4.5 Gap analysis and score (`sira/utils/cv_diff.py`)

```python
def compute_gap_analysis(
    original: CV,
    tailored: CV | None,
    job: JobAnalysis,
    *,
    skill_matches: Mapping[str, SkillMatch] | None = None,
) -> GapAnalysis
```

- `skill_matches is None` → today's exact-equality behaviour against
  `original.skills` (kept for existing callers and tests). Covered skills get
  `evidence=""`.
- Otherwise a skill is covered iff `skill_matches[skill].covered` is true; a
  skill absent from the mapping is missing.
- `hard_skill_coverage_percent = covered_hard / len(job.hard_skills) * 100`
  (0.0 when the job lists no hard skills); same for soft. Rounded to 1 decimal
  like `keyword_coverage_percent`.
- `skill_evidence` holds every covered skill (hard and soft) → its evidence.
- Keyword coverage unchanged.

```python
def compute_match_score(gap: GapAnalysis) -> int
def compute_recommendation(score: int, gap: GapAnalysis)
    -> Literal["Strong Match", "Partial Match", "Weak Match"]
```

Score formula (weights sum to 1.0):

| Bucket | Weight | Coverage source |
|---|---|---|
| hard skills | 0.60 | `hard_skill_coverage_percent` |
| soft skills | 0.20 | `soft_skill_coverage_percent` |
| ATS keywords | 0.20 | `keyword_coverage_percent` |

`score = round(Σ weight_b × coverage_b)` over the buckets the job actually
lists items for. A bucket is non-empty when `covered + missing` for it is
non-empty in the `GapAnalysis` (so no `JobAnalysis` argument is needed; when
the writer failed, `missing_keywords` still holds every job keyword). Weights of empty buckets
are dropped and the remaining weights are rescaled to sum to 1.0. When every
bucket is empty the score is 0. The result is an `int` in 0–100 by
construction; `round` uses Python's built-in (banker's rounding) and the test
suite pins one half-way case so the behaviour is explicit.

Recommendation:

- **Strong Match**: `score >= 75` and (`hard_skill_coverage_percent >= 75` or
  the hard-skill bucket is empty).
- **Partial Match**: `score >= 50`.
- **Weak Match**: otherwise.

Worked example from the issue (keywords 33.3 %; after semantic matching
hard 70 %, soft 80 %): `0.6·70 + 0.2·80 + 0.2·33.3 = 64.7 → 65`,
Partial Match. Today the same run reports 0 / Weak Match.

### 4.6 Report agent contract (`sira/workflows/agents.py`)

- `report_agent.output_type = ReportNarrative`.
- Prompt changes: remove the `overall_recommendation` and `match_score`
  rules and the "copy these fields VERBATIM" block and the `passed` rule (the
  workflow already sets `passed` itself). Add: "You are given the computed
  match score, the verdict, and for each covered skill the CV evidence.
  `recommendation_rationale` must explain the verdict using those numbers and
  the missing skills; do not recompute or contradict them." Anti-cliché rules
  stay as they are.
- User prompt built by the workflow gains `Match score: <n>/100`,
  `Verdict: <recommendation>`, and the existing `Gap Analysis:` JSON (which now
  contains the evidence).

### 4.7 Workflow wiring (`sira/workflows/__init__.py`, report phase)

Order inside the existing `try:` block:

1. `cv_diff = compute_cv_diff(...)` (unchanged)
2. `skill_matches = await match_skills(original_cv, job_analysis, usage=total_usage, usage_limits=USAGE_LIMITS)` — reporter log line
   `"🔎 Skill Matcher: judging N job skills against your CV..."` before the
   call.
3. `gap_analysis = compute_gap_analysis(original_cv, new_cv, job_analysis, skill_matches=skill_matches)`
4. `match_score = compute_match_score(gap_analysis)`;
   `recommendation = compute_recommendation(match_score, gap_analysis)`
5. `report_agent` run with the enriched prompt → `ReportNarrative`.
6. `FinalReport(..., overall_recommendation=recommendation, match_score=match_score, ...)`.

`match_skills` runs once per run — its inputs (original CV, job analysis) do
not change across Hook 1/Hook 2 feedback loops — so only the keyword bucket
(20 points at most) can move when the writer re-runs. Hook 2 shows hard/soft
coverage so the user can see what feedback can change.

The existing `except UnexpectedModelBehavior` around the report phase keeps
its meaning: it covers the report agent; the matcher handles its own
fallback inside `match_skills`. Hook 2 (weak-match checkpoint) is unchanged in
logic; its lists are now accurate.

### 4.8 Presentation

- `sira/main.py::_print_report_to_console`: after `SKILL GAPS`, print
  `SKILLS COVERED` with `Hard: a/b (x%) · Soft: c/d (y%)` and one line per
  covered skill: `  ✅ <skill>` for a literal hit, `  ✅ <skill> — "<evidence>"`
  when the judge supplied a quote.
- `sira/utils/markdown_writer.py`: same information as a bullet list under a
  new `## ✅ Skills Covered` section placed before `## 🚧 Skill Gaps`; the
  score section gains one line: `_Score = 0.6·hard + 0.2·soft + 0.2·keywords
  (see ARCHITECTURE.md)_`.

## 5. Error handling summary

| Failure | Behaviour |
|---|---|
| Judge returns wrong/missing skills | `ModelRetry` up to 3 times |
| Judge still fails / usage limit hit | Warning via reporter; pending skills stay missing; run continues |
| Job lists no skills | No judge call; coverage buckets empty; score from the remaining buckets |
| `tailored is None` (writer failed) | Skill coverage still computed from the original CV; keyword coverage 0 % as today |
| Reporter unavailable | `_safe_report` semantics — logging never aborts |

## 6. Testing

All offline; no test may need a model or a key.

- `tests/test_skill_matching_utils.py` (new): `render_cv_text` includes every
  section and every bullet; `literal_matches` is case-insensitive, ignores
  extra whitespace, returns `evidence=""`.
- `tests/test_skill_matcher.py` (new), using `skill_matcher_agent.override(model=FunctionModel(...))`.
  `skill_matcher_agent` carries `DBOSDurability`, which attaches an
  event-stream handler, so pydantic-ai always calls the streaming path — a
  plain `FunctionModel(fn)` with no `stream_function` fails every call. Tests
  build the model through a small streaming helper (`fn` and `stream_function`
  sharing one `answer(messages) -> dict`) instead.
  - validator raises `ModelRetry` when a skill is missing or an extra one is
    returned, then accepts a corrected second answer;
  - validator blanks evidence for `covered=False` and truncates to 200 chars;
  - `match_skills` skips the LLM when every skill matches literally (the
    function model asserts it was never called);
  - `match_skills` falls back to the literal result when the model raises
    after retries, and logs a warning.
- `tests/test_cv_diff.py` (extended): `compute_gap_analysis` with and
  without `skill_matches`; coverage percentages; `compute_match_score` for
  all-covered (100), none (0), empty-bucket rescaling, a pinned half-way
  rounding case; `compute_recommendation` at each threshold edge (74/75, 49/50,
  and the hard-coverage guard).
- `tests/test_report_integration.py`: updated for `ReportNarrative`.
- `tests/test_semantic_match_regression.py` (new): a fixture CV containing
  "context engineering", "mentor to ~30 engineers", "cross-functional"
  platform work, production RAG and monitoring, and a `JobAnalysis` with the
  long descriptive skills from the issue. A scripted judge marks the concept
  matches covered. Asserts `hard_skill_coverage_percent >= 50` and
  `match_score >= 50` and recommendation is not "Weak Match". Caveat recorded
  in the test docstring: with a scripted judge this proves the pipeline and
  formula, not a real model's judgement.
- Existing workflow tests that stub `report_agent` are updated to the new
  output type; any test that inspects `FinalReport.match_score` from a stubbed
  narrative moves to asserting the Python-computed value.

## 7. Documentation

- `ARCHITECTURE.md`: add the Skill Matcher to the agent list and the data-flow
  diagram; the score table and recommendation thresholds from §4.5; correct
  every "GapAnalysis is pure Python / no LLM" sentence to "skill coverage uses
  one judge call, then all metrics and the score are pure Python".
- `AGENTS.md`, `.github/copilot-instructions.md`: new agent row, tier, and the
  `GapAnalysis` / `ReportNarrative` fields; "add a new agent" checklist
  unchanged.
- MkDocs site: `docs/agents.md`, `docs/output.md`, `docs/extending.md`,
  `docs/project-layout.md` (new files) — same content, and `mkdocs build
  --strict` must stay green.
- `README.md`: one paragraph on semantic skill matching and the score
  formula.
- `CLAUDE.md`: update the bullet that says `CVDiff`/`GapAnalysis` are computed
  "in pure Python, not by an LLM".
- `CHANGELOG.md` is generated by commitizen; not edited by hand.

## 8. Out of scope

- Semantic matching of ATS keywords.
- Embedding-based matching or a local model.
- Any change to the Writer/Reviewer/Auditor loop.
- A CLI flag to disable the judge (the fallback already covers unavailable
  models; add a flag only if users ask).

## 9. Delivery

One pull request on this branch, conventional title
`feat: semantic job skill matching and python-computed match score`, closing
issue #2. Implementation follows `docs/superpowers/plans/2026-09-14-semantic-skill-matching.md`
(written next with the writing-plans skill).
