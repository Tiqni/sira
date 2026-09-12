"""pydantic-ai 2.x maps ``openai:`` to the Responses API; Sira keeps Chat Completions."""

import pytest
from pydantic_ai.models.openai import OpenAIChatModel

import pydantic_ai
from sira.workflows import agents as agents_mod
from sira.workflows.agents import normalize_model_name


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("openai:gpt-5-mini", "openai-chat:gpt-5-mini"),
        ("openai:custom", "openai-chat:custom"),
        ("openai-chat:gpt-5-mini", "openai-chat:gpt-5-mini"),
        ("openai-responses:gpt-5-mini", "openai-responses:gpt-5-mini"),
        ("ollama:llama3", "ollama:llama3"),
        ("anthropic:claude-sonnet-4-5", "anthropic:claude-sonnet-4-5"),
        (None, None),
    ],
)
def test_normalize_model_name(given, expected):
    assert normalize_model_name(given) == expected


def test_default_model_is_chat_completions():
    # conftest sets a dummy OPENAI_API_KEY, so this is the infer_model branch.
    assert isinstance(agents_mod._DEFAULT_MODEL, OpenAIChatModel)
    assert isinstance(agents_mod._build_default_model(), OpenAIChatModel)


def test_first_run_banner_is_disabled():
    """pydantic-ai 2.x prints a first-run banner to stderr; Sira owns its output."""
    assert pydantic_ai.BANNER_ENABLED is False
