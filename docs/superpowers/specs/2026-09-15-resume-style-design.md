# Styled resume output (PDF + DOCX templates) — design

Date: 2026-09-15
Status: approved design, not yet implemented
Issue: https://github.com/Tiqni/sira/issues/15

## 1. Goal

1. The tailored resume PDF and DOCX must look professionally designed: clear
   typographic hierarchy, an accent colour, consistent spacing, a proper
   contact line under the name. Today both use library defaults
   (`markdown-pdf` with no CSS; `python-docx` with the blank Word template).
2. The user picks one of **three named templates** with `--style
   modern|classic|compact` (default `modern`). Every template is
   **single-column and ATS-safe** (ATS = Applicant Tracking System, the
   software recruiters use to parse resumes into a database; multi-column
   layouts and tables often scramble that parsing).
3. PDF and DOCX for the same template must **look the same** (same fonts,
   colours, sizes, margins) and must not drift apart as templates are edited.
4. The `CV` data model becomes **structured enough to lay out**: contact
   details as separate fields, education as degree/institution/dates, projects
   as name/description/link, and skills grouped by category. The issue
   explicitly allows refactoring the parsing mechanism for this.
5. Remove the duplicated, un-wired output code
   (`sira/utils/resume_output_converter.py`).

## 2. Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Outputs that get styled | PDF and DOCX. Markdown stays plain text | Markdown has no styling power; it stays the human-readable source and the debug artefact. |
| Data model | Restructured (`ContactInfo`, `Education`, `Project`, `SkillGroup`) | A styled header and education line cannot be laid out from free text. Approved by the issue author. |
| Templates | Three built-in: `modern`, `classic`, `compact` | Requested. A user-supplied CSS file was rejected because DOCX could not honour it and the two outputs would diverge. |
| Layout | Single column only, no tables | ATS safety. Templates differ by typography, colour and spacing. |
| How PDF and DOCX stay in sync | One `TemplateSpec` dataclass per template. A CSS generator reads it for the PDF; a python-docx styler reads it for the DOCX | Adding a template is adding one spec. The two renderers cannot disagree on a number because there is only one number. Rejected: hand-written CSS + hand-written DOCX code per template (two artefacts to keep aligned by eye). |
| PDF engine | `pymupdf` (PyMuPDF) `Story` directly, HTML + CSS in, PDF out | Already installed (it is what `markdown-pdf` wraps). Pure wheel, no system libraries. Handles fonts, colours, borders, margins, page breaks. Rejected: WeasyPrint (needs pango/cairo system libs — install friction on macOS/Windows); keeping `markdown-pdf` (its `Section` accepts Markdown only, so we cannot attach CSS classes to the header/contact line). |
| HTML templating | Jinja2 (a template engine: a text file with `{{ placeholders }}` and `{% for %}` loops rendered against Python objects) | One HTML template shared by all styles, with semantic CSS classes. Already in the environment transitively; becomes a direct dependency. Autoescape on. |
| DOCX engine | `python-docx` (already a dependency), styles set programmatically from the `TemplateSpec` | No bundled `.docx` template files to maintain. Hyperlinks via a small OXML helper (python-docx has no public hyperlink API). |
| Paper size | A4, fixed | Style, not paper, is the issue's scope. `--paper letter` is listed as a follow-up. |
| Fonts | PDF: MuPDF built-in `sans-serif` (Helvetica) / `serif` (Times). DOCX: `Calibri` / `Georgia` | No font files to bundle; both sets exist on every Office/LibreOffice install. |
| Flat skills list | `CV.skills` becomes a read-only computed property (flattened, ordered, de-duplicated case-insensitively) | `cv_diff`, the skill matcher and the report keep working unchanged. |
| Cache invalidation | Bump `_PARSER_VERSION` in `sira/memory/parser.py` | The memory service already re-parses when the stored `parser_version` differs. |
| Old tailored records in memory | Not migrated | Only `re-tailor`'s last-resort fallback reads a stored tailored CV, and that path already catches validation errors and prints a warning. |
| Where `--style` lives | CLI option passed to `_write_outputs`, **not** a DBOS workflow input | Rendering happens after the workflow returns. No change to `TailorInputs`, no DBOS versioning issue. |
| Failure handling | Markdown is written first. A PDF or DOCX failure raises `RenderError`; `_write_outputs` prints a warning for that format and continues. Exit code stays `0` when the Markdown was written | Today a PDF failure crashes the run after the model work is done; a DOCX failure is a printed warning. Unify on "warn and continue" — the tailoring result is never lost because of a rendering bug. |
| Console output | Renderer never prints. `_write_outputs` prints the saved paths | Keeps the renderer testable and reusable. |

