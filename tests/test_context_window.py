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

"""Tests for the known context-window table, held to
``tests/fixtures/context_window.json`` (the Rust port reads the same file),
the reader of provider metadata, and ``LLMClient.context_window`` with each
provider's lookup (fake SDK clients, no network)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bmlib.llm import context_window as cw
from bmlib.llm.client import LLMClient
from bmlib.llm.context_window import (
    KNOWN_CONTEXT_WINDOWS,
    ContextWindowError,
    NoSuchModelError,
    known_context_window,
    read_context_window,
)
from bmlib.llm.providers.anthropic import AnthropicProvider
from bmlib.llm.providers.deepseek import DeepSeekProvider
from bmlib.llm.providers.gemini import GeminiProvider
from bmlib.llm.providers.mistral import MistralProvider
from bmlib.llm.providers.ollama import OllamaProvider
from bmlib.llm.providers.openai_provider import OpenAIProvider

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "context_window.json").read_text())


def test_table_matches_the_fixture():
    rows = {(r["provider"], r["model"], r["window"]) for r in FIXTURE["table"]}
    assert len(rows) == len(FIXTURE["table"])
    assert rows == {(p, m, w) for (p, m), w in KNOWN_CONTEXT_WINDOWS.items()}


@pytest.mark.parametrize("case", FIXTURE["table_lookups"], ids=lambda c: c["name"])
def test_table_lookups(case):
    expected = None if case["expect"].get("unknown") else case["expect"]["window"]
    assert known_context_window(case["provider"], case["model"]) == expected


@pytest.mark.parametrize("case", FIXTURE["metadata"], ids=lambda c: c["name"])
def test_metadata_cases(case):
    expect = case["expect"]
    args = (case["provider"], case["model"], case["body"])
    if expect.get("error") == "no_such_model":
        with pytest.raises(NoSuchModelError) as exc:
            read_context_window(*args)
        assert exc.value.provider == case["provider"]
        assert exc.value.model == case["model"]
    else:
        want = None if expect.get("unknown") else expect["window"]
        assert read_context_window(*args) == want


def test_provider_without_metadata_is_a_value_error():
    with pytest.raises(ValueError):
        read_context_window("openai", "gpt-4o-mini", {})


def test_missing_field_logs_info(caplog):
    with caplog.at_level(logging.INFO, logger="bmlib.llm.context_window"):
        assert read_context_window("anthropic", "m", {}) is None
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "max_input_tokens" in caplog.text


def test_refused_value_logs_warning_quoting_it(caplog):
    with caplog.at_level(logging.INFO, logger="bmlib.llm.context_window"):
        assert read_context_window("anthropic", "m", {"max_input_tokens": "131072"}) is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "'131072'" in caplog.text


def test_wrong_typed_body_logs_warning(caplog):
    with caplog.at_level(logging.INFO, logger="bmlib.llm.context_window"):
        assert read_context_window("anthropic", "m", None) is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


class _Dumpable:
    """Stands in for an SDK model object that converts itself to a dict."""

    def __init__(self, body: dict, method: str = "to_dict") -> None:
        setattr(self, method, lambda: body)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Record the backoff delays instead of waiting them out."""
    delays: list[float] = []
    monkeypatch.setattr(cw.time, "sleep", delays.append)
    return delays


def _client_with(provider) -> LLMClient:
    client = LLMClient(default_provider=provider.PROVIDER_NAME)
    client._providers[provider.PROVIDER_NAME] = provider
    return client


class _Models:
    """A fake ``client.models`` whose calls return, or raise, what it is told."""

    def __init__(self, retrieve=None, list_=None, raises=None) -> None:
        self._retrieve, self._list, self._raises = retrieve, list_, raises
        self.retrieved: list[str] = []

    def retrieve(self, model):
        self.retrieved.append(model)
        if self._raises:
            raise self._raises
        return self._retrieve

    def list(self):
        if self._raises:
            raise self._raises
        return self._list


