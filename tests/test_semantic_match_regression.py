"""Regression for GitHub issue #2: the match score collapsed to 0/100.

A CV that plainly contains "context engineering", "mentor to ~30 engineers",
cross-functional platform work, production RAG and monitoring was reported as
missing all of them, because gap analysis compared the job's long descriptive
skill phrases against the CV's ``skills`` list by exact text.

Caveat: the judge here is scripted. This proves the pipeline (literal pre-pass
+ judge + Python formula) turns concept matches into a sane score; it does not
prove a real model's judgement.
"""

import json
import re

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from sira.models.agents.output import (
    CV,
    ContactInfo,
    Education,
    JobAnalysis,
    SkillGroup,
    WorkExperience,
)
from sira.utils.cv_diff import (
    compute_gap_analysis,
    compute_match_score,
    compute_recommendation,
)

pytestmark = pytest.mark.anyio

_SKILL_LINE = re.compile(r"^\d+\. (.+)$", re.MULTILINE)

HARD_SKILLS = [
    "Large language models (LLMs) — open-weight models",
    "Fine-tuning and distillation of LLMs",
    "Context engineering, context management and state handling for multi-turn dialogue",
    "Retrieval-augmented generation (RAG) in production",
    "Monitoring and evaluation of LLM systems in production",
    "Python",
    "Reinforcement learning from human feedback (RLHF)",
    "Speech recognition and voice interfaces",
]
SOFT_SKILLS = [
    "Technical leadership and mentorship",
    "Cross-functional collaboration (product, design, analytics)",
    "Communicating trade-offs to non-technical stakeholders",
    "Ownership of ambiguous problems",
]

# What a competent judge should say about this CV. Skills not listed -> not covered.
JUDGE_TABLE = {
    HARD_SKILLS[0]: "Fine-tuned and distilled open-weight Llama models",
    HARD_SKILLS[1]: "Fine-tuned and distilled open-weight Llama models",
    HARD_SKILLS[2]: "Designed context engineering for a multi-turn shopping assistant",
    HARD_SKILLS[3]: "Shipped production RAG over 2M product documents",
    HARD_SKILLS[4]: "Built monitoring and evaluation dashboards for hallucination rate",
    SOFT_SKILLS[0]: "Mentor to ~30 engineers across three teams",
    SOFT_SKILLS[1]: "Cross-functional platform work with product, design and analytics",
}


def _cv() -> CV:
    return CV(
        full_name="Sam Staff",
        contact=ContactInfo(email="sam@example.com"),
        summary=(
            "Staff engineer building production LLM systems: RAG pipelines, "
            "context engineering and evaluation for a grocery app."
        ),
        skill_groups=[
            SkillGroup(
                category="Skills",
                skills=[
                    "Python",
                    "PyTorch",
                    "LangGraph",
                    "Kubernetes",
                    "Observability",
                ],
            )
        ],
        experience=[
            WorkExperience(
                company="Picnic-like Co",
                role="Staff AI Engineer",
                dates="2022-2026",
                highlights=[
                    "Designed context engineering for a multi-turn shopping assistant: "
                    "conversation state, memory and tool results managed per turn.",
                    "Shipped production RAG over 2M product documents with hybrid retrieval.",
                    "Fine-tuned and distilled open-weight Llama models for intent classification.",
                    "Mentor to ~30 engineers across three teams; ran the internal LLM guild.",
                    "Cross-functional platform work with product, design and analytics "
                    "to define assistant metrics.",
                    "Built monitoring and evaluation dashboards for hallucination rate and latency.",
                ],
            )
        ],
        education=[
            Education(degree="MSc Computer Science", institution="State University")
        ],
    )


def _job() -> JobAnalysis:
    return JobAnalysis(
        job_title="Staff AI Engineer – Agentic Shopping",
        company_name="Picnic-like Co",
        summary="Own the shopping assistant.",
        hard_skills=HARD_SKILLS,
        soft_skills=SOFT_SKILLS,
        key_responsibilities=["Build the assistant"],
        keywords_to_target=["LLM", "RAG", "Python", "Kubernetes", "RLHF", "voice"],
    )


def _scripted_judge() -> FunctionModel:
    """A fake judge that answers from JUDGE_TABLE.

    The agent carries DBOSDurability with an event-stream handler, so pydantic-ai
    always uses the streaming path; a FunctionModel needs ``stream_function``.
    """

    def answer(messages) -> dict:
        # messages[0].parts holds the system prompt AND the user prompt; only the
        # user prompt carries the numbered skill list.
        prompt = next(
            p.content
            for p in messages[0].parts
            if isinstance(getattr(p, "content", None), str)
            and "Skills to judge:" in p.content
        )
        asked = _SKILL_LINE.findall(prompt.split("Skills to judge:", 1)[1])
        return {
            "matches": [
                {
                    "skill": s,
                    "covered": s in JUDGE_TABLE,
                    "evidence": JUDGE_TABLE.get(s, ""),
                }
                for s in asked
            ]
        }

    def fn(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=info.output_tools[0].name, args=answer(messages))
            ]
        )

    async def sfn(messages, info: AgentInfo):
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name, json_args=json.dumps(answer(messages))
            )
        }

    return FunctionModel(fn, stream_function=sfn)


def test_literal_matching_reproduces_the_collapse(subtests):
    """Without semantic matches the old behaviour scores this pair as Weak."""
    gap = compute_gap_analysis(_cv(), _cv(), _job())  # skill_matches=None → literal
    score = compute_match_score(gap)
    with subtests.test("hard_coverage_tiny"):
        assert gap.hard_skill_coverage_percent == 12.5  # only "Python"
    with subtests.test("score_low"):
        assert score < 30
    with subtests.test("verdict_weak"):
        assert compute_recommendation(score, gap) == "Weak Match"


async def test_semantic_matching_scores_the_pair_sanely(subtests):
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    with skill_matcher_agent.override(model=_scripted_judge()):
        matches = await match_skills(_cv(), _job())
    gap = compute_gap_analysis(_cv(), _cv(), _job(), skill_matches=matches)
    score = compute_match_score(gap)

    with subtests.test("context_engineering_covered"):
        assert HARD_SKILLS[2] in gap.covered_hard_skills
    with subtests.test("mentorship_covered"):
        assert SOFT_SKILLS[0] in gap.covered_soft_skills
    with subtests.test("truly_missing_stay_missing"):
        assert gap.missing_hard_skills == [HARD_SKILLS[6], HARD_SKILLS[7]]
    with subtests.test("hard_coverage_at_least_50"):
        assert gap.hard_skill_coverage_percent >= 50.0
    with subtests.test("score_well_above_zero"):
        assert score >= 50
    with subtests.test("verdict_not_weak"):
        assert compute_recommendation(score, gap) != "Weak Match"
    with subtests.test("exact_numbers_for_the_record"):
        # hard 6/8 = 75, soft 2/4 = 50, keywords 4/6 = 66.7 → 45 + 10 + 13.34 → 68
        assert (gap.hard_skill_coverage_percent, gap.soft_skill_coverage_percent) == (
            75.0,
            50.0,
        )
        assert score == 68
