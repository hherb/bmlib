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

"""The evidence ladder and the number: risk of bias and imprecision to a weight.

A GRADE-style ladder for one primary outcome of a randomised trial. It starts
at ``Level.HIGH``, steps down for the RoB 2 overall judgement and for
imprecision (an interval that includes no effect, a small trial, no interval),
and ends at a level whose table weight is the number. Pure functions, no LLM.
Rust's ``rust/bmlib/src/quality/evidence_ladder.rs`` is the same rules;
``tests/fixtures/evidence_ladder.json`` holds both to them.

Inputs that Rust's types would refuse (wrong types, non-finite numbers) are
refused here too, never coerced.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from bmlib.quality.data_models import StudyDesign
from bmlib.quality.rob2_algorithm import Judgement

LADDER_VERSION = "ladder/1"
"""Names the ladder rules; stored beside a result so it can be re-computed."""

RATIO_NO_EFFECT = 1.0
"""The value of a ratio measure that means no effect."""

DIFFERENCE_NO_EFFECT = 0.0
"""The value of a difference measure that means no effect."""

U8_MAX = 2**8 - 1
"""Largest step count Rust's ``u8`` holds."""

U32_MAX = 2**32 - 1
"""Largest sample size Rust's ``u32`` holds; Python refuses more."""


class Level(Enum):
    """Certainty of the evidence, best first; the value is the wire form."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    VERY_LOW = "very_low"


_LEVELS: tuple[Level, ...] = tuple(Level)


class EffectScale(Enum):
    """Whether an effect measure is a ratio (no effect 1.0) or a difference (0.0)."""

    RATIO = "ratio"
    DIFFERENCE = "difference"

    def no_effect(self) -> float:
        """The value of this scale that means no effect."""
        return RATIO_NO_EFFECT if self is EffectScale.RATIO else DIFFERENCE_NO_EFFECT


def _is_number(value: object) -> bool:
    """A finite int or float (a bool is not a number here)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class ReportedResult:
    """The effect a paper reports for one outcome; ``measure`` is free text."""

    measure: str
    scale: EffectScale
    estimate: float
    ci_low: float | None = None
    ci_high: float | None = None
    ci_level: float | None = None
    n_analysed: int | None = None

    def validate(self) -> None:
        """Raise ``ValueError`` naming the field if the result is incoherent.

        Both bounds or neither; all numbers finite; ``ci_low <= estimate <=
        ci_high``; ratio values above 0; ``0 < ci_level < 1`` when stated (it is
        optional); ``n_analysed`` a whole number that fits a u32.
        """
        if not isinstance(self.measure, str):
            raise ValueError("measure must be text")
        if not isinstance(self.scale, EffectScale):
            raise ValueError("scale must be ratio or difference")
        for name in ("estimate", "ci_low", "ci_high", "ci_level"):
            value = getattr(self, name)
            if value is None and name != "estimate":
                continue
            if not _is_number(value):
                raise ValueError(f"{name} must be a finite number")
        n = self.n_analysed
        if n is not None and (
            isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= U32_MAX
        ):
            raise ValueError("n_analysed must be a whole number that fits 32 bits")
        if (self.ci_low is None) != (self.ci_high is None):
            raise ValueError("ci_low and ci_high must be given together")
        if self.ci_level is not None and not 0 < self.ci_level < 1:
            raise ValueError("ci_level must be above 0 and below 1")
        if self.scale is EffectScale.RATIO:
            for name in ("estimate", "ci_low", "ci_high"):
                value = getattr(self, name)
                if value is not None and value <= 0:
                    raise ValueError(f"{name} must be above 0 on the ratio scale")
        if self.ci_low is not None and self.ci_high is not None:
            if not self.ci_low <= self.estimate <= self.ci_high:
                raise ValueError("ci_low <= estimate <= ci_high does not hold")


