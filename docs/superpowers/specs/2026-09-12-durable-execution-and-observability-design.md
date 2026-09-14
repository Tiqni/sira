# Durable execution (DBOS) and observability (OpenTelemetry) — design

Date: 2026-09-12
Status: approved design, not yet implemented
Delivery: three pull requests, in order (see "Delivery plan")

## 1. Goal

1. Make a `sira tailor` / `sira re-tailor` run **durable**: if the process is
   killed or crashes, or a model call fails, the run can be resumed from the
   last completed model request without repeating earlier LLM calls.
2. Make a run **observable**: one trace per run, showing every pipeline stage,
   agent run, and model request (with model name, tokens, and content), sent to
   any OpenTelemetry-compatible backend. MLflow is the documented default.

## 2. Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Durability granularity | Per model request (each LLM call is a checkpoint) | User choice. Uses pydantic-ai's DBOS integration. |
| pydantic-ai version | Upgrade 1.24 → 2.x first, in its own PR | `DBOSAgent` (1.x) is deprecated and removed in v3; `DBOSDurability` (2.x) is the supported path and allows run-time model strings and live streaming. |
| Workflow boundary | One DBOS workflow per run (`sira.tailor`), Parser and Analyst as child workflows | Linear CLI pipeline; child workflows are the only sanctioned way to keep the Parser ∥ Analyst concurrency under DBOS's determinism rule. |
| Recovery UX | Explicit `sira resume <run-id>`; unique `executor_id` per process | A new `sira tailor` must never silently recover an older crashed run. |
| Durable state store | SQLite file `memory/dbos.sqlite3` (override: `SIRA_DBOS_DATABASE_URL`) | Next to the existing memory DB; no Postgres for a CLI. |
| Observability transport | Vendor-neutral OTLP (OpenTelemetry protocol), enabled by the standard `OTEL_EXPORTER_OTLP_*` env vars | No vendor SDK in Sira; MLflow, Opik, and Logfire all accept OTLP. |
| Documented default backend | MLflow (local `mlflow server`) | Lightest to run locally; native OTLP ingestion at `/v1/traces`. |

## 3. Facts verified against library source

Verified on `dbos` 2.31.1 and `pydantic-ai` 2.43.0 (installed in a scratch
virtual environment during design):

- `DBOSConfig` has `executor_id`, `application_version`, `run_admin_server`,
  `enable_otlp`, `otel_attribute_format`, `otlp_traces_endpoints`.
- `DBOS.launch()` auto-recovers `PENDING` workflows whose `executor_id` and
  `application_version` match the current process. A unique `executor_id` per
  process therefore disables auto-recovery; `DBOS.resume_workflow_async(id)`
  is the explicit path.
- A directly awaited async `@DBOS.workflow` runs in the caller's asyncio task
  (`asyncio.create_task`, contextvars copied). A **resumed** workflow runs on
  DBOS's background event-loop thread (no contextvars from the CLI task).
- `DBOS.start_workflow_async` runs the child in the same event loop as the
  caller.
- Re-invoking an existing workflow ID (`SetWorkflowID`) does **not** replay in
  the foreground: `init_workflow` sets `should_execute=False` and the caller
  only waits for the existing run.
- DBOS forbids interleaving two *sequences* of steps inside one workflow
  (docs: "Disallowed: interleaving steps in concurrent sequences"); the
  sanctioned pattern is child workflows.
- `DBOSDurability(models=…, event_stream_handler=…, model_step_config=…)`:
  model **name strings** passed at run time cross the step boundary and are
  built inside the step; `Model` instances must be registered in `models=`.
  The agent's primary model is registered as `'default'` automatically.
  Outside a DBOS workflow the capability is transparent.
- Streaming inside a workflow: `run_stream_events()` works but is buffered per
  step; live delivery requires `DBOSDurability(event_stream_handler=…)`.
