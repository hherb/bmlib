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

"""Reading a library: scan it, find papers by identifier or files by digest, group folders
that are one paper split in two, and read a file with its digest checked.

A scan never fails on one bad folder: every oddity is a :class:`Problem`, so the caller can
show it (a record is never silently dropped). Rust's ``library::scan`` is the twin.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bmlib.library.constants import DIGEST_PREFIX_HEX, PAPERS_DIR, RECORDS_DIR
from bmlib.library.errors import DigestMismatch, Missing, NotDownloaded
from bmlib.library.files import (
    component,
    has_placeholder,
    io_error,
    is_dir,
    list_names,
    read_bytes,
    sha256_hex,
)
from bmlib.library.ids import Installation, PaperIds, SourceKind
from bmlib.library.records import Record, RecordProblem, parse_record


@dataclass(frozen=True)
class PaperFolder:
    """One paper's folder as found on disk.

    ``records`` are the valid, correctly named records (by file name, bytes); ``files_on_disk``
    the source files present (placeholders and ``records/`` left out).
    """

    folder: str
    records: tuple[Record, ...]
    files_on_disk: tuple[str, ...]

    def ids(self) -> PaperIds:
        """The union of the records' identifiers; the first record wins a conflict."""
        acc = PaperIds()
        for r in self.records:
            acc = acc.merged(r.paper.ids)[0]
        return acc

    def used_by(self, sha256: str) -> list[Installation]:
        """The installations whose record lists a file with this digest."""
        return [r.installation for r in self.records if any(f.sha256 == sha256 for f in r.files)]


@dataclass(frozen=True)
class Problem:
    """Something odd a scan found and did not use.

    ``kind`` is one of ``unreadable_record`` (``folder``, ``file``, ``reason``; ``file`` is
    ``"records"`` or ``"."`` when a listing failed), ``newer_record`` (``folder``, ``file``,
    ``version``), ``misnamed_record`` (``folder``, ``file``), ``unrecorded_file`` (``folder``,
    ``name``) or ``stray_entry`` (``name``). Fields a kind does not use are ``None``.
    """

    kind: str
    folder: str | None = None
    file: str | None = None
    name: str | None = None
    reason: str | None = None
    version: int | None = None


@dataclass(frozen=True)
class Scan:
    """The result of scanning a library: folders ordered by name (bytes), and every oddity."""

    folders: tuple[PaperFolder, ...]
    problems: tuple[Problem, ...]


@dataclass(frozen=True)
class FileRef:
    """A source file in a paper's folder, with the full SHA-256 its bytes must have."""

    folder: str
    name: str
    sha256: str
    kind: SourceKind


def _read_record(path: Path, folder: str, file: str) -> Record | Problem:
    """Parse one record file, or say why not."""
    try:
        text = read_bytes(path).decode("utf-8")
        return parse_record(text)
    except RecordProblem as p:
        if p.kind == "newer":
            return Problem("newer_record", folder=folder, file=file, version=p.version)
        return Problem("unreadable_record", folder=folder, file=file, reason=p.reason)
    except (OSError, UnicodeDecodeError) as e:
        return Problem("unreadable_record", folder=folder, file=file, reason=str(e))


def _scan_folder(directory: Path, folder: str, problems: list[Problem]) -> PaperFolder:
    """Scan one paper folder, adding what is odd to *problems*."""
    records: list[Record] = []
    records_dir = directory / RECORDS_DIR
    if is_dir(records_dir):
        try:
            names = list_names(records_dir)
        except OSError as e:
            names = []
            problems.append(
                Problem("unreadable_record", folder=folder, file=RECORDS_DIR, reason=str(e))
            )
        for file in names:
            path = records_dir / file
            if is_dir(path):
                continue
            got = _read_record(path, folder, file)
            if isinstance(got, Problem):
                problems.append(got)
            elif got.installation.record_file_name() != file:
                problems.append(Problem("misnamed_record", folder=folder, file=file))
            else:
                records.append(got)
    try:
        on_disk = [n for n in list_names(directory) if not is_dir(directory / n)]
    except OSError as e:
        on_disk = []
        problems.append(Problem("unreadable_record", folder=folder, file=".", reason=str(e)))
    recorded = {f.name for r in records for f in r.files}
    problems.extend(
        Problem("unrecorded_file", folder=folder, name=n) for n in on_disk if n not in recorded
    )
    return PaperFolder(folder, tuple(records), tuple(on_disk))


def scan_library(root: Path) -> Scan:
    """Read every paper folder under *root*. Never writes.

    :raises Io: ``papers/`` itself cannot be listed; trouble inside a folder is a
        :class:`Problem` in the result instead.
    """
    papers = root / PAPERS_DIR
    try:
        names = list_names(papers)
    except OSError as e:
        raise io_error(papers, e, write=False) from e
    folders: list[PaperFolder] = []
    problems: list[Problem] = []
    for name in names:
        if is_dir(papers / name):
            folders.append(_scan_folder(papers / name, name, problems))
        else:
            problems.append(Problem("stray_entry", name=name))
    return Scan(tuple(folders), tuple(problems))


def read_file(root: Path, file: FileRef) -> bytes:
    """Read a file's bytes under *root*, checking them against ``file.sha256``.

    :raises BadName: the folder or file name is not one path component.
    :raises NotDownloaded: only an online placeholder is here.
    :raises Missing: the file is not there.
    :raises DigestMismatch: the bytes hash to something else.
    :raises Io: any other failure to read.
    """
    directory = root / PAPERS_DIR / component(file.folder)
    path = directory / component(file.name)
    try:
        data = read_bytes(path)
    except FileNotFoundError as e:
        if has_placeholder(directory, file.name):
            raise NotDownloaded(file.folder, file.name) from e
        raise Missing(file.folder, file.name) from e
    except OSError as e:
        raise io_error(path, e, write=False) from e
    found = sha256_hex(data)
    if found != file.sha256:
        raise DigestMismatch(file.folder, file.name, file.sha256, found)
    return data


def find(scan: Scan, ids: PaperIds) -> list[PaperFolder]:
    """The folders whose identifiers share any with *ids*, in scan order."""
    return [f for f in scan.folders if f.ids().shares_any(ids)]


def _is_digest(s: str) -> bool:
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def find_digest(scan: Scan, sha256: str) -> FileRef | None:
    """A file on disk named for *sha256* (``{kind}-{first 12 hex}.{ext}``), or ``None``.

    Only the name is looked at: :func:`read_file` checks the full digest. A digest that is
    not 64 lowercase hex digits finds nothing.
    """
    if not _is_digest(sha256):
        return None
    prefix = sha256[:DIGEST_PREFIX_HEX]
    for f in scan.folders:
        for kind in SourceKind:  # declaration order, as Rust's KINDS
            name = f"{kind.value}-{prefix}.{kind.extension()}"
            if name in f.files_on_disk:
                return FileRef(f.folder, name, sha256, kind)
    return None


def group(scan: Scan) -> list[list[int]]:
    """Indices of folders connected by shared identifiers, each group and the list sorted.

    A folder with no identifiers stands alone.
    """
    ids = [f.ids() for f in scan.folders]
    parent = list(range(len(ids)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if ids[i].shares_any(ids[j]):
                a, b = root(i), root(j)
                parent[max(a, b)] = min(a, b)
    groups: dict[int, list[int]] = {}
    for i in range(len(ids)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values())
