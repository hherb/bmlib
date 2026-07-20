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

"""Tests for bmlib.llm data types, token tracker, and client routing."""

from __future__ import annotations

import json

import pytest

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.token_tracker import TokenTracker


class TestLLMMessage:
    def test_construction(self):
        msg = LLMMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"


class TestLLMResponse:
    def test_auto_total(self):
        resp = LLMResponse(content="Hi", input_tokens=10, output_tokens=5)
        assert resp.total_tokens == 15

    def test_explicit_total(self):
        resp = LLMResponse(content="Hi", input_tokens=10, output_tokens=5, total_tokens=20)
        assert resp.total_tokens == 20


class TestTokenTracker:
    def test_record_and_summary(self):
        tracker = TokenTracker()
        tracker.record_usage("test:model", 100, 50, cost=0.001)
        tracker.record_usage("test:model", 200, 100, cost=0.003)

        s = tracker.get_summary()
        assert s.total_input_tokens == 300
        assert s.total_output_tokens == 150
        assert s.total_tokens == 450
        assert s.call_count == 2
        assert abs(s.total_cost_usd - 0.004) < 1e-9
        assert "test:model" in s.by_model
        assert s.by_model["test:model"]["calls"] == 2

    def test_reset(self):
        tracker = TokenTracker()
        tracker.record_usage("m", 10, 5)
        tracker.reset()
        assert tracker.get_summary().call_count == 0

    def test_recent_records(self):
        tracker = TokenTracker()
        for i in range(5):
            tracker.record_usage(f"m{i}", i, i)
        recent = tracker.get_recent_records(3)
        assert len(recent) == 3
        assert recent[0].model == "m2"


class TestProviderRegistry:
    def test_list_providers_includes_builtins(self):
        from bmlib.llm.providers import list_providers

        # Even without the actual packages installed, the registry
        # should at least attempt to register them.  If neither
        # anthropic nor ollama is installed, the list may be empty —
        # but the function itself should not raise.
        names = list_providers()
        assert isinstance(names, list)

    def test_unknown_provider_raises(self):
        import pytest

        from bmlib.llm.providers import get_provider

        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("nonexistent_provider_xyz")

    def test_builtin_registration_failure_is_retried(self, monkeypatch):
        # A non-ImportError failure during built-in registration must not latch
        # the registered flag — the next lookup retries instead of silently
        # resolving without the built-ins forever.
        import pytest

        import bmlib.llm.providers as prov

        saved_registry = dict(prov._REGISTRY)
        saved_flag = prov._builtins_registered
        try:
            prov._REGISTRY.clear()
            prov._builtins_registered = False

            real_register = prov._register_builtins
            calls = {"n": 0}

            def flaky_register():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient failure")
                real_register()

            monkeypatch.setattr(prov, "_register_builtins", flaky_register)

            with pytest.raises(RuntimeError):
                prov.list_providers()

            names = prov.list_providers()
            assert isinstance(names, list)
            assert calls["n"] == 2

            # Success latches the flag: further lookups do not re-register.
            prov.list_providers()
            assert calls["n"] == 2
        finally:
            prov._REGISTRY.clear()
            prov._REGISTRY.update(saved_registry)
            prov._builtins_registered = saved_flag


class TestOllamaTokenAccounting:
    """Real token counts of 0 must not be replaced by estimates."""

    class _FakeOllamaClient:
        def __init__(self, response):
            self._response = response

        def chat(self, **kwargs):
            return self._response

    def _make_provider(self, response):
        from bmlib.llm.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        provider._client = self._FakeOllamaClient(response)
        return provider

    def test_zero_counts_preserved(self):
        response = {
            "message": {"content": "hi"},
            "prompt_eval_count": 0,
            "eval_count": 0,
        }
        provider = self._make_provider(response)
        resp = provider.chat([LLMMessage(role="user", content="hello")])
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0

    def test_missing_counts_estimated(self):
        response = {"message": {"content": "hi"}}  # no counts at all
        provider = self._make_provider(response)
        resp = provider.chat([LLMMessage(role="user", content="hello world")])
        # Falls back to an estimate (> 0) when the field is absent.
        assert resp.input_tokens > 0


class TestModelStringParsing:
    def test_default_provider_normalised_to_lowercase(self):
        from bmlib.llm.client import LLMClient

        client = LLMClient(default_provider="Anthropic")
        assert client.default_provider == "anthropic"
        # A bare model name routes to the (normalised) default provider.
        provider, _model = client._parse_model_string(None)
        assert provider == "anthropic"

    def test_colon_form_lowercases_provider(self):
        from bmlib.llm.client import LLMClient

        client = LLMClient()
        provider, model = client._parse_model_string("Anthropic:claude-x")
        assert provider == "anthropic"
        assert model == "claude-x"


