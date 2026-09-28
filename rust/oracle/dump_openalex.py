#!/usr/bin/env python3
"""Dump bmlib's OpenAlex fetch behaviour, for the Rust port."""

from __future__ import annotations

import json
import sys
from datetime import date

from _oracle import named_exception, response_marker

import bmlib.publications.fetchers.openalex as oa


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            # Python's own exception name, and a **deterministic** message on
            # purpose: httpx's is version-specific (on 0.28.1 a 500 reads
            # `Server error '500 Internal Server Error' for url '<url>'` followed
            # by an MDN link), so pinning the corpus to it would redden the oracle
            # on an httpx upgrade for a cosmetic reason. The port states the URL and
            # status itself, which the case's `corrected` block records (#349).
            raise named_exception(f"HTTPStatusError: HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def get(self, url, params=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        payload = (
            self._payloads.pop(0)
            if self._payloads
            else {"results": [], "meta": {"count": 0, "next_cursor": None}}
        )
        marker = response_marker(payload)
        if marker is None:
            return FakeResponse(payload)
        if marker[0] == "transport_error":
            # No response at all: the request never arrived, so `get` raises
            # rather than answering. The name is httpx's subclass where the port
            # has one variant and names the base class — the `corrected` block's
            # half of the divergence (#361).
            raise named_exception(f"{marker[1]}: {marker[2]}")
        return FakeResponse(marker[1], marker[2])


def normalize(raw):
    r = oa._normalize(raw)
    return {
        "title": r.title,
        "source": r.source,
        "doi": r.doi,
        "pmid": r.pmid,
        "abstract": r.abstract,
        "authors": r.authors,
        "journal": r.journal,
        "publication_date": r.publication_date,
        "keywords": r.keywords,
        "publication_types": r.publication_types,
        "is_open_access": r.is_open_access,
        "license": r.license,
        "fulltext_sources": [f.to_dict() for f in r.fulltext_sources],
    }


def abstract(index):
    return oa._reconstruct_abstract(index)


def fetch(payloads, email="a@b.c", api_key=None, day="2024-06-10"):
    client = FakeClient(payloads)
    records = []
    progress = []
    result = oa.fetch_openalex(
        client,
        date.fromisoformat(day),
        on_record=records.append,
        on_progress=progress.append,
        email=email,
        api_key=api_key,
    )
    # The cursor is not a request parameter the caller set, but it *is* what
    # distinguishes one page from the next, so it is reported.
    return {
        "status": result.status,
        "error": result.error,
        "note": result.note,
        "record_count": result.record_count,
        "cursors": [c["params"].get("cursor") for c in client.calls],
        "params": [c["params"] for c in client.calls],
        "records": [r.title for r in records],
        "progress": [[p.records_processed, p.records_total, p.status] for p in progress],
    }


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "normalize":
        return normalize(a["raw"])
    if fn == "abstract":
        return abstract(a.get("index"))
    if fn == "fetch":
        return fetch(
            a["payloads"],
            a.get("email", "a@b.c"),
            a.get("api_key"),
            a.get("day", "2024-06-10"),
        )
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
