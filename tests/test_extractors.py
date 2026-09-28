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

"""Tests for bmlib.quality rule-based extractors and scoring models."""

from __future__ import annotations

import math

from bmlib.quality.extractors import (
    _DENIAL_LOOKAROUND,
    calculate_sample_size_score,
    extract_sample_size_dimension,
    extract_study_type,
    extract_text_context,
    find_ci_context,
    find_power_calc_context,
    find_sample_size,
    get_extracted_sample_size,
    get_extracted_study_type,
    has_ci_reporting,
    has_exclusion_pattern,
    has_power_calculation,
    is_denied,
    prepare_extractor_search_text,
)
from bmlib.quality.scoring_models import (
    DIMENSION_SAMPLE_SIZE,
    DIMENSION_STUDY_DESIGN,
    AssessmentDetail,
    DimensionScore,
)


class TestScoringModels:
    def test_assessment_detail_to_dict(self):
        detail = AssessmentDetail(
            dimension="study_design",
            component="study_type",
            extracted_value="rct",
            score_contribution=8.0,
            evidence_text="randomized",
            reasoning="matched",
        )
        d = detail.to_dict()
        assert d["dimension"] == "study_design"
        assert d["score_contribution"] == 8.0

    def test_dimension_score_add_detail(self):
        dim = DimensionScore(dimension_name="sample_size", score=5.0)
        dim.add_detail(component="extracted_n", value="450", contribution=5.0)
        assert len(dim.details) == 1
        assert dim.details[0].extracted_value == "450"
        assert dim.details[0].dimension == "sample_size"

    def test_dimension_score_to_dict(self):
        dim = DimensionScore(dimension_name="sample_size", score=5.0)
        dim.add_detail(component="c", value="v", contribution=1.0)
        d = dim.to_dict()
        assert d["dimension_name"] == "sample_size"
        assert len(d["details"]) == 1

    def test_assessment_detail_round_trip(self):
        detail = AssessmentDetail(
            dimension="study_design",
            component="study_type",
            extracted_value="rct",
            score_contribution=8.0,
            evidence_text="randomized",
            reasoning="matched",
        )
        assert AssessmentDetail.from_dict(detail.to_dict()) == detail

    def test_dimension_score_round_trip(self):
        dim = DimensionScore(dimension_name="sample_size", score=5.0)
        dim.add_detail(component="extracted_n", value="450", contribution=5.0)
        restored = DimensionScore.from_dict(dim.to_dict())
        assert restored == dim
        assert restored.details[0].extracted_value == "450"


class TestSampleSize:
    def test_finds_n_equals(self):
        assert find_sample_size("The study enrolled n = 450 patients") == 450

    def test_returns_largest(self):
        assert find_sample_size("n = 20 in arm A, n = 30 in arm B, 50 participants") == 50

    def test_none_when_absent(self):
        assert find_sample_size("no numbers about people here") is None

    def test_filters_out_of_range(self):
        # Below min_n (default 5) is ignored.
        assert find_sample_size("n = 2 patients") is None

    def test_score_is_log_scaled(self):
        assert calculate_sample_size_score(100, log_multiplier=2.0) == math.log10(100) * 2.0

    def test_score_capped_at_ten(self):
        assert calculate_sample_size_score(10_000_000) == 10.0

    def test_score_zero_for_nonpositive(self):
        assert calculate_sample_size_score(0) == 0.0


