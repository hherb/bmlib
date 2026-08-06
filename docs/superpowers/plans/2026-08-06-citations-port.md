# bmlib/citations/ Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port bmlibrarian's citation/reference stack (Phase 2 row 4) into a
new pure-stdlib `bmlib/citations/` package: marker parsing, four citation
styles, and a DB-severed reference builder.

**Architecture:** Four modules — `models.py` (dataclasses + `CitationStyle`),
`parser.py` (pure functions over the `[@id:N:Label]` marker format),
`formatter.py` (ABC + four style formatters + facade), `builder.py` (pure
orchestration with caller-injected metadata). Spec:
`docs/superpowers/specs/2026-08-06-citations-port-design.md`. Upstream
source (the spec for behaviour): `~/src/bmlibrarian/src/bmlibrarian/writing/`.

**Tech Stack:** Python ≥3.11 stdlib only (`re`, `dataclasses`, `enum`,
`abc`). pytest for tests. No new dependency group.

## Global Constraints

- Every new source file starts with the 15-line AGPL header copied verbatim
  from `bmlib/fulltext/segmenter.py` (lines 1–15). Shown once in Task 1;
  every later file needs it too.
- `from __future__ import annotations` after the module docstring in every
  module; lowercase builtin generics (`list[str]`, `dict[int, int]`).
- Absolute imports: `from bmlib.citations.models import ...`.
- Type hints and Google-style docstrings on every public function, class,
  and module.
- String-valued enums use `enum.StrEnum` (the `RetractionNature` precedent).
- Tests: `uv run pytest tests/<file> -q` per step; full suite at the end.
- Lint with the CI-pinned ruff, not `.venv`'s:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- Upstream output is preserved exactly except the four defects named in the
  spec; upstream docstring examples that disagree with upstream code are
  resolved in favour of the code.
- Commit after each task with a conventional-commit message ending in the
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.

---

### Task 1: Models

**Files:**
- Create: `bmlib/citations/__init__.py` (minimal, filled out in Task 5)
- Create: `bmlib/citations/models.py`
- Test: `tests/test_citations_parser.py` (model test classes; parser classes join in Task 2)

**Interfaces:**
- Produces: `CitationStyle` (StrEnum: VANCOUVER/APA/HARVARD/CHICAGO),
  `DEFAULT_CITATION_STYLE`, `author_surname(author: str) -> str`,
  `Citation(document_id: int, label: str, position: int, text: str)`,
  `DocumentMetadata(document_id, title, authors, journal, year, pmid, doi,
  volume, issue, pages, publication_date)` with `from_dict()/to_dict()`,
  `get_first_author_surname()`, `generate_label()`,
  `FormattedReference(number, document_id, formatted_text, metadata)` with
  `from_dict()/to_dict()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_citations_parser.py`:

