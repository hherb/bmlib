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
from collections import Counter
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
# The sampler does `from _sampling import …`, and `scripts/` is not a package.
# Running the script puts that directory on sys.path as sys.path[0]; loading it
# by path here does not, so insert it explicitly.
if str(_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_PATH.parent))
_spec.loader.exec_module(sampler)
# The shared helpers, for the one constant this file asserts against. Imported
# by name rather than by path — sys.path now carries `scripts/` — and through
# `import_module` rather than an `import` statement, which would have to sit
# below the insert above and trip E402.
helpers = importlib.import_module("_sampling")


class _Resp:
    """A minimal stand-in for an httpx response.

    Serves both the probe path, which reads raw ``content``, and the search
    and Unpaywall paths, which read ``json()``.
    """

    def __init__(
        self,
        status_code: int,
        content: bytes = b"%PDF-1.7 ...",
        headers: dict[str, str] | None = None,
        payload: object = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._payload = payload

    def json(self) -> object:
        return self._payload


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
        outcomes = [sampler.ProbeOutcome(cause=None, status=200)] * 10
        lines = sampler.summarise("europepmc", outcomes)
        assert not any("ERROR" in line for line in lines)
        assert any("0.0%" in line for line in lines)

    def test_an_empty_sample_is_an_error_not_a_perfect_score(self) -> None:
        """Sampling that returned zero URLs is not a population with no failures."""
        lines = sampler.summarise("europepmc", [])
        assert any("ERROR" in line for line in lines)


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
            [sampler.ProbeOutcome(cause=None, status=200)] * 8
            + [sampler.ProbeOutcome(cause="http-403", status=403)]
            + [sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)]
        )
        assert len(outcomes) == 10
        lines = sampler.summarise("unpaywall", outcomes)
        text = "\n".join(lines)
        assert not any("ERROR" in line for line in lines)
        assert "11.1%" in text
        assert "10.0%" not in text

    def test_the_unmeasured_count_is_reported_on_its_own_line(self) -> None:
        """The rendered count, not a substring of it.

        ``"1" in line`` passed for any count containing a 1 — 10, 11, 21 — so
        it could not tell the reported number from a wrong one.
        """
        outcomes = [sampler.ProbeOutcome(cause=None, status=200)] * 9 + [
            sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)
        ]
        lines = sampler.summarise("unpaywall", outcomes)
        assert any("1 unmeasured (429/503 after retries; excluded above)" in line for line in lines)

    def test_an_unmeasured_share_at_exactly_twenty_percent_still_reports_a_rate(self) -> None:
        """The threshold is "more than 20%" — exactly 20% must not trip ERROR."""
        outcomes = [sampler.ProbeOutcome(cause=None, status=200)] * 8 + [
            sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)
        ] * 2
        lines = sampler.summarise("biorxiv", outcomes)
        assert not any("ERROR" in line for line in lines)

    def test_an_unmeasured_share_over_twenty_percent_prints_error_and_no_rate(self) -> None:
        outcomes = [sampler.ProbeOutcome(cause=None, status=200)] * 7 + [
            sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)
        ] * 3
        lines = sampler.summarise("biorxiv", outcomes)
        assert any("ERROR" in line for line in lines)
        text = "\n".join(lines)
        assert "%" not in text

    def test_a_wholly_unmeasured_population_is_an_error_not_a_zero_measured_rate(self) -> None:
        outcomes = [sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)] * 5
        lines = sampler.summarise("biorxiv", outcomes)
        assert any("ERROR" in line for line in lines)


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
        client = _Client({sampler.EUROPE_PMC_SEARCH: _Resp(200, payload=payload)})
        sample = sampler.sample_europepmc(client, target=2, pace=lambda url: None)
        assert sample is not None
        assert sorted(sample.urls) == [
            "https://e/in.pdf?pdf=render",
            "https://e/out.pdf?pdf=render",
        ]
        assert sample.dois == ["10.1/out"]