## 3. Facts verified against the code

- `main.py:_write_outputs` (shared by `tailor`, `re-tailor`, `resume`) calls
  `markdown_writer.generate_resume`, which builds Markdown then writes `.md`,
  `.pdf` (`markdown_pdf.MarkdownPdf`, no CSS) and `.docx` (`Document()`,
  headings + "List Bullet" only). It prints the paths itself.
- `sira/utils/resume_output_converter.py` duplicates that builder and the
  converters behind an `OutputConverterRegistry`. Nothing in `sira/` imports
  it; only `tests/test_resume_output_converter.py` does. The two builders even
  disagree on where "Projects" goes.
- `CV` today: `full_name`, `contact_info: str`, `summary`, `skills:
  list[str]`, `projects: list[str]`, `experience: list[WorkExperience]`,
  `education: list[str]`, `certifications: list[str]`, `publications:
  list[str]`.
- Readers of `CV` fields outside the renderer: `sira/utils/cv_diff.py`
  (`.skills`, `.summary`, `.experience`), `sira/utils/skill_matching.py:
  render_cv_text` (`.skills`, `.experience`, `.projects`, `.education`,
  `.certifications`, `.publications`), `compute_gap_analysis` (keyword search
  over `tailored.model_dump_json()`), `main.py` (builds a fallback `CV(...,
  skills=[], education=[])` for filename patterns). The writer/auditor/
  reviewer agents receive the CV as JSON text and are schema-agnostic apart
  from prompt wording.
- `ResumeMemoryService` stores parsed CVs as JSON with `content_hash` and
  `parser_version`; a version mismatch triggers a re-parse
  (`sira/memory/service.py:129-160`).
- `pymupdf` 1.26.7 is installed; `pymupdf.Story(html=..., user_css=...)`
  plus `DocumentWriter` is exactly what `markdown_pdf.MarkdownPdf.add_section`
  does internally. Jinja2 is present only as a dependency of `commitizen`
  (dev group), so it must be added to the runtime dependencies.
- The `markdown` package is not used by the rendering path (only
  variable names called `markdown` are). It stays because other code may
  still need it; not touched by this change.
- 11 test files construct `CV(...)` with the old fields (`tests/factories.py`
  is the shared factory).

## 4. Data model (`sira/models/agents/output.py`)

```python
class ContactInfo(BaseModel):
    email: str = ""
    phone: str = ""
    location: str = Field(default="", description="City, country — as written")
    links: list[str] = Field(
        default_factory=list,
        description="LinkedIn, GitHub, website… keep markdown [text](url)",
    )


class Education(BaseModel):
    degree: str
    institution: str
    dates: str = ""
    details: str = Field(default="", description="Honours, GPA, thesis — optional")


class Project(BaseModel):
    name: str
    description: str
    link: str = Field(default="", description="URL or markdown link, optional")


class SkillGroup(BaseModel):
    category: str = Field(description='e.g. "Languages", "Cloud", "Soft skills"')
    skills: list[str]


class WorkExperience(BaseModel):      # unchanged
    company: str
    role: str
    dates: str
    highlights: list[str]


class CV(BaseModel):
    full_name: str
    contact: ContactInfo = Field(default_factory=ContactInfo)
    summary: str
    skill_groups: list[SkillGroup]
    experience: list[WorkExperience]
    education: list[Education]
    projects: list[Project] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)

    @property
    def skills(self) -> list[str]:
        """Flat skill list in group order, first spelling wins on
        case-insensitive duplicates. Not part of the schema or JSON."""
```

Rules:

- `contact_info: str` is **removed**, not aliased. The parser version bump
  makes old cached parses re-parse; there is no silent half-migration.
- `skills: list[str]` is **removed** from the schema. Code that read
  `cv.skills` keeps working through the property.
- The model is only ever built from LLM output or JSON, so no backwards-
  compatible validators are added.

### Prompt changes (`sira/workflows/agents.py`)

- **Parser**: extract `contact` fields (email, phone, location, links — links
  keep `[text](url)`); put every skill into exactly one `skill_groups` entry
  using 4–8 category names natural for the resume (examples: Languages,
  Frameworks & Libraries, Cloud & Infrastructure, Databases, Tools,
  Practices, Soft Skills); `education` as degree / institution / dates /
  details; `projects` as name / description / link. The existing "extract
  ALL skills, 40+ for a senior resume, preserve links" rules stay.
