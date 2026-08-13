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
import json
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from bmlib.db import executemany, fetch_all, placeholder, transaction
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
#
# The same set guards the *lookup* path, not just parsing: a caller holding a
# PMID column that stores "0" for "absent" -- which is what 46.04% of this
# very export writes -- would otherwise query for a paper that cannot exist
# and read the empty result as "not retracted".
_ABSENT_IDENTIFIER_VALUES = frozenset({"0", "unavailable"})

# The US-first form the export actually uses, tried first, then its
# day-first twin, then the two ISO-ish shapes. The ``%m/%d/%Y`` /
# ``%d/%m/%Y`` ambiguity is real and is not resolved: for any day <= 12 both
# parse and disagree, and nothing in the row says which was meant. US-first
# is kept because Retraction Watch is a US publication -- and it must stay
# immediately ahead of ``%d/%m/%Y`` if this order is ever touched again,
# since that relative order is the ambiguity resolution, not an optimisation.
# ``%Y-%m-%d`` and ``%Y/%m/%d`` cannot be confused with either slash form or
# each other (distinct separators; a 4-digit year does not parse as a 1-2
# digit ``%m``/``%d``), so moving them after costs nothing: profiling put
# 3.62s of a 4.95s parse of the 2026-08-03 export's 142,612 dates in
# ``strptime``, because every one of them is ``%m/%d/%Y`` (with a time
# component -- see below) and it used to be reached only on the sixth of
# eight attempts (four formats against the full text, then the date-only
# candidate's first two).
_DATE_FORMATS = ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d")


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

    The date-only candidate (the text split at the first space) is tried
    *before* the full text with its time component still attached, since
    every real dated row has a time component and none of ``_DATE_FORMATS``
    matches one -- trying the full text first burns one wasted ``strptime``
    call per format (four, as ``_DATE_FORMATS`` stands) before falling
    through to the split-off date, on every single row.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    candidates = [text.split(" ", 1)[0], text] if " " in text else [text]
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

# Deliberately omits plain ``utf-8``: ``utf-8-sig`` decodes a BOM'd file by
# stripping the (always-valid-UTF-8) BOM and decoding the remainder as
# utf-8, and decodes a non-BOM'd file identically to plain utf-8 -- so as a
# yes/no decodability test the two are equivalent, and ``utf-8-sig`` already
# comes first. Including bare ``utf-8`` after it is a guaranteed-losing
# attempt on every file: whatever plain utf-8 could decode, utf-8-sig
# already decoded and returned on. Fuzzed against 200,000 random byte
# strings with no counterexample. ``cp1252`` is not guaranteed to succeed --
# see ``_FALLBACK_ENCODING``.
_ENCODINGS = ("utf-8-sig", "cp1252")

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

    Anything but the first candidate is reported at WARNING. Falling back is
    not a detail: the export is nominally UTF-8, so needing ``cp1252`` or
    ``latin-1`` means a byte in the file is not valid UTF-8 -- and since the
    fallbacks decode *every* byte rather than failing, the whole file is then
    re-read under an encoding that silently mis-renders every non-ASCII
    character in 66,000 titles, journals and reasons. Unlogged, that is a
    corrupt import indistinguishable from a clean one.

    Leaves *handle*'s position wherever the last scan left it; the caller is
    responsible for rewinding before actually reading the file with the
    chosen encoding.
    """
    for encoding in _ENCODINGS:
        if _decodes_whole_file(handle, encoding):
            if encoding != _ENCODINGS[0]:
                logger.warning(
                    "Retraction Watch CSV does not decode as %s; reading it as %s"
                    " instead. Non-ASCII characters may be misread.",
                    _ENCODINGS[0],
                    encoding,
                )
            return encoding
    logger.warning(
        "Retraction Watch CSV decodes as none of %s; falling back to %s, which accepts"
        " every byte and so cannot fail, but may misread any non-ASCII character.",
        ", ".join(_ENCODINGS),
        _FALLBACK_ENCODING,
    )
    return _FALLBACK_ENCODING


def _report_skip(on_skip: Callable[[int, str], None] | None, line_number: int, reason: str) -> None:
    """Log a skipped row and hand it to the caller's callback, if any."""
    logger.debug("Skipping Retraction Watch row %d: %s", line_number, reason)
    if on_skip is not None:
        on_skip(line_number, reason)


