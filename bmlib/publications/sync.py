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

"""Sync orchestrator — fetches publications from multiple sources and stores them.

Coordinates fetchers, deduplication, and download-day tracking across all
configured publication sources (PubMed, bioRxiv, medRxiv, OpenAlex).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, NamedTuple, TypeVar

from bmlib import __version__
from bmlib.db import execute, fetch_all, placeholder, transaction
from bmlib.publications.fetchers.registry import get_fetcher, get_source, source_names
from bmlib.publications.models import (
    AuthorAffiliation,
    DayStatus,
    FetchedRecord,
    FetchResult,
    FullTextSource,
    Grant,
    PartCheckpoint,
    Publication,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import store_publication

logger = logging.getLogger(__name__)

# Both carry ``source`` and are dataclasses, which is all _stamp_source needs.
# Bound rather than bare, so ``dataclasses.replace`` — which accepts only a
# dataclass instance — type-checks, and so the return type stays the caller's
# own type rather than widening to the union.
_ChildRow = TypeVar("_ChildRow", Grant, AuthorAffiliation)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT_SECONDS = 30.0

_DAY_ENDS_EVERYWHERE_AT_UTC_HOUR = 12
"""The hour on *D+1* at which day *D* is over in every timezone.

UTC-12 is the last zone to finish any calendar day, and its midnight is noon
UTC the following day. See :func:`_day_was_over_when_fetched`, which is the
only reader and carries the argument in full.
"""

_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
"""How far past "now" a stored ``downloaded_at`` may sit and still be believed.

A fetch cannot have happened in the future, so a timestamp beyond now is a
clock the rule cannot trust. The value is a fixed choice, not a measured one —
but unlike ``SHORTFALL_FAILURE_RATIO`` the choice is bounded on both sides by
an asymmetry rather than by taste: too tight costs one merged re-fetch of a
day that settles on the next run, while too loose reads an impossible claim as
durable and loses the day permanently. Five minutes is therefore generous
against ordinary host skew (NTP keeps hosts within milliseconds; an unsynced
host drifts seconds per day) and far tighter than any of the failures this
guards — see :func:`_day_was_over_when_fetched`.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_fetcher_for_source(source: str) -> Callable | None:
    """Return the registered fetcher for a source, or None if unknown."""
    try:
        return get_fetcher(source)
    except ValueError:
        return None


def _source_is_resumable(source: str) -> bool:
    """Whether *source*'s descriptor declares it accepts the resume keywords.

    An unknown source answers ``False`` rather than raising. Not because of
    call order — by the time :func:`sync` asks, it has already resolved the
    fetcher — but because a source supplied through ``_fetcher_override`` need
    not be registered at all, so :func:`get_source` raises for a source that
    nonetheless has a working fetcher. That raise would escape the per-day
    handler into a ``try`` carrying only a ``finally``, and lose the whole
    multi-source run's report.

    The *descriptor* decides, not the fetcher actually called — so overriding a
    resumable source's fetcher for a test supplies one that accepts the resume
    keywords, or its day fails.
    """
    try:
        descriptor, _ = get_source(source)
    except ValueError:
        return False
    return bool(descriptor.resumable)


