"""Workflow contract tests: structured original CV input, no resume_parser_agent calls."""

import pytest

from sira.models.agents.output import (
    AuditResult,
    JobAnalysis,
    ReportNarrative,
    ReviewResult,
    SkillMatch,
    SkillMatchResult,
)
from sira.workflows import PipelineError, ResumeTailorWorkflow


class DummyRunResult:
    def __init__(self, output):
        self.output = output


class _FakeStdin:
    """stdin stub with a controllable ``isatty()``.

    Patch ``sys.stdin`` with this instead of ``sys.stdin.isatty`` directly —
    ``isatty`` is a read-only attribute on some Python builds, so setting it
    can raise.
    """

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def _patch_stdin(monkeypatch, *, is_tty: bool) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin(is_tty))


@pytest.mark.anyio
async def test_workflow_uses_provided_original_cv_without_reparsing(
    monkeypatch, sample_cv, subtests
) -> None:
    """When provided with a structured CV, the workflow runs end-to-end without errors."""

    async def run_parser(*args, **kwargs):
        return DummyRunResult(sample_cv)

    async def run_analyst(*args, **kwargs):
        return DummyRunResult(
            JobAnalysis(
                job_title="Platform Engineer",
                company_name="Acme",
                summary="Platform role",
                hard_skills=["Python"],
                soft_skills=["Communication"],
                key_responsibilities=["Build systems"],
                keywords_to_target=["Python", "Platform"],
            )
        )

    async def run_writer(*args, **kwargs):
        return DummyRunResult(sample_cv)

    async def run_reviewer(*args, **kwargs):
        return DummyRunResult(
            ReviewResult(
                quality_score=9,
                needs_improvement=False,
                specific_suggestions=[],
                strengths=["Good targeting"],
            )
        )

    async def run_auditor(*args, **kwargs):
        return DummyRunResult(
            AuditResult(
                passed=True,
                hallucination_score=0,
                ai_cliche_score=0,
                issues=[],
                feedback_summary="Looks good",
            )
        )

    monkeypatch.setattr("sira.workflows.agents.resume_parser_agent.run", run_parser)
    monkeypatch.setattr("sira.workflows.agents.analyst_agent.run", run_analyst)
    monkeypatch.setattr("sira.workflows.agents.writer_agent.run", run_writer)
    monkeypatch.setattr("sira.workflows.agents.reviewer_agent.run", run_reviewer)
    monkeypatch.setattr("sira.workflows.agents.auditor_agent.run", run_auditor)

    result = await ResumeTailorWorkflow().run("# resume", "files/job_posting.md")

    with subtests.test("job_title"):
        assert result.job_title == "Platform Engineer"

    with subtests.test("company_name"):
        assert result.company_name == "Acme"

    with subtests.test("passed"):
        assert result.passed is True


@pytest.mark.anyio
async def test_analyst_failure_after_retries_raises_pipeline_error(
    monkeypatch, sample_cv
) -> None:
    """Analyst failure after all retries raises PipelineError with a user-facing message."""

    async def run_parser(*args, **kwargs):
        return DummyRunResult(sample_cv)

    async def always_fail(*args, **kwargs):
        raise ValueError("simulated agent unavailable")

    monkeypatch.setattr("sira.workflows.agents.resume_parser_agent.run", run_parser)
    monkeypatch.setattr("sira.workflows.agents.analyst_agent.run", always_fail)

    with pytest.raises(PipelineError) as excinfo:
        await ResumeTailorWorkflow().run("# resume", job_content="job posting")

    # The error carries a user-facing message that surfaces the underlying error.
    assert "simulated agent unavailable" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Task 1: UserAbortedError + interactive param
# ---------------------------------------------------------------------------


def test_user_aborted_error_is_exception():
    from sira.workflows import UserAbortedError

    err = UserAbortedError("test")
    assert isinstance(err, Exception)


def test_workflow_interactive_defaults_false():
    workflow = ResumeTailorWorkflow()
    assert workflow._interactive is False


def test_workflow_accepts_interactive_true():
    workflow = ResumeTailorWorkflow(interactive=True)
    assert workflow._interactive is True


# ---------------------------------------------------------------------------
# Task 2: _human_checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_non_interactive_returns_default():
    workflow = ResumeTailorWorkflow(interactive=False)
    action, feedback = workflow._human_checkpoint(
        header="Test header",
        details=["detail line"],
        choices=[("c", "Continue"), ("q", "Quit")],
        default="c",
    )
    assert action == "c"
    assert feedback == ""


