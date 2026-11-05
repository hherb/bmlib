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

import pytest

from bmlib.fulltext.canonical import canonical_from_segmented
from bmlib.fulltext.models import Section, SectionType, SegmentedDocument, TextBlock
from bmlib.fulltext.segmenter import (
    FURNITURE_MAX_EDGE_LINES,
    FURNITURE_MIN_PAGES,
    FURNITURE_PAGE_RATIO,
    PARTIAL_MATCH_CONFIDENCE,
    SectionSegmenter,
    _median_font_size,
    drop_page_furniture,
    match_section_type,
    page_furniture,
)

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
    italic: bool = False,
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
        is_italic=italic,
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

    def test_a_subsections_own_subsections_render_one_level_deeper(self):
        # The segmenter nests "2.8.1." under "2.8." under "2."; rendering one
        # level of subsections dropped the third, heading and text.
        power = Section(SectionType.METHODS, "2.8.1. Power", "Eighty per cent.", 0, 0)
        stats = Section(SectionType.METHODS, "2.8. Statistics", "Tests.", 0, 0, subsections=[power])
        md = Section(
            SectionType.METHODS, "2. Methods", "Overview.", 0, 0, subsections=[stats]
        ).to_markdown()
        assert md == (
            "## 2. Methods\n\nOverview.\n\n"
            "### 2.8. Statistics\n\nTests.\n\n"
            "#### 2.8.1. Power\n\nEighty per cent.\n"
        )

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


class TestSerialization:
    def test_a_text_block_round_trips(self):
        original = block("Methods", page=2, size=13.0, bold=True, y=88.0, height=14.0)
        assert TextBlock.from_dict(original.to_dict()) == original

    def test_a_section_round_trips_with_enum_and_subsections(self):
        sub = Section(SectionType.UNKNOWN, "Participants", "Adults.", 1, 1)
        original = Section(
            SectionType.METHODS, "Methods", "Overview.", 1, 2, confidence=0.7, subsections=[sub]
        )
        restored = Section.from_dict(original.to_dict())
        assert restored == original
        assert restored.section_type is SectionType.METHODS
        assert restored.subsections[0].section_type is SectionType.UNKNOWN

    def test_a_segmented_document_survives_json(self):
        # The round trip goes through actual JSON, so an enum or dataclass
        # leaking into to_dict() unserialised fails here, not downstream.
        import json

        original = SegmentedDocument(
            file_path="paper.pdf",
            title="A Trial",
            authors=["J Smith"],
            sections=[Section(SectionType.METHODS, "Methods", "We measured.", 0, 0)],
            metadata={"title": "A Trial"},
        )
        restored = SegmentedDocument.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored == original

    def test_from_dict_supplies_the_dataclass_defaults(self):
        section = Section.from_dict(
            {
                "section_type": "methods",
                "title": "Methods",
                "content": "",
                "page_start": 0,
                "page_end": 0,
            }
        )
        assert section.confidence == 1.0
        assert section.subsections == []
        document = SegmentedDocument.from_dict({})
        assert document.file_path == ""
        assert document.title is None
        assert document.authors == []
        assert document.sections == []
        assert document.metadata == {}


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

    def test_financial_disclosure_classifies_the_same_in_both_numbers(self):
        # FUNDING used to list the singular form while CONFLICTS listed
        # both, so "Financial disclosure" went to FUNDING and "Financial
        # disclosures" to CONFLICTS — the same heading in two sections,
        # decided by dict iteration order. CONFLICTS owns the disclosure
        # family, in both numbers.
        assert self.segmenter._match_section_type("Financial disclosure") == (
            SectionType.CONFLICTS,
            1.0,
        )
        assert self.segmenter._match_section_type("Financial disclosures") == (
            SectionType.CONFLICTS,
            1.0,
        )


