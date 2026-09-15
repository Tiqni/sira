"""HTML + CSS → PDF through PyMuPDF's ``Story`` layout engine.

This is the same loop ``markdown-pdf`` runs internally, minus its Markdown
step, so we can feed our own HTML classes. Links are added after layout with
``Story.add_pdf_links`` from the recorded element positions.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf

from sira.rendering.errors import RenderError
from sira.rendering.templates import TemplateSpec

_PT_PER_MM = 72 / 25.4


def write_pdf(
    html: str,
    css: str,
    spec: TemplateSpec,
    output_path: Path,
    *,
    title: str = "",
) -> None:
    """Lay ``html`` out on A4 pages with ``spec.margin_mm`` margins and save it.

    Raises:
        RenderError: on any PyMuPDF or filesystem failure.
    """
    try:
        story = pymupdf.Story(html=html, user_css=css)
        media_box = pymupdf.paper_rect("a4")
        margin = spec.margin_mm * _PT_PER_MM
        where = media_box + (margin, margin, -margin, -margin)

        buffer = io.BytesIO()
        positions: list = []
        page_num = 0

        def record(elpos) -> None:
            elpos.page_num = page_num
            positions.append(elpos)

        with pymupdf.DocumentWriter(buffer) as writer:
            more = 1
            while more:
                page_num += 1
                device = writer.begin_page(media_box)
                more, _ = story.place(where)
                story.element_positions(record, {})
                story.draw(device)
                writer.end_page()

        buffer.seek(0)
        doc = pymupdf.Story.add_pdf_links(buffer, positions)
        try:
            doc.set_metadata({"title": title, "producer": "sira"})
            output_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(output_path))
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 — wrap every engine/IO failure
        raise RenderError(f"Failed to write PDF: {exc}") from exc
