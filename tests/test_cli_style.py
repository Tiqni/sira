"""`--style` reaches render_resume on every command that writes files."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from sira.main import ResumeStyle, _write_outputs, app
from sira.rendering import RenderedResume, RenderError, STYLES
from tests.factories import make_result

runner = CliRunner()


def _fake_rendered(directory: Path, errors=None) -> RenderedResume:
    return RenderedResume(
        markdown=directory / "r.md",
        pdf=directory / "r.pdf",
        docx=None if errors else directory / "r.docx",
        errors=errors or {},
    )


def test_resume_style_enum_matches_registered_styles():
    assert tuple(style.value for style in ResumeStyle) == STYLES


def test_write_outputs_passes_style_and_reports_paths(tmp_path: Path, capsys):
    render = MagicMock(return_value=_fake_rendered(tmp_path))
    with patch("sira.main.render_resume", render):
        code, resume_path, _ = _write_outputs(
            make_result(),
            resume_content="# Jane",
            output_dir=str(tmp_path),
            output_pattern="{company_name}",
            resume_name_pattern="{full_name}",
            debug=False,
            style="compact",
        )
    assert code == 0 and resume_path == str(tmp_path / "r.md")
    cv, out_dir, base_name = render.call_args.args
    assert cv.full_name == "Jane Doe"
    assert out_dir == Path(tmp_path / "acme_corp") and base_name == "jane_doe"
    assert render.call_args.kwargs == {"style": "compact"}
    out = capsys.readouterr().out  # Rich wraps long paths, so match the file name only
    assert "Tailored CV saved to" in out and "r.pdf" in out


def test_write_outputs_warns_on_format_failure_but_exits_zero(tmp_path: Path, capsys):
    errors = {"docx": RenderError("Failed to write DOCX: boom")}
    with patch(
        "sira.main.render_resume",
        MagicMock(return_value=_fake_rendered(tmp_path, errors)),
    ):
        code, _, _ = _write_outputs(
            make_result(),
            resume_content="# Jane",
            output_dir=str(tmp_path),
            output_pattern="{company_name}",
            resume_name_pattern="{full_name}",
            debug=False,
        )
    assert code == 0
    assert "Failed to write docx: Failed to write DOCX: boom" in capsys.readouterr().out


def test_write_outputs_invalid_cv_json_exits_one_without_rendering(tmp_path: Path):
    result = make_result().model_copy(update={"tailored_resume": "{not json"})
    render = MagicMock()
    with patch("sira.main.render_resume", render):
        code, resume_path, _ = _write_outputs(
            result,
            resume_content="# Jane",
            output_dir=str(tmp_path),
            output_pattern="{company_name}",
            resume_name_pattern="{full_name}",
            debug=False,
        )
    assert code == 1 and resume_path is None
    render.assert_not_called()


def test_tailor_rejects_unknown_style(tmp_path: Path):
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane")
    result = runner.invoke(
        app, ["tailor", "https://example.com/j", str(resume_file), "--style", "fancy"]
    )
    assert result.exit_code == 2
    assert "fancy" in result.output


def test_tailor_forwards_style_to_write_outputs(tmp_path: Path):
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane")
    with (
        patch("sira.main._tailor_impl", AsyncMock(return_value=0)) as impl,
        patch("sira.main.durable_runtime"),
    ):
        result = runner.invoke(
            app,
            ["tailor", "https://example.com/j", str(resume_file), "--style", "classic"],
        )
    assert result.exit_code == 0, result.output
    assert impl.call_args.kwargs["style"] == "classic"


def test_re_tailor_and_resume_forward_style():
    with (
        patch("sira.main._re_tailor_impl", AsyncMock(return_value=0)) as re_impl,
        patch("sira.main.durable_runtime"),
    ):
        result = runner.invoke(
            app, ["re-tailor", "job-1", "more Python", "--style", "compact"]
        )
    assert result.exit_code == 0, result.output
    assert re_impl.call_args.kwargs["style"] == "compact"

    with (
        patch("sira.main._resume_impl", AsyncMock(return_value=0)) as res_impl,
        patch("sira.main.durable_runtime"),
    ):
        result = runner.invoke(app, ["resume", "run-1", "--style", "classic"])
    assert result.exit_code == 0, result.output
    assert res_impl.call_args.kwargs["style"] == "classic"
