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

"""Measure the JATS exhibit populations that ``fulltext/jats_parser.py`` encodes.

The parser carries several rules about ``<fig>``, ``<table-wrap>``,
``<label>`` and ``<graphic>`` that were settled by measurement — issues #115,
#116 and #117 — and two curated lists, ``_ARCHIVAL_MIME_SUBTYPES`` /
``_ARCHIVAL_EXTENSIONS`` and ``_GRAPHIC_TRANSPARENT_WRAPPERS``. This is the
live runner those rest on, and the one to re-run **before changing any of
them**, the way ``sample_databank_names.py`` and ``sample_free_pdf_urls.py``
stand behind their own allow-lists. Issue #131 is why it exists: the rules
shipped without it, and the populations behind them lived only in a sibling
repository.

Five questions, each answering a decision the parser makes:

1. **Is a ``<label>`` a direct child of the exhibit it numbers?** This is the
   premise of the parent-based routing that replaced #116's footnote-depth
   counter. If an exhibit anywhere carries its label *only* indirectly, the
   rule loses that label and the premise is wrong.
2. **What else carries a ``<label>`` inside an exhibit?** The depth rule
   needed this enumerated; the parent rule does not. Measuring it says how
   much the enumeration was missing.
3. **Do ``<alternatives>`` members declare ``mime-subtype``?** The archival
   demotion is keyed on it, with an extension fallback for the undeclared
   case. Both tiers are dead code if nothing archival is ever deposited.
4. **How are several ``<graphic>`` deposited?** Counts per figure, and which
   end the thumbnail sits at — the population behind #117's ranking.
5. **Is a ``<graphic>`` ever owned by something other than its exhibit**, and
   does a ``<table-wrap>`` carry one with no ``<table>`` (#127)? Plus the
   XLink prefix actually used, which is what #128 turns on.

**It does not import the parser's predicates**, and a future refactor must not
"deduplicate" the two. A corpus labelled by the rule under test can only
confirm that rule — the standing rule ``sample_pdf_metadata_titles.py`` states
and the reason it carries its own title comparison. Everything here is a plain
``xml.etree`` walk over the raw deposit.

An article that could not be fetched or parsed is **unmeasured**: excluded
from every denominator, and reported as ERROR rather than as a rate once it
eats more than ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of the sample. A dead host
must not read as a clean population.

Writes ``tests/data/jats_exhibits.json``, or ``*.unreportable.json`` when any
population trips that threshold — so a throttled run cannot replace evidence a
later reader takes as measured. The journal keeps every row, so refusing costs
a re-run and nothing else.

Usage::

    uv run python scripts/sample_jats_exhibits.py --target 300
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import httpx
except ImportError:  # pragma: no cover - the script is a live runner
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(1) from None

# Pacing, throttling and the unmeasured-share rule are shared with the other
# live runners; see `_sampling`'s docstring for why they are not duplicated.
# `scripts/` is not a package — running a script puts this directory on
# sys.path as sys.path[0], and the test files that load one by path insert it
# explicitly.
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

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
PAGE_SIZE = 100
DEFAULT_TARGET = 300
# Monthly strata the sample is drawn from — see `open_access_pmcids`.
SAMPLE_MONTHS = 24
DEFAULT_OUTPUT = Path("tests/data/jats_exhibits.json")
_USER_AGENT = f"bmlib-jats-exhibit-sampler/{__version__} (+https://github.com/hherb/bmlib)"

# The two elements that are exhibits. Structural, and complete: these are the
# only JATS elements the parser builds a figure or table from.
_EXHIBITS = frozenset({"fig", "table-wrap"})

# This sampler's OWN notion of an archival master, kept deliberately separate
# from the parser's `_ARCHIVAL_MIME_SUBTYPES` / `_ARCHIVAL_EXTENSIONS`. It is
# wider than the parser's on purpose: the question being measured is "what
# does the corpus deposit", and reusing the parser's narrower set would make
# the answer agree with the rule by construction.
_ARCHIVAL_HINTS = frozenset({"tiff", "tif", "eps", "ps", "postscript", "svg", "pdf", "ai"})

# Likewise this sampler's own thumbnail test, not the parser's.
_THUMB_PATTERN = re.compile(r"thumb", re.IGNORECASE)


def _local(tag: str) -> str:
    """The local name of a possibly namespace-qualified tag."""
    return tag.split("}")[-1]


def _href(el: ET.Element) -> str:
    """The element's ``href``, whatever namespace prefix it was written with."""
    for key, value in el.attrib.items():
        if _local(key) == "href":
            return value
    return ""


