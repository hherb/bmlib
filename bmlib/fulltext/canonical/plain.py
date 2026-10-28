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

"""Plain text to canonical text: one untitled section, paragraphs by blank lines."""

from __future__ import annotations

from bmlib.fulltext.canonical.model import (
    TOP_LEVEL,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
    normalise_whitespace,
)

#: Bumped whenever the plain-text converter (or :func:`assemble`) can lay a
#: text out differently.
PLAIN_TEXT_VERSION = 1


def split_lines(text: str) -> list[str]:
    """*text*'s lines, with ``\\r\\n`` and a lone ``\\r`` read as ``\\n``."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def split_paragraphs(text: str) -> list[BlockDraft]:
    """*text*'s paragraphs, separated by blank lines, each line joined by a space.

    A line holding only whitespace counts as blank, so a run of blank lines
    separates two paragraphs once and no paragraph is empty.
    """
    paragraphs: list[BlockDraft] = []
    current: list[str] = []
    for line in split_lines(text):
        if normalise_whitespace(line):
            current.append(line)
        elif current:
            paragraphs.append(BlockDraft.paragraph(" ".join(current)))
            current = []
    if current:
        paragraphs.append(BlockDraft.paragraph(" ".join(current)))
    return paragraphs


def canonical_from_plain_text(text: str, title: str = "") -> CanonicalText:
    """Convert plain *text*: paragraphs are separated by blank lines.

    A line holding only whitespace counts as blank. *title* (the paper's title,
    known from elsewhere) becomes the title when it is not blank. Everything
    else is one section, level 1, with no heading.
    """
    return assemble(
        title,
        [SectionDraft("", TOP_LEVEL, split_paragraphs(text))],
        SourceFormat.PLAIN_TEXT,
        f"{SourceFormat.PLAIN_TEXT.value}-{PLAIN_TEXT_VERSION}",
    )
