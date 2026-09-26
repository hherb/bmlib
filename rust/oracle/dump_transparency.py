#!/usr/bin/env python3
"""Dump bmlib's transparency model rules, for the Rust port.

The partitions are read off the module's own named sets rather than restated, and
`calculate_risk_level` is called directly, so the corpus and the code cannot
drift apart.
"""

from __future__ import annotations

import json
import sys

from bmlib.transparency.models import (
    _ANSWERED_TRIAL_RESULTS_STATUSES,
    _NOT_REFUSED_FULL_TEXT_STATUSES,
    _REFUSED_FULL_TEXT_STATUSES,
    MEDIUM_RISK_SCORE_THRESHOLD,
    FullTextStatus,
    TransparencyRisk,
    TransparencySettings,
    TransparencyUnknownReason,
    TrialResultsStatus,
    calculate_risk_level,
)


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "risk_level":
        settings = TransparencySettings(**a.get("settings", {}))
        return calculate_risk_level(
            a["score"], a["industry_funding"], a["data_availability"],
            a.get("coi_disclosed"), settings,
        ).value
    if fn == "full_text_is_refusal":
        return FullTextStatus(a["status"]).is_refusal
    if fn == "trial_is_answered":
        return TrialResultsStatus(a["status"]).is_answered
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    tables = {
        "risk": [m.value for m in TransparencyRisk],
        "unknown_reason": [m.value for m in TransparencyUnknownReason],
        "full_text_status": [m.value for m in FullTextStatus],
        "trial_results_status": [m.value for m in TrialResultsStatus],
        "refused_full_text": sorted(m.value for m in _REFUSED_FULL_TEXT_STATUSES),
        "not_refused_full_text": sorted(m.value for m in _NOT_REFUSED_FULL_TEXT_STATUSES),
        "answered_trial_results": sorted(m.value for m in _ANSWERED_TRIAL_RESULTS_STATUSES),
        "medium_risk_score_threshold": MEDIUM_RISK_SCORE_THRESHOLD,
    }
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump({"tables": tables, "cases": out}, sys.stdout,
              indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
