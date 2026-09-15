"""Shape of the structured CV model (spec §4)."""

import json

from sira.models.agents.output import (
    CV,
    ContactInfo,
    Education,
    Project,
    SkillGroup,
    WorkExperience,
)


def _cv() -> CV:
    return CV(
        full_name="Jane Doe",
        contact=ContactInfo(
            email="jane@example.com",
            phone="",
            location="Berlin",
            links=["[GitHub](https://github.com/jane)"],
        ),
        summary="Engineer.",
        skill_groups=[
            SkillGroup(category="Languages", skills=["Python", "Go"]),
            SkillGroup(category="Cloud", skills=["AWS", "python"]),
        ],
        experience=[
            WorkExperience(company="Acme", role="Eng", dates="2020", highlights=[])
        ],
        education=[Education(degree="BSc", institution="TU Berlin")],
        projects=[Project(name="Tool", description="A CLI")],
    )


def test_skills_property_flattens_groups_in_order_without_case_duplicates():
    assert _cv().skills == ["Python", "Go", "AWS"]


def test_skills_is_not_part_of_the_schema_or_json():
    schema = CV.model_json_schema()
    assert "skills" not in schema["properties"]
    assert "contact_info" not in schema["properties"]
    top_level_keys = json.loads(_cv().model_dump_json()).keys()
    assert "skills" not in top_level_keys and "skill_groups" in top_level_keys


def test_contact_display_items_skips_empty_fields_in_display_order():
    items = _cv().contact.display_items()
    assert items == ["jane@example.com", "Berlin", "[GitHub](https://github.com/jane)"]


def test_optional_sections_default_to_empty():
    cv = CV(
        full_name="X",
        summary="",
        skill_groups=[],
        experience=[],
        education=[],
    )
    assert cv.contact == ContactInfo()
    assert cv.projects == [] and cv.certifications == [] and cv.publications == []