def _report_unknown_nature(seen: set[str], raw_nature: str) -> None:
    """Warn once per distinct ``RetractionNature`` value this version cannot map.

    Mapping an unknown nature to ``OTHER`` rather than raising is deliberate
    (see :class:`~bmlib.publications.models.RetractionNature`), but silence is
    not: :func:`is_retracted` treats ``OTHER`` as evidence of nothing, so if
    Retraction Watch ever rewords ``"Retraction"``, an import would succeed,
    store all 66,062 of them as ``OTHER``, and answer "not retracted" for
    every paper in the file. That is this feature's worst failure and it must
    not be silent.

    Warned once per distinct value rather than once per row: the vocabulary is
    small, so this is bounded at a handful of lines even when every row is
    affected, while a per-row warning would emit 66,000 of them.
    """
    key = raw_nature.strip().lower()
    if key in seen:
        return
    seen.add(key)
    logger.warning(
        "Unrecognised Retraction Watch nature %r; storing it as %r, which"
        " is_retracted() reads as evidence of neither retraction nor"
        " reinstatement. The original string is kept in raw_nature.",
        raw_nature,
        RetractionNature.OTHER.value,
    )


def _row_to_notice(
    row: Mapping[str, str | None],
    line_number: int,
    on_skip: Callable[[int, str], None] | None,
    unknown_natures: set[str],
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
    nature = RetractionNature.from_raw(raw_nature)
    if nature is RetractionNature.OTHER and raw_nature:
        _report_unknown_nature(unknown_natures, raw_nature)
    return RetractionNotice(
        record_id=record_id,
        nature=nature,
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


def _reject_unusable_stream(handle: IO[bytes]) -> None:
    """Raise :class:`ValueError` unless *handle* is a seekable binary stream.

    Kept out of the generator below so the public entry point can run it
    *eagerly*. A generator body does not execute until the first ``next()``,
    which for the documented usage means these errors would otherwise surface
    from inside :func:`store_retraction_notices`'s transaction rather than at
    the call that got the argument wrong.
    """
    # `type: ignore[unreachable]`: no class can subclass both `IO[bytes]` and
    # `TextIOBase`, so a checker reading the annotation alone calls this body
    # dead. It is not — the annotation is a request, not an enforcement, and
    # this guard exists for the caller who does not honour it. Deleting it to
    # satisfy the checker would restore the confusing `codecs` failure below.
    if isinstance(handle, io.TextIOBase):  # type: ignore[unreachable]
        # A plausible slip: the signature also accepts a bare path, so
        # open(path) (text mode) reads as a valid call. Text mode decodes on
        # read using its own encoding, before this module's own whole-file
        # encoding detection ever gets a chance to run against raw bytes --
        # left unguarded, this dies inside `codecs` with "can't concat str
        # to bytes", which does not say what went wrong.
        raise ValueError(
            "parse_retraction_watch_csv() needs a binary stream, not a text"
            " one: use open(path, 'rb') rather than open(path), or pass the"
            " path itself and let this function open it."
        )
    if not handle.seekable():
        raise ValueError(
            "parse_retraction_watch_csv() needs a seekable binary stream: an"
            " encoding is chosen only once it has been shown to decode the"
            " whole file, which means scanning it once and then rewinding to"
            " actually read it. Save the download to a file first."
        )


def _parse_stream(
    handle: IO[bytes], on_skip: Callable[[int, str], None] | None
) -> Iterator[RetractionNotice]:
    """Yield notices from an open, seekable binary stream."""
    _reject_unusable_stream(handle)
    encoding = _detect_encoding(handle)
    handle.seek(0)
    text = io.TextIOWrapper(handle, encoding=encoding, newline="")
    # Distinct natures already warned about, so vocabulary drift costs a
    # handful of log lines rather than one per row. Scoped to the parse, not
    # the module, so it cannot suppress the warning on a later import.
    unknown_natures: set[str] = set()
    try:
        reader = csv.DictReader(text)
        # reader.line_num is the true physical line the row ended on, unlike
        # a plain row counter: a row with an embedded newline inside a quoted
        # field spans more than one physical line, and enumerate() would
        # under-report every row after it.
        try:
            for row in reader:
                notice = _row_to_notice(row, reader.line_num, on_skip, unknown_natures)
                if notice is not None:
                    yield notice
        except csv.Error as exc:
            # The one mid-stream abort the whole-file encoding scan does not
            # cover: a stray quote makes csv read to EOF looking for its
            # close and trip the 128 KiB field limit, tens of thousands of
            # rows in. Nothing can salvage the row, but a bare csv.Error names
            # neither the file nor where in it, and "field larger than field
            # limit" reads as a bmlib bug rather than a malformed download.
            # line_num is the last line read *whole*, so the fault is after it.
            raise ValueError(
                f"Malformed Retraction Watch CSV after line {reader.line_num}: {exc}."
                " The download is probably truncated or corrupt -- fetch it again."
            ) from exc
    finally:
        # A TextIOWrapper closes the stream it wraps when it is finalised.
        # Detaching leaves a caller-supplied stream open and theirs to close,
        # which is what a library taking an open handle owes its caller.
        text.detach()


def _parse_path(
    path: str | Path, on_skip: Callable[[int, str], None] | None
) -> Iterator[RetractionNotice]:
    """Yield notices from a CSV this function opens and closes itself."""
    with open(path, "rb") as handle:
        yield from _parse_stream(handle, on_skip)


def parse_retraction_watch_csv(
    source: str | Path | IO[bytes],
    *,
    on_skip: Callable[[int, str], None] | None = None,
) -> Iterator[RetractionNotice]:
    """Stream :class:`RetractionNotice` records from a Retraction Watch CSV.

    The file is read lazily rather than materialised: the Crossref export is
    65 MB and 71,306 rows, which is not worth holding in memory as dicts.

    This function is deliberately *not* itself a generator -- it validates a
    stream argument and returns one. A generator body does not run until the
    first ``next()``, which would defer the ``ValueError`` below into whatever
    consumes the iterator, typically :func:`store_retraction_notices`'s open
    transaction. Opening a *path* stays lazy, since holding the file open
    between the call and the first iteration would leak a handle for a caller
    who never iterates.

    Args:
        source: Path to the CSV, or an open **seekable binary** stream. The
            encoding is chosen by scanning the whole file for one that
            decodes it end-to-end (``utf-8-sig``, which also covers plain
            UTF-8, falling back through ``cp1252`` to ``latin-1``, which
            always succeeds), so a single bad byte anywhere in the file is
            caught before any row is yielded rather than raising mid-stream.
            Any fallback off ``utf-8-sig`` is logged at WARNING, since the
            fallbacks cannot fail and would otherwise mis-render every
            non-ASCII character in silence. The stream must support ``seek``:
            the scan rewinds to actually read the file afterwards.
        on_skip: Called as ``on_skip(line_number, reason)`` for each row that
            cannot be used -- one with no ``Record ID`` (the export ends with
            190 entirely empty rows) or no usable identifier for the retracted
            paper (5,189 rows carry only sentinels). Skips are also logged at
            DEBUG, so a short import is diagnosable rather than silent.

    Yields:
        One :class:`RetractionNotice` per usable row. A row whose
        ``RetractionNature`` this version does not recognise still yields a
        notice, typed :attr:`RetractionNature.OTHER` with the original string
        in ``raw_nature``, and is logged once per distinct unknown value at
        WARNING.

    Raises:
        ValueError: Immediately, if *source* is a stream that is not seekable
            or is a text rather than a binary one. During iteration, if the
            CSV itself is malformed (an unclosed quote, a truncated download).
    """
    if hasattr(source, "read"):
        stream: IO[bytes] = source  # type: ignore[assignment]
        _reject_unusable_stream(stream)
        return _parse_stream(stream, on_skip)
    return _parse_path(source, on_skip)


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

# Rows handed to one ``executemany``. Both drivers run the statement once per
# parameter set, so ``ON CONFLICT`` still resolves row by row and two notices
# sharing a record_id inside one chunk behave exactly as they did row-at-a-time
# -- unlike a single multi-row ``VALUES``, which PostgreSQL rejects with "ON
# CONFLICT DO UPDATE command cannot affect row a second time". What batching
# buys is one cursor per 1,000 rows instead of one per row across a 66,117-row
# import; what the bound preserves is the streaming guarantee, since only this
# many parameter tuples are ever held at once.
_UPSERT_CHUNK_ROWS = 1000


def _notice_values(notice: RetractionNotice, now: str) -> tuple[Any, ...]:
    """Return one notice's parameters, in ``_NOTICE_COLUMNS`` order."""
    return (
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

    ``notices`` is consumed lazily, in chunks of ``_UPSERT_CHUNK_ROWS``, so
    handing it :func:`parse_retraction_watch_csv`'s iterator keeps the whole
    import streaming rather than materialising 66,117 notices to write them.

    Args:
        conn: A DB-API connection (SQLite or PostgreSQL).
        notices: The notices to write. Any iterable, including a generator.

    Returns:
        The number of notices *processed*, not the number of rows left
        behind: two notices sharing a ``record_id`` within one call count as
        2, even though the second's ``ON CONFLICT`` update leaves only 1 row.
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
        chunk: list[tuple[Any, ...]] = []
        for notice in notices:
            chunk.append(_notice_values(notice, now))
            if len(chunk) >= _UPSERT_CHUNK_ROWS:
                executemany(conn, statement, chunk)
                count += len(chunk)
                chunk = []
        if chunk:
            executemany(conn, statement, chunk)
            count += len(chunk)
    return count


def _row_to_notice_model(row: Mapping[str, Any]) -> RetractionNotice:
    """Build a :class:`RetractionNotice` from a database row.

    ``RetractionNature(...)`` is the strict constructor here, deliberately,
    where the parse path's :meth:`RetractionNature.from_raw` is forgiving. The
    asymmetry is the point: a value in the CSV comes from a vocabulary bmlib
    does not own, so an unknown one must cost a row rather than the import,
    but a value in this column was written by bmlib itself, so an unknown one
    means the database was written by a version that knows a notice type this
    one does not. Mapping it to ``OTHER`` would make :func:`is_retracted` read
    it as evidence of nothing and answer "not retracted" -- a silent wrong
    answer where raising is a loud, accurate one.
    """
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

    Args:
        conn: A DB-API connection (SQLite or PostgreSQL).
        doi: The retracted paper's DOI, in any case or prefix variant.
        pmid: The retracted paper's PMID.

    Returns:
        The matching notices, newest first (undated notices last -- see the
        ``ORDER BY`` below).

    Raises:
        ValueError: If neither ``doi`` nor ``pmid`` is given, or if the ones
            given reduce to nothing usable -- blank, whitespace, a bare
            ``https://doi.org/`` prefix, or one of the export's own
            "no identifier here" sentinels (``pmid="0"``,
            ``doi="Unavailable"``). Either way there is no usable identifier,
            which is a programming error, not an empty result.
    """
    if doi is None and pmid is None:
        raise ValueError("lookup_retractions() needs a doi or a pmid")

    ph = placeholder(conn)
    clauses: list[str] = []
    params: list[str] = []
    # Normalise to the canonical stored form, then reject what that leaves as
    # unusable. Sentinels are screened here and not only at parse time: a
    # caller whose PMID column stores "0" for "absent" -- the shape 46.04% of
    # this very export has -- would otherwise query for a paper that cannot
    # exist and read the empty result as "not retracted".
    normalised_doi = _clean_identifier(_normalize_doi(doi))
    if normalised_doi:
        clauses.append(f"doi = {ph}")
        params.append(normalised_doi)
    normalised_pmid = _clean_identifier(_normalize_pmid(pmid))
    if normalised_pmid:
        clauses.append(f"pmid = {ph}")
        params.append(normalised_pmid)
    if not clauses:
        # doi/pmid were given but nothing usable survived -- e.g. "", "   ",
        # "https://doi.org/" alone, "0", "Unavailable". That is the same
        # programming error as passing None: a caller with no usable
        # identifier must not silently read an empty result as "not retracted"
        # -- the design doc's "worst failure this feature can have".
        raise ValueError(
            "lookup_retractions() needs a usable doi or pmid; got"
            f" doi={doi!r}, pmid={pmid!r}, neither of which is an identifier"
        )

    rows = fetch_all(
        conn,
        f"SELECT {', '.join(_NOTICE_COLUMNS)} FROM retraction_notices"
        f" WHERE {' OR '.join(clauses)}"
        # SQLite sorts NULLs last in a DESC order, PostgreSQL sorts them
        # first. Ordering on the IS NULL flag first pins undated notices last
        # on both, so "newest" means the same thing on either backend --
        # is_retracted() reads the first decisive row, so a backend-dependent
        # order is a backend-dependent answer.
        " ORDER BY (retraction_date IS NULL), retraction_date DESC, id DESC",
        tuple(params),
    )
    return [_row_to_notice_model(row) for row in rows]
