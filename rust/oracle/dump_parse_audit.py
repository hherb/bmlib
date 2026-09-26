#!/usr/bin/env python3
"""Dump bmlib's parse-unwind audit, for the Rust port.

Every field is exercised **alone** and then in combinations, so a message that
drifts and a message that fires on the wrong field are both caught. The field
list is read off the dataclass, so a field added later appears in the corpus
without this script being edited.
"""

from __future__ import annotations

import dataclasses
import json
import sys

from bmlib.fulltext._parse_audit import ParseUnwindState, unwind_diagnostics

INT_FIELDS = [
    f.name
    for f in dataclasses.fields(ParseUnwindState)
    if f.type in ("int",) or (isinstance(f.type, str) and f.type == "int")
]
TUPLE_FIELDS = ["open_elements", "stuck_flags"]


def render(state: ParseUnwindState) -> dict:
    return {
        "nested_article_depth": state.nested_article_depth,
        "open_sections": state.open_sections,
        "open_figures": state.open_figures,
        "open_tables": state.open_tables,
        "open_captions": state.open_captions,
        "open_formulas": state.open_formulas,
        "open_contrib_groups": state.open_contrib_groups,
        "open_contribs": state.open_contribs,
        "open_definition_items": state.open_definition_items,
        "open_award_groups": state.open_award_groups,
        "open_funder_named_content": state.open_funder_named_content,
        "open_container_headings": state.open_container_headings,
        "unfilled_author_slots": state.unfilled_author_slots,
        "unfilled_figure_slots": state.unfilled_figure_slots,
        "unfilled_table_slots": state.unfilled_table_slots,
        "excess_text_buffers": state.excess_text_buffers,
        "open_elements": list(state.open_elements),
        "stuck_flags": list(state.stuck_flags),
    }


def run(case):
    kwargs = dict(case["args"]["state"])
    if "open_elements" in kwargs:
        kwargs["open_elements"] = tuple(kwargs["open_elements"])
    if "stuck_flags" in kwargs:
        kwargs["stuck_flags"] = tuple(kwargs["stuck_flags"])
    return unwind_diagnostics(ParseUnwindState(**kwargs))


def main() -> int:
    payload = {"int_fields": INT_FIELDS, "tuple_fields": TUPLE_FIELDS}
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump({"fields": payload, "cases": out}, sys.stdout,
              indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
