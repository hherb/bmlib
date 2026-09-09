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

"""Tests for ``scripts/sample_api_failures.py``.

The script is a live runner, but what it prints is a maintainer's evidence for
the log levels in ``bmlib/transparency/analyzer.py``, so the tables have to be
trustworthy offline. Two properties are pinned here, and they are the two the
other sampler tests pin for their own scripts:

* **An attempt that never reached an answer never prints as a finding.** A
  throttled probe is the sampler failing, not the endpoint, and a run that
  reports it as a non-200 sets a log level from its own rate limiting.
* **A zero over an absent population is not a clean result.** Nothing probed
  and nothing wrong must not print alike, because a healthy endpoint is
  exactly what an unsampled one looks like.

And one that is this script's own, because this script is the first whose
subject is *the request itself*: **it must probe what the analyzer requests.**
``TestTheSamplerProbesWhatTheAnalyzerRequests`` drives both and compares, which
is stronger than checking that the constants were imported — a restated
literal is precisely how issue #184 lived a release, and issue #194 is the
same defect in a header.

No network: every test drives the script through a fake client.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bmlib.transparency.analyzer import TransparencyAnalyzer, _user_agent

# `scripts/` is not a package — it holds runnable tools, not importable
# modules — so the module is loaded by path, with that directory on `sys.path`
# because the script imports its shared helpers from `_sampling`. Executing it
# is safe: everything below `if __name__ == "__main__"` stays unrun.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_SAMPLER_PATH = _SCRIPTS / "sample_api_failures.py"
_spec = importlib.util.spec_from_file_location("bmlib_api_failure_sampler", _SAMPLER_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the API-failure sampler from {_SAMPLER_PATH}")
sampler = importlib.util.module_from_spec(_spec)
# Registered before exec: the script's `ProbeOutcome` is a dataclass, and
# under `from __future__ import annotations` its field types are strings that
# `dataclasses` resolves through `sys.modules[cls.__module__]` — a module never
# inserted there raises `AttributeError` on a `None` lookup. Same line, same
# reason, as `tests/test_free_pdf_sampler.py`.
sys.modules[_spec.name] = sampler
_spec.loader.exec_module(sampler)


class _FakeResponse:
    """The two attributes ``probe`` and the draw read, plus headers for the clamp."""

    def __init__(self, status_code: int, payload: object = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class _ScriptedClient:
    """Answers each ``get`` from a queue, recording every request made."""

    def __init__(self, *responses):
        self._responses = list(responses)
        #: ``(url, params, headers)`` per request. Headers were not recorded
        #: until PR #195's review, which is why nothing could see that the
        #: sampler sent none where the analyzer sends
        #: ``Accept: application/json``.
        self.calls: list[tuple[str, dict, dict]] = []

    def get(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if not self._responses:
            return _FakeResponse(200, {})
        answer = self._responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def urls(self) -> list[str]:
        return [url for url, _, _ in self.calls]

    def sent(self) -> dict[str, tuple[dict, dict]]:
        """``url -> (params, headers)``, for comparing two callers request by request."""
        return {url: (params, headers) for url, params, headers in self.calls}


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Never actually wait. The retry schedule is `_sampling`'s to test."""
    monkeypatch.setattr(sampler, "_sleep_for", lambda _seconds: None)


def _pace(_url: str) -> None:
    """A pacer that does nothing, so tests do not sit through the interval."""


def _as(outcomes: list, endpoint: str) -> list:
    """The same outcomes at another endpoint, for comparing a per-endpoint rule."""
    return [
        sampler.ProbeOutcome(
            endpoint=endpoint,
            status=o.status,
            cause=o.cause,
            measured=o.measured,
            shape=(sampler.BodyShape(endpoint=endpoint, top=o.shape.top) if o.shape else None),
        )
        for o in outcomes
    ]


def _epmc_body(**record: object) -> dict:
    """One EuropePMC search body carrying one record."""
    return {"resultList": {"result": [record]}}


def _draw_page(n: int = 1) -> dict:
    """A draw page of *n* analysable records, each offering a full-text address.

    ``inEPMC`` and ``pmcid`` are load-bearing rather than decoration: without
    them no record offers an address, `probe_record` probes none, and the
    full-text table added for issue #216 reports an absent population — so
    every `main` test would exit 1 for a reason that has nothing to do with
    what it asserts. `_AlwaysClient` answers the single-record lookup with
    this same body, which is what makes one fixture serve both.
    """
    return {
        "resultList": {
            "result": [
                {
                    "doi": f"10.1/{i}",
                    "pmid": str(i),
                    "source": "MED",
                    "inEPMC": "Y",
                    "isOpenAccess": "Y",
                    "pmcid": f"PMC{i}",
                }
                for i in range(n)
            ]
        }
    }


def _probe_record(client, record, *, email: str = "a@b.c") -> list:
    """Drive ``probe_record`` where the address population is not the subject."""
    return sampler.probe_record(client, record, email, _pace, [])


def _outcome(cause: str | None) -> sampler.ProbeOutcome:
    """One outcome for the summary tests, built from its bucket alone.

    **The status and ``measured`` are derived, not passed.** They used to be,
    and this helper was building two outcomes `probe()` cannot produce — an
    ``http-404`` with no status, and a success with no status — which is the
    contradiction `ProbeOutcome.__post_init__` now refuses (PR #195's review).
    Deriving them here means a summary test cannot accidentally describe an
    event that never happens, and cannot silently disagree with the probe.
    """
    if cause is None:
        # A served outcome carries a shape, since `probe` observes every body it
        # serves — the summary tests below are about the status table, so the
        # shape is the least interesting one a body can have.
        return sampler.ProbeOutcome(
            endpoint="crossref",
            status=200,
            cause=None,
            shape=sampler.BodyShape(endpoint="crossref", top="object"),
        )
    kind, _, tail = cause.partition("-")
    status = None if kind == "exception" else int(tail)
    return sampler.ProbeOutcome(
        endpoint="crossref", status=status, cause=cause, measured=kind != "unmeasured"
    )


class TestProbeClassifiesWhatCameBack:
    """Each of the four things that can happen gets its own bucket."""

    def test_a_200_is_a_success_with_no_cause(self):
        outcome = sampler.probe(_ScriptedClient(_FakeResponse(200, {})), "crossref", "u")
        assert outcome.ok
        assert outcome.status == 200

    def test_a_non_200_is_bucketed_by_its_status(self):
        # The status is kept in the bucket name, not merged into one "failed":
        # the whole point of the draw is that a level may be earned by *one*
        # status and not by its neighbours, which is issue #191.
        outcome = sampler.probe(_ScriptedClient(_FakeResponse(404)), "openalex", "u")
        assert outcome.cause == "http-404"
        assert not outcome.ok

    def test_a_raised_request_is_kept_apart_from_a_status(self):
        client = _ScriptedClient(RuntimeError("connection reset"))
        outcome = sampler.probe(client, "crossref", "u")
        assert outcome.cause == "exception-RuntimeError"
        assert outcome.status is None

    @pytest.mark.parametrize("status", [429, 503])
    def test_throttling_is_retried_and_then_left_unmeasured(self, status):
        # Three 429s in a row: the probe never reached an answer, so it is
        # `measured=False` and enters no denominator. Reported as `http-429`
        # instead, it would be a "non-200" the endpoint never sent — the
        # sampler measuring its own pacing, which is the defect
        # `sample_free_pdf_urls.py`'s first live run actually shipped.
        client = _ScriptedClient(*[_FakeResponse(status) for _ in range(3)])
        outcome = sampler.probe(client, "crossref", "u")
        assert not outcome.measured
        assert outcome.cause == f"unmeasured-{status}"
        assert len(client.calls) == sampler.MAX_PROBE_ATTEMPTS

    def test_throttling_that_clears_is_measured(self):
        client = _ScriptedClient(_FakeResponse(429), _FakeResponse(200, {}))
        outcome = sampler.probe(client, "crossref", "u")
        assert outcome.measured
        assert outcome.ok


class TestNothingUnmeasuredPrintsAsAFinding:
    """The rule every sampler in this directory shares."""

    def test_an_empty_population_is_an_error_not_a_clean_sheet(self):
        [line] = sampler.summarise("crossref", [])
        assert "ERROR" in line
        # And it must not read as a rate: `0 non-200` over nothing probed is
        # indistinguishable from a healthy endpoint.
        assert "not served" not in line

    def test_a_mostly_throttled_population_is_an_error(self):
        # Half unmeasured, well past the threshold. The probes that got
        # through are the *early* ones, so what survived is not a random
        # sample of the population.
        outcomes = [_outcome(None) for _ in range(5)] + [
            _outcome("unmeasured-429") for _ in range(5)
        ]
        [line] = sampler.summarise("crossref", outcomes)
        assert "ERROR" in line
        assert "5/10" in line

    def test_a_reportable_population_excludes_unmeasured_from_both_numbers(self):
        # One unmeasured attempt inside the threshold: the rate is over the
        # nine that answered, not the ten attempted, and the excluded one is
        # reported on its own line rather than folded into either number.
        outcomes = [_outcome(None) for _ in range(8)] + [
            _outcome("http-404"),
            _outcome("unmeasured-503"),
        ]
        lines = sampler.summarise("crossref", outcomes)
        assert "9 probed" in lines[0]
        assert "1 not served" in lines[0]
        assert any("1 unmeasured" in line for line in lines)

    def test_is_reportable_agrees_with_what_was_printed(self):
        # One predicate behind the ERROR branches and the exit code, so a
        # caller chaining this script cannot be told something the tables
        # do not say.
        throttled = [_outcome("unmeasured-429")]
        assert not sampler.is_reportable(throttled)
        assert "ERROR" in sampler.summarise("crossref", throttled)[0]
        assert not sampler.is_reportable([])
        assert sampler.is_reportable([_outcome(None)])


class TestADrawnStratumIsNeverQuietlyReplaced:
    """A hole in the stratification is reported, not filled from elsewhere."""

    def _draw(self, *responses):
        client = _ScriptedClient(*responses)
        return client, sampler.draw_records(client, 2, _pace, (("MED", 2024), ("PMC", 2024)), "")

    def _page(self, *ids):
        return _FakeResponse(
            200,
            {"resultList": {"result": [{"doi": f"10.1/{i}", "pmid": i} for i in ids]}},
        )

    def test_a_failed_stratum_is_named_and_contributes_nothing(self):
        _client, draw = self._draw(_FakeResponse(503), self._page("a"))
        assert draw.failed_strata == ["MED/2024"]
        assert [r.source for r in draw.records] == ["PMC"]

    def test_an_empty_stratum_is_a_hole_too(self):
        # Europe PMC answering 200 with no records is not a stratum that was
        # sampled and found clean — it is a cell the sample does not cover,
        # and the tables below claim a spread it then does not have.
        _client, draw = self._draw(self._page(), self._page("a"))
        assert draw.failed_strata == ["MED/2024"]

    def test_a_stratum_is_never_back_filled_from_another(self):
        # The one that would be silent. Two strata, one dead: the survivor
        # must not be asked for twice to reach the target, which would
        # re-weight the sample without saying so.
        client, draw = self._draw(_FakeResponse(503), self._page("a", "b"))
        assert len(client.calls) == 2
        assert {r.source for r in draw.records} == {"PMC"}

    def test_an_unreadable_body_is_a_failure_and_not_an_empty_page(self):
        _client, draw = self._draw(_FakeResponse(200, None), self._page("a"))
        assert draw.failed_strata == ["MED/2024"]

    def test_the_report_names_the_missing_cells(self):
        _client, draw = self._draw(_FakeResponse(503), self._page("a"))
        lines = sampler.summarise_draw("draw", draw, 20, 1)
        assert any("ERROR" in line and "MED/2024" in line for line in lines)

    def test_a_draw_that_got_nothing_is_an_error_not_a_table(self):
        [line] = sampler.summarise_draw("draw", sampler.Draw(), 20, 1)
        assert "ERROR" in line


class TestTheTwoDrawsStayApart:
    """The CT.gov population has its own provenance and must keep it."""

    def test_the_main_draw_makes_no_clinicaltrials_request(self):
        # Pooling two differently-drawn samples into one denominator is the
        # defect this separation exists to prevent — a share is of a
        # denominator, and the trial draw is enriched where the main one is
        # not.
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        outcomes = _probe_record(client, record)
        assert "clinicaltrials" not in {o.endpoint for o in outcomes}
        assert not any("clinicaltrials" in url for url in client.urls())

    def test_the_trial_draws_efetch_is_not_counted_as_a_pubmed_probe(self):
        # It builds the population — which accessions bmlib would ask about —
        # exactly as the stratified search page builds the record population.
        # Counted as a probe it would put a second, differently-drawn copy of
        # the PubMed table into the same denominator.
        efetch = _FakeResponse(
            200,
            text=(
                "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
                "<DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName>"
                "<AccessionNumberList><AccessionNumber>NCT00000001</AccessionNumber>"
                "</AccessionNumberList></DataBank></DataBankList>"
                "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
            ),
        )
        client = _ScriptedClient(efetch, _FakeResponse(200, {"hasResults": True}))
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        outcomes = sampler.probe_trials(client, record, "a@b.c", _pace)
        assert [o.endpoint for o in outcomes] == ["clinicaltrials"]
        assert any("clinicaltrials" in url for url in client.urls())

    def test_a_record_with_no_accession_contributes_no_probe(self):
        # Right, rather than an omission: for such a record bmlib makes no
        # request either, so counting one would put an attempt into the
        # denominator that production never makes.
        client = _ScriptedClient(_FakeResponse(200, text="<PubmedArticleSet/>"))
        record = sampler.DrawnRecord(
            source="MED", year=2024, doi="10.1/x", pmid="1", raw={"abstractText": "no ids here"}
        )
        outcomes = sampler.probe_trials(client, record, "a@b.c", _pace)
        assert outcomes == []


class TestAnOutcomeCannotDescribeAnImpossibleEvent:
    """``ProbeOutcome``'s three stored fields re-encode each other.

    Its own docstring makes the argument — *"two fields describing one event
    can be constructed disagreeing, and the disagreement would silently move
    the very rate a log level is set from"* — and applied it to the derived
    ``ok`` and not to ``status``, ``cause`` and ``measured``. This test file
    was itself building two of the impossible states (PR #195's review).
    """

    def test_a_success_carries_a_200(self):
        with pytest.raises(ValueError, match="status 200"):
            sampler.ProbeOutcome(endpoint="crossref", status=None, cause=None)

    def test_an_http_bucket_agrees_with_its_status(self):
        with pytest.raises(ValueError, match="disagrees with status"):
            sampler.ProbeOutcome(endpoint="crossref", status=200, cause="http-500")

    def test_a_raised_request_carries_no_status(self):
        with pytest.raises(ValueError, match="no status"):
            sampler.ProbeOutcome(endpoint="crossref", status=500, cause="exception-OSError")

    def test_an_unmeasured_bucket_is_not_measured(self):
        # The one that would move a denominator: an "unmeasured" outcome
        # counted as measured puts the sampler's own throttling inside the
        # very rate the log levels are set from.
        with pytest.raises(ValueError, match="disagrees with measured"):
            sampler.ProbeOutcome(
                endpoint="crossref", status=429, cause="unmeasured-429", measured=True
            )

    def test_a_measured_outcome_is_not_in_an_unmeasured_bucket(self):
        with pytest.raises(ValueError, match="disagrees with measured"):
            sampler.ProbeOutcome(endpoint="crossref", status=503, cause="http-503", measured=False)

    def test_an_unknown_bucket_is_refused(self):
        # Fails closed: a bucket nobody taught it about is a defect in the
        # instrument, not a value to pass through into a table.
        with pytest.raises(ValueError, match="unknown cause bucket"):
            sampler.ProbeOutcome(endpoint="crossref", status=500, cause="oops-500")

    def test_every_outcome_probe_produces_is_accepted(self):
        # The negative control the rule needs — a guard that rejected a real
        # outcome would be worse than no guard.
        assert sampler.probe(_ScriptedClient(_FakeResponse(200, {})), "crossref", "u").ok
        assert sampler.probe(_ScriptedClient(_FakeResponse(404)), "crossref", "u").cause
        assert sampler.probe(_ScriptedClient(RuntimeError("x")), "crossref", "u").cause
        throttled = sampler.probe(
            _ScriptedClient(*[_FakeResponse(429) for _ in range(3)]), "crossref", "u"
        )
        assert not throttled.measured


