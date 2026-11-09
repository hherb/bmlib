// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! The evidence ladder and the number: risk of bias and imprecision to a weight.
//!
//! A GRADE-style ladder for one primary outcome of a randomised trial. It
//! starts at [`Level::High`], steps down for the RoB 2 overall judgement and
//! for imprecision (an interval that includes no effect, a small trial, no
//! interval), and ends at a level whose table weight is the number. Pure
//! functions, no LLM. `bmlib/quality/evidence_ladder.py` is the same rules;
//! `tests/fixtures/evidence_ladder.json` holds both to them.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::data_models::StudyDesign;
use super::rob2_algorithm::Judgement;

/// Names the ladder rules; stored beside a result so it can be re-computed.
pub const LADDER_VERSION: &str = "ladder/1";

/// The value of a ratio measure that means no effect.
pub const RATIO_NO_EFFECT: f64 = 1.0;

/// The value of a difference measure that means no effect.
pub const DIFFERENCE_NO_EFFECT: f64 = 0.0;

/// The smallest table weight a setting may give.
pub const WEIGHT_MIN: f64 = 0.0;

/// The largest table weight a setting may give.
pub const WEIGHT_MAX: f64 = 1.0;

/// Message for weights that increase down the ladder.
const WEIGHT_ORDER: &str = "the weights must not increase from weight_high to weight_very_low";

/// Certainty of the evidence, best first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Level {
    /// Further research is very unlikely to change the conclusion.
    High,
    /// Further research is likely to have an important impact.
    Moderate,
    /// Further research is very likely to have an important impact.
    Low,
    /// Any estimate is very uncertain.
    VeryLow,
}

impl Level {
    /// All levels, best first.
    const ALL: [Level; 4] = [Level::High, Level::Moderate, Level::Low, Level::VeryLow];

    /// Position in [`Level::ALL`]: 0 for `High`.
    fn index(self) -> usize {
        Level::ALL
            .iter()
            .position(|l| *l == self)
            .expect("every level is in ALL")
    }
}

/// Whether an effect measure is a ratio (no effect 1.0) or a difference (0.0).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectScale {
    /// Risk ratio, odds ratio, hazard ratio.
    Ratio,
    /// Mean difference, risk difference.
    Difference,
}

impl EffectScale {
    /// The value of this scale that means no effect.
    pub fn no_effect(self) -> f64 {
        match self {
            EffectScale::Ratio => RATIO_NO_EFFECT,
            EffectScale::Difference => DIFFERENCE_NO_EFFECT,
        }
    }
}

/// The effect a paper reports for one outcome; `measure` is free text.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReportedResult {
    /// The measure's name, as the paper gives it ("risk ratio").
    pub measure: String,
    /// Ratio or difference.
    pub scale: EffectScale,
    /// The point estimate.
    pub estimate: f64,
    /// Lower bound of the interval, if reported.
    pub ci_low: Option<f64>,
    /// Upper bound of the interval, if reported.
    pub ci_high: Option<f64>,
    /// The interval's level, such as 0.95.
    pub ci_level: Option<f64>,
    /// How many participants were analysed, if reported.
    pub n_analysed: Option<u32>,
}

impl ReportedResult {
    /// Refuse an incoherent result, naming the field.
    ///
    /// Both bounds or neither; all numbers
    /// finite; `ci_low <= estimate <= ci_high`; ratio values above 0;
    /// `0 < ci_level < 1` when the level is stated (it is optional).
    pub fn validate(&self) -> Result<(), String> {
        for (name, value) in [
            ("estimate", Some(self.estimate)),
            ("ci_low", self.ci_low),
            ("ci_high", self.ci_high),
            ("ci_level", self.ci_level),
        ] {
            if value.is_some_and(|v| !v.is_finite()) {
                return Err(format!("{name} must be a finite number"));
            }
        }
        if self.ci_low.is_some() != self.ci_high.is_some() {
            return Err("ci_low and ci_high must be given together".into());
        }
        if self.ci_level.is_some_and(|l| !(l > 0.0 && l < 1.0)) {
            return Err("ci_level must be above 0 and below 1".into());
        }
        if self.scale == EffectScale::Ratio {
            for (name, value) in [
                ("estimate", Some(self.estimate)),
                ("ci_low", self.ci_low),
                ("ci_high", self.ci_high),
            ] {
                if value.is_some_and(|v| v <= 0.0) {
                    return Err(format!("{name} must be above 0 on the ratio scale"));
                }
            }
        }
        if let (Some(low), Some(high)) = (self.ci_low, self.ci_high) {
            if !(low <= self.estimate && self.estimate <= high) {
                return Err("ci_low <= estimate <= ci_high does not hold".into());
            }
        }
        Ok(())
    }
}

