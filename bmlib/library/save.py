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

"""Writing a library: save a source file, note one already there, drop it from one
installation's record, and the manual clear. Rust's ``library::save`` is the twin.

Nothing is overwritten or deleted unless the caller asks. A source file is written once under
a name made from its digest; each installation writes only its own record. Two writers saving
the same paper at once therefore end with one file and both records.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
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
from bmlib.library.errors import BadIdentifier, BadName, Io, Missing, NewerRecord, NotDownloaded
from bmlib.library.files import (
    component,
    create_dir_new,
    exists,
    has_placeholder,
    io_error,
    is_dir,
    is_not_downloaded,
    list_names,
    paper_dir,
    read_bytes,
    remove_dir_all,
    remove_file,
    rename,
    sha256_hex,
    sync_name,
    write_once,
    write_replace,
)
from bmlib.library.ids import Installation, PaperIds, PaperInfo, SourceKind, canonical_ids
from bmlib.library.names import folder_name, folder_name_numbered
from bmlib.library.records import FileEntry, Record, RecordProblem, parse_record, render_record
from bmlib.library.scan import (
    FileRef,
    Problem,
    _scan_folder,
    find,
    find_digest,
    read_file,
    scan_library,
)

logger = logging.getLogger(__name__)

# Serialises the read-merge-write of records (and keeping one aside) within this process, so
# two threads of one installation never lose each other's update. One lock for all records:
# a record write is short, and separate processes of one installation are out of scope.
_RECORD_LOCK = threading.Lock()


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
    left it empty).

    A folder whose records are all newer than this code is neither: its identifiers cannot be
    read, and a second folder beside a newer app's paper would be a silent second store, so
    that raises :class:`NewerRecord`.
    """
    problems: list[Problem] = []
    found = _scan_folder(papers / name, name, problems)
    if not found.records:
        for p in problems:
            if p.kind == "newer_record":
                assert p.file is not None and p.version is not None
                raise NewerRecord(name, p.file, p.version)
        return True
    return found.shares_any(paper.ids)


def _claim_folder(papers: Path, paper: PaperInfo) -> str:
    """The folder *paper* goes in when no folder holds it yet: a new one, or one a concurrent
    writer of the same paper just made."""
    base = folder_name(paper)
    for n in range(1, MAX_NAME_SUFFIX + 1):
        candidate = folder_name_numbered(paper, n)
        for _ in range(2):  # the second round re-reads the names after losing a create race
            same = _same_name_as(papers, candidate)
            if same:
                newer: NewerRecord | None = None
                for s in same:
                    if not is_dir(papers / s):
                        continue  # a file or link holds the name (#761): it is taken
                    try:
                        if _belongs_to(papers, s, paper):
                            return s
                    except NewerRecord as e:
                        newer = newer or e
                if newer is not None:
                    raise newer
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


def _check_not_evicted(path: Path, folder: str) -> None:
    """Refuse to go on when this installation's record at *path* is absent but iCloud holds a
    placeholder for it: a fresh record written now would replace the real one when it is
    downloaded, and its entries would be lost.

    :raises NotDownloaded: naming the record file.
    """
    if not exists(path) and has_placeholder(path.parent, path.name):
        raise NotDownloaded(folder, path.name)


def _load_own_record(path: Path, folder: str) -> Record | None:
    """This installation's record at *path*: ``None`` when there is none, or when it was
    unreadable and has been kept aside. A newer record raises :class:`NewerRecord`, one present
    only as an iCloud placeholder :class:`NotDownloaded`."""
    try:
        data = read_bytes(path)
    except FileNotFoundError:
        _check_not_evicted(path, folder)
        return None
    except OSError as e:
        if is_not_downloaded(e):
            raise NotDownloaded(folder, path.name) from e
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


def _check_who(who: Installation) -> None:
    """Refuse an installation :meth:`Installation.new` would refuse.

    A dataclass can be built without ``new``, and its record name becomes a path.

    :raises BadInstallation: naming the field.
    :raises BadName: the record name is not one path component.
    """
    Installation.new(who.app, who.id, who.label)
    component(who.record_file_name())


def _check_ids(ids: PaperIds) -> None:
    """Refuse identifiers that are not canonical.

    :func:`parse_record` refuses them, so a record holding them would not read back and the
    next save would put it aside.

    :raises BadIdentifier: naming the first field that is not canonical.
    """
    canon = canonical_ids(ids.pmid, ids.doi, ids.pmcid)
    for field in ("pmid", "doi", "pmcid"):
        given = getattr(ids, field)
        if given != getattr(canon, field):
            raise BadIdentifier(field, given or "")


