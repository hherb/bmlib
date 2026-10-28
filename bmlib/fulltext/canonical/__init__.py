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

"""One canonical text per paper, built from JATS, Markdown, plain text or a PDF, and its chunks.

See :mod:`bmlib.fulltext.canonical.model` for the layout and the offset unit.
"""

from bmlib.fulltext.canonical.chunk import (
    ABBREVIATIONS,
    CHUNKER_VERSION,
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    Chunk,
    chunk_canonical,
    is_abbreviation,
    sentence_spans,
)
from bmlib.fulltext.canonical.jats import (
    JATS_CANONICAL_VERSION,
    canonical_from_jats,
    strip_links,
)
from bmlib.fulltext.canonical.markdown import (
    MARKDOWN_VERSION,
    canonical_from_markdown,
    strip_inline,
)
from bmlib.fulltext.canonical.model import (
    BLOCK_SEPARATOR,
    CELL_SEPARATOR,
    TOP_LEVEL,
    BlockDraft,
    BlockKind,
    CanonicalBlock,
    CanonicalSection,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    TextSpan,
    assemble,
    is_white_space,
    normalise_whitespace,
)
from bmlib.fulltext.canonical.pdf import PDF_CANONICAL_VERSION, canonical_from_segmented
from bmlib.fulltext.canonical.plain import (
    PLAIN_TEXT_VERSION,
    canonical_from_plain_text,
    split_lines,
    split_paragraphs,
)

__all__ = [
    "ABBREVIATIONS",
    "BLOCK_SEPARATOR",
    "CELL_SEPARATOR",
    "CHUNKER_VERSION",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "JATS_CANONICAL_VERSION",
    "MARKDOWN_VERSION",
    "PDF_CANONICAL_VERSION",
    "PLAIN_TEXT_VERSION",
    "TOP_LEVEL",
    "BlockDraft",
    "BlockKind",
    "CanonicalBlock",
    "CanonicalSection",
    "CanonicalText",
    "Chunk",
    "SectionDraft",
    "SourceFormat",
    "TextSpan",
    "assemble",
    "canonical_from_jats",
    "canonical_from_markdown",
    "canonical_from_plain_text",
    "canonical_from_segmented",
    "chunk_canonical",
    "is_abbreviation",
    "is_white_space",
    "normalise_whitespace",
    "sentence_spans",
    "split_lines",
    "split_paragraphs",
    "strip_inline",
    "strip_links",
]
