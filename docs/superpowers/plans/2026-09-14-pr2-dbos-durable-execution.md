# PR 2 — Durable execution with DBOS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `sira tailor` / `sira re-tailor` run durable: each model request is checkpointed by DBOS in a local SQLite file, and a killed, crashed, or failed run can be continued with `sira resume <run-id>` without repeating earlier LLM calls.

**Architecture:** One DBOS workflow per run (`sira.tailor`) wraps the existing `ResumeTailorWorkflow._run_impl`. Every agent carries pydantic-ai's `DBOSDurability` capability, so each model request becomes a checkpointed step; live token streaming moves into the capability's `event_stream_handler`. Parser ∥ Analyst become two child workflows (DBOS forbids interleaved step sequences in one workflow); the interactive checkpoint becomes a step so a resumed run never asks twice. The CLI owns the DBOS runtime (`sira/durability.py`), prints a run id, and gains `sira resume` and `sira runs`.

**Tech Stack:** Python 3.13, `uv`, pydantic-ai 2.43 (`pydantic_ai.durable_exec.dbos.DBOSDurability`), `dbos` 2.31 (SQLite system database), Typer, Rich, pytest + pytest-anyio.

**Spec:** `docs/superpowers/specs/2026-09-12-durable-execution-and-observability-design.md` (sections 3 and 5).

## Global Constraints

- Always use `uv` (`uv run …`, `uv sync`, `uv lock`) — never bare `python`, `pip`, or `pytest`.
- Dependency line becomes exactly `"pydantic-ai[dbos,groq,mistral,cohere,bedrock]>=2.43,<3"` (adds the `dbos` extra; keeps the provider extras from PR 1).
- Agent construction stays key-free: `_build_default_model()` in `sira/workflows/agents.py` stays; no model string is passed to `Agent(...)` at import.
- Keep: `@output_validator` quality gate, per-agent `retries=`, `agent.override(model=...)` in tests, `models.ALLOW_MODEL_REQUESTS = False`, `normalize_model_name()` at the pydantic-ai boundary, `pydantic_ai.BANNER_ENABLED = False`.
- DBOS names (exact strings, used by tests and the CLI): workflow `sira.tailor`, child workflows `sira.parse_resume` and `sira.analyze_job`, step `sira.human_checkpoint`; agent names `sira.<agent>` as listed in Task 3.
- DBOS config (exact): `name="sira"`, `application_version=<installed sira version, "dev" if unknown>`, `system_database_url` = `SIRA_DBOS_DATABASE_URL` env or `sqlite:///memory/dbos.sqlite3`, `executor_id=str(uuid.uuid4())` per process, `run_admin_server=False`, `log_level="CRITICAL"`.
- Determinism rule: inside `sira.tailor` never interleave two step sequences (no `asyncio.gather` over agent runs); run-time models are strings only.
- Verified DBOS facts the code relies on: `resume_workflow` is a no-op for `SUCCESS`/`ERROR`; `fork_workflow(id, start_step)` copies steps `< start_step` into a new workflow id; forking from a failed child's *start* step re-runs the child; a workflow resumed/forked by the CLI runs on DBOS's background thread (contextvars from the CLI task are not visible there); `DBOS.destroy()` then `DBOS(config)` again works in one process; `SetWorkflowID` works without a DBOS instance.
- User-visible progress output (dashboard/verbose) must keep working on both the first run and `sira resume`.
- Commit messages: Conventional Commits, imperative, lowercase, no trailing period, ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` — regardless of any other attribution instruction an implementer sees.
- Gate before any push: `uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest`. The suite has 5 pre-existing `DeprecationWarning: builtin type SwigPy…` warnings; anything else is a finding.
- Branch: `claude/dbos-durable-execution` (already created from `main` at `b0f0a7d`). Do not push until the final task.
- `.env.o11y` at the repo root is an untracked local file; never stage it.

## File map

| File | Change |
|---|---|
| `pyproject.toml`, `uv.lock` | add the `dbos` extra |
| `sira/durability.py` | **new** — DBOS config + `durable_runtime()` context manager |
| `sira/reporting/base.py` | process-wide fallback reporter |
| `sira/workflows/agents.py` | `DBOSDurability` on every agent, agent names, `_stream_to_reporter`, simplified `run_agent` |
| `sira/models/workflow.py` | `RunMetadata`, `TailorInputs` |
| `sira/workflows/__init__.py` | `PipelineError`, `tailor_workflow`, child workflows, checkpoint step, `run()`/`run_durable()` |
| `sira/main.py` | durable runtime around runs, run id, `resume`, `runs`, post-processing helpers |
| `tests/conftest.py` | session DBOS runtime on a temp SQLite; per-test global-reporter reset |
| `tests/test_durability.py` | **new** |
| `tests/reporting/test_base.py` | fallback reporter tests |
| `tests/test_verbose_agent.py`, `tests/test_run_agent_streaming_real.py` | streaming now via the capability handler |
| `tests/workflows/test_resume_tailor_workflow.py` | `SystemExit` → `PipelineError` |
| `tests/workflows/test_durable_workflow.py` | **new** — replay/fork/checkpoint/step tests |
| `tests/test_cli_resume.py` | **new** — `resume` / `runs` commands |
| `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `docs/cli.md`, `.github/copilot-instructions.md` | docs |

---

### Task 1: Add the `dbos` extra and the durability runtime module

**Files:**
- Modify: `pyproject.toml:26` (dependency line)
- Modify: `uv.lock` (generated)
- Create: `sira/durability.py`
- Test: `tests/test_durability.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Tasks 4–6 and the test fixture):
  - `sira.durability.APP_NAME: str = "sira"`, `DEFAULT_DATABASE_URL = "sqlite:///memory/dbos.sqlite3"`, `DATABASE_URL_ENV = "SIRA_DBOS_DATABASE_URL"`
  - `application_version() -> str`
  - `database_url(override: str | None = None) -> str`
  - `build_config(db_url: str | None = None, *, tracing: bool = False) -> DBOSConfig`
  - `is_active() -> bool`
  - `durable_runtime(db_url: str | None = None, *, tracing: bool = False)` — context manager; outermost caller launches and destroys DBOS, nested use is a no-op.

- [ ] **Step 1: Add the extra and re-lock**

In `pyproject.toml` replace the dependency line with:

```toml
    "pydantic-ai[dbos,groq,mistral,cohere,bedrock]>=2.43,<3",
```

`uv lock` re-resolves; `uv sync` installs (the `dbos` package, ~2.31):

```bash
uv lock && uv sync && uv pip show dbos | head -2
```

Expected: `Name: dbos` / `Version: 2.31.x` (any 2.x ≥ 2.31 is fine).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_durability.py`:

```python
"""DBOS runtime configuration for durable runs."""

from importlib import metadata

import pytest
from dbos import DBOS

from sira import durability


def test_build_config_defaults(monkeypatch):
    monkeypatch.delenv(durability.DATABASE_URL_ENV, raising=False)
    cfg = durability.build_config()
    assert cfg["name"] == "sira"
    assert cfg["system_database_url"] == "sqlite:///memory/dbos.sqlite3"
    assert cfg["run_admin_server"] is False
    assert cfg["log_level"] == "CRITICAL"
    assert cfg["application_version"] == durability.application_version()
    assert "enable_otlp" not in cfg


def test_executor_id_is_unique_per_config():
    a = durability.build_config()["executor_id"]
    b = durability.build_config()["executor_id"]
    assert a != b and len(a) == 36  # uuid4 text


def test_application_version_matches_installed_package():
    assert durability.application_version() == metadata.version("sira")


def test_database_url_env_override(monkeypatch):
    monkeypatch.setenv(durability.DATABASE_URL_ENV, "sqlite:////tmp/x.sqlite3")
    assert durability.database_url() == "sqlite:////tmp/x.sqlite3"
    # An explicit argument wins over the environment.
    assert durability.database_url("sqlite:///y.sqlite3") == "sqlite:///y.sqlite3"


def test_tracing_flag_adds_otel_settings():
    cfg = durability.build_config(tracing=True)
    assert cfg["enable_otlp"] is True
    assert cfg["otel_attribute_format"] == "semconv"
    assert "otlp_traces_endpoints" not in cfg


def test_sqlite_parent_directory_is_created(tmp_path):
    url = f"sqlite:///{tmp_path}/nested/dir/dbos.sqlite3"
    durability.build_config(url)
    assert (tmp_path / "nested" / "dir").is_dir()


def test_durable_runtime_is_owned_by_outermost_caller(tmp_path):
    was_active = durability.is_active()
    with durability.durable_runtime(f"sqlite:///{tmp_path}/dbos.sqlite3"):
        assert durability.is_active()
        with durability.durable_runtime():  # nested: no-op
            assert durability.is_active()
        assert durability.is_active()
    assert durability.is_active() == was_active


# Registered at import time: DBOS workflows must exist before the runtime is
# launched (the session fixture launches it once for the whole suite).
@DBOS.workflow(name="sira.test_probe")
async def _probe_workflow(x: int) -> int:
    return x * 2


@pytest.mark.anyio
async def test_workflow_runs_inside_durable_runtime(tmp_path):
    with durability.durable_runtime(f"sqlite:///{tmp_path}/dbos.sqlite3"):
        assert await _probe_workflow(21) == 42
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/test_durability.py -q
```

Expected: collection error `ModuleNotFoundError: No module named 'sira.durability'`.

- [ ] **Step 4: Create `sira/durability.py`**

```python
"""DBOS runtime for durable execution.

DBOS (a library for durable workflows) stores every workflow's inputs, each
step's result, and the final output in a SQLite file. A run that is killed,
crashes, or fails can then be continued with ``sira resume <run-id>`` from the
last completed model request instead of starting over.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from importlib import metadata

from dbos import DBOS, DBOSConfig

APP_NAME = "sira"
DEFAULT_DATABASE_URL = "sqlite:///memory/dbos.sqlite3"
DATABASE_URL_ENV = "SIRA_DBOS_DATABASE_URL"

_active = False


def application_version() -> str:
    """Version tag stored with every workflow.

    DBOS only continues a workflow whose tag matches the running code, so the
    tag is the installed package version (``dev`` when Sira is not installed).
    """
    try:
        return metadata.version("sira")
    except metadata.PackageNotFoundError:
        return "dev"


def database_url(override: str | None = None) -> str:
    """Resolve the system database URL: argument, then env var, then default."""
    return override or os.environ.get(DATABASE_URL_ENV) or DEFAULT_DATABASE_URL


def _ensure_sqlite_directory(url: str) -> None:
    """Create the folder of a ``sqlite:///path`` URL; SQLAlchemy creates only the file."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix) :]
    if path in ("", ":memory:"):
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def build_config(db_url: str | None = None, *, tracing: bool = False) -> DBOSConfig:
    """Build the DBOS configuration for one process."""
    url = database_url(db_url)
    _ensure_sqlite_directory(url)
    config: DBOSConfig = {
        "name": APP_NAME,
        "application_version": application_version(),
        "system_database_url": url,
        # Unique per process. DBOS auto-recovers PENDING workflows that belong
        # to the same executor id, and a new `sira tailor` must never silently
        # pick up an older crashed run. Continuing a run is explicit: `sira resume`.
        "executor_id": str(uuid.uuid4()),
        "run_admin_server": False,
        # DBOS logs a full traceback at ERROR level when a workflow fails; the
        # CLI reports failures itself, so keep the library quiet.
        "log_level": "CRITICAL",
    }
    if tracing:
        # Spans go through the global OpenTelemetry tracer provider that
        # sira.telemetry sets up; DBOS must not add its own exporter.
        config["enable_otlp"] = True
        config["otel_attribute_format"] = "semconv"
    return config


def is_active() -> bool:
    """True while a DBOS runtime launched by ``durable_runtime`` is running."""
    return _active


@contextlib.contextmanager
def durable_runtime(
    db_url: str | None = None, *, tracing: bool = False
) -> Iterator[None]:
    """Configure and launch DBOS for this process; a no-op when already active.

    Only the outermost caller launches and shuts DBOS down. That lets the test
    suite hold one runtime for the whole session while the CLI code under test
    calls this again without side effects. Workflows and agents must be
    imported (registered) before entering; `sira.workflows` does that.
    """
    global _active
    if _active:
        yield
        return
    DBOS(config=build_config(db_url, tracing=tracing))
    DBOS.launch()
    _active = True
    try:
        yield
    finally:
        _active = False
        DBOS.destroy()
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_durability.py -q
```

Expected: `8 passed`.

- [ ] **Step 6: Full gate and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest -q
```

Expected: ruff clean; `374 passed` (366 + 8).

```bash
git add pyproject.toml uv.lock sira/durability.py tests/test_durability.py
git commit -m "feat: add the dbos runtime module

