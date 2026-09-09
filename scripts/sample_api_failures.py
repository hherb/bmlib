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

"""Measure what the dropped responses in ``bmlib.transparency`` actually are.

``TransparencyAnalyzer`` makes five requests it can throw away —
``_query_crossref``, ``_query_europepmc``, ``_query_pubmed``,
``_query_openalex`` and ``_check_trial_results``. Until issue #193 a non-200
from any of them fell off the end of the method with **no log line at any
level**, so the level is not being raised here, it is being *invented*: there
was nothing to raise.

**A sixth joined them for issue #216**, and it is unlike the five: the
full-text fetch ``_fetch_europepmc_fulltext`` logs on every branch that
produces no document, and its levels are argued at length. (Not *"every
branch"*, which is what this said until PR #219's review — the success return
logs nothing, as a success should.) What it has in common with them is where the
argument comes from — a draw of *"200 live probes stratified by source and
publication year, 81 of the 81 non-200s were 404"* taken by hand, recorded in
a comment, and reproducible by nothing in this repository. Issue #188's remedy
rests on a spot check in the same position (*"the bare ``id`` 404s, three of
three"*), quoted in four files beside a table that could not produce it. So
the address this script categorises is now the address it probes.

That makes this script the evidence for a level rather than a re-check of one.
This repository's rule is that a diagnostic's level is a claim that has to be
measured, and that **the branch it sits on must be no wider than the draw** —
issue #191 was exactly a DEBUG measured on 404s and applied to every status
code. So what is measured here is, per endpoint, the whole status distribution
over identifiers shaped like the ones bmlib is handed, and the levels in
``analyzer.py`` cite these numbers.

**Run it before changing any of those levels.**

    uv run python scripts/sample_api_failures.py --email you@example.org

Exits non-zero if any population printed ``ERROR`` instead of a distribution,
or if the draw lost records it had asked for. **Today that is every run**: the
three ``SRC:PMC`` strata yield records carrying neither a DOI nor a PMID, which
``DrawnRecord`` refuses because ``analyze()`` accepts nothing else, so those
strata are reported as holes. Issue #212 is the choice of population that ends
it — drop those strata, condition each query on the record being analysable, or
page until each contributes its quota — and the red is deliberate until it is
made, because the alternative is a header claiming a source spread the sample
does not have.

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

Four further populations ride on the same draw, because each is a decision
blocked on a count. Three of them cost no request; the fourth costs one per
record that offers a full-text address, and is the reason issue #216 exists:

* **How bmlib would address the full text** (issues #207 and #188).
  ``FullTextStatus.NOT_ATTEMPTED`` covers several causes, one of which is a
  record claiming ``inEPMC: Y`` and carrying nothing to address the text by —
  for which the member's documented meaning is false. Both categories reached
  by falling back to the record's own ``id`` are split by ``source``, which is
  what tells a preprint's only address from a ``MED`` record's bare PMID.
  **And what became of that address**, which is the population that costs the
  request: one per record offering an address, crossed with the category, the
  source, and the ``isOpenAccess`` flag bmlib does not read but issue #188's
  second population turns on.
* **Which registration sources answered at all** (issue #204).
  ``trial_registered`` is ``False`` both for a paper with no trial and for one
  bmlib could not look for a trial in.
* **What each results check could ask, and what answered** (issue #206). The
  accession cap is silent, and ``answered`` goes true on the first accession
  that replies, so one reachable *"no results"* outvotes any number of
  unreachable ones.

Each is its own population with its own denominator, reports ERROR rather than
a share when it has none, and can flip the exit code on its own.

A ``200`` this script cannot read is reported as **this script** being wrong
rather than as the remote's malformed body: ``json`` raises ``ValueError`` for
a body that is not JSON, and anything else is a ``_BUG_TYPES`` member, which
reported as ``not-json`` would print a *valid* body as an undecodable one at
the top of the table the whole run is quoted from. That is ``analyzer.py``'s
own ``_report_swallowed_exception`` split, one layer up.

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
    _EUROPEPMC_ACCESSION_RE,
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

#: The populations, named once. Every table, gate and exit code is keyed on
#: these, so an endpoint added to the module and not to this tuple prints
#: nowhere rather than printing wrongly.
#:
#: **The sixth arrived with issue #216**, and it is not one of the five
#: dropped responses the module docstring opens with: the full-text fetch
#: logs on every branch that produces no document. It is here because its
#: levels rest on a draw taken by hand — *"200 live probes stratified by
#: source and year, 81 of the 81 non-200s were 404"* — that exists in a
#: comment and in no instrument, and
#: because issue #188's remedy rests on *"that address 404s, three of three"*,
#: which is a spot check printed beside a committed table rather than in it.
#: Probing it is one request per record that offers an address.
ENDPOINTS = (
    "crossref",
    "europepmc_search",
    "europepmc_fulltext",
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
#: Records to **request**, spread evenly over the strata — 20 per stratum. 150
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
#: from what is written down (issues #132/#138).
#:
#: **What that rule buys here is the invocation and not the count**, and this
#: comment claimed both until PR #213's review. The 2026-09-08 run requested
#: 180 and kept **124**: two ``SRC:PMC`` strata contributed no analysable
#: record at all and a third contributed four, which is issue #212. So the
#: default is the command the committed numbers were taken at; their
#: *denominator* is 124, and no endpoint but EuropePMC reaches the 150 probes
#: the resolution argument above is written for — CrossRef and OpenAlex saw
#: 74, PubMed 60, ClinicalTrials.gov 55. Nor is the draw itself re-derivable
#: the way `sample_jats_exhibits.py`'s is: EuropePMC's search is live and
#: unseeded, so a re-run samples different records. Settling #212 is what
#: would let the stated target and the measured draw agree again.
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

#: The verdict two different populations use for *"the sampler failed, not the
#: remote"* — :func:`source_reach`'s and :class:`TrialCheck`'s. One constant
#: rather than a literal decided at seven sites across those two vocabularies,
#: where a misspelling in any ``==``/``!=`` would move a published share and
#: redden no test (PR #213's review).
UNMEASURED = "unmeasured"

#: Prefix of the top-level kind recorded when reading a body raised something
#: that is not a decode failure — this script being wrong, never the remote.
#: It is a prefix rather than one kind so the exception's own type name is on
#: the row: "which defect" is the whole content of such a line.
_INSTRUMENT_KIND_PREFIX = "instrument-"

#: The statuses :func:`probe` retries rather than reports. Shared with
#: :meth:`ProbeOutcome.__post_init__` so the two cannot disagree about which
#: statuses mean *the sampler was throttled*: spelled as an inline literal in
#: one place only, an outcome carrying ``status=429`` with an ``http-`` bucket
#: satisfied every clause and put a throttled probe inside the denominator
#: every log level is set from — the exact hazard the ``measured`` clause
#: beside it exists to prevent, reached through a different spelling (PR
#: #213's review).
_THROTTLE_STATUSES = frozenset({429, 503})

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


#: The address categories for which bmlib *makes* a full-text request.
ADDRESSED_CATEGORIES = frozenset({"pmcid", "id-accession"})
#: The categories that offer an address at all, which is what this script
#: *probes* — and it is deliberately not the same question as
#: :data:`ADDRESSED_CATEGORIES`, which is what bmlib *asks* with.
#:
#: They coincided until issue #188, which is why they are separate names:
#: bmlib no longer asks with an identifier it can know will 404, and a table
#: keyed on what bmlib asks would then stop measuring the very thing that
#: licensed the refusal. A guard installed is a guard whose evidence has to
#: stay re-derivable — this repository's rule about a share going stale
#: silently — so the probe follows the *record's offer* and the table says,
#: per category, what became of it. ``id-not-an-address`` is the row that
#: exists to keep issue #188 answerable on every later run.
#:
#: **A literal, not ``ADDRESSED_CATEGORIES | {...}``** (PR #219's review).
#: Written as a derivation it inherits every narrowing of the set it is
#: documented to be independent of: issue #188 narrowed that one and this one
#: had to be widened by hand to compensate, which is the whole argument above
#: relying on someone remembering it. Spelled out, the next narrowing leaves
#: this alone, and the disagreement it would create is loud —
#: ``RecordAddressing.__post_init__`` raises for a category that offers an
#: address and is not probed. ``test_the_two_questions_are_asked_separately``
#: pins that the two sets are not equal.
PROBED_CATEGORIES = frozenset({"pmcid", "id-accession", "id-not-an-address"})
#: The two categories reached by falling back to the record's own ``id``,
#: which is the fallback issue #188 narrowed rather than deleted. Named
#: because the report splits exactly these by ``source`` — that is the field
#: the fallback turns on — and a ``pmcid`` row split the same way would fan
#: one population into three for no question.
#:
#: **This is the set whose next member is lost in silence** (PR #219's
#: review), and the loss is the one this script exists to prevent: a third
#: outcome of :func:`_addressability`'s ``ext_id`` branch — a ``bookid``
#: split is the obvious next one — omitted here loses its ``, source X``
#: suffix and **pools two source populations into one denominator**, at exit
#: 0. It is derivable, being exactly the categories that branch produces, so
#: ``test_the_id_fallback_set_is_every_category_the_id_branch_produces``
#: holds it against the function rather than against a restated list.
_ID_FALLBACK_CATEGORIES = frozenset({"id-accession", "id-not-an-address"})
#: The categories for which it makes none, and issue #207 is that
#: ``FullTextStatus`` cannot tell them apart: ``not-claimed`` is an ordinary
#: closed-access paper, ``no-record`` is EuropePMC answering with no record,
#: ``unaddressable`` is a record claiming ``inEPMC: Y`` and then carrying
#: nothing to address the text by — which the member's own documented meaning
#: (*"EuropePMC's own answer is why"*) contradicts — and since issue #188
#: ``id-not-an-address`` is a record whose only identifier is a PMID or a
#: ``bookid``. All four store ``NOT_ATTEMPTED``, and for the fourth that
#: reading is exact: the record is EuropePMC's answer and it names no
#: accession.
#:
#: **Named as a set rather than left to be the complement**, which is this
#: repository's own rule about ``FullTextStatus.is_refusal``: a category
#: added to :func:`_addressability` and omitted from
#: :data:`ADDRESSED_CATEGORIES` would default to *"no request would be made"*
#: and silently move the headline figure two issues are blocked on.
#: ``test_every_address_category_chooses_a_side`` asserts the partition, so a
#: new member has to choose (PR #213's review). Stated without an ordinal —
#: it read *"a sixth category"* and #188 added the sixth (PR #219's review),
#: which is ``is_refusal``'s own reason for being phrased that way.
UNADDRESSED_CATEGORIES = frozenset(
    {"no-record", "not-claimed", "unaddressable", "id-not-an-address"}
)
#: Every category :func:`_addressability` may return, as a partition.
ALL_ADDRESS_CATEGORIES = ADDRESSED_CATEGORIES | UNADDRESSED_CATEGORIES


@dataclass(frozen=True)
class RecordAddressing:
    """How bmlib would address this record's full text, and what it says about it.

    One object rather than four fields on :class:`BodyShape`, because they are
    four facts about one thing and one population reads all of them together —
    and because the relations between them are then checkable in one place
    instead of being four independent nullable columns that can be combined
    into states no record produces. That is the argument issue #217 makes
    against ``ProbeOutcome.cause``, applied where the fields are new rather
    than to a shape whose construction sites are all already written.

    Attributes:
        category: One of :data:`ALL_ADDRESS_CATEGORIES`.
        source: The record's own ``source``, which is what tells issue #188's
            two halves apart: a ``PPR`` accession is the only address a
            preprint has, while a ``MED`` record's bare ``id`` is a PMID whose
            404 is known before the request leaves.
        accession: What bmlib would interpolate into the full-text URL —
            ``pmcid`` if the record carries one, else its ``id``, which is the
            analyzer's own fallback *with its coercions*: it reads each
            through ``_json_text``, and that is load-bearing (PR #208 added
            it so a mistyped accession could not be interpolated raw).
            ``None`` for a category that offers no address.
        open_access: The record's ``isOpenAccess``, which bmlib does **not**
            read. It is carried because issue #188's own second population
            turns on it — ``inEPMC`` says EuropePMC *holds* the text while
            ``fullTextXML`` serves the open-access subset of it — and it costs
            no request to record. Read it as a cross-tabulation of the same
            probes, never as something the analyzer consults.
    """

    category: str
    source: str | None = None
    accession: str | None = None
    open_access: str | None = None

    def __post_init__(self) -> None:
        """Refuse an addressing that describes no record :func:`_addressability` reads."""
        if self.category not in ALL_ADDRESS_CATEGORIES:
            raise ValueError(f"unknown address category {self.category!r}")
        # Both directions. An accession on a category that offers none would
        # send a probe for a record that offers no address, putting a request
        # into a denominator nothing licensed; a category that offers one and
        # carries none would drop that record out of the table silently, which
        # is the shape of loss `addressing_reportable` exists to refuse.
        #
        # *"offers no address"* and not *"bmlib never addresses"*, which is
        # what this read until PR #219's review — and which argues for the
        # opposite of what the check does, `id-not-an-address` being exactly
        # a record bmlib never addresses and still probed. The two questions
        # are why there are two names; see `PROBED_CATEGORIES`.
        if (self.accession is not None) != (self.category in PROBED_CATEGORIES):
            raise ValueError(
                f"category {self.category!r} disagrees with accession {self.accession!r}"
            )
        # And the relation that decides issue #188, which is derivable from
        # the two fields and so was re-encodable wrongly: `id-accession` is
        # *defined* as an `id` the analyzer's own regex accepts. Without this
        # the test helper built an `id-accession` carrying `"ID-1"` — a state
        # `_addressability` cannot produce, in the row #188 is decided on —
        # and passed (PR #219's review). `ProbeOutcome.__post_init__`'s rule
        # one type up, applied to the fields it is a relation between.
        if self.category in _ID_FALLBACK_CATEGORIES:
            addressable = bool(_EUROPEPMC_ACCESSION_RE.fullmatch(self.accession or ""))
            if addressable != (self.category == "id-accession"):
                raise ValueError(
                    f"category {self.category!r} disagrees with the shape of {self.accession!r}"
                )


def _addressability(body: object) -> RecordAddressing | None:
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
        A :class:`RecordAddressing`, or ``None`` for a body that is **not a
        JSON object**, which is not a category at all.

    **A non-object body is refused rather than categorised** (PR #213's
    review). ``_epmc_records`` funnels its argument through ``_json_object``,
    which answers ``{}`` for anything that is not a ``dict`` — so ``[]``,
    ``["x"]``, ``"s"``, ``7`` and ``true`` all used to come back
    ``no-record``, a category whose documented meaning is that EuropePMC
    answered and held nothing. What bmlib does with such a body is refuse it
    at ``_request_json`` and store ``FullTextStatus.SEARCH_FAILED`` — a
    WARNING and up to 30 unscored points — so the table reported a loud
    failure as a quiet absence, inside the denominator issues #207 and #188
    are decided on. It is refused *here* rather than at the caller because
    this is where the body's type is already being read; the caller then
    treats it exactly as it already treats an undecodable body, which
    ``_request_json`` also answers ``None`` for. The two are the same outcome
    for bmlib and are now the same outcome here.
    """
    if not isinstance(body, dict):
        return None
    records = _epmc_records(body)
    if not records:
        return RecordAddressing("no-record")
    record = records[0]
    rest = {
        "source": _json_text(record.get("source")) or None,
        "open_access": _json_text(record.get("isOpenAccess")) or None,
    }
    if record.get("inEPMC") != "Y":
        return RecordAddressing("not-claimed", **rest)
    pmcid = _json_text(record.get("pmcid"))
    if pmcid:
        return RecordAddressing("pmcid", accession=pmcid, **rest)
    ext_id = _json_text(record.get("id"))
    if ext_id:
        # The one place this script's own rule bites hardest: the accession
        # test is `analyzer.py`'s module constant, so it is **imported**, for
        # the same reason the URLs are. Restating it would put a record in the
        # `id-accession` row that bmlib refuses, or the reverse — which is the
        # drift that made issue #184 survive a release, in the one table
        # issue #188 is decided on. The `inEPMC` gate above cannot be
        # imported, is restated, and is pinned by driving both instead.
        addressable = bool(_EUROPEPMC_ACCESSION_RE.fullmatch(ext_id))
        category = "id-accession" if addressable else "id-not-an-address"
        return RecordAddressing(category, accession=ext_id, **rest)
    return RecordAddressing("unaddressable", **rest)


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
            asked. At most one row per path — built through a ``dict`` in
            :func:`observe_body` so a path repeated in :data:`FIELD_PATHS`
            cannot double that field's denominator in a table whose whole
            subject is that a share is of a denominator.
        addressing: For ``europepmc_search`` alone: how bmlib would address
            the record's full text, or ``None`` where the body was not a JSON
            object and so carries no record to categorise.
    """

    endpoint: str
    top: str
    fields: tuple[tuple[str, str], ...] = ()
    addressing: RecordAddressing | None = None

    def __post_init__(self) -> None:
        """Refuse a shape that describes no body :func:`observe_body` can read."""
        if self.endpoint != "europepmc_search" and self.addressing is not None:
            raise ValueError(
                f"only a EuropePMC record is addressed, and this shape is {self.endpoint!r}"
            )
        # The other direction, and the one that keeps `None` from acquiring a
        # second meaning: `_addressability` returns a category for every JSON
        # *object*, so an uncategorised object body would be dropped by
        # `_addressed_shapes` and shrink issue #207's denominator with no line
        # printed — `FullTextStatus`'s own rule that `None` must mean *not
        # recorded* and never a determinate answer (PR #213's review).
        #
        # **A biconditional and not a half-guard** (PR #219's review). Written
        # as `top == "object" and not addressing` it left the third direction
        # open, so a *non-object* EuropePMC body could carry a category —
        # which is the pre-PR-#213 defect `_addressability` returns `None` for
        # a non-dict specifically to prevent, reachable through this
        # constructor. `is None` rather than falsiness for the module's own
        # reason: `SEARCH_FAILED` exists because the two differ.
        if self.endpoint == "europepmc_search" and (self.addressing is not None) != (
            self.top == "object"
        ):
            raise ValueError(
                "a decoded EuropePMC object body is always categorised and nothing else "
                f"ever is, but {self.top!r} carries {self.addressing!r}"
            )

    @property
    def addressability(self) -> str | None:
        """The address category, or ``None`` where the body carries no record to categorise."""
        return self.addressing.category if self.addressing else None


#: What ``_parse_pubmed_signals`` looks for once the document parses. Restated
#: from ``analyzer.py`` because it is an XPath in the middle of a function and
#: not a module constant; ``test_the_xml_kind_agrees_with_what_the_analyzer_reads``
#: drives both over the same bodies, which is what a restated literal does not
#: survive.
_PUBMED_CITATION_PATH = ".//PubmedArticle/MedlineCitation"


def _xml_kind(text: str) -> str:
    """Whether *text* is the XML document ``_parse_pubmed_signals`` would read.

    Mirrors that function rather than testing for a root element or a prefix,
    because what bmlib does with a body is the only thing worth counting here.

    Args:
        text: The served body.

    Returns:
        ``empty`` for a 200 carrying nothing, ``not-xml`` for a body that will
        not parse, ``no-citation`` for one that parses and carries no
        ``PubmedArticle``, else ``xml``.

    **``no-citation`` is the branch that was missing, and it is the only
    silent one** (PR #213's review). ``_parse_pubmed_signals`` WARNs on a body
    that will not parse and ``_check_pubmed`` WARNs on an empty one — but a
    document that parses and whose ``PubmedArticle/MedlineCitation`` is absent
    returns empty signals with **no line at any level**, so no
    ``<CoiStatement>``, nothing retracted from the COI indicators, and the
    missing-COI downgrade free to fire. Two populations reach it and neither
    is hypothetical: NCBI serves ``<eFetchResult><ERROR>…`` at HTTP 200, and a
    ``<PubmedBookArticle>`` set is declined by name — which is what a
    Bookshelf PMID returns, and issue #188's own finding is that every
    ``id-only`` ``MED`` record in a 150-record spot draw was a Bookshelf
    chapter. Folding those into ``xml`` reported the endpoint as wholly
    healthy over bodies that gave bmlib nothing.
    """
    if not text:
        return "empty"
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "not-xml"
    if root.find(_PUBMED_CITATION_PATH) is None:
        return "no-citation"
    return "xml"


def _fulltext_kind(text: str) -> str:
    """Whether ``fullTextXML`` served a document at all.

    **Two values, and the narrowness is the point.** Everything
    ``_fetch_europepmc_fulltext`` does with a served body past this — the
    truncation test, the markup lex, the nested-region stack — is a
    *judgement* about the document, and this directory's standing rule is that
    an instrument does not import the predicate under test, because a corpus
    labelled by that rule can only confirm it. So the shape recorded here is
    the one distinction the analyzer makes before any judgement: whether bytes
    arrived. How often each refusal fires over live bodies is a real question
    and a different one; it is filed rather than answered here.

    Args:
        text: The served body.

    Returns:
        ``empty`` for a 200 carrying nothing, else ``served``.

    ``empty`` is the population the module records as unmeasured in its own
    comment — *"whether they ever serve one is not measured, and the local
    corpora cannot answer it"* — and it is not idle: such a body used to reach
    the *entirely nested* branch and store a refusal that did not happen
    (issue #190). EuropePMC's own 404 carries ``content-length: 0``, so a 200
    that does the same is at minimum anomalous.
    """
    return "served" if text else "empty"


#: What to read out of a served body that is not JSON, per endpoint.
_BODY_KINDS: dict[str, Callable[[str], str]] = {
    "pubmed_efetch": _xml_kind,
    "europepmc_fulltext": _fulltext_kind,
}

#: Endpoints whose body is not JSON, **derived** from the dispatch above so
#: the two cannot disagree about which bodies :func:`observe_body` decodes —
#: an endpoint in one and not the other either has its XML handed to
#: ``resp.json()`` or reaches a ``KeyError`` in the dispatch.
_TEXT_ENDPOINTS = frozenset(_BODY_KINDS)


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
    ``_check_crossref``, ``_check_europepmc`` and ``_check_openalex`` are each
    reached under a truthiness guard — ``if cr:`` / ``elif epmc:`` / ``if
    oa:``, the three ``_request_json``'s own docstring names — so an **empty
    object** body reaches no field read at all, while the walk treats it as a
    reachable parent whose every key is absent. So a first-level field's
    ``absent`` count is an upper bound. Narrowing it would mean restating each
    caller's own guard here, which is the restated literal issue #184 argues
    against — and the body is visible in its own right, since an empty object
    is the one showing ``object`` at the top with every field absent. *The
    EuropePMC guard was omitted from this list while it carries nine of the
    seventeen field rows* (PR #213's review).

    **A decode that fails for bmlib's reasons is not a claim about the
    remote.** ``json.JSONDecodeError`` and ``UnicodeDecodeError`` are both
    ``ValueError`` and both say the body is not JSON; anything else — a
    response object this script is wrong about, carrying no ``.json`` at all —
    is a ``_BUG_TYPES`` member, and reporting it as ``not-json`` would print a
    *valid* JSON body as an undecodable one, at the top of the table this
    whole run is quoted from. That is ``analyzer.py``'s own
    ``_report_swallowed_exception`` defect reproduced one layer up, so it is
    split the same way: the remote's malformed body keeps its kind, and the
    instrument's own defect gets a kind of its own, an ERROR line naming the
    exception, and a term in the exit code (PR #213's review).
    """
    if endpoint in _TEXT_ENDPOINTS:
        return BodyShape(endpoint=endpoint, top=_BODY_KINDS[endpoint](resp.text))
    try:
        body = resp.json()
    except ValueError:
        return BodyShape(endpoint=endpoint, top="empty" if not resp.text else "not-json")
    except Exception as exc:
        print(
            f"  {endpoint}: ERROR — reading the body raised {type(exc).__name__}: {exc}; "
            "that is this script being wrong, not the remote",
            file=sys.stderr,
        )
        return BodyShape(endpoint=endpoint, top=f"{_INSTRUMENT_KIND_PREFIX}{type(exc).__name__}")
    # A dict, so a path repeated in `FIELD_PATHS` yields one row rather than
    # two — `summarise_shapes` sums the rows to get that field's denominator,
    # so a duplicate would double it silently.
    fields: dict[str, str] = {}
    for steps in FIELD_PATHS.get(endpoint, ()):
        kind = _kind_at(body, steps)
        if kind is not None:
            fields[render_path(steps)] = kind
    return BodyShape(
        endpoint=endpoint,
        top=_kind(body),
        fields=tuple(fields.items()),
        addressing=_addressability(body) if endpoint == "europepmc_search" else None,
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
        # Keyed on the status as well as on the bucket. `probe` retries every
        # `_THROTTLE_STATUSES` member and can only ever report one as
        # `unmeasured-`, so an `http-429` describes no event it produces —
        # and it is the one spelling that slipped past the clause above,
        # putting a throttled probe into the failure share as a *failure*.
        if self.status in _THROTTLE_STATUSES and self.measured:
            raise ValueError(f"status {self.status!r} is retried, so it is never measured")

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
        if resp.status_code in _THROTTLE_STATUSES:
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
        The :class:`Draw`. It holds at least *target* records only when every
        stratum answers **and every record it returns carries an identifier**
        — the per-stratum count is rounded up, so on that path the total may
        exceed *target* by up to ``len(strata) - 1``.

        Neither half is idle. A stratum whose page did not answer contributes
        no records and is named in ``failed_strata``, never back-filled from
        another stratum, which would re-weight the sample without saying so.
        And a page that *did* answer may still contribute nothing:
        :class:`DrawnRecord` refuses a record carrying neither a DOI nor a
        PMID, so the draw can be far short of *target* with ``failed_strata``
        empty — which is what the 2026-09-08 run did, keeping 124 of 180
        (issue #212). Those are counted in ``unusable_records`` and, where a
        stratum is emptied outright, named in ``unusable_strata``. The
        contract said "when every stratum answers" while that run had every
        stratum answer (PR #213's review).
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
        # `_epmc_records`, not `.get("resultList", {}).get("result", [])`.
        # Two defects in that one line, both of them the ones this instrument
        # exists to measure in `analyzer.py` (PR #213's review). A key present
        # with `null` returns the *value*, not the default, so `resultList:
        # null` raised `AttributeError` into the handler below and printed
        # "unreadable body" — a false claim about a body that decoded
        # perfectly, which is exactly the rule `_INDICATOR_FUNDERS_NOT_READABLE`
        # states one module over. And a wrong-typed *element* was never
        # guarded at all: the record loop is outside the `try`, so `result` as
        # a string raised out of `main` and discarded every paced request the
        # run had already spent. The imported reader answers `[]` for both,
        # and it is what the analyzer reads the same envelope with — it
        # truncates at the first non-object rather than filtering, so a
        # malformed tail shows as a thinner stratum in the report's own
        # per-stratum counts rather than as a different article.
        try:
            results = _epmc_records(resp.json())
        except ValueError as exc:
            print(f"  draw {label}: body is not JSON ({exc})", file=sys.stderr)
            draw.failed_strata.append(label)
            continue
        except Exception as exc:
            print(
                f"  draw {label}: ERROR — reading the body raised {type(exc).__name__}: {exc}; "
                "that is this script being wrong, not the remote",
                file=sys.stderr,
            )
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
            # Coerced, for the reason PR #208 coerced `source` and the
            # accession one module over: a mistyped identifier is truthy, and
            # it would be interpolated into `DOI:"{...}"` or `EXT_ID:{...}`
            # and probed — a request bmlib would never make entering the
            # denominator that sets that endpoint's log level.
            doi = _json_text(result.get("doi")) or None
            pmid = _json_text(result.get("pmid")) or None
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


def _fulltext_url(accession: str) -> str:
    """The full-text URL ``_fetch_europepmc_fulltext`` builds for *accession*.

    Restated from an f-string in the middle of that method rather than
    imported, because it is not a module constant there — the *base* is, and
    that is imported. ``TestTheSamplerProbesWhatTheAnalyzerRequests`` drives
    both and diffs, which is what a restated literal does not survive: issue
    #184 was two spellings of this very URL drifting apart for a release.
    """
    return f"{EUROPEPMC_REST_BASE}/{accession}/fullTextXML"


@dataclass(frozen=True)
class AddressProbe:
    """One full-text address, and what EuropePMC did with it — issues #216, #188.

    The address table said how bmlib *would* address each record and stopped
    there: nothing in this script ever built the URL, so *"the bare ``id``
    404s, three of three"* — the finding issue #188's remedy rests on — was a
    spot check quoted beside a committed table rather than a row in it, and
    the 404's DEBUG level rested on a hand-taken draw in the same position.

    Attributes:
        addressing: The record's own answer, carried whole so the outcome can
            be crossed with the category, the source and ``isOpenAccess``
            without any of the three being re-derived here.
        outcome: What the probe produced.

    A record enters this population exactly when it offers an address
    (:data:`PROBED_CATEGORIES`), which is deliberately not *"when bmlib would
    ask"*: see that constant.
    """

    addressing: RecordAddressing
    outcome: ProbeOutcome

    def __post_init__(self) -> None:
        """Refuse a probe of a record that offers no address."""
        if self.addressing.accession is None:
            raise ValueError(f"{self.addressing.category!r} offers no address to probe")
        if self.outcome.endpoint != "europepmc_fulltext":
            raise ValueError(f"a full-text probe is not a {self.outcome.endpoint!r} one")

    @property
    def is_unmeasured(self) -> bool:
        """Whether the probe never reached an answer, so it enters no denominator."""
        return not self.outcome.measured

    @property
    def served(self) -> bool:
        """Whether EuropePMC served a *document* for this address.

        **Not ``outcome.ok``**, which is "no cause", i.e. HTTP 200 (PR #219's
        review). :func:`_fulltext_kind` exists precisely to split a 200 into
        ``served`` and ``empty``, and issue #190's whole finding is that *a
        200 alone is not "a document arrived"* — an empty body used to store
        a refusal that did not happen. Reading `ok` here let the address
        table call a probe served while the shape table two rows above called
        the same probe empty.

        Nothing published moves: the 2026-09-09 draw measured 0 empty of 6
        served. That is the point at which to fix it — a figure that changes
        meaning without changing is this repository's own scar, and here the
        change is still free.
        """
        return (
            self.outcome.ok
            and self.outcome.shape is not None
            and self.outcome.shape.top == "served"
        )


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
    address_probes: list[AddressProbe],
) -> list[ProbeOutcome]:
    """Make the per-record requests ``analyze()`` would make, and classify each.

    **With one deliberate exception, and it is issue #216's whole point.** The
    full-text probe follows :data:`PROBED_CATEGORIES`, so an ``id-not-an-address``
    record is probed here and is *not* requested by ``analyze()`` since issue
    #188. A table keyed on what bmlib asks would stop measuring the thing that
    licensed the refusal the moment the refusal landed, so the probe follows
    the record's own offer. This docstring promised the analyzer's own set
    until PR #219's review, which is the half issue #188 made false.

    ClinicalTrials.gov is deliberately **not** among them: its population is
    drawn separately (:data:`TRIAL_STRATA`) and probed by :func:`probe_trials`,
    so the two draws cannot pool into one denominator.

    Args:
        client: The HTTP client.
        record: The drawn record.
        email: The contact address NCBI asks for.
        pace: The per-host pacer.
        address_probes: Collected into, one entry per record that offers a
            full-text address. An out-parameter for :func:`probe_trials`'
            reason: the population is per *record*, and no endpoint's own
            table can hold a row keyed on the record's category.

    Returns:
        One outcome per request, including the full-text probe bmlib may
        refuse to make. A record with no DOI contributes nothing to the
        CrossRef or OpenAlex populations, and a record with no PMID nothing to
        the PubMed one, which is exactly what bmlib does with them.
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
    search = probe(client, "europepmc_search", search_url, _search_params(query))
    outcomes.append(search)

    # The address the search body offered, probed — issue #216. It is read off
    # *this* probe's body rather than off the draw page, because the
    # single-record lookup is the body `analyze()` reads and the two can
    # disagree; and a probe that reached no body offers nothing, which is why
    # this is guarded on the shape rather than on the record.
    addressing = search.shape.addressing if search.shape else None
    if addressing is not None and addressing.accession is not None:
        url = _fulltext_url(addressing.accession)
        pace(url)
        outcome = probe(client, "europepmc_fulltext", url)
        outcomes.append(outcome)
        address_probes.append(AddressProbe(addressing=addressing, outcome=outcome))

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
            applies it. bmlib may ask **fewer**, stopping at the first
            accession reporting posted results; that early exit is
            deliberately not mirrored, for the reason :func:`probe_trials`
            gives.
        answered: Of those, how many ClinicalTrials.gov gave bmlib an answer
            for — which is **not** how many served HTTP 200; see
            :func:`trial_answered`.
        unmeasured: Of those, how many were throttled out. A record with one
            enters no verdict denominator: the sampler failed, not the remote.
    """

    found: int
    probed: int
    answered: int
    unmeasured: int

    def __post_init__(self) -> None:
        """Refuse counters that describe no check :func:`probe_trials` can run.

        The ordering ``found >= probed >= answered + unmeasured >= 0`` is the
        whole meaning of the type and lived only in the prose above. The same
        argument was accepted for :class:`ProbeOutcome` in this PR — where two
        impossible outcomes were being built by test helpers — so declining it
        here would be inconsistent rather than pragmatic (PR #213's review).
        """
        if min(self.found, self.probed, self.answered, self.unmeasured) < 0:
            raise ValueError("a check counts requests, so no count is negative")
        if self.answered + self.unmeasured > self.probed:
            raise ValueError(
                f"{self.answered} answered + {self.unmeasured} unmeasured exceeds "
                f"{self.probed} probed"
            )
        if self.probed > self.found:
            raise ValueError(f"{self.probed} probed exceeds the {self.found} accessions found")
        if self.probed > MAX_TRIAL_IDS_TO_CHECK:
            raise ValueError(
                f"{self.probed} probed exceeds bmlib's own cap of {MAX_TRIAL_IDS_TO_CHECK}"
            )

    @property
    def truncated(self) -> bool:
        """Whether bmlib would have dropped accessions the record named."""
        return self.found > self.probed

    @property
    def is_unmeasured(self) -> bool:
        """Whether the sampler, rather than the remote, is why this has no verdict.

        Asked of the type rather than by comparing :attr:`verdict` against a
        string literal, which four call sites were doing across two unrelated
        vocabularies: a misspelling at any one of them silently moved a
        published share — a ``*_reportable`` predicate excluding the throttled
        checks while its own summariser divided by a denominator that included
        them, which is the disagreement those predicates exist to prevent (PR
        #213's review).
        """
        return bool(self.unmeasured)

    @property
    def verdict(self) -> str:
        """``complete``, ``partial``, ``unanswered`` — or ``unmeasured``.

        ``partial`` is the population issue #206 turns on: the check reached
        an answer for some accessions and not others, and the finding bmlib
        stores does not say so.
        """
        if self.is_unmeasured:
            return UNMEASURED
        if self.answered == self.probed:
            return "complete"
        return "partial" if self.answered else "unanswered"


#: What ``hasResults`` may be for ``_check_trial_results`` to still have given
#: bmlib an answer. ``_json_bool`` answers ``None`` for every other type, and
#: an **absent** key (or one present with ``null``) keeps the ``False`` that
#: `test_missing_has_results_is_false` has pinned since before the tri-state
#: existed — issue #210, which this sampler's own first run settled at 0 of 55.
_ANSWERING_HAS_RESULTS_KINDS = frozenset({"boolean", "absent", "null"})


def trial_answered(outcome: ProbeOutcome) -> bool:
    """Whether ``_check_trial_results`` would have returned an answer for *outcome*.

    **Not ``outcome.ok``**, which is HTTP 200 (PR #213's review). bmlib sets
    its own ``answered`` flag only when that method returns non-``None``, and
    since PR #208 routed the value through ``_json_bool`` it returns ``None``
    for a 200 whose body is not an object *and* for one whose ``hasResults``
    is wrong-typed. Counting 200s instead inflated ``complete`` and deflated
    ``partial`` and ``unanswered`` — the two rows issue #206 turns on, and the
    ones that decide whether *"Registered trial without posted results"* is
    being stored over an accession nobody answered. It is also precisely the
    wrong-typed-boolean shape ``_json_bool``'s docstring says no contract net
    can see, which makes this the one population that ought to see it.

    Args:
        outcome: One ClinicalTrials.gov probe outcome.

    Returns:
        Whether bmlib would have had an answer. Read off the shape already
        recorded, so it costs no extra request.
    """
    if outcome.shape is None or outcome.shape.top != "object":
        return False
    kinds = dict(outcome.shape.fields)
    return kinds.get("hasResults", "absent") in _ANSWERING_HAS_RESULTS_KINDS


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
            return UNMEASURED
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
                answered=sum(1 for o in outcomes if trial_answered(o)),
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
    # narrower thing than it counted — and a label is a claim about what was
    # counted, which is this repository's own rule (PR #195's review). The
    # note that stood here, "every count is 0 today, so it has never printed
    # wrongly", was made false by the run this instrument was written for: the
    # 2026-09-08 draw recorded one ClinicalTrials.gov 404 in 56 probes.
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


def instrument_defects(outcomes: list[ProbeOutcome]) -> int:
    """How many served bodies this script was itself unable to read.

    See :func:`observe_body` — a ``_BUG_TYPES`` member raised while decoding is
    recorded under its own kind rather than as the remote's malformed body,
    and it is counted here so it reaches the exit code instead of printing as
    one row among the findings.
    """
    return sum(1 for s in _shapes_of(outcomes) if s.top.startswith(_INSTRUMENT_KIND_PREFIX))


#: Endpoints at which a probe that served no body is a **measurement** rather
#: than a hole in the shape population, so :func:`shapes_reportable`'s second
#: rule does not apply to them.
#:
#: One member, and it earned its place from a live run rather than from
#: review. That rule reads *"for the status table a non-200 is the
#: measurement; for the shape table it is a probe that reached no body, which
#: is exactly as uninformative as a throttled one"* — true of the five
#: endpoints it was written for, where a non-200 is close to unheard of.
#: ``europepmc_fulltext`` is the one whose gate is deliberately wider than
#: what it serves. Measured on 2026-09-09: 46 of 52 probes 404'd, and the
#: shape table nevertheless reported ERROR and flipped the exit code on a
#: clean run.
#:
#: **Two mechanisms produce that 46, and only one of them is the gate** (PR
#: #219's review). ``inEPMC`` says EuropePMC *holds* the text while this
#: endpoint serves the open-access subset, which is what puts 3 of the 9
#: accession addresses at 404; the other 43 are ``id-not-an-address`` probes
#: this script deliberately keeps making after issue #188 stopped bmlib
#: making them, and they 404 because the URL addresses nothing. The
#: exception is right either way — a probe that reached no body is this
#: endpoint's own answer — but the majority is the second mechanism, and
#: attributing it to the first would licence *"a 404 is the ordinary majority
#: outcome"* in contexts where it is not. See :func:`summarise_addresses`,
#: which reports the two separately for exactly this reason.
#:
#: What it does *not* buy is a free pass: the throttling rule still applies,
#: an endpoint that served nothing at all is still an ERROR, and every row
#: carries its Wilson interval — so a distribution over six bodies prints as
#: one. It **is** the ``bool(shapes)`` floor PR #213 removed, restored for one
#: endpoint and for no other; the interval is what makes it safe here and its
#: absence is what made it unsafe there. (This said the interval stopped it
#: *"reading like"* that floor, which denied a resemblance the code makes
#: literal.)
_ENDPOINTS_WHOSE_SHAPE_IS_OVER_SERVED_BODIES = frozenset({"europepmc_fulltext"})


def shapes_reportable(endpoint: str, outcomes: list[ProbeOutcome]) -> bool:
    """Whether this endpoint's shape table is a distribution rather than an ERROR.

    Two rules, both of them the module's own, applied to the population the
    shape table actually has (PR #213's review) — and the second is skipped
    for :data:`_ENDPOINTS_WHOSE_SHAPE_IS_OVER_SERVED_BODIES`, where a probe
    that served no body is this endpoint's own answer rather than a gap.

    The first is :func:`is_reportable`'s, delegated so a shape table can never
    report a distribution the status table above it refused.

    The second is that rule read once more, with the right denominator: *"an
    attempt that never reached an answer enters no denominator, and a
    population past ``UNMEASURED_SHARE_ERROR_THRESHOLD`` reports ERROR rather
    than a share"*. For the **status** table a non-200 is the measurement; for
    the **shape** table it is a probe that reached no body, which is exactly
    as uninformative as a throttled one. ``bool(shapes)`` was the whole floor,
    so one served body out of 180 printed ``100.0%`` — with no interval, in
    the table whose figures are quoted as *"0 non-object at all five
    endpoints"* — while the status table above it honestly reported 99.4% not
    served.

    The stated cost: an endpoint failing more than
    ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of its probes reports no shape
    distribution at all. That is deliberate — the shape claim is the one that
    gets quoted, so it is the one that must not rest on a remnant — and it is
    comfortably clear for the five endpoints the rule was written for, the
    2026-09-08 draw having recorded a single non-200 in 56 CT.gov probes and
    none anywhere else. It is **not** clear for the sixth, and that is the
    exception above rather than a threshold moved: 46 of 52 full-text probes
    404'd on 2026-09-09, which is that endpoint's finding and not its
    failure.

    The parameter it no longer takes was the endpoint's name, which it never
    read while its docstring promised the answer depended on it. It takes one
    again — the exception is per endpoint, so the answer now genuinely does
    depend on it.

    Args:
        endpoint: Which population these outcomes belong to.
        outcomes: The endpoint's probe outcomes.

    Returns:
        Whether the shape distribution may be printed as a share.
    """
    if not is_reportable(outcomes):
        return False
    if endpoint in _ENDPOINTS_WHOSE_SHAPE_IS_OVER_SERVED_BODIES:
        return bool(_shapes_of(outcomes))
    measured = [o for o in outcomes if o.measured]
    return _population_reportable(len(measured), len(measured) - len(_shapes_of(outcomes)))


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
        The lines — the top-level kind distribution with a Wilson interval per
        kind, then one row per declared field with **its own** denominator,
        since a field is only counted in the bodies where it could be asked
        about. A field no served body could be asked is said to have no
        population rather than omitted: otherwise *"never wrong-typed"* and
        *"never reachable"* print alike.

    A no-population row is a **reader's** finding and not a run's, which is
    the one thing about it that changed in PR #213's review. It used to be
    described as what a mis-declared path looks like while nothing could tell
    that from a field the remote simply never carries — and a mis-declared
    path is now caught outright by
    ``test_every_declared_path_is_reachable_in_a_representative_body``, which
    reddens on the mis-nesting the ``ast`` net cannot see because that net
    compares leaf keys. So the row is left to the reader rather than given an
    exit-code term that a draw with no funded CrossRef record would trip
    honestly.

    The interval is printed because these are the rows that get quoted. A
    bare ``100.0%`` over four bodies and over four hundred read identically,
    and the status table directly above has carried its interval all along.
    """
    if not shapes_reportable(name, outcomes):
        measured = [o for o in outcomes if o.measured]
        served = len(_shapes_of(outcomes))
        if not is_reportable(outcomes):
            return [
                f"{name:<18} ERROR — the status population above was not reportable; "
                "no body shape is reported"
            ]
        if not served:
            return [f"{name:<18} ERROR — no body was served; no shape distribution is reported"]
        return [
            f"{name:<18} ERROR — only {served}/{len(measured)} probes served a body; "
            "too few to report a shape distribution"
        ]
    shapes = _shapes_of(outcomes)
    served = len(shapes)
    lines = [f"{name:<18} {served:>4} bodies served"]
    for kind, count in sorted(Counter(shape.top for shape in shapes).items()):
        lo, hi = wilson(count, served)
        lines.append(
            f"{'':<18}   {kind:<34} {count:>4}   {100 * count / served:5.1f}%   "
            f"95% CI [{100 * lo:.1f}%, {100 * hi:.1f}%]"
        )
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
    """The EuropePMC bodies that were categorised — see :func:`_addressability`.

    The filter is load-bearing, not tidiness: a body that did not decode, and
    since PR #213's review one that decoded to something other than an object,
    carries no category, and ``summarise_addressing`` formats the category
    into a fixed-width field — so an uncategorised shape reaching it raises
    ``TypeError`` on ``None``. That body shape is issue #184's exact live
    failure, EuropePMC answering 200 with an HTML error page.
    """
    return [shape for shape in _shapes_of(outcomes) if shape.addressability]


def addressing_reportable(outcomes: list[ProbeOutcome]) -> bool:
    """Whether the address table is a distribution rather than an ERROR.

    Two ways it is not. There may be no categorised body at all; or **every**
    categorised body may be ``no-record``, which is a real bmlib outcome —
    EuropePMC answers, holds nothing, and ``_check_europepmc`` returns before
    any address is built — but is *also* what this script looks like when its
    own single-record lookup has stopped working. Every drawn record came from
    EuropePMC, so a population that is wholly ``no-record`` cannot be a fact
    about the corpus, and it used to print ``a full-text request would be made
    for 0 of 180`` at exit ``0``: a spectacular finding about bmlib
    manufactured out of a broken lookup (PR #213's review).

    No threshold is invented between those two: what share of ``no-record`` is
    ordinary here has never been measured, and a guess would be the
    permanently-red hazard issue #94 records one instrument over. The count is
    printed on its own line instead, so a reader can see it move.
    """
    shapes = _addressed_shapes(outcomes)
    return bool(shapes) and any(s.addressability != "no-record" for s in shapes)


def summarise_addressing(outcomes: list[ProbeOutcome]) -> list[str]:
    """How bmlib would have addressed each record's full text — issues #207, #188.

    Every category but ``pmcid`` and ``id-accession`` makes no request, and
    all of them store
    ``FullTextStatus.NOT_ATTEMPTED``, whose documented meaning — *"no request
    was made, and EuropePMC's own answer is why"* — is false for
    ``unaddressable``. Whether that earns a member of its own is what #207
    asks, and it was filed rather than taken because the population was
    unmeasured; it measured **0 of 124** on 2026-09-08.

    **``id-only`` is gone and did not become one of the two names below.** It
    counted every record addressed by its bare ``id``, which issue #188 split
    into the accession that serves and the PMID that cannot; keeping the name
    for either half would have made a published figure — *"43 of 124"* — mean
    something else without changing, which is this repository's own scar
    (``_COUNTER_DEFINITIONS_VERSION``, four counters redefined in place). Any
    figure quoted against ``id-only`` is from before that split.

    Args:
        outcomes: The EuropePMC probe outcomes.

    Returns:
        The category distribution, with both ``id``-fallback categories split
        by the source that decides whether their address is real (issue #188).
    """
    shapes = _addressed_shapes(outcomes)
    if not shapes:
        return [f"{'addressing':<18} ERROR — no EuropePMC record was served; nothing to categorise"]
    if not addressing_reportable(outcomes):
        return [
            f"{'addressing':<18} ERROR — all {len(shapes)} categorised bodies held no record, "
            "so this lookup found none of the records it drew; nothing is reported"
        ]
    total = len(shapes)
    asked = sum(1 for s in shapes if s.addressability in ADDRESSED_CATEGORIES)
    lines = [
        f"{'addressing':<18} {total:>4} records categorised; "
        f"a full-text request would be made for {asked} of {total}"
    ]
    for category, count in sorted(Counter(s.addressability for s in shapes).items()):
        lines.append(f"{'':<18}   {category:<34} {count:>4}   {100 * count / total:5.1f}%")
    # Called out beside the distribution rather than left as one row among
    # the rest: every drawn record came from this same API, so a `no-record` is
    # this script failing to re-find its own draw and not the corpus
    # answering. The share is what a reader needs to judge the rest by.
    no_record = sum(1 for s in shapes if s.addressability == "no-record")
    if no_record:
        lines.append(
            f"{'':<18}   of which {no_record} were re-drawn records this lookup did not "
            "re-find, which is an instrument result rather than a corpus one"
        )
    by_source = Counter(
        (s.addressing.category, s.addressing.source or "(no source)")
        for s in shapes
        if s.addressing and s.addressing.category in _ID_FALLBACK_CATEGORIES
    )
    for (category, source), count in sorted(by_source.items()):
        lines.append(f"{'':<18}     {category}, source {source:<17} {count:>4}")
    return lines


def addresses_reportable(probes: list[AddressProbe]) -> bool:
    """Whether the full-text address table is a distribution rather than an ERROR."""
    return _population_reportable(len(probes), sum(1 for p in probes if p.is_unmeasured))


def _served_share(label: str, probes: list[AddressProbe]) -> str:
    """One row: how many of *probes* EuropePMC served a document for.

    The interval is on every row for :func:`summarise_shapes`' reason — these
    are the rows that get quoted, and a bare ``0.0%`` over three probes and
    over three hundred read identically. Issue #188's whole remedy is *"do not
    make a request that cannot succeed"*, and the strength of that claim is
    the width of this interval.

    An **empty** population and one whose every probe was thrown away are two
    answers, so they get two lines (PR #219's review): the first is a row the
    draw never reached, the second is a row the draw reached and lost.
    Collapsing them is the *"never wrong-typed" versus "never asked"* conflation
    this script's shape table is built to avoid.
    """
    if not probes:
        return f"{'':<18}   {label:<34}    - NO POPULATION HERE (none was drawn)"
    measured = [p for p in probes if not p.is_unmeasured]
    if not measured:
        return f"{'':<18}   {label:<34} {len(probes):>4} probed   none measured"
    served = sum(1 for p in measured if p.served)
    lo, hi = wilson(served, len(measured))
    return (
        f"{'':<18}   {label:<34} {len(measured):>4} probed   {served:>4} served = "
        f"{100 * served / len(measured):5.1f}%   95% CI [{100 * lo:.1f}%, {100 * hi:.1f}%]"
    )


def summarise_addresses(probes: list[AddressProbe]) -> list[str]:
    """What EuropePMC did with each full-text address bmlib would build — issues #216, #188.

    Two cross-tabulations over one set of probes, and they answer two
    different questions that this repository has kept getting told apart:

    * **by address category and source** — issue #188's own. A ``pmcid``
      address and a ``PPR`` record's bare ``id`` are the article; a ``MED``
      record's bare ``id`` is a PMID, and whether *that* ever serves is the
      whole of what licenses refusing to ask.
    * **by ``isOpenAccess``** — the larger population recorded on that issue
      rather than filed separately. ``inEPMC`` says EuropePMC *holds* the
      text while this endpoint serves the open-access subset of it, so the
      two are different gates and the second is the one the module does not
      use. Recorded, not acted on: a hand-taken page put it at 0 of 53, and a
      gate narrowed on a floor silently loses an article that would have been
      served.

    Above both sits **one row for the population the analyzer's own 404
    branch takes**, which is :data:`ADDRESSED_CATEGORIES` and not the probed
    set (PR #219's review). Without it the only served share on the page was
    over all of :data:`PROBED_CATEGORIES`, and that is what four documents
    quoted as the DEBUG level's committed denominator — while the branch the
    level sits on had just been narrowed by issue #188 to exclude 43 of those
    52 probes. The two disagree by more than their intervals: 46 of 52 not
    served against 3 of 9, [77.0, 94.6] against [12.1, 64.6]. That is this
    script's own rule (*"the branch it sits on must be no wider than the
    draw"*) read the other way round, and it is the ``id-only`` scar again —
    a published figure meaning something else without changing.

    **Every probed category prints a row even at zero**, for the reason
    :func:`summarise_shapes` prints ``NO POPULATION HERE``: ``id-accession``
    measured 0 in the 2026-09-09 draw, so the fallback the shape test exists
    to preserve was unexercised and the table said nothing at all — *"served
    0 of 0"* and *"never drawn"* being the distinction this whole script is
    built to keep.

    Args:
        probes: One per record that offered an address.

    Returns:
        The lines. An absent population is an ERROR rather than a clean zero,
        this directory's standing rule — and here it is not a formality: a run
        in which no record claims ``inEPMC: Y`` probes no address at all, and
        would otherwise print nothing while every table above it stayed green.
    """
    label = "full-text address"
    if not probes:
        return [
            f"{label:<18} ERROR — no record offered a full-text address; nothing was probed",
        ]
    if not addresses_reportable(probes):
        unmeasured = sum(1 for p in probes if p.is_unmeasured)
        return [
            f"{label:<18} ERROR — {unmeasured}/{len(probes)} addresses were throttled "
            "(429/503) even after retries; no distribution is reported"
        ]
    lines = [f"{label:<18} {len(probes):>4} addresses probed"]
    # The analyzer's own population first, because it is the one a log level
    # is set from and the one every other row can be mistaken for.
    asked = [p for p in probes if p.addressing.category in ADDRESSED_CATEGORIES]
    lines.append(_served_share("addresses bmlib asks with", asked))
    by_category: dict[str, list[AddressProbe]] = {}
    for probe_result in probes:
        addressing = probe_result.addressing
        # The source is on the row only for the category it decides, which is
        # issue #188's split. On a `pmcid` row it would be noise that fans one
        # population into three and shrinks every denominator on the page.
        key = (
            f"{addressing.category}, source {addressing.source or '(none)'}"
            if addressing.category in _ID_FALLBACK_CATEGORIES
            else addressing.category
        )
        by_category.setdefault(key, []).append(probe_result)
    for key in sorted(by_category):
        lines.append(_served_share(key, by_category[key]))
    for category in sorted(PROBED_CATEGORIES):
        if not any(k == category or k.startswith(f"{category}, source ") for k in by_category):
            lines.append(
                f"{'':<18}   {category:<34}    - NO POPULATION HERE "
                "(no record in this draw offered one)"
            )
    lines.append(f"{'':<18}   and by isOpenAccess, which bmlib does not read:")
    by_access: dict[str, list[AddressProbe]] = {}
    for probe_result in probes:
        by_access.setdefault(probe_result.addressing.open_access or "(absent)", []).append(
            probe_result
        )
    for key in sorted(by_access):
        lines.append(_served_share(f"isOpenAccess {key}", by_access[key]))
    return lines


def checks_reportable(checks: list[TrialCheck]) -> bool:
    """Whether the results-check table is a distribution rather than an ERROR."""
    return _population_reportable(len(checks), sum(1 for c in checks if c.is_unmeasured))


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
        unmeasured = sum(1 for c in checks if c.is_unmeasured)
        return [
            f"{'results checks':<18} ERROR — {unmeasured}/{len(checks)} checks were throttled; "
            "no distribution is reported"
        ]
    classified = [c for c in checks if not c.is_unmeasured]
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
    return _population_reportable(len(verdicts), verdicts.count(UNMEASURED))


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
    classified = [v for v in verdicts if v != UNMEASURED]
    total = len(classified)
    lines = [f"{'registration':<18} {total:>4} records with a source outcome"]
    for verdict, count in sorted(Counter(classified).items()):
        lines.append(f"{'':<18}   {verdict:<34} {count:>4}   {100 * count / total:5.1f}%")
    return lines


def summarise_draw(name: str, draw: Draw, target: int, strata: int) -> list[str]:
    """Report one sample: how it was stratified, and where it is not.

    A stratum that failed is named. The tables below rest on the draw being
    spread over source and year, so a reader who is not told which cells are
    missing is reading a distribution as if it were the one the header claims.

    Args:
        name: Which draw this is — the two are reported separately because
            they are two populations, and a reader who is shown one figure for
            both cannot tell which denominator a share belongs to.
        draw: The sample.
        target: How many records were requested, printed beside the kept count
            so the shortfall is visible without the reader having to know the
            default. Without it a draw at a twentieth of its target read as an
            ordinary header line (PR #213's review).

    Returns:
        The lines to print. A stratum that failed is named, and so is one that
        answered and kept nothing — the loss `failed_strata` cannot see,
        because it tests the page and the refusal happens one level down.
    """
    if not draw.records:
        return [f"{name:<18} ERROR — no records were drawn; its table below is empty"]
    by_stratum = Counter(f"{r.source}/{r.year}" for r in draw.records)
    lines = [
        f"{name:<18} {len(draw.records)} of {target} requested records, over "
        f"{len(by_stratum)} of {strata} strata "
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
        "--target",
        type=int,
        default=DEFAULT_TARGET,
        help="Records to request in total; the draw keeps only those bmlib could analyse.",
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
    #: Issues #216 and #188's: the full-text address each record offered, and
    #: what EuropePMC did with it. Per record for `trial_checks`' reason — the
    #: row is keyed on the record's own category, which no endpoint table has.
    address_probes: list[AddressProbe] = []

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
            record_outcomes = probe_record(client, record, args.email, pace, address_probes)
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
    for line in summarise_draw("draw", draw, args.target, len(DRAW_STRATA)):
        print(line)
    for line in summarise_draw("trial draw", trial_draw, args.trial_target, len(TRIAL_STRATA)):
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
    for line in summarise_addresses(address_probes):
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
    # Each table added for issue #211 can flip the exit code on its own, for
    # the reason `is_reportable` gives about its own: the exit code is judged
    # by what was printed, and every one of these populations can be absent
    # while every status distribution above it is perfectly healthy — a run in
    # which every probe 404s measures no body at all and would otherwise go
    # green. They are ANDed into two names rather than six, and
    # `test_each_rider_populations_verdict_reaches_the_exit_code` is what
    # holds each of them individually load-bearing.
    shaped = all(shapes_reportable(name, by_endpoint[name]) for name in ENDPOINTS)
    sized = (
        addressing_reportable(by_endpoint["europepmc_search"])
        and addresses_reportable(address_probes)
        and reach_reportable(reach_verdicts)
        and checks_reportable(trial_checks)
    )
    # A body this script could not read is bmlib's caller being wrong, never
    # the remote — reported at the point it happens and counted here, or the
    # kind sits in the distribution as though it were a finding.
    sound = not any(instrument_defects(by_endpoint[name]) for name in ENDPOINTS)
    # `drawn` is subsumed today — an empty draw empties every per-record
    # population, so `reportable` is already False — and is kept as the
    # statement of the rule rather than as a live guard.
    drawn = bool(draw.records) and bool(trial_draw.records)
    # `unusable_records` joins the two stratum lists: a record the page
    # returned and `DrawnRecord` refused is a hole in the draw exactly as an
    # emptied stratum is, only smaller, and the draw is then shorter than the
    # target every interval below is read against. Without it a draw could
    # fall to a single record per stratum and still exit 0 (PR #213's review).
    lost = any(
        d.failed_strata or d.unusable_strata or d.unusable_records for d in (draw, trial_draw)
    )
    return (
        0
        if reportable
        and shaped
        and sized
        and sound
        and drawn
        and not lost
        and not population_failures
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
