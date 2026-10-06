# filename: tests/normalization/test_chunking.py
# title: Normalization Layer - Filing Chunker Tests
# layer: Test suite - normalization
# status: Phase 9 readiness

from __future__ import annotations

from veda.normalization.chunking import (
    DEFAULT_MAX_CHARS,
    chunk_text,
    html_to_text,
    locate_passage,
)
from veda.shared.ids import chunk_id


DOC_ID = "doc:sec_edgar:10k:000093646825000009"
FIXTURE_HTML = """
<html>
  <head>
    <style>body { color: red; }</style>
    <script>var leaked = "do-not-keep";</script>
  </head>
  <body>
    <p>Total revenues were $71,043 million for the year ended December 31, 2024.</p>
    <p>Lockheed Martin Corporation is the ultimate parent of its consolidated subsidiaries.</p>
    <table>
      <tr><td>Item</td><td>Amount</td></tr>
      <tr><td>Net sales</td><td>71043</td></tr>
    </table>
  </body>
</html>
"""


def test_html_to_text_drops_script_and_style_and_keeps_breaks() -> None:
    text = html_to_text(FIXTURE_HTML)
    assert "do-not-keep" not in text
    assert "color" not in text
    assert "Total revenues were $71,043 million" in text
    assert "ultimate parent" in text
    lines = text.split("\n")
    assert "Item Amount" in lines
    assert "Net sales 71043" in lines
    assert text == html_to_text(FIXTURE_HTML)


def test_chunk_ids_offsets_and_hashes_are_stable() -> None:
    text = html_to_text(FIXTURE_HTML)
    first = chunk_text(DOC_ID, text)
    second = chunk_text(DOC_ID, text)
    assert [chunk.model_dump() for chunk in first] == [
        chunk.model_dump() for chunk in second
    ]
    assert first[0].chunk_id == chunk_id(DOC_ID, 0)
    assert text[first[0].char_start:first[0].char_end] == first[0].text
    assert len(first[0].text_sha256) == 64


def test_long_text_chunks_do_not_overlap() -> None:
    paragraph = "revenues " * 400
    text = paragraph.strip() + "\n" + ("other " * 400).strip()
    chunks = chunk_text(DOC_ID, text, max_chars=1500)
    assert len(chunks) > 1
    assert DEFAULT_MAX_CHARS == 1500
    for earlier, later in zip(chunks, chunks[1:]):
        assert later.char_start >= earlier.char_end
        assert len(earlier.text) <= 1500
    rebuilt = []
    cursor = 0
    for chunk in chunks:
        if chunk.char_start > cursor:
            rebuilt.append(text[cursor:chunk.char_start])
        rebuilt.append(chunk.text)
        cursor = chunk.char_end
    rebuilt.append(text[cursor:])
    assert "".join(rebuilt) == text


def test_locate_passage_uses_regex_when_hint_string_is_absent() -> None:
    text = html_to_text(FIXTURE_HTML)
    import re

    pattern = re.compile(r"(?i)\bultimate\s+parent\b")
    hit = locate_passage(text, "subsidiary_relationship", pattern)
    assert hit is not None
    assert hit.method == "regex"
    assert text[hit.start:hit.end] == "ultimate parent"