def test_checkpoint_non_interactive_custom_default():
    workflow = ResumeTailorWorkflow(interactive=False)
    action, feedback = workflow._human_checkpoint(
        header="Test",
        details=[],
        choices=[("c", "Continue"), ("q", "Quit")],
        default="q",
    )
    assert action == "q"
    assert feedback == ""


def test_checkpoint_non_tty_returns_default(monkeypatch):
    _patch_stdin(monkeypatch, is_tty=False)
    workflow = ResumeTailorWorkflow(interactive=True)
    action, feedback = workflow._human_checkpoint(
        header="Test",
        details=[],
        choices=[("c", "Continue"), ("q", "Quit")],
    )
    assert action == "c"
    assert feedback == ""


def test_checkpoint_non_tty_input_never_called(monkeypatch):
    _patch_stdin(monkeypatch, is_tty=False)
    monkeypatch.setattr(
        "builtins.input",
        lambda _: (_ for _ in ()).throw(AssertionError("input called")),
    )
    workflow = ResumeTailorWorkflow(interactive=True)
    action, _ = workflow._human_checkpoint(
        header="Test", details=[], choices=[("c", "Continue")]
    )
    assert action == "c"


def test_checkpoint_quit_choice(monkeypatch):
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: "q")
    workflow = ResumeTailorWorkflow(interactive=True)
    action, feedback = workflow._human_checkpoint(
        header="Test",
        details=["Some detail"],
        choices=[("c", "Continue"), ("q", "Quit")],
    )
    assert action == "q"
    assert feedback == ""