Adds the pydantic-ai dbos extra and a small module that owns the DBOS
configuration (SQLite system database next to the memory database, one
executor id per process, pinned application version) and a context
manager that launches the runtime once per process.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Process-wide fallback reporter

**Why:** a run continued by `sira resume` executes on DBOS's background thread, where the `use_reporter` contextvar set by the CLI is not visible. Without a fallback, progress output is silently lost on resume.

**Files:**
- Modify: `sira/reporting/base.py`
- Modify: `sira/reporting/__init__.py`
- Test: `tests/reporting/test_base.py`

**Interfaces:**
- Produces: `install_global_reporter(reporter: ProgressReporter | None) -> None` (None clears). `get_active_reporter()` now returns: contextvar reporter → global reporter → `NullReporter()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/reporting/test_base.py`:

```python
import threading

from sira.reporting.base import (
    get_active_reporter,
    install_global_reporter,
    use_reporter,
)


def test_global_reporter_is_fallback_when_no_context_reporter():
    rec = RecordingReporter()
    install_global_reporter(rec)
    try:
        assert get_active_reporter() is rec
    finally:
        install_global_reporter(None)
    assert isinstance(get_active_reporter(), NullReporter)


def test_context_reporter_wins_over_global():
    global_rec = RecordingReporter()
    local_rec = RecordingReporter()
    install_global_reporter(global_rec)
    try:
        with use_reporter(local_rec):
            assert get_active_reporter() is local_rec
        assert get_active_reporter() is global_rec
    finally:
        install_global_reporter(None)


def test_global_reporter_visible_from_another_thread():
    """A resumed DBOS workflow runs on a background thread with no contextvars."""
    rec = RecordingReporter()
    install_global_reporter(rec)
    seen: list = []
    try:
        with use_reporter(RecordingReporter()):  # only visible in this thread
            t = threading.Thread(target=lambda: seen.append(get_active_reporter()))
            t.start()
            t.join()
    finally:
        install_global_reporter(None)
    assert seen == [rec]
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/reporting/test_base.py -q
```

Expected: `ImportError: cannot import name 'install_global_reporter'`.

- [ ] **Step 3: Implement**

In `sira/reporting/base.py`, after the `_active_reporter` contextvar definition, add:

```python
# Process-wide fallback. A workflow continued by `sira resume` runs on DBOS's
# background thread, where the contextvar above is not set; the CLI installs
# its reporter here so progress output keeps working on that path.
_global_reporter: ProgressReporter | None = None


def install_global_reporter(reporter: ProgressReporter | None) -> None:
    """Set (or clear, with None) the fallback reporter for every thread."""
    global _global_reporter
    _global_reporter = reporter
```

and change `get_active_reporter` to:

```python
def get_active_reporter() -> ProgressReporter:
    """Return the reporter for the current context, else the global one, else a NullReporter."""
    return _active_reporter.get() or _global_reporter or NullReporter()
```

In `sira/reporting/__init__.py` add `install_global_reporter` to both the import and `__all__` (keep alphabetical order in `__all__`).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/reporting -q
```

Expected: all pass (3 new).

- [ ] **Step 5: Commit**

```bash
git add sira/reporting/base.py sira/reporting/__init__.py tests/reporting/test_base.py
git commit -m "feat: add a process-wide fallback progress reporter

A run continued by DBOS executes on its background thread, where the
context-local reporter is not visible. The fallback keeps the dashboard
and verbose output working on that path.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Make every agent durable and move streaming into the capability handler

**Why:** with `DBOSDurability` on an agent, every model request that happens inside a DBOS workflow becomes a checkpointed step; outside a workflow the capability is transparent (verified: streaming, output-validator retries, and `override(model=…)` all work with no DBOS runtime configured). Live token streaming must move into the capability's `event_stream_handler` because events are otherwise buffered per step.

**Files:**
- Modify: `sira/workflows/agents.py`
- Modify: `tests/test_verbose_agent.py` (rewrite)
- Modify: `tests/test_run_agent_streaming_real.py`

**Interfaces:**
- Consumes: `get_active_reporter()` (Task 2 fallback), `normalize_model_name()` (already in `agents.py`).
- Produces:
  - `_current_agent_label: contextvars.ContextVar[str]` (default `""`) — set by `run_agent` for the duration of the run; read by the handler.
  - `_stream_to_reporter(ctx, events)` — forwards `TextPartDelta` → `reporter.token(label, text, "output")` and `ThinkingPartDelta` → `"thinking"` when `reporter.wants_tokens`.
  - `_MODEL_STEP_CONFIG = {"retries_allowed": True, "max_attempts": 3, "interval_seconds": 2, "backoff_rate": 2}`.
  - `_durability() -> DBOSDurability` — one fresh capability per agent.
  - `run_agent(agent, prompt, *, verbose=False, agent_label="", usage=None, usage_limits=None, model=None) -> AgentRunResult` — unchanged signature; always calls `agent.run(...)`; no streaming fallback path and no `reporter.note(...)` any more.
  - Agent names (exact): `quality_gate_agent`→`sira.quality_gate`, `analyst_agent`→`sira.analyst`, `resume_parser_agent`→`sira.resume_parser`, `writer_agent`→`sira.writer`, `auditor_agent`→`sira.auditor`, `cover_letter_writer_agent`→`sira.cover_letter_writer`, `reviewer_agent`→`sira.reviewer`, `report_agent`→`sira.report`, `job_scraper_agent`→`sira.job_scraper`.

- [ ] **Step 1: Replace `tests/test_run_agent_streaming_real.py` (failing first)**

```python
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
```

- [ ] **Step 2: Replace `tests/test_verbose_agent.py`**

```python
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
```

- [ ] **Step 3: Run both files to verify they fail**

```bash
uv run pytest tests/test_run_agent_streaming_real.py tests/test_verbose_agent.py -q
```

Expected: `ImportError: cannot import name '_durability'` (and `_current_agent_label`).

- [ ] **Step 4: Edit `sira/workflows/agents.py` — imports**

Change the import block so it reads (only the listed lines change):

```python
import asyncio
import contextvars
import logging
import os
import time
from collections.abc import AsyncIterable
from typing import Any

import pydantic_ai
from pydantic import BaseModel, ConfigDict
from pydantic_ai import (
    Agent,
    AgentStreamEvent,
    ModelRetry,
    PartDeltaEvent,
    RunContext,
)
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import TextPartDelta, ThinkingPartDelta
```

(`AgentRunResultEvent` is removed from the `pydantic_ai` import — it is no longer used. Keep every other existing import line as it is.)

- [ ] **Step 5: Add the streaming handler and the durability factory**

Insert immediately after the `_safe_report` function (before `async def run_agent`):

```python
# Label of the agent currently running through run_agent, read by the
# streaming handler below. A contextvar: set per task, visible inside the
# DBOS model-request step that runs in the same task.
_current_agent_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_agent_label", default=""
)


async def _stream_to_reporter(
    ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]
) -> None:
    """Forward text/thinking deltas to the active reporter as they arrive.

    Installed on every agent through DBOSDurability so tokens stream live even
    when the model request runs inside a checkpointed DBOS step.
    """
    reporter = get_active_reporter()
    try:
        wants_tokens = reporter.wants_tokens
    except Exception:
        logger.debug("reporter_call_failed", exc_info=True)
        wants_tokens = False
    label = _current_agent_label.get()
    async for event in events:
        if not wants_tokens or not isinstance(event, PartDeltaEvent):
            continue
        if isinstance(event.delta, TextPartDelta):
            _safe_report(reporter.token, label, event.delta.content_delta, "output")
        elif isinstance(event.delta, ThinkingPartDelta):
            _safe_report(
                reporter.token, label, event.delta.content_delta, "thinking"
            )


# DBOS retries a model request that raised (network or HTTP errors) before
# pydantic-ai's own agent-level retries ever see the failure.
_MODEL_STEP_CONFIG = {
    "retries_allowed": True,
    "max_attempts": 3,
    "interval_seconds": 2,
    "backoff_rate": 2,
}


def _durability() -> DBOSDurability:
    """One DBOSDurability per agent (DBOS binds step names to the agent name).

    Inside a DBOS workflow each model request becomes a checkpointed step;
    outside a workflow the capability is transparent.
    """
    return DBOSDurability(
        event_stream_handler=_stream_to_reporter,
        model_step_config=_MODEL_STEP_CONFIG,
    )
```

- [ ] **Step 6: Replace `run_agent`**

Replace the whole `run_agent` function (from `async def run_agent(` to the end of its final `return result`) with:

```python
async def run_agent(
    agent: Agent,
    prompt: str,
    *,
    verbose: bool = False,  # retained for call-site compatibility; reporter drives streaming
    agent_label: str = "",
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
    model: str | None = None,
) -> AgentRunResult:
    """Run an agent, emitting lifecycle events to the active reporter.

    Token streaming is done by the DBOSDurability event-stream handler on the
    agent (see _stream_to_reporter), so this always uses ``agent.run``: one
    code path inside and outside a DBOS workflow.
    """
    reporter = get_active_reporter()

    run_kwargs: dict[str, Any] = {"usage": usage, "usage_limits": usage_limits}
    resolved = model if model is not None else resolve_model(agent_label)
    if resolved is not None:
        run_kwargs["model"] = normalize_model_name(resolved)

    _safe_report(reporter.agent_start, agent_label, prompt)
    start = time.monotonic()
    label_token = _current_agent_label.set(agent_label)
    try:
        result = await agent.run(prompt, **run_kwargs)
    finally:
        _current_agent_label.reset(label_token)
    _safe_report(reporter.agent_done, agent_label, time.monotonic() - start)
    return result
```

- [ ] **Step 7: Name every agent and attach the capability**

For each of the nine `Agent(` constructors below, insert `    name="<name>",` as the line directly after `    _DEFAULT_MODEL,` and insert `    capabilities=[_durability()],` as the line directly after the constructor's `    retries=<n>,` line:

| Variable | `name=` |
|---|---|
| `quality_gate_agent` | `"sira.quality_gate"` |
| `analyst_agent` | `"sira.analyst"` |
| `resume_parser_agent` | `"sira.resume_parser"` |
| `writer_agent` | `"sira.writer"` |
| `auditor_agent` | `"sira.auditor"` |
| `cover_letter_writer_agent` | `"sira.cover_letter_writer"` |
| `reviewer_agent` | `"sira.reviewer"` |
| `report_agent` | `"sira.report"` |
| `job_scraper_agent` | `"sira.job_scraper"` |

Example (the quality gate):

```python
quality_gate_agent = Agent(
    _DEFAULT_MODEL,
    name="sira.quality_gate",
    model_settings=MODEL_SETTINGS,
    system_prompt="""...unchanged...""",
    output_type=QualityCheckResult,
    retries=2,
    capabilities=[_durability()],
)
```

- [ ] **Step 8: Run the two test files, then the full gate**

```bash
uv run pytest tests/test_run_agent_streaming_real.py tests/test_verbose_agent.py -q
```

Expected: `7 passed`.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest -q
```

Expected: ruff clean (if ruff reports an unused import, delete that import). Count: `378 passed` (377 after Task 2; `test_verbose_agent.py` keeps 5 tests, the real-streaming file grows from 1 to 2). The quality-gate, model-tuning, and workflow tests must still pass unchanged — `DBOSDurability` is transparent outside a workflow.

- [ ] **Step 9: Commit**

```bash
git add sira/workflows/agents.py tests/test_verbose_agent.py tests/test_run_agent_streaming_real.py
git commit -m "feat: make every agent durable with DBOSDurability

Each agent gets a stable name and the DBOSDurability capability, so a
model request that runs inside a DBOS workflow is a checkpointed step
with transient-error retries. Token streaming moves into the capability's
event-stream handler, which lets run_agent use one code path in and out
of workflows.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Wrap the pipeline in a DBOS workflow (inputs model, child workflows, checkpoint step)

**Why:** this is the durability boundary. `sira.tailor` is the workflow; Parser and Analyst run as child workflows (DBOS forbids two interleaved step sequences in one workflow); the human checkpoint is a step so a continued run never asks twice; `sys.exit` becomes `PipelineError` so DBOS records a failed run as `ERROR` (a `SystemExit` would leave it `PENDING`).

**Files:**
- Modify: `sira/models/workflow.py`
- Modify: `sira/workflows/__init__.py`
- Modify: `tests/conftest.py`
- Modify: `tests/workflows/test_resume_tailor_workflow.py:117` (the `SystemExit` test)
- Modify: `tests/workflows/test_parallel_parse_analyze.py` (sleep length)
- Test (new): `tests/workflows/test_tailor_inputs.py`

