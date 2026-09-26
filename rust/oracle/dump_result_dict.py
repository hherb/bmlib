#!/usr/bin/env python3
"""Dump `TransparencyResult.to_dict`/`from_dict`, for the Rust port."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from bmlib.transparency.models import (
    FullTextStatus,
    TransparencyResult,
    TransparencyRisk,
    TransparencyUnknownReason,
    TrialResultsStatus,
)

#: The instant the corpus pins, so `analyzed_at` is a value and not a clock read.
FIXED = datetime(2026, 9, 26, 10, 30, 0, tzinfo=UTC)


def build(spec: dict) -> TransparencyResult:
    """A result from a spec, with `analyzed_at` pinned."""
    fields = dict(spec)
    # **Overridden, not defaulted**: the corpus carries the instant as JSON text
    # so the expected output is pinnable, and `to_dict` calls `.isoformat()` on a
    # real datetime. Using `setdefault` here would leave the string in place and
    # the harness would fail rather than the port.
    fields["analyzed_at"] = FIXED
    for key, enum in (
        ("risk_level", TransparencyRisk),
        ("unknown_reason", TransparencyUnknownReason),
        ("full_text_status", FullTextStatus),
        ("trial_results_status", TrialResultsStatus),
    ):
        if fields.get(key) is not None:
            fields[key] = enum(fields[key])
    return TransparencyResult(**fields)


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "to_dict":
        return build(a["result"]).to_dict()
    if fn == "round_trip":
        # Serialise, read back, serialise again: the second dict must equal the
        # first, which is the property a persisted row depends on.
        first = build(a["result"]).to_dict()
        again = TransparencyResult.from_dict(first).to_dict()
        return {"first": first, "again": again}
    if fn == "from_dict":
        out = TransparencyResult.from_dict(a["data"]).to_dict()
        if not a["data"].get("analyzed_at"):
            # **Pinned to a placeholder so regeneration is byte-stable.** An absent
            # timestamp is a clock read in both languages, so the real value would
            # differ on every run and a committed expectation could never match.
            # The case marks the field `volatile` and the Rust test owns the rule.
            out["analyzed_at"] = "<now>"
        return out
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