- `Agent.instrument_all(InstrumentationSettings(...))` exists in 2.x;
  `InstrumentationSettings(include_content=…, include_binary_content=…)`.
- DBOS's tracer uses the **global** OTel tracer provider when it is not given
  its own `otlp_traces_endpoints`, and it sets the workflow span into the
  active OTel context (`_UseOtelContext`). Spans from pydantic-ai and Sira
  therefore nest under DBOS spans when Sira sets the global provider.
- MLflow server accepts OTLP at `<tracking-uri>/v1/traces` with header
  `x-mlflow-experiment-id=<id>`. Opik accepts OTLP at
  `<host>/api/v1/private/otel`. Logfire accepts OTLP at
  `https://logfire-api.pydantic.dev` with `Authorization=<token>`.

## 4. PR 1 — `build!: upgrade pydantic-ai to 2.x`

Goal: mechanical upgrade, no behavior change, all tests green.

Known v1 → v2 changes that touch Sira:

| v1 (current) | v2 | Where |
|---|---|---|
| `async for ev in agent.run_stream_events(...)` | `async with agent.run_stream_events(...) as events: async for ev in events` | `run_agent` in `sira/workflows/agents.py` |
| `Agent(instrument=...)`, `Agent(event_stream_handler=...)` | `capabilities=[Instrumentation(...)]`, `capabilities=[ProcessEventStream(...)]` | not used today; PR 2/3 use the v2 forms |
| `Agent[None, X]` generic default | `Agent[object, X]` | type hints, if any |
| `ModelProfile` dataclass | `TypedDict` (`.get()` instead of attribute access) | check `sira/memory/parser.py` and any custom model construction |
| Model names need a provider prefix | already `openai:…` / `ollama:…` | none expected |

Constraints that must survive the upgrade:

- Agent construction stays key-free (`_build_default_model()`); do not pass a
  model string to `Agent(...)` at import.
- `@output_validator` quality gate, per-agent `retries=`, `agent.override(model=...)`
  in tests, `models.ALLOW_MODEL_REQUESTS = False` in `tests/conftest.py`.

Steps:

1. `pyproject.toml`: `pydantic-ai>=2.43,<3`; `uv lock`.
2. Run the gate; fix each failure.
3. Grep test output for `DeprecationWarning` from pydantic-ai and fix those too
   (v2 warnings become v3 removals).
4. Update `AGENTS.md` and `.github/copilot-instructions.md` where they describe
   a v1-only API.

Testing: the existing suite (355 tests) is the contract. No new tests unless a
fix needs one.

## 5. PR 2 — `feat: durable execution with DBOS`

### 5.1 Components

**`sira/durability.py` (new)**

- `configure_dbos(db_url: str | None = None, *, tracing: bool = False) -> None`
  builds `DBOSConfig`:
  - `name="sira"`
  - `application_version=<sira package version>` via
    `importlib.metadata.version("sira")` (fallback `"dev"`). Pinned so a resume
    after re-installing the same version still matches.
  - `system_database_url = db_url or os.environ.get("SIRA_DBOS_DATABASE_URL") or "sqlite:///memory/dbos.sqlite3"`
  - `executor_id=str(uuid.uuid4())` — unique per process.
  - `run_admin_server=False`, `log_level="WARNING"`.
  - When `tracing` is true (PR 3): `enable_otlp=True`,
    `otel_attribute_format="semconv"`, **no** `otlp_traces_endpoints`.
- `launch_dbos()` → `DBOS.launch()`; `shutdown_dbos()` → `DBOS.destroy()`.
- Must be called after `sira.workflows` and `sira.workflows.agents` are
  imported (workflows and agents register at import) and before any workflow
  runs.

**`sira/models/workflow.py`** — new `TailorInputs` (Pydantic model). It is the
single workflow input and holds everything the run *and* the post-processing
need, so `sira resume <id>` needs no other arguments:

