# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.0.0 (2026-09-01)

First public release of Sira.

### Feat

- **cli**: `sira tailor <JOB_URL> <RESUME>` and `sira re-tailor <JOB_ID> "<RECOMMENDATIONS>"`, built on Typer
- **scraper**: fetch a job posting from any public URL with Playwright and convert it to Markdown
- **pipeline**: six-stage multi-agent workflow — Parser, Analyst, Writer, Reviewer, Auditor, Report — with a Write → Review → Audit refinement loop
- **quality-gate**: advisory 0–10 scoring on gated agents, with a configurable `--gate-threshold` and graceful fallback to the last good output
- **memory**: SQLite-backed store for the original resume and every tailored output, with content-hash caching of parsed CVs
- **models**: any PydanticAI provider via `--model <provider>:<model>`, plus a `--fast` tier for cheaper stages; local Ollama models work without an OpenAI key
- **reporting**: live Rich dashboard by default, deep token streaming with `-v`
- **output**: Markdown, DOCX and PDF resumes, plus a self-review report with a CV diff and gap analysis
- **anti-hallucination**: agents may only rephrase existing resume content, and a cliché blacklist keeps the tone human
