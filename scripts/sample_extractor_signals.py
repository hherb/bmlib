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

"""Measure bmlib's rule-based extractors over two labelled populations (issue #368).

``bmlib/quality/extractors.py`` decides four things from text — a sample size,
whether a power calculation is reported, whether a confidence interval is, and
a study type — and issues #294, #297 and #298 were decided on a draw no
committed script produced. This is that draw as an instrument, so the next
change to the extractors has something to re-run.

**Two populations, measured the same way.**

* ``abstracts`` — Europe PMC ``SRC:MED`` English abstracts in twenty-one
  strata: seven PubMed publication types × 2006 / 2014 / 2023. The publication
  type is the **ground truth** for the study-type confusion, and it is
  PubMed's, not bmlib's, so the label cannot confirm the rule under test.
* ``fulltext`` — the articles of one named Europe PMC served bundle (the audit
  used ``PMC10030002_PMC10040000.xml.gz``) whose abstract and body, stripped of
  markup, hold at least :data:`MIN_FULLTEXT_CHARS` characters. Offline.

**A random draw, not a relevance page.** The audit took the first page by
Europe PMC relevance, which is an ordering, not a sample (the #127 lesson: one
window is not the rate). Here each stratum's whole population is enumerated by
cursor and a seeded sample taken from the *sorted* identifiers, so the draw
depends on ``(query, seed, population)`` and not on the order pages arrive in.
``--draw relevance`` re-takes the audit's own first page, which is how this
instrument was validated against PR #370's published figures; it may not be
written to the committed corpus's path.

**Identifiers, not text.** Abstracts are the publishers'. A corpus records each
record's identifier, its PubMed publication types, a SHA-256 of the exact text
the extractors read, and the four signals. ``remeasure`` re-fetches the text
for the committed identifiers, runs the *current* extractors, and prints what
moved — separating a record whose text changed upstream (its hash moved, so the
comparison is not of the code) from one the code moved.

**The text is the audit's, which is not every caller's.** An abstract is Europe
PMC's ``abstractText`` as served, HTML section headings and ``<sub>``/``<i>``
included; bmlib's own PubMed fetcher stores escaped Markdown instead
(``G\\*Power``, ``~95%~``), and ``FullTextService`` returns ``JATSParser``'s
HTML where the full-text half regex-strips the raw XML. The extractors read
markup in both spellings (``_CI_MARKUP``), but a figure here is of these texts,
kept because they are what the decisions on file were taken on.

**Counts, not judgements.** Every figure printed is a count of what the
extractor returned. Whether a power or CI credit is *genuine* is a judgement
this script cannot make; the hand-labelled readings behind
``docs/DECISIONS.md``'s #297 entry are in PR #370's body and are not
re-derivable from anything here.

It shares the other runners' rules (``scripts/_sampling.py``): the per-host
pacer and the two-ended ``Retry-After`` clamp, a record that could not be
measured entering no denominator, a stratum past
``UNMEASURED_SHARE_ERROR_THRESHOLD`` reporting ``ERROR`` rather than a share
and its corpus going to ``*.unreportable.json``, and a non-zero exit.

Run it before changing ``bmlib/quality/extractors.py``, and after::

    uv run python scripts/sample_extractor_signals.py remeasure --email you@example.org
    uv run python scripts/sample_extractor_signals.py fulltext \\
        ~/europepmc/packages/PMC10030002_PMC10040000.xml.gz --baseline

``tests/test_extractor_sampler.py`` fails while either committed corpus was
measured by an ``extractors.py`` other than the current one.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from _sampling import (
    MAX_PROBE_ATTEMPTS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _make_pacer,
    _sleep_for,
    _throttle_delay,
    wilson,
)

from bmlib.quality.extractors import (
    extract_study_type,
    find_sample_size,
    get_extracted_study_type,
    has_ci_reporting,
    has_power_calculation,
    prepare_extractor_search_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTORS_PATH = REPO_ROOT / "bmlib" / "quality" / "extractors.py"
DEFAULT_ABSTRACTS_OUTPUT = REPO_ROOT / "tests" / "data" / "extractor_abstracts.json"
DEFAULT_FULLTEXT_OUTPUT = REPO_ROOT / "tests" / "data" / "extractor_fulltext.json"

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
#: The audit's query, verbatim apart from the two placeholders.
QUERY_TEMPLATE = (
    'PUB_TYPE:"{publication_type}" AND HAS_ABSTRACT:y AND PUB_YEAR:{year} AND SRC:MED AND LANG:eng'
)
PUBLICATION_TYPES = (
    "Randomized Controlled Trial",
    "Controlled Clinical Trial",
    "Clinical Trial",
    "Observational Study",
    "Systematic Review",
    "Meta-Analysis",
    "Case Reports",
)
YEARS = (2006, 2014, 2023)
DEFAULT_PER_STRATUM = 300
DEFAULT_SEED = 0
#: Europe PMC's largest page. The populations run to 54,486 records a stratum
#: (Case Reports 2023, 2026-10-09), 312,831 over all twenty-one.
ID_PAGE_SIZE = 1000
#: PMIDs per ``EXT_ID:(… OR …)`` lookup. A hundred keeps the URL near 1.3 kB.
LOOKUP_BATCH = 100
PER_HOST_INTERVAL_SECONDS = 1.0
#: Walks of one stratum before it is failed. **Europe PMC's cursor walk is not
#: reliable at this size**: a walk delivers some records twice and misses as
#: many, or ends short. Measured 2026-10-09 over one draw of the twenty
#: strata with records: 11 faulty walks in 7 strata, each 1 to 138 records
#: off, and the largest stratum (Case Reports 2023, 54,486) faulty on
#: three walks running — so "walk again until one is clean" fails exactly
#: where the population is biggest. Each faulty walk misses *different*
#: records, so the population is the **union** of successive walks, taken
#: until it holds exactly ``hitCount`` PMIDs (:func:`walk_until_reconciled`).
WALK_ATTEMPTS = 6
HTTP_TIMEOUT_SECONDS = 60.0
_THROTTLE_STATUSES = frozenset({429, 503})
#: Exceptions that can only mean this script is wrong about its own call, never
#: that the remote failed. Re-raised rather than filed as a hole (#214's split
#: in ``sample_api_failures.py``): under the threshold a hole exits 0, so an
#: instrument defect would otherwise pass as a slightly thinner sample.
#: Restated from ``bmlib.transparency.analyzer._BUG_TYPES``, as the repo's
#: other copies are.
_BUG_TYPES = (AttributeError, TypeError, NameError, KeyError, IndexError, AssertionError)

#: PubMed publication types to one label, first match wins — the audit's order
#: (issue #368). A review outranks a trial because a meta-analysis *of* RCTs
#: carries both types and is not itself one; an RCT outranks the wider trial
#: types for the same reason. Compared case-folded: the value is PubMed's.
LABEL_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("review", ("Meta-Analysis", "Systematic Review")),
    ("rct", ("Randomized Controlled Trial",)),
    ("non_randomised", ("Controlled Clinical Trial",)),
    ("clinical_trial", ("Clinical Trial",)),
    ("observational", ("Observational Study",)),
    ("case_report", ("Case Reports",)),
)
OTHER_LABEL = "other"

#: A full text shorter than this is not measured: the audit's threshold, and
#: the rule below reproduces its 7,410 of 8,118 exactly.
MIN_FULLTEXT_CHARS = 500
_ARTICLE = re.compile(r"<article[\s>].*?</article>", re.S)
_ARTICLE_OPENER = re.compile(r"<article[\s>]")
_ABSTRACT = re.compile(r"<abstract[\s>].*?</abstract>", re.S)
_BODY = re.compile(r"<body[\s>].*?</body>", re.S)
_TAG = re.compile(r"<[^>]+>")
_PMCID = re.compile(r'<article-id pub-id-type="pmc(?:id)?">\s*(?:PMC)?(\d+)\s*</article-id>')

#: Why a drawn record has no signals. Every one is a hole in its stratum's
#: sample, so every one counts towards the unmeasured share and none enters a
#: denominator. ``no-abstract`` is a contradiction rather than a finding — the
#: query asked for ``HAS_ABSTRACT:y`` — so it is a hole too.
UNMEASURED_STATUSES = frozenset({"failed", "absent", "no-abstract"})
MEASURED_STATUS = "ok"


def _is_hole(status: str) -> bool:
    """Whether a row's *status* is a hole, refusing one on neither side of the partition."""
    if status == MEASURED_STATUS:
        return False
    if status in UNMEASURED_STATUSES:
        return True
    raise ValueError(f"a record status this script does not know: {status!r}")


