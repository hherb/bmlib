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

"""``assess_full_text`` and ``match_section_type``, rule by rule.

The shared contract is ``tests/test_full_text_assessment_contract.py``.
"""

from __future__ import annotations

from bmlib.fulltext.canonical import (
    MIN_BODY_WORDS,
    assess_full_text,
    canonical_from_markdown,
    count_words,
)
from bmlib.fulltext.models import SectionType
from bmlib.fulltext.segmenter import PARTIAL_MATCH_CONFIDENCE, SectionSegmenter, match_section_type

#: A paragraph of exactly :data:`MIN_BODY_WORDS` words.
BODY = " ".join(["word"] * MIN_BODY_WORDS)


def paper(*parts: str) -> str:
    """Markdown joining *parts* as blank-line-separated blocks."""
    return "\n\n".join(parts)


class TestMatchSectionType:
    def test_it_is_the_segmenters_own_rule(self) -> None:
        for heading in ("2. Methods", "Statistical methods", "Thanks", "Results:"):
            assert match_section_type(heading) == SectionSegmenter()._match_section_type(heading)

    def test_exact_and_partial_confidence(self) -> None:
        assert match_section_type("Methods") == (SectionType.METHODS, 1.0)
        assert match_section_type("Statistical methods") == (
            SectionType.METHODS,
            PARTIAL_MATCH_CONFIDENCE,
        )
        assert match_section_type("Thanks") == (SectionType.UNKNOWN, 0.0)


class TestCountWords:
    def test_words_are_runs_between_white_space(self) -> None:
        assert count_words(" a  b\tc\n") == 3

    def test_an_information_separator_is_not_white_space(self) -> None:
        assert count_words("a\x1cb") == 1


class TestAssessFullText:
    def test_methods_results_and_enough_words_is_a_paper(self) -> None:
        result = assess_full_text(
            canonical_from_markdown(paper("## Methods", BODY, "## Results", "x"))
        )
        assert result.is_full_text
        assert result.section_types == (SectionType.METHODS, SectionType.RESULTS)
        assert result.body_words == MIN_BODY_WORDS + 1

    def test_discussion_stands_in_for_results(self) -> None:
        assert assess_full_text(
            canonical_from_markdown(paper("## Methods", BODY, "## Discussion", "x"))
        ).is_full_text

    def test_without_methods_it_is_not(self) -> None:
        assert not assess_full_text(
            canonical_from_markdown(paper("## Results", BODY, "## Discussion", "x"))
        ).is_full_text

    def test_one_word_short_it_is_not(self) -> None:
        short = " ".join(["word"] * (MIN_BODY_WORDS - 1))
        result = assess_full_text(canonical_from_markdown(paper("## Methods", short, "## Results")))
        assert (result.is_full_text, result.body_words) == (False, MIN_BODY_WORDS - 1)

    def test_a_structured_abstracts_headings_and_words_are_the_abstracts(self) -> None:
        result = assess_full_text(
            canonical_from_markdown(
                paper(
                    "## Abstract", "### Methods", BODY, "### Results", "x", "## Introduction", "y"
                )
            )
        )
        assert result.section_types == (SectionType.ABSTRACT, SectionType.INTRODUCTION)
        assert (result.is_full_text, result.body_words) == (False, 1)

    def test_the_references_words_are_not_the_body(self) -> None:
        result = assess_full_text(
            canonical_from_markdown(paper("## Methods", "## Results", "## References", BODY))
        )
        assert (result.is_full_text, result.body_words) == (False, 0)

    def test_the_abstract_ends_at_the_next_heading_of_its_level(self) -> None:
        result = assess_full_text(
            canonical_from_markdown(paper("## Abstract", "a b", "## Methods", BODY, "## Results"))
        )
        assert result.is_full_text and result.body_words == MIN_BODY_WORDS

    def test_each_type_is_listed_once(self) -> None:
        result = assess_full_text(
            canonical_from_markdown(paper("## Methods", "## Methods", "## Results"))
        )
        assert result.section_types == (SectionType.METHODS, SectionType.RESULTS)

    def test_to_dict_is_the_contract_shape(self) -> None:
        result = assess_full_text(canonical_from_markdown(paper("## Methods", "a")))
        assert result.to_dict() == {
            "is_full_text": False,
            "section_types": ["methods"],
            "body_words": 1,
        }