class TestADigitGroupedCountIsReadWhole:
    """Issue #294: every pattern captured ``(\\d+)``, which cannot span a
    thousands separator, so the size read was whichever digit run the pattern
    anchored to — ``12,345 patients`` read 345 and ``n = 12,345`` read 12.

    Measured over 5,976 Europe PMC abstracts (seven PubMed publication types x
    three years): 2,260 yield a size on ``main`` and 225 move. The separators
    are those the draw deposits: a comma (192) and the space family — an ASCII
    space (8, every one a genuine grouping), a thin space (3), a no-break space
    (2), a punctuation space and a hair space (1 each).
    """

    def test_a_trailing_anchored_pattern_reads_the_whole_number(self):
        # main: 345, the last digit run.
        assert find_sample_size("A total of 12,345 patients were enrolled.") == 12345
        assert find_sample_size("The study enrolled 1,234 patients.") == 1234

    def test_a_leading_anchored_pattern_reads_the_whole_number(self):
        # main: 12, the first digit run.
        assert find_sample_size("n = 12,345") == 12345

    def test_a_fragment_below_the_floor_no_longer_reads_as_absent(self):
        # main: None — the fragment "000" is 0, under min_n.
        assert find_sample_size("The trial recruited 10,000 subjects") == 10000

    def test_a_space_grouped_count_is_read_whole(self):
        # main: 882.
        assert find_sample_size("The final data set included 20 882 patients") == 20882

    def test_every_space_family_separator_groups(self):
        # U+2008 and U+200A are the draw's own: "33\u2008958 patients" (PMID
        # 25131979) and "14\u200a034 patients" (PMID 37409599).
        for sep in ("\u00a0", "\u2009", "\u202f", "\u2008", "\u200a"):
            text = f"28 RCTs containing 17{sep}266 participants"
            assert find_sample_size(text) == 17266, repr(sep)

    def test_a_grouped_count_above_the_ceiling_is_out_of_bounds_not_a_fragment(self):
        # main: 756, the last three digits of a number over max_n.
        assert find_sample_size("Among 2\u2009902\u2009756 patients who were admitted") is None

    def test_a_fragment_of_a_decimal_is_not_a_count(self):
        # main: 32, 9 and 20 — the digits after a decimal point.
        assert find_sample_size("consultations per hour of 0.32 patients") is None
        assert find_sample_size("is required by 2.9 patients with long-term use") is None
        assert find_sample_size("for a total of 35.020 patients.") is None

    def test_a_leading_anchored_pattern_does_not_read_a_decimal(self):
        # From the served full text. main: 70, the integer part of 70.6%; the
        # lookbehind cannot refuse it, since "70" starts the number.
        assert find_sample_size("chronic heart failure (n = 70.6%, n = 12), and") == 12

    def test_n_equals_is_a_whole_word(self):
        # main: 45 — "median = 45" ends in "n = 45". The fixture's other count
        # is smaller, so the largest-match rule cannot hide the defect.
        assert find_sample_size("The median = 45 days; 12 patients were enrolled.") == 12
        assert find_sample_size("two assessment points (mean = 118.45 days apart)") is None

    def test_n_equals_still_reads_its_ordinary_spellings(self):
        assert find_sample_size("N = 450") == 450
        assert find_sample_size("(n=450)") == 450
        assert find_sample_size("GWAS (N\u00a0=\u00a0456 380).") == 456380

    def test_a_grouped_count_reaches_the_audit_trail(self):
        result = extract_sample_size_dimension({"abstract": "A total of 12,345 patients."})
        assert get_extracted_sample_size(result) == 12345


class TestSignals:
    def test_power_calculation_detected(self):
        assert has_power_calculation("A power calculation was performed") is True

    def test_power_calculation_absent(self):
        assert has_power_calculation("no such thing here") is False

    def test_ci_reporting_percent_form(self):
        assert has_ci_reporting("the OR was 1.5 (95% CI 1.1-2.0)") is True

    def test_ci_reporting_phrase(self):
        assert has_ci_reporting("we report the confidence interval") is True

    def test_ci_reporting_absent(self):
        assert has_ci_reporting("plain text without intervals") is False

    def test_ci_reporting_decimal_range_detected(self):
        assert has_ci_reporting("the hazard ratio was 0.81 (0.71-0.93)") is True
        assert has_ci_reporting("effect size [1.10, 2.34]") is True

    def test_citation_brackets_not_ci(self):
        # Integer citation markers must not count as CI reporting.
        assert has_ci_reporting("as shown previously [12, 15] the effect persists") is False

    def test_year_range_not_ci(self):
        assert has_ci_reporting("records from the registry (2010-2015) were included") is False


