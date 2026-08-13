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
``Retry-After`` when the server sends a usable integer, clamped to
``MAX_RETRY_AFTER_SECONDS``, otherwise backing off 2s then 4s — for up to
three attempts total (``MAX_PROBE_ATTEMPTS``, so two retries); an attempt
still throttled after that is ``measured=False``, not a failure, because
bmlib never actually asked the question "does this URL serve a PDF" and got
an answer for it. And when throttling leaves more than 20% of a population's
attempts unmeasured (``UNMEASURED_SHARE_ERROR_THRESHOLD``), the attempts that
did get through are not a random sample of it — they are the *early* ones,
made before the host started refusing — so that population prints ``ERROR``
too, the same rule extended from "zero sampled" to "too few measured to
trust."

That rule covers the Unpaywall *resolution* phase as well as the probe phase,
because that is where Unpaywall's own rate limiter actually bites. A DOI
throttled out of resolution is an attempt on the population that reached no
answer, so it is carried into the outcome list as an unmeasured attempt
rather than silently shrinking the denominator.

The second table is the access-label distribution: every ``documentStyle=pdf``
entry the search returned, counted by ``(availability, availabilityCode)`` and
marked with whether ``_entry_is_free`` takes it. It is counted over *all* pdf
entries, not the accepted ones — a distribution filtered by the allow-list
could only ever confirm the allow-list, and issue #79 was exactly a value that
never appeared in what bmlib accepted. This is the table that makes the
allow-list answerable to the records, the way ``sample_databank_names.py``
does for the DataBankName lists.

Exits non-zero if any population printed ``ERROR`` instead of a rate.

    uv run python scripts/sample_free_pdf_urls.py --email you@example.org

Companion to ``scripts/sample_databank_names.py`` and
``scripts/sample_funder_names.py``. Run it before changing
``_FREE_PDF_AVAILABILITY_CODES`` or the log levels in
``_report_pdf_download_failure``.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

try:
    import httpx
except ImportError:  # pragma: no cover - the script is a live runner
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(1) from None

# Pacing, throttling and the interval live in `_sampling` so that this script
# and `sample_pdf_metadata_titles.py` cannot drift apart on rules that were
# learned from a live run gone wrong. `scripts/` is not a package; running a
# script puts this directory on sys.path as sys.path[0], and the test files
# that load one by path insert it explicitly.
from _sampling import (
    MAX_PROBE_ATTEMPTS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _make_pacer,
    _sleep_for,
    _throttle_delay,
    is_probeable,
    wilson,
)

from bmlib import __version__
from bmlib.fulltext.service import _entry_is_free, _extract_free_pdf_url, _pick_oa_pdf_url
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
# How many days back to walk for the bioRxiv population before giving up on
# reaching --target. Ten days of postings is several hundred preprints, so
# this bounds a fetch loop rather than limiting the sample.
BIORXIV_DAYS_TO_WALK = 10


@dataclass(frozen=True)
class ProbeOutcome:
    """What one download attempt would have produced for bmlib.

    Attributes:
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

    ``ok`` is a property, not a field, for the reason ``_TierFailures``'
    docstring gives for holding no two fields describing one event: a stored
    ``ok=True`` beside ``cause="http-403"`` constructs happily and would drop
    that probe out of ``failures``, silently lowering the very rate this
    script exists to measure and that a production log level is set from.
    """

    cause: str | None
    status: int | None
    measured: bool = True

    @property
    def ok(self) -> bool:
        """Whether bmlib would have cached a PDF. Success is the absence of a cause."""
        return self.cause is None


@dataclass(frozen=True)
class EuropePMCSample:
    """One Europe PMC search, read for everything the two allow-lists rest on.

    Attributes:
        urls: The free PDF render URLs ``_extract_free_pdf_url`` accepted —
            the ``europepmc`` population.
        dois: DOIs of records not already in Europe PMC (``inEPMC != "Y"``),
            which are the ones that reach Tier 2. Unpaywall must be sampled
            from these, never from a separate search, or the two populations
            stop being comparable.
        availability: How every ``documentStyle=pdf`` entry in the search
            labelled its access, keyed ``(availability, availabilityCode)``.
            This is the evidence behind ``_FREE_PDF_AVAILABILITY_CODES``, and
            it is collected over *all* pdf entries rather than the accepted
            ones — a distribution counted after the allow-list has filtered it
            could only ever confirm the allow-list. Issue #79 was precisely a
            value that never appeared in what bmlib accepted.
    """

    urls: list[str]
    dois: list[str]
    availability: Counter[tuple[str, str]]


