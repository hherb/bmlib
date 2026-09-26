#!/usr/bin/env python3
"""Dump bmlib's rule-based quality extractors as JSON, for the Rust port.

The fourth differential oracle. Reads a JSON list of cases on stdin and writes a
JSON list of results on stdout.

    .venv/bin/python rust/oracle/dump_quality.py < rust/oracle/quality_cases.json
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.extractors import (
    calculate_sample_size_score,
    extract_sample_size_dimension,
    extract_study_type,
    find_sample_size,
    get_extracted_sample_size,
    get_extracted_study_type,
    has_ci_reporting,
    has_power_calculation,
    prepare_extractor_search_text,
)
from bmlib.quality.scoring_models import DimensionScore


def dim(d: DimensionScore) -> dict:
    return {
        "dimension_name": d.dimension_name,
        "score": d.score,
        "details": [
            {
                "dimension": x.dimension,
                "component": x.component,
                "extracted_value": x.extracted_value,
                "score_contribution": x.score_contribution,
                "evidence_text": x.evidence_text,
                "reasoning": x.reasoning,
            }
            for x in d.details
        ],
    }


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})
    doc = args.get("document", {})

    if fn == "find_sample_size":
        return find_sample_size(args["text"], args.get("min_n", 5), args.get("max_n", 1000000))
    if fn == "calculate_sample_size_score":
        return calculate_sample_size_score(args["n"], args.get("log_multiplier", 2.0))
    if fn == "has_power_calculation":
        return has_power_calculation(args["text"])
    if fn == "has_ci_reporting":
        return has_ci_reporting(args["text"])
    if fn == "prepare_search_text":
        return prepare_extractor_search_text(doc)
    if fn == "extract_study_type":
        d = extract_study_type(doc)
        return {"score": d.score, "type": get_extracted_study_type(d), "details": dim(d)["details"]}
    if fn == "extract_sample_size_dimension":
        d = extract_sample_size_dimension(doc)
        return {"score": d.score, "n": get_extracted_sample_size(d), "details": dim(d)["details"]}
    if fn == "dimension_score_roundtrip":
        d = DimensionScore.from_dict(args["data"])
        return dim(d)
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001 - the oracle records failures too
            results.append(
                {"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
