#!/usr/bin/env python3
"""Re-run every Rust-port differential oracle against the live Python library.

The port's corpora are evidence only while they regenerate: a stale expectation
agrees with a library that has moved.  This script runs each ``rust/oracle/dump_*.py``
driver over the case file its Rust test reads and diffs the result against the
committed expectation, comparing **parsed** JSON (the port's own comparison rule).

Usage (from the repository root):

    .venv/bin/python scripts/rerun_rust_oracle.py            # report drift
    .venv/bin/python scripts/rerun_rust_oracle.py --write    # regenerate in place
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "rust" / "oracle"
DATA = ROOT / "rust" / "bmlib" / "tests" / "data"

# (dumper, case file relative to oracle/ or None, expectation relative to DATA)
# The case file is what the dumper reads on stdin; a `None` marks a driver that
# is self-contained (it imports the corpus itself or hard-codes its inputs).
CORPORA: list[tuple[str, str | None, str]] = [
    ("dump_analyzer.py", "analyzer_cases.json", "analyzer_expected.json"),
    ("dump_biorxiv.py", "biorxiv_cases.json", "biorxiv_expected.json"),
    ("dump_cache.py", "cache_cases.json", "cache_expected.json"),
    ("dump_citations.py", "cases.json", "citations_expected.json"),
    ("dump_cochrane.py", "cochrane_cases.json", "cochrane_expected.json"),
    (
        "dump_cochrane_assessor.py",
        "cochrane_assessor_cases.json",
        "cochrane_assessor_expected.json",
    ),
    ("dump_context.py", "context_cases.json", "context_expected.json"),
    ("dump_cost.py", None, "cost_expected.json"),
    ("dump_fetchers.py", "fetcher_cases.json", "fetcher_expected.json"),
    ("dump_formatter.py", "formatter_cases.json", "formatter_expected.json"),
    ("dump_funder_matcher.py", None, "funder_matcher_expected.json"),
    ("dump_jats.py", "jats_cases.json", "jats_expected.json"),
    ("dump_jats_text.py", "jats_text_cases.json", "jats_text_expected.json"),
    ("dump_json.py", "json_cases.json", "json_expected.json"),
    (
        "dump_llm_processor.py",
        "llm_processor_cases.json",
        "llm_processor_expected.json",
    ),
    ("dump_models.py", "model_cases.json", "model_expected.json"),
    ("dump_openalex.py", "openalex_cases.json", "openalex_expected.json"),
    ("dump_parse_audit.py", "parse_audit_cases.json", "parse_audit_expected.json"),
    ("dump_pdf_text.py", "pdf_text_cases.json", "pdf_text_expected.json"),
    ("dump_protocol.py", "protocol_cases.json", "protocol_expected.json"),
    ("dump_pubmed.py", "pubmed_cases.json", "pubmed_expected.json"),
    ("dump_pubmed_part.py", "pubmed_part_cases.json", "pubmed_part_expected.json"),
    (
        "dump_pubmed_transport.py",
        "pubmed_transport_cases.json",
        "pubmed_transport_expected.json",
    ),
    ("dump_pubmed_walk.py", "pubmed_walk_cases.json", "pubmed_walk_expected.json"),
    ("dump_pubmodels.py", "pubmodels_cases.json", "pubmodels_expected.json"),
    ("dump_quality.py", "quality_cases.json", "quality_expected.json"),
    ("dump_quality_llm.py", "quality_llm_cases.json", "quality_llm_expected.json"),
    ("dump_result_dict.py", "result_dict_cases.json", "result_dict_expected.json"),
    (
        "dump_retraction_store.py",
        "retraction_store_cases.json",
        "retraction_store_expected.json",
    ),
    ("dump_retractions.py", "retraction_cases.json", "retraction_expected.json"),
    ("dump_schema.py", None, "schema_expected.json"),
    ("dump_segmenter.py", "segmenter_cases.json", "segmenter_expected.json"),
    ("dump_service.py", "service_cases.json", "service_expected.json"),
    ("dump_storage.py", "storage_cases.json", "storage_expected.json"),
    ("dump_sync.py", "sync_cases.json", "sync_expected.json"),
    ("dump_sync_credit.py", "sync_credit_cases.json", "sync_credit_expected.json"),
    ("dump_templates.py", "templates_cases.json", "templates_expected.json"),
    ("dump_tiering.py", "tiering_cases.json", "tiering_expected.json"),
    ("dump_titles.py", "titles_cases.json", "titles_expected.json"),
    ("dump_transparency.py", "transparency_cases.json", "transparency_expected.json"),
]


def case_path(cases: str) -> Path:
    """The case file's home: ``oracle/``, or ``tests/data/`` for `service` alone.

    Most corpora keep two copies of their cases — one the dumper reads here and
    one the Rust test `include_str!`s — but `service` has no `oracle/` copy, so
    its single file is the dumper's input too.
    """
    candidate = ORACLE / cases
    return candidate if candidate.exists() else DATA / cases


def run_one(dumper: str, cases: str | None) -> tuple[str, str]:
    """Run one dumper, returning (stdout, stderr). Raises on a non-zero exit."""
    stdin = case_path(cases).read_bytes() if cases else b""
    proc = subprocess.run(
        [sys.executable, str(ORACLE / dumper)],
        input=stdin,
        cwd=ROOT,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{dumper} exited {proc.returncode}\n{proc.stderr.decode()[-2000:]}")
    return proc.stdout.decode(), proc.stderr.decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate in place")
    parser.add_argument("--only", help="run just this dumper")
    args = parser.parse_args()

    stale: list[str] = []
    broken: list[str] = []
    clean = 0
    ran = 0
    copies_differ: list[str] = []

    for dumper, cases, expected in CORPORA:
        if args.only and args.only not in dumper:
            continue
        expected_path = DATA / expected
        # A corpus with two copies of its cases (oracle/ and tests/data/) is
        # evidence only while they are the same list: the dumper's input and the
        # test's `include_str!` drifting apart is the round-41 trap in miniature.
        if cases:
            oracle_copy, data_copy = ORACLE / cases, DATA / cases
            if oracle_copy.exists() and data_copy.exists():
                if json.loads(oracle_copy.read_text()) != json.loads(data_copy.read_text()):
                    copies_differ.append(cases)
        try:
            fresh_text, stderr = run_one(dumper, cases)
        except RuntimeError as exc:
            broken.append(f"{dumper}: {exc}")
            print(f"BROKEN  {dumper}")
            continue

        try:
            fresh = json.loads(fresh_text)
            committed = json.loads(expected_path.read_text())
        except json.JSONDecodeError as exc:
            broken.append(f"{dumper}: unparseable output ({exc})")
            print(f"BROKEN  {dumper}  {exc}")
            continue

        ran += 1
        if fresh == committed:
            clean += 1
            note = f"  (stderr {len(stderr)}B)" if stderr.strip() else ""
            print(f"ok      {dumper}{note}")
            continue

        stale.append(dumper)
        print(f"STALE   {dumper}")
        if args.write:
            expected_path.write_text(fresh_text)
            print(f"        rewritten {expected_path.relative_to(ROOT)}")

    print()
    print(f"{ran + len(broken)} run: {clean} clean, {len(stale)} stale, {len(broken)} broken")
    if copies_differ:
        print("CASE COPIES DIFFER: " + ", ".join(copies_differ))
    if stale:
        print("STALE: " + ", ".join(stale))
    if broken:
        print("BROKEN:\n  " + "\n  ".join(broken))
    return 1 if (stale or broken or copies_differ) and not args.write else 0


if __name__ == "__main__":
    raise SystemExit(main())
