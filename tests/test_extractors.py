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
    calculate_sample_size_score,
    extract_sample_size_dimension,
    extract_study_type,
    extract_text_context,
    find_sample_size,
    get_extracted_sample_size,
    get_extracted_study_type,
    has_ci_reporting,
    has_exclusion_pattern,
    has_power_calculation,
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


class TestExclusionAndContext:
    def test_exclusion_pattern_blocks_false_positive(self):
        text = "this was a non-randomized trial of patients"
        assert has_exclusion_pattern(text, "randomized trial", ["non-randomized"]) is True

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
        doc = {"abstract": ("n = 1000000 participants, power calculation done, 95% CI reported")}
        result = extract_sample_size_dimension(doc)
        assert result.score == 10.0
