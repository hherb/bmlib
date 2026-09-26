#!/usr/bin/env python3
"""Dump bmlib's Cochrane assessor parser, for the Rust port.

    .venv/bin/python rust/oracle/dump_cochrane_assessor.py \
        < rust/oracle/cochrane_assessor_cases.json

`CochraneAssessor._parse_assessment` is called on a bare object — it touches no
instance state — so the corpus pins the reading rules without a model or a
network. `render_*` cases pin the tiers' user prompts as `str.format` renders
them, doubled braces collapsed and a substituted value never re-scanned. Cases
carrying a `corrected` block are places where the port deliberately narrows a
field to its annotated type (a list of notes keeps only strings,
`evidence_level` is read only as a string); the block carries the port's value,
so the corpus can hold the Python's side of it too.

`created_at` is stamped with `now()` by the characteristics dataclass, so it is
pinned to `None` before serialising — the same determinism measure
`dump_cochrane.py` takes.
"""

from __future__ import annotations

import json
import sys

from bmlib.quality.cochrane_assessor import (
    CONDENSE_CONSOLIDATION_PROMPT,
    CONDENSE_EXTRACTION_PROMPT,
    CochraneAssessor,
)
from bmlib.quality.quality_agent import ASSESSMENT_USER_TEMPLATE
from bmlib.quality.study_classifier import CLASSIFIER_USER_TEMPLATE


def cochrane_parse(case: dict) -> dict:
    args = case["args"]
    agent = CochraneAssessor.__new__(CochraneAssessor)
    assessment = CochraneAssessor._parse_assessment(
        agent,
        args["data"],
        args.get("notes") or [],
        args.get("condensed_from"),
        args.get("condensation_status"),
    )
    assessment.study_characteristics.created_at = None
    return assessment.to_dict()


def run(case: dict) -> object:
    fn = case["fn"]
    args = case["args"]
    if fn == "cochrane_parse":
        return cochrane_parse(case)
    # The three tiers' user prompts, rendered exactly as the tiers render them.
    # Formatting is all these cases pin; the tiers' own guards are separate.
    if fn == "render_tier2":
        return CLASSIFIER_USER_TEMPLATE.format(title=args["title"], abstract=args["abstract"])
    if fn == "render_tier3":
        return ASSESSMENT_USER_TEMPLATE.format(title=args["title"], abstract=args["abstract"])
    if fn == "render_condense_extraction":
        return CONDENSE_EXTRACTION_PROMPT.format(query=args["query"], content=args["content"])
    if fn == "render_condense_consolidation":
        return CONDENSE_CONSOLIDATION_PROMPT.format(query=args["query"], content=args["content"])
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
