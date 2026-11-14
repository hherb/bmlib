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

"""#769: a file a sync service evicted to a dataless file under its real name reads as not
downloaded, at once, and stays evicted. Rust's ``tests/library_dataless.rs`` is the twin.

The call sites are tested everywhere by making a read fail as a dataless one does. The device
test is opt-in, as it needs iCloud Drive and ``brctl``: set ``BMLIB_ICLOUD_DIR`` to a folder in
iCloud Drive.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from bmlib.library import (
    Installation,
    Library,
    NotDownloaded,
    PaperInfo,
    SourceKind,
    canonical_ids,
    files,
    open_library,
    save,
    scan,
)

_PAPER = PaperInfo(canonical_ids("111", None, None), "Metformin", 2024, "Smith")
_ME = Installation.new("studyanalyzer", "aaaa", "Mac")
_SF_DATALESS = 0x40000000
"""``SF_DATALESS`` in ``<sys/stat.h>``'s ``st_flags``."""
_UPLOAD_WAIT_S = 180
"""How long iCloud may take to upload a new file before it can be evicted."""


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    (tmp_path / "lib").mkdir()
    return open_library(tmp_path / "lib")


def _evicted(monkeypatch: pytest.MonkeyPatch, module: object, name: str) -> None:
    """Make *module*'s ``read_bytes`` fail as a dataless file does for a file called *name*."""
    real: Callable[[Path], bytes] = files.read_bytes

    def read(path: Path) -> bytes:
        if path.name == name:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided", str(path))
        return real(path)

    monkeypatch.setattr(files, "_APPLE", True)
    monkeypatch.setattr(module, "read_bytes", read)


def test_an_evicted_file_reads_as_not_downloaded(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = lib.save(_ME, _PAPER, b"%PDF a paper", SourceKind.PDF, "test")
    _evicted(monkeypatch, scan, ref.name)
    with pytest.raises(NotDownloaded) as e:
        lib.read(ref)
    assert (e.value.folder, e.value.name) == (ref.folder, ref.name)


def test_an_evicted_record_is_a_scan_problem_and_stops_a_save_and_a_release(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = lib.save(_ME, _PAPER, b"%PDF a paper", SourceKind.PDF, "test")
    record = _ME.record_file_name()
    _evicted(monkeypatch, scan, record)
    _evicted(monkeypatch, save, record)
    problems = lib.scan().problems
    assert ("record_not_downloaded", record) in [(p.kind, p.file) for p in problems]
    for act in (
        lambda: lib.save(_ME, _PAPER, b"%PDF another", SourceKind.PDF, "test"),
        lambda: lib.release(_ME, ref),
    ):
        with pytest.raises(NotDownloaded) as e:
            act()
        assert (e.value.folder, e.value.name) == (ref.folder, record)


def _dataless(path: Path) -> bool:
    return bool(os.stat(path).st_flags & _SF_DATALESS)


def _evict(path: Path) -> None:
    """Evict *path*, retrying while iCloud has not uploaded it yet."""
    start = time.monotonic()
    while not _dataless(path):
        subprocess.run(["brctl", "evict", str(path)], capture_output=True, check=False)
        assert time.monotonic() - start < _UPLOAD_WAIT_S, f"{path} was never evicted"
        time.sleep(2)


@pytest.mark.skipif(not os.environ.get("BMLIB_ICLOUD_DIR"), reason="set BMLIB_ICLOUD_DIR")
def test_on_icloud_drive_an_evicted_file_and_record_read_as_not_downloaded() -> None:
    root = Path(os.environ["BMLIB_ICLOUD_DIR"]) / f"bmlib-dataless-py-{os.getpid()}"
    root.mkdir(parents=True)
    lib = open_library(root)
    ref = lib.save(_ME, _PAPER, b"%PDF an evicted paper", SourceKind.PDF, "test")
    folder = root / "papers" / ref.folder

    source = folder / ref.name
    _evict(source)
    start = time.monotonic()
    with pytest.raises(NotDownloaded):
        lib.read(ref)
    assert time.monotonic() - start < 1, "the read waited"
    assert _dataless(source), "the read downloaded it"

    record = folder / "records" / _ME.record_file_name()
    _evict(record)
    assert ("record_not_downloaded", record.name) in [(p.kind, p.file) for p in lib.scan().problems]
    with pytest.raises(NotDownloaded):
        lib.release(_ME, ref)
    assert _dataless(record), "a read downloaded the record"

    shutil.rmtree(root)
