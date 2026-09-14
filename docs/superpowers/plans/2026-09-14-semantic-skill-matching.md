# Semantic Skill Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Job skills are judged as covered by the CV when the CV expresses the same concept (one LLM judge call over the whole CV text), and `match_score` / `overall_recommendation` are computed in pure Python from coverage percentages.

**Architecture:** A literal pre-pass (pure Python) marks skills whose text appears in the CV; the remaining skills go to a new `skill_matcher_agent` (pydantic-ai, fast tier, DBOS-checkpointed) that returns `covered` + `evidence` per skill. `compute_gap_analysis` consumes those matches, and two new pure functions compute the score and the verdict. The report agent shrinks to narrative-only output (`ReportNarrative`).

**Tech Stack:** Python 3.13, pydantic v2, pydantic-ai 2.43 (`Agent`, `output_validator`, `ModelRetry`, `FunctionModel` for tests), DBOS via `DBOSDurability`, pytest + pytest-anyio + pytest-subtests, ruff.

**Spec:** `docs/superpowers/specs/2026-09-14-semantic-skill-matching-design.md`

## Global Constraints

- Always `uv run …`; never bare `python`/`pip`.
- Tests never call a real model: `tests/conftest.py` sets `models.ALLOW_MODEL_REQUESTS = False`. Agent tests use `agent.override(model=FunctionModel(...))` or `monkeypatch.setattr("sira.workflows.agents.<agent>.run", stub)`.
- Agent construction stays key-free: every new agent is built with `_DEFAULT_MODEL` and `capabilities=[_durability()]` exactly like the existing ones.
- No `asyncio.gather` over agent runs inside `tailor_workflow`; the matcher runs sequentially in the report phase.
- Conventional Commits for every commit (`feat:`, `test:`, `docs:`, `refactor:`), imperative mood, lowercase, no trailing period, body explains *why*. Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Before the final push: `uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest` must all exit 0, and `uv run mkdocs build --strict` must pass (install with `uv sync --group docs`).
- Keep the cliché blacklist in prompts unchanged.
- Score formula (fixed by the spec): weights hard 60 / soft 20 / keyword 20; empty buckets drop out and the remaining weights are rescaled; all empty → 0; Python `round()` (banker's rounding). Verdict: Strong = score ≥ 75 and (hard coverage ≥ 75 or no hard skills); Partial = score ≥ 50; else Weak.
- Evidence quotes are capped at 200 characters (`MAX_EVIDENCE_CHARS`).

## File map

| File | Responsibility |
|---|---|
| `sira/models/agents/output.py` (modify) | `SkillMatch`, `SkillMatchResult`, `ReportNarrative`; new fields on `GapAnalysis` |
| `sira/utils/skill_matching.py` (create) | `render_cv_text`, `literal_matches` — pure Python |
| `sira/utils/cv_diff.py` (modify) | `compute_gap_analysis(..., skill_matches=)`, `compute_match_score`, `compute_recommendation` |
| `sira/workflows/agents.py` (modify) | `skill_matcher_agent` + validator, `_AGENT_TIERS`, `run_agent(deps=)`, `report_agent` → `ReportNarrative` |
| `sira/workflows/skill_matching.py` (create) | `match_skills` orchestration: pre-pass → judge → fallback |
| `sira/workflows/__init__.py` (modify) | report phase wiring |
| `sira/main.py`, `sira/utils/markdown_writer.py` (modify) | "Skills covered" presentation |
| `tests/test_skill_matching_utils.py`, `tests/test_skill_matcher.py`, `tests/test_semantic_match_regression.py` (create); `tests/test_cv_diff.py`, `tests/test_report_integration.py`, `tests/workflows/stubs.py`, `tests/workflows/test_resume_tailor_workflow.py`, `tests/workflows/test_reporter_events.py`, `tests/test_markdown_writer.py` (create) | tests |
| `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `docs/agents.md`, `docs/output.md`, `docs/extending.md`, `docs/project-layout.md` | docs |

---

### Task 1: Output models

**Files:**
- Modify: `sira/models/agents/output.py` (the `GapAnalysis` class, and add three classes after it)
- Test: `tests/test_cv_diff.py` (append one test)

**Interfaces:**
- Produces: `SkillMatch(skill: str, covered: bool, evidence: str = "")`, `SkillMatchResult(matches: list[SkillMatch])`, `ReportNarrative(suggestions_to_strengthen: list[str] = [], audit_summary: str, recommendation_rationale: str)`, and `GapAnalysis` fields `covered_hard_skills`, `covered_soft_skills`, `skill_evidence: dict[str, str]`, `hard_skill_coverage_percent`, `soft_skill_coverage_percent` (all with defaults).

- [ ] **Step 1: Write the failing test** — append to `tests/test_cv_diff.py`:

```python
# ---------------------------------------------------------------------------
# Output model contracts for semantic matching
# ---------------------------------------------------------------------------


def test_gap_analysis_new_fields_default_empty(subtests):
    from sira.models.agents.output import GapAnalysis, SkillMatch, SkillMatchResult

    gap = GapAnalysis()
    with subtests.test("covered_hard_skills"):
        assert gap.covered_hard_skills == []
    with subtests.test("covered_soft_skills"):
        assert gap.covered_soft_skills == []
    with subtests.test("skill_evidence"):
        assert gap.skill_evidence == {}
    with subtests.test("hard_pct"):
        assert gap.hard_skill_coverage_percent == 0.0
    with subtests.test("soft_pct"):
        assert gap.soft_skill_coverage_percent == 0.0
    with subtests.test("skill_match_evidence_default"):
        assert SkillMatch(skill="Python", covered=True).evidence == ""
    with subtests.test("skill_match_result_roundtrip"):
        result = SkillMatchResult(matches=[SkillMatch(skill="Python", covered=False)])
        assert result.model_dump()["matches"][0]["covered"] is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cv_diff.py::test_gap_analysis_new_fields_default_empty -q`
Expected: FAIL with `ImportError: cannot import name 'SkillMatch'`.

- [ ] **Step 3: Implement the models** — in `sira/models/agents/output.py` replace the `GapAnalysis` class with:

```python
class GapAnalysis(BaseModel):
    """Gap analysis between job requirements and the original CV.

    Skill coverage is decided by ``compute_gap_analysis`` from ``SkillMatch``
    results (literal pre-pass plus the skill matcher agent). Keyword coverage
    is a literal substring check on the tailored CV — the ATS view.
    """

    missing_hard_skills: list[str] = []
    missing_soft_skills: list[str] = []
    covered_hard_skills: list[str] = []
    covered_soft_skills: list[str] = []
    skill_evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Covered skill -> short CV quote; empty string for literal hits.",
    )
    hard_skill_coverage_percent: float = 0.0
    soft_skill_coverage_percent: float = 0.0
    covered_keywords: list[str] = []
    missing_keywords: list[str] = []
    keyword_coverage_percent: float = 0.0


class SkillMatch(BaseModel):
    """One job skill judged against the CV."""

    skill: str = Field(description="The job skill, exactly as listed in the job analysis.")
    covered: bool
    evidence: str = Field(
        default="",
        description="Short quote from the CV that shows the skill; empty when not covered.",
    )


class SkillMatchResult(BaseModel):
    """Output of the skill matcher agent: one entry per skill it was asked about."""

    matches: list[SkillMatch]


class ReportNarrative(BaseModel):
    """The only fields the report agent writes; the workflow computes the rest."""

    suggestions_to_strengthen: list[str] = []
    audit_summary: str
    recommendation_rationale: str
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cv_diff.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sira/models/agents/output.py tests/test_cv_diff.py
git commit -m "feat(models): add skill match and report narrative models

Semantic skill matching needs a per-skill verdict with evidence, and the
report agent will stop producing the score, so its output shrinks to the
narrative fields.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Pure-Python helpers — `render_cv_text` and `literal_matches`

**Files:**
- Create: `sira/utils/skill_matching.py`
- Create: `tests/test_skill_matching_utils.py`

**Interfaces:**
- Consumes: `CV`, `WorkExperience`, `SkillMatch` from `sira.models.agents.output`.
- Produces: `render_cv_text(cv: CV) -> str`; `literal_matches(skills: Sequence[str], cv_text: str) -> dict[str, SkillMatch]`; `normalise_text(text: str) -> str`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_skill_matching_utils.py`:

```python
"""Pure-Python helpers for semantic skill matching — no model calls."""

from sira.models.agents.output import CV, WorkExperience
from sira.utils.skill_matching import literal_matches, normalise_text, render_cv_text


def _cv() -> CV:
    return CV(
        full_name="Alice Dev",
        contact_info="alice@example.com",
        summary="Backend engineer doing context engineering for chat products.",
        skills=["Python", "K8s"],
        projects=["Open-source RAG toolkit"],
        experience=[
            WorkExperience(
                company="Acme",
                role="Staff Engineer",
                dates="2020-2024",
                highlights=["Mentor to ~30 engineers.", "Built   monitoring dashboards."],
            )
        ],
        education=["BSc CS"],
        certifications=["AWS SAA"],
        publications=["Talk: Context windows"],
    )


def test_render_cv_text_includes_every_section(subtests):
    text = render_cv_text(_cv())
    for needle in (
        "Summary: Backend engineer doing context engineering",
        "Skills: Python, K8s",
        "Experience:",
        "- Acme — Staff Engineer (2020-2024)",
        "  - Mentor to ~30 engineers.",
        "Projects:",
        "- Open-source RAG toolkit",
        "Education:",
        "- BSc CS",
        "Certifications:",
        "- AWS SAA",
        "Publications:",
        "- Talk: Context windows",
    ):
        with subtests.test(needle=needle):
            assert needle in text


def test_render_cv_text_skips_empty_optional_sections():
    cv = _cv().model_copy(update={"projects": [], "certifications": [], "publications": []})
    text = render_cv_text(cv)
    assert "Projects:" not in text
    assert "Certifications:" not in text
    assert "Publications:" not in text


def test_normalise_text_collapses_whitespace_and_case():
    assert normalise_text("  Built   Monitoring\nDashboards ") == "built monitoring dashboards"


def test_literal_matches_is_case_insensitive_and_whitespace_tolerant(subtests):
    text = render_cv_text(_cv())
    found = literal_matches(
        ["python", "Monitoring dashboards", "Kubernetes", "context   engineering"], text
    )
    with subtests.test("python"):
        assert found["python"].covered is True
    with subtests.test("monitoring"):
        assert "Monitoring dashboards" in found
    with subtests.test("context_engineering_whitespace"):
        assert "context   engineering" in found
    with subtests.test("kubernetes_not_literal"):
        assert "Kubernetes" not in found
    with subtests.test("evidence_empty_for_literal"):
        assert found["python"].evidence == ""
    with subtests.test("skill_key_is_original_text"):
        assert found["python"].skill == "python"


def test_literal_matches_ignores_blank_skills():
    assert literal_matches(["", "   "], "anything") == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_skill_matching_utils.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sira.utils.skill_matching'`.

- [ ] **Step 3: Implement** — create `sira/utils/skill_matching.py`:

```python
"""Pure-Python helpers for semantic skill matching.

No LLM calls here. ``render_cv_text`` turns a ``CV`` into one plain-text block
that both the literal pre-pass and the skill matcher agent read, so both see
the same text. ``literal_matches`` is the pre-pass: a skill whose text appears
in the CV is covered without asking a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from sira.models.agents.output import CV, SkillMatch


def normalise_text(text: str) -> str:
    """Collapse whitespace and fold case so comparisons ignore formatting."""
    return " ".join(text.split()).casefold()


def render_cv_text(cv: CV) -> str:
    """Render every CV section as labelled plain text, one item per line."""
    lines: list[str] = [
        f"Summary: {cv.summary.strip()}",
        "Skills: " + ", ".join(cv.skills),
    ]
    if cv.experience:
        lines.append("Experience:")
        for exp in cv.experience:
            lines.append(f"- {exp.company} — {exp.role} ({exp.dates})")
            lines.extend(f"  - {bullet}" for bullet in exp.highlights)
    optional_sections = (
        ("Projects", cv.projects),
        ("Education", cv.education),
        ("Certifications", cv.certifications),
        ("Publications", cv.publications),
    )
    for label, items in optional_sections:
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


def literal_matches(skills: Sequence[str], cv_text: str) -> dict[str, SkillMatch]:
    """Return a covered ``SkillMatch`` for each skill whose text appears in the CV.

    Keys are the skill strings exactly as passed in. Evidence is left empty:
    the match is the skill text itself.
    """
    haystack = normalise_text(cv_text)
    found: dict[str, SkillMatch] = {}
    for skill in skills:
        needle = normalise_text(skill)
        if needle and needle in haystack:
            found[skill] = SkillMatch(skill=skill, covered=True, evidence="")
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skill_matching_utils.py -q`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add sira/utils/skill_matching.py tests/test_skill_matching_utils.py
git commit -m "feat(utils): add cv text rendering and literal skill pre-pass

The pre-pass keeps skills that appear verbatim in the CV away from the
model, so the judge only sees the skills that need a semantic decision.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `compute_gap_analysis` consumes skill matches

**Files:**
- Modify: `sira/utils/cv_diff.py` (`compute_gap_analysis`, lines 104–161)
- Test: `tests/test_cv_diff.py` (append)

**Interfaces:**
- Consumes: `SkillMatch`, `GapAnalysis` from Task 1.
- Produces: `compute_gap_analysis(original, tailored, job, *, skill_matches: Mapping[str, SkillMatch] | None = None) -> GapAnalysis` populating the new fields in both modes.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cv_diff.py`:

```python
# ---------------------------------------------------------------------------
# compute_gap_analysis with semantic skill matches
# ---------------------------------------------------------------------------


def test_gap_analysis_literal_mode_populates_covered_lists_and_percentages(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    with subtests.test("covered_hard"):
        assert gap.covered_hard_skills == ["Python", "Docker"]
    with subtests.test("missing_hard"):
        assert gap.missing_hard_skills == ["Kubernetes", "Terraform"]
    with subtests.test("hard_pct"):
        assert gap.hard_skill_coverage_percent == 50.0
    with subtests.test("soft_all_missing"):
        assert gap.covered_soft_skills == []
        assert gap.soft_skill_coverage_percent == 0.0
    with subtests.test("evidence_empty_for_literal"):
        assert gap.skill_evidence == {"Python": "", "Docker": ""}


def test_gap_analysis_uses_skill_matches_when_given(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    from sira.models.agents.output import SkillMatch

    matches = {
        "Python": SkillMatch(skill="Python", covered=True, evidence=""),
        "Docker": SkillMatch(skill="Docker", covered=True, evidence=""),
        "Kubernetes": SkillMatch(
            skill="Kubernetes", covered=True, evidence="ran services on K8s"
        ),
        "Terraform": SkillMatch(skill="Terraform", covered=False, evidence=""),
        "teamwork": SkillMatch(skill="teamwork", covered=True, evidence="pair programming"),
        # "communication" deliberately absent from the mapping -> missing
    }
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis, skill_matches=matches)
    with subtests.test("covered_hard"):
        assert gap.covered_hard_skills == ["Python", "Docker", "Kubernetes"]
    with subtests.test("missing_hard"):
        assert gap.missing_hard_skills == ["Terraform"]
    with subtests.test("hard_pct"):
        assert gap.hard_skill_coverage_percent == 75.0
    with subtests.test("soft_split"):
        assert gap.covered_soft_skills == ["teamwork"]
        assert gap.missing_soft_skills == ["communication"]
    with subtests.test("soft_pct"):
        assert gap.soft_skill_coverage_percent == 50.0
    with subtests.test("evidence"):
        assert gap.skill_evidence["Kubernetes"] == "ran services on K8s"
        assert gap.skill_evidence["teamwork"] == "pair programming"
        assert "Terraform" not in gap.skill_evidence
    with subtests.test("keywords_still_literal"):
        assert "Kubernetes" in gap.missing_keywords


def test_gap_analysis_skill_matches_do_not_need_tailored_cv(
    original_cv: CV, job_analysis: JobAnalysis, subtests
):
    from sira.models.agents.output import SkillMatch

    matches = {s: SkillMatch(skill=s, covered=True) for s in job_analysis.hard_skills}
    gap = compute_gap_analysis(original_cv, None, job_analysis, skill_matches=matches)
    with subtests.test("hard_all_covered"):
        assert gap.hard_skill_coverage_percent == 100.0
    with subtests.test("keywords_zero_without_tailored"):
        assert gap.keyword_coverage_percent == 0.0
        assert gap.missing_keywords == list(job_analysis.keywords_to_target)


def test_gap_analysis_percent_is_zero_when_job_lists_no_skills(original_cv: CV, tailored_cv: CV):
    job = JobAnalysis(
        job_title="x",
        company_name="y",
        summary="z",
        hard_skills=[],
        soft_skills=[],
        key_responsibilities=[],
        keywords_to_target=["Python"],
    )
    gap = compute_gap_analysis(original_cv, tailored_cv, job, skill_matches={})
    assert gap.hard_skill_coverage_percent == 0.0
    assert gap.soft_skill_coverage_percent == 0.0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cv_diff.py -q -k "skill_matches or literal_mode or no_skills"`
Expected: FAIL — `AttributeError`/`TypeError` (`covered_hard_skills` empty, unexpected keyword `skill_matches`).

- [ ] **Step 3: Implement** — in `sira/utils/cv_diff.py` change the module docstring and imports, and replace `compute_gap_analysis`:

```python
"""Pure-Python utilities for computing CV diffs, gap analysis, and the match score.

No LLM calls in this module. Skill coverage decisions arrive as ``SkillMatch``
values (from the literal pre-pass and the skill matcher agent); everything
computed here from them is deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping

from sira.models.agents.output import (
    CV,
    CVDiff,
    ExperienceChange,
    GapAnalysis,
    JobAnalysis,
    SkillMatch,
)
```

```python
def _percent(covered: int, total: int) -> float:
    """Coverage as a percentage rounded to one decimal; 0.0 when total is 0."""
    return round(covered / total * 100.0, 1) if total > 0 else 0.0


def _split_skills(
    skills: list[str],
    original_skills_lower: set[str],
    skill_matches: Mapping[str, SkillMatch] | None,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Split job skills into (covered, missing, evidence-by-covered-skill).

    With ``skill_matches`` a skill is covered iff its match says so; a skill
    absent from the mapping is missing. Without it, fall back to exact
    lowercase equality against the CV's ``skills`` list (evidence stays "").
    """
    covered: list[str] = []
    missing: list[str] = []
    evidence: dict[str, str] = {}
    for skill in skills:
        if skill_matches is None:
            is_covered = skill.lower() in original_skills_lower
            quote = ""
        else:
            match = skill_matches.get(skill)
            is_covered = match is not None and match.covered
            quote = match.evidence if match is not None else ""
        if is_covered:
            covered.append(skill)
            evidence[skill] = quote
        else:
            missing.append(skill)
    return covered, missing, evidence


def compute_gap_analysis(
    original: CV,
    tailored: CV | None,
    job: JobAnalysis,
    *,
    skill_matches: Mapping[str, SkillMatch] | None = None,
) -> GapAnalysis:
    """Compute skill and keyword gaps between the original CV and job requirements.

    Args:
        original: The candidate's original CV (used for skill gap detection).
        tailored: The tailored CV (used for keyword coverage). Pass None if the
                  writer failed — keyword coverage will default to 0%.
        job: Structured job analysis with required skills and ATS keywords.
        skill_matches: Per-skill verdicts from ``match_skills`` (literal
                  pre-pass + skill matcher agent), keyed by the skill text as
                  listed in ``job``. None means literal matching against
                  ``original.skills`` only (the pre-semantic behaviour).

    Returns:
        GapAnalysis with covered/missing skills, evidence, and coverage metrics.
    """
    original_skills_lower = {s.lower() for s in original.skills}

    covered_hard, missing_hard, hard_evidence = _split_skills(
        job.hard_skills, original_skills_lower, skill_matches
    )
    covered_soft, missing_soft, soft_evidence = _split_skills(
        job.soft_skills, original_skills_lower, skill_matches
    )
    skill_fields = {
        "missing_hard_skills": missing_hard,
        "missing_soft_skills": missing_soft,
        "covered_hard_skills": covered_hard,
        "covered_soft_skills": covered_soft,
        "skill_evidence": {**hard_evidence, **soft_evidence},
        "hard_skill_coverage_percent": _percent(len(covered_hard), len(job.hard_skills)),
        "soft_skill_coverage_percent": _percent(len(covered_soft), len(job.soft_skills)),
    }

    # --- Keyword coverage (literal, from tailored CV text — the ATS view) ---
    if tailored is None:
        return GapAnalysis(
            **skill_fields,
            covered_keywords=[],
            missing_keywords=list(job.keywords_to_target),
            keyword_coverage_percent=0.0,
        )

    tailored_text = tailored.model_dump_json().lower()
    covered_kw: list[str] = []
    missing_kw: list[str] = []
    for keyword in job.keywords_to_target:
        if keyword.lower() in tailored_text:
            covered_kw.append(keyword)
        else:
            missing_kw.append(keyword)

    return GapAnalysis(
        **skill_fields,
        covered_keywords=covered_kw,
        missing_keywords=missing_kw,
        keyword_coverage_percent=_percent(len(covered_kw), len(job.keywords_to_target)),
    )
```

- [ ] **Step 4: Run the whole file**

Run: `uv run pytest tests/test_cv_diff.py -q`
Expected: all PASS (existing keyword/percent tests unchanged; `keyword_coverage_percent` is still rounded to 1 decimal).

- [ ] **Step 5: Commit**

```bash
git add sira/utils/cv_diff.py tests/test_cv_diff.py
git commit -m "feat(cv-diff): accept semantic skill matches in gap analysis

Gap analysis now records covered skills, their evidence, and coverage
percentages, and takes per-skill verdicts instead of only comparing the
skills list by exact text.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `compute_match_score` and `compute_recommendation`

**Files:**
- Modify: `sira/utils/cv_diff.py` (append)
- Test: `tests/test_cv_diff.py` (append)

**Interfaces:**
- Produces: `compute_match_score(gap: GapAnalysis) -> int`; `compute_recommendation(score: int, gap: GapAnalysis) -> Literal["Strong Match", "Partial Match", "Weak Match"]`; constants `SCORE_WEIGHT_HARD = 60`, `SCORE_WEIGHT_SOFT = 20`, `SCORE_WEIGHT_KEYWORDS = 20`, `STRONG_MATCH_MIN_SCORE = 75`, `STRONG_MATCH_MIN_HARD_COVERAGE = 75.0`, `PARTIAL_MATCH_MIN_SCORE = 50`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cv_diff.py`:

```python
# ---------------------------------------------------------------------------
# compute_match_score / compute_recommendation
# ---------------------------------------------------------------------------


def _gap(
    *,
    hard: tuple[int, int] = (0, 0),
    soft: tuple[int, int] = (0, 0),
    kw: tuple[int, int] = (0, 0),
):
    """Build a GapAnalysis from (covered, total) counts per bucket."""
    from sira.models.agents.output import GapAnalysis

    def names(prefix: str, n: int) -> list[str]:
        return [f"{prefix}{i}" for i in range(n)]

    def pct(c: int, t: int) -> float:
        return round(c / t * 100.0, 1) if t else 0.0

    return GapAnalysis(
        covered_hard_skills=names("h", hard[0]),
        missing_hard_skills=names("hm", hard[1] - hard[0]),
        hard_skill_coverage_percent=pct(*hard),
        covered_soft_skills=names("s", soft[0]),
        missing_soft_skills=names("sm", soft[1] - soft[0]),
        soft_skill_coverage_percent=pct(*soft),
        covered_keywords=names("k", kw[0]),
        missing_keywords=names("km", kw[1] - kw[0]),
        keyword_coverage_percent=pct(*kw),
    )


def test_match_score_all_covered_is_100():
    from sira.utils.cv_diff import compute_match_score

    assert compute_match_score(_gap(hard=(4, 4), soft=(2, 2), kw=(5, 5))) == 100


def test_match_score_nothing_covered_is_0():
    from sira.utils.cv_diff import compute_match_score

    assert compute_match_score(_gap(hard=(0, 4), soft=(0, 2), kw=(0, 5))) == 0


def test_match_score_all_buckets_empty_is_0():
    from sira.utils.cv_diff import compute_match_score

    assert compute_match_score(_gap()) == 0


def test_match_score_weights_60_20_20():
    from sira.utils.cv_diff import compute_match_score

    # 0.6*50 + 0.2*100 + 0.2*50 = 30 + 20 + 10
    assert compute_match_score(_gap(hard=(2, 4), soft=(2, 2), kw=(1, 2))) == 60


def test_match_score_rescales_when_a_bucket_is_empty():
    from sira.utils.cv_diff import compute_match_score

    # soft empty: (60*50 + 20*100) / 80 = 62.5 -> Python round() -> 62 (banker's)
    assert compute_match_score(_gap(hard=(2, 4), kw=(2, 2))) == 62


def test_match_score_single_bucket_uses_that_coverage():
    from sira.utils.cv_diff import compute_match_score

    # only hard skills listed: 2/3 -> 66.7 -> 67
    assert compute_match_score(_gap(hard=(2, 3))) == 67


def test_match_score_issue_example_is_well_above_zero():
    """Issue #2: keywords 33.3 %, semantic hard 70 %, soft 80 % -> 65, not 0."""
    from sira.utils.cv_diff import compute_match_score

    assert compute_match_score(_gap(hard=(7, 10), soft=(4, 5), kw=(15, 45))) == 65


def test_recommendation_thresholds(subtests):
    from sira.utils.cv_diff import compute_recommendation

    strong_gap = _gap(hard=(4, 4), soft=(2, 2), kw=(5, 5))
    cases = [
        (75, strong_gap, "Strong Match"),
        (74, strong_gap, "Partial Match"),
        (50, strong_gap, "Partial Match"),
        (49, strong_gap, "Weak Match"),
        (0, strong_gap, "Weak Match"),
    ]
    for score, gap, expected in cases:
        with subtests.test(score=score):
            assert compute_recommendation(score, gap) == expected


def test_recommendation_strong_needs_hard_coverage_75(subtests):
    from sira.utils.cv_diff import compute_recommendation

    with subtests.test("hard_70_blocks_strong"):
        gap = _gap(hard=(7, 10), soft=(5, 5), kw=(5, 5))  # hard 70 %
        assert compute_recommendation(90, gap) == "Partial Match"
    with subtests.test("hard_75_allows_strong"):
        gap = _gap(hard=(3, 4), soft=(5, 5), kw=(5, 5))  # hard 75 %
        assert compute_recommendation(90, gap) == "Strong Match"
    with subtests.test("no_hard_skills_allows_strong"):
        gap = _gap(soft=(5, 5), kw=(5, 5))
        assert compute_recommendation(90, gap) == "Strong Match"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cv_diff.py -q -k "match_score or recommendation"`
Expected: FAIL with `ImportError: cannot import name 'compute_match_score'`.

- [ ] **Step 3: Implement** — add `from typing import Literal` to the imports of `sira/utils/cv_diff.py` (after the `collections.abc` import), then append:

```python
# ---------------------------------------------------------------------------
# Match score and recommendation (documented in ARCHITECTURE.md)
# ---------------------------------------------------------------------------

SCORE_WEIGHT_HARD = 60
SCORE_WEIGHT_SOFT = 20
SCORE_WEIGHT_KEYWORDS = 20

STRONG_MATCH_MIN_SCORE = 75
STRONG_MATCH_MIN_HARD_COVERAGE = 75.0
PARTIAL_MATCH_MIN_SCORE = 50

Recommendation = Literal["Strong Match", "Partial Match", "Weak Match"]


def compute_match_score(gap: GapAnalysis) -> int:
    """Weighted coverage: hard skills 60, soft skills 20, ATS keywords 20.

    A bucket the job lists nothing for (covered + missing empty) is dropped
    and the remaining weights are rescaled to keep the score on 0–100. All
    buckets empty gives 0. Uses Python's built-in ``round`` (half to even).
    """
    buckets = (
        (
            SCORE_WEIGHT_HARD,
            gap.hard_skill_coverage_percent,
            len(gap.covered_hard_skills) + len(gap.missing_hard_skills),
        ),
        (
            SCORE_WEIGHT_SOFT,
            gap.soft_skill_coverage_percent,
            len(gap.covered_soft_skills) + len(gap.missing_soft_skills),
        ),
        (
            SCORE_WEIGHT_KEYWORDS,
            gap.keyword_coverage_percent,
            len(gap.covered_keywords) + len(gap.missing_keywords),
        ),
    )
    active = [(weight, pct) for weight, pct, total in buckets if total > 0]
    if not active:
        return 0
    weight_sum = sum(weight for weight, _ in active)
    score = sum(weight * pct for weight, pct in active) / weight_sum
    return max(0, min(100, round(score)))


def compute_recommendation(score: int, gap: GapAnalysis) -> Recommendation:
    """Verdict from the score, with a hard-skill guard on "Strong Match"."""
    hard_total = len(gap.covered_hard_skills) + len(gap.missing_hard_skills)
    hard_ok = (
        hard_total == 0
        or gap.hard_skill_coverage_percent >= STRONG_MATCH_MIN_HARD_COVERAGE
    )
    if score >= STRONG_MATCH_MIN_SCORE and hard_ok:
        return "Strong Match"
    if score >= PARTIAL_MATCH_MIN_SCORE:
        return "Partial Match"
    return "Weak Match"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cv_diff.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sira/utils/cv_diff.py tests/test_cv_diff.py
git commit -m "feat(cv-diff): compute match score and verdict in python

The report model used to subtract 5 points per missing hard skill, which
drove real runs to 0/100. The score is now a weighted coverage average
with fixed, documented weights.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `skill_matcher_agent`, its output validator, `run_agent(deps=)`

**Files:**
- Modify: `sira/workflows/agents.py` (imports at top; `run_agent` at ~114–145; `_AGENT_TIERS` at ~210; add the agent after `report_agent`)
- Create: `tests/test_skill_matcher.py`

**Interfaces:**
- Consumes: `SkillMatch`, `SkillMatchResult` (Task 1).
- Produces: `skill_matcher_agent: Agent[tuple[str, ...], SkillMatchResult]` named `"sira.skill_matcher"`, `MAX_EVIDENCE_CHARS = 200`, `_AGENT_TIERS["Skill Matcher"] == "fast"`, `run_agent(..., deps: Any = None)` forwarding `deps` to `agent.run` only when not `None`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_skill_matcher.py`:

```python
"""skill_matcher_agent contract tests with FunctionModel (no real model calls)."""

import re

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from sira.models.agents.output import SkillMatchResult

pytestmark = pytest.mark.anyio

_SKILL_LINE = re.compile(r"^\d+\. (.+)$", re.MULTILINE)


def _skills_in_prompt(messages) -> list[str]:
    """Read the numbered 'Skills to judge' list back out of the user prompt."""
    for part in messages[0].parts:
        content = getattr(part, "content", "")
        if isinstance(content, str) and "Skills to judge:" in content:
            return _SKILL_LINE.findall(content.split("Skills to judge:", 1)[1])
    return []


def _judge(answers: list[dict]):
    """A FunctionModel that returns the given match dicts on every call."""

    def fn(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name, args={"matches": answers}
                )
            ]
        )

    return FunctionModel(fn)


