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

import threading
from unittest.mock import MagicMock, patch

import pytest

from bmlib.agents.base import BaseAgent
from bmlib.agents.metrics import PerformanceMetrics
from bmlib.llm.data_types import BatchEmbeddingResponse, EmbeddingResponse, LLMResponse


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

    def test_json_with_braces_in_string_value(self):
        # Braces inside a string value must not break brace balancing.
        text = 'Result: {"expr": "f(x) = {x}", "ok": true} done.'
        result = BaseAgent.parse_json(text)
        assert result["expr"] == "f(x) = {x}"
        assert result["ok"] is True

    def test_picks_first_valid_object_among_prose(self):
        # A broken brace pair before a valid object must not swallow it.
        text = 'noise {not valid} more prose {"good": 1} trailing'
        result = BaseAgent.parse_json(text)
        assert result == {"good": 1}

    def test_repairs_trailing_comma(self):
        # Trailing commas are invalid JSON; the repair fallback must recover.
        result = BaseAgent.parse_json('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_repairs_single_quotes_in_code_block(self):
        # Single-quoted keys/values inside a code block must be repaired.
        text = "```json\n{'design': 'rct', 'n': 120}\n```"
        result = BaseAgent.parse_json(text)
        assert result == {"design": "rct", "n": 120}

    def test_repairs_truncated_response(self):
        # A response cut off mid-object must be closed and parsed.
        result = BaseAgent.parse_json('{"design": "rct", "scores": [1, 2')
        assert result == {"design": "rct", "scores": [1, 2]}

    def test_deeply_nested_text_raises_valueerror_not_recursionerror(self):
        # json.loads() descends recursively, so a repetition-looping model
        # blows the stack.  The documented contract is ValueError.
        with pytest.raises(ValueError, match="Could not parse JSON"):
            BaseAgent.parse_json('{"j": ' * 20000)

    def test_message_helpers(self):
        sys = BaseAgent.system_msg("sys")
        usr = BaseAgent.user_msg("usr")
        asst = BaseAgent.assistant_msg("asst")
        assert sys.role == "system"
        assert usr.role == "user"
        assert asst.role == "assistant"


class TestParseJsonShape:
    """The return contract: ``dict | list``, with opt-in strictness."""

    def test_a_bare_array_response_returns_the_list(self):
        assert BaseAgent.parse_json('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]

    def test_a_fenced_array_response_returns_the_list(self):
        text = '```json\n[{"pmid": "111"}, {"pmid": "222"}]\n```'
        assert BaseAgent.parse_json(text) == [{"pmid": "111"}, {"pmid": "222"}]

    def test_an_array_in_prose_returns_the_whole_list(self):
        # The fragment defect reached parse_json() through extract_json():
        # this used to return {"a": 1} and drop the sibling.
        text = 'Records: [{"a": 1}, {"b": 2}] done.'
        assert BaseAgent.parse_json(text) == [{"a": 1}, {"b": 2}]

    def test_require_dict_raises_and_names_the_shape(self):
        with pytest.raises(ValueError, match="list"):
            BaseAgent.parse_json('[{"a": 1}]', require_dict=True)

    def test_require_dict_accepts_a_dict(self):
        assert BaseAgent.parse_json('{"a": 1}', require_dict=True) == {"a": 1}

    def test_a_truncated_array_of_objects_is_repaired_whole(self, caplog):
        # The array never balances, so extraction can only offer the first
        # object.  Taking it would drop the sibling *and* skip the truncation
        # warning, which is the same silent loss the whole-span preference
        # exists to prevent — so the fragment waits until repair has had its
        # turn, and repair closes the bracket.
        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            result = BaseAgent.parse_json('[{"a": 1}, {"b": 2}')

        assert result == [{"a": 1}, {"b": 2}]
        assert "truncated" in caplog.text.lower()

    def test_a_fragment_is_still_the_last_resort(self):
        # Nothing whole parses and nothing repairs — extract_and_repair_json()
        # refuses to repair a fragment of a broken array — so the nested
        # object is better than reporting the response unparseable.
        assert BaseAgent.parse_json('[{"a": 1}, invalid junk]') == {"a": 1}

    @pytest.mark.parametrize("text", ["42", '"done"', "true", "null"])
    def test_a_bare_scalar_is_not_a_structured_answer(self, text):
        # dict | list is the whole contract, so it is enforced rather than
        # merely annotated: a scalar handed back would only defer the failure
        # to the caller's first subscript.
        with pytest.raises(ValueError, match="expected a JSON object or array"):
            BaseAgent.parse_json(text)

    def test_the_scalar_error_names_the_type_it_got(self):
        with pytest.raises(ValueError, match="got int"):
            BaseAgent.parse_json("42")


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

    # Anthropic reports truncation as "max_tokens", OpenAI-compatible
    # providers (and now the Ollama provider, via done_reason) as "length".
    @pytest.mark.parametrize("stop_reason", ["max_tokens", "length"])
    @patch("bmlib.agents.base.time.sleep")
    def test_truncated_at_temperature_zero_fails_fast(self, mock_sleep, stop_reason):
        # Greedy sampling reproduces the identical truncation, so retrying
        # only pays for it again: raise immediately and name the real cause.
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content='{"cases": [{"claim": "truncated mid-obj',
            model="test",
            stop_reason=stop_reason,
        )

        with pytest.raises(ValueError, match=f"stop_reason={stop_reason!r}"):
            agent.chat_json([agent.user_msg("test")], temperature=0.0)

        assert agent.llm.chat.call_count == 1
        mock_sleep.assert_not_called()

    @patch("bmlib.agents.base.time.sleep")
    def test_truncated_at_nonzero_temperature_retries_then_names_cause(self, mock_sleep):
        # At temperature > 0 a retry may sample a shorter completion that
        # fits, so truncation gets the normal retries — but the final error
        # names truncation, not "unparseable response".
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content='{"cases": [{"claim": "truncated mid-obj',
            model="test",
            stop_reason="max_tokens",
        )

        with pytest.raises(ValueError, match="truncated at max_tokens=4096"):
            agent.chat_json([agent.user_msg("test")], temperature=0.7)

        assert agent.llm.chat.call_count == 3

    @patch("bmlib.agents.base.time.sleep")
    def test_truncation_failure_names_the_retry_context(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content='{"cases": [{"claim": "truncated mid-obj',
            model="test",
            stop_reason="max_tokens",
        )

        with pytest.raises(ValueError, match="cochrane assessment"):
            agent.chat_json(
                [agent.user_msg("t")], temperature=0.0, retry_context="cochrane assessment"
            )

    @patch("bmlib.agents.base.time.sleep")
    def test_truncated_then_shorter_retry_recovers(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            LLMResponse(
                content='{"cases": [{"claim": "truncated mid-obj',
                model="test",
                stop_reason="max_tokens",
            ),
            _make_response('{"study_design": "rct"}'),
        ]

        result = agent.chat_json([agent.user_msg("test")], temperature=0.7)

        assert result == {"study_design": "rct"}
        assert agent.llm.chat.call_count == 2

    @patch("bmlib.agents.base.time.sleep")
    def test_truncated_empty_response_reports_truncation(self, mock_sleep):
        # A budget consumed before any visible text (e.g. by thinking tokens)
        # is truncation, not "empty response from model".
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="",
            model="test",
            stop_reason="max_tokens",
        )

        with pytest.raises(ValueError, match="truncated at max_tokens=4096"):
            agent.chat_json([agent.user_msg("test")], temperature=0.0)

        assert agent.llm.chat.call_count == 1

    @patch("bmlib.agents.base.time.sleep")
    def test_truncated_but_parseable_response_returns(self, mock_sleep):
        # If the JSON happens to be complete despite hitting the ceiling,
        # the response is usable — no error.
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content='{"ok": true}',
            model="test",
            stop_reason="max_tokens",
        )

        result = agent.chat_json([agent.user_msg("test")])

        assert result == {"ok": True}
        assert agent.llm.chat.call_count == 1

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


