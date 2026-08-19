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

"""Tests for ``scripts/sample_efetch_paging.py``.

The script is a live runner, but what it prints is the evidence for
``EFETCH_MAX_RETRIEVABLE`` and for the fixed stride in ``fetch_pubmed``'s page
walk, so the reading has to be trustworthy offline. Two properties are pinned
here, and both are about the same failure:

**A probe that could not be made never prints as a measurement.** This script
has a sharper version of the problem its companions have, because the number
it is chasing is reported by an HTTP 400: a refusal *is* the measurement at the
boundary, and a request that merely failed looks exactly like one. Read as a
refusal, a throttled probe drags the binary search down with it and prints a
cap that no server ever enforced — a number a maintainer would then hard-code.

**A day that could not be sized is excluded, never counted as small.** The
share of days over the cap is what sizes issue #105, and an unread day scored
as "under the cap" moves that share in the reassuring direction.

No network: every test drives the script through a stubbed ``_get``.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

# `scripts/` is not a package — it holds runnable tools, not importable modules
# — so the module is loaded by path. Executing it is safe: everything below
# `if __name__ == "__main__"` stays unrun. `scripts/` goes on the path first so
# the script's own `from _sampling import wilson` resolves.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "bmlib_efetch_paging_sampler", _SCRIPTS / "sample_efetch_paging.py"
)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError("cannot load the efetch paging sampler")
sampler = importlib.util.module_from_spec(_spec)
# Registered before execution because `@dataclass` resolves its own module out
# of `sys.modules`; loaded by path alone it raises on the class body.
sys.modules[_spec.name] = sampler
_spec.loader.exec_module(sampler)


SESSION = (500_000, "WEBENV1", "1")

_REFUSAL = (
    "<eFetchResult><ERROR>Search backend cannot retrieve history data. Reason:"
    " Exception: 'retstart' cannot be larger than 9998.</ERROR></eFetchResult>"
)


class _FakeEUtils:
    """E-utilities with a configurable retrieval limit and configurable failures.

    *limit* is the number of records the session serves, so ``retstart`` may go
    up to ``limit - 1``. *fail_at* names the ``retstart`` values whose request
    fails outright — the case that must not be read as a refusal.
    """

    def __init__(
        self,
        *,
        limit: int = 9999,
        clamps: bool = True,
        fail_at: frozenset[int] = frozenset(),
        counts: dict[str, int | None] | None = None,
        records: list[str] | None = None,
    ) -> None:
        self.limit = limit
        self.clamps = clamps
        self.fail_at = fail_at
        self.counts = counts or {}
        self.records = records

    def __call__(self, url: str, params: dict[str, str]) -> tuple[int, str] | None:
        if url == sampler.ESEARCH and params.get("usehistory") == "y":
            return 200, (
                f"<eSearchResult><Count>{SESSION[0]}</Count>"
                f"<WebEnv>{SESSION[1]}</WebEnv><QueryKey>{SESSION[2]}</QueryKey></eSearchResult>"
            )
        if url == sampler.ESEARCH:
            count = self.counts.get(params["term"], 5000)
            if count is None:
                return None
            return 200, f"<eSearchResult><Count>{count}</Count></eSearchResult>"

        retstart, retmax = int(params["retstart"]), int(params["retmax"])
        if retstart in self.fail_at:
            return None
        if retstart >= self.limit:
            return 400, _REFUSAL
        served = min(retmax, self.limit - retstart) if self.clamps else retmax
        if params.get("rettype") == "uilist":
            return 200, "\n".join(str(90_000 + i) for i in range(retstart, retstart + served))
        if self.records is not None:
            body = "".join(self.records)
        else:
            body = "".join(
                f"<PubmedArticle><MedlineCitation><PMID>{90_000 + i}</PMID>"
                "</MedlineCitation></PubmedArticle>"
                for i in range(retstart, retstart + served)
            )
        return 200, f"<PubmedArticleSet>{body}</PubmedArticleSet>"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No test here may reach NCBI: every one drives the script through a stub."""
    monkeypatch.setattr(sampler, "_get", _FakeEUtils())


def _run(monkeypatch, capsys, fake: _FakeEUtils, *args: str) -> str:
    """Run ``main()`` against *fake*, returning what it printed."""
    monkeypatch.setattr(sampler, "_get", fake)
    monkeypatch.setattr(sys, "argv", ["sample_efetch_paging.py", "--email", "t@example.org", *args])
    sampler.main()
    return capsys.readouterr().out


