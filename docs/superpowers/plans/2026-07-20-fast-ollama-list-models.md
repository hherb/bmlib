# Fast `OllamaProvider.list_models()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `OllamaProvider.list_models()` cost one HTTP request instead of N+1, by deferring the only field that needs a per-model `show()` call behind a lazy property.

**Architecture:** `client.list()` (`/api/tags`) already returns every model's name, `parameter_size`, and `capabilities` array. `list_models()` fills those eagerly and returns `ModelMetadata` subclass instances whose `context_window` (and `capabilities.max_context_window`) resolve via a memoising `show()` call only when read. A 60-second TTL cache on the list itself absorbs repeated calls.

**Tech Stack:** Python 3.11+, `ollama` SDK (optional extra), pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-20-fast-ollama-list-models-design.md`

**Branch:** `feature/fast-ollama-list-models` (already exists, spec already committed)

## Global Constraints

- Python `>=3.11`. Use `from __future__ import annotations` (already present in every file touched).
- Type hints required on all function signatures (parameters and return types).
- Docstrings required on all public functions, classes, and modules. This module uses Google-style — match it.
- AGPL-3 license header at the top of every source file. No new source files are created by this plan, so no new headers are needed.
- ruff: line-length 100, target Python 3.11+, rules E, F, I, N, W, UP.
- Use `uv`, never bare pip. Run tests with `uv run pytest`.
- Every test uses mocks. No live Ollama server. No network.
- Only `bmlib/llm/providers/ollama.py`, `tests/test_llm.py`, and `CLAUDE.md` are modified. Do not touch `BaseProvider`, `LLMClient`, or any other provider.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `bmlib/llm/providers/ollama.py` | Ollama provider | Modify: add 2 lazy dataclass subclasses, a resolver method, a tags→metadata builder, and a TTL cache; rewrite `list_models()` |
| `tests/test_llm.py` | LLM unit tests | Modify: add a fake-client helper and 3 new test classes |
| `CLAUDE.md` | Project guidance | Modify: document the lazy-metadata pattern |

---

### Task 1: Lazy metadata classes and the context-window resolver

Builds the mechanism in isolation. `list_models()` is not touched yet — these classes are constructed directly by the tests.

**Files:**
- Modify: `bmlib/llm/providers/ollama.py` (imports at lines 26-31; constants at lines 50-62; `__init__` at lines 86-94; new classes and resolver method)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: existing `_safe_get()`, `_extract_context_window()`, `FALLBACK_CONTEXT_WINDOW`, `_FREE_PRICING`, `OllamaProvider._get_model_info()`
- Produces:
  - `_UNRESOLVED: int` — module-level sentinel (`-1`)
  - `_LazyOllamaCapabilities(resolver: Callable[[], int], **kwargs) -> ProviderCapabilities` subclass
  - `_LazyOllamaModelMetadata(resolver: Callable[[], int], **kwargs) -> ModelMetadata` subclass
  - `OllamaProvider._resolve_context_window(self, model_name: str) -> int`

---

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`:

```python
class TestOllamaLazyMetadata:
    """The lazy context-window mechanism, exercised without list_models()."""

    def _lazy_pair(self, ctx=131072, error=None):
        """Build a lazy metadata object over a counting resolver."""
        from bmlib.llm.providers.ollama import (
            _LazyOllamaCapabilities,
            _LazyOllamaModelMetadata,
            _FREE_PRICING,
        )

        calls = {"n": 0}

        def resolver():
            calls["n"] += 1
            if error is not None:
                raise error
            return ctx

        meta = _LazyOllamaModelMetadata(
            resolver,
            model_id="qwen3:8b",
            display_name="qwen3:8b (8.2B)",
            pricing=_FREE_PRICING,
            capabilities=_LazyOllamaCapabilities(
                resolver,
                supports_system_messages=True,
                supports_function_calling=True,
                supports_vision=False,
            ),
        )
        return meta, calls

    def test_eager_fields_do_not_resolve(self):
        meta, calls = self._lazy_pair()

        assert meta.model_id == "qwen3:8b"
        assert meta.display_name == "qwen3:8b (8.2B)"
        assert meta.pricing.input_cost == 0.0
        assert meta.capabilities.supports_function_calling is True
        assert meta.capabilities.supports_vision is False
        assert calls["n"] == 0

    def test_context_window_resolves_on_read(self):
        meta, calls = self._lazy_pair(ctx=131072)

        assert meta.context_window == 131072
        assert calls["n"] == 1

    def test_context_window_is_memoised(self):
        meta, calls = self._lazy_pair()

        meta.context_window
        meta.context_window
        meta.context_window
        assert calls["n"] == 1

    def test_capabilities_max_context_window_resolves(self):
        meta, calls = self._lazy_pair(ctx=131072)

        assert meta.capabilities.max_context_window == 131072
        assert calls["n"] == 1

    def test_repr_does_not_resolve(self):
        meta, calls = self._lazy_pair()

        text = repr(meta)
        assert calls["n"] == 0
        assert "<unresolved>" in text
        assert "qwen3:8b" in text

    def test_repr_shows_value_once_resolved(self):
        meta, _ = self._lazy_pair(ctx=4096)

        # __repr__ nests capabilities!r, and the metadata and capabilities
        # objects memoise independently — both must be resolved before the
        # rendering is fully concrete.
        meta.context_window
        meta.capabilities.max_context_window
        assert "4096" in repr(meta)
        assert "<unresolved>" not in repr(meta)

    def test_explicit_context_window_is_honoured(self):
        from bmlib.llm.providers.ollama import _LazyOllamaModelMetadata, _FREE_PRICING

        calls = {"n": 0}

        def resolver():
            calls["n"] += 1
            return 999

        meta = _LazyOllamaModelMetadata(
            resolver,
            model_id="m",
            display_name="m",
            pricing=_FREE_PRICING,
            context_window=2048,
        )
        assert meta.context_window == 2048
        assert calls["n"] == 0


class TestOllamaContextWindowResolver:
    """OllamaProvider._resolve_context_window must never raise."""

    def _provider(self, show_result=None, show_error=None):
        from types import SimpleNamespace

        from bmlib.llm.providers.ollama import OllamaProvider

        class FakeClient:
            def __init__(self):
                self.show_calls = 0

            def show(self, model_name):
                self.show_calls += 1
                if show_error is not None:
                    raise show_error
                return show_result

        provider = OllamaProvider()
        provider._client = FakeClient()
        return provider, SimpleNamespace(client=provider._client)

    def test_resolves_from_show(self):
        provider, h = self._provider(
            show_result={"model_info": {"qwen3.context_length": 40960}}
        )

        assert provider._resolve_context_window("qwen3:8b") == 40960
        assert h.client.show_calls == 1

    def test_result_is_cached(self):
        provider, h = self._provider(
            show_result={"model_info": {"qwen3.context_length": 40960}}
        )

        provider._resolve_context_window("qwen3:8b")
        provider._resolve_context_window("qwen3:8b")
        assert h.client.show_calls == 1

    def test_failure_falls_back_without_raising(self):
        from bmlib.llm.providers.ollama import FALLBACK_CONTEXT_WINDOW

        provider, _ = self._provider(show_error=RuntimeError("server down"))

        assert provider._resolve_context_window("missing") == FALLBACK_CONTEXT_WINDOW
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::TestOllamaLazyMetadata tests/test_llm.py::TestOllamaContextWindowResolver -v`