def _read_aware_timestamp(value: object) -> datetime | None:
    """Return *value* as a timezone-aware datetime, or ``None`` if it is not one.

    Separate from its caller so that "unusable" is one answer rather than
    three: a non-string, an unparseable string and a naive timestamp all mean
    the same thing to the durability rule, and the naive case in particular
    must not reach a comparison — ``aware >= naive`` raises ``TypeError``,
    which would abort a whole sync from inside day selection.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _day_was_over_when_fetched(source: str, day: date, downloaded_at: object) -> bool:
    """Had *day* already ended everywhere on earth when this row was written?

    This is what makes a stored ``completed`` day durable, and issue #95 is
    what happens without it. ``sync()``'s default window is
    ``[yesterday, today]``, so a 09:00 cron captured today as it stood at
    09:00 and recorded it done; tomorrow that day is neither ``today`` nor
    ``failed``, so at the documented default ``recheck_days=0`` it was never
    offered again and the remaining 15 hours of indexing were permanently
    absent. Nothing in :mod:`~bmlib.publications.fetchers._reconcile` can
    catch it: the source's own count agreed at 09:00, because the walk really
    did deliver everything that existed then.

    The boundary is **12:00 UTC on the following day**, and the hour is not a
    safety margin. Day *D* finishes last in UTC-12, whose midnight is noon UTC
    on *D+1*; equally, that instant is exactly the point beyond which "now"
    can no longer fall inside day *D* anywhere on earth. The second reading is
    why this rule *subsumes* the ``if current == today`` special case it
    replaces rather than approximating it — a row written during today is
    always earlier than its own boundary, in every timezone — and why the wall
    clock no longer *decides* whether a completed day is done. It is still
    read, but only as the upper bound below, which can move the answer towards
    a re-fetch and never away from one.

    The two obvious cheaper rules are both unsafe, and not hypothetically:
    all three built-in sources are US-based (UTC-5 to UTC-8), so comparing
    UTC *dates* would call a fetch at 00:30 UTC on *D+1* durable while
    PubMed's own day *D* still had four and a half hours to run (US Eastern in
    winter; three and a half on daylight time, and longer for a Pacific-time
    source), and comparing *local* dates is worse still — up to 16 hours out
    for a machine in Sydney.

    What this does **not** fix is late indexing: a record that appears for day
    *D* three days later is not covered by any rule about when *D* ended, and
    ``recheck_days`` is what exists for it.

    A timestamp that cannot be *read* fails closed and says so.
    ``downloaded_at`` is ``NOT NULL TEXT`` and bmlib has only ever written an
    aware UTC ISO value, so anything else came from elsewhere; reading it as
    durable would lose the day permanently, while the re-fetch it costs is
    merged by ``store_publication()`` and rewrites the column, so the row heals
    itself.

    A timestamp that reads cleanly but cannot be *true* fails closed for the
    same reason. A fetch cannot have happened in the future, so a value beyond
    now — a restored backup, a host resuming with a bad RTC, an external
    writer — is rejected rather than believed. Without that bound the guard is
    loud about a value it cannot parse and silent about one asserting the day
    was fetched tomorrow, which is #95's own failure mode: permanent,
    invisible loss. See :data:`_CLOCK_SKEW_TOLERANCE`.

    Parameters
    ----------
    source:
        The source name, for the warnings only.
    day:
        The day the row describes.
    downloaded_at:
        The row's stored ``downloaded_at``. Typed ``object`` because it
        arrives from a DB-API row as ``Any`` and the guard below is what
        makes it a string.

    Returns
    -------
    bool
        True only if the fetch is known to have happened after *day* was over
        everywhere.
    """
    fetched_at = _read_aware_timestamp(downloaded_at)
    if fetched_at is None:
        logger.warning(
            "download_days row for %s/%s carries an unusable downloaded_at (%r);"
            " re-fetching the day rather than reading it as complete",
            source,
            day.isoformat(),
            downloaded_at,
        )
        return False

    now = datetime.now(tz=UTC)
    if fetched_at > now + _CLOCK_SKEW_TOLERANCE:
        logger.warning(
            "download_days row for %s/%s claims a downloaded_at in the future"
            " (%s, now %s); re-fetching the day rather than reading it as complete",
            source,
            day.isoformat(),
            fetched_at.isoformat(),
            now.isoformat(),
        )
        return False

    day_over_everywhere = datetime.combine(
        day + timedelta(days=1),
        time(hour=_DAY_ENDS_EVERYWHERE_AT_UTC_HOUR),
        tzinfo=UTC,
    )
    return fetched_at >= day_over_everywhere


def _read_verification_date(source: str, day: date, value: object) -> date | None:
    """Return *value*'s calendar date, or ``None`` if it cannot be read.

    The companion to :func:`_read_aware_timestamp` for ``last_verified_at``,
    and deliberately laxer: only the calendar date is used, so a naive value is
    perfectly usable here where it is not for the durability rule. Routing this
    column through the aware-only guard instead would fail closed on every
    naive row and re-fetch the whole window on every run for a ``recheck_days``
    caller.

    What the two share is why they exist. Read raw, this column raises
    ``ValueError`` from inside day selection, which escapes ``sync()`` — whose
    ``try`` carries only a ``finally`` — and kills the whole multi-source run
    before a single record is fetched, losing the ``SyncReport`` with it. That
    is worse than the per-day losses this module's other rules guard against,
    because it is total rather than per-day.

    A stored ``NULL`` is not unusable: it is the documented "never verified"
    state, which rule 4 of :func:`_days_needing_fetch` already answers by
    rechecking. It returns ``None`` without a warning, since both answers lead
    to the same place.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            pass
    logger.warning(
        "download_days row for %s/%s carries an unusable last_verified_at (%r);"
        " rechecking the day rather than reading it as recently verified",
        source,
        day.isoformat(),
        value,
    )
    return None


def _require_plain_date(value: object, field_name: str) -> None:
    """Refuse anything but a ``date`` that is not also a ``datetime``.

    ``datetime`` subclasses ``date``, so ``sync(date_to=datetime.now())``
    satisfies the annotation and every type checker, and no value-based guard
    below can see it: ``datetime.max == date.max`` is ``False``. Mistaking
    ``datetime.now()`` for ``date.today()`` is a far likelier slip than any
    input #99 originally named, and it fails in two shapes. Mixed with a
    ``date`` it raises ``TypeError`` from the comparison in
    :func:`_days_needing_fetch` and loses the whole run's report. On *both*
    ends it raises nothing at all: the walk succeeds and writes
    ``download_days.date`` values carrying a time component, which no
    date-keyed lookup can ever match — so the day is re-fetched for the life
    of the installation and the table fills with rows nothing reads. The
    silent shape is the reason this is a type check and not a value check.

    Parameters
    ----------
    value:
        The caller's value. Typed ``object`` because the annotation is
        exactly what cannot be trusted here.
    field_name:
        The parameter being validated, interpolated into the message.

    Raises
    ------
    ValueError
        Naming *field_name* and what arrived instead.
    """
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(
            f"{field_name} must be a datetime.date, got {type(value).__name__}"
            " — note that a datetime is not one for this purpose: it satisfies"
            " the annotation and then writes a download_days.date no lookup matches"
        )


