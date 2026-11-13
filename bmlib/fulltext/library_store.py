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

"""The shared library as the full-text service's second store.

The library is keyed by the request's ``pmid``, ``doi`` and ``pmc_id``. Each is
made canonical on its own, so one bad value costs only that identifier, not the
whole key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bmlib.fulltext.models import JATSArticle
from bmlib.library import (
    PAPERS_DIR,
    BadIdentifier,
    FileRef,
    Installation,
    Library,
    LibraryError,
    PaperIds,
    PaperInfo,
    Scan,
    SourceKind,
    canonical_ids,
    find,
)
from bmlib.library.files import component


class LibraryStoreFailure(Exception):  # noqa: N818 (the name is fixed by the plan)
    """A library operation failed; the message names the paper, the file and the cause."""


def library_ids(
    pmid: str | None, doi: str | None, pmc_id: str | None
) -> tuple[PaperIds, list[str]]:
    """The request's identifiers as the library's canonical ones, and the fields refused.

    Each of *pmid*, *doi* and *pmc_id* goes alone through :func:`canonical_ids` (a blank one is
    simply absent). A field it refuses is left out of the result and its name (``"pmid"``,
    ``"doi"`` or ``"pmc_id"``, the request's parameter names) is returned in the list, in that
    order. A value that is not text (an untyped caller's number, say) is refused the same way;
    Rust's ``&str`` cannot hold one. This function only reports; the caller logs the warning.
    """
    picked: dict[str, str | None] = {}
    dropped: list[str] = []
    for field, key, value, args in (
        ("pmid", "pmid", pmid, (pmid, None, None)),
        ("doi", "doi", doi, (None, doi, None)),
        ("pmc_id", "pmcid", pmc_id, (None, None, pmc_id)),
    ):
        # The real type, not ``isinstance``, which a faked ``__class__`` passes (#500).
        if value is not None and not issubclass(type(value), str):
            dropped.append(field)
            continue
        try:
            picked[key] = getattr(canonical_ids(*args), key)
        except BadIdentifier:
            dropped.append(field)
    return PaperIds(**picked), dropped


#: The shape of a well-formed ``saved_at``: ``D`` is an ASCII digit, anything else must match
#: exactly (``2026-11-14T09:30:00Z``).
_SAVED_AT_SHAPE = "DDDD-DD-DDTDD:DD:DDZ"

#: The ``i32`` range a year must fit, as in Rust.
_YEAR_MIN = -(2**31)
_YEAR_MAX = 2**31 - 1


def _saved_at_key(saved_at: str) -> str:
    """*saved_at* as a sort key: itself when it has exactly the shape ``_SAVED_AT_SHAPE``
    (fixed-width UTC, so string order is time order), else ``""``, which sorts below every
    well-formed one. A record is free text, so a foreign or tampered ``saved_at`` must not
    shadow the real newest file."""
    ok = len(saved_at) == len(_SAVED_AT_SHAPE) and all(
        (c.isascii() and c.isdigit()) if want == "D" else c == want
        for c, want in zip(saved_at, _SAVED_AT_SHAPE, strict=True)
    )
    return saved_at if ok else ""


def _earliest(a: str, b: str) -> str:
    """The earlier of two :func:`_saved_at_key` values; ``""`` (malformed) only when both are."""
    return min(a, b) if a and b else a or b


def contradiction(theirs: PaperIds, ours: PaperIds) -> tuple[str, str, str] | None:
    """The first identifier on which *theirs* contradicts *ours*, as the request's field name
    (``"pmid"``, ``"doi"`` or ``"pmc_id"``), their value and ours; ``None`` when they agree. A
    field absent on either side agrees. The one rule for a library JATS's own identifiers and for
    a record's; Rust's ``contradiction`` is the same."""
    for name, a, b in (
        ("pmid", theirs.pmid, ours.pmid),
        ("doi", theirs.doi, ours.doi),
        ("pmc_id", theirs.pmcid, ours.pmcid),
    ):
        if a and b and a != b:
            return name, a, b
    return None


@dataclass(frozen=True)
class Held:
    """A source file read from the library, its bytes already checked against its digest."""

    file: FileRef
    bytes: bytes


def _describe_ids(ids: PaperIds) -> str:
    """The identifiers present as ``pmid=1 doi=x pmcid=PMC1``, or ``no identifiers``."""
    named = (("pmid", ids.pmid), ("doi", ids.doi), ("pmcid", ids.pmcid))
    return " ".join(f"{n}={v}" for n, v in named if v) or "no identifiers"


def _failure(ids: PaperIds, file: tuple[str, str] | None, err: Exception) -> LibraryStoreFailure:
    """The one wording of every library failure: identifiers, the file (``folder/name``) when
    there is one, and what the library said. Rust's ``failure`` words it the same."""
    at = f" file {file[0]}/{file[1]}" if file else ""
    return LibraryStoreFailure(f"library, paper {_describe_ids(ids)}{at}: {err}")


@dataclass(frozen=True)
class LibraryStore:
    """The shared library and the installation that writes to it."""

    library: Library
    who: Installation

    def find_source(self, ids: PaperIds, kind: SourceKind) -> Held | None:
        """The paper's newest source file of *kind*, read and digest-checked, or ``None``: one
        scan of the library, then :meth:`find_source_in`.

        :raises LibraryStoreFailure: the scan or the read failed; the message names the paper's
            identifiers, the file and the library's error.
        """
        return self.find_source_in(self.scan(ids), ids, kind)

    def scan(self, ids: PaperIds) -> Scan:
        """The library scanned once, for :meth:`find_source_in` and :meth:`note_use`.

        :raises LibraryStoreFailure: the scan failed; the message names *ids* and the library's
            error.
        """
        try:
            return self.library.scan()
        except LibraryError as e:
            raise _failure(ids, None, e) from e

    def find_source_in(self, scan: Scan, ids: PaperIds, kind: SourceKind) -> Held | None:
        """The paper's newest source file of *kind* in *scan*, read and digest-checked, or
        ``None``.

        Looks in the paper's whole linked group of folders (:func:`find`, not only an
        exact-identifier folder) for files of *kind* that are on disk and that at least one
        record lists under identifiers not contradicting *ids* (field by field; a field absent on
        either side agrees), so a file only another paper's records list is never served. Each
        such file ranks by the earliest ``saved_at`` among the records listing it, since noting
        a use stamps the time of the note; the latest wins, and ties go to the lower digest. A
        ``saved_at`` not of the exact form ``YYYY-MM-DDTHH:MM:SSZ`` counts only when no record
        has a well-formed one, and then sorts below every well-formed one; fixed-width UTC
        compares in time order as a string. If the newest file cannot be read that is an error:
        a tampered file is reported, not skipped for an older one.

        :raises LibraryStoreFailure: the read failed; the message names the paper's identifiers,
            the file and the library's error.
        """
        # Per digest: its earliest well-formed ``saved_at`` (``""`` when none), its file, and
        # whether a record not contradicting *ids* lists it.
        seen: dict[str, tuple[str, FileRef, bool]] = {}
        for folder in find(scan, ids):
            for record in folder.records:
                agrees = contradiction(record.paper.ids, ids) is None
                for entry in record.files:
                    if entry.kind != kind or entry.name not in folder.files_on_disk:
                        continue
                    at = _saved_at_key(entry.saved_at)
                    file = FileRef(folder.folder, entry.name, entry.sha256, kind)
                    if entry.sha256 in seen:
                        was, file, ok = seen[entry.sha256]
                        at, agrees = _earliest(was, at), agrees or ok
                    seen[entry.sha256] = (at, file, agrees)
        best: tuple[str, FileRef] | None = None
        for at, file, ok in seen.values():
            # Later ``saved_at`` wins; on a tie the lower digest does.
            if ok and (best is None or (at, best[1].sha256) > (best[0], file.sha256)):
                best = (at, file)
        if best is None:
            return None
        file = best[1]
        try:
            return Held(file, self.library.read(file))
        except LibraryError as e:
            raise _failure(ids, (file.folder, file.name), e) from e

    def keep(self, paper: PaperInfo, data: bytes, kind: SourceKind, origin: str) -> FileRef:
        """Save *data* for *paper* in the library, as this installation.

        :raises LibraryStoreFailure: the save failed; the message names the paper and the cause.
        """
        try:
            return self.library.save(self.who, paper, data, kind, origin)
        except LibraryError as e:
            raise _failure(paper.ids, None, e) from e

    def note_use(self, paper: PaperInfo, file: FileRef) -> bool:
        """Note in this installation's record that it uses *file*: one scan of the library, then
        :meth:`note_use_in`.

        :raises LibraryStoreFailure: the scan or the write failed; the message names the paper,
            the file and the cause.
        """
        return self.note_use_in(self.scan(paper.ids), paper, file)

    def note_use_in(self, scan: Scan, paper: PaperInfo, file: FileRef) -> bool:
        """Note in this installation's record that it uses *file*, unless that record (in the
        file's folder) already lists the digest in *scan*, in which case nothing is written.
        Returns whether a use was written. A *scan* older than the record only costs a write
        that changes nothing.

        :raises LibraryStoreFailure: the write failed; the message names the paper, the file and
            the cause.
        """
        already = any(
            r.installation.app == self.who.app
            and r.installation.id == self.who.id
            and any(e.sha256 == file.sha256 for e in r.files)
            for f in scan.folders
            if f.folder == file.folder
            for r in f.records
        )
        if already:
            return False
        try:
            self.library.record_use(self.who, paper, file)
        except LibraryError as e:
            raise _failure(paper.ids, (file.folder, file.name), e) from e
        return True

    def path_of(self, file: FileRef) -> Path:
        """The path of *file* inside the library: ``root/papers/<folder>/<name>``.

        :raises LibraryStoreFailure: the folder or the name is not one safe path component.
        """
        try:
            return self.library.root / PAPERS_DIR / component(file.folder) / component(file.name)
        except LibraryError as e:
            raise _failure(PaperIds(), (file.folder, file.name), e) from e


def paper_info(ids: PaperIds, article: JATSArticle | None) -> PaperInfo:
    """What the library needs to know of a paper: *ids* plus, from the JATS when there is one,
    the title, the year and the first author.

    The year is the JATS ``year`` when it is all ASCII digits, else ``None``. The first author is
    the first contributor's surname, or their collaboration name when there is no surname. The
    JATS's own identifiers fill the ones *ids* lacks; each goes through :func:`library_ids`, so a
    value the library refuses is left out.
    """
    if article is None:
        return PaperInfo(ids)
    own, _ = library_ids(article.pmid, article.doi, article.pmc_id)
    year = article.year.strip()
    first = article.authors[0] if article.authors else None
    return PaperInfo(
        ids=ids.merged(own)[0],
        title=_non_blank(article.title),
        year=_year(year),
        first_author=(_non_blank(first.surname) or _non_blank(first.collab)) if first else None,
    )


def _non_blank(text: str) -> str | None:
    """*text* unchanged unless it is only whitespace, then ``None``."""
    return text if text.strip() else None


def _year(text: str) -> int | None:
    """*text* as a year when all ASCII digits and within ``i32``, else ``None``."""
    if not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    return value if _YEAR_MIN <= value <= _YEAR_MAX else None
