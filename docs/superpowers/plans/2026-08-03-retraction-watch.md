# Retraction Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give bmlib a first-class answer to "is this paper retracted?", by parsing the Crossref-distributed Retraction Watch CSV and storing its notices in a dual-backend table with a lookup and a pure retraction rule.

**Architecture:** A new `bmlib/publications/retractions.py` holds a streaming CSV parser, dual-backend storage/lookup, and the pure `is_retracted()` rule. The `RetractionNature` enum and `RetractionNotice` dataclass live in `publications/models.py` with every other public dataclass; the `retraction_notices` DDL goes into `publications/schema.py` so existing `ensure_schema()` callers get the table with no new call. This is deliberately **not** a fetcher — see the design doc's "Why this is not a fetcher".

**Tech Stack:** Python ≥3.11, stdlib only (`csv`, `codecs`, `io`, `json`, `datetime`, `logging`). No new dependency, no new optional extra. Tests: pytest, in-memory SQLite, plus PostgreSQL parity via `tests/test_backends.py`.

**Design doc:** [`docs/superpowers/specs/2026-08-02-retraction-watch-design.md`](../specs/2026-08-02-retraction-watch-design.md) — read it before starting. Every "why" below is argued there.

## Global Constraints

- **AGPL-3 header** at the top of every new source file, copied verbatim from an existing file (e.g. `bmlib/publications/storage.py` lines 1–15).
- **`from __future__ import annotations`** at the top of every module.
- **Type hints required** on every function signature; **docstrings required** on every public function, class and module (Google style, as the rest of `publications/` uses).
- **ruff**: line-length 100, target py311, rules E, F, I, N, W, UP. Lint with the **CI-pinned** version, not `.venv`'s: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- **`uv` only, never bare pip.** Tests: `uv run pytest tests/ -v`.
- **Lowercase builtin generics** (`list[str]`, `dict[str, Any]`, `X | None`) — never `List`/`Optional`.
- **No ORM.** Explicit SQL through `bmlib.db` helpers; placeholders from `placeholder(conn)`, never a hard-coded `?` or `%s`.
- **Writes run inside `transaction(conn)`** so a standalone call commits once and a nested call joins the caller's block.
- **Dataclasses get `to_dict()` / `from_dict()`**; mutable defaults use `field(default_factory=...)`.
- **Every new public name** is exported from `bmlib/publications/__init__.py` and listed in its `__all__`.
- **Do not commit the 65 MB export.** All fixtures are small hand-built CSVs.

---

## File Structure

| File | Responsibility |
|---|---|
| `bmlib/publications/models.py` (modify) | `RetractionNature` enum + `RetractionNotice` dataclass, beside every other public dataclass. |
| `bmlib/publications/schema.py` (modify) | `retraction_notices` DDL in both dialect strings, so `ensure_schema()` creates it. |
| `bmlib/publications/retractions.py` (create) | Everything retraction-specific: CSV parsing, storage, lookup, the rule. ~330 lines. |
| `bmlib/publications/__init__.py` (modify) | Re-export the six public names. |
| `tests/test_retractions.py` (create) | Parser, model and rule tests; SQLite-only storage tests. |
| `tests/test_backends.py` (modify) | Storage/lookup parity on SQLite **and** PostgreSQL. |
| `docs/manual/publications.md` (modify) | Retractions section. |
| `CHANGELOG.md`, `ROADMAP.md`, `CLAUDE.md` (modify) | Record the feature. |

`retractions.py` holds parsing *and* storage rather than splitting storage into `storage.py`, because `storage.py` is already 428 lines of publication dedup/merge logic and the retraction concern is cohesive and self-contained. Files that change together live together.

---

### Task 1: The model — `RetractionNature` and `RetractionNotice`

**Files:**
- Modify: `bmlib/publications/models.py` (append after `SourceDescriptor`, at the end of the file)
- Test: `tests/test_retractions.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RetractionNature(str, Enum)` with members `RETRACTION`, `CORRECTION`, `EXPRESSION_OF_CONCERN`, `REINSTATEMENT`, `OTHER` (values `"retraction"`, `"correction"`, `"expression_of_concern"`, `"reinstatement"`, `"other"`), and classmethod `from_raw(value: str | None) -> RetractionNature`.
  - `RetractionNotice` dataclass with fields, in order: `record_id: str`, `nature: RetractionNature`, `doi: str | None = None`, `pmid: str | None = None`, `notice_doi: str | None = None`, `notice_pmid: str | None = None`, `title: str | None = None`, `journal: str | None = None`, `retraction_date: str | None = None`, `original_paper_date: str | None = None`, `reasons: list[str] = field(default_factory=list)`, `raw_nature: str | None = None`. Methods `to_dict() -> dict[str, Any]` and classmethod `from_dict(data: dict[str, Any]) -> RetractionNotice`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retractions.py` with the AGPL header (copy lines 1–15 from `tests/test_publications.py`), then:

```python
"""Tests for Retraction Watch parsing, storage and the retraction rule."""

from __future__ import annotations

import pytest

from bmlib.publications.models import RetractionNature, RetractionNotice


class TestRetractionNature:
    def test_the_four_known_natures_map_from_the_export(self):
        assert RetractionNature.from_raw("Retraction") is RetractionNature.RETRACTION
        assert RetractionNature.from_raw("Correction") is RetractionNature.CORRECTION
        assert RetractionNature.from_raw("Reinstatement") is RetractionNature.REINSTATEMENT

    def test_expression_of_concern_is_matched_case_insensitively(self):
        # The live export writes "Expression of concern" -- lower-case "c".
        assert (
            RetractionNature.from_raw("Expression of concern")
            is RetractionNature.EXPRESSION_OF_CONCERN
        )
        assert (
            RetractionNature.from_raw("  EXPRESSION OF CONCERN  ")
            is RetractionNature.EXPRESSION_OF_CONCERN
        )

    def test_an_unknown_nature_is_preserved_rather_than_rejected(self):
        # The vocabulary is Retraction Watch's and can grow. A new notice type
        # must cost one row of fidelity, not a failed 71,000-row import.
        assert RetractionNature.from_raw("Partial Retraction") is RetractionNature.OTHER
        assert RetractionNature.from_raw("") is RetractionNature.OTHER
        assert RetractionNature.from_raw(None) is RetractionNature.OTHER


class TestRetractionNoticeModel:
    def test_round_trips_through_a_dict(self):
        notice = RetractionNotice(
            record_id="71974",
            nature=RetractionNature.RETRACTION,
            doi="10.1007/s00500-023-08327-1",
            pmid="12345678",
            notice_doi="10.1007/s00500-023-99999-9",
            notice_pmid="87654321",
            title="A paper",
            journal="Soft Computing",
            retraction_date="2026-03-09",
            original_paper_date="2023-05-06",
            reasons=["Rogue Editor", "Unreliable Results and/or Conclusions"],
            raw_nature="Retraction",
        )

        restored = RetractionNotice.from_dict(notice.to_dict())

        assert restored == notice

    def test_the_serialised_nature_is_the_enum_value_not_the_file_wording(self):
        notice = RetractionNotice(
            record_id="1",
            nature=RetractionNature.EXPRESSION_OF_CONCERN,
            doi="10.1/x",
            raw_nature="Expression of concern",
        )

        data = notice.to_dict()

        assert data["nature"] == "expression_of_concern"
        assert data["raw_nature"] == "Expression of concern"
        assert RetractionNotice.from_dict(data).nature is RetractionNature.EXPRESSION_OF_CONCERN

    def test_reasons_default_to_an_independent_list(self):
        first = RetractionNotice(record_id="1", nature=RetractionNature.RETRACTION)
        second = RetractionNotice(record_id="2", nature=RetractionNature.RETRACTION)

        first.reasons.append("Falsification of Data")

        assert second.reasons == []

    def test_record_id_is_required(self):
        with pytest.raises(TypeError):
            RetractionNotice(nature=RetractionNature.RETRACTION)  # type: ignore[call-arg]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_retractions.py -v`
