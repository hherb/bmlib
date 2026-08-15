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

"""Reconcile what a fetcher's page walk delivered against what the source promised.

Every built-in fetcher learns a record count from its source before walking
pages — PubMed's ``<Count>``, OpenAlex's ``meta.count``, bioRxiv's
``messages[0].total`` — and none of them used to compare that promise against
what arrived (issue #88). A walk that stopped early therefore returned
``status="completed"``, ``sync()`` wrote the day to ``download_days`` as done,
and ``_days_needing_fetch()`` never offered that day again once it was in the
past and ``recheck_days`` was left at its default: the records are permanently
absent, with nothing logged above INFO.

This module is that comparison, in one place because three fetchers share the
shape and a rule split three ways drifts.

Three rules, deliberately different in kind:

*Stalled* — a page delivering nothing while the source's own count says
records remain. That is broken outright whatever the magnitude, so it carries
no threshold. It is also the only rule that catches a history session
expiring on the *last* page of a long walk.

*Unreconcilable* — records arrived but the source named no count to judge
them against. There is no threshold to apply and no way to tell a complete
walk from a truncated one, so the day cannot be claimed as complete: an
unverifiable success is the failure this module exists to prevent. A day that
delivered *nothing* against no count is the ordinary quiet day and passes
silently, which is what keeps bioRxiv's total-omitting quiet response working
(issue #88 follow-up; see ``fetchers/biorxiv.py``).

*Shortfall* — a walk that ran to its natural end and still came up short.
Here the sources are not exact, so a threshold is unavoidable, and it is a
floor rather than strict inequality for a reason that is easy to miss: a day
marked ``failed`` is re-offered on **every** later sync run, so failing on a
gap with a benign and permanent cause re-fetches that day forever, silently
growing with the date range. Known benign causes include a record withdrawn
between search and fetch and an index updated mid-walk.

``SHORTFALL_FAILURE_RATIO`` is a rule fixed *before* measurement, unlike the
allow-lists and log levels elsewhere in bmlib that were set from sampled
populations. It says only what can be argued without data: no benign cause
plausibly removes half a day's records. Issue #92 is the follow-up that
measures the real delivered-versus-promised distribution per source and
tightens it; until that runs, a reader must not take 0.5 as a measured value.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


class Reconciliation(NamedTuple):
    """What the comparison concluded, in the two forms a caller must act on.

    Both fields are ``None`` for a walk that delivered what was promised. At
    most one is ever set.
    """

    failure: str | None = None
    """Set when the day must be recorded ``failed``; use as ``FetchResult.error``."""

    note: str | None = None
    """Set when the day completes but came up short; use as ``FetchResult.note``.

    Returning this rather than only logging it is what lets a caller find a
    short day afterwards. A day may be missing nearly half its records on this
    path, and a log line is not a surface anything can query.
    """


# Fraction of the promised count below which a naturally-ended walk is treated
# as broken rather than merely short. Fixed before measurement — see the module
# docstring and issue #92. Exclusive: delivering exactly this fraction passes.
SHORTFALL_FAILURE_RATIO = 0.5


def reconcile_delivery(
    source: str,
    date_str: str,
    *,
    delivered: int,
    promised: int | None,
    stalled: bool = False,
) -> Reconciliation:
    """Judge a finished page walk against the count its source promised.

    Parameters
    ----------
    source:
        The source name, as it will appear in logs and in ``FetchResult``.
    date_str:
        The day being fetched, ISO-formatted, for the message.
    delivered:
        How many records the source actually handed over. This is what the
        *server* delivered, not what the fetcher chose to parse: PubMed's
        efetch returns ``<PubmedBookArticle>`` elements that the fetcher skips,
        so counting parsed records here would report a phantom shortfall on
        every day carrying a book chapter.
    promised:
        The record count the source itself reported for the day, or ``None``
        when it reported none. ``None`` is *not* the same as zero and must not
        be flattened into it: zero is a source saying "this day is empty",
        which a delivery of zero satisfies, while ``None`` is a source saying
        nothing at all, against which no delivery can be verified.
    stalled:
        True when a page delivered no records while *promised* was unmet.

    Returns
    -------
    Reconciliation
        ``failure`` set when the day must be recorded as failed; ``note`` set
        when it may complete but came up short; both ``None`` when the walk
        delivered what was promised.
    """
    if promised is None:
        if delivered <= 0:
            return Reconciliation()
        message = (
            f"{source} delivered {delivered} records for {date_str} but reported no count"
            " to reconcile them against, so the walk cannot be shown to have finished"
        )
        logger.error("%s", message)
        return Reconciliation(failure=message)

    if promised <= 0 or delivered >= promised:
        return Reconciliation()

    counted = f"{source} delivered {delivered} of {promised} records for {date_str}"

    if stalled:
        message = (
            f"{counted} and then returned an empty page, so the walk stopped short"
            " (an expired history session, or an index that moved under the walk)"
        )
        logger.error("%s", message)
        return Reconciliation(failure=message)

    if delivered < promised * SHORTFALL_FAILURE_RATIO:
        message = (
            f"{counted} — below the {SHORTFALL_FAILURE_RATIO:.0%} floor, so the walk is"
            " treated as truncated rather than short"
        )
        logger.error("%s", message)
        return Reconciliation(failure=message)

    note = (
        f"{counted}; recording the day as completed, since a shortfall this small has"
        " benign causes (a record withdrawn between search and fetch, an index updated"
        " mid-walk)"
    )
    logger.warning("%s", note)
    return Reconciliation(note=note)
