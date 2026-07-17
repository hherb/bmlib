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

"""Rule-based (LLM-free) extractors for paper characteristics.

Pure functions that estimate study characteristics with keyword and regex
heuristics: study-type detection with exclusion-context guarding, sample-size
extraction with logarithmic scoring, and power-calculation / confidence-interval
signals. They produce :class:`bmlib.quality.scoring_models.DimensionScore`
objects with a full audit trail, and make a cheap pre-filter or fallback for
the LLM-based tiers in :mod:`bmlib.quality`.

All functions are stateless and can be tested in isolation.
"""

from __future__ import annotations

import math
import re
from typing import Any

from bmlib.quality.scoring_models import (
    DIMENSION_SAMPLE_SIZE,
    DIMENSION_STUDY_DESIGN,
    DimensionScore,
)

# Priority order for study-type detection (highest evidence level first).
# quasi_experimental is checked BEFORE rct so "non-randomized trial" does not
# match RCT keywords like "randomized trial".
STUDY_TYPE_PRIORITY = [
    "systematic_review",
    "meta_analysis",
    "quasi_experimental",
    "rct",
    "pilot_feasibility",
    "interventional_single_arm",
    "cohort_prospective",
    "cohort_retrospective",
    "case_control",
    "cross_sectional",
    "case_series",
    "case_report",
]

# Default study-type keywords.
DEFAULT_STUDY_TYPE_KEYWORDS = {
    "systematic_review": ["systematic review", "systematic literature review"],
    "meta_analysis": ["meta-analysis", "meta analysis", "pooled analysis"],
    "quasi_experimental": [
        "non-randomized trial",
        "non-randomised trial",
        "nonrandomized trial",
        "nonrandomised trial",
        "quasi-experimental",
        "quasi experimental",
        "single-arm trial",
        "single arm trial",
        "open-label trial",
    ],
    "rct": [
        "randomized controlled trial",
        "randomised controlled trial",
        "RCT",
        "randomized trial",
        "randomised trial",
        "random allocation",
        "randomly assigned",
        "double-blind randomized",
        "double-blind randomised",
    ],
    "pilot_feasibility": [
        "pilot study",
        "pilot trial",
        "feasibility study",
        "feasibility trial",
        "proof-of-concept study",
        "proof of concept study",
    ],
    "interventional_single_arm": [
        "open-label",
        "open-labeled",
        "open label",
        "open labeled",
        "single-arm trial",
        "single-arm study",
        "single arm trial",
        "single arm study",
        "prospective protocol",
        "prospective intervention",
        "uncontrolled trial",
        "non-randomized trial",
        "non-randomised trial",
        "before-and-after study",
        "pre-post study",
        "pretest-posttest",
    ],
    "cohort_prospective": [
        "prospective cohort",
        "prospective study",
        "longitudinal cohort",
        "followed prospectively",
        "prospective follow-up",
        "prospective observation",
    ],
    "cohort_retrospective": ["retrospective cohort", "retrospective study"],
    "case_control": ["case-control", "case control study"],
    "cross_sectional": ["cross-sectional", "cross sectional study", "prevalence study"],
    "case_series": ["case series", "case-series"],
    "case_report": ["case report", "case study"],
}

# Keywords that EXCLUDE a match for specific study types. If any exclusion
# pattern appears near the keyword, the match is rejected.
STUDY_TYPE_EXCLUSIONS = {
    "rct": [
        "non-randomized",
        "non-randomised",
        "nonrandomized",
        "nonrandomised",
        "not randomized",
        "not randomised",
        "without randomization",
        "without randomisation",
        "quasi-experimental",
        "quasi experimental",
    ]
}

# How far before a keyword to search for an exclusion pattern (characters).
EXCLUSION_CONTEXT_WINDOW = 50

# Default study-type hierarchy scores.
DEFAULT_STUDY_TYPE_HIERARCHY = {
    "systematic_review": 10.0,
    "meta_analysis": 10.0,
    "rct": 8.0,
    "quasi_experimental": 7.0,
    "pilot_feasibility": 6.5,
    "interventional_single_arm": 7.0,
    "cohort_prospective": 6.0,
    "cohort_retrospective": 5.0,
    "case_control": 4.0,
    "cross_sectional": 3.0,
    "scoping_review": 3.0,
    "narrative_review": 2.5,
    "expert_opinion": 2.0,
    "case_series": 2.0,
    "case_report": 1.0,
}

# Sample-size regex patterns.
SAMPLE_SIZE_PATTERNS = [
    r"n\s*=\s*(\d+)",
    r"N\s*=\s*(\d+)",
    r"(\d+)\s+participants",
    r"(\d+)\s+subjects",
    r"(\d+)\s+patients",
    r"sample\s+size\s+of\s+(\d+)",
    r"total\s+of\s+(\d+)\s+(?:participants|subjects|patients)",
    r"enrolled\s+(\d+)\s+(?:participants|subjects|patients)",
    r"recruited\s+(\d+)\s+(?:participants|subjects|patients)",
]

