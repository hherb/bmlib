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

import contextlib
import email.message
import importlib.util
import io
import sys
import urllib.error
from collections.abc import Callable
from datetime import date, timedelta
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

# Captured before the autouse fixture replaces `sampler._get` with a stub: the
# throttle-retry rules live inside the real one, so testing them needs the
# function itself rather than the stand-in every other test drives.
_REAL_GET = sampler._get


SESSION = sampler.Session(500_000, "WEBENV1", "1")

# What the script calls: `_get(url, params)`, answering with a status and a
# body, or None for a request that never arrived. `_FakeEUtils` is one, and so
# is every ad-hoc stub below.
_Get = Callable[[str, dict[str, str]], tuple[int, str] | None]

# What `_count(term, base)` answers: a bare record count, or None for a
# question that could not be asked. A different shape from `_Get` above —
# `_count` sits above `_get`, already stripped of status and body — so a
# `--partition` stub is built against this alias, not that one.
_Count = Callable[[str, dict[str, str]], int | None]

_REFUSAL = (
    "<eFetchResult><ERROR>Search backend cannot retrieve history data. Reason:"
    " Exception: 'retstart' cannot be larger than 9998.</ERROR></eFetchResult>"
)


class _FakeEUtils:
    """E-utilities with a configurable retrieval limit and configurable failures.

    *limit* is the number of records the session serves, so ``retstart`` may go
    up to ``limit - 1``. *fail_at* names the ``retstart`` values whose request
    fails outright — the case that must not be read as a refusal. *day_count*
    is what a day-size esearch reports, or None for a day the request never
    came back for.
    """

    def __init__(
        self,
        *,
        limit: int = 9999,
        clamps: bool = True,
        fail_at: frozenset[int] = frozenset(),
        day_count: int | None = 5000,
        records: list[str] | None = None,
    ) -> None:
        self.limit = limit
        self.clamps = clamps
        self.fail_at = fail_at
        self.day_count = day_count
        self.records = records

    def __call__(self, url: str, params: dict[str, str]) -> tuple[int, str] | None:
        if url == sampler.ESEARCH and params.get("usehistory") == "y":
            return 200, (
                f"<eSearchResult><Count>{SESSION.count}</Count>"
                f"<WebEnv>{SESSION.web_env}</WebEnv>"
                f"<QueryKey>{SESSION.query_key}</QueryKey></eSearchResult>"
            )
        if url == sampler.ESEARCH:
            if self.day_count is None:
                return None
            return 200, f"<eSearchResult><Count>{self.day_count}</Count></eSearchResult>"

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


class _Reply:
    """The bare shape ``_get`` reads off a successful ``urlopen`` result."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No test here may reach NCBI: every one drives the script through a stub."""
    monkeypatch.setattr(sampler, "_get", _FakeEUtils())


def _status(monkeypatch, fake: _Get, *args: str) -> int:
    """Run ``main()`` against *fake*, returning the status it exits with."""
    monkeypatch.setattr(sampler, "_get", fake)
    monkeypatch.setattr(sys, "argv", ["sample_efetch_paging.py", "--email", "t@example.org", *args])
    return sampler.main()


