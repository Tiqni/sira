# Troubleshooting

Errors are grouped by the message Sira prints.

## Playwright cannot find a browser

```
❌ Failed to scrape job posting from URL: … Executable doesn't exist at …
💡 Tip: Ensure the URL is publicly accessible and contains a valid job posting.
```

The Playwright Python package is installed, but the actual browser binary is a separate
download. Install it once:

```bash
uv run playwright install chromium
```

On Linux you may also need the shared libraries the browser links against:

```bash
uv run playwright install --with-deps chromium
```

## `❌ Failed to scrape job posting from URL`

The scraper drives a real headless browser, so it can read JavaScript-rendered
postings. It still fails when:

| Cause | What to do |
| --- | --- |
| The posting is behind a login | Save the posting text to a file and open an issue — Sira currently only scrapes public URLs. |
| The page is bot-protected | Some boards block headless browsers outright. Try the employer's own careers page rather than the aggregator. |
| The page is slow | The default timeout is 30 seconds per navigation. A very slow page can exceed it; retry. |
| The URL redirects to a search page | The posting has probably expired. |

## `❌ Job posting scraped but content is empty`

The page loaded, but nothing survived extraction. This normally means the posting body
is rendered behind an interaction (an "expand" button) or the page is a listing rather
than a single posting. Use the URL of the posting itself.

## `❌ Error: Job URL must start with http:// or https://`

The first argument is the URL, the second is the resume path — in that order. A local
file path in the first position produces this message.

## `❌ Resume file not found at …`

The path does not exist. `~` is expanded, but shell quoting still matters:

```bash
uv run sira tailor <JOB_URL> "~/Documents/my resume.pdf"
```

## `❌ Failed to convert resume: …`

Only `.md`, `.docx`, and `.pdf` are accepted. Some PDFs — scans, or heavily designed
layouts — extract badly or not at all. Two things to try:

1. Export the resume to `.docx` from the original editor and pass that instead.
2. Run with `--debug` and read `resume_converted.md` in the output directory. That file
   is exactly what the parser saw. If it is empty or scrambled, the problem is
   conversion, not the model.

## `❌ Resume content is empty`

The file exists but converted to nothing. Almost always an image-only PDF, which has no
extractable text layer. Convert it to text first, or use the original document.

## `❌ Original resume not found at recorded path: …`

`re-tailor` looks the original resume up at the path recorded when you first ran
`tailor`. If you moved or renamed that file, point at the new location:

```bash
uv run sira re-tailor <JOB_ID> "…" --resume-path ~/Documents/resume.pdf
```

## `❌ Job not found: <id>`

The job ID is the UUID printed at the end of a successful `tailor` run, and it is
looked up in `memory/resume_memory.sqlite3` **relative to your current directory**.
Running `re-tailor` from a different directory looks in a different (empty) database.
Run it from the same place you ran `tailor`.

## `❌ No job posting content stored for this job`

The job row predates the column that stores the posting text, so `re-tailor` has
nothing to work from. Re-run `tailor` on the original URL.

## Authentication errors from the model provider

```
AuthenticationError: Incorrect API key provided …
```

Each provider reads its own environment variable — see
[Models and providers](models.md#supported-providers). Two cases catch people out:

- **No key at all.** Agents are built at import time with a placeholder key so that
  importing Sira never fails. The error only appears at the first real call.
- **`--fast` with a non-OpenAI model.** The fast tier stays on `openai:gpt-5-nano`, so
  an `OPENAI_API_KEY` is still required. Drop `--fast` to use a single provider.

## Ollama: connection refused, or a missing base URL

PydanticAI has no default endpoint for Ollama. Export it explicitly:

```bash
export OLLAMA_BASE_URL=http://localhost:11434/v1
```

Then confirm the daemon is up (`ollama list`) and the model is pulled
(`ollama pull llama3`).

## The audit keeps failing

The auditor rejects a draft that invents facts, leans on clichés, or drops hyperlinks.
Give the loop more room:

```bash
uv run sira tailor <JOB_URL> <RESUME_PATH> --write-attempts 3 --review-iterations 2
```

If it still fails, read the report's **Audit Summary** — the reason is usually specific,
and `re-tailor` with an instruction addressing it works better than more retries.

## The terminal output looks garbled

The live dashboard and some plain `print()` calls in the workflow can interleave in an
interactive terminal. The run is unaffected; only the drawing is. Use `--verbose` for
clean streaming output, or pipe the output — in a non-TTY the dashboard degrades to
line-by-line logging.

## The run is slow or expensive

Start with `--fast`, then tune:

```bash
uv run sira tailor <JOB_URL> <RESUME_PATH> \
  --no-quality-gate --write-attempts 1 --review-iterations 0
```

The trade-offs behind each flag are in [Controlling cost](models.md#controlling-cost).

## Nothing here matches

Run with `--verbose` to see each agent's input and output as it streams, and open an
issue at [Tiqni/sira/issues](https://github.com/Tiqni/sira/issues) with that output.
