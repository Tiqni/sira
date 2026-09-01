# CLI reference

Sira exposes two commands, both taking **positional arguments** — it never prompts you
for the URL or the resume path.

```bash
uv run sira tailor <JOB_URL> <RESUME_PATH> [OPTIONS]
uv run sira re-tailor <JOB_ID> <RECOMMENDATIONS> [OPTIONS]
```

You can also run the module directly, which bypasses the installed console script:

```bash
uv run python sira/main.py tailor <JOB_URL> <RESUME_PATH>
```

---

## `tailor`

Runs the full pipeline: scrape → parse → analyse → write → review → audit → report.

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `JOB_URL` | yes | URL of the job posting. Must start with `http://` or `https://`. |
| `RESUME_PATH` | yes | Path to your resume: `.md`, `.docx`, or `.pdf`. `~` is expanded. |

### Example

```bash
uv run sira tailor \
  https://www.linkedin.com/jobs/view/12345678 \
  ~/Documents/resume.pdf \
  --model openai:gpt-4o-mini \
  --fast
```

---

## `re-tailor`

Re-runs the write → review → audit loop against a job you have already scraped, with
your own instructions folded in. The stored job posting is reused, so nothing is
scraped a second time, and the run always starts from your **original** resume — never
from the previous tailored output.

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `JOB_ID` | yes | The UUID printed at the end of the earlier `tailor` run. |
| `RECOMMENDATIONS` | yes | Free text: what you want changed. Quote it as one shell argument. |

### Example

```bash
uv run sira re-tailor \
  a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  "Emphasise cloud infrastructure work and drop the teaching section" \
  --resume-path ~/Documents/resume.pdf
```

!!! tip "When you need `--resume-path`"
    Sira records where your original resume lived on disk. If you have since moved or
    renamed that file, `re-tailor` cannot find it and you must point at the new
    location with `--resume-path`.

---

## Options

Every option below works on **both** commands, except `--resume-path`, which is
`re-tailor` only.

### Input and output

| Option | Default | Description |
| --- | --- | --- |
| `--resume-path PATH` | stored path | *(`re-tailor` only)* Where your original resume lives now. |
| `--output-dir PATH` | `./output` | Root directory for generated files. |
| `--output-pattern TEMPLATE` | `{company_name}-{job_title}` | Name of the per-job subdirectory. |
| `--resume-name-pattern TEMPLATE` | `{company_name}-{full_name}` | Base filename for the generated resume, without extension. |

Both patterns accept `{company_name}`, `{job_title}`, `{full_name}`, and `{timestamp}`
(today's date as `YYYYMMDD`). Values are lowercased, spaces become underscores, and
anything else is stripped. A pattern that resolves to a path separator, a `..`, or an
absolute path is rejected before any file is written.

### Model selection

| Option | Default | Description |
| --- | --- | --- |
| `--model MODEL` | `openai:gpt-5-mini` | Provider and model as `provider:model`. |

The override is applied **before** the scraper runs, so every stage — including the
ones that run outside the workflow — uses the model you asked for. See
[Models and providers](models.md) for the full provider table.

### Speed and quality flags

| Option | Default | Description |
| --- | --- | --- |
| `--fast` | off | Speed preset. Sets 2 write attempts, 1 review iteration, gate threshold 5, and puts mechanical stages on a cheaper model tier. |
| `--write-attempts N` | `2` | Maximum writer attempts in the write → review → audit loop. |
| `--review-iterations N` | `1` | Maximum reviewer iterations per write attempt. |
| `--quality-gate` / `--no-quality-gate` | on | Turn the advisory quality gate on or off. |
| `--gate-threshold N` | `6` | Re-run an agent only when its quality score (0–10) is below this. |

The quality gate is **advisory**: it scores an agent's output once and only asks for a
retry when the score is below the threshold. Raising the threshold buys quality with
tokens and time; lowering it does the opposite. Turning it off with
`--no-quality-gate` removes the scoring calls entirely, which is the cheapest setting.

!!! note "`--fast` and `--model` together"
    `--fast` sets a two-tier model split: `openai:gpt-5-nano` for mechanical stages
    (Parser, Analyst, Reviewer, Quality Gate) and a stronger model for the rest. If
    you also pass `--model`, your choice becomes the strong tier and the fast tier
    stays `openai:gpt-5-nano`. Combining `--fast` with a non-OpenAI `--model` therefore
    still needs an `OPENAI_API_KEY` for the fast tier.

### Display and diagnostics

| Option | Default | Description |
| --- | --- | --- |
| `--verbose`, `-v` | off | Stream every agent's thinking and output tokens as they arrive, instead of the live dashboard. |
| `--debug`, `-d` | off | Extra diagnostics, and writes the converted resume Markdown to `resume_debug.md` in the job output directory. |
| `--interactive`, `-i` | off | Pause at quality checkpoints and ask what to do. |

### Interactive mode

With `--interactive`, the run stops at two points and asks:

1. **The audit failed** — the auditor found invented facts, clichés, or dropped links.
2. **Weak match** — the finished resume is unlikely to pass ATS screening for this role.

At each stop you choose:

| Key | Action |
| --- | --- |
| `c` | Continue anyway (the default). |
| `f` | Give written feedback and re-run the write → audit cycle. Available once per run. |
| `q` | Quit without saving. |

If standard input is not a terminal — a pipe, or CI — the checkpoints are skipped and
the default (`c`, continue) is taken automatically, so `--interactive` never hangs a
non-interactive job.

---

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The run finished and files were written. |
| `1` | The run failed, or you quit at an interactive checkpoint. |

Failures that produce exit code `1` include: a `JOB_URL` that is not `http(s)`, a
resume file that does not exist or is empty, a resume format that cannot be converted,
a posting that could not be scraped or came back empty, and an output pattern that
resolves to an unsafe path.

An audit failure is **not** an error. Sira reports the failure, still writes the
report, and exits `0`.
