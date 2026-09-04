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
# two COI lines: a structured <CoiStatement> can establish a disclosure that the
# full-text scan missed, and leaving either line in place would then contradict
# `coi_disclosed=True`.
_INDICATOR_NO_COI_IN_FULLTEXT = "No COI disclosure found in full text"
_INDICATOR_COI_UNKNOWN = "COI disclosure status unknown (full text unavailable)"
_INDICATOR_COI_IN_PUBMED = "COI disclosure found in PubMed record"
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
            something other than served XML still owns the rest. That leaves
            one shape of the hazard this guard was built for: a body truncated
            *between* tags opens no unterminated construct, so it is accepted
            and scanned as a complete article. Issue #183.

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
    ) -> str | None:
        """Fetch this article's own full-text XML for an open-access EuropePMC record.

        Nested articles are removed here rather than at each scan, so there is
        one door into the module for a string that has to be the article's:
        every reader downstream — the tagged-COI match, the cue-phrase scan,
        the data-availability patterns and the industry-COI extraction — takes
        it from this return value. ``None`` means "no full text", whether
        because none was served, because what was served could not be
        segmented into the article's own text (see
        :func:`_strip_nested_articles`), or because none of it was the
        article's; the caller falls back to the abstract in every case, which
        leaves the COI status *unknown* rather than absent — it can still be
        set ``True`` from the abstract, but never ``False``, and only ``False``
        triggers the missing-COI downgrade. That is a claim about *that* rule
        and not about the result: ``score < score_threshold`` is the first
        test in :func:`~bmlib.transparency.models.calculate_risk_level`, so an
        article can still reach ``HIGH`` by losing the points its full text
        would have scored. Falling back is cheaper than being wrong, but it
        is not free, and issue #161 is where that is tracked. The three
        segmentation outcomes
        WARN, each naming which it was — an unclosed region, markup that never
        terminates, and a body that was entirely nested articles are different
        claims about what arrived. A non-200 does not warn, because there is
        nothing there to report.
        """
        if not source or not ext_id:
            return None
        self._rate_limit()
        # Only the request is wrapped. `_strip_nested_articles` is bmlib's own
        # computation over a string, so anything it raises is a bmlib defect,
        # and inside this `except` it would be logged at DEBUG as "fetch
        # failed" and reported to the caller as "EuropePMC served nothing" —
        # the shape `fulltext/service.py` keeps `_BUG_TYPES` for.
        try:
            resp = client.get(
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{ext_id}/fullTextXML"
            )
        except Exception as e:
            logger.debug("EuropePMC full-text fetch failed for %s/%s: %s", source, ext_id, e)
            return None
        if resp.status_code != 200:
            return None
        # The one documented raise, on its own line and caught on its own
        # terms: a truncated body can reach it, so it is not a bmlib defect,
        # and the wider `except` above would have logged it at DEBUG as a
        # fetch failure — the mischaracterisation #159 moved this call out of
        # that block to avoid. Anything *else* this computation raises IS a
        # bmlib defect, and the narrow type here is what keeps it unswallowed.
        try:
            article_xml = _strip_nested_articles(resp.text)
        except _UnterminatedMarkupError as e:
            logger.warning(
                "EuropePMC full text for %s/%s is not well-formed (%s); "
                "scanning the abstract instead",
                source,
                ext_id,
                e,
            )
            return None
        if article_xml is None:
            # A deposit can reach this, so it is not a bmlib defect: WARNING,
            # and the analysis proceeds on the abstract.
            logger.warning(
                "EuropePMC full text for %s/%s leaves an unclosed nested article; "
                "scanning the abstract instead",
                source,
                ext_id,
            )
            return None
        if not article_xml.strip():
            # Everything served was nested. The caller's `if full_text:` would
            # read the empty string as "nothing was served" and fall back
            # silently, so it is reported here instead — the one outcome of
            # this function that would otherwise reach storage with no signal
            # at all. Measured empty: all 3,389 carriers across the baseline
            # corpus and an 880-article EuropePMC draw keep their <body>, the
            # least of them retaining 32.2% of its bytes.
            logger.warning(
                "EuropePMC full text for %s/%s is entirely nested articles; "
                "scanning the abstract instead",
                source,
                ext_id,
            )
            return None
        return article_xml

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