class TestAPowerBonusNeedsTheStudysOwnCalculation:
    """Issue #297: ``has_power_calculation`` was a bare substring test, so a
    text denying a power calculation was awarded the +2.0 bonus.

    The measurement moved the remedy. Of 22 power-positive abstracts in a
    5,976-abstract draw only 9 report the paper's own calculation; 13 *discuss*
    power ("low statistical power", "future studies with sufficient
    statistical power", "the original trials' power calculations"), and a
    negation word catches one of them. So bare ``statistical power`` and
    ``power to detect`` no longer count, a quantified power does, and a mention
    a denial governs (``is_denied``) is refused: 16 genuine credited and 4
    false, against 9 and 13 on ``main``. The original trials' calculations are
    a keyword match and stay among the 4.
    """

    def test_the_issues_denial_is_refused(self):
        text = "No power calculation was performed and confidence intervals were not reported."
        assert has_power_calculation(text) is False

    def test_a_denial_after_the_mention_is_refused(self):
        assert has_power_calculation("A power calculation was not performed.") is False
        assert has_power_calculation("Power analysis was never conducted.") is False

    def test_a_denial_a_few_words_before_is_refused(self):
        assert has_power_calculation("We did not perform a formal power calculation.") is False
        assert has_power_calculation("without an a priori power analysis") is False

    def test_a_discussion_of_power_is_not_a_calculation(self):
        for text in (
            "the study does not have the statistical power to rule out a difference",
            "small sample sizes and low statistical power",
            "Prospective studies with sufficient statistical power are warranted.",
            "the subsequent lack of statistical power",
            "insufficient power to detect a difference in mortality",
        ):
            assert has_power_calculation(text) is False, text

    def test_a_quantified_power_is_a_calculation(self):
        for text in (
            "82 patients were needed to achieve 80% statistical power",
            "The study had 80% power to detect a 10% difference.",
            "to achieve a statistical power of 80 %",
            "with one-sided alpha of 5%, power of 80%, and expected values",
            "type I error (alpha = 0.05), and power of 0.80",
            "this study projects a 90% power for each endpoint",
            "The power was 90% at a two-sided alpha of 0.05.",
            "combination therapy over the null hypothesis with power of at least 80%",
            "to achieve a statistical power of 80\u00a0\u200b%. Patients with",
            "giving 90\u200b% power at a two-sided alpha",
        ):
            assert has_power_calculation(text) is True, text

    def test_every_spelling_of_a_calculation_counts(self):
        # From the served full text: a no-break space inside the phrase, the
        # plural, the program, and the phrase written the other way round.
        for text in (
            "In a post\u00a0hoc\u00a0power\u00a0analysis the observed power was",
            "Sample sizes were estimated by power analyses using pilot data.",
            "The sample size was calculated by using G* Power 3.1.9.7.",
            "The required number was estimated using G*Power (V 3.1).",
            "The sample size was calculated using the single population proportion formula.",
            "sufficient statistical power (0.80) to detect even small effects",
        ):
            assert has_power_calculation(text) is True, text

    def test_a_sample_size_that_was_not_calculated_is_not_a_calculation(self):
        text = "The sample size was determined by the number of eligible patients."
        assert has_power_calculation(text) is False

    def test_another_kind_of_power_is_not_a_studys(self):
        assert has_power_calculation("a positive predictive power of 88%") is False
        assert has_power_calculation("sufficient discriminatory power (0.75)") is False

    def test_a_physical_power_is_not_a_studys(self):
        # From the served full text: a laser, where the fraction has a unit.
        assert has_power_calculation("at a post-objective power of 0.6 mW") is False
        assert has_power_calculation("delivered at a power of 0.8 W for 10 s") is False

    def test_a_power_of_one_hundred_percent_is_not_a_calculation(self):
        # A calculation never sets power at 100%; a detection rate does.
        assert has_power_calculation("the assay had a detection power of 100%") is False

    def test_a_quantity_that_cannot_be_a_studys_power_is_not_one(self):
        # A cycling abstract's "mean power", from the draw: a power calculation
        # is set at 50% or above, conventionally 80% or 90%.
        assert has_power_calculation("a trivial increase in mean power of 1.0% over baseline") is (
            False
        )

    def test_a_denied_mention_does_not_cancel_a_later_credited_one(self):
        text = (
            "No power calculation was performed for the pilot phase. "
            "For the main trial, a power analysis indicated 400 participants."
        )
        assert has_power_calculation(text) is True

    def test_the_evidence_is_the_credited_mention(self):
        # main: "" — the context scan read only the first three keywords, so a
        # bonus earned by "statistical power" recorded no evidence at all.
        context = find_power_calc_context("It had 80% statistical power to detect a change.")
        assert "80% statistical power" in context
        denied_then_credited = (
            "No power calculation was performed for the pilot phase. "
            "For the main trial, a power analysis indicated 400 participants."
        )
        assert "power analysis indicated" in find_power_calc_context(denied_then_credited)

    def test_the_evidence_is_the_earliest_credited_mention(self):
        # The candidates come from several patterns; the evidence is the first
        # in the text, not the first pattern's.
        # The filler keeps each mention out of the other's 50-character snippet.
        filler = " Recruitment ran across twelve centres over three consecutive years."
        text = "A power analysis showed 80 were needed." + filler + " The power calculation held."
        context = find_power_calc_context(text)
        assert "power analysis showed" in context
        assert "calculation held" not in context
        text = "OR 1.5 (95% CI 1.1-2.0)." + filler + " All confidence intervals are two-sided."
        context = find_ci_context(text)
        assert "95% CI 1.1-2.0" in context
        assert "two-sided" not in context

    def test_no_credited_mention_records_no_evidence(self):
        assert find_power_calc_context("No power calculation was performed.") == ""