- **Writer**: rule 10 becomes: within each group, order the skills most
  relevant to the job first; groups may be reordered so the most relevant
  group comes first; do not rename, merge, split groups or move a skill
  between groups. Add: never change `contact` or `education` facts.
- **Auditor / Reviewer / Report**: no field names are mentioned in their
  prompts today; only verify that stays true.
- `_PARSER_VERSION` is bumped.

### Other readers

- `sira/utils/skill_matching.py:render_cv_text`: skills rendered per group
  (`Skills — Languages: Python, Go`), education as `Degree — Institution
  (dates). details`, projects as `Name — description`. Contact is not
  rendered (no skills live there).
- `main.py` fallback CV: `skill_groups=[]`.
- `tests/factories.py:make_cv` and every `CV(...)` in tests: new shape.

## 5. Rendering package (`sira/rendering/`)

Replaces `markdown_writer.generate_resume` (deleted) and
`utils/resume_output_converter.py` (deleted with its test).
`markdown_writer.generate_report_markdown` stays where it is.

```
sira/rendering/
├── __init__.py      render_resume(), RenderedResume, RenderError, STYLES
├── templates.py     TemplateSpec + the three specs + get_template(name)
├── inline.py        inline_markdown_to_html(), inline_markdown_to_runs()
├── html.py          render_html(cv, spec) -> str   (Jinja2, resume.html.j2)
├── css.py           build_css(spec) -> str
├── pdf.py           write_pdf(html, css, spec, path)
├── docx.py          write_docx(cv, spec, path)
├── markdown.py      render_markdown(cv) -> str
└── resume.html.j2   the single HTML structure (package data)
```

### 5.1 `TemplateSpec`

```python
@dataclass(frozen=True)
class TemplateSpec:
    name: str
    description: str                      # one line, shown in --help and docs
    pdf_font: str                         # "sans-serif" | "serif" (MuPDF built-ins)
    docx_font: str                        # "Calibri" | "Georgia"
    accent_hex: str                       # headings, name
    text_hex: str
    muted_hex: str                        # contact line, dates, details
    base_pt: float                        # body text
    name_pt: float
    heading_pt: float
    line_height: float                    # multiplier, e.g. 1.3
    margin_mm: float                      # all four page margins
    heading_style: Literal["rule", "caps"]
    section_gap_pt: float                 # space before each section heading
    item_gap_pt: float                    # space between entries / bullets
```

`heading_style`: `rule` = accent-coloured heading with a thin accent bottom
border; `caps` = uppercase, letter-spaced, text-coloured heading with a thin
text-coloured bottom border.

| name | fonts | accent | base / name / heading | margin | heading | gaps |
|---|---|---|---|---|---|---|
| `modern` (default) | sans-serif / Calibri | `#1F4E79` navy | 10.5 / 22 / 12 pt | 18 mm | rule | 10 / 4 pt |
| `classic` | serif / Georgia | `#000000` | 11 / 20 / 11 pt | 20 mm | caps | 10 / 4 pt |
| `compact` | sans-serif / Calibri | `#0B6E6E` teal | 9.5 / 18 / 10.5 pt | 14 mm | rule | 6 / 2 pt |

`STYLES = ("modern", "classic", "compact")`; `get_template(name)` raises
`ValueError` listing the valid names.

### 5.2 Document structure (shared by all outputs)

Section order and headings — an empty section is **omitted entirely**
(today the Markdown always prints Skills / Work Experience / Education even
when empty):

1. Header, left-aligned in every template: `full_name`; contact line =
   non-empty items of email, phone, location, links joined with ` · `.
2. **Summary**
3. **Skills** — one line per group: `Category:` in bold, then `a, b, c`.
4. **Experience** — per entry, one head line: role (bold) ` — ` company
   ` · ` dates (muted colour); then highlight bullets. Dates stay inline (no
   right-aligned floats or tables, so all three outputs agree and ATS
   parsing stays linear).
5. **Projects** — per entry: name (bold) ` — ` description, link appended
   as a hyperlink when present.
6. **Education** — per entry, one head line: degree (bold) ` — `
   institution ` · ` dates (muted); details on the next line when present.
7. **Certifications** — bullets.
8. **Publications** — bullets.

### 5.3 Inline markdown (`inline.py`)

The parser preserves `[text](url)`; resumes also carry `**bold**`,
`*italic*` and `` `code` ``. Support exactly that subset, nothing else.

