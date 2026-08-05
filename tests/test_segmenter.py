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

    def test_authors_curly_apostrophe_contributions_matches(self):
        # U+2019 (’), not ASCII '\''  — what InDesign/Word actually produce.
        # This is the regression guard: a plain-ASCII test would keep
        # passing without the fix.
        assert self.segmenter._match_section_type("Authors’ contributions") == (
            SectionType.AUTHOR_CONTRIBUTIONS,
            1.0,
        )

    def test_authors_singular_possessive_contributions_matches(self):
        assert self.segmenter._match_section_type("Author's contributions") == (
            SectionType.AUTHOR_CONTRIBUTIONS,
            1.0,
        )


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
        # 9pt clears the ratio rule against this median without needing
        # bold (6.0 * 1.2 = 7.2 < 9.0), so only the absolute floor — 10.0,
        # `min_heading_size`'s default — can be what rejects it.
        assert not self.segmenter._is_potential_header(block("Methods", size=9.0), 6.0)

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


def paper_blocks() -> list[TextBlock]:
    """A miniature paper: front matter, four sections, two pages.

    Sizes matter: the median is 11.5 (five body lines at 10, four headings
    at 13, one title at 20), so 13-point headings are below 1.2x the median
    and need their bold; the 20-point title clears every threshold but
    matches no pattern.
    """
    return [
        block("Aspirin and Mortality: A Trial", size=TITLE_SIZE),
        block("J Smith, R Jones"),
        block("Abstract", size=HEADING_SIZE, bold=True),
        block("We tested aspirin against placebo."),
        block("Methods", size=HEADING_SIZE, bold=True),
        block("Participants were randomised by coin toss."),
        block("Results", size=HEADING_SIZE, bold=True),
        block("Mortality fell.", page=1),
        block("References", size=HEADING_SIZE, bold=True, page=1),
        block("1. Prior trial.", page=1),
    ]


class TestSectionExtraction:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_a_paper_segments_into_its_sections(self):
        doc = self.segmenter.segment_document(paper_blocks())
        assert [s.section_type for s in doc.sections] == [
            SectionType.FRONT_MATTER,
            SectionType.ABSTRACT,
            SectionType.METHODS,
            SectionType.RESULTS,
            SectionType.REFERENCES,
        ]
        methods = doc.get_section(SectionType.METHODS)
        assert methods is not None
        assert methods.content == "Participants were randomised by coin toss."
        assert methods.confidence == 1.0

    def test_front_matter_is_kept(self):
        # Upstream dropped everything before the first detected heading —
        # title, authors, an abstract whose heading was missed — silently.
        doc = self.segmenter.segment_document(paper_blocks())
        front = doc.sections[0]
        assert front.section_type is SectionType.FRONT_MATTER
        assert front.content == "Aspirin and Mortality: A Trial\nJ Smith, R Jones"
        assert front.confidence == 0.5

    def test_a_heading_with_no_body_is_still_reported(self):
        # Upstream skipped a marker whose slice was empty, discarding the
        # heading with it — two adjacent headings lost the first entirely.
        blocks = [
            block("Methods", size=HEADING_SIZE, bold=True),
            block("Results", size=HEADING_SIZE, bold=True),
            block("Mortality fell."),
        ]
        doc = self.segmenter.segment_document(blocks)
        methods = doc.get_section(SectionType.METHODS)
        assert methods is not None
        assert methods.content == ""
        assert methods.page_start == 0 and methods.page_end == 0

    def test_no_headings_returns_one_unknown_section(self):
        doc = self.segmenter.segment_document([block("Just prose."), block("More prose.")])
        assert [s.section_type for s in doc.sections] == [SectionType.UNKNOWN]
        fallback = doc.sections[0]
        assert fallback.title == "Full Text"
        assert fallback.confidence == 0.5
        assert "Just prose." in fallback.content
        assert "More prose." in fallback.content

    def test_no_blocks_returns_no_sections(self):
        doc = self.segmenter.segment_document([])
        assert doc.sections == []
        assert doc.title is None

    def test_a_vertical_gap_becomes_a_paragraph_break(self):
        blocks = [
            block("Methods", size=HEADING_SIZE, bold=True, y=100.0),
            block("First paragraph.", y=120.0, height=12.0),
            # Gap: 160 - (120 + 12) = 28 > 12 * 1.5 — a paragraph boundary.
            block("Second paragraph.", y=160.0, height=12.0),
        ]
        doc = self.segmenter.segment_document(blocks)
        assert doc.sections[-1].content == "First paragraph.\n\nSecond paragraph."

    def test_a_page_boundary_is_not_a_paragraph_break(self):
        # The next page starts higher on the canvas, so the gap is negative;
        # a paragraph continuing across the page break stays one paragraph.
        blocks = [
            block("Methods", size=HEADING_SIZE, bold=True, y=100.0),
            block("wrapped line one", y=700.0, height=12.0),
            block("continues at the top of the next page", page=1, y=72.0, height=12.0),
        ]
        doc = self.segmenter.segment_document(blocks)
        assert doc.sections[-1].content == (
            "wrapped line one\ncontinues at the top of the next page"
        )

    def test_metadata_is_optional(self):
        doc = self.segmenter.segment_document([block("Just prose.")])
        assert doc.file_path == ""
        assert doc.metadata == {}


class TestTitleExtraction:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_the_metadata_title_wins(self):
        doc = self.segmenter.segment_document(paper_blocks(), {"title": "From Metadata"})
        assert doc.title == "From Metadata"

    def test_the_largest_first_page_line_is_the_fallback_title(self):
        doc = self.segmenter.segment_document(paper_blocks())
        assert doc.title == "Aspirin and Mortality: A Trial"

    def test_a_title_must_clear_the_median_by_half_again(self):
        doc = self.segmenter.segment_document([block("Modest line"), block("Body text.")])
        assert doc.title is None

    def test_no_first_page_blocks_means_no_title(self):
        doc = self.segmenter.segment_document([block("Late text", page=2)])
        assert doc.title is None

    def test_file_path_comes_from_metadata(self):
        doc = self.segmenter.segment_document(paper_blocks(), {"file_path": "paper.pdf"})
        assert doc.file_path == "paper.pdf"

    def test_an_explicit_none_file_path_does_not_become_the_string_none(self):
        doc = self.segmenter.segment_document(paper_blocks(), {"file_path": None})
        assert doc.file_path == ""


class TestPublicExports:
    def test_segmenter_types_are_importable_and_exported(self):
        import bmlib.fulltext as fulltext

        names = ["SectionSegmenter", "Section", "SectionType", "SegmentedDocument", "TextBlock"]
        for name in names:
            assert hasattr(fulltext, name), f"{name} not importable from bmlib.fulltext"
            assert name in fulltext.__all__, f"{name} missing from bmlib.fulltext.__all__"

        # LayoutExtractor lives in pdf_converter but is re-exported at the
        # package level alongside the segmentation types.
        assert hasattr(fulltext, "LayoutExtractor")
        assert "LayoutExtractor" in fulltext.__all__
