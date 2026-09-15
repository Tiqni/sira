"""Optional local prompt-injection classifier (the ``sira[guard]`` extra).

Second, model-based layer on top of the regex scan in
``job_scraper_helpers.detect_prompt_injection``. It runs a small text
classifier (Meta's Llama Prompt Guard 2, 22M parameters) on the scraped
Markdown, fully on the user's machine. Because it needs ``torch`` +
``transformers`` (~2 GB) and the default model is gated on Hugging Face, it is
opt-in three times over:

1. installed only with ``uv sync --extra guard``;
2. run only after the user consents once (a model is downloaded and executed
   locally) — see :func:`resolve_consent`;
3. never fatal: any load or runtime failure raises :class:`GuardUnavailable`,
   which the CLI turns into a warning and a regex-only run.

Why a model at all, given issue #1 ruled out an "LLM-based classifier"? That
non-goal targets a generative LLM asked to judge text — a judge that can itself
be talked into a verdict by the text it reads. Prompt Guard 2 is a
discriminative classifier: it outputs a label and cannot follow instructions,
so that objection does not apply. The cost/latency objection is handled by
keeping the layer opt-in.

No heavy import happens at module import time; ``transformers`` is imported
inside :func:`_load_pipeline` only.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from importlib.util import find_spec as _find_spec
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_GUARD_MODEL = "meta-llama/Llama-Prompt-Guard-2-22M"
GUARD_MODEL_ENV = "SIRA_GUARD_MODEL"
# "yes"/"1" or "no"/"0" pre-answers the consent question (CI, scripts).
GUARD_CONSENT_ENV = "SIRA_GUARD_CONSENT"
CLASSIFIER_INDICATOR = "classifier_flagged"

# Prompt Guard 2 emits BENIGN / MALICIOUS. The protectai DeBERTa models emit
# SAFE / INJECTION (or LABEL_0 / LABEL_1). Accept every "bad" spelling.
_MALICIOUS_LABELS = frozenset({"MALICIOUS", "INJECTION", "JAILBREAK", "LABEL_1"})
_MAX_TOKENS = 512  # the classifiers' context window
# ~4 chars per token in English keeps a chunk well under 512 tokens; the
# pipeline truncates anyway, and the overlap stops a phrase being cut in two.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
_THRESHOLD = 0.5

PipelineLoader = Callable[[str], Callable[..., list[dict]]]


class GuardUnavailable(RuntimeError):
    """The classifier could not be loaded or run; fall back to regex only."""


# ---------------------------------------------------------------------------
# Model selection / availability
# ---------------------------------------------------------------------------


def model_name() -> str:
    """Hugging Face model id, overridable with ``SIRA_GUARD_MODEL``."""
    return os.environ.get(GUARD_MODEL_ENV) or DEFAULT_GUARD_MODEL


def is_installed() -> bool:
    """True when the ``guard`` extra (torch + transformers) is importable."""
    return _find_spec("transformers") is not None and _find_spec("torch") is not None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _load_pipeline(model: str) -> Callable[..., list[dict]]:
    try:
        from transformers import pipeline
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise GuardUnavailable(
            "transformers is not installed; install the guard extra: "
            "uv sync --extra guard"
        ) from e
    try:
        return pipeline("text-classification", model=model)
    except Exception as e:
        raise GuardUnavailable(f"could not load classifier {model!r}: {e}") from e


def _flatten_results(results: object) -> list[dict]:
    """Normalise pipeline output to a flat list of ``{"label", "score"}`` dicts.

    ``pipeline(...)`` returns one dict per input by default, but a list of
    lists when ``top_k``/``return_all_scores`` is in play. Anything else is a
    contract violation and is reported as :class:`GuardUnavailable`.
    """
    flat: list[dict] = []
    for item in results if isinstance(results, list) else [results]:
        if isinstance(item, dict):
            flat.append(item)
        elif isinstance(item, list) and all(isinstance(r, dict) for r in item):
            flat.extend(item)
        else:
            raise GuardUnavailable(
                f"unexpected classifier output shape: {type(item).__name__}"
            )
    return flat


def _is_malicious(result: dict, threshold: float) -> bool:
    label = str(result.get("label", "")).upper()
    return label in _MALICIOUS_LABELS and float(result.get("score", 0.0)) >= threshold


def _chunk(text: str) -> list[str]:
    step = CHUNK_CHARS - CHUNK_OVERLAP
    return [text[i : i + CHUNK_CHARS] for i in range(0, len(text), step)]


def classify(
    markdown: str,
    *,
    threshold: float = _THRESHOLD,
    load_pipeline: PipelineLoader = _load_pipeline,
) -> list[str]:
    """Return ``[CLASSIFIER_INDICATOR]`` if any chunk is classified malicious.

    ``load_pipeline`` is injectable so tests never touch torch. Raises
    :class:`GuardUnavailable` on any load or inference failure.
    """
    text = markdown.strip()
    if not text:
        return []
    model = model_name()
    try:
        pipe = load_pipeline(model)
    except GuardUnavailable:
        raise
    except Exception as e:
        raise GuardUnavailable(f"could not load classifier {model!r}: {e}") from e
    chunks = _chunk(text)
    try:
        results = pipe(chunks, truncation=True, max_length=_MAX_TOKENS)
        flagged = any(_is_malicious(r, threshold) for r in _flatten_results(results))
    except GuardUnavailable:
        raise
    except Exception as e:
        raise GuardUnavailable(f"classifier {model!r} failed: {e}") from e
    logger.debug(
        "guard_classifier_result",
        extra={"model": model, "chunks": len(chunks), "flagged": flagged},
    )
    return [CLASSIFIER_INDICATOR] if flagged else []


# ---------------------------------------------------------------------------
# Consent (asked once, remembered)
# ---------------------------------------------------------------------------


def consent_path() -> Path:
    """``$XDG_CONFIG_HOME/sira/guard_consent.json`` (default ``~/.config``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sira" / "guard_consent.json"


def _load_stored_consent() -> bool | None:
    path = consent_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    value = data.get("consent") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else None


def _store_consent(value: bool) -> None:
    path = consent_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"consent": value, "model": model_name()}))
    except OSError as e:
        # Not being able to remember the answer is not worth failing a run.
        logger.debug("guard_consent_not_saved", extra={"error": str(e)})


def consent_message() -> str:
    return (
        f"The 'guard' extra is installed. Sira can run the {model_name()} "
        "prompt-injection classifier on scraped job postings. This downloads the "
        "model (~90 MB) from Hugging Face on first use and runs it locally on this "
        "machine; the default model requires accepting Meta's license and a "
        "Hugging Face token (HF_TOKEN). Enable it? Your answer is remembered in "
        f"{consent_path()}."
    )


def resolve_consent(ask: Callable[[str], bool] | None) -> bool | None:
    """Decide whether the classifier may run.

    Order: ``SIRA_GUARD_CONSENT`` env var → stored answer → ``ask(message)``
    (stored afterwards). Returns ``None`` when there is no answer and no way
    to ask (non-interactive run), which callers treat as "skip".
    """
    env = os.environ.get(GUARD_CONSENT_ENV, "").strip().lower()
    if env in {"yes", "y", "true", "1"}:
        return True
    if env in {"no", "n", "false", "0"}:
        return False

    stored = _load_stored_consent()
    if stored is not None:
        return stored

    if ask is None:
        return None
    answer = bool(ask(consent_message()))
    _store_consent(answer)
    return answer