class TestLLMClientSingletonThreadSafety:
    """get_llm_client must create exactly one client under concurrent first use."""

    def test_concurrent_first_use_creates_single_client(self, monkeypatch):
        import threading
        import time

        import bmlib.llm.client as client_mod

        client_mod.reset_llm_client()

        init_calls = []
        orig_init = client_mod.LLMClient.__init__

        def slow_init(self, *args, **kwargs):
            init_calls.append(1)
            time.sleep(0.02)  # widen the check-then-create race window
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(client_mod.LLMClient, "__init__", slow_init)

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        results: list[object] = []

        def worker():
            barrier.wait()
            results.append(client_mod.get_llm_client())

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        client_mod.reset_llm_client()

        assert len(init_calls) == 1
        assert len({id(r) for r in results}) == 1


class TestAnthropicFallbackPricingWarning:
    """Estimated pricing for unknown models must be visible, not silent."""

    def test_unknown_model_pricing_warns(self, caplog):
        import logging

        from bmlib.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="bmlib.llm.providers.anthropic"):
            pricing = provider.get_model_pricing("claude-future-99")
        assert pricing == provider._FALLBACK_PRICING
        assert any("claude-future-99" in rec.message for rec in caplog.records)

    def test_unknown_model_warns_only_once(self, caplog):
        import logging

        from bmlib.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="bmlib.llm.providers.anthropic"):
            provider.get_model_pricing("claude-future-99")
            provider.get_model_pricing("claude-future-99")
        warnings = [r for r in caplog.records if "claude-future-99" in r.message]
        assert len(warnings) == 1

    def test_known_model_does_not_warn(self, caplog):
        import logging

        from bmlib.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key")
        known = next(iter(provider.MODEL_PRICING))
        with caplog.at_level(logging.WARNING, logger="bmlib.llm.providers.anthropic"):
            provider.get_model_pricing(known)
        assert not caplog.records


class TestAnthropicListModelsCacheIsolation:
    """Mutating a returned model list must not corrupt the cache (issue #12)."""

    def _provider_with_stub_client(self):
        from unittest.mock import MagicMock

        from bmlib.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key")
        mock_model = MagicMock()
        mock_model.id = "claude-test-1"
        mock_model.display_name = "Claude Test 1"
        mock_client = MagicMock()
        mock_client.models.list.return_value = [mock_model]
        provider._client = mock_client
        return provider

    def test_mutating_first_result_does_not_corrupt_cache(self):
        provider = self._provider_with_stub_client()

        first = provider.list_models()
        first.clear()

        second = provider.list_models()  # served from cache
        assert [m.model_id for m in second] == ["claude-test-1"]

    def test_mutating_cache_hit_result_does_not_corrupt_cache(self):
        provider = self._provider_with_stub_client()

        provider.list_models()
        second = provider.list_models()  # cache hit
        second.append("bogus")

        third = provider.list_models()  # cache hit again
        assert [m.model_id for m in third] == ["claude-test-1"]