def _record_bytes(record: Record, path: Path) -> bytes:
    """*record* as the bytes to write, after the last check that it reads back.

    A record :func:`parse_record` refuses would be put aside by the next save, and the files
    it lists would then look unused. Text that is not valid Unicode (a lone surrogate) cannot
    be written as UTF-8 and is refused the same way.

    :raises Io: the record would not read back; nothing was written.
    """
    text = render_record(record)
    try:
        data = text.encode("utf-8")
        parse_record(text)
    except (UnicodeEncodeError, RecordProblem) as e:
        raise Io(path, f"the record would not read back ({e}); nothing was written") from e
    return data


def _store_record(path: Path, record: Record) -> None:
    """Write *record* over *path* unless that is what is there already, which is synced
    instead: a failed sync may have left it so, and a retry must not return without one."""
    data = _record_bytes(record, path)
    try:
        unchanged = read_bytes(path) == data
    except OSError:
        unchanged = False
    if unchanged:
        sync_name(path)
    else:
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


def _stored_name(kind: SourceKind, digest: str) -> str:
    """The name a file of *kind* with this digest has in a paper's folder:
    ``{kind}-{first 12 hex}.{extension}``."""
    return f"{kind.value}-{digest[:DIGEST_PREFIX_HEX]}.{kind.extension()}"


def _now() -> str:
    """The time a file is noted, as records write it: ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _note_use(
    directory: Path, folder: str, who: Installation, paper: PaperInfo, entry: FileEntry
) -> None:
    """Note *entry* in *who*'s record in the paper folder *directory*, under the record lock.

    The entry is added unless the record lists that file (its digest and kind) already, and
    *paper* is merged into the record's account. The one path by which both :func:`save_file` and
    :func:`record_file_use` change a record.
    """
    sync_name(directory)  # the folder may be another writer's, not yet synced into papers/
    records_dir = directory / RECORDS_DIR
    try:
        create_dir_new(records_dir)
    except OSError as e:
        raise io_error(records_dir, e, write=True) from e
    path = records_dir / component(who.record_file_name())
    with _RECORD_LOCK:
        record = _load_own_record(path, folder)
        if record is None:
            record = Record(who, paper, (entry,))
        else:
            files = record.files
            if all((f.sha256, f.kind) != (entry.sha256, entry.kind) for f in files):
                files = (*files, entry)
            record = _merge_paper(dataclasses.replace(record, files=files), paper, path)
        _store_record(path, record)


def _locate(root: Path, file: FileRef) -> tuple[Path, Path]:
    """The file's folder and path, its names checked as single path components.

    :raises Io: the folder is a symbolic link or a file (#761).
    """
    directory = paper_dir(root, file.folder)
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

    The paper's folder is the folder already holding these bytes, even when the identifiers
    find another (the bytes are kept once); otherwise the first folder holding a record that
    shares an identifier with it; otherwise a new folder, named by :func:`folder_name` (with
    `` (2)``, `` (3)`` … when another paper has the name, compared without regard to case). The
    file is written first, then the record (by the same merge as :func:`record_file_use`), so a
    crash between leaves only an unrecorded file, which saving the same bytes again records.

    Within one process the update of a record is serialised, so threads of one installation
    never lose each other's entries; separate processes of one installation are out of scope.

    :raises BadInstallation: *who* is one :meth:`Installation.new` would refuse.
    :raises BadIdentifier: ``paper.ids`` are not canonical (nothing is written).
    :raises Io: the record would not read back, such as text holding a lone surrogate
        (nothing is written).
    :raises NotDownloaded: *who*'s record is only an iCloud placeholder (nothing is written).
    :raises FileClash: a file of that name holds other bytes (nothing is overwritten).
    :raises NewerRecord: *who*'s record, or every record of the folder the paper would go in,
        is from a newer layout (no second folder is made).
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    _check_who(who)
    _check_ids(paper.ids)
    digest = sha256_hex(data)
    name = _stored_name(kind, digest)
    entry = FileEntry(digest, kind, name, origin, len(data), _now())
    fresh = Record(who, paper, (entry,))
    _record_bytes(fresh, root)  # before anything is written: the record must read back
    # shortcut: save rescans the whole library per call, so a full-text lookup costs one scan
    # plus one per file saved (and one more when it uses a held PDF); when that shows in a
    # lookup's time, let the caller pass its Scan.
    scan = scan_library(root)
    papers = root / PAPERS_DIR
    # Bytes are kept once: the folder already holding them wins over the identifiers,
    # which may name another folder (a split paper).
    by_digest = find_digest(scan, digest)
    found = find(scan, paper.ids)
    if by_digest:
        folder = by_digest.folder
    elif found:
        folder = found[0].folder
    else:
        folder = _claim_folder(papers, paper)
    directory = papers / component(folder)
    records_dir = directory / RECORDS_DIR
    path = records_dir / component(who.record_file_name())
    _check_not_evicted(path, folder)
    write_once(directory / name, data, digest)
    _note_use(directory, folder, who, paper, entry)
    return FileRef(folder, name, digest, kind)


def record_file_use(root: Path, who: Installation, paper: PaperInfo, file: FileRef) -> None:
    """Note in *who*'s record that it uses *file*, a file already in the library (such as one
    another installation saved), with what it knows of *paper*.

    The file is read through :func:`read_file`, so it must be present with the bytes
    ``file.sha256`` names; then it is merged into the record exactly as :func:`save_file`
    does, under the same lock. The entry's origin is copied from a record in the folder that
    lists the file, or left empty when none does.

    :raises BadInstallation: *who* is one :meth:`Installation.new` would refuse.
    :raises BadIdentifier: ``paper.ids`` are not canonical.
    :raises BadName: a name that is not one path component, or not the library's name for
        those bytes.
    :raises Missing: the file is not there (nothing is written).
    :raises NotDownloaded: the file, or *who*'s record, is only an iCloud placeholder.
    :raises DigestMismatch: the bytes hash to something else (nothing is written).
    :raises NewerRecord: *who*'s record is from a newer layout.
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    _check_who(who)
    _check_ids(paper.ids)
    data = read_file(root, file)
    if file.name != _stored_name(file.kind, file.sha256):
        raise BadName(file.name)
    directory, _ = _locate(root, file)
    found = _scan_folder(directory, file.folder, [])
    origin = next(
        (
            f.origin
            for r in found.records
            for f in r.files
            if (f.sha256, f.kind) == (file.sha256, file.kind)
        ),
        "",
    )
    entry = FileEntry(file.sha256, file.kind, file.name, origin, len(data), _now())
    _record_bytes(Record(who, paper, (entry,)), directory)
    _note_use(directory, file.folder, who, paper, entry)


