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

"""A user-chosen folder of papers, opened and checked."""

from __future__ import annotations

import json
from pathlib import Path

from bmlib._strict_json import strict_json_loads
from bmlib.library.constants import LIBRARY_FORMAT, LIBRARY_VERSION, MARKER_FILE, PAPERS_DIR
from bmlib.library.errors import FileClash, NewerLibrary, NotALibrary, Unreachable
from bmlib.library.files import (
    create_dir_new,
    exists,
    first_user_entry,
    io_error,
    is_dir,
    read_bytes,
    sha256_hex,
    write_once,
)
from bmlib.library.ids import Installation, PaperInfo, SourceKind
from bmlib.library.save import (
    record_file_use,
    release_file,
    remove_paper_folder,
    remove_source_file,
    save_file,
)
from bmlib.library.scan import FileRef, Scan, read_file, scan_library

_U32_MAX = 2**32 - 1


class Library:
    """An opened library folder."""

    def __init__(self, root: Path) -> None:
        """Wrap *root* as is; use :func:`open_library`, which checks it."""
        self._root = root

    @property
    def root(self) -> Path:
        """The library's folder."""
        return self._root

    def scan(self) -> Scan:
        """Read every paper folder; trouble inside one is a :class:`Problem`. Never writes."""
        return scan_library(self._root)

    def read(self, file: FileRef) -> bytes:
        """Read a file's bytes, checking them against ``file.sha256`` (see :func:`read_file`)."""
        return read_file(self._root, file)

    def save(
        self,
        who: Installation,
        paper: PaperInfo,
        data: bytes,
        kind: SourceKind,
        origin: str,
    ) -> FileRef:
        """Save *data* for *paper* and note it in *who*'s record (see :func:`save_file`).

        Never overwrites: a file of that name with other bytes raises :class:`FileClash`.
        """
        return save_file(self._root, who, paper, data, kind, origin)

    def record_use(self, who: Installation, paper: PaperInfo, file: FileRef) -> None:
        """Note in *who*'s record that it uses *file*, already in the library, merging *paper*
        as :meth:`save` does (see :func:`record_file_use`)."""
        record_file_use(self._root, who, paper, file)

    def release(self, who: Installation, file: FileRef) -> None:
        """Drop *file* from *who*'s record only; the file stays (see :func:`release_file`)."""
        release_file(self._root, who, file)

    def remove_file(self, file: FileRef) -> None:
        """Delete one source file, the manual clear (see :func:`remove_source_file`)."""
        remove_source_file(self._root, file)

    def remove_folder(self, folder: str) -> None:
        """Delete a paper's whole folder, the manual clear (see :func:`remove_paper_folder`)."""
        remove_paper_folder(self._root, folder)


def _check_marker(root: Path, data: bytes) -> None:
    """Validate a marker's bytes: JSON, the right format, and a version that is a positive
    integer within ``u32`` (the rule records use) no newer than ours."""
    try:
        value = strict_json_loads(data.decode("utf-8"))
    except ValueError as e:
        raise NotALibrary(root, f"{MARKER_FILE} is not JSON: {e}") from e
    if not isinstance(value, dict) or value.get("format") != LIBRARY_FORMAT:
        raise NotALibrary(root, f"{MARKER_FILE} is not a {LIBRARY_FORMAT} marker")
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= _U32_MAX:
        raise NotALibrary(root, f"{MARKER_FILE}: version must be a positive integer")
    if version > LIBRARY_VERSION:
        raise NewerLibrary(version)


def _read_marker(marker: Path) -> bytes:
    """The marker file's bytes.

    :raises Io: it cannot be read.
    """
    try:
        return read_bytes(marker)
    except OSError as e:
        raise io_error(marker, e, write=False) from e


def open_library(root: Path) -> Library:
    """Open the library at *root*, creating the marker and ``papers/`` in an empty folder.

    :raises Unreachable: *root* is missing or not a folder.
    :raises NotALibrary: it holds anything but a valid marker (or only ignored names).
    :raises NewerLibrary: the marker's version is newer than ``LIBRARY_VERSION``.
    :raises ReadOnly: the marker or ``papers/`` cannot be written.
    """
    if not is_dir(root):
        raise Unreachable(root, "not a folder" if exists(root) else "no such folder")
    marker = root / MARKER_FILE
    first = None
    if not exists(marker):
        try:
            first = first_user_entry(root)
        except OSError as e:
            raise io_error(root, e, write=False) from e
    # The marker is looked at again after the folder seemed taken: another opener may have
    # published it (and papers/) in between.
    if exists(marker):
        _check_marker(root, _read_marker(marker))
    elif first is not None:
        raise NotALibrary(root, f"the folder is not empty (found {first}) and has no {MARKER_FILE}")
    else:
        text = json.dumps(
            {"format": LIBRARY_FORMAT, "version": LIBRARY_VERSION}, indent=2, ensure_ascii=False
        )
        data = (text + "\n").encode("utf-8")
        try:
            write_once(marker, data, sha256_hex(data))
        except FileClash:
            # Another opener published a marker first: it decides, not ours.
            _check_marker(root, _read_marker(marker))
    papers = root / PAPERS_DIR
    try:
        create_dir_new(papers)
    except OSError as e:
        raise io_error(papers, e, write=True) from e
    return Library(root)
