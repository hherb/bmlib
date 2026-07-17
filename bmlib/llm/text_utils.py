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

"""Text chunking and long-document processing utilities.

Splits documents that exceed an LLM's context window into overlapping
chunks, and drives map-reduce / rolling-summary processing over them. The
chunker is **boundary-aware** by default: it prefers to end chunks on
paragraph or sentence boundaries and never discards text — every character
of the input appears in at least one chunk.

Example::

    from bmlib.llm.text_utils import chunk_text

    for chunk in chunk_text(long_document, chunk_size=10000, overlap=250):
        process(chunk.content)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Default configuration constants (characters).
DEFAULT_CHUNK_SIZE = 10000
DEFAULT_CHUNK_OVERLAP = 250
DEFAULT_MIN_CHUNK_SIZE = 500


@dataclass
class TextChunk:
    """A chunk of text with positional metadata.

    Attributes:
        content: The chunk text.
        start_pos: Start offset (inclusive) in the source text.
        end_pos: End offset (exclusive) in the source text.
        chunk_index: Zero-based index of this chunk.
        total_chunks: Total number of chunks the source was split into.
    """

    content: str
    start_pos: int
    end_pos: int
    chunk_index: int
    total_chunks: int

    @property
    def size(self) -> int:
        """Size of this chunk in characters."""
        return len(self.content)


class TextChunker:
    """Sliding-window chunker with optional boundary awareness.

    Overlapping chunks ensure no information is lost at chunk boundaries —
    important for citation extraction and question answering. When
    ``boundary_aware`` is set, chunk ends are pulled back to the nearest
    paragraph or sentence break (beyond ``min_chunk_size``) so sentences are
    not split mid-way; the full text is always preserved.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
        *,
        boundary_aware: bool = True,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
    ) -> None:
        """Initialise the chunker.

        Args:
            chunk_size: Maximum size of each chunk in characters.
            overlap: Characters of overlap between consecutive chunks.
            boundary_aware: Prefer paragraph/sentence breaks over hard cuts.
            min_chunk_size: Minimum offset a boundary break may occur at,
                to avoid tiny leading chunks.

        Raises:
            ValueError: If ``chunk_size <= 0``, ``overlap < 0``, or
                ``overlap >= chunk_size``.
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if overlap < 0:
            raise ValueError(f"overlap must be non-negative, got {overlap}")
        if overlap >= chunk_size:
            raise ValueError(f"overlap ({overlap}) must be less than chunk_size ({chunk_size})")

        self.chunk_size = chunk_size
        self.overlap = overlap
        self.boundary_aware = boundary_aware
        self.min_chunk_size = min_chunk_size

    def chunk_text(self, text: str) -> list[TextChunk]:
        """Split *text* into overlapping :class:`TextChunk` objects."""
        if not text:
            return []

        text_length = len(text)
        if text_length <= self.chunk_size:
            return [
                TextChunk(
                    content=text,
                    start_pos=0,
                    end_pos=text_length,
                    chunk_index=0,
                    total_chunks=1,
                )
            ]

        chunks: list[TextChunk] = []
        start = 0
        while start < text_length:
            end = min(start + self.chunk_size, text_length)
            if self.boundary_aware and end < text_length:
                end = self._adjust_to_boundary(text, start, end)

            chunks.append(
                TextChunk(
                    content=text[start:end],
                    start_pos=start,
                    end_pos=end,
                    chunk_index=len(chunks),
                    total_chunks=0,  # filled in after the loop
                )
            )

            if end >= text_length:
                break

            next_start = end - self.overlap
            start = next_start if next_start > start else end

        total = len(chunks)
        for chunk in chunks:
            chunk.total_chunks = total
        return chunks

    def _adjust_to_boundary(self, text: str, start: int, end: int) -> int:
        """Pull *end* back to the nearest paragraph/sentence break, if any.

        Returns the original *end* when no suitable break exists beyond
        ``min_chunk_size``.
        """
        window = text[start:end]

        para_break = window.rfind("\n\n")
        if para_break > self.min_chunk_size:
            return start + para_break + 2

        sentence_breaks = [
            window.rfind(". "),
            window.rfind(".\n"),
            window.rfind("? "),
            window.rfind("! "),
        ]
        candidates = [b for b in sentence_breaks if b > self.min_chunk_size]
        if candidates:
            return start + max(candidates) + 2

        return end

    def get_chunk_info(self, text: str) -> dict[str, Any]:
        """Report how *text* would be chunked, without building the chunks."""
        chunks = self.chunk_text(text)
        if not chunks:
            return {
                "text_length": 0,
                "num_chunks": 0,
                "chunk_size": self.chunk_size,
                "overlap": self.overlap,
                "avg_chunk_size": 0,
                "last_chunk_size": 0,
            }

        sizes = [c.size for c in chunks]
        return {
            "text_length": len(text),
            "num_chunks": len(chunks),
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "avg_chunk_size": sum(sizes) // len(sizes),
            "last_chunk_size": sizes[-1],
        }


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    *,
    boundary_aware: bool = True,
    min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
) -> list[TextChunk]:
    """Chunk *text* using a one-off :class:`TextChunker`.

    Convenience wrapper; see :class:`TextChunker` for the parameters.
    """
    chunker = TextChunker(
        chunk_size=chunk_size,
        overlap=overlap,
        boundary_aware=boundary_aware,
        min_chunk_size=min_chunk_size,
    )
    return chunker.chunk_text(text)


def process_with_map_reduce(
    text: str,
    map_fn: Callable[[str], Any],
    reduce_fn: Callable[[list[Any]], Any],
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Any:
    """Process long *text* with a map-reduce pattern.

    Maps each chunk to an intermediate result, then reduces the list of
    intermediates to a final result. Short text bypasses chunking and is
    passed straight to *map_fn*.

    Args:
        text: Full text to process.
        map_fn: Maps one chunk's content to an intermediate result.
        reduce_fn: Combines the list of intermediate results.
        max_chunk_size: Maximum chunk size in characters.

    Returns:
        The reduced result (or ``map_fn(text)`` for short text).
    """
    if len(text) <= max_chunk_size:
        return map_fn(text)

    chunks = chunk_text(text, chunk_size=max_chunk_size)
    intermediate_results = []
    for chunk in chunks:
        logger.debug(
            "Processing chunk %d/%d (%d chars)",
            chunk.chunk_index + 1,
            chunk.total_chunks,
            chunk.size,
        )
        intermediate_results.append(map_fn(chunk.content))

    return reduce_fn(intermediate_results)


def process_with_rolling_summary(
    text: str,
    process_fn: Callable[[str, str | None], tuple[Any, str]],
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
    summary_max_length: int = 500,
) -> Any:
    """Process long *text* while carrying a rolling summary between chunks.

    Each chunk is processed with the previous chunk's summary as context.
    The rolling summary is truncated to *summary_max_length* to bound growth.
    Short text bypasses chunking (called with ``None`` context).

    Args:
        text: Full text to process.
        process_fn: ``(chunk, previous_summary) -> (result, new_summary)``.
        max_chunk_size: Maximum chunk size in characters.
        summary_max_length: Maximum length of the carried summary.

    Returns:
        The result from processing the final chunk.
    """
    if len(text) <= max_chunk_size:
        result, _ = process_fn(text, None)
        return result

    chunks = chunk_text(text, chunk_size=max_chunk_size)
    rolling_summary: str | None = None
    result: Any = None
    for chunk in chunks:
        logger.debug(
            "Processing chunk %d/%d (%d chars)",
            chunk.chunk_index + 1,
            chunk.total_chunks,
            chunk.size,
        )
        result, rolling_summary = process_fn(chunk.content, rolling_summary)
        if rolling_summary and len(rolling_summary) > summary_max_length:
            rolling_summary = rolling_summary[:summary_max_length] + "..."

    return result


def get_text_with_priority(
    document: dict[str, Any],
    prefer_full_text: bool = True,
) -> tuple[str, str]:
    """Return the best available text from *document* and its source field.

    Never truncates. Checks ``full_text``, ``abstract``, ``content``, and
    ``text`` fields in priority order.

    Args:
        document: Document mapping with candidate text fields.
        prefer_full_text: Prefer ``full_text`` over ``abstract`` when both exist.

    Returns:
        Tuple of ``(text, source_field_name)``; ``("", "none")`` if empty.
    """
    full_text = document.get("full_text", "") or ""
    abstract = document.get("abstract", "") or ""
    content = document.get("content", "") or ""
    text_field = document.get("text", "") or ""

    if prefer_full_text and full_text:
        return full_text, "full_text"
    if abstract:
        return abstract, "abstract"
    if full_text:
        return full_text, "full_text"
    if content:
        return content, "content"
    if text_field:
        return text_field, "text"
    return "", "none"


def combine_title_and_text(title: str, text: str, max_title_length: int = 500) -> str:
    """Prefix *text* with *title* for analysis.

    Args:
        title: Document title (truncated at *max_title_length* as a guard).
        text: Document body (abstract or full text).
        max_title_length: Maximum retained title length.

    Returns:
        ``"Title: <title>\\n\\n<text>"``, or whichever part is present.
    """
    if title and len(title) > max_title_length:
        title = title[:max_title_length]

    if title and text:
        return f"Title: {title}\n\n{text}"
    if title:
        return f"Title: {title}"
    return text
