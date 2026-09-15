"""Deterministic clean-up of skill groups (issue #20) — no model calls."""

from sira.models.agents.output import CV, SkillGroup
from sira.utils.skill_cleanup import clean_skill_groups, skill_key


def _cv(*groups: tuple[str, list[str]]) -> CV:
    return CV(
        full_name="Jane Doe",
        summary="",
        skill_groups=[SkillGroup(category=c, skills=s) for c, s in groups],
        experience=[],
        education=[],
    )


def _groups(cv: CV) -> list[tuple[str, list[str]]]:
    return [(g.category, list(g.skills)) for g in cv.skill_groups]


# --- skill_key -----------------------------------------------------------


def test_key_is_case_hyphen_and_whitespace_insensitive(subtests):
    for a, b in (
        ("Agentic AI Platform", "agentic-AI platform"),
        ("Spec Driven  Development", "spec_driven development"),
        ("Python", " python "),
    ):
        with subtests.test(a=a, b=b):
            assert skill_key(a) == skill_key(b)


def test_key_strips_version_suffixes(subtests):
    for versioned, base in (
        ("Python 3.13+", "Python"),
        ("Go 1.22+", "Go"),
        ("Python 3.13", "Python"),
        ("Java 17+", "Java"),
    ):
        with subtests.test(versioned=versioned):
            assert skill_key(versioned) == skill_key(base)


def test_key_keeps_product_numbers_and_inline_versions(subtests):
    # A bare number without a dot or "+" is part of the name, not a version.
    for a, b in (("Office 365", "Office"), ("GPT-5.5", "GPT"), ("HTTP/2", "HTTP")):
        with subtests.test(a=a, b=b):
            assert skill_key(a) != skill_key(b)


# --- clean_skill_groups ----------------------------------------------------


def test_version_variants_collapse_to_the_base_spelling():
    cv = _cv(("Languages", ["Go 1.22+", "Go", "Python", "Python 3.13+"]))
    assert _groups(clean_skill_groups(cv)) == [("Languages", ["Go", "Python"])]


def test_acronym_variant_wins_when_the_base_skill_also_exists(subtests):
    with subtests.test("acronym first"):
        cv = _cv(("P", ["Spec-Driven Development (SDD)", "Spec-Driven Development"]))
        assert _groups(clean_skill_groups(cv)) == [
            ("P", ["Spec-Driven Development (SDD)"])
        ]
    with subtests.test("base first"):
        cv = _cv(("P", ["Spec-Driven Development", "Spec-Driven Development (SDD)"]))
        assert _groups(clean_skill_groups(cv)) == [
            ("P", ["Spec-Driven Development (SDD)"])
        ]
    with subtests.test("acronym without a base is untouched"):
        cv = _cv(("P", ["Retrieval-Augmented Generation (RAG)", "Vector search"]))
        assert _groups(clean_skill_groups(cv)) == [
            ("P", ["Retrieval-Augmented Generation (RAG)", "Vector search"])
        ]


def test_case_and_hyphen_duplicates_keep_the_first_spelling():
    cv = _cv(("AI", ["agentic-AI platform", "Agentic AI Platform", "RAG"]))
    assert _groups(clean_skill_groups(cv)) == [("AI", ["agentic-AI platform", "RAG"])]


def test_duplicates_across_groups_keep_the_first_group():
    cv = _cv(("AI", ["PydanticAI", "LangChain"]), ("Tools", ["pytest", "PydanticAI"]))
    assert _groups(clean_skill_groups(cv)) == [
        ("AI", ["PydanticAI", "LangChain"]),
        ("Tools", ["pytest"]),
    ]


def test_aliases_and_word_forms_are_not_merged(subtests):
    # No stemming, no alias table: content must never be invented or lost.
    for skills in (
        ["Go", "Golang"],
        ["Mentoring", "mentor"],
        ["Kubernetes", "Kubernetes runtime"],
    ):
        with subtests.test(skills=skills):
            cv = _cv(("G", skills))
            assert _groups(clean_skill_groups(cv)) == [("G", skills)]


def test_blank_entries_and_empty_groups_are_dropped_and_order_is_kept():
    cv = _cv(("A", ["  ", "Python "]), ("B", ["", " "]), ("C", ["SQL"]))
    assert _groups(clean_skill_groups(cv)) == [("A", ["Python"]), ("C", ["SQL"])]


def test_clean_is_idempotent_and_leaves_other_fields_alone():
    cv = _cv(("L", ["Python", "python 3.13+"])).model_copy(update={"summary": "Hi"})
    once = clean_skill_groups(cv)
    assert clean_skill_groups(once) == once
    assert once.summary == "Hi" and once.full_name == "Jane Doe"
    assert once.skills == ["Python"]


def test_regression_real_resume_duplicates_from_issue_20():
    cv = _cv(
        (
            "AI, LLMs & Agent Technologies",
            ["agentic-AI platform", "Agentic AI Platform", "PydanticAI", "LangGraph"],
        ),
        (
            "Architecture, Design & Practices",
            [
                "Spec-Driven Development (SDD)",
                "Spec-Driven Development",
                "Observability",
            ],
        ),
        ("Frameworks, Libraries & Tools", ["PydanticAI", "FastAPI", "pytest"]),
        (
            "Languages & Runtimes",
            ["Python", "Python 3.13+", "Go", "Golang", "Go 1.22+", "SQL"],
        ),
        ("Cloud, DevOps & Infrastructure", ["Kubernetes", "Kubernetes runtime", "AWS"]),
        ("Leadership", ["Mentoring", "mentor", "Leadership"]),
    )
    assert _groups(clean_skill_groups(cv)) == [
        (
            "AI, LLMs & Agent Technologies",
            ["agentic-AI platform", "PydanticAI", "LangGraph"],
        ),
        (
            "Architecture, Design & Practices",
            ["Spec-Driven Development (SDD)", "Observability"],
        ),
        ("Frameworks, Libraries & Tools", ["FastAPI", "pytest"]),
        ("Languages & Runtimes", ["Python", "Go", "Golang", "SQL"]),
        ("Cloud, DevOps & Infrastructure", ["Kubernetes", "Kubernetes runtime", "AWS"]),
        ("Leadership", ["Mentoring", "mentor", "Leadership"]),
    ]
