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

"""Tests for bmlib.citations models and the citation-marker parser."""

from __future__ import annotations

from bmlib.citations.models import (
    DEFAULT_CITATION_STYLE,
    Citation,
    CitationStyle,
    DocumentMetadata,
    FormattedReference,
    author_surname,
)
from bmlib.citations.parser import (
    citation_positions,
    citations_in_range,
    count_citations,
    count_unique_citations,
    create_citation_marker,
    extract_document_id_from_citation,
    extract_label_from_citation,
    find_adjacent_citations,
    format_citation_group,
    parse_citations,
    replace_all_citations_with_numbers,
    replace_citation_with_number,
    unique_document_ids,
    validate_citation_marker,
)


class TestCitationStyle:
    def test_the_default_style_is_vancouver(self):
        assert DEFAULT_CITATION_STYLE is CitationStyle.VANCOUVER

    def test_styles_round_trip_by_value(self):
        assert CitationStyle("apa") is CitationStyle.APA


class TestCitationModel:
    def test_two_citations_of_one_document_at_different_positions_differ(self):
        # Upstream compared Citations equal by document_id alone, so a set()
        # collapsed distinct markers. Nothing ported relies on that; value
        # equality over all fields is the unsurprising contract.
        first = Citation(document_id=1, label="A", position=0, text="[@id:1:A]")
        second = Citation(document_id=1, label="A", position=10, text="[@id:1:A]")
        assert first != second

    def test_round_trip_via_to_dict(self):
        citation = Citation(document_id=7, label="Smith2023", position=4, text="[@id:7:Smith2023]")
        assert Citation.from_dict(citation.to_dict()) == citation


class TestAuthorSurname:
    def test_surname_from_an_inverted_name(self):
        assert author_surname("van der Berg, Jan") == "van der Berg"

    def test_surname_from_a_natural_name_is_the_last_word(self):
        # Upstream-faithful naive split: particles are lost in this format.
        assert author_surname("Jan van der Berg") == "Berg"

    def test_an_empty_name_is_unknown(self):
        assert author_surname("  ") == "Unknown"


class TestDocumentMetadata:
    def test_semicolon_separated_inverted_names_survive_from_dict(self):
        # Upstream replaced ';' with ',' and split on ',', shattering
        # "Smith, John; Doe, Jane" into four author fragments.
        metadata = DocumentMetadata.from_dict(
            {"id": 1, "title": "T", "authors": "Smith, John; Doe, Jane"}
        )
        assert metadata.authors == ["Smith, John", "Doe, Jane"]

    def test_a_comma_separated_author_string_still_splits(self):
        metadata = DocumentMetadata.from_dict(
            {"id": 1, "title": "T", "authors": "John Smith, Jane Doe"}
        )
        assert metadata.authors == ["John Smith", "Jane Doe"]

    def test_an_author_list_passes_through_unchanged(self):
        metadata = DocumentMetadata.from_dict(
            {"id": 1, "title": "T", "authors": ["Smith, John", "Doe, Jane"]}
        )
        assert metadata.authors == ["Smith, John", "Doe, Jane"]

    def test_document_id_falls_back_to_the_document_id_key(self):
        assert DocumentMetadata.from_dict({"document_id": 9, "title": "T"}).document_id == 9

    def test_pmid_is_coerced_to_a_string(self):
        assert DocumentMetadata.from_dict({"id": 1, "title": "T", "pmid": 123}).pmid == "123"

    def test_round_trip_via_to_dict(self):
        metadata = DocumentMetadata(
            document_id=5,
            title="A title",
            authors=["John Smith"],
            journal="J",
            year=2020,
            pmid="1",
            doi="10.1/x",
            volume="3",
            issue="2",
            pages="1-9",
            publication_date="2020-01-01",
        )
        assert DocumentMetadata.from_dict(metadata.to_dict()) == metadata

    def test_first_author_surname_and_label(self):
        metadata = DocumentMetadata(document_id=1, title="T", authors=["John Smith"], year=2023)
        assert metadata.get_first_author_surname() == "Smith"
        assert metadata.generate_label() == "Smith2023"

    def test_a_label_without_a_year_uses_nd(self):
        metadata = DocumentMetadata(document_id=1, title="T", authors=["John Smith"])
        assert metadata.generate_label() == "Smithn.d."

    def test_no_authors_reads_unknown(self):
        metadata = DocumentMetadata(document_id=1, title="T")
        assert metadata.get_first_author_surname() == "Unknown"


class TestFormattedReference:
    def test_round_trip_with_nested_metadata(self):
        reference = FormattedReference(
            number=1,
            document_id=5,
            formatted_text="1. Smith J. T.",
            metadata=DocumentMetadata(document_id=5, title="T", authors=["John Smith"]),
        )
        assert FormattedReference.from_dict(reference.to_dict()) == reference

    def test_round_trip_without_metadata(self):
        reference = FormattedReference(number=2, document_id=9, formatted_text="2. [missing]")
        assert FormattedReference.from_dict(reference.to_dict()) == reference


class TestParseCitations:
    def test_markers_parse_in_order_with_positions(self):
        text = "Alpha [@id:12:Smith2023] beta [@id:7:Jones2021] gamma."
        citations = parse_citations(text)
        assert [c.document_id for c in citations] == [12, 7]
        assert citations[0].label == "Smith2023"
        assert citations[0].position == text.index("[@id:12")
        assert citations[0].text == "[@id:12:Smith2023]"

    def test_text_without_markers_yields_nothing(self):
        assert parse_citations("No citations here.") == []

    def test_malformed_markers_are_ignored(self):
        assert parse_citations("[@id:abc:NotANumber] [@id:5] [@id:5:]") == []


