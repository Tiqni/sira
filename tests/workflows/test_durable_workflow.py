"""End-to-end durability: replay, fork, checkpoint, steps, fallback reporter."""

import asyncio
import uuid

import pytest
from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from sira.models.workflow import ResumeTailorResult, TailorInputs
from sira.reporting.base import install_global_reporter, use_reporter
from sira.workflows import (
    ANALYZE_WORKFLOW_NAME,
    CHECKPOINT_STEP_NAME,
    PARSE_WORKFLOW_NAME,
    TAILOR_WORKFLOW_NAME,
    ResumeTailorWorkflow,
    UserAbortedError,
)
from sira.workflows.agents import _durability, run_agent
from sira.workflows.continuation import continue_run
from tests.reporting.test_base import RecordingReporter
from tests.workflows.stubs import FakeStdin, install_pipeline_stubs


@pytest.mark.anyio
async def test_run_is_recorded_as_a_sira_tailor_workflow_with_two_children(
    monkeypatch, sample_cv
):
    install_pipeline_stubs(monkeypatch, sample_cv)
    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        result = await ResumeTailorWorkflow().run("# resume", job_content="job")
    assert result.passed is True

    handle = await DBOS.retrieve_workflow_async(run_id)
    status = await handle.get_status()
    assert status.status == "SUCCESS"
    assert status.name == TAILOR_WORKFLOW_NAME
    assert isinstance(status.input["args"][0], TailorInputs)
    assert isinstance(await handle.get_result(), ResumeTailorResult)  # stored output

    children = await DBOS.list_workflows_async(parent_workflow_id=run_id)
    assert {c.name for c in children} == {PARSE_WORKFLOW_NAME, ANALYZE_WORKFLOW_NAME}
    assert all(c.status == "SUCCESS" for c in children)


@pytest.mark.anyio
async def test_fork_after_writer_crash_replays_parser_and_analyst(
    monkeypatch, sample_cv
):
    calls = install_pipeline_stubs(monkeypatch, sample_cv, writer_fail_once=True)
    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(RuntimeError, match="simulated crash"):
            await ResumeTailorWorkflow().run("# resume", job_content="job")
    assert (calls["parser"], calls["analyst"], calls["writer"]) == (1, 1, 1)

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "ERROR"

    # The continued run executes on DBOS's background thread: only the
    # process-wide fallback reporter can see its progress.
    rec = RecordingReporter()
    install_global_reporter(rec)
    handle = await continue_run(status)
    assert handle.workflow_id != run_id  # a fork is a new run
    result = await handle.get_result()

    assert result.passed is True
    assert calls["parser"] == 1 and calls["analyst"] == 1  # replayed, not re-run
    assert calls["writer"] == 2
    assert ("stage_start", "WRITING_CV") in rec.events


@pytest.mark.anyio
async def test_aborted_run_is_asked_the_checkpoint_question_again(
    monkeypatch, sample_cv
):
    calls = install_pipeline_stubs(monkeypatch, sample_cv, audit_passed=False)
    monkeypatch.setattr("sys.stdin", FakeStdin(is_tty=True))
    answers = iter(["q", "c"])
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)

    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(UserAbortedError):
            await ResumeTailorWorkflow(interactive=True).run(
                "# resume", job_content="job"
            )
    assert len(asked) == 1

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "ERROR"
    assert isinstance(status.error, UserAbortedError)
    steps = await DBOS.list_workflow_steps_async(run_id)
    assert any(s["function_name"] == CHECKPOINT_STEP_NAME for s in steps)

    handle = await continue_run(status)
    result = await handle.get_result()
    assert len(asked) == 2  # forked at the checkpoint: asked once more
    assert result.passed is False
    assert calls["parser"] == 1 and calls["analyst"] == 1


# A throwaway durable agent inside a workflow: the model request itself must
# be a checkpointed step, and its tokens must stream to the reporter.
_probe_agent = Agent(
    TestModel(custom_output_text="probe says hi"),
    name="sira.test_durable",
    capabilities=[_durability()],
)


