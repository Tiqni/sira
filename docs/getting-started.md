# Getting started

This page takes you from an empty directory to a tailored resume on disk.

## 1. Install the prerequisites

Sira needs Python 3.13 or newer and [uv](https://github.com/astral-sh/uv) — a fast
Python package manager that also runs commands inside the project environment. Every
command below goes through `uv`; there is no `pip install` step and no virtual
environment to activate by hand.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Clone and sync

```bash
git clone https://github.com/Tiqni/sira
cd sira
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates a `.venv/` directory, and
installs the exact pinned versions. It is safe to re-run at any time.

## 3. Install the browser Playwright needs

The job scraper drives a real headless Chromium browser, because many job boards render
their posting with JavaScript and return an almost-empty page to a plain HTTP request.
Playwright ships as a Python package, but the browser binary is a separate download:

```bash
uv run playwright install chromium
```

!!! warning "Skipping this step"
    Without it, the first `tailor` run fails while fetching the posting with an error
    about a missing executable. See [Troubleshooting](troubleshooting.md#playwright-cannot-find-a-browser).

## 4. Set your API key

Sira defaults to OpenAI's `gpt-5-mini`. Export the matching key:

```bash
export OPENAI_API_KEY=sk-…
```

To use Anthropic, Gemini, Groq, Mistral, or a local model through Ollama instead, see
[Models and providers](models.md). Each provider reads its own environment variable.

## 5. Run it

```bash
uv run sira tailor <JOB_URL> <RESUME_PATH>
```

Both arguments are **positional** — Sira never prompts you interactively for them.

- `JOB_URL` must start with `http://` or `https://`.
- `RESUME_PATH` points at a `.md`, `.docx`, or `.pdf` file. DOCX and PDF are converted
  to Markdown before parsing.

A real example:

```bash
uv run sira tailor \
  https://www.linkedin.com/jobs/view/12345678 \
  ~/Documents/resume.pdf
```

## 6. Read the output

Files land in `output/<company>-<job-title>/`:

```
output/
└── acme_corp-senior_engineer/
    ├── acme_corp-jane_doe.md          ← tailored resume (Markdown)
    ├── acme_corp-jane_doe.pdf         ← same resume as PDF
    ├── acme_corp-jane_doe.docx        ← same resume as DOCX
    └── acme_corp-jane_doe_report.md   ← self-review report
```

The report is the part worth reading first: it lists what changed, which job keywords
your resume covers, which skills you are genuinely missing, and an overall verdict of
*Strong Match*, *Partial Match*, or *Weak Match*. See
[Output and reports](output.md) for a full walkthrough.

The run also prints the report to your terminal, along with a **job ID** — a UUID you
need if you later want to re-run the tailoring with feedback.

## 7. Iterate on the result

If the report suggests something, feed it back without re-scraping the posting:

```bash
uv run sira re-tailor <JOB_ID> "Put more emphasis on cloud infrastructure work"
```

`re-tailor` reuses the stored job posting and your stored original resume, so it costs
one fewer scrape and always starts from your *original* resume — never from a previously
tailored one.

## Making runs faster and cheaper

A default run makes a lot of model calls. Two flags cut that down:

```bash
# Speed preset: fewer loop iterations, cheaper model for mechanical stages
uv run sira tailor <JOB_URL> <RESUME_PATH> --fast

# Or pick a cheaper model outright
uv run sira tailor <JOB_URL> <RESUME_PATH> --model openai:gpt-4o-mini
```

Every knob is documented in the [CLI reference](cli.md#speed-and-quality-flags).

## Watching what the agents do

By default you get a live dashboard that updates as each stage finishes. To watch the
agents' reasoning stream by token instead:

```bash
uv run sira tailor <JOB_URL> <RESUME_PATH> --verbose
```

!!! note "Why `--verbose` looks cleaner than the dashboard"
    The workflow still writes some progress lines with plain `print()`, which can
    interleave with the dashboard's live panel in an interactive terminal and garble
    the display. Nothing is actually wrong with the run — only the drawing. In a
    non-interactive environment (a pipe, or CI) the dashboard degrades to plain
    line-by-line logging and the problem disappears.

## What happens on the second run

Sira remembers. Your original resume is stored in a local SQLite database on the first
run, and its parsed form is cached by content hash — so if the file has not changed,
the second run skips the parsing model call entirely. Details in
[Resume memory](memory.md).
