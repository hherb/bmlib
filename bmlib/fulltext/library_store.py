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
    order. This function only reports; the caller logs the warning.
    """
    picked: dict[str, str | None] = {}
    dropped: list[str] = []
    for field, key, args in (
        ("pmid", "pmid", (pmid, None, None)),
        ("doi", "doi", (None, doi, None)),
        ("pmc_id", "pmcid", (None, None, pmc_id)),
    ):
        try:
            picked[key] = getattr(canonical_ids(*args), key)
        except BadIdentifier:
            dropped.append(field)
    return PaperIds(**picked), dropped


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
        """The paper's newest source file of *kind*, read and digest-checked, or ``None``.

        Looks in every folder of the paper (:func:`find`) for files of *kind* that some record
        lists and that are on disk, and takes the one with the latest ``saved_at`` across the
        records (ties go to the lower digest). ``saved_at`` is written as fixed-width UTC
        (``2026-11-14T09:30:00Z``), so comparing the strings orders them in time. If the newest
        file cannot be read that is an error: a tampered file is reported, not skipped for an
        older one.

        :raises LibraryStoreFailure: the scan or the read failed; the message names the paper's
            identifiers, the file and the library's error.
        """
        try:
            scan = self.library.scan()
        except LibraryError as e:
            raise _failure(ids, None, e) from e
        best: tuple[str, FileRef] | None = None
        for folder in find(scan, ids):
            for entry in (f for r in folder.records for f in r.files):
                if entry.kind != kind or entry.name not in folder.files_on_disk:
                    continue
                # Later ``saved_at`` wins; on a tie the lower digest does.
                if best is None or (entry.saved_at, best[1].sha256) > (best[0], entry.sha256):
                    best = (entry.saved_at, FileRef(folder.folder, entry.name, entry.sha256, kind))
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

    def note_use(self, paper: PaperInfo, file: FileRef) -> None:
        """Note in this installation's record that it uses *file*, unless that record (in the
        file's folder) already lists the digest, in which case nothing is written.

        :raises LibraryStoreFailure: the scan or the write failed; the message names the paper,
            the file and the cause.
        """
        at = (file.folder, file.name)
        try:
            scan = self.library.scan()
            already = any(
                r.installation.app == self.who.app
                and r.installation.id == self.who.id
                and any(e.sha256 == file.sha256 for e in r.files)
                for f in scan.folders
                if f.folder == file.folder
                for r in f.records
            )
            if not already:
                self.library.record_use(self.who, paper, file)
        except LibraryError as e:
            raise _failure(paper.ids, at, e) from e

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
        year=int(year) if year.isascii() and year.isdigit() else None,
        first_author=(_non_blank(first.surname) or _non_blank(first.collab)) if first else None,
    )


def _non_blank(text: str) -> str | None:
    """*text* unchanged unless it is only whitespace, then ``None``."""
    return text if text.strip() else None