@DBOS.workflow(name="sira.test_agent_workflow")
async def _agent_workflow() -> str:
    result = await run_agent(_probe_agent, "hi", agent_label="Probe")
    return result.output


@pytest.mark.anyio
async def test_model_request_inside_workflow_is_a_step_and_streams():
    rec = RecordingReporter()
    rec.wants_tokens = True
    run_id = str(uuid.uuid4())
    with use_reporter(rec):
        with SetWorkflowID(run_id):
            output = await _agent_workflow()
    assert output == "probe says hi"

    steps = await DBOS.list_workflow_steps_async(run_id)
    names = [s["function_name"] for s in steps]
    assert any(n.startswith("sira.test_durable__model.request") for n in names), names

    tokens = [e for e in rec.events if e[0] == "token"]
    assert "".join(e[2] for e in tokens) == "probe says hi"
    assert {e[1] for e in tokens} == {"Probe"}


async def _wait_for_children(run_id: str, expected: dict[str, str]) -> None:
    """Children finish on their own tasks; wait until their statuses settle."""
    for _ in range(50):
        children = await DBOS.list_workflows_async(parent_workflow_id=run_id)
        if {c.name: c.status for c in children} == expected:
            return
        await asyncio.sleep(0.1)
    raise AssertionError({c.name: c.status for c in children})


@pytest.mark.anyio
async def test_interrupt_during_parsing_resumes_children_and_parent(
    monkeypatch, sample_cv
):
    """Ctrl+C in the first stage leaves a child PENDING; resume must not hang."""
    calls = install_pipeline_stubs(monkeypatch, sample_cv, parser_cancel_first=True)
    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(asyncio.CancelledError):
            await ResumeTailorWorkflow().run("# resume", job_content="job")

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "PENDING"
    await _wait_for_children(
        run_id, {PARSE_WORKFLOW_NAME: "PENDING", ANALYZE_WORKFLOW_NAME: "SUCCESS"}
    )

    install_global_reporter(RecordingReporter())
    handle = await continue_run(status)
    assert handle.workflow_id == run_id  # resumed in place
    result = await asyncio.wait_for(handle.get_result(), timeout=15)
    assert result.passed is True
    assert (calls["parser"], calls["analyst"], calls["writer"]) == (2, 1, 1)


@pytest.mark.anyio
async def test_interrupt_during_writing_resumes_without_rerunning_children(
    monkeypatch, sample_cv
):
    calls = install_pipeline_stubs(monkeypatch, sample_cv, writer_cancel_first=True)
    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(asyncio.CancelledError):
            await ResumeTailorWorkflow().run("# resume", job_content="job")

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "PENDING"
    await _wait_for_children(
        run_id, {PARSE_WORKFLOW_NAME: "SUCCESS", ANALYZE_WORKFLOW_NAME: "SUCCESS"}
    )

    install_global_reporter(RecordingReporter())
    handle = await continue_run(status)
    assert handle.workflow_id == run_id
    result = await asyncio.wait_for(handle.get_result(), timeout=15)
    assert result.passed is True
    assert (calls["parser"], calls["analyst"], calls["writer"]) == (1, 1, 2)


@pytest.mark.anyio
async def test_checkpoint_answer_is_not_asked_again_after_a_later_crash(
    monkeypatch, sample_cv
):
    """The answer given at a checkpoint is replayed, not re-prompted, on a fork."""
    calls = install_pipeline_stubs(
        monkeypatch, sample_cv, report_weak_first=True, auditor_fail_on_call=2
    )
    monkeypatch.setattr("sys.stdin", FakeStdin(is_tty=True))
    answers = iter(["f", "emphasize backend work"])
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)

    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(RuntimeError, match="simulated crash in auditor"):
            await ResumeTailorWorkflow(interactive=True).run(
                "# resume", job_content="job"
            )
    assert len(asked) == 2  # the choice and the feedback text
    assert calls["auditor"] == 2

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "ERROR"

    handle = await continue_run(status)
    assert handle.workflow_id != run_id  # forked after the checkpoint
    result = await asyncio.wait_for(handle.get_result(), timeout=15)
    assert result.passed is True
    assert len(asked) == 2  # replayed from the checkpoint step, not re-asked