# Power-calculation keywords.
POWER_CALCULATION_KEYWORDS = [
    "power calculation",
    "power analysis",
    "sample size calculation",
    "calculated sample size",
    "statistical power",
    "power to detect",
]

# Confidence-interval patterns.
CI_PATTERNS = [
    r"confidence interval",
    r"\bCI\b",
    r"95%\s*CI",
    r"\[\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*\]",
    r"\(\s*\d+\.?\d*\s*-\s*\d+\.?\d*\s*\)",
]


def extract_text_context(text: str, keyword: str, context_chars: int = 50) -> str:
    """Return a snippet of *text* around the first occurrence of *keyword*.

    Adds ellipses where the snippet is truncated. Returns ``""`` if the
    keyword is not present.
    """
    keyword_pos = text.find(keyword)
    if keyword_pos == -1:
        return ""

    start = max(0, keyword_pos - context_chars)
    end = min(len(text), keyword_pos + len(keyword) + context_chars)

    context = text[start:end]
    if start > 0:
        context = "..." + context
    if end < len(text):
        context = context + "..."

    return context


def prepare_extractor_search_text(document: dict[str, Any]) -> str:
    """Choose the best text from *document* for rule-based extraction.

    Prefers a substantial ``full_text`` (longer than the abstract), otherwise
    falls back to ``abstract`` + ``methods_text``.
    """
    full_text = document.get("full_text", "") or ""
    abstract = document.get("abstract", "") or ""
    methods = document.get("methods_text", "") or ""

    if full_text and len(full_text) > len(abstract):
        return full_text

    return f"{abstract} {methods}"


def find_sample_size(text: str, min_n: int = 5, max_n: int = 1_000_000) -> int | None:
    """Find the sample size in *text*, returning the largest plausible match.

    Args:
        text: Text to search.
        min_n: Minimum valid sample size.
        max_n: Maximum valid sample size.

    Returns:
        The largest matched size within ``[min_n, max_n]``, or ``None``.
    """
    found_sizes = []
    for pattern in SAMPLE_SIZE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            size = int(match.group(1))
            if min_n <= size <= max_n:
                found_sizes.append(size)

    if not found_sizes:
        return None

    return max(found_sizes)


def calculate_sample_size_score(n: int, log_multiplier: float = 2.0) -> float:
    """Score a sample size on a 0-10 scale as ``log10(n) * log_multiplier``."""
    if n <= 0:
        return 0.0

    score = math.log10(n) * log_multiplier
    return min(10.0, max(0.0, score))


def has_power_calculation(text: str) -> bool:
    """Return whether *text* mentions a power/sample-size calculation."""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in POWER_CALCULATION_KEYWORDS)


def find_power_calc_context(text: str) -> str:
    """Return a snippet around the first power-calculation mention, if any."""
    text_lower = text.lower()
    for keyword in POWER_CALCULATION_KEYWORDS[:3]:
        if keyword in text_lower:
            return extract_text_context(text_lower, keyword)
    return ""


