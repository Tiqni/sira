# ARCHITECTURE.md — Sira

## Project Overview

Sira is a multi-agent AI system that analyzes job postings and tailors resumes to match specific job requirements. It ensures authenticity, avoids AI clichés, and optimizes for Applicant Tracking Systems (ATS). The system uses a sequential pipeline of specialized agents orchestrated by `pydantic-ai`, with built-in quality gates, an inner refinement loop, and SQLite-backed memory for caching and persistence.

---

## Architecture at a Glance

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  RESUME  │    │   JOB    │    │    CV    │    │ REVIEWER │    │ AUDITOR  │    │  REPORT  │
│  PARSER  │───▶│ ANALYST  │───▶│  WRITER  │───▶│  (x3)    │───▶│          │───▶│GENERATOR │
│          │    │          │    │          │    │          │    │          │    │          │
│ CV JSON  │    │ Job JSON │    │ Tailored │    │  Review  │    │  Audit   │    │  Final   │
│          │    │          │    │ CV JSON  │    │  Scores  │    │  Result  │    │  Report  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     ▲                ▲               │                                  │
     │                │               │          ◀── RETRY LOOP ──       │
     │                │               ▼          (up to 3 attempts)      │
     │                │         ┌──────────┐                            │
     │                │         │  QUALITY  │◀─────── score < threshold  │
     │                │         │   GATE    │         triggers retry     │
     │                │         └──────────┘                            │
     │                │                                                  │
  Content-         Raw Job                                               │
  Hash Cache       Posting                                               │
  (SQLite)         Markdown                                              │