@dataclass(frozen=True)
class UnpaywallSample:
    """The Unpaywall population, and how much of it was never reached.

    Attributes:
        urls: The open-access PDF URLs that resolved.
        unmeasured: How many DOIs were throttled out of the resolution phase
            entirely. Carried rather than discarded so ``main()`` can put them
            back into the population as unmeasured attempts, where the same
            ``UNMEASURED_SHARE_ERROR_THRESHOLD`` rule that governs throttled
            *probes* applies to throttled *resolutions* too.
    """

    urls: list[str]
    unmeasured: int


def count_pdf_availability(result: dict[str, Any]) -> Counter[tuple[str, str]]:
    """Tally how one search hit's PDF entries label their access.

    Args:
        result: One Europe PMC search result.

    Returns:
        A count keyed ``(availability, availabilityCode)``, with ``"-"`` for
        either value absent and ``"?"`` for one present in a shape that is not
        a string. The two are distinguished because
        ``_entry_is_free`` distinguishes them: it reads a non-string code as
        no code at all and falls through to the label, so a run where those
        differ is a run where the fallback path is load-bearing.

        Only ``documentStyle == "pdf"`` entries are counted; the allow-list
        governs nothing else.
    """
    counts: Counter[tuple[str, str]] = Counter()
    url_list = result.get("fullTextUrlList")
    if not isinstance(url_list, dict):
        return counts
    entries = url_list.get("fullTextUrl")
    if not isinstance(entries, list):
        return counts
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("documentStyle") != "pdf":
            continue
        availability = _label_of(entry.get("availability"))
        code = _label_of(entry.get("availabilityCode"))
        counts[(availability, code)] += 1
    return counts


def _label_of(value: object) -> str:
    """Render one access field for the distribution table."""
    if value is None:
        return "-"
    return value if isinstance(value, str) else "?"


def summarise_availability(counts: Counter[tuple[str, str]]) -> list[str]:
    """Render the access-label distribution, and say which values bmlib takes.

    Args:
        counts: The tally from :class:`EuropePMCSample`.

    Returns:
        The lines to print. An empty tally prints ``ERROR`` rather than an
        empty table, on the same principle as :func:`summarise`: no entries
        seen is a sample that failed, not a vocabulary with nothing in it.

        Each row is marked with whether ``_entry_is_free`` would accept it, so
        a value bmlib has never evaluated reads as a finding at a glance
        rather than having to be cross-checked against the source. That is the
        drift issue #79 went unnoticed as: ``Open access``/``OA`` was 95.7% of
        the PDF entries on offer and bmlib took none of it.
    """
    if not counts:
        return ["availability  ERROR — no documentStyle=pdf entries were seen; nothing to report"]
    total = sum(counts.values())
    lines = [f"PDF entries by access label ({total} entries)", ""]
    for (availability, code), count in counts.most_common():
        taken = _entry_is_free({"availability": availability, "availabilityCode": code})
        lines.append(
            f"  {availability:<24} {code:<6} {count:>5}  {100 * count / total:>5.1f}%  "
            f"{'taken' if taken else 'SKIPPED'}"
        )
    return lines


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
            return ProbeOutcome(cause=f"exception-{type(exc).__name__}", status=None)
        if resp.status_code in (429, 503):
            if attempt == MAX_PROBE_ATTEMPTS:
                return ProbeOutcome(
                    cause=f"unmeasured-{resp.status_code}",
                    status=resp.status_code,
                    measured=False,
                )
            _sleep_for(_throttle_delay(resp, attempt))
            continue
        # 206 Partial Content is the success for a ranged GET; a server
        # ignoring Range answers 200 with the whole body, which is equally
        # fine.
        if resp.status_code not in (200, 206):
            return ProbeOutcome(cause=f"http-{resp.status_code}", status=resp.status_code)
        if not resp.content.startswith(b"%PDF"):
            return ProbeOutcome(cause="not-a-pdf", status=resp.status_code)
        return ProbeOutcome(cause=None, status=resp.status_code)
    raise AssertionError("unreachable: the loop above always returns")  # pragma: no cover


