from sira.models.agents.output import (
    CV,
    ContactInfo,
    Education,
    Project,
    SkillGroup,
    WorkExperience,
)
from sira.rendering.html import render_html
from sira.rendering.templates import CLASSIC, MODERN


def _cv(**overrides) -> CV:
    base = dict(
        full_name="Jane <Doe>",
        contact=ContactInfo(
            email="jane@example.com",
            location="Berlin",
            links=["[GitHub](https://github.com/jane)"],
        ),
        summary="Builds **reliable** systems.",
        skill_groups=[SkillGroup(category="Languages", skills=["Python", "C&C++"])],
        experience=[
            WorkExperience(
                company="Acme",
                role="Engineer",
                dates="2020 – Now",
                highlights=["Did X"],
            )
        ],
        education=[
            Education(degree="BSc", institution="TU", dates="2016", details="Honours")
        ],
        projects=[Project(name="Tool", description="A CLI", link="https://t.io")],
        certifications=["AWS SAA"],
        publications=[],
    )
    base.update(overrides)
    return CV(**base)


def test_header_and_contact_line():
    html = render_html(_cv(), MODERN)
    assert '<div class="name">Jane &lt;Doe&gt;</div>' in html
    assert (
        '<div class="contact">jane@example.com · Berlin · '
        '<a href="https://github.com/jane">GitHub</a></div>'
    ) in html


def test_sections_use_semantic_classes_and_inline_markdown():
    html = render_html(_cv(), MODERN)
    assert '<h2 class="section-title">Summary</h2>' in html
    assert "<p>Builds <b>reliable</b> systems.</p>" in html
    assert '<p class="skill-group"><b>Languages:</b> Python, C&amp;C++</p>' in html
    assert (
        '<p class="entry-head"><b>Engineer</b> — Acme · '
        '<span class="dates">2020 – Now</span></p>'
    ) in html
    assert '<ul class="bullets">' in html and "<li>Did X</li>" in html
    assert (
        '<p class="entry-head"><b>Tool</b> — A CLI '
        '(<a href="https://t.io">https://t.io</a>)</p>'
    ) in html
    assert (
        '<p class="entry-head"><b>BSc</b> — TU · <span class="dates">2016</span></p>'
    ) in html
    assert '<p class="details">Honours</p>' in html
    assert '<h2 class="section-title">Certifications</h2>' in html


def test_empty_sections_are_omitted():
    html = render_html(
        _cv(projects=[], certifications=[], publications=[], education=[]), MODERN
    )
    for heading in ("Projects", "Certifications", "Publications", "Education"):
        assert f">{heading}</h2>" not in html


def test_no_tables_anywhere():
    assert "<table" not in render_html(_cv(), MODERN)


def test_caps_style_uppercases_section_titles():
    html = render_html(_cv(), CLASSIC)
    assert '<h2 class="section-title">SUMMARY</h2>' in html
    assert ">Summary</h2>" not in html

    html = render_html(_cv(), MODERN)
    assert '<h2 class="section-title">Summary</h2>' in html
