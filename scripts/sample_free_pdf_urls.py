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

"""Measure how often a PDF bmlib decides to download actually fails.

``FullTextService._download_and_cache_pdf`` swallows three distinct outcomes at
DEBUG — a non-200, a magic-byte rejection, and any exception — so a full disk
across a 10,000-paper run looks exactly like 10,000 publishers 404ing
(issue #68). Choosing a log level for those is a noise question, and this repo
settles noise questions by measuring rather than by taste.

What is measured is the failure rate **given bmlib already holds the URL**.
That is deliberately not "how often does Tier 1d fire": reachability governs how
often the code runs, not how often it fails when it does, and conflating the two
would let issue #79's fix silently move the number issue #68 was set from.

Three populations, one per call site of ``_download_and_cache_pdf``, because
they are not alike — Europe PMC serves its own host, Unpaywall points at
arbitrary repositories and often at a landing page rather than a PDF (which is
exactly the magic-byte rejection), and the fetchers build their own links:

===============  ========  ===================================================
Population       Tier      Drawn from
===============  ========  ===================================================
europepmc        1d        ``fullTextUrlList`` of one Europe PMC search
unpaywall        2         that search's DOIs, resolved as ``_fetch_unpaywall``
biorxiv          0         ``fetch_biorxiv`` itself
===============  ========  ===================================================

The first two come from the *same* papers, which makes their rates directly
comparable; Unpaywall's half is drawn from ``inEPMC != "Y"`` records, since
those are the ones that reach Tier 2. The third calls ``fetch_biorxiv`` rather
than re-spelling its URL template, so the URL under test cannot drift from the
one bmlib builds.

Probes are a ranged GET for the first kilobyte, so measuring does not mean
downloading 900 whole PDFs, and they record both of bmlib's failure modes: the
status code, and whether the bytes begin ``%PDF``.

A population that could not be sampled prints ``ERROR``, never a zero — a 0%
failure rate is what a perfectly healthy population looks like. An individual
probe that raises is the opposite: that is a real finding, one of the three
causes bmlib swallows, and it is counted.

    uv run python scripts/sample_free_pdf_urls.py --email you@example.org

Companion to ``scripts/sample_databank_names.py`` and
``scripts/sample_funder_names.py``. Run it before changing
``_FREE_PDF_AVAILABILITY_CODES`` or the log levels in
``_download_and_cache_pdf``.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

try:
    import httpx
except ImportError:  # pragma: no cover - the script is a live runner
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(1) from None

from bmlib import __version__
from bmlib.fulltext.service import _extract_free_pdf_url
from bmlib.publications.fetchers.biorxiv import fetch_biorxiv

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
PAGE_SIZE = 100
PROBE_BYTES = 1024
# One request per second per host. The probe walks third-party publisher hosts
# that never agreed to be measured; Europe PMC's own guidance is the ceiling,
# not the target.
REQUEST_INTERVAL_SECONDS = 1.0
DEFAULT_TARGET = 300


@dataclass(frozen=True)
class ProbeOutcome:
    """What one download attempt would have produced for bmlib.

    Attributes:
        ok: Whether bmlib would have cached a PDF.
        cause: ``None`` on success, else the failure bucket — ``http-<status>``,
            ``not-a-pdf``, or ``exception-<TypeName>``. The three are kept
            apart because they are the three bmlib swallows, and merging them
            would answer #68's question with a number that cannot tell a full
            disk from a publisher 404.
        status: The HTTP status, when there was one.
    """

    ok: bool
    cause: str | None
    status: int | None


def is_probeable(url: str) -> bool:
    """Whether *url* is one bmlib would actually fetch.

    The URLs come from third-party JSON — Europe PMC's ``fullTextUrlList`` and
    Unpaywall's locations — so the scheme is not bmlib's to assume. A
    ``file://`` or ``ftp://`` URL is not a *download failure*; counting it as
    one would put a scheme bmlib never fetches into the rate that sets a log
    level. The same reasoning as ``_normalise_base_url`` in the Ollama
    provider, for the same class of input.
    """
    return urlsplit(url).scheme in ("http", "https")


def probe(client: Any, url: str) -> ProbeOutcome:
    """Attempt *url* the way ``_download_and_cache_pdf`` would, and classify it.

    Args:
        client: An HTTP client with ``get(url, headers=...)``.
        url: The PDF URL to probe.

    Returns:
        The outcome, in one of the three buckets bmlib swallows.
    """
    try:
        resp = client.get(url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"})
    except Exception as exc:
        return ProbeOutcome(ok=False, cause=f"exception-{type(exc).__name__}", status=None)
    # 206 Partial Content is the success for a ranged GET; a server ignoring
    # Range answers 200 with the whole body, which is equally fine.
    if resp.status_code not in (200, 206):
        return ProbeOutcome(ok=False, cause=f"http-{resp.status_code}", status=resp.status_code)
    if not resp.content.startswith(b"%PDF"):
        return ProbeOutcome(ok=False, cause="not-a-pdf", status=resp.status_code)
    return ProbeOutcome(ok=True, cause=None, status=resp.status_code)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A Wilson score interval for *k* failures in *n* attempts.

    An interval rather than a point estimate because issue #68's rule has a
    threshold in it (5%), and a point estimate near that threshold would
    misrepresent what the sample settles: 15 failures in 300 is exactly 5.0%
    and its interval runs from 3.1% to 8.1%.

    Raises:
        ValueError: If *n* is zero. There is no interval over no attempts, and
            returning ``(0.0, 0.0)`` would print as a perfect score.
    """
    if n <= 0:
        raise ValueError("no attempts to compute an interval over")
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def summarise(name: str, outcomes: list[ProbeOutcome] | None) -> list[str]:
    """Render one population's result as report lines.

    Args:
        name: The population's name.
        outcomes: Its probe outcomes, or ``None`` when the *sampling* failed.
            An empty list is treated the same as ``None``: zero URLs sampled is
            not a population with no failures.

    Returns:
        The lines to print. A population that could not be measured yields a
        single ``ERROR`` line and no rate, because a zero is exactly what a
        healthy population looks like.
    """
    if not outcomes:
        return [f"{name:<12} ERROR — could not sample this population; no rate is reported"]
    n = len(outcomes)
    failures = [o for o in outcomes if not o.ok]
    lo, hi = wilson(len(failures), n)
    lines = [
        f"{name:<12} {n:>4} probed   "
        f"{len(failures):>4} failed = {100 * len(failures) / n:.1f}%   "
        f"95% CI [{100 * lo:.1f}%, {100 * hi:.1f}%]"
    ]
    for cause, count in sorted(Counter(o.cause for o in failures).items()):
        lines.append(f"{'':<12}   {cause:<28} {count:>4}")
    return lines


