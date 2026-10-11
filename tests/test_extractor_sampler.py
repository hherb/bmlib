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

"""Tests for ``scripts/sample_extractor_signals.py`` (issue #368).

The script is a live runner, but what it prints is the evidence for changing
``bmlib/quality/extractors.py``, so its reading has to hold offline. What is
pinned here: a record that could not be measured never enters a denominator; a
draw is a function of the population and the seed, not of the order pages
arrive in; the corpus keeps no text; a moved text is never reported as a code
move; the commands that write a corpus write it where they say and only then;
and the committed corpora were measured by the extractors in this checkout.

No network: every test drives the script through a fake client.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from bmlib.quality.extractors import (
    extract_study_type,
    find_sample_size,
    get_extracted_study_type,
    has_ci_reporting,
    has_power_calculation,
)

# `scripts/` is not a package, so the module is loaded by path, with that
# directory on `sys.path` because the script imports `_sampling`.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "bmlib_extractor_sampler", _SCRIPTS / "sample_extractor_signals.py"
)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError("cannot load the extractor sampler")
sampler = importlib.util.module_from_spec(_spec)
# Registered before it runs: `@dataclass` looks its own module up by name.
sys.modules[_spec.name] = sampler
_spec.loader.exec_module(sampler)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that reaches the real HTTP client.

    Not hypothetical: with the default-path guard mutated, the relevance-draw
    test below ran a live draw against Europe PMC and wrote it over the
    committed corpus. A test needing a client installs its own fake over this.
    ``httpx.Client.send`` is refused too, so a path that builds a client of its
    own rather than through ``_client`` is caught as well.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an offline test reached the network")

    monkeypatch.setattr(sampler, "_client", _refuse)
    monkeypatch.setattr(httpx.Client, "send", _refuse)


def _no_pace(url: str) -> None:
    """A pacer that never waits."""


class _Response:
    """Enough of an httpx response for ``get_json``."""

    def __init__(self, status: int, body: Any = None, *, not_json: bool = False) -> None:
        self.status_code = status
        self._body = body
        self._not_json = not_json
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        if self._not_json:
            raise ValueError("Expecting value")
        return self._body


class _Client:
    """Answers each request with the next response from *script*, recording the params."""

    def __init__(self, *script: _Response | BaseException) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any]) -> _Response:
        assert url == sampler.SEARCH_URL
        self.requests.append(dict(params))
        answer = self.script.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _idlist(hit_count: Any, pmids: list[str], next_cursor: str | None) -> _Response:
    """One idlist page."""
    body: dict[str, Any] = {
        "hitCount": hit_count,
        "resultList": {"result": [{"id": p, "source": "MED", "pmid": p} for p in pmids]},
    }
    if next_cursor is not None:
        body["nextCursorMark"] = next_cursor
    return _Response(200, body)


def _core(*records: dict[str, Any], hit_count: int | None = None) -> _Response:
    """One core page, counting what it delivers unless told otherwise."""
    count = len(records) if hit_count is None else hit_count
    return _Response(200, {"hitCount": count, "resultList": {"result": list(records)}})


_OMITTED = object()


def _record(pmid: str, abstract: Any = "An abstract.", types: Any = ("Journal Article",)) -> dict:
    """One core record; ``types=_OMITTED`` leaves ``pubTypeList`` out."""
    record: dict[str, Any] = {"id": pmid, "source": "MED", "abstractText": abstract}
    if types is not _OMITTED:
        record["pubTypeList"] = {"pubType": list(types) if isinstance(types, tuple) else types}
    return record


_STRATUM = sampler.Stratum("Randomized Controlled Trial", 2014)
#: `draw_sample(range(90, 600) as strings, 5, seed 0, _STRATUM)`.
GOLDEN_DRAW = ["430", "443", "484", "495", "579"]


class TestTheGroundTruthIsPubMeds:
    """The label is PubMed's publication type, mapped in the audit's order."""

    @pytest.mark.parametrize(
        ("types", "label"),
        [
            (["Meta-Analysis", "Randomized Controlled Trial"], "review"),
            (["Systematic Review"], "review"),
            (["Randomized Controlled Trial", "Clinical Trial"], "rct"),
            (["Controlled Clinical Trial", "Clinical Trial"], "non_randomised"),
            (["Clinical Trial", "Journal Article"], "clinical_trial"),
            (["Observational Study"], "observational"),
            (["Case Reports"], "case_report"),
            (["Journal Article"], "other"),
            (["systematic-review"], "other"),  # a JATS article type, not PubMed's
            ([], "other"),
        ],
    )
    def test_the_first_matching_label_wins(self, types, label):
        assert sampler.label_for(types) == label

    def test_the_comparison_folds_case(self):
        assert sampler.label_for(["randomized controlled trial"]) == "rct"

    def test_the_strata_are_seven_types_by_three_years(self):
        strata = sampler.all_strata()
        assert len(strata) == 21
        assert strata[0].query == (
            'PUB_TYPE:"Randomized Controlled Trial" AND HAS_ABSTRACT:y AND PUB_YEAR:2006 '
            "AND SRC:MED AND LANG:eng"
        )


class TestOneWalk:
    """``walk_once`` follows the cursor and refuses a page it cannot read."""

    def test_the_walk_follows_the_cursor_to_its_end(self):
        client = _Client(
            _idlist(3, ["1", "2"], "c1"),
            _idlist(3, ["3"], "c2"),
            _idlist(3, [], "c2"),
        )
        assert sampler.walk_once(client, _STRATUM, _no_pace) == (3, ["1", "2", "3"])
        assert [r["cursorMark"] for r in client.requests] == ["*", "c1", "c2"]
        assert client.requests[0]["query"] == _STRATUM.query
        assert client.requests[0]["pageSize"] == sampler.ID_PAGE_SIZE

    def test_a_cursor_that_does_not_advance_ends_the_walk(self):
        client = _Client(_idlist(2, ["1", "2"], "*"))
        assert sampler.walk_once(client, _STRATUM, _no_pace) == (2, ["1", "2"])

    def test_a_missing_next_cursor_ends_the_walk(self):
        client = _Client(_idlist(2, ["1", "2"], None))
        assert sampler.walk_once(client, _STRATUM, _no_pace) == (2, ["1", "2"])
        assert len(client.requests) == 1

    def test_repeats_are_kept_for_the_fault_check(self):
        client = _Client(_idlist(2, ["1"], "c1"), _idlist(2, ["1"], "c2"), _idlist(2, [], "c2"))
        assert sampler.walk_once(client, _STRATUM, _no_pace) == (2, ["1", "1"])

    def test_a_count_that_moves_within_the_walk_is_refused(self):
        client = _Client(_idlist(3, ["1", "2"], "c1"), _idlist(4, ["3"], "c2"))
        with pytest.raises(sampler.RequestFailedError, match="moved during the walk"):
            sampler.walk_once(client, _STRATUM, _no_pace)

    @pytest.mark.parametrize(
        "record",
        [
            {"id": "PPR1", "source": "PPR"},
            {"id": "12345", "source": "AGR"},
            {"id": 12345, "source": "MED"},
        ],
        ids=["preprint", "numeric-id-from-another-source", "integer-id"],
    )
    def test_a_record_without_a_pmid_is_refused(self, record):
        body = {"hitCount": 1, "resultList": {"result": [record]}}
        with pytest.raises(sampler.RequestFailedError, match="no PMID"):
            sampler.walk_once(_Client(_Response(200, body)), _STRATUM, _no_pace)

    @pytest.mark.parametrize(
        "answer",
        [
            _Response(500),
            _Response(200, not_json=True),
            _Response(200, ["not", "an", "object"]),
            # One record, so `True == 1` would reconcile were the bool not refused.
            _idlist(True, ["1"], None),
            _Response(200, {"hitCount": -1, "resultList": {"result": []}}),
            _Response(200, {"resultList": {"result": []}}),
            _Response(200, {"hitCount": 1, "resultList": None}),
            _Response(200, {"hitCount": 1, "resultList": {"result": ["1"]}}),
            OSError("unreachable"),
            httpx.ConnectTimeout("timed out"),
        ],
        ids=[
            "http-500",
            "not-json",
            "array",
            "bool-count",
            "negative-count",
            "no-count",
            "null-list",
            "string-record",
            "os-error",
            "transport-error",
        ],
    )
    def test_an_unreadable_page_fails_rather_than_reading_as_empty(self, answer):
        with pytest.raises(sampler.RequestFailedError):
            sampler.walk_once(_Client(answer), _STRATUM, _no_pace)

    @pytest.mark.parametrize(
        "raised",
        [
            TypeError("bad call"),
            AssertionError("wrong URL"),
            httpx.InvalidURL("bad"),
            MemoryError(),
        ],
        ids=["bug-type", "assertion", "invalid-url", "memory"],
    )
    def test_an_instrument_defect_is_raised_not_filed_as_a_hole(self, raised):
        # A hole under the threshold exits 0; this script being wrong must not.
        with pytest.raises(type(raised)):
            sampler.walk_once(_Client(raised), _STRATUM, _no_pace)

    def test_a_throttled_page_is_retried(self, monkeypatch):
        waits: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", waits.append)
        client = _Client(_Response(429), _idlist(1, ["7"], "*"))
        assert sampler.walk_once(client, _STRATUM, _no_pace) == (1, ["7"])
        assert waits == [2.0]

    def test_a_page_throttled_on_every_attempt_fails(self, monkeypatch):
        monkeypatch.setattr(sampler, "_sleep_for", lambda _: None)
        client = _Client(_Response(429), _Response(429), _Response(503))
        with pytest.raises(sampler.RequestFailedError, match="HTTP 503"):
            sampler.walk_once(client, _STRATUM, _no_pace)

    @pytest.mark.parametrize(
        ("pmids", "fault"),
        [
            (["1", "2"], None),
            (["1"], "the walk delivered 1 of 2 promised records"),
            (["1", "1"], "the walk repeated 1 records"),
        ],
    )
    def test_a_walk_is_faulty_when_short_or_repeating(self, pmids, fault):
        assert sampler._walk_fault(2, pmids) == fault


class TestThePopulationIsTheUnionOfItsWalks:
    """A sample drawn from part of a population reads like one drawn from all of it."""

    def test_a_clean_first_walk_is_taken_at_once(self):
        client = _Client(_idlist(2, ["2", "1"], "*"))
        reconciled = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (reconciled.population.pmids, reconciled.walks, reconciled.faults) == (
            ["1", "2"],
            1,
            [],
        )

    def test_faulty_walks_reconcile_as_a_union_and_are_recorded(self):
        # Each walk repeats one record and so misses one; together they hold all three.
        client = _Client(_idlist(3, ["1", "2", "2"], "*"), _idlist(3, ["1", "3", "3"], "*"))
        reconciled = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (reconciled.population.pmids, reconciled.walks) == (["1", "2", "3"], 2)
        assert reconciled.faults == [
            "walk 1: the walk repeated 1 records",
            "walk 2: the walk repeated 1 records",
        ]

    def test_a_failed_walk_spends_an_attempt_and_adds_nothing(self):
        client = _Client(_Response(500), _idlist(2, ["1", "2"], "*"))
        reconciled = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (reconciled.population.pmids, reconciled.walks) == (["1", "2"], 2)
        assert reconciled.faults == ["walk 1: HTTP 500"]

    def test_a_union_short_after_every_walk_fails_the_stratum(self):
        client = _Client(*[_idlist(3, ["1"], "*") for _ in range(sampler.WALK_ATTEMPTS)])
        with pytest.raises(sampler.RequestFailedError, match="union holds 1 of 3"):
            sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert not client.script

    def test_a_union_past_the_count_fails_the_stratum(self):
        # Two different sets under one count: the population changed between walks.
        client = _Client(_idlist(2, ["1", "1"], "*"), _idlist(2, ["2", "3"], "*"))
        with pytest.raises(sampler.RequestFailedError, match="union holds 3 of 2"):
            sampler.walk_until_reconciled(client, _STRATUM, _no_pace)

    def test_a_count_that_moves_between_walks_fails_the_stratum(self):
        client = _Client(_idlist(2, ["1", "1"], "*"), _idlist(3, ["1", "2", "3"], "*"))
        with pytest.raises(sampler.RequestFailedError, match="moved between walks"):
            sampler.walk_until_reconciled(client, _STRATUM, _no_pace)

    def test_an_empty_population_needs_two_walks_that_agree(self):
        client = _Client(_idlist(0, [], None), _idlist(0, [], None))
        reconciled = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (reconciled.population.pmids, reconciled.walks) == ([], 2)

    def test_an_empty_answer_once_is_not_a_population(self):
        script = [_idlist(0, [], None)] + [_Response(500)] * (sampler.WALK_ATTEMPTS - 1)
        with pytest.raises(sampler.RequestFailedError, match="empty population was answered once"):
            sampler.walk_until_reconciled(_Client(*script), _STRATUM, _no_pace)

    def test_an_empty_answer_then_a_population_is_a_moved_count(self):
        client = _Client(_idlist(0, [], None), _idlist(2, ["1", "2"], None))
        with pytest.raises(sampler.RequestFailedError, match="moved between walks"):
            sampler.walk_until_reconciled(client, _STRATUM, _no_pace)


class TestAPopulationHoldsWhatItClaims:
    def test_the_digest_names_the_set_not_the_count(self):
        a = sampler.Population(2, ["10", "9"])
        assert a.digest == hashlib.sha256(b"9,10").hexdigest()
        assert a.digest != sampler.Population(2, ["9", "11"]).digest

    @pytest.mark.parametrize(
        ("hit_count", "pmids", "complete"),
        [
            (2, ["1", "1"], True),
            (3, ["1", "2"], True),
            (1, ["1", "2"], False),
            (3, ["1", "1"], False),
        ],
        ids=["repeated", "complete-but-short", "page-past-the-count", "repeated-page"],
    )
    def test_a_state_no_producer_makes_is_refused(self, hit_count, pmids, complete):
        with pytest.raises(ValueError):
            sampler.Population(hit_count, pmids, complete)

    def test_a_relevance_page_has_no_digest(self):
        with pytest.raises(ValueError, match="no digest"):
            _ = sampler.Population(10, ["5", "2"], complete=False).digest


class TestTheDrawIsAFunctionOfThePopulationAndTheSeed:
    """Not of the order Europe PMC happened to page the population in."""

    POPULATION = [str(n) for n in range(1000, 1500)]

    def test_the_draw_is_pinned_exactly(self):
        # Inequality tests let a changed seed string or a string sort through;
        # this list is what `random.Random("0/Randomized Controlled Trial/2014")`
        # draws from the numerically sorted pool, and any such edit moves it.
        # The PMIDs differ in length, or a string sort would agree with it.
        population = [str(n) for n in range(90, 600)]
        assert sampler.draw_sample(population, 5, 0, _STRATUM) == GOLDEN_DRAW

    def test_page_order_does_not_move_the_draw(self):
        shuffled = list(self.POPULATION)
        random.Random(1).shuffle(shuffled)
        assert sampler.draw_sample(shuffled, 20, 0, _STRATUM) == sampler.draw_sample(
            self.POPULATION, 20, 0, _STRATUM
        )

    def test_the_seed_and_the_stratum_each_move_it(self):
        base = sampler.draw_sample(self.POPULATION, 20, 0, _STRATUM)
        other = sampler.Stratum("Case Reports", 2014)
        assert sampler.draw_sample(self.POPULATION, 20, 1, _STRATUM) != base
        assert sampler.draw_sample(self.POPULATION, 20, 0, other) != base

    def test_a_population_smaller_than_the_draw_is_taken_whole(self):
        assert sampler.draw_sample(["3", "1", "2"], 300, 0, _STRATUM) == ["1", "2", "3"]

    def test_a_short_relevance_page_fails(self):
        with pytest.raises(sampler.RequestFailedError, match="delivered 2 of 3"):
            sampler.relevance_page(_Client(_idlist(10, ["5", "2"], None)), _STRATUM, 3, _no_pace)

    def test_a_relevance_page_repeating_a_record_fails(self):
        with pytest.raises(sampler.RequestFailedError, match="repeated 1"):
            sampler.relevance_page(
                _Client(_idlist(10, ["5", "2", "5"], None)), _STRATUM, 3, _no_pace
            )

    def test_the_relevance_draw_keeps_the_remotes_order(self):
        client = _Client(_idlist(10, ["5", "2", "9"], None))
        population = sampler.relevance_page(client, _STRATUM, 3, _no_pace)
        assert (population.pmids, population.complete) == (["5", "2", "9"], False)
        assert client.requests[0]["pageSize"] == 3
        assert "cursorMark" not in client.requests[0]

    def test_a_relevance_population_smaller_than_the_page_is_taken_whole(self):
        client = _Client(_idlist(2, ["5", "2"], None))
        assert sampler.relevance_page(client, _STRATUM, 3, _no_pace).pmids == ["5", "2"]


class TestEveryHoleIsNamed:
    """A failed, absent, abstract-less or untyped record is a hole, each kind kept apart."""

    def test_each_outcome_is_classified(self):
        client = _Client(
            _core(
                _record("1", "Randomised.", ["Randomized Controlled Trial"]),
                _record("2", "   "),
                _record("3", None),
                _record("5", "Single type.", "Case Reports"),
                _record("6", "Untyped.", types=_OMITTED),
                _record("7", "Typed as nothing.", []),
            )
        )
        pmids = ["1", "2", "3", "4", "5", "6", "7"]
        fetched = sampler.fetch_abstracts(client, pmids, _no_pace)
        assert fetched["1"] == sampler.Fetched(
            "ok", "Randomised.", ("Randomized Controlled Trial",)
        )
        assert fetched["2"] == sampler.Fetched(
            "no-abstract", publication_types=("Journal Article",)
        )
        assert fetched["3"].status == "no-abstract"
        assert fetched["4"] == sampler.Fetched("absent")
        assert fetched["5"].publication_types == ("Case Reports",)
        assert fetched["6"] == sampler.Fetched("no-types")
        assert fetched["7"] == sampler.Fetched("no-types")
        assert client.requests[0] == {
            "query": "EXT_ID:(1 OR 2 OR 3 OR 4 OR 5 OR 6 OR 7) AND SRC:MED",
            "resultType": "core",
            "format": "json",
            "pageSize": 7,
        }

    @pytest.mark.parametrize(
        "record",
        [
            _record("1", {"text": "an object"}),
            _record("1", 12),
            _record("1", types={"name": "Randomized Controlled Trial"}),
            _record("1", types=[{"name": "Randomized Controlled Trial"}]),
            {"id": "1", "source": "MED", "abstractText": "x", "pubTypeList": ["RCT"]},
        ],
        ids=["abstract-object", "abstract-number", "types-object", "type-objects", "list-holder"],
    )
    def test_a_malformed_field_is_this_runs_hole_not_europe_pmcs_answer(self, record):
        # `no-abstract` and `no-types` are written by `remeasure --write`;
        # a malformed body read as either would persist as Europe PMC's answer.
        fetched = sampler.fetch_abstracts(_Client(_core(record)), ["1"], _no_pace)
        assert fetched["1"] == sampler.Fetched("failed")

    @pytest.mark.parametrize(
        "page",
        [
            # Counted 3, delivered 2.
            _core(_record("1"), _record("2"), hit_count=3),
            # One record repeated: three delivered, two of this batch.
            _core(_record("1"), _record("2"), _record("2")),
            # A record this batch did not ask for.
            _core(_record("1"), _record("2"), _record("999")),
            # A record that is not a MEDLINE one.
            _core(_record("1"), _record("2"), {"id": "PPR9", "source": "PPR"}),
            # A count past the batch: the page was not answering this query.
            _core(_record("1"), _record("2"), hit_count=4),
        ],
        ids=["cut-short", "repeated", "stray", "not-medline", "count-past-the-batch"],
    )
    def test_a_faulty_page_files_what_it_omits_as_failed_not_absent(self, page):
        # `remeasure --write` writes an `absent` row over a measured one.
        fetched = sampler.fetch_abstracts(_Client(page), ["1", "2", "3"], _no_pace)
        assert fetched["3"] == sampler.Fetched("failed")
        assert (fetched["1"].status, fetched["2"].status) == ("ok", "ok")

    def test_a_sound_page_counting_fewer_than_asked_files_an_absence(self):
        # Europe PMC counted two and delivered both: it holds no third.
        page = _core(_record("1"), _record("2"))
        assert sampler.fetch_abstracts(_Client(page), ["1", "2", "3"], _no_pace)["3"] == (
            sampler.Fetched("absent")
        )

    def test_a_failed_batch_costs_only_its_own_records(self):
        client = _Client(_Response(500), _core(_record("3")))
        fetched = sampler.fetch_abstracts(client, ["1", "2", "3"], _no_pace, batch=2)
        assert [fetched[p].status for p in ("1", "2", "3")] == ["failed", "failed", "ok"]
        assert [r["pageSize"] for r in client.requests] == [2, 1]

    @pytest.mark.parametrize(
        ("status", "abstract", "types"),
        [
            ("ok", "", ("RCT",)),
            ("ok", "text", ()),
            ("no-abstract", "text", ()),
            ("failed", "", ("RCT",)),
            ("absent", "", ("RCT",)),
            ("throttled", "", ()),
        ],
        ids=["ok-blank", "ok-untyped", "hole-with-text", "failed-typed", "absent-typed", "unknown"],
    )
    def test_a_lookup_no_fetch_produces_is_refused(self, status, abstract, types):
        with pytest.raises(ValueError):
            sampler.Fetched(status, abstract, types)


class TestTheCorpusKeepsNoText:
    """Abstracts are the publishers'; a corpus holds a hash of them and nothing else."""

    ABSTRACT = (
        "In this randomized controlled trial, 1,240 patients were enrolled; "
        "a power calculation was performed. HR 0.8 (95% CI 0.7-0.9)."
    )

    def test_a_row_holds_the_hash_and_the_signals(self):
        row = sampler.record_row(sampler.Fetched("ok", self.ABSTRACT, ("Clinical Trial",)))
        assert self.ABSTRACT not in json.dumps(row)
        assert "randomized" not in json.dumps(row)
        assert row == {
            "status": "ok",
            "publication_types": ["Clinical Trial"],
            "text_sha256": hashlib.sha256(self.ABSTRACT.encode()).hexdigest(),
            "text_chars": len(self.ABSTRACT),
            "signals": {"sample_size": 1240, "power": True, "ci": True, "study_type": "rct"},
        }

    def test_an_unmeasured_row_carries_no_signals(self):
        assert sampler.record_row(sampler.Fetched("absent")) == {"status": "absent"}