```python
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

"""Tests for bmlib.citations models and the citation-marker parser."""

from __future__ import annotations

from bmlib.citations.models import (
    DEFAULT_CITATION_STYLE,
    Citation,
    CitationStyle,
    DocumentMetadata,
    FormattedReference,
    author_surname,
)


class TestCitationStyle:
    def test_the_default_style_is_vancouver(self):
        assert DEFAULT_CITATION_STYLE is CitationStyle.VANCOUVER

    def test_styles_round_trip_by_value(self):
        assert CitationStyle("apa") is CitationStyle.APA


class TestCitationModel:
    def test_two_citations_of_one_document_at_different_positions_differ(self):
        # Upstream compared Citations equal by document_id alone, so a set()
        # collapsed distinct markers. Nothing ported relies on that; value
        # equality over all fields is the unsurprising contract.
        first = Citation(document_id=1, label="A", position=0, text="[@id:1:A]")
        second = Citation(document_id=1, label="A", position=10, text="[@id:1:A]")
        assert first != second

    def test_round_trip_via_to_dict(self):
        citation = Citation(document_id=7, label="Smith2023", position=4, text="[@id:7:Smith2023]")
        assert Citation.from_dict(citation.to_dict()) == citation


class TestAuthorSurname:
    def test_surname_from_an_inverted_name(self):
        assert author_surname("van der Berg, Jan") == "van der Berg"

    def test_surname_from_a_natural_name_is_the_last_word(self):
        # Upstream-faithful naive split: particles are lost in this format.
        assert author_surname("Jan van der Berg") == "Berg"

    def test_an_empty_name_is_unknown(self):
        assert author_surname("  ") == "Unknown"


class TestDocumentMetadata:
    def test_semicolon_separated_inverted_names_survive_from_dict(self):
        # Upstream replaced ';' with ',' and split on ',', shattering
        # "Smith, John; Doe, Jane" into four author fragments.
        metadata = DocumentMetadata.from_dict(
            {"id": 1, "title": "T", "authors": "Smith, John; Doe, Jane"}
        )
        assert metadata.authors == ["Smith, John", "Doe, Jane"]

    def test_a_comma_separated_author_string_still_splits(self):
        metadata = DocumentMetadata.from_dict(
            {"id": 1, "title": "T", "authors": "John Smith, Jane Doe"}
        )
        assert metadata.authors == ["John Smith", "Jane Doe"]

    def test_an_author_list_passes_through_unchanged(self):
        metadata = DocumentMetadata.from_dict(
            {"id": 1, "title": "T", "authors": ["Smith, John", "Doe, Jane"]}
        )
        assert metadata.authors == ["Smith, John", "Doe, Jane"]

    def test_document_id_falls_back_to_the_document_id_key(self):
        assert DocumentMetadata.from_dict({"document_id": 9, "title": "T"}).document_id == 9

    def test_pmid_is_coerced_to_a_string(self):
        assert DocumentMetadata.from_dict({"id": 1, "title": "T", "pmid": 123}).pmid == "123"

    def test_round_trip_via_to_dict(self):
        metadata = DocumentMetadata(
            document_id=5,
            title="A title",
            authors=["John Smith"],
            journal="J",
            year=2020,
            pmid="1",
            doi="10.1/x",
            volume="3",
            issue="2",
            pages="1-9",
            publication_date="2020-01-01",
        )
        assert DocumentMetadata.from_dict(metadata.to_dict()) == metadata

    def test_first_author_surname_and_label(self):
        metadata = DocumentMetadata(document_id=1, title="T", authors=["John Smith"], year=2023)
        assert metadata.get_first_author_surname() == "Smith"
        assert metadata.generate_label() == "Smith2023"

    def test_a_label_without_a_year_uses_nd(self):
        metadata = DocumentMetadata(document_id=1, title="T", authors=["John Smith"])
        assert metadata.generate_label() == "Smithn.d."

    def test_no_authors_reads_unknown(self):
        metadata = DocumentMetadata(document_id=1, title="T")
        assert metadata.get_first_author_surname() == "Unknown"


class TestFormattedReference:
    def test_round_trip_with_nested_metadata(self):
        reference = FormattedReference(
            number=1,
            document_id=5,
            formatted_text="1. Smith J. T.",
            metadata=DocumentMetadata(document_id=5, title="T", authors=["John Smith"]),
        )
        assert FormattedReference.from_dict(reference.to_dict()) == reference

    def test_round_trip_without_metadata(self):
        reference = FormattedReference(number=2, document_id=9, formatted_text="2. [missing]")
        assert FormattedReference.from_dict(reference.to_dict()) == reference
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_citations_parser.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bmlib.citations'` (the correct red for a new module).

- [ ] **Step 3: Write the implementation**

Create `bmlib/citations/__init__.py` (placeholder; Task 5 completes it):

```python
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

"""Citation parsing, reference formatting, and reference-list building."""
```

Create `bmlib/citations/models.py` (same header, then):

```python
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
        upstream broke by treating both separators alike.
        """
        authors = data.get("authors", [])
        if isinstance(authors, str):
            separator = ";" if ";" in authors else ","
            authors = [author.strip() for author in authors.split(separator) if author.strip()]
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
        """The first author's surname, or ``"Unknown"`` without authors."""
        if not self.authors:
            return "Unknown"
        return author_surname(self.authors[0])

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_citations_parser.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bmlib/citations/ tests/test_citations_parser.py
git commit -m "feat(citations): add the citation and reference models"
```

---

### Task 2: Parser

**Files:**
- Create: `bmlib/citations/parser.py`
- Test: `tests/test_citations_parser.py` (append parser test classes)

**Interfaces:**
- Consumes: `Citation` from Task 1.
- Produces: `CITATION_PATTERN: re.Pattern[str]`,
  `parse_citations(text) -> list[Citation]`,
  `unique_document_ids(text) -> list[int]`,
  `count_citations(text) -> int`, `count_unique_citations(text) -> int`,
  `citation_positions(text) -> dict[int, list[int]]`,
  `citations_in_range(text, start, end) -> list[Citation]`,
  `create_citation_marker(document_id, label) -> str`,
  `replace_citation_with_number(text, document_id, number) -> str`,
  `replace_all_citations_with_numbers(text, id_to_number: Mapping[int, int]) -> str`,
  `find_adjacent_citations(text) -> list[list[Citation]]`,
  `format_citation_group(citations, id_to_number, combine_sequential=True) -> str`,
  `validate_citation_marker(marker) -> tuple[bool, str | None]`,
  `extract_label_from_citation(marker) -> str | None`,
  `extract_document_id_from_citation(marker) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_citations_parser.py` (extend the import block):

```python
from bmlib.citations.parser import (
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
```

and the classes:

```python
class TestParseCitations:
    def test_markers_parse_in_order_with_positions(self):
        text = "Alpha [@id:12:Smith2023] beta [@id:7:Jones2021] gamma."
        citations = parse_citations(text)
        assert [c.document_id for c in citations] == [12, 7]
        assert citations[0].label == "Smith2023"
        assert citations[0].position == text.index("[@id:12")
        assert citations[0].text == "[@id:12:Smith2023]"

    def test_text_without_markers_yields_nothing(self):
        assert parse_citations("No citations here.") == []

    def test_malformed_markers_are_ignored(self):
        assert parse_citations("[@id:abc:NotANumber] [@id:5] [@id:5:]") == []


class TestCountingAndPositions:
    TEXT = "[@id:3:A] mid [@id:1:B] and [@id:3:A] end"

    def test_unique_ids_keep_order_of_first_appearance(self):
        assert unique_document_ids(self.TEXT) == [3, 1]

    def test_every_marker_counts(self):
        assert count_citations(self.TEXT) == 3

    def test_unique_documents_count_once(self):
        assert count_unique_citations(self.TEXT) == 2

    def test_positions_group_by_document(self):
        positions = citation_positions(self.TEXT)
        assert set(positions) == {3, 1}
        assert positions[3] == [0, self.TEXT.rindex("[@id:3")]

    def test_range_lookup_is_half_open(self):
        second_start = self.TEXT.index("[@id:1")
        found = citations_in_range(self.TEXT, 0, second_start)
        assert [c.document_id for c in found] == [3]


class TestMarkersAndReplacement:
    def test_create_citation_marker_round_trips(self):
        marker = create_citation_marker(12345, "Smith2023")
        assert marker == "[@id:12345:Smith2023]"
        [citation] = parse_citations(marker)
        assert (citation.document_id, citation.label) == (12345, "Smith2023")

    def test_replace_one_document_everywhere(self):
        text = "A [@id:5:X] B [@id:5:Y] C [@id:6:Z]"
        assert replace_citation_with_number(text, 5, 1) == "A [1] B [1] C [@id:6:Z]"

    def test_an_id_sharing_a_prefix_is_not_replaced(self):
        assert replace_citation_with_number("[@id:55:X]", 5, 1) == "[@id:55:X]"

    def test_replace_all_preserves_unmapped_markers(self):
        text = "[@id:5:X] and [@id:6:Y]"
        assert replace_all_citations_with_numbers(text, {5: 1}) == "[1] and [@id:6:Y]"


class TestAdjacentCitations:
    def test_comma_and_space_separated_markers_group(self):
        text = "Claim [@id:1:A], [@id:2:B] [@id:3:C]. Later [@id:4:D]."
        groups = find_adjacent_citations(text)
        assert [[c.document_id for c in g] for g in groups] == [[1, 2, 3], [4]]

    def test_prose_between_markers_breaks_the_group(self):
        groups = find_adjacent_citations("[@id:1:A] and [@id:2:B]")
        assert [[c.document_id for c in g] for g in groups] == [[1], [2]]

    def test_no_markers_means_no_groups(self):
        assert find_adjacent_citations("plain text") == []


def _group(*document_ids: int) -> list:
    return [
        Citation(document_id=i, label=f"L{i}", position=0, text=f"[@id:{i}:L{i}]")
        for i in document_ids
    ]


class TestFormatCitationGroup:
    def test_two_numbers_stay_listed(self):
        assert format_citation_group(_group(1, 2), {1: 1, 2: 2}) == "[1,2]"

    def test_three_sequential_numbers_combine_to_a_range(self):
        assert format_citation_group(_group(1, 2, 3), {1: 1, 2: 2, 3: 3}) == "[1-3]"

    def test_a_run_and_a_straggler(self):
        assert format_citation_group(_group(1, 2, 3, 5), {1: 1, 2: 2, 3: 3, 5: 5}) == "[1-3,5]"

    def test_a_two_run_is_listed_not_ranged(self):
        assert format_citation_group(_group(1, 2, 4), {1: 1, 2: 2, 4: 4}) == "[1,2,4]"

    def test_combining_can_be_disabled(self):
        assert (
            format_citation_group(_group(1, 2, 3), {1: 1, 2: 2, 3: 3}, combine_sequential=False)
            == "[1,2,3]"
        )

    def test_unnumbered_citations_are_skipped(self):
        assert format_citation_group(_group(1, 9), {1: 1}) == "[1]"

    def test_nothing_numbered_is_empty(self):
        assert format_citation_group(_group(9), {}) == ""


class TestMarkerValidation:
    def test_a_well_formed_marker_validates(self):
        assert validate_citation_marker("[@id:5:Smith2023]") == (True, None)

    def test_a_marker_with_trailing_text_is_not_valid(self):
        # Regression: upstream used .match(), so trailing junk validated.
        ok, reason = validate_citation_marker("[@id:5:Smith2023] trailing")
        assert not ok
        assert reason is not None

    def test_a_zero_document_id_is_rejected(self):
        ok, reason = validate_citation_marker("[@id:0:X]")
        assert not ok
        assert "positive" in reason

    def test_an_overlong_label_is_rejected(self):
        ok, _reason = validate_citation_marker(f"[@id:5:{'x' * 101}]")
        assert not ok
        assert validate_citation_marker(f"[@id:5:{'x' * 100}]") == (True, None)

    def test_extract_helpers_read_a_marker(self):
        assert extract_label_from_citation("[@id:5:Smith2023]") == "Smith2023"
        assert extract_document_id_from_citation("[@id:5:Smith2023]") == 5

    def test_extract_helpers_reject_junk_and_trailing_text(self):
        assert extract_label_from_citation("not a marker") is None
        assert extract_document_id_from_citation("[@id:5:X] tail") is None
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_citations_parser.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bmlib.citations.parser'`.