Expected: FAIL — `ImportError: cannot import name 'RetractionNature' from 'bmlib.publications.models'`

- [ ] **Step 3: Add the enum and dataclass**

In `bmlib/publications/models.py`, add `from enum import Enum` to the imports (keep the import block ruff-sorted: `from dataclasses import ...`, `from datetime import ...`, `from enum import Enum`, `from typing import ...`). Then append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Retraction notices
# ---------------------------------------------------------------------------


class RetractionNature(str, Enum):
    """The kind of notice a Retraction Watch row records.

    ``OTHER`` is forward-compatibility, not a case the current export
    exercises: every one of the 71,306 real rows in the 2026-08-03 Crossref
    export carries one of the four named values. The vocabulary belongs to
    Retraction Watch, so a value this enum does not know must cost one row of
    fidelity rather than abort the import — the raw string is kept in
    :attr:`RetractionNotice.raw_nature`.
    """

    RETRACTION = "retraction"
    CORRECTION = "correction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    REINSTATEMENT = "reinstatement"
    OTHER = "other"

    @classmethod
    def from_raw(cls, value: str | None) -> RetractionNature:
        """Map an export's ``RetractionNature`` cell onto this enum.

        Matching is case-insensitive on a stripped value: the export writes
        ``"Expression of concern"`` with a lower-case ``c``. An unrecognised
        or empty value maps to :attr:`OTHER`.
        """
        return _NATURE_BY_RAW.get((value or "").strip().lower(), cls.OTHER)


# Keyed by the *file's* wording (spaces, any case), which is a different
# vocabulary from the enum's own values (underscores). ``from_dict`` reads the
# latter, ``from_raw`` the former; conflating them silently maps every
# expression of concern to OTHER.
_NATURE_BY_RAW: dict[str, RetractionNature] = {
    "retraction": RetractionNature.RETRACTION,
    "correction": RetractionNature.CORRECTION,
    "expression of concern": RetractionNature.EXPRESSION_OF_CONCERN,
    "reinstatement": RetractionNature.REINSTATEMENT,
}


@dataclass
class RetractionNotice:
    """One Retraction Watch notice about one paper.

    A row of the export describes **two** papers, so both identifier pairs are
    carried under names that say which is which: :attr:`doi`/:attr:`pmid` are
    always the **retracted paper** (the export's ``OriginalPaper*`` columns),
    and :attr:`notice_doi`/:attr:`notice_pmid` are the retraction notice
    itself (its ``Retraction*`` columns). They are sometimes equal.

    Dates are ISO ``yyyy-mm-dd`` strings, matching
    :attr:`Publication.publication_date`.
    """

    record_id: str
    nature: RetractionNature

    doi: str | None = None
    pmid: str | None = None
    notice_doi: str | None = None
    notice_pmid: str | None = None
    title: str | None = None
    journal: str | None = None
    retraction_date: str | None = None
    original_paper_date: str | None = None
    reasons: list[str] = field(default_factory=list)
    raw_nature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "record_id": self.record_id,
            "nature": self.nature.value,
            "doi": self.doi,
            "pmid": self.pmid,
            "notice_doi": self.notice_doi,
            "notice_pmid": self.notice_pmid,
            "title": self.title,
            "journal": self.journal,
            "retraction_date": self.retraction_date,
            "original_paper_date": self.original_paper_date,
            "reasons": list(self.reasons),
            "raw_nature": self.raw_nature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetractionNotice:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            record_id=data["record_id"],
            nature=RetractionNature(data["nature"]),
            doi=data.get("doi"),
            pmid=data.get("pmid"),
            notice_doi=data.get("notice_doi"),
            notice_pmid=data.get("notice_pmid"),
            title=data.get("title"),
            journal=data.get("journal"),
            retraction_date=data.get("retraction_date"),
            original_paper_date=data.get("original_paper_date"),
            reasons=list(data.get("reasons", [])),
            raw_nature=data.get("raw_nature"),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_retractions.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/publications/models.py tests/test_retractions.py
git commit -m "feat(publications): add RetractionNotice and RetractionNature models"
```

---

### Task 2: Field-level parsing helpers

**Files:**
- Create: `bmlib/publications/retractions.py`
- Test: `tests/test_retractions.py` (append)

**Interfaces:**
- Consumes: `RetractionNature`, `RetractionNotice` from Task 1.
- Produces (module-private, used by Task 3): `_find_column(row: Mapping[str, str | None], candidates: tuple[str, ...]) -> str | None`, `_clean_identifier(value: str | None) -> str | None`, `_split_reasons(value: str | None) -> list[str]`, `_parse_date(value: str | None) -> str | None`, and the `_*_COLUMNS` candidate tuples.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retractions.py`:

```python
from bmlib.publications.retractions import (
    _clean_identifier,
    _find_column,
    _parse_date,
    _split_reasons,
)


class TestColumnResolution:
    def test_the_pmid_column_of_the_real_export_is_found(self):
        # Upstream's candidate tuple contained none of the export's real PMID
        # column names, so its PMID branch never fired on a real file.
        from bmlib.publications.retractions import _PMID_COLUMNS

        row = {"OriginalPaperPubMedID": "12345678", "RetractionPubMedID": "87654321"}

        assert _find_column(row, _PMID_COLUMNS) == "12345678"

    def test_the_retracted_paper_is_preferred_to_the_notice(self):
        from bmlib.publications.retractions import _DOI_COLUMNS, _NOTICE_DOI_COLUMNS

        row = {"RetractionDOI": "10.1/notice", "OriginalPaperDOI": "10.1/paper"}

        assert _find_column(row, _DOI_COLUMNS) == "10.1/paper"
        assert _find_column(row, _NOTICE_DOI_COLUMNS) == "10.1/notice"

    def test_a_blank_cell_falls_through_to_the_next_candidate(self):
        row = {"A": "   ", "B": "value"}

        assert _find_column(row, ("A", "B")) == "value"

    def test_no_candidate_present_returns_none(self):
        assert _find_column({"X": "y"}, ("A", "B")) is None


class TestIdentifierSentinels:
    def test_a_zero_pubmed_id_is_not_an_identifier(self):
        # 46.04% of rows in the live export write "0" for an absent PMID.
        # It is a non-empty string, so a truthiness test accepts it and every
        # one of those rows collapses onto a single fake key.
        assert _clean_identifier("0") is None

    def test_an_unavailable_doi_is_not_an_identifier_in_either_casing(self):
        # The same file carries both casings: "Unavailable" 2,235, and
        # "unavailable" 1,184. A case-sensitive check leaks 1,184 rows.
        assert _clean_identifier("Unavailable") is None
        assert _clean_identifier("unavailable") is None

    def test_blank_and_missing_values_are_not_identifiers(self):
        assert _clean_identifier("") is None
        assert _clean_identifier("   ") is None
        assert _clean_identifier(None) is None

    def test_a_real_identifier_survives_and_is_stripped(self):
        assert _clean_identifier("  10.1/abc  ") == "10.1/abc"
        assert _clean_identifier("12345678") == "12345678"

    def test_a_zero_inside_a_real_identifier_is_untouched(self):
        assert _clean_identifier("10.1016/j.0000") == "10.1016/j.0000"
        assert _clean_identifier("101") == "101"


class TestReasonSplitting:
    def test_the_trailing_semicolon_does_not_become_an_empty_reason(self):
        # Every populated row in the live export ends its Reason cell with
        # ";", so a naive split always yields an empty final item.
        value = "Concerns/Issues about Peer Review;Rogue Editor;"

        assert _split_reasons(value) == ["Concerns/Issues about Peer Review", "Rogue Editor"]

    def test_a_leading_plus_is_stripped_for_the_other_export_variant(self):
        # The Crossref export carries no "+" prefix (0 rows of 71,306); the
        # Retraction Watch native export does.
        assert _split_reasons("+Falsification of Data;+Rogue Editor;") == [
            "Falsification of Data",
            "Rogue Editor",
        ]

    def test_a_blank_cell_yields_no_reasons(self):
        assert _split_reasons("") == []
        assert _split_reasons(None) == []
        assert _split_reasons(";;;") == []


class TestDateParsing:
    def test_the_export_format_with_a_trailing_time_is_parsed(self):
        # The live export writes M/D/YYYY H:MM on 100% of dated rows.
        assert _parse_date("3/9/2026 0:00") == "2026-03-09"
        assert _parse_date("12/25/2021 0:00") == "2021-12-25"

    def test_an_iso_date_is_parsed(self):
        assert _parse_date("2026-03-09") == "2026-03-09"

    def test_a_day_above_twelve_disambiguates_to_month_first(self):
        assert _parse_date("5/31/2024 0:00") == "2024-05-31"

    def test_an_unparseable_date_becomes_none_rather_than_failing(self):
        assert _parse_date("not a date") is None
        assert _parse_date("") is None
        assert _parse_date(None) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_retractions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bmlib.publications.retractions'`

