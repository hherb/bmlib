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

import os
import sys
from pathlib import Path

import pytest

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


_NOT_ON_WINDOWS = pytest.mark.skipif(
    os.name == "nt", reason="Windows names are UTF-16: bytes that are not UTF-8 cannot be faked"
)


@_NOT_ON_WINDOWS
def test_a_non_utf8_folder_is_a_stray_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #755: Rust reads the name lossily and so finds no folder by it; Python now does the
    # same rather than scan it. Faked, since APFS refuses such names.
    from bmlib.library import files

    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    real = files.os.listdir
    papers = lib.root / "papers"
    (papers / "caf\xe9").mkdir()  # the same characters as valid UTF-8: must not be reached
    monkeypatch.setattr(
        files.os,
        "listdir",
        lambda d: [os.fsdecode(b"caf\xe9")] if Path(d) == papers else real(d),
    )
    problems = lib.scan().problems
    assert [(p.kind, p.name) for p in problems] == [("stray_entry", "caf�")]


@_NOT_ON_WINDOWS
def test_a_non_utf8_folder_is_reported_beside_one_with_its_lossy_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #788 review: the lossy name of b"caf\xe9" is a real folder's "caf\ufffd"; joined to a
    # path it reached that folder, which was scanned twice and the other one dropped unreported.
    from bmlib.library import files

    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    real = files.os.listdir
    papers = lib.root / "papers"
    (papers / "caf\ufffd").mkdir()
    monkeypatch.setattr(
        files.os,
        "listdir",
        lambda d: [os.fsdecode(b"caf\xe9"), "caf\ufffd"] if Path(d) == papers else real(d),
    )
    scan = lib.scan()
    assert [f.folder for f in scan.folders] == ["caf\ufffd"]
    assert [(p.kind, p.name) for p in scan.problems] == [("stray_entry", "caf\ufffd")]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only Linux allows such names")
def test_a_non_utf8_folder_on_disk_is_reported_not_scanned(tmp_path: Path) -> None:
    # The same as above on a real file system, with each entry holding a file.
    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    papers = os.fsencode(lib.root / "papers")
    for name in (b"caf\xe9", "caf\ufffd".encode()):
        os.mkdir(papers + b"/" + name)
        with open(papers + b"/" + name + b"/x.pdf", "wb") as f:
            f.write(name)
    scan = lib.scan()
    assert [(f.folder, f.files_on_disk) for f in scan.folders] == [("caf\ufffd", ("x.pdf",))]
    assert [(p.kind, p.name) for p in scan.problems if p.kind == "stray_entry"] == [
        ("stray_entry", "caf\ufffd")
    ]


@_NOT_ON_WINDOWS
def test_non_utf8_names_inside_a_folder_are_each_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two names that are not UTF-8 read alike lossily; both are reported, neither is probed nor
    # listed as a file (#796), and a record whose name is not UTF-8 is unreadable rather than
    # looked up by its lossy name.
    from bmlib.library import files

    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    real = files.os.listdir
    folder = lib.root / "papers" / "p"
    (folder / "records").mkdir(parents=True)
    bad = [os.fsdecode(b"x\xe9"), os.fsdecode(b"x\xea")]
    monkeypatch.setattr(
        files.os,
        "listdir",
        lambda d: (
            [*bad, "records"]
            if Path(d) == folder
            else [os.fsdecode(b"r\xe9.json")]
            if Path(d) == folder / "records"
            else real(d)
        ),
    )
    scan = lib.scan()
    assert scan.folders[0].files_on_disk == ()
    assert [(p.kind, p.folder, p.name or p.file) for p in scan.problems] == [
        ("unreadable_record", "p", "r\ufffd.json"),
        ("undecodable_name", "p", "x\ufffd"),
        ("undecodable_name", "p", "x\ufffd"),
    ]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only Linux allows such names")
def test_a_non_utf8_file_is_not_listed_beside_one_with_its_lossy_name(tmp_path: Path) -> None:
    # #796: the lossy name of b"x\xe9.pdf" is a real sibling's "x\ufffd.pdf". Listed as a file,
    # it would be handed back to remove_file, which would delete the sibling instead.
    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    folder = os.fsencode(lib.root / "papers" / "p")
    os.makedirs(folder)
    for name in (b"x\xe9.pdf", "x\ufffd.pdf".encode()):
        with open(folder + b"/" + name, "wb") as f:
            f.write(name)
    scan = lib.scan()
    assert scan.folders[0].files_on_disk == ("x\ufffd.pdf",)
    assert [(p.kind, p.name) for p in scan.problems] == [
        ("undecodable_name", "x\ufffd.pdf"),
        ("unrecorded_file", "x\ufffd.pdf"),
    ]