- [ ] **Step 3: Write the implementation**

Create `bmlib/citations/parser.py` (AGPL header, then):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_citations_parser.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bmlib/citations/parser.py tests/test_citations_parser.py
git commit -m "feat(citations): port the citation-marker parser as pure functions"
```

---

### Task 3: Formatters

**Files:**
- Create: `bmlib/citations/formatter.py`
- Modify: `docs/superpowers/specs/2026-08-06-citations-port-design.md` (add defect 4, found while deriving the golden strings)
- Test: `tests/test_citations_formatter.py`

**Interfaces:**
- Consumes: `CitationStyle`, `DEFAULT_CITATION_STYLE`, `DocumentMetadata`,
  `FormattedReference`, `author_surname` from Task 1.
- Produces: `MAX_AUTHORS_BEFORE_ET_AL: Final[int] = 6`, `BaseFormatter`
  (abstract `format_reference(metadata, number=None) -> str`,
  `format_inline_citation(metadata, number=None) -> str`),
  `VancouverFormatter`, `APAFormatter`, `HarvardFormatter`,
  `ChicagoFormatter`, and `CitationFormatter` facade
  (`__init__(style=DEFAULT_CITATION_STYLE)`, `style` property + setter,
  `format_reference`, `format_inline_citation`,
  `format_reference_list(references: list[FormattedReference]) -> str`,
  classmethods `get_available_styles() -> list[CitationStyle]`,
  `get_style_description(style) -> str`).

**Defect 4 (spec amendment):** upstream's APA author block appends a
terminating ``"."`` to a block that already ends with an initial's period,
so *every* APA reference with initials reads ``"Smith, J.."``; Chicago does
the same whenever the input name itself ends with a period. Fix: append the
terminal period only when the block does not already end with one. Tests:
`test_apa_references_never_double_the_period`,
`test_an_initialed_chicago_author_does_not_double_the_period`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_citations_formatter.py` (AGPL header as in Task 1, then):

