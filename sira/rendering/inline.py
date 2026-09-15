"""The inline-markdown subset both renderers understand.

Supported: ``[text](url)``, bare ``http(s)://`` URLs, ``**bold**``, ``*italic*``
and ```` `code` ````. Everything else is plain text. Only ``http``, ``https``
and ``mailto`` links get an href; other schemes render as plain text.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from markupsafe import Markup

_SAFE_SCHEMES = ("http://", "https://", "mailto:")

# Alternation order matters: bold (**) must be tried before italic (*).
_TOKEN = re.compile(
    r"\[(?P<ltext>[^\]]+)\]\((?P<lurl>(?:[^)(]|\([^)]*\))*)\)"
    r"|(?P<url>https?://[^\s<>()\[\]]+)"
    r"|\*\*(?P<bold>[^*]+)\*\*"
    r"|\*(?P<italic>[^*]+)\*"
    r"|`(?P<code>[^`]+)`"
)


@dataclass(frozen=True)
class Run:
    """One styled piece of text; ``href`` is empty for non-links."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str = ""


def _safe_href(url: str) -> str:
    return url if url.lower().startswith(_SAFE_SCHEMES) else ""


def inline_markdown_to_runs(text: str) -> list[Run]:
    """Split ``text`` into styled runs (used by the DOCX renderer)."""
    runs: list[Run] = []
    pos = 0
    for match in _TOKEN.finditer(text):
        if match.start() > pos:
            runs.append(Run(text[pos : match.start()]))
        if match.group("ltext") is not None:
            runs.append(Run(match.group("ltext"), href=_safe_href(match.group("lurl"))))
        elif match.group("url") is not None:
            url = match.group("url")
            runs.append(Run(url, href=_safe_href(url)))
        elif match.group("bold") is not None:
            runs.append(Run(match.group("bold"), bold=True))
        elif match.group("italic") is not None:
            runs.append(Run(match.group("italic"), italic=True))
        else:
            runs.append(Run(match.group("code"), code=True))
        pos = match.end()
    if pos < len(text):
        runs.append(Run(text[pos:]))
    return runs


def inline_markdown_to_html(text: str) -> Markup:
    """Convert ``text`` to escaped HTML (used by the Jinja template as ``| inline``)."""
    parts: list[str] = []
    for run in inline_markdown_to_runs(text):
        piece = html.escape(run.text, quote=True)
        if run.code:
            piece = f"<code>{piece}</code>"
        if run.bold:
            piece = f"<b>{piece}</b>"
        if run.italic:
            piece = f"<i>{piece}</i>"
        if run.href:
            piece = f'<a href="{html.escape(run.href, quote=True)}">{piece}</a>'
        parts.append(piece)
    return Markup("".join(parts))
