"""Pure-Python helpers for semantic skill matching.

No LLM calls here. ``render_cv_text`` turns a ``CV`` into one plain-text block
that both the literal pre-pass and the skill matcher agent read, so both see
the same text. ``literal_matches`` is the pre-pass: a skill whose text appears
in the CV is covered without asking a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from sira.models.agents.output import CV, SkillMatch


def normalise_text(text: str) -> str:
    """Collapse whitespace and fold case so comparisons ignore formatting."""
    return " ".join(text.split()).casefold()


def render_cv_text(cv: CV) -> str:
    """Render every CV section as labelled plain text, one item per line."""
    lines: list[str] = [
        f"Summary: {cv.summary.strip()}",
        "Skills: " + ", ".join(cv.skills),
    ]
    if cv.experience:
        lines.append("Experience:")
        for exp in cv.experience:
            lines.append(f"- {exp.company} — {exp.role} ({exp.dates})")
            lines.extend(f"  - {bullet}" for bullet in exp.highlights)
    optional_sections = (
        ("Projects", cv.projects),
        ("Education", cv.education),
        ("Certifications", cv.certifications),
        ("Publications", cv.publications),
    )
    for label, items in optional_sections:
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


def literal_matches(skills: Sequence[str], cv_text: str) -> dict[str, SkillMatch]:
    """Return a covered ``SkillMatch`` for each skill whose text appears in the CV.

    Keys are the skill strings exactly as passed in. Evidence is left empty:
    the match is the skill text itself.
    """
    haystack = normalise_text(cv_text)
    found: dict[str, SkillMatch] = {}
    for skill in skills:
        needle = normalise_text(skill)
        if needle and needle in haystack:
            found[skill] = SkillMatch(skill=skill, covered=True, evidence="")
    return found