def test_checkpoint_invalid_then_valid_input(monkeypatch):
    responses = iter(["x", "q"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    workflow = ResumeTailorWorkflow(interactive=True)
    action, feedback = workflow._human_checkpoint(
        header="Test",
        details=[],
        choices=[("c", "Continue"), ("q", "Quit")],
    )
    assert action == "q"
    assert feedback == ""


def test_checkpoint_empty_feedback_re_prompts(monkeypatch):
    responses = iter(["f", "", "my instructions"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    workflow = ResumeTailorWorkflow(interactive=True)
    action, feedback = workflow._human_checkpoint(
        header="Test",
        details=[],
        choices=[("c", "Continue"), ("f", "Provide feedback"), ("q", "Quit")],
    )
    assert action == "f"
    assert feedback == "my instructions"


def test_checkpoint_feedback_returned_directly(monkeypatch):
    responses = iter(["f", "emphasize Python"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    workflow = ResumeTailorWorkflow(interactive=True)
    action, feedback = workflow._human_checkpoint(
        header="Test",
        details=[],
        choices=[("c", "Continue"), ("f", "Provide feedback"), ("q", "Quit")],
    )
    assert action == "f"
    assert feedback == "emphasize Python"


# ---------------------------------------------------------------------------
# Shared helpers for interactive checkpoint integration tests
# ---------------------------------------------------------------------------


def _make_failing_audit():
    return AuditResult(
        passed=False,
        hallucination_score=3,
        ai_cliche_score=5,
        issues=[],
        feedback_summary="Missing key skills from job description.",
    )


def _make_passing_audit():
    return AuditResult(
        passed=True,
        hallucination_score=0,
        ai_cliche_score=1,
        issues=[],
        feedback_summary="Looks good.",
    )


def _make_report_narrative():
    return ReportNarrative(
        suggestions_to_strengthen=["Add Kubernetes"],
        audit_summary="Passed",
        recommendation_rationale="See coverage.",
    )


def _matcher_stub(covered_sequence: list[bool]):
    """agent.run stand-in for skill_matcher_agent.

    Call N answers ``covered_sequence[min(N, len-1)]`` for every requested
    skill. With the job used by ``_base_agent_mocks`` (hard: Python,
    Kubernetes; soft: Communication; keywords: Python, Kubernetes) and
    ``sample_cv`` (has Python), all-False gives score 40 → "Weak Match" and
    all-True gives 90 → "Strong Match".
    """
    calls = {"n": 0}

    async def run_matcher(*args, **kwargs):
        idx = min(calls["n"], len(covered_sequence) - 1)
        calls["n"] += 1
        covered = covered_sequence[idx]
        skills = kwargs.get("deps") or ()
        return DummyRunResult(
            SkillMatchResult(
                matches=[
                    SkillMatch(
                        skill=s, covered=covered, evidence="stub" if covered else ""
                    )
                    for s in skills
                ]
            )
        )

    return run_matcher


def _base_agent_mocks(
    monkeypatch, sample_cv, *, auditor_result, matcher_covered: list[bool] | None = None
):
    """Patch all agents. auditor_result can be a single AuditResult or a list for sequential calls.

    ``matcher_covered`` drives the verdict (see ``_matcher_stub``); default
    ``[True]`` → "Strong Match".
    """

    async def run_analyst(*args, **kwargs):
        return DummyRunResult(
            JobAnalysis(
                job_title="Engineer",
                company_name="Acme",
                summary="Platform role",
                hard_skills=["Python", "Kubernetes"],
                soft_skills=["Communication"],
                key_responsibilities=["Build"],
                keywords_to_target=["Python", "Kubernetes"],
            )
        )

    async def run_writer(*args, **kwargs):
        return DummyRunResult(sample_cv)

    async def run_reviewer(*args, **kwargs):
        return DummyRunResult(
            ReviewResult(
                quality_score=9,
                needs_improvement=False,
                specific_suggestions=[],
                strengths=[],
            )
        )

    if isinstance(auditor_result, list):
        call_idx = {"n": 0}

        async def run_auditor(*args, **kwargs):
            idx = min(call_idx["n"], len(auditor_result) - 1)
            call_idx["n"] += 1
            return DummyRunResult(auditor_result[idx])
    else:

        async def run_auditor(*args, **kwargs):
            return DummyRunResult(auditor_result)

    async def run_report(*args, **kwargs):
        return DummyRunResult(_make_report_narrative())

    monkeypatch.setattr("sira.workflows.agents.report_agent.run", run_report)
    monkeypatch.setattr(
        "sira.workflows.agents.skill_matcher_agent.run",
        _matcher_stub(matcher_covered or [True]),
    )

    async def run_parser(*args, **kwargs):
        return DummyRunResult(sample_cv)

    monkeypatch.setattr("sira.workflows.agents.resume_parser_agent.run", run_parser)
    monkeypatch.setattr("sira.workflows.agents.analyst_agent.run", run_analyst)
    monkeypatch.setattr("sira.workflows.agents.writer_agent.run", run_writer)
    monkeypatch.setattr("sira.workflows.agents.reviewer_agent.run", run_reviewer)
    monkeypatch.setattr("sira.workflows.agents.auditor_agent.run", run_auditor)


# ---------------------------------------------------------------------------
# Task 4: Hook 1 — audit failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_interactive_never_prompts(monkeypatch, sample_cv):
    """Non-interactive workflow with audit failure never calls input()."""
    monkeypatch.setattr(
        "builtins.input",
        lambda _: (_ for _ in ()).throw(
            AssertionError("input() was called in non-interactive mode")
        ),
    )
    _base_agent_mocks(monkeypatch, sample_cv, auditor_result=_make_failing_audit())

    result = await ResumeTailorWorkflow(interactive=False, write_attempts=1).run(
        "# resume", job_content="job description"
    )
    assert result.passed is False


@pytest.mark.anyio
async def test_interactive_audit_failure_quit(monkeypatch, sample_cv):
    """With interactive=True and audit failure, choosing 'q' raises UserAbortedError."""
    from sira.workflows import UserAbortedError

    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: "q")
    _base_agent_mocks(monkeypatch, sample_cv, auditor_result=_make_failing_audit())

    with pytest.raises(UserAbortedError):
        await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
            "# resume", job_content="job description"
        )


@pytest.mark.anyio
async def test_interactive_audit_failure_continue(monkeypatch, sample_cv):
    """With interactive=True and audit failure, choosing 'c' completes the run (passed=False)."""
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: "c")
    _base_agent_mocks(monkeypatch, sample_cv, auditor_result=_make_failing_audit())

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )
    assert result.passed is False


@pytest.mark.anyio
async def test_interactive_audit_failure_feedback_then_pass(monkeypatch, sample_cv):
    """Feedback retries the cycle; second cycle passes audit."""
    writer_prompts = []

    async def run_writer_capture(*args, **kwargs):
        writer_prompts.append(args[0] if args else "")
        return DummyRunResult(sample_cv)

    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=[_make_failing_audit(), _make_passing_audit()],
    )
    monkeypatch.setattr("sira.workflows.agents.writer_agent.run", run_writer_capture)

    responses = iter(["f", "Emphasize Python and avoid leverage"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )

    assert result.passed is True
    assert len(writer_prompts) >= 2
    assert "Emphasize Python and avoid leverage" in writer_prompts[1]


@pytest.mark.anyio
async def test_interactive_audit_failure_feedback_still_fails_then_continue(
    monkeypatch, sample_cv
):
    """After feedback retry still fails, only c/q offered; choosing 'c' completes normally."""
    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_failing_audit(),
    )

    responses = iter(["f", "my instructions", "c"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )
    assert result.passed is False


@pytest.mark.anyio
async def test_interactive_audit_failure_feedback_still_fails_then_quit(
    monkeypatch, sample_cv
):
    """After feedback retry still fails, choosing 'q' at second checkpoint raises UserAbortedError."""
    from sira.workflows import UserAbortedError

    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_failing_audit(),
    )

    responses = iter(["f", "my instructions", "q"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    with pytest.raises(UserAbortedError):
        await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
            "# resume", job_content="job description"
        )


# ---------------------------------------------------------------------------
# Task 5: Hook 2 — weak match
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_interactive_weak_match_quit(monkeypatch, sample_cv):
    """Audit passes but Weak Match report → 'q' raises UserAbortedError."""
    from sira.workflows import UserAbortedError

    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: "q")
    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_passing_audit(),
        matcher_covered=[False],
    )

    with pytest.raises(UserAbortedError):
        await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
            "# resume", job_content="job description"
        )