class TestAProbeIsRangedAndAcceptsPartialContent:
    """The politeness property the module docstring claims, and its consequence.

    Probing sends ``Range``, so a compliant server answers **206**, not 200.
    Treating only 200 as success would bucket every such probe ``http-206``
    and report a ~100% failure rate for a wholly healthy population — a
    number that looks precise and is not evidence of anything. Nothing pinned
    either half before: every stubbed response in this file was a 200.
    """

    def test_a_206_partial_content_is_a_success_not_an_http_failure(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(206, b"%PDF-1.7 body")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert outcome.cause is None

    def test_a_206_that_is_not_a_pdf_is_still_a_magic_byte_rejection(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(206, b"<!DOCTYPE html>")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.cause == "not-a-pdf"

    def test_the_probe_asks_for_only_the_first_kilobyte(self) -> None:
        """Measuring 450 URLs must not mean downloading 450 whole PDFs."""
        seen: list[object] = []

        class _Recording:
            def get(self, url: str, **kwargs: object) -> _Resp:
                seen.append(kwargs.get("headers"))
                return _Resp(206, b"%PDF-1.7 body")

        sampler.probe(_Recording(), "https://e/a.pdf")
        assert seen == [{"Range": f"bytes=0-{sampler.PROBE_BYTES - 1}"}]


class TestARetryAfterIsClampedAtBothEnds:
    """An unclamped header loses the run at either end, not just the low one.

    The zero clamp was reasoned about because ``time.sleep(-1)`` raises. The
    high end is the same failure by another route: nothing prints until every
    population has finished, so an honoured ``Retry-After: 86400`` is a
    process producing no output that the operator kills, losing every
    population's data — exactly what the low clamp exists to prevent.

    The clamp itself now lives in ``scripts/_sampling.py`` and is pinned in
    ``tests/test_sampling_helpers.py``. What stays here is the half that file
    cannot cover: that ``probe`` actually *applies* it. A ``probe`` that
    stopped consulting ``_throttle_delay`` would leave every direct test of
    the delay passing.
    """

    def test_an_enormous_retry_after_is_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        slept: list[float] = []
        monkeypatch.setattr(sampler, "_sleep_for", slept.append)
        client = _SequencedClient(
            [
                _Resp(429, headers={"Retry-After": "86400"}),
                _Resp(200, b"%PDF-1.7 body"),
            ]
        )
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert slept == [helpers.MAX_RETRY_AFTER_SECONDS]


class TestASamplerThatCouldNotSampleReturnsNone:
    """The module's headline property, pinned through the code that decides it.

    ``summarise(None)`` was tested directly, but the half that decides *when*
    ``None`` is produced was untested for all three populations — so a sampler
    returning its partial results on a mid-run failure would report a
    truncated sample as a complete one, with nothing failing.
    """

    def test_a_dead_europepmc_search_is_none_not_an_empty_sample(self) -> None:
        client = _Client({sampler.EUROPE_PMC_SEARCH: _Resp(503, payload={})})
        assert sampler.sample_europepmc(client, target=5, pace=lambda url: None) is None

    def test_a_raising_europepmc_search_is_none(self) -> None:
        client = _Client({sampler.EUROPE_PMC_SEARCH: ConnectionError("no route")})
        assert sampler.sample_europepmc(client, target=5, pace=lambda url: None) is None

    def test_a_mid_paging_failure_discards_the_partial_sample(self) -> None:
        """A truncated sample reported as a complete one is a wrong rate, not a small one."""
        page = {
            "resultList": {
                "result": [
                    {
                        "doi": "10.1/a",
                        "inEPMC": "Y",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "documentStyle": "pdf",
                                    "availabilityCode": "OA",
                                    "url": "https://e/a.pdf?pdf=render",
                                }
                            ]
                        },
                    }
                ]
            },
            "nextCursorMark": "next",
        }
        client = _SequencedClient([_Resp(200, payload=page), _Resp(500, payload={})])
        assert sampler.sample_europepmc(client, target=50, pace=lambda url: None) is None

    def test_unpaywall_asked_about_nothing_is_none_not_a_clean_sheet(self) -> None:
        assert sampler.sample_unpaywall(_Client({}), [], "e@x.org", 5, lambda url: None) is None

    def test_a_biorxiv_whose_every_day_failed_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise ConnectionError("no route")

        monkeypatch.setattr(sampler, "fetch_biorxiv", _boom)
        assert sampler.sample_biorxiv(_Client({}), 5, lambda url: None) is None


class TestOnlyURLsBMLibWouldFetchEnterAPopulation:
    """``is_probeable`` is pinned in isolation; its *use* was not.

    A ``file://`` URL out of third-party JSON still reached the fetcher under
    a mutation that dropped the call, because no test drove a population with
    one in it.
    """

    def _search_payload(self, url: str) -> dict[str, object]:
        return {
            "resultList": {
                "result": [
                    {
                        "doi": "10.1/a",
                        "inEPMC": "Y",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {"documentStyle": "pdf", "availabilityCode": "OA", "url": url}
                            ]
                        },
                    }
                ]
            },
            "nextCursorMark": "",
        }

    def test_a_file_url_never_enters_the_europepmc_population(self) -> None:
        payload = self._search_payload("file:///etc/passwd")
        client = _Client({sampler.EUROPE_PMC_SEARCH: _Resp(200, payload=payload)})
        sample = sampler.sample_europepmc(client, target=5, pace=lambda url: None)
        assert sample is not None
        assert sample.urls == []

    def test_an_http_url_does_enter_it(self) -> None:
        """The negative control: the rejection above is the scheme, not the fixture."""
        payload = self._search_payload("https://e/a.pdf?pdf=render")
        client = _Client({sampler.EUROPE_PMC_SEARCH: _Resp(200, payload=payload)})
        sample = sampler.sample_europepmc(client, target=5, pace=lambda url: None)
        assert sample is not None
        assert sample.urls == ["https://e/a.pdf?pdf=render"]


