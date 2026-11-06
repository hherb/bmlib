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

"""The shared key-list contract, ``tests/fixtures/secret_key_list.json`` (the
same file Rust's ``rust/bmlib/tests/secrets_contract.rs`` reads, #634), and
:class:`KeyringVault` over a stand-in keyring backend."""

from __future__ import annotations

import builtins
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import keyring
import pytest
from keyring.backend import KeyringBackend

from bmlib.secrets import (
    API_KEYS_ACCOUNT,
    KeyBundle,
    KeyringVault,
    MemorySecrets,
    SecretError,
    read_key_list,
    write_key_list,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "secret_key_list.json").read_text())


def test_fixture_has_cases() -> None:
    assert FIXTURE["read"] and FIXTURE["write"] and FIXTURE["bundle"]


@pytest.mark.parametrize("case", FIXTURE["read"], ids=lambda c: c["name"])
def test_read_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    if case["expect"].get("error") == "unreadable":
        with pytest.raises(SecretError) as caught:
            read_key_list(case["text"])
        assert "sk-" not in str(caught.value)
        return
    assert read_key_list(case["text"]) == case["expect"]["keys"]


@pytest.mark.parametrize("case", FIXTURE["write"], ids=lambda c: c["name"])
def test_write_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    text = write_key_list(case["keys"])
    assert text == case["text"]
    assert read_key_list(text) == case["keys"]


class FakeVault:
    """An in-memory vault that counts reads and fails a read or a write when told to."""

    def __init__(self, item: str | None) -> None:
        self.item = item
        self.reads = 0
        self.fail_read = False
        self.fail_write = False

    def read(self) -> str | None:
        self.reads += 1
        if self.fail_read:
            raise SecretError("locked")
        return self.item

    def write(self, text: str) -> None:
        if self.fail_write:
            raise SecretError("denied")
        self.item = text


@pytest.mark.parametrize("case", FIXTURE["bundle"], ids=lambda c: c["name"])
def test_bundle_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    vault = FakeVault(case["item"])
    store = KeyBundle(vault)
    for i, step in enumerate(case["steps"]):
        vault.fail_write = step.get("fail_write", False)
        vault.fail_read = step.get("fail_read", False)
        got: object
        try:
            if step["op"] == "get":
                got = {"key": store.get(step["provider"])}
            elif step["op"] == "set":
                store.set(step["provider"], step["key"])
                got = "ok"
            else:
                assert step["op"] == "delete"
                store.delete(step["provider"])
                got = "ok"
        except SecretError:
            got = "error"
        vault.fail_write = False
        vault.fail_read = False
        assert got == step["expect"], f"step {i}"
    assert vault.item == case["item_after"]
    assert vault.reads == case["reads"]


def test_nesting_past_the_recursion_limit_is_unreadable() -> None:
    # Too deep for any Python's json recursion limit; generated, not in the fixture.
    deep = '{"openai":' + "[" * 100_000 + "]" * 100_000 + "}"
    with pytest.raises(SecretError):
        read_key_list(deep)


def test_memory_secrets_set_get_delete() -> None:
    s = MemorySecrets()
    s.set("openai", "k1")
    s.set("openai", "k2")
    assert s.get("openai") == "k2"
    s.delete("openai")
    s.delete("openai")
    assert s.get("openai") is None


class DictKeyring(KeyringBackend):
    """A keyring backend holding items in a dict, or failing every call."""

    priority = 1

    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}
        self.fail = fail

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            raise RuntimeError("the keychain is locked")
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("the keychain is locked")
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.items[(service, username)]


@pytest.fixture
def backend() -> Iterator[DictKeyring]:
    """A :class:`DictKeyring` as the process's keyring, restored afterwards."""
    before = keyring.get_keyring()
    ring = DictKeyring()
    keyring.set_keyring(ring)
    yield ring
    keyring.set_keyring(before)


def test_keyring_bundle_keeps_every_key_in_one_item(backend: DictKeyring) -> None:
    store = KeyBundle.keyring("com.example.app")
    assert store.get("openai") is None
    store.set("openai", "k1")
    store.set("anthropic", "k2")
    assert backend.items == {
        ("com.example.app", API_KEYS_ACCOUNT): '{"anthropic":"k2","openai":"k1"}'
    }
    assert KeyBundle.keyring("com.example.app").get("anthropic") == "k2"


def test_a_backend_failure_is_a_secret_error(backend: DictKeyring) -> None:
    backend.fail = True
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    with pytest.raises(SecretError, match="RuntimeError: the keychain is locked"):
        vault.read()
    with pytest.raises(SecretError, match="RuntimeError: the keychain is locked"):
        vault.write("{}")


def test_a_missing_keyring_package_is_a_secret_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_keyring(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_keyring)
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    with pytest.raises(SecretError, match="extra `secrets`"):
        vault.read()
    with pytest.raises(SecretError, match="extra `secrets`"):
        vault.write("{}")
