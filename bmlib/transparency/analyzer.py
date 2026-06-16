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
_INDUSTRY_KEYWORDS = [
    "pharma",
    "biotech",
    "therapeutics",
    "inc.",
    "corp.",
    "ltd.",
    "gmbh",
    "laboratories",
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

# ---- COI detection patterns ----
_COI_PATTERNS = [
    "conflict of interest",
    "competing interest",
    "no conflict",
    "nothing to disclose",
    "declare no",
    "financial disclosure",
]

# ---- Data availability patterns ----
_DATA_PATTERNS: dict[str, str] = {
    "zenodo": "full_open",
    "figshare": "full_open",
    "dryad": "full_open",
    "github": "full_open",
    "available upon request": "on_request",
    "upon reasonable request": "on_request",
    "not available": "not_available",
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
                coi_disclosed, data_level, score, indicators, full_text_analyzed = (
                    self._check_europepmc(client, epmc, score, indicators)
                )

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
                    )
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
    ) -> tuple[bool | None, str, int, list[str], bool]:
        """Extract COI and data-availability signals from EuropePMC.

        COI and data-availability statements live in a paper's full text, not
        its abstract.  We therefore fetch the full text from EuropePMC when it
        is available (open-access articles) and scan that; we fall back to the
        abstract only when full text cannot be retrieved.

        Returns ``(coi_disclosed, data_level, score, indicators,
        full_text_analyzed)`` where ``coi_disclosed`` is tri-state:
        ``True`` (statement found), ``False`` (full text scanned, none found),
        or ``None`` (undeterminable — full text unavailable and no abstract
        signal).
        """
        coi_disclosed: bool | None = None
        data_level = "unknown"
        full_text_analyzed = False

        result_list = epmc.get("resultList", {}).get("result", [])
        if not result_list:
            return coi_disclosed, data_level, score, indicators, full_text_analyzed

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

        return coi_disclosed, data_level, score, indicators, full_text_analyzed

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
    ) -> tuple[bool, bool, int, list[str]]:
        """Check ClinicalTrials.gov registration and results posting."""
        trial_registered = False
        results_compliant = False

        ct_ids = self._find_trial_ids(client, pmid, doi)
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
                return resp.json()
        except Exception as e:
            logger.debug("OpenAlex query failed for %s: %s", doi, e)
        return None

    def _find_trial_ids(
        self,
        client: Any,
        pmid: str | None,
        doi: str | None,
    ) -> list[str]:
        """Look for clinical trial IDs linked to this paper."""
        ids: list[str] = []

        query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid}"
        data = self._query_europepmc(client, query)
        if data:
            results = data.get("resultList", {}).get("result", [])
            if results:
                abstract = results[0].get("abstractText") or ""
                nct_matches = re.findall(r"NCT\d{8}", abstract)
                ids.extend(nct_matches)

        return list(set(ids))

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
