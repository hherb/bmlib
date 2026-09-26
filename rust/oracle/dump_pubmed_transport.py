#!/usr/bin/env python3
"""Dump bmlib's ESearch reading and day-level branch, for the Rust port."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

from bmlib.publications.fetchers.pubmed import (
    EFETCH_MAX_RETRIEVABLE,
    _esearch,
    _text,
)


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)


class FakeClient:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        payload = self._payloads.pop(0)
        if isinstance(payload, tuple):
            payload, status = payload
            return FakeResponse(payload, status)
        return FakeResponse(payload)


def read_esearch(xml):
    """Read a document the way `_esearch` does, without a request."""
    root = ET.fromstring(xml)
    raw_count = _text(root.find("Count"))
    if raw_count is None or not raw_count.strip().isdigit():
        error = _text(root.find("ERROR")) or _text(root.find("ErrorList"))
        raise ValueError(
            f"esearch returned no usable <Count>{f' (NCBI said: {error})' if error else ''}"
        )
    return {
        "count": int(raw_count),
        "web_env": _text(root.find("WebEnv")),
        "query_key": _text(root.find("QueryKey")),
    }


def esearch_call(term, api_key, usehistory):
    client = FakeClient(["<eSearchResult><Count>0</Count></eSearchResult>"])
    _esearch(client, term, api_key, usehistory=usehistory)
    call = client.calls[0]
    return {"url": call["url"], "params": call["params"]}


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "read_esearch":
        try:
            return {"ok": True, "value": read_esearch(a["xml"])}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    if fn == "esearch_call":
        return esearch_call(a["term"], a.get("api_key"), a.get("usehistory", True))
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