Expected: FAIL — `ImportError: cannot import name '_LazyOllamaCapabilities' from 'bmlib.llm.providers.ollama'`

- [ ] **Step 3: Add imports and the sentinel constant**

In `bmlib/llm/providers/ollama.py`, change the import block (currently lines 26-31) to add `time` and `partial`:

```python
from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from typing import Any
```

Two constraints on this block:

- `Callable` must come from `collections.abc`, not `typing` — this project's
  ruff config enables `UP`, and `UP035` rejects `from typing import Callable`
  on Python 3.11+.
- Add **only** `Callable` here. `time` and `partial` are needed by Tasks 3
  and 2 respectively, and each task adds its own import when it first uses
  it. Importing them now trips `F401` (unused import) and fails this task's
  own ruff gate at Step 7.

Then add the sentinel immediately after the `FALLBACK_CONTEXT_WINDOW` constant (currently line 54):

```python
# Default context window when model metadata is unavailable (tokens)
FALLBACK_CONTEXT_WINDOW = 8192

# Sentinel written into the lazy context-window fields at construction time.
# ``ModelMetadata`` and ``ProviderCapabilities`` are dataclasses, so their
# generated ``__init__`` always assigns these fields — the lazy subclasses
# cannot tell "caller supplied nothing" from "caller supplied a real value"
# without a sentinel distinct from any legitimate context window.
_UNRESOLVED = -1
```

- [ ] **Step 4: Add the two lazy subclasses**

Insert immediately after the `_safe_get()` helper (currently ends at line 74), before `class OllamaProvider`:

```python
class _LazyOllamaCapabilities(ProviderCapabilities):
    """:class:`ProviderCapabilities` whose ``max_context_window`` is lazy.

    Every other capability flag is derived from Ollama's ``/api/tags``
    payload and stays eager, so reading ``supports_vision`` or
    ``supports_function_calling`` costs nothing.  Only the context window
    requires a per-model ``show()`` call, and only that field defers.

    Args:
        resolver: Zero-argument callable returning the context window.
            Called at most once; the result is memoised.
        **kwargs: Forwarded to :class:`ProviderCapabilities`.  Passing
            ``max_context_window`` seeds the memo and prevents any fetch.
    """

    def __init__(self, resolver: Callable[[], int], **kwargs: Any) -> None:
        self._resolver = resolver
        self._resolved: int | None = None
        kwargs.setdefault("max_context_window", _UNRESOLVED)
        super().__init__(**kwargs)

    @property
    def max_context_window(self) -> int:
        """Context window in tokens, fetched on first read."""
        if self._resolved is None:
            self._resolved = self._resolver()
        return self._resolved

    @max_context_window.setter
    def max_context_window(self, value: int) -> None:
        self._resolved = None if value == _UNRESOLVED else value

    def __repr__(self) -> str:
        """Render without triggering a fetch (see class docstring)."""
        ctx = "<unresolved>" if self._resolved is None else self._resolved
        return (
            f"{type(self).__name__}("
            f"supports_streaming={self.supports_streaming!r}, "
            f"supports_function_calling={self.supports_function_calling!r}, "
            f"supports_vision={self.supports_vision!r}, "
            f"supports_system_messages={self.supports_system_messages!r}, "
            f"max_context_window={ctx})"
        )


class _LazyOllamaModelMetadata(ModelMetadata):
    """:class:`ModelMetadata` whose ``context_window`` is lazy.

    Returned by :meth:`OllamaProvider.list_models`.  ``model_id``,
    ``display_name``, ``pricing`` and the capability flags all come from
    ``/api/tags`` and are eager; ``context_window`` triggers one
    ``show()`` call on first read and memoises the result.

    ``__repr__`` deliberately does **not** resolve — otherwise logging a
    model list would silently fire one HTTP request per model, which is
    the exact cost this class exists to avoid.  ``__eq__`` is left as the
    dataclass default and *does* resolve, on the grounds that comparing
    two metadata objects means wanting their real values.

    Args:
        resolver: Zero-argument callable returning the context window.
            Called at most once; the result is memoised.
        **kwargs: Forwarded to :class:`ModelMetadata`.  Passing
            ``context_window`` seeds the memo and prevents any fetch.
    """

    def __init__(self, resolver: Callable[[], int], **kwargs: Any) -> None:
        self._resolver = resolver
        self._resolved: int | None = None
        kwargs.setdefault("context_window", _UNRESOLVED)
        super().__init__(**kwargs)

    @property
    def context_window(self) -> int:
        """Context window in tokens, fetched on first read."""
        if self._resolved is None:
            self._resolved = self._resolver()
        return self._resolved

    @context_window.setter
    def context_window(self, value: int) -> None:
        self._resolved = None if value == _UNRESOLVED else value

    def __repr__(self) -> str:
        """Render without triggering a fetch (see class docstring)."""
        ctx = "<unresolved>" if self._resolved is None else self._resolved
        return (
            f"{type(self).__name__}("
            f"model_id={self.model_id!r}, "
            f"display_name={self.display_name!r}, "
            f"context_window={ctx}, "
            f"pricing={self.pricing!r}, "
            f"capabilities={self.capabilities!r}, "
            f"is_deprecated={self.is_deprecated!r})"
        )
```

- [ ] **Step 5: Add the resolver method**

In `bmlib/llm/providers/ollama.py`, insert immediately after `_get_model_info()` (currently ends at line 462):

```python
    def _resolve_context_window(self, model_name: str) -> int:
        """Return the context window for *model_name*, fetching if needed.

        Delegates to :meth:`_get_model_info`, which performs one
        ``show()`` call and memoises the result in ``_model_info_cache``.
        Sharing that cache means a lazy read and a
        :meth:`get_model_metadata` call never fetch the same model twice.

        Never raises.  :meth:`_get_model_info` already swallows every
        failure and returns metadata carrying
        :data:`FALLBACK_CONTEXT_WINDOW`, which matters here because this
        runs behind attribute access — an exception from reading
        ``.context_window`` would be badly surprising.

        Thread safety: concurrent first-touch of the same model may issue
        duplicate ``show()`` calls.  Both write the same value and dict
        assignment is atomic under the GIL, so this is left unlocked,
        consistent with the rest of ``_model_info_cache``.

        Args:
            model_name: Ollama model identifier, e.g. ``"qwen3:8b"``.

        Returns:
            Context window in tokens.
        """
        return self._get_model_info(model_name).context_window
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py::TestOllamaLazyMetadata tests/test_llm.py::TestOllamaContextWindowResolver -v`

Expected: PASS, 10 tests.

- [ ] **Step 7: Run the full suite and linters**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check .`

Expected: all tests pass, ruff reports no issues. Nothing constructs the lazy classes yet, so no existing behaviour changes.

- [ ] **Step 8: Commit**

```bash
git add bmlib/llm/providers/ollama.py tests/test_llm.py
git commit -m "feat(ollama): add lazy context-window metadata classes

