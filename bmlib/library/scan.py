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
    Listing,
    component,
    exists,
    has_placeholder,
    io_error,
    is_dir,
    is_not_downloaded,
    listing,
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
        """The union of the records' identifiers; the first record wins a conflict.

        A scan reports the conflict as ``conflicting_ids``. To match a query use
        :meth:`shares_any`, which sees every record's value.
        """
        acc = PaperIds()
        for r in self.records:
            acc = acc.merged(r.paper.ids)[0]
        return acc

    def shares_any(self, ids: PaperIds) -> bool:
        """True when any record here holds the same value as *ids* for an identifier.

        So a value that lost a conflict inside the folder still finds it.
        """
        return any(r.paper.ids.shares_any(ids) for r in self.records)

    def used_by(self, sha256: str) -> list[Installation]:
        """The installations whose record lists a file with this digest."""
        return [r.installation for r in self.records if any(f.sha256 == sha256 for f in r.files)]


@dataclass(frozen=True)
class Problem:
    """Something odd a scan found and did not use.

    ``kind`` is one of (with the fields it uses):

    - ``unreadable_record`` (``folder``, ``file``, ``reason``);
    - ``unreadable_folder`` (``folder``, ``reason``): it or its ``records/`` cannot be listed;
    - ``newer_record`` (``folder``, ``file``, ``version``);
    - ``record_not_downloaded`` (``folder``, ``file``): the record is only an iCloud
      placeholder or evicted (dataless), so its installation's uses are unknown;
    - ``leftover_temp`` (``folder``, ``name``, as ``records/<name>`` when in ``records/``): a
      temporary of ``atomic_write`` from a write in flight or one a crash interrupted; it is
      left alone, never deleted;
    - ``misnamed_record`` (``folder``, ``file``);
    - ``conflicting_ids`` (``folder``, ``field``): two records hold different values for it;
      both count for :func:`find` and :func:`group`;
    - ``unrecorded_file`` (``folder``, ``name``);
    - ``stray_entry`` (``name``).

    Fields a kind does not use are ``None``.
    """

    kind: str
    folder: str | None = None
    field: str | None = None
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
    except OSError as e:
        if is_not_downloaded(e):
            return Problem("record_not_downloaded", folder=folder, file=file)
        return Problem("unreadable_record", folder=folder, file=file, reason=str(e))
    except UnicodeDecodeError as e:
        return Problem("unreadable_record", folder=folder, file=file, reason=str(e))


def _scan_folder(directory: Path, folder: str, problems: list[Problem]) -> PaperFolder:
    """Scan one paper folder, adding what is odd to *problems*."""
    records: list[Record] = []
    records_dir = directory / RECORDS_DIR
    if exists(records_dir) and not is_dir(records_dir):
        problems.append(
            Problem("unreadable_folder", folder=folder, reason="records is not a folder")
        )
    elif is_dir(records_dir):
        try:
            found = listing(records_dir)
        except OSError as e:
            found = Listing()
            problems.append(Problem("unreadable_folder", folder=folder, reason=f"records: {e}"))
        for file in found.names:
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
        problems.extend(
            Problem("record_not_downloaded", folder=folder, file=f)
            for f in found.placeholders
            if f not in found.names
        )
        problems.extend(
            Problem("leftover_temp", folder=folder, name=f"{RECORDS_DIR}/{t}") for t in found.temps
        )
    acc = PaperIds()
    conflicts: list[str] = []
    for r in records:
        acc, fields = acc.merged(r.paper.ids)
        for field in fields:
            if field not in conflicts:
                conflicts.append(field)
                problems.append(Problem("conflicting_ids", folder=folder, field=field))
    try:
        here = listing(directory)
        problems.extend(Problem("leftover_temp", folder=folder, name=t) for t in here.temps)
        on_disk = [n for n in here.names if n != RECORDS_DIR and not is_dir(directory / n)]
    except OSError as e:
        on_disk = []
        problems.append(Problem("unreadable_folder", folder=folder, reason=str(e)))
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
        found = listing(papers)
    except OSError as e:
        raise io_error(papers, e, write=False) from e
    folders: list[PaperFolder] = []
    problems: list[Problem] = []
    for name in found.names:
        if is_dir(papers / name):
            folders.append(_scan_folder(papers / name, name, problems))
        else:
            problems.append(Problem("stray_entry", name=name))
    # Nothing writes a file into papers/ itself; a temporary there is stray.
    problems.extend(Problem("stray_entry", name=t) for t in found.temps)
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
        if is_not_downloaded(e):
            raise NotDownloaded(file.folder, file.name) from e
        raise io_error(path, e, write=False) from e
    found = sha256_hex(data)
    if found != file.sha256:
        raise DigestMismatch(file.folder, file.name, file.sha256, found)
    return data


def find(scan: Scan, ids: PaperIds) -> list[PaperFolder]:
    """Every folder of the paper(s) matching *ids*, in scan order.

    A paper is the union of folders whose records share an identifier, so this is each folder
    in the :func:`group` of any folder with a record sharing one with *ids*.
    """
    hit = {i for g in group(scan) if any(scan.folders[j].shares_any(ids) for j in g) for i in g}
    return [f for i, f in enumerate(scan.folders) if i in hit]


def _is_digest(s: str) -> bool:
    """True when *s* is a SHA-256 as the library writes it: 64 lowercase hex digits."""
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def find_digest(scan: Scan, sha256: str, kind: SourceKind | None = None) -> FileRef | None:
    """A file on disk named for *sha256* (``{kind}-{first 12 hex}.{ext}``), or ``None``.

    The same bytes saved as two kinds are two files, so pass *kind* to find the one stored as
    that kind; without it, the first folder holding any kind wins, and in it the first kind
    in :class:`SourceKind`'s order. Only the name is looked at: :func:`read_file` checks the
    full digest. A digest that is not 64 lowercase hex digits finds nothing.
    """
    if not _is_digest(sha256):
        return None
    prefix = sha256[:DIGEST_PREFIX_HEX]
    kinds = list(SourceKind) if kind is None else [kind]  # declaration order, as Rust's KINDS
    for f in scan.folders:
        for k in kinds:
            name = f"{k.value}-{prefix}.{k.extension()}"
            if name in f.files_on_disk:
                return FileRef(f.folder, name, sha256, k)
    return None


def group(scan: Scan) -> list[list[int]]:
    """Indices of folders connected by shared identifiers, each group and the list sorted.

    Every record's identifiers count, not only :meth:`PaperFolder.ids`. A folder with no
    identifiers stands alone. Linear in the number of records: each identifier value is
    mapped to the first folder holding it, and later holders are joined to that one.
    """
    parent = list(range(len(scan.folders)))

    def root(i: int) -> int:
        """The representative of *i*'s set, halving the path on the way."""
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[tuple[str, str], int] = {}
    for i, f in enumerate(scan.folders):
        for r in f.records:
            for field in ("pmid", "doi", "pmcid"):
                value = getattr(r.paper.ids, field)
                if value is None:
                    continue
                j = first.setdefault((field, value), i)
                if j != i:
                    a, b = root(i), root(j)
                    parent[max(a, b)] = min(a, b)
    groups: dict[int, list[int]] = {}
    for i in range(len(scan.folders)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values())
