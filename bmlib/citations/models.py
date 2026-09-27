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

"""Data models for citation parsing and reference formatting.

Ported from bmlibrarian's ``writing.models`` / ``writing.constants``
(subset). The behaviour changes from upstream — ordinary field equality on
:class:`Citation`, the author-string split fix in
:meth:`DocumentMetadata.from_dict` — are argued in
``docs/superpowers/specs/2026-08-06-citations-port-design.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Final


class CitationStyle(StrEnum):
    """Supported citation formatting styles."""

    VANCOUVER = "vancouver"
    APA = "apa"
    HARVARD = "harvard"
    CHICAGO = "chicago"


DEFAULT_CITATION_STYLE: Final[CitationStyle] = CitationStyle.VANCOUVER
"""Vancouver — the numbered style medical journals use."""


def author_surname(author: str) -> str:
    """Extract the surname from one author name in either common format.

    ``"Surname, Firstname"`` yields everything before the comma;
    ``"Firstname Surname"`` yields the last whitespace-separated word (a
    naive, upstream-faithful split — particles like ``van der`` are kept
    only in the inverted format).

    Args:
        author: A single author name.

    Returns:
        The surname, or ``"Unknown"`` for a blank name.
    """
    author = author.strip()
    if "," in author:
        return author.split(",")[0].strip()
    parts = author.split()
    return parts[-1] if parts else "Unknown"


def _named_authors(authors: list[str]) -> list[str]:
    """Drop whitespace-only entries — a blank string is no author.

    Upstream's author helpers ran ``parts[-1]`` on an empty split, so one
    blank entry crashed every style's ``format_reference`` with an
    ``IndexError``; an all-blank list falls through to each style's
    "Unknown author" branch. **One rule for every path that reads the list**
    — the reference renderers, the inline renderers and
    :meth:`DocumentMetadata.get_first_author_surname` — because the inline
    path counting the raw list read two named authors and a blank as three,
    and attributed a blank first author to ``Unknown`` (issue #296).
    """
    return [author for author in authors if author.strip()]


@dataclass
class Citation:
    """A citation marker found in document text.

    Unlike upstream, two citations compare equal only when *all* fields
    match — upstream's equality by ``document_id`` alone made markers of one
    document at different positions collapse in sets.

    Attributes:
        document_id: Database id of the cited document.
        label: Human-readable label (e.g. ``"Smith2023"``).
        position: Character offset of the marker in the source text.
        text: The full marker text (e.g. ``"[@id:12345:Smith2023]"``).
    """

    document_id: int
    label: str
    position: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Citation:
        """Deserialise from :meth:`to_dict` output."""
        return cls(
            document_id=data["document_id"],
            label=data["label"],
            position=data["position"],
            text=data["text"],
        )


@dataclass
class DocumentMetadata:
    """Bibliographic metadata for one cited document.

    Attributes:
        document_id: Database id of the document.
        title: Document title.
        authors: Author names, each ``"Surname, Firstname"`` or
            ``"Firstname Surname"``.
        journal: Journal name.
        year: Publication year.
        pmid: PubMed id, if any.
        doi: DOI, if any.
        volume: Journal volume.
        issue: Journal issue.
        pages: Page range (e.g. ``"123-134"``).
        publication_date: Full publication date as text.
    """

    document_id: int
    title: str
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    year: int | None = None
    pmid: str | None = None
    doi: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publication_date: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentMetadata:
        """Deserialise from a plain dictionary.

        ``authors`` may be a list or a single string. A string splits on
        ``";"`` when one is present, else on ``","`` — semicolons are how
        inverted names (``"Smith, John; Doe, Jane"``) stay whole, which
        upstream broke by treating both separators alike. A list keeps its
        named entries as given and drops the rest: a ``None`` entry or a
        non-string used to reach the formatter, where ``.strip()`` raised out
        of every reference renderer, and a blank string is no author (issue
        #296). A ``None`` in place of the list — a NULL column — is no
        authors.
        """
        # A NULL column (``None``) is no authors, as it was before the list
        # branch below existed: `generate_label()` read it as "Unknown".
        authors = data.get("authors") or []
        if isinstance(authors, str):
            separator = ";" if ";" in authors else ","
            authors = [author.strip() for author in authors.split(separator) if author.strip()]
        elif isinstance(authors, list):
            authors = _named_authors([author for author in authors if isinstance(author, str)])
        return cls(
            document_id=data.get("id") or data.get("document_id", 0),
            title=data.get("title", ""),
            authors=authors,
            journal=data.get("journal"),
            year=data.get("year"),
            pmid=str(data["pmid"]) if data.get("pmid") else None,
            doi=data.get("doi"),
            volume=data.get("volume"),
            issue=data.get("issue"),
            pages=data.get("pages"),
            publication_date=data.get("publication_date"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return asdict(self)

    def get_first_author_surname(self) -> str:
        """The first *named* author's surname, or ``"Unknown"`` without one.

        A blank entry is skipped, as every renderer skips it — otherwise the
        label :meth:`generate_label` returns, which callers pass to
        ``create_citation_marker()`` and so into stored text, reads
        ``Unknown2023`` for a document whose reference names its authors
        (issue #296).
        """
        named = _named_authors(self.authors)
        if not named:
            return "Unknown"
        return author_surname(named[0])

    def generate_label(self) -> str:
        """A citation label like ``"Smith2023"`` (``"Smithn.d."`` sans year)."""
        year = self.year if self.year is not None else "n.d."
        return f"{self.get_first_author_surname()}{year}"


@dataclass
class FormattedReference:
    """One formatted bibliography entry.

    Attributes:
        number: Sequential reference number (1-based, order of first
            appearance in the text).
        document_id: Database id of the referenced document.
        formatted_text: The full formatted bibliographic entry.
        metadata: The source metadata, or ``None`` for a placeholder entry
            whose document the caller could not supply.
    """

    number: int
    document_id: int
    formatted_text: str
    metadata: DocumentMetadata | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary (metadata nested or ``None``)."""
        return {
            "number": self.number,
            "document_id": self.document_id,
            "formatted_text": self.formatted_text,
            "metadata": self.metadata.to_dict() if self.metadata else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FormattedReference:
        """Deserialise from :meth:`to_dict` output."""
        metadata = data.get("metadata")
        return cls(
            number=data["number"],
            document_id=data["document_id"],
            formatted_text=data["formatted_text"],
            metadata=DocumentMetadata.from_dict(metadata) if metadata else None,
        )