class TestACIBonusNeedsAConfidenceInterval:
    """Issue #297's other half, and the population beside it.

    No CI-positive abstract of 1,308 loses the bonus to a denial (the one denied
    mention sits beside a reported CI), so there the guard pins a direction; in
    full text it refuses 9 mentions. The population the draw did find is the
    bare ``\\bCI\\b`` token crediting 16 abstracts that report no interval,
    11 of them a cardiac index, a cochlear implant, cognitive impairment or a
    chronicity index. A ``CI`` now counts after a percentage, before an
    interval or a percentage, or beside a bound.
    """

    def test_the_issues_denials_are_refused(self):
        assert has_ci_reporting("We did not report confidence intervals.") is False
        assert has_ci_reporting("Confidence intervals were not reported.") is False
        assert has_ci_reporting("No confidence intervals were given.") is False

    def test_a_ci_beside_a_negated_finding_is_still_a_ci(self):
        # The Rust port's +-40-character negation window drops this one; 16 of
        # the draw's abstracts are shaped like it, and every one reports a CI.
        text = "HR 0.96, 95% CI 0.46-1.49, P = .92), with no difference for the composite"
        assert has_ci_reporting(text) is True
        text = "The pooled odds ratio was 7.3 (95% CI, 4.7-11.1) without significant heterogeneity"
        assert has_ci_reporting(text) is True

    def test_another_abbreviation_is_not_a_ci(self):
        for text in (
            "Cardiac index (CI) increased from 1.9 (0.7) to 2.8 (1.3) L/min",
            "whether unilateral cochlear implant (CI) users benefit",
            "manifestations of dementia or cognitive impairment (CI).",
            "contrast-induced acute kidney injury (CI-AKI) is well known",
            "anesthesia (group CI, n = 50)",
            "the cis-9, trans-11 isomer",
            "[3H] thymidine (0.5 \u03bcCi/well, 5 Ci/mmol)",
            "chemical ionization (CI) of 100 eV, and methane as the reagent",
            "The combination index (CI) values showed synergy",
        ):
            assert has_ci_reporting(text) is False, text

    def test_every_spelling_of_a_reported_ci_counts(self):
        for text in (
            "the OR was 1.5 (95% CI 1.1-2.0)",
            "(95%-CI 9.3-NA)",
            "(odds ratio: 0.460, 95% <i>CI</i>: 0.278, 0.761)",
            "SMD = -1.53, 95%<i>CI</i> (-1.96, -1.10)",
            "a global estimate of 0.81 (CI<sub>95%</sub>: 0.51 to 1.2)",
            "The hazard ratios (HRs) and 95% CIs were calculated",
            "(p < 0.001, CI 95%)",
            "(HR 2.2; CI 1.0-4.7; P = 0.04)",
            "the measure RR/OR and CI of 95% to estimate",
            "Results are reported with 95 % CI.",
            "the odds ratio was 1.4 (95% ci 1.1-1.8)",
            "HR 7.49 (95%CI0.99-56.34)",
            "we report the confidence interval",
            "Compulsory school: 11.7% (CI: \u00b10.4%)",
            "Predictor variable Estimate Lower CI Upper CI P-value",
            "the lower CI of each estimate",
        ):
            assert has_ci_reporting(text) is True, text

    def test_a_denied_mention_does_not_cancel_a_later_credited_one(self):
        text = "Confidence intervals were not reported for the pilot. OR 1.5 (95% CI 1.1-2.0)."
        assert has_ci_reporting(text) is True

    def test_the_evidence_is_the_credited_mention(self):
        assert "95% CI" in find_ci_context("OR 1.5 (95% CI 1.1-2.0)")
        assert find_ci_context("Cardiac index (CI) was 2.1") == ""
        assert find_ci_context("Confidence intervals were not reported.") == ""


class TestADenialIsRefusedOnlyWhereItGovernsTheMention:
    """``is_denied`` is the one rule both bonuses use. It is narrow on purpose:
    a CI is reported next to exactly the vocabulary a wide negation window
    reads ("no significant difference", "without heterogeneity")."""

    def _span(self, text, mention):
        start = text.index(mention)
        return start, start + len(mention)

    def test_a_negation_governing_the_mention_denies_it(self):
        text = "No formal power calculation was done"
        assert is_denied(text, *self._span(text, "power calculation")) is True

    def test_a_negation_across_punctuation_does_not(self):
        text = "no difference (HR 0.96, 95% CI 0.46-1.49)"
        assert is_denied(text, *self._span(text, "95% CI")) is False

    def test_a_negation_four_words_away_does_not(self):
        # Exactly four words between: one more than the rule allows.
        text = "No adverse events occurred and confidence intervals were reported."
        assert is_denied(text, *self._span(text, "confidence intervals")) is False
        text = "No adverse events and confidence intervals were reported."
        assert is_denied(text, *self._span(text, "confidence intervals")) is True

    def test_a_negation_word_inside_another_word_does_not(self):
        text = "Notably a power calculation was performed"
        assert is_denied(text, *self._span(text, "power calculation")) is False

    def test_a_denied_verb_after_the_mention_denies_it(self):
        text = "Confidence intervals (CIs) were not reported"
        assert is_denied(text, *self._span(text, "Confidence intervals")) is True

    def test_a_window_cut_inside_a_word_does_not_make_a_negation(self):
        # Three long words put "casino" exactly one lookaround before the
        # mention, so the window's first two characters are its "no".
        gap = " " + "a" * 24 + " " + "b" * 24 + " " + "c" * 26 + " "
        text = "The casi" + "no" + gap + "power calculation was performed"
        start = text.index("power calculation")
        assert text[start - _DENIAL_LOOKAROUND : start].startswith("no ")
        assert is_denied(text, start, start + len("power calculation")) is False
        # And the same words after a real "no" are a denial.
        denied = "The " + "no" + gap + "power calculation was performed"
        start = denied.index("power calculation")
        assert is_denied(denied, start, start + len("power calculation")) is True

    def test_a_finding_that_is_not_significant_is_not_a_denial(self):
        text = "the 95% CI was not significant"
        assert is_denied(text, *self._span(text, "95% CI")) is False


