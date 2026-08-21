"""Tests for the source fetcher registry."""

from __future__ import annotations

import pytest

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


class TestAFetcherDeclaresResumability:
    """New keywords reach only fetchers that asked for them."""

    def test_a_descriptor_is_not_resumable_by_default(self):
        descriptor = SourceDescriptor(
            name="custom", display_name="Custom", description="A third-party source"
        )

        assert descriptor.resumable is False

    def test_pubmed_declares_itself_resumable(self):
        descriptor, _ = get_source("pubmed")

        assert descriptor.resumable is True

    def test_the_other_builtins_do_not(self):
        for name in ("biorxiv", "medrxiv", "openalex"):
            descriptor, _ = get_source(name)
            assert descriptor.resumable is False, name


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

    def test_custom_source_can_override_a_builtin_name(self):
        # Regression: register_source did not trigger lazy built-in
        # registration, so overriding a built-in name before any lookup was
        # silently reverted the first time _ensure_builtins ran.
        import bmlib.publications.fetchers.registry as reg

        saved_registry = dict(reg._REGISTRY)
        saved_flag = reg._builtins_registered
        try:
            reg._REGISTRY.clear()
            reg._builtins_registered = False

            def custom_pubmed(client, target_date, *, on_record, on_progress=None, **config):
                return "custom"

            register_source(
                SourceDescriptor(name="pubmed", display_name="Custom", description="d"),
                custom_pubmed,
            )

            # The override must survive lazy built-in registration.
            desc, fetcher = get_source("pubmed")
            assert fetcher is custom_pubmed
            assert desc.display_name == "Custom"
            # Other built-ins are still registered.
            assert "biorxiv" in source_names()
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


class TestARegistrationDeclaringResumableIsChecked:
    """`resumable=True` is a promise about the fetcher's signature.

    `sync()` reads the *descriptor* to decide whether to pass the three resume
    keywords, so a descriptor that declares more than its fetcher accepts
    raises `TypeError` inside the per-day handler — which records the day
    `failed`, on every day, on every run, forever. Loud, but at the wrong
    place and once per day rather than once per mistake.
    """

    def test_a_resumable_fetcher_missing_the_keywords_is_refused(self):
        def fetcher(client, target_date, *, on_record, on_progress=None):
            raise AssertionError("never called")

        with pytest.raises(ValueError, match="completed_parts"):
            register_source(
                SourceDescriptor(
                    name="unsound_resumable",
                    display_name="Unsound",
                    description="declares more than it accepts",
                    resumable=True,
                ),
                fetcher,
            )

    def test_a_resumable_fetcher_accepting_them_by_name_is_registered(self):
        def fetcher(
            client,
            target_date,
            *,
            on_record,
            on_progress=None,
            completed_parts=None,
            on_part_finished=None,
            on_part_skipped=None,
        ):
            raise AssertionError("never called")

        register_source(
            SourceDescriptor(
                name="sound_resumable",
                display_name="Sound",
                description="accepts what it declares",
                resumable=True,
            ),
            fetcher,
        )

        assert get_source("sound_resumable")[0].resumable is True

    def test_a_resumable_fetcher_accepting_them_through_kwargs_is_registered(self):
        # `**config` is how the built-in fetchers absorb per-source settings,
        # so it has to satisfy the check.
        def fetcher(client, target_date, *, on_record, on_progress=None, **config):
            raise AssertionError("never called")

        register_source(
            SourceDescriptor(
                name="kwargs_resumable",
                display_name="Kwargs",
                description="absorbs them through **config",
                resumable=True,
            ),
            fetcher,
        )

        assert get_source("kwargs_resumable")[0].resumable is True

    def test_a_non_resumable_fetcher_is_not_asked_for_them(self):
        # The negative control: the default is what every third-party fetcher
        # written against an earlier bmlib has, and it must stay registrable.
        def fetcher(client, target_date, *, on_record, on_progress=None):
            raise AssertionError("never called")

        register_source(
            SourceDescriptor(
                name="ordinary_source",
                display_name="Ordinary",
                description="does not resume",
            ),
            fetcher,
        )

        assert get_source("ordinary_source")[0].resumable is False
