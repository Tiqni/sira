"""Semantic skill matching: literal pre-pass, then the skill matcher agent.

``match_skills`` is called once, sequentially, inside the report phase of the
durable workflow. The agent run is a DBOS-checkpointed step through
``DBOSDurability``; nothing here may run agents concurrently.
"""

from __future__ import annotations

from collections.abc import Sequence

from dbos import error as dbos_error
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.usage import RunUsage, UsageLimits

from sira.models.agents.output import CV, JobAnalysis, SkillMatch
from sira.reporting.base import get_active_reporter
from sira.utils.skill_matching import literal_matches, render_cv_text
from sira.workflows.agents import _safe_report, run_agent, skill_matcher_agent

SKILL_MATCHER_LABEL = "Skill Matcher"


def unique_job_skills(job: JobAnalysis) -> list[str]:
    """Hard then soft skills, exact-string de-duplicated, first occurrence kept."""
    seen: set[str] = set()
    unique: list[str] = []
    for skill in (*job.hard_skills, *job.soft_skills):
        if skill not in seen:
            seen.add(skill)
            unique.append(skill)
    return unique


def build_matcher_prompt(cv_text: str, skills: Sequence[str]) -> str:
    numbered = "\n".join(f"{i}. {skill}" for i, skill in enumerate(skills, start=1))
    return f"CV:\n{cv_text}\n\nSkills to judge:\n{numbered}"


async def match_skills(
    original: CV,
    job: JobAnalysis,
    *,
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
) -> dict[str, SkillMatch]:
    """Decide, per job skill, whether the original CV covers it.

    Skills whose text appears in the CV are covered without a model call. The
    rest go to ``skill_matcher_agent`` in one request. If that request fails
    for good (retries or usage limit exhausted, provider error, or DBOS
    step-retry exhaustion inside the durable workflow), the pending skills
    stay undecided — they count as missing, exactly as before semantic
    matching existed — and a warning is logged. This never fails the run.
    """
    reporter = get_active_reporter()
    skills = unique_job_skills(job)
    cv_text = render_cv_text(original)
    matches = literal_matches(skills, cv_text)
    pending = [skill for skill in skills if skill not in matches]
    if not pending:
        return matches

    _safe_report(
        reporter.log,
        f"\n🔎 Skill Matcher: judging {len(pending)} of {len(skills)} job skills "
        "against your CV (the rest matched literally)...",
    )
    try:
        result = await run_agent(
            skill_matcher_agent,
            build_matcher_prompt(cv_text, pending),
            agent_label=SKILL_MATCHER_LABEL,
            usage=usage,
            usage_limits=usage_limits,
            deps=tuple(pending),
        )
    except (AgentRunError, dbos_error.DBOSMaxStepRetriesExceeded) as exc:
        _safe_report(
            reporter.log,
            f"   ⚠️ Skill matcher unavailable ({type(exc).__name__}) — "
            "falling back to literal matching for the remaining skills.",
        )
        return matches

    for match in result.output.matches:
        matches[match.skill] = match
    return matches
