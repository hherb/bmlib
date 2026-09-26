#!/usr/bin/env python3
"""Dump bmlib's PDF text assembly, for the Rust port.

The pure half of `pdf_converter.py`: span weighting, line collapse, furniture
detection, paragraph reflow and the HTML render. The backend needs a PDF and is
not exercised here.
"""

from __future__ import annotations

import json
import sys

from bmlib.fulltext.pdf_converter import (
    ConversionResult,
    _group_paragraphs,
    _line_to_block,
    _normalize,
    _repeated_lines,
    _span_text_weight,
    _split_on_short_lines,
    render_html,
)


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "normalize":
        return _normalize(a["line"])
    if fn == "span_text_weight":
        return _span_text_weight(a["span"])
    if fn == "line_to_block":
        block = _line_to_block(a["line"], a["page_num"])
        if block is None:
            return None
        return {
            "text": block.text,
            "page_num": block.page_num,
            "font_size": block.font_size,
            "font_name": block.font_name,
            "is_bold": block.is_bold,
            "is_italic": block.is_italic,
            "x": block.x,
            "y": block.y,
            "width": block.width,
            "height": block.height,
        }
    if fn == "repeated_lines":
        return sorted(_repeated_lines(a["pages"]))
    if fn == "split_on_short_lines":
        return _split_on_short_lines(a["lines"], a["break_below"])
    if fn == "group_paragraphs":
        return _group_paragraphs(a["lines"])
    if fn == "render_html":
        result = ConversionResult(
            success=a["success"],
            text=a["text"],
            format=a.get("format", "plaintext"),
            page_count=a.get("page_count", 1),
            converted_pages=a.get("converted_pages", 1),
            char_count=len(a["text"]),
            page_texts=a.get("page_texts", []),
        )
        return render_html(result)
    if fn == "is_complete":
        return ConversionResult(
            success=a["success"],
            text="",
            format="plaintext",
            page_count=a["page_count"],
            converted_pages=a["converted_pages"],
            char_count=a["char_count"],
        ).is_complete
    if fn == "completion_ratio":
        return ConversionResult(
            success=True,
            text="",
            format="plaintext",
            page_count=a["page_count"],
            converted_pages=a["converted_pages"],
            char_count=0,
        ).completion_ratio
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