class TestAThrottledUnpaywallResolutionIsUnmeasuredNotInvisible:
    """Unpaywall's rate limiter bites in resolution, not in probing.

    Before this, a resolution phase throttled away printed a confident rate
    over whatever got through first, with nothing recording that the rest of
    the population was never reached — the one place the script's own central
    rule did not hold.
    """

    def _url(self, doi: str) -> str:
        return f"{sampler.UNPAYWALL_BASE}/{doi}?email=e%40x.org"

    def test_a_persistently_throttled_doi_is_counted_unmeasured_not_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda s: None)
        client = _SequencedClient([_Resp(429)] * sampler.MAX_PROBE_ATTEMPTS)
        sample = sampler.sample_unpaywall(client, ["10.1/a"], "e@x.org", 5, lambda url: None)
        assert sample is not None
        assert sample.urls == []
        assert sample.unmeasured == 1

    def test_a_429_then_a_hit_resolves_normally_and_counts_as_measured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda s: None)
        client = _SequencedClient(
            [
                _Resp(429),
                _Resp(200, payload={"best_oa_location": {"url_for_pdf": "https://r/a.pdf"}}),
            ]
        )
        sample = sampler.sample_unpaywall(client, ["10.1/a"], "e@x.org", 5, lambda url: None)
        assert sample is not None
        assert sample.urls == ["https://r/a.pdf"]
        assert sample.unmeasured == 0

    def test_a_404_is_an_answer_not_an_unmeasured_attempt(self) -> None:
        """Unpaywall having no record *is* the measurement; it is not throttling."""
        client = _Client({self._url("10.1%2Fa"): _Resp(404, payload={})})
        sample = sampler.sample_unpaywall(client, ["10.1/a"], "e@x.org", 5, lambda url: None)
        assert sample is not None
        assert sample.unmeasured == 0

    def test_throttled_resolutions_reach_the_population_and_trip_the_error_rule(self) -> None:
        """The whole point: they must reach ``summarise`` and be able to trip ERROR."""
        sample = sampler.UnpaywallSample(urls=[], unmeasured=4)
        outcomes = sampler._unpaywall_population(_Client({}), sample, lambda url: None)
        assert outcomes is not None
        assert len(outcomes) == 4
        assert not sampler.is_reportable(outcomes)
        assert any("ERROR" in line for line in sampler.summarise("unpaywall", outcomes))


