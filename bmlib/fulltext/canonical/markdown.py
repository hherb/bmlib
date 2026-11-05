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

"""Markdown to canonical text: headings become sections, inline markup is stripped.

A deliberately small subset, hand-written here and in the Rust port alike, so
the two cannot drift apart through two different parsers. Line by line:

* ATX headings (``#`` to ``######``, closing ``#``s dropped) and setext
  headings (``===`` for level 1, ``---`` for level 2, under a paragraph line)
  start a section at their level. A level-1 heading before any other content
  is the **title**, not a section.
* Blank lines separate paragraphs. A list item is a block of its own and takes
  the plain lines that follow it: a bullet (``-``, ``*``, ``+``) loses its
  marker, a number (``1.``, ``1)``) keeps it, being text. As in CommonMark, a
  numbered line interrupts a paragraph (or a bulleted item) only when it starts
  at 1, and an item of a numbered list at any number, so a wrapped ``2019.``
  stays in its sentence. A setext underline under a list item is a thematic
  break, not a heading. ``>`` is stripped. A fenced code block (three or more
  backticks or tildes) is one block whose inline markup is not stripped; its
  lines and indentation collapse to single spaces like any paragraph's. A
  backtick fence's info string may not hold a backtick, so a line opening with
  three backticks and holding another backtick is text (a code span). A fence
  opened inside a blockquote has its lines' ``>`` stripped and ends where the
  blockquote does, at a line without them, as in CommonMark. A thematic break
  (``---``, ``***``, ``___``) is dropped.
* A line starting with ``|`` is a table row; the delimiter row
  (``|---|:--:|``) right after a table's first row is dropped, and ``\\|``
  is a pipe inside a cell.
* Inline: emphasis and strong emphasis (``*``/``_``) lose their delimiters,
  a code span its backtick runs (an opening run closes at the next run of the
  same length), a link or image keeps its text (so one with empty text leaves
  nothing, as a renderer shows nothing), an autolink (``<https://…>``) its
  address, and a backslash escape its character. Emphasis opens only where the
  delimiter is not preceded by a letter or digit, and closes only where it is
  not followed by one, so ``snake_case``, ``IL_6`` and ``2*3*4`` stay as
  written; a letter or digit is ``str.isalnum``, which the Rust port's
  ``char::is_alphanumeric`` exceeds for some marks and symbols (see
  ``markdown.rs``). A link's target may hold balanced parentheses
  (``doi.org/…(20)…``) and its text escaped brackets; a target holding
  whitespace is a link only as ``url "title"``, so a parenthetical after a
  bracket stays text. Anything else, HTML included, is text. A paragraph is
  read for link brackets once, and for an emphasis closer once per delimiter
  and run length that finds none, so unclosed openers cost linear time (#549).
* Whitespace is Unicode ``White_Space`` (:func:`is_white_space`), never
  ``str.isspace()``, so U+001C to U+001F stay characters as in the Rust port.
* Content before the first heading is an untitled level-1 section.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from typing import NamedTuple

from bmlib.fulltext.canonical.model import (
    TOP_LEVEL,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
    is_white_space,
    normalise_whitespace,
)
from bmlib.fulltext.canonical.plain import split_lines

#: Bumped whenever this converter (or :func:`assemble`) can lay a text out
#: differently.
MARKDOWN_VERSION = 2

#: An ATX heading: up to three spaces, one to six ``#``, then a space or the end.
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t](.*))?$")
#: The closing ``#`` run an ATX heading may end in.
_ATX_CLOSE = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
#: A setext underline: ``=`` for level 1, ``-`` for level 2.
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
#: A list item: a bullet (group 1) or a number (group 2), then its text (group 3).
_LIST_ITEM = re.compile(r"^[ \t]*(?:([-*+])|([0-9]{1,9})[.)])[ \t]+(.*)$")
#: The number a numbered item must start at to interrupt a paragraph.
_INTERRUPTING_NUMBER = 1
#: A code fence: three or more backticks or tildes, then an info string.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: A blockquote marker and the one space that may follow it.
_QUOTE = re.compile(r"^ {0,3}> ?")
#: What a table's delimiter row is made of.
_DELIMITER_ROW_CHARS = frozenset("|:- \t")
#: The characters that make a thematic break, at least this many of one.
_BREAK_CHARS = frozenset("-*_")
_MIN_BREAK_RUN = 3
#: The ASCII punctuation a backslash escapes (CommonMark's set).
_ESCAPABLE = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
#: The schemes an autolink is recognised by.
_AUTOLINK_SCHEMES = ("http://", "https://", "mailto:")
#: The emphasis delimiters and the longest run read as emphasis (strong).
_EMPHASIS = frozenset("*_")
_MAX_EMPHASIS_RUN = 2
#: The level setext underlines give, by underline character.
_SETEXT_LEVELS = {"=": 1, "-": 2}


def _strip(text: str) -> str:
    """*text* without leading and trailing :func:`is_white_space` characters."""
    start, end = 0, len(text)
    while start < end and is_white_space(text[start]):
        start += 1
    while end > start and is_white_space(text[end - 1]):
        end -= 1
    return text[start:end]


def strip_inline(text: str) -> str:
    """*text* with its inline Markdown markup removed (see the module docstring)."""
    out: list[str] = []
    # Emphasis openers already known to have no closer, by delimiter and run (#549).
    unclosed: set[tuple[str, int]] = set()
    links: _LinkIndex | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in _ESCAPABLE:
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            run = _run_length(text, i)
            close = _find_run(text, i + run, run)
            out.append(text[i + run : close] if close >= 0 else text[i : i + run])
            i = close + run if close >= 0 else i + run
            continue
        if ch == "[" or (ch == "!" and text.startswith("[", i + 1)):
            if links is None:
                links = _link_index(text)
            link = _link_at(text, i + 1 if ch == "!" else i, links)
            if link is not None:
                label, i = link
                out.append(strip_inline(label))
                continue
        if ch == "<":
            close = text.find(">", i + 1)
            inner = text[i + 1 : close] if close > 0 else ""
            if inner.startswith(_AUTOLINK_SCHEMES) and not any(map(is_white_space, inner)):
                out.append(inner)
                i = close + 1
                continue
        if ch in _EMPHASIS:
            emphasis = _emphasis_at(text, i, unclosed)
            if emphasis is not None:
                inner, i = emphasis
                out.append(strip_inline(inner))
                continue
            run = _run_length(text, i)
            out.append(text[i : i + run])
            i += run
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_run(text: str, start: int, run: int) -> int:
    """The index of the first run of exactly *run* backticks at or after *start*, or -1."""
    i = start
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        length = _run_length(text, i)
        if length == run:
            return i
        i += length
    return -1


class _LinkIndex(NamedTuple):
    """What finding a link in one text needs, read once so no scan repeats (#549)."""

    #: Every unescaped ``]``, in order.
    closes: list[int]
    #: Each unescaped ``(``'s balancing ``)``.
    parens: dict[int, int]
    #: Every :func:`is_white_space` index, and every other index, in order.
    spaces: list[int]
    solid: list[int]


def _link_index(text: str) -> _LinkIndex:
    """The :class:`_LinkIndex` of *text*.

    A backslash escapes the character after it. A link's scans start just
    after a ``[`` or a ``]``, never inside an escape, so one pass from the
    start reads every escape as they would.
    """
    closes: list[int] = []
    parens: dict[int, int] = {}
    opens: list[int] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "]":
            closes.append(i)
        elif ch == "(":
            opens.append(i)
        elif ch == ")" and opens:
            parens[opens.pop()] = i
        i += 1
    spaces: list[int] = []
    solid: list[int] = []
    for i, ch in enumerate(text):
        (spaces if is_white_space(ch) else solid).append(i)
    return _LinkIndex(closes, parens, spaces, solid)


def _first_at(indices: list[int], start: int) -> int:
    """The first of the ordered *indices* at or after *start*, or -1."""
    at = bisect_left(indices, start)
    return indices[at] if at < len(indices) else -1


def _is_link_target(index: _LinkIndex, text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` is a link destination: no whitespace, or ``url "title"``."""
    first = _first_at(index.solid, start)
    if first < 0 or first >= end:
        return True
    last = index.solid[bisect_left(index.solid, end) - 1]
    gap = _first_at(index.spaces, first)
    if gap < 0 or gap > last:
        return True
    title = _first_at(index.solid, gap)
    return title < last and text[title] in "\"'" and text[last] == text[title]


def _link_at(text: str, start: int, index: _LinkIndex) -> tuple[str, int] | None:
    """The label of a ``[label](target)`` starting at *start*, and the index after it.

    The label ends at the first unescaped ``]``; the target at the ``)`` that
    balances its opening one, skipping escaped parentheses, and must pass
    :func:`_is_link_target`. *index* is *text*'s :func:`_link_index`.
    """
    close = _first_at(index.closes, start + 1)
    end = index.parens.get(close + 1) if close >= 0 else None
    if end is None or not _is_link_target(index, text, close + 2, end):
        return None
    return text[start + 1 : close], end + 1


def _run_length(text: str, start: int) -> int:
    """How many copies of ``text[start]`` follow from *start*."""
    end = start
    while end < len(text) and text[end] == text[start]:
        end += 1
    return end - start


def _emphasis_at(text: str, start: int, unclosed: set[tuple[str, int]]) -> tuple[str, int] | None:
    """The content of an emphasis opening at *start*, and the index after it.

    The opener is a run of one or two delimiters not preceded by a letter or
    digit and followed by a non-space; the closer is the first run of the same
    length after it that follows a non-space and is not followed by a letter
    or digit. Whether a run closes depends on the run alone, so once an opener
    finds none, no later opener of its delimiter and length can: it joins
    *unclosed*, which the caller keeps for one left-to-right walk of *text*.
    """
    delimiter = text[start]
    run = _run_length(text, start)
    after = start + run
    if run > _MAX_EMPHASIS_RUN or after >= len(text) or is_white_space(text[after]):
        return None
    if start > 0 and text[start - 1].isalnum():
        return None
    if (delimiter, run) in unclosed:
        return None
    i = after + 1
    while i < len(text):
        if text[i] != delimiter:
            i += 1
            continue
        length = _run_length(text, i)
        end = i + length
        if (
            length == run
            and not is_white_space(text[i - 1])
            and not (end < len(text) and text[end].isalnum())
        ):
            return text[after:i], end
        i = end
    unclosed.add((delimiter, run))
    return None


def _table_cells(line: str) -> list[str]:
    """The cells of a table row, split on unescaped pipes, inline markup stripped."""
    body = _strip(line)[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            current.append("\\|")
            i += 2
            continue
        if body[i] == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(body[i])
        i += 1
    cells.append("".join(current))
    return [strip_inline(cell) for cell in cells]


def _is_delimiter_row(line: str) -> bool:
    """Whether *line* is a table's ``|---|:--:|`` row."""
    return "-" in line and all(c in _DELIMITER_ROW_CHARS for c in _strip(line))


def _is_thematic_break(line: str) -> bool:
    """Whether *line* is ``---``, ``***`` or ``___`` (spaces allowed between)."""
    marks = [c for c in line if c not in " \t"]
    return len(marks) >= _MIN_BREAK_RUN and marks[0] in _BREAK_CHARS and len(set(marks)) == 1


class _Converter:
    """The line-by-line state of one conversion."""

    def __init__(self) -> None:
        self.title = ""
        self.title_taken = False
        self.sections = [SectionDraft("", TOP_LEVEL)]
        self.paragraph: list[str] = []
        self.item: str | None = None  # the open list item's marker: "bullet" or "number"
        self.table_rows = 0
        self.seen_content = False
        self.fence: str | None = None
        self.fence_quotes = 0  # the blockquote depth the open fence sits at
        self.fence_lines: list[str] = []

    def block(self, draft: BlockDraft) -> None:
        """Add *draft* to the current section."""
        self.sections[-1].blocks.append(draft)
        self.seen_content = True

    def flush(self) -> None:
        """End the paragraph (or list item) being collected."""
        if self.paragraph:
            self.block(BlockDraft.paragraph(strip_inline(" ".join(self.paragraph))))
            self.paragraph = []
        self.item = None

    def heading(self, level: int, text: str) -> None:
        """Start a section, or take the title for a leading level-1 heading."""
        self.flush()
        if level == TOP_LEVEL and not self.seen_content and not self.title_taken:
            self.title = strip_inline(text)
            self.title_taken = True
            return
        self.sections.append(SectionDraft(strip_inline(text), level))
        self.seen_content = True

    def close_fence(self) -> None:
        """End the fenced block; its inline markup is not stripped."""
        self.block(BlockDraft.paragraph("\n".join(self.fence_lines)))
        self.fence, self.fence_lines = None, []

    def line(self, line: str) -> None:
        """Read one line."""
        quotes = 0
        if self.fence is not None:
            while quotes < self.fence_quotes and _QUOTE.match(line):
                line = _QUOTE.sub("", line, count=1)
                quotes += 1
            if quotes == self.fence_quotes:
                stripped = _strip(line)
                if (
                    stripped
                    and stripped == self.fence[0] * len(stripped)
                    and len(stripped) >= len(self.fence)
                ):
                    self.close_fence()
                else:
                    self.fence_lines.append(line)
                return
            self.close_fence()  # the blockquote holding the fence ended, and the fence with it
        while _QUOTE.match(line):
            line = _QUOTE.sub("", line, count=1)
            quotes += 1
        is_table_line = _strip(line).startswith("|")
        if not is_table_line:
            self.table_rows = 0
        fence = _FENCE.match(line)
        # A backtick fence's info string may not hold a backtick (CommonMark).
        if fence and not (fence.group(1)[0] == "`" and "`" in line[fence.end() :]):
            self.flush()
            self.fence, self.fence_quotes = fence.group(1), quotes
            return
        if not normalise_whitespace(line):
            self.flush()
            return
        if atx := _ATX.match(line):
            self.heading(len(atx.group(1)), _ATX_CLOSE.sub("", atx.group(2) or ""))
            return
        if self.paragraph and self.item is None and (setext := _SETEXT.match(line)):
            text = " ".join(self.paragraph)
            self.paragraph = []
            self.heading(_SETEXT_LEVELS[setext.group(1)[0]], text)
            return
        if _is_thematic_break(line):
            self.flush()
            return
        if is_table_line:
            self.flush()
            self.table_rows += 1
            # Only the row right after a table's first is its delimiter row.
            if not (self.table_rows == 2 and _is_delimiter_row(line)):
                self.block(BlockDraft.row(_table_cells(line)))
            return
        item = _LIST_ITEM.match(line)
        if item and (
            item.group(1)
            or not self.paragraph
            or self.item == "number"
            or int(item.group(2)) == _INTERRUPTING_NUMBER
        ):
            self.flush()
            self.paragraph = [item.group(3) if item.group(1) else _strip(line)]
            self.item = "bullet" if item.group(1) else "number"
            return
        self.paragraph.append(_strip(line))

    def finish(self) -> CanonicalText:
        """End the document and lay it out."""
        if self.fence is not None:
            self.close_fence()
        self.flush()
        return assemble(
            self.title,
            self.sections,
            SourceFormat.MARKDOWN,
            f"{SourceFormat.MARKDOWN.value}-{MARKDOWN_VERSION}",
        )


def canonical_from_markdown(text: str) -> CanonicalText:
    """Convert Markdown *text* to its canonical text (the subset is in the module docstring)."""
    converter = _Converter()
    for line in split_lines(text):
        converter.line(line)
    return converter.finish()
