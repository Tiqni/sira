"""Stubs for every pipeline agent, shared by the durability and CLI tests.

Agents are stubbed at ``agent.run`` (no model calls); the DBOS machinery —
workflow, child workflows, checkpoint step, continuation — stays real.
"""

import asyncio

from sira.models.agents.output import (
    AuditResult,
    CVDiff,
    FinalReport,
    GapAnalysis,
    JobAnalysis,
    ReviewResult,
)


class DummyRunResult:
    def __init__(self, output):
        self.output = output


class FakeStdin:
    """stdin stand-in with a controllable ``isatty()``."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def install_pipeline_stubs(
    monkeypatch,
    sample_cv,
    *,
    audit_passed=True,
    writer_fail_once=False,
    writer_fail_on_call: int | None = None,
    auditor_fail_on_call: int | None = None,
    parser_cancel_first: bool = False,
    writer_cancel_first: bool = False,
    report_weak_first: bool = False,
) -> dict[str, int]:
    """Stub every pipeline agent; return the per-agent call counters.

    ``*_cancel_first`` raise ``asyncio.CancelledError`` on that agent's first
    call — the shape of a Ctrl+C mid-stage (DBOS leaves the run PENDING).
    ``*_fail_on_call`` raise ``RuntimeError`` on that call number.
    ``report_weak_first`` makes the first report "Weak Match" (triggers the
    interactive checkpoint) and later reports "Strong Match".
    """
    calls = {
        "parser": 0,
        "analyst": 0,
        "writer": 0,
        "reviewer": 0,
        "auditor": 0,
        "report": 0,
    }

    async def run_parser(*a, **k):
        calls["parser"] += 1
        if parser_cancel_first and calls["parser"] == 1:
            raise asyncio.CancelledError("simulated interrupt in parser")
        return DummyRunResult(sample_cv)

    async def run_analyst(*a, **k):
        calls["analyst"] += 1
        return DummyRunResult(
            JobAnalysis(
                job_title="Platform Engineer",
                company_name="Acme",
                summary="role",
                hard_skills=["Python"],
                soft_skills=["Communication"],
                key_responsibilities=["Build"],
                keywords_to_target=["Python"],
            )
        )

    async def run_writer(*a, **k):
        calls["writer"] += 1
        if writer_cancel_first and calls["writer"] == 1:
            raise asyncio.CancelledError("simulated interrupt in writer")
        fail_on = 1 if writer_fail_once else writer_fail_on_call
        if fail_on is not None and calls["writer"] == fail_on:
            raise RuntimeError("simulated crash in writer")
        return DummyRunResult(sample_cv)

    async def run_reviewer(*a, **k):
        calls["reviewer"] += 1
        return DummyRunResult(
            ReviewResult(
                quality_score=9,
                needs_improvement=False,
                specific_suggestions=[],
                strengths=["ok"],
            )
        )

    async def run_auditor(*a, **k):
        calls["auditor"] += 1
        if (
            auditor_fail_on_call is not None
            and calls["auditor"] == auditor_fail_on_call
        ):
            raise RuntimeError("simulated crash in auditor")
        return DummyRunResult(
            AuditResult(
                passed=audit_passed,
                hallucination_score=0,
                ai_cliche_score=0,
                issues=[],
                feedback_summary="fine" if audit_passed else "needs work",
            )
        )

    async def run_report(*a, **k):
        calls["report"] += 1
        recommendation = (
            "Weak Match"
            if (report_weak_first and calls["report"] == 1)
            else "Strong Match"
        )
        return DummyRunResult(
            FinalReport(
                job_title="Platform Engineer",
                company_name="Acme",
                generated_at="2026-01-01T00:00:00Z",
                overall_recommendation=recommendation,
                match_score=90,
                what_changed=CVDiff(),
                gaps=GapAnalysis(),
                suggestions_to_strengthen=[],
                audit_summary="ok",
                recommendation_rationale="ok",
                passed=audit_passed,
            )
        )

    for target, fn in [
        ("resume_parser_agent", run_parser),
        ("analyst_agent", run_analyst),
        ("writer_agent", run_writer),
        ("reviewer_agent", run_reviewer),
        ("auditor_agent", run_auditor),
        ("report_agent", run_report),
    ]:
        monkeypatch.setattr(f"sira.workflows.agents.{target}.run", fn)
    return calls
