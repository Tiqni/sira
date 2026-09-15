"""Tests for _tailor_impl memory service integration and error handling.

Coverage:
- Successful workflow run → result persisted via memory service
- Failed audit → record still persisted (for re-tailoring), resume file NOT generated
- Save failure → graceful handling (warning, not crash)
- Cache hit: pre-parsed CV reused when available
- Cache miss / error → falls back to AI parsing
- Invalid URL rejected early
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer import Exit as TyperExit

from sira.tools.job_scraper import RawScrape
from tests.factories import make_cv, make_result

pytestmark = pytest.mark.anyio

CLEANED_JOB_MD = (
    "# Senior Software Engineer\n\nRequirements: Python, distributed systems."
)


# ---------------------------------------------------------------------------
# Mock setup
# ---------------------------------------------------------------------------


def _setup_mocks(
    *,
    cv=None,
    passed: bool = True,
    resolve_side_effect=None,
    save_side_effect=None,
    scraper_side_effect=None,
):
    """Patch all external collaborators needed by _tailor_impl()."""
    cv = cv or make_cv()
    workflow_result = make_result(cv=cv, passed=passed)

    mock_workflow = MagicMock()
    mock_workflow.run = AsyncMock(return_value=workflow_result)

    mock_generate_resume = MagicMock(return_value="/fake/output/resume.md")

    mock_fetch = AsyncMock(
        return_value=RawScrape(
            markdown_raw="raw body markdown",
            source_text="<html>...</html>",
            extraction_strategy="markitdown",
        )
    )

    if scraper_side_effect is not None:
        mock_scraper_run = AsyncMock(side_effect=scraper_side_effect)
    else:
        mock_scraper_run = AsyncMock(return_value=MagicMock(output=CLEANED_JOB_MD))

    mock_repo = MagicMock()
    mock_parser = MagicMock()
    mock_svc = MagicMock()

    resolved = MagicMock()
    resolved.source = MagicMock(id="src-123")
    resolved.cv = cv

    if resolve_side_effect is not None:
        mock_svc.aresolve_original_resume = AsyncMock(side_effect=resolve_side_effect)
    else:
        mock_svc.aresolve_original_resume = AsyncMock(return_value=resolved)

    if save_side_effect is not None:
        mock_svc.save_tailored_resume = MagicMock(side_effect=save_side_effect)
    else:
        mock_svc.save_tailored_resume = MagicMock(return_value=MagicMock(id="job-456"))

    mocks = {
        "workflow": mock_workflow,
        "generate_resume": mock_generate_resume,
        "fetch": mock_fetch,
        "scraper_run": mock_scraper_run,
        "service": mock_svc,
    }

    patches = [
        patch("sira.main.fetch_job_markdown", mock_fetch),
        patch("sira.main.job_scraper_agent.run", mock_scraper_run),
        patch(
            "sira.main.ResumeTailorWorkflow",
            return_value=mock_workflow,
        ),
        patch("sira.main.generate_resume", mock_generate_resume),
        patch(
            "sira.main.SQLiteResumeMemoryRepository",
            return_value=mock_repo,
        ),
        patch(
            "sira.main.PydanticAIResumeParser",
            return_value=mock_parser,
        ),
        patch(
            "sira.main.ResumeMemoryService",
            return_value=mock_svc,
        ),
    ]

    return patches, mocks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tailor_impl_persists_result_on_success(
    tmp_path, monkeypatch, subtests
) -> None:
    """_tailor_impl() with valid inputs should save to memory on success."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    patches, mocks = _setup_mocks()

    from sira.main import _tailor_impl

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        exit_code = await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )

    with subtests.test("exits zero"):
        assert exit_code == 0

    with subtests.test("workflow.run called"):
        mocks["workflow"].run.assert_called_once()

    with subtests.test("generate_resume called"):
        mocks["generate_resume"].assert_called_once()

    with subtests.test("save_tailored_resume called"):
        mocks["service"].save_tailored_resume.assert_called_once()


