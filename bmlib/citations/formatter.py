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

"""Bibliographic reference and inline-citation formatters.

Four styles: Vancouver (numbered — the default for medical journals), APA,
Harvard, and Chicago (author–date). Ported from bmlibrarian's
``writing.citation_formatter``; output is preserved exactly as upstream's
*code* produced it (upstream's docstring examples disagree with its code in
places; the code wins), except the doubled terminal period in APA/Chicago
author blocks — argued in
``docs/superpowers/specs/2026-08-06-citations-port-design.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Final

from bmlib.citations.models import (
    DEFAULT_CITATION_STYLE,
    CitationStyle,
    DocumentMetadata,
    FormattedReference,
    author_surname,
)

MAX_AUTHORS_BEFORE_ET_AL: Final[int] = 6
"""Author-list length beyond which each style applies its truncation."""


def _terminated(author_block: str) -> str:
    """Append the terminal period unless the block already ends with one."""
    return author_block if author_block.endswith(".") else author_block + "."


class BaseFormatter(ABC):
    """Formats references and inline citations for one citation style."""

    @abstractmethod
    def format_reference(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        """Format a full bibliographic reference.

        Args:
            metadata: The cited document.
            number: Reference number, used by numbered styles.
        """

    @abstractmethod
    def format_inline_citation(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        """Format an inline citation for running text."""

    def _format_title(self, title: str) -> str:
        """Title with a guaranteed trailing period (``"Untitled"`` if empty)."""
        if not title:
            return "Untitled"
        title = title.strip()
        if not title.endswith("."):
            title += "."
        return title

    def _format_journal(self, journal: str | None) -> str:
        """Journal name italicised for markdown, or empty."""
        if not journal:
            return ""
        return f"*{journal}*"


class VancouverFormatter(BaseFormatter):
    """Vancouver style — numbered references, surname-plus-initials authors.

    Example::

        1. Smith J, Johnson A. Title of the article. *Journal Name*.
        2023;45(2):123-134. doi:10.1234/example
    """

    def format_reference(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        parts = []
        if number is not None:
            parts.append(f"{number}.")
        parts.append(self._format_authors(metadata.authors))
        parts.append(self._format_title(metadata.title))
        if metadata.journal:
            journal_part = self._format_journal(metadata.journal)
            year_and_volume = []
            if metadata.year:
                year_and_volume.append(str(metadata.year))
            if metadata.volume:
                volume = metadata.volume
                if metadata.issue:
                    volume += f"({metadata.issue})"
                year_and_volume.append(volume)
            if year_and_volume:
                journal_part += f". {';'.join(year_and_volume)}"
            if metadata.pages:
                journal_part += f":{metadata.pages}"
            parts.append(journal_part + ".")
        if metadata.doi:
            parts.append(f"doi:{metadata.doi}")
        elif metadata.pmid:
            parts.append(f"PMID:{metadata.pmid}")
        return " ".join(parts)

    def format_inline_citation(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        if number is not None:
            return f"[{number}]"
        return f"[{metadata.document_id}]"

    def _format_authors(self, authors: list[str]) -> str:
        if not authors:
            return "Unknown author."
        formatted = []
        for i, author in enumerate(authors):
            if i >= MAX_AUTHORS_BEFORE_ET_AL:
                formatted.append("et al")
                break
            formatted.append(self._surname_and_initials(author))
        return ", ".join(formatted) + "."

    def _surname_and_initials(self, author: str) -> str:
        """``"John A. Smith"`` / ``"Smith, John A."`` → ``"Smith JA"``."""
        author = author.strip()
        if "," in author:
            surname, _, given = author.partition(",")
            initials = "".join(n[0].upper() for n in given.split() if n)
            return f"{surname.strip()} {initials}"
        parts = author.split()
        if len(parts) == 1:
            return parts[0]
        initials = "".join(n[0].upper() for n in parts[:-1] if n)
        return f"{parts[-1]} {initials}"


class APAFormatter(BaseFormatter):
    """APA style — author–date, ``Surname, I.`` authors.

    Example::

        Smith, J., Johnson, A., & Williams, B. (2023) Title of the article.
        *Journal Name*, *45*(2), 123-134. https://doi.org/10.1234/example
    """

    def format_reference(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        parts = [self._format_authors(metadata.authors)]
        parts.append(f"({metadata.year})" if metadata.year else "(n.d.)")
        parts.append(self._format_title(metadata.title))
        if metadata.journal:
            journal_part = self._format_journal(metadata.journal)
            if metadata.volume:
                journal_part += f", *{metadata.volume}*"
                if metadata.issue:
                    journal_part += f"({metadata.issue})"
            if metadata.pages:
                journal_part += f", {metadata.pages}"
            parts.append(journal_part + ".")
        if metadata.doi:
            parts.append(f"https://doi.org/{metadata.doi}")
        return " ".join(parts)

    def format_inline_citation(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        surname = metadata.get_first_author_surname()
        year = metadata.year or "n.d."
        if len(metadata.authors) > 2:
            return f"({surname} et al., {year})"
        if len(metadata.authors) == 2:
            return f"({surname} & {author_surname(metadata.authors[1])}, {year})"
        return f"({surname}, {year})"

    def _format_authors(self, authors: list[str]) -> str:
        if not authors:
            return "Unknown author."
        formatted = []
        for i, author in enumerate(authors):
            # Upstream guarded `i >= MAX + 1: break` before the `i == MAX`
            # ellipsis branch, which always breaks first — `>` here is the
            # same behaviour for every input length.
            if i > MAX_AUTHORS_BEFORE_ET_AL:
                break
            if i == MAX_AUTHORS_BEFORE_ET_AL:
                formatted.append("...")
                formatted.append(self._surname_and_initials(authors[-1]))
                break
            formatted.append(self._surname_and_initials(author))
        if len(formatted) == 1:
            return _terminated(formatted[0])
        if len(formatted) == 2:
            return _terminated(f"{formatted[0]} & {formatted[1]}")
        return _terminated(", ".join(formatted[:-1]) + ", & " + formatted[-1])

    def _surname_and_initials(self, author: str) -> str:
        """``"John Smith"`` / ``"Smith, John"`` → ``"Smith, J."``."""
        author = author.strip()
        if "," in author:
            surname, _, given = author.partition(",")
            initials = ". ".join(n[0].upper() for n in given.split() if n)
            if initials:
                initials += "."
            return f"{surname.strip()}, {initials}"
        parts = author.split()
        if len(parts) == 1:
            return parts[0]
        initials = ". ".join(n[0].upper() for n in parts[:-1] if n)
        if initials:
            initials += "."
        return f"{parts[-1]}, {initials}"


class HarvardFormatter(BaseFormatter):
    """Harvard style — author–date, quoted title, ``pp.`` pages.

    Example::

        Smith, J., Johnson, A. and Williams, B. (2023) 'Title of the
        article', *Journal Name*, 45(2), pp. 123-134. doi: 10.1234/example.
    """

    def format_reference(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        parts = [self._format_authors(metadata.authors)]
        parts.append(f"({metadata.year})" if metadata.year else "(n.d.)")
        title = metadata.title.strip()
        if title.endswith("."):
            title = title[:-1]
        parts.append(f"'{title}',")
        if metadata.journal:
            journal_part = self._format_journal(metadata.journal)
            if metadata.volume:
                journal_part += f", {metadata.volume}"
                if metadata.issue:
                    journal_part += f"({metadata.issue})"
            if metadata.pages:
                journal_part += f", pp. {metadata.pages}"
            parts.append(journal_part + ".")
        if metadata.doi:
            parts.append(f"doi: {metadata.doi}.")
        return " ".join(parts)

    def format_inline_citation(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        surname = metadata.get_first_author_surname()
        year = metadata.year or "n.d."
        if len(metadata.authors) > 2:
            return f"({surname} et al., {year})"
        if len(metadata.authors) == 2:
            return f"({surname} and {author_surname(metadata.authors[1])}, {year})"
        return f"({surname}, {year})"

    def _format_authors(self, authors: list[str]) -> str:
        if not authors:
            return "Unknown author"
        formatted = []
        for i, author in enumerate(authors):
            if i >= MAX_AUTHORS_BEFORE_ET_AL:
                formatted.append("et al.")
                break
            formatted.append(self._surname_and_initials(author))
        if len(formatted) == 1:
            return formatted[0]
        if len(formatted) == 2:
            return f"{formatted[0]} and {formatted[1]}"
        return ", ".join(formatted[:-1]) + " and " + formatted[-1]

    def _surname_and_initials(self, author: str) -> str:
        """``"John Smith"`` / ``"Smith, John"`` → ``"Smith, J."`` (dots run together)."""
        author = author.strip()
        if "," in author:
            surname, _, given = author.partition(",")
            initials = ".".join(n[0].upper() for n in given.split() if n)
            if initials:
                initials += "."
            return f"{surname.strip()}, {initials}"
        parts = author.split()
        if len(parts) == 1:
            return parts[0]
        initials = ".".join(n[0].upper() for n in parts[:-1] if n)
        if initials:
            initials += "."
        return f"{parts[-1]}, {initials}"


class ChicagoFormatter(BaseFormatter):
    """Chicago author–date style — first author inverted, title in quotes.

    Example::

        Smith, John, Anna Johnson, and Brian Williams. 2023. "Title of the
        Article." *Journal Name* 45 (2): 123-134.
        https://doi.org/10.1234/example.
    """

    def format_reference(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        parts = [self._format_authors(metadata.authors)]
        parts.append(f"{metadata.year}." if metadata.year else "n.d.")
        title = metadata.title.strip()
        if title.endswith("."):
            title = title[:-1]
        parts.append(f'"{title}."')
        if metadata.journal:
            journal_part = self._format_journal(metadata.journal)
            if metadata.volume:
                journal_part += f" {metadata.volume}"
                if metadata.issue:
                    journal_part += f" ({metadata.issue})"
            if metadata.pages:
                journal_part += f": {metadata.pages}"
            parts.append(journal_part + ".")
        if metadata.doi:
            parts.append(f"https://doi.org/{metadata.doi}.")
        return " ".join(parts)

    def format_inline_citation(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        surname = metadata.get_first_author_surname()
        year = metadata.year or "n.d."
        if len(metadata.authors) > 2:
            return f"({surname} et al. {year})"
        if len(metadata.authors) == 2:
            return f"({surname} and {author_surname(metadata.authors[1])} {year})"
        return f"({surname} {year})"

    def _format_authors(self, authors: list[str]) -> str:
        if not authors:
            return "Unknown author."
        formatted = []
        for i, author in enumerate(authors):
            if i >= MAX_AUTHORS_BEFORE_ET_AL:
                formatted.append("et al")
                break
            formatted.append(self._inverted(author) if i == 0 else self._natural(author))
        if len(formatted) == 1:
            return _terminated(formatted[0])
        if len(formatted) == 2:
            return _terminated(f"{formatted[0]}, and {formatted[1]}")
        return _terminated(", ".join(formatted[:-1]) + ", and " + formatted[-1])

    def _inverted(self, author: str) -> str:
        """First author: ``"Surname, Firstname"``."""
        author = author.strip()
        if "," in author:
            return author
        parts = author.split()
        if len(parts) == 1:
            return parts[0]
        return f"{parts[-1]}, {' '.join(parts[:-1])}"

    def _natural(self, author: str) -> str:
        """Subsequent authors: ``"Firstname Surname"``."""
        author = author.strip()
        if "," in author:
            surname, _, given = author.partition(",")
            return f"{given.strip()} {surname.strip()}".strip()
        return author


class CitationFormatter:
    """Formats references and inline citations in a selectable style.

    Example::

        formatter = CitationFormatter(CitationStyle.VANCOUVER)
        reference = formatter.format_reference(metadata, number=1)
        inline = formatter.format_inline_citation(metadata, number=1)
    """

    _formatters: ClassVar[dict[CitationStyle, type[BaseFormatter]]] = {
        CitationStyle.VANCOUVER: VancouverFormatter,
        CitationStyle.APA: APAFormatter,
        CitationStyle.HARVARD: HarvardFormatter,
        CitationStyle.CHICAGO: ChicagoFormatter,
    }

    def __init__(self, style: CitationStyle = DEFAULT_CITATION_STYLE) -> None:
        """Create a formatter for *style* (Vancouver by default)."""
        self._style = style
        self._formatter = self._formatters[style]()

    @property
    def style(self) -> CitationStyle:
        """The active citation style."""
        return self._style

    @style.setter
    def style(self, new_style: CitationStyle) -> None:
        self._style = new_style
        self._formatter = self._formatters[new_style]()

    def format_reference(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        """Format a full bibliographic reference in the active style."""
        return self._formatter.format_reference(metadata, number)

    def format_inline_citation(self, metadata: DocumentMetadata, number: int | None = None) -> str:
        """Format an inline citation in the active style."""
        return self._formatter.format_inline_citation(metadata, number)

    def format_reference_list(self, references: list[FormattedReference]) -> str:
        """Render a complete markdown reference list."""
        lines = ["", "---", "", "## References", ""]
        for reference in references:
            lines.append(reference.formatted_text)
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def get_available_styles(cls) -> list[CitationStyle]:
        """All supported citation styles."""
        return list(cls._formatters)

    @classmethod
    def get_style_description(cls, style: CitationStyle) -> str:
        """A one-line human-readable description of *style*."""
        descriptions = {
            CitationStyle.VANCOUVER: "Vancouver (numbered, common in medical journals)",
            CitationStyle.APA: "APA (Author-Date, common in psychology and social sciences)",
            CitationStyle.HARVARD: "Harvard (Author-Date variant)",
            CitationStyle.CHICAGO: "Chicago (Author-Date, common in humanities)",
        }
        return descriptions.get(style, str(style.value))
