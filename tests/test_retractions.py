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

import io

import pytest

from bmlib.publications.models import RetractionNature, RetractionNotice
from bmlib.publications.retractions import (
    _clean_identifier,
    _find_column,
    _parse_date,
    _split_reasons,
    is_retracted,
    parse_retraction_watch_csv,
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


_HEADER = (
    "Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,"
    "ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,"
    "OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n"
)


def _row(
    record_id="1",
    retraction_date="3/9/2026 0:00",
    retraction_doi="10.1/notice",
    retraction_pmid="87654321",
    original_date="5/6/2023 0:00",
    original_doi="10.1/paper",
    original_pmid="12345678",
    nature="Retraction",
    reason="Rogue Editor;",
    title="A paper",
    journal="Soft Computing",
):
    """Build one CSV data row matching the live export's 21-field shape."""
    return (
        f"{record_id},{title},Subject,Inst,{journal},Pub,AU,Author,URL,Article,"
        f"{retraction_date},{retraction_doi},{retraction_pmid},{original_date},"
        f"{original_doi},{original_pmid},{nature},{reason},No,Notes,\n"
    )


def _csv(*rows, header=_HEADER, encoding="utf-8"):
    """Return a seekable binary stream of a CSV document."""
    return io.BytesIO((header + "".join(rows)).encode(encoding))


def _document_with_a_bad_byte_past(offset: int, row_count: int = 1000) -> bytes:
    """Return `row_count` encoded rows with one invalid UTF-8 byte past `offset`.

    Corrupts the first row whose default ``"A paper"`` title starts at or
    after `offset`, so callers can place the bad byte on either side of the
    old, now-retired 64 KiB probe window to distinguish "caught during
    detection" from "hit while actually streaming the file".
    """
    rows = [_row(record_id=str(i)) for i in range(1, row_count + 1)]
    document = (_HEADER + "".join(rows)).encode("utf-8")
    bad_byte_offset = document.index(b"A paper", offset)
    assert bad_byte_offset >= offset
    return document[:bad_byte_offset] + b"caf\xe9" + document[bad_byte_offset + len(b"A paper") :]


class TestParsingTheExport:
    def test_a_row_becomes_a_notice_with_both_identifier_pairs(self):
        (notice,) = list(parse_retraction_watch_csv(_csv(_row())))

        assert notice.record_id == "1"
        assert notice.nature is RetractionNature.RETRACTION
        assert notice.doi == "10.1/paper"
        assert notice.pmid == "12345678"
        assert notice.notice_doi == "10.1/notice"
        assert notice.notice_pmid == "87654321"
        assert notice.title == "A paper"
        assert notice.journal == "Soft Computing"
        assert notice.retraction_date == "2026-03-09"
        assert notice.original_paper_date == "2023-05-06"
        assert notice.reasons == ["Rogue Editor"]
        assert notice.raw_nature == "Retraction"

    def test_a_zero_pubmed_id_is_not_stored_as_a_pmid(self):
        (notice,) = list(parse_retraction_watch_csv(_csv(_row(original_pmid="0"))))

        assert notice.pmid is None
        assert notice.doi == "10.1/paper"

    def test_an_unavailable_doi_is_not_stored_as_a_doi(self):
        rows = (
            _row(record_id="1", original_doi="Unavailable"),
            _row(record_id="2", original_doi="unavailable"),
        )

        notices = list(parse_retraction_watch_csv(_csv(*rows)))

        assert [n.doi for n in notices] == [None, None]
        assert [n.pmid for n in notices] == ["12345678", "12345678"]

    def test_a_row_with_no_usable_identifier_is_reported_not_stored(self):
        skipped: list[tuple[int, str]] = []
        row = _row(original_doi="Unavailable", original_pmid="0")

        # on_skip is called as on_skip(line_number, reason) -- two positional
        # args, per the Callable[[int, str], None] contract -- so a plain
        # list.append (arity one) needs a lambda wrapper, as the design doc's
        # own worked example does.
        notices = list(
            parse_retraction_watch_csv(_csv(row), on_skip=lambda n, why: skipped.append((n, why)))
        )

        assert notices == []
        assert len(skipped) == 1
        assert skipped[0][0] == 2

    def test_the_trailing_empty_rows_are_skipped_not_stored(self):
        # The live export ends with 190 entirely empty rows.
        empty = "," * 20 + "\n"
        skipped: list[tuple[int, str]] = []

        notices = list(
            parse_retraction_watch_csv(
                _csv(_row(), empty, empty),
                on_skip=lambda n, why: skipped.append((n, why)),
            )
        )

        assert len(notices) == 1
        assert len(skipped) == 2

    def test_a_byte_order_mark_does_not_hide_the_first_column(self):
        # Decoded as plain utf-8, a BOM glues itself to the first field name,
        # so "Record ID" becomes unfindable and every row is skipped.
        stream = _csv(_row(), encoding="utf-8-sig")

        (notice,) = list(parse_retraction_watch_csv(stream))

        assert notice.record_id == "1"

    def test_a_failed_encoding_attempt_does_not_duplicate_rows(self):
        # Upstream accumulated into a list created outside its encoding retry
        # loop and never cleared it, so a decode failure part-way through left
        # the rows already read in place and the next attempt appended them
        # all again. The bad byte must sit past the old 64 KiB probe window:
        # inside it, the probe itself failed and detection fell through to
        # the next encoding before any row was ever read, so the retry loop
        # the defect actually lived in never ran.
        document = _document_with_a_bad_byte_past(1 << 16, row_count=1000)

        notices = list(parse_retraction_watch_csv(io.BytesIO(document)))

        assert len(notices) == 1000
        assert [n.record_id for n in notices] == [str(i) for i in range(1, 1001)]

    def test_an_invalid_byte_past_the_old_probe_window_does_not_crash_mid_stream(self):
        # A 64 KiB probe can decode cleanly and still leave a single bad byte
        # tens of megabytes later. The old probe-based detector would commit
        # to that encoding and then stream the rest of the file through a
        # TextIOWrapper that could not retry: hitting the bad byte raised an
        # uncaught UnicodeDecodeError after tens of thousands of rows had
        # already been yielded, with no way for the caller to resume.
        # Scanning the whole file before choosing an encoding closes that
        # gap -- this test fails with that UnicodeDecodeError against the
        # old probe-based detector and only passes against the whole-file
        # scan.
        document = _document_with_a_bad_byte_past(1 << 16, row_count=1000)
        assert document[: 1 << 16].decode("utf-8")  # the probe window itself is clean UTF-8

        notices = list(parse_retraction_watch_csv(io.BytesIO(document)))

        assert len(notices) == 1000
        assert notices[-1].record_id == "1000"

    def test_an_unknown_nature_does_not_stop_the_parse(self):
        rows = (_row(record_id="1", nature="Partial Retraction"), _row(record_id="2"))

        notices = list(parse_retraction_watch_csv(_csv(*rows)))

        assert [n.nature for n in notices] == [
            RetractionNature.OTHER,
            RetractionNature.RETRACTION,
        ]
        assert notices[0].raw_nature == "Partial Retraction"

    def test_a_path_is_accepted_as_well_as_a_stream(self, tmp_path):
        path = tmp_path / "rw.csv"
        path.write_bytes((_HEADER + _row()).encode("utf-8"))

        (notice,) = list(parse_retraction_watch_csv(path))

        assert notice.record_id == "1"

    def test_a_non_seekable_stream_is_rejected_clearly(self):
        class _Unseekable(io.RawIOBase):
            def readable(self):
                return True

            def seekable(self):
                return False

        with pytest.raises(ValueError, match="seekable"):
            list(parse_retraction_watch_csv(_Unseekable()))


def _notice(nature, date, record_id="x"):
    return RetractionNotice(
        record_id=record_id, nature=nature, doi="10.1/paper", retraction_date=date
    )


class TestTheRetractionRule:
    def test_no_notices_means_not_retracted(self):
        assert is_retracted([]) is False

    def test_a_single_retraction_reads_as_retracted(self):
        assert is_retracted([_notice(RetractionNature.RETRACTION, "2020-01-01")]) is True

    def test_a_reinstatement_does_not_read_as_retracted(self):
        # A Reinstatement is the opposite of a retraction. Upstream stored
        # every row as is_retracted=TRUE, including these.
        notices = [
            _notice(RetractionNature.REINSTATEMENT, "2022-10-28"),
            _notice(RetractionNature.RETRACTION, "2020-05-01"),
        ]

        assert is_retracted(notices) is False

    def test_a_later_correction_does_not_clear_an_earlier_retraction(self):
        # 10.1016/j.anbehav.2009.11.027 in the live export: retracted
        # 2011-09-08, corrected 2017-12-14. A flat "latest notice wins" reads
        # this retracted paper as clean, and 51 other papers with it.
        notices = [
            _notice(RetractionNature.CORRECTION, "2017-12-14"),
            _notice(RetractionNature.RETRACTION, "2011-09-08"),
        ]

        assert is_retracted(notices) is True

    def test_an_expression_of_concern_after_a_retraction_does_not_clear_it(self):
        notices = [
            _notice(RetractionNature.EXPRESSION_OF_CONCERN, "2024-10-02"),
            _notice(RetractionNature.RETRACTION, "2024-09-30"),
        ]

        assert is_retracted(notices) is True

    def test_a_retraction_after_a_reinstatement_reads_as_retracted(self):
        notices = [
            _notice(RetractionNature.RETRACTION, "2024-01-01"),
            _notice(RetractionNature.REINSTATEMENT, "2022-01-01"),
        ]

        assert is_retracted(notices) is True

    def test_an_expression_of_concern_alone_is_not_a_retraction(self):
        notice = _notice(RetractionNature.EXPRESSION_OF_CONCERN, "2021-01-01")
        assert is_retracted([notice]) is False

    def test_sorting_by_date_is_required_for_correct_decision(self):
        # Without sorting, scanning arrival order [RETRACTION(2020), REINSTATEMENT(2022)]
        # hits RETRACTION first and returns True — wrong. The correct answer is False
        # because REINSTATEMENT is newest and decides. This test verifies both orderings
        # return False, which means sorting must have happened.
        retraction_2020 = _notice(RetractionNature.RETRACTION, "2020-01-01")
        reinstatement_2022 = _notice(RetractionNature.REINSTATEMENT, "2022-01-01")

        # Unsorted arrival order: RETRACTION, REINSTATEMENT
        assert is_retracted([retraction_2020, reinstatement_2022]) is False

        # Reversed arrival order: REINSTATEMENT, RETRACTION
        assert is_retracted([reinstatement_2022, retraction_2020]) is False

    def test_a_dateless_notice_does_not_outrank_a_dated_one(self):
        notices = [
            _notice(RetractionNature.RETRACTION, "2020-01-01"),
            _notice(RetractionNature.REINSTATEMENT, None),
        ]

        assert is_retracted(notices) is True


class TestPublicSurface:
    def test_every_public_name_is_exported_from_the_package(self):
        import bmlib.publications as publications

        expected = {
            "RetractionNature",
            "RetractionNotice",
            "parse_retraction_watch_csv",
            "store_retraction_notices",
            "lookup_retractions",
            "is_retracted",
        }

        assert expected <= set(publications.__all__)
        for name in expected:
            assert hasattr(publications, name), name
