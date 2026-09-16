import io
from pathlib import Path

import pymupdf
import pytest

from sira.models.agents.output import ContactInfo
from sira.rendering.css import build_css
from sira.rendering.errors import RenderError
from sira.rendering.html import render_html
from sira.rendering.pdf import orphaned_headings, write_pdf
from sira.rendering.templates import CLASSIC, COMPACT, MODERN, TemplateSpec
from tests.factories import make_cv


@pytest.mark.parametrize("spec", [MODERN, CLASSIC, COMPACT])
def test_pdf_contains_the_resume_text_on_a4(tmp_path: Path, spec: TemplateSpec):
    cv = make_cv()
    out = tmp_path / "r.pdf"
    write_pdf(render_html(cv, spec), build_css(spec), spec, out, title=cv.full_name)

    with pymupdf.open(out) as doc:
        assert doc.page_count == 1
        text = doc[0].get_text()
        assert "Jane Doe" in text and "jane@example.com" in text
        assert "Languages: Python, SQL" in text
        assert doc.metadata["title"] == "Jane Doe"
        assert doc.metadata["producer"] == "sira"
        assert round(doc[0].rect.width) == 595 and round(doc[0].rect.height) == 842


def test_pdf_links_are_clickable(tmp_path: Path):
    contact = ContactInfo(
        email="jane@example.com", links=["[GitHub](https://github.com/jane)"]
    )
    cv = make_cv().model_copy(update={"contact": contact})
    out = tmp_path / "r.pdf"
    write_pdf(render_html(cv, MODERN), build_css(MODERN), MODERN, out)
    with pymupdf.open(out) as doc:
        assert [link["uri"] for link in doc[0].get_links()] == [
            "https://github.com/jane"
        ]


def test_pdf_creates_missing_parent_directories(tmp_path: Path):
    out = tmp_path / "a" / "b" / "r.pdf"
    write_pdf("<p>x</p>", "", MODERN, out)
    assert out.exists()


def test_pdf_failure_raises_render_error(tmp_path: Path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(RenderError, match="PDF"):
        write_pdf("<p>x</p>", "", MODERN, blocker / "r.pdf")


# --- orphaned section headings (issue #21) --------------------------------


class _Pos:
    """Stand-in for a PyMuPDF ``ElementPosition``: only the fields we read."""

    def __init__(self, id: str, page_num: int, open_close: int = 1):
        self.id = id
        self.page_num = page_num
        self.open_close = open_close


def test_orphan_detector_flags_headings_whose_body_starts_on_a_later_page():
    positions = [
        _Pos("section-summary", 1),
        _Pos("section-summary-body", 1),
        _Pos("section-experience", 1),  # last thing on page 1 …
        _Pos("section-experience-body", 2),  # … content only on page 2
        _Pos("section-education", 2),
        _Pos("section-education-body", 2),
        _Pos("section-education", 2, open_close=2),  # close events are ignored
    ]
    assert orphaned_headings(positions) == ["section-experience"]


def test_orphan_detector_ignores_links_and_bodies_without_a_heading():
    positions = [_Pos("", 1), _Pos("section-projects-body", 1)]
    assert orphaned_headings(positions) == []


def _section_html(filler_lines: int) -> str:
    """Filler paragraphs, then a heading + body using the template's id scheme."""
    filler = "".join(f"<p>line {i}</p>" for i in range(filler_lines))
    return (
        filler
        + '<h2 class="section-title" id="section-experience">Experience</h2>'
        + '<div class="section-body" id="section-experience-body">'
        + "<p>first entry</p><p>second entry</p></div>"
    )


# The page boundary sits at ~40 filler lines for modern, ~38 for classic and
# ~52 for compact; this range crosses all three.
_SWEEP = range(34, 58)


@pytest.mark.parametrize("spec", [MODERN, CLASSIC, COMPACT])
@pytest.mark.parametrize("filler_lines", _SWEEP)
def test_section_heading_is_never_the_last_line_of_a_page(
    tmp_path: Path, spec: TemplateSpec, filler_lines: int
):
    # Sweeping the filler length crosses the page boundary, so at least one
    # value would leave "Experience" orphaned without the two-pass layout.
    out = tmp_path / "r.pdf"
    write_pdf(_section_html(filler_lines), build_css(spec), spec, out)
    with pymupdf.open(out) as doc:
        pages = [page.get_text().strip().splitlines() for page in doc]
    # The synthetic HTML is not uppercased (that is the template's job), so the
    # heading text is "Experience" for every style.
    assert all(lines[-1] != "Experience" for lines in pages if lines)
    heading_page = next(i for i, lines in enumerate(pages) if "Experience" in lines)
    # MuPDF's text layer ligates "fi"; accept both spellings.
    assert any(line in ("first entry", "ﬁrst entry") for line in pages[heading_page])


def _plain_layout_first_page(html: str, spec: TemplateSpec) -> list[str]:
    """Single-pass layout without the orphan fix; return page 1's text lines."""
    story = pymupdf.Story(html=html, user_css=build_css(spec))
    media = pymupdf.paper_rect("a4")
    margin = spec.margin_mm * 72 / 25.4
    where = media + (margin, margin, -margin, -margin)
    buffer = io.BytesIO()
    with pymupdf.DocumentWriter(buffer) as writer:
        more = 1
        while more:
            device = writer.begin_page(media)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    buffer.seek(0)
    with pymupdf.open("pdf", buffer) as doc:
        return doc[0].get_text().strip().splitlines()


@pytest.mark.parametrize("spec", [MODERN, CLASSIC, COMPACT])
def test_sweep_range_really_crosses_the_page_boundary(spec: TemplateSpec):
    # Guard for the sweep above: without the fix, the plain engine must orphan
    # the heading for at least one value in _SWEEP, or the sweep proves nothing.
    orphaned = [
        n
        for n in _SWEEP
        if (first := _plain_layout_first_page(_section_html(n), spec))
        and first[-1] == "Experience"
    ]
    assert orphaned, f"{spec.name}: sweep range no longer crosses the page boundary"


def test_long_link_text_wraps_inside_the_page_margins(tmp_path: Path):
    # A certification whose whole text is a link must wrap like plain text;
    # a nowrap link is clipped at the right margin (seen on a real resume).
    long_cert = (
        "[**Courses:** Supervised Machine Learning; Advanced Learning Algorithms "
        "— DeepLearning.AI. Senior Engineer to Lead — Maven, Sep 2025]"
        "(https://www.coursera.org/account/accomplishments/verify/KDJWHF4LHSL3)"
    )
    cv = make_cv().model_copy(update={"certifications": [long_cert]})
    out = tmp_path / "r.pdf"
    write_pdf(render_html(cv, MODERN), build_css(MODERN), MODERN, out)
    with pymupdf.open(out) as doc:
        page = doc[0]
        right_margin = page.rect.width - MODERN.margin_mm * 72 / 25.4
        words = page.get_text("words")  # (x0, y0, x1, y1, word, …)
        assert "Sep 2025" in page.get_text()
        assert all(w[2] <= right_margin + 1 for w in words), (
            "text drawn past the right margin"
        )