@pytest.mark.anyio
async def test_tailor_impl_failed_audit_persists_record(tmp_path, monkeypatch) -> None:
    """Save record even when audit fails (for re-tailoring), but skip resume file."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    patches, mocks = _setup_mocks(passed=False)

    from sira.main import _tailor_impl

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        exit_code = await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )

    assert exit_code == 0
    mocks["workflow"].run.assert_called_once()
    mocks["service"].save_tailored_resume.assert_called_once()
    mocks["generate_resume"].assert_not_called()


@pytest.mark.anyio
async def test_tailor_impl_save_failure_handled_gracefully(
    tmp_path, monkeypatch
) -> None:
    """Warn and continue when save_tailored_resume fails."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    patches, mocks = _setup_mocks(save_side_effect=Exception("disk full"))

    from sira.main import _tailor_impl

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        exit_code = await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )

    assert exit_code == 0
    mocks["workflow"].run.assert_called_once()
    mocks["generate_resume"].assert_called_once()
    mocks["service"].save_tailored_resume.assert_called_once()


@pytest.mark.anyio
async def test_tailor_impl_cache_hit_reuses_pre_parsed_cv(
    tmp_path, monkeypatch
) -> None:
    """When memory service returns a cached CV, pass it to the workflow."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    cv = make_cv(full_name="Cached Jane")
    patches, mocks = _setup_mocks(cv=cv)

    from sira.main import _tailor_impl

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )

    call_kwargs = mocks["workflow"].run.call_args.kwargs
    assert call_kwargs.get("pre_parsed_cv") == cv


@pytest.mark.anyio
async def test_tailor_impl_cache_miss_falls_back_to_ai_parsing(
    tmp_path, monkeypatch
) -> None:
    """When memory service raises, pre_parsed_cv should be None (fallback to AI)."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    patches, mocks = _setup_mocks(resolve_side_effect=Exception("DB connection lost"))

    from sira.main import _tailor_impl

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )

    mocks["workflow"].run.assert_called_once()
    call_kwargs = mocks["workflow"].run.call_args.kwargs
    assert call_kwargs.get("pre_parsed_cv") is None


@pytest.mark.anyio
async def test_tailor_impl_invalid_url_exits(tmp_path, monkeypatch) -> None:
    """Reject non-http(s) URLs with TyperExit."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    from sira.main import _tailor_impl

    with pytest.raises(TyperExit):
        await _tailor_impl(
            job_url="not-a-url",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )


@pytest.mark.anyio
async def test_tailor_impl_empty_resume_content_exits(tmp_path, monkeypatch) -> None:
    """Reject empty resume content with TyperExit."""
    resume_file = tmp_path / "resume.md"
    resume_file.write_text("")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.chdir(tmp_path)

    from sira.main import _tailor_impl

    with pytest.raises(TyperExit):
        await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
        )


@pytest.mark.anyio
async def test_tailor_impl_interactive_flag_wired_through(
    tmp_path, monkeypatch
) -> None:
    """_tailor_impl(..., interactive=True) passes interactive=True to ResumeTailorWorkflow."""
    from unittest.mock import AsyncMock, MagicMock, patch as _patch

    resume_file = tmp_path / "resume.md"
    resume_file.write_text("# Jane Doe\nPython developer.")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    from tests.factories import make_cv, make_result

    cv = make_cv()
    workflow_result = make_result(cv=cv, passed=True)

    captured_kwargs = {}

    class CapturingWorkflow:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

        run = AsyncMock(return_value=workflow_result)

    with (
        _patch(
            "sira.main.fetch_job_markdown",
            AsyncMock(
                return_value=RawScrape(
                    markdown_raw="raw body markdown",
                    source_text="<html>...</html>",
                    extraction_strategy="markitdown",
                )
            ),
        ),
        _patch(
            "sira.main.job_scraper_agent.run",
            AsyncMock(return_value=MagicMock(output=CLEANED_JOB_MD)),
        ),
        _patch("sira.main.ResumeTailorWorkflow", CapturingWorkflow),
        _patch(
            "sira.main.generate_resume",
            MagicMock(return_value="/fake/resume.md"),
        ),
        _patch("sira.main.SQLiteResumeMemoryRepository", MagicMock()),
        _patch("sira.main.PydanticAIResumeParser", MagicMock()),
        _patch(
            "sira.main.ResumeMemoryService",
            MagicMock(
                return_value=MagicMock(
                    aresolve_original_resume=AsyncMock(
                        return_value=MagicMock(source=MagicMock(id="s"), cv=cv)
                    ),
                    save_tailored_resume=MagicMock(return_value=MagicMock(id="j")),
                )
            ),
        ),
    ):
        from sira.main import _tailor_impl

        await _tailor_impl(
            job_url="https://example.com/job/123",
            resume_path=str(resume_file),
            output_dir=str(output_dir),
            model=None,
            interactive=True,
        )

    assert captured_kwargs.get("interactive") is True


def test_print_report_to_console_escapes_rich_markup_in_skill_lines(capsys):
    """A skill name or CV-quoted evidence containing "[...]" must not crash
    Rich's markup parser or silently swallow the bracketed text."""
    from sira.main import _print_report_to_console
    from sira.models.agents.output import CVDiff, FinalReport, GapAnalysis

    report = FinalReport(
        job_title="Engineer",
        company_name="Acme",
        generated_at="2026-01-01T00:00:00Z",
        overall_recommendation="Strong Match",
        match_score=90,
        what_changed=CVDiff(),
        gaps=GapAnalysis(
            covered_hard_skills=["[Scripting]"],
            skill_evidence={"[Scripting]": "Wrote [docs](url) and a [/bad] tag"},
        ),
        audit_summary="ok",
        recommendation_rationale="ok",
        passed=True,
    )

    _print_report_to_console(report)  # must not raise rich.errors.MarkupError

    out = capsys.readouterr().out
    assert "[Scripting]" in out
    assert "[/bad] tag" in out


