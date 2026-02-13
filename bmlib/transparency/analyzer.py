"""Multi-API transparency analyzer.

Queries PubMed, CrossRef, EuropePMC, ClinicalTrials.gov, and OpenAlex
to assess transparency of biomedical publications.

Requires ``httpx`` (install with ``pip install bmlib[transparency]``).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    calculate_risk_level,
)

logger = logging.getLogger(__name__)

# Known pharma / industry funder keywords
_INDUSTRY_KEYWORDS = [
    "pharma", "biotech", "therapeutics", "inc.", "corp.", "ltd.",
    "gmbh", "laboratories", "employee of", "speaker fee",
    "consultant for", "advisory board",
]

# Rate limiting
_MIN_REQUEST_INTERVAL = 0.35  # seconds between API calls


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
        pubmed_api_key: Optional[str] = None,
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
        pmid: Optional[str] = None,
        doi: Optional[str] = None,
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
        coi_disclosed = False
        trial_registered = False
        results_compliant = False

        with httpx.Client(
            timeout=15.0,
            headers={"User-Agent": f"bmlib/0.1 (mailto:{self.email})"},
        ) as client:
            # --- CrossRef (funder info) ---
            if doi:
                cr = self._query_crossref(client, doi)
                if cr:
                    funders = cr.get("message", {}).get("funder", [])
                    if funders:
                        score += 15
                        for funder in funders:
                            name = (funder.get("name") or "").lower()
                            if any(kw in name for kw in _INDUSTRY_KEYWORDS):
                                industry_funding = True
                                industry_confidence = max(industry_confidence, 0.8)
                                indicators.append(f"Industry funder: {funder.get('name')}")
                    else:
                        indicators.append("No funder information in CrossRef")

            # --- EuropePMC (abstract, COI) ---
            epmc = None
            if doi:
                epmc = self._query_europepmc(client, f'DOI:"{doi}"')
            elif pmid:
                epmc = self._query_europepmc(client, f'EXT_ID:{pmid}')

            if epmc:
                result_list = epmc.get("resultList", {}).get("result", [])
                if result_list:
                    paper = result_list[0]
                    abstract_text = (paper.get("abstractText") or "").lower()

                    # COI detection
                    coi_patterns = [
                        "conflict of interest", "competing interest",
                        "no conflict", "nothing to disclose",
                        "declare no", "financial disclosure",
                    ]
                    for pat in coi_patterns:
                        if pat in abstract_text:
                            coi_disclosed = True
                            score += 10
                            break

                    if not coi_disclosed:
                        indicators.append("No COI disclosure found")

                    # Data availability
                    data_patterns = {
                        "zenodo": "full_open",
                        "figshare": "full_open",
                        "dryad": "full_open",
                        "github": "full_open",
                        "available upon request": "on_request",
                        "upon reasonable request": "on_request",
                        "not available": "not_available",
                    }
                    for pattern, level in data_patterns.items():
                        if pattern in abstract_text:
                            data_level = level
                            break

                    if data_level == "full_open":
                        score += 20
                    elif data_level == "on_request":
                        score += 10
                    elif data_level == "not_available":
                        indicators.append("Data explicitly not available")

            # --- OpenAlex (additional metadata) ---
            if doi:
                oa = self._query_openalex(client, doi)
                if oa:
                    # Open access check
                    oa_info = oa.get("open_access", {})
                    if oa_info.get("is_oa"):
                        score += 15

                    # Cited by count as a weak signal
                    cited = oa.get("cited_by_count", 0)
                    if cited > 0:
                        score += 5

            # --- ClinicalTrials.gov (trial registration) ---
            if doi or pmid:
                ct_ids = self._find_trial_ids(client, pmid, doi)
                if ct_ids:
                    trial_registered = True
                    score += 20
                    # Check results posting
                    for tid in ct_ids[:3]:
                        has_results = self._check_trial_results(client, tid)
                        if has_results:
                            results_compliant = True
                            score += 15
                            break
                    if not results_compliant:
                        indicators.append("Registered trial without posted results")

        # Cap score
        score = min(score, 100)

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
            tier_downgrade_applied=(
                self.settings.tier_downgrade_amount
                if risk_level == TransparencyRisk.HIGH
                else 0
            ),
        )

    # --- API query helpers ---

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request = time.time()

    def _query_crossref(self, client, doi: str) -> dict | None:
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

    def _query_europepmc(self, client, query: str) -> dict | None:
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

    def _query_openalex(self, client, doi: str) -> dict | None:
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
        self, client, pmid: str | None, doi: str | None
    ) -> list[str]:
        """Look for clinical trial IDs linked to this paper."""
        ids: list[str] = []

        # Search EuropePMC for linked databanks
        query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid}"
        data = self._query_europepmc(client, query)
        if data:
            results = data.get("resultList", {}).get("result", [])
            if results:
                abstract = (results[0].get("abstractText") or "")
                # Extract NCT IDs from text
                nct_matches = re.findall(r"NCT\d{8}", abstract)
                ids.extend(nct_matches)

        return list(set(ids))

    def _check_trial_results(self, client, nct_id: str) -> bool:
        """Check if a ClinicalTrials.gov trial has posted results."""
        self._rate_limit()
        try:
            resp = client.get(
                f"https://clinicaltrials.gov/api/v2/studies/{nct_id}",
                params={"fields": "ResultsSection"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return bool(data.get("resultsSection"))
        except Exception as e:
            logger.debug("ClinicalTrials.gov query failed for %s: %s", nct_id, e)
        return False