# ---- Measuring a text ----


@dataclass(frozen=True)
class Signals:
    """The four things the extractors decide about one text.

    Measured through the public functions a caller uses, on the document shape
    a caller passes — ``prepare_extractor_search_text`` chooses the text, as
    ``extract_sample_size_dimension`` does — so a change to that choice moves
    this instrument as it moves a caller. ``power`` and ``ci`` are the raw
    predicates, not the dimension's bonuses, which are granted only where a
    sample size was found; every #297 figure is of the predicates.
    """

    sample_size: int | None
    power: bool
    ci: bool
    study_type: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise for a corpus row."""
        return {
            "sample_size": self.sample_size,
            "power": self.power,
            "ci": self.ci,
            "study_type": self.study_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Signals:
        """Read a corpus row's signals, refusing a value of the wrong type.

        A ``bool`` is an ``int`` in Python, so ``sample_size: true`` is refused
        by name rather than read as a sample of one.
        """
        size = data.get("sample_size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int)):
            raise ValueError(f"sample_size must be an int or null, not {size!r}")
        power, ci, study_type = data.get("power"), data.get("ci"), data.get("study_type")
        if not isinstance(power, bool) or not isinstance(ci, bool):
            raise ValueError(f"power and ci must be booleans, not {power!r} and {ci!r}")
        if not isinstance(study_type, str) or not study_type:
            raise ValueError(f"study_type must be a non-empty string, not {study_type!r}")
        return cls(size, power, ci, study_type)


def measure(document: dict[str, str]) -> Signals:
    """Run the extractors over *document* exactly as a caller would."""
    text = prepare_extractor_search_text(document)
    study_type = get_extracted_study_type(extract_study_type(document)) or "unknown"
    return Signals(
        sample_size=find_sample_size(text),
        power=has_power_calculation(text),
        ci=has_ci_reporting(text),
        study_type=study_type,
    )


def text_digest(text: str) -> str:
    """SHA-256 of *text*, which is what lets ``remeasure`` tell a code move from a text move."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extractors_digest(path: Path = EXTRACTORS_PATH) -> str:
    """SHA-256 of the extractor module's source: which code measured a corpus."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label_for(publication_types: list[str]) -> str:
    """Map a record's PubMed publication types to one ground-truth label."""
    held = {pt.casefold() for pt in publication_types}
    for label, members in LABEL_ORDER:
        if any(member.casefold() in held for member in members):
            return label
    return OTHER_LABEL


