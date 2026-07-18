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
