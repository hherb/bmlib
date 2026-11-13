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

"""Writing a library: save a source file, drop it from one installation's record, and the
manual clear. Rust's ``library::save`` is the twin.

Nothing is overwritten or deleted unless the caller asks. A source file is written once under
a name made from its digest; each installation writes only its own record. Two writers saving
the same paper at once therefore end with one file and both records.
"""

from __future__ import annotations

import dataclasses
import logging
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from bmlib.library.constants import (
    CORRUPT_SUFFIX,
    DIGEST_PREFIX_HEX,
    MAX_NAME_SUFFIX,
    PAPERS_DIR,
    RECORDS_DIR,
)
from bmlib.library.errors import Io, NewerRecord
from bmlib.library.files import (
    component,
    create_dir_all,
    create_dir_new,
    exists,
    io_error,
    is_dir,
    list_names,
    read_bytes,
    remove_dir_all,
    remove_file,
    rename,
    sha256_hex,
    write_once,
    write_replace,
)
from bmlib.library.ids import Installation, PaperInfo, SourceKind
from bmlib.library.names import folder_name
from bmlib.library.records import FileEntry, Record, RecordProblem, parse_record, render_record
from bmlib.library.scan import FileRef, _scan_folder, find, find_digest, scan_library

logger = logging.getLogger(__name__)


def _folded(name: str) -> str:
    """A folder name as the code compares it: NFC, then lowercase, so the outcome is the same
    on a case-sensitive and a case-insensitive disk."""
    return unicodedata.normalize("NFC", name).lower()


def _same_name_as(papers: Path, candidate: str) -> list[str]:
    """The names in *papers* that equal *candidate* once folded, in order."""
    want = _folded(candidate)
    try:
        names = list_names(papers)
    except OSError as e:
        raise io_error(papers, e, write=False) from e
    return [n for n in names if _folded(n) == want]


def _belongs_to(papers: Path, name: str, paper: PaperInfo) -> bool:
    """Whether the folder *name* may take *paper*: some record there shares an identifier with
    it, or no record there is readable yet (another writer has claimed the folder, or a crash
    left it empty)."""
    found = _scan_folder(papers / name, name, [])
    return not found.records or found.ids().shares_any(paper.ids)


def _claim_folder(papers: Path, paper: PaperInfo) -> str:
    """The folder *paper* goes in when no folder holds it yet: a new one, or one a concurrent
    writer of the same paper just made."""
    base = folder_name(paper)
    for n in range(1, MAX_NAME_SUFFIX + 1):
        candidate = base if n == 1 else f"{base} ({n})"
        for _ in range(2):  # the second round re-reads the names after losing a create race
            same = _same_name_as(papers, candidate)
            if same:
                mine = next((s for s in same if _belongs_to(papers, s, paper)), None)
                if mine is not None:
                    return mine
                break
            target = papers / candidate
            try:
                if create_dir_new(target):
                    return candidate
            except OSError as e:
                raise io_error(target, e, write=True) from e
    raise Io(papers / base, f"no free folder name up to suffix {MAX_NAME_SUFFIX}")


def _keep_aside(path: Path) -> Path:
    """Move an unreadable record aside, to a name nothing is at yet."""
    n = 1
    while True:
        aside = path.with_name(path.name + (CORRUPT_SUFFIX if n == 1 else f"{CORRUPT_SUFFIX}-{n}"))
        if not exists(aside):
            try:
                rename(path, aside)
            except OSError as e:
                raise io_error(path, e, write=True) from e
            return aside
        n += 1


