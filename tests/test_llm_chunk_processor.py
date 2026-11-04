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

"""Tests for the LLM-backed context processor."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.agents.base import BaseAgent, ChatStoppedError
from bmlib.context_processor import (
    ConsolidatedItem,
    LLMChunkProcessor,
    ProcessingConfig,
    ProcessingStatus,
    ProcessingStoppedError,
)
from bmlib.context_processor.llm_processor import CONTEXT_MODEL_ATTEMPTS
from bmlib.llm.data_types import LLMResponse


def make_agent(*replies: str) -> BaseAgent:
    """An agent whose client returns *replies* in order, then repeats the last."""
    agent = BaseAgent(llm=MagicMock(), model="test:model")
    responses = [
        LLMResponse(content=reply, model="test:model", input_tokens=1, output_tokens=1)
        for reply in (replies or ("extracted",))
    ]
    # Pad, so a run needing more calls than replies keeps the last one.
    agent.llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 50)
    return agent


@pytest.fixture(autouse=True)
def _no_backoff() -> Iterator[MagicMock]:
    """The model call's retry backoff, recorded rather than slept."""
    with patch("bmlib.context_processor.llm_processor.time.sleep") as sleep:
        yield sleep


class TestPromptTemplates:
    """Validation and rendering of the two prompt templates."""

    def test_a_template_without_query_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="extraction_prompt"):
            LLMChunkProcessor(make_agent(), extraction_prompt="only {content}")

    def test_a_template_without_content_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="consolidation_prompt"):
            LLMChunkProcessor(make_agent(), consolidation_prompt="only {query}")

    def test_literal_braces_survive_rendering(self) -> None:
        """Substitution is by replacement, not ``str.format`` — a template may
        carry a JSON example or a regex without doubling its braces."""
        agent = make_agent("done")
        processor = LLMChunkProcessor(
            agent,
            extraction_prompt='Answer {query} over {content} as {"a": 1}',
        )
        processor.process(["chunk"], query="what?")
        prompt = agent.llm.chat.call_args.kwargs["messages"][0].content
        assert '{"a": 1}' in prompt
        assert "what?" in prompt
        assert "chunk" in prompt

    def test_structured_output_selects_the_json_template(self) -> None:
        processor = LLMChunkProcessor(make_agent(), use_structured_output=True)
        assert "JSON" in processor.extraction_prompt

    def test_a_placeholder_inside_the_query_is_not_expanded(self) -> None:
        """Two chained ``str.replace`` calls run the second pass over what the
        first substituted, so a query containing the literal ``{content}``
        had the whole batch spliced into it — doubling a prompt that was
        sized to fit exactly, which is the overflow this module prevents."""
        agent = make_agent("done")
        processor = LLMChunkProcessor(agent)
        processor.process(["THE-CHUNK-BODY"], query="what does {content} mean?")
        prompt = agent.llm.chat.call_args.kwargs["messages"][0].content
        assert prompt.count("THE-CHUNK-BODY") == 1
        assert "what does {content} mean?" in prompt

    def test_a_placeholder_inside_a_chunk_is_not_expanded_either(self) -> None:
        agent = make_agent("done")
        processor = LLMChunkProcessor(agent)
        processor.process(["a chunk mentioning {query} in passing"], query="THE-QUESTION")
        prompt = agent.llm.chat.call_args.kwargs["messages"][0].content
        assert prompt.count("THE-QUESTION") == 1
        assert "{query} in passing" in prompt