@dataclass(frozen=True)
class LadderSettings:
    """Every number the ladder uses; the defaults are the shipped rules."""

    some_concerns_steps: int = 1
    high_steps: int = 2
    crosses_no_effect_steps: int = 1
    small_trial_n: int = 400
    """GRADE's rule-of-thumb optimal information size (continuous outcome); a default to tune."""
    small_trial_steps: int = 1
    no_interval_steps: int = 1
    max_imprecision_steps: int = 2
    weight_high: float = 1.0
    weight_moderate: float = 0.7
    weight_low: float = 0.4
    weight_very_low: float = 0.1

    def weight(self, level: Level) -> float:
        """The table weight of a level."""
        return {
            Level.HIGH: self.weight_high,
            Level.MODERATE: self.weight_moderate,
            Level.LOW: self.weight_low,
            Level.VERY_LOW: self.weight_very_low,
        }[level]


@dataclass(frozen=True)
class LadderReason:
    """Why a step was taken; ``kind`` is the wire name, the other fields belong to it."""

    kind: str
    judgement: Judgement | None = None
    n: int | None = None
    threshold: int | None = None

    @staticmethod
    def risk_of_bias(judgement: Judgement) -> LadderReason:
        """The RoB 2 overall judgement."""
        return LadderReason("risk_of_bias", judgement=judgement)

    @staticmethod
    def crosses_no_effect() -> LadderReason:
        """The interval includes the no-effect value."""
        return LadderReason("crosses_no_effect")

    @staticmethod
    def small_trial(n: int, threshold: int) -> LadderReason:
        """Fewer analysed than the threshold."""
        return LadderReason("small_trial", n=n, threshold=threshold)

    @staticmethod
    def no_interval() -> LadderReason:
        """No result, or no interval."""
        return LadderReason("no_interval")

    @staticmethod
    def sample_size_unknown() -> LadderReason:
        """The number analysed is not reported (shown, costs nothing)."""
        return LadderReason("sample_size_unknown")

    def to_wire(self) -> Any:
        """The JSON form, as serde writes the Rust enum (externally tagged)."""
        if self.kind == "risk_of_bias":
            assert self.judgement is not None
            return {self.kind: self.judgement.value}
        if self.kind == "small_trial":
            return {self.kind: {"n": self.n, "threshold": self.threshold}}
        return self.kind

    @staticmethod
    def from_wire(raw: object) -> LadderReason:
        """Rebuild from :meth:`to_wire` output.

        Raises:
            ValueError: *raw* is not a reason serde would write.
        """
        if isinstance(raw, str) and raw in _UNIT_REASONS:
            return LadderReason(raw)
        if isinstance(raw, dict) and len(raw) == 1:
            ((kind, body),) = raw.items()
            if kind == "risk_of_bias":
                return LadderReason.risk_of_bias(Judgement(body))
            if kind == "small_trial" and isinstance(body, dict) and set(body) == {"n", "threshold"}:
                n, threshold = body["n"], body["threshold"]
                if all(
                    isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= U32_MAX
                    for v in (n, threshold)
                ):
                    return LadderReason.small_trial(n, threshold)
        raise ValueError(f"{raw!r} is not a ladder reason")


_UNIT_REASONS = ("crosses_no_effect", "no_interval", "sample_size_unknown")


@dataclass(frozen=True)
class LadderStep:
    """One step down the ladder: the reason and how many levels it cost."""

    reason: LadderReason
    levels: int


