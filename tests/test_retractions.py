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

"""Tests for Retraction Watch parsing, storage and the retraction rule."""

from __future__ import annotations

import pytest

from bmlib.publications.models import RetractionNature, RetractionNotice
from bmlib.publications.retractions import (
    _clean_identifier,
    _find_column,
    _parse_date,
    _split_reasons,
)


class TestRetractionNature:
    def test_the_four_known_natures_map_from_the_export(self):
        assert RetractionNature.from_raw("Retraction") is RetractionNature.RETRACTION
        assert RetractionNature.from_raw("Correction") is RetractionNature.CORRECTION
        assert RetractionNature.from_raw("Reinstatement") is RetractionNature.REINSTATEMENT

    def test_expression_of_concern_is_matched_case_insensitively(self):
        # The live export writes "Expression of concern" -- lower-case "c".
        assert (
            RetractionNature.from_raw("Expression of concern")
            is RetractionNature.EXPRESSION_OF_CONCERN
        )
        assert (
            RetractionNature.from_raw("  EXPRESSION OF CONCERN  ")
            is RetractionNature.EXPRESSION_OF_CONCERN
        )

    def test_an_unknown_nature_is_preserved_rather_than_rejected(self):
        # The vocabulary is Retraction Watch's and can grow. A new notice type
        # must cost one row of fidelity, not a failed 71,000-row import.
        assert RetractionNature.from_raw("Partial Retraction") is RetractionNature.OTHER
        assert RetractionNature.from_raw("") is RetractionNature.OTHER
        assert RetractionNature.from_raw(None) is RetractionNature.OTHER


class TestRetractionNoticeModel:
    def test_round_trips_through_a_dict(self):
        notice = RetractionNotice(
            record_id="71974",
            nature=RetractionNature.RETRACTION,
            doi="10.1007/s00500-023-08327-1",
            pmid="12345678",
            notice_doi="10.1007/s00500-023-99999-9",
            notice_pmid="87654321",
            title="A paper",
            journal="Soft Computing",
            retraction_date="2026-03-09",
            original_paper_date="2023-05-06",
            reasons=["Rogue Editor", "Unreliable Results and/or Conclusions"],
            raw_nature="Retraction",
        )

        restored = RetractionNotice.from_dict(notice.to_dict())

        assert restored == notice

    def test_the_serialised_nature_is_the_enum_value_not_the_file_wording(self):
        notice = RetractionNotice(
            record_id="1",
            nature=RetractionNature.EXPRESSION_OF_CONCERN,
            doi="10.1/x",
            raw_nature="Expression of concern",
        )

        data = notice.to_dict()

        assert data["nature"] == "expression_of_concern"
        assert data["raw_nature"] == "Expression of concern"
        assert RetractionNotice.from_dict(data).nature is RetractionNature.EXPRESSION_OF_CONCERN

    def test_reasons_default_to_an_independent_list(self):
        first = RetractionNotice(record_id="1", nature=RetractionNature.RETRACTION)
        second = RetractionNotice(record_id="2", nature=RetractionNature.RETRACTION)

        first.reasons.append("Falsification of Data")

        assert second.reasons == []

    def test_record_id_is_required(self):
        with pytest.raises(TypeError):
            RetractionNotice(nature=RetractionNature.RETRACTION)  # type: ignore[call-arg]


class TestColumnResolution:
    def test_the_pmid_column_of_the_real_export_is_found(self):
        # Upstream's candidate tuple contained none of the export's real PMID
        # column names, so its PMID branch never fired on a real file.
        from bmlib.publications.retractions import _PMID_COLUMNS  # noqa: E402

        row = {"OriginalPaperPubMedID": "12345678", "RetractionPubMedID": "87654321"}

        assert _find_column(row, _PMID_COLUMNS) == "12345678"

    def test_the_retracted_paper_is_preferred_to_the_notice(self):
        from bmlib.publications.retractions import (  # noqa: E402
            _DOI_COLUMNS,
            _NOTICE_DOI_COLUMNS,
        )

        row = {"RetractionDOI": "10.1/notice", "OriginalPaperDOI": "10.1/paper"}

        assert _find_column(row, _DOI_COLUMNS) == "10.1/paper"
        assert _find_column(row, _NOTICE_DOI_COLUMNS) == "10.1/notice"

    def test_a_blank_cell_falls_through_to_the_next_candidate(self):
        row = {"A": "   ", "B": "value"}

        assert _find_column(row, ("A", "B")) == "value"

    def test_no_candidate_present_returns_none(self):
        assert _find_column({"X": "y"}, ("A", "B")) is None


class TestIdentifierSentinels:
    def test_a_zero_pubmed_id_is_not_an_identifier(self):
        # 46.04% of rows in the live export write "0" for an absent PMID.
        # It is a non-empty string, so a truthiness test accepts it and every
        # one of those rows collapses onto a single fake key.
        assert _clean_identifier("0") is None

    def test_an_unavailable_doi_is_not_an_identifier_in_either_casing(self):
        # The same file carries both casings: "Unavailable" 2,235, and
        # "unavailable" 1,184. A case-sensitive check leaks 1,184 rows.
        assert _clean_identifier("Unavailable") is None
        assert _clean_identifier("unavailable") is None

    def test_blank_and_missing_values_are_not_identifiers(self):
        assert _clean_identifier("") is None
        assert _clean_identifier("   ") is None
        assert _clean_identifier(None) is None

    def test_a_real_identifier_survives_and_is_stripped(self):
        assert _clean_identifier("  10.1/abc  ") == "10.1/abc"
        assert _clean_identifier("12345678") == "12345678"

    def test_a_zero_inside_a_real_identifier_is_untouched(self):
        assert _clean_identifier("10.1016/j.0000") == "10.1016/j.0000"
        assert _clean_identifier("101") == "101"


class TestReasonSplitting:
    def test_the_trailing_semicolon_does_not_become_an_empty_reason(self):
        # Every populated row in the live export ends its Reason cell with
        # ";", so a naive split always yields an empty final item.
        value = "Concerns/Issues about Peer Review;Rogue Editor;"

        assert _split_reasons(value) == ["Concerns/Issues about Peer Review", "Rogue Editor"]

    def test_a_leading_plus_is_stripped_for_the_other_export_variant(self):
        # The Crossref export carries no "+" prefix (0 rows of 71,306); the
        # Retraction Watch native export does.
        assert _split_reasons("+Falsification of Data;+Rogue Editor;") == [
            "Falsification of Data",
            "Rogue Editor",
        ]

    def test_a_blank_cell_yields_no_reasons(self):
        assert _split_reasons("") == []
        assert _split_reasons(None) == []
        assert _split_reasons(";;;") == []


class TestDateParsing:
    def test_the_export_format_with_a_trailing_time_is_parsed(self):
        # The live export writes M/D/YYYY H:MM on 100% of dated rows.
        assert _parse_date("3/9/2026 0:00") == "2026-03-09"
        assert _parse_date("12/25/2021 0:00") == "2021-12-25"

    def test_an_iso_date_is_parsed(self):
        assert _parse_date("2026-03-09") == "2026-03-09"

    def test_a_day_above_twelve_disambiguates_to_month_first(self):
        assert _parse_date("5/31/2024 0:00") == "2024-05-31"

    def test_an_unparseable_date_becomes_none_rather_than_failing(self):
        assert _parse_date("not a date") is None
        assert _parse_date("") is None
        assert _parse_date(None) is None