class TestAVariantNameMatchesOnlyAWholeHeading:
    """#636: "Method", "Result", "Discussions" and "Experimental" are sections.

    They are matched as the whole heading at 0.7 and never searched within a
    line, so neither a body line using the word nor a bold column header
    under the body's size becomes a heading.
    """

    @pytest.mark.parametrize(
        ("heading", "section_type"),
        [
            ("2. Method", SectionType.METHODS),
            ("Experimental", SectionType.METHODS),
            ("2. Experimental Section", SectionType.METHODS),
            ("3. Result", SectionType.RESULTS),
            ("5. Discussions", SectionType.DISCUSSION),
        ],
    )
    def test_a_variant_heading_classifies_at_partial_confidence(self, heading, section_type):
        assert match_section_type(heading) == (section_type, PARTIAL_MATCH_CONFIDENCE)

    def test_a_variant_is_not_searched_for_within_a_line(self):
        assert match_section_type("Treatment may result in harm") == (SectionType.UNKNOWN, 0.0)
        # Searched, "experimental" would claim it for METHODS ahead of RESULTS.
        assert match_section_type("Experimental Results") == (SectionType.RESULTS, 0.7)

    def test_a_variant_gets_no_relaxed_size_test(self):
        segmenter = SectionSegmenter()
        exact = block("Methods", size=9.5, bold=True)
        variant = block("Method", size=9.5, bold=True)
        assert segmenter._is_potential_header(exact, 10.5)
        assert not segmenter._is_potential_header(variant, 10.5)


class TestSectionTypeCoverage:
    def test_every_section_type_is_produced_or_declared_reserved(self):
        # TITLE is reserved for callers, FRONT_MATTER and UNKNOWN are the
        # containers the extraction itself produces, and OTHER is a heading
        # naming no section; every other member must have a heading pattern,
        # or get_section() lies for it forever.
        reserved = {
            SectionType.TITLE,
            SectionType.FRONT_MATTER,
            SectionType.OTHER,
            SectionType.UNKNOWN,
        }
        assert set(SectionSegmenter.SECTION_PATTERNS) == set(SectionType) - reserved


class TestHeadingDetection:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_a_font_below_the_minimum_is_not_a_heading(self):
        # Under a 12pt body the floor is `min_heading_size`'s 10.0, so a 9pt
        # bold line is refused though bold rescues body-sized headings.
        assert not self.segmenter._is_potential_header(block("Methods", size=9.0, bold=True), 12.0)

    def test_the_floor_is_the_body_size_in_small_print(self):
        # A journal set in 8pt sets its headings in 8pt bold; the floor drops
        # to the body size, and no lower (a 7.2pt bold "Table 1" is not one).
        assert self.segmenter._is_potential_header(block("Methods", size=8.0, bold=True), 8.0)
        assert not self.segmenter._is_potential_header(block("Table 1", size=7.2, bold=True), 8.0)

    def test_body_sized_text_must_be_bold_or_italic(self):
        assert not self.segmenter._is_potential_header(block("Methods", size=10.0), 10.0)
        assert self.segmenter._is_potential_header(block("Methods", size=10.0, bold=True), 10.0)
        assert self.segmenter._is_potential_header(block("Methods", size=10.0, italic=True), 10.0)

    def test_a_clearly_larger_font_needs_no_bold(self):
        assert self.segmenter._is_potential_header(block("Methods", size=13.0), 10.0)

    def test_a_long_line_is_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("m" * 101, size=14.0, bold=True), 10.0)

    def test_digits_alone_are_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("123 456", size=14.0, bold=True), 10.0)

    def test_an_exact_section_name_just_over_the_body_needs_no_bold(self):
        # Cell: 8.97 pt headings over an 8 pt body, 1.12x, not flagged bold (#628).
        assert self.segmenter._is_potential_header(block("INTRODUCTION", size=8.97), 8.0)
        # A partial match still needs the ratio: that is how body lines became headings.
        assert not self.segmenter._is_potential_header(
            block("the GRADE approach. Results: Thirty-four", size=8.97), 8.0
        )
        # Exactly the body size is not "over" it.
        assert not self.segmenter._is_potential_header(block("Results", size=8.0), 8.0)

    def test_an_exact_bold_section_name_may_sit_just_under_the_body(self):
        # Bold 9.5 pt headings under a 10 pt body (#628), down to 0.9 of it.
        assert self.segmenter._is_potential_header(block("Methods", size=9.5, bold=True), 10.0)
        assert self.segmenter._is_potential_header(block("Methods", size=9.0, italic=True), 10.0)
        assert not self.segmenter._is_potential_header(block("Methods", size=8.9, bold=True), 10.0)
        assert not self.segmenter._is_potential_header(block("Methods", size=9.5), 10.0)
        assert not self.segmenter._is_potential_header(
            block("Methods of study", size=9.5, bold=True), 10.0
        )


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
        assert _median_font_size([block("", size=10.0)]) == 12.0

    def test_lines_count_by_their_characters(self):
        # A table's short 7 pt cells outnumber the body's lines, not its text.
        cells = [block("n = 12", size=7.0) for _ in range(10)]
        body = [block("Patients were enrolled at three sites in 2024.", size=10.0)] * 3
        assert _median_font_size(cells + body) == 10.0

    def test_an_exact_half_averages_the_sizes_either_side(self):
        assert _median_font_size([block("aaa", size=10.0), block("bbb", size=12.0)]) == 11.0


