#!/usr/bin/env python3
"""Dump bmlib's retraction rules, for the Rust port."""

from __future__ import annotations

import io
import json
import sys

from bmlib.publications.models import RetractionNature, RetractionNotice
from bmlib.publications.retractions import (
    _DOI_COLUMNS,
    _JOURNAL_COLUMNS,
    _NATURE_COLUMNS,
    _NOTICE_DOI_COLUMNS,
    _NOTICE_PMID_COLUMNS,
    _ORIGINAL_DATE_COLUMNS,
    _PMID_COLUMNS,
    _REASON_COLUMNS,
    _RECORD_ID_COLUMNS,
    _RETRACTION_DATE_COLUMNS,
    _TITLE_COLUMNS,
    _clean_identifier,
    _find_column,
    _parse_date,
    _split_reasons,
    is_retracted,
    parse_retraction_watch_csv,
)

HEADER = (
    "Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,"
    "ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,"
    "OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n"
)


def row(
    record_id="1",
    retraction_date="3/9/2026 0:00",
    retraction_doi="10.1/notice",
    retraction_pmid="87654321",
    original_date="5/6/2023 0:00",
    original_doi="10.1/paper",
    original_pmid="12345678",
    nature="Retraction",
    reason="Rogue Editor;",
    title="A paper",
    journal="Soft Computing",
):
    return (
        f"{record_id},{title},Subject,Inst,{journal},Pub,AU,Author,URL,Article,"
        f"{retraction_date},{retraction_doi},{retraction_pmid},{original_date},"
        f"{original_doi},{original_pmid},{nature},{reason},No,Notes,\n"
    )


def csv_bytes(*rows, encoding="utf-8"):
    return (HEADER + "".join(rows)).encode(encoding)


def parse(case_bytes):
    skipped = []
    notices = list(
        parse_retraction_watch_csv(
            io.BytesIO(case_bytes), on_skip=lambda n, why: skipped.append([n, why])
        )
    )
    # Drop the created/updated stamps, which are wall-clock on both sides:
    # `RetractionNotice` has none, but `to_dict` carries the enum spelling.
    return {"notices": [n.to_dict() for n in notices], "skipped": skipped}


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "clean_identifier":
        return _clean_identifier(a.get("value"))
    if fn == "split_reasons":
        return _split_reasons(a.get("value"))
    if fn == "parse_date":
        return _parse_date(a.get("value"))
    if fn == "columns":
        return {
            "record_id": list(_RECORD_ID_COLUMNS),
            "doi": list(_DOI_COLUMNS),
            "pmid": list(_PMID_COLUMNS),
            "notice_doi": list(_NOTICE_DOI_COLUMNS),
            "notice_pmid": list(_NOTICE_PMID_COLUMNS),
            "nature": list(_NATURE_COLUMNS),
            "reason": list(_REASON_COLUMNS),
            "title": list(_TITLE_COLUMNS),
            "journal": list(_JOURNAL_COLUMNS),
            "retraction_date": list(_RETRACTION_DATE_COLUMNS),
            "original_date": list(_ORIGINAL_DATE_COLUMNS),
        }
    if fn == "find_column":
        return _find_column(a["row"], tuple(a["candidates"]))
    if fn == "is_retracted":
        # `RetractionNotice` is a plain dataclass and does NOT coerce `nature`,
        # so passing the corpus's string spelling straight in leaves
        # `is_retracted` comparing a `str` against an enum member with `is` --
        # which is False for every notice, making it answer "not retracted" for
        # everything. The real pipeline goes through `_row_to_notice`, which
        # sets a `RetractionNature`; the oracle has to do the same or it
        # measures a state the library never produces. Found by diffing a case
        # whose documented answer is `true`.
        notices = []
        for n in a["notices"]:
            data = dict(n)
            data["nature"] = RetractionNature(data["nature"])
            notices.append(RetractionNotice(**data))
        return is_retracted(notices)
    if fn == "parse":
        return parse(bytes(a["csv"], "utf-8"))
    if fn == "parse_rows":
        return parse(csv_bytes(*[row(**r) for r in a["rows"]]))
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
