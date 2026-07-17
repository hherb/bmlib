# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests for bmlib.llm.text_utils — chunking and long-text processing."""

from __future__ import annotations

import pytest

from bmlib.llm.text_utils import (
    TextChunk,
    TextChunker,
    chunk_text,
    combine_title_and_text,
    get_text_with_priority,
    process_with_map_reduce,
    process_with_rolling_summary,
)


class TestTextChunk:
    def test_size_property(self):
        chunk = TextChunk(content="hello", start_pos=0, end_pos=5, chunk_index=0, total_chunks=1)
        assert chunk.size == 5


class TestChunker:
    def test_empty_text_returns_no_chunks(self):
        assert chunk_text("") == []

    def test_short_text_single_chunk(self):
        text = "short document"
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 1
        assert chunks[0].content == text
        assert chunks[0].start_pos == 0
        assert chunks[0].end_pos == len(text)
        assert chunks[0].total_chunks == 1

    def test_long_text_produces_multiple_chunks(self):
        text = "A" * 2500
        chunks = chunk_text(text, chunk_size=1000, overlap=100, boundary_aware=False)
        assert len(chunks) > 1
        assert all(c.total_chunks == len(chunks) for c in chunks)

    def test_content_matches_positions(self):
        text = "word " * 1000
        chunks = chunk_text(text, chunk_size=800, overlap=50)
        for c in chunks:
            assert c.content == text[c.start_pos : c.end_pos]

    def test_no_text_dropped_intervals_are_contiguous(self):
        text = "word " * 1000
        chunks = chunk_text(text, chunk_size=800, overlap=50)
        assert chunks[0].start_pos == 0
        assert chunks[-1].end_pos == len(text)
        # Consecutive chunks must not leave a gap (they may overlap).
        for prev, nxt in zip(chunks, chunks[1:]):
            assert nxt.start_pos <= prev.end_pos
            assert nxt.start_pos > prev.start_pos  # always makes progress

    def test_overlap_between_consecutive_chunks(self):
        text = "A" * 2500
        chunks = chunk_text(text, chunk_size=1000, overlap=100, boundary_aware=False)
        for prev, nxt in zip(chunks, chunks[1:]):
            assert nxt.start_pos < prev.end_pos  # genuine overlap

    def test_boundary_aware_splits_on_paragraph(self):
        # A paragraph break well past min_chunk_size should end the chunk there,
        # not mid-content.
        para_a = "A" * 600 + "\n\n"
        para_b = "B" * 600
        text = para_a + para_b
        chunks = chunk_text(
            text, chunk_size=900, overlap=0, boundary_aware=True, min_chunk_size=100
        )
        # First chunk ends at the paragraph boundary (right after the blank line).
        assert chunks[0].content.endswith("\n\n")
        assert chunks[0].end_pos == len(para_a)

    def test_boundary_unaware_uses_fixed_window(self):
        text = "A" * 600 + "\n\n" + "B" * 600
        chunks = chunk_text(text, chunk_size=900, overlap=0, boundary_aware=False)
        assert chunks[0].size == 900

    def test_invalid_chunk_size_raises(self):
        with pytest.raises(ValueError):
            TextChunker(chunk_size=0)

    def test_negative_overlap_raises(self):
        with pytest.raises(ValueError):
            TextChunker(chunk_size=100, overlap=-1)

    def test_overlap_ge_chunk_size_raises(self):
        with pytest.raises(ValueError):
            TextChunker(chunk_size=100, overlap=100)

    def test_get_chunk_info(self):
        chunker = TextChunker(chunk_size=1000, overlap=100)
        info = chunker.get_chunk_info("x" * 2500)
        assert info["text_length"] == 2500
        assert info["num_chunks"] >= 1


class TestMapReduce:
    def test_short_text_maps_once(self):
        calls = []

        def map_fn(chunk: str) -> int:
            calls.append(chunk)
            return len(chunk)

        result = process_with_map_reduce("hi", map_fn, sum, max_chunk_size=1000)
        assert result == 2
        assert len(calls) == 1

    def test_long_text_maps_each_chunk_then_reduces(self):
        text = "A" * 2500
        result = process_with_map_reduce(text, lambda c: len(c), sum, max_chunk_size=1000)
        # Reduce over chunk lengths totals more than len(text) because of overlap,
        # but must at least cover the whole document.
        assert result >= len(text)


class TestRollingSummary:
    def test_short_text_processed_once(self):
        def process_fn(chunk, prev):
            return (len(chunk), "summary")

        result = process_with_rolling_summary("hi", process_fn, max_chunk_size=1000)
        assert result == 2

    def test_long_text_accumulates_context(self):
        seen_summaries = []

        def process_fn(chunk, prev):
            seen_summaries.append(prev)
            return (len(chunk), f"seen {len(chunk)}")

        text = "A" * 2500
        process_with_rolling_summary(text, process_fn, max_chunk_size=1000)
        # First chunk has no previous summary; later chunks receive one.
        assert seen_summaries[0] is None
        assert any(s is not None for s in seen_summaries[1:])


class TestDocumentTextHelpers:
    def test_prefers_full_text(self):
        doc = {"full_text": "FULL", "abstract": "ABS"}
        text, source = get_text_with_priority(doc)
        assert text == "FULL"
        assert source == "full_text"

    def test_falls_back_to_abstract(self):
        doc = {"abstract": "ABS"}
        text, source = get_text_with_priority(doc)
        assert text == "ABS"
        assert source == "abstract"

    def test_prefer_full_text_false_uses_abstract(self):
        doc = {"full_text": "FULL", "abstract": "ABS"}
        text, source = get_text_with_priority(doc, prefer_full_text=False)
        assert text == "ABS"
        assert source == "abstract"

    def test_no_text_returns_none_source(self):
        text, source = get_text_with_priority({})
        assert text == ""
        assert source == "none"

    def test_combine_title_and_text(self):
        assert combine_title_and_text("T", "body") == "Title: T\n\nbody"

    def test_combine_title_only(self):
        assert combine_title_and_text("T", "") == "Title: T"

    def test_combine_text_only(self):
        assert combine_title_and_text("", "body") == "body"