class TestCountingAndPositions:
    TEXT = "[@id:3:A] mid [@id:1:B] and [@id:3:A] end"

    def test_unique_ids_keep_order_of_first_appearance(self):
        assert unique_document_ids(self.TEXT) == [3, 1]

    def test_every_marker_counts(self):
        assert count_citations(self.TEXT) == 3

    def test_unique_documents_count_once(self):
        assert count_unique_citations(self.TEXT) == 2

    def test_positions_group_by_document(self):
        positions = citation_positions(self.TEXT)
        assert set(positions) == {3, 1}
        assert positions[3] == [0, self.TEXT.rindex("[@id:3")]

    def test_range_lookup_is_half_open(self):
        second_start = self.TEXT.index("[@id:1")
        found = citations_in_range(self.TEXT, 0, second_start)
        assert [c.document_id for c in found] == [3]


class TestMarkersAndReplacement:
    def test_create_citation_marker_round_trips(self):
        marker = create_citation_marker(12345, "Smith2023")
        assert marker == "[@id:12345:Smith2023]"
        [citation] = parse_citations(marker)
        assert (citation.document_id, citation.label) == (12345, "Smith2023")

    def test_replace_one_document_everywhere(self):
        text = "A [@id:5:X] B [@id:5:Y] C [@id:6:Z]"
        assert replace_citation_with_number(text, 5, 1) == "A [1] B [1] C [@id:6:Z]"

    def test_an_id_sharing_a_prefix_is_not_replaced(self):
        assert replace_citation_with_number("[@id:55:X]", 5, 1) == "[@id:55:X]"

    def test_replace_all_preserves_unmapped_markers(self):
        text = "[@id:5:X] and [@id:6:Y]"
        assert replace_all_citations_with_numbers(text, {5: 1}) == "[1] and [@id:6:Y]"


class TestAdjacentCitations:
    def test_comma_and_space_separated_markers_group(self):
        text = "Claim [@id:1:A], [@id:2:B] [@id:3:C]. Later [@id:4:D]."
        groups = find_adjacent_citations(text)
        assert [[c.document_id for c in g] for g in groups] == [[1, 2, 3], [4]]

    def test_prose_between_markers_breaks_the_group(self):
        groups = find_adjacent_citations("[@id:1:A] and [@id:2:B]")
        assert [[c.document_id for c in g] for g in groups] == [[1], [2]]

    def test_no_markers_means_no_groups(self):
        assert find_adjacent_citations("plain text") == []


def _group(*document_ids: int) -> list[Citation]:
    return [
        Citation(document_id=i, label=f"L{i}", position=0, text=f"[@id:{i}:L{i}]")
        for i in document_ids
    ]


class TestFormatCitationGroup:
    def test_two_numbers_stay_listed(self):
        assert format_citation_group(_group(1, 2), {1: 1, 2: 2}) == "[1,2]"

    def test_three_sequential_numbers_combine_to_a_range(self):
        assert format_citation_group(_group(1, 2, 3), {1: 1, 2: 2, 3: 3}) == "[1-3]"

    def test_a_run_and_a_straggler(self):
        assert format_citation_group(_group(1, 2, 3, 5), {1: 1, 2: 2, 3: 3, 5: 5}) == "[1-3,5]"

    def test_a_two_run_is_listed_not_ranged(self):
        assert format_citation_group(_group(1, 2, 4), {1: 1, 2: 2, 4: 4}) == "[1,2,4]"

    def test_combining_can_be_disabled(self):
        assert (
            format_citation_group(_group(1, 2, 3), {1: 1, 2: 2, 3: 3}, combine_sequential=False)
            == "[1,2,3]"
        )

    def test_unnumbered_citations_are_skipped(self):
        assert format_citation_group(_group(1, 9), {1: 1}) == "[1]"

    def test_nothing_numbered_is_empty(self):
        assert format_citation_group(_group(9), {}) == ""


class TestMarkerValidation:
    def test_a_well_formed_marker_validates(self):
        assert validate_citation_marker("[@id:5:Smith2023]") == (True, None)

    def test_a_marker_with_trailing_text_is_not_valid(self):
        # Regression: upstream used .match(), so trailing junk validated.
        ok, reason = validate_citation_marker("[@id:5:Smith2023] trailing")
        assert not ok
        assert reason is not None

    def test_a_zero_document_id_is_rejected(self):
        ok, reason = validate_citation_marker("[@id:0:X]")
        assert not ok
        assert "positive" in reason

    def test_an_overlong_label_is_rejected(self):
        ok, _reason = validate_citation_marker(f"[@id:5:{'x' * 101}]")
        assert not ok
        assert validate_citation_marker(f"[@id:5:{'x' * 100}]") == (True, None)

    def test_extract_helpers_read_a_marker(self):
        assert extract_label_from_citation("[@id:5:Smith2023]") == "Smith2023"
        assert extract_document_id_from_citation("[@id:5:Smith2023]") == 5

    def test_extract_helpers_reject_junk_and_trailing_text(self):
        assert extract_label_from_citation("not a marker") is None
        assert extract_document_id_from_citation("[@id:5:X] tail") is None