def _validate_window(date_from: object, date_to: object, recheck_days: object) -> None:
    """Refuse a window or recheck depth that day selection cannot walk.

    Two kinds of rejection, and they fail differently. The **type** checks
    catch a value that is not a day or a whole number of days at all; the
    **range** checks catch one that is, but lies outside the calendar. Both
    reach date arithmetic inside :func:`_days_needing_fetch`, and what
    escapes from there — ``OverflowError`` for a range, ``TypeError`` or
    ``AttributeError`` for a type — takes the whole multi-source run with it,
    because ``sync()``'s ``try`` carries only a ``finally``. The
    ``SyncReport`` is lost before a single record is fetched, for every
    source rather than for one day. That is worse in kind than the per-day
    losses the rest of this module guards against, because it is total (#99).

    Only one of these is not an exception in the first place: a **negative**
    ``recheck_days`` walked fine and was swallowed by ``recheck_days > 0``,
    delivering "recheck nothing" to a caller who asked for the opposite,
    without a word. ``float('nan')`` reached the same silence through both
    range checks, since every comparison against it is ``False``. Those are
    rejected as caller bugs of the same family, not as arithmetic hazards.

    Validated once at the public entry, deliberately **not** caught at the
    helpers: an ``except OverflowError`` around the arithmetic would convert
    a caller bug into a day that quietly looks like it needs no fetch, which
    is the failure mode the whole #88-#95 family exists to remove.

    What is deliberately **not** rejected is an *empty* window
    (``date_from > date_to``). That is what the ordinary incremental-sync
    idiom produces once it has caught up — ``date_from = last_synced + 1
    day``, ``date_to = today`` — so raising would turn a caller that is
    simply up to date into a crashing one. It writes no row and claims no
    day, which is what separates it from the rejections below. A window
    reaching into the *future* is likewise accepted, and reported instead —
    see :func:`_note_unreachable_days`.

    Parameters
    ----------
    date_from:
        Start of the window, already defaulted by :func:`sync`.
    date_to:
        End of the window, already defaulted by :func:`sync`.
    recheck_days:
        The caller's recheck depth.

    Raises
    ------
    ValueError
        Naming the offending parameter.
    """
    _require_plain_date(date_from, "date_from")
    _require_plain_date(date_to, "date_to")
    if not isinstance(recheck_days, int):
        raise ValueError(
            f"recheck_days must be a whole number of days, got {type(recheck_days).__name__}"
            " — a float slips both range checks below, since every comparison"
            " against nan is False, and then silently disables rechecking"
        )

    if date_to == date.max:
        raise ValueError(
            f"date_to must be earlier than {date.max.isoformat()}: day selection asks"
            " which day follows the last day of the window, and there is none"
        )
    if recheck_days < 0:
        raise ValueError(f"recheck_days must not be negative, got {recheck_days}")
    days_since_date_min = (date.today() - date.min).days
    if recheck_days > days_since_date_min:
        raise ValueError(
            f"recheck_days must not reach back before {date.min.isoformat()}:"
            f" got {recheck_days}, and today is only {days_since_date_min} days after it"
        )


def _note_unreachable_days(date_to: date) -> str | None:
    """Report a window ending in the future, which can never complete.

    :func:`_day_was_over_when_fetched` requires a fetch at or after 12:00 UTC
    on the day *after* the day it describes, which for a day that has not
    happened is unsatisfiable. So each future day is stored ``completed`` and
    re-offered on every subsequent run, for the life of the installation —
    a permanent cost that was reported at no log level and in no field of the
    ``SyncReport``. Invisible and permanent is the pair this module's other
    rules exist to break up.

    Rejecting the window was considered and refused. The past half of a
    window ending tomorrow is perfectly fetchable, and raising would discard
    it along with the unreachable half; the ordinary caller passing
    ``date_to=today`` must also stay unaffected across a midnight rollover.
    Returning a note is what :attr:`SyncReport.notes` already exists for —
    the same choice the shortfall rule makes for a gap too small to fail on.

    Returns
    -------
    str | None
        The note, or ``None`` when the window ends today or earlier.
    """
    today = date.today()
    if date_to <= today:
        return None
    return (
        f"date_to is {(date_to - today).days} day(s) in the future ({date_to.isoformat()});"
        " a day that has not ended cannot be recorded durably, so those days"
        " will be re-fetched on every run until they are past"
    )


