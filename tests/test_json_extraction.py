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

from bmlib.llm.json_repair import extract_and_repair_json, salvage_json_fields
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

    def test_nested_objects_can_be_suppressed(self):
        assert list(iter_json_spans('[{"a": 1}]', nested_objects=False)) == ['[{"a": 1}]']


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

    def test_fenced_array_of_objects_is_returned_whole(self):
        # A fence is the model's own delimitation of its answer, so it wins
        # over dict-preference.  Returning the first element would silently
        # drop the rest of a list a caller asked for.
        text = '```json\n[{"pmid": "111"}, {"pmid": "222"}]\n```'
        assert json.loads(extract_json(text)) == [{"pmid": "111"}, {"pmid": "222"}]

    def test_unfenced_nested_object_still_wins(self):
        # Without a fence, dict-preference still applies — this is the
        # pre-consolidation behaviour and must not change.
        assert extract_json('[{"a": 1}]') == '{"a": 1}'

    def test_deep_nesting_returns_the_text_rather_than_raising(self):
        # json.loads() descends recursively, so the stage-6 tail candidate of
        # a repetition loop blows the stack.  extract_json() is documented
        # never to raise and runs on every json_mode response from the
        # Anthropic and OpenAI-compatible providers, so a crash here would
        # take out the provider call.
        text = '{"j": ' * 20000
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

    def test_raises_rather_than_returning_a_fragment_of_a_broken_array(self):
        # Walking must not reach into a candidate it already rejected: the
        # nested object parses alone, but returning it discards the array
        # and everything after it.
        with pytest.raises(ValueError):
            extract_and_repair_json('[{"a": 1}, invalid junk]')

    def test_repair_disabled_still_walks_past_a_junk_candidate(self):
        text = '```\nnot json at all\n```\nand {"a": 1}'
        extracted, repaired = extract_and_repair_json(text, repair=False)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is False

    def test_repair_disabled_raises_when_every_candidate_fails(self):
        with pytest.raises(ValueError):
            extract_and_repair_json('{"a": 1,} and {"b": 2,}', repair=False)

    def test_deep_nesting_raises_valueerror_not_recursionerror(self):
        # json.loads() descends recursively, so a repetition loop nests past
        # the stack limit.  RecursionError is not a ValueError and would
        # escape the documented contract.
        with pytest.raises(ValueError):
            extract_and_repair_json('{"j": ' * 20000)

    def test_no_dict_preference_unlike_extract_json(self):
        # The two share a locator but not an acceptance policy.  Pinned
        # because it is the one place where sharing iter_json_spans() could
        # tempt a future change into "harmonising" them.
        text = 'text [1,2] then {"a": 1}'
        extracted, _ = extract_and_repair_json(text)
        assert json.loads(extracted) == [1, 2]
        assert extract_json(text) == '{"a": 1}'


class TestSalvageJsonFields:
    def test_recovers_fields_from_valid_json(self):
        text = '{"a": 1, "b": "x"}'
        assert salvage_json_fields(text, ["a", "b"]) == {"a": 1, "b": "x"}

    def test_recovers_intact_fields_when_the_tail_is_malformed(self):
        # The motivating case: only the trailing array was mangled.
        text = '{"judgement": "high", "quotes": ["a", "b'
        result = salvage_json_fields(text, ["judgement", "quotes"])
        assert result["judgement"] == "high"
        assert result["quotes"] == ["a", "b"]

    def test_recovers_values_of_every_json_type(self):
        text = '{"s": "x", "n": 1.5, "b": true, "z": null, "o": {"k": [1]}}'
        result = salvage_json_fields(text, ["s", "n", "b", "z", "o"])
        assert result == {"s": "x", "n": 1.5, "b": True, "z": None, "o": {"k": [1]}}

    def test_decodes_escapes_in_string_values(self):
        text = r'{"j": "he said \"hi\"\nthen left"}'
        assert salvage_json_fields(text, ["j"])["j"] == 'he said "hi"\nthen left'

    def test_missing_key_is_simply_absent(self):
        assert salvage_json_fields('{"a": 1}', ["a", "nope"]) == {"a": 1}

    def test_never_raises_on_junk(self):
        assert salvage_json_fields("no json here", ["a"]) == {}

    def test_empty_text_returns_empty_dict(self):
        assert salvage_json_fields("", ["a"]) == {}

    def test_first_match_wins(self):
        # Documented limitation: a key name occurring inside a string value
        # can be matched.  The first occurrence is what is taken.
        text = '{"note": "raw "judgement": "wrong" here", "judgement": "right"}'
        assert salvage_json_fields(text, ["judgement"])["judgement"] == "wrong"

    def test_exported_from_package(self):
        from bmlib.llm import salvage_json_fields as pkg_salvage

        assert pkg_salvage is salvage_json_fields

    def test_repair_is_attempted_at_most_once_per_key(self, monkeypatch):
        # A repetition-loop response matches the key many times and never
        # decodes.  Repairing the tail at every match is quadratic.
        from bmlib.llm import json_repair as jr

        calls = []
        real_repair = jr.repair_json

        def counting_repair(text, *args, **kwargs):
            calls.append(len(text))
            return real_repair(text, *args, **kwargs)

        monkeypatch.setattr(jr, "repair_json", counting_repair)

        text = '{"j": ' * 500
        assert jr.salvage_json_fields(text, ["j"]) == {}
        assert len(calls) <= 1

    def test_fast_pass_is_bounded_to_the_match_cap(self, monkeypatch):
        # Each failed raw_decode scans forward to the end of the document, so
        # an unbounded fast pass is quadratic in the response length.
        from bmlib.llm import json_repair as jr

        calls = []
        real_decode_at = jr._decode_value_at

        def counting_decode_at(*args, **kwargs):
            calls.append(kwargs.get("allow_repair", True))
            return real_decode_at(*args, **kwargs)

        monkeypatch.setattr(jr, "_decode_value_at", counting_decode_at)

        text = '{"j": ' * (jr.MAX_SALVAGE_MATCHES * 5)
        assert jr.salvage_json_fields(text, ["j"]) == {}
        # MAX_SALVAGE_MATCHES fast attempts, plus the single repair attempt.
        assert len(calls) == jr.MAX_SALVAGE_MATCHES + 1
        assert calls.count(True) == 1

    def test_the_last_match_is_still_reached_beyond_the_cap(self):
        # The cap bounds the fast pass, but repair always runs at the true
        # last match — that is where a truncated tail lives.
        from bmlib.llm.json_repair import MAX_SALVAGE_MATCHES

        text = '{"j": ' * (MAX_SALVAGE_MATCHES + 50) + '{"j": 42}'
        assert salvage_json_fields(text, ["j"]) == {"j": 42}

    def test_deep_nesting_does_not_raise_recursionerror(self):
        # raw_decode descends recursively; a repetition loop nests past the
        # interpreter's stack limit.  RecursionError is not a ValueError, so
        # it would escape the promise never to raise on malformed text.
        text = '{"j": ' * 20000
        assert salvage_json_fields(text, ["j"]) == {}