```

The system runs a **6-stage sequential pipeline** in `ResumeTailorWorkflow`:

1. **PARSING_RESUME** — Parse resume → structured `CV`
2. **ANALYZING_JOB** — Extract structured `JobAnalysis`
3. **WRITING_CV** — Tailor CV to match job
4. **REVIEWING_CV** — Score quality, suggest improvements (refinement loop)
5. **AUDITING_CV** — Validate for hallucinations and AI clichés
6. **GENERATING_REPORT** — Compile self-review report

Stages 3-5 form the **Write → Review → Audit inner loop**: after the initial write, the reviewer assesses quality and the writer refines (up to 3 review iterations). The auditor then checks the final draft. If the audit fails, the entire Write → Review → Audit loop retries (up to 3 write attempts). The Report phase **always runs**, even on audit failure.

---

## Agent Pipeline (Execution Order)

### 0. Job Scraper (`job_scraper_agent`)

**Not part of the internal pipeline** — called by the CLI before launching the workflow.

- **Responsibility**: Strip site chrome (navigation, cookie banners, footers) from Markdown that was already extracted deterministically.
- **Input / Output**: `str` → `str`. The fetch itself is not an agent step: `sira/tools/job_scraper.py::fetch_job_markdown()` drives Playwright (headless Chromium), converts HTML to Markdown, runs the quality gate (`assert_quality`), and returns a `RawScrape(markdown_raw, source_text, extraction_strategy, injection_indicators)`.
- **Tools**: none (the former `fetch_webpage` / `validate_extraction` tools were replaced by the deterministic fetch).
- **Prompt-injection scan**: `fetch_job_markdown()` also calls `detect_prompt_injection(raw_html, markdown)` (regex only, 10 languages) and stores category names in `RawScrape.injection_indicators`; `main.py` logs and prints an advisory warning and continues. An optional local classifier (`sira[guard]` extra, `sira/tools/injection_guard.py`, Meta Llama Prompt Guard 2) can add a `classifier_flagged` indicator after a one-time user consent. Both the scraper and analyst prompts state that page text is data, never instructions.
- **Retries**: 3
- **Quality Gate**: No (the deterministic `assert_quality` runs before the agent)

### 1. Resume Parser (`resume_parser_agent`)

- **Responsibility**: Parse Markdown resume text into a structured `CV` object. Extract ALL skills from every section (summary, experience, projects, certifications, education, publications).
- **Output**: `CV` (full_name, contact_info, summary, skills, projects, experience, education, certifications, publications)
- **Key Rules**: Preserve ALL hyperlinks in `[text](url)` format. Never add or modify information. For senior resumes, expect 40+ skills.
- **Retries**: 5
- **Quality Gate**: Yes — validated by `_validate_resume_parser`

### 2. Job Analyst (`analyst_agent`)

- **Responsibility**: Analyze raw job posting text and extract structured job requirements. Identifies core requirements (not "nice-to-haves") and hidden ATS keywords.
- **Output**: `JobAnalysis` (job_title, company_name, summary, hard_skills, soft_skills, key_responsibilities, keywords_to_target)
- **Retries**: 5
- **Quality Gate**: Yes — validated by `_validate_analyst`

### 3. CV Writer (`writer_agent`)

- **Responsibility**: Rewrite the CV to target the Job Analysis using ONLY content from the original CV. Rephrase and reorganize but never invent skills or experiences. Groups relevant skills at the top.
- **Output**: `CV`
- **Key Rules**: Only use skills/experiences from original CV. Rephrase existing content to align with job keywords. Avoid AI clichés. Preserve ALL hyperlinks.
- **Retries**: 5
- **Quality Gate**: Yes — validated by `_validate_writer`
- **Also used**: For the refinement loop (review-based improvements)

### 4. Reviewer (`reviewer_agent`)

- **Responsibility**: Review the tailored CV against job requirements. Score quality and provide specific improvement suggestions.
- **Output**: `ReviewResult` (quality_score 0-10, needs_improvement bool, specific_suggestions, strengths)
- **Review Criteria**: Keyword optimization, impact & achievements, relevance, clarity & readability, ATS compatibility
- **Retries**: 5
- **Quality Gate**: No — reviewer output is used to drive refinement, not gated itself

### 5. Auditor (`auditor_agent`)

- **Responsibility**: Compare original vs. tailored CV. Validate: no hallucinations (no new skills/companies/roles), no AI clichés, all hyperlinks preserved, proper job targeting.
- **Output**: `AuditResult` (passed bool, hallucination_score 0-10, ai_cliche_score 0-10, issues list, feedback_summary)
- **Pass Criteria**: Hallucination score ≤ 2, AI cliché score ≤ 3, all hyperlinks preserved
- **Retries**: 5
- **Quality Gate**: Yes — validated by `_validate_auditor`

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

### Auxiliary Agents

| Agent                       | Output Type          | Quality Gate | Status                                                     |
| --------------------------- | -------------------- | :----------: | ---------------------------------------------------------- |
| `cover_letter_writer_agent` | `str`                |     Yes      | Defined but **not wired** into the main workflow.          |
| `quality_gate_agent`        | `QualityCheckResult` |     N/A      | Shared validator; scores any pipeline agent's output 0–10. |

---

## Data Models

All models are defined in `sira/models/agents/output.py` using Pydantic v2.

### Core CV & Job Models

| Model            | Purpose                    | Key Fields                                                                                                                |
| ---------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `CV`             | Parsed resume structure    | `full_name`, `contact_info`, `summary`, `skills`, `experience`, `education`, `certifications`, `publications`, `projects` |
| `WorkExperience` | Single job entry           | `company`, `role`, `dates`, `highlights`                                                                                  |
| `JobAnalysis`    | Extracted job requirements | `job_title`, `company_name`, `summary`, `hard_skills`, `soft_skills`, `key_responsibilities`, `keywords_to_target`        |

### Audit & Review Models

| Model                | Purpose                           | Key Fields                                                                                     |
| -------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------- |
| `AuditResult`        | Hallucination & cliché validation | `passed`, `hallucination_score` (0–10), `ai_cliche_score` (0–10), `issues`, `feedback_summary` |
| `AuditIssue`         | Single audit finding              | `severity` ("Critical" / "Minor"), `issue`, `suggestion`                                       |
| `ReviewResult`       | Quality review scoring            | `quality_score` (0–10), `needs_improvement`, `specific_suggestions`, `strengths`               |
| `QualityCheckResult` | Quality gate scoring              | `score` (0–10), `reasoning`, `improvements`                                                    |

### Diff & Report Models

| Model              | Purpose                                    | Key Fields                                                                                                                                                                                                                                     |
| ------------------ | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CVDiff`           | Structural diff original vs. tailored      | `summary_changed`, `skills_reordered`, `skills_deprioritized`, `experience_changes`, `sections_modified`                                                                                                                                       |
| `ExperienceChange` | Per-role bullet changes                    | `role`, `company`, `bullets_rephrased`, `bullets_unchanged`                                                                                                                                                                                    |
| `SkillMatch`       | One job skill judged against the CV        | `skill`, `covered`, `evidence` (CV quote, empty when not covered)                                                                                                                                                                              |
| `SkillMatchResult` | Skill matcher agent output                 | `matches` (list of `SkillMatch`, one per requested skill)                                                                                                                                                                                      |
| `GapAnalysis`      | Skill/keyword gap metrics                  | `missing_hard_skills`, `missing_soft_skills`, `covered_hard_skills`, `covered_soft_skills`, `skill_evidence`, `hard_skill_coverage_percent`, `soft_skill_coverage_percent`, `covered_keywords`, `missing_keywords`, `keyword_coverage_percent` |
| `ReportNarrative`  | Narrative fields written by `report_agent` | `suggestions_to_strengthen`, `audit_summary`, `recommendation_rationale`                                                                                                                                                                       |
| `FinalReport`      | Complete self-review output                | `job_title`, `company_name`, `overall_recommendation` (Strong/Partial/Weak Match), `match_score` (0–100), `what_changed`, `gaps`, `suggestions_to_strengthen`, `audit_summary`, `recommendation_rationale`, `passed`                           |

