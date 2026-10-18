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

"""The shared retraction-status contract (#504), and what it cannot express."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from bmlib.publications.models import RetractionNature, RetractionNotice
from bmlib.publications.retraction_status import (
    CONCERN_KINDS,
    NON_DECIDING_KINDS,
    RETRACTION_FAMILY,
    CrossrefOutcome,
    CrossrefResult,
    RetractionEvidence,
    RetractionKind,
    RetractionLookup,
    RetractionSource,
    RetractionStatus,
    RetractionVerdict,
    crossref_retraction_requests,
    evidence_from_notices,
    parse_crossref_retraction_response,
    retraction_status,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "retraction_status.json").read_text(encoding="utf-8")
)


def _body(case: dict[str, Any]) -> bytes:
    if "body_text" in case:
        return case["body_text"].encode("utf-8")
    return json.dumps(case["body"]).encode("utf-8")


def _without_reason(result: dict[str, Any]) -> dict[str, Any]:
    assert result["outcome"] != "unreachable" or result["reason"], "an unreachable result names why"
    return {key: value for key, value in result.items() if key != "reason"}


@pytest.mark.parametrize("case", FIXTURE["rule_cases"], ids=lambda c: c["name"])
def test_rule_case(case: dict[str, Any]) -> None:
    crossref = None
    if case["crossref"] is not None:
        (crossref,) = parse_crossref_retraction_response(
            case["crossref"]["status"],
            _body(case["crossref"]),
            [case["crossref"].get("for", case["doi"])],
        ).values()
    export = case["export"]
    verdict = retraction_status(
        doi=case["doi"],
        export=None if export is None else [RetractionEvidence.from_dict(e) for e in export],
        crossref=crossref,
        publication_types=case["publication_types"],
    )
    assert verdict.to_dict() == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["parse_cases"], ids=lambda c: c["name"])
def test_parse_case(case: dict[str, Any]) -> None:
    results = parse_crossref_retraction_response(case["status"], _body(case), case["requested"])
    got = {doi: _without_reason(result.to_dict()) for doi, result in results.items()}
    assert got == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["request_cases"], ids=lambda c: c["name"])
def test_request_case(case: dict[str, Any]) -> None:
    requests = crossref_retraction_requests(case["dois"], case["mailto"])
    got = {
        "batches": [{"url": b.url, "dois": list(b.dois)} for b in requests.batches],
        "unsendable": list(requests.unsendable),
    }
    assert got == case["expected"]


def test_the_fixture_covers_every_status() -> None:
    statuses = {case["expected"]["status"] for case in FIXTURE["rule_cases"]}
    assert statuses == {status.value for status in RetractionStatus}


def test_every_kind_that_decides_decides_a_fixture_case() -> None:
    deciding = {
        case["expected"]["deciding"]["kind"]
        for case in FIXTURE["rule_cases"]
        if case["expected"]["deciding"]
    }
    decisive = RETRACTION_FAMILY | CONCERN_KINDS | {RetractionKind.REINSTATEMENT}
    assert {kind.value for kind in decisive} <= deciding


def test_every_kind_chooses_one_role() -> None:
    groups = [
        RETRACTION_FAMILY,
        CONCERN_KINDS,
        NON_DECIDING_KINDS,
        {RetractionKind.REINSTATEMENT},
        {RetractionKind.OTHER},
    ]
    assert sum(len(group) for group in groups) == len(RetractionKind)
    assert set().union(*groups) == set(RetractionKind)


def _served(
    evidence: list[RetractionEvidence], targets: tuple[str, ...] = (), doi: str = "10.5555/a"
) -> CrossrefResult:
    return CrossrefResult(CrossrefOutcome.SERVED, tuple(evidence), targets, doi=doi)


def test_the_guard_normalises_both_sides_itself() -> None:
    # A result an app rebuilt from storage, not from the parser.
    retraction = RetractionEvidence(
        RetractionSource.CROSSREF_PUBLISHER,
        RetractionKind.RETRACTION,
        "2020-06-04",
        "https://doi.org/10.5555/BMLIB.NOTICE",
    )
    verdict = retraction_status(
        doi="10.5555/bmlib.notice-of",
        export=[],
        crossref=_served(
            [retraction], ("10.5555/Bmlib.Notice",), doi="https://doi.org/10.5555/BMLIB.NOTICE-OF"
        ),
    )
    assert verdict == RetractionVerdict(RetractionStatus.CLEAR)


def test_a_result_for_another_doi_has_not_answered() -> None:
    # #508's unit form; the fixture's two misfiled cases are the contract.
    verdict = retraction_status(doi="10.5555/b", export=[], crossref=_served([]))
    assert verdict == RetractionVerdict(
        RetractionStatus.UNCHECKED, unanswered=(RetractionLookup.CROSSREF,)
    )


def test_crossref_served_without_a_doi_has_not_answered() -> None:
    verdict = retraction_status(doi=None, export=[], crossref=_served([]))
    assert verdict == RetractionVerdict(
        RetractionStatus.UNCHECKED, unanswered=(RetractionLookup.CROSSREF,)
    )


def test_one_joined_string_of_publication_types_is_refused() -> None:
    with pytest.raises(TypeError):
        retraction_status(
            doi=None, export=None, crossref=None, publication_types="Retracted Publication"
        )


@pytest.mark.parametrize(
    ("status", "kind", "unanswered"),
    [
        (RetractionStatus.CLEAR, None, (RetractionLookup.CROSSREF,)),
        (RetractionStatus.UNCHECKED, None, ()),
        (RetractionStatus.CLEAR, RetractionKind.RETRACTION, ()),
        (RetractionStatus.RETRACTED, None, ()),
        (RetractionStatus.RETRACTED, RetractionKind.EXPRESSION_OF_CONCERN, ()),
        (RetractionStatus.CONCERN, RetractionKind.RETRACTION, ()),
    ],
)
def test_a_verdict_that_contradicts_itself_is_refused(
    status: RetractionStatus,
    kind: RetractionKind | None,
    unanswered: tuple[RetractionLookup, ...],
) -> None:
    deciding = RetractionEvidence(RetractionSource.PUBMED_LABEL, kind) if kind else None
    with pytest.raises(ValueError):
        RetractionVerdict(status, deciding, unanswered)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"outcome": CrossrefOutcome.NOT_KNOWN, "retraction_targets": ("10.5555/a",)},
        {"outcome": CrossrefOutcome.UNREACHABLE},
        {"outcome": CrossrefOutcome.SERVED, "reason": "why", "doi": "10.5555/a"},
        {"outcome": CrossrefOutcome.SERVED},
        {"outcome": CrossrefOutcome.NOT_KNOWN, "doi": "10.5555/a"},
        {"outcome": CrossrefOutcome.UNREACHABLE, "reason": "why", "doi": "10.5555/a"},
    ],
)
def test_a_crossref_result_that_contradicts_itself_is_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CrossrefResult(**kwargs)


def test_an_undecodable_body_is_unreachable() -> None:
    (result,) = parse_crossref_retraction_response(200, b"\xff\xfe{", ["10.5555/a"]).values()
    assert result.outcome is CrossrefOutcome.UNREACHABLE


def test_an_unknown_type_warns_once_per_value(caplog: pytest.LogCaptureFixture) -> None:
    relation = {"DOI": "10.5555/n", "type": "Mystery", "source": "publisher"}
    body = json.dumps(
        {
            "message": {
                "items": [
                    {"DOI": "10.5555/a", "updated-by": [relation, relation]},
                    {"DOI": "10.5555/b", "updated-by": [{**relation, "type": "mystery"}]},
                ]
            }
        }
    )
    with caplog.at_level(logging.WARNING, logger="bmlib.publications.retraction_status"):
        results = parse_crossref_retraction_response(200, body, ["10.5555/a", "10.5555/b"])
    assert [r.message for r in caplog.records if "Unrecognised Crossref relation type" in r.message]
    assert len(caplog.records) == 1
    assert results["10.5555/a"].evidence[0].kind is RetractionKind.OTHER


def test_export_notices_become_evidence() -> None:
    notices = [
        RetractionNotice(
            record_id="4036",
            nature=RetractionNature.RETRACTION,
            doi="10.1016/s0140-6736(97)11096-0",
            notice_doi="10.1016/S0140-6736(10)60175-4",
            retraction_date="2010-02-06",
            raw_nature="Retraction",
        ),
        RetractionNotice(record_id="9", nature=RetractionNature.OTHER, raw_nature="Mystery"),
    ]
    assert evidence_from_notices(notices) == [
        RetractionEvidence(
            RetractionSource.RETRACTION_WATCH_EXPORT,
            RetractionKind.RETRACTION,
            "2010-02-06",
            "10.1016/s0140-6736(10)60175-4",
            "4036",
            "Retraction",
        ),
        RetractionEvidence(
            RetractionSource.RETRACTION_WATCH_EXPORT,
            RetractionKind.OTHER,
            record_id="9",
            raw_kind="Mystery",
        ),
    ]


def test_every_export_nature_has_a_kind() -> None:
    for nature in RetractionNature:
        notice = RetractionNotice(record_id="1", nature=nature)
        assert evidence_from_notices([notice])[0].kind.value == nature.value
