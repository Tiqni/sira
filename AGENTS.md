# AGENTS.md — Sira

## Quickstart

```bash
uv sync                        # install dev deps
export OPENAI_API_KEY=sk-…
uv run sira setup                                   # download the Chromium browser (once)
uv run sira tailor <JOB_URL> <RESUME_PATH>          # scrape + tailor
uv run sira re-tailor <JOB_ID> <RECOMMENDATIONS>    # re-run with feedback
uv run sira resume <RUN_ID>                         # continue a killed/failed run
uv run sira runs                                    # list recent runs
```

## Development Workflow

**Always use isolated git worktrees.** Never commit or modify directly on `main`. Worktrees allow multiple agents to work on the same repo concurrently without interference.

```bash
# Worktrees are created automatically via EnterWorktree native tool.
# .worktrees/ and .claude/worktrees/ are gitignored — use them for isolation.
# The main branch stays pristine as the source of truth.
```

## Tool Invocation

**Always use `uv` for any Python invocation.** Never use bare `python`, `python3`, or `pip` directly.

```bash
uv run pytest ...           # tests
uv run python -m ...        # modules
uv run sira ...    # CLI
uv sync                     # install deps
uv add <pkg>                # add dependency
```

## CLI Commands

Five Typer subcommands in `sira/main.py`:

| Command     | Signature                                                                                                                                    | Description                           |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `tailor`    | `tailor JOB_URL RESUME_PATH [OPTIONS]`                                                                                                       | Scrape job posting, run full pipeline |
| `re-tailor` | `re-tailor JOB_ID RECOMMENDATIONS [--resume-path] [OPTIONS]`                                                                                 | Re-run with prior audit feedback      |
| `resume`    | `resume RUN_ID [--verbose] [--style]`                                                                                                        | Continue a killed/crashed/failed run from its last DBOS checkpoint |
| `runs`      | `runs [--limit N]`                                                                                                                           | List recent runs and their status     |
| `setup`     | `setup`                                                                                                                                      | Download the Chromium browser Playwright drives |

