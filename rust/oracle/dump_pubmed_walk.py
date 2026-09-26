#!/usr/bin/env python3
"""Dump bmlib's PubMed E-utilities walk, for the Rust port."""

from __future__ import annotations

import json
import sys
from datetime import date

import bmlib.publications.fetchers.pubmed as pm


class ScriptedCounter:
    """A `count_fn` over a scripted term->count map, recording every term asked.

    A term absent from the map is 0, which is what an empty Entrez range
    reports. Recording the terms is what makes the *number* of probes visible,
    so a mutation that adds or removes one shows up.
    """

    def __init__(self, counts, default=0):
        self.counts = counts
        self.default = default
        self.terms: list[str] = []

    def __call__(self, term):
        self.terms.append(term)
        return self.counts.get(term, self.default)


def _refused(kind: str, counter, exc: Exception) -> dict:
    """A refused partition, in the shape both drivers report.

    One helper because the three refusals differ only in their name — and the
    three inline copies were each 101 characters, which is how a rule stated
    three times ends up measured once.
    """
    return {
        "ok": False,
        "error": kind,
        "terms": counter.terms,
        "message": str(exc),
    }


def plan(case):
    counter = ScriptedCounter(case.get("counts", {}), case.get("default", 0))
    lo = date.fromisoformat(case.get("lo", "1900-01-01"))
    hi = date.fromisoformat(case.get("hi", "2100-12-31"))
    try:
        parts = pm._plan_partitions(
            counter,
            case["day_term"],
            case["day_count"],
            lo=lo,
            hi=hi,
            probe_root=case.get("probe_root", True),
            known_count=case.get("known_count"),
        )
        return {
            "ok": True,
            "parts": [[p.lo.isoformat(), p.hi.isoformat(), p.promised, p.key] for p in parts],
            "terms": counter.terms,
        }
    except pm._UnsplittableDayError as exc:
        return _refused("Unsplittable", counter, exc)
    except pm._RootNotCoveringError as exc:
        return _refused("RootNotCovering", counter, exc)
    except ValueError as exc:
        return _refused("ValueError", counter, exc)


def walk(case):
    """Drive `_walk_session` with scripted pages."""
    pages = list(case["pages"])

    def fetch_page(retstart):
        page = pages.pop(0)
        if isinstance(page, str):
            raise ValueError(page)
        return pm._EFetchPage(articles=[], delivered=page["delivered"])

    # `_walk_session` calls `_efetch_page` and `_parse_article_xml`; both are
    # replaced so the script drives the loop without XML.
    seen_retstarts: list[int] = []
    real_efetch = pm._efetch_page
    real_parse = pm._parse_article_xml
    real_sleep = pm.time.sleep
    real_logger = pm.logger

    class _Silent:
        def __getattr__(self, _):
            return lambda *a, **kw: None

    def efetch(client, web_env, query_key, retstart, api_key):
        seen_retstarts.append(retstart)
        page = pages.pop(0)
        if isinstance(page, str):
            raise ValueError(page)
        return pm._EFetchPage(
            articles=[object()] * page.get("articles", 0), delivered=page["delivered"]
        )

    pm._efetch_page = efetch
    pm._parse_article_xml = lambda el: el
    pm.time.sleep = lambda _: None
    pm.logger = _Silent()
    try:
        progress: list[int] = []
        outcome = pm._walk_session(
            None,
            "w",
            "q",
            case["promised"],
            on_record=lambda r: None,
            api_key=None,
            rate_limit=0.0,
            on_page=progress.append,
        )
        return {
            "processed": outcome.processed,
            "delivered": outcome.delivered,
            "stalled": outcome.stalled,
            "error": outcome.error,
            "retstarts": seen_retstarts,
            "progress": progress,
        }
    finally:
        pm._efetch_page = real_efetch
        pm._parse_article_xml = real_parse
        pm.time.sleep = real_sleep
        pm.logger = real_logger


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "part_key":
        return pm._part_key(date.fromisoformat(a["lo"]), date.fromisoformat(a["hi"]))
    if fn == "edat_range_term":
        return pm._edat_range_term(
            a["day_term"], date.fromisoformat(a["lo"]), date.fromisoformat(a["hi"])
        )
    if fn == "plan":
        return plan(a)
    if fn == "walk":
        return walk(a)
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
