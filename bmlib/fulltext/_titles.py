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
# A hyphen immediately before a line break is typesetting, not spelling: the
# word continues on the next line. Anchored on the break, so an ordinary
# mid-line hyphen is left alone and `dose-response` stays two tokens.
#
# The four spellings are written as escapes rather than as themselves. Two of
# them — U+00AD SOFT HYPHEN especially — are invisible or near-invisible in a
# source file, and a character class whose members cannot be seen is one a
# later reader deletes as a duplicate of the plain `-`.
_LINE_BREAK_HYPHEN_RE = re.compile(
    "["
    "\u002d"  # HYPHEN-MINUS, the ordinary one
    "\u2010"  # HYPHEN
    "\u2011"  # NON-BREAKING HYPHEN
    "\u00ad"  # SOFT HYPHEN
    "]"
    r"\s*\n\s*"
)


#: A line holding nothing but a number is a line number or a page number.
#:
#: Preprint servers number the lines of a submitted manuscript, and PyMuPDF
#: reports each number as its own line — *between* the lines of the title it
#: sits beside. Joining them into the page text splices digits into the middle
#: of the title, so a metadata title reading "Coordinated leaf hydraulic
#: thresholds maintain virtually null stomatal safety margins…" is not
#: contained in a page reading "…virtually null 1 stomatal safety margins… 2
#: and nutrient induced…", and a perfectly good title is rejected.
#:
#: Measured: this was the *only* wrongly rejected title in 130 matched rows,
#: and it is a whole class of document rather than one file. Dropping these
#: lines can only remove text from the page side of a containment test, and a
#: line that is only a number is not part of any title.
_LINE_NUMBER_RE = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.MULTILINE)


def _page_text_for_matching(page_one_text: str) -> str:
    """Page 1's text with its line numbering removed."""
    return _LINE_NUMBER_RE.sub("", page_one_text)


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


#: A title of fewer than this many words is not an article title.
#:
#: **Retained as defence-in-depth, not as a member the corpus currently
#: earns.** It was earned by exactly one row of the 235 — ``"Nepal Journ"``, a
#: journal name truncated mid-word in a running header, which page 1 really
#: does print — under the membership rule in :func:`looks_like_junk`. Anchored
#: containment now rejects that row on its own (``journ`` is not ``journal``),
#: so re-measured over the corpus this threshold rescues **nothing**
#: corroboration does not already reject, and it would not ship today under
#: the rule that first admitted it.
#:
#: What it still covers, and anchoring does not: a short but *complete* junk
#: string that page 1 genuinely prints — a footer reading ``"Layout 1"``.
#: Anchoring only catches junk cut mid-token. The corpus shows no such row, so
#: that cover is argued rather than measured; it is stated here so the next
#: maintainer is not misled into thinking the threshold is load-bearing.
#:
#: Two things this does not claim. Short article titles exist in the wild
#: ("Malaria", "Retraction"), and the corpus contains none — the shortest
#: genuine title measured is five words — so the false-positive risk here is
#: *bounded* by the corpus rather than disproven. And a title rejected here is
#: not lost: it falls through to the font-size heuristic, which for a genuinely
#: short title printed large returns it anyway. Both are why the threshold is
#: low rather than "tuned".
_MIN_TITLE_WORDS = 3