/// Every number the ladder uses; the defaults are the shipped rules.
///
/// Deserialising refuses an unknown field and anything
/// [`LadderSettings::validate`] refuses, in Python's words; an absent field
/// takes its default (Python's `settings_from_wire`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "Value")]
pub struct LadderSettings {
    /// Levels lost to "some concerns" overall risk of bias.
    pub some_concerns_steps: u8,
    /// Levels lost to "high" overall risk of bias.
    pub high_steps: u8,
    /// Levels lost when the interval includes the no-effect value.
    pub crosses_no_effect_steps: u8,
    /// Fewer analysed than this is a small trial. 400 is GRADE's rule-of-thumb
    /// optimal information size for a continuous outcome; a default to tune.
    pub small_trial_n: u32,
    /// Levels lost to a small trial.
    pub small_trial_steps: u8,
    /// Levels lost when no interval is reported.
    pub no_interval_steps: u8,
    /// Imprecision never costs more than this many levels in total.
    pub max_imprecision_steps: u8,
    /// Weight of a `High` ladder.
    pub weight_high: f64,
    /// Weight of a `Moderate` ladder.
    pub weight_moderate: f64,
    /// Weight of a `Low` ladder.
    pub weight_low: f64,
    /// Weight of a `VeryLow` ladder.
    pub weight_very_low: f64,
}

impl Default for LadderSettings {
    fn default() -> Self {
        LadderSettings {
            some_concerns_steps: 1,
            high_steps: 2,
            crosses_no_effect_steps: 1,
            small_trial_n: 400,
            small_trial_steps: 1,
            no_interval_steps: 1,
            max_imprecision_steps: 2,
            weight_high: 1.0,
            weight_moderate: 0.7,
            weight_low: 0.4,
            weight_very_low: 0.1,
        }
    }
}

/// Refuse `weight` for the setting `name` unless it is a finite number from 0 to 1.
fn check_weight(name: &str, weight: f64) -> Result<f64, String> {
    if weight.is_finite() && (WEIGHT_MIN..=WEIGHT_MAX).contains(&weight) {
        Ok(weight)
    } else {
        Err(format!("{name} must be a finite number from 0 to 1"))
    }
}

impl TryFrom<Value> for LadderSettings {
    type Error = String;

    /// Read settings field by field, in declaration order, as Python's
    /// `settings_from_wire` does, then [`LadderSettings::validate`].
    fn try_from(raw: Value) -> Result<Self, String> {
        const FIELDS: [&str; 11] = [
            "some_concerns_steps",
            "high_steps",
            "crosses_no_effect_steps",
            "small_trial_n",
            "small_trial_steps",
            "no_interval_steps",
            "max_imprecision_steps",
            "weight_high",
            "weight_moderate",
            "weight_low",
            "weight_very_low",
        ];
        let Value::Object(map) = raw else {
            return Err("the ladder settings are not an object".to_string());
        };
        let mut keys: Vec<&String> = map.keys().collect();
        keys.sort();
        if let Some(unknown) = keys.into_iter().find(|k| !FIELDS.contains(&k.as_str())) {
            return Err(format!("\"{unknown}\" is not a ladder setting"));
        }
        let steps = |name: &str, default: u8| -> Result<u8, String> {
            map.get(name).map_or(Ok(default), |v| {
                v.as_u64()
                    .and_then(|n| u8::try_from(n).ok())
                    .ok_or_else(|| format!("{name} must be a whole number that fits 8 bits"))
            })
        };
        let weight = |name: &str, default: f64| -> Result<f64, String> {
            map.get(name).map_or(Ok(default), |v| {
                v.as_f64()
                    .ok_or_else(|| format!("{name} must be a finite number from 0 to 1"))
                    .and_then(|w| check_weight(name, w))
            })
        };
        let d = LadderSettings::default();
        let settings = LadderSettings {
            some_concerns_steps: steps(FIELDS[0], d.some_concerns_steps)?,
            high_steps: steps(FIELDS[1], d.high_steps)?,
            crosses_no_effect_steps: steps(FIELDS[2], d.crosses_no_effect_steps)?,
            small_trial_n: map.get(FIELDS[3]).map_or(Ok(d.small_trial_n), |v| {
                v.as_u64()
                    .and_then(|n| u32::try_from(n).ok())
                    .ok_or_else(|| {
                        format!("{} must be a whole number that fits 32 bits", FIELDS[3])
                    })
            })?,
            small_trial_steps: steps(FIELDS[4], d.small_trial_steps)?,
            no_interval_steps: steps(FIELDS[5], d.no_interval_steps)?,
            max_imprecision_steps: steps(FIELDS[6], d.max_imprecision_steps)?,
            weight_high: weight(FIELDS[7], d.weight_high)?,
            weight_moderate: weight(FIELDS[8], d.weight_moderate)?,
            weight_low: weight(FIELDS[9], d.weight_low)?,
            weight_very_low: weight(FIELDS[10], d.weight_very_low)?,
        };
        settings.validate()?;
        Ok(settings)
    }
}

impl LadderSettings {
    /// Refuse settings the ladder cannot use (Python's `__post_init__`).
    ///
    /// Each weight must be a finite number from 0 to 1 (checked in field
    /// order), and the weights must not increase from `weight_high` to
    /// `weight_very_low`. The step counts and `small_trial_n` are whole
    /// numbers by type.
    ///
    /// # Errors
    ///
    /// A message naming the first weight refused, or the order.
    pub fn validate(&self) -> Result<(), String> {
        let weights = [
            ("weight_high", self.weight_high),
            ("weight_moderate", self.weight_moderate),
            ("weight_low", self.weight_low),
            ("weight_very_low", self.weight_very_low),
        ];
        for (name, weight) in weights {
            check_weight(name, weight)?;
        }
        if weights.windows(2).any(|pair| pair[0].1 < pair[1].1) {
            return Err(WEIGHT_ORDER.to_string());
        }
        Ok(())
    }