def _sleep() -> None:
    """Pace requests. Separated so tests can stub it out."""
    time.sleep(REQUEST_INTERVAL_SECONDS)


def sample_europepmc(client: Any, target: int) -> tuple[list[str], list[str], list[str]] | None:
    """Collect free PDF render URLs, split by whether the record is in EPMC.

    Returns:
        ``(in_epmc_urls, not_in_epmc_urls, dois)``, or ``None`` when the search
        could not be completed — the caller must then print ``ERROR`` rather
        than a rate. The split is the spec's stated approximation of "XML
        unusable", which is the subgroup Tier 1d actually reaches; measuring it
        exactly would cost one ``fullTextXML`` request per sampled record.
        ``dois`` are the DOIs of records not already in EPMC (``inEPMC !=
        "Y"``) seen in this same search — Unpaywall must be sampled from these,
        never from a separate search, which would destroy the comparability
        the measurement depends on.
    """
    query = "(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])"
    inside: list[str] = []
    outside: list[str] = []
    dois: list[str] = []
    cursor = "*"
    while len(inside) + len(outside) < target:
        _sleep()
        try:
            resp = client.get(
                EUROPE_PMC_SEARCH,
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": PAGE_SIZE,
                    "cursorMark": cursor,
                },
            )
            if resp.status_code != 200:
                print(f"  Europe PMC search HTTP {resp.status_code}", file=sys.stderr)
                return None
            payload = resp.json()
        except Exception as exc:
            print(f"  Europe PMC search failed: {exc}", file=sys.stderr)
            return None
        hits = payload.get("resultList", {}).get("result", [])
        if not hits:
            break
        for hit in hits:
            if hit.get("doi") and hit.get("inEPMC") != "Y":
                dois.append(hit["doi"])
            url = _extract_free_pdf_url(hit)
            if url and is_probeable(url):
                (inside if hit.get("inEPMC") == "Y" else outside).append(url)
        cursor = payload.get("nextCursorMark") or ""
        if not cursor:
            break
    return inside[:target], outside[:target], dois


