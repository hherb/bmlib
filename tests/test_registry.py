"""Tests for the source fetcher registry."""

from __future__ import annotations

from bmlib.publications.fetchers.registry import (
    _REGISTRY,
    _ensure_builtins,
    get_fetcher,
    get_source,
    list_sources,
    register_source,
    source_names,
)
from bmlib.publications.models import SourceDescriptor, SourceParam


class TestBuiltinRegistration:
    def test_builtins_registered(self):
        _ensure_builtins()
        names = source_names()
        assert "pubmed" in names
        assert "biorxiv" in names
        assert "medrxiv" in names
        assert "openalex" in names

    def test_list_sources_returns_descriptors(self):
        sources = list_sources()
        assert len(sources) >= 4
        for desc in sources:
            assert isinstance(desc, SourceDescriptor)
            assert desc.name
            assert desc.display_name
            assert desc.description

    def test_get_fetcher_returns_callable(self):
        for name in ("pubmed", "biorxiv", "medrxiv", "openalex"):
            fetcher = get_fetcher(name)
            assert callable(fetcher)

    def test_get_source_returns_tuple(self):
        desc, fetcher = get_source("pubmed")
        assert isinstance(desc, SourceDescriptor)
        assert desc.name == "pubmed"
        assert desc.display_name == "PubMed"
        assert callable(fetcher)

    def test_pubmed_has_api_key_param(self):
        desc, _ = get_source("pubmed")
        param_names = [p.name for p in desc.params]
        assert "api_key" in param_names

    def test_openalex_has_email_param(self):
        desc, _ = get_source("openalex")
        param_names = [p.name for p in desc.params]
        assert "email" in param_names
        email_param = next(p for p in desc.params if p.name == "email")
        assert email_param.required is True


class TestGetSourceErrors:
    def test_unknown_source_raises(self):
        import pytest

        with pytest.raises(ValueError, match="Unknown source"):
            get_source("nonexistent_source")

    def test_unknown_fetcher_raises(self):
        import pytest

        with pytest.raises(ValueError):
            get_fetcher("nonexistent_source")


class TestCustomRegistration:
    def test_register_custom_source(self):
        calls = []

        def fake_fetcher(client, target_date, *, on_record, on_progress=None, **config):
            calls.append((client, target_date))

        desc = SourceDescriptor(
            name="test_custom",
            display_name="Test Custom",
            description="A test source",
            params=[SourceParam("token", "Auth token", required=True)],
        )
        register_source(desc, fake_fetcher)

        assert "test_custom" in source_names()
        retrieved = get_fetcher("test_custom")
        assert retrieved is fake_fetcher

        # Clean up to avoid polluting other tests
        _REGISTRY.pop("test_custom", None)

    def test_custom_source_before_builtins_does_not_hide_them(self):
        # Regression: a custom source registered before any lookup must not make
        # _ensure_builtins believe built-ins are already present.
        import bmlib.publications.fetchers.registry as reg

        saved_registry = dict(reg._REGISTRY)
        saved_flag = reg._builtins_registered
        try:
            reg._REGISTRY.clear()
            reg._builtins_registered = False

            def fake_fetcher(client, target_date, *, on_record, on_progress=None, **config):
                return None

            register_source(
                SourceDescriptor(name="custom_first", display_name="C", description="d"),
                fake_fetcher,
            )

            # Built-ins must still resolve.
            desc, _ = get_source("pubmed")
            assert desc.name == "pubmed"
            assert "custom_first" in source_names()
        finally:
            reg._REGISTRY.clear()
            reg._REGISTRY.update(saved_registry)
            reg._builtins_registered = saved_flag

    def test_builtin_registration_failure_is_retried(self, monkeypatch):
        # A non-ImportError failure during built-in registration must not latch
        # the registered flag — the next lookup retries instead of silently
        # resolving without the built-ins forever.
        import pytest

        import bmlib.publications.fetchers.registry as reg

        saved_registry = dict(reg._REGISTRY)
        saved_flag = reg._builtins_registered
        try:
            reg._REGISTRY.clear()
            reg._builtins_registered = False

            real_register = reg._register_builtins
            calls = {"n": 0}

            def flaky_register():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient failure")
                real_register()

            monkeypatch.setattr(reg, "_register_builtins", flaky_register)

            with pytest.raises(RuntimeError):
                reg.get_source("pubmed")

            desc, _ = reg.get_source("pubmed")
            assert desc.name == "pubmed"
            assert calls["n"] == 2
        finally:
            reg._REGISTRY.clear()
            reg._REGISTRY.update(saved_registry)
            reg._builtins_registered = saved_flag
