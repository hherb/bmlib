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

"""Measure what the five dropped responses in ``bmlib.transparency`` actually are.

``TransparencyAnalyzer`` makes five requests it can throw away —
``_query_crossref``, ``_query_europepmc``, ``_query_pubmed``,
``_query_openalex`` and ``_check_trial_results``. Until issue #193 a non-200
from any of them fell off the end of the method with **no log line at any
level**, so the level is not being raised here, it is being *invented*: there
was nothing to raise.

That makes this script the evidence for a level rather than a re-check of one.
This repository's rule is that a diagnostic's level is a claim that has to be
measured, and that **the branch it sits on must be no wider than the draw** —
issue #191 was exactly a DEBUG measured on 404s and applied to every status
code. So what is measured here is, per endpoint, the whole status distribution
over identifiers shaped like the ones bmlib is handed, and the levels in
``analyzer.py`` cite these numbers.

**Run it before changing any of those levels.**

    uv run python scripts/sample_api_failures.py --email you@example.org

Exits non-zero if any population printed ``ERROR`` instead of a distribution.

## What the population is, and why the draw is stratified

The identifiers come from one Europe PMC search per stratum, **stratified by
source (MED/PMC/PPR) and publication year**. A cursor page is a contiguous
block of accessions, so an unstratified walk measures one corner of the corpus
— PR #189 drew ``IN_EPMC:Y`` unstratified and read 48% 404 where a stratified
draw of the same field read 3%. Needed twice already; this is the third.

The draw uses the Europe PMC search, which is *also* one of the endpoints
under test. That is not circular: the draw issues a stratified page query and
the probe issues the single-record lookup ``_fetch_europepmc`` builds
(``DOI:"…"`` or ``EXT_ID:…``), which is a different request against the same
host. A draw page that fails costs its stratum and is reported, never
back-filled from another stratum, which would quietly re-weight the sample.

## Two things it deliberately imports, against the samplers' usual rule

The other live runners in this directory carry their own copies of the
predicates under test, because a corpus labelled by the rule under test can
only confirm that rule. Neither import here is such a predicate:

* **The URLs** come from ``analyzer.py``'s own constants, so this script
  cannot measure an endpoint bmlib does not call. That is the direct lesson of
  issue #184, where the module's two Europe PMC literals had drifted apart and
  every full-text fetch 404'd for a release; and it is what PR #192's probe
  meant by addressing records "exactly as ``_check_europepmc`` addresses them".
* **The trial ids** come from ``_find_trial_ids`` and ``_parse_pubmed_signals``,
  because the CT.gov population is *the accessions bmlib asks about* and
  nothing else. A looser scrape would put ids bmlib never requests into the
  denominator; since a made-up accession is precisely what 404s, that would
  manufacture the ordinariness a DEBUG level would then rest on.

Companion to ``scripts/sample_databank_names.py``,
``scripts/sample_free_pdf_urls.py`` and ``scripts/sample_efetch_paging.py``,
and it shares their rules: the ``_sampling`` per-host pacer and two-ended
``Retry-After`` clamp, an attempt that never reached an answer entering no
denominator, and a population past ``UNMEASURED_SHARE_ERROR_THRESHOLD``
reporting ERROR rather than a share. **Run one live probe at a time** — the
pacer is per-process (issue #179), so two concurrent runs double the rate
against one host.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - the script is a live runner
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(1) from None

# Pacing, throttling and the interval live in `_sampling` so a rule learned
# from one bad live run does not exist in two copies that can drift.
# `scripts/` is not a package; running a script puts this directory on
# sys.path as sys.path[0], and the test files that load one by path insert it
# explicitly.
from _sampling import (
    MAX_PROBE_ATTEMPTS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _make_pacer,
    _sleep_for,
    _throttle_delay,
    wilson,
)

from bmlib.transparency.analyzer import (
    _HTTP_TIMEOUT_SECONDS,
    CLINICALTRIALS_STUDY_URL,
    CROSSREF_WORKS_URL,
    EFETCH_URL,
    EUROPEPMC_REST_BASE,
    EUTILS_TOOL_NAME,
    JSON_ACCEPT_HEADERS,
    MAX_TRIAL_IDS_TO_CHECK,
    OPENALEX_WORKS_URL,
    TransparencyAnalyzer,
    _parse_pubmed_signals,
    _user_agent,
)

#: The five populations, named once. Every table, gate and exit code is keyed
#: on these, so an endpoint added to the module and not to this tuple prints
#: nowhere rather than printing wrongly.
ENDPOINTS = (
    "crossref",
    "europepmc_search",
    "pubmed_efetch",
    "openalex",
    "clinicaltrials",
)

#: The strata, written out rather than built as a source × year cross product.
#: The three sources are the whole of what Europe PMC's ``SRC`` takes for the
#: records this module sees — ``MED`` (a PubMed record), ``PMC`` (an article
#: Europe PMC holds) and ``PPR`` (a preprint, which carries no PMID at all and
#: so exercises the DOI-only path) — and the years reach back past the window
#: where every record carries a DOI, issue #138's redraw being the standing
#: lesson that a population living in older material reads as absent from a
#: two-year window.
#:
#: **A cross product would carry a cell the corpus cannot fill.** ``SRC:PPR AND
#: PUB_YEAR:2008`` is 0 hits — measured 2026-09-06, against 1,789 for 2014,
#: 31,215 for 2018 and 182,124 for 2024 — because Europe PMC's preprint corpus
#: does not reach back that far. An empty cell is indistinguishable here from a
#: stratum whose page failed, so a cross product would report a hole in the
#: stratification on every run for ever: the runaway-failure shape ``#94``
#: exists to avoid, one instrument over. Every pair below was measured
#: non-empty on the same day.
DRAW_STRATA = (
    ("MED", 2024),
    ("MED", 2014),
    ("MED", 2004),
    ("PMC", 2024),
    ("PMC", 2014),
    ("PMC", 2004),
    ("PPR", 2024),
    ("PPR", 2019),
    ("PPR", 2014),
)
#: Records to draw in total, spread evenly over the strata — 20 per stratum,
#: so the default *is* the draw the committed numbers were taken at. 150
#: measured probes settle "is a non-200 the ordinary outcome here?" to within
#: a Wilson half-width of about ±4% at a true 5%, which is the resolution the
#: level decision needs — the same reasoning `sample_free_pdf_urls.py` records
#: for its own 150 — and 180 is the next multiple of the nine strata above it.
#:
#: **It was 150, which nine strata cannot spread evenly**: the floor made the
#: real draw 144 while every table in the repository reported 180, and the
#: invocation that produced 180 was recorded nowhere, so a reader re-running
#: the documented command got a third number (PR #195's review). The rule the
#: sibling samplers follow is that a committed figure must be re-derivable
#: from what is written down (issues #132/#138); making the default the
#: measured draw is the cheapest way to hold it.
DEFAULT_TARGET = 180

#: The ClinicalTrials.gov population is drawn separately, and that is forced by
#: what the population *is*. Every other table's denominator is requests over
#: records bmlib was handed; this one is requests over records for which bmlib
#: *found an accession*, and in a natural draw that is almost none — the first
#: smoke run's 8 records yielded 0 probes, so the table read ERROR over an
#: absent population rather than reporting a level. The request is conditional
#: on bmlib finding an accession, so the draw has to be too.
#:
#: **The obvious enrichment was measured and refused.** PubMed's own
#: ``PUB_TYPE:"Randomized Controlled Trial"`` yielded **0 accessions in 5
#: records** (2026-09-06) by either route — neither a ``DataBankList`` entry
#: nor an abstract the heuristic credits — so it enriches for the publication
#: type and not for the thing bmlib asks about. Conditioning on the abstract
#: naming the registry yields 4 of 6.
#:
#: The caveat that leaves is worth stating rather than discovering: a record
#: whose *only* registration signal is PubMed's ``DataBankList``, with an
#: abstract that never names the registry, is under-represented here. Which
#: direction that moves the 404 rate is **not measured** — a structured
#: accession is the likelier of the two to resolve, so the honest reading is
#: that this draw is, if anything, the pessimistic one.
#:
#: **The two draws never pool.** A record from the main draw contributes no
#: CT.gov probe and a record from this one contributes to no other table, so
#: each population has one stated provenance — a share is of a denominator, and
#: two differently-drawn samples summed make a denominator that describes
#: neither.
#:
#: 2004 is not among the years: it holds 6 such records against 3,346 for 2014
#: and 10,169 for 2024, registration having become a condition of publication
#: in 2005. A stratum that thin is a hole reported as a cell.
TRIAL_STRATA = (
    ("MED", 2024),
    ("MED", 2019),
    ("MED", 2014),
)
TRIAL_QUERY_SUFFIX = ' AND ABSTRACT:"ClinicalTrials.gov"'
#: Records to draw for that population. Each yields up to
#: ``MAX_TRIAL_IDS_TO_CHECK`` accessions, so this buys rather more probes than
#: records.
DEFAULT_TRIAL_TARGET = 60
#: Minimum seconds between two requests to the *same* host, tracked per host.
#: NCBI's unauthenticated ceiling is 3 requests/second and this is the
#: slowest-moving of the five hosts, so one request every 1.5s stays well
#: inside every published limit without a per-host override.
PER_HOST_INTERVAL_SECONDS = 1.5


@dataclass(frozen=True)
class ProbeOutcome:
    """What one request would have produced for ``bmlib.transparency``.

    Attributes:
        endpoint: Which of :data:`ENDPOINTS` this was.
        status: The HTTP status, when there was one.
        cause: ``None`` for a 200, else the bucket — ``http-<status>``,
            ``exception-<TypeName>`` or ``unmeasured-<status>``. Kept as a
            string bucket rather than as the status alone because a raised
            request has no status and is a different branch in the code this
            measures: it is the one that can carry a bmlib defect.
        measured: Whether this probe reached an answer. ``False`` when a 429
            or 503 persisted through every retry — the *sampler* was
            throttled, not the population, so it enters no denominator.

    ``ok`` is a property rather than a field, for the reason
    ``sample_free_pdf_urls.py`` gives: two fields describing one event can be
    constructed disagreeing, and the disagreement would silently move the very
    rate a log level is set from.

    **That argument was applied to the derived field and not to the three
    stored ones it is derived from** (PR #195's review), and the three do
    re-encode each other — so ``__post_init__`` asserts the equivalences. It
    is not defensive: the test helper was building two impossible outcomes
    (``status=None`` with ``cause="http-404"``, and a success with no status),
    neither of which :func:`probe` can produce, and reachable states include
    ``measured=True`` with an ``unmeasured-`` bucket, which would put a
    throttled probe *inside* the denominator every log level is set from.
    """

    endpoint: str
    status: int | None
    cause: str | None
    measured: bool = True

    def __post_init__(self) -> None:
        """Refuse an outcome that describes no event :func:`probe` can produce."""
        if self.cause is None:
            if self.status != 200:
                raise ValueError(f"a served outcome must carry status 200, not {self.status!r}")
            if not self.measured:
                raise ValueError("a served outcome was measured by definition")
            return
        kind, _, tail = self.cause.partition("-")
        if kind == "exception":
            if self.status is not None:
                raise ValueError(f"a raised request has no status, but carries {self.status!r}")
        elif kind in {"http", "unmeasured"}:
            if str(self.status) != tail:
                raise ValueError(f"cause {self.cause!r} disagrees with status {self.status!r}")
        else:
            raise ValueError(f"unknown cause bucket {self.cause!r}")
        if self.measured == (kind == "unmeasured"):
            raise ValueError(f"cause {self.cause!r} disagrees with measured={self.measured!r}")

    @property
    def ok(self) -> bool:
        """Whether bmlib would have got an answer. Success is the absence of a cause."""
        return self.cause is None


@dataclass(frozen=True)
class DrawnRecord:
    """One record from the stratified draw, with everything the probes need.

    Attributes:
        source: The Europe PMC ``source`` this was drawn under.
        year: The publication year stratum.
        doi: Its DOI, if the record carries one. ``None`` is common for older
            ``MED`` records, and those records are kept: an analysis of one is
            what bmlib does with a PMID alone.
        pmid: Its PMID, if any. A ``PPR`` record carries none.
        raw: The search result itself, handed to ``_find_trial_ids`` so the
            trial population is the one bmlib would ask about.
    """

    source: str
    year: int
    doi: str | None
    pmid: str | None
    raw: dict[str, Any]

    def __post_init__(self) -> None:
        """Refuse a record bmlib could not analyse.

        ``analyze()`` returns ``UNKNOWN`` without a request for a record
        carrying neither identifier, and :func:`summarise_draw` prints "a
        record carrying neither reaches no endpoint here" — but
        :func:`probe_record` built ``EXT_ID:None`` from one and probed it into
        the Europe PMC denominator, which is a request bmlib would never make
        entering the population that sets that endpoint's log level. The
        sampler's own cardinal sin, so it raises here rather than being
        skipped quietly at the probe (PR #195's review).
        """
        if not self.doi and not self.pmid:
            raise ValueError("a drawn record must carry a DOI or a PMID; bmlib analyses neither")


@dataclass
class Draw:
    """The sample, and what it cost to take.

    Attributes:
        records: The records drawn.
        failed_strata: Strata whose search page did not answer. Carried rather
            than logged and forgotten, because a stratum missing from the draw
            is a hole in the stratification the tables rest on — the reader
            has to be told the sample is not the one the header claims.
        unusable_records: Records the search returned that carry neither a DOI
            nor a PMID, so bmlib would analyse none of them. Reported for the
            same reason: the draw is then smaller than its target, and a
            reader shown only the total cannot tell that from a smaller
            ``--target``.
    """

    records: list[DrawnRecord] = field(default_factory=list)
    failed_strata: list[str] = field(default_factory=list)
    unusable_records: int = 0


def probe(
    client: Any,
    endpoint: str,
    url: str,
    params: dict[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> ProbeOutcome:
    """Make one request and classify what came back.

    *headers* is per-request, as the analyzer sends them. Omitting it was a
    real gap rather than a tidiness one: CrossRef and OpenAlex are both sent
    ``Accept: application/json``, so without it this script measured two of
    the five endpoints under a header shape bmlib never presents — the same
    class of error as issue #194, which was a ``User-Agent`` (PR #195's
    review).

    A 429 or 503 is retried rather than reported: that status means the probe
    could not be made, not that bmlib's request would have failed, and a
    sampler that has throttled itself measures its own throttling. Up to
    :data:`MAX_PROBE_ATTEMPTS` attempts; a bare-integer ``Retry-After`` is
    honoured, clamped at both ends, otherwise the shared backoff applies.

    Args:
        client: An HTTP client with ``get(url, params=...)``.
        endpoint: Which population this probe belongs to.
        url: The URL, built from ``analyzer.py``'s own constants.
        params: Query parameters, for the endpoints that take them.
        headers: Per-request headers, from ``analyzer.py``'s own constant.

    Returns:
        The outcome. ``measured`` is ``False`` only when every attempt ended in
        429/503.
    """
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        try:
            resp = client.get(url, params=params, headers=headers)
        except Exception as exc:
            return ProbeOutcome(
                endpoint=endpoint, status=None, cause=f"exception-{type(exc).__name__}"
            )
        if resp.status_code in (429, 503):
            if attempt == MAX_PROBE_ATTEMPTS:
                return ProbeOutcome(
                    endpoint=endpoint,
                    status=resp.status_code,
                    cause=f"unmeasured-{resp.status_code}",
                    measured=False,
                )
            _sleep_for(_throttle_delay(resp, attempt))
            continue
        if resp.status_code != 200:
            return ProbeOutcome(
                endpoint=endpoint, status=resp.status_code, cause=f"http-{resp.status_code}"
            )
        return ProbeOutcome(endpoint=endpoint, status=200, cause=None)
    raise AssertionError("unreachable: the loop above always returns")  # pragma: no cover


def _search_params(query: str) -> dict[str, str]:
    """The Europe PMC search parameters ``_query_europepmc`` sends — all of them.

    **No ``pageSize``.** The analyzer sends none, and this helper used to add
    one while its docstring claimed to be "in the shape ``_query_europepmc``
    sends" — so the probe asked a question bmlib does not ask, on the endpoint
    whose failure gates issue #193's whole fix (PR #195's review). The draw
    needs a page size and adds its own at :func:`_draw_params`, where it is
    the draw's parameter rather than a restated one.
    """
    return {"query": query, "format": "json", "resultType": "core"}


def _draw_params(query: str, page_size: int) -> dict[str, str]:
    """:func:`_search_params` plus the page size the *draw* needs.

    Separate because the draw is not a probe: it is the stratified page query
    that builds the population, where the probe is the single-record lookup
    ``_fetch_europepmc`` builds. Keeping them apart is what stops a draw-only
    parameter leaking into the measurement.
    """
    return {**_search_params(query), "pageSize": str(page_size)}


def draw_records(
    client: Any,
    target: int,
    pace: Callable[[str], None],
    strata: tuple[tuple[str, int], ...] = DRAW_STRATA,
    query_suffix: str = "",
) -> Draw:
    """Draw *target* records, spread evenly over *strata*.

    Args:
        client: The HTTP client.
        target: How many records to aim for in total.
        pace: The per-host pacer.
        strata: The ``(source, year)`` cells to spread the draw over.
        query_suffix: Appended to every stratum's query, which is how the
            ClinicalTrials.gov population is enriched — see
            :data:`TRIAL_QUERY_SUFFIX`. The suffix rides on the *query* rather
            than being a second function, so both draws are demonstrably the
            same request against the same field.

    Returns:
        The :class:`Draw`, holding *at least* *target* records when every
        stratum answers — the per-stratum count is rounded up, so the total
        may exceed *target* by up to ``len(strata) - 1``. A stratum whose page
        did not answer contributes no records and is named in
        ``failed_strata`` — never back-filled from another stratum, which
        would re-weight the sample without saying so.
    """
    # Round **up**, as `sample_jats_exhibits.py` does: `150 // 9` is 16, so a
    # floor made the default draw 144 records while every comment reasoned
    # from 150 and `--target`'s help said "in total" (PR #195's review). "Up
    # to" is the honest contract, and a draw short of its own target is the
    # one direction that quietly weakens every interval below.
    per_stratum = max(1, -(-target // len(strata)))
    draw = Draw()
    url = f"{EUROPEPMC_REST_BASE}/search"
    for source, year in strata:
        label = f"{source}/{year}"
        query = f"SRC:{source} AND PUB_YEAR:{year}{query_suffix}"
        pace(url)
        try:
            resp = client.get(url, params=_draw_params(query, per_stratum))
        except Exception as exc:
            print(f"  draw {label}: request raised {type(exc).__name__}: {exc}", file=sys.stderr)
            draw.failed_strata.append(label)
            continue
        if resp.status_code != 200:
            print(f"  draw {label}: HTTP {resp.status_code}", file=sys.stderr)
            draw.failed_strata.append(label)
            continue
        try:
            results = resp.json().get("resultList", {}).get("result", [])
        except Exception as exc:
            print(f"  draw {label}: unreadable body ({type(exc).__name__})", file=sys.stderr)
            draw.failed_strata.append(label)
            continue
        if not results:
            # An empty stratum is a hole in the stratification just as a failed
            # one is: the table would otherwise claim a source-and-year spread
            # the sample does not have.
            print(f"  draw {label}: no records", file=sys.stderr)
            draw.failed_strata.append(label)
            continue
        for result in results:
            doi = result.get("doi") or None
            pmid = result.get("pmid") or None
            if not doi and not pmid:
                # Counted, not raised: one unusable record must not cost the
                # stratum, and it must not be invisible either — a draw
                # quietly shorter than its target weakens every interval
                # below without saying so.
                print(f"  draw {label}: a record carries no DOI and no PMID", file=sys.stderr)
                draw.unusable_records += 1
                continue
            draw.records.append(
                DrawnRecord(source=source, year=year, doi=doi, pmid=pmid, raw=result)
            )
    return draw


def _efetch_params(pmid: str, email: str) -> dict[str, str]:
    """The efetch parameters ``_query_pubmed`` sends, minus the optional key."""
    return {
        "db": "pubmed",
        "id": pmid,
        "retmode": "xml",
        "tool": EUTILS_TOOL_NAME,
        "email": email,
    }


def trial_ids_for(
    analyzer: TransparencyAnalyzer, record: DrawnRecord, efetch_xml: str | None
) -> list[str]:
    """The NCT accessions bmlib would ask ClinicalTrials.gov about for *record*.

    Mirrors ``_check_trial_registration``: PubMed's own ``DataBankList``
    accessions win when the record was fetched, and the abstract heuristic is
    the fallback. Measuring the fallback alone would overstate the 404 rate —
    an accession scraped out of prose is exactly the kind that does not
    resolve — and measuring only the structured ones would understate it.

    Args:
        analyzer: Supplies ``_find_trial_ids``; no request is made, since the
            record it would search for is passed in.
        record: The drawn record.
        efetch_xml: The PubMed record's XML, when the efetch probe served one.

    Returns:
        Up to ``MAX_TRIAL_IDS_TO_CHECK`` accessions, the cap bmlib applies.
    """
    if efetch_xml:
        accessions = list(_parse_pubmed_signals(efetch_xml).trial_accessions)
        if accessions:
            return accessions[:MAX_TRIAL_IDS_TO_CHECK]
    found = analyzer._find_trial_ids(
        None, record.pmid, record.doi, epmc={"resultList": {"result": [record.raw]}}
    )
    return found[:MAX_TRIAL_IDS_TO_CHECK]


def probe_record(
    client: Any,
    analyzer: TransparencyAnalyzer,
    record: DrawnRecord,
    email: str,
    pace: Callable[[str], None],
) -> list[ProbeOutcome]:
    """Make the four per-record requests ``analyze()`` would make, and classify each.

    ClinicalTrials.gov is deliberately **not** among them: its population is
    drawn separately (:data:`TRIAL_STRATA`) and probed by :func:`probe_trials`,
    so the two draws cannot pool into one denominator.

    Args:
        client: The HTTP client.
        analyzer: Unused here, and kept in the signature so this and
            :func:`probe_trials` are called the same way by ``main``.
        record: The drawn record.
        email: The contact address NCBI asks for.
        pace: The per-host pacer.

    Returns:
        One outcome per request that would have been made. A record with no
        DOI contributes nothing to the CrossRef or OpenAlex populations, and a
        record with no PMID nothing to the PubMed one, which is exactly what
        bmlib does with them.
    """
    del analyzer
    outcomes: list[ProbeOutcome] = []

    if record.doi:
        url = CROSSREF_WORKS_URL.format(doi=record.doi)
        pace(url)
        outcomes.append(probe(client, "crossref", url, headers=JSON_ACCEPT_HEADERS))

    # The single-record lookup, which is what `_fetch_europepmc` builds — not
    # the stratified page query the draw used, and with no `pageSize`, which
    # the analyzer does not send.
    query = f'DOI:"{record.doi}"' if record.doi else f"EXT_ID:{record.pmid}"
    search_url = f"{EUROPEPMC_REST_BASE}/search"
    pace(search_url)
    outcomes.append(probe(client, "europepmc_search", search_url, _search_params(query)))

    if record.pmid:
        pace(EFETCH_URL)
        outcomes.append(
            probe(client, "pubmed_efetch", EFETCH_URL, _efetch_params(record.pmid, email))
        )

    if record.doi:
        url = OPENALEX_WORKS_URL.format(doi=record.doi)
        pace(url)
        outcomes.append(probe(client, "openalex", url, headers=JSON_ACCEPT_HEADERS))

    return outcomes


def probe_trials(
    client: Any,
    analyzer: TransparencyAnalyzer,
    record: DrawnRecord,
    email: str,
    pace: Callable[[str], None],
    population_failures: list[str] | None = None,
) -> list[ProbeOutcome]:
    """Probe ClinicalTrials.gov for every accession bmlib would ask about.

    The PubMed ``efetch`` this makes is part of **constructing the
    population**, not a probe of it: which accessions bmlib asks about is
    decided by what that record says, exactly as which records exist at all is
    decided by the stratified search page. Counting it here would put a second
    copy of the PubMed population into the table under a different draw.

    Args:
        client: The HTTP client.
        analyzer: Supplies the trial-id extraction.
        record: A record from the trial-enriched draw.
        email: The contact address NCBI asks for.
        pace: The per-host pacer.
        population_failures: Appended to when the population-building efetch
            does not answer, so :func:`main` can report — and exit non-zero on
            — a trial population that was reshaped by something other than the
            draw. Optional so the function stays callable on its own.

    Returns:
        One outcome per accession, up to bmlib's own cap. A record for which
        no accession could be found contributes nothing — which is right: for
        such a record bmlib makes no request either, *provided the efetch
        succeeded*. When it did not, the record is not "one bmlib would not
        ask about" but one this script could not classify, which is why those
        are counted separately rather than read as an absence.
    """
    if population_failures is None:
        population_failures = []
    efetch_xml: str | None = None
    if record.pmid:
        pace(EFETCH_URL)
        try:
            resp = client.get(EFETCH_URL, params=_efetch_params(record.pmid, email))
        except Exception as exc:
            # The **type** as well as the message, which `draw_records` above
            # already gets right and this did not: `str(OSError(...))` does
            # not contain "OSError", and the analyzer's own handler argues the
            # point at length.
            print(
                f"  trial draw: efetch for {record.pmid} raised {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            population_failures.append(f"efetch {record.pmid}: {type(exc).__name__}")
        else:
            if resp.status_code == 200:
                efetch_xml = resp.text
            else:
                # **Reported and counted, where it used to be neither.** This
                # efetch builds the population rather than measuring it, so it
                # enters no table — but a failure silently demotes the record
                # from the structured-accession route to the abstract
                # heuristic, or drops it from the population altogether, and
                # `trial_ids_for` argues that measuring the fallback alone
                # "would overstate the 404 rate". An unreported failure
                # therefore introduces exactly the bias that function balances,
                # and the headline share could not be told from a population
                # thinned this way (PR #195's review). It is also the one
                # request here that `probe()` does not retry.
                print(
                    f"  trial draw: efetch for {record.pmid} answered HTTP {resp.status_code}",
                    file=sys.stderr,
                )
                population_failures.append(f"efetch {record.pmid}: HTTP {resp.status_code}")

    outcomes: list[ProbeOutcome] = []
    for nct_id in trial_ids_for(analyzer, record, efetch_xml):
        url = CLINICALTRIALS_STUDY_URL.format(nct_id=nct_id)
        pace(url)
        outcomes.append(probe(client, "clinicaltrials", url, {"fields": "hasResults"}))
    return outcomes


def is_reportable(outcomes: list[ProbeOutcome]) -> bool:
    """Whether a population yielded a distribution rather than an ``ERROR`` line.

    The single predicate behind both :func:`summarise`'s ERROR branches and
    :func:`main`'s exit status, so the exit code cannot disagree with what was
    printed.

    Args:
        outcomes: A population's attempts.

    Returns:
        ``False`` when nothing was probed, or when more than
        ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of the attempts never reached an
        answer. A zero over an absent population is not a clean result.
    """
    if not outcomes:
        return False
    unmeasured = sum(1 for o in outcomes if not o.measured)
    return unmeasured / len(outcomes) <= UNMEASURED_SHARE_ERROR_THRESHOLD


def summarise(name: str, outcomes: list[ProbeOutcome]) -> list[str]:
    """Render one population's status distribution as report lines.

    Args:
        name: The endpoint's name.
        outcomes: Its probe outcomes. An empty list is an ERROR, not a
            population with no failures: a zero is exactly what a healthy
            endpoint looks like, so the two must not print alike.

    Returns:
        The lines to print — the non-200 share with its Wilson interval, then
        one line per bucket. The buckets are what the level decision is made
        from: DEBUG can only be earned by a status measured to be the ordinary
        outcome, and only for that status.
    """
    if not outcomes:
        return [f"{name:<18} ERROR — nothing was probed; no distribution is reported"]
    n = len(outcomes)
    unmeasured = [o for o in outcomes if not o.measured]
    if not is_reportable(outcomes):
        return [
            f"{name:<18} ERROR — {len(unmeasured)}/{n} attempts were throttled (429/503) "
            "even after retries; no distribution is reported"
        ]
    measured = [o for o in outcomes if o.measured]
    m = len(measured)
    failures = [o for o in measured if not o.ok]
    lo, hi = wilson(len(failures), m)
    # "not served", not "non-200": `failures` includes the `exception-*`
    # bucket, whose outcomes have no status at all, so the old label named a
    # narrower thing than it counted. Every count is 0 today, so it has never
    # printed wrongly — but a label is a claim about what was counted, which
    # is this repository's own rule (PR #195's review).
    lines = [
        f"{name:<18} {m:>4} probed   "
        f"{len(failures):>4} not served = {100 * len(failures) / m:5.1f}%   "
        f"95% CI [{100 * lo:.1f}%, {100 * hi:.1f}%]"
    ]
    if unmeasured:
        lines.append(
            f"{'':<18}   {len(unmeasured)} unmeasured (429/503 after retries; excluded above)"
        )
    for cause, count in sorted(Counter(o.cause for o in failures).items()):
        lines.append(f"{'':<18}   {cause:<24} {count:>4}   {100 * count / m:5.1f}% of measured")
    return lines


def summarise_draw(name: str, draw: Draw) -> list[str]:
    """Report one sample: how it was stratified, and where it is not.

    A stratum that failed is named. The tables below rest on the draw being
    spread over source and year, so a reader who is not told which cells are
    missing is reading a distribution as if it were the one the header claims.

    Args:
        name: Which draw this is — the two are reported separately because
            they are two populations, and a reader who is shown one figure for
            both cannot tell which denominator a share belongs to.
        draw: The sample.
    """
    if not draw.records:
        return [f"{name:<18} ERROR — no records were drawn; its table below is empty"]
    by_stratum = Counter(f"{r.source}/{r.year}" for r in draw.records)
    lines = [
        f"{name:<18} {len(draw.records)} records over {len(by_stratum)} strata "
        f"({', '.join(f'{k}={v}' for k, v in sorted(by_stratum.items()))})"
    ]
    if draw.failed_strata:
        lines.append(
            f"{'':<18}   ERROR — {len(draw.failed_strata)} stratum/strata did not answer: "
            f"{', '.join(draw.failed_strata)}; the sample is not evenly stratified"
        )
    with_doi = sum(1 for r in draw.records if r.doi)
    with_pmid = sum(1 for r in draw.records if r.pmid)
    lines.append(
        f"{'':<18}   {with_doi} carry a DOI, {with_pmid} carry a PMID "
        "(a record carrying neither is refused by `DrawnRecord` and not drawn)"
    )
    if draw.unusable_records:
        lines.append(
            f"{'':<18}   {draw.unusable_records} returned record(s) carried neither and "
            "were skipped; the draw is that much smaller than its target"
        )
    return lines


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address NCBI asks callers for.")
    parser.add_argument(
        "--target", type=int, default=DEFAULT_TARGET, help="Records to draw in total."
    )
    parser.add_argument(
        "--trial-target",
        type=int,
        default=DEFAULT_TRIAL_TARGET,
        help="Records to draw for the ClinicalTrials.gov population, which is drawn separately.",
    )
    parser.add_argument(
        "--per-host-interval",
        type=float,
        default=PER_HOST_INTERVAL_SECONDS,
        help="Minimum seconds between two requests to the same host.",
    )
    return parser


def main() -> int:
    """Draw, probe every endpoint, and print the tables.

    Returns:
        ``1`` if the draw lost a stratum, any population printed ``ERROR``
        instead of a distribution, or a population-building request did not
        answer, else ``0``. A scheduled re-run is judged by the exit code, and
        "nothing could be measured" must not read as "measured, and nothing
        was wrong" — nor, since PR #195's review, may "measured over a
        population something else reshaped".
    """
    args = _build_arg_parser().parse_args()

    # **bmlib's own header, not a sampler one.** A remote that judges the
    # caller by it — ClinicalTrials.gov does, issue #194 — answers a sampler
    # string and the analyzer's string differently, and the table would then
    # be of an identity bmlib never presents. The URL half of that lesson is
    # in the module docstring; this is the header half, and it is the half
    # that found #194.
    headers = {"User-Agent": _user_agent(args.email, httpx.__version__)}
    pace = _make_pacer(args.per_host_interval)
    analyzer = TransparencyAnalyzer(email=args.email)
    by_endpoint: dict[str, list[ProbeOutcome]] = {name: [] for name in ENDPOINTS}
    #: Requests that build the ClinicalTrials.gov population rather than
    #: measure it, and did not answer. They enter no table and would enter no
    #: exit code either, which is what made them invisible.
    population_failures: list[str] = []

    # **The analyzer's transport policy, not a sampler one.** A sampler that
    # follows redirects and waits three times as long turns two of bmlib's
    # failures into successes: a 3xx is a non-200 to `_request` — which
    # `FullTextStatus.REQUEST_FAILED` names explicitly as an outcome — and a
    # 20-second response is a `ReadTimeout`. Measuring under a laxer policy
    # than the code uses makes the zeroes below zeroes about a different
    # client (PR #195's review). `follow_redirects=False` is httpx's default
    # and is written out because it is a decision here, not an omission.
    with httpx.Client(
        timeout=_HTTP_TIMEOUT_SECONDS, headers=headers, follow_redirects=False
    ) as client:
        draw = draw_records(client, args.target, pace)
        for index, record in enumerate(draw.records, start=1):
            if index % 10 == 0:
                print(f"  probed {index}/{len(draw.records)} records", file=sys.stderr)
            for outcome in probe_record(client, analyzer, record, args.email, pace):
                by_endpoint[outcome.endpoint].append(outcome)
        trial_draw = draw_records(client, args.trial_target, pace, TRIAL_STRATA, TRIAL_QUERY_SUFFIX)
        for index, record in enumerate(trial_draw.records, start=1):
            if index % 10 == 0:
                print(f"  probed {index}/{len(trial_draw.records)} trial records", file=sys.stderr)
            for outcome in probe_trials(
                client, analyzer, record, args.email, pace, population_failures
            ):
                by_endpoint[outcome.endpoint].append(outcome)

    print("\nStatus distribution per dropped-response endpoint\n")
    for line in summarise_draw("draw", draw):
        print(line)
    for line in summarise_draw("trial draw", trial_draw):
        print(line)
    print()
    for name in ENDPOINTS:
        for line in summarise(name, by_endpoint[name]):
            print(line)

    if population_failures:
        print(
            f"\n{'trial population':<18}   ERROR — {len(population_failures)} "
            "population-building efetch(es) did not answer, so the accessions probed "
            f"below are not the ones bmlib would ask about: {', '.join(population_failures)}"
        )

    reportable = all(is_reportable(by_endpoint[name]) for name in ENDPOINTS)
    drawn = bool(draw.records) and bool(trial_draw.records)
    lost = bool(draw.failed_strata) or bool(trial_draw.failed_strata)
    return 0 if reportable and drawn and not lost and not population_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