```python
"""Golden-output tests for the four citation-style formatters."""

from __future__ import annotations

from dataclasses import replace

from bmlib.citations.formatter import (
    APAFormatter,
    ChicagoFormatter,
    CitationFormatter,
    HarvardFormatter,
    VancouverFormatter,
)
from bmlib.citations.models import CitationStyle, DocumentMetadata, FormattedReference

METADATA = DocumentMetadata(
    document_id=42,
    title="Title of the article",
    authors=["John Smith", "Anna Johnson", "Brian Williams"],
    journal="Journal Name",
    year=2023,
    volume="45",
    issue="2",
    pages="123-134",
    doi="10.1234/example",
)


class TestVancouver:
    def test_full_reference(self):
        assert VancouverFormatter().format_reference(METADATA, number=1) == (
            "1. Smith J, Johnson A, Williams B. Title of the article. "
            "*Journal Name*. 2023;45(2):123-134. doi:10.1234/example"
        )

    def test_inline_is_the_number(self):
        assert VancouverFormatter().format_inline_citation(METADATA, number=3) == "[3]"

    def test_inline_without_a_number_falls_back_to_the_document_id(self):
        assert VancouverFormatter().format_inline_citation(METADATA) == "[42]"

    def test_seven_authors_truncate_to_et_al(self):
        metadata = replace(METADATA, authors=[f"Given{i} Surname{i}" for i in range(7)])
        reference = VancouverFormatter().format_reference(metadata)
        assert reference.startswith(
            "Surname0 G, Surname1 G, Surname2 G, Surname3 G, Surname4 G, Surname5 G, et al."
        )
        assert "Surname6" not in reference

    def test_inverted_author_names_format_identically(self):
        metadata = replace(METADATA, authors=["Smith, John", "Johnson, Anna"])
        assert VancouverFormatter().format_reference(metadata).startswith("Smith J, Johnson A.")

    def test_pmid_is_the_doi_fallback(self):
        metadata = replace(METADATA, doi=None, pmid="12345678")
        assert VancouverFormatter().format_reference(metadata).endswith("PMID:12345678")

    def test_without_a_journal_the_journal_block_is_absent(self):
        metadata = replace(METADATA, journal=None)
        assert VancouverFormatter().format_reference(metadata, number=1) == (
            "1. Smith J, Johnson A, Williams B. Title of the article. doi:10.1234/example"
        )

    def test_no_authors_reads_unknown(self):
        metadata = replace(METADATA, authors=[])
        assert VancouverFormatter().format_reference(metadata).startswith("Unknown author.")


class TestAPA:
    def test_full_reference(self):
        assert APAFormatter().format_reference(METADATA) == (
            "Smith, J., Johnson, A., & Williams, B. (2023) Title of the article. "
            "*Journal Name*, *45*(2), 123-134. https://doi.org/10.1234/example"
        )

    def test_apa_references_never_double_the_period(self):
        # Regression: upstream appended "." to an author block already ending
        # with an initial's period, so every such reference read "…, J..".
        for authors in (["John Smith"], ["John Smith", "Anna Johnson"]):
            reference = APAFormatter().format_reference(replace(METADATA, authors=authors))
            assert ".." not in reference

    def test_eight_authors_elide_the_middle(self):
        metadata = replace(METADATA, authors=[f"Given{i} Surname{i}" for i in range(8)])
        reference = APAFormatter().format_reference(metadata)
        assert "Surname6" not in reference
        assert "..., & Surname7, G." in reference

    def test_inline_shapes(self):
        formatter = APAFormatter()
        assert formatter.format_inline_citation(METADATA) == "(Smith et al., 2023)"
        two = replace(METADATA, authors=["John Smith", "Anna Johnson"])
        assert formatter.format_inline_citation(two) == "(Smith & Johnson, 2023)"
        one = replace(METADATA, authors=["John Smith"])
        assert formatter.format_inline_citation(one) == "(Smith, 2023)"

    def test_a_missing_year_is_nd(self):
        metadata = replace(METADATA, year=None, authors=["John Smith"])
        assert "(n.d.)" in APAFormatter().format_reference(metadata)
        assert APAFormatter().format_inline_citation(metadata) == "(Smith, n.d.)"


class TestHarvard:
    def test_full_reference(self):
        assert HarvardFormatter().format_reference(METADATA) == (
            "Smith, J., Johnson, A. and Williams, B. (2023) 'Title of the article', "
            "*Journal Name*, 45(2), pp. 123-134. doi: 10.1234/example."
        )

    def test_inline_two_authors_use_and(self):
        two = replace(METADATA, authors=["John Smith", "Anna Johnson"])
        assert HarvardFormatter().format_inline_citation(two) == "(Smith and Johnson, 2023)"

    def test_seven_authors_truncate_to_et_al(self):
        metadata = replace(METADATA, authors=[f"Given{i} Surname{i}" for i in range(7)])
        reference = HarvardFormatter().format_reference(metadata)
        assert " and et al. (2023)" in reference
        assert "Surname6" not in reference


class TestChicago:
    def test_full_reference(self):
        assert ChicagoFormatter().format_reference(METADATA) == (
            'Smith, John, Anna Johnson, and Brian Williams. 2023. "Title of the article." '
            "*Journal Name* 45 (2): 123-134. https://doi.org/10.1234/example."
        )

    def test_only_the_first_author_is_inverted(self):
        two = replace(METADATA, authors=["Smith, John", "Johnson, Anna"])
        assert ChicagoFormatter().format_reference(two).startswith(
            "Smith, John, and Anna Johnson."
        )

    def test_an_initialed_chicago_author_does_not_double_the_period(self):
        one = replace(METADATA, authors=["Smith, John A."])
        assert ".." not in ChicagoFormatter().format_reference(one).split("2023")[0]

    def test_inline_shapes(self):
        formatter = ChicagoFormatter()
        assert formatter.format_inline_citation(METADATA) == "(Smith et al. 2023)"
        two = replace(METADATA, authors=["John Smith", "Anna Johnson"])
        assert formatter.format_inline_citation(two) == "(Smith and Johnson 2023)"
        one = replace(METADATA, authors=["John Smith"])
        assert formatter.format_inline_citation(one) == "(Smith 2023)"


class TestCitationFormatterFacade:
    def test_the_default_style_is_vancouver(self):
        assert CitationFormatter().style is CitationStyle.VANCOUVER

    def test_the_style_is_switchable(self):
        formatter = CitationFormatter(CitationStyle.VANCOUVER)
        assert formatter.format_inline_citation(METADATA, number=1) == "[1]"
        formatter.style = CitationStyle.APA
        assert formatter.format_inline_citation(METADATA) == "(Smith et al., 2023)"

    def test_reference_list_markdown(self):
        references = [
            FormattedReference(number=1, document_id=5, formatted_text="1. X."),
            FormattedReference(number=2, document_id=6, formatted_text="2. Y."),
        ]
        assert CitationFormatter().format_reference_list(references) == (
            "\n---\n\n## References\n\n1. X.\n\n2. Y.\n"
        )

    def test_every_style_is_available_and_described(self):
        styles = CitationFormatter.get_available_styles()
        assert styles == [
            CitationStyle.VANCOUVER,
            CitationStyle.APA,
            CitationStyle.HARVARD,
            CitationStyle.CHICAGO,
        ]
        for style in styles:
            assert CitationFormatter.get_style_description(style)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_citations_formatter.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bmlib.citations.formatter'`.

- [ ] **Step 3: Write the implementation**

Create `bmlib/citations/formatter.py` (AGPL header, then):

