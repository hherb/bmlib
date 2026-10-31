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

"""``canonical_from_segmented`` and the ``split_paragraphs`` rule it shares.

The byte-exact layouts are in the shared contract
(``tests/test_canonical_text_contract.py``); these tests name the rules.
"""

from __future__ import annotations

from bmlib.fulltext import SectionSegmenter
from bmlib.fulltext.canonical import (
    BlockDraft,
    SourceFormat,
    canonical_from_segmented,
    join_soft_hyphens,
    split_paragraphs,
)
from bmlib.fulltext.models import Section, SectionType, SegmentedDocument, TextBlock


def section(kind: SectionType, title: str, content: str) -> Section:
    """A one-page section of *kind*."""
    return Section(section_type=kind, title=title, content=content, page_start=0, page_end=0)


def line(text: str, y: float, size: float = 10.0, bold: bool = False) -> TextBlock:
    """A first-page line at height *y*, 12 points tall."""
    return TextBlock(text, 0, size, "Times", bold, False, 50.0, y, 400.0, 12.0)


class TestSplitParagraphs:
    def test_blank_lines_separate_and_lines_join_with_a_space(self) -> None:
        assert split_paragraphs("a\nb\n\n \n\nc") == [
            BlockDraft.paragraph("a b"),
            BlockDraft.paragraph("c"),
        ]

    def test_text_with_no_line_is_no_paragraph(self) -> None:
        assert split_paragraphs("\n \n") == []


class TestJoinSoftHyphens:
    def test_a_soft_hyphen_before_white_space_goes_with_it(self) -> None:
        assert join_soft_hyphens("preva\u00ad lent, spe\u00ad\u3000 cific") == (
            "prevalent, specific"
        )

    def test_any_other_soft_hyphen_is_kept(self) -> None:
        assert join_soft_hyphens("mid\u00adword, end\u00ad") == "mid\u00adword, end\u00ad"

    def test_white_space_is_rusts(self) -> None:
        # U+001F is ``str.isspace()`` but not Unicode White_Space, so it ends no line.
        assert join_soft_hyphens("a\u00ad\x1fb") == "a\u00ad\x1fb"

    def test_a_hard_hyphen_is_kept(self) -> None:
        doc = canonical_from_segmented(
            SegmentedDocument(
                sections=[
                    section(SectionType.RESULTS, "Results", "dose-\nresponse, preva\u00ad\nlent")
                ]
            )
        )
        assert doc.text == "Results\n\ndose- response, prevalent"


class TestCanonicalFromSegmented:
    def test_a_detected_heading_is_kept_as_printed(self) -> None:
        doc = canonical_from_segmented(
            SegmentedDocument(sections=[section(SectionType.METHODS, "2. Methods", "We did.")])
        )
        assert doc.text == "2. Methods\n\nWe did."

    def test_the_containers_made_up_headings_are_not_the_papers(self) -> None:
        doc = canonical_from_segmented(
            SegmentedDocument(
                sections=[
                    section(SectionType.FRONT_MATTER, "Front Matter", "By A. Author"),
                    section(SectionType.UNKNOWN, "Full Text", "Body."),
                ]
            )
        )
        assert doc.text == "By A. Author\n\nBody."
        assert [s.heading_span for s in doc.sections] == [None, None]

    def test_a_subsection_follows_its_parent_one_level_deeper(self) -> None:
        parent = section(SectionType.METHODS, "Methods", "")
        parent.subsections.append(section(SectionType.METHODS, "Participants", "Adults."))
        doc = canonical_from_segmented(SegmentedDocument(sections=[parent]))
        assert [s.level for s in doc.sections] == [1, 2]

    def test_the_documents_title_is_the_title(self) -> None:
        doc = canonical_from_segmented(SegmentedDocument(title="A trial"))
        assert doc.title_span is not None
        assert doc.span_text(doc.title_span) == "A trial"

    def test_format_and_version(self) -> None:
        doc = canonical_from_segmented(SegmentedDocument())
        assert (doc.source_format, doc.converter_version) == (SourceFormat.PDF, "pdf-2")

    def test_the_segmenters_output_converts_end_to_end(self) -> None:
        blocks = [
            line("A randomised trial", 50.0, size=20.0),
            line("We randomised 120", 100.0),
            line("patients.", 112.0),
            line("Methods", 150.0, size=14.0, bold=True),
            line("Dosing was fixed.", 170.0),
            line("Follow-up was a year.", 210.0),
        ]
        doc = canonical_from_segmented(SectionSegmenter().segment_document(blocks))
        assert doc.text == (
            "A randomised trial\n\n"
            "A randomised trial\n\n"
            "We randomised 120 patients.\n\n"
            "Methods\n\n"
            "Dosing was fixed.\n\n"
            "Follow-up was a year."
        )
