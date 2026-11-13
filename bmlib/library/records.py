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

"""An installation's JSON account of one paper: who wrote it, what the paper is.

Rust's ``bmlib::library::record`` is the twin; both write the same bytes.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, NoReturn, TypeGuard

from bmlib._strict_json import strict_json_loads
from bmlib.library.constants import DIGEST_PREFIX_HEX, RECORD_FORMAT, RECORD_VERSION
from bmlib.library.errors import LibraryError
from bmlib.library.ids import Installation, PaperInfo, SourceKind, canonical_ids

_HEX = frozenset("0123456789abcdef")
_U32_MAX = 2**32 - 1


@dataclass(frozen=True)
class FileEntry:
    """One source file an installation saved in the paper's folder."""

    sha256: str  # 64 lowercase hex digits
    kind: SourceKind
    name: str  # ``{kind}-{first 12 hex of sha256}.{extension}``
    origin: str
    size: int
    saved_at: str


@dataclass(frozen=True)
class Record:
    """An installation's account of one paper."""

    installation: Installation
    paper: PaperInfo
    files: tuple[FileEntry, ...]


class RecordProblem(Exception):
    """Why a record was not accepted.

    ``kind`` is ``"unreadable"`` (``reason`` says why) or ``"newer"`` (``version`` is the
    version a newer bmlib wrote).
    """

    def __init__(self, kind: str, reason: str = "", version: int = 0) -> None:
        """Make the problem; the message is *reason*, or one naming *version*."""
        super().__init__(reason or f"record version {version} is newer than this bmlib reads")
        self.kind = kind
        self.reason = reason
        self.version = version


def _bad(reason: str) -> NoReturn:
    """Refuse the record as ``unreadable`` with *reason*."""
    raise RecordProblem("unreadable", reason)


def _is_int(v: Any) -> TypeGuard[int]:
    """True for an ``int`` that is not a ``bool`` (JSON's ``true`` is no number)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _whole(v: Any) -> int | None:
    """An integer value, or ``None``. Negative zero (``-0.0``) is 0, as in Rust, where
    serde_json reads ``-0`` as that float."""
    if _is_int(v):
        return v
    if isinstance(v, float) and v == 0.0 and math.copysign(1.0, v) < 0:
        return 0
    return None


def _string(obj: dict[str, Any], key: str, what: str) -> str:
    """The string at ``obj[key]``; anything else is refused, naming it as ``{what}.{key}``."""
    v = obj.get(key)
    if not isinstance(v, str):
        _bad(f"{what}.{key} must be a string")
    return v


def _opt_string(obj: dict[str, Any], key: str) -> str | None:
    """A string, or null/absent as ``None``; anything else is refused."""
    v = obj.get(key)
    if v is not None and not isinstance(v, str):
        _bad(f"paper.{key} must be a string or null")
    return v


def _parse_paper(paper: Any) -> PaperInfo:
    """The record's ``paper`` object: identifiers that must already be canonical, and an
    optional title, year (within ``i32``) and first author."""
    if not isinstance(paper, dict):
        _bad("paper must be an object")
    stored = (_opt_string(paper, "pmid"), _opt_string(paper, "doi"), _opt_string(paper, "pmcid"))
    try:
        ids = canonical_ids(*stored)
    except LibraryError as err:
        _bad(str(err))
    if (ids.pmid, ids.doi, ids.pmcid) != stored:
        _bad("paper identifiers are not canonical")
    year = _whole(paper.get("year"))
    if paper.get("year") is not None and (year is None or not -(2**31) <= year < 2**31):
        _bad("paper.year must be an integer or null")
    return PaperInfo(
        ids=ids,
        title=_opt_string(paper, "title"),
        year=year,
        first_author=_opt_string(paper, "first_author"),
    )


def _parse_file(file: Any) -> FileEntry:
    """One entry of ``files``: a 64-hex digest, a known kind, the name those two give, an
    origin, a non-negative size (within ``u64``) and the time it was saved."""
    if not isinstance(file, dict):
        _bad("a file entry must be an object")
    sha = _string(file, "sha256", "file")
    if len(sha) != 64 or not set(sha) <= _HEX:
        _bad("file.sha256 must be 64 lowercase hex digits")
    kind_name = _string(file, "kind", "file")
    kind = SourceKind.parse(kind_name)
    if kind is None:
        _bad(f"unknown file kind {kind_name!r}")
    name = _string(file, "name", "file")
    if name != f"{kind.value}-{sha[:DIGEST_PREFIX_HEX]}.{kind.extension()}":
        _bad(f"file name {name!r} does not match its kind and digest")
    size = _whole(file.get("size"))
    if size is None or not 0 <= size < 2**64:
        _bad("file.size must be a non-negative integer")
    return FileEntry(
        sha256=sha,
        kind=kind,
        name=name,
        origin=_string(file, "origin", "file"),
        size=size,
        saved_at=_string(file, "saved_at", "file"),
    )


def parse_record(text: str) -> Record:
    """Read a record from its JSON text.

    Unknown fields are ignored. The format is checked first, then the version (a newer
    one is ``newer`` whatever else it holds), then installation, identifiers and files.

    Raises:
        RecordProblem: ``unreadable`` with the reason, or ``newer``.
    """
    try:
        root = strict_json_loads(text)
    except ValueError as err:  # JSONDecodeError is one
        _bad(f"not JSON: {err}")
    if not isinstance(root, dict):
        _bad("a record must be a JSON object")
    if root.get("format") != RECORD_FORMAT:
        _bad(f"format is not {RECORD_FORMAT!r}")
    version = root.get("version")
    if not _is_int(version) or not 1 <= version <= _U32_MAX:
        _bad("version must be a positive integer")
    if version > RECORD_VERSION:
        raise RecordProblem("newer", version=version)
    inst = root.get("installation")
    if not isinstance(inst, dict):
        _bad("installation must be an object")
    try:
        installation = Installation.new(
            _string(inst, "app", "installation"),
            _string(inst, "id", "installation"),
            _string(inst, "label", "installation"),
        )
    except LibraryError as err:
        _bad(str(err))
    paper = _parse_paper(root.get("paper"))
    raw = root.get("files")
    if not isinstance(raw, list):
        _bad("files must be a list")
    files: list[FileEntry] = []
    for item in raw:
        entry = _parse_file(item)
        if any(o.sha256 == entry.sha256 for o in files):
            _bad(f"file {entry.sha256} is listed twice")
        files.append(entry)
    return Record(installation, paper, tuple(files))


def render_record(record: Record) -> str:
    """The record as JSON text: two-space indent, fixed key order, ``null`` for absent
    optionals, a trailing newline. Rust's ``render_record`` writes the same bytes."""
    p = record.paper
    out = {
        "format": RECORD_FORMAT,
        "version": RECORD_VERSION,
        "installation": {
            "app": record.installation.app,
            "id": record.installation.id,
            "label": record.installation.label,
        },
        "paper": {
            "pmid": p.ids.pmid,
            "doi": p.ids.doi,
            "pmcid": p.ids.pmcid,
            "title": p.title,
            "year": p.year,
            "first_author": p.first_author,
        },
        "files": [
            {
                "sha256": f.sha256,
                "kind": f.kind.value,
                "name": f.name,
                "origin": f.origin,
                "size": f.size,
                "saved_at": f.saved_at,
            }
            for f in record.files
        ],
    }
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"