**Interfaces:**
- Consumes: `durable_runtime` (Task 1), `get_active_reporter`/`use_reporter` (Task 2), `run_agent` and agents (Task 3), `agents_mod.FAST_MODEL / STRONG_MODEL / QUALITY_GATE_ENABLED / QUALITY_GATE_THRESHOLD / agent_models_configured() / set_agent_models() / set_quality_gate()`.
- Produces (used by Tasks 5–6):
  - `sira.models.workflow.RunMetadata(job_url, job_id, resume_source_path, output_dir, output_pattern, resume_name_pattern, job_posting_markdown)` and `TailorInputs(resume_text, job_content, job_content_file_path, pre_parsed_cv, model, fast_model, strong_model, write_attempts, review_iterations, quality_gate, gate_threshold, interactive, debug, verbose, metadata)`.
  - `sira.workflows.PipelineError(Exception)`.
  - Constants `TAILOR_WORKFLOW_NAME = "sira.tailor"`, `PARSE_WORKFLOW_NAME = "sira.parse_resume"`, `ANALYZE_WORKFLOW_NAME = "sira.analyze_job"`, `CHECKPOINT_STEP_NAME = "sira.human_checkpoint"`.
  - `tailor_workflow(inputs: TailorInputs) -> ResumeTailorResult` (`@DBOS.workflow(name="sira.tailor")`).
  - `ResumeTailorWorkflow.build_inputs(resume_text, *, job_content_file_path=None, job_content=None, model=None, pre_parsed_cv=None, debug=False, verbose=False, metadata=None) -> TailorInputs`.
  - `ResumeTailorWorkflow.run(...)` — unchanged signature plus `metadata: RunMetadata | None = None`; delegates to `run_durable`.
  - `ResumeTailorWorkflow.run_durable(inputs, *, reporter=None) -> ResumeTailorResult`.
  - `ResumeTailorWorkflow._human_checkpoint(...)` keeps its sync signature (tests call it directly).

- [ ] **Step 1: Write the failing inputs test**

Create `tests/workflows/test_tailor_inputs.py`:

```python
"""TailorInputs snapshots everything a durable run needs."""

from sira.models.workflow import RunMetadata, TailorInputs
from sira.workflows import ResumeTailorWorkflow
from sira.workflows import agents as agents_mod


def test_build_inputs_snapshots_loop_and_gate_settings():
    agents_mod.set_quality_gate(enabled=False, threshold=9)
    wf = ResumeTailorWorkflow(write_attempts=3, review_iterations=2, interactive=True)
    inputs = wf.build_inputs("# resume", job_content="job", model="openai:x", debug=True)
    assert inputs.resume_text == "# resume"
    assert inputs.job_content == "job"
    assert inputs.model == "openai:x"
    assert inputs.write_attempts == 3
    assert inputs.review_iterations == 2
    assert inputs.interactive is True
    assert inputs.debug is True
    assert inputs.quality_gate is False
    assert inputs.gate_threshold == 9
    assert inputs.metadata == RunMetadata()


def test_build_inputs_records_model_tiers_only_when_configured():
    wf = ResumeTailorWorkflow()
    assert wf.build_inputs("r", job_content="j").fast_model is None
    agents_mod.set_agent_models(fast="openai:fast", strong="openai:strong")
    inputs = wf.build_inputs("r", job_content="j")
    assert (inputs.fast_model, inputs.strong_model) == ("openai:fast", "openai:strong")


def test_tailor_inputs_round_trip_through_pickle(sample_cv):
    import pickle

    inputs = TailorInputs(
        resume_text="r",
        job_content="j",
        pre_parsed_cv=sample_cv,
        metadata=RunMetadata(job_url="https://x", output_dir="/tmp/out"),
    )
    assert pickle.loads(pickle.dumps(inputs)) == inputs
```

(`set_quality_gate` / `set_agent_models` are reset by the autouse fixture in `tests/conftest.py`.)

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/workflows/test_tailor_inputs.py -q
```

Expected: `ImportError: cannot import name 'RunMetadata'`.

- [ ] **Step 3: Extend `sira/models/workflow.py`**

Replace the file with:

```python
from pydantic import BaseModel, Field

from sira.models.agents.output import CV, FinalReport


class ResumeTailorResult(BaseModel):
    company_name: str
    job_title: str
    tailored_resume: str
    audit_report: dict
    passed: bool
    final_report: FinalReport | None = None


class RunMetadata(BaseModel):
    """What the CLI needs after the workflow returns (output files, memory).

    Stored with the run as part of TailorInputs, so ``sira resume <run-id>``
    needs no other arguments.
    """

    job_url: str | None = None
    # Set for re-tailor runs: the prior job record to update in memory.
    job_id: str | None = None
    resume_source_path: str = ""
    output_dir: str = "./output"
    output_pattern: str = "{company_name}-{job_title}"
    resume_name_pattern: str = "{company_name}-{full_name}"
    # The scraped (or stored) posting, saved to memory together with the result.
    job_posting_markdown: str = ""


class TailorInputs(BaseModel):
    """Everything one durable run needs. DBOS stores it as the workflow input.

    Model tiers and quality-gate settings are snapshotted here so a continued
    run is configured exactly like the first one.
    """

    resume_text: str
    job_content: str | None = None
    job_content_file_path: str | None = None
    pre_parsed_cv: CV | None = None
    model: str | None = None
    fast_model: str | None = None
    strong_model: str | None = None
    write_attempts: int = 2
    review_iterations: int = 1
    quality_gate: bool = True
    gate_threshold: int = 6
    interactive: bool = False
    debug: bool = False
    verbose: bool = False
    metadata: RunMetadata = Field(default_factory=RunMetadata)
```

- [ ] **Step 4: Rewrite the top of `sira/workflows/__init__.py` (imports, errors, checkpoint step, child workflows)**

Replace everything from the first line down to and including the `class UserAbortedError` definition with:

```python
import sys
from datetime import datetime, timezone

from dbos import DBOS
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import RunUsage

from sira.models.agents.output import CV, CVDiff, FinalReport, JobAnalysis
from sira.models.workflow import ResumeTailorResult, RunMetadata, TailorInputs
from sira.reporting.base import (
    NullReporter,
    ProgressReporter,
    get_active_reporter,
    use_reporter,
)
from sira.utils.cv_diff import compute_cv_diff, compute_gap_analysis
from sira.workflows import agents as agents_mod
from sira.workflows.agents import (
    USAGE_LIMITS,
    _analyst_qs,
    _auditor_qs,
    _parser_qs,
    _writer_qs,
    analyst_agent,
    auditor_agent,
    report_agent,
    resume_parser_agent,
    reviewer_agent,
    run_agent,
    writer_agent,
    apply_model_override,
    get_model,
)

# DBOS names. The CLI and tests look these up, so keep them stable.
TAILOR_WORKFLOW_NAME = "sira.tailor"
PARSE_WORKFLOW_NAME = "sira.parse_resume"
ANALYZE_WORKFLOW_NAME = "sira.analyze_job"
CHECKPOINT_STEP_NAME = "sira.human_checkpoint"

# How often a parent workflow polls for a child's result. SQLite has no
# notifications, so this is the latency added per child.
_CHILD_RESULT_POLL_SECONDS = 0.1


class UserAbortedError(Exception):
    """Raised when the user explicitly aborts at an interactive checkpoint."""


class PipelineError(Exception):
    """A stage failed for good and the run cannot produce a result.

    Raised instead of ``sys.exit`` so DBOS records the run as ERROR (a
    SystemExit would leave it PENDING) and the CLI can print the message.
    """


def _prompt_checkpoint(
    header: str,
    details: list[str],
    choices: list[tuple[str, str]],
    default: str,
    interactive: bool,
) -> tuple[str, str]:
    """Ask the user at an interactive checkpoint; return (action_key, feedback).

    Returns (default, "") immediately when non-interactive or stdin is not a
    TTY. Loops on unrecognized input. Loops on empty feedback when "f" is
    selected.
    """
    if not interactive:
        return (default, "")
    if not sys.stdin.isatty():
        get_active_reporter().log(
            "⚠️  Interactive checkpoint skipped (stdin is not a TTY — using default)"
        )
        return (default, "")

    valid_keys = {key for key, _ in choices}

    while True:
        print(f"\n{header}")
        for line in details:
            print(f"  {line}")
        print("\nWhat would you like to do?")
        for key, label in choices:
            print(f"  [{key}] {label}")
        print()
        raw = input(f"Choice [{default}]: ").strip().lower() or default

        if raw not in valid_keys:
            print(
                f"Unrecognized choice '{raw}'. Please choose from: {', '.join(sorted(valid_keys))}"
            )
            continue

        if raw == "f":
            while True:
                feedback = input("Your feedback/instructions: ").strip()
                if feedback:
                    return ("f", feedback)
                print("Feedback cannot be empty. Please provide your instructions.")

        return (raw, "")


@DBOS.step(name=CHECKPOINT_STEP_NAME)
async def _checkpoint_step(
    header: str,
    details: list[str],
    choices: list[tuple[str, str]],
    default: str,
    interactive: bool,
) -> tuple[str, str]:
    """The human decision as a checkpointed step: a continued run never asks twice."""
    return _prompt_checkpoint(header, details, choices, default, interactive)


