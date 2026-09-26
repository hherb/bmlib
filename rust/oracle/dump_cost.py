#!/usr/bin/env python3
"""Dump the pricing tables and cost arithmetic, for the Rust port."""

from __future__ import annotations

import json
import sys

from bmlib.llm.providers.anthropic import AnthropicProvider
from bmlib.llm.providers.deepseek import DeepSeekProvider
from bmlib.llm.providers.gemini import GeminiProvider
from bmlib.llm.providers.mistral import MistralProvider
from bmlib.llm.providers.openai_provider import OpenAIProvider

#: The provider key each class's table belongs to, matching the port's registry.
PROVIDERS = {
    "anthropic": AnthropicProvider,
    "deepseek": DeepSeekProvider,
    "gemini": GeminiProvider,
    "mistral": MistralProvider,
    "openai": OpenAIProvider,
}


def main() -> int:
    tables = {}
    costs = []
    for key, cls in PROVIDERS.items():
        # **Constructed properly, not `__new__`-ed.** The constructors only
        # resolve a key and initialise caches — the SDK client is created lazily —
        # so no request happens, while `__new__` skips the fields the fallback
        # path needs (`_pricing_warned`).
        provider = cls()
        table = {
            model: [
                cls.MODEL_PRICING[model].input_cost,
                cls.MODEL_PRICING[model].output_cost,
            ]
            for model in cls.MODEL_PRICING
        }
        tables[key] = table

        # Cost arithmetic over a grid, plus the fallback path for a model the
        # table does not name.
        for model in sorted(cls.MODEL_PRICING) + ["a-model-nobody-has-heard-of"]:
            for in_tok, out_tok in (
                (0, 0),
                (1, 1),
                (1_000_000, 0),
                (0, 1_000_000),
                (1234, 5678),
                (1_000_000, 1_000_000),
            ):
                costs.append(
                    {
                        "provider": key,
                        "model": model,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "cost": provider.calculate_cost(model, in_tok, out_tok),
                    }
                )
    json.dump({"tables": tables, "costs": costs}, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
