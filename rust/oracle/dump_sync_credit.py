#!/usr/bin/env python3
"""Dump bmlib's per-day credit and count rules, for the Rust port.

The rules are spelled as `sync()`'s own loop spells them, so this oracle cannot
drift from the code it pins:

* the carried credit is
  `sum(cp.record_count for key, cp in prior_parts.items() if key in skipped_keys)`;
* the day's stored count is `day_added + day_merged + carried`;
* a failed day reports `day_added + day_merged + day_failed + len(day_records)`.
"""

from __future__ import annotations

import json
import sys


def carried_credit(prior_parts, skipped_keys):
    """`sync()`'s own comprehension over `prior_parts`."""
    return sum(cp["record_count"] for key, cp in prior_parts.items() if key in skipped_keys)


def day_record_count(added, merged, carried):
    return added + merged + carried


def failed_record_count(added, merged, failed, buffered):
    return added + merged + failed + buffered


def day_error_line(source, date, error):
    return f"{source}/{date}: {error}"


def no_fetcher_line(source):
    return f"No fetcher found for source: {source}"


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "carried_credit":
        return carried_credit(a["prior_parts"], set(a["skipped_keys"]))
    if fn == "day_record_count":
        return day_record_count(a["added"], a["merged"], a["carried"])
    if fn == "failed_record_count":
        return failed_record_count(a["added"], a["merged"], a["failed"], a["buffered"])
    if fn == "day_error_line":
        return day_error_line(a["source"], a["date"], a["error"])
    if fn == "no_fetcher_line":
        return no_fetcher_line(a["source"])
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