class TestTheSignalsAreTheExtractorsOwn:
    """The instrument runs bmlib's public functions on the shape a caller passes."""

    TEXTS = [
        "A retrospective cohort of 12,345 patients. 95% CI 1.1-1.4.",
        "No power calculation was performed in this case report.",
        "We randomly assigned 300 participants; G*Power gave 80% power.",
        "",
    ]

    @pytest.mark.parametrize("text", TEXTS)
    def test_an_abstract_is_measured_as_a_caller_would(self, text):
        document = {"abstract": text}
        search = f"{text} "  # `prepare_extractor_search_text` appends the empty methods
        assert sampler.measure(document) == sampler.Signals(
            find_sample_size(search),
            has_power_calculation(search),
            has_ci_reporting(search),
            get_extracted_study_type(extract_study_type(document)),
        )

    def test_the_longer_full_text_is_chosen_over_the_abstract(self):
        document = {
            "abstract": "We enrolled 40 patients.",
            "full_text": "We enrolled 400 patients. " * 3,
        }
        assert sampler.measure(document).sample_size == 400

    def test_a_full_text_is_measured_as_a_caller_would(self):
        document = {"full_text": self.TEXTS[0] * 3}
        assert sampler.measure(document).sample_size == 12345

    def test_a_missing_study_type_is_refused_not_read_as_unknown(self, monkeypatch):
        monkeypatch.setattr(sampler, "get_extracted_study_type", lambda score: None)
        with pytest.raises(ValueError, match="recorded no study type"):
            sampler.measure({"abstract": "text"})

    @pytest.mark.parametrize(
        "bad",
        [
            {"sample_size": True, "power": False, "ci": False, "study_type": "rct"},
            {"sample_size": "12", "power": False, "ci": False, "study_type": "rct"},
            {"sample_size": 0, "power": False, "ci": False, "study_type": "rct"},
            {"sample_size": None, "power": 1, "ci": False, "study_type": "rct"},
            {"sample_size": None, "power": False, "ci": False, "study_type": ""},
            {"sample_size": None, "power": False, "ci": False, "study_type": "banana"},
            {"power": False, "ci": False, "study_type": "rct"},
            ["not", "an", "object"],
        ],
        ids=[
            "bool-size",
            "string-size",
            "zero-size",
            "int-power",
            "empty-type",
            "unknown-type",
            "missing-size",
            "not-an-object",
        ],
    )
    def test_a_stored_signal_no_extractor_returns_is_refused(self, bad):
        with pytest.raises(ValueError):
            sampler.Signals.from_dict(bad)

    def test_the_study_types_are_the_extractors_own(self):
        assert "unknown" in sampler.STUDY_TYPES
        assert "rct" in sampler.STUDY_TYPES