    /// The table weight of a level.
    pub fn weight(&self, level: Level) -> f64 {
        match level {
            Level::High => self.weight_high,
            Level::Moderate => self.weight_moderate,
            Level::Low => self.weight_low,
            Level::VeryLow => self.weight_very_low,
        }
    }
}

/// Why a step was taken.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LadderReason {
    /// The RoB 2 overall judgement.
    RiskOfBias(Judgement),
    /// The interval includes the no-effect value.
    CrossesNoEffect,
    /// Fewer analysed (`n`) than the `threshold`.
    SmallTrial {
        /// How many were analysed.
        n: u32,
        /// The threshold it fell under.
        threshold: u32,
    },
    /// No result, or no interval.
    NoInterval,
    /// The number analysed is not reported (shown, costs nothing).
    SampleSizeUnknown,
}

/// One step down the ladder: the reason and how many levels it cost.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct LadderStep {
    /// Why.
    pub reason: LadderReason,
    /// Levels lost (0 is shown, not hidden).
    pub levels: u8,
}

/// The whole ladder for one outcome: start, steps, end level and weight.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidenceLadder {
    /// Where the ladder started.
    pub start: Level,
    /// Every step, risk of bias first.
    pub steps: Vec<LadderStep>,
    /// Where it ended, never below `VeryLow`.
    pub level: Level,
    /// The number: the end level's table weight.
    pub weight: f64,
}

/// Where a design's ladder starts: an RCT at `High`, every other design `None` (not covered).
pub fn start_level(design: StudyDesign) -> Option<Level> {
    (design == StudyDesign::Rct).then_some(Level::High)
}

/// Walk the ladder for one outcome.
///
/// A risk-of-bias step always appears (0 levels for Low). Imprecision steps are
/// summed, capped at `max_imprecision_steps` (the cap trims from the last step
/// back), and the level never drops below `VeryLow`. `Err` only for settings
/// that fail [`LadderSettings::validate`] or a result that fails
/// [`ReportedResult::validate`].
pub fn build_ladder(
    start: Level,
    rob_overall: Judgement,
    result: Option<&ReportedResult>,
    settings: &LadderSettings,
) -> Result<EvidenceLadder, String> {
    settings.validate()?;
    if let Some(result) = result {
        result.validate()?;
    }
    let rob_levels = match rob_overall {
        Judgement::Low => 0,
        Judgement::SomeConcerns => settings.some_concerns_steps,
        Judgement::High => settings.high_steps,
    };
    let mut imprecision = Vec::new();
    let interval = result.and_then(|r| r.ci_low.zip(r.ci_high).map(|bounds| (r, bounds)));
    match interval {
        None => imprecision.push(LadderStep {
            reason: LadderReason::NoInterval,
            levels: settings.no_interval_steps,
        }),
        Some((r, (low, high))) if low <= r.scale.no_effect() && r.scale.no_effect() <= high => {
            imprecision.push(LadderStep {
                reason: LadderReason::CrossesNoEffect,
                levels: settings.crosses_no_effect_steps,
            })
        }
        Some(_) => {}
    }
    let sample = result.and_then(|r| match r.n_analysed {
        None => Some(LadderStep {
            reason: LadderReason::SampleSizeUnknown,
            levels: 0,
        }),
        Some(n) if n < settings.small_trial_n => Some(LadderStep {
            reason: LadderReason::SmallTrial {
                n,
                threshold: settings.small_trial_n,
            },
            levels: settings.small_trial_steps,
        }),
        Some(_) => None,
    });
    imprecision.extend(sample);

    let total: u32 = imprecision.iter().map(|s| u32::from(s.levels)).sum();
    let mut excess = total.saturating_sub(u32::from(settings.max_imprecision_steps));
    for step in imprecision.iter_mut().rev() {
        let cut = excess.min(u32::from(step.levels));
        step.levels -= cut as u8; // cut <= levels, so it fits a u8
        excess -= cut;
    }
    let mut steps = vec![LadderStep {
        reason: LadderReason::RiskOfBias(rob_overall),
        levels: rob_levels,
    }];
    steps.extend(imprecision);
    let drop: usize = steps.iter().map(|s| usize::from(s.levels)).sum();
    let level = Level::ALL[(start.index() + drop).min(Level::ALL.len() - 1)];
    Ok(EvidenceLadder {
        start,
        steps,
        level,
        weight: settings.weight(level),
    })
}

/// The largest weight of any primary outcome; `None` for no outcomes.
///
/// A paper passes if any primary outcome passes (spec section 4.1).
pub fn paper_weight(ladders: &[EvidenceLadder]) -> Option<f64> {
    ladders.iter().map(|l| l.weight).reduce(f64::max)
}
