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

import codecs
import csv
import io
import logging
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import IO

from bmlib.publications.models import RetractionNature, RetractionNotice

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


def _report_skip(on_skip: Callable[[int, str], None] | None, line_number: int, reason: str) -> None:
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
