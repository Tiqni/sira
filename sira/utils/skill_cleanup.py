"""Deterministic clean-up of a CV's skill groups (issue #20).

The parser extracts every technical term it sees, so one resume can yield
``Python`` and ``Python 3.13+``, or ``Spec-Driven Development`` and
``Spec-Driven Development (SDD)``, often in different groups. This module
collapses such variants without inventing or losing content:

* variants that differ only by case, hyphen/underscore/whitespace, or a
  trailing version (``3.13+``, ``1.22+``) are one skill;
* the bare ``X`` merges into ``X (ABC)`` when both are present — the acronym
  form is kept because it carries an extra keyword. Two different acronym
  forms (``X (ABC)`` and ``X (DEF)``) stay two skills;
* the first occurrence (document order, across groups) decides position;
* aliases (``Go``/``Golang``) and word forms (``mentor``/``Mentoring``) are
  never merged — that would need a dictionary and could drop real content.

Pure Python, no model calls, idempotent. ``variant_key`` is deliberately
different from ``skill_matching.skill_key``: that one only folds case and
whitespace because job-posting skills must stay literal for matching.
"""

from __future__ import annotations

import re

from sira.models.agents.output import CV, SkillGroup
from sira.utils.skill_matching import normalise_text

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


def _has_acronym(skill: str) -> bool:
    return _ACRONYM_SUFFIX.search(skill) is not None


def _has_version(skill: str) -> bool:
    return _VERSION_SUFFIX.search(skill) is not None


def variant_key(skill: str) -> str:
    """Case-, separator- and version-insensitive identity of a skill.

    ``variant_key("Python 3.13+") == variant_key("python")`` and
    ``variant_key("agentic-AI platform") == variant_key("Agentic AI Platform")``.
    An acronym suffix stays part of the key: ``X (ABC)`` and ``X (DEF)`` are
    different keys, and whether ``X`` joins one of them is decided by
    ``clean_skill_groups``, which can see both.
    """
    text = _VERSION_SUFFIX.sub("", _tidy(skill))
    return normalise_text(_SEPARATORS.sub(" ", text))


def clean_skill_groups(cv: CV) -> CV:
    """Return a copy of ``cv`` with duplicate skill variants collapsed.

    Order of groups and of skills inside a group is preserved; a group left
    without skills is dropped.
    """
    kept: list[list[str]] = [[] for _ in cv.skill_groups]
    # variant key -> (group index, index inside the group) of the kept entry
    slot_of: dict[str, tuple[int, int]] = {}
    # base key -> variant key of the entry that represents the bare base:
    # either the bare "X" itself or the first acronym form "X (ABC)".
    base_owner: dict[str, str] = {}

    def add(group_index: int, text: str) -> tuple[int, int]:
        slot = (group_index, len(kept[group_index]))
        kept[group_index].append(text)
        return slot

    def replace(slot: tuple[int, int], text: str) -> None:
        g, i = slot
        kept[g][i] = text

    for group_index, group in enumerate(cv.skill_groups):
        for raw in group.skills:
            text = _tidy(raw)
            if not text:
                continue
            key = variant_key(text)
            base_key = variant_key(_strip_acronym(text))

            if key in slot_of:
                # Same skill again: keep the version-less spelling.
                slot = slot_of[key]
                g, i = slot
                if _has_version(kept[g][i]) and not _has_version(text):
                    replace(slot, text)
                continue

            owner = base_owner.get(base_key)
            if key == base_key:
                # Bare "X": join an existing acronym form, else stand alone.
                if owner is not None:
                    slot_of[key] = slot_of[owner]
                else:
                    slot_of[key] = add(group_index, text)
                    base_owner[base_key] = key
                continue

            # "X (ABC)": absorb the bare "X" if it stands alone; a base already
            # owned by another acronym form is a different skill.
            if owner is not None and owner == base_key:
                slot = slot_of[owner]
                replace(slot, text)
                slot_of[key] = slot
                base_owner[base_key] = key
            else:
                slot_of[key] = add(group_index, text)
                if owner is None:
                    base_owner[base_key] = key

    groups = [
        SkillGroup(category=group.category, skills=skills)
        for group, skills in zip(cv.skill_groups, kept, strict=True)
        if skills
    ]
    return cv.model_copy(update={"skill_groups": groups})
