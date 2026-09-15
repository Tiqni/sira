"""Render a tailored CV to Markdown, PDF and DOCX.

``render_resume`` writes the Markdown first (a failure there propagates —
nothing useful was saved), then the PDF and DOCX, each guarded: a failing
format is reported in ``RenderedResume.errors`` and its path is ``None``.
Nothing here prints; the CLI reports the paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from sira.models.agents.output import CV
from sira.rendering.css import build_css
from sira.rendering.docx import write_docx
from sira.rendering.errors import RenderError
from sira.rendering.html import render_html
from sira.rendering.markdown import render_markdown
from sira.rendering.pdf import write_pdf
from sira.rendering.templates import DEFAULT_STYLE, STYLES, TemplateSpec, get_template

__all__ = [
    "DEFAULT_STYLE",
    "STYLES",
    "RenderError",
    "RenderedResume",
    "TemplateSpec",
    "get_template",
    "render_resume",
]


@dataclass(frozen=True)
class RenderedResume:
    markdown: Path
    pdf: Path | None
    docx: Path | None
    errors: dict[str, RenderError] = field(default_factory=dict)

    @property
    def written(self) -> list[Path]:
        return [
            path for path in (self.markdown, self.pdf, self.docx) if path is not None
        ]


def _check_base_name(base_name: str) -> None:
    if not base_name or os.sep in base_name or (os.altsep and os.altsep in base_name):
        raise ValueError(
            f"base_name is empty or contains a path separator: {base_name!r}"
        )
    if ".." in base_name:
        raise ValueError(
            f"base_name contains a parent-directory reference: {base_name!r}"
        )


def render_resume(
    cv: CV, output_dir: Path, base_name: str, style: str = DEFAULT_STYLE
) -> RenderedResume:
    _check_base_name(base_name)
    spec = get_template(style)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = output_dir / f"{base_name}.md"
    markdown_path.write_text(render_markdown(cv), encoding="utf-8")

    errors: dict[str, RenderError] = {}

    pdf_path: Path | None = output_dir / f"{base_name}.pdf"
    try:
        write_pdf(
            render_html(cv, spec), build_css(spec), spec, pdf_path, title=cv.full_name
        )
    except RenderError as exc:
        errors["pdf"] = exc
        pdf_path = None
    except Exception as exc:  # noqa: BLE001 — a template bug must never lose the tailoring result
        errors["pdf"] = RenderError(f"Failed to write PDF: {exc}")
        pdf_path = None

    docx_path: Path | None = output_dir / f"{base_name}.docx"
    try:
        write_docx(cv, spec, docx_path)
    except RenderError as exc:
        errors["docx"] = exc
        docx_path = None

    return RenderedResume(
        markdown=markdown_path, pdf=pdf_path, docx=docx_path, errors=errors
    )
