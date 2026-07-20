# Fast `OllamaProvider.list_models()` — Design

**Date:** 2026-07-20
**Status:** Approved, ready for implementation plan
**Affects:** `bmlib/llm/providers/ollama.py`, `tests/test_llm.py`

## Problem

`OllamaProvider.list_models()` is N+1 by construction. It issues one
`client.list()` call (which already returns every model name and most of
their details), then issues one `client.show()` call **per model** to
obtain the context window.

Measured on a real installation with 139 local models:

| Operation | Cost |
|---|---|
| `/api/tags` (`client.list()`) | 47 ms, 1 request |
| `/api/show` (`client.show()`) | ~26 ms each |
| Current `list_models()`, cold | **~3.6 s, 140 requests** |

Downstream applications have had to work around this. The waste is total
in the most common path: `LLMClient.list_models(provider="ollama")`
(`bmlib/llm/client.py:339`) reduces the result to
`[m.model_id for m in p.list_models()]`, discarding every field the 139
`show()` calls were made to populate.

Two related defects compound it:

- **No list-level cache.** `AnthropicProvider` and `OpenAICompatProvider`
  both keep a TTL cache with `force_refresh`. Ollama keeps only
  `_model_info_cache`, a per-model dict that is never invalidated, and
  re-issues `client.list()` on every call.
- **`validate_model()` inherits the storm.** `BaseProvider.validate_model()`
  (`bmlib/llm/providers/base.py:201`) calls `list_models()` and is not
  overridden by Ollama, so a single model-name validation costs 140
  requests.

Anthropic and openai_compat are not affected — each performs a single API
call. This design is Ollama-only.

## Goals

- `list_models()` costs exactly one HTTP request.
- No change to `BaseProvider`, `LLMClient`, or any provider signature.
- No loss of accuracy for callers that read every field.
- Existing callers get the speedup without code changes.

## Non-goals

- No `names_only=` parameter. The public surface stays as it is; a flag
  whose presence changes the return type would have to be threaded through
  `BaseProvider`, every provider, and `LLMClient`, and would not help the
  `LLMClient.list_models(provider=...)` path, which already returns names.
- No changes to Anthropic or openai_compat model listing.
- No parallel/threaded prefetch of `show()`.

## Design

### 1. Eager fields from `/api/tags`

`client.list()` returns, per model:

```json
{
  "name": "medgemma1.5:4b-it-q8_0",
  "model": "medgemma1.5:4b-it-q8_0",
  "modified_at": "...",
  "size": 4980124986,
  "digest": "...",
  "details": {
    "parent_model": "", "format": "gguf", "family": "gemma3",
    "families": ["gemma3"], "parameter_size": "4.3B",
    "quantization_level": "Q8_0"
  },
  "capabilities": ["completion"]
}
```

`list_models()` fills everything derivable from this payload eagerly:

| `ModelMetadata` field | Source |
|---|---|
| `model_id` | `model` |
| `display_name` | `f"{model} ({parameter_size})"` when `parameter_size` present, else `model` |
| `pricing` | `_FREE_PRICING` constant |
| `capabilities.supports_system_messages` | `True` (constant) |
| `capabilities.supports_function_calling` | `"tools" in capabilities` |
| `capabilities.supports_vision` | `"vision" in capabilities` |
| `context_window` | **lazy** (see §2) |
| `capabilities.max_context_window` | **lazy** (see §2) |

The `capabilities` array observed in practice contains: `audio`,
`completion`, `embedding`, `image`, `thinking`, `tools`, `vision`.
Mapping `tools` and `vision` onto `ProviderCapabilities` is a net accuracy
**improvement** — Ollama currently leaves both at their `False` defaults.

The array may be absent on older Ollama servers; treat a missing or null
`capabilities` key as an empty list, which yields today's `False` defaults.

### 2. Lazy context window

Only `context_window` genuinely requires `show()`. It is deferred behind a
property on two thin subclasses, both delegating to a single memoising
resolver on the provider.

```python
class _LazyOllamaCapabilities(ProviderCapabilities):
    """ProviderCapabilities whose max_context_window resolves on read."""

class _LazyOllamaMetadata(ModelMetadata):
    """ModelMetadata whose context_window resolves on read."""
```

Both call `provider._resolve_context_window(model_id)`, which performs one
`show()` and memoises the result in the existing `_model_info_cache`.

Behaviour:

- Reading `.model_id`, `.display_name`, `.pricing`,
  `.capabilities.supports_vision`, or
  `.capabilities.supports_function_calling` costs nothing.
- Reading `.context_window` or `.capabilities.max_context_window` triggers
  at most one `show()` per model, ever (per provider instance, until
  `force_refresh`).
- A caller that reads `context_window` for all 139 models pays today's cost
  — but only then, and only for the models it actually inspects.

Three implementation constraints:

- **Resolution never raises.** `_resolve_context_window` wraps `show()` in
  the same `try/except` used by the current `_get_model_info`, logging at
  debug level and returning `FALLBACK_CONTEXT_WINDOW` on any failure. An
  attribute read must not propagate an exception.
- **`__repr__` must not resolve.** `_LazyOllamaMetadata.__repr__` (and the
  capabilities subclass's) renders an unresolved context window as
  `context_window=<unresolved>`. Without this, logging or `repr()`-ing a
  model list silently fires one request per model — reintroducing the exact
  bug being fixed, in the place hardest to notice.
- **The property needs a setter.** `ModelMetadata` is a dataclass, so its
  generated `__init__` assigns `self.context_window`. A getter-only
  property would raise `AttributeError` during construction. The setter
  seeds the memo, so an explicitly-passed value is honoured and never
  triggers a fetch.

`__eq__` is left as the dataclass default. Comparing two `ModelMetadata`
instances resolves the context window on both — correct behaviour, since a
caller comparing metadata wants real values, and it is a rare operation.

**Thread safety:** concurrent first-touch of the same model from multiple
threads may issue duplicate `show()` calls. Both write the same value to
`_model_info_cache`; dict assignment is atomic under the GIL. This is
harmless and is left unlocked, consistent with the provider's existing
unlocked `_model_info_cache`. It is documented in the resolver's docstring.

### 3. TTL cache

Add `_models_cache: list[ModelMetadata] | None` and
`_cache_timestamp: float` to `OllamaProvider.__init__`, and honour the
already-declared `force_refresh` parameter, mirroring
`AnthropicProvider.list_models()` (`bmlib/llm/providers/anthropic.py:277`):

- Serve from cache when `not force_refresh` and the entry is younger than
  `CACHE_TTL_SECONDS`.

**TTL value: 60 seconds**, declared as a module-level constant in
`ollama.py` (anthropic.py and openai_compat.py each declare their own at
3600; the constant is not shared). Ollama deliberately diverges. The
expensive call was never `list()` — that is 47 ms against localhost — it
was `show()`, and those results live in `_model_info_cache`, which persists
until `force_refresh` regardless of this TTL. So the list-level TTL exists
only to absorb bursts of repeated calls, and buying that with an hour of
staleness is a bad trade for a local server: `ollama pull` a new model and
it would be invisible for an hour. 60 seconds absorbs the bursts and makes
new models appear promptly.
- Return `list(self._models_cache)` — a copy — so caller mutation cannot
  corrupt the cache (issue #12).
- On `force_refresh=True`, additionally clear `_model_info_cache`. Today
  that dict is unbounded and never invalidated: re-pulling a model with a
  different `num_ctx` leaves the stale context window cached for the life
  of the process.

Failure handling follows the existing Ollama contract, not the
openai_compat one: on error, log a warning and return `[]` without caching.
Ollama has no `FALLBACK_MODELS` list, and an empty result correctly means
"no local models reachable".

### 4. Consequences for callers

No caller changes are required.

- `LLMClient.list_models(provider="ollama")` (`client.py:339`) reduces to
  `model_id` and never touches `context_window` → one request. Downstream
  workarounds can be reverted.
- `BaseProvider.validate_model()` goes from 140 requests to 1 with no
  override needed.
- `OllamaProvider.get_model_metadata()` is already overridden and still
  performs exactly one `show()`.

## Testing

New tests in `tests/test_llm.py`, using a mocked ollama client with a
`show()` call counter. No live server required.

| Test | Assertion |
|---|---|
| `list_models` issues no `show()` | `list()` called once, `show()` call count is 0 |
| lazy resolve on read | reading `.context_window` → `show()` count 1 |
| memoisation | reading `.context_window` twice → `show()` count stays 1 |
| shared resolve | reading `.capabilities.max_context_window` after `.context_window` → count stays 1 |
| repr is safe | `repr(model)` → `show()` count 0, output contains `<unresolved>` |
| eager capability flags | `supports_function_calling` / `supports_vision` derive from tags `capabilities`, `show()` count 0 |
| missing capabilities key | absent/null `capabilities` → both flags `False`, no error |
| resolve failure | `show()` raising → `context_window == FALLBACK_CONTEXT_WINDOW`, no exception |
| TTL cache hit | second `list_models()` → `list()` call count stays 1 |
| `force_refresh` | `list_models(force_refresh=True)` → `list()` called again, `_model_info_cache` cleared |
| cache copy-on-return | mutating a returned list does not corrupt a later call (mirrors the existing Anthropic tests at `tests/test_llm.py:283`) |
| list failure | `list()` raising → returns `[]`, nothing cached |

Run: `uv run pytest tests/ -v`, `uv run ruff check .`,
`uv run ruff format --check .`

## Delivery

Feature branch + pull request, per the project's established flow.