def _extension(href: str) -> str:
    """The lowercased file extension of *href*, or ``""`` if it has none."""
    last = href.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
    return "." + last.rsplit(".", 1)[-1].lower() if "." in last else ""


@dataclass
class ArticleMeasurement:
    """Everything one article contributes to the populations below."""

    pmcid: str
    figures: int = 0
    tables: int = 0
    nested_figures: int = 0
    nested_tables: int = 0
    exhibits_with_direct_label: int = 0
    exhibits_with_descendant_label: int = 0
    label_parents: Counter[str] = field(default_factory=Counter)
    figures_with_graphic: int = 0
    figures_multi_graphic: int = 0
    last_is_thumb: int = 0
    first_is_thumb: int = 0
    alternatives_members: int = 0
    alternatives_declaring_mime: int = 0
    alternatives_archival: int = 0
    graphics: int = 0
    graphic_extensions: Counter[str] = field(default_factory=Counter)
    content_type_values: Counter[str] = field(default_factory=Counter)
    specific_use_values: Counter[str] = field(default_factory=Counter)
    foreign_owned_graphics: Counter[str] = field(default_factory=Counter)
    tables_image_only: int = 0
    href_prefixes: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the journal and the corpus file."""
        out: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            out[key] = dict(value) if isinstance(value, Counter) else value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArticleMeasurement:
        """Rebuild a row written by :meth:`to_dict`."""
        row = cls(pmcid=str(data["pmcid"]))
        for key, value in data.items():
            if key == "pmcid":
                continue
            current = getattr(row, key, None)
            if isinstance(current, Counter):
                setattr(row, key, Counter(value))
            elif current is not None or isinstance(value, int):
                setattr(row, key, value)
        return row


def _is_thumbnail(el: ET.Element) -> bool:
    """This sampler's own thumbnail test — not the parser's."""
    return bool(
        _THUMB_PATTERN.search(el.attrib.get("content-type", ""))
        or _THUMB_PATTERN.search(el.attrib.get("specific-use", ""))
    )


def _looks_archival(el: ET.Element) -> bool:
    """This sampler's own archival test, deliberately wider than the parser's."""
    subtype = (el.attrib.get("mime-subtype") or "").lower()
    if subtype in _ARCHIVAL_HINTS:
        return True
    return _extension(_href(el)).lstrip(".") in _ARCHIVAL_HINTS


def measure_article(pmcid: str, xml: bytes) -> ArticleMeasurement | None:
    """Walk one article's JATS and record every population it contributes to.

    Args:
        pmcid: The article's PMC identifier, used only to label the row.
        xml: The raw ``fullTextXML`` body.

    Returns:
        The measurement, or ``None`` if the document would not parse — which
        makes the article *unmeasured* rather than empty.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    row = ArticleMeasurement(pmcid=pmcid)
    # Ownership is judged against this sampler's own idea of a transparent
    # wrapper, stated here rather than imported so the measurement can
    # disagree with the parser.
    transparent = {"alternatives", "p"}

    def walk(el: ET.Element, exhibit: str | None, chain: list[str], exhibit_depth: int) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag in _EXHIBITS:
                _record_exhibit(child, tag, exhibit_depth, row)
                walk(child, tag, [tag], exhibit_depth + 1)
                continue
            if tag == "label" and exhibit is not None:
                row.label_parents[chain[-1] if chain else exhibit] += 1
                continue
            if tag == "graphic":
                _record_graphic(child, exhibit, chain, transparent, row)
                continue
            walk(child, exhibit, chain + [tag], exhibit_depth)

    walk(root, None, [], 0)
    return row


