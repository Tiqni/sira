"""Unit tests for compute_cv_diff and compute_gap_analysis.

These are pure-Python tests — no LLM calls.
"""

import pytest

from sira.models.agents.output import (
    CV,
    JobAnalysis,
    WorkExperience,
)
from sira.utils.cv_diff import compute_cv_diff, compute_gap_analysis


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def original_cv() -> CV:
    return CV(
        full_name="Alice Dev",
        contact_info="alice@example.com",
        summary="Backend engineer with 5 years experience.",
        skills=["Python", "Django", "PostgreSQL", "Redis", "Docker"],
        experience=[
            WorkExperience(
                company="Acme Corp",
                role="Software Engineer",
                dates="2020-2024",
                highlights=[
                    "Built REST APIs serving 10k requests/day.",
                    "Maintained PostgreSQL databases.",
                ],
            )
        ],
        education=["BSc Computer Science, MIT, 2019"],
        certifications=[],
        publications=[],
        projects=[],
    )


@pytest.fixture
def tailored_cv(original_cv: CV) -> CV:
    """A tailored version: summary changed, skills reordered, one bullet rephrased."""
    return CV(
        full_name=original_cv.full_name,
        contact_info=original_cv.contact_info,
        summary="Backend engineer focused on scalable APIs and cloud-native development.",
        skills=["Python", "Docker", "Django", "PostgreSQL", "Redis"],  # Docker moved up
        experience=[
            WorkExperience(
                company="Acme Corp",
                role="Software Engineer",
                dates="2020-2024",
                highlights=[
                    "Designed and shipped REST APIs handling 10k requests/day.",  # rephrased
                    "Maintained PostgreSQL databases.",  # unchanged
                ],
            )
        ],
        education=original_cv.education,
        certifications=original_cv.certifications,
        publications=original_cv.publications,
        projects=original_cv.projects,
    )


@pytest.fixture
def job_analysis() -> JobAnalysis:
    return JobAnalysis(
        job_title="Senior Backend Engineer",
        company_name="Acme Corp",
        summary="Looking for a senior backend engineer.",
        hard_skills=["Python", "Docker", "Kubernetes", "Terraform"],
        soft_skills=["teamwork", "communication"],
        key_responsibilities=["Build APIs", "Manage infra"],
        keywords_to_target=["Python", "Docker", "Kubernetes", "REST API", "CI/CD"],
    )


# ---------------------------------------------------------------------------
# compute_cv_diff tests
# ---------------------------------------------------------------------------


def test_cv_diff_detects_summary_change(original_cv: CV, tailored_cv: CV):
    diff = compute_cv_diff(original_cv, tailored_cv)
    assert diff.summary_changed is True


def test_cv_diff_no_summary_change_when_identical(original_cv: CV):
    diff = compute_cv_diff(original_cv, original_cv)
    assert diff.summary_changed is False


def test_cv_diff_detects_skills_reordered(original_cv: CV, tailored_cv: CV):
    diff = compute_cv_diff(original_cv, tailored_cv)
    # Docker moved from index 4 to index 1 — should appear in reordered
    assert "Docker" in diff.skills_reordered


def test_cv_diff_detects_skills_deprioritized(original_cv: CV, tailored_cv: CV):
    diff = compute_cv_diff(original_cv, tailored_cv)
    # Redis moved from index 3 to index 4 — should appear in deprioritized
    assert "Redis" in diff.skills_deprioritized


def test_cv_diff_detects_rephrased_bullets(original_cv: CV, tailored_cv: CV, subtests):
    diff = compute_cv_diff(original_cv, tailored_cv)
    assert len(diff.experience_changes) == 1  # guard/pre-condition, not a subtest
    change = diff.experience_changes[0]
    with subtests.test("company_name"):
        assert change.company == "Acme Corp"
    with subtests.test("bullets_rephrased_count"):
        assert len(change.bullets_rephrased) == 1
    with subtests.test("arrow_separator"):
        assert "→" in change.bullets_rephrased[0]


