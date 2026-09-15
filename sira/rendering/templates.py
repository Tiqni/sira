"""Template specifications.

One ``TemplateSpec`` per style. The PDF stylesheet (``css.py``) and the DOCX
styler (``docx.py``) both read these numbers, so the two outputs cannot drift
apart. Adding a style means adding one spec here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HeadingStyle = Literal["rule", "caps"]


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    description: str  # one line, shown in --help and docs
    pdf_font: str  # "sans-serif" | "serif" — MuPDF built-in families
    docx_font: str  # "Calibri" | "Georgia"
    accent_hex: str  # name and (for "rule") headings
    text_hex: str
    muted_hex: str  # contact line, dates, details
    base_pt: float
    name_pt: float
    heading_pt: float
    line_height: float  # multiplier
    margin_mm: float  # all four page margins
    heading_style: HeadingStyle
    section_gap_pt: float  # space before a section heading
    item_gap_pt: float  # space between entries / bullets

    @property
    def heading_hex(self) -> str:
        """Heading colour: accent for 'rule', body text colour for 'caps'."""
        return self.accent_hex if self.heading_style == "rule" else self.text_hex


MODERN = TemplateSpec(
    name="modern",
    description="Sans-serif, navy headings with a thin rule (default)",
    pdf_font="sans-serif",
    docx_font="Calibri",
    accent_hex="#1F4E79",
    text_hex="#222222",
    muted_hex="#666666",
    base_pt=10.5,
    name_pt=22,
    heading_pt=12,
    line_height=1.3,
    margin_mm=18,
    heading_style="rule",
    section_gap_pt=10,
    item_gap_pt=4,
)

CLASSIC = TemplateSpec(
    name="classic",
    description="Serif, black uppercase headings — conservative",
    pdf_font="serif",
    docx_font="Georgia",
    accent_hex="#000000",
    text_hex="#111111",
    muted_hex="#555555",
    base_pt=11,
    name_pt=20,
    heading_pt=11,
    line_height=1.3,
    margin_mm=20,
    heading_style="caps",
    section_gap_pt=10,
    item_gap_pt=4,
)

COMPACT = TemplateSpec(
    name="compact",
    description="Sans-serif, teal headings, tight spacing — fits more on one page",
    pdf_font="sans-serif",
    docx_font="Calibri",
    accent_hex="#0B6E6E",
    text_hex="#222222",
    muted_hex="#666666",
    base_pt=9.5,
    name_pt=18,
    heading_pt=10.5,
    line_height=1.25,
    margin_mm=14,
    heading_style="rule",
    section_gap_pt=6,
    item_gap_pt=2,
)

_TEMPLATES: dict[str, TemplateSpec] = {t.name: t for t in (MODERN, CLASSIC, COMPACT)}
STYLES: tuple[str, ...] = tuple(_TEMPLATES)
DEFAULT_STYLE = MODERN.name


def get_template(name: str) -> TemplateSpec:
    """Return the spec for ``name`` (case-insensitive).

    Raises:
        ValueError: unknown name; the message lists the valid styles.
    """
    try:
        return _TEMPLATES[name.lower()]
    except KeyError:
        valid = ", ".join(STYLES)
        raise ValueError(
            f"Unknown resume style {name!r}. Valid styles: {valid}"
        ) from None