def is_reportable(outcomes: list[ProbeOutcome] | None) -> bool:
    """Whether a population yielded a rate rather than an ``ERROR`` line.

    The single predicate behind both of :func:`summarise`'s ERROR branches and
    :func:`main`'s exit status, so a caller's exit code cannot disagree with
    what was printed.

    Args:
        outcomes: A population's attempts, or ``None`` if it could not be
            sampled.

    Returns:
        ``False`` when nothing was sampled, or when more than
        ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of the attempts never reached an
        answer.
    """
    if not outcomes:
        return False
    unmeasured = sum(1 for o in outcomes if not o.measured)
    return unmeasured / len(outcomes) <= UNMEASURED_SHARE_ERROR_THRESHOLD


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
    if not is_reportable(outcomes):
        return [
            f"{name:<12} ERROR — {len(unmeasured)}/{n} attempts were throttled (429/503) "
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


def sample_europepmc(
    client: Any, target: int, pace: Callable[[str], None]
) -> EuropePMCSample | None:
    """Collect free PDF render URLs, the Unpaywall DOIs, and the access vocabulary.

    Returns:
        An :class:`EuropePMCSample`, or ``None`` when the search could not be
        completed — the caller must then print ``ERROR`` rather than a rate.

        This used to also split ``urls`` by ``inEPMC``, printed as
        ``europepmc/in`` / ``europepmc/out``, meant to approximate "XML
        unusable" for the subgroup Tier 1d actually serves. See the module
        docstring for why that split was removed rather than fixed: it was
        structurally incapable of ever populating its "out" half.
    """
    query = "(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])"
    urls: list[str] = []
    dois: list[str] = []
    availability: Counter[tuple[str, str]] = Counter()
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
            availability.update(count_pdf_availability(hit))
            url = _extract_free_pdf_url(hit)
            if url and is_probeable(url):
                urls.append(url)
        cursor = payload.get("nextCursorMark") or ""
        if not cursor:
            break
    return EuropePMCSample(urls=urls[:target], dois=dois, availability=availability)


def sample_unpaywall(
    client: Any, dois: list[str], email: str, target: int, pace: Callable[[str], None]
) -> UnpaywallSample | None:
    """Resolve DOIs to open-access PDF URLs exactly as ``_fetch_unpaywall`` does.

    Args:
        client: An HTTP client with ``get(url)``.
        dois: DOIs from the same Europe PMC search, so the two populations
            stay comparable.
        email: The contact address Unpaywall requires.
        target: Stop once this many URLs have resolved.
        pace: Per-host pacer.

    Returns:
        An :class:`UnpaywallSample`, or ``None`` when no DOI was even asked
        about — nothing resolved out of nothing asked is a failed sample, not
        a finding.

        A 429/503 here is retried and then counted as **unmeasured**, exactly
        as :func:`probe` treats one, rather than dropped. Unpaywall's own rate
        limiter bites in this phase and not in the probe phase, so leaving it
        uncounted was the one place the script's central rule did not hold: a
        run whose resolutions were throttled printed a confident rate over the
        handful that got through, with nothing recording that the rest of the
        population was never reached.
    """
    urls: list[str] = []
    asked = 0
    unmeasured = 0
    for doi in dois:
        if len(urls) >= target:
            break
        asked += 1
        resolved, was_measured = _resolve_one_doi(client, doi, email, pace)
        if not was_measured:
            unmeasured += 1
            continue
        if resolved and is_probeable(resolved):
            urls.append(resolved)
    if not asked:
        return None
    return UnpaywallSample(urls=urls, unmeasured=unmeasured)


def _resolve_one_doi(
    client: Any, doi: str, email: str, pace: Callable[[str], None]
) -> tuple[str | None, bool]:
    """Ask Unpaywall for one DOI's best PDF URL.

    Returns:
        ``(url, measured)``. ``measured`` is ``False`` only when 429/503
        persisted through every retry, which means the question was never
        answered for this DOI and it must not be read as "no open access".
        A 404 *is* an answer — Unpaywall has no record — and reports
        ``(None, True)``.
    """
    url = f"{UNPAYWALL_BASE}/{quote(doi, safe='')}?email={quote(email, safe='')}"
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        pace(UNPAYWALL_BASE)
        try:
            resp = client.get(url)
            if resp.status_code in (429, 503):
                if attempt == MAX_PROBE_ATTEMPTS:
                    print(f"  Unpaywall throttled for {doi}; unmeasured", file=sys.stderr)
                    return None, False
                _sleep_for(_throttle_delay(resp, attempt))
                continue
            if resp.status_code == 404:
                return None, True
            if resp.status_code != 200:
                print(f"  Unpaywall HTTP {resp.status_code} for {doi}", file=sys.stderr)
                return None, True
            return _pick_oa_pdf_url(resp.json()), True
        except Exception as exc:
            print(f"  Unpaywall failed for {doi}: {exc}", file=sys.stderr)
            return None, True
    raise AssertionError("unreachable: the loop above always returns")  # pragma: no cover


def sample_biorxiv(
    client: Any, target: int, pace: Callable[[str], None], server: str = "biorxiv"
) -> list[str] | None:
    """Collect the PDF URLs ``fetch_biorxiv`` itself builds.

    Truncated to *target* like the other two populations. bioRxiv posts well
    over a hundred preprints a day and the length is only re-checked between
    days, so an untruncated return probed several hundred URLs against one
    third-party host — spending the run's time budget on a sample no more
    informative than the one that was asked for.
    """
    urls: list[str] = []
    day = date.today() - timedelta(days=30)
    for _ in range(BIORXIV_DAYS_TO_WALK):
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
    return urls[:target] or None


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


def probe_all(
    client: Any, urls: list[str] | None, pace: Callable[[str], None]
) -> list[ProbeOutcome] | None:
    """Probe every URL in *urls*, pacing each request by host.

    ``None`` in, ``None`` out: a population that could not be sampled must
    stay unsampled all the way to :func:`summarise`, which prints ``ERROR``
    for it. An empty list in its place would be reported as a rate.
    """
    if urls is None:
        return None
    outcomes: list[ProbeOutcome] = []
    for url in urls:
        pace(url)
        outcomes.append(probe(client, url))
    return outcomes


def main() -> int:
    """Probe all populations and print the tables.

    Returns:
        ``1`` if any population printed ``ERROR`` instead of a rate, else
        ``0``. A caller — a re-run loop, or a maintainer chaining this into
        anything — otherwise cannot tell "measured, here are the rates" from
        "nothing could be sampled", which is the script's own central
        distinction made illegible at its outermost boundary.
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    headers = {
        "User-Agent": f"bmlib-sampler/{__version__} (+https://github.com/hherb/bmlib; {args.email})"
    }
    pace = _make_pacer(args.per_host_interval)
    with httpx.Client(timeout=45.0, headers=headers, follow_redirects=True) as client:
        epmc = sample_europepmc(client, args.target, pace)
        if epmc is None:
            epmc_outcomes: list[ProbeOutcome] | None = None
            unpaywall_outcomes: list[ProbeOutcome] | None = None
            availability: Counter[tuple[str, str]] = Counter()
        else:
            availability = epmc.availability
            epmc_outcomes = probe_all(client, epmc.urls, pace)
            unpaywall = sample_unpaywall(client, epmc.dois, args.email, args.target, pace)
            unpaywall_outcomes = _unpaywall_population(client, unpaywall, pace)
        biorxiv_outcomes = probe_all(client, sample_biorxiv(client, args.target, pace), pace)

    populations = [
        ("europepmc", epmc_outcomes),
        ("unpaywall", unpaywall_outcomes),
        ("biorxiv", biorxiv_outcomes),
    ]

    print("\nPDF download failure rates, by population\n")
    for name, outcomes in populations:
        for line in summarise(name, outcomes):
            print(line)
    print()
    for line in summarise_availability(availability):
        print(line)

    reportable = [is_reportable(o) for _, o in populations] + [bool(availability)]
    return 0 if all(reportable) else 1


def _unpaywall_population(
    client: Any, sample: UnpaywallSample | None, pace: Callable[[str], None]
) -> list[ProbeOutcome] | None:
    """Probe an Unpaywall sample, putting its throttled resolutions back in.

    A DOI that could not be resolved because Unpaywall throttled it is an
    attempt on this population that never reached an answer — exactly what
    ``measured=False`` means for a probe — so it is carried into the outcome
    list rather than dropped. Without it, a resolution phase that was throttled
    away prints as a confident rate over whatever got through first.
    """
    if sample is None:
        return None
    outcomes = probe_all(client, sample.urls, pace) or []
    return (
        outcomes
        + [ProbeOutcome(cause="unmeasured-resolution", status=None, measured=False)]
        * sample.unmeasured
    )


if __name__ == "__main__":
    raise SystemExit(main())