class TestChatJsonRequireDict:
    """``require_dict`` turns a wrong-shaped answer into a retry, then an error.

    Without it a list reached the two bmlib callers' ``_parse_data()`` and died
    on ``.get()`` with an ``AttributeError``, swallowed by a broad
    ``except Exception`` and degraded to ``unclassified()`` — no retry, no
    diagnosis.
    """

    @patch("bmlib.agents.base.time.sleep")
    def test_default_returns_a_list_unchanged(self, mock_sleep):
        # Non-breaking: the widened contract is the default.
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response('[{"a": 1}, {"b": 2}]')

        assert agent.chat_json([agent.user_msg("test")]) == [{"a": 1}, {"b": 2}]
        assert agent.llm.chat.call_count == 1

    @patch("bmlib.agents.base.time.sleep")
    def test_retries_a_list_then_accepts_the_dict(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response('[{"a": 1}]'),
            _make_response('{"study_design": "rct"}'),
        ]

        result = agent.chat_json([agent.user_msg("test")], require_dict=True)

        assert result == {"study_design": "rct"}
        assert agent.llm.chat.call_count == 2

    @patch("bmlib.agents.base.time.sleep")
    def test_persistent_list_names_the_shape_not_unparseability(self, mock_sleep):
        # "expected a JSON object, got list" and "unparseable response" are
        # different diagnoses; chat_json does its own isinstance check rather
        # than message-sniffing parse_json's ValueError to keep them apart.
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response('[{"a": 1}]')

        with pytest.raises(ValueError, match="expected a JSON object, got list"):
            agent.chat_json([agent.user_msg("test")], require_dict=True, temperature=0.7)

        assert agent.llm.chat.call_count == 3

    @patch("bmlib.agents.base.time.sleep")
    def test_a_list_at_temperature_zero_fails_fast(self, mock_sleep):
        # Mirrors the truncation path: greedy sampling returns the same array
        # from the same messages, so the retry is provably futile.  Assert the
        # call count, not just the exception — retrying still ends in a
        # ValueError naming the shape, so an exception-only assertion cannot
        # tell the two behaviours apart.
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response('[{"a": 1}]')

        with pytest.raises(ValueError, match="expected a JSON object, got list"):
            agent.chat_json([agent.user_msg("test")], require_dict=True, temperature=0.0)

        assert agent.llm.chat.call_count == 1
        mock_sleep.assert_not_called()

    @patch("bmlib.agents.base.time.sleep")
    def test_rejects_a_list_arriving_via_the_truncation_path(self, mock_sleep):
        # A response that hit the ceiling but happens to hold complete JSON is
        # returned as-is by _try_parse — that shortcut must respect the shape
        # requirement too.
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content='[{"a": 1}]',
            model="test",
            stop_reason="max_tokens",
        )

        with pytest.raises(ValueError, match="expected a JSON object, got list"):
            agent.chat_json([agent.user_msg("test")], require_dict=True, temperature=0.0)

        assert agent.llm.chat.call_count == 1

    @patch("bmlib.agents.base.time.sleep")
    def test_retries_a_list_from_the_truncation_path_above_temperature_zero(self, mock_sleep):
        # The other half of the truncation shortcut: above temperature 0 the
        # wrong shape is retryable there too, exactly as it is on the normal
        # return path.  Without this the `continue` in that branch is dead as
        # far as the suite is concerned.
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            LLMResponse(content='[{"a": 1}]', model="test", stop_reason="max_tokens"),
            _make_response('{"study_design": "rct"}'),
        ]

        result = agent.chat_json([agent.user_msg("t")], require_dict=True, temperature=0.7)

        assert result == {"study_design": "rct"}
        assert agent.llm.chat.call_count == 2

    @patch("bmlib.agents.base.time.sleep")
    def test_the_shape_failure_names_the_retry_context(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response('[{"a": 1}]')

        with pytest.raises(ValueError, match="cochrane assessment"):
            agent.chat_json(
                [agent.user_msg("t")],
                require_dict=True,
                temperature=0.0,
                retry_context="cochrane assessment",
            )

    @patch("bmlib.agents.base.time.sleep")
    def test_the_shape_failure_log_line_names_the_context_once(self, mock_sleep, caplog):
        agent = _make_agent()
        agent.llm.chat.return_value = _make_response('[{"a": 1}]')

        with caplog.at_level("ERROR", logger="bmlib.agents.base"):
            with pytest.raises(ValueError):
                agent.chat_json(
                    [agent.user_msg("t")],
                    require_dict=True,
                    temperature=0.0,
                    retry_context="quality classification",
                )

        # The attempt marker already carries the context; the reason must not
        # repeat it.
        assert caplog.text.count("quality classification") == 1

    @patch("bmlib.agents.base.time.sleep")
    def test_a_scalar_response_is_retried_as_unparseable(self, mock_sleep):
        # A bare scalar is outside parse_json's dict | list contract, so it
        # surfaces as a ValueError and chat_json retries it like any other
        # unparseable response rather than handing back 42.
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response("42"),
            _make_response('{"study_design": "rct"}'),
        ]

        assert agent.chat_json([agent.user_msg("t")]) == {"study_design": "rct"}
        assert agent.llm.chat.call_count == 2


