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

## What else it measures, and why on the same draw

Since issue #211 the run also reads the **shape** of every 200 body it gets.
``ProbeOutcome`` carried HTTP statuses and nothing else, so PR #208's claim
that its new coercers move nothing for a well-formed body — *"no draw has seen
these endpoints answer 200 with a non-object"* — was a count of what nobody
had looked for. The shape table reports the top-level JSON type of each served
body and, for every field ``analyzer.py`` actually reads, that field's type
over the bodies in which it could be asked about. The field list is pinned
against the module by an ``ast`` walk
(``TestTheFieldListIsEveryFieldTheAnalyzerReads``) rather than restated: the
list in issue #211's own text was already missing ``source`` on the day it was
written.

Three further populations ride on the same bodies, because each is a decision
blocked on a count and none of them costs a request:

* **How bmlib would address the full text** (issues #207 and #188).
  ``FullTextStatus.NOT_ATTEMPTED`` covers three causes, one of which is a
  record claiming ``inEPMC: Y`` and carrying nothing to address the text by —
  for which the member's documented meaning is false. The ``id-only`` records
  are split by ``source``, which is what tells a preprint's only address from
  a ``MED`` record's bare PMID.
* **Which registration sources answered at all** (issue #204).
  ``trial_registered`` is ``False`` both for a paper with no trial and for one
  bmlib could not look for a trial in.
* **What each results check could ask, and what answered** (issue #206). The
  accession cap is silent, and ``answered`` goes true on the first accession
  that replies, so one reachable *"no results"* outvotes any number of
  unreachable ones.

Each is its own population with its own denominator, reports ERROR rather than
a share when it has none, and carries its own term in the exit code.

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
import xml.etree.ElementTree as ET
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
    _epmc_records,
    _find_trial_ids,
    _json_text,
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


#: What ``analyzer.py`` reads out of a 200 body, as paths from the root.
#:
#: **Derived, not restated.** Issue #211's own field list omits ``source``,
#: which ``_check_europepmc`` has read since PR #208 coerced it before
#: building the full-text URL — so the list was stale on the day it was
#: written, which is the argument for
#: :class:`TestTheFieldListIsEveryFieldTheAnalyzerReads` walking
#: ``analyzer.py`` with ``ast`` rather than for a comment saying "keep these
#: in step". That test is the mechanism; this tuple is only its subject.
#:
#: Two element steps, and the difference is what the analyzer does with the
#: list. ``[*]`` reads **every** element, which is what ``_check_crossref``
#: does with ``message.funder``; ``[0]`` reads the **head**, which is what
#: every ``_epmc_records`` caller does — EuropePMC returns best-match-first,
#: so ``records[0]`` is *this paper* and a second record's shape is one bmlib
#: never sees. One sentinel for both would make the instrument either wider or
#: narrower than the code it measures, which is this repository's standing
#: rule about a counter matching what the code routes.
FIELD_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "crossref": (
        ("message",),
        ("message", "funder"),
        ("message", "funder", "[*]"),
        ("message", "funder", "[*]", "name"),
    ),
    "europepmc_search": (
        ("resultList",),
        ("resultList", "result"),
        ("resultList", "result", "[0]"),
        ("resultList", "result", "[0]", "abstractText"),
        ("resultList", "result", "[0]", "inEPMC"),
        ("resultList", "result", "[0]", "source"),
        ("resultList", "result", "[0]", "pmcid"),
        ("resultList", "result", "[0]", "id"),
        ("resultList", "result", "[0]", "pmid"),
    ),
    "openalex": (
        ("open_access",),
        ("open_access", "is_oa"),
        ("cited_by_count",),
    ),
    "clinicaltrials": (("hasResults",),),
}

#: The step sentinels, named so the walker and the renderer cannot disagree
#: about which strings are steps into a list rather than object keys.
_EVERY_ELEMENT = "[*]"
_HEAD_ELEMENT = "[0]"

#: A key the body does not carry. Distinct from every JSON value, since
#: ``None`` is one — a key present with ``null`` and a key that is not there
#: are different answers, and conflating them is the defect ``_json_object``
#: exists to prevent (``x.get("k", {})`` returns its default only for the
#: second).
_ABSENT = object()


def _kind(value: object) -> str:
    """Name *value*'s JSON type.

    ``bool`` is tested before ``int`` because in Python it **is** one, the
    same trap ``_json_count`` documents at length: read the other way round,
    a ``true`` where a count belongs is counted as a number and the row that
    exists to show a wrong-typed boolean shows nothing at all.

    Args:
        value: A decoded JSON value.

    Returns:
        One of ``object``, ``array``, ``string``, ``number``, ``boolean`` or
        ``null`` — JSON's own vocabulary, so a row reads as the specification
        rather than as Python's type names.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return f"python-{type(value).__name__}"  # pragma: no cover - json decodes nothing else


def render_path(steps: tuple[str, ...]) -> str:
    """Render a path the way a reader of ``analyzer.py`` would write it.

    Args:
        steps: The path, as stored in :data:`FIELD_PATHS`.

    Returns:
        ``message.funder[].name``, ``resultList.result[0].abstractText`` — the
        element step printed so the table itself says whether bmlib reads the
        whole list or only its head.
    """
    rendered = ""
    for step in steps:
        if step == _EVERY_ELEMENT:
            rendered += "[]"
        elif step == _HEAD_ELEMENT:
            rendered += "[0]"
        else:
            rendered += f".{step}" if rendered else step
    return rendered


def _kind_at(body: object, steps: tuple[str, ...]) -> str | None:
    """The kind of what *steps* reaches in *body*, or ``None`` if unreachable.

    ``None`` and ``"absent"`` are different answers and the distinction sets
    the row's denominator. A key the body omits is ``absent`` — bmlib asked
    and got nothing. A field whose **parent** was the wrong type, or an
    element of an empty list, was never reachable at all, so it enters no
    denominator: putting it in one would report a question that could not be
    asked as an answer of "no", which is the conflation every status enum in
    ``transparency/`` exists to undo.

    Args:
        body: The decoded body.
        steps: The path to walk.

    Returns:
        A kind from :func:`_kind`, ``absent``, ``mixed`` where an iterated
        list is not homogeneous, or ``None`` when the path was unreachable.
    """
    current: list[object] = [body]
    for step in steps:
        reached: list[object] = []
        for value in current:
            if step == _EVERY_ELEMENT:
                if isinstance(value, list):
                    reached.extend(value)
            elif step == _HEAD_ELEMENT:
                if isinstance(value, list) and value:
                    reached.append(value[0])
            elif isinstance(value, dict):
                reached.append(value.get(step, _ABSENT))
        if not reached:
            return None
        current = reached
    kinds = {"absent" if value is _ABSENT else _kind(value) for value in current}
    return kinds.pop() if len(kinds) == 1 else "mixed"


#: The address categories for which bmlib *makes* a full-text request. The
#: other three make none, and issue #207 is that ``FullTextStatus`` cannot
#: tell them apart: ``not-claimed`` is an ordinary closed-access paper,
#: ``no-record`` is EuropePMC not knowing the identifier, and
#: ``unaddressable`` is a record claiming ``inEPMC: Y`` and then carrying
#: nothing to address the text by — which the member's own documented meaning
#: (*"EuropePMC's own answer is why"*) contradicts. All three store
#: ``NOT_ATTEMPTED`` today.
ADDRESSED_CATEGORIES = frozenset({"pmcid", "id-only"})


def _addressability(body: object) -> tuple[str, str | None]:
    """How bmlib would address this record's full text, and what source it is.

    ``_epmc_records`` and ``_json_text`` are **imported**, for this script's
    own reason: its subject is the request, so it must decide what bmlib
    decides. The one thing it cannot import is the ``inEPMC == "Y"`` gate,
    which is inline in ``_check_europepmc`` — so that literal is restated and
    ``TestTheAddressCategoryAgreesWithWhatTheAnalyzerDoes`` drives both over
    the same bodies and compares, which is what a restated literal does not
    survive.

    Args:
        body: The decoded EuropePMC search body.

    Returns:
        The category — one of ``no-record``, ``not-claimed``, ``pmcid``,
        ``id-only``, ``unaddressable`` — and the record's own ``source``,
        which is what tells issue #188's two halves apart: a ``PPR``
        accession is the only address a preprint has, while a ``MED``
        record's bare ``id`` is a PMID whose 404 is known before the request
        leaves.
    """
    records = _epmc_records(body)
    if not records:
        return "no-record", None
    record = records[0]
    source = _json_text(record.get("source")) or None
    if record.get("inEPMC") != "Y":
        return "not-claimed", source
    if _json_text(record.get("pmcid")):
        return "pmcid", source
    if _json_text(record.get("id")):
        return "id-only", source
    return "unaddressable", source


@dataclass(frozen=True)
class BodyShape:
    """What a served 200 body looked like, in the terms the analyzer reads it.

    Attributes:
        endpoint: Which of :data:`ENDPOINTS` served it.
        top: The top-level kind — one of :func:`_kind`'s, or ``empty`` for a
            200 carrying no bytes, or ``not-json`` for one whose body will not
            decode. bmlib cannot tell those last two apart (``_request_json``
            logs one line for both), so the instrument is deliberately *finer*
            than the code here: whether any of these endpoints ever serves an
            empty 200 is the question left open at issue #190, and a table
            that folded it into "not JSON" could not answer it.
        fields: ``(rendered path, kind)`` for each of :data:`FIELD_PATHS`'
            entries the body made reachable. A path that was not reachable is
            **absent from this tuple** rather than carrying a kind, so each
            field's denominator is the bodies in which the question could be
            asked.
    """

    endpoint: str
    top: str
    fields: tuple[tuple[str, str], ...] = ()
    addressability: str | None = None
    address_source: str | None = None

    def __post_init__(self) -> None:
        """Refuse a shape carrying a judgement about an endpoint it is not of."""
        if self.endpoint != "europepmc_search" and (self.addressability or self.address_source):
            raise ValueError(
                f"only a EuropePMC record is addressed, and this shape is {self.endpoint!r}"
            )


#: Endpoints whose body is not JSON. ``efetch`` serves XML, so its shape
#: question is not "which JSON type" but "does it parse" — the two branches
#: that end in empty ``_PubMedSignals`` and a WARNING, whose levels issue
#: #193's status draw could not speak to.
_TEXT_ENDPOINTS = frozenset({"pubmed_efetch"})


def _xml_kind(text: str) -> str:
    """Whether *text* is the XML document ``_parse_pubmed_signals`` would read.

    Mirrors that function exactly — ``ET.fromstring`` and ``ET.ParseError`` —
    rather than testing for a root element or a prefix, because what bmlib
    does with a body is the only thing worth counting here.

    Args:
        text: The served body.

    Returns:
        ``empty`` for a 200 carrying nothing, ``not-xml`` for a body that will
        not parse, else ``xml``.
    """
    if not text:
        return "empty"
    try:
        ET.fromstring(text)
    except ET.ParseError:
        return "not-xml"
    return "xml"


def observe_body(endpoint: str, resp: Any) -> BodyShape:
    """Read *resp*'s body for its shape, without judging whether bmlib copes.

    Args:
        endpoint: Which population this body belongs to.
        resp: The served response.

    Returns:
        The shape. A body that will not decode carries no field rows — which
        is faithful, since ``_request_json`` refuses such a body before any
        caller reads a field out of it.

    **One place this is wider than the code, and it is confined.**
    ``_check_crossref`` and ``_check_openalex`` guard on truthiness (``if
    cr:``), so an **empty object** body reaches no field read at all, while
    the walk treats it as a reachable parent whose every key is absent. So a
    first-level field's ``absent`` count is an upper bound. Narrowing it would
    mean restating each caller's own guard here, which is the restated literal
    issue #184 argues against — and the body is visible in its own right,
    since an empty object is the one showing ``object`` at the top with every
    field absent.
    """
    if endpoint in _TEXT_ENDPOINTS:
        return BodyShape(endpoint=endpoint, top=_xml_kind(resp.text))
    try:
        body = resp.json()
    except Exception:
        return BodyShape(endpoint=endpoint, top="empty" if not resp.text else "not-json")
    fields = []
    for steps in FIELD_PATHS.get(endpoint, ()):
        kind = _kind_at(body, steps)
        if kind is not None:
            fields.append((render_path(steps), kind))
    addressability, address_source = (
        _addressability(body) if endpoint == "europepmc_search" else (None, None)
    )
    return BodyShape(
        endpoint=endpoint,
        top=_kind(body),
        fields=tuple(fields),
        addressability=addressability,
        address_source=address_source,
    )


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
    shape: BodyShape | None = None

    def __post_init__(self) -> None:
        """Refuse an outcome that describes no event :func:`probe` can produce."""
        if self.shape is not None and self.shape.endpoint != self.endpoint:
            raise ValueError(
                f"a {self.endpoint!r} outcome carries a shape for {self.shape.endpoint!r}"
            )
        if self.cause is None:
            if self.status != 200:
                raise ValueError(f"a served outcome must carry status 200, not {self.status!r}")
            if not self.measured:
                raise ValueError("a served outcome was measured by definition")
            if self.shape is None:
                raise ValueError(
                    "a served outcome carries a shape; `probe` observes every body it serves"
                )
            return
        if self.shape is not None:
            raise ValueError(f"only a served outcome has a body shape, not {self.cause!r}")
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
        unusable_strata: Strata that answered and still contributed **no**
            record, every one of theirs having been unusable. As absent from
            the sample as a stratum whose page failed, and invisible to
            ``failed_strata``, which tests the page while the loss happens one
            level down: this script's own first run with the shape tables drew
            twenty ``SRC:PMC`` records apiece in two strata, kept none of
            them, and printed *"124 records over 7 strata"* at exit ``0``.
    """

    records: list[DrawnRecord] = field(default_factory=list)
    failed_strata: list[str] = field(default_factory=list)
    unusable_records: int = 0
    unusable_strata: list[str] = field(default_factory=list)


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
        return ProbeOutcome(
            endpoint=endpoint, status=200, cause=None, shape=observe_body(endpoint, resp)
        )
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
        kept_here = 0
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
            kept_here += 1
        if not kept_here:
            # The page answered and the stratum is still absent. Named rather
            # than left to `unusable_records`, which a reader cannot tell a
            # thinned stratum from an emptied one by.
            print(f"  draw {label}: every record was unusable", file=sys.stderr)
            draw.unusable_strata.append(label)
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


def trial_ids_for(record: DrawnRecord, efetch_xml: str | None) -> list[str]:
    """The NCT accessions bmlib would ask ClinicalTrials.gov about for *record*.

    Mirrors ``_check_trial_registration``: PubMed's own ``DataBankList``
    accessions win when the record was fetched, and the abstract heuristic is
    the fallback. Measuring the fallback alone would overstate the 404 rate —
    an accession scraped out of prose is exactly the kind that does not
    resolve — and measuring only the structured ones would understate it.

    Args:
        record: The drawn record.
        efetch_xml: The PubMed record's XML, when the efetch probe served one.

    Returns:
        **Every** accession the record names, uncapped. The cap belongs where
        bmlib applies it (:func:`probe_trials`), not here: applied at both
        ends, the count issue #206 needs — how often a paper carries more
        accessions than bmlib asks about — could never be taken.
    """
    if efetch_xml:
        accessions = list(_parse_pubmed_signals(efetch_xml).trial_accessions)
        if accessions:
            return accessions
    # A module function since issue #202, and one that makes no request of
    # its own — so this reads the drawn record and cannot reach the network,
    # which is what the "no request is made" line above used to have to
    # promise on the caller's behalf.
    return _find_trial_ids({"resultList": {"result": [record.raw]}})


def probe_record(
    client: Any,
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
        record: The drawn record.
        email: The contact address NCBI asks for.
        pace: The per-host pacer.

    Returns:
        One outcome per request that would have been made. A record with no
        DOI contributes nothing to the CrossRef or OpenAlex populations, and a
        record with no PMID nothing to the PubMed one, which is exactly what
        bmlib does with them.
    """
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


@dataclass(frozen=True)
class TrialCheck:
    """What one paper's results check could ask, and what answered — issue #206.

    ``_check_trial_registration`` walks the paper's accessions and sets
    ``answered`` on the **first** one that replies, so a single reachable
    *"no results"* outvotes any number of unreachable ones and the paper
    stores *"Registered trial without posted results"* — issue #194's class of
    false claim about a trial, narrowed by PR #195's tri-state rather than
    removed. And ``MAX_TRIAL_IDS_TO_CHECK`` slices an unbounded list, silently.

    Neither half can be decided without a count, which is what this carries.

    Attributes:
        found: Accessions the record named, **before** the cap.
        probed: Accessions actually asked about — the cap, applied where bmlib
            applies it.
        answered: Of those, how many ClinicalTrials.gov answered.
        unmeasured: Of those, how many were throttled out. A record with one
            enters no verdict denominator: the sampler failed, not the remote.
    """

    found: int
    probed: int
    answered: int
    unmeasured: int

    @property
    def truncated(self) -> bool:
        """Whether bmlib would have dropped accessions the record named."""
        return self.found > self.probed

    @property
    def verdict(self) -> str:
        """``complete``, ``partial``, ``unanswered`` — or ``unmeasured``.

        ``partial`` is the population issue #206 turns on: the check reached
        an answer for some accessions and not others, and the finding bmlib
        stores does not say so.
        """
        if self.unmeasured:
            return "unmeasured"
        if self.answered == self.probed:
            return "complete"
        return "partial" if self.answered else "unanswered"


#: The two endpoints a trial registration can be found through. CrossRef and
#: OpenAlex feed no registration signal, so neither decides issue #204's
#: question, and a record for which they both answered is not thereby one
#: bmlib could look for a trial in.
_REGISTRATION_SOURCES = ("europepmc_search", "pubmed_efetch")


def source_reach(outcomes: list[ProbeOutcome]) -> str:
    """Which registration sources answered for one record — issue #204.

    ``trial_registered`` is ``False`` for a paper with no trial *and* for one
    whose sources never answered. The second is a claim about bmlib wearing
    the clothes of a claim about the paper, and how much it matters is a count
    nobody has taken.

    Args:
        outcomes: One record's probe outcomes, as :func:`probe_record` returns
            them.

    Returns:
        ``both``, ``epmc-only``, ``pubmed-only``, ``neither`` — or
        ``unmeasured`` when a source's probe was throttled out, since the
        sampler failing is not the remote failing.

    An absent PubMed probe counts as a source that did not answer, which is
    right in both directions: no efetch is made without a PMID, and a PMID
    bmlib would otherwise recover from the EuropePMC record is unavailable
    exactly when that search is the one that failed.
    """
    served = set()
    for outcome in outcomes:
        if outcome.endpoint not in _REGISTRATION_SOURCES:
            continue
        if not outcome.measured:
            return "unmeasured"
        if outcome.ok:
            served.add(outcome.endpoint)
    if len(served) == 2:
        return "both"
    if "europepmc_search" in served:
        return "epmc-only"
    if "pubmed_efetch" in served:
        return "pubmed-only"
    return "neither"


def probe_trials(
    client: Any,
    record: DrawnRecord,
    email: str,
    pace: Callable[[str], None],
    population_failures: list[str] | None = None,
    checks: list[TrialCheck] | None = None,
) -> list[ProbeOutcome]:
    """Probe ClinicalTrials.gov for every accession bmlib would ask about.

    The PubMed ``efetch`` this makes is part of **constructing the
    population**, not a probe of it: which accessions bmlib asks about is
    decided by what that record says, exactly as which records exist at all is
    decided by the stratified search page. Counting it here would put a second
    copy of the PubMed population into the table under a different draw.

    Args:
        client: The HTTP client.
        record: A record from the trial-enriched draw.
        email: The contact address NCBI asks for.
        pace: The per-host pacer.
        population_failures: Appended to when the population-building efetch
            does not answer, so :func:`main` can report — and exit non-zero on
            — a trial population that was reshaped by something other than the
            draw. Optional so the function stays callable on its own.
        checks: Appended to with one :class:`TrialCheck` per record that named
            an accession, which is issue #206's population. Nothing is
            appended for a record bmlib would ask nothing about, because
            there is no check to describe.

    **The early exit is not mirrored, deliberately.** bmlib stops at the first
    accession reporting posted results; this asks all of them within the cap,
    because what each *would* answer is the question, and the exit is decided
    by an answer bmlib only has once it has asked. Every request made here is
    still one bmlib could make, and never more than the cap.

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
    found = trial_ids_for(record, efetch_xml)
    for nct_id in found[:MAX_TRIAL_IDS_TO_CHECK]:
        url = CLINICALTRIALS_STUDY_URL.format(nct_id=nct_id)
        pace(url)
        outcomes.append(probe(client, "clinicaltrials", url, {"fields": "hasResults"}))
    if found and checks is not None:
        checks.append(
            TrialCheck(
                found=len(found),
                probed=len(outcomes),
                answered=sum(1 for o in outcomes if o.ok),
                unmeasured=sum(1 for o in outcomes if not o.measured),
            )
        )
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


def _population_reportable(total: int, unmeasured: int) -> bool:
    """Whether a population may be printed as a share rather than as an ERROR.

    One predicate for every table added for issue #211, for the reason
    :func:`is_reportable` gives about its own: the exit code and what was
    printed must be decided by the same rule, or a scheduled run goes green
    over a table that reported nothing.

    Args:
        total: Attempts in the population.
        unmeasured: How many never reached an answer.

    Returns:
        ``False`` for an absent population, or one past
        ``UNMEASURED_SHARE_ERROR_THRESHOLD``.
    """
    if total <= 0:
        return False
    return unmeasured / total <= UNMEASURED_SHARE_ERROR_THRESHOLD


def _shapes_of(outcomes: list[ProbeOutcome]) -> list[BodyShape]:
    """The bodies actually served. A probe that returned none enters no denominator."""
    return [o.shape for o in outcomes if o.shape is not None]


def shapes_reportable(name: str, outcomes: list[ProbeOutcome]) -> bool:
    """Whether *name*'s shape table is a distribution rather than an ERROR.

    Delegates the throttling half to :func:`is_reportable`, so a shape table
    can never report a distribution the status table above it refused.
    """
    return is_reportable(outcomes) and bool(_shapes_of(outcomes))


def summarise_shapes(name: str, outcomes: list[ProbeOutcome]) -> list[str]:
    """Render what one endpoint's served 200 bodies actually looked like.

    Issue #211: every claim about how often PR #208's coercers fire rested on
    *"no draw has seen a non-object body"*, which no instrument here could
    support — ``ProbeOutcome`` carried HTTP statuses and nothing else, so the
    sentence was a count of what nobody looked for.

    Args:
        name: The endpoint.
        outcomes: Its probe outcomes.

    Returns:
        The lines — the top-level kind distribution, then one row per declared
        field with **its own** denominator, since a field is only counted in
        the bodies where it could be asked about. A field no served body could
        be asked is said to have no population rather than omitted: otherwise
        *"never wrong-typed"* and *"never reachable"* print alike, and the
        second is what a mis-declared path looks like.
    """
    if not is_reportable(outcomes):
        return [
            f"{name:<18} ERROR — the status population above was not reportable; "
            "no body shape is reported"
        ]
    shapes = _shapes_of(outcomes)
    if not shapes:
        return [f"{name:<18} ERROR — no body was served; no shape distribution is reported"]
    served = len(shapes)
    lines = [f"{name:<18} {served:>4} bodies served"]
    for kind, count in sorted(Counter(shape.top for shape in shapes).items()):
        lines.append(f"{'':<18}   {kind:<34} {count:>4}   {100 * count / served:5.1f}%")
    for steps in FIELD_PATHS.get(name, ()):
        path = render_path(steps)
        kinds = Counter(k for shape in shapes for observed, k in shape.fields if observed == path)
        askable = sum(kinds.values())
        if not askable:
            lines.append(
                f"{'':<18}   {path:<34}    - NO POPULATION HERE "
                "(no served body could be asked for it)"
            )
            continue
        seen = ", ".join(f"{kind}={count}" for kind, count in sorted(kinds.items()))
        lines.append(f"{'':<18}   {path:<34} {askable:>4}   {seen}")
    return lines


def _addressed_shapes(outcomes: list[ProbeOutcome]) -> list[BodyShape]:
    """The EuropePMC bodies that were categorised — see :func:`_addressability`."""
    return [shape for shape in _shapes_of(outcomes) if shape.addressability]


def addressing_reportable(outcomes: list[ProbeOutcome]) -> bool:
    """Whether the address table has a population at all."""
    return bool(_addressed_shapes(outcomes))


def summarise_addressing(outcomes: list[ProbeOutcome]) -> list[str]:
    """How bmlib would have addressed each record's full text — issues #207, #188.

    Three of the five categories make no request and all three store
    ``FullTextStatus.NOT_ATTEMPTED``, whose documented meaning — *"no request
    was made, and EuropePMC's own answer is why"* — is false for
    ``unaddressable``. Whether that earns a fourth member is what #207 asks,
    and it was filed rather than taken because the population was unmeasured.

    Args:
        outcomes: The EuropePMC probe outcomes.

    Returns:
        The category distribution, with the ``id-only`` records split by the
        source that decides whether their address is real (issue #188).
    """
    shapes = _addressed_shapes(outcomes)
    if not shapes:
        return [f"{'addressing':<18} ERROR — no EuropePMC record was served; nothing to categorise"]
    total = len(shapes)
    asked = sum(1 for s in shapes if s.addressability in ADDRESSED_CATEGORIES)
    lines = [
        f"{'addressing':<18} {total:>4} records categorised; "
        f"a full-text request would be made for {asked} of {total}"
    ]
    for category, count in sorted(Counter(s.addressability for s in shapes).items()):
        lines.append(f"{'':<18}   {category:<34} {count:>4}   {100 * count / total:5.1f}%")
    by_source = Counter(
        s.address_source or "(no source)" for s in shapes if s.addressability == "id-only"
    )
    for source, count in sorted(by_source.items()):
        lines.append(f"{'':<18}     id-only, source {source:<17} {count:>4}")
    return lines


def checks_reportable(checks: list[TrialCheck]) -> bool:
    """Whether the results-check table is a distribution rather than an ERROR."""
    return _population_reportable(len(checks), sum(1 for c in checks if c.verdict == "unmeasured"))


def summarise_trial_checks(checks: list[TrialCheck]) -> list[str]:
    """What a paper's results check could ask, and what answered — issue #206.

    Args:
        checks: One per paper that named an accession.

    Returns:
        The verdict distribution and the truncated share. ``partial`` is the
        row the issue turns on: bmlib stores *"Registered trial without posted
        results"* for such a paper, and the accession that did not answer may
        be the trial that has them.
    """
    if not checks:
        return [f"{'results checks':<18} ERROR — no paper named an accession; nothing to report"]
    if not checks_reportable(checks):
        unmeasured = sum(1 for c in checks if c.verdict == "unmeasured")
        return [
            f"{'results checks':<18} ERROR — {unmeasured}/{len(checks)} checks were throttled; "
            "no distribution is reported"
        ]
    classified = [c for c in checks if c.verdict != "unmeasured"]
    total = len(classified)
    lines = [f"{'results checks':<18} {total:>4} papers with at least one accession"]
    for verdict, count in sorted(Counter(c.verdict for c in classified).items()):
        lines.append(f"{'':<18}   {verdict:<34} {count:>4}   {100 * count / total:5.1f}%")
    truncated = sum(1 for c in classified if c.truncated)
    lines.append(
        f"{'':<18}   {'truncated by the cap':<34} {truncated:>4}   "
        f"{100 * truncated / total:5.1f}%   (MAX_TRIAL_IDS_TO_CHECK = {MAX_TRIAL_IDS_TO_CHECK})"
    )
    return lines


def reach_reportable(verdicts: list[str]) -> bool:
    """Whether the registration-source table is a distribution rather than an ERROR."""
    return _population_reportable(len(verdicts), verdicts.count("unmeasured"))


def summarise_source_reach(verdicts: list[str]) -> list[str]:
    """Which registration sources answered, per record — issue #204.

    Args:
        verdicts: One :func:`source_reach` verdict per drawn record.

    Returns:
        The distribution. ``neither`` is the population the issue asks for:
        those are the records whose ``trial_registered=False`` is a claim
        about bmlib wearing the clothes of a claim about the paper.
    """
    if not verdicts:
        return [f"{'registration':<18} ERROR — no record was probed; nothing to report"]
    if not reach_reportable(verdicts):
        return [
            f"{'registration':<18} ERROR — {verdicts.count('unmeasured')}/{len(verdicts)} "
            "records were throttled; no distribution is reported"
        ]
    classified = [v for v in verdicts if v != "unmeasured"]
    total = len(classified)
    lines = [f"{'registration':<18} {total:>4} records with a source outcome"]
    for verdict, count in sorted(Counter(classified).items()):
        lines.append(f"{'':<18}   {verdict:<34} {count:>4}   {100 * count / total:5.1f}%")
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
    if draw.unusable_strata:
        lines.append(
            f"{'':<18}   ERROR — {len(draw.unusable_strata)} stratum/strata answered and "
            f"contributed no record at all: {', '.join(draw.unusable_strata)}; "
            "the sample is not evenly stratified"
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
    by_endpoint: dict[str, list[ProbeOutcome]] = {name: [] for name in ENDPOINTS}
    #: Requests that build the ClinicalTrials.gov population rather than
    #: measure it, and did not answer. They enter no table and would enter no
    #: exit code either, which is what made them invisible.
    population_failures: list[str] = []
    #: Issue #204's population: which registration sources answered, per
    #: record. Kept per record rather than per endpoint, because "neither
    #: answered" is a joint fact that no endpoint's own table can show.
    reach_verdicts: list[str] = []
    #: Issue #206's: what each paper's results check could ask, and what
    #: answered.
    trial_checks: list[TrialCheck] = []

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
            record_outcomes = probe_record(client, record, args.email, pace)
            reach_verdicts.append(source_reach(record_outcomes))
            for outcome in record_outcomes:
                by_endpoint[outcome.endpoint].append(outcome)
        trial_draw = draw_records(client, args.trial_target, pace, TRIAL_STRATA, TRIAL_QUERY_SUFFIX)
        for index, record in enumerate(trial_draw.records, start=1):
            if index % 10 == 0:
                print(f"  probed {index}/{len(trial_draw.records)} trial records", file=sys.stderr)
            for outcome in probe_trials(
                client, record, args.email, pace, population_failures, trial_checks
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

    print("\nShape of the 200 bodies, in the terms the analyzer reads them\n")
    for name in ENDPOINTS:
        for line in summarise_shapes(name, by_endpoint[name]):
            print(line)

    print("\nWhat the shapes say about the questions blocked on a count\n")
    for line in summarise_addressing(by_endpoint["europepmc_search"]):
        print(line)
    for line in summarise_source_reach(reach_verdicts):
        print(line)
    for line in summarise_trial_checks(trial_checks):
        print(line)

    if population_failures:
        print(
            f"\n{'trial population':<18}   ERROR — {len(population_failures)} "
            "population-building efetch(es) did not answer, so the accessions probed "
            f"below are not the ones bmlib would ask about: {', '.join(population_failures)}"
        )

    reportable = all(is_reportable(by_endpoint[name]) for name in ENDPOINTS)
    # Each table added for issue #211 carries its own term, for the reason
    # `is_reportable` gives about its own: the exit code is judged by what was
    # printed, and every one of these populations can be absent while every
    # status distribution above it is perfectly healthy — a run in which every
    # probe 404s measures no body at all and would otherwise go green.
    shaped = all(shapes_reportable(name, by_endpoint[name]) for name in ENDPOINTS)
    sized = (
        addressing_reportable(by_endpoint["europepmc_search"])
        and reach_reportable(reach_verdicts)
        and checks_reportable(trial_checks)
    )
    drawn = bool(draw.records) and bool(trial_draw.records)
    lost = (
        bool(draw.failed_strata)
        or bool(trial_draw.failed_strata)
        or bool(draw.unusable_strata)
        or bool(trial_draw.unusable_strata)
    )
    return (
        0
        if reportable and shaped and sized and drawn and not lost and not population_failures
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