def paper_blocks() -> list[TextBlock]:
    """A miniature paper: front matter, four sections, two pages.

    Sizes matter: by characters the median is 10 (the body lines hold 122 of
    the 184 characters), so the 13-point headings clear 1.2x the median
    whether or not they are bold; the 20-point title clears every threshold
    but matches no pattern.
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


def in_face(b: TextBlock, face: str) -> TextBlock:
    b.font_name = face
    return b


class TestAHeadingFaceIsSetApartAsBoldIs:
    """#628: Frontiers sets its numbered subsections, unflagged, in its heading face."""

    def paper(self, face: str) -> list[TextBlock]:
        return [
            block("Body text of the paper.", y=0.0),
            in_face(block("1 Introduction", size=HEADING_SIZE, y=12.0), "Head"),
            block("Body text of the paper.", y=24.0),
            in_face(block("1.1 Low pH", y=36.0), face),
            block("Body text of the paper.", y=48.0),
        ]

    def test_a_numbered_line_in_the_heading_face_is_a_subsection(self):
        intro = SectionSegmenter().segment_document(self.paper("Head")).sections[-1]
        assert intro.title == "1 Introduction"
        assert [(s.title, s.section_type) for s in intro.subsections] == [
            ("1.1 Low pH", SectionType.INTRODUCTION)
        ]

    def test_the_same_line_in_the_body_face_is_body_text(self):
        intro = SectionSegmenter().segment_document(self.paper("Helvetica")).sections[-1]
        assert intro.title == "1 Introduction"
        assert intro.subsections == []
        assert "1.1 Low pH" in intro.content

    def test_a_body_line_naming_a_section_does_not_teach_the_body_face(self):
        blocks = [
            block("Body text of the paper.", y=0.0),
            block("Results", size=BODY_SIZE + 1, y=12.0),
            block("Body text of the paper.", y=24.0),
            block("2.1 months after surgery", y=36.0),
            block("Body text of the paper.", y=48.0),
        ]
        results = SectionSegmenter().segment_document(blocks).sections[-1]
        assert results.title == "Results"
        assert results.subsections == []

    def test_a_partial_match_teaches_no_face(self):
        blocks = [
            *self.paper("Head")[:3],
            in_face(block("Results of the trial", size=HEADING_SIZE, y=36.0), "Other"),
            block("Body text of the paper.", y=48.0),
            in_face(block("1.1 Low pH", y=60.0), "Other"),
            block("Body text of the paper.", y=72.0),
        ]
        results = SectionSegmenter().segment_document(blocks).sections[-1]
        assert results.title == "Results of the trial"
        assert results.subsections == []
        assert "1.1 Low pH" in results.content

    def test_a_line_naming_a_section_that_is_no_heading_teaches_no_face(self):
        blocks = [
            *self.paper("Head")[:3],
            in_face(block("Results", y=36.0), "Caption"),
            in_face(block("1.1 Low pH", y=48.0), "Caption"),
            block("Body text of the paper.", y=60.0),
        ]
        intro = SectionSegmenter().segment_document(blocks).sections[-1]
        assert intro.title == "1 Introduction"
        assert intro.subsections == []

    def test_an_exact_name_in_the_heading_face_counts_down_to_the_floor(self):
        blocks = [
            *self.paper("Head")[:3],
            in_face(block("Results", size=BODY_SIZE * 0.95, y=36.0), "Head"),
            block("Body text of the paper.", y=48.0),
            in_face(block("Discussion", size=BODY_SIZE * 0.85, y=60.0), "Head"),
            block("Body text of the paper.", y=72.0),
        ]
        doc = SectionSegmenter().segment_document(blocks)
        assert [s.title for s in doc.sections] == ["Front Matter", "1 Introduction", "Results"]
        assert "Discussion" in doc.sections[-1].content


