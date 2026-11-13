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

"""Saving, releasing and removing: the guarantees that need more than a fixture tree."""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from bmlib.library import (
    CORRUPT_SUFFIX,
    FileClash,
    FileRef,
    Installation,
    Library,
    Missing,
    NewerRecord,
    PaperInfo,
    ReadOnly,
    SourceKind,
    canonical_ids,
    open_library,
    parse_record,
    sha256_hex,
)

_PAPER = PaperInfo(canonical_ids("111", None, None), "Metformin", 2024, "Smith")
_BYTES = b"%PDF a paper"
_ME = Installation.new("studyanalyzer", "aaaa", "Mac")
_YOU = Installation.new("medicalfactchecker", "bbbb", "Mac")


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    (tmp_path / "lib").mkdir()
    return open_library(tmp_path / "lib")


def _record_path(lib: Library, who: Installation, folder: str) -> Path:
    return lib.root / "papers" / folder / "records" / who.record_file_name()


def _save_many(root: str, app: str, ident: str, times: int) -> None:
    """A worker process: save the same paper's bytes *times* times (module level to spawn)."""
    mine = open_library(Path(root))
    who = Installation.new(app, ident, "Mac")
    for _ in range(times):
        mine.save(who, _PAPER, _BYTES, SourceKind.PDF, "worker")


def test_save_same_bytes_twice_is_one_file(lib: Library) -> None:
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_save_many, args=(str(lib.root), app, ident, 20))
        for app, ident in (("studyanalyzer", "aaaa"), ("medicalfactchecker", "bbbb"))
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0, 0]
    scan = lib.scan()
    assert scan.problems == ()
    assert len(scan.folders) == 1
    (folder,) = scan.folders
    assert len(folder.files_on_disk) == 1
    assert sorted(r.installation.app for r in folder.records) == [
        "medicalfactchecker",
        "studyanalyzer",
    ]
    assert all(len(r.files) == 1 for r in folder.records)


