#!/usr/bin/env python3
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

"""Measure how far a PubMed history session can be walked, and how many days need more.

`bmlib.publications.fetchers.pubmed` fetches a day by running one esearch with
``usehistory=y`` and then paging efetch over the session it opens. Two
properties of that route are load-bearing and neither is bmlib's to choose, so
both are measured here rather than assumed:

1. **How far the session serves.** ``EFETCH_MAX_RETRIEVABLE`` says 9,999. The
   backend enforces it in two different ways — an outright HTTP 400 past the
   boundary, and a *silent* clamp on the page that straddles it — and the
   second is why a day over the cap is refused outright rather than walked as
   far as it goes: a fetcher that asks for what the server will not send
   cannot tell that page apart from a day missing records.

2. **What ``retstart`` indexes.** Issue #96 asked whether a short non-empty
   page leaves records never requested. It does not: ``retstart`` offsets the
   session's UID list, so a page carries the records of the slice it names,
   and a missing one is a UID the server had nothing to return for. Advancing
   by what arrived — #96's proposed fix — would re-request the tail of every
   short page and count the duplicates as delivery. This script re-establishes
   that by comparing a page's record elements against the session's own UID
   list, in order.

3. **How many days exceed the cap**, in the field bmlib actually queries.
   ``[Date - Publication]`` is not ``[EDAT]``: a record carrying only a year
   and a month is indexed at day 1 of that month, and one carrying only a year
   at 1 January, so those days are enormous and *structurally* so. That is the
   measurement behind issue #105, which partitions an over-cap day into
   sub-queries that fit.

Run it before changing ``EFETCH_MAX_RETRIEVABLE``, before touching the page
walk in ``fetch_pubmed``, and when sizing #105's partitioning:

    uv run python scripts/sample_efetch_paging.py --email you@example.org

Every probe that could not be made prints ``ERROR`` and is excluded from the
population rather than counted as a pass — a refusal and an unreachable server
look identical in a total, and only one of them is a finding. Costs about 150
small requests plus one ~200 KB XML page; nothing here downloads a full page of
500 records.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# Pacing and the interval live in `_sampling` alongside the other samplers'
# copies of the same rules; running the script puts this directory on sys.path
# as sys.path[0], and the test file loads the module by path.
from _sampling import wilson

from bmlib.publications.fetchers.pubmed import (
    EFETCH_MAX_RETRIEVABLE,
    EFETCH_URL,
    ESEARCH_URL,
)

ESEARCH = ESEARCH_URL
EFETCH = EFETCH_URL

# NCBI's unauthenticated ceiling is 3 requests/second; stay under it.
REQUEST_INTERVAL_SECONDS = 0.4

# Records compared element-for-UID in the slice probe. A handful settles what
# `retstart` indexes, and a full page of 500 is ~2 MB for no extra evidence.
SLICE_SAMPLE = 50

# The boundary search needs a session larger than any plausible cap; 90 days of
# indexing is ~500,000 records.
BOUNDARY_SESSION_DAYS = 90

# Upper bound for the search. Doubling from the known-good side would work too,
# but a fixed bound keeps the request count fixed at ~17 and printable.
BOUNDARY_SEARCH_CEILING = 131_072


@dataclass(frozen=True)
class Probe:
    """One measurement, or the reason it could not be made.

    A sampler that returns a bare number cannot distinguish "the server said
    9,999" from "the server said nothing", and the second printed as the first
    is how a throttled run comes to read as evidence. *refused* is the third
    state and the one a boundary search lives on: an HTTP 400 past the limit is
    the server answering, not failing to.
    """

    value: Any = None
    refused: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _get(url: str, params: dict[str, str]) -> tuple[int, str] | None:
    """Issue one paced E-utilities request; return ``(status, body)`` or None.

    The status is returned rather than raised on, because HTTP 400 is a
    *measurement* here — it is how the backend reports the boundary — while a
    connection failure is not. ``urllib`` rather than httpx for the reason
    ``scripts/sample_databank_names.py`` gives: this wants nothing httpx
    offers, and the standard library keeps the script runnable without the
    optional extras installed.
    """
    time.sleep(REQUEST_INTERVAL_SECONDS)
    query = urllib.parse.urlencode(params)
    try:
        # The URL is a module constant; only the query is built here.
        with urllib.request.urlopen(f"{url}?{query}", timeout=60) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  request failed: {e}", file=sys.stderr)
        return None


def _count(term: str, base: dict[str, str]) -> int | None:
    """How many records match *term*, or None if the question could not be asked."""
    got = _get(ESEARCH, {**base, "db": "pubmed", "term": term, "retmax": "0"})
    if got is None or got[0] != 200:
        return None
    found = re.search(r"<Count>(\d+)</Count>", got[1])
    if found is None:
        # A 200 carrying no <Count> is an NCBI error document, not an empty
        # day; read as 0 it would print as a day comfortably under the cap.
        print("  esearch response carried no <Count>", file=sys.stderr)
        return None
    return int(found.group(1))


def _session(term: str, base: dict[str, str]) -> tuple[int, str, str] | None:
    """Open a history session over *term*: ``(count, WebEnv, QueryKey)``."""
    got = _get(
        ESEARCH,
        {**base, "db": "pubmed", "term": term, "retmax": "0", "usehistory": "y"},
    )
    if got is None or got[0] != 200:
        return None
    try:
        root = ET.fromstring(got[1])  # noqa: S314 - NCBI payload, no entities requested
    except ET.ParseError as e:
        print(f"  unparsable esearch response: {e}", file=sys.stderr)
        return None
    count, web_env, query_key = (
        root.findtext("Count"),
        root.findtext("WebEnv"),
        root.findtext("QueryKey"),
    )
    if not (count or "").isdigit() or not web_env or not query_key:
        print("  esearch returned no usable history session", file=sys.stderr)
        return None
    return int(count), web_env, query_key


def _uilist(session: tuple[int, str, str], start: int, retmax: int, base: dict[str, str]) -> Probe:
    """The UIDs the session holds at ``[start, start + retmax)``.

    ``rettype=uilist`` is what makes the boundary search affordable: the same
    ``retstart`` arithmetic the record walk uses, answered in bytes rather than
    megabytes. A refusal is a measurement, so an HTTP 400 returns a Probe whose
    value is the refusal, not an error.
    """
    _, web_env, query_key = session
    got = _get(
        EFETCH,
        {
            **base,
            "db": "pubmed",
            "WebEnv": web_env,
            "query_key": query_key,
            "retstart": str(start),
            "retmax": str(retmax),
            "rettype": "uilist",
            "retmode": "text",
        },
    )
    if got is None:
        return Probe(error="request failed")
    status, body = got
    if status == 400:
        return Probe(refused=True)
    if status != 200:
        return Probe(error=f"HTTP {status}")
    return Probe(value=[line.strip() for line in body.splitlines() if line.strip()])


def measure_boundary(session: tuple[int, str, str], base: dict[str, str]) -> Probe:
    """The largest ``retstart`` the backend will serve, by binary search.

    Bounded below by a value known to work and above by one known to be
    refused; if either end fails to behave, the search is abandoned rather
    than reported, since a search over a broken bound converges on a number
    that means nothing.
    """
    low = _uilist(session, 0, 1, base)
    if not low.ok:
        return Probe(error=f"the known-good end could not be probed ({low.error})")
    if low.refused:
        return Probe(error="retstart=0 was refused, so there is no boundary to find")

    high = _uilist(session, BOUNDARY_SEARCH_CEILING, 1, base)
    if not high.ok:
        return Probe(error=f"the known-bad end could not be probed ({high.error})")
    if not high.refused:
        return Probe(error=f"retstart={BOUNDARY_SEARCH_CEILING} was served; raise the ceiling")

    lo, hi = 0, BOUNDARY_SEARCH_CEILING  # lo is served, hi is refused
    while hi - lo > 1:
        mid = (lo + hi) // 2
        probe = _uilist(session, mid, 1, base)
        if not probe.ok:
            return Probe(error=f"the search broke off at retstart={mid} ({probe.error})")
        if probe.refused:
            hi = mid
        else:
            lo = mid
    return Probe(value=lo)


def measure_straddling_page(
    session: tuple[int, str, str], last_retstart: int, base: dict[str, str]
) -> Probe:
    """How many UIDs a page asking to cross the boundary is actually given.

    The quiet half of the limit. A page starting inside the served range and
    asking for more than remains is answered at HTTP 200, short, with nothing
    to say it was clamped. *start* is chosen so the page asks for exactly one
    record past the last one served — a page ending on the boundary is served
    whole and measures nothing.
    """
    start = max(0, last_retstart + 1 - 499)
    probe = _uilist(session, start, 500, base)
    if not probe.ok:
        return Probe(error=probe.error)
    if probe.refused:
        return Probe(error=f"retstart={start} was refused, so no page straddles the boundary")
    return Probe(value=(start, len(probe.value)))


def measure_slice_semantics(session: tuple[int, str, str], base: dict[str, str]) -> Probe:
    """Are a page's record elements the UID slice it named, in order?

    The evidence for the fixed stride. Book chapters are included on purpose:
    ``<PubmedBookArticle>`` is a record element the fetcher counts as
    delivered and does not parse, and a comparison that dropped them would
    report a mismatch bmlib does not have.
    """
    uids = _uilist(session, 0, SLICE_SAMPLE, base)
    if not uids.ok or uids.refused:
        return Probe(error=uids.error or "the UID slice was refused")

    _, web_env, query_key = session
    got = _get(
        EFETCH,
        {
            **base,
            "db": "pubmed",
            "WebEnv": web_env,
            "query_key": query_key,
            "retstart": "0",
            "retmax": str(SLICE_SAMPLE),
            "retmode": "xml",
        },
    )
    if got is None or got[0] != 200:
        reason = f"HTTP {got[0]}" if got else "no reply"
        return Probe(error=f"the record page could not be fetched ({reason})")
    try:
        root = ET.fromstring(got[1])  # noqa: S314 - NCBI payload, no entities requested
    except ET.ParseError as e:
        return Probe(error=f"unparsable record page: {e}")
    if root.tag != "PubmedArticleSet":
        return Probe(error=f"the record page was <{root.tag}>, not <PubmedArticleSet>")

    delivered = [
        child.findtext(".//PMID")
        for child in root
        if child.tag in ("PubmedArticle", "PubmedBookArticle", "DeleteCitation")
    ]
    return Probe(value=(delivered == uids.value, len(delivered), len(uids.value)))


def measure_day_sizes(days: list[date], base: dict[str, str]) -> list[tuple[date, int | None]]:
    """The ``[Date - Publication]`` count for each day, None where unmeasured."""
    rows = []
    for day in days:
        stamp = day.strftime("%Y/%m/%d")
        rows.append((day, _count(f'("{stamp}"[Date - Publication])', base)))
    return rows


def _structural_days(today: date) -> list[date]:
    """The days the indexing convention makes large: month firsts, and 1 January.

    A record carrying only a year and a month is indexed at day 1 of it, and
    one carrying only a year at 1 January, so these are not the tail of the
    ordinary distribution — they are a second population, and averaging them
    into the first would hide both.
    """
    firsts = [date(today.year, month, 1) for month in range(1, today.month + 1)]
    firsts += [date(today.year - 1, month, 1) for month in range(today.month + 1, 13)]
    januarys = [date(today.year - n, 1, 1) for n in range(1, 5)]
    return sorted(set(firsts + januarys))


def report_day_sizes(rows: list[tuple[date, int | None]], label: str) -> None:
    """Print one population's over-cap share, excluding what could not be read."""
    measured = [(day, count) for day, count in rows if count is not None]
    unmeasured = len(rows) - len(measured)
    print(f"\n{label}: {len(measured)} days measured, {unmeasured} unmeasured")
    if not measured:
        print("  ERROR — nothing measured, so there is no share to report")
        return
    over = [(day, count) for day, count in measured if count > EFETCH_MAX_RETRIEVABLE]
    low, high = wilson(len(over), len(measured))
    counts = sorted(count for _, count in measured)
    print(
        f"  over {EFETCH_MAX_RETRIEVABLE}: {len(over)}/{len(measured)}"
        f" = {100 * len(over) / len(measured):.1f}%  (95% CI {100 * low:.1f}-{100 * high:.1f}%)"
    )
    print(f"  median={counts[len(counts) // 2]}  max={counts[-1]}")
    for day, count in sorted(over, key=lambda kv: kv[1], reverse=True)[:12]:
        print(f"    {day}  {count:>8}  ({count - EFETCH_MAX_RETRIEVABLE} out of reach)")


