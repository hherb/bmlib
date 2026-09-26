#!/usr/bin/env python3
"""Dump bmlib's Cochrane formatters as JSON, for the Rust port.

.venv/bin/python rust/oracle/dump_formatter.py < rust/oracle/formatter_cases.json
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.cochrane_formatter import (
    COCHRANE_CSS,
    format_complete_assessment_markdown,
    format_multiple_assessments_markdown,
    format_risk_of_bias_html,
    format_risk_of_bias_markdown,
    format_risk_of_bias_summary_markdown,
    format_study_characteristics_html,
    format_study_characteristics_markdown,
    get_cochrane_css,
)
from bmlib.quality.cochrane_models import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    create_default_cochrane_risk_of_bias,
)


def rob(judgements=None):
    r = create_default_cochrane_risk_of_bias()
    for name, j in (judgements or {}).items():
        getattr(r, name).judgement = j
    return r


def chars(o=None):
    o = o or {}
    ch = CochraneStudyCharacteristics(
        study_id=o.get("study_id", "Andrei 2011"),
        methods=o.get("methods", "Parallel randomised trial"),
        participants=CochraneParticipants(
            setting=o.get("setting", "Romania"),
            population=o.get("population", "Chronic heart failure"),
            total_participants=o.get("total_participants"),
            group_sizes=o.get("group_sizes"),
        ),
        interventions=CochraneInterventions(
            description=o.get("intervention_description", "Hospital at home")
        ),
        outcomes=CochraneOutcomes(description=o.get("outcomes_description", "Mortality, cost")),
        notes=CochraneNotes(
            funding_source=o.get("funding_source"),
            additional_notes=o.get("additional_notes"),
        ),
    )
    ch.created_at = None
    return ch


def assessment(o=None, judgements=None, **kw):
    return CochraneStudyAssessment(
        study_characteristics=chars(o),
        risk_of_bias=rob(judgements),
        overall_quality_score=kw.get("overall_quality_score"),
        overall_confidence=kw.get("overall_confidence"),
        evidence_level=kw.get("evidence_level"),
        assessment_notes=kw.get("assessment_notes"),
    )


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})

    if fn == "chars_markdown":
        return format_study_characteristics_markdown(chars(args.get("overrides")))
    if fn == "chars_html":
        return format_study_characteristics_html(chars(args.get("overrides")))
    if fn == "rob_markdown":
        return format_risk_of_bias_markdown(rob(args.get("judgements")))
    if fn == "rob_html":
        return format_risk_of_bias_html(rob(args.get("judgements")))
    if fn == "complete_markdown":
        return format_complete_assessment_markdown(
            assessment(args.get("overrides"), args.get("judgements"), **args.get("kwargs", {}))
        )
    if fn == "multiple_markdown":
        n = args.get("count", 2)
        return format_multiple_assessments_markdown(
            [assessment({"study_id": f"Study {i + 1}"}) for i in range(n)],
            title=args.get("title", "Characteristics of included studies"),
        )
    if fn == "summary_markdown":
        n = args.get("count", 2)
        return format_risk_of_bias_summary_markdown(
            [assessment({"study_id": f"Study {i + 1}"}) for i in range(n)]
        )
    if fn == "summary_empty":
        return format_risk_of_bias_summary_markdown([])
    if fn == "css":
        return get_cochrane_css()
    if fn == "css_len":
        return {"len": len(COCHRANE_CSS), "lines": COCHRANE_CSS.count("\n")}
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
