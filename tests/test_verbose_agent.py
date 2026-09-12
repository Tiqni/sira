"""Tests for run_agent(): emits reporter events and streams when wants_tokens."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent, AgentRunResultEvent, PartDeltaEvent
from pydantic_ai.messages import TextPartDelta

from sira.reporting.base import use_reporter
from sira.workflows.agents import run_agent
from tests.reporting.test_base import RecordingReporter


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCM:
    """Stand-in for the async context manager returned by run_stream_events()
    in pydantic-ai v2: entering it yields the event iterator."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return _AsyncIter(self._events)

    async def __aexit__(self, *exc):
        return False


class TestRunAgentNonStreaming:
    @pytest.mark.anyio
    async def test_delegates_to_agent_run_when_reporter_wants_no_tokens(self):
        agent = MagicMock(spec=Agent)
        expected = MagicMock()
        agent.run = AsyncMock(return_value=expected)

        rec = RecordingReporter()  # wants_tokens = False
        with use_reporter(rec):
            result = await run_agent(agent, "prompt", agent_label="A")

        agent.run.assert_awaited_once()
        assert result is expected
        kinds = [e[0] for e in rec.events]
        assert kinds[0] == "agent_start"
        assert "agent_done" in kinds

    @pytest.mark.anyio
    async def test_passes_usage_params(self):
        agent = MagicMock(spec=Agent)
        agent.run = AsyncMock()
        with use_reporter(RecordingReporter()):
            await run_agent(
                agent, "test", agent_label="A", usage="u", usage_limits="ul"
            )
        agent.run.assert_awaited_once_with("test", usage="u", usage_limits="ul")


class TestRunAgentStreaming:
    @pytest.mark.anyio
    async def test_streams_tokens_and_returns_final_result_without_fallback(self):
        agent = MagicMock(spec=Agent)
        expected = MagicMock()
        final = MagicMock(spec=AgentRunResultEvent)
        final.result = expected
        delta = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hi"))
        agent.run_stream_events = MagicMock(return_value=_AsyncCM([delta, final]))
        agent.run = AsyncMock()

        rec = RecordingReporter()
        rec.wants_tokens = True
        with use_reporter(rec):
            result = await run_agent(agent, "prompt", agent_label="Writer")

        agent.run_stream_events.assert_called_once_with(
            "prompt", usage=None, usage_limits=None
        )
        assert result is expected
        agent.run.assert_not_awaited()  # the stream delivered the result
        assert ("token", "Writer", "hi", "output") in rec.events
        kinds = [e[0] for e in rec.events]
        assert kinds[0] == "agent_start"
        assert kinds[-1] == "agent_done"
        assert "note" not in kinds  # note() is only emitted on stream failure

    @pytest.mark.anyio
    async def test_runs_agent_when_stream_ends_without_result(self):
        agent = MagicMock(spec=Agent)
        expected = MagicMock()
        agent.run_stream_events = MagicMock(return_value=_AsyncCM([]))
        agent.run = AsyncMock(return_value=expected)

        rec = RecordingReporter()
        rec.wants_tokens = True
        with use_reporter(rec):
            result = await run_agent(agent, "prompt", agent_label="Writer")

        assert result is expected
        agent.run.assert_awaited_once()


class TestRunAgentFallback:
    @pytest.mark.anyio
    async def test_falls_back_on_stream_error(self):
        agent = MagicMock(spec=Agent)
        fallback = MagicMock()
        agent.run = AsyncMock(return_value=fallback)
        bad = MagicMock()
        bad.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        agent.run_stream_events = MagicMock(return_value=bad)

        rec = RecordingReporter()
        rec.wants_tokens = True
        with use_reporter(rec):
            result = await run_agent(agent, "p", agent_label="Writer")

        assert result is fallback
        agent.run.assert_awaited_once()
        assert ("note", "Stream interrupted for [Writer], falling back...") in (
            rec.events
        )