class TestTheAvailabilityDistributionIsMeasuredNotAssumed:
    """#79's allow-list must be answerable to the records, like the databank lists.

    The script was cited in four documents as the evidence for
    ``_FREE_PDF_AVAILABILITY_CODES`` while reading neither access field. A
    maintainer told to run it before changing the allow-list got a
    failure-rate table and no evidence either way.
    """

    def _hit(self, *entries: dict[str, object]) -> dict[str, object]:
        return {"fullTextUrlList": {"fullTextUrl": list(entries)}}

    def test_pdf_entries_are_counted_by_label_and_code(self) -> None:
        counts = sampler.count_pdf_availability(
            self._hit(
                {"documentStyle": "pdf", "availability": "Open access", "availabilityCode": "OA"},
                {"documentStyle": "pdf", "availability": "Open access", "availabilityCode": "OA"},
                {"documentStyle": "pdf", "availability": "Free", "availabilityCode": "F"},
            )
        )
        assert counts == {("Open access", "OA"): 2, ("Free", "F"): 1}

    def test_the_distribution_counts_entries_bmlib_rejects(self) -> None:
        """Counted before the allow-list, or it could only ever confirm itself."""
        counts = sampler.count_pdf_availability(
            self._hit(
                {
                    "documentStyle": "pdf",
                    "availability": "Subscription required",
                    "availabilityCode": "S",
                }
            )
        )
        assert counts == {("Subscription required", "S"): 1}

    def test_non_pdf_entries_are_not_counted(self) -> None:
        counts = sampler.count_pdf_availability(
            self._hit({"documentStyle": "html", "availability": "Open access"})
        )
        assert counts == {}

    def test_a_malformed_payload_yields_no_counts_rather_than_raising(self) -> None:
        assert sampler.count_pdf_availability({"fullTextUrlList": None}) == {}
        assert sampler.count_pdf_availability({"fullTextUrlList": {"fullTextUrl": None}}) == {}
        assert sampler.count_pdf_availability({}) == {}

    def test_absent_and_malformed_fields_are_distinguished(self) -> None:
        """`_entry_is_free` reads a non-string code as no code; the table must too."""
        counts = sampler.count_pdf_availability(
            self._hit(
                {"documentStyle": "pdf", "availability": "Free"},
                {"documentStyle": "pdf", "availabilityCode": {"v": "OA"}},
            )
        )
        assert counts == {("Free", "-"): 1, ("-", "?"): 1}

    def test_the_report_marks_which_values_bmlib_takes(self) -> None:
        counts: Counter[tuple[str, str]] = Counter(
            {("Open access", "OA"): 90, ("Subscription required", "S"): 10}
        )
        text = "\n".join(sampler.summarise_availability(counts))
        assert "Open access" in text and "taken" in text
        assert "SKIPPED" in text
        assert "90.0%" in text

    def test_a_value_bmlib_has_never_evaluated_reads_as_skipped(self) -> None:
        """#79 in miniature: a new code must be visible as unclaimed, not silent."""
        counts: Counter[tuple[str, str]] = Counter({("Some new label", "X"): 5})
        line = "\n".join(sampler.summarise_availability(counts))
        assert "SKIPPED" in line

    def test_no_entries_seen_is_an_error_not_an_empty_table(self) -> None:
        assert "ERROR" in sampler.summarise_availability(Counter())[0]


class TestTheExitStatusAgreesWithWhatWasPrinted:
    """A caller cannot otherwise tell "here are the rates" from "nothing sampled"."""

    def test_a_healthy_population_is_reportable(self) -> None:
        assert sampler.is_reportable([sampler.ProbeOutcome(cause=None, status=200)] * 5)

    def test_an_unsampled_population_is_not(self) -> None:
        assert not sampler.is_reportable(None)
        assert not sampler.is_reportable([])

    def test_a_mostly_throttled_population_is_not(self) -> None:
        outcomes = [sampler.ProbeOutcome(cause=None, status=200)] * 7 + [
            sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)
        ] * 3
        assert not sampler.is_reportable(outcomes)

    def test_the_predicate_agrees_with_the_printed_report(self) -> None:
        """One predicate behind both, so the exit code cannot drift from the table."""
        for outcomes in (
            None,
            [],
            [sampler.ProbeOutcome(cause=None, status=200)] * 5,
            [sampler.ProbeOutcome(cause="unmeasured-429", status=429, measured=False)] * 5,
        ):
            printed_error = any("ERROR" in line for line in sampler.summarise("p", outcomes))
            assert printed_error is not sampler.is_reportable(outcomes)


class TestTheBiorxivPopulationHonoursTheTarget:
    """``--target`` bounded two populations and not the third.

    bioRxiv posts 100+ preprints a day and the length is only re-checked
    between days, so an untruncated return probed several hundred URLs against
    one third-party host — spending the run's budget on no more evidence.
    """

    def test_the_sample_is_truncated_to_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Entry:
            def __init__(self, url: str) -> None:
                self.format = "pdf"
                self.url = url

        class _Record:
            def __init__(self, url: str) -> None:
                self.fulltext_sources = [_Entry(url)]

        def _fetch(client: object, day: object, on_record: Any, **kwargs: object) -> None:
            for i in range(50):
                on_record(_Record(f"https://b/{i}.pdf"))

        monkeypatch.setattr(sampler, "fetch_biorxiv", _fetch)
        urls = sampler.sample_biorxiv(_Client({}), 5, lambda url: None)
        assert urls is not None
        assert len(urls) == 5


class TestTheParserDefaultsAreTheDocumentedOnes:
    """``_build_arg_parser``'s docstring says tests inspect its defaults."""

    def test_defaults_match_the_module_constants(self) -> None:
        args = sampler._build_arg_parser().parse_args(["--email", "e@x.org"])
        assert args.target == sampler.DEFAULT_TARGET
        assert args.per_host_interval == sampler.PER_HOST_INTERVAL_SECONDS

    def test_email_is_required(self) -> None:
        with pytest.raises(SystemExit):
            sampler._build_arg_parser().parse_args([])
