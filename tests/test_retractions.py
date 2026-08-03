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
