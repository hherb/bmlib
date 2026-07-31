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
from dataclasses import dataclass, field
from typing import Any

from bmlib import __version__
from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    TransparencyUnknownReason,
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
# Membership of both lists was decided by measuring against 833 real names
# sampled from CrossRef and PubMed — see `scripts/sample_funder_names.py`,
# the labelled corpus in `tests/data/funder_names.json`, and the metric test
# in `tests/test_funder_matching.py`. The counts below are from that corpus.

# Substring stems. Every one scored at least one true positive and no false
# positives; each is also *narrower* than what it replaced:
#   "pharmaceutic"  3 TP / 0 FP  — replaces "pharma", which scored 3 TP / 5 FP
#                                  by reaching "Pharmacy", "Pharmacology" and
#                                  "Pharmacogenetics", all academic.
#   "therapeutics"  1 TP / 0 FP
#   "laboratories"  1 TP / 0 FP  — the plural only. "Key Laboratory" (singular)
#                                  is a Chinese state-lab form and appeared 8
#                                  times; it must keep missing them.
_INDUSTRY_STEMS = (
    "pharmaceutic",
    "therapeutics",
    "laboratories",
)

# Whole words. No trailing "\.?" is needed: `\b` already sits between the last
# letter and a following ".", so "Inc" and "Inc." both match.
#
# The first two are the safe residue of stems the corpus disqualified — see
# `_INDUSTRY_STEMS` for "pharma", and for "biotech": as a substring it scored
# 0 TP / 4 FP, reaching only "Department of Biotechnology" (an Indian
# ministry) and "Biotechnology and Biological Sciences Research Council" (a UK
# research council). "Biotechnology" names a field, not a company type. As a
# bare word it is a company name ("Acme Biotech"), so that form is kept.
#
# The rest are legally reserved incorporation suffixes — a public body cannot
# use one. Deliberately absent, each for a measured or stated reason:
#   "co"           4 TP / 1 FP — collides with the English prefix in
#                                "project co-sponsored by province…"
#   "corporation"  1 TP / 1 FP — US non-profits use it ("Research Corporation
#                                for Science Advancement")
#   "plc" "pty"    0 TP        — no corpus evidence, so nothing earned
#   "ag" "bv" "nv" "sa"  0 TP  — same, and two-letter tokens besides
#   "ab"           1 TP / 0 FP — passes the count but excluded: it collides
#                                with province and country codes, and these
#                                strings carry locations ("…, Hyderabad,
#                                India"), so "University of Calgary, AB" would
#                                be a false positive the corpus cannot see.
#                                Costs one true positive, "Roche Sweden AB".
#   "labs"         1 TP / 0 FP — same call: "Los Alamos National Labs" is not
#                                industry. Costs "Tempus Labs".
# Ties go to precision here, because `industry_funding_detected` feeds a
# HIGH-risk rule and HIGH downgrades a paper's quality tier.
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
)

_INDUSTRY_WORD_RE = re.compile(
    r"\b(?:" + "|".join(_INDUSTRY_WORDS) + r")\b",
    re.IGNORECASE,
)


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

# ---- PubMed E-utilities ----
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# NCBI asks every E-utilities caller to identify itself; `email` comes from the
# analyzer's own contact address.
EUTILS_TOOL_NAME = "bmlib"

# `DataBankName` values PubMed emits for clinical-trial registries, lowercased
# for matching. Anything outside this set — GENBANK, PDB, SRA, Dryad, … — is a
# data-deposition accession: a transparency signal of its own, read as evidence
# of data availability rather than of trial registration.
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
        "jprn",
        "jrct",
        "ntr",
        "pactr",
        "rebec",
        "rpcec",
        "slctr",
        "tctr",
        "umin-ctr",
    }
)
_CLINICALTRIALS_GOV = "clinicaltrials.gov"

# ---- Indicator strings ----
# Named rather than inlined because the PubMed step must be able to retract the
# two COI lines: a structured <CoiStatement> can establish a disclosure that the
# full-text scan missed, and leaving either line in place would then contradict
# `coi_disclosed=True`.
_INDICATOR_NO_COI_IN_FULLTEXT = "No COI disclosure found in full text"
_INDICATOR_COI_UNKNOWN = "COI disclosure status unknown (full text unavailable)"
_INDICATOR_COI_IN_PUBMED = "COI disclosure found in PubMed record"
_INDICATOR_INDUSTRY_COI = "Industry ties disclosed in COI statement"
_INDICATOR_NO_POSTED_RESULTS = "Registered trial without posted results"
# Named for the same reason as the COI lines: a PubMed data-deposition record
# can establish open data that this line contradicts, so it has to be
# retractable by the same name that appended it.
_INDICATOR_DATA_NOT_AVAILABLE = "Data explicitly not available"
# Deliberately does not name a registry. It covers a registration in another
# registry *and* a ClinicalTrials.gov registration whose accession was missing
# or malformed; saying "registered outside ClinicalTrials.gov" would be a plain
# falsehood in the second case.
_INDICATOR_RESULTS_NOT_CHECKABLE = (
    "Trial registration found; posted-results status could not be checked"
)