def _days_needing_fetch(
    conn: Any,
    source: str,
    *,
    date_from: date,
    date_to: date,
    recheck_days: int = 0,
) -> list[date]:
    """Determine which days need fetching for a source.

    Rules, each failing closed — an uncertain day costs a re-fetch, which
    ``store_publication()`` merges, while a day wrongly called done is
    permanently missing:

    1. No row at all: include.
    2. A row whose status is anything but ``"completed"``: include.
    3. A completed row whose fetch cannot be shown to have happened after the
       day was over everywhere: include. See
       :func:`_day_was_over_when_fetched`, which replaced an unconditional
       "today is always re-fetched" branch — it re-offered today and nothing
       else, so a day captured *as* today was stored done and never revisited
       (#95).
    4. If *recheck_days* > 0 and ``last_verified_at`` is older than that many
       days, absent, or unreadable: include.

    Rule 3 costs exactly one extra day-fetch per run under the default window
    ``[yesterday, today]`` — two rather than one — because day *D* is offered
    once more on *D+1*, which is the point. A caller passing a window of three
    days or more, whose run happens before 12:00 UTC, pays one more again
    (three); the cost does not grow with the window beyond that, and vanishes
    entirely for a run at or after 12:00 UTC. On the *first* run after
    upgrading it is larger and one-off: every row stored by the old code was
    written while its own day was current, so none of them is durable and the
    whole window is re-fetched once.

    **Preconditions.** *date_from*, *date_to* and *recheck_days* are assumed
    already validated — they must be plain ``date`` objects (not
    ``datetime``, which subclasses it) and a whole number of days inside the
    calendar. :func:`sync` guarantees that at its entry via
    :func:`_validate_window`, and this function does not re-check: an
    ``except OverflowError`` here would convert a caller bug into a day that
    quietly looks like it needs no fetch, which is the wrong shape of fix.
    Called directly with unvalidated values, the loop below raises
    ``OverflowError``, ``TypeError`` or ``AttributeError``, and a ``datetime``
    on *both* ends does something worse than raise — see
    :func:`_require_plain_date`.

    Parameters
    ----------
    conn:
        A DB-API connection with the publications schema.
    source:
        The source name (e.g. "pubmed", "biorxiv").
    date_from:
        Start of the date range (inclusive).
    date_to:
        End of the date range (inclusive).
    recheck_days:
        If > 0, re-fetch days whose last_verified_at is older than this many
        days. See **Preconditions** above.

    Returns
    -------
    list[date]
        Dates that need fetching, sorted ascending.
    """
    today = date.today()
    ph = placeholder(conn)

    # Every stored row for this source in range, whatever its status — which
    # of them counts as done is decided below, not by the query.
    rows = fetch_all(
        conn,
        "SELECT date, status, downloaded_at, last_verified_at FROM download_days"
        f" WHERE source = {ph} AND date >= {ph} AND date <= {ph}",
        (source, date_from.isoformat(), date_to.isoformat()),
    )

    rows_by_day: dict[str, Any] = {}
    for row in rows:
        rows_by_day[row["date"]] = row

    needed: list[date] = []
    current = date_from
    while current <= date_to:
        if current.isoformat() not in rows_by_day:
            # No row at all — needs fetch
            needed.append(current)
        else:
            entry = rows_by_day[current.isoformat()]
            if entry["status"] != "completed":
                # Anything that is not a recorded success is retried. Read as
                # a denylist (`== "failed"`) this is the mirror of the write
                # bug `_resolve_day_status` fixes: a status in any other
                # spelling counted as done, so a day that never succeeded was
                # never offered again. Fails closed — an unrecognised status
                # costs a re-fetch, which `store_publication` merges.
                needed.append(current)
            elif not _day_was_over_when_fetched(source, current, entry["downloaded_at"]):
                # The day was still running somewhere when it was fetched, so
                # what it delivered cannot be the whole day (#95).
                needed.append(current)
            elif recheck_days > 0:
                # Absent, unreadable and stale are one answer: none of them
                # shows the day was verified inside the window, and reading
                # the column raw raises from inside day selection, which
                # aborts the whole run rather than costing one merged day.
                last_verified = _read_verification_date(source, current, entry["last_verified_at"])
                if last_verified is None or last_verified < today - timedelta(days=recheck_days):
                    needed.append(current)
        current += timedelta(days=1)

    return needed


def _record_to_publication(record: FetchedRecord) -> Publication:
    """Convert a :class:`FetchedRecord` to a :class:`Publication`."""
    return Publication(
        title=record.title,
        doi=record.doi,
        pmid=record.pmid,
        pmcid=record.pmc_id,
        abstract=record.abstract,
        authors=record.authors,
        journal=record.journal,
        publication_date=record.publication_date,
        publication_types=record.publication_types,
        keywords=record.keywords,
        is_open_access=record.is_open_access,
        license=record.license,
        sources=[record.source],
        first_seen_source=record.source,
    )


def _stamp_source(rows: Sequence[_ChildRow], source: str) -> list[_ChildRow]:
    """Return copies of *rows* whose ``source`` is the record's own.

    Provenance is stamped here rather than in each fetcher because this is the
    one place that authoritatively knows which source produced the record — a
    fetcher can forget, and the cost of forgetting is silent: rows land in an
    unnamed bucket and stop being scoped, which is the cross-source
    flip-flopping that ``source`` exists to prevent. Whatever a fetcher may
    have set is overwritten, which is correct: a row's provenance *is* the
    source that reported the record carrying it.
    """
    return [replace(row, source=source) for row in rows]


def _record_to_fulltext_sources(record: FetchedRecord) -> list[FullTextSource] | None:
    """Extract :class:`FullTextSource` objects from a :class:`FetchedRecord`."""
    if not record.fulltext_sources:
        return None
    result = []
    for fts in record.fulltext_sources:
        # Fetchers populate ``fulltext_sources`` with ``FullTextSourceEntry``
        # dataclass instances; accept plain dicts too for robustness.
        if isinstance(fts, dict):
            url = fts.get("url")
            source = fts.get("source", "unknown")
            fmt = fts.get("format", "html")
            version = fts.get("version")
        else:
            url = getattr(fts, "url", None)
            source = getattr(fts, "source", "unknown")
            fmt = getattr(fts, "format", "html")
            version = getattr(fts, "version", None)
        if not url:
            continue
        result.append(
            FullTextSource(
                publication_id=0,  # will be set by store_publication
                source=source,
                url=url,
                format=fmt,
                version=version,
            )
        )
    return result if result else None


