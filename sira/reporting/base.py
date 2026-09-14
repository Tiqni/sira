"""ProgressReporter seam: decouples workflow logic from how progress is shown."""

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class ProgressReporter(Protocol):
    """Sink for pipeline lifecycle events. All methods are best-effort."""

    #: When True, run_agent streams token-level deltas to this reporter.
    wants_tokens: bool

    def stage_start(self, stage: str) -> None: ...
    def stage_done(self, stage: str, *, success: bool = True) -> None: ...
    def agent_start(self, label: str, prompt: str) -> None: ...
    def agent_retry(self, label: str, reason: str) -> None: ...
    def quality_score(self, label: str, score: int) -> None: ...
    def token(self, label: str, text: str, kind: str) -> None: ...
    def agent_done(self, label: str, elapsed: float) -> None: ...
    def note(self, msg: str) -> None: ...
    def log(self, msg: str) -> None: ...


class NullReporter:
    """Does nothing. The default when no reporter is installed."""

    wants_tokens = False

    def stage_start(self, stage: str) -> None: ...
    def stage_done(self, stage: str, *, success: bool = True) -> None: ...
    def agent_start(self, label: str, prompt: str) -> None: ...
    def agent_retry(self, label: str, reason: str) -> None: ...
    def quality_score(self, label: str, score: int) -> None: ...
    def token(self, label: str, text: str, kind: str) -> None: ...
    def agent_done(self, label: str, elapsed: float) -> None: ...
    def note(self, msg: str) -> None: ...

    def log(self, msg: str) -> None:
        print(msg)


_active_reporter: contextvars.ContextVar[ProgressReporter | None] = (
    contextvars.ContextVar("active_reporter", default=None)
)

# Process-wide fallback. A workflow continued by `sira resume` runs on DBOS's
# background thread, where the contextvar above is not set; the CLI installs
# its reporter here so progress output keeps working on that path.
_global_reporter: ProgressReporter | None = None


def install_global_reporter(reporter: ProgressReporter | None) -> None:
    """Set (or clear, with None) the fallback reporter for every thread."""
    global _global_reporter
    _global_reporter = reporter


def get_active_reporter() -> ProgressReporter:
    """Return the reporter for the current context, else the global one, else a NullReporter."""
    return _active_reporter.get() or _global_reporter or NullReporter()


@contextlib.contextmanager
def use_reporter(reporter: ProgressReporter) -> Iterator[ProgressReporter]:
    """Install `reporter` as active for the duration of the `with` block."""
    token = _active_reporter.set(reporter)
    try:
        yield reporter
    finally:
        _active_reporter.reset(token)
