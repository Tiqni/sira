from pathlib import Path

import pytest

from sira.rendering import DEFAULT_STYLE, RenderedResume, RenderError, render_resume
from tests.factories import make_cv


def test_writes_all_three_files(tmp_path: Path):
    rendered = render_resume(make_cv(), tmp_path / "out", "acme-jane", style="classic")
    assert rendered == RenderedResume(
        markdown=tmp_path / "out" / "acme-jane.md",
        pdf=tmp_path / "out" / "acme-jane.pdf",
        docx=tmp_path / "out" / "acme-jane.docx",
        errors={},
    )
    assert all(path.exists() for path in rendered.written)
    assert DEFAULT_STYLE == "modern"


@pytest.mark.parametrize("bad", ["a/b", "..", "x..y", ""])
def test_rejects_unsafe_base_names(tmp_path: Path, bad: str):
    with pytest.raises(ValueError):
        render_resume(make_cv(), tmp_path, bad)


def test_unknown_style_raises_before_writing(tmp_path: Path):
    with pytest.raises(ValueError, match="Valid styles"):
        render_resume(make_cv(), tmp_path, "r", style="fancy")
    assert not (tmp_path / "r.md").exists()


def test_docx_failure_is_collected_and_other_files_still_written(tmp_path, monkeypatch):
    def boom(cv, spec, output_path):
        raise RenderError("Failed to write DOCX: boom")

    monkeypatch.setattr("sira.rendering.write_docx", boom)
    rendered = render_resume(make_cv(), tmp_path, "r")
    assert rendered.docx is None
    assert "boom" in str(rendered.errors["docx"])
    assert (
        rendered.markdown.exists()
        and rendered.pdf is not None
        and rendered.pdf.exists()
    )
    assert rendered.written == [rendered.markdown, rendered.pdf]


def test_html_rendering_failure_is_collected_as_pdf_error(tmp_path, monkeypatch):
    def boom(cv, spec):
        raise RuntimeError("template broke")

    monkeypatch.setattr("sira.rendering.render_html", boom)
    rendered = render_resume(make_cv(), tmp_path, "r")
    assert rendered.pdf is None
    assert "template broke" in str(rendered.errors["pdf"])
    assert isinstance(rendered.errors["pdf"], RenderError)
    assert (
        rendered.markdown.exists()
        and rendered.docx is not None
        and rendered.docx.exists()
    )
