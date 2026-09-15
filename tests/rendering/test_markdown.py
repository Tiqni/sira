from sira.models.agents.output import ContactInfo, Education, Project
from sira.rendering.markdown import render_markdown
from tests.factories import make_cv


def test_golden_markdown_for_factory_cv():
    expected = "\n".join(
        [
            "# Jane Doe",
            "jane@example.com",
            "",
            "## Summary",
            "Platform engineer.",
            "",
            "## Skills",
            "**Languages:** Python, SQL  ",
            "",
            "## Experience",
            "",
            "### Engineer — Acme",
            "*2022-2026*",
            "- Built services",
            "",
            "## Education",
            "**BSc CS** — State University  ",
            "",
        ]
    )
    assert render_markdown(make_cv()) == expected


def test_full_contact_line_projects_details_and_lists():
    cv = make_cv().model_copy(
        update={
            "contact": ContactInfo(
                email="j@x.io",
                phone="+1 555",
                location="Berlin",
                links=["[GitHub](https://g.h/j)"],
            ),
            "projects": [
                Project(name="Tool", description="A CLI", link="https://t.io")
            ],
            "education": [
                Education(
                    degree="BSc", institution="TU", dates="2016", details="Honours"
                )
            ],
            "certifications": ["AWS SAA"],
            "publications": ["Talk: X"],
        }
    )
    md = render_markdown(cv)
    assert md.splitlines()[1] == "j@x.io · +1 555 · Berlin · [GitHub](https://g.h/j)"
    assert "## Projects\n**Tool** — A CLI (https://t.io)  \n" in md
    assert "## Education\n**BSc** — TU *(2016)*  \nHonours  \n" in md
    assert "## Certifications\n- AWS SAA\n" in md
    assert "## Publications\n- Talk: X\n" in md


def test_empty_optional_sections_and_flat_skills_header_are_gone():
    md = render_markdown(make_cv())
    assert "## Projects" not in md and "## Certifications" not in md
    assert "- Python" not in md  # skills are grouped lines, not bullets
