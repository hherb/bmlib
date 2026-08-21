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
``usehistory=y`` and then paging efetch over the session it opens. Three
questions about that route are load-bearing and none of them is bmlib's to
answer, so all three are measured here rather than assumed:

1. **How far the session serves.** ``EFETCH_MAX_RETRIEVABLE`` says 9,999. The
   backend enforces it in two different ways — an outright HTTP 400 past the
   boundary, and a *silent* clamp on the page that straddles it — and the
   second is why a day over the cap is partitioned into Entrez-date ranges
   that each fit, rather than walked as far as it goes: a fetcher that asks
   for what the server will not send cannot tell that page apart from a day
   missing records. ``--partition`` is what measures that ladder.

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
look identical in a total, and only one of them is a finding. Excluding is not
enough on its own: past ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of a population a
share is not reported at all, because the days that got through a throttled run
are the *early* ones and the rest are exactly what is missing from the sample.
``main()`` exits non-zero when any probe or population came back unreportable,
so a run whose evidence is incomplete cannot pass for one whose evidence is not.

Costs about 150 small requests plus one ~200 KB XML page; nothing here downloads
a full page of 500 records. ``--skip-day-sizes`` runs the three session probes
alone, at a fixed 23 requests, and does **not** re-measure the day-size
populations.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# The retry rules and `wilson()` come from `_sampling` so a rule learned from
# one bad live run does not exist in two copies that can drift. The request
# interval below is this script's own, as `sample_databank_names.py`'s is: the
# shared pacer exists for the samplers that hit several hosts, and this one
# talks only to NCBI. `scripts/` is not a package; running a script puts this
# directory on sys.path as sys.path[0], and the test file loads it by path.
from _sampling import (
    MAX_PROBE_ATTEMPTS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _sleep_for,
    _throttle_delay,
    wilson,
)