async def test_validator_accepts_complete_answer_and_canonicalises_names(subtests):
    from sira.workflows.agents import skill_matcher_agent

    model = _judge(
        [
            {"skill": "kubernetes", "covered": True, "evidence": "ran K8s clusters"},
            {"skill": "Team  Leadership", "covered": False, "evidence": "should be blanked"},
        ]
    )
    with skill_matcher_agent.override(model=model):
        result = await skill_matcher_agent.run(
            "CV:\nx\n\nSkills to judge:\n1. Kubernetes\n2. Team Leadership",
            deps=("Kubernetes", "Team Leadership"),
        )
    out = result.output
    assert isinstance(out, SkillMatchResult)
    with subtests.test("names_canonical"):
        assert [m.skill for m in out.matches] == ["Kubernetes", "Team Leadership"]
    with subtests.test("evidence_kept_when_covered"):
        assert out.matches[0].evidence == "ran K8s clusters"
    with subtests.test("evidence_blanked_when_not_covered"):
        assert out.matches[1].evidence == ""


async def test_validator_retries_on_missing_skill_then_accepts():
    from sira.workflows.agents import skill_matcher_agent

    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        skills = ["Kubernetes", "Terraform"]
        answered = skills[:1] if calls["n"] == 1 else skills  # first answer incomplete
        args = {"matches": [{"skill": s, "covered": False, "evidence": ""} for s in answered]}
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
        )

    with skill_matcher_agent.override(model=FunctionModel(fn)):
        result = await skill_matcher_agent.run(
            "CV:\nx\n\nSkills to judge:\n1. Kubernetes\n2. Terraform",
            deps=("Kubernetes", "Terraform"),
        )
    assert calls["n"] == 2
    assert [m.skill for m in result.output.matches] == ["Kubernetes", "Terraform"]


async def test_validator_rejects_extra_and_duplicate_skills_until_retries_exhausted():
    from sira.workflows.agents import skill_matcher_agent

    model = _judge(
        [
            {"skill": "Kubernetes", "covered": True, "evidence": "k"},
            {"skill": "Kubernetes", "covered": True, "evidence": "k"},
            {"skill": "Rust", "covered": True, "evidence": "r"},
        ]
    )
    with skill_matcher_agent.override(model=model):
        with pytest.raises(UnexpectedModelBehavior):
            await skill_matcher_agent.run(
                "CV:\nx\n\nSkills to judge:\n1. Kubernetes", deps=("Kubernetes",)
            )


async def test_validator_truncates_long_evidence():
    from sira.workflows.agents import MAX_EVIDENCE_CHARS, skill_matcher_agent

    model = _judge([{"skill": "Python", "covered": True, "evidence": "x" * 500}])
    with skill_matcher_agent.override(model=model):
        result = await skill_matcher_agent.run(
            "CV:\nx\n\nSkills to judge:\n1. Python", deps=("Python",)
        )
    assert len(result.output.matches[0].evidence) == MAX_EVIDENCE_CHARS


def test_skill_matcher_is_fast_tier():
    from sira.workflows.agents import _AGENT_TIERS

    assert _AGENT_TIERS["Skill Matcher"] == "fast"


