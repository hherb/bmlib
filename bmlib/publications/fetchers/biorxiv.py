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
for a given date.  The same endpoint serves both bioRxiv and medRxiv data,
controlled by the ``server`` parameter.

**The endpoint is ``/pubs``, and a day means the day a preprint's journal
version appeared** (#325). ``/details``, which listed the preprints *posted* on
a day, has answered HTTP 200 with a zero-byte body on every URL shape since at
least 2026-09-26, so every bioRxiv day failed. ``/pubs`` pairs a preprint with
its publication, which makes it a narrower population — a preprint that is
never published is never collected here — and one that fills in late: bioRxiv
learns of a publication weeks after it appears. See :data:`BASE_URL` and
:data:`BIORXIV_SETTLE_DAYS`.
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

It was ``/details``, which served the preprints **posted** on a day. Probed
2026-09-27, ``/details`` answers HTTP 200 with a zero-byte body in every form
tried — bioRxiv's date-interval, *N most recent*, *N days*, ``/json`` and
``/xml`` forms and medRxiv's single-DOI form (#325 adds medRxiv's date
interval, 2026-09-26) — while its documentation page still describes it. So it
is not a URL-shape defect bmlib could route around. ``/pubs`` answers the same
five-segment shape.

**It is a different population, and the switch is a decision rather than a
repair** (the maintainer's, on #325). ``/pubs`` serves only preprints bioRxiv
has paired with a journal publication, filed under the date that publication
appeared: about 500 bioRxiv and 120 medRxiv records a week, a small fraction
of the postings (#325 puts bioRxiv's at several hundred a day, not
re-measured). A preprint that is never published is not collected, and a
source for those (bioRxiv's TDM bucket, an OAI-PMH feed or Crossref's
posted-content records) is open work, #341.
"""

PAGE_SIZE = 100
"""Records per ``/pubs`` page: bioRxiv's documentation says 100, and
2026-07-29 served 100 of its 105 on the first page (probed 2026-09-27)."""

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


def _normalize(raw: dict[str, Any], server: str) -> FetchedRecord:
    """Convert a raw bioRxiv/medRxiv API record to a :class:`FetchedRecord`.

    Reads a ``/pubs`` record, and a ``/details`` one for the fields the two
    share (see :func:`_field`). ``/pubs`` carries no ``version`` and no
    ``jatsxml``, so its PDF URL names ``v1`` and it yields no XML source.
    ``publication_date`` is the preprint's own date, which for ``/pubs`` is
    usually months before the day it was fetched for; that day is
    ``extras["published_date"]``.
    """
    doi = _field(raw, "preprint_doi", "doi")
    authors_raw = _field(raw, "preprint_authors", "authors")
    authors = [a.strip() for a in authors_raw.split(";") if a.strip()] if authors_raw else []

    # Build full-text sources
    fulltext_sources: list[FullTextSourceEntry] = []

    # PDF URL derived from DOI. Use the record's actual version rather than a
    # hard-coded "v1", which 404s / points to the wrong revision for v2+.
    if doi:
        version = str(raw.get("version") or "1").strip() or "1"
        pdf_url = f"https://www.{server}.org/content/{doi}v{version}.full.pdf"
        fulltext_sources.append(
            FullTextSourceEntry(
                url=pdf_url,
                format="pdf",
                source=server,
                open_access=True,
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
            )
        )

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
            # The journal version's DOI: `published_doi` on /pubs, a bare
            # `published` on /details.
            "published": _field(raw, "published_doi", "published"),
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

    Returns
    -------
    FetchResult
        Summary of the fetch operation.
    """
    date_str = target_date.isoformat()
    cursor = 0
    total_fetched = 0
    records_total: int | None = None
    stalled = False

    try:
        while True:
            url = f"{BASE_URL}/{server}/{date_str}/{date_str}/{cursor}"
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
                on_record(normalized)
                total_fetched += 1

            # Report progress after each page
            if on_progress is not None:
                on_progress(
                    SyncProgress(
                        source=server,
                        date=date_str,
                        records_processed=total_fetched,
                        records_total=records_total or total_fetched,
                        status="in_progress",
                    )
                )

            # Stop if this was the last page
            if len(collection) < PAGE_SIZE:
                break

            cursor += PAGE_SIZE
            time.sleep(RATE_LIMIT_SECONDS)

    except Exception as exc:
        # Logged here as well as returned: the type is what separates a bmlib
        # defect from a bad response, and `FetchResult.error` alone reaches
        # only callers that inspect it.
        logger.error("%s fetch failed for %s: %s: %s", server, date_str, type(exc).__name__, exc)
        return FetchResult(
            source=server,
            date=date_str,
            record_count=total_fetched,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    # Not `records_total or 0` — see reconcile_delivery's `promised` docstring:
    # None and 0 are different claims and collapsing them disables the rules.
    verdict = reconcile_delivery(
        server,
        date_str,
        delivered=total_fetched,
        promised=records_total,
        stalled=stalled,
    )
    if verdict.failure is not None:
        return FetchResult(
            source=server,
            date=date_str,
            record_count=total_fetched,
            status="failed",
            error=verdict.failure,
        )

    return FetchResult(
        source=server,
        date=date_str,
        record_count=total_fetched,
        status="completed",
        note=verdict.note,
    )