def _corpus(strata: list[dict[str, Any]], records: dict[str, Any]) -> dict[str, Any]:
    return {"header": {"population": "abstracts"}, "strata": strata, "records": records}


def _ok(size: int | None = None, power: bool = False, ci: bool = False, **extra: Any) -> dict:
    signals = {"sample_size": size, "power": power, "ci": ci, "study_type": "unknown"}
    return {"status": "ok", "text_sha256": "h", "text_chars": 1, "signals": signals} | extra


def _stratum(pmids: list[str], population: int = 1000) -> dict[str, Any]:
    return {
        "publication_type": "Clinical Trial",
        "year": 2014,
        "status": "ok",
        "population": population,
        "pmids": pmids,
    }


def _failed_stratum() -> dict[str, Any]:
    return {
        "publication_type": "Clinical Trial",
        "year": 2014,
        "status": "failed",
        "reason": "HTTP 500",
        "pmids": [],
    }


class TestAnUnmeasuredRecordEntersNoDenominator:
    """And a stratum with too many holes prints ERROR, not a share."""

    def test_a_hole_is_counted_and_left_out(self):
        records = {str(i): _ok(size=100, power=i < 3, ci=i < 5) for i in range(9)} | {
            "9": {"status": "absent"}
        }
        lines, reportable = sampler.report_abstracts(_corpus([_stratum(list(records))], records))
        text = "\n".join(lines)
        assert reportable
        assert "unmeasured 1 (absent 1)" in text
        assert "    sample size found  9/9 " in text
        assert "    power credited     3/9 " in text
        assert "    CI credited        5/9 " in text

    def test_past_the_threshold_the_stratum_reports_error(self):
        records = {str(i): _ok(size=100) for i in range(7)} | {
            str(i): {"status": "failed"} for i in range(7, 10)
        }
        lines, reportable = sampler.report_abstracts(_corpus([_stratum(list(records))], records))
        text = "\n".join(lines)
        assert not reportable
        assert "ERROR — 30.0% unmeasured" in text
        # Nor is its share printed one section down by pooling its records.
        assert "sample size found" not in text
        assert "Pooled rows and the confusion: ERROR" in text

    def test_exactly_at_the_threshold_the_stratum_is_reported(self):
        records = {str(i): _ok(size=100) for i in range(8)} | {
            str(i): {"status": "no-types"} for i in range(8, 10)
        }
        lines, reportable = sampler.report_abstracts(_corpus([_stratum(list(records))], records))
        assert reportable
        assert "    sample size found  8/8 " in "\n".join(lines)

    def test_a_status_on_neither_side_of_the_partition_is_refused(self):
        records = {"1": {"status": "throttled"}}
        with pytest.raises(ValueError, match="does not know"):
            sampler.report_abstracts(_corpus([_stratum(["1"])], records))

    def test_a_stratum_status_on_neither_side_is_refused(self):
        stratum = _stratum([]) | {"status": "skipped"}
        with pytest.raises(ValueError, match="stratum status"):
            sampler.report_abstracts(_corpus([stratum], {}))

    def test_a_stratum_that_could_not_be_drawn_is_an_error(self):
        lines, reportable = sampler.report_abstracts(_corpus([_failed_stratum()], {}))
        assert not reportable
        assert (
            "  Clinical Trial/2014: ERROR — the population could not be drawn (HTTP 500)" in lines
        )

    def test_an_empty_population_beside_a_drawn_one_is_a_finding(self):
        strata = [_stratum([], population=0), _stratum(["1"])]
        lines, reportable = sampler.report_abstracts(_corpus(strata, {"1": _ok(5)}))
        assert reportable
        assert "  Clinical Trial/2014: population 0, nothing to draw" in lines

    def test_a_corpus_in_which_no_stratum_holds_a_record_is_an_error(self):
        strata = [_stratum([], population=0), _stratum([], population=0)]
        lines, reportable = sampler.report_abstracts(_corpus(strata, {}))
        assert not reportable
        assert "ERROR — no stratum holds a record; the query matched nothing" in lines

    def test_the_pooled_rows_are_of_the_drawn_records_only(self):
        records = {"1": _ok(5), "2": _ok(None)}
        lines, _ = sampler.report_abstracts(_corpus([_stratum(["1"])], records))
        assert "  sample size found  1/1 " in "\n".join(lines)

    def test_the_confusion_is_over_unique_records_by_their_own_types(self):
        row = _ok(publication_types=["Clinical Trial", "Randomized Controlled Trial"])
        row["signals"] = row["signals"] | {"study_type": "rct"}
        strata = [_stratum(["1"]), _stratum(["1"]) | {"publication_type": "Case Reports"}]
        lines, _ = sampler.report_abstracts(_corpus(strata, {"1": row}))
        assert "  rct                 1: rct 1" in lines

    def test_what_an_unlabelled_record_carries_is_shown(self):
        row = _ok(publication_types=["Clinical Trial Protocol", "Journal Article"])
        lines, _ = sampler.report_abstracts(_corpus([_stratum(["1"])], {"1": row}))
        assert "(other records carry: " in lines[-1]
        assert "Clinical Trial Protocol 1" in lines[-1]

    def test_an_unreportable_corpus_is_diverted(self, tmp_path):
        path = tmp_path / "corpus.json"
        written = sampler.write_corpus({"header": {}, "records": {}}, path, reportable=False)
        assert written == tmp_path / "corpus.unreportable.json"
        assert not path.exists()
        assert sampler.write_corpus({"header": {}, "records": {}}, path, True) == path


