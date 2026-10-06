"""
File: src/veda/normalization/chunking.py
Title: Deterministic HTML-to-text and paragraph chunker
Layer: Normalization layer
Status: Phase 9 readiness

Purpose
-------
Turns SEC filing HTML into stable plain text and splits that text into
non-overlapping chunks. Offsets are absolute indexes into the normalized
text, so a span can be checked with ``text[start:end]``.

Public API
----------
html_to_text
chunk_text
locate_passage
looks_like_filing_index
sha256_text
DEFAULT_MAX_CHARS
VERSION

Does not
--------
Does not fetch documents, choose evidence categories, or call a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser

from veda.shared.ids import chunk_id
from veda.shared.models import EvidenceChunk


VERSION = "1"
DEFAULT_MAX_CHARS = 1500

_SKIP_TAGS = frozenset({"script", "style"})
_NEWLINE_TAGS = frozenset({
    "p",
    "div",
    "br",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "section",
    "article",
    "blockquote",
    "pre",
})
_CELL_TAGS = frozenset({"td", "th"})


class _FilingTextParser(HTMLParser):
    """Drop script and style; keep paragraph and table-row breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if name in _NEWLINE_TAGS:
            self._parts.append("\n")
        elif name in _CELL_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if name in _NEWLINE_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def sha256_text(text: str) -> str:
    """Return the lowercase SHA-256 hex digest of UTF-8 text."""
    return sha256(text.encode("utf-8")).hexdigest()


def _collapse_whitespace(raw: str) -> str:
    """
    Collapse horizontal whitespace and keep one newline between blocks.

    Blank lines produced by paired block tags are dropped. The result
    has no leading or trailing whitespace.
    """
    lines: list[str] = []
    for line in raw.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def html_to_text(html: str) -> str:
    """Convert HTML to deterministic plain text. Non-HTML is unchanged."""
    if not isinstance(html, str):
        raise ValueError("html_to_text requires a string")
    parser = _FilingTextParser()
    parser.feed(html)
    parser.close()
    return _collapse_whitespace(parser.text())


def looks_like_html(text: str) -> bool:
    """True when the body should be passed through the HTML parser."""
    return "<" in text and ">" in text


def looks_like_filing_index(text: str, url: str | None = None) -> bool:
    """
    True for an EDGAR filing index page.

    An index URL (``*-index.htm``) is never filing content. A body that
    carries the EDGAR index headings is rejected even if the URL is
    something else.
    """
    if url:
        path = url.split("?", 1)[0].split("#", 1)[0].lower()
        if path.endswith("-index.htm") or path.endswith("-index.html"):
            return True
    sample = text.lower()
    if "edgar filing documents" in sample:
        return True
    if "filing detail" in sample and "document format files" in sample:
        return True
    return False


def normalize_filing_body(text: str, url: str | None = None) -> str:
    """
    Return normalized filing text.

    HTML is converted. Plain text only has its whitespace collapsed so
    fixture passages and live HTML share one offset space.
    """
    if looks_like_html(text):
        return html_to_text(text)
    return _collapse_whitespace(text)


@dataclass(frozen=True)
class PassageHit:
    """One located hint inside normalized text."""

    start: int
    end: int
    matched: str
    method: str


def locate_passage(
    text: str,
    hint: str,
    pattern: re.Pattern[str] | None = None,
) -> PassageHit | None:
    """
    Find a hint in normalized text.

    Order is fixed: exact substring, then case-insensitive substring,
    then the documented regular expression for that hint.
    """
    if not hint:
        return None
    exact = text.find(hint)
    if exact >= 0:
        end = exact + len(hint)
        return PassageHit(exact, end, text[exact:end], "exact")
    folded = text.lower()
    insensitive = folded.find(hint.lower())
    if insensitive >= 0:
        end = insensitive + len(hint)
        return PassageHit(insensitive, end, text[insensitive:end], "case_insensitive")
    if pattern is not None:
        match = pattern.search(text)
        if match is not None:
            return PassageHit(match.start(), match.end(), match.group(0), "regex")
    return None


def _chunk_windows(text: str, max_chars: int) -> list[tuple[int, int]]:
    """
    Paragraph-aware windows with no overlap.

    A newline that separates two chunks is not included in either
    window. A paragraph longer than ``max_chars`` is split on a hard
    character boundary.
    """
    windows: list[tuple[int, int]] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        limit = min(cursor + max_chars, length)
        if limit < length:
            newline = text.rfind("\n", cursor, limit)
            if newline > cursor:
                end = newline
                next_cursor = newline + 1
            else:
                end = limit
                next_cursor = limit
        else:
            end = length
            next_cursor = length
        if end > cursor:
            windows.append((cursor, end))
        if next_cursor <= cursor:
            next_cursor = cursor + 1
        cursor = next_cursor
    return windows


def chunk_text(
    doc_id: str,
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[EvidenceChunk]:
    """
    Split normalized text into EvidenceChunk records.

    ``chunk_id`` comes from ``veda.shared.ids.chunk_id``. ``char_start``
    and ``char_end`` index ``text``. ``text_sha256`` is the SHA-256 of
    the chunk text only.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if not text:
        return []
    chunks: list[EvidenceChunk] = []
    for index, (start, end) in enumerate(_chunk_windows(text, max_chars)):
        piece = text[start:end]
        chunks.append(
            EvidenceChunk(
                chunk_id=chunk_id(doc_id, index),
                doc_id=doc_id,
                index=index,
                char_start=start,
                char_end=end,
                text=piece,
                text_sha256=sha256_text(piece),
            )
        )
    return chunks


def chunk_for_span(
    chunks: list[EvidenceChunk],
    start: int,
    end: int,
) -> EvidenceChunk | None:
    """Return the chunk that fully contains ``[start, end)``."""
    for chunk in chunks:
        if chunk.char_start <= start and end <= chunk.char_end:
            return chunk
    return None


__all__ = [
    "DEFAULT_MAX_CHARS",
    "VERSION",
    "PassageHit",
    "chunk_for_span",
    "chunk_text",
    "html_to_text",
    "locate_passage",
    "looks_like_filing_index",
    "looks_like_html",
    "normalize_filing_body",
    "sha256_text",
]
