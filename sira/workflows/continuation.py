"""Continue a stored run: resume when DBOS can, fork from the right step otherwise.

DBOS facts this relies on (verified on dbos 2.31):
- ``resume_workflow`` only acts on runs that never finished; it is a no-op
  for SUCCESS and ERROR.
- ``fork_workflow(id, start_step)`` creates a new run that copies every
  checkpointed step before ``start_step`` and executes from there.
- Child workflow ids derive from the parent id, so forking a parent from the
  step that started a failed child gives that child a fresh id and re-runs it.
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS, WorkflowHandleAsync, WorkflowStatus

from sira.workflows import CHECKPOINT_STEP_NAME, UserAbortedError

# Statuses DBOS can resume in place (same run id).
RESUMABLE_STATUSES = frozenset(
    {"PENDING", "ENQUEUED", "DELAYED", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}
)


def fork_start_step(steps: list[dict[str, Any]], *, aborted: bool) -> int:
    """Return the 1-based step a fork should start from.

    - Aborted run (the user chose "quit"): the last checkpoint step, so the
      question is asked again.
    - A step recorded an error: the earliest such step. When that step is a
      child-result step, the fork must start at the step that *started* the
      child, or the copied child id would fail again.
    - No step recorded an error (the workflow raised between steps): the step
      after the last completed one.
    """
    if aborted:
        checkpoints = [
            s["function_id"]
            for s in steps
            if s["function_name"] == CHECKPOINT_STEP_NAME
        ]
        if checkpoints:
            return checkpoints[-1]
    failed_children = {
        s.get("child_workflow_id")
        for s in steps
        if s.get("error") is not None and s.get("child_workflow_id")
    }
    candidates = [
        s["function_id"]
        for s in steps
        if s.get("error") is not None or s.get("child_workflow_id") in failed_children
    ]
    if candidates:
        return min(candidates)
    return max((s["function_id"] for s in steps), default=0) + 1


async def continue_run(status: WorkflowStatus) -> WorkflowHandleAsync[Any]:
    """Resume or fork the run described by ``status``; return the handle to await.

    A forked run has a *new* workflow id (``handle.workflow_id``).
    Raises ValueError when there is nothing to continue.
    """
    if status.status in RESUMABLE_STATUSES:
        return await DBOS.resume_workflow_async(status.workflow_id)
    if status.status == "ERROR":
        steps = await DBOS.list_workflow_steps_async(status.workflow_id)
        aborted = isinstance(status.error, UserAbortedError)
        return await DBOS.fork_workflow_async(
            status.workflow_id, fork_start_step(steps, aborted=aborted)
        )
    raise ValueError(
        f"run {status.workflow_id} is {status.status}; nothing to continue"
    )
