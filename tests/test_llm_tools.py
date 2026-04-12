# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for LLM tool-calling support across providers.

Three layers of tests:

1. Data type tests — backwards compatibility of LLMMessage/LLMResponse
   and basic construction of the new LLMToolDefinition / LLMToolCall.
2. Per-provider converter tests — exercise each provider's pure-function
   converters (_convert_tool_def_to_*, _convert_tool_choice_to_*,
   _convert_messages_to_*) in isolation. No SDK mocking needed.
3. Per-provider end-to-end tests with mocked SDK clients — verify the
   full chat() pipeline correctly forwards tools, parses tool_calls
   from responses, and round-trips a multi-turn conversation.

Integration tests against real APIs are not in this file. They live
in tests/test_llm_tools_integration.py (created in Phase 1i) and are
marked with @pytest.mark.integration so they can be skipped in CI.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from bmlib.llm import (
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)


# ===========================================================================
# 1. DATA TYPE TESTS
# ===========================================================================
# Verify the new types work and the existing ones remain backwards compatible.


class TestLLMMessageBackwardsCompat:
    """LLMMessage existing two-argument construction must keep working."""

    def test_old_construction_still_works(self):
        msg = LLMMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.tool_call_id is None
        assert msg.tool_calls is None

    def test_assistant_message_with_text(self):
        msg = LLMMessage(role="assistant", content="Hi there")
        assert msg.role == "assistant"
        assert msg.tool_call_id is None
        assert msg.tool_calls is None

    def test_system_message(self):
        msg = LLMMessage(role="system", content="You are helpful")
        assert msg.role == "system"


class TestLLMMessageToolExtensions:
    """New tool-related fields on LLMMessage."""

    def test_tool_role_with_id(self):
        msg = LLMMessage(role="tool", content='{"result": 5}', tool_call_id="t1")
        assert msg.role == "tool"
        assert msg.tool_call_id == "t1"

    def test_assistant_with_tool_calls(self):
        call = LLMToolCall(id="t1", name="add", arguments={"a": 2, "b": 3})
        msg = LLMMessage(role="assistant", content="", tool_calls=[call])
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "add"


class TestLLMResponseBackwardsCompat:
    """LLMResponse existing constructors and the auto-total field must keep working."""

    def test_existing_constructor(self):
        resp = LLMResponse(content="Hi", input_tokens=10, output_tokens=5)
        assert resp.total_tokens == 15
        assert resp.tool_calls is None

    def test_existing_constructor_with_explicit_total(self):
        resp = LLMResponse(content="Hi", input_tokens=10, output_tokens=5, total_tokens=20)
        assert resp.total_tokens == 20

    def test_response_with_tool_calls(self):
        call = LLMToolCall(id="t1", name="add", arguments={"a": 2})
        resp = LLMResponse(content="", tool_calls=[call])
        assert resp.tool_calls is not None
        assert resp.tool_calls[0].id == "t1"


class TestLLMToolDefinition:
    def test_construction(self):
        tool = LLMToolDefinition(
            name="add",
            description="Add two integers",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        )
        assert tool.name == "add"
        assert tool.description == "Add two integers"
        assert tool.parameters["required"] == ["a", "b"]

    def test_default_parameters(self):
        tool = LLMToolDefinition(name="noop", description="Does nothing")
        assert tool.parameters == {}


class TestLLMToolCall:
    def test_construction(self):
        call = LLMToolCall(id="call_0", name="add", arguments={"a": 2, "b": 3})
        assert call.id == "call_0"
        assert call.name == "add"
        assert call.arguments == {"a": 2, "b": 3}

    def test_default_arguments(self):
        call = LLMToolCall(id="x", name="ping")
        assert call.arguments == {}


# ===========================================================================
# 2. PER-PROVIDER CONVERTER TESTS — pure functions, no mocking
# ===========================================================================


