#!/usr/bin/env python3
"""Dump bmlib's JATS reader output, for the Rust port.

The input corpus is the fixtures the Python suite commits plus the JATS documents
its tests carry inline. Each is parsed and the whole `JATSArticle` rendered, so
the port is compared on **every field of every article** rather than on the
assertions one test happened to make.
"""

from __future__ import annotations

import json
import sys

from bmlib.fulltext.jats_parser import JATSParser


def render_author(a) -> dict:
    return {
        "surname": a.surname, "given_names": a.given_names,
        "affiliations": list(a.affiliations), "collab": a.collab,
        "string_name": a.string_name, "full_name": a.full_name,
        "is_named": a.is_named,
    }


def render_article(article) -> dict:
    return {
        "title": article.title,
        "journal": article.journal,
        "volume": article.volume,
        "issue": article.issue,
        "pages": article.pages,
        "year": article.year,
        "doi": article.doi,
        "pmc_id": article.pmc_id,
        "pmid": article.pmid,
        "elocation_id": article.elocation_id,
        "has_body": article.has_body,
        "suppressed_nested_articles": article.suppressed_nested_articles,
        "authors": [render_author(a) for a in article.authors],
        "abstract_sections": [
            {"title": s.title, "content": s.content}
            for s in article.abstract_sections
        ],
        "body_sections": [render_body(s) for s in article.body_sections],
        "figures": [
            {"id": f.id, "label": f.label, "caption": f.caption,
             "graphic_url": f.graphic_url, "footnotes": list(f.footnotes)}
            for f in article.figures
        ],
        "tables": [
            {"id": t.id, "label": t.label, "caption": t.caption,
             "html_content": t.html_content, "graphic_url": t.graphic_url,
             "footnotes": list(t.footnotes)}
            for t in article.tables
        ],
        "references": [render_reference(r) for r in article.references],
        "funding_statements": list(article.funding_statements),
        "funding_awards": [
            {"sources": [{"name": s.name, "identifier": s.identifier} for s in a.sources],
             "award_ids": list(a.award_ids)}
            for a in article.funding_awards
        ],
    }


def render_body(section) -> dict:
    return {
        "title": section.title,
        "paragraphs": list(section.paragraphs),
        "subsections": [render_body(s) for s in section.subsections],
    }


def render_reference(ref) -> dict:
    return {
        "id": ref.id, "label": ref.label, "citation": ref.citation,
        "authors": list(ref.authors), "article_title": ref.article_title,
        "source": ref.source, "year": ref.year, "volume": ref.volume,
        "issue": ref.issue, "first_page": ref.first_page,
        "last_page": ref.last_page, "doi": ref.doi, "pmid": ref.pmid,
        "elocation_id": ref.elocation_id,
        "formatted_citation": ref.formatted_citation,
    }


def run(case):
    xml = case["xml"].encode()
    for attempt in (xml, None):
        if attempt is None:
            break
        try:
            article = JATSParser(attempt).parse()
        except Exception:  # noqa: BLE001
            continue
        return render_article(article)
    # The wrapped form, for a fragment that is not a whole document.
    article = JATSParser(
        f'<?xml version="1.0"?>\n<article>{case["xml"]}</article>'.encode()
    ).parse()
    return render_article(article)


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"[:200]})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