```python
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
    def format_inline_citation(
        self, metadata: DocumentMetadata, number: int | None = None
    ) -> str:
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

    def format_inline_citation(
        self, metadata: DocumentMetadata, number: int | None = None
    ) -> str:
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

    def format_inline_citation(
        self, metadata: DocumentMetadata, number: int | None = None
    ) -> str:
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

    def format_inline_citation(
        self, metadata: DocumentMetadata, number: int | None = None
    ) -> str:
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

    def format_inline_citation(
        self, metadata: DocumentMetadata, number: int | None = None
    ) -> str:
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
            formatted.append(
                self._inverted(author) if i == 0 else self._natural(author)
            )
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

    def format_inline_citation(
        self, metadata: DocumentMetadata, number: int | None = None
    ) -> str:
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
```

Note the one intentional structural deviation inside `APAFormatter
._format_authors`: upstream's loop guard was `if i >= MAX + 1: break`
followed by the `i == MAX` ellipsis branch — the `>=` guard is unreachable
(the `==` branch always breaks first), so the port uses `if i > MAX` purely
for symmetry; behaviour is identical for every input length.

- [ ] **Step 2: Amend the spec with defect 4**

In `docs/superpowers/specs/2026-08-06-citations-port-design.md`, append to
the "Upstream defects fixed" list:

```markdown
4. **APA/Chicago author blocks double the terminal period.** Upstream
   appends `"."` to an author block that already ends with an initial's
   period, so every APA reference with initials reads `"…Williams, B.."`,
   and a Chicago first author like `"Smith, John A."` doubles the same way.
   Found while deriving the golden test strings. Fix: append the terminal
   period only when the block does not already end with one.
   Tests: `test_apa_references_never_double_the_period`,
   `test_an_initialed_chicago_author_does_not_double_the_period`.
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_citations_formatter.py tests/test_citations_parser.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add bmlib/citations/formatter.py tests/test_citations_formatter.py docs/superpowers/specs/2026-08-06-citations-port-design.md
git commit -m "feat(citations): port the four citation-style formatters"
```

---

### Task 4: Builder

**Files:**
- Create: `bmlib/citations/builder.py`
- Test: `tests/test_citations_builder.py`

**Interfaces:**
- Consumes: parser functions (Task 2), `CitationFormatter` (Task 3), models
  (Task 1).
- Produces:
  `build_references(text, metadata: Mapping[int, DocumentMetadata], style=DEFAULT_CITATION_STYLE, combine_sequential=True) -> tuple[str, list[FormattedReference]]`,
  `format_document(text, metadata, style=DEFAULT_CITATION_STYLE, include_reference_list=True, combine_sequential=True) -> str`,
  `find_missing_documents(text, metadata) -> list[Citation]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_citations_builder.py` (AGPL header as in Task 1, then):

