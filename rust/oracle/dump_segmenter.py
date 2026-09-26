#!/usr/bin/env python3
"""Dump bmlib's PDF section segmenter, for the Rust port.

Blocks arrive as data, so the corpus drives the whole algorithm — heading
detection, classification and slicing — without a PDF.
"""

from __future__ import annotations

import json
import sys

from bmlib.fulltext.models import Section, TextBlock
from bmlib.fulltext.segmenter import (
    SectionSegmenter,
    _join_blocks,
    _median_font_size,
)

# `_match_section_type` and `_is_potential_header` are instance methods, and they
# read only the constructor's two thresholds — so a default instance serves.
_SEGMENTER = SectionSegmenter()


def block(spec: dict) -> TextBlock:
    return TextBlock(
        text=spec["text"], page_num=spec.get("page_num", 0),
        font_size=spec.get("font_size", 12.0), font_name=spec.get("font_name", "Body"),
        is_bold=spec.get("is_bold", False), is_italic=spec.get("is_italic", False),
        x=spec.get("x", 0.0), y=spec.get("y", 0.0),
        width=spec.get("width", 100.0), height=spec.get("height", 10.0),
    )


def render_section(section: Section) -> dict:
    return {
        "section_type": section.section_type.value,
        "title": section.title,
        "content": section.content,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "confidence": section.confidence,
        "subsections": [render_section(s) for s in section.subsections],
    }


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "median_font_size":
        return _median_font_size([block(b) for b in a["blocks"]])
    if fn == "join_blocks":
        return _join_blocks([block(b) for b in a["blocks"]])
    if fn == "match_section_type":
        section_type, confidence = _SEGMENTER._match_section_type(a["text"])
        return {"section_type": section_type.value, "confidence": confidence}
    if fn == "is_potential_header":
        return _SEGMENTER._is_potential_header(
            block(a["block"]), a["median_font_size"]
        )
    if fn == "segment_document":
        seg = _SEGMENTER
        doc = seg.segment_document([block(b) for b in a["blocks"]],
                                   a.get("metadata"))
        return {
            "file_path": doc.file_path,
            "title": doc.title,
            "sections": [render_section(s) for s in doc.sections],
        }
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"[:200]})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