```
resume_text, job_content, pre_parsed_cv: CV | None,
model: str | None, fast: bool, write_attempts: int, review_iterations: int,
quality_gate: bool, gate_threshold: int, interactive: bool, debug: bool, verbose: bool,
job_url: str | None, resume_source_path: str, output_dir: str,
output_pattern: str, resume_name_pattern: str, recommendations: str
```

**`sira/workflows/__init__.py`**

- `@DBOS.workflow(name="sira.tailor") async def tailor_workflow(inputs: TailorInputs) -> ResumeTailorResult`
  — module-level. Body: `apply_model_override(inputs.model)`, the `--fast`
  preset when `inputs.fast`, `set_quality_gate(...)`, build
  `ResumeTailorWorkflow(...)`, run `_run_impl`. Applying settings inside the
  workflow makes first run and resume identical.
- `ResumeTailorWorkflow.run()` keeps its signature and delegates to
  `tailor_workflow` (tests keep calling `run()`; the reporter is installed via
  `use_reporter` around the call and is **not** a workflow input).
- Parser and Analyst become child workflows
  `@DBOS.workflow(name="sira.parse_resume")` and `"sira.analyze_job"`
  (module-level functions taking primitives: text, `max_retries`). Started
  with `DBOS.start_workflow_async` in a fixed order, then both handles
  awaited. `_QualityState` fallbacks (`_parser_qs`, `_analyst_qs`) stay as
  they are (module-level state read inside the same process).
- `_human_checkpoint` body moves into
  `@DBOS.step(name="sira.human_checkpoint") async def _checkpoint_step(header, details, choices, default, interactive) -> tuple[str, str]`.
  The instance method delegates to it. The answer is checkpointed; a resume
  never re-asks.
- `sys.exit(...)` inside `_run_impl` → `raise PipelineError(msg)` (new
  exception in `sira/workflows/__init__.py`). DBOS then records the run as
  `ERROR` instead of leaving it `PENDING`. The one test expecting
  `SystemExit` changes to `PipelineError`.

**`sira/workflows/agents.py`**

- Every `Agent(...)` gets `name="sira.<agent>"` (unique; DBOS step names derive
  from it) and
  `capabilities=[DBOSDurability(event_stream_handler=_stream_to_reporter, model_step_config=_MODEL_STEP_CONFIG)]`
  with `_MODEL_STEP_CONFIG = {"retries_allowed": True, "max_attempts": 3, "interval_seconds": 2, "backoff_rate": 2}`.
- `_stream_to_reporter(ctx, events)` forwards `TextPartDelta` /
  `ThinkingPartDelta` to `get_active_reporter().token(label, text, kind)` only
  when `reporter.wants_tokens`. The label comes from a contextvar set by
  `run_agent` (`_current_agent_label`).
- `run_agent` drops the `run_stream_events` branch and always calls
  `agent.run(...)`. One code path inside and outside workflows.
- Quality-gate nested runs (`_score_output` → `run_agent(quality_gate_agent)`)
  stay as they are: sequential, so deterministic.

**`sira/reporting/base.py`**

- `install_global_reporter(reporter)` / `clear_global_reporter()`;
  `get_active_reporter()` returns the contextvar reporter, else the global
  one, else `NullReporter()`. Needed because a resumed workflow runs on DBOS's
  background thread. `LiveDashboard.update()` is safe from another thread
  (Rich `Live` locks internally).

**`sira/main.py`**

- `tailor` / `re-tailor`: `configure_dbos()` + `launch_dbos()` before the run,
  `install_global_reporter(reporter)`, generate `run_id = uuid4()`, print
  `🧾 Run ID: <run_id>`, then `with SetWorkflowID(run_id): await workflow.run(...)`
  (the workflow runs in the CLI task; contextvars and the dashboard work as
  today), `shutdown_dbos()` in `finally`.
  `KeyboardInterrupt` → print `Interrupted — resume with: sira resume <id>`,
  exit code 130. `PipelineError` → message, exit code 1.