class TestTitleExtraction:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_a_corroborated_metadata_title_wins(self):
        """Issue #56 narrowed this: the metadata title still wins, but only
        where page 1 prints it. It used to win unconditionally, which is how
        "Microsoft Word - manuscript.docx" beat the title on the page.

        The metadata title here is deliberately *not* the largest line, so the
        two paths give different answers and the assertion says which one ran.
        Naming the 20-point line — the obvious choice — made this test pass
        under a rule that rejected every metadata title, since the fallback
        returns that same string. ``paper_blocks``'s title is one line, so
        there is no wrapped title to use instead; the author line is printed
        on page 1, clears the three-word backstop, and is what the fallback
        will never return.
        """
        doc = self.segmenter.segment_document(paper_blocks(), {"title": "J Smith, R Jones"})
        assert doc.title == "J Smith, R Jones"

    def test_a_metadata_title_the_page_never_prints_is_rejected(self):
        doc = self.segmenter.segment_document(paper_blocks(), {"title": "From Metadata"})
        assert doc.title == "Aspirin and Mortality: A Trial"

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


class TestAJunkMetadataTitleDoesNotBeatTheTitleOnThePage:
    """Issue #56. Real PDFs carry filenames, "untitled" and typesetter job
    numbers in /Title, and any of them used to win outright."""

    def _blocks(self) -> list[TextBlock]:
        """A title typeset across two lines, on purpose.

        It makes the two paths give different answers, so each test below says
        which path produced its result. With the title on one line, a rule
        that rejected every metadata title would pass the first test by
        accident.

        Sizes matter here as they do in ``paper_blocks``: the body lines hold
        140 of the 169 characters, so the median is 10 and the 20-point title
        clears ``_TITLE_SIZE_RATIO``. Were the title lines to hold half the
        characters, the median would rise toward 20 and the fallback would
        reject its own title.
        """
        return [
            block("Effects of aspirin", size=TITLE_SIZE, y=72.0),
            block("on outcomes", size=TITLE_SIZE, y=100.0),
            block("Jane Smith, John Doe", y=130.0),
            block("We studied aspirin in 400 adults.", y=150.0),
            block("Mortality was the primary outcome.", y=170.0),
            block("Follow-up ran for two years.", y=190.0),
            block("The trial was registered.", y=210.0),
        ]

    def test_junk_falls_through_to_the_font_candidate(self):
        doc = SectionSegmenter().segment_document(
            self._blocks(), {"title": "Microsoft Word - ms.docx"}
        )
        assert doc.title == "Effects of aspirin"

    def test_a_corroborated_title_still_wins(self):
        """The negative control. The metadata title spans both title lines, so
        only the metadata path can return it — a rule that rejected everything
        would fail here while still passing the test above."""
        doc = SectionSegmenter().segment_document(
            self._blocks(), {"title": "Effects of aspirin on outcomes"}
        )
        assert doc.title == "Effects of aspirin on outcomes"

    def test_junk_with_no_font_candidate_yields_no_title(self):
        """Rejection must not invent a title out of an ordinary body line."""
        flat = [block("We studied aspirin in 400 adults.", y=72.0)]
        doc = SectionSegmenter().segment_document(flat, {"title": "Microsoft Word - ms.docx"})
        assert doc.title is None

    def test_a_scanned_page_with_no_text_keeps_its_metadata_title(self):
        """No page-1 blocks at all: corroboration cannot be run, so the
        metadata title is all there is. Rejecting would leave every image-only
        scan with no title rather than an unverified one."""
        doc = SectionSegmenter().segment_document(
            [block("Late text", page=2)], {"title": "Effects of aspirin on outcomes"}
        )
        assert doc.title == "Effects of aspirin on outcomes"


_BODY = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]


def paged(pages: int, head_on: int | None = None) -> list[TextBlock]:
    """*pages* pages of two body lines and a page number, the first *head_on*
    (default: all) under a running head."""
    blocks: list[TextBlock] = []
    for page in range(pages):
        if page < (pages if head_on is None else head_on):
            blocks.append(block("Smith et al. A trial 2024", page=page, y=30.0))
        blocks.append(block(f"{_BODY[page]} reports", page=page, y=100.0))
        blocks.append(block(f"{_BODY[page]} replies", page=page, y=114.0))
        blocks.append(block(str(page + 101), page=page, y=760.0))
    return blocks