class TestTheCorpusFormat:
    def test_it_round_trips_with_one_record_per_line(self):
        corpus = {
            "header": {"population": "abstracts", "seed": 0},
            "strata": [_stratum(["10", "9"])],
            "records": {"10": _ok(5), "9": {"status": "absent"}},
        }
        text = sampler.dumps_corpus(corpus)
        assert json.loads(text) == corpus
        lines = text.splitlines()
        records = [line for line in lines if '"status"' in line and '"pmids"' not in line]
        assert [line.split(":")[0].strip() for line in records] == [
            '"9"',
            '"10"',
        ]

    def test_an_empty_corpus_round_trips(self):
        corpus = {"header": {}, "strata": [], "records": {}}
        assert json.loads(sampler.dumps_corpus(corpus)) == corpus

    def test_a_well_formed_corpus_loads(self, tmp_path):
        path = tmp_path / "c.json"
        corpus = _corpus([_stratum(["1", "2"]), _failed_stratum()], {"1": _ok(5), "2": _ok()})
        path.write_text(sampler.dumps_corpus(corpus))
        assert sampler.load_corpus(path) == corpus

    @pytest.mark.parametrize(
        "corpus",
        [
            ["not", "a", "corpus"],
            {"records": {}},
            {"header": {}},
            {"header": {}, "records": {"1": {"status": "throttled"}}},
            {"header": {}, "records": {"1": {"status": ["ok"]}}},
            {"header": {}, "records": {"1": {"status": "ok"}}},
            {"header": {}, "records": {"1": {"status": "absent", "signals": {}}}},
            {"header": {}, "records": {"1": _ok() | {"abstract": "the publisher's text"}}},
            {"header": {}, "records": {"1": _ok(publication_types="RCT")}},
            {"header": {}, "records": {"1": _ok() | {"signals": {"power": 1}}}},
            _corpus([_stratum(["1"])] * 0, {}) | {"strata": None},
            _corpus([_stratum(["1"]) | {"status": "skipped"}], {"1": _ok()}),
            _corpus([_stratum(["1", "1"])], {"1": _ok()}),
            _corpus([_stratum(["2"])], {"1": _ok()}),
            _corpus([_failed_stratum() | {"pmids": ["1"]}], {"1": _ok()}),
            _corpus([_stratum(["1", "2"], population=1)], {"1": _ok(), "2": _ok()}),
            _corpus([_stratum(["1"], population=True)], {"1": _ok()}),
        ],
        ids=[
            "not-an-object",
            "no-header",
            "no-records",
            "unknown-status",
            "unhashable-status",
            "ok-without-signals",
            "hole-with-signals",
            "text-kept",
            "string-types",
            "bad-signals",
            "no-strata",
            "unknown-stratum-status",
            "repeated-pmid",
            "unrecorded-pmid",
            "failed-yet-drew",
            "drew-past-the-population",
            "bool-population",
        ],
    )
    def test_a_corpus_this_script_cannot_write_is_refused(self, tmp_path, corpus):
        path = tmp_path / "c.json"
        path.write_text(json.dumps(corpus))
        with pytest.raises(ValueError, match=str(path)):
            sampler.load_corpus(path)


