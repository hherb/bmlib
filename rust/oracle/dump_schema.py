#!/usr/bin/env python3
"""Dump bmlib's publications DDL, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.publications.schema import (
    SCHEMA_SQL,
    SCHEMA_SQL_POSTGRESQL,
    _ADDED_COLUMNS,
)


def main() -> int:
    out = {
        "sqlite": SCHEMA_SQL,
        "postgresql": SCHEMA_SQL_POSTGRESQL,
        "added_columns": [
            [table, name, col_type]
            for table, cols in _ADDED_COLUMNS.items()
            for name, col_type in cols
        ],
        "sqlite_statements": SCHEMA_SQL.count(";"),
        "postgresql_statements": SCHEMA_SQL_POSTGRESQL.count(";"),
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
