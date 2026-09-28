#!/usr/bin/env python3
"""Dump bmlib's bioRxiv/medRxiv fetch behaviour, for the Rust port."""

from __future__ import annotations

import json
import sys
from datetime import date

from _oracle import named_exception, response_marker

import bmlib.publications.fetchers.biorxiv as bx


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
        # A real client decodes the body, so a body that is the JSON string
        # `"text"` arrives as the *string* `text` and is refused by the
        # object check. Raising here instead would model a decode failure,
        # which is a transport concern and a different message.
        return self._payload


class FakeClient:
    """Serves a list of payloads in order, one per GET, recording the URLs."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.urls: list[str] = []

    def get(self, url):
        self.urls.append(url)
        payload = self._payloads.pop(0) if self._payloads else {"collection": [], "messages": []}
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


def normalize(raw, server):
    r = bx._normalize(raw, server)
    return {
        "title": r.title,
        "source": r.source,
        "doi": r.doi,
        "abstract": r.abstract,
        "authors": r.authors,
        "publication_date": r.publication_date,
        "is_open_access": r.is_open_access,
        "fulltext_sources": [f.to_dict() for f in r.fulltext_sources],
        "extras": r.extras,
    }


def fetch(payloads, server="biorxiv", day="2024-06-10"):
    client = FakeClient(payloads)
    records = []
    progress = []
    result = bx.fetch_biorxiv(
        client,
        date.fromisoformat(day),
        on_record=records.append,
        on_progress=progress.append,
        server=server,
    )
    return {
        "status": result.status,
        "error": result.error,
        "note": result.note,
        "record_count": result.record_count,
        "urls": client.urls,
        "records": [r.title for r in records],
        "progress": [[p.records_processed, p.records_total, p.status] for p in progress],
    }


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "normalize":
        return normalize(a["raw"], a.get("server", "biorxiv"))
    if fn == "fetch":
        return fetch(a["payloads"], a.get("server", "biorxiv"), a.get("day", "2024-06-10"))
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
