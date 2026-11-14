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

import contextlib
import ctypes
import enum
import errno
import hashlib
import logging
import os
import shutil
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from bmlib._atomic import atomic_write
from bmlib.library.constants import (
    ICLOUD_PLACEHOLDER,
    IGNORED_NAMES,
    PAPERS_DIR,
    PY_TEMP_EXT,
    PY_TEMP_HEX,
    RUST_TEMP_SUFFIX,
    RUST_TEMP_TOKEN_LEN,
)
from bmlib.library.errors import BadName, FileClash, Io, LibraryError, NotDownloaded, ReadOnly
from bmlib.library.names import is_reserved

_READ_ONLY_ERRNOS = (errno.EROFS, errno.EACCES, errno.EPERM)

NOT_A_REGULAR_FILE = "not a regular file"
"""The reason :func:`read_bytes` gives for a symbolic link, FIFO, device or socket (#761)."""

NOT_A_FOLDER = "not a folder"
"""The reason :func:`check_folder` and :func:`create_dir_new` give for a link or a file where
the library needs a folder (#761)."""

_NAME_SURROGATE = 0x20000000
"""The reparse-tag bit of a Windows entry that names another path (a link or a junction)."""

logger = logging.getLogger(__name__)

_APPLE = sys.platform in ("darwin", "ios")
"""Whether a sync service can leave a *dataless* file here (macOS 14+, iOS)."""

# setiopolicy_np(3)'s values for dataless files, from <sys/resource.h>: the policy type
# (IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES), the calling thread's scope only
# (IOPOL_SCOPE_THREAD) and "an I/O on a dataless file fails" (..._FILES_OFF).
_IOPOL_TYPE_DATALESS = 3
_IOPOL_SCOPE_THREAD = 1
_IOPOL_DATALESS_OFF = 1

_libc = ctypes.CDLL(None, use_errno=True) if _APPLE else None


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
    """Map an OS error at *path* to :class:`NotDownloaded` (a dataless file, its folder the
    full parent path), :class:`ReadOnly` (a refused write) or :class:`Io`."""
    if is_not_downloaded(err):
        return NotDownloaded(str(path.parent), path.name)
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


def _lstat(path: Path) -> os.stat_result | None:
    """``os.lstat(path)``, or ``None`` when nothing is there.

    :raises OSError: any other failure (a folder that cannot be searched, an I/O error), which
        must not read as absent: a scan would miss the paper and a save make it a second
        folder.
    """
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def is_dir(path: Path) -> bool:
    """Whether *path* is a directory itself, not a symbolic link to one.

    A linked folder inside the library would let a save write outside it (#761). A Windows
    junction counts as a link too, as Rust's ``std`` reads one (a name-surrogate reparse tag).

    :raises OSError: *path* cannot be examined (:func:`_lstat`).
    """
    st = _lstat(path)
    return (
        st is not None
        and stat.S_ISDIR(st.st_mode)
        and not getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE
    )


def is_dir_following_links(path: Path) -> bool:
    """Whether *path* is a directory or a link to one: only for the library's root, which a
    user may well reach through a link."""
    return path.is_dir()


def exists(path: Path) -> bool:
    """Whether anything exists at *path*, a dangling symbolic link included.

    :raises OSError: *path* cannot be examined (:func:`_lstat`).
    """
    return _lstat(path) is not None


def check_folder(path: Path) -> None:
    """Refuse *path* when something that is not a directory itself is there (#761).

    Nothing at *path* passes, for the caller to find missing.

    :raises Io: a symbolic link or a file is at *path* (:data:`NOT_A_FOLDER`), or it cannot be
        examined.
    """
    try:
        if exists(path) and not is_dir(path):
            raise Io(path, NOT_A_FOLDER)
    except OSError as e:
        raise io_error(path, e, write=False) from e


def paper_dir(root: Path, folder: str) -> Path:
    """The paper folder *folder* under *root*, its name checked by :func:`component`.

    ``papers/`` is checked as well as the folder: it may have been replaced by a link since
    the library was opened.

    :raises BadName: *folder* is not one path component.
    :raises Io: ``papers/`` or the folder is not a directory itself (:func:`check_folder`), so
        nothing is read, written or deleted through it (#761). A name with nothing at it is
        returned, for the caller to find missing.
    """
    papers = root / PAPERS_DIR
    directory = papers / component(folder)
    check_folder(papers)
    check_folder(directory)
    return directory