def release_file(root: Path, who: Installation, file: FileRef) -> None:
    """Drop *file* from *who*'s record. The file stays, and so do the other records.

    A file the record does not list (or no record) is already not used, so that is fine.
    Serialised with :func:`save_file` in this process.

    :raises BadInstallation: *who* is one :meth:`Installation.new` would refuse.
    :raises BadName: a name that is not one path component.
    :raises NewerRecord: the record is from a newer layout.
    :raises NotDownloaded: the record is only an iCloud placeholder.
    :raises Io: the record cannot be read (it is not touched).
    """
    _check_who(who)
    directory, _ = _locate(root, file)
    path = directory / RECORDS_DIR / component(who.record_file_name())
    with _RECORD_LOCK:
        try:
            text = read_bytes(path).decode("utf-8")
        except FileNotFoundError:
            _check_not_evicted(path, file.folder)
            return
        except OSError as e:
            if is_not_downloaded(e):
                raise NotDownloaded(file.folder, path.name) from e
            raise Io(path, f"the record is not readable: {e}") from e
        except UnicodeDecodeError as e:
            raise Io(path, f"the record is not readable: {e}") from e
        try:
            record = parse_record(text)
        except RecordProblem as p:
            if p.kind == "newer":
                raise NewerRecord(file.folder, path.name, p.version) from p
            raise Io(path, f"the record is not readable: {p.reason}") from p
        kept = tuple(f for f in record.files if (f.sha256, f.kind) != (file.sha256, file.kind))
        _store_record(path, dataclasses.replace(record, files=kept))


def remove_source_file(root: Path, file: FileRef) -> None:
    """Delete one source file (the manual clear). Other installations' records are left as they
    are and will read ``Missing``.

    :raises BadName: a name that is not one path component.
    :raises Missing: the file is not there.
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    _, path = _locate(root, file)
    try:
        remove_file(path)
    except FileNotFoundError as e:
        raise Missing(file.folder, file.name) from e
    except OSError as e:
        raise io_error(path, e, write=True) from e


def remove_paper_folder(root: Path, folder: str) -> None:
    """Delete a paper's whole folder, records included (the manual clear).

    :raises BadName: *folder* is not one path component.
    :raises Missing: there is no such folder (or it is a file or a symbolic link).
    :raises ReadOnly: the library cannot be written.
    :raises Io: any other failure.
    """
    directory = root / PAPERS_DIR / component(folder)
    if not is_dir(directory):
        raise Missing(PAPERS_DIR, folder)
    try:
        remove_dir_all(directory)
    except OSError as e:
        raise io_error(directory, e, write=True) from e