class TestExclusionAndContext:
    def test_exclusion_pattern_blocks_false_positive(self):
        text = "this was a non-randomized trial of patients"
        assert has_exclusion_pattern(text, "randomized trial", ["non-randomized"]) is True

    def test_the_window_includes_the_keyword_itself(self):
        # "randomized controlled trial" is found *inside* "non-randomized
        # controlled trial" (the hyphen is a word boundary), so the exclusion
        # that has to fire is the one that contains the keyword. The Rust port
        # ended its window before the keyword and read 28 abstracts as RCTs, 27
        # of them among the 896 labelled Controlled Clinical Trial (issue 366).
        text = "we performed a non-randomized controlled trial"
        keyword_pos = text.index("randomized controlled trial")
        assert (
            has_exclusion_pattern(
                text, "randomized controlled trial", ["non-randomized"], keyword_pos=keyword_pos
            )
            is True
        )

    def test_no_exclusion_pattern(self):
        text = "this was a randomized trial of patients"
        assert has_exclusion_pattern(text, "randomized trial", ["non-randomized"]) is False

    def test_extract_text_context_returns_snippet(self):
        ctx = extract_text_context("x" * 100 + "keyword" + "y" * 100, "keyword", context_chars=10)
        assert "keyword" in ctx
        assert ctx.startswith("...")
        assert ctx.endswith("...")


class TestPrepareSearchText:
    def test_prefers_full_text_when_longer(self):
        doc = {"full_text": "a much longer full text body", "abstract": "short"}
        assert prepare_extractor_search_text(doc) == "a much longer full text body"

    def test_falls_back_to_abstract_and_methods(self):
        doc = {"abstract": "abs", "methods_text": "meth"}
        assert prepare_extractor_search_text(doc) == "abs meth"


class TestExtractStudyType:
    def test_detects_rct(self):
        doc = {"abstract": "A randomized controlled trial of drug X"}
        result = extract_study_type(doc)
        assert result.dimension_name == DIMENSION_STUDY_DESIGN
        assert get_extracted_study_type(result) == "rct"
        assert result.score == 8.0

    def test_non_randomized_not_classified_as_rct(self):
        doc = {"abstract": "A non-randomized trial evaluated the intervention"}
        result = extract_study_type(doc)
        assert get_extracted_study_type(result) != "rct"

    def test_a_non_randomised_controlled_trial_is_not_an_rct(self):
        doc = {"abstract": "We performed a non-randomized controlled trial."}
        assert get_extracted_study_type(extract_study_type(doc)) == "unknown"

    def test_systematic_review_wins(self):
        doc = {"abstract": "A systematic review and randomized trial discussion"}
        result = extract_study_type(doc)
        assert get_extracted_study_type(result) == "systematic_review"
        assert result.score == 10.0

    def test_unknown_default(self):
        doc = {"abstract": "Some general discussion of a topic"}
        result = extract_study_type(doc)
        assert get_extracted_study_type(result) == "unknown"
        assert result.score == 5.0

    def test_infarction_not_classified_as_rct(self):
        # "rct" must match whole words only — not the substring in "infarction".
        doc = {"abstract": "Outcomes after myocardial infarction in a community registry."}
        result = extract_study_type(doc)
        assert get_extracted_study_type(result) == "unknown"

    def test_rct_acronym_and_plural_match(self):
        assert get_extracted_study_type(extract_study_type({"abstract": "An RCT of drug X"})) == (
            "rct"
        )
        doc = {"abstract": "Twelve RCTs were pooled"}  # plural acronym
        assert get_extracted_study_type(extract_study_type(doc)) == "rct"

    def test_later_clean_occurrence_survives_excluded_first_one(self):
        # The first "randomized trial" mention sits next to an exclusion
        # phrase; the later clean mention must still classify as RCT.
        doc = {
            "abstract": (
                "An earlier study without randomization mimicked a randomized trial. "
                "Our subsequent well-conducted randomized trial enrolled 200 patients."
            )
        }
        result = extract_study_type(doc)
        assert get_extracted_study_type(result) == "rct"


class TestTheFirstTypeInPriorityOrderWins:
    """Issue #298, closed as measured-empty — see ``docs/DECISIONS.md``.

    A higher-priority type with any clean match wins, so a contrastive mention
    of ``quasi_experimental`` outranks the paper's own RCT description. That
    shape occurs in 0 of 914 RCT abstracts in a 5,976-abstract draw. Swapping
    ``rct`` ahead of ``quasi_experimental`` moves 8 results and improves none
    (five non-randomised trials and two reviews become ``rct``), and the Rust
    port's clause-level contrastive veto moves 23 on its own, none an
    improvement. This test pins the decision: reverse it knowingly.
    """

    def test_a_contrastive_mention_of_a_higher_priority_type_wins(self):
        doc = {
            "full_text": (
                "This was a randomized controlled trial. In contrast to "
                "quasi-experimental designs, treatment was randomised."
            )
        }
        assert get_extracted_study_type(extract_study_type(doc)) == "quasi_experimental"

    def test_a_non_randomised_trial_is_still_quasi_experimental(self):
        doc = {"abstract": "A prospective non-randomized trial of 60 patients."}
        assert get_extracted_study_type(extract_study_type(doc)) == "quasi_experimental"