class TestTheDrawReadsTheEnvelopeTheWayTheAnalyzerDoes:
    """``.get("resultList", {}).get("result", [])`` carried two of the defects
    this instrument exists to measure one module over (PR #213's review).

    A key present with ``null`` returns the *value*, not the default — the
    idiom ``_json_object`` was written to replace — so a ``resultList: null``
    raised ``AttributeError`` into a handler that printed *"unreadable body"*,
    a false claim about a body that decoded perfectly. And a wrong-typed
    *element* was guarded nowhere at all: the record loop sits outside the
    ``try``, so a string in ``result`` raised out of ``main`` and discarded
    every paced request the run had already spent.
    """

    def _draw_one(self, payload):
        client = _ScriptedClient(_FakeResponse(200, payload))
        return sampler.draw_records(client, 1, _pace, (("MED", 2024),), "")

    @pytest.mark.parametrize(
        "payload",
        [{"resultList": None}, {"resultList": {"result": None}}, {"resultList": []}, {}],
        ids=["null-resultList", "null-result", "array-resultList", "empty"],
    )
    def test_a_null_or_wrong_typed_envelope_is_an_empty_stratum_not_an_unreadable_body(
        self, payload, capsys
    ):
        draw = self._draw_one(payload)
        assert draw.records == []
        assert draw.failed_strata == ["MED/2024"]
        err = capsys.readouterr().err
        assert "no records" in err
        assert "unreadable" not in err

    def test_a_wrong_typed_record_does_not_escape_the_draw(self):
        # It used to raise `AttributeError` out of `main` — after the whole
        # main draw had already been probed, so the run lost everything.
        draw = self._draw_one({"resultList": {"result": ["not-an-object"]}})
        assert draw.records == []
        assert draw.failed_strata == ["MED/2024"]

    def test_a_mistyped_identifier_is_not_probed(self):
        # Coerced for the reason PR #208 coerced `source` and the accession:
        # a mistyped identifier is truthy, and would be interpolated into
        # `DOI:"{...}"` and probed — a request bmlib would never make entering
        # the denominator that sets that endpoint's log level.
        draw = self._draw_one({"resultList": {"result": [{"doi": {"a": 1}, "pmid": ["x"]}]}})
        assert draw.records == []
        assert draw.unusable_records == 1

    def test_a_well_formed_page_still_draws(self):
        # The anti-vacuity control: every assertion above is about a refusal.
        draw = self._draw_one({"resultList": {"result": [{"doi": "10.1/a", "pmid": "1"}]}})
        assert [(r.doi, r.pmid) for r in draw.records] == [("10.1/a", "1")]


class TestTheDrawIsWhatItSaysItIs:
    """The draw's own honesty: its size, and what it refuses to put in a denominator."""

    def _client(self, per_page: int, strata: int) -> _ScriptedClient:
        page = {
            "resultList": {
                "result": [{"doi": f"10.1/{i}", "pmid": str(i)} for i in range(per_page)]
            }
        }
        return _ScriptedClient(*[_FakeResponse(200, page) for _ in range(strata)])

    def test_the_draw_reaches_its_target(self):
        # `150 // 9` is 16, so the floor drew 144 while every comment reasoned
        # from 150 and `--target`'s help said "in total" (PR #195's review).
        # Rounding up is `sample_jats_exhibits.py`'s convention and the only
        # direction that does not quietly weaken the intervals.
        client = self._client(per_page=20, strata=len(sampler.DRAW_STRATA))
        draw = sampler.draw_records(client, 150, _pace)
        assert len(draw.records) >= 150

    def test_the_default_target_spreads_evenly_over_the_strata(self):
        # The committed numbers are of a 180-record draw, and the invocation
        # that produced it was recorded nowhere — so the default is now the
        # measured draw, which is the cheapest way to keep a committed figure
        # re-derivable from what is written down (issues #132/#138).
        assert sampler.DEFAULT_TARGET % len(sampler.DRAW_STRATA) == 0

    def test_a_record_bmlib_could_not_analyse_is_not_drawn(self):
        # It used to be, and `probe_record` then built the literal
        # `EXT_ID:None` and probed it into the Europe PMC denominator: a
        # request bmlib would never make, entering the population that sets
        # that endpoint's log level, while `summarise_draw` printed that such
        # a record reaches no endpoint.
        page = {"resultList": {"result": [{"title": "no identifiers at all"}]}}
        client = _ScriptedClient(
            *[_FakeResponse(200, page) for _ in range(len(sampler.DRAW_STRATA))]
        )
        draw = sampler.draw_records(client, 9, _pace)
        assert draw.records == []
        assert draw.unusable_records == len(sampler.DRAW_STRATA)

    def test_such_a_record_cannot_be_constructed_at_all(self):
        with pytest.raises(ValueError, match="DOI or a PMID"):
            sampler.DrawnRecord(source="MED", year=2024, doi=None, pmid=None, raw={})

    def test_the_report_says_how_many_were_skipped(self):
        draw = sampler.Draw(
            records=[sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid=None, raw={})],
            unusable_records=3,
        )
        assert any(
            "3 returned record(s)" in line for line in sampler.summarise_draw("draw", draw, 20, 1)
        )


class TestThePopulationBuildingRequestIsNotSilent:
    """``probe_trials``' efetch measures nothing and shapes everything.

    It decides *which accessions bmlib would ask about*, so a failure demotes
    the record to the abstract heuristic or drops it entirely — and
    ``trial_ids_for`` argues that measuring the fallback alone "would overstate
    the 404 rate". Until PR #195's review a non-200 there printed nothing,
    counted nothing, and could not reach the exit code, so the headline share
    could not be told from a population thinned that way.
    """

    def _record(self):
        return sampler.DrawnRecord(
            source="MED",
            year=2024,
            doi="10.1/x",
            pmid="1",
            raw={"abstractText": "Registered at ClinicalTrials.gov NCT00000001."},
        )

    def test_a_non_200_efetch_is_counted(self, capsys):
        failures: list[str] = []
        client = _ScriptedClient(_FakeResponse(503), _FakeResponse(200, {"hasResults": True}))
        sampler.probe_trials(client, self._record(), "a@b.c", _pace, failures)
        assert failures == ["efetch 1: HTTP 503"]
        assert "HTTP 503" in capsys.readouterr().err

    def test_a_raised_efetch_names_the_type(self, capsys):
        # `str(OSError("reset"))` does not contain "OSError" — the rule the
        # analyzer's own handler argues, and which `draw_records` beside this
        # already followed.
        failures: list[str] = []
        client = _ScriptedClient(OSError("reset"), _FakeResponse(200, {"hasResults": True}))
        sampler.probe_trials(client, self._record(), "a@b.c", _pace, failures)
        assert failures == ["efetch 1: OSError"]
        assert "OSError" in capsys.readouterr().err

    def test_a_clean_efetch_records_nothing(self):
        failures: list[str] = []
        client = _ScriptedClient(
            _FakeResponse(200, text="<PubmedArticleSet/>"), _FakeResponse(200, {"hasResults": True})
        )
        sampler.probe_trials(client, self._record(), "a@b.c", _pace, failures)
        assert failures == []


class TestTheExitCodeMeansWhatAScheduledRunReadsIt:
    """``main`` was never executed by any test, so its exit code was unpinned.

    ``is_reportable``'s docstring calls itself *"the single predicate behind
    both `summarise`'s ERROR branches and `main`'s exit status"* — but ``main``
    ANDs three further terms, and none of them was tested (PR #195's review).
    A scheduled re-run is judged by the exit code alone, so a run that lost a
    whole stratum, or whose trial population was reshaped by a failed efetch,
    must not exit 0 while its tables look healthy.
    """

    def _run(self, monkeypatch, client, argv=("--email", "a@b.c", "--target", "9")):
        import httpx

        monkeypatch.setattr(sys, "argv", ["sample_api_failures.py", *argv])
        monkeypatch.setattr(sampler, "_make_pacer", lambda _interval: _pace)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _ContextClient(client))
        return sampler.main()

    def _page(self, n=1):
        return _draw_page(n)

    def test_a_clean_run_exits_zero(self, monkeypatch, capsys):
        client = _AlwaysClient(self._page())
        assert self._run(monkeypatch, client) == 0
        assert "ERROR" not in capsys.readouterr().out

    def test_a_lost_stratum_exits_non_zero(self, monkeypatch, capsys):
        # **One** stratum, so the tables below are perfectly reportable and
        # the exit code is carrying this on its own — which is exactly what
        # `is_reportable` cannot do, and why `main`'s composition needed a
        # test of its own. A hole in the stratification is not visible in any
        # distribution; it is visible only in the draw line and the exit code.
        client = _AlwaysClient(self._page(), fail_draws={0})
        assert self._run(monkeypatch, client) == 1
        out = capsys.readouterr().out
        assert "did not answer" in out
        assert "not evenly stratified" in out

    def test_a_reshaped_trial_population_exits_non_zero(self, monkeypatch, capsys):
        # **One** efetch, and one of the *trial* draw's — the main draw makes
        # nine before it. So the ClinicalTrials.gov table below is non-empty
        # and reportable, the draw lost no stratum, and the exit code is
        # carrying this term alone. Failing every efetch instead empties that
        # population, which `is_reportable` already catches, and would let a
        # mutant dropping this term pass.
        client = _AlwaysClient(self._page(), fail_efetches={9})
        assert (
            self._run(
                monkeypatch,
                client,
                argv=("--email", "a@b.c", "--target", "9", "--trial-target", "3"),
            )
            == 1
        )
        out = capsys.readouterr().out
        assert "trial population" in out
        assert "efetch" in out
        # The table itself is fine, which is the whole point.
        assert "clinicaltrials" in out
        assert "clinicaltrials     ERROR" not in out

    def test_an_unreportable_population_exits_non_zero(self, monkeypatch):
        client = _AlwaysClient(self._page(), probe_status=429)
        assert self._run(monkeypatch, client) == 1


class _ContextClient:
    """Wraps a client so it can stand in for the one ``main`` opens with ``with``."""

    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        return self._inner

    def __exit__(self, *args):
        return False


class _AlwaysClient:
    """Answers every URL by its host, so ``main`` can be driven end to end."""

    def __init__(
        self,
        page,
        *,
        probe_status=200,
        fail_draws=frozenset(),
        fail_efetches=frozenset(),
        fail_probe_hosts=frozenset(),
    ):
        self.page = page
        self.probe_status = probe_status
        #: Substrings of the probe URLs to fail, so **one** endpoint can be
        #: made to serve nothing while every other population stays healthy.
        #: `probe_status` alone fails all five at once, which makes several
        #: exit-code terms fire together and hides a dropped one.
        self.fail_probe_hosts = fail_probe_hosts
        #: Indices of efetch calls to fail. Indexed across the whole run, so a
        #: caller can pick out one of the *trial* draw's rather than one of
        #: the main draw's probes.
        self.fail_efetches = fail_efetches
        self.efetches = 0
        #: Indices of draw pages to fail, so a *partial* hole can be made —
        #: failing every stratum empties the draw instead, which is a
        #: different branch.
        self.fail_draws = fail_draws
        self.draws = 0

    def get(self, url, params=None, headers=None, **kwargs):
        params = params or {}
        if "eutils" in url:
            index = self.efetches
            self.efetches += 1
            return _FakeResponse(
                503 if index in self.fail_efetches else 200,
                text="<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
                "<DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName>"
                "<AccessionNumberList><AccessionNumber>NCT00000001</AccessionNumber>"
                "</AccessionNumberList></DataBank></DataBankList></Article>"
                "</MedlineCitation></PubmedArticle></PubmedArticleSet>",
            )
        if "europepmc" in url and "pageSize" in params:
            index = self.draws
            self.draws += 1
            if index in self.fail_draws:
                return _FakeResponse(503)
            return _FakeResponse(200, self.page)
        if any(host in url for host in self.fail_probe_hosts):
            return _FakeResponse(404)
        return _FakeResponse(self.probe_status, self.page)


