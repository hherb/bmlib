#!/usr/bin/env python3
"""Dump bmlib's two wire protocols, for the Rust port.

The OpenAI-side transforms are the Python's own functions, imported rather than
re-implemented. The Anthropic side is spelled out because the Python reaches it
through the `anthropic` SDK, which converts for it — so the oracle states the
target shape and the Rust is written against the same statement.
"""

from __future__ import annotations

import json
import sys

from bmlib.llm.data_types import LLMMessage, LLMToolCall, LLMToolDefinition
from bmlib.llm.providers.anthropic import (
    _convert_messages_to_anthropic,
    _convert_tool_choice_to_anthropic,
    _convert_tool_def_to_anthropic,
)
from bmlib.llm.providers.openai_compat import (
    _convert_messages_to_openai,
    _convert_tool_choice_to_openai,
    _convert_tool_def_to_openai,
    _split_think_tags,
)


def message(spec):
    calls = spec.get("tool_calls")
    return LLMMessage(
        role=spec["role"],
        content=spec.get("content", ""),
        tool_call_id=spec.get("tool_call_id"),
        tool_calls=(
            [LLMToolCall(id=c["id"], name=c["name"], arguments=c["arguments"]) for c in calls]
            if calls
            else None
        ),
    )


def tool(spec):
    return LLMToolDefinition(
        name=spec["name"],
        description=spec.get("description", ""),
        parameters=spec.get("parameters", {}),
    )


def tool_arguments_openai(raw):
    """OpenAI sends arguments as a JSON string; read it the way the provider does."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return {"_raw": raw}
    return {}


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "messages_to_openai":
        return _convert_messages_to_openai([message(m) for m in a["messages"]])
    if fn == "tool_def_to_openai":
        return _convert_tool_def_to_openai(tool(a["tool"]))
    if fn == "tool_choice_to_openai":
        return _convert_tool_choice_to_openai(a["tool_choice"])
    if fn == "split_think_tags":
        thinking, rest = _split_think_tags(a["content"])
        return {"thinking": thinking, "content": rest}
    if fn == "tool_arguments_openai":
        return tool_arguments_openai(a["raw"])
    if fn == "messages_to_anthropic":
        system, messages = _convert_messages_to_anthropic([message(m) for m in a["messages"]])
        return {"system": system, "messages": messages}
    if fn == "tool_def_to_anthropic":
        return _convert_tool_def_to_anthropic(tool(a["tool"]))
    if fn == "tool_choice_to_anthropic":
        return _convert_tool_choice_to_anthropic(a["tool_choice"])
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
