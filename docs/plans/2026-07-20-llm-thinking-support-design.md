# LLM thinking/reasoning support — design

**Date:** 2026-07-20
**Status:** implemented

## Problem

bmlibrarian's `qa/document_qa.py` passes `think=True` to `chat()` and reads a
`thinking` field off the response. bmlib forwards `think` to the Ollama
provider (request side works), but `LLMResponse` has no `thinking` attribute —
the model's reasoning trace is silently dropped. The fix must generalize
across providers, not just Ollama, and must not break existing bmlib callers.

## Approaches considered

1. **Minimal:** add `LLMResponse.thinking` and extract it only in the Ollama
   provider. Fixes the blocker but leaves every other provider dropping
   reasoning output; `think=True` stays Ollama-only jargon.
2. **Canonical `think` kwarg + per-provider interpretation (chosen):** keep
   the existing `**kwargs` passthrough as the transport, define `think` as a
   cross-provider option (`bool` on/off, `str` effort level, `int` token
   budget), have each provider map it to its native parameter, and extract
   reasoning output into a new optional `LLMResponse.thinking` field in every
   provider that can return it.
3. **Full config object** (`ThinkingConfig` dataclass + capability flags +
   `supports_thinking()` helper). More API surface than any current caller
   needs; can be layered on later without breaking approach 2.

## Design (approach 2)

### Response side

`LLMResponse` gains `thinking: str | None = None`, appended **after**
`tool_calls` so positional construction keeps working. `None` means the
provider returned no separated reasoning output.

### Request side — the `think` kwarg

`LLMClient.chat(..., think=...)` continues to travel via `**kwargs` (no
signature change to `BaseProvider.chat`, so third-party subclasses are
unaffected). Accepted values: `True`/`False`, effort strings
(`"low"`/`"medium"`/`"high"`), or an `int` token budget. Absent → provider
default, exactly as before.

Per-provider mapping:

| Provider | Request mapping | Response extraction |
|---|---|---|
| Ollama | `bool`/`str` forwarded natively (unchanged); `int` coerced to on/off by truthiness (Ollama has no budget concept; `0` stays off) | `message.thinking` |
| Anthropic | truthy → `thinking={"type": "enabled", "budget_tokens": N}`; `int` → that budget, `"low"`=2048 / `"medium"`=8192 / `"high"`=16384, `True`=8192; budget clamped to `[1024, max_tokens-1]`; `ValueError` if `max_tokens <= 1024` or the budget is negative; `temperature`/`top_p` omitted (API requires defaults with thinking) | `thinking` content blocks (`redacted_thinking` skipped) |
| OpenAI-compat base | `str` + reasoning model → `reasoning_effort`; otherwise logged at debug and not sent (avoids 400s from servers that reject unknown params). New: `extra_body` kwarg forwarded verbatim as the escape hatch for server-specific knobs (vLLM `chat_template_kwargs`, etc.) | `message.reasoning_content` (DeepSeek, vLLM, SGLang) or `message.reasoning` (OpenRouter-style); fallback: leading `<think>…</think>` block split out of `content`, **only when the caller passed a truthy `think`** |

### Backwards compatibility

- `LLMResponse.thinking` is optional-with-default and appended last —
  positional and keyword construction, and all existing reads, unchanged.
- Callers that never pass `think` see byte-identical requests (the Anthropic
  `temperature` omission only happens when thinking is enabled).
- OpenAI-compat `content` is only ever rewritten (`<think>` split) when the
  caller explicitly opted in with a truthy `think` — a no-op today since
  `think` was previously ignored by those providers.
- Extraction guards require `isinstance(..., str)`, so SDK objects that lack
  the fields (or mocks) never leak non-strings into `thinking`.

### Known limitation

Anthropic extended thinking does not compose with multi-turn tool loops: the
API requires the original `thinking` blocks (with their signatures) to be
re-sent in the assistant turn that carried the `tool_use` blocks, but
`LLMResponse.thinking` is a plain joined string and
`_convert_messages_to_anthropic` does not round-trip thinking blocks, so the
follow-up request is rejected. Fixing this needs signature-preserving block
storage — tracked in ROADMAP.md.

### Testing

New `tests/test_llm_thinking.py` (mocked SDK clients, no network), covering:
dataclass default + positional compat; Ollama extraction (pydantic-style and
dict-style responses) and `think` normalization; Anthropic request shaping
(budget mapping, clamping, `ValueError`, temperature omission) and block
extraction; OpenAI-compat extraction, `reasoning_effort` gating, `<think>`
split opt-in, `extra_body` passthrough; LLMClient end-to-end kwarg routing.