class TestATextMoveIsNotACodeMove:
    """A record whose text changed upstream is counted apart from what the code moved."""

    def test_the_kinds_are_kept_apart(self):
        moved = {"sample_size": 20, "power": True, "ci": True, "study_type": "rct"}
        old = {
            "1": _ok(10),
            "2": _ok(10),
            "3": _ok(10),
            "4": {"status": "absent"},
            "5": {"status": "absent"},
        }
        new = {
            "1": _ok(10) | {"signals": moved},
            "2": _ok(20) | {"text_sha256": "other"},
            "3": {"status": "failed"},
            "4": _ok(10),
            "5": {"status": "no-abstract"},
        }
        comparison = sampler.compare(old, new)
        assert comparison.compared == ["1"]
        # Named, not read off `SIGNAL_NAMES`: a signal dropped from that tuple
        # would drop out of an assertion built from it too.
        assert comparison.moves == {
            "sample_size": ["1"],
            "power": ["1"],
            "ci": ["1"],
            "study_type": ["1"],
        }
        assert comparison.text_moved == ["2"]
        assert comparison.one_side == ["3", "4"]
        assert comparison.neither_side == ["5"]
        lines = sampler.report_comparison(comparison)
        assert lines[0] == (
            "Compared 1 records. Not compared: text moved upstream in 1, measured on one "
            "side only in 2, on neither side in 1"
        )
        assert lines[1:5] == [
            "  sample_size  moved in 1: 1",
            "  power        moved in 1: 1",
            "  ci           moved in 1: 1",
            "  study_type   moved in 1: 1",
        ]

    def test_a_publication_type_move_is_reported_apart(self):
        old = {"1": _ok(10, publication_types=["Clinical Trial"])}
        new = {"1": _ok(10, publication_types=["Randomized Controlled Trial", "Clinical Trial"])}
        comparison = sampler.compare(old, new)
        assert (comparison.compared, comparison.types_moved) == (["1"], ["1"])
        assert "  publication types moved in 1: 1" in sampler.report_comparison(comparison)

    def test_a_reordering_of_the_types_is_not_a_move(self):
        old = {"1": _ok(10, publication_types=["A", "B"])}
        new = {"1": _ok(10, publication_types=["B", "A"])}
        assert sampler.compare(old, new).types_moved == []

    def test_a_stored_signal_of_the_wrong_type_is_refused_not_equated(self):
        stored = {"sample_size": 10, "power": 1, "ci": False, "study_type": "unknown"}
        old = {"1": _ok(10) | {"signals": stored}}
        with pytest.raises(ValueError):
            sampler.compare(old, {"1": _ok(10)})

    def test_an_empty_comparison_reports(self):
        assert sampler.report_comparison(sampler.Comparison())[1] == "  sample_size  moved in 0"


class _ClientContext:
    """Stands in for ``_client(email)``: a context manager yielding a fake client."""

    def __init__(self, client: _Client) -> None:
        self.client = client

    def __call__(self, email: str) -> _ClientContext:
        return self

    def __enter__(self) -> _Client:
        return self.client

    def __exit__(self, *exc: object) -> bool:
        return False


def _live(monkeypatch: pytest.MonkeyPatch, *answers: _Response | BaseException) -> _Client:
    """Install a fake client and a pacer that never waits, for a live command."""
    client = _Client(*answers)
    monkeypatch.setattr(sampler, "_client", _ClientContext(client))
    monkeypatch.setattr(sampler, "_make_pacer", lambda interval: _no_pace)
    return client


