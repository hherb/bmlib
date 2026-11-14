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

"""The library's errors. Every one names what the user must be told."""

from __future__ import annotations

from pathlib import Path


class LibraryError(Exception):
    """Base of every library failure; the subclasses are Rust's variants."""


class Unreachable(LibraryError):
    """The library folder cannot be reached (an unmounted drive, a revoked grant)."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path, self.reason = path, reason
        super().__init__(f"the library at {path} cannot be reached: {reason}")


class NotALibrary(LibraryError):
    """The folder exists but is not a library."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path, self.reason = path, reason
        super().__init__(f"{path} is not a library: {reason}")


class NewerLibrary(LibraryError):
    """The library was written by a newer version of the layout."""

    def __init__(self, version: int) -> None:
        self.version = version
        super().__init__(
            f"the library uses layout version {version}, newer than this app understands; "
            "update the app"
        )


class ReadOnly(LibraryError):
    """The library is read-only, so nothing can be saved to it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"the library at {path} is read-only")


class BadIdentifier(LibraryError):
    """An identifier is not in its canonical shape."""

    def __init__(self, field: str, value: str) -> None:
        self.field, self.value = field, value
        super().__init__(f"{value!r} is not a valid {field}")


class BadInstallation(LibraryError):
    """An installation's app, id or label is not acceptable."""

    def __init__(self, field: str, reason: str) -> None:
        self.field, self.reason = field, reason
        super().__init__(f"the installation's {field} is not acceptable: {reason}")


class BadName(LibraryError):
    """A name from a caller or a record cannot be used as a path component."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name!r} cannot be used as a file or folder name")


class Missing(LibraryError):
    """A file the record names is not in the paper's folder."""

    def __init__(self, folder: str, name: str) -> None:
        self.folder, self.name = folder, name
        super().__init__(f"{name} is missing from {folder}")


class NotDownloaded(LibraryError):
    """A file exists only as an online placeholder or evicted (dataless), not yet downloaded."""

    def __init__(self, folder: str, name: str) -> None:
        self.folder, self.name = folder, name
        super().__init__(
            f"{name} in {folder} is not downloaded to this device yet; "
            "open it in the file manager to download it"
        )


class DigestMismatch(LibraryError):
    """A file's bytes do not match the digest the record holds."""

    def __init__(self, folder: str, name: str, expected: str, found: str) -> None:
        self.folder, self.name, self.expected, self.found = folder, name, expected, found
        super().__init__(
            f"{name} in {folder} has changed: its digest is {found}, the record says {expected}"
        )


class FileClash(LibraryError):
    """A file of that name already exists with other bytes."""

    def __init__(self, folder: str, name: str) -> None:
        self.folder, self.name = folder, name
        super().__init__(
            f"{folder} already holds a different file named {name}; nothing was overwritten"
        )


class NewerRecord(LibraryError):
    """A record was written by a newer version of the layout."""

    def __init__(self, folder: str, file: str, version: int) -> None:
        self.folder, self.file, self.version = folder, file, version
        super().__init__(
            f"the record {file} in {folder} uses version {version}, newer than this app "
            "understands; update the app"
        )


class Io(LibraryError):
    """A file-system operation failed."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")
