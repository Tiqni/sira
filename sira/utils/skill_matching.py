"""Pure-Python helpers for semantic skill matching.

No LLM calls here. ``render_cv_text`` turns a ``CV`` into one plain-text block
that both the literal pre-pass and the skill matcher agent read, so both see
the same text. ``literal_matches`` is the pre-pass: a skill whose text appears
in the CV is covered without asking a model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sira.models.agents.output import CV, SkillMatch


def normalise_text(text: str) -> str:
    """Collapse whitespace and fold case so comparisons ignore formatting."""
    return " ".join(text.split()).casefold()


def skill_key(text: str) -> str:
    """Canonical key for a skill string: whitespace-collapsed, case-folded.

    Used to treat "Leadership" and "leadership" (or "Team  Leadership" and
    "team leadership") as the same skill when deduplicating and when fanning
    a judge's or the literal pre-pass's answer back out to every spelling a
    job posting used.
    """
    return normalise_text(text)


def _contains_term(haystack: str, needle: str) -> bool:
    """True when ``needle`` occurs in ``haystack`` as a whole term.

    Both sides of the hit must be a non-alphanumeric character or the text
    edge, so "java" does not match "javascript" and "c" does not match
    "code" or "c++", while "c++", ".net" and "node.js" still match themselves.
    Lookahead excludes language-name characters ("+", "#") to prevent short
    skills from matching partial names (C not in C++, C not in C#).
    """
    pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9+#])"
    return re.search(pattern, haystack) is not None


def render_cv_text(cv: CV) -> str:
    """Render every CV section as labelled plain text, one item per line."""
    lines: list[str] = [f"Summary: {cv.summary.strip()}"]
    for group in cv.skill_groups:
        if group.skills:
            lines.append(f"Skills — {group.category}: " + ", ".join(group.skills))
    if cv.experience:
        lines.append("Experience:")
        for exp in cv.experience:
            lines.append(f"- {exp.company} — {exp.role} ({exp.dates})")
            lines.extend(f"  - {bullet}" for bullet in exp.highlights)
    if cv.projects:
        lines.append("Projects:")
        lines.extend(f"- {p.name} — {p.description}" for p in cv.projects)
    if cv.education:
        lines.append("Education:")
        for edu in cv.education:
            head = f"- {edu.degree} — {edu.institution}"
            if edu.dates:
                head += f" ({edu.dates})"
            if edu.details:
                head += f". {edu.details}"
            lines.append(head)
    optional_sections = (
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
    the match is the skill text itself. Matches require whole-term boundaries
    to avoid false positives (e.g., "Java" does not match in "JavaScript").
    """
    haystack = normalise_text(cv_text)
    found: dict[str, SkillMatch] = {}
    for skill in skills:
        needle = normalise_text(skill)
        if needle and _contains_term(haystack, needle):
            found[skill] = SkillMatch(skill=skill, covered=True, evidence="")
    return found
