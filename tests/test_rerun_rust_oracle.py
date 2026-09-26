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

"""Tests for ``scripts/rerun_rust_oracle.py``.

The script is the Rust port's evidence that its oracle corpora still describe
the live Python library, so what is pinned here is the property that makes a
green run trustworthy: **nothing it could not check reports success.** A
dumper it never ran, a case list the Rust test does not read, and a ``--only``
that named nothing must each fail the run — ``--write`` or not.

No dumper is run: the pure decisions are tested directly, and the one test of
``check`` substitutes ``run_one``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "rerun_rust_oracle.py"
_spec = importlib.util.spec_from_file_location("bmlib_rerun_rust_oracle", _SCRIPT_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the oracle rerunner from {_SCRIPT_PATH}")
rerun = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rerun
_spec.loader.exec_module(rerun)


class TestEveryDumperIsListed:
    """``CORPORA`` is hand-written, so the disk is what it is checked against."""

    def test_no_dumper_on_disk_is_unlisted(self) -> None:
        assert rerun.unlisted_dumpers() == []

    def test_every_listed_dumper_exists(self) -> None:
        missing = [d for d, _, _ in rerun.CORPORA if not (rerun.ORACLE / d).exists()]
        assert missing == []

    def test_an_unlisted_dumper_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dropped = rerun.CORPORA[0][0]
        monkeypatch.setattr(rerun, "CORPORA", rerun.CORPORA[1:])
        assert rerun.unlisted_dumpers() == [dropped]


class TestOnlyNamesOneDumperExactly:
    """A substring match ran ``dump_cochrane_assessor.py`` for ``cochrane``."""

    @pytest.mark.parametrize("only", ["cochrane", "dump_cochrane", "dump_cochrane.py"])
    def test_each_spelling_selects_that_dumper_alone(self, only: str) -> None:
        assert [d for d, _, _ in rerun.select(only)] == ["dump_cochrane.py"]

    def test_an_unknown_name_selects_nothing(self) -> None:
        assert rerun.select("no_such_dumper") == []

    def test_no_filter_selects_every_corpus(self) -> None:
        assert rerun.select(None) == list(rerun.CORPORA)


class TestTheExitCode:
    """``--write`` excuses staleness and nothing else."""

    def test_a_clean_run_passes(self) -> None:
        assert rerun.Report(clean=["a"]).exit_code(write=False) == 0

    def test_staleness_fails_a_report_and_passes_a_write(self) -> None:
        report = rerun.Report(stale=["a"])
        assert report.exit_code(write=False) == 1
        assert report.exit_code(write=True) == 0

    @pytest.mark.parametrize(
        "report",
        [
            rerun.Report(broken=["a: exited 1"]),
            rerun.Report(copies_differ=["a_cases.json"]),
            rerun.Report(unlisted=["dump_new.py"]),
            rerun.Report(selected_nothing=True),
        ],
    )
    def test_what_a_write_cannot_repair_fails_either_way(self, report: rerun.Report) -> None:
        assert report.exit_code(write=False) == 1
        assert report.exit_code(write=True) == 1


class TestCaseCopiesAreCompared:
    """The dumper's case list and the Rust test's must be the same list."""

    def test_every_committed_pair_agrees(self) -> None:
        drifted = [c for _, c, _ in rerun.CORPORA if c and not rerun.copies_agree(c)]
        assert drifted == []

    def test_the_differently_named_citations_copy_is_compared(self) -> None:
        assert rerun.test_copy_path("cases.json").name == "citations_cases.json"
        assert rerun.test_copy_path("cases.json").exists()

    def test_a_drifted_copy_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oracle, data = tmp_path / "oracle", tmp_path / "data"
        oracle.mkdir()
        data.mkdir()
        (oracle / "x_cases.json").write_text(json.dumps([{"name": "a"}]))
        (data / "x_cases.json").write_text(json.dumps([{"name": "b"}]))
        monkeypatch.setattr(rerun, "ORACLE", oracle)
        monkeypatch.setattr(rerun, "DATA", data)
        assert not rerun.copies_agree("x_cases.json")

    def test_the_funder_cases_follow_the_labelled_corpus(self) -> None:
        assert rerun.funder_cases_agree()


class TestAVerdictNamesItsCause:
    """A corrupt *expectation* is stale and rewritable; bad *output* is broken."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(rerun, "DATA", tmp_path)
        monkeypatch.setattr(rerun, "ORACLE", tmp_path)
        return tmp_path

    def _stub(self, monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
        monkeypatch.setattr(rerun, "run_one", lambda dumper, cases: (stdout, ""))

    def test_matching_output_is_clean(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (data_dir / "x_expected.json").write_text("[1]\n")
        self._stub(monkeypatch, "[1]\n")
        assert rerun.check("dump_x.py", None, "x_expected.json", False, False) == "clean"

    def test_a_corrupt_expectation_is_stale_and_rewritten(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (data_dir / "x_expected.json").write_text("[1")
        self._stub(monkeypatch, "[1]\n")
        assert rerun.check("dump_x.py", None, "x_expected.json", True, False) == "stale"
        assert (data_dir / "x_expected.json").read_text() == "[1]\n"

    def test_a_missing_expectation_is_stale_and_written(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub(monkeypatch, "[2]\n")
        assert rerun.check("dump_x.py", None, "x_expected.json", True, False) == "stale"
        assert (data_dir / "x_expected.json").read_text() == "[2]\n"

    def test_unparseable_output_is_broken_and_writes_nothing(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (data_dir / "x_expected.json").write_text("[1]\n")
        self._stub(monkeypatch, "Traceback")
        verdict = rerun.check("dump_x.py", None, "x_expected.json", True, False)
        assert verdict.startswith("broken: unparseable output")
        assert (data_dir / "x_expected.json").read_text() == "[1]\n"

    def test_a_missing_case_file_is_broken(self, data_dir: Path) -> None:
        verdict = rerun.check("dump_x.py", "x_cases.json", "x_expected.json", False, False)
        assert verdict == "broken: no case file x_cases.json"