class TestAnthropicConverters:
    """Anthropic uses input_schema (not parameters), no tool role,
    user-role tool_result blocks."""

    def test_tool_def_uses_input_schema(self):
        from bmlib.llm.providers.anthropic import _convert_tool_def_to_anthropic

        tool = LLMToolDefinition(
            name="add",
            description="Add a+b",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
        out = _convert_tool_def_to_anthropic(tool)
        assert out["name"] == "add"
        assert out["description"] == "Add a+b"
        assert "input_schema" in out
        assert "parameters" not in out
        assert out["input_schema"] == {
            "type": "object",
            "properties": {"a": {"type": "integer"}},
        }

    def test_tool_def_empty_parameters_becomes_object_schema(self):
        from bmlib.llm.providers.anthropic import _convert_tool_def_to_anthropic

        tool = LLMToolDefinition(name="noop", description="No-op")
        out = _convert_tool_def_to_anthropic(tool)
        assert out["input_schema"] == {"type": "object", "properties": {}}

    def test_tool_choice_auto_returns_none(self):
        from bmlib.llm.providers.anthropic import _convert_tool_choice_to_anthropic

        # Anthropic's default is to omit the parameter entirely
        assert _convert_tool_choice_to_anthropic("auto") is None

    def test_tool_choice_required_maps_to_any(self):
        from bmlib.llm.providers.anthropic import _convert_tool_choice_to_anthropic

        assert _convert_tool_choice_to_anthropic("required") == {"type": "any"}
        assert _convert_tool_choice_to_anthropic("any") == {"type": "any"}

    def test_tool_choice_none(self):
        from bmlib.llm.providers.anthropic import _convert_tool_choice_to_anthropic

        assert _convert_tool_choice_to_anthropic("none") == {"type": "none"}

    def test_tool_choice_specific_tool_name(self):
        from bmlib.llm.providers.anthropic import _convert_tool_choice_to_anthropic

        assert _convert_tool_choice_to_anthropic("add") == {
            "type": "tool",
            "name": "add",
        }

    def test_messages_system_extracted(self):
        from bmlib.llm.providers.anthropic import _convert_messages_to_anthropic

        msgs = [
            LLMMessage(role="system", content="You are helpful"),
            LLMMessage(role="user", content="Hi"),
        ]
        system, out = _convert_messages_to_anthropic(msgs)
        assert system == "You are helpful"
        assert len(out) == 1
        assert out[0] == {"role": "user", "content": "Hi"}

    def test_messages_plain_pass_through(self):
        from bmlib.llm.providers.anthropic import _convert_messages_to_anthropic

        msgs = [
            LLMMessage(role="user", content="One"),
            LLMMessage(role="assistant", content="Two"),
            LLMMessage(role="user", content="Three"),
        ]
        system, out = _convert_messages_to_anthropic(msgs)
        assert system == ""
        assert out == [
            {"role": "user", "content": "One"},
            {"role": "assistant", "content": "Two"},
            {"role": "user", "content": "Three"},
        ]

    def test_messages_assistant_with_tool_calls_emits_tool_use_blocks(self):
        from bmlib.llm.providers.anthropic import _convert_messages_to_anthropic

        msgs = [
            LLMMessage(
                role="assistant",
                content="Let me add those.",
                tool_calls=[
                    LLMToolCall(id="toolu_01", name="add", arguments={"a": 2, "b": 3})
                ],
            ),
        ]
        _, out = _convert_messages_to_anthropic(msgs)
        assert len(out) == 1
        assert out[0]["role"] == "assistant"
        blocks = out[0]["content"]
        assert isinstance(blocks, list)
        assert len(blocks) == 2
        assert blocks[0] == {"type": "text", "text": "Let me add those."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["id"] == "toolu_01"
        assert blocks[1]["name"] == "add"
        assert blocks[1]["input"] == {"a": 2, "b": 3}

    def test_messages_tool_role_becomes_user_with_tool_result(self):
        from bmlib.llm.providers.anthropic import _convert_messages_to_anthropic

        msgs = [
            LLMMessage(role="tool", content="5", tool_call_id="toolu_01"),
        ]
        _, out = _convert_messages_to_anthropic(msgs)
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert isinstance(out[0]["content"], list)
        block = out[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "toolu_01"
        assert block["content"] == "5"

    def test_messages_parallel_tool_results_merge_into_one_user_message(self):
        from bmlib.llm.providers.anthropic import _convert_messages_to_anthropic

        msgs = [
            LLMMessage(role="tool", content="5", tool_call_id="t1"),
            LLMMessage(role="tool", content="6", tool_call_id="t2"),
        ]
        _, out = _convert_messages_to_anthropic(msgs)
        # Both tool results should fold into a single user message
        # with two tool_result blocks
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert len(out[0]["content"]) == 2
        assert out[0]["content"][0]["tool_use_id"] == "t1"
        assert out[0]["content"][1]["tool_use_id"] == "t2"

    def test_messages_tool_result_after_other_user_starts_new_message(self):
        from bmlib.llm.providers.anthropic import _convert_messages_to_anthropic

        msgs = [
            LLMMessage(role="user", content="Hi"),
            LLMMessage(role="tool", content="5", tool_call_id="t1"),
        ]
        _, out = _convert_messages_to_anthropic(msgs)
        # The tool result should NOT merge into the plain text user
        # message because that user message has string content, not
        # tool_result blocks. Each gets its own entry.
        assert len(out) == 2
        assert out[0] == {"role": "user", "content": "Hi"}
        assert out[1]["role"] == "user"
        assert out[1]["content"][0]["type"] == "tool_result"


class TestOllamaConverters:
    """Ollama uses OpenAI-style nested function specs and supports
    a 'tool' role natively."""

    def test_tool_def_wraps_in_function_spec(self):
        from bmlib.llm.providers.ollama import _convert_tool_def_to_ollama

        tool = LLMToolDefinition(
            name="add",
            description="Add a+b",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
        out = _convert_tool_def_to_ollama(tool)
        assert out == {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Add a+b",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}},
                },
            },
        }

    def test_tool_def_empty_parameters(self):
        from bmlib.llm.providers.ollama import _convert_tool_def_to_ollama

        tool = LLMToolDefinition(name="noop", description="No-op")
        out = _convert_tool_def_to_ollama(tool)
        assert out["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_messages_plain_pass_through(self):
        from bmlib.llm.providers.ollama import _convert_messages_to_ollama

        msgs = [
            LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="u"),
            LLMMessage(role="assistant", content="a"),
        ]
        out = _convert_messages_to_ollama(msgs)
        assert out == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]

    def test_messages_tool_role_with_id(self):
        from bmlib.llm.providers.ollama import _convert_messages_to_ollama

        msgs = [LLMMessage(role="tool", content="5", tool_call_id="call_0")]
        out = _convert_messages_to_ollama(msgs)
        assert out == [
            {"role": "tool", "content": "5", "tool_call_id": "call_0"}
        ]

    def test_messages_tool_role_without_id(self):
        from bmlib.llm.providers.ollama import _convert_messages_to_ollama

        msgs = [LLMMessage(role="tool", content="result")]
        out = _convert_messages_to_ollama(msgs)
        # Some local models don't use tool_call_id; the field should
        # be omitted entirely rather than set to None
        assert out == [{"role": "tool", "content": "result"}]
        assert "tool_call_id" not in out[0]

    def test_messages_assistant_with_tool_calls(self):
        from bmlib.llm.providers.ollama import _convert_messages_to_ollama

        msgs = [
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="call_0", name="add", arguments={"a": 2, "b": 3}
                    )
                ],
            ),
        ]
        out = _convert_messages_to_ollama(msgs)
        assert len(out) == 1
        entry = out[0]
        assert entry["role"] == "assistant"
        # Ollama accepts dict arguments directly (unlike OpenAI's JSON string)
        assert entry["tool_calls"][0]["function"]["arguments"] == {"a": 2, "b": 3}
        assert entry["tool_calls"][0]["id"] == "call_0"


