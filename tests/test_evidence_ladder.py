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

"""Tests for the evidence ladder and the number (``bmlib.quality.evidence_ladder``)."""

from __future__ import annotations

import math

import pytest

from bmlib.quality.data_models import StudyDesign
from bmlib.quality.evidence_ladder import (
    LADDER_VERSION,
    EffectScale,
    LadderReason,
    LadderSettings,
    Level,
    ReportedResult,
    build_ladder,
    paper_weight,
    start_level,
)
from bmlib.quality.rob2_algorithm import Judgement


def _ratio(lo: float | None, est: float, hi: float | None, n: int | None) -> ReportedResult:
    level = 0.95 if lo is not None else None
    return ReportedResult("risk ratio", EffectScale.RATIO, est, lo, hi, level, n)


def test_clean_trial_stays_high() -> None:
    r = ReportedResult("risk ratio", EffectScale.RATIO, 0.7, 0.55, 0.88, 0.95, 1200)
    lad = build_ladder(Level.HIGH, Judgement.LOW, r, LadderSettings())
    assert (lad.level, lad.weight) == (Level.HIGH, 1.0)
    assert [s.levels for s in lad.steps] == [0]


def test_some_concerns_and_crossing_interval() -> None:
    lad = build_ladder(
        Level.HIGH, Judgement.SOME_CONCERNS, _ratio(0.6, 0.8, 1.02, 1200), LadderSettings()
    )
    assert [s.levels for s in lad.steps] == [1, 1]
    assert (lad.level, lad.weight) == (Level.LOW, 0.4)
    assert lad.steps[1].reason == LadderReason.crosses_no_effect()


def test_high_risk_crossing_small_trial_floors_at_very_low() -> None:
    lad = build_ladder(Level.HIGH, Judgement.HIGH, _ratio(0.5, 0.9, 1.6, 80), LadderSettings())
    assert [s.levels for s in lad.steps] == [2, 1, 1]
    assert (lad.level, lad.weight) == (Level.VERY_LOW, 0.1)
    assert lad.steps[2].reason == LadderReason.small_trial(80, 400)


def test_imprecision_is_capped_by_trimming_the_last_step() -> None:
    s = LadderSettings(max_imprecision_steps=1)
    lad = build_ladder(Level.HIGH, Judgement.LOW, _ratio(0.5, 0.9, 1.6, 80), s)
    assert [x.levels for x in lad.steps] == [0, 1, 0]
    assert lad.level == Level.MODERATE


def test_difference_scale_not_crossing() -> None:
    r = ReportedResult("mean difference", EffectScale.DIFFERENCE, -2.0, -3.1, -0.9, 0.95, 500)
    lad = build_ladder(Level.HIGH, Judgement.LOW, r, LadderSettings())
    assert lad.level == Level.HIGH


def test_interval_touching_no_effect_crosses() -> None:
    lad = build_ladder(Level.HIGH, Judgement.LOW, _ratio(0.8, 0.9, 1.0, 500), LadderSettings())
    assert lad.steps[1].reason == LadderReason.crosses_no_effect()


def test_no_result_is_no_interval() -> None:
    lad = build_ladder(Level.HIGH, Judgement.LOW, None, LadderSettings())
    assert [s.reason for s in lad.steps][1:] == [LadderReason.no_interval()]
    assert lad.level == Level.MODERATE


def test_result_without_interval_still_checks_the_sample_size() -> None:
    lad = build_ladder(Level.HIGH, Judgement.LOW, _ratio(None, 0.9, None, 80), LadderSettings())
    assert [s.reason.kind for s in lad.steps] == ["risk_of_bias", "no_interval", "small_trial"]


def test_unknown_sample_size_is_shown_with_zero_levels() -> None:
    lad = build_ladder(Level.HIGH, Judgement.LOW, _ratio(0.5, 0.7, 0.9, None), LadderSettings())
    assert lad.steps[-1].reason == LadderReason.sample_size_unknown()
    assert lad.steps[-1].levels == 0
    assert lad.level == Level.HIGH


def test_validate_refusals() -> None:
    bad = [
        _ratio(0.5, 0.7, None, 10),  # one bound only
        _ratio(0.0, 0.7, 0.9, 10),  # ratio bound not positive
        _ratio(0.8, 0.7, 0.9, 10),  # estimate outside
        ReportedResult("r", EffectScale.RATIO, 0.7, 0.5, 0.9, 1.0, 10),  # level
        ReportedResult("r", EffectScale.RATIO, math.nan, None, None, None, 10),
        ReportedResult("r", EffectScale.RATIO, 0.7, 0.5, math.inf, 0.95, 10),
        ReportedResult("r", EffectScale.RATIO, 0.7, 0.5, 0.9, 0.0, 10),  # level 0
    ]
    for r in bad:
        with pytest.raises(ValueError):
            r.validate()
        with pytest.raises(ValueError):
            build_ladder(Level.HIGH, Judgement.LOW, r, LadderSettings())


def test_wrong_types_are_refused_not_coerced() -> None:
    with pytest.raises(ValueError):
        ReportedResult("r", EffectScale.RATIO, "0.7", None, None, None, 10).validate()  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ReportedResult("r", EffectScale.RATIO, True, None, None, None, 10).validate()
    with pytest.raises(ValueError):
        ReportedResult("r", EffectScale.RATIO, 0.7, None, None, None, 2.5).validate()  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ReportedResult("r", EffectScale.RATIO, 0.7, None, None, None, -1).validate()
    with pytest.raises(TypeError):
        build_ladder("high", Judgement.LOW, None, LadderSettings())  # type: ignore[arg-type]


def test_start_level() -> None:
    assert start_level(StudyDesign.RCT) == Level.HIGH
    assert start_level(StudyDesign.COHORT_PROSPECTIVE) is None


def test_paper_weight_is_the_largest() -> None:
    s = LadderSettings()
    low = build_ladder(Level.HIGH, Judgement.HIGH, None, s)
    high = build_ladder(Level.HIGH, Judgement.LOW, _ratio(0.5, 0.7, 0.9, 900), s)
    assert paper_weight([low, high]) == 1.0
    assert paper_weight([]) is None


def test_custom_weights_are_honoured() -> None:
    s = LadderSettings(weight_low=0.5)
    lad = build_ladder(Level.HIGH, Judgement.SOME_CONCERNS, _ratio(0.6, 0.8, 1.02, 1200), s)
    assert lad.weight == 0.5


def test_version() -> None:
    assert LADDER_VERSION == "ladder/1"


def test_a_cap_larger_than_the_last_step_trims_earlier_steps_too() -> None:
    s = LadderSettings(crosses_no_effect_steps=2, small_trial_steps=2, max_imprecision_steps=1)
    lad = build_ladder(Level.HIGH, Judgement.LOW, _ratio(0.5, 0.9, 1.6, 80), s)
    assert [x.levels for x in lad.steps] == [0, 1, 0]


def test_interval_without_a_stated_level_is_accepted_and_judged() -> None:
    r = ReportedResult("risk ratio", EffectScale.RATIO, 0.8, 0.6, 1.02, None, 1200)
    r.validate()
    lad = build_ladder(Level.HIGH, Judgement.LOW, r, LadderSettings())
    assert lad.steps[1].reason == LadderReason.crosses_no_effect()
