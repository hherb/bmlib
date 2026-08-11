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

"""Tests for ``scripts/sample_free_pdf_urls.py``.

The script is a live runner — it probes Europe PMC, Unpaywall and publisher
hosts — but the rates it prints are the evidence for issue #68's log level and
issue #79's allow-list, so the table has to be trustworthy offline. What is
pinned here is the property that makes it so: **a population that could not be
sampled prints ERROR, never a zero.** A 0% failure rate is what a perfectly
healthy population looks like, and a dead Europe PMC must not be readable as
one. Run 1 showed a second way a population can lie: a self-inflicted 429 from
under-paced probing landed in the failure bucket and inflated the very rate
the table exists to report. What is pinned below alongside the original
property is that a 429/503 is retried, and — if still throttled after
retries — excluded from the rate entirely rather than counted as a failure,
with the same "print ERROR, not a misleading number" rule applied when
throttling ate too much of a population to trust what got through.

Note the asymmetry, which is the whole design: an individual *probe* that
raises is a real finding — it is one of the three causes bmlib's
``_download_and_cache_pdf`` swallows — and is counted. It is the *sampling*
step, and now also an unresolved 429/503, that must report ERROR or be
excluded rather than counted as a finding.

No network, no real sleeping: every test drives the script through a stubbed
client, and any test that exercises a retry path stubs ``_sleep_for`` so no
test actually waits.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sample_free_pdf_urls.py"
_spec = importlib.util.spec_from_file_location("bmlib_free_pdf_sampler", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the sampler from {_PATH}")
sampler = importlib.util.module_from_spec(_spec)
# Registered in sys.modules before exec: the script's ProbeOutcome is a
# dataclass, and with `from __future__ import annotations` its field types are
# strings that dataclasses resolves via `sys.modules[cls.__module__]` — a
# module never inserted there raises AttributeError on a None lookup. The
# databank sampler's loader (tests/test_databank_sampler.py) does not need
# this line because that script defines no dataclass.
sys.modules[_spec.name] = sampler
_spec.loader.exec_module(sampler)


class _Resp:
    """A minimal stand-in for an httpx response."""

    def __init__(
        self,
        status_code: int,
        content: bytes = b"%PDF-1.7 ...",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class _Client:
    """Answers each URL from a script, or raises what the script holds."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.seen: list[str] = []

    def get(self, url: str, **kwargs: object) -> _Resp:
        self.seen.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


