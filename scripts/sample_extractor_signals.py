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
  (Europe PMC's ``pubTypeList`` also carries JATS article types, which no
  label names, and ``PUB_TYPE:`` is a phrase search; see :data:`LABEL_ORDER`.)
* ``fulltext`` — the articles of one named Europe PMC served bundle (the audit
  used ``PMC10030002_PMC10040000.xml.gz``) whose abstract and body, stripped of
  markup, hold at least :data:`MIN_FULLTEXT_CHARS` characters. Offline.

``--publication-type`` draws other PubMed types in the same three years, a
displaced draw that must name its own ``-o``: ``tests/data/extractor_abstracts_review.json``
is ``Review`` (#540), labelled ``review_only`` where no other label applies.

**A random draw, not a relevance page.** The audit took the first page by
Europe PMC relevance, which is an ordering, not a sample (``sample_jats_exhibits.py``'s
lesson: a contiguous block is not a rate). Here each stratum's whole population is enumerated by
cursor and a seeded sample taken from the *sorted* identifiers, so the draw
depends on ``(query, seed, population)`` and not on the order pages arrive in.
``--draw relevance`` re-takes the audit's own first page, which is how this
instrument was validated against PR #370's published figures; it may not be
written to the committed corpus's path.

**Identifiers, not text.** Abstracts are the publishers'. A corpus records each
record's identifier, its publication types, a SHA-256 of the exact text the
extractors read and its length (``text_chars``), and the four signals.
``remeasure`` re-fetches the text for the committed identifiers, runs the
*current* extractors, and prints what moved — separating a record whose text
changed upstream (its hash moved, so the comparison is not of the code) from
one the code moved, and reporting apart a record whose publication types moved,
which moves the confusion table without moving a signal.

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

Run it before changing ``bmlib/quality/extractors.py``, and after; ``--write``
rewrites the committed corpus once the moves are read::

    uv run python scripts/sample_extractor_signals.py remeasure --email you@example.org
    uv run python scripts/sample_extractor_signals.py fulltext \\
        ~/europepmc/packages/PMC10030002_PMC10040000.xml.gz --baseline

``tests/test_extractor_sampler.py`` fails while any committed corpus was
measured by an ``extractors.py`` other than the current one — byte for byte,
so a comment-only edit counts. Every measuring command refuses to run when
``import bmlib`` resolves to another checkout's extractors (a worktree on the
main checkout's venv), which would stamp this file's digest on that code's
output.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import inspect
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

import bmlib.quality.extractors as _extractors_module
from bmlib.quality.extractors import (
    STUDY_TYPE_PRIORITY,
    extract_study_type,
    find_sample_size,
    get_extracted_study_type,
    has_ci_reporting,
    has_power_calculation,
    prepare_extractor_search_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The extractor module in *this* checkout, which the committed corpora are
#: pinned to. Not necessarily the one imported: under a worktree whose venv's
#: editable install points at another checkout, ``import bmlib`` resolves
#: there, and a measurement taken by that code and stamped with this file's
#: digest would pass the pin while measuring something else. The measuring
#: commands refuse that case (:func:`foreign_extractors`).
EXTRACTORS_PATH = REPO_ROOT / "bmlib" / "quality" / "extractors.py"
#: What the study type can be: the extractor's own vocabulary plus its default.
#: Imported rather than restated — it is the extractor's output domain, not a
#: judgement under test, so a restated copy would only refuse a type added later.
STUDY_TYPES = frozenset(STUDY_TYPE_PRIORITY) | {"unknown"}
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
#: Those counts are from that run's stderr, which nothing kept; a stratum row
#: records its walks' faults (``walk_faults``) since, so the next draw's are
#: re-derivable from its corpus. The committed draw predates the field.
WALK_ATTEMPTS = 6
HTTP_TIMEOUT_SECONDS = 60.0
_THROTTLE_STATUSES = frozenset({429, 503})

#: PubMed publication types to one label, first match wins — the audit's order
#: (issue #368). A systematic review or meta-analysis outranks a trial (bare
#: ``Review`` does not, below) because a meta-analysis *of* RCTs
#: carries both types and is not itself one; an RCT outranks the wider trial
#: types because a record carrying both *is* the narrower one, so the more
#: specific type wins. Compared case-folded: the value is PubMed's. Europe
#: PMC's ``pubTypeList`` also carries JATS article types (``research-article``,
#: ``systematic-review``), which no label names, so a record carrying only
#: those is ``other``.
LABEL_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("review", ("Meta-Analysis", "Systematic Review")),
    ("rct", ("Randomized Controlled Trial",)),
    ("non_randomised", ("Controlled Clinical Trial",)),
    ("clinical_trial", ("Clinical Trial",)),
    ("observational", ("Observational Study",)),
    ("case_report", ("Case Reports",)),
    # `Review` and nothing more specific, which is not necessarily narrative
    # (#540). Last, so a "case report and review of the literature" stays a
    # case report, and a review carrying a trial or observational type keeps it.
    ("review_only", ("Review",)),
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
#: query asked for ``HAS_ABSTRACT:y`` — so it is a hole too, and ``no-types``
#: the same contradiction for ``PUB_TYPE:``: a record carrying no publication
#: type has no ground truth, and labelling it ``other`` would put it in the
#: confusion table as PubMed saying so. ``failed`` is this run's own (a request
#: or a malformed answer), the other three are Europe PMC's answers.
UNMEASURED_STATUSES = frozenset({"failed", "absent", "no-abstract", "no-types"})
MEASURED_STATUS = "ok"
#: What a stratum row's ``status`` can be — a second vocabulary, on the same
#: rule: ``ok`` was drawn, ``failed`` could not be, and anything else is refused.
STRATUM_STATUSES = frozenset({"ok", "failed"})


def _is_hole(status: object) -> bool:
    """Whether a row's *status* is a hole, refusing one on neither side of the partition."""
    # Type-checked first: `in frozenset` hashes, and a list status would raise
    # `TypeError` — which reads as this script's defect — for a malformed corpus.
    if isinstance(status, str):
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

    def __post_init__(self) -> None:
        """Refuse a value neither :func:`measure` nor a corpus this script wrote can hold.

        Checked here and not only in :meth:`from_dict`, so a fresh measurement
        is held to it too: an extractor change that starts returning a ``bool``
        or a new spelling fails at the instrument rather than in a corpus.
        A ``bool`` is an ``int`` in Python, so ``sample_size=True`` is refused
        by name rather than read as a sample of one.
        """
        size = self.sample_size
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 1):
            raise ValueError(f"sample_size must be a positive int or None, not {size!r}")
        if not isinstance(self.power, bool) or not isinstance(self.ci, bool):
            raise ValueError(f"power and ci must be booleans, not {self.power!r} and {self.ci!r}")
        if not isinstance(self.study_type, str) or self.study_type not in STUDY_TYPES:
            raise ValueError(f"study_type must be one of the extractor's, not {self.study_type!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise for a corpus row."""
        return {
            "sample_size": self.sample_size,
            "power": self.power,
            "ci": self.ci,
            "study_type": self.study_type,
        }

    @classmethod
    def from_dict(cls, data: object) -> Signals:
        """Read a corpus row's signals, refusing a missing key or a value of the wrong type.

        Every key is required, ``sample_size`` included: read through
        ``.get()``, an absent one would default to ``None`` and so to "no
        sample size found", a finding nobody recorded.
        """
        if not isinstance(data, dict):
            raise ValueError(f"signals must be an object, not {data!r}")
        missing = [name for name in SIGNAL_NAMES if name not in data]
        if missing:
            raise ValueError(f"signals lack {missing}")
        return cls(data["sample_size"], data["power"], data["ci"], data["study_type"])


#: The four signals, in report order; also the keys of a corpus row's ``signals``.
SIGNAL_NAMES = ("sample_size", "power", "ci", "study_type")


def measure(document: dict[str, str]) -> Signals:
    """Run the extractors over *document* exactly as a caller would.

    Raises:
        ValueError: The extractor recorded no study type. It always records
            one, ``unknown`` included, so this is the extractor changing under
            the instrument and is refused rather than read as ``unknown``.
    """
    text = prepare_extractor_search_text(document)
    study_type = get_extracted_study_type(extract_study_type(document))
    if study_type is None:
        raise ValueError("extract_study_type recorded no study type")
    return Signals(
        sample_size=find_sample_size(text),
        power=has_power_calculation(text),
        ci=has_ci_reporting(text),
        study_type=study_type,
    )


def text_digest(text: str) -> str:
    """SHA-256 of *text*, which is what lets ``remeasure`` tell a code move from a text move."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def imported_extractors_path() -> Path:
    """The file ``bmlib.quality.extractors`` was actually imported from."""
    return Path(inspect.getfile(_extractors_module)).resolve()


def foreign_extractors() -> str | None:
    """Why this run would measure code other than this checkout's, or ``None``.

    The corpora are pinned to :data:`EXTRACTORS_PATH`, so a run whose import
    resolved elsewhere would stamp one file's digest on another file's output.
    """
    imported = imported_extractors_path()
    if imported != EXTRACTORS_PATH.resolve():
        return (
            f"bmlib.quality.extractors was imported from {imported}, not this checkout's "
            f"{EXTRACTORS_PATH}; run from this checkout's own environment"
        )
    return None


def extractors_digest(path: Path | None = None) -> str:
    """SHA-256 of the extractor module's source: which code measured a corpus.

    By default, of the file the functions were *imported* from — the code
    that ran — which :func:`foreign_extractors` holds equal to this checkout's.
    The raw bytes, so a comment-only edit counts as a change and forces a
    re-measure — unlike ``test_renderer_version.py``, which normalises tokens.
    The script's own measuring code (:func:`measure`, :func:`article_text`) is
    outside it and is pinned by exact-output tests instead.
    """
    return hashlib.sha256((path or imported_extractors_path()).read_bytes()).hexdigest()


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


def _is_transport_failure(exc: BaseException) -> bool:
    """Whether *exc* is the network failing rather than this script's call being wrong.

    ``httpx.TransportError`` (a timeout, a refused connection, a dropped
    stream) and a bare ``OSError`` are; anything else is the instrument's and
    is re-raised — a ``_BUG_TYPES`` member (``sample_api_failures.py``'s
    ``instrument-`` bucket, #215), an ``httpx.InvalidURL``, an
    ``UnsupportedProtocol``, a ``MemoryError``. Filed as a hole, any of them
    would pass under the threshold as a slightly thinner sample. An allow-list
    rather than ``_BUG_TYPES``' deny-list, so an exception nobody listed is
    loud by default. httpx is imported here, not at the top, so the offline
    commands need none.
    """
    if isinstance(exc, OSError):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - the dev extra installs it
        return False
    return isinstance(exc, httpx.TransportError)


def get_json(client: Any, params: dict[str, Any], pace: Callable[[str], None]) -> dict[str, Any]:
    """One paced search request, retried while throttled, returning the JSON object.

    Raises:
        RequestFailedError: The transport failed, the request answered a status
            other than 200 after its throttle retries, or it answered a body that
            is not a JSON object. Each is a hole in the sample, never a finding.
        Exception: Anything else the request raised, unchanged — it can only be
            this script being wrong about its own call.
    """
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        pace(SEARCH_URL)
        try:
            resp = client.get(SEARCH_URL, params=params)
        except Exception as exc:
            if not _is_transport_failure(exc):
                raise
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


@dataclass(frozen=True)
class Population:
    """What a stratum's draw saw: a whole enumeration, or the relevance draw's first page.

    ``complete`` says which. A complete population holds exactly ``hit_count``
    PMIDs and has a :attr:`digest`; a relevance page is a ranked prefix in the
    remote's order, holds at most ``hit_count``, and names no population.
    Neither repeats a PMID, which would count one record twice in a stratum.
    """

    hit_count: int
    pmids: list[str]
    complete: bool = True

    def __post_init__(self) -> None:
        """Refuse a state no producer here can make."""
        if len(set(self.pmids)) != len(self.pmids):
            raise ValueError(f"{len(self.pmids) - len(set(self.pmids))} PMIDs repeated")
        if self.complete and len(self.pmids) != self.hit_count:
            raise ValueError(f"a complete population of {self.hit_count} holds {len(self.pmids)}")
        if len(self.pmids) > self.hit_count:
            raise ValueError(f"{len(self.pmids)} PMIDs against a count of {self.hit_count}")

    @property
    def digest(self) -> str:
        """SHA-256 of the sorted, comma-joined PMIDs: *which* population was sampled.

        A count alone cannot tell a re-draw from the original when the same
        number of records is a different set; this can, so the draw's claim to
        be a function of ``(query, seed, population)`` is checkable. A relevance
        page has none: it is an ordering, not a population.
        """
        if not self.complete:
            raise ValueError("a relevance page is not a population and has no digest")
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


@dataclass(frozen=True)
class Reconciled:
    """A stratum's population and how the walks got there."""

    population: Population
    walks: int
    #: One line per walk that did not deliver the population once each, in walk
    #: order — what a run would otherwise leave only on stderr.
    faults: list[str]


def walk_until_reconciled(
    client: Any, stratum: Stratum, pace: Callable[[str], None], attempts: int = WALK_ATTEMPTS
) -> Reconciled:
    """Walk a stratum until the union of its walks holds exactly ``hitCount`` PMIDs.

    A clean first walk returns at once. Otherwise each further walk adds what
    it delivered, and the union reconciles when it reaches the count — never
    past it, which would mean the population changed between walks, and never
    against a count that moved, for the same reason. A walk whose request
    failed adds nothing and spends an attempt.

    **A population of zero needs two walks that agree.** One empty answer is
    also what a transient empty response looks like, and a stratum drawn from
    it would print ``nothing to draw`` and exit 0; one stratum is genuinely
    empty (``Observational Study/2006``), so a zero cannot simply be refused.

    Raises:
        RequestFailedError: The count moved between walks, the union overshot
            it, or *attempts* walks left the union short or a zero unconfirmed.
    """
    union: set[str] = set()
    promised: int | None = None
    faults: list[str] = []
    empty_answers = 0
    for walk in range(1, attempts + 1):
        try:
            count, pmids = walk_once(client, stratum, pace)
        except RequestFailedError as exc:
            faults.append(f"walk {walk}: {exc}")
            print(f"  walk {walk} of {stratum.key}: {exc}", file=sys.stderr)
            continue
        if promised is None:
            promised = count
        elif count != promised:
            raise RequestFailedError(f"population moved between walks ({promised} then {count})")
        union.update(pmids)
        fault = _walk_fault(count, pmids)
        if fault is not None:
            faults.append(f"walk {walk}: {fault}")
            print(f"  walk {walk} of {stratum.key}: {fault}", file=sys.stderr)
        if len(union) > promised:
            raise RequestFailedError(f"the walks' union holds {len(union)} of {promised} records")
        if promised == 0:
            empty_answers += 1
            if empty_answers < 2:
                continue
        if len(union) == promised:
            population = Population(hit_count=promised, pmids=sorted(union, key=int))
            return Reconciled(population, walk, faults)
    if promised == 0:
        raise RequestFailedError(f"an empty population was answered once in {attempts} walks")
    held = f"{len(union)} of {promised} records" if promised is not None else "nothing"
    raise RequestFailedError(f"after {attempts} walks the union holds {held}")


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
    repeated = len(pmids) - len(set(pmids))
    if repeated:
        # Kept, it would count one record twice in the stratum's shares.
        raise RequestFailedError(f"the page repeated {repeated} records")
    return Population(hit_count=hit_count, pmids=pmids, complete=False)


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
    """One record's abstract lookup: what came back, or why nothing did.

    Only a measured record carries an abstract, and it carries a non-blank
    one and at least one publication type; ``failed`` and ``absent`` carry
    neither, there being no record to read them from.
    """

    status: str
    abstract: str = ""
    publication_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a state :func:`fetch_abstracts` cannot produce."""
        measured = not _is_hole(self.status)
        if measured != bool(self.abstract.strip()):
            raise ValueError(f"a {self.status!r} lookup with abstract {self.abstract[:40]!r}")
        if measured and not self.publication_types:
            raise ValueError("a measured lookup carries no publication type")
        if self.status in ("failed", "absent") and self.publication_types:
            raise ValueError(f"a {self.status!r} lookup carries publication types")


def _publication_types(record: dict[str, Any]) -> tuple[str, ...] | None:
    """A core record's ``pubTypeList.pubType``, which may be a list or a lone string.

    Returns:
        The types — empty where the record carries none — or ``None`` where
        the field is present and not a list of strings, which is a malformed
        answer and not Europe PMC saying the record has no type.
    """
    if "pubTypeList" not in record:
        return ()
    holder = record["pubTypeList"]
    raw = holder.get("pubType") if isinstance(holder, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(pt, str) for pt in raw):
        return None
    return tuple(raw)


def _read_record(record: dict[str, Any]) -> Fetched:
    """Classify one delivered record, a malformed field being this run's hole."""
    abstract = record.get("abstractText")
    types = _publication_types(record)
    if types is None or not (abstract is None or isinstance(abstract, str)):
        # A malformed answer, not Europe PMC saying there is no abstract or
        # no type: `failed`, which `remeasure --write` refuses to persist.
        return Fetched("failed")
    if abstract is None or not abstract.strip():
        return Fetched("no-abstract", publication_types=types)
    if not types:
        return Fetched("no-types")
    return Fetched(MEASURED_STATUS, abstract, types)


def _page_is_faulty(records: list[dict[str, Any]], chunk: list[str], hit_count: int) -> bool:
    """Whether a lookup page cannot vouch for a PMID it omits.

    It can only where it delivered every record it counted, each of them a
    distinct PMID of this batch. Counted raw, a page of ``[1, 2, 2]`` or
    ``[1, 2, 999]`` for the batch ``[1, 2, 3]`` reads as complete and files 3
    as ``absent`` — Europe PMC's answer, which ``remeasure --write`` writes over
    a measured row. A count past the batch needs no rule of its own: the page
    cannot deliver it without falling short, repeating a record or carrying a
    stray one.
    """
    on_page = [_pmid_of(r) for r in records]
    wanted = set(chunk)
    return (
        any(pmid is None or pmid not in wanted for pmid in on_page)
        or len(set(on_page)) != len(on_page)
        or len(records) < hit_count
    )


def fetch_abstracts(
    client: Any, pmids: list[str], pace: Callable[[str], None], batch: int = LOOKUP_BATCH
) -> dict[str, Fetched]:
    """Look *pmids* up in batches, returning one :class:`Fetched` per PMID.

    A batch whose request failed marks every one of its PMIDs ``failed``, as
    does a faulty page (:func:`_page_is_faulty`) for every PMID it omits, and a
    record whose abstract or types are malformed. A PMID a sound page omits is
    ``absent``; one returned with no abstract text is ``no-abstract``, and one
    with no publication type ``no-types``. All are holes, kept apart so a
    report can say which kind of hole a stratum has.
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
            hit_count = _hit_count(body)
        except RequestFailedError as exc:
            print(f"  lookup of {len(chunk)} PMIDs failed: {exc}", file=sys.stderr)
            fetched.update({pmid: Fetched("failed") for pmid in chunk})
            continue
        by_pmid = {pmid: r for r in records if (pmid := _pmid_of(r)) is not None}
        faulty = _page_is_faulty(records, chunk, hit_count)
        if faulty:
            print(f"  lookup of {len(chunk)} PMIDs: a faulty page", file=sys.stderr)
        for pmid in chunk:
            record = by_pmid.get(pmid)
            if record is None:
                fetched[pmid] = Fetched("failed" if faulty else "absent")
                continue
            fetched[pmid] = _read_record(record)
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
        ValueError: The bundle holds no article, or its ``<article`` openers,
            ``</article>`` closers and the articles split out disagree — which
            means the non-greedy split welded two articles. Raised after the
            last article is yielded where the counts match and the split does
            not (an article opened inside another).
    """
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        text = stream.read()
    openers, closers = len(_ARTICLE_OPENER.findall(text)), text.count("</article>")
    if openers != closers:
        raise ValueError(f"{path.name}: {openers} <article> openers against {closers} closers")
    if not openers:
        raise ValueError(f"{path.name}: no <article> in the bundle")
    seen: Counter[str] = Counter()
    split = 0
    for index, match in enumerate(_ARTICLE.finditer(text)):
        split += 1
        xml = match.group(0)
        found = _PMCID.search(xml)
        ident = f"PMC{found.group(1)}" if found else f"article_{index}"
        seen[ident] += 1
        yield (ident if seen[ident] == 1 else f"{ident}#{seen[ident]}"), xml
    if split != openers:
        raise ValueError(f"{path.name}: {openers} <article> openers split into {split} articles")


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
    # Tags first, then entities: decoded first, a `p &lt; 0.05` becomes a `<`
    # the tag pattern then reads as the start of markup.
    return html.unescape(_TAG.sub(" ", " ".join(parts)))


def has_text_elements(xml: str) -> bool:
    """Whether an article carries an ``<abstract>`` or a ``<body>`` the rule can match.

    An article where neither matches strips to nothing and falls under the
    length threshold like a genuinely short one; counted apart, a rendition
    the patterns stopped matching (a namespace prefix) shows as such.
    """
    return bool(_ABSTRACT.search(xml) or _BODY.search(xml))


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


#: Every key a record row may carry; the last three exactly when it was measured.
_ROW_KEYS = frozenset({"status", "publication_types", "text_sha256", "text_chars", "signals"})
_MEASURED_KEYS = frozenset({"text_sha256", "text_chars", "signals"})


def _check_row(ident: str, row: object) -> None:
    """Refuse a record row :func:`record_row` or :func:`measure_bundle` cannot write."""
    if not isinstance(row, dict):
        raise ValueError(f"record {ident} is not an object")
    if not set(row) <= _ROW_KEYS:
        raise ValueError(f"record {ident} carries {sorted(set(row) - _ROW_KEYS)}")
    measured = not _is_hole(row.get("status"))
    keyed = _MEASURED_KEYS & set(row)
    if keyed != (_MEASURED_KEYS if measured else frozenset()):
        raise ValueError(f"record {ident} is {row['status']!r} with {sorted(keyed)}")
    types = row.get("publication_types", [])
    if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
        raise ValueError(f"record {ident}: publication_types {types!r}")
    if measured:
        Signals.from_dict(row["signals"])


def _check_stratum(stratum: object, records: dict[str, Any]) -> None:
    """Refuse a stratum row :func:`draw_abstracts` cannot write."""
    if not isinstance(stratum, dict) or stratum.get("status") not in STRATUM_STATUSES:
        raise ValueError(f"a stratum row this script does not know: {stratum!r}")
    pmids = stratum.get("pmids")
    if not isinstance(pmids, list) or not all(isinstance(p, str) for p in pmids):
        raise ValueError(f"stratum {stratum.get('publication_type')}: pmids {pmids!r}")
    if len(set(pmids)) != len(pmids) or not set(pmids) <= set(records):
        raise ValueError(f"stratum {stratum.get('publication_type')}: repeated or unrecorded PMIDs")
    if stratum["status"] == "failed" and pmids:
        raise ValueError(f"stratum {stratum.get('publication_type')}: failed, yet drew PMIDs")
    population = stratum.get("population")
    if stratum["status"] == "ok" and (
        isinstance(population, bool) or not isinstance(population, int) or population < len(pmids)
    ):
        raise ValueError(f"stratum {stratum.get('publication_type')}: population {population!r}")


def load_corpus(path: Path) -> dict[str, Any]:
    """Read a corpus, refusing one that is not shaped like this script's output.

    Every row and stratum is checked here, once, so every reader downstream
    (the reports, :func:`compare`) can filter on ``status`` without an unknown
    one quietly reading as unmeasured.
    """
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(corpus, dict) or not isinstance(corpus.get("header"), dict):
        raise ValueError(f"{path}: not a corpus this script wrote (no header)")
    records = corpus.get("records")
    if not isinstance(records, dict):
        raise ValueError(f"{path}: not a corpus this script wrote (no records)")
    try:
        for ident, row in records.items():
            _check_row(ident, row)
        if corpus["header"].get("population") == "abstracts":
            if not isinstance(corpus.get("strata"), list):
                raise ValueError("an abstract corpus carries no strata")
            for stratum in corpus["strata"]:
                _check_stratum(stratum, records)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return corpus


# ---- Reports ----


def _share(k: int, n: int) -> str:
    """``k/n  pct [low-high]`` with a Wilson interval, or a dash over nothing."""
    if n == 0:
        return f"{k}/0  -"
    low, high = wilson(k, n)
    return f"{k}/{n}  {100 * k / n:5.1f}% [{100 * low:.1f}-{100 * high:.1f}]"


def _measured_signals(records: dict[str, Any], ids: list[str]) -> list[Signals]:
    """The signals of every measured record among *ids*, each status held to the partition."""
    return [
        Signals.from_dict(records[i]["signals"]) for i in ids if not _is_hole(records[i]["status"])
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
        key = Stratum(stratum["publication_type"], stratum["year"]).key
        status = stratum["status"]
        if status not in STRATUM_STATUSES:
            raise ValueError(f"a stratum status this script does not know: {status!r}")
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

    drawn = {p for stratum in corpus["strata"] for p in stratum["pmids"]}
    if reportable and not drawn:
        # Every stratum answered and none held a record: what a query Europe
        # PMC stopped understanding looks like, and one stratum is empty in fact.
        reportable = False
        lines += ["", "ERROR — no stratum holds a record; the query matched nothing"]
    if not reportable:
        # Pooling the measured records of an ERROR stratum would print, one
        # section down, the share that stratum was refused.
        lines += ["", "Pooled rows and the confusion: ERROR — a stratum above is unreportable"]
        return lines, reportable
    measured = {i: records[i] for i in sorted(drawn) if not _is_hole(records[i]["status"])}
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
        # Europe PMC's `PUB_TYPE:` is a phrase search, so every stratum draws
        # types the audit's exact labels do not name: `Clinical Trial` brings
        # "Clinical Trial Protocol" and the phase-specific and veterinary trials,
        # `Meta-Analysis` "Network Meta-Analysis", the RCT and observational
        # strata their veterinary types, and `Systematic Review` a JATS
        # `systematic-review` article type. Shown rather than folded in: which
        # label each deserves is not a count (#451).
        shown = ", ".join(f"{t} {c}" for t, c in others.most_common(10))
        lines.append(f"  {'':<15} {'':>5}  ({OTHER_LABEL} records carry: {shown})")
    return lines, reportable


def report_fulltext(corpus: dict[str, Any]) -> list[str]:
    """The full-text corpus's report."""
    header = corpus["header"]
    signals = [Signals.from_dict(r["signals"]) for r in corpus["records"].values()]
    types = Counter(s.study_type for s in signals)
    # Absent from a corpus measured before it was counted, which says so
    # rather than printing a zero nobody measured.
    unmatched = header.get("no_text_elements")
    unmatched_text = "not counted" if unmatched is None else str(unmatched)
    return [
        f"{header['artifact']}: {header['articles']} articles, {len(signals)} with at least "
        f"{header['min_chars']} characters of abstract and body",
        f"  carrying neither an <abstract> nor a <body> the rule matches: {unmatched_text}",
        "",
        *_signal_lines(signals, "  "),
        "",
        "  study type: " + ", ".join(f"{t} {c}" for t, c in types.most_common()),
    ]


# ---- Comparing a fresh measurement with a corpus ----


@dataclass
class Comparison:
    """What moved between a corpus and a fresh measurement of the same records.

    Every identifier lands in exactly one of ``compared``, ``text_moved``,
    ``one_side`` and ``neither_side``; ``moves`` holds members of ``compared``
    only. ``types_moved`` is apart from all four: a record's publication types
    are the confusion's ground truth, and PubMed retro-types records, so a
    re-measure that rewrote them unannounced would move the confusion table
    with every signal reading ``moved in 0``.
    """

    compared: list[str] = field(default_factory=list)
    text_moved: list[str] = field(default_factory=list)
    one_side: list[str] = field(default_factory=list)
    neither_side: list[str] = field(default_factory=list)
    types_moved: list[str] = field(default_factory=list)
    moves: dict[str, list[str]] = field(default_factory=lambda: {n: [] for n in SIGNAL_NAMES})


def _types_of(row: dict[str, Any]) -> list[str]:
    """A row's publication types as a set, so a reordering is not a move."""
    return sorted(set(row.get("publication_types", [])))


def compare(old: dict[str, Any], new: dict[str, Any]) -> Comparison:
    """Diff two record maps keyed by the same identifiers.

    A record is compared only where both sides measured it **and** read the
    same text; one whose hash moved is counted apart, because a move there is
    the remote's and not the code's. One measured on one side only, or on
    neither, is a hole and never a move. The signals are read through
    :meth:`Signals.from_dict`, so a stored ``1`` is refused rather than
    compared equal to a fresh ``True``.
    """
    result = Comparison()
    for ident in sorted(set(old) | set(new)):
        before, after = old.get(ident, {}), new.get(ident, {})
        measured = [not _is_hole(row.get("status", "absent")) for row in (before, after)]
        if not all(measured):
            (result.one_side if any(measured) else result.neither_side).append(ident)
            continue
        if _types_of(before) != _types_of(after):
            result.types_moved.append(ident)
        if before["text_sha256"] != after["text_sha256"]:
            result.text_moved.append(ident)
            continue
        result.compared.append(ident)
        was, now = Signals.from_dict(before["signals"]), Signals.from_dict(after["signals"])
        for name in SIGNAL_NAMES:
            if getattr(was, name) != getattr(now, name):
                result.moves[name].append(ident)
    return result


def _named(ids: list[str], examples: int) -> str:
    """Up to *examples* identifiers, for a reviewer to read."""
    return ", ".join(ids[:examples]) + (" …" if len(ids) > examples else "")


def report_comparison(comparison: Comparison, examples: int = 10) -> list[str]:
    """The diff, with the identifiers that moved so a reviewer can read them."""
    lines = [
        f"Compared {len(comparison.compared)} records. Not compared: text moved upstream "
        f"in {len(comparison.text_moved)}, measured on one side only in "
        f"{len(comparison.one_side)}, on neither side in {len(comparison.neither_side)}",
    ]
    for name in SIGNAL_NAMES:
        moved = comparison.moves[name]
        shown = f": {_named(moved, examples)}" if moved else ""
        lines.append(f"  {name:<12} moved in {len(moved)}{shown}")
    types = comparison.types_moved
    shown = f": {_named(types, examples)}" if types else ""
    lines.append(f"  publication types moved in {len(types)}{shown}")
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
                reconciled = walk_until_reconciled(client, stratum, pace)
                population = reconciled.population
                # Recorded, so a figure about faulty walks is re-derivable from
                # the corpus and not only from one run's stderr.
                row["walks"] = reconciled.walks
                row["walk_faults"] = reconciled.faults
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
                "population_sha256": population.digest if population.complete else None,
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
    unmatched = 0
    for ident, xml in bundle_articles(path):
        articles += 1
        if not has_text_elements(xml):
            unmatched += 1
            continue
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
        "fulltext",
        artifact=path.name,
        articles=articles,
        no_text_elements=unmatched,
        min_chars=MIN_FULLTEXT_CHARS,
    )
    return {"header": header, "records": records}


def _same_path(a: Path, b: Path) -> bool:
    """Whether two paths name one file, resolved — raw equality let ``$PWD/…`` through (#165)."""
    return a.resolve() == b.resolve()


def cmd_abstracts(args: argparse.Namespace) -> int:
    """Draw and measure the abstract population."""
    if args.per_stratum < 1:
        print(f"--per-stratum must be at least 1, not {args.per_stratum}")
        return 2
    displaced = (
        args.publication_type is not None
        or args.draw != "random"
        or args.seed != DEFAULT_SEED
        or args.per_stratum != DEFAULT_PER_STRATUM
    )
    if displaced and _same_path(args.output, DEFAULT_ABSTRACTS_OUTPUT):
        # The default path is the committed draw; any other draw written there
        # replaces it under its own name.
        print(
            "a draw other than the committed one (random, seed "
            f"{DEFAULT_SEED}, {DEFAULT_PER_STRATUM} a stratum, the committed strata) "
            "must name its own -o"
        )
        return 2
    pace = _make_pacer(args.per_host_interval)
    with _client(args.email) as client:
        strata = None
        if args.publication_type is not None:
            # A repeated type would walk and report each of its strata twice.
            types = dict.fromkeys(args.publication_type)
            strata = [Stratum(pt, year) for pt in types for year in YEARS]
        corpus = draw_abstracts(client, pace, args.per_stratum, args.seed, args.draw, strata)
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
    if failed:
        # A `failed` lookup is this run's network, not the population: writing
        # it would replace a measured row with a hole, and a few at a time stay
        # under the threshold, so the committed corpus would thin out silently.
        # `absent`, `no-abstract` and `no-types` are Europe PMC's answers and
        # are written. Without --write the run still fails: "moved in 0" over
        # a thinned set is the verdict this command exists to give.
        print(f"\n{failed} lookups failed; the diff is incomplete — re-run")
        if args.write:
            print("not written")
        return 1
    if args.write:
        print(f"\nwrote {write_corpus(updated, args.corpus, reportable)}")
    return 0 if reportable else 1


def cmd_fulltext(args: argparse.Namespace) -> int:
    """Measure a served bundle; with ``--baseline``, diff against the corpus at ``-o``.

    ``--write`` over an existing corpus diffs against it first, whether or not
    ``--baseline`` was given, and refuses one of another artifact: writing
    another bundle's measurement under the committed name, or restamping the
    digest without printing what moved, is what the pin exists to prevent.
    """
    corpus = measure_bundle(args.bundle)
    print("\n".join(report_fulltext(corpus)))
    if args.baseline or (args.write and args.output.exists()):
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
    p.add_argument(
        "--publication-type",
        action="append",
        help="Draw this PubMed type in every year instead of the committed seven (repeatable).",
    )
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
    if args.command != "report":
        foreign = foreign_extractors()
        if foreign is not None:
            print(foreign)
            return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
