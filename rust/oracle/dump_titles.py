#!/usr/bin/env python3
"""Dump bmlib's PDF-title corroboration, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.fulltext._titles import (
    _page_text_for_matching,
    accepted_metadata_title,
    looks_like_junk,
    normalise,
)


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "normalise":
        return normalise(a["text"])
    if fn == "page_text_for_matching":
        return _page_text_for_matching(a["text"])
    if fn == "looks_like_junk":
        return looks_like_junk(a["title"])
    if fn == "accepted_metadata_title":
        return accepted_metadata_title(a.get("metadata", {}), a.get("page_one_text"))
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
