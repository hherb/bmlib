#!/usr/bin/env python3
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Re-run every Rust-port differential oracle against the live Python library.

The port's corpora are evidence only while they regenerate: a stale expectation
agrees with a library that has moved.  This script runs each ``rust/oracle/dump_*.py``
driver over the case file its Rust test reads and diffs the result against the
committed expectation, comparing **parsed** JSON — which is looser than the Rust
harnesses' comparison (``1 == 1.0`` here), so "clean" means "not stale", and the
Rust tests remain the check that the port agrees.

Three things fail a run, ``--write`` or not: a dumper that crashed or printed
something unparseable, a corpus whose two case copies disagree (the dumper
would describe a case list the Rust test does not read), and a ``dump_*.py`` on
disk that ``CORPORA`` does not list (it would be skipped while the run reported
every corpus clean).  ``--write`` excuses staleness alone, by rewriting it.

Usage (from the repository root):

    .venv/bin/python scripts/rerun_rust_oracle.py                   # report drift
    .venv/bin/python scripts/rerun_rust_oracle.py --write           # regenerate in place
    .venv/bin/python scripts/rerun_rust_oracle.py --only cochrane   # one dumper
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "rust" / "oracle"
DATA = ROOT / "rust" / "bmlib" / "tests" / "data"
FUNDER_CORPUS = ROOT / "tests" / "data" / "funder_names.json"

# A dumper that runs longer than this is hung, not slow: the largest takes seconds.
DUMPER_TIMEOUT_SECONDS = 600

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

# The Rust test's copy of a case file, where it is not named like the dumper's.
TEST_COPY_NAMES: dict[str, str] = {"cases.json": "citations_cases.json"}


def case_path(cases: str) -> Path:
    """The case file the dumper reads: ``oracle/``, or ``tests/data/`` for `service`.

    Most corpora keep two copies of their cases — one the dumper reads here and
    one the Rust test ``include_str!``s — but `service` has no ``oracle/`` copy,
    so its single file is the dumper's input too.
    """
    candidate = ORACLE / cases
    return candidate if candidate.exists() else DATA / cases


def test_copy_path(cases: str) -> Path:
    """The case file the Rust test ``include_str!``s."""
    return DATA / TEST_COPY_NAMES.get(cases, cases)


def copies_agree(cases: str) -> bool:
    """Whether the dumper's case list and the Rust test's are the same list.

    A corpus with one copy agrees trivially.  Two copies that drift apart make
    the regenerated expectation describe cases the Rust test never runs.
    """
    dumper_copy, test_copy = case_path(cases), test_copy_path(cases)
    if dumper_copy == test_copy or not test_copy.exists():
        return True
    return json.loads(dumper_copy.read_text()) == json.loads(test_copy.read_text())


def funder_cases_agree() -> bool:
    """Whether ``funder_matcher_cases.json`` still lists the labelled corpus.

    `dump_funder_matcher.py` reads ``tests/data/funder_names.json`` itself, while
    the Rust test reads a case file derived from it; the two must name the same
    funders in the same order.
    """
    entries = json.loads(FUNDER_CORPUS.read_text())["entries"]
    derived = json.loads((DATA / "funder_matcher_cases.json").read_text())
    return [e["name"] for e in entries] == [c["args"]["name"] for c in derived]


def unlisted_dumpers() -> list[str]:
    """``dump_*.py`` files on disk that ``CORPORA`` does not list."""
    listed = {dumper for dumper, _, _ in CORPORA}
    return sorted(p.name for p in ORACLE.glob("dump_*.py") if p.name not in listed)


def select(only: str | None) -> list[tuple[str, str | None, str]]:
    """The corpora to run: all, or the one dumper ``only`` names exactly.

    ``cochrane``, ``dump_cochrane`` and ``dump_cochrane.py`` all name
    ``dump_cochrane.py`` and nothing else — a substring match would also run
    ``dump_cochrane_assessor.py``.  An unknown name selects nothing, which
    ``main`` reports as a failure rather than as a clean run of zero corpora.
    """
    if only is None:
        return list(CORPORA)
    name = only if only.startswith("dump_") else f"dump_{only}"
    name = name if name.endswith(".py") else f"{name}.py"
    return [corpus for corpus in CORPORA if corpus[0] == name]


