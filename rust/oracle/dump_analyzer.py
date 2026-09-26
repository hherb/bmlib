#!/usr/bin/env python3
"""Dump bmlib's transparency analyzer helpers, for the Rust port.

Drives the pure half of ``bmlib.transparency.analyzer``: the funder matcher,
the four JSON coercers, the Europe PMC record walk, the trial-id heuristic, the
nested-article lexer, the COI text scans, the PubMed signal parser and the
analysis merge rules. The HTTP steps are *not* driven here — they need a live
client, and on the Rust side they are covered by ``tests/analyzer.rs``'s
scripted transport instead.

Reads a JSON list of cases on stdin, writes a JSON list of results on stdout.

    .venv/bin/python rust/oracle/dump_analyzer.py < rust/oracle/analyzer_cases.json \
        > rust/bmlib/tests/data/analyzer_expected.json
"""

from __future__ import annotations

import json
import sys

from bmlib.transparency.analyzer import (
    _DATA_PATTERNS,
    _Analysis,
    _discloses_industry_ties,
    _epmc_records,
    _extract_coi_text,
    _extract_tagged_coi_text,
    _find_trial_ids,
    _is_industry_funder,
    _json_bool,
    _json_count,
    _json_object,
    _json_text,
    _merge_pubmed_signals,
    _note_full_text_provenance,
    _parse_pubmed_signals,
    _pmid_from_epmc,
    _PubMedSignals,
    _score_data_availability,
    _strip_nested_articles,
    _UnterminatedMarkupError,
    _user_agent,
)
from bmlib.transparency.models import FullTextStatus, TrialResultsStatus


def _signals(a: dict) -> _PubMedSignals:
    return _PubMedSignals(
        coi_statement=a.get("coi_statement", False),
        trial_accessions=tuple(a.get("trial_accessions", ())),
        registration_not_checkable=a.get("registration_not_checkable", False),
        funders=tuple(a.get("funders", ())),
        deposition_databanks=tuple(a.get("deposition_databanks", ())),
    )


def _signals_out(s: _PubMedSignals) -> dict:
    return {
        "coi_statement": s.coi_statement,
        "trial_accessions": list(s.trial_accessions),
        "registration_not_checkable": s.registration_not_checkable,
        "funders": list(s.funders),
        "deposition_databanks": list(s.deposition_databanks),
    }


def _analysis(a: dict) -> _Analysis:
    an = _Analysis()
    an.indicators = list(a.get("indicators", []))
    for key in (
        "score",
        "industry_funding",
        "industry_confidence",
        "data_level",
        "coi_disclosed",
        "trial_registered",
        "results_compliant",
        "full_text_analyzed",
        "funder_info_scored",
    ):
        if key in a:
            setattr(an, key, a[key])
    if "full_text_status" in a:
        an.full_text_status = FullTextStatus(a["full_text_status"])
    if "trial_results_status" in a:
        an.trial_results_status = TrialResultsStatus(a["trial_results_status"])
    return an


def _analysis_out(an: _Analysis) -> dict:
    return {
        "score": an.score,
        "indicators": list(an.indicators),
        "industry_funding": an.industry_funding,
        "industry_confidence": an.industry_confidence,
        "data_level": an.data_level,
        "coi_disclosed": an.coi_disclosed,
        "trial_registered": an.trial_registered,
        "results_compliant": an.results_compliant,
        "full_text_analyzed": an.full_text_analyzed,
        "funder_info_scored": an.funder_info_scored,
        "full_text_status": an.full_text_status.value,
        "trial_results_status": an.trial_results_status.value,
    }


def run(case: dict) -> object:
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "is_industry_funder":
        return _is_industry_funder(a["name"])
    if fn == "json_object":
        return _json_object(a["value"])
    if fn == "json_text":
        return _json_text(a["value"])
    if fn == "json_count":
        return _json_count(a["value"])
    if fn == "json_bool":
        return _json_bool(a["value"])
    if fn == "epmc_records":
        return _epmc_records(a["epmc"])
    if fn == "find_trial_ids":
        return _find_trial_ids(a["epmc"])
    if fn == "pmid_from_epmc":
        return _pmid_from_epmc(a["epmc"])
    if fn == "strip_nested_articles":
        try:
            return _strip_nested_articles(a["xml"])
        except _UnterminatedMarkupError as exc:
            return {"refused": str(exc)}
    if fn == "extract_tagged_coi_text":
        return _extract_tagged_coi_text(a["full_text"])
    if fn == "extract_coi_text":
        return _extract_coi_text(a["full_text"])
    if fn == "discloses_industry_ties":
        return _discloses_industry_ties(a["coi_text"])
    if fn == "parse_pubmed_signals":
        return _signals_out(_parse_pubmed_signals(a["xml"], a.get("pmid", "1")))
    if fn == "merge_pubmed_signals":
        an = _analysis(a.get("analysis", {}))
        _merge_pubmed_signals(_signals(a["pubmed"]), an)
        return _analysis_out(an)
    if fn == "note_full_text_provenance":
        an = _analysis(a)
        _note_full_text_provenance(an)
        return _analysis_out(an)
    if fn == "score_data_availability":
        an = _analysis(a)
        _score_data_availability(an)
        return _analysis_out(an)
    if fn == "note_data_level":
        an = _analysis(a)
        an.note_data_level(a["level"])
        return an.data_level
    if fn == "user_agent":
        return _user_agent(a["email"], a["version"])
    if fn == "data_patterns":
        return [[pattern, level] for pattern, level in _DATA_PATTERNS.items()]
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
