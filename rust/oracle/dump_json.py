#!/usr/bin/env python3
"""Dump bmlib's JSON repair and span-location behaviour as JSON, for the Rust port.

The third differential oracle. Reads a JSON list of cases on stdin and writes a
JSON list of results on stdout.

    .venv/bin/python rust/oracle/dump_json.py < rust/oracle/json_cases.json
"""

from __future__ import annotations

import json
import sys

from bmlib.llm.json_repair import (
    JSONRepairError,
    extract_and_repair_json,
    repair_json,
    safe_json_loads,
    salvage_json_fields,
)
from bmlib.llm.utils import extract_json, iter_json_spans

MAX = 3


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})

    if fn == "repair_json":
        try:
            return {"ok": True, "value": repair_json(args["text"])}
        except (JSONRepairError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "type": type(exc).__name__}

    if fn == "safe_json_loads":
        try:
            v = safe_json_loads(args["text"], args.get("repair", True))
            return {"ok": True, "value": v}
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "type": type(exc).__name__}

    if fn == "extract_and_repair_json":
        try:
            text, repaired = extract_and_repair_json(args["text"], args.get("repair", True))
            return {"ok": True, "text": text, "repaired": repaired}
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "type": type(exc).__name__}

    if fn == "extract_json":
        return extract_json(args["text"], allow_fragments=args.get("allow_fragments", True))

    if fn == "iter_json_spans":
        return list(iter_json_spans(args["text"], nested_objects=args.get("nested_objects", True)))

    if fn == "salvage_json_fields":
        return salvage_json_fields(args["text"], args["keys"])

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
