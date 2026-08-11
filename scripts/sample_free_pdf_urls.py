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

``europepmc`` was originally split further, into ``europepmc/in`` and
``europepmc/out`` by ``inEPMC``, meant to approximate "XML unusable" for the
subgroup Tier 1d actually reaches. Run 1 measured it and killed it:
``europepmc/out`` sampled zero URLs, and the reason is structural, not bad
luck. A ``?pdf=render`` URL embeds a PMC ID, so *every* record that carries one
is in Europe PMC by construction — the "out" half of the split could never be
populated, so it could never approximate anything. ``sample_europepmc`` now
returns one ``europepmc`` population; ``dois`` for the Unpaywall population
still comes from ``inEPMC != "Y"`` records in the same search, since those are
the ones that genuinely reach Tier 2.

Probes are a ranged GET for the first kilobyte, so measuring does not mean
downloading 900 whole PDFs, and they record both of bmlib's failure modes: the
status code, and whether the bytes begin ``%PDF``. A 403 is counted as a real
failure — bmlib would hit the identical 403 on the identical URL and fail the
identical way — but a 429 is not a property of the URL population at all: its
rate depends on how fast the *caller* asked, not on what is being asked for,
so it cannot inform a default (see "unmeasured" below).

A population that could not be sampled prints ``ERROR``, never a zero — a 0%
failure rate is what a perfectly healthy population looks like. An individual
probe that raises is the opposite: that is a real finding, one of the three
causes bmlib swallows, and it is counted.

The same principle covers throttling. A 429 or 503 is retried — honouring
``Retry-After`` when the server sends a usable integer, otherwise backing off
2s then 4s — for up to three attempts total; a probe still throttled after
that is ``measured=False``, not a failure, because bmlib never actually asked
the question "does this URL serve a PDF" and got an answer for it. And when
throttling leaves more than 20% of a population's attempts unmeasured
(``UNMEASURED_SHARE_ERROR_THRESHOLD``), the probes that did get through are
not a random sample of it — they are the *early* ones, made before the host
started refusing — so that population prints ``ERROR`` too, the same rule
extended from "zero sampled" to "too few measured to trust."

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
from collections.abc import Callable
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
from bmlib.publications.fetchers.biorxiv import BASE_URL as BIORXIV_BASE_URL
from bmlib.publications.fetchers.biorxiv import fetch_biorxiv

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
PAGE_SIZE = 100
PROBE_BYTES = 1024
# Minimum seconds between two requests to the *same* host, tracked
# independently per host (see `_make_pacer`). Run 1 paced every request off
# one shared clock, so one host's throttling paced every other host too, for
# no reason. Overridable with --per-host-interval when a re-run needs it
# wider.
PER_HOST_INTERVAL_SECONDS = 3.0
# The decision issue #68 feeds only needs to separate "under 5%" from "at or
# above 5%" — the Wilson interval half-width at a true 5% rate is about
# ±3.6% at this size — and 150 clean, actually-measured probes settle that.
# 150 clean probes are worth more evidence than 300 that ran into throttling,
# which is what run 1 spent its budget on instead.
DEFAULT_TARGET = 150
# A rate computed from probes that got through despite heavy throttling is
# not a random sample of the population: the ones that got through are the
# *early* ones, made before the host started refusing, and the later attempts
# it would have refused are exactly the ones missing from the sample. Past
# this share of unmeasured attempts, summarise() reports ERROR instead of a
# number that looks precise but is not evidence of anything.
UNMEASURED_SHARE_ERROR_THRESHOLD = 0.20
# Retry budget for a throttled (429/503) probe, and the backoff used when the
# server gives no usable Retry-After. Index 0 is the wait before the 2nd
# attempt, index 1 the wait before the 3rd.
MAX_PROBE_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 4.0)


@dataclass(frozen=True)
class ProbeOutcome:
    """What one download attempt would have produced for bmlib.

    Attributes:
        ok: Whether bmlib would have cached a PDF.
        cause: ``None`` on success, else the failure bucket — ``http-<status>``,
            ``not-a-pdf``, ``exception-<TypeName>``, or ``unmeasured-<status>``.
            The first three are kept apart because they are the three bmlib
            swallows, and merging them would answer #68's question with a
            number that cannot tell a full disk from a publisher 404.
        status: The HTTP status, when there was one.
        measured: Whether this probe reached a real answer. ``False`` when a
            429 or 503 persisted through every retry — the *sampler* was
            throttled, not the URL population, so ``summarise()`` must
            exclude it from the failure rate rather than count it as one.
    """

    ok: bool
    cause: str | None
    status: int | None
    measured: bool = True


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