def has_placeholder(directory: Path, name: str) -> bool:
    """Return whether *directory* holds iCloud's online-only placeholder for *name*."""
    return (directory / ICLOUD_PLACEHOLDER.format(name=name)).exists()


def read_bytes(path: Path) -> bytes:
    """Read a whole file, never downloading it.

    On macOS 14+ and iOS a sync service (iCloud Drive, Dropbox, OneDrive) can evict a file to
    a *dataless* one under its real name, and a plain read then blocks until it is downloaded,
    perhaps forever offline. Here the read fails at once instead, with an error
    :func:`is_not_downloaded` recognises (#769).

    Only a regular file is read: a symbolic link (to a file outside the library, or to
    ``/dev/zero``), a FIFO or a device raises ``OSError`` with :data:`NOT_A_REGULAR_FILE`, so
    a scan or read can neither hang nor leave the library (#761).
    """
    with _materialisation_off(path):
        seen = os.lstat(path)
        if not stat.S_ISREG(seen.st_mode):
            raise OSError(NOT_A_REGULAR_FILE)
        # shortcut: a FIFO (or a link to one) swapped in between the lstat and the open still
        # blocks the open, and on Windows a link swapped in is followed; elsewhere the open
        # file must be the one lstat saw. Open with O_NONBLOCK | O_NOFOLLOW if a writer ever
        # races this.
        with open(path, "rb") as f:
            opened = os.fstat(f.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                os.name != "nt" and (opened.st_dev, opened.st_ino) != (seen.st_dev, seen.st_ino)
            ):
                raise OSError(NOT_A_REGULAR_FILE)
            return f.read()


def is_not_downloaded(err: OSError) -> bool:
    """Whether *err*, from :func:`read_bytes`, means the file is dataless: present but not
    downloaded to this device. Always ``False`` off macOS and iOS."""
    return _APPLE and err.errno == errno.EDEADLK


@contextlib.contextmanager
def _materialisation_off(path: Path) -> Iterator[None]:
    """Turn materialisation of dataless files off on this thread while the block runs, then
    give the thread its previous policy back.

    A failure to read or set the policy is logged and the block runs with the default policy,
    which downloads an evicted file (the behaviour before #769), rather than refusing every
    read.
    """
    if _libc is None:
        yield
        return
    previous = _libc.getiopolicy_np(_IOPOL_TYPE_DATALESS, _IOPOL_SCOPE_THREAD)
    if previous == -1 or (
        _libc.setiopolicy_np(_IOPOL_TYPE_DATALESS, _IOPOL_SCOPE_THREAD, _IOPOL_DATALESS_OFF) == -1
    ):
        logger.warning(
            "could not stop %s downloading if it is evicted: %s",
            path,
            os.strerror(ctypes.get_errno()),
        )
        yield
        return
    try:
        yield
    finally:
        if _libc.setiopolicy_np(_IOPOL_TYPE_DATALESS, _IOPOL_SCOPE_THREAD, previous) == -1:
            logger.warning(
                "could not restore this thread's policy for evicted files: %s",
                os.strerror(ctypes.get_errno()),
            )


def write_once(path: Path, data: bytes, sha256: str) -> Written:
    """Publish *data* at *path* unless a file is there.

    *sha256* is the digest of *data*. An existing file with that digest gives
    :attr:`Written.ALREADY_THERE`; one with another raises :class:`FileClash`
    and is left untouched.
    """
    try:
        existing = read_bytes(path)
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
    be another writer's, not yet synced. Anything else at *path*, a symbolic link to a
    directory included, raises ``OSError`` with :data:`NOT_A_FOLDER`; a failure to examine
    *path* raises that failure.
    """
    try:
        path.mkdir()
        made = True
    except FileExistsError:
        if not is_dir(path):
            raise OSError(NOT_A_FOLDER) from None
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