class TestTheSamplerProbesWhatTheAnalyzerRequests:
    """The instrument's own correctness, and this script's own rule.

    Every other sampler in this directory is forbidden from importing the
    predicate it measures. This one is *required* to address what the analyzer
    addresses, because its subject is the request: a sampler that probes a URL
    or presents a header bmlib does not is measuring somebody else. Issue #184
    was two literals for one endpoint drifting apart, and issue #194 was a
    header a remote judged us by — so the comparison is made by driving both
    and diffing, not by checking that a constant was imported, which a
    restated literal passes.

    **The subset relation is conditional on the record's category, and since
    issue #188 that is not a technicality** (PR #219's review). The sampler
    probes :data:`~sample_api_failures.PROBED_CATEGORIES` and the analyzer
    asks with :data:`~sample_api_failures.ADDRESSED_CATEGORIES`, so for an
    ``id-not-an-address`` record the sampler probes a URL the analyzer does
    not — by design, and asserted from the other side by
    ``test_an_address_bmlib_refuses_is_still_probed``. The assertions below
    hold because ``_RECORD`` carries a ``pmcid``. Parametrising it over the
    categories would redden them, and the wrong repair is narrowing
    ``PROBED_CATEGORIES``, which undoes issue #216.
    """

    #: The one record body both sides are driven over. It claims full text and
    #: offers an accession, so the **sixth** endpoint — the full-text fetch —
    #: is inside the comparison rather than beside it. With a bare ``{}`` the
    #: analyzer builds no full-text URL and the sampler probes no address, so
    #: every assertion below passed over an endpoint neither side reached:
    #: exactly the silence issue #216 is about, in the test written to prevent
    #: it.
    _RECORD = _epmc_body(inEPMC="Y", pmcid="PMC1", id="1", source="MED")

    def _analyzer_client(self, doi: str, pmid: str) -> _ScriptedClient:
        from bmlib.transparency.analyzer import _Analysis

        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(5)])
        analyzer = TransparencyAnalyzer(email="a@b.c")
        analyzer._query_crossref(client, doi)
        analyzer._query_europepmc(client, f'DOI:"{doi}"')
        analyzer._check_europepmc(client, self._RECORD, _Analysis(), "d")
        analyzer._query_pubmed(client, pmid)
        analyzer._query_openalex(client, doi)
        analyzer._check_trial_results(client, "NCT00000001")
        return client

    def _analyzer_urls(self, doi: str, pmid: str) -> set[str]:
        return set(self._analyzer_client(doi, pmid).urls())

    def _sampler_client(self, doi: str, pmid: str) -> _ScriptedClient:
        record = sampler.DrawnRecord(source="MED", year=2024, doi=doi, pmid=pmid, raw={})
        # The EuropePMC search answers with the record above, so the sampler
        # reads an address off it and probes it, as `probe_record` does live.
        client = _ScriptedClient(
            _FakeResponse(200, {}),
            _FakeResponse(200, self._RECORD),
            *[_FakeResponse(200, {}) for _ in range(3)],
        )
        _probe_record(client, record)
        return client

    def test_every_request_carries_the_parameters_and_headers_the_analyzer_sends(self):
        # **The URL comparison alone passed while two of the five endpoints
        # were probed bare** (PR #195's review): `probe()` had no `headers`
        # parameter, so CrossRef and OpenAlex never got the
        # `Accept: application/json` the analyzer sends — and `_search_params`
        # added a `pageSize` the analyzer never sends, on the endpoint whose
        # failure gates issue #193's whole fix. The class docstring already
        # said the subject here *is* the request; this is the assertion that
        # makes that true of more than the path.
        doi, pmid = "10.1/x", "1"
        analyzer_sent = self._analyzer_client(doi, pmid).sent()
        for url, (params, headers) in self._sampler_client(doi, pmid).sent().items():
            assert url in analyzer_sent, f"the sampler probes {url}, which the analyzer does not"
            assert (params, headers) == analyzer_sent[url], (
                f"the sampler sends {(params, headers)} to {url} where the analyzer sends "
                f"{analyzer_sent[url]}"
            )

    def test_the_clinicaltrials_request_matches_too(self):
        # Drawn separately, so it is compared separately rather than left out
        # of the loop above — which is how it came to be the one endpoint
        # whose header nobody checked.
        analyzer_sent = self._analyzer_client("10.1/x", "1").sent()
        trial_client = _ScriptedClient(
            _FakeResponse(200, text="<PubmedArticleSet/>"), _FakeResponse(200, {})
        )
        sampler.probe_trials(
            trial_client,
            sampler.DrawnRecord(
                source="MED",
                year=2024,
                doi="10.1/x",
                pmid="1",
                raw={"abstractText": "Registered at ClinicalTrials.gov NCT00000001."},
            ),
            "a@b.c",
            _pace,
        )
        sent = {u: (p, h) for u, p, h in trial_client.calls if "clinicaltrials" in u}
        assert sent
        for url, request in sent.items():
            assert request == analyzer_sent[url]

    def test_the_client_uses_the_analyzers_transport_policy(self):
        # Not a request-shape question but the same class of error: a sampler
        # that follows redirects and waits three times as long turns two of
        # bmlib's failures into successes, so a zero here would be a zero
        # about a different client. `REQUEST_FAILED` names a redirect as an
        # outcome explicitly, so the draw could not observe the one status
        # class the module documents (PR #195's review).
        source = _SAMPLER_PATH.read_text()
        assert "timeout=_HTTP_TIMEOUT_SECONDS" in source
        assert "follow_redirects=False" in source
        assert "timeout=45.0" not in source

    def test_every_url_the_sampler_probes_is_one_the_analyzer_requests(self):
        doi, pmid = "10.1/x", "1"
        client = self._sampler_client(doi, pmid)
        trial_client = _ScriptedClient(
            _FakeResponse(200, text="<PubmedArticleSet/>"), _FakeResponse(200, {})
        )
        sampler.probe_trials(
            trial_client,
            sampler.DrawnRecord(
                source="MED",
                year=2024,
                doi=doi,
                pmid=pmid,
                raw={"abstractText": "Registered at ClinicalTrials.gov NCT00000001."},
            ),
            "a@b.c",
            _pace,
        )
        probed = set(client.urls()) | {
            url for url in trial_client.urls() if "clinicaltrials" in url
        }
        assert probed <= self._analyzer_urls(doi, pmid)

    def test_the_sampler_reaches_every_endpoint_the_analyzer_has(self):
        # The other direction, and the one that goes stale silently: a
        # response the analyzer drops, added to the module and not to
        # `ENDPOINTS`, would simply never be measured and its level would be
        # chosen the way all five were before this script existed. That is not
        # hypothetical — the full-text fetch was exactly such an endpoint
        # until issue #216, and this assertion did not see it because both
        # sides were driven over a body carrying no record.
        doi, pmid = "10.1/x", "1"
        probed = set(self._sampler_client(doi, pmid).urls()) | {
            sampler.CLINICALTRIALS_STUDY_URL.format(nct_id="NCT00000001")
        }
        assert probed == self._analyzer_urls(doi, pmid)

    def test_the_full_text_url_is_among_them(self):
        # The anti-vacuity half of the two assertions above: they are set
        # comparisons, so a fixture in which neither side builds a full-text
        # URL satisfies both. Naming it is what keeps them about six
        # endpoints rather than five.
        assert sampler._fulltext_url("PMC1") in self._analyzer_urls("10.1/x", "1")

    def test_the_pubmed_parameters_are_the_ones_the_analyzer_sends(self):
        # `efetch` is addressed by its query, not its path, so a URL
        # comparison alone would pass while the sampler asked for a different
        # database or format.
        client = _ScriptedClient(_FakeResponse(200, text=""))
        TransparencyAnalyzer(email="a@b.c")._query_pubmed(client, "1")
        _url, sent, _headers = client.calls[0]
        assert sampler._efetch_params("1", "a@b.c") == sent

    def test_the_sampler_presents_the_header_the_analyzer_presents(self):
        # Issue #194's own lesson: a remote may judge the caller by this, so a
        # sampler string of its own measures an identity bmlib never presents.
        # Pinned against `_user_agent` rather than against a literal, so the
        # two cannot be edited apart.
        source = _SAMPLER_PATH.read_text()
        assert "_user_agent(args.email, httpx.__version__)" in source
        assert "bmlib-sampler/" not in source
        assert "python-httpx" in _user_agent("a@b.c", "1.0")


class TestABodyShapeIsWhatTheAnalyzerWouldHaveRead:
    """Issue #211: the sampler measured HTTP statuses and never a 200's *shape*.

    Every claim PR #208 made about how often its coercers fire rested on "no
    draw has seen a non-object body", which no instrument here could support —
    a count of what nobody looked for, which is this repository's own
    recurring finding. These are the tests for the counter that looks.

    The vocabulary is JSON's own, with two additions the transport forces:
    ``empty`` for a 200 carrying nothing and ``not-json`` for one whose body
    will not decode. bmlib reports those two identically (``_request_json``
    logs one line for both), so the instrument is *finer* than the code here
    and deliberately — whether any of these endpoints ever serves an empty 200
    is the standing unmeasured question behind issue #190.
    """

    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            ({}, "object"),
            ({"a": 1}, "object"),
            ([], "array"),
            ("x", "string"),
            (1, "number"),
            (1.5, "number"),
            (None, "null"),
        ],
    )
    def test_each_json_type_is_named_by_its_own_name(self, value, kind):
        assert sampler._kind(value) == kind

    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_a_boolean_and_not_a_number(self, value):
        # `bool` is an `int` in Python, which is the trap `_json_count`
        # already documents: tested before `int`, or `{"is_oa": true}` is
        # counted as a number and the row that would show a wrong-typed
        # boolean shows nothing at all.
        assert sampler._kind(value) == "boolean"

    def test_the_top_level_type_of_a_served_body_is_recorded(self):
        shape = sampler.observe_body("openalex", _FakeResponse(200, {"cited_by_count": 3}))
        assert shape.top == "object"

    def test_a_body_that_is_not_an_object_is_recorded_as_what_it_is(self):
        # The twelve top-level escapes PR #208 measured are exactly this
        # shape, and nothing has ever counted how often a remote sends one.
        assert sampler.observe_body("crossref", _FakeResponse(200, [])).top == "array"

    def test_a_200_whose_body_will_not_decode_is_not_json(self):
        assert sampler.observe_body("crossref", _FakeResponse(200, text="<html>")).top == "not-json"

    def test_an_empty_200_is_kept_apart_from_one_that_will_not_decode(self):
        # bmlib cannot tell these apart; the instrument can, and the question
        # of whether an empty 200 is ever served is why it should.
        assert sampler.observe_body("crossref", _FakeResponse(200, text="")).top == "empty"


class TestAFieldIsObservedWhereTheAnalyzerReadsIt:
    """The per-field half, and the two ways an element can be reached.

    ``_check_crossref`` iterates every funder; every ``_epmc_records`` caller
    takes ``records[0]``. So the step sentinels are two — ``[*]`` and ``[0]``
    — because an instrument that aggregated over a list bmlib never looks past
    the head of would report a shape bmlib never sees, and one that read only
    the head of a list bmlib iterates would miss the shape that breaks it.
    """

    def test_a_fields_type_is_recorded_under_its_rendered_path(self):
        shape = sampler.observe_body("openalex", _FakeResponse(200, {"cited_by_count": 3}))
        assert dict(shape.fields)["cited_by_count"] == "number"

    def test_a_nested_field_is_reached_through_its_parent(self):
        shape = sampler.observe_body(
            "openalex", _FakeResponse(200, {"open_access": {"is_oa": True}})
        )
        assert dict(shape.fields)["open_access.is_oa"] == "boolean"

    def test_a_key_the_body_omits_is_absent_rather_than_unrecorded(self):
        # Issue #210 is exactly this row: an absent `hasResults` is stored as
        # a finding, and whether ClinicalTrials.gov ever omits it is what
        # settles that issue.
        shape = sampler.observe_body("clinicaltrials", _FakeResponse(200, {}))
        assert dict(shape.fields)["hasResults"] == "absent"

    def test_a_field_whose_parent_is_the_wrong_type_is_not_observed_at_all(self):
        # Not "absent": the question was never reachable, so putting it in the
        # denominator would report a body that could not be asked as one that
        # answered no. A share is of a denominator.
        shape = sampler.observe_body("openalex", _FakeResponse(200, {"open_access": "yes"}))
        fields = dict(shape.fields)
        assert fields["open_access"] == "string"
        assert "open_access.is_oa" not in fields

    def test_every_element_is_read_where_the_analyzer_iterates(self):
        body = {"message": {"funder": [{"name": "A"}, {"name": 1}]}}
        shape = sampler.observe_body("crossref", _FakeResponse(200, body))
        assert dict(shape.fields)["message.funder[].name"] == "mixed"

    def test_a_homogeneous_iterated_list_reports_the_one_kind(self):
        body = {"message": {"funder": [{"name": "A"}, {"name": "B"}]}}
        shape = sampler.observe_body("crossref", _FakeResponse(200, body))
        assert dict(shape.fields)["message.funder[].name"] == "string"

    def test_only_the_head_is_read_where_the_analyzer_reads_the_head(self):
        # `records[0]`, so a second record's wrong-typed abstract is a shape
        # bmlib never sees and must not enter this table.
        body = {"resultList": {"result": [{"abstractText": "a"}, {"abstractText": 7}]}}
        shape = sampler.observe_body("europepmc_search", _FakeResponse(200, body))
        assert dict(shape.fields)["resultList.result[0].abstractText"] == "string"

    def test_an_empty_object_still_reports_its_fields_as_absent(self):
        # Wider than the code, and deliberately. `_check_crossref` and
        # `_check_openalex` guard on truthiness (`if cr:`), so an empty object
        # body reaches no field read at all — while the walker treats it as a
        # reachable parent whose every key is absent. The cost is confined to
        # a first-level field's `absent` count, which is therefore an upper
        # bound; and the body is visible in its own right, an empty object
        # being the one that shows `object` at the top with every field
        # absent. Narrowing it would mean restating each caller's guard here,
        # which is the restated literal issue #184 argues against.
        shape = sampler.observe_body("crossref", _FakeResponse(200, {}))
        assert dict(shape.fields)["message"] == "absent"

    def test_an_empty_list_reaches_no_element_field(self):
        body = {"resultList": {"result": []}}
        shape = sampler.observe_body("europepmc_search", _FakeResponse(200, body))
        fields = dict(shape.fields)
        assert fields["resultList.result"] == "array"
        assert "resultList.result[0].abstractText" not in fields


#: Which endpoint's body each of ``analyzer.py``'s JSON-reading functions is
#: reading. Every literal ``.get()`` in that module must fall under one of
#: these or the walk below raises: a read in a function named nowhere is
#: exactly how a field would slip out of :data:`sampler.FIELD_PATHS` unseen,
#: and "I found nothing" must not be an answer a net can return.
_READERS_BY_ENDPOINT = {
    "_check_crossref": "crossref",
    "_epmc_records": "europepmc_search",
    "_check_europepmc": "europepmc_search",
    "_pmid_from_epmc": "europepmc_search",
    "_find_trial_ids": "europepmc_search",
    "_check_openalex": "openalex",
    "_check_trial_results": "clinicaltrials",
}


def _analyzer_reads(source: str) -> set[tuple[str, str]]:
    """Every ``(endpoint, key)`` the given ``analyzer.py`` source reads out of a body.

    A literal ``.get("k")`` is the whole of how this module reads a decoded
    body, and — measured over the real file — there is no other kind of
    literal ``.get()`` in it: ``_UNTERMINATED_OPENER_NAMES.get(opener, "tag")``
    takes a name, and ``client.get(url)`` takes one too, so both fall outside
    without needing to be excused. That is what lets this walk be strict.

    Raises:
        AssertionError: If a read appears in a function
            :data:`_READERS_BY_ENDPOINT` does not name.
    """
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node: ast.AST) -> str:
        cursor = parents.get(node)
        while cursor is not None:
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cursor.name
            cursor = parents.get(cursor)
        return "<module>"

    reads: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        key = node.args[0]
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        owner = enclosing(node)
        assert owner in _READERS_BY_ENDPOINT, (
            f"{owner}() reads {key.value!r} out of a body and is named by no endpoint; "
            "add it to _READERS_BY_ENDPOINT (and its field to FIELD_PATHS) or the "
            "sampler measures a body bmlib reads and this net does not"
        )
        reads.add((_READERS_BY_ENDPOINT[owner], key.value))
    return reads


class TestTheFieldListIsEveryFieldTheAnalyzerReads:
    """Issue #211's own rule: the field list must not be a restated literal.

    It was written as one in the issue and was **already stale** — ``source``
    has been read by ``_check_europepmc`` since PR #208 coerced it before
    building the full-text URL, and the issue lists it nowhere. A restated
    literal is how issue #184 lived a release; a rule enforced by prose is not
    enforced (``TestTheAuditNetIsComplete``,
    ``TestOnlyTheHelperWalksTheEuropePMCResultList``). So the relation is
    *equality*, checked by walking the module.
    """

    @property
    def _reads(self) -> set[tuple[str, str]]:
        return _analyzer_reads(
            (
                Path(sampler.__file__).resolve().parent.parent / "bmlib/transparency/analyzer.py"
            ).read_text()
        )

    def _listed(self) -> set[tuple[str, str]]:
        return {
            (endpoint, steps[-1])
            for endpoint, paths in sampler.FIELD_PATHS.items()
            for steps in paths
            if steps[-1] not in {"[*]", "[0]"}
        }

    def test_the_walk_finds_the_reads_at_all(self):
        # The anti-vacuity assertion. A walk that silently matched nothing
        # would turn both directions below green at once, which is the shape
        # `TestTheAuditNetIsComplete` fails closed against.
        reads = self._reads
        assert len(reads) >= 15, reads
        assert ("clinicaltrials", "hasResults") in reads

    def test_every_field_the_analyzer_reads_is_in_the_sampler_list(self):
        assert self._reads - self._listed() == set()

    def test_the_sampler_lists_no_field_the_analyzer_does_not_read(self):
        # The other direction, and not symmetry for its own sake: a row for a
        # field bmlib never reads is a population in the table that no
        # decision rests on, and a reader cannot tell it from one that does.
        assert self._listed() - self._reads == set()

    def test_a_read_in_a_function_the_net_does_not_name_fails_closed(self):
        with pytest.raises(AssertionError, match="named by no endpoint"):
            _analyzer_reads("def _check_newthing(body):\n    return body.get('surprise')\n")

    def test_a_get_that_is_not_a_body_read_is_not_counted(self):
        # `.get(opener, "tag")` and `client.get(url)` both take a name, which
        # is what lets the walk above be strict without an excuse list.
        assert _analyzer_reads("def _check_crossref(c, u):\n    return c.get(u)\n") == set()


