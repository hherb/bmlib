#!/usr/bin/env python3
"""Dump bmlib's cache-naming rules, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.fulltext.cache import _MAX_PREFIX_CHARS, _safe_filename, sanitize_identifier


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "sanitize_identifier":
        return sanitize_identifier(a["raw"])
    if fn == "safe_filename":
        return _safe_filename(a["identifier"])
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    tables = {"MAX_PREFIX_CHARS": _MAX_PREFIX_CHARS}
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(
        {"tables": tables, "cases": out}, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
