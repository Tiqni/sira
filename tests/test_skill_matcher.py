"""skill_matcher_agent contract tests with fake models (no real model calls)."""

import json
import re

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from sira.models.agents.output import SkillMatchResult

pytestmark = pytest.mark.anyio


def _judge_model(answer):
    """FunctionModel whose plain and streaming paths share one answer function.

    ``answer(messages) -> dict`` returns the structured-output args. The agent
    carries DBOSDurability with an event-stream handler, so pydantic-ai always
    calls the streaming path; a FunctionModel without ``stream_function`` fails.
    """

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


# Used by the match_skills tests appended in the next task.
_SKILL_LINE = re.compile(r"^\d+\. (.+)$", re.MULTILINE)


def _skills_in_prompt(messages) -> list[str]:
    """Read the numbered 'Skills to judge' list back out of the user prompt."""
    for part in messages[0].parts:
        content = getattr(part, "content", "")
        if isinstance(content, str) and "Skills to judge:" in content:
            return _SKILL_LINE.findall(content.split("Skills to judge:", 1)[1])
    return []


def _judge(answers: list[dict]):
    """A TestModel that returns the given match dicts on every call."""
    return TestModel(custom_output_args={"matches": answers})


async def test_validator_accepts_complete_answer_and_canonicalises_names(subtests):
    from sira.workflows.agents import skill_matcher_agent

    model = _judge(
        [
            {"skill": "kubernetes", "covered": True, "evidence": "ran K8s clusters"},
            {
                "skill": "Team  Leadership",
                "covered": False,
                "evidence": "should be blanked",
            },
        ]
    )
    with skill_matcher_agent.override(model=model):
        result = await skill_matcher_agent.run(
            "CV:\nx\n\nSkills to judge:\n1. Kubernetes\n2. Team Leadership",
            deps=("Kubernetes", "Team Leadership"),
        )
    out = result.output
    assert isinstance(out, SkillMatchResult)
    with subtests.test("names_canonical"):
        assert [m.skill for m in out.matches] == ["Kubernetes", "Team Leadership"]
    with subtests.test("evidence_kept_when_covered"):
        assert out.matches[0].evidence == "ran K8s clusters"
    with subtests.test("evidence_blanked_when_not_covered"):
        assert out.matches[1].evidence == ""


async def test_validator_retries_on_missing_skill_then_accepts():
    from sira.workflows.agents import skill_matcher_agent

    calls = {"n": 0}

    def answer(messages) -> dict:
        calls["n"] += 1
        skills = ["Kubernetes", "Terraform"]
        answered = skills[:1] if calls["n"] == 1 else skills  # first answer incomplete
        return {
            "matches": [
                {"skill": s, "covered": False, "evidence": ""} for s in answered
            ]
        }

    with skill_matcher_agent.override(model=_judge_model(answer)):
        result = await skill_matcher_agent.run(
            "CV:\nx\n\nSkills to judge:\n1. Kubernetes\n2. Terraform",
            deps=("Kubernetes", "Terraform"),
        )
    assert calls["n"] == 2
    assert [m.skill for m in result.output.matches] == ["Kubernetes", "Terraform"]


async def test_validator_rejects_extra_and_duplicate_skills_until_retries_exhausted():
    from sira.workflows.agents import skill_matcher_agent

    model = _judge(
        [
            {"skill": "Kubernetes", "covered": True, "evidence": "k"},
            {"skill": "Kubernetes", "covered": True, "evidence": "k"},
            {"skill": "Rust", "covered": True, "evidence": "r"},
        ]
    )
    with skill_matcher_agent.override(model=model):
        with pytest.raises(UnexpectedModelBehavior):
            await skill_matcher_agent.run(
                "CV:\nx\n\nSkills to judge:\n1. Kubernetes", deps=("Kubernetes",)
            )


async def test_validator_truncates_long_evidence():
    from sira.workflows.agents import MAX_EVIDENCE_CHARS, skill_matcher_agent

    model = _judge([{"skill": "Python", "covered": True, "evidence": "x" * 500}])
    with skill_matcher_agent.override(model=model):
        result = await skill_matcher_agent.run(
            "CV:\nx\n\nSkills to judge:\n1. Python", deps=("Python",)
        )
    assert len(result.output.matches[0].evidence) == MAX_EVIDENCE_CHARS


def test_skill_matcher_is_fast_tier():
    from sira.workflows.agents import _AGENT_TIERS

    assert _AGENT_TIERS["Skill Matcher"] == "fast"


async def test_run_agent_forwards_deps(monkeypatch):
    from sira.workflows import agents as agents_mod

    seen = {}

    async def fake_run(prompt, **kwargs):
        seen.update(kwargs)

        class R:
            output = None

        return R()

    monkeypatch.setattr(agents_mod.skill_matcher_agent, "run", fake_run)
    await agents_mod.run_agent(
        agents_mod.skill_matcher_agent, "p", agent_label="Skill Matcher", deps=("A",)
    )
    assert seen["deps"] == ("A",)


async def test_run_agent_omits_deps_when_none(monkeypatch):
    from sira.workflows import agents as agents_mod

    seen = {}

    async def fake_run(prompt, **kwargs):
        seen.update(kwargs)

        class R:
            output = None

        return R()

    monkeypatch.setattr(agents_mod.report_agent, "run", fake_run)
    await agents_mod.run_agent(agents_mod.report_agent, "p", agent_label="Report")
    assert "deps" not in seen
