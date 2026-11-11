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

"""
Locate an (LLM-copied) passage inside a source text and return the exact
source span.

LLMs asked to quote a passage "verbatim" frequently change whitespace, case or
a punctuation mark. ``find_passage`` finds where the passage really is in the
source and returns the source's own text for that span, so callers can store
the original wording instead of the model's copy. Paraphrases fall below the
similarity threshold and are rejected.

Similarity is ``difflib.SequenceMatcher.ratio()`` (``2*M / (len(a)+len(b))``)
between the normalised passage and the normalised candidate window. Text is
normalised by folding typographic quotes and dashes to ASCII, decomposing
compatibility characters, dropping accents, lowercasing and collapsing
whitespace; an offset map turns a window in normalised text back into an
exact span of the original.

Search strategy:

1. Exact substring match of the normalised passage (similarity 1.0), at the
   first occurrence that neither starts nor ends inside a word or inside one
   source character; "HR 0.9" is not an exact match in "HR 0.95".
2. Size check: a passage longer than ``len(text) * (2-s)/s`` cannot reach
   threshold ``s`` against any window, so it is rejected without a search.
3. Seed voting: the passage is cut into short non-overlapping seeds, each
   seed's occurrences in the text vote for an alignment offset. A window
   scoring ``>= min_similarity`` contains at most ``2P(1-s)/s`` edit points,
   so with that many seeds plus one, at least one seed survives intact and
   votes for the true location. This holds for high thresholds such as 0.95,
   provided that seed is not skipped as too common (more than
   ``SEED_MAX_OCCURRENCES`` hits) and its location ranks among the
   ``MAX_CANDIDATE_REGIONS`` best supported. Seed length is floored at
   ``SEED_MIN_LENGTH``, so low thresholds are best-effort.
4. For each candidate location, one ``SequenceMatcher`` alignment of the
   passage against the surrounding text estimates where the passage starts
   and ends; the window edges are then refined over the few word boundaries
   near those estimates. This is a local search: the result is the best
   window found, not a guaranteed global optimum.
5. A best window reaching the threshold is still rejected when it contradicts
   the passage (#632): a one-character edit scores about ``1 - 1/L``, so no
   threshold separates a dropped "the" from a changed hazard ratio. Both are
   cut into words, numbers and symbols, aligned by ``SequenceMatcher``, and a
   token on either side that the alignment leaves unmatched rejects the match
   if it holds a digit, is a negation or is a direction or comparison word
   (:class:`PassageContradiction`). A bracketed citation marker ("[12]") left
   out of the quote whole is not tested.

Cost is dominated by step 4: per candidate location, one alignment plus a
bounded number of window scores (independent of the passage length), each a
``SequenceMatcher`` comparison that costs quadratic or more in the passage
length ``P``. The seed lookups are linear in the text length. Measured on
prose in bmlibrarian, before the module moved here (no bmlib script re-derives
it): a sentence takes a few milliseconds, a 1 kB passage about 0.1 s, a 2 kB
passage up to about 2 s.
"""

from __future__ import annotations

import bisect
import math
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache
from itertools import accumulate

# Shortest seed used for voting; shorter seeds match almost anywhere.
SEED_MIN_LENGTH = 4
# Seeds occurring more often than this carry no location signal and are skipped.
SEED_MAX_OCCURRENCES = 64
# Number of distinct candidate locations refined per search.
MAX_CANDIDATE_REGIONS = 5
# Alternating start/end refinement passes per candidate location.
REFINEMENT_PASSES = 2
# Matching blocks shorter than this are ignored when estimating where the
# passage starts and ends; short blocks also match stray text nearby.
EDGE_BLOCK_MIN_LENGTH = 2 * SEED_MIN_LENGTH
# Characters searched for word boundaries on either side of an estimated
# window edge (a few words).
EDGE_SEARCH_MARGIN = 16
# Fewest words each piece of a quote split at its ellipses must have to be
# located on its own: a shorter fragment ("Anxiety", "or colchicine") matches
# somewhere in almost any paper.
ELLIPSIS_PIECE_MIN_WORDS = 3
# The characters an ellipsis is made of: the ellipsis character, or a run of at
# least ELLIPSIS_MIN_DOTS full stops.
_ELLIPSIS_CHARS = ".\u2026"
ELLIPSIS_MIN_DOTS = 3
# Left at a piece's start or end by the ellipsis next to it ("a, [...] b"):
# trimmed, along with whitespace.
_PIECE_LEADING_TRIM = ",;:)]"
_PIECE_TRAILING_TRIM = ",;:(["

_SPACE = " "

