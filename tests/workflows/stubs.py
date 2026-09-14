"""Stubs for every pipeline agent, shared by the durability and CLI tests.

Agents are stubbed at ``agent.run`` (no model calls); the DBOS machinery —
workflow, child workflows, checkpoint step, continuation — stays real.
"""

import asyncio

from sira.models.agents.output import (
    AuditResult,
    JobAnalysis,
    ReportNarrative,
    ReviewResult,
    SkillMatch,
    SkillMatchResult,
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
    ``report_weak_first`` makes the skill matcher cover nothing on its first
    real CALL (score 30 → "Weak Match", triggers the interactive checkpoint)
    and everything on every real call afterwards ("Strong Match"). Since
    ``match_skills`` runs at most once per run, a continued run only reaches
    a second real call when it is forked (a fresh invocation of the workflow
    function, which calls the matcher again).
    """
    calls = {
        "parser": 0,
        "analyst": 0,
        "writer": 0,
        "reviewer": 0,
        "auditor": 0,
        "matcher": 0,
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
                # "Python" is in sample_cv (literal hit); the others reach the judge.
                hard_skills=["Python", "Kubernetes", "Terraform"],
                soft_skills=["Communication"],
                key_responsibilities=["Build"],
                keywords_to_target=["Python", "Kubernetes"],
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

    async def run_matcher(*a, **k):
        calls["matcher"] += 1
        covered = not (report_weak_first and calls["matcher"] == 1)
        skills = k.get("deps") or ()
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

    async def run_report(*a, **k):
        calls["report"] += 1
        return DummyRunResult(
            ReportNarrative(
                suggestions_to_strengthen=[],
                audit_summary="ok",
                recommendation_rationale="ok",
            )
        )

    for target, fn in [
        ("resume_parser_agent", run_parser),
        ("analyst_agent", run_analyst),
        ("writer_agent", run_writer),
        ("reviewer_agent", run_reviewer),
        ("auditor_agent", run_auditor),
        ("skill_matcher_agent", run_matcher),
        ("report_agent", run_report),
    ]:
        monkeypatch.setattr(f"sira.workflows.agents.{target}.run", fn)
    return calls
