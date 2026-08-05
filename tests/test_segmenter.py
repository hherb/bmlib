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
from bmlib.fulltext.segmenter import SectionSegmenter, _median_font_size

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


class TestPatternMatching:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_an_exact_heading_matches_at_full_confidence(self):
        assert self.segmenter._match_section_type("Methods") == (SectionType.METHODS, 1.0)

    def test_matching_ignores_case(self):
        assert self.segmenter._match_section_type("MATERIALS AND METHODS") == (
            SectionType.METHODS,
            1.0,
        )

    def test_leading_numbering_is_stripped(self):
        assert self.segmenter._match_section_type("3.  Results") == (SectionType.RESULTS, 1.0)

    def test_trailing_punctuation_is_stripped(self):
        assert self.segmenter._match_section_type("Discussion:") == (SectionType.DISCUSSION, 1.0)

    def test_a_multi_word_pattern_matches_partially(self):
        # Upstream compared the regex source against the heading as literal
        # text, so r"supplementary\s+materials?" could never occur in prose
        # and every multi-word pattern was dead in the fallback.
        assert self.segmenter._match_section_type("Supplementary materials online") == (
            SectionType.SUPPLEMENTARY,
            0.7,
        )

    def test_a_heading_containing_a_known_name_matches_partially(self):
        assert self.segmenter._match_section_type("Study methods overview") == (
            SectionType.METHODS,
            0.7,
        )

    def test_a_short_heading_is_not_a_partial_match(self):
        # Upstream's reverse containment ("a" in "abstract") classified a
        # heading "A" as ABSTRACT at 0.7. The reverse direction is deleted.
        assert self.segmenter._match_section_type("A") == (SectionType.UNKNOWN, 0.0)

    def test_a_partial_match_respects_word_boundaries(self):
        assert self.segmenter._match_section_type("methodsxyz") == (SectionType.UNKNOWN, 0.0)

    def test_appendices_is_an_appendix_not_supplementary(self):
        assert self.segmenter._match_section_type("Appendices") == (SectionType.APPENDIX, 1.0)


class TestSectionTypeCoverage:
    def test_every_section_type_is_produced_or_declared_reserved(self):
        # TITLE is reserved for callers, FRONT_MATTER and UNKNOWN are the
        # containers the extraction itself produces; every other member must
        # have a heading pattern, or get_section() lies for it forever.
        reserved = {SectionType.TITLE, SectionType.FRONT_MATTER, SectionType.UNKNOWN}
        assert set(SectionSegmenter.SECTION_PATTERNS) == set(SectionType) - reserved


class TestHeadingDetection:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_a_font_below_the_minimum_is_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("Methods", size=8.0), 10.0)

    def test_body_sized_text_must_be_bold(self):
        assert not self.segmenter._is_potential_header(block("Methods", size=10.0), 10.0)
        assert self.segmenter._is_potential_header(block("Methods", size=10.0, bold=True), 10.0)

    def test_a_clearly_larger_font_needs_no_bold(self):
        assert self.segmenter._is_potential_header(block("Methods", size=13.0), 10.0)

    def test_a_long_line_is_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("m" * 101, size=14.0, bold=True), 10.0)

    def test_digits_alone_are_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("123 456", size=14.0, bold=True), 10.0)


class TestMedianFontSize:
    def test_the_median_not_the_mean(self):
        blocks = [block("a", size=10.0), block("b", size=10.0), block("c", size=40.0)]
        assert _median_font_size(blocks) == 10.0

    def test_non_positive_sizes_are_ignored(self):
        blocks = [block("a", size=0.0), block("b", size=-1.0), block("c", size=10.0)]
        assert _median_font_size(blocks) == 10.0

    def test_no_usable_sizes_returns_the_default(self):
        assert _median_font_size([]) == 12.0
        assert _median_font_size([block("a", size=0.0)]) == 12.0
