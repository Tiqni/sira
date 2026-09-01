# Resume memory

Sira keeps a small local database so that repeat runs are cheaper and always start from
the same place: your **original** resume.

## Where it lives

```
memory/resume_memory.sqlite3
```

The path is relative to the directory you run `sira` from. The parent directory is
created automatically on first use, and the file is in `.gitignore` — it is runtime
data, not source. Nothing is uploaded anywhere; the database is a plain SQLite file on
your machine.

## What it stores

```mermaid
erDiagram
    original_resume_sources ||--o| parsed_original_resumes : "cached parse"
    original_resume_sources ||--o{ tailored_resumes : "one per job"

    original_resume_sources {
        TEXT id PK
        TEXT path "where your resume lives"
        TEXT content_hash "SHA-256 of the text"
        INTEGER is_active "the resume in use"
    }
    parsed_original_resumes {
        TEXT source_id PK
        TEXT content_hash "must match to reuse"
        TEXT parser_version "must match to reuse"
        TEXT cv_json "the structured CV"
    }
    tailored_resumes {
        TEXT id PK "the job ID you pass to re-tailor"
        TEXT job_fingerprint UK "sha256(url + title)"
        TEXT company_name
        TEXT job_title
        TEXT tailored_cv_json
        TEXT audit_report_json
        TEXT job_posting_markdown "so re-tailor need not re-scrape"
    }
```

## The parse cache

Parsing a resume into a structured `CV` costs a model call. Sira skips it when it can.

```mermaid
flowchart TD
    START([tailor starts]) --> HASH["SHA-256 the resume text"]
    HASH --> LOOK{"Cached parse for<br/>this source?"}
    LOOK -->|no| PARSE
    LOOK -->|yes| CHECK{"content_hash AND<br/>parser_version both match?"}
    CHECK -->|no| PARSE["Run resume_parser_agent<br/>(one model call)"]
    CHECK -->|yes| REUSE["Reuse the stored CV<br/>(no model call)"]
    PARSE --> SAVE["Store the parse<br/>with hash + version"]
    SAVE --> GO([Pipeline continues])
    REUSE --> GO
```

Two things invalidate the cache, and both should:

- **You edited your resume.** The content hash changes, so the old parse is stale.
- **The parser itself changed.** `parser_version` (currently `1.1.0`) is bumped when
  the parsing prompt or output shape changes, so old parses are not silently reused
  under new rules.

## Rules the memory layer enforces

- The **first run must be given a resume path**, so there is something to store.
- Later runs reuse the stored active original resume.
- **Every job starts from the original resume** — never from a previously tailored one.
  Tailoring on top of tailoring is how a resume drifts away from the truth.
- Each successful run stores the tailored CV, the audit report, and the scraped job
  posting, all linked back to the original source.

## Job IDs and `re-tailor`

A successful run prints a **job ID** — the primary key of the `tailored_resumes` row.
Pass it to `re-tailor` to iterate without re-scraping:

```bash
uv run sira re-tailor <JOB_ID> "Lead with the platform migration work"
```

Because `job_posting_markdown` was stored, `re-tailor` reuses the posting text
directly. No browser is launched and no scrape happens.

Each job is also given a fingerprint — `sha256(job_url + ":" + job_title)`, truncated —
which is unique in the table. Running `tailor` again for the same URL and title updates
that row rather than creating a second one.

!!! warning "If you move your resume file"
    `re-tailor` looks up the original resume at its recorded path. If the file is gone,
    the run fails and tells you to pass `--resume-path` pointing at the new location.

## Starting over

The database is a single file. To wipe all stored resumes and history:

```bash
rm -rf memory/
```

The next run recreates the schema and treats your resume as new.
