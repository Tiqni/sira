"""Deterministic clean-up of a CV's skill groups (issue #20).

The parser extracts every technical term it sees, so one resume can yield
``Python`` and ``Python 3.13+``, or ``Spec-Driven Development`` and
``Spec-Driven Development (SDD)``, often in different groups. This module
collapses such variants without inventing or losing content:

* variants that differ only by case, hyphen/underscore/whitespace, or a
  trailing version (``3.13+``, ``1.22+``) are one skill;
* ``X (ABC)`` and ``X`` are one skill when both are present — the acronym
  form is kept because it carries an extra keyword;
* the first occurrence (document order, across groups) decides position;
* aliases (``Go``/``Golang``) and word forms (``mentor``/``Mentoring``) are
  never merged — that would need a dictionary and could drop real content.

Pure Python, no model calls, idempotent.
"""

from __future__ import annotations

import re

from sira.models.agents.output import CV, SkillGroup

# A trailing version: digits with a dot ("3.13", "1.22.0") or digits with a
# plus ("17+"), optionally "v"-prefixed. A bare number ("Office 365") is part
# of the name and is left alone.
_VERSION_SUFFIX = re.compile(r"\s+v?(?:\d+\.\d+[\d.]*\+?|\d+\+)$", re.IGNORECASE)
# A trailing one-token acronym in parentheses: "(SDD)", "(RAG)", "(GenAI)".
_ACRONYM_SUFFIX = re.compile(r"\s+\([A-Za-z][A-Za-z0-9&/+.-]*\)$")
_SEPARATORS = re.compile(r"[-_\s]+")


def _tidy(skill: str) -> str:
    """Collapse internal whitespace and trim the ends."""
    return " ".join(skill.split())


def _strip_acronym(skill: str) -> str:
    return _ACRONYM_SUFFIX.sub("", skill)


def skill_key(skill: str) -> str:
    """Case-, separator- and version-insensitive identity of a skill.

    ``skill_key("Python 3.13+") == skill_key("python")`` and
    ``skill_key("agentic-AI platform") == skill_key("Agentic AI Platform")``.
    The acronym suffix is *not* part of the key on purpose: whether
    ``X (ABC)`` merges with ``X`` depends on both being present, which only
    ``clean_skill_groups`` can see.
    """
    text = _VERSION_SUFFIX.sub("", _tidy(skill))
    return _SEPARATORS.sub(" ", text).casefold().strip()


def _has_version_suffix(skill: str) -> bool:
    return _VERSION_SUFFIX.search(skill) is not None


def clean_skill_groups(cv: CV) -> CV:
    """Return a copy of ``cv`` with duplicate skill variants collapsed.

    Order of groups and of skills inside a group is preserved; a group left
    without skills is dropped.
    """
    # Pass 1: every skill as (group index, tidied text, key, key of the
    # acronym-less base). A skill "X (ABC)" only merges with "X" when some
    # other skill has the base key.
    entries: list[tuple[int, str, str, str]] = []
    base_keys: set[str] = set()
    for group_index, group in enumerate(cv.skill_groups):
        for raw in group.skills:
            text = _tidy(raw)
            if not text:
                continue
            key = skill_key(text)
            base_key = skill_key(_strip_acronym(text))
            entries.append((group_index, text, key, base_key))
            if base_key == key:
                base_keys.add(key)

    def merge_key(key: str, base_key: str) -> str:
        return base_key if base_key in base_keys else key

    # Pass 2: first occurrence wins the position; the displayed spelling is
    # the version-less form (versions are noise) and the acronym form when
    # one exists (an acronym is an extra keyword).
    slot_of: dict[str, tuple[int, int]] = {}  # merge key -> (group, index)
    kept: list[list[str]] = [[] for _ in cv.skill_groups]
    for group_index, text, key, base_key in entries:
        mkey = merge_key(key, base_key)
        if mkey not in slot_of:
            slot_of[mkey] = (group_index, len(kept[group_index]))
            kept[group_index].append(text)
            continue
        g, i = slot_of[mkey]
        current = kept[g][i]
        prefer_new = (
            _has_version_suffix(current) and not _has_version_suffix(text)
        ) or (_strip_acronym(text) != text and _strip_acronym(current) == current)
        if prefer_new:
            kept[g][i] = text

    groups = [
        SkillGroup(category=group.category, skills=skills)
        for group, skills in zip(cv.skill_groups, kept, strict=True)
        if skills
    ]
    return cv.model_copy(update={"skill_groups": groups})
