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

"""The only module of the library that touches the file system.

A platform without ordinary paths (phase 4's Android) swaps this one file.
Nothing here overwrites: :func:`write_once` publishes with
:func:`bmlib._atomic.atomic_write` and refuses a different file of the same name.
"""

from __future__ import annotations

import enum
import errno
import hashlib
import os
import shutil
from pathlib import Path

from bmlib._atomic import atomic_write
from bmlib.library.constants import ICLOUD_PLACEHOLDER, IGNORED_NAMES
from bmlib.library.errors import BadName, FileClash, Io, LibraryError, ReadOnly

_READ_ONLY_ERRNOS = (errno.EROFS, errno.EACCES, errno.EPERM)


class Written(enum.Enum):
    """Whether :func:`write_once` made the file or found the same bytes there."""

    NEW = "new"
    ALREADY_THERE = "already_there"


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def component(name: str) -> str:
    """Return *name* if it is one safe path component, else raise :class:`BadName`.

    Refuses the empty name, ``.``, ``..``, and any name holding ``/``, ``\\`` or
    NUL, so a name from a record or a caller can never leave its folder.
    """
    if name in ("", ".", "..") or any(c in name for c in "/\\\0"):
        raise BadName(name)
    return name


def io_error(path: Path, err: OSError, *, write: bool) -> LibraryError:
    """Map an OS error at *path* to :class:`ReadOnly` (a refused write) or :class:`Io`."""
    if write and err.errno in _READ_ONLY_ERRNOS:
        return ReadOnly(path)
    return Io(path, str(err))


def list_names(directory: Path) -> list[str]:
    """Return the names in *directory*, sorted by bytes, without ignored or dot names."""
    names = [n for n in os.listdir(directory) if not n.startswith(".") and n not in IGNORED_NAMES]
    return sorted(names, key=lambda n: n.encode())


def has_placeholder(directory: Path, name: str) -> bool:
    """Return whether *directory* holds iCloud's online-only placeholder for *name*."""
    return (directory / ICLOUD_PLACEHOLDER.format(name=name)).exists()


def read_bytes(path: Path) -> bytes:
    """Read a whole file."""
    return path.read_bytes()


def write_once(path: Path, data: bytes, sha256: str) -> Written:
    """Publish *data* at *path* unless a file is there.

    *sha256* is the digest of *data*. An existing file with that digest gives
    :attr:`Written.ALREADY_THERE`; one with another raises :class:`FileClash`
    and is left untouched.
    """
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        pass
    except OSError as e:
        raise io_error(path, e, write=False) from e
    else:
        if sha256_hex(existing) == sha256:
            return Written.ALREADY_THERE
        raise FileClash(str(path.parent), path.name)
    try:
        atomic_write(path, data)
    except OSError as e:
        raise io_error(path, e, write=True) from e
    return Written.NEW


def create_dir_new(path: Path) -> bool:
    """Make the directory *path* (its parent must exist); ``False`` if it exists."""
    try:
        path.mkdir()
    except FileExistsError:
        return False
    return True


def remove_file(path: Path) -> None:
    """Delete one file."""
    path.unlink()


def remove_dir_all(path: Path) -> None:
    """Delete a directory and everything in it."""
    shutil.rmtree(path)