def main() -> int:
    """Run the three probes and print what each one settles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address NCBI asks callers for.")
    parser.add_argument("--api-key", default=None, help="Optional NCBI API key.")
    parser.add_argument(
        "--days", type=int, default=120, help="Ordinary days to size, counting back (default 120)."
    )
    parser.add_argument(
        "--skip-day-sizes", action="store_true", help="Probe the session limit only."
    )
    args = parser.parse_args()

    base = {"tool": "bmlib", "email": args.email}
    if args.api_key:
        base["api_key"] = args.api_key

    today = date.today()
    window_start = today - timedelta(days=BOUNDARY_SESSION_DAYS)
    term = f'("{window_start:%Y/%m/%d}"[EDAT] : "{today:%Y/%m/%d}"[EDAT])'
    print(f"bmlib says a session serves its first {EFETCH_MAX_RETRIEVABLE} records.")
    print(f"Opening a session over {term} to check that.")

    session = _session(term, base)
    if session is None:
        print("ERROR — no history session, so the session limit was not measured")
        return 1
    print(f"  session holds {session[0]} records")

    boundary = measure_boundary(session, base)
    if not boundary.ok:
        print(f"  largest served retstart: ERROR — {boundary.error}")
    else:
        served = int(boundary.value)
        agrees = "agrees" if served + 1 == EFETCH_MAX_RETRIEVABLE else "DISAGREES"
        print(
            f"  largest served retstart: {served}, so the session serves {served + 1} records"
            f" — {agrees} with EFETCH_MAX_RETRIEVABLE={EFETCH_MAX_RETRIEVABLE}"
        )

        straddle = measure_straddling_page(session, served, base)
        if not straddle.ok:
            print(f"  the page straddling the boundary: ERROR — {straddle.error}")
        else:
            start, delivered = straddle.value
            verdict = (
                "clamped silently — HTTP 200, no notice"
                if delivered < 500
                else "served whole, though it asked past the boundary — the limit moved"
                " under the probe; re-run"
            )
            print(
                f"  the page straddling the boundary: retstart={start} retmax=500 gave"
                f" {delivered} UIDs — {verdict}"
            )

    semantics = measure_slice_semantics(session, base)
    if not semantics.ok:
        print(f"  what retstart indexes: ERROR — {semantics.error}")
    else:
        same, delivered, wanted = semantics.value
        print(
            f"  what retstart indexes: {delivered} record elements against {wanted} UIDs —"
            f" {'the slice, in order' if same else 'NOT the slice; the stride assumption is void'}"
        )

    if args.skip_day_sizes:
        return 0

    # Three populations, measured once: a month first inside the window is not
    # re-fetched for the structural table, and the window is reported whole as
    # well as split, because "what share of ordinary days is fine" and "what
    # share of a sync window will fail" are different questions with different
    # answers, and only the second sizes the retry cost.
    window = [today - timedelta(days=n) for n in range(1, args.days + 1)]
    window_rows = measure_day_sizes(window, base)
    structural = set(_structural_days(today))
    outside = [day for day in sorted(structural) if day not in set(window)]

    report_day_sizes(
        [row for row in window_rows if row[0] not in structural],
        f"Ordinary days (last {args.days}, month firsts set aside)",
    )
    report_day_sizes(
        [row for row in window_rows if row[0] in structural] + measure_day_sizes(outside, base),
        "Month firsts and 1 January",
    )
    report_day_sizes(window_rows, f"Every day of the last {args.days} — one sync window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