class _SequencedClient:
    """Answers successive ``get`` calls from a fixed list, in order.

    Used for retry tests, where the same URL must answer differently on
    successive attempts (e.g. 429, then 200) — ``_Client`` can only hold one
    fixed answer per URL.
    """

    def __init__(self, answers: list[object]) -> None:
        self._answers = list(answers)
        self.seen: list[str] = []

    def get(self, url: str, **kwargs: object) -> _Resp:
        self.seen.append(url)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestTheProbeSortsTheThreeCauses:
    """bmlib swallows three distinct outcomes; the measurement must keep them apart.

    Merging them would answer #68's level question with a number that cannot
    distinguish a full disk from a publisher 404 — which is the defect, not
    the measurement.
    """

    def test_a_pdf_is_a_success(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(200, b"%PDF-1.7 body")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert outcome.cause is None
        assert outcome.measured is True

    def test_a_non_200_is_reported_with_its_status(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(404)})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "http-404"
        assert outcome.status == 404
        assert outcome.measured is True

    def test_a_landing_page_is_a_magic_byte_rejection_not_an_http_failure(self) -> None:
        """The Unpaywall failure mode: HTTP 200, and the bytes are HTML."""
        client = _Client({"https://e/a.pdf": _Resp(200, b"<!DOCTYPE html><html>")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "not-a-pdf"

    def test_an_exception_is_reported_by_type(self) -> None:
        client = _Client({"https://e/a.pdf": TimeoutError("timed out")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "exception-TimeoutError"

    def test_a_non_http_url_is_not_probed_at_all(self) -> None:
        """The URLs come from third-party JSON.

        A ``file://`` or ``ftp://`` URL is not a download failure — counting it
        as one would put a scheme bmlib never fetches into the rate that sets a
        log level — and it must never be handed to a fetcher.
        """
        assert sampler.is_probeable("https://e/a.pdf") is True
        assert sampler.is_probeable("http://e/a.pdf") is True
        assert sampler.is_probeable("file:///etc/passwd") is False
        assert sampler.is_probeable("ftp://e/a.pdf") is False


class TestAFailedSampleNeverPrintsAsAFinding:
    """The property that makes the table trustworthy offline."""

    def test_an_unsampled_population_prints_error_not_a_zero_rate(self) -> None:
        lines = sampler.summarise("europepmc", None)
        assert any("ERROR" in line for line in lines)
        assert not any("0.0%" in line for line in lines)

    def test_a_sampled_population_with_no_failures_prints_a_rate(self) -> None:
        """The control: a genuine 0% must still be reportable as 0%."""
        outcomes = [sampler.ProbeOutcome(ok=True, cause=None, status=200)] * 10
        lines = sampler.summarise("europepmc", outcomes)
        assert not any("ERROR" in line for line in lines)
        assert any("0.0%" in line for line in lines)

    def test_an_empty_sample_is_an_error_not_a_perfect_score(self) -> None:
        """Sampling that returned zero URLs is not a population with no failures."""
        lines = sampler.summarise("europepmc", [])
        assert any("ERROR" in line for line in lines)


class TestTheIntervalIsComputedOverAttemptsActuallyMade:
    """A threshold rule (#68's 5%) needs an interval, not a point estimate."""

    def test_known_wilson_values(self) -> None:
        lo, hi = sampler.wilson(0, 300)
        assert lo == pytest.approx(0.0, abs=1e-9)
        assert hi == pytest.approx(0.012643, abs=1e-5)

        lo, hi = sampler.wilson(15, 300)
        assert lo == pytest.approx(0.030531, abs=1e-5)
        assert hi == pytest.approx(0.080848, abs=1e-5)

    def test_the_interval_straddles_the_decision_threshold_at_five_percent(self) -> None:
        """Why the spec asks for an interval: 15/300 is exactly 5%, and the
        sample does not actually settle which side of the rule it falls on."""
        lo, hi = sampler.wilson(15, 300)
        assert lo < 0.05 < hi

    def test_no_attempts_is_an_error_not_an_interval(self) -> None:
        with pytest.raises(ValueError):
            sampler.wilson(0, 0)


class TestAThrottledProbeIsRetriedNotImmediatelyGivenUp:
    """Run 1's actual defect: a 429 is the sampler throttling itself, not a finding.

    ``probe`` must not report a 429/503 on the first try — it must retry, up
    to three attempts total, before concluding the probe could not be made.
    """

    def test_a_429_that_persists_across_every_retry_is_unmeasured_not_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _SequencedClient([_Resp(429), _Resp(429), _Resp(429)])
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.measured is False
        assert outcome.ok is False
        assert outcome.cause == "unmeasured-429"
        # Three attempts total, not one and not unbounded.
        assert len(client.seen) == 3

    def test_a_503_that_persists_across_every_retry_is_unmeasured_not_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _SequencedClient([_Resp(503), _Resp(503), _Resp(503)])
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.measured is False
        assert outcome.cause == "unmeasured-503"

    def test_a_429_followed_by_a_200_on_retry_is_a_normal_measured_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _SequencedClient([_Resp(429), _Resp(200, b"%PDF-1.7 body")])
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert outcome.measured is True
        assert outcome.cause is None
        assert len(client.seen) == 2

    def test_retry_after_header_is_honoured_as_the_sleep_duration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[Any] = []
        monkeypatch.setattr(sampler, "_sleep_for", recorded.append)
        client = _SequencedClient(
            [_Resp(429, headers={"Retry-After": "5"}), _Resp(200, b"%PDF-1.7 body")]
        )
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert recorded == [5]

    def test_an_http_date_retry_after_falls_back_to_exponential_backoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An HTTP-date Retry-After is not the integer form this script parses."""
        recorded: list[Any] = []
        monkeypatch.setattr(sampler, "_sleep_for", recorded.append)
        client = _SequencedClient(
            [
                _Resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                _Resp(200, b"%PDF-1.7 body"),
            ]
        )
        sampler.probe(client, "https://e/a.pdf")
        assert recorded == [2.0]

    def test_no_retry_after_header_backs_off_exponentially(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[Any] = []
        monkeypatch.setattr(sampler, "_sleep_for", recorded.append)
        client = _SequencedClient([_Resp(429), _Resp(429), _Resp(429)])
        sampler.probe(client, "https://e/a.pdf")
        # 2s before the 2nd attempt, 4s before the 3rd; no sleep after the
        # last attempt, since there is nothing left to retry.
        assert recorded == [2.0, 4.0]

    def test_a_zero_retry_after_is_honoured_as_zero_not_treated_as_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Retry-After: 0`` is a value, not a missing header.

        The code tests ``retry_after is not None`` rather than truthiness
        precisely so a server saying "retry immediately" is not silently
        promoted to a 2-second backoff. Nothing pinned that, so a
        simplification to ``retry_after or fallback`` would have passed.
        """
        recorded: list[Any] = []
        monkeypatch.setattr(sampler, "_sleep_for", recorded.append)
        client = _SequencedClient(
            [_Resp(429, headers={"Retry-After": "0"}), _Resp(200, b"%PDF-1.7 body")]
        )
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert recorded == [0]
        assert all(seconds >= 0 for seconds in recorded)

    def test_a_negative_retry_after_does_not_abort_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hostile or broken header must not cost 25 minutes of live probing.

        ``int("-1")`` parses fine and ``time.sleep(-1)`` raises ``ValueError``
        — from a line that sits *outside* the ``try`` wrapping ``client.get``,
        so it propagates out of ``probe()`` through ``run()`` to ``main()``
        and loses every population's data. The delay is clamped at zero.
        """
        recorded: list[Any] = []
        monkeypatch.setattr(sampler, "_sleep_for", recorded.append)
        client = _SequencedClient(
            [_Resp(429, headers={"Retry-After": "-1"}), _Resp(200, b"%PDF-1.7 body")]
        )
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert outcome.measured is True
        assert all(seconds >= 0 for seconds in recorded)


class TestThrottledProbesAreExcludedFromTheRateNotCountedAsFailures:
    """``summarise`` must not let a self-inflicted 429 inflate the failure rate."""

    def test_a_small_unmeasured_share_still_reports_a_rate_over_measured_only(self) -> None:
        """10 probes, 1 unmeasured, 1 failed: the rate is 1/9, not 1/10."""
        outcomes = (
            [sampler.ProbeOutcome(ok=True, cause=None, status=200)] * 8
            + [sampler.ProbeOutcome(ok=False, cause="http-403", status=403)]
            + [sampler.ProbeOutcome(ok=False, cause="unmeasured-429", status=429, measured=False)]
        )
        assert len(outcomes) == 10
        lines = sampler.summarise("unpaywall", outcomes)
        text = "\n".join(lines)
        assert not any("ERROR" in line for line in lines)
        assert "11.1%" in text
        assert "10.0%" not in text

    def test_the_unmeasured_count_is_reported_on_its_own_line(self) -> None:
        outcomes = [sampler.ProbeOutcome(ok=True, cause=None, status=200)] * 9 + [
            sampler.ProbeOutcome(ok=False, cause="unmeasured-429", status=429, measured=False)
        ]
        lines = sampler.summarise("unpaywall", outcomes)
        assert any("1" in line and "unmeasured" in line for line in lines)

    def test_an_unmeasured_share_at_exactly_twenty_percent_still_reports_a_rate(self) -> None:
        """The threshold is "more than 20%" — exactly 20% must not trip ERROR."""
        outcomes = [sampler.ProbeOutcome(ok=True, cause=None, status=200)] * 8 + [
            sampler.ProbeOutcome(ok=False, cause="unmeasured-429", status=429, measured=False)
        ] * 2
        lines = sampler.summarise("biorxiv", outcomes)
        assert not any("ERROR" in line for line in lines)

    def test_an_unmeasured_share_over_twenty_percent_prints_error_and_no_rate(self) -> None:
        outcomes = [sampler.ProbeOutcome(ok=True, cause=None, status=200)] * 7 + [
            sampler.ProbeOutcome(ok=False, cause="unmeasured-429", status=429, measured=False)
        ] * 3
        lines = sampler.summarise("biorxiv", outcomes)
        assert any("ERROR" in line for line in lines)
        text = "\n".join(lines)
        assert "%" not in text

    def test_a_wholly_unmeasured_population_is_an_error_not_a_zero_measured_rate(self) -> None:
        outcomes = [
            sampler.ProbeOutcome(ok=False, cause="unmeasured-429", status=429, measured=False)
        ] * 5
        lines = sampler.summarise("biorxiv", outcomes)
        assert any("ERROR" in line for line in lines)


class TestThePacerSpacesRequestsPerHostNotGlobally:
    """Run 1 paced every request on one global clock; one host's throttling
    then throttled every other host's pacing too, for no reason — the fix
    tracks the last request time per host."""

    def test_a_hosts_first_request_does_not_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", sleeps.append)
        clock = iter([100.0])
        pace = sampler._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        assert sleeps == []

    def test_a_second_request_to_the_same_host_waits_out_the_remainder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", sleeps.append)
        clock = iter([100.0, 101.0])
        pace = sampler._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        pace("https://a.example/y")
        assert sleeps == [2.0]

    def test_a_request_already_past_the_interval_does_not_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", sleeps.append)
        clock = iter([100.0, 104.0])
        pace = sampler._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        pace("https://a.example/y")
        assert sleeps == []

    def test_a_different_host_does_not_wait_for_an_unrelated_hosts_pacing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", sleeps.append)
        clock = iter([100.0, 100.1])
        pace = sampler._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        pace("https://b.example/y")
        assert sleeps == []


class TestTheEuropePMCPopulationIsNoLongerSplitByInEPMC:
    """The 'out' half of the split was structurally empty (run 1's other finding).

    ``?pdf=render`` embeds a PMC ID, so a record carrying one is in Europe PMC
    by construction — the split could never populate its own "out" half.
    """

    def test_sample_europepmc_returns_urls_and_dois_not_a_three_way_split(self) -> None:
        payload = {
            "resultList": {
                "result": [
                    {
                        "doi": "10.1/in",
                        "inEPMC": "Y",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "documentStyle": "pdf",
                                    "availabilityCode": "OA",
                                    "url": "https://e/in.pdf?pdf=render",
                                }
                            ]
                        },
                    },
                    {
                        "doi": "10.1/out",
                        "inEPMC": "N",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "documentStyle": "pdf",
                                    "availabilityCode": "OA",
                                    "url": "https://e/out.pdf?pdf=render",
                                }
                            ]
                        },
                    },
                ]
            },
            "nextCursorMark": "",
        }
        # A response stand-in exposing both .status_code and .json(), which
        # _Resp does not (it is shaped for the probe path, not the search
        # path — the search reads JSON, the probe reads raw bytes).
        resp = type(
            "_SearchResp",
            (),
            {"status_code": 200, "json": lambda self, payload=payload: payload},
        )()
        client = _Client({sampler.EUROPE_PMC_SEARCH: resp})
        urls, dois = sampler.sample_europepmc(client, target=2, pace=lambda url: None)
        assert sorted(urls) == ["https://e/in.pdf?pdf=render", "https://e/out.pdf?pdf=render"]
        assert dois == ["10.1/out"]
