"""Build the PDF stylesheet from a ``TemplateSpec``.

MuPDF's HTML engine supports fonts, colours, borders, margins and lists — no
flexbox, floats or ``@page``. Page margins are applied by the placement
rectangle in ``pdf.py``, not here.
"""

from __future__ import annotations

from sira.rendering.templates import TemplateSpec


def build_css(spec: TemplateSpec) -> str:
    # MuPDF's Story engine silently ignores both `text-transform` and
    # `letter-spacing` — they have no effect on the rendered PDF. They stay
    # here to document intent and because they're harmless (and DOCX gets
    # real uppercase/spacing via python-docx font properties in docx.py).
    # The actual uppercasing for the PDF happens on the HTML *text* itself,
    # in resume.html.j2's `section_title` macro (driven by `caps` — see
    # html.py:render_html), so the PDF doesn't depend on CSS support for it.
    heading_extra = (
        "text-transform: uppercase; letter-spacing: 1pt;"
        if spec.heading_style == "caps"
        else ""
    )
    return f"""
body {{
  font-family: {spec.pdf_font};
  font-size: {spec.base_pt}pt;
  line-height: {spec.line_height};
  color: {spec.text_hex};
}}
a {{ color: {spec.accent_hex}; text-decoration: none; }}
/* Contact links stay on one line so the contact line wraps at its separators
   instead of inside a URL. Body links (certifications, projects) must still
   wrap, or long link text is clipped at the margin. */
.contact a {{ white-space: nowrap; }}
code {{ font-family: monospace; font-size: {spec.base_pt - 0.5}pt; }}
.name {{
  font-size: {spec.name_pt}pt;
  font-weight: bold;
  color: {spec.accent_hex};
  margin: 0;
}}
.contact {{
  color: {spec.muted_hex};
  margin: 2pt 0 {spec.section_gap_pt}pt 0;
}}
.section-title {{
  font-size: {spec.heading_pt}pt;
  font-weight: bold;
  color: {spec.heading_hex};
  border-bottom: 0.75pt solid {spec.heading_hex};
  margin: {spec.section_gap_pt}pt 0 {spec.item_gap_pt}pt 0;
  padding-bottom: 1pt;
  {heading_extra}
}}
p {{ margin: 0 0 {spec.item_gap_pt}pt 0; }}
.entry {{ margin-bottom: {spec.item_gap_pt}pt; }}
.entry-head {{ margin: 0; }}
.dates, .details {{ color: {spec.muted_hex}; }}
ul.bullets {{ margin: 1pt 0 0 0; padding-left: 14pt; }}
ul.bullets li {{ margin-bottom: 1pt; }}
.skill-group {{ margin: 0 0 1pt 0; }}
"""
