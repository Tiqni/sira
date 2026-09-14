"""`sira runs` and `sira resume` against the real DBOS runtime (agents stubbed)."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from dbos import SetWorkflowID
from typer.testing import CliRunner

from sira.main import app
from sira.models.workflow import RunMetadata
from sira.workflows import ResumeTailorWorkflow
from tests.workflows.stubs import install_pipeline_stubs

runner = CliRunner()


def _memory_patches():
    service = MagicMock(
        aresolve_original_resume=AsyncMock(
            return_value=MagicMock(source=MagicMock(id="src"), cv=None)
        ),
        save_tailored_resume=MagicMock(return_value=MagicMock(id="job-1")),
    )
    return [
        patch("sira.main.generate_resume", MagicMock(return_value="/fake/resume.md")),
        patch("sira.main.SQLiteResumeMemoryRepository", MagicMock()),
        patch("sira.main.PydanticAIResumeParser", MagicMock()),
        patch("sira.main.ResumeMemoryService", MagicMock(return_value=service)),
    ]


def _start_run(tmp_path, sample_cv, monkeypatch, **stub_kwargs):
    """Run the stubbed pipeline once under a known run id; return (run_id, calls, exc)."""
    calls = install_pipeline_stubs(monkeypatch, sample_cv, **stub_kwargs)
    run_id = str(uuid.uuid4())
    metadata = RunMetadata(
        job_url="https://example.com/job/1",
        resume_source_path=str(tmp_path / "resume.md"),
        output_dir=str(tmp_path / "out"),
        job_posting_markdown="# job",
    )
    exc = None
    try:
        with SetWorkflowID(run_id):
            asyncio.run(
                ResumeTailorWorkflow().run(
                    "# resume", job_content="job", metadata=metadata
                )
            )
    except Exception as e:  # noqa: BLE001 — the crash we set up
        exc = e
    return run_id, calls, exc


def test_runs_lists_recent_runs(tmp_path, sample_cv, monkeypatch):
    run_id, _, exc = _start_run(tmp_path, sample_cv, monkeypatch)
    assert exc is None
    result = runner.invoke(app, ["runs", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert run_id[:8] in result.output
    assert "SUCCESS" in result.output
    assert "example.com/job/1" in result.output


def test_resume_of_completed_run_reuses_result_without_rerunning(
    tmp_path, sample_cv, monkeypatch
):
    run_id, calls, exc = _start_run(tmp_path, sample_cv, monkeypatch)
    assert exc is None
    before = dict(calls)
    patches = _memory_patches()
    for p in patches:
        p.start()
    try:
        result = runner.invoke(app, ["resume", run_id])
    finally:
        for p in patches:
            p.stop()
    assert result.exit_code == 0, result.output
    assert "already completed" in result.output
    assert calls == before  # nothing re-ran
    assert "Report saved to" in result.output


def test_resume_after_error_continues_from_the_last_checkpoint(
    tmp_path, sample_cv, monkeypatch
):
    run_id, calls, exc = _start_run(
        tmp_path, sample_cv, monkeypatch, writer_fail_once=True
    )
    assert isinstance(exc, RuntimeError)
    assert calls["writer"] == 1
    patches = _memory_patches()
    for p in patches:
        p.start()
    try:
        result = runner.invoke(app, ["resume", run_id])
    finally:
        for p in patches:
            p.stop()
    assert result.exit_code == 0, result.output
    assert "Continued as run" in result.output  # a fork has a new id
    assert calls["parser"] == 1 and calls["analyst"] == 1  # replayed
    assert calls["writer"] == 2


def test_resume_unknown_run_id_fails_cleanly():
    result = runner.invoke(app, ["resume", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 1
    assert "Unknown run id" in result.output


def test_resume_refuses_a_run_from_another_sira_version(
    tmp_path, sample_cv, monkeypatch
):
    run_id, _, _ = _start_run(tmp_path, sample_cv, monkeypatch)
    monkeypatch.setattr("sira.main.application_version", lambda: "9.9.9")
    result = runner.invoke(app, ["resume", run_id])
    assert result.exit_code == 1
    assert "9.9.9" in result.output