class TestOllamaLazyMetadata:
    """The lazy context-window mechanism, exercised without list_models()."""

    def _lazy_pair(self, ctx=131072, error=None):
        """Build a lazy metadata object over a counting resolver."""
        from bmlib.llm.providers.ollama import (
            _FREE_PRICING,
            _LazyOllamaCapabilities,
            _LazyOllamaModelMetadata,
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
        from bmlib.llm.providers.ollama import _FREE_PRICING, _LazyOllamaModelMetadata

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
        provider, h = self._provider(show_result={"model_info": {"qwen3.context_length": 40960}})

        assert provider._resolve_context_window("qwen3:8b") == 40960
        assert h.client.show_calls == 1

    def test_result_is_cached(self):
        provider, h = self._provider(show_result={"model_info": {"qwen3.context_length": 40960}})

        provider._resolve_context_window("qwen3:8b")
        provider._resolve_context_window("qwen3:8b")
        assert h.client.show_calls == 1

    def test_failure_falls_back_without_raising(self):
        from bmlib.llm.providers.ollama import FALLBACK_CONTEXT_WINDOW

        provider, _ = self._provider(show_error=RuntimeError("server down"))

        assert provider._resolve_context_window("missing") == FALLBACK_CONTEXT_WINDOW


class TestOllamaExtractContextWindowRealShowResponse:
    """_extract_context_window against genuine ollama SDK objects.

    ollama._types.ShowResponse declares its model-info field as
    ``modelinfo``, with ``model_info`` only as a Pydantic alias. Pydantic
    exposes the attribute under the declared name, so
    ``getattr(info, "model_info", None)`` — the branch _safe_get takes for
    non-dict input — returns None on a real response. A dict fixture hits
    _safe_get's dict branch instead and passes regardless, which is
    exactly how this bug survived: tests used dicts, production received
    Pydantic objects.
    """

    def test_extract_context_window_handles_real_show_response(self):
        """Guard: ShowResponse declares `modelinfo`, aliased `model_info`.

        A dict fixture hits _safe_get's dict branch and passes even when
        the production getattr branch returns None. This test builds the
        genuine SDK object so the two cannot diverge again.
        """
        ollama = pytest.importorskip("ollama")
        from bmlib.llm.providers.ollama import _extract_context_window

        info = ollama._types.ShowResponse.model_validate(
            {"model_info": {"qwen3.context_length": 131072}}
        )
        assert _extract_context_window(info) == 131072

    def test_real_context_length_wins_over_rope_original(self):
        """The two keys both match a loose "context" search.

        Measured on a 139-model server, 9 models carry both
        ``<arch>.context_length`` and
        ``<arch>.rope.scaling.original_context_length``, the latter
        smaller by up to two orders of magnitude. Picking whichever came
        first made the answer depend on GGUF key emission order.
        """
        from bmlib.llm.providers.ollama import _extract_context_window

        info = {
            "model_info": {
                "mistral3.rope.scaling.original_context_length": 4096,
                "mistral3.context_length": 262144,
            }
        }
        assert _extract_context_window(info) == 262144

    def test_real_context_length_wins_regardless_of_key_order(self):
        from bmlib.llm.providers.ollama import _extract_context_window

        info = {
            "model_info": {
                "gptoss.context_length": 131072,
                "gptoss.rope.scaling.original_context_length": 4096,
            }
        }
        assert _extract_context_window(info) == 131072

    def test_rope_original_used_only_when_nothing_else_exists(self):
        """A lower bound still beats the hardcoded 8192 fallback."""
        from bmlib.llm.providers.ollama import _extract_context_window

        info = {"model_info": {"x.rope.scaling.original_context_length": 4096}}
        assert _extract_context_window(info) == 4096

    def test_loose_context_key_still_matches(self):
        """Architectures spelling it differently must keep working."""
        from bmlib.llm.providers.ollama import _extract_context_window

        assert _extract_context_window({"model_info": {"max_context": 16384}}) == 16384

    def test_context_preference_holds_on_real_show_response(self):
        ollama = pytest.importorskip("ollama")
        from bmlib.llm.providers.ollama import _extract_context_window

        info = ollama._types.ShowResponse.model_validate(
            {
                "model_info": {
                    "mistral3.rope.scaling.original_context_length": 8192,
                    "mistral3.context_length": 393216,
                }
            }
        )
        assert _extract_context_window(info) == 393216

    def test_extract_context_window_handles_string_parameters(self):
        ollama = pytest.importorskip("ollama")
        from bmlib.llm.providers.ollama import _extract_context_window

        info = ollama._types.ShowResponse.model_validate(
            {
                "model_info": {},
                "parameters": 'num_ctx                 4096\nstop "<|im_end|>"',
            }
        )
        assert _extract_context_window(info) == 4096


class _FakeOllamaClient:
    """Counting stand-in for ollama.Client covering show().

    ``list_models()`` no longer calls ``client.list()`` — the tags payload
    is intercepted separately via ``_install_fake_tags``. This fake only
    needs to cover ``show()``, which is still used to resolve the context
    window for models whose tags entry omits ``details.context_length``.
    """

    def __init__(self, show_error=None, context_length=40960):
        self._show_error = show_error
        self._context_length = context_length
        self.show_calls = 0

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
    def test_list_models_issues_no_show_calls(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [_ollama_entry(f"model-{i}") for i in range(50)])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        models = provider.list_models()

        assert len(models) == 50
        assert counter["n"] == 1
        assert client.show_calls == 0

    def test_reading_model_ids_stays_free(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry(f"model-{i}") for i in range(50)])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        ids = [m.model_id for m in provider.list_models()]

        assert ids[0] == "model-0"
        assert client.show_calls == 0

    def test_display_name_includes_parameter_size(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("qwen3:8b", "8.2B")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].display_name == "qwen3:8b (8.2B)"
        assert client.show_calls == 0

    def test_display_name_without_parameter_size(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("bare", parameter_size="")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].display_name == "bare"

    def test_capability_flags_derived_from_tags(self, monkeypatch):
        _install_fake_tags(
            monkeypatch, [_ollama_entry("m", capabilities=("completion", "tools", "vision"))]
        )
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is True
        assert caps.supports_vision is True
        assert caps.supports_system_messages is True
        assert client.show_calls == 0

    def test_capability_flags_false_when_absent(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", capabilities=("completion",))])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is False
        assert caps.supports_vision is False

    def test_missing_capabilities_key_is_tolerated(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", capabilities=None)])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is False
        assert caps.supports_vision is False

    def test_context_window_resolves_lazily_per_model(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("a"), _ollama_entry("b")])
        client = _FakeOllamaClient(context_length=40960)
        provider = _ollama_provider_with(client)

        models = provider.list_models()
        assert client.show_calls == 0

        assert models[0].context_window == 40960
        assert client.show_calls == 1

        assert models[0].context_window == 40960
        assert client.show_calls == 1

        assert models[1].context_window == 40960
        assert client.show_calls == 2

    def test_both_lazy_fields_share_one_show_call(self, monkeypatch):
        # The metadata object and its capabilities object hold separate
        # memos, so each calls the resolver once. Only one HTTP request
        # may result: the second call must hit _model_info_cache.
        _install_fake_tags(monkeypatch, [_ollama_entry("a")])
        client = _FakeOllamaClient(context_length=40960)
        provider = _ollama_provider_with(client)

        model = provider.list_models()[0]
        assert model.context_window == 40960
        assert model.capabilities.max_context_window == 40960
        assert client.show_calls == 1

    def test_entries_without_a_name_are_skipped(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("real"), {"model": "", "details": {}}])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        assert [m.model_id for m in provider.list_models()] == ["real"]

    def test_list_failure_returns_empty(self, monkeypatch):
        _install_fake_tags(monkeypatch, [], error=OSError("connection refused"))
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        assert provider.list_models() == []

    def test_list_failure_is_not_cached(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [], error=OSError("connection refused"))
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        provider.list_models()
        provider.list_models()
        assert counter["n"] == 2


