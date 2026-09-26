#!/usr/bin/env python3
"""Dump bmlib's ``fulltext.service`` pure helpers, for the Rust port.

The service's tier chain needs a live client, so it is driven in Rust by a
scripted ``HttpClient`` and its named tests. What *is* directly drivable here is
the module-level half — the free-PDF availability rule, the free-PDF URL
extractor, Unpaywall's PDF picker, the PMC-ID validator, the pluraliser and the
exhaustion summary — plus ``jats_parser._build_html``, which the port carries
inside ``service.rs`` because ``jats_reader.rs`` stops at structured data and
the service is the rendering's only consumer.

Cases come in on stdin and expectations go out on stdout, as for every other
dumper in this directory.
"""

from __future__ import annotations

import json
import sys

from bmlib.fulltext.jats_parser import _build_html
from bmlib.fulltext.models import (
    JATSAbstractSection,
    JATSArticle,
    JATSAuthorInfo,
    JATSBodySection,
    JATSFigureInfo,
    JATSFundingAward,
    JATSFundingSource,
    JATSReferenceInfo,
    JATSTableInfo,
)
from bmlib.fulltext.service import (
    FullTextError,
    FullTextUnavailableError,
    _entry_is_free,
    _extract_free_pdf_url,
    _normalise_pmc_id,
    _pick_oa_pdf_url,
    _plural,
    _TierFailures,
)

#: The exception classes a case may name, by ``__name__``.
FAULTS = {
    "OSError": OSError,
    "TypeError": TypeError,
    "AttributeError": AttributeError,
    "NameError": NameError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "ValueError": ValueError,
    "RuntimeError": RuntimeError,
}


def author(spec: dict) -> JATSAuthorInfo:
    return JATSAuthorInfo(
        surname=spec.get("surname", ""),
        given_names=spec.get("given_names", ""),
        affiliations=list(spec.get("affiliations", [])),
        collab=spec.get("collab", ""),
        string_name=spec.get("string_name", ""),
    )


def abstract(spec: dict) -> JATSAbstractSection:
    return JATSAbstractSection(title=spec["title"], content=spec["content"])


def body(spec: dict) -> JATSBodySection:
    return JATSBodySection(
        title=spec["title"],
        paragraphs=list(spec.get("paragraphs", [])),
        subsections=[body(s) for s in spec.get("subsections", [])],
    )


def figure(spec: dict) -> JATSFigureInfo:
    return JATSFigureInfo(
        id=spec["id"],
        label=spec["label"],
        caption=spec["caption"],
        graphic_url=spec.get("graphic_url"),
        footnotes=list(spec.get("footnotes", [])),
    )


def table(spec: dict) -> JATSTableInfo:
    return JATSTableInfo(
        id=spec["id"],
        label=spec["label"],
        caption=spec["caption"],
        html_content=spec.get("html_content", ""),
        graphic_url=spec.get("graphic_url"),
        footnotes=list(spec.get("footnotes", [])),
    )


def reference(spec: dict) -> JATSReferenceInfo:
    return JATSReferenceInfo(
        id=spec["id"],
        label=spec["label"],
        citation=spec["citation"],
        authors=list(spec.get("authors", [])),
        article_title=spec.get("article_title", ""),
        source=spec.get("source", ""),
        year=spec.get("year", ""),
        volume=spec.get("volume", ""),
        issue=spec.get("issue", ""),
        first_page=spec.get("first_page", ""),
        last_page=spec.get("last_page", ""),
        doi=spec.get("doi", ""),
        pmid=spec.get("pmid", ""),
        elocation_id=spec.get("elocation_id", ""),
    )


def award(spec: dict) -> JATSFundingAward:
    return JATSFundingAward(
        sources=[
            JATSFundingSource(
                name=source.get("name", ""), identifier=source.get("identifier", "")
            )
            for source in spec.get("sources", [])
        ],
        award_ids=list(spec.get("award_ids", [])),
    )


def article(spec: dict) -> JATSArticle:
    """Build a ``JATSArticle`` from the case's JSON.

    Every default here is the dataclass's own, so the JSON the Rust side
    deserialises into ``JATSArticle`` and the article rendered here are the same
    document.
    """
    built = JATSArticle(
        title=spec.get("title", ""),
        authors=[author(a) for a in spec.get("authors", [])],
        journal=spec.get("journal", ""),
        volume=spec.get("volume", ""),
        issue=spec.get("issue", ""),
        pages=spec.get("pages", ""),
        year=spec.get("year", ""),
        doi=spec.get("doi", ""),
        pmc_id=spec.get("pmc_id", ""),
        pmid=spec.get("pmid", ""),
        abstract_sections=[abstract(s) for s in spec.get("abstract_sections", [])],
        body_sections=[body(s) for s in spec.get("body_sections", [])],
        figures=[figure(f) for f in spec.get("figures", [])],
        tables=[table(t) for t in spec.get("tables", [])],
        references=[reference(r) for r in spec.get("references", [])],
    )
    built.has_body = spec.get("has_body", False)
    built.suppressed_nested_articles = spec.get("suppressed_nested_articles", 0)
    built.elocation_id = spec.get("elocation_id", "")
    built.funding_statements = list(spec.get("funding_statements", []))
    built.funding_awards = [award(a) for a in spec.get("funding_awards", [])]
    return built


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})
    if fn == "entry_is_free":
        return _entry_is_free(args["entry"])
    if fn == "extract_free_pdf_url":
        return _extract_free_pdf_url(args["result"])
    if fn == "pick_oa_pdf_url":
        return _pick_oa_pdf_url(args["data"])
    if fn == "normalise_pmc_id":
        return _normalise_pmc_id(args["value"])
    if fn == "plural":
        return _plural(args["n"], args["noun"])
    if fn == "tier_failures":
        failures = _TierFailures.unreported()
        for item in args["records"]:
            if item["kind"] == "fault":
                failures.record(FAULTS[item["name"]](item.get("message", "boom")))
            elif item["kind"] == "unavailable":
                failures.record(FullTextUnavailableError(item.get("message", "none")))
            elif item["kind"] == "plain_error":
                failures.record(FullTextError(item.get("message", "failed")))
            elif item["kind"] == "absence":
                failures.note_absence()
            else:
                raise ValueError(f"unknown record kind {item['kind']!r}")
        return {
            "describe": failures.describe(),
            "faults": list(failures.faults),
            "absences": failures.absences,
        }
    if fn == "build_html":
        return _build_html(article(args["article"]))
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append(
                {"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