def _store_records(
    conn: Any, source: str, day: date, records: Sequence[FetchedRecord]
) -> tuple[int, int, int]:
    """Store *records*, returning ``(added, merged, failed)``.

    Runs inside the caller's transaction, so a record that fails rolls back to
    its own savepoint without losing the batch. Shared by the two places a
    day's buffer is drained: once per finished part for a resumable source,
    and once at the end of the day for whatever is left (see :func:`sync`).
    """
    added = merged = failed = 0
    for record in records:
        try:
            pub = _record_to_publication(record)
            fts = _record_to_fulltext_sources(record)
            result = store_publication(
                conn,
                pub,
                fulltext_sources=fts,
                grants=_stamp_source(record.grants, record.source),
                affiliations=_stamp_source(record.author_affiliations, record.source),
            )
            if result == "added":
                added += 1
            elif result == "merged":
                merged += 1
        except Exception as exc:
            # Broad on purpose — one bad record must not lose the batch — so
            # the exception *type* is logged too: a TypeError here is a bmlib
            # defect affecting every record, not bad data from the source, and
            # the two read identically without it.
            failed += 1
            logger.error(
                "Failed to store record from %s/%s: %s: %s",
                source,
                day.isoformat(),
                type(exc).__name__,
                exc,
            )
    return added, merged, failed


class _DayPartsUnreadableError(Exception):
    """This day's stored part rows could not be read.

    Raised inside the per-day handler purely so one ``except`` records the
    day, rather than a second copy of that block existing for this case. The
    message is built at the read, where the cause is still in hand.
    """


class _DayOutcome(NamedTuple):
    """What to store for a day, and what to tell the caller about it."""

    status: DayStatus
    errors: list[str]
    """Lines for :class:`SyncReport`'s ``errors`` — days that will be retried."""
    notes: list[str]
    """Lines for :class:`SyncReport`'s ``notes`` — days that will not be."""


def _resolve_day_status(
    source: str,
    day: date,
    fetch_result: FetchResult,
    day_failed: int,
) -> _DayOutcome:
    """Decide what to store for a day, and what to tell the caller about it.

    Both failure modes here used to be recorded as ``completed``, which is
    durable: ``_days_needing_fetch()`` does not offer a completed day again
    once it is in the past *and was fetched after the day was over* (see
    :func:`_day_was_over_when_fetched`), unless ``recheck_days`` is set — so
    the records are, for the default configuration, permanently absent.

    The return type is :data:`DayStatus` rather than ``str`` deliberately. This
    function exists to turn an unvalidated status into a validated one, so
    handing back the type it consumed would leave nothing downstream able to
    tell the two apart; ``_upsert_download_day`` takes the narrowed type for
    the same reason, which makes writing an unrecognised status into
    ``download_days`` a type error rather than a silent permanent loss.
    ``FetchResult.status`` stays a bare ``str`` on purpose — it is a boundary
    value arriving from a public extension point, and narrowing it would both
    break third-party fetchers under their own type checker and remove the
    reason the runtime check below exists.

    Two rules, both failing closed:

    *Unknown status* — the convention is ``"completed"`` or ``"failed"``, but
    it was enforced by a denylist (anything not exactly ``"failed"`` became
    ``completed``), so a fetcher reporting failure in any other spelling had
    that failure converted into success. ``register_source()`` is a documented
    extension point, and a third-party fetcher is exactly the caller who will
    not know the convention.

    *Records that failed to store* — a day whose records raised on the way in
    is missing them by name. ``store_publication`` merges, so re-fetching is
    idempotent and the retry is cheap.
    """
    errors: list[str] = []
    notes: list[str] = []
    status: DayStatus

    # Spelled as an allowlist rather than `if status not in (...)`, because a
    # membership test does not narrow `str` to the Literal and so cannot be
    # checked; this shape makes the vocabulary the type system's business.
    if fetch_result.status == "completed":
        status = "completed"
    elif fetch_result.status == "failed":
        status = "failed"
    else:
        logger.error(
            "Fetcher for %s/%s returned unknown status %r; recording the day as failed",
            source,
            day.isoformat(),
            fetch_result.status,
        )
        errors.append(
            f"{source}/{day.isoformat()}: fetcher returned unknown status"
            f" {fetch_result.status!r}; recorded as failed"
        )
        status = "failed"

    if day_failed:
        errors.append(f"{source}/{day.isoformat()}: {day_failed} record(s) failed to store")
        status = "failed"

    # Only meaningful on a day that completes: a note on a failed day describes
    # a walk that is about to be retried anyway.
    if fetch_result.note and status == "completed":
        notes.append(f"{source}/{day.isoformat()}: {fetch_result.note}")

    return _DayOutcome(status=status, errors=errors, notes=notes)


def _upsert_download_day(
    conn: Any,
    source: str,
    day: date,
    status: DayStatus,
    record_count: int,
) -> None:
    """Insert or update a download_days row.

    Runs inside the caller's per-day transaction (see :func:`sync`), so the
    day's status commits atomically with the day's records; commits itself
    only when called with no transaction open.
    """
    day_str = day.isoformat()
    now = datetime.now(tz=UTC).isoformat()
    ph = placeholder(conn)
    with transaction(conn):
        execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at,"
            f" last_verified_at) VALUES ({', '.join([ph] * 6)})"
            " ON CONFLICT (source, date) DO UPDATE SET"
            "   status = excluded.status,"
            "   record_count = excluded.record_count,"
            "   downloaded_at = excluded.downloaded_at,"
            "   last_verified_at = excluded.last_verified_at",
            (source, day_str, status, record_count, now, now),
        )