class TestOpenAICompatConverters:
    """OpenAI requires arguments as JSON strings; tool_call_id required
    on tool result messages; canonical wire format."""

    def test_tool_def_wraps_in_function_spec(self):
        from bmlib.llm.providers.openai_compat import _convert_tool_def_to_openai

        tool = LLMToolDefinition(
            name="add",
            description="Add a+b",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
        out = _convert_tool_def_to_openai(tool)
        assert out == {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Add a+b",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}},
                },
            },
        }

    def test_tool_choice_auto(self):
        from bmlib.llm.providers.openai_compat import _convert_tool_choice_to_openai

        assert _convert_tool_choice_to_openai("auto") == "auto"

    def test_tool_choice_required(self):
        from bmlib.llm.providers.openai_compat import _convert_tool_choice_to_openai

        assert _convert_tool_choice_to_openai("required") == "required"
        # bmlib accepts "any" as an Anthropic-style alias
        assert _convert_tool_choice_to_openai("any") == "required"

    def test_tool_choice_none(self):
        from bmlib.llm.providers.openai_compat import _convert_tool_choice_to_openai

        assert _convert_tool_choice_to_openai("none") == "none"

    def test_tool_choice_specific_name(self):
        from bmlib.llm.providers.openai_compat import _convert_tool_choice_to_openai

        assert _convert_tool_choice_to_openai("add") == {
            "type": "function",
            "function": {"name": "add"},
        }

    def test_tool_choice_empty_string_defaults_to_auto(self):
        from bmlib.llm.providers.openai_compat import _convert_tool_choice_to_openai

        assert _convert_tool_choice_to_openai("") == "auto"

    def test_messages_plain_pass_through(self):
        from bmlib.llm.providers.openai_compat import _convert_messages_to_openai

        msgs = [
            LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="u"),
        ]
        out = _convert_messages_to_openai(msgs)
        assert out == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
        ]

    def test_messages_tool_role_requires_tool_call_id(self):
        from bmlib.llm.providers.openai_compat import _convert_messages_to_openai

        msgs = [LLMMessage(role="tool", content="5", tool_call_id="call_0")]
        out = _convert_messages_to_openai(msgs)
        assert out == [
            {"role": "tool", "content": "5", "tool_call_id": "call_0"}
        ]

    def test_messages_assistant_tool_calls_arguments_serialised_to_json_string(self):
        from bmlib.llm.providers.openai_compat import _convert_messages_to_openai

        msgs = [
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="call_0", name="add", arguments={"a": 2, "b": 3}
                    )
                ],
            ),
        ]
        out = _convert_messages_to_openai(msgs)
        # OpenAI requires arguments as a JSON STRING, not a dict
        args = out[0]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args) == {"a": 2, "b": 3}

    def test_messages_assistant_only_tool_calls_has_none_content(self):
        from bmlib.llm.providers.openai_compat import _convert_messages_to_openai

        msgs = [
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    LLMToolCall(id="x", name="ping", arguments={})
                ],
            ),
        ]
        out = _convert_messages_to_openai(msgs)
        # Empty string content becomes None per OpenAI's preferred format
        assert out[0]["content"] is None


