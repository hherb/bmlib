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

"""The shared Ollama chat contract, ``tests/fixtures/ollama_chat.json``.

Python's :class:`OllamaProvider` is the oracle: these tests hold it to the
fixture, and Rust's ``tests/llm_ollama_contract.rs`` holds the native port to
the same file, so the two build the same request and read the same answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmlib.llm.data_types import LLMMessage, LLMToolCall, LLMToolDefinition
from bmlib.llm.providers.ollama import OllamaProvider

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ollama_chat.json").read_text(encoding="utf-8")
)

# The neutral request's optional fields, passed to chat() as keyword options.
_OPTIONAL_FIELDS = ("top_p", "json_mode", "think", "num_ctx")


def _messages(raw: list[dict]) -> list[LLMMessage]:
    """Build the fixture's messages as a caller would."""
    return [
        LLMMessage(
            role=m["role"],
            content=m["content"],
            tool_call_id=m.get("tool_call_id"),
            tool_calls=[LLMToolCall(**c) for c in m["tool_calls"]] if "tool_calls" in m else None,
        )
        for m in raw
    ]


class _FakeClient:
    """Stands in for ``ollama.Client``: answers every chat with one body."""

    def __init__(self, body: dict) -> None:
        """Remember the native body every chat answers with."""
        self._body = body

    def chat(self, **kwargs: object) -> dict:
        """Return the scripted native body, whatever was asked."""
        return self._body


@pytest.mark.parametrize("case", FIXTURE["requests"], ids=[c["name"] for c in FIXTURE["requests"]])
def test_request_body(case):
    request = case["request"]
    kwargs: dict[str, object] = {k: request[k] for k in _OPTIONAL_FIELDS if k in request}
    if "tools" in request:
        kwargs["tools"] = [LLMToolDefinition(**t) for t in request["tools"]]
    built = OllamaProvider()._request_kwargs(
        _messages(request["messages"]),
        request["model"],
        request["temperature"],
        request["max_tokens"],
        kwargs,
    )
    assert built == case["body"]


@pytest.mark.parametrize(
    "case", FIXTURE["responses"], ids=[c["name"] for c in FIXTURE["responses"]]
)
def test_response(case):
    provider = OllamaProvider()
    provider._client = _FakeClient(case["body"])
    response = provider.chat(_messages(case["messages"]), model="model-x")
    got = {
        "content": response.content,
        "thinking": response.thinking,
        "tool_calls": (
            [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls]
            if response.tool_calls is not None
            else None
        ),
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "stop_reason": response.stop_reason,
    }
    assert got == case["expected"]
    assert response.model == "model-x"
