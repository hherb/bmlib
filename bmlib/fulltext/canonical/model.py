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

"""The canonical text of a paper: one string, and every piece of it as a span.

A paper's text is converted, whatever its format, into a title and an ordered
list of sections, each with an optional heading and a list of blocks. All of
it is laid out as **one string**, and the title, every heading and every block
is recorded as a :class:`TextSpan` into that string. Everything downstream
(chunks, citations, highlights) points into the same string, so a quote
located once is located for good.

Spans count **Unicode code points**, half-open ``[start, end)``: Python's own
string indices, and what the Rust port counts as ``char``s. A byte offset
differs from the first non-ASCII character (``é``), a UTF-16 one from the first
character outside the Basic Multilingual Plane (``𝛽``), so any conversion
happens at an application's boundary, never here.

The layout is exact: the pieces are joined by :data:`BLOCK_SEPARATOR` and
nothing else, every span is non-empty, and together the spans and separators
cover the whole text. :meth:`CanonicalText.validate` checks that, and
:func:`assemble` validates what it returns.

Converters do not lay text out themselves. They hand :func:`assemble` drafts
(:class:`SectionDraft`, :class:`BlockDraft`), and it normalises, drops what is
empty and records the spans, so every format shares one layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: What separates two pieces (title, heading, block) in the canonical text.
#: It belongs to no span.
BLOCK_SEPARATOR = "\n\n"

#: What separates two cells of a table row. A tab is whitespace, so a quote
#: that separates the cells with spaces still matches the row.
CELL_SEPARATOR = "\t"

#: The level of a top-level section.
TOP_LEVEL = 1

#: Characters Python's ``str.isspace()`` accepts that are not Unicode
#: ``White_Space`` (the information separators U+001C to U+001F). Rust's
#: ``char::is_whitespace`` follows ``White_Space``, so these stay characters
#: in both languages.
_NOT_WHITE_SPACE = frozenset("\x1c\x1d\x1e\x1f")


class BlockKind(Enum):
    """What a block is: running prose, or one row of a table."""

    PARAGRAPH = "paragraph"
    TABLE_ROW = "table_row"


class SourceFormat(Enum):
    """The format a canonical text was converted from."""

    JATS = "jats"
    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"
    PDF = "pdf"
    HTML = "html"


@dataclass(frozen=True)
class TextSpan:
    """A half-open range ``[start, end)`` of code points in the canonical text."""

    start: int
    end: int

    def __post_init__(self) -> None:
        """Refuse a span that is not two integers with ``0 <= start <= end``."""
        if type(self.start) is not int or type(self.end) is not int:
            raise ValueError(f"a span's start and end must be integers, got {self!r}")
        if not 0 <= self.start <= self.end:
            raise ValueError(f"span {self} is reversed or negative")

    def to_dict(self) -> dict[str, int]:
        """Return ``{"start": …, "end": …}``."""
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextSpan:
        """Rebuild a span from :meth:`to_dict` output; ``ValueError`` if malformed."""
        return cls(_field(data, "start"), _field(data, "end"))


@dataclass(frozen=True)
class CanonicalBlock:
    """One block of a section: its kind and where its text lies."""

    kind: BlockKind
    span: TextSpan


@dataclass(frozen=True)
class CanonicalSection:
    """One section: its level (1 is top), its heading if it has one, its blocks.

    Sections form a flat list in document order; a section nests under the
    nearest section before it with a lower ``level``. Levels may skip (a
    Markdown ``#`` followed by ``###``).
    """

    level: int
    heading_span: TextSpan | None
    blocks: tuple[CanonicalBlock, ...]


@dataclass(frozen=True)
class CanonicalText:
    """A paper's canonical text and the spans of its title, headings and blocks.

    ``converter_version`` names the converter that produced ``text``
    (``"jats-1"``, ``"markdown-1"``, ``"plain_text-1"``, ``"pdf-1"``,
    ``"html-1"``), so whatever points
    into it can tell when a newer converter would lay the text out differently.
    """

    text: str
    title_span: TextSpan | None
    sections: tuple[CanonicalSection, ...]
    source_format: SourceFormat
    converter_version: str

    def span_text(self, span: TextSpan) -> str:
        """The text *span* covers.

        Raises:
            ValueError: if the span reaches outside the text.
        """
        _check_span(span, len(self.text))
        return self.text[span.start : span.end]

    def spans(self) -> list[TextSpan]:
        """Every span, title first, in document order."""
        out = [self.title_span] if self.title_span is not None else []
        for section in self.sections:
            if section.heading_span is not None:
                out.append(section.heading_span)
            out.extend(block.span for block in section.blocks)
        return out

    def validate(self) -> None:
        """Check the layout: the spans, in order and each non-empty, tile the text.

        The first span starts at 0, each next one starts right after a
        :data:`BLOCK_SEPARATOR` following the one before, and the last ends at
        the end of the text; with no spans the text is empty.

        Raises:
            ValueError: naming the first span or level that is wrong.
        """
        for section in self.sections:
            if type(section.level) is not int or section.level < TOP_LEVEL:
                raise ValueError(
                    f"a section's level must be an integer of at least {TOP_LEVEL}, "
                    f"got {section.level!r}"
                )
        position = 0
        for index, span in enumerate(self.spans()):
            _check_span(span, len(self.text))
            if index:
                if self.text[position : position + len(BLOCK_SEPARATOR)] != BLOCK_SEPARATOR:
                    raise ValueError(f"no block separator at {position}, before span {span}")
                position += len(BLOCK_SEPARATOR)
            if span.start != position or span.end == span.start:
                raise ValueError(
                    f"span {span} is empty or does not start at {position}, "
                    "where the layout puts the next piece"
                )
            position = span.end
        if position != len(self.text):
            raise ValueError(f"the text runs on past the last span, from {position}")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form, the shape the shared contract pins."""
        return {
            "text": self.text,
            "title_span": _span_dict(self.title_span),
            "sections": [
                {
                    "level": section.level,
                    "heading_span": _span_dict(section.heading_span),
                    "blocks": [
                        {"kind": block.kind.value, "span": block.span.to_dict()}
                        for block in section.blocks
                    ],
                }
                for section in self.sections
            ],
            "source_format": self.source_format.value,
            "converter_version": self.converter_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalText:
        """Rebuild and :meth:`validate` a canonical text from :meth:`to_dict` output.

        Raises:
            ValueError: if a field is missing or malformed, or a span is invalid.
        """
        text = _field(data, "text")
        version = _field(data, "converter_version")
        if not isinstance(text, str) or not isinstance(version, str):
            raise ValueError("text and converter_version must be strings")
        sections = tuple(
            CanonicalSection(
                level=_field(section, "level"),
                heading_span=_optional_span(_field(section, "heading_span")),
                blocks=tuple(
                    CanonicalBlock(
                        kind=_enum(BlockKind, _field(block, "kind")),
                        span=TextSpan.from_dict(_field(block, "span")),
                    )
                    for block in _list_field(section, "blocks")
                ),
            )
            for section in _list_field(data, "sections")
        )
        result = cls(
            text=text,
            title_span=_optional_span(_field(data, "title_span")),
            sections=sections,
            source_format=_enum(SourceFormat, _field(data, "source_format")),
            converter_version=version,
        )
        result.validate()
        return result


@dataclass(frozen=True)
class BlockDraft:
    """A block before layout: its kind and its text, not yet normalised."""

    kind: BlockKind
    text: str

    @classmethod
    def paragraph(cls, text: str) -> BlockDraft:
        """A paragraph; :func:`assemble` collapses its whitespace."""
        return cls(BlockKind.PARAGRAPH, text)

    @classmethod
    def row(cls, cells: Sequence[str]) -> BlockDraft:
        """A table row: each cell normalised, joined by :data:`CELL_SEPARATOR`.

        An empty cell is kept (it holds a column's place); a row whose cells
        are all empty is an empty block, which :func:`assemble` drops.
        """
        return cls(BlockKind.TABLE_ROW, CELL_SEPARATOR.join(normalise_whitespace(c) for c in cells))


@dataclass
class SectionDraft:
    """A section before layout: heading (``""`` for none), level, blocks."""

    heading: str
    level: int
    blocks: list[BlockDraft] = field(default_factory=list)


def is_white_space(ch: str) -> bool:
    """Whether *ch* has the Unicode ``White_Space`` property (as Rust decides it)."""
    return ch.isspace() and ch not in _NOT_WHITE_SPACE


def normalise_whitespace(text: str) -> str:
    """Collapse each run of :func:`is_white_space` characters to one space, then trim."""
    out: list[str] = []
    in_run = False
    for ch in text:
        if is_white_space(ch):
            if not in_run:
                out.append(" ")
                in_run = True
        else:
            out.append(ch)
            in_run = False
    return "".join(out).strip(" ")


def assemble(
    title: str,
    sections: Sequence[SectionDraft],
    source_format: SourceFormat,
    converter_version: str,
) -> CanonicalText:
    """Lay *title* and *sections* out as one canonical text and record every span.

    The title, headings and paragraphs have their whitespace collapsed; a table
    row is kept as :meth:`BlockDraft.row` built it. An empty block is dropped,
    a section with no heading and no blocks left is dropped, and a heading with
    no blocks is kept. Pieces are joined by :data:`BLOCK_SEPARATOR`.

    Raises:
        ValueError: if a section's level is below :data:`TOP_LEVEL` (the
            result is validated before it is returned).
    """
    parts: list[str] = []
    length = 0

    def put(piece: str) -> TextSpan:
        nonlocal length
        if parts:
            parts.append(BLOCK_SEPARATOR)
            length += len(BLOCK_SEPARATOR)
        parts.append(piece)
        start, length = length, length + len(piece)
        return TextSpan(start, length)

    clean_title = normalise_whitespace(title)
    title_span = put(clean_title) if clean_title else None
    laid_out: list[CanonicalSection] = []
    for draft in sections:
        heading = normalise_whitespace(draft.heading)
        blocks = [(b.kind, _block_text(b)) for b in draft.blocks]
        blocks = [(kind, text) for kind, text in blocks if text]
        if not heading and not blocks:
            continue
        heading_span = put(heading) if heading else None
        laid_out.append(
            CanonicalSection(
                level=draft.level,
                heading_span=heading_span,
                blocks=tuple(CanonicalBlock(kind, put(text)) for kind, text in blocks),
            )
        )
    result = CanonicalText(
        text="".join(parts),
        title_span=title_span,
        sections=tuple(laid_out),
        source_format=source_format,
        converter_version=converter_version,
    )
    result.validate()
    return result


def _block_text(block: BlockDraft) -> str:
    """A block's laid-out text: each cell of a row normalised (empty if every
    cell is), prose normalised as a whole."""
    if block.kind is BlockKind.TABLE_ROW:
        cells = [normalise_whitespace(cell) for cell in block.text.split(CELL_SEPARATOR)]
        return CELL_SEPARATOR.join(cells) if any(cells) else ""
    return normalise_whitespace(block.text)


def _check_span(span: TextSpan, length: int) -> None:
    """Raise ``ValueError`` if *span* reaches past a text of *length* code points.

    A reversed or negative span cannot be built (:meth:`TextSpan.__post_init__`).
    """
    if span.end > length:
        raise ValueError(f"span {span} lies outside a text of {length} code points")


def _span_dict(span: TextSpan | None) -> dict[str, int] | None:
    return None if span is None else span.to_dict()


def _optional_span(data: Any) -> TextSpan | None:
    return None if data is None else TextSpan.from_dict(data)


def _field(data: Any, name: str) -> Any:
    """``data[name]``, or a ``ValueError`` naming the missing field."""
    if not isinstance(data, dict) or name not in data:
        raise ValueError(f"canonical text is missing the field {name!r}")
    return data[name]


def _list_field(data: Any, name: str) -> list[Any]:
    """``data[name]`` if it is a list, else a ``ValueError`` naming the field."""
    value = _field(data, name)
    if not isinstance(value, list):
        raise ValueError(f"canonical text field {name!r} must be a list, got {value!r}")
    return value


def _enum(kind: type[Enum], value: Any) -> Any:
    """The member of *kind* whose value is *value*, or a ``ValueError`` naming it."""
    try:
        return kind(value)
    except ValueError:
        raise ValueError(f"unknown {kind.__name__} {value!r}") from None