def test_open_memory_service_moves_a_legacy_database_and_says_so(
    tmp_path, monkeypatch, capsys
):
    """A ``./memory`` database from before the data dir is moved on first open."""
    from sira import paths
    from sira.main import _open_memory_service
    from sira.memory.sqlite_repository import SQLiteResumeMemoryRepository

    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "memory" / "resume_memory.sqlite3"
    SQLiteResumeMemoryRepository(db_path=legacy).close()

    repo, service = _open_memory_service()

    assert not legacy.exists()
    assert paths.memory_db_path().is_file()
    assert service._repo is repo
    # Rich wraps long lines at the terminal width; compare without line breaks.
    printed = capsys.readouterr().out.replace("\n", "")
    assert "Moved the resume memory" in printed
    assert str(paths.memory_db_path()) in printed


def test_open_memory_service_is_quiet_without_a_legacy_database(
    tmp_path, monkeypatch, capsys
):
    from sira.main import _open_memory_service

    monkeypatch.chdir(tmp_path)

    _open_memory_service()

    assert capsys.readouterr().out == ""


def test_is_memory_job_id_finds_a_job_stored_in_a_legacy_database(
    tmp_path, monkeypatch
):
    """`sira resume <id>` is a realistic first command after upgrading.

    DBOS does not know the id (its database moved too), so `_resume_impl` asks
    the memory whether the id is a Job ID. That lookup must migrate the legacy
    database instead of creating an empty one that blocks the migration forever.
    """
    from sira import paths
    from sira.main import _is_memory_job_id
    from sira.memory.sqlite_repository import SQLiteResumeMemoryRepository

    monkeypatch.chdir(tmp_path)
    legacy = SQLiteResumeMemoryRepository(db_path=paths.LEGACY_MEMORY_DB_PATH)
    source = legacy.upsert_original_source(
        path="/r/a.md", content_hash="h1", is_active=True
    )
    job = legacy.save_tailored_resume(
        source_id=source.id,
        job_fingerprint="fp",
        company_name="Acme",
        job_title="Engineer",
        tailored_cv_json="{}",
        audit_report_json="{}",
    )
    legacy.close()

    assert _is_memory_job_id(job.id) is True
    assert not paths.LEGACY_MEMORY_DB_PATH.exists()
    assert paths.memory_db_path().is_file()