@DBOS.workflow(name=PARSE_WORKFLOW_NAME)
async def _parse_resume_workflow(
    resume_text: str, max_retries: int
) -> tuple[CV, RunUsage]:
    """Parse the original resume into a CV (child workflow). Raises on hard failure."""
    reporter = get_active_reporter()
    usage = RunUsage()
    original_cv: CV | None = None
    original_cv_result = None
    for attempt in range(max_retries):
        try:
            original_cv_result = await run_agent(
                resume_parser_agent,
                f"Parse this resume into structured format:\n\n{resume_text}",
                agent_label="Parser",
                usage=usage,
                usage_limits=USAGE_LIMITS,
            )
            if original_cv_result.output is None:
                raise ValueError("Resume parsing returned None")
            if (
                original_cv_result.output.full_name
                and original_cv_result.output.experience
            ):
                original_cv = original_cv_result.output
                break
            reporter.log(
                f"⚠️ Attempt {attempt + 1}/{max_retries}: Incomplete resume parse, retrying..."
            )
        except UnexpectedModelBehavior:
            if _parser_qs.last_output is not None:
                reporter.log("⚠️  Resume Parser failed — using best available output")
                original_cv = _parser_qs.last_output
                break
            raise
        except Exception as e:
            reporter.log(f"⚠️ Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt == max_retries - 1:
                raise
    if original_cv is None:
        if original_cv_result is None or original_cv_result.output is None:
            raise RuntimeError("Failed to parse original resume after retries.")
        original_cv = original_cv_result.output
    return original_cv, usage


@DBOS.workflow(name=ANALYZE_WORKFLOW_NAME)
async def _analyze_job_workflow(
    job_analysis_prompt: str, max_retries: int
) -> tuple[JobAnalysis, RunUsage]:
    """Analyze the job posting (child workflow). Raises on hard failure."""
    reporter = get_active_reporter()
    usage = RunUsage()
    job_analysis = None
    job_analysis_result = None
    for attempt in range(max_retries):
        try:
            job_analysis_result = await run_agent(
                analyst_agent,
                job_analysis_prompt,
                agent_label="Analyst",
                usage=usage,
                usage_limits=USAGE_LIMITS,
            )
            if job_analysis_result.output is None:
                raise ValueError("Job analysis data is None")
            if (
                job_analysis_result.output.job_title
                and job_analysis_result.output.company_name
            ):
                job_analysis = job_analysis_result.output
                break
            reporter.log(
                f"⚠️ Attempt {attempt + 1}/{max_retries}: Incomplete job data, retrying..."
            )
        except UnexpectedModelBehavior:
            if _analyst_qs.last_output is not None:
                reporter.log("⚠️  Job Analyst failed — using best available output")
                job_analysis = _analyst_qs.last_output
                break
            raise
        except Exception as e:
            reporter.log(f"⚠️ Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt == max_retries - 1:
                raise
    if job_analysis is None:
        if job_analysis_result is None or job_analysis_result.output is None:
            raise RuntimeError("Failed to get complete job analysis after retries.")
        job_analysis = job_analysis_result.output
    return job_analysis, usage
```

- [ ] **Step 5: Replace the instance methods `_human_checkpoint`, `_parse_resume`, `_analyze_job`, and `run`**

In `class ResumeTailorWorkflow`, replace the whole `_human_checkpoint` method with:

```python
    def _human_checkpoint(
        self,
        header: str,
        details: list[str],
        choices: list[tuple[str, str]],
        default: str = "c",
    ) -> tuple[str, str]:
        """Present an interactive checkpoint and return (action_key, feedback_text).

        The durable pipeline goes through ``_checkpoint_step`` instead; this
        sync form stays for direct callers and tests.
        """
        return _prompt_checkpoint(header, details, choices, default, self._interactive)
```

Delete the `_parse_resume` and `_analyze_job` methods entirely (their bodies now live in the two child workflows above).

Replace the whole `run` method with:

```python
    def build_inputs(
        self,
        resume_text: str,
        *,
        job_content_file_path: str | None = None,
        job_content: str | None = None,
        model: str | None = None,
        pre_parsed_cv: CV | None = None,
        debug: bool = False,
        verbose: bool = False,
        metadata: RunMetadata | None = None,
    ) -> TailorInputs:
        """Snapshot everything the durable workflow needs.

        Includes the current model tiers and quality-gate settings, so a run
        continued later by ``sira resume`` is configured exactly like this one.
        """
        configured = agents_mod.agent_models_configured()
        return TailorInputs(
            resume_text=resume_text,
            job_content=job_content,
            job_content_file_path=job_content_file_path,
            pre_parsed_cv=pre_parsed_cv,
            model=model,
            fast_model=agents_mod.FAST_MODEL if configured else None,
            strong_model=agents_mod.STRONG_MODEL if configured else None,
            write_attempts=self.max_write_attempts,
            review_iterations=self.max_review_iterations,
            quality_gate=agents_mod.QUALITY_GATE_ENABLED,
            gate_threshold=agents_mod.QUALITY_GATE_THRESHOLD,
            interactive=self._interactive,
            debug=debug,
            verbose=verbose,
            metadata=metadata or RunMetadata(),
        )

    async def run(
        self,
        resume_text: str,
        job_content_file_path: str | None = None,
        job_content: str | None = None,
        model: str | None = None,
        *,
        pre_parsed_cv: CV | None = None,
        debug: bool = False,
        verbose: bool = False,
        reporter: ProgressReporter | None = None,
        metadata: RunMetadata | None = None,
    ) -> ResumeTailorResult:
        """Run the resume tailoring workflow durably (see ``run_durable``)."""
        inputs = self.build_inputs(
            resume_text,
            job_content_file_path=job_content_file_path,
            job_content=job_content,
            model=model,
            pre_parsed_cv=pre_parsed_cv,
            debug=debug,
            verbose=verbose,
            metadata=metadata,
        )
        return await self.run_durable(inputs, reporter=reporter)

    async def run_durable(
        self,
        inputs: TailorInputs,
        *,
        reporter: ProgressReporter | None = None,
    ) -> ResumeTailorResult:
        """Run ``tailor_workflow`` with ``reporter`` active.

        DBOS checkpoints every model request. The caller chooses the run id
        with ``dbos.SetWorkflowID`` (the CLI does; tests let DBOS pick one).
        """
        self._reporter = reporter or NullReporter()
        try:
            with use_reporter(self._reporter):
                return await tailor_workflow(inputs)
        finally:
            self._reporter = NullReporter()
```

- [ ] **Step 6: Edit `_run_impl`**

Six edits inside `_run_impl`, in order of appearance:

(a) The "no job content" exit:

```python
        else:
            sys.exit(
                "❌ No job content provided. Supply either job_content or job_content_file_path."
            )
```
becomes
```python
        else:
            raise PipelineError(
                "No job content provided. Supply either job_content or job_content_file_path."
            )
```

(b) Delete the line `self._analyst_result = None` (keep the two `RunUsage()` lines above it).

(c) The parse ∥ analyze block. Replace:

```python
                self._complete_stage("PARSING_RESUME")
                self._set_stage("ANALYZING_JOB")
                job_analysis = await self._analyze_job(job_analysis_prompt, verbose)
            else:
                # Parse and analyze run concurrently — show BOTH as running.
                # Mark ANALYZING_JOB running directly (do NOT use _set_stage,
                # which would prematurely flip the in-flight PARSING_RESUME to
                # done before the gather completes).
                self._stage_status["ANALYZING_JOB"] = "running"
                self._current_stage = "ANALYZING_JOB"
                self._reporter.stage_start("ANALYZING_JOB")
                original_cv, job_analysis = await asyncio.gather(
                    self._parse_resume(resume_text, debug, verbose),
                    self._analyze_job(job_analysis_prompt, verbose),
                )
                self._complete_stage("PARSING_RESUME")
        except UnexpectedModelBehavior:
            self._complete_stage("PARSING_RESUME", success=False)
            self._complete_stage("ANALYZING_JOB", success=False)
            sys.exit(
                "❌ Resume parsing or job analysis failed: the agent did not return "
                "usable output after retries."
            )
        except (RuntimeError, ValueError) as e:
            self._complete_stage("ANALYZING_JOB", success=False)
            sys.exit(f"❌ {e}")
```
with
```python
                self._complete_stage("PARSING_RESUME")
                self._set_stage("ANALYZING_JOB")
                analyze_handle = await DBOS.start_workflow_async(
                    _analyze_job_workflow, job_analysis_prompt, self.MAX_RETRIES
                )
                job_analysis, self._analyze_usage = await analyze_handle.get_result(
                    polling_interval_sec=_CHILD_RESULT_POLL_SECONDS
                )
            else:
                # Parse and analyze run concurrently as two child workflows —
                # DBOS forbids interleaving two step sequences in one workflow,
                # and each child owns its own sequence. Show BOTH as running.
                # Mark ANALYZING_JOB running directly (do NOT use _set_stage,
                # which would prematurely flip the in-flight PARSING_RESUME to
                # done before both children complete).
                self._stage_status["ANALYZING_JOB"] = "running"
                self._current_stage = "ANALYZING_JOB"
                self._reporter.stage_start("ANALYZING_JOB")
                parse_handle = await DBOS.start_workflow_async(
                    _parse_resume_workflow, resume_text, self.MAX_RETRIES
                )
                analyze_handle = await DBOS.start_workflow_async(
                    _analyze_job_workflow, job_analysis_prompt, self.MAX_RETRIES
                )
                original_cv, self._parse_usage = await parse_handle.get_result(
                    polling_interval_sec=_CHILD_RESULT_POLL_SECONDS
                )
                job_analysis, self._analyze_usage = await analyze_handle.get_result(
                    polling_interval_sec=_CHILD_RESULT_POLL_SECONDS
                )
                self._complete_stage("PARSING_RESUME")
        except UnexpectedModelBehavior:
            self._complete_stage("PARSING_RESUME", success=False)
            self._complete_stage("ANALYZING_JOB", success=False)
            raise PipelineError(
                "Resume parsing or job analysis failed: the agent did not return "
                "usable output after retries."
            )
        except (RuntimeError, ValueError) as e:
            self._complete_stage("ANALYZING_JOB", success=False)
            raise PipelineError(str(e)) from e
```

(d) Hook 1 checkpoint call. Replace:

```python
                action, feedback_text = self._human_checkpoint(
                    header=hook1_header,
                    details=hook1_details,
                    choices=hook1_choices,
                    default="c",
                )
```
with
```python
                action, feedback_text = await _checkpoint_step(
                    hook1_header, hook1_details, hook1_choices, "c", self._interactive
                )
```

(e) Hook 2 checkpoint call. Replace:

```python
                    action, feedback_text = self._human_checkpoint(
                        header="⚠️  Weak Match — the resume may not pass ATS screening for this role.",
                        details=hook2_details,
                        choices=hook2_choices,
                        default="c",
                    )
```
with
```python
                    action, feedback_text = await _checkpoint_step(
                        "⚠️  Weak Match — the resume may not pass ATS screening for this role.",
                        hook2_details,
                        hook2_choices,
                        "c",
                        self._interactive,
                    )
```

(f) Gap analysis no longer reads `_analyst_result`. Replace:

```python
                gap_analysis = compute_gap_analysis(
                    original_cv,
                    new_cv,
                    self._analyst_result.output
                    if self._analyst_result and self._analyst_result.output
                    else JobAnalysis(),
                )
```
with
```python
                gap_analysis = compute_gap_analysis(original_cv, new_cv, job_analysis)
```

- [ ] **Step 7: Add `tailor_workflow` after the class**

Append at the end of `sira/workflows/__init__.py`:

```python
@DBOS.workflow(name=TAILOR_WORKFLOW_NAME)
async def tailor_workflow(inputs: TailorInputs) -> ResumeTailorResult:
    """The durable pipeline: one DBOS workflow per run.

    Model tiers and the quality gate are applied here, from the stored inputs,
    so a run continued by ``sira resume`` behaves exactly like the first run.
    The reporter comes from the active context (or the process-wide fallback
    when DBOS runs this on its background thread).
    """
    apply_model_override(inputs.model)
    if inputs.fast_model is not None or inputs.strong_model is not None:
        agents_mod.set_agent_models(fast=inputs.fast_model, strong=inputs.strong_model)
    agents_mod.set_quality_gate(
        enabled=inputs.quality_gate, threshold=inputs.gate_threshold
    )
    workflow = ResumeTailorWorkflow(
        write_attempts=inputs.write_attempts,
        review_iterations=inputs.review_iterations,
        interactive=inputs.interactive,
    )
    workflow._reporter = get_active_reporter()
    return await workflow._run_impl(
        inputs.resume_text,
        job_content_file_path=inputs.job_content_file_path,
        job_content=inputs.job_content,
        model=inputs.model,
        pre_parsed_cv=inputs.pre_parsed_cv,
        debug=inputs.debug,
        verbose=inputs.verbose,
    )
```

- [ ] **Step 8: Launch DBOS for the test session**

Append to `tests/conftest.py`:

```python
@pytest.fixture(scope="session", autouse=True)
def _dbos_runtime(tmp_path_factory):
    """One DBOS runtime for the whole suite, on a throwaway SQLite file.

    Workflows are registered at import, so import them before launching.
    """
    import sira.workflows  # noqa: F401
    from sira.durability import durable_runtime

    db = tmp_path_factory.mktemp("dbos") / "dbos.sqlite3"
    with durable_runtime(f"sqlite:///{db}"):
        yield


@pytest.fixture(autouse=True)
def _clear_global_reporter():
    """Never let a test's fallback reporter leak into the next test."""
    from sira.reporting.base import install_global_reporter

    yield
    install_global_reporter(None)
```

- [ ] **Step 9: Update the two existing tests the refactor changes**

In `tests/workflows/test_resume_tailor_workflow.py`, change the import line `from sira.workflows import ResumeTailorWorkflow` to `from sira.workflows import PipelineError, ResumeTailorWorkflow`, and replace the body of `test_analyst_failure_after_retries_exits` from `with pytest.raises(SystemExit)` onward with:

```python
    with pytest.raises(PipelineError) as excinfo:
        await ResumeTailorWorkflow().run("# resume", job_content="job posting")

    # The error carries a user-facing message that surfaces the underlying error.
    assert "simulated agent unavailable" in str(excinfo.value)
```

(and rename the test to `test_analyst_failure_after_retries_raises_pipeline_error`; update its docstring to "Analyst failure after all retries raises PipelineError with a user-facing message.").

In `tests/workflows/test_parallel_parse_analyze.py`, change `await asyncio.sleep(0.02)` to `await asyncio.sleep(0.2)` — starting a child workflow writes to SQLite first, so the overlap window must be longer than that write.

- [ ] **Step 10: Run the workflow tests, then the full gate**

```bash
uv run pytest tests/workflows tests/test_durability.py -q
```

Expected: all pass (3 new in `test_tailor_inputs.py`). A failure with `DBOSUnexpectedStepError` or "workflow not registered" means a `@DBOS.workflow`/`@DBOS.step` function was defined after launch or two step sequences interleaved — re-read Steps 4 and 6(c).

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest -q
```

Expected: ruff clean; `381 passed` (378 + 3). If ruff flags `asyncio` as unused in `sira/workflows/__init__.py`, the import block in Step 4 already omits it — make sure no stray `import asyncio` remains.

- [ ] **Step 11: Commit**

```bash
git add sira/models/workflow.py sira/workflows/__init__.py tests/conftest.py tests/workflows/test_resume_tailor_workflow.py tests/workflows/test_parallel_parse_analyze.py tests/workflows/test_tailor_inputs.py
git commit -m "feat: run the tailoring pipeline as a durable dbos workflow

The pipeline becomes the sira.tailor workflow with Parser and Analyst as
child workflows and the interactive checkpoint as a step, so a continued
run replays checkpointed results instead of repeating model calls. Hard
failures raise PipelineError instead of exiting the process, which lets
DBOS record the run as failed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Continuing a stored run (resume vs. fork) + durability tests

**Why:** DBOS's `resume_workflow` only works for runs that never finished (`PENDING`, `CANCELLED`, …); a run that ended in `ERROR` must be **forked** from the right step. This task adds that decision as a small pure function plus the DBOS call, and the integration tests that prove replay works end to end (Parser/Analyst not re-run after a Writer crash; an aborted run asks the checkpoint question again; a model request is recorded as a step; progress reaches the fallback reporter on the background thread).

**Files:**
- Create: `sira/workflows/continuation.py`
- Create: `tests/workflows/__init__.py` (empty — makes `tests.workflows.stubs` importable)
- Create: `tests/workflows/stubs.py` (shared pipeline stubs, also used by Task 6)
- Test (new): `tests/workflows/test_continuation.py`
- Test (new): `tests/workflows/test_durable_workflow.py`

**Interfaces:**
- Consumes: `CHECKPOINT_STEP_NAME`, `UserAbortedError`, `PipelineError`, `ResumeTailorWorkflow`, `tailor_workflow` (Task 4); `_durability`, `run_agent` (Task 3); `install_global_reporter` (Task 2); `dbos.DBOS`, `dbos.SetWorkflowID`.
- Produces (used by Task 6):
  - `RESUMABLE_STATUSES: frozenset[str]`
  - `fork_start_step(steps: list[dict], *, aborted: bool) -> int`
  - `continue_run(status) -> WorkflowHandleAsync` — `status` is a `dbos.WorkflowStatus`; resumes or forks; raises `ValueError` when the status cannot be continued (`SUCCESS`, or an unknown status).

- [ ] **Step 1: Write the failing pure-function tests**

Create `tests/workflows/test_continuation.py`:

```python
"""Which DBOS step a fork must start from, for each failure shape."""

import pytest

from sira.workflows import CHECKPOINT_STEP_NAME
from sira.workflows.continuation import RESUMABLE_STATUSES, fork_start_step


def _step(fid, name, *, error=None, child=None):
    return {
        "function_id": fid,
        "function_name": name,
        "error": error,
        "child_workflow_id": child,
        "output": None,
    }


def test_step_error_forks_from_that_step():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "sira.analyze_job", child="p-2"),
        _step(3, "DBOS.getResult", child="p-1"),
        _step(4, "DBOS.getResult", child="p-2"),
        _step(5, "sira.writer__model.request_stream", error=RuntimeError("x")),
    ]
    assert fork_start_step(steps, aborted=False) == 5


