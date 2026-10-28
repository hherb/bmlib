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

"""Sentence-aware chunks of a canonical text, for retrieval by embedding.

:func:`chunk_canonical` splits each block of a :class:`CanonicalText` into
*units* (sentences, or pieces of a sentence longer than a chunk) and packs a
section's units, in order, into chunks of at most ``max_chars`` code points.
A chunk is a :class:`TextSpan` into the canonical text plus the index of the
section it lies in; it never crosses a section, and it may cross the blocks of
one. Consecutive chunks of a section share whole units, up to
``overlap_chars``.

The sizes follow bmlibrarian's ``embeddings/chunk_embedder.py`` (1,000 and
100), and the sentence rule its ``adaptive_chunker_optimized.py``: a ``.``,
``!`` or ``?`` followed by whitespace ends a sentence unless the token before
it is an abbreviation (:data:`ABBREVIATIONS`). Unlike bmlibrarian,
``max_chars`` is a hard cap: a longer unit is cut at whitespace, or at the cap
where it has none (a cut at the cap counts code points, so it can part a
combining mark from its letter). Nothing is dropped: every non-whitespace code
point of every block lies in some chunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bmlib.fulltext.canonical.model import CanonicalText, TextSpan, _field, is_white_space

#: Bumped whenever a change can move the chunks of any canonical text, so a
#: stored chunk (and its embedding) can tell it is stale. Pinned against this
#: file and the model in ``tests/test_renderer_version.py``.
CHUNKER_VERSION = 1

#: The longest chunk, in code points (bmlibrarian's ``DEFAULT_CHUNK_SIZE``).
DEFAULT_MAX_CHARS = 1000

#: The most two consecutive chunks of a section share, in code points
#: (bmlibrarian's ``DEFAULT_CHUNK_OVERLAP``).
DEFAULT_OVERLAP_CHARS = 100

#: The marks that end a sentence when whitespace follows.
SENTENCE_ENDINGS = frozenset(".!?")

#: Tokens after which a ``.``, ``!`` or ``?`` does not end a sentence, compared
#: lower-case.
#: bmlibrarian's fourteen, plus ``figs``, ``e.g``, ``i.e``, ``cf`` and ``approx``.
ABBREVIATIONS = frozenset(
    {
        "dr",
        "mr",
        "ms",
        "mrs",
        "fig",
        "tab",
        "vol",
        "no",
        "pp",
        "al",
        "jr",
        "sr",
        "vs",
        "ca",
        "figs",
        "e.g",
        "i.e",
        "cf",
        "approx",
    }
)

#: Characters stripped from the front of a token before it is compared with
#: :data:`ABBREVIATIONS`, so ``(Fig. 2)`` reads ``fig``.
OPENING_MARKS = frozenset("([{\"'“‘")


@dataclass(frozen=True)
class Chunk:
    """One chunk: the index of the section it lies in, and its span.

    It does not carry :data:`CHUNKER_VERSION`; a caller storing chunks records
    that beside the canonical text's ``converter_version``.
    """

    section_index: int
    span: TextSpan

    def __post_init__(self) -> None:
        """Refuse a section index that is not a non-negative integer."""
        if type(self.section_index) is not int or self.section_index < 0:
            raise ValueError(
                f"a chunk's section_index must be a non-negative integer, got {self!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return ``{"section_index": …, "span": {"start": …, "end": …}}``."""
        return {"section_index": self.section_index, "span": self.span.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        """Rebuild a chunk from :meth:`to_dict` output; ``ValueError`` if malformed."""
        return cls(_field(data, "section_index"), TextSpan.from_dict(_field(data, "span")))


def chunk_canonical(
    canonical: CanonicalText,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Split *canonical* into chunks, section by section, in document order.

    Each chunk's span starts and ends on non-whitespace, is at most *max_chars*
    code points long, and lies within one section's blocks. The title and the
    headings are not chunked; a section's heading is reached by its index. So a
    text whose blocks hold no non-whitespace — a title and headings alone —
    gives no chunks.

    Raises:
        ValueError: if *max_chars* is below 1, *overlap_chars* is negative or
            not below *max_chars*, either is not an integer, or *canonical*
            fails :meth:`CanonicalText.validate`.
    """
    if type(max_chars) is not int or type(overlap_chars) is not int:
        raise ValueError("max_chars and overlap_chars must be integers")
    if max_chars < 1 or not 0 <= overlap_chars < max_chars:
        raise ValueError(
            f"need max_chars >= 1 and 0 <= overlap_chars < max_chars, "
            f"got {max_chars} and {overlap_chars}"
        )
    canonical.validate()
    text = canonical.text
    chunks: list[Chunk] = []
    for index, section in enumerate(canonical.sections):
        units = [
            piece
            for block in section.blocks
            for unit in sentence_spans(text, block.span)
            for piece in _cut(text, unit, max_chars)
        ]
        chunks.extend(Chunk(index, span) for span in _pack(units, max_chars, overlap_chars))
    return chunks


def sentence_spans(text: str, span: TextSpan) -> list[TextSpan]:
    """The sentences of ``text[span.start:span.end]``, as spans into *text*.

    Each starts and ends on non-whitespace. A sentence ends at a mark of
    :data:`SENTENCE_ENDINGS` followed by whitespace, unless the token before
    the mark is an abbreviation; the span's end always ends one. A span of
    whitespace alone has none.
    """
    out: list[TextSpan] = []
    start = _skip_white_space(text, span.start, span.end)
    for i in range(start, span.end - 1):
        if (
            text[i] in SENTENCE_ENDINGS
            and is_white_space(text[i + 1])
            and not is_abbreviation(text, start, i)
        ):
            out.append(TextSpan(start, i + 1))
            start = _skip_white_space(text, i + 1, span.end)
    end = _trim_white_space(text, start, span.end)
    if start < end:
        out.append(TextSpan(start, end))
    return out


def is_abbreviation(text: str, start: int, mark: int) -> bool:
    """Whether the token before ``text[mark]``, not reaching back past *start*,
    is one of :data:`ABBREVIATIONS`.

    The token is the run of non-whitespace before the mark, its leading
    :data:`OPENING_MARKS` stripped; only an ASCII token can match.
    """
    begin = mark
    while begin > start and not is_white_space(text[begin - 1]):
        begin -= 1
    while begin < mark and text[begin] in OPENING_MARKS:
        begin += 1
    token = text[begin:mark]
    return token.isascii() and token.lower() in ABBREVIATIONS


def _cut(text: str, unit: TextSpan, max_chars: int) -> list[TextSpan]:
    """*unit* in pieces of at most *max_chars*, each cut at the last whitespace
    that keeps it within the cap, or at the cap where the piece has none."""
    pieces: list[TextSpan] = []
    start = unit.start
    while unit.end - start > max_chars:
        cut = start + max_chars
        while cut > start and not is_white_space(text[cut]):
            cut -= 1
        end = _trim_white_space(text, start, cut) if cut > start else start + max_chars
        pieces.append(TextSpan(start, end))
        start = _skip_white_space(text, end, unit.end)
    pieces.append(TextSpan(start, unit.end))
    return pieces


def _pack(units: list[TextSpan], max_chars: int, overlap_chars: int) -> list[TextSpan]:
    """Pack *units* (each within the cap, in order) greedily into chunk spans.

    After a chunk, the next restarts at the first of its units after its first
    that begins within *overlap_chars* of its end (the first is never carried,
    so every chunk moves on), unless there is none or those units and the next
    new one would together pass *max_chars*.
    """
    chunks: list[TextSpan] = []
    first = 0
    while first < len(units):
        last = first
        while last + 1 < len(units) and units[last + 1].end - units[first].start <= max_chars:
            last += 1
        end = units[last].end
        chunks.append(TextSpan(units[first].start, end))
        if last + 1 == len(units):
            break
        carried = next(
            (k for k in range(first + 1, last + 1) if units[k].start >= end - overlap_chars),
            last + 1,
        )
        if units[last + 1].end - units[carried].start > max_chars:
            carried = last + 1
        first = carried
    return chunks


def _skip_white_space(text: str, start: int, end: int) -> int:
    """The first index from *start* (before *end*) that is not whitespace, else *end*."""
    while start < end and is_white_space(text[start]):
        start += 1
    return start


def _trim_white_space(text: str, start: int, end: int) -> int:
    """*end* moved back over trailing whitespace, not past *start*."""
    while end > start and is_white_space(text[end - 1]):
        end -= 1
    return end
