#!/usr/bin/env python3
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License

"""Dump bmlib.citations.passage_match's behaviour as JSON, for the Rust port to be diffed against.

It reads a JSON list of cases on stdin and writes a JSON list of results on
stdout. Each case names one public function and its arguments (named as in
Python); the result is whatever that function returned, normalised so a diff is
readable.

The corpus keeps a single copy of its cases, in ``rust/bmlib/tests/data/``,
which this dumper and the Rust test both read.

Run from the repository root, with the Python library importable:

    .venv/bin/python rust/oracle/dump_passage_match.py \\
        < rust/bmlib/tests/data/passage_match_cases.json
"""

from __future__ import annotations

import json
import sys
import unicodedata

from bmlib.citations.passage_match import (
    find_passage,
    fits_text,
    max_edit_points,
    normalize_with_offsets,
    occurs_once,
)

# Rust's Unicode tables are newer than Python's, and differ only on code points
# Python treats as unassigned; a case containing one would test the tables, not
# the port.
UNASSIGNED = "Cn"


def refuse_unassigned(case: dict) -> None:
    """Raise if any string argument holds a code point Python has not assigned."""
    for value in case.get("args", {}).values():
        if isinstance(value, str):
            for char in value:
                if unicodedata.category(char) == UNASSIGNED:
                    raise ValueError(
                        f"case {case['name']!r} holds unassigned code point U+{ord(char):04X}"
                    )


def run(case: dict):
    fn = case["fn"]
    args = case["args"]
    if fn == "find_passage":
        m = find_passage(args["passage"], args["text"], args["min_similarity"])
        return {
            "similarity": m.similarity,
            "start": m.start,
            "end": m.end,
            "text": m.text,
            "exact": m.exact,
        }
    if fn == "normalize_with_offsets":
        norm, offsets = normalize_with_offsets(args["text"])
        return [norm, offsets]
    if fn == "occurs_once":
        return occurs_once(args["fragment"], args["text"])
    if fn == "fits_text":
        return fits_text(args["passage_length"], args["text_length"], args["min_similarity"])
    if fn == "max_edit_points":
        return max_edit_points(args["passage_length"], args["min_similarity"])
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        refuse_unassigned(case)
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001 - the oracle records failures too
            results.append(
                {"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
