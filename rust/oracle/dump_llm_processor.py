#!/usr/bin/env python3
"""Dump bmlib's LLM chunk processor's pure half, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.context_processor.data_types import ConsolidatedItem
from bmlib.context_processor.llm_processor import (
    DEFAULT_EXTRACTION_CONFIDENCE,
    LLMChunkProcessor,
    _is_scored_chunk,
)

# `_validate_template` is a `@staticmethod` on the class, so it is reached through
# it rather than imported as a module function.
_validate_template = LLMChunkProcessor._validate_template


def render(template, query, content):
    return LLMChunkProcessor._render(template, query, content)


def format_item(item, index):
    proc = LLMChunkProcessor.__new__(LLMChunkProcessor)
    return LLMChunkProcessor.format_item(proc, item, index)


def format_consolidated(item, index):
    proc = LLMChunkProcessor.__new__(LLMChunkProcessor)
    return LLMChunkProcessor.format_consolidated_item(
        proc, ConsolidatedItem(content=item["content"], metadata=item.get("metadata", {})), index
    )


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "is_scored_chunk":
        return _is_scored_chunk(a["item"])
    if fn == "render":
        return render(a["template"], a["query"], a["content"])
    if fn == "validate_template":
        try:
            _validate_template(a["template"], a["name"])
            return None
        except ValueError as exc:
            return str(exc)
    if fn == "format_item":
        # A **JSON array is not a tuple**, so a case marked `as_tuple` must be
        # reconstructed as one before the call — otherwise the predicate is false
        # for the case that exists to exercise it, and the oracle measures its own
        # harness.
        item = a["item"]
        if case.get("as_tuple") and isinstance(item, list):
            item = tuple(item)
        return format_item(item, a["index"])
    if fn == "format_consolidated_item":
        return format_consolidated(a["item"], a["index"])
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    tables = {"DEFAULT_EXTRACTION_CONFIDENCE": DEFAULT_EXTRACTION_CONFIDENCE}
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump({"tables": tables, "cases": out}, sys.stdout,
              indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