def test_cv_diff_counts_unchanged_bullets(original_cv: CV, tailored_cv: CV):
    diff = compute_cv_diff(original_cv, tailored_cv)
    change = diff.experience_changes[0]
    assert change.bullets_unchanged == 1


def test_cv_diff_sections_modified_includes_summary(original_cv: CV, tailored_cv: CV):
    diff = compute_cv_diff(original_cv, tailored_cv)
    assert "summary" in diff.sections_modified


def test_cv_diff_identical_cv_produces_empty_diff(original_cv: CV, subtests):
    diff = compute_cv_diff(original_cv, original_cv)
    with subtests.test("summary_changed"):
        assert diff.summary_changed is False
    with subtests.test("skills_reordered"):
        assert diff.skills_reordered == []
    with subtests.test("skills_deprioritized"):
        assert diff.skills_deprioritized == []
    with subtests.test("experience_changes"):
        assert diff.experience_changes == []
    with subtests.test("sections_modified"):
        assert diff.sections_modified == []


# ---------------------------------------------------------------------------
# compute_gap_analysis tests
# ---------------------------------------------------------------------------


def test_gap_analysis_missing_hard_skills(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    with subtests.test("kubernetes_missing"):
        assert "Kubernetes" in gap.missing_hard_skills
    with subtests.test("terraform_missing"):
        assert "Terraform" in gap.missing_hard_skills


def test_gap_analysis_no_false_positive_for_existing_skill(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    with subtests.test("python_not_missing"):
        assert "Python" not in gap.missing_hard_skills
    with subtests.test("docker_not_missing"):
        assert "Docker" not in gap.missing_hard_skills


def test_gap_analysis_covered_keywords(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    with subtests.test("python_covered"):
        assert "Python" in gap.covered_keywords
    with subtests.test("docker_covered"):
        assert "Docker" in gap.covered_keywords


def test_gap_analysis_missing_keywords(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    # "Kubernetes" and "CI/CD" do NOT appear in tailored CV text
    assert "Kubernetes" in gap.missing_keywords


def test_gap_analysis_coverage_percent_is_between_0_and_100(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    assert 0.0 <= gap.keyword_coverage_percent <= 100.0


def test_gap_analysis_when_tailored_cv_is_none_keyword_coverage_is_zero(
    original_cv: CV, job_analysis: JobAnalysis, subtests
):
    """When writer fails (new_cv is None), coverage defaults to 0%."""
    gap = compute_gap_analysis(original_cv, None, job_analysis)
    with subtests.test("coverage_is_zero"):
        assert gap.keyword_coverage_percent == 0.0
    with subtests.test("covered_keywords_empty"):
        assert gap.covered_keywords == []


# ---------------------------------------------------------------------------
# Output model contracts for semantic matching
# ---------------------------------------------------------------------------


def test_gap_analysis_new_fields_default_empty(subtests):
    from sira.models.agents.output import GapAnalysis, SkillMatch, SkillMatchResult

    gap = GapAnalysis()
    with subtests.test("covered_hard_skills"):
        assert gap.covered_hard_skills == []
    with subtests.test("covered_soft_skills"):
        assert gap.covered_soft_skills == []
    with subtests.test("skill_evidence"):
        assert gap.skill_evidence == {}
    with subtests.test("hard_pct"):
        assert gap.hard_skill_coverage_percent == 0.0
    with subtests.test("soft_pct"):
        assert gap.soft_skill_coverage_percent == 0.0
    with subtests.test("skill_match_evidence_default"):
        assert SkillMatch(skill="Python", covered=True).evidence == ""
    with subtests.test("skill_match_result_roundtrip"):
        result = SkillMatchResult(matches=[SkillMatch(skill="Python", covered=False)])
        assert result.model_dump()["matches"][0]["covered"] is False


# ---------------------------------------------------------------------------
# compute_gap_analysis with semantic skill matches
# ---------------------------------------------------------------------------


def test_gap_analysis_literal_mode_populates_covered_lists_and_percentages(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    gap = compute_gap_analysis(original_cv, tailored_cv, job_analysis)
    with subtests.test("covered_hard"):
        assert gap.covered_hard_skills == ["Python", "Docker"]
    with subtests.test("missing_hard"):
        assert gap.missing_hard_skills == ["Kubernetes", "Terraform"]
    with subtests.test("hard_pct"):
        assert gap.hard_skill_coverage_percent == 50.0
    with subtests.test("soft_all_missing"):
        assert gap.covered_soft_skills == []
        assert gap.soft_skill_coverage_percent == 0.0
    with subtests.test("evidence_empty_for_literal"):
        assert gap.skill_evidence == {"Python": "", "Docker": ""}


def test_gap_analysis_uses_skill_matches_when_given(
    original_cv: CV, tailored_cv: CV, job_analysis: JobAnalysis, subtests
):
    from sira.models.agents.output import SkillMatch

    matches = {
        "Python": SkillMatch(skill="Python", covered=True, evidence=""),
        "Docker": SkillMatch(skill="Docker", covered=True, evidence=""),
        "Kubernetes": SkillMatch(
            skill="Kubernetes", covered=True, evidence="ran services on K8s"
        ),
        "Terraform": SkillMatch(skill="Terraform", covered=False, evidence=""),
        "teamwork": SkillMatch(
            skill="teamwork", covered=True, evidence="pair programming"
        ),
        # "communication" deliberately absent from the mapping -> missing
    }
    gap = compute_gap_analysis(
        original_cv, tailored_cv, job_analysis, skill_matches=matches
    )
    with subtests.test("covered_hard"):
        assert gap.covered_hard_skills == ["Python", "Docker", "Kubernetes"]
    with subtests.test("missing_hard"):
        assert gap.missing_hard_skills == ["Terraform"]
    with subtests.test("hard_pct"):
        assert gap.hard_skill_coverage_percent == 75.0
    with subtests.test("soft_split"):
        assert gap.covered_soft_skills == ["teamwork"]
        assert gap.missing_soft_skills == ["communication"]
    with subtests.test("soft_pct"):
        assert gap.soft_skill_coverage_percent == 50.0
    with subtests.test("evidence"):
        assert gap.skill_evidence["Kubernetes"] == "ran services on K8s"
        assert gap.skill_evidence["teamwork"] == "pair programming"
        assert "Terraform" not in gap.skill_evidence
    with subtests.test("keywords_still_literal"):
        assert "Kubernetes" in gap.missing_keywords


def test_gap_analysis_skill_matches_do_not_need_tailored_cv(
    original_cv: CV, job_analysis: JobAnalysis, subtests
):
    from sira.models.agents.output import SkillMatch

    matches = {s: SkillMatch(skill=s, covered=True) for s in job_analysis.hard_skills}
    gap = compute_gap_analysis(original_cv, None, job_analysis, skill_matches=matches)
    with subtests.test("hard_all_covered"):
        assert gap.hard_skill_coverage_percent == 100.0
    with subtests.test("keywords_zero_without_tailored"):
        assert gap.keyword_coverage_percent == 0.0
        assert gap.missing_keywords == list(job_analysis.keywords_to_target)


def test_gap_analysis_percent_is_zero_when_job_lists_no_skills(
    original_cv: CV, tailored_cv: CV
):
    job = JobAnalysis(
        job_title="x",
        company_name="y",
        summary="z",
        hard_skills=[],
        soft_skills=[],
        key_responsibilities=[],
        keywords_to_target=["Python"],
    )
    gap = compute_gap_analysis(original_cv, tailored_cv, job, skill_matches={})
    assert gap.hard_skill_coverage_percent == 0.0
    assert gap.soft_skill_coverage_percent == 0.0
