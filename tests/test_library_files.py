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

"""Tests for the library's file-operations module."""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest

from bmlib.library import BadName, FileClash
from bmlib.library import files as files_module
from bmlib.library.files import (
    NOT_A_FOLDER,
    NOT_A_REGULAR_FILE,
    Written,
    component,
    create_dir_new,
    exists,
    first_user_entry,
    has_placeholder,
    is_dir,
    is_temp_name,
    list_names,
    read_bytes,
    sha256_hex,
    sync_dir,
    write_once,
)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        ".",
        "..",
        "a/b",
        "a\\b",
        "../x",
        "x\0",
        "...",
        " ",
        ". ",
        "x.",
        "C:",
        "a:b",
        "con",
        "NUL.txt",
    ],
)
def test_component_refuses_paths(bad: str) -> None:
    with pytest.raises(BadName):
        component(bad)


def test_component_accepts_a_plain_name() -> None:
    assert component("2024 Smith") == "2024 Smith"


def test_write_once_never_overwrites(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    assert write_once(f, b"one", sha256_hex(b"one")) is Written.NEW
    assert write_once(f, b"one", sha256_hex(b"one")) is Written.ALREADY_THERE
    with pytest.raises(FileClash):
        write_once(f, b"two", sha256_hex(b"two"))
    assert f.read_bytes() == b"one"


def test_write_once_publishes_atomically(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    real = os.replace
    seen: list[bool] = []

    def spy(src: object, dst: object) -> None:
        seen.append(f.exists())
        real(src, dst)  # type: ignore[arg-type]

    with mock.patch("os.replace", spy):
        write_once(f, b"x", sha256_hex(b"x"))
    assert seen == [False]
    assert f.read_bytes() == b"x"


def test_list_names_sorts_and_skips_ignored(tmp_path: Path) -> None:
    for n in ["b", "a", ".DS_Store", ".hidden", "Thumbs.db", "C"]:
        (tmp_path / n).write_bytes(b"")
    assert list_names(tmp_path) == ["C", "a", "b"]
    (tmp_path / ".x.pdf.icloud").write_bytes(b"")
    assert has_placeholder(tmp_path, "x.pdf")
    assert not has_placeholder(tmp_path, "y.pdf")


def test_create_dir_new_reports_existing(tmp_path: Path) -> None:
    assert create_dir_new(tmp_path / "p") is True
    assert create_dir_new(tmp_path / "p") is False


def test_create_dir_new_syncs_a_directory_found_there(tmp_path: Path) -> None:
    # #768: one found there may be another writer's, not yet synced into its parent.
    (tmp_path / "p").mkdir()
    with mock.patch("bmlib.library.files.sync_dir") as synced:
        assert create_dir_new(tmp_path / "p") is False
    synced.assert_called_once_with(tmp_path)


def test_create_dir_new_refuses_a_file_in_the_way(tmp_path: Path) -> None:
    (tmp_path / "p").write_bytes(b"")
    with pytest.raises(OSError) as err:
        create_dir_new(tmp_path / "p")
    assert str(err.value) == NOT_A_FOLDER


def test_sha256_hex() -> None:
    assert sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_first_user_entry_counts_placeholders_not_dotfiles(tmp_path: Path) -> None:
    for n in [".DS_Store", ".hidden", "Thumbs.db"]:
        (tmp_path / n).write_bytes(b"")
    assert first_user_entry(tmp_path) is None
    (tmp_path / ".x.pdf.icloud").write_bytes(b"")
    assert first_user_entry(tmp_path) == ".x.pdf.icloud"


@pytest.mark.skipif(sys.platform != "linux", reason="APFS and NTFS refuse non-UTF-8 names")
def test_non_utf8_name_is_a_user_entry(tmp_path: Path) -> None:
    try:
        (tmp_path / os.fsdecode(b"caf\xe9.pdf")).write_bytes(b"")
    except OSError:
        pytest.skip("this file system refuses non-UTF-8 names")
    assert first_user_entry(tmp_path) is not None
    assert len(list_names(tmp_path)) == 1


def test_temp_names_of_both_languages_are_recognised() -> None:
    rust = "a.pdf.bmlib-tmp-uuid4hex" + "0" * 42
    assert is_temp_name(rust)
    assert is_temp_name(".a.pdf.0123456789abcdef0123456789abcdef.tmp")
    for name in [
        "a.pdf",
        rust[:-1],
        ".a.pdf.0123456789ABCDEF0123456789abcdef.tmp",
        "a.pdf.0123456789abcdef0123456789abcdef.tmp",
        ".0123456789abcdef0123456789abcdef.tmp",
    ]:
        assert not is_temp_name(name), name


@pytest.mark.skipif(os.name == "nt", reason="a directory cannot be opened to sync on Windows")
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_sync_dir_flushes_the_directory_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    # #768: the directory's own descriptor is flushed, by F_FULLFSYNC on macOS as Rust's
    # sync_all does there, by fsync elsewhere. Both branches run on every platform CI has.
    import fcntl

    monkeypatch.setattr(fcntl, "F_FULLFSYNC", 51, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    calls: list[tuple[str, int, tuple[object, ...]]] = []

    def fake(name: str) -> Callable[..., int]:
        def record(fd: int, *rest: object) -> int:
            calls.append((name, os.fstat(fd).st_ino, rest))
            return 0

        return record

    monkeypatch.setattr(fcntl, "fcntl", fake("fcntl"))
    monkeypatch.setattr(os, "fsync", fake("fsync"))
    sync_dir(tmp_path)
    ino = tmp_path.stat().st_ino
    if platform == "darwin":
        assert calls == [("fcntl", ino, (fcntl.F_FULLFSYNC,))]
    else:
        assert calls == [("fsync", ino, ())]


def test_sync_dir_is_a_no_op_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "nt")
    with mock.patch("os.open") as opened:
        sync_dir(tmp_path)
    opened.assert_not_called()


_APPLE = sys.platform in ("darwin", "ios")


@pytest.mark.skipif(not _APPLE, reason="setiopolicy_np is Apple's")
def test_a_read_turns_materialisation_off_and_back(tmp_path: Path) -> None:
    """#769: a read turns materialisation off on its own thread only while it runs."""
    from bmlib.library import files

    def current() -> int:
        return files._libc.getiopolicy_np(files._IOPOL_TYPE_DATALESS, files._IOPOL_SCOPE_THREAD)

    path = tmp_path / "x.pdf"
    path.write_bytes(b"one")
    before = current()
    with files._materialisation_off(path):
        assert current() == files._IOPOL_DATALESS_OFF
    assert current() == before
    assert files.read_bytes(path) == b"one"
    assert current() == before


def test_edeadlk_reads_as_not_downloaded_on_apple_only() -> None:
    """#769: a dataless read's EDEADLK is "not downloaded", and io_error says so."""
    from bmlib.library import Io, NotDownloaded
    from bmlib.library.files import io_error, is_not_downloaded

    err = OSError(errno.EDEADLK, "Resource deadlock avoided")
    assert is_not_downloaded(err) == _APPLE
    assert not is_not_downloaded(FileNotFoundError(errno.ENOENT, "gone"))
    mapped = io_error(Path("lib/.bmlib-library.json"), err, write=False)
    if _APPLE:
        assert isinstance(mapped, NotDownloaded)
        assert (mapped.folder, mapped.name) == ("lib", ".bmlib-library.json")
    else:
        assert isinstance(mapped, Io)


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="root ignores directory permissions"
)
def test_a_path_that_cannot_be_examined_is_an_error_not_absent(tmp_path: Path) -> None:
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d").chmod(0o600)  # listable, not searchable
    try:
        for check in (is_dir, exists):
            with pytest.raises(PermissionError):
                check(tmp_path / "d" / "sub")
        with pytest.raises(PermissionError):
            create_dir_new(tmp_path / "d" / "sub")
    finally:
        (tmp_path / "d").chmod(0o755)
    assert not is_dir(tmp_path / "nothing") and not exists(tmp_path / "nothing")
    assert not exists(tmp_path / "nothing" / "below")


@pytest.mark.skipif(os.name == "nt", reason="the open file's identity is checked off Windows")
def test_a_file_swapped_in_after_the_lstat_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mine").write_bytes(b"mine")
    (tmp_path / "theirs").write_bytes(b"theirs")
    real_fstat = os.fstat
    theirs = os.stat(tmp_path / "theirs")
    # What the open finds is another regular file: a link swapped in after the lstat.
    monkeypatch.setattr(files_module.os, "fstat", lambda fd: theirs)
    with pytest.raises(OSError) as err:
        read_bytes(tmp_path / "mine")
    assert str(err.value) == NOT_A_REGULAR_FILE
    monkeypatch.setattr(files_module.os, "fstat", real_fstat)
    assert read_bytes(tmp_path / "mine") == b"mine"


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_a_junction_is_not_a_folder(tmp_path: Path) -> None:
    import _winapi  # type: ignore[import-not-found]  # Windows only, CPython's own

    (tmp_path / "target").mkdir()
    _winapi.CreateJunction(str(tmp_path / "target"), str(tmp_path / "junction"))
    assert is_dir(tmp_path / "target")
    assert not is_dir(tmp_path / "junction")
    assert exists(tmp_path / "junction")