def run_one(dumper: str, cases: str | None) -> tuple[str, str]:
    """Run one dumper, returning (stdout, stderr).

    Raises:
        RuntimeError: If the dumper exits non-zero or hangs past the timeout.
    """
    stdin = case_path(cases).read_bytes() if cases else b""
    try:
        proc = subprocess.run(
            [sys.executable, str(ORACLE / dumper)],
            input=stdin,
            cwd=ROOT,
            capture_output=True,
            timeout=DUMPER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{dumper} ran past {DUMPER_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{dumper} exited {proc.returncode}\n{proc.stderr.decode()[-2000:]}")
    return proc.stdout.decode(), proc.stderr.decode()


@dataclass
class Report:
    """What one run found."""

    clean: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    copies_differ: list[str] = field(default_factory=list)
    unlisted: list[str] = field(default_factory=list)
    selected_nothing: bool = False

    def exit_code(self, write: bool) -> int:
        """Non-zero for anything ``--write`` did not repair.

        Staleness is the one thing ``--write`` fixes.  A broken dumper was not
        regenerated, differing copies were refused a write, an unlisted dumper
        was never run, and a ``--only`` naming nothing ran nothing.
        """
        unrepaired = self.broken or self.copies_differ or self.unlisted or self.selected_nothing
        return 1 if unrepaired or (self.stale and not write) else 0


def check(dumper: str, cases: str | None, expected: str, write: bool, verbose: bool) -> str:
    """Run one corpus and return its verdict: ``clean``, ``stale`` or ``broken: …``.

    A missing or corrupt *committed* expectation is stale, not broken — the
    dumper is fine and ``--write`` repairs it — while a missing case file or
    unparseable dumper output is broken.
    """
    if cases and not case_path(cases).exists():
        return f"broken: no case file {cases}"
    try:
        fresh_text, stderr = run_one(dumper, cases)
    except RuntimeError as exc:
        return f"broken: {exc}"
    try:
        fresh = json.loads(fresh_text)
    except json.JSONDecodeError as exc:
        return f"broken: unparseable output ({exc})"
    if verbose and stderr.strip():
        indented = "\n".join(f"          {line}" for line in stderr.splitlines())
        print(f"        stderr of {dumper}:\n{indented}")
    expected_path = DATA / expected
    try:
        committed = json.loads(expected_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        committed = None
    if committed is not None and fresh == committed:
        return "clean"
    if write:
        expected_path.write_text(fresh_text)
    return "stale"


def main() -> int:
    """Run the selected corpora, print a report and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate stale expectations")
    parser.add_argument("--only", help="run one dumper, e.g. `cochrane` or `dump_cochrane.py`")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each dumper's stderr")
    args = parser.parse_args()

    report = Report(unlisted=unlisted_dumpers())
    selected = select(args.only)
    report.selected_nothing = not selected
    if not funder_cases_agree() and any(d == "dump_funder_matcher.py" for d, _, _ in selected):
        report.copies_differ.append("funder_matcher_cases.json (against funder_names.json)")

    for dumper, cases, expected in selected:
        if cases and not copies_agree(cases):
            report.copies_differ.append(cases)
            print(f"REFUSED {dumper}  (case copies differ; not run, not written)")
            continue
        verdict = check(dumper, cases, expected, args.write, args.verbose)
        if verdict == "clean":
            report.clean.append(dumper)
            print(f"ok      {dumper}")
        elif verdict == "stale":
            report.stale.append(dumper)
            written = f"  rewritten {expected}" if args.write else ""
            print(f"STALE   {dumper}{written}")
        else:
            report.broken.append(f"{dumper}: {verdict.removeprefix('broken: ')}")
            print(f"BROKEN  {dumper}")

    print()
    clean, stale, broken = len(report.clean), len(report.stale), len(report.broken)
    print(f"{clean + stale + broken} run: {clean} clean, {stale} stale, {broken} broken")
    if report.selected_nothing:
        print(f"NOTHING SELECTED: no dumper is named {args.only!r}")
    if report.unlisted:
        print("UNLISTED (never run): " + ", ".join(report.unlisted))
    if report.copies_differ:
        print("CASE COPIES DIFFER: " + ", ".join(report.copies_differ))
    if report.stale:
        print("STALE: " + ", ".join(report.stale))
    if report.broken:
        print("BROKEN:\n  " + "\n  ".join(report.broken))
    return report.exit_code(args.write)


if __name__ == "__main__":
    raise SystemExit(main())
