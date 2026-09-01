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
   rule loses that label and the premise is wrong — and on the redrawn recent
   corpus it **is** wrong, for 7 exhibits of 6,944 in 7 of 997 articles. The
   report says ``PREMISE VIOLATED`` rather than printing a rate, because a
   premise is not a population.
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
   rule is reasoned onto tables rather than observed on them. Answered, and
   the answer is empty: of the 92 tables in the redrawn recent corpus that
   carry a ``<graphic>``, none carries two.
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

**The walk shares the parser's scope, and records what that costs.**
``<sub-article>`` and ``<response>`` open a region in which the parser fires
no handler at all (issue #110); this walk skips the same regions, so every
counter here is a suppressed-region-excluding count like the parser's. It used
to descend into them, which mattered most where JATS is densest: a peer-review
``<sub-article>`` names its reviewers with ``<contrib>`` elements, so
``contribs``, ``contrib_name_spellings`` and ``nested_contribs`` were all
inflated by a region the parser reads as no part of this article, and
``articles_losing_every_author`` could be suppressed outright by a single
reviewer's ``<name>``.

Scoping alone would have made the committed corpora unre-derivable, so issue
#138 was scope *and* redraw. What the scoping removed is kept rather than
discarded: each row carries an ``unscoped`` mapping naming every counter the
old whole-document walk would have reported differently, and its value there,
so the correction is measurable from the corpus instead of being asserted. A
row whose regions are absent carries an empty mapping, which is the right
reading both for "no nested article" and for "written before this existed" —
there is no difference to report either way, so ``unscoped`` deliberately has
no NOT-MEASURED sentinel where the other counter generations do. In the
committed recent draw 29 of 997 articles carry a region (145 regions in all)
and ``unscoped`` is non-empty for exactly those 29; the back-filled window
carries none.

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
drawn from, which for a package draw is ``(packages, first_year, last_year,
target, seed)`` and is the whole of what a reader needs to re-derive the
identifier list.

**The sample and the bytes come from different places, on purpose** (#138).
``--package`` draws deterministically from a named PMC OA baseline package, so
the draw is reproducible; ``--measure-europepmc`` then measures each drawn
article from Europe PMC's ``fullTextXML``, which is the rendition
``FullTextService`` feeds the parser. Both committed corpora are taken that
way, and the corpus records which under ``window["rendition"]``. The two
renditions disagree on cited populations rather than on details:
``last_is_thumb`` **differs in 156 of 300 compared articles, and where it
differs the archive measures 0 against 781 served**.

**Read that number only at that scope**, which is a rule this module's own
report exists to enforce and which its author got wrong first time.
``rendition_delta`` records a field only where the two renditions disagree,
so an agreeing article is absent from the file entirely and the archive's
total over all 300 cannot be recovered from it — summing the deltas gives a
sum over the disagreements, not a corpus total. Nor is there one mechanism to
name: an early draft said the archive deposits one bare ``<graphic
xlink:href="…-g001">`` per figure where Europe PMC synthesises an image/thumb
pair, which holds for a spot-checked article and not in general, since
``PMC12169732`` deposits its own four thumbnails as
``specific-use="thumbnail"`` where Europe PMC re-labels them
``content-type="thumb"`` and both renditions measure four — a **live
spot-check** (re-run 2026-09-02), that article having been drawn out of the
held sample, which is itself the caveat's point: the artifact carries
disagreements alone, so no archive total can be read off it whatever one
article does. The finding is
decisive either way; it was the statement that overreached.

``--compare-europepmc N`` produces that comparison and writes
``*.rendition.json``; run it before treating an archive-drawn figure as one
about the parser.

``--months-ago`` displaces the live stratified draw backwards by whole months,
and the live path is kept for the rendition it measures — it fetches
``fullTextXML`` directly, so it is the served rendition without the package
step, at the cost of a draw nobody else can repeat (#132). Its window is
counted back from *today*. The default is the last two years, born-digital
XML, and at least one population is not in it at all: #127's image-only tables
measured 0 of 662 tables in a recent draw and 11 of 93 in one ending 28 years
back. **Neither of those draws is in the repo** — both were replaced by the
#138 redraw, whose back-filled window carries no ``<table-wrap>`` at all — so
they are the historical evidence for the rule and not a figure a reader can
re-derive. A displaced run must name its own ``-o``; writing one to the
default path would replace the recent corpus, or pool the two windows through
the shared journal.

Usage — the two committed corpora, as taken. The recent one carries
``--compare-europepmc 300``, which is also what writes
``tests/data/jats_exhibits.rendition.json``, so its provenance describes this
same draw rather than an earlier one::

    uv run python scripts/sample_jats_exhibits.py \
        --package /path/to/oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz \
        --from-year 2023 --to-year 2025 --target 1000 --seed 0 \
        --measure-europepmc --compare-europepmc 300
    uv run python scripts/sample_jats_exhibits.py \
        --package /path/to/oa_comm_xml.PMC002xxxxxx.baseline.2025-06-26.tar.gz \
        --from-year 1996 --to-year 1998 --target 1000 --seed 0 \
        --measure-europepmc -o tests/data/jats_exhibits.backfill.json
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import IO, Any
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
# The default seed for a --package draw. Recorded in the corpus either way, so
# a non-default seed is still reproducible from the written window.
DEFAULT_SEED = 0
_USER_AGENT = f"bmlib-jats-exhibit-sampler/{__version__} (+https://github.com/hherb/bmlib)"

# The calendar-year span of the default *package* corpus
# (`tests/data/jats_exhibits.json`, drawn from `PMC012xxxxxx` against the
# 2025-06-26 baseline snapshot — see
# docs/superpowers/specs/2026-09-01-jats-corpus-redraw-design.md). This is
# NOT the live source's window: `_month_windows(SAMPLE_MONTHS, date.today())`
# slides one month per month and can never be pinned to a calendar year, so
# it cannot be re-derived from that function. A `--package` window not
# wholly contained in [_RECENT_WINDOW_FIRST_YEAR, _RECENT_WINDOW_LAST_YEAR]
# is a displaced draw, and the pooling rule `--months-ago` already carries
# applies to it for the same reason.
_RECENT_WINDOW_FIRST_YEAR = 2023
_RECENT_WINDOW_LAST_YEAR = 2025

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

# The two elements the parser suppresses entirely (#110). Restated here
# rather than imported so this module needs nothing from
# `bmlib.fulltext.jats_parser` — unlike `_ARCHIVAL_HINTS` / `_THUMB_PATTERN` /
# `_TRANSPARENT_WRAPPERS` above, which must deliberately *differ* from the
# parser's own sets, this one must be identical to it: it defines the scope
# the walk is measuring, not a judgement the walk is free to disagree with.
# The set is complete for a structural reason — exactly three JATS elements
# admit `<front>`/`<front-stub>` and `<body>`, and the third is `<article>`
# itself.
_NESTED_ARTICLE_ELEMENTS = frozenset({"sub-article", "response"})

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
# The third generation, arriving with issues #120 and #140 — the two spellings
# of a contributor's name that give one undivided string. Same sentinel and
# same reason as the two above: an article naming every contributor with
# `<name>` measures zero here, and so does a row written before anything
# counted them.
_CONTRIB_SIDE_COUNTERS = (
    "contribs",
    "nested_contribs",
    "collabs_with_a_roster",
    "articles_losing_every_author",
)
# The fourth such generation, arriving with issue #138 — the scoped-walk
# correction. Same sentinel and same reason as the three above: an article
# carrying no nested article genuinely measures zero here, so zero cannot
# mean "absent".
_SCOPE_SIDE_COUNTERS = ("nested_article_regions",)
# The fifth counter generation (issues #142, #143, #147, #150), and the same
# sentinel rule as the four before it: an article citing nothing and printing
# no formula genuinely measures zero here, so zero cannot also mean "this row
# predates the counter".
_WAITING_SIDE_COUNTERS = (
    "collabs_with_element_children",
    "contribs_multi_collab",
    "contribs_multi_string_name",
    "name_alternatives",
    "collab_alternatives",
    "disp_formulas",
    "inline_formulas",
    "tex_math",
    "mml_math",
    "formula_alternatives_both",
    "disp_formulas_with_label",
    "disp_formulas_image_only",
    "refs",
    "refs_note_only",
)
# Every counter generation, by the name the report and the corpus header use
# for it. A generation is a set of fields added in one commit, so a row written
# before that commit carries `NOT_MEASURED` for all of them together — which is
# what makes "which generations is this sample short of?" a well-formed
# question and a per-field answer redundant.
#
# `TestEveryCounterIsInAGeneration` walks the dataclass and fails on a field
# reaching neither this registry nor a *named* exclusion, because
# `TestTheAuditNetIsComplete` is the standing precedent that a rule enforced by
# prose is not enforced: a field added to `ArticleMeasurement` and forgotten
# here defaults to 0 on a stale row and reads as measured-empty, which is
# exactly the collapse the sentinel exists to prevent.
# The counters present since the first draw. Every row ever written carries
# them, so they need no sentinel and belong to no generation — which is a
# claim about this module's history, and the *only* reason a field may be
# absent from `_COUNTER_GENERATIONS`. Named rather than inferred, so that
# `TestEveryCounterIsInAGeneration` fails on a *new* field someone forgot
# instead of quietly widening to admit it.
_FIRST_GENERATION_COUNTERS = (
    "figures",
    "tables",
    "nested_figures",
    "nested_tables",
    "exhibits_with_direct_label",
    "exhibits_with_descendant_label",
    "figures_with_graphic",
    "figures_multi_graphic",
    "last_is_thumb",
    "first_is_thumb",
    "alternatives_members",
    "alternatives_declaring_mime",
    "alternatives_archival",
    "graphics",
    "tables_image_only",
)
_COUNTER_GENERATIONS: dict[str, tuple[str, ...]] = {
    "the table side (#135)": _TABLE_SIDE_COUNTERS,
    "caption and title owners (#123, #125, #130)": _OWNER_SIDE_COUNTERS,
    "contributor spellings (#120, #140)": _CONTRIB_SIDE_COUNTERS,
    "nested-article scoping (#138, #158)": _SCOPE_SIDE_COUNTERS,
    "the four waiting populations (#142, #143, #147, #150)": _WAITING_SIDE_COUNTERS,
}
# How a `<contrib>` names its contributor. JATS models it as
# `(name | string-name | collab | anonymous | ...)`, and the tail of that model
# is the point: `<on-behalf-of>` is in it too, and #130's `<list>` is the
# standing lesson that an enumeration written from the issues to hand misses
# the container nobody thought of. So the vocabulary is genuinely **open** —
# every child of a `<contrib>` is counted by its own name, the way
# `label_parents` does one section up — and these two sets only say what the
# reading *means*, never what gets counted.
#
# `_CONTRIB_NAMING_ELEMENTS` are the spellings that name a contributor at all;
# a `<contrib>` carrying none of them named nobody. `_CONTRIB_COLLECTED_BY_BMLIB`
# is the subset bmlib extracts, which is what the report annotates: `anonymous`
# names a contributor and is deliberately *not* collected, so marking it
# "collected" would put a false claim in the evidence a rule change is judged
# against.
_CONTRIB_COLLECTED_BY_BMLIB = frozenset({"name", "string-name", "collab"})
_CONTRIB_NAMING_ELEMENTS = _CONTRIB_COLLECTED_BY_BMLIB | {"anonymous", "on-behalf-of"}
# Children of a `<contrib>` that are not a name. Everything else prints,
# including a spelling nobody has listed.
_CONTRIB_NON_NAME_CHILDREN = frozenset({"xref", "aff", "address", "bio", "role", "email", "uri"})
# Wrap a name without being one, so the walk passes through them and counts
# what is inside — the parser's `_GRAPHIC_TRANSPARENT_WRAPPERS` idiom. Anything
# else is counted where it is found and *not* descended into, so a `<name>`
# prints as `name` rather than as its `<surname>` and `<given-names>` parts.
_CONTRIB_NAME_WRAPPERS = frozenset({"name-alternatives", "collab-alternatives"})


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


# A publication date, read from the raw bytes rather than from a parsed tree:
# the scan touches every article in a package and parsing them all to pick two
# elements costs more than the walk that follows it.
_PUB_DATE_RE = re.compile(rb"<pub-date[^>]*>(.*?)</pub-date>", re.DOTALL)
# The open tag is matched as `<year` followed by a `>` or by whitespace, never
# as `<year` followed by anything: `[^>]*` would accept `<yearly>`, and the
# element name has to be the whole name. The attributes themselves are not read
# — a `<year>` legally carries `@iso-8601-date`, `@calendar` and
# `@content-type`, and requiring a bare open tag made every attributed one
# undated and so *undrawable*, silently and along a publisher-correlated axis
# (measured: 17 of 97,909 in `PMC012xxxxxx`, all `<year iso-8601-date="...">`,
# all inside the recent window, and 14 of them one contiguous journal block,
# PMC12085917-PMC12085930 — bias, not noise; `PMC002xxxxxx` is unaffected,
# 0 of 122,576). That is the same silent publisher-correlated loss the prefix
# read is refused for in `article_year`'s own docstring, reached by another
# route. `(?!\d)` keeps a malformed five-digit year from reading as a
# four-digit one, which is what the old `</year>` anchor bought.
_YEAR_RE = re.compile(rb"<year(?:\s[^>]*)?>\s*(\d{4})(?!\d)")


class PackageError(Exception):
    """A ``--package`` path that is neither a directory nor a tarball."""


def article_year(xml: bytes) -> int | None:
    """The earliest year any ``<pub-date>`` declares, or ``None``.

    The date's declared *kind* is deliberately not consulted. The attribute is
    not one vocabulary — the back-filled range is dominated by
    ``pub-type="ppub"`` (2,868 of 3,000 articles), the recent window by
    ``pub-type="epub"`` (2,704), JATS 1.x spells it
    ``date-type="pub" publication-format="electronic"``, and ``pmc-release``,
    ``nihms-submitted`` and ``epreprint`` all appear — so an enumeration would
    be the kind #130's ``<list>`` is the standing lesson against. The obvious
    refinement, excluding the deposit and submission kinds (the two that could
    pull a date away from publication), was measured against this rule and
    **changes the earliest year in 0 of 3,000 articles in each window**.

    The ``<year>`` must be read from *inside* a ``<pub-date>``. Matching the
    open tag lazily to the next ``<year>`` anywhere after it reaches into
    ``<ref>`` and reports a cited work's year as this article's — the first
    draw made that mistake and produced articles "published" in 1861.

    **Deliberately whole-document, and so the one thing here that is not
    scoped to what the parser routes.** ``measure_article`` stops at a
    ``<sub-article>``/``<response>``; this does not, so a nested article's
    own ``<pub-date>`` is read and — ``min`` being the rule — an earlier one
    wins. That is a real difference on live input, 29 of 997 recent articles
    carrying such a region, and it is kept for two reasons: the window is a
    property of the *deposit*, which is what a reader re-deriving the draw
    downloads, and scoping it would mean parsing every article in a
    122,576-member package to decide whether to draw it. Measured at **0 of
    3,385** region-carrying articles moving their year, so the choice costs
    nothing today; it is stated because a silent divergence from the scoping
    rule the rest of this module follows is worse than a costly one.

    A ``<year>``'s **attributes are not read, and their presence is not a
    reason to skip it** — see `_YEAR_RE`, where requiring a bare open tag cost
    17 recent articles, 14 of them one journal block.

    Args:
        xml: The article's raw bytes, **whole**. A prefix read is measured in
            this plan's spec as both lossy and wrong.

    Returns:
        The year, or ``None`` — which makes the article undated and so
        undrawable, never "published in year zero". After the `_YEAR_RE` fix
        that outcome has **no measured population**: 0 of 97,909 in
        `PMC012xxxxxx` and 0 of 122,576 in `PMC002xxxxxx`. Do not restate it
        as "the document declares no `<pub-date>` carrying a `<year>`" — that
        was the old wording and it was false for 100% of the articles it
        actually fired on, every one of which carried both elements.
    """
    years = [
        int(year.group(1))
        for block in _PUB_DATE_RE.finditer(xml)
        if (year := _YEAR_RE.search(block.group(1)))
    ]
    return min(years) if years else None


def _is_gzip_file(path: Path) -> bool:
    """Whether *path* starts with the two-byte gzip magic number.

    Cheaper than ``tarfile.is_tarfile()``, which opens the file and parses a
    tar header — and, unlike it, false for an uncompressed ``.tar``:
    :func:`iter_package_articles` always opens with ``"r|gz"``, so a real
    tarball that is not gzip-compressed is not a package this module can
    read. Any read failure (permissions, a path that vanished between the
    caller's check and this one) reads as "not gzip", not as an error here —
    the caller's own open call is what should report it.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _opens_as_tarball(path: Path) -> bool:
    """Whether *path* opens as a gzip tarball and yields a member header.

    The gzip magic bytes alone are **not** enough, and treating them as
    enough was a live hole (issue #165): a ``.tar.gz`` that is a gzipped
    *non*-tar passed :func:`_validate_args`, reached
    :func:`package_candidates`, and raised `PackageError` there — uncaught
    anywhere in this module, and *after* the journal header had been
    written, so the run died on a traceback leaving a header with no rows.

    One member header is read rather than the whole archive: it is what
    tells a tar from a gzipped anything-else, and it costs a few kilobytes
    of a package that may be a gigabyte. An archive that opens and holds no
    members is a package with no articles, not a malformed one, so
    ``tar.next()`` returning ``None`` is accepted.
    """
    try:
        with tarfile.open(path, "r|gz") as tar:
            tar.next()
    except (tarfile.TarError, OSError, EOFError):
        return False
    return True


def _package_path_refusal(path: Path) -> str | None:
    """Why *path* is not something :func:`iter_package_articles` can read.

    ``None`` when it is. The two conditions are told apart because they call
    for different actions: a mistyped path is a typo, while a gzipped
    non-tar is a truncated or wrong download, and one message covering both
    sends the reader to check the wrong thing.

    Both :func:`_validate_args` and :func:`iter_package_articles` call this
    function directly — neither re-derives the disjunction — so the two
    cannot silently drift apart the way ``tarfile.is_tarfile()`` (true for
    an uncompressed ``.tar``) and this module's ``"r|gz"`` open mode (false
    for one) once did (issue #138): a first fix left ``_validate_args``
    calling this predicate while :func:`iter_package_articles` still tested
    its own inline copy of the same condition, which is exactly the
    two-tests-that-happen-to-agree shape this function exists to close off.
    """
    if path.is_dir():
        return None
    if not (path.is_file() and _is_gzip_file(path)):
        return f"{path} {_NOT_A_PACKAGE_PATH}"
    if not _opens_as_tarball(path):
        return f"{path} {_NOT_A_TARBALL}"
    return None


def _is_package_path(path: Path) -> bool:
    """Whether *path* is something :func:`iter_package_articles` can read."""
    return _package_path_refusal(path) is None


# The one place these sentences are written. `_validate_args` and
# `iter_package_articles` both build their refusal from them, so a wording
# change is made once rather than kept in sync by hand across two literals.
_NOT_A_PACKAGE_PATH = "is neither a package directory nor a gzip-compressed tarball"
_NOT_A_TARBALL = "is gzip-compressed but not a tarball"


def _package_identity(path: Path) -> str:
    """The public artifact name behind *path*, for the corpus header.

    A PMC OA baseline tarball's own filename carries the whole identity a
    reader needs to re-derive the draw — the subset (``oa_comm_xml``), the
    accession range, and the dated snapshot (``baseline.2025-06-26``) — but
    that identity lives in the *filename* alone: the tarball's internal
    top-level entry is the accession range on its own (``PMC012xxxxxx``),
    which is exactly what a plain extraction leaves on disk, and nothing
    inside the extracted files restates the rest — no manifest, no embedded
    date. So a bare directory's own name is missing two-thirds of the
    identity that makes the draw re-derivable, and nothing *inside* it can
    recover that loss.

    What can: the tarball itself, if it still sits beside the directory it
    was extracted into — the ordinary shape for a local mirror kept unpacked
    for repeated reads, and exactly how the recent corpus's own package was
    laid out (``PMC012xxxxxx/`` and
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` side by side in
    the same parent directory).

    A name match alone is not proof: two OA subsets (``oa_comm_xml``,
    ``oa_noncomm_xml``) partition the *same* accession range into disjoint
    articles, so a lone, unambiguous, gzip-compressed sibling can still be
    the *wrong* subset's tarball — one that happens to sit beside a directory
    it was never extracted from. A single cheap check catches this without
    reading the candidate whole (some of these tarballs are multi-gigabyte):
    the *first* article the candidate tarball yields must be one of this
    directory's own files. Since a PMCID belongs to at most one subset, a
    mismatched candidate's first article is not one this directory has, with
    the same probability as any other cross-subset collision — vanishing in
    practice. A single sample rather than a full comparison, because
    reading every member just to confirm a filename is disproportionate to
    what the filename is used for (a header string), and the corpus's own
    candidate walk already pays the cost of reading every *wanted* member in
    full where correctness of the draw itself is what is at stake.

    **Scope limit (review round 2): this check is cross-*subset* only.** A
    same-subset sibling from a *different* baseline snapshot date — e.g. a
    stale ``oa_comm_xml.PMC012xxxxxx.baseline.2024-01-01.tar.gz`` left beside
    a directory actually extracted from the ``2025-06-26`` snapshot — shares
    the same accession range and so, very plausibly, the same first article,
    which passes this check and records a confidently wrong *date*. Nothing
    on disk distinguishes two snapshots of the same subset well enough to
    check cheaply (both name the same articles; only some of those articles'
    *content* would differ between snapshots, which is exactly the
    expensive whole-file comparison this function avoids). Unmeasured how
    often this actually happens.

    Every fallback is reported to stderr, naming which of the three reasons
    it was (no candidate, more than one, or a candidate that failed the
    content check) — silently degrading to the bare name would leave "no
    sibling was found" and "a sibling was found and rejected" looking
    identical to a reader of the corpus alone.

    **This is the corpus header's field, not the journal's.** What it
    returns is a name a reader anywhere can re-download; it is deliberately
    not unique on this machine, and two different directories sharing an
    accession range share it. The journal's draw identity uses
    :func:`_package_location` beside it for exactly that reason — see there
    before merging the two.

    Args:
        path: A package directory or tarball, as passed to ``--package``.

    Returns:
        *path*'s own name where *path* is a file — a tarball's filename
        already carries its full identity, unchanged. For a directory: the
        name of a sibling file, in the same parent directory, whose name
        contains both *path*'s own name and ``baseline`` and which is itself
        gzip-compressed, confirmed by content — found and unambiguous.
        Otherwise, the directory's bare name, unchanged: a guessed identity
        naming the wrong snapshot is worse than an incomplete one naming
        none, so an absent, ambiguous, or unconfirmed sibling falls back
        rather than picks.
    """
    if path.is_file():
        return path.name
    # `path.name` is interpolated into the pattern, not written as one
    # ourselves, so any glob metacharacter it happens to contain (`[`, `]`,
    # `*`, `?`) must be escaped — otherwise a bracketed accession range would
    # be read as a character class instead of literal text, and either miss
    # its real sibling or match one it should not.
    pattern = f"*{glob.escape(path.name)}*baseline*"
    candidates = [
        sibling
        for sibling in path.parent.glob(pattern)
        if sibling.is_file() and _is_gzip_file(sibling)
    ]
    if not candidates:
        sys.stderr.write(
            f"{path}: no sibling baseline tarball found; recording the bare "
            "directory name, which loses the dated snapshot.\n"
        )
        return path.name
    if len(candidates) > 1:
        sys.stderr.write(
            f"{path}: {len(candidates)} candidate baseline tarballs match "
            "(ambiguous); recording the bare directory name rather than "
            "guessing which one.\n"
        )
        return path.name
    sibling = candidates[0]
    try:
        first = next(iter_package_articles(sibling), None)
    except (PackageError, tarfile.TarError):
        # A sibling that passed the gzip-magic-bytes filter above can still
        # be truncated, corrupted, or otherwise not a readable tar stream —
        # `tarfile` raises from several `TarError` subclasses depending on
        # exactly how it is broken, not only the `ReadError`
        # `iter_package_articles` converts to `PackageError`. Any of them
        # means "cannot confirm," not "crash the whole run."
        first = None
    if first is None or not (path / f"{first[0]}.xml").exists():
        sys.stderr.write(
            f"{path}: {sibling.name} does not look like this directory's own "
            "tarball (its first article is not one of this directory's "
            "files); recording the bare directory name instead.\n"
        )
        return path.name
    return sibling.name


def _package_location(path: Path) -> str:
    """Where *path* actually is on this machine, for the journal's draw identity.

    Deliberately **not** :func:`_package_identity`, and the two must not be
    collapsed into one field: they look like duplicates and answer different
    questions for different readers.

    The corpus header's ``packages`` names the *public artifact* — the whole
    point of the name recovery in :func:`_package_identity` — so that a
    reader anywhere can re-download
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` and re-derive the
    draw. A machine path there would say nothing to them, and putting one
    there would undo that recovery.

    The journal's draw identity asks something narrower and entirely local:
    *are the rows already in this journal drawn from the same bytes this run
    is about to read?* The artifact name cannot answer it, because PMC's own
    baseline extraction names a directory by accession range alone —
    independent of subset and of snapshot date, which is the layout this
    repo's packages already use. Two genuinely different packages under
    different parents therefore share a basename; where neither has its
    tarball beside it, :func:`_package_identity` falls back to that shared
    basename for both, the header's ``packages`` matches, and the two draws
    pool in silence. Reproduced in review at exit 0: four articles carrying
    two figures each, pooled with four carrying seven, under one
    ``"packages": ["PMC012xxxxxx"]``.

    Neither field subsumes the other, which is why both are compared. The
    path catches two different directories sharing a basename; the artifact
    name catches a *different snapshot's* tarball swapped in beside a
    directory that has not moved, which no path can see.

    Resolved rather than merely made absolute, so a symlink and its target,
    or two spellings of one directory (``a/../a``), are one location and
    resume normally — resuming the same draw is the journal's entire
    purpose, and a check that refuses it trades one defect for another. The
    converse, a package directory that has *moved* since the journal was
    written, is refused: that costs a re-draw and is the direction to fail
    in, since accepting two different packages is the defect and refusing
    one package twice is an inconvenience.

    Args:
        path: A package directory or tarball, as passed to ``--package``.

    Returns:
        *path*'s resolved absolute path, as a string.
    """
    return str(path.resolve())


def iter_package_articles(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(pmcid, raw_xml)`` for every article in one baseline package.

    A ``.tar.gz`` is streamed member by member and never unpacked; a directory
    is walked with ``rglob``. Members are read **whole** — see
    :func:`article_year`.

    **Both walks reach the same depth on purpose** (issue #165). The tar walk
    has always taken members at any depth, and the directory walk globbed one
    level, so one artifact unpacked and packed yielded different candidate
    sets — and so a different :func:`draw` under the same
    ``(packages, window, target, seed)``. That is the whole reproducibility
    claim: a reader re-deriving a committed corpus downloads the tarball,
    while the corpora were drawn from an unpacked directory. The local mirror
    is flat, so no committed figure moves; the equivalence is pinned by a
    test rather than left resting on that.

    Args:
        path: A package directory, or a baseline ``.tar.gz``.

    Yields:
        The PMC identifier (the member's stem) and its bytes.

    Raises:
        PackageError: If :func:`_is_package_path` is false for *path* — this
            function's first statement, so the dispatch below never runs on
            anything that predicate has not already accepted — or *path* is
            gzip-compressed but not a tarball once opened. Refused rather
            than skipped: a mistyped ``--package`` that silently contributed
            nothing would print a rate over a draw nobody asked for, which
            is what :func:`_validate_args` exists to prevent — and it tests
            the same predicate this function does, so nothing that reaches
            here was not already refused there.
    """
    refusal = _package_path_refusal(path)
    if refusal is not None:
        raise PackageError(refusal)
    if path.is_dir():
        for entry in sorted(path.rglob("*.xml")):
            yield entry.stem, entry.read_bytes()
        return
    try:
        with tarfile.open(path, "r|gz") as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith(".xml"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:  # pragma: no cover - a tarball oddity
                    continue
                yield Path(member.name).stem, handle.read()
    except tarfile.ReadError as exc:
        raise PackageError(f"{path} {_NOT_A_TARBALL}: {exc}") from exc


def package_candidates(paths: list[Path], first: int, last: int) -> list[str]:
    """Every article in *paths* published in ``[first, last]``, sorted, once each.

    **Deduplicated, because two `--package` paths may hold the same article.**
    `--package <dir> --package <its own tarball>` is the layout
    `_package_identity` documents as the ordinary local-mirror shape — an
    extracted directory sitting beside the tarball it came out of — so the
    overlap is a plausible command line rather than a contrived one, and every
    consequence of not deduplicating is silent: each article enters the pool
    twice, so `draw` can select one identifier twice and hand `main` a
    `wanted` set smaller than `--target` with nothing said; it is then
    measured twice and journalled twice, doubling every population; and
    `_comparison_reportable`'s denominator inflates the same way. Exit 0
    throughout, with plausible-looking numbers.
    :func:`read_package_articles` carries the same rule on the other side.

    Args:
        paths: Package directories or tarballs. May overlap.
        first: Earliest publication year to accept, inclusive.
        last: Latest publication year to accept, inclusive.

    **An undated article is reported, never merely skipped.** It is dropped
    from the pool — nothing else is possible, a draw being by year — but a
    drop that says nothing is the shape the `_YEAR_RE` fix was: absent from
    the candidate pool, never counted as unmeasured, exit 0, and 14 of the 17
    it cost were one contiguous journal block, so publisher-clustered rather
    than noise. The population measures **0 of 220,485** across both baseline
    packages at this revision, so this is a net for the next cause rather
    than a live one; that is exactly when it has to be built, since the
    symptom of the last one was that there was no symptom.

    Args:
        paths: Package directories or tarballs. May overlap.
        first: Earliest publication year to accept, inclusive.
        last: Latest publication year to accept, inclusive.

    Returns:
        The identifiers, sorted and distinct — the order a draw is taken
        against, so it must not depend on a directory's glob order.
    """
    found: set[str] = set()
    undated: set[str] = set()
    for path in paths:
        for pmcid, xml in iter_package_articles(path):
            year = article_year(xml)
            if year is None:
                undated.add(pmcid)
            elif first <= year <= last:
                found.add(pmcid)
    if undated:
        shown = ", ".join(sorted(undated)[:5])
        sys.stderr.write(
            f"{len(undated)} article(s) declare no readable <pub-date> year and are "
            f"undrawable, so they are in no window's pool: {shown}"
            f"{', ...' if len(undated) > 5 else ''}\n"
        )
    return sorted(found)


def draw(candidates: list[str], target: int, seed: int) -> list[str]:
    """*target* identifiers from *candidates*, reproducibly.

    Sorted before sampling, because ``random.sample`` is a function of the
    sequence's order as well as of the seed: an unpacked directory's glob
    order is not stable across machines, so an unsorted draw would reproduce
    only where it was made — which is the property this whole change exists
    to give the corpora.

    Args:
        candidates: The identifiers to draw from.
        target: How many to take; taking them all is fine.
        seed: The recorded seed.

    Returns:
        The drawn identifiers, sorted.
    """
    pool = sorted(candidates)
    if target >= len(pool):
        return pool
    return sorted(random.Random(seed).sample(pool, target))


def read_package_articles(paths: list[Path], wanted: set[str]) -> Iterator[tuple[str, bytes]]:
    """Yield ``(pmcid, raw_xml)`` for the drawn articles, in package order.

    A second pass over the packages, rather than holding the first pass's
    bytes: the recent window has 97,668 in-window candidates, which is too
    many whole articles to hold in memory between passes. For a tarball the
    pass costs one more sequential decompression (16.5 s for `PMC002xxxxxx`).

    **Each identifier is yielded once, however many packages hold it.** The
    other half of :func:`package_candidates`' deduplication rule, and the half
    that costs a doubled measurement rather than a doubled pool: an article
    present in both an extracted directory and the tarball beside it would
    otherwise be measured and journalled twice, and `Totals` adds every row it
    is handed. First package wins, which is arbitrary and does not matter —
    the members are the same bytes.

    Args:
        paths: The same packages the candidates came from. May overlap.
        wanted: The drawn identifiers.

    Yields:
        Each wanted article's identifier and bytes, once per identifier.
    """
    yielded: set[str] = set()
    for path in paths:
        for pmcid, xml in iter_package_articles(path):
            if pmcid in wanted and pmcid not in yielded:
                yielded.add(pmcid)
                yield pmcid, xml


def _hold_for_comparison(
    paths: list[Path], drawn: list[str], n: int, seed: int
) -> list[tuple[str, bytes]]:
    """A seeded sample of *n* of the corpus's own drawn articles, read back.

    Held from *drawn* — the corpus draw's full result — never from whatever a
    resumed run still has left to measure. A journal that already carries
    every drawn article is the ordinary case for a second invocation adding
    ``--compare-europepmc``, and that must not decide this: reading from the
    unmeasured remainder would silently hold nothing, and a comparison run
    over nothing writes a result — "0 compared, 0 differing" — that is
    indistinguishable from a genuine null finding.

    Sampled with :func:`draw`, the same seeded mechanism the corpus itself
    is drawn with, rather than by taking the first *n* pairs
    :func:`read_package_articles` happens to yield. That generator walks in
    package order, so "first n" is a contiguous accession block, and the
    rendition gap is a per-publisher deposit property — publishers cluster in
    accession ranges, which is the same reason the corpus draw is stratified
    by month in the first place rather than taken as one contiguous walk.

    Args:
        paths: The same packages the corpus was drawn from.
        drawn: The corpus's own full drawn set of identifiers, before any
            journal/``seen`` filtering.
        n: How many to hold; ``0`` holds none and reads nothing back.
        seed: The corpus draw's own seed, reused so this sample is
            reproducible from the same recorded header.

    Returns:
        ``(pmcid, xml)`` pairs for the sampled identifiers, selected by
        membership rather than by the position they arrive in.
    """
    if not n:
        return []
    ids = set(draw(drawn, n, seed))
    return list(read_package_articles(paths, ids))


# The sentinel key on a journal's own first line, naming which (source,
# rendition) it was drawn as. A rendition-qualified filename narrows most
# collisions but cannot rule every one out: `Path.with_suffix()` is not
# injective over `(output, rendition)` — `Path("jats_exhibits.json")
# .with_suffix(".europepmc.journal.jsonl")` equals
# `Path("jats_exhibits.europepmc.json").with_suffix(".journal.jsonl")` — and
# the live source's own default journal name collides outright with a
# `--package` archive draw's default one, since neither is rendition-
# qualified. No filename scheme can carry this property against an
# arbitrary `-o`, so the data has to: this key can never collide with an
# `ArticleMeasurement` field name (none of that dataclass's fields start
# with `__`), which is what tells a header line apart from a row.
_JOURNAL_HEADER_KEY = "__journal_header__"


def _read_journal_text(journal: Path) -> str | None:
    """*journal*'s raw text, or ``None`` if its bytes cannot be decoded as UTF-8.

    A journal is written by this script alone, in UTF-8, one JSON object per
    line, so a decode failure (review round 3: reproduced with a journal
    truncated mid multibyte character) means the file is corrupt rather
    than a shape this module ever produces itself — the same "cannot trust
    it, cannot crash on it" territory as a header-less legacy journal, and
    handled the same way by both of this module's callers of this function
    (:func:`_journal_disagreement` refuses; :func:`_ensure_journal_header`
    leaves the file untouched rather than guessing it is safe to overwrite).

    Returns:
        The decoded text, or ``None`` if it will not decode.
    """
    try:
        return journal.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _journal_header_line(source: str, rendition: str, draw: dict[str, Any]) -> str:
    """The one line that must open every journal this script writes.

    Args:
        source: This run's own ``"package"`` or ``"europepmc"``.
        rendition: This run's own ``"archive"`` or ``"europepmc"``.
        draw: This run's own draw identity — see :func:`_journal_disagreement`.

    Returns:
        A single JSON line, newline-terminated, naming all three.
    """
    header = {
        _JOURNAL_HEADER_KEY: True,
        "source": source,
        "rendition": rendition,
        "draw": draw,
    }
    return json.dumps(header) + "\n"


def _journal_disagreement(
    journal: Path, source: str, rendition: str, draw: dict[str, Any]
) -> str | None:
    """Why *journal* must not be resumed from this run, or ``None`` if it may be.

    Two reproduced sequences (review round 2) are why the filename alone —
    round 1's fix — is not enough: an `-o` collision (`with_suffix` is not
    injective, see :data:`_JOURNAL_HEADER_KEY`'s comment) and the live
    source sharing its default journal name with a `--package` archive draw
    at the default output, since the live branch is never rendition-
    qualified. Both let a resumed run read another run's rows as `seen` and
    stamp them with this run's own `(source, rendition)` — silently, at
    exit 0, zero fetches issued. The header line is what closes this for
    good: it is data travelling with the journal's own rows rather than a
    fact inferred from a filename a caller chose.

    *A rendition-agreeing journal can still pool the wrong draw* (review
    round 3, reproduced live): two archive runs at one `-o` over different
    packages and year windows both exited 0 and produced a corpus stamped
    with the *second* run's `packages`/`first_year` over a mix of both
    runs' rows; a different `--seed` at one `-o` pooled the same way. Same
    `(source, rendition)`, different draw. `draw` is therefore part of what
    is checked here too — for a `--package` run, its packages, its year
    window and its seed; for the live source, its month flags **and the
    boundaries those flags resolved to** — everything that decides *which*
    identifiers this run is drawing, deliberately excluding `target` (a
    resumed run growing its target is the ordinary top-up workflow this
    whole journal mechanism exists for, not a disagreement).

    *The live source's flags do not identify its draw; its resolved dates
    do.* `--months`/`--months-ago` are counted back from `date.today()`, so
    the identical command names a different window each month. Carrying only
    the flags, a journal written on the last day of a month resumes cleanly
    the next day under a window shifted by a whole month, and the corpus is
    stamped with the second run's `first`/`last` over a mix of both runs'
    rows — the package branch's round-3 pooling, one axis over and reachable
    without changing a single argument.

    *A package is identified twice over, on purpose* (review round 4,
    reproduced at exit 0). `draw["packages"]` carries the public artifact
    name (:func:`_package_identity`) and `draw["package_paths"]` the
    resolved location it was read from (:func:`_package_location`). They
    look like one field written twice and are not: PMC's baseline
    extraction names a directory by accession range alone, so two
    genuinely different packages under different parents share a basename
    and — with no tarball beside either to recover the rest of the name
    from — share an artifact name too, which let four articles carrying
    two figures pool with four carrying seven under one
    `"packages": ["PMC012xxxxxx"]`. The path closes that; the name closes
    what a path cannot see, a different snapshot's tarball swapped in
    beside a directory that has not moved. See :func:`_package_location`.

    Args:
        journal: The path this run is about to read from and append to.
        source: This run's own ``"package"`` or ``"europepmc"``.
        rendition: This run's own ``"archive"`` or ``"europepmc"``.
        draw: This run's own draw identity, compared for equality against
            the journal's own recorded ``draw``.

    Returns:
        A reason naming the journal and what disagrees, if its header does
        not match this run; if it has content but no header at all (a
        journal written before this check existed, or a journal this
        script did not write); or if its bytes cannot be decoded as UTF-8
        at all (:func:`_read_journal_text`) — refused rather than trusted
        blind or silently ignored in every case. ``None`` if the journal
        does not exist, is empty (or whitespace-only — the same test
        :func:`_ensure_journal_header` uses, so the two agree on what
        "empty" means), or its header agrees on every count.
    """
    if not journal.exists():
        return None
    text = _read_journal_text(journal)
    if text is None:
        return (
            f"{journal} cannot be read as UTF-8 (corrupt or truncated, e.g. mid a "
            "multibyte character); refusing to resume from it. Delete it (or move it "
            "aside) to start a fresh draw."
        )
    if not text.strip():
        return None
    try:
        header = json.loads(text.splitlines()[0])
    except json.JSONDecodeError:
        header = None
    if not isinstance(header, dict) or not header.get(_JOURNAL_HEADER_KEY):
        return (
            f"{journal} has content but no rendition header (it was written before "
            "this check existed, or by something other than this script); refusing "
            "to resume from it rather than guess what it was drawn as. Delete it (or "
            "move it aside) to start a fresh draw."
        )
    mismatches = [
        f"{name} {header.get(name)!r} != {value!r}"
        for name, value in (("source", source), ("rendition", rendition), ("draw", draw))
        if header.get(name) != value
    ]
    if mismatches:
        return (
            f"{journal} disagrees with this run ({'; '.join(mismatches)}); refusing "
            "to resume a journal that disagrees with this run. Pick a different -o, "
            "or delete/rename the journal to start fresh."
        )
    return None


def _ensure_journal_header(
    journal: Path, source: str, rendition: str, draw: dict[str, Any]
) -> None:
    """Create *journal*, with its header line, if it does not already carry one.

    Call only after :func:`_journal_disagreement` has cleared the run to
    proceed — so *journal* is either absent, empty, or already carries a
    header that agrees with *source*/*rendition*/*draw*. An existing-but-
    empty file (e.g. a prior run that crashed between creating it and
    writing a row) is overwritten with just the header line rather than
    appended to, since there is nothing in it worth preserving — "empty"
    tested the same way :func:`_journal_disagreement` tests it
    (whitespace-only, via :func:`_read_journal_text`), so the two cannot
    disagree about the same file (review round 3). A file that exists and
    is *not* empty but cannot be decoded is left untouched rather than
    overwritten: :func:`_journal_disagreement` would already have refused
    the run over it, so reaching this function with such a file at all
    means it was called directly, and destroying unreadable content on a
    guess is worse than doing nothing.

    Args:
        journal: Where the journal lives.
        source: This run's own ``"package"`` or ``"europepmc"``.
        rendition: This run's own ``"archive"`` or ``"europepmc"``.
        draw: This run's own draw identity — see :func:`_journal_disagreement`.
    """
    journal.parent.mkdir(parents=True, exist_ok=True)
    if not journal.exists():
        journal.write_text(_journal_header_line(source, rendition, draw), encoding="utf-8")
        return
    text = _read_journal_text(journal)
    if text is not None and not text.strip():
        journal.write_text(_journal_header_line(source, rendition, draw), encoding="utf-8")


def _measure_and_journal(
    handle: IO[str], totals: Totals, articles: Iterable[tuple[str, bytes | None]]
) -> None:
    """Measure every article *articles* yields and append the rows to the journal.

    The one place either of ``main``'s package-branch sources — the archive's
    own bytes, or a live Europe PMC fetch — turns into a measured row, so
    there is exactly one call site to get the "no fallback" rule right rather
    than two copies that could drift. *articles* carries the source's whole
    decision already made: this function has no bytes of its own to fall back
    to, which is what makes mixing renditions inside one corpus structurally
    impossible rather than merely a branch that happens to be correct.

    Args:
        handle: The open journal file, in append mode.
        totals: Accumulates every measured row, the unmeasured count and why
            each unmeasured article was unmeasured.
        articles: ``(pmcid, xml)`` pairs. ``xml`` is ``None`` for a failed
            live fetch — the archive package's own read never produces
            ``None``, only bytes, empty or otherwise, which
            :func:`measure_article` already turns into an unmeasured row on
            its own via ``ET.ParseError`` — and that article is counted
            unmeasured with nothing substituted for it. That asymmetry is
            what the two causes are keyed on: ``None`` is
            ``europepmc_unavailable``, everything else that fails is
            ``unparseable``.
    """
    for pmcid, xml in articles:
        # `is None`, not falsiness: only a failed live fetch is ``None``,
        # while an archive member that is present and *empty* is ``b""`` —
        # falsy, but a document that would not parse rather than one that
        # could not be retrieved, and the two are exactly the distinction the
        # causes exist to record. `measure_article` turns the empty case into
        # ``None`` itself, through `ET.ParseError`.
        if xml is None:
            totals.count_unmeasured("europepmc_unavailable")
            continue
        row = measure_article(pmcid, xml)
        if row is None:
            totals.count_unmeasured("unparseable")
            continue
        totals.add(row)
        handle.write(json.dumps(row.to_dict()) + "\n")
        handle.flush()


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
    contribs: int = 0
    contrib_name_spellings: Counter[str] = field(default_factory=Counter)
    nested_contribs: int = 0
    collabs_with_a_roster: int = 0
    articles_losing_every_author: int = 0
    nested_article_regions: int = 0
    # What the pre-#138 whole-document walk would have said, for the fields
    # where that differs — and *only* those, so an article carrying no nested
    # article contributes an empty mapping rather than a second copy of itself.
    # Recording it is what makes the redraw a measurement of the correction
    # rather than a silent application of it: #158's four disagreeing rates are
    # exactly the question "how much does the region inflate a count?".
    unscoped: dict[str, Any] = field(default_factory=dict)
    # Issue #142 — what a `<collab>` carries besides text: `<institution>` and
    # `<addr-line>` are legal children that run together with no separator
    # when the parser accumulates them. A member roster (`<contrib-group>`,
    # #120's shape, counted separately below) is deliberately NOT counted
    # here — it does not exhibit #142's defect at all, since the parser's
    # `_UNDIVIDED_NAME_ELEMENTS` guard refuses the merge while a `<contrib>`
    # is open, so folding it in would make this counter's headline number
    # #142's population plus an unrelated one.
    collab_children: Counter[str] = field(default_factory=Counter)
    collabs_with_element_children: int = 0
    # Issue #143 — multiplicity, which section 11 cannot see: it counts
    # spellings per *article*, so one `<contrib>` carrying two `<collab>` is
    # invisible there. #117 is the precedent that "how many does one element
    # deposit?" decides between first-wins, last-wins and ranking.
    # `contribs_multi_collab`/`contribs_multi_string_name` are counted through
    # `_CONTRIB_NAME_WRAPPERS` (a `<collab-alternatives>` or
    # `<name-alternatives>` holds its members one level deeper than `<contrib>`
    # itself, and a direct-child-only count reads that shape as zero either
    # way it could occur). `name_alternatives`/`collab_alternatives` are a
    # separate signal — how often the wrapper *itself* is used — and each is a
    # per-contrib flag (0 or 1), not a raw element count, so it stays a valid
    # binomial numerator against `contribs` the way the two multiplicity
    # counters already are.
    contribs_multi_collab: int = 0
    contribs_multi_string_name: int = 0
    name_alternatives: int = 0
    collab_alternatives: int = 0
    # Issue #147 — `<tex-math>` is taken from the prose containing it and
    # `<disp-formula>` dropped outright. `formula_alternatives_both` is the
    # count that rules out "add them to `_INLINE_ELEMENTS`": an `<alternatives>`
    # holding both encodings of one formula would emit it twice.
    # `disp_formulas_image_only` is the residual no fix for the text-taking
    # defect can recover: a formula deposited as nothing but a `<graphic>` was
    # never text to begin with.
    disp_formulas: int = 0
    inline_formulas: int = 0
    tex_math: int = 0
    mml_math: int = 0
    formula_alternatives_both: int = 0
    disp_formulas_with_label: int = 0
    disp_formulas_image_only: int = 0
    # Issue #150 — a `<ref>` whose only content is a `<note>` renders as an
    # empty `<li>`. The vocabulary is open, so a `<ref>` child nobody has
    # listed prints as itself rather than as evidence of nothing.
    refs: int = 0
    refs_note_only: int = 0
    ref_child_kinds: Counter[str] = field(default_factory=Counter)

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
        than left at its zero default. These arrived in five generations —
        ``_TABLE_SIDE_COUNTERS`` with issue #135, ``_OWNER_SIDE_COUNTERS`` with
        #123/#125/#130, ``_CONTRIB_SIDE_COUNTERS`` with #120/#140,
        ``_SCOPE_SIDE_COUNTERS`` with #138, and ``_WAITING_SIDE_COUNTERS`` with
        #142/#143/#147/#150 — so a corpus or journal older than
        a generation carries none of it, and
        each would otherwise sum to zero, which is exactly what a genuine "no
        table deposits an image", "no caption nests" or "no contributor is
        named undivided" draw looks like; reading one as the other is the
        mistake #127 spent two windows disproving.
        :meth:`Totals.measured` is what tests it.
        """
        row = cls(pmcid=str(data["pmcid"]))
        for name in (
            *_TABLE_SIDE_COUNTERS,
            *_OWNER_SIDE_COUNTERS,
            *_CONTRIB_SIDE_COUNTERS,
            *_SCOPE_SIDE_COUNTERS,
            *_WAITING_SIDE_COUNTERS,
        ):
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


def _measure_tree(pmcid: str, root: ET.Element, *, scoped: bool) -> ArticleMeasurement:
    """Walk one parsed article and record every population it contributes to.

    Args:
        pmcid: The article's PMC identifier, used only to label the row.
        root: The parsed document.
        scoped: Whether to stop at a nested-article region, the way the parser
            does. ``False`` reproduces the pre-#138 whole-document walk, which
            is what the ``unscoped`` diff is taken against.

    Returns:
        The measurement.
    """
    row = ArticleMeasurement(pmcid=pmcid)
    transparent = set(_TRANSPARENT_WRAPPERS)

    def walk(el: ET.Element, exhibit: str | None, chain: list[str], exhibit_depth: int) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag in _NESTED_ARTICLE_ELEMENTS:
                # Counted either way — the count is #158's population — but
                # descended into only when reproducing the old walk. Scoped,
                # a region nested inside another is never reached, so the
                # scoped count is of top-level regions and the unscoped one
                # of all of them; the difference is a count of how many are
                # nested, not a rate.
                row.nested_article_regions += 1
                if scoped:
                    continue
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
            elif tag == "contrib":
                _record_contrib(child, chain, row)
            elif tag == "disp-formula":
                row.disp_formulas += 1
                if any(_local(c.tag) == "label" for c in child):
                    row.disp_formulas_with_label += 1
                content = [_local(c.tag) for c in child if _local(c.tag) != "label"]
                if content and set(content) == {"graphic"}:
                    # No fix for #147's text-taking defect can recover this
                    # one: a formula deposited as nothing but an image was
                    # never text to begin with.
                    row.disp_formulas_image_only += 1
                _record_formula_alternatives(child, row)
            elif tag == "inline-formula":
                row.inline_formulas += 1
                _record_formula_alternatives(child, row)
            elif tag == "tex-math":
                row.tex_math += 1
            elif tag == "math":
                row.mml_math += 1
            elif tag == "ref":
                _record_ref(child, row)
            elif tag == "title" and exhibit is None and "sec" in chain and chain[-1] != "sec":
                # The population issues #125 and #130 are about, and only it:
                # a <title> that a section was open for, owned by something
                # else. `exhibit is None` because the parser already dropped
                # a <title> inside a <fig> or <table-wrap> before this fix, so
                # counting those would report a change that was not made.
                row.section_renaming_titles[chain[-1]] += 1
            walk(child, exhibit, chain + [tag], exhibit_depth)

    walk(root, None, [], 0)
    # A per-article question, so it is answered once the walk is over: an
    # article naming no contributor with `<name>` loses *every* author to the
    # two undivided spellings, which is the difference between #140 and #120 —
    # 100% of an article's authors against 34 of 1,025 articles losing at least
    # one contributor. That draw counted `<contrib>` elements carrying no
    # `<surname>`, a set both spellings share, so it is a rate for neither
    # alone. Asked of the spellings actually deposited rather than of bmlib's
    # output, so it stays answerable after the fix that makes the loss stop.
    undivided = row.contrib_name_spellings["string-name"] + row.contrib_name_spellings["collab"]
    if undivided and not row.contrib_name_spellings["name"]:
        row.articles_losing_every_author = 1
    return row


def measure_article(pmcid: str, xml: bytes) -> ArticleMeasurement | None:
    """Walk one article's JATS and record every population it contributes to.

    The walk is scoped to what ``jats_parser`` routes: it stops at a
    ``<sub-article>`` or ``<response>``, in which the parser fires no handler
    (#110). It is then run a second time *unscoped*, and the fields that
    differ are recorded on the row — so the corpus says how much the old
    whole-document walk overstated each population, which is the measurement
    issue #158's four disagreeing rates are asking for.

    Args:
        pmcid: The article's PMC identifier, used only to label the row.
        xml: The raw JATS body.

    Returns:
        The measurement, or ``None`` if the document would not parse **or is
        not a JATS ``<article>``** — either way the article is *unmeasured*
        rather than empty.

    The root-element test is the envelope check this module's own doctrine
    asks for, and it is not theoretical (issue #166): a proxy, CDN or captive
    portal serving an HTML error page at HTTP 200 produces perfectly
    well-formed XML, so ``ET.fromstring`` succeeds and every counter reads
    zero. That row is then ``totals.add()``-ed, journalled, and enters every
    denominator as a measured article — and on ``--compare-europepmc`` an
    outage is counted as a *rendition disagreement*, which is the population
    ``tests/data/jats_exhibits.rendition.json`` is committed as evidence for.
    The corpora legitimately contain all-zero rows, so nothing downstream can
    tell the two apart after the fact; this is the only place that can.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    if _local(root.tag) != "article":
        return None

    row = _measure_tree(pmcid, root, scoped=True)
    if not row.nested_article_regions:
        # No region, so nothing can differ — true only because the walk's
        # only two skips that stop short of full descent are a <graphic> and
        # an exhibit's <label>, and JATS admits neither as a container for
        # <sub-article>/<response>, so no region can be hiding where this
        # count would miss it. A later `continue` added for some other
        # container element would need re-checking against that assumption.
        # Skipping the second walk here is not only an optimisation: it keeps
        # `unscoped` empty for articles that carry no region at all, which is
        # what stops the corpus doubling in size.
        return row
    shadow = _measure_tree(pmcid, root, scoped=False)
    row.unscoped = _row_difference(row, shadow)
    return row


def _row_difference(scoped: ArticleMeasurement, shadow: ArticleMeasurement) -> dict[str, Any]:
    """The fields where the unscoped walk disagrees, and only those.

    Args:
        scoped: The row as the parser would see the document.
        shadow: The row the pre-#138 whole-document walk produces.

    Returns:
        A mapping from field name to the unscoped value, ``Counter`` fields
        rendered as plain dicts so the row serialises without special cases.
    """
    out: dict[str, Any] = {}
    for name, value in shadow.__dict__.items():
        if name in ("pmcid", "unscoped"):
            continue
        if value != getattr(scoped, name):
            out[name] = dict(value) if isinstance(value, Counter) else value
    return out


def _flattened_naming_children(el: ET.Element) -> Counter[str]:
    """*el*'s naming children, with ``_CONTRIB_NAME_WRAPPERS`` flattened away.

    ``descend`` (inside :func:`_record_contrib`) already treats a
    ``<name-alternatives>`` or ``<collab-alternatives>`` as transparent when
    collecting *which* spellings a ``<contrib>`` carries. This does the same
    when counting *how many* of one spelling it carries — issue #143's
    multiplicity question — because counting only *el*'s direct children (as
    this counter's first cut did) reads a ``<collab-alternatives>`` holding
    two ``<collab>`` — one collaboration deposited in two scripts, the
    canonical multi-deposit shape #143 is about — as zero of either, the
    wrapped pair sitting one level below where a direct-child count looks.

    Args:
        el: The ``<contrib>`` element.

    Returns:
        A counter of local tag names, with any wrapper's own contents
        substituted for the wrapper itself (recursively, though JATS does not
        nest these wrappers in practice).
    """
    counts: Counter[str] = Counter()
    for child in el:
        name = _local(child.tag)
        if name in _CONTRIB_NAME_WRAPPERS:
            counts.update(_flattened_naming_children(child))
        else:
            counts[name] += 1
    return counts


def _record_contrib(el: ET.Element, chain: list[str], row: ArticleMeasurement) -> None:
    """Count one ``<contrib>``: how it names its contributor, and how it nests.

    **Scoped to what the parser routes**, not to the subtree. A ``<collab>``
    may carry a ``<contrib-group>`` of the collaboration's own members, so a
    ``<contrib>`` nests inside another; a whole-subtree walk would credit each
    member's ``<name>`` to the consortium and report the article as naming
    every contributor the structured way. The descent therefore stops at a
    nested ``<contrib>``, which is where the parser's own frame stack hands
    routing to the inner contributor.

    Args:
        el: The ``<contrib>`` element.
        chain: The local names between the article root and *el*, outermost
            first, so ``"contrib" in chain`` answers the nesting question.
        row: The measurement to count into.
    """
    row.contribs += 1
    if "contrib" in chain:
        # Issue #120's roster case, and the reason the parser holds a stack of
        # frames rather than one builder.
        row.nested_contribs += 1

    found = 0

    def descend(node: ET.Element) -> None:
        nonlocal found
        for child in node:
            name = _local(child.tag)
            if name == "contrib":
                # A nested contributor is its own row; the outer walk reaches
                # it separately, so descending would credit a roster member's
                # name to the consortium enclosing it.
                continue
            if name in _CONTRIB_NAME_WRAPPERS:
                descend(child)
                continue
            if name in _CONTRIB_NON_NAME_CHILDREN:
                continue
            # Counted by whatever it is called, not by membership of a list
            # this script wrote: a spelling nobody has thought of has to print
            # as itself, or it falls into "(none)" and is reported as a
            # contributor naming nobody — #121's mis-certification, inside the
            # instrument built to detect the next one. Not descended into: the
            # parts of a name are not spellings of it.
            row.contrib_name_spellings[name] += 1
            if name in _CONTRIB_NAMING_ELEMENTS:
                found += 1
            if name == "collab":
                # Issue #142 — what the `<collab>` carries besides its own
                # text: `<institution>` and `<addr-line>` are legal children,
                # counted only when at least one is present (bare text, as in
                # `<collab>The Y Group</collab>`, contributes nothing — the
                # distinction the fix turns on). `<contrib-group>` is
                # excluded here: a member roster is #120's shape, counted on
                # the next line, and does not exhibit #142's defect at all —
                # the parser's `_UNDIVIDED_NAME_ELEMENTS` guard already
                # refuses the merge while a `<contrib>` is open, so folding a
                # roster into this counter would report #142's population
                # plus an unrelated one.
                children = [_local(c.tag) for c in child if _local(c.tag) != "contrib-group"]
                if children:
                    row.collab_children.update(children)
                    row.collabs_with_element_children += 1
                if any(_local(c.tag) == "contrib-group" for c in child):
                    row.collabs_with_a_roster += 1

    descend(el)
    # Issue #143 — several `<collab>` or `<string-name>`, or a
    # `<name-alternatives>`/`<collab-alternatives>`, deposited on one
    # `<contrib>`. The multiplicity counters are taken through
    # `_CONTRIB_NAME_WRAPPERS` rather than from *el*'s direct children alone:
    # a `<collab-alternatives>` (one collaboration deposited in two scripts —
    # the parser has no handling for it at all, so it is last-wins the same
    # way two bare sibling `<collab>` are) holds its members one level deeper
    # than `<contrib>` itself, and a direct-child-only count reads that shape
    # as zero. `<name-alternatives>` wrapping two `<string-name>` is the same
    # shape, one rung less severe (it was at least flagged, never silent,
    # by `name_alternatives` below).
    flattened = _flattened_naming_children(el)
    if flattened["collab"] > 1:
        row.contribs_multi_collab += 1
    if flattened["string-name"] > 1:
        row.contribs_multi_string_name += 1
    # `name_alternatives`/`collab_alternatives` count *contribs* carrying the
    # wrapper (0 or 1 each), not raw element occurrences: JATS's `(...)*`
    # content model does not forbid a `<contrib>` from carrying more than one
    # of the same wrapper, and summing raw occurrences would let such a row
    # contribute more than 1 to a count that `_pct` then treats as a binomial
    # numerator over `contribs`.
    direct = Counter(_local(child.tag) for child in el)
    if direct["name-alternatives"]:
        row.name_alternatives += 1
    if direct["collab-alternatives"]:
        row.collab_alternatives += 1
    if not found:
        # Its own vocabulary entry rather than a silence: a `<contrib>` naming
        # nobody is what the parser reports at DEBUG and drops, so a draw in
        # which that is common is worth seeing.
        row.contrib_name_spellings["(none)"] += 1


def _record_formula_alternatives(el: ET.Element, row: ArticleMeasurement) -> None:
    """Count a formula whose ``<alternatives>`` holds two encodings of itself.

    This is the count that decides #147's shape: where one formula is
    deposited as both LaTeX and MathML, merging every accumulating child would
    emit it twice, so the fix cannot be one more ``_INLINE_ELEMENTS`` member.

    Args:
        el: The ``<disp-formula>`` or ``<inline-formula>``.
        row: The measurement to count into.
    """
    for child in el:
        if _local(child.tag) != "alternatives":
            continue
        kinds = {_local(g.tag) for g in child}
        if "tex-math" in kinds and "math" in kinds:
            row.formula_alternatives_both += 1


def _record_ref(el: ET.Element, row: ArticleMeasurement) -> None:
    """Count one ``<ref>`` and the kinds of child it carries.

    A ``<ref>`` whose only content is a ``<note>`` — ``<label>`` aside, which
    is the publisher's own number and not content — carries no citation for
    ``_format_ref_html`` to render, so it becomes an empty ``<li>`` (#150).
    The child vocabulary is open and this docstring does not try to enumerate
    it exactly: JATS's ``<ref>`` admits ``<mixed-citation>``,
    ``<element-citation>``, ``<note>``, ``<p>``, ``<x>`` and
    ``<citation-alternatives>`` (several encodings of *one* reference — #149's
    own population, measured at 0 of 216 in the local corpus), plus the
    pre-JATS-NLM legacy ``<citation>`` some archives still carry. The point of
    counting by name rather than against a list is exactly that this
    enumeration need not be complete: a spelling missing from it still prints
    under its own name rather than being reported as absent.

    Args:
        el: The ``<ref>`` element.
        row: The measurement to count into.
    """
    row.refs += 1
    kinds = [_local(child.tag) for child in el]
    row.ref_child_kinds.update(kinds)
    content = [k for k in kinds if k != "label"]
    if content and set(content) == {"note"}:
        row.refs_note_only += 1


def _owned(el: ET.Element, wanted: str) -> list[ET.Element]:
    """The *wanted* descendants that *el* itself owns.

    The same judgement :func:`_record_graphic` makes, applied downwards: a
    descendant belongs to *el* only if every element between them is a
    transparent wrapper. A whole-subtree ``el.iter()`` counts a ``<td>``'s
    inline image and a nested exhibit's deposit as the outer exhibit's, which
    is not what the parser routes — issue #135's stated residual, and not a
    theoretical one: unscoped, four of ten recent-window tables read as
    carrying several deposits, every one a ``<td>`` cell image from two
    articles. That measurement is from the pre-#138 draw and is not
    re-derivable from either committed corpus, which record
    ``tables_with_graphic`` 92 and ``tables_multi_graphic`` 0 — scoped, and
    with no unscoped table counter to compare against.

    The **figure** counters deliberately keep the subtree walk: their
    percentages are cited at ``offer_graphic`` and in CLAUDE.md, and
    re-scoping them silently would invalidate every one.

    **The argument that this costs nothing is refused rather than restated**
    (issue #164). It used to read "both committed draws record zero nested
    exhibits and every foreign owner is a ``<td>``, which can only sit under a
    ``<table-wrap>``" — and the redraw refutes both halves: the recent corpus
    holds 7 nested ``<fig>`` (all ``PMC12143881``) and three foreign owners,
    ``<td>`` 82, ``<inline-formula>`` 69, ``<disp-formula>`` 2. An
    ``<inline-formula>`` is not confined to a ``<table-wrap>``. What the
    unscoped walk costs on the **served** rendition the percentages are of is
    unmeasured; a spot check over the same articles' *archive* bytes moves the
    multi-graphic figure count 77 → 58, which is large enough to matter and
    the wrong rendition to cite. Scoping the figure side means re-measuring
    #117 — that is #164, not a docstring edit.

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


def rendition_delta(archive: ArticleMeasurement, served: ArticleMeasurement) -> dict[str, Any]:
    """The fields where two renditions of one article disagree.

    Args:
        archive: The row measured from the baseline package's bytes.
        served: The row measured from Europe PMC's ``fullTextXML``.

    Returns:
        A mapping from field name to both values, empty where they agree.
        ``unscoped`` is skipped: it is a *within-rendition* property (the
        scoped-vs-unscoped walk of one document), while this function reports
        *between-rendition* facts, so it is out of scope here rather than
        redundant with what it reports — two renditions can each carry a
        nested-article region, agree on every named field including
        ``nested_article_regions`` itself, and still disagree on ``unscoped``
        (archive's region holds three figures, served's holds none), which
        this function then reports nowhere at all. Closing that gap is a
        separate change; this skip is only ever a scope decision, not a claim
        that nothing is lost by it.
    """
    out: dict[str, Any] = {}
    for name, value in archive.__dict__.items():
        if name in ("pmcid", "unscoped"):
            continue
        other = getattr(served, name)
        if value != other:
            out[name] = {
                "archive": dict(value) if isinstance(value, Counter) else value,
                "europepmc": dict(other) if isinstance(other, Counter) else other,
            }
    return out


def compare_renditions(
    client: httpx.Client, pace: Any, articles: list[tuple[str, bytes]]
) -> dict[str, Any]:
    """Measure each article in both renditions and report where they disagree.

    **This function's own answer is why the citable corpora are drawn from
    the *served* rendition.** It was written while they were drawn from the
    archive one, to test whether citing an archive figure for a parser fed by
    Europe PMC was safe. It found the overwhelming majority of compared
    articles differing, on exactly the populations this repo cites, and both
    corpora were redrawn on ``fullTextXML`` in response — the counts are in
    ``tests/data/jats_exhibits.rendition.json``, which this writes, rather
    than restated here where they would drift from it. The archive-side
    premise below is the question this asked, not a description of what the
    corpora are.

    The *sample* stays package-defined either way — a reader re-derives the
    identifier list from ``(packages, window, target, seed)`` — and only the
    bytes measured moved.

    ``FullTextService`` feeds the parser Europe PMC's ``fullTextXML``. #119
    found the two differ in a
    construct the scan's lexer reads: Springer's commented-out
    ``<authorqueries>`` block is in the archive copy of three articles and
    absent from Europe PMC's copy of the same three. (CLAUDE.md's own
    measurement is the other half of that fact — the lexer's comment token
    has *no* measured population on Europe PMC's own ``fullTextXML``, 0 of an
    880-article draw against 25.6% of the archive — so "differs" is
    established; "reaches a scan on this module's live input" is not, and
    this function is what would measure that rather than assume it.) So
    citing an archive figure for a parser fed by Europe PMC is a claim, and
    this is what tests it.

    Args:
        client: An ``httpx.Client``.
        pace: The per-host pacer.
        articles: ``(pmcid, archive_bytes)`` pairs drawn from the corpus —
            see :func:`_hold_for_comparison` for how these are selected.

    Returns:
        The comparison: how many were compared, how many could not be
        (unmeasured, entering no denominator, split by which side was
        unreadable), how many differ at all, which fields differ and how
        often, and the per-article deltas.
    """
    compared = unmeasured = 0
    unmeasured_causes: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    deltas: dict[str, dict[str, Any]] = {}
    for pmcid, archive_xml in articles:
        # The archive side costs no request — its bytes are already in hand
        # — so it is checked first: an archive article that will not parse is
        # a corpus property `main` already counts elsewhere, not a fact about
        # what Europe PMC will serve, and this order spends no paced request
        # finding that out.
        archive = measure_article(pmcid, archive_xml)
        if archive is None:
            unmeasured += 1
            unmeasured_causes["archive_unparseable"] += 1
            continue
        served_xml = _fetch(client, f"{EUROPE_PMC}/{pmcid}/fullTextXML", pace)
        # `is None`, not falsiness, and the two causes are kept apart — the
        # rule `_measure_and_journal` states at length and this function used
        # to collapse (issue #167). A body that arrives whole and will not
        # parse is permanent; a fetch that failed is transient, and a re-run
        # recovers only the second. Reporting the first as the second tells
        # the reader to re-run for an article no re-run will ever recover.
        if served_xml is None:
            unmeasured += 1
            unmeasured_causes["europepmc_unavailable"] += 1
            continue
        served = measure_article(pmcid, served_xml)
        if served is None:
            unmeasured += 1
            unmeasured_causes["served_unparseable"] += 1
            continue
        compared += 1
        delta = rendition_delta(archive, served)
        if delta:
            deltas[pmcid] = delta
            # `.keys()`, not `delta` itself: `Counter.update()` on a mapping
            # *adds* the mapping's values into the count rather than
            # counting its keys, and a delta's values are
            # `{"archive": ..., "europepmc": ...}` payload dicts, not
            # numbers. Under `fields.update(delta)`, the *first* article to
            # disagree on a field already writes that raw payload dict into
            # `fields_differing` in place of `1` (`Counter.update()` takes a
            # plain-`dict.update()` fast path while it is still empty); a
            # *second* article disagreeing on the same field then tries to
            # add its own payload dict to the first — `dict + dict` — and
            # raises `TypeError`. Counting the keys is what "how often"
            # means, and is what stops both.
            fields.update(delta.keys())
    return {
        "compared": compared,
        "unmeasured": unmeasured,
        "unmeasured_causes": dict(unmeasured_causes),
        "articles_differing": len(deltas),
        "fields_differing": dict(fields),
        "deltas": deltas,
    }


def _comparison_reportable(comparison: dict[str, Any], held: int) -> bool:
    """Is a `--compare-europepmc` comparison worth reporting as evidence?

    The same rule `Totals.reportable` applies to the corpus draw — an
    unmeasured share past `UNMEASURED_SHARE_ERROR_THRESHOLD` is not a random
    sample of the population — applied here to what was *held* for
    comparison rather than to what was drawn. A throttled Europe PMC serving
    only a small minority of the held articles must not write a rate a
    later reader takes as measured, any more than an empty `for_comparison`
    may (that case is refused in `main` before this function is called).

    Args:
        comparison: A :func:`compare_renditions` result.
        held: How many articles were held for the comparison — the
            denominator `comparison["unmeasured"]` is a share of. Assumed
            never zero: every held article resolves to either `compared` or
            `unmeasured`, and the zero case is `main`'s empty-net refusal.

    Returns:
        Whether the unmeasured share is at or below
        `UNMEASURED_SHARE_ERROR_THRESHOLD`.
    """
    return comparison["unmeasured"] / held <= UNMEASURED_SHARE_ERROR_THRESHOLD


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

    unmeasured: int = 0
    # Why each unmeasured article was unmeasured, in the same two-value
    # vocabulary `compare_renditions` already reports its own by. The corpora
    # recorded a bare *count*, so "3 unmeasured" could not be read as
    # permanent (an article Europe PMC does not serve, which no re-run
    # recovers) or transient (a throttled or flaky fetch, which a re-run
    # does) — and those call for different actions from a reader deciding
    # whether the draw is finished. Not journalled, like `unmeasured` itself,
    # so it describes the attempts *this* run made.
    unmeasured_causes: Counter[str] = field(default_factory=Counter)
    rows: list[ArticleMeasurement] = field(default_factory=list)

    def add(self, row: ArticleMeasurement) -> None:
        self.rows.append(row)

    @property
    def articles(self) -> int:
        """How many rows this sample holds.

        Derived rather than counted alongside `rows`, so the two cannot
        drift: they are one fact, and issue #169's reconcile — which drops
        journalled rows outside this run's draw — is exactly the operation
        that moved one and not the other.
        """
        return len(self.rows)

    def count_unmeasured(self, cause: str) -> None:
        """Record one unmeasured article, and why.

        The count and the cause move together — incremented in one place
        rather than at each call site — so a new unmeasured path cannot
        contribute to the total while leaving the causes silently short of
        it, which would read as "the rest had no cause" rather than as a
        missing branch.
        """
        self.unmeasured += 1
        self.unmeasured_causes[cause] += 1

    def sum_of(self, attribute: str) -> int:
        return sum(int(getattr(r, attribute)) for r in self.rows)

    def counter_of(self, attribute: str) -> Counter[str]:
        merged: Counter[str] = Counter()
        for row in self.rows:
            merged.update(getattr(row, attribute))
        return merged

    def articles_where(self, attribute: str) -> int:
        """How many rows carry a non-zero *attribute*.

        ``> 0``, never truthiness: `NOT_MEASURED` is ``-1``, which is truthy,
        so a row that measured *nothing* used to count as an article that
        carries the thing — inflating exactly the article-level denominators
        the comments cite beside a counter total (issue #168).
        """
        return sum(1 for r in self.rows if _as_count(getattr(r, attribute)) > 0)

    def measured(self, attribute: str) -> bool:
        """Did **every** row actually carry *attribute*?

        Asked per row rather than of the sum, because the sentinel is a small
        negative and the sum is not: one stale row among three hundred fresh
        ones still totals positive, and the population would then print as a
        rate that quietly omits it. A journal is topped up across runs, so a
        mixed one is the ordinary case rather than a corner.
        """
        return all(_as_count(getattr(r, attribute)) >= 0 for r in self.rows)

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


def _as_count(value: object) -> int:
    """*value* as a count, for the two accessors that must not mis-read one.

    A ``Counter`` field carries no sentinel — it cannot, there being no
    negative dict — so an empty one is genuinely "measured, and empty" and a
    populated one is measured too. Reading it as ``0`` therefore says the
    right thing to both callers, where comparing the ``Counter`` itself
    against an ``int`` raised `TypeError` and made
    :meth:`Totals.measured` unusable on eleven of this row's fields.
    """
    return value if isinstance(value, int) else 0


def _unmeasured_generations(totals: Totals) -> list[str]:
    """The counter generations no row in this sample carries, named.

    `print_report` prints ``NOT MEASURED`` per section and used to return
    ``True`` anyway, so a run whose journal predated a generation wrote its
    corpus to the **canonical** path at exit 0, with ``-1`` inline and no
    marker in the header (issue #168). A population that was not measured at
    all is strictly worse than one that tripped the unmeasured-share
    threshold, and it was getting the better filename.
    """
    return sorted(
        label
        for label, names in _COUNTER_GENERATIONS.items()
        if not all(totals.measured(name) for name in names)
    )


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "n/a"
    low, high = wilson(part, whole)
    return f"{100 * part / whole:5.1f}%  [{100 * low:.1f}-{100 * high:.1f}]"


def print_report(totals: Totals) -> bool:
    """Print every population.

    Returns ``True`` only if every one of them was reportable — both that the
    sample itself is worth a rate over (:attr:`Totals.reportable`) and that no
    counter generation is missing from the rows (issue #168). The second half
    used to print ``NOT MEASURED`` and return ``True`` regardless.
    """
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
    print("\n5. SEVERAL <graphic> PER TABLE  (issue #135 — #117's rule, unexercised here)")
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

    # Issues #120 and #140 — the two spellings of a contributor's name that
    # give one undivided string, and which bmlib extracted from neither until
    # they were fixed. The rule that fixed them is spec-driven (JATS says the
    # name is undivided, and refusing to split it is what "measured, not
    # assumed" means here), so what this section answers is how much of a
    # corpus each spelling reaches, and whether a `<contrib>` nests often
    # enough for the parser's frame stack to be load-bearing in practice.
    print("\n11. HOW A <contrib> NAMES ITS CONTRIBUTOR  (issues #120, #140)")
    if not all(totals.measured(name) for name in _CONTRIB_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        contribs = totals.sum_of("contribs")
        print(f"   <contrib> elements                     : {contribs}")
        spellings = totals.counter_of("contrib_name_spellings")
        named = sum(spellings.values())
        for name, count in spellings.most_common(10):
            collected = (
                "  <-- collected as an author" if name in _CONTRIB_COLLECTED_BY_BMLIB else ""
            )
            print(f"      {name:<26} {count:>6}  {_pct(count, named)}{collected}")
        if not spellings:
            print("      (no <contrib> in this draw)")
        nested = totals.sum_of("nested_contribs")
        rosters = totals.sum_of("collabs_with_a_roster")
        print(f"   <contrib> nested inside another        : {nested:>6}  {_pct(nested, contribs)}")
        print(f"   <collab> carrying a <contrib-group>    : {rosters:>6}")
        losing = totals.articles_where("articles_losing_every_author")
        print(
            f"   articles naming every contributor undivided: "
            f"{losing:>6}   of {totals.articles}  {_pct(losing, totals.articles)}"
        )

    # Issues #142, #143, #147 and #150 — four populations each waiting on a
    # measurement before its fix can be chosen. One walk answers all four.
    print("\n12. A <collab>'s ELEMENT CHILDREN  (issue #142)")
    if not all(totals.measured(name) for name in _WAITING_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        with_children = totals.sum_of("collabs_with_element_children")
        print(f"   <collab> carrying an element child     : {with_children}")
        children = totals.counter_of("collab_children")
        total_children = sum(children.values())
        for name, count in children.most_common(12):
            print(f"      {name:<26} {count:>6}  {_pct(count, total_children)}")
        if not children:
            print("      (no <collab> in this draw carries an element child)")

    print("\n13. CONTRIBUTOR MULTIPLICITY PER <contrib>  (issue #143)")
    # Gated on `_CONTRIB_SIDE_COUNTERS` as well as `_WAITING_SIDE_COUNTERS`:
    # the denominator below is `contribs`, itself a member of the third
    # generation, so a row carrying the fifth generation's counters but not
    # the third's (an old journal topped up unevenly) must still read as
    # NOT MEASURED rather than divide by that row's sentinel.
    if not all(
        totals.measured(name) for name in (*_WAITING_SIDE_COUNTERS, *_CONTRIB_SIDE_COUNTERS)
    ):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        contribs = totals.sum_of("contribs")
        multi_collab = totals.sum_of("contribs_multi_collab")
        multi_string_name = totals.sum_of("contribs_multi_string_name")
        alternatives = totals.sum_of("name_alternatives")
        collab_alternatives = totals.sum_of("collab_alternatives")
        print(
            f"   {'<contrib> carrying >1 <collab>':<42}: "
            f"{multi_collab:>6}  {_pct(multi_collab, contribs)}"
        )
        print(
            f"   {'<contrib> carrying >1 <string-name>':<42}: "
            f"{multi_string_name:>6}  {_pct(multi_string_name, contribs)}"
        )
        print(
            f"   {'<contrib> carrying a <name-alternatives>':<42}: "
            f"{alternatives:>6}  {_pct(alternatives, contribs)}"
        )
        print(
            f"   {'<contrib> carrying a <collab-alternatives>':<42}: "
            f"{collab_alternatives:>6}  {_pct(collab_alternatives, contribs)}"
        )

    print("\n14. FORMULAS  (issue #147)")
    if not all(totals.measured(name) for name in _WAITING_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        disp_formulas = totals.sum_of("disp_formulas")
        inline_formulas = totals.sum_of("inline_formulas")
        labelled = totals.sum_of("disp_formulas_with_label")
        image_only = totals.sum_of("disp_formulas_image_only")
        both = totals.sum_of("formula_alternatives_both")
        print(f"   {'<disp-formula>':<40}: {disp_formulas}")
        print(f"   {'...carrying a <label>':<40}: {labelled:>6}  {_pct(labelled, disp_formulas)}")
        print(
            f"   {'...whose only content is a <graphic>':<40}: "
            f"{image_only:>6}  {_pct(image_only, disp_formulas)}"
        )
        print(f"   {'<inline-formula>':<40}: {inline_formulas}")
        print(f"   {'<tex-math>':<40}: {totals.sum_of('tex_math')}")
        print(f"   {'<math> (MathML)':<40}: {totals.sum_of('mml_math')}")
        print(
            f"   {'<alternatives> holding BOTH encodings':<40}: {both:>6}   "
            "(rules out one more _INLINE_ELEMENTS member)"
        )

    print("\n15. REFERENCES CARRYING ONLY A <note>  (issue #150)")
    if not all(totals.measured(name) for name in _WAITING_SIDE_COUNTERS):
        print("   NOT MEASURED — these rows predate the counter. Re-run to fill it.")
    else:
        refs = totals.sum_of("refs")
        note_only = totals.sum_of("refs_note_only")
        print(f"   <ref> elements                           : {refs}")
        print(
            f"   ...whose only content is a <note>        : {note_only:>6}  {_pct(note_only, refs)}"
        )
        kinds = totals.counter_of("ref_child_kinds")
        total_kinds = sum(kinds.values())
        print("   what a <ref> carries:")
        for name, count in kinds.most_common(12):
            print(f"      {name:<26} {count:>6}  {_pct(count, total_kinds)}")
        if not kinds:
            print("      (no <ref> in this draw)")

    missing = _unmeasured_generations(totals)
    if missing:
        print(
            "\nERROR: these counter generations are not measured in this sample — "
            + "; ".join(missing)
            + ".\n   Re-run to fill them. The rows carrying the sentinel are kept."
        )
    return not missing


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
        "--package",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Draw offline from a PMC OA baseline package — a directory of "
            "articles or a baseline .tar.gz. Repeatable. Requires --from-year "
            "and --to-year. A package draw is reproducible by any reader from "
            "(packages, window, target, seed); a live draw is not, which is "
            "what issue 132 is about."
        ),
    )
    parser.add_argument(
        "--from-year", type=int, default=None, help="Earliest publication year, inclusive."
    )
    parser.add_argument(
        "--to-year", type=int, default=None, help="Latest publication year, inclusive."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Seed for the package draw; recorded in the corpus either way.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the corpus."
    )
    parser.add_argument(
        "--per-host-interval", type=float, default=0.7, help="Minimum seconds between requests."
    )
    parser.add_argument(
        "--compare-europepmc",
        type=int,
        default=0,
        metavar="N",
        help=(
            "After a --package draw, re-fetch N of the drawn articles from "
            "Europe PMC and report where the two renditions disagree. This is "
            "what licenses citing an archive-drawn figure for a parser fed by "
            "fullTextXML."
        ),
    )
    parser.add_argument(
        "--measure-europepmc",
        action="store_true",
        help=(
            "Measure a --package draw's own rows from Europe PMC's "
            "fullTextXML, live, instead of the package's archive bytes — the "
            "rendition FullTextService actually feeds the parser. The drawn "
            "identifier list is unchanged; only the bytes measured move. An "
            "article Europe PMC will not serve is unmeasured, never measured "
            "from the archive copy instead."
        ),
    )
    return parser


def _names_default_corpus(output: Path) -> bool:
    """Whether *output* names the committed recent corpus, however spelled.

    Resolved on both sides, the rule :func:`_package_location` already
    applies: a symlink and its target, an absolute path, and ``a/../a`` are
    one location. Raw ``PurePath`` equality matched only the two spellings
    the committed command line happens to use, so
    ``-o "$PWD/tests/data/jats_exhibits.json"`` with the back-filled package
    overwrote the *recent* corpus at exit 0 (issue #165). No journal is
    committed, so on a fresh clone :func:`_journal_disagreement` cannot catch
    that either — these two guards are the only protection there is.

    ``strict=False`` throughout: the output usually does **not** exist yet,
    which is the ordinary case rather than an error.
    """
    return output.resolve(strict=False) == DEFAULT_OUTPUT.resolve(strict=False)


def _validate_args(args: argparse.Namespace) -> str | None:
    """Refuse a run that would print a rate over a draw nobody asked for.

    Returns the reason, or ``None`` when the run may proceed.

    *The window arithmetic is not negotiable.* ``--months-ago`` and
    ``--months`` are checked here as well as in :func:`_month_windows`,
    because argparse's ``type=int`` accepts a minus sign happily and the
    degradation is silent — see that function's ``Raises``.

    *``--target`` is checked for the same reason, and it degrades two
    different ways.* On the package branch a negative target reaches
    ``random.sample`` and raises `ValueError` out of the draw — loud, but as
    a stack trace out of the middle of a run, where every other bad argument
    here gets a one-line refusal before anything is touched. On the live
    branch it raises nothing at all: ``--target -5`` asks
    :func:`open_access_pmcids` for 145 rather than 150 identifiers and spends
    the search requests for them, and then ``totals.articles >= args.target``
    is true before the first article is fetched, so the walk measures nothing
    — a quietly narrower draw on one side of the same flag that crashes on
    the other.

    *Zero is deliberately **not** refused with the negatives.* It already
    fails closed on its own: ``Totals.reportable`` is ``bool(self.rows) and
    …``, so a fresh ``--target 0`` draw is unreportable, exits non-zero and
    writes to ``*.unreportable.json``. And it is a *used* value —
    ``TestTheEmptyComparisonNet`` reaches the empty-comparison net through
    it, which is the only way to hold nothing for comparison without
    monkeypatching the holder. Refusing it would remove a working lever to
    re-refuse a case that is already refused.

    *A displaced live draw may not land on the default output.* The journal
    is derived from ``--output``, so a run with ``--months-ago`` and no
    ``-o`` either overwrites the recent corpus with an older window under the
    recent corpus's name, or — journal present — tops one window's rows up
    with another's and prints the pooled result as one rate. The window
    decides the answer — the two committed corpora hold 2,448
    ``<table-wrap>`` and 0, and 58.1% against 44.0% of figures carrying
    several ``<graphic>`` — so pooling two windows produces a number
    describing neither. Naming an explicit ``-o`` is the whole fix.

    *A live-only or package-only flag used on the other source is refused,
    not silently ignored.* ``--months``/``--months-ago`` are the live
    source's strata and do nothing against ``--package``; ``--seed`` is
    recorded and consumed only by the package draw and does nothing against
    the live source. Accepting either combination in silence would let a
    run's flags describe a draw the run is not actually making. This check
    runs before the ``--months-ago``/default-output check above, which is
    what keeps that check about the *live* window: without this ordering, a
    non-default ``--months-ago`` on an otherwise valid ``--package`` run
    would trip that check and refuse for a reason belonging to a window it
    is not drawing.

    *A package window is all-or-nothing.* ``--from-year``/``--to-year`` only
    mean anything against ``--package`` — the live draw's strata are months,
    not years — so either flag without ``--package`` is refused, and
    ``--package`` without both years is refused the same way. A backwards
    window is refused rather than silently drawing nothing.

    *A package window not wholly contained in the recent corpus's window may
    not land on the default output.* The default output is
    ``tests/data/jats_exhibits.json``, drawn from ``[_RECENT_WINDOW_FIRST_YEAR,
    _RECENT_WINDOW_LAST_YEAR]``. Checking only ``to_year`` against the lower
    bound would accept ``--from-year 1996 --to-year 2025`` — a 29-year window
    whose tail happens to reach the recent one — and pool decades of a
    differently-shaped corpus into the recent draw's journal under the
    recent draw's name, which is exactly what this rule and the
    ``--months-ago`` one above both exist to prevent. Containment in both
    directions is the correct test; a fully-contained window (e.g.
    2023-2025 itself) may use the default output.

    *A `--package` path is checked here, not left to fail inside the draw.*
    :func:`iter_package_articles` calls :func:`_is_package_path` as its own
    first statement and raises immediately when it is false, but it is a
    generator — nothing runs until something iterates it, which for
    ``package_candidates`` is part way through the draw, after the "N
    candidates" line has already been decided. Calling the same predicate
    here, eagerly, means a bad path is refused with the same up-front,
    one-line message every other rule here gives, rather than surfacing as
    a stack trace out of the draw.

    *`--compare-europepmc` only means anything against a `--package` draw.*
    The live source's rows are already Europe PMC's own rendition, so
    comparing them against Europe PMC again would compare a document with
    itself; naming a negative count cannot be a request for a rate.

    *`--measure-europepmc` only means anything against a `--package` draw*,
    for the same reason: the live source's rows already are Europe PMC's
    rendition, with nothing archival to switch away from.

    *`--measure-europepmc` is deliberately not refused against
    `DEFAULT_OUTPUT`* (review round 2 flagged this as worth a word, not as a
    defect): a served draw at the default `-o` silently replaces the
    committed archive corpus, the same shape of hazard the two window-axis
    rules above both refuse outright ("must not be written to
    `DEFAULT_OUTPUT`"). Left open here because *replacing* the default
    archive corpus with a served one is this flag's whole eventual purpose,
    per the plan this task belongs to — the next task is expected to make
    that call deliberately, not be blocked from it by a rule written before
    the flag existed to be used that way.

    *`--measure-europepmc` and `--compare-europepmc` are independent and may
    be combined.* It is tempting to read the corpus's own rendition as
    deciding what a comparison could still mean, but it does not:
    `_hold_for_comparison` reads the *archive* bytes back from the package
    directly, and `compare_renditions` fetches the *served* side itself —
    neither consults which rendition `main` measured this run's own corpus
    rows from. So the comparison is exactly the archive-vs-served check it
    always is, whether or not `--measure-europepmc` is also set, and a
    corpus already measured from Europe PMC is not evidence the comparison
    would find nothing: the two questions ("what is this corpus's own
    rendition" and "how far would the archive one have diverged") are
    answered independently and both may be worth asking in one run. An
    earlier version of this function refused the combination on the
    claim that it would "report Europe PMC disagreeing with itself" — false,
    and caught in review; nothing here still asserts it.

    Left as a known cost rather than optimised (review round 2): the two
    flags together fetch each held article's ``fullTextXML`` *twice* through
    two separate paced clients — once for this run's own corpus row, once
    more inside :func:`compare_renditions` for the comparison — and the
    combination has no end-to-end test of its own, only the two flags'
    independent unit coverage. Both are acceptable for a low-frequency live
    runner and neither changes correctness, but a future reader optimising
    request count should know the overlap exists before "fixing" it.
    """
    for path in args.package:
        refusal = _package_path_refusal(path)
        if refusal is not None:
            return f"--package {refusal}"
    if args.compare_europepmc and not args.package:
        return "--compare-europepmc compares a --package draw against Europe PMC"
    if args.compare_europepmc < 0:
        return f"--compare-europepmc must not be negative, got {args.compare_europepmc}"
    if args.target < 0:
        return f"--target must not be negative, got {args.target}"
    if args.measure_europepmc and not args.package:
        return "--measure-europepmc measures a --package draw from Europe PMC; it needs one"
    if args.months < 1:
        return f"--months must be at least 1, got {args.months}"
    if args.months_ago < 0:
        return f"--months-ago must not be negative, got {args.months_ago}"
    if args.package and (args.months != SAMPLE_MONTHS or args.months_ago != 0):
        return "--months/--months-ago select the live source's strata; --package draws by year"
    if not args.package and args.seed != DEFAULT_SEED:
        return "--seed only applies to a --package draw; the live draw is not seeded"
    if args.months_ago and _names_default_corpus(args.output):
        return (
            f"--months-ago {args.months_ago} draws a displaced window, which must not "
            f"be written to {DEFAULT_OUTPUT} — that path is the recent draw, and its "
            "journal would pool the two. Pass an explicit -o, as "
            "tests/data/jats_exhibits.backfill.json was."
        )
    window = (args.from_year, args.to_year)
    if any(v is not None for v in window) and not args.package:
        return "--from-year/--to-year select from a --package; the live draw strata are months"
    if args.package and any(v is None for v in window):
        return "--package needs both --from-year and --to-year"
    if args.package and args.from_year > args.to_year:
        return f"--from-year {args.from_year} is after --to-year {args.to_year}"
    displaced = args.package and (
        args.from_year < _RECENT_WINDOW_FIRST_YEAR or args.to_year > _RECENT_WINDOW_LAST_YEAR
    )
    if displaced and _names_default_corpus(args.output):
        return (
            f"a window of {args.from_year}-{args.to_year} is not contained in the recent "
            f"draw's {_RECENT_WINDOW_FIRST_YEAR}-{_RECENT_WINDOW_LAST_YEAR}, so it must not "
            f"be written to {DEFAULT_OUTPUT} — that path is the recent draw, and its journal "
            "would pool the two. Pass an explicit -o, as "
            "tests/data/jats_exhibits.backfill.json was."
        )
    return None


def main() -> int:
    """Measure every population, print the tables, write the corpus.

    Resumable: rows land in a JSONL journal beside the output, and a later run
    tops the sample up rather than starting over. Never pooled across two
    different draws or renditions at the same `-o`, in either direction: the
    journal's own first line is a header naming `(source, rendition, draw)`
    for whatever wrote it, and a run whose own identity disagrees is refused
    rather than silently resumed — see `_journal_disagreement`. The
    rendition-qualified journal filename
    (`*.europepmc.journal.jsonl` under `--measure-europepmc`,
    `*.journal.jsonl` otherwise) only narrows how often that refusal has to
    fire; it is the header, not the name, that makes the property hold.
    """
    args = _build_arg_parser().parse_args()
    refusal = _validate_args(args)
    if refusal is not None:
        sys.stderr.write(f"{refusal}\n")
        return 2

    # This run's own identity, decided before any file is touched: which
    # source measured it, which rendition (for a --package draw), and which
    # draw — everything that decides *which identifiers* this run asks for,
    # deliberately excluding `target` (see `_journal_disagreement`). Used
    # three ways below — the journal's filename, the journal's own header
    # line, and the disagreement check between them — because the filename
    # alone cannot carry this property (see `_JOURNAL_HEADER_KEY`'s comment):
    # review round 2 reproduced two ways a shared journal silently pooled two
    # renditions under the wrong label, and round 3 reproduced the same
    # shape one axis over — two archive runs at one `-o` over different
    # packages/years, or different seeds, both pooling silently at exit 0.
    if args.package:
        source = "package"
        rendition = "europepmc" if args.measure_europepmc else "archive"
        # Computed once, here, and reused for `window["packages"]` below —
        # not recomputed a second time, which would run `_package_identity`'s
        # sibling lookup (and its stderr notices on a fallback) twice per
        # package for no reason.
        packages_identity = sorted(_package_identity(p) for p in args.package)
        draw_identity: dict[str, Any] = {
            "packages": packages_identity,
            # Where those packages actually are, which the artifact name
            # above cannot say and must not be replaced by — see
            # `_package_location`, which argues the separation in full. Both
            # are kept because neither subsumes the other: the name catches a
            # different snapshot's tarball swapped in beside an unmoved
            # directory, the path catches two different directories sharing a
            # basename (review round 4, reproduced at exit 0 — PMC's own
            # extraction names a directory by accession range alone, so the
            # collision is the ordinary layout rather than a contrived one).
            # This field is the journal's alone; the corpus header keeps
            # naming the public artifact, so a reader can still re-derive the
            # draw from it.
            "package_paths": sorted(_package_location(p) for p in args.package),
            "first_year": args.from_year,
            "last_year": args.to_year,
            "seed": args.seed,
        }
    else:
        source = "europepmc"
        rendition = "europepmc"
        # Resolved here rather than inside the live branch 150 lines below,
        # because the *resolved boundaries* are part of this run's draw
        # identity and the flags alone are not. The windows are counted back
        # from `date.today()`, so `--months 24` names one draw today and a
        # different one next month: a journal written on the last day of a
        # month resumes cleanly the day after under a window shifted by a
        # whole month, and the corpus is then stamped with the *second* run's
        # boundaries over a mix of both runs' rows — the pooling
        # `_journal_disagreement` exists to refuse, arriving on the one axis
        # the live identity did not cover. Reused verbatim for `window` below,
        # so the identity and the corpus header cannot disagree.
        windows = _month_windows(args.months, date.today(), skip=args.months_ago)
        draw_identity = {
            "months": args.months,
            "months_ago": args.months_ago,
            "first": windows[-1][0],
            "last": windows[0][1],
        }

    # Rendition-qualified for --measure-europepmc: a friendlier failure mode
    # (a --measure-europepmc run gets its own fresh journal by default,
    # rather than a name collision with the archive one on the very first
    # run), but not what makes mixing impossible on its own — `with_suffix`
    # is not injective over `(output, rendition)`, and the live branch's
    # journal name is never qualified at all, so it can still collide with a
    # --package archive draw's default journal. The header check right below
    # is what actually enforces the property; this filename choice only
    # narrows how often it has to fire.
    journal = args.output.with_suffix(
        ".europepmc.journal.jsonl" if args.measure_europepmc else ".journal.jsonl"
    )
    disagreement = _journal_disagreement(journal, source, rendition, draw_identity)
    if disagreement is not None:
        sys.stderr.write(f"{disagreement}\n")
        return 2

    totals = Totals()
    seen: set[str] = set()
    if journal.exists():
        # `_journal_disagreement` already confirmed this file decodes and
        # its header (line one, if any) agrees with this run — line one,
        # where present, is skipped here, never measured as a row.
        journal_text = _read_journal_text(journal) or ""
        for line in journal_text.splitlines()[1:]:
            if not line.strip():
                continue
            row = ArticleMeasurement.from_dict(json.loads(line))
            seen.add(row.pmcid)
            totals.add(row)
    _ensure_journal_header(journal, source, rendition, draw_identity)

    # Hoisted above the branch it is filled in, rather than left to a
    # refusal 150 lines away (`_validate_args` already refuses
    # `--compare-europepmc` without `--package`) to keep the block below from
    # depending on that refusal for an `UnboundLocalError` it would otherwise
    # only accidentally avoid.
    for_comparison: list[tuple[str, bytes]] = []

    # This run's own draw, for the reconcile below. `None` on the live branch,
    # which has no enumerable draw to reconcile against.
    expected: set[str] | None = None

    if args.package:
        # A package draw needs no network at all *unless* --measure-europepmc
        # is set: `_validate_args` has already refused every path that is not
        # a real package, so the two passes below (candidates, then the drawn
        # articles' identifiers) are the whole of it either way.
        window = {
            "source": source,
            "packages": packages_identity,
            "first_year": args.from_year,
            "last_year": args.to_year,
            "target": args.target,
            "seed": args.seed,
            # Which bytes this corpus's own rows were measured from — the
            # archive package by default, or Europe PMC's fullTextXML with
            # --measure-europepmc. Recorded unconditionally, not only when
            # the flag is set, so a reader of the file never has to consult
            # the command line that produced it to know which rendition a
            # figure describes. Reuses the same `rendition` the journal's
            # own header was just written with, rather than re-deriving the
            # ternary a second time.
            "rendition": rendition,
        }
        candidates = package_candidates(args.package, args.from_year, args.to_year)
        print(f"{len(candidates)} candidates in {args.from_year}-{args.to_year}")
        # The corpus's own full draw — kept apart from `wanted` below, which
        # a resumed run narrows to what the journal does not already hold.
        # `for_comparison` is held from this, never from `wanted`: a run
        # whose journal already holds every drawn article must still be able
        # to compare, and `wanted` would otherwise be empty for exactly that
        # run. See `_hold_for_comparison`.
        drawn = draw(candidates, args.target, args.seed)
        expected = set(drawn)
        wanted = {p for p in drawn if p not in seen}
        if args.measure_europepmc:
            # This branch never reads `read_package_articles` — there is no
            # archive byte string in scope to fall back to, so an article
            # Europe PMC will not serve cannot be silently measured from the
            # package instead *within this run*. That is only half of "one
            # corpus, one rendition": the other half is a *resumed* run not
            # pooling this run's rows with an earlier run's differently-
            # rendered (or differently-drawn) ones, and the rendition-
            # qualified journal filename chosen above does not carry that on
            # its own (review round 2 reproduced two collisions it cannot
            # rule out). What actually stops it is `_journal_disagreement`'s
            # header check above, before this branch ever runs. The
            # identifier list (`wanted`, from `drawn`) is exactly what the
            # archive branch below uses too — only the bytes' source moves.
            pace = _make_pacer(args.per_host_interval)
            with httpx.Client(
                headers={"User-Agent": _USER_AGENT}, timeout=60.0, follow_redirects=True
            ) as client:
                served = (
                    (pmcid, _fetch(client, f"{EUROPE_PMC}/{pmcid}/fullTextXML", pace))
                    for pmcid in sorted(wanted)
                )
                with journal.open("a", encoding="utf-8") as handle:
                    _measure_and_journal(handle, totals, served)
        else:
            with journal.open("a", encoding="utf-8") as handle:
                _measure_and_journal(handle, totals, read_package_articles(args.package, wanted))
        for_comparison = _hold_for_comparison(
            args.package, drawn, args.compare_europepmc, args.seed
        )
    else:
        # `windows` was resolved once, up in the identity block, before any
        # file was touched or any request made — so the corpus can state the
        # window it was drawn from and the journal's header can refuse a
        # resume under a *different* one. Without it "1996-1998" lives only in
        # prose: the windows are counted back from `date.today()`, so the
        # same command a year from now draws a different draw, and nothing in
        # the written file would say which one it is. Issue #132 is the same
        # failure by another route — a cited measurement whose corpus is not
        # in the repo.
        window = {
            "source": source,
            "months": args.months,
            "months_ago": args.months_ago,
            "first": windows[-1][0],
            "last": windows[0][1],
            # Always Europe PMC here — this source has no archive rendition
            # to choose between, unlike the --package branch above. Recorded
            # for the same reason: a reader should never need the command
            # line to know which rendition a row came from.
            "rendition": rendition,
        }
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
            with journal.open("a", encoding="utf-8") as handle:
                for pmcid in pmcids:
                    if totals.articles >= args.target:
                        break
                    raw = _fetch(client, f"{EUROPE_PMC}/{pmcid}/fullTextXML", pace)
                    if raw is None:
                        totals.count_unmeasured("europepmc_unavailable")
                        continue
                    row = measure_article(pmcid, raw)
                    if row is None:
                        totals.count_unmeasured("unparseable")
                        continue
                    totals.add(row)
                    handle.write(json.dumps(row.to_dict()) + "\n")
                    handle.flush()

    # The corpus must hold exactly the rows its own header explains (issue
    # #169). The journal is deliberately allowed to outlive one draw — that is
    # what makes a run resumable, and `_journal_disagreement` excludes
    # `target` from the draw identity so a top-up resumes rather than being
    # refused — but every row it holds used to be written into the corpus
    # regardless, under *this* run's `window`. So `--target 300` over a
    # journal of 1,000 wrote `"target": 300` above 1,000 rows, and a reader
    # following this module's own re-derivation recipe got 300 identifiers
    # against a file holding 1,000. Rows outside the draw are dropped from the
    # corpus and kept in the journal, so nothing measured is lost and the next
    # run at the larger target picks them straight back up.
    if expected is not None:
        outside = [r for r in totals.rows if r.pmcid not in expected]
        if outside:
            totals.rows = [r for r in totals.rows if r.pmcid in expected]
            print(
                f"\n{len(outside)} journalled row(s) are outside this draw of "
                f"{len(expected)} and are not written to the corpus. They stay in "
                f"{journal.name}; re-run at the larger --target to include them."
            )

    # Summarised *before* the corpus is written, so an unreportable run cannot
    # replace evidence a later reader takes as measured.
    ok = print_report(totals)
    # Named in the artifact, not only on the terminal it was printed to
    # (issue #168). A reader otherwise has to *notice* a `-1` inline to know
    # a generation is missing, and the sentinel's whole purpose is that the
    # absence not be inferred from a value.
    missing_generations = _unmeasured_generations(totals)
    destination = args.output if ok else args.output.with_suffix(".unreportable.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "articles": totals.articles,
                "unmeasured": totals.unmeasured,
                # What kind of unmeasured, in the vocabulary
                # `jats_exhibits.rendition.json` already uses. A bare count
                # cannot be read as permanent or transient, and the two call
                # for different actions from a reader asking whether the draw
                # is finished.
                "unmeasured_causes": dict(totals.unmeasured_causes),
                "not_measured_generations": missing_generations,
                "window": window,
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

    # Derived from `-o`, the way `journal` and the `.unreportable.json` corpus
    # path both are, rather than a fixed literal: `--package X -o other.json
    # --compare-europepmc 50` must not overwrite the canonical rendition
    # artifact with a different draw's numbers.
    rendition_ok = True
    if args.compare_europepmc:
        rendition_path = args.output.with_suffix(".rendition.json")
        # `window` is the package-branch provenance built above — recorded
        # here too, plus how many were requested and actually held, for the
        # same reason the corpus records its own header: the same command
        # later draws a different sample, and nothing else would say which
        # one produced this file.
        # `window`'s own `"rendition"` describes the *corpus draw* this run
        # also made (or "archive" if `--measure-europepmc` was not set) —
        # not the comparison written below it, which is always archive-vs-
        # served regardless of that setting (review round 2: dropping the
        # refusal between the two flags made this combination reachable,
        # and a bare `"rendition"` here would read as describing the
        # comparison it sits beside). Renamed on the way in, not left for a
        # reader of `jats_exhibits.rendition.json` to guess which of the two
        # meanings it has.
        provenance = {k: v for k, v in window.items() if k != "rendition"}
        provenance["corpus_rendition"] = window["rendition"]
        provenance["requested"] = args.compare_europepmc
        provenance["held"] = len(for_comparison)
        comparison: dict[str, Any] | None = None
        if for_comparison and len(for_comparison) < args.compare_europepmc:
            # `_comparison_reportable` guards the *served* side against
            # `held`, and nothing guarded `held` against `requested` (issue
            # #170). `_hold_for_comparison` returns `min(n, len(drawn))` by
            # design, and `_validate_args` does not relate
            # `--compare-europepmc` to `--target`, so a re-run at a smaller
            # target — or a package whose in-window pool shrank — held 12 of
            # 300 and overwrote the canonical artifact with `compared: 12` at
            # exit 0. The headline this repo quotes off that file would have
            # silently become a 12-article claim under the same filename.
            rendition_ok = False
            print(
                f"\nERROR: --compare-europepmc {args.compare_europepmc} requested but only "
                f"{len(for_comparison)} could be held (the draw is {args.target}); refusing "
                "to write a comparison at the canonical name over a smaller sample than "
                "the one asked for."
            )
        if not for_comparison:
            # The net for whatever empties `for_comparison`, `_hold_for_
            # comparison`'s own fix included: a comparison over nothing
            # would write "0 compared, 0 differing", indistinguishable from
            # a genuine null result, at the exact canonical name the next
            # two tasks cite as what licenses every archive-drawn figure.
            rendition_ok = False
            print(
                "\nERROR: --compare-europepmc requested but no articles were held for "
                "comparison; refusing to write a result indistinguishable from a "
                "genuine null finding."
            )
        else:
            pace = _make_pacer(args.per_host_interval)
            with httpx.Client(
                headers={"User-Agent": _USER_AGENT}, timeout=60.0, follow_redirects=True
            ) as client:
                comparison = compare_renditions(client, pace, for_comparison)
            print(
                f"\nRendition: {comparison['compared']} compared, "
                f"{comparison['unmeasured']} unmeasured, "
                f"{comparison['articles_differing']} differing"
            )
            if not _comparison_reportable(comparison, len(for_comparison)):
                rendition_ok = False
                share = comparison["unmeasured"] / len(for_comparison)
                print(
                    f"ERROR: {share:.0%} of held articles were unmeasured "
                    f"(threshold {UNMEASURED_SHARE_ERROR_THRESHOLD:.0%}). This comparison "
                    "is not evidence."
                )
        rendition_destination = (
            rendition_path if rendition_ok else rendition_path.with_suffix(".unreportable.json")
        )
        rendition_destination.parent.mkdir(parents=True, exist_ok=True)
        rendition_destination.write_text(
            json.dumps({**provenance, "comparison": comparison}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {rendition_destination}")

    return 0 if (ok and rendition_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