### Scraping Model

| Model               | Purpose                                                                      |
| ------------------- | ---------------------------------------------------------------------------- |
| `ScrapedJobPosting` | Legacy scraped-job model; the live path uses the `RawScrape` dataclass in `sira/tools/job_scraper.py` |

### Workflow Result

`ResumeTailorResult` (in `models/workflow.py`): `company_name`, `job_title`, `tailored_resume` (JSON string), `audit_report` (dict), `passed`, `final_report` (optional `FinalReport`)

---

## Data Flow

```
1. CLI (main.py)
   ├── Reads resume file → converts DOCX/PDF to Markdown via InputConverterRegistry
   ├── Scrapes job URL via job_scraper_agent → job_posting_markdown
   └── ResumeMemoryService.aresolve_original_resume (hash-based cache check)
       └── Pre-parsed CV if cache hit, None if miss

2. ResumeTailorWorkflow.run()
   ├── PARSING_RESUME: markdown → resume_parser_agent → CV JSON
   │   └── If pre_parsed_cv provided, skip AI parsing
   ├── ANALYZING_JOB: job markdown → analyst_agent → JobAnalysis JSON
   ├── WRITING_CV: CV + JobAnalysis → writer_agent → tailored CV JSON
   │   └── REVIEWING_CV: tailored CV → reviewer_agent → ReviewResult
   │       └── If needs_improvement: writer_agent refines (up to 3 iterations)
   ├── AUDITING_CV: original CV + tailored CV → auditor_agent → AuditResult
   │   └── If failed: retry WRITING → REVIEWING → AUDITING (up to 3 write attempts)
   └── GENERATING_REPORT:
       ├── compute_cv_diff(original, tailored) → CVDiff (pure Python, no LLM)
       ├── match_skills(original, job): literal pre-pass → skill_matcher_agent (one call) → {skill: SkillMatch}
       ├── compute_gap_analysis(original, tailored, job, skill_matches) → GapAnalysis (pure Python)
       ├── compute_match_score(gap) → 0–100; compute_recommendation(score, gap) → verdict (pure Python)
       └── report_agent: score + verdict + diff + gaps + audit + review → ReportNarrative
           └── Workflow assembles FinalReport from the computed numbers + narrative

3. CLI post-processing
   ├── If passed: generate_resume → .md + .pdf + .docx output files
   ├── generate_report_markdown → _report.md
   ├── Files saved to output/{company_name}-{job_title}/
   └── Memory: ResumeMemoryService.save_tailored_resume → SQLite
```

### Key Data Flow Design Decisions

- **Skill coverage uses one judge call, then everything is pure Python.** `skill_matcher_agent` decides which job skills the CV covers (with a CV quote as evidence); `compute_gap_analysis`, `compute_match_score` and `compute_recommendation` in `cv_diff.py` turn those verdicts into deterministic metrics. Given the judge's answer, every number is reproducible. ATS keyword coverage stays a literal substring check on the tailored CV — that is what an applicant tracking system does.
- **Match score formula** (`compute_match_score`): `score = round((60·hard% + 20·soft% + 20·keyword%) / 100)`. A bucket the job lists nothing for is dropped and the remaining weights are rescaled to sum to 100; all buckets empty → 0. `round` is Python's built-in (half to even). **Verdict** (`compute_recommendation`): *Strong Match* when score ≥ 75 and hard-skill coverage ≥ 75 % (or the job lists no hard skills); *Partial Match* when score ≥ 50; otherwise *Weak Match*.
- **The report_agent only produces narrative fields** (suggestions, audit_summary, rationale). The workflow assembles the `FinalReport` from the computed score, verdict, `CVDiff` and `GapAnalysis` plus that narrative.
- **The Report phase always runs**, even when audit fails or the writer produces no output. This ensures the user always gets feedback.
- **Content-hash-based caching** in `ResumeMemoryService`: if the resume file content hash matches a previously parsed version AND the parser version matches, the cached `CV` is reused, skipping AI parsing entirely.

