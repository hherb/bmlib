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

"""Tests for the shared fetcher delivery-reconciliation rule (issue #88)."""

from __future__ import annotations

import logging

from bmlib.publications.fetchers._reconcile import (
    SHORTFALL_FAILURE_RATIO,
    reconcile_delivery,
)


class TestTheFloorIsTheDocumentedValue:
    """The constant is cited as 0.5 in the docstring, CLAUDE.md and the manual."""

    def test_the_floor_is_one_half(self):
        """Pinned so tightening it cannot be a silent edit.

        Every other test here derives its boundary from the constant, so all
        of them would follow a change without complaint. CLAUDE.md says the
        value must not be tightened without first running issue #92's sampler;
        this is what makes ignoring that instruction fail a test.
        """
        assert SHORTFALL_FAILURE_RATIO == 0.5


class TestNothingToReconcile:
    """Cases where the source's own count gives nothing to check against."""

    def test_a_source_promising_nothing_is_not_a_shortfall(self):
        """A quiet day is a legitimate outcome, not a truncated walk."""
        assert reconcile_delivery("pubmed", "2024-01-15", delivered=0, promised=0).failure is None

    def test_a_complete_walk_reconciles(self):
        verdict = reconcile_delivery("pubmed", "2024-01-15", delivered=500, promised=500)
        assert verdict.failure is None
        assert verdict.note is None

    def test_over_delivery_is_not_a_shortfall(self):
        """A source may deliver more than it promised; only a shortfall is a defect.

        OpenAlex's ``meta.count`` is a snapshot taken when the first page was
        served, so an index that gains works mid-walk can legitimately yield
        more than the promise.
        """
        verdict = reconcile_delivery("openalex", "2024-01-15", delivered=501, promised=500)
        assert verdict.failure is None


class TestAnUnknownPromise:
    """``promised=None`` is a source that said nothing, not one that said zero."""

    def test_records_delivered_against_no_count_cannot_complete(self):
        """The absent-``total`` hole: reconciliation silently became a no-op.

        bioRxiv's first page can carry records and no ``total``. Flattened to
        ``0``, the promise is "met" by any delivery, so both rules switch off
        and a walk that then stops early completes — 100 of 250 preprints
        stored as a finished day.
        """
        verdict = reconcile_delivery("biorxiv", "2024-01-15", delivered=100, promised=None)
        assert verdict.failure is not None
        assert "100" in verdict.failure
        assert "no count" in verdict.failure

    def test_nothing_delivered_against_no_count_is_a_quiet_day(self):
        """The negative control that keeps bioRxiv's quiet day working.

        A quiet day omits ``total`` entirely, so it arrives here as ``None``
        with nothing delivered. Failing it would fail every quiet day on every
        run for the life of the installation.
        """
        verdict = reconcile_delivery("biorxiv", "2024-01-15", delivered=0, promised=None)
        assert verdict.failure is None
        assert verdict.note is None

    def test_an_unknown_promise_is_not_read_as_zero(self, caplog):
        """``None`` and ``0`` must reach different branches.

        Pinned separately because the bug was precisely a caller collapsing
        the two with ``records_total or 0``, which no assertion about the
        ``0`` case can catch.
        """
        with caplog.at_level(logging.ERROR, logger="bmlib.publications.fetchers._reconcile"):
            zero = reconcile_delivery("biorxiv", "2024-01-15", delivered=7, promised=0)
            unknown = reconcile_delivery("biorxiv", "2024-01-15", delivered=7, promised=None)
        assert zero.failure is None
        assert unknown.failure is not None


class TestAStalledWalk:
    """A page delivering nothing while the source says records remain."""

    def test_a_stalled_walk_fails_however_small_the_gap(self):
        """No magnitude rule applies: an empty page mid-walk is broken outright.

        This is the shape a history session expiring on the *last* page takes,
        which a ratio floor would never catch.
        """
        verdict = reconcile_delivery(
            "pubmed", "2024-01-15", delivered=4500, promised=5000, stalled=True
        )
        assert verdict.failure is not None
        assert "4500" in verdict.failure
        assert "5000" in verdict.failure

    def test_a_stalled_walk_that_delivered_everything_is_not_a_failure(self):
        """``stalled`` is only meaningful while the promise is unmet."""
        verdict = reconcile_delivery(
            "pubmed", "2024-01-15", delivered=500, promised=500, stalled=True
        )
        assert verdict.failure is None

    def test_a_stalled_walk_is_logged_at_error(self, caplog):
        """The level, not just the message.

        Its below-floor sibling has this assertion; without it here, the
        stalled branch could be downgraded to DEBUG and nothing would fail —
        a mutation that survived the suite before this test existed.
        """
        with caplog.at_level(logging.DEBUG, logger="bmlib.publications.fetchers._reconcile"):
            reconcile_delivery("pubmed", "2024-01-15", delivered=4500, promised=5000, stalled=True)
        assert [r.levelno for r in caplog.records] == [logging.ERROR]


