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

"""Multi-API transparency analyzer.

Queries CrossRef, Europe PMC (search and full text), PubMed, OpenAlex, and
ClinicalTrials.gov to assess transparency of biomedical publications.

Requires ``httpx`` (install with ``pip install bmlib[transparency]``).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from bmlib import __version__
from bmlib.transparency.models import (
    FullTextStatus,
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    TransparencyUnknownReason,
    TrialResultsStatus,
    calculate_risk_level,
)

logger = logging.getLogger(__name__)

# ---- Known pharma / industry funder keywords ----
# Matched against structured funder names — CrossRef `funder[].name` and
# PubMed `<Grant><Agency>` — both short org-name strings.
#
# THE LIST IS TWO KINDS OF THING, AND MERGING THEM BACK INTO ONE IS A BUG.
# A stem has to match *inside* a longer word ("pharmaceutic" reaching
# "Pharmaceuticals"); a whole word must not ("inc" as a substring matches
# "Lincoln", "Vincent" and "province"). Applying word boundaries uniformly —
# how issue #36 frames the fix — would lose the stems; applying substrings
# uniformly is what made "Pfizer Inc" a false negative in the first place.
#
# MEMBERSHIP FOLLOWS FOUR RULES, AND RULE 4 OVERRIDES THE OTHER THREE (#112).
# Stating one rule and applying another is what the counts below were hiding:
# "plc" and "pty" were excluded for scoring 0 TP while "pharma", "biotech",
# "corp" and "gmbh" were kept at exactly the same score. So every row below
# carries the rule that decided it and an explicit "in"/"out", and the test
# named further down checks that naming against these tuples — a row can no
# longer say a token is refused while the matcher is using it.
#   1. Corpus evidence earns a token: at least one true positive, with its
#      false positives counted rather than assumed. It also *refuses* one —
#      "corporation" is out at 1 TP / 1 FP. There is no numeric threshold
#      here, and inventing one would be false precision; the tiebreak at the
#      foot of this block is what decides a close call.
#   2. A reserved incorporation suffix is a strong prior in itself, so it is
#      kept where the corpus holds no evidence either way. NOT because a
#      public body cannot use the form: it can, and the cost is real. German
#      and Austrian public research institutes routinely incorporate as GmbH
#      (Forschungszentrum Jülich GmbH, Helmholtz Zentrum München GmbH), and
#      UK charities and public bodies as companies limited by guarantee
#      (Genome Research Limited, the Wellcome Sanger Institute's own legal
#      entity). This corpus holds such a name itself — "Goethe Business
#      School GmbH", labelled *ambiguous* for the reason "incorporated as a
#      GmbH, but an academic business school rather than a commercial
#      research sponsor" — and it is invisible in the "gmbh" row because
#      ambiguous names are excluded from scoring. So a 0 TP / 0 FP under
#      rule 2 means "not scored", never "not present". The rule is a prior,
#      not proof; #156 is the open question of whether it survives a corpus
#      drawn to contain those names.
#   3. The residue of a disqualified stem is kept as a bare word where it
#      cannot match more than the stem it replaced, which makes it free.
#      "pharma" and "biotech" are in on this and on nothing else — they
#      satisfy neither rule 1 nor rule 2, and describing them as "kept at
#      the same score" as rule 2's members is what left this fourth category
#      unnamed while the block claimed every token was covered.
#   4. A token is refused where it collides with a form this corpus cannot
#      see, AND THIS RULE VETOES THE OTHER THREE. Two applications: a token
#      of two characters is refused outright, its collision surface being
#      wider than 412 names can sample; a longer token is refused on a
#      *named* collision. The veto is paid in measured true positives and in
#      rule-2 standing, which is why it is worth stating as a veto rather
#      than as a fourth opinion — "ab" (Aktiebolag), "ag", "bv", "nv" and
#      "sa" are every bit as reserved as rule 2's members, and "co" carries
#      4 TP / 0 FP, the strongest corpus evidence of any refused token.
# Ties go to precision throughout, because `industry_funding_detected` feeds a
# HIGH-risk rule and HIGH downgrades a paper's quality tier.
#
# EVERY COUNT BELOW IS MEASURED, AND THE MEASUREMENT IS A TEST.
# The corpus is `tests/data/funder_names.json`, sampled live from CrossRef and
# PubMed by `scripts/sample_funder_names.py`: 833 names drawn, 816 unique, 417
# labelled, 412 scoring (the five ambiguous are excluded). Those four figures
# are themselves asserted against the file, since a corpus quietly cut down to
# the names some token reaches would reproduce every row below unchanged.
# `tests/test_funder_matching.py::TestTheStatedCountsAreWhatTheCorpusHolds`
# parses the rows below out of *this file* and re-derives every one against
# that corpus, so a redraw fails the suite rather than leaving a stale number
# here. Eight claims here were wrong before that test existed — seven figures
# and one named example — and not by drift: the corpus has one commit and the
# matcher was byte-identical, so they were taken against a revision that was
# never committed. Do not reformat a row without reading that class: the row
# is the input under test, not a copy of one.
#
# ROW FORMAT, which is a contract with that test and not a layout choice:
#   #   "<token>"  <stem|word>  <in|out>  <N> TP / <M> FP  rule <R>
# with the reason, if any, on indented continuation lines that deliberately
# match nothing. "in" means the token is in the tuple below it.

# Substring stems.
#   "pharmaceutic"    stem  in   3 TP / 1 FP  rule 1
#       Its one false positive is the whole matcher's only one, and it is not
#       academic: "National Inheritance Studio of Veteran Pharmaceutical
#       Workers of Zhong Lingyun". That name is what caps precision below
#       1.000, so a blanket "the stems have no false positives" is wrong.
#   "therapeutics"    stem  in   1 TP / 0 FP  rule 1
#   "laboratories"    stem  in   1 TP / 0 FP  rule 1
#       The plural only — see the "key laboratory" row below for what the
#       singular would cost.
#   "pharma"          stem  out  3 TP / 5 FP  rule 1
#       Disqualified, and replaced by "pharmaceutic". Its five are "Pharmacy"
#       three times (a university faculty, a hospital department and a
#       provincial key laboratory), "Pharmacogenetics", and the
#       Pharmaceutical-Workers name that "pharmaceutic" inherits. Narrowing
#       kept all three true positives and dropped four of the five.
#   "biotech"         stem  out  0 TP / 4 FP  rule 1
#       Disqualified. Its only hits are "Department of Biotechnology" (an
#       Indian department within the Ministry of Science and Technology, in
#       three spellings) and "Biotechnology and Biological Sciences Research
#       Council" (a UK research council). "Biotechnology" names a field, not
#       a company type.
#   "key laboratory"  stem  out  0 TP / 2 FP  rule 1
#       Never a candidate, carried as a row so the figure is re-derived: this
#       is the Chinese state-lab form "laboratories" must keep missing, and
#       the count was recorded as eight until #112 measured it. Both are
#       provincial or university labs; neither is commercial.
_INDUSTRY_STEMS = (
    "pharmaceutic",
    "therapeutics",
    "laboratories",
)

# Whole words. No trailing "\.?" is needed: `\b` already sits between the last
# letter and a following ".", so "Inc" and "Inc." both match.
#   "pharma"          word  in   0 TP / 0 FP  rule 3
#       Residue of the disqualified stem: as a bare word it names a company
#       ("Novartis Pharma AG") and cannot match more than the stem did.
#   "biotech"         word  in   0 TP / 0 FP  rule 3
#       Likewise ("Acme Biotech").
#   "incorporated"    word  in   1 TP / 0 FP  rule 1
#       "inc" does not reach it: `\binc\b` needs a boundary and
#       "Incorporated" continues with "o".
#   "inc"             word  in   2 TP / 0 FP  rule 1
#   "corp"            word  in   0 TP / 0 FP  rule 2
#       Note that "corporation", one row family down, is refused on a
#       measured false positive. Delaware §102(a)(1) reserves both forms and
#       also reserves "Foundation", "Institute" and "Society", so what keeps
#       "corp" is that the abbreviation is rarer among non-profits — a
#       frequency argument, not the categorical one rule 2 makes.
#   "limited"         word  in   1 TP / 0 FP  rule 1
#   "ltd"             word  in   2 TP / 0 FP  rule 1
#   "gmbh"            word  in   0 TP / 0 FP  rule 2
#       The corpus's one GmbH is ambiguous-labelled and so unscored; see
#       rule 2 above, which is where the cost of this row is written down.
#   "llc"             word  in   2 TP / 0 FP  rule 1
#   "plc"             word  in   0 TP / 0 FP  rule 2
#       Added when rule 2 was written down (#112); the form UK-listed pharma
#       reports under, and the Swift port keeps it for the same reason. Rule
#       4 was asked of it and the answer was not free: PLC is also the usual
#       abbreviation of *phospholipase C*, so "Role of PLC-gamma signalling
#       in tumour invasion" is flagged. It is kept because that collision is
#       a research topic while rule 4's other members collide with forms
#       that appear in organisation names — but 41 of these 417 names run to
#       ten words or more, so topic strings do reach this field, and #157 is
#       the measurement that would settle it. Unmeasured, and said so here
#       rather than nowhere.
#   "pty"             word  in   0 TP / 0 FP  rule 2
#       Rule 2 likewise. No collision found for it; the Swift port's
#       deviation covers "plc" alone, so this one stands on rule 2 only.
#
# Refused. Each row names the rule that refuses it.
#   "co"              word  out  4 TP / 0 FP  rule 4
#       This corpus holds no collision at all, so the earlier note of one
#       measured false positive was wrong (#112). The risk is real and simply
#       invisible here: `\bco\b` reaches "co-sponsored", "co-funded" and
#       "Co-operative". Refusing it costs one true positive no other token
#       reaches, "Merck & Co.; Merck Sharp & Dohme" — the other three carry
#       "Ltd", "Limited" or "Pharmaceutical" too.
#   "corporation"     word  out  1 TP / 1 FP  rule 1
#       US non-profits use it ("Research Corporation for Science
#       Advancement"). Costs "Invitae Corporation".
#   "ag"              word  out  0 TP / 0 FP  rule 4
#   "bv"              word  out  0 TP / 0 FP  rule 4
#   "nv"              word  out  0 TP / 0 FP  rule 4
#   "sa"              word  out  0 TP / 0 FP  rule 4
#       Those four are two characters, so rule 4 refuses them outright. All
#       four are reserved incorporation suffixes — Aktiengesellschaft,
#       Besloten and Naamloze Vennootschap, Société Anonyme — so rule 2
#       would admit every one of them, and the veto is what decides it.
#   "ab"              word  out  1 TP / 0 FP  rule 4
#       Two characters, and it passes rule 1 as well. These strings carry
#       locations ("…, Hyderabad, India"), so "University of Calgary, AB"
#       would be a false positive this corpus cannot see. Costs "Roche
#       Sweden AB".
#   "labs"            word  out  1 TP / 0 FP  rule 4
#       The only token refused on a named collision rather than on length:
#       "Los Alamos National Labs" is not industry. Costs "Tempus Labs".
_INDUSTRY_WORDS = (
    "pharma",
    "biotech",
    "incorporated",
    "inc",
    "corp",
    "limited",
    "ltd",
    "gmbh",
    "llc",
    "plc",
    "pty",
)


def _compile_word_re(words: tuple[str, ...]) -> re.Pattern[str]:
    """Compile the whole-word alternation the matcher applies to ``words``.

    Exists to be shared with ``tests/test_funder_matching.py``, which scores
    one token at a time to re-derive the counts above and so cannot use
    ``_INDUSTRY_WORD_RE`` (a single union over the whole tuple). Handing it
    this constructor rather than letting it hand-write ``\\b…\\b`` a second
    time is what keeps the two in step: with a second copy, dropping the
    leading ``\\b`` moved four of the stated counts while the test's
    whole-name agreement control stayed green, because no corpus name
    disagreed (#112, review of PR #155).
    """
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


_INDUSTRY_WORD_RE = _compile_word_re(_INDUSTRY_WORDS)


def _is_industry_funder(name: str) -> bool:
    """Report whether a structured funder name looks like a commercial entity.

    The single predicate behind both funder sources — CrossRef
    ``funder[].name`` and PubMed ``<Grant><Agency>`` — so there is one
    definition to test and one to measure against the labelled corpus.

    Deliberately **not** applied to COI prose; see
    :data:`_INDUSTRY_COI_KEYWORDS` for why that is a different corpus with
    different failure modes.

    Args:
        name: The funder or grant-agency name as the source reported it.

    Returns:
        True if a stem matches anywhere in the name, or one of the whole-word
        terms matches as a word.
    """
    if any(stem in name.lower() for stem in _INDUSTRY_STEMS):
        return True
    return _INDUSTRY_WORD_RE.search(name) is not None


# ---- Industry disclosure phrases ----
# Matched against the paper's COI/disclosure statement in the full text. Kept
# separate from the funder keywords above: the generic org suffixes ("inc",
# "ltd", …) match far too freely in running text, while these phrases never
# occur in a funder name.
_INDUSTRY_COI_KEYWORDS = [
    "employee of",
    "speaker fee",
    "consultant for",
    "advisory board",
]

# ---- EuropePMC REST ----
#: The base every EuropePMC call is built from. One constant rather than two
#: literals, because issue #184 was exactly the two drifting apart: the search
#: call was right and the full-text call was not, and nothing said so.
#:
#: **An article is addressed by its EuropePMC accession alone** — no
#: ``{source}`` segment. That is measured, not inferred: on 2026-09-05 the
#: single-segment form served 200 for PMC12900525, PMC3258128, PMC10030002,
#: PMC13426601 and six ``PPR`` accessions, while ``{source}/{ext_id}``, the
#: bare numeric id and the PMID all returned EuropePMC's own 404 (their CORS
#: headers, ``content-length: 0``, so theirs rather than a proxy's). The
#: sibling two-segment endpoints ``textMinedTerms`` and ``supplementaryFiles``
#: 404 the same way, so it is the path shape and not one endpoint.
#:
#: The accession is **not** a PMCID and must not be normalised into one:
#: 75,760 of EuropePMC's 12,220,678 ``IN_EPMC:Y`` records (0.62%, from their
#: own hit counts rather than a draw) are preprints carrying no ``pmcid``,
#: addressed by a ``PPR…`` accession that
#: :func:`~bmlib.fulltext.service._normalise_pmc_id` would reject. This
#: module and ``fulltext/service.py`` agree on the *base* — pinned by
#: ``test_the_two_modules_agree_on_the_base_and_on_a_pmcid`` — and
#: deliberately not on the identifier.
EUROPEPMC_REST_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# ---- The other three endpoints ----
#: Named constants rather than f-strings inside the methods, for the reason
#: :data:`EUROPEPMC_REST_BASE` was extracted: a URL buried in a method body is
#: a URL nothing can pin, and issue #184 was one of those wrong for a whole
#: release. What they buy here is that ``scripts/sample_api_failures.py`` —
#: the draw the log levels below are set from — probes *these* strings, so the
#: measurement cannot be of an endpoint bmlib does not call. Pinned by
#: ``TestTheSamplerProbesWhatTheAnalyzerRequests``.
#:
#: The identifier is interpolated with ``str.format``, which reads braces in
#: the *template* only, so a DOI carrying one is passed through unharmed.
CROSSREF_WORKS_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_WORKS_URL = "https://api.openalex.org/works/doi:{doi}"
CLINICALTRIALS_STUDY_URL = "https://clinicaltrials.gov/api/v2/studies/{nct_id}"

#: The per-request headers CrossRef and OpenAlex are sent, named for the same
#: reason the URLs above are: ``scripts/sample_api_failures.py`` has to send
#: what bmlib sends, and a header restated there measures somebody else's
#: request. Issue #194 was exactly that mistake in a ``User-Agent``, and the
#: sampler's first cut sent no per-request headers at all (PR #195's review).
#: Pinned by ``TestTheSamplerProbesWhatTheAnalyzerRequests``.
#:
#: A read-only mapping, not a plain ``dict``: it is module state shared by two
#: call sites and imported by a script, and the frozen forms beside it
#: (``_BUG_TYPES`` a tuple, the ``_ORDINARY_STATUSES`` sets ``frozenset``) are
#: frozen for the same reason. There is no frozen dict builtin, so this is the
#: nearest thing.
JSON_ACCEPT_HEADERS: Mapping[str, str] = MappingProxyType({"Accept": "application/json"})


def _user_agent(email: str, httpx_version: str) -> str:
    """The ``User-Agent`` every request from this module carries.

    Extracted from :meth:`TransparencyAnalyzer.analyze` for the reason the
    URLs above were: it is a value the remote ends judge us by, and one
    written inline in a method body is one nothing can pin — while
    ``scripts/sample_api_failures.py`` has to send the *same* header or it is
    not measuring what bmlib does. That is the header half of issue #184's
    lesson, and issue #194 is what it cost.

    **The trailing ``python-httpx`` token is load-bearing and is not
    decoration.** ClinicalTrials.gov's edge refuses bmlib's identification
    with a bare 134-byte ``403 Forbidden`` page, so every
    :meth:`_check_trial_results` call ever made was declined — returning
    ``False``, which in a ``bool`` is indistinguishable from *"this trial
    posted no results"*. ``SCORE_RESULTS_POSTED`` was therefore never awarded
    to any paper and *"Registered trial without posted results"* was stored
    as a false claim about every registered trial (issue #194).

    Measured 2026-09-06 against ``/api/v2/studies/{nct}?fields=hasResults``:
    six alternating rounds of bmlib's header against httpx's own default gave
    403/200 six times of six, and four accessions all 403'd on bmlib's. Of
    thirteen header shapes, the five carrying ``python-httpx`` — including
    ``python-httpx`` bare, and the token appended *after* bmlib's own
    identification — served 200, while ``curl/8.7.1``,
    ``python-requests/2.31.0``, ``Python-urllib/3.11``, ``Go-http-client/2.0``,
    ``PostmanRuntime/7.37.0`` and a browser string were all refused. So it is
    an allow-list on that one token, and its position does not matter.

    The token is **appended to** bmlib's identification rather than replacing
    it: CrossRef and NCBI both ask a caller to say who it is and where to
    write, and answering ``python-httpx`` alone would trade one API's policy
    for two others'. And it is not a fiction — bmlib *is* httpx here, so this
    says exactly what httpx would have said about itself before this module
    overrode it. The version comes from the caller's own ``httpx.__version__``
    for the same reason.

    This is a live-only property that **no test can hold**: no test in the
    suite makes a live request, which is precisely why the 403 went unseen
    through a whole release. ``scripts/sample_api_failures.py`` is the guard —
    run it before touching this string.

    Args:
        email: The analyzer's contact address.
        httpx_version: ``httpx.__version__``, passed in because httpx is an
            optional dependency this module imports only inside ``analyze()``.

    Returns:
        The header value.
    """
    return f"bmlib/{__version__} (mailto:{email}) python-httpx/{httpx_version}"


#: Statuses that log at DEBUG rather than WARNING, **per endpoint**, because a
#: draw measured them to be that endpoint's ordinary outcome. Issue #191's
#: rule: a level is a claim, and the branch it sits on must be no wider than
#: the draw that earned it — DEBUG measured on 404s and applied to every
#: status is what that issue was.
#:
#: The evidence is ``scripts/sample_api_failures.py``. **Run it before
#: changing any of these**, and read the run's own report rather than these
#: comments, which are a snapshot of one draw.
#:
#: **Every one of them is empty, and that is the measurement rather than a
#: default.** 180 records drawn 2026-09-06, stratified over source × year
#: (MED/PMC/PPR × 2024/2014/2004, preprints 2024/2019/2014), plus 60 drawn
#: separately for the trial population, every request addressed and headed
#: exactly as this module addresses and heads it:
#:
#: ===================  =======  ========  ==================
#: endpoint             probed   non-200   95% CI on non-200
#: ===================  =======  ========  ==================
#: CrossRef                  73         0  [0.0%, 5.0%]
#: EuropePMC search         180         0  [0.0%, 2.1%]
#: PubMed efetch             60         0  [0.0%, 6.0%]
#: OpenAlex                  73         0  [0.0%, 5.0%]
#: ClinicalTrials.gov        53         0  [0.0%, 6.8%]
#: ===================  =======  ========  ==================
#:
#: So no status has been measured ordinary at any of these endpoints, and
#: every non-200 warns. Read those as **upper bounds and not as proof**: a
#: zero says the ordinary outcome is a 200, not that a 404 cannot happen. The
#: contrast with ``_fetch_europepmc_fulltext``'s 404 is the whole point —
#: there, **81 of 81 non-200s were 404**, and separately the 404 is the
#: majority outcome of the gate that module uses (88 of 150 in the stratified
#: draw recorded at ``_fetch_europepmc_fulltext``). Those are two figures over
#: two denominators and neither implies the other: over the 200-probe draw the
#: 81 are 40.5%, a minority. Both are needed — exhaustive *and* ordinary — and
#: welding them into one sentence is the denominator error this repo's own
#: rule names (PR #195's review). Nothing here has either.
#:
#: The population is *identifiers bmlib is handed*, which come from indexed
#: records — a caller passing a malformed or invented DOI is outside the draw,
#: and a 404 is presumably ordinary for one. That is a claim about the caller,
#: not about the endpoint, and it is **not measured**.
#:
#: The ClinicalTrials.gov row is also the live confirmation of issue #194: the
#: same draw against the header this module used to send is 403 for every
#: probe, and 53 of 53 serve under the corrected one.
_CROSSREF_ORDINARY_STATUSES: frozenset[int] = frozenset()
_EUROPEPMC_SEARCH_ORDINARY_STATUSES: frozenset[int] = frozenset()
_PUBMED_ORDINARY_STATUSES: frozenset[int] = frozenset()
_OPENALEX_ORDINARY_STATUSES: frozenset[int] = frozenset()
_CLINICALTRIALS_ORDINARY_STATUSES: frozenset[int] = frozenset()

# ---- PubMed E-utilities ----
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# NCBI asks every E-utilities caller to identify itself; `email` comes from the
# analyzer's own contact address.
EUTILS_TOOL_NAME = "bmlib"

# `DataBankName` values PubMed emits for clinical-trial registries, lowercased
# for matching. A name outside this set is not necessarily a data-deposition
# accession: it is one only if it is also a key of
# `_DEPOSITION_DATABANK_LEVELS` below (GENBANK, PDB, SRA, Dryad, …). A name in
# neither — OMIM, RefSeq, dbSNP, PubChem-*, or anything NLM adds that this
# module has not been updated for — is simply not scored; see the comment
# above `_DEPOSITION_DATABANK_LEVELS` for why those specific names are
# excluded on purpose rather than by omission.
#
# Curated from NLM's published vocabulary:
# https://www.nlm.nih.gov/bsd/medline_databank_source.html
# Both spellings of UMIN's registry are kept: NLM's table says "UMIN CTR" but
# the hyphenated form appears in older records. "jrct" and "iran registry of
# clinical trials" are not in NLM's table and are kept anyway — they cost
# nothing, and jRCT is the live successor to Japan's earlier registries.
_TRIAL_REGISTRY_NAMES = frozenset(
    {
        "clinicaltrials.gov",
        "isrctn",
        "eudract",
        "anzctr",
        "chictr",
        "cris",
        "ctri",
        "drks",
        "iran registry of clinical trials",
        "irct",
        "japiccti",
        "jmacct",
        "jprn",
        "jrct",
        "ntr",
        "pactr",
        "rebec",
        "repec",
        "rpcec",
        "slctr",
        "tctr",
        "umin-ctr",
        "umin ctr",
    }
)
_CLINICALTRIALS_GOV = "clinicaltrials.gov"

# `DataBankName` values naming a repository authors *deposit into*, lowercased,
# each mapped to the data-availability level a deposit into it establishes.
# A mapping rather than a set-per-level so that adding a repository cannot
# silently inherit a default: the level is the value, so there is nowhere to
# add a name without stating what a deposit into it is worth. That matters
# because the two levels are not interchangeable — see `dbgap` below — and the
# generous one feeds a 20-point award.
#
# Curated from the same NLM vocabulary as the registries above, whose second
# table this splits in half. The other half is deliberately excluded: dbSNP,
# GDB, OMIM, PIR, PubChem-BioAssay, PubChem-Compound, PubChem-Substance,
# RefSeq, SWISSPROT, UniMES, UniParc, UniProtKB and UniRef are curated
# *reference* databases. An OMIM number says the paper is about a known
# condition; a RefSeq accession names a sequence NCBI curated, not one these
# authors produced. Neither is evidence that these authors shared their own
# data, which is what the data-availability component measures — so adding
# one back would award 20 points for a citation.
#
# dbSNP is the one exclusion worth spelling out, since dbVar sits right in
# this map: a dbVar accession is a structural-variant submission, but a dbSNP
# citation is overwhelmingly an rs-number reference to a variant someone else
# already catalogued, not a deposit of these authors' own data. Submitters
# can deposit novel variants as ss accessions, but that is the rare case, and
# ties go to precision here because this component feeds a HIGH-risk rule.
#
# Zenodo is absent because NLM's vocabulary does not carry it, so PubMed never
# emits it. `_DATA_PATTERNS` already matches "zenodo" in prose.
_DEPOSITION_DATABANK_LEVELS: dict[str, str] = {
    "bioproject": "full_open",
    "dbvar": "full_open",
    "dryad": "full_open",
    "figshare": "full_open",
    "genbank": "full_open",
    "geo": "full_open",
    "pdb": "full_open",
    "sra": "full_open",
    # Controlled access. The deposit is real, findable and citable, but a
    # reader needs Data Access Committee approval to obtain the data — which
    # is what `on_request` already means, so `full_open` would overstate what
    # a reader can actually get.
    "dbgap": "on_request",
}

# ---- Indicator strings ----
# Named rather than inlined because the PubMed step must be able to retract the
# COI lines: a structured <CoiStatement> can establish a disclosure that the
# full-text scan missed, and leaving any of them in place would then contradict
# `coi_disclosed=True`. They are named once, in
# `_INDICATORS_RETRACTED_BY_PUBMED_COI` below.
_INDICATOR_NO_COI_IN_FULLTEXT = "No COI disclosure found in full text"
# **One claim, and it used to be three lines carrying two each** (issue #203).
# The text was `"… unknown (full text unavailable)"`, with siblings reading
# `"(full text served but not usable)"` (issue #161) and `"(EuropePMC lookup
# failed)"` (issue #193). Each parenthetical said what became of the full text
# — and all three sat in `_INDICATORS_RETRACTED_BY_PUBMED_COI`, so a PubMed
# `<CoiStatement>` refuting the COI half took the provenance with it and a
# HIGH verdict with a tier downgrade could carry a COI *success* as its only
# human-readable line. That is issue #193's own complaint reintroduced through
# the retraction set.
#
# So the COI claim is one line, and what became of the full text is a
# **provenance** line from `_FULL_TEXT_PROVENANCE_INDICATORS` below — which
# also makes the parentheticals honest, `"(full text unavailable)"` having
# been a claim about EuropePMC that is false for `REQUEST_FAILED` (issue
# #191's defect, surviving in the prose half).
_INDICATOR_COI_UNKNOWN = "COI disclosure status unknown"
_INDICATOR_COI_IN_PUBMED = "COI disclosure found in PubMed record"
#: The COI lines written before PubMed is consulted, every one of which claims
#: the status is undeterminable and **nothing else** — so a `<CoiStatement>`
#: arriving afterwards refutes all of them at once and they are retracted
#: together (see :func:`_merge_pubmed_signals`). A set rather than a tuple
#: spelled out at the one call site, for `FullTextStatus.is_refusal`'s reason
#: one module over: a third member was once added at the appending site and
#: not here, so a served-and-refused full text with a PubMed statement stored
#: "status unknown" beside "disclosure found" — permanently, in a persisted
#: field, which is issue #161's own failure mode inside its fix.
#:
#: It held four lines until issue #203 showed the set was the wrong place to
#: solve that: three of them also said what became of the full text, and a
#: retraction is all-or-nothing. Splitting the claims shrank it to two, and
#: what a line must satisfy to belong here is now stated rather than implied —
#: it asserts something about the COI status and nothing else.
_INDICATORS_RETRACTED_BY_PUBMED_COI = frozenset(
    {
        _INDICATOR_NO_COI_IN_FULLTEXT,
        _INDICATOR_COI_UNKNOWN,
    }
)
#: What became of the full text, in prose, keyed on the status that decided it
#: (issue #203). One line per outcome, and the mapping is the point: the three
#: parentheticals it replaces were written on three branches, so they could
#: only ever be as fine-grained as the branch, and `REQUEST_FAILED` shared
#: *"full text unavailable"* with the 404 it was split away from (issue #191).
#: Keyed on the enum, the prose is exactly as precise as the machine-readable
#: half and cannot silently stop describing it — every member must appear
#: here or in :data:`_STATUSES_WITH_NO_PROVENANCE_LINE`, which
#: ``test_every_status_says_what_happened`` pins. The
#: ``TestTheAuditNetIsComplete`` rule one package over: a rule enforced by
#: prose is not enforced.
#:
#: These lines are **never retracted**, which is what issue #203 is about.
#: That is enforced structurally rather than by keeping them out of
#: :data:`_INDICATORS_RETRACTED_BY_PUBMED_COI`: :func:`_note_full_text_provenance`
#: runs after every step, so there is no window in which a retraction could
#: reach them. ``test_no_provenance_line_is_retractable`` is the belt to that
#: brace, since a future line appended earlier would be silently retractable.
#:
#: None of them states the score cost. Each says one thing — what happened —
#: and the points lost are on the result and in the WARNING that names the
#: step; a number restated in prose is a number that goes stale.
_FULL_TEXT_PROVENANCE_INDICATORS: dict[FullTextStatus, str] = {
    # **Says only that no request was made, which is the member's own name.**
    # It read *"EuropePMC holds no open-access full text for this article"*
    # until PR #205's review, and `NOT_ATTEMPTED` has three causes of which
    # the third contradicts that outright: a record carrying `inEPMC == "Y"`
    # and no address for the text (`_fetch_europepmc_fulltext`'s first guard)
    # is EuropePMC positively claiming to hold it. `risk_indicators` is
    # persisted, so that reached storage — the #187/#190/#191 defect, a claim
    # in EuropePMC's mouth that only one of the causes makes, reintroduced in
    # the prose half by the fix whose own argument is that *"(full text
    # unavailable)"* was false for `REQUEST_FAILED`.
    #
    # Keyed on the enum the prose can only be as precise as the member, so a
    # member conflating three causes gets the line true of all three. Making
    # the third its own member is filed rather than taken here.
    FullTextStatus.NOT_ATTEMPTED: "Full text not scanned (no EuropePMC full-text request was made)",
    FullTextStatus.SEARCH_FAILED: (
        "Full text not scanned (the EuropePMC search produced no answer)"
    ),
    FullTextStatus.NOT_SERVED: "Full text not scanned (EuropePMC served none for this article)",
    FullTextStatus.REQUEST_FAILED: (
        "Full text not scanned (the request to EuropePMC produced no answer)"
    ),
    FullTextStatus.TRUNCATED: (
        "Full text not scanned (served, but the document did not arrive whole)"
    ),
    FullTextStatus.UNTERMINATED_MARKUP: (
        "Full text not scanned (served, but its markup does not terminate)"
    ),
    FullTextStatus.UNCLOSED_REGION: (
        "Full text not scanned (served, but a nested-article region is left open)"
    ),
    FullTextStatus.ENTIRELY_NESTED: (
        "Full text not scanned (served, but nothing outside a nested-article region remained)"
    ),
}

#: The other side of the same partition, named rather than defaulted. Only
#: :attr:`FullTextStatus.ANALYZED` belongs: the text was scanned, so there is
#: nothing to explain, and a line here would be noise on every successful
#: analysis. A member added later and listed in neither collection is a red
#: test rather than a silent omission — which matters because the silent
#: omission is invisible, the result simply carrying one line fewer.
_STATUSES_WITH_NO_PROVENANCE_LINE = frozenset({FullTextStatus.ANALYZED})

_INDICATOR_INDUSTRY_COI = "Industry ties disclosed in COI statement"
_INDICATOR_DATA_NOT_AVAILABLE = "Data explicitly not available"
# A prefix, completed with the repository names. `data_availability_level`
# alone cannot distinguish a hard accession from the word "github" appearing
# somewhere in the full text, and `risk_indicators` is the only channel the
# result has for that provenance — the same job `Industry funder: X` does.
_INDICATOR_DATA_DEPOSITED_PREFIX = "Data deposited: "
_INDICATOR_NO_POSTED_RESULTS = "Registered trial without posted results"
# Deliberately does not name a registry. It covers a registration in another
# registry *and* a ClinicalTrials.gov registration whose accession was missing
# or malformed; saying "registered outside ClinicalTrials.gov" would be a plain
# falsehood in the second case.
#
# **Three appending sites, two enum members** (issue #198, and the comment said
# two until PR #205's review). Both causes above are `NOT_CHECKABLE`; the third
# site is `REQUEST_FAILED` — asked, and not one accession answered — which
# shares this string because the claim a human can act on is identical and
# neither puts anything in ClinicalTrials.gov's mouth. The difference a caller
# *can* act on is *"would re-running change this?"*, and that is the enum's to
# carry, not this line's. See `TrialResultsStatus` and `docs/DECISIONS.md`.
_INDICATOR_RESULTS_NOT_CHECKABLE = (
    "Trial registration found; posted-results status could not be checked"
)

#: CrossRef holds no funder information for this DOI — an absent ``funder``
#: key, or one present with an empty array. Both are CrossRef *answering*, so
#: the line is a claim about the record and it is true.
_INDICATOR_NO_FUNDER_INFO = "No funder information in CrossRef"
#: CrossRef sent a ``funder`` this module cannot read — an object, a string, a
#: number. Split out of the line above by PR #208's review, which is issue
#: #191's rule applied one endpoint over: a body that *was* served must not be
#: reported as one that carried nothing, because "CrossRef has no funders for
#: this paper" is a claim about the paper and this is a claim about the
#: exchange. The distinction is the same one `_INDICATOR_RESULTS_NOT_CHECKABLE`
#: draws above, and it is stated here rather than shared with that line only
#: because the component differs; the grammar is deliberately identical.
_INDICATOR_FUNDERS_NOT_READABLE = "Funder information could not be read from CrossRef's response"

# ---- Rate limiting ----
_MIN_REQUEST_INTERVAL_SECONDS = 0.35

# ---- HTTP settings ----
_HTTP_TIMEOUT_SECONDS = 15.0

# Exception types that can only mean a bmlib defect, never a remote-data or
# environment failure (issue #187). Every network step in this module wraps its
# request in `except Exception`, which is right for a transport error and wrong
# for a `TypeError` — and since issue #161 the value returned from that handler
# is a determinate, persisted claim, so a bmlib bug was being stored as a
# Europe PMC absence at DEBUG.
#
# **Restated from `fulltext/service.py`, not imported**, exactly as the
# nested-article element set is: importing across would make `bmlib.fulltext` a
# runtime dependency of `bmlib.transparency`, which it is not. The deny-list
# and its reasoning belong to that module's comment; what is load-bearing is
# what is *excluded*, and three of those are counter-intuitive —
# `json.JSONDecodeError` IS a `ValueError`, `ET.ParseError` IS a `SyntaxError`,
# `RecursionError` IS a `RuntimeError`, and `OSError` is the environment.
# `NameError` carries `UnboundLocalError` in by inheritance.
#
# The two copies are pinned as agreeing by
# `TestTheRestatedBugTypesMatchTheOtherModules`; a rule stated in prose is not
# enforced — including this pointer, which named a test that has never existed
# until the review of PR #192 grepped for it.
#
# The membership is pinned by `isinstance`, never by name: replacing
# `KeyError, IndexError` with their shared base `LookupError` in *both* copies
# passed the whole suite, silently widening the deny-list to every
# `LookupError` subclass, because a set comparison sees two edited copies
# agreeing and a name-exclusion test sees a name it was not told about.
_BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
)


def _report_swallowed_exception(
    e: BaseException, *, api: str, subject: str, doing: str, ordinary: str
) -> None:
    """Report one swallowed exception at the level its *type* earns.

    **One reporter, because the two-level split is the whole of issue #187's
    rule and a second copy of it is a second place to get it wrong.** That is
    not hypothetical: :meth:`TransparencyAnalyzer._request` was given the
    split in issue #193 and the two decode layers above it kept a bare
    ``except Exception`` -> ``logger.warning``, so a response object bmlib was
    wrong about — no ``.json``, a ``.json`` that is not callable — printed as
    *"CrossRef answered 200 with a body that is not JSON"*: a ``_BUG_TYPES``
    member reported as a claim about the remote, which is #187's own defect
    inside the fix for it, one layer up.

    ERROR is ``jats_parser``'s level for the identical claim, and ``exc_info``
    is the whole of what an operator can act on — without it the report is
    "bmlib logged a TypeError". (``fulltext/service.py`` reports its own
    ``_BUG_TYPES`` member at WARNING, so it is the precedent for *continuing*
    rather than for the level.) Everything else is the environment's or the
    remote's and WARNs, rather than DEBUGs, because results are cacheable and
    nothing here retries: a failure held at DEBUG is a scoring gap stored for
    ever.

    The exception's **type** is named as well as its message in both branches:
    ``str(OSError("connection reset"))`` does not contain ``"OSError"``, and a
    ``ConnectTimeout`` and a ``ReadTimeout`` are the same line without it.

    Args:
        e: What was raised.
        api: The remote's name.
        subject: What was being asked about — a DOI, a PMID, an accession — so
            a line can be joined to a stored result.
        doing: What bmlib was doing, completing *"<api> for <subject>: <doing>
            raised ..."*. Used for the bmlib-defect branch only, where naming
            the step is what tells an operator which call site to look at.
        ordinary: What happened, completing *"<api> for <subject>: <ordinary>
            (<Type>: <message>)"*. Used for the environment/remote branch.
    """
    if isinstance(e, _BUG_TYPES):
        logger.error(
            "%s for %s: %s raised %s, which can only mean a bmlib defect: %s",
            api,
            subject,
            doing,
            type(e).__name__,
            e,
            exc_info=True,
        )
    else:
        logger.warning("%s for %s: %s (%s: %s)", api, subject, ordinary, type(e).__name__, e)


# ---- Transparency scoring weights ----
SCORE_FUNDER_INFO = 15
SCORE_COI_DISCLOSED = 10
SCORE_DATA_FULL_OPEN = 20
SCORE_DATA_ON_REQUEST = 10
SCORE_OPEN_ACCESS = 15
SCORE_CITED = 5
SCORE_TRIAL_REGISTERED = 20
SCORE_RESULTS_POSTED = 15
MAX_TRANSPARENCY_SCORE = 100

# Data-availability levels ranked by how much data sharing is *established*,
# so a second producer of `data_level` can be merged rather than having to
# assume it runs last. An explicit denial outranks silence because it is a
# finding rather than the absence of one; any positive level outranks the
# denial. `calculate_risk_level()` accepts two further levels, "restricted"
# and "not_stated", which the analyzer has never produced — they are for
# callers computing the level themselves, and are deliberately absent here so
# that nominating one raises rather than ranking at zero.
_DATA_LEVEL_RANK = {
    "unknown": 0,
    "not_available": 1,
    "on_request": 2,
    "full_open": 3,
}

# ---- Trial lookup ----
MAX_TRIAL_IDS_TO_CHECK = 3
DEFAULT_INDUSTRY_CONFIDENCE = 0.8
# Industry involvement inferred from COI text is weaker evidence than a
# structured CrossRef funder record, so it gets a moderate confidence.
TEXT_INDUSTRY_CONFIDENCE = 0.5

# An NCT id in an abstract only counts as *this* paper's own registered trial
# when it appears next to registration language. Reviews and pooled analyses
# that merely cite their constituent trials either list the numbers without such
# language or list several of them, so those are not credited. These patterns
# were calibrated against real EuropePMC abstracts (registered RCTs vs. reviews):
# they credit ~97% of genuinely registered single-trial abstracts while
# rejecting citation lists of three or more distinct trials.
_NCT_ID_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)
_REGISTRATION_CUE_RE = re.compile(
    r"clinicaltrials?\.?gov"  # ClinicalTrials.gov (tolerating a missing dot)
    r"|regist"  # register / registered / registration / registry
    r"|\bnct(?!\d)",  # "NCT" as a label ("NCT number:", "(NCT):", …), not an id
    re.IGNORECASE,
)
# Characters on either side of an NCT id scanned for registration language
# (the cue may precede — "registered under NCT…" — or follow the id —
# "NCT…; registered at ClinicalTrials.gov").
_REGISTRATION_CUE_WINDOW = 60
# A paper's own registration cites one (occasionally two linked) trial numbers;
# three or more distinct ids indicate a citation list of constituent trials.
_MAX_OWN_TRIAL_IDS = 2

# ---- COI detection patterns ----
_COI_PATTERNS = [
    "conflict of interest",
    "competing interest",
    "no conflict",
    "nothing to disclose",
    "declare no",
    "financial disclosure",
]

# ---- COI section extraction ----
# JATS containers that hold the COI/disclosure statement: <fn fn-type="COI-statement">,
# <sec sec-type="conflict">, <notes notes-type="COI-statement">, case variants, and
# either attribute quoting style (\2 pins the closing quote to the opening one).
_COI_SECTION_RE = re.compile(
    r"<(fn|sec|notes)\b[^>]*-type=([\"'])[^\"']*(?:coi|conflict|competing)[^\"']*\2"
    r"[^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Sections whose <title> names conflicts/competing interests but carry no typed attribute.
_COI_TITLED_SEC_RE = re.compile(
    r"<sec\b[^>]*>\s*<title>[^<]*(?:conflict|competing|disclosure)[^<]*</title>(.*?)</sec>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
# When the full text has no tagged COI section, scan a bounded window after
# each COI cue phrase instead of the whole document, so industry phrases in
# references or author affiliations are not misread as disclosures.
_COI_FALLBACK_WINDOW = 1000
_COI_CUE_RE = re.compile("|".join(re.escape(p) for p in _COI_PATTERNS))

# Negation cues that turn an industry phrase into a denial ("none of the
# authors served as a consultant for ... any company"). Scoped per sentence:
# ICMJE-style disclosures routinely enumerate the relationship types they
# deny, which would otherwise substring-match the disclosure keywords.
_NEGATION_RE = re.compile(r"\b(?:no|none|not|neither|nor|never|without|den(?:y|ies|ied))\b")
_SENTENCE_SPLIT_RE = re.compile(r"[.;]")

# Otherwise-industry phrases in a clearly non-industry context: being an
# employee of a university, hospital, or government body, or sitting on an
# editorial/community/safety advisory board, is a genuine disclosure but not
# an industry tie. Matched spans are blanked before keyword matching, so the
# rest of the sentence can still disclose a real industry relationship.
# Curated employer nouns only — a generic word like "institute" would excuse
# industry bodies such as the Novartis Institutes for BioMedical Research.
_NON_INDUSTRY_CONTEXT_RE = re.compile(
    r"employees? of (?:the |a |an )?(?:\w+ )?"
    r"(?:universit\w*|hospitals?|colleges?|schools?|governments?|ministr\w*"
    r"|national institutes of health|public health)"
    r"|(?:editorial|community|data safety|safety) advisory board"
    r"|advisory board of (?:the |this )?journal"
)


@dataclass(frozen=True)
class _PubMedSignals:
    """Transparency signals carried by a PubMed record.

    All three are structured publisher-supplied metadata, which is why they
    outrank the text heuristics elsewhere in this module. An empty instance is
    the result of every failure path (no PMID, unreachable, unparsable), so
    callers never have to distinguish "no signals" from "no answer".

    Attributes:
        coi_statement: A non-blank ``<CoiStatement>`` is present.
        trial_accessions: ClinicalTrials.gov NCT ids, upper-cased.
        registration_not_checkable: A registration was recorded that
            ClinicalTrials.gov cannot be asked about — either it belongs to
            another registry, or it is a ClinicalTrials.gov entry whose
            accession is missing or malformed. Registration is established
            either way; followability is the separate fact this records.
        funders: Distinct ``<Grant><Agency>`` names, in document order. PubMed
            emits one ``<Grant>`` per grant number, so a single agency funding
            four grants appears four times in the XML and once here.
        deposition_databanks: Repository names from ``<DataBankList>`` that
            carried at least one non-blank accession, in PubMed's own
            spelling and document order, deduplicated case-insensitively.
            Names rather than a level: this class reports what the record
            said, and :func:`_merge_pubmed_signals` decides what it is worth
            — the same division `funders` already follows.
    """

    coi_statement: bool = False
    trial_accessions: tuple[str, ...] = ()
    registration_not_checkable: bool = False
    funders: tuple[str, ...] = ()
    deposition_databanks: tuple[str, ...] = ()


def _parse_pubmed_signals(xml_text: str) -> _PubMedSignals:
    """Extract transparency signals from a PubMed ``efetch`` response.

    Returns empty signals for anything unusable — malformed XML, an empty
    result set, a record without the relevant elements — so a surprising
    response degrades the analysis rather than raising into it.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        # WARNING, not DEBUG, for the reason `_request_json` gives about the
        # four JSON endpoints: the request succeeded and the *body* is what is
        # wrong, so DEBUG names the wrong stage — and holding it there left
        # PubMed the one endpoint of five whose unusable 200 was invisible by
        # default (PR #195's review). What it costs is stated at
        # `_check_pubmed`.
        logger.warning("PubMed answered 200 with a body that is not parsable XML: %s", e)
        return _PubMedSignals()

    # Only `PubmedArticle` is read. A `PubmedBookArticle` (StatPearls,
    # GeneReviews, …) carries no `<CoiStatement>` and no `<DataBankList>` in
    # its DTD, so the two signals worth having are absent by construction and
    # the record degrades to empty signals rather than being parsed for the
    # third.
    citation = root.find(".//PubmedArticle/MedlineCitation")
    if citation is None:
        return _PubMedSignals()

    # The MEDLINE DTD declares CoiStatement as (%text;)*, so inline markup
    # (<b>, <i>, <sup>, …) is legal inside it. Reading `.text` alone would miss
    # a statement that opens with a tag — "<b>Conflict of interest:</b> none" —
    # and report a disclosure as absent.
    coi_el = citation.find("CoiStatement")
    coi_statement = coi_el is not None and bool("".join(coi_el.itertext()).strip())

    accessions: list[str] = []
    registration_not_checkable = False
    # Keyed by the lowercased name so a record naming one repository twice —
    # or once as "GENBANK" and once as "GenBank" — yields one entry. The value
    # is the first spelling seen, because it is rendered to humans.
    deposition: dict[str, str] = {}
    for databank in citation.findall("Article/DataBankList/DataBank"):
        raw_name = (databank.findtext("DataBankName") or "").strip()
        name = raw_name.lower()

        if name in _DEPOSITION_DATABANK_LEVELS:
            # A repository name with no accession is an assertion with no
            # referent — nothing a reader could go and fetch — so it is not
            # the structured proof of a deposit this signal claims to be.
            if any(
                (el.text or "").strip()
                for el in databank.findall("AccessionNumberList/AccessionNumber")
            ):
                deposition.setdefault(name, raw_name)
            continue

        if name not in _TRIAL_REGISTRY_NAMES:
            continue
        # Every accession is publisher-supplied text that would be interpolated
        # into a ClinicalTrials.gov URL path, so only a well-formed NCT id is
        # ever carried forward. A ClinicalTrials.gov entry whose accession is
        # missing or malformed still establishes registration — it just cannot
        # be followed up, which is what `registration_not_checkable` records.
        usable = [
            acc
            for acc in (
                (el.text or "").strip().upper()
                for el in databank.findall("AccessionNumberList/AccessionNumber")
            )
            if _NCT_ID_RE.fullmatch(acc)
        ]
        if name == _CLINICALTRIALS_GOV and usable:
            accessions.extend(usable)
        else:
            if name == _CLINICALTRIALS_GOV:
                # Not the same story as a registration in another registry, and
                # the only place the difference is visible — the result records
                # followability, not which of the two caused it.
                logger.debug("ClinicalTrials.gov databank carried no usable accession")
            registration_not_checkable = True

    # Deduplicated: PubMed emits one <Grant> per grant number, so an agency
    # funding several grants on one paper would otherwise repeat — and each
    # repeat would add its own "Industry funder: …" line to the result.
    funders = tuple(
        dict.fromkeys(
            agency
            for agency in (
                (el.text or "").strip() for el in citation.findall("Article/GrantList/Grant/Agency")
            )
            if agency
        )
    )

    return _PubMedSignals(
        coi_statement=coi_statement,
        trial_accessions=tuple(accessions),
        registration_not_checkable=registration_not_checkable,
        funders=funders,
        deposition_databanks=tuple(deposition.values()),
    )


@dataclass
class _Analysis:
    """Everything :meth:`TransparencyAnalyzer.analyze` accumulates.

    Passed to each sub-step and mutated in place. The alternative — passing
    each value in and unpacking a tuple back out — bound a value to its name by
    position alone, so a mis-ordered unpacking was a silent, type-compatible
    swap (``industry_funding`` and ``funder_info_scored`` are both ``bool``;
    ``score`` is interchangeable with any other ``int``) and adding one signal
    meant widening several signatures.

    Mutable by design, and private: it never leaves this module, which is why
    it carries no ``to_dict()``/``from_dict()`` — the same reasoning as the
    frozen :class:`_PubMedSignals` beside it, which is a message from one
    source rather than shared state.

    Attributes:
        score: Running transparency score, uncapped until ``analyze()`` ends.
        indicators: Human-readable findings, in the order they were made.
        industry_funding: Any industry involvement was detected.
        industry_confidence: Confidence in that detection; the strongest
            evidence seen wins, regardless of arrival order.
        data_level: Data-availability level from :data:`_DATA_PATTERNS` or a
            PubMed deposition accession; the strongest evidence seen wins,
            regardless of arrival order. Set through
            :meth:`note_data_level`, never assigned.
        coi_disclosed: Tri-state — ``True`` (statement found), ``False`` (full
            text scanned, none found), ``None`` (undeterminable).
        trial_registered: A trial registration was established.
        results_compliant: Posted results were found for a registered trial.
        trial_results_status: What became of the posted-results check — see
            :class:`~bmlib.transparency.models.TrialResultsStatus`. Defaults
            to ``NOT_REGISTERED``, which is what an analysis that never
            establishes a registration should carry, and like
            ``full_text_status`` it is never ``None`` here: the carrier is
            built fresh by every analysis, so *"not recorded"* cannot arise.
        full_text_analyzed: Findings came from full text, not just an abstract.
        full_text_status: What became of the full-text attempt — see
            :class:`~bmlib.transparency.models.FullTextStatus`. Defaults to
            ``NOT_ATTEMPTED``, which is what an analysis that never reaches
            the EuropePMC step *because EuropePMC said so* (no record, or
            ``inEPMC != "Y"``) should carry. There is a third way not to reach
            it — the search itself producing no answer — and the default is
            the wrong value for that one, which is why ``analyze()``
            overwrites it with ``SEARCH_FAILED`` rather than leaving it
            (issue #193).
            Unlike the field of the same name on ``TransparencyResult`` this
            is never ``None``: the carrier is built fresh by every analysis,
            so "not recorded" cannot arise here and a default that means it
            would only be reachable by mistake.
        funder_info_scored: :data:`SCORE_FUNDER_INFO` has been spent. Named
            state rather than a positional bool, so a third funder source gets
            the once-only rule from :meth:`award_funder_info` instead of having
            to remember a convention. The field stays writable — the rule lives
            in the method, not in the type — so a source that spends the
            component by hand can still double-score it. Go through
            ``award_funder_info()``.
    """

    score: int = 0
    indicators: list[str] = field(default_factory=list)
    industry_funding: bool = False
    industry_confidence: float = 0.0
    data_level: str = "unknown"
    coi_disclosed: bool | None = None
    trial_registered: bool = False
    results_compliant: bool = False
    trial_results_status: TrialResultsStatus = TrialResultsStatus.NOT_REGISTERED
    full_text_analyzed: bool = False
    funder_info_scored: bool = False
    full_text_status: FullTextStatus = FullTextStatus.NOT_ATTEMPTED

    def award_funder_info(self) -> None:
        """Award :data:`SCORE_FUNDER_INFO` the first time any source reports funders.

        Two sources can report them — CrossRef funder records and PubMed's
        ``<GrantList>`` — and the component is worth 15 points once, not twice.
        Neither caller has to know whether the other ran first, which is what
        makes a third source safe to add.
        """
        if not self.funder_info_scored:
            self.score += SCORE_FUNDER_INFO
            self.funder_info_scored = True

    def note_industry_funder(self, name: str) -> None:
        """Record *name* as an industry funder named in structured metadata.

        The confidence is fixed at :data:`DEFAULT_INDUSTRY_CONFIDENCE` rather
        than passed in: "structured metadata" — a CrossRef funder record or a
        PubMed ``<Grant><Agency>`` — is exactly what distinguishes this from
        the weaker prose signal in :meth:`note_industry_coi`, and a caller free
        to choose the number could blur the two.

        The indicator is deduplicated. One funder is one finding however many
        sources report it, and however often a single source repeats it: both
        registries emit one record per award, so an organisation funding four
        awards on one paper appears four times upstream.
        """
        self.industry_funding = True
        self.industry_confidence = max(self.industry_confidence, DEFAULT_INDUSTRY_CONFIDENCE)
        line = f"Industry funder: {name}"
        if line not in self.indicators:
            self.indicators.append(line)

    def note_industry_coi(self) -> None:
        """Record industry ties disclosed in a full-text COI statement.

        Weaker evidence than a funder record — an inference from prose rather
        than a structured field — so it raises the confidence only to
        :data:`TEXT_INDUSTRY_CONFIDENCE` and never lowers a stronger one.
        """
        self.industry_funding = True
        self.industry_confidence = max(self.industry_confidence, TEXT_INDUSTRY_CONFIDENCE)
        self.indicators.append(_INDICATOR_INDUSTRY_COI)

    def note_data_level(self, level: str) -> None:
        """Nominate *level* as the paper's data availability; the strongest wins.

        Two sources produce this — Europe PMC's full-text pattern scan and
        PubMed's ``<DataBankList>`` deposition accessions — and neither can
        know whether the other ran first, so the field is merged by rank
        rather than assigned. A source that found nothing nominates
        ``"unknown"``, which is a no-op: finding nothing is not evidence
        against what another source found.

        Args:
            level: A key of :data:`_DATA_LEVEL_RANK`.

        Raises:
            KeyError: If *level* is not a level the analyzer produces. A typo
                must fail loudly rather than silently rank below everything.
        """
        if _DATA_LEVEL_RANK[level] > _DATA_LEVEL_RANK[self.data_level]:
            self.data_level = level


# ---- Reading a decoded JSON body ----
#
# :meth:`TransparencyAnalyzer._request_json` guarantees the body is a JSON
# *object*, and that is the whole of what a boundary can promise: every value
# inside it is still whatever the remote chose to send. Reading one as a
# mapping, a string or a number is therefore an assumption, and three of the
# four generic coercers below were measured escaping a public ``analyze()`` as
# a :data:`_BUG_TYPES` member — ``.get()`` on a list, ``.lower()`` on an
# object, ``>`` between a string and an int (issue #199).
#
# :func:`_json_bool` is the fourth and was measured differently, which is why
# it is worth naming separately: a wrong-typed boolean **raises nothing**, so
# no contract net can see it. ``bool("no")`` is ``True``, and that read
# published *"results posted"* for a ClinicalTrials.gov body stating the
# opposite — a false claim about a trial at the one site issue #194 had
# already made one for a release (PR #208's review).
#
# :func:`_epmc_records` below them is not a generic coercer at all but a
# domain walk, and it carries a rule of its own about *rank* that none of
# these four needs.
#
# They are coercers rather than guards on purpose: the caller's next line is a
# read, and a value of the wrong type is the *absence* of the value that was
# asked for, which is what the readers already do with an absent key.
#
# **They are silent, and that is a choice rather than a consequence.** An
# earlier draft justified it with *"the request itself has been reported at
# :meth:`_request_json`"*, which is false for exactly the case they exist to
# handle: a 200 carrying a well-formed object whose *value* is wrong is
# reported nowhere, at no level (PR #208's review). What rules out a line
# *here* is that it would fire per field of every malformed body; what
# ``jats_parser`` does with that shape is count and report once per article,
# and the equivalent for this module — a coercion tally on :class:`_Analysis`
# reported once per :meth:`analyze` — is filed as issue #209 rather than
# argued away.


def _json_object(value: object) -> dict[str, Any]:
    """*value* if it is a JSON object, else an empty one.

    Replaces ``x.get("k", {})``, which returns the default only for an
    **absent** key — a key present with ``null``, or with an array, hands the
    caller the wrong type and the next ``.get()`` raises. That exact defect is
    already recorded against ``fulltext``'s ``_extract_free_pdf_url`` one
    package over, which is why it is worth a named helper rather than an
    ``isinstance`` at each of the four sites.
    """
    return value if isinstance(value, dict) else {}


def _json_text(value: object) -> str:
    """*value* if it is a JSON string, else ``""``.

    ``(x.get("k") or "")`` looks like this and is not: it rescues ``null`` and
    passes an object or an array straight through to the ``.lower()`` or the
    regex that follows.
    """
    return value if isinstance(value, str) else ""


def _json_count(value: object) -> int:
    """*value* if it is a JSON integer, else ``0``.

    ``bool`` is excluded although it is an ``int`` in Python: a
    ``"cited_by_count": true`` would otherwise compare greater than zero and
    award :data:`SCORE_CITED` on a body that stated no count at all. Floats
    are excluded too — a count is not fractional, and accepting one would make
    the helper's name a lie about what it validated.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _json_bool(value: object) -> bool | None:
    """*value* if it is a JSON boolean, else ``None``.

    ``None`` and not ``False``, because both callers need *"the remote did not
    say"* to be a third answer rather than a negative one. This is the only
    coercer whose absent value is not falsy, and the reason is that the other
    three replace a read that **raised**: a wrong-typed boolean raises
    nothing, so the defect it prevents is a value read *wrongly* and stored,
    which no contract net can see (PR #208's review).

    Measured, at both call sites: ``{"hasResults": "no"}`` scored
    :data:`SCORE_RESULTS_POSTED` and stored
    :attr:`TrialResultsStatus.POSTED` with ``trial_results_compliant=True``
    — ClinicalTrials.gov stating *no results* published as *results posted* —
    and ``{"is_oa": "false"}`` awarded :data:`SCORE_OPEN_ACCESS`. Both are
    truthy strings, so ``bool()`` inverts the remote's answer rather than
    merely losing it, which is worse than the absence it looks like.
    """
    return value if isinstance(value, bool) else None


def _epmc_records(epmc: object) -> list[dict[str, Any]]:
    """The leading run of object records in a EuropePMC search body.

    ``epmc["resultList"]["result"]`` is walked by **three** readers —
    :func:`_find_trial_ids`, :func:`_pmid_from_epmc` and
    :meth:`TransparencyAnalyzer._check_europepmc` — which each hand-rolled it,
    and every level of it is a value the remote chose: ``resultList`` may be
    an array, ``result`` may be an object (``result[0]`` then raised
    ``KeyError``), and a record may be a bare string.

    Three copies is well past this repository's threshold for a helper, and
    here it also puts the readers on one answer: they used to agree by being
    written the same way. PR #208's review found the third — the count in this
    docstring said *two*, and :func:`_pmid_from_epmc` was left hand-rolled in
    the commit that wrote the sentence, so four ``_BUG_TYPES`` members still
    escaped :meth:`TransparencyAnalyzer.analyze` on any DOI-only analysis.

    **The list is truncated at the first non-object, never filtered** (PR
    #208's review). EuropePMC returns best-match-first and every reader takes
    ``records[0]`` as *this paper*, so an index is a rank: dropping a
    malformed record promotes the one behind it, and a filter whose head was
    bad silently made a **different article** the subject — its trial
    accession, its PMID sent on to efetch, its abstract scanned for COI. That
    is worse than the ``KeyError`` it replaced, which at least said so.
    Stopping keeps every record at its own index, which is the invariant a
    later reader of ``records[1]`` would need too.
    """
    result = _json_object(_json_object(epmc).get("resultList")).get("result")
    if not isinstance(result, list):
        return []
    records: list[dict[str, Any]] = []
    for record in result:
        if not isinstance(record, dict):
            break
        records.append(record)
    return records


def _find_trial_ids(epmc: dict | None) -> list[str]:
    """Return NCT ids that identify *this* paper's own registered trial.

    The abstract is scanned for ``NCT`` accession numbers, but a match is only
    credited as the paper's own registration when it appears next to
    registration language (see :data:`_REGISTRATION_CUE_RE`). Abstracts that
    list three or more distinct ids are treated as citation lists — e.g. a
    systematic review or pooled analysis enumerating its constituent trials —
    and return nothing, so a review is not credited for registrations that
    belong to studies it merely cites.

    *epmc* is the record ``analyze()`` already fetched, and this reads it
    rather than fetching anything: no client, and **no fallback query**. It
    was a method taking a client, documented as *"falling back to a fresh
    query only if it was not supplied, so the same search is not issued twice
    per document"* — decided with ``if data is None``, which is exactly what a
    **failed** search returns, so during an outage the identical failing
    search went out twice and (since the failure gained a log line) was
    reported twice for one document (issue #202).

    A sentinel telling the two ``None``s apart would have fixed that. Deleting
    the fallback fixes it structurally, and is available because ``analyze()``
    is the only caller and has always had the record in hand: a trial id
    scraped out of an abstract bmlib never received is not a thing that can
    happen. ``None`` therefore returns nothing, quietly — the outage has
    already been reported where it happened.
    """
    records = _epmc_records(epmc)
    if not records:
        return []

    # Strip XML/HTML markup so cue detection is not thrown off by tags.
    abstract = _TAG_RE.sub(" ", _json_text(records[0].get("abstractText")))

    # Deduplicate while preserving order, normalizing to the canonical
    # upper-case form ClinicalTrials.gov uses.
    distinct_ids = list(dict.fromkeys(m.upper() for m in _NCT_ID_RE.findall(abstract)))
    if not distinct_ids or len(distinct_ids) > _MAX_OWN_TRIAL_IDS:
        return []

    for match in _NCT_ID_RE.finditer(abstract):
        window = abstract[
            max(0, match.start() - _REGISTRATION_CUE_WINDOW) : match.end()
            + _REGISTRATION_CUE_WINDOW
        ]
        if _REGISTRATION_CUE_RE.search(window):
            return distinct_ids

    return []


def _note_full_text_provenance(analysis: _Analysis) -> None:
    """Record what became of the full text, as prose that cannot be retracted.

    A module-level function for :func:`_merge_pubmed_signals`' reason — it
    needs no HTTP client — and called from ``analyze()`` **after every step
    has run**, which is the whole design rather than an ordering detail. The
    defect issue #203 is about is that this information used to be a
    parenthetical inside a COI line, which
    :data:`_INDICATORS_RETRACTED_BY_PUBMED_COI` removes wholesale when PubMed
    supplies a ``<CoiStatement>``; appending here puts it structurally beyond
    that retraction, where keeping it out of the set would leave the rule
    enforced by set membership — and membership is exactly what went wrong
    twice already (issues #161 and #193).

    Silent for :attr:`FullTextStatus.ANALYZED`, the one member with nothing to
    explain. Every other member must have a line: see
    :data:`_FULL_TEXT_PROVENANCE_INDICATORS`.
    """
    # **Subscripted, and the exclusion set is what guards it** — the
    # `_DEPOSITION_DATABANK_LEVELS` idiom in `_merge_pubmed_signals` below,
    # for its reason: a
    # `.get()` here is silent for a member listed in neither collection, and
    # what it drops is invisible, the result simply carrying one line fewer
    # (PR #205's review). Subscripting makes the partition load-bearing at
    # runtime, so `test_every_status_says_what_happened` becomes the second
    # protection rather than the only one. A `KeyError` out of `analyze()` is
    # the right cost: it can only mean a member was added to the enum and to
    # neither collection, which is a defect in this module.
    if analysis.full_text_status in _STATUSES_WITH_NO_PROVENANCE_LINE:
        return
    analysis.indicators.append(_FULL_TEXT_PROVENANCE_INDICATORS[analysis.full_text_status])


def _merge_pubmed_signals(pubmed: _PubMedSignals, analysis: _Analysis) -> None:
    """Fold PubMed's structured signals into *analysis*.

    A module-level function rather than a method because it needs no HTTP
    client; trial registration is handled separately, in
    :meth:`TransparencyAnalyzer._check_trial_registration`, because that step
    does.

    Each score component is awarded at most once. ``coi_disclosed is not
    True`` is a reliable guard rather than an incidental one: the only
    branch that sets ``True`` is the same branch that adds
    ``SCORE_COI_DISCLOSED``.

    ``<DataBankList>`` deposition accessions nominate the data-availability
    level :data:`_DEPOSITION_DATABANK_LEVELS` maps their repository to,
    through :meth:`_Analysis.note_data_level`, so the strongest evidence wins
    whichever source ran first. The component itself is scored later, by
    :func:`_score_data_availability`.
    """
    if pubmed.coi_statement and analysis.coi_disclosed is not True:
        analysis.coi_disclosed = True
        analysis.score += SCORE_COI_DISCLOSED
        # Every one of those lines was written before PubMed was consulted and
        # would now contradict the result, so they are retracted rather than
        # left to be reconciled by whoever reads the indicators. Read from
        # `_INDICATORS_RETRACTED_BY_PUBMED_COI` rather than enumerated here:
        # this site is where the third one went missing.
        analysis.indicators = [
            ind for ind in analysis.indicators if ind not in _INDICATORS_RETRACTED_BY_PUBMED_COI
        ]
        analysis.indicators.append(_INDICATOR_COI_IN_PUBMED)

    # A missing <CoiStatement> deliberately does not demote `None` to
    # `False`: it means the publisher supplied no statement to PubMed, not
    # that the paper carries none, and `False` would trigger the
    # missing-COI downgrade on no evidence.

    if pubmed.funders:
        analysis.award_funder_info()
        for agency in pubmed.funders:
            if _is_industry_funder(agency):
                # A grant agency is structured metadata, the same class of
                # evidence as a CrossRef funder record — not the weaker
                # signal inferred from COI prose. CrossRef may already have
                # named this funder; note_industry_funder() deduplicates.
                analysis.note_industry_funder(agency)

    if pubmed.deposition_databanks:
        for name in pubmed.deposition_databanks:
            # The parser collected the name; deciding what a deposit into it
            # is worth is this step's job, which is why the signals carry
            # names rather than a level. Subscripted rather than defaulted:
            # the parser admits a name only if it is a key here, so a name
            # that is not one is a bug in this module and raises, the same
            # way `note_data_level()` raises on a level outside the ranking.
            analysis.note_data_level(_DEPOSITION_DATABANK_LEVELS[name.lower()])
        # Written whether or not the level above won: it reports what PubMed
        # said, which stays true either way.
        analysis.indicators.append(
            _INDICATOR_DATA_DEPOSITED_PREFIX + ", ".join(pubmed.deposition_databanks)
        )


def _score_data_availability(analysis: _Analysis) -> None:
    """Award the data-availability component once, for the level that won.

    Called by :meth:`TransparencyAnalyzer.analyze` after every sub-step has
    nominated, rather than by the step that finds a level. With two producers
    — Europe PMC's text scan and PubMed's deposition accessions — scoring at
    the point of discovery would either spend the component twice or spend it
    on a level later beaten. The sub-steps only ever call
    :meth:`_Analysis.note_data_level`, which nominates and cannot add points,
    so neither is capable of scoring this component at all.

    Unlike :meth:`_Analysis.award_funder_info`, this carries no
    "already spent" flag: what holds it to one award is that ``analyze()``
    calls it from exactly one place, so a re-score or retry path added there
    would have to bring its own guard.

    Deferring is also what keeps :data:`_INDICATOR_DATA_NOT_AVAILABLE`
    honest: the line is written only if that level survived the merge, so it
    never has to be retracted the way the PubMed COI lines are.
    """
    if analysis.data_level == "full_open":
        analysis.score += SCORE_DATA_FULL_OPEN
    elif analysis.data_level == "on_request":
        analysis.score += SCORE_DATA_ON_REQUEST
    elif analysis.data_level == "not_available":
        analysis.indicators.append(_INDICATOR_DATA_NOT_AVAILABLE)


# ---- nested articles ----
# A <sub-article> or <response> is a complete article of its own — its own
# <front>/<front-stub>, its own <body>, its own back matter — nested inside the
# one that carries it, and nothing in it is this article's. Peer-review rounds,
# author responses, SciELO's translated full text, meeting abstracts and Europe
# PMC's own injected "associated-data" block all arrive that way, and reviewers
# write in exactly the vocabulary the scans below hunt for: a round's "the
# reviewers declare no competing interests" was read as the *article's*
# disclosure, and a round's data-availability statement as the article's data
# level (issue #119).
#
# The two-element set is complete, and structurally so: of JATS's ~295 elements
# exactly three admit <front>/<front-stub> and <body>, and the third is
# <article> itself. The disjunction is what makes the count three — <response>
# is modelled (front-stub, body?, back?, …) and admits <front-stub> only, so
# "admits <front> and <body>" states a rule that puts one of these two elements
# outside it. `bmlib.fulltext.jats_parser` makes the same rule with the same
# argument at greater length — read `_NESTED_ARTICLE_ELEMENTS` there before
# changing this — and it is restated rather than imported so that
# `bmlib.transparency` needs nothing from `bmlib.fulltext`;
# `TestTheRestatedSetMatchesTheParsers` is what keeps the two in step, since a
# rule enforced by prose is not enforced. A tuple rather than the parser's
# frozenset because it is joined into a regex alternation below and needs a
# deterministic order. Also structural rather than by @article-type, which is
# CDATA #IMPLIED, has four published vocabularies that disagree, and is
# deposited in none of them.
#
# Measured over PMC's `oa_comm` baseline package PMC012xxxxxx (2025-06-26,
# 97,909 open-access articles): 3,382 (3.45%) carry a region this removes —
# 3,377 a <sub-article>, and 5 more a top-level <response response-type="reply">
# with no <sub-article> at all — and for 602 of those (0.61% of the corpus) at
# least one of the four scan outputs below moves once the regions go: 499 the
# data-availability level, 125 the COI cue phrase (4 of them flipping
# `coi_disclosed`), 6 the industry-COI signal, 1 the tagged COI section. The
# industry row moves more than an indicator string: `note_industry_coi()` also
# sets `industry_funding` and raises `industry_confidence`, both of which reach
# `calculate_risk_level`. None of the five <response> articles is among the
# 602, so that element is *rare*, not *absent* — the first cut of this comment
# said it measured zero, which was a grep for the other element.
#
# Nesting is exercised rather than defensive: 98 of the 3,382 carriers nest
# (96 at depth 2, 2 at depth 3). Two populations beside it measure *empty*, and
# say so rather than implying a shape someone has seen. No article leaves a
# region open (0 of 97,909), so the refusal path guards a truncated body. And
# no article is emptied by the removal: all 3,389 carriers across this corpus
# and an 880-article Europe PMC draw keep their <body>, the least of them
# retaining 32.2% of its bytes — so `_check_europepmc`'s truthiness test is not
# standing in for a size rule.
_NESTED_ARTICLE_ELEMENTS = ("sub-article", "response")

# One lexer for the whole scan. In well-formed XML a literal "<" can only open
# markup — a "<" in text or in an attribute value has to be escaped — so a
# comment, a CDATA section, a processing instruction and the DOCTYPE's internal
# subset are the *complete* set of places the characters "<sub-article" can
# appear without being a start tag. Each is matched as a token and skipped,
# which is what makes this exact rather than merely careful: the alternative is
# a list of hazards someone thought of, and a publisher's comment naming the
# element would then cost the article its whole remaining text. Case-sensitive,
# because XML is, and DOCTYPE/CDATA are spelled by the spec.
#
# The converse of that argument does *not* hold for ">", which is legal
# unescaped in an attribute value and inside a DOCTYPE's system literal, and
# "]" is legal inside an entity's replacement text. Both the tag branch and the
# doctype branch therefore step over quoted literals rather than scanning to the
# first ">", and the internal subset ends at the "]" that precedes the ">"
# rather than at the first one. Scanning to the first ">" cost a whole article
# each time — fail-closed, but a refusal is still a full text discarded.
#
# Which populations these four have is worth stating, because only one of them
# has ever been seen to fire. Over the 97,909-article baseline: the comment
# token fires on 3 articles, where Springer deposits an <authorqueries> block
# commented out and its <aq> children carry <response> elements — but that is
# the *archive* rendition, and this function reads Europe PMC's `fullTextXML`,
# which serves those same three articles with no comments at all and carries a
# comment in 0 of an 880-article draw against 25.6% of the archive. CDATA
# sections appear in 159 articles and internal subsets in none, and neither
# they nor the processing instructions ever hold these element names. So all
# four are kept for the structural argument and none of them for a measured
# population on this module's own input.
#
# A fifth branch refuses the document, and it is what bounds the work (issue
# #160). Each of the four above scans to end-of-string when its terminator is
# absent, and `finditer` then retries at every later opener, so the lex was
# quadratic in the input: 256 kB of a repeated `<!DOCTYPE a[` took 33.6s and
# 224 kB of an unterminated tag 33.3s, every doubling costing about four times
# the last (the issue's own 22.9s is the first shape on another machine),
# against 4-9 ms for the three largest well-formed articles in the corpus at
# 2.9-3.4 MB. `_HTTP_TIMEOUT_SECONDS` bounds the request and nothing bounded
# this, so a truncated HTTP-200 body did not fail — it stalled, reaching
# neither the refusal below nor its warning. Slow was not the whole of it:
# with no branch matching, the construct's own content was then read as this
# article's markup, which is what the four exist to prevent. Refusing at the
# first unterminated opener costs one failed scan and stops, 0.001-0.004s for
# those same two shapes. The issue's other two remedies — a size cap and a
# multiple of the input length — each wanted a constant nobody had drawn, and
# real articles reach 3.4 MB; this one needs none.
_NESTED_ARTICLE_ALTERNATION = "|".join(re.escape(name) for name in _NESTED_ARTICLE_ELEMENTS)
# What each opener is called when the refusal names it, keyed on the whole
# opener rather than on what the refusal branch captures. That branch leaves
# the "<" outside its group, for the prefix reason argued at the branch below,
# so keying on the bare `!--` would make this table read as a fingerprint of
# that optimisation rather than as a list of constructs. (The keys are never
# printed — the message interpolates this table's *value* and the
# reconstructed opener — so the choice is about what a reader of this dict
# sees, not what an operator does.) Anything not here is one of the two
# element tags, whose own text the message carries.
_UNTERMINATED_OPENER_NAMES = {
    "<!--": "comment",
    "<![CDATA[": "CDATA section",
    "<?": "processing instruction",
    "<!DOCTYPE": "doctype",
}

# The non-tag half of the refusal branch is *derived* from that table, so the
# `.get(opener, "tag")` fallback below is provable rather than checked by
# hand: every alternative here is either a key or one of the two elements.
# Stating the rule and enumerating it separately is how a construct added to
# one and not the other would come out labelled "tag" — with the branch count
# still 6, so `test_every_branch_of_the_lexer_opens_with_the_literal` would
# not notice. Keys are matched in insertion order, and none is a prefix of
# another; a future one that is must be ordered longest-first.
_UNTERMINATED_OPENERS = "|".join(re.escape(opener[1:]) for opener in _UNTERMINATED_OPENER_NAMES)

# The four groups are *named*: `closing`, `element` and `attributes` are read
# below to tell a start tag from an end tag and from a self-closing one, and
# `unterminated` to tell either from a construct that never closes. Every
# branch must leave the groups that are not its own unset. Positional groups
# made that a property of the pattern's shape — a group added to any earlier
# branch would have silently made a comment look like a start tag, with
# nothing failing. `test_each_branch_sets_only_its_own_groups` is the guard,
# for the same reason the set has one.
_NESTED_ARTICLE_TOKEN_RE = re.compile(
    r"<!--.*?-->"  # comment
    r"|<!\[CDATA\[.*?\]\]>"  # CDATA section
    r"|<\?.*?\?>"  # processing instruction
    # doctype: quoted literals stepped over, internal subset closed at the "]"
    # that precedes the ">" rather than at the first one
    r"|<!DOCTYPE(?:[^>\"'\[]|\"[^\"]*\"|'[^']*')*+(?:\[.*?\]\s*)?>"
    # a start, end or self-closing tag of one of the two elements. The name is
    # followed by a negative lookahead rather than \b, because \b is a boundary
    # at "-", "." and ":" — all legal in an XML name — so <response-note> and
    # <sub-article-x> matched, and stripped prose no JATS element owns. The
    # attributes are one possessive run rather than a lazy one: a lazy star
    # over this alternation backtracks quadratically on a tag that never
    # closes, and self-closing is then read off the run's last character
    # instead of from a second group, which a "/" inside a quoted value would
    # otherwise have to be kept out of.
    r"|<(?P<closing>/?)(?P<element>" + _NESTED_ARTICLE_ALTERNATION + r")(?![-.:\w])"
    r"(?P<attributes>(?:[^>\"']|\"[^\"]*\"|'[^']*')*+)>"
    # Last, and so reached only where every branch above it failed: a bare
    # *opener*. In well-formed XML each of the five terminates, so this branch
    # cannot fire on the input the contract describes, and
    # `test_a_well_formed_construct_never_reaches_the_refusal_branch` is the
    # negative control that says so directly. It is not the only thing
    # standing there — moving this branch to the front of the alternation
    # reddens 31 tests, 27 of them in `TestANestedArticleIsNotThisArticles`,
    # which predates the branch — so the control is kept for stating the
    # property outright rather than for being the sole guard. An earlier
    # draft claimed it broke no other test; that was never measured.
    #
    # The "<" sits *outside* the group on purpose. `sre` derives a prefix for
    # the whole pattern only when every top-level branch starts with the same
    # literal, and then skips from "<" to "<" instead of trying the pattern at
    # every position; a branch opening with a group defeats that analysis.
    #
    # Three configurations, and naming them is load-bearing because two were
    # once conflated: over 7.8 MB of real articles, **13.4 ms** with no
    # refusal branch at all, **26.6 ms** with it and the literal outside the
    # group, **191 ms** with it inside. So the *placement* penalty — the two
    # forms that differ by two characters — is **7.2x**; the guard's own cost
    # against having none is the 1.9x under Raises, and the two must not be
    # multiplied into one figure. Re-measured on a re-derivable draw (the
    # first 880 articles of `PMC10030002_PMC10040000.xml.gz`, 90.8 MB, the
    # served rendition this module reads), nine interleaved runs, best of
    # each: 165 -> 297 -> 1,959 ms, so **1.80x** and **6.6x**. Interleave
    # them: run-to-run drift on one machine reached 27% here, which is larger
    # than the difference between two of the sampled ratios, so a figure
    # taken from separate runs is not one. The ratios move with the sample;
    # the ordering and the magnitude do not.
    #
    # Factoring the alternatives *within* the group recovers ~8% of the
    # penalty and not the penalty, so it is the group boundary and not the
    # shape of what follows.
    # `test_every_branch_of_the_lexer_opens_with_the_literal` is the guard,
    # since prose is not enforcement.
    r"|<(?P<unterminated>"
    + _UNTERMINATED_OPENERS
    + r"|/?(?:"
    + _NESTED_ARTICLE_ALTERNATION
    + r")(?![-.:\w]))",
    re.DOTALL,
)


#: The root element's end tag. A served ``fullTextXML`` body that does not
#: contain it did not arrive whole (issue #183) — see the completeness check
#: in :meth:`TransparencyAnalyzer._fetch_europepmc_fulltext` for why presence
#: rather than position, and for the corpus counts behind that.
_ROOT_END_TAG = "</article>"


@dataclass(frozen=True)
class _FullTextFetch:
    """What one full-text fetch produced, and what became of it.

    Frozen and private, like :class:`_PubMedSignals` beside it: a message from
    one step rather than shared state, which is why it carries no
    ``to_dict()``. Two fields because "no full text" was several different
    claims collapsed onto one ``None`` — the whole of issue #161 — and the
    caller needs the reason to choose an honest indicator and to store it.

    ``text`` is non-``None`` if and only if ``status`` is
    :attr:`~bmlib.transparency.models.FullTextStatus.ANALYZED`. That is not
    enforced here: this type never leaves the module and has one producer,
    where :class:`~bmlib.transparency.models.TransparencyResult` is
    constructed by downstream projects and enforces the matching rule in
    ``__post_init__``.
    """

    text: str | None
    status: FullTextStatus


class _UnterminatedMarkupError(ValueError):
    """A construct in the served body never terminates, so it cannot be lexed.

    Raised by :func:`_strip_nested_articles` and caught at its one call site,
    where it becomes a WARNING and a fall back to the abstract. It is not a
    bmlib defect and not a publisher's deposit either: **0 of 98,789
    articles** carries an unterminated construct — every article of PMC's
    `oa_comm` `PMC012xxxxxx` baseline package (97,909, archive rendition) and
    of an 880-article Europe PMC draw (served rendition) — so what reaches
    this is a body truncated or corrupted in transit, an HTTP 200 being no
    promise that the whole document arrived.

    An exception rather than the ``None`` the function already returns,
    because the two refusals are different claims and an operator acts on them
    differently: an unclosed region is a document bmlib will not segment, and
    this is a document that did not arrive. Collapsing them onto one return
    value would put the module's own issue #161 shape one level down —
    "refused" and "never served" reading identically downstream — and the
    message names the construct and the offset, which nothing downstream could
    re-derive without lexing the body a second time.
    """


def _strip_nested_articles(xml: str) -> str | None:
    """Return *xml* with every nested-article region removed.

    A ``<sub-article>`` or ``<response>`` region — the element, its content and
    its end tag — is cut out, and the text either side of it is kept verbatim,
    so the JATS-tagged containers the COI scan matches on survive untouched.

    Regions are held as a **stack of element names**, not as a flag and not as
    a bare count, because JATS nests them: a ``<response>`` sits inside the
    ``<sub-article>`` it answers, and an inner end tag would otherwise
    re-admit the rest of the outer round as the article's own prose. Nesting
    is measured, not hypothetical: 98 of the 3,382 carriers in the baseline
    corpus nest. The names are what a count alone could not give — see
    Returns. A self-closing ``<sub-article/>`` opens nothing.

    Args:
        xml: A ``fullTextXML`` body as Europe PMC served it. Assumed
            well-formed, which is what the caller is served. What the
            differential over the 98,789 articles of both corpora could
            establish is narrower than that, and is all it is quoted for
            here: none of them carries an unterminated construct, and none
            leaves a region open. Well-formedness itself is neither checked
            here nor measured there. The assumption is no longer
            unenforced: a construct that never terminates refuses the
            document (see Raises), which is both what bounds the running time
            and what stops that construct's own content being read as
            markup. It is still not a well-formedness check — nothing here
            would notice a mismatched ``<p>`` — so a caller handing this
            something other than served XML still owns the rest. One shape of
            the hazard this guard was built for is still not reached *here*: a
            body truncated *between* tags opens no unterminated construct,
            leaves no region open and empties nothing, so it passes this
            function unremarked. It is refused at the call site instead, by a
            separate completeness check — see
            :meth:`TransparencyAnalyzer._fetch_europepmc_fulltext`, which owns
            the ordering argument for why that check runs after this one
            (issue #183).

    Raises:
        _UnterminatedMarkupError: A comment, CDATA section, processing
            instruction, doctype or nested-article tag opens and never closes,
            naming which and where. Only a document expat would reject can
            hold one, and none of the 98,789 articles in the two corpora does;
            what reaches it is a body truncated in transit.

    Returns:
        The article's own markup, or ``None`` if a region is left open at the
        end of the document — the caller then has no full text rather than a
        guess. Both other readings are worse: scanning the tail is the defect
        itself, and dropping it silently manufactures the
        ``No COI disclosure found in full text`` finding, which is what
        triggers the missing-COI downgrade. An unmatched *end* tag **at depth
        0** is not an imbalance in this sense — no nested prose reaches the
        scans through one — so it is ignored rather than costing the article a
        signal it really carries. An end tag *inside* an open region is a
        different matter and is matched against the element that opened it
        (issue #160): as a bare depth, `</response>` closed a region a
        <sub-article> had opened, and the rest of the outer round came back as
        this article's prose — issue #119's own defect, reached through the
        fix for it. A mismatch closes nothing, so a region left open by one is
        refused here like any other.
    """
    kept: list[str] = []
    # The open regions, innermost last. A bare count read `</response>` as
    # closing a region a <sub-article> had opened, and the rest of the outer
    # round then came back as the article's own prose — the defect issue #119
    # removed, from inside the fix for it. Names cost one list.
    open_elements: list[str] = []
    resume_at = 0
    for token in _NESTED_ARTICLE_TOKEN_RE.finditer(xml):
        unterminated = token.group("unterminated")
        if unterminated is not None:
            opener = "<" + unterminated
            # The first one wins and the scan stops: continuing is the
            # quadratic half of issue #160, and there is nothing left to be
            # right about once the markup cannot be located.
            raise _UnterminatedMarkupError(
                f"unterminated {_UNTERMINATED_OPENER_NAMES.get(opener, 'tag')} "
                f"({opener!r}) at offset {token.start()}"
            )
        closing = token.group("closing")
        if closing is None:
            continue  # a comment, CDATA section, PI or doctype: not markup we route on
        if closing:
            # A mismatch is ignored rather than refused here: at depth 0 it is
            # the harmless stray end tag the docstring scopes, and inside a
            # region it leaves that region open, which the refusal below
            # catches. Either way no nested prose reaches the scans.
            if open_elements and open_elements[-1] == token.group("element"):
                open_elements.pop()
                if not open_elements:
                    resume_at = token.end()
            continue
        if token.group("attributes").endswith("/"):
            continue  # self-closing: opens nothing
        if not open_elements:
            kept.append(xml[resume_at : token.start()])
        open_elements.append(token.group("element"))
    if open_elements:
        return None
    kept.append(xml[resume_at:])
    return "".join(kept)


def _extract_tagged_coi_text(full_text: str) -> str:
    """Return the text of JATS-tagged COI containers, tag-stripped and lowercased.

    Returns an empty string when the text carries no tagged COI section. A
    non-blank result is structural proof that the paper has a COI/disclosure
    statement, regardless of its wording (issue #13).
    """
    sections = [m.group(3) for m in _COI_SECTION_RE.finditer(full_text)]
    sections += [m.group(1) for m in _COI_TITLED_SEC_RE.finditer(full_text)]
    if not sections:
        return ""
    return _TAG_RE.sub(" ", " ".join(sections)).lower()


def _extract_coi_text(full_text: str, tagged: str | None = None) -> str:
    """Return the COI/disclosure portion of *full_text*, tag-stripped and lowercased.

    Prefers JATS-tagged COI containers (see :func:`_extract_tagged_coi_text`);
    falls back to fixed-size windows following each COI cue phrase (see
    :data:`_COI_PATTERNS`) when the tagged text is blank — a whitespace-only
    tagged section proves nothing, so an untagged disclosure elsewhere in the
    text must still be found. Returns an empty string when no COI-like region
    is found. Pass *tagged* to reuse an already-computed
    :func:`_extract_tagged_coi_text` result instead of rescanning.

    Known limitation: a fallback window is a fixed span, so it can bleed past
    the end of a short disclosure into whatever follows (acknowledgements,
    references). Accepted trade-off, matched by the moderate
    :data:`TEXT_INDUSTRY_CONFIDENCE` given to text-derived signals.
    """
    if tagged is None:
        tagged = _extract_tagged_coi_text(full_text)
    if tagged.strip():
        return tagged

    text = _TAG_RE.sub(" ", full_text).lower()
    windows = [text[m.start() : m.end() + _COI_FALLBACK_WINDOW] for m in _COI_CUE_RE.finditer(text)]
    return " ".join(windows)


def _discloses_industry_ties(coi_text: str) -> bool:
    """Return True when a COI sentence discloses (not denies) industry ties.

    A sentence counts only when it contains an industry disclosure phrase
    (see :data:`_INDUSTRY_COI_KEYWORDS`) and no negation cue, so an
    enumerated denial ("none of the authors served as a consultant for …")
    is not misread as a disclosure. A genuine disclosure alongside a denial
    sentence still counts, since sentences are scored independently.
    Clearly non-industry contexts (see :data:`_NON_INDUSTRY_CONTEXT_RE` —
    university/government employment, editorial boards) are blanked out
    before matching, so they neither trigger a sentence nor mask an industry
    tie disclosed alongside them.

    This is keyword matching, not entity recognition: an unlisted
    non-industry employer ("employee of the World Bank") still flags. That
    residual fuzziness is why text-derived signals carry only
    :data:`TEXT_INDUSTRY_CONFIDENCE`.
    """
    for sentence in _SENTENCE_SPLIT_RE.split(coi_text):
        sentence = _NON_INDUSTRY_CONTEXT_RE.sub(" ", sentence)
        if any(kw in sentence for kw in _INDUSTRY_COI_KEYWORDS) and not _NEGATION_RE.search(
            sentence
        ):
            return True
    return False


# ---- Data availability patterns ----
# Order matters: matching stops at the first hit, so the negated form
# ("not available") is checked before the "…upon request" phrases. Otherwise a
# statement like "data are not available upon reasonable request" would match
# "upon reasonable request" and be scored as if data sharing were offered.
_DATA_PATTERNS: dict[str, str] = {
    "not available": "not_available",
    "zenodo": "full_open",
    "figshare": "full_open",
    "dryad": "full_open",
    "github": "full_open",
    "available upon request": "on_request",
    "upon reasonable request": "on_request",
}


def _pmid_from_epmc(epmc: dict | None) -> str | None:
    """Return the PMID carried by an already-fetched Europe PMC record.

    Lets a DOI-only analysis reach PubMed without spending an extra request to
    resolve the identifier.

    **This was the third hand-rolled walk of ``resultList.result``, and the
    one issue #199's fix missed** (PR #208's review). It is reached from
    :meth:`TransparencyAnalyzer.analyze` as ``pmid or _pmid_from_epmc(epmc)``,
    so it runs only when the caller passed no PMID — which is exactly the path
    the issue's end-to-end net never drove, every row of it supplying one. Four
    shapes a remote can legally answer 200 with still escaped a public
    ``analyze()`` here: ``AttributeError`` for a ``resultList`` that is an
    array or a present ``null``, ``KeyError: 0`` for a ``result`` that is an
    object, and ``TypeError`` for one that is a scalar. Going through
    :func:`_epmc_records` closes all four and is what makes the module's claim
    true of the DOI-only path as well.

    The identifier is type-checked rather than ``str()``-ed for a smaller
    reason that is the same shape: ``str({"a": 1})`` is truthy and would be
    sent to NCBI's efetch as the literal ``"{'a': 1}"``. An ``int`` is still
    accepted — EuropePMC serves the field as a string, and refusing a number
    would narrow behaviour on well-formed input for no gain.
    """
    records = _epmc_records(epmc)
    if not records:
        return None
    pmid = records[0].get("pmid")
    if isinstance(pmid, bool):
        return None
    if isinstance(pmid, int):
        return str(pmid)
    return _json_text(pmid) or None


class TransparencyAnalyzer:
    """Analyze transparency of a biomedical publication via external APIs.

    Args:
        email: Contact email for API politeness headers.
        pubmed_api_key: Optional NCBI API key. Sent with the PubMed
            ``efetch`` request, which moves it out of NCBI's 3 requests/second
            per-IP bucket and into the key's 10 requests/second one — so
            bmlib's traffic stops competing with the calling application's own
            E-utilities requests. It does not change bmlib's own pacing, which
            stays at the interval shared with the other APIs.
        settings: Transparency settings (thresholds, etc.).
    """

    def __init__(
        self,
        email: str = "user@example.com",
        pubmed_api_key: str | None = None,
        settings: TransparencySettings | None = None,
    ) -> None:
        self.email = email
        self.pubmed_api_key = pubmed_api_key
        self.settings = settings or TransparencySettings()
        self._last_request: float = 0.0
        # Rate limiting throttles a shared remote API, so the interval is
        # enforced across all threads using this analyzer — hence a lock
        # rather than per-thread state.
        self._rate_limit_lock = threading.Lock()
        # Reachability, by contrast, describes a single analysis. It is held
        # per-thread so that concurrent analyze() calls (which
        # TransparencySettings.max_concurrent_analyses invites) cannot
        # contaminate each other: without this, a thread whose APIs were all
        # down inherits a concurrent thread's success and gets scored 0 /
        # HIGH instead of UNKNOWN, wrongly triggering a tier downgrade.
        self._local = threading.local()

    @property
    def _api_reachable(self) -> bool:
        """Whether any external API answered during *this thread's* analysis.

        Set True by any query helper that receives a 200 response, so a run
        in which every external API was unreachable can be reported as
        UNKNOWN rather than scored 0 (which would read as HIGH risk).
        """
        return getattr(self._local, "api_reachable", False)

    @_api_reachable.setter
    def _api_reachable(self, value: bool) -> None:
        self._local.api_reachable = value

    def analyze(
        self,
        document_id: str,
        *,
        pmid: str | None = None,
        doi: str | None = None,
    ) -> TransparencyResult:
        """Run transparency analysis for a single document.

        At least one of *pmid* or *doi* must be provided.

        Returns an ``UNKNOWN`` result when ``settings.enabled`` is False, when
        neither identifier is given, or when every external API was unreachable
        — three distinct cases, each named in ``risk_indicators`` for humans
        and in ``TransparencyResult.unknown_reason`` for callers that branch on
        the cause. The first two cases contact no API at all.

        ``unknown_reason`` is set if and only if ``risk_level`` is ``UNKNOWN``:
        :func:`~bmlib.transparency.models.calculate_risk_level` never returns
        ``UNKNOWN``, so every ``UNKNOWN`` originates in one of the three early
        returns below. ``UNKNOWN`` never triggers a quality tier downgrade, so
        a paper we learned nothing about is not penalised.
        """
        # Checked before the httpx import: a disabled analyzer does no HTTP,
        # so it must not demand the optional dependency either.
        if not self.settings.enabled:
            return TransparencyResult(
                document_id=document_id,
                transparency_score=0,
                risk_level=TransparencyRisk.UNKNOWN,
                risk_indicators=["Transparency analysis disabled in settings"],
                unknown_reason=TransparencyUnknownReason.DISABLED,
                # Determinate, so it is recorded. `None` is reserved for a
                # result persisted before the field existed; a path that
                # *knows* nothing was attempted and leaves `None` behind makes
                # this version's own output indistinguishable from a legacy
                # row, which is the discrimination the field exists to give.
                full_text_status=FullTextStatus.NOT_ATTEMPTED,
                # And the same, for the same reason (issue #198): a disabled
                # analyzer establishes no registration, so nothing was asked.
                trial_results_status=TrialResultsStatus.NOT_REGISTERED,
            )

        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for transparency analysis. "
                "Install with: pip install bmlib[transparency]"
            )

        if not pmid and not doi:
            return TransparencyResult(
                document_id=document_id,
                transparency_score=0,
                risk_level=TransparencyRisk.UNKNOWN,
                risk_indicators=["No PMID or DOI provided"],
                unknown_reason=TransparencyUnknownReason.NO_IDENTIFIER,
                # Likewise: no identifier, so no request was made.
                full_text_status=FullTextStatus.NOT_ATTEMPTED,
                # A literal here and read off the carrier at the UNREACHABLE
                # return below, because there is no carrier yet — the same
                # split `full_text_status` makes on the line above. Recorded
                # rather than left `None` so that `None` keeps meaning *this
                # result predates the field*: a version that leaves it unset
                # on any path makes a current row indistinguishable from a
                # legacy one, which is the rule issue #161 established.
                trial_results_status=TrialResultsStatus.NOT_REGISTERED,
            )

        self._api_reachable = False
        analysis = _Analysis()

        with httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": _user_agent(self.email, httpx.__version__)},
        ) as client:
            # --- CrossRef (funder info) ---
            if doi:
                self._check_crossref(client, doi, analysis)

            # --- EuropePMC (full text / abstract, COI, data availability) ---
            epmc = self._fetch_europepmc(client, pmid, doi)
            if epmc is None:
                # The search produced no answer, so the whole full-text step
                # below is skipped — and it used to be skipped in silence,
                # storing `NOT_ATTEMPTED`, whose documented meaning is that
                # EuropePMC's own answer is the reason (issue #193).
                #
                # `is None` and not falsiness. A 200 carrying an empty object
                # is EuropePMC answering, and answering with no record for
                # this identifier is exactly what `NOT_ATTEMPTED` is for; only
                # `_query_europepmc` returning `None` means no answer arrived.
                # Reaching here at all implies a request was made, because
                # `analyze()` has already refused the no-identifier case
                # above and `_fetch_europepmc` queries on either one.
                analysis.full_text_status = FullTextStatus.SEARCH_FAILED
                # The COI claim only. What became of the full text is the
                # provenance line `_note_full_text_provenance` appends after
                # every step, from this very status — which is what keeps the
                # outage on the record when PubMed supplies a `<CoiStatement>`
                # and the COI line is retracted (issue #203).
                analysis.indicators.append(_INDICATOR_COI_UNKNOWN)
                # The step that was lost, named where it was lost. `_request`
                # reports the request and deliberately does not claim a
                # consequence, because it is shared by five call sites whose
                # consequences differ — this is the widest of them, and the
                # one issue #193 is about. `document_id` is the field that
                # joins a log line to a stored result (issue #161).
                logger.warning(
                    "EuropePMC search produced no answer for %s, so no full-text request "
                    "was made; COI and data-availability findings are unavailable and up "
                    "to %d points are not scored",
                    document_id or pmid or doi,
                    SCORE_COI_DISCLOSED + SCORE_DATA_FULL_OPEN,
                )
            elif epmc:
                self._check_europepmc(client, epmc, analysis, document_id)

            # --- PubMed (structured COI, trial registration, grants) ---
            # Placed after Europe PMC so a DOI-only analysis can reuse the PMID
            # from the record already fetched, and before ClinicalTrials.gov so
            # a structured accession can feed the posted-results check.
            pubmed = self._check_pubmed(client, pmid or _pmid_from_epmc(epmc))
            _merge_pubmed_signals(pubmed, analysis)

            # --- OpenAlex (additional metadata) ---
            if doi:
                self._check_openalex(client, doi, analysis)

            # --- ClinicalTrials.gov (trial registration) ---
            if doi or pmid:
                self._check_trial_registration(client, analysis, epmc=epmc, pubmed=pubmed)

        # If not one external API responded, we measured nothing: report the
        # result as UNKNOWN rather than letting an all-zero score read as HIGH
        # risk (which would be indistinguishable from a genuinely opaque paper
        # and would wrongly trigger a quality-tier downgrade).
        if not self._api_reachable:
            return TransparencyResult(
                document_id=document_id,
                transparency_score=0,
                risk_level=TransparencyRisk.UNKNOWN,
                risk_indicators=["Transparency APIs unreachable — score not determinable"],
                unknown_reason=TransparencyUnknownReason.UNREACHABLE,
                # The analysis ran, so report what it recorded rather than
                # discarding it. **Read from the carrier, never written as a
                # literal**: `NOT_ATTEMPTED` and `SEARCH_FAILED` are both
                # reachable here, and `SEARCH_FAILED` is the *typical* one —
                # a total outage is precisely the case where the EuropePMC
                # search produced no answer, which is the branch above. Until
                # PR #195's review this comment claimed `NOT_ATTEMPTED` was
                # the only value reachable, which issue #193 had falsified in
                # the same commit; the danger of that claim was not the claim
                # but its licence, since "the two spellings are equal" invites
                # substituting the literal and reinstating #193 on the one
                # path the issue opens with.
                # Pinned by `test_a_total_outage_still_records_which_step_never_ran`.
                full_text_status=analysis.full_text_status,
                # Read from the carrier for the same reason. In a total outage
                # this is `NOT_REGISTERED` — nothing was established, because
                # nothing answered — which is a weaker claim than it looks
                # beside `risk_level=UNKNOWN` and `unknown_reason`. What it
                # buys is that every path this version writes records the
                # field (issue #198).
                trial_results_status=analysis.trial_results_status,
            )

        # After every step, and deliberately after `_merge_pubmed_signals`:
        # this line says what became of the full text, which a PubMed
        # `<CoiStatement>` refutes no part of, so it must be out of reach of
        # the retraction rather than merely absent from its set (issue #203).
        # Placed after the UNREACHABLE return above because that result
        # substitutes its own indicator — an UNKNOWN verdict reports no
        # findings at all, and `full_text_status` carries the finer answer
        # there.
        _note_full_text_provenance(analysis)

        # Awarded here rather than by the step that found the level: two
        # sources nominate one, and the component is worth its points once.
        _score_data_availability(analysis)

        analysis.score = min(analysis.score, MAX_TRANSPARENCY_SCORE)

        risk_level = calculate_risk_level(
            score=analysis.score,
            industry_funding=analysis.industry_funding,
            data_availability=analysis.data_level,
            coi_disclosed=analysis.coi_disclosed,
            settings=self.settings,
        )

        return TransparencyResult(
            document_id=document_id,
            transparency_score=analysis.score,
            risk_level=risk_level,
            industry_funding_detected=analysis.industry_funding,
            industry_funding_confidence=analysis.industry_confidence,
            data_availability_level=analysis.data_level,
            coi_disclosed=analysis.coi_disclosed,
            trial_registered=analysis.trial_registered,
            trial_results_compliant=analysis.results_compliant,
            trial_results_status=analysis.trial_results_status,
            risk_indicators=analysis.indicators,
            full_text_analyzed=analysis.full_text_analyzed,
            full_text_status=analysis.full_text_status,
            tier_downgrade_applied=(
                self.settings.tier_downgrade_amount if risk_level == TransparencyRisk.HIGH else 0
            ),
        )

    # --- Analysis sub-steps ---

    def _check_crossref(self, client: Any, doi: str, analysis: _Analysis) -> None:
        """Query CrossRef for funder information and fold it into *analysis*.

        ``SCORE_FUNDER_INFO`` is spent through
        :meth:`_Analysis.award_funder_info`, so it stays a once-per-analysis
        component however many funder sources run and in whatever order — this
        step is merely the first one today.
        """
        cr = self._query_crossref(client, doi)
        if cr:
            funders = _json_object(cr.get("message")).get("funder")
            if isinstance(funders, list) and funders:
                analysis.award_funder_info()
                for funder in funders:
                    name = _json_text(_json_object(funder).get("name"))
                    if _is_industry_funder(name):
                        analysis.note_industry_funder(name)
            elif funders is None or funders == []:
                # CrossRef answered and holds nothing — the only two shapes
                # that mean that, and the only two this line may claim.
                analysis.indicators.append(_INDICATOR_NO_FUNDER_INFO)
            else:
                # CrossRef sent *something* under `funder` that this module
                # cannot read. Reporting that as "no funder information" is a
                # false claim about the record — measured: a `funder` arriving
                # as `{"name": "Acme Pharmaceuticals Inc"}` stored "CrossRef
                # has none" for a body naming an industry funder (PR #208's
                # review). Issue #191's rule, one endpoint over: what was
                # served must not be reported as what was not.
                analysis.indicators.append(_INDICATOR_FUNDERS_NOT_READABLE)

    def _fetch_europepmc(
        self,
        client: Any,
        pmid: str | None,
        doi: str | None,
    ) -> dict | None:
        """Fetch a paper record from EuropePMC."""
        if doi:
            return self._query_europepmc(client, f'DOI:"{doi}"')
        if pmid:
            return self._query_europepmc(client, f"EXT_ID:{pmid}")
        return None

    def _check_europepmc(
        self,
        client: Any,
        epmc: dict,
        analysis: _Analysis,
        document_id: str = "",
    ) -> None:
        """Fold COI and data-availability signals from EuropePMC into *analysis*.

        COI and data-availability statements live in a paper's full text, not
        its abstract.  We therefore fetch the full text from EuropePMC when it
        is available (open-access articles) and scan that; we fall back to the
        abstract only when full text cannot be retrieved.

        Sets ``coi_disclosed`` tri-state: ``True`` (statement found), ``False``
        (full text scanned, none found), or — left as it was — ``None``
        (undeterminable: full text not usable and no abstract signal — which
        of *"unavailable"* and *"served but not usable"* is reported depends on
        :attr:`_Analysis.full_text_status`, since only one of them is ever
        true).

        Industry ties disclosed in the COI statement itself (consultancies,
        speaker fees, …) are recorded through
        :meth:`_Analysis.note_industry_coi`, which is why this step needs no
        return value: it is only ever reached when full text was analyzed, and
        the confidence that belongs to a prose signal is the method's business
        rather than the caller's.
        """
        records = _epmc_records(epmc)
        if not records:
            return

        record = records[0]
        abstract_text = _json_text(record.get("abstractText")).lower()

        # Prefer full text — COI / data-availability statements are not in the
        # abstract. EuropePMC serves full text for open-access records.
        search_text = abstract_text
        if record.get("inEPMC") == "Y":
            # Both coerced: a mistyped accession is truthy, so it used to be
            # interpolated into the URL (`.../{'a': 1}/fullTextXML`), spend a
            # rate-limited request, and store the resulting 404 as
            # `FullTextStatus.NOT_SERVED` — a claim in EuropePMC's mouth for a
            # URL bmlib mangled, at the quiet level the 404 earned from a draw
            # of well-formed requests. Empty is the true answer, and the guard
            # inside already reads it as `NOT_ATTEMPTED` (PR #208's review).
            fetch = self._fetch_europepmc_fulltext(
                client,
                _json_text(record.get("source")) or None,
                _json_text(record.get("pmcid")) or _json_text(record.get("id")) or None,
                document_id,
            )
            analysis.full_text_status = fetch.status
            if fetch.text:
                search_text = fetch.text.lower()
                analysis.full_text_analyzed = True

        # COI detection (a COI/disclosure statement counts as "disclosed",
        # including a statement that there is nothing to declare). A non-blank
        # JATS-tagged COI section is structural proof of a disclosure even
        # when its wording contains no cue phrase (issue #13); the cue-phrase
        # scan remains the fallback for untagged text.
        tagged_coi = _extract_tagged_coi_text(search_text)
        if tagged_coi.strip() or any(pat in search_text for pat in _COI_PATTERNS):
            analysis.coi_disclosed = True
            analysis.score += SCORE_COI_DISCLOSED
        elif analysis.full_text_analyzed:
            # Full text inspected and no COI statement found -> explicitly absent.
            analysis.coi_disclosed = False
            analysis.indicators.append(_INDICATOR_NO_COI_IN_FULLTEXT)
        else:
            # Could not inspect full text; the COI status is genuinely
            # unknown — and that is the whole of what this line says. It used
            # to fork on `full_text_status.is_refusal` to append one of two
            # strings whose parentheticals said *why* the text was not
            # scanned, which made a single line carry two claims and put the
            # provenance inside the reach of the PubMed retraction (issue
            # #203). The why is `_note_full_text_provenance`'s, keyed on the
            # status rather than on this branch, so it distinguishes eight
            # outcomes where the fork distinguished two.
            analysis.indicators.append(_INDICATOR_COI_UNKNOWN)

        # Data availability. The level is found into a local and nominated
        # once: this step is one of two producers, and the winner is scored by
        # `_score_data_availability()` after every step has run. Nominating
        # unconditionally — including the "unknown" this falls through to —
        # keeps the step free of a "is this worth reporting?" judgement only
        # the carrier can make.
        data_level = "unknown"
        for pattern, level in _DATA_PATTERNS.items():
            if pattern in search_text:
                data_level = level
                break
        analysis.note_data_level(data_level)

        # Industry ties disclosed in the COI statement itself ("consultant
        # for X", "speaker fees from Y"). Scanned only in full text — an
        # abstract rarely carries a real disclosure statement — and only
        # within the COI/disclosure region to avoid false positives from
        # references or affiliations. Folded in last so the indicator order
        # stays COI, then data availability, then this.
        if analysis.full_text_analyzed and _discloses_industry_ties(
            _extract_coi_text(search_text, tagged=tagged_coi)
        ):
            analysis.note_industry_coi()

    def _fetch_europepmc_fulltext(
        self,
        client: Any,
        source: str | None,
        ext_id: str | None,
        document_id: str = "",
    ) -> _FullTextFetch:
        """Fetch this article's own full-text XML for an open-access EuropePMC record.

        Nested articles are removed here rather than at each scan, so there is
        one door into the module for a string that has to be the article's:
        every reader downstream — the tagged-COI match, the cue-phrase scan,
        the data-availability patterns and the industry-COI extraction — takes
        it from this return value.

        A :class:`_FullTextFetch` rather than a bare ``str | None``, because
        "no full text" is several different claims and collapsing them is
        issue #161: none was served, or what was served could not be
        segmented into the article's own text, or none of it was the
        article's. The caller falls back to the abstract in every case, which
        leaves the COI status *unknown* rather than absent — it can still be
        set ``True`` from the abstract, but never ``False``, and only
        ``False`` triggers the missing-COI downgrade. That is a claim about
        *that* rule and not about the result: ``score < score_threshold`` is
        the first test in
        :func:`~bmlib.transparency.models.calculate_risk_level`, so an article
        can still reach ``HIGH`` by losing the points its full text would have
        scored. Falling back is cheaper than being wrong, but it is not free,
        which is why the reason is now carried rather than logged only.

        **The four refusals are ordered most-specific-first, and the order is
        load-bearing.** A truncated body can satisfy several of them at once —
        truncation is the cause and the rest are symptoms — and each of the
        first three knows something the completeness check does not: which
        construct and at what offset, that a nested region was left open, that
        nothing outside a nested region arrived. (The unclosed-region refusal
        knows *which* element too — :func:`_strip_nested_articles` holds them
        as a stack of names — but discards it at the return rather than
        reporting it; issue #186 is where that is tracked. The ordering
        argument does not rest on it.) Put the completeness check
        ahead of the lex and issue #160's message becomes unreachable for the
        input that most often produces it — a body *corrupted* rather than
        truncated still carries ``</article>`` and reaches the lex, so
        "only" would overstate it; put it ahead of the entirely-nested
        report and *that* becomes unreachable, since a body of nothing but
        ``<sub-article>`` carries no ``</article>`` either. So it runs last
        and reports only what nothing more specific claimed.

        Every refusal WARNs, naming which it was, the ``document_id`` that
        joins the line to a stored result — where the caller supplied one;
        ``analyze()`` always does, but its own ``document_id`` is a caller's
        string and may be empty, and a line without it cannot be joined to
        anything — and how much was served, in bytes.

        **Exactly one outcome is quiet, and it is the only one measured to be
        ordinary.** A **404** logs the URL at DEBUG: EuropePMC answered, and
        its answer is that it serves no open-access full text here, which is
        the majority outcome of the ``inEPMC`` gate this module uses. Every
        other way of getting no document WARNs and stores
        :attr:`~bmlib.transparency.models.FullTextStatus.REQUEST_FAILED`
        rather than ``NOT_SERVED``, because none of them is EuropePMC saying
        anything about this article (issues #187, #190, #191): a raised
        request, a 429 or 503 or 403, and an HTTP 200 carrying an empty body.
        Results are cacheable and nothing in this package retries, so an
        outage window used to cache absences indistinguishable from
        closed-access papers. The one exception to *WARNING* is upward: a
        request raising a :data:`_BUG_TYPES` member logs **ERROR**, since that
        can only mean bmlib is wrong — the level the parse audit in
        ``fulltext/`` fixes for the identical claim. It still does not raise;
        see the comment at that branch.

        Args:
            client: An ``httpx``-shaped client; only ``get(url)`` is used.
            source: The record's ``source``. It addresses nothing — see
                :data:`EUROPEPMC_REST_BASE` — and is used only to name the
                subject of the log lines, so it may be ``None``.
            ext_id: What addresses the article. Ordinarily a EuropePMC
                accession (``PMC…`` or ``PPR…``), but the caller builds it as
                ``record["pmcid"] or record["id"]``, so for a ``MED`` record
                carrying no ``pmcid`` it is a bare PMID — a request whose 404
                is known before it is made (issue #188). Without it no
                request is made at all.
            document_id: The caller's own identifier, so a log line can be
                joined to the stored result. May be empty.
        """
        if not ext_id:
            # Reachable only under `inEPMC == "Y"`, so EuropePMC has positively
            # claimed to hold the full text and then given nothing to address
            # it by. That is a malformed record rather than an ordinary
            # closed-access paper, and it used to be indistinguishable from
            # one: no request, no log at any level, and a status a reader would
            # take as "we had no reason to ask". A deposit can reach it, so
            # WARNING and not ERROR — the module's own rule.
            #
            # **`source` is deliberately not required.** It was, while it was
            # a path segment; since issue #184 it addresses nothing, and a
            # guard kept past the reason for it refuses a fetch that would
            # have worked — the module's own rule about asking what else a
            # guard was holding. It is still reported, because a record
            # naming no source is worth seeing in the line, and it still
            # names the subject of every message below.
            logger.warning(
                "EuropePMC says it holds full text for document %s but the record carries "
                "no address for it (source=%r, id=%r); scanning the abstract instead",
                document_id or "?",
                source,
                ext_id,
            )
            return _FullTextFetch(None, FullTextStatus.NOT_ATTEMPTED)
        # `{source}/{ext_id}` only while a source was named. `source` may now
        # be `None` — it addresses nothing, see `EUROPEPMC_REST_BASE` — and
        # the unconditional form then renders `None/PMC123`: a two-segment
        # path, in the one module whose signature defect *was* a spurious
        # two-segment path, printed on the same DEBUG line as the corrected
        # single-segment URL. The accession alone is what identifies the
        # article, so an unnamed source drops out of the subject rather than
        # printing as a segment that never existed.
        subject = f"{source}/{ext_id}" if source else ext_id
        if document_id:
            subject = f"{subject} (document {document_id})"
        self._rate_limit()
        # Only the request is wrapped. `_strip_nested_articles` is bmlib's own
        # computation over a string, so anything it raises is a bmlib defect,
        # and inside this `except` it would be logged at DEBUG as "fetch
        # failed" and reported to the caller as "EuropePMC served nothing" —
        # the shape `fulltext/service.py` keeps `_BUG_TYPES` for.
        url = f"{EUROPEPMC_REST_BASE}/{ext_id}/fullTextXML"
        try:
            resp = client.get(url)
        except Exception as e:
            # `REQUEST_FAILED`, not `NOT_SERVED` (issue #187). The scope of
            # this handler is right and deliberate — only `client.get` is
            # inside it — but since issue #161 its return value is a
            # determinate, machine-readable, persisted claim, and "requested
            # and not served" is a claim about *Europe PMC*. Nothing here
            # licenses one: the request never reached an answer.
            #
            # The level is the exception's, not the branch's. A `TypeError`
            # from a client that is not what this code assumes, an
            # `AttributeError` from one that arrived as `None` — those are
            # bmlib being wrong, and holding one at DEBUG is what
            # `fulltext/service.py` keeps `_BUG_TYPES` for. Both examples are
            # `_BUG_TYPES` members and both are exercised by
            # `test_a_bmlib_defect_is_reported_as_one`; an earlier draft named
            # `httpx.InvalidURL` here, which is **neither** — it subclasses
            # `Exception` directly, so it takes the WARNING branch, and the
            # `ext_id` it blamed is interpolated into an f-string that
            # stringifies anything. Everything else is the environment, and
            # WARNs for the reason the non-404 branch below WARNs: results
            # cache, nothing retries, and a silent transport failure is stored
            # forever.
            #
            # **It does not re-raise**, which was issue #187's other option.
            # Every network step in this module swallows its own request so
            # one dead API cannot cost an analysis — `analyze()` itself wraps
            # nothing, which is why each step must — and making *this* step
            # alone fatal would change what a public `analyze()` may raise
            # while `_check_crossref`'s identical defect stayed swallowed.
            # The ERROR is what an operator acts on; the status is what a
            # stored result can be audited by.
            #
            # ERROR rather than WARNING is `jats_parser`'s level for the
            # identical claim, and that is the whole of the precedent: a
            # bmlib defect is not a remote-data failure. **`fulltext/
            # service.py` is not a second precedent for the level** — four
            # documents said it "reports a `_BUG_TYPES` member at ERROR",
            # and it does not; `_warn_swallowed_bug` routes through
            # `_warn_once`, which is `logger.warning`, and that module
            # contains no `logger.error` at all. What it *is* the precedent
            # for is reporting and continuing rather than raising, which is
            # the claim this paragraph makes of it.
            if isinstance(e, _BUG_TYPES):
                # `exc_info=True`, which no other line in this module carries:
                # every one of them reports something a remote gave us, where
                # this one reports that bmlib is wrong. The stack is the whole
                # of what an operator can act on, and without it the report is
                # "bmlib logged a TypeError". `fulltext/service.py` pairs its
                # own bug report with a traceback for the same reason.
                logger.error(
                    "EuropePMC full-text request for %s raised %s, which can only mean a bmlib "
                    "defect: %s; scanning the abstract instead",
                    subject,
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "EuropePMC full-text request for %s failed (%s: %s); scanning the abstract "
                    "instead",
                    subject,
                    type(e).__name__,
                    e,
                )
            return _FullTextFetch(None, FullTextStatus.REQUEST_FAILED)
        if resp.status_code == 404:
            # DEBUG, and the level is measured rather than chosen. Issue #184
            # proposed raising it, on the argument that a non-200 for a record
            # whose own metadata says `inEPMC: Y` is EuropePMC contradicting
            # itself. It is not: `inEPMC` says EuropePMC *holds* the text,
            # while `fullTextXML` serves the open-access subset of it. Probed
            # on 2026-09-05 over 150 `IN_EPMC:Y` records stratified by source
            # and publication year, **no `isOpenAccess: N` record served — 0
            # of 53** — and `isOpenAccess: Y` still 404'd in 35 of 97. So a
            # non-200 here is the ordinary majority outcome for the gate this
            # module actually uses, and a WARNING on it would be noise on
            # every closed-access paper analysed. (Each cell is one cursor
            # page, so those are not population rates; the 0 of 53 is what
            # the decision rests on, and it is a floor, not a proof that no
            # such record can serve.)
            #
            # **That draw is of 404s, and so is this branch, since issue
            # #191.** It used to take every status code, which generalised the
            # measurement past what it looked for: a 429, a 503 or a 403 is
            # the ordinary outcome of nothing. Re-probed on 2026-09-05 over
            # 200 `IN_EPMC:Y` records stratified the same way and addressed
            # exactly as `_check_europepmc` addresses them, **81 of the 81
            # non-200s were 404** — so the DEBUG level is measured on the
            # whole of what this branch now takes, and 0 of the 81 non-200s
            # is the floor under the branch below. That denominator is the
            # tight one: 119 of the 200 served, so they could never have
            # reached it, and "0 of 200" dilutes the claim with probes that
            # were never eligible for it.
            #
            # It is logged rather than dropped, though, because #184 lived a
            # whole release inside this silence: every request 404'd and
            # nothing said so at any level. The URL is what names the defect,
            # so the URL is what the line carries. What actually catches the
            # next #184 is the test that pins it —
            # `TestTheFullTextUrlIsTheOneEuropePmcServes` — not this line,
            # which no operator reads until they already suspect something.
            logger.debug(
                "EuropePMC served no full text for %s: HTTP %d from %s",
                subject,
                resp.status_code,
                url,
            )
            return _FullTextFetch(None, FullTextStatus.NOT_SERVED)
        if resp.status_code != 200:
            # Issue #191. Every status that is neither the 200 below nor the
            # 404 above: a 429, a 503, a 403. None of them is a statement
            # about whether EuropePMC holds this article's full text, which is
            # the one thing `NOT_SERVED` asserts — so `REQUEST_FAILED`.
            #
            # WARNING, and the asymmetry with the 404 is the measurement, not
            # a preference. The 404's DEBUG rests on it being the ordinary
            # majority outcome of the gate this module uses; this branch took
            # 0 of the 81 non-200s among 200 live probes (2026-09-05) — the
            # eligible denominator, the other 119 having served — so it fires
            # on nothing in a healthy draw and a WARNING is not noise. It is
            # the level the consequence needs, too: `cache_results` is True by
            # default and there is no retry, no `Retry-After` handling in
            # `transparency/`, so an outage window silently caches a corpus of
            # absences, each having lost up to
            # `SCORE_COI_DISCLOSED + SCORE_DATA_FULL_OPEN`, never re-attempted.
            # The status is what makes that auditable afterwards; the WARNING
            # is what makes it visible while it is happening.
            #
            # The URL rides along for the same reason the 404 line carries it:
            # the louder branch must not say less than the quiet one.
            logger.warning(
                "EuropePMC answered HTTP %d for %s from %s, which is not an answer about "
                "whether it holds this article; scanning the abstract instead",
                resp.status_code,
                subject,
                url,
            )
            return _FullTextFetch(None, FullTextStatus.REQUEST_FAILED)
        served = resp.text
        if not served:
            # Issue #190, and it runs here rather than beside the other four
            # because it is not a claim about a document's shape at all —
            # nothing arrived to have a shape. Left to fall through, an empty
            # body reached the *entirely nested* branch: `_strip_nested_
            # articles("")` returns `""`, which is falsy but not `None`, so
            # the unclosed-region check passes it and the emptiness check
            # below claims everything served was nested. That logged the
            # self-contradictory *"is entirely nested articles (0 bytes
            # served)"* and stored `ENTIRELY_NESTED`, whose `is_refusal` is
            # True — so the caller persisted *"full text served but not
            # usable"* for a response that carried no document. Issue #161's
            # own class of stored dishonesty, one branch it did not reach.
            #
            # **`not served`, not `not served.strip()`.** A body carrying
            # bytes that strip to nothing did arrive, and is a document-shaped
            # claim; the stricter test would take a **wholly-whitespace body**
            # — `"   \n  "` — out of the entirely-nested branch and report it
            # as nothing served, which is a claim about the transport that the
            # response refutes. Pinned both ways, and the second direction
            # rests on `test_a_whitespace_only_body_is_still_entirely_nested_
            # not_empty` alone.
            #
            # An earlier draft justified this by *"a document whose regions
            # strip out leaving whitespace"* — `"  <sub-article>…</sub-
            # article>  "` — which is the wrong document and measures nothing:
            # its `served.strip()` is truthy, so both spellings of the guard
            # reach the entirely-nested branch identically. Mutating the
            # boundary reddens exactly one test, and it is the wholly-
            # whitespace one.
            #
            # Measured empty, so the guard is carried by the branch it lands
            # in being wrong rather than by a rate: of 119 bodies served
            # across 200 live probes stratified by source and year
            # (2026-09-05), **none was empty** — the smallest was 2,622 bytes,
            # the median 85,925. WARNING because 0 of 119 is the ordinary
            # outcome of nothing, and because Europe PMC's own 404 carries
            # `content-length: 0`, which makes a 200 that does the same at
            # minimum anomalous. Whether they ever serve one is **not
            # measured**, and the local corpora cannot answer it — the
            # importer that built them may have discarded failures, issue
            # #183's caveat.
            logger.warning(
                "EuropePMC answered HTTP 200 with an empty body for %s from %s; "
                "scanning the abstract instead",
                subject,
                url,
            )
            return _FullTextFetch(None, FullTextStatus.REQUEST_FAILED)
        # Bytes, not `len(served)`: `resp.text` is the *decoded* string, so its
        # length under-reports any body carrying non-ASCII — routine in this
        # corpus — and the number exists to be compared against a
        # `Content-Length` or a corpus size distribution. httpx has already
        # read the response, so this costs nothing.
        served_bytes = len(resp.content)
        # The one documented raise, on its own line and caught on its own
        # terms: a truncated body can reach it, so it is not a bmlib defect,
        # and the wider `except` above would have logged it at DEBUG as a
        # fetch failure — the mischaracterisation #159 moved this call out of
        # that block to avoid. Anything *else* this computation raises IS a
        # bmlib defect, and the narrow type here is what keeps it unswallowed.
        try:
            article_xml = _strip_nested_articles(served)
        except _UnterminatedMarkupError as e:
            logger.warning(
                "EuropePMC full text for %s is not well-formed (%s) in %d bytes served; "
                "scanning the abstract instead",
                subject,
                e,
                served_bytes,
            )
            return _FullTextFetch(None, FullTextStatus.UNTERMINATED_MARKUP)
        if article_xml is None:
            # A deposit can reach this, so it is not a bmlib defect: WARNING,
            # and the analysis proceeds on the abstract.
            logger.warning(
                "EuropePMC full text for %s leaves an unclosed nested article in %d bytes "
                "served; scanning the abstract instead",
                subject,
                served_bytes,
            )
            return _FullTextFetch(None, FullTextStatus.UNCLOSED_REGION)
        if not article_xml.strip():
            # Everything served was nested. The caller's `if fetch.text:` would
            # read the empty string as "nothing was served" and fall back
            # silently, so it is reported here instead. Measured empty: all
            # 3,389 carriers across the baseline corpus and an 880-article
            # EuropePMC draw keep their <body>, the least of them retaining
            # 32.2% of its bytes.
            logger.warning(
                "EuropePMC full text for %s is entirely nested articles (%d bytes served); "
                "scanning the abstract instead",
                subject,
                served_bytes,
            )
            return _FullTextFetch(None, FullTextStatus.ENTIRELY_NESTED)
        if _ROOT_END_TAG not in served:
            # Issue #183, and the last check for the reason given above: a body
            # truncated *between* tags opens no unterminated construct, leaves
            # no region open and empties nothing, so all three checks above
            # pass it. Scanned as a complete article it yields
            # `coi_disclosed=False` — "No COI disclosure found in full text" —
            # for a disclosure that was in the lost tail, which is the
            # missing-COI HIGH downgrade fired on evidence that does not exist.
            #
            # **Presence, not position.** Issue #183 proposed
            # `rstrip().endswith(_ROOT_END_TAG)`; measured, that refuses
            # complete articles at a real rate, because trailing comments, PIs
            # and whitespace after the root are legal XML — 1,727 of the
            # 97,909 archive articles (1.76%) of `oa_comm` baseline package
            # `PMC012xxxxxx` (2025-06-26) and 23 of the 8,118 served ones in
            # EuropePMC bundle `PMC10030002_PMC10040000.xml.gz` (0.28%) end
            # `</article><!--requester-ID …-->`. Both of those are honest on
            # the served bundle too: what is measured there is the gap
            # *between* one article's end tag and the next article's opener.
            #
            # **The presence test's own 0 is measured on the archive half
            # only** — 0 false refusals of 97,909 individual documents. The
            # served bundle *cannot* answer it: it is one concatenation split
            # into articles on `</article>`, so "does this article contain
            # `</article>`?" is true by construction, and pooling the two into
            # a single 106,027 would report a tautology as evidence. A
            # truncation removes the tail and the root's end tag *is* in the
            # tail, so absence is what a truncation looks like.
            # `</sub-article>` does not contain the substring, so what the
            # strip removes cannot affect this.
            #
            # Not a well-formedness check, which is the second parse PR #159
            # and PR #182 both declined; it would notice no mismatched <p>.
            logger.warning(
                "EuropePMC full text for %s did not arrive whole: no %s in %d bytes served; "
                "scanning the abstract instead",
                subject,
                _ROOT_END_TAG,
                served_bytes,
            )
            return _FullTextFetch(None, FullTextStatus.TRUNCATED)
        return _FullTextFetch(article_xml, FullTextStatus.ANALYZED)

    def _check_pubmed(self, client: Any, pmid: str | None) -> _PubMedSignals:
        """Fetch and parse the PubMed record for *pmid*.

        Returns empty signals when there is no PMID to look up or the request
        fails, so the step is optional in every sense: it costs no request
        without an identifier and never breaks an analysis when NCBI is down.

        **An empty 200 body is reported, not dropped.** ``_query_pubmed``
        returns ``None`` having already logged, but it returns ``""`` for a
        200 carrying nothing, and the falsy test below cannot tell the two
        apart — so until PR #195's review this was the one endpoint of five
        where *"answered, and the answer is unusable"* left no line at any
        level. It is not a cosmetic gap: empty signals mean no
        ``<CoiStatement>``, so nothing in
        :data:`_INDICATORS_RETRACTED_BY_PUBMED_COI` is retracted, *"COI
        disclosure status unknown"* stands, and the missing-COI downgrade can
        fire. That is the sentence issue #193 justifies itself with, applied
        to the path it did not take. Issue #190 one endpoint over, and the
        same remedy: ``is None`` distinguishes it from a request that was
        already reported.
        """
        if not pmid:
            return _PubMedSignals()
        xml_text = self._query_pubmed(client, pmid)
        if xml_text is None:
            return _PubMedSignals()
        if not xml_text:
            logger.warning(
                "PubMed for %s: answered 200 with an empty body; "
                "no COI, trial-registration or grant signals are available",
                pmid,
            )
            return _PubMedSignals()
        return _parse_pubmed_signals(xml_text)

    def _check_openalex(self, client: Any, doi: str, analysis: _Analysis) -> None:
        """Fold open-access status and citation count from OpenAlex into *analysis*."""
        oa = self._query_openalex(client, doi)
        if oa:
            # `_json_bool` and not truthiness: `{"is_oa": "false"}` is a
            # truthy string, so the bare read awarded `SCORE_OPEN_ACCESS` for
            # a body stating the opposite (PR #208's review). `None` — the
            # remote did not say — is not open access, which is what the
            # existing absent-key behaviour already was.
            if _json_bool(_json_object(oa.get("open_access")).get("is_oa")):
                analysis.score += SCORE_OPEN_ACCESS
            if _json_count(oa.get("cited_by_count")) > 0:
                analysis.score += SCORE_CITED

    def _check_trial_registration(
        self,
        client: Any,
        analysis: _Analysis,
        *,
        epmc: dict | None = None,
        pubmed: _PubMedSignals | None = None,
    ) -> None:
        """Check trial registration and, where possible, results posting.

        PubMed's ``DataBankList`` is preferred over the abstract heuristic when
        present: it is the publisher asserting *this* paper's registration, so
        none of the heuristic's defences against a review's citation list apply
        to it. The heuristic remains the fallback for records PubMed does not
        cover.

        A registration ClinicalTrials.gov cannot be asked about — another
        registry, or a ClinicalTrials.gov entry with an unusable accession —
        counts as registered, but no claim is made about posted results either
        way.

        Takes no ``pmid``/``doi``: they existed only to let the heuristic
        re-issue the EuropePMC search, which is what issue #202 removed.
        """
        pubmed = pubmed or _PubMedSignals()

        ct_ids = list(pubmed.trial_accessions) or _find_trial_ids(epmc)
        if ct_ids or pubmed.registration_not_checkable:
            analysis.trial_registered = True
            analysis.score += SCORE_TRIAL_REGISTERED

        if ct_ids:
            # **Three outcomes, not two** (issue #195's review). Until then
            # this was `any(...)` over a `bool`, so a trial nobody managed to
            # ask about was indistinguishable from one that answered "none
            # posted" — and the `else` stored *"Registered trial without
            # posted results"*, a false claim about the trial, in a persisted
            # field. That is what made issue #194 silent for a release: the
            # edge 403'd every request and every registered trial was
            # published as non-compliant. Correcting the `User-Agent` made the
            # requests succeed; it did not make the *conflation* honest, which
            # is what this loop is for. `FullTextStatus`'s argument (issue
            # #161) one endpoint over, at the scale this endpoint needs.
            #
            # The loop still stops at the first trial with posted results, as
            # the `any()` it replaces did, because that answer is final. It
            # cannot stop early on any other, since a later accession may be
            # the one that answers. The outcome is this step's own finding and
            # deliberately not a read of `analysis.results_compliant`: the
            # indicators below report what ClinicalTrials.gov did, which a
            # flag arriving from elsewhere must not be able to retract.
            answered = False
            compliant = False
            for tid in ct_ids[:MAX_TRIAL_IDS_TO_CHECK]:
                posted = self._check_trial_results(client, tid)
                if posted is None:
                    continue
                answered = True
                if posted:
                    compliant = True
                    break
            if compliant:
                analysis.results_compliant = True
                analysis.trial_results_status = TrialResultsStatus.POSTED
                analysis.score += SCORE_RESULTS_POSTED
            elif answered:
                analysis.trial_results_status = TrialResultsStatus.NOT_POSTED
                analysis.indicators.append(_INDICATOR_NO_POSTED_RESULTS)
            else:
                # Asked, and not one accession answered. The same line the
                # other-registry case gets, because the claim is identical —
                # *"could not be checked"* — and it puts nothing in
                # ClinicalTrials.gov's mouth, which is the whole distinction
                # issues #187/#190/#191 drew. The two causes are not split in
                # *prose* because nothing downstream could act on the
                # difference in a sentence; what one can act on is that this
                # is not a finding.
                #
                # The **status** does split them, and that is not a
                # disagreement (issue #198): *"would re-running change this?"*
                # is `yes` here and `no` for the other-registry case below,
                # which is the question `FullTextStatus.REQUEST_FAILED`
                # exists to answer one endpoint over and the one results
                # being cacheable makes worth storing.
                analysis.trial_results_status = TrialResultsStatus.REQUEST_FAILED
                analysis.indicators.append(_INDICATOR_RESULTS_NOT_CHECKABLE)
        elif pubmed.registration_not_checkable:
            analysis.trial_results_status = TrialResultsStatus.NOT_CHECKABLE
            analysis.indicators.append(_INDICATOR_RESULTS_NOT_CHECKABLE)

    # --- API query helpers ---

    def _rate_limit(self) -> None:
        """Enforce minimum interval between outgoing HTTP requests.

        The lock is held across the sleep so concurrent callers queue rather
        than all observing the same stale ``_last_request`` and firing
        simultaneously — serialising here is the point of a rate limiter.
        """
        with self._rate_limit_lock:
            elapsed = time.time() - self._last_request
            if elapsed < _MIN_REQUEST_INTERVAL_SECONDS:
                time.sleep(_MIN_REQUEST_INTERVAL_SECONDS - elapsed)
            self._last_request = time.time()

    def _request(
        self,
        client: Any,
        url: str,
        *,
        api: str,
        subject: str,
        params: dict[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        quiet_statuses: frozenset[int] = frozenset(),
    ) -> Any | None:
        """Make one paced request and return the 200 response, or ``None``.

        **One helper, because the shape it replaces had been got wrong in five
        copies** (issue #193). Each was ``try`` -> ``if status == 200: return``
        -> ``except Exception: logger.debug`` -> ``return None``, which is two
        silences of different kinds:

        * a :data:`_BUG_TYPES` member — bmlib being wrong about its own client
          — held at a level nobody enables. That is issue #187, whose fix
          landed at ``_fetch_europepmc_fulltext`` and in none of these five.
        * **a non-200 falling off the end with no line at any level.** Not a
          level problem but an absence: the ``except`` catches only raises, so
          a 429, a 503 or a 403 simply reached ``return None`` and there was
          no DEBUG line for an operator to enable. Measured that way, with
          every API but CrossRef answering 503: zero lines, a ``HIGH``
          verdict, and a tier downgrade.

        It does **not** re-raise, for the reason argued at
        :meth:`_fetch_europepmc_fulltext`'s own handler: ``analyze()`` wraps
        none of these, so every step must swallow its own request or one dead
        API costs the analysis.

        **The response is returned rather than the decoded body**, and the
        decode lives in :meth:`_request_json` / :meth:`_request_text` above
        the same reporting boundary. A body that will not parse is the
        remote's failure — ``json.JSONDecodeError`` is a ``ValueError`` and so
        deliberately outside ``_BUG_TYPES`` — and moving the decode outside
        any handler would let it escape a public ``analyze()``.

        Args:
            client: An ``httpx``-shaped client; only ``get`` is used.
            url: The URL, from this module's own constants.
            api: The remote's name, for the log lines.
            subject: What was being asked about — a DOI, a PMID, an
                accession — so a line can be joined to a stored result.
            params: Query parameters, if any.
            headers: Per-request headers, if any. The ``User-Agent`` is set
                once on the client (see :func:`_user_agent`) and is not one
                of these.
            quiet_statuses: Statuses that log at DEBUG rather than WARNING
                for this endpoint, because a draw measured them ordinary.
                **Per endpoint and per status**, which is issue #191's rule:
                a level is a claim, and the branch it sits on must be no
                wider than the draw that earned it. Empty means every non-200
                warns, which is the safe default for an endpoint nothing has
                measured.

        Returns:
            The response when it was 200, else ``None`` — having logged, in
            every case, exactly what happened.
        """
        self._rate_limit()
        try:
            resp = client.get(url, params=params, headers=headers)
        except Exception as e:
            # The two-level split lives in `_report_swallowed_exception`, so
            # the decode layers above share it rather than each keeping a
            # bare `except Exception` — which is what they did, and what made
            # them report a bmlib defect as the remote's malformed body.
            _report_swallowed_exception(
                e,
                api=api,
                subject=subject,
                doing="the request",
                ordinary="the request failed",
            )
            return None
        if resp.status_code == 200:
            # Set on the 200 and before the body is read, exactly as the
            # four `_query_*` helpers already did — a remote that answered 200
            # and then sent something unreadable *was* reachable, and demoting
            # the whole analysis to UNKNOWN over a malformed body would claim
            # more than the evidence supports. Stated here because it is now
            # one rule for five call sites rather than four copies of one, and
            # **not** because anything moved: an earlier draft of this comment
            # said the assignment used to follow the body read, which
            # `git show main` refutes (PR #195's review).
            #
            # `_check_trial_results` now marks reachability too, where it did
            # not before. That is unobservable and deliberate: every path to
            # it needs an accession, which comes either from a PubMed record
            # or from a EuropePMC one, so a 200 has already been seen by the
            # time it runs. What changes is the rule, which now reads "any
            # external API that answered" without an exception nobody could
            # have derived from the code.
            self._api_reachable = True
            return resp
        level = logging.DEBUG if resp.status_code in quiet_statuses else logging.WARNING
        # **The consequence is the caller's to state, not this helper's.** The
        # line used to end "that component is not scored", which is true for
        # CrossRef and OpenAlex and wrong for the other three: a refused
        # ClinicalTrials.gov request used to *manufacture* a scored finding
        # (issue #194), and a failed EuropePMC search gates the whole
        # full-text step rather than one component. A helper shared by five
        # call sites cannot know which, so it reports the request and
        # `analyze()` reports what was lost (PR #195's review).
        logger.log(
            level,
            "%s answered HTTP %d for %s from %s; that request produced no answer",
            api,
            resp.status_code,
            subject,
            url,
        )
        return None

    def _request_json(
        self,
        client: Any,
        url: str,
        *,
        api: str,
        subject: str,
        params: dict[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        quiet_statuses: frozenset[int] = frozenset(),
    ) -> dict[str, Any] | None:
        """:meth:`_request`, decoded as a JSON **object**, or ``None``.

        A 200 carrying something that is not JSON used to be logged as
        *"query failed"* at DEBUG, which names the wrong stage: the request
        succeeded and the body is what is wrong.

        **The level is not decided here.** ``json.JSONDecodeError`` is a
        ``ValueError`` and so deliberately outside ``_BUG_TYPES``, which is
        why the ordinary case WARNs — but the handler catches everything, and
        a response object bmlib was wrong about raises ``AttributeError`` or
        ``TypeError`` from this very ``resp.json()``. Reporting that as *"the
        remote sent a body that is not JSON"* is issue #187's defect inside
        the fix for it, so the call goes through
        :func:`_report_swallowed_exception`, which reads the type.

        **It promises an object, not merely valid JSON** (issue #199). JSON's
        top level may be an array, a string, a number, ``true`` or ``null``,
        and every caller here reads the body with ``.get()``. A **truthy**
        non-object therefore raised ``AttributeError`` out of a public
        :meth:`analyze`, which wraps none of its steps — truthy because
        ``null``, ``[]``, ``false`` and ``0`` are refused a step earlier by
        the callers' own ``if cr:`` / ``elif epmc:`` / ``if oa:``, which is
        why the measured population is 12 and not 24 (PR #208's review
        corrected the blanket "any of those"). That is a ``_BUG_TYPES`` member
        escaping over a body the remote chose, and the three ``_query_*``
        helpers — :meth:`_query_crossref`, :meth:`_query_europepmc` and
        :meth:`_query_openalex` — already annotate ``dict | None``: the
        annotation was **false**, and invisible to mypy only because this
        method returned ``Any``. Narrowing it here makes those three true
        rather than adding a claim.

        **The refusal belongs at this layer and not at the caller**, by
        :meth:`_request`'s own rule read the other way round: that helper
        pushes the *consequence* out to the caller because five call sites
        lose different things, while the *body* is this layer's subject
        already — it is what the line above reports. One step finer is the
        same claim, so it is stated once here instead of four times.

        A future endpoint whose 200 legitimately carries an array wants its
        own helper, not a flag on this one — :meth:`_request_text`'s rule
        about ``headers``, one method down: add it when a second one arrives.
        """
        resp = self._request(
            client,
            url,
            api=api,
            subject=subject,
            params=params,
            headers=headers,
            quiet_statuses=quiet_statuses,
        )
        if resp is None:
            return None
        try:
            data = resp.json()
        except Exception as e:
            _report_swallowed_exception(
                e,
                api=api,
                subject=subject,
                doing="decoding the 200 response body",
                ordinary="answered 200 with a body that is not JSON",
            )
            return None
        if not isinstance(data, dict):
            # WARNING for the reason a body that will not parse warns: the
            # remote answered and sent a shape no reader here can use, which
            # is the remote's failure and not bmlib's. Reported rather than
            # dropped quietly, because a response thrown away with no line is
            # the other half of issue #193 — the type is named, since "not an
            # object" does not say whether an array or a bare string arrived.
            logger.warning(
                "%s for %s: answered 200 with JSON that is not an object (%s) "
                "from %s; that request produced no answer",
                api,
                subject,
                type(data).__name__,
                url,
            )
            return None
        return data

    def _request_text(
        self,
        client: Any,
        url: str,
        *,
        api: str,
        subject: str,
        params: dict[str, str] | None = None,
        quiet_statuses: frozenset[int] = frozenset(),
    ) -> str | None:
        """:meth:`_request`, read as text.

        Separate from :meth:`_request_json` rather than a flag on it, because
        the two return different types and a caller that got the wrong one
        would find out at a ``.get()`` several frames away. The body read is
        inside a handler for :meth:`_request`'s own reason: it is the remote's
        bytes, and nothing here may escape into a public ``analyze()``. It
        reports through :func:`_report_swallowed_exception` for the reason
        :meth:`_request_json` does — a missing ``.text`` is bmlib being wrong
        about its client, not the remote sending something unreadable.

        It takes no ``headers``, unlike :meth:`_request_json`, and the
        asymmetry is deliberate rather than an omission: PubMed's ``efetch``
        is the only text endpoint and asks for none. Add the parameter when a
        second one arrives, not before.
        """
        resp = self._request(
            client,
            url,
            api=api,
            subject=subject,
            params=params,
            quiet_statuses=quiet_statuses,
        )
        if resp is None:
            return None
        try:
            return str(resp.text)
        except Exception as e:
            _report_swallowed_exception(
                e,
                api=api,
                subject=subject,
                doing="reading the 200 response body",
                ordinary="answered 200 with a body that could not be read",
            )
            return None

    def _query_crossref(self, client: Any, doi: str) -> dict | None:
        """Query the CrossRef API for a DOI."""
        return self._request_json(
            client,
            CROSSREF_WORKS_URL.format(doi=doi),
            api="CrossRef",
            subject=doi,
            headers=JSON_ACCEPT_HEADERS,
            quiet_statuses=_CROSSREF_ORDINARY_STATUSES,
        )

    def _query_europepmc(self, client: Any, query: str) -> dict | None:
        """Query the EuropePMC search API."""
        return self._request_json(
            client,
            f"{EUROPEPMC_REST_BASE}/search",
            api="EuropePMC",
            subject=query,
            params={"query": query, "format": "json", "resultType": "core"},
            quiet_statuses=_EUROPEPMC_SEARCH_ORDINARY_STATUSES,
        )

    def _query_pubmed(self, client: Any, pmid: str) -> str | None:
        """Fetch a single PubMed record as XML via E-utilities ``efetch``.

        ``tool`` and ``email`` identify the caller, as NCBI asks. ``api_key``
        is sent when configured: it does not change this client's pacing, but
        it moves the request into the key's 10 requests/second allowance
        instead of the 3 requests/second shared by everything on the IP.
        """
        params: dict[str, str] = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "xml",
            "tool": EUTILS_TOOL_NAME,
            "email": self.email,
        }
        if self.pubmed_api_key:
            params["api_key"] = self.pubmed_api_key

        return self._request_text(
            client,
            EFETCH_URL,
            api="PubMed",
            subject=pmid,
            params=params,
            quiet_statuses=_PUBMED_ORDINARY_STATUSES,
        )

    def _query_openalex(self, client: Any, doi: str) -> dict | None:
        """Query the OpenAlex API for a DOI."""
        return self._request_json(
            client,
            OPENALEX_WORKS_URL.format(doi=doi),
            api="OpenAlex",
            subject=doi,
            headers=JSON_ACCEPT_HEADERS,
            quiet_statuses=_OPENALEX_ORDINARY_STATUSES,
        )

    def _check_trial_results(self, client: Any, nct_id: str) -> bool | None:
        """Check if a ClinicalTrials.gov trial has posted results.

        Uses the v2 API's top-level ``hasResults`` boolean. An earlier
        implementation requested a ``ResultsSection`` field but read a
        ``resultsSection`` key, so it under-detected posted results.

        The request is narrowed to ``hasResults``, so that is the only key
        the response can carry; a missing key means the API did not answer
        the question and is reported as "no posted results" rather than
        guessed at from a payload that was never requested.

        **A ``bool`` could not distinguish "no results posted" from "not
        answered", and that is what made issue #194 silent**: the edge refused
        bmlib's ``User-Agent`` with a 403, this returned ``False``, and the
        caller stored *"Registered trial without posted results"* — a false
        claim about the trial — for every registered trial bmlib ever
        analysed. Correcting the header at :func:`_user_agent` made the
        requests succeed and left the conflation in place, so a 404, a 403 or
        a body that will not decode still manufactured the same false finding.
        The tri-state is the fix (PR #195's review); the ``FullTextStatus``
        argument from issue #161, one endpoint over and at the scale this
        endpoint needs.

        Returns:
            ``True`` when ClinicalTrials.gov said results are posted,
            ``False`` when it said they are not, and ``None`` when it did not
            answer the question — a request that raised, a non-200, a body
            that will not decode, or a 200 carrying something that is not a
            JSON object. Every one of those is *"we do not know"*, and
            :meth:`_check_trial_registration` reports it as that.

            The public model carries the same three-way answer since issue
            #198: :attr:`TransparencyResult.trial_results_status` is what a
            downstream branches on, and this tri-state is what it is built
            from: ``None`` from *every* accession becomes ``REQUEST_FAILED``
            there, while one ``None`` beside one ``False`` becomes
            ``NOT_POSTED`` — see issue #206 on that. Until then
            the residual was recorded in ``docs/DECISIONS.md`` as *"not worth
            a schema change today"*, and that entry now says why it was.
            ``trial_results_compliant`` stays as the compatibility field.
        """
        data = self._request_json(
            client,
            CLINICALTRIALS_STUDY_URL.format(nct_id=nct_id),
            api="ClinicalTrials.gov",
            subject=nct_id,
            params={"fields": "hasResults"},
            quiet_statuses=_CLINICALTRIALS_ORDINARY_STATUSES,
        )
        if not isinstance(data, dict):
            # A JSON body that is not an object answers the question no more
            # than a 404 does — so `None`, the same as a 404, rather than the
            # `False` this returned until PR #195's review, which was a
            # *finding* manufactured out of an unusable body. It used to reach
            # `.get()` inside this method's own `try` and come back as `False`
            # through an `AttributeError` logged as "query failed", which the
            # `_BUG_TYPES` branch would now report as a bmlib defect it is
            # not.
            #
            # **Since issue #199 this branch is reached only for `None`**, the
            # non-object body being refused a layer down at `_request_json`,
            # which every reader in the module needed and not this one alone.
            # It is kept rather than narrowed to `data is None`, as the second
            # of two independent protections at the one site where an unusable
            # body did not merely raise but *published a false finding about a
            # trial* for a whole release (issue #194) — the redundancy issue
            # #203 argues for, at the place with the worst measured cost.
            # No test can now separate this guard from a `data is None`
            # narrowing *through* `_request_json`, that path being closed —
            # `test_an_unusable_body_is_refused_at_this_site_too` calls the
            # method with the boundary stubbed, which is the only way left to
            # exercise what it defends (PR #208's review).
            return None
        # `_json_bool` and not `bool()`. This is the value the guard above
        # never covered: `bool("no")` is `True`, so ClinicalTrials.gov stating
        # *no results* was stored as `POSTED` with `trial_results_compliant`
        # set and `SCORE_RESULTS_POSTED` awarded — a false claim in the
        # affirmative about a trial, at the site issue #194 already made one
        # for a release (PR #208's review). `None` here is *"did not answer"*,
        # which `_check_trial_registration` already routes to
        # `REQUEST_FAILED` + `_INDICATOR_RESULTS_NOT_CHECKABLE`, so the
        # tri-state absorbs it with no new vocabulary.
        #
        # **An absent key keeps its old answer, deliberately.** `.get()`
        # returning `None` here is CrossRef's… no — it is ClinicalTrials.gov
        # omitting a field this request narrowed to, which
        # `test_missing_has_results_is_false` has pinned as `False` since
        # before the tri-state existed. Routing it to `None` with the
        # wrong-typed values would move a stored value for a **well-formed**
        # body, which this change is otherwise careful not to do, and the
        # question is genuinely open — the existing comment says "an absent
        # key means unanswered" and then reports a finding, which is the
        # conflation issues #195/#198 removed everywhere else. Filed as issue
        # #210 rather than settled in passing.
        has_results = data.get("hasResults")
        if has_results is None:
            return False
        return _json_bool(has_results)
