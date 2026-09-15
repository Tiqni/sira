from sira.models.agents.output import FinalReport


def generate_report_markdown(report: FinalReport) -> str:
    """Render a FinalReport as a Markdown string.

    Args:
        report: The completed FinalReport.

    Returns:
        A Markdown-formatted string ready to write to a file.
    """
    lines: list[str] = []

    lines.append(f"# Self-Review Report — {report.company_name} · {report.job_title}\n")
    lines.append(f"**Generated:** {report.generated_at}  \n")
    lines.append(f"**Audit Passed:** {'✅ Yes' if report.passed else '❌ No'}\n")

    lines.append("---\n")

    # Match score and recommendation
    lines.append("## 🎯 Match Score & Recommendation\n")
    lines.append(f"**Score:** {report.match_score}/100  \n")
    lines.append(f"**Verdict:** {report.overall_recommendation}\n")
    lines.append(
        "_Score = 0.6·hard + 0.2·soft + 0.2·keywords coverage; "
        "empty buckets are rescaled (see ARCHITECTURE.md)._\n"
    )
    lines.append(f"{report.recommendation_rationale}\n")

    # What changed
    lines.append("---\n")
    lines.append("## ✏️ What Changed\n")
    diff = report.what_changed
    if not diff.sections_modified:
        lines.append("_No significant changes detected._\n")
    else:
        rendered_any = False
        if diff.summary_changed:
            lines.append("- **Summary** was rewritten\n")
            rendered_any = True
        if diff.skills_reordered:
            reordered = ", ".join(diff.skills_reordered)
            lines.append(f"- **Skills reordered to top:** {reordered}\n")
            rendered_any = True
        if diff.skills_deprioritized:
            deprioritized = ", ".join(diff.skills_deprioritized)
            lines.append(f"- **Skills deprioritized:** {deprioritized}\n")
            rendered_any = True
        for exp_change in diff.experience_changes:
            lines.append(
                f"- **{exp_change.role} at {exp_change.company}:** "
                f"{len(exp_change.bullets_rephrased)} bullet(s) rephrased, "
                f"{exp_change.bullets_unchanged} unchanged\n"
            )
            for bullet in exp_change.bullets_rephrased:
                lines.append(f"  - {bullet}\n")
            rendered_any = True
        if not rendered_any:
            lines.append("_Changes noted but details unavailable._\n")

    # Keyword coverage
    lines.append("---\n")
    lines.append("## 🔑 Keyword Coverage\n")
    gap = report.gaps
    total = len(gap.covered_keywords) + len(gap.missing_keywords)
    if total > 0:
        pct = (len(gap.covered_keywords) / total) * 100
        coverage_str = f"{len(gap.covered_keywords)}/{total}"
    else:
        pct = 0.0
        coverage_str = "N/A"
    lines.append(f"**Keywords covered: {coverage_str} ({pct:.1f}%)**\n")
    if gap.covered_keywords:
        covered_str = ", ".join(f"`{k}`" for k in gap.covered_keywords)
        lines.append(f"\n✅ **Covered:** {covered_str}\n")
    if gap.missing_keywords:
        missing_str = ", ".join(f"`{k}`" for k in gap.missing_keywords)
        lines.append(f"\n❌ **Missing:** {missing_str}\n")

    # Skills covered (literal pre-pass + semantic judge, with evidence)
    lines.append("---\n")
    lines.append("## ✅ Skills Covered\n")
    hard_total = len(gap.covered_hard_skills) + len(gap.missing_hard_skills)
    soft_total = len(gap.covered_soft_skills) + len(gap.missing_soft_skills)
    lines.append(
        f"**Hard skills: {len(gap.covered_hard_skills)}/{hard_total} "
        f"({gap.hard_skill_coverage_percent:.1f}%) · "
        f"Soft skills: {len(gap.covered_soft_skills)}/{soft_total} "
        f"({gap.soft_skill_coverage_percent:.1f}%)**\n"
    )
    covered_skills = [*gap.covered_hard_skills, *gap.covered_soft_skills]
    if not covered_skills:
        lines.append("_No required skills found in your CV._\n")
    for skill in covered_skills:
        quote = gap.skill_evidence.get(skill, "")
        lines.append(f'- **{skill}** — "{quote}"\n' if quote else f"- **{skill}**\n")

    # Skill gaps
    lines.append("---\n")
    lines.append("## 🚧 Skill Gaps\n")
    if not gap.missing_hard_skills and not gap.missing_soft_skills:
        lines.append("_No skill gaps detected — your CV covers all required skills!_\n")
    else:
        if gap.missing_hard_skills:
            hard_str = ", ".join(gap.missing_hard_skills)
            lines.append(f"**Hard skills not in your CV:** {hard_str}\n")
        if gap.missing_soft_skills:
            soft_str = ", ".join(gap.missing_soft_skills)
            lines.append(f"**Soft skills not in your CV:** {soft_str}\n")

    # Suggestions
    lines.append("---\n")
    lines.append("## 💡 Suggestions to Strengthen Your Application\n")
    if not report.suggestions_to_strengthen:
        lines.append("_No additional suggestions — your application looks strong!_\n")
    else:
        for suggestion in report.suggestions_to_strengthen:
            lines.append(f"- {suggestion}\n")

    # Audit summary
    lines.append("---\n")
    lines.append("## 🔍 Audit Summary\n")
    lines.append(f"{report.audit_summary}\n")

    return "".join(lines)