def sample_unpaywall(client: Any, dois: list[str], email: str, target: int) -> list[str] | None:
    """Resolve DOIs to open-access PDF URLs exactly as ``_fetch_unpaywall`` does."""
    urls: list[str] = []
    asked = 0
    for doi in dois:
        if len(urls) >= target:
            break
        asked += 1
        _sleep()
        try:
            resp = client.get(
                f"{UNPAYWALL_BASE}/{quote(doi, safe='')}?email={quote(email, safe='')}"
            )
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                print(f"  Unpaywall HTTP {resp.status_code} for {doi}", file=sys.stderr)
                continue
            data = resp.json()
        except Exception as exc:
            print(f"  Unpaywall failed for {doi}: {exc}", file=sys.stderr)
            continue
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        if not url:
            for loc in data.get("oa_locations") or []:
                url = loc.get("url_for_pdf") or loc.get("url")
                if url:
                    break
        if url and is_probeable(url):
            urls.append(url)
    # Nothing resolved out of nothing asked is a failed sample, not a finding.
    return urls if asked else None


def sample_biorxiv(client: Any, target: int, server: str = "biorxiv") -> list[str] | None:
    """Collect the PDF URLs ``fetch_biorxiv`` itself builds."""
    urls: list[str] = []
    day = date.today() - timedelta(days=30)
    for _ in range(10):
        if len(urls) >= target:
            break
        records: list[Any] = []
        _sleep()
        try:
            fetch_biorxiv(client, day, on_record=records.append, server=server)
        except Exception as exc:
            print(f"  bioRxiv fetch failed for {day}: {exc}", file=sys.stderr)
            day -= timedelta(days=1)
            continue
        for record in records:
            for entry in record.fulltext_sources:
                if entry.format == "pdf" and is_probeable(entry.url):
                    urls.append(entry.url)
        day -= timedelta(days=1)
    return urls or None


def main() -> int:
    """Probe all three populations and print the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address Unpaywall requires.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="URLs per population.")
    args = parser.parse_args()

    headers = {
        "User-Agent": f"bmlib-sampler/{__version__} (+https://github.com/hherb/bmlib; {args.email})"
    }
    with httpx.Client(timeout=45.0, headers=headers, follow_redirects=True) as client:
        epmc = sample_europepmc(client, args.target)
        if epmc is not None:
            epmc_in, epmc_out, dois = epmc
        else:
            epmc_in, epmc_out, dois = None, None, []
        unpaywall = (
            sample_unpaywall(client, dois, args.email, args.target) if epmc is not None else None
        )
        biorxiv = sample_biorxiv(client, args.target)

        def run(urls: list[str] | None) -> list[ProbeOutcome] | None:
            if urls is None:
                return None
            outcomes: list[ProbeOutcome] = []
            for url in urls:
                _sleep()
                outcomes.append(probe(client, url))
            return outcomes

        populations = [
            ("europepmc/in", run(epmc_in)),
            ("europepmc/out", run(epmc_out)),
            ("unpaywall", run(unpaywall)),
            ("biorxiv", run(biorxiv)),
        ]

    print("\nPDF download failure rates, by population\n")
    for name, outcomes in populations:
        for line in summarise(name, outcomes):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
