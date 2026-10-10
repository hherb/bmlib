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
move; and the committed corpora were measured by the extractors in this
checkout.

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
    """

    def _refuse(email: str) -> Any:
        raise AssertionError("an offline test reached the network")

    monkeypatch.setattr(sampler, "_client", _refuse)


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

    def __init__(self, *script: _Response | Exception) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any]) -> _Response:
        assert url == sampler.SEARCH_URL
        self.requests.append(dict(params))
        answer = self.script.pop(0)
        if isinstance(answer, Exception):
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


def _core(*records: dict[str, Any]) -> _Response:
    """One core page."""
    return _Response(200, {"hitCount": len(records), "resultList": {"result": list(records)}})


def _record(pmid: str, abstract: Any = "An abstract.", types: Any = None) -> dict[str, Any]:
    """One core record."""
    record: dict[str, Any] = {"id": pmid, "source": "MED", "abstractText": abstract}
    if types is not None:
        record["pubTypeList"] = {"pubType": types}
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


class TestThePopulationIsWalkedWhole:
    """A sample drawn from part of a population reads like one drawn from all of it."""

    def test_the_walk_follows_the_cursor_to_its_end(self):
        client = _Client(
            _idlist(3, ["1", "2"], "c1"),
            _idlist(3, ["3"], "c2"),
            _idlist(3, [], "c2"),
        )
        population = sampler.enumerate_population(client, _STRATUM, _no_pace)
        assert population.pmids == ["1", "2", "3"]
        assert population.hit_count == 3
        assert [r["cursorMark"] for r in client.requests] == ["*", "c1", "c2"]
        assert client.requests[0]["query"] == _STRATUM.query

    def test_a_cursor_that_does_not_advance_ends_the_walk(self):
        client = _Client(_idlist(2, ["1", "2"], "*"))
        assert sampler.enumerate_population(client, _STRATUM, _no_pace).pmids == ["1", "2"]

    def test_a_clean_first_walk_is_taken_at_once(self):
        client = _Client(_idlist(2, ["2", "1"], "*"))
        population, walks = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (population.pmids, walks) == (["1", "2"], 1)

    def test_faulty_walks_reconcile_as_a_union(self):
        # Each walk repeats one record and so misses one; together they hold all three.
        client = _Client(_idlist(3, ["1", "2", "2"], "*"), _idlist(3, ["1", "3", "3"], "*"))
        population, walks = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (population.pmids, walks) == (["1", "2", "3"], 2)

    def test_a_failed_walk_spends_an_attempt_and_adds_nothing(self):
        client = _Client(_Response(500), _idlist(2, ["1", "2"], "*"))
        population, walks = sampler.walk_until_reconciled(client, _STRATUM, _no_pace)
        assert (population.pmids, walks) == (["1", "2"], 2)

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

    def test_a_missing_next_cursor_ends_the_walk(self):
        client = _Client(_idlist(2, ["1", "2"], None))
        assert sampler.enumerate_population(client, _STRATUM, _no_pace).pmids == ["1", "2"]
        assert len(client.requests) == 1

    def test_a_short_walk_fails_the_stratum(self):
        client = _Client(_idlist(5, ["1", "2"], "c1"), _idlist(5, [], "c1"))
        with pytest.raises(sampler.RequestFailedError, match="delivered 2 of 5"):
            sampler.enumerate_population(client, _STRATUM, _no_pace)

    def test_a_population_that_moves_fails_the_stratum(self):
        client = _Client(_idlist(3, ["1", "2"], "c1"), _idlist(4, ["3"], "c2"))
        with pytest.raises(sampler.RequestFailedError, match="moved"):
            sampler.enumerate_population(client, _STRATUM, _no_pace)

    def test_a_repeated_record_fails_the_stratum(self):
        client = _Client(_idlist(2, ["1"], "c1"), _idlist(2, ["1"], "c2"), _idlist(2, [], "c2"))
        with pytest.raises(sampler.RequestFailedError, match="repeated"):
            sampler.enumerate_population(client, _STRATUM, _no_pace)

    @pytest.mark.parametrize(
        "record",
        [
            {"id": "PPR1", "source": "PPR"},
            {"id": "12345", "source": "AGR"},
            {"id": 12345, "source": "MED"},
        ],
        ids=["preprint", "numeric-id-from-another-source", "integer-id"],
    )
    def test_a_record_without_a_pmid_fails_the_stratum(self, record):
        body = {"hitCount": 1, "resultList": {"result": [record]}}
        with pytest.raises(sampler.RequestFailedError, match="no PMID"):
            sampler.enumerate_population(_Client(_Response(200, body)), _STRATUM, _no_pace)

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
            "raised",
        ],
    )
    def test_an_unreadable_page_fails_rather_than_reading_as_empty(self, answer):
        with pytest.raises(sampler.RequestFailedError):
            sampler.enumerate_population(_Client(answer), _STRATUM, _no_pace)

    def test_an_instrument_defect_is_raised_not_filed_as_a_hole(self):
        # A hole under the threshold exits 0; this script being wrong must not.
        with pytest.raises(TypeError):
            sampler.enumerate_population(_Client(TypeError("bad call")), _STRATUM, _no_pace)

    def test_the_population_digest_names_the_set_not_the_count(self):
        a = sampler.Population(2, ["10", "9"])
        assert a.digest == hashlib.sha256(b"9,10").hexdigest()
        assert a.digest != sampler.Population(2, ["9", "11"]).digest

    def test_a_throttled_page_is_retried(self, monkeypatch):
        waits: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", waits.append)
        client = _Client(_Response(429), _idlist(1, ["7"], "*"))
        assert sampler.enumerate_population(client, _STRATUM, _no_pace).pmids == ["7"]
        assert waits == [2.0]

    def test_a_page_throttled_on_every_attempt_fails(self, monkeypatch):
        monkeypatch.setattr(sampler, "_sleep_for", lambda _: None)
        client = _Client(_Response(429), _Response(429), _Response(503))
        with pytest.raises(sampler.RequestFailedError, match="HTTP 503"):
            sampler.enumerate_population(client, _STRATUM, _no_pace)


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

    def test_it_is_numerically_sorted_and_the_right_size(self):
        drawn = sampler.draw_sample(["9", "10", "100", "11"], 3, 0, _STRATUM)
        assert len(drawn) == 3
        assert drawn == sorted(drawn, key=int)

    def test_a_population_smaller_than_the_draw_is_taken_whole(self):
        assert sampler.draw_sample(["3", "1", "2"], 300, 0, _STRATUM) == ["1", "2", "3"]

    def test_a_short_relevance_page_fails(self):
        with pytest.raises(sampler.RequestFailedError, match="delivered 2 of 3"):
            sampler.relevance_page(_Client(_idlist(10, ["5", "2"], None)), _STRATUM, 3, _no_pace)

    def test_the_relevance_draw_keeps_the_remotes_order(self):
        client = _Client(_idlist(10, ["5", "2", "9"], None))
        population = sampler.relevance_page(client, _STRATUM, 3, _no_pace)
        assert population.pmids == ["5", "2", "9"]
        assert client.requests[0]["pageSize"] == 3
        assert "cursorMark" not in client.requests[0]


class TestEveryHoleIsNamed:
    """A failed, absent or abstract-less record is a hole, and each kind is kept apart."""

    def test_each_outcome_is_classified(self):
        client = _Client(
            _core(
                _record("1", "Randomised.", ["Randomized Controlled Trial"]),
                _record("2", "   "),
                _record("3", None),
                _record("5", "Single type.", "Case Reports"),
            )
        )
        fetched = sampler.fetch_abstracts(client, ["1", "2", "3", "4", "5"], _no_pace)
        assert fetched["1"] == sampler.Fetched(
            "ok", "Randomised.", ("Randomized Controlled Trial",)
        )
        assert fetched["2"].status == "no-abstract"
        assert fetched["3"].status == "no-abstract"
        assert fetched["4"].status == "absent"
        assert fetched["5"].publication_types == ("Case Reports",)
        assert client.requests[0]["query"] == "EXT_ID:(1 OR 2 OR 3 OR 4 OR 5) AND SRC:MED"

    def test_a_page_cut_short_is_a_failure_not_an_absence(self):
        # Counted 3, delivered 2: the missing PMID is this run's hole, and
        # `remeasure --write` would write an `absent` row over a measured one.
        short = _Response(
            200, {"hitCount": 3, "resultList": {"result": [_record("1"), _record("2")]}}
        )
        fetched = sampler.fetch_abstracts(_Client(short), ["1", "2", "3"], _no_pace)
        assert fetched["3"].status == "failed"

    def test_a_failed_batch_costs_only_its_own_records(self):
        client = _Client(_Response(500), _core(_record("3")))
        fetched = sampler.fetch_abstracts(client, ["1", "2", "3"], _no_pace, batch=2)
        assert [fetched[p].status for p in ("1", "2", "3")] == ["failed", "failed", "ok"]


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
        assert row["text_sha256"] == hashlib.sha256(self.ABSTRACT.encode()).hexdigest()
        assert row["text_chars"] == len(self.ABSTRACT)
        assert row["signals"] == {
            "sample_size": 1240,
            "power": True,
            "ci": True,
            "study_type": "rct",
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
            get_extracted_study_type(extract_study_type(document)) or "unknown",
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

    @pytest.mark.parametrize(
        "bad",
        [
            {"sample_size": True, "power": False, "ci": False, "study_type": "rct"},
            {"sample_size": "12", "power": False, "ci": False, "study_type": "rct"},
            {"sample_size": None, "power": 1, "ci": False, "study_type": "rct"},
            {"sample_size": None, "power": False, "ci": False, "study_type": ""},
        ],
    )
    def test_a_mistyped_stored_signal_is_refused(self, bad):
        with pytest.raises(ValueError):
            sampler.Signals.from_dict(bad)


def _corpus(strata: list[dict[str, Any]], records: dict[str, Any]) -> dict[str, Any]:
    return {"header": {"population": "abstracts"}, "strata": strata, "records": records}


def _ok(size: int | None = None, power: bool = False, ci: bool = False) -> dict[str, Any]:
    signals = {"sample_size": size, "power": power, "ci": ci, "study_type": "unknown"}
    return {"status": "ok", "text_sha256": "h", "text_chars": 1, "signals": signals}


def _stratum(pmids: list[str], population: int = 1000) -> dict[str, Any]:
    return {
        "publication_type": "Clinical Trial",
        "year": 2014,
        "status": "ok",
        "population": population,
        "pmids": pmids,
    }


class TestAnUnmeasuredRecordEntersNoDenominator:
    """And a stratum with too many holes prints ERROR, not a share."""

    def test_a_hole_is_counted_and_left_out(self):
        records = {str(i): _ok(size=100) for i in range(9)} | {"9": {"status": "absent"}}
        lines, reportable = sampler.report_abstracts(_corpus([_stratum(list(records))], records))
        text = "\n".join(lines)
        assert reportable
        assert "unmeasured 1 (absent 1)" in text
        assert "sample size found  9/9 " in text

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
            str(i): {"status": "absent"} for i in range(8, 10)
        }
        lines, reportable = sampler.report_abstracts(_corpus([_stratum(list(records))], records))
        assert reportable
        assert "    sample size found  8/8 " in "\n".join(lines)

    def test_a_status_on_neither_side_of_the_partition_is_refused(self):
        records = {"1": {"status": "throttled"}}
        with pytest.raises(ValueError, match="does not know"):
            sampler.report_abstracts(_corpus([_stratum(["1"])], records))

    def test_a_stratum_that_could_not_be_drawn_is_an_error(self):
        failed = {
            "publication_type": "Clinical Trial",
            "year": 2014,
            "status": "failed",
            "reason": "HTTP 500",
            "pmids": [],
        }
        lines, reportable = sampler.report_abstracts(_corpus([failed], {}))
        assert not reportable
        assert "ERROR — the population could not be drawn (HTTP 500)" in "\n".join(lines)

    def test_an_empty_population_is_a_finding_not_an_error(self):
        lines, reportable = sampler.report_abstracts(_corpus([_stratum([], population=0)], {}))
        assert reportable
        assert "population 0, nothing to draw" in "\n".join(lines)

    def test_the_confusion_is_over_unique_records_by_their_own_types(self):
        row = _ok() | {"publication_types": ["Clinical Trial", "Randomized Controlled Trial"]}
        row["signals"] = row["signals"] | {"study_type": "rct"}
        strata = [_stratum(["1"]), _stratum(["1"]) | {"publication_type": "Case Reports"}]
        lines, _ = sampler.report_abstracts(_corpus(strata, {"1": row}))
        assert "  rct                 1: rct 1" in lines

    def test_what_an_unlabelled_record_carries_is_shown(self):
        row = _ok() | {"publication_types": ["Clinical Trial Protocol", "Journal Article"]}
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


class TestATextMoveIsNotACodeMove:
    """A record whose text changed upstream is counted apart from what the code moved."""

    def test_the_three_kinds_are_kept_apart(self):
        old = {"1": _ok(10), "2": _ok(10), "3": _ok(10), "4": {"status": "absent"}}
        new = {
            "1": _ok(20),
            "2": _ok(20) | {"text_sha256": "other"},
            "3": {"status": "failed"},
            "4": _ok(10),
        }
        comparison = sampler.compare(old, new)
        assert comparison.compared == 1
        assert comparison.moves["sample_size"] == ["1"]
        assert comparison.text_moved == ["2"]
        assert comparison.not_compared == ["3", "4"]
        assert "sample_size  moved in 1: 1" in "\n".join(sampler.report_comparison(comparison))


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

    def _run(self, monkeypatch, path: Path, *answers: _Response, write: bool = True) -> int:
        monkeypatch.setattr(sampler, "_client", _ClientContext(_Client(*answers)))
        monkeypatch.setattr(sampler, "_make_pacer", lambda interval: _no_pace)
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
        assert "sample_size  moved in 1: 1" in capsys.readouterr().out

    def test_a_failed_lookup_leaves_the_corpus_untouched(self, monkeypatch, tmp_path, capsys):
        path = self._write(tmp_path)
        before = path.read_text()
        assert self._run(monkeypatch, path, _Response(500)) == 1
        assert path.read_text() == before
        assert "not written: 1 lookups failed" in capsys.readouterr().out

    def test_without_write_nothing_is_written(self, monkeypatch, tmp_path):
        path = self._write(tmp_path)
        before = path.read_text()
        answer = _core(_record("1", self.TEXT))
        assert self._run(monkeypatch, path, answer, write=False) == 0
        assert path.read_text() == before

    def test_a_fulltext_corpus_is_refused(self, monkeypatch, tmp_path):
        path = tmp_path / "fulltext.json"
        path.write_text(sampler.dumps_corpus({"header": {"population": "fulltext"}, "records": {}}))
        assert self._run(monkeypatch, path) == 2


class TestTheOfflineCommands:
    def test_a_baseline_of_another_artifact_is_refused(self, tmp_path, capsys):
        bundle = tmp_path / "bundle.xml.gz"
        with gzip.open(bundle, "wt", encoding="utf-8") as stream:
            stream.write("<articles></articles>")
        baseline = tmp_path / "baseline.json"
        header = {"population": "fulltext", "artifact": "other.xml.gz"}
        baseline.write_text(sampler.dumps_corpus({"header": header, "records": {}}))
        assert sampler.main(["fulltext", str(bundle), "-o", str(baseline), "--baseline"]) == 2
        assert "nothing to compare" in capsys.readouterr().out

    def test_report_reprints_and_exits_on_reportability(self, tmp_path, capsys):
        path = tmp_path / "abstracts.json"
        records = {"1": _ok(size=50)}
        path.write_text(sampler.dumps_corpus(_corpus([_stratum(["1"])], records)))
        assert sampler.main(["report", str(path)]) == 0
        assert "sample size found  1/1 " in capsys.readouterr().out
        failed = {
            "publication_type": "Clinical Trial",
            "year": 2014,
            "status": "failed",
            "reason": "HTTP 500",
            "pmids": [],
        }
        path.write_text(sampler.dumps_corpus(_corpus([failed], {})))
        assert sampler.main(["report", str(path)]) == 1


class TestTheRelevanceDrawCannotReplaceTheCorpus:
    def test_the_default_path_is_refused_however_it_is_spelled(self, capsys):
        spelled = (
            sampler.DEFAULT_ABSTRACTS_OUTPUT.parent / ".." / "data" / "extractor_abstracts.json"
        )
        argv = ["abstracts", "--email", "t@example.org", "--draw", "relevance", "-o", str(spelled)]
        assert sampler.main(argv) == 2
        assert "must name its own -o" in capsys.readouterr().out


class TestTheFullTextIsStrippedAsTheAuditStrippedIt:
    ARTICLE = (
        '<article><front><article-meta><article-id pub-id-type="pmcid">PMC123</article-id>'
        "<abstract><p>Alpha&amp;beta</p></abstract><trans-abstract><p>Gamma</p></trans-abstract>"
        "</article-meta></front><body><p>Delta<sub>2</sub></p></body>"
        "<sub-article><body><p>Reviewer</p></body></sub-article></article>"
    )

    def test_abstract_and_every_body_tags_to_spaces_entities_decoded(self):
        text = sampler.article_text(self.ARTICLE)
        assert "Alpha&beta" in text
        assert "Delta 2" in text
        assert "Reviewer" in text  # wider than the article's own text, as the audit was
        assert "Gamma" not in text  # `<trans-abstract>` is not an `<abstract>`

    def _bundle(self, tmp_path: Path, xml: str) -> Path:
        path = tmp_path / "bundle.xml.gz"
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(f"<articles>{xml}</articles>")
        return path

    def test_a_bundle_is_split_and_identified(self, tmp_path):
        long = self.ARTICLE.replace("Delta", "Delta " * 200)
        bare = "<article><body><p>short</p></body></article>"
        corpus = sampler.measure_bundle(self._bundle(tmp_path, long + bare + long))
        assert corpus["header"]["articles"] == 3
        assert corpus["header"]["artifact"] == "bundle.xml.gz"
        assert sorted(corpus["records"]) == ["PMC123", "PMC123#2"]

    def test_a_welded_bundle_is_refused(self, tmp_path):
        path = self._bundle(tmp_path, "<article><body>x</body><article><body>y</body></article>")
        with pytest.raises(ValueError, match="openers"):
            list(sampler.bundle_articles(path))


# ---- The committed corpora ----

_ABSTRACTS = sampler.DEFAULT_ABSTRACTS_OUTPUT
_FULLTEXT = sampler.DEFAULT_FULLTEXT_OUTPUT


@pytest.mark.parametrize("path", [_ABSTRACTS, _FULLTEXT], ids=["abstracts", "fulltext"])
def test_the_committed_corpus_was_measured_by_these_extractors(path):
    """A change to ``extractors.py`` fails here until the corpus is re-measured.

    That is the point of #368: the next change has an instrument to re-run, and
    a rule enforced by prose is not enforced. Re-measure, read the moves the
    script prints, and commit the rewritten corpus with them:

        uv run python scripts/sample_extractor_signals.py remeasure --email … --write
        uv run python scripts/sample_extractor_signals.py fulltext BUNDLE --baseline --write
    """
    header = sampler.load_corpus(path)["header"]
    assert header["extractors_sha256"] == sampler.extractors_digest(), (
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
        assert set(stratum["pmids"]) <= set(corpus["records"])
    _, reportable = sampler.report_abstracts(corpus)
    assert reportable


@pytest.mark.parametrize("path", [_ABSTRACTS, _FULLTEXT], ids=["abstracts", "fulltext"])
def test_the_committed_corpus_keeps_no_text(path):
    allowed = {"status", "publication_types", "text_sha256", "text_chars", "signals"}
    for row in sampler.load_corpus(path)["records"].values():
        assert set(row) <= allowed
        if row["status"] == "ok":
            sampler.Signals.from_dict(row["signals"])


def test_the_committed_fulltext_corpus_is_the_audits_population():
    header = sampler.load_corpus(_FULLTEXT)["header"]
    assert header["artifact"] == "PMC10030002_PMC10040000.xml.gz"
    assert header["articles"] == 8118
    assert header["min_chars"] == sampler.MIN_FULLTEXT_CHARS
    assert len(sampler.load_corpus(_FULLTEXT)["records"]) == 7410
