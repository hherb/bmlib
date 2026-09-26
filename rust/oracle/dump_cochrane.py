#!/usr/bin/env python3
"""Dump bmlib's Cochrane models as JSON, for the Rust port.

    .venv/bin/python rust/oracle/dump_cochrane.py < rust/oracle/cochrane_cases.json
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.cochrane_models import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
    collapse_risk_of_bias,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
)


def rob_from_spec(spec):
    """Build a nine-domain RoB from a per-domain judgement map."""
    rob = create_default_cochrane_risk_of_bias()
    for name, judgement in (spec or {}).items():
        getattr(rob, name).judgement = judgement
    return rob


def study_chars(overrides=None):
    o = overrides or {}
    ch = CochraneStudyCharacteristics(
        study_id=o.get("study_id", "Andrei 2011"),
        methods=o.get("methods", "Parallel randomised trial"),
        participants=CochraneParticipants(
            setting=o.get("setting", "Romania"),
            population=o.get("population", "Chronic heart failure"),
            total_participants=o.get("total_participants"),
            group_sizes=o.get("group_sizes"),
            baseline_characteristics_reported=o.get("baseline_characteristics_reported", False),
        ),
        interventions=CochraneInterventions(
            description=o.get("intervention_description", "Hospital at home"),
            intervention_groups=o.get("intervention_groups"),
            control_description=o.get("control_description"),
            duration=o.get("duration"),
            setting=o.get("intervention_setting"),
        ),
        outcomes=CochraneOutcomes(
            description=o.get("outcomes_description", "Mortality, cost"),
            primary_outcomes=o.get("primary_outcomes"),
            secondary_outcomes=o.get("secondary_outcomes"),
            outcome_timepoints=o.get("outcome_timepoints"),
            outcome_assessment_methods=o.get("outcome_assessment_methods"),
        ),
        notes=CochraneNotes(
            follow_up_periods=o.get("follow_up_periods"),
            funding_source=o.get("funding_source"),
            conflicts_of_interest=o.get("conflicts_of_interest"),
            ethical_approval=o.get("ethical_approval"),
            trial_registration=o.get("trial_registration"),
            publication_status=o.get("publication_status"),
            additional_notes=o.get("additional_notes"),
        ),
        document_id=o.get("document_id"),
        document_title=o.get("document_title"),
        pmid=o.get("pmid"),
        doi=o.get("doi"),
    )
    # `created_at` is stamped with now; pin it so the oracle is deterministic.
    ch.created_at = None
    return ch


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})

    if fn == "judgement_from_string":
        return RiskOfBiasJudgement.from_string(args["value"]).value
    if fn == "default_item":
        return create_default_risk_of_bias_item(
            args.get("domain", "D"), args.get("bias_type", "selection bias"),
            args.get("outcome_type"),
        ).to_dict()
    if fn == "default_rob":
        return create_default_cochrane_risk_of_bias().to_dict()
    if fn == "rob_to_list":
        return [i.to_dict() for i in rob_from_spec(args.get("judgements")).to_list()]
    if fn == "rob_summary_counts":
        return rob_from_spec(args.get("judgements")).get_summary_counts()
    if fn == "rob_roundtrip":
        return create_default_cochrane_risk_of_bias().to_dict()
    if fn == "item_to_dict_omits_empty_outcome_type":
        return RiskOfBiasItem(
            domain="D", bias_type="selection bias", judgement="Low risk",
            support_for_judgement="s", outcome_type=args.get("outcome_type"),
        ).to_dict()
    if fn == "participants_format":
        return study_chars(args.get("overrides")).participants.format_for_table()
    if fn == "notes_format":
        return study_chars(args.get("overrides")).notes.format_for_table()
    if fn == "characteristics_to_dict":
        return study_chars(args.get("overrides")).to_dict()
    if fn == "characteristics_roundtrip":
        d = study_chars(args.get("overrides")).to_dict()
        out = CochraneStudyCharacteristics.from_dict(d).to_dict()
        # `from_dict` re-stamps `created_at` from a live clock when the input's
        # was null, so the stamp is dropped: comparing two wall-clock readings
        # in different processes would fail for a reason unrelated to the port.
        # That `from_dict` re-stamps at all is Python's behaviour and is not
        # modelled here — see the port's `from_json`, which preserves whatever
        # was there.
        out.pop("created_at", None)
        return out
    if fn == "assessment_to_dict":
        a = CochraneStudyAssessment(
            study_characteristics=study_chars(args.get("overrides")),
            risk_of_bias=rob_from_spec(args.get("judgements")),
            overall_quality_score=args.get("overall_quality_score"),
            overall_confidence=args.get("overall_confidence"),
            evidence_level=args.get("evidence_level"),
            assessment_notes=args.get("assessment_notes"),
            condensed_from_chars=args.get("condensed_from_chars"),
            condensation_status=args.get("condensation_status"),
        )
        return a.to_dict()
    if fn == "assessment_study_id":
        a = CochraneStudyAssessment(
            study_characteristics=study_chars(args.get("overrides")),
            risk_of_bias=rob_from_spec(args.get("judgements")),
        )
        return {"study_id": a.study_id, "document_id": a.document_id}
    if fn == "collapse":
        try:
            return {"ok": True, "value": collapse_risk_of_bias(rob_from_spec(args["judgements"])).to_dict()}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    if fn == "collapse_custom_types":
        rob = create_default_cochrane_risk_of_bias()
        for name, bt in args["bias_types"].items():
            getattr(rob, name).bias_type = bt
        try:
            return {"ok": True, "value": collapse_risk_of_bias(rob).to_dict()}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
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