- `inline_markdown_to_html(text) -> Markup`: HTML-escape first, then convert.
  Link `href` is only emitted for `http://`, `https://`, `mailto:`;
  anything else renders as plain text. Bare `https://…` URLs also become
  links.
- `inline_markdown_to_runs(text) -> list[Run]` where
  `Run(text, bold, italic, code, href)` — consumed by the DOCX renderer.

### 5.4 HTML + CSS → PDF (`html.py`, `css.py`, `pdf.py`)

- `resume.html.j2` uses semantic classes: `.name`, `.contact`, `.section`,
  `.section-title`, `.entry`, `.entry-head`, `.entry-sub`, `.dates`,
  `.skill-group`, `.details`, `ul.bullets`.
- `build_css(spec)` emits one stylesheet from the spec. `@page`-style margins
  are applied by the placement rectangle in `pdf.py` (MuPDF ignores
  `@page`), not by CSS.
- `write_pdf`: `pymupdf.Story(html=html, user_css=css)` +
  `pymupdf.DocumentWriter`; page `pymupdf.paper_rect("a4")`, placement rect
  inset by `margin_mm` (converted to points, 1 mm = 2.835 pt); loop
  `place`/`draw` until `more == 0`. Set PDF metadata `title = full_name`,
  `producer = "sira"`. Wrap any `pymupdf` exception in `RenderError`.

### 5.5 CV → DOCX (`docx.py`)

- Page: A4 (`section.page_width/height`), all margins `Mm(spec.margin_mm)`.
- Styles: `Normal` (docx_font, base_pt, text_hex, line spacing), `Title`
  (name_pt, accent, no Word "Title" underline border), `Heading 2`
  (heading_pt, accent or text colour per `heading_style`, bottom border via
  OXML `w:pBdr`, `all caps` + `character spacing` for `caps`), `List
  Bullet` (base_pt), a custom `Contact` paragraph style (muted colour,
  left-aligned), a custom `Dates` character style (muted).
- Header and contact line are plain paragraphs (no tables, no text boxes).
- Hyperlinks through a helper that adds `w:hyperlink` with a relationship id
  (standard OXML recipe). Bold/italic/code runs from `inline_markdown_to_runs`
  (code = Consolas).
- No `doc.add_heading(level=1)` for the name; use `Title` so Word's
  navigation pane shows sections at one level.
- Wrap any `python-docx` exception in `RenderError`.

### 5.6 CV → Markdown (`markdown.py`)

```markdown
# Jane Doe
jane@example.com · +1 555 0100 · Berlin · [LinkedIn](https://…) · [GitHub](https://…)

## Summary
…

## Skills
**Languages:** Python, Go
**Cloud:** AWS, Kubernetes

## Experience
### Senior Engineer — Acme Corp
*2020 – Present*
- bullet

## Projects
**Name** — description ([link](https://…))

## Education
**MSc Computer Science** — TU Berlin *(2016 – 2018)*
details

## Certifications
- …

## Publications
- …
```

Two-space line endings where a line break inside a paragraph is required.

### 5.7 Entry point

```python
@dataclass(frozen=True)
class RenderedResume:
    markdown: Path
    pdf: Path | None      # None when that format failed
    docx: Path | None
    errors: dict[str, RenderError]   # keyed "pdf" / "docx"


def render_resume(
    cv: CV, output_dir: Path, base_name: str, style: str = "modern"
) -> RenderedResume:
```

- Validates `base_name` has no path separator / `..` (same check as today,
  raises `ValueError`).
- Writes Markdown first (a failure here propagates — nothing useful was
  saved). Then PDF, then DOCX, each guarded; failures are collected in
  `errors` and the path is `None`.
- `_write_outputs` in `main.py` builds the `CV` from
  `result.tailored_resume` (already does this for the name), calls
  `render_resume`, prints `✅ Tailored CV saved to:` with the successful
  paths and `⚠️ Failed to write <fmt>: <error>` for each failure, and returns
  the Markdown path as `resume_path` (unchanged contract for callers).

## 6. CLI (`sira/main.py`)

- `class ResumeStyle(str, Enum): modern, classic, compact` (Typer renders the
  choices in `--help`).
- `--style` option, default `modern`, help text from each spec's
  `description`, on `tailor`, `re-tailor` and `resume`. All three forward it
  to `_write_outputs(…, style=…)`.
- `resume` (continue a run) accepts `--style` because rendering happens in
  the CLI, not in the checkpointed workflow, so a continued run can still
  pick a style.

## 7. Dependencies (`pyproject.toml`)