class TestRemeasureRewritesTheCorpusHonestly:
    """``remeasure --write`` is the command that rewrites committed evidence."""

    TEXT = "In this randomized controlled trial we enrolled 1,240 patients."

    def _write(self, tmp_path: Path) -> Path:
        row = sampler.record_row(sampler.Fetched("ok", self.TEXT, ("Clinical Trial",)))
        # A stale signal: what an older extractors.py might have said.
        row["signals"] = row["signals"] | {"sample_size": 999}
        corpus = {
            "header": {"population": "abstracts", "extractors_sha256": "stale", "drawn_on": "d0"},
            "strata": [_stratum(["1"], population=1)],
            "records": {"1": row},
        }
        path = tmp_path / "abstracts.json"
        path.write_text(sampler.dumps_corpus(corpus))
        return path

    def _write_many(self, tmp_path: Path, n: int) -> Path:
        pmids = [str(i) for i in range(1, n + 1)]
        row = sampler.record_row(sampler.Fetched("ok", self.TEXT, ("Clinical Trial",)))
        corpus = {
            "header": {"population": "abstracts", "extractors_sha256": "stale"},
            "strata": [_stratum(pmids, population=n)],
            "records": {p: row for p in pmids},
        }
        path = tmp_path / "abstracts.json"
        path.write_text(sampler.dumps_corpus(corpus))
        return path

    def _run(self, monkeypatch, path: Path, *answers: _Response, write: bool = True) -> int:
        _live(monkeypatch, *answers)
        argv = ["remeasure", "--email", "t@example.org", str(path)] + (["--write"] if write else [])
        return sampler.main(argv)

    def test_the_rewritten_corpus_holds_the_fresh_signals_under_the_new_digest(
        self, monkeypatch, tmp_path, capsys
    ):
        path = self._write(tmp_path)
        answer = _core(_record("1", self.TEXT, ["Clinical Trial"]))
        assert self._run(monkeypatch, path, answer) == 0
        corpus = sampler.load_corpus(path)
        assert corpus["records"]["1"]["signals"]["sample_size"] == 1240
        assert corpus["header"]["extractors_sha256"] == sampler.extractors_digest()
        assert corpus["header"]["drawn_on"] == "d0"
        assert corpus["strata"] == [_stratum(["1"], population=1)]
        out = capsys.readouterr().out
        assert "  sample_size  moved in 1: 1" in out
        assert "  publication types moved in 0" in out

    def test_a_publication_type_move_is_announced(self, monkeypatch, tmp_path, capsys):
        path = self._write(tmp_path)
        answer = _core(_record("1", self.TEXT, ["Clinical Trial", "Randomized Controlled Trial"]))
        assert self._run(monkeypatch, path, answer, write=False) == 0
        assert "  publication types moved in 1: 1" in capsys.readouterr().out

    def test_a_failed_lookup_leaves_the_corpus_untouched(self, monkeypatch, tmp_path, capsys):
        path = self._write(tmp_path)
        before = path.read_text()
        assert self._run(monkeypatch, path, _Response(500)) == 1
        assert path.read_text() == before
        out = capsys.readouterr().out
        assert "1 lookups failed; the diff is incomplete" in out
        assert "not written" in out

    def test_a_failed_lookup_fails_the_run_without_write_too(self, monkeypatch, tmp_path, capsys):
        # "moved in 0" over a thinned set is not a verdict.
        path = self._write(tmp_path)
        assert self._run(monkeypatch, path, _Response(500), write=False) == 1
        assert "the diff is incomplete" in capsys.readouterr().out

    def test_without_write_nothing_is_written(self, monkeypatch, tmp_path):
        path = self._write(tmp_path)
        before = path.read_text()
        answer = _core(_record("1", self.TEXT))
        assert self._run(monkeypatch, path, answer, write=False) == 0
        assert path.read_text() == before

    def test_absences_past_the_threshold_are_diverted_not_written(self, monkeypatch, tmp_path):
        # Europe PMC's own answer, so not refused like a failure; but a stratum
        # past the threshold is unreportable, and the committed name keeps its
        # evidence.
        path = self._write_many(tmp_path, 5)
        before = path.read_text()
        answer = _core(*[_record(p, self.TEXT, ["Clinical Trial"]) for p in ("1", "2", "3")])
        assert self._run(monkeypatch, path, answer) == 1
        assert path.read_text() == before
        diverted = sampler.load_corpus(tmp_path / "abstracts.unreportable.json")
        assert [diverted["records"][p]["status"] for p in ("4", "5")] == ["absent", "absent"]

    def test_absences_under_the_threshold_are_written(self, monkeypatch, tmp_path):
        path = self._write_many(tmp_path, 10)
        pmids = [str(i) for i in range(1, 10)]
        answer = _core(*[_record(p, self.TEXT, ["Clinical Trial"]) for p in pmids])
        assert self._run(monkeypatch, path, answer) == 0
        assert sampler.load_corpus(path)["records"]["10"] == {"status": "absent"}

    def test_a_fulltext_corpus_is_refused(self, monkeypatch, tmp_path):
        path = tmp_path / "fulltext.json"
        path.write_text(sampler.dumps_corpus({"header": {"population": "fulltext"}, "records": {}}))
        assert self._run(monkeypatch, path) == 2


_DRAWN = sampler.Stratum("Observational Study", 2014)
_UNDRAWABLE = sampler.Stratum("Case Reports", 2023)
_POPULATION = [str(n) for n in range(100, 110)]


def _draw_script(failing: bool) -> tuple[list[_Response], list[str]]:
    """Answers for a two-stratum draw of 3 a stratum, and the PMIDs it samples."""
    chosen = sampler.draw_sample(_POPULATION, 3, 0, _DRAWN)
    walk = [_idlist(10, _POPULATION, "*")]
    failed = [_Response(500)] * sampler.WALK_ATTEMPTS if failing else []
    lookup = [_core(*[_record(p, "An abstract.", ["Observational Study"]) for p in chosen])]
    return walk + failed + lookup, chosen


class TestTheDrawIsWhatTheCorpusRecords:
    """``draw_abstracts`` is the function that wrote the committed corpus."""

    def test_each_stratum_is_sampled_and_a_failed_one_filed(self):
        answers, chosen = _draw_script(failing=True)
        corpus = sampler.draw_abstracts(
            _Client(*answers), _no_pace, 3, 0, "random", [_DRAWN, _UNDRAWABLE]
        )
        drawn, failed = corpus["strata"]
        assert drawn == {
            "publication_type": "Observational Study",
            "year": 2014,
            "status": "ok",
            "population": 10,
            "population_sha256": sampler.Population(10, _POPULATION).digest,
            "walks": 1,
            "walk_faults": [],
            "pmids": chosen,
        }
        assert chosen != _POPULATION[:3]  # sampled, not the head of the population
        assert failed["status"] == "failed"
        assert failed["pmids"] == []
        assert failed["reason"] == f"after {sampler.WALK_ATTEMPTS} walks the union holds nothing"
        assert sorted(corpus["records"], key=int) == chosen
        assert corpus["header"]["seed"] == 0
        assert corpus["header"]["draw"] == "random"

    def test_a_relevance_draw_records_no_population_digest_and_no_seed(self):
        page = _idlist(10, ["105", "101", "109"], None)
        lookup = _core(
            *[_record(p, "An abstract.", ["Observational Study"]) for p in ("101", "105", "109")]
        )
        corpus = sampler.draw_abstracts(
            _Client(page, lookup), _no_pace, 3, 0, "relevance", [_DRAWN]
        )
        (stratum,) = corpus["strata"]
        assert stratum["population_sha256"] is None
        assert stratum["pmids"] == ["105", "101", "109"]
        assert "walks" not in stratum
        assert corpus["header"]["seed"] is None


