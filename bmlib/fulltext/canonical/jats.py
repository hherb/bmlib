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

"""A parsed JATS article to canonical text.

Everything :meth:`JATSParser.to_html` shows, apart from the author, journal and
identifier lines (metadata held elsewhere), in the same order: the abstract,
the body with its back matter, funding, figure captions, tables as plain rows,
and references. The headings ``Abstract``, ``Funding``, ``Figures``,
``Tables`` and ``References`` are the labels ``to_html`` already prints for
those modelled fields; no other heading is invented, so prose the document
heads with nothing stays an untitled section.

The reader writes a ``[text](#id)`` link for a cross-reference into whatever
field holds it (body, abstract, captions, notes, funding), so every text read
from the article goes through :func:`strip_links`.

A figure and a table are laid out where ``to_html`` puts them, after the body:
:class:`~bmlib.fulltext.models.JATSArticle` keeps exhibits in lists of their
own, not at the point the body cites them.
"""

from __future__ import annotations

from bmlib.fulltext.canonical.model import (
    TOP_LEVEL,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
)
from bmlib.fulltext.jats_parser import _LINK_RE, _format_award
from bmlib.fulltext.models import JATSArticle, JATSBodySection

#: Bumped whenever this converter, :func:`assemble` or the JATS reader can lay
#: an article out differently. ``tests/test_renderer_version.py`` pins it
#: beside ``RENDERER_VERSION``, since a reader change can move both.
JATS_CANONICAL_VERSION = 1

#: The labels ``to_html`` prints for the modelled fields that carry no heading.
ABSTRACT_HEADING = "Abstract"
FUNDING_HEADING = "Funding"
FIGURES_HEADING = "Figures"
TABLES_HEADING = "Tables"
REFERENCES_HEADING = "References"


def strip_links(text: str) -> str:
    """*text* with each ``[text](target)`` link the reader wrote reduced to its text."""
    return _LINK_RE.sub(r"\1", text)


def canonical_from_jats(article: JATSArticle) -> CanonicalText:
    """Convert a parsed JATS *article* to its canonical text.

    Order: title; ``Abstract`` with each abstract section beneath it; the body
    sections, nested by level; ``Funding`` (statements, then awards);
    ``Figures`` and ``Tables``, each exhibit a section headed by its label;
    ``References``, one block per reference.
    """
    sections: list[SectionDraft] = []
    sub_level = TOP_LEVEL + 1

    def para(text: str) -> BlockDraft:
        return BlockDraft.paragraph(strip_links(text))

    if article.abstract_sections:
        sections.append(SectionDraft(ABSTRACT_HEADING, TOP_LEVEL))
        sections.extend(
            SectionDraft(strip_links(part.title), sub_level, [para(part.content)])
            for part in article.abstract_sections
        )
    for body in article.body_sections:
        sections.extend(_body_sections(body, TOP_LEVEL))
    funding = [para(s) for s in article.funding_statements]
    funding += [para(_format_award(a)) for a in article.funding_awards]
    if funding:
        sections.append(SectionDraft(FUNDING_HEADING, TOP_LEVEL, funding))
    if article.figures:
        sections.append(SectionDraft(FIGURES_HEADING, TOP_LEVEL))
        for figure in article.figures:
            texts = [figure.caption, *figure.footnotes]
            sections.append(
                SectionDraft(strip_links(figure.label), sub_level, [para(t) for t in texts])
            )
    if article.tables:
        sections.append(SectionDraft(TABLES_HEADING, TOP_LEVEL))
        for table in article.tables:
            rows = [
                BlockDraft.row([strip_links(c) for c in r])
                for r in (*table.header_rows, *table.body_rows)
            ]
            sections.append(
                SectionDraft(
                    strip_links(table.label),
                    sub_level,
                    [para(table.caption), *rows, *(para(f) for f in table.footnotes)],
                )
            )
    if article.references:
        sections.append(
            SectionDraft(
                REFERENCES_HEADING,
                TOP_LEVEL,
                [para(r.formatted_citation) for r in article.references],
            )
        )
    return assemble(
        strip_links(article.title),
        sections,
        SourceFormat.JATS,
        f"{SourceFormat.JATS.value}-{JATS_CANONICAL_VERSION}",
    )


def _body_sections(section: JATSBodySection, level: int) -> list[SectionDraft]:
    """*section* and its subsections, depth first, links stripped."""
    out = [
        SectionDraft(
            strip_links(section.title),
            level,
            [BlockDraft.paragraph(strip_links(p)) for p in section.paragraphs],
        )
    ]
    for sub in section.subsections:
        out.extend(_body_sections(sub, level + 1))
    return out