def test_failed_child_forks_from_the_childs_start_step():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "sira.analyze_job", child="p-2"),
        _step(3, "DBOS.getResult", child="p-1"),
        _step(4, "DBOS.getResult", child="p-2", error=ValueError("analyst died")),
    ]
    assert fork_start_step(steps, aborted=False) == 2


def test_workflow_level_error_forks_after_the_last_completed_step():
    steps = [_step(1, "sira.parse_resume", child="p-1"), _step(2, "DBOS.getResult", child="p-1")]
    assert fork_start_step(steps, aborted=False) == 3


def test_aborted_run_forks_at_the_last_checkpoint():
    steps = [
        _step(1, "sira.parse_resume", child="p-1"),
        _step(2, "DBOS.getResult", child="p-1"),
        _step(3, CHECKPOINT_STEP_NAME),
        _step(4, "sira.writer__model.request_stream"),
        _step(5, CHECKPOINT_STEP_NAME),
    ]
    assert fork_start_step(steps, aborted=True) == 5


def test_aborted_without_checkpoint_falls_back_to_error_rule():
    steps = [_step(1, "sira.parse_resume", child="p-1")]
    assert fork_start_step(steps, aborted=True) == 2


def test_no_steps_forks_from_the_beginning():
    assert fork_start_step([], aborted=False) == 1


@pytest.mark.parametrize("status", ["PENDING", "ENQUEUED", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"])
def test_resumable_statuses(status):
    assert status in RESUMABLE_STATUSES


def test_terminal_statuses_are_not_resumable():
    assert "SUCCESS" not in RESUMABLE_STATUSES
    assert "ERROR" not in RESUMABLE_STATUSES
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/workflows/test_continuation.py -q
```

Expected: `ModuleNotFoundError: No module named 'sira.workflows.continuation'`.

- [ ] **Step 3: Create `sira/workflows/continuation.py`**

```python
"""Continue a stored run: resume when DBOS can, fork from the right step otherwise.

DBOS facts this relies on (verified on dbos 2.31):
- ``resume_workflow`` only acts on runs that never finished; it is a no-op
  for SUCCESS and ERROR.
- ``fork_workflow(id, start_step)`` creates a new run that copies every
  checkpointed step before ``start_step`` and executes from there.
- Child workflow ids derive from the parent id, so forking a parent from the
  step that started a failed child gives that child a fresh id and re-runs it.
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS, WorkflowHandleAsync, WorkflowStatus

from sira.workflows import CHECKPOINT_STEP_NAME, UserAbortedError

# Statuses DBOS can resume in place (same run id).
RESUMABLE_STATUSES = frozenset(
    {"PENDING", "ENQUEUED", "DELAYED", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}
)


def fork_start_step(steps: list[dict[str, Any]], *, aborted: bool) -> int:
    """Return the 1-based step a fork should start from.

    - Aborted run (the user chose "quit"): the last checkpoint step, so the
      question is asked again.
    - A step recorded an error: the earliest such step. When that step is a
      child-result step, the fork must start at the step that *started* the
      child, or the copied child id would fail again.
    - No step recorded an error (the workflow raised between steps): the step
      after the last completed one.
    """
    if aborted:
        checkpoints = [
            s["function_id"] for s in steps if s["function_name"] == CHECKPOINT_STEP_NAME
        ]
        if checkpoints:
            return checkpoints[-1]
    failed_children = {
        s.get("child_workflow_id")
        for s in steps
        if s.get("error") is not None and s.get("child_workflow_id")
    }
    candidates = [
        s["function_id"]
        for s in steps
        if s.get("error") is not None or s.get("child_workflow_id") in failed_children
    ]
    if candidates:
        return min(candidates)
    return max((s["function_id"] for s in steps), default=0) + 1


async def continue_run(status: WorkflowStatus) -> WorkflowHandleAsync[Any]:
    """Resume or fork the run described by ``status``; return the handle to await.

    A forked run has a *new* workflow id (``handle.workflow_id``).
    Raises ValueError when there is nothing to continue.
    """
    if status.status in RESUMABLE_STATUSES:
        return await DBOS.resume_workflow_async(status.workflow_id)
    if status.status == "ERROR":
        steps = await DBOS.list_workflow_steps_async(status.workflow_id)
        aborted = isinstance(status.error, UserAbortedError)
        return await DBOS.fork_workflow_async(
            status.workflow_id, fork_start_step(steps, aborted=aborted)
        )
    raise ValueError(f"run {status.workflow_id} is {status.status}; nothing to continue")
```

- [ ] **Step 4: Run the pure tests**

```bash
uv run pytest tests/workflows/test_continuation.py -q
```

Expected: `11 passed`.

- [ ] **Step 5: Create the shared pipeline stubs**

Create an empty `tests/workflows/__init__.py`, then `tests/workflows/stubs.py`:

```python
"""Stubs for every pipeline agent, shared by the durability and CLI tests.

Agents are stubbed at ``agent.run`` (no model calls); the DBOS machinery —
workflow, child workflows, checkpoint step, continuation — stays real.
"""

from sira.models.agents.output import (
    AuditResult,
    CVDiff,
    FinalReport,
    GapAnalysis,
    JobAnalysis,
    ReviewResult,
)


class DummyRunResult:
    def __init__(self, output):
        self.output = output


class FakeStdin:
    """stdin stand-in with a controllable ``isatty()``."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def install_pipeline_stubs(
    monkeypatch, sample_cv, *, audit_passed=True, writer_fail_once=False
) -> dict[str, int]:
    """Stub every pipeline agent; return the per-agent call counters."""
    calls = {
        "parser": 0,
        "analyst": 0,
        "writer": 0,
        "reviewer": 0,
        "auditor": 0,
        "report": 0,
    }

    async def run_parser(*a, **k):
        calls["parser"] += 1
        return DummyRunResult(sample_cv)

    async def run_analyst(*a, **k):
        calls["analyst"] += 1
        return DummyRunResult(
            JobAnalysis(
                job_title="Platform Engineer",
                company_name="Acme",
                summary="role",
                hard_skills=["Python"],
                soft_skills=["Communication"],
                key_responsibilities=["Build"],
                keywords_to_target=["Python"],
            )
        )

    async def run_writer(*a, **k):
        calls["writer"] += 1
        if writer_fail_once and calls["writer"] == 1:
            raise RuntimeError("simulated crash in writer")
        return DummyRunResult(sample_cv)

    async def run_reviewer(*a, **k):
        calls["reviewer"] += 1
        return DummyRunResult(
            ReviewResult(
                quality_score=9,
                needs_improvement=False,
                specific_suggestions=[],
                strengths=["ok"],
            )
        )

    async def run_auditor(*a, **k):
        calls["auditor"] += 1
        return DummyRunResult(
            AuditResult(
                passed=audit_passed,
                hallucination_score=0,
                ai_cliche_score=0,
                issues=[],
                feedback_summary="fine" if audit_passed else "needs work",
            )
        )

    async def run_report(*a, **k):
        calls["report"] += 1
        return DummyRunResult(
            FinalReport(
                job_title="Platform Engineer",
                company_name="Acme",
                generated_at="2026-01-01T00:00:00Z",
                overall_recommendation="Strong Match",
                match_score=90,
                what_changed=CVDiff(),
                gaps=GapAnalysis(),
                suggestions_to_strengthen=[],
                audit_summary="ok",
                recommendation_rationale="ok",
                passed=audit_passed,
            )
        )

    for target, fn in [
        ("resume_parser_agent", run_parser),
        ("analyst_agent", run_analyst),
        ("writer_agent", run_writer),
        ("reviewer_agent", run_reviewer),
        ("auditor_agent", run_auditor),
        ("report_agent", run_report),
    ]:
        monkeypatch.setattr(f"sira.workflows.agents.{target}.run", fn)
    return calls
```

- [ ] **Step 5b: Write the integration tests**

Create `tests/workflows/test_durable_workflow.py`:

```python
"""End-to-end durability: replay, fork, checkpoint, steps, fallback reporter."""

import uuid

import pytest
from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from sira.models.workflow import ResumeTailorResult, TailorInputs
from sira.reporting.base import install_global_reporter, use_reporter
from sira.workflows import (
    ANALYZE_WORKFLOW_NAME,
    CHECKPOINT_STEP_NAME,
    PARSE_WORKFLOW_NAME,
    TAILOR_WORKFLOW_NAME,
    ResumeTailorWorkflow,
    UserAbortedError,
)
from sira.workflows.agents import _durability, run_agent
from sira.workflows.continuation import continue_run
from tests.reporting.test_base import RecordingReporter
from tests.workflows.stubs import FakeStdin, install_pipeline_stubs


@pytest.mark.anyio
async def test_run_is_recorded_as_a_sira_tailor_workflow_with_two_children(monkeypatch, sample_cv):
    install_pipeline_stubs(monkeypatch, sample_cv)
    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        result = await ResumeTailorWorkflow().run("# resume", job_content="job")
    assert result.passed is True

    handle = await DBOS.retrieve_workflow_async(run_id)
    status = await handle.get_status()
    assert status.status == "SUCCESS"
    assert status.name == TAILOR_WORKFLOW_NAME
    assert isinstance(status.input["args"][0], TailorInputs)
    assert isinstance(await handle.get_result(), ResumeTailorResult)  # stored output

    children = await DBOS.list_workflows_async(parent_workflow_id=run_id)
    assert {c.name for c in children} == {PARSE_WORKFLOW_NAME, ANALYZE_WORKFLOW_NAME}
    assert all(c.status == "SUCCESS" for c in children)


@pytest.mark.anyio
async def test_fork_after_writer_crash_replays_parser_and_analyst(monkeypatch, sample_cv):
    calls = install_pipeline_stubs(monkeypatch, sample_cv, writer_fail_once=True)
    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(RuntimeError, match="simulated crash"):
            await ResumeTailorWorkflow().run("# resume", job_content="job")
    assert (calls["parser"], calls["analyst"], calls["writer"]) == (1, 1, 1)

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "ERROR"

    # The continued run executes on DBOS's background thread: only the
    # process-wide fallback reporter can see its progress.
    rec = RecordingReporter()
    install_global_reporter(rec)
    handle = await continue_run(status)
    assert handle.workflow_id != run_id  # a fork is a new run
    result = await handle.get_result()

    assert result.passed is True
    assert calls["parser"] == 1 and calls["analyst"] == 1  # replayed, not re-run
    assert calls["writer"] == 2
    assert ("stage_start", "WRITING_CV") in rec.events


@pytest.mark.anyio
async def test_aborted_run_is_asked_the_checkpoint_question_again(monkeypatch, sample_cv):
    calls = install_pipeline_stubs(monkeypatch, sample_cv, audit_passed=False)
    monkeypatch.setattr("sys.stdin", FakeStdin(is_tty=True))
    answers = iter(["q", "c"])
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)

    run_id = str(uuid.uuid4())
    with SetWorkflowID(run_id):
        with pytest.raises(UserAbortedError):
            await ResumeTailorWorkflow(interactive=True).run("# resume", job_content="job")
    assert len(asked) == 1

    status = await (await DBOS.retrieve_workflow_async(run_id)).get_status()
    assert status.status == "ERROR"
    assert isinstance(status.error, UserAbortedError)
    steps = await DBOS.list_workflow_steps_async(run_id)
    assert any(s["function_name"] == CHECKPOINT_STEP_NAME for s in steps)

    handle = await continue_run(status)
    result = await handle.get_result()
    assert len(asked) == 2  # forked at the checkpoint: asked once more
    assert result.passed is False
    assert calls["parser"] == 1 and calls["analyst"] == 1