@dataclass(frozen=True)
class EvidenceLadder:
    """The whole ladder for one outcome: start, steps, end level and weight."""

    start: Level
    steps: tuple[LadderStep, ...]
    level: Level
    weight: float

    def to_wire(self) -> dict[str, Any]:
        """The JSON form, as serde writes the Rust struct."""
        return {
            "start": self.start.value,
            "steps": [{"reason": s.reason.to_wire(), "levels": s.levels} for s in self.steps],
            "level": self.level.value,
            "weight": self.weight,
        }

    @staticmethod
    def from_wire(raw: object) -> EvidenceLadder:
        """Rebuild from :meth:`to_wire` output (the steps are not re-derived).

        Raises:
            ValueError: A field is absent or of the wrong type or value, naming it.
        """
        if not isinstance(raw, dict):
            raise ValueError("the ladder is not an object")
        steps = raw.get("steps")
        weight = raw.get("weight")
        if not isinstance(steps, list):
            raise ValueError("steps is not a list")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, int | float)
            or not math.isfinite(weight)
        ):
            raise ValueError("weight is not a finite number")
        parsed = []
        for step in steps:
            levels = step.get("levels") if isinstance(step, dict) else None
            if isinstance(levels, bool) or not isinstance(levels, int) or not 0 <= levels <= U8_MAX:
                raise ValueError("a step's levels is not a whole number that fits 8 bits")
            parsed.append(LadderStep(LadderReason.from_wire(step.get("reason")), levels))
        try:
            return EvidenceLadder(
                Level(raw.get("start")), tuple(parsed), Level(raw.get("level")), float(weight)
            )
        except ValueError:
            raise ValueError("start or level is not a level") from None


def settings_to_wire(settings: LadderSettings) -> dict[str, Any]:
    """The JSON form of settings, field for field."""
    return asdict(settings)


def start_level(design: StudyDesign) -> Level | None:
    """Where a design's ladder starts: an RCT at ``HIGH``, every other design not covered."""
    return Level.HIGH if design is StudyDesign.RCT else None


def build_ladder(
    start: Level,
    rob_overall: Judgement,
    result: ReportedResult | None,
    settings: LadderSettings,
) -> EvidenceLadder:
    """Walk the ladder for one outcome.

    A risk-of-bias step always appears (0 levels for Low). Imprecision steps
    are summed, capped at ``max_imprecision_steps`` (the cap trims the last
    step's levels), and the level never drops below ``VERY_LOW``. Raises
    ``ValueError`` only for a result that fails ``validate``; ``TypeError`` for
    arguments of the wrong type.
    """
    if not (
        isinstance(start, Level)
        and isinstance(rob_overall, Judgement)
        and isinstance(settings, LadderSettings)
        and (result is None or isinstance(result, ReportedResult))
    ):
        raise TypeError("build_ladder: arguments of the wrong type")
    if result is not None:
        result.validate()
    rob_levels = {
        Judgement.LOW: 0,
        Judgement.SOME_CONCERNS: settings.some_concerns_steps,
        Judgement.HIGH: settings.high_steps,
    }[rob_overall]
    steps = [LadderStep(LadderReason.risk_of_bias(rob_overall), rob_levels)]

    imprecision: list[LadderStep] = []
    if result is None or result.ci_low is None or result.ci_high is None:
        imprecision.append(LadderStep(LadderReason.no_interval(), settings.no_interval_steps))
    elif result.ci_low <= result.scale.no_effect() <= result.ci_high:
        imprecision.append(
            LadderStep(LadderReason.crosses_no_effect(), settings.crosses_no_effect_steps)
        )
    if result is not None:
        if result.n_analysed is None:
            imprecision.append(LadderStep(LadderReason.sample_size_unknown(), 0))
        elif result.n_analysed < settings.small_trial_n:
            reason = LadderReason.small_trial(result.n_analysed, settings.small_trial_n)
            imprecision.append(LadderStep(reason, settings.small_trial_steps))

    excess = sum(s.levels for s in imprecision) - settings.max_imprecision_steps
    for i in reversed(range(len(imprecision))):  # trim from the last step back
        if excess <= 0:
            break
        cut = min(excess, imprecision[i].levels)
        imprecision[i] = LadderStep(imprecision[i].reason, imprecision[i].levels - cut)
        excess -= cut
    steps.extend(imprecision)

    drop = sum(s.levels for s in steps)
    level = _LEVELS[min(_LEVELS.index(start) + drop, len(_LEVELS) - 1)]
    return EvidenceLadder(start, tuple(steps), level, settings.weight(level))


def paper_weight(ladders: list[EvidenceLadder]) -> float | None:
    """The largest weight of any primary outcome (a paper passes if any outcome passes)."""
    return max((lad.weight for lad in ladders), default=None)
