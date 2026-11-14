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

import contextlib
import errno
import logging
import multiprocessing
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from bmlib.library import (
    CORRUPT_SUFFIX,
    FileClash,
    FileRef,
    Installation,
    Io,
    Library,
    Missing,
    NewerRecord,
    PaperInfo,
    ReadOnly,
    SourceKind,
    Unreachable,
    canonical_ids,
    files,
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


def test_save_and_release_refuse_unchecked_fields_and_write_nothing(lib: Library) -> None:
    from bmlib.library import BadIdentifier, BadInstallation, LibraryError, PaperIds

    before = _tree(lib.root)
    with pytest.raises(BadIdentifier) as err:
        lib.save(_ME, PaperInfo(PaperIds(doi="10.1/AbC")), _BYTES, SourceKind.PDF, "t")
    assert (err.value.field, err.value.value) == ("doi", "10.1/AbC")
    # A dataclass can be built without ``new``; its record name would climb out of papers/.
    escaped = Installation("../../../escaped", "x", "x")
    with pytest.raises(BadInstallation) as bad:
        lib.save(escaped, _PAPER, _BYTES, SourceKind.PDF, "t")
    assert bad.value.field == "app"
    # A lone surrogate cannot be written as UTF-8: a LibraryError, not UnicodeEncodeError.
    with pytest.raises(LibraryError):
        lib.save(_ME, PaperInfo(_PAPER.ids, "bad \ud800 title"), _BYTES, SourceKind.PDF, "t")
    assert _tree(lib.root) == before
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    saved = _tree(lib.root)
    with pytest.raises(BadInstallation):
        lib.release(escaped, first)
    assert _tree(lib.root) == saved


def test_an_evicted_own_record_is_not_downloaded_and_nothing_is_written(lib: Library) -> None:
    # iCloud's Optimize Storage left only ".<record>.icloud": a fresh record would replace the
    # real one when it comes back, losing its entries.
    from bmlib.library import NotDownloaded

    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    path = _record_path(lib, _ME, first.folder)
    path.rename(path.with_name(f".{path.name}.icloud"))
    before = _tree(lib.root)
    with pytest.raises(NotDownloaded) as err:
        lib.save(_ME, _PAPER, b"%PDF other", SourceKind.PDF, "t")
    assert err.value.name == _ME.record_file_name()
    with pytest.raises(NotDownloaded):
        lib.release(_ME, first)
    assert _tree(lib.root) == before


def test_record_use_checks_its_inputs_and_the_file(lib: Library) -> None:
    from bmlib.library import BadIdentifier, BadInstallation, BadName, PaperIds

    theirs = lib.save(_YOU, _PAPER, _BYTES, SourceKind.PDF, "t")
    before = _tree(lib.root)
    with pytest.raises(BadInstallation):
        lib.record_use(Installation("../x", "aaaa", "Mac"), _PAPER, theirs)
    with pytest.raises(BadIdentifier):
        lib.record_use(_ME, PaperInfo(PaperIds(pmid="111", doi="10.1/X")), theirs)
    # A name that is not the library's name for those bytes is refused.
    folder = lib.root / "papers" / theirs.folder
    (folder / "paper.pdf").write_bytes(_BYTES)
    with pytest.raises(BadName):
        lib.record_use(_ME, _PAPER, FileRef(theirs.folder, "paper.pdf", theirs.sha256, theirs.kind))
    (folder / "paper.pdf").unlink()
    assert _tree(lib.root) == before
    lib.remove_file(theirs)
    with pytest.raises(Missing):
        lib.record_use(_ME, _PAPER, theirs)
    assert not _record_path(lib, _ME, theirs.folder).exists()


def test_every_name_a_save_makes_is_synced_into_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #768: a caller drops its own copy once save returns, so each new entry (the folder in
    # papers/, the file and records/ in the folder, the record in records/) must have had its
    # directory synced after it appeared. The second save finds the bytes there and must still
    # sync them, since they may be another writer's not yet synced.
    real = files.sync_dir
    synced: list[tuple[Path, set[str]]] = []

    def spy(directory: Path) -> None:
        synced.append((directory, set(os.listdir(directory))))
        real(directory)

    monkeypatch.setattr(files, "sync_dir", spy)
    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    root = lib.root
    assert (root, {"bmlib-library.json", "papers"}) in synced
    synced.clear()
    ref = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "test")
    folder = root / "papers" / ref.folder
    made = {(root / "papers", ref.folder), (folder, ref.name), (folder, "records")}
    made.add((folder / "records", _ME.record_file_name()))
    for directory, name in made:
        assert any(d == directory and name in seen for d, seen in synced), (directory, name)
    synced.clear()
    lib.save(_YOU, _PAPER, _BYTES, SourceKind.PDF, "test")
    assert any(d == folder and ref.name in seen for d, seen in synced)


