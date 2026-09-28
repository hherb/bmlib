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
from collections.abc import Iterator
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

# A count as a paper deposits it (issue #294). The patterns used to capture
# ``(\d+)``, which cannot span a thousands separator, so "12,345 patients" read
# as 345 and "n = 12,345" as 12. The separators are the ones a 5,976-abstract
# Europe PMC draw deposits beside a count: a comma (192), and the space family —
# an ASCII space (8, every one a genuine grouping), a no-break space and a thin
# space. A period is not one of them: "2.9 patients" is a decimal, and the one
# period-grouped count in the draw ("35.020 patients") is refused rather than
# guessed at. The lookarounds are what refuse it: a count never starts or ends
# inside a larger numeric token, so a fragment is not captured at all, where
# ``(\d+)`` returned the digits after a decimal point (0.32 -> 32).
_COUNT = (
    r"(?<![\d.,])"
    r"(\d{1,3}(?:,\d{3})+|\d{1,3}(?:[ \u00a0\u2009\u202f]\d{3})+|\d+)"
    r"(?![.,]?\d)"
)

# Sample-size regex patterns (matched case-insensitively, so "n =" covers "N =").
# Each captures one ``_COUNT``; ``find_sample_size`` strips the separators.
# ``n`` is a whole word: without the boundary, "mean = 118.45" and
# "postintervention = 733.88" each ended in "n =" (20 abstracts of 5,976, and
# the returned size in 4).
SAMPLE_SIZE_PATTERNS = [
    rf"\bn\s*=\s*{_COUNT}",
    rf"{_COUNT}\s+participants",
    rf"{_COUNT}\s+subjects",
    rf"{_COUNT}\s+patients",
    rf"sample\s+size\s+of\s+{_COUNT}",
    rf"total\s+of\s+{_COUNT}\s+(?:participants|subjects|patients)",
    rf"enrolled\s+{_COUNT}\s+(?:participants|subjects|patients)",
    rf"recruited\s+{_COUNT}\s+(?:participants|subjects|patients)",
]

# Power-calculation keywords: phrases that name the calculation itself (issue
# #297). "statistical power" and "power to detect" were members and are not:
# of 22 power-positive abstracts in the draw only 9 reported the paper's own
# calculation, and most of the other 13 were these two phrases *discussing*
# power — "low statistical power", "future studies with sufficient statistical
# power", "insufficient power to detect". A power the study states as a number
# is ``QUANTIFIED_POWER_PATTERN``'s. Each space in a keyword matches any run of
# whitespace, since deposits write "power\u00a0analysis".
POWER_CALCULATION_KEYWORDS = [
    "power calculation",
    "power analysis",
    "power analyses",
    "sample size calculation",
    "calculated sample size",
]

# The same claim in shapes a phrase list cannot hold: the power-analysis program
# ("calculated using G*Power"), and the phrase written the other way round
# ("the sample size was calculated"). "determined" is not among the verbs — "the
# sample size was determined by the number of eligible patients" is the
# opposite claim.
POWER_CALCULATION_PATTERNS = [
    r"\bG\s*\*\s*Power\b",
    r"\bsample\s+sizes?\s+(?:was|were|has\s+been|had\s+been)\s+(?:calculated|computed)\b",
]

# A power the study states as a quantity: "80% power", "80% statistical power",
# "a statistical power of 80 %", "power of 0.80", "power (0.80)". The quantity
# is range-checked by ``_is_a_study_power`` — a calculation sets power at 50%
# or above — which is what keeps a cycling abstract's "mean power of 1.0%" out.
# A test's "predictive power of 88%" is not a study's power either.
# ``\u200b`` sits among the spaces because the draw deposits "80\u00a0\u200b%".
QUANTIFIED_POWER_PATTERN = (
    r"(?P<pct>\d{1,3}(?:\.\d+)?)[\s\u200b]*%[\s\u200b]*(?:statistical\s+)?power\b"
    r"|(?<!predictive\s)(?<!discriminative\s)(?<!discriminatory\s)"
    r"\bpower[\s\u200b]*(?:of|=|:|was|at)?[\s\u200b]*\(?[\s\u200b]*(?:at\s+least[\s\u200b]+)?"
    r"(?:(?P<pct2>\d{1,3}(?:\.\d+)?)[\s\u200b]*%|(?P<frac>0?\.\d+)(?![\d%]))"
)

# Markup a deposit may put between the parts of a CI report: "95% <i>CI</i>",
# "CI<sub>95%</sub>", or bmlib's own Markdown emphasis.
_CI_MARKUP = r"(?:\s|<[^>]+>|[*_])*"