def _run(monkeypatch, capsys, fake: _Get, *args: str) -> str:
    """Run ``main()`` against *fake*, returning what it printed."""
    _status(monkeypatch, fake, *args)
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
        """The denominator is what was read, not what was asked for.

        Six rows so the one unmeasured day stays inside
        ``UNMEASURED_SHARE_ERROR_THRESHOLD``; past it the share is withheld
        entirely, which is the test below.
        """
        rows = [
            (date(2026, 1, 1), 200_000),
            (date(2026, 1, 2), 5_000),
            (date(2026, 1, 3), 5_000),
            (date(2026, 1, 4), 5_000),
            (date(2026, 1, 5), 5_000),
            (date(2026, 1, 6), None),
        ]

        assert sampler.report_day_sizes(rows, "test") is True
        out = capsys.readouterr().out

        assert "5 days measured, 1 unmeasured" in out
        assert "1/5 = 20.0%" in out

    def test_a_population_mostly_unmeasured_reports_no_share(self, capsys):
        """Excluding an unread day is necessary but not sufficient.

        What survives a throttled run is the *early* attempts, so the
        surviving days are not a random sample of the population and a
        precise-looking share over them is not evidence. Both sibling samplers
        gate on the same threshold; this one carried only the all-or-nothing
        case, so 118 days of 120 throttled out still printed a share with a
        95% interval.
        """
        rows = [(date(2026, 1, 1), 5_000), (date(2026, 1, 2), None), (date(2026, 1, 3), None)]

        assert sampler.report_day_sizes(rows, "test") is False
        out = capsys.readouterr().out

        assert "ERROR" in out
        assert "2 of 3 days went unmeasured" in out
        # The share line and its interval are the thing withheld; the threshold
        # itself is rendered as a percentage in the refusal, so a bare "%" test
        # would pass vacuously against the message rather than against the share.
        assert "over 9999" not in out
        assert "CI" not in out

    def test_a_day_exactly_at_the_cap_is_not_reported_as_out_of_reach(self, capsys):
        """The cap is the largest *fetchable* day, not the smallest refused one.

        `fetch_pubmed` walks a 9,999-record day to completion, so counting it
        here as over the cap would inflate the share that sizes #105.
        """
        sampler.report_day_sizes([(date(2026, 1, 1), 9_999)], "test")
        out = capsys.readouterr().out

        assert "0/1 = 0.0%" in out
        assert "out of reach" not in out

    def test_a_population_that_was_wholly_unmeasured_reports_no_share(self, capsys):
        assert sampler.report_day_sizes([(date(2026, 1, 1), None)], "test") is False
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
    12,096 — so the largest day the right field finds is 26x the largest the
    wrong one does, and sampling the wrong field blames load spikes for what
    the indexing convention does. The EDAT figure was a one-off probe: this
    walk has no flag for it, which is what the test below pins.
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


