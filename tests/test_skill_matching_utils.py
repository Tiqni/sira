"""Pure-Python helpers for semantic skill matching — no model calls."""

from sira.models.agents.output import (
    CV,
    ContactInfo,
    Education,
    Project,
    SkillGroup,
    WorkExperience,
)
from sira.utils.skill_matching import literal_matches, normalise_text, render_cv_text


def _cv() -> CV:
    return CV(
        full_name="Alice Dev",
        contact=ContactInfo(email="alice@example.com"),
        summary="Backend engineer doing context engineering for chat products.",
        skill_groups=[SkillGroup(category="Skills", skills=["Python", "K8s"])],
        projects=[Project(name="RAG toolkit", description="Open-source RAG toolkit")],
        experience=[
            WorkExperience(
                company="Acme",
                role="Staff Engineer",
                dates="2020-2024",
                highlights=[
                    "Mentor to ~30 engineers.",
                    "Built   monitoring dashboards.",
                ],
            )
        ],
        education=[Education(degree="BSc CS", institution="State University")],
        certifications=["AWS SAA"],
        publications=["Talk: Context windows"],
    )


def test_render_cv_text_includes_every_section(subtests):
    text = render_cv_text(_cv())
    for needle in (
        "Summary: Backend engineer doing context engineering",
        "Skills — Skills: Python, K8s",
        "Experience:",
        "- Acme — Staff Engineer (2020-2024)",
        "  - Mentor to ~30 engineers.",
        "Projects:",
        "- RAG toolkit — Open-source RAG toolkit",
        "Education:",
        "- BSc CS — State University",
        "Certifications:",
        "- AWS SAA",
        "Publications:",
        "- Talk: Context windows",
    ):
        with subtests.test(needle=needle):
            assert needle in text


def test_render_cv_text_skips_empty_optional_sections():
    cv = _cv().model_copy(
        update={"projects": [], "certifications": [], "publications": []}
    )
    text = render_cv_text(cv)
    assert "Projects:" not in text
    assert "Certifications:" not in text
    assert "Publications:" not in text


def test_normalise_text_collapses_whitespace_and_case():
    assert (
        normalise_text("  Built   Monitoring\nDashboards ")
        == "built monitoring dashboards"
    )


def test_literal_matches_is_case_insensitive_and_whitespace_tolerant(subtests):
    text = render_cv_text(_cv())
    found = literal_matches(
        ["python", "Monitoring dashboards", "Kubernetes", "context   engineering"], text
    )
    with subtests.test("python"):
        assert found["python"].covered is True
    with subtests.test("monitoring"):
        assert "Monitoring dashboards" in found
    with subtests.test("context_engineering_whitespace"):
        assert "context   engineering" in found
    with subtests.test("kubernetes_not_literal"):
        assert "Kubernetes" not in found
    with subtests.test("evidence_empty_for_literal"):
        assert found["python"].evidence == ""
    with subtests.test("skill_key_is_original_text"):
        assert found["python"].skill == "python"


def test_literal_matches_ignores_blank_skills():
    assert literal_matches(["", "   "], "anything") == {}


def test_literal_matches_requires_whole_terms(subtests):
    text = "Skills: JavaScript, C++, Node.js, .NET\nSummary: code for google"
    found = literal_matches(
        ["Java", "C", "R", "Go", "C++", "Node.js", ".NET", "JavaScript"], text
    )
    with subtests.test("java_not_in_javascript"):
        assert "Java" not in found
    with subtests.test("c_not_in_code"):
        assert "C" not in found
    with subtests.test("r_not_anywhere"):
        assert "R" not in found
    with subtests.test("go_not_in_google"):
        assert "Go" not in found
    with subtests.test("cpp_matches"):
        assert "C++" in found
    with subtests.test("node_js_matches"):
        assert "Node.js" in found
    with subtests.test("dot_net_matches"):
        assert ".NET" in found
    with subtests.test("javascript_matches"):
        assert "JavaScript" in found
