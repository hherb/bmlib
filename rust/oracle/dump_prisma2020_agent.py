#!/usr/bin/env python3
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

"""Dump whole runs of bmlib's PRISMA 2020 agent over scripted model replies, for the Rust port.

    .venv/bin/python rust/oracle/dump_prisma2020_agent.py \\
        < rust/oracle/prisma2020_agent_cases.json \\
        > rust/bmlib/tests/data/prisma2020_agent_expected.json

``dump_pico_agent.py``'s pattern: each case scripts the model's replies (exact
strings, so both languages parse the same bytes) and the agent runs through
the real ``chat_json``, the real reply readers and the real ``find_passage``.
A case with ``max_context_chars`` and ``digest`` condenses: the map-reduce's
``process`` is patched to return the digest with ``digest_status``, so
``condense_text``'s own checks still run, and the query each condensation was
given is recorded.  The Rust test hands the same result to
``condense_checked`` through a fake ``QueryCondenser``.

A ``part`` case prepares the paper for its items and assesses them; a
``suitability`` case checks the paper; ``stop: true`` sets the stop before the
call.  A ``prompts`` case dumps the eight prompt constants, the condensation's
included, since the patched map-reduce never renders those, and a
``part_prompt_chars`` case the measure a caller budgets with.

Each value carries the result's ``to_dict()`` (or the error's kind), the
messages every model call was sent and the condensation queries, so a prompt
that drifts in one language fails the oracle.  ``time.sleep`` is patched out:
``chat_json``'s backoff would otherwise pause the run.
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
from bmlib.quality import prisma2020_agent
from bmlib.quality.prisma2020_agent import Prisma2020Agent, PrismaError, part_prompt_chars

#: Every prompt the agent can send, the condensation's included.
PROMPTS = (
    "PRISMA_SYSTEM_PROMPT",
    "SUITABILITY_TASK_PROMPT",
    "SUITABILITY_RESPONSE_FORMAT",
    "PART_TASK_PROMPT",
    "PART_RESPONSE_FORMAT",
    "SUITABILITY_CONDENSE_QUERY",
    "PRISMA_CONDENSE_EXTRACTION_PROMPT",
    "PRISMA_CONDENSE_CONSOLIDATION_PROMPT",
)


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


def run(case: dict[str, Any]) -> dict[str, Any]:
    args = case["args"]
    if case["fn"] == "prompts":
        return {name: getattr(prisma2020_agent, name) for name in PROMPTS}
    if case["fn"] == "part_prompt_chars":
        return {"result": part_prompt_chars(args["item_ids"])}
    llm = _client(args["replies"], args.get("stop_reasons", []))
    kwargs: dict[str, Any] = {}
    if "max_context_chars" in args:
        kwargs["condense_config"] = ProcessingConfig(max_context_chars=args["max_context_chars"])
    if args.get("stop"):
        kwargs["stop"] = lambda: True
    agent = Prisma2020Agent(llm=llm, model="test:model", **kwargs)

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
        patch.object(LLMChunkProcessor, "process", return_value=digest) as process,
    ):
        try:
            if case["fn"] == "part":
                prepared = agent.prepare_part(args["title"], args["text"], args["item_ids"])
                assessment = agent.assess_part(
                    prepared, args["item_ids"], min_similarity=args["min_similarity"]
                )
                value = {"result": assessment.to_dict()}
            else:
                value = {"result": agent.check_suitability(args["title"], args["text"]).to_dict()}
        except PrismaError as exc:
            value = {"error": exc.kind.value}

    value["messages"] = [
        [{"role": m.role, "content": m.content} for m in call.kwargs["messages"]]
        for call in llm.chat.call_args_list
    ]
    value["queries"] = [call.kwargs["query"] for call in process.call_args_list]
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
