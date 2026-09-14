"""run_agent streams real events from a TestModel through the durability handler.

No pydantic-ai API is mocked: a real ``TestModel`` streams text deltas, the
``DBOSDurability`` event-stream handler forwards them to the active reporter.
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from sira.reporting.base import use_reporter
from sira.workflows.agents import _durability, run_agent
from tests.reporting.test_base import RecordingReporter


def _agent(text: str) -> Agent:
    return Agent(
        TestModel(custom_output_text=text),
        name="sira.test_stream",
        capabilities=[_durability()],
    )


@pytest.mark.anyio
async def test_run_agent_streams_tokens_from_real_model():
    agent = _agent("hello streamed world")
    rec = RecordingReporter()
    rec.wants_tokens = True
    with use_reporter(rec):
        result = await run_agent(agent, "say hi", agent_label="Probe")

    assert result.output == "hello streamed world"
    token_events = [e for e in rec.events if e[0] == "token"]
    assert token_events, "expected at least one token event"
    assert "".join(e[2] for e in token_events) == "hello streamed world"
    assert {e[1] for e in token_events} == {"Probe"}  # label from run_agent
    assert all(e[3] == "output" for e in token_events)
    kinds = [e[0] for e in rec.events]
    assert kinds[0] == "agent_start" and kinds[-1] == "agent_done"


@pytest.mark.anyio
async def test_run_agent_emits_no_tokens_when_reporter_does_not_want_them():
    agent = _agent("quiet")
    rec = RecordingReporter()  # wants_tokens = False
    with use_reporter(rec):
        result = await run_agent(agent, "say hi", agent_label="Probe")
    assert result.output == "quiet"
    assert not [e for e in rec.events if e[0] == "token"]
