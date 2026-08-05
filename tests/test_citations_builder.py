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

"""End-to-end tests for the reference builder."""

from __future__ import annotations

from bmlib.citations.builder import build_references, find_missing_documents, format_document
from bmlib.citations.models import CitationStyle, DocumentMetadata


def _metadata_map() -> dict[int, DocumentMetadata]:
    return {
        1: DocumentMetadata(
            document_id=1,
            title="First paper",
            authors=["John Smith"],
            journal="J One",
            year=2021,
            doi="10.1/one",
        ),
        2: DocumentMetadata(
            document_id=2,
            title="Second paper",
            authors=["Jane Doe", "Bob Roe"],
            journal="J Two",
            year=2022,
        ),
        3: DocumentMetadata(
            document_id=3,
            title="Third paper",
            authors=["Ann Poe"],
            journal="J Three",
            year=2023,
        ),
    }


class TestBuildReferencesVancouver:
    def test_numbers_follow_first_appearance(self):
        text = "B [@id:2:Doe2022] then A [@id:1:Smith2021] then B again [@id:2:Doe2022]."
        formatted, references = build_references(text, _metadata_map())
        assert formatted == "B [1] then A [2] then B again [1]."
        assert [(r.number, r.document_id) for r in references] == [(1, 2), (2, 1)]

    def test_references_are_formatted_in_style(self):
        _, references = build_references("See [@id:1:Smith2021].", _metadata_map())
        assert references[0].formatted_text == (
            "1. Smith J. First paper. *J One*. 2021. doi:10.1/one"
        )
        assert references[0].metadata == _metadata_map()[1]

    def test_adjacent_markers_combine_to_a_range(self):
        text = "Claim [@id:1:A] [@id:2:B] [@id:3:C]."
        formatted, _ = build_references(text, _metadata_map())
        assert formatted == "Claim [1-3]."

    def test_combining_can_be_disabled(self):
        text = "Claim [@id:1:A] [@id:2:B] [@id:3:C]."
        formatted, _ = build_references(text, _metadata_map(), combine_sequential=False)
        assert formatted == "Claim [1,2,3]."

    def test_a_missing_document_gets_a_visible_placeholder(self):
        text = "Known [@id:1:A] unknown [@id:99:Z]."
        formatted, references = build_references(text, _metadata_map())
        assert formatted == "Known [1] unknown [2]."
        assert references[1].formatted_text == "2. [Document 99 not found]"
        assert references[1].metadata is None

    def test_no_citations_returns_the_text_unchanged(self):
        assert build_references("plain prose", _metadata_map()) == ("plain prose", [])


class TestAuthorDateStyles:
    def test_author_date_styles_get_author_date_inline_citations(self):
        # Regression: upstream replaced markers with [N] in every style, so
        # an APA document read "[3]" against an unnumbered reference list.
        text = "As shown [@id:2:Doe2022]."
        formatted, _ = build_references(text, _metadata_map(), style=CitationStyle.APA)
        assert formatted == "As shown (Doe & Roe, 2022)."

    def test_harvard_and_chicago_inline_shapes(self):
        text = "As shown [@id:2:Doe2022]."
        harvard, _ = build_references(text, _metadata_map(), style=CitationStyle.HARVARD)
        assert harvard == "As shown (Doe and Roe, 2022)."
        chicago, _ = build_references(text, _metadata_map(), style=CitationStyle.CHICAGO)
        assert chicago == "As shown (Doe and Roe 2022)."

    def test_a_marker_without_metadata_stays_verbatim(self):
        text = "Mystery [@id:99:Z]."
        formatted, references = build_references(text, _metadata_map(), style=CitationStyle.APA)
        assert formatted == text
        assert references[0].formatted_text == "1. [Document 99 not found]"


class TestFormatDocument:
    def test_appends_the_reference_list(self):
        out = format_document("See [@id:1:A].", _metadata_map())
        assert out.startswith("See [1].")
        assert "## References" in out
        assert "1. Smith J. First paper. *J One*. 2021. doi:10.1/one" in out

    def test_the_reference_list_can_be_suppressed(self):
        out = format_document("See [@id:1:A].", _metadata_map(), include_reference_list=False)
        assert out == "See [1]."

    def test_a_document_with_no_citations_gains_no_reference_list(self):
        assert format_document("plain prose", _metadata_map()) == "plain prose"


class TestFindMissingDocuments:
    def test_missing_ids_are_reported_per_marker(self):
        text = "[@id:1:A] [@id:99:Z] and again [@id:99:Z]"
        missing = find_missing_documents(text, _metadata_map())
        assert [c.document_id for c in missing] == [99, 99]

    def test_nothing_missing_is_empty(self):
        assert find_missing_documents("[@id:1:A]", _metadata_map()) == []
