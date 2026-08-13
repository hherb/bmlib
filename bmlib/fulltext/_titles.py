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

"""Deciding whether a PDF's metadata title is the article's title (issue #56).

Real PDFs carry junk in ``/Title`` — ``"Microsoft Word - manuscript.docx"``,
``"untitled"``, a typesetter's job number, the source file's name — and
``SectionSegmenter`` used to return any non-empty value there verbatim, so
junk beat a perfectly good large-font first-page line.

The rule here is not "does this look like junk" but **"does the document
itself say this"**: a junk title has one property every shape of it shares,
whether or not anyone sampled that shape — it is not printed in the document,
and a real title is. A reject-list survives only as a backstop for junk that
*is* printed on page 1, and every member of it was earned from a measured
corpus (``scripts/sample_pdf_metadata_titles.py``,
``tests/data/pdf_metadata_titles.json``). **Run that sampler before changing
it.**

Private, and stdlib-only: nothing outside ``bmlib.fulltext`` has asked for
this, and ``fulltext`` must import on a core install (issue #64).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# A hyphen — in any of its Unicode spellings, including the soft hyphen —
# immediately before a line break is typesetting, not spelling: the word
# continues on the next line. Anchored on the break, so an ordinary
# mid-line hyphen is left alone and `dose-response` stays two tokens.
_LINE_BREAK_HYPHEN_RE = re.compile("[-‐‑­]\\s*\n\\s*")


def normalise(text: str) -> str:
    """Reduce *text* to the form both sides of the corroboration test compare in.

    Line-break hyphenation is closed up first; the text is then decomposed to
    NFKD, its combining marks dropped, lowercased, and reduced to its
    ``[a-z0-9]+`` runs joined by single spaces. That absorbs the differences
    which separate a correct metadata title from its printed form — case, the
    terminal period metadata usually drops, en-dash versus hyphen, ligatures,
    diacritics, and the line break a wrapped title carries — while keeping
    every difference that changes what the string says.

    Args:
        text: A metadata title, or a page's text with newlines.

    Returns:
        The normalised form, possibly empty when *text* holds no word
        characters at all.
    """
    closed = _LINE_BREAK_HYPHEN_RE.sub("", text)
    decomposed = unicodedata.normalize("NFKD", closed)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_TOKEN_RE.findall(stripped.lower()))


def looks_like_junk(title: str, metadata: Mapping[str, Any]) -> bool:
    """Whether *title* is a known junk shape, regardless of what page 1 says.

    The backstop to corroboration, for junk the document *does* print — a
    running header, a job number repeated in the footer. Deliberately empty
    of guesses: every member is earned from
    ``tests/data/pdf_metadata_titles.json`` under the rule that it must
    reject at least one measured junk title corroboration accepted, and no
    measured good one. See :mod:`bmlib.fulltext._titles`'s docstring.

    Args:
        title: The metadata title, stripped.
        metadata: The full metadata dict, so a member may consult ``creator``,
            ``producer`` or ``file_path``.

    Returns:
        ``True`` when the title matches a measured junk shape.
    """
    return False


def accepted_metadata_title(metadata: Mapping[str, Any], page_one_text: str) -> str | None:
    """The PDF's own metadata title, where the document corroborates it.

    Args:
        metadata: The converter's metadata dict; ``title`` is read, and
            :func:`looks_like_junk` may read its neighbours.
        page_one_text: Page 1's text, newline-separated. Empty when page 1
            carries none.

    Returns:
        The metadata title as the document gives it, stripped of surrounding
        whitespace — or ``None`` when it is blank, holds no word characters,
        is a known junk shape, or is absent from a page that had text to
        check it against.

        A page with **no** text accepts the title: corroboration is then a
        test that could not be run, not one that failed, and rejecting would
        blank the title of every image-only scan — where the metadata is the
        only title signal there is. The backstop still applies there, so an
        unrunnable check is not a free pass.
    """
    raw = metadata.get("title")
    title = str(raw).strip() if raw else ""
    if not title:
        return None
    if looks_like_junk(title, metadata):
        return None
    wanted = normalise(title)
    if not wanted:
        return None
    page = normalise(page_one_text)
    if not page:
        return title
    return title if wanted in page else None
