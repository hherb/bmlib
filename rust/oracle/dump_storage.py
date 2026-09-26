#!/usr/bin/env python3
"""Dump bmlib's publication storage rules, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.publications.storage import (
    _DOI_PREFIXES,
    _normalize_doi,
    _normalize_pmid,
)


def merge_sources(existing, incoming):
    """The rule `_merge_publication` implements inline for sources."""
    merged = list(existing)
    for src in incoming:
        if src not in merged:
            merged.append(src)
    return merged


def merge_json_list(existing, incoming):
    """The rule `_merge_publication` implements inline for the three JSON lists."""
    if not existing or existing == "[]":
        return json.dumps(incoming)
    return existing


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "normalize_doi":
        return _normalize_doi(a.get("value"))
    if fn == "normalize_pmid":
        return _normalize_pmid(a.get("value"))
    if fn == "prefixes":
        return list(_DOI_PREFIXES)
    if fn == "merge_sources":
        return merge_sources(a["existing"], a["incoming"])
    if fn == "merge_json_list":
        return merge_json_list(a.get("existing"), a["incoming"])
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
