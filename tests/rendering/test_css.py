import pytest

from sira.rendering.css import build_css
from sira.rendering.templates import CLASSIC, COMPACT, MODERN, TemplateSpec


@pytest.mark.parametrize("spec", [MODERN, CLASSIC, COMPACT])
def test_css_carries_the_spec_numbers(spec: TemplateSpec):
    css = build_css(spec)
    assert f"font-family: {spec.pdf_font};" in css
    assert f"font-size: {spec.base_pt}pt;" in css
    assert f"font-size: {spec.name_pt}pt;" in css
    assert f"font-size: {spec.heading_pt}pt;" in css
    assert f"color: {spec.accent_hex};" in css
    assert f"color: {spec.muted_hex};" in css
    assert f"border-bottom: 0.75pt solid {spec.heading_hex};" in css


def test_caps_headings_are_uppercase_and_rule_headings_are_not():
    assert "text-transform: uppercase;" in build_css(CLASSIC)
    assert "text-transform: uppercase;" not in build_css(MODERN)


def test_css_names_every_semantic_class():
    css = build_css(MODERN)
    for selector in (
        ".name",
        ".contact",
        ".section-title",
        ".entry-head",
        ".dates",
        ".details",
        "ul.bullets",
        ".skill-group",
    ):
        assert selector in css