# Confidence-interval patterns (issue #297). A bare "CI" token used to count on
# its own, and the draw credited "cardiac index (CI)", "cochlear implant (CI)",
# "cognitive impairment (CI)" and "chronicity index (CI)" with it — 16
# abstracts, and in full text curies ("Ci/mmol"), chemical ionization and a
# drug-combination index as well. A "CI" now counts beside a percentage, a
# number or a bound ("Lower CI", a table's column header). Only after a
# percentage may it be lowercase ("95% ci"): elsewhere the case is what keeps
# "cis-9" and "Ci/mmol" out. The bare-numeric bracket/range forms require a
# decimal point in both numbers so integer citation markers like "[12, 15]" and
# year ranges like "(2010-2015)" do not count as CI reporting.
CI_PATTERNS = [
    r"confidence\s+intervals?",
    rf"\d\s*%\s*-?{_CI_MARKUP}CIs?\b",
    rf"(?<!\w)(?-i:CIs?)\b{_CI_MARKUP}(?:of\s+)?[:=,]?\s*"
    r"(?:\d{2}(?:\.\d+)?\s*%|[\[(]?\s*[-\u2212\u2013\u00b1]?\d)",
    r"\b(?:lower|upper)[\s-]+(?-i:CIs?)\b|(?<!\w)(?-i:CIs?)[\s-]+(?:lower|upper|limits?|bounds?)\b",
    r"\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\]",
    r"\(\s*\d+\.\d+\s*-\s*\d+\.\d+\s*\)",
]

# A denial that governs a mention (issue #297): a negation at most three words
# before it, with nothing but words in between, or a negated verb of reporting
# straight after it. Narrow on purpose. A confidence interval is reported next
# to exactly the vocabulary a wider window reads — "HR 0.96, 95% CI 0.46-1.49),
# with no difference" — and a +-40-character window of the Rust port's refused
# 16 genuine CI reports in the draw while finding no real denial.
_DENIED_BEFORE = re.compile(
    r"\b(?:no|not|without|neither|nor|never|cannot)(?:\s+[a-z-]+){0,3}\s+$", re.IGNORECASE
)
_DENIED_AFTER = re.compile(
    r"^\s*(?:\([^()]{1,20}\)\s*)?(?:(?:was|were|is|are|has|have|had|been|be)\s+)*"
    r"(?:not|never)\s+(?:been\s+)?"
    r"(?:performed|reported|calculated|conducted|done|provided|given|stated|available"
    r"|presented|described|carried\s+out|undertaken)\b",
    re.IGNORECASE,
)

# How far either side of a mention ``is_denied`` reads. Both patterns are
# anchored to the mention, so this only bounds the work.
_DENIAL_LOOKAROUND = 80


def _iter_keyword_positions(text: str, keyword: str) -> Iterator[int]:
    """Yield start offsets of whole-word occurrences of *keyword* in *text*.

    A match must start and end at a word boundary (an optional plural "s" is
    tolerated), so the keyword "rct" matches "RCTs" but not "infarct".
    """
    pattern = rf"(?<!\w){re.escape(keyword)}s?(?!\w)"
    for match in re.finditer(pattern, text):
        yield match.start()


