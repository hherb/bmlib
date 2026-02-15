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

"""Tests for bmlib.agents.base JSON parsing and chat_json retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bmlib.agents.base import BaseAgent
from bmlib.llm.data_types import LLMResponse


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


def _make_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, model="test", input_tokens=0, output_tokens=0)


def _make_agent() -> BaseAgent:
    mock_llm = MagicMock()
    return BaseAgent(llm=mock_llm, model="test:model")


class TestChatJson:
    """Tests for BaseAgent.chat_json() retry logic."""

    @patch("bmlib.agents.base.time.sleep")
    def test_success_first_attempt(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response('{"study_design": "rct"}')

        result = agent.chat_json([agent.user_msg("test")])

        assert result == {"study_design": "rct"}
        assert agent.llm.chat.call_count == 1
        mock_sleep.assert_not_called()

    @patch("bmlib.agents.base.time.sleep")
    def test_retry_after_empty_response(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response(""),
            _make_response('{"study_design": "rct"}'),
        ]

        result = agent.chat_json([agent.user_msg("test")])

        assert result == {"study_design": "rct"}
        assert agent.llm.chat.call_count == 2
        mock_sleep.assert_called_once_with(1)  # 2^(1-1) = 1s

    @patch("bmlib.agents.base.time.sleep")
    def test_retry_after_unparseable_response(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response("not json at all"),
            _make_response('{"study_design": "cohort_prospective"}'),
        ]

        result = agent.chat_json([agent.user_msg("test")])

        assert result == {"study_design": "cohort_prospective"}
        assert agent.llm.chat.call_count == 2

    @patch("bmlib.agents.base.time.sleep")
    def test_all_retries_exhausted_raises(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response("")

        with pytest.raises(ValueError, match="Failed after 3 attempts"):
            agent.chat_json([agent.user_msg("test")])

        assert agent.llm.chat.call_count == 3

    @patch("bmlib.agents.base.time.sleep")
    def test_exponential_backoff_timing(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response("")

        with pytest.raises(ValueError):
            agent.chat_json([agent.user_msg("test")])

        # First attempt: no sleep. Then: sleep(1), sleep(2)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("bmlib.agents.base.time.sleep")
    def test_custom_max_retries(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response("")

        with pytest.raises(ValueError, match="Failed after 5 attempts"):
            agent.chat_json([agent.user_msg("test")], max_retries=5)

        assert agent.llm.chat.call_count == 5

    @patch("bmlib.agents.base.time.sleep")
    def test_empty_response_logs_warning(self, mock_sleep, caplog):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response(""),
            _make_response('{"ok": true}'),
        ]

        import logging
        with caplog.at_level(logging.WARNING, logger="bmlib.agents.base"):
            agent.chat_json([agent.user_msg("test")])

        assert "empty response" in caplog.text.lower()

    @patch("bmlib.agents.base.time.sleep")
    def test_unparseable_response_logs_error_with_content(self, mock_sleep, caplog):
        agent = _make_agent()
        bad_content = "This is garbage output from the model"
        agent.llm.chat.side_effect = [
            _make_response(bad_content),
            _make_response('{"ok": true}'),
        ]

        import logging
        with caplog.at_level(logging.ERROR, logger="bmlib.agents.base"):
            agent.chat_json([agent.user_msg("test")])

        assert bad_content in caplog.text

    @patch("bmlib.agents.base.time.sleep")
    def test_whitespace_only_treated_as_empty(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response("   \n  "),
            _make_response('{"ok": true}'),
        ]

        result = agent.chat_json([agent.user_msg("test")])
        assert result == {"ok": True}
        assert agent.llm.chat.call_count == 2