class TestPageFurniture:
    def test_a_running_head_and_a_page_number_are_furniture(self):
        blocks = paged(4)
        assert {blocks[i].text for i in page_furniture(blocks)} == {
            "Smith et al. A trial 2024",
            "101",
            "102",
            "103",
            "104",
        }

    def test_digits_aside_the_text_must_agree(self):
        # A run of digits is one ``#``, so page 9 and page 10 agree.
        blocks = [block(f"Page {p + 8} of 11", page=p, y=760.0) for p in range(4)]
        assert page_furniture(blocks) == {0, 1, 2, 3}

    def test_the_height_must_agree_within_the_tolerance(self):
        moved = [block("Running head", page=p, y=30.0 + 1.5 * p) for p in range(4)]
        assert page_furniture(moved) == {0, 1, 2, 3}  # each within 2 pt of the last
        apart = [block("Running head", page=p, y=30.0 + 10 * p) for p in range(4)]
        assert page_furniture(apart) == set()

    def test_the_share_of_pages_decides(self):
        assert (FURNITURE_PAGE_RATIO, FURNITURE_MIN_PAGES) == (0.4, 3)

        def heads(blocks: list[TextBlock]) -> int:
            return sum(blocks[i].text.startswith("Smith") for i in page_furniture(blocks))

        assert heads(paged(8, head_on=4)) == 4  # ceil(3.2) = 4 of 8
        assert heads(paged(8, head_on=3)) == 0
        # Two pages are never enough, whatever the share.
        assert page_furniture(paged(2)) == set()

    def test_a_table_repeating_on_one_grid_is_kept_between_its_page_text(self):
        # PR review: "0.12" and "3.4" both read "#.#", so a table continued on
        # the same grid over pages is one run per row; between the page's prose
        # it is not at an edge.
        blocks = paged(6)
        for page in range(6):
            blocks.append(block("Characteristic n (%) OR (95% CI)", page=page, y=200.0))
            for row in range(5):
                blocks.append(
                    block(f"{row}.{page}{row} ({page + 10}%)", page=page, y=220.0 + 14 * row)
                )
            blocks.append(block(f"{_BODY[page]} concludes", page=page, y=400.0))
        assert {blocks[i].text for i in page_furniture(blocks)} == {
            "Smith et al. A trial 2024",
            *(str(p + 101) for p in range(6)),
        }

    def test_too_many_lines_at_one_edge_are_the_pages_content(self):
        assert FURNITURE_MAX_EDGE_LINES == 20

        def rows(n: int) -> list[TextBlock]:
            return [
                block(f"{r}.{p} {r + p}", page=p, y=40.0 + 14 * r)
                for p in range(4)
                for r in range(n)
            ] + [block(f"{_BODY[p]} below", page=p, y=700.0) for p in range(4)]

        assert len(page_furniture(rows(FURNITURE_MAX_EDGE_LINES))) == 4 * FURNITURE_MAX_EDGE_LINES
        assert page_furniture(rows(FURNITURE_MAX_EDGE_LINES + 1)) == set()

    def test_a_line_with_no_finite_height_is_never_furniture(self):
        blocks = paged(4)
        blocks[0] = block("Smith et al. A trial 2024", page=0, y=float("nan"))
        furniture = page_furniture(blocks)
        assert (
            0 not in furniture and sum(blocks[i].text.startswith("Smith") for i in furniture) == 3
        )

    def test_line_numbers_are_not_page_numbers(self):
        # A manuscript numbers its lines, restarting or running on: either way
        # the number at one height is not its page's index plus a constant.
        restarting = [
            block(str(j + 1), page=p, y=100.0 + 14 * j) for p in range(4) for j in range(3)
        ]
        running = [
            block(str(3 * p + j + 1), page=p, y=100.0 + 14 * j) for p in range(4) for j in range(3)
        ]
        assert page_furniture(restarting) == set()
        assert page_furniture(running) == set()

    def test_a_number_and_a_literal_hash_at_one_height_are_not_page_numbers(self):
        blocks = [block("#" if p == 0 else str(p + 1), page=p, y=760.0) for p in range(4)]
        assert page_furniture(blocks) == set()

    def test_the_segmenter_sets_furniture_aside_and_rejoins_the_paragraph(self):
        blocks = paged(3)
        # A sentence crossing from page 0 to page 1: the page number and the
        # next page's running head used to sit inside it.
        blocks[2] = block("The trial enrolled", page=0, y=114.0)
        blocks[5] = block("patients for a year.", page=1, y=100.0)
        document = SectionSegmenter().segment_document(blocks)
        content = "\n".join(s.content for s in document.sections)
        assert "Smith et al." not in content and "101" not in content
        assert "The trial enrolled\npatients for a year." in content
        assert drop_page_furniture(blocks) == [b for b in blocks if b.y in (100.0, 114.0)]


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