class TestOllamaListModelsCache:
    def test_second_call_is_served_from_cache(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        provider.list_models()
        provider.list_models()
        assert counter["n"] == 1

    def test_force_refresh_refetches(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        provider.list_models()
        provider.list_models(force_refresh=True)
        assert counter["n"] == 2

    def test_force_refresh_clears_model_info_cache(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient(context_length=4096)
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].context_window == 4096
        assert client.show_calls == 1

        # A re-pulled model can carry a different context window; the
        # per-model cache must not survive an explicit refresh.
        client._context_length = 8192
        assert provider.list_models(force_refresh=True)[0].context_window == 8192
        assert client.show_calls == 2

    def test_failed_force_refresh_keeps_the_show_cache(self, monkeypatch):
        """A refused connection must not discard accumulated show() results.

        Clearing _model_info_cache before the fetch meant one transient
        failure threw away the expensive cache — the one the short TTL
        exists specifically not to disturb — and returned [] as well.
        """
        _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient(context_length=4096)
        provider = _ollama_provider_with(client)

        assert provider.list_models()[0].context_window == 4096
        assert client.show_calls == 1

        _install_fake_tags(monkeypatch, [], error=OSError("refused"))
        assert provider.list_models(force_refresh=True) == []

        # The show() result survived, so resolving the same model again
        # costs nothing.
        assert provider.get_model_metadata("m").context_window == 4096
        assert client.show_calls == 1

    def test_expired_ttl_refetches(self, monkeypatch):
        import bmlib.llm.providers.ollama as ollama_mod

        counter = _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        clock = {"t": 1000.0}
        monkeypatch.setattr(ollama_mod.time, "time", lambda: clock["t"])

        provider.list_models()
        clock["t"] += ollama_mod.CACHE_TTL_SECONDS + 1
        provider.list_models()
        assert counter["n"] == 2

    def test_mutating_first_result_does_not_corrupt_cache(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        first = provider.list_models()
        first.clear()

        second = provider.list_models()
        assert [m.model_id for m in second] == ["m"]

    def test_mutating_cache_hit_result_does_not_corrupt_cache(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m")])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        provider.list_models()
        second = provider.list_models()
        second.append("bogus")

        third = provider.list_models()
        assert [m.model_id for m in third] == ["m"]

    def test_ttl_is_shorter_than_the_remote_providers(self):
        from bmlib.llm.providers.anthropic import CACHE_TTL_SECONDS as REMOTE_TTL
        from bmlib.llm.providers.ollama import CACHE_TTL_SECONDS as LOCAL_TTL

        assert LOCAL_TTL < REMOTE_TTL


class TestOllamaBaseUrlSchemeGuard:
    """Only http(s) may reach urlopen.

    urlopen honours whatever scheme it is handed — file:// reads a local
    path and feeds the bytes straight to json.loads — whereas httpx, which
    backs every SDK-mediated call, rejects anything else. Without this
    guard the SDK-bypassing path was the more permissive of the two.
    """

    @pytest.mark.parametrize(
        "bad_url",
        [
            "file:///etc/passwd",
            "ftp://example.com",
            "gopher://example.com",
            # Opaque schemes carry no "://" — a "://" test would miss them,
            # and urlopen has a DataHandler installed by default.
            "data:text/plain,{}",
            "javascript:alert(1)",
        ],
    )
    def test_non_http_scheme_is_rejected(self, bad_url):
        from bmlib.llm.providers.ollama import _normalise_base_url

        with pytest.raises(ValueError, match="http or https"):
            _normalise_base_url(bad_url)

    def test_provider_construction_rejects_bad_scheme(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        with pytest.raises(ValueError, match="http or https"):
            OllamaProvider(base_url="file:///etc/passwd")

    def test_bad_scheme_from_env_is_rejected(self, monkeypatch):
        from bmlib.llm.providers.ollama import OllamaProvider

        monkeypatch.setenv("OLLAMA_HOST", "file:///etc/passwd")
        with pytest.raises(ValueError, match="http or https"):
            OllamaProvider()

    def test_query_and_fragment_are_dropped(self):
        """They would otherwise land mid-URL: http://h/x?t=1/api/tags."""
        from bmlib.llm.providers.ollama import _normalise_base_url

        assert _normalise_base_url("http://h/x?token=1#frag") == "http://h/x"

    def test_none_yields_the_default(self):
        from bmlib.llm.providers.ollama import _normalise_base_url

        assert _normalise_base_url(None) == "http://localhost:11434"

    @pytest.mark.parametrize(
        "host,expected",
        [
            # "<word>:<digits>" is host:port, not a scheme — even though
            # urlsplit reports scheme="localhost" for the first of these.
            ("localhost:11434", "http://localhost:11434"),
            ("myserver:8080", "http://myserver:8080"),
            ("127.0.0.1:11434", "http://127.0.0.1:11434"),
            ("[::1]:11434", "http://[::1]:11434"),
        ],
    )
    def test_host_port_is_not_mistaken_for_a_scheme(self, host, expected):
        from bmlib.llm.providers.ollama import _normalise_base_url

        assert _normalise_base_url(host) == expected

    def test_non_numeric_port_is_read_as_a_scheme_and_rejected(self):
        """The flip side of the host:port rule, stated so it is deliberate."""
        from bmlib.llm.providers.ollama import _normalise_base_url

        with pytest.raises(ValueError, match="http or https"):
            _normalise_base_url("myhost:notaport")


class TestOllamaRedirectAuthStripping:
    """The bearer token must not survive a cross-origin redirect.

    urllib's stock HTTPRedirectHandler copies every header except
    Content-Length/Content-Type onto the redirected request, so a gateway
    answering /api/tags with a 302 elsewhere would be handed the caller's
    OLLAMA_API_KEY. httpx strips it; this restores parity for the one path
    that bypasses the SDK.
    """

    def _redirect(self, from_url, to_url):
        import urllib.request

        from bmlib.llm.providers.ollama import _StripAuthOnCrossOriginRedirect

        request = urllib.request.Request(from_url)
        request.add_header("Authorization", "Bearer secret-token-123")
        return _StripAuthOnCrossOriginRedirect().redirect_request(
            request, None, 302, "Found", {}, to_url
        )

    def test_cross_host_redirect_drops_the_token(self):
        new = self._redirect("http://localhost:11434/api/tags", "http://evil.example.com/api/tags")

        assert new.get_header("Authorization") is None

    def test_cross_scheme_redirect_drops_the_token(self):
        new = self._redirect("https://gw.example.com/api/tags", "http://gw.example.com/api/tags")

        assert new.get_header("Authorization") is None

    def test_cross_port_redirect_drops_the_token(self):
        new = self._redirect("http://gw.example.com:11434/api/tags", "http://gw.example.com:9999/x")

        assert new.get_header("Authorization") is None

    def test_same_origin_redirect_keeps_the_token(self):
        new = self._redirect("http://gw.example.com/api/tags", "http://gw.example.com/v1/api/tags")

        assert new.get_header("Authorization") == "Bearer secret-token-123"

    def test_implicit_default_port_is_same_origin(self):
        """http://h and http://h:80 are one origin, as httpx treats them."""
        new = self._redirect("http://gw.example.com/api/tags", "http://gw.example.com:80/other")

        assert new.get_header("Authorization") == "Bearer secret-token-123"

    def test_fetch_installs_the_stripping_handler(self, monkeypatch):
        """Guard: the handler is wired in, not merely defined."""
        from bmlib.llm.providers.ollama import _StripAuthOnCrossOriginRedirect

        counter = _install_fake_tags(monkeypatch, [])
        _ollama_provider_with(_FakeOllamaClient()).list_models()

        assert _StripAuthOnCrossOriginRedirect in counter["handlers"]


class TestOllamaBaseUrlNormalisation:
    def test_scheme_less_host_gets_scheme(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert OllamaProvider(base_url="localhost:11434")._base_url == "http://localhost:11434"

    def test_bare_host_gets_scheme_and_default_port(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert OllamaProvider(base_url="127.0.0.1")._base_url == "http://127.0.0.1:11434"

    def test_full_url_is_unchanged(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert (
            OllamaProvider(base_url="http://localhost:11434")._base_url == "http://localhost:11434"
        )

    def test_trailing_slash_is_stripped(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert (
            OllamaProvider(base_url="http://localhost:11434/")._base_url == "http://localhost:11434"
        )

    def test_https_and_path_are_preserved(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert (
            OllamaProvider(base_url="https://example.com/ollama")._base_url
            == "https://example.com/ollama"
        )

    def test_explicit_https_keeps_scheme_default_port(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert (
            OllamaProvider(base_url="https://ollama.example.com")._base_url
            == "https://ollama.example.com"
        )

    def test_explicit_http_keeps_scheme_default_port(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert (
            OllamaProvider(base_url="http://ollama.example.com")._base_url
            == "http://ollama.example.com"
        )

    def test_explicit_port_is_always_respected(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert (
            OllamaProvider(base_url="https://ollama.example.com:8443")._base_url
            == "https://ollama.example.com:8443"
        )

    def test_bracketed_ipv6_is_preserved(self):
        from bmlib.llm.providers.ollama import OllamaProvider

        assert OllamaProvider(base_url="[::1]:11434")._base_url == "http://[::1]:11434"
        assert OllamaProvider(base_url="[::1]")._base_url == "http://[::1]:11434"

    def test_env_var_is_normalised(self, monkeypatch):
        from bmlib.llm.providers.ollama import OllamaProvider

        monkeypatch.setenv("OLLAMA_HOST", "myhost:1234")
        assert OllamaProvider()._base_url == "http://myhost:1234"

    def test_scheme_less_host_reaches_the_right_tags_url(self, monkeypatch):
        from bmlib.llm.providers.ollama import _normalise_base_url

        counter = _install_fake_tags(monkeypatch, [])
        provider = _ollama_provider_with(_FakeOllamaClient())
        provider._base_url = _normalise_base_url("localhost:11434")

        provider.list_models()

        assert counter["url"] == "http://localhost:11434/api/tags"


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


class _FakeOpener:
    """Stand-in for the ``OpenerDirector`` ``build_opener`` returns."""

    def __init__(self, on_open):
        self._on_open = on_open

    def open(self, request, timeout=None):
        return self._on_open(request, timeout)


def _install_raw_opener(monkeypatch, on_open):
    """Patch ``build_opener`` with a bare handler, bypassing the counter.

    For tests that need to control the response body directly rather than
    describe it as a list of tags entries.
    """
    import bmlib.llm.providers.ollama as ollama_mod

    monkeypatch.setattr(
        ollama_mod.urllib.request,
        "build_opener",
        lambda *handlers: _FakeOpener(on_open),
    )


def _install_fake_tags(monkeypatch, entries, error=None):
    """Patch ``urllib.request.build_opener`` for the duration of the test.

    ``bmlib.llm.providers.ollama`` does ``import urllib.request`` rather
    than importing its own copy, so ``ollama_mod.urllib`` **is** the
    global ``urllib`` module — this patches ``build_opener`` process-wide
    for as long as the test runs, not just within the ollama module.  That
    is harmless here because ``monkeypatch`` restores the original
    afterwards, but it does mean any other code exercised by the same test
    would see the fake too.

    ``_fetch_tags_payload`` builds its own opener rather than calling
    ``urlopen`` so that the bearer token is stripped across a cross-origin
    redirect, so this intercepts one level lower than the URL itself.  The
    handler classes it was asked to install are recorded in ``handlers``,
    which is what proves the redirect guard is actually wired in.

    Returns a call counter dict with ``n`` (call count), ``url`` (last URL
    requested), ``timeout`` (last ``timeout`` kwarg received), ``headers``
    (last request's headers) and ``handlers`` (classes passed to
    ``build_opener``).

    The first argument received is a ``urllib.request.Request`` object
    (not a plain string) since ``_fetch_tags_payload`` builds one to
    attach an optional bearer-token header. ``request.full_url`` recovers
    the URL string so the existing assertions keep working unchanged.
    """
    import bmlib.llm.providers.ollama as ollama_mod

    counter = {"n": 0, "url": None, "timeout": None, "headers": None, "handlers": ()}

    def fake_open(request, timeout=None):
        counter["n"] += 1
        counter["url"] = request.full_url
        counter["timeout"] = timeout
        counter["headers"] = dict(request.headers)
        if error is not None:
            raise error
        return _FakeTagsResponse({"models": entries})

    def fake_build_opener(*handlers):
        counter["handlers"] = handlers
        return _FakeOpener(fake_open)

    monkeypatch.setattr(ollama_mod.urllib.request, "build_opener", fake_build_opener)
    return counter


def _ollama_entry(name, parameter_size="8.2B", capabilities=("completion",), context_length=None):
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
        provider = _ollama_provider_with(_FakeOllamaClient())

        caps = provider.list_models()[0].capabilities
        assert caps.supports_function_calling is True
        assert caps.supports_vision is True

    def test_known_context_length_needs_no_show_call(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=40960)])
        client = _FakeOllamaClient()
        provider = _ollama_provider_with(client)

        model = provider.list_models()[0]
        assert model.context_window == 40960
        assert model.capabilities.max_context_window == 40960
        assert client.show_calls == 0

    def test_missing_context_length_still_resolves_lazily(self, monkeypatch):
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=None)])
        client = _FakeOllamaClient(context_length=8192)
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
        client = _FakeOllamaClient(context_length=8192)
        provider = _ollama_provider_with(client)

        models = provider.list_models()
        assert [m.context_window for m in models] == [40960, 8192]
        assert client.show_calls == 1

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://example.invalid:9999/",
            "http://example.invalid:9999",
        ],
        ids=["trailing_slash", "no_trailing_slash"],
    )
    def test_fetch_targets_the_configured_base_url(self, monkeypatch, base_url):
        counter = _install_fake_tags(monkeypatch, [])
        provider = _ollama_provider_with(_FakeOllamaClient())
        provider._base_url = base_url

        provider.list_models()
        assert counter["url"] == "http://example.invalid:9999/api/tags"

    def test_timeout_is_passed_to_urlopen(self, monkeypatch):
        from bmlib.llm.providers.ollama import TAGS_REQUEST_TIMEOUT

        counter = _install_fake_tags(monkeypatch, [])
        provider = _ollama_provider_with(_FakeOllamaClient())

        provider.list_models()

        assert counter["timeout"] == TAGS_REQUEST_TIMEOUT

    def test_top_level_array_payload_is_tolerated(self, monkeypatch):
        # Call _fetch_tags_payload() directly rather than list_models():
        # list_models() wraps the fetch in a broad except Exception, which
        # would also swallow an AttributeError from a missing isinstance
        # guard and mask whether this branch actually did anything.
        _install_raw_opener(monkeypatch, lambda req, timeout=None: _FakeTagsResponse(["a", "b"]))
        provider = _ollama_provider_with(_FakeOllamaClient())

        assert provider._fetch_tags_payload() == []

    @pytest.mark.parametrize("bad_context_length", [0, -4096, "8192", None])
    def test_context_length_boundary_values_fall_back_to_lazy_path(
        self, monkeypatch, bad_context_length
    ):
        # 0, negative, a string, and an explicit JSON null are all
        # untrustworthy as a context window — only a positive int should
        # seed context_window eagerly. Everything else must defer to the
        # show()-backed resolver, same as a missing key entirely.
        entry = {"model": "m", "details": {"context_length": bad_context_length}}
        _install_fake_tags(monkeypatch, [entry])
        client = _FakeOllamaClient(context_length=8192)
        provider = _ollama_provider_with(client)

        model = provider.list_models()[0]
        assert client.show_calls == 0

        assert model.context_window == 8192
        assert client.show_calls == 1

    def test_fetch_failure_returns_empty_uncached(self, monkeypatch):
        counter = _install_fake_tags(monkeypatch, [], error=OSError("refused"))
        provider = _ollama_provider_with(_FakeOllamaClient())

        assert provider.list_models() == []
        assert provider.list_models() == []
        assert counter["n"] == 2

    def test_malformed_payload_is_tolerated(self, monkeypatch):
        _install_raw_opener(
            monkeypatch, lambda req, timeout=None: _FakeTagsResponse({"unexpected": "shape"})
        )
        provider = _ollama_provider_with(_FakeOllamaClient())

        assert provider.list_models() == []


class TestOllamaTagsAuth:
    def test_api_key_is_forwarded_as_bearer(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "secret-token-123")
        counter = _install_fake_tags(monkeypatch, [])

        _ollama_provider_with(_FakeOllamaClient()).list_models()

        assert counter["headers"].get("Authorization") == "Bearer secret-token-123"

    def test_no_auth_header_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        counter = _install_fake_tags(monkeypatch, [])

        _ollama_provider_with(_FakeOllamaClient()).list_models()

        assert counter["headers"].get("Authorization") is None

    def test_blank_api_key_sends_no_header(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "   ")
        counter = _install_fake_tags(monkeypatch, [])

        _ollama_provider_with(_FakeOllamaClient()).list_models()

        assert counter["headers"].get("Authorization") is None


class TestOllamaMetadataPathConsistency:
    """Characterises how list_models() and get_model_metadata() relate.

    _get_model_info used to hardcode supports_function_calling and
    supports_vision to False no matter what show() reported, so the two
    public methods could give contradictory answers for the very same
    model. It now derives the flags from ShowResponse.capabilities, the
    same source /api/tags exposes -- but the two Ollama endpoints
    themselves do not always agree on that array. Measured across 139
    local models, /api/tags reported 77 tool-capable where /api/show
    reported 102, and 32 vision-capable where /api/show reported 44; the
    two arrays differed on 49 of the 139 models. /api/show is
    authoritative; /api/tags is a lower bound.

    The tests below cover both regimes: the matching-arrays case, where
    the two paths necessarily agree because the fixture feeds them
    identical data, and the differing-arrays case, which is what actually
    happens in production and is the behaviour worth asserting on.
    """

    class _FakeShowClient:
        """show() carrying a configurable capabilities array.

        Also carries a resolvable context length so tests covering the
        context_window invariant don't need a second fixture.
        """

        def __init__(self, capabilities=("completion", "tools", "thinking")):
            self.show_calls = 0
            self._capabilities = list(capabilities)

        def show(self, model_name):
            self.show_calls += 1
            return {
                "capabilities": self._capabilities,
                "model_info": {"qwen3.context_length": 262144},
            }

    def test_context_window_agrees_between_paths(self, monkeypatch):
        """context_window genuinely must agree: both paths share _get_model_info.

        Unlike the capability flags, list_models() has no independent
        /api/tags-derived context_window once the tags entry omits
        details.context_length -- both list_models() and
        get_model_metadata() resolve it through the same memoised
        show()-backed call, so this invariant is real and worth keeping
        as a regression guard.
        """
        _install_fake_tags(
            monkeypatch,
            [
                _ollama_entry(
                    "m",
                    capabilities=("completion", "tools", "thinking"),
                    context_length=None,
                )
            ],
        )
        client = self._FakeShowClient()
        provider = _ollama_provider_with(client)

        listed = provider.list_models()[0]
        fetched = provider.get_model_metadata("m")

        assert listed.context_window == fetched.context_window == 262144

    def test_capabilities_agree_when_tags_and_show_arrays_match(self, monkeypatch):
        """Matching-arrays case only: both paths report the same flags.

        This fixture feeds /api/tags and /api/show the identical
        capabilities array, so agreement here is guaranteed by
        construction -- it does not demonstrate that the two paths always
        agree. See test_capabilities_diverge_when_tags_and_show_arrays_differ
        below for the case that actually occurs on real servers.
        """
        _install_fake_tags(
            monkeypatch,
            [
                _ollama_entry(
                    "m",
                    capabilities=("completion", "tools", "thinking"),
                    context_length=None,
                )
            ],
        )
        client = self._FakeShowClient(capabilities=("completion", "tools", "thinking"))
        provider = _ollama_provider_with(client)

        listed = provider.list_models()[0]
        fetched = provider.get_model_metadata("m")

        assert listed.capabilities.supports_function_calling is True
        assert (
            listed.capabilities.supports_function_calling
            == fetched.capabilities.supports_function_calling
        )

    def test_capabilities_diverge_when_tags_and_show_arrays_differ(self, monkeypatch):
        """/api/tags under-reports capabilities relative to /api/show.

        This is the real, measured behaviour (see class docstring), fed
        directly rather than accidentally: /api/tags reports only
        "completion" while /api/show additionally reports "tools" and
        "vision" for the same model. list_models()'s flags are a lower
        bound; get_model_metadata() is authoritative.
        """
        _install_fake_tags(
            monkeypatch,
            [_ollama_entry("m", capabilities=("completion",), context_length=None)],
        )
        client = self._FakeShowClient(capabilities=("completion", "tools", "vision"))
        provider = _ollama_provider_with(client)

        listed = provider.list_models()[0]
        fetched = provider.get_model_metadata("m")

        # Lower bound: /api/tags reported neither tools nor vision.
        assert listed.capabilities.supports_function_calling is False
        assert listed.capabilities.supports_vision is False

        # Authoritative: /api/show reported both.
        assert fetched.capabilities.supports_function_calling is True
        assert fetched.capabilities.supports_vision is True


class TestOllamaMetadataIsPortable:
    """list_models() results must stay copyable, picklable, replaceable.

    All three worked before the lazy subclasses existed. The resolver
    closes over the live ollama client (an httpx client holding an
    RLock), so the lazy objects must degrade to plain ones on the way out.
    """

    def _model(self, monkeypatch, context_length=40960):
        # NOTE: context_length is always seeded here, so the tags entry
        # already carries a known context window and the lazy resolver
        # (hence _FakeOllamaClient.show()) is never invoked by these
        # tests. See _unresolved_model below for the variant that
        # actually exercises the resolver.
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=context_length)])
        return _ollama_provider_with(_FakeOllamaClient()).list_models()[0]

    def test_reduce_binds_fields_by_keyword(self, monkeypatch):
        """Positional binding silently reorders if a field is ever inserted.

        A context window landing in a bool flag would still pickle, still
        round-trip, and still pass every equality test that compares a
        reconstructed object against another reconstructed object.
        """
        model = self._model(monkeypatch)

        factory, args = model.__reduce__()
        caps_factory, caps_args = model.capabilities.__reduce__()

        assert args == ()
        assert caps_args == ()
        assert factory.keywords["model_id"] == "m"
        assert factory.keywords["context_window"] == 40960
        assert caps_factory.keywords["max_context_window"] == 40960
        assert caps_factory.keywords["supports_system_messages"] is True

    def _unresolved_model(self, monkeypatch, resolved_context_length=131072):
        """Build a model whose tags entry omits context_length.

        Reading ``context_window`` (directly, or indirectly via pickle/
        deepcopy's ``__reduce__``) must therefore actually invoke the
        show()-backed resolver rather than reading a pre-seeded value.
        Returns ``(model, client)`` so callers can assert on
        ``client.show_calls``.
        """
        _install_fake_tags(monkeypatch, [_ollama_entry("m", context_length=None)])
        client = _FakeOllamaClient(context_length=resolved_context_length)
        model = _ollama_provider_with(client).list_models()[0]
        return model, client

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

    def test_pickle_roundtrip_resolves_unseeded_context_window(self, monkeypatch):
        """The docstring's "both lazy fields resolve here" claim, tested.

        A seeded context_length never invokes the resolver, so a fixture
        that always seeds it cannot tell "resolver ran and returned the
        right value" from "resolver never ran, sentinel/fallback leaked
        through". This variant's tags entry omits context_length, so
        __reduce__ must actually call show() to produce a real value.
        """
        import pickle

        from bmlib.llm.providers.base import ModelMetadata

        model, client = self._unresolved_model(monkeypatch, resolved_context_length=131072)
        assert client.show_calls == 0

        restored = pickle.loads(pickle.dumps(model))

        assert client.show_calls == 1
        assert type(restored) is ModelMetadata
        assert restored.context_window == 131072
        assert restored.capabilities.max_context_window == 131072

    def test_deepcopy_resolves_unseeded_context_window(self, monkeypatch):
        import copy

        from bmlib.llm.providers.base import ModelMetadata

        model, client = self._unresolved_model(monkeypatch, resolved_context_length=128000)
        assert client.show_calls == 0

        clone = copy.deepcopy(model)

        assert client.show_calls == 1
        assert type(clone) is ModelMetadata
        assert clone.context_window == 128000
        assert clone.capabilities.max_context_window == 128000
