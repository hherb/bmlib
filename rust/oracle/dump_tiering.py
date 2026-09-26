#!/usr/bin/env python3
"""Dump bmlib's Tier 1 tables and tiering rule, for the Rust port.

The tables are read out of the module rather than retyped, so the port's
transcription cannot silently drift: every key, every design and the priority
order are compared as data.
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.data_models import QualityAssessment, QualityFilter
from bmlib.quality.manager import METADATA_ACCEPTANCE_THRESHOLD
from bmlib.quality.metadata_filter import (
    METADATA_HIGH_CONFIDENCE,
    PUBMED_TYPE_TO_DESIGN,
    TYPE_PRIORITY,
    _normalize_type,
    classify_from_metadata,
)


def render(assessment: QualityAssessment) -> dict:
    return {
        "study_design": assessment.study_design.name,
        "quality_tier": assessment.quality_tier.value,
        "quality_score": assessment.quality_score,
        "confidence": assessment.confidence,
        "assessment_tier": assessment.assessment_tier,
        "extraction_method": assessment.extraction_method,
        "is_randomized": assessment.is_randomized,
    }


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "classify":
        return render(classify_from_metadata(a["publication_types"]))
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    payload = {
        "constants": {
            "METADATA_HIGH_CONFIDENCE": METADATA_HIGH_CONFIDENCE,
            "METADATA_ACCEPTANCE_THRESHOLD": METADATA_ACCEPTANCE_THRESHOLD,
        },
        "type_to_design": [[k, v.name] for k, v in PUBMED_TYPE_TO_DESIGN.items()],
        "type_priority": list(TYPE_PRIORITY),
        "normalized_lookup": {_normalize_type(k): k for k in PUBMED_TYPE_TO_DESIGN},
        "default_filter": {
            "use_metadata_only": QualityFilter().use_metadata_only,
            "use_llm_classification": QualityFilter().use_llm_classification,
            "use_detailed_assessment": QualityFilter().use_detailed_assessment,
            "use_cochrane_assessment": QualityFilter().use_cochrane_assessment,
        },
    }
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump({"tables": payload, "cases": out}, sys.stdout,
              indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