#: One body per JSON endpoint, shaped as that API actually answers — the
#: minimum that makes every declared path reachable.
_REPRESENTATIVE_BODIES: dict[str, dict] = {
    "crossref": {"message": {"funder": [{"name": "Wellcome Trust"}]}},
    "europepmc_search": {
        "resultList": {
            "result": [
                {
                    "abstractText": "…",
                    "inEPMC": "Y",
                    "source": "MED",
                    "pmcid": "PMC4154587",
                    "id": "24895382",
                    "pmid": "24895382",
                }
            ]
        }
    },
    "openalex": {"open_access": {"is_oa": True}, "cited_by_count": 7},
    "clinicaltrials": {"hasResults": True},
}


class TestEveryDeclaredPathIsReachableInARepresentativeBody:
    """The nesting, which the ``ast`` net above structurally cannot check.

    That net holds ``{(endpoint, leaf key)}`` equal in both directions, so
    **mis-nesting leaves the key unchanged and passes**: rewriting
    ``("resultList", "result", "[0]", "pmid")`` as ``("resultList", "pmid")``
    was measured to survive the entire suite, as were the same edits to
    ``source``, ``pmcid``, ``id`` and ``message.funder`` — five of the sixteen
    declared paths, and four of them are exactly issues #188's and #207's
    evidence (PR #213's review).

    A mis-nested path renders ``NO POPULATION HERE`` for every body, which is
    the row a reader is meant to read as a *finding about the remote*. So the
    failure mode is not merely uncaught, it prints as its opposite.

    The nesting cannot be derived from ``analyzer.py``: it is a fact about the
    document each API serves, and the reads that establish it run through
    local variables (``record = records[0]``, ``for funder in …``) that no
    static walk resolves. So it is pinned **behaviourally** instead — against
    a body shaped the way the API answers, every declared path must be
    reachable. That is a fixture, which this suite is built from throughout,
    rather than a restated literal of the module's source.
    """

    @pytest.mark.parametrize("endpoint", sorted(_REPRESENTATIVE_BODIES))
    def test_a_representative_body_answers_every_declared_path(self, endpoint):
        # **The discriminator is `absent`, not reachability.** Comparing the
        # observed path set against the declared one is vacuous — both sides
        # are derived from `FIELD_PATHS`, so a mis-nesting moves them
        # together, and the first cut of this test passed all five mutants
        # (measured). What a mis-nesting cannot fake is the *answer*: the
        # representative body carries every field the analyzer reads, so a
        # correctly-nested path finds a value and a mis-nested one lands on an
        # object that does not carry the key and reads `absent`.
        shape = sampler.observe_body(endpoint, _FakeResponse(200, _REPRESENTATIVE_BODIES[endpoint]))
        declared = {sampler.render_path(steps) for steps in sampler.FIELD_PATHS[endpoint]}
        observed = dict(shape.fields)
        assert set(observed) == declared
        assert [path for path, kind in observed.items() if kind == "absent"] == []

    def test_every_json_endpoint_has_a_representative_body(self):
        # Anti-vacuity: a body missing from the mapping would silently take
        # its endpoint's paths out of the check above.
        assert set(_REPRESENTATIVE_BODIES) == set(sampler.FIELD_PATHS)

    def test_a_mis_nested_path_reads_the_wrong_answer(self):
        # The negative control, and the mutation that survived the whole
        # suite: the leaf key is unchanged, so the `ast` net is satisfied.
        #
        # What the mis-nesting produces is worse than the `NO POPULATION HERE`
        # first supposed. `resultList` is itself an object, so the shortened
        # path is *reachable* and reports `absent` — a field row asserting
        # that bmlib asked EuropePMC for a PMID and was given nothing, over
        # every body in the draw. A row that reads as a finding, for a
        # question never asked.
        body = _REPRESENTATIVE_BODIES["europepmc_search"]
        assert sampler._kind_at(body, ("resultList", "result", "[0]", "pmid")) == "string"
        assert sampler._kind_at(body, ("resultList", "pmid")) == "absent"

    def test_the_rendered_path_set_is_what_separates_them(self):
        # And this is why the check above is on the rendered set rather than
        # on kinds: the two paths render differently, so a mis-nesting cannot
        # satisfy `test_a_representative_body_reaches_every_declared_path`
        # whether it is reachable or not.
        assert sampler.render_path(("resultList", "result", "[0]", "pmid")) != sampler.render_path(
            ("resultList", "pmid")
        )


class TestOnlyAServedBodyHasAShape:
    """A shape is a fact about a body, so an outcome that carried none has none.

    ``ProbeOutcome``'s own rule, applied to the field added for issue #211:
    two fields describing one event can be constructed disagreeing, and here
    the disagreement would put a shape row under a request that never
    returned a body — inventing a population out of the endpoint's failures,
    which is the exact direction ``measured`` already guards.
    """

    def test_a_served_probe_carries_the_shape_of_what_it_served(self):
        outcome = sampler.probe(_ScriptedClient(_FakeResponse(200, {})), "crossref", "u")
        assert outcome.shape is not None
        assert outcome.shape.top == "object"

    def test_a_non_200_carries_no_shape(self):
        assert sampler.probe(_ScriptedClient(_FakeResponse(404)), "crossref", "u").shape is None

    def test_a_raised_request_carries_no_shape(self):
        assert sampler.probe(_ScriptedClient(OSError("x")), "crossref", "u").shape is None

    def test_a_throttled_probe_carries_no_shape(self):
        throttled = sampler.probe(
            _ScriptedClient(*[_FakeResponse(429) for _ in range(3)]), "crossref", "u"
        )
        assert throttled.shape is None

    def test_an_outcome_that_served_nothing_cannot_be_given_a_shape(self):
        with pytest.raises(ValueError, match="only a served outcome"):
            sampler.ProbeOutcome(
                endpoint="crossref",
                status=404,
                cause="http-404",
                shape=sampler.BodyShape(endpoint="crossref", top="object"),
            )

    def test_a_served_outcome_without_one_is_refused_too(self):
        # The other direction, and the one a hand-built test fixture reaches:
        # `probe()` observes every body it serves, so a served outcome with no
        # shape describes no event, and a summary built from such outcomes
        # would report an endpoint as having served nothing readable.
        with pytest.raises(ValueError, match="served outcome carries a shape"):
            sampler.ProbeOutcome(endpoint="crossref", status=200, cause=None)

    def test_a_retried_status_is_never_a_measured_outcome(self):
        # The clause keyed on the status rather than on the bucket. `probe`
        # retries every `_THROTTLE_STATUSES` member and can only ever report
        # one as `unmeasured-`, so an `http-429` describes no event it
        # produces — and it satisfied every other clause, putting a throttled
        # probe into the failure share as a *failure*, which is the exact
        # hazard `measured` exists to prevent (PR #213's review).
        for status in sorted(sampler._THROTTLE_STATUSES):
            with pytest.raises(ValueError, match="is retried"):
                sampler.ProbeOutcome(endpoint="crossref", status=status, cause=f"http-{status}")

    def test_the_retried_statuses_are_the_ones_probe_retries(self):
        # One set, so the guard above and the retry loop cannot disagree about
        # which statuses mean the sampler was throttled.
        for status in sorted(sampler._THROTTLE_STATUSES):
            outcome = sampler.probe(
                _ScriptedClient(*[_FakeResponse(status) for _ in range(4)]), "crossref", "u"
            )
            assert outcome.measured is False
            assert outcome.cause == f"unmeasured-{status}"

    def test_a_body_this_script_cannot_read_is_not_the_remotes_fault(self):
        # A response object carrying *valid* JSON that this script cannot call
        # `.json()` on: reported as `not-json`, that is a `_BUG_TYPES` member
        # dressed as a claim about the remote — `analyzer.py`'s own
        # `_report_swallowed_exception` defect one layer up, and it would print
        # a healthy endpoint as serving undecodable bodies 100% of the time,
        # in the table the whole run is quoted from (PR #213's review).
        class _NoJson:
            status_code = 200
            text = '{"message": {"funder": [{"name": "X"}]}}'

        shape = sampler.observe_body("crossref", _NoJson())
        assert shape.top.startswith(sampler._INSTRUMENT_KIND_PREFIX)
        assert "AttributeError" in shape.top
        assert shape.top != "not-json"

    def test_a_body_that_is_genuinely_not_json_still_reads_as_that(self):
        # The other side of the split, so it is not simply "report everything
        # as the instrument's fault".
        assert sampler.observe_body("crossref", _FakeResponse(200, text="<html>")).top == "not-json"

    def test_an_unreadable_body_reaches_the_exit_code(self):
        outcome = sampler.ProbeOutcome(
            endpoint="crossref",
            status=200,
            cause=None,
            shape=sampler.BodyShape(endpoint="crossref", top="instrument-AttributeError"),
        )
        assert sampler.instrument_defects([outcome]) == 1
        assert sampler.instrument_defects([_served("crossref", {})]) == 0

    def test_a_shape_from_another_endpoint_is_refused(self):
        with pytest.raises(ValueError, match="shape for 'openalex'"):
            sampler.ProbeOutcome(
                endpoint="crossref",
                status=200,
                cause=None,
                shape=sampler.BodyShape(endpoint="openalex", top="object"),
            )


#: An ``efetch`` body carrying the element ``_parse_pubmed_signals`` looks for.
_PUBMED_WITH_CITATION = (
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article/>"
    "</MedlineCitation></PubmedArticle></PubmedArticleSet>"
)
#: One that parses and carries none — NCBI's error envelope, served at 200.
_PUBMED_EUTILS_ERROR = "<eFetchResult><ERROR>Empty id list</ERROR></eFetchResult>"
#: And the other population that reaches the same branch: a Bookshelf PMID,
#: which ``_parse_pubmed_signals`` declines by name.
_PUBMED_BOOK_ARTICLE = (
    "<PubmedArticleSet><PubmedBookArticle><BookDocument/></PubmedBookArticle></PubmedArticleSet>"
)


class TestThePubMedBodyIsShapedToo:
    """``efetch`` serves XML, so its shape question is whether bmlib can read it.

    Not a JSON row, and it earns its place for the same reason the JSON ones
    do: ``_check_pubmed`` WARNs on an empty 200 and ``_parse_pubmed_signals``
    WARNs on a body that is not parsable XML, and **neither level was ever
    measured** — issue #193's draw settled statuses. Every branch ends with
    empty signals, which means no ``<CoiStatement>``, nothing retracted from
    the COI indicators, and the missing-COI downgrade free to fire.
    """

    def test_a_parsable_document_carrying_a_citation_is_xml(self):
        served = _FakeResponse(200, text=_PUBMED_WITH_CITATION)
        assert sampler.observe_body("pubmed_efetch", served).top == "xml"

    def test_an_empty_body_is_its_own_answer(self):
        assert sampler.observe_body("pubmed_efetch", _FakeResponse(200, text="")).top == "empty"

    def test_a_body_that_will_not_parse_is_not_xml(self):
        served = _FakeResponse(200, text="<PubmedArticleSet>")
        assert sampler.observe_body("pubmed_efetch", served).top == "not-xml"

    @pytest.mark.parametrize(
        "body",
        [_PUBMED_EUTILS_ERROR, _PUBMED_BOOK_ARTICLE, "<PubmedArticleSet/>"],
        ids=["eutils-error", "book-article", "empty-set"],
    )
    def test_a_document_carrying_no_citation_is_its_own_kind(self, body):
        # The branch that was folded into `xml`, and the one that was silent
        # where the other three WARN — until issue #218 gave it three lines of
        # its own. This counter is what sized that, and the category stays one
        # value: widening it would restate the analyzer's branch predicates
        # here, and a corpus labelled by the rule under test can only confirm
        # that rule.
        #
        # **The error envelope belongs to a different request** (PR #225's
        # review): `<eFetchResult><ERROR>` at 200 is what an evicted *history
        # session* efetch serves, the shape `publications/` guards against,
        # while this probe is by id. What is live here is a Bookshelf set —
        # issue #188's finding is that every `id-only` `MED` record in a
        # 150-record spot draw was a book chapter — and an empty record set for
        # an id NCBI does not hold. The envelope is kept as a fixture because
        # the counter must take it if some other error class arrives at 200.
        assert sampler.observe_body("pubmed_efetch", _FakeResponse(200, text=body)).top == (
            "no-citation"
        )

    def test_the_xml_kind_agrees_with_what_the_analyzer_reads(self):
        # The restated XPath, driven rather than compared as source: for each
        # body, "did bmlib get a citation?" must match "did this call it xml?".
        for body in (_PUBMED_WITH_CITATION, _PUBMED_EUTILS_ERROR, _PUBMED_BOOK_ARTICLE):
            root = ET.fromstring(body)
            analyzer_found = root.find(".//PubmedArticle/MedlineCitation") is not None
            assert (sampler._xml_kind(body) == "xml") is analyzer_found, body

    def test_the_xml_endpoint_has_no_field_rows(self):
        served = _FakeResponse(200, text=_PUBMED_WITH_CITATION)
        assert sampler.observe_body("pubmed_efetch", served).fields == ()


def _served_shape(endpoint: str) -> sampler.BodyShape:
    """A minimal shape for a body *endpoint* served, valid for that endpoint.

    ``europepmc_search`` needs a category: since PR #213's review a decoded
    object body always carries one, so a bare ``top="object"`` there describes
    a body :func:`observe_body` cannot produce.
    """
    if endpoint == "europepmc_search":
        return sampler.BodyShape(
            endpoint=endpoint, top="object", addressing=sampler.RecordAddressing("no-record")
        )
    return sampler.BodyShape(endpoint=endpoint, top="object")


