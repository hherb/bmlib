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

from bmlib._atomic import atomic_write
from bmlib.library.constants import LIBRARY_FORMAT, LIBRARY_VERSION, MARKER_FILE, PAPERS_DIR
from bmlib.library.errors import NewerLibrary, NotALibrary, Unreachable
from bmlib.library.files import create_dir_new, io_error, list_names, read_bytes


class Library:
    """An opened library folder; later tasks add scan, read, save and remove."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """The library's folder."""
        return self._root


def open_library(root: Path) -> Library:
    """Open the library at *root*, creating the marker and ``papers/`` in an empty folder.

    :raises Unreachable: *root* is missing or not a folder.
    :raises NotALibrary: it holds anything but a valid marker (or only ignored names).
    :raises NewerLibrary: the marker's version is newer than ``LIBRARY_VERSION``.
    :raises ReadOnly: the marker or ``papers/`` cannot be written.
    """
    if not root.is_dir():
        reason = "not a folder" if root.exists() else "no such folder"
        raise Unreachable(root, reason)
    marker = root / MARKER_FILE
    if marker.exists():
        try:
            value = json.loads(read_bytes(marker))
        except ValueError as e:
            raise NotALibrary(root, f"{MARKER_FILE} is not JSON: {e}") from e
        except OSError as e:
            raise io_error(marker, e, write=False) from e
        if not isinstance(value, dict) or value.get("format") != LIBRARY_FORMAT:
            raise NotALibrary(root, f"{MARKER_FILE} is not a {LIBRARY_FORMAT} marker")
        version = value.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise NotALibrary(root, f"{MARKER_FILE} has no version")
        if version > LIBRARY_VERSION:
            raise NewerLibrary(version)
    else:
        try:
            names = list_names(root)
        except OSError as e:
            raise io_error(root, e, write=False) from e
        if names:
            raise NotALibrary(
                root, f"the folder is not empty (found {names[0]}) and has no {MARKER_FILE}"
            )
        text = json.dumps(
            {"format": LIBRARY_FORMAT, "version": LIBRARY_VERSION}, indent=2, ensure_ascii=False
        )
        try:
            atomic_write(marker, (text + "\n").encode("utf-8"))
        except OSError as e:
            raise io_error(marker, e, write=True) from e
    papers = root / PAPERS_DIR
    try:
        create_dir_new(papers)
    except OSError as e:
        raise io_error(papers, e, write=True) from e
    return Library(root)