async def test_run_agent_forwards_deps(monkeypatch):
    from sira.workflows import agents as agents_mod

    seen = {}

    async def fake_run(prompt, **kwargs):
        seen.update(kwargs)

        class R:
            output = None

        return R()

    monkeypatch.setattr(agents_mod.skill_matcher_agent, "run", fake_run)
    await agents_mod.run_agent(
        agents_mod.skill_matcher_agent, "p", agent_label="Skill Matcher", deps=("A",)
    )
    assert seen["deps"] == ("A",)


async def test_run_agent_omits_deps_when_none(monkeypatch):
    from sira.workflows import agents as agents_mod

    seen = {}

    async def fake_run(prompt, **kwargs):
        seen.update(kwargs)

        class R:
            output = None

        return R()

    monkeypatch.setattr(agents_mod.report_agent, "run", fake_run)
    await agents_mod.run_agent(agents_mod.report_agent, "p", agent_label="Report")
    assert "deps" not in seen
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_skill_matcher.py -q`
Expected: FAIL with `ImportError: cannot import name 'skill_matcher_agent'`.

- [ ] **Step 3: Implement** — in `sira/workflows/agents.py`:

(a) extend the output-model import:

```python
from sira.models.agents.output import (
    AuditResult,
    CV,
    JobAnalysis,
    QualityCheckResult,
    ReviewResult,
    FinalReport,
    SkillMatch,
    SkillMatchResult,
)
```

(b) in `run_agent`, add the `deps` keyword and forward it:

```python
async def run_agent(
    agent: Agent,
    prompt: str,
    *,
    verbose: bool = False,  # retained for call-site compatibility; reporter drives streaming
    agent_label: str = "",
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
    model: str | None = None,
    deps: Any = None,
) -> AgentRunResult:
    """Run an agent, emitting lifecycle events to the active reporter.

    Token streaming is done by the DBOSDurability event-stream handler on the
    agent (see _stream_to_reporter), so this always uses ``agent.run``: one
    code path inside and outside a DBOS workflow. ``deps`` is forwarded to
    ``agent.run`` only when given, so agents without a deps type are unaffected.
    """
    reporter = get_active_reporter()

    run_kwargs: dict[str, Any] = {"usage": usage, "usage_limits": usage_limits}
    resolved = model if model is not None else resolve_model(agent_label)
    if resolved is not None:
        run_kwargs["model"] = normalize_model_name(resolved)
    if deps is not None:
        run_kwargs["deps"] = deps
```

(the rest of the function body is unchanged.)

(c) add the tier:

```python
_AGENT_TIERS = {
    "Parser": "fast",
    "Analyst": "fast",
    "Quality Gate": "fast",
    "Reviewer": "fast",
    "Skill Matcher": "fast",  # yes/no per skill with a quote; a small model is enough
    "Writer": "strong",
    "Writer (refine)": "strong",
    "Auditor": "strong",
    "Report": "strong",
    "Cover Letter Writer": "strong",
    "Scraper": "fast",  # job_scraper_agent: cleanup pass on clean Markdown
}
```

(d) add the agent and validator directly after the `report_agent` definition (before the "Quality Gate Validators" banner):

```python
# --- Skill Matcher (judge) ---
# Responsibility: decide, per job skill, whether the ORIGINAL CV shows the same
# concept — even in different words — and quote the CV line as evidence. The
# workflow runs a literal pre-pass first, so this agent only sees the skills
# that need a semantic decision. Output feeds compute_gap_analysis; the score
# itself is pure Python (compute_match_score).
MAX_EVIDENCE_CHARS = 200

skill_matcher_agent = Agent(
    _DEFAULT_MODEL,
    name="sira.skill_matcher",
    model_settings=MODEL_SETTINGS,
    deps_type=tuple[str, ...],
    system_prompt="""
    You judge whether a candidate's CV covers each skill a job asks for.

    You receive the CV as plain text and a numbered list of job skills.
    For EACH skill decide whether the CV shows the candidate has done or used
    that concept.

    Rules:
    1. "Covered" means the same concept is present even when the wording differs:
       abbreviations, synonyms, or a bullet that describes the activity.
       Examples: "K8s" covers "Kubernetes"; "mentor to ~30 engineers" covers
       "Technical leadership and mentorship"; "built RAG pipeline in production"
       covers "Retrieval-augmented generation".
    2. "Not covered" means the CV gives no evidence. When unsure, answer not covered.
       Never invent evidence.
    3. evidence: a short quote (at most 200 characters) copied from the CV text
       that shows the skill. Empty string when not covered.
    4. Return exactly one entry per input skill, using the skill text exactly as
       given, in the same order. No extra skills, no duplicates.
    """,
    output_type=SkillMatchResult,
    retries=3,
    capabilities=[_durability()],
)


def _skill_key(text: str) -> str:
    return " ".join(text.split()).casefold()


@skill_matcher_agent.output_validator
async def _validate_skill_matches(
    ctx: RunContext[tuple[str, ...]], output: SkillMatchResult
) -> SkillMatchResult:
    """Require one entry per requested skill; canonicalise names; clean evidence."""
    expected_by_key = {_skill_key(s): s for s in (ctx.deps or ())}
    seen: set[str] = set()
    unexpected: list[str] = []
    cleaned: list[SkillMatch] = []
    for match in output.matches:
        key = _skill_key(match.skill)
        if key not in expected_by_key or key in seen:
            unexpected.append(match.skill)
            continue
        seen.add(key)
        evidence = match.evidence.strip()[:MAX_EVIDENCE_CHARS] if match.covered else ""
        cleaned.append(
            SkillMatch(skill=expected_by_key[key], covered=match.covered, evidence=evidence)
        )
    missing = [s for k, s in expected_by_key.items() if k not in seen]
    if missing or unexpected:
        raise ModelRetry(
            "Return exactly one entry per requested skill, names copied verbatim. "
            f"Missing: {missing}. Unexpected or duplicated: {unexpected}."
        )
    return SkillMatchResult(matches=cleaned)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skill_matcher.py tests/test_quality_gate.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sira/workflows/agents.py tests/test_skill_matcher.py
git commit -m "feat(agents): add skill matcher judge agent

One fast-tier call decides per job skill whether the CV shows the same
concept and quotes the CV line as evidence. run_agent learns a deps
keyword so the validator can check the answer against the request.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `match_skills` orchestration (pre-pass → judge → fallback)

**Files:**
- Create: `sira/workflows/skill_matching.py`
- Test: `tests/test_skill_matcher.py` (append)

**Interfaces:**
- Consumes: `render_cv_text`, `literal_matches` (Task 2); `skill_matcher_agent`, `run_agent` (Task 5); `get_active_reporter`; the test helper `_judge_model(answer)` already defined at the top of `tests/test_skill_matcher.py` (builds a `FunctionModel` with both `function` and `stream_function`, because every agent's DBOSDurability streams requests).
- Produces: `async def match_skills(original: CV, job: JobAnalysis, *, usage: RunUsage | None = None, usage_limits: UsageLimits | None = None) -> dict[str, SkillMatch]`; `build_matcher_prompt(cv_text: str, skills: Sequence[str]) -> str`; `unique_job_skills(job: JobAnalysis) -> list[str]`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_skill_matcher.py`:

```python
# ---------------------------------------------------------------------------
# match_skills orchestration
# ---------------------------------------------------------------------------

from sira.models.agents.output import CV, JobAnalysis, WorkExperience  # noqa: E402
from sira.reporting.base import NullReporter, use_reporter  # noqa: E402


class _LogReporter(NullReporter):
    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)


def _cv() -> CV:
    return CV(
        full_name="A",
        summary="Engineer who ran Kubernetes clusters and mentored juniors.",
        skills=["Python"],
        experience=[
            WorkExperience(
                company="Acme", role="Eng", dates="2020", highlights=["Mentor to 5 people"]
            )
        ],
        education=[],
    )


def _job(hard: list[str], soft: list[str]) -> JobAnalysis:
    return JobAnalysis(
        job_title="t",
        company_name="c",
        summary="s",
        hard_skills=hard,
        soft_skills=soft,
        key_responsibilities=[],
        keywords_to_target=[],
    )


def _table_judge(table: dict[str, tuple[bool, str]], calls: list[list[str]] | None = None):
    """Judge that answers from a table for whatever skills the prompt asks about.

    Built on ``_judge_model`` (defined above in this file) so it works with the
    agent's DBOSDurability streaming path.
    """

    def answer(messages) -> dict:
        asked = _skills_in_prompt(messages)
        if calls is not None:
            calls.append(asked)
        matches = []
        for skill in asked:
            covered, evidence = table.get(skill, (False, ""))
            matches.append({"skill": skill, "covered": covered, "evidence": evidence})
        return {"matches": matches}

    return _judge_model(answer)


async def test_match_skills_skips_model_when_everything_is_literal():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    with skill_matcher_agent.override(model=_table_judge({}, calls)):
        matches = await match_skills(_cv(), _job(["Python", "kubernetes"], []))
    assert calls == []
    assert matches["Python"].covered and matches["kubernetes"].covered


async def test_match_skills_sends_only_pending_skills_and_merges(subtests):
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    table = {
        "Technical leadership and mentorship": (True, "Mentor to 5 people"),
        "Rust": (False, ""),
    }
    with skill_matcher_agent.override(model=_table_judge(table, calls)):
        matches = await match_skills(
            _cv(), _job(["Python", "Rust"], ["Technical leadership and mentorship"])
        )
    with subtests.test("only_pending_sent"):
        assert calls == [["Rust", "Technical leadership and mentorship"]]
    with subtests.test("literal_kept"):
        assert matches["Python"].covered is True and matches["Python"].evidence == ""
    with subtests.test("judge_merged"):
        assert matches["Technical leadership and mentorship"].evidence == "Mentor to 5 people"
        assert matches["Rust"].covered is False


async def test_match_skills_dedupes_skills_listed_as_hard_and_soft():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    with skill_matcher_agent.override(model=_table_judge({"Rust": (False, "")}, calls)):
        await match_skills(_cv(), _job(["Rust"], ["Rust"]))
    assert calls == [["Rust"]]


async def test_match_skills_falls_back_to_literal_when_judge_fails():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    broken = _judge_model(lambda messages: {"matches": []})  # never answers -> retries exhausted
    reporter = _LogReporter()
    with use_reporter(reporter), skill_matcher_agent.override(model=broken):
        matches = await match_skills(_cv(), _job(["Python", "Rust"], []))
    assert matches["Python"].covered is True
    assert "Rust" not in matches
    assert any("falling back to literal" in line for line in reporter.logs)


async def test_match_skills_logs_how_many_skills_it_judges():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    reporter = _LogReporter()
    with use_reporter(reporter), skill_matcher_agent.override(model=_table_judge({})):
        await match_skills(_cv(), _job(["Python", "Rust"], ["Grit"]))
    assert any("2 of 3" in line for line in reporter.logs)


