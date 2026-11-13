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

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from bmlib.library import BadName, FileClash
from bmlib.library.files import (
    Written,
    component,
    create_dir_new,
    first_user_entry,
    has_placeholder,
    list_names,
    sha256_hex,
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
