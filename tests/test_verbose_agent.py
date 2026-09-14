"""Tests for run_agent(): lifecycle events and the model kwarg."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent

from sira.reporting.base import use_reporter
from sira.workflows import agents as agents_mod
from sira.workflows.agents import _current_agent_label, run_agent
from tests.reporting.test_base import RecordingReporter


class TestRunAgentLifecycle:
    @pytest.mark.anyio
    async def test_delegates_to_agent_run(self):
        agent = MagicMock(spec=Agent)
        expected = MagicMock()
        agent.run = AsyncMock(return_value=expected)

        rec = RecordingReporter()
        with use_reporter(rec):
            result = await run_agent(agent, "prompt", agent_label="A")

        agent.run.assert_awaited_once()
        assert result is expected
        kinds = [e[0] for e in rec.events]
        assert kinds == ["agent_start", "agent_done"]

    @pytest.mark.anyio
    async def test_passes_usage_params(self):
        agent = MagicMock(spec=Agent)
        agent.run = AsyncMock()
        with use_reporter(RecordingReporter()):
            await run_agent(
                agent, "test", agent_label="A", usage="u", usage_limits="ul"
            )
        agent.run.assert_awaited_once_with("test", usage="u", usage_limits="ul")

    @pytest.mark.anyio
    async def test_label_contextvar_is_set_during_run_and_reset_after(self):
        agent = MagicMock(spec=Agent)
        seen: list[str] = []

        async def fake_run(*args, **kwargs):
            seen.append(_current_agent_label.get())
            return MagicMock()

        agent.run = fake_run
        with use_reporter(RecordingReporter()):
            await run_agent(agent, "p", agent_label="Writer (refine)")
        assert seen == ["Writer (refine)"]
        assert _current_agent_label.get() == ""

    @pytest.mark.anyio
    async def test_label_is_reset_even_when_the_run_raises(self):
        agent = MagicMock(spec=Agent)
        agent.run = AsyncMock(side_effect=RuntimeError("boom"))
        with use_reporter(RecordingReporter()):
            with pytest.raises(RuntimeError):
                await run_agent(agent, "p", agent_label="Auditor")
        assert _current_agent_label.get() == ""


def test_every_production_agent_is_named_and_durable():
    from pydantic_ai.durable_exec.dbos import DBOSDurability

    expected = {
        "quality_gate_agent": "sira.quality_gate",
        "analyst_agent": "sira.analyst",
        "resume_parser_agent": "sira.resume_parser",
        "writer_agent": "sira.writer",
        "auditor_agent": "sira.auditor",
        "cover_letter_writer_agent": "sira.cover_letter_writer",
        "reviewer_agent": "sira.reviewer",
        "report_agent": "sira.report",
        "job_scraper_agent": "sira.job_scraper",
    }
    for attr, name in expected.items():
        agent = getattr(agents_mod, attr)
        assert agent.name == name, attr
        # pydantic-ai 2.x exposes the bound capabilities on root_capability.
        assert any(
            isinstance(c, DBOSDurability) for c in agent.root_capability.capabilities
        ), f"{attr} is not durable"
