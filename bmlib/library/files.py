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


def _entries(directory: Path) -> list[str]:
    """Every name in *directory*, sorted by its raw bytes.

    A name that is not UTF-8 comes back surrogate-escaped (``os.listdir``'s way); it is only
    ever shown or compared, never turned into a path again except through ``os.fsencode``.
    """
    return sorted(os.listdir(directory), key=os.fsencode)


def list_names(directory: Path) -> list[str]:
    """Return the names in *directory*, sorted by bytes, without ignored or dot names."""
    return [n for n in _entries(directory) if not n.startswith(".") and n not in IGNORED_NAMES]


def _is_placeholder_name(name: str) -> bool:
    """Whether *name* is an iCloud placeholder, ``.<something>.icloud``."""
    return len(name) > len(".icloud") + 1 and name.startswith(".") and name.endswith(".icloud")


def first_user_entry(directory: Path) -> str | None:
    """Return the first entry of *directory* that belongs to the user, or ``None``.

    Ignored names and dotfiles do not count, but an iCloud placeholder does (it stands for a
    real file), and so does a name that is not UTF-8.
    """
    for n in _entries(directory):
        if _is_placeholder_name(n) or (not n.startswith(".") and n not in IGNORED_NAMES):
            return n
    return None


def is_dir(path: Path) -> bool:
    """Whether *path* is an existing directory."""
    return path.is_dir()


def exists(path: Path) -> bool:
    """Whether anything exists at *path*."""
    return path.exists()


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


def create_dir_all(path: Path) -> None:
    """Make the directory *path* and any missing parents; fine when it exists."""
    path.mkdir(parents=True, exist_ok=True)


def rename(source: Path, target: Path) -> None:
    """Rename *source* to *target*."""
    source.rename(target)


def write_replace(path: Path, data: bytes) -> None:
    """Publish *data* at *path* atomically, replacing what is there.

    Only for a file its writer alone owns (an installation's own record).
    """
    try:
        atomic_write(path, data)
    except OSError as e:
        raise io_error(path, e, write=True) from e


def remove_file(path: Path) -> None:
    """Delete one file."""
    path.unlink()


def remove_dir_all(path: Path) -> None:
    """Delete a directory and everything in it."""
    shutil.rmtree(path)
