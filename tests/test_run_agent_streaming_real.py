"""run_agent streams real events from a TestModel (no mocks of pydantic-ai APIs).

This guards the shape of ``Agent.run_stream_events`` — a mock cannot notice
when the library changes it, a real ``TestModel`` can.
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from sira.reporting.base import use_reporter
from sira.workflows.agents import run_agent
from tests.reporting.test_base import RecordingReporter


@pytest.mark.anyio
async def test_run_agent_streams_tokens_from_real_model():
    agent = Agent(TestModel(custom_output_text="hello streamed world"))

    rec = RecordingReporter()
    rec.wants_tokens = True
    with use_reporter(rec):
        result = await run_agent(agent, "say hi", agent_label="Probe")

    assert result.output == "hello streamed world"
    token_events = [e for e in rec.events if e[0] == "token"]
    assert token_events, "expected at least one token event"
    assert "".join(e[2] for e in token_events) == "hello streamed world"
    assert all(e[3] == "output" for e in token_events)
    kinds = [e[0] for e in rec.events]
    assert kinds[0] == "agent_start" and kinds[-1] == "agent_done"
    # No fallback happened: note() is only called when the stream fails.
    assert "note" not in kinds
