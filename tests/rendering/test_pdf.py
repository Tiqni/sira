from pathlib import Path

import pymupdf
import pytest

from sira.models.agents.output import ContactInfo
from sira.rendering.css import build_css
from sira.rendering.errors import RenderError
from sira.rendering.html import render_html
from sira.rendering.pdf import write_pdf
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