# ===========================================================================
# 3. END-TO-END PROVIDER TESTS — mocked SDK clients
# ===========================================================================


class TestAnthropicProviderToolCalling:
    """Mock the anthropic SDK and verify the chat() pipeline."""

    def _make_mock_client_returning_tool_use(self, tool_name="add", tool_input=None):
        """Build a mock anthropic Anthropic client that returns a tool_use block.

        Uses bare ``object()`` instances for the content blocks instead of
        ``MagicMock`` because MagicMock auto-creates *every* attribute that's
        accessed, which would let the provider's "block has 'text' attr"
        fall-through branch fire on a tool_use block. We want crisp positive
        and negative cases per block type.
        """
        if tool_input is None:
            tool_input = {"a": 2, "b": 3}

        class _TextBlock:
            type = "text"
            text = "Let me calculate that."

        class _ToolUseBlock:
            type = "tool_use"
            id = "toolu_01abc"

            def __init__(self, name, input_):
                self.name = name
                self.input = input_

        mock_response = MagicMock()
        mock_response.content = [_TextBlock(), _ToolUseBlock(tool_name, tool_input)]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 20
        mock_response.stop_reason = "tool_use"

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_chat_forwards_tools_in_anthropic_format(self):
        from bmlib.llm.providers.anthropic import AnthropicProvider

        p = AnthropicProvider(api_key="test-key")
        p._client = self._make_mock_client_returning_tool_use()

        tool = LLMToolDefinition(
            name="add",
            description="Add a+b",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        )
        msgs = [LLMMessage(role="user", content="What is 2+3?")]
        p.chat(msgs, model="claude-sonnet-4-20250514", tools=[tool])

        call_kwargs = p._client.messages.create.call_args.kwargs
        # Tools should be in Anthropic's input_schema format, not parameters
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 1
        sent_tool = call_kwargs["tools"][0]
        assert sent_tool["name"] == "add"
        assert "input_schema" in sent_tool
        assert "parameters" not in sent_tool

    def test_chat_parses_tool_use_blocks_into_tool_calls(self):
        from bmlib.llm.providers.anthropic import AnthropicProvider

        p = AnthropicProvider(api_key="test-key")
        p._client = self._make_mock_client_returning_tool_use(
            tool_name="add", tool_input={"a": 5, "b": 7}
        )

        tool = LLMToolDefinition(
            name="add", description="Add", parameters={}
        )
        msgs = [LLMMessage(role="user", content="Add 5 and 7")]
        result = p.chat(msgs, model="claude-sonnet-4-20250514", tools=[tool])

        # Text content from the text block should be in content
        assert "Let me calculate" in result.content
        # Tool call should be parsed out
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call.id == "toolu_01abc"
        assert call.name == "add"
        assert call.arguments == {"a": 5, "b": 7}
        assert result.stop_reason == "tool_use"

    def test_chat_without_tools_keeps_old_behavior(self):
        """Backwards compat: chat() without tools= must work exactly as before."""
        from bmlib.llm.providers.anthropic import AnthropicProvider

        p = AnthropicProvider(api_key="test-key")

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hi there"

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 3
        mock_response.stop_reason = "end_turn"
        p._client = MagicMock()
        p._client.messages.create.return_value = mock_response

        msgs = [LLMMessage(role="user", content="Hello")]
        result = p.chat(msgs, model="claude-sonnet-4-20250514")

        assert result.content == "Hi there"
        assert result.tool_calls is None  # default — no tool calls
        # The request should NOT have a tools field
        call_kwargs = p._client.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs


class TestOllamaProviderToolCalling:
    """Mock the ollama-python client and verify chat() round-trips tool calls."""

    def _make_mock_ollama_client_with_tool_call(self):
        """Build a mock ollama Client that returns a message with tool_calls."""
        # ollama >=0.4 returns Pydantic models accessed via attribute syntax.
        # Build a MagicMock that behaves like one.
        function_obj = MagicMock()
        function_obj.name = "add"
        function_obj.arguments = {"a": 2, "b": 3}

        tool_call_obj = MagicMock()
        tool_call_obj.function = function_obj
        # Some Ollama models leave id unset — getattr returns None
        tool_call_obj.id = None

        message_obj = MagicMock()
        message_obj.content = "I'll use the add tool."
        message_obj.tool_calls = [tool_call_obj]

        response_obj = MagicMock()
        response_obj.message = message_obj
        response_obj.prompt_eval_count = 30
        response_obj.eval_count = 10

        mock_client = MagicMock()
        mock_client.chat.return_value = response_obj
        return mock_client

    def test_chat_forwards_tools_in_function_spec_format(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        p = OllamaProvider()
        p._client = self._make_mock_ollama_client_with_tool_call()

        tool = LLMToolDefinition(
            name="add",
            description="Add a+b",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
        msgs = [LLMMessage(role="user", content="Add 2 and 3")]
        p.chat(msgs, model="gemma4:26b", tools=[tool])

        call_kwargs = p._client.chat.call_args.kwargs
        assert "tools" in call_kwargs
        sent_tool = call_kwargs["tools"][0]
        assert sent_tool["type"] == "function"
        assert sent_tool["function"]["name"] == "add"
        assert "parameters" in sent_tool["function"]

    def test_chat_parses_tool_calls_from_response(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        p = OllamaProvider()
        p._client = self._make_mock_ollama_client_with_tool_call()

        tool = LLMToolDefinition(name="add", description="Add", parameters={})
        msgs = [LLMMessage(role="user", content="Add 2 and 3")]
        result = p.chat(msgs, model="gemma4:26b", tools=[tool])

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call.name == "add"
        assert call.arguments == {"a": 2, "b": 3}
        # Synthesised id when ollama doesn't provide one
        assert call.id == "call_0"
        assert result.stop_reason == "tool_calls"

    def test_chat_handles_tool_arguments_as_json_string(self):
        """Some Ollama models return arguments as a JSON string instead of a dict."""
        from bmlib.llm.providers.ollama import OllamaProvider

        function_obj = MagicMock()
        function_obj.name = "add"
        function_obj.arguments = '{"a": 4, "b": 5}'  # JSON STRING

        tool_call_obj = MagicMock()
        tool_call_obj.function = function_obj
        tool_call_obj.id = "call_xyz"

        message_obj = MagicMock()
        message_obj.content = ""
        message_obj.tool_calls = [tool_call_obj]

        response_obj = MagicMock()
        response_obj.message = message_obj
        response_obj.prompt_eval_count = 20
        response_obj.eval_count = 5

        p = OllamaProvider()
        p._client = MagicMock()
        p._client.chat.return_value = response_obj

        tool = LLMToolDefinition(name="add", description="Add", parameters={})
        result = p.chat(
            [LLMMessage(role="user", content="Add 4 and 5")],
            model="gemma4:26b",
            tools=[tool],
        )
        assert result.tool_calls is not None
        # Provider parsed the JSON string into a dict
        assert result.tool_calls[0].arguments == {"a": 4, "b": 5}
        assert result.tool_calls[0].id == "call_xyz"

    def test_chat_without_tools_returns_none_tool_calls(self):
        """Backwards compat: existing chat() callers must keep working."""
        from bmlib.llm.providers.ollama import OllamaProvider

        message_obj = MagicMock()
        message_obj.content = "Hello"
        message_obj.tool_calls = None  # no tool calls

        response_obj = MagicMock()
        response_obj.message = message_obj
        response_obj.prompt_eval_count = 5
        response_obj.eval_count = 2

        p = OllamaProvider()
        p._client = MagicMock()
        p._client.chat.return_value = response_obj

        result = p.chat(
            [LLMMessage(role="user", content="Hi")],
            model="gemma4:26b",
        )
        assert result.content == "Hello"
        assert result.tool_calls is None
        assert result.stop_reason == "stop"


class TestOpenAICompatProviderToolCalling:
    """Mock the openai SDK and verify chat() round-trips tool calls."""

    def _make_provider(self):
        """Concrete subclass of OpenAICompatibleProvider for testing."""
        from bmlib.llm.providers.base import ModelMetadata, ModelPricing
        from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider

        class StubProvider(OpenAICompatibleProvider):
            PROVIDER_NAME = "stub"
            DISPLAY_NAME = "Stub"
            DESCRIPTION = "Test"
            WEBSITE_URL = "https://stub.test"
            SETUP_INSTRUCTIONS = "N/A"
            API_KEY_ENV_VAR = "STUB_KEY"
            DEFAULT_BASE_URL = "https://api.stub/v1"
            DEFAULT_MODEL = "stub-model"
            FALLBACK_MODELS = [
                ModelMetadata(
                    model_id="stub-model",
                    display_name="Stub",
                    context_window=128_000,
                    pricing=ModelPricing(1.0, 2.0),
                ),
            ]
            MODEL_PRICING = {"stub-model": ModelPricing(1.0, 2.0)}

        return StubProvider(api_key="test-key")

    def _make_mock_client_with_tool_call(self):
        """Build a mock openai Client returning a tool_calls message."""
        function_obj = MagicMock()
        function_obj.name = "add"
        function_obj.arguments = '{"a": 2, "b": 3}'  # OpenAI sends as JSON string

        tool_call_obj = MagicMock()
        tool_call_obj.id = "call_abc123"
        tool_call_obj.function = function_obj

        choice_message = MagicMock()
        choice_message.content = None  # Often None when only tool calls
        choice_message.tool_calls = [tool_call_obj]

        choice = MagicMock()
        choice.message = choice_message
        choice.finish_reason = "tool_calls"

        response = MagicMock()
        response.choices = [choice]
        response.usage.prompt_tokens = 40
        response.usage.completion_tokens = 15

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response
        return mock_client

    def test_chat_forwards_tools_with_function_spec(self):
        p = self._make_provider()
        p._client = self._make_mock_client_with_tool_call()

        tool = LLMToolDefinition(
            name="add",
            description="Add",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
        p.chat(
            [LLMMessage(role="user", content="Add 2+3")],
            model="stub-model",
            tools=[tool],
        )

        call_kwargs = p._client.chat.completions.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["type"] == "function"
        assert call_kwargs["tools"][0]["function"]["name"] == "add"
        assert call_kwargs["tool_choice"] == "auto"  # default

    def test_chat_parses_json_string_arguments_to_dict(self):
        p = self._make_provider()
        p._client = self._make_mock_client_with_tool_call()

        tool = LLMToolDefinition(name="add", description="Add", parameters={})
        result = p.chat(
            [LLMMessage(role="user", content="Add")],
            model="stub-model",
            tools=[tool],
        )

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call.id == "call_abc123"
        assert call.name == "add"
        # Arguments parsed from JSON string into dict
        assert call.arguments == {"a": 2, "b": 3}
        assert result.stop_reason == "tool_calls"

    def test_chat_without_tools_old_behavior(self):
        """Backwards compat: existing chat() callers must keep working."""
        p = self._make_provider()

        choice_message = MagicMock()
        choice_message.content = "Hi there"
        choice_message.tool_calls = None

        choice = MagicMock()
        choice.message = choice_message
        choice.finish_reason = "stop"

        response = MagicMock()
        response.choices = [choice]
        response.usage.prompt_tokens = 5
        response.usage.completion_tokens = 2

        p._client = MagicMock()
        p._client.chat.completions.create.return_value = response

        result = p.chat([LLMMessage(role="user", content="Hi")], model="stub-model")
        assert result.content == "Hi there"
        assert result.tool_calls is None
        # No tools field in the request
        call_kwargs = p._client.chat.completions.create.call_args.kwargs
        assert "tools" not in call_kwargs


# ===========================================================================
# 4. CAPABILITY CHECK & ERROR HANDLING
# ===========================================================================


class TestCapabilityCheck:
    """LLMClient.chat() must raise NotImplementedError if tools= is
    passed to a provider that doesn't support tool calling."""

    def test_provider_supports_tools_helper_returns_true_for_known(self):
        from bmlib.llm.client import _provider_supports_tools

        for name in ("anthropic", "openai", "deepseek", "mistral", "gemini", "ollama"):
            mock_provider = MagicMock()
            mock_provider.PROVIDER_NAME = name
            assert _provider_supports_tools(mock_provider), name

    def test_provider_supports_tools_helper_returns_false_for_unknown(self):
        from bmlib.llm.client import _provider_supports_tools

        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "imaginary"
        assert not _provider_supports_tools(mock_provider)

    def test_chat_raises_for_unsupported_provider_when_tools_passed(self):
        """If a future provider gets registered without tool support,
        passing tools= should raise NotImplementedError before any
        network round-trip happens."""
        from bmlib.llm.client import LLMClient

        client = LLMClient(default_provider="anthropic", anthropic_api_key="test-key")

        # Mock a provider that does NOT support tools
        unsupported_provider = MagicMock()
        unsupported_provider.PROVIDER_NAME = "unsupported_xyz"
        unsupported_provider.default_model = "fake-model"
        client._providers["unsupported_xyz"] = unsupported_provider

        tool = LLMToolDefinition(name="x", description="x", parameters={})

        with pytest.raises(NotImplementedError, match="does not support tool calling"):
            client.chat(
                messages=[LLMMessage(role="user", content="Hi")],
                model="unsupported_xyz:fake-model",
                tools=[tool],
            )

        # Critically: the provider's chat() must NOT have been called
        unsupported_provider.chat.assert_not_called()