class TestExtractSampleSizeDimension:
    def test_scores_with_power_and_ci_bonus(self):
        doc = {
            "abstract": (
                "We enrolled n = 1000 patients. A power calculation was performed. "
                "Results are reported with 95% CI."
            )
        }
        result = extract_sample_size_dimension(doc)
        assert result.dimension_name == DIMENSION_SAMPLE_SIZE
        assert get_extracted_sample_size(result) == 1000
        # base = log10(1000)*2 = 6, +2 power +0.5 ci = 8.5
        assert result.score == 8.5

    def test_no_sample_size_scores_zero(self):
        result = extract_sample_size_dimension({"abstract": "no counts here"})
        assert result.score == 0.0
        assert get_extracted_sample_size(result) is None

    def test_score_capped_at_ten(self):
        # n = 1,000,000 (the max valid size): log10(1e6)*2 = 12, capped to 10.
        # Written in the comma form this comment always used (#294: main read
        # it as absent and scored 0.0).
        doc = {"abstract": ("n = 1,000,000 participants, power calculation done, 95% CI reported")}
        result = extract_sample_size_dimension(doc)
        assert result.score == 10.0

    def test_the_issues_denials_earn_no_bonus_and_record_none(self):
        # #297: main scored 7.10 and recorded "power_calculation yes" and
        # "ci_reporting yes" for a text denying both.
        doc = {
            "abstract": (
                "We enrolled 200 patients. No power calculation was performed and "
                "confidence intervals were not reported."
            )
        }
        result = extract_sample_size_dimension(doc)
        assert result.score == math.log10(200) * 2.0
        assert [d.component for d in result.details] == ["extracted_n"]

    def test_a_credited_bonus_records_its_evidence(self):
        doc = {"abstract": "We enrolled 200 patients, giving 90% power. OR 1.5 (95% CI 1.1-2.0)."}
        result = extract_sample_size_dimension(doc)
        power = next(d for d in result.details if d.component == "power_calculation")
        assert "90% power" in (power.evidence_text or "")
        ci = next(d for d in result.details if d.component == "ci_reporting")
        assert "95% CI" in (ci.evidence_text or "")


class TestTheReviewsFindings:
    """Defects the correctness and claims reviews of the #294/#297 fix found.

    Each fixture is the reviewer's own input or a shape from the draw.
    """

    def test_a_plural_keyword_is_still_denied(self):
        # The keyword matched inside "calculations", leaving an "s" that the
        # after-denial could not read past.
        for text in (
            "Power calculations were not performed.",
            "Sample size calculations were not performed.",
            "Formal power calculations were not done for this pilot.",
            "Power calculation: not performed",
        ):
            assert has_power_calculation(text) is False, text
        assert has_power_calculation("Power calculations indicated 120 per arm.") is True

    def test_a_denial_reaches_a_ci_written_with_its_percentage(self):
        for text in (
            "No 95% CIs were reported.",
            "We did not report 95% confidence intervals.",
            "We did not calculate 95% CIs for these estimates.",
        ):
            assert has_ci_reporting(text) is False, text

    def test_a_full_width_ci_counts(self):
        # PMC12337216; main credited it and the first cut of this fix did not.
        assert has_ci_reporting("中位随访期为20.7（95％CI：18.7～27.9）个月") is True
        # Each full-width form on its own, since that sentence matches two
        # patterns and so pins neither.
        assert has_ci_reporting("风险比（95％CI）") is True
        assert has_ci_reporting("HR 1.2（CI：1.05～1.42）") is True

    def test_cis_after_a_percentage_is_not_a_ci(self):
        assert has_ci_reporting("a mixture containing 50% cis-9, trans-11 CLA") is False
        assert has_ci_reporting("the 10% cis isomer") is False
        assert has_ci_reporting("the lower cis isomer") is False

    def test_a_ci_needs_an_interval_after_it(self):
        for text in (
            "the HDAC inhibitor CI-994 was given",
            "the MEK inhibitor CI-1040",
            "the pigment CI 77891",
            "a CI of 2.4 L/min/m2",
            "(CI = 2.4 L/min/m2)",
        ):
            assert has_ci_reporting(text) is False, text
        for text in (
            "(CI: 0.278, 0.761)",
            "(CI 1.43 to 10.17)",
            "(OR 3.22, CI 1.59‐6.51, p=0.001)",
            "(RR 2.50; CI 95%, 0.55 to 11.41)",
            # The served full text's four real intervals the first cut of the
            # interval test lost: a semicolon, a percentage on each bound, and
            # a hyphen before the level.
            "(AUC=0.686 [CI 0.566; 0.807], p=0.002)",
            "(r = 0.530, p = 0.002; CI = [0.31; 0.87])",
            "(P = .02; CI = \u221246.99%, \u22126.40%)",
            "Frailty (HR 3.576(CI-95% 1.033-12.378;p=0.044))",
        ):
            assert has_ci_reporting(text) is True, text

    def test_a_comma_that_is_not_a_thousands_group_ends_the_count(self):
        # main: 120. The first cut refused it, reading ",45" as a fragment.
        assert find_sample_size("n=120,45% female") == 120

    def test_a_comma_run_that_is_not_a_grouping_is_no_count(self):
        # Neither "2345" nor "1" is the count "1,2345" could mean.
        assert find_sample_size("1,2345 patients") is None
        assert find_sample_size("12,3456 patients") is None
        # A leading-anchored pattern has no suffix to refuse the fragment, so
        # the lookahead alone decides: main read 1 and 12.
        assert find_sample_size("n = 1,2345") is None
        assert find_sample_size("n = 12,3456") is None

    def test_a_number_too_long_to_be_a_count_is_none_not_an_error(self):
        # int() refuses more than 4,300 digits; main raised on a long digit run.
        assert find_sample_size("1" + ",000" * 1500 + " patients") is None
        assert find_sample_size("1" * 5000 + " patients") is None

    def test_another_kind_of_power_is_not_a_studys_whatever_the_spacing(self):
        for text in (
            "the predictive  power of 88%",
            "its predictive-power of 88%",
            "a diagnostic power of 85%",
            "an explanatory power of 65%",
            "sufficient discriminative power (0.75)",
        ):
            assert has_power_calculation(text) is False, text

    def test_a_percentage_below_one_is_not_a_fraction(self):
        assert has_power_calculation("a power of 0.85% over baseline") is False
        # With no leading zero the percentage branch cannot match, so the
        # fraction's own lookahead is what refuses ".85" before a "%".
        assert has_power_calculation("a power of .85% over baseline") is False

    def test_a_percentage_is_read_from_its_first_digit(self):
        # "080%" inside "1080%" would be an 80% power.
        assert has_power_calculation("a 1080% power increase") is False

    def test_the_program_counts_with_its_version_attached(self):
        assert has_power_calculation("computed in G*Power3.1 for an effect of 0.5") is True

    def test_long_runs_of_whitespace_or_digits_stay_fast(self):
        # Adjacent optional whitespace runs backtracked cubically: 2,000 spaces
        # after "power" took 51 s and after "CI" 16 s.
        import time

        # 20,000 and 80,000 because a single non-possessive run is only
        # quadratic, which 3,000 characters would not show: the CI markup run
        # made greedy takes 0.2 s at 20,000 and 3.4 s at 80,000.
        for text in (
            "power" + " " * 20000 + "x",
            "CI" + " " * 80000 + "x",
            "CI of" + " " * 20000 + "x",
            "123 " * 5000 + "x",
            # A digit run with no lookbehind made every digit a start of the
            # percentage-first CI pattern: 5 s at 20,000 digits.
            "1" * 20000 + " x",
            "No" + " power" * 2000,
        ):
            started = time.perf_counter()
            has_power_calculation(text)
            has_ci_reporting(text)
            find_sample_size(text)
            assert time.perf_counter() - started < 1.0, text[:12]