class TestAnEuropePMCRecordSaysHowBmlibWouldAddressIt:
    """The rider populations: issues #207 and #188, on the body already fetched.

    ``FullTextStatus.NOT_ATTEMPTED`` is documented *"no request was made, and
    EuropePMC's own answer is why"* and covers several causes, one of which is
    a record claiming ``inEPMC: Y`` and carrying nothing to address the text
    by — a malformed record, not a closed-access paper, and the one cause for
    which that sentence is false. #207 asks for a member of its own for that
    cause and was filed rather than taken because its population is
    unmeasured. Stated without an ordinal: this read *"three causes"* and
    *"a fourth member"*, and #188 added a fourth cause without moving either,
    so the second was off by one in two directions at once (PR #219's
    review). #188 is the
    neighbouring split: a record addressed by its bare ``id`` rather than a
    ``pmcid``, which for a ``MED`` record is a request whose 404 is known
    before it leaves and for a ``PPR`` one is the only address there is.

    Both are counted here rather than reasoned about, and the categories are
    finer than ``FullTextStatus`` — which is the finding, not a mismatch.
    """

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"resultList": {"result": []}}, "no-record"),
            ({}, "no-record"),
            (_epmc_body(id="1", pmcid="PMC1"), "not-claimed"),
            (_epmc_body(inEPMC="N", pmcid="PMC1"), "not-claimed"),
            (_epmc_body(inEPMC="Y", pmcid="PMC1", id="1"), "pmcid"),
            (_epmc_body(inEPMC="Y", id="PPR123"), "id-accession"),
            (_epmc_body(inEPMC="Y", pmcid="", id="PPR123"), "id-accession"),
            (_epmc_body(inEPMC="Y", id="PMC4154587"), "id-accession"),
            (_epmc_body(inEPMC="Y", id="41637542"), "id-not-an-address"),
            (_epmc_body(inEPMC="Y", id="NBK620630"), "id-not-an-address"),
            (_epmc_body(inEPMC="Y"), "unaddressable"),
            (_epmc_body(inEPMC="Y", pmcid="", id=""), "unaddressable"),
        ],
    )
    def test_each_record_falls_in_the_category_bmlib_would_act_on(self, body, expected):
        shape = sampler.observe_body("europepmc_search", _FakeResponse(200, body))
        assert shape.addressability == expected

    def test_the_records_own_source_is_kept_beside_it(self):
        # #188 turns on it: a `PPR` record's `id` is the only address there
        # is, while a `MED` record's is a bare PMID that never serves.
        shape = sampler.observe_body(
            "europepmc_search", _FakeResponse(200, _epmc_body(inEPMC="Y", id="PPR1", source="PPR"))
        )
        assert shape.addressing.source == "PPR"

    def test_a_body_that_did_not_decode_is_not_categorised(self):
        shape = sampler.observe_body("europepmc_search", _FakeResponse(200, text="<html>"))
        assert shape.addressability is None

    @pytest.mark.parametrize("body", [[], ["x"], "s", 7, True], ids=str)
    def test_a_body_that_is_not_an_object_is_not_categorised_either(self, body):
        # `_epmc_records` funnels through `_json_object`, which answers `{}`
        # for anything that is not a dict — so every one of these came back
        # `no-record`, a category documented as EuropePMC answering and
        # holding nothing. What bmlib does with such a body is refuse it at
        # `_request_json` and store `FullTextStatus.SEARCH_FAILED`: a WARNING
        # and up to 30 unscored points. The table reported a loud failure as a
        # quiet absence, inside the denominator issues #207 and #188 are
        # decided on (PR #213's review).
        shape = sampler.observe_body("europepmc_search", _FakeResponse(200, body))
        assert shape.addressability is None
        assert shape.top != "object"

    @pytest.mark.parametrize(
        ("body", "text"),
        [({}, ""), ([], ""), ("s", ""), (None, "<html>")],
        ids=["object", "array", "string", "undecodable"],
    )
    def test_a_body_is_categorised_exactly_when_the_analyzer_can_read_one(self, body, text):
        # Driven against the real boundary rather than argued: whatever
        # `_request_json` refuses, `analyze()` reaches its `epmc is None`
        # branch for and stores `SEARCH_FAILED` — so this script must decline
        # to categorise exactly those bodies and no others. The comparison is
        # what a restated rule does not survive.
        resp = _FakeResponse(200, body, text=text)
        analyzer = TransparencyAnalyzer(email="a@b.c")
        readable = (
            analyzer._request_json(_ScriptedClient(resp), "u", api="EuropePMC", subject="x")
            is not None
        )
        shape = sampler.observe_body("europepmc_search", resp)
        assert (shape.addressability is not None) is readable

    def test_every_address_category_chooses_a_side(self):
        # This repository's own `FullTextStatus.is_refusal` rule: both sides
        # are named sets and a test asserts the partition, because a sixth
        # category omitted from `ADDRESSED_CATEGORIES` would default to *"no
        # request would be made"* and silently move the headline figure two
        # issues are blocked on. `ADDRESSED_CATEGORIES` was a positive set
        # with no named complement (PR #213's review).
        assert not (sampler.ADDRESSED_CATEGORIES & sampler.UNADDRESSED_CATEGORIES)
        assert (
            sampler.ADDRESSED_CATEGORIES | sampler.UNADDRESSED_CATEGORIES
        ) == sampler.ALL_ADDRESS_CATEGORIES

    def test_the_id_fallback_set_is_every_category_the_id_branch_produces(self):
        # The one set here whose next member is lost in *silence*: a third
        # outcome of `_addressability`'s `ext_id` branch — a `bookid` split is
        # the obvious next one — omitted from `_ID_FALLBACK_CATEGORIES` loses
        # its `, source X` suffix and pools two source populations into one
        # denominator, at exit 0 (PR #219's review). It is derivable, so it is
        # held against the function rather than against a restated list: every
        # category reachable with `pmcid` absent and an `id` present.
        produced = {
            sampler.observe_body(
                "europepmc_search", _FakeResponse(200, _epmc_body(inEPMC="Y", id=ext_id))
            ).addressability
            for ext_id in ("PPR1", "PMC1", "41637542", "NBK620630", "x")
        }
        assert produced == sampler._ID_FALLBACK_CATEGORIES
        # And the anti-vacuity half: the set is not simply everything.
        assert sampler._ID_FALLBACK_CATEGORIES < sampler.ALL_ADDRESS_CATEGORIES

    def test_every_category_the_code_can_return_is_in_the_partition(self):
        # And the partition is held against what `_addressability` actually
        # produces, not against a restated list — otherwise a new category
        # could be added to both the function and one set and still be wrong.
        produced = {
            sampler.observe_body("europepmc_search", _FakeResponse(200, body)).addressability
            for body in (
                {"resultList": {"result": []}},
                _epmc_body(id="1", pmcid="PMC1"),
                _epmc_body(inEPMC="Y", pmcid="PMC1"),
                _epmc_body(inEPMC="Y", id="PPR1"),
                _epmc_body(inEPMC="Y", id="41637542"),
                _epmc_body(inEPMC="Y"),
            )
        }
        assert produced == sampler.ALL_ADDRESS_CATEGORIES

    def test_a_category_outside_the_partition_is_refused(self):
        with pytest.raises(ValueError, match="unknown address category"):
            sampler.RecordAddressing("invented")

    def test_a_decoded_object_body_always_carries_one(self):
        # The direction that keeps `None` from acquiring a second meaning: an
        # uncategorised object body would be dropped by `_addressed_shapes`
        # and shrink issue #207's denominator with no line printed.
        with pytest.raises(ValueError, match="always categorised"):
            sampler.BodyShape(endpoint="europepmc_search", top="object")

    @pytest.mark.parametrize("top", ["array", "string", "not-json", "null"])
    def test_a_non_object_body_cannot_carry_a_category(self, top):
        # The third direction, open until PR #219's review: the guard was
        # `top == "object" and not addressing`, so a *non-object* EuropePMC
        # body could carry a category through this constructor — which is the
        # pre-PR-#213 defect `_addressability` returns `None` for a non-dict
        # specifically to prevent.
        with pytest.raises(ValueError, match="nothing else\\s+ever is"):
            sampler.BodyShape(
                endpoint="europepmc_search",
                top=top,
                addressing=sampler.RecordAddressing("no-record"),
            )

    def test_a_source_without_a_category_is_refused_on_another_endpoint(self):
        # The other half of the endpoint guard, which was written as
        # `addressability or address_source` and tested only through the
        # first: narrowing it to `addressability` alone survived the suite.
        with pytest.raises(ValueError, match="only a EuropePMC record"):
            sampler.BodyShape(
                endpoint="crossref", top="object", addressing=sampler.RecordAddressing("no-record")
            )

    def test_no_other_endpoint_carries_a_category(self):
        assert sampler.observe_body("crossref", _FakeResponse(200, {})).addressability is None

    def test_a_category_on_another_endpoint_is_refused(self):
        with pytest.raises(ValueError, match="only a EuropePMC record"):
            sampler.BodyShape(
                endpoint="crossref",
                top="object",
                addressing=sampler.RecordAddressing("pmcid", accession="PMC1"),
            )


class TestTheAddressCategoryAgreesWithWhatTheAnalyzerDoes:
    """Driven against ``_check_europepmc`` itself, not read off its source.

    The category restates one literal the sampler cannot import — the
    ``inEPMC == "Y"`` gate is inline in that method — so the relation is
    pinned the way ``TestTheSamplerProbesWhatTheAnalyzerRequests`` pins the
    URLs: drive both over the same bodies and compare. A restated literal is
    what passes an import check and fails a live remote (issue #184).

    What is compared is what the analyzer *observably* does: whether it asks
    EuropePMC for full text, and which accession it asks with. It cannot
    distinguish ``not-claimed`` from ``unaddressable`` — both make no request
    and both store ``NOT_ATTEMPTED`` — and that indistinguishability **is**
    issue #207.
    """

    @pytest.mark.parametrize(
        ("body", "expected_accession"),
        [
            ({"resultList": {"result": []}}, None),
            (_epmc_body(id="1", pmcid="PMC1"), None),
            (_epmc_body(inEPMC="N", pmcid="PMC1"), None),
            (_epmc_body(inEPMC="Y", pmcid="PMC1", id="1"), "PMC1"),
            (_epmc_body(inEPMC="Y", id="PPR123"), "PPR123"),
            (_epmc_body(inEPMC="Y"), None),
            # Issue #188's own population, and the row this comparison had no
            # case for while the analyzer asked with anything: a `MED`
            # record's bare `id` is a PMID, and neither side addresses it.
            (_epmc_body(inEPMC="Y", id="41637542", source="MED"), None),
            (_epmc_body(inEPMC="Y", id="NBK620630", source="MED"), None),
            # Where a *prefix* test and the analyzer's `fullmatch` disagree.
            # Without this row, restating the accession test as
            # `ext_id.startswith(("PMC", "PPR"))` passed the whole suite while
            # the identity assertion below sat there importing a constant it
            # no longer used (measured by mutation).
            (_epmc_body(inEPMC="Y", id="PMCnotanumber", source="PMC"), None),
            (_epmc_body(inEPMC="Y", id="PPR123 ", source="PPR"), None),
        ],
    )
    def test_a_request_is_made_exactly_where_the_category_says_it_would_be(
        self, body, expected_accession
    ):
        from bmlib.transparency.analyzer import _Analysis

        client = _ScriptedClient(*[_FakeResponse(404) for _ in range(2)])
        TransparencyAnalyzer(email="a@b.c")._check_europepmc(client, body, _Analysis(), "d")
        full_text_urls = [url for url in client.urls() if url.endswith("/fullTextXML")]

        category = sampler.observe_body("europepmc_search", _FakeResponse(200, body)).addressability
        asked = category in sampler.ADDRESSED_CATEGORIES

        assert asked == bool(full_text_urls)
        if expected_accession is not None:
            expected_url = f"{sampler.EUROPEPMC_REST_BASE}/{expected_accession}/fullTextXML"
            assert full_text_urls == [expected_url]

    def test_the_categories_that_ask_are_not_all_of_them(self):
        # The anti-vacuity half: were `ADDRESSED_CATEGORIES` every category,
        # `asked` would be constant and the comparison above would pass over
        # a sampler that agreed with nothing.
        assert sampler.ADDRESSED_CATEGORIES == frozenset({"pmcid", "id-accession"})

    def test_the_accession_test_is_the_analyzers_own(self):
        # The one predicate this script must **not** restate, for the reason
        # it imports the URLs: a restated accession test would put a record
        # in the `id-accession` row that bmlib refuses, or the reverse, in
        # the one table issue #188 is decided on.
        #
        # **This assertion alone is worth little, and saying so is the
        # point.** It is exactly the "checking that the constant was
        # imported" this module's own docstring calls weaker than driving
        # both — a mutant that kept the import and used
        # `startswith(("PMC", "PPR"))` passed it and the whole suite. What
        # has teeth is the parametrised comparison above, which now carries
        # two ids where a prefix test and a fullmatch disagree. This stays as
        # the statement of intent.
        from bmlib.transparency.analyzer import _EUROPEPMC_ACCESSION_RE

        assert sampler._EUROPEPMC_ACCESSION_RE is _EUROPEPMC_ACCESSION_RE

    def test_an_address_bmlib_refuses_is_still_probed(self):
        # Issue #216's whole point surviving issue #188's fix: bmlib stops
        # asking, and the table that licensed it keeps measuring. Keyed on
        # `PROBED_CATEGORIES`, which is why that is a second name.
        assert "id-not-an-address" in sampler.PROBED_CATEGORIES
        assert "id-not-an-address" not in sampler.ADDRESSED_CATEGORIES
        probes: list = []
        client = _ScriptedClient(
            _FakeResponse(200, {}),
            _FakeResponse(200, _epmc_body(inEPMC="Y", id="41637542", source="MED")),
            _FakeResponse(404),
            _FakeResponse(200, text=""),
            _FakeResponse(200, {}),
        )
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        sampler.probe_record(client, record, "a@b.c", _pace, probes)
        assert sampler._fulltext_url("41637542") in client.urls()
        assert [p.addressing.category for p in probes] == ["id-not-an-address"]


def _address_probe(
    category: str = "id-accession",
    *,
    source: str | None = "MED",
    open_access: str | None = "Y",
    cause: str | None = "http-404",
) -> sampler.AddressProbe:
    """One full-text address probe, built from its bucket."""
    # An accession of the category's *own* shape. It read
    # `f"{category[:3].upper()}1"`, which yields `"ID-1"` for `id-accession` —
    # a value the analyzer's regex refuses, so the helper built the one state
    # `_addressability` cannot produce, in the row issue #188 is decided on
    # (PR #219's review). `RecordAddressing.__post_init__` now refuses it.
    accession = {"pmcid": "PMC1", "id-accession": "PPR1", "id-not-an-address": "1"}[category]
    if cause is None:
        outcome = sampler.ProbeOutcome(
            endpoint="europepmc_fulltext",
            status=200,
            cause=None,
            shape=sampler.BodyShape(endpoint="europepmc_fulltext", top="served"),
        )
    else:
        kind, _, tail = cause.partition("-")
        outcome = sampler.ProbeOutcome(
            endpoint="europepmc_fulltext",
            status=None if kind == "exception" else int(tail),
            cause=cause,
            measured=kind != "unmeasured",
        )
    return sampler.AddressProbe(
        addressing=sampler.RecordAddressing(
            category, source=source, accession=accession, open_access=open_access
        ),
        outcome=outcome,
    )


