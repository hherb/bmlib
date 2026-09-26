#!/usr/bin/env python3
"""Dump bmlib's PubMed XML reader behaviour, for the Rust port."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

import bmlib.publications.fetchers.pubmed as pm


def parse(xml):
    root = ET.fromstring(xml)
    return [pm._parse_article_xml(el) for el in root]


def record_to_dict(r):
    return {
        "title": r.title, "source": r.source, "doi": r.doi, "pmid": r.pmid,
        "pmc_id": r.pmc_id, "abstract": r.abstract, "authors": r.authors,
        "journal": r.journal, "publication_date": r.publication_date,
        "keywords": r.keywords, "publication_types": r.publication_types,
        "fulltext_sources": [f.to_dict() for f in r.fulltext_sources],
        "grants": [g.to_dict() for g in r.grants],
        "author_affiliations": [a.to_dict() for a in r.author_affiliations],
    }


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "parse":
        return [record_to_dict(r) for r in parse(a["xml"])]
    if fn == "escape_markdown":
        return pm._escape_markdown(a["text"])
    if fn == "text_with_formatting":
        el = ET.fromstring(a["xml"])
        return pm._text_with_formatting(el)
    if fn == "abstract_markdown":
        el = ET.fromstring(a["xml"])
        return pm._format_abstract_markdown(el)
    if fn == "parse_pubdate":
        el = ET.fromstring(a["xml"])
        return pm._parse_pubdate(el)
    if fn == "author_name":
        el = ET.fromstring(a["xml"])
        return pm._author_name(el)
    if fn == "grants":
        el = ET.fromstring(a["xml"])
        return [g.to_dict() for g in pm._parse_grants(el)]
    if fn == "day_term":
        from datetime import date
        return pm._day_term(date.fromisoformat(a["date"]))
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