def _record_exhibit(el: ET.Element, tag: str, depth: int, row: ArticleMeasurement) -> None:
    """Count one ``<fig>`` or ``<table-wrap>`` and its label placement."""
    if tag == "fig":
        row.figures += 1
        if depth:
            row.nested_figures += 1
    else:
        row.tables += 1
        if depth:
            row.nested_tables += 1

    direct = [c for c in el if _local(c.tag) == "label"]
    descendant = [c for c in el.iter() if _local(c.tag) == "label" and c is not el]
    if direct:
        row.exhibits_with_direct_label += 1
    if descendant:
        row.exhibits_with_descendant_label += 1

    graphics = [g for g in el.iter() if _local(g.tag) == "graphic"]
    if tag == "fig":
        if graphics:
            row.figures_with_graphic += 1
        if len(graphics) > 1:
            row.figures_multi_graphic += 1
            if _is_thumbnail(graphics[-1]):
                row.last_is_thumb += 1
            if _is_thumbnail(graphics[0]):
                row.first_is_thumb += 1
    elif graphics and not any(_local(c.tag) == "table" for c in el.iter()):
        # A table deposited as an image and nothing else — issue #127.
        row.tables_image_only += 1


def _record_graphic(
    el: ET.Element,
    exhibit: str | None,
    chain: list[str],
    transparent: set[str],
    row: ArticleMeasurement,
) -> None:
    """Count one ``<graphic>`` deposit, its attributes and its owner."""
    row.graphics += 1
    href = _href(el)
    row.graphic_extensions[_extension(href) or "(none)"] += 1
    for key in el.attrib:
        if _local(key) == "href":
            namespace = key.split("}")[0].lstrip("{") if "}" in key else "(no namespace)"
            row.href_prefixes[namespace] += 1
    # Counted BEFORE any allow-list filters, so the table can show a value the
    # parser never accepts — issue #79's rule, which is exactly how a missing
    # allow-list member becomes visible.
    if el.attrib.get("content-type"):
        row.content_type_values[el.attrib["content-type"].lower()] += 1
    if el.attrib.get("specific-use"):
        row.specific_use_values[el.attrib["specific-use"].lower()] += 1

    if "alternatives" in chain:
        row.alternatives_members += 1
        if el.attrib.get("mime-subtype"):
            row.alternatives_declaring_mime += 1
        if _looks_archival(el):
            row.alternatives_archival += 1

    owner = next((name for name in reversed(chain) if name not in transparent), None)
    if exhibit is not None and owner is not None and owner not in _EXHIBITS:
        row.foreign_owned_graphics[owner] += 1


def _fetch(client: httpx.Client, url: str, pace: Any) -> bytes | None:
    """GET *url*, honouring throttling. ``None`` means unmeasured, not empty."""
    if not is_probeable(url):
        return None
    for attempt in range(MAX_PROBE_ATTEMPTS):
        pace(url)
        try:
            response = client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code in (429, 503) and attempt + 1 < MAX_PROBE_ATTEMPTS:
            _sleep_for(_throttle_delay(response, attempt))
            continue
        if response.status_code != 200:
            return None
        return response.content
    return None


def _month_windows(months: int, today: date) -> list[tuple[str, str]]:
    """The last *months* whole calendar months, most recent first.

    Args:
        months: How many windows to build.
        today: The date to count back from, injected so tests need no clock.

    Returns:
        ``(first_day, last_day)`` ISO pairs, one per month.
    """
    windows: list[tuple[str, str]] = []
    year, month = today.year, today.month
    for _ in range(months):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        first = date(year, month, 1)
        last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        windows.append((first.isoformat(), last.isoformat()))
    return windows


