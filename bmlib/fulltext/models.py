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

"""Data models for full-text retrieval and JATS XML parsing.

Mirrors the Swift BioMedLit library's JATSModels and FullTextResult types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JATSAuthorInfo:
    """Parsed author information from a JATS article."""

    surname: str
    given_names: str = ""
    affiliations: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        if not self.given_names:
            return self.surname
        return f"{self.given_names} {self.surname}"


@dataclass
class JATSAbstractSection:
    """Parsed abstract section (e.g. Background, Methods)."""

    title: str
    content: str


@dataclass
class JATSBodySection:
    """Parsed body section with nested subsections."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    subsections: list[JATSBodySection] = field(default_factory=list)


@dataclass
class JATSFigureInfo:
    """Parsed figure metadata."""

    id: str
    label: str
    caption: str
    graphic_url: str | None = None


@dataclass
class JATSTableInfo:
    """Parsed table metadata with pre-rendered HTML content."""

    id: str
    label: str
    caption: str
    html_content: str = ""


@dataclass
class JATSReferenceInfo:
    """Parsed reference/citation information."""

    id: str
    label: str
    citation: str
    authors: list[str] = field(default_factory=list)
    article_title: str = ""
    source: str = ""
    year: str = ""
    volume: str = ""
    issue: str = ""
    first_page: str = ""
    last_page: str = ""
    doi: str = ""
    pmid: str = ""

    @property
    def formatted_citation(self) -> str:
        parts: list[str] = []
        if self.authors:
            if len(self.authors) <= 3:
                parts.append(", ".join(self.authors))
            else:
                parts.append(f"{self.authors[0]}, {self.authors[1]}, et al.")
        if self.article_title:
            parts.append(self.article_title)
        if self.source:
            parts.append(self.source)
        if self.year:
            parts.append(f"({self.year})")
        volume_info = ""
        if self.volume:
            volume_info = self.volume
            if self.issue:
                volume_info += f"({self.issue})"
        if self.first_page:
            if volume_info:
                volume_info += ":"
            volume_info += self.first_page
            if self.last_page:
                volume_info += f"-{self.last_page}"
        if volume_info:
            parts.append(volume_info)
        if self.doi:
            parts.append(f"doi:{self.doi}")
        if not parts:
            return self.citation
        return ". ".join(parts)


@dataclass
class JATSArticle:
    """Complete parsed JATS article data."""

    title: str
    authors: list[JATSAuthorInfo]
    journal: str
    volume: str
    issue: str
    pages: str
    year: str
    doi: str
    pmc_id: str
    pmid: str
    abstract_sections: list[JATSAbstractSection]
    body_sections: list[JATSBodySection]
    figures: list[JATSFigureInfo]
    tables: list[JATSTableInfo]
    references: list[JATSReferenceInfo]


@dataclass
class FullTextSourceEntry:
    """A known full-text source URL discovered by a fetcher.

    Produced by publication fetchers, consumed by :class:`FullTextService`.
    """

    url: str
    format: str  # "pdf", "xml", "html"
    source: str  # e.g. "biorxiv", "medrxiv", "pmc", "publisher"
    open_access: bool = True
    version: str | None = None  # e.g. "preprint", "accepted", "published"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "url": self.url,
            "format": self.format,
            "source": self.source,
            "open_access": self.open_access,
        }
        if self.version:
            d["version"] = self.version
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullTextSourceEntry:
        return cls(
            url=data["url"],
            format=data["format"],
            source=data["source"],
            open_access=data.get("open_access", True),
            version=data.get("version"),
        )


@dataclass
class FullTextResult:
    """Result of a full-text retrieval attempt."""

    source: str  # "europepmc", "unpaywall", "doi", "cached"
    html: str | None = None
    pdf_url: str | None = None
    web_url: str | None = None
    file_path: str | None = None