- [ ] **Step 3: Create the module with the helpers**

Create `bmlib/publications/retractions.py`. Start with the AGPL header (copy lines 1–15 from `bmlib/publications/storage.py`), then:

```python
"""Retraction Watch notices — parsing, storage and the retraction rule.

The Retraction Watch database is distributed by Crossref as a CSV under CC0
(``https://api.labs.crossref.org/data/retractionwatch?<mailto>``). This module
turns that file into :class:`~bmlib.publications.models.RetractionNotice`
records, stores them on either backend, and answers "is this paper retracted?".

It is deliberately **not** a registered source fetcher: fetchers are a
date-keyed feed protocol producing publications, while a retraction notice
annotates a paper that is usually not in the caller's ``publications`` table
at all. See ``docs/superpowers/specs/2026-08-02-retraction-watch-design.md``.
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from bmlib.db import execute, fetch_all, placeholder, transaction
from bmlib.publications.models import RetractionNature, RetractionNotice
from bmlib.publications.storage import _normalize_doi, _normalize_pmid, _now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------

# A Retraction Watch row describes two papers, and the export carries a column
# pair for each. These tuples are ordered most-specific-first and deliberately
# exclude a bare ``"DOI"`` / ``"PMID"``: such a column could mean either paper,
# and guessing is what let upstream's resolution return the notice's
# identifier for the retracted paper.
_RECORD_ID_COLUMNS = ("Record ID", "RecordID", "Record Id")
_DOI_COLUMNS = ("OriginalPaperDOI", "Original Paper DOI")
_PMID_COLUMNS = ("OriginalPaperPubMedID", "Original Paper PubMedID", "OriginalPaperPMID")
_NOTICE_DOI_COLUMNS = ("RetractionDOI", "Retraction DOI")
_NOTICE_PMID_COLUMNS = ("RetractionPubMedID", "Retraction PubMedID")
_NATURE_COLUMNS = ("RetractionNature", "Nature")
_REASON_COLUMNS = ("Reason", "Reasons", "Reason(s)")
_TITLE_COLUMNS = ("Title", "OriginalPaperTitle")
_JOURNAL_COLUMNS = ("Journal",)
_RETRACTION_DATE_COLUMNS = ("RetractionDate", "Retraction Date")
_ORIGINAL_DATE_COLUMNS = ("OriginalPaperDate", "Original Paper Date")

# Values the export writes to mean "there is no identifier here". Neither is
# falsy, so a plain truthiness test accepts both: measured over the 2026-08-03
# export, ``"0"`` appears in 46.04% of PubMed ID cells and ``"unavailable"``
# (in two casings) in 4.80% of DOI cells. Storing them collapses tens of
# thousands of unrelated notices onto one key. Only these two are listed --
# each was measured; adding an unmeasured guess is how the list stops being
# answerable to the data.
_ABSENT_IDENTIFIER_VALUES = frozenset({"0", "unavailable"})

# ISO first, then the US-first form the export actually uses. The
# ``%m/%d/%Y`` / ``%d/%m/%Y`` ambiguity is real and is not resolved: for any
# day <= 12 both parse and disagree, and nothing in the row says which was
# meant. US-first is kept because Retraction Watch is a US publication.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d")


def _find_column(row: Mapping[str, str | None], candidates: tuple[str, ...]) -> str | None:
    """Return the stripped value of the first candidate column that has one."""
    for name in candidates:
        value = row.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _clean_identifier(value: str | None) -> str | None:
    """Return a usable identifier, or ``None`` for a blank or sentinel value.

    See :data:`_ABSENT_IDENTIFIER_VALUES` for why a truthiness test is not
    enough.
    """
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() in _ABSENT_IDENTIFIER_VALUES:
        return None
    return text


def _split_reasons(value: str | None) -> list[str]:
    """Split a ``Reason`` cell into individual reasons.

    Reasons are semicolon-separated, and every populated row of the Crossref
    export ends with a trailing ``;`` -- so empties are dropped rather than
    yielding a blank final reason. A single leading ``+`` is stripped: that
    prefix belongs to Retraction Watch's own export, not Crossref's, and
    costs nothing to accommodate.
    """
    if not value:
        return []
    reasons: list[str] = []
    for part in value.split(";"):
        item = part.strip()
        if item.startswith("+"):
            item = item[1:].strip()
        if item:
            reasons.append(item)
    return reasons


def _parse_date(value: str | None) -> str | None:
    """Parse an export date into an ISO ``yyyy-mm-dd`` string, or ``None``.

    The export writes ``M/D/YYYY H:MM`` (a time component is present on every
    dated row), so a trailing time is tolerated. An unparseable value returns
    ``None`` rather than failing the row -- a missing date is worth less than
    a lost retraction.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    candidates = [text]
    if " " in text:
        candidates.append(text.split(" ", 1)[0])
    for candidate in candidates:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None
```

> Note: the imports of `csv`, `codecs`, `io`, `json`, `Callable`, `Iterable`, `Iterator`, `Sequence`, `IO`, `Path`, `Any`, and the `bmlib.db` / storage names are used by Tasks 3–5. If ruff's F401 flags them at this point, add them in the task that first uses them instead of all at once.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_retractions.py -v`
Expected: PASS (all Task 1 tests plus 16 new)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/publications/retractions.py tests/test_retractions.py
git commit -m "feat(publications): add Retraction Watch field parsing helpers"
```

---

### Task 3: The streaming CSV parser

**Files:**
- Modify: `bmlib/publications/retractions.py` (append)
- Test: `tests/test_retractions.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces: `parse_retraction_watch_csv(source: str | Path | IO[bytes], *, on_skip: Callable[[int, str], None] | None = None) -> Iterator[RetractionNotice]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retractions.py`:

```python
import io

from bmlib.publications.retractions import parse_retraction_watch_csv

_HEADER = (
    "Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,"
    "ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,"
    "OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n"
)


def _row(
    record_id="1",
    retraction_date="3/9/2026 0:00",
    retraction_doi="10.1/notice",
    retraction_pmid="87654321",
    original_date="5/6/2023 0:00",
    original_doi="10.1/paper",
    original_pmid="12345678",
    nature="Retraction",
    reason="Rogue Editor;",
    title="A paper",
    journal="Soft Computing",
):
    """Build one CSV data row matching the live export's 21-field shape."""
    return (
        f"{record_id},{title},Subject,Inst,{journal},Pub,AU,Author,URL,Article,"
        f"{retraction_date},{retraction_doi},{retraction_pmid},{original_date},"
        f"{original_doi},{original_pmid},{nature},{reason},No,Notes,\n"
    )


def _csv(*rows, header=_HEADER, encoding="utf-8"):
    """Return a seekable binary stream of a CSV document."""
    return io.BytesIO((header + "".join(rows)).encode(encoding))


class TestParsingTheExport:
    def test_a_row_becomes_a_notice_with_both_identifier_pairs(self):
        (notice,) = list(parse_retraction_watch_csv(_csv(_row())))

        assert notice.record_id == "1"
        assert notice.nature is RetractionNature.RETRACTION
        assert notice.doi == "10.1/paper"
        assert notice.pmid == "12345678"
        assert notice.notice_doi == "10.1/notice"
        assert notice.notice_pmid == "87654321"
        assert notice.title == "A paper"
        assert notice.journal == "Soft Computing"
        assert notice.retraction_date == "2026-03-09"
        assert notice.original_paper_date == "2023-05-06"
        assert notice.reasons == ["Rogue Editor"]
        assert notice.raw_nature == "Retraction"

    def test_a_zero_pubmed_id_is_not_stored_as_a_pmid(self):
        (notice,) = list(parse_retraction_watch_csv(_csv(_row(original_pmid="0"))))

        assert notice.pmid is None
        assert notice.doi == "10.1/paper"

    def test_an_unavailable_doi_is_not_stored_as_a_doi(self):
        rows = (_row(record_id="1", original_doi="Unavailable"),
                _row(record_id="2", original_doi="unavailable"))

        notices = list(parse_retraction_watch_csv(_csv(*rows)))

        assert [n.doi for n in notices] == [None, None]
        assert [n.pmid for n in notices] == ["12345678", "12345678"]

    def test_a_row_with_no_usable_identifier_is_reported_not_stored(self):
        skipped: list[tuple[int, str]] = []
        row = _row(original_doi="Unavailable", original_pmid="0")

        notices = list(parse_retraction_watch_csv(_csv(row), on_skip=skipped.append))

        assert notices == []
        assert len(skipped) == 1
        assert skipped[0][0] == 2

    def test_the_trailing_empty_rows_are_skipped_not_stored(self):
        # The live export ends with 190 entirely empty rows.
        empty = "," * 20 + "\n"
        skipped: list[tuple[int, str]] = []

        notices = list(
            parse_retraction_watch_csv(_csv(_row(), empty, empty), on_skip=skipped.append)
        )

        assert len(notices) == 1
        assert len(skipped) == 2

    def test_a_byte_order_mark_does_not_hide_the_first_column(self):
        # Decoded as plain utf-8, a BOM glues itself to the first field name,
        # so "Record ID" becomes unfindable and every row is skipped.
        stream = _csv(_row(), encoding="utf-8-sig")

        (notice,) = list(parse_retraction_watch_csv(stream))

        assert notice.record_id == "1"

    def test_a_failed_encoding_attempt_does_not_duplicate_rows(self):
        # Upstream accumulated into a list created outside its encoding retry
        # loop and never cleared it, so a decode failure part-way through left
        # the rows already read in place and the next attempt appended them
        # all again.
        rows = [_row(record_id=str(i)) for i in range(1, 21)]
        # A cp1252 byte that is not valid UTF-8, inside a later row's title.
        document = (_HEADER + "".join(rows)).encode("utf-8").replace(b"A paper", b"caf\xe9", 1)

        notices = list(parse_retraction_watch_csv(io.BytesIO(document)))

        assert len(notices) == 20
        assert [n.record_id for n in notices] == [str(i) for i in range(1, 21)]

    def test_an_unknown_nature_does_not_stop_the_parse(self):
        rows = (_row(record_id="1", nature="Partial Retraction"), _row(record_id="2"))

        notices = list(parse_retraction_watch_csv(_csv(*rows)))

        assert [n.nature for n in notices] == [
            RetractionNature.OTHER,
            RetractionNature.RETRACTION,
        ]
        assert notices[0].raw_nature == "Partial Retraction"

    def test_a_path_is_accepted_as_well_as_a_stream(self, tmp_path):
        path = tmp_path / "rw.csv"
        path.write_bytes((_HEADER + _row()).encode("utf-8"))

        (notice,) = list(parse_retraction_watch_csv(path))

        assert notice.record_id == "1"

    def test_a_non_seekable_stream_is_rejected_clearly(self):
        class _Unseekable(io.RawIOBase):
            def readable(self):
                return True

            def seekable(self):
                return False

        with pytest.raises(ValueError, match="seekable"):
            list(parse_retraction_watch_csv(_Unseekable()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_retractions.py -v -k TestParsingTheExport`
Expected: FAIL — `ImportError: cannot import name 'parse_retraction_watch_csv'`

- [ ] **Step 3: Implement the parser**

Append to `bmlib/publications/retractions.py`:

```python
# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# How much of the file to decode before choosing an encoding.
_PROBE_BYTES = 1 << 16

# ``utf-8-sig`` must precede ``utf-8``: on a BOM'd file plain utf-8 does not
# fail, it succeeds and glues the BOM to the first field name, so the first
# column becomes unfindable. ``latin-1`` is last and makes the chain total --
# every byte is valid Latin-1 -- which is what lets the reader stream without
# a mid-file decode failure it could not retry.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _detect_encoding(head: bytes) -> str:
    """Choose an encoding by decoding a leading chunk of the file.

    An *incremental* decoder is used because the probe almost certainly cuts
    a multi-byte character in half; a plain ``bytes.decode`` would call that a
    UnicodeDecodeError and fall through to a wrong encoding.
    """
    for encoding in _ENCODINGS:
        decoder = codecs.getincrementaldecoder(encoding)()
        try:
            decoder.decode(head, final=False)
        except UnicodeDecodeError:
            continue
        return encoding
    return "latin-1"


def _report_skip(
    on_skip: Callable[[int, str], None] | None, line_number: int, reason: str
) -> None:
    """Log a skipped row and hand it to the caller's callback, if any."""
    logger.debug("Skipping Retraction Watch row %d: %s", line_number, reason)
    if on_skip is not None:
        on_skip(line_number, reason)


def _row_to_notice(
    row: Mapping[str, str | None],
    line_number: int,
    on_skip: Callable[[int, str], None] | None,
) -> RetractionNotice | None:
    """Build a notice from one CSV row, or ``None`` if the row is unusable."""
    record_id = _find_column(row, _RECORD_ID_COLUMNS)
    if record_id is None:
        _report_skip(on_skip, line_number, "no Record ID")
        return None

    doi = _clean_identifier(_find_column(row, _DOI_COLUMNS))
    pmid = _clean_identifier(_find_column(row, _PMID_COLUMNS))
    if doi is None and pmid is None:
        _report_skip(on_skip, line_number, "no usable DOI or PMID for the retracted paper")
        return None

    raw_nature = _find_column(row, _NATURE_COLUMNS)
    return RetractionNotice(
        record_id=record_id,
        nature=RetractionNature.from_raw(raw_nature),
        doi=doi,
        pmid=pmid,
        notice_doi=_clean_identifier(_find_column(row, _NOTICE_DOI_COLUMNS)),
        notice_pmid=_clean_identifier(_find_column(row, _NOTICE_PMID_COLUMNS)),
        title=_find_column(row, _TITLE_COLUMNS),
        journal=_find_column(row, _JOURNAL_COLUMNS),
        retraction_date=_parse_date(_find_column(row, _RETRACTION_DATE_COLUMNS)),
        original_paper_date=_parse_date(_find_column(row, _ORIGINAL_DATE_COLUMNS)),
        reasons=_split_reasons(_find_column(row, _REASON_COLUMNS)),
        raw_nature=raw_nature,
    )


def _parse_stream(
    handle: IO[bytes], on_skip: Callable[[int, str], None] | None
) -> Iterator[RetractionNotice]:
    """Yield notices from an open, seekable binary stream."""
    if not handle.seekable():
        raise ValueError(
            "parse_retraction_watch_csv() needs a seekable binary stream: the"
            " encoding is chosen from a leading probe, which must then be"
            " re-read. Save the download to a file first."
        )
    head = handle.read(_PROBE_BYTES)
    handle.seek(0)
    text = io.TextIOWrapper(handle, encoding=_detect_encoding(head), newline="")
    try:
        reader = csv.DictReader(text)
        # start=2: line 1 is the header, so the number matches what an editor shows.
        for line_number, row in enumerate(reader, start=2):
            notice = _row_to_notice(row, line_number, on_skip)
            if notice is not None:
                yield notice
    finally:
        # A TextIOWrapper closes the stream it wraps when it is finalised.
        # Detaching leaves a caller-supplied stream open and theirs to close,
        # which is what a library taking an open handle owes its caller.
        text.detach()


def parse_retraction_watch_csv(
    source: str | Path | IO[bytes],
    *,
    on_skip: Callable[[int, str], None] | None = None,
) -> Iterator[RetractionNotice]:
    """Stream :class:`RetractionNotice` records from a Retraction Watch CSV.

    The file is read lazily rather than materialised: the Crossref export is
    65 MB and 71,306 rows, which is not worth holding in memory as dicts.

    Args:
        source: Path to the CSV, or an open **seekable binary** stream. The
            encoding is detected from a leading probe (``utf-8-sig`` before
            ``utf-8``, falling back through ``cp1252`` to ``latin-1``), so the
            stream must support ``seek``.
        on_skip: Called as ``on_skip(line_number, reason)`` for each row that
            cannot be used -- one with no ``Record ID`` (the export ends with
            190 entirely empty rows) or no usable identifier for the retracted
            paper (5,189 rows carry only sentinels). Skips are also logged at
            DEBUG, so a short import is diagnosable rather than silent.

    Yields:
        One :class:`RetractionNotice` per usable row.

    Raises:
        ValueError: If *source* is a stream that is not seekable.
    """
    if hasattr(source, "read"):
        yield from _parse_stream(source, on_skip)  # type: ignore[arg-type]
        return
    with open(source, "rb") as handle:
        yield from _parse_stream(handle, on_skip)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_retractions.py -v`
Expected: PASS (all previous plus 10 new)

- [ ] **Step 5: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/publications/retractions.py tests/test_retractions.py
git commit -m "feat(publications): stream Retraction Watch notices from CSV"
```

---

### Task 4: The retraction rule

**Files:**
- Modify: `bmlib/publications/retractions.py` (append)
- Test: `tests/test_retractions.py` (append)

**Interfaces:**
- Consumes: `RetractionNotice`, `RetractionNature`.
- Produces: `is_retracted(notices: Sequence[RetractionNotice]) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retractions.py`:

```python
from bmlib.publications.retractions import is_retracted


def _notice(nature, date, record_id="x"):
    return RetractionNotice(
        record_id=record_id, nature=nature, doi="10.1/paper", retraction_date=date
    )


class TestTheRetractionRule:
    def test_no_notices_means_not_retracted(self):
        assert is_retracted([]) is False

    def test_a_single_retraction_reads_as_retracted(self):
        assert is_retracted([_notice(RetractionNature.RETRACTION, "2020-01-01")]) is True

    def test_a_reinstatement_does_not_read_as_retracted(self):
        # A Reinstatement is the opposite of a retraction. Upstream stored
        # every row as is_retracted=TRUE, including these.
        notices = [
            _notice(RetractionNature.REINSTATEMENT, "2022-10-28"),
            _notice(RetractionNature.RETRACTION, "2020-05-01"),
        ]

        assert is_retracted(notices) is False

    def test_a_later_correction_does_not_clear_an_earlier_retraction(self):
        # 10.1016/j.anbehav.2009.11.027 in the live export: retracted
        # 2011-09-08, corrected 2017-12-14. A flat "latest notice wins" reads
        # this retracted paper as clean, and 51 other papers with it.
        notices = [
            _notice(RetractionNature.CORRECTION, "2017-12-14"),
            _notice(RetractionNature.RETRACTION, "2011-09-08"),
        ]

        assert is_retracted(notices) is True

    def test_an_expression_of_concern_after_a_retraction_does_not_clear_it(self):
        notices = [
            _notice(RetractionNature.EXPRESSION_OF_CONCERN, "2024-10-02"),
            _notice(RetractionNature.RETRACTION, "2024-09-30"),
        ]

        assert is_retracted(notices) is True

    def test_a_retraction_after_a_reinstatement_reads_as_retracted(self):
        notices = [
            _notice(RetractionNature.RETRACTION, "2024-01-01"),
            _notice(RetractionNature.REINSTATEMENT, "2022-01-01"),
        ]

        assert is_retracted(notices) is True

    def test_an_expression_of_concern_alone_is_not_a_retraction(self):
        assert is_retracted([_notice(RetractionNature.EXPRESSION_OF_CONCERN, "2021-01-01")]) is False

    def test_the_order_notices_arrive_in_does_not_change_the_answer(self):
        newest = _notice(RetractionNature.CORRECTION, "2017-12-14")
        oldest = _notice(RetractionNature.RETRACTION, "2011-09-08")

        assert is_retracted([newest, oldest]) is is_retracted([oldest, newest])

    def test_a_dateless_notice_does_not_outrank_a_dated_one(self):
        notices = [
            _notice(RetractionNature.RETRACTION, "2020-01-01"),
            _notice(RetractionNature.REINSTATEMENT, None),
        ]

        assert is_retracted(notices) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_retractions.py -v -k TestTheRetractionRule`
Expected: FAIL — `ImportError: cannot import name 'is_retracted'`

- [ ] **Step 3: Implement the rule**

Append to `bmlib/publications/retractions.py`:

```python
# ---------------------------------------------------------------------------
# The retraction rule
# ---------------------------------------------------------------------------


def _newest_first(notices: Sequence[RetractionNotice]) -> list[RetractionNotice]:
    """Order notices newest first, with undated ones last.

    ``""`` sorts below any ISO date, so a notice with no date never displaces
    a dated one. The sort is stable, so the order
    :func:`lookup_retractions` returned is preserved within a tie.
    """
    return sorted(notices, key=lambda notice: notice.retraction_date or "", reverse=True)


