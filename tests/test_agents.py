"""Tests for bmlib.agents.base JSON parsing."""

from __future__ import annotations

import pytest

from bmlib.agents.base import BaseAgent


class TestParseJson:
    def test_plain_json(self):
        result = BaseAgent.parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = BaseAgent.parse_json(text)
        assert result == {"key": "value"}

    def test_json_in_generic_code_block(self):
        text = '```\n{"key": 42}\n```'
        result = BaseAgent.parse_json(text)
        assert result == {"key": 42}

    def test_json_with_surrounding_text(self):
        text = 'Here is the result: {"score": 0.8, "design": "rct"} end.'
        result = BaseAgent.parse_json(text)
        assert result["score"] == 0.8

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            BaseAgent.parse_json("not json at all")

    def test_message_helpers(self):
        sys = BaseAgent.system_msg("sys")
        usr = BaseAgent.user_msg("usr")
        asst = BaseAgent.assistant_msg("asst")
        assert sys.role == "system"
        assert usr.role == "user"
        assert asst.role == "assistant"
