#!/usr/bin/env python3
"""Dump bmlib's Europe PMC day-by-day fetch behaviour, for the Rust port."""

from __future__ import annotations

import json
import sys
from datetime import date
from unittest.mock import patch

from _oracle import named_exception, response_marker

import bmlib.publications.fetchers.europepmc as ep


class FakeResponse:
    """A scripted httpx-like response."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise named_exception(f"HTTPStatusError: HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeClient:
    """Answers the scripted pages in order, recording each call's parameters."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def get(self, url, params=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        payload = (
            self._payloads.pop(0)
            if self._payloads
            else {"hitCount": 0, "resultList": {"result": []}}
        )
        marker = response_marker(payload)
        if marker is None:
            return FakeResponse(payload)
        if marker[0] == "transport_error":
            raise named_exception(f"{marker[1]}: {marker[2]}")
        return FakeResponse(marker[1], marker[2])


def fetch(payloads, query, email=None, day="2024-06-10"):
    """Run one scripted walk and report what the port must reproduce."""
    client = FakeClient(payloads)
    records = []
    progress = []
    with patch.object(ep.time, "sleep"):
        result = ep.fetch_europepmc(
            client,
            date.fromisoformat(day),
            on_record=records.append,
            on_progress=progress.append,
            query=query,
            email=email,
        )
    return {
        "status": result.status,
        "error": result.error,
        "note": result.note,
        "record_count": result.record_count,
        "urls": [c["url"] for c in client.calls],
        "params": [c["params"] for c in client.calls],
        "records": [r.title for r in records],
        "fulltext_urls": [[f.url for f in r.fulltext_sources] for r in records],
        "progress": [[p.records_processed, p.records_total, p.status] for p in progress],
    }


def run(case):
    """Dispatch one case."""
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "fetch":
        return fetch(a["payloads"], a["query"], a.get("email"), a.get("day", "2024-06-10"))
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    """Read cases on stdin, write the expectations on stdout."""
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
