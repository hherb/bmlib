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

"""A segmented PDF to canonical text: the segmenter's sections, as extracted.

The input is :meth:`bmlib.fulltext.SectionSegmenter.segment_document`'s
output, so this module reads no PDF itself and needs no PDF library. The text
is taken as the extraction gives it: a table's cells may run together, and a
hyphen at a line break is kept, the lines joined by a space (``dose- response``),
since ``dose-`` / ``response`` must keep it and nothing in a PDF tells it from
``random-`` / ``ised``. A **soft** hyphen (U+00AD) is the exception, because it
says so: one followed by white space inside a paragraph is removed with that
space, which is the line break it ended (``preva\u00ad`` / ``lent`` gives
``prevalent``). Only a PDF that maps its line-end hyphens to U+00AD carries
one; most typeset an ordinary ``-``.
"""

from __future__ import annotations

from bmlib.fulltext.canonical.model import (
    TOP_LEVEL,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
    is_white_space,
)
from bmlib.fulltext.canonical.plain import split_paragraphs
from bmlib.fulltext.models import Section, SectionType, SegmentedDocument

#: Bumped whenever this converter, :func:`assemble`, or the segmenter and
#: extraction feeding it can lay a PDF's text out differently.
PDF_CANONICAL_VERSION = 2

#: The segmenter's containers, whose headings ("Front Matter", "Full Text")
#: bmlib makes up rather than reads from the page.
_UNPRINTED_HEADINGS = frozenset({SectionType.FRONT_MATTER, SectionType.UNKNOWN})

SOFT_HYPHEN = "\u00ad"


def join_soft_hyphens(text: str) -> str:
    """*text* with each soft hyphen followed by white space removed, with that space.

    A paragraph's lines are joined by a space, so a word the PDF broke at a soft
    hyphen arrives as ``preva\u00ad lent``; this gives ``prevalent``. A soft
    hyphen anywhere else is kept. White space is :func:`is_white_space`'s, as
    Rust decides it.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == SOFT_HYPHEN and i + 1 < len(text) and is_white_space(text[i + 1]):
            i += 1
            while i < len(text) and is_white_space(text[i]):
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def canonical_from_segmented(document: SegmentedDocument) -> CanonicalText:
    """Convert a segmented PDF to its canonical text.

    The title is ``document.title`` (pass a known title to
    ``segment_document()`` as ``metadata["title"]``, which checks it against
    page 1). Each section becomes a section at :data:`TOP_LEVEL` and each of its
    ``subsections`` follows it one level deeper. A section's heading is its
    ``title``, except that front matter and the no-headings fallback are
    untitled, since their headings are not the paper's. That is keyed on
    ``section_type``, so any ``FRONT_MATTER`` or ``UNKNOWN`` section is untitled,
    a hand-built one too; the segmenter makes ``UNKNOWN`` only for the
    fallback. Paragraphs are the content's blank-line-separated runs
    (:func:`split_paragraphs`), each with :func:`join_soft_hyphens` applied.
    """
    drafts: list[SectionDraft] = []

    def add(section: Section, level: int) -> None:
        heading = "" if section.section_type in _UNPRINTED_HEADINGS else section.title
        paragraphs = [
            BlockDraft.paragraph(join_soft_hyphens(p.text))
            for p in split_paragraphs(section.content)
        ]
        drafts.append(SectionDraft(heading, level, paragraphs))
        for subsection in section.subsections:
            add(subsection, level + 1)

    for section in document.sections:
        add(section, TOP_LEVEL)
    return assemble(
        document.title or "",
        drafts,
        SourceFormat.PDF,
        f"{SourceFormat.PDF.value}-{PDF_CANONICAL_VERSION}",
    )