class TestAnUnnamedNumberedParentIsKeptForItsChildren:
    """#628: "2 Characteristics of hypoxia" names no section, and dropping it
    dropped every subsection numbered from it (PMC9798000 lost 19)."""

    def paper(self, *headings: str) -> list[TextBlock]:
        blocks = [block("Body text of the paper.", y=0.0)]
        for title in ("1. Introduction", *headings):
            blocks.append(block(title, bold=True, y=blocks[-1].y + 12.0))
            blocks.append(block(f"Body under {title}.", y=blocks[-1].y + 12.0))
        return blocks

    def tree(self, blocks: list[TextBlock]) -> list[tuple]:
        def walk(sections: list[Section]) -> list[tuple]:
            return [(s.title, s.section_type, walk(s.subsections)) for s in sections]

        return walk(SectionSegmenter().segment_document(blocks).sections[1:])

    def test_the_parent_and_its_unnamed_children_are_other(self):
        blocks = self.paper("2. Characteristics of hypoxia", "2.1. Low pH", "2.2. Methods")
        assert self.tree(blocks) == [
            ("1. Introduction", SectionType.INTRODUCTION, []),
            (
                "2. Characteristics of hypoxia",
                SectionType.OTHER,
                [
                    ("2.1. Low pH", SectionType.OTHER, []),
                    ("2.2. Methods", SectionType.METHODS, []),
                ],
            ),
        ]
        parent = SectionSegmenter().segment_document(blocks).sections[2]
        assert parent.content == "Body under 2. Characteristics of hypoxia."
        assert (parent.confidence, parent.subsections[0].confidence) == (0.0, 0.0)

    def test_a_named_first_child_keeps_the_parent(self):
        blocks = self.paper("2. Characteristics of hypoxia", "2.1. Methods")
        assert self.tree(blocks)[1:] == [
            (
                "2. Characteristics of hypoxia",
                SectionType.OTHER,
                [("2.1. Methods", SectionType.METHODS, [])],
            ),
        ]

    def test_a_parent_no_heading_numbers_from_is_text(self):
        blocks = self.paper("2. Characteristics of hypoxia", "3. Results", "3.1. Low pH")
        assert self.tree(blocks) == [
            ("1. Introduction", SectionType.INTRODUCTION, []),
            ("3. Results", SectionType.RESULTS, [("3.1. Low pH", SectionType.RESULTS, [])]),
        ]
        # The dropped parent stays where main put it: text of the section before.
        intro = SectionSegmenter().segment_document(blocks).sections[1]
        assert "2. Characteristics of hypoxia" in intro.content

    def test_a_subsection_of_another_number_does_not_keep_it(self):
        blocks = self.paper("2. Characteristics of hypoxia", "3.1. Methods")
        # A named subsection nests under any shallower heading, as on main.
        assert self.tree(blocks) == [
            (
                "1. Introduction",
                SectionType.INTRODUCTION,
                [("3.1. Methods", SectionType.METHODS, [])],
            ),
        ]

    def test_an_unnamed_subsection_is_not_held(self):
        # Only a top-level heading is held: "3.1." does not become a parent.
        blocks = self.paper("2. Methods", "3.1. Orphan unnamed", "3.1.1. Deeper unnamed")
        assert self.tree(blocks)[1:] == [("2. Methods", SectionType.METHODS, [])]

    def test_a_dropped_line_between_keeps_the_hold(self):
        # Only a marker ends the hold; an unnamed unnumbered bold line is text.
        blocks = self.paper("2. Characteristics of hypoxia", "Low oxygen tension", "2.1. Low pH")
        assert self.tree(blocks)[1:] == [
            (
                "2. Characteristics of hypoxia",
                SectionType.OTHER,
                [("2.1. Low pH", SectionType.OTHER, [])],
            ),
        ]

    def test_the_later_of_two_held_headings_is_kept(self):
        blocks = self.paper("2. Characteristics of hypoxia", "3. Something unnamed", "3.1. Low pH")
        assert self.tree(blocks)[1:] == [
            ("3. Something unnamed", SectionType.OTHER, [("3.1. Low pH", SectionType.OTHER, [])]),
        ]

    def test_a_wrapped_parent_keeps_its_whole_title(self):
        blocks = [
            block("Body text of the paper.", y=0.0),
            block("1. Introduction", bold=True, y=12.0),
            block("Body under 1. Introduction.", y=24.0),
            block("2. Characteristics of", bold=True, y=36.0),
            block("hypoxia in tumours", bold=True, y=48.0),
            block("Parent body.", y=60.0),
            block("2.1. Low pH", bold=True, y=72.0),
            block("Child body.", y=84.0),
        ]
        parent = SectionSegmenter().segment_document(blocks).sections[2]
        assert (parent.title, parent.section_type, parent.content) == (
            "2. Characteristics of hypoxia in tumours",
            SectionType.OTHER,
            "Parent body.",
        )

    def test_the_repeated_number_guard_holds_under_a_kept_parent(self):
        blocks = self.paper(
            "2. Characteristics of hypoxia", "2.1. Low pH", "2 Theta degrees", "2.2. High pH"
        )
        assert self.tree(blocks)[1:] == [
            (
                "2. Characteristics of hypoxia",
                SectionType.OTHER,
                [
                    ("2.1. Low pH", SectionType.OTHER, []),
                    ("2.2. High pH", SectionType.OTHER, []),
                ],
            ),
        ]

    def test_the_canonical_text_prints_other_headings(self):
        # Why OTHER is a member of its own: UNKNOWN's heading is not printed.
        blocks = self.paper("2. Characteristics of hypoxia", "2.1. Low pH")
        text = canonical_from_segmented(SectionSegmenter().segment_document(blocks)).text
        assert "\n\n2. Characteristics of hypoxia\n\nBody under 2. Characteristics" in text
        assert "\n\n2.1. Low pH\n\nBody under 2.1. Low pH." in text

    def test_a_marker_between_parent_and_child_lets_the_parent_go(self):
        # "2.1." is under "Results" now, not the unnamed "2." two headings up.
        blocks = self.paper("2. Characteristics of hypoxia", "Results", "2.1. Low pH")
        assert self.tree(blocks)[1:] == [
            ("Results", SectionType.RESULTS, [("2.1. Low pH", SectionType.RESULTS, [])]),
        ]

    def test_a_parent_repeating_the_open_number_is_text(self):
        # A figure's "2θ degrees" axis label, read as "2 Theta degrees".
        blocks = self.paper("2. Results", "2.1. Imaging", "2 Theta degrees", "2.2. Magnetism")
        assert self.tree(blocks)[1:] == [
            (
                "2. Results",
                SectionType.RESULTS,
                [
                    ("2.1. Imaging", SectionType.RESULTS, []),
                    ("2.2. Magnetism", SectionType.RESULTS, []),
                ],
            ),
        ]

    def test_an_unnumbered_unnamed_line_is_not_held(self):
        # An unnumbered parent contradicts no number, so held it would take
        # every subsection after it.
        blocks = self.paper("Characteristics of hypoxia", "1.1. Low pH")
        assert self.tree(blocks) == [
            (
                "1. Introduction",
                SectionType.INTRODUCTION,
                [("1.1. Low pH", SectionType.INTRODUCTION, [])],
            ),
        ]

    def test_a_top_level_heading_with_the_same_number_is_no_child(self):
        # "2. Results" opens with "2." too, but it is the parent's peer.
        blocks = self.paper("2 Theta degrees", "2. Results", "2.1. Imaging")
        assert self.tree(blocks)[1:] == [
            ("2. Results", SectionType.RESULTS, [("2.1. Imaging", SectionType.RESULTS, [])]),
        ]
