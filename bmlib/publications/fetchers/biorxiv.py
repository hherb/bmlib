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

"""Fetcher for bioRxiv and medRxiv preprint records.

Uses the bioRxiv API (https://api.biorxiv.org) to retrieve preprint metadata
for a given date.  The same API serves both bioRxiv and medRxiv data,
controlled by the ``server`` parameter, through two endpoints that list
different populations (#341):

- ``"pubs"`` (:data:`BASE_URL`, the default, registered as ``biorxiv`` and
  ``medrxiv``): a day means the day a preprint's **journal version**
  appeared (#325). Only preprints bioRxiv has paired with a publication are
  listed, and a day fills in for weeks (:data:`BIORXIV_SETTLE_DAYS`).
- ``"details"`` (:data:`DETAILS_BASE_URL`, registered as ``biorxiv-details``
  and ``medrxiv-details``): a day means the day a version was **posted**.
  Every posting is listed, one record per version, each with its ``jatsxml``
  and ``version`` (:data:`DETAILS_SETTLE_DAYS`).

The two are separate registered sources rather than one source with a
setting because ``download_days`` is keyed by the source's name: a day
completed under ``/pubs`` must not read as completed for ``/details``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.fetchers._reconcile import reconcile_delivery
from bmlib.publications.models import FetchedRecord, FetchResult, SyncProgress

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://api.biorxiv.org/pubs"
"""bioRxiv's *"preprint published article detail"* endpoint.

It was ``/details``, which serves the preprints **posted** on a day. Probed
2026-09-27, ``/details`` answered HTTP 200 with a zero-byte body in every form
tried — bioRxiv's date-interval, *N most recent*, *N days*, ``/json`` and
``/xml`` forms and medRxiv's single-DOI form (#325 adds medRxiv's date
interval, 2026-09-26) — while its documentation page still described it, so it
was not a URL-shape defect bmlib could route around. ``/pubs`` answers the same
five-segment shape.

