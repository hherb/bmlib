#!/usr/bin/env python3
"""Dump bmlib's JATS text primitives, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.fulltext.jats_parser import (
    _delimiter_pair,
    _elocation_part_continues,
    _latex_expression,
    _normalize_whitespace,
    _pad_as_deposited,
    _pad_row,
    _render_formula,
    _without_whitespace,
)


class Frame:
    """The two `_FormulaFrame` fields `_render_formula` reads beyond its args."""

    def __init__(self, latex, display, alt_text, label):
        self.latex = latex
        self.display = display
        self.alt_text = alt_text
        self.label = label


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "without_whitespace":
        return _without_whitespace(a["text"])
    if fn == "normalize_whitespace":
        return _normalize_whitespace(a["text"])
    if fn == "elocation_part_continues":
        return _elocation_part_continues(a["buffer"], a["joined"], a["citation_element"])
    if fn == "delimiter_pair":
        pair = _delimiter_pair(a["body"])
        return list(pair) if pair else None
    if fn == "latex_expression":
        return _latex_expression(a["deposit"], a["display"])
    if fn == "pad_as_deposited":
        return _pad_as_deposited(a["rendered"], a["buffered"], a["display"])
    if fn == "render_formula":
        return _render_formula(
            Frame(a["latex"], a["display"], a.get("alt_text", ""), a.get("label", "")),
            a["buffered"],
            numbered=a.get("numbered", False),
        )
    if fn == "pad_row":
        return _pad_row(a["row"], a["count"])
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
