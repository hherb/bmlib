#!/usr/bin/env python3
"""Dump bmlib's per-part decisions, for the Rust port.

The decisions are read out of `_fetch_partitioned`'s own source-level predicates
rather than re-implemented, so this oracle cannot drift from the code it pins:

* skip-vs-refetch is `prior is not None and prior.promised == part.promised`;
* the plan reconcile is `reconcile_delivery(..., delivered=part_count,
  promised=part.promised, stalled=False)`;
* the checkpoint condition is `not (plan_note or walk_note)`.
"""

from __future__ import annotations

import json
import sys

from bmlib.publications.fetchers._reconcile import reconcile_delivery


def part_step(planned, checkpoint):
    """The skip/refetch decision, as `_fetch_partitioned` spells it."""
    if checkpoint is not None and checkpoint == planned:
        return {"step": "skip", "credited": checkpoint}
    if checkpoint is not None:
        return {"step": "refetch", "was": checkpoint, "now": planned}
    return {"step": "walk"}


def plan_verdict(part_count, planned):
    v = reconcile_delivery("pubmed", "2024-06-10 part edat:x:y",
                           delivered=part_count, promised=planned, stalled=False)
    return {"failure": v.failure, "note": v.note}


def may_checkpoint(plan_noted, walk_noted):
    return not (plan_noted or walk_noted)


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "part_step":
        return part_step(a["planned"], a.get("checkpoint"))
    if fn == "plan_verdict":
        return plan_verdict(a["part_count"], a["planned"])
    if fn == "may_checkpoint":
        return may_checkpoint(a["plan_noted"], a["walk_noted"])
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
