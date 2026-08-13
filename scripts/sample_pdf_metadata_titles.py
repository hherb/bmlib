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
import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable
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

_BUCKETS = ("match", "truncated", "unrelated", "absent")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


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
    path = tmpdir / "sample.pdf"
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


def sample_europepmc_rows(
    client: Any, target: int, pace: Callable[[str], None]
) -> tuple[list[dict[str, Any]], int]:
    """Collect rows from Europe PMC's free ``?pdf=render`` PDFs.

    The same search ``sample_free_pdf_urls.py`` uses, so the two instruments
    look at comparable slices of the literature.

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
                    break
                payload = resp.json()
            except Exception as exc:
                print(f"  Europe PMC search failed: {exc}", file=sys.stderr)
                break
            hits = payload.get("resultList", {}).get("result", [])
            if not hits:
                break
            for hit in hits:
                if len(rows) >= target:
                    break
                title = (hit.get("title") or "").strip()
                url = _extract_free_pdf_url(hit)
                if not title or not url or not is_probeable(url):
                    continue
                identifier = hit.get("pmcid") or hit.get("id") or url
                body, measured = download(client, url, pace)
                if not measured or body is None:
                    unmeasured += 1
                    continue
                row = row_from_pdf(body, "europepmc", identifier, title, _file_name(url), tmpdir)
                if row is None:
                    unmeasured += 1
                    continue
                rows.append(row)
                print(f"  europepmc: {len(rows)}/{target} rows", file=sys.stderr)
            cursor = payload.get("nextCursorMark") or ""
            if not cursor:
                break
    return rows, unmeasured


def sample_biorxiv_rows(
    client: Any, target: int, pace: Callable[[str], None], server: str = "biorxiv"
) -> tuple[list[dict[str, Any]], int]:
    """Collect rows from bioRxiv/medRxiv preprint PDFs.

    Author-submitted files straight out of Word and LaTeX — the population
    whose metadata titles issue #56 is actually about. The URLs come from
    ``fetch_biorxiv`` itself rather than a re-spelled template, so what is
    sampled cannot drift from what bmlib fetches.

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
            pace(BIORXIV_BASE_URL)
            try:
                fetch_biorxiv(client, day, on_record=records.append, server=server)
            except Exception as exc:
                print(f"  bioRxiv fetch failed for {day}: {exc}", file=sys.stderr)
                day -= timedelta(days=1)
                continue
            for record in records:
                if len(rows) >= target:
                    break
                title = (record.title or "").strip()
                urls = [
                    entry.url
                    for entry in record.fulltext_sources
                    if entry.format == "pdf" and is_probeable(entry.url)
                ]
                if not title or not urls:
                    continue
                body, measured = download(client, urls[0], pace)
                if not measured or body is None:
                    unmeasured += 1
                    continue
                identifier = record.doi or urls[0]
                row = row_from_pdf(body, server, identifier, title, _file_name(urls[0]), tmpdir)
                if row is None:
                    unmeasured += 1
                    continue
                rows.append(row)
                print(f"  {server}: {len(rows)}/{target} rows", file=sys.stderr)
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
    return parser


def main() -> int:
    """Sample both populations, write the corpus, print the tables."""
    args = _build_arg_parser().parse_args()
    pace = _make_pacer(args.per_host_interval)
    populations: dict[str, tuple[list[dict[str, Any]], int]] = {}

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        if args.source in ("europepmc", "both"):
            print("Sampling Europe PMC free PDFs…", file=sys.stderr)
            populations["europepmc"] = sample_europepmc_rows(client, args.target, pace)
        if args.source in ("biorxiv", "medrxiv", "both"):
            server = "medrxiv" if args.source == "medrxiv" else "biorxiv"
            print(f"Sampling {server} preprint PDFs…", file=sys.stderr)
            populations[server] = sample_biorxiv_rows(client, args.target, pace, server=server)

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
