#!/usr/bin/env python3
"""Dump bmlib's fetcher shared rules, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.publications.fetchers._reconcile import (
    SHORTFALL_FAILURE_RATIO,
    reconcile_delivery,
)
from bmlib.publications.fetchers.registry import (
    list_sources,
    source_names,
)


def descriptor_to_dict(d):
    return {
        "name": d.name,
        "display_name": d.display_name,
        "description": d.description,
        "resumable": bool(d.resumable),
        "params": [
            {
                "name": p.name,
                "description": p.description,
                "required": bool(p.required),
                "default": p.default,
                "secret": bool(p.secret),
            }
            for p in d.params
        ],
    }


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "reconcile":
        r = reconcile_delivery(
            a["source"],
            a["date"],
            delivered=a["delivered"],
            promised=a.get("promised"),
            stalled=a.get("stalled", False),
        )
        return {"failure": r.failure, "note": r.note}
    if fn == "ratio":
        return SHORTFALL_FAILURE_RATIO
    if fn == "builtins":
        return [descriptor_to_dict(d) for d in sorted(list_sources(), key=lambda x: x.name)]
    if fn == "source_names":
        return sorted(source_names())
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