# ---- Strata ----


@dataclass(frozen=True)
class Stratum:
    """One publication type in one year."""

    publication_type: str
    year: int

    @property
    def key(self) -> str:
        """The stratum's name in a corpus and a report."""
        return f"{self.publication_type}/{self.year}"

    @property
    def query(self) -> str:
        """The Europe PMC query that defines this stratum's population."""
        return QUERY_TEMPLATE.format(publication_type=self.publication_type, year=self.year)


def all_strata() -> list[Stratum]:
    """Every publication type × year, in the audit's order."""
    return [Stratum(pt, year) for pt in PUBLICATION_TYPES for year in YEARS]


# ---- Talking to Europe PMC ----


class RequestFailedError(Exception):
    """A Europe PMC request that answered nothing this script can read."""


def get_json(client: Any, params: dict[str, Any], pace: Callable[[str], None]) -> dict[str, Any]:
    """One paced search request, retried while throttled, returning the JSON object.

    Raises:
        RequestFailedError: The request raised, answered a status other than 200
            after its throttle retries, or answered a body that is not a JSON
            object. Each is a hole in the sample, never a finding about it.
    """
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        pace(SEARCH_URL)
        try:
            resp = client.get(SEARCH_URL, params=params)
        except _BUG_TYPES:
            raise
        except Exception as exc:  # a transport failure is not a measurement
            raise RequestFailedError(f"request raised {type(exc).__name__}: {exc}") from exc
        if resp.status_code in _THROTTLE_STATUSES and attempt < MAX_PROBE_ATTEMPTS:
            _sleep_for(_throttle_delay(resp, attempt))
            continue
        if resp.status_code != 200:
            raise RequestFailedError(f"HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise RequestFailedError(f"body is not JSON ({exc})") from exc
        if not isinstance(body, dict):
            raise RequestFailedError(f"body is a JSON {type(body).__name__}, not an object")
        return body
    raise RequestFailedError(
        "throttled on every attempt"
    )  # pragma: no cover - loop returns or raises


def _hit_count(body: dict[str, Any]) -> int:
    """The envelope's ``hitCount``, refused rather than defaulted when absent or mistyped."""
    count = body.get("hitCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RequestFailedError(f"envelope carries no usable hitCount ({count!r})")
    return count


def _results(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The envelope's ``resultList.result``, refused when it is not a list of objects.

    Read through ``isinstance`` and not ``.get(…, {})``: a key present with
    ``null`` returns the value, not the default, which is the defect
    ``_json_object`` exists to prevent one package over.
    """
    result_list = body.get("resultList")
    results = result_list.get("result") if isinstance(result_list, dict) else None
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        raise RequestFailedError("envelope carries no list of record objects")
    return results


def _pmid_of(record: dict[str, Any]) -> str | None:
    """A ``SRC:MED`` record's PMID, or ``None`` for anything else."""
    pmid = record.get("id")
    if record.get("source") != "MED" or not isinstance(pmid, str) or not pmid.isdigit():
        return None
    return pmid


@dataclass
class Population:
    """What a stratum's enumeration found."""

    hit_count: int
    pmids: list[str]

    @property
    def digest(self) -> str:
        """SHA-256 of the sorted, comma-joined PMIDs: *which* population was sampled.

        A count alone cannot tell a re-draw from the original when the same
        number of records is a different set; this can, so the draw's claim to
        be a function of ``(query, seed, population)`` is checkable.
        """
        return hashlib.sha256(",".join(sorted(self.pmids, key=int)).encode()).hexdigest()


def walk_once(client: Any, stratum: Stratum, pace: Callable[[str], None]) -> tuple[int, list[str]]:
    """One cursor walk of a stratum: its ``hitCount`` and every PMID delivered, repeats kept.

    Raises:
        RequestFailedError: A page failed, a record carries no PMID, or the
            count moved between pages.
    """
    cursor = "*"
    promised: int | None = None
    pmids: list[str] = []
    while True:
        body = get_json(
            client,
            {
                "query": stratum.query,
                "resultType": "idlist",
                "format": "json",
                "pageSize": ID_PAGE_SIZE,
                "cursorMark": cursor,
            },
            pace,
        )
        count = _hit_count(body)
        if promised is None:
            promised = count
        elif count != promised:
            raise RequestFailedError(f"population moved during the walk ({promised} then {count})")
        page = _results(body)
        for record in page:
            pmid = _pmid_of(record)
            if pmid is None:
                raise RequestFailedError(f"a record carries no PMID: {record!r}")
            pmids.append(pmid)
        next_cursor = body.get("nextCursorMark")
        if not page or not isinstance(next_cursor, str) or next_cursor == cursor:
            break
        cursor = next_cursor
    assert promised is not None  # the loop runs at least once
    return promised, pmids


def _walk_fault(promised: int, pmids: list[str]) -> str | None:
    """What is wrong with one walk, or ``None`` when it delivered the population once each."""
    repeated = len(pmids) - len(set(pmids))
    if repeated:
        return f"the walk repeated {repeated} records"
    if len(pmids) != promised:
        return f"the walk delivered {len(pmids)} of {promised} promised records"
    return None


def enumerate_population(client: Any, stratum: Stratum, pace: Callable[[str], None]) -> Population:
    """One walk of a stratum, which must deliver what its first page promised.

    Every record a PMID, none repeated, and as many as ``hitCount`` — or the
    walk fails. A short walk is the quiet failure ``fetchers/_reconcile.py``
    exists for in sync: a sample drawn from part of a population reads exactly
    like one drawn from all of it.

    Raises:
        RequestFailedError: A page failed, the walk did not reconcile, or the
            population moved under it.
    """
    promised, pmids = walk_once(client, stratum, pace)
    fault = _walk_fault(promised, pmids)
    if fault is not None:
        raise RequestFailedError(fault)
    return Population(hit_count=promised, pmids=pmids)


def walk_until_reconciled(
    client: Any, stratum: Stratum, pace: Callable[[str], None], attempts: int = WALK_ATTEMPTS
) -> tuple[Population, int]:
    """Walk a stratum until the union of its walks holds exactly ``hitCount`` PMIDs.

    A clean first walk returns at once. Otherwise each further walk adds what
    it delivered, and the union reconciles when it reaches the count — never
    past it, which would mean the population changed between walks, and never
    against a count that moved, for the same reason. A walk whose request
    failed adds nothing and spends an attempt.

    Returns:
        The population and the number of walks it took.

    Raises:
        RequestFailedError: The count moved between walks, the union overshot
            it, or *attempts* walks left the union short.
    """
    union: set[str] = set()
    promised: int | None = None
    for walk in range(1, attempts + 1):
        try:
            count, pmids = walk_once(client, stratum, pace)
        except RequestFailedError as exc:
            print(f"  walk {walk} of {stratum.key}: {exc}", file=sys.stderr)
            continue
        if promised is None:
            promised = count
        elif count != promised:
            raise RequestFailedError(f"population moved between walks ({promised} then {count})")
        union.update(pmids)
        fault = _walk_fault(count, pmids)
        if fault is not None:
            print(f"  walk {walk} of {stratum.key}: {fault}", file=sys.stderr)
        if len(union) > promised:
            raise RequestFailedError(f"the walks' union holds {len(union)} of {promised} records")
        if len(union) == promised:
            return Population(hit_count=promised, pmids=sorted(union, key=int)), walk
    held = f"{len(union)} of {promised}" if promised is not None else "nothing"
    raise RequestFailedError(f"after {attempts} walks the union holds {held} records")


def relevance_page(
    client: Any, stratum: Stratum, size: int, pace: Callable[[str], None]
) -> Population:
    """The audit's draw: the first *size* records by Europe PMC relevance, in that order."""
    body = get_json(
        client,
        {"query": stratum.query, "resultType": "idlist", "format": "json", "pageSize": size},
        pace,
    )
    pmids = []
    for record in _results(body):
        pmid = _pmid_of(record)
        if pmid is None:
            raise RequestFailedError(f"a record carries no PMID: {record!r}")
        pmids.append(pmid)
    hit_count = _hit_count(body)
    if len(pmids) != min(size, hit_count):
        raise RequestFailedError(
            f"the page delivered {len(pmids)} of {min(size, hit_count)} records"
        )
    return Population(hit_count=hit_count, pmids=pmids)


def draw_sample(pmids: list[str], size: int, seed: int, stratum: Stratum) -> list[str]:
    """A seeded sample of *size* from *pmids*, independent of the order they arrived in.

    The pool is sorted numerically before sampling, and the generator is seeded
    per stratum by name — a ``str`` seed is hashed with SHA-512, so it is the
    same in every process — which makes the draw a function of the population
    and the seed alone. Returned sorted, so a corpus diffs cleanly.
    """
    pool = sorted(set(pmids), key=int)
    rng = random.Random(f"{seed}/{stratum.key}")
    return sorted(rng.sample(pool, min(size, len(pool))), key=int)


@dataclass(frozen=True)
class Fetched:
    """One record's abstract lookup: what came back, or why nothing did."""

    status: str
    abstract: str = ""
    publication_types: tuple[str, ...] = ()


def _publication_types(record: dict[str, Any]) -> tuple[str, ...]:
    """A core record's ``pubTypeList.pubType``, which may be a list or a lone string."""
    holder = record.get("pubTypeList")
    raw = holder.get("pubType") if isinstance(holder, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(pt for pt in raw if isinstance(pt, str))


def fetch_abstracts(
    client: Any, pmids: list[str], pace: Callable[[str], None], batch: int = LOOKUP_BATCH
) -> dict[str, Fetched]:
    """Look *pmids* up in batches, returning one :class:`Fetched` per PMID.

    A batch whose request failed marks every one of its PMIDs ``failed``; a PMID
    the answer omits is ``absent``; one returned with no abstract text is
    ``no-abstract``. All three are holes, and each is kept apart so a report can
    say which kind of hole a stratum has.
    """
    fetched: dict[str, Fetched] = {}
    for start in range(0, len(pmids), batch):
        chunk = pmids[start : start + batch]
        query = "EXT_ID:(" + " OR ".join(chunk) + ") AND SRC:MED"
        try:
            body = get_json(
                client,
                {"query": query, "resultType": "core", "format": "json", "pageSize": len(chunk)},
                pace,
            )
            records = _results(body)
            _hit_count(body)
        except RequestFailedError as exc:
            print(f"  lookup of {len(chunk)} PMIDs failed: {exc}", file=sys.stderr)
            fetched.update({pmid: Fetched("failed") for pmid in chunk})
            continue
        by_pmid = {pmid: r for r in records if (pmid := _pmid_of(r)) is not None}
        # A page that delivered fewer records than it counted was cut short,
        # so a PMID missing from it is this run's hole, not Europe PMC saying
        # the record is gone — and `remeasure --write` writes `absent` rows.
        short = len(records) < min(_hit_count(body), len(chunk))
        for pmid in chunk:
            record = by_pmid.get(pmid)
            if record is None:
                fetched[pmid] = Fetched("failed" if short else "absent")
                continue
            abstract = record.get("abstractText")
            types = _publication_types(record)
            if not isinstance(abstract, str) or not abstract.strip():
                fetched[pmid] = Fetched("no-abstract", publication_types=types)
                continue
            fetched[pmid] = Fetched(MEASURED_STATUS, abstract, types)
    return fetched


def record_row(fetched: Fetched) -> dict[str, Any]:
    """A corpus row for one looked-up record. Text is hashed, never kept."""
    row: dict[str, Any] = {"status": fetched.status}
    if fetched.publication_types:
        row["publication_types"] = list(fetched.publication_types)
    if fetched.status == MEASURED_STATUS:
        row["text_sha256"] = text_digest(fetched.abstract)
        row["text_chars"] = len(fetched.abstract)
        row["signals"] = measure({"abstract": fetched.abstract}).to_dict()
    return row


# ---- Full text ----


def bundle_articles(path: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(identifier, article XML)`` from a gzipped served bundle.

    Split on the root element (``<article`` then whitespace or ``>``, since a
    bare word boundary also matches ``<article-meta``). The identifier is the
    article's PMCID, or its position where it declares none.

    Raises:
        ValueError: The bundle's ``<article`` openers and ``</article>`` closers
            disagree, which means the non-greedy split welded two articles.
    """
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        text = stream.read()
    openers, closers = len(_ARTICLE_OPENER.findall(text)), text.count("</article>")
    if openers != closers:
        raise ValueError(f"{path.name}: {openers} <article> openers against {closers} closers")
    seen: Counter[str] = Counter()
    for index, match in enumerate(_ARTICLE.finditer(text)):
        xml = match.group(0)
        found = _PMCID.search(xml)
        ident = f"PMC{found.group(1)}" if found else f"article_{index}"
        seen[ident] += 1
        yield (ident if seen[ident] == 1 else f"{ident}#{seen[ident]}"), xml


def article_text(xml: str) -> str:
    """An article's abstract and body text, as the audit stripped it.

    Every ``<abstract>``, then every ``<body>``, each in document order, each tag
    replaced by a space and the entities decoded. This is **wider than the
    article's own text** — a nested ``<sub-article>``'s body is included, as it
    was in the audit — and it is kept so because it is the rule that reproduces
    the audit's population: of the six strippings tried on 2026-10-09 (tags to
    nothing or to a space, entities decoded or not, an XML parse's
    ``itertext()``) only this one yields 7,410 of 8,118, and the same rule
    reproduces PR #370's power moves exactly (207 lost, 135 gained).
    """
    parts = _ABSTRACT.findall(xml) + _BODY.findall(xml)
    return html.unescape(_TAG.sub(" ", " ".join(parts)))


# ---- Corpus ----


def corpus_header(population: str, **extra: Any) -> dict[str, Any]:
    """The header every corpus carries: what was measured, by which code, when."""
    return {
        "instrument": "scripts/sample_extractor_signals.py",
        "population": population,
        "extractors_sha256": extractors_digest(),
        "measured_on": date.today().isoformat(),
        **extra,
    }


def _row_order(ident: str) -> tuple[int, str]:
    """Order identifiers numerically within one spelling (``PMID``s, ``PMC…``)."""
    return len(ident), ident


def dumps_corpus(corpus: dict[str, Any]) -> str:
    """Serialise *corpus* with one record, and one stratum, per line.

    Compact, and a re-measurement's diff then reads as the records that moved.
    The header stays indented, being what a reader looks at first.
    """
    entries = []
    for key in sorted(corpus):
        value = corpus[key]
        if key == "records":
            rows = [
                f"    {json.dumps(ident)}: {json.dumps(value[ident], sort_keys=True)}"
                for ident in sorted(value, key=_row_order)
            ]
            body = "{\n" + ",\n".join(rows) + "\n  }" if rows else "{}"
        elif key == "strata":
            rows = [f"    {json.dumps(row, sort_keys=True)}" for row in value]
            body = "[\n" + ",\n".join(rows) + "\n  ]" if rows else "[]"
        else:
            body = json.dumps(value, sort_keys=True, indent=2).replace("\n", "\n  ")
        entries.append(f"  {json.dumps(key)}: {body}")
    return "{\n" + ",\n".join(entries) + "\n}\n"


def write_corpus(corpus: dict[str, Any], path: Path, reportable: bool) -> Path:
    """Write *corpus*, diverting an unreportable one away from *path*.

    Returns:
        Where it was written. An unreportable run goes to
        ``<stem>.unreportable.json``, so a throttled run cannot replace the
        evidence a later reader takes as measured.
    """
    target = path if reportable else path.with_name(f"{path.stem}.unreportable.json")
    target.write_text(dumps_corpus(corpus), encoding="utf-8")
    return target


def load_corpus(path: Path) -> dict[str, Any]:
    """Read a corpus, refusing one that is not shaped like this script's output."""
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(corpus, dict) or not isinstance(corpus.get("header"), dict):
        raise ValueError(f"{path}: not a corpus this script wrote (no header)")
    if not isinstance(corpus.get("records"), dict):
        raise ValueError(f"{path}: not a corpus this script wrote (no records)")
    return corpus


# ---- Reports ----


def _share(k: int, n: int) -> str:
    """``k/n  pct [low-high]`` with a Wilson interval, or a dash over nothing."""
    if n == 0:
        return f"{k}/0  -"
    low, high = wilson(k, n)
    return f"{k}/{n}  {100 * k / n:5.1f}% [{100 * low:.1f}-{100 * high:.1f}]"


def _measured_signals(records: dict[str, Any], ids: list[str]) -> list[Signals]:
    """The signals of every measured record among *ids*."""
    return [
        Signals.from_dict(records[i]["signals"])
        for i in ids
        if records.get(i, {}).get("status") == MEASURED_STATUS
    ]


def _signal_lines(signals: list[Signals], indent: str = "    ") -> list[str]:
    """The three per-population signal rows."""
    n = len(signals)
    return [
        f"{indent}sample size found  {_share(sum(s.sample_size is not None for s in signals), n)}",
        f"{indent}power credited     {_share(sum(s.power for s in signals), n)}",
        f"{indent}CI credited        {_share(sum(s.ci for s in signals), n)}",
    ]


def report_abstracts(corpus: dict[str, Any]) -> tuple[list[str], bool]:
    """The abstract corpus's report, and whether every stratum is reportable.

    Rows are per stratum (a record drawn in two strata counts in both, as the
    audit's tables did); the confusion is over unique records, labelled by
    their own publication types, since a record drawn for ``Clinical Trial``
    may also be an RCT.
    """
    records: dict[str, Any] = corpus["records"]
    lines = ["Per stratum (rows; a record drawn in two strata counts in both)", ""]
    reportable = True
    for stratum in corpus["strata"]:
        key = f"{stratum['publication_type']}/{stratum['year']}"
        status = stratum["status"]
        if status == "failed":
            reportable = False
            lines.append(
                f"  {key}: ERROR — the population could not be drawn ({stratum['reason']})"
            )
            continue
        pmids: list[str] = stratum["pmids"]
        if not pmids:
            lines.append(f"  {key}: population {stratum['population']}, nothing to draw")
            continue
        holes = Counter(records[p]["status"] for p in pmids if _is_hole(records[p]["status"]))
        unmeasured = sum(holes.values())
        share = unmeasured / len(pmids)
        hole_text = ", ".join(f"{k} {v}" for k, v in sorted(holes.items())) or "none"
        lines.append(
            f"  {key}: population {stratum['population']}, drawn {len(pmids)}, "
            f"unmeasured {unmeasured} ({hole_text})"
        )
        if share > UNMEASURED_SHARE_ERROR_THRESHOLD:
            reportable = False
            lines.append(
                f"    ERROR — {100 * share:.1f}% unmeasured, past the "
                f"{100 * UNMEASURED_SHARE_ERROR_THRESHOLD:.0f}% threshold; no share is printed"
            )
            continue
        lines.extend(_signal_lines(_measured_signals(records, pmids)))

    if not reportable:
        # Pooling the measured records of an ERROR stratum would print, one
        # section down, the share that stratum was refused.
        lines += ["", "Pooled rows and the confusion: ERROR — a stratum above is unreportable"]
        return lines, reportable
    measured = {i: r for i, r in records.items() if r["status"] == MEASURED_STATUS}
    lines += [
        "",
        f"Over {len(measured)} unique measured records — unweighted across strata, so a",
        "summary of this draw and not a rate for any population",
        "",
    ]
    lines.extend(_signal_lines([Signals.from_dict(r["signals"]) for r in measured.values()], "  "))
    lines += ["", "extract_study_type against PubMed's publication type (unique records)", ""]
    confusion: dict[str, Counter[str]] = {}
    for row in measured.values():
        label = label_for(row.get("publication_types", []))
        confusion.setdefault(label, Counter())[row["signals"]["study_type"]] += 1
    order = [label for label, _ in LABEL_ORDER] + [OTHER_LABEL]
    for label in order:
        if label not in confusion:
            continue
        counts = confusion[label]
        total = sum(counts.values())
        spread = ", ".join(f"{t} {c}" for t, c in counts.most_common())
        lines.append(f"  {label:<15} {total:>5}: {spread}")
    others: Counter[str] = Counter()
    for row in measured.values():
        types = row.get("publication_types", [])
        if label_for(types) == OTHER_LABEL:
            others.update(set(types))
    if others:
        # Europe PMC's `PUB_TYPE:"Clinical Trial"` is a phrase search, so it also
        # draws "Clinical Trial Protocol", "Clinical Trial, Phase II" and the
        # veterinary types, none of which the audit's exact labels name. Shown
        # rather than folded in: which label a protocol deserves is not a count.
        shown = ", ".join(f"{t} {c}" for t, c in others.most_common(10))
        lines.append(f"  {'':<15} {'':>5}  ({OTHER_LABEL} records carry: {shown})")
    return lines, reportable


def report_fulltext(corpus: dict[str, Any]) -> list[str]:
    """The full-text corpus's report."""
    header = corpus["header"]
    signals = [Signals.from_dict(r["signals"]) for r in corpus["records"].values()]
    types = Counter(s.study_type for s in signals)
    return [
        f"{header['artifact']}: {header['articles']} articles, {len(signals)} with at least "
        f"{header['min_chars']} characters of abstract and body",
        "",
        *_signal_lines(signals, "  "),
        "",
        "  study type: " + ", ".join(f"{t} {c}" for t, c in types.most_common()),
    ]


# ---- Comparing a fresh measurement with a corpus ----


@dataclass
class Comparison:
    """What moved between a corpus and a fresh measurement of the same records."""

    compared: int = 0
    text_moved: list[str] = field(default_factory=list)
    not_compared: list[str] = field(default_factory=list)
    moves: dict[str, list[str]] = field(default_factory=dict)


SIGNAL_NAMES = ("sample_size", "power", "ci", "study_type")


def compare(old: dict[str, Any], new: dict[str, Any]) -> Comparison:
    """Diff two record maps keyed by the same identifiers.

    A record is compared only where both sides measured it **and** read the
    same text; one whose hash moved is counted apart, because a move there is
    the remote's and not the code's. One measured on either side only is
    ``not_compared`` — a hole, never a move.
    """
    result = Comparison(moves={name: [] for name in SIGNAL_NAMES})
    for ident in sorted(set(old) | set(new)):
        before, after = old.get(ident, {}), new.get(ident, {})
        if before.get("status") != MEASURED_STATUS or after.get("status") != MEASURED_STATUS:
            result.not_compared.append(ident)
            continue
        if before["text_sha256"] != after["text_sha256"]:
            result.text_moved.append(ident)
            continue
        result.compared += 1
        for name in SIGNAL_NAMES:
            if before["signals"][name] != after["signals"][name]:
                result.moves[name].append(ident)
    return result


def report_comparison(comparison: Comparison, examples: int = 10) -> list[str]:
    """The diff, with the identifiers that moved so a reviewer can read them."""
    lines = [
        f"Compared {comparison.compared} records; text moved upstream in "
        f"{len(comparison.text_moved)} (not compared); measured on one side only in "
        f"{len(comparison.not_compared)} (not compared)",
    ]
    for name in SIGNAL_NAMES:
        moved = comparison.moves[name]
        shown = ", ".join(moved[:examples]) + (" …" if len(moved) > examples else "")
        lines.append(f"  {name:<12} moved in {len(moved)}" + (f": {shown}" if moved else ""))
    return lines


# ---- Commands ----


def _client(email: str) -> Any:
    """An httpx client carrying bmlib's own identification."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - the dev extra installs it
        raise SystemExit(f"this command needs httpx ({exc}); install bmlib[all,dev]") from exc

    from bmlib._user_agent import user_agent

    return httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": user_agent(email, httpx.__version__)}
    )


def draw_abstracts(
    client: Any,
    pace: Callable[[str], None],
    per_stratum: int,
    seed: int,
    draw: str,
    strata: list[Stratum] | None = None,
) -> dict[str, Any]:
    """Draw every stratum and measure every drawn record; return the corpus."""
    stratum_rows: list[dict[str, Any]] = []
    for stratum in strata if strata is not None else all_strata():
        row: dict[str, Any] = {"publication_type": stratum.publication_type, "year": stratum.year}
        try:
            if draw == "relevance":
                population = relevance_page(client, stratum, per_stratum, pace)
                chosen = population.pmids
            else:
                population, walks = walk_until_reconciled(client, stratum, pace)
                row["walks"] = walks
                chosen = draw_sample(population.pmids, per_stratum, seed, stratum)
        except RequestFailedError as exc:
            print(f"  draw {stratum.key}: {exc}", file=sys.stderr)
            stratum_rows.append({**row, "status": "failed", "reason": str(exc), "pmids": []})
            continue
        print(f"  draw {stratum.key}: {len(chosen)} of {population.hit_count}", file=sys.stderr)
        stratum_rows.append(
            {
                **row,
                "status": "ok",
                "population": population.hit_count,
                "population_sha256": population.digest if draw == "random" else None,
                "pmids": chosen,
            }
        )
    wanted = sorted({p for row in stratum_rows for p in row["pmids"]}, key=int)
    fetched = fetch_abstracts(client, wanted, pace)
    return {
        "header": corpus_header(
            "abstracts",
            source=SEARCH_URL,
            query_template=QUERY_TEMPLATE,
            draw=draw,
            seed=seed if draw == "random" else None,
            per_stratum=per_stratum,
            drawn_on=date.today().isoformat(),
        ),
        "strata": stratum_rows,
        "records": {pmid: record_row(fetched[pmid]) for pmid in wanted},
    }


def measure_bundle(path: Path) -> dict[str, Any]:
    """Measure every article of a served bundle above the length threshold."""
    records: dict[str, Any] = {}
    articles = 0
    for ident, xml in bundle_articles(path):
        articles += 1
        text = article_text(xml)
        if len(text) < MIN_FULLTEXT_CHARS:
            continue
        records[ident] = {
            "status": MEASURED_STATUS,
            "text_sha256": text_digest(text),
            "text_chars": len(text),
            "signals": measure({"full_text": text}).to_dict(),
        }
    header = corpus_header(
        "fulltext", artifact=path.name, articles=articles, min_chars=MIN_FULLTEXT_CHARS
    )
    return {"header": header, "records": records}


def _same_path(a: Path, b: Path) -> bool:
    """Whether two paths name one file, resolved — raw equality let ``$PWD/…`` through (#166)."""
    return a.resolve() == b.resolve()


def cmd_abstracts(args: argparse.Namespace) -> int:
    """Draw and measure the abstract population."""
    if args.draw == "relevance" and _same_path(args.output, DEFAULT_ABSTRACTS_OUTPUT):
        print("a relevance draw must name its own -o; the default path is the random corpus")
        return 2
    pace = _make_pacer(args.per_host_interval)
    with _client(args.email) as client:
        corpus = draw_abstracts(client, pace, args.per_stratum, args.seed, args.draw)
    lines, reportable = report_abstracts(corpus)
    print("\n".join(lines))
    written = write_corpus(corpus, args.output, reportable)
    print(f"\nwrote {written}")
    return 0 if reportable else 1


def cmd_remeasure(args: argparse.Namespace) -> int:
    """Re-fetch a corpus's records, measure them with the current code, and diff."""
    corpus = load_corpus(args.corpus)
    if corpus["header"].get("population") != "abstracts":
        print(f"{args.corpus} is not an abstract corpus; re-run `fulltext --baseline` instead")
        return 2
    pace = _make_pacer(args.per_host_interval)
    pmids = sorted(corpus["records"], key=int)
    with _client(args.email) as client:
        fetched = fetch_abstracts(client, pmids, pace)
    fresh = {pmid: record_row(fetched[pmid]) for pmid in pmids}
    comparison = compare(corpus["records"], fresh)
    print("\n".join(report_comparison(comparison)))
    updated = {
        **corpus,
        "header": {
            **corpus["header"],
            "extractors_sha256": extractors_digest(),
            "measured_on": date.today().isoformat(),
        },
        "records": fresh,
    }
    lines, reportable = report_abstracts(updated)
    print("\n" + "\n".join(lines))
    failed = sum(row["status"] == "failed" for row in fresh.values())
    if args.write and failed:
        # A `failed` lookup is this run's network, not the population: writing
        # it would replace a measured row with a hole, and a few at a time stay
        # under the threshold, so the committed corpus would thin out silently.
        # `absent` and `no-abstract` are Europe PMC's answers and are written.
        print(f"\nnot written: {failed} lookups failed; re-run before --write")
        return 1
    if args.write:
        print(f"\nwrote {write_corpus(updated, args.corpus, reportable)}")
    return 0 if reportable else 1


def cmd_fulltext(args: argparse.Namespace) -> int:
    """Measure a served bundle; with ``--baseline``, diff against the committed corpus."""
    corpus = measure_bundle(args.bundle)
    print("\n".join(report_fulltext(corpus)))
    if args.baseline:
        baseline = load_corpus(args.output)
        if baseline["header"].get("artifact") != args.bundle.name:
            print(
                f"\n{args.output} measured {baseline['header'].get('artifact')}, not "
                f"{args.bundle.name}; nothing to compare"
            )
            return 2
        print("\n" + "\n".join(report_comparison(compare(baseline["records"], corpus["records"]))))
    if args.write:
        print(f"\nwrote {write_corpus(corpus, args.output, True)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Re-print a committed corpus's report, offline."""
    corpus = load_corpus(args.corpus)
    if corpus["header"].get("population") == "fulltext":
        print("\n".join(report_fulltext(corpus)))
        return 0
    lines, reportable = report_abstracts(corpus)
    print("\n".join(lines))
    return 0 if reportable else 1


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def live(p: argparse.ArgumentParser) -> None:
        p.add_argument("--email", required=True, help="Contact address for the User-Agent.")
        p.add_argument("--per-host-interval", type=float, default=PER_HOST_INTERVAL_SECONDS)

    p = sub.add_parser("abstracts", help="Draw and measure the abstract population (live).")
    live(p)
    p.add_argument("--per-stratum", type=int, default=DEFAULT_PER_STRATUM)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--draw", choices=("random", "relevance"), default="random")
    p.add_argument("-o", "--output", type=Path, default=DEFAULT_ABSTRACTS_OUTPUT)
    p.set_defaults(func=cmd_abstracts)

    p = sub.add_parser("remeasure", help="Re-fetch a corpus's records and diff (live).")
    live(p)
    p.add_argument("corpus", type=Path, nargs="?", default=DEFAULT_ABSTRACTS_OUTPUT)
    p.add_argument("--write", action="store_true", help="Rewrite the corpus with the new run.")
    p.set_defaults(func=cmd_remeasure)

    p = sub.add_parser("fulltext", help="Measure a served bundle (offline).")
    p.add_argument("bundle", type=Path)
    p.add_argument("-o", "--output", type=Path, default=DEFAULT_FULLTEXT_OUTPUT)
    p.add_argument("--baseline", action="store_true", help="Diff against the corpus at -o.")
    p.add_argument("--write", action="store_true", help="Write the corpus to -o.")
    p.set_defaults(func=cmd_fulltext)

    p = sub.add_parser("report", help="Re-print a corpus's report (offline).")
    p.add_argument("corpus", type=Path, nargs="?", default=DEFAULT_ABSTRACTS_OUTPUT)
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