ModelMetadata and ProviderCapabilities subclasses whose context window
resolves via a memoised show() call on first read. __repr__ renders
<unresolved> rather than fetching, so logging a model list stays free.

Not yet wired into list_models().

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Rewrite `list_models()` to use `/api/tags` only

**Files:**
- Modify: `bmlib/llm/providers/ollama.py` (`list_models()` at lines 412-427; new `_metadata_from_tags_entry()`)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `_LazyOllamaModelMetadata`, `_LazyOllamaCapabilities`, `_resolve_context_window` (Task 1); existing `_safe_get`, `_FREE_PRICING`
- Produces: `OllamaProvider._metadata_from_tags_entry(self, name: str, entry: Any) -> ModelMetadata`

---

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`:

```python
def _ollama_tags_entry(name, parameter_size="8.2B", capabilities=("completion",)):
    """One entry as returned by Ollama's /api/tags."""
    entry = {"model": name, "details": {"parameter_size": parameter_size}}
    if capabilities is not None:
        entry["capabilities"] = list(capabilities)
    return entry


class _FakeOllamaClient:
    """Counting stand-in for ollama.Client covering list() and show()."""

    def __init__(self, entries, list_error=None, show_error=None, context_length=40960):
        self._entries = entries
        self._list_error = list_error
        self._show_error = show_error
        self._context_length = context_length
        self.list_calls = 0
        self.show_calls = 0

    def list(self):
        from types import SimpleNamespace

        self.list_calls += 1
        if self._list_error is not None:
            raise self._list_error
        return SimpleNamespace(models=self._entries)

    def show(self, model_name):
        self.show_calls += 1
        if self._show_error is not None:
            raise self._show_error
        return {"model_info": {"qwen3.context_length": self._context_length}}


def _ollama_provider_with(client):
    from bmlib.llm.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    provider._client = client
    return provider