@pytest.mark.anyio
async def test_interactive_weak_match_continue(monkeypatch, sample_cv):
    """Audit passes but Weak Match report → 'c' completes run."""
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: "c")
    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_passing_audit(),
        matcher_covered=[False],
    )

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )
    assert result.passed is True  # audit passed; only report is weak


@pytest.mark.anyio
async def test_final_report_score_and_verdict_are_computed_in_python(
    monkeypatch, sample_cv, subtests
):
    """The report agent no longer decides the score; the workflow does."""
    _patch_stdin(monkeypatch, is_tty=False)
    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_passing_audit(),
        matcher_covered=[True],
    )

    result = await ResumeTailorWorkflow(write_attempts=1).run(
        "# resume", job_content="job"
    )
    report = result.final_report
    assert report is not None
    with subtests.test("score"):
        assert report.match_score == 90  # hard 100, soft 100, keywords 50
    with subtests.test("verdict"):
        assert report.overall_recommendation == "Strong Match"
    with subtests.test("evidence_in_gaps"):
        assert report.gaps.skill_evidence["Kubernetes"] == "stub"
        assert report.gaps.skill_evidence["Python"] == ""  # literal hit


@pytest.mark.anyio
async def test_interactive_weak_match_feedback_then_strong(monkeypatch, sample_cv):
    """Feedback triggers a re-run; second cycle produces Strong Match."""
    writer_prompts = []

    async def run_writer_capture(*args, **kwargs):
        writer_prompts.append(args[0] if args else "")
        return DummyRunResult(sample_cv)

    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_passing_audit(),
        matcher_covered=[False, True],
    )
    monkeypatch.setattr("sira.workflows.agents.writer_agent.run", run_writer_capture)

    responses = iter(["f", "emphasize Python"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )

    assert result.passed is True
    assert len(writer_prompts) >= 2
    assert "emphasize Python" in writer_prompts[1]


@pytest.mark.anyio
async def test_interactive_weak_match_feedback_still_weak_then_continue(
    monkeypatch, sample_cv
):
    """After feedback retry still Weak Match, second checkpoint offers only c/q; 'c' completes."""
    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_passing_audit(),
        matcher_covered=[False],
    )

    responses = iter(["f", "my instructions", "c"])
    _patch_stdin(monkeypatch, is_tty=True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )
    assert result.passed is True


@pytest.mark.anyio
async def test_checkpoint_non_tty_auto_continues(monkeypatch, sample_cv):
    """With non-TTY stdin, checkpoints auto-continue regardless of interactive flag."""
    _patch_stdin(monkeypatch, is_tty=False)
    monkeypatch.setattr(
        "builtins.input",
        lambda _: (_ for _ in ()).throw(AssertionError("input() called on non-TTY")),
    )
    _base_agent_mocks(
        monkeypatch,
        sample_cv,
        auditor_result=_make_failing_audit(),
    )

    result = await ResumeTailorWorkflow(interactive=True, write_attempts=1).run(
        "# resume", job_content="job description"
    )
    assert result.passed is False
