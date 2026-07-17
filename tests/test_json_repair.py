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

"""Tests for bmlib.llm.json_repair — repairing malformed LLM JSON."""

from __future__ import annotations

import json

import pytest

from bmlib.llm.json_repair import (
    JSONRepairError,
    extract_and_repair_json,
    repair_json,
    safe_json_loads,
)


class TestRepairJson:
    def test_valid_json_returned_unchanged(self):
        valid = '{"a": 1, "b": [2, 3]}'
        assert repair_json(valid) == valid

    def test_trailing_comma_in_object(self):
        repaired = repair_json('{"a": 1, "b": 2,}')
        assert json.loads(repaired) == {"a": 1, "b": 2}

    def test_trailing_comma_in_array(self):
        repaired = repair_json('{"a": [1, 2, 3,]}')
        assert json.loads(repaired) == {"a": [1, 2, 3]}

    def test_single_quotes_converted_to_double(self):
        repaired = repair_json("{'a': 'hello', 'b': 'world'}")
        assert json.loads(repaired) == {"a": "hello", "b": "world"}

    def test_unescaped_newline_in_string(self):
        repaired = repair_json('{"text": "line one\nline two"}')
        assert json.loads(repaired) == {"text": "line one\nline two"}

    def test_unescaped_tab_in_string(self):
        repaired = repair_json('{"text": "col1\tcol2"}')
        assert json.loads(repaired) == {"text": "col1\tcol2"}

    def test_truncated_object_is_closed(self):
        repaired = repair_json('{"a": 1, "b": 2')
        assert json.loads(repaired) == {"a": 1, "b": 2}

    def test_truncated_nested_structure_is_closed(self):
        repaired = repair_json('{"items": [{"id": 1}, {"id": 2}')
        assert json.loads(repaired) == {"items": [{"id": 1}, {"id": 2}]}

    def test_unquoted_keys_are_quoted(self):
        repaired = repair_json('{key: "value", other: 5}')
        assert json.loads(repaired) == {"key": "value", "other": 5}

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            repair_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            repair_json("   \n  ")

    def test_oversized_input_raises_value_error(self):
        with pytest.raises(ValueError):
            repair_json('{"a": "' + "x" * 1_000_001 + '"}')

    def test_unrepairable_raises_json_repair_error(self):
        with pytest.raises(JSONRepairError):
            repair_json("this is not json at all, no braces here")

    def test_apostrophe_inside_double_quoted_string_preserved(self):
        # A genuine apostrophe must survive; only quote-delimiters convert.
        repaired = repair_json('{"note": "it\'s fine"}')
        assert json.loads(repaired) == {"note": "it's fine"}


class TestSafeJsonLoads:
    def test_parses_valid_json(self):
        assert safe_json_loads('{"a": 1}') == {"a": 1}

    def test_repairs_and_parses(self):
        assert safe_json_loads('{"a": 1,}') == {"a": 1}

    def test_repair_disabled_raises_on_malformed(self):
        with pytest.raises(ValueError):
            safe_json_loads('{"a": 1,}', repair=False)

    def test_empty_raises_value_error(self):
        with pytest.raises(ValueError):
            safe_json_loads("")


class TestExtractAndRepairJson:
    def test_extracts_from_json_code_block(self):
        response = 'Here you go:\n```json\n{"a": 1}\n```\nDone.'
        extracted, repaired = extract_and_repair_json(response)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is False

    def test_extracts_and_repairs_from_code_block(self):
        response = '```json\n{"a": 1,}\n```'
        extracted, repaired = extract_and_repair_json(response)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is True

    def test_extracts_bare_object_from_prose(self):
        response = 'The answer is {"score": 5} according to the model.'
        extracted, _ = extract_and_repair_json(response)
        assert json.loads(extracted) == {"score": 5}

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_and_repair_json("no json here")