def test_build_matcher_prompt_numbers_skills():
    from sira.workflows.skill_matching import build_matcher_prompt

    prompt = build_matcher_prompt("Summary: x", ["A", "B"])
    assert prompt.endswith("Skills to judge:\n1. A\n2. B")
    assert prompt.startswith("CV:\nSummary: x")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_skill_matcher.py -q -k "match_skills or build_matcher"`
Expected: FAIL with `ModuleNotFoundError: No module named 'sira.workflows.skill_matching'`.

- [ ] **Step 3: Implement** — create `sira/workflows/skill_matching.py`:

```python
"""Semantic skill matching: literal pre-pass, then the skill matcher agent.

``match_skills`` is called once, sequentially, inside the report phase of the
durable workflow. The agent run is a DBOS-checkpointed step through
``DBOSDurability``; nothing here may run agents concurrently.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.usage import RunUsage, UsageLimits

from sira.models.agents.output import CV, JobAnalysis, SkillMatch
from sira.reporting.base import get_active_reporter
from sira.utils.skill_matching import literal_matches, render_cv_text
from sira.workflows.agents import _safe_report, run_agent, skill_matcher_agent

SKILL_MATCHER_LABEL = "Skill Matcher"


def unique_job_skills(job: JobAnalysis) -> list[str]:
    """Hard then soft skills, exact-string de-duplicated, first occurrence kept."""
    seen: set[str] = set()
    unique: list[str] = []
    for skill in (*job.hard_skills, *job.soft_skills):
        if skill not in seen:
            seen.add(skill)
            unique.append(skill)
    return unique


def build_matcher_prompt(cv_text: str, skills: Sequence[str]) -> str:
    numbered = "\n".join(f"{i}. {skill}" for i, skill in enumerate(skills, start=1))
    return f"CV:\n{cv_text}\n\nSkills to judge:\n{numbered}"


async def match_skills(
    original: CV,
    job: JobAnalysis,
    *,
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
) -> dict[str, SkillMatch]:
    """Decide, per job skill, whether the original CV covers it.

    Skills whose text appears in the CV are covered without a model call. The
    rest go to ``skill_matcher_agent`` in one request. If that request fails
    for good (retries or usage limit exhausted, provider error), the pending
    skills stay undecided — they count as missing, exactly as before semantic
    matching existed — and a warning is logged. This never fails the run.
    """
    reporter = get_active_reporter()
    skills = unique_job_skills(job)
    cv_text = render_cv_text(original)
    matches = literal_matches(skills, cv_text)
    pending = [skill for skill in skills if skill not in matches]
    if not pending:
        return matches

    _safe_report(
        reporter.log,
        f"\n🔎 Skill Matcher: judging {len(pending)} of {len(skills)} job skills "
        "against your CV (the rest matched literally)...",
    )
    try:
        result = await run_agent(
            skill_matcher_agent,
            build_matcher_prompt(cv_text, pending),
            agent_label=SKILL_MATCHER_LABEL,
            usage=usage,
            usage_limits=usage_limits,
            deps=tuple(pending),
        )
    except AgentRunError as exc:
        _safe_report(
            reporter.log,
            f"   ⚠️ Skill matcher unavailable ({type(exc).__name__}) — "
            "falling back to literal matching for the remaining skills.",
        )
        return matches

    for match in result.output.matches:
        matches[match.skill] = match
    return matches
```

`AgentRunError` is the pydantic-ai base class of `UnexpectedModelBehavior`, `UsageLimitExceeded`, and `ModelAPIError`/`ModelHTTPError` (verified on 2.43.0), so one clause covers "retries exhausted", "usage limit", and "provider down".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skill_matcher.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sira/workflows/skill_matching.py tests/test_skill_matcher.py
git commit -m "feat(workflows): orchestrate literal pre-pass and skill judge

Only skills that do not appear verbatim in the CV reach the model, and a
failing judge degrades to today's literal result instead of failing the
run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Report agent writes narrative only

**Files:**
- Modify: `sira/workflows/agents.py` (`report_agent` definition, ~lines 574–627)
- Modify: `tests/test_report_integration.py` (rewrite)

**Interfaces:**
- Produces: `report_agent.output_type is ReportNarrative`. The workflow (Task 8) will pass `Match score:` and `Verdict:` lines in the user prompt.

- [ ] **Step 1: Rewrite the tests** — replace the whole of `tests/test_report_integration.py` with:

```python
"""Integration tests for report_agent using TestModel (no real LLM calls)."""

import pytest
from pydantic_ai.models.test import TestModel
from pytest_subtests import SubTests

from sira.models.agents.output import ReportNarrative

pytestmark = pytest.mark.anyio

_PROMPT = (
    "Match score: 65/100\nVerdict: Partial Match\n"
    "CV Diff: {} Gap Analysis: {} Audit: {} Review: {} Job: {}"
)


async def test_report_agent_returns_report_narrative() -> None:
    from sira.workflows.agents import report_agent  # noqa: PLC0415 — avoids import-time LLM init

    custom = {
        "suggestions_to_strengthen": ["Get Kubernetes certification"],
        "audit_summary": "Hallucination score 1/10. AI cliché score 2/10.",
        "recommendation_rationale": "Strong backend skills but missing infra experience.",
    }
    with report_agent.override(model=TestModel(custom_output_args=custom)):
        result = await report_agent.run(_PROMPT)

    assert isinstance(result.output, ReportNarrative)


async def test_report_agent_output_has_required_fields(subtests: SubTests) -> None:
    from sira.workflows.agents import report_agent  # noqa: PLC0415 — avoids import-time LLM init

    custom = {
        "suggestions_to_strengthen": ["Add Terraform side project"],
        "audit_summary": "Excellent quality. No hallucinations.",
        "recommendation_rationale": "Covers 90% of job keywords.",
    }
    with report_agent.override(model=TestModel(custom_output_args=custom)):
        result = await report_agent.run(_PROMPT)

    out = result.output
    with subtests.test("suggestions"):
        assert out.suggestions_to_strengthen == ["Add Terraform side project"]
    with subtests.test("audit_summary"):
        assert out.audit_summary
    with subtests.test("rationale"):
        assert out.recommendation_rationale


def test_report_prompt_no_longer_asks_model_to_score() -> None:
    """The score and verdict are computed in Python; the prompt must not re-derive them."""
    from sira.workflows.agents import report_agent  # noqa: PLC0415

    prompt = " ".join(report_agent._system_prompts)  # pydantic-ai keeps static prompts here
    assert "subtract 5 points" not in prompt
    assert "copy them VERBATIM" not in prompt
    assert "Match score" in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_report_integration.py -q`
Expected: FAIL — `isinstance(result.output, ReportNarrative)` false / `TestModel` output type mismatch.

- [ ] **Step 3: Implement** — in `sira/workflows/agents.py` add `ReportNarrative` to the output-model import and replace the `report_agent` definition with:

```python
# --- Agent 5: The Report Writer ---
# Responsibility: Write the narrative section of the self-review report.
# Receives pre-computed CVDiff, GapAnalysis (with skill evidence), AuditResult,
# ReviewResult, JobAnalysis, and the Python-computed match score + verdict.
# Produces only narrative fields; every number in the report is computed by
# the workflow (compute_gap_analysis / compute_match_score), never by the LLM.
report_agent = Agent(
    _DEFAULT_MODEL,
    name="sira.report",
    model_settings=MODEL_SETTINGS,
    system_prompt="""
    You are a Career Advisor writing a clear, honest self-review report.

    You receive pre-computed structured data about:
    - Match score (0-100) and verdict ("Strong Match", "Partial Match", "Weak Match"),
      already computed from coverage percentages. Do not recompute or contradict them.
    - What changed between the original and tailored CV (CVDiff JSON)
    - Skill and keyword gaps vs. job requirements (GapAnalysis JSON): which hard and
      soft skills are covered (with a CV quote as evidence) or missing, and the
      ATS keyword coverage
    - Audit quality scores: hallucination and AI-cliché (AuditResult JSON)
    - Quality review scores (ReviewResult JSON)
    - Job requirements (JobAnalysis JSON)

    Your job is to produce ONLY the following narrative fields:
    1. suggestions_to_strengthen: 2-4 concrete, actionable items the candidate can do
       to close the missing skills (certifications, side projects, courses, etc.)
    2. audit_summary: one paragraph in plain English summarising the hallucination
       score and AI-cliché score from the AuditResult.
    3. recommendation_rationale: one honest paragraph explaining the given verdict.
       Refer to the match score, the coverage percentages, and the missing skills.
       Be direct. Do not sugarcoat weak matches.

    CRITICAL RULES:
    - Never use AI clichés: "orchestrated", "spearheaded", "leveraged", "synergy",
      "tapestry", "dynamic", "innovative", "cutting-edge", "game-changer".
    - Do not repeat the raw JSON back. Synthesise it into human-readable text.
    - Be concise: audit_summary and recommendation_rationale should each be 2-4 sentences.
    """,
    output_type=ReportNarrative,
    retries=5,
    capabilities=[_durability()],
)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_report_integration.py -q`
Expected: 3 PASS. (Workflow tests will fail until Task 8 — expected; do not run the full suite yet.)

- [ ] **Step 5: Commit**

```bash
git add sira/workflows/agents.py tests/test_report_integration.py
git commit -m "refactor(agents): report agent writes narrative only

The score and verdict now arrive pre-computed in the prompt; the model
explains them instead of deriving them.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Wire the report phase and update workflow tests

**Files:**
- Modify: `sira/workflows/__init__.py` (imports ~lines 17–33; report phase ~lines 832–878)
- Modify: `tests/workflows/stubs.py`
- Modify: `tests/workflows/test_resume_tailor_workflow.py` (helpers at ~282–370 and the four weak-match tests at ~508–600)
- Modify: `tests/workflows/test_reporter_events.py` (`run_report` stub ~lines 58–79 and the `monkeypatch.setattr` block)

**Interfaces:**
- Consumes: `match_skills` (Task 6), `compute_match_score`, `compute_recommendation` (Task 4), `ReportNarrative` (Task 1).
- Produces: `FinalReport.match_score` / `overall_recommendation` computed in Python; report prompt starts with `Match score: N/100\nVerdict: …`.

- [ ] **Step 1: Update the shared stubs** — in `tests/workflows/stubs.py`:

(a) imports:

```python
from sira.models.agents.output import (
    AuditResult,
    JobAnalysis,
    ReportNarrative,
    ReviewResult,
    SkillMatch,
    SkillMatchResult,
)
```

(b) add `"matcher": 0` to `calls`, change the analyst stub's job so a Weak verdict is reachable, replace `run_report`, add `run_matcher`, and register it. The docstring line for `report_weak_first` becomes: ``report_weak_first`` makes the skill matcher cover nothing on its first call (score 30 → "Weak Match", triggers the interactive checkpoint) and everything afterwards ("Strong Match").

```python
    async def run_analyst(*a, **k):
        calls["analyst"] += 1
        return DummyRunResult(
            JobAnalysis(
                job_title="Platform Engineer",
                company_name="Acme",
                summary="role",
                # "Python" is in sample_cv (literal hit); the others reach the judge.
                hard_skills=["Python", "Kubernetes", "Terraform"],
                soft_skills=["Communication"],
                key_responsibilities=["Build"],
                keywords_to_target=["Python", "Kubernetes"],
            )
        )
```

```python
    async def run_matcher(*a, **k):
        calls["matcher"] += 1
        covered = not (report_weak_first and calls["matcher"] == 1)
        skills = k.get("deps") or ()
        return DummyRunResult(
            SkillMatchResult(
                matches=[
                    SkillMatch(skill=s, covered=covered, evidence="stub" if covered else "")
                    for s in skills
                ]
            )
        )

    async def run_report(*a, **k):
        calls["report"] += 1
        return DummyRunResult(
            ReportNarrative(
                suggestions_to_strengthen=[],
                audit_summary="ok",
                recommendation_rationale="ok",
            )
        )
```

and in the registration list add `("skill_matcher_agent", run_matcher),` before `("report_agent", run_report),`. Remove the now-unused `CVDiff`, `FinalReport`, `GapAnalysis` imports.

Score check for these stubs (sample_cv has skills Python/Django, summary "Experienced Python engineer."): judge covers nothing → hard 1/3 = 33.3 %, soft 0 %, keywords 1/2 = 50 % → `(60·33.3 + 0 + 20·50)/100 = 30` → Weak. Judge covers everything → hard 100, soft 100, keywords 50 → 90 → Strong.

- [ ] **Step 2: Update `tests/workflows/test_resume_tailor_workflow.py`**

(a) imports: add `ReportNarrative, SkillMatch, SkillMatchResult` and drop `CVDiff, FinalReport, GapAnalysis` if no longer referenced (check with `grep -n "CVDiff\|FinalReport\|GapAnalysis" tests/workflows/test_resume_tailor_workflow.py`).

(b) replace `_make_weak_report_narrative` and `_make_strong_report_narrative` with:

```python
def _make_report_narrative():
    return ReportNarrative(
        suggestions_to_strengthen=["Add Kubernetes"],
        audit_summary="Passed",
        recommendation_rationale="See coverage.",
    )


def _matcher_stub(covered_sequence: list[bool]):
    """agent.run stand-in for skill_matcher_agent.

    Call N answers ``covered_sequence[min(N, len-1)]`` for every requested
    skill. With the job used by ``_base_agent_mocks`` (hard: Python,
    Kubernetes; soft: Communication; keywords: Python, Kubernetes) and
    ``sample_cv`` (has Python), all-False gives score 40 → "Weak Match" and
    all-True gives 90 → "Strong Match".
    """
    calls = {"n": 0}

    async def run_matcher(*args, **kwargs):
        idx = min(calls["n"], len(covered_sequence) - 1)
        calls["n"] += 1
        covered = covered_sequence[idx]
        skills = kwargs.get("deps") or ()
        return DummyRunResult(
            SkillMatchResult(
                matches=[
                    SkillMatch(skill=s, covered=covered, evidence="stub" if covered else "")
                    for s in skills
                ]
            )
        )

    return run_matcher
```

(c) change `_base_agent_mocks` signature and the report/matcher patching:

```python
def _base_agent_mocks(
    monkeypatch, sample_cv, *, auditor_result, matcher_covered: list[bool] | None = None
):
    """Patch all agents. auditor_result can be a single AuditResult or a list for sequential calls.

    ``matcher_covered`` drives the verdict (see ``_matcher_stub``); default
    ``[True]`` → "Strong Match".
    """
```

and replace the `if report_narrative is not None:` block with:

```python
    async def run_report(*args, **kwargs):
        return DummyRunResult(_make_report_narrative())

    monkeypatch.setattr("sira.workflows.agents.report_agent.run", run_report)
    monkeypatch.setattr(
        "sira.workflows.agents.skill_matcher_agent.run",
        _matcher_stub(matcher_covered or [True]),
    )
```

(d) in `test_interactive_weak_match_quit`, `test_interactive_weak_match_continue`, and `test_interactive_weak_match_feedback_still_weak_then_continue` replace `report_narrative=_make_weak_report_narrative(),` with `matcher_covered=[False],`.

(e) in `test_interactive_weak_match_feedback_then_strong` delete `report_call` and `run_report_alternating` and the `monkeypatch.setattr("sira.workflows.agents.report_agent.run", run_report_alternating)` call; pass `matcher_covered=[False, True]` to `_base_agent_mocks`.

(f) add one new assertion test after `test_interactive_weak_match_continue`:

```python
@pytest.mark.anyio
async def test_final_report_score_and_verdict_are_computed_in_python(monkeypatch, sample_cv, subtests):
    """The report agent no longer decides the score; the workflow does."""
    _patch_stdin(monkeypatch, is_tty=False)
    _base_agent_mocks(monkeypatch, sample_cv, auditor_result=_make_passing_audit(), matcher_covered=[True])

    result = await ResumeTailorWorkflow(write_attempts=1).run("# resume", job_content="job")
    report = result.final_report
    assert report is not None
    with subtests.test("score"):
        assert report.match_score == 90  # hard 100, soft 100, keywords 50
    with subtests.test("verdict"):
        assert report.overall_recommendation == "Strong Match"
    with subtests.test("evidence_in_gaps"):
        assert report.gaps.skill_evidence["Kubernetes"] == "stub"
        assert report.gaps.skill_evidence["Python"] == ""  # literal hit
```

- [ ] **Step 3: Update `tests/workflows/test_reporter_events.py`** — replace `run_report` with a `ReportNarrative` stub and add a matcher stub:

```python
    async def run_matcher(*a, **k):
        from sira.models.agents.output import SkillMatch, SkillMatchResult

        return DummyRunResult(
            SkillMatchResult(
                matches=[SkillMatch(skill=s, covered=True, evidence="ok") for s in (k.get("deps") or ())]
            )
        )

    async def run_report(*a, **k):
        from sira.models.agents.output import ReportNarrative

        return DummyRunResult(
            ReportNarrative(
                suggestions_to_strengthen=[],
                audit_summary="ok",
                recommendation_rationale="ok",
            )
        )
```

and register `monkeypatch.setattr("sira.workflows.agents.skill_matcher_agent.run", run_matcher)` next to the report one.

- [ ] **Step 4: Run the workflow tests to verify they fail for the right reason**

Run: `uv run pytest tests/workflows -q`
Expected: FAIL. The workflow still reads `narrative.overall_recommendation`, which raises `AttributeError` inside the report phase's `except Exception` — so `final_report` is `None` and the log shows "Report generation failed". Visible failures: `test_final_report_score_and_verdict_are_computed_in_python` (`report is not None`), the three weak-match tests (no verdict → no checkpoint → no `UserAbortedError` / no second writer prompt), and `test_checkpoint_answer_is_not_asked_again_after_a_later_crash` in `test_durable_workflow.py`.

- [ ] **Step 5: Wire the workflow** — in `sira/workflows/__init__.py`:

(a) imports:

```python
from sira.utils.cv_diff import (
    compute_cv_diff,
    compute_gap_analysis,
    compute_match_score,
    compute_recommendation,
)
from sira.workflows.skill_matching import match_skills
```

(b) replace the block from `gap_analysis = compute_gap_analysis(original_cv, new_cv, job_analysis)` through the `final_report = FinalReport(...)` assignment with:

```python
                skill_matches = await match_skills(
                    original_cv,
                    job_analysis,
                    usage=total_usage,
                    usage_limits=USAGE_LIMITS,
                )
                gap_analysis = compute_gap_analysis(
                    original_cv, new_cv, job_analysis, skill_matches=skill_matches
                )
                match_score = compute_match_score(gap_analysis)
                recommendation = compute_recommendation(match_score, gap_analysis)

                review_json = review.model_dump_json() if review is not None else "N/A"
                audit_json = audit.model_dump_json() if audit is not None else "N/A"

                report_prompt = f"""
