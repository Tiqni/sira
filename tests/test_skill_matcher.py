"""skill_matcher_agent contract tests with fake models (no real model calls)."""

import json
import re

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from sira.models.agents.output import CV, JobAnalysis, SkillMatchResult, WorkExperience
from sira.reporting.base import NullReporter, use_reporter

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


# ---------------------------------------------------------------------------
# match_skills orchestration
# ---------------------------------------------------------------------------


class _LogReporter(NullReporter):
    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)


def _cv() -> CV:
    return CV(
        full_name="A",
        summary="Engineer who ran Kubernetes clusters and mentored juniors.",
        skills=["Python"],
        experience=[
            WorkExperience(
                company="Acme",
                role="Eng",
                dates="2020",
                highlights=["Mentor to 5 people"],
            )
        ],
        education=[],
    )


def _job(hard: list[str], soft: list[str]) -> JobAnalysis:
    return JobAnalysis(
        job_title="t",
        company_name="c",
        summary="s",
        hard_skills=hard,
        soft_skills=soft,
        key_responsibilities=[],
        keywords_to_target=[],
    )


def _table_judge(
    table: dict[str, tuple[bool, str]], calls: list[list[str]] | None = None
):
    """Judge that answers from a table for whatever skills the prompt asks about.

    Built on ``_judge_model`` (defined above in this file) so it works with the
    agent's DBOSDurability streaming path.
    """

    def answer(messages) -> dict:
        asked = _skills_in_prompt(messages)
        if calls is not None:
            calls.append(asked)
        matches = []
        for skill in asked:
            covered, evidence = table.get(skill, (False, ""))
            matches.append({"skill": skill, "covered": covered, "evidence": evidence})
        return {"matches": matches}

    return _judge_model(answer)


async def test_match_skills_skips_model_when_everything_is_literal():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    with skill_matcher_agent.override(model=_table_judge({}, calls)):
        matches = await match_skills(_cv(), _job(["Python", "kubernetes"], []))
    assert calls == []
    assert matches["Python"].covered and matches["kubernetes"].covered


async def test_match_skills_sends_only_pending_skills_and_merges(subtests):
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    table = {
        "Technical leadership and mentorship": (True, "Mentor to 5 people"),
        "Rust": (False, ""),
    }
    with skill_matcher_agent.override(model=_table_judge(table, calls)):
        matches = await match_skills(
            _cv(), _job(["Python", "Rust"], ["Technical leadership and mentorship"])
        )
    with subtests.test("only_pending_sent"):
        assert calls == [["Rust", "Technical leadership and mentorship"]]
    with subtests.test("literal_kept"):
        assert matches["Python"].covered is True and matches["Python"].evidence == ""
    with subtests.test("judge_merged"):
        assert (
            matches["Technical leadership and mentorship"].evidence
            == "Mentor to 5 people"
        )
        assert matches["Rust"].covered is False


async def test_match_skills_dedupes_skills_listed_as_hard_and_soft():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    with skill_matcher_agent.override(model=_table_judge({"Rust": (False, "")}, calls)):
        await match_skills(_cv(), _job(["Rust"], ["Rust"]))
    assert calls == [["Rust"]]


async def test_match_skills_treats_case_and_whitespace_variants_as_one_skill(subtests):
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    calls: list[list[str]] = []
    table = {"Team  Leadership": (True, "Mentor to 5 people")}
    with skill_matcher_agent.override(model=_table_judge(table, calls)):
        matches = await match_skills(
            _cv(), _job(["Team  Leadership"], ["team leadership"])
        )
    with subtests.test("judged_once"):
        assert calls == [["Team  Leadership"]]
    with subtests.test("both_spellings_covered"):
        assert matches["Team  Leadership"].covered is True
        assert matches["team leadership"].covered is True
        assert matches["team leadership"].evidence == "Mentor to 5 people"


async def test_match_skills_falls_back_to_literal_when_judge_fails():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    broken = _judge_model(
        lambda messages: {"matches": []}
    )  # never answers -> retries exhausted
    reporter = _LogReporter()
    with use_reporter(reporter), skill_matcher_agent.override(model=broken):
        matches = await match_skills(_cv(), _job(["Python", "Rust"], []))
    assert matches["Python"].covered is True
    assert "Rust" not in matches
    assert any("falling back to literal" in line for line in reporter.logs)


async def test_match_skills_logs_how_many_skills_it_judges():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    reporter = _LogReporter()
    with use_reporter(reporter), skill_matcher_agent.override(model=_table_judge({})):
        await match_skills(_cv(), _job(["Python", "Rust"], ["Grit"]))
    assert any("2 of 3" in line for line in reporter.logs)


async def test_match_skills_log_omits_literal_note_when_nothing_matched_literally():
    from sira.workflows.agents import skill_matcher_agent
    from sira.workflows.skill_matching import match_skills

    reporter = _LogReporter()
    with use_reporter(reporter), skill_matcher_agent.override(model=_table_judge({})):
        await match_skills(_cv(), _job(["Rust", "Grit"], []))
    line = next(line for line in reporter.logs if "Skill Matcher" in line)
    assert "2 of 2" in line
    assert "matched literally" not in line


def test_judge_prompt_rejects_adjacent_work():
    """The judge must not count related work as the skill itself."""
    from sira.workflows.agents import skill_matcher_agent

    prompt = " ".join(skill_matcher_agent._system_prompts)
    assert "Adjacent work does not count" in prompt


def test_build_matcher_prompt_numbers_skills():
    from sira.workflows.skill_matching import build_matcher_prompt

    prompt = build_matcher_prompt("Summary: x", ["A", "B"])
    assert prompt.endswith("Skills to judge:\n1. A\n2. B")
    assert prompt.startswith("CV:\nSummary: x")
