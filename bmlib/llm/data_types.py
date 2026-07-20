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

"""Data types for LLM communication.

Provides type-safe dataclasses for messages and responses used across
all providers.

Tool-calling types (``LLMToolDefinition``, ``LLMToolCall``) and the
optional ``tool_calls`` field on :class:`LLMResponse` were added in
the tool-calling feature (bmlib feature/tool-calling branch). They are
purely additive — existing callers that do not use tool calling
continue to work unchanged:

* :class:`LLMMessage` gained a new optional ``tool_call_id`` field
  and its ``role`` literal was widened to include ``"tool"``. Existing
  ``LLMMessage(role="user", content="...")`` calls are unaffected.
* :class:`LLMResponse` gained a new optional ``tool_calls`` field
  appended at the end of the dataclass so positional constructors
  keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class LLMMessage:
    """A message in an LLM conversation.

    Attributes:
        role: The role of the message sender. ``"tool"`` is used when
            sending a tool-call result back to the model in a follow-up
            turn after the model invoked a tool; in that case
            ``tool_call_id`` must reference the original tool call id
            emitted by the model.
        content: The text content of the message. For tool-result
            messages this should be a JSON-encoded string representing
            the tool output. For assistant messages that consist solely
            of tool calls, this may be empty.
        tool_call_id: For ``role="tool"`` messages, the id of the tool
            call this message is responding to. Ignored for other roles.
        tool_calls: For ``role="assistant"`` messages that re-send a
            previous turn in which the model invoked tools, the list of
            tool calls the model emitted. Used by callers who maintain
            their own conversation state across turns: after receiving
            an :class:`LLMResponse` with non-empty ``tool_calls``, the
            caller appends an ``LLMMessage(role="assistant", content=...,
            tool_calls=...)`` to the conversation, then appends one or
            more ``LLMMessage(role="tool", ...)`` messages with the
            tool results, and re-sends the full message list. Ignored
            for non-assistant roles.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[LLMToolCall] | None = None


@dataclass
class LLMToolDefinition:
    """Definition of a tool the model can call.

    Follows the OpenAI function-calling JSON Schema format. Providers
    that need a different shape (e.g. Anthropic's ``input_schema``
    format) convert internally.

    Attributes:
        name: Canonical tool name. Must be unique within a tool list
            and match the regex ``[a-zA-Z0-9_-]{1,64}``.
        description: Human-readable description of what the tool does.
            The model reads this to decide when to call the tool, so
            clarity matters.
        parameters: JSON Schema describing the tool's parameters.
            Typical shape::

                {
                    "type": "object",
                    "properties": {
                        "arg1": {"type": "string", "description": "..."},
                        "arg2": {"type": "integer"},
                    },
                    "required": ["arg1"],
                }

    Example::

        LLMToolDefinition(
            name="add",
            description="Add two integers and return the sum",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        )
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMToolCall:
    """A tool invocation emitted by the model.

    Returned as part of :attr:`LLMResponse.tool_calls` when the model
    decides to call one or more tools instead of (or in addition to)
    producing text content.

    Attributes:
        id: Provider-assigned unique id for this tool call. Must be
            echoed back in the subsequent ``role="tool"`` message's
            ``tool_call_id`` so the model can correlate the result to
            the call.
        name: Name of the tool the model is invoking. Must match one
            of the tool names passed in ``tools=``.
        arguments: Arguments the model is passing to the tool, parsed
            from the model's JSON into a plain dict. Dispatch code is
            responsible for validating against the tool's parameters
            schema before executing the tool.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Response from an LLM request.

    Attributes:
        content: The text response from the model. May be empty when
            the model emits only tool calls and no text.
        model: The model that generated the response.
        input_tokens: Number of input tokens used.
        output_tokens: Number of output tokens generated.
        total_tokens: Total tokens used (input + output).
        stop_reason: Why the model stopped generating. Providers that
            support tools typically use ``"tool_use"`` (Anthropic) or
            ``"tool_calls"`` (OpenAI/Ollama) when the stop was caused
            by tool invocation.
        duration_seconds: Wall-clock time spent in the request.
        tool_calls: Tool invocations the model emitted, or ``None``
            if the model did not call any tool. Populated only for
            providers that support tool calling.
        thinking: The model's reasoning trace, separated from
            ``content``, when the provider returns one (e.g. Ollama
            ``message.thinking``, Anthropic ``thinking`` content
            blocks, DeepSeek ``reasoning_content``). ``None`` when the
            model produced no separated reasoning output. Request it
            with the cross-provider ``think`` kwarg on
            :meth:`~bmlib.llm.client.LLMClient.chat`. Appended after
            ``tool_calls`` so positional constructors keep working.
    """

    content: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    stop_reason: str | None = None
    duration_seconds: float = 0.0
    tool_calls: list[LLMToolCall] | None = None
    thinking: str | None = None

    def __post_init__(self) -> None:
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens


@dataclass
class EmbeddingResponse:
    """Response from an embedding request.

    Attributes:
        embedding: The embedding vector.
        model: The model that generated the embedding.
        dimensions: Number of dimensions in the embedding vector.
        input_tokens: Number of input tokens processed.
    """

    embedding: list[float]
    model: str = ""
    dimensions: int = 0
    input_tokens: int = 0