class TestTheBoundarySearchReportsOnlyWhatItMeasured:
    """The number this script exists for is reported by an HTTP 400.

    Which means a failed request and the answer are the same shape, and the
    search converges on whichever it is told.
    """

    def test_the_measured_boundary_is_the_last_retstart_served(self):
        probe = sampler.measure_boundary(SESSION, {})

        assert probe.ok
        assert probe.value == 9998  # so the session serves 9,999 records

    def test_a_backend_with_a_different_limit_is_reported_as_disagreeing(self, monkeypatch, capsys):
        """A cap that moved must read as a disagreement, not be silently absorbed."""
        out = _run(monkeypatch, capsys, _FakeEUtils(limit=50_000), "--skip-day-sizes")

        assert "DISAGREES" in out
        assert "50000 records" in out

    def test_a_failed_probe_mid_search_is_an_error_not_a_boundary(self, monkeypatch, capsys):
        """The whole point: a dead request read as a refusal invents a cap.

        The search's first midpoint is 65,536; failing it would otherwise pull
        the upper bound down to it and every later step with it.
        """
        out = _run(
            monkeypatch, capsys, _FakeEUtils(fail_at=frozenset({65_536})), "--skip-day-sizes"
        )

        assert "largest served retstart: ERROR" in out
        assert "65536" in out
        assert "agrees" not in out

    def test_a_failed_probe_at_the_known_good_end_is_an_error(self, monkeypatch):
        """A search whose lower bound never answered has no bound to start from."""
        monkeypatch.setattr(sampler, "_get", _FakeEUtils(fail_at=frozenset({0})))

        probe = sampler.measure_boundary(SESSION, {})

        assert not probe.ok
        assert "known-good end" in (probe.error or "")

    def test_a_ceiling_that_is_served_is_an_error_not_a_measurement(self, monkeypatch):
        """If the backend serves the search's upper bound, there is no bound."""
        monkeypatch.setattr(sampler, "_get", _FakeEUtils(limit=10**9))

        probe = sampler.measure_boundary(SESSION, {})

        assert not probe.ok
        assert "raise the ceiling" in (probe.error or "")

    def test_a_refusal_at_zero_is_an_error_not_a_cap_of_zero(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", _FakeEUtils(limit=0))

        probe = sampler.measure_boundary(SESSION, {})

        assert not probe.ok
        assert "no boundary to find" in (probe.error or "")


class TestTheSilentClampIsReportedSeparately:
    """The HTTP 400 is the loud half of the limit; this is the quiet half."""

    def test_a_clamped_page_is_named_as_clamped(self, monkeypatch, capsys):
        out = _run(monkeypatch, capsys, _FakeEUtils(), "--skip-day-sizes")

        assert "clamped silently" in out

    def test_a_page_served_whole_says_the_limit_moved_rather_than_reporting_a_clamp(
        self, monkeypatch, capsys
    ):
        """A page asking past the boundary and getting all of it is not a reading.

        It means the two probes disagree — the search found a boundary the
        straddle probe was then served past — and the run has measured nothing
        it can print as the clamp.
        """
        out = _run(monkeypatch, capsys, _FakeEUtils(clamps=False), "--skip-day-sizes")

        assert "the limit moved under the probe" in out
        assert "clamped silently" not in out

    def test_a_failed_straddle_probe_prints_an_error(self, monkeypatch, capsys):
        out = _run(monkeypatch, capsys, _FakeEUtils(fail_at=frozenset({9500})), "--skip-day-sizes")

        assert "the page straddling the boundary: ERROR" in out


class TestTheSliceProbeCannotConfirmItselfByAccident:
    """This probe is the evidence for the fixed stride, so its negative matters."""

    def test_records_matching_the_uid_slice_are_reported_as_the_slice(self, monkeypatch, capsys):
        out = _run(monkeypatch, capsys, _FakeEUtils(), "--skip-day-sizes")

        assert "the slice, in order" in out

    def test_records_that_are_not_the_slice_are_reported_as_a_void_assumption(
        self, monkeypatch, capsys
    ):
        """A mismatch is the one result that would change `fetch_pubmed`."""
        wrong = ["<PubmedArticle><MedlineCitation><PMID>1</PMID></MedlineCitation></PubmedArticle>"]
        out = _run(monkeypatch, capsys, _FakeEUtils(records=wrong), "--skip-day-sizes")

        assert "NOT the slice" in out

    def test_an_unfetchable_record_page_prints_an_error(self, monkeypatch, capsys):
        out = _run(monkeypatch, capsys, _FakeEUtils(fail_at=frozenset({0})), "--skip-day-sizes")

        assert "ERROR" in out
        assert "the slice, in order" not in out


class TestADayThatCouldNotBeSizedIsNotCountedAsSmall:
    """The over-cap share sizes issue #105; an unread day must not soften it."""

    def test_unmeasured_days_are_excluded_from_the_population(self, capsys):
        rows = [
            (date(2026, 1, 1), 200_000),
            (date(2026, 1, 2), 5_000),
            (date(2026, 1, 3), None),
        ]

        sampler.report_day_sizes(rows, "test")
        out = capsys.readouterr().out

        assert "2 days measured, 1 unmeasured" in out
        assert "1/2 = 50.0%" in out

    def test_a_population_that_was_wholly_unmeasured_reports_no_share(self, capsys):
        sampler.report_day_sizes([(date(2026, 1, 1), None)], "test")
        out = capsys.readouterr().out

        assert "ERROR" in out
        assert "%" not in out

    def test_an_over_cap_day_says_how_many_records_are_out_of_reach(self, capsys):
        sampler.report_day_sizes([(date(2026, 1, 1), 200_000)], "test")
        out = capsys.readouterr().out

        assert "190001 out of reach" in out

    def test_the_cap_the_share_is_taken_against_is_bmlibs_own(self):
        """The script must not carry a second copy of the constant it checks."""
        from bmlib.publications.fetchers.pubmed import EFETCH_MAX_RETRIEVABLE

        assert sampler.EFETCH_MAX_RETRIEVABLE is EFETCH_MAX_RETRIEVABLE


class TestTheStructuralDaysAreTheOnesTheIndexingConventionMakesLarge:
    """Month firsts and 1 January are a second population, not a long tail."""

    def test_every_month_first_of_the_trailing_year_is_included(self):
        days = sampler._structural_days(date(2026, 8, 20))

        assert date(2026, 8, 1) in days
        assert date(2026, 1, 1) in days
        assert date(2025, 12, 1) in days
        assert date(2025, 9, 1) in days

    def test_january_first_of_earlier_years_is_included(self):
        days = sampler._structural_days(date(2026, 8, 20))

        assert {date(2025, 1, 1), date(2024, 1, 1), date(2023, 1, 1)} <= set(days)

    def test_a_day_is_listed_once(self):
        days = sampler._structural_days(date(2026, 8, 20))

        assert len(days) == len(set(days))


class TestTheDaySizeWalkAsksForThePublicationDateField:
    """`[EDAT]` and `[Date - Publication]` are different populations.

    Measured 2026-08-20: under the field bmlib queries, no ordinary day was
    over the cap (0 of 58) and every structural one was (16 of 16, up to
    315,282). Under EDAT the same question gives 4 of 120 days, none above
    12,096 — sampling the wrong field understates the magnitude by a factor of
    25 and blames load spikes for what the indexing convention does.
    """

    def test_the_term_names_the_publication_date(self, monkeypatch):
        asked = []

        def fake(url, params):
            asked.append(params.get("term", ""))
            return 200, "<eSearchResult><Count>1</Count></eSearchResult>"

        monkeypatch.setattr(sampler, "_get", fake)
        sampler.measure_day_sizes([date(2026, 1, 1)], {})

        assert asked == ['("2026/01/01"[Date - Publication])']

    def test_a_response_carrying_no_count_is_unmeasured_not_zero(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", lambda url, params: (200, "<eSearchResult/>"))

        assert sampler.measure_day_sizes([date(2026, 1, 1)], {}) == [(date(2026, 1, 1), None)]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_throttled_uilist_probe_is_unmeasured_rather_than_refused(monkeypatch, status):
    """Only a 400 is the boundary; every other non-200 is a failed probe.

    Read as a refusal, one 429 during the search reports a cap that is simply
    where the throttling started.
    """
    monkeypatch.setattr(sampler, "_get", lambda url, params: (status, "throttled"))

    probe = sampler._uilist(SESSION, 0, 1, {})

    assert not probe.ok
    assert not probe.refused
