#!/usr/bin/env python3
"""Dump `_is_industry_funder` over the hand-labelled corpus, for the Rust port.

The corpus is the pre-existing `tests/data/funder_names.json` (417 names, hand
labelled for issue #36) rather than a corpus written for this test. That matters:
the names were sampled live from CrossRef and PubMed and labelled by a person, so
they are not the port author's idea of what a funder name looks like — which is
the property a parser cannot get from fixtures it wrote itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bmlib.transparency.analyzer import _is_industry_funder

CORPUS = Path(__file__).resolve().parents[2] / "tests" / "data" / "funder_names.json"


def main() -> int:
    entries = json.loads(CORPUS.read_text())["entries"]
    out = []
    for entry in entries:
        try:
            value = _is_industry_funder(entry["name"])
            ok, error = True, None
        except Exception as exc:  # noqa: BLE001
            value, ok, error = None, False, f"{type(exc).__name__}: {exc}"
        out.append(
            {
                "name": entry["name"],
                "label": entry["label"],
                "python": value,
                "ok": ok,
                "error": error,
            }
        )
    json.dump(out, sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
