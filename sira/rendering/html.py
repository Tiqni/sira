"""Render a CV as semantic HTML — the input for the PDF renderer."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sira.models.agents.output import CV
from sira.rendering.inline import inline_markdown_to_html
from sira.rendering.templates import TemplateSpec

# The template sits next to this module so hatchling ships it with the package.
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent)),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["inline"] = inline_markdown_to_html


def render_html(cv: CV, spec: TemplateSpec) -> str:
    """Return the resume as HTML using the semantic classes ``css.py`` styles.

    ``caps`` is passed separately (in addition to ``spec``) so the template can
    uppercase section-heading *text* itself: PyMuPDF's ``Story`` engine ignores
    the CSS ``text-transform`` property, so for ``heading_style == "caps"`` the
    heading text must already be uppercase in the HTML for the PDF to match the
    DOCX output (which uses the real ``w:caps`` font property).
    """
    template = _env.get_template("resume.html.j2")
    return template.render(
        cv=cv,
        spec=spec,
        contact_items=cv.contact.display_items(),
        caps=spec.heading_style == "caps",
    )
