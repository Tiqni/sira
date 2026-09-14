"""generate_report_markdown renders the semantic skill coverage."""

from sira.models.agents.output import CVDiff, FinalReport, GapAnalysis
from sira.utils.markdown_writer import generate_report_markdown


def _report(gap: GapAnalysis) -> FinalReport:
    return FinalReport(
        job_title="Engineer",
        company_name="Acme",
        generated_at="2026-01-01T00:00:00Z",
        overall_recommendation="Partial Match",
        match_score=65,
        what_changed=CVDiff(),
        gaps=gap,
        suggestions_to_strengthen=[],
        audit_summary="ok",
        recommendation_rationale="ok",
        passed=True,
    )


def test_markdown_lists_covered_skills_with_evidence(subtests):
    gap = GapAnalysis(
        covered_hard_skills=["Python", "Kubernetes"],
        missing_hard_skills=["Rust"],
        covered_soft_skills=["Mentorship"],
        skill_evidence={
            "Python": "",
            "Kubernetes": "ran K8s",
            "Mentorship": "mentored 30",
        },
        hard_skill_coverage_percent=66.7,
        soft_skill_coverage_percent=100.0,
        covered_keywords=["Python"],
        missing_keywords=["Rust"],
        keyword_coverage_percent=50.0,
    )
    md = generate_report_markdown(_report(gap))
    with subtests.test("section_present"):
        assert "## ✅ Skills Covered" in md
    with subtests.test("counts"):
        assert "Hard skills: 2/3 (66.7%)" in md
        assert "Soft skills: 1/1 (100.0%)" in md
    with subtests.test("literal_hit_without_quote"):
        assert "- **Python**\n" in md
    with subtests.test("semantic_hit_with_quote"):
        assert '- **Kubernetes** — "ran K8s"' in md
    with subtests.test("formula_note"):
        assert "0.6·hard + 0.2·soft + 0.2·keywords" in md
    with subtests.test("covered_section_precedes_gaps"):
        assert md.index("## ✅ Skills Covered") < md.index("## 🚧 Skill Gaps")


def test_markdown_covered_section_handles_nothing_covered():
    md = generate_report_markdown(_report(GapAnalysis(missing_hard_skills=["Rust"])))
    assert "_No required skills found in your CV._" in md