```python
"""End-to-end tests for the reference builder."""

from __future__ import annotations

from bmlib.citations.builder import build_references, find_missing_documents, format_document
from bmlib.citations.models import CitationStyle, DocumentMetadata


def _metadata_map() -> dict[int, DocumentMetadata]:
    return {
        1: DocumentMetadata(
            document_id=1,
            title="First paper",
            authors=["John Smith"],
            journal="J One",
            year=2021,
            doi="10.1/one",
        ),
        2: DocumentMetadata(
            document_id=2,
            title="Second paper",
            authors=["Jane Doe", "Bob Roe"],
            journal="J Two",
            year=2022,
        ),
        3: DocumentMetadata(
            document_id=3,
            title="Third paper",
            authors=["Ann Poe"],
            journal="J Three",
            year=2023,
        ),
    }


class TestBuildReferencesVancouver:
    def test_numbers_follow_first_appearance(self):
        text = "B [@id:2:Doe2022] then A [@id:1:Smith2021] then B again [@id:2:Doe2022]."
        formatted, references = build_references(text, _metadata_map())
        assert formatted == "B [1] then A [2] then B again [1]."
        assert [(r.number, r.document_id) for r in references] == [(1, 2), (2, 1)]

    def test_references_are_formatted_in_style(self):
        _, references = build_references("See [@id:1:Smith2021].", _metadata_map())
        assert references[0].formatted_text == (
            "1. Smith J. First paper. *J One*. 2021. doi:10.1/one"
        )
        assert references[0].metadata == _metadata_map()[1]

    def test_adjacent_markers_combine_to_a_range(self):
        text = "Claim [@id:1:A] [@id:2:B] [@id:3:C]."
        formatted, _ = build_references(text, _metadata_map())
        assert formatted == "Claim [1-3]."

    def test_combining_can_be_disabled(self):
        text = "Claim [@id:1:A] [@id:2:B] [@id:3:C]."
        formatted, _ = build_references(text, _metadata_map(), combine_sequential=False)
        assert formatted == "Claim [1,2,3]."

    def test_a_missing_document_gets_a_visible_placeholder(self):
        text = "Known [@id:1:A] unknown [@id:99:Z]."
        formatted, references = build_references(text, _metadata_map())
        assert formatted == "Known [1] unknown [2]."
        assert references[1].formatted_text == "2. [Document 99 not found]"
        assert references[1].metadata is None

    def test_no_citations_returns_the_text_unchanged(self):
        assert build_references("plain prose", _metadata_map()) == ("plain prose", [])


class TestAuthorDateStyles:
    def test_author_date_styles_get_author_date_inline_citations(self):
        # Regression: upstream replaced markers with [N] in every style, so
        # an APA document read "[3]" against an unnumbered reference list.
        text = "As shown [@id:2:Doe2022]."
        formatted, _ = build_references(text, _metadata_map(), style=CitationStyle.APA)
        assert formatted == "As shown (Doe & Roe, 2022)."

    def test_harvard_and_chicago_inline_shapes(self):
        text = "As shown [@id:2:Doe2022]."
        harvard, _ = build_references(text, _metadata_map(), style=CitationStyle.HARVARD)
        assert harvard == "As shown (Doe and Roe, 2022)."
        chicago, _ = build_references(text, _metadata_map(), style=CitationStyle.CHICAGO)
        assert chicago == "As shown (Doe and Roe 2022)."

    def test_a_marker_without_metadata_stays_verbatim(self):
        text = "Mystery [@id:99:Z]."
        formatted, references = build_references(
            text, _metadata_map(), style=CitationStyle.APA
        )
        assert formatted == text
        assert references[0].formatted_text == "1. [Document 99 not found]"


class TestFormatDocument:
    def test_appends_the_reference_list(self):
        out = format_document("See [@id:1:A].", _metadata_map())
        assert out.startswith("See [1].")
        assert "## References" in out
        assert "1. Smith J. First paper. *J One*. 2021. doi:10.1/one" in out

    def test_the_reference_list_can_be_suppressed(self):
        out = format_document("See [@id:1:A].", _metadata_map(), include_reference_list=False)
        assert out == "See [1]."

    def test_a_document_with_no_citations_gains_no_reference_list(self):
        assert format_document("plain prose", _metadata_map()) == "plain prose"


class TestFindMissingDocuments:
    def test_missing_ids_are_reported_per_marker(self):
        text = "[@id:1:A] [@id:99:Z] and again [@id:99:Z]"
        missing = find_missing_documents(text, _metadata_map())
        assert [c.document_id for c in missing] == [99, 99]

    def test_nothing_missing_is_empty(self):
        assert find_missing_documents("[@id:1:A]", _metadata_map()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_citations_builder.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bmlib.citations.builder'`.

- [ ] **Step 3: Write the implementation**

Create `bmlib/citations/builder.py` (AGPL header, then):

```python
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


def find_missing_documents(
    text: str, metadata: Mapping[int, DocumentMetadata]
) -> list[Citation]:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_citations_builder.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bmlib/citations/builder.py tests/test_citations_builder.py
git commit -m "feat(citations): port the reference builder with injected metadata"
```

---

### Task 5: Package exports, documentation, and full verification

**Files:**
- Modify: `bmlib/citations/__init__.py`
- Create: `docs/manual/citations.md`
- Modify: `CHANGELOG.md` (`[Unreleased]` section), `CLAUDE.md` (directory
  tree, module descriptions, test mapping), `ROADMAP.md`
- Test: `tests/test_citations_builder.py` (package-export smoke test)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `bmlib.citations` public API via `__all__`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_citations_builder.py`:

```python
class TestPackageExports:
    def test_the_public_names_import_from_the_package(self):
        from bmlib.citations import (  # noqa: F401
            CITATION_PATTERN,
            DEFAULT_CITATION_STYLE,
            Citation,
            CitationFormatter,
            CitationStyle,
            DocumentMetadata,
            FormattedReference,
            build_references,
            create_citation_marker,
            find_missing_documents,
            format_document,
            parse_citations,
        )
```

Run: `uv run pytest tests/test_citations_builder.py::TestPackageExports -q`
Expected: FAIL — `ImportError` (the package `__init__` exports nothing yet).

- [ ] **Step 2: Complete the package `__init__.py`**

Replace the docstring-only body of `bmlib/citations/__init__.py` (keep the
AGPL header) with:

```python
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
```

Run: `uv run pytest tests/test_citations_builder.py -q` — all pass.

- [ ] **Step 3: Write the manual page**

Create `docs/manual/citations.md` covering, in this order (mirror the tone
and structure of `docs/manual/fulltext.md`):

1. **Overview** — what the marker format is, the four styles, that the
   package is pure stdlib with metadata injected by the caller.
2. **Quick start** — the `build_references()` / `format_document()` example
   from the package docstring, expanded to show the output text and one
   Vancouver reference.
3. **The marker format** — `[@id:12345:Smith2023]`, `create_citation_marker()`,
   `validate_citation_marker()` (whole-string), `DocumentMetadata.generate_label()`.
4. **Parsing utilities** — the counting/position/grouping functions in a
   short table.
5. **Citation styles** — one formatted example reference per style
   (copy the golden strings from `tests/test_citations_formatter.py`), the
   `CitationFormatter` facade, `MAX_AUTHORS_BEFORE_ET_AL`.
6. **Building reference lists** — numbering by first appearance, adjacent
   combining, the missing-document placeholder, author–date inline
   citations, `find_missing_documents()`.
7. **Differences from bmlibrarian** — the four fixed defects and the
   dropped app-editor pieces, one line each, pointing at the design doc.

Every code example must be executable as written (imports included).

- [ ] **Step 4: Update CHANGELOG.md**

Under `## [Unreleased]` add (merge into the existing `### Added` /
`### Fixed` subsections if present, creating them if not):

