#!/usr/bin/env python3
"""Dump bmlib's LLM quality-answer parsers, for the Rust port.

`_parse_data` is called directly — it is a pure function of the parsed JSON — so
the corpus pins the reading rules without a model. The cases carrying a
`corrected` block are defect #295: the Python raises where the port reads "not
answered".
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.data_models import BiasRisk, QualityAssessment
from bmlib.quality.quality_agent import QualityAgent
from bmlib.quality.study_classifier import StudyClassifier


def render(assessment: QualityAssessment) -> dict:
    return {
        "assessment_tier": assessment.assessment_tier,
        "extraction_method": assessment.extraction_method,
        "study_design": assessment.study_design.name,
        "quality_tier": assessment.quality_tier.value,
        "quality_score": assessment.quality_score,
        "evidence_level": assessment.evidence_level,
        "is_randomized": assessment.is_randomized,
        "is_controlled": assessment.is_controlled,
        "is_blinded": assessment.is_blinded,
        "is_prospective": assessment.is_prospective,
        "is_multicenter": assessment.is_multicenter,
        "sample_size": assessment.sample_size,
        "confidence": assessment.confidence,
        "bias_risk": (
            {
                "selection": assessment.bias_risk.selection,
                "performance": assessment.bias_risk.performance,
                "detection": assessment.bias_risk.detection,
                "attrition": assessment.bias_risk.attrition,
                "reporting": assessment.bias_risk.reporting,
            }
            if assessment.bias_risk is not None
            else None
        ),
        "strengths": list(assessment.strengths),
        "limitations": list(assessment.limitations),
        "extraction_details": list(assessment.extraction_details),
    }


def parse_tier3(data):
    """`QualityAgent._parse_data`, called without constructing an agent.

    The method touches no instance state, so it can be called on a bare object —
    which is what keeps this oracle a pure read of the Python's rule.
    """
    agent = QualityAgent.__new__(QualityAgent)
    return render(QualityAgent._parse_data(agent, data))


def parse_tier2(data):
    agent = StudyClassifier.__new__(StudyClassifier)
    return render(StudyClassifier._parse_data(agent, data))


def run(case):
    fn = case["fn"]
    data = case["args"]["data"]
    if fn == "parse_assessment":
        return parse_tier3(data)
    if fn == "parse_classification":
        return parse_tier2(data)
    if fn == "bias_risk_from_dict":
        bias = BiasRisk.from_dict(data)
        return {
            "selection": bias.selection, "performance": bias.performance,
            "detection": bias.detection, "attrition": bias.attrition,
            "reporting": bias.reporting,
        }
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
