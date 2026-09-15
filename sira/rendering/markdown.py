"""CV → plain Markdown: the human-readable source file next to the PDF/DOCX."""

from __future__ import annotations

from sira.models.agents.output import CV

_BREAK = "  "  # two trailing spaces = line break inside a paragraph


def render_markdown(cv: CV) -> str:
    lines: list[str] = [f"# {cv.full_name}"]
    items = cv.contact.display_items()
    if items:
        lines.append(" · ".join(items))

    if cv.summary.strip():
        lines += ["", "## Summary", cv.summary.strip()]

    groups = [group for group in cv.skill_groups if group.skills]
    if groups:
        lines += ["", "## Skills"]
        lines += [f"**{g.category}:** {', '.join(g.skills)}{_BREAK}" for g in groups]

    if cv.experience:
        lines += ["", "## Experience"]
        for exp in cv.experience:
            lines += ["", f"### {exp.role} — {exp.company}"]
            if exp.dates:
                lines.append(f"*{exp.dates}*")
            lines += [f"- {item}" for item in exp.highlights]

    if cv.projects:
        lines += ["", "## Projects"]
        for project in cv.projects:
            link = f" ({project.link})" if project.link else ""
            lines.append(f"**{project.name}** — {project.description}{link}{_BREAK}")

    if cv.education:
        lines += ["", "## Education"]
        for edu in cv.education:
            dates = f" *({edu.dates})*" if edu.dates else ""
            lines.append(f"**{edu.degree}** — {edu.institution}{dates}{_BREAK}")
            if edu.details:
                lines.append(f"{edu.details}{_BREAK}")

    for heading, entries in (
        ("Certifications", cv.certifications),
        ("Publications", cv.publications),
    ):
        if entries:
            lines += ["", f"## {heading}"]
            lines += [f"- {entry}" for entry in entries]

    return "\n".join(lines) + "\n"
