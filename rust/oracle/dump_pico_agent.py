#!/usr/bin/env python3
"""Dump whole runs of bmlib's PICO agent over scripted model replies, for the Rust port.

    .venv/bin/python rust/oracle/dump_pico_agent.py < rust/oracle/pico_agent_cases.json

The first dumper to run an **agent**, not a parser: each case scripts the
model's replies (exact strings, so both languages parse the same bytes) and
the agent runs through the real ``chat_json``, the real reply readers and the
real ``find_passage``.  A case with ``max_context_chars`` and ``digest``
condenses: the map-reduce's ``process`` is patched to return the digest with
``digest_status``, so ``condense_text``'s own checks and note still run — the
Rust test hands the same result to ``condense_checked`` through a fake
``Condenser``.

A ``prompts`` case dumps the eight prompt constants, the condensation's
included, since the patched map-reduce never renders those.

Each value carries the result's ``to_dict()`` (or the error's kind) and the
messages every model call was sent, so a prompt that drifts in one language
fails the oracle.  ``time.sleep`` is patched out: ``chat_json``'s backoff
would otherwise pause the run.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

from bmlib.context_processor import (
    ExtractionResult,
    LLMChunkProcessor,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
)
from bmlib.llm.data_types import LLMResponse
from bmlib.quality import pico_agent
from bmlib.quality.pico_agent import PicoAgent
from bmlib.quality.pico_models import PicoError


def _client(replies: list[str], stop_reasons: list[str | None]) -> MagicMock:
    responses = [
        LLMResponse(
            content=reply,
            model="test:model",
            input_tokens=1,
            output_tokens=1,
            stop_reason=stop_reasons[i] if i < len(stop_reasons) else "stop",
        )
        for i, reply in enumerate(replies)
    ]
    llm = MagicMock()
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return llm


#: Every prompt the agent can send, the condensation's included: the runs pin
#: the five a run sends (the system prompt, and each call's task and format),
#: and only this case reaches the three the patched map-reduce never renders.
PROMPTS = (
    "PICO_SYSTEM_PROMPT",
    "SUITABILITY_TASK_PROMPT",
    "SUITABILITY_RESPONSE_FORMAT",
    "EXTRACTION_TASK_PROMPT",
    "EXTRACTION_RESPONSE_FORMAT",
    "PICO_CONDENSE_QUERY",
    "PICO_CONDENSE_EXTRACTION_PROMPT",
    "PICO_CONDENSE_CONSOLIDATION_PROMPT",
)


def run(case: dict[str, Any]) -> dict[str, Any]:
    if case["fn"] == "prompts":
        return {name: getattr(pico_agent, name) for name in PROMPTS}
    args = case["args"]
    llm = _client(args["replies"], args.get("stop_reasons", []))
    kwargs: dict[str, Any] = {}
    if "temperature" in args:
        kwargs["temperature"] = args["temperature"]
    if "max_context_chars" in args:
        kwargs["condense_config"] = ProcessingConfig(max_context_chars=args["max_context_chars"])
    agent = PicoAgent(llm=llm, model="test:model", **kwargs)

    digest = ProcessingResult(
        final_result=ExtractionResult(content=args.get("digest", ""), confidence=1.0),
        status=ProcessingStatus(args.get("digest_status", "completed")),
        total_items_processed=1,
        batches_created=1,
        recursion_levels_used=0,
        successful_batches=1,
    )
    value: dict[str, Any]
    with (
        patch("bmlib.agents.base.time.sleep"),
        patch.object(LLMChunkProcessor, "process", return_value=digest),
    ):
        try:
            if case["fn"] == "extract":
                result: Any = agent.extract(
                    args["title"], args["text"], min_similarity=args["min_similarity"]
                )
            else:
                result = agent.check_suitability(args["title"], args["text"])
            value = {"result": result.to_dict()}
        except PicoError as exc:
            value = {"error": exc.kind.value}

    value["messages"] = [
        [{"role": m.role, "content": m.content} for m in call.kwargs["messages"]]
        for call in llm.chat.call_args_list
    ]
    return value


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