def _not_found(cls):
    resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
    return cls("nope", response=resp, body=None)


class TestClientRouting:
    def test_client_context_window_routes_provider_prefix(self):
        provider = OpenAIProvider(api_key="k")
        client = _client_with(provider)
        assert client.context_window("openai:gpt-4o-mini") == 128_000
        assert client.context_window("gpt-4o-mini", provider="openai") == 128_000
        assert client.context_window("gpt-4o-mini") == 128_000  # default provider

    def test_unknown_provider_propagates(self):
        with pytest.raises(Exception):  # noqa: B017 - whatever _get_provider raises
            LLMClient().context_window("nosuchprovider:m")

    def test_openai_context_window_needs_no_request(self):
        provider = OpenAIProvider(api_key="k")
        provider._client = None  # any _get_client() would build a real SDK client
        provider._get_client = lambda: pytest.fail("made a request")
        assert provider.context_window("gpt-4o-mini") == 128_000
        assert provider.context_window("no-such-model-xyz") is None


class TestAnthropic:
    def test_reads_max_input_tokens(self):
        p = AnthropicProvider(api_key="k")
        p._client = SimpleNamespace(
            models=_Models(retrieve=_Dumpable({"id": "m", "max_input_tokens": 200_000}))
        )
        assert p.context_window("m") == 200_000

    def test_model_dump_fallback(self):
        p = AnthropicProvider(api_key="k")
        body = _Dumpable({"max_input_tokens": 7}, method="model_dump")
        p._client = SimpleNamespace(models=_Models(retrieve=body))
        assert p.context_window("m") == 7

    def test_not_found(self):
        import anthropic

        p = AnthropicProvider(api_key="k")
        p._client = SimpleNamespace(models=_Models(raises=_not_found(anthropic.NotFoundError)))
        with pytest.raises(NoSuchModelError) as ei:
            p.context_window("m")
        assert (ei.value.provider, ei.value.model) == ("anthropic", "m")

    def test_other_failure_is_chained(self):
        p = AnthropicProvider(api_key="k")
        p._client = SimpleNamespace(models=_Models(raises=RuntimeError("boom")))
        with pytest.raises(ContextWindowError) as ei:
            p.context_window("m")
        assert isinstance(ei.value.__cause__, RuntimeError)


class TestOpenAICompatible:
    def test_mistral_reads_model_object(self):
        p = MistralProvider(api_key="k")
        p._client = SimpleNamespace(
            models=_Models(retrieve=_Dumpable({"id": "m", "max_context_length": 32_000}))
        )
        assert p.context_window("m") == 32_000

    def test_mistral_not_found(self):
        import openai

        p = MistralProvider(api_key="k")
        p._client = SimpleNamespace(models=_Models(raises=_not_found(openai.NotFoundError)))
        with pytest.raises(NoSuchModelError):
            p.context_window("m")

    def test_deepseek_reads_listed_model(self):
        p = DeepSeekProvider(api_key="k")
        listed = [
            _Dumpable({"id": "other", "context_window": 1}),
            _Dumpable({"id": "deepseek-chat", "context_window": 128_000}),
        ]
        p._client = SimpleNamespace(models=_Models(list_=listed))
        assert p.context_window("deepseek-chat") == 128_000

    def test_deepseek_absent_model(self):
        p = DeepSeekProvider(api_key="k")
        p._client = SimpleNamespace(models=_Models(list_=[]))
        with pytest.raises(NoSuchModelError):
            p.context_window("deepseek-chat")

    def test_deepseek_failure_is_chained(self):
        p = DeepSeekProvider(api_key="k")
        p._client = SimpleNamespace(models=_Models(raises=ValueError("bad")))
        with pytest.raises(ContextWindowError) as ei:
            p.context_window("deepseek-chat")
        assert isinstance(ei.value.__cause__, ValueError)