# A throwaway durable agent inside a workflow: the model request itself must
# be a checkpointed step, and its tokens must stream to the reporter.
_probe_agent = Agent(
    TestModel(custom_output_text="probe says hi"),
    name="sira.test_durable",
    capabilities=[_durability()],
)


@DBOS.workflow(name="sira.test_agent_workflow")
async def _agent_workflow() -> str:
    result = await run_agent(_probe_agent, "hi", agent_label="Probe")
    return result.output


@pytest.mark.anyio
async def test_model_request_inside_workflow_is_a_step_and_streams():
    rec = RecordingReporter()
    rec.wants_tokens = True
    run_id = str(uuid.uuid4())
    with use_reporter(rec):
        with SetWorkflowID(run_id):
            output = await _agent_workflow()
    assert output == "probe says hi"

    steps = await DBOS.list_workflow_steps_async(run_id)
    names = [s["function_name"] for s in steps]
    assert any(n.startswith("sira.test_durable__model.request") for n in names), names

    tokens = [e for e in rec.events if e[0] == "token"]
    assert "".join(e[2] for e in tokens) == "probe says hi"
    assert {e[1] for e in tokens} == {"Probe"}
```

- [ ] **Step 6: Run the integration tests**

```bash
uv run pytest tests/workflows/test_durable_workflow.py -q
```

Expected: `4 passed` (each fork/resume test takes ~1–2 s: `get_result` polls). If `test_fork_after_writer_crash…` re-runs the parser (`calls["parser"] == 2`), the fork started too early — check `fork_start_step` against the printed `DBOS.list_workflow_steps_async(run_id)` output.

- [ ] **Step 7: Full gate and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest -q
```

Expected: ruff clean; `396 passed` (381 + 11 + 4).

```bash
git add sira/workflows/continuation.py tests/workflows/__init__.py tests/workflows/stubs.py tests/workflows/test_continuation.py tests/workflows/test_durable_workflow.py
git commit -m "feat: continue a stored run by resuming or forking it

DBOS resumes only runs that never finished; a run that ended in error is
forked from the right step: the failed step, the start of a failed child
workflow, the last checkpoint for an aborted run, or right after the last
completed step. Integration tests prove earlier agents are replayed, not
re-run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: CLI — run ids, `sira resume`, `sira runs`

**Files:**
- Modify: `sira/main.py`
- Test (new): `tests/test_cli_resume.py`
- Existing CLI tests (`tests/test_main.py`, `tests/test_cli_typer.py`) must keep passing unchanged: they patch `sira.main.ResumeTailorWorkflow` with a mock whose `run` is an `AsyncMock`, and `_run_workflow` keeps calling `workflow.run(...)`.

**Interfaces:**
- Consumes: `durable_runtime`, `application_version` (Task 1); `install_global_reporter` (Task 2); `RunMetadata`, `TailorInputs`, `PipelineError`, `TAILOR_WORKFLOW_NAME` (Task 4); `continue_run` (Task 5); `dbos.DBOS`, `dbos.SetWorkflowID`.
- Produces: commands `sira resume <run-id> [-v]` and `sira runs [--limit N]`; helpers `_write_outputs`, `_save_tailor_to_memory`, `_save_re_tailor_to_memory`, `_resume_impl`, `_runs_impl`; `_tailor_impl`/`_re_tailor_impl` gain `run_id: str | None = None`; `_run_workflow` gains `metadata: RunMetadata | None = None, run_id: str | None = None`.

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_cli_resume.py`:

```python
"""`sira runs` and `sira resume` against the real DBOS runtime (agents stubbed)."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbos import SetWorkflowID
from typer.testing import CliRunner

from sira.main import app
from sira.models.workflow import RunMetadata
from sira.workflows import ResumeTailorWorkflow
from tests.workflows.stubs import install_pipeline_stubs

runner = CliRunner()


def _memory_patches():
    service = MagicMock(
        aresolve_original_resume=AsyncMock(
            return_value=MagicMock(source=MagicMock(id="src"), cv=None)
        ),
        save_tailored_resume=MagicMock(return_value=MagicMock(id="job-1")),
    )
    return [
        patch("sira.main.generate_resume", MagicMock(return_value="/fake/resume.md")),
        patch("sira.main.SQLiteResumeMemoryRepository", MagicMock()),
        patch("sira.main.PydanticAIResumeParser", MagicMock()),
        patch("sira.main.ResumeMemoryService", MagicMock(return_value=service)),
    ]


def _start_run(tmp_path, sample_cv, monkeypatch, **stub_kwargs):
    """Run the stubbed pipeline once under a known run id; return (run_id, calls, exc)."""
    calls = install_pipeline_stubs(monkeypatch, sample_cv, **stub_kwargs)
    run_id = str(uuid.uuid4())
    metadata = RunMetadata(
        job_url="https://example.com/job/1",
        resume_source_path=str(tmp_path / "resume.md"),
        output_dir=str(tmp_path / "out"),
        job_posting_markdown="# job",
    )
    exc = None
    try:
        with SetWorkflowID(run_id):
            asyncio.run(
                ResumeTailorWorkflow().run("# resume", job_content="job", metadata=metadata)
            )
    except Exception as e:  # noqa: BLE001 — the crash we set up
        exc = e
    return run_id, calls, exc


def test_runs_lists_recent_runs(tmp_path, sample_cv, monkeypatch):
    run_id, _, exc = _start_run(tmp_path, sample_cv, monkeypatch)
    assert exc is None
    result = runner.invoke(app, ["runs", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert run_id[:8] in result.output
    assert "SUCCESS" in result.output
    assert "example.com/job/1" in result.output


def test_resume_of_completed_run_reuses_result_without_rerunning(tmp_path, sample_cv, monkeypatch):
    run_id, calls, exc = _start_run(tmp_path, sample_cv, monkeypatch)
    assert exc is None
    before = dict(calls)
    patches = _memory_patches()
    for p in patches:
        p.start()
    try:
        result = runner.invoke(app, ["resume", run_id])
    finally:
        for p in patches:
            p.stop()
    assert result.exit_code == 0, result.output
    assert "already completed" in result.output
    assert calls == before  # nothing re-ran
    assert "Report saved to" in result.output


def test_resume_after_error_continues_from_the_last_checkpoint(tmp_path, sample_cv, monkeypatch):
    run_id, calls, exc = _start_run(tmp_path, sample_cv, monkeypatch, writer_fail_once=True)
    assert isinstance(exc, RuntimeError)
    assert calls["writer"] == 1
    patches = _memory_patches()
    for p in patches:
        p.start()
    try:
        result = runner.invoke(app, ["resume", run_id])
    finally:
        for p in patches:
            p.stop()
    assert result.exit_code == 0, result.output
    assert "Continued as run" in result.output  # a fork has a new id
    assert calls["parser"] == 1 and calls["analyst"] == 1  # replayed
    assert calls["writer"] == 2


def test_resume_unknown_run_id_fails_cleanly():
    result = runner.invoke(app, ["resume", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 1
    assert "Unknown run id" in result.output


def test_resume_refuses_a_run_from_another_sira_version(tmp_path, sample_cv, monkeypatch):
    run_id, _, _ = _start_run(tmp_path, sample_cv, monkeypatch)
    monkeypatch.setattr("sira.main.application_version", lambda: "9.9.9")
    result = runner.invoke(app, ["resume", run_id])
    assert result.exit_code == 1
    assert "9.9.9" in result.output
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_cli_resume.py -q
```

Expected: every test fails (`No such command 'runs'` / `'resume'`, or `TypeError: run() got an unexpected keyword argument 'metadata'` before Task 4 — all five must be red).

- [ ] **Step 3: Imports in `sira/main.py`**

Add to the import block (keep alphabetical groups):

```python
import uuid
from datetime import date, datetime

from dbos import DBOS, SetWorkflowID
from rich.table import Table

from sira.durability import application_version, durable_runtime
from sira.models.workflow import ResumeTailorResult, RunMetadata, TailorInputs
from sira.reporting.base import install_global_reporter, use_reporter
from sira.workflows import (
    TAILOR_WORKFLOW_NAME,
    PipelineError,
    ResumeTailorWorkflow,
    UserAbortedError,
)
from sira.workflows.continuation import continue_run
```

(`from datetime import date` becomes `from datetime import date, datetime`; the existing `from sira.models.workflow import ResumeTailorResult`, `from sira.reporting.base import use_reporter`, and `from sira.workflows import ResumeTailorWorkflow, UserAbortedError` lines are replaced by the ones above.)

- [ ] **Step 4: Split `_run_workflow` into run + output writing**

Replace the whole `_run_workflow` function with the two functions below. `_write_outputs` is the old tail of `_run_workflow` (from `resume_path = None` to the end) moved verbatim into its own function, with the parameters it used.

```python
def _write_outputs(
    result: ResumeTailorResult,
    *,
    resume_content: str,
    output_dir: str,
    output_pattern: str,
    resume_name_pattern: str,
    debug: bool,
) -> tuple[int, str | None, str | None]:
    """Write the tailored CV and the report; return (exit_code, resume_path, report_path).

    Shared by `tailor`, `re-tailor`, and `resume`, so a continued run produces
    the same files as an uninterrupted one.
    """
    resume_path = None
    report_path = None

    # Guard CV parsing: workflow may return empty or invalid tailored_resume
    full_name = ""
    if result.tailored_resume:
        try:
            cv = CV.model_validate_json(result.tailored_resume)
            full_name = cv.full_name
        except (ValidationError, ValueError):
            full_name = ""

    # Build a minimal CV-like object for pattern resolution if parsing failed
    cv_fallback = CV(
        full_name=full_name or "unknown",
        summary="",
        skills=[],
        experience=[],
        education=[],
    )

    # Resolve directory and file name patterns
    job_dir_name = _resolve_pattern(output_pattern, result, cv_fallback)
    if not _is_safe_path_component(job_dir_name):
        console.print(
            f"[red]❌ Invalid output pattern resolves to unsafe path: {job_dir_name}[/red]"
        )
        raise typer.Exit(code=1)
    job_dir = os.path.join(output_dir, job_dir_name)
    os.makedirs(job_dir, exist_ok=True)

    if debug:
        debug_path = os.path.join(job_dir, "resume_debug.md")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(resume_content)
        console.print(f"🔍 [Debug] Converted resume saved to: {debug_path}")
        console.print(
            f"🔍 [Debug] First 500 chars of resume sent to parser:\n"
            f"{resume_content[:500]}"
        )

    resume_base_name = _resolve_pattern(resume_name_pattern, result, cv_fallback)
    if not _is_safe_path_component(resume_base_name):
        console.print(
            f"[red]❌ Invalid resume name pattern resolves to unsafe path: {resume_base_name}[/red]"
        )
        raise typer.Exit(code=1)

    if result.passed:
        console.print("\n✅ Audit Passed. Saving CV...")
        resume_path = generate_resume(result, job_dir, resume_base_name)
    else:
        console.print("\n❌ Audit Failed. Please review the feedback and try again.")
        feedback = result.audit_report.get("feedback_summary", "No feedback available")
        console.print(f"Feedback: {feedback}")

    if result.final_report is not None:
        _print_report_to_console(result.final_report)

        report_md = generate_report_markdown(result.final_report)
        report_path = os.path.join(job_dir, f"{resume_base_name}_report.md")

        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_md)
            console.print(f"\n📄 Report saved to: {report_path}")
        except IOError as e:
            console.print(f"⚠️ Error writing report file: {e}")
    else:
        console.print("\n⚠️ Self-review report could not be generated.")

    return 0, resume_path, report_path


_EMPTY_RESULT = ResumeTailorResult(
    company_name="", job_title="", tailored_resume="", audit_report={}, passed=False
)


async def _run_workflow(
    resume_content: str,
    job_posting_markdown: str,
    output_dir: str,
    model: str | None,
    recommendations: str = "",
    verbose: bool = False,
    output_pattern: str = "{company_name}-{job_title}",
    resume_name_pattern: str = "{company_name}-{full_name}",
    pre_parsed_cv: CV | None = None,
    debug: bool = False,
    write_attempts: int = 2,
    review_iterations: int = 1,
    quality_gate: bool = True,
    gate_threshold: int = 6,
    reporter=None,
    interactive: bool = False,
    metadata: RunMetadata | None = None,
    run_id: str | None = None,
) -> tuple[int, str | None, str | None, ResumeTailorResult]:
    set_quality_gate(enabled=quality_gate, threshold=gate_threshold)
    workflow = ResumeTailorWorkflow(
        write_attempts=write_attempts,
        review_iterations=review_iterations,
        interactive=interactive,
    )

    job_content = job_posting_markdown
    if recommendations:
        job_content += f"\n\n---\n**Additional recommendations from prior audit:**\n{recommendations}\n"

    # The run id is the DBOS workflow id: what `sira resume` takes.
    run_id = run_id or str(uuid.uuid4())
    console.print(f"🧾 Run ID: {run_id}  (continue later with: sira resume {run_id})")

    try:
        with SetWorkflowID(run_id):
            result = await workflow.run(
                resume_content,
                job_content=job_content,
                model=model,
                pre_parsed_cv=pre_parsed_cv,
                debug=debug,
                verbose=verbose,
                reporter=reporter,
                metadata=metadata,
            )
    except UserAbortedError as e:
        console.print(f"[yellow]🚫 {e}[/yellow]")
        return (1, None, None, _EMPTY_RESULT)
    except PipelineError as e:
        console.print(f"[red]❌ {e}[/red]")
        console.print(
            f"[yellow]💡 Retry from the last checkpoint with: sira resume {run_id}[/yellow]"
        )
        return (1, None, None, _EMPTY_RESULT)

    exit_code, resume_path, report_path = _write_outputs(
        result,
        resume_content=resume_content,
        output_dir=output_dir,
        output_pattern=output_pattern,
        resume_name_pattern=resume_name_pattern,
        debug=debug,
    )
    return exit_code, resume_path, report_path, result
```

