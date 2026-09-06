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
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        if not self._responses:
            return _FakeResponse(200, {})
        answer = self._responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Never actually wait. The retry schedule is `_sampling`'s to test."""
    monkeypatch.setattr(sampler, "_sleep_for", lambda _seconds: None)


def _pace(_url: str) -> None:
    """A pacer that does nothing, so tests do not sit through the interval."""


def _outcome(cause: str | None, *, measured: bool = True) -> sampler.ProbeOutcome:
    """One outcome for the summary tests, whose endpoint and status are irrelevant."""
    return sampler.ProbeOutcome(endpoint="crossref", status=None, cause=cause, measured=measured)


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
        assert "non-200" not in line

    def test_a_mostly_throttled_population_is_an_error(self):
        # Half unmeasured, well past the threshold. The probes that got
        # through are the *early* ones, so what survived is not a random
        # sample of the population.
        outcomes = [_outcome(None) for _ in range(5)] + [
            _outcome("unmeasured-429", measured=False) for _ in range(5)
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
            _outcome("unmeasured-503", measured=False),
        ]
        lines = sampler.summarise("crossref", outcomes)
        assert "9 probed" in lines[0]
        assert "1 non-200" in lines[0]
        assert any("1 unmeasured" in line for line in lines)

    def test_is_reportable_agrees_with_what_was_printed(self):
        # One predicate behind the ERROR branches and the exit code, so a
        # caller chaining this script cannot be told something the tables
        # do not say.
        throttled = [_outcome("unmeasured-429", measured=False)]
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

    def _analyzer_urls(self, doi: str, pmid: str) -> set[str]:
        client = _ScriptedClient(*[_FakeResponse(200, {}) for _ in range(4)])
        analyzer = TransparencyAnalyzer(email="a@b.c")
        analyzer._query_crossref(client, doi)
        analyzer._query_europepmc(client, f'DOI:"{doi}"')
        analyzer._query_pubmed(client, pmid)
        analyzer._query_openalex(client, doi)
        analyzer._check_trial_results(client, "NCT00000001")
        return set(client.urls())

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
        _url, sent = client.calls[0]
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