def _load_day_parts(conn: Any, source: str, day: date) -> dict[str, PartCheckpoint]:
    """Return the parts of *day* a previous run finished, keyed by part key."""
    ph = placeholder(conn)
    rows = fetch_all(
        conn,
        "SELECT part_scheme, part_key, promised, record_count FROM download_day_parts"
        f" WHERE source = {ph} AND date = {ph}",
        (source, day.isoformat()),
    )
    parts = {}
    for row in rows:
        # `fetch_all` hands back a driver row — `sqlite3.Row` here, psycopg2's
        # `RealDictRow` there — and only the second is a `Mapping`.
        # `PartCheckpoint.from_dict` takes one, so the conversion happens here
        # rather than the model widening its contract to the union of two
        # drivers' row types. Both support `keys()` and string indexing.
        cp = PartCheckpoint.from_dict({key: row[key] for key in row.keys()})
        parts[cp.part_key] = cp
    return parts


def _record_day_part(conn: Any, source: str, day: date, checkpoint: PartCheckpoint) -> None:
    """Record one completed part.

    Runs inside the caller's transaction (see :func:`sync`), so the checkpoint
    commits atomically with the records it attests to — a checkpoint that
    outlived a rolled-back batch would make a re-run skip records that were
    never stored.
    """
    ph = placeholder(conn)
    with transaction(conn):
        execute(
            conn,
            "INSERT INTO download_day_parts (source, date, part_scheme, part_key,"
            f" promised, record_count, completed_at) VALUES ({', '.join([ph] * 7)})"
            " ON CONFLICT (source, date, part_key) DO UPDATE SET"
            "   part_scheme = excluded.part_scheme,"
            "   promised = excluded.promised,"
            "   record_count = excluded.record_count,"
            "   completed_at = excluded.completed_at",
            (
                source,
                day.isoformat(),
                checkpoint.part_scheme,
                checkpoint.part_key,
                checkpoint.promised,
                checkpoint.record_count,
                datetime.now(tz=UTC).isoformat(),
            ),
        )


