import os

import pytest
from pathlib import Path
from pydantic_ai import models

# Allow OpenAI client to initialise in tests without a real key.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")

# Prevent accidental real LLM API calls across the entire test suite.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(params=["asyncio"])
def anyio_backend() -> str:
    """Restrict anyio tests to asyncio backend (trio is not installed)."""
    return "asyncio"


SAMPLE_MARKDOWN = """\
# Jane Smith

jane@example.com | linkedin.com/in/janesmith

## Professional Summary

Experienced Python engineer.

## Skills

- Python
- Django

## Work Experience

### Senior Engineer at Acme Corp (2020-2024)

- Built microservices

## Education

- BSc CS, State University, 2018
"""


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_heading("Jane Smith", 0)
    doc.add_paragraph("jane@example.com | linkedin.com/in/janesmith")
    doc.add_heading("Professional Summary", 1)
    doc.add_paragraph("Experienced Python engineer.")
    doc.add_heading("Skills", 1)
    doc.add_paragraph("Python", style="List Bullet")
    path = tmp_path / "resume.docx"
    doc.save(str(path))
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    from markdown_pdf import MarkdownPdf, Section

    pdf = MarkdownPdf()
    pdf.add_section(Section(SAMPLE_MARKDOWN))
    path = tmp_path / "resume.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def sample_cv(tmp_path: Path):
    from sira.models.agents.output import CV, WorkExperience

    return CV(
        full_name="Jane Smith",
        contact_info="jane@example.com",
        summary="Experienced Python engineer.",
        skills=["Python", "Django"],
        experience=[
            WorkExperience(
                company="Acme Corp",
                role="Senior Engineer",
                dates="2020-2024",
                highlights=["Built microservices"],
            )
        ],
        education=["BSc CS, State University, 2018"],
    )
    path = tmp_path / "resume.md"
    path.write_text(SAMPLE_MARKDOWN, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_agent_runtime_state():
    """Keep per-agent model tiers and the quality-gate config at defaults between tests."""
    from sira.workflows.agents import (
        reset_agent_models,
        reset_quality_gate,
    )

    reset_agent_models()
    reset_quality_gate()
    yield
    reset_agent_models()
    reset_quality_gate()


@pytest.fixture(scope="session", autouse=True)
def _dbos_runtime(tmp_path_factory):
    """One DBOS runtime for the whole suite, on a throwaway SQLite file.

    Workflows are registered at import, so import them before launching.

    DBOS installs its own thread pool as the *default executor* of whatever
    event loop is running. pytest-anyio gives every test a fresh loop and shuts
    that loop's default executor down on teardown — which would kill DBOS's
    shared pool for every later test. In tests, leave the loop's executor alone.
    """
    import sira.workflows  # noqa: F401
    from dbos import DBOS
    from sira.durability import durable_runtime

    async def _keep_loop_executor(cls) -> None:
        return None

    db = tmp_path_factory.mktemp("dbos") / "dbos.sqlite3"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            DBOS, "_configure_asyncio_thread_pool", classmethod(_keep_loop_executor)
        )
        with durable_runtime(f"sqlite:///{db}"):
            yield


@pytest.fixture(autouse=True)
def _clear_global_reporter():
    """Never let a test's fallback reporter leak into the next test."""
    from sira.reporting.base import install_global_reporter

    yield
    install_global_reporter(None)
