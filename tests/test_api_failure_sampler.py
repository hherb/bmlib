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
        outcomes = sampler.probe_record(client, record, "a@b.c", _pace)
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
        sampler.probe_record(client, record, "a@b.c", _pace)
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
        record = sampler.DrawnRecord(source="MED", year=2024, doi=doi, pmid=pmid, raw={})
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        sampler.probe_record(client, record, "a@b.c", _pace)
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
        # The other direction, and the one that goes stale silently: a sixth
        # dropped response added to the module and not to `ENDPOINTS` would
        # simply never be measured, and the level for it would be chosen the
        # way all five were before this script existed.
        doi, pmid = "10.1/x", "1"
        record = sampler.DrawnRecord(source="MED", year=2024, doi=doi, pmid=pmid, raw={})
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        sampler.probe_record(client, record, "a@b.c", _pace)
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

    def test_a_shape_from_another_endpoint_is_refused(self):
        with pytest.raises(ValueError, match="shape for 'openalex'"):
            sampler.ProbeOutcome(
                endpoint="crossref",
                status=200,
                cause=None,
                shape=sampler.BodyShape(endpoint="openalex", top="object"),
            )


class TestThePubMedBodyIsShapedToo:
    """``efetch`` serves XML, so its shape question is whether it parses.

    Not a JSON row, and it earns its place for the same reason the JSON ones
    do: ``_check_pubmed`` WARNs on an empty 200 and ``_parse_pubmed_signals``
    WARNs on a body that is not parsable XML, and **neither level was ever
    measured** — issue #193's draw settled statuses. Both branches end with
    empty signals, which means no ``<CoiStatement>``, nothing retracted from
    the COI indicators, and the missing-COI downgrade free to fire.
    """

    def test_a_parsable_document_is_xml(self):
        served = _FakeResponse(200, text="<PubmedArticleSet><x/></PubmedArticleSet>")
        assert sampler.observe_body("pubmed_efetch", served).top == "xml"

    def test_an_empty_body_is_its_own_answer(self):
        assert sampler.observe_body("pubmed_efetch", _FakeResponse(200, text="")).top == "empty"

    def test_a_body_that_will_not_parse_is_not_xml(self):
        served = _FakeResponse(200, text="<PubmedArticleSet>")
        assert sampler.observe_body("pubmed_efetch", served).top == "not-xml"

    def test_the_xml_endpoint_has_no_field_rows(self):
        served = _FakeResponse(200, text="<PubmedArticleSet/>")
        assert sampler.observe_body("pubmed_efetch", served).fields == ()


def _epmc_body(**record: object) -> dict:
    """One EuropePMC search body carrying one record."""
    return {"resultList": {"result": [record]}}


class TestAnEuropePMCRecordSaysHowBmlibWouldAddressIt:
    """The rider populations: issues #207 and #188, on the body already fetched.

    ``FullTextStatus.NOT_ATTEMPTED`` is documented *"no request was made, and
    EuropePMC's own answer is why"* and covers three causes, one of which is a
    record claiming ``inEPMC: Y`` and carrying nothing to address the text by
    — a malformed record, not a closed-access paper, and the one cause for
    which that sentence is false. #207 asks for a fourth member and was filed
    rather than taken because its population is unmeasured. #188 is the
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
            (_epmc_body(inEPMC="Y", id="PPR123"), "id-only"),
            (_epmc_body(inEPMC="Y", pmcid="", id="PPR123"), "id-only"),
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
        assert shape.address_source == "PPR"

    def test_a_body_that_did_not_decode_is_not_categorised(self):
        shape = sampler.observe_body("europepmc_search", _FakeResponse(200, text="<html>"))
        assert shape.addressability is None

    def test_no_other_endpoint_carries_a_category(self):
        assert sampler.observe_body("crossref", _FakeResponse(200, {})).addressability is None

    def test_a_category_on_another_endpoint_is_refused(self):
        with pytest.raises(ValueError, match="only a EuropePMC record"):
            sampler.BodyShape(endpoint="crossref", top="object", addressability="pmcid")


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
        assert sampler.ADDRESSED_CATEGORIES == frozenset({"pmcid", "id-only"})


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

    Two silences, one per half. ``MAX_TRIAL_IDS_TO_CHECK`` slices an
    **unbounded** list — ``_parse_pubmed_signals`` extends over every
    ``<AccessionNumberList>`` entry — so a pooled report's fourth accession is
    dropped with no log line, no indicator and no test. And ``answered`` goes
    ``True`` on the *first* accession that replies, so one reachable *"no
    results"* outvotes any number of unreachable ones and the paper stores
    *"Registered trial without posted results"*: issue #194's class of false
    claim, narrowed by the tri-state rather than removed.

    Neither can be decided without knowing how often a paper carries more than
    three accessions, and how often a check is partly answered. Both are
    counted here.
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
                shape=sampler.BodyShape(endpoint=endpoint, top="object") if ok else None,
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
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="X", source="PPR")),
        ]
        text = "\n".join(sampler.summarise_addressing(outcomes))
        assert "pmcid" in text and "id-only" in text

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

    def test_the_id_only_records_are_split_by_source(self):
        # Issue #188 turns on exactly this: a `PPR` accession is the only
        # address a preprint has, and a `MED` record's bare id never serves.
        outcomes = [
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="X", source="PPR")),
            _served("europepmc_search", _epmc_body(inEPMC="Y", id="9", source="MED")),
        ]
        text = "\n".join(sampler.summarise_addressing(outcomes))
        assert "PPR" in text and "MED" in text

    def test_no_trial_check_is_an_error(self):
        assert any("ERROR" in line for line in sampler.summarise_trial_checks([]))

    def test_a_wholly_unmeasured_check_population_is_an_error(self):
        checks = [sampler.TrialCheck(found=1, probed=1, answered=0, unmeasured=1)]
        assert any("ERROR" in line for line in sampler.summarise_trial_checks(checks))

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
        return {"resultList": {"result": [{"doi": f"10.1/{i}", "pmid": str(i)} for i in range(n)]}}

    def test_a_clean_run_prints_every_new_table(self, monkeypatch, capsys):
        assert self._run(monkeypatch, _AlwaysClient(self._page())) == 0
        out = capsys.readouterr().out
        assert "bodies served" in out
        assert "records categorised" in out
        assert "papers with at least one accession" in out
        assert "records with a source outcome" in out

    def test_the_field_rows_reach_the_report(self, monkeypatch, capsys):
        assert self._run(monkeypatch, _AlwaysClient(self._page())) == 0
        out = capsys.readouterr().out
        assert "resultList.result[0].inEPMC" in out
        assert "hasResults" in out

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
        "predicate", ["addressing_reportable", "reach_reportable", "checks_reportable"]
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
        lines = sampler.summarise_draw("draw", draw)
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