def _sync_spy(
    monkeypatch: pytest.MonkeyPatch, fail_at: int | None = None
) -> list[tuple[Path, set[str]]]:
    """Log each directory synced with its names then; the call numbered *fail_at* raises EIO."""
    real = files.sync_dir
    synced: list[tuple[Path, set[str]]] = []

    def spy(directory: Path) -> None:
        synced.append((directory, set(os.listdir(directory))))
        if len(synced) - 1 == fail_at:
            raise OSError(errno.EIO, "injected")
        real(directory)

    monkeypatch.setattr(files, "sync_dir", spy)
    return synced


def test_a_failed_sync_fails_the_save_and_a_retry_syncs_what_it_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #768: the caller keeps its copy unless save returns, so every sync a save makes must fail
    # it when it fails. A retry then finds the folder, records/ and the record already there,
    # and must still sync each into its directory before it returns.
    (tmp_path / "count").mkdir()
    lib = open_library(tmp_path / "count")
    synced = _sync_spy(monkeypatch)
    lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "test")
    calls = len(synced)
    assert calls >= 4
    for at in range(calls):
        (tmp_path / str(at)).mkdir()
        lib = open_library(tmp_path / str(at))
        _sync_spy(monkeypatch, fail_at=at)
        with pytest.raises(Io):
            lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "test")
        synced = _sync_spy(monkeypatch)
        ref = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "test")
        folder = lib.root / "papers" / ref.folder
        made = {(lib.root / "papers", ref.folder), (folder, ref.name), (folder, "records")}
        made.add((folder / "records", _ME.record_file_name()))
        for directory, name in made:
            assert any(d == directory and name in seen for d, seen in synced), (at, name)


def test_a_failed_sync_names_the_file_and_the_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The file is published before its directory is synced; the error must say which step
    # failed, or a caller cannot tell a lost write from an unsynced one.
    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    real = files.sync_dir

    def refuse(directory: Path) -> None:
        if directory.parent.name == "papers" and not (directory / "records").exists():
            raise OSError(errno.ENOTSUP, "Operation not supported")
        real(directory)

    monkeypatch.setattr(files, "sync_dir", refuse)
    with pytest.raises(Io) as caught:
        lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "test")
    assert caught.value.path.parent.parent.name == "papers"
    assert caught.value.reason.startswith("syncing its folder: ")
    assert "Operation not supported" in caught.value.reason


_NO_PERMISSIONS = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="root ignores directory permissions"
)


@contextlib.contextmanager
def _unsearchable(directory: Path) -> Iterator[None]:
    """*directory* listable but not searchable: every lstat of a name in it fails (EACCES)."""
    directory.chmod(0o600)
    try:
        yield
    finally:
        directory.chmod(0o755)


@_NO_PERMISSIONS
def test_a_folder_that_cannot_be_examined_is_reported_not_read_as_empty(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    with _unsearchable(lib.root / "papers" / first.folder):
        scan = lib.scan()
    assert [f.folder for f in scan.folders] == [first.folder]
    reasons = [p.reason for p in scan.problems if p.kind == "unreadable_folder"]
    assert len(reasons) == 1 and reasons[0] is not None
    assert reasons[0].startswith("records: ") and "Permission denied" in reasons[0]


@_NO_PERMISSIONS
def test_a_paper_folder_that_cannot_be_examined_is_unreadable_not_stray(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    with _unsearchable(lib.root / "papers"):
        problems = lib.scan().problems
    assert [(p.kind, p.folder) for p in problems] == [("unreadable_folder", first.folder)]


@_NO_PERMISSIONS
def test_a_taken_name_that_cannot_be_examined_fails_the_save(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    papers = lib.root / "papers"
    with _unsearchable(papers), pytest.raises(Io) as err:
        lib.save(_ME, _PAPER, b"%PDF other bytes", SourceKind.PDF, "t")
    assert err.value.path == papers / first.folder
    assert sorted(n.name for n in papers.iterdir()) == [first.folder]


@_NO_PERMISSIONS
def test_removing_a_folder_that_cannot_be_examined_is_not_missing(lib: Library) -> None:
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    with _unsearchable(lib.root / "papers"), pytest.raises(ReadOnly):
        lib.remove_folder(first.folder)
    assert lib.read(first) == _BYTES


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need Developer Mode")
def test_a_library_reached_through_a_link_is_used(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "lib").symlink_to(tmp_path / "real", target_is_directory=True)
    lib = open_library(tmp_path / "lib")
    first = lib.save(_ME, _PAPER, _BYTES, SourceKind.PDF, "t")
    assert lib.read(first) == _BYTES
    assert [f.folder for f in lib.scan().folders] == [first.folder]
    assert open_library(tmp_path / "lib").scan().problems == ()
    assert (tmp_path / "real" / "papers" / first.folder / first.name).is_file()


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need Developer Mode")
def test_an_unplugged_drive_behind_the_root_link_is_no_such_folder(tmp_path: Path) -> None:
    (tmp_path / "lib").symlink_to(tmp_path / "gone", target_is_directory=True)
    with pytest.raises(Unreachable) as err:
        open_library(tmp_path / "lib")
    assert err.value.reason == "no such folder"
