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

import logging
from collections.abc import Mapping
from datetime import datetime

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
