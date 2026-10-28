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

"""Whether a canonical text looks like a whole paper rather than its abstract.

A publisher's landing page converts as cleanly as its full-text page, so a
converter cannot tell the two apart; this check reads the result, from any
format. It is a heuristic and **unmeasured**: :data:`MIN_BODY_WORDS` and the
heading rule are a starting point without a corpus behind them, and a case
report or letter with no IMRaD headings fails it.

Each heading is classified by the PDF segmenter's pattern table
(:func:`bmlib.fulltext.segmenter.match_section_type`). A section under an
*Abstract* or *References* heading (at a deeper level, up to the next section
at that heading's level or above) belongs to it, so a structured abstract's
own *Methods* and *Results* are not the paper's.
"""

from __future__ import annotations

from dataclasses import dataclass

from bmlib.fulltext.canonical.model import CanonicalText, is_white_space
from bmlib.fulltext.models import SectionType
from bmlib.fulltext.segmenter import match_section_type

#: The fewest words a paper's body (its blocks outside the abstract and the
#: references) must hold. Unmeasured: a structured abstract runs to some 400
#: words and a research letter to about 1,000; a longer abstract page or a
#: shorter paper is misjudged.
MIN_BODY_WORDS = 1000

#: Headings whose sections, and those nested under them, are not the body.
_NOT_BODY = frozenset({SectionType.ABSTRACT, SectionType.REFERENCES})

#: The section that must be found, and the ones of which one must be too.
_REQUIRED = SectionType.METHODS
_ONE_OF = frozenset({SectionType.RESULTS, SectionType.DISCUSSION})


@dataclass(frozen=True)
class FullTextAssessment:
    """What :func:`assess_full_text` found, and its verdict.

    ``section_types`` are the classified headings outside the abstract and the
    references (those two themselves included), each once, in document order;
    ``body_words`` counts the words of the blocks outside them. The verdict,
    :attr:`is_full_text`, is computed from the two, so no assessment can carry
    one they contradict.
    """

    section_types: tuple[SectionType, ...]
    body_words: int

    @property
    def is_full_text(self) -> bool:
        """Whether the text looks like a whole paper (see :func:`assess_full_text`)."""
        return (
            _REQUIRED in self.section_types
            and any(k in _ONE_OF for k in self.section_types)
            and self.body_words >= MIN_BODY_WORDS
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serialisable form, the shape the shared contract pins."""
        return {
            "is_full_text": self.is_full_text,
            "section_types": [t.value for t in self.section_types],
            "body_words": self.body_words,
        }


def count_words(text: str) -> int:
    """The number of runs of characters that are not :func:`is_white_space`."""
    words = 0
    in_word = False
    for ch in text:
        if is_white_space(ch):
            in_word = False
        elif not in_word:
            words += 1
            in_word = True
    return words


def assess_full_text(text: CanonicalText) -> FullTextAssessment:
    """Judge whether *text* is a whole paper (see the module docstring).

    It is when a *Methods* heading and a *Results* or *Discussion* one lie
    outside the abstract and the references, and the body holds at least
    :data:`MIN_BODY_WORDS`.
    """
    found: list[SectionType] = []
    body_words = 0
    excluded_level: int | None = None
    for section in text.sections:
        if excluded_level is not None and section.level > excluded_level:
            continue
        excluded_level = None
        kind = SectionType.UNKNOWN
        if section.heading_span is not None:
            kind, _ = match_section_type(text.span_text(section.heading_span))
        if kind is not SectionType.UNKNOWN and kind not in found:
            found.append(kind)
        if kind in _NOT_BODY:
            excluded_level = section.level
            continue
        body_words += sum(count_words(text.span_text(b.span)) for b in section.blocks)
    return FullTextAssessment(tuple(found), body_words)
