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