def is_retracted(notices: Sequence[RetractionNotice]) -> bool:
    """Decide whether a paper is currently retracted, from all its notices.

    Scans newest first; the first :attr:`RetractionNature.RETRACTION` or
    :attr:`RetractionNature.REINSTATEMENT` decides. A Correction or an
    Expression of Concern is **not** evidence either way, which is what makes
    this different from a flat "latest notice wins": a paper retracted in 2011
    and corrected in 2017 is still retracted, and 52 papers in the live export
    have exactly that shape.

    Pure by design -- it takes the notices, not a connection -- so the rule is
    testable without a database and re-derivable without re-importing 71,306
    rows if it ever changes. Pair it with :func:`lookup_retractions`::

        if is_retracted(lookup_retractions(conn, doi=doi)):
            ...
    """
    for notice in _newest_first(notices):
        if notice.nature is RetractionNature.RETRACTION:
            return True
        if notice.nature is RetractionNature.REINSTATEMENT:
            return False
    return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_retractions.py -v`
Expected: PASS (all previous plus 9 new)

- [ ] **Step 5: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/publications/retractions.py tests/test_retractions.py
git commit -m "feat(publications): decide retraction status from a paper's notices"
```

---

### Task 5: The schema

**Files:**
- Modify: `bmlib/publications/schema.py` (both DDL strings)
- Test: `tests/test_backends.py` (extend `TestSchema`)

**Interfaces:**
- Consumes: nothing.
- Produces: a `retraction_notices` table created by `ensure_schema(conn)` on both backends, with columns `id`, `record_id` (`TEXT NOT NULL UNIQUE`), `doi`, `pmid`, `notice_doi`, `notice_pmid`, `nature` (`TEXT NOT NULL`), `raw_nature`, `title`, `journal`, `retraction_date`, `original_paper_date`, `reasons` (`TEXT NOT NULL DEFAULT '[]'`), `created_at`, `updated_at`.

- [ ] **Step 1: Write the failing test**

In `tests/test_backends.py`, find `TestSchema::test_ensure_schema_creates_all_tables` and add `"retraction_notices"` to its table tuple, then add below it:

```python
    def test_the_record_id_is_unique(self, backend_conn):
        # A full UNIQUE constraint, not the partial index publications uses
        # for doi/pmid: ON CONFLICT cannot infer a partial index without
        # repeating its predicate, and a retraction notice always has a
        # Record ID so there is no reason to accept a null.
        ensure_schema(backend_conn)
        ph = placeholder(backend_conn)
        columns = "(record_id, nature, reasons, created_at, updated_at)"
        values = f"({', '.join([ph] * 5)})"

        execute(
            backend_conn,
            f"INSERT INTO retraction_notices {columns} VALUES {values}",
            ("rw-1", "retraction", "[]", "2026-01-01", "2026-01-01"),
        )

        with pytest.raises(Exception):  # noqa: B017 -- dialect-specific class
            execute(
                backend_conn,
                f"INSERT INTO retraction_notices {columns} VALUES {values}",
                ("rw-1", "retraction", "[]", "2026-01-01", "2026-01-01"),
            )

        # PostgreSQL leaves the transaction aborted after an integrity error,
        # so every later statement on this connection -- including the
        # fixture's teardown -- fails until it is rolled back.
        backend_conn.rollback()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_backends.py -v -k TestSchema`
Expected: FAIL — `no such table: retraction_notices`

- [ ] **Step 3: Add the DDL to both dialect strings**