Match score: {match_score}/100
Verdict: {recommendation}
CV Diff: {cv_diff.model_dump_json()}
Gap Analysis: {gap_analysis.model_dump_json()}
Audit Result: {audit_json}
Review Result: {review_json}
Job Analysis: {job_data_json}
"""

                report_result = await run_agent(
                    report_agent,
                    report_prompt,
                    verbose=verbose,
                    agent_label="Report",
                    usage=total_usage,
                    usage_limits=USAGE_LIMITS,
                )
                narrative = report_result.output

                final_report = FinalReport(
                    job_title=job_analysis.job_title,
                    company_name=job_analysis.company_name,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    overall_recommendation=recommendation,
                    match_score=match_score,
                    what_changed=cv_diff,
                    gaps=gap_analysis,
                    suggestions_to_strengthen=narrative.suggestions_to_strengthen,
                    audit_summary=narrative.audit_summary,
                    recommendation_rationale=narrative.recommendation_rationale,
                    passed=audit_passed,
                )
```

Keep the `self._reporter.log("\n🤖 Agent 5 (Report Writer): …")` line where it is; `match_skills` logs its own line.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS, including `tests/workflows/test_durable_workflow.py::test_checkpoint_answer_is_not_asked_again_after_a_later_crash` (first matcher call → Weak → checkpoint asked once; forked run → matcher call ≥ 2 → Strong → not asked again).

- [ ] **Step 7: Commit**

```bash
git add sira/workflows/__init__.py tests/workflows/stubs.py tests/workflows/test_resume_tailor_workflow.py tests/workflows/test_reporter_events.py
git commit -m "feat(workflows): judge skills semantically and score in python

The report phase runs the skill matcher before gap analysis, computes the
score and verdict with compute_match_score / compute_recommendation, and
hands them to the report agent as facts to explain.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Regression test for the issue's symptom

**Files:**
- Create: `tests/test_semantic_match_regression.py`

**Interfaces:**
- Consumes: `match_skills`, `compute_gap_analysis`, `compute_match_score`, `compute_recommendation`, `skill_matcher_agent`.

- [ ] **Step 1: Write the test** — create `tests/test_semantic_match_regression.py`:

```python
"""Regression for GitHub issue #2: the match score collapsed to 0/100.

A CV that plainly contains "context engineering", "mentor to ~30 engineers",
cross-functional platform work, production RAG and monitoring was reported as
missing all of them, because gap analysis compared the job's long descriptive
skill phrases against the CV's ``skills`` list by exact text.

