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