def _sleep_for(seconds: float) -> None:
    """Sleep for *seconds*. Separated so tests can stub it out."""
    time.sleep(seconds)


def _retry_after_seconds(resp: Any) -> int | None:
    """Parse a ``Retry-After`` header's integer-seconds form.

    Args:
        resp: The throttled response.

    Returns:
        The number of seconds to wait, or ``None`` when the header is
        absent, an HTTP-date, or otherwise not a bare integer — the caller
        falls back to exponential backoff in that case. Handling the
        HTTP-date form is not worth it here: it is rare on a 429/503 in
        practice, and a wrong guess only costs one extra backoff step, not a
        wrong measurement.
    """
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe(client: Any, url: str) -> ProbeOutcome:
    """Attempt *url* the way ``_download_and_cache_pdf`` would, and classify it.

    A 429 or 503 is retried rather than reported immediately: that status
    means the probe itself could not be made, not that bmlib's download would
    have failed, and run 1 showed the difference matters — the sampler had
    throttled itself into being the dominant "failure" it reported. Up to
    ``MAX_PROBE_ATTEMPTS`` attempts total; a ``Retry-After`` header is honoured
    when it is a bare integer, otherwise ``RETRY_BACKOFF_SECONDS`` applies.

    Args:
        client: An HTTP client with ``get(url, headers=...)``.
        url: The PDF URL to probe.

    Returns:
        The outcome. ``measured`` is ``False`` only when every attempt ended
        in 429/503 — that probe never got an answer, and must not be counted
        as a failure by ``summarise()``.
    """
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        try:
            resp = client.get(url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"})
        except Exception as exc:
            return ProbeOutcome(ok=False, cause=f"exception-{type(exc).__name__}", status=None)
        if resp.status_code in (429, 503):
            if attempt == MAX_PROBE_ATTEMPTS:
                return ProbeOutcome(
                    ok=False,
                    cause=f"unmeasured-{resp.status_code}",
                    status=resp.status_code,
                    measured=False,
                )
            retry_after = _retry_after_seconds(resp)
            fallback = RETRY_BACKOFF_SECONDS[attempt - 1]
            # Clamped at zero because the header is remote input and this
            # sleep sits *outside* the try that wraps client.get: `int("-1")`
            # parses fine, `time.sleep(-1)` raises ValueError, and that
            # exception propagates out of probe() -> run() -> main(), losing
            # every population's data after ~25 minutes of live probing. The
            # clamp closes the `Retry-After: 0` case with it.
            delay: float = max(0.0, retry_after if retry_after is not None else fallback)
            _sleep_for(delay)
            continue
        # 206 Partial Content is the success for a ranged GET; a server
        # ignoring Range answers 200 with the whole body, which is equally
        # fine.
        if resp.status_code not in (200, 206):
            return ProbeOutcome(ok=False, cause=f"http-{resp.status_code}", status=resp.status_code)
        if not resp.content.startswith(b"%PDF"):
            return ProbeOutcome(ok=False, cause="not-a-pdf", status=resp.status_code)
        return ProbeOutcome(ok=True, cause=None, status=resp.status_code)
    raise AssertionError("unreachable: the loop above always returns")  # pragma: no cover


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
        The lines to print.

        A population that could not be sampled at all yields a single
        ``ERROR`` line and no rate, because a zero is exactly what a healthy
        population looks like. So does a population where throttling
        (429/503, even after retries) left more than
        ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of its attempts unmeasured: the
        probes that got through are not a random sample of it. Otherwise the
        failure rate and its Wilson interval are computed over the *measured*
        probes only, and the unmeasured count — if any — is reported on its
        own line rather than folded into either number.
    """
    if not outcomes:
        return [f"{name:<12} ERROR — could not sample this population; no rate is reported"]
    n = len(outcomes)
    unmeasured = [o for o in outcomes if not o.measured]
    if len(unmeasured) / n > UNMEASURED_SHARE_ERROR_THRESHOLD:
        return [
            f"{name:<12} ERROR — {len(unmeasured)}/{n} probes were throttled (429/503) "
            "even after retries; no rate is reported"
        ]
    measured = [o for o in outcomes if o.measured]
    m = len(measured)
    failures = [o for o in measured if not o.ok]
    lo, hi = wilson(len(failures), m)
    lines = [
        f"{name:<12} {m:>4} probed   "
        f"{len(failures):>4} failed = {100 * len(failures) / m:.1f}%   "
        f"95% CI [{100 * lo:.1f}%, {100 * hi:.1f}%]"
    ]
    if unmeasured:
        lines.append(
            f"{'':<12}   {len(unmeasured)} unmeasured (429/503 after retries; excluded above)"
        )
    for cause, count in sorted(Counter(o.cause for o in failures).items()):
        lines.append(f"{'':<12}   {cause:<28} {count:>4}")
    return lines


def _make_pacer(
    interval: float, clock: Callable[[], float] = time.monotonic
) -> Callable[[str], None]:
    """Build a function that paces requests to a minimum interval *per host*.

    A global pause (run 1's approach) punishes every host for one host's
    throttling — 300 requests to Europe PMC at one request per second is what
    triggered its 429s, and pausing bioRxiv in lockstep with it bought
    nothing. Tracking the last request time per host instead lets a
    cooperative host go at its own pace while a throttling one gets slowed
    down on its own.

    Args:
        interval: Minimum seconds between two requests to the same host.
        clock: Source of the current time, injected so tests can drive it
            without a real clock or a real sleep.

    Returns:
        A function ``pace(url)`` that sleeps only as long as *url*'s host
        still needs to have waited *interval* seconds since its last request
        through this same pacer.
    """
    last_request: dict[str, float] = {}

    def pace(url: str) -> None:
        host = urlsplit(url).netloc
        now = clock()
        last = last_request.get(host)
        if last is None:
            last_request[host] = now
            return
        remaining = interval - (now - last)
        if remaining > 0:
            _sleep_for(remaining)
            last_request[host] = now + remaining
        else:
            last_request[host] = now

    return pace


def sample_europepmc(
    client: Any, target: int, pace: Callable[[str], None]
) -> tuple[list[str], list[str]] | None:
    """Collect free PDF render URLs and the DOIs the Unpaywall population needs.

    Returns:
        ``(urls, dois)``, or ``None`` when the search could not be completed —
        the caller must then print ``ERROR`` rather than a rate. ``dois`` are
        the DOIs of records not already in Europe PMC (``inEPMC != "Y"``) seen
        in this same search — Unpaywall must be sampled from these, never from
        a separate search, which would destroy the comparability the
        measurement depends on.

        This used to also split ``urls`` by ``inEPMC``, printed as
        ``europepmc/in`` / ``europepmc/out``, meant to approximate "XML
        unusable" for the subgroup Tier 1d actually serves. See the module
        docstring for why that split was removed rather than fixed: it was
        structurally incapable of ever populating its "out" half.
    """
    query = "(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])"
    urls: list[str] = []
    dois: list[str] = []
    cursor = "*"
    while len(urls) < target:
        pace(EUROPE_PMC_SEARCH)
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
                urls.append(url)
        cursor = payload.get("nextCursorMark") or ""
        if not cursor:
            break
    return urls[:target], dois


def sample_unpaywall(
    client: Any, dois: list[str], email: str, target: int, pace: Callable[[str], None]
) -> list[str] | None:
    """Resolve DOIs to open-access PDF URLs exactly as ``_fetch_unpaywall`` does."""
    urls: list[str] = []
    asked = 0
    for doi in dois:
        if len(urls) >= target:
            break
        asked += 1
        pace(UNPAYWALL_BASE)
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


def sample_biorxiv(
    client: Any, target: int, pace: Callable[[str], None], server: str = "biorxiv"
) -> list[str] | None:
    """Collect the PDF URLs ``fetch_biorxiv`` itself builds."""
    urls: list[str] = []
    day = date.today() - timedelta(days=30)
    for _ in range(10):
        if len(urls) >= target:
            break
        records: list[Any] = []
        pace(BIORXIV_BASE_URL)
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


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser. Separated so tests can inspect defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address Unpaywall requires.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="URLs per population.")
    parser.add_argument(
        "--per-host-interval",
        type=float,
        default=PER_HOST_INTERVAL_SECONDS,
        help="Minimum seconds between two requests to the same host.",
    )
    return parser


def main() -> int:
    """Probe all populations and print the table."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    headers = {
        "User-Agent": f"bmlib-sampler/{__version__} (+https://github.com/hherb/bmlib; {args.email})"
    }
    pace = _make_pacer(args.per_host_interval)
    with httpx.Client(timeout=45.0, headers=headers, follow_redirects=True) as client:
        epmc = sample_europepmc(client, args.target, pace)
        if epmc is not None:
            epmc_urls, dois = epmc
        else:
            epmc_urls, dois = None, []
        unpaywall = (
            sample_unpaywall(client, dois, args.email, args.target, pace)
            if epmc is not None
            else None
        )
        biorxiv = sample_biorxiv(client, args.target, pace)

        def run(urls: list[str] | None) -> list[ProbeOutcome] | None:
            if urls is None:
                return None
            outcomes: list[ProbeOutcome] = []
            for url in urls:
                pace(url)
                outcomes.append(probe(client, url))
            return outcomes

        populations = [
            ("europepmc", run(epmc_urls)),
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