**``/details`` came back later on 2026-09-27** (found by the Rust port's gated
live suite, which had been asserting it dead), and it has served JSON on every
probe since: re-probed 2026-10-01, the date-interval shape the fetcher builds
answers 64,657 bytes for bioRxiv 2024-01-15, declaring 207 records against
``/pubs``' 34, and 75,608 bytes for medRxiv (#386). So this is a choice between
two endpoints that both answer, and both are read: ``/details`` through
:data:`DETAILS_BASE_URL` (#341).

**It is a different population from ``/details``.** ``/pubs`` serves only
preprints bioRxiv has paired with a journal publication, filed under the date
that publication appeared: about 500 bioRxiv and 120 medRxiv records a week, a
small fraction of the postings. A preprint that is never published is not
collected here; the ``*-details`` sources collect it.
"""

DETAILS_BASE_URL = "https://api.biorxiv.org/details"
"""bioRxiv's *"content detail"* endpoint: the versions **posted** on a day (#341).

One record per posted version, each carrying ``version``, ``date`` (the
posting date), ``jatsxml`` and ``published`` (the journal DOI, or ``"NA"``).
Measured 2026-11-13 against ``/pubs``: medRxiv 2024-01-15 declares 28
against 10, bioRxiv 207 against 34, and recent weekdays 55-99 medRxiv and
190-440 bioRxiv postings a day.
"""

PAGE_SIZE = 100
"""Records per ``/pubs`` page: bioRxiv's documentation says 100, and
2026-07-29 served 100 of its 105 on the first page (probed 2026-09-27, and
again 2026-11-13)."""

DETAILS_PAGE_SIZE = 30
"""Records per ``/details`` page, **not** ``/pubs``' 100 (measured 2026-11-13).

bioRxiv 2026-11-04 declared 378: pages at cursors 0, 30, ..., 330 held 30
each, cursor 360 held 18, and the 378 were distinct; cursor 100 served
records 100-129, so the cursor is a record offset. A walk that took 100 as
the page size would read a 30-record first page as the last one and stop,
and reconciliation lets that complete, with only a WARNING and a note, for
any day of 60 or fewer (30 of 60 is not below half). A page longer than the
size fails its day, because the next page would overlap it. Rust's gated
live suite asserts the size.
"""

RATE_LIMIT_SECONDS = 0.5

BIORXIV_SETTLE_DAYS = 90
"""How long after a day ends ``/pubs`` may still be adding records to it.

Measured 2026-09-27 as one snapshot of weekly totals, each week at a
different age, bioRxiv then medRxiv: 1 and 1 for the week just ended, 3 and 1
for the week before, then 350/85, 470/105, 135/32 and 237/45 for the weeks two
to five weeks old, and a steady 450-580 / 95-160 for every week from six to
seventy-six weeks old. No week was watched filling, so this reads a fill curve
off weeks of different ages. The fill is irregular rather than smooth, which
is why the margin is wide: at weekly resolution the plateau begins somewhere
between about 36 and 49 days, and ninety is roughly twice that. Whether
anything is still paired after it is **not measured** — that needs the same
day observed twice, months apart.

Read by the registry into :attr:`SourceDescriptor.settle_days`; see that
attribute for what ``sync()`` does with it.
"""

DETAILS_SETTLE_DAYS = 7
"""How long after a day ends ``/details`` may still be adding records to it.

**A bound, not a measurement.** ``/details`` files a version under the date
it was posted, so a day should be whole soon after it ends, and the old days
probed hold their counts: bioRxiv 2024-01-15 and 2025-06-01 declared 207 and
195, medRxiv 28 and 26, on both 2026-09-27 and 2026-11-13. How soon a day is
whole after it ends was not measured (that needs one day observed at several
ages, #759). A day recorded too early is never offered again, while each
day of margin costs one re-fetch of a day per run, so the bound is generous.
"""

_ENDPOINTS: dict[str, tuple[str, int]] = {
    "pubs": (BASE_URL, PAGE_SIZE),
    "details": (DETAILS_BASE_URL, DETAILS_PAGE_SIZE),
}
"""Each endpoint's base URL and page size, by the name ``fetch_biorxiv`` takes."""


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _field(raw: dict[str, Any], pubs_name: str, details_name: str) -> Any:
    """Read a field under its ``/pubs`` name, or else its ``/details`` name.

    ``/pubs`` prefixes every preprint field (``preprint_title``) where
    ``/details`` did not (``title``), so a reader that was only re-pointed
    finds **every value absent** and stores a titleless record per preprint —
    #295's shape, reached through an endpoint instead of a ``null``. Both
    spellings are accepted so a record from either endpoint reads the same,
    and the mapping is stated here once rather than at each field.

    A present but empty ``/pubs`` value falls through to the ``/details``
    name, which a ``/pubs`` record does not carry, so the answer is the empty
    value either way.
    """
    value = raw.get(pubs_name)
    return value if value else raw.get(details_name, "")


def _record_version(raw: dict[str, Any]) -> str | None:
    """The record's own version, or None where it names none (``/pubs``)."""
    return str(raw.get("version") or "").strip() or None


def _normalize(raw: dict[str, Any], server: str) -> FetchedRecord:
    """Convert a raw bioRxiv/medRxiv API record to a :class:`FetchedRecord`.

    Reads a ``/pubs`` record, and a ``/details`` one for the fields the two
    share (see :func:`_field`). ``/pubs`` carries no ``version`` and no
    ``jatsxml``, so its PDF URL names ``v1``, it yields no XML source, and its
    sources' ``version`` is ``None``. A ``/details`` record's ``version`` is
    its sources' ``version``, which is how a v2 posting stored over a v1 row
    is told apart: each version's URLs differ, so both versions' sources are
    kept.
    ``publication_date`` is the preprint's own date, which for ``/pubs`` is
    usually months before the day it was fetched for; that day is
    ``extras["published_date"]``.
    """
    doi = _field(raw, "preprint_doi", "doi")
    authors_raw = _field(raw, "preprint_authors", "authors")
    authors = [a.strip() for a in authors_raw.split(";") if a.strip()] if authors_raw else []

    # Build full-text sources
    fulltext_sources: list[FullTextSourceEntry] = []

    version = _record_version(raw)

    # PDF URL derived from DOI. Use the record's actual version rather than a
    # hard-coded "v1", which 404s / points to the wrong revision for v2+.
    if doi:
        pdf_url = f"https://www.{server}.org/content/{doi}v{version or '1'}.full.pdf"
        fulltext_sources.append(
            FullTextSourceEntry(
                url=pdf_url,
                format="pdf",
                source=server,
                open_access=True,
                version=version,
            )
        )

    # JATS XML URL from the record
    jatsxml = raw.get("jatsxml", "")
    if jatsxml:
        fulltext_sources.append(
            FullTextSourceEntry(
                url=jatsxml,
                format="xml",
                source=server,
                open_access=True,
                version=version,
            )
        )

    # The journal version's DOI: `published_doi` on /pubs, a bare `published`
    # on /details, which sends "NA" for a preprint with none (#341). "NA" is
    # no DOI, so it is stored as the field's empty value.
    published = _field(raw, "published_doi", "published")
    if published == "NA":
        published = ""

    return FetchedRecord(
        title=_field(raw, "preprint_title", "title"),
        source=server,
        doi=doi or None,
        # Use None (not "") for absent optional fields so the storage layer's
        # COALESCE-based merge can still fill them in from another source later;
        # an empty string is not SQL NULL and would block that fill-in forever.
        abstract=_field(raw, "preprint_abstract", "abstract") or None,
        authors=authors,
        publication_date=_field(raw, "preprint_date", "date") or None,
        is_open_access=True,
        fulltext_sources=fulltext_sources,
        extras={
            "category": _field(raw, "preprint_category", "category"),
            "published": published,
            "published_journal": raw.get("published_journal") or "",
            "published_date": raw.get("published_date") or "",
            "server": raw.get("server", server),
        },
    )


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


def fetch_biorxiv(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    server: str = "biorxiv",
    api_key: str | None = None,
    endpoint: str = "pubs",
) -> FetchResult:
    """Fetch preprint records from the bioRxiv/medRxiv API for a single date.

    Parameters
    ----------
    client:
        An HTTP client with a ``get(url)`` method that returns a response
        object with ``.status_code`` (int), ``.json()`` (dict), and
        ``.raise_for_status()`` methods (e.g. ``httpx.Client``).
    target_date:
        The date to fetch records for.
    on_record:
        Callback invoked with each normalised :class:`FetchedRecord`.
    on_progress:
        Optional callback invoked after each page to report progress.
    server:
        ``"biorxiv"`` (default) or ``"medrxiv"``.
    api_key:
        Unused; reserved for future API authentication.
    endpoint:
        ``"pubs"`` (default; preprints by the day their journal version
        appeared) or ``"details"`` (every version, by the day it was
        posted). See the module docstring.

    Returns
    -------
    FetchResult
        Summary of the fetch operation.

    Raises
    ------
    ValueError
        *endpoint* is neither ``"pubs"`` nor ``"details"``. Raised, not
        returned as a failed day: it is the caller's mistake, on every day.
    """
    if endpoint not in _ENDPOINTS:
        raise ValueError(
            f"unknown bioRxiv endpoint {endpoint!r}; expected one of {sorted(_ENDPOINTS)}"
        )
    base_url, page_size = _ENDPOINTS[endpoint]
    # The registered source's name, for logs, progress and the result: sync()
    # walks `medrxiv` and `medrxiv-details` for the same day. Records keep the
    # server as their source.
    name = server if endpoint == "pubs" else f"{server}-details"
    date_str = target_date.isoformat()
    cursor = 0
    total_fetched = 0
    records_total: int | None = None
    stalled = False

    try:
        while True:
            url = f"{base_url}/{server}/{date_str}/{date_str}/{cursor}"
            response = client.get(url)
            response.raise_for_status()

            data = response.json()
            # Checked rather than defaulted (#88): read through
            # ``.get(..., [])``, an HTTP-200 error body is indistinguishable
            # from a day with no preprints, and a day stored as completed is
            # not offered again once it is in the past.
            if not isinstance(data, dict):
                raise ValueError(
                    f"{server} returned a {type(data).__name__} payload, not an object"
                )

            messages = data.get("messages")
            if not isinstance(messages, list):
                messages = []
            # The guard is "carries no evidence either way", not "carries a
            # collection", and the difference is deliberate. bioRxiv's quiet
            # day is known to omit ``total`` (DECISIONS.md). Whether it also
            # omits ``collection`` was never measured for ``/details``; for
            # ``/pubs``, 6 of 6 quiet days sent ``collection: []`` (2026-09-27,
            # #94), which is six days and not a guarantee — and requiring a
            # key the API may not send on a quiet day would fail that day on
            # every run for the life of the installation, the runaway-retry
            # cost this package's reconciliation rules are written to avoid.
            # A body carrying neither key makes no claim at all about the day,
            # so refusing it needs no knowledge of which keys a quiet day
            # sends. Issue #94 is the live sampler that would let this be
            # tightened.
            if "collection" not in data and not messages:
                raise ValueError(
                    f"{server} returned an object carrying neither a collection nor"
                    " messages, so it makes no claim about the day"
                )

            collection = data.get("collection", [])
            if not isinstance(collection, list):
                raise ValueError(f"{server} returned a collection that is not a list")

            # Absent ``total`` leaves this None rather than 0 (#88): flattening
            # the two makes "the source said this day is empty" and "the source
            # said nothing" identical, and the second silently switches off
            # both reconciliation rules — a page walk that then stops early
            # completes with no shortfall and no stall detected.
            if records_total is None and messages:
                first = messages[0]
                if isinstance(first, dict) and first.get("total") is not None:
                    try:
                        # ``int(True)`` is 1, so a boolean would become a
                        # promise; refused as OpenAlex refuses one (#313).
                        if isinstance(first["total"], bool):
                            raise TypeError("a boolean is not a count")
                        records_total = int(first["total"])
                    except (TypeError, ValueError) as exc:
                        # Named, because the day retries on every run until the
                        # cause is fixed and a bare int() message ("invalid
                        # literal for int() with base 10") says neither which
                        # source nor which field is at fault.
                        raise ValueError(
                            f"{server} reported a non-numeric total"
                            f" {first['total']!r} for {date_str}"
                        ) from exc

            # The cursor is a record offset, so a page longer than the step
            # overlaps the next one, and every repeat would count as delivered:
            # a stall would then read as a whole day.
            if len(collection) > page_size:
                raise ValueError(
                    f"{server} served {len(collection)} records on one {endpoint} page"
                    f" for {date_str}, more than the {page_size} the walk steps by, so"
                    " the next page would overlap it"
                )

            if not collection:
                # An empty page while the source's own total says records
                # remain is a walk that stopped serving them, not the end.
                stalled = records_total is not None and total_fetched < records_total
                break

            for raw_record in collection:
                normalized = _normalize(raw_record, server)
                if normalized.doi is None:
                    # Every bioRxiv and medRxiv preprint has a DOI, so a record
                    # without one under either spelling means the endpoint's
                    # shape changed (a renamed ``preprint_doi``) — and stored,
                    # it has no identity to deduplicate on, so each revisit of
                    # an unsettled day would insert it again. Failing the day
                    # is loud and retried; storing it is neither.
                    raise ValueError(
                        f"{server} served a record for {date_str} carrying no DOI"
                        " under either spelling (preprint_doi, doi)"
                    )
                if endpoint == "details" and _record_version(raw_record) is None:
                    # Every posting names its version. One that does not takes
                    # v1's URLs, which a stored v1 row already holds, so its
                    # sources would be dropped as duplicates.
                    raise ValueError(
                        f"{server} served a details record for {date_str}"
                        f" ({normalized.doi}) carrying no version"
                    )
                on_record(normalized)
                total_fetched += 1

            # Report progress after each page
            if on_progress is not None:
                on_progress(
                    SyncProgress(
                        source=name,
                        date=date_str,
                        records_processed=total_fetched,
                        records_total=records_total or total_fetched,
                        status="in_progress",
                    )
                )

            # Stop if this was the last page
            if len(collection) < page_size:
                break

            cursor += page_size
            time.sleep(RATE_LIMIT_SECONDS)

    except Exception as exc:
        # Logged here as well as returned: the type is what separates a bmlib
        # defect from a bad response, and `FetchResult.error` alone reaches
        # only callers that inspect it.
        logger.error("%s fetch failed for %s: %s: %s", name, date_str, type(exc).__name__, exc)
        return FetchResult(
            source=name,
            date=date_str,
            record_count=total_fetched,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    # Not `records_total or 0` — see reconcile_delivery's `promised` docstring:
    # None and 0 are different claims and collapsing them disables the rules.
    verdict = reconcile_delivery(
        name,
        date_str,
        delivered=total_fetched,
        promised=records_total,
        stalled=stalled,
    )
    if verdict.failure is not None:
        return FetchResult(
            source=name,
            date=date_str,
            record_count=total_fetched,
            status="failed",
            error=verdict.failure,
        )

    return FetchResult(
        source=name,
        date=date_str,
        record_count=total_fetched,
        status="completed",
        note=verdict.note,
    )
