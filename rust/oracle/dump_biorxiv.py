#!/usr/bin/env python3
"""Dump bmlib's bioRxiv/medRxiv fetch behaviour, for the Rust port."""

from __future__ import annotations

import json
import sys
from datetime import date

import bmlib.publications.fetchers.biorxiv as bx


class HTTPStatusError(Exception):
    """A non-2xx response, under Python's own exception name.

    `httpx.Response.raise_for_status()` raises `httpx.HTTPStatusError`, and
    `fetch_biorxiv` catches it and stores `f"{type(exc).__name__}: {exc}"` — so
    the *name* is what the library contributes and the message is the
    transport's. This class carries the name. Its message is deterministic on
    purpose: httpx's own is version-specific (on 0.28.1 a 500 reads
    `Server error '500 Internal Server Error' for url '<url>'` followed by an
    MDN link), and pinning the corpus to that would redden the oracle on an
    httpx upgrade for a cosmetic reason. The port states the URL and status
    itself, so the case records the difference as a `corrected` block (#349).
    """


def split_response(payload):
    """Split a corpus payload into `(body, status)`.

    A response is either a bare body (HTTP 200) or an object
    ``{"http_status": N, "body": B}``. The marker is an **object** because the
    ``(body, status)`` **tuple** this used to test for cannot be written in
    JSON at all — a list decodes as a list, so every case that meant to serve a
    500 served its payload as a list body instead, and `fetch/http-error`
    duplicated `fetch/non-object-payload` on both sides (#349).
    """
    if (
        isinstance(payload, dict)
        and "http_status" in payload
        and set(payload) <= {"http_status", "body"}
    ):
        status = payload["http_status"]
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError(f"http_status must be an integer, got {status!r}")
        return payload.get("body"), status
    return payload, 200


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPStatusError(f"HTTP {self.status_code}")

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
        body, status = split_response(payload)
        return FakeResponse(body, status)


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