class TestTheAbstractsCommandWritesWhereItSays:
    def _strata(self, monkeypatch, *strata: Any) -> None:
        monkeypatch.setattr(sampler, "all_strata", lambda: list(strata))

    def test_an_unreportable_draw_is_diverted_and_exits_1(self, monkeypatch, tmp_path):
        answers, _ = _draw_script(failing=True)
        _live(monkeypatch, *answers)
        self._strata(monkeypatch, _DRAWN, _UNDRAWABLE)
        out = tmp_path / "x.json"
        argv = ["abstracts", "--email", "t@example.org", "--per-stratum", "3", "-o", str(out)]
        assert sampler.main(argv) == 1
        assert not out.exists()
        assert sampler.load_corpus(tmp_path / "x.unreportable.json")["strata"][1]["status"] == (
            "failed"
        )

    def test_the_committed_draw_is_written_to_the_default_path(self, monkeypatch, tmp_path):
        answers, chosen = _draw_script(failing=False)
        _live(monkeypatch, *answers)
        self._strata(monkeypatch, _DRAWN)
        default = tmp_path / "extractor_abstracts.json"
        monkeypatch.setattr(sampler, "DEFAULT_ABSTRACTS_OUTPUT", default)
        monkeypatch.setattr(sampler, "DEFAULT_PER_STRATUM", 3)
        assert sampler.main(["abstracts", "--email", "t@example.org"]) == 0
        assert sampler.load_corpus(default)["strata"][0]["pmids"] == chosen

    @pytest.mark.parametrize(
        "flags",
        [["--draw", "relevance"], ["--seed", "1"], ["--per-stratum", "10"]],
        ids=["relevance", "seed", "per-stratum"],
    )
    def test_any_other_draw_is_refused_at_the_default_path_however_spelled(self, flags, capsys):
        spelled = (
            sampler.DEFAULT_ABSTRACTS_OUTPUT.parent / ".." / "data" / "extractor_abstracts.json"
        )
        argv = ["abstracts", "--email", "t@example.org", *flags, "-o", str(spelled)]
        assert sampler.main(argv) == 2
        assert "must name its own -o" in capsys.readouterr().out

    def test_a_draw_of_nothing_a_stratum_is_refused(self, tmp_path, capsys):
        argv = ["abstracts", "--email", "t@example.org", "--per-stratum", "0"]
        assert sampler.main(argv + ["-o", str(tmp_path / "x.json")]) == 2
        assert "--per-stratum must be at least 1" in capsys.readouterr().out


class TestTheMeasuredCodeIsThisCheckouts:
    """A corpus stamped with this file's digest must have been measured by it."""

    def test_the_imported_extractors_are_this_checkouts(self):
        # Fails, rightly, when the suite runs on another checkout's install:
        # every other test here would then be measuring that checkout's code.
        assert sampler.imported_extractors_path() == sampler.EXTRACTORS_PATH.resolve()
        assert sampler.foreign_extractors() is None

    def test_a_measuring_command_refuses_another_checkouts_extractors(
        self, monkeypatch, tmp_path, capsys
    ):
        other = tmp_path / "bmlib" / "quality" / "extractors.py"
        monkeypatch.setattr(sampler, "EXTRACTORS_PATH", other)
        assert sampler.main(["fulltext", str(tmp_path / "bundle.xml.gz")]) == 2
        assert "was imported from" in capsys.readouterr().out

    def test_the_offline_report_does_not_need_them(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sampler, "EXTRACTORS_PATH", tmp_path / "extractors.py")
        path = tmp_path / "abstracts.json"
        path.write_text(sampler.dumps_corpus(_corpus([_stratum(["1"])], {"1": _ok(5)})))
        assert sampler.main(["report", str(path)]) == 0

    def test_the_digest_is_of_the_imported_file(self):
        expected = hashlib.sha256(sampler.imported_extractors_path().read_bytes()).hexdigest()
        assert sampler.extractors_digest() == expected


class TestTheReportCommand:
    def test_report_reprints_and_exits_on_reportability(self, tmp_path, capsys):
        path = tmp_path / "abstracts.json"
        path.write_text(sampler.dumps_corpus(_corpus([_stratum(["1"])], {"1": _ok(size=50)})))
        assert sampler.main(["report", str(path)]) == 0
        assert "sample size found  1/1 " in capsys.readouterr().out
        path.write_text(sampler.dumps_corpus(_corpus([_failed_stratum()], {})))
        assert sampler.main(["report", str(path)]) == 1


ARTICLE = (
    '<article><front><article-meta><article-id pub-id-type="pmcid">PMC123</article-id>'
    "<abstract><p>Alpha&amp;beta</p></abstract><trans-abstract><p>Gamma</p></trans-abstract>"
    "</article-meta></front><body><p>Delta<sub>2</sub> p &lt; 0.05 and q &gt; 1</p></body>"
    "<sub-article><body><p>Reviewer</p></body></sub-article></article>"
)


def _bundle(tmp_path: Path, xml: str, name: str = "bundle.xml.gz") -> Path:
    path = tmp_path / name
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(f"<articles>{xml}</articles>")
    return path


def _long(pmcid: str, words: int = 200, attribute: str = "pmcid") -> str:
    """An article whose stripped text clears the threshold."""
    return ARTICLE.replace("Delta", "Delta " * words).replace(
        'pub-id-type="pmcid">PMC123', f'pub-id-type="{attribute}">{pmcid}'
    )


class TestTheFullTextIsStrippedAsTheAuditStrippedIt:
    def test_every_abstract_then_every_body_tags_to_spaces_then_entities(self):
        # Exact: an `in` check let the body ahead of the abstract through, and
        # decoding entities before stripping tags eats `p < 0.05 and q >`.
        assert sampler.article_text(ARTICLE) == (
            "  Alpha&beta     Delta 2  p < 0.05 and q > 1     Reviewer  "
        )

    def test_a_bundle_is_split_measured_and_identified(self, tmp_path):
        bare = "<article><body><p>short</p></body></article>"
        untagged = "<article><front>no text elements at all</front></article>"
        bundle = _bundle(
            tmp_path,
            _long("PMC123") + bare + _long("PMC123") + _long("77", attribute="pmc") + untagged,
        )
        corpus = sampler.measure_bundle(bundle)
        assert corpus["header"]["articles"] == 5
        assert corpus["header"]["no_text_elements"] == 1
        assert corpus["header"]["artifact"] == "bundle.xml.gz"
        assert sorted(corpus["records"]) == ["PMC123", "PMC123#2", "PMC77"]
        text = sampler.article_text(_long("PMC123"))
        assert corpus["records"]["PMC123"] == {
            "status": "ok",
            "text_sha256": sampler.text_digest(text),
            "text_chars": len(text),
            "signals": sampler.measure({"full_text": text}).to_dict(),
        }

    def test_a_bundle_is_measured_as_full_text(self, tmp_path, monkeypatch):
        # The shape a caller passes for an article. As an `abstract` the same
        # text reaches the extractors with one trailing space, which no output
        # in the suite tells apart, so the shape is pinned at the call.
        seen: list[dict[str, str]] = []

        def _record_shape(document: dict[str, str]) -> Any:
            seen.append(document)
            return sampler.Signals(None, False, False, "unknown")

        monkeypatch.setattr(sampler, "measure", _record_shape)
        sampler.measure_bundle(_bundle(tmp_path, _long("PMC123")))
        assert seen == [{"full_text": sampler.article_text(_long("PMC123"))}]

    def test_an_article_without_a_pmcid_is_identified_by_its_position(self, tmp_path):
        anonymous = _long("PMC123").replace(
            '<article-id pub-id-type="pmcid">PMC123</article-id>', ""
        )
        corpus = sampler.measure_bundle(_bundle(tmp_path, _long("PMC1") + anonymous))
        assert sorted(corpus["records"]) == ["PMC1", "article_1"]

    def test_the_threshold_keeps_an_article_of_exactly_its_length(self, tmp_path):
        filler = "x" * (sampler.MIN_FULLTEXT_CHARS - 2)
        exact = f"<article><body>{filler}</body></article>"
        assert len(sampler.article_text(exact)) == sampler.MIN_FULLTEXT_CHARS
        shorter = f"<article><body>{filler[:-1]}</body></article>"
        corpus = sampler.measure_bundle(_bundle(tmp_path, exact + shorter))
        assert list(corpus["records"]) == ["article_0"]

    def test_a_welded_bundle_is_refused(self, tmp_path):
        path = _bundle(tmp_path, "<article><body>x</body><article><body>y</body></article>")
        with pytest.raises(ValueError, match="openers against"):
            list(sampler.bundle_articles(path))

    def test_an_article_opened_inside_another_is_refused(self, tmp_path):
        # Openers and closers agree; the split yields one article of two.
        nested = "<article><body>x<article><body>y</body></article></body></article>"
        with pytest.raises(ValueError, match="split into 1 articles"):
            list(sampler.bundle_articles(_bundle(tmp_path, nested)))

    def test_a_bundle_holding_no_article_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="no <article>"):
            list(sampler.bundle_articles(_bundle(tmp_path, "")))