def _load_own_record(path: Path, folder: str) -> Record | None:
    """This installation's record at *path*: ``None`` when there is none, or when it was
    unreadable and has been kept aside. A newer record raises :class:`NewerRecord`."""
    try:
        data = read_bytes(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise io_error(path, e, write=False) from e
    try:
        return parse_record(data.decode("utf-8"))
    except (RecordProblem, UnicodeDecodeError) as p:
        if isinstance(p, RecordProblem) and p.kind == "newer":
            raise NewerRecord(folder, path.name, p.version) from p
        reason = p.reason if isinstance(p, RecordProblem) else str(p)
        aside = _keep_aside(path)
        logger.warning(
            "the record %s was unreadable (%s); kept as %s and replaced", path, reason, aside
        )
        return None


def _store_record(path: Path, record: Record) -> None:
    """Write *record* over *path* unless that is what is there already."""
    data = render_record(record).encode("utf-8")
    try:
        if read_bytes(path) == data:
            return
    except OSError:
        pass
    write_replace(path, data)


def _merge_paper(record: Record, paper: PaperInfo, path: Path) -> Record:
    """*paper* folded into the record's own account: identifiers merged (a conflict is logged,
    the record's own value kept), gaps in the rest filled."""
    ids, conflicts = record.paper.ids.merged(paper.ids)
    for field in conflicts:
        logger.warning(
            "%s: the %s given differs from the one recorded; the recorded one is kept",
            path,
            field,
        )
    mine = record.paper
    merged = PaperInfo(
        ids,
        mine.title if mine.title is not None else paper.title,
        mine.year if mine.year is not None else paper.year,
        mine.first_author if mine.first_author is not None else paper.first_author,
    )
    return dataclasses.replace(record, paper=merged)


def _locate(root: Path, file: FileRef) -> tuple[Path, Path]:
    """The file's folder and path, its names checked as single path components."""
    directory = root / PAPERS_DIR / component(file.folder)
    return directory, directory / component(file.name)


def save_file(
    root: Path,
    who: Installation,
    paper: PaperInfo,
    data: bytes,
    kind: SourceKind,
    origin: str,
) -> FileRef:
    """Save *data* of *kind* for *paper* and note it in *who*'s record.

    The paper's folder is the first folder holding a record that shares an identifier with it;
    with no identifiers, the folder already holding these bytes; otherwise a new folder, named
    by :func:`folder_name` (with `` (2)``, `` (3)`` … when another paper has the name, compared
    without regard to case). The file is written first, then the record, so a crash between
    leaves only an unrecorded file, which saving the same bytes again records.

    :raises FileClash: a file of that name holds other bytes (nothing is overwritten).
    :raises NewerRecord: *who*'s record is from a newer layout.
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    digest = sha256_hex(data)
    name = f"{kind.value}-{digest[:DIGEST_PREFIX_HEX]}.{kind.extension()}"
    scan = scan_library(root)
    papers = root / PAPERS_DIR
    found = find(scan, paper.ids)
    if found:
        folder = found[0].folder
    else:
        by_digest = find_digest(scan, digest) if paper.ids.is_empty() else None
        folder = by_digest.folder if by_digest else _claim_folder(papers, paper)
    directory = papers / component(folder)
    write_once(directory / name, data, digest)

    records_dir = directory / RECORDS_DIR
    try:
        create_dir_all(records_dir)
    except OSError as e:
        raise io_error(records_dir, e, write=True) from e
    path = records_dir / who.record_file_name()
    entry = FileEntry(
        digest, kind, name, origin, len(data), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    record = _load_own_record(path, folder)
    if record is None:
        record = Record(who, paper, (entry,))
    else:
        files = record.files
        if all(f.sha256 != digest for f in files):
            files = (*files, entry)
        record = _merge_paper(dataclasses.replace(record, files=files), paper, path)
    _store_record(path, record)
    return FileRef(folder, name, digest, kind)


def release_file(root: Path, who: Installation, file: FileRef) -> None:
    """Drop *file* from *who*'s record. The file stays, and so do the other records.

    :raises BadName: a name that is not one path component.
    :raises NewerRecord: the record is from a newer layout.
    :raises Io: the record cannot be read (it is not touched).
    """
    directory, _ = _locate(root, file)
    path = directory / RECORDS_DIR / who.record_file_name()
    try:
        text = read_bytes(path).decode("utf-8")
    except FileNotFoundError:
        return
    except (OSError, UnicodeDecodeError) as e:
        raise Io(path, f"the record is not readable: {e}") from e
    try:
        record = parse_record(text)
    except RecordProblem as p:
        if p.kind == "newer":
            raise NewerRecord(file.folder, path.name, p.version) from p
        raise Io(path, f"the record is not readable: {p.reason}") from p
    kept = tuple(f for f in record.files if f.sha256 != file.sha256)
    _store_record(path, dataclasses.replace(record, files=kept))


def remove_source_file(root: Path, file: FileRef) -> None:
    """Delete one source file (the manual clear). Other installations' records are left as they
    are and will read ``Missing``. A file that is not there is already cleared.

    :raises BadName: a name that is not one path component.
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    _, path = _locate(root, file)
    try:
        remove_file(path)
    except FileNotFoundError:
        return
    except OSError as e:
        raise io_error(path, e, write=True) from e


def remove_paper_folder(root: Path, folder: str) -> None:
    """Delete a paper's whole folder, records included (the manual clear). A folder that is
    not there is already cleared.

    :raises BadName: *folder* is not one path component.
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    directory = root / PAPERS_DIR / component(folder)
    if not is_dir(directory):
        return
    try:
        remove_dir_all(directory)
    except OSError as e:
        raise io_error(directory, e, write=True) from e
