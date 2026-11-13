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

"""Unit tests for ``bmlib.library.scan`` that need no fixture tree (Rust's ``scan::tests``)."""

from __future__ import annotations

from pathlib import Path

from bmlib.library import (
    Installation,
    PaperFolder,
    PaperInfo,
    Record,
    Scan,
    canonical_ids,
    find,
    find_digest,
    group,
    open_library,
)


def _folder(
    name: str, pmid: str | None, doi: str | None, *more: tuple[str | None, str | None]
) -> PaperFolder:
    recs = [
        Record(
            Installation.new("studyanalyzer", f"a{i}", "Mac"),
            PaperInfo(canonical_ids(p, d, None)),
            (),
        )
        for i, (p, d) in enumerate([(pmid, doi), *more])
    ]
    return PaperFolder(name, tuple(recs), ())


def test_group_joins_transitively_and_keeps_scan_order() -> None:
    scan = Scan(
        (
            _folder("a", "1", None),
            _folder("b", "2", None),
            _folder("c", "1", "10.1/x"),
            _folder("d", None, "10.1/x"),
        ),
        (),
    )
    assert group(scan) == [[0, 2, 3], [1]]


def test_group_keeps_folders_without_identifiers_apart() -> None:
    scan = Scan((_folder("a", None, None), _folder("b", None, None)), ())
    assert group(scan) == [[0], [1]]


def test_ids_first_record_wins_a_conflict() -> None:
    ids = _folder("a", "1", None, ("2", "10.1/x")).ids()
    assert (ids.pmid, ids.doi) == ("1", "10.1/x")


def test_every_records_ids_count_for_find_and_group() -> None:
    # a's second record lost the PMID conflict to its first; b shares the loser.
    scan = Scan((_folder("a", "1", None, ("2", None)), _folder("b", "2", None)), ())
    assert group(scan) == [[0, 1]]
    assert [f.folder for f in find(scan, canonical_ids("2"))] == ["a", "b"]


def test_find_digest_refuses_a_malformed_digest() -> None:
    scan = Scan((), ())
    assert find_digest(scan, "ABC") is None
    assert find_digest(scan, "A" * 64) is None


def test_find_follows_a_chain_of_three_folders() -> None:
    # a-b share a DOI, b-c share a PMCID: a query for c's PMCID finds all three.
    def f(name: str, doi: str | None, pmcid: str | None) -> PaperFolder:
        rec = Record(
            Installation.new("studyanalyzer", "a", "Mac"),
            PaperInfo(canonical_ids(None, doi, pmcid)),
            (),
        )
        return PaperFolder(name, (rec,), ())

    scan = Scan(
        (
            f("a", "10.1/x", None),
            f("d", None, "PMC9"),
            f("b", "10.1/x", "PMC5"),
            f("c", None, "PMC5"),
        ),
        (),
    )
    got = find(scan, canonical_ids(None, None, "PMC5"))
    assert [x.folder for x in got] == ["a", "b", "c"]


def test_a_records_that_is_a_file_is_an_unreadable_folder(tmp_path: Path) -> None:
    lib = open_library(tmp_path / "lib") if (tmp_path / "lib").mkdir() is None else None
    assert lib is not None
    (lib.root / "papers" / "p").mkdir()
    (lib.root / "papers" / "p" / "records").write_text("x", encoding="utf-8")
    problems = lib.scan().problems
    assert [(p.kind, p.folder) for p in problems] == [("unreadable_folder", "p")]