def looks_like_junk(title: str, metadata: Mapping[str, Any]) -> bool:
    """Whether *title* is a known junk shape, regardless of what page 1 says.

    The backstop to corroboration, for junk the document *does* print — a
    running header, a job number repeated in the footer. Every member is
    earned from ``tests/data/pdf_metadata_titles.json`` under the rule that it
    must reject at least one measured junk title corroboration accepted, and
    no measured good one; a shape the corpus never showed does not become a
    member however obvious it looks, which is the reject-list this design
    exists to avoid. **Re-run the sampler before changing this.**

    Consulted *before* page 1 (see :func:`accepted_metadata_title`), so a
    document that cannot be corroborated at all does not thereby get a free
    pass for a shape already known to be junk.

    Args:
        title: The metadata title, stripped.
        metadata: The full metadata dict, so a member may consult ``creator``,
            ``producer`` or ``file_path``. Unused today, and the measurement
            says why: the junk clusters hard by ``creator`` — 14 of bioRxiv's
            16 junk titles carry ``creator: "Appligent AppendPDF Pro 5.5"``,
            and not one row with that value is a good title — but every one of
            those 14 is *already* rejected by corroboration, so a member on it
            would clear no row and fails the rule above on its own terms.
            (``producer`` is not the signal: ``Appligent`` appears in it zero
            times.)

    Returns:
        ``True`` when the title matches a measured junk shape.
    """
    del metadata  # Reserved for a future member; see Args.
    return len(normalise(title).split()) < _MIN_TITLE_WORDS


def accepted_metadata_title(metadata: Mapping[str, Any], page_one_text: str | None) -> str | None:
    """The PDF's own metadata title, where the document corroborates it.

    Args:
        metadata: The converter's metadata dict; ``title`` is read, and
            :func:`looks_like_junk` may read its neighbours.
        page_one_text: Page 1's text, newline-separated. ``""`` when page 1
            was read and carried no text — an image-only scan. ``None`` when
            page 1 could not be read *at all*: its extraction raised, or the
            document has no pages.

    Returns:
        The metadata title as the document gives it, stripped of surrounding
        whitespace — or ``None`` when it is blank, holds no word characters,
        is a known junk shape, could not be checked because of a fault, or is
        absent from a page that had text to check it against.

        The two uncheckable cases are deliberately opposite, and the
        distinction is the one the samplers draw between an unmeasured probe
        and a failed one:

        * A page **read as empty** accepts the title. Corroboration is then a
          test that could not be run, and rejecting would blank the title of
          every image-only scan — where the metadata is the only title signal
          there is.
        * A page that **could not be read** rejects it. That is a test that
          failed, not one that was inapplicable, and a fault is not evidence:
          it is precisely where there is least reason to trust anything the
          file claims about itself.

        The backstop applies in both, so an unrunnable check is never a free
        pass.
    """
    raw = metadata.get("title")
    title = str(raw).strip() if raw else ""
    if not title:
        return None
    if looks_like_junk(title, metadata):
        return None
    wanted = normalise(title)
    if not wanted:
        # Masked twice over, and still load-bearing in one case. The backstop
        # rejects a zero-word title before this is reached, and anchoring
        # would reject it after — `f" {''} "` is two spaces, which cannot
        # occur in a page joined by single ones.
        #
        # What neither covers: an empty-normalising title against a page that
        # is *also* empty. That falls to the unrunnable-check path below,
        # which hands the title back — so without this guard an image-only
        # scan whose /Title is "###" gets "###" as its title. The backstop is
        # documented as revisable and anchoring is a property of one
        # expression, so this stays rather than resting on either.
        return None
    if page_one_text is None:
        # The check failed rather than being inapplicable — see Returns.
        return None
    page = normalise(_page_text_for_matching(page_one_text))
    if not page:
        return title
    # Both sides padded, so containment is anchored to whole tokens.
    #
    # `normalise` exists to produce tokens, and a bare `wanted in page`
    # throws those boundaries away again — matching inside a word, in the
    # *accepting* direction. Two shapes it let through: a title whose first
    # token is a suffix of a page token ("art in medicine" corroborated by a
    # page printing "A heart in medicine"), and — the one that matters — a
    # `/Title` truncated mid-word, which producers emit routinely and which
    # was then returned verbatim *and* beat the font-size fallback that would
    # have recovered the whole line.
    #
    # Measured: anchoring changes no verdict on any of the 235 corpus rows,
    # so this costs nothing already accounted for. The corpus holds no
    # `truncated` row, so that class is unmeasured rather than shown safe —
    # which is why the rule is tightened here rather than left to the corpus.
    return title if f" {wanted} " in f" {page} " else None
