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
from collections.abc import Callable, Iterator, Mapping, Sequence
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

# Chunk size used while scanning the whole file to test an encoding. A probe
# of the leading bytes is not enough: it can decode cleanly and still leave a
# single bad byte tens of megabytes later, which a streaming
# ``TextIOWrapper``/``csv.DictReader`` cannot retry once rows have already
# been yielded to the caller. Scanning to EOF up front costs one extra read
# pass (well under a second for a 65 MB file) and turns that documented
# failure mode into a guarantee instead. 1 MiB keeps exactly one chunk in
# memory at a time regardless of file size.
_ENCODING_SCAN_CHUNK_BYTES = 1 << 20

# Tried in this order because ``utf-8-sig`` must precede ``utf-8``: on a
# BOM'd file plain utf-8 does not fail, it succeeds and glues the BOM to the
# first field name, so the first column becomes unfindable. Neither of these
# nor ``cp1252`` is guaranteed to succeed -- see ``_FALLBACK_ENCODING``.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")

# The guaranteed-total fallback: every byte 0x00-0xFF is a valid Latin-1 code
# point, so decoding under it can never raise. It is deliberately not a
# member of ``_ENCODINGS`` and not tried inside the loop below -- doing
# either would make it a candidate that "might fail" like the others, which
# it structurally cannot, and would leave a final return after the loop
# unreachable.
_FALLBACK_ENCODING = "latin-1"


def _decodes_whole_file(handle: IO[bytes], encoding: str) -> bool:
    """Return whether *encoding* can decode *handle* end-to-end.

    Reads from the start of *handle* in fixed-size chunks through an
    *incremental* decoder: a plain ``bytes.decode`` per chunk would treat a
    multi-byte character split across a chunk boundary as a UnicodeDecodeError,
    rejecting an encoding that in fact decodes the file correctly. The final
    ``decode(b"", final=True)`` catches a file truncated mid-character, which
    no non-final chunk decode would.
    """
    handle.seek(0)
    decoder = codecs.getincrementaldecoder(encoding)()
    try:
        while True:
            chunk = handle.read(_ENCODING_SCAN_CHUNK_BYTES)
            if not chunk:
                decoder.decode(b"", final=True)
                return True
            decoder.decode(chunk, final=False)
    except UnicodeDecodeError:
        return False


def _detect_encoding(handle: IO[bytes]) -> str:
    """Choose an encoding able to decode *handle* end-to-end.

    Leaves *handle*'s position at EOF; the caller is responsible for
    rewinding before actually reading the file with the chosen encoding.
    """
    for encoding in _ENCODINGS:
        if _decodes_whole_file(handle, encoding):
            return encoding
    return _FALLBACK_ENCODING


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
            "parse_retraction_watch_csv() needs a seekable binary stream: an"
            " encoding is chosen only once it has been shown to decode the"
            " whole file, which means scanning it once and then rewinding to"
            " actually read it. Save the download to a file first."
        )
    encoding = _detect_encoding(handle)
    handle.seek(0)
    text = io.TextIOWrapper(handle, encoding=encoding, newline="")
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
            encoding is chosen by scanning the whole file for one that
            decodes it end-to-end (``utf-8-sig`` before ``utf-8``, falling
            back through ``cp1252`` to ``latin-1``, which always succeeds),
            so a single bad byte anywhere in the file is caught before any
            row is yielded rather than raising mid-stream. The stream must
            support ``seek``: the scan rewinds to actually read the file
            afterwards.
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