def extract_text_context(
    text: str, keyword: str, context_chars: int = 50, keyword_pos: int | None = None
) -> str:
    """Return a snippet of *text* around an occurrence of *keyword*.

    Uses the first occurrence unless *keyword_pos* gives the offset of a
    specific one. Adds ellipses where the snippet is truncated. Returns ``""``
    if the keyword is not present.
    """
    if keyword_pos is None:
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

    A count grouped with commas or spaces is read whole ("12,345", "20 882"),
    and a digit run inside a larger number — the digits after a decimal point,
    or one group of a count — is never captured on its own (issue #294).

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
            size = int(re.sub(r"\D", "", match.group(1)))
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


def is_denied(text: str, start: int, end: int) -> bool:
    """Return whether the mention at ``text[start:end]`` is denied.

    A denial governs the mention: a negation at most three words before it with
    only words in between ("no power calculation", "did not perform a formal
    power calculation"), or a negated verb of reporting right after it
    ("confidence intervals were not reported"). A negation elsewhere in the
    sentence does not count, so a CI reported beside "no significant
    difference" is still a CI (issue #297).

    Args:
        text: Text containing the mention.
        start: Offset of the mention's first character.
        end: Offset just past the mention's last character.
    """
    window_start = max(0, start - _DENIAL_LOOKAROUND)
    before = text[window_start:start]
    if window_start > 0 and re.match(r"\w", text[window_start - 1]):
        # A window cut inside a word would make its tail a word of its own:
        # "casino" cut to "no". A cut on a word boundary keeps its first word.
        before = re.sub(r"^\w+", "", before)
    after = text[end : end + _DENIAL_LOOKAROUND]
    return bool(_DENIED_BEFORE.search(before) or _DENIED_AFTER.match(after))


def _is_a_study_power(match: re.Match[str]) -> bool:
    """Whether a quantified-power match states a power a calculation would set."""
    pct = match.group("pct") or match.group("pct2")
    value = float(pct) / 100 if pct is not None else float(match.group("frac"))
    return 0.5 <= value < 1.0


def _find_power_mention(text: str) -> re.Match[str] | None:
    """Return the first power-calculation mention that is not denied, if any."""
    candidates: list[re.Match[str]] = []
    for keyword in POWER_CALCULATION_KEYWORDS:
        pattern = r"\b" + r"\s+".join(re.escape(word) for word in keyword.split())
        candidates.extend(re.finditer(pattern, text, re.IGNORECASE))
    for pattern in POWER_CALCULATION_PATTERNS:
        candidates.extend(re.finditer(pattern, text, re.IGNORECASE))
    candidates.extend(
        match
        for match in re.finditer(QUANTIFIED_POWER_PATTERN, text, re.IGNORECASE)
        if _is_a_study_power(match)
    )
    candidates.sort(key=lambda match: match.start())
    return next((m for m in candidates if not is_denied(text, m.start(), m.end())), None)


def _find_ci_mention(text: str) -> re.Match[str] | None:
    """Return the first confidence-interval report that is not denied, if any."""
    candidates = sorted(
        (m for pattern in CI_PATTERNS for m in re.finditer(pattern, text, re.IGNORECASE)),
        key=lambda match: match.start(),
    )
    return next((m for m in candidates if not is_denied(text, m.start(), m.end())), None)


def _mention_context(text: str, match: re.Match[str] | None) -> str:
    """Return a snippet around *match*, or ``""`` when there is none."""
    if match is None:
        return ""
    return extract_text_context(text, match.group(0), keyword_pos=match.start())


def has_power_calculation(text: str) -> bool:
    """Return whether *text* reports the study's own power calculation.

    Counts a calculation phrase (``POWER_CALCULATION_KEYWORDS``,
    ``POWER_CALCULATION_PATTERNS``) or a power stated as a quantity of 50% or
    more (``QUANTIFIED_POWER_PATTERN``), and
    refuses a mention a denial governs (:func:`is_denied`). A discussion of
    power — "low statistical power" — is not a calculation (issue #297).
    """
    return _find_power_mention(text) is not None


def find_power_calc_context(text: str) -> str:
    """Return a snippet around the mention that earns the power bonus, or ``""``.

    It is the same mention :func:`has_power_calculation` credits, so a bonus
    never records empty evidence.
    """
    return _mention_context(text, _find_power_mention(text))


def has_ci_reporting(text: str) -> bool:
    """Return whether *text* reports confidence intervals.

    Tests ``CI_PATTERNS`` and refuses a mention a denial governs
    (:func:`is_denied`). A bare "CI" counts only beside a percentage or a
    number, since the token is also a cardiac index, a cochlear implant and
    cognitive impairment (issue #297).
    """
    return _find_ci_mention(text) is not None


def find_ci_context(text: str) -> str:
    """Return a snippet around the mention that earns the CI bonus, or ``""``."""
    return _mention_context(text, _find_ci_mention(text))


def has_exclusion_pattern(
    text: str,
    keyword: str,
    exclusion_patterns: list[str],
    context_window: int = EXCLUSION_CONTEXT_WINDOW,
    keyword_pos: int | None = None,
) -> bool:
    """Return whether an exclusion pattern appears just before *keyword*.

    Prevents false positives such as "non-randomized trial" matching as RCT
    when searching for "randomized trial". Checks the first occurrence unless
    *keyword_pos* gives the offset of a specific one.

    Args:
        text: Full (lowercase) text being searched.
        keyword: The matched (lowercase) keyword.
        exclusion_patterns: Patterns that should invalidate the match.
        context_window: Characters before the keyword to inspect.
        keyword_pos: Offset of the occurrence to check (default: first).
    """
    if keyword_pos is None:
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

    Searches ``full_text`` when available (else abstract + methods) and tries
    each type in priority order (systematic review > quasi-experimental > RCT >
    …), rejecting matches whose exclusion patterns fire. **The first type with
    any surviving match wins**: a lower-priority type is never consulted once a
    higher one has matched, so a clean description of the paper's own design
    loses to any unexcluded mention of a higher-priority type, a contrastive
    one included ("in contrast to quasi-experimental designs"). That shape
    measured 0 of 914 RCT abstracts, and reordering the priority cost more than
    it recovered — see ``docs/DECISIONS.md`` (issue #298). Keywords match whole
    words only (with an optional plural "s"), so "RCT" matches "RCTs" but not
    "infarct"; every occurrence of a keyword is tried, so one excluded mention
    does not suppress a later clean one. Returns a :class:`DimensionScore` for
    the study-design dimension with an audit trail; defaults to "unknown" at a
    neutral score when nothing matches.
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
            for keyword_pos in _iter_keyword_positions(search_text, keyword_lower):
                if exclusions and has_exclusion_pattern(
                    search_text, keyword_lower, exclusions, keyword_pos=keyword_pos
                ):
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
                    evidence=extract_text_context(
                        search_text, keyword_lower, keyword_pos=keyword_pos
                    ),
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
            evidence=find_ci_context(search_text),
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
