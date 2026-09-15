import dataclasses

import pytest

from sira.rendering.templates import (
    CLASSIC,
    COMPACT,
    DEFAULT_STYLE,
    MODERN,
    STYLES,
    TemplateSpec,
    get_template,
)


def test_three_styles_registered_in_order():
    assert STYLES == ("modern", "classic", "compact")
    assert DEFAULT_STYLE == "modern"


@pytest.mark.parametrize("spec", [MODERN, CLASSIC, COMPACT])
def test_get_template_returns_the_spec_case_insensitively(spec: TemplateSpec):
    assert get_template(spec.name) is spec
    assert get_template(spec.name.upper()) is spec


def test_get_template_unknown_lists_valid_names():
    with pytest.raises(ValueError, match="modern, classic, compact"):
        get_template("fancy")


def test_specs_are_frozen_and_single_column_friendly():
    with pytest.raises(dataclasses.FrozenInstanceError):
        MODERN.accent_hex = "#000000"  # type: ignore[misc]
    assert MODERN.heading_style == "rule" and MODERN.heading_hex == MODERN.accent_hex
    assert CLASSIC.heading_style == "caps" and CLASSIC.heading_hex == CLASSIC.text_hex
    assert COMPACT.base_pt < MODERN.base_pt and COMPACT.margin_mm < MODERN.margin_mm
