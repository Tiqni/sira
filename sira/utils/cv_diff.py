"""Pure-Python utilities for computing CV diffs, gap analysis, and the match score.

No LLM calls in this module. Skill coverage decisions arrive as ``SkillMatch``
values (from the literal pre-pass and the skill matcher agent); everything
computed here from them is deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping

from sira.models.agents.output import (
    CV,
    CVDiff,
    ExperienceChange,
    GapAnalysis,
    JobAnalysis,
    SkillMatch,
)


def compute_cv_diff(original: CV, tailored: CV) -> CVDiff:
    """Compute a factual diff between an original CV and a tailored CV.

    Args:
        original: The candidate's original CV.
        tailored: The CV produced by the writer agent.

    Returns:
        CVDiff populated with detected changes.
    """
    sections_modified: list[str] = []

    # --- Summary ---
    summary_changed = original.summary.strip() != tailored.summary.strip()
    if summary_changed:
        sections_modified.append("summary")

    # --- Skills reordering ---
    orig_positions = {skill.lower(): idx for idx, skill in enumerate(original.skills)}
    tail_positions = {skill.lower(): idx for idx, skill in enumerate(tailored.skills)}

    skills_reordered: list[str] = []
    skills_deprioritized: list[str] = []

    for skill in tailored.skills:
        key = skill.lower()
        if key in orig_positions:
            orig_idx = orig_positions[key]
            tail_idx = tail_positions[key]
            if tail_idx < orig_idx:
                skills_reordered.append(skill)
            elif tail_idx > orig_idx:
                skills_deprioritized.append(skill)

    if skills_reordered or skills_deprioritized:
        sections_modified.append("skills")

    # --- Experience bullet diffs ---
    experience_changes: list[ExperienceChange] = []

    # Build lookup for tailored experience by (company, role)
    tailored_exp_map = {
        (exp.company.strip(), exp.role.strip()): exp for exp in tailored.experience
    }

    for orig_exp in original.experience:
        key = (orig_exp.company.strip(), orig_exp.role.strip())
        tail_exp = tailored_exp_map.get(key)
        if tail_exp is None:
            continue

        bullets_rephrased: list[str] = []
        bullets_unchanged = 0

        for i, orig_bullet in enumerate(orig_exp.highlights):
            if i < len(tail_exp.highlights):
                tail_bullet = tail_exp.highlights[i]
                if orig_bullet.strip() != tail_bullet.strip():
                    bullets_rephrased.append(
                        f"{orig_bullet.strip()} → {tail_bullet.strip()}"
                    )
                else:
                    bullets_unchanged += 1
            else:
                # Bullet was removed
                bullets_rephrased.append(f"{orig_bullet.strip()} → (removed)")

        if bullets_rephrased:
            experience_changes.append(
                ExperienceChange(
                    company=orig_exp.company,
                    role=orig_exp.role,
                    bullets_rephrased=bullets_rephrased,
                    bullets_unchanged=bullets_unchanged,
                )
            )

    if experience_changes:
        sections_modified.append("experience")

    return CVDiff(
        summary_changed=summary_changed,
        skills_reordered=skills_reordered,
        skills_deprioritized=skills_deprioritized,
        experience_changes=experience_changes,
        sections_modified=sections_modified,
    )


def _percent(covered: int, total: int) -> float:
    """Coverage as a percentage rounded to one decimal; 0.0 when total is 0."""
    return round(covered / total * 100.0, 1) if total > 0 else 0.0


def _split_skills(
    skills: list[str],
    original_skills_lower: set[str],
    skill_matches: Mapping[str, SkillMatch] | None,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Split job skills into (covered, missing, evidence-by-covered-skill).

    With ``skill_matches`` a skill is covered iff its match says so; a skill
    absent from the mapping is missing. Without it, fall back to exact
    lowercase equality against the CV's ``skills`` list (evidence stays "").
    """
    covered: list[str] = []
    missing: list[str] = []
    evidence: dict[str, str] = {}
    for skill in skills:
        if skill_matches is None:
            is_covered = skill.lower() in original_skills_lower
            quote = ""
        else:
            match = skill_matches.get(skill)
            is_covered = match is not None and match.covered
            quote = match.evidence if match is not None else ""
        if is_covered:
            covered.append(skill)
            evidence[skill] = quote
        else:
            missing.append(skill)
    return covered, missing, evidence


def compute_gap_analysis(
    original: CV,
    tailored: CV | None,
    job: JobAnalysis,
    *,
    skill_matches: Mapping[str, SkillMatch] | None = None,
) -> GapAnalysis:
    """Compute skill and keyword gaps between the original CV and job requirements.

    Args:
        original: The candidate's original CV (used for skill gap detection).
        tailored: The tailored CV (used for keyword coverage). Pass None if the
                  writer failed — keyword coverage will default to 0%.
        job: Structured job analysis with required skills and ATS keywords.
        skill_matches: Per-skill verdicts from ``match_skills`` (literal
                  pre-pass + skill matcher agent), keyed by the skill text as
                  listed in ``job``. None means literal matching against
                  ``original.skills`` only (the pre-semantic behaviour).

    Returns:
        GapAnalysis with covered/missing skills, evidence, and coverage metrics.
    """
    original_skills_lower = {s.lower() for s in original.skills}

    covered_hard, missing_hard, hard_evidence = _split_skills(
        job.hard_skills, original_skills_lower, skill_matches
    )
    covered_soft, missing_soft, soft_evidence = _split_skills(
        job.soft_skills, original_skills_lower, skill_matches
    )
    skill_fields = {
        "missing_hard_skills": missing_hard,
        "missing_soft_skills": missing_soft,
        "covered_hard_skills": covered_hard,
        "covered_soft_skills": covered_soft,
        "skill_evidence": {**hard_evidence, **soft_evidence},
        "hard_skill_coverage_percent": _percent(
            len(covered_hard), len(job.hard_skills)
        ),
        "soft_skill_coverage_percent": _percent(
            len(covered_soft), len(job.soft_skills)
        ),
    }

    # --- Keyword coverage (literal, from tailored CV text — the ATS view) ---
    if tailored is None:
        return GapAnalysis(
            **skill_fields,
            covered_keywords=[],
            missing_keywords=list(job.keywords_to_target),
            keyword_coverage_percent=0.0,
        )

    tailored_text = tailored.model_dump_json().lower()
    covered_kw: list[str] = []
    missing_kw: list[str] = []
    for keyword in job.keywords_to_target:
        if keyword.lower() in tailored_text:
            covered_kw.append(keyword)
        else:
            missing_kw.append(keyword)

    return GapAnalysis(
        **skill_fields,
        covered_keywords=covered_kw,
        missing_keywords=missing_kw,
        keyword_coverage_percent=_percent(len(covered_kw), len(job.keywords_to_target)),
    )
