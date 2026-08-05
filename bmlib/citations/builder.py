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

"""Build numbered reference lists from citation markers in text.

The upstream ``ReferenceBuilder`` fetched document metadata from
bmlibrarian's PostgreSQL ``document`` table; here the caller supplies a
``Mapping[int, DocumentMetadata]`` and every function is pure. The
author–date inline-citation fix (upstream numbered every style) is argued
in ``docs/superpowers/specs/2026-08-06-citations-port-design.md``.

Example::

    from bmlib.citations import build_references

    text = "Statins lower LDL [@id:1:Smith2021] [@id:2:Doe2022]."
    formatted, references = build_references(text, metadata)
    # formatted == "Statins lower LDL [1,2]."
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from bmlib.citations.formatter import CitationFormatter
from bmlib.citations.models import (
    DEFAULT_CITATION_STYLE,
    Citation,
    CitationStyle,
    DocumentMetadata,
    FormattedReference,
)
from bmlib.citations.parser import (
    CITATION_PATTERN,
    find_adjacent_citations,
    format_citation_group,
    parse_citations,
    unique_document_ids,
)


def build_references(
    text: str,
    metadata: Mapping[int, DocumentMetadata],
    style: CitationStyle = DEFAULT_CITATION_STYLE,
    combine_sequential: bool = True,
) -> tuple[str, list[FormattedReference]]:
    """Number, format, and inline every citation in *text*.

    Documents are numbered by order of first appearance. Each unique cited
    document yields one :class:`FormattedReference`; a document id missing
    from *metadata* yields a visible ``[Document N not found]`` placeholder
    rather than disappearing. Markers in the text are replaced with ``[N]``
    (Vancouver, adjacent markers combined to e.g. ``[1-3]``) or with the
    style's author–date inline citation (APA/Harvard/Chicago; a marker
    whose document is missing stays verbatim, since an author–date citation
    needs the metadata's surname).

    Args:
        text: Document text containing ``[@id:N:Label]`` markers.
        metadata: The cited documents, keyed by document id.
        style: Citation style for references and inline citations.
        combine_sequential: Combine adjacent sequential numbers (``[1-3]``).

    Returns:
        ``(formatted_text, references)``.
    """
    document_ids = unique_document_ids(text)
    if not document_ids:
        return text, []

    formatter = CitationFormatter(style)
    id_to_number = {document_id: i + 1 for i, document_id in enumerate(document_ids)}

    references = []
    for document_id in document_ids:
        number = id_to_number[document_id]
        document = metadata.get(document_id)
        if document is not None:
            references.append(
                FormattedReference(
                    number=number,
                    document_id=document_id,
                    formatted_text=formatter.format_reference(document, number),
                    metadata=document,
                )
            )
        else:
            references.append(
                FormattedReference(
                    number=number,
                    document_id=document_id,
                    formatted_text=f"{number}. [Document {document_id} not found]",
                    metadata=None,
                )
            )

    replaced = _replace_citations(text, metadata, id_to_number, formatter, combine_sequential)
    return replaced, references


def format_document(
    text: str,
    metadata: Mapping[int, DocumentMetadata],
    style: CitationStyle = DEFAULT_CITATION_STYLE,
    include_reference_list: bool = True,
    combine_sequential: bool = True,
) -> str:
    """Format *text* and, by default, append the markdown reference list."""
    formatted_text, references = build_references(text, metadata, style, combine_sequential)
    if include_reference_list and references:
        formatted_text += CitationFormatter(style).format_reference_list(references)
    return formatted_text


def find_missing_documents(text: str, metadata: Mapping[int, DocumentMetadata]) -> list[Citation]:
    """Citations in *text* whose document id has no entry in *metadata*.

    One :class:`Citation` per marker, so a document cited twice is reported
    twice, each with its own position.
    """
    return [c for c in parse_citations(text) if c.document_id not in metadata]


def _replace_citations(
    text: str,
    metadata: Mapping[int, DocumentMetadata],
    id_to_number: Mapping[int, int],
    formatter: CitationFormatter,
    combine_sequential: bool,
) -> str:
    """Replace markers per the style: numbered groups or author–date."""
    if formatter.style == CitationStyle.VANCOUVER:
        groups = find_adjacent_citations(text)
        # Reverse order keeps the earlier groups' positions valid while
        # later spans are being replaced.
        for group in reversed(groups):
            if len(group) == 1:
                replacement = f"[{id_to_number[group[0].document_id]}]"
            else:
                replacement = format_citation_group(group, id_to_number, combine_sequential)
            start = group[0].position
            end = group[-1].position + len(group[-1].text)
            text = text[:start] + replacement + text[end:]
        return text

    def _inline(match: re.Match[str]) -> str:
        document = metadata.get(int(match.group(1)))
        if document is None:
            return match.group(0)
        return formatter.format_inline_citation(document)

    return CITATION_PATTERN.sub(_inline, text)
