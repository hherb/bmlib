#!/usr/bin/env python3
"""Dump bmlib's quality data models as JSON, for the Rust port.

    .venv/bin/python rust/oracle/dump_models.py < rust/oracle/model_cases.json
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.data_models import (
    DESIGN_TO_RANDOMIZED,
    DESIGN_TO_SCORE,
    DESIGN_TO_TIER,
    STUDY_DESIGN_MAPPING,
    BiasRisk,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    StudyDesign,
)


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})

    if fn == "design_mappings":
        return {
            "tier": {d.value: DESIGN_TO_TIER[d].value for d in StudyDesign},
            "score": {d.value: DESIGN_TO_SCORE[d] for d in StudyDesign},
            "randomized": {
                d.value: DESIGN_TO_RANDOMIZED.get(d) for d in StudyDesign
            },
            "design_count": len(list(StudyDesign)),
            "tier_count": len(list(QualityTier)),
        }
    if fn == "design_from_str":
        d = STUDY_DESIGN_MAPPING.get(args["raw"], StudyDesign.UNKNOWN)
        return d.value
    if fn == "tier_ordering":
        return [t.value for t in sorted(QualityTier)]
    if fn == "bias_roundtrip":
        b = BiasRisk.from_dict(args["data"])
        return b.to_dict()
    if fn == "assessment_unclassified":
        a = QualityAssessment.unclassified()
        return a.to_dict()
    if fn == "assessment_from_metadata":
        a = QualityAssessment.from_metadata(StudyDesign(args["design"]))
        return a.to_dict()
    if fn == "assessment_from_classification":
        a = QualityAssessment.from_classification(
            StudyDesign(args["design"]),
            confidence=args.get("confidence", 0.7),
            sample_size=args.get("sample_size"),
            is_blinded=args.get("is_blinded"),
        )
        return a.to_dict()
    if fn == "assessment_roundtrip":
        a = QualityAssessment.from_dict(args["data"])
        return a.to_dict()
    if fn == "assessment_to_dict":
        # Build the object directly rather than through `from_dict`: the
        # `cochrane_assessment` field is typed `Any`, so a caller who
        # round-tripped through JSON may assign the plain dict straight back,
        # and `to_dict()` must tolerate that. Python's own test does exactly
        # this. (Going through `from_dict` would raise `KeyError: 'methods'`
        # instead — a real limitation of `CochraneStudyAssessment.from_dict`,
        # which is why this is a `to_dict` case and not a round-trip one.)
        data = dict(args["data"])
        if isinstance(data.get("study_design"), str):
            data["study_design"] = StudyDesign(data["study_design"])
        if isinstance(data.get("quality_tier"), int):
            data["quality_tier"] = QualityTier(data["quality_tier"])
        a = QualityAssessment(**data)
        return a.to_dict()
    if fn == "passes_filter":
        a = QualityAssessment.from_dict(args["assessment"])
        f = QualityFilter(**{k: (QualityTier(v) if k == "min_tier" else v)
                             for k, v in args.get("filter", {}).items()})
        return a.passes_filter(f)
    if fn == "filter_defaults":
        f = QualityFilter()
        return {
            "min_tier": f.min_tier.value if f.min_tier else None,
            "require_randomization": f.require_randomization,
            "require_blinding": f.require_blinding,
            "min_sample_size": f.min_sample_size,
            "use_metadata_only": f.use_metadata_only,
            "use_llm_classification": f.use_llm_classification,
            "use_detailed_assessment": f.use_detailed_assessment,
            "use_cochrane_assessment": f.use_cochrane_assessment,
        }
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001 - the oracle records failures too
            results.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