from bmlib.publications.fetchers.pubmed import (
    EFETCH_MAX_RETRIEVABLE,
    EFETCH_PAGE_SIZE,
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

# The boundary search needs a session larger than `BOUNDARY_SEARCH_CEILING`,
# not merely larger than the cap — over a smaller one the search converges on
# the session's own size and prints it as the backend's limit. 90 days of
# indexing is ~500,000 records, comfortably past the ceiling below, and
# `measure_boundary` refuses rather than assuming it.
BOUNDARY_SESSION_DAYS = 90

# Upper bound for the search. Doubling from the known-good side would work too,
# but a fixed bound keeps the request count fixed — exactly 17 steps, since
# `while hi - lo > 1` halves 2**17 down to 1 — and so printable in advance.
BOUNDARY_SEARCH_CEILING = 131_072

# The ladder's root, restated rather than imported: this script is the evidence
# for the day-partitioning rule in `fetchers/pubmed.py`, and evidence gathered
# with the rule under test can only ever agree with it — the reason the
# planning function that rule uses is deliberately not named here, let alone
# imported (see `tests/test_efetch_paging_sampler.py`'s import guard). Kept
# identical in value to that module's own root constants but under a
# different name, so a diff renaming one cannot silently rename both.
LADDER_ROOT_LO = date(1900, 1, 1)
LADDER_ROOT_HI = date(2100, 12, 31)


@dataclass(frozen=True)
class Session:
    """A PubMed history session: how many records it holds, and how to read it.

    A dataclass rather than the ``tuple[int, str, str]`` this used to be, for
    the reason `sample_pdf_metadata_titles.py` gives for its own: two of the
    three fields are `str`, they are threaded positionally through four
    signatures, and nothing about a bare tuple stops them being transposed.
    """

    count: int
    web_env: str
    query_key: str


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

    def __post_init__(self) -> None:
        # `Probe()` — every field defaulted — otherwise reads as a *successful
        # measurement whose value is None*, which is the unmeasured probe
        # reported as a finding that this class exists to prevent. Tested
        # against `is None` rather than falsiness: `value=0` is what
        # `measure_boundary` legitimately returns for a cap of one record, and
        # `value=[]` is an empty UID slice.
        if self.error is not None and (self.refused or self.value is not None):
            raise ValueError("a failed probe carries neither a refusal nor a value")
        if self.error is None and not self.refused and self.value is None:
            raise ValueError("a probe that measured nothing must say why")

    @property
    def ok(self) -> bool:
        """Did the server answer? A refusal is an answer."""
        return self.error is None

    @property
    def measured(self) -> bool:
        """Is ``value`` readable? False for a refusal as well as a failure."""
        return self.error is None and not self.refused


@dataclass
class LadderReport:
    """What one day's Entrez-date ladder looked like.

    Every field defaults to "nothing measured yet" rather than a sentinel
    that could be mistaken for a real reading — the same reasoning
    :class:`Probe` gives for refusing to be constructed empty. ``parts=0`` and
    ``stuck=[]`` are legitimate defaults here, unlike ``Probe.value=None``:
    a report that stopped at the first unmeasured count really does have zero
    parts and no stuck dates to show, and `report_partitions` tells that case
    apart from a completed walk through :attr:`measured`, not through these.
    """

    day: date
    day_count: int | None = None
    root_count: int | None = None
    parts: int = 0
    stuck: list[tuple[date, int]] = field(default_factory=list)
    depth: int = 0
    calls: int = 0
    exact: bool | None = None

    @property
    def measured(self) -> bool:
        """Whether every probe this report needed actually came back.

        ``exact`` is only trustworthy when this is True: it is assigned once,
        after the whole descent returns without hitting an unmeasured count,
        so a count that failed mid-descent leaves ``exact`` at its ``None``
        default rather than at a value a missing request cannot support.
        """
        return self.day_count is not None and self.root_count is not None


def _get(url: str, params: dict[str, str]) -> tuple[int, str] | None:
    """Issue one paced E-utilities request; return ``(status, body)`` or None.

    The status is returned rather than raised on, because HTTP 400 is a
    *measurement* here — it is how the backend reports the boundary — while a
    connection failure is not. ``urllib`` rather than httpx for the reason
    ``scripts/sample_databank_names.py`` gives: this wants nothing httpx
    offers, and the standard library keeps the script runnable without the
    optional extras installed.

    A 429 or 503 is retried up to ``MAX_PROBE_ATTEMPTS`` times, honouring
    ``Retry-After`` through the shared two-ended clamp. Without that, a single
    transient throttle mid-binary-search abandons the run's headline
    measurement, and one during the day-size walk silently costs that day —
    and a throttled run is the expected shape here, not the exotic one: a full
    run makes 150-odd requests against NCBI's 3/s unauthenticated ceiling.
    """
    query = urllib.parse.urlencode(params)
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        time.sleep(REQUEST_INTERVAL_SECONDS)
        try:
            # The URL is a module constant; only the query is built here.
            with urllib.request.urlopen(f"{url}?{query}", timeout=60) as resp:  # noqa: S310
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < MAX_PROBE_ATTEMPTS:
                _sleep_for(_throttle_delay(e, attempt))
                continue
            return e.code, e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  request failed: {e}", file=sys.stderr)
            return None


def _esearch_root(
    term: str, base: dict[str, str], *, usehistory: bool = False
) -> ET.Element | None:
    """Run one esearch over *term* and return the document element, or None.

    Both callers ask for the metadata only (``retmax=0``) and both need the
    same reading of a reply that never arrived: a dead request, a non-200 and
    an unparsable body are all *unmeasured*, and each gives None rather than an
    empty element the caller would go on to read fields off.
    """
    params = {**base, "db": "pubmed", "term": term, "retmax": "0"}
    if usehistory:
        params["usehistory"] = "y"
    got = _get(ESEARCH, params)
    if got is None or got[0] != 200:
        return None
    try:
        return ET.fromstring(got[1])  # noqa: S314 - NCBI payload, no entities requested
    except ET.ParseError as e:
        print(f"  unparsable esearch response: {e}", file=sys.stderr)
        return None


def _count(term: str, base: dict[str, str]) -> int | None:
    """How many records match *term*, or None if the question could not be asked."""
    root = _esearch_root(term, base)
    if root is None:
        return None
    # E-utilities reports a failed search at HTTP 200 with an <ErrorList> or an
    # <ERROR>, and it still carries <Count>0</Count> — so reading the count
    # without looking for the error prints a backend degradation as a day
    # comfortably under the cap, which moves the share sizing #105 in the
    # reassuring direction. `_esearch` in the fetcher makes the same check.
    if root.find("ERROR") is not None or root.find("ErrorList") is not None:
        print("  esearch answered with an error document", file=sys.stderr)
        return None
    # `findtext("Count")` and not a regex over the body: <TranslationStack>
    # carries a <Count> per sub-term, so the first match in document order is
    # the top-level one only until the term becomes a conjunction.
    raw = root.findtext("Count")
    if raw is None or not raw.isdigit():
        # A 200 carrying no readable <Count> is an NCBI error document, not an
        # empty day; read as 0 it would print as a day comfortably under the cap.
        print("  esearch response carried no <Count>", file=sys.stderr)
        return None
    return int(raw)


def _session(term: str, base: dict[str, str]) -> Session | None:
    """Open a history session over *term*."""
    root = _esearch_root(term, base, usehistory=True)
    if root is None:
        return None
    count, web_env, query_key = (
        root.findtext("Count"),
        root.findtext("WebEnv"),
        root.findtext("QueryKey"),
    )
    if not (count or "").isdigit() or not web_env or not query_key:
        print("  esearch returned no usable history session", file=sys.stderr)
        return None
    return Session(int(count or 0), web_env, query_key)


def _uilist(session: Session, start: int, retmax: int, base: dict[str, str]) -> Probe:
    """The UIDs the session holds at ``[start, start + retmax)``.

    ``rettype=uilist`` is what makes the boundary search affordable: the same
    ``retstart`` arithmetic the record walk uses, answered in bytes rather than
    megabytes. A refusal is a measurement, so an HTTP 400 naming ``retstart``
    returns a Probe whose value is the refusal, not an error.

    The 400 is read for *which* 400 it is, and that is the mirror of the rule
    below it. A 400 from anything else — a WebEnv the backend has dropped, a
    malformed parameter, a backend error rendered as 400 — is not evidence of
    a limit, and read as one mid-search it collapses the upper bound onto
    wherever it started and prints a cap no server enforces.
    """
    got = _get(
        EFETCH,
        {
            **base,
            "db": "pubmed",
            "WebEnv": session.web_env,
            "query_key": session.query_key,
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
        if "retstart" not in body:
            return Probe(error=f"HTTP 400, but not the retstart refusal: {body[:200]}")
        return Probe(refused=True)
    if status != 200:
        return Probe(error=f"HTTP {status}")
    return Probe(value=[line.strip() for line in body.splitlines() if line.strip()])


def measure_boundary(session: Session, base: dict[str, str]) -> Probe:
    """The largest ``retstart`` the backend will serve, by binary search.

    Bounded below by a value known to work and above by one known to be
    refused; if either end fails to behave, the search is abandoned rather
    than reported, since a search over a broken bound converges on a number
    that means nothing.

    The ceiling is only *known* to be refused if it is past the end of the
    session as well as past the cap. Over a session smaller than the ceiling
    the search would converge on the session's own size and print it as the
    backend's limit — a `DISAGREES` line telling a maintainer to change the
    constant that gates every over-cap day.
    """
    if session.count <= BOUNDARY_SEARCH_CEILING:
        return Probe(
            error=(
                f"the session holds only {session.count} records, so a refusal past"
                f" retstart={BOUNDARY_SEARCH_CEILING} would measure the session, not the cap"
            )
        )

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


def measure_straddling_page(session: Session, last_retstart: int, base: dict[str, str]) -> Probe:
    """How many UIDs a page asking to cross the boundary is actually given.

    The quiet half of the limit. A page starting inside the served range and
    asking for more than remains is answered at HTTP 200, short, with nothing
    to say it was clamped. *start* is chosen so the page asks for exactly one
    record past the last one served — a page ending on the boundary is served
    whole and measures nothing.

    Sized in ``EFETCH_PAGE_SIZE`` rather than a literal 500, because the point
    of the probe is what happens to the page **bmlib actually asks for**: with
    the size hard-coded, raising the fetcher's page size leaves this measuring
    a page nothing issues. The returned triple carries the size the clamp
    should produce, so a page that came back short for some *other* reason is
    not printed as the clamp.
    """
    if session.count <= last_retstart + 1:
        return Probe(
            error=(
                f"the session holds {session.count} records and the boundary is at"
                f" {last_retstart}, so a short page would mean the session ran out,"
                " not that it was clamped"
            )
        )
    start = max(0, last_retstart + 2 - EFETCH_PAGE_SIZE)
    probe = _uilist(session, start, EFETCH_PAGE_SIZE, base)
    if not probe.ok:
        return Probe(error=probe.error)
    if probe.refused:
        return Probe(error=f"retstart={start} was refused, so no page straddles the boundary")
    return Probe(value=(start, len(probe.value), last_retstart + 1 - start))


def measure_slice_semantics(session: Session, base: dict[str, str]) -> Probe:
    """Are a page's record elements the UID slice it named, in order?

    The evidence for the fixed stride. Book chapters are included on purpose:
    ``<PubmedBookArticle>`` is a record element the fetcher counts as
    delivered and does not parse, and a comparison that dropped them would
    report a mismatch bmlib does not have.

    ``<DeleteCitation>`` is included too, and for a different reason than the
    fetcher's: this compares *slots in the UID list*, not deliveries, and a
    withdrawn UID occupies a slot even though ``_efetch_page`` deliberately
    excludes it from delivery. It carries **one PMID per deleted record**, so
    every one of them is expanded — reading only the first collapses N slots
    into one and prints "NOT the slice", which is the sampler telling a
    maintainer to make the very change #96 was closed for refusing.
    """
    uids = _uilist(session, 0, SLICE_SAMPLE, base)
    if not uids.measured:
        return Probe(error=uids.error or "the UID slice was refused")

    got = _get(
        EFETCH,
        {
            **base,
            "db": "pubmed",
            "WebEnv": session.web_env,
            "query_key": session.query_key,
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

    delivered: list[str] = []
    for child in root:
        if child.tag == "DeleteCitation":
            pmids = [el.text for el in child.findall("PMID")]
        elif child.tag in ("PubmedArticle", "PubmedBookArticle"):
            # The citation's own PMID is the first in document order;
            # <CommentsCorrections> and <ReferenceList> carry *other* records'
            # PMIDs deeper in the same subtree, so this cannot expand them all.
            pmids = [child.findtext(".//PMID")]
        else:
            continue
        if not pmids or any(text is None for text in pmids):
            return Probe(error=f"a <{child.tag}> element carried no readable PMID")
        delivered.extend(text for text in pmids if text is not None)
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


def _range_term(day: date, lo: date, hi: date) -> str:
    """Build the ESearch term for one rung of *day*'s Entrez-date ladder.

    Deliberately not the term builder `fetchers/pubmed.py` uses for the same
    E-utilities syntax, nor an import of it — arrived at by reading NCBI's
    own field documentation instead, since importing it would make this
    script confirm the rule instead of measuring it independently. See
    `tests/test_efetch_paging_sampler.py`'s import guard, which is what keeps
    a future refactor from "deduplicating" the two back together.
    """
    return (
        f'("{day:%Y/%m/%d}"[Date - Publication])'
        f' AND ("{lo:%Y/%m/%d}"[EDAT] : "{hi:%Y/%m/%d}"[EDAT])'
    )


def measure_partition(day: date, base: dict[str, str]) -> LadderReport:
    """Walk *day*'s Entrez-date ladder live and report its shape.

    A second, independent descent from the day-partitioning planner in
    `fetchers/pubmed.py` — deliberately not named or imported here, so a
    corpus labelled by the rule under test could only ever confirm that
    rule. The two disagree in shape as well as by not sharing code: this one
    collects every stuck Entrez date into :attr:`LadderReport.stuck` rather
    than raising on the first one, since a live run wants to see all of
    them, not stop at whichever the recursion happens to reach first.

    Every count that could not be made leaves the report unmeasured rather
    than contributing a zero: a zero is what an empty range legitimately
    looks like, and a failed request read as one would print a ladder that
    tiles when it does not.

    Args:
        day: The publication day to partition, as `fetch_pubmed` would query
            it under ``[Date - Publication]``.
        base: The ``tool``/``email``/``api_key`` parameters common to every
            E-utilities request, threaded through to :func:`_count`.

    Returns:
        The day's ladder shape: parts, depth, calls, any stuck Entrez dates,
        and whether the parts summed exactly to the root count.
    """
    report = LadderReport(day=day)
    day_count = _count(f'("{day:%Y/%m/%d}"[Date - Publication])', base)
    if day_count is None:
        return report
    report.day_count = day_count
    report.calls += 1

    root_count = _count(_range_term(day, LADDER_ROOT_LO, LADDER_ROOT_HI), base)
    if root_count is None:
        return report
    report.root_count = root_count
    report.calls += 1

    total = 0
    unmeasured = False

    def descend(lo: date, hi: date, n: int, depth: int) -> None:
        nonlocal total, unmeasured
        report.depth = max(report.depth, depth)
        if n <= 0:
            return
        if n <= EFETCH_MAX_RETRIEVABLE:
            report.parts += 1
            total += n
            return
        if lo == hi:
            report.stuck.append((lo, n))
            total += n
            return
        mid = lo + (hi - lo) // 2
        left = _count(_range_term(day, lo, mid), base)
        if left is None:
            unmeasured = True
            return
        report.calls += 1
        descend(lo, mid, left, depth + 1)
        descend(mid + timedelta(days=1), hi, n - left, depth + 1)

    descend(LADDER_ROOT_LO, LADDER_ROOT_HI, root_count, 0)
    if unmeasured:
        # The ladder is incomplete: some branch's count never arrived, so
        # `parts`, `stuck` and `total` are a partial walk, not a finding.
        # Clearing `root_count` is what `measured` reads to exclude this day
        # rather than let a half-finished descent print as a real report.
        report.root_count = None
        return report
    report.exact = total == root_count
    return report


def report_day_sizes(rows: list[tuple[date, int | None]], label: str) -> bool:
    """Print one population's over-cap share, excluding what could not be read.

    Returns whether the population was reportable, so a run whose evidence is
    incomplete can exit non-zero rather than printing an ERROR nobody reads.
    """
    measured = [(day, count) for day, count in rows if count is not None]
    unmeasured = len(rows) - len(measured)
    print(f"\n{label}: {len(measured)} days measured, {unmeasured} unmeasured")
    if not measured:
        print("  ERROR — nothing measured, so there is no share to report")
        return False
    if unmeasured / len(rows) > UNMEASURED_SHARE_ERROR_THRESHOLD:
        # Excluding an unread day from the denominator is necessary but not
        # sufficient: the days that got through a throttled run are the *early*
        # ones, so what survives is not a random sample of the population. Both
        # sibling samplers gate on this same threshold.
        print(
            f"  ERROR — {unmeasured} of {len(rows)} days went unmeasured, past the"
            f" {UNMEASURED_SHARE_ERROR_THRESHOLD:.0%} threshold; no share is reported"
        )
        return False
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
    return True


def report_straddling_page(session: Session, served: int, base: dict[str, str]) -> bool:
    """Print how a page asking to cross the boundary at *served* was answered.

    Returns whether the reading is reportable, which is not the same as whether
    the page was clamped: a page short of the clamp this boundary predicts is
    two probes disagreeing, and printing that as the clamp would put a number
    behind a claim nothing made.
    """
    straddle = measure_straddling_page(session, served, base)
    if not straddle.measured:
        print(f"  the page straddling the boundary: ERROR — {straddle.error}")
        return False

    start, delivered, expected = straddle.value
    # Only a page stopping exactly at the boundary the search just found is a
    # reading. Both other outcomes are that search and this probe disagreeing
    # about where the limit is, and both tell the operator to re-run — so both
    # fail the run. Marking only the second would exit 0 on a page served past
    # a boundary that was supposed to refuse it, which is the louder of the two.
    reportable = delivered == expected
    if reportable:
        verdict = "clamped silently — HTTP 200, no notice"
    elif delivered >= EFETCH_PAGE_SIZE:
        verdict = (
            "served whole, though it asked past the boundary — the limit moved"
            " under the probe; re-run"
        )
    else:
        verdict = (
            f"short, but {expected} were expected at this boundary — not a clean"
            " clamp, so the two probes disagree; re-run"
        )
    print(
        f"  the page straddling the boundary: retstart={start}"
        f" retmax={EFETCH_PAGE_SIZE} gave {delivered} UIDs — {verdict}"
    )
    return reportable


def report_session_probes(session: Session, base: dict[str, str]) -> bool:
    """Print what the three session probes settle; return whether all three did.

    A run that measured nothing must not exit like one that measured
    everything: these probes are the evidence for a hard-coded constant, and a
    green exit is what a scheduled re-run is judged by.
    """
    reportable = True

    boundary = measure_boundary(session, base)
    if not boundary.measured:
        print(f"  largest served retstart: ERROR — {boundary.error}")
        reportable = False
    else:
        served = int(boundary.value)
        agrees = "agrees" if served + 1 == EFETCH_MAX_RETRIEVABLE else "DISAGREES"
        print(
            f"  largest served retstart: {served}, so the session serves {served + 1} records"
            f" — {agrees} with EFETCH_MAX_RETRIEVABLE={EFETCH_MAX_RETRIEVABLE}"
        )
        if not report_straddling_page(session, served, base):
            reportable = False

    semantics = measure_slice_semantics(session, base)
    if not semantics.measured:
        print(f"  what retstart indexes: ERROR — {semantics.error}")
        reportable = False
    else:
        same, delivered, wanted = semantics.value
        print(
            f"  what retstart indexes: {delivered} record elements against {wanted} UIDs —"
            f" {'the slice, in order' if same else 'NOT the slice; the stride assumption is void'}"
        )
    return reportable


def report_day_size_populations(today: date, days: int, base: dict[str, str]) -> bool:
    """Size three day populations against the cap; return whether all three reported.

    Every day is measured once: a month first inside the window is not
    re-fetched for the structural table, and the window is reported whole as
    well as split, because "what share of ordinary days is fine" and "what
    share of a sync window will fail" are different questions with different
    answers, and only the second sizes the retry cost.
    """
    window = [today - timedelta(days=n) for n in range(1, days + 1)]
    window_rows = measure_day_sizes(window, base)
    structural = set(_structural_days(today))
    in_window = set(window)
    outside = [day for day in sorted(structural) if day not in in_window]

    # A list and not a generator: `all()` over a generator would stop at the
    # first unreportable population and leave the rest of them unprinted.
    reported = [
        report_day_sizes(
            [(day, count) for day, count in window_rows if day not in structural],
            f"Ordinary days (last {days}, month firsts set aside)",
        ),
        report_day_sizes(
            [(day, count) for day, count in window_rows if day in structural]
            + measure_day_sizes(outside, base),
            "Month firsts and 1 January",
        ),
        report_day_sizes(window_rows, f"Every day of the last {days} — one sync window"),
    ]
    return all(reported)


def report_partitions(days: list[date], base: dict[str, str]) -> bool:
    """Walk the Entrez-date ladder for each of *days* and print what it found.

    Follows `report_day_sizes`'s shape for the unmeasured half: a day whose
    ladder could not be walked is excluded from the population rather than
    printed as clean, and past `UNMEASURED_SHARE_ERROR_THRESHOLD` no finding
    is reported for the run at all — the days that got through a throttled
    run are the *early* ones, not a random sample of the population.

    Below that threshold, a day that *was* measured but came back inexact or
    carrying a stuck Entrez date still fails the run, even though neither
    touches the unmeasured share: that disagreement is the "0 stuck" claim
    itself failing, which is the one thing this mode exists to notice, and it
    is not softened by how many other days agreed.

    Args:
        days: The publication days to walk a full ladder for.
        base: The ``tool``/``email``/``api_key`` parameters common to every
            E-utilities request.

    Returns:
        Whether every day's population was reportable and every reported day
        tiled exactly with no stuck Entrez date.
    """
    reports = [measure_partition(day, base) for day in days]
    measured = [r for r in reports if r.measured]
    unmeasured = len(reports) - len(measured)
    print(
        f"\nEntrez-date ladder ({len(reports)} days): {len(measured)} measured,"
        f" {unmeasured} unmeasured"
    )
    if not measured:
        print("  ERROR — nothing measured, so there is no ladder to report")
        return False
    if unmeasured / len(reports) > UNMEASURED_SHARE_ERROR_THRESHOLD:
        print(
            f"  ERROR — {unmeasured} of {len(reports)} days went unmeasured, past the"
            f" {UNMEASURED_SHARE_ERROR_THRESHOLD:.0%} threshold; no ladder is reported"
        )
        return False

    all_held = True
    for r in reports:
        if not r.measured:
            print(f"  {r.day}  ERROR — the ladder could not be walked")
            all_held = False
            continue
        agrees = "==" if r.root_count == r.day_count else "!="
        verdict = "EXACT" if r.exact else "MISMATCH"
        if not r.exact or r.stuck:
            all_held = False
        stuck_note = f"  STUCK: {r.stuck}" if r.stuck else ""
        print(
            f"  {r.day}  day={r.day_count:>7}  root={r.root_count:>7} {agrees} day"
            f"  parts={r.parts:>3}  depth={r.depth:>2}  calls={r.calls:>3}  {verdict}{stuck_note}"
        )
    return all_held


def main() -> int:
    """Open a session wide enough to measure, then report what each probe settles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address NCBI asks callers for.")
    parser.add_argument("--api-key", default=None, help="Optional NCBI API key.")
    parser.add_argument(
        "--days", type=int, default=120, help="Ordinary days to size, counting back (default 120)."
    )
    parser.add_argument(
        "--skip-day-sizes", action="store_true", help="Probe the session limit only."
    )
    parser.add_argument(
        "--partition",
        action="store_true",
        help=(
            "Walk the Entrez-date ladder for the most recent --partition-days"
            " 1 Januarys — the largest known over-cap days — instead of the"
            " session probes and day-size populations. It is the live evidence"
            " for issue #105's 'no stuck Entrez date' claim. A full ladder is"
            " 40-51 ESearches per day, so --partition --partition-days 3 costs"
            " about 135 requests (measured: 51 + 40 + 44 over 2025/2024/2023);"
            " no history session is opened for it."
        ),
    )
    parser.add_argument(
        "--partition-days",
        type=int,
        default=3,
        help="How many recent 1 January days to walk the ladder for (default 3).",
    )
    args = parser.parse_args()

    base = {"tool": "bmlib", "email": args.email}
    if args.api_key:
        base["api_key"] = args.api_key

    today = date.today()

    if args.partition:
        targets = [date(today.year - n, 1, 1) for n in range(1, args.partition_days + 1)]
        print(f"Walking the Entrez-date ladder for {len(targets)} day(s):")
        for target in targets:
            print(f"  {target}")
        return 0 if report_partitions(targets, base) else 1

    window_start = today - timedelta(days=BOUNDARY_SESSION_DAYS)
    term = f'("{window_start:%Y/%m/%d}"[EDAT] : "{today:%Y/%m/%d}"[EDAT])'
    print(f"bmlib says a session serves its first {EFETCH_MAX_RETRIEVABLE} records.")
    print(f"Opening a session over {term} to check that.")

    session = _session(term, base)
    if session is None:
        print("ERROR — no history session, so the session limit was not measured")
        return 1
    print(f"  session holds {session.count} records")

    reportable = report_session_probes(session, base)
    if args.skip_day_sizes:
        return 0 if reportable else 1

    populations_reported = report_day_size_populations(today, args.days, base)
    return 0 if reportable and populations_reported else 1


if __name__ == "__main__":
    raise SystemExit(main())