class TestTheFulltextCommand:
    def _baseline(self, tmp_path: Path, bundle: Path, artifact: str) -> Path:
        corpus = sampler.measure_bundle(bundle)
        corpus["header"]["artifact"] = artifact
        row = corpus["records"]["PMC123"]
        row["signals"] = row["signals"] | {"sample_size": 999}  # a stale signal
        path = tmp_path / "baseline.json"
        path.write_text(sampler.dumps_corpus(corpus))
        return path

    def test_the_baseline_reports_what_moved(self, tmp_path, capsys):
        bundle = _bundle(tmp_path, _long("PMC123"))
        baseline = self._baseline(tmp_path, bundle, "bundle.xml.gz")
        before = baseline.read_text()
        assert sampler.main(["fulltext", str(bundle), "-o", str(baseline), "--baseline"]) == 0
        out = capsys.readouterr().out
        assert "  sample_size  moved in 1: PMC123" in out
        assert "  carrying neither an <abstract> nor a <body> the rule matches: 0" in out
        assert baseline.read_text() == before

    def test_write_rewrites_the_corpus_after_printing_the_moves(self, tmp_path, capsys):
        bundle = _bundle(tmp_path, _long("PMC123"))
        baseline = self._baseline(tmp_path, bundle, "bundle.xml.gz")
        assert sampler.main(["fulltext", str(bundle), "-o", str(baseline), "--write"]) == 0
        assert "  sample_size  moved in 1: PMC123" in capsys.readouterr().out
        assert sampler.load_corpus(baseline) == sampler.measure_bundle(bundle)

    def test_write_to_a_new_path_writes(self, tmp_path):
        bundle = _bundle(tmp_path, _long("PMC123"))
        out = tmp_path / "new.json"
        assert sampler.main(["fulltext", str(bundle), "-o", str(out), "--write"]) == 0
        assert sorted(sampler.load_corpus(out)["records"]) == ["PMC123"]

    @pytest.mark.parametrize("flag", ["--baseline", "--write"])
    def test_a_corpus_of_another_artifact_is_neither_compared_nor_replaced(
        self, tmp_path, capsys, flag
    ):
        bundle = _bundle(tmp_path, _long("PMC123"))
        baseline = self._baseline(tmp_path, bundle, "other.xml.gz")
        before = baseline.read_text()
        assert sampler.main(["fulltext", str(bundle), "-o", str(baseline), flag]) == 2
        assert "nothing to compare" in capsys.readouterr().out
        assert baseline.read_text() == before

    def test_the_report_names_the_artifact_and_the_signals(self, tmp_path, capsys):
        bundle = _bundle(tmp_path, _long("PMC123"))
        out = tmp_path / "ft.json"
        assert sampler.main(["fulltext", str(bundle), "-o", str(out), "--write"]) == 0
        capsys.readouterr()
        assert sampler.main(["report", str(out)]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == (
            f"bundle.xml.gz: 1 articles, 1 with at least {sampler.MIN_FULLTEXT_CHARS} "
            "characters of abstract and body"
        )
        assert lines[-1].startswith("  study type: ")

    def test_a_corpus_measured_before_the_unmatched_count_says_so(self):
        corpus = {
            "header": {"artifact": "a.xml.gz", "articles": 0, "min_chars": 500},
            "records": {},
        }
        assert sampler.report_fulltext(corpus)[1] == (
            "  carrying neither an <abstract> nor a <body> the rule matches: not counted"
        )


# ---- The committed corpora ----

_ABSTRACTS = sampler.DEFAULT_ABSTRACTS_OUTPUT
_FULLTEXT = sampler.DEFAULT_FULLTEXT_OUTPUT


@pytest.mark.parametrize("path", [_ABSTRACTS, _FULLTEXT], ids=["abstracts", "fulltext"])
def test_the_committed_corpus_was_measured_by_these_extractors(path):
    """A change to ``extractors.py`` fails here until the corpus is re-measured.

    That is the point of #368: the next change has an instrument to re-run, and
    a rule enforced by prose is not enforced. The digest is of the file's raw
    bytes, so a comment-only edit counts. Re-measure, read the moves the
    script prints, and commit the rewritten corpus with them:

        uv run python scripts/sample_extractor_signals.py remeasure --email … --write
        uv run python scripts/sample_extractor_signals.py fulltext BUNDLE --baseline --write
    """
    header = sampler.load_corpus(path)["header"]
    assert header["extractors_sha256"] == sampler.extractors_digest(sampler.EXTRACTORS_PATH), (
        f"{path.name} was measured by another extractors.py; re-measure it"
    )


def test_the_committed_abstract_corpus_is_a_reportable_random_draw():
    corpus = sampler.load_corpus(_ABSTRACTS)
    header = corpus["header"]
    assert header["draw"] == "random"
    assert header["seed"] == sampler.DEFAULT_SEED
    assert header["per_stratum"] == sampler.DEFAULT_PER_STRATUM
    assert header["query_template"] == sampler.QUERY_TEMPLATE
    assert [(s["publication_type"], s["year"]) for s in corpus["strata"]] == [
        (s.publication_type, s.year) for s in sampler.all_strata()
    ]
    for stratum in corpus["strata"]:
        assert stratum["status"] == "ok"
        assert len(stratum["pmids"]) == min(header["per_stratum"], stratum["population"])
        # Which population was sampled, not only how large it was.
        assert len(stratum["population_sha256"]) == 64
    _, reportable = sampler.report_abstracts(corpus)
    assert reportable


@pytest.mark.parametrize("path", [_ABSTRACTS, _FULLTEXT], ids=["abstracts", "fulltext"])
def test_the_committed_corpus_keeps_no_text(path):
    # `load_corpus` refuses a row carrying any key but these, a measured row
    # lacking any of the last three, and a stored signal no extractor returns.
    for row in sampler.load_corpus(path)["records"].values():
        assert set(row) <= {"status", "publication_types", "text_sha256", "text_chars", "signals"}


def test_the_committed_fulltext_corpus_is_the_audits_population():
    corpus = sampler.load_corpus(_FULLTEXT)
    header = corpus["header"]
    assert header["artifact"] == "PMC10030002_PMC10040000.xml.gz"
    assert header["articles"] == 8118
    assert header["min_chars"] == sampler.MIN_FULLTEXT_CHARS
    assert len(corpus["records"]) == 7410
    # Of the 708 excluded, 634 carry no `<abstract>` or `<body>` the rule
    # matches; the threshold itself drops the other 74 on length.
    assert header["no_text_elements"] == 634