def _clear_day_parts(conn: Any, source: str, day: date) -> None:
    """Drop *day*'s part rows.

    Called when the day completes: the rows describe an unfinished day, so
    keeping them would grow the table without bound and would make a
    ``recheck_days`` re-fetch skip parts it was explicitly asked to redo.
    """
    ph = placeholder(conn)
    with transaction(conn):
        execute(
            conn,
            f"DELETE FROM download_day_parts WHERE source = {ph} AND date = {ph}",
            (source, day.isoformat()),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_source_configs(
    source_configs: dict[str, dict[str, Any]] | None,
    email: str,
    api_keys: dict[str, str] | None,
) -> dict[str, dict[str, Any]]:
    """Merge legacy ``email``/``api_keys`` params into a ``source_configs`` dict."""
    if source_configs is not None:
        return source_configs

    configs: dict[str, dict[str, Any]] = {}
    if api_keys:
        for src, key in api_keys.items():
            configs.setdefault(src, {})["api_key"] = key
    if email:
        configs.setdefault("openalex", {})["email"] = email
    return configs


def sync(
    conn: Any,
    *,
    sources: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email: str = "",
    api_keys: dict[str, str] | None = None,
    source_configs: dict[str, dict[str, Any]] | None = None,
    on_record: Callable[[FetchedRecord], None] | None = None,
    on_progress: Callable[[SyncProgress], None] | None = None,
    recheck_days: int = 0,
    _fetcher_override: dict[str, Callable] | None = None,
) -> SyncReport:
    """Orchestrate syncing publications from multiple sources.

    Parameters
    ----------
    conn:
        A DB-API connection.
    sources:
        Source names to sync.  Defaults to all registered sources.
    date_from:
        Start date (inclusive).  Defaults to yesterday.
    date_to:
        End date (inclusive).  Defaults to today.
    email:
        Contact email for polite API access (legacy; prefer *source_configs*).
    api_keys:
        Dict mapping source names to API keys (legacy; prefer *source_configs*).
    source_configs:
        Dict mapping source names to config dicts.  Each config dict is
        unpacked as ``**kwargs`` when calling the fetcher.  Supersedes
        *email* and *api_keys* when provided.
    on_record:
        Optional callback invoked with each :class:`FetchedRecord` as the
        fetcher streams it — *before* the record is stored, so the callback
        must not expect to read the record back from the database. Records
        are stored in one batch per day after the fetch completes.
    on_progress:
        Optional callback invoked with progress updates.
    recheck_days:
        If > 0, re-fetch completed days older than this many days.
    _fetcher_override:
        Dict mapping source names to callable fetchers (for testing).

    Returns
    -------
    SyncReport
        Summary of the sync operation.

    Raises
    ------
    ValueError
        If *date_from*, *date_to* or *recheck_days* is not the type day
        selection can walk, or is outside the range it can walk — see
        :func:`_validate_window`, which also says why an *empty* window
        (*date_from* after *date_to*) is accepted rather than rejected, and
        :func:`_note_unreachable_days` for why a *future* window is accepted
        and reported instead.
    """
    ensure_schema(conn)

    today = date.today()
    if sources is None:
        sources = list(source_names())
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = today - timedelta(days=1)

    # Before the HTTP client is built, so a caller bug cannot leak one: the
    # client is created outside the try whose finally closes it, so a raise
    # between the two would strand the connection pool. And before any source
    # is touched — the alternative is an exception out of day selection that
    # takes the whole run's SyncReport with it (#99). ensure_schema() has
    # already run, which is idempotent DDL any valid call would perform.
    _validate_window(date_from, date_to, recheck_days)

    resolved_configs = _build_source_configs(source_configs, email, api_keys)

    # Create HTTP client only if using real fetchers
    client: Any = None
    if _fetcher_override is None:
        import httpx

        user_agent_email = resolved_configs.get("openalex", {}).get("email", email) or "unknown"
        client = httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/{__version__} (mailto:{user_agent_email})"},
        )

    total_added = 0
    total_merged = 0
    total_failed = 0
    total_days = 0
    errors: list[str] = []
    notes: list[str] = []
    sources_synced: list[str] = []

    unreachable = _note_unreachable_days(date_to)
    if unreachable is not None:
        logger.warning("%s", unreachable)
        notes.append(unreachable)

    try:
        for source in sources:
            if _fetcher_override is not None:
                fetcher = _fetcher_override.get(source)
            else:
                fetcher = _get_fetcher_for_source(source)

            if fetcher is None:
                errors.append(f"No fetcher found for source: {source}")
                continue

            days = _days_needing_fetch(
                conn,
                source,
                date_from=date_from,
                date_to=date_to,
                recheck_days=recheck_days,
            )

            if not days:
                sources_synced.append(source)
                continue

            src_config = resolved_configs.get(source, {})

            resumable = _source_is_resumable(source)

            for day in days:
                day_added = 0
                day_merged = 0
                day_failed = 0
                day_records: list[FetchedRecord] = []
                prior_parts: dict[str, PartCheckpoint] = {}
                parts_error: str | None = None
                if resumable:
                    try:
                        prior_parts = _load_day_parts(conn, source, day)
                    except Exception as exc:
                        # Guarded for the reason `_source_is_resumable` is,
                        # one call earlier: this runs *before* the per-day
                        # handler below, and the source loop carries only a
                        # `finally`, so anything raised here leaves `sync()`
                        # without returning a `SyncReport` at all — every
                        # source's work in the run becomes unreportable (#99).
                        #
                        # It fails the day rather than proceeding with no
                        # checkpoints. Fetching a day from scratch would be
                        # correct, but recording it `completed` on a run that
                        # could not read what an earlier run had already
                        # stored is recording success over an unknown, and
                        # `completed` is never re-offered. A failed day is,
                        # and `store_publication` merges, so the retry is
                        # idempotent.
                        parts_error = (
                            "could not read download_day_parts for"
                            f" {source}/{day.isoformat()}: {type(exc).__name__}: {exc}"
                        )
                        logger.error("%s", parts_error)
                skipped_keys: set[str] = set()

                def handle_record(record: FetchedRecord) -> None:
                    # Buffer only — the store happens after the fetch (or,
                    # for a resumable source, after each part) so a write
                    # transaction never spans network I/O.
                    day_records.append(record)
                    if on_record is not None:
                        on_record(record)

                def flush_part(checkpoint: PartCheckpoint | None) -> None:
                    """Store a finished part's records, and checkpoint it if it earned one.

                    Called for every part the fetcher walked to its end
                    without failing a reconcile (one that fails ends the day,
                    and its records are drained by the closing block below).
                    The records are always stored: this is the only thing that
                    empties ``day_records``, so a version that stored nothing
                    for a part the fetcher could not vouch for would hold a
                    whole 242,216-record day in memory exactly when the source
                    is degraded — the peak the per-part flush exists to remove
                    (#105 review, F1). One transaction, so a checkpoint can
                    never attest to records a rollback discarded.

                    The *checkpoint* is what a part has to earn, and there are
                    two independent ways not to earn one. The fetcher passes
                    ``None`` for a part that reconciled short of its own
                    promise; and a part holding a record that would not store
                    is not checkpointed here. Both are the same rule: the
                    failure records the day ``failed``, so it is re-offered,
                    and a checkpoint written beside the gap would make that
                    retry skip the one part holding it — the record would then
                    be lost silently and permanently. ``_store_records``
                    swallows the record's own exception so one bad record
                    cannot lose the batch, which is exactly why the count has
                    to be read back and acted on here.
                    """
                    nonlocal day_added, day_merged, day_failed
                    with transaction(conn):
                        added, merged, failed_ = _store_records(conn, source, day, day_records)
                        if checkpoint is not None and failed_ == 0:
                            _record_day_part(conn, source, day, checkpoint)
                    day_added += added
                    day_merged += merged
                    day_failed += failed_
                    day_records.clear()

                def note_skipped_part(part_key: str) -> None:
                    """Remember a part the fetcher skipped on this run.

                    Only these are credited from ``prior_parts`` below. A part
                    the fetcher re-walked stored its records through
                    :func:`flush_part` (or through the day's final store), so
                    crediting its stored count as well would double it.
                    """
                    skipped_keys.add(part_key)

                # A separate name, not a rebinding of ``src_config``: that one
                # is built once per source and reused for every day, so
                # folding this day's callbacks into it would leak them into
                # the next day's call.
                day_config = src_config
                if resumable:
                    day_config = {
                        **src_config,
                        "completed_parts": prior_parts,
                        "on_part_finished": flush_part,
                        "on_part_skipped": note_skipped_part,
                    }

                try:
                    if parts_error is not None:
                        raise _DayPartsUnreadableError(parts_error)
                    fetch_result = fetcher(
                        client,
                        day,
                        on_record=handle_record,
                        on_progress=on_progress,
                        **day_config,
                    )
                    if not isinstance(fetch_result, FetchResult):
                        # Raised rather than handled separately, so the one
                        # handler below covers both ways a fetcher can break
                        # its contract. `register_source()` is public, so the
                        # caller getting this wrong is a third party; a
                        # fetcher that *returns* without a `.status` used to
                        # reach `_resolve_day_status` outside this handler,
                        # and the AttributeError escaped through the finally
                        # and out of sync(), losing every source's report.
                        raise TypeError(
                            f"fetcher for {source} returned"
                            f" {type(fetch_result).__name__}, not a FetchResult"
                        )
                except Exception as exc:
                    # A misconfigured source (e.g. a required kwarg like
                    # OpenAlex's ``email`` not supplied) or a bug inside a
                    # fetcher must not abort the whole multi-source run and
                    # discard the report. Record it as a failed day and move on.
                    # The type is logged because this handler exists to catch
                    # bugs *inside* a fetcher, and a bare message cannot tell a
                    # misconfigured source from a defect in bmlib.
                    logger.error(
                        "Fetcher for %s/%s raised: %s: %s",
                        source,
                        day.isoformat(),
                        type(exc).__name__,
                        exc,
                    )
                    fetch_result = FetchResult(
                        source=source,
                        date=day.isoformat(),
                        # Records already flushed by a finished part plus the
                        # ones still buffered: the buffer alone stopped being
                        # the day's whole delivery when the flush moved to a
                        # per-part boundary.
                        record_count=day_added + day_merged + day_failed + len(day_records),
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )

                # One transaction for whatever the parts did not already
                # flush: each store_publication call joins it (savepoint)
                # instead of committing, so a batch of thousands of records
                # costs a single commit/fsync rather than one per statement —
                # and because records were buffered during the fetch, SQLite's
                # write lock is held only for the store loop, not for the
                # network-bound fetch. A record that fails to store rolls back
                # to its own savepoint without losing the batch, and the
                # day-status row commits atomically with the records. Should
                # writing the day-status row itself fail, the whole block
                # rolls back and the error propagates — the day is left
                # unrecorded and simply retried on the next run.
                with transaction(conn):
                    added, merged, failed_ = _store_records(conn, source, day, day_records)
                    day_added += added
                    day_merged += merged
                    day_failed += failed_

                    outcome = _resolve_day_status(source, day, fetch_result, day_failed)
                    # Credit the parts an earlier run stored and this run
                    # therefore skipped, so a day fetched across three runs is
                    # not recorded as holding only the last run's share. Only
                    # the skipped ones: a prior part whose count moved is
                    # re-walked, and its records are in the totals above
                    # already — crediting it as well would double it, and a
                    # re-walk that came up short is not checkpointed, so
                    # "everything this run did not checkpoint" would catch it.
                    #
                    # One cosmetic residue, named here so it is not later
                    # re-discovered as a bug: the skip rule compares a part's
                    # *current* count against the stored `promised`, so a part
                    # whose count moved away and back again is skipped and
                    # credited at the `record_count` the earlier run stored —
                    # a number describing that range's old contents rather
                    # than what is in `publications` now. It moves this row's
                    # `record_count` only, which no day-selection rule reads.
                    carried = sum(
                        cp.record_count for key, cp in prior_parts.items() if key in skipped_keys
                    )
                    record_count = day_added + day_merged + carried

                    _upsert_download_day(conn, source, day, outcome.status, record_count)
                    if outcome.status == "completed":
                        # The rows describe an unfinished day. Keeping them
                        # would grow the table without bound and would make a
                        # `recheck_days` re-fetch skip the parts it was asked
                        # to redo. Not conditioned on `resumable`: the delete
                        # is a no-op for a source that writes no parts, and
                        # conditioning it would strand a source's rows forever
                        # the moment its descriptor stopped declaring it
                        # resumable — to resurface if it ever declared it again.
                        _clear_day_parts(conn, source, day)

                total_added += day_added
                total_merged += day_merged
                total_failed += day_failed
                total_days += 1

                # `is not None`, not truthiness: `str(OSError())` is the empty
                # string, and a day that keeps failing with one would be
                # retried on every run while the report claimed no errors.
                if fetch_result.error is not None:
                    errors.append(f"{source}/{day.isoformat()}: {fetch_result.error}")
                errors.extend(outcome.errors)
                notes.extend(outcome.notes)

            sources_synced.append(source)
    finally:
        if client is not None and _fetcher_override is None:
            client.close()

    return SyncReport(
        sources_synced=sources_synced,
        days_processed=total_days,
        records_added=total_added,
        records_merged=total_merged,
        records_failed=total_failed,
        errors=errors,
        notes=notes,
    )
