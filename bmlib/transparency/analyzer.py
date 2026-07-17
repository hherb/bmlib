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

Queries PubMed, CrossRef, EuropePMC, ClinicalTrials.gov, and OpenAlex
to assess transparency of biomedical publications.

Requires ``httpx`` (install with ``pip install bmlib[transparency]``).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    calculate_risk_level,
)

logger = logging.getLogger(__name__)

# ---- Known pharma / industry funder keywords ----
# Matched against CrossRef's structured funder names (short org-name strings).
_INDUSTRY_KEYWORDS = [
    "pharma",
    "biotech",
    "therapeutics",
    "inc.",
    "corp.",
    "ltd.",
    "gmbh",
    "laboratories",
]

# ---- Industry disclosure phrases ----
# Matched against the paper's COI/disclosure statement in the full text. Kept
# separate from _INDUSTRY_KEYWORDS: the generic org suffixes above ("inc.",
# "ltd.", …) match far too freely in running text, while these phrases never
# occur in a funder name.
_INDUSTRY_COI_KEYWORDS = [
    "employee of",
    "speaker fee",
    "consultant for",
    "advisory board",
]

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
# <sec sec-type="conflict">, <notes notes-type="COI-statement">, and case variants.
_COI_SECTION_RE = re.compile(
    r"<(fn|sec|notes)\b[^>]*-type=\"[^\"]*(?:coi|conflict|competing)[^\"]*\"[^>]*>(.*?)</\1>",
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


def _extract_coi_text(full_text: str) -> str:
    """Return the COI/disclosure portion of *full_text*, tag-stripped and lowercased.

    Prefers JATS-tagged COI containers; falls back to fixed-size windows
    following each COI cue phrase (see :data:`_COI_PATTERNS`) when the text
    carries no tagged section. Returns an empty string when no COI-like
    region is found.
    """
    sections = [m.group(2) for m in _COI_SECTION_RE.finditer(full_text)]
    sections += [m.group(1) for m in _COI_TITLED_SEC_RE.finditer(full_text)]
    if sections:
        return _TAG_RE.sub(" ", " ".join(sections)).lower()

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
    """
    for sentence in _SENTENCE_SPLIT_RE.split(coi_text):
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


class TransparencyAnalyzer:
    """Analyze transparency of a biomedical publication via external APIs.

    Args:
        email: Contact email for API politeness headers.
        pubmed_api_key: Optional NCBI API key for higher rate limits.
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
        # Set True by any query helper that receives a 200 response, so a run
        # in which every external API was unreachable can be reported as
        # UNKNOWN rather than scored 0 (which would read as HIGH risk).
        self._api_reachable: bool = False

    def analyze(
        self,
        document_id: str,
        *,
        pmid: str | None = None,
        doi: str | None = None,
    ) -> TransparencyResult:
        """Run transparency analysis for a single document.

        At least one of *pmid* or *doi* must be provided.
        """
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
            )

        self._api_reachable = False
        score = 0
        indicators: list[str] = []
        industry_funding = False
        industry_confidence = 0.0
        data_level = "unknown"
        coi_disclosed: bool | None = None
        trial_registered = False
        results_compliant = False
        full_text_analyzed = False

        with httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/0.1 (mailto:{self.email})"},
        ) as client:
            # --- CrossRef (funder info) ---
            if doi:
                score, industry_funding, industry_confidence, indicators = self._check_crossref(
                    client,
                    doi,
                    score,
                    industry_funding,
                    industry_confidence,
                    indicators,
                )

            # --- EuropePMC (full text / abstract, COI, data availability) ---
            epmc = self._fetch_europepmc(client, pmid, doi)
            if epmc:
                (
                    coi_disclosed,
                    data_level,
                    score,
                    indicators,
                    full_text_analyzed,
                    industry_coi,
                ) = self._check_europepmc(client, epmc, score, indicators)
                if industry_coi:
                    industry_funding = True
                    industry_confidence = max(industry_confidence, TEXT_INDUSTRY_CONFIDENCE)
                    indicators.append("Industry ties disclosed in COI statement")

            # --- OpenAlex (additional metadata) ---
            if doi:
                score = self._check_openalex(client, doi, score)

            # --- ClinicalTrials.gov (trial registration) ---
            if doi or pmid:
                trial_registered, results_compliant, score, indicators = (
                    self._check_trial_registration(
                        client,
                        pmid,
                        doi,
                        score,
                        indicators,
                        epmc=epmc,
                    )
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
            )

        score = min(score, MAX_TRANSPARENCY_SCORE)

        risk_level = calculate_risk_level(
            score=score,
            industry_funding=industry_funding,
            data_availability=data_level,
            coi_disclosed=coi_disclosed,
            settings=self.settings,
        )

        return TransparencyResult(
            document_id=document_id,
            transparency_score=score,
            risk_level=risk_level,
            industry_funding_detected=industry_funding,
            industry_funding_confidence=industry_confidence,
            data_availability_level=data_level,
            coi_disclosed=coi_disclosed,
            trial_registered=trial_registered,
            trial_results_compliant=results_compliant,
            risk_indicators=indicators,
            full_text_analyzed=full_text_analyzed,
            tier_downgrade_applied=(
                self.settings.tier_downgrade_amount if risk_level == TransparencyRisk.HIGH else 0
            ),
        )

    # --- Analysis sub-steps ---

    def _check_crossref(
        self,
        client: Any,
        doi: str,
        score: int,
        industry_funding: bool,
        industry_confidence: float,
        indicators: list[str],
    ) -> tuple[int, bool, float, list[str]]:
        """Query CrossRef for funder information."""
        cr = self._query_crossref(client, doi)
        if cr:
            funders = cr.get("message", {}).get("funder", [])
            if funders:
                score += SCORE_FUNDER_INFO
                for funder in funders:
                    name = (funder.get("name") or "").lower()
                    if any(kw in name for kw in _INDUSTRY_KEYWORDS):
                        industry_funding = True
                        industry_confidence = max(industry_confidence, DEFAULT_INDUSTRY_CONFIDENCE)
                        indicators.append(f"Industry funder: {funder.get('name')}")
            else:
                indicators.append("No funder information in CrossRef")
        return score, industry_funding, industry_confidence, indicators

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
        score: int,
        indicators: list[str],
    ) -> tuple[bool | None, str, int, list[str], bool, bool]:
        """Extract COI and data-availability signals from EuropePMC.

        COI and data-availability statements live in a paper's full text, not
        its abstract.  We therefore fetch the full text from EuropePMC when it
        is available (open-access articles) and scan that; we fall back to the
        abstract only when full text cannot be retrieved.

        Returns ``(coi_disclosed, data_level, score, indicators,
        full_text_analyzed, industry_coi)`` where ``coi_disclosed`` is
        tri-state: ``True`` (statement found), ``False`` (full text scanned,
        none found), or ``None`` (undeterminable — full text unavailable and
        no abstract signal). ``industry_coi`` is ``True`` when the full-text
        COI/disclosure statement discloses industry ties (consultancies,
        speaker fees, …); it is only ever set when full text was analyzed.
        """
        coi_disclosed: bool | None = None
        data_level = "unknown"
        full_text_analyzed = False
        industry_coi = False

        result_list = epmc.get("resultList", {}).get("result", [])
        if not result_list:
            return coi_disclosed, data_level, score, indicators, full_text_analyzed, industry_coi

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
                full_text_analyzed = True

        # COI detection (a COI/disclosure statement counts as "disclosed",
        # including a statement that there is nothing to declare).
        if any(pat in search_text for pat in _COI_PATTERNS):
            coi_disclosed = True
            score += SCORE_COI_DISCLOSED
        elif full_text_analyzed:
            # Full text inspected and no COI statement found -> explicitly absent.
            coi_disclosed = False
            indicators.append("No COI disclosure found in full text")
        else:
            # Could not inspect full text; status is genuinely unknown.
            indicators.append("COI disclosure status unknown (full text unavailable)")

        # Industry ties disclosed in the COI statement itself ("consultant
        # for X", "speaker fees from Y"). Scanned only in full text — an
        # abstract rarely carries a real disclosure statement — and only
        # within the COI/disclosure region to avoid false positives from
        # references or affiliations.
        if full_text_analyzed:
            industry_coi = _discloses_industry_ties(_extract_coi_text(search_text))

        # Data availability
        for pattern, level in _DATA_PATTERNS.items():
            if pattern in search_text:
                data_level = level
                break
        if data_level == "full_open":
            score += SCORE_DATA_FULL_OPEN
        elif data_level == "on_request":
            score += SCORE_DATA_ON_REQUEST
        elif data_level == "not_available":
            indicators.append("Data explicitly not available")

        return coi_disclosed, data_level, score, indicators, full_text_analyzed, industry_coi

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

    def _check_openalex(
        self,
        client: Any,
        doi: str,
        score: int,
    ) -> int:
        """Check open-access status and citation count via OpenAlex."""
        oa = self._query_openalex(client, doi)
        if oa:
            oa_info = oa.get("open_access", {})
            if oa_info.get("is_oa"):
                score += SCORE_OPEN_ACCESS
            if oa.get("cited_by_count", 0) > 0:
                score += SCORE_CITED
        return score

    def _check_trial_registration(
        self,
        client: Any,
        pmid: str | None,
        doi: str | None,
        score: int,
        indicators: list[str],
        *,
        epmc: dict | None = None,
    ) -> tuple[bool, bool, int, list[str]]:
        """Check ClinicalTrials.gov registration and results posting."""
        trial_registered = False
        results_compliant = False

        ct_ids = self._find_trial_ids(client, pmid, doi, epmc=epmc)
        if ct_ids:
            trial_registered = True
            score += SCORE_TRIAL_REGISTERED
            for tid in ct_ids[:MAX_TRIAL_IDS_TO_CHECK]:
                if self._check_trial_results(client, tid):
                    results_compliant = True
                    score += SCORE_RESULTS_POSTED
                    break
            if not results_compliant:
                indicators.append("Registered trial without posted results")

        return trial_registered, results_compliant, score, indicators

    # --- API query helpers ---

    def _rate_limit(self) -> None:
        """Enforce minimum interval between outgoing HTTP requests."""
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

        Uses the v2 API's top-level ``hasResults`` boolean. The previous
        implementation requested a ``ResultsSection`` field but read a
        ``resultsSection`` key, so it under-detected posted results.
        """
        self._rate_limit()
        try:
            resp = client.get(
                f"https://clinicaltrials.gov/api/v2/studies/{nct_id}",
                params={"fields": "hasResults"},
            )
            if resp.status_code == 200:
                data = resp.json()
                has_results = data.get("hasResults")
                if has_results is None:
                    # Fall back to presence of a results section in the payload.
                    has_results = bool(data.get("resultsSection"))
                return bool(has_results)
        except Exception as e:
            logger.debug("ClinicalTrials.gov query failed for %s: %s", nct_id, e)
        return False