class TestTheAddressTheTableCategorisesIsTheAddressItProbes:
    """Issue #216, and what it makes decidable: issue #188.

    The address table said how bmlib *would* address each record's full text
    and stopped there — nothing in this script ever built
    ``{EUROPEPMC_REST_BASE}/{accession}/fullTextXML``. So the finding issue
    #188's remedy rests on, *"the bare ``id`` 404s, three of three, against a
    ``pmcid`` address serving 53 kB"*, was a spot check quoted in four files
    beside a committed table that could not produce it; and the 404's own
    DEBUG level rested on a hand-taken draw in the same position. Both are now
    rows with a denominator and an interval.
    """

    def _probe(self, body, *, fulltext=None):
        probes: list = []
        client = _ScriptedClient(
            _FakeResponse(200, {}),
            _FakeResponse(200, body),
            fulltext if fulltext is not None else _FakeResponse(404),
            _FakeResponse(200, text=""),
            _FakeResponse(200, {}),
        )
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        sampler.probe_record(client, record, "a@b.c", _pace, probes)
        return client, probes

    @pytest.mark.parametrize(
        ("body", "accession"),
        [
            (_epmc_body(inEPMC="Y", pmcid="PMC1", id="1"), "PMC1"),
            (_epmc_body(inEPMC="Y", id="PPR123"), "PPR123"),
            (_epmc_body(inEPMC="Y", id="41637542"), "41637542"),
        ],
    )
    def test_the_address_the_record_offers_is_the_one_probed(self, body, accession):
        client, probes = self._probe(body)
        assert sampler._fulltext_url(accession) in client.urls()
        assert [p.addressing.accession for p in probes] == [accession]

    @pytest.mark.parametrize(
        "body",
        [
            {"resultList": {"result": []}},
            _epmc_body(pmcid="PMC1"),
            _epmc_body(inEPMC="N", pmcid="PMC1"),
            _epmc_body(inEPMC="Y"),
        ],
        ids=["no-record", "not-claimed-absent", "not-claimed-N", "unaddressable"],
    )
    def test_a_record_offering_no_address_is_not_probed(self, body):
        client, probes = self._probe(body)
        assert not [url for url in client.urls() if url.endswith("/fullTextXML")]
        assert probes == []

    def test_a_search_that_served_no_body_offers_nothing_to_probe(self):
        # Guarded on the shape rather than on the record, which is the only
        # thing that can carry an address: a 404 to the single-record lookup
        # leaves the sampler with no record at all, and building a URL from
        # the *draw* page instead would probe an address `analyze()` never
        # sees.
        probes: list = []
        client = _ScriptedClient(*[_FakeResponse(404) for _ in range(4)])
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        sampler.probe_record(client, record, "a@b.c", _pace, probes)
        assert probes == []

    def test_the_records_own_answers_ride_on_the_probe(self):
        # All three, because all three are cross-tabulated and none is
        # re-derived at the table: #188 turns on the source, and its second
        # population on `isOpenAccess`, which bmlib does not read at all.
        _client, probes = self._probe(
            _epmc_body(inEPMC="Y", id="41637542", source="MED", isOpenAccess="N")
        )
        assert (probes[0].addressing.source, probes[0].addressing.open_access) == ("MED", "N")

    def test_a_served_address_and_a_refused_one_read_differently(self):
        _client, served = self._probe(
            _epmc_body(inEPMC="Y", pmcid="PMC1"), fulltext=_FakeResponse(200, text="<article/>")
        )
        _client, refused = self._probe(_epmc_body(inEPMC="Y", pmcid="PMC1"))
        assert served[0].served
        assert not refused[0].served

    def test_the_body_shape_of_a_full_text_answer_is_whether_a_document_arrived(self):
        # Deliberately two values. Everything past this in
        # `_fetch_europepmc_fulltext` is a judgement about the document, and
        # an instrument does not import the predicate under test.
        assert sampler.observe_body("europepmc_fulltext", _FakeResponse(200, text="<a/>")).top == (
            "served"
        )
        assert sampler.observe_body("europepmc_fulltext", _FakeResponse(200, text="")).top == (
            "empty"
        )

    def test_a_wholly_whitespace_body_is_served_and_not_empty(self):
        # The one boundary the analyzer settles deliberately, and the
        # instrument must agree with it rather than merely happen to: that
        # module tests `not served` and never `not served.strip()`, because
        # the stricter form takes a wholly-whitespace body out of the
        # entirely-nested branch and reports it as nothing served. Mutating
        # `_fulltext_kind` to `text.strip()` used to leave all 239 green
        # (PR #219's review), on the endpoint whose whole `empty` row exists
        # to measure issue #190.
        assert sampler.observe_body("europepmc_fulltext", _FakeResponse(200, text="   ")).top == (
            "served"
        )

    def test_a_full_text_body_is_never_handed_to_a_json_decoder(self):
        # `_TEXT_ENDPOINTS` is derived from `_BODY_KINDS`, so the two cannot
        # disagree about which bodies are decoded — an endpoint in one and not
        # the other either has its XML given to `resp.json()` or reaches a
        # `KeyError` in the dispatch.
        assert sampler._TEXT_ENDPOINTS == frozenset(sampler._BODY_KINDS)
        assert "europepmc_fulltext" in sampler._TEXT_ENDPOINTS

    def test_the_text_endpoints_are_derived_and_not_restated(self):
        # The assertion above compares *values*, which a restated literal
        # passes — `frozenset({"pubmed_efetch", "europepmc_fulltext"})` left
        # all 239 green (PR #219's review). What the comment claims is that
        # the two *cannot* disagree, which is a property of the statement, so
        # it is read off the source: `TestTheAuditNetIsComplete`'s rule that
        # a rule enforced by prose is not enforced.
        tree = ast.parse(_SAMPLER_PATH.read_text())
        assigned = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_TEXT_ENDPOINTS" for t in node.targets)
        ]
        assert len(assigned) == 1, "_TEXT_ENDPOINTS is assigned somewhere unexpected"
        call = assigned[0].value
        assert isinstance(call, ast.Call), "_TEXT_ENDPOINTS is not built from anything"
        assert [a.id for a in call.args if isinstance(a, ast.Name)] == ["_BODY_KINDS"], (
            "_TEXT_ENDPOINTS no longer reads _BODY_KINDS, so the two can disagree about "
            "which bodies observe_body decodes"
        )


class TestWhatBecameOfEachFullTextAddress:
    """The table itself — issues #216 and #188."""

    def test_an_absent_population_is_an_error_and_not_a_clean_zero(self):
        assert "ERROR" in sampler.summarise_addresses([])[0]
        assert not sampler.addresses_reportable([])

    def test_a_throttled_population_reports_no_distribution(self):
        probes = [_address_probe(cause="unmeasured-429") for _ in range(4)]
        assert not sampler.addresses_reportable(probes)
        assert "throttled" in sampler.summarise_addresses(probes)[0]

    def test_a_throttled_probe_enters_no_denominator(self):
        # The rule every sampler here follows: the sampler failing is not the
        # endpoint failing, and a share computed over it sets a level from its
        # own rate limiting.
        probes = [
            *[_address_probe(cause=None) for _ in range(4)],
            _address_probe(cause="unmeasured-503"),
        ]
        row = next(line for line in sampler.summarise_addresses(probes) if "id-accession" in line)
        assert "4 probed" in row and "4 served" in row

    def test_the_id_fallback_rows_are_split_by_source_and_the_pmcid_row_is_not(self):
        # #188's whole split: a `PPR` record's bare `id` is the only address
        # it has and a `MED` record's is a PMID. On a `pmcid` row the source
        # would fan one population into three for no question.
        probes = [
            _address_probe("id-accession", source="MED"),
            _address_probe("id-accession", source="PPR", cause=None),
            _address_probe("pmcid", source="MED", cause=None),
        ]
        lines = sampler.summarise_addresses(probes)
        assert any("id-accession, source MED" in line and "0 served" in line for line in lines)
        assert any("id-accession, source PPR" in line and "1 served" in line for line in lines)
        assert any(line.strip().startswith("pmcid ") for line in lines)
        assert not any("pmcid, source" in line for line in lines)

    def test_the_population_the_analyzer_asks_with_is_its_own_row(self):
        # The row four documents quoted the *pooled* share as. `46 of 52` is
        # over `PROBED_CATEGORIES`, and issue #188 had just narrowed the 404
        # branch to `ADDRESSED_CATEGORIES` — 3 of 9 in the same draw, an
        # interval that does not overlap the pooled one. Without this row the
        # level's own denominator existed nowhere on the page.
        probes = [
            _address_probe("pmcid", cause=None),
            _address_probe("pmcid"),
            _address_probe("id-not-an-address"),
            _address_probe("id-not-an-address"),
            _address_probe("id-not-an-address"),
        ]
        row = next(
            line
            for line in sampler.summarise_addresses(probes)
            if "addresses bmlib asks with" in line
        )
        assert "2 probed" in row and "1 served" in row
        assert "95% CI" in row

    def test_a_probed_category_no_record_offered_says_so(self):
        # `id-accession` measured 0 in the 2026-09-09 draw, so the fallback
        # the shape test exists to preserve was unexercised and the table said
        # nothing — *"served 0 of 0"* and *"never drawn"* being the
        # distinction this script is built to keep (`summarise_shapes`' own
        # `NO POPULATION HERE`).
        lines = sampler.summarise_addresses([_address_probe("pmcid")])
        assert any("id-accession" in line and "NO POPULATION HERE" in line for line in lines)
        assert any("id-not-an-address" in line and "NO POPULATION HERE" in line for line in lines)
        assert not any("pmcid" in line and "NO POPULATION HERE" in line for line in lines)

    def test_an_empty_body_is_not_counted_as_a_served_document(self):
        # `served` read `outcome.ok`, i.e. HTTP 200 — while `_fulltext_kind`
        # exists to split a 200 into served and empty, and issue #190's whole
        # finding is that a 200 alone is not a document. The address table
        # called such a probe served while the shape table called it empty
        # (PR #219's review). 0 empty of 6 in the committed draw, so nothing
        # published moves.
        empty = sampler.AddressProbe(
            addressing=sampler.RecordAddressing("pmcid", accession="PMC1"),
            outcome=sampler.ProbeOutcome(
                endpoint="europepmc_fulltext",
                status=200,
                cause=None,
                shape=sampler.BodyShape(endpoint="europepmc_fulltext", top="empty"),
            ),
        )
        assert not empty.served
        assert not empty.is_unmeasured
        assert _address_probe("pmcid", cause=None).served

    def test_every_row_carries_its_interval(self):
        # These are the rows that get quoted, and a bare `0.0%` over three
        # probes and over three hundred read identically — `summarise_shapes`'
        # own reason, and here it is the strength of #188's claim.
        lines = sampler.summarise_addresses([_address_probe()])
        assert all("95% CI" in line for line in lines[1:] if "probed" in line)

    def test_the_open_access_cross_is_reported_beside_it(self):
        # Issue #188's second population, recorded rather than acted on:
        # `inEPMC` says EuropePMC holds the text where this endpoint serves
        # the open-access subset of it.
        probes = [
            _address_probe(open_access="N"),
            _address_probe(open_access="Y", cause=None),
            _address_probe(open_access=None),
        ]
        lines = sampler.summarise_addresses(probes)
        assert any("isOpenAccess N" in line and "0 served" in line for line in lines)
        assert any("isOpenAccess Y" in line and "1 served" in line for line in lines)
        assert any("isOpenAccess (absent)" in line for line in lines)

    def test_a_row_whose_probes_were_all_throttled_says_so_rather_than_scoring_zero(self):
        # A category can be wholly throttled while the population as a whole
        # is reportable, and `wilson` refuses a zero denominator — so the row
        # says it was not measured instead of printing a share of nothing.
        probes = [
            _address_probe("pmcid", cause="unmeasured-429"),
            *[_address_probe("id-accession", cause=None) for _ in range(9)],
        ]
        assert sampler.addresses_reportable(probes)
        assert any(
            "pmcid" in line and "none measured" in line
            for line in sampler.summarise_addresses(probes)
        )


class TestAnAddressProbeDescribesAnEventThatHappened:
    """The same rule ``ProbeOutcome.__post_init__`` makes, one population over."""

    def test_a_category_offering_no_address_cannot_carry_one(self):
        with pytest.raises(ValueError, match="disagrees with accession"):
            sampler.RecordAddressing("not-claimed", accession="PMC1")

    def test_a_category_offering_an_address_must_carry_one(self):
        # The direction that loses records silently: a `pmcid` record with no
        # accession would simply never be probed, thinning the denominator
        # issue #188 is decided on with nothing printed.
        with pytest.raises(ValueError, match="disagrees with accession"):
            sampler.RecordAddressing("pmcid")

    def test_an_unknown_category_is_refused(self):
        with pytest.raises(ValueError, match="unknown address category"):
            sampler.RecordAddressing("invented")

    @pytest.mark.parametrize(
        ("category", "accession"),
        [("id-accession", "1"), ("id-not-an-address", "PMC1"), ("id-accession", "ID-1")],
    )
    def test_an_id_fallback_category_must_agree_with_the_shape_of_its_id(self, category, accession):
        # The relation issue #188 turns on, and it is derivable from two
        # stored fields — so it was re-encodable wrongly, and was: this
        # module's own helper built `id-accession` carrying `"ID-1"` and
        # passed the whole suite (PR #219's review).
        with pytest.raises(ValueError, match="disagrees with the shape"):
            sampler.RecordAddressing(category, accession=accession)

    def test_a_pmcid_is_not_held_to_that_rule(self):
        # The anti-vacuity half. `pmcid` is not an `id` fallback, so the shape
        # rule must not reach it — EuropePMC's `pmcid` field is its own
        # authority and a malformed one is PR #208's case, not #188's.
        assert sampler.RecordAddressing("pmcid", accession="whatever")

    def test_a_probe_of_a_record_offering_no_address_is_refused(self):
        # Unpinned until PR #219's review: replacing this guard with
        # `if False:` passed all 239 tests, while the branch is live — the
        # sibling endpoint refusal three lines below it had a test and this
        # one did not. `_check_trial_results`' own defence, one package over,
        # was unpinned exactly this way for a release.
        with pytest.raises(ValueError, match="offers no address to probe"):
            sampler.AddressProbe(
                addressing=sampler.RecordAddressing("no-record"),
                outcome=sampler.ProbeOutcome(
                    endpoint="europepmc_fulltext", status=404, cause="http-404"
                ),
            )

    def test_a_probe_of_another_endpoint_is_not_a_full_text_probe(self):
        with pytest.raises(ValueError, match="not a 'crossref' one"):
            sampler.AddressProbe(
                addressing=sampler.RecordAddressing("pmcid", accession="PMC1"),
                outcome=sampler.ProbeOutcome(endpoint="crossref", status=404, cause="http-404"),
            )

    def test_what_the_script_probes_and_what_bmlib_asks_are_different_questions(self):
        # They coincide today and are two names because issue #188 separates
        # them: bmlib stops asking with an address it can know will 404, and a
        # table keyed on what bmlib asks would then stop measuring the very
        # thing that licensed the refusal.
        assert sampler.PROBED_CATEGORIES <= sampler.ALL_ADDRESS_CATEGORIES
        # Not equal, which is the whole of why there are two names — and the
        # assertion `PROBED_CATEGORIES` names in its own comment.
        assert sampler.PROBED_CATEGORIES != sampler.ADDRESSED_CATEGORIES
        assert sampler.PROBED_CATEGORIES - sampler.ADDRESSED_CATEGORIES == {"id-not-an-address"}
        # Every probed category can carry an address, each of its own shape:
        # `RecordAddressing` refuses an `id-accession` whose id is not one.
        for category, accession in (
            ("pmcid", "PMC1"),
            ("id-accession", "PPR1"),
            ("id-not-an-address", "1"),
        ):
            assert sampler.RecordAddressing(category, accession=accession)


def _efetch_with(*accessions: str) -> _FakeResponse:
    """A PubMed record whose ``DataBankList`` names *accessions*."""
    numbers = "".join(f"<AccessionNumber>{a}</AccessionNumber>" for a in accessions)
    return _FakeResponse(
        200,
        text=(
            "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
            "<DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName>"
            f"<AccessionNumberList>{numbers}</AccessionNumberList></DataBank></DataBankList>"
            "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
        ),
    )