In `bmlib/publications/schema.py`, append to `SCHEMA_SQL` (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS retraction_notices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       TEXT NOT NULL UNIQUE,
    doi             TEXT,
    pmid            TEXT,
    notice_doi      TEXT,
    notice_pmid     TEXT,
    nature          TEXT NOT NULL,
    raw_nature      TEXT,
    title           TEXT,
    journal         TEXT,
    retraction_date TEXT,
    original_paper_date TEXT,
    reasons         TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_doi
    ON retraction_notices (doi);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_pmid
    ON retraction_notices (pmid);
```

And the same to `SCHEMA_SQL_POSTGRESQL`, with `id SERIAL PRIMARY KEY` in place of the SQLite `id` line. Every other line is identical — the two dialects agree on everything else here.

Then extend the module docstring's second paragraph to mention the new table, so the file still describes what it creates.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_backends.py -v -k TestSchema`
Expected: PASS. If a PostgreSQL DSN is configured, both parameterisations pass:

```bash
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/pgrun port=5432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py -v -k TestSchema
```

- [ ] **Step 5: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/publications/schema.py tests/test_backends.py
git commit -m "feat(publications): add the retraction_notices table to both backends"
```

---

### Task 6: Storage and lookup

**Files:**
- Modify: `bmlib/publications/retractions.py` (append)
- Test: `tests/test_backends.py` (append a `TestRetractionStorage` class)

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `store_retraction_notices(conn: Any, notices: Iterable[RetractionNotice]) -> int` — upserts on `record_id`, returns the number written.
  - `lookup_retractions(conn: Any, *, doi: str | None = None, pmid: str | None = None) -> list[RetractionNotice]` — newest first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backends.py` (add the imports `from bmlib.publications.models import RetractionNature, RetractionNotice` and `from bmlib.publications.retractions import is_retracted, lookup_retractions, store_retraction_notices` to the existing import block):

```python
# ---------------------------------------------------------------------------
# Retraction notices
# ---------------------------------------------------------------------------


def _notice(record_id, nature, date, doi="10.1/paper", pmid=None):
    """Build a RetractionNotice with the fields these tests care about."""
    return RetractionNotice(
        record_id=record_id,
        nature=nature,
        doi=doi,
        pmid=pmid,
        retraction_date=date,
        reasons=["Rogue Editor"],
    )


class TestRetractionStorage:
    def test_a_notice_round_trips(self, backend_conn):
        ensure_schema(backend_conn)
        stored = _notice("rw-1", RetractionNature.RETRACTION, "2020-05-01", pmid="123")

        assert store_retraction_notices(backend_conn, [stored]) == 1

        (loaded,) = lookup_retractions(backend_conn, doi="10.1/paper")
        assert loaded.record_id == "rw-1"
        assert loaded.nature is RetractionNature.RETRACTION
        assert loaded.retraction_date == "2020-05-01"
        assert loaded.reasons == ["Rogue Editor"]
        assert loaded.pmid == "123"

    def test_reimporting_the_same_file_does_not_duplicate_notices(self, backend_conn):
        ensure_schema(backend_conn)
        notices = [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01")]

        store_retraction_notices(backend_conn, notices)
        store_retraction_notices(backend_conn, notices)

        assert _count(backend_conn, "retraction_notices") == 1

    def test_a_reimport_refreshes_a_changed_notice(self, backend_conn):
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn, [_notice("rw-1", RetractionNature.EXPRESSION_OF_CONCERN, "2020-05-01")]
        )

        store_retraction_notices(
            backend_conn, [_notice("rw-1", RetractionNature.RETRACTION, "2021-06-02")]
        )

        (loaded,) = lookup_retractions(backend_conn, doi="10.1/paper")
        assert loaded.nature is RetractionNature.RETRACTION
        assert loaded.retraction_date == "2021-06-02"

    def test_a_prefixed_uppercase_doi_matches_a_stored_notice(self, backend_conn):
        # Stored and looked up through the same normalisers store_publication
        # uses; a second normaliser that drifts is a lookup that silently
        # misses a paper that is in fact retracted.
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn, [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01",
                                   doi="10.1016/J.ABC")]
        )

        found = lookup_retractions(backend_conn, doi="https://doi.org/10.1016/j.abc")

        assert len(found) == 1

    def test_notices_come_back_newest_first(self, backend_conn):
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [
                _notice("rw-old", RetractionNature.RETRACTION, "2011-09-08"),
                _notice("rw-new", RetractionNature.CORRECTION, "2017-12-14"),
            ],
        )

        found = lookup_retractions(backend_conn, doi="10.1/paper")

        assert [n.record_id for n in found] == ["rw-new", "rw-old"]
        assert is_retracted(found) is True

    def test_an_undated_notice_sorts_after_a_dated_one_on_both_backends(self, backend_conn):
        # SQLite and PostgreSQL disagree about where NULLs land in a DESC
        # sort, so the ORDER BY has to say explicitly. Without that, the
        # "newest" notice differs by backend and is_retracted() follows it.
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [
                _notice("rw-dated", RetractionNature.RETRACTION, "2020-01-01"),
                _notice("rw-undated", RetractionNature.REINSTATEMENT, None),
            ],
        )

        found = lookup_retractions(backend_conn, doi="10.1/paper")

        assert found[0].record_id == "rw-dated"
        assert is_retracted(found) is True

    def test_a_lookup_by_pmid_finds_a_notice_stored_without_a_doi(self, backend_conn):
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01", doi=None, pmid="99")],
        )

        found = lookup_retractions(backend_conn, pmid="99")

        assert [n.record_id for n in found] == ["rw-1"]

    def test_a_lookup_with_no_identifier_is_a_programming_error(self, backend_conn):
        ensure_schema(backend_conn)

        with pytest.raises(ValueError):
            lookup_retractions(backend_conn)

    def test_an_unknown_paper_has_no_notices(self, backend_conn):
        ensure_schema(backend_conn)

        assert lookup_retractions(backend_conn, doi="10.1/never-retracted") == []

    def test_a_caller_transaction_owns_the_commit(self, backend_conn):
        ensure_schema(backend_conn)

        with transaction(backend_conn):
            store_retraction_notices(
                backend_conn, [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01")]
            )
            assert not owns_commit(backend_conn)

        assert _count(backend_conn, "retraction_notices") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_backends.py -v -k TestRetractionStorage`
Expected: FAIL — `ImportError: cannot import name 'store_retraction_notices'`

- [ ] **Step 3: Implement storage and lookup**

Append to `bmlib/publications/retractions.py`:

```python
# ---------------------------------------------------------------------------
# Storage and lookup
# ---------------------------------------------------------------------------

_NOTICE_COLUMNS = (
    "record_id",
    "doi",
    "pmid",
    "notice_doi",
    "notice_pmid",
    "nature",
    "raw_nature",
    "title",
    "journal",
    "retraction_date",
    "original_paper_date",
    "reasons",
    "created_at",
    "updated_at",
)

# Everything except the key and the creation stamp is replaced on re-import:
# the CSV is the source of truth, so a notice whose reasons or nature changed
# upstream must change here too. This is the opposite of store_publication()'s
# merge, where several sources contribute to one row.
_NOTICE_UPDATE_COLUMNS = tuple(
    column for column in _NOTICE_COLUMNS if column not in ("record_id", "created_at")
)


def store_retraction_notices(conn: Any, notices: Iterable[RetractionNotice]) -> int:
    """Insert or refresh retraction notices, keyed by ``record_id``.

    Re-importing the monthly export is idempotent: ``record_id`` is Retraction
    Watch's own primary key and carries a ``UNIQUE`` constraint, so a second
    import of the same file updates rather than duplicates.

    Identifiers are normalised with the same functions
    :func:`~bmlib.publications.storage.store_publication` uses, so a DOI
    stored here matches one looked up in any case or prefix variant.

    The whole batch is one transaction: called with no transaction open it
    commits once; called inside a caller's ``transaction(conn)`` block it
    joins that block and leaves the commit to the caller.

    Returns:
        The number of notices written.
    """
    ph = placeholder(conn)
    now = _now_iso()
    values_sql = ", ".join([ph] * len(_NOTICE_COLUMNS))
    update_sql = ", ".join(f"{column} = excluded.{column}" for column in _NOTICE_UPDATE_COLUMNS)
    statement = (
        f"INSERT INTO retraction_notices ({', '.join(_NOTICE_COLUMNS)})"
        f" VALUES ({values_sql})"
        f" ON CONFLICT (record_id) DO UPDATE SET {update_sql}"
    )

    count = 0
    with transaction(conn):
        for notice in notices:
            execute(
                conn,
                statement,
                (
                    notice.record_id,
                    _normalize_doi(notice.doi),
                    _normalize_pmid(notice.pmid),
                    _normalize_doi(notice.notice_doi),
                    _normalize_pmid(notice.notice_pmid),
                    notice.nature.value,
                    notice.raw_nature,
                    notice.title,
                    notice.journal,
                    notice.retraction_date,
                    notice.original_paper_date,
                    json.dumps(notice.reasons),
                    now,
                    now,
                ),
            )
            count += 1
    return count


def _row_to_notice_model(row: Any) -> RetractionNotice:
    """Build a :class:`RetractionNotice` from a database row."""
    return RetractionNotice(
        record_id=row["record_id"],
        nature=RetractionNature(row["nature"]),
        doi=row["doi"],
        pmid=row["pmid"],
        notice_doi=row["notice_doi"],
        notice_pmid=row["notice_pmid"],
        title=row["title"],
        journal=row["journal"],
        retraction_date=row["retraction_date"],
        original_paper_date=row["original_paper_date"],
        reasons=json.loads(row["reasons"]) if row["reasons"] else [],
        raw_nature=row["raw_nature"],
    )


def lookup_retractions(
    conn: Any,
    *,
    doi: str | None = None,
    pmid: str | None = None,
) -> list[RetractionNotice]:
    """Return every stored notice about one paper, newest first.

    A paper may have several notices -- 2,354 papers in the live export do --
    so this returns a list. Pass it to :func:`is_retracted` for the boolean.

    Identifiers are normalised before lookup, so any case or prefix variant of
    a DOI matches the canonical stored form. Supplying both ``doi`` and
    ``pmid`` matches a notice on **either**.

    Raises:
        ValueError: If neither ``doi`` nor ``pmid`` is given -- a lookup with
            no identifier is a programming error, not an empty result.
    """
    if doi is None and pmid is None:
        raise ValueError("lookup_retractions() needs a doi or a pmid")

    ph = placeholder(conn)
    clauses: list[str] = []
    params: list[str] = []
    normalised_doi = _normalize_doi(doi)
    if normalised_doi:
        clauses.append(f"doi = {ph}")
        params.append(normalised_doi)
    normalised_pmid = _normalize_pmid(pmid)
    if normalised_pmid:
        clauses.append(f"pmid = {ph}")
        params.append(normalised_pmid)
    if not clauses:
        return []

    rows = fetch_all(
        conn,
        f"SELECT * FROM retraction_notices WHERE {' OR '.join(clauses)}"
        # SQLite sorts NULLs last in a DESC order, PostgreSQL sorts them
        # first. Ordering on the IS NULL flag first pins undated notices last
        # on both, so "newest" means the same thing on either backend --
        # is_retracted() reads the first decisive row, so a backend-dependent
        # order is a backend-dependent answer.
        " ORDER BY (retraction_date IS NULL), retraction_date DESC, id DESC",
        tuple(params),
    )
    return [_row_to_notice_model(row) for row in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_backends.py -v -k TestRetractionStorage`
Expected: PASS (10 tests, ×2 if a PostgreSQL DSN is set)

Then the whole suite:

Run: `uv run pytest tests/ -q`
Expected: all pass, no regressions.

- [ ] **Step 5: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/publications/retractions.py tests/test_backends.py
git commit -m "feat(publications): store and look up retraction notices"
```

---

### Task 7: Exports and documentation

**Files:**
- Modify: `bmlib/publications/__init__.py`
- Modify: `docs/manual/publications.md`
- Modify: `CHANGELOG.md`, `ROADMAP.md`, `CLAUDE.md`
- Test: `tests/test_retractions.py` (append one export test)

**Interfaces:**
- Consumes: every public name from Tasks 1–6.
- Produces: `RetractionNature`, `RetractionNotice`, `parse_retraction_watch_csv`, `store_retraction_notices`, `lookup_retractions`, `is_retracted` importable from `bmlib.publications`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retractions.py`:

```python
class TestPublicSurface:
    def test_every_public_name_is_exported_from_the_package(self):
        import bmlib.publications as publications

        expected = {
            "RetractionNature",
            "RetractionNotice",
            "parse_retraction_watch_csv",
            "store_retraction_notices",
            "lookup_retractions",
            "is_retracted",
        }

        assert expected <= set(publications.__all__)
        for name in expected:
            assert hasattr(publications, name), name
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_retractions.py -v -k TestPublicSurface`
Expected: FAIL — `AssertionError` on the `__all__` subset check.

- [ ] **Step 3: Add the exports**

In `bmlib/publications/__init__.py`, add to the `models` import: `RetractionNature`, `RetractionNotice`. Add a new import block (ruff-sorted, after `from bmlib.publications.models import ...`):

```python
from bmlib.publications.retractions import (
    is_retracted,
    lookup_retractions,
    parse_retraction_watch_csv,
    store_retraction_notices,
)
```

Then append the six names to `__all__`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_retractions.py -v -k TestPublicSurface`
Expected: PASS

- [ ] **Step 5: Write the documentation**

**`docs/manual/publications.md`** — add a `## Retractions` section covering: where to get the CSV (Crossref's URL, and that it can take minutes and time out a default client); `parse_retraction_watch_csv()` with a worked example; that `doi`/`pmid` mean the retracted paper and `notice_doi`/`notice_pmid` the notice; `store_retraction_notices()` and re-import idempotence; `lookup_retractions()` + `is_retracted()` with the rule stated ("only a Retraction or a Reinstatement decides"); and the `on_skip` callback. A complete example:

```python
from bmlib.db import connect_sqlite
from bmlib.publications import (
    ensure_schema,
    is_retracted,
    lookup_retractions,
    parse_retraction_watch_csv,
    store_retraction_notices,
)

conn = connect_sqlite("literature.db")
ensure_schema(conn)

skipped = []
notices = parse_retraction_watch_csv("retraction_watch.csv", on_skip=lambda n, why: skipped.append((n, why)))
print(f"stored {store_retraction_notices(conn, notices)} notices, skipped {len(skipped)} rows")

if is_retracted(lookup_retractions(conn, doi="10.1016/j.anbehav.2009.11.027")):
    print("retracted — do not cite as evidence")
```

**`CHANGELOG.md`** — under `## [Unreleased]` → `### Added`:

```markdown
- **Retraction Watch notices: answer "is this paper retracted?"** Ported from
  bmlibrarian (Phase 2 row 10 of the porting analysis). A biomedical
  literature tool must not present a retracted paper as evidence, and bmlib
  had no way to tell. `parse_retraction_watch_csv()` streams the
  Crossref-distributed export (65 MB, 71,306 rows) into `RetractionNotice`
  records; `store_retraction_notices()` upserts them on Retraction Watch's own
  `record_id`, so re-importing the monthly file updates rather than
  duplicates; `lookup_retractions()` returns every notice about one paper,
  newest first, and the pure `is_retracted()` reduces them to a boolean.

  Purely additive — a new table and a new module, nothing existing changed, so
  no stored value moves.

  This is deliberately **not** a registered source fetcher. Fetchers are a
  date-keyed feed protocol producing publications; a retraction notice
  annotates a paper that is usually not in the caller's `publications` table
  at all.

  A row describes **two** papers, so both identifier pairs are kept under
  names that say which is which: `doi`/`pmid` are always the retracted paper,
  `notice_doi`/`notice_pmid` the notice.

  Five defects in the upstream implementation are fixed, each pinned by a
  regression test named for it:

  1. **The PMID match path was dead.** Its candidate column tuple contained
     none of the export's real names (`OriginalPaperPubMedID`,
     `RetractionPubMedID`), so every row matched `None`.
  2. **A failed encoding attempt duplicated every row already read.** The row
     accumulator was created outside the encoding retry loop and never
     cleared, so `utf-8` failing part-way through left those rows in place and
     the next encoding appended the whole file again. The port probes a
     leading chunk to choose the encoding, then streams.
  3. **A byte-order mark hid the first column.** `utf-8` was tried before
     `utf-8-sig`; on a BOM'd file it succeeds and glues the BOM to the first
     field name, so `Record ID` became unfindable.
  4. **Every row was stored as retracted** — including Corrections,
     Expressions of Concern, and Reinstatements, which are the opposite.
  5. **Missing identifiers are truthy sentinels.** The export writes `0` for
     an absent PubMed ID (46.04% of rows) and `Unavailable`/`unavailable` for
     an absent DOI, none of them falsy, so a truthiness test accepts them and
     collapses tens of thousands of unrelated notices onto a single fake key.

  The retraction rule is deliberately not "latest notice wins": scanning
  newest-first, only a Retraction or a Reinstatement decides, because a
  correction does not undo a retraction. 52 papers in the live export are
  retracted while carrying a later Correction or Expression of Concern.
```

**`ROADMAP.md`** — a row under **Publications (`bmlib.publications`)**:

```
| ✅ Done | Retraction Watch notices | Phase 2 row 10 of the bmlibrarian port. `parse_retraction_watch_csv()` streams the Crossref export into `RetractionNotice`s; `store_retraction_notices()` upserts them on Retraction Watch's own `record_id`, so re-importing the monthly file is idempotent; `lookup_retractions()` + the pure `is_retracted()` answer "is this paper retracted?". Not a fetcher — a notice annotates a paper that is usually not in the caller's table. Five upstream defects fixed, each with a named regression test: the PMID candidate list matched none of the export's real column names; the encoding-retry loop never cleared its accumulator, duplicating every row already read; `utf-8` was tried before `utf-8-sig`, so a BOM hid the first column; every row was stored as retracted, including Corrections, Expressions of Concern and Reinstatements; and missing identifiers are truthy sentinels (`0` in 46% of PubMed ID cells, `Unavailable`/`unavailable` in DOI cells), which a truthiness test accepts and which collapse tens of thousands of notices onto one key (unreleased) |
```

**`CLAUDE.md`** — add `retractions.py` to the `publications/` directory tree, extend the `publications/` module description with one sentence, and add `test_retractions.py` to the test-file mapping table's `publications/` row.

- [ ] **Step 6: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

Expected: all tests pass (1300 previous + ~46 new), both ruff commands clean.

- [ ] **Step 7: Commit**

```bash
git add bmlib/publications/__init__.py tests/test_retractions.py docs/manual/publications.md CHANGELOG.md ROADMAP.md CLAUDE.md
git commit -m "docs(publications): document Retraction Watch support"
```

---

## Definition of done

- [ ] `uv run pytest tests/ -v` — all pass, no skips beyond the existing 32.
- [ ] `BMLIB_TEST_POSTGRESQL_DSN=... uv run pytest tests/test_backends.py` — the PostgreSQL half of every retraction test runs and passes.
- [ ] `uvx ruff@0.15.20 check .` and `uvx ruff@0.15.20 format --check .` — clean.
- [ ] Every defect in the design doc's "Defects fixed in the port" has a test named after it, and that test **fails when the fix is reverted** — verify by mutation, not inspection (clear `__pycache__` after restoring; a same-length edit can otherwise fake the result).
- [ ] HANDOVER.md and ROADMAP.md updated; the design doc's deliberate non-fixes folded into HANDOVER's "Deliberate non-fixes" list.
- [ ] Branch pushed and a PR opened against `main`.
