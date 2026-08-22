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

Eight questions, each answering a decision the parser makes:

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
5. **The same question for a ``<table-wrap>``**, counted separately (#135).
   #127 routes a table's deposits through #117's ranking, which was measured
   on figures alone; until a draw finds a table carrying more than one, that
   rule is reasoned onto tables rather than observed on them.
6. **Is a ``<graphic>`` ever owned by something other than its exhibit**, and
   does a ``<table-wrap>`` carry one with no ``<table>`` (#127) — or with
   both, which is the rendition ``to_html()`` drops? Plus the XLink prefix
   actually used, which is what #128 turns on.
7. **Is a ``<caption>`` a direct child of the element it describes**, what
   else carries one inside an exhibit, and how often does one nest inside
   another? Questions 1 and 2 again for the element routed by the rule
   issue #123 installed, plus that issue's own prevalence, which had been
   measured against nothing.
8. **What owns a ``<title>`` that a section was open for?** The population
   issues #125 and #130 are about — a ``<fn-group>``, a ``<ref-list>``, a
   ``<boxed-text>``'s ``<caption>`` — every one of which used to rename the
   enclosing section, leaving not a blank but a heading the publisher never
   wrote.

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
a re-run and nothing else. The written corpus records the ``window`` it was
drawn from, because the strata are counted back from *today* and the same
command run later draws a different sample.

``--months-ago`` displaces the whole stratified draw backwards by whole
months. The default window is the last two years — born-digital XML — and at
least one population is not in it at all: #127's image-only tables measure 0
of 642 tables there and 11 of 93 in a draw ending 28 years back. A displaced
run must name its own ``-o``; writing one to the default path would replace
the recent corpus, or pool the two windows through the shared journal.

Usage::

    uv run python scripts/sample_jats_exhibits.py --target 300
    uv run python scripts/sample_jats_exhibits.py --target 300 --months-ago 336 \
        -o tests/data/jats_exhibits.backfill.json
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

# And its own idea of a wrapper that does not take ownership of a <graphic>,
# stated here rather than imported so the measurement can disagree with the
# parser's `_GRAPHIC_TRANSPARENT_WRAPPERS`.
_TRANSPARENT_WRAPPERS = frozenset({"alternatives", "p"})

# The counters that arrived with issue #135, and the sentinel a row written
# before them is loaded with. Zero is not usable as "absent" here: it is also
# what a draw in which no table deposits an image genuinely measures, and #127
# is the case of a population that reads as empty in the wrong window. A
# negative value cannot be produced by counting, so a row carrying one predates
# the counter rather than saying anything about deposits — asked per row by
# `Totals.measured`, never of the sum, which one stale row cannot turn negative.
NOT_MEASURED = -1
_TABLE_SIDE_COUNTERS = (
    "tables_with_both",
    "tables_with_graphic",
    "tables_multi_graphic",
    "tables_last_is_thumb",
    "tables_first_is_thumb",
)
# The second such generation, arriving with issues #123, #125 and #130 — the
# caption and title owner rules. Same sentinel and same reason: a draw in
# which no caption nests measures zero, and so does a corpus written before
# anything counted them.
_OWNER_SIDE_COUNTERS = (
    "captions",
    "nested_captions",
    "exhibits_with_direct_caption",
    "exhibits_with_descendant_caption",
    "sections",
    "sections_with_direct_title",
)


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
    tables_with_both: int = 0
    tables_with_graphic: int = 0
    tables_multi_graphic: int = 0
    tables_last_is_thumb: int = 0
    tables_first_is_thumb: int = 0
    href_prefixes: Counter[str] = field(default_factory=Counter)
    captions: int = 0
    nested_captions: int = 0
    exhibits_with_direct_caption: int = 0
    exhibits_with_descendant_caption: int = 0
    exhibit_caption_owners: Counter[str] = field(default_factory=Counter)
    sections: int = 0
    sections_with_direct_title: int = 0
    section_renaming_titles: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the journal and the corpus file."""
        out: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            out[key] = dict(value) if isinstance(value, Counter) else value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArticleMeasurement:
        """Rebuild a row written by :meth:`to_dict`.

        A counter the row does not carry is set to ``NOT_MEASURED`` rather
        than left at its zero default. Every one of these arrived with issue
        #135, so an older corpus or journal carries none of them and each
        would otherwise sum to zero — which is exactly what a genuine "no
        table deposits an image" draw looks like, and reading one as the other
        is the mistake #127 spent two windows disproving.
        :meth:`Totals.measured` is what tests it.
        """
        row = cls(pmcid=str(data["pmcid"]))
        for name in (*_TABLE_SIDE_COUNTERS, *_OWNER_SIDE_COUNTERS):
            if name not in data:
                setattr(row, name, NOT_MEASURED)
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
    transparent = set(_TRANSPARENT_WRAPPERS)

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
            if tag == "caption":
                row.captions += 1
                if "caption" in chain:
                    # Issue #123's own population: a <caption> inside another.
                    # Held as a boolean, the inner close truncated the outer.
                    row.nested_captions += 1
                if exhibit is not None:
                    row.exhibit_caption_owners[chain[-1] if chain else exhibit] += 1
            elif tag == "sec":
                row.sections += 1
                if any(_local(c.tag) == "title" for c in child):
                    row.sections_with_direct_title += 1
            elif tag == "title" and exhibit is None and "sec" in chain and chain[-1] != "sec":
                # The population issues #125 and #130 are about, and only it:
                # a <title> that a section was open for, owned by something
                # else. `exhibit is None` because the parser already dropped
                # a <title> inside a <fig> or <table-wrap> before this fix, so
                # counting those would report a change that was not made.
                row.section_renaming_titles[chain[-1]] += 1
            walk(child, exhibit, chain + [tag], exhibit_depth)

    walk(root, None, [], 0)
    return row


def _owned(el: ET.Element, wanted: str) -> list[ET.Element]:
    """The *wanted* descendants that *el* itself owns.

    The same judgement :func:`_record_graphic` makes, applied downwards: a
    descendant belongs to *el* only if every element between them is a
    transparent wrapper. A whole-subtree ``el.iter()`` counts a ``<td>``'s
    inline image and a nested exhibit's deposit as the outer exhibit's, which
    is not what the parser routes — issue #135's stated residual, and not a
    theoretical one: of the ten recent-window tables carrying a ``<graphic>``
    anywhere, the four holding more than one are the two articles depositing
    35 of the draw's 36 ``<td>``-owned images.

    The **figure** counters deliberately keep the subtree walk: their
    percentages are cited at ``offer_graphic`` and in CLAUDE.md, and
    re-scoping them silently would invalidate every one. Both committed draws
    record zero nested exhibits and every foreign owner is a ``<td>``, which
    can only sit under a ``<table-wrap>``, so the two walks agree on the
    figure side in this evidence anyway.

    Args:
        el: The exhibit element.
        wanted: The local name to collect.

    Returns:
        The owned elements, in document order.
    """
    found: list[ET.Element] = []

    def descend(node: ET.Element) -> None:
        for child in node:
            name = _local(child.tag)
            if name == wanted:
                found.append(child)
            elif name in _TRANSPARENT_WRAPPERS:
                descend(child)

    descend(el)
    return found


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

    # The same pair for <caption>, which is now routed by its parent for the
    # reason <label> is — so it owes the same premise (#123).
    if any(_local(c.tag) == "caption" for c in el):
        row.exhibits_with_direct_caption += 1
    if any(_local(c.tag) == "caption" for c in el.iter() if c is not el):
        row.exhibits_with_descendant_caption += 1

    if tag == "fig":
        # A whole-subtree walk, unlike the table branch below — see `_owned`
        # for why the asymmetry is deliberate and why it costs nothing here.
        graphics = [g for g in el.iter() if _local(g.tag) == "graphic"]
        if graphics:
            row.figures_with_graphic += 1
        if len(graphics) > 1:
            row.figures_multi_graphic += 1
            if _is_thumbnail(graphics[-1]):
                row.last_is_thumb += 1
            if _is_thumbnail(graphics[0]):
                row.first_is_thumb += 1
    else:
        # The same three counts for a <table-wrap>, because #127 routes a
        # table's deposits through the *same* ranking a figure's go through
        # and nothing had measured that the rule holds there — issue #135,
        # which these counters answer as an empty population: across the two
        # committed draws no table carries a second owned deposit. Kept as
        # separate fields rather than folded into the figure ones: the figure
        # percentages are cited in `jats_parser` and in CLAUDE.md, and
        # silently widening their denominator would invalidate every one.
        graphics = _owned(el, "graphic")
        has_table = bool(_owned(el, "table"))
        if graphics:
            row.tables_with_graphic += 1
            if has_table:
                # Both renditions deposited. `to_html()` shows the markup and
                # drops the image, so this is the population that choice
                # discards — measured for the same reason the kept one is.
                row.tables_with_both += 1
            else:
                # A table deposited as an image and nothing else — issue #127.
                row.tables_image_only += 1
        if len(graphics) > 1:
            row.tables_multi_graphic += 1
            if _is_thumbnail(graphics[-1]):
                row.tables_last_is_thumb += 1
            if _is_thumbnail(graphics[0]):
                row.tables_first_is_thumb += 1


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


def _month_windows(months: int, today: date, skip: int = 0) -> list[tuple[str, str]]:
    """*months* whole calendar months, most recent first, after skipping *skip*.

    ``skip`` is what lets the draw reach material the default window cannot.
    The last two years of open-access deposits are born-digital XML, and some
    of the populations here are properties of *older* deposits — a
    ``<table-wrap>`` carrying nothing but a scanned image (issue #127) is the
    one this was added for. Counting the skipped months with the same
    arithmetic as the taken ones is what keeps a year boundary from drifting.

    Args:
        months: How many windows to build; at least one.
        today: The date to count back from, injected so tests need no clock.
        skip: How many whole months to step back before the first window;
            never negative.

    Returns:
        ``(first_day, last_day)`` ISO pairs, one per month.

    Raises:
        ValueError: If *months* is below 1 or *skip* is negative. Neither
            degrades gracefully — ``skip`` is both a loop bound and a slice
            index, so a negative one silently returns the *oldest* few of a
            list already shortened by it (``skip=-1, months=24`` yields one
            window from two years ago, ``skip=-24`` yields none at all), and
            the run then prints a rate with a Wilson interval over a draw
            nobody asked for. The same shape as ``sync()``'s negative
            ``recheck_days``, and refused at the same place: the entry.
    """
    if months < 1:
        raise ValueError(f"months must be at least 1, got {months}")
    if skip < 0:
        raise ValueError(f"skip must not be negative, got {skip}")
    windows: list[tuple[str, str]] = []
    year, month = today.year, today.month
    for _ in range(skip + months):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        first = date(year, month, 1)
        last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        windows.append((first.isoformat(), last.isoformat()))
    return windows[skip:]


def open_access_pmcids(
    client: httpx.Client,
    pace: Any,
    target: int,
    months: int = SAMPLE_MONTHS,
    skip_months: int = 0,
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
        skip_months: How many whole months to step back before the first
            stratum — see :func:`_month_windows`. The default draw is the last
            two years, which is born-digital XML; an older draw is what reaches
            the back-filled deposits some populations live in.

    Returns:
        Up to *target* identifiers, interleaved across the strata so a short
        month cannot silently drop out of the sample.
    """
    per_window: list[list[str]] = []
    wanted = max(1, target // max(1, months) + 1)
    for first, last in _month_windows(months, date.today(), skip=skip_months):
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

    def measured(self, attribute: str) -> bool:
        """Did **every** row actually carry *attribute*?

        Asked per row rather than of the sum, because the sentinel is a small
        negative and the sum is not: one stale row among three hundred fresh
        ones still totals positive, and the population would then print as a
        rate that quietly omits it. A journal is topped up across runs, so a
        mixed one is the ordinary case rather than a corner.
        """
        return all(getattr(r, attribute) >= 0 for r in self.rows)

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

    # Issue #135. The ranking these deposits go through was measured on
    # figures alone and reasoned onto tables; this is what would settle it.
    print("\n5. SEVERAL <graphic> PER TABLE  (issue #135 — #117's rule, unmeasured here)")
    tables_with_graphic = totals.sum_of("tables_with_graphic")
    tables_multi = totals.sum_of("tables_multi_graphic")
    tables_last = totals.sum_of("tables_last_is_thumb")
    tables_first = totals.sum_of("tables_first_is_thumb")
    if not all(totals.measured(name) for name in _TABLE_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter (issue #135). Re-run to fill it.")
    else:
        print(f"   tables carrying a <graphic>            : {tables_with_graphic}")
        print(
            f"   ...carrying more than one              : "
            f"{tables_multi:>6}  {_pct(tables_multi, tables_with_graphic)}"
        )
        print(
            f"   ...whose LAST deposit is a thumbnail   : "
            f"{tables_last:>6}  {_pct(tables_last, tables_with_graphic)}"
        )
        print(
            f"   ...whose FIRST deposit is a thumbnail  : "
            f"{tables_first:>6}  {_pct(tables_first, tables_with_graphic)}"
        )

    print("\n6. OWNERSHIP, IMAGE-ONLY TABLES AND THE XLINK PREFIX")
    foreign = totals.counter_of("foreign_owned_graphics")
    print(f"   <graphic> owned by a non-exhibit inside one: {sum(foreign.values())}")
    for name, count in foreign.most_common(8):
        print(f"      {name:<23} {count:>6}")
    image_only = totals.sum_of("tables_image_only")
    # A share of the TABLES, not of every exhibit: this population is the one
    # measured on two windows, and a bare count cannot be compared across draws
    # of different sizes while a figure-heavy draw would dilute an exhibit-wide
    # denominator. Issue #127.
    print(
        f"   <table-wrap> with a <graphic> and no <table>: "
        f"{image_only:>6}  {_pct(image_only, tables)}   (issue #127)"
    )
    both = totals.sum_of("tables_with_both")
    both_rate = (
        f"{both:>6}  {_pct(both, tables)}"
        if totals.measured("tables_with_both")
        else "NOT MEASURED (issue #135)"
    )
    print(f"   <table-wrap> with a <graphic> AND a <table> : {both_rate}   (to_html drops it)")
    print(f"   xlink href namespaces: {dict(totals.counter_of('href_prefixes'))}   (issue #128)")

    print("\n7. NESTING  (issue #115's population)")
    nested_articles = sum(1 for r in totals.rows if r.nested_figures or r.nested_tables)
    print(f"   nested <fig>                           : {totals.sum_of('nested_figures')}")
    print(f"   nested <table-wrap>                    : {totals.sum_of('nested_tables')}")
    print(
        f"   articles nesting an exhibit            : {nested_articles:>6}  "
        f"{_pct(nested_articles, totals.articles)}"
    )

    print("\n8. GRAPHIC ATTRIBUTE VALUES  (counted before any allow-list)")
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

    # Issues #123, #125 and #130 — the caption and title owner rules. Sections
    # 1 and 2 asked these of <label>; a <caption> is now routed the same way
    # and owes the same premise.
    print("\n9. IS A <caption> A DIRECT CHILD OF ITS EXHIBIT?  (issue #123's premise)")
    if not all(totals.measured(name) for name in _OWNER_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        direct_caption = totals.sum_of("exhibits_with_direct_caption")
        descendant_caption = totals.sum_of("exhibits_with_descendant_caption")
        print(f"   exhibits with a direct-child <caption> : {direct_caption}")
        print(f"   exhibits with a descendant <caption>   : {descendant_caption}")
        print(
            "   PREMISE HOLDS: no exhibit carries its caption only indirectly"
            if direct_caption >= descendant_caption
            else "   PREMISE VIOLATED: an exhibit carries a caption only indirectly"
        )
        captions = totals.sum_of("captions")
        nested = totals.sum_of("nested_captions")
        print(
            f"   <caption> nested inside another        : "
            f"{nested:>6}  {_pct(nested, captions)}   of {captions}"
        )
        owners = totals.counter_of("exhibit_caption_owners")
        total_owned = sum(owners.values())
        print("   what owns a <caption> inside an exhibit:")
        for name, count in owners.most_common(10):
            own = "  <-- the exhibit itself" if name in _EXHIBITS else ""
            print(f"      {name:<26} {count:>6}  {_pct(count, total_owned)}{own}")

    print("\n10. WHAT OWNS A <title> A SECTION WAS OPEN FOR  (issues #125, #130)")
    if not all(totals.measured(name) for name in _OWNER_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        sections = totals.sum_of("sections")
        print(f"   <sec> elements                         : {sections}")
        print(
            f"   ...carrying a direct-child <title>     : "
            f"{totals.sum_of('sections_with_direct_title'):>6}"
        )
        renaming = totals.counter_of("section_renaming_titles")
        stolen = sum(renaming.values())
        affected = totals.articles_where("section_renaming_titles")
        print(
            f"   <title> inside a <sec>, owned elsewhere: "
            f"{stolen:>6}   in {affected} articles  {_pct(affected, totals.articles)}"
        )
        for name, count in renaming.most_common(12):
            print(f"      {name:<26} {count:>6}  {_pct(count, stolen)}")
        if not renaming:
            print("      (none — no section title was overwritten in this draw)")
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
        "--months-ago",
        type=int,
        default=0,
        help=(
            "Step back this many whole months before the first stratum. The "
            "default draw is born-digital XML; use this to reach older, "
            "back-filled deposits (issue #127's population lives there)."
        ),
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the corpus."
    )
    parser.add_argument(
        "--per-host-interval", type=float, default=0.7, help="Minimum seconds between requests."
    )
    return parser


def _validate_args(args: argparse.Namespace) -> str | None:
    """Refuse a run that would print a rate over a draw nobody asked for.

    Returns the reason, or ``None`` when the run may proceed. Two rules:

    *The window arithmetic is not negotiable.* ``--months-ago`` and
    ``--months`` are checked here as well as in :func:`_month_windows`,
    because argparse's ``type=int`` accepts a minus sign happily and the
    degradation is silent — see that function's ``Raises``.

    *A displaced draw may not land on the default output.* The journal is
    derived from ``--output``, so a run with ``--months-ago`` and no ``-o``
    either overwrites the recent corpus with an older window under the recent
    corpus's name, or — journal present — tops one window's rows up with
    another's and prints the pooled result as one rate. This PR's whole claim
    is that the window decides the answer (0 of 642 recent tables against 11
    of 93 from 1996-1998), so pooling two windows produces a number
    describing neither. Naming an explicit ``-o`` is the whole fix.
    """
    if args.months < 1:
        return f"--months must be at least 1, got {args.months}"
    if args.months_ago < 0:
        return f"--months-ago must not be negative, got {args.months_ago}"
    if args.months_ago and args.output == DEFAULT_OUTPUT:
        return (
            f"--months-ago {args.months_ago} draws a displaced window, which must not "
            f"be written to {DEFAULT_OUTPUT} — that path is the recent draw, and its "
            "journal would pool the two. Pass an explicit -o, as "
            "tests/data/jats_exhibits.backfill.json was."
        )
    return None


def main() -> int:
    """Measure every population, print the tables, write the corpus.

    Resumable: rows land in a JSONL journal beside the output, and a later run
    tops the sample up rather than starting over.
    """
    args = _build_arg_parser().parse_args()
    refusal = _validate_args(args)
    if refusal is not None:
        sys.stderr.write(f"{refusal}\n")
        return 2
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

    # Resolved once, before any request, so the corpus can state the window it
    # was drawn from. Without it "1996-1998" lives only in prose: the windows
    # are counted back from `date.today()`, so the same command a year from now
    # draws a different draw, and nothing in the written file would say which
    # one it is. Issue #132 is the same failure by another route — a cited
    # measurement whose corpus is not in the repo.
    windows = _month_windows(args.months, date.today(), skip=args.months_ago)
    pace = _make_pacer(args.per_host_interval)
    headers = {"User-Agent": _USER_AGENT}
    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=True) as client:
        pmcids = [
            p
            for p in open_access_pmcids(
                client, pace, args.target + 150, args.months, args.months_ago
            )
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
                "window": {
                    "months": args.months,
                    "months_ago": args.months_ago,
                    "first": windows[-1][0],
                    "last": windows[0][1],
                },
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