---

## Quality Gate System

### Architecture

- **Shared validator**: A single `quality_gate_agent` scores all gated agents' output, not per-agent custom validation code.
- **Decorator pattern**: Each quality-gated agent has an `@output_validator` async function that calls the quality gate.
- **Threshold**: Score ≥ 9 = pass. Score < 9 = raise `ModelRetry` with improvements.
- **Fallback**: `_QualityState` per-agent holds `last_output`. On `UnexpectedModelBehavior` (retries exhausted), the fallback is used.
- **Retry counts**: Quality gate itself has `retries=2`. Pipeline agents have `retries=5`. Job scraper has `retries=3`.

### Gated Agents

| Agent                       | Validator Function              | Fallback State |
| --------------------------- | ------------------------------- | -------------- |
| `resume_parser_agent`       | `_validate_resume_parser`       | `_parser_qs`   |
| `analyst_agent`             | `_validate_analyst`             | `_analyst_qs`  |
| `writer_agent`              | `_validate_writer`              | `_writer_qs`   |
| `auditor_agent`             | `_validate_auditor`             | `_auditor_qs`  |
| `cover_letter_writer_agent` | `_validate_cover_letter_writer` | `_cover_qs`    |

### Ungated Agents

- `reviewer_agent` — Output drives refinement loop; quality is implicitly validated by the auditor later.
- `report_agent` — Produces narrative; score, verdict and gaps are computed in Python.
- `skill_matcher_agent` — Shape-validated by `_validate_skill_matches`; a wrong answer degrades to literal matching.
- `job_scraper_agent` — Ungated; the deterministic `assert_quality` in `sira/tools/job_scraper.py` runs before it.

### Scoring Criteria by Role

| Role                | Criteria                                                                          |
| ------------------- | --------------------------------------------------------------------------------- |
| Resume Parser       | Completeness, no data loss, correctly structured fields                           |
| Job Analyst         | Keyword coverage, clear requirement identification, no omissions                  |
| CV Writer           | No hallucinations, ATS keywords naturally incorporated, human tone, no clichés    |
| Auditor             | Thorough hallucination check, specific cliché identification, actionable feedback |
| Cover Letter Writer | Authentic human voice, no AI clichés, specific to role, concise                   |

---

## Memory Layer

```
sira/memory/
├── models.py          # Data classes: ResolvedOriginalResume, TailoredResumeRecord, etc.
├── repository.py      # Abstract interface: ResumeMemoryRepository
├── sqlite_repository.py  # SQLite implementation
├── parser.py          # PydanticAIResumeParser (adapter over resume_parser_agent)
└── service.py         # ResumeMemoryService — single entry point for CLI
```

### Key Behaviors

- **Database**: SQLite at `resume_memory.sqlite3` in the per-user data directory (`sira/paths.py`; `SIRA_DATA_DIR` overrides it)
- **Content-hash caching**: `ResumeMemoryService.resolve_original_resume()` hashes the resume file content. If the hash matches a previously parsed version AND the parser version matches, the stored `CV` JSON is deserialized directly — no AI call.
- **Two variants**: `resolve_original_resume` (sync) and `aresolve_original_resume` (async). The CLI uses the async variant since it runs under `asyncio`.
- **Source tracking**: Every resume source is stored with its absolute path and content hash. Multiple tailored resumes can link back to the same source.
- **Job fingerprint**: Each tailored resume is keyed by a truncated SHA-256 hash (first 32 hex chars) of `{job_url}:{job_title}` to avoid duplicates for the same job.

---

## Tools Layer

```
sira/tools/
├── job_scraper.py         # fetch_job_markdown, RawScrape, assert_quality (deterministic, no LLM)
├── job_scraper_helpers.py # parse_html_with_markitdown, parse_html_with_html2text,
│                          # detect_placeholder_content, detect_prompt_injection,
│                          # clean_job_posting_markdown
└── injection_guard.py     # optional local classifier (sira[guard] extra) + consent flow
```

### Job Scraper Architecture