def has_ci_reporting(text: str) -> bool:
    """Return whether *text* reports confidence intervals."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in CI_PATTERNS)


def has_exclusion_pattern(
    text: str,
    keyword: str,
    exclusion_patterns: list[str],
    context_window: int = EXCLUSION_CONTEXT_WINDOW,
) -> bool:
    """Return whether an exclusion pattern appears just before *keyword*.

    Prevents false positives such as "non-randomized trial" matching as RCT
    when searching for "randomized trial".

    Args:
        text: Full (lowercase) text being searched.
        keyword: The matched (lowercase) keyword.
        exclusion_patterns: Patterns that should invalidate the match.
        context_window: Characters before the keyword to inspect.
    """
    keyword_pos = text.find(keyword)
    if keyword_pos == -1:
        return False

    start_pos = max(0, keyword_pos - context_window)
    context_before = text[start_pos : keyword_pos + len(keyword)]

    return any(exclusion.lower() in context_before for exclusion in exclusion_patterns)


def extract_study_type(
    document: dict[str, Any],
    keywords_config: dict[str, list[str]] | None = None,
    hierarchy_config: dict[str, float] | None = None,
    priority_order: list[str] | None = None,
    exclusions_config: dict[str, list[str]] | None = None,
) -> DimensionScore:
    """Detect study type by keyword matching, with exclusion-context guarding.

    Searches ``full_text`` when available (else abstract + methods), tries each
    type in priority order (systematic review > quasi-experimental > RCT > …),
    and rejects matches whose exclusion patterns fire. Returns a
    :class:`DimensionScore` for the study-design dimension with an audit trail;
    defaults to "unknown" at a neutral score when nothing matches.
    """
    if keywords_config is None:
        keywords_config = DEFAULT_STUDY_TYPE_KEYWORDS
    if hierarchy_config is None:
        hierarchy_config = DEFAULT_STUDY_TYPE_HIERARCHY
    if priority_order is None:
        priority_order = STUDY_TYPE_PRIORITY
    if exclusions_config is None:
        exclusions_config = STUDY_TYPE_EXCLUSIONS

    search_text = prepare_extractor_search_text(document).lower()

    for study_type in priority_order:
        keywords = keywords_config.get(study_type, [])
        exclusions = exclusions_config.get(study_type, [])

        for keyword in keywords:
            keyword_lower = keyword.lower()
            if keyword_lower in search_text:
                if exclusions and has_exclusion_pattern(search_text, keyword_lower, exclusions):
                    continue

                score = hierarchy_config.get(study_type, 5.0)
                dimension_score = DimensionScore(
                    dimension_name=DIMENSION_STUDY_DESIGN,
                    score=score,
                )
                dimension_score.add_detail(
                    component="study_type",
                    value=study_type,
                    contribution=score,
                    evidence=extract_text_context(search_text, keyword_lower),
                    reasoning=(
                        f"Matched keyword '{keyword}' indicating {study_type.replace('_', ' ')}"
                    ),
                )
                return dimension_score

    dimension_score = DimensionScore(dimension_name=DIMENSION_STUDY_DESIGN, score=5.0)
    dimension_score.add_detail(
        component="study_type",
        value="unknown",
        contribution=5.0,
        reasoning="No study type keywords matched - assigned neutral score",
    )
    return dimension_score


def extract_sample_size_dimension(
    document: dict[str, Any],
    scoring_config: dict[str, float] | None = None,
) -> DimensionScore:
    """Extract sample size and score it, with power/CI bonuses.

    Applies logarithmic scoring to the extracted size, then adds bonuses when
    a power calculation and/or confidence intervals are reported (capped at
    10). Returns a :class:`DimensionScore` with an audit trail; a score of 0
    when no sample size is found.
    """
    if scoring_config is None:
        scoring_config = {
            "log_multiplier": 2.0,
            "power_calculation_bonus": 2.0,
            "ci_reported_bonus": 0.5,
        }

    log_multiplier = scoring_config.get("log_multiplier", 2.0)
    power_bonus = scoring_config.get("power_calculation_bonus", 2.0)
    ci_bonus = scoring_config.get("ci_reported_bonus", 0.5)

    search_text = prepare_extractor_search_text(document)
    sample_size = find_sample_size(search_text)

    if sample_size is None:
        dimension_score = DimensionScore(dimension_name=DIMENSION_SAMPLE_SIZE, score=0.0)
        dimension_score.add_detail(
            component="extracted_n",
            value="not_found",
            contribution=0.0,
            reasoning="No sample size could be extracted from text",
        )
        return dimension_score

    base_score = calculate_sample_size_score(sample_size, log_multiplier)
    dimension_score = DimensionScore(dimension_name=DIMENSION_SAMPLE_SIZE, score=base_score)
    dimension_score.add_detail(
        component="extracted_n",
        value=str(sample_size),
        contribution=base_score,
        reasoning=f"Log10({sample_size}) * {log_multiplier} = {base_score:.2f}",
    )

    if has_power_calculation(search_text):
        dimension_score.score = min(10.0, dimension_score.score + power_bonus)
        dimension_score.add_detail(
            component="power_calculation",
            value="yes",
            contribution=power_bonus,
            evidence=find_power_calc_context(search_text),
            reasoning=f"Power calculation mentioned, bonus +{power_bonus}",
        )

    if has_ci_reporting(search_text):
        dimension_score.score = min(10.0, dimension_score.score + ci_bonus)
        dimension_score.add_detail(
            component="ci_reporting",
            value="yes",
            contribution=ci_bonus,
            reasoning=f"Confidence intervals reported, bonus +{ci_bonus}",
        )

    return dimension_score


def get_extracted_sample_size(dimension_score: DimensionScore) -> int | None:
    """Return the numeric sample size recorded in a sample-size dimension."""
    if not dimension_score.details:
        return None

    extracted_value = dimension_score.details[0].extracted_value
    if extracted_value and extracted_value.isdigit():
        return int(extracted_value)
    return None


def get_extracted_study_type(dimension_score: DimensionScore) -> str | None:
    """Return the study-type string recorded in a study-design dimension."""
    if not dimension_score.details:
        return None
    return dimension_score.details[0].extracted_value