- Add `jinja2>=3.1`, `pymupdf>=1.26`.
- Remove `markdown-pdf` (its only caller, `utils/pdf_converter.py`, is
  deleted).
- `uv lock` after editing.

## 8. Testing (TDD, one module at a time; no model calls anywhere)

- `tests/rendering/test_templates.py` — the three specs exist, names match
  `STYLES`, `get_template("nope")` raises with the valid names in the
  message.
- `tests/rendering/test_inline.py` — link / bold / italic / code
  conversion; `javascript:` href is dropped; `<script>` in text is escaped;
  `inline_markdown_to_runs` round-trips the same cases.
- `tests/rendering/test_html.py` — rendered HTML contains the semantic
  classes, the contact items joined with `·`, group labels, and omits the
  `Projects` block when `projects` is empty; user text is escaped.
- `tests/rendering/test_css.py` — `build_css` contains the accent hex,
  font family, and base size for each spec; `caps` style emits
  `text-transform: uppercase`, `rule` does not.
- `tests/rendering/test_pdf.py` — writes a PDF for each style, reopens it
  with `pymupdf`, asserts page 1 text contains the name and email, page count
  ≥ 1, and the factory CV fits on one page in `compact`. Also: a
  `RenderError` is raised when the target directory is a file.
- `tests/rendering/test_docx.py` — reopens with `python-docx`, asserts:
  `Heading 2` colour equals the spec accent (rule) or text colour (caps),
  `Normal` font name equals `docx_font`, left margin equals `Mm(margin_mm)`,
  `len(doc.tables) == 0` (ATS guard), first paragraph style is `Title` with
  the name, a hyperlink relationship exists when a link is present.
- `tests/rendering/test_markdown.py` — golden string for the factory CV;
  empty optional sections are omitted; the flat `Skills` header is gone.
- `tests/rendering/test_render_resume.py` — writes all three files;
  `base_name` with `..` raises `ValueError`; a monkeypatched failing
  `write_docx` yields `docx=None`, an entry in `errors`, and Markdown + PDF
  still written.
- `tests/test_cli_typer.py` — `--style classic` reaches `render_resume`
  (monkeypatched) on `tailor`, `re-tailor` and `resume`; invalid style
  exits 2 with Typer's choice error; `_write_outputs` prints a warning and
  returns exit code `0` when `errors` is non-empty.
- `tests/memory/test_service.py` — a stored parse with the previous
  `parser_version` is re-parsed (already covered generically; add the
  explicit version-bump case).
- `tests/test_skill_matching_utils.py` — `render_cv_text` shows group
  labels and structured education/projects.
- `tests/test_cv_diff.py`, `tests/test_semantic_match_regression.py`,
  `tests/factories.py`, and every other `CV(...)` in `tests/` — updated to
  the new shape; behaviour assertions unchanged.
- Delete `tests/test_resume_output_converter.py` and the
  `generate_resume` cases in `tests/test_markdown_writer.py` (keep the
  report cases).

## 9. Documentation

- `docs/output.md` — "Styles" section: the three templates (one line each,
  ATS-safe statement), `--style`, sample of the new Markdown layout; the
  "same content in three formats" sentence is reworded (PDF and DOCX are now
  rendered from the structured CV, not converted from Markdown).
- `docs/cli.md` — `--style` row for `tailor`, `re-tailor`, `resume`.
- `README.md` — output section and option table.
- `ARCHITECTURE.md` — `CV` field table (lines 61 and 132), new
  `sira/rendering/` package in the layout tree, `markdown-pdf` → `pymupdf`.
- `AGENTS.md` and `.github/copilot-instructions.md` — data-model index
  entries for `CV` and the new sub-models.
- `CHANGELOG.md` — `feat: styled resume templates (--style modern|classic|
  compact)` with a `BREAKING CHANGE` note: the `CV` JSON schema changed;
  cached parses re-parse automatically; stored tailored records from earlier
  versions cannot be reused by `re-tailor`'s fallback path.
- `CLAUDE.md` — replace the `markdown_writer.py`/`resume_output_converter`
  mention with `sira/rendering/`.

## 10. Out of scope (follow-ups, not in this change)

- `--paper letter` (A4 only for now).
- User-supplied CSS or DOCX template files.
- A two-column template.
- A `sira styles` command (the choices already appear in `--help`).
- Migrating pre-existing tailored records in `resume_memory.sqlite3`.
- A cover-letter renderer (`cover_letter_writer_agent` is still not wired
  into the workflow).