- New `sira resume <run-id>`:
  1. `DBOS.retrieve_workflow(id)` → status. Unknown id → error, exit 1.
  2. Load `TailorInputs` from `WorkflowStatus.input`. If
     `WorkflowStatus.app_version` differs from the installed Sira version,
     refuse with a message (DBOS only dequeues a resumed workflow whose
     version matches, so it would otherwise hang).
  3. `SUCCESS` → use `WorkflowStatus.output`, skip to step 5.
  4. `ERROR` whose stored exception is `UserAbortedError` → print "this run
     was aborted by you; start a new `sira tailor`", exit 1. Any other
     non-running status → `DBOS.resume_workflow_async(id)`, await the handle.
  5. Post-processing: write CV and report files, save to memory — the same
     code `tailor` uses after the workflow returns.
- New `sira runs [--limit N]`: `DBOS.list_workflows(name="sira.tailor", sort_desc=True, limit=N)`
  → Rich table: run id, status, company / job title (from inputs), started
  at, duration.
- Post-processing (files, memory save) stays outside the workflow: it is file
  I/O, idempotent, and re-runnable from `resume`.

**`pyproject.toml`**: `pydantic-ai[dbos]>=2.43,<3`.

### 5.2 Data flow

```
sira tailor
  ├─ convert resume, resolve cache, scrape job (unchanged, outside DBOS)
  ├─ configure_dbos(); launch_dbos(); install reporter
  ├─ run_id = uuid4(); print Run ID
  ├─ with SetWorkflowID(run_id): result = await workflow.run(TailorInputs(...))
  │    tailor_workflow
  │      ├─ start child: sira.parse_resume   ─┐ each run_agent → model-request steps
  │      ├─ start child: sira.analyze_job    ─┘ (skipped on cold cache hit, as today)
  │      ├─ await both
  │      ├─ write → review → audit loop      (model-request steps)
  │      ├─ sira.human_checkpoint step       (only when interactive and needed)
  │      └─ report                           (model-request steps)
  │    → ResumeTailorResult (pickled as the workflow output)
  ├─ post-processing: write files, save memory
  └─ finally: shutdown_dbos()

sira resume <id>
  ├─ configure_dbos(); launch_dbos(); install reporter (global)
  ├─ status = retrieve_workflow(id); inputs = status.input
  ├─ SUCCESS → output; else resume_workflow_async(id) → await
  └─ post-processing (same as above); shutdown
```

### 5.3 Error handling

| Failure | Behavior |
|---|---|
| Transient HTTP / model error inside a model request | DBOS step retry: 3 attempts, 2 s, ×2 backoff. |
| pydantic-ai `ModelRetry`, quality-gate exhaustion (`UnexpectedModelBehavior`) | Unchanged agent-level behavior; not checkpointed, so re-run on resume. |
| `PipelineError`, `UserAbortedError` | Workflow `ERROR`, original exception stored. |
| Process killed / crash / Ctrl+C | Workflow `PENDING`; `sira resume <id>` continues from the last checkpoint. |
| `sira resume` on an aborted run | Refused with a message (the checkpointed answer would replay as "quit"). |
| Resume with a different installed Sira version | The CLI compares `app_version` before resuming and refuses with a message. |

### 5.4 Testing

- `tests/conftest.py`: session-scoped autouse fixture: `configure_dbos(db_url=f"sqlite:///{tmp}/dbos.sqlite3")`,
  `launch_dbos()`, yield, `shutdown_dbos()`. Imports `sira.workflows` first so
  workflows and agents are registered.
- The 24 existing workflow tests run through the real workflow, child
  workflows, and checkpoint step with `run_agent` still monkeypatched.