class TestGemini:
    def _provider(self, monkeypatch, responder):
        calls: list[tuple[str, dict]] = []

        def fake_get(url, headers=None, **kw):
            calls.append((url, headers))
            return responder(url)

        monkeypatch.setattr(httpx, "get", fake_get)
        return GeminiProvider(api_key="secret"), calls

    @staticmethod
    def _resp(status, body=None):
        return httpx.Response(status, json=body or {}, request=httpx.Request("GET", "http://x"))

    def test_reads_input_token_limit_from_native_url(self, monkeypatch):
        p, calls = self._provider(
            monkeypatch, lambda u: self._resp(200, {"inputTokenLimit": 1_048_576})
        )
        assert p.context_window("gemini-2.0-flash") == 1_048_576
        assert calls == [
            (
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash",
                {"x-goog-api-key": "secret"},
            )
        ]

    def test_404_is_no_such_model_and_not_retried(self, monkeypatch):
        p, calls = self._provider(monkeypatch, lambda u: self._resp(404))
        with pytest.raises(NoSuchModelError):
            p.context_window("nope")
        assert len(calls) == 1

    def test_429_retried_then_answers(self, monkeypatch):
        answers = iter([self._resp(429), self._resp(503), self._resp(200, {"inputTokenLimit": 9})])
        p, calls = self._provider(monkeypatch, lambda u: next(answers))
        assert p.context_window("m") == 9
        assert len(calls) == 3

    def test_400_is_not_retried(self, monkeypatch):
        p, calls = self._provider(monkeypatch, lambda u: self._resp(400))
        with pytest.raises(ContextWindowError):
            p.context_window("m")
        assert len(calls) == 1

    def test_transport_error_retried_then_fails(self, monkeypatch):
        def boom(url):
            raise httpx.ConnectError("down")

        p, calls = self._provider(monkeypatch, boom)
        with pytest.raises(ContextWindowError) as ei:
            p.context_window("m")
        assert len(calls) == cw.CONTEXT_LOOKUP_ATTEMPTS
        assert isinstance(ei.value.__cause__, httpx.ConnectError)


class _ShowClient:
    """A fake ``ollama.Client`` whose ``show`` runs through a script."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls = 0

    def show(self, model):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


_SHOW = {"model_info": {"general.architecture": "llama", "llama.context_length": 8192}}


class TestOllamaLookup:
    def _p(self, script):
        p = OllamaProvider()
        p._client = _ShowClient(script)
        return p

    def test_reads_dict(self):
        assert self._p([_SHOW]).context_window("m") == 8192

    def test_reads_pydantic_show_response(self):
        ollama = pytest.importorskip("ollama")
        resp = ollama.ShowResponse(
            model_info={"general.architecture": "llama", "llama.context_length": 4096}
        )
        assert self._p([resp]).context_window("m") == 4096

    def test_connection_error_three_times(self, _no_sleep):
        p = self._p([ConnectionError("a")] * 3)
        with pytest.raises(ContextWindowError) as ei:
            p.context_window("m")
        assert p._client.calls == cw.CONTEXT_LOOKUP_ATTEMPTS
        assert isinstance(ei.value.__cause__, ConnectionError)
        assert _no_sleep == [1.0, 2.0]  # base * 2**attempt, none after the last

    def test_fails_twice_then_answers(self):
        p = self._p([ConnectionError("a"), ConnectionError("b"), _SHOW])
        assert p.context_window("m") == 8192
        assert p._client.calls == 3

    def test_404_is_no_such_model_and_not_retried(self):
        ollama = pytest.importorskip("ollama")
        p = self._p([ollama.ResponseError("model not found", 404)])
        with pytest.raises(NoSuchModelError):
            p.context_window("m")
        assert p._client.calls == 1

    def test_5xx_retried_other_status_not(self):
        ollama = pytest.importorskip("ollama")
        p = self._p([ollama.ResponseError("busy", 503), _SHOW])
        assert p.context_window("m") == 8192
        q = self._p([ollama.ResponseError("bad", 400)])
        with pytest.raises(ContextWindowError):
            q.context_window("m")
        assert q._client.calls == 1