def open_access_pmcids(
    client: httpx.Client, pace: Any, target: int, months: int = SAMPLE_MONTHS
) -> list[str]:
    """Draw open-access PMC identifiers, **stratified by publication month**.

    A single cursor walk from ``*`` returns a contiguous block of accessions,
    which is not a random sample of anything: the first live run of this
    sampler drew 120 articles of which 106 carried no exhibit at all, because
    the block happened to land in a run of abstract-only deposits. Taking a
    slice from each of the last *months* whole months spreads the draw across
    journals and time, and is what makes an exhibit rate a rate rather than a
    property of one accession range.

    Args:
        client: HTTP client.
        pace: Per-host pacer from :func:`_make_pacer`.
        target: How many identifiers to return.
        months: How many monthly strata to draw from.

    Returns:
        Up to *target* identifiers, interleaved across the strata so a short
        month cannot silently drop out of the sample.
    """
    per_window: list[list[str]] = []
    wanted = max(1, target // max(1, months) + 1)
    for first, last in _month_windows(months, date.today()):
        collected: list[str] = []
        cursor = "*"
        while len(collected) < wanted:
            query = f"OPEN_ACCESS:y AND IN_EPMC:y AND FIRST_PDATE:[{first} TO {last}]"
            url = (
                f"{EUROPE_PMC}/search?query={quote(query)}"
                f"&format=json&pageSize={PAGE_SIZE}&cursorMark={quote(cursor)}"
            )
            raw = _fetch(client, url, pace)
            if raw is None:
                break
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                break
            results = payload.get("resultList", {}).get("result", [])
            if not results:
                break
            collected.extend(r["pmcid"] for r in results if r.get("pmcid"))
            nxt = payload.get("nextCursorMark")
            if not nxt or nxt == cursor:
                break
            cursor = nxt
        per_window.append(collected[:wanted])

    # Interleaved, not concatenated: truncating a concatenation to *target*
    # would drop the last months entirely and undo the stratification.
    found: list[str] = []
    for index in range(wanted):
        for window in per_window:
            if index < len(window):
                found.append(window[index])
    return found[:target]


@dataclass
class Totals:
    """The populations, aggregated across every measured article."""

    articles: int = 0
    unmeasured: int = 0
    rows: list[ArticleMeasurement] = field(default_factory=list)

    def add(self, row: ArticleMeasurement) -> None:
        self.articles += 1
        self.rows.append(row)

    def sum_of(self, attribute: str) -> int:
        return sum(int(getattr(r, attribute)) for r in self.rows)

    def counter_of(self, attribute: str) -> Counter[str]:
        merged: Counter[str] = Counter()
        for row in self.rows:
            merged.update(getattr(row, attribute))
        return merged

    def articles_where(self, attribute: str) -> int:
        return sum(1 for r in self.rows if getattr(r, attribute))

    @property
    def attempts(self) -> int:
        return self.articles + self.unmeasured

    @property
    def unmeasured_share(self) -> float:
        return self.unmeasured / self.attempts if self.attempts else 0.0

    @property
    def reportable(self) -> bool:
        """Is the sample worth reporting a rate over?

        A rate computed from whatever got through heavy throttling is not a
        random sample of the population, so past the shared threshold this
        reports ERROR instead of a number that looks precise.
        """
        return bool(self.rows) and self.unmeasured_share <= UNMEASURED_SHARE_ERROR_THRESHOLD


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "n/a"
    low, high = wilson(part, whole)
    return f"{100 * part / whole:5.1f}%  [{100 * low:.1f}-{100 * high:.1f}]"


def print_report(totals: Totals) -> bool:
    """Print every population. Returns ``True`` if all of them were reportable."""
    print(f"\nArticles measured: {totals.articles}   unmeasured: {totals.unmeasured}")
    if not totals.reportable:
        print(
            f"\nERROR: {totals.unmeasured_share:.0%} of attempts were unmeasured "
            f"(threshold {UNMEASURED_SHARE_ERROR_THRESHOLD:.0%}). No rate below is evidence."
        )
        return False

    figures = totals.sum_of("figures")
    tables = totals.sum_of("tables")
    exhibits = figures + tables
    print(f"Exhibits: {figures} <fig> + {tables} <table-wrap> = {exhibits}\n")

    print("1. IS A <label> A DIRECT CHILD OF ITS EXHIBIT?  (the parent rule's premise)")
    direct = totals.sum_of("exhibits_with_direct_label")
    descendant = totals.sum_of("exhibits_with_descendant_label")
    print(f"   exhibits with a direct-child <label>   : {direct}")
    print(f"   exhibits with a descendant <label>     : {descendant}")
    print(
        "   PREMISE HOLDS: no exhibit carries its label only indirectly"
        if direct >= descendant
        else "   PREMISE VIOLATED: an exhibit carries a label only indirectly"
    )

    print("\n2. WHAT CARRIES A <label> INSIDE AN EXHIBIT")
    parents = totals.counter_of("label_parents")
    total_labels = sum(parents.values())
    for name, count in parents.most_common(12):
        own = "  <-- the exhibit itself" if name in _EXHIBITS else ""
        print(f"   {name:<26} {count:>6}  {_pct(count, total_labels)}{own}")

    print("\n3. <alternatives> DEPOSITS  (the archival demotion's population)")
    members = totals.sum_of("alternatives_members")
    declaring = totals.sum_of("alternatives_declaring_mime")
    print(f"   <graphic> inside <alternatives>        : {members}")
    print(f"   ...declaring mime-subtype              : {declaring}")
    print(f"   ...archival by subtype or extension    : {totals.sum_of('alternatives_archival')}")

    print("\n4. SEVERAL <graphic> PER FIGURE  (issue #117's population)")
    with_graphic = totals.sum_of("figures_with_graphic")
    multi = totals.sum_of("figures_multi_graphic")
    print(f"   figures carrying a <graphic>           : {with_graphic}")
    print(f"   ...carrying more than one              : {multi:>6}  {_pct(multi, with_graphic)}")
    print(
        f"   ...whose LAST deposit is a thumbnail   : "
        f"{totals.sum_of('last_is_thumb'):>6}  {_pct(totals.sum_of('last_is_thumb'), with_graphic)}"
    )
    first_thumb = totals.sum_of("first_is_thumb")
    print(
        f"   ...whose FIRST deposit is a thumbnail  : "
        f"{first_thumb:>6}  {_pct(first_thumb, with_graphic)}"
    )

    print("\n5. OWNERSHIP, IMAGE-ONLY TABLES AND THE XLINK PREFIX")
    foreign = totals.counter_of("foreign_owned_graphics")
    print(f"   <graphic> owned by a non-exhibit inside one: {sum(foreign.values())}")
    for name, count in foreign.most_common(8):
        print(f"      {name:<23} {count:>6}")
    image_only = totals.sum_of("tables_image_only")
    print(f"   <table-wrap> with a <graphic> and no <table>: {image_only}   (issue #127)")
    print(f"   xlink href namespaces: {dict(totals.counter_of('href_prefixes'))}   (issue #128)")

    print("\n6. NESTING  (issue #115's population)")
    nested_articles = sum(1 for r in totals.rows if r.nested_figures or r.nested_tables)
    print(f"   nested <fig>                           : {totals.sum_of('nested_figures')}")
    print(f"   nested <table-wrap>                    : {totals.sum_of('nested_tables')}")
    print(
        f"   articles nesting an exhibit            : {nested_articles:>6}  "
        f"{_pct(nested_articles, totals.articles)}"
    )

    print("\n7. GRAPHIC ATTRIBUTE VALUES  (counted before any allow-list)")
    for label, attribute in (
        ("content-type", "content_type_values"),
        ("specific-use", "specific_use_values"),
    ):
        values = totals.counter_of(attribute)
        print(f"   {label}:")
        for name, count in values.most_common(10):
            taken = "taken" if _THUMB_PATTERN.search(name) else "SKIPPED"
            print(f"      {name:<28} {count:>6}   {taken} by the thumbnail test")
        if not values:
            print("      (none deposited)")
    return True


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Articles to measure.")
    parser.add_argument(
        "--months", type=int, default=SAMPLE_MONTHS, help="Monthly strata to sample across."
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the corpus."
    )
    parser.add_argument(
        "--per-host-interval", type=float, default=0.7, help="Minimum seconds between requests."
    )
    return parser


def main() -> int:
    """Measure every population, print the tables, write the corpus.

    Resumable: rows land in a JSONL journal beside the output, and a later run
    tops the sample up rather than starting over.
    """
    args = _build_arg_parser().parse_args()
    journal = args.output.with_suffix(".journal.jsonl")
    totals = Totals()
    seen: set[str] = set()
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = ArticleMeasurement.from_dict(json.loads(line))
            seen.add(row.pmcid)
            totals.add(row)

    pace = _make_pacer(args.per_host_interval)
    headers = {"User-Agent": _USER_AGENT}
    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=True) as client:
        pmcids = [
            p
            for p in open_access_pmcids(client, pace, args.target + 150, args.months)
            if p not in seen
        ]
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as handle:
            for pmcid in pmcids:
                if totals.articles >= args.target:
                    break
                raw = _fetch(client, f"{EUROPE_PMC}/{pmcid}/fullTextXML", pace)
                row = measure_article(pmcid, raw) if raw else None
                if row is None:
                    totals.unmeasured += 1
                    continue
                totals.add(row)
                handle.write(json.dumps(row.to_dict()) + "\n")
                handle.flush()

    # Summarised *before* the corpus is written, so an unreportable run cannot
    # replace evidence a later reader takes as measured.
    ok = print_report(totals)
    destination = args.output if ok else args.output.with_suffix(".unreportable.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "articles": totals.articles,
                "unmeasured": totals.unmeasured,
                "rows": [r.to_dict() for r in sorted(totals.rows, key=lambda r: r.pmcid)],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {destination}")
    if not ok:
        print("At least one population is unreportable; the journal keeps every row.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