```markdown
### Added

- `bmlib.citations` — citation-marker parsing, four citation styles, and
  reference-list building, ported from bmlibrarian's `writing` package
  (Phase 2 row 4 of the porting analysis). `parse_citations()` and friends
  read the `[@id:12345:Smith2023]` marker format as pure functions;
  `CitationFormatter` renders references and inline citations in Vancouver,
  APA, Harvard, or Chicago style; `build_references()` /
  `format_document()` number citations by order of first appearance,
  combine adjacent markers (`[1-3]`), and append a markdown reference list,
  with document metadata injected by the caller instead of fetched from a
  database. Four upstream defects fixed, each with a named regression test:
  a semicolon-separated author string of inverted names was shattered into
  fragments (`"Smith, John; Doe, Jane"` became four authors); marker
  validation anchored only the start, so trailing junk validated; author–
  date styles (APA/Harvard/Chicago) received numeric `[N]` inline citations
  against an unnumbered reference list; and APA/Chicago author blocks
  doubled the terminal period (`"Williams, B.."`).
```

- [ ] **Step 5: Update CLAUDE.md**

Three edits:

1. Directory-structure block — insert after the `agents/` entry
   (alphabetical order):

```
├── citations/               # Citation markers, styles, and reference lists (pure stdlib)
│   ├── models.py            # CitationStyle, Citation, DocumentMetadata, FormattedReference
│   ├── parser.py            # [@id:N:Label] marker parsing/replacement as pure functions
│   ├── formatter.py         # Vancouver/APA/Harvard/Chicago + CitationFormatter facade
│   └── builder.py           # build_references, format_document, find_missing_documents
```

2. Module descriptions — add after the `agents/` bullet:

```markdown
- **`citations/`** — Citation-marker parsing and reference building, pure
  stdlib. Text carries `[@id:12345:Smith2023]` markers; `build_references()`
  numbers the cited documents by order of first appearance, formats
  references in Vancouver, APA, Harvard, or Chicago style, replaces markers
  with `[N]` (Vancouver, adjacent runs combined to `[1-3]`) or the style's
  author–date inline citation, and reports a missing document as a visible
  placeholder rather than dropping it. Metadata is injected as
  `Mapping[int, DocumentMetadata]` — the upstream DB fetch was severed in
  the port.
```

3. Test-file mapping table — add the row:

```markdown
| `citations/`         | `test_citations_parser.py`, `test_citations_formatter.py`, `test_citations_builder.py` |
```

- [ ] **Step 6: Update ROADMAP.md**

Insert a new section between the Templates and Quality sections:

```markdown
| **Citations (`bmlib.citations`)** | | |
| ✅ Done | Citation/reference stack | Phase 2 row 4 of the bmlibrarian port. `[@id:N:Label]` marker parsing as pure functions, Vancouver/APA/Harvard/Chicago formatters, and a reference builder that numbers by first appearance, combines adjacent markers, and takes caller-injected metadata (the upstream DB fetch severed). Four upstream defects fixed with named regression tests: shattered semicolon-separated inverted author names, start-anchored marker validation, numeric inline citations in author–date styles, and doubled terminal periods in APA/Chicago author blocks (unreleased) |
```

- [ ] **Step 7: Full verification**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check .
uvx ruff@0.15.20 format --check .
```

Expected: full suite passes (baseline 1513 + the new citation tests), both
ruff commands clean. Fix any findings before committing.

- [ ] **Step 8: Commit**

```bash
git add bmlib/citations/__init__.py docs/manual/citations.md CHANGELOG.md CLAUDE.md ROADMAP.md tests/test_citations_builder.py
git commit -m "docs(citations): export the public API and document the package"
```

---

## Self-review notes

- Spec coverage: models/parser/formatter/builder tasks map 1:1 onto the
  spec's module designs; all four defects have named tests (defect 4 added
  to the spec in Task 3); the spec's documentation section is Task 5.
- Golden strings in Task 3 were derived by executing upstream's logic by
  hand; if a golden test fails against the ported code, first re-check the
  expected string against upstream (`~/src/bmlibrarian/src/bmlibrarian/
  writing/citation_formatter.py`) before touching the implementation —
  the test is the spec only when it matches upstream (plus the four fixes).
- Type consistency: `Mapping[int, int]` for `id_to_number` and
  `Mapping[int, DocumentMetadata]` for metadata throughout; `Citation`
  everywhere position-bearing.
```