# Typographic punctuation folded to ASCII before comparing (LLMs copy curly
# quotes as straight ones and dashes as hyphens), and invisible characters
# dropped. Other compatibility forms are handled by NFKD in fold_char().
_TYPOGRAPHIC_FOLDS = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛′´`", "'"),
        **dict.fromkeys("“”„‟″«»", '"'),
        **dict.fromkeys("‐‑‒–—―−﹘﹣－", "-"),
        # Soft hyphen, zero-width space/non-joiner/joiner, word joiner, BOM.
        **dict.fromkeys("\u00ad\u200b\u200c\u200d\u2060\ufeff", None),
    }
)
# Combining overlays that negate their base character ("≠" is "=" + U+0338);
# kept so a negated relation never folds to its positive form.
_NEGATION_MARKS = frozenset("\u0338\u20d2\u20e5")
# Bump with any change to the word lists below or to how _tokens cuts text,
# since either can turn an accepted quote into a rejected one or back;
# tests/test_passage_match.py pins it to a digest of the guard's code and data.
CONTRADICTION_RULES_VERSION = 1
# Differing tokens that negate a claim; a token ending "n't" is one too.
NEGATION_WORDS = frozenset(
    {
        "absence", "absent", "cannot", "fail", "failed", "fails", "lack",
        "lacked", "lacking", "lacks", "neither", "never", "no", "non", "none",
        "nor", "not", "nothing", "without",
    }
)  # fmt: skip
# A differing token that is a differing token of the other side with one of
# these in front negates it ("ineffective", "asymptomatic", "unlikely").
NEGATING_PREFIXES = ("a", "an", "dis", "il", "im", "in", "ir", "non", "un")
# Shortest word a negating prefix is looked for on, so "a" + "n" is not "an".
NEGATED_WORD_MIN_LENGTH = 3
# Differing tokens that state a direction or a comparison.
DIRECTION_WORDS = frozenset(
    {
        "above", "after", "before", "below", "benefit", "beneficial", "best",
        "better", "decrease", "decreased", "decreases", "decreasing", "down",
        "downward", "earlier", "elevated", "exceed", "exceeded", "exceeds",
        "faster", "fell", "fewer", "fewest", "gain", "gained", "greater",
        "greatest", "harm", "harmful", "high", "higher", "highest", "improve",
        "improved", "improvement", "improves", "increase", "increased",
        "increases", "increasing", "inferior", "larger", "largest", "later",
        "least", "less", "longer", "loss", "lost", "low", "lower", "lowered",
        "lowest", "max", "maximum", "min", "minimum", "more", "most",
        "negative", "positive", "protective", "raised", "reduce", "reduced",
        "reduces", "reducing", "reduction", "rise", "rises", "rising", "rose",
        "shorter", "slower", "smaller", "smallest", "superior", "up",
        "upward", "worse", "worsened", "worsening", "worst",
        "<", ">", "\u2264", "\u2265", "\u2266", "\u2267", "\u226a", "\u226b",
        "\u2a7d", "\u2a7e", "\u2191", "\u2193",
    }
)  # fmt: skip
# Separators joined into a number between two digits, as the token spells them:
# Lancet's middle dot is the "." a model copies it as. A comma is not one: it
# parts a thousands group, a European decimal or a list ("refs 3,4") alike, so
# "3,4" and "3, 4" cut the same, and a changed digit still leaves a digit token.
_DECIMAL_POINTS = {".": ".", "\u00b7": "."}
# Separators inside a bracketed citation marker ("[3,4]", "[5-7]").
_CITATION_MARKER_SEPARATORS = frozenset(",-")
# Distinct characters whose folding is cached (text is mostly a small alphabet).
FOLD_CACHE_SIZE = 4096


class PassageContradiction(StrEnum):
    """Why a window that reached the threshold was rejected all the same.

    Checked in this order, so a window differing in a number and a word
    reports ``NUMBER``.
    """

    NUMBER = "number"
    """A differing token holds a digit (a value, a sign, a decimal point)."""
    NEGATION = "negation"
    """A differing token negates: one of ``NEGATION_WORDS``, a word ending
    "n't", a negated relation such as "≠", or the other side's word with one
    of ``NEGATING_PREFIXES`` ("ineffective" for "effective")."""
    DIRECTION = "direction"
    """A differing token is one of ``DIRECTION_WORDS`` ("increased",
    "lower", "<", "≥" ...)."""


@dataclass(frozen=True)
class PassageMatch:
    """Result of :func:`find_passage`.

    A match is either accepted (``start``, ``end`` and ``text`` all set) or
    rejected (all three None); ``__post_init__`` enforces this.

    Attributes:
        similarity: Best similarity found, in [0, 1]. 1.0 for a passage found
            verbatim after normalisation; 0.0 when no candidate location was
            found at all, or the passage is too long to reach the threshold
            against the text. Reported even when the match is rejected.
        start: Start index of the matched span in the original text, or None.
        end: End index (exclusive) of the matched span, or None.
        text: ``original_text[start:end]`` when the match was accepted, else None.
        exact: True when the normalised passage occurs verbatim in the
            normalised text, starting and ending on whole words and whole
            source characters (only case, whitespace, accents, typographic
            variants, compatibility forms such as "²" vs "2", and invisible
            characters differ). Only ever set with similarity 1.0.
        contradiction: Why the best window was rejected although it reached
            the threshold, or None. Only ever set on a rejected match.
    """

    similarity: float
    start: int | None = None
    end: int | None = None
    text: str | None = None
    exact: bool = False
    contradiction: PassageContradiction | None = None

    def __post_init__(self) -> None:
        """Reject internally inconsistent results.

        Raises:
            ValueError: If ``similarity`` is outside [0, 1], the span fields
                are only partly set, the span is empty or inconsistent with
                ``text``, ``exact`` is set on a rejected match or with a
                similarity below 1.0, or ``contradiction`` is not a
                :class:`PassageContradiction`, is set on an accepted match or
                with a similarity of 0.0 (no window was found to contradict).
        """
        if not 0.0 <= self.similarity <= 1.0:
            raise ValueError(f"similarity must be in [0, 1], got {self.similarity}")
        if self.exact and self.similarity != 1.0:
            raise ValueError(f"an exact match has similarity 1.0, got {self.similarity}")
        if self.contradiction is not None:
            if not isinstance(self.contradiction, PassageContradiction):
                raise ValueError(
                    f"contradiction must be a PassageContradiction, got {self.contradiction!r}"
                )
            if self.similarity == 0.0:
                raise ValueError("a contradiction needs a window, but similarity is 0.0")
        span = (self.start, self.end, self.text)
        if all(field is None for field in span):
            if self.exact:
                raise ValueError("a rejected match cannot be exact")
            return
        if self.contradiction is not None:
            raise ValueError("an accepted match cannot carry a contradiction")
        if self.start is None or self.end is None or self.text is None:
            raise ValueError("start, end and text must be all set or all None")
        if not 0 <= self.start < self.end or len(self.text) != self.end - self.start:
            raise ValueError(
                f"invalid span start={self.start} end={self.end} for {len(self.text)} chars"
            )

    @property
    def matched(self) -> bool:
        """Whether a span was accepted: at or above the threshold, and not contradicting."""
        return self.text is not None


@lru_cache(maxsize=FOLD_CACHE_SIZE)
def fold_char(char: str) -> str:
    """Fold one character to the form passages are compared in.

    Typographic quotes and dashes become ASCII, compatibility characters
    are decomposed (NFKD: "ﬁ" -> "fi", "²" -> "2"), accents and other
    combining marks are dropped (so NFC and NFD spellings compare equal)
    except negation overlays ("≠" stays distinct from "="), whitespace NFKD
    produces is dropped, and the result is lowercased.

    Args:
        char: A single character.

    Returns:
        Zero or more code points; "" for a character that is dropped
        entirely (a combining mark, soft hyphen or zero-width character).
    """
    # Fold before NFKD ("´" would decompose to a bare accent) and after it
    # (NFKD turns "⁻" into U+2212 and "‴" into primes).
    decomposed = unicodedata.normalize("NFKD", char.translate(_TYPOGRAPHIC_FOLDS)).translate(
        _TYPOGRAPHIC_FOLDS
    )
    return "".join(
        code_point
        for code_point in decomposed
        if (code_point in _NEGATION_MARKS or not unicodedata.combining(code_point))
        and not code_point.isspace()
    ).lower()


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Fold ``text`` and collapse whitespace, keeping a map back to it.

    Each character is folded with :func:`fold_char`. Leading and trailing
    whitespace is dropped and every internal whitespace run becomes one space.

    Args:
        text: Original text.

    Returns:
        ``(normalised, offsets)`` where ``offsets[i]`` is the index in ``text``
        of the character that produced ``normalised[i]`` (for a collapsed
        whitespace run, its first character). Characters folded to nothing
        have no entry.
    """
    chars: list[str] = []
    offsets: list[int] = []
    pending_space = -1
    for index, char in enumerate(text):
        if char.isspace():
            if pending_space < 0 and chars:
                pending_space = index
            continue
        folded = fold_char(char)
        if not folded:
            continue
        if pending_space >= 0:
            chars.append(_SPACE)
            offsets.append(pending_space)
            pending_space = -1
        # Folding can yield several code points ("ﬁ" -> "fi"); map each back.
        for code_point in folded:
            chars.append(code_point)
            offsets.append(index)
    return "".join(chars), offsets


def occurs_once(fragment: str, text: str) -> bool:
    """Whether ``fragment`` occurs exactly once in ``text``.

    Compared the way :func:`find_passage` compares (both sides normalised by
    :func:`normalize_with_offsets`), so copies that differ only in case,
    whitespace, accents, compatibility forms, invisible characters or
    typography count as the same text. Overlapping occurrences count too, and
    so does an occurrence inside a longer word: this asks whether a fragment
    is unambiguous, not whether :func:`find_passage` would match it.

    Args:
        fragment: Text to look for (e.g. a matched span of ``text``).
        text: Text to search.

    Returns:
        True if the normalised fragment is non-empty and found exactly once.

    Raises:
        TypeError: If ``fragment`` or ``text`` is not a string.
    """
    if not isinstance(fragment, str) or not isinstance(text, str):
        raise TypeError("fragment and text must be strings")
    norm_fragment, _ = normalize_with_offsets(fragment)
    norm_text, _ = normalize_with_offsets(text)
    if not norm_fragment:
        return False
    first = norm_text.find(norm_fragment)
    return first >= 0 and norm_text.find(norm_fragment, first + 1) < 0


def split_at_ellipses(quote: str) -> list[str]:
    """The pieces of ``quote`` between its ellipses, for locating each on its own.

    A model that shortens a quote marks the cut with an ellipsis, and the
    shortened quote is then nowhere in the paper, though every piece of it may
    be. An ellipsis is the character "\u2026" or a run of three or more full
    stops, alone or mixed ("..\u2026"). Each piece is trimmed of whitespace and
    of what an ellipsis leaves next to it: a comma, semicolon or colon, an
    opening bracket at its end and a closing one at its start (the halves of
    "[...]" or "(\u2026)"). Empty pieces are dropped, so a leading or trailing
    ellipsis leaves one piece.

    Args:
        quote: A quote as the model gave it.

    Returns:
        The trimmed, non-empty pieces in order; an empty list when ``quote``
        has no ellipsis (or nothing but ellipses).
    """
    pieces: list[str] = []
    start = index = 0
    while index < len(quote):
        end = index
        while end < len(quote) and quote[end] in _ELLIPSIS_CHARS:
            end += 1
        run = quote[index:end]
        if "\u2026" in run or len(run) >= ELLIPSIS_MIN_DOTS:
            pieces.append(quote[start:index])
            start = end
        index = max(end, index + 1)
    if not pieces:
        return []
    pieces.append(quote[start:])
    return [piece for piece in map(_trim_piece, pieces) if piece]


def _trim_piece(piece: str) -> str:
    """``piece`` without the whitespace and ellipsis leftovers at its ends."""
    start, end = 0, len(piece)
    while start < end and (piece[start].isspace() or piece[start] in _PIECE_LEADING_TRIM):
        start += 1
    while end > start and (piece[end - 1].isspace() or piece[end - 1] in _PIECE_TRAILING_TRIM):
        end -= 1
    return piece[start:end]


def find_passage(passage: str, text: str, min_similarity: float) -> PassageMatch:
    """Find the best-scoring span of ``text`` it can locate for ``passage``.

    An exact match (after normalisation) is returned as the raw source span;
    an occurrence that would start or end inside a word or inside one source
    character is not exact, and the search moves on to the next occurrence
    and then to the fuzzy search. A fuzzy match starts and ends on word
    boundaries (unless no boundary lies near an estimated edge).

    Similarity 0.0 means no candidate location was found: either no fragment
    of the passage occurs in the text, or every fragment that does occurs too
    often (more than ``SEED_MAX_OCCURRENCES`` times) to locate it.

    A fuzzy match whose best window reaches the threshold but contradicts the
    passage (a number, a negation or a direction differs) is rejected with
    that window's similarity and ``contradiction`` set.

    Args:
        passage: Passage to locate (e.g. an LLM's "verbatim" quote).
        text: Source text to search.
        min_similarity: Acceptance threshold in (0, 1].

    Returns:
        A :class:`PassageMatch`; ``matched`` is True and ``text`` holds the
        original source wording when the best similarity reaches the threshold
        and the window does not contradict the passage. A similarity at or
        above the threshold therefore no longer implies ``matched``.

    Raises:
        TypeError: If ``passage`` or ``text`` is not a string.
        ValueError: If ``min_similarity`` is outside (0, 1], NaN, or a bool.
    """
    if not isinstance(passage, str) or not isinstance(text, str):
        raise TypeError("passage and text must be strings")
    # A bool is an int, so True would otherwise pass as a threshold of 1.0.
    if isinstance(min_similarity, bool) or not 0.0 < min_similarity <= 1.0:
        raise ValueError(f"min_similarity must be in (0, 1], got {min_similarity}")

    norm_passage, _ = normalize_with_offsets(passage)
    norm_text, offsets = normalize_with_offsets(text)
    if not norm_passage or not norm_text:
        return PassageMatch(similarity=0.0)

    exact_at = _exact_at(norm_passage, norm_text, offsets)
    if exact_at >= 0:
        return _accepted(text, offsets, exact_at, exact_at + len(norm_passage), 1.0, exact=True)
    if not _fits_text(len(norm_passage), len(norm_text), min_similarity):
        return PassageMatch(similarity=0.0)

    max_edits = _max_edit_points(len(norm_passage), len(norm_text), min_similarity)
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(norm_passage)

    best_similarity, best_start, best_end = 0.0, 0, 0
    for anchor in _candidate_anchors(norm_passage, norm_text, max_edits):
        similarity, start, end = _refine_window(
            matcher, norm_text, anchor, len(norm_passage), max_edits + 1, min_similarity
        )
        if similarity > best_similarity:
            best_similarity, best_start, best_end = similarity, start, end

    if best_similarity < min_similarity:
        return PassageMatch(similarity=best_similarity)
    contradiction = _contradiction(_tokens(norm_passage), _tokens(norm_text[best_start:best_end]))
    if contradiction is not None:
        return PassageMatch(similarity=best_similarity, contradiction=contradiction)
    return _accepted(text, offsets, best_start, best_end, best_similarity)


def _tokens(text: str) -> list[str]:
    """Cut normalised text into the words, numbers and symbols compared by the guard.

    A word is a run of letters (an apostrophe between two letters joins it,
    so "didn't" is one token). A number is a run of decimal digits, joined
    across a "." or middle dot between two digits (the middle dot spelled
    "."; a comma parts it, so "1,204" is three tokens), and opened by a "+"
    or "-" directly before a digit when no word character precedes it
    ("-0.5", but "18-65" is three tokens). A letter and a digit always part
    ("10mg" is "10", "mg"), so a dropped space changes nothing. Every other
    character is a token of its own, and a combining mark joins whichever
    token it follows ("=" + U+0338 is "≠").

    Args:
        text: Normalised text (single spaces, folded characters).

    Returns:
        The tokens in order.
    """
    tokens: list[str] = []
    current, kind = "", ""  # the token being built, and "letters", "number" or "symbol"
    for index, char in enumerate(text):
        before, after = text[index - 1 : index], text[index + 1 : index + 2]
        if current and _is_mark(char):
            extends, char_kind = True, kind
        elif char == _SPACE:
            extends, char_kind = False, ""
        elif char.isdecimal():
            extends, char_kind = kind == "number", "number"
        elif _is_word_char(char):
            extends, char_kind = kind == "letters", "letters"
        elif kind == "number" and char in _DECIMAL_POINTS and after.isdecimal():
            extends, char_kind, char = True, kind, _DECIMAL_POINTS[char]
        elif kind == "letters" and char == "'" and _is_letter(after):
            extends, char_kind = True, kind
        elif char in "+-" and after.isdecimal() and not (before and _is_word_char(before)):
            extends, char_kind = False, "number"
        else:
            extends, char_kind = False, "symbol"
        if extends:
            current += char
            continue
        if current:
            tokens.append(current)
        current, kind = char if char_kind else "", char_kind
    if current:
        tokens.append(current)
    return tokens


def _contradiction(passage: list[str], window: list[str]) -> PassageContradiction | None:
    """Whether a window's tokens contradict the passage's (see :func:`_tokens`).

    The tokens are aligned as the characters are (the window as ``a``, the
    passage as ``b``), and every token the alignment leaves unmatched, on
    either side, is tested, except a bracketed citation marker left unmatched
    whole (a quote dropping the source's "[12]" changes no claim).

    Args:
        passage: Tokens of the normalised passage.
        window: Tokens of the normalised window.

    Returns:
        The first :class:`PassageContradiction` (in declaration order) any
        unmatched token shows, or None.
    """
    matcher = SequenceMatcher(None, window, passage, autojunk=False)
    window_only: list[str] = []
    passage_only: list[str] = []
    window_at = passage_at = 0
    for block in matcher.get_matching_blocks():
        window_only += _without_citation_markers(window[window_at : block.a])
        passage_only += _without_citation_markers(passage[passage_at : block.b])
        window_at, passage_at = block.a + block.size, block.b + block.size
    differing = window_only + passage_only
    if any(any(char.isdecimal() for char in token) for token in differing):
        return PassageContradiction.NUMBER
    if any(
        token in NEGATION_WORDS
        or token.endswith("n't")
        or any(char in _NEGATION_MARKS for char in token)
        for token in differing
    ) or any(
        _negates(one, other) or _negates(other, one)
        for one in window_only
        for other in passage_only
    ):
        return PassageContradiction.NEGATION
    if any(token in DIRECTION_WORDS for token in differing):
        return PassageContradiction.DIRECTION
    return None


def _without_citation_markers(tokens: list[str]) -> list[str]:
    """``tokens`` less every bracketed citation marker in them ("[12]", "[3, 4]", "[5-7]").

    A marker is "[", whole numbers parted by "," or "-", then "]"; "[0.5]",
    "[-5]" and "[95% ci ...]" are not markers.

    Args:
        tokens: A run of tokens from :func:`_tokens`.

    Returns:
        The tokens outside any marker, in order.
    """
    kept: list[str] = []
    at = 0
    while at < len(tokens):
        end = _citation_marker_end(tokens, at)
        if end:
            at = end
        else:
            kept.append(tokens[at])
            at += 1
    return kept


def _citation_marker_end(tokens: list[str], at: int) -> int:
    """End of the bracketed citation marker starting at ``tokens[at]``, or 0 if none does."""
    if tokens[at] != "[":
        return 0
    at += 1
    while at + 1 < len(tokens) and tokens[at].isdecimal():
        if tokens[at + 1] == "]":
            return at + 2
        if tokens[at + 1] not in _CITATION_MARKER_SEPARATORS:
            return 0
        at += 2
    return 0


def _exact_at(passage: str, text: str, offsets: list[int]) -> int:
    """First occurrence of ``passage`` in ``text`` whose edges cut nothing.

    An occurrence that starts or ends inside a word ("HR 0.9" in "HR 0.95",
    "effective" in "ineffective") or inside one source character ("=" in
    "≠", "f" in "ﬁ") does not say what the source says, so it is skipped.

    Args:
        passage: Normalised passage.
        text: Normalised text.
        offsets: Offset map of ``text`` from :func:`normalize_with_offsets`.

    Returns:
        The start of the first occurrence with clean edges, or -1.
    """
    at = text.find(passage)
    while at >= 0:
        if not _cuts(text, offsets, at) and not _cuts(text, offsets, at + len(passage)):
            return at
        at = text.find(passage, at + 1)
    return -1


def _cuts(text: str, offsets: list[int], index: int) -> bool:
    """Whether a window edge at ``index`` of normalised ``text`` cuts into something.

    It does when the characters either side come from one source character
    (a multi-code-point fold such as "ﬁ" or "≠"), when the character after it
    is a combining mark (which belongs to the character before), or when both
    are word characters. An edge at either end of the text cuts nothing.

    Args:
        text: Normalised text.
        offsets: Offset map of ``text`` from :func:`normalize_with_offsets`.
        index: Edge position, between ``text[index - 1]`` and ``text[index]``.

    Returns:
        True if an exact match may not start or end there.
    """
    if index <= 0 or index >= len(text):
        return False
    before, after = text[index - 1], text[index]
    return (
        offsets[index - 1] == offsets[index]
        or _is_mark(after)
        or (_is_word_char(before) and _is_word_char(after))
    )


def _is_mark(char: str) -> bool:
    """Whether ``char`` is a combining mark (general category M)."""
    return unicodedata.category(char)[0] == "M"


def _is_word_char(char: str) -> bool:
    """Letters, digits and combining marks: what a word is made of."""
    return char.isalnum() or _is_mark(char)


def _negates(word: str, other: str) -> bool:
    """Whether ``word`` is ``other`` with a negating prefix ("ineffective", "effective")."""
    return len(other) >= NEGATED_WORD_MIN_LENGTH and any(
        word == prefix + other for prefix in NEGATING_PREFIXES
    )


def _is_letter(char: str) -> bool:
    """A word character that is not a decimal digit ("" is neither)."""
    return bool(char) and _is_word_char(char) and not char.isdecimal()


def _fits_text(passage_length: int, text_length: int, min_similarity: float) -> bool:
    """Whether a passage this long could reach the threshold against the text.

    A window ``W`` of the text matches at most ``W`` characters, so
    ``ratio <= 2W / (P + W) <= 2T / (P + T)``; that bound reaches ``s`` only
    when ``P <= T(2-s)/s``.

    Args:
        passage_length: Length of the normalised passage.
        text_length: Length of the normalised text.
        min_similarity: Acceptance threshold in (0, 1].

    Returns:
        False when no window of the text can score ``>= min_similarity``.
    """
    return passage_length * min_similarity <= text_length * (2 - min_similarity)


def _max_edit_points(passage_length: int, text_length: int, min_similarity: float) -> int:
    """Upper bound on edit points in a window scoring ``>= min_similarity``.

    With ``ratio = 2M / (P + W)`` and the longest admissible window
    ``W = P(2-s)/s``, unmatched characters on both sides total at most
    ``(1-s)(P+W) = 2P(1-s)/s``. Each is at most one edit point, and the
    alignment offset can drift by at most that much along the window.

    The bound is capped at ``P + T``: candidate offsets lie in ``(-P, T)``, so
    a larger bound changes no result, and the cap keeps a vanishingly small
    threshold (whose quotient is infinite) from overflowing ``math.floor``.

    Args:
        passage_length: Length of the normalised passage.
        text_length: Length of the normalised text.
        min_similarity: Acceptance threshold in (0, 1].

    Returns:
        Non-negative bound on the number of edit points.
    """
    bound = 2 * passage_length * (1 - min_similarity) / min_similarity
    return math.floor(min(bound, passage_length + text_length))


def _candidate_anchors(passage: str, text: str, max_edits: int) -> list[int]:
    """Rank text offsets where the passage plausibly starts, by seed votes.

    Args:
        passage: Normalised passage.
        text: Normalised text.
        max_edits: Bound from :func:`_max_edit_points`; also the clustering radius.

    Returns:
        Up to ``MAX_CANDIDATE_REGIONS`` offsets (possibly negative), strongest
        first, more than ``max_edits`` apart.
    """
    seed_length = max(SEED_MIN_LENGTH, len(passage) // (max_edits + 1))
    votes: Counter[int] = Counter()
    for seed_start in range(0, len(passage) - seed_length + 1, seed_length):
        positions = _occurrences(text, passage[seed_start : seed_start + seed_length])
        for position in positions:
            votes[position - seed_start] += 1
    if not votes:
        return []

    # Indels shift the offset by up to max_edits, splitting one location's
    # votes over neighbouring offsets: score each offset by its neighbourhood.
    offsets = sorted(votes)
    cumulative = [0, *accumulate(votes[offset] for offset in offsets)]

    def neighbourhood(offset: int) -> int:
        """Total votes within ``max_edits`` of ``offset``."""
        low = bisect.bisect_left(offsets, offset - max_edits)
        high = bisect.bisect_right(offsets, offset + max_edits)
        return cumulative[high] - cumulative[low]

    ranked = sorted(offsets, key=lambda offset: (-neighbourhood(offset), -votes[offset]))
    chosen: list[int] = []
    for offset in ranked:
        if all(abs(offset - kept) > max_edits for kept in chosen):
            chosen.append(offset)
            if len(chosen) == MAX_CANDIDATE_REGIONS:
                break
    return chosen


def _occurrences(text: str, seed: str) -> list[int]:
    """Positions of ``seed`` in ``text``; empty if it is too common to locate.

    Args:
        text: Normalised text.
        seed: Substring to find.

    Returns:
        All start positions, or ``[]`` when there are more than
        ``SEED_MAX_OCCURRENCES``.
    """
    positions: list[int] = []
    position = text.find(seed)
    while position >= 0:
        if len(positions) == SEED_MAX_OCCURRENCES:
            return []
        positions.append(position)
        position = text.find(seed, position + 1)
    return positions


def _refine_window(
    matcher: SequenceMatcher,
    text: str,
    anchor: int,
    passage_length: int,
    slack: int,
    min_similarity: float,
) -> tuple[float, int, int]:
    """Choose the best-scoring window near an anchor offset.

    Aligns the passage against ``text`` around the anchor (``slack`` on either
    side) to estimate where it starts and ends, then, starting from the word
    boundaries nearest those estimates, alternately moves the start, then the
    end, to the best nearby word boundary. Refinement is skipped when the
    alignment matches too few characters for any window to reach
    ``min_similarity``; the estimated window's score is then returned for
    diagnostics.

    Args:
        matcher: SequenceMatcher with the normalised passage set as ``seq2``.
        text: Normalised text.
        anchor: Candidate start offset (may be negative).
        passage_length: Length of the normalised passage.
        slack: Radius around the anchored window searched for the passage.
        min_similarity: Acceptance threshold in (0, 1].

    Returns:
        ``(similarity, start, end)`` of the best window, in normalised indices.
    """
    length = len(text)
    starts, ends, aligned = _edge_estimates(matcher, text, anchor, passage_length, slack)
    start_candidates = _near_boundaries(_word_starts, text, starts, 0, length - 1)
    end_candidates = _near_boundaries(_word_ends, text, ends, 1, length)
    best_start = _closest(start_candidates, starts[0])
    viable_ends = [end for end in end_candidates if end > best_start]
    best_end = _closest(viable_ends or [min(best_start + passage_length, length)], ends[1])
    best = _similarity(matcher, text, best_start, best_end, floor=-1.0)
    # A window matching M characters scores at most 2M / (P + M). ``aligned``
    # is difflib's count for the wider region (a heuristic, not the longest
    # common subsequence), so this skips regions that are almost surely too
    # dissimilar; the exhaustive-search test in test_passage_match guards it.
    if 2 * aligned < min_similarity * (passage_length + aligned):
        return best, best_start, best_end
    for _ in range(REFINEMENT_PASSES):
        for start in start_candidates:
            if start < best_end:
                score = _similarity(matcher, text, start, best_end, floor=best)
                if score > best:
                    best, best_start = score, start
        for end in end_candidates:
            if end > best_start:
                score = _similarity(matcher, text, best_start, end, floor=best)
                if score > best:
                    best, best_end = score, end
    return best, best_start, best_end


def _near_boundaries(
    boundaries: Callable[[str, int, int], set[int]],
    text: str,
    estimates: tuple[int, int],
    lowest: int,
    highest: int,
) -> list[int]:
    """Word boundaries within ``EDGE_SEARCH_MARGIN`` of either estimate.

    Args:
        boundaries: :func:`_word_starts` or :func:`_word_ends`.
        text: Normalised text.
        estimates: Low and high estimate of the edge.
        lowest: Smallest valid edge index.
        highest: Largest valid edge index.

    Returns:
        Sorted candidate edges; the clamped estimates themselves when no word
        boundary lies near them (e.g. inside one very long token).
    """
    found: set[int] = set()
    for estimate in estimates:
        found |= boundaries(text, estimate - EDGE_SEARCH_MARGIN, estimate + EDGE_SEARCH_MARGIN)
    if not found:
        found = {min(max(estimate, lowest), highest) for estimate in estimates}
    return sorted(found)


def _closest(candidates: list[int], target: int) -> int:
    """The candidate nearest ``target`` (the lower one on ties)."""
    return min(candidates, key=lambda candidate: (abs(candidate - target), candidate))


def _edge_estimates(
    matcher: SequenceMatcher,
    text: str,
    anchor: int,
    passage_length: int,
    slack: int,
) -> tuple[tuple[int, int], tuple[int, int], int]:
    """Estimate where the passage starts and ends near an anchor.

    The passage is aligned against ``text`` from ``slack`` before the anchor
    to ``slack`` past the anchored end. Its start lies between where the
    first substantial matching block begins and where the passage would begin
    if its unmatched prefix were substitutions (inserted words put it at the
    former, substituted ones at the latter); likewise for the end.

    Args:
        matcher: SequenceMatcher with the normalised passage set as ``seq2``.
        text: Normalised text.
        anchor: Candidate start offset (may be negative).
        passage_length: Length of the normalised passage.
        slack: Radius around the anchored window to align against.

    Returns:
        ``((start_low, start_high), (end_low, end_high), aligned)``: edge
        estimates in normalised text indices (not clamped to the text; both
        pairs are the anchored window edges when the alignment has no
        substantial block), and the number of passage characters aligned.
    """
    low = max(anchor - slack, 0)
    high = min(anchor + passage_length + slack, len(text))
    matcher.set_seq1(text[low:high])
    all_blocks = matcher.get_matching_blocks()
    aligned = sum(block.size for block in all_blocks)
    blocks = [block for block in all_blocks if block.size >= EDGE_BLOCK_MIN_LENGTH]
    if not blocks:
        anchored_end = anchor + passage_length
        return (anchor, anchor), (anchored_end, anchored_end), aligned
    first, last = blocks[0], blocks[-1]
    first_at = low + first.a
    last_end = low + last.a + last.size
    unmatched_tail = passage_length - (last.b + last.size)
    return (first_at - first.b, first_at), (last_end, last_end + unmatched_tail), aligned


def _similarity(matcher: SequenceMatcher, text: str, start: int, end: int, floor: float) -> float:
    """Ratio of the passage against ``text[start:end]``, pruned by upper bounds.

    Args:
        matcher: SequenceMatcher with the normalised passage set as ``seq2``.
        text: Normalised text.
        start: Window start.
        end: Window end (exclusive).
        floor: Score to beat; returns early with ``floor`` when the cheap
            upper bounds show the window cannot exceed it.

    Returns:
        The window's similarity, or ``floor`` if it cannot beat it.
    """
    matcher.set_seq1(text[start:end])
    if matcher.real_quick_ratio() <= floor or matcher.quick_ratio() <= floor:
        return floor
    return matcher.ratio()


def _word_starts(text: str, low: int, high: int) -> set[int]:
    """Indices in ``[low, high]`` where a word starts in normalised ``text``."""
    low, high = max(low, 0), min(high, len(text) - 1)
    return {
        index
        for index in range(low, high + 1)
        if text[index] != _SPACE and (index == 0 or text[index - 1] == _SPACE)
    }


def _word_ends(text: str, low: int, high: int) -> set[int]:
    """Exclusive indices in ``[low, high]`` where a word ends in normalised ``text``."""
    low, high = max(low, 1), min(high, len(text))
    return {
        index
        for index in range(low, high + 1)
        if text[index - 1] != _SPACE and (index == len(text) or text[index] == _SPACE)
    }


def _accepted(
    original: str,
    offsets: list[int],
    start: int,
    end: int,
    similarity: float,
    exact: bool = False,
) -> PassageMatch:
    """Map a normalised window back onto the original text.

    Args:
        original: Original (un-normalised) text.
        offsets: Offset map from :func:`normalize_with_offsets`.
        start: Window start in normalised indices.
        end: Window end (exclusive) in normalised indices; ``end > start``.
        similarity: Similarity of the window.
        exact: Whether the window is a verbatim (normalised) match.

    Returns:
        An accepted :class:`PassageMatch` spanning whole original characters.
    """
    original_start = offsets[start]
    # Extend up to the next kept character so marks folded to nothing (an
    # NFD accent after the last letter) stay in the span; a window ending
    # inside a multi-code-point fold ("ﬁ") keeps the whole character.
    next_kept = offsets[end] if end < len(offsets) else len(original)
    original_end = max(offsets[end - 1] + 1, next_kept)
    # A window edge on a collapsed space maps to raw whitespace: trim it.
    span = original[original_start:original_end]
    original_start += len(span) - len(span.lstrip())
    original_end -= len(span) - len(span.rstrip())
    return PassageMatch(
        similarity=similarity,
        start=original_start,
        end=original_end,
        text=original[original_start:original_end],
        exact=exact,
    )