- `fetch_job_markdown(url)`: Playwright (headless Chromium) → raw HTML → Markdown → `assert_quality` → `detect_prompt_injection` → `RawScrape`
- `parse_html_with_markitdown(html)`: Primary parser via `markitdown` library
- `parse_html_with_html2text(html)`: Fallback parser via `html2text` library
- `detect_placeholder_content(text)`: Validates extracted content isn't error/placeholder (checks for `<script` tags, "click here", "error loading", "404", minimum 100 chars)
- `detect_prompt_injection(raw_html, extracted_text)`: Regex scan for instruction overrides, AI-addressing, role/output manipulation, exfiltration requests, invisible Unicode, and phrases present in the HTML but not in the visible text (`hidden_content`). Advisory; returns category names only.
- `clean_job_posting_markdown(markdown)`: Normalizes whitespace, collapses blank lines
- `injection_guard.classify(markdown)`: Optional second layer — a local discriminative classifier (not a generative LLM), opt-in via the `guard` extra plus a remembered consent; any failure degrades to regex-only.

---

## Utils Layer

```
sira/utils/
├── cv_diff.py              # Pure Python CVDiff + GapAnalysis + match score
├── skill_matching.py       # render_cv_text, literal pre-pass
├── markdown_writer.py      # generate_resume (.md/.pdf/.docx), generate_report_markdown
├── resume_converter.py     # InputConverterRegistry: DOCX/PDF → Markdown via markitdown
├── resume_output_converter.py  # Output format conversion utilities
├── pdf_converter.py        # PDF creation helpers
└── validate_inputs.py      # Standalone input validation (not used by Typer CLI)
```

A same-named but different file, `sira/workflows/skill_matching.py`, holds the one exception to "no model calls in `utils/`": `match_skills` orchestration — literal pre-pass → `skill_matcher_agent` → fallback to literal-only matching on `AgentRunError`. It lives under `workflows/` because it calls a model; `utils/skill_matching.py` above stays model-free.

---

## Durable Execution

Every run is one DBOS workflow (`sira.tailor`) whose input is a `TailorInputs` snapshot (resume text, job content, model tiers, quality-gate settings, and the CLI metadata needed for post-processing). Inside it:

```
sira.tailor (workflow, id = run id)
├─ sira.parse_resume  (child workflow)  ─ model-request steps (Parser)
├─ sira.analyze_job   (child workflow)  ─ model-request steps (Analyst)
├─ write → review → audit loop         ─ model-request steps (Writer, Reviewer, Auditor, Quality Gate)
├─ sira.human_checkpoint (step)        ─ the interactive answer, checkpointed
├─ skill matcher                       ─ model-request step (Skill Matcher)
└─ report                              ─ model-request steps (Report)
```

- Every agent carries pydantic-ai's `DBOSDurability` capability: a model request that runs inside the workflow is a checkpointed step (with retries on transient errors). Outside a workflow (the job scraper, the memory cache parser) the capability is transparent.
- Parser and Analyst are child workflows because DBOS requires a deterministic step order inside one workflow; each child owns its own sequence, so they may run concurrently.
- DBOS only continues a run under the same executor id and application version. Sira uses a fresh executor id per process, so a new `sira tailor` never silently picks up an old run; continuation is explicit (`sira resume`) and pinned to the installed Sira version.
- Continuation (`sira/workflows/continuation.py`): interrupted runs are resumed in place; failed runs are forked from the failed step (or the start of a failed child workflow, or the last checkpoint when the user aborted), which creates a new run id with the earlier checkpoints copied.
- The system database is SQLite at `dbos.sqlite3` in the per-user data directory (`sira/paths.py`; `SIRA_DBOS_DATABASE_URL` overrides it). Post-processing (output files, memory save) stays outside the workflow and is repeated by `resume`.
- Every model request is now made in streaming mode (the durability capability attaches an event-stream handler), including non-interactive runs.

---

## CLI

Entry point: `sira/main.py` — Typer app, console script `sira`

### Subcommands

#### `tailor` — Full workflow

```
uv run sira tailor JOB_URL RESUME_PATH [OPTIONS]
```

