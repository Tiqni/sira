"""Which DBOS step a fork must start from, for each failure shape."""

import pytest

from sira.workflows import CHECKPOINT_STEP_NAME
from sira.workflows.continuation import RESUMABLE_STATUSES, fork_start_step


def _step(fid, name, *, error=None, child=None):
    return {
        "function_id": fid,
        "function_name": name,
        "error": error,
        "child_workflow_id": child,
        "output": None,
    }


def test_step_error_forks_from_that_step():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "sira.analyze_job", child="p-2"),
        _step(3, "DBOS.getResult", child="p-1"),
        _step(4, "DBOS.getResult", child="p-2"),
        _step(5, "sira.writer__model.request_stream", error=RuntimeError("x")),
    ]
    assert fork_start_step(steps, aborted=False) == 5


def test_failed_child_forks_from_the_childs_start_step():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "sira.analyze_job", child="p-2"),
        _step(3, "DBOS.getResult", child="p-1"),
        _step(4, "DBOS.getResult", child="p-2", error=ValueError("analyst died")),
    ]
    assert fork_start_step(steps, aborted=False) == 2


def test_workflow_level_error_forks_after_the_last_completed_step():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "DBOS.getResult", child="p-1"),
    ]
    assert fork_start_step(steps, aborted=False) == 3


def test_aborted_run_forks_at_the_last_checkpoint():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "DBOS.getResult", child="p-1"),
        _step(3, CHECKPOINT_STEP_NAME),
        _step(4, "sira.writer__model.request_stream"),
        _step(5, CHECKPOINT_STEP_NAME),
    ]
    assert fork_start_step(steps, aborted=True) == 5


def test_aborted_without_checkpoint_falls_back_to_error_rule():
    steps = [_step(1, "sira.parse_resume", child="p-1")]
    assert fork_start_step(steps, aborted=True) == 2


def test_no_steps_forks_from_the_beginning():
    assert fork_start_step([], aborted=False) == 1


@pytest.mark.parametrize(
    "status", ["PENDING", "ENQUEUED", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"]
)
def test_resumable_statuses(status):
    assert status in RESUMABLE_STATUSES


def test_terminal_statuses_are_not_resumable():
    assert "SUCCESS" not in RESUMABLE_STATUSES
    assert "ERROR" not in RESUMABLE_STATUSES