`[OPTIONS]` for `tailor` and `re-tailor` is the same list — see [CLI Options](#cli-options) below.

A console script is registered: `sira` → `sira.main:run` (see `pyproject.toml`).

## Execution & Commands

| Command       | How to run                                   | Description                                                |
| ------------- | -------------------------------------------- | ---------------------------------------------------------- |
| `tailor`      | `uv run sira tailor <URL> <PATH>`   | Scrape job posting and run the agentic workflow.           |
| `re-tailor`   | `uv run sira re-tailor <ID> <RECS>` | Re-run tailoring with prior audit recommendations.         |
| `resume`      | `uv run sira resume <RUN_ID>`       | Continue an interrupted or failed run.                     |
| `runs`        | `uv run sira runs`                  | List recent runs.                                          |
| `setup`       | `uv run sira setup`                 | Install the Playwright Chromium browser.                   |
| `test`        | `make test` or `uv run pytest -v`            | Run the full test suite.                                   |
| `install/dev` | `make install/dev` or `uv sync`              | Install development dependencies (incl. `pytest`, `ruff`). |
| `help`        | `make help`                                  | Show Makefile targets.                                     |

> **Note**: The Makefile `run` target is deprecated and uses broken paths. Use `uv run sira tailor` instead.

### CLI Options

| Option                  | Description                                                                         |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `--output-dir`          | Output directory (default: `./output`)                                              |
| `--model`               | AI model override (default: `openai:gpt-5-mini`)                                    |
| `--verbose` / `-v`      | Stream agent thinking and prompts in real-time                                      |
| `--debug` / `-d`        | Enable debug output; saves converted resume to `resume_debug.md`                    |
| `--output-pattern`      | Template for job-specific subdirectory name (default: `{company_name}-{job_title}`) |
| `--resume-name-pattern` | Template for resume file base name (default: `{company_name}-{full_name}`)          |
| `--style`               | Resume template for the PDF and DOCX: `modern` (default), `classic`, `compact`; also on `resume` |
| `--fast`                | Speed preset: gate threshold 5 and mechanical stages on `openai:gpt-5-nano`; `--model` (or `openai:gpt-5-mini`) becomes the strong tier. Loops stay at their defaults. |
| `--write-attempts N`    | Max writer attempts in the write → review → audit loop (default: `2`)               |
| `--review-iterations N` | Max reviewer iterations per write attempt (default: `1`)                            |
| `--quality-gate` / `--no-quality-gate` | Enable the advisory quality gate (default: on)                        |
| `--gate-threshold N`    | Re-run a gated agent only when its score is below this (default: `6`)               |
| `--interactive` / `-i`  | Pause at quality checkpoints (audit failure, weak match); skipped when stdin is not a TTY |
| `--resume-path`         | `re-tailor` only: where the original resume lives now (default: the stored path)    |

Pattern variables: `{company_name}`, `{job_title}`, `{full_name}`, `{timestamp}` — all slugified.

### Environment

- **Python** `3.13+` managed by `uv`. (`.python-version` pins 3.13)
- **Required env var**: the key for the provider you use — `OPENAI_API_KEY` for the default `openai:gpt-5-mini`; see the README's LLM Providers table for the others. `--fast` always needs `OPENAI_API_KEY` because its fast tier is `openai:gpt-5-nano`.

### Inputs

- `tailor` and `re-tailor` use **positional arguments** (not interactive prompts).
- `tailor` requires a job URL (scraped via Playwright) and a resume path (`.md`, `.docx`, or `.pdf`).
- `re-tailor` requires a job ID (UUID from a prior `tailor` run) and recommendations text. It uses the stored job posting and resolved resume.
- `sira/utils/` contains:
  - `validate_inputs.py` — Standalone input validation script (not used by the Typer CLI).
  - `resume_converter.py` — Converts DOCX/PDF to Markdown via `markitdown`.
  - `markdown_writer.py` — Generates the self-review report's Markdown (`generate_report_markdown`).
  - `cv_diff.py` — Computes structural diffs, gap analysis, the match score and the verdict (pure Python over the skill matcher's verdicts).
  - `skill_matching.py` — `render_cv_text` (CV → plain text) and the literal skill pre-pass (`literal_matches`); no model calls.
  - `skill_cleanup.py` — `clean_skill_groups`: collapses version/case/acronym duplicates across skill groups (pure Python, idempotent); applied to the parsed CV before caching and to the original and tailored CVs in the workflow.
- `sira/rendering/` renders the tailored `CV` to `.md`/`.pdf`/`.docx` via `render_resume(cv, dir, base_name, style)`; one `TemplateSpec` per style (`modern`, `classic`, `compact`) drives both the PDF CSS and the DOCX styler.
- `sira/workflows/skill_matching.py` — `match_skills`: orchestrates the literal pre-pass, then `skill_matcher_agent` for the remaining skills, falling back to literal-only matching on `AgentRunError`.

## Architecture

Single package: `sira/`

| Directory                      | Purpose                                                                |
| ------------------------------ | ---------------------------------------------------------------------- |
| `sira/workflows/` | Workflow orchestration (`ResumeTailorWorkflow`) and agent definitions  |
| `sira/models/`    | Pydantic data models (agents output types, workflow result)            |
| `sira/memory/`    | SQLite-backed memory (parser, repository, service)                     |
| `sira/tools/`     | Playwright scraping, HTML→Markdown parsing, placeholder detection      |
| `sira/rendering/` | Styled resume output: TemplateSpec styles, HTML/CSS → PDF, DOCX, Markdown |
| `sira/utils/`     | Report markdown writer, resume input conversion, CV diff, skill matching, validation |
| `output/`                      | Default output directory for generated files                           |

### Multi-Agent Pipeline (6 Stages)

1. **Resume Parser** — parses Markdown/DOCX/PDF resume into structured `CV` object.
2. **Job Analyst** — extracts structured `JobAnalysis` (title, company, skills, keywords).
3. **CV Writer** — tailors CV to match job requirements using only original content.
4. **Reviewer** — scores draft quality and provides improvement suggestions (feeds refinement loop).
5. **Auditor** — checks for hallucinations and AI clichés; triggers write-retry loop on failure.
6. **Report Generator** — compiles `FinalReport` with CVDiff, gap analysis, and narrative.

**Job scraping runs BEFORE the pipeline** — the CLI calls the deterministic `fetch_job_markdown()` (`sira/tools/job_scraper.py`: Playwright → Markdown → quality gate → prompt-injection scan), then `job_scraper_agent` strips site chrome from that Markdown (`str` → `str`). The result is passed into the pipeline as `job_content`. Injection indicators are advisory: a warning is printed and the run continues.

The **Write → Review → Audit** inner loop: after the initial write, the reviewer assesses quality and the writer refines (up to `--review-iterations`, default 1). The auditor then checks the final draft. If audit fails, the entire loop retries from a fresh write (up to `--write-attempts`, default 2).

On a cold cache the Parser and Analyst run concurrently as two DBOS child workflows (`DBOS.start_workflow_async`), never via `asyncio.gather` — see the DBOS rules in `CLAUDE.md`.

### Agents Defined (in `workflows/agents.py`)

| Agent                       | Output Type          | Retries | Has Quality Gate                                     |
| --------------------------- | --------------------- | ------- | ------------------------------------------------------ |
| `job_scraper_agent`         | `str`                | 3       | No (deterministic `assert_quality` runs before it)   |
| `resume_parser_agent`       | `CV`                 | 2       | No (removed for speed; parse is cached by hash)      |
| `analyst_agent`             | `JobAnalysis`        | 2       | No (removed for speed)                               |
| `writer_agent`              | `CV`                 | 2       | Yes                                                  |
| `reviewer_agent`            | `ReviewResult`       | 5       | No                                                   |
| `auditor_agent`             | `AuditResult`        | 2       | Yes                                                  |
| `skill_matcher_agent`       | `SkillMatchResult`   | 3       | No (shape validator; falls back to literal matching) |
| `report_agent`              | `ReportNarrative`    | 5       | No                                                   |
| `cover_letter_writer_agent` | `str`                | 2       | Yes (defined but not wired into workflow)            |
| `quality_gate_agent`        | `QualityCheckResult` | 2       | N/A (this is the gate itself)                        |

**Quality gate fallback**: Each gated agent has a `_QualityState` instance (`_writer_qs`, `_auditor_qs`, `_cover_qs`). When the quality gate exhausts retries, the system uses the last available output instead of failing fatally (`UnexpectedModelBehavior` is caught and fallback applied). `_parser_qs` and `_analyst_qs` still exist and are read by the workflow's fallback branches, but nothing writes to them since those gates were removed.

**Durability**: every agent is constructed with `capabilities=[_durability()]` (pydantic-ai's `DBOSDurability`), so a model request made inside the `sira.tailor` DBOS workflow is a checkpointed step. A new agent without it would not be resumable.

### Technology Stack

- **Framework**: `pydantic-ai` (agent orchestration)
- **LLM**: OpenAI GPT (configurable via `--model`; default `openai:gpt-5-mini`)
- **Models**: Pydantic v2 for structured outputs
- **CLI**: Typer with five subcommands (`tailor`, `re-tailor`, `resume`, `runs`, `setup`)
- **Web Scraping**: Playwright (headless Chromium) in `fetch_job_markdown()`; `job_scraper_agent` only cleans the Markdown. Optional `guard` extra (transformers + torch) adds a local prompt-injection classifier.
- **HTML→Markdown**: `html2text` and `markitdown` (multi-strategy fallback in `job_scraper_helpers.py`)
- **Resume Conversion**: `markitdown` via `InputConverterRegistry` (DOCX/PDF → Markdown)
- **Memory**: SQLite (`resume_memory.sqlite3` in the per-user data directory, see `sira/paths.py`) with `ResumeMemoryService`
- **Durable execution**: DBOS (`pydantic-ai[dbos]`), checkpoint database `dbos.sqlite3` in the same data directory (`SIRA_DBOS_DATABASE_URL` overrides it); runtime setup in `sira/durability.py`, continuation logic in `sira/workflows/continuation.py`
- **Rendering**: `sira/rendering/` — Jinja2 + PyMuPDF for the PDF, python-docx for the DOCX
- **Formatting**: `ruff`

## Testing

```bash
uv run pytest -v
```

- Test config lives in `pyproject.toml` (`[tool.pytest.ini_options]`).
- `tests/conftest.py` disables real LLM calls when testing (`models.ALLOW_MODEL_REQUESTS = False`).
- All tests use a dummy `OPENAI_API_KEY`; no real API calls are made.
- Layout: `tests/test_*.py` (CLI, scraper, quality gate, converters, durability, skill matching, smoke), `tests/memory/`, `tests/rendering/`, `tests/reporting/`, `tests/workflows/` (pipeline, continuation, loop config, model tiers, reporter events). Test data builders live in `tests/factories.py`; there is no checked-in demo-resume directory.
- One DBOS runtime is started for the whole session by `tests/conftest.py`.

## Style & Linting

- Python style enforced by `ruff` (see `pyproject.toml` dev dependencies).
- No pre-commit hooks. CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, pytest with coverage, and `mkdocs build --strict` on every PR to `main`; `docs.yml` deploys the site and `release.yml` handles versioning and PyPI publishing. Run the gate in `CLAUDE.md` yourself before pushing.

## Key Conventions

- **Never hallucinate**: Agents must only rephrase existing content, never invent skills or experiences.
- **Anti-cliché**: Avoid terms like "spearheaded", "synergy", "leveraged", "game-changer".
- **Quality Gates**: Core pipeline agents' output is scored 0–10 by a quality gate validator; a score below the gate threshold (`--gate-threshold`, default 6) triggers a retry. On exhaustion, fallback to last available output via `_QualityState`.
- **Resume formats**: Accepts `.md`, `.docx`, `.pdf`; DOCX/PDF are converted to Markdown before processing.
- **Memory**: Each `tailor` run stores the original resume, tailored CV, audit result, and job posting in SQLite. `re-tailor` reuses the stored job posting.
- **Output**: Tailored resumes and reports are saved to `output/<company_name>-<job_title>/` (overridable via `--output-dir`).

### Content-Hash Caching

The `ResumeMemoryService.aresolve_original_resume` method (async, used by CLI under asyncio) computes a SHA-256 content hash of the resume file. If the hash and parser version match a previously stored parsed record, the pre-parsed `CV` is reused — skipping AI parsing entirely. This avoids redundant LLM calls on re-runs with unchanged resumes.

### `re-tailor` Edge Cases

- If the original resume file no longer exists on disk but a source record is stored, `re-tailor` fails with a message to re-provide `--resume-path`.
- If no original source record exists for the job, the same fallback applies.
- The stored job posting markdown is reused directly; no re-scraping occurs.

## Other Instruction Files

- `.github/copilot-instructions.md` — Additional agent-specific guidelines and anti-hallucination rules.
- `ARCHITECTURE.md` — Detailed system architecture and data flow documentation.