# ---- Rate limiting ----
_MIN_REQUEST_INTERVAL_SECONDS = 0.35

# ---- HTTP settings ----
_HTTP_TIMEOUT_SECONDS = 15.0

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

# ---- Data-availability levels ----
# The whole vocabulary `calculate_risk_level()` accepts, including the two
# levels this module never produces — `restricted` and `not_stated`, which
# exist for callers computing the level from a richer source. Listing them
# makes the table the definition of the vocabulary, so a level outside it
# raises rather than being silently ranked weakest.
#
# The rank orders *evidence of openness*, which is what
# `_Analysis.note_data_availability()` resolves its two producers with. Hence
# `unknown` below the withheld levels: it is the absence of a finding, and must
# never overwrite one.
_DATA_LEVEL_RANK: dict[str, int] = {
    "unknown": 0,
    "not_stated": 1,
    "restricted": 1,
    "not_available": 1,
    "on_request": 2,
    "full_open": 3,
}
# Levels absent from this table earn nothing; only the two scoring levels are
# listed, so the awards stay mutually exclusive by construction.
_DATA_LEVEL_SCORES: dict[str, int] = {
    "on_request": SCORE_DATA_ON_REQUEST,
    "full_open": SCORE_DATA_FULL_OPEN,
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

    All of them are structured publisher-supplied metadata, which is why they
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
        data_banks: Distinct names of the data archives this article deposited
            in — every ``<DataBank>`` whose name is not a trial registry — in
            document order and spelled as PubMed spelled them. Accession
            numbers are deliberately not carried: nothing fetches them, so
            they would be validated for no consumer.
    """

    coi_statement: bool = False
    trial_accessions: tuple[str, ...] = ()
    registration_not_checkable: bool = False
    funders: tuple[str, ...] = ()
    data_banks: tuple[str, ...] = ()


def _parse_pubmed_signals(xml_text: str) -> _PubMedSignals:
    """Extract transparency signals from a PubMed ``efetch`` response.

    Returns empty signals for anything unusable — malformed XML, an empty
    result set, a record without the relevant elements — so a surprising
    response degrades the analysis rather than raising into it.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug("PubMed response was not parsable XML: %s", e)
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
    # Keyed by the lowercased name so one archive spelled two ways is one
    # finding, valued by the first spelling because that is what a human is
    # shown in the indicator.
    data_banks: dict[str, str] = {}
    for databank in citation.findall("Article/DataBankList/DataBank"):
        original = (databank.findtext("DataBankName") or "").strip()
        name = original.lower()
        if name not in _TRIAL_REGISTRY_NAMES:
            # Everything the registry set does not name is a data archive:
            # `DataBankName` is an NLM controlled vocabulary of registries and
            # archives, so once the registries are named the complement is the
            # archives. An allowlist of repositories would go stale as NLM adds
            # them, and would discard the signal rather than record it.
            if name:
                data_banks.setdefault(name, original)
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
        data_banks=tuple(data_banks.values()),
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
        data_level: Data-availability level, a member of
            :data:`_DATA_LEVEL_RANK`. Two steps produce it — the Europe PMC
            text scan and PubMed's ``<DataBankList>`` — so it is written
            through :meth:`note_data_availability`, never assigned.
        coi_disclosed: Tri-state — ``True`` (statement found), ``False`` (full
            text scanned, none found), ``None`` (undeterminable).
        trial_registered: A trial registration was established.
        results_compliant: Posted results were found for a registered trial.
        full_text_analyzed: Findings came from full text, not just an abstract.
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
    full_text_analyzed: bool = False
    funder_info_scored: bool = False

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

    def note_data_availability(self, level: str) -> None:
        """Record *level* as this analysis's data-availability finding.

        The field has two producers — the Europe PMC text scan and PubMed's
        ``<DataBankList>`` — so it is merged rather than assigned: the level
        with the strongest evidence of openness wins, whichever arrives first,
        the way :attr:`industry_confidence` resolves its own two sources. A tie
        keeps the incumbent, and ``"unknown"`` — the absence of a finding, not
        a weaker one — never displaces anything.

        The score is *swapped*, not added to: the credit already spent on the
        superseded level is taken back. The two data-availability awards are
        mutually exclusive, which is what makes the documented maximum of
        exactly 100 attainable, and a second producer adding its own credit
        would score one component twice.

        Args:
            level: A member of :data:`_DATA_LEVEL_RANK`. Anything else raises
                ``KeyError`` — ranking an unlisted level weakest would swallow
                a typo and silently drop the finding it names.
        """
        if _DATA_LEVEL_RANK[level] <= _DATA_LEVEL_RANK[self.data_level]:
            return

        self.score += _DATA_LEVEL_SCORES.get(level, 0) - _DATA_LEVEL_SCORES.get(self.data_level, 0)
        self.data_level = level

        # Appended and retracted through the same name, so the indicator list
        # cannot end up contradicting the field above it.
        if level == "not_available":
            self.indicators.append(_INDICATOR_DATA_NOT_AVAILABLE)
        elif _INDICATOR_DATA_NOT_AVAILABLE in self.indicators:
            self.indicators.remove(_INDICATOR_DATA_NOT_AVAILABLE)

    def note_data_deposition(self, name: str) -> None:
        """Record a deposit in the data archive *name*, reported by PubMed.

        The level is fixed at ``"full_open"`` inside the method rather than
        passed in, for the reason :meth:`note_industry_funder` fixes its
        confidence: "an archive accession is open data" is the rule, and a
        caller free to choose could blur it.

        The indicator is deduplicated — one archive is one finding — and says
        which archive, because it is the only thing telling a reader why the
        level contradicts what the full text said.
        """
        line = f"Data deposited in {name}"
        if line not in self.indicators:
            self.indicators.append(line)
        self.note_data_availability("full_open")

    def note_industry_coi(self) -> None:
        """Record industry ties disclosed in a full-text COI statement.

        Weaker evidence than a funder record — an inference from prose rather
        than a structured field — so it raises the confidence only to
        :data:`TEXT_INDUSTRY_CONFIDENCE` and never lowers a stronger one.
        """
        self.industry_funding = True
        self.industry_confidence = max(self.industry_confidence, TEXT_INDUSTRY_CONFIDENCE)
        self.indicators.append(_INDICATOR_INDUSTRY_COI)


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
    """
    if pubmed.coi_statement and analysis.coi_disclosed is not True:
        analysis.coi_disclosed = True
        analysis.score += SCORE_COI_DISCLOSED
        # Both lines were written before PubMed was consulted and would now
        # contradict the result, so they are retracted rather than left to
        # be reconciled by whoever reads the indicators.
        analysis.indicators = [
            ind
            for ind in analysis.indicators
            if ind not in (_INDICATOR_NO_COI_IN_FULLTEXT, _INDICATOR_COI_UNKNOWN)
        ]
        analysis.indicators.append(_INDICATOR_COI_IN_PUBMED)

    # A missing <CoiStatement> deliberately does not demote `None` to
    # `False`: it means the publisher supplied no statement to PubMed, not
    # that the paper carries none, and `False` would trigger the
    # missing-COI downgrade on no evidence.

    # A databank entry is the publisher asserting that this article's data went
    # into a public archive — structured metadata, so it outranks the substring
    # scan of the retrieved text, and it is the only data-availability evidence
    # a closed-access paper can carry.
    for name in pubmed.data_banks:
        analysis.note_data_deposition(name)

    if pubmed.funders:
        analysis.award_funder_info()
        for agency in pubmed.funders:
            if _is_industry_funder(agency):
                # A grant agency is structured metadata, the same class of
                # evidence as a CrossRef funder record — not the weaker
                # signal inferred from COI prose. CrossRef may already have
                # named this funder; note_industry_funder() deduplicates.
                analysis.note_industry_funder(agency)


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
    """
    if not epmc:
        return None
    results = epmc.get("resultList", {}).get("result", [])
    if not results:
        return None
    pmid = results[0].get("pmid")
    return str(pmid) if pmid else None


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
            )

        self._api_reachable = False
        analysis = _Analysis()

        with httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/{__version__} (mailto:{self.email})"},
        ) as client:
            # --- CrossRef (funder info) ---
            if doi:
                self._check_crossref(client, doi, analysis)

            # --- EuropePMC (full text / abstract, COI, data availability) ---
            epmc = self._fetch_europepmc(client, pmid, doi)
            if epmc:
                self._check_europepmc(client, epmc, analysis)

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
                self._check_trial_registration(
                    client, pmid, doi, analysis, epmc=epmc, pubmed=pubmed
                )

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
            )

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
            risk_indicators=analysis.indicators,
            full_text_analyzed=analysis.full_text_analyzed,
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
            funders = cr.get("message", {}).get("funder", [])
            if funders:
                analysis.award_funder_info()
                for funder in funders:
                    name = funder.get("name") or ""
                    if _is_industry_funder(name):
                        analysis.note_industry_funder(name)
            else:
                analysis.indicators.append("No funder information in CrossRef")

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

    def _check_europepmc(self, client: Any, epmc: dict, analysis: _Analysis) -> None:
        """Fold COI and data-availability signals from EuropePMC into *analysis*.

        COI and data-availability statements live in a paper's full text, not
        its abstract.  We therefore fetch the full text from EuropePMC when it
        is available (open-access articles) and scan that; we fall back to the
        abstract only when full text cannot be retrieved.

        Sets ``coi_disclosed`` tri-state: ``True`` (statement found), ``False``
        (full text scanned, none found), or — left as it was — ``None``
        (undeterminable: full text unavailable and no abstract signal).

        Industry ties disclosed in the COI statement itself (consultancies,
        speaker fees, …) are recorded through
        :meth:`_Analysis.note_industry_coi`, which is why this step needs no
        return value: it is only ever reached when full text was analyzed, and
        the confidence that belongs to a prose signal is the method's business
        rather than the caller's.
        """
        result_list = epmc.get("resultList", {}).get("result", [])
        if not result_list:
            return

        record = result_list[0]
        abstract_text = (record.get("abstractText") or "").lower()

        # Prefer full text — COI / data-availability statements are not in the
        # abstract. EuropePMC serves full text for open-access records.
        search_text = abstract_text
        if record.get("inEPMC") == "Y":
            full_text = self._fetch_europepmc_fulltext(
                client,
                record.get("source"),
                record.get("pmcid") or record.get("id"),
            )
            if full_text:
                search_text = full_text.lower()
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
            # Could not inspect full text; status is genuinely unknown.
            analysis.indicators.append(_INDICATOR_COI_UNKNOWN)

        # Data availability. The level is found into a local and reported once,
        # rather than assigned to the carrier and read back to decide the
        # credit: reading it back would score whatever the field happened to
        # hold on the way in, which is the positional hazard `_Analysis` exists
        # to remove, respelled as state. PubMed's `<DataBankList>` is the
        # field's second producer, so what this step reports is merged —
        # scoring and the retractable indicator both belong to the method.
        data_level = "unknown"
        for pattern, level in _DATA_PATTERNS.items():
            if pattern in search_text:
                data_level = level
                break
        analysis.note_data_availability(data_level)

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
    ) -> str | None:
        """Fetch full-text XML for an open-access EuropePMC record."""
        if not source or not ext_id:
            return None
        self._rate_limit()
        try:
            resp = client.get(
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{ext_id}/fullTextXML"
            )
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.debug("EuropePMC full-text fetch failed for %s/%s: %s", source, ext_id, e)
        return None

    def _check_pubmed(self, client: Any, pmid: str | None) -> _PubMedSignals:
        """Fetch and parse the PubMed record for *pmid*.

        Returns empty signals when there is no PMID to look up or the request
        fails, so the step is optional in every sense: it costs no request
        without an identifier and never breaks an analysis when NCBI is down.
        """
        if not pmid:
            return _PubMedSignals()
        xml_text = self._query_pubmed(client, pmid)
        if not xml_text:
            return _PubMedSignals()
        return _parse_pubmed_signals(xml_text)

    def _check_openalex(self, client: Any, doi: str, analysis: _Analysis) -> None:
        """Fold open-access status and citation count from OpenAlex into *analysis*."""
        oa = self._query_openalex(client, doi)
        if oa:
            oa_info = oa.get("open_access", {})
            if oa_info.get("is_oa"):
                analysis.score += SCORE_OPEN_ACCESS
            if oa.get("cited_by_count", 0) > 0:
                analysis.score += SCORE_CITED

    def _check_trial_registration(
        self,
        client: Any,
        pmid: str | None,
        doi: str | None,
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
        """
        pubmed = pubmed or _PubMedSignals()

        ct_ids = list(pubmed.trial_accessions) or self._find_trial_ids(client, pmid, doi, epmc=epmc)
        if ct_ids or pubmed.registration_not_checkable:
            analysis.trial_registered = True
            analysis.score += SCORE_TRIAL_REGISTERED

        if ct_ids:
            # `any()` over a generator stops at the first trial with posted
            # results, as the loop it replaces did. The outcome is this step's
            # own finding and deliberately not a read of
            # `analysis.results_compliant`: the indicator below reports that
            # ClinicalTrials.gov was asked and said no, which a flag arriving
            # from elsewhere must not be able to retract.
            compliant = any(
                self._check_trial_results(client, tid) for tid in ct_ids[:MAX_TRIAL_IDS_TO_CHECK]
            )
            if compliant:
                analysis.results_compliant = True
                analysis.score += SCORE_RESULTS_POSTED
            else:
                analysis.indicators.append(_INDICATOR_NO_POSTED_RESULTS)
        elif pubmed.registration_not_checkable:
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

    def _query_crossref(self, client: Any, doi: str) -> dict | None:
        """Query the CrossRef API for a DOI."""
        self._rate_limit()
        try:
            resp = client.get(
                f"https://api.crossref.org/works/{doi}",
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                self._api_reachable = True
                return resp.json()
        except Exception as e:
            logger.debug("CrossRef query failed for %s: %s", doi, e)
        return None

    def _query_europepmc(self, client: Any, query: str) -> dict | None:
        """Query the EuropePMC search API."""
        self._rate_limit()
        try:
            resp = client.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": query, "format": "json", "resultType": "core"},
            )
            if resp.status_code == 200:
                self._api_reachable = True
                return resp.json()
        except Exception as e:
            logger.debug("EuropePMC query failed: %s", e)
        return None

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

        self._rate_limit()
        try:
            resp = client.get(EFETCH_URL, params=params)
            if resp.status_code == 200:
                self._api_reachable = True
                return resp.text
        except Exception as e:
            logger.debug("PubMed query failed for %s: %s", pmid, e)
        return None

    def _query_openalex(self, client: Any, doi: str) -> dict | None:
        """Query the OpenAlex API for a DOI."""
        self._rate_limit()
        try:
            resp = client.get(
                f"https://api.openalex.org/works/doi:{doi}",
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                self._api_reachable = True
                return resp.json()
        except Exception as e:
            logger.debug("OpenAlex query failed for %s: %s", doi, e)
        return None

    def _find_trial_ids(
        self,
        client: Any,
        pmid: str | None,
        doi: str | None,
        *,
        epmc: dict | None = None,
    ) -> list[str]:
        """Return NCT ids that identify *this* paper's own registered trial.

        The abstract is scanned for ``NCT`` accession numbers, but a match is
        only credited as the paper's own registration when it appears next to
        registration language (see :data:`_REGISTRATION_CUE_RE`). Abstracts that
        list three or more distinct ids are treated as citation lists — e.g. a
        systematic review or pooled analysis enumerating its constituent trials —
        and return nothing, so a review is not credited for registrations that
        belong to studies it merely cites.

        Reuses the EuropePMC record already fetched by :meth:`analyze` when
        available, falling back to a fresh query only if it was not supplied,
        so the same search is not issued twice per document.
        """
        data = epmc
        if data is None:
            query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid}"
            data = self._query_europepmc(client, query)
        if not data:
            return []

        results = data.get("resultList", {}).get("result", [])
        if not results:
            return []

        # Strip XML/HTML markup so cue detection is not thrown off by tags.
        abstract = _TAG_RE.sub(" ", results[0].get("abstractText") or "")

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

    def _check_trial_results(self, client: Any, nct_id: str) -> bool:
        """Check if a ClinicalTrials.gov trial has posted results.

        Uses the v2 API's top-level ``hasResults`` boolean. An earlier
        implementation requested a ``ResultsSection`` field but read a
        ``resultsSection`` key, so it under-detected posted results.

        The request is narrowed to ``hasResults``, so that is the only key
        the response can carry; a missing key means the API did not answer
        the question and is reported as "no posted results" rather than
        guessed at from a payload that was never requested.
        """
        self._rate_limit()
        try:
            resp = client.get(
                f"https://clinicaltrials.gov/api/v2/studies/{nct_id}",
                params={"fields": "hasResults"},
            )
            if resp.status_code == 200:
                return bool(resp.json().get("hasResults"))
        except Exception as e:
            logger.debug("ClinicalTrials.gov query failed for %s: %s", nct_id, e)
        return False
