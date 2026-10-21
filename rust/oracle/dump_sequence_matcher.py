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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Dump difflib.SequenceMatcher's behaviour as JSON, for the Rust port to be diffed against.

``bmlib::citations::sequence_matcher`` ports ``SequenceMatcher(None, a, b,
autojunk=False)``, the configuration ``bmlib.citations.passage_match`` uses.
This dumper reads a JSON list of ``{"name", "a", "b"}`` cases on stdin and
writes, for each, the matching blocks (with difflib's closing ``(len(a),
len(b), 0)`` sentinel) and the three ratios.

The corpus keeps a single copy of its cases, in ``rust/bmlib/tests/data/``,
which this dumper and the crate's unit test both read.

Run from the repository root:

    .venv/bin/python rust/oracle/dump_sequence_matcher.py \\
        < rust/bmlib/tests/data/sequence_matcher_cases.json
"""

from __future__ import annotations

import json
import sys
from difflib import SequenceMatcher


def run(case: dict) -> dict:
    matcher = SequenceMatcher(None, case["a"], case["b"], autojunk=False)
    return {
        "blocks": [list(block) for block in matcher.get_matching_blocks()],
        "ratio": matcher.ratio(),
        "quick_ratio": matcher.quick_ratio(),
        "real_quick_ratio": matcher.real_quick_ratio(),
    }


def main() -> int:
    cases = json.load(sys.stdin)
    results = [{"name": case["name"], "ok": True, "value": run(case)} for case in cases]
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