def test_save_never_overwrites_other_bytes(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    target = lib.root / "papers" / first.folder / first.name
    target.write_bytes(b"planted")
    with pytest.raises(FileClash):
        lib.save(_YOU, _PAPER, _BYTES, SourceKind.PDF, "t")
    assert target.read_bytes() == b"planted"
    assert not _record_path(lib, _YOU, first.folder).exists()


def test_newer_own_record_is_not_downgraded(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    path = _record_path(lib, _ME, first.folder)
    newer = path.read_text(encoding="utf-8").replace('"version": 1', '"version": 2')
    path.write_text(newer, encoding="utf-8")
    before = _tree(lib.root)
    with pytest.raises(NewerRecord) as err:
        lib.save(_ME, _PAPER, b"%PDF other", SourceKind.PDF, "t")
    assert err.value.version == 2
    assert path.read_text(encoding="utf-8") == newer
    assert _tree(lib.root) == before  # no new folder, nothing else written


def test_corrupt_own_record_is_kept_aside(lib: Library, caplog: pytest.LogCaptureFixture) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    path = _record_path(lib, _ME, first.folder)
    path.write_text("{ not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="bmlib.library.save"):
        lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    aside = path.with_name(path.name + CORRUPT_SUFFIX)
    assert aside.read_text(encoding="utf-8") == "{ not json"
    assert len(parse_record(path.read_text(encoding="utf-8")).files) == 1
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_a_second_corrupt_record_does_not_replace_the_first_kept_aside(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    path = _record_path(lib, _ME, first.folder)
    for text in ("bad one", "bad two"):
        path.write_text(text, encoding="utf-8")
        lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    kept = sorted(p.read_text(encoding="utf-8") for p in path.parent.glob("*.corrupt*"))
    assert kept == ["bad one", "bad two"]


def test_an_identifier_conflict_is_logged_and_the_record_keeps_its_own(
    lib: Library, caplog: pytest.LogCaptureFixture
) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    other = PaperInfo(canonical_ids("111", "10.1/a", None))
    lib.save(_ME, other, b"two", SourceKind.TEXT, "t")
    clash = PaperInfo(canonical_ids("111", "10.1/b", None))
    with caplog.at_level(logging.WARNING, logger="bmlib.library.save"):
        lib.save(_ME, clash, b"three", SourceKind.TEXT, "t")
    rec = parse_record(_record_path(lib, _ME, first.folder).read_text(encoding="utf-8"))
    assert rec.paper.ids.doi == "10.1/a"
    assert any("doi" in r.getMessage() for r in caplog.records)


def test_a_folder_without_a_readable_record_is_used_not_duplicated(lib: Library) -> None:
    # Another writer has claimed the folder but not yet written its record.
    from bmlib.library import folder_name

    claimed = lib.root / "papers" / folder_name(_PAPER).upper()
    claimed.mkdir()
    got = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    assert got.folder == claimed.name
    assert [p.name for p in (lib.root / "papers").iterdir()] == [claimed.name]


def test_a_taken_name_with_other_identifiers_takes_the_next_suffix(lib: Library) -> None:
    def put(data: bytes) -> FileRef:
        return lib.save(_ME, PaperInfo(canonical_ids(), "T"), data, SourceKind.TEXT, "t")

    _, second, third = put(b"1"), put(b"2"), put(b"3")
    assert second.folder.endswith("(no identifier) (2)")
    assert third.folder.endswith("(no identifier) (3)")


def test_release_keeps_the_file_and_the_other_records(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    lib.save(_YOU, _PAPER, _BYTES, SourceKind.PDF, "t")
    lib.release(_ME, first)
    assert lib.read(first) == _BYTES
    mine = parse_record(_record_path(lib, _ME, first.folder).read_text(encoding="utf-8"))
    yours = parse_record(_record_path(lib, _YOU, first.folder).read_text(encoding="utf-8"))
    assert (len(mine.files), len(yours.files)) == (0, 1)


def test_the_manual_clear_refuses_paths_in_names(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    from bmlib.library import BadName, FileRef

    for bad in ("../x", "a/b", ""):
        with pytest.raises(BadName):
            lib.remove_folder(bad)
        with pytest.raises(BadName):
            lib.remove_file(FileRef(bad, first.name, first.sha256, first.kind))
        with pytest.raises(BadName):
            lib.remove_file(FileRef(first.folder, bad, first.sha256, first.kind))
        with pytest.raises(BadName):
            lib.release(_ME, FileRef(first.folder, bad, first.sha256, first.kind))
    assert lib.read(first) == _BYTES


def test_removing_what_is_not_there_is_missing(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    lib.remove_file(first)
    with pytest.raises(Missing):
        lib.remove_file(first)
    lib.remove_folder(first.folder)
    with pytest.raises(Missing):
        lib.remove_folder(first.folder)
    assert lib.scan().folders == ()


def test_remove_folder_of_a_regular_file_is_missing(lib: Library) -> None:
    (lib.root / "papers" / "stray").write_text("x", encoding="utf-8")
    with pytest.raises(Missing):
        lib.remove_folder("stray")
    assert (lib.root / "papers" / "stray").exists()


def test_release_of_an_unlisted_file_or_without_a_record_is_fine(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    lib.release(_YOU, first)  # no record of YOU
    lib.release(_ME, first)
    lib.release(_ME, first)  # no longer listed
    assert lib.read(first) == _BYTES


def test_a_folder_with_only_a_newer_record_fails_the_claim_and_writes_nothing(
    lib: Library,
) -> None:
    first = lib.save(_YOU, _PAPER, _BYTES, SourceKind.PDF, "t")
    path = _record_path(lib, _YOU, first.folder)
    path.write_text(path.read_text(encoding="utf-8").replace('"version": 1', '"version": 2'))
    before = _tree(lib.root)
    with pytest.raises(NewerRecord) as err:
        lib.save(_ME, _PAPER, b"other", SourceKind.PDF, "t")
    assert err.value.folder == first.folder
    assert _tree(lib.root) == before


def _tree(root: Path) -> list[tuple[str, bytes | None]]:
    return sorted((str(p), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))


def _save_one(lib: Library, name: bytes, kind: SourceKind, barrier: threading.Barrier) -> None:
    barrier.wait()
    lib.save(_ME, _PAPER, name, kind, "thread")


def test_two_threads_of_one_installation_both_land_in_the_record(lib: Library) -> None:
    for i in range(25):
        barrier = threading.Barrier(2)
        a = threading.Thread(
            target=_save_one, args=(lib, f"pdf {i}".encode(), SourceKind.PDF, barrier)
        )
        b = threading.Thread(
            target=_save_one, args=(lib, f"text {i}".encode(), SourceKind.TEXT, barrier)
        )
        a.start()
        b.start()
        a.join()
        b.join()
        (folder,) = lib.scan().folders
        (rec,) = folder.records
        assert len(rec.files) == 2 * (i + 1)


def test_the_saved_ref_carries_the_full_digest(lib: Library) -> None:
    got = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    assert got.sha256 == sha256_hex(_BYTES)
    assert got.name == f"pdf-{got.sha256[:12]}.pdf"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_read_only_library_says_so(lib: Library) -> None:
    papers = lib.root / "papers"
    papers.chmod(0o555)
    try:
        with pytest.raises(ReadOnly):
            lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    finally:
        papers.chmod(0o755)
    assert list(papers.iterdir()) == []
