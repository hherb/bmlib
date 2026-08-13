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

"""Tests for ``scripts/_sampling.py``.

The pacing and throttling rules covered here were learned from a live run
that measured its own throttling instead of the population it was aiming at —
``sample_free_pdf_urls.py``'s first run sampled one host 300 times in 300
seconds and reported HTTP 429 as its dominant finding. They moved out of
``tests/test_free_pdf_sampler.py`` together with the code they pin, so that a
second sampler inherits both the helpers and their tests rather than a copy
of each.

No real sleeping: every test that reaches a wait stubs ``_sleep_for`` or
drives an injected clock.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "_sampling.py"
_spec = importlib.util.spec_from_file_location("bmlib_sampling_helpers", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the module is in-tree
    raise ImportError(f"cannot load the sampling helpers from {_PATH}")
helpers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = helpers
_spec.loader.exec_module(helpers)


class _Resp:
    """A minimal stand-in for a throttled response, carrying headers only."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class TestTheIntervalIsComputedOverAttemptsActuallyMade:
    """A threshold rule (#68's 5%) needs an interval, not a point estimate."""

    def test_known_wilson_values(self) -> None:
        lo, hi = helpers.wilson(0, 300)
        assert lo == pytest.approx(0.0, abs=1e-9)
        assert hi == pytest.approx(0.012643, abs=1e-5)

        lo, hi = helpers.wilson(15, 300)
        assert lo == pytest.approx(0.030531, abs=1e-5)
        assert hi == pytest.approx(0.080848, abs=1e-5)

    def test_the_interval_straddles_the_decision_threshold_at_five_percent(self) -> None:
        """Why the spec asks for an interval: 15/300 is exactly 5%, and the
        sample does not actually settle which side of the rule it falls on."""
        lo, hi = helpers.wilson(15, 300)
        assert lo < 0.05 < hi

    def test_no_attempts_is_an_error_not_an_interval(self) -> None:
        with pytest.raises(ValueError):
            helpers.wilson(0, 0)


class TestThePacerSpacesRequestsPerHostNotGlobally:
    """Run 1 paced every request on one global clock; one host's throttling
    then throttled every other host's pacing too, for no reason — the fix
    tracks the last request time per host."""

    def test_a_hosts_first_request_does_not_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(helpers, "_sleep_for", sleeps.append)
        clock = iter([100.0])
        pace = helpers._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        assert sleeps == []

    def test_a_second_request_to_the_same_host_waits_out_the_remainder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(helpers, "_sleep_for", sleeps.append)
        clock = iter([100.0, 101.0])
        pace = helpers._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        pace("https://a.example/y")
        assert sleeps == [2.0]

    def test_a_request_already_past_the_interval_does_not_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(helpers, "_sleep_for", sleeps.append)
        clock = iter([100.0, 104.0])
        pace = helpers._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        pace("https://a.example/y")
        assert sleeps == []

    def test_a_different_host_does_not_wait_for_an_unrelated_hosts_pacing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(helpers, "_sleep_for", sleeps.append)
        clock = iter([100.0, 100.1])
        pace = helpers._make_pacer(3.0, clock=lambda: next(clock))
        pace("https://a.example/x")
        pace("https://b.example/y")
        assert sleeps == []


class TestARetryAfterIsHonouredWithinItsClamp:
    """The delay function itself. That the *callers* apply it is pinned where
    they live — ``tests/test_free_pdf_sampler.py`` drives it through
    ``probe()``, which is what would catch a caller that stopped calling it.
    """

    def test_a_retry_after_under_the_cap_is_honoured_unchanged(self) -> None:
        assert helpers._throttle_delay(_Resp(429, headers={"Retry-After": "5"}), 1) == 5.0

    def test_an_enormous_retry_after_is_capped(self) -> None:
        delay = helpers._throttle_delay(_Resp(503, headers={"Retry-After": "86400"}), 1)
        assert delay == helpers.MAX_RETRY_AFTER_SECONDS

    def test_a_negative_retry_after_cannot_reach_time_sleep(self) -> None:
        """``time.sleep(-1)`` raises, which would end the run on a header a
        server is free to send."""
        assert helpers._throttle_delay(_Resp(429, headers={"Retry-After": "-30"}), 1) == 0.0

    def test_an_absent_header_falls_back_to_the_backoff(self) -> None:
        assert helpers._throttle_delay(_Resp(429), 1) == helpers.RETRY_BACKOFF_SECONDS[0]
        assert helpers._throttle_delay(_Resp(429), 2) == helpers.RETRY_BACKOFF_SECONDS[1]

    def test_an_http_date_is_not_mistaken_for_seconds(self) -> None:
        """``int("Wed, 21 Oct 2015 07:28:00 GMT")`` raises; the fallback is
        the backoff, not a crash and not a zero."""
        header = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
        assert (
            helpers._throttle_delay(_Resp(429, headers=header), 1)
            == (helpers.RETRY_BACKOFF_SECONDS[0])
        )
