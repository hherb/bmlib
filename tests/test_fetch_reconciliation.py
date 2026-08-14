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


class TestNothingToReconcile:
    """Cases where the source's own count gives nothing to check against."""

    def test_a_source_promising_nothing_is_not_a_shortfall(self):
        """A quiet day is a legitimate outcome, not a truncated walk."""
        assert reconcile_delivery("pubmed", "2024-01-15", delivered=0, promised=0) is None

    def test_a_complete_walk_reconciles(self):
        assert reconcile_delivery("pubmed", "2024-01-15", delivered=500, promised=500) is None

    def test_over_delivery_is_not_a_shortfall(self):
        """A source may deliver more than it promised; only a shortfall is a defect.

        OpenAlex's ``meta.count`` is a snapshot taken when the first page was
        served, so an index that gains works mid-walk can legitimately yield
        more than the promise.
        """
        assert reconcile_delivery("openalex", "2024-01-15", delivered=501, promised=500) is None


class TestAStalledWalk:
    """A page delivering nothing while the source says records remain."""

    def test_a_stalled_walk_fails_however_small_the_gap(self):
        """No magnitude rule applies: an empty page mid-walk is broken outright.

        This is the shape a history session expiring on the *last* page takes,
        which a ratio floor would never catch.
        """
        message = reconcile_delivery(
            "pubmed", "2024-01-15", delivered=4500, promised=5000, stalled=True
        )
        assert message is not None
        assert "4500" in message
        assert "5000" in message

    def test_a_stalled_walk_that_delivered_everything_is_not_a_failure(self):
        """``stalled`` is only meaningful while the promise is unmet."""
        assert (
            reconcile_delivery("pubmed", "2024-01-15", delivered=500, promised=500, stalled=True)
            is None
        )


class TestTheShortfallFloor:
    """The magnitude rule for a walk that ended naturally but came up short."""

    def test_a_walk_delivering_almost_nothing_fails(self):
        """OpenAlex's reproduced row: 1 work of 5,000, then no next_cursor."""
        message = reconcile_delivery("openalex", "2024-01-15", delivered=1, promised=5000)
        assert message is not None
        assert "openalex" in message
        assert "2024-01-15" in message

    def test_a_small_shortfall_completes(self):
        """A record withdrawn between search and fetch must not fail the day.

        A failed day is re-offered by ``_days_needing_fetch()`` on every later
        run, so failing on a benign, permanent gap re-fetches that day for the
        rest of the installation's life.
        """
        assert reconcile_delivery("pubmed", "2024-01-15", delivered=4999, promised=5000) is None

    def test_exactly_the_floor_completes(self):
        """The floor is exclusive: the failure is *below* the ratio, not at it."""
        promised = 5000
        delivered = int(promised * SHORTFALL_FAILURE_RATIO)
        assert (
            reconcile_delivery("pubmed", "2024-01-15", delivered=delivered, promised=promised)
            is None
        )

    def test_just_below_the_floor_fails(self):
        promised = 5000
        delivered = int(promised * SHORTFALL_FAILURE_RATIO) - 1
        assert (
            reconcile_delivery("pubmed", "2024-01-15", delivered=delivered, promised=promised)
            is not None
        )


class TestWhatTheOperatorSees:
    """Both outcomes have to be legible in a log, not only in a return value."""

    def test_a_shortfall_that_fails_is_logged_at_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="bmlib.publications.fetchers._reconcile"):
            reconcile_delivery("openalex", "2024-01-15", delivered=1, promised=5000)
        assert "delivered 1 of 5000" in caplog.text

    def test_a_shortfall_that_completes_is_logged_at_warning(self, caplog):
        """Silence here is what made the bug invisible; the gap is still reported."""
        with caplog.at_level(logging.WARNING, logger="bmlib.publications.fetchers._reconcile"):
            result = reconcile_delivery("pubmed", "2024-01-15", delivered=4999, promised=5000)
        assert result is None
        assert "delivered 4999 of 5000" in caplog.text

    def test_a_complete_walk_says_nothing(self, caplog):
        """A negative control: the warning above must not fire for every day."""
        with caplog.at_level(logging.DEBUG, logger="bmlib.publications.fetchers._reconcile"):
            reconcile_delivery("pubmed", "2024-01-15", delivered=500, promised=500)
        assert caplog.text == ""

    def test_the_message_names_the_source_the_day_and_both_counts(self):
        message = reconcile_delivery("biorxiv", "2024-03-02", delivered=3, promised=250)
        assert message is not None
        assert "biorxiv" in message
        assert "2024-03-02" in message
        assert "3" in message
        assert "250" in message