class TestItemFormatting:
    """How chunks are rendered into a batch."""

    def test_a_scored_chunk_shows_its_score(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        rendered = processor.format_item(("the text", 0.8235), 0)
        assert rendered == "[Chunk 1, score 0.82]\nthe text"

    def test_an_integer_score_is_accepted(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        assert "score 1.00" in processor.format_item(("the text", 1), 0)

    def test_a_plain_string_is_accepted(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        assert processor.format_item("just text", 2) == "[Item 3]\njust text"

    def test_an_unexpected_type_is_rendered_not_dropped(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        assert "42" in processor.format_item(42, 0)

    def test_a_boolean_is_not_mistaken_for_a_score(self) -> None:
        """``bool`` is an ``int``, so ``("text", True)`` would render as
        ``score 1.00`` — a caller's mistake dressed up as a search result."""
        processor = LLMChunkProcessor(make_agent())
        rendered = processor.format_item(("the text", True), 0)
        assert "score" not in rendered
        assert "the text" in rendered

    def test_a_consolidated_item_names_its_level(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        item = ConsolidatedItem(content="summary", metadata={"recursion_level": 2})
        assert processor.format_consolidated_item(item, 0) == (
            "[Consolidated level 2, item 1]\nsummary"
        )


class TestSplitting:
    """Oversized chunks keep their identity."""

    def test_a_split_chunk_keeps_its_score_on_every_piece(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        pieces = processor.split_oversized_item(("word " * 200, 0.75), 200, 0)
        assert len(pieces) > 1
        assert all(score == 0.75 for _, score in pieces)
        assert "".join(text for text, _ in pieces) == "word " * 200

    def test_a_plain_string_still_splits(self) -> None:
        processor = LLMChunkProcessor(make_agent())
        pieces = processor.split_oversized_item("word " * 200, 200, 0)
        assert len(pieces) > 1
        assert all(isinstance(piece, str) for piece in pieces)

    def test_an_oversized_scored_chunk_survives_a_whole_run(self) -> None:
        agent = make_agent("summary")
        config = ProcessingConfig(max_context_chars=300, max_recursion_depth=1)
        processor = LLMChunkProcessor(agent, config=config)
        result = processor.process([("word " * 400, 0.9)], query="q")
        assert result.status is ProcessingStatus.COMPLETED
        assert agent.llm.chat.call_count > 1


class TestExtraction:
    """What reaches the model, and what comes back."""

    def test_the_extraction_prompt_is_used_at_level_zero(self) -> None:
        agent = make_agent("the summary")
        processor = LLMChunkProcessor(agent)
        result = processor.process(["a chunk"], query="what happened?")
        assert result.content == "the summary"
        prompt = agent.llm.chat.call_args.kwargs["messages"][0].content
        assert "Extracted Information:" in prompt
        assert "what happened?" in prompt

    def test_the_consolidation_prompt_is_used_above_level_zero(self) -> None:
        agent = make_agent("y" * 90)
        config = ProcessingConfig(
            max_context_chars=100,
            min_items_for_recursion=1,
            max_recursion_depth=1,
            separator="\n",
        )
        processor = LLMChunkProcessor(agent, config=config)
        processor.process([("x" * 60, 0.5)] * 6, query="q")
        prompts = [call.kwargs["messages"][0].content for call in agent.llm.chat.call_args_list]
        assert any("Extracted Information:" in prompt for prompt in prompts)
        assert any("Consolidated Information:" in prompt for prompt in prompts)

    def test_the_response_is_stripped(self) -> None:
        agent = make_agent("  padded  \n")
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")
        assert result.content == "padded"

    def test_temperature_and_max_tokens_reach_the_client(self) -> None:
        agent = make_agent("ok")
        processor = LLMChunkProcessor(agent, temperature=0.05, max_tokens=256)
        processor.process(["chunk"], query="q")
        assert agent.llm.chat.call_args.kwargs["temperature"] == 0.05
        assert agent.llm.chat.call_args.kwargs["max_tokens"] == 256

    def test_the_agent_defaults_apply_when_not_overridden(self) -> None:
        agent = make_agent("ok")
        agent.temperature = 0.42
        LLMChunkProcessor(agent).process(["chunk"], query="q")
        assert agent.llm.chat.call_args.kwargs["temperature"] == 0.42

    def test_a_failing_model_is_recorded_not_raised(self) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(side_effect=ConnectionError("the server is down"))
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")
        assert result.status is ProcessingStatus.FAILED
        assert result.failed_batches == [0]

    @pytest.mark.parametrize("stop_reason", ["max_tokens", "length"])
    def test_a_truncated_completion_fails_its_batch(self, stop_reason: str) -> None:
        """A completion cut at the output ceiling has lost passages, so it is
        a failed batch, not a digest that reads as complete."""
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(
            return_value=LLMResponse(
                content="the first half of a pass",
                model="test:model",
                input_tokens=1,
                output_tokens=1,
                stop_reason=stop_reason,
            )
        )
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")
        assert result.status is ProcessingStatus.FAILED
        assert result.failed_batches == [0]
        assert result.content == ""

    def test_extraction_is_counted_by_the_agent_metrics(self) -> None:
        agent = make_agent("ok")
        LLMChunkProcessor(agent).process(["chunk"], query="q")
        assert agent.metrics.total_requests == 1


class TestStructuredExtraction:
    """The JSON-mode path, which reads the model's own confidence."""

    def test_content_and_confidence_come_from_the_json(self) -> None:
        agent = make_agent('{"extracted_content": "found it", "confidence": 0.6}')
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        result = processor.process(["chunk"], query="q")
        assert result.content == "found it"
        assert result.final_result.confidence == 0.6

    def test_key_findings_are_carried_on_the_metadata(self) -> None:
        agent = make_agent('{"extracted_content": "x", "key_findings": ["one", "two"]}')
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        result = processor.process(["chunk"], query="q")
        assert result.final_result.metadata["key_findings"] == ["one", "two"]

    def test_a_missing_confidence_falls_back_to_the_default(self) -> None:
        agent = make_agent('{"extracted_content": "x"}')
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        result = processor.process(["chunk"], query="q")
        assert result.final_result.confidence == pytest.approx(0.9)

    def test_an_unusable_confidence_falls_back_to_the_default(self) -> None:
        agent = make_agent('{"extracted_content": "x", "confidence": "very"}')
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        result = processor.process(["chunk"], query="q")
        assert result.final_result.confidence == pytest.approx(0.9)

    def test_a_confidence_outside_the_range_is_clamped(self) -> None:
        """``min_confidence_threshold`` and the weighted merge both assume
        0.0-1.0; a model reporting 1.4 must not outrank everything."""
        agent = make_agent('{"extracted_content": "x", "confidence": 1.4}')
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        result = processor.process(["chunk"], query="q")
        assert result.final_result.confidence == 1.0

    @pytest.mark.parametrize(
        "answer",
        [
            '{"extracted_content": null, "confidence": 0.8}',
            '{"extracted_content": {"a": 1}, "confidence": 0.8, "key_findings": ["k"]}',
            '{"extracted_content": ["x"], "confidence": 0.8}',
            '{"extracted_content": 5, "confidence": 0.8}',
            '{"confidence": 0.8}',
        ],
        ids=["null", "object", "list", "number", "absent"],
    )
    def test_content_that_is_not_text_is_no_content(
        self, answer: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """#570: ``str(None)`` stored the literal ``"None"`` at the model's
        confidence, and an object its ``repr``; no content carries none."""
        agent = make_agent(answer)
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        with caplog.at_level(logging.WARNING, logger="bmlib.context_processor.llm_processor"):
            answer_read = processor._extract_structured("p", "batch 0")
        assert answer_read == ("", 0.0, [])
        assert "extracted_content that is not text in batch 0" in caplog.text

    def test_malformed_json_is_repaired_by_the_agent(self) -> None:
        agent = make_agent('{"extracted_content": "recovered", "confidence": 0.5,}')
        processor = LLMChunkProcessor(agent, use_structured_output=True)
        result = processor.process(["chunk"], query="q")
        assert result.content == "recovered"

    def test_structured_output_applies_only_to_the_first_level(self) -> None:
        """Consolidation merges prose; asking for JSON there would demand the
        model re-wrap a summary it was told to write as text."""
        agent = make_agent(*['{"extracted_content": "' + "y" * 90 + '"}'] * 6)
        config = ProcessingConfig(
            max_context_chars=100,
            min_items_for_recursion=1,
            max_recursion_depth=1,
            separator="\n",
        )
        processor = LLMChunkProcessor(agent, use_structured_output=True, config=config)
        processor.process([("x" * 60, 0.5)] * 6, query="q")
        json_modes = [call.kwargs.get("json_mode") for call in agent.llm.chat.call_args_list]
        assert json_modes[0] is True
        assert json_modes[-1] is not True


def _answer(content: str = "extracted") -> LLMResponse:
    return LLMResponse(content=content, model="test:model", input_tokens=1, output_tokens=1)


class _HTTPError(Exception):
    """An SDK's HTTP-status exception, as ``anthropic``/``openai``'s
    ``APIStatusError`` and ``ollama.ResponseError`` carry one."""

    def __init__(self, status_code: int | None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class TestTheModelCallIsRetried:
    """#641: a failed request is retried with backoff (rule 7), since
    ollama-python retries nothing; Rust's ``LlmContextModel`` does the same."""

    def test_a_failure_then_an_answer_is_one_result(self, _no_backoff: MagicMock) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(side_effect=[ConnectionError("down"), _answer("the digest")])
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")

        assert result.status is ProcessingStatus.COMPLETED
        assert result.content == "the digest"
        assert agent.llm.chat.call_count == 2
        assert [call.args for call in _no_backoff.call_args_list] == [(1.0,)]

    def test_the_structured_path_is_retried_too(self) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        answer = _answer('{"extracted_content": "the digest", "confidence": 0.8}')
        agent.llm.chat = MagicMock(side_effect=[ConnectionError("down"), answer])
        result = LLMChunkProcessor(agent, use_structured_output=True).process(["chunk"], query="q")

        assert result.content == "the digest"
        assert agent.llm.chat.call_count == 2

    def test_failures_past_the_attempts_fail_the_batch(self, _no_backoff: MagicMock) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(side_effect=ConnectionError("down"))
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")

        assert result.status is ProcessingStatus.FAILED
        assert agent.llm.chat.call_count == CONTEXT_MODEL_ATTEMPTS
        # Doubling from the base delay, none after the last attempt.
        assert [call.args for call in _no_backoff.call_args_list] == [(1.0,), (2.0,)]

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("unknown provider"),
            ImportError("no SDK"),
            NotImplementedError("no such feature"),
            TypeError("a bmlib defect"),
        ],
    )
    def test_what_no_retry_cures_is_not_retried(self, error: Exception) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(side_effect=error)
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")

        assert result.status is ProcessingStatus.FAILED
        assert agent.llm.chat.call_count == 1

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 501])
    def test_an_http_refusal_is_not_retried(self, status: int, _no_backoff: MagicMock) -> None:
        # #646: a bad key, a missing model or a prompt too long is refused again.
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(side_effect=_HTTPError(status))
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")

        assert result.status is ProcessingStatus.FAILED
        assert agent.llm.chat.call_count == 1
        _no_backoff.assert_not_called()

    # -1 is ollama-python's "no HTTP status"; True is an int that is no status.
    @pytest.mark.parametrize("status", [408, 429, 500, 503, 529, -1, None, True])
    def test_a_status_a_retry_may_cure_is_retried(
        self, status: int | None, _no_backoff: MagicMock
    ) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        agent.llm.chat = MagicMock(side_effect=[_HTTPError(status), _answer("the digest")])
        result = LLMChunkProcessor(agent).process(["chunk"], query="q")

        assert result.content == "the digest"
        assert agent.llm.chat.call_count == 2

    def test_the_reply_is_read_once_and_never_resent(self) -> None:
        # ``float()`` of an integer past a double's range raises OverflowError;
        # reading the reply is no request, so it is neither retried nor fatal.
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        huge = "1" + "0" * 400
        answer = _answer('{"extracted_content": "the digest", "confidence": ' + huge + "}")
        agent.llm.chat = MagicMock(return_value=answer)
        result = LLMChunkProcessor(agent, use_structured_output=True).process(["chunk"], query="q")

        assert result.status is ProcessingStatus.COMPLETED
        assert result.content == "the digest"
        assert agent.llm.chat.call_count == 1

    def test_a_truncated_completion_is_not_retried(self) -> None:
        agent = BaseAgent(llm=MagicMock(), model="test:model")
        cut = LLMResponse(
            content="half", model="m", input_tokens=1, output_tokens=1, stop_reason="length"
        )
        agent.llm.chat = MagicMock(return_value=cut)
        LLMChunkProcessor(agent).process(["chunk"], query="q")

        assert agent.llm.chat.call_count == 1


class _StoppingAgent(BaseAgent):
    """An agent reading a stop, as the PICO agent does."""

    def __init__(self, stop: threading.Event) -> None:
        super().__init__(llm=MagicMock(), model="test:model")
        self._stop = stop

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def chat(self, messages: Any, **kwargs: Any) -> LLMResponse:
        if self._stop.is_set():
            raise ChatStoppedError("stopped")
        return super().chat(messages, **kwargs)


class TestAStopEndsTheRun:
    """#642: a stop ends the map-reduce, propagated and unlogged, rather than
    failing every batch left at ERROR."""

    def test_a_refused_request_ends_the_run_unlogged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        stop = threading.Event()
        stop.set()
        agent = _StoppingAgent(stop)
        processor = LLMChunkProcessor(agent, config=ProcessingConfig(max_context_chars=200))

        with caplog.at_level(logging.WARNING), pytest.raises(ProcessingStoppedError):
            processor.process(["a chunk of text " * 4] * 6, query="q")
        assert agent.llm.chat.call_count == 0
        assert caplog.records == []

    def test_a_failure_while_stopped_waits_for_no_retry(self, _no_backoff: MagicMock) -> None:
        stop = threading.Event()
        agent = _StoppingAgent(stop)

        def fail(**kwargs: Any) -> LLMResponse:
            stop.set()
            raise ConnectionError("the server went away")

        agent.llm.chat = MagicMock(side_effect=fail)
        with pytest.raises(ProcessingStoppedError) as raised:
            LLMChunkProcessor(agent).process(["chunk"], query="q")
        assert isinstance(raised.value.__cause__, ConnectionError)
        assert agent.llm.chat.call_count == 1
        _no_backoff.assert_not_called()
