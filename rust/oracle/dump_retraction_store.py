#!/usr/bin/env python3
"""Dump `store_retraction_notices` / `lookup_retractions` against real SQLite.

Drives an **in-memory SQLite database** so both languages exercise the same SQL:
the store's upsert, the idempotent re-import, and the lookup's identifier
normalisation and ordering.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from bmlib.db import create_tables
from bmlib.publications.models import RetractionNotice
from bmlib.publications.retractions import lookup_retractions, store_retraction_notices
from bmlib.publications.schema import SCHEMA_SQL


def connect():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_tables(conn, SCHEMA_SQL)
    return conn


def notice(spec: dict) -> RetractionNotice:
    return RetractionNotice.from_dict(spec)


def run(case):
    conn = connect()
    steps = case["steps"]
    out = []
    for step in steps:
        kind = step["op"]
        if kind == "store":
            n = store_retraction_notices(conn, [notice(s) for s in step["notices"]])
            out.append({"op": "store", "processed": n})
        elif kind == "lookup":
            rows = lookup_retractions(conn, doi=step.get("doi"), pmid=step.get("pmid"))
            out.append({"op": "lookup", "notices": [r.to_dict() for r in rows]})
        elif kind == "count":
            cur = conn.execute("SELECT COUNT(*) FROM retraction_notices")
            out.append({"op": "count", "rows": cur.fetchone()[0]})
        else:
            raise ValueError(f"unknown op {kind!r}")
    conn.close()
    return out


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
    json.dump(results, sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
