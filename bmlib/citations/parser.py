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

"""Pure functions over the ``[@id:N:Label]`` citation-marker format.

A marker looks like ``[@id:12345:Smith2023]``: the ``@id:`` prefix, the
integer document id, and a human-readable label. Ported from bmlibrarian's
``writing.citation_parser``, with the stateless class dissolved into module
functions; the validation fix (``fullmatch`` where upstream anchored only
the start) is argued in
``docs/superpowers/specs/2026-08-06-citations-port-design.md``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from bmlib.citations.models import Citation

CITATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[@id:(\d+):([^\]]+)\]")
"""One citation marker: ``[@id:<digits>:<label without ']'>]``."""

_MAX_LABEL_LENGTH: Final[int] = 100

_ADJACENT_GAP: Final[re.Pattern[str]] = re.compile(r"^[\s,]*$")


def parse_citations(text: str) -> list[Citation]:
    """Extract every citation marker from *text*, in order of appearance."""
    return [
        Citation(
            document_id=int(match.group(1)),
            label=match.group(2),
            position=match.start(),
            text=match.group(0),
        )
        for match in CITATION_PATTERN.finditer(text)
    ]


def unique_document_ids(text: str) -> list[int]:
    """Unique cited document ids, in order of first appearance."""
    seen: set[int] = set()
    ordered: list[int] = []
    for citation in parse_citations(text):
        if citation.document_id not in seen:
            seen.add(citation.document_id)
            ordered.append(citation.document_id)
    return ordered


def count_citations(text: str) -> int:
    """Number of citation markers in *text* (repeats count every time)."""
    return sum(1 for _ in CITATION_PATTERN.finditer(text))


def count_unique_citations(text: str) -> int:
    """Number of distinct documents cited in *text*."""
    return len(unique_document_ids(text))


def citation_positions(text: str) -> dict[int, list[int]]:
    """Character positions of every marker, grouped by document id."""
    positions: dict[int, list[int]] = {}
    for citation in parse_citations(text):
        positions.setdefault(citation.document_id, []).append(citation.position)
    return positions


def citations_in_range(text: str, start: int, end: int) -> list[Citation]:
    """Citations whose marker starts in ``[start, end)``."""
    return [c for c in parse_citations(text) if start <= c.position < end]


def create_citation_marker(document_id: int, label: str) -> str:
    """Build the marker string for a document id and label."""
    return f"[@id:{document_id}:{label}]"


def replace_citation_with_number(text: str, document_id: int, number: int) -> str:
    """Replace every marker of one document with ``[number]``."""
    specific = re.compile(rf"\[@id:{document_id}:[^\]]+\]")
    return specific.sub(f"[{number}]", text)


def replace_all_citations_with_numbers(text: str, id_to_number: Mapping[int, int]) -> str:
    """Replace each mapped marker with ``[number]``; unmapped markers stay."""

    def _replace(match: re.Match[str]) -> str:
        number = id_to_number.get(int(match.group(1)))
        return match.group(0) if number is None else f"[{number}]"

    return CITATION_PATTERN.sub(_replace, text)


def find_adjacent_citations(text: str) -> list[list[Citation]]:
    """Group markers separated only by whitespace and/or commas.

    Adjacent groups are what a numbered style renders as a combined
    reference such as ``[1-3]``.
    """
    citations = parse_citations(text)
    if not citations:
        return []
    groups: list[list[Citation]] = []
    current: list[Citation] = [citations[0]]
    for previous, citation in zip(citations, citations[1:]):
        gap = text[previous.position + len(previous.text) : citation.position]
        if _ADJACENT_GAP.match(gap):
            current.append(citation)
        else:
            groups.append(current)
            current = [citation]
    groups.append(current)
    return groups


def format_citation_group(
    citations: list[Citation],
    id_to_number: Mapping[int, int],
    combine_sequential: bool = True,
) -> str:
    """Format one adjacent group as ``[1,2]``, ``[1-3]``, or ``[1-3,5]``.

    Citations whose id has no number are skipped; an entirely unnumbered
    group formats as the empty string.
    """
    if not citations:
        return ""
    numbers = sorted(
        {id_to_number[c.document_id] for c in citations if c.document_id in id_to_number}
    )
    if not numbers:
        return ""
    if not combine_sequential or len(numbers) <= 2:
        return f"[{','.join(str(n) for n in numbers)}]"
    runs: list[str] = []
    start = end = numbers[0]
    for number in numbers[1:]:
        if number == end + 1:
            end = number
        else:
            runs.append(_format_run(start, end))
            start = end = number
    runs.append(_format_run(start, end))
    return f"[{','.join(runs)}]"


def _format_run(start: int, end: int) -> str:
    """One maximal consecutive run: ``"1-3"``, ``"1,2"``, or ``"1"``."""
    if end > start + 1:
        return f"{start}-{end}"
    if end > start:
        return f"{start},{end}"
    return str(start)


def validate_citation_marker(marker: str) -> tuple[bool, str | None]:
    """Check that *marker* is exactly one well-formed citation marker.

    Returns:
        ``(True, None)``, or ``(False, reason)``. The whole string must be
        the marker — upstream anchored only the start, so trailing text
        validated.
    """
    match = CITATION_PATTERN.fullmatch(marker)
    if not match:
        return False, "Invalid citation format. Expected: [@id:NUMBER:LABEL]"
    if int(match.group(1)) <= 0:
        return False, "Document ID must be a positive integer"
    if len(match.group(2)) > _MAX_LABEL_LENGTH:
        return False, f"Label must be 1-{_MAX_LABEL_LENGTH} characters"
    return True, None


def extract_label_from_citation(marker: str) -> str | None:
    """The label of a marker string, or ``None`` if it is not one marker."""
    match = CITATION_PATTERN.fullmatch(marker)
    return match.group(2) if match else None


def extract_document_id_from_citation(marker: str) -> int | None:
    """The document id of a marker string, or ``None`` if not one marker."""
    match = CITATION_PATTERN.fullmatch(marker)
    return int(match.group(1)) if match else None
