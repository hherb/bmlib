"""Transparency analysis for biomedical publications.

Queries external APIs (PubMed, CrossRef, ClinicalTrials.gov, EuropePMC,
OpenAlex) to assess funding, data availability, COI disclosure, and
trial registration compliance.
"""

from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    calculate_risk_level,
)
from bmlib.transparency.analyzer import TransparencyAnalyzer

__all__ = [
    "TransparencyAnalyzer",
    "TransparencyResult",
    "TransparencyRisk",
    "TransparencySettings",
    "calculate_risk_level",
]
