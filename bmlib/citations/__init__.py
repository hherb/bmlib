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

"""Citation parsing, reference formatting, and reference-list building.

Ported from bmlibrarian's ``writing`` package (Phase 2 row 4 of the
porting analysis), with the database-backed pieces severed: the caller
supplies ``DocumentMetadata``, and everything here is pure stdlib.

Example::

    from bmlib.citations import DocumentMetadata, build_references

    metadata = {
        1: DocumentMetadata(
            document_id=1,
            title="Statin therapy and LDL",
            authors=["John Smith"],
            journal="J Lipid",
            year=2021,
        ),
    }
    formatted, references = build_references("Shown in [@id:1:Smith2021].", metadata)
"""

from bmlib.citations.builder import build_references, find_missing_documents, format_document
from bmlib.citations.formatter import (
    MAX_AUTHORS_BEFORE_ET_AL,
    APAFormatter,
    BaseFormatter,
    ChicagoFormatter,
    CitationFormatter,
    HarvardFormatter,
    VancouverFormatter,
)
from bmlib.citations.models import (
    DEFAULT_CITATION_STYLE,
    Citation,
    CitationStyle,
    DocumentMetadata,
    FormattedReference,
    author_surname,
)
from bmlib.citations.parser import (
    CITATION_PATTERN,
    citation_positions,
    citations_in_range,
    count_citations,
    count_unique_citations,
    create_citation_marker,
    extract_document_id_from_citation,
    extract_label_from_citation,
    find_adjacent_citations,
    format_citation_group,
    parse_citations,
    replace_all_citations_with_numbers,
    replace_citation_with_number,
    unique_document_ids,
    validate_citation_marker,
)

__all__ = [
    "CITATION_PATTERN",
    "DEFAULT_CITATION_STYLE",
    "MAX_AUTHORS_BEFORE_ET_AL",
    "APAFormatter",
    "BaseFormatter",
    "ChicagoFormatter",
    "Citation",
    "CitationFormatter",
    "CitationStyle",
    "DocumentMetadata",
    "FormattedReference",
    "HarvardFormatter",
    "VancouverFormatter",
    "author_surname",
    "build_references",
    "citation_positions",
    "citations_in_range",
    "count_citations",
    "count_unique_citations",
    "create_citation_marker",
    "extract_document_id_from_citation",
    "extract_label_from_citation",
    "find_adjacent_citations",
    "find_missing_documents",
    "format_citation_group",
    "format_document",
    "parse_citations",
    "replace_all_citations_with_numbers",
    "replace_citation_with_number",
    "unique_document_ids",
    "validate_citation_marker",
]
