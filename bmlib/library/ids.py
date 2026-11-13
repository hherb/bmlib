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

"""Source kinds, canonical identifiers and installations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from bmlib.library.constants import MAX_LABEL_CHARS
from bmlib.library.errors import BadIdentifier, BadInstallation
from bmlib.publications.storage import _normalize_doi

_APP = re.compile(r"[a-z0-9]{1,40}")
_ID = re.compile(r"[a-z0-9-]{1,64}")
_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def is_bidi_control(c: str) -> bool:
    """True for the bidirectional control characters that could disguise a label."""
    return c in _BIDI


def _is_control(c: str) -> bool:
    """True for a Unicode Cc character (Rust's ``char::is_control``)."""
    return ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F


class SourceKind(str, Enum):  # noqa: UP042 (the brief fixes str + Enum)
    """What a stored source file is. The values are the hand-off format's names."""

    PDF = "pdf"
    JATS = "jats"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"

    def extension(self) -> str:
        """The file extension, without a dot."""
        return {"jats": "xml", "markdown": "md", "text": "txt"}.get(self.value, self.value)

    @classmethod
    def parse(cls, name: str) -> SourceKind | None:
        """The kind named ``name`` (exactly, lower-case), or ``None``."""
        try:
            return cls(name)
        except ValueError:
            return None


def _ascii_digits(s: str) -> bool:
    return s != "" and s.isascii() and s.isdigit()


def _digits_without_zeros(s: str) -> str | None:
    """Leading zeros stripped from ASCII digits; ``None`` for none or all zeros."""
    if not _ascii_digits(s):
        return None
    return s.lstrip("0") or None


def _canonical_pmid(raw: str) -> str:
    s = raw.strip()
    if s[:3].isascii() and s[:3].upper() == "PPR":
        if not _ascii_digits(s[3:]):
            raise BadIdentifier("pmid", raw)
        return "PPR" + s[3:]
    digits = _digits_without_zeros(s)
    if digits is None:
        raise BadIdentifier("pmid", raw)
    return digits


def _canonical_pmcid(raw: str) -> str:
    s = raw.strip()
    if s[:3].isascii() and s[:3].upper() == "PMC":
        s = s[3:]
    digits = _digits_without_zeros(s)
    if digits is None:
        raise BadIdentifier("pmcid", raw)
    return "PMC" + digits


def _canonical_doi(raw: str) -> str | None:
    doi = _normalize_doi(raw)
    if doi is None:
        return None
    if not (doi.startswith("10.") and "/" in doi):
        raise BadIdentifier("doi", raw)
    return doi


@dataclass(frozen=True)
class PaperIds:
    """A paper's identifiers, each in canonical form or absent."""

    pmid: str | None = None
    doi: str | None = None
    pmcid: str | None = None

    def is_empty(self) -> bool:
        """True when no identifier is known."""
        return self.pmid is None and self.doi is None and self.pmcid is None

    def shares_any(self, other: PaperIds) -> bool:
        """True when both hold the same value for at least one identifier."""
        return any(
            a is not None and a == b
            for a, b in ((self.pmid, other.pmid), (self.doi, other.doi), (self.pmcid, other.pmcid))
        )

    def merged(self, other: PaperIds) -> tuple[PaperIds, list[str]]:
        """``self`` with its gaps filled from ``other``.

        Where both hold different values ``self``'s is kept and the field's name is in
        the second item.
        """
        conflicts: list[str] = []
        picked: dict[str, str | None] = {}
        for field in ("pmid", "doi", "pmcid"):
            a, b = getattr(self, field), getattr(other, field)
            if a is not None and b is not None and a != b:
                conflicts.append(field)
            picked[field] = b if a is None else a
        return PaperIds(**picked), conflicts


def canonical_ids(
    pmid: str | None = None, doi: str | None = None, pmcid: str | None = None
) -> PaperIds:
    """Canonical identifiers from raw ones; blank values become ``None``.

    Raises:
        BadIdentifier: naming the first field that is not valid.
    """

    def present(v: str | None) -> str | None:
        return v if v is not None and v.strip() else None

    p, d, c = present(pmid), present(doi), present(pmcid)
    return PaperIds(
        pmid=None if p is None else _canonical_pmid(p),
        doi=None if d is None else _canonical_doi(d),
        pmcid=None if c is None else _canonical_pmcid(c),
    )


@dataclass(frozen=True)
class PaperInfo:
    """What a folder name is built from."""

    ids: PaperIds
    title: str | None = None
    year: int | None = None
    first_author: str | None = None


@dataclass(frozen=True)
class Installation:
    """One app on one machine, which writes its own record per paper."""

    app: str
    id: str
    label: str

    @classmethod
    def new(cls, app: str, id: str, label: str) -> Installation:
        """A validated installation.

        Raises:
            BadInstallation: naming the field that is not acceptable.
        """
        if not _APP.fullmatch(app):
            raise BadInstallation("app", "1-40 characters of a-z and 0-9")
        if not _ID.fullmatch(id):
            raise BadInstallation("id", "1-64 characters of a-z, 0-9 and -")
        if not 1 <= len(label) <= MAX_LABEL_CHARS:
            raise BadInstallation("label", "must be 1 to 200 characters")
        if any(_is_control(c) or is_bidi_control(c) for c in label):
            raise BadInstallation("label", "must not hold control characters")
        return cls(app, id, label)

    def record_file_name(self) -> str:
        """The name of this installation's record file in a paper's folder."""
        return f"{self.app}-{self.id}.json"