- [ ] **Step 5: Memory-save helpers (extracted from `_tailor_impl` / `_re_tailor_impl`)**

Add after `_run_workflow`:

```python
async def _save_tailor_to_memory(
    result: ResumeTailorResult,
    *,
    job_url: str,
    source_path: str,
    job_posting_markdown: str,
) -> str | None:
    """Persist a `tailor` result; return the job id, or None when saving failed."""
    try:
        repo = SQLiteResumeMemoryRepository()
        parser = PydanticAIResumeParser()
        service = ResumeMemoryService(repository=repo, parser=parser)

        # Use converted markdown path for non-markdown resumes so
        # resolve_original_resume can read it as text.
        resolved = await service.aresolve_original_resume(path=source_path)
        job_fingerprint = _get_job_fingerprint(job_url, result.job_title)

        audit = _audit_result_from_dict(result.audit_report)

        if result.tailored_resume:
            tailored_cv = CV.model_validate_json(result.tailored_resume)
        else:
            tailored_cv = resolved.cv

        record = service.save_tailored_resume(
            source_id=resolved.source.id,
            job_fingerprint=job_fingerprint,
            company_name=result.company_name,
            job_title=result.job_title,
            tailored_cv=tailored_cv,
            audit_result=audit,
            job_posting_markdown=job_posting_markdown,
        )
        console.print(f"\n💾 Job ID: {record.id}")
        return record.id
    except Exception as e:
        logger.warning("Failed to persist tailored resume", exc_info=True)
        console.print(f"[yellow]⚠️ Failed to save job to memory: {e}[/yellow]")
        return None


def _save_re_tailor_to_memory(
    result: ResumeTailorResult,
    *,
    job_id: str,
    job_posting_markdown: str,
    fallback_cv: CV | None = None,
) -> bool:
    """Update the prior job record after a `re-tailor`; return True on success."""
    try:
        repo = SQLiteResumeMemoryRepository()
        tailored_record = repo.get_tailored_resume_by_id(job_id)
        if tailored_record is None:
            console.print(f"[yellow]⚠️ Prior job not found in memory: {job_id}[/yellow]")
            return False
        audit = _audit_result_from_dict(result.audit_report)

        if result.tailored_resume:
            tailored_cv = CV.model_validate_json(result.tailored_resume)
        elif fallback_cv is not None:
            tailored_cv = fallback_cv
        else:
            tailored_cv = CV.model_validate_json(tailored_record.tailored_cv_json)

        repo.save_tailored_resume(
            source_id=tailored_record.source_id,
            job_fingerprint=tailored_record.job_fingerprint,
            company_name=result.company_name,
            job_title=result.job_title,
            tailored_cv_json=tailored_cv.model_dump_json(),
            audit_report_json=audit.model_dump_json(),
            job_posting_markdown=job_posting_markdown,
        )
        return True
    except Exception as e:
        logger.warning("Failed to update tailored resume record", exc_info=True)
        console.print(f"[yellow]⚠️ Failed to update job record: {e}[/yellow]")
        return False
```

- [ ] **Step 6: `_tailor_impl` — run id, durable runtime, metadata**

Add `run_id: str | None = None,` as the last parameter of `_tailor_impl`. Then replace everything from the comment `# LiveDashboard is a context manager (drives a Rich Live panel);` down to the function's final `return exit_code` with:

```python
    source_path = converted_resume_path or resume_path_expanded
    metadata = RunMetadata(
        job_url=job_url,
        resume_source_path=source_path,
        output_dir=output_dir,
        output_pattern=output_pattern,
        resume_name_pattern=resume_name_pattern,
    )

    # LiveDashboard is a context manager (drives a Rich Live panel);
    # VerboseReporter is not, so fall back to a nullcontext for it.
    dashboard_ctx = (
        reporter if hasattr(reporter, "__enter__") else contextlib.nullcontext()
    )
    # durable_runtime launches DBOS for this process (a no-op when a runtime
    # is already active, e.g. in the test suite).
    with durable_runtime(), dashboard_ctx, use_reporter(reporter):
        install_global_reporter(reporter)
        try:
            logger.info("scraping_job_posting", extra={"url": job_url})
            try:
                raw = await fetch_job_markdown(job_url)
                scrape_result = await run_agent(
                    job_scraper_agent,
                    raw.markdown_raw,
                    verbose=verbose,
                    agent_label="Scraper",
                )
                job_posting_markdown = scrape_result.output
                if not job_posting_markdown.strip():
                    logger.error(
                        "job_posting_scraped_but_empty", extra={"url": job_url}
                    )
                    console.print(
                        "[red]❌ Job posting scraped but content is empty[/red]"
                    )
                    raise typer.Exit(code=1)
                logger.info(
                    "job_posting_scraped_successfully",
                    extra={
                        "url": job_url,
                        "content_length": len(job_posting_markdown),
                    },
                )
                console.print(f"✅ Job posting scraped successfully from {job_url}")
            except (typer.Exit, KeyboardInterrupt):
                raise
            except Exception as e:
                logger.error(
                    "job_posting_scraping_failed",
                    extra={"url": job_url, "error": str(e)},
                )
                console.print(
                    f"[red]❌ Failed to scrape job posting from URL: {e}[/red]"
                )
                console.print(
                    "[yellow]💡 Tip: Ensure the URL is publicly accessible and contains a valid job posting.[/yellow]"
                )
                raise typer.Exit(code=1)

            metadata.job_posting_markdown = job_posting_markdown
            exit_code, resume_path_out, report_path_out, result = await _run_workflow(
                resume_content,
                job_posting_markdown,
                output_dir,
                model,
                verbose=verbose,
                output_pattern=output_pattern,
                resume_name_pattern=resume_name_pattern,
                pre_parsed_cv=pre_parsed_cv,
                debug=debug,
                write_attempts=write_attempts,
                review_iterations=review_iterations,
                quality_gate=quality_gate,
                gate_threshold=gate_threshold,
                reporter=reporter,
                interactive=interactive,
                metadata=metadata,
                run_id=run_id,
            )
        finally:
            install_global_reporter(None)

    if exit_code == 0:
        await _save_tailor_to_memory(
            result,
            job_url=job_url,
            source_path=source_path,
            job_posting_markdown=job_posting_markdown,
        )
        console.print("\n✅ Job completed")
        console.print(f"📄 Tailored CV: {resume_path_out}")
        console.print(f"📊 Report: {report_path_out}")

    return exit_code
```

- [ ] **Step 7: `_re_tailor_impl` — the same treatment**

Add `run_id: str | None = None,` as the last parameter. Replace everything from the comment `# LiveDashboard is a context manager (drives a Rich Live panel);` down to the function's final `return exit_code` with:

```python
    metadata = RunMetadata(
        job_id=job_id,
        resume_source_path=_resume_source_path or "",
        output_dir=output_dir,
        output_pattern=output_pattern,
        resume_name_pattern=resume_name_pattern,
        job_posting_markdown=job_posting_markdown,
    )

    # LiveDashboard is a context manager (drives a Rich Live panel);
    # VerboseReporter is not, so fall back to a nullcontext for it.
    dashboard_ctx = (
        reporter if hasattr(reporter, "__enter__") else contextlib.nullcontext()
    )
    with durable_runtime(), dashboard_ctx, use_reporter(reporter):
        install_global_reporter(reporter)
        try:
            exit_code, resume_path_out, report_path_out, result = await _run_workflow(
                resume_content,
                job_posting_markdown,
                output_dir,
                model,
                recommendations=recommendations,
                verbose=verbose,
                output_pattern=output_pattern,
                resume_name_pattern=resume_name_pattern,
                pre_parsed_cv=pre_parsed_cv,
                debug=debug,
                write_attempts=write_attempts,
                review_iterations=review_iterations,
                quality_gate=quality_gate,
                gate_threshold=gate_threshold,
                reporter=reporter,
                interactive=interactive,
                metadata=metadata,
                run_id=run_id,
            )
        finally:
            install_global_reporter(None)

    if exit_code == 0:
        saved = _save_re_tailor_to_memory(
            result,
            job_id=job_id,
            job_posting_markdown=job_posting_markdown,
            fallback_cv=resolved.cv if resolved else None,
        )
        if saved and resume_path_out and report_path_out:
            console.print(
                f"\n✅ Re-tailoring completed: {result.company_name} / {result.job_title}"
            )
            console.print(f"📄 Updated CV: {resume_path_out}")
            console.print(f"📊 Updated Report: {report_path_out}")

    return exit_code
```

- [ ] **Step 8: `tailor` / `re_tailor` commands — generate the run id and handle Ctrl+C**

In the `tailor` command replace `return asyncio.run(` … `)` (the whole return statement) with:

```python
    run_id = str(uuid.uuid4())
    try:
        return asyncio.run(
            _tailor_impl(
                job_url,
                resume_path,
                output_dir,
                model,
                verbose=verbose,
                output_pattern=output_pattern,
                resume_name_pattern=resume_name_pattern,
                debug=debug,
                write_attempts=write_attempts,
                review_iterations=review_iterations,
                quality_gate=quality_gate,
                gate_threshold=gate_threshold,
                fast=fast,
                interactive=interactive,
                run_id=run_id,
            )
        )
    except KeyboardInterrupt:
        console.print(
            f"\n⏹  Interrupted. If the pipeline had started, continue it with: sira resume {run_id}"
        )
        raise typer.Exit(code=130)
```

Do the same in `re_tailor` (its existing argument list plus `run_id=run_id`, same `except KeyboardInterrupt` block).

- [ ] **Step 9: Add the `resume` and `runs` commands**

Add before `def run():`:

```python
async def _resume_impl(run_id: str, *, verbose: bool = False) -> int:
    """Continue a stored run and finish its post-processing (files, memory)."""
    reporter = (
        VerboseReporter(console=console) if verbose else LiveDashboard(console=console)
    )
    with durable_runtime():
        try:
            handle = await DBOS.retrieve_workflow_async(run_id)
            status = await handle.get_status()
        except Exception:
            console.print(f"[red]❌ Unknown run id: {run_id}[/red]")
            return 1
        if status.name != TAILOR_WORKFLOW_NAME:
            console.print(f"[red]❌ {run_id} is not a tailoring run ({status.name})[/red]")
            return 1
        if status.app_version != application_version():
            console.print(
                f"[red]❌ This run was started with Sira {status.app_version}; "
                f"installed is {application_version()}. Start a new run.[/red]"
            )
            return 1
        inputs = status.input["args"][0] if status.input else None
        if not isinstance(inputs, TailorInputs):
            console.print(f"[red]❌ Stored inputs for {run_id} are unreadable[/red]")
            return 1

        if status.status == "SUCCESS":
            console.print("♻️  Run already completed — reusing its result")
            result = await handle.get_result()  # the stored output, no re-run
        else:
            dashboard_ctx = (
                reporter if hasattr(reporter, "__enter__") else contextlib.nullcontext()
            )
            with dashboard_ctx, use_reporter(reporter):
                # The continued run executes on DBOS's background thread,
                # where only the process-wide reporter is visible.
                install_global_reporter(reporter)
                try:
                    try:
                        handle = await continue_run(status)
                    except ValueError as e:
                        console.print(f"[red]❌ {e}[/red]")
                        return 1
                    if handle.workflow_id != run_id:
                        console.print(f"🧾 Continued as run: {handle.workflow_id}")
                    try:
                        result = await handle.get_result()
                    except UserAbortedError as e:
                        console.print(f"[yellow]🚫 {e}[/yellow]")
                        return 1
                    except PipelineError as e:
                        console.print(f"[red]❌ {e}[/red]")
                        console.print(
                            f"[yellow]💡 Retry with: sira resume {handle.workflow_id}[/yellow]"
                        )
                        return 1
                finally:
                    install_global_reporter(None)

    meta = inputs.metadata
    exit_code, resume_path_out, report_path_out = _write_outputs(
        result,
        resume_content=inputs.resume_text,
        output_dir=meta.output_dir,
        output_pattern=meta.output_pattern,
        resume_name_pattern=meta.resume_name_pattern,
        debug=inputs.debug,
    )
    if exit_code == 0:
        if meta.job_id:
            _save_re_tailor_to_memory(
                result,
                job_id=meta.job_id,
                job_posting_markdown=meta.job_posting_markdown,
                fallback_cv=inputs.pre_parsed_cv,
            )
        else:
            await _save_tailor_to_memory(
                result,
                job_url=meta.job_url or "",
                source_path=meta.resume_source_path,
                job_posting_markdown=meta.job_posting_markdown,
            )
        console.print("\n✅ Job completed")
        console.print(f"📄 Tailored CV: {resume_path_out}")
        console.print(f"📊 Report: {report_path_out}")
    return exit_code


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run ID printed by `sira tailor` / `sira re-tailor`"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Stream agent thinking and prompts in real-time"
    ),
) -> None:
    """Continue a killed, crashed, or failed run from its last checkpoint."""
    try:
        code = asyncio.run(_resume_impl(run_id, verbose=verbose))
    except KeyboardInterrupt:
        console.print(f"\n⏹  Interrupted. Continue again with: sira resume {run_id}")
        raise typer.Exit(code=130)
    raise typer.Exit(code=code)


async def _runs_impl(limit: int) -> None:
    with durable_runtime():
        statuses = await DBOS.list_workflows_async(
            name=TAILOR_WORKFLOW_NAME, sort_desc=True, limit=limit, load_output=False
        )
    if not statuses:
        console.print("No runs recorded yet.")
        return
    table = Table(title="Recent runs")
    for column in ("Run ID", "Status", "Job", "Started", "Duration"):
        table.add_column(column)
    for st in statuses:
        inputs = st.input["args"][0] if st.input else None
        meta = inputs.metadata if isinstance(inputs, TailorInputs) else RunMetadata()
        job = meta.job_url or (f"re-tailor of {meta.job_id}" if meta.job_id else "-")
        started = (
            datetime.fromtimestamp(st.created_at / 1000).strftime("%Y-%m-%d %H:%M")
            if st.created_at
            else "-"
        )
        end = st.completed_at or st.updated_at
        duration = f"{(end - st.created_at) / 1000:.0f}s" if st.created_at and end else "-"
        table.add_row(st.workflow_id, st.status, job, started, duration)
    console.print(table)


@app.command()
def runs(
    limit: int = typer.Option(10, help="How many recent runs to show"),
) -> None:
    """List recent tailoring runs and their status."""
    asyncio.run(_runs_impl(limit))
```

- [ ] **Step 10: Run the new CLI tests, the existing CLI tests, then the gate**

```bash
uv run pytest tests/test_cli_resume.py tests/test_main.py tests/test_cli_typer.py -q
```

Expected: all pass (5 new). If `test_runs_lists_recent_runs` cannot find the run id, check that Rich did not wrap the table: the test only checks the first 8 characters of the id, which fit in any terminal width.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest -q
```

Expected: ruff clean; `401 passed` (396 + 5).

- [ ] **Step 11: Commit**

```bash
git add sira/main.py tests/test_cli_resume.py
git commit -m "feat: add sira resume and sira runs commands

Every tailor and re-tailor run now has a run id (the DBOS workflow id)
printed at start. \`sira resume <run-id>\` continues a killed, crashed, or
failed run from its last checkpoint and finishes the output files and
memory save; \`sira runs\` lists recent runs. Ctrl+C and pipeline failures
print the resume hint instead of losing the work.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation, final gate, pull request

**Files:**
- Modify: `README.md` (new subsection under "🏃 Usage", after the `re-tailor` example), `docs/cli.md` (new `resume` and `runs` sections after `re-tailor`), `ARCHITECTURE.md` (new "Durable execution" section before "## CLI"), `CLAUDE.md` (gotchas), `.github/copilot-instructions.md` (dependency line).

**Interfaces:** none (docs only).

- [ ] **Step 1: README — usage**

Insert after the `re-tailor` "### Example" block (before `### Live progress & speed`):

````markdown
### `resume` / `runs` — Continue an interrupted run

Every run is **durable**: each model request is checkpointed by [DBOS](https://docs.dbos.dev) in a local SQLite file (`memory/dbos.sqlite3`, override with `SIRA_DBOS_DATABASE_URL`). `sira tailor` prints a **Run ID** at start. If the process is killed, crashes, or a stage fails, continue from the last completed model request — earlier agents are replayed from their checkpoints, not called again:

```bash
uv run sira resume <RUN_ID>
```

A run that failed is continued as a **new** run id (printed as `Continued as run: …`); a run that was only interrupted keeps its id. If you answered "quit" at an interactive checkpoint, `resume` asks the question again. List recent runs and their status with:

```bash
uv run sira runs --limit 10
```

A run can only be resumed by the same Sira version that started it.
````

- [ ] **Step 2: `docs/cli.md` — command reference**

Insert after the `re-tailor` "### Example" block (before `## Options`):

````markdown
## `resume`

Continue a killed, crashed, or failed run from its last checkpoint. Output files and the memory record are written exactly as for an uninterrupted run.

```bash
uv run sira resume <RUN_ID> [-v]
```

### Arguments

| Argument | Description |
|---|---|
| `RUN_ID` | The run id printed by `tailor` / `re-tailor` (also shown by `sira runs`). |

Behaviour by run state:

| State | What happens |
|---|---|
| Completed | The stored result is reused; nothing is re-run. |
| Interrupted (killed, Ctrl+C) | Resumed under the same run id. |
| Failed (a stage raised) | Forked from the failed step into a new run id; earlier checkpoints are replayed. |
| Aborted at a checkpoint | Forked at that checkpoint; the question is asked again. |
| Started by another Sira version | Refused — start a new run. |

## `runs`

List recent runs with their status, job, start time, and duration.

```bash
uv run sira runs [--limit N]
```
````

Also add to the "## Exit codes" table: `130 | Interrupted with Ctrl+C (the resume hint is printed)`.

- [ ] **Step 3: `ARCHITECTURE.md` — durable execution section**

Insert before `## CLI`:

````markdown
## Durable Execution

Every run is one DBOS workflow (`sira.tailor`) whose input is a `TailorInputs` snapshot (resume text, job content, model tiers, quality-gate settings, and the CLI metadata needed for post-processing). Inside it:

```
sira.tailor (workflow, id = run id)
├─ sira.parse_resume  (child workflow)  ─ model-request steps (Parser)
├─ sira.analyze_job   (child workflow)  ─ model-request steps (Analyst)
├─ write → review → audit loop         ─ model-request steps (Writer, Reviewer, Auditor, Quality Gate)
├─ sira.human_checkpoint (step)        ─ the interactive answer, checkpointed
└─ report                              ─ model-request steps (Report)
```

- Every agent carries pydantic-ai's `DBOSDurability` capability: a model request that runs inside the workflow is a checkpointed step (with retries on transient errors). Outside a workflow (the job scraper, the memory cache parser) the capability is transparent.
- Parser and Analyst are child workflows because DBOS requires a deterministic step order inside one workflow; each child owns its own sequence, so they may run concurrently.
- DBOS only continues a run under the same executor id and application version. Sira uses a fresh executor id per process, so a new `sira tailor` never silently picks up an old run; continuation is explicit (`sira resume`) and pinned to the installed Sira version.
- Continuation (`sira/workflows/continuation.py`): interrupted runs are resumed in place; failed runs are forked from the failed step (or the start of a failed child workflow, or the last checkpoint when the user aborted), which creates a new run id with the earlier checkpoints copied.
- The system database is SQLite at `memory/dbos.sqlite3` (`SIRA_DBOS_DATABASE_URL` overrides it). Post-processing (output files, memory save) stays outside the workflow and is repeated by `resume`.
````

- [ ] **Step 4: `CLAUDE.md` — gotchas**

Append these bullets to the "Conventions & gotchas" list:

```markdown
- **Durable execution (DBOS)**: `sira.workflows.tailor_workflow` is the DBOS workflow; every agent has `DBOSDurability`, so a model request inside it is a checkpointed step. Rules: define `@DBOS.workflow`/`@DBOS.step` functions at module level (registered before `durable_runtime()` launches); never interleave two step sequences in one workflow (`asyncio.gather` over agent runs is forbidden — use child workflows); run-time models must be strings; anything non-deterministic that affects control flow goes in a step (the checkpoint prompt is one). Tests get one DBOS runtime for the session from `tests/conftest.py`.
- **Continuing runs**: `resume_workflow` only works for runs that never finished; failed runs are *forked* — see `sira/workflows/continuation.py` for which step to fork from. A resumed/forked run executes on DBOS's background thread, so the CLI installs a process-wide fallback reporter (`install_global_reporter`).
```

Also update the Commands block in `CLAUDE.md` with two lines after the `re-tailor` line:

```bash
uv run sira resume <RUN_ID>              # continue a killed/crashed/failed run from its last checkpoint
uv run sira runs [--limit N]             # list recent runs and their status
```

- [ ] **Step 5: `.github/copilot-instructions.md`**

Change the dependency line to:

```markdown
- `pydantic-ai[dbos,groq,mistral,cohere,bedrock]>=2.43,<3`: Agent framework + DBOS durable execution (xAI is an opt-in `xai` extra)
```

- [ ] **Step 6: Strict docs build, gate, graph refresh**

`--group docs` installs MkDocs; `mkdocs build --strict` fails on any warning (CI runs it):

```bash
uv sync --group docs && uv run mkdocs build --strict && rm -rf site
```

Expected: `Documentation built`.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest
```

Expected: ruff clean; `401 passed`; only the 5 pre-existing SWIG warnings.

```bash
graphify update .
```

- [ ] **Step 7: Commit the docs**

```bash
git add README.md docs/cli.md ARCHITECTURE.md CLAUDE.md .github/copilot-instructions.md
git commit -m "docs: describe durable runs, sira resume and sira runs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Push and open the pull request** — only after the controller's explicit go-ahead.

```bash
git -c credential.helper='!gh auth git-credential' -c url.https://github.com/.insteadOf=git@github.com: push -u origin claude/dbos-durable-execution
```

(SSH to GitHub hangs from this sandbox; the `-c` options push over HTTPS with the `gh` token for this one command and do not change the repo's remote.)

```bash
cat > /tmp/sira-pr2-body.md <<'BODY'
## Summary
Second of three PRs from the durable-execution + observability design (`docs/superpowers/specs/2026-09-12-durable-execution-and-observability-design.md`, section 5).

- Every `tailor` / `re-tailor` run is a DBOS workflow (`sira.tailor`) checkpointed in `memory/dbos.sqlite3`; each model request is a step (every agent carries `DBOSDurability`), Parser/Analyst are child workflows, the interactive checkpoint is a step.
- New `sira resume <run-id>` continues a killed, crashed, failed, or aborted run from its last checkpoint (interrupted → resumed in place; failed → forked from the failed step into a new run id; aborted → asked again). New `sira runs` lists recent runs.
- Hard stage failures raise `PipelineError` instead of `sys.exit`, so DBOS records them; Ctrl+C prints the resume hint.
- Token streaming moves into the durability capability's event-stream handler (one `run_agent` path in and out of workflows); a process-wide fallback reporter keeps the dashboard working when a continued run executes on DBOS's background thread.

## Test plan
- [x] `uv run ruff check .` / `uv run ruff format --check .` — clean
- [x] `uv run pytest` — 401 passed
- [x] `uv run mkdocs build --strict` — passes
- [x] Replay verified end-to-end: Writer crash → resume re-runs only the Writer; abort at checkpoint → resume asks again; model request inside a workflow recorded as a DBOS step
- [ ] CI green
- [ ] Manual: kill a real run mid-Writer, `sira resume`, confirm Parser/Analyst not called again (MLflow trace or `-v` output)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
gh pr create --base main --head claude/dbos-durable-execution --assignee EmadMokhtar --title "feat: durable execution with dbos" --body-file /tmp/sira-pr2-body.md
```

Expected: a PR URL. Report it with the final test count.
