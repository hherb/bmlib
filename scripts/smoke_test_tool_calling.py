#!/usr/bin/env python3
"""Manual integration smoke test for bmlib tool calling.

Exercises a complete two-turn tool-calling round-trip against a real
LLM provider. Requires the appropriate API key to be set in the
environment (ANTHROPIC_API_KEY for Anthropic, etc.).

Usage:
    uv run python scripts/smoke_test_tool_calling.py anthropic
    uv run python scripts/smoke_test_tool_calling.py ollama
    uv run python scripts/smoke_test_tool_calling.py deepseek

The test:
  1. Defines a simple deterministic tool (add two integers).
  2. Sends a user message asking the model to compute a sum.
  3. Verifies the model emits a tool call rather than guessing.
  4. Dispatches the tool call locally (pure Python addition).
  5. Sends the tool result back with role="tool".
  6. Verifies the model returns a natural-language answer that
     includes the correct computed result.

Exits 0 on success, 1 on failure, 2 on configuration problem.
"""

from __future__ import annotations

import json
import os
import sys

from bmlib.llm import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)

ADD_TOOL = LLMToolDefinition(
    name="add",
    description=(
        "Add two integers and return their sum. "
        "Always use this tool when asked to add numbers — "
        "do not compute the answer yourself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First integer"},
            "b": {"type": "integer", "description": "Second integer"},
        },
        "required": ["a", "b"],
    },
)


def dispatch_add(args: dict) -> str:
    """Execute the add tool and return the result as a JSON string."""
    a = int(args["a"])
    b = int(args["b"])
    return json.dumps({"result": a + b})


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

PROVIDER_CONFIG = {
    "anthropic": {
        "model": "anthropic:claude-sonnet-4-5-20250929",
        "env_var": "ANTHROPIC_API_KEY",
        "kwargs": {"anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY")},
    },
    "ollama": {
        "model": "ollama:gemma4:26b-a4b-it-q8_0",
        "env_var": None,  # no API key required
        "kwargs": {},
    },
    "deepseek": {
        "model": "deepseek:deepseek-chat",
        "env_var": "DEEPSEEK_API_KEY",
        "kwargs": {"api_key": os.environ.get("DEEPSEEK_API_KEY")},
    },
    "openai": {
        "model": "openai:gpt-4o-mini",
        "env_var": "OPENAI_API_KEY",
        "kwargs": {"api_key": os.environ.get("OPENAI_API_KEY")},
    },
}


def run_test(provider_name: str) -> int:
    cfg = PROVIDER_CONFIG.get(provider_name)
    if cfg is None:
        print(f"ERROR: Unknown provider {provider_name!r}")
        print(f"       Supported: {list(PROVIDER_CONFIG.keys())}")
        return 2

    if cfg["env_var"] and not os.environ.get(cfg["env_var"]):
        print(f"ERROR: {cfg['env_var']} not set in environment")
        return 2

    print(f"=== bmlib tool-calling smoke test ({provider_name}) ===")
    print(f"Model: {cfg['model']}")
    print()

    client = LLMClient(**cfg["kwargs"])

    # --- Turn 1: ask for a sum the model can only compute via the tool ---
    user_msg = LLMMessage(
        role="user",
        content="What is 1,247 + 3,856? Use the add tool.",
    )
    print("[turn 1] user:", user_msg.content)

    try:
        resp: LLMResponse = client.chat(
            messages=[user_msg],
            model=cfg["model"],
            tools=[ADD_TOOL],
            temperature=0.0,
            max_tokens=1024,
        )
    except Exception as exc:
        print(f"\nFAIL: client.chat() raised: {exc!r}")
        return 1

    print(f"[turn 1] response content: {resp.content!r}")
    print(f"[turn 1] tool_calls: {resp.tool_calls}")
    print(f"[turn 1] stop_reason: {resp.stop_reason}")
    print(f"[turn 1] tokens: in={resp.input_tokens} out={resp.output_tokens}")
    print()

    if not resp.tool_calls:
        print("FAIL: model did not emit any tool calls")
        return 1

    if len(resp.tool_calls) != 1:
        print(f"WARN: model emitted {len(resp.tool_calls)} tool calls, expected 1")

    call: LLMToolCall = resp.tool_calls[0]
    if call.name != "add":
        print(f"FAIL: model called tool {call.name!r}, expected 'add'")
        return 1

    # Validate arguments shape
    expected_args = {1247, 3856}
    actual_args = set(call.arguments.values()) if call.arguments else set()
    if actual_args != expected_args:
        print(f"FAIL: tool call arguments {call.arguments!r} do not match expected {expected_args}")
        return 1

    print(f"[dispatch] running add({call.arguments})")
    tool_result = dispatch_add(call.arguments)
    print(f"[dispatch] result: {tool_result}")
    print()

    # --- Turn 2: send the tool result back ---
    # Re-send the assistant turn with tool_calls, then the tool result
    follow_up_messages = [
        user_msg,
        LLMMessage(
            role="assistant",
            content=resp.content,
            tool_calls=resp.tool_calls,
        ),
        LLMMessage(
            role="tool",
            content=tool_result,
            tool_call_id=call.id,
        ),
    ]

    print("[turn 2] sending tool result back")
    try:
        resp2: LLMResponse = client.chat(
            messages=follow_up_messages,
            model=cfg["model"],
            tools=[ADD_TOOL],
            temperature=0.0,
            max_tokens=1024,
        )
    except Exception as exc:
        print(f"\nFAIL: follow-up chat() raised: {exc!r}")
        return 1

    print(f"[turn 2] response content: {resp2.content!r}")
    print(f"[turn 2] tool_calls: {resp2.tool_calls}")
    print(f"[turn 2] stop_reason: {resp2.stop_reason}")
    print(f"[turn 2] tokens: in={resp2.input_tokens} out={resp2.output_tokens}")
    print()

    # Verify the model's final answer contains the correct sum.
    # Strip digit-group separators (commas, non-breaking spaces) from
    # the content before matching so "5,103" / "5 103" / "5103" all
    # compare equal.
    expected_sum = 5103  # 1247 + 3856
    content_stripped = resp2.content.replace(",", "").replace(" ", "").replace("\u00a0", "")
    if str(expected_sum) not in content_stripped:
        print(
            f"FAIL: final response does not contain the expected sum "
            f"{expected_sum}: {resp2.content!r}"
        )
        return 1

    print(f"PASS: {provider_name} tool-calling round-trip complete")
    print(f"       expected sum {expected_sum} found in final response")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    return run_test(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
