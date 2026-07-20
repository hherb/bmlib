# Batch Embedding Support — Design

**Date:** 2026-07-20
**Status:** Approved (autonomous session — design decisions made per documented rationale; interactive approval gates not available)

## Problem

A downstream consumer (BioMedicalNews "Area 5" chunk_embedder) embeds document chunks
in bulk. The Ollama SDK supports batch embedding via `client.embed(model=..., input=[...])`
— one HTTP round-trip for N texts. bmlib's `LLMClient.embed()` only accepts a single
string, forcing a Python loop of N round-trips. Measured on 32 chunks: batch 0.59 s vs
looped singles 4.48 s — a **7.6× slowdown** on the bulk-corpus path.

Additionally, `OllamaProvider.embed()` currently calls `client.embeddings(prompt=...)`,
the **deprecated** legacy `/api/embeddings` endpoint.

## Goals

- One API call per batch of texts, at every layer: `LLMClient` → `BaseProvider` → `OllamaProvider`.
- Purely additive public API; existing `embed()` callers keep working unchanged.
- Single code path for single and batch embedding inside the Ollama provider.

## Non-goals

- Embedding support for other providers (OpenAI etc.) — none implement `embed()` today; unchanged.
- Token-tracker integration for embeddings — `embed()` has never recorded usage; parity kept.
- Client-side sub-batching/chunking of very large input lists — callers control batch size.

## Approaches considered

1. **Additive `embed_batch()` + migrate single `embed()` to `/api/embed`** ← chosen.
2. Union input `embed(text: str | Sequence[str])` — rejected: conditional return type,
   hostile to type-checked callers, breaks the "purely additive" convention.
3. `embed_batch()` only, leave `embed()` on the legacy endpoint — rejected: Ollama's
   `/api/embed` returns **normalized** vectors while legacy `/api/embeddings` does not,
   so `embed(t)` and `embed_batch([t])[0]` would disagree in scale. A documented
   one-time migration beats a permanent inconsistency.

## Design

### Data type (`bmlib/llm/data_types.py`)

```python
@dataclass
class BatchEmbeddingResponse:
    embeddings: list[list[float]]   # one vector per input text, in input order
    model: str = ""
    dimensions: int = 0             # len(embeddings[0]), 0 if empty batch
    input_tokens: int = 0           # total tokens across the whole batch
```

Existing `EmbeddingResponse` is unchanged. Exported from `bmlib.llm`.

### Provider layer

- `BaseProvider.embed_batch(texts: list[str], model=None, **kwargs) -> BatchEmbeddingResponse`
  — concrete default that raises `NotImplementedError`, mirroring `embed()`.
- `OllamaProvider.embed_batch()` — one `client.embed(model=model, input=texts)` call.
  - Empty `texts` → returns an empty `BatchEmbeddingResponse` **without** a network call.
  - API/connection failure → `ConnectionError` (same contract as `embed()`).
  - Response vector count ≠ input count → `ValueError` (protocol violation, fail loud).
  - Handles both dict-shaped (ollama < 0.4) and Pydantic (≥ 0.4) responses via `_safe_get`.
- `OllamaProvider.embed()` — now delegates to `embed_batch([text])` and unwraps the
  first vector into the existing `EmbeddingResponse` shape.

### Client layer

`LLMClient.embed_batch(texts, model=None, **kwargs)` — parses the
`"provider:model_name"` string and routes to the provider, exactly like `embed()`.

### Behavioral change (documented, deliberate)

Single-text `embed()` on Ollama moves from the deprecated `/api/embeddings` endpoint to
`/api/embed`. The new endpoint returns **L2-normalized** vectors (unit length); the
legacy one returned raw vectors. Cosine similarity — the standard comparison for
embeddings — is scale-invariant and unaffected; raw dot-product or L2 comparisons
against vectors stored from the old endpoint will differ in scale. Called out in the
manual and the PR description.

## Testing

New `tests/test_llm_embeddings.py` (pattern: `MagicMock` client injected via
`provider._client`, as in `test_llm_tools.py`):

- batch returns vectors in order; dimensions/input_tokens populated; **exactly one**
  `client.embed` call (the performance contract)
- single `embed()` routes through `/api/embed` and preserves the `EmbeddingResponse` shape
- empty batch → no API call
- count mismatch → `ValueError`; connection failure → `ConnectionError`
- dict-shaped and attribute-shaped SDK responses both parse
- `BaseProvider.embed_batch` default raises `NotImplementedError`
- `LLMClient.embed_batch` routes on the model string

## Documentation

- `docs/manual/llm.md`: `BatchEmbeddingResponse`, `LLMClient.embed_batch`, endpoint
  migration note in the Embeddings section.
- `CLAUDE.md`: llm module description + test-file mapping.
