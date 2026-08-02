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

from unittest.mock import MagicMock

import pytest

from bmlib.agents.base import BaseAgent
from bmlib.context_processor import (
    ConsolidatedItem,
    LLMChunkProcessor,
    ProcessingConfig,
    ProcessingStatus,
)
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
