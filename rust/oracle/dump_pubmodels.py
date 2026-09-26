#!/usr/bin/env python3
"""Dump bmlib's publication models as JSON, for the Rust port.

    .venv/bin/python rust/oracle/dump_pubmodels.py < rust/oracle/pubmodels_cases.json

`created_at`/`updated_at` are stamped with the wall clock by the models, so
every case that would carry one pins it first — otherwise the oracle compares
two readings from different processes and fails for a reason unrelated to the
port.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from bmlib.publications.models import (
    AuthorAffiliation,
    DownloadDay,
    FullTextSource,
    Grant,
    PartCheckpoint,
    Publication,
    RetractionNature,
    RetractionNotice,
    _require_count,
    _require_datetime,
    _require_text,
)

PINNED = "2024-01-02T03:04:05+00:00"


def jsonable(value):
    """ISO-format a datetime, so a validator's return value is comparable."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def validator(fn, value, **kw):
    """Run one validator and report either its value or its message."""
    try:
        return {"ok": True, "value": jsonable(fn(value, **kw))}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})

    if fn == "require_text":
        return validator(lambda v, **k: _require_text(v, k["field"]), args.get("value"), field=args["field"])
    if fn == "require_count":
        return validator(
            lambda v, **k: _require_count(v, k["field"], minimum=k["minimum"]),
            args.get("value"), field=args["field"], minimum=args["minimum"],
        )
    if fn == "require_datetime":
        return validator(lambda v, **k: _require_datetime(v, k["field"]), args.get("value"), field=args["field"])

    if fn == "publication_roundtrip":
        p = Publication.from_dict(args["data"])
        p.created_at = datetime.fromisoformat(PINNED)
        p.updated_at = datetime.fromisoformat(PINNED)
        return p.to_dict()
    if fn == "publication_stamps_now":
        p = Publication(title="T", sources=["pubmed"], first_seen_source="pubmed")
        return {
            "has_created_at": bool(p.created_at),
            "has_updated_at": bool(p.updated_at),
            "tz": str(p.created_at.tzinfo),
            "id": p.id, "pmcid": p.pmcid, "authors": p.authors,
            "is_open_access": p.is_open_access,
        }
    if fn == "fulltext_roundtrip":
        s = FullTextSource.from_dict(args["data"])
        s.created_at = datetime.fromisoformat(PINNED)
        return s.to_dict()
    if fn == "grant_roundtrip":
        return Grant.from_dict(args["data"]).to_dict()
    if fn == "affiliation_roundtrip":
        return AuthorAffiliation.from_dict(args["data"]).to_dict()
    if fn == "downloadday_roundtrip":
        # Recorded through the same `{ok, value}` envelope the validators use,
        # so the corpus has one shape for both a returned model and a rejected
        # input rather than two.
        return {"ok": True, "value": DownloadDay.from_dict(args["data"]).to_dict()}
    if fn == "part_checkpoint_new":
        return {"ok": True, "value": PartCheckpoint(**args["data"]).to_dict()}
    if fn == "part_checkpoint_roundtrip":
        return {"ok": True, "value": PartCheckpoint.from_dict(args["data"]).to_dict()}
    if fn == "retraction_nature_from_raw":
        return RetractionNature.from_raw(args.get("value")).value
    if fn == "retraction_nature_from_dict":
        return {"ok": True, "value": RetractionNature(args["value"]).value}
    if fn == "retraction_roundtrip":
        return {"ok": True, "value": RetractionNotice.from_dict(args["data"]).to_dict()}
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        if case.get("expect_error"):
            # The case's subject *is* the rejection, so the message is the
            # value. Recorded as a success carrying `{ok, error}` so the Rust
            # side can compare it like any other payload.
            try:
                run(case)
            except (ValueError, KeyError) as exc:
                # `str(KeyError("k"))` is `"'k'"`, but `args[0]` is the bare
                # key — which is what a caller reads off the exception and what
                # the Rust side reports.
                message = (
                    str(exc.args[0]) if isinstance(exc, KeyError) and exc.args
                    else str(exc)
                )
                results.append(
                    {"name": case["name"], "ok": True,
                     "value": {"ok": False, "error": message}}
                )
            else:
                results.append(
                    {"name": case["name"], "ok": False,
                     "error": "expected a ValueError and none was raised"}
                )
            continue
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