| Option                  | Type | Default                      | Description                                                       |
| ----------------------- | ---- | ---------------------------- | ----------------------------------------------------------------- |
| `--output-dir`          | PATH | `./output`                   | Output directory                                                  |
| `--model`               | TEXT | `None`                       | LLM provider:model override (e.g., `anthropic:claude-sonnet-4-5`) |
| `--verbose` / `-v`      | FLAG | `False`                      | Stream agent thinking in real-time                                |
| `--debug` / `-d`        | FLAG | `False`                      | Save converted resume, show content hashes                        |
| `--output-pattern`      | TEXT | `{company_name}-{job_title}` | Subdirectory name template                                        |
| `--resume-name-pattern` | TEXT | `{company_name}-{full_name}` | Resume file base name template                                    |

Template variables: `{company_name}`, `{job_title}`, `{full_name}`, `{timestamp}`

#### `re-tailor` — Re-run with audit feedback

```
uv run sira re-tailor JOB_ID RECOMMENDATIONS [OPTIONS]
```

All options from `tailor` plus:

| Option          | Type | Default | Description                               |
| --------------- | ---- | ------- | ----------------------------------------- |
| `--resume-path` | TEXT | `None`  | Resume path (uses stored path if omitted) |

**Edge case**: When the original resume file no longer exists on disk but a source record is stored, the CLI prints an error and instructs the user to re-provide `--resume-path`.

### Execution Flow

Both commands are synchronous wrappers (`def`) that call `asyncio.run()` on async implementation functions:

- `tailor` → `asyncio.run(_tailor_impl(...))`
- `re_tailor` → `asyncio.run(_re_tailor_impl(...))`

---

## Technology Stack

| Component         | Technology      | Version Constraint |
| ----------------- | --------------- | ------------------ |
| Language          | Python          | ≥ 3.13             |
| Package manager   | uv              | latest             |
| Agent framework   | pydantic-ai     | ≥ 2.43, < 3        |
| Data validation   | Pydantic v2     | (via pydantic-ai)  |
| CLI framework     | Typer           | ≥ 0.25.1           |
| Web scraping      | Playwright      | ≥ 1.56.0           |
| Injection guard   | transformers + torch | ≥ 4.45 / ≥ 2.2 (opt-in `guard` extra) |
| HTML→Markdown     | html2text       | ≥ 2025.4.15        |
| DOCX/PDF→Markdown | markitdown      | ≥ 0.1.0            |
| Markdown→PDF      | markdown-pdf    | ≥ 1.10             |
| DOCX generation   | python-docx     | ≥ 1.1.0            |
| Rich output       | rich            | ≥ 14.2.0           |
| Memory            | SQLite (stdlib) | —                  |
| Linting           | ruff            | ≥ 0.14.6 (dev)     |
| Testing           | pytest          | ≥ 8.0.0 (dev)      |
| Releases          | commitizen      | ≥ 4.15.1 (dev)     |
| Build backend     | hatchling       | —                  |

---

## Key Design Decisions

1. **Shared Quality Gate**: One `quality_gate_agent` validates all pipeline agents via role-specific scoring criteria, rather than per-agent custom validation code.

2. **Python-Computed Metrics**: `GapAnalysis`, `match_score` and `overall_recommendation` are computed in `cv_diff.py` from per-skill verdicts. The only model involvement is the skill matcher's yes/no-with-evidence per skill; the report agent only writes prose.

3. **Always-Run Report Phase**: The Report phase executes regardless of audit pass/fail, ensuring users always get actionable feedback.

4. **Content-Hash Caching**: `ResumeMemoryService` caches parsed CVs by content hash + parser version. If the resume hasn't changed, AI parsing is skipped entirely.

5. **Fallback State Pattern**: Each quality-gated agent stores its `last_output` in a module-level `_QualityState` instance. On quality gate exhaustion, the fallback is used rather than crashing.

6. **Inner Loop with Outer Retry**: The Write → Review refinement loop (up to 3 iterations) is nested inside the Write → Audit retry loop (up to 3 attempts). This allows both fine-tuning and broader corrections.

7. **Job Fingerprint Dedup**: Tailored resumes are keyed by a truncated SHA-256 fingerprint (first 32 hex chars of `{job_url}:{job_title}`), preventing duplicate entries for the same job applied multiple times.

8. **Pre-Parsed CV Bypass**: The workflow accepts an optional `pre_parsed_cv` parameter. When provided (from cache), the Resume Parser stage is skipped entirely, saving AI calls.

9. **Streaming via Verbose Mode**: `run_agent()` has a `verbose` flag that streams `TextPartDelta` and `ThinkingPartDelta` events to console via Rich.

10. **CLI runs under asyncio**: All async implementation functions use `asyncio.run()` from synchronous Typer command wrappers.
