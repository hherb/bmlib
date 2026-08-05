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

"""Behaviour tests for PDF section segmentation.

The segmenter consumes ``TextBlock``s, so everything here runs without
PyMuPDF — blocks are built directly. The extraction half (PDF → blocks)
is tested in ``tests/test_pdf_converter.py``.
"""

from __future__ import annotations

from bmlib.fulltext.models import Section, SectionType, SegmentedDocument, TextBlock

# Font sizes used throughout: body text, a section heading, the paper title.
BODY_SIZE = 10.0
HEADING_SIZE = 13.0
TITLE_SIZE = 20.0


def block(
    text: str,
    *,
    page: int = 0,
    size: float = BODY_SIZE,
    bold: bool = False,
    y: float = 0.0,
    height: float = 12.0,
) -> TextBlock:
    """A TextBlock with the segmentation-relevant fields settable, rest defaulted."""
    return TextBlock(
        text=text,
        page_num=page,
        font_size=size,
        font_name="Helvetica",
        is_bold=bold,
        is_italic=False,
        x=72.0,
        y=y,
        width=400.0,
        height=height,
    )


class TestSectionModels:
    def test_get_section_returns_the_first_match_or_none(self):
        methods = Section(SectionType.METHODS, "Methods", "how", 0, 1)
        doc = SegmentedDocument(sections=[methods])
        assert doc.get_section(SectionType.METHODS) is methods
        assert doc.get_section(SectionType.RESULTS) is None

    def test_section_renders_at_heading_level_two(self):
        md = Section(SectionType.METHODS, "Methods", "We measured.", 0, 0).to_markdown()
        assert md.startswith("## Methods")
        assert "We measured." in md

    def test_a_title_section_renders_at_heading_level_one(self):
        md = Section(SectionType.TITLE, "A Trial", "", 0, 0).to_markdown()
        assert md.startswith("# A Trial")

    def test_subsections_render_at_heading_level_three(self):
        sub = Section(SectionType.UNKNOWN, "Participants", "Adults.", 0, 0)
        md = Section(
            SectionType.METHODS, "Methods", "Overview.", 0, 0, subsections=[sub]
        ).to_markdown()
        assert "### Participants" in md
        assert "Adults." in md

    def test_document_markdown_carries_title_authors_and_sections(self):
        doc = SegmentedDocument(
            title="A Trial",
            authors=["J Smith", "R Jones"],
            sections=[Section(SectionType.METHODS, "Methods", "We measured.", 0, 0)],
        )
        md = doc.to_markdown()
        assert "# A Trial" in md
        assert "**Authors:** J Smith, R Jones" in md
        assert "## Methods" in md

    def test_document_markdown_without_title_or_authors_has_neither_line(self):
        md = SegmentedDocument(
            sections=[Section(SectionType.METHODS, "Methods", "x", 0, 0)]
        ).to_markdown()
        assert not md.startswith("# ")
        assert "**Authors:**" not in md

    def test_str_summarises_rather_than_dumping_content(self):
        section = Section(SectionType.METHODS, "Methods", "x" * 5000, 0, 3)
        assert len(str(section)) < 200
