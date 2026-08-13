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

"""Measure what real PDFs put in their metadata title, against the truth.

``SectionSegmenter._extract_title()`` returns any truthy ``metadata["title"]``
verbatim, and real PDFs carry junk there — ``"Microsoft Word -
manuscript.docx"``, ``"untitled"``, a typesetter's job number — so junk beats
a perfectly good large-font first-page line (issue #56). The issue proposes a
reject-list of junk shapes; this repo settles list-shaped questions by
measuring a corpus instead.

**Ground truth is free here.** Every PDF this script downloads comes from a
record that already states the article's title, so each row self-labels — no
hand-labelling pass, unlike ``tests/data/funder_names.json``:

===========  =========================================  ========================
Bucket       Test                                       What it measures
===========  =========================================  ========================
absent       no metadata title at all                   the falsy case that
                                                        already falls through
match        agrees with the record title, token        the population a wrong
             for token                                  rejection damages
truncated    a strictly shorter prefix of it            a partial title
unrelated    neither                                    the junk #56 is about
===========  =========================================  ========================

**Two sources, for opposite reasons.** Europe PMC ``?pdf=render`` serves
publisher-typeset PDFs, whose metadata is mostly clean — that population
measures how often a rule wrongly rejects a *good* title, which is the cost
side. bioRxiv/medRxiv serves author-submitted PDFs straight out of Word and
LaTeX, which is where the junk actually lives. Sampling only the first would
flatter any rule; only the second would hide its cost.

**The script never runs the acceptance rule.** It collects and labels, and
``tests/test_pdf_metadata_titles.py`` evaluates the rule against what it
wrote. A corpus labelled by the rule under test could only ever confirm it.

**A PDF that could not be sampled is never a finding.** A 429 surviving its
retries, a non-200, a transport exception, an oversized body, bytes that are
not a PDF, or a file PyMuPDF cannot open counts as *unmeasured*: excluded from
every denominator. Past ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of a population,
the table prints ``ERROR`` instead of a distribution and the script exits
non-zero — the rows that get through heavy throttling are the early ones, not
a random sample. Pacing, the clamped ``Retry-After`` and that threshold come
from ``scripts/_sampling.py``.

Each PDF goes through **bmlib's own** converter, so the committed fixture
holds blocks from the code path the library runs rather than from a parallel
implementation here that could drift from it. The file is deleted straight
after.

Usage::

    uv run python scripts/sample_pdf_metadata_titles.py
    uv run python scripts/sample_pdf_metadata_titles.py --target 150 --source biorxiv

Writes ``tests/data/pdf_metadata_titles.json``, sorted by ``(source, id)`` so
a re-run produces a reviewable diff rather than a reshuffle.

Companion to ``scripts/sample_free_pdf_urls.py``,
``scripts/sample_databank_names.py`` and ``scripts/sample_funder_names.py``.
**Run it before changing the reject-list in ``bmlib/fulltext/_titles.py``.**
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - the script is a live runner
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(1) from None

# Pacing, throttling and the unmeasured-share rule are shared with
# `sample_free_pdf_urls.py`; see `_sampling`'s docstring for why they are not
# duplicated. `scripts/` is not a package — running a script puts this
# directory on sys.path as sys.path[0], and the test files that load one by
# path insert it explicitly.
from _sampling import (
    MAX_PROBE_ATTEMPTS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _make_pacer,
    _sleep_for,
    _throttle_delay,
    is_probeable,
)

from bmlib import __version__
from bmlib.fulltext.pdf_converter import PyMuPDFConverter
from bmlib.fulltext.segmenter import _median_font_size
from bmlib.fulltext.service import _extract_free_pdf_url
from bmlib.publications.fetchers.biorxiv import BASE_URL as BIORXIV_BASE_URL
from bmlib.publications.fetchers.biorxiv import fetch_biorxiv

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE_SIZE = 100
_USER_AGENT = f"bmlib-title-sampler/{__version__} (+https://github.com/hherb/bmlib)"

# Rows per source. 150 apiece puts the 95% upper bound on a zero wrong-
# rejection rate at about 1%, which is the floor the ship rule names; halving
# it would only bound that at 2.4% and would likely miss a junk shape
# occurring in 2% of PDFs altogether.
DEFAULT_TARGET = 150
DEFAULT_OUTPUT = Path("tests/data/pdf_metadata_titles.json")
# Minimum seconds between two requests to the *same* host. Publisher hosts
# serve whole PDFs here rather than the 1KB ranged probes the free-PDF
# sampler makes, so this is the politer of the two defaults.
PER_HOST_INTERVAL_SECONDS = 3.0
# Enough of page 1 to carry a title, its authors and their affiliations —
# which is where the decision is made — capped so the committed fixture stays
# manageable and carries little article prose. 40 rather than 20 because a
# line-numbered preprint interleaves a number block between every real line,
# so 20 blocks can be 10 lines of document; the first smoke run showed a
# title occupying stored blocks 5 and 7 for that reason. `page_one_line_count`
# records what was truncated, because a row whose title fell outside the cap
# would otherwise score as a wrong rejection that never happened.
PAGE_ONE_LINES_KEPT = 40
MAX_LINE_CHARS = 200
# A PDF larger than this is not parsed: the corpus needs 300 title pages, not
# a supplement bundle, and one 400MB file can stall a paced run.
MAX_PDF_BYTES = 30 * 1024 * 1024
# How many days back to walk for the bioRxiv population before giving up on
# reaching --target.
BIORXIV_DAYS_TO_WALK = 20
# Concurrent fetches. The pacer still admits one request per host per
# interval, so this overlaps *transfers* rather than raising the request rate
# — which is what a run is actually spending its time on.
DEFAULT_WORKERS = 4
# Downloaded PDFs are kept here between runs, so a resumed run never pays for
# the same transfer twice. Outside the repo on purpose: a full sample is
# gigabytes.
DEFAULT_PDF_CACHE = Path.home() / ".cache" / "bmlib-title-sampler"

_BUCKETS = ("match", "truncated", "unrelated", "absent")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Markup left after unescaping a record title — `<i>`, `<sub>`, `<sup>`.
_TAG_RE = re.compile(r"<[^>]+>")


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens of *text*, diacritics folded away.

    The sampler's own normaliser, deliberately **not** imported from
    ``bmlib.fulltext._titles``: the buckets are ground truth, and labelling
    the corpus with the rule under test would let the corpus only ever
    confirm the rule.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _TOKEN_RE.findall(stripped.lower())


def clean_record_title(text: str) -> str:
    """The record's title as prose, with its markup resolved and removed.

    Europe PMC returns titles with the markup **escaped**, so a title reading
    ``<i>MET</i> alterations`` arrives as ``&lt;i&gt;MET&lt;/i&gt;
    alterations``; bioRxiv serves the unescaped form of the same thing.
    Tokenised raw, either becomes ``lt i gt met …``, and a PDF whose metadata
    title is a *perfect* match is then labelled ``unrelated`` — which is the
    worst direction for this corpus to be wrong in, since corroboration
    accepts such a title and the row would count as junk the rule failed to
    reject.

    Unescape first, then strip tags: doing it the other way round leaves the
    escaped form untouched. ``&amp;`` therefore survives as ``&``, which it
    must — ``Trials & Tribulations`` is a title, not markup.
    """
    return _TAG_RE.sub("", html.unescape(text)).strip()


def classify(metadata_title: str, record_title: str) -> str:
    """Label one row against the title the source's own record states.

    Args:
        metadata_title: What the PDF's own metadata claims.
        record_title: What the API that served the PDF says the article is.

    Returns:
        ``"absent"`` when the PDF carries no metadata title — the falsy case
        that already falls through to the font heuristic; ``"match"`` when it
        agrees with the record title token for token; ``"truncated"`` when it
        is a strictly shorter prefix of it; ``"unrelated"`` otherwise, which
        is the junk issue #56 is about.
    """
    meta = _tokens(metadata_title)
    record = _tokens(record_title)
    if not meta:
        return "absent"
    if meta == record:
        return "match"
    if len(meta) < len(record) and record[: len(meta)] == meta:
        return "truncated"
    return "unrelated"


def download(client: Any, url: str, pace: Callable[[str], None]) -> tuple[bytes | None, bool]:
    """Fetch one PDF, retrying a 429/503 before giving up on it.

    Args:
        client: An HTTP client with ``get(url, headers=...)``.
        url: The PDF URL.
        pace: Per-host pacer, called before every attempt.

    Returns:
        ``(body, measured)``. ``measured`` is ``False`` whenever the row
        cannot be labelled — a throttled request, a non-200, a transport
        exception, an oversized body, or bytes that are not a PDF. This
        script measures *titles*, so anything short of a readable PDF is a
        question never asked rather than an answer of any kind.
    """
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        pace(url)
        try:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})
        except Exception as exc:
            print(f"  download failed for {url}: {exc}", file=sys.stderr)
            return None, False
        if resp.status_code in (429, 503):
            if attempt == MAX_PROBE_ATTEMPTS:
                print(f"  throttled for {url}; unmeasured", file=sys.stderr)
                return None, False
            _sleep_for(_throttle_delay(resp, attempt))
            continue
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} for {url}", file=sys.stderr)
            return None, False
        body = bytes(resp.content)
        if len(body) > MAX_PDF_BYTES:
            print(f"  oversized ({len(body)} bytes) for {url}; unmeasured", file=sys.stderr)
            return None, False
        if not body.startswith(b"%PDF"):
            print(f"  not a PDF for {url}; unmeasured", file=sys.stderr)
            return None, False
        return body, True
    raise AssertionError("unreachable: the loop above always returns")  # pragma: no cover


def fetch_pdf(
    client: Any,
    url: str,
    identifier: str,
    pace: Callable[[str], None],
    cache: Any | None,
) -> tuple[bytes | None, bool]:
    """The PDF for *identifier*, from the cache if it is already there.

    The transfer is the expensive half of a run — a full sample is gigabytes
    over hours — so a resumed run must not pay for it twice. The cache is
    bmlib's own :class:`~bmlib.fulltext.cache.FullTextCache`, which publishes
    each file atomically (issue #70), so a run killed mid-write leaves no
    half-PDF to be read back as a real one on the next pass.

    Args:
        client: An HTTP client with ``get(url, headers=...)``.
        url: The PDF URL.
        identifier: The cache key — the PMC id or DOI.
        pace: Per-host pacer.
        cache: A ``FullTextCache``, or ``None`` to fetch every time.

    Returns:
        ``(body, measured)`` exactly as :func:`download` defines them. A
        failed download caches nothing, so a later run retries it rather than
        inheriting the failure.
    """
    if cache is not None:
        cached = cache.get_pdf(identifier)
        if cached is not None:
            try:
                return Path(cached).read_bytes(), True
            except OSError as exc:
                # Best-effort, like every other cache read in bmlib: an
                # unreadable entry costs a re-download, not the run.
                print(f"  unreadable cache entry for {identifier}: {exc}", file=sys.stderr)

    body, measured = download(client, url, pace)
    if cache is not None and body is not None:
        try:
            cache.save_pdf(body, identifier)
        except OSError as exc:
            print(f"  could not cache {identifier}: {exc}", file=sys.stderr)
    return body, measured


def load_partial(path: Path) -> list[dict[str, Any]]:
    """Rows written by an earlier run, from the JSONL journal beside the output.

    A line that does not parse is skipped rather than fatal: what a kill
    mid-write leaves behind is a truncated *final* line, and refusing the
    whole file for it would turn one lost row into every row lost — the
    failure the journal exists to prevent.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  skipping an incomplete journal line in {path}", file=sys.stderr)
    return rows


def append_row(path: Path, row: dict[str, Any]) -> None:
    """Append one row to the journal and put it beyond a crash.

    ``flush()`` returns success while the bytes are still in the page cache,
    so the ``fsync`` is not ceremony — it is the difference between a journal
    that survives a kill and one that mostly does. One fsync per row is free
    at this cadence: rows arrive seconds apart at best.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def already_seen(rows: list[dict[str, Any]]) -> set[str]:
    """The identifiers a resumed run must not collect again.

    Successes only. An attempt that went *unmeasured* is deliberately left
    open to a retry: the first live run lost 40 of 153 to local DNS failures
    and read timeouts, and treating those as settled would have carried a
    transient fault on this machine into the population's permanent record —
    the unmeasured share could then never fall below the threshold that makes
    the whole population unreportable, however many good rows followed.

    Retrying is safe because :func:`tally_previous` counts each identifier's
    *last* outcome, so an id that fails, is retried and succeeds stops being
    counted as unmeasured rather than being counted as both.
    """
    return {str(row["id"]) for row in rows if row.get("id") and not row.get("unmeasured")}


def tally_previous(entries: list[dict[str, Any]]) -> dict[str, tuple[list[dict[str, Any]], int]]:
    """Sort a journal into ``{source: (rows, unmeasured)}``, last outcome winning.

    One entry per identifier: a journal holds every *attempt*, so an id that
    went unmeasured and later succeeded appears twice, and counting both would
    charge the population for a failure that was retried away.
    """
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        identifier = str(entry.get("id", ""))
        if identifier:
            latest[identifier] = entry

    populations: dict[str, tuple[list[dict[str, Any]], int]] = {}
    for entry in latest.values():
        source = str(entry.get("source", ""))
        rows, unmeasured = populations.get(source, ([], 0))
        if entry.get("unmeasured"):
            populations[source] = (rows, unmeasured + 1)
        else:
            populations[source] = ([*rows, entry], unmeasured)
    return populations


def row_from_pdf(
    pdf_bytes: bytes,
    source: str,
    identifier: str,
    record_title: str,
    file_name: str,
    tmpdir: Path,
) -> dict[str, Any] | None:
    """Build one fixture row, or ``None`` if the PDF could not be read.

    The PDF goes through bmlib's own :class:`PyMuPDFConverter` — both
    ``convert()`` for the metadata and ``extract_blocks()`` for the lines — so
    the fixture holds what the library itself would see. ``None`` rather than
    an empty row on failure: a row with no page-1 lines is indistinguishable
    from a real image-only scan, which is a case the acceptance rule treats
    specially.

    Args:
        pdf_bytes: The downloaded file.
        source: ``"europepmc"`` or ``"biorxiv"``.
        identifier: PMC id or DOI, whichever the source gave.
        record_title: Ground truth, from the record that served the PDF.
        file_name: The URL's last path segment, for the file-stem candidate.
        tmpdir: A directory to write the PDF into; it is removed again here.

    Returns:
        The row, or ``None`` when conversion failed.
    """
    # A unique name per call: with a fixed one, two workers would write the
    # same file and each would parse the other's bytes. (#70 learned the
    # same lesson about a fixed temp name in the cache.)
    path = tmpdir / f"sample-{uuid.uuid4().hex}.pdf"
    try:
        path.write_bytes(pdf_bytes)
        converter = PyMuPDFConverter()
        result = converter.convert(path)
        if not result.success:
            print(f"  unreadable PDF for {identifier}: {result.error_message}", file=sys.stderr)
            return None
        blocks = converter.extract_blocks(path)
    except Exception as exc:
        print(f"  conversion failed for {identifier}: {exc}", file=sys.stderr)
        return None
    finally:
        path.unlink(missing_ok=True)

    # Cleaned here rather than at each call site, so a walk that forgets
    # cannot silently mislabel its whole population.
    record_title = clean_record_title(record_title)
    metadata_title = str(result.metadata.get("title") or "")
    all_page_one = [b for b in blocks if b.page_num == 0]
    page_one = all_page_one[:PAGE_ONE_LINES_KEPT]
    return {
        "source": source,
        "id": identifier,
        "record_title": record_title,
        "metadata_title": metadata_title,
        "bucket": classify(metadata_title, record_title),
        "creator": str(result.metadata.get("creator") or ""),
        "producer": str(result.metadata.get("producer") or ""),
        "file_name": file_name,
        "median_font_size": _median_font_size(blocks),
        # What page 1 actually held, so a reader of the fixture can tell a
        # document with a short first page from one the cap truncated. A
        # truncated row is not evidence about a rule that sees whole pages.
        "page_one_line_count": len(all_page_one),
        "page_one_lines": [
            {
                "text": block.text[:MAX_LINE_CHARS],
                "size": round(block.font_size, 2),
                "bold": block.is_bold,
                "y": round(block.y, 2),
            }
            for block in page_one
        ],
    }


def summarise(source: str, rows: list[dict[str, Any]], unmeasured: int) -> list[str]:
    """Render one population's bucket distribution, or an ``ERROR`` line.

    Args:
        source: The population's name.
        rows: Its labelled rows.
        unmeasured: Attempts that never reached a labelled row.

    Returns:
        Lines to print. An ``ERROR`` line and **no percentages** when nothing
        was sampled, or when more than
        ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of the attempts went unmeasured:
        a distribution over the rows that survived heavy throttling is not a
        distribution over the population, and a number that looks precise is
        worse than a line saying it could not be measured.
    """
    attempts = len(rows) + unmeasured
    if not rows or (attempts and unmeasured / attempts > UNMEASURED_SHARE_ERROR_THRESHOLD):
        return [
            f"{source}: ERROR — {len(rows)} rows labelled, {unmeasured} of {attempts} "
            "attempts unmeasured; too little of this population was reached to report it"
        ]
    counts = Counter(row["bucket"] for row in rows)
    lines = [f"{source}: {len(rows)} rows ({unmeasured} unmeasured, excluded below)"]
    for bucket in _BUCKETS:
        count = counts[bucket]
        lines.append(f"    {bucket:<10} {count:>4}  {count / len(rows):>6.1%}")
    return lines


@dataclass(frozen=True)
class Candidate:
    """One PDF worth fetching: where it is, and what the record calls it."""

    source: str
    identifier: str
    record_title: str
    url: str


@dataclass
class RunContext:
    """What every batch of a run needs, gathered so the walks stay readable.

    ``seen`` is mutated as candidates are claimed, so the same identifier is
    never fetched twice — within a run, or across a resumed one, since it
    starts populated from the journal.
    """

    pace: Callable[[str], None]
    journal: Path
    seen: set[str]
    cache: Any | None = None
    workers: int = DEFAULT_WORKERS


def process_candidates(
    client: Any,
    candidates: list[Candidate],
    context: RunContext,
    tmpdir: Path,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch and label *candidates* concurrently, journalling each as it lands.

    Concurrency is safe against the hosts because the pacer is admission
    control: it still admits at most one request per host per interval, and
    what overlaps is the *transfer*, which is what actually dominates a run
    (the first live run managed 1.9 rows a minute against a 3s interval, so
    pacing was never the bottleneck).

    Every outcome is journalled, including an unmeasured one — a resumed run
    that inherited only the successes would recompute the unmeasured share
    over a denominator missing its failures, and the ERROR rule that share
    feeds would then pass by having forgotten.

    Returns:
        ``(rows, unmeasured)`` for this batch.
    """
    rows: list[dict[str, Any]] = []
    unmeasured = 0

    def work(candidate: Candidate) -> dict[str, Any] | None:
        body, measured = fetch_pdf(
            client, candidate.url, candidate.identifier, context.pace, context.cache
        )
        if not measured or body is None:
            return None
        return row_from_pdf(
            body,
            candidate.source,
            candidate.identifier,
            candidate.record_title,
            _file_name(candidate.url),
            tmpdir,
        )

    with ThreadPoolExecutor(max_workers=context.workers) as pool:
        # Results are consumed on this thread, in submission order, so the
        # journal stays append-ordered and needs no lock of its own.
        for candidate, row in zip(candidates, pool.map(work, candidates), strict=True):
            if row is None:
                unmeasured += 1
                append_row(
                    context.journal,
                    {"unmeasured": True, "source": candidate.source, "id": candidate.identifier},
                )
                continue
            rows.append(row)
            append_row(context.journal, row)
    return rows, unmeasured


def sample_europepmc_rows(
    client: Any, target: int, context: RunContext
) -> tuple[list[dict[str, Any]], int]:
    """Collect rows from Europe PMC's free ``?pdf=render`` PDFs.

    The same search ``sample_free_pdf_urls.py`` uses, so the two instruments
    look at comparable slices of the literature. One search page at a time:
    its candidates are fetched concurrently, journalled, and only then is the
    next page requested — so progress survives a kill at any point.

    Returns:
        ``(rows, unmeasured)``. Stops at *target* **rows**, not target URLs —
        a run that downloaded 150 files and could label 90 has not sampled
        150.
    """
    query = "(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])"
    rows: list[dict[str, Any]] = []
    unmeasured = 0
    cursor = "*"
    with TemporaryDirectory(prefix="bmlib-titles-") as tmp:
        tmpdir = Path(tmp)
        while len(rows) < target:
            context.pace(EUROPE_PMC_SEARCH)
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
                    break
                payload = resp.json()
            except Exception as exc:
                print(f"  Europe PMC search failed: {exc}", file=sys.stderr)
                break
            hits = payload.get("resultList", {}).get("result", [])
            if not hits:
                break

            candidates: list[Candidate] = []
            for hit in hits:
                title = (hit.get("title") or "").strip()
                url = _extract_free_pdf_url(hit)
                if not title or not url or not is_probeable(url):
                    continue
                identifier = hit.get("pmcid") or hit.get("id") or url
                if identifier in context.seen:
                    continue
                context.seen.add(identifier)
                candidates.append(Candidate("europepmc", identifier, title, url))
            candidates = candidates[: max(0, target - len(rows))]

            batch, batch_unmeasured = process_candidates(client, candidates, context, tmpdir)
            rows.extend(batch)
            unmeasured += batch_unmeasured
            print(f"  europepmc: {len(rows)}/{target} rows", file=sys.stderr)

            cursor = payload.get("nextCursorMark") or ""
            if not cursor:
                break
    return rows, unmeasured


def sample_biorxiv_rows(
    client: Any, target: int, context: RunContext, server: str = "biorxiv"
) -> tuple[list[dict[str, Any]], int]:
    """Collect rows from bioRxiv/medRxiv preprint PDFs.

    Author-submitted files straight out of Word and LaTeX — the population
    whose metadata titles issue #56 is actually about. The URLs come from
    ``fetch_biorxiv`` itself rather than a re-spelled template, so what is
    sampled cannot drift from what bmlib fetches. One posting day at a time,
    for the same reason the Europe PMC walk goes a page at a time.

    Returns:
        ``(rows, unmeasured)``.
    """
    rows: list[dict[str, Any]] = []
    unmeasured = 0
    day = date.today() - timedelta(days=30)
    with TemporaryDirectory(prefix="bmlib-titles-") as tmp:
        tmpdir = Path(tmp)
        for _ in range(BIORXIV_DAYS_TO_WALK):
            if len(rows) >= target:
                break
            records: list[Any] = []
            context.pace(BIORXIV_BASE_URL)
            try:
                fetch_biorxiv(client, day, on_record=records.append, server=server)
            except Exception as exc:
                print(f"  bioRxiv fetch failed for {day}: {exc}", file=sys.stderr)
                day -= timedelta(days=1)
                continue

            candidates: list[Candidate] = []
            for record in records:
                title = (record.title or "").strip()
                urls = [
                    entry.url
                    for entry in record.fulltext_sources
                    if entry.format == "pdf" and is_probeable(entry.url)
                ]
                if not title or not urls:
                    continue
                identifier = record.doi or urls[0]
                if identifier in context.seen:
                    continue
                context.seen.add(identifier)
                candidates.append(Candidate(server, identifier, title, urls[0]))
            candidates = candidates[: max(0, target - len(rows))]

            batch, batch_unmeasured = process_candidates(client, candidates, context, tmpdir)
            rows.extend(batch)
            unmeasured += batch_unmeasured
            print(f"  {server}: {len(rows)}/{target} rows ({day})", file=sys.stderr)
            day -= timedelta(days=1)
    return rows, unmeasured


def _file_name(url: str) -> str:
    """The URL's last path segment, which is the file's own name."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser. Separated so tests can inspect defaults."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target", type=int, default=DEFAULT_TARGET, help="Rows to collect per source."
    )
    parser.add_argument(
        "--per-host-interval",
        type=float,
        default=PER_HOST_INTERVAL_SECONDS,
        help="Minimum seconds between two requests to the same host.",
    )
    parser.add_argument(
        "--source",
        choices=("europepmc", "biorxiv", "medrxiv", "both"),
        default="both",
        help="Which population to sample (default both).",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the corpus."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Concurrent fetches. The per-host request rate is unchanged.",
    )
    parser.add_argument(
        "--pdf-cache",
        type=Path,
        default=DEFAULT_PDF_CACHE,
        help="Where downloaded PDFs are kept between runs.",
    )
    parser.add_argument(
        "--no-pdf-cache",
        action="store_true",
        help="Fetch every PDF again rather than reusing a cached copy.",
    )
    return parser


def _journal_path(output: Path) -> Path:
    """Where a run's rows are appended as they land, beside its output."""
    return output.with_suffix(output.suffix + ".partial.jsonl")


def _open_cache(args: argparse.Namespace) -> Any | None:
    """The PDF cache for this run, or ``None`` when it is switched off.

    A cache that cannot be created is a warning and not a failure: it costs
    re-downloads on a resume, which is exactly what ``--no-pdf-cache`` asks
    for, and losing the whole run over it would be the worse outcome — the
    same call ``FullTextService`` makes for its own cache (issue #75).
    """
    if args.no_pdf_cache:
        return None
    from bmlib.fulltext.cache import FullTextCache

    try:
        return FullTextCache(cache_dir=args.pdf_cache)
    except (OSError, RuntimeError) as exc:
        print(
            f"WARNING: no PDF cache at {args.pdf_cache} ({exc}); every PDF will be "
            "downloaded again if this run is resumed",
            file=sys.stderr,
        )
        return None


def main() -> int:
    """Sample both populations, write the corpus, print the tables.

    Resumable: rows are appended to a JSONL journal beside the output as they
    land, and a later run loads it, skips the identifiers already collected,
    and tops each population up to ``--target``. Delete the journal to start
    over.
    """
    args = _build_arg_parser().parse_args()
    journal = _journal_path(args.output)
    previous = load_partial(journal)
    cache = _open_cache(args)
    context = RunContext(
        pace=_make_pacer(args.per_host_interval),
        journal=journal,
        seen=already_seen(previous),
        cache=cache,
        workers=args.workers,
    )
    if previous:
        print(f"Resuming from {journal}: {len(previous)} attempts already made", file=sys.stderr)

    # Rows and unmeasured attempts carried over from earlier runs, per source.
    # Both halves are needed: a resumed run that inherited only its successes
    # would compute the unmeasured share over a denominator missing its
    # failures, and the ERROR rule would pass by having forgotten.
    populations = tally_previous(previous)

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        if args.source in ("europepmc", "both"):
            done = len(populations.get("europepmc", ([], 0))[0])
            print(f"Sampling Europe PMC free PDFs ({done} already held)…", file=sys.stderr)
            sample_europepmc_rows(client, args.target - done, context)
        if args.source in ("biorxiv", "medrxiv", "both"):
            server = "medrxiv" if args.source == "medrxiv" else "biorxiv"
            done = len(populations.get(server, ([], 0))[0])
            print(f"Sampling {server} preprint PDFs ({done} already held)…", file=sys.stderr)
            sample_biorxiv_rows(client, args.target - done, context, server=server)

    # Re-tallied from the journal rather than merged in memory. Every attempt
    # this run made is already in there, and the journal is the only place
    # where an id's outcomes can be reduced to its *last* one: merging a
    # freshly-collected row onto a tally that still counts the same id as
    # unmeasured counts it twice, which is exactly what a retried failure is.
    populations = tally_previous(load_partial(journal))

    rows = [row for population, _ in populations.values() for row in population]
    rows.sort(key=lambda row: (row.get("source", ""), row.get("id", "")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} rows to {args.output}", file=sys.stderr)

    failed = False
    for source, (population, unmeasured) in populations.items():
        lines = summarise(source, population, unmeasured)
        failed = failed or any("ERROR" in line for line in lines)
        for line in lines:
            print(line)

    _print_unrelated(rows)
    return 1 if failed else 0


def _print_unrelated(rows: list[dict[str, Any]]) -> None:
    """List every junk metadata title verbatim.

    The shapes are read off this listing, so it prints every one rather than a
    summary: a shape that appears twice in 300 PDFs is exactly what a summary
    would drop, and it is a shape the backstop may need.
    """
    junk = [row for row in rows if row.get("bucket") == "unrelated"]
    print(f"\nunrelated metadata titles ({len(junk)}):")
    for row in junk:
        print(f"    [{row['source']}] {row['metadata_title']!r}  (creator={row['creator']!r})")


if __name__ == "__main__":
    raise SystemExit(main())
