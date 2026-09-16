"""HTML + CSS → PDF through PyMuPDF's ``Story`` layout engine.

This is the same loop ``markdown-pdf`` runs internally, minus its Markdown
step, so we can feed our own HTML classes. Links are added after layout with
``Story.add_pdf_links`` from the recorded element positions.

Orphaned headings (issue #21): MuPDF ignores every ``*-after: avoid`` /
``*-inside: avoid`` CSS rule but honours ``page-break-before: always``. So
the layout runs once, any section heading whose body starts on a later page
is found from the recorded positions, a page break is forced before it, and
the layout runs again. One extra pass is enough in practice; the loop is
capped in case a forced break pushes another heading to a page bottom.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pymupdf

from sira.rendering.errors import RenderError
from sira.rendering.templates import TemplateSpec

_PT_PER_MM = 72 / 25.4
_SECTION_ID_PREFIX = "section-"
_BODY_ID_SUFFIX = "-body"
_MAX_LAYOUT_PASSES = 4


def orphaned_headings(positions: list[Any]) -> list[str]:
    """Ids of section headings whose body div opens on a later page.

    ``positions`` are PyMuPDF element positions with a ``page_num`` attached
    by the layout loop. Only "open" events (``open_close & 1``) of elements
    with an id are considered; link positions have no id and are skipped.
    """
    first_page: dict[str, int] = {}
    for pos in positions:
        if not pos.id or not (pos.open_close & 1):
            continue
        first_page.setdefault(pos.id, pos.page_num)
    orphans: list[str] = []
    for element_id, page in first_page.items():
        if not element_id.startswith(_SECTION_ID_PREFIX) or element_id.endswith(
            _BODY_ID_SUFFIX
        ):
            continue
        body_page = first_page.get(element_id + _BODY_ID_SUFFIX)
        if body_page is not None and body_page > page:
            orphans.append(element_id)
    return orphans


def _layout(
    html: str, css: str, media_box: pymupdf.Rect, where: pymupdf.Rect
) -> tuple[io.BytesIO, list[Any]]:
    """Run one full layout; return the PDF bytes and the element positions."""
    story = pymupdf.Story(html=html, user_css=css)
    buffer = io.BytesIO()
    positions: list[Any] = []
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
    return buffer, positions


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
        media_box = pymupdf.paper_rect("a4")
        margin = spec.margin_mm * _PT_PER_MM
        where = media_box + (margin, margin, -margin, -margin)

        forced_breaks: list[str] = []
        for _ in range(_MAX_LAYOUT_PASSES):
            break_css = "".join(
                f"#{element_id}{{page-break-before:always}}"
                for element_id in forced_breaks
            )
            buffer, positions = _layout(html, css + break_css, media_box, where)
            new_orphans = [
                element_id
                for element_id in orphaned_headings(positions)
                if element_id not in forced_breaks
            ]
            if not new_orphans:
                break
            forced_breaks.extend(new_orphans)

        doc = pymupdf.Story.add_pdf_links(buffer, positions)
        try:
            doc.set_metadata({"title": title, "producer": "sira"})
            output_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(output_path))
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 — wrap every engine/IO failure
        raise RenderError(f"Failed to write PDF: {exc}") from exc
