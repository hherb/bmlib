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

import importlib.util
import sys
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
        return sampler.ProbeOutcome(endpoint="crossref", status=200, cause=None)
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
        lines = sampler.summarise_draw("draw", draw)
        assert any("ERROR" in line and "MED/2024" in line for line in lines)

    def test_a_draw_that_got_nothing_is_an_error_not_a_table(self):
        [line] = sampler.summarise_draw("draw", sampler.Draw())
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
        outcomes = sampler.probe_record(
            client, TransparencyAnalyzer(email="a@b.c"), record, "a@b.c", _pace
        )
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
        outcomes = sampler.probe_trials(
            client, TransparencyAnalyzer(email="a@b.c"), record, "a@b.c", _pace
        )
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
        outcomes = sampler.probe_trials(
            client, TransparencyAnalyzer(email="a@b.c"), record, "a@b.c", _pace
        )
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
        assert any("3 returned record(s)" in line for line in sampler.summarise_draw("draw", draw))


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
        sampler.probe_trials(
            client, TransparencyAnalyzer(email="a@b.c"), self._record(), "a@b.c", _pace, failures
        )
        assert failures == ["efetch 1: HTTP 503"]
        assert "HTTP 503" in capsys.readouterr().err

    def test_a_raised_efetch_names_the_type(self, capsys):
        # `str(OSError("reset"))` does not contain "OSError" — the rule the
        # analyzer's own handler argues, and which `draw_records` beside this
        # already followed.
        failures: list[str] = []
        client = _ScriptedClient(OSError("reset"), _FakeResponse(200, {"hasResults": True}))
        sampler.probe_trials(
            client, TransparencyAnalyzer(email="a@b.c"), self._record(), "a@b.c", _pace, failures
        )
        assert failures == ["efetch 1: OSError"]
        assert "OSError" in capsys.readouterr().err

    def test_a_clean_efetch_records_nothing(self):
        failures: list[str] = []
        client = _ScriptedClient(
            _FakeResponse(200, text="<PubmedArticleSet/>"), _FakeResponse(200, {"hasResults": True})
        )
        sampler.probe_trials(
            client, TransparencyAnalyzer(email="a@b.c"), self._record(), "a@b.c", _pace, failures
        )
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
        return {"resultList": {"result": [{"doi": f"10.1/{i}", "pmid": str(i)} for i in range(n)]}}

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
    ):
        self.page = page
        self.probe_status = probe_status
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
    """

    def _analyzer_client(self, doi: str, pmid: str) -> _ScriptedClient:
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        analyzer = TransparencyAnalyzer(email="a@b.c")
        analyzer._query_crossref(client, doi)
        analyzer._query_europepmc(client, f'DOI:"{doi}"')
        analyzer._query_pubmed(client, pmid)
        analyzer._query_openalex(client, doi)
        analyzer._check_trial_results(client, "NCT00000001")
        return client

    def _analyzer_urls(self, doi: str, pmid: str) -> set[str]:
        return set(self._analyzer_client(doi, pmid).urls())

    def _sampler_client(self, doi: str, pmid: str) -> _ScriptedClient:
        record = sampler.DrawnRecord(source="MED", year=2024, doi=doi, pmid=pmid, raw={})
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        sampler.probe_record(client, TransparencyAnalyzer(email="a@b.c"), record, "a@b.c", _pace)
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
            TransparencyAnalyzer(email="a@b.c"),
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
        record = sampler.DrawnRecord(source="MED", year=2024, doi=doi, pmid=pmid, raw={})
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        sampler.probe_record(client, TransparencyAnalyzer(email="a@b.c"), record, "a@b.c", _pace)
        trial_client = _ScriptedClient(
            _FakeResponse(200, text="<PubmedArticleSet/>"), _FakeResponse(200, {})
        )
        sampler.probe_trials(
            trial_client,
            TransparencyAnalyzer(email="a@b.c"),
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
        # The other direction, and the one that goes stale silently: a sixth
        # dropped response added to the module and not to `ENDPOINTS` would
        # simply never be measured, and the level for it would be chosen the
        # way all five were before this script existed.
        doi, pmid = "10.1/x", "1"
        record = sampler.DrawnRecord(source="MED", year=2024, doi=doi, pmid=pmid, raw={})
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        sampler.probe_record(client, TransparencyAnalyzer(email="a@b.c"), record, "a@b.c", _pace)
        probed = set(client.urls()) | {
            sampler.CLINICALTRIALS_STUDY_URL.format(nct_id="NCT00000001")
        }
        assert probed == self._analyzer_urls(doi, pmid)

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
