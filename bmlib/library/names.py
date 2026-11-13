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

"""Paper-folder names: readable, safe on every file system, bounded."""

from __future__ import annotations

import unicodedata

from bmlib.library.constants import (
    MAX_AUTHOR_CHARS_IN_NAME,
    MAX_FOLDER_NAME_BYTES,
    MAX_TITLE_CHARS_IN_NAME,
)
from bmlib.library.ids import PaperInfo, _is_control, is_bidi_control

_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_FORBIDDEN = frozenset('<>:"/\\|?*')


def _cut_bytes(s: str, limit: int) -> str:
    """The longest prefix of ``s`` that is at most ``limit`` UTF-8 bytes."""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _cut_title(s: str, limit: int) -> str:
    """``s`` cut to ``limit`` characters at the last space at or before the limit."""
    if len(s) <= limit:
        return s
    end = s[: limit + 1].rfind(" ")
    return s[: limit if end < 0 else end]


def _sanitise(s: str) -> str:
    """NFC; forbidden, control and bidi characters to spaces; one space between words;
    no leading dots or spaces, no trailing spaces.
    """
    spaced = "".join(
        " " if c in _FORBIDDEN or _is_control(c) or is_bidi_control(c) else c
        for c in unicodedata.normalize("NFC", s)
    )
    return " ".join(spaced.split()).lstrip(". ")


def folder_name(info: PaperInfo) -> str:
    """The folder name for a paper: ``year author – title (ID)``.

    At most ``MAX_FOLDER_NAME_BYTES`` bytes, safe on macOS, Windows and Linux. The length is
    fitted first; a Windows device name (``con.x``) in the fitted name then gets a ``_``
    prefix, for which one byte of the budget is kept.
    """
    return _named(info, 1)


def folder_name_numbered(info: PaperInfo, n: int) -> str:
    """The folder name for the ``n``th paper to want :func:`folder_name`'s name.

    The same name ending `` (n)``, still at most ``MAX_FOLDER_NAME_BYTES`` bytes (the body is
    cut to make room). ``n`` below 2 gives :func:`folder_name` itself.
    """
    return _named(info, n)


def _named(info: PaperInfo, n: int) -> str:
    """The shared fitting code behind both; ``n`` of 1 or less adds no number."""
    ids = info.ids
    if ids.pmid:
        ident = f"PMID {ids.pmid}"
    elif ids.doi:
        ident = f"DOI {ids.doi}"
    elif ids.pmcid:
        ident = f"PMCID {ids.pmcid}"
    else:
        ident = "no identifier"
    year = str(info.year) if info.year is not None and 1000 <= info.year <= 9999 else ""
    # NFC before any cut by characters, so NFD input (Swift, PDFKit) fits the same.
    author = unicodedata.normalize("NFC", info.first_author or "").strip()
    author = author[:MAX_AUTHOR_CHARS_IN_NAME]
    head = " ".join(p for p in (year, author) if p)
    title = unicodedata.normalize("NFC", info.title or "").strip()
    title = _cut_title(title, MAX_TITLE_CHARS_IN_NAME).rstrip()
    body = _sanitise(f"{head} – {title}" if head and title else head or title)
    ident = _sanitise(ident)
    suffix = f"({ident})" if not body else f" ({ident})"
    if n > 1:
        suffix += f" ({n})"
    # Length first, then the reserved-name check on the final name: a fit that
    # is reserved is redone one byte shorter so the "_" still keeps it in budget.
    name = _fit(body, suffix, MAX_FOLDER_NAME_BYTES)
    if is_reserved(name):
        return "_" + _fit(body, suffix, MAX_FOLDER_NAME_BYTES - 1)
    return name


def is_reserved(name: str) -> bool:
    """True when the part before the first dot is a Windows device name."""
    return name.split(".")[0].upper() in _RESERVED


def _fit(body: str, suffix: str, limit: int) -> str:
    """``body + suffix`` within ``limit`` bytes.

    The body is cut at a character boundary; when the suffix alone does not fit, the whole
    name is.
    """
    name = body + suffix
    if len(name.encode("utf-8")) <= limit:
        return name
    room = limit - len(suffix.encode("utf-8"))
    if room >= 0:
        kept = _cut_bytes(body, room).rstrip()
        return kept + (suffix if kept else suffix.lstrip())
    return _cut_bytes(name, limit).rstrip(". ")
