"""Integration tests for report_agent using TestModel (no real LLM calls)."""

import pytest
from pydantic_ai.models.test import TestModel
from pytest_subtests import SubTests

from sira.models.agents.output import ReportNarrative

pytestmark = pytest.mark.anyio

_PROMPT = (
    "Match score: 65/100\nVerdict: Partial Match\n"
    "CV Diff: {} Gap Analysis: {} Audit: {} Review: {} Job: {}"
)


async def test_report_agent_returns_report_narrative() -> None:
    from sira.workflows.agents import report_agent  # noqa: PLC0415 — avoids import-time LLM init

    custom = {
        "suggestions_to_strengthen": ["Get Kubernetes certification"],
        "audit_summary": "Hallucination score 1/10. AI cliché score 2/10.",
        "recommendation_rationale": "Strong backend skills but missing infra experience.",
    }
    with report_agent.override(model=TestModel(custom_output_args=custom)):
        result = await report_agent.run(_PROMPT)

    assert isinstance(result.output, ReportNarrative)


async def test_report_agent_output_has_required_fields(subtests: SubTests) -> None:
    from sira.workflows.agents import report_agent  # noqa: PLC0415 — avoids import-time LLM init

    custom = {
        "suggestions_to_strengthen": ["Add Terraform side project"],
        "audit_summary": "Excellent quality. No hallucinations.",
        "recommendation_rationale": "Covers 90% of job keywords.",
    }
    with report_agent.override(model=TestModel(custom_output_args=custom)):
        result = await report_agent.run(_PROMPT)

    out = result.output
    with subtests.test("suggestions"):
        assert out.suggestions_to_strengthen == ["Add Terraform side project"]
    with subtests.test("audit_summary"):
        assert out.audit_summary
    with subtests.test("rationale"):
        assert out.recommendation_rationale


def test_report_prompt_no_longer_asks_model_to_score() -> None:
    """The score and verdict are computed in Python; the prompt must not re-derive them."""
    from sira.workflows.agents import report_agent  # noqa: PLC0415

    prompt = " ".join(
        report_agent._system_prompts
    )  # pydantic-ai keeps static prompts here
    assert "subtract 5 points" not in prompt
    assert "copy them VERBATIM" not in prompt
    assert "Match score" in prompt