class TestTheResultsCheckSaysWhatItCouldAskAndWhatAnswered:
    """Issue #206's population, on the draw that already probes these accessions.

    Two silences, one per half. ``MAX_TRIAL_IDS_TO_CHECK`` sliced an
    **unbounded** list — ``_parse_pubmed_signals`` collects every
    ClinicalTrials.gov accession that is a well-formed NCT id — so a pooled
    report's fourth accession was dropped with no log line and no indicator.
    And ``answered`` went ``True`` on the *first* accession that replied, so
    one reachable *"no results"* outvoted any number of unreachable ones and
    the paper stored *"Registered trial without posted results"*: issue #194's
    class of false claim, narrowed by the tri-state rather than removed.

    Neither could be decided without knowing how often a paper carries more
    than three accessions, and how often a check is partly answered. Both are
    counted here, and PR #225 acted on the counts: the walk now stores
    ``TrialResultsStatus.PARTLY_ANSWERED`` and WARNs on a truncation, so these
    rows size that member rather than a silence. The cap itself is unchanged,
    so *how far* a list runs past it is still open.
    """

    def _check(self, *responses, accessions=("NCT00000001",)):
        client = _ScriptedClient(_efetch_with(*accessions), *responses)
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        checks: list[sampler.TrialCheck] = []
        sampler.probe_trials(client, record, "a@b.c", _pace, checks=checks)
        return checks

    def test_every_accession_is_counted_before_the_cap_is_applied(self):
        [check] = self._check(
            *[_FakeResponse(200, {"hasResults": True}) for _ in range(3)],
            accessions=("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004", "NCT00000005"),
        )
        assert check.found == 5

    def test_only_the_accessions_bmlib_would_ask_about_are_probed(self):
        [check] = self._check(
            *[_FakeResponse(200, {"hasResults": True}) for _ in range(3)],
            accessions=("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004", "NCT00000005"),
        )
        assert check.probed == sampler.MAX_TRIAL_IDS_TO_CHECK
        assert check.truncated

    def test_a_record_within_the_cap_is_not_truncated(self):
        [check] = self._check(_FakeResponse(200, {"hasResults": True}))
        assert (check.found, check.probed, check.truncated) == (1, 1, False)

    @pytest.mark.parametrize(
        "body",
        [{"hasResults": "no"}, {"hasResults": 1}, [1, 2]],
        ids=["wrong-typed-string", "wrong-typed-number", "not-an-object"],
    )
    def test_a_200_bmlib_could_not_read_did_not_answer(self, body):
        # `answered` counted HTTP 200 where bmlib counts *"did the method
        # return non-`None`"* — and since PR #208 routed the value through
        # `_json_bool` it returns `None` for a non-object body and for a
        # wrong-typed `hasResults`. Counting 200s inflated `complete` and
        # deflated `partial` and `unanswered`, the two rows issue #206 turns
        # on. It is also the wrong-typed-boolean shape `_json_bool`'s own
        # docstring says no contract net can see, which makes this the one
        # population that ought to see it (PR #213's review).
        [check] = self._check(_FakeResponse(200, body))
        assert check.answered == 0
        assert check.verdict == "unanswered"

    @pytest.mark.parametrize(
        "body", [{"hasResults": True}, {"hasResults": False}, {}], ids=["true", "false", "absent"]
    )
    def test_a_200_bmlib_could_read_did_answer(self, body):
        # The other side, and the `absent` case is deliberate: an absent key
        # keeps the `False` that `test_missing_has_results_is_false` has
        # pinned since before the tri-state existed (issue #210, which this
        # sampler's own first run settled at 0 of 55).
        [check] = self._check(_FakeResponse(200, body))
        assert check.answered == 1
        assert check.verdict == "complete"

    def test_the_answer_test_agrees_with_the_analyzer(self):
        # Driven against `_check_trial_results` itself rather than restated:
        # for each body, "would bmlib have had an answer?" must match what
        # this script counted.
        analyzer = TransparencyAnalyzer(email="a@b.c")
        for body in ({"hasResults": True}, {"hasResults": "no"}, {}, [1, 2], "s"):
            resp = _FakeResponse(200, body)
            bmlib_answered = (
                analyzer._check_trial_results(_ScriptedClient(resp), "NCT00000001") is not None
            )
            outcome = sampler.probe(_ScriptedClient(resp), "clinicaltrials", "u")
            assert sampler.trial_answered(outcome) is bmlib_answered, body

    def test_a_check_every_accession_answered_is_complete(self):
        [check] = self._check(
            _FakeResponse(200, {"hasResults": True}),
            _FakeResponse(200, {"hasResults": False}),
            accessions=("NCT00000001", "NCT00000002"),
        )
        assert check.verdict == "complete"

    def test_a_check_one_accession_did_not_answer_is_partial(self):
        # The stored finding says "without posted results"; the accession that
        # did not answer may be the trial that has them.
        [check] = self._check(
            _FakeResponse(200, {"hasResults": False}),
            _FakeResponse(404),
            accessions=("NCT00000001", "NCT00000002"),
        )
        assert check.verdict == "partial"

    def test_a_check_nothing_answered_is_its_own_verdict(self):
        [check] = self._check(_FakeResponse(404))
        assert check.verdict == "unanswered"

    def test_a_throttled_probe_leaves_the_check_unclassifiable(self):
        # The sampler was throttled, not ClinicalTrials.gov, so this record
        # enters no verdict denominator — the rule every other population here
        # follows.
        [check] = self._check(*[_FakeResponse(429) for _ in range(sampler.MAX_PROBE_ATTEMPTS)])
        assert check.verdict == "unmeasured"

    def test_a_record_with_no_accession_records_no_check(self):
        client = _ScriptedClient(_efetch_with(), _FakeResponse(200, {}))
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        checks: list[sampler.TrialCheck] = []
        sampler.probe_trials(client, record, "a@b.c", _pace, checks=checks)
        assert checks == []

    def test_the_accession_list_itself_is_uncapped(self):
        # `trial_ids_for` reports what the record carries; the cap belongs
        # where bmlib applies it, or the count above could never be taken.
        record = sampler.DrawnRecord(source="MED", year=2024, doi="10.1/x", pmid="1", raw={})
        efetch = _efetch_with("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004")
        found = sampler.trial_ids_for(record, efetch.text)
        assert len(found) == 4


class TestWhetherAnyRegistrationSourceAnsweredAtAll:
    """Issue #204's population, derived from probes the run already makes.

    ``TransparencyResult.trial_registered`` is a bare ``bool``, ``False`` both
    for a paper reporting no registered trial and for one whose sources never
    answered — and after issue #202 the second is *structurally* reachable
    rather than incidental, ``_find_trial_ids`` returning ``[]`` for a record
    that never arrived. Whether that deserves a fourth status enum, a derived
    read, or a recorded residual turns on how big the second case is, and the
    issue's own third option says so: *"defensible only if the population is
    small, which nobody has measured"*.

    Two sources feed the check: the EuropePMC record, whose abstract the
    heuristic scans, and PubMed's ``<DataBankList>``. A record for which
    neither answered is one where ``False`` is a claim about bmlib rather than
    about the paper.
    """

    def _outcomes(self, *pairs):
        return [
            sampler.ProbeOutcome(
                endpoint=endpoint,
                status=200 if ok else 404,
                cause=None if ok else "http-404",
                shape=_served_shape(endpoint) if ok else None,
            )
            for endpoint, ok in pairs
        ]

    def test_both_sources_answering_is_the_ordinary_case(self):
        outcomes = self._outcomes(("europepmc_search", True), ("pubmed_efetch", True))
        assert sampler.source_reach(outcomes) == "both"

    def test_only_europepmc_answering_is_named(self):
        outcomes = self._outcomes(("europepmc_search", True), ("pubmed_efetch", False))
        assert sampler.source_reach(outcomes) == "epmc-only"

    def test_only_pubmed_answering_is_named(self):
        outcomes = self._outcomes(("europepmc_search", False), ("pubmed_efetch", True))
        assert sampler.source_reach(outcomes) == "pubmed-only"

    def test_neither_answering_is_the_population_the_issue_asks_for(self):
        outcomes = self._outcomes(("europepmc_search", False), ("pubmed_efetch", False))
        assert sampler.source_reach(outcomes) == "neither"

    def test_a_record_with_no_pmid_has_no_pubmed_source_when_the_search_failed(self):
        # No efetch is made, and no PMID can be recovered from a search that
        # did not answer — so bmlib reaches the registration check with
        # nothing, which is the same population by a different route.
        assert sampler.source_reach(self._outcomes(("europepmc_search", False))) == "neither"

    def test_a_record_with_no_pmid_still_counts_the_search_that_answered(self):
        assert sampler.source_reach(self._outcomes(("europepmc_search", True))) == "epmc-only"

    def test_a_throttled_probe_leaves_the_record_unclassifiable(self):
        outcomes = [
            sampler.ProbeOutcome(
                endpoint="europepmc_search", status=429, cause="unmeasured-429", measured=False
            ),
            *self._outcomes(("pubmed_efetch", True)),
        ]
        assert sampler.source_reach(outcomes) == "unmeasured"

    def test_the_other_endpoints_do_not_decide_it(self):
        # CrossRef and OpenAlex feed no registration signal, so a run in which
        # both answered says nothing about whether a trial could be found.
        outcomes = self._outcomes(
            ("crossref", True), ("openalex", True), ("europepmc_search", False)
        )
        assert sampler.source_reach(outcomes) == "neither"


def _served(endpoint: str, body: object) -> sampler.ProbeOutcome:
    """One served outcome carrying the shape of *body*."""
    return sampler.probe(_ScriptedClient(_FakeResponse(200, body)), endpoint, "u")


class TestTheShapeTableFollowsTheRulesEveryTableHereFollows:
    """A shape population is a population, so it obeys the same three rules.

    A body that could not be fetched enters no denominator; a population past
    ``UNMEASURED_SHARE_ERROR_THRESHOLD`` reports ERROR rather than a share;
    and a zero over an absent population is not a clean result — nothing
    served and nothing malformed must not print alike, because a healthy
    endpoint is exactly what an unsampled one looks like.
    """

    def test_nothing_served_is_an_error_and_not_a_clean_distribution(self):
        [line] = sampler.summarise_shapes("crossref", [_outcome("http-404")])
        assert "ERROR" in line

    def test_an_empty_population_is_an_error_too(self):
        [line] = sampler.summarise_shapes("crossref", [])
        assert "ERROR" in line

    def test_a_throttled_population_is_an_error_here_as_well(self):
        # Delegated to `is_reportable`, so the shape table cannot report a
        # distribution the status table above it refused to report.
        outcomes = [_outcome("unmeasured-429") for _ in range(5)] + [_outcome(None)]
        assert any("ERROR" in line for line in sampler.summarise_shapes("crossref", outcomes))

    def test_the_top_level_distribution_names_each_kind_it_saw(self):
        outcomes = [_served("crossref", {}), _served("crossref", []), _served("crossref", {})]
        text = "\n".join(sampler.summarise_shapes("crossref", outcomes))
        assert "object" in text and "array" in text

    def test_a_fields_denominator_is_the_bodies_it_could_be_asked_of(self):
        # `open_access.is_oa` is reachable in one of these two bodies, so its
        # row is over one — not over two, which would report a question that
        # could not be asked as an answer.
        outcomes = [
            _served("openalex", {"open_access": {"is_oa": True}}),
            _served("openalex", {"open_access": "yes"}),
        ]
        rows = {
            line.split()[0]: line for line in sampler.summarise_shapes("openalex", outcomes)[1:]
        }
        assert " 1 " in rows["open_access.is_oa"]
        assert " 2 " in rows["open_access"]

    def test_a_fields_row_names_the_kinds_it_actually_saw(self):
        # **Issue #211's own deliverable**, and it was pinned by nothing:
        # replacing the kind breakdown with a constant passed the whole suite
        # (measured). The denominator above is what a mis-declared path moves;
        # *this* is the cell the run's headline — "0 non-object at all five
        # endpoints", "`hasResults` absent in 0 of 55" — is read out of.
        outcomes = [
            _served("openalex", {"open_access": {"is_oa": True}}),
            _served("openalex", {"open_access": {"is_oa": "false"}}),
            _served("openalex", {"open_access": {}}),
        ]
        rows = {
            line.split()[0]: line for line in sampler.summarise_shapes("openalex", outcomes)[1:]
        }
        # The wrong-typed boolean is the shape `_json_bool` exists for, and
        # the one no contract net can see, so the row has to name it.
        assert "boolean=1" in rows["open_access.is_oa"]
        assert "string=1" in rows["open_access.is_oa"]
        assert "absent=1" in rows["open_access.is_oa"]

    def test_a_top_level_row_carries_its_interval(self):
        # The status table has carried one all along; without it here a bare
        # `100.0%` over four bodies and over four hundred read identically,
        # and these are the rows that get quoted.
        outcomes = [_served("crossref", {}) for _ in range(4)]
        rows = sampler.summarise_shapes("crossref", outcomes)
        assert "95% CI" in rows[1], rows

    def test_a_shape_population_thinned_by_failures_is_an_error(self):
        # `bool(shapes)` was the whole floor, so one served body out of many
        # printed `100.0%` with no interval while the status table above
        # honestly reported the endpoint failing almost every probe. A non-200
        # is the measurement for that table and a hole for this one.
        outcomes = [_served("crossref", {})] + [
            sampler.ProbeOutcome(endpoint="crossref", status=404, cause="http-404")
            for _ in range(20)
        ]
        assert sampler.shapes_reportable("crossref", outcomes) is False
        assert any("too few to report" in line for line in sampler.summarise_shapes("cr", outcomes))

    def test_a_shape_population_that_mostly_served_is_reported(self):
        # The other edge, so the floor is not simply "any failure at all".
        outcomes = [_served("crossref", {}) for _ in range(19)] + [
            sampler.ProbeOutcome(endpoint="crossref", status=404, cause="http-404")
        ]
        assert sampler.shapes_reportable("crossref", outcomes) is True

    def test_the_endpoint_whose_404_is_the_finding_keeps_its_shape_table(self):
        # Found by the 2026-09-09 live run rather than by review. That rule
        # was written for five endpoints at which a non-200 is close to
        # unheard of; `europepmc_fulltext`'s gate is deliberately wider than
        # what it serves, so a 404 is its ordinary majority outcome — 46 of
        # 52 — and the shape table reported ERROR and flipped the exit code
        # on a clean run. The same outcomes at any other endpoint still do.
        outcomes = [
            sampler.ProbeOutcome(
                endpoint="europepmc_fulltext",
                status=200,
                cause=None,
                shape=sampler.BodyShape(endpoint="europepmc_fulltext", top="served"),
            )
        ] + [
            sampler.ProbeOutcome(endpoint="europepmc_fulltext", status=404, cause="http-404")
            for _ in range(20)
        ]
        assert sampler.shapes_reportable("europepmc_fulltext", outcomes) is True
        assert sampler.shapes_reportable("crossref", _as(outcomes, "crossref")) is False

    @pytest.mark.parametrize(
        "endpoint", ["crossref", "europepmc_search", "pubmed_efetch", "openalex", "clinicaltrials"]
    )
    def test_no_other_endpoint_gets_the_exception(self, endpoint):
        # The negative half tested `crossref` alone, so adding a *wrong*
        # member — `{"europepmc_fulltext", "clinicaltrials"}` — left all 239
        # green (PR #219's review). A wrongly-added member silently exempts
        # an endpoint from the thinned-population ERROR that keeps a quoted
        # share off a remnant.
        assert endpoint not in sampler._ENDPOINTS_WHOSE_SHAPE_IS_OVER_SERVED_BODIES

    def test_the_exception_has_exactly_one_member(self):
        # Its own docstring says *"one member, and it earned its place from a
        # live run"*, which was prose beside a set anyone could widen. The
        # rule is that a member is earned by a draw, and no test can check
        # that a draw was taken — so what is checked is that the set did not
        # grow without this line being edited.
        assert sampler._ENDPOINTS_WHOSE_SHAPE_IS_OVER_SERVED_BODIES == frozenset(
            {"europepmc_fulltext"}
        )

    def test_it_is_not_a_free_pass(self):
        # The exception drops one rule and keeps the rest: an endpoint that
        # served nothing at all is still an ERROR, and a throttled population
        # is still one.
        assert not sampler.shapes_reportable(
            "europepmc_fulltext",
            [
                sampler.ProbeOutcome(endpoint="europepmc_fulltext", status=404, cause="http-404")
                for _ in range(4)
            ],
        )
        assert not sampler.shapes_reportable(
            "europepmc_fulltext",
            [
                sampler.ProbeOutcome(
                    endpoint="europepmc_fulltext",
                    status=429,
                    cause="unmeasured-429",
                    measured=False,
                )
                for _ in range(4)
            ],
        )

    def test_a_field_no_served_body_could_be_asked_says_so_rather_than_vanishing(self):
        # Otherwise "never wrong-typed" and "never reachable" print alike,
        # and the second is what a mis-declared path looks like.
        outcomes = [_served("openalex", [])]
        rows = "\n".join(sampler.summarise_shapes("openalex", outcomes))
        assert "cited_by_count" in rows
        assert "NO POPULATION" in rows