- New tests:
  1. Crash-then-resume: fake Writer raises once → workflow `ERROR`;
     `resume_workflow_async` completes; Parser/Analyst call counters prove
     they were not re-run.
  2. Checkpoint step: `input` monkeypatched to raise on a second call; resume
     must not call it.
  3. Throwaway `Agent(TestModel(...), name="t", capabilities=[DBOSDurability()])`
     run inside a workflow → `DBOS.list_workflow_steps(id)` contains a
     `*__model.request` step (proves message serialization through SQLite).
  4. Reporter global fallback.
  5. CLI `runs`; `resume` on `SUCCESS` (no workflow re-run) and on an aborted run.
  6. Streaming: token events reach the reporter inside a workflow via the
     `event_stream_handler`.

### 5.5 Docs

README (resume/runs commands, `SIRA_DBOS_DATABASE_URL`), ARCHITECTURE.md
(durability section with the data flow above), CLAUDE.md gotchas: define
workflows and agents before `launch_dbos()`; never interleave step sequences
in one workflow (use child workflows); run-time models must be strings.

## 6. PR 3 — `feat: OpenTelemetry tracing`

### 6.1 Trace shape (one run)

```
sira.tailor (DBOS workflow, root)
├─ sira.parse_resume (child workflow) ─ sira.agent.Parser ─ pydantic-ai "agent run" ─ "chat …" (model request step)
├─ sira.analyze_job (child workflow) ─ sira.agent.Analyst ─ …
├─ sira.stage.WRITING_CV ─ sira.agent.Writer ─ agent run ─ chat … ─ (quality-gate nested run)
├─ sira.stage.REVIEWING_CV ─ …
├─ sira.human_checkpoint (DBOS step)
└─ sira.stage.GENERATING_REPORT ─ …
```

Model-request spans carry pydantic-ai's GenAI semantic-convention attributes
(`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
message content). MLflow renders these as LLM calls.

### 6.2 Components

**`sira/telemetry.py` (new)**

- `configure_telemetry(*, exporter=None) -> bool`. Enabled only when
  `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set
  and `OTEL_SDK_DISABLED` is not `true`; otherwise returns `False` and touches
  nothing. When enabled:
  - `TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "sira"), "service.version": <package version>}))`
  - `BatchSpanProcessor(exporter or OTLPSpanExporter())` — HTTP/protobuf; the
    exporter reads endpoint and headers from the standard env vars.
  - `trace.set_tracer_provider(provider)`.
  - `Agent.instrument_all(InstrumentationSettings(include_content=os.environ.get("SIRA_TRACE_CONTENT", "1") != "0", include_binary_content=False))`.
- `shutdown_telemetry()` → `provider.force_flush()` then `provider.shutdown()`.
  Required in a short-lived CLI, or the batch processor drops the last spans.
- `span(name, **attributes)` context manager over the global tracer. When
  tracing is off the global provider is OTel's proxy, so spans are no-ops and
  call sites never branch. Records exceptions and sets status `ERROR` on
  failure.
- `telemetry_enabled() -> bool`; `configure_dbos(tracing=telemetry_enabled())`.

**Call sites**

- `ResumeTailorWorkflow._set_stage` opens `sira.stage.<NAME>`; the span object
  is held on the instance and ended by `_complete_stage` or by the next
  `_set_stage` (stages are not lexically nested, so no `with`).
- `run_agent` wraps the run in `sira.agent.<label>` with attributes
  `sira.agent.label`, `sira.model` (resolved tier string or `"default"`),
  and `sira.elapsed_s` on exit.
- `_score_output` adds span events `quality_score {label, score, threshold}`
  and `agent_retry {label, reason}` on the current span.

**`sira/main.py`**: `configure_telemetry()` is the first call in `tailor`,
`re-tailor`, `resume` (before `configure_dbos()`, so DBOS sees the provider);
print `📡 Tracing → <endpoint>` when enabled; `shutdown_telemetry()` in the
same `finally` as `shutdown_dbos()`.

**`pyproject.toml`**: add `opentelemetry-sdk` and
`opentelemetry-exporter-otlp-proto-http` as explicit dependencies. No
`mlflow`, `opik`, or `logfire` dependency.

### 6.3 Privacy

Prompts and completions contain the user's resume. Content capture is on when
the user has opted in by configuring a backend; `SIRA_TRACE_CONTENT=0` turns
it off (spans keep timing, tokens, and model name only). Documented next to
the setup steps.

### 6.4 Docs

README "Observability" section, MLflow first:

```bash
uvx mlflow server --host 127.0.0.1 --port 5000      # local UI at http://127.0.0.1:5000
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:5000/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_HEADERS=x-mlflow-experiment-id=0
uv run sira tailor <url> <resume>
```

Alternatives, each one block:

- Opik: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:5173/api/v1/private/otel`,
  `OTEL_EXPORTER_OTLP_HEADERS=projectName=sira` (cloud: `https://www.comet.com/opik/api/v1/private/otel`
  plus `Authorization=<key>,Comet-Workspace=<ws>`).