class TestOllamaListModelsIsSingleRequest:
    def test_list_models_issues_no_show_calls(self):
        client = _FakeOllamaClient(
            [_ollama_tags_entry(f"model-{i}") for i in range(50)]
        )
        provider = _ollama_provider_with(client)

        models = provider.list_models()

        assert len(models) == 50
        assert client.list_calls == 1
        assert client.show_calls == 0

    def test_reading_model_ids_stays_free(self):
        client = _FakeOllamaClient(
            [_ollama_tags_entry(f"model-{i}") for i in range(50)]
        )
        provider = _ollama_provider_with(client)

        ids = [m.model_id for m in provider.list_models()]

        assert ids[0] == "model-0"
        assert client.show_calls == 0

    def test_display_name_includes_parameter_size(self):
        client = _FakeOllamaClient([_ollama_tags_entry("qwen3:8b", "8.2B")])
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].display_name == "qwen3:8b (8.2B)"
        assert client.show_calls == 0

    def test_display_name_without_parameter_size(self):
        client = _FakeOllamaClient([_ollama_tags_entry("bare", parameter_size="")])
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].display_name == "bare"

    def test_capability_flags_derived_from_tags(self):
        client = _FakeOllamaClient(
            [_ollama_tags_entry("m", capabilities=("completion", "tools", "vision"))]
        )
        provider = _ollama_provider_with(client)

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is True
        assert caps.supports_vision is True
        assert caps.supports_system_messages is True
        assert client.show_calls == 0

    def test_capability_flags_false_when_absent(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m", capabilities=("completion",))])
        provider = _ollama_provider_with(client)

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is False
        assert caps.supports_vision is False

    def test_missing_capabilities_key_is_tolerated(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m", capabilities=None)])
        provider = _ollama_provider_with(client)

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is False
        assert caps.supports_vision is False

    def test_context_window_resolves_lazily_per_model(self):
        client = _FakeOllamaClient(
            [_ollama_tags_entry("a"), _ollama_tags_entry("b")], context_length=40960
        )
        provider = _ollama_provider_with(client)

        models = provider.list_models()
        assert client.show_calls == 0

        assert models[0].context_window == 40960
        assert client.show_calls == 1

        assert models[0].context_window == 40960
        assert client.show_calls == 1

        assert models[1].context_window == 40960
        assert client.show_calls == 2

    def test_both_lazy_fields_share_one_show_call(self):
        # The metadata object and its capabilities object hold separate
        # memos, so each calls the resolver once. Only one HTTP request
        # may result: the second call must hit _model_info_cache.
        client = _FakeOllamaClient([_ollama_tags_entry("a")], context_length=40960)
        provider = _ollama_provider_with(client)

        model = provider.list_models()[0]
        assert model.context_window == 40960
        assert model.capabilities.max_context_window == 40960
        assert client.show_calls == 1

    def test_entries_without_a_name_are_skipped(self):
        client = _FakeOllamaClient(
            [_ollama_tags_entry("real"), {"model": "", "details": {}}]
        )
        provider = _ollama_provider_with(client)

        assert [m.model_id for m in provider.list_models()] == ["real"]

    def test_list_failure_returns_empty(self):
        client = _FakeOllamaClient([], list_error=RuntimeError("connection refused"))
        provider = _ollama_provider_with(client)

        assert provider.list_models() == []

    def test_list_failure_is_not_cached(self):
        client = _FakeOllamaClient([], list_error=RuntimeError("connection refused"))
        provider = _ollama_provider_with(client)

        provider.list_models()
        provider.list_models()
        assert client.list_calls == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::TestOllamaListModelsIsSingleRequest -v`

Expected: FAIL. `test_list_models_issues_no_show_calls` fails with `assert 50 == 0` on `client.show_calls`, because the current `list_models()` calls `show()` per model.

- [ ] **Step 3: Add the tags→metadata builder**

First add the `partial` import — Task 1 deliberately left it out, since it
would have been unused there and tripped `F401`. The import block becomes:

```python
from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from functools import partial
from typing import Any
```

Then, in `bmlib/llm/providers/ollama.py`, insert directly above `list_models()` (currently line 412), under the existing `# --- Model discovery (native API) ---` comment:

```python
    def _metadata_from_tags_entry(self, name: str, entry: Any) -> ModelMetadata:
        """Build lazy metadata for one ``/api/tags`` entry.

        Every field except the context window is available in the tags
        payload, so only that field defers to a ``show()`` call.

        Ollama servers older than the capabilities feature omit the
        ``capabilities`` key entirely; a missing or null value is treated
        as an empty list, which yields the same ``False`` flags this
        provider reported before capabilities existed.

        Args:
            name: Model identifier from the entry's ``model`` field.
            entry: One element of the ``models`` list, either a dict or a
                Pydantic model depending on the ``ollama`` SDK version.

        Returns:
            A :class:`_LazyOllamaModelMetadata` instance.
        """
        details = _safe_get(entry, "details") or {}
        parameter_size = _safe_get(details, "parameter_size", "") or ""
        display_name = f"{name} ({parameter_size})" if parameter_size else name

        raw_capabilities = _safe_get(entry, "capabilities") or []
        capabilities = {str(c).lower() for c in raw_capabilities}

        resolver = partial(self._resolve_context_window, name)

        return _LazyOllamaModelMetadata(
            resolver,
            model_id=name,
            display_name=display_name,
            pricing=_FREE_PRICING,
            capabilities=_LazyOllamaCapabilities(
                resolver,
                supports_system_messages=True,
                supports_function_calling="tools" in capabilities,
                supports_vision="vision" in capabilities,
            ),
        )
```

- [ ] **Step 4: Replace `list_models()`**

Replace the whole existing method (lines 412-427) with:

```python
    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        """List models currently available on the Ollama server.

        Costs exactly one HTTP request.  Every field except the context
        window comes from ``/api/tags``; ``context_window`` and
        ``capabilities.max_context_window`` resolve via a memoised
        ``show()`` call the first time they are read, so callers that only
        need names or display labels never pay for them.

        Args:
            force_refresh: Currently unused; wired up in the next commit.

        Returns:
            One :class:`_LazyOllamaModelMetadata` per installed model, or
            an empty list if the server is unreachable.
        """
        try:
            client = self._get_client()
            response = client.list()
        except Exception as e:
            logger.warning("Failed to list Ollama models: %s", e)
            return []

        models: list[ModelMetadata] = []
        for entry in getattr(response, "models", []) or []:
            name = _safe_get(entry, "model", "") or ""
            if name:
                models.append(self._metadata_from_tags_entry(name, entry))
        return models
```

Note the `try` now wraps only the two calls that can fail on the wire. The per-entry parsing is pure and must not be silently swallowed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py::TestOllamaListModelsIsSingleRequest -v`

Expected: PASS, 12 tests.

- [ ] **Step 6: Run the full suite and linters**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check .`

Expected: all tests pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add bmlib/llm/providers/ollama.py tests/test_llm.py
git commit -m "perf(ollama): make list_models() a single HTTP request

list_models() called show() once per model to obtain the context window
— 140 requests and ~3.6s on a 139-model installation, where /api/tags
alone answers in 47ms.

It now builds metadata from the tags payload alone and defers the
context window to the lazy property added in the previous commit.
Capability flags (tools, vision) are derived from the tags capabilities
array, which this provider previously left at its False defaults.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: TTL cache and `force_refresh`

Ollama is the only provider without a list-level cache. This brings it in line, with a deliberately shorter TTL.

**Files:**
- Modify: `bmlib/llm/providers/ollama.py` (constants near line 62; `__init__` at lines 86-94; `list_models()` from Task 2)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `list_models()` and `_metadata_from_tags_entry()` (Task 2); `time` import (Task 1)
- Produces: `CACHE_TTL_SECONDS: int` module constant; `OllamaProvider._models_cache`, `OllamaProvider._cache_timestamp`

---

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`:

```python
class TestOllamaListModelsCache:
    def test_second_call_is_served_from_cache(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m")])
        provider = _ollama_provider_with(client)

        provider.list_models()
        provider.list_models()
        assert client.list_calls == 1

    def test_force_refresh_refetches(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m")])
        provider = _ollama_provider_with(client)

        provider.list_models()
        provider.list_models(force_refresh=True)
        assert client.list_calls == 2

    def test_force_refresh_clears_model_info_cache(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m")], context_length=4096)
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].context_window == 4096
        assert client.show_calls == 1

        # A re-pulled model can carry a different context window; the
        # per-model cache must not survive an explicit refresh.
        client._context_length = 8192
        assert provider.list_models(force_refresh=True)[0].context_window == 8192
        assert client.show_calls == 2

    def test_expired_ttl_refetches(self, monkeypatch):
        import bmlib.llm.providers.ollama as ollama_mod

        client = _FakeOllamaClient([_ollama_tags_entry("m")])
        provider = _ollama_provider_with(client)

        clock = {"t": 1000.0}
        monkeypatch.setattr(ollama_mod.time, "time", lambda: clock["t"])

        provider.list_models()
        clock["t"] += ollama_mod.CACHE_TTL_SECONDS + 1
        provider.list_models()
        assert client.list_calls == 2

    def test_mutating_first_result_does_not_corrupt_cache(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m")])
        provider = _ollama_provider_with(client)

        first = provider.list_models()
        first.clear()

        second = provider.list_models()
        assert [m.model_id for m in second] == ["m"]

    def test_mutating_cache_hit_result_does_not_corrupt_cache(self):
        client = _FakeOllamaClient([_ollama_tags_entry("m")])
        provider = _ollama_provider_with(client)

        provider.list_models()
        second = provider.list_models()
        second.append("bogus")

        third = provider.list_models()
        assert [m.model_id for m in third] == ["m"]

    def test_ttl_is_shorter_than_the_remote_providers(self):
        from bmlib.llm.providers.anthropic import CACHE_TTL_SECONDS as remote_ttl
        from bmlib.llm.providers.ollama import CACHE_TTL_SECONDS as local_ttl

        assert local_ttl < remote_ttl
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::TestOllamaListModelsCache -v`

Expected: FAIL — `test_second_call_is_served_from_cache` fails with `assert 2 == 1`, and `test_ttl_is_shorter_than_the_remote_providers` fails with `ImportError: cannot import name 'CACHE_TTL_SECONDS' from 'bmlib.llm.providers.ollama'`.

- [ ] **Step 3: Add the TTL constant**

First add the `time` import — Tasks 1 and 2 deliberately left it out, since
it would have been unused there and tripped `F401`. The import block becomes:

```python
from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from functools import partial
from typing import Any
```

Then, in `bmlib/llm/providers/ollama.py`, add after the `_UNRESOLVED` sentinel from Task 1:

```python
# Seconds a cached model list stays fresh.
#
# Deliberately far shorter than the 3600 used by anthropic.py and
# openai_compat.py (each module declares its own; the constant is not
# shared).  The expensive call was never ``list()`` — that is ~47ms
# against localhost — it was ``show()``, and those results live in
# ``_model_info_cache``, which survives this TTL entirely.  So this cache
# exists only to absorb bursts of repeated calls, and buying that with an
# hour of staleness is a bad trade for a local server: an ``ollama pull``
# would leave the new model invisible for an hour.
CACHE_TTL_SECONDS = 60
```

- [ ] **Step 4: Add the cache fields to `__init__`**

In `OllamaProvider.__init__` (currently lines 86-94), extend the body:

```python
        resolved_base_url = base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        super().__init__(api_key=None, base_url=resolved_base_url, **kwargs)
        self._model_info_cache: dict[str, ModelMetadata] = {}
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0.0
```

- [ ] **Step 5: Wire the cache into `list_models()`**

Replace the docstring's `force_refresh` line and add the cache logic. The method becomes:

```python
    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        """List models currently available on the Ollama server.

        Costs exactly one HTTP request on a cache miss, and none on a
        hit.  Every field except the context window comes from
        ``/api/tags``; ``context_window`` and
        ``capabilities.max_context_window`` resolve via a memoised
        ``show()`` call the first time they are read, so callers that only
        need names or display labels never pay for them.

        Args:
            force_refresh: Bypass the cache and re-query the server.  Also
                clears the per-model ``show()`` cache, so a model re-pulled
                with a different ``num_ctx`` reports its new value.

        Returns:
            One :class:`_LazyOllamaModelMetadata` per installed model, or
            an empty list if the server is unreachable.  A copy is
            returned, so caller mutation cannot corrupt the cache.
        """
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < CACHE_TTL_SECONDS
        ):
            # Copy so caller mutation cannot corrupt the cache (issue #12).
            return list(self._models_cache)

        if force_refresh:
            self._model_info_cache.clear()

        try:
            client = self._get_client()
            response = client.list()
        except Exception as e:
            # Transient failure: do not cache, so the next call retries.
            logger.warning("Failed to list Ollama models: %s", e)
            return []

        models: list[ModelMetadata] = []
        for entry in getattr(response, "models", []) or []:
            name = _safe_get(entry, "model", "") or ""
            if name:
                models.append(self._metadata_from_tags_entry(name, entry))

        self._models_cache = models
        self._cache_timestamp = time.time()
        return list(models)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py::TestOllamaListModelsCache -v`

Expected: PASS, 7 tests.

- [ ] **Step 7: Run the full suite and linters**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check .`

Expected: all tests pass, ruff clean.

- [ ] **Step 8: Verify the end-to-end win against the real server**

This is the only step in the plan that touches a live Ollama server. Skip it if none is running.

Run:

```bash
uv run python -c "
import time
from bmlib.llm.providers.ollama import OllamaProvider
p = OllamaProvider()
t = time.time(); models = p.list_models(); dt = time.time() - t
print(f'{len(models)} models in {dt*1000:.0f} ms')
print('ids are free:', [m.model_id for m in models][:3])
t = time.time(); models[0].context_window; print(f'one lazy resolve: {(time.time()-t)*1000:.0f} ms')
"
```

Expected: the listing completes in tens of milliseconds (versus ~3.6s before), and a single lazy resolve costs ~25ms.

- [ ] **Step 9: Commit**

```bash
git add bmlib/llm/providers/ollama.py tests/test_llm.py
git commit -m "feat(ollama): add TTL cache and force_refresh to list_models()

Ollama was the only provider without a list-level cache. TTL is 60s
rather than the 3600s used by the remote providers: list() is ~47ms
against localhost, the costly show() results live in _model_info_cache
regardless, and an hour of staleness would hide a freshly pulled model.

force_refresh additionally clears _model_info_cache, which was
previously unbounded and never invalidated.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Document the pattern

**Files:**
- Modify: `CLAUDE.md` ("Key Design Patterns" section, and the `llm/` bullet under "Module descriptions")

**Interfaces:**
- Consumes: everything from Tasks 1-3
- Produces: nothing consumed by later tasks

---

- [ ] **Step 1: Add a design-pattern entry**

In `CLAUDE.md`, under "Key Design Patterns", insert after the "Optional dependencies guarded at the call site" subsection:

```markdown
### Lazy model metadata (Ollama)
`OllamaProvider.list_models()` costs one HTTP request regardless of how many
models are installed. Ollama's `/api/tags` carries everything the metadata
actually needs from the server — names, parameter sizes, and per-model
capability flags — leaving only the context window to fetch, so
`list_models()` returns `ModelMetadata` subclasses whose
`context_window` — and `capabilities.max_context_window` — fetch via a
memoised `show()` call only when read. A caller that reads `context_window`
for every model pays the old cost; one that only needs names pays nothing.
`__repr__` on those subclasses renders `<unresolved>` rather than fetching,
so logging a model list stays free. This is the only place in bmlib where
attribute access performs I/O.
```

- [ ] **Step 2: Update the `llm/` module description**

In `CLAUDE.md`, in the "Module descriptions" list, append this sentence to the end of the `llm/` bullet (which currently ends with "...JSON repair, and text chunking."):

```
Model listing never fans out per model: the Anthropic and
OpenAI-compatible providers each issue a single `models.list()` call, and
Ollama defers its per-model context-window lookup (see "Lazy model
metadata" below).
```

- [ ] **Step 3: Verify no code changed**

Run: `git diff --name-only`

Expected: `CLAUDE.md` only.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe the lazy Ollama model-metadata pattern

Attribute access performing I/O is unusual enough to be worth naming
explicitly, since it is the only such place in the library.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Read the raw `/api/tags` payload

Added after the final whole-branch review found that Tasks 1-3 shipped a
capability-derivation feature which never executes in production.

**The defect.** `ollama.Client.list()` parses into `ListResponse.Model`, a
Pydantic model declaring only `model`, `modified_at`, `digest`, `size`,
`details`. Its config leaves Pydantic's default `extra="ignore"` in place,
so two fields the server *does* send are silently dropped: the per-model
`capabilities` array, and `details.context_length`. Measured against the
live 139-model server:

| | On the wire | Through the SDK |
|---|---|---|
| models with `capabilities` | 139 / 139 | 0 |
| models with `details.context_length` | 122 / 139 | 0 |
| tool-capable models flagged | 79 | 0 |
| vision-capable models flagged | 32 | 0 |

The tests did not catch it because every fixture is a plain `dict`, which
takes `_safe_get`'s `isinstance(obj, dict)` branch. Production takes the
`getattr` branch and gets `None`.

Reading the payload directly fixes the capability flags **and** removes the
need for any `show()` call on the 122 models that report their context
length — the lazy machinery becomes the fallback for the remaining 17
rather than the primary path.

**Files:**
- Modify: `bmlib/llm/providers/ollama.py` (imports; new `TAGS_REQUEST_TIMEOUT`; new `_fetch_tags_payload()`; `_metadata_from_tags_entry()`; `list_models()`)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `_UNRESOLVED`, `_LazyOllamaModelMetadata`, `_LazyOllamaCapabilities`, `_resolve_context_window`, `_safe_get`, `_FREE_PRICING`
- Produces: `TAGS_REQUEST_TIMEOUT: int`; `OllamaProvider._fetch_tags_payload(self) -> list[Any]`

---

- [ ] **Step 1: Write the failing tests**

Replace the existing `_FakeOllamaClient`-based tags plumbing. `list_models()`
will no longer call `client.list()`, so the fixtures must intercept the HTTP
fetch instead. `_FakeOllamaClient` is still needed for `show()` counting.

Append to `tests/test_llm.py`:

```python
class _FakeTagsResponse:
    """Context-manager stand-in for urlopen's return value."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_tags(monkeypatch, entries, error=None):
    """Patch urlopen in the ollama module. Returns a call counter."""
    import bmlib.llm.providers.ollama as ollama_mod

    counter = {"n": 0, "url": None}

    def fake_urlopen(url, timeout=None):
        counter["n"] += 1
        counter["url"] = url
        if error is not None:
            raise error
        return _FakeTagsResponse({"models": entries})

    monkeypatch.setattr(ollama_mod.urllib.request, "urlopen", fake_urlopen)
    return counter


def _ollama_entry(name, parameter_size="8.2B", capabilities=("completion",),
                  context_length=None):
    """One entry as the SERVER sends it (not as the SDK parses it)."""
    details = {"parameter_size": parameter_size}
    if context_length is not None:
        details["context_length"] = context_length
    entry = {"model": name, "details": details}
    if capabilities is not None:
        entry["capabilities"] = list(capabilities)
    return entry


class TestOllamaRawTagsPayload:
    def test_sdk_list_response_drops_the_fields_we_need(self):
        """Guard: this is WHY list_models() bypasses client.list().

        If this test ever fails, the ollama SDK has started carrying
        capabilities/context_length and _fetch_tags_payload could be
        reconsidered. Until then, switching back to client.list()
        silently disables capability flags for every model.
        """
        ollama = pytest.importorskip("ollama")

        entry = ollama._types.ListResponse.Model.model_validate(
            {
                "model": "m",
                "digest": "d",
                "size": 1,
                "details": {
                    "parameter_size": "8B",
                    "family": "llama",
                    "format": "gguf",
                    "families": ["llama"],
                    "quantization_level": "Q8_0",
                    "parent_model": "",
                    "context_length": 40960,
                },
                "capabilities": ["tools", "vision"],
            }
        )
        assert getattr(entry, "capabilities", None) is None
        assert getattr(entry.details, "context_length", None) is None

    def test_capability_flags_work_on_real_payload_shape(self, monkeypatch):
        _install_fake_tags(
            monkeypatch, [_ollama_entry("m", capabilities=("completion", "tools", "vision"))]
        )
        provider = _ollama_provider_with(_FakeOllamaClient([]))

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is True
        assert caps.supports_vision is True

    def test_known_context_length_needs_no_show_call(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=40960)])
        client = _FakeOllamaClient([])
        provider = _ollama_provider_with(client)

        model = provider.list_models()[0]
        assert model.context_window == 40960
        assert model.capabilities.max_context_window == 40960
        assert client.show_calls == 0

    def test_missing_context_length_still_resolves_lazily(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=None)])
        client = _FakeOllamaClient([], context_length=8192)
        provider = _ollama_provider_with(client)

        model = provider.list_models()[0]
        assert client.show_calls == 0
        assert model.context_window == 8192
        assert client.show_calls == 1

    def test_mixed_payload_only_fetches_the_unknown_ones(self, monkeypatch):
        _install_fake_tags(
            monkeypatch,
            [
                _ollama_entry("known", context_length=40960),
                _ollama_entry("unknown", context_length=None),
            ],
        )
        client = _FakeOllamaClient([], context_length=8192)
        provider = _ollama_provider_with(client)

        models = provider.list_models()
        assert [m.context_window for m in models] == [40960, 8192]
        assert client.show_calls == 1

    def test_fetch_targets_the_configured_base_url(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [])
        provider = _ollama_provider_with(_FakeOllamaClient([]))
        provider._base_url = "http://example.invalid:9999/"

        provider.list_models()
        assert counter["url"] == "http://example.invalid:9999/api/tags"

    def test_fetch_failure_returns_empty_uncached(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [], error=OSError("refused"))
        provider = _ollama_provider_with(_FakeOllamaClient([]))

        assert provider.list_models() == []
        assert provider.list_models() == []
        assert counter["n"] == 2

    def test_malformed_payload_is_tolerated(self, monkeypatch):
        import bmlib.llm.providers.ollama as ollama_mod

        def fake_urlopen(url, timeout=None):
            return _FakeTagsResponse({"unexpected": "shape"})

        monkeypatch.setattr(ollama_mod.urllib.request, "urlopen", fake_urlopen)
        provider = _ollama_provider_with(_FakeOllamaClient([]))

        assert provider.list_models() == []
```

Add `import json` and `import pytest` to the top of `tests/test_llm.py` if
not already present.

**You must also update the pre-existing tests** in
`TestOllamaListModelsIsSingleRequest` and `TestOllamaListModelsCache`, which
assert on `client.list_calls`. Convert each to `_install_fake_tags` +
the returned counter, keeping every assertion's intent identical — the
`show_calls` assertions stay exactly as they are, and `list_calls == N`
becomes `counter["n"] == N`. `_ollama_tags_entry` is superseded by
`_ollama_entry`; delete it and update its call sites. Do not weaken or
delete any existing assertion.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::TestOllamaRawTagsPayload -v`

Expected: FAIL — `test_capability_flags_work_on_real_payload_shape` and the
context-length tests fail because `list_models()` still calls `client.list()`
and never issues an HTTP GET. `test_sdk_list_response_drops_the_fields_we_need`
should PASS immediately; it documents SDK behaviour, not ours.

- [ ] **Step 3: Add imports and the timeout constant**

The import block becomes:

```python
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from collections.abc import Callable
from functools import partial
from typing import Any
```

Add after `CACHE_TTL_SECONDS`:

```python
# Timeout for the raw /api/tags fetch, in seconds.
TAGS_REQUEST_TIMEOUT = 30
```

- [ ] **Step 4: Add `_fetch_tags_payload()`**

Insert directly above `_metadata_from_tags_entry()`:

```python
    def _fetch_tags_payload(self) -> list[Any]:
        """Fetch ``/api/tags`` as raw JSON, bypassing the ollama SDK.

        The SDK's ``client.list()`` parses into ``ListResponse.Model``, a
        Pydantic model declaring only ``model``, ``modified_at``,
        ``digest``, ``size`` and ``details``.  Its config leaves Pydantic's
        default ``extra="ignore"`` in place, so two fields the server does
        send are dropped silently: the per-model ``capabilities`` array,
        and ``details.context_length``.  On a 139-model installation that
        discards 139 capability arrays and 122 context lengths — and those
        context lengths are exactly what makes a ``show()`` call
        unnecessary for 88% of models.

        Bypassing the SDK costs nothing here: :meth:`_get_client`
        constructs ``ollama.Client(host=...)`` with no custom headers or
        auth, so there is no client configuration to inherit.  The SDK is
        still used for ``show()``.

        Returns:
            The ``models`` list from the payload.  An empty list if the
            payload is missing or malformed.

        Raises:
            OSError: If the request fails.  :meth:`list_models` handles it.
            ValueError: If the response is not valid JSON.
        """
        url = f"{self._base_url.rstrip('/')}/api/tags"
        with urllib.request.urlopen(url, timeout=TAGS_REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            return []
        models = payload.get("models")
        return models if isinstance(models, list) else []
```

- [ ] **Step 5: Seed the context window when the payload carries it**

In `_metadata_from_tags_entry()`, replace the body from the
`raw_capabilities` line through the `return` with:

```python
        raw_capabilities = _safe_get(entry, "capabilities") or []
        capability_names = {str(c).lower() for c in raw_capabilities}

        # Most models report details.context_length. When present there is
        # nothing left to fetch, so seed both lazy fields and this model
        # never triggers a show() call at all. _UNRESOLVED leaves them lazy.
        raw_context = _safe_get(details, "context_length")
        known_context = (
            raw_context if isinstance(raw_context, int) and raw_context > 0 else _UNRESOLVED
        )

        resolver = partial(self._resolve_context_window, name)

        return _LazyOllamaModelMetadata(
            resolver,
            model_id=name,
            display_name=display_name,
            pricing=_FREE_PRICING,
            context_window=known_context,
            capabilities=_LazyOllamaCapabilities(
                resolver,
                supports_system_messages=True,
                supports_function_calling="tools" in capability_names,
                supports_vision="vision" in capability_names,
                max_context_window=known_context,
            ),
        )
```

Also update the method's docstring to note that `context_length`, when
present, is used directly and no `show()` call occurs for that model.

- [ ] **Step 6: Switch `list_models()` to the raw payload**

Replace the fetch block inside `list_models()`:

```python
        try:
            entries = self._fetch_tags_payload()
        except Exception as e:
            # Transient failure: do not cache, so the next call retries.
            logger.warning("Failed to list Ollama models: %s", e)
            return []

        models: list[ModelMetadata] = []
        for entry in entries:
            name = _safe_get(entry, "model", "") or ""
            if name:
                models.append(self._metadata_from_tags_entry(name, entry))
```

Update the method docstring: most models now resolve their context window
with no extra request; only those whose entry omits `context_length` fall
back to a lazy `show()`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -k Ollama -v`

Expected: PASS — the new class plus every converted pre-existing test.

- [ ] **Step 8: Run the full suite and linters**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check .`

- [ ] **Step 9: Verify against the live server**

```bash
uv run python -c "
import time
from bmlib.llm.providers.ollama import OllamaProvider
p = OllamaProvider()
t = time.time(); models = p.list_models(); dt = time.time() - t
print(f'{len(models)} models in {dt*1000:.0f} ms')
tools = sum(1 for m in models if m.capabilities.supports_function_calling)
vis = sum(1 for m in models if m.capabilities.supports_vision)
print(f'tools={tools} vision={vis}  (expect ~79 / ~32, NOT 0/0)')
t = time.time(); ctx = [m.context_window for m in models]; dt = time.time() - t
print(f'all {len(ctx)} context windows in {dt*1000:.0f} ms')
"
```

Expected: non-zero tools/vision counts, and reading every context window
costs far less than the ~3.6s baseline because most need no `show()`.
Report the real numbers.

- [ ] **Step 10: Commit**

```bash
git add bmlib/llm/providers/ollama.py tests/test_llm.py
git commit -m "fix(ollama): read raw /api/tags so capability flags actually work

The ollama SDK parses /api/tags into ListResponse.Model, which declares
only model/modified_at/digest/size/details and leaves Pydantic's default
extra='ignore' in place. Two fields the server sends were dropped
silently: the per-model capabilities array, and details.context_length.
On a 139-model server that is 139 capability arrays and 122 context
lengths discarded — every tool-capable and vision-capable model was
reported as incapable.

The tests missed it because every fixture was a plain dict, which takes
_safe_get's dict branch; production takes the getattr branch.

list_models() now reads the payload directly. Capability flags work, and
the 122 models reporting context_length need no show() call at all — the
lazy path is now the fallback for the remaining 17. Added a regression
test built on a genuine ollama ListResponse.Model.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Restore copy/pickle/replace, and correct the remaining claims

The final review found three regressions and one wrong docstring.

**Files:**
- Modify: `bmlib/llm/providers/ollama.py` (both lazy classes)
- Modify: `CLAUDE.md`, `docs/superpowers/specs/2026-07-20-fast-ollama-list-models-design.md`
- Test: `tests/test_llm.py`

---

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`:

```python
class TestOllamaMetadataIsPortable:
    """list_models() results must stay copyable, picklable, replaceable.

    All three worked before the lazy subclasses existed. The resolver
    closes over the live ollama client (an httpx client holding an
    RLock), so the lazy objects must degrade to plain ones on the way out.
    """

    def _model(self, monkeypatch, context_length=40960):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=context_length)])
        return _ollama_provider_with(_FakeOllamaClient([])).list_models()[0]

    def test_deepcopy_yields_plain_metadata(self, monkeypatch):
        import copy

        from bmlib.llm.providers.base import ModelMetadata

        clone = copy.deepcopy(self._model(monkeypatch))
        assert type(clone) is ModelMetadata
        assert clone.model_id == "m"
        assert clone.context_window == 40960

    def test_pickle_roundtrip(self, monkeypatch):
        import pickle

        from bmlib.llm.providers.base import ModelMetadata

        restored = pickle.loads(pickle.dumps(self._model(monkeypatch)))
        assert type(restored) is ModelMetadata
        assert restored.context_window == 40960
        assert restored.capabilities.max_context_window == 40960

    def test_dataclasses_replace(self, monkeypatch):
        import dataclasses

        updated = dataclasses.replace(self._model(monkeypatch), display_name="renamed")
        assert updated.display_name == "renamed"
        assert updated.context_window == 40960

    def test_capabilities_pickle_to_plain(self, monkeypatch):
        import pickle

        from bmlib.llm.providers.base import ProviderCapabilities

        caps = pickle.loads(pickle.dumps(self._model(monkeypatch).capabilities))
        assert type(caps) is ProviderCapabilities
        assert caps.max_context_window == 40960
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::TestOllamaMetadataIsPortable -v`

Expected: FAIL. `deepcopy`/`pickle` fail with `TypeError: cannot pickle
'_thread.RLock' object` when a real client is attached, or produce a lazy
subclass rather than a plain one; `dataclasses.replace` fails with
`TypeError: ... missing 1 required positional argument: 'resolver'`.

- [ ] **Step 3: Give `resolver` a default and add `__reduce__`**

In BOTH lazy classes, change the `__init__` signature so `resolver` is
optional — this is what makes `dataclasses.replace()` work, since it
reconstructs via `cls(**field_values)` with no `resolver` among them:

```python
    def __init__(self, resolver: Callable[[], int] | None = None, **kwargs: Any) -> None:
```

In BOTH getters, guard the `None` case (attribute access must never raise):

```python
        if self._resolved is None:
            if self._resolver is None:
                return FALLBACK_CONTEXT_WINDOW
            self._resolved = self._resolver()
        return self._resolved
```

`replace()` reads every current field value first, so it passes the already
-resolved `context_window` through and the reconstructed object never needs
a resolver.

Add `__reduce__` to `_LazyOllamaCapabilities`:

```python
    def __reduce__(self) -> tuple[Any, ...]:
        """Degrade to a plain ProviderCapabilities on copy/pickle.

        The resolver closes over the provider's live ``ollama.Client``,
        which holds an unpicklable lock.  Resolving now and handing back a
        plain object keeps these values portable, which they were before
        the lazy subclasses existed.
        """
        return (
            ProviderCapabilities,
            (
                self.supports_streaming,
                self.supports_function_calling,
                self.supports_vision,
                self.supports_system_messages,
                self.max_context_window,
            ),
        )
```

And to `_LazyOllamaModelMetadata`:

```python
    def __reduce__(self) -> tuple[Any, ...]:
        """Degrade to a plain ModelMetadata on copy/pickle.

        See :meth:`_LazyOllamaCapabilities.__reduce__`.  Both lazy fields
        resolve here, so the result carries real values rather than the
        sentinel.
        """
        return (
            ModelMetadata,
            (
                self.model_id,
                self.display_name,
                self.context_window,
                self.pricing,
                self.capabilities,
                self.is_deprecated,
            ),
        )
```

`self.capabilities` is itself a `_LazyOllamaCapabilities`; pickle applies
its `__reduce__` recursively, so the nested object degrades too.

- [ ] **Step 4: Fix the `__eq__` docstring (finding M2)**

In `_LazyOllamaModelMetadata`'s class docstring, replace the sentence
claiming `__eq__` "is left as the dataclass default and *does* resolve"
with:

```
    ``__eq__`` is the dataclass default, which short-circuits on
    ``other.__class__ is self.__class__``.  Two instances of this class
    compare by value (resolving both context windows to do so), but an
    instance never compares equal to a plain :class:`ModelMetadata`, even
    with identical fields — worth knowing when writing a test that builds
    an expected value by hand.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -k Ollama -v`

- [ ] **Step 6: Correct the remaining documentation claims**

In `CLAUDE.md`, in the "Lazy model metadata (Ollama)" subsection, the text
now understates what the implementation does. Replace the body with:

```markdown
`OllamaProvider.list_models()` costs one HTTP request regardless of how many
models are installed. It reads `/api/tags` as raw JSON rather than through
the `ollama` SDK, whose Pydantic model silently drops the per-model
`capabilities` array and `details.context_length`. Most models report their
context length there, so their metadata is complete immediately. For the
rest, `context_window` — and `capabilities.max_context_window` — fetch via a
memoised `show()` call only when read. `__repr__` on those subclasses renders
`<unresolved>` rather than fetching, so logging a model list stays free.
This is the only place in bmlib where attribute access performs I/O. The
returned objects degrade to plain `ModelMetadata` when copied or pickled.
```

Also in `CLAUDE.md`, change "each issue a single `models.list()` call" to
"each issue a single source-level `models.list()` call (the SDK may
paginate underneath)" — closing the round-trip ambiguity the final review
flagged.

In the design spec `docs/superpowers/specs/2026-07-20-fast-ollama-list-models-design.md`,
§1 claims mapping the capabilities array is "a net accuracy **improvement**".
That was true in intent but false as first implemented. Append to that
paragraph:

```markdown
**Correction (found in final review):** the `ollama` SDK's
`ListResponse.Model` leaves Pydantic's `extra="ignore"` in place and
declares neither `capabilities` nor `details.context_length`, so reading
them through `client.list()` yields nothing. `list_models()` therefore reads
the raw `/api/tags` JSON. This also makes `details.context_length` available,
which removes the `show()` call entirely for the models that report it.
```

- [ ] **Step 7: Run the full suite and linters**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check .`

- [ ] **Step 8: Commit**

```bash
git add bmlib/llm/providers/ollama.py tests/test_llm.py CLAUDE.md docs/superpowers/specs/2026-07-20-fast-ollama-list-models-design.md
git commit -m "fix(ollama): keep lazy metadata copyable, picklable, replaceable

The resolver closes over the provider's live ollama.Client, which holds
an httpx RLock, so deepcopy and pickle raised TypeError and
dataclasses.replace() failed on the required positional resolver. All
three worked before the lazy subclasses landed.

__reduce__ now degrades both classes to their plain counterparts,
resolving the context window on the way out, and resolver defaults to
None so replace() can reconstruct from field values alone.

Also corrects the __eq__ docstring, which claimed equality always
resolves — the dataclass default short-circuits on a class check, so a
lazy instance never equals a plain ModelMetadata.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Open the pull request

**Files:** none

- [ ] **Step 1: Confirm the branch is clean and green**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check . && git status --short`

Expected: all tests pass, ruff clean, working tree clean.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feature/fast-ollama-list-models
gh pr create --title "perf(ollama): single-request list_models() via lazy context window" --body "$(cat <<'EOF'
## Problem

`OllamaProvider.list_models()` was N+1: one `client.list()` plus one
`client.show()` per model. Measured on a 139-model installation:

| | |
|---|---|
| `/api/tags` | 47 ms, 1 request |
| `/api/show` | ~26 ms each |
| `list_models()`, cold | **~3.6 s, 140 requests** |

The waste was total in the most common path — `LLMClient.list_models(provider="ollama")`
reduces the result to `[m.model_id for m in ...]`, discarding everything
those 139 requests were made to fetch.

## Change

- `list_models()` builds metadata from the `/api/tags` payload alone.
  `context_window` and `capabilities.max_context_window` resolve via a
  memoised `show()` call on first read. `__repr__` renders `<unresolved>`
  rather than fetching.
- Capability flags (`tools`, `vision`) now derive from the tags
  `capabilities` array. Ollama previously left both at their `False`
  defaults, so this is a net accuracy improvement.
- Added the TTL cache Ollama was the only provider missing, at 60s rather
  than the remote providers' 3600s, and `force_refresh` now also clears
  the previously never-invalidated `_model_info_cache`.

**No public API change.** `BaseProvider`, `LLMClient` and the other
providers are untouched. `BaseProvider.validate_model()` drops from 140
requests to 1 as a side effect.

## Trade-off

Attribute access can now perform I/O — the one design smell here. Mitigated,
not eliminated: resolution never raises, is memoised, and `__repr__` is
repr-safe. Documented in `CLAUDE.md`.

Design: `docs/superpowers/specs/2026-07-20-fast-ollama-list-models-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 Eager fields from `/api/tags` | Task 2, Step 3 |
| §1 `capabilities` absent/null tolerated | Task 2, Step 3 + `test_missing_capabilities_key_is_tolerated` |
| §2 Two lazy subclasses | Task 1, Step 4 |
| §2 Resolution never raises | Task 1, Step 5 + `test_failure_falls_back_without_raising` |
| §2 `__repr__` must not resolve | Task 1, Step 4 + `test_repr_does_not_resolve` |
| §2 Property needs a setter | Task 1, Step 4 (`_UNRESOLVED` sentinel) + `test_explicit_context_window_is_honoured` |
| §2 Thread safety documented | Task 1, Step 5 docstring |
| §2 "at most one `show()` per model, ever" | Task 2, `test_both_lazy_fields_share_one_show_call` |
| §3 TTL cache, 60s, own constant | Task 3, Steps 3-5 |
| §3 Copy-on-return (issue #12) | Task 3, Step 5 + two mutation tests |
| §3 `force_refresh` clears `_model_info_cache` | Task 3, Step 5 + `test_force_refresh_clears_model_info_cache` |
| §3 Failure returns `[]` uncached | Task 3, Step 5 + `test_list_failure_is_not_cached` |
| §4 No caller changes | Verified by the full suite passing at each task |
| Testing table | Tasks 1-3 |
| Delivery: branch + PR | Task 5 |

**Deviation from the spec, noted deliberately:** §2 says the memo lives "in
the existing `_model_info_cache`". The plan achieves that by having
`_resolve_context_window()` delegate to `_get_model_info()` rather than
introducing a second cache keyed differently. Same outcome, one cache,
and `get_model_metadata()` and lazy reads now share results in both
directions.

**Placeholder scan:** none. Every code step carries complete code; every
run step carries an exact command and expected outcome.

**Type consistency:** `_UNRESOLVED` (int), `_LazyOllamaCapabilities`,
`_LazyOllamaModelMetadata`, `_resolve_context_window`,
`_metadata_from_tags_entry`, `CACHE_TTL_SECONDS`, `_models_cache`,
`_cache_timestamp` are each defined once and referenced under the same
name throughout. `resolver` is `Callable[[], int]` at every use site.
Test helpers `_ollama_tags_entry`, `_FakeOllamaClient`, and
`_ollama_provider_with` are defined once in Task 2 and reused in Task 3.