class TestTheRiderTablesRefuseAnAbsentPopulation:
    """The three counters carried for issues #204, #206, #207 and #188.

    Each is a population in its own right with its own denominator, so each
    reports ERROR rather than a share when it has none — the failure mode
    every one of these issues is blocked on is a *number*, and a zero over an
    absent population would answer them wrongly rather than not at all.
    """

    def test_no_addressed_record_is_an_error(self):
        assert any("ERROR" in line for line in sampler.summarise_addressing([]))

    def test_an_address_table_reports_each_category_it_saw(self):
        outcomes = [
            _served("europepmc_search", _epmc_body(inEPMC="Y", pmcid="PMC1")),
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="PPR1", source="PPR")),
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="41637542", source="MED")),
        ]
        text = "\n".join(sampler.summarise_addressing(outcomes))
        assert "pmcid" in text
        # Both `id`-fallback categories, split by the source the fallback
        # turns on — that split is issue #188's whole finding.
        assert "id-accession, source PPR" in text
        assert "id-not-an-address, source MED" in text

    def test_the_table_says_how_many_records_a_request_would_be_made_for(self):
        # The denominator of the full-text fetch population, and what makes
        # `ADDRESSED_CATEGORIES` load-bearing in the report rather than a
        # constant only a test reads.
        outcomes = [
            _served("europepmc_search", _epmc_body(inEPMC="Y", pmcid="PMC1")),
            _served("europepmc_search", _epmc_body(inEPMC="N")),
            _served("europepmc_search", _epmc_body(inEPMC="Y")),
        ]
        text = "\n".join(sampler.summarise_addressing(outcomes))
        assert "1 of 3" in text

    def test_both_id_fallback_categories_are_split_by_source(self):
        # Issue #188 turns on exactly this: a `PPR` accession is the only
        # address a preprint has, and a `MED` record's bare id is a PMID.
        #
        # It was named for `id-only` — a category retired by the split it
        # claims to demonstrate — and built `id="X"` under `source="PPR"`,
        # which is not an accession, so **both** records were
        # `id-not-an-address` and the test exercised one category twice. The
        # loose `"PPR" in text` was what let it pass (PR #219's review).
        outcomes = [
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="PPR1", source="PPR")),
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="9", source="MED")),
        ]
        text = "\n".join(sampler.summarise_addressing(outcomes))
        assert "id-accession, source PPR" in text
        assert "id-not-an-address, source MED" in text

    def test_a_population_that_found_none_of_its_own_records_is_an_error(self):
        # Every drawn record came from this same API, so a population that is
        # wholly `no-record` cannot be a fact about the corpus — it is this
        # script's single-record lookup having stopped working. It used to
        # print "a full-text request would be made for 0 of N" at exit 0: a
        # spectacular finding about bmlib manufactured out of a broken lookup.
        outcomes = [_served("europepmc_search", {"resultList": {"result": []}}) for _ in range(5)]
        assert sampler.addressing_reportable(outcomes) is False
        lines = sampler.summarise_addressing(outcomes)
        assert any("found none of the records it drew" in line for line in lines)
        assert not any("would be made for" in line for line in lines)

    def test_one_real_record_is_enough_to_report(self):
        # The other edge: `no-record` is a genuine bmlib outcome, so a
        # population merely containing some must still report.
        outcomes = [
            _served("europepmc_search", {"resultList": {"result": []}}),
            _served("europepmc_search", _epmc_body(inEPMC="N")),
        ]
        assert sampler.addressing_reportable(outcomes) is True
        assert any("re-find" in line for line in sampler.summarise_addressing(outcomes))

    def test_a_body_that_carried_no_record_is_not_an_addressed_shape(self):
        # The filter is load-bearing, not tidiness: `summarise_addressing`
        # formats the category into a fixed-width field, so an uncategorised
        # shape reaching it raises `TypeError` on `None`. That body is issue
        # #184's exact live failure — EuropePMC answering 200 with HTML.
        outcomes = [
            _served("europepmc_search", _epmc_body(inEPMC="N")),
            sampler.ProbeOutcome(
                endpoint="europepmc_search",
                status=200,
                cause=None,
                shape=sampler.observe_body("europepmc_search", _FakeResponse(200, text="<html>")),
            ),
        ]
        lines = sampler.summarise_addressing(outcomes)
        assert any("1 records categorised" in line for line in lines)

    def test_no_trial_check_is_an_error(self):
        assert any("ERROR" in line for line in sampler.summarise_trial_checks([]))

    def test_an_absent_population_is_never_reportable(self):
        # `_population_reportable`'s own first clause, which nothing exercised:
        # inverting it made both predicates below answer True while their
        # summarisers printed ERROR — the disagreement they exist to prevent.
        assert sampler.checks_reportable([]) is False
        assert sampler.reach_reportable([]) is False

    def test_a_wholly_unmeasured_check_population_is_an_error(self):
        checks = [sampler.TrialCheck(found=1, probed=1, answered=0, unmeasured=1)]
        assert any("ERROR" in line for line in sampler.summarise_trial_checks(checks))

    def test_a_check_whose_counters_contradict_each_other_is_refused(self):
        # The ordering `found >= probed >= answered + unmeasured >= 0` is the
        # whole meaning of the type and lived only in prose. The same argument
        # was accepted for `ProbeOutcome` in this PR, where two impossible
        # outcomes were being built by test helpers.
        with pytest.raises(ValueError, match="exceeds"):
            sampler.TrialCheck(found=1, probed=1, answered=2, unmeasured=0)
        with pytest.raises(ValueError, match="exceeds the"):
            sampler.TrialCheck(found=1, probed=2, answered=0, unmeasured=0)
        with pytest.raises(ValueError, match="no count is negative"):
            sampler.TrialCheck(found=1, probed=1, answered=-1, unmeasured=0)
        with pytest.raises(ValueError, match="bmlib's own cap"):
            sampler.TrialCheck(
                found=99, probed=sampler.MAX_TRIAL_IDS_TO_CHECK + 1, answered=0, unmeasured=0
            )

    def test_the_trial_table_reports_the_truncated_share_too(self):
        checks = [
            sampler.TrialCheck(found=5, probed=3, answered=3, unmeasured=0),
            sampler.TrialCheck(found=1, probed=1, answered=1, unmeasured=0),
        ]
        text = "\n".join(sampler.summarise_trial_checks(checks))
        assert "truncated" in text and "complete" in text

    def test_no_reach_verdict_is_an_error(self):
        assert any("ERROR" in line for line in sampler.summarise_source_reach([]))

    def test_a_wholly_unmeasured_reach_population_is_an_error(self):
        assert any("ERROR" in line for line in sampler.summarise_source_reach(["unmeasured"]))

    def test_the_reach_table_reports_the_population_the_issue_asks_for(self):
        text = "\n".join(sampler.summarise_source_reach(["both", "both", "neither"]))
        assert "neither" in text


class TestTheNewTablesReachTheReportAndTheExitCode:
    """A table nothing prints and no exit code reads is not an instrument.

    ``main`` is where issue #211's counters become evidence, and this
    repository has already been caught with a counter registered and never
    read (``_FORMULA_ROUTING_COUNTERS``, which printed a population nothing
    had counted). Both halves are pinned: the tables are printed, and each
    one's absent population is carried into the exit code, since a scheduled
    re-run is judged by that alone.
    """

    def _run(self, monkeypatch, client, argv=("--email", "a@b.c", "--target", "9")):
        import httpx

        monkeypatch.setattr(sys, "argv", ["sample_api_failures.py", *argv])
        monkeypatch.setattr(sampler, "_make_pacer", lambda _interval: _pace)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _ContextClient(client))
        return sampler.main()

    def _page(self, n=1):
        return _draw_page(n)

    def test_a_clean_run_prints_every_new_table(self, monkeypatch, capsys):
        assert self._run(monkeypatch, _AlwaysClient(self._page())) == 0
        out = capsys.readouterr().out
        assert "bodies served" in out
        assert "records categorised" in out
        assert "addresses probed" in out
        assert "papers with at least one accession" in out
        assert "records with a source outcome" in out

    def test_the_field_rows_reach_the_report(self, monkeypatch, capsys):
        assert self._run(monkeypatch, _AlwaysClient(self._page())) == 0
        out = capsys.readouterr().out
        assert "resultList.result[0].inEPMC" in out
        assert "hasResults" in out

    def test_the_registration_verdicts_reach_the_report(self, monkeypatch, capsys):
        # The **wire**, and the one of the three that was open: replacing
        # `source_reach(record_outcomes)` in `main` with the constant `"both"`
        # passed the entire suite, so issue #204 would have been answered by a
        # table that could only ever say one thing (PR #213's review).
        # `source_reach` itself is covered above; what this drives is that
        # `main` asks it. A run whose EuropePMC probe fails cannot report
        # `both`, and its status population stays reportable because a 404 is
        # an answer.
        # Matched against the URL; the draw page is answered earlier, by its
        # `pageSize`, so only the single-record probe fails.
        client = _AlwaysClient(self._page(), fail_probe_hosts={"europepmc"})
        self._run(monkeypatch, client)
        out = capsys.readouterr().out
        table = out.split("records with a source outcome")[-1]
        assert "pubmed-only" in table, out
        assert "both" not in table

    def test_a_body_this_script_could_not_read_exits_non_zero(self, monkeypatch, capsys):
        # The `sound` term, isolated. A response this script cannot decode
        # still *records a shape*, so the endpoint counts as having served a
        # body and `shaped` stays True — which is exactly why the defect
        # needed a term of its own rather than riding on an existing one. One
        # endpoint only, so nothing else fires alongside it.
        import httpx

        class _Unreadable:
            status_code = 200
            text = '{"message": {"funder": []}}'

        class _BrokenCrossref(_AlwaysClient):
            def get(self, url, params=None, headers=None, **kwargs):
                if "crossref" in url:
                    return _Unreadable()
                return super().get(url, params, headers, **kwargs)

        client = _BrokenCrossref(self._page())
        monkeypatch.setattr(sys, "argv", ["s", "--email", "a@b.c", "--target", "9"])
        monkeypatch.setattr(sampler, "_make_pacer", lambda _interval: _pace)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _ContextClient(client))
        assert sampler.main() == 1
        captured = capsys.readouterr()
        assert "AttributeError" in captured.err
        assert "not the remote" in captured.err
        # And the other tables are untouched, which is the point of isolating it.
        assert "records categorised" in captured.out

    def test_an_endpoint_that_served_no_body_exits_non_zero(self, monkeypatch, capsys):
        # **One** endpoint, and one whose body feeds no rider table. Its
        # status population is perfectly reportable — `is_reportable` is about
        # throttling, and a 404 is an answer — the draw lost no stratum, and
        # the address, registration and results-check tables all have their
        # populations. So the exit code is carrying the shape term alone.
        #
        # Failing *every* probe instead, which is what this test did first,
        # empties the rider populations too: their terms then fire together
        # and deleting the shape term leaves the whole suite green (measured).
        # Pick the fixture that separates the guard from its own mutant.
        client = _AlwaysClient(self._page(), fail_probe_hosts={"crossref"})
        assert self._run(monkeypatch, client) == 1
        out = capsys.readouterr().out
        assert "no body was served" in out
        # The tables that are fine are fine, which is the whole point.
        assert "records categorised" in out
        assert "papers with at least one accession" in out

    @pytest.mark.parametrize(
        "predicate",
        ["addressing_reportable", "addresses_reportable", "reach_reportable", "checks_reportable"],
    )
    def test_each_rider_populations_verdict_reaches_the_exit_code(self, monkeypatch, predicate):
        # The wire, pinned directly, because no fixture separates these three
        # from the probe-level `is_reportable` above them: a run in which no
        # paper names an accession also has an empty ClinicalTrials.gov status
        # population, and one in which every record is throttled has an
        # unreportable endpoint too. They are not the same predicate —
        # `checks_reportable` and `reach_reportable` count *records* where
        # `is_reportable` counts *probes*, and a record is unmeasured if any
        # one of its accessions was — so the terms are kept, and what needed
        # pinning is that `main` consults them at all. A counter registered
        # and never read is this repository's own scar
        # (`_FORMULA_ROUTING_COUNTERS`, which printed a population nothing
        # had counted).
        assert self._run(monkeypatch, _AlwaysClient(self._page())) == 0
        monkeypatch.setattr(sampler, predicate, lambda *args: False)
        assert self._run(monkeypatch, _AlwaysClient(self._page())) == 1


class TestAStratumThatContributedNothingIsAHoleToo:
    """Found by this script's own first run with the shape tables (2026-09-08).

    Two of the nine strata answered with twenty records apiece and contributed
    **none**: every ``SRC:PMC`` record in them carried neither a DOI nor a
    PMID, so ``DrawnRecord`` refused them all. The draw line then read *"124
    records over 7 strata"*, ``failed_strata`` was empty, and the run exited
    ``0`` — reporting a source-and-year spread the sample does not have, which
    is the exact thing ``summarise_draw`` exists to prevent and the reason a
    page returning *no* records is already a hole.

    A stratum whose records are all unusable is as absent as one whose page
    did not answer. The existing guard could not see it because it tests the
    page, and the loss happens one level down.
    """

    def _draw(self, *responses):
        client = _ScriptedClient(*responses)
        return sampler.draw_records(client, 2, _pace, strata=(("MED", 2024), ("PMC", 2024)))

    def _page(self, *records):
        return _FakeResponse(200, {"resultList": {"result": list(records)}})

    def test_a_stratum_whose_records_are_all_unusable_is_named(self):
        draw = self._draw(self._page({"doi": "10.1/a"}), self._page({"title": "no identifier"}))
        assert "PMC/2024" in draw.unusable_strata
        assert "MED/2024" not in draw.unusable_strata

    def test_a_stratum_that_lost_only_some_records_is_not_a_hole(self):
        # The negative control: losing one record of two thins the stratum
        # without emptying it, which `unusable_records` already reports.
        draw = self._draw(self._page({"doi": "10.1/a"}), self._page({"doi": "10.1/b"}, {}))
        assert draw.unusable_strata == []
        assert draw.unusable_records == 1

    def test_the_report_says_the_sample_is_not_the_one_the_header_claims(self):
        draw = self._draw(self._page({"doi": "10.1/a"}), self._page({}))
        lines = sampler.summarise_draw("draw", draw, 20, 1)
        assert any("ERROR" in line and "PMC/2024" in line for line in lines)

    def test_such_a_draw_exits_non_zero(self, monkeypatch):
        import httpx

        class _PartlyUnusable(_AlwaysClient):
            def get(self, url, params=None, headers=None, **kwargs):
                if "europepmc" in url and "pageSize" in (params or {}):
                    self.draws += 1
                    if self.draws == 1:
                        return _FakeResponse(200, {"resultList": {"result": [{"title": "x"}]}})
                return super().get(url, params, headers, **kwargs)

        client = _PartlyUnusable({"resultList": {"result": [{"doi": "10.1/a", "pmid": "1"}]}})
        monkeypatch.setattr(sys, "argv", ["s", "--email", "a@b.c", "--target", "9"])
        monkeypatch.setattr(sampler, "_make_pacer", lambda _interval: _pace)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _ContextClient(client))
        assert sampler.main() == 1

    def test_a_thinned_draw_exits_non_zero_too(self, monkeypatch, capsys):
        # `unusable_records` short of emptying a stratum is the same loss,
        # only smaller — and it entered no exit-code term, so a draw could
        # fall to a fraction of its target and still go green while every
        # interval below was read against the target it never reached (PR
        # #213's review). Every stratum here contributes, so `unusable_strata`
        # and `failed_strata` are both empty and this term is carrying it
        # alone.
        import httpx

        page = {
            "resultList": {"result": [{"doi": "10.1/a", "pmid": "1"}, {"title": "no identifier"}]}
        }
        client = _AlwaysClient(page)
        monkeypatch.setattr(sys, "argv", ["s", "--email", "a@b.c", "--target", "9"])
        monkeypatch.setattr(sampler, "_make_pacer", lambda _interval: _pace)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _ContextClient(client))
        assert sampler.main() == 1
        out = capsys.readouterr().out
        assert "returned record(s) carried neither" in out
        assert "of 9 requested records" in out
