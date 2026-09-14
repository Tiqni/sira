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
    try:
        DBOS(config=build_config(db_url, tracing=tracing))
        DBOS.launch()
    except BaseException:
        # A failed launch must not leave DBOS's process-wide singleton half
        # initialised: a later call would silently reuse it instead of
        # rebuilding it.
        DBOS.destroy()
        raise
    _active = True
    try:
        yield
    finally:
        _active = False
        DBOS.destroy()