Caveat: the judge here is scripted. This proves the pipeline (literal pre-pass
+ judge + Python formula) turns concept matches into a sane score; it does not
prove a real model's judgement.
"""

import json
import re

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from sira.models.agents.output import CV, JobAnalysis, WorkExperience
from sira.utils.cv_diff import (
    compute_gap_analysis,
    compute_match_score,
    compute_recommendation,
)

pytestmark = pytest.mark.anyio

_SKILL_LINE = re.compile(r"^\d+\. (.+)$", re.MULTILINE)

HARD_SKILLS = [
    "Large language models (LLMs) — open-weight models",
    "Fine-tuning and distillation of LLMs",
    "Context engineering, context management and state handling for multi-turn dialogue",
    "Retrieval-augmented generation (RAG) in production",
    "Monitoring and evaluation of LLM systems in production",
    "Python",
    "Reinforcement learning from human feedback (RLHF)",
    "Speech recognition and voice interfaces",
]
SOFT_SKILLS = [
    "Technical leadership and mentorship",
    "Cross-functional collaboration (product, design, analytics)",
    "Communicating trade-offs to non-technical stakeholders",
    "Ownership of ambiguous problems",
]

# What a competent judge should say about this CV. Skills not listed -> not covered.
JUDGE_TABLE = {
    HARD_SKILLS[0]: "Fine-tuned and distilled open-weight Llama models",
    HARD_SKILLS[1]: "Fine-tuned and distilled open-weight Llama models",
    HARD_SKILLS[2]: "Designed context engineering for a multi-turn shopping assistant",
    HARD_SKILLS[3]: "Shipped production RAG over 2M product documents",
    HARD_SKILLS[4]: "Built monitoring and evaluation dashboards for hallucination rate",
    SOFT_SKILLS[0]: "Mentor to ~30 engineers across three teams",
    SOFT_SKILLS[1]: "Cross-functional platform work with product, design and analytics",
}


def _cv() -> CV:
    return CV(
        full_name="Sam Staff",
        contact_info="sam@example.com",
        summary=(
            "Staff engineer building production LLM systems: RAG pipelines, "
            "context engineering and evaluation for a grocery app."
        ),
        skills=["Python", "PyTorch", "LangGraph", "Kubernetes", "Observability"],
        experience=[
            WorkExperience(
                company="Picnic-like Co",
                role="Staff AI Engineer",
                dates="2022-2026",
                highlights=[
                    "Designed context engineering for a multi-turn shopping assistant: "
                    "conversation state, memory and tool results managed per turn.",
                    "Shipped production RAG over 2M product documents with hybrid retrieval.",
                    "Fine-tuned and distilled open-weight Llama models for intent classification.",
                    "Mentor to ~30 engineers across three teams; ran the internal LLM guild.",
                    "Cross-functional platform work with product, design and analytics "
                    "to define assistant metrics.",
                    "Built monitoring and evaluation dashboards for hallucination rate and latency.",
                ],
            )
        ],
        education=["MSc Computer Science"],
    )


def _job() -> JobAnalysis:
    return JobAnalysis(
        job_title="Staff AI Engineer – Agentic Shopping",
        company_name="Picnic-like Co",
        summary="Own the shopping assistant.",
        hard_skills=HARD_SKILLS,
        soft_skills=SOFT_SKILLS,
        key_responsibilities=["Build the assistant"],
        keywords_to_target=["LLM", "RAG", "Python", "Kubernetes", "RLHF", "voice"],
    )


def _scripted_judge() -> FunctionModel:
    """A fake judge that answers from JUDGE_TABLE.

    The agent carries DBOSDurability with an event-stream handler, so pydantic-ai
    always uses the streaming path; a FunctionModel needs ``stream_function``.
    """

    def answer(messages) -> dict:
        # messages[0].parts holds the system prompt AND the user prompt; only the
        # user prompt carries the numbered skill list.
        prompt = next(
            p.content
            for p in messages[0].parts
            if isinstance(getattr(p, "content", None), str) and "Skills to judge:" in p.content
        )
        asked = _SKILL_LINE.findall(prompt.split("Skills to judge:", 1)[1])
        return {
            "matches": [
                {"skill": s, "covered": s in JUDGE_TABLE, "evidence": JUDGE_TABLE.get(s, "")}
                for s in asked
            ]
        }

    def fn(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=answer(messages))]
        )

    async def sfn(messages, info: AgentInfo):
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args=json.dumps(answer(messages)))}

    return FunctionModel(fn, stream_function=sfn)


def test_literal_matching_reproduces_the_collapse(subtests):
    """Without semantic matches the old behaviour scores this pair as Weak."""
    gap = compute_gap_analysis(_cv(), _cv(), _job())  # skill_matches=None → literal
    score = compute_match_score(gap)
    with subtests.test("hard_coverage_tiny"):
        assert gap.hard_skill_coverage_percent == 12.5  # only "Python"
    with subtests.test("score_low"):
        assert score < 30
    with subtests.test("verdict_weak"):
        assert compute_recommendation(score, gap) == "Weak Match"


async def test_semantic_matching_scores_the_pair_sanely(subtests):
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    with skill_matcher_agent.override(model=_scripted_judge()):
        matches = await match_skills(_cv(), _job())
    gap = compute_gap_analysis(_cv(), _cv(), _job(), skill_matches=matches)
    score = compute_match_score(gap)

    with subtests.test("context_engineering_covered"):
        assert HARD_SKILLS[2] in gap.covered_hard_skills
    with subtests.test("mentorship_covered"):
        assert SOFT_SKILLS[0] in gap.covered_soft_skills
    with subtests.test("truly_missing_stay_missing"):
        assert gap.missing_hard_skills == [HARD_SKILLS[6], HARD_SKILLS[7]]
    with subtests.test("hard_coverage_at_least_50"):
        assert gap.hard_skill_coverage_percent >= 50.0
    with subtests.test("score_well_above_zero"):
        assert score >= 50
    with subtests.test("verdict_not_weak"):
        assert compute_recommendation(score, gap) != "Weak Match"
    with subtests.test("exact_numbers_for_the_record"):
        # hard 6/8 = 75, soft 2/4 = 50, keywords 4/6 = 66.7 → 45 + 10 + 13.34 → 68
        assert (gap.hard_skill_coverage_percent, gap.soft_skill_coverage_percent) == (75.0, 50.0)
        assert score == 68
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_semantic_match_regression.py -q`
Expected: 2 PASS. If `keyword_coverage_percent` differs from 66.7, check the keyword list against `_cv().model_dump_json().lower()` ("llm" appears in "LLM systems", "voice" and "rlhf" must be absent).

- [ ] **Step 3: Commit**

```bash
git add tests/test_semantic_match_regression.py
git commit -m "test: pin the issue #2 score collapse and its fix

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Show covered skills in the terminal and Markdown report

**Files:**
- Modify: `sira/main.py` (`_print_report_to_console`, after the `SKILL GAPS` block ~line 205)
- Modify: `sira/utils/markdown_writer.py` (`generate_report_markdown`, score section ~line 133 and before `## 🚧 Skill Gaps` ~line 188)
- Create: `tests/test_markdown_writer.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_markdown_writer.py`:

```python
"""generate_report_markdown renders the semantic skill coverage."""

from sira.models.agents.output import CVDiff, FinalReport, GapAnalysis
from sira.utils.markdown_writer import generate_report_markdown


def _report(gap: GapAnalysis) -> FinalReport:
    return FinalReport(
        job_title="Engineer",
        company_name="Acme",
        generated_at="2026-01-01T00:00:00Z",
        overall_recommendation="Partial Match",
        match_score=65,
        what_changed=CVDiff(),
        gaps=gap,
        suggestions_to_strengthen=[],
        audit_summary="ok",
        recommendation_rationale="ok",
        passed=True,
    )


def test_markdown_lists_covered_skills_with_evidence(subtests):
    gap = GapAnalysis(
        covered_hard_skills=["Python", "Kubernetes"],
        missing_hard_skills=["Rust"],
        covered_soft_skills=["Mentorship"],
        skill_evidence={"Python": "", "Kubernetes": "ran K8s", "Mentorship": "mentored 30"},
        hard_skill_coverage_percent=66.7,
        soft_skill_coverage_percent=100.0,
        covered_keywords=["Python"],
        missing_keywords=["Rust"],
        keyword_coverage_percent=50.0,
    )
    md = generate_report_markdown(_report(gap))
    with subtests.test("section_present"):
        assert "## ✅ Skills Covered" in md
    with subtests.test("counts"):
        assert "Hard skills: 2/3 (66.7%)" in md
        assert "Soft skills: 1/1 (100.0%)" in md
    with subtests.test("literal_hit_without_quote"):
        assert "- **Python**\n" in md
    with subtests.test("semantic_hit_with_quote"):
        assert '- **Kubernetes** — "ran K8s"' in md
    with subtests.test("formula_note"):
        assert "0.6·hard + 0.2·soft + 0.2·keywords" in md
    with subtests.test("covered_section_precedes_gaps"):
        assert md.index("## ✅ Skills Covered") < md.index("## 🚧 Skill Gaps")


def test_markdown_covered_section_handles_nothing_covered():
    md = generate_report_markdown(_report(GapAnalysis(missing_hard_skills=["Rust"])))
    assert "_No required skills found in your CV._" in md
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_markdown_writer.py -q`
Expected: FAIL — `"## ✅ Skills Covered" not in md`.

- [ ] **Step 3: Implement the Markdown side** — in `sira/utils/markdown_writer.py`:

(a) after `lines.append(f"**Verdict:** {report.overall_recommendation}\n")` insert:

```python
    lines.append(
        "_Score = 0.6·hard + 0.2·soft + 0.2·keywords coverage; "
        "empty buckets are rescaled (see ARCHITECTURE.md)._\n"
    )
```

(b) before the `# Skill gaps` comment insert:

```python
    # Skills covered (literal pre-pass + semantic judge, with evidence)
    lines.append("---\n")
    lines.append("## ✅ Skills Covered\n")
    hard_total = len(gap.covered_hard_skills) + len(gap.missing_hard_skills)
    soft_total = len(gap.covered_soft_skills) + len(gap.missing_soft_skills)
    lines.append(
        f"**Hard skills: {len(gap.covered_hard_skills)}/{hard_total} "
        f"({gap.hard_skill_coverage_percent:.1f}%) · "
        f"Soft skills: {len(gap.covered_soft_skills)}/{soft_total} "
        f"({gap.soft_skill_coverage_percent:.1f}%)**\n"
    )
    covered_skills = [*gap.covered_hard_skills, *gap.covered_soft_skills]
    if not covered_skills:
        lines.append("_No required skills found in your CV._\n")
    for skill in covered_skills:
        quote = gap.skill_evidence.get(skill, "")
        lines.append(f'- **{skill}** — "{quote}"\n' if quote else f"- **{skill}**\n")
```

- [ ] **Step 4: Implement the terminal side** — in `sira/main.py`, after the `SKILL GAPS` block (after the `Soft:` prints) and before `SUGGESTIONS TO STRENGTHEN`, insert:

```python
    hard_total = len(gap.covered_hard_skills) + len(gap.missing_hard_skills)
    soft_total = len(gap.covered_soft_skills) + len(gap.missing_soft_skills)
    console.print(
        f"\nSKILLS COVERED — Hard: {len(gap.covered_hard_skills)}/{hard_total} "
        f"({gap.hard_skill_coverage_percent:.1f}%) · "
        f"Soft: {len(gap.covered_soft_skills)}/{soft_total} "
        f"({gap.soft_skill_coverage_percent:.1f}%)"
    )
    for skill in (*gap.covered_hard_skills, *gap.covered_soft_skills):
        quote = gap.skill_evidence.get(skill, "")
        console.print(f'  ✅ {skill} — "{quote}"' if quote else f"  ✅ {skill}")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_markdown_writer.py tests/test_cli_typer.py tests/test_main.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add sira/main.py sira/utils/markdown_writer.py tests/test_markdown_writer.py
git commit -m "feat(report): show covered skills with cv evidence

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Documentation

**Files:**
- Modify: `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `docs/agents.md`, `docs/output.md`, `docs/extending.md`, `docs/project-layout.md`

- [ ] **Step 1: `ARCHITECTURE.md`**

(a) Replace the `### 6. Report Generator (report_agent)` block with:

```markdown
### 6. Skill Matcher (`skill_matcher_agent`)

- **Responsibility**: Judge, per job skill, whether the **original** CV shows the same concept even in different words ("mentor to ~30 engineers" covers "Technical leadership and mentorship"). Runs inside the report phase after a pure-Python literal pre-pass, so it only sees skills that did not appear verbatim in the CV.
- **Input**: The whole CV rendered as plain text (`utils/skill_matching.py::render_cv_text`) plus a numbered skill list; the expected skill list also travels as `deps` so the validator can check the answer.
- **Output**: `SkillMatchResult` — one `SkillMatch(skill, covered, evidence)` per skill; `evidence` is a CV quote (≤ 200 chars) or empty.
- **Validator**: `_validate_skill_matches` — `ModelRetry` unless exactly the requested skills come back; canonicalises names; blanks evidence for uncovered skills.
- **Retries**: 3 · **Tier**: fast · **Quality Gate**: No
- **Fallback**: on `AgentRunError` the undecided skills stay "missing" (pre-semantic behaviour) and a warning is logged. The run never fails because of the matcher.

### 7. Report Generator (`report_agent`)

- **Responsibility**: Write the narrative section of the self-review report. Receives the computed match score and verdict plus CVDiff, GapAnalysis (with evidence), AuditResult, ReviewResult, and JobAnalysis as structured JSON.
- **Output**: `ReportNarrative` (suggestions_to_strengthen, audit_summary, recommendation_rationale)
- **Key Design**: every number in the report is computed in Python: `compute_gap_analysis`, `compute_match_score`, `compute_recommendation` in `cv_diff.py`. The model explains them; it cannot change them.
- **Retries**: 5
- **Quality Gate**: No
```

(b) In the data-flow tree, replace the `GENERATING_REPORT` subtree with:

```
   └── GENERATING_REPORT:
       ├── compute_cv_diff(original, tailored) → CVDiff (pure Python, no LLM)
       ├── match_skills(original, job): literal pre-pass → skill_matcher_agent (one call) → {skill: SkillMatch}
       ├── compute_gap_analysis(original, tailored, job, skill_matches) → GapAnalysis (pure Python)
       ├── compute_match_score(gap) → 0–100; compute_recommendation(score, gap) → verdict (pure Python)
       └── report_agent: score + verdict + diff + gaps + audit + review → ReportNarrative
           └── Workflow assembles FinalReport from the computed numbers + narrative
```

(c) Replace the bullet `- **CVDiff and GapAnalysis are computed in pure Python** …` with:

```markdown
- **Skill coverage uses one judge call, then everything is pure Python.** `skill_matcher_agent` decides which job skills the CV covers (with a CV quote as evidence); `compute_gap_analysis`, `compute_match_score` and `compute_recommendation` in `cv_diff.py` turn those verdicts into deterministic metrics. Given the judge's answer, every number is reproducible. ATS keyword coverage stays a literal substring check on the tailored CV — that is what an applicant tracking system does.
- **Match score formula** (`compute_match_score`): `score = round(60·hard% + 20·soft% + 20·keyword%) / 100`. A bucket the job lists nothing for is dropped and the remaining weights are rescaled to sum to 100; all buckets empty → 0. `round` is Python's built-in (half to even). **Verdict** (`compute_recommendation`): *Strong Match* when score ≥ 75 and hard-skill coverage ≥ 75 % (or the job lists no hard skills); *Partial Match* when score ≥ 50; otherwise *Weak Match*.
- **The report_agent only produces narrative fields** (suggestions, audit_summary, rationale). The workflow assembles the `FinalReport` from the computed score, verdict, `CVDiff` and `GapAnalysis` plus that narrative.
```

(d) In the `### Ungated Agents` list change the `report_agent` line to `- \`report_agent\` — Produces narrative; score, verdict and gaps are computed in Python.` and add `- \`skill_matcher_agent\` — Shape-validated by \`_validate_skill_matches\`; a wrong answer degrades to literal matching.`

(e) In `## Key Design Decisions` replace item 2 with: `2. **Python-Computed Metrics**: \`GapAnalysis\`, \`match_score\` and \`overall_recommendation\` are computed in \`cv_diff.py\` from per-skill verdicts. The only model involvement is the skill matcher's yes/no-with-evidence per skill; the report agent only writes prose.`

(f) In the models table row for `GapAnalysis`, extend the field list with `covered_hard_skills`, `covered_soft_skills`, `skill_evidence`, `hard_skill_coverage_percent`, `soft_skill_coverage_percent`; add rows for `SkillMatch` / `SkillMatchResult` (skill matcher output) and `ReportNarrative` (report agent output). In the layout tree (`cv_diff.py` comment) write `# Pure Python CVDiff + GapAnalysis + match score` and add `skill_matching.py  # render_cv_text, literal pre-pass` under utils and `skill_matching.py  # match_skills orchestration` under workflows.

- [ ] **Step 2: `AGENTS.md`** — in the agent table change `report_agent` output to `ReportNarrative` and add the row `| \`skill_matcher_agent\` | \`SkillMatchResult\` | 3 | No (shape validator; falls back to literal matching) |`. Change the `cv_diff.py` bullet (~line 84) to `— Computes structural diffs, gap analysis, the match score and the verdict (pure Python over the skill matcher's verdicts).` Add `skill_matching.py` bullets for `utils/` and `workflows/` next to it. Add `test_skill_matcher.py`, `test_skill_matching_utils.py`, `test_semantic_match_regression.py` to the test-module list (~line 152).

- [ ] **Step 3: `.github/copilot-instructions.md`** — line 54: add `SkillMatch`, `SkillMatchResult`, `ReportNarrative` to the key output models. Line 56: replace with `- Skill coverage is decided by \`skill_matcher_agent\` (one judge call with CV evidence); \`GapAnalysis\`, \`match_score\` and the verdict are then computed in **pure Python** by \`utils/cv_diff.py\` — the report agent only writes prose`. Line 85: `utils/cv_diff.py (diff/gap/score), utils/skill_matching.py (CV text + literal pre-pass)`. Line 92: add `tests/test_skill_matcher.py`, `tests/test_semantic_match_regression.py`.

- [ ] **Step 4: `CLAUDE.md`** — replace the sentence in item 2 of "Big-picture architecture": `\`CVDiff\`/\`GapAnalysis\` for the report are computed in **pure Python** (\`utils/cv_diff.py\`), not by an LLM.` with `\`CVDiff\`, \`GapAnalysis\`, \`match_score\` and the verdict are computed in **pure Python** (\`utils/cv_diff.py\`) from per-skill verdicts; the only model call in that phase besides the report narrative is \`skill_matcher_agent\` (\`workflows/skill_matching.py\`), which says whether the CV covers each job skill and quotes the evidence.`

- [ ] **Step 5: `README.md`** — line 43: `**Stage 6 — Report Generator**: Judges which job skills your CV covers by meaning (one skill-matcher call over the whole CV text), computes the match score and verdict in Python, and compiles the self-review report.` In `## 📊 Self-Review Report` replace the last two bullets with:

```markdown
- **Skills Covered**: Hard/soft skills the job asks for that your CV shows — matched by meaning, not only by exact words ("mentor to ~30 engineers" covers "Technical leadership and mentorship"), each with the CV line as evidence
- **Match Score**: 0–100 = `0.6 × hard-skill coverage + 0.2 × soft-skill coverage + 0.2 × ATS keyword coverage` (buckets the job does not list are rescaled away). Computed in Python, not by the model.
- **Overall Recommendation**: "Strong Match" (score ≥ 75 and hard coverage ≥ 75 %), "Partial Match" (score ≥ 50), or "Weak Match"
```

Line 358 comment: `# Pure-Python CV diff, gap analysis, match score`.

- [ ] **Step 6: MkDocs pages**

`docs/agents.md`: table — change row 6 to `| 6 | \`skill_matcher_agent\` | \`SkillMatchResult\` | 3 | no | stage 6, before the report |` and add `| 7 | \`report_agent\` | \`ReportNarrative\` | 5 | no | stage 6 |`. In the mermaid block replace the `G` node lines with:

```
    CV0 --> M["match_skills()<br/>literal pre-pass + skill_matcher_agent"]
    JA --> M --> G["compute_gap_analysis()<br/>compute_match_score()<br/>pure Python"]
    G --> GA["GapAnalysis + score + verdict"]
```

and the paragraph under it with: `Everything ending in \`_agent\` is a model call. The skill matcher answers one yes/no per job skill and quotes the CV line; \`compute_gap_analysis()\`, \`compute_match_score()\` and \`compute_recommendation()\` are deterministic Python over those answers — no model decides the score or the verdict.`

`docs/output.md`: mermaid — replace the `GAP` node with `JOB --> MATCH["match_skills()<br/>skill judge + evidence"] --> GAP["compute_gap_analysis()<br/>compute_match_score()<br/>pure Python"]` and keep `ORIG --> MATCH`. Replace the paragraph "The two `pure Python` boxes matter…" with: `The \`pure Python\` box matters: **the diff, the gap analysis, the score and the verdict are computed in plain code**. A model is asked one thing — whether your CV shows each job skill, with a quote as evidence — and cannot flatter you beyond that, because it never touches the numbers.` In the sections table add, before **Skill Gaps**: `| **Skills Covered** | Hard and soft skills the job asks for that your resume shows — by meaning, not only exact words — each with the resume line that proves it, and the coverage percentages that feed the score. |` and change the **Match Score & Recommendation** row to end with `…with the reasoning. Score = 0.6·hard + 0.2·soft + 0.2·keywords coverage.`

`docs/extending.md`: replace the first paragraph of `## Change the report` with: `\`CVDiff\`, \`GapAnalysis\`, the match score and the verdict are computed in \`sira/utils/cv_diff.py\` in pure Python. Keep them that way. The only model input is \`skill_matcher_agent\` (\`sira/workflows/skill_matching.py\`), which answers "does the CV show this skill?" with a quote; change its prompt if matches are too generous or too strict, and change the weights in \`compute_match_score\` (documented in ARCHITECTURE.md) if the score feels off. The report model writes the prose around those numbers; it must not be the one deciding them, or the report stops being trustworthy.`

`docs/project-layout.md`: line 31 → `cv_diff.py  # CVDiff + GapAnalysis + match score, pure Python`, add `skill_matching.py  # CV text rendering + literal skill pre-pass` under utils and `skill_matching.py  # match_skills: pre-pass → skill judge → fallback` under workflows; item 3 (~line 89) → `**Gap analysis, match score and verdict are computed in pure Python** in \`utils/cv_diff.py\` from the skill matcher's per-skill verdicts. The report agent writes prose around numbers it did not choose.`

- [ ] **Step 7: Build the docs strictly**

Run: `uv sync --group docs && uv run mkdocs build --strict`
Expected: exit 0, no warnings.

- [ ] **Step 8: Commit**

```bash
git add ARCHITECTURE.md AGENTS.md README.md CLAUDE.md .github/copilot-instructions.md docs/agents.md docs/output.md docs/extending.md docs/project-layout.md
git commit -m "docs: describe semantic skill matching and the score formula

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Final gate

- [ ] **Step 1: Lint, format, test**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest -q`
Expected: every step exit 0. Fix anything ruff reports (unused imports in tests/stubs are the likely candidates) and re-run.

- [ ] **Step 2: Docs build**

Run: `uv run mkdocs build --strict`
Expected: exit 0.

- [ ] **Step 3: Knowledge graph**

Run: `graphify update .` (AST-only, no API cost) — only if the `graphify` CLI is installed; skip otherwise.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A && git commit -m "style: apply ruff fixes

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

(only if Step 1 changed files.)

- [ ] **Step 5: Push and open the PR** (only after the user asks)

```bash
git push -u origin claude/github-issue-2-d2b846
gh pr create --assignee @EmadMokhtar --title "feat: semantic job skill matching and python-computed match score" --body "$(cat <<'EOF'
Closes #2.

## What
- New `skill_matcher_agent` (fast tier, DBOS-checkpointed) judges, per job skill, whether the **original CV** shows the same concept and quotes the CV line as evidence. A pure-Python literal pre-pass keeps verbatim hits away from the model; a failing judge degrades to literal matching instead of failing the run.
- `compute_gap_analysis` consumes those verdicts and reports covered skills, evidence and coverage percentages. ATS keyword coverage stays literal.
- `match_score` and `overall_recommendation` are now computed in Python (`compute_match_score`, `compute_recommendation`): `0.6·hard + 0.2·soft + 0.2·keywords`, empty buckets rescaled; Strong ≥ 75 (and hard ≥ 75 %), Partial ≥ 50, else Weak. Formula documented in ARCHITECTURE.md.
- The report agent now writes narrative only (`ReportNarrative`).
- Terminal and Markdown reports list covered skills with evidence.

## Why
On a real run the score collapsed to 0/100 because gap analysis compared long descriptive skill phrases against the skills list by exact text, and the model then subtracted 5 points per "missing" skill. See the regression test `tests/test_semantic_match_regression.py`.

## Tests
All offline (`FunctionModel` / stubs). `uv run ruff check . && uv run ruff format --check . && uv run pytest` and `uv run mkdocs build --strict` pass.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