class TestTheShortfallFloor:
    """The magnitude rule for a walk that ended naturally but came up short."""

    def test_a_walk_delivering_almost_nothing_fails(self):
        """OpenAlex's reproduced row: 1 work of 5,000, then no next_cursor."""
        verdict = reconcile_delivery("openalex", "2024-01-15", delivered=1, promised=5000)
        assert verdict.failure is not None
        assert "openalex" in verdict.failure
        assert "2024-01-15" in verdict.failure

    def test_a_small_shortfall_completes(self):
        """A record withdrawn between search and fetch must not fail the day.

        A failed day is re-offered by ``_days_needing_fetch()`` on every later
        run, so failing on a benign, permanent gap re-fetches that day for the
        rest of the installation's life.
        """
        assert (
            reconcile_delivery("pubmed", "2024-01-15", delivered=4999, promised=5000).failure
            is None
        )

    def test_exactly_the_floor_completes(self):
        """The floor is exclusive: the failure is *below* the ratio, not at it."""
        promised = 5000
        delivered = int(promised * SHORTFALL_FAILURE_RATIO)
        verdict = reconcile_delivery("pubmed", "2024-01-15", delivered=delivered, promised=promised)
        assert verdict.failure is None

    def test_just_below_the_floor_fails(self):
        promised = 5000
        delivered = int(promised * SHORTFALL_FAILURE_RATIO) - 1
        verdict = reconcile_delivery("pubmed", "2024-01-15", delivered=delivered, promised=promised)
        assert verdict.failure is not None


class TestWhatTheOperatorSees:
    """Both outcomes have to be legible in a log, not only in a return value."""

    def test_a_shortfall_that_fails_is_logged_at_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="bmlib.publications.fetchers._reconcile"):
            reconcile_delivery("openalex", "2024-01-15", delivered=1, promised=5000)
        assert "delivered 1 of 5000" in caplog.text

    def test_a_shortfall_that_completes_is_logged_at_warning(self, caplog):
        """Silence here is what made the bug invisible; the gap is still reported."""
        with caplog.at_level(logging.WARNING, logger="bmlib.publications.fetchers._reconcile"):
            verdict = reconcile_delivery("pubmed", "2024-01-15", delivered=4999, promised=5000)
        assert verdict.failure is None
        assert "delivered 4999 of 5000" in caplog.text

    def test_a_shortfall_that_completes_is_also_returned(self):
        """A log line is not a surface a caller can query afterwards.

        A day may be missing nearly half its records on this path and is never
        re-offered, so "which of my completed days came up short?" has to be
        answerable from the return value.
        """
        verdict = reconcile_delivery("pubmed", "2024-01-15", delivered=4999, promised=5000)
        assert verdict.note is not None
        assert "delivered 4999 of 5000" in verdict.note

    def test_a_complete_walk_says_nothing(self, caplog):
        """A negative control: the warning above must not fire for every day."""
        with caplog.at_level(logging.DEBUG, logger="bmlib.publications.fetchers._reconcile"):
            reconcile_delivery("pubmed", "2024-01-15", delivered=500, promised=500)
        assert caplog.text == ""

    def test_a_failure_carries_no_note(self):
        """The two channels are exclusive; a failed day is not also a short one."""
        verdict = reconcile_delivery("openalex", "2024-01-15", delivered=1, promised=5000)
        assert verdict.note is None

    def test_the_message_names_the_source_the_day_and_both_counts(self):
        verdict = reconcile_delivery("biorxiv", "2024-03-02", delivered=3, promised=250)
        assert verdict.failure is not None
        assert "biorxiv" in verdict.failure
        assert "2024-03-02" in verdict.failure
        assert "3" in verdict.failure
        assert "250" in verdict.failure