class TestADenialDoesNotReachAcrossWhatSeparatesIt:
    """The served full text's false denials, once a percentage could sit
    between a negation and its CI: a table's column headers, and a negation
    governing another noun that the CI is merely attached to."""

    def _denied(self, text, mention):
        start = text.index(mention)
        return is_denied(text, start, start + len(mention))

    def test_a_blank_line_ends_the_reach(self):
        assert self._denied("Death without rehospitalisation\n\nSHR\n\n95% CI", "95% CI") is False
        assert self._denied("No of cases\n\nP\n\n95% CI", "95% CI") is False
        # One line break inside a sentence does not.
        assert self._denied("We did not\nreport 95% CIs.", "95% CIs") is True

    def test_a_negation_governing_another_noun_is_not_a_denial(self):
        text = "with no overlap between the 95% CI of the two measurements"
        assert self._denied(text, "95% CI") is False
        text = "was not significant as the 95% CIs crossed unity"
        assert self._denied(text, "95% CIs") is False

    def test_a_denial_through_by_or_using_is_still_one(self):
        text = "the number was not predetermined by a power calculation"
        assert self._denied(text, "power calculation") is True
        text = "was not previously determined using power analysis"
        assert self._denied(text, "power analysis") is True


class TestTheSecondReviewsFindings:
    """PR #370's review: shapes the first cut misread, and edges the suite did
    not pin (each one a mutant that survived the whole file)."""

    def _denied(self, text, mention):
        start = text.index(mention)
        return is_denied(text, start, start + len(mention))

    def test_a_ci_in_bmlibs_own_markdown_counts(self):
        # The PubMed fetcher writes <sub> as ~x~, <sup> as ^x^ and <i> as *x*.
        assert has_ci_reporting("OR 1.4, CI~95%~ 1.1-2.0") is True
        assert has_ci_reporting("OR 1.4, CI^95%^ 1.1-2.0") is True
        assert has_ci_reporting("OR 1.4 (95% *CI* 1.1-1.8)") is True
        assert has_ci_reporting("OR 1.4 (95% _CI_ 1.1-1.8)") is True

    def test_a_tag_stripped_subscript_ci_counts(self):
        assert has_ci_reporting("OR 1.4, CI95% 1.1-2.0") is True
        # The digits have to be a percentage: a bare "CI95" is no interval.
        assert has_ci_reporting("the CI95 cohort") is False
        assert has_ci_reporting("MCI95% of cases") is False

    def test_a_long_digit_run_is_linear(self):
        import time

        started = time.perf_counter()
        assert has_ci_reporting("1" * 100000) is False
        assert time.perf_counter() - started < 1.0

    def test_the_space_grouping_is_bounded_without_a_clock(self):
        # Unbounded, the grouping reads 1,501 groups and int() refuses the
        # 4,501 digits; bounded, every candidate is a run of zeros.
        assert find_sample_size("1" + " 000" * 1500 + " patients") is None

    def test_a_power_target_the_study_missed_is_not_a_calculation(self):
        for text in (
            "a power of 80% was not achieved",
            "80% power was not reached",
            "a power of 90% could not be attained",
            "a power of 80% cannot be achieved with this sample",
        ):
            assert has_power_calculation(text) is False, text
        assert has_power_calculation("the trial had 80% power to detect it") is True

    def test_not_only_is_not_a_negation(self):
        assert has_power_calculation("We not only performed a power calculation but also") is True
        assert has_power_calculation("Not only was a power analysis performed") is True

    def test_a_ci_stated_with_its_interval_cannot_be_denied(self):
        for text in (
            "No adverse events 95% CI 0.1-0.4",
            "Never smokers 95% CI 1.0-1.4",
            "there was no significant difference 95% CI 0.8-1.2",
            "no difference CI 95% in either arm",
        ):
            assert has_ci_reporting(text) is True, text
        # A mention stating no interval is still refused.
        assert has_ci_reporting("we did not report the 95% CI") is False
        assert has_ci_reporting("95% CI: not reported") is False

    def test_the_evidence_is_the_interval_a_denial_could_not_refuse(self):
        text = "Never smokers 95% CI 1.0-1.4"
        assert "1.0-1.4" in find_ci_context(text)

    def test_a_blank_line_ends_the_reach_after_the_mention_too(self):
        assert self._denied("95% CI\n\nNot reported", "95% CI") is False
        assert self._denied("95% CI\nnot reported", "95% CI") is True

    def test_a_count_after_prose_punctuation_is_read(self):
        assert find_sample_size("Of these,120 patients were randomised") == 120
        assert find_sample_size("were excluded.120 patients remained") == 120
        # After a digit the punctuation is still part of a number.
        assert find_sample_size("0.32 patients") is None
        assert find_sample_size("1,2,120 patients") is None

    def test_every_tense_of_a_denial_after_the_mention(self):
        assert has_power_calculation("A power calculation has not been performed.") is False
        assert has_power_calculation("A power analysis could not be performed.") is False
        assert has_ci_reporting("Confidence intervals were not calculated.") is False

    def test_every_negation_before_the_mention(self):
        for text in (
            "We never performed a power calculation.",
            "We cannot report a power calculation.",
            "neither a power calculation nor a pilot",
            "nor any power calculation",
        ):
            assert has_power_calculation(text) is False, text

    def test_every_preposition_ends_the_reach(self):
        for text, mention in (
            ("no overlap in the 95% CI", "95% CI"),
            ("no widening of the 95% CI", "95% CI"),
            ("no change with the 95% CI", "95% CI"),
        ):
            assert self._denied(text, mention) is False, text

    def test_the_evidence_is_the_credited_one_of_two_identical_phrases(self):
        text = (
            "No power calculation was performed for the pilot study. "
            + "The main trial enrolled far more people over several years. " * 2
            + "A power calculation was done for the main trial."
        )
        evidence = find_power_calc_context(text)
        assert "was done" in evidence
        assert "No power" not in evidence

    def test_case_keeps_a_chemical_prefix_out_of_an_interval(self):
        assert has_ci_reporting("exposure to cis-1,2-dichloroethylene") is False
        assert has_ci_reporting("MCI 20-30 years") is False

    def test_a_bound_after_the_ci_counts(self):
        for text in ("CI lower 1.2", "the CI limits were wide", "CI upper bound"):
            assert has_ci_reporting(text) is True, text

    def test_every_spelling_of_a_quantified_power(self):
        for text in ("power = 0.80", "Power: 80%", "power at 90%", "a power of .80"):
            assert has_power_calculation(text) is True, text

    def test_fifty_percent_power_is_the_floor(self):
        assert has_power_calculation("a power of 50% to detect it") is True
        assert has_power_calculation("a power of 49% to detect it") is False

    def test_an_en_dash_interval_counts(self):
        assert has_ci_reporting("OR 1.4, CI 1.1–2.0") is True

    def test_a_models_power_is_refused_only_right_before_it(self):
        assert has_power_calculation("prognostic power of 85%") is False
        # "diagnostic" earlier in the phrase does not make this power a test's.
        assert has_power_calculation("sample size for diagnostic tests with 80% power") is True

    def test_the_reversed_phrase_takes_computed_and_were(self):
        assert has_power_calculation("sample sizes were computed a priori") is True

    def test_a_power_in_kilowatts_is_not_a_studys(self):
        assert has_power_calculation("a power of 0.5 kW") is False

    def test_a_later_stated_interval_does_not_lend_the_earlier_mention_its_values(self):
        text = (
            "We did not report the 95% CI for the pilot cohort of the study, "
            "and the main trial gave an HR with CI 1.1-2.0."
        )
        assert has_ci_reporting(text) is True
        assert "did not report" not in find_ci_context(text)
