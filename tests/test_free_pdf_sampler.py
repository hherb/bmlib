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
one.

Note the asymmetry, which is the whole design: an individual *probe* that
raises is a real finding — it is one of the three causes bmlib's
``_download_and_cache_pdf`` swallows — and is counted. It is the *sampling*
step that must report ERROR when it fails.

No network: every test drives the script through a stubbed client.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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

    def __init__(self, status_code: int, content: bytes = b"%PDF-1.7 ...") -> None:
        self.status_code = status_code
        self.content = content


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

    def test_a_non_200_is_reported_with_its_status(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(404)})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "http-404"
        assert outcome.status == 404

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