- Logfire: `OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-api.pydantic.dev`,
  `OTEL_EXPORTER_OTLP_HEADERS=Authorization=<token>`.

ARCHITECTURE.md gets the trace tree from 6.1.

### 6.5 Testing

1. Env unset → `configure_telemetry()` is `False`; global provider still the proxy.
2. `InMemorySpanExporter` + stubbed workflow → `sira.tailor` root,
   `sira.stage.*` and `sira.agent.*` spans with the expected parent span IDs,
   and the `quality_score` event.
3. Throwaway `TestModel` agent inside a workflow → a pydantic-ai model-request
   span with `gen_ai.*` attributes nested under `sira.agent.<label>`.
4. `SIRA_TRACE_CONTENT=0` → no message-content attributes or events.
5. `shutdown_telemetry()` flushes: spans still batched reach the exporter.

## 7. Delivery plan

| PR | Title | Depends on |
|---|---|---|
| 1 | `build!: upgrade pydantic-ai to 2.x` | — |
| 2 | `feat: durable execution with DBOS` | PR 1 merged |
| 3 | `feat: OpenTelemetry tracing` | PR 2 merged |

Each PR: `uv run ruff format . && uv run ruff check --fix . && uv run ruff format --check . && uv run ruff check . && uv run pytest`
must pass before push; `graphify update .` after code changes; PR assigned to
`@EmadMokhtar`.

## 8. Out of scope

- Postgres as the DBOS system database (SQLite only; env override exists).
- DBOS queues, scheduled workflows, Conductor.
- OTLP logs and metrics (traces only).
- `--run-id` / idempotency keys chosen by the user.
- Forking an aborted run past its checkpointed "quit" answer.

## 9. Glossary

- **DBOS** — a Python library for durable execution: it checkpoints workflow
  inputs, outputs, and each step's result in a database so a crashed run can
  resume from the last finished step.
- **Workflow / step (DBOS)** — a workflow is the orchestrating function; a step
  is a unit of work whose result is checkpointed once and never re-run.
- **Child workflow** — a workflow started from inside another; it has its own
  step sequence, which is what makes concurrency safe under DBOS.
- **Determinism rule** — on recovery the workflow function re-runs and must
  call the same steps in the same order; checkpointed steps return their
  stored result instead of executing.
- **`executor_id`** — the identity of the process running DBOS; recovery only
  picks up workflows that belong to the same executor id.
- **`application_version`** — a version tag stored with each workflow; DBOS
  only recovers workflows with a matching tag.
- **OpenTelemetry (OTel)** — the open standard for emitting traces, metrics,
  and logs. **OTLP** is its wire protocol.
- **Span** — one timed, nested unit of work in a trace (for example one model
  request), with attributes and events.
- **Tracer provider** — the OTel object that creates spans and owns the
  exporters; "global" means the process-wide default.
- **GenAI semantic conventions** — the standard attribute names for LLM calls
  (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, …).
- **Contextvar** — Python's per-task variable; copied into new asyncio tasks
  but not into other threads.
