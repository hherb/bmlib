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

"""Tests for the shared JSON span locator and the two extractors built on it."""

from __future__ import annotations

import json

import pytest

from bmlib.llm.json_repair import extract_and_repair_json
from bmlib.llm.utils import extract_json, iter_json_spans


class TestIterJsonSpans:
    def test_json_fence_comes_first(self):
        text = '```\nnot json\n```\n```json\n{"a": 1}\n```'
        assert list(iter_json_spans(text))[0] == '{"a": 1}'

    def test_bare_fence_with_json_body_beats_plain_fence(self):
        text = '```\nprose\n```\n```\n{"a": 1}\n```'
        spans = list(iter_json_spans(text))
        assert spans.index('{"a": 1}') < spans.index("prose")

    def test_balanced_span_in_prose(self):
        text = 'The answer is {"score": 5} according to the model.'
        assert '{"score": 5}' in list(iter_json_spans(text))

    def test_braces_inside_strings_do_not_break_balancing(self):
        text = 'Result: {"expr": "f(x) = {x}", "ok": true} done.'
        assert '{"expr": "f(x) = {x}", "ok": true}' in list(iter_json_spans(text))

    def test_escaped_quote_does_not_end_the_string(self):
        text = r'{"a": "he said \"hi\"", "b": 2}'
        assert text in list(iter_json_spans(text))

    def test_array_span_and_nested_object_are_both_offered(self):
        # Stage 4 yields the array; stage 5 yields the object nested in it.
        spans = list(iter_json_spans('[{"a": 1}]'))
        assert '[{"a": 1}]' in spans
        assert '{"a": 1}' in spans
        assert spans.index('[{"a": 1}]') < spans.index('{"a": 1}')

    def test_two_objects_are_yielded_in_document_order(self):
        spans = list(iter_json_spans('noise {not valid} more {"good": 1} end'))
        assert spans.index("{not valid}") < spans.index('{"good": 1}')

    def test_unbalanced_opener_yields_the_tail(self):
        # Nothing balances, so the truncated tail is the only candidate.
        assert list(iter_json_spans('Result: {"a": 1, "b": [2')) == ['{"a": 1, "b": [2']

    def test_tail_is_not_yielded_when_something_balanced(self):
        spans = list(iter_json_spans('{"a": 1} trailing {'))
        assert spans == ['{"a": 1}']

    def test_no_json_yields_nothing(self):
        assert list(iter_json_spans("no json here")) == []

    def test_empty_text_yields_nothing(self):
        assert list(iter_json_spans("")) == []

    def test_tail_skips_an_opener_inside_a_string(self):
        text = 'named "config{v1}" and the result: {"a": 1, "items": [1, 2'
        assert list(iter_json_spans(text)) == ['{"a": 1, "items": [1, 2']

    def test_no_tail_when_the_only_opener_is_inside_a_string(self):
        assert list(iter_json_spans('Just a "quoted { thing" and nothing else.')) == []


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == '{"a": 1}'

    def test_object_from_json_fence(self):
        assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_object_from_bare_fence(self):
        assert extract_json('```\n{"a": 42}\n```') == '{"a": 42}'

    def test_object_from_prose(self):
        text = 'Here is the result: {"score": 0.8} end.'
        assert extract_json(text) == '{"score": 0.8}'

    def test_skips_unparseable_candidate(self):
        text = 'noise {not valid} more prose {"good": 1} trailing'
        assert extract_json(text) == '{"good": 1}'

    def test_returns_input_unchanged_when_nothing_parses(self):
        assert extract_json("no json here") == "no json here"

    def test_prefers_a_dict_over_an_earlier_array(self):
        # Preserves the pre-consolidation outcome: the brace-only scan was
        # object-only, so the object won.  Dict-preference keeps that.
        assert extract_json('[1, 2] then {"a": 1}') == '{"a": 1}'

    def test_prefers_the_object_nested_in_a_single_element_array(self):
        assert extract_json('[{"a": 1}]') == '{"a": 1}'

    def test_falls_back_to_an_array_when_no_object_parses(self):
        # No dict candidate anywhere, so the array is better than nothing —
        # previously the whole prose-wrapped text came back unchanged.
        assert extract_json("Values: [1, 2, 3] done") == "[1, 2, 3]"

    def test_truncated_text_is_left_alone(self):
        # The tail candidate never parses, so it self-excludes.
        text = '{"a": 1, "b": [2'
        assert extract_json(text) == text


class TestExtractAndRepairJsonPolicy:
    def test_walks_past_a_candidate_that_cannot_repair(self):
        # The junk fence body is offered first; the real object follows.
        text = '```\nnot json at all\n```\nbut here: {"a": 1}'
        extracted, repaired = extract_and_repair_json(text)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is False

    def test_walks_past_an_unrepairable_brace_span(self):
        text = 'noise {not valid} more prose {"good": 1} trailing'
        extracted, _ = extract_and_repair_json(text)
        assert json.loads(extracted) == {"good": 1}

    def test_array_span_wins_over_its_nested_object(self):
        # Unlike extract_json, this has no dict-preference: the outermost
        # span is the model's actual output and repair should target it.
        extracted, _ = extract_and_repair_json('[{"a": 1}]')
        assert json.loads(extracted) == [{"a": 1}]

    def test_repair_disabled_still_finds_valid_json(self):
        extracted, repaired = extract_and_repair_json('{"a": 1}', repair=False)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is False

    def test_repair_disabled_raises_on_malformed(self):
        with pytest.raises(ValueError):
            extract_and_repair_json('{"a": 1,}', repair=False)
