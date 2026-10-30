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

"""OpenAlex fetcher — retrieves publication records from the OpenAlex API.

Uses cursor-based pagination to walk through all works published on a given
date.  Each raw record is normalised into the common publication dict format
before being handed to the *on_record* callback.
"""

from __future__ import annotations

import logging
import re
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

_API_URL = "https://api.openalex.org/works"
_PER_PAGE = 200
_RATE_LIMIT_SECONDS = 0.1
_DOI_PREFIX = "https://doi.org/"
_PMID_PREFIX = "https://pubmed.ncbi.nlm.nih.gov/"
_PMCID_SEGMENT = re.compile(r"(?:PMC)?([0-9]+)")

_VERSION_MAP: dict[str, str] = {
    "publishedVersion": "published",
    "acceptedVersion": "accepted",
    "submittedVersion": "preprint",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconstruct_abstract(inverted_index: dict[str, Any] | None) -> str | None:
    """Reconstruct an abstract from OpenAlex's inverted-index representation.

    OpenAlex stores abstracts as ``{"word": [pos, ...], ...}``.  We flatten
    this into a list of ``(position, word)`` pairs, sort by position, and join.

    Returns ``None`` when *inverted_index* is ``None`` or empty.
    """
    if not inverted_index:
        return None

    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            # An integer position only, as Rust's `as_i64` (a bool is not one).
            if isinstance(pos, int) and not isinstance(pos, bool):
                pairs.append((pos, word))

    if not pairs:
        return None

    pairs.sort(key=lambda p: p[0])
    return " ".join(word for _, word in pairs)


def _object(value: Any, key: str) -> dict[str, Any]:
    """``value[key]`` when both are JSON objects, else ``{}``."""
    if not isinstance(value, dict):
        return {}
    found = value.get(key)
    return found if isinstance(found, dict) else {}


def _string(value: dict[str, Any], key: str) -> str | None:
    """``value[key]`` when it is a non-empty string, else ``None``."""
    found = value.get(key)
    return found if isinstance(found, str) and found else None


def _list(value: dict[str, Any], key: str) -> list[Any]:
    """``value[key]`` when it is a JSON array, else ``[]``."""
    found = value.get(key)
    return found if isinstance(found, list) else []


def _pmc_id(pmcid_url: str | None) -> str | None:
    """``PMC`` + digits from OpenAlex's ``ids.pmcid`` article URL, or ``None``.

    OpenAlex serves it as ``https://www.ncbi.nlm.nih.gov/pmc/articles/7745181``
    (measured 2026-10-30), with no ``PMC`` prefix.
    """
    if pmcid_url is None:
        return None
    match = _PMCID_SEGMENT.fullmatch(pmcid_url.rstrip("/").rsplit("/", 1)[-1])
    return f"PMC{match.group(1)}" if match else None


def _normalize(raw: dict[str, Any]) -> FetchedRecord:
    """Convert a raw OpenAlex work record to a :class:`FetchedRecord`.

    Every field is read type-checked, as Rust's ``normalize`` reads it: a
    value of the wrong type is absent, costing that field and not the record.
    A *raw* that is not an object still raises ``AttributeError`` at the first
    ``raw.get``, which the page walk reports — the message Rust's port restates.
    """
    doi_raw = _string(raw, "doi")
    doi = doi_raw.removeprefix(_DOI_PREFIX) if doi_raw else None
    if doi == "":
        doi = None

    ids = _object(raw, "ids")
    pmid_raw = _string(ids, "pmid")
    pmid = pmid_raw.removeprefix(_PMID_PREFIX) if pmid_raw else None
    if pmid == "":
        pmid = None

    authors: list[str] = []
    for authorship in _list(raw, "authorships"):
        name = _string(_object(authorship, "author"), "display_name")
        if name:
            authors.append(name)

    journal = _string(_object(_object(raw, "primary_location"), "source"), "display_name")

    index = raw.get("abstract_inverted_index")
    abstract = _reconstruct_abstract(index if isinstance(index, dict) else None)

    keywords: list[str] = []
    topic_name = _string(_object(raw, "primary_topic"), "display_name")
    if topic_name:
        keywords.append(topic_name)

    is_open_access = _object(raw, "open_access").get("is_oa") is True

    # A present empty string survives as "", as it always has (Rust: `as_str`).
    license_raw = raw.get("license")
    license_value = license_raw if isinstance(license_raw, str) else None

    publication_types: list[str] = []
    work_type = _string(raw, "type")
    if work_type:
        publication_types.append(work_type)

    fulltext_sources: list[FullTextSourceEntry] = []
    for location in _list(raw, "locations"):
        if not isinstance(location, dict):
            continue
        loc_source = _string(_object(location, "source"), "display_name") or "unknown"
        version_raw = _string(location, "version")
        version = _VERSION_MAP.get(version_raw, version_raw) if version_raw else None
        loc_is_oa = location.get("is_oa") is True

        for key, fmt in (("landing_page_url", "html"), ("pdf_url", "pdf")):
            url = _string(location, key)
            if url:
                fulltext_sources.append(
                    FullTextSourceEntry(
                        url=url,
                        format=fmt,
                        source=loc_source,
                        open_access=loc_is_oa,
                        version=version,
                    )
                )

    return FetchedRecord(
        title=_string(raw, "title") or "",
        source="openalex",
        doi=doi,
        pmid=pmid,
        pmc_id=_pmc_id(_string(ids, "pmcid")),
        abstract=abstract,
        authors=authors,
        journal=journal,
        publication_date=_string(raw, "publication_date"),
        keywords=keywords,
        publication_types=publication_types,
        is_open_access=is_open_access,
        license=license_value,
        fulltext_sources=fulltext_sources,
    )


def _failed(date_str: str, records_processed: int, message: str) -> FetchResult:
    """Build a failed :class:`FetchResult`, keeping whatever the walk delivered.

    ``record_count`` is what arrived before the failure, not zero: those
    records were already handed to ``on_record`` and will be stored, and the
    day is retried regardless.
    """
    return FetchResult(
        source="openalex",
        date=date_str,
        record_count=records_processed,
        status="failed",
        error=message,
    )


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------


def fetch_openalex(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    email: str,
    api_key: str | None = None,
) -> FetchResult:
    """Fetch all OpenAlex works published on *target_date*.

    Parameters
    ----------
    client:
        An httpx-compatible client with a ``.get()`` method.
    target_date:
        The publication date to query.
    on_record:
        Called once per normalised :class:`FetchedRecord`.
    on_progress:
        Optional callback receiving :class:`SyncProgress` updates.
    email:
        Polite-pool email sent as ``mailto`` parameter.
    api_key:
        Optional OpenAlex API key for premium access.

    Returns
    -------
    FetchResult
        Summary of the fetch operation.
    """
    date_str = target_date.isoformat()
    # `str | None`, not `str`: "*" only seeds the first page, and the loop
    # below exits on the `None` the last page's `next_cursor` returns.
    cursor: str | None = "*"
    records_processed = 0
    records_total = 0
    is_first_page = True
    stalled = False

    while cursor is not None:
        params: dict[str, Any] = {
            "filter": f"from_publication_date:{date_str},to_publication_date:{date_str}",
            "per_page": _PER_PAGE,
            "cursor": cursor,
            "mailto": email,
        }
        if api_key:
            params["api_key"] = api_key

        try:
            response = client.get(_API_URL, params=params)
            response.raise_for_status()
            # Inside the guard, not after it (#91): a malformed body is a
            # failure of this HTTP call, and decoding it outside meant the
            # exception escaped the fetcher entirely and was logged by
            # ``sync()`` as "Fetcher raised" — pointing at the wrong layer.
            data = response.json()
        except Exception as exc:
            # Type included and logged: `str(OSError())` is the empty string,
            # which reads downstream as "no error" and is dropped from the
            # report entirely, and a day that keeps failing then does so with
            # nothing said about why.
            logger.error("OpenAlex fetch failed for %s: %s: %s", date_str, type(exc).__name__, exc)
            return _failed(date_str, records_processed, f"{type(exc).__name__}: {exc}")

        # OpenAlex answers an invalid query with an ``{"error": ...}`` body at
        # HTTP 200. Read through ``.get()`` defaults, such a body is
        # indistinguishable from a day with no works — which is how a rejected
        # query came to be stored as a completed day (#88). The envelope is
        # therefore checked rather than defaulted, in the order that lets a
        # page's valid records still be emitted before the page is refused.
        if not isinstance(data, dict):
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a {type(data).__name__} payload, not an object, for {date_str}",
            )

        results = data.get("results")
        if not isinstance(results, list):
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a page carrying no results list for {date_str}",
            )

        try:
            for raw in results:
                normalised = _normalize(raw)
                on_record(normalised)
                records_processed += 1
        except Exception as exc:
            # Same reasoning as the ``response.json()`` move above (#91): a
            # results list whose members are not work objects is this page
            # failing, and letting it escape had ``sync()`` report it as
            # "Fetcher raised" — the wrong layer for a malformed payload.
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a page bmlib could not normalise for {date_str}:"
                f" {type(exc).__name__}: {exc}",
            )

        meta = data.get("meta")
        if not isinstance(meta, dict):
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a page carrying no meta object for {date_str}",
            )

        if is_first_page:
            # ``bool`` is an ``int``, but a count is never sent as one, so it
            # is refused by name (issue #313: ``True`` passed the bare
            # ``isinstance`` and reached a caller's error line as "0 of True
            # records"). The check otherwise exists for the error bodies that
            # send no count at all.
            count = meta.get("count")
            if isinstance(count, bool) or not isinstance(count, int):
                return _failed(
                    date_str,
                    records_processed,
                    f"OpenAlex returned a page whose meta carries no numeric count for {date_str}",
                )
            records_total = count
            is_first_page = False

        if not results:
            # An empty page while ``meta.count`` says works remain is a walk
            # that stopped serving them, not the end of one — the late-page
            # death that the shortfall floor cannot catch, since 600 of 1,000
            # clears it. OpenAlex reached ``reconcile_delivery`` without this
            # flag and so was judged by the floor alone.
            #
            # Breaking unconditionally also bounds the loop: a page carrying
            # no results and a non-null ``next_cursor`` otherwise repeats for
            # ever. A walk that has already met its promise breaks here too
            # and completes, because ``reconcile_delivery`` returns early once
            # ``delivered >= promised`` and never consults *stalled*.
            stalled = records_processed < records_total
            break

        if on_progress is not None:
            on_progress(
                SyncProgress(
                    source="openalex",
                    date=date_str,
                    records_processed=records_processed,
                    records_total=records_total,
                    status="in_progress",
                )
            )

        cursor = meta.get("next_cursor")

        # Respect rate limit between paginated requests
        if cursor is not None:
            time.sleep(_RATE_LIMIT_SECONDS)

    # ``delivered`` is what the server handed over. That is ``records_processed``
    # only because ``_normalize`` skips nothing — the moment this loop grows a
    # "skip this kind of work" branch, this must count list members instead, or
    # it reports PubMed's phantom shortfall (see reconcile_delivery's docstring).
    verdict = reconcile_delivery(
        "openalex",
        date_str,
        delivered=records_processed,
        promised=records_total,
        stalled=stalled,
    )
    if verdict.failure is not None:
        return _failed(date_str, records_processed, verdict.failure)

    return FetchResult(
        source="openalex",
        date=date_str,
        record_count=records_processed,
        status="completed",
        note=verdict.note,
    )