class TestOnlyTheRetstartRefusalIsTheBoundary:
    """A 400 is the measurement here, which makes *which* 400 load-bearing.

    The rule below — every non-200 that is not a 400 is a failed probe — has a
    mirror the first cut left open. A 400 raised by anything other than the
    retstart limit (a WebEnv the backend has dropped, a malformed parameter, a
    backend error rendered as 400) is not evidence of a limit either, and read
    as one mid-search it collapses the upper bound onto wherever it began and
    prints a cap no server enforces.
    """

    def test_a_400_that_does_not_name_retstart_is_a_failed_probe(self, monkeypatch):
        dropped = (
            "<eFetchResult><ERROR>Search backend failed: WebEnv WEBENV1"
            " is not valid</ERROR></eFetchResult>"
        )
        monkeypatch.setattr(sampler, "_get", lambda url, params: (400, dropped))

        probe = sampler._uilist(SESSION, 0, 1, {})

        assert not probe.ok
        assert not probe.refused
        assert "not the retstart refusal" in (probe.error or "")

    def test_a_400_naming_retstart_is_still_the_refusal(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", lambda url, params: (400, _REFUSAL))

        probe = sampler._uilist(SESSION, 0, 1, {})

        assert probe.refused
        assert probe.ok

    def test_a_foreign_400_mid_search_does_not_become_a_cap(self, monkeypatch, capsys):
        """The failure the body check exists to stop, driven end to end."""

        class _DropsWebEnvAboveTheCap(_FakeEUtils):
            def __call__(self, url, params):
                if url == sampler.EFETCH and int(params["retstart"]) >= self.limit:
                    return 400, "<eFetchResult><ERROR>WebEnv is not valid</ERROR></eFetchResult>"
                return super().__call__(url, params)

        out = _run(monkeypatch, capsys, _DropsWebEnvAboveTheCap(), "--skip-day-sizes")

        assert "largest served retstart: ERROR" in out
        assert "agrees" not in out
        assert "DISAGREES" not in out


class TestTheBoundarySearchNeedsASessionBiggerThanItsCeiling:
    """The ceiling is only *known* to be refused if it is past the session too.

    Over a smaller session the search converges on the session's own size and
    prints it as the backend's limit — a `DISAGREES` line telling a maintainer
    to change the constant that gates every over-cap day.
    """

    def test_a_session_smaller_than_the_ceiling_is_refused(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", _FakeEUtils(limit=40_000))
        small = sampler.Session(50_000, "WEBENV1", "1")

        probe = sampler.measure_boundary(small, {})

        assert not probe.measured
        assert "would measure the session, not the cap" in (probe.error or "")

    def test_a_session_larger_than_the_ceiling_is_measured(self):
        probe = sampler.measure_boundary(SESSION, {})

        assert probe.measured
        assert probe.value == 9998


class _TruncatesTheStraddlingPage(_FakeEUtils):
    """Answers the straddling page short of the clamp its boundary predicts."""

    def __call__(self, url, params):
        got = super().__call__(url, params)
        if got and params.get("rettype") == "uilist" and int(params["retstart"]) == 9500:
            return 200, "\n".join(got[1].splitlines()[:300])
        return got


class TestTheStraddleProbeReportsOnlyACleanClamp:
    """A page can come back short for reasons that are not the clamp."""

    def test_a_session_ending_at_the_boundary_cannot_show_a_clamp(self, monkeypatch):
        """A page short because the session ran out is not a clamped page."""
        monkeypatch.setattr(sampler, "_get", _FakeEUtils())
        exact = sampler.Session(9_999, "WEBENV1", "1")

        probe = sampler.measure_straddling_page(exact, 9_998, {})

        assert not probe.measured
        assert "the session ran out" in (probe.error or "")

    def test_a_page_short_of_the_expected_clamp_is_not_printed_as_the_clamp(
        self, monkeypatch, capsys
    ):
        """499 was expected at this boundary; 300 is two probes disagreeing."""
        out = _run(monkeypatch, capsys, _TruncatesTheStraddlingPage(), "--skip-day-sizes")

        assert "499 were expected at this boundary" in out
        assert "clamped silently" not in out

    def test_a_page_short_of_the_expected_clamp_fails_the_run(self, monkeypatch):
        """Saying "re-run" in the output and exiting 0 are contradictory."""
        assert _status(monkeypatch, _TruncatesTheStraddlingPage(), "--skip-day-sizes") == 1

    def test_a_page_served_whole_fails_the_run(self, monkeypatch):
        """The louder of the two disagreements: a page served past a boundary
        the search had just found refuses it. It printed "re-run" and exited 0.
        """
        assert _status(monkeypatch, _FakeEUtils(clamps=False), "--skip-day-sizes") == 1

    def test_a_clean_clamp_does_not_fail_the_run(self, monkeypatch):
        """The negative control for the two above."""
        assert _status(monkeypatch, _FakeEUtils(), "--skip-day-sizes") == 0

    def test_the_probe_asks_for_the_page_bmlib_actually_walks(self, monkeypatch):
        """Sized in EFETCH_PAGE_SIZE, so raising it does not leave this measuring
        a page no fetcher issues."""
        asked = []

        def fake(url, params):
            asked.append((int(params["retstart"]), int(params["retmax"])))
            return 200, "\n".join(str(i) for i in range(499))

        monkeypatch.setattr(sampler, "_get", fake)
        sampler.measure_straddling_page(SESSION, 9_998, {})

        assert asked == [(9_999 + 1 - sampler.EFETCH_PAGE_SIZE, sampler.EFETCH_PAGE_SIZE)]


class TestOneDeleteCitationHoldsManyUids:
    """`<DeleteCitation>` carries a PMID per deleted record, all of them slots.

    Reading only the first collapses N UIDs into one entry, and the script then
    prints "NOT the slice; the stride assumption is void" — telling a
    maintainer to advance `retstart` by what arrived, which is exactly the
    change #96 was closed for refusing.
    """

    @staticmethod
    def _page_with_a_deleted_pair() -> list[str]:
        articles = [
            f"<PubmedArticle><MedlineCitation><PMID>{90_000 + i}</PMID>"
            "</MedlineCitation></PubmedArticle>"
            for i in range(48)
        ]
        return [*articles, "<DeleteCitation><PMID>90048</PMID><PMID>90049</PMID></DeleteCitation>"]

    def test_a_page_carrying_a_deleted_pair_is_still_the_slice(self, monkeypatch, capsys):
        out = _run(
            monkeypatch,
            capsys,
            _FakeEUtils(records=self._page_with_a_deleted_pair()),
            "--skip-day-sizes",
        )

        assert "50 record elements against 50 UIDs" in out
        assert "the slice, in order" in out

    def test_an_article_subtree_carrying_another_records_pmid_is_not_expanded(
        self, monkeypatch, capsys
    ):
        """`<CommentsCorrections>` holds the PMID of a *different* record.

        So the expansion cannot be "every PMID in the subtree" — for an article
        element the citation's own PMID is the first, and the rest belong to
        records that occupy no slot in this page.
        """
        records = [
            "<PubmedArticle><MedlineCitation><PMID>90000</PMID>"
            "<CommentsCorrectionsList><CommentsCorrections>"
            "<PMID>12345678</PMID></CommentsCorrections></CommentsCorrectionsList>"
            "</MedlineCitation></PubmedArticle>",
            *[
                f"<PubmedArticle><MedlineCitation><PMID>{90_000 + i}</PMID>"
                "</MedlineCitation></PubmedArticle>"
                for i in range(1, 50)
            ],
        ]
        out = _run(monkeypatch, capsys, _FakeEUtils(records=records), "--skip-day-sizes")

        assert "50 record elements against 50 UIDs" in out
        assert "the slice, in order" in out


class TestAnErrorDocumentIsNotASmallDay:
    """E-utilities reports a failed search at HTTP 200, and still sends a Count."""

    def test_an_error_document_carrying_a_zero_count_is_unmeasured(self, monkeypatch):
        body = (
            "<eSearchResult><Count>0</Count><ErrorList>"
            "<FieldNotFound>Date - Publication</FieldNotFound></ErrorList></eSearchResult>"
        )
        monkeypatch.setattr(sampler, "_get", lambda url, params: (200, body))

        assert sampler.measure_day_sizes([date(2026, 1, 1)], {}) == [(date(2026, 1, 1), None)]

    def test_a_bare_error_element_is_unmeasured(self, monkeypatch):
        body = "<eSearchResult><Count>0</Count><ERROR>Invalid db name</ERROR></eSearchResult>"
        monkeypatch.setattr(sampler, "_get", lambda url, params: (200, body))

        assert sampler.measure_day_sizes([date(2026, 1, 1)], {}) == [(date(2026, 1, 1), None)]

    def test_a_nested_count_is_not_read_as_the_days_count(self, monkeypatch):
        """The day's count is the document element's own child, not any <Count>.

        `<TranslationStack>` carries one per sub-term. A regex over the body —
        what this used to be — matches whichever comes first in document
        order, so a response that carries a sub-term count but no top-level one
        reads as a day of 7 records instead of as unmeasured. Note the
        ordinary conjunction case does *not* discriminate between the two
        implementations, because a real `eSearchResult` puts its own <Count>
        first; this is the payload where they differ.
        """
        body = (
            "<eSearchResult><TranslationStack>"
            "<TermSet><Term>a</Term><Count>7</Count></TermSet>"
            "</TranslationStack></eSearchResult>"
        )
        monkeypatch.setattr(sampler, "_get", lambda url, params: (200, body))

        assert sampler.measure_day_sizes([date(2026, 1, 1)], {}) == [(date(2026, 1, 1), None)]

    def test_an_ordinary_response_is_read_as_its_count(self, monkeypatch):
        """The negative control for the test above: a normal body still reads."""
        body = (
            "<eSearchResult><Count>40000</Count><TranslationStack>"
            "<TermSet><Term>a</Term><Count>7</Count></TermSet>"
            "</TranslationStack></eSearchResult>"
        )
        monkeypatch.setattr(sampler, "_get", lambda url, params: (200, body))

        assert sampler.measure_day_sizes([date(2026, 1, 1)], {}) == [(date(2026, 1, 1), 40_000)]

    def test_an_unparsable_body_is_unmeasured(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", lambda url, params: (200, "<eSearchResult>"))

        assert sampler.measure_day_sizes([date(2026, 1, 1)], {}) == [(date(2026, 1, 1), None)]


class TestAThrottledRequestIsRetriedRatherThanLost:
    """One transient 429 must not cost the run its headline measurement.

    A full run makes ~150 requests against NCBI's 3/s unauthenticated ceiling,
    so a throttle is the expected shape here. Without a retry, one of them
    mid-binary-search abandons the boundary search outright, and one during the
    day-size walk silently drops that day out of its population.
    """

    @staticmethod
    def _http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "https://example.org", code, "throttled", email.message.Message(), io.BytesIO(b"slow")
        )

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch):
        monkeypatch.setattr(sampler.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)

    @pytest.mark.parametrize("code", [429, 503])
    def test_a_throttled_request_is_retried_and_its_answer_returned(self, monkeypatch, code):
        replies = [self._http_error(code), self._http_error(code)]

        def fake_urlopen(url, timeout=None):
            if replies:
                raise replies.pop(0)
            return contextlib.nullcontext(_Reply(200, b"<eSearchResult/>"))

        monkeypatch.setattr(sampler.urllib.request, "urlopen", fake_urlopen)

        assert _REAL_GET("https://example.org", {}) == (200, "<eSearchResult/>")
        assert replies == []

    def test_a_persistent_throttle_returns_the_status_rather_than_looping(self, monkeypatch):
        attempts = []

        def fake_urlopen(url, timeout=None):
            attempts.append(url)
            raise self._http_error(429)

        monkeypatch.setattr(sampler.urllib.request, "urlopen", fake_urlopen)

        assert _REAL_GET("https://example.org", {}) == (429, "slow")
        assert len(attempts) == sampler.MAX_PROBE_ATTEMPTS

    def test_a_400_is_not_retried_because_it_is_the_measurement(self, monkeypatch):
        attempts = []

        def fake_urlopen(url, timeout=None):
            attempts.append(url)
            raise urllib.error.HTTPError(
                url, 400, "bad", email.message.Message(), io.BytesIO(_REFUSAL.encode())
            )

        monkeypatch.setattr(sampler.urllib.request, "urlopen", fake_urlopen)

        assert _REAL_GET("https://example.org", {}) == (400, _REFUSAL)
        assert len(attempts) == 1


class TestAProbeCannotBeBuiltWithNothingToSay:
    """`Probe()` would otherwise read as a measurement whose value is None.

    Which is the unmeasured probe reported as a finding that the class exists
    to prevent — the sibling `ProbeOutcome` cannot be constructed empty either.
    """

    def test_an_empty_probe_is_refused(self):
        with pytest.raises(ValueError, match="must say why"):
            sampler.Probe()

    def test_a_failed_probe_carrying_a_value_is_refused(self):
        with pytest.raises(ValueError, match="neither a refusal nor a value"):
            sampler.Probe(value=5, error="boom")

    def test_a_failed_probe_that_also_claims_a_refusal_is_refused(self):
        with pytest.raises(ValueError, match="neither a refusal nor a value"):
            sampler.Probe(refused=True, error="boom")

    def test_a_measurement_of_zero_is_legal(self):
        """`value=0` is a real cap of one record, and `[]` a real empty slice."""
        assert sampler.Probe(value=0).measured
        assert sampler.Probe(value=[]).measured

    def test_a_refusal_is_an_answer_but_not_a_measurement(self):
        probe = sampler.Probe(refused=True)

        assert probe.ok
        assert not probe.measured


class TestARunThatMeasuredNothingDoesNotExitLikeOneThatDid:
    """These probes are the evidence for a hard-coded constant.

    A scheduled re-run is judged by its exit status, so a run whose evidence
    never arrived must not be indistinguishable from one whose evidence agreed.
    """

    def test_a_clean_session_run_exits_zero(self, monkeypatch):
        assert _status(monkeypatch, _FakeEUtils(), "--skip-day-sizes") == 0

    def test_a_run_whose_boundary_search_failed_exits_non_zero(self, monkeypatch):
        fake = _FakeEUtils(fail_at=frozenset({65_536}))

        assert _status(monkeypatch, fake, "--skip-day-sizes") == 1

    def test_a_run_whose_slice_probe_failed_exits_non_zero(self, monkeypatch):
        fake = _FakeEUtils(fail_at=frozenset({0}))

        assert _status(monkeypatch, fake, "--skip-day-sizes") == 1

    def test_a_day_size_population_past_the_threshold_exits_non_zero(self, monkeypatch):
        """The populations are reported, and one withheld share fails the run."""
        fake = _FakeEUtils(day_count=None)

        assert _status(monkeypatch, fake, "--days", "2") == 1


class TestTheThreePopulationsArePartitionedNotOverlapped:
    """The day-size half of `main()`, which the session probes skip.

    The numbers hard-coded into `pubmed.py`, `docs/DECISIONS.md` and
    `CLAUDE.md` come out of this partition, and its own comment states an
    invariant nothing else checks: a month first inside the window is reported
    in the structural population and *not* re-fetched for it. Let month firsts
    back into the ordinary population and "no ordinary day was over the cap" —
    the sentence the guard's scope rests on — becomes a statement about a
    population containing the days that are always over.
    """

    @staticmethod
    def _sized(monkeypatch, capsys, *args: str) -> tuple[list[str], str]:
        """Run the full day-size half, returning the terms asked and the output."""
        asked: list[str] = []
        structural = {f"{day:%Y/%m/%d}" for day in sampler._structural_days(date.today())}

        def fake(url, params):
            # The day-size counts are the only thing this stub is here for;
            # the session probes are the ordinary fake's job.
            if url != sampler.ESEARCH or params.get("usehistory") == "y":
                return _FakeEUtils()(url, params)
            term = params["term"]
            asked.append(term)
            big = any(stamp in term for stamp in structural)
            return 200, f"<eSearchResult><Count>{200_000 if big else 5_000}</Count></eSearchResult>"

        return asked, _run(monkeypatch, capsys, fake, *args)

    def test_no_day_is_sized_twice(self, monkeypatch, capsys):
        """A re-fetched day doubles the run's budget against a rate-limited API."""
        asked, _ = self._sized(monkeypatch, capsys, "--days", "40")

        assert len(asked) == len(set(asked))

    def test_the_ordinary_population_excludes_every_structural_day(self, monkeypatch, capsys):
        _, out = self._sized(monkeypatch, capsys, "--days", "40")
        ordinary = out.split("Ordinary days")[1].split("Month firsts")[0]

        assert "over 9999: 0/" in ordinary

    def test_the_structural_population_is_every_structural_day(self, monkeypatch, capsys):
        """Month firsts inside the window and outside it both land here."""
        _, out = self._sized(monkeypatch, capsys, "--days", "40")
        structural = out.split("Month firsts and 1 January")[1].split("Every day")[0]
        expected = len(sampler._structural_days(date.today()))

        assert f"{expected} days measured, 0 unmeasured" in structural
        assert f"{expected}/{expected} = 100.0%" in structural

    def test_the_window_is_reported_whole_as_well_as_split(self, monkeypatch, capsys):
        """ "What share of ordinary days is fine" and "what share of a sync
        window will fail" are different questions; only the second sizes the
        retry cost."""
        _, out = self._sized(monkeypatch, capsys, "--days", "40")

        assert "Every day of the last 40 — one sync window" in out
        assert out.count("days measured,") == 3


def _stub_count(distribution: dict[date, int]) -> _Count:
    """A synthetic Entrez-date distribution, as a ``_count``-shaped stand-in.

    The same shape ``tests/test_pubmed_fetcher.py``'s ``_counter`` uses for
    ``_plan_partitions``, written out again here rather than imported — the
    two test modules must be able to disagree, since one exists to confirm a
    rule and the other exists to check it independently. It answers the bare
    day term the same way it answers the full-root EDAT range (the whole
    distribution), and any narrower range with the slice of *distribution*
    that range covers — mirroring what a real ``[Date - Publication] AND
    ("lo"[EDAT] : "hi"[EDAT])`` query would report.
    """

    def count(term: str, base: dict[str, str]) -> int | None:
        if "[EDAT]" not in term:  # the bare day term, before any range narrows it
            return sum(distribution.values())
        lo_text, hi_text = term.split('"[EDAT] : "')
        lo = _parse_stamp(lo_text.rsplit('"', 1)[-1])
        hi = _parse_stamp(hi_text.split('"', 1)[0])
        return sum(n for d, n in distribution.items() if lo <= d <= hi)

    return count


def _parse_stamp(stamp: str) -> date:
    """Parse a bare ``YYYY/MM/DD`` stamp, as ``_range_term`` writes it."""
    year, month, day = (int(part) for part in stamp.split("/"))
    return date(year, month, day)


class TestThePartitionMode:
    """A probe that could not be made must never print as a finding.

    `measure_partition` is a second, independent descent from
    `fetchers/pubmed.py`'s `_plan_partitions` — this test class is what
    guards that independence as well as the behaviour, since a corpus
    labelled by the rule under test could only ever confirm that rule.
    """

    def test_a_failed_count_is_unmeasured_not_a_finding(self, monkeypatch):
        monkeypatch.setattr(sampler, "_count", lambda term, base: None)

        report = sampler.measure_partition(date(2024, 1, 1), {})

        assert report.day_count is None
        assert report.parts == 0
        assert report.exact is None

    def test_the_ladder_tiles_and_reports_exact(self, monkeypatch):
        distribution = {date(2023, 6, 1) + timedelta(days=i): 4000 for i in range(10)}
        monkeypatch.setattr(sampler, "_count", _stub_count(distribution))

        report = sampler.measure_partition(date(2024, 1, 1), {})

        assert report.exact is True
        assert report.stuck == []
        assert sum(1 for _ in range(report.parts)) == report.parts
        assert report.parts > 1

    def test_a_single_entrez_day_over_the_cap_is_reported_stuck(self, monkeypatch):
        monkeypatch.setattr(sampler, "_count", _stub_count({date(2023, 6, 1): 25000}))

        report = sampler.measure_partition(date(2024, 1, 1), {})

        assert report.stuck == [(date(2023, 6, 1), 25000)]

    def test_a_run_with_an_unreportable_population_exits_non_zero(self, monkeypatch):
        monkeypatch.setattr(sampler, "_count", lambda term, base: None)

        assert sampler.report_partitions([date(2024, 1, 1)], {}) is False

    def test_the_sampler_does_not_import_the_rule_it_measures(self):
        # A corpus labelled by the rule under test can only confirm that rule.
        source = Path(sampler.__file__).read_text()
        assert "_plan_partitions" not in source
        assert "_edat_range_term" not in source


class TestReportPartitionsPrintsAndCounts:
    """The reporting half: what gets printed, and what fails the run."""

    def test_an_exact_untiled_day_is_reported_exact(self, monkeypatch, capsys):
        monkeypatch.setattr(sampler, "_count", _stub_count({date(2023, 6, 1): 5_000}))

        assert sampler.report_partitions([date(2024, 1, 1)], {}) is True
        out = capsys.readouterr().out

        assert "1 measured, 0 unmeasured" in out
        assert "EXACT" in out
        assert "STUCK" not in out

    def test_a_stuck_day_fails_the_run_even_though_it_was_measured(self, monkeypatch, capsys):
        monkeypatch.setattr(sampler, "_count", _stub_count({date(2023, 6, 1): 25_000}))

        assert sampler.report_partitions([date(2024, 1, 1)], {}) is False
        out = capsys.readouterr().out

        assert "STUCK" in out

    def test_a_population_mostly_unmeasured_reports_no_ladder(self, monkeypatch, capsys):
        """One day's ladder walks clean; the other four never answer at all.

        4 of 5 unmeasured is past `UNMEASURED_SHARE_ERROR_THRESHOLD` (0.20),
        so no ladder is reported even though one day's own walk was fine.
        """
        working = _stub_count({date(2023, 6, 1): 5_000})

        def flaky(term: str, base: dict[str, str]) -> int | None:
            # `_range_term` embeds `day` in every term it builds for that day,
            # bare or ranged, so this routes every count for 2024-01-01 to the
            # working stub and leaves every other day's first count unanswered.
            return working(term, base) if "2024/01/01" in term else None

        monkeypatch.setattr(sampler, "_count", flaky)
        days = [
            date(2024, 1, 1),
            date(2023, 1, 1),
            date(2022, 1, 1),
            date(2021, 1, 1),
            date(2020, 1, 1),
        ]

        assert sampler.report_partitions(days, {}) is False
        out = capsys.readouterr().out

        assert "ERROR" in out
        assert "went unmeasured" in out

    def test_a_wholly_unreportable_population_prints_an_error(self, monkeypatch, capsys):
        monkeypatch.setattr(sampler, "_count", lambda term, base: None)

        assert sampler.report_partitions([date(2024, 1, 1)], {}) is False
        out = capsys.readouterr().out

        assert "ERROR — nothing measured" in out


class TestThePartitionFlagInMain:
    """`--partition` is wired into `main()` and folds into its exit status."""

    def test_the_flag_runs_the_ladder_instead_of_the_ordinary_probes(self, monkeypatch, capsys):
        out = _run(monkeypatch, capsys, _FakeEUtils(), "--partition", "--partition-days", "2")

        assert "Entrez-date ladder (2 days)" in out
        assert "session holds" not in out  # the session-opening bootstrap is skipped

    def test_a_clean_partition_run_exits_zero(self, monkeypatch):
        assert _status(monkeypatch, _FakeEUtils(), "--partition", "--partition-days", "1") == 0

    def test_an_unmeasured_partition_run_exits_non_zero(self, monkeypatch):
        fake = _FakeEUtils(day_count=None)

        assert _status(monkeypatch, fake, "--partition", "--partition-days", "1") == 1