class TestPerformanceMetrics:
    def test_starts_empty(self):
        m = PerformanceMetrics()
        assert m.total_tokens == 0
        assert m.total_requests == 0
        assert m.tokens_per_second == 0.0
        assert m.average_tokens_per_request == 0.0
        assert m.elapsed_time_seconds == 0.0

    def test_add_request_accumulates(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        m.add_request(10, 5, 1.0)
        assert m.total_prompt_tokens == 110
        assert m.total_completion_tokens == 55
        assert m.total_tokens == 165
        assert m.total_requests == 2
        assert m.total_wall_time_seconds == 3.0

    def test_tokens_per_second_uses_wall_time(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        assert m.tokens_per_second == 25.0

    def test_average_tokens_per_request(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 1.0)
        m.add_request(50, 0, 1.0)
        assert m.average_tokens_per_request == 100.0

    def test_add_retry_is_separate_from_requests(self):
        m = PerformanceMetrics()
        m.add_request(1, 1, 0.1)
        m.add_retry()
        assert m.total_requests == 1
        assert m.total_retries == 1

    @patch("bmlib.agents.metrics.time.monotonic")
    @patch("bmlib.agents.metrics.time.time")
    def test_elapsed_time_between_marks(self, mock_time, mock_monotonic):
        mock_time.side_effect = [100.0, 102.5]
        mock_monotonic.side_effect = [5.0, 7.5]
        m = PerformanceMetrics()
        m.mark_start()
        m.mark_end()
        assert m.elapsed_time_seconds == 2.5
        # start_time/end_time stay absolute wall-clock timestamps.
        assert m.end_time == 102.5

    @patch("bmlib.agents.metrics.time.monotonic")
    @patch("bmlib.agents.metrics.time.time")
    def test_elapsed_survives_a_wall_clock_step(self, mock_time, mock_monotonic):
        # An NTP correction or DST change moves time.time() backwards mid-run.
        # elapsed must stay positive, or format_report() prints a duration
        # shorter than the time already accounted to requests.
        mock_time.side_effect = [100.0, 40.0]
        mock_monotonic.side_effect = [5.0, 8.0]
        m = PerformanceMetrics()
        m.mark_start()
        m.mark_end()
        assert m.elapsed_time_seconds == 3.0

    def test_elapsed_falls_back_to_timestamps_after_from_dict(self):
        # Monotonic marks are not serialisable across processes.
        restored = PerformanceMetrics.from_dict({"start_time": 100.0, "end_time": 102.5})
        assert restored.elapsed_time_seconds == 2.5

    @patch("bmlib.agents.metrics.time.monotonic")
    @patch("bmlib.agents.metrics.time.time")
    def test_snapshot_carries_the_monotonic_marks(self, mock_time, mock_monotonic):
        # The two clocks get deliberately different intervals: a snapshot that
        # dropped the monotonic marks would fall back to end_time - start_time
        # and still look plausible, so equal intervals would not catch it.
        mock_time.side_effect = [100.0, 109.0]  # 9s of wall clock
        mock_monotonic.side_effect = [5.0, 8.0]  # 3s of monotonic
        m = PerformanceMetrics()
        m.mark_start()
        m.mark_end()
        assert m.snapshot().elapsed_time_seconds == 3.0

    def test_mark_start_clears_a_previous_end(self):
        m = PerformanceMetrics()
        m.mark_start()
        m.mark_end()
        m.mark_start()
        assert m.end_time is None
        # A stale monotonic end would freeze elapsed at the previous period.
        assert m.snapshot().elapsed_time_seconds >= 0.0

    def test_reset_clears_everything(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        m.add_retry()
        m.mark_start()
        m.reset()
        assert m.total_tokens == 0
        assert m.total_retries == 0
        assert m.start_time is None

    def test_snapshot_is_independent(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        snap = m.snapshot()
        m.add_request(1, 1, 0.1)
        assert snap.total_tokens == 150
        assert m.total_tokens == 152

    def test_to_dict_round_trips(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        m.add_retry()
        restored = PerformanceMetrics.from_dict(m.to_dict())
        assert restored.total_tokens == m.total_tokens
        assert restored.total_retries == m.total_retries
        assert restored.total_wall_time_seconds == m.total_wall_time_seconds

    def test_to_dict_has_no_lock(self):
        assert "_lock" not in PerformanceMetrics().to_dict()

    def test_format_report_includes_the_numbers(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        report = m.format_report(title="ScoringAgent")
        assert "ScoringAgent" in report
        assert "150" in report
        assert "Requests:     1" in report

    def test_format_report_without_title(self):
        assert "===" not in PerformanceMetrics().format_report()

    def test_concurrent_add_request_loses_nothing(self):
        # += is a read-modify-write; a shared agent must not drop counts.
        m = PerformanceMetrics()

        def worker() -> None:
            for _ in range(200):
                m.add_request(1, 1, 0.001)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert m.total_requests == 1600
        assert m.total_tokens == 3200

    def test_exported_from_package(self):
        from bmlib.agents import PerformanceMetrics as PkgPerformanceMetrics

        assert PkgPerformanceMetrics is PerformanceMetrics


class TestAgentMetrics:
    @patch("bmlib.agents.base.time.monotonic")
    def test_chat_records_a_request(self, mock_monotonic):
        mock_monotonic.side_effect = [10.0, 12.5]
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="hi", model="test", input_tokens=10, output_tokens=4
        )

        agent.chat([agent.user_msg("test")])

        assert agent.metrics.total_requests == 1
        assert agent.metrics.total_prompt_tokens == 10
        assert agent.metrics.total_completion_tokens == 4
        assert agent.metrics.total_wall_time_seconds == 2.5

    def test_failed_chat_records_nothing(self):
        agent = _make_agent()
        agent.llm.chat.side_effect = RuntimeError("provider exploded")

        with pytest.raises(RuntimeError):
            agent.chat([agent.user_msg("test")])

        assert agent.metrics.total_requests == 0

    @patch("bmlib.agents.base.time.sleep")
    def test_chat_json_counts_attempts_and_retries(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response(""),
            LLMResponse(content='{"a": 1}', model="test", input_tokens=5, output_tokens=2),
        ]

        agent.chat_json([agent.user_msg("test")])

        assert agent.metrics.total_requests == 2
        assert agent.metrics.total_retries == 1

    def test_metrics_property_is_a_snapshot(self):
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="hi", model="test", input_tokens=1, output_tokens=1
        )
        agent.chat([agent.user_msg("test")])

        snap = agent.metrics
        agent.chat([agent.user_msg("test")])

        assert snap.total_requests == 1
        assert agent.metrics.total_requests == 2

    def test_reset_start_stop(self):
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="hi", model="test", input_tokens=1, output_tokens=1
        )
        agent.chat([agent.user_msg("test")])
        agent.reset_metrics()
        assert agent.metrics.total_requests == 0

        agent.start_metrics()
        agent.stop_metrics()
        assert agent.metrics.end_time is not None

    def test_format_metrics_report_names_the_agent_class(self):
        agent = _make_agent()
        assert "BaseAgent" in agent.format_metrics_report()

    @patch("bmlib.agents.base.time.sleep")
    def test_retry_context_appears_in_the_log(self, mock_sleep, caplog):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response(""),
            _make_response('{"a": 1}'),
        ]

        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            agent.chat_json([agent.user_msg("t")], retry_context="citation extraction")

        assert "citation extraction" in caplog.text

    def test_repaired_response_logs_a_warning(self, caplog):
        # A response that only parsed after repair is the truncation signal.
        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            BaseAgent.parse_json('{"design": "rct", "scores": [1, 2')
        assert "repair" in caplog.text.lower()

    def test_clean_response_logs_no_warning(self, caplog):
        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            BaseAgent.parse_json('{"a": 1}')
        assert caplog.text == ""


class TestAgentEmbeddings:
    def test_embed_returns_the_vector(self):
        agent = _make_agent()
        agent.llm.embed.return_value = EmbeddingResponse(
            embedding=[0.1, 0.2], model="e", dimensions=2
        )

        assert agent.embed("hello") == [0.1, 0.2]

    def test_embed_uses_the_configured_embedding_model(self):
        mock_llm = MagicMock()
        agent = BaseAgent(llm=mock_llm, model="test:model", embedding_model="ollama:e2")
        mock_llm.embed.return_value = EmbeddingResponse(embedding=[1.0], model="e2")

        agent.embed("hello")

        assert mock_llm.embed.call_args.kwargs["model"] == "ollama:e2"

    def test_embed_argument_overrides_the_default(self):
        mock_llm = MagicMock()
        agent = BaseAgent(llm=mock_llm, model="test:model", embedding_model="ollama:e2")
        mock_llm.embed.return_value = EmbeddingResponse(embedding=[1.0], model="e3")

        agent.embed("hello", model="ollama:e3")

        assert mock_llm.embed.call_args.kwargs["model"] == "ollama:e3"

    def test_embed_raises_on_an_empty_vector(self):
        agent = _make_agent()
        agent.llm.embed.return_value = EmbeddingResponse(embedding=[], model="e")

        with pytest.raises(ValueError, match="[Ee]mpty embedding"):
            agent.embed("hello")

    def test_embed_does_not_touch_generation_metrics(self):
        agent = _make_agent()
        agent.llm.embed.return_value = EmbeddingResponse(embedding=[1.0], model="e")

        agent.embed("hello")

        assert agent.metrics.total_requests == 0

    def test_embed_batch_returns_vectors_in_order(self):
        agent = _make_agent()
        agent.llm.embed_batch.return_value = BatchEmbeddingResponse(
            embeddings=[[1.0], [2.0]], model="e", dimensions=1
        )

        assert agent.embed_batch(["a", "b"]) == [[1.0], [2.0]]

    def test_embed_batch_short_circuits_on_an_empty_list(self):
        agent = _make_agent()

        assert agent.embed_batch([]) == []
        agent.llm.embed_batch.assert_not_called()

    def test_embed_batch_raises_on_a_count_mismatch(self):
        agent = _make_agent()
        agent.llm.embed_batch.return_value = BatchEmbeddingResponse(
            embeddings=[[1.0]], model="e", dimensions=1
        )

        with pytest.raises(ValueError, match="2 texts"):
            agent.embed_batch(["a", "b"])


class TestAgentConnection:
    def test_reports_a_reachable_provider(self):
        agent = _make_agent()
        agent.llm.test_connection.return_value = True

        assert agent.test_connection() is True
        agent.llm.test_connection.assert_called_once_with("test")

    def test_reports_an_unreachable_provider(self):
        agent = _make_agent()
        agent.llm.test_connection.return_value = False

        assert agent.test_connection() is False

    def test_falls_back_to_the_client_default_provider(self):
        mock_llm = MagicMock()
        mock_llm.default_provider = "ollama"
        mock_llm.test_connection.return_value = True
        agent = BaseAgent(llm=mock_llm, model="bare-model-name")

        assert agent.test_connection() is True
        mock_llm.test_connection.assert_called_once_with("ollama")

    def test_pathological_leading_colon_falls_back_to_the_client_default(self):
        # ":model".split(":", 1)[0] is "" — falsy — which must not be passed
        # through as the provider.  LLMClient.test_connection("") takes the
        # all-providers branch and returns a (truthy) dict, so this would
        # silently report a non-existent provider as reachable.
        mock_llm = MagicMock()
        mock_llm.default_provider = "ollama"
        mock_llm.test_connection.return_value = True
        agent = BaseAgent(llm=mock_llm, model=":model")

        assert agent.test_connection() is True
        mock_llm.test_connection.assert_called_once_with("ollama")
