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
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bmlib._atomic import atomic_write
from bmlib.library.constants import (
    ICLOUD_PLACEHOLDER,
    IGNORED_NAMES,
    PY_TEMP_EXT,
    PY_TEMP_HEX,
    RUST_TEMP_SUFFIX,
    RUST_TEMP_TOKEN_LEN,
)
from bmlib.library.errors import BadName, FileClash, Io, LibraryError, ReadOnly
from bmlib.library.names import is_reserved

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

    Refuses, on every platform, the empty name, any name holding ``/``, ``\\``, NUL or ``:``,
    a name ending in ``.`` or a space or starting with a space, and a Windows device name
    (``con``, ``NUL.txt``). Windows drops trailing dots and spaces (``...`` and ``x.`` would
    name ``papers`` itself or ``x``), ``C:`` replaces a joined path and ``a:b`` names a stream,
    so a name from a record or a caller can never reach anything but its own entry. The
    library's own names never take these shapes (:func:`folder_name` sanitises).
    """
    if (
        name == ""
        or any(c in name for c in "/\\\0:")
        or name.endswith((".", " "))
        or name.startswith(" ")
        or is_reserved(name)
    ):
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


_HEX = frozenset("0123456789abcdef")


def is_temp_name(name: str) -> bool:
    """Whether *name* is a temporary of either language's ``atomic_write``.

    Rust's is ``{name}{RUST_TEMP_SUFFIX}{42-character token}``, Python's
    ``.{name}.{32 hex}.tmp``. One is there while a write is in flight, and stays after a
    crash; it is never a user's file and never a paper's.
    """
    head = name[: len(name) - RUST_TEMP_TOKEN_LEN]
    rust = len(name) > len(RUST_TEMP_SUFFIX) + RUST_TEMP_TOKEN_LEN and head.endswith(
        RUST_TEMP_SUFFIX
    )
    stem = name[: -len(PY_TEMP_EXT)]
    tail = stem[-PY_TEMP_HEX - 1 :]
    python = (
        name.startswith(".")
        and name.endswith(PY_TEMP_EXT)
        and len(stem) > PY_TEMP_HEX + 2
        and tail.startswith(".")
        and set(tail[1:]) <= _HEX
    )
    return rust or python


@dataclass(frozen=True)
class Listing:
    """What :func:`listing` found in a folder, each list sorted by bytes.

    ``names`` are those a scan looks at (not ignored, not starting with ``.``);
    ``placeholders`` the files present only as an iCloud placeholder, by their own name
    (``.x.pdf.icloud`` gives ``x.pdf``); ``temps`` the ``atomic_write`` temporaries
    (:func:`is_temp_name`), in flight or left by a crash.
    """

    names: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    temps: list[str] = field(default_factory=list)


def listing(directory: Path) -> Listing:
    """Return the entries of *directory*, sorted into a :class:`Listing`."""
    out = Listing()
    for n in _entries(directory):
        own = _placeholder_for(n)
        if is_temp_name(n):
            out.temps.append(n)
        elif own is not None:
            out.placeholders.append(own)
        elif not n.startswith(".") and n not in IGNORED_NAMES:
            out.names.append(n)
    return out


def list_names(directory: Path) -> list[str]:
    """Return the names in *directory*, sorted by bytes, without ignored or dot names or
    ``atomic_write`` temporaries."""
    return listing(directory).names


def _placeholder_for(name: str) -> str | None:
    """The file an iCloud placeholder name (``.<name>.icloud``) stands for, or ``None``."""
    pre, post = ICLOUD_PLACEHOLDER.split("{name}")
    if name.startswith(pre) and name.endswith(post) and len(name) > len(pre) + len(post):
        return name[len(pre) : len(name) - len(post)]
    return None


def _is_placeholder_name(name: str) -> bool:
    """Whether *name* is an iCloud placeholder, ``.<something>.icloud``."""
    return _placeholder_for(name) is not None


def first_user_entry(directory: Path) -> str | None:
    """Return the first entry of *directory* that belongs to the user, or ``None``.

    Ignored names, dotfiles and ``atomic_write`` temporaries do not count, but an iCloud
    placeholder does (it stands for a real file), and so does a name that is not UTF-8.
    """
    for n in _entries(directory):
        if is_temp_name(n):
            continue
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
            sync_name(path)  # another writer's, perhaps not yet synced
            return Written.ALREADY_THERE
        raise FileClash(str(path.parent), path.name)
    _publish(path, data)
    return Written.NEW


def sync_dir(directory: Path) -> None:
    """Flush *directory*'s entries to disk, so that a name published or a directory made in it
    survives a crash. ``atomic_write`` syncs a file's data, not its name, and a caller may drop
    its own copy once a save returns (#768).

    On macOS this is ``F_FULLFSYNC``, as Rust's ``sync_all`` is there. It also flushes the
    drive's cache, and so covers the data ``atomic_write`` synced with a plain ``fsync``.
    """
    # shortcut: a no-op on Windows, where a directory cannot be opened to sync it; the rename
    # is as durable as NTFS makes it. Use MoveFileEx's write-through when the Windows build
    # gets the Library setting.
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        if sys.platform == "darwin":
            import fcntl

            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        else:
            os.fsync(fd)
    finally:
        os.close(fd)


def sync_name(path: Path) -> None:
    """Sync *path*'s directory (:func:`sync_dir`), a failure reported against *path*.

    A file system that refuses the sync (``ENOTSUP``, ``EINVAL``) fails the call: the save is
    not durable there, and saying so beats a save that may vanish (DECISIONS, #768).
    """
    try:
        sync_dir(path.parent)
    except OSError as e:
        raise Io(path, f"syncing its folder: {e}") from e


def _publish(path: Path, data: bytes) -> None:
    """``atomic_write`` *data* at *path*, then sync its directory so the name is durable."""
    try:
        atomic_write(path, data)
    except OSError as e:
        raise io_error(path, e, write=True) from e
    sync_name(path)


def create_dir_new(path: Path) -> bool:
    """Make the directory *path* (its parent must exist); ``False`` if it exists.

    Either way the directory is synced into its parent (:func:`sync_dir`): one found there may
    be another writer's, not yet synced. Anything else at *path* raises ``FileExistsError``.
    """
    try:
        path.mkdir()
        made = True
    except FileExistsError:
        if not path.is_dir():
            raise
        made = False
    sync_dir(path.parent)
    return made


def rename(source: Path, target: Path) -> None:
    """Rename *source* to *target*."""
    source.rename(target)


def write_replace(path: Path, data: bytes) -> None:
    """Publish *data* at *path* atomically, replacing what is there.

    Only for a file its writer alone owns (an installation's own record). The directory is
    synced, so the record survives a crash once this returns.
    """
    _publish(path, data)


def remove_file(path: Path) -> None:
    """Delete one file."""
    path.unlink()


def remove_dir_all(path: Path) -> None:
    """Delete a directory and everything in it."""
    shutil.rmtree(path)
