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
import logging
import pickle
import sys
import threading
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import keyring
import pytest
from keyring.backend import KeyringBackend

from bmlib import secrets
from bmlib.secrets import (
    API_KEYS_ACCOUNT,
    KeyBundle,
    KeyringVault,
    MemorySecrets,
    SecretError,
    SecretErrorKind,
    read_key_list,
    write_key_list,
)

UNREADABLE = "the keychain item is not a valid key list"

STEP_FIELDS = {"op", "provider", "key", "expect", "fail_write", "fail_read", "another_writer"}
"""Every field a bundle step may carry; a misspelt one would read as absent."""

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "secret_key_list.json").read_text())


def test_fixture_has_cases() -> None:
    assert FIXTURE["read"] and FIXTURE["write"] and FIXTURE["bundle"]


@pytest.mark.parametrize("case", FIXTURE["read"], ids=lambda c: c["name"])
def test_read_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    if case["expect"].get("error") == "unreadable-item":
        with pytest.raises(SecretError) as caught:
            read_key_list(case["text"])
        assert str(caught.value) == UNREADABLE
        assert caught.value.kind is SecretErrorKind.UNREADABLE_ITEM
        # No parse error rides along: one can quote the item (JSONDecodeError.doc).
        assert caught.value.__context__ is None and caught.value.__cause__ is None
        return
    assert read_key_list(case["text"]) == case["expect"]["keys"]


@pytest.mark.parametrize("case", FIXTURE["write"], ids=lambda c: c["name"])
def test_write_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    text = write_key_list(case["keys"])
    assert text == case["text"]
    assert read_key_list(text) == case["keys"]


class FakeVault:
    """An in-memory vault that counts reads and fails a read or a write, with a
    given kind, when told to."""

    def __init__(self, item: str | None) -> None:
        self.item = item
        self.reads = 0
        self.fail_read: SecretErrorKind | None = None
        self.fail_write: SecretErrorKind | None = None

    def read(self) -> str | None:
        self.reads += 1
        if self.fail_read:
            raise SecretError("locked", self.fail_read)
        return self.item

    def write(self, text: str) -> None:
        if self.fail_write:
            raise SecretError("denied", self.fail_write)
        self.item = text


@pytest.mark.parametrize("case", FIXTURE["bundle"], ids=lambda c: c["name"])
def test_bundle_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    vault = FakeVault(case["item"])
    store = KeyBundle(vault)
    for i, step in enumerate(case["steps"]):
        assert set(step) <= STEP_FIELDS, f"step {i}"
        if "another_writer" in step:
            vault.item = step["another_writer"]
        # A kind the enum does not name raises here, so the fixture cannot drift from it.
        vault.fail_write = SecretErrorKind(step["fail_write"]) if "fail_write" in step else None
        vault.fail_read = SecretErrorKind(step["fail_read"]) if "fail_read" in step else None
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
        except SecretError as e:
            got = {"error": e.kind.value}
        vault.fail_write = None
        vault.fail_read = None
        assert got == step["expect"], f"step {i}"
    assert vault.item == case["item_after"]
    assert vault.reads == case["reads"]


def test_nesting_past_the_recursion_limit_is_unreadable() -> None:
    # Too deep for any Python's json recursion limit; generated, not in the fixture.
    deep = '{"openai":' + "[" * 100_000 + "]" * 100_000 + "}"
    with pytest.raises(SecretError):
        read_key_list(deep)


def test_the_kinds_are_rusts() -> None:
    # Rust's contract test holds its kinds to the same list.
    assert [k.value for k in SecretErrorKind] == FIXTURE["kinds"]


def test_a_secret_error_pickles_with_its_kind() -> None:
    e = SecretError("two items", SecretErrorKind.AMBIGUOUS_ITEM)
    back = pickle.loads(pickle.dumps(e))
    assert (type(back), str(back), back.kind) == (SecretError, "two items", e.kind)


def test_a_kind_given_by_name_is_the_member() -> None:
    e = SecretError("bad bytes", "unreadable-item")  # type: ignore[arg-type]  # an untyped Vault's call
    assert e.kind is SecretErrorKind.UNREADABLE_ITEM


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        SecretError("x", "bogus")  # type: ignore[arg-type]  # the misspelling under test


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
        self.not_utf8 = False

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            raise RuntimeError("the keychain is locked")
        if self.not_utf8:
            # What the macOS and Secret Service backends raise: it holds the item's bytes.
            raise UnicodeDecodeError("utf-8", b'\xff{"openai":"sk-leaky"}', 0, 1, "bad")
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("the keychain is locked")
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.items[(service, username)]


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[DictKeyring]:
    """A :class:`DictKeyring` as the process's keyring, restored afterwards,
    on the platform that goes through ``keyring`` (macOS)."""
    monkeypatch.setattr(secrets, "_PLATFORM", "darwin")
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
    for call in (vault.read, lambda: vault.write("{}")):
        with pytest.raises(SecretError) as caught:
            call()
        assert str(caught.value) == "RuntimeError: the keychain is locked"
        assert caught.value.kind is SecretErrorKind.STORE
        assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_an_item_that_is_not_utf8_is_unreadable_and_replaced(backend: DictKeyring) -> None:
    backend.not_utf8 = True
    store = KeyBundle.keyring("com.example.app")
    with pytest.raises(SecretError) as caught:
        store.get("openai")
    assert str(caught.value) == UNREADABLE and caught.value.__context__ is None
    assert caught.value.kind is SecretErrorKind.UNREADABLE_ITEM
    store.set("openai", "k1")
    assert backend.items == {("com.example.app", API_KEYS_ACCOUNT): '{"openai":"k1"}'}


@pytest.mark.parametrize(
    ("platform", "module"),
    [("darwin", "keyring"), ("win32", "win32ctypes.pywin32"), ("linux", "secretstorage")],
)
def test_a_missing_keyring_package_is_a_secret_error(
    monkeypatch: pytest.MonkeyPatch, platform: str, module: str
) -> None:
    monkeypatch.setattr(secrets, "_PLATFORM", platform)
    monkeypatch.delitem(sys.modules, module, raising=False)
    real_import = builtins.__import__

    def no_keyring(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == module:
            raise ModuleNotFoundError("No module named 'jeepney'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_keyring)
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    # What the import raised is said, since a present keyring can fail on its own dependency.
    said = (
        "a package KeyringVault needs could not be imported (extra `secrets`): "
        "ModuleNotFoundError: No module named 'jeepney'"
    )
    for call in (vault.read, lambda: vault.write("{}")):
        with pytest.raises(SecretError) as caught:
            call()
        assert str(caught.value) == said


def test_set_refuses_a_lone_surrogate_and_writes_nothing() -> None:
    # Stored, it would make the whole item unreadable, and the next set would replace it.
    vault = FakeVault('{"anthropic":"k0"}')
    store = KeyBundle(vault)
    for provider, key in (("openai", "\ud800"), ("\udfff", "k1")):
        with pytest.raises(SecretError) as caught:
            store.set(provider, key)
        assert caught.value.__context__ is None
        assert caught.value.kind is SecretErrorKind.STORE
    assert vault.item == '{"anthropic":"k0"}'
    assert store.get("anthropic") == "k0"


def test_saving_over_an_unreadable_item_says_so_once(caplog: pytest.LogCaptureFixture) -> None:
    store = KeyBundle(FakeVault("not json sk-leaky"))
    with caplog.at_level(logging.WARNING, logger="bmlib.secrets"):
        store.set("openai", "k1")
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, f"{UNREADABLE}; replacing it with the key being saved")
    ]


class BlockingVault(FakeVault):
    """A vault whose first write waits until released, to hold one ``set`` inside it."""

    def __init__(self) -> None:
        super().__init__(None)
        self.inside = threading.Event()
        self.release = threading.Event()

    def write(self, text: str) -> None:
        if not self.inside.is_set():
            self.inside.set()
            assert self.release.wait(5)
        super().write(text)


def test_concurrent_sets_keep_both_keys() -> None:
    vault = BlockingVault()
    store = KeyBundle(vault)
    first = threading.Thread(target=store.set, args=("a", "1"))
    first.start()
    assert vault.inside.wait(5)
    second = threading.Thread(target=store.set, args=("b", "2"))
    second.start()
    second.join(0.2)  # without the lock it would read the empty item and finish here
    vault.release.set()
    first.join(5)
    second.join(5)
    assert vault.item == '{"a":"1","b":"2"}'


def test_the_vault_is_read_only() -> None:
    store = KeyBundle(FakeVault(None))
    with pytest.raises(AttributeError):
        store.vault = FakeVault(None)  # type: ignore[misc]  # the assignment under test


# --- Windows: the generic credential named by the service (#686) ------------


class FakeWinError(Exception):
    """``pywintypes.error``: a Windows error code and the failing call."""

    def __init__(self, winerror: int, funcname: str) -> None:
        super().__init__(winerror, funcname, "message")
        self.winerror = winerror
        self.funcname = funcname


class FakeCredentialManager:
    """``win32cred`` over a dict of credentials by ``TargetName``, holding each
    blob as UTF-16-LE bytes, as ``pywin32-ctypes`` writes a ``str`` and reads
    back ``bytes``."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_ENTERPRISE = 3

    def __init__(self) -> None:
        self.creds: dict[str, dict[str, Any]] = {}
        self.fail: int | None = None

    def CredRead(self, Type: int, TargetName: str) -> dict[str, Any]:  # noqa: N802, N803
        assert Type == self.CRED_TYPE_GENERIC
        if self.fail:
            raise FakeWinError(self.fail, "CredRead")
        if TargetName not in self.creds:
            raise FakeWinError(1168, "CredRead")
        return dict(self.creds[TargetName])

    def CredWrite(self, credential: dict[str, Any], flags: int) -> None:  # noqa: N802
        assert flags == 0 and credential["Type"] == self.CRED_TYPE_GENERIC
        if self.fail:
            raise FakeWinError(self.fail, "CredWrite")
        blob = credential["CredentialBlob"].encode("utf-16-le")
        self.creds[credential["TargetName"]] = {**credential, "CredentialBlob": blob}


@pytest.fixture
def wincred(monkeypatch: pytest.MonkeyPatch) -> FakeCredentialManager:
    """A :class:`FakeCredentialManager` as ``win32ctypes.pywin32``, on Windows."""
    manager = FakeCredentialManager()
    pywin32 = types.ModuleType("win32ctypes.pywin32")
    pywin32.pywintypes = types.SimpleNamespace(error=FakeWinError)  # type: ignore[attr-defined]
    pywin32.win32cred = manager  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32ctypes", types.ModuleType("win32ctypes"))
    monkeypatch.setitem(sys.modules, "win32ctypes.pywin32", pywin32)
    monkeypatch.setattr(secrets, "_PLATFORM", "win32")
    return manager


def test_windows_names_the_credential_by_the_service_alone(
    wincred: FakeCredentialManager,
) -> None:
    store = KeyBundle.keyring("com.example.app")
    assert store.get("openai") is None
    store.set("openai", "k1")
    store.set("anthropic", "k2")
    store.delete("openai")
    # One credential, Rust's name and Python's; no `api-keys@service` copy keeping k1.
    assert list(wincred.creds) == ["com.example.app"]
    cred = wincred.creds["com.example.app"]
    assert cred["UserName"] == API_KEYS_ACCOUNT
    assert cred["Persist"] == wincred.CRED_PERSIST_ENTERPRISE
    assert cred["CredentialBlob"] == '{"anthropic":"k2"}'.encode("utf-16-le")


def test_windows_reads_a_credential_rust_wrote(wincred: FakeCredentialManager) -> None:
    wincred.creds["com.example.app"] = {
        "TargetName": "com.example.app",
        "UserName": API_KEYS_ACCOUNT,
        "CredentialBlob": '{"openai":"k1"}'.encode("utf-16-le"),
    }
    assert KeyBundle.keyring("com.example.app").get("openai") == "k1"


@pytest.mark.parametrize(
    "blob", [b"\x00", '{"openai":"\ud800"}'.encode("utf-16-le", "surrogatepass")]
)
def test_windows_a_blob_that_is_not_utf16_is_unreadable(
    wincred: FakeCredentialManager, blob: bytes
) -> None:
    wincred.creds["com.example.app"] = {"UserName": API_KEYS_ACCOUNT, "CredentialBlob": blob}
    with pytest.raises(SecretError) as caught:
        KeyBundle.keyring("com.example.app").get("openai")
    assert caught.value.kind is SecretErrorKind.UNREADABLE_ITEM
    assert caught.value.__context__ is None


def test_windows_a_failure_is_a_secret_error(wincred: FakeCredentialManager) -> None:
    wincred.fail = 5  # ERROR_ACCESS_DENIED
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    for call in (vault.read, lambda: vault.write("{}")):
        with pytest.raises(SecretError) as caught:
            call()
        assert caught.value.kind is SecretErrorKind.STORE
        assert str(caught.value).startswith("FakeWinError: (5, ")
        assert caught.value.__context__ is None


# --- Linux: the Secret Service item carrying service and username (#686) -----


class FakeItem:
    """A Secret Service item."""

    def __init__(self, attributes: dict[str, str], secret: bytes, locked: bool = False) -> None:
        self.attributes = attributes
        self.secret = secret
        self.locked = locked
        self.dismiss = False

    def is_locked(self) -> bool:
        return self.locked

    def unlock(self) -> bool:
        self.locked = self.dismiss
        return self.dismiss

    def get_secret(self) -> bytes:
        assert not self.locked
        return self.secret

    def set_secret(self, secret: bytes) -> None:
        assert not self.locked
        self.secret = secret


class FakeSecretService:
    """``secretstorage`` over a list of items: a search matches every item
    carrying the attributes asked for, whatever else it carries, as the
    Secret Service does."""

    def __init__(self) -> None:
        self.items: list[FakeItem] = []
        self.created: list[tuple[str, dict[str, str]]] = []
        self.open_connections = 0
        self.collection = FakeItem({}, b"")
        self.no_bus = False

    def dbus_init(self) -> Any:
        if self.no_bus:
            raise RuntimeError("no D-Bus session bus")
        self.open_connections += 1
        service = self

        class Connection:
            def close(self) -> None:
                service.open_connections -= 1

        return Connection()

    def search_items(self, connection: Any, attributes: dict[str, str]) -> Iterator[FakeItem]:
        return (i for i in self.items if attributes.items() <= i.attributes.items())

    def get_default_collection(self, connection: Any) -> Any:
        service = self

        class Collection:
            def is_locked(self) -> bool:
                return service.collection.is_locked()

            def unlock(self) -> bool:
                return service.collection.unlock()

            def create_item(
                self, label: str, attributes: dict[str, str], secret: bytes, replace: bool
            ) -> None:
                assert not service.collection.locked
                service.created.append((label, attributes))
                service.items.append(FakeItem(dict(attributes), secret))

        return Collection()


@pytest.fixture
def secret_service(monkeypatch: pytest.MonkeyPatch) -> FakeSecretService:
    """A :class:`FakeSecretService` as ``secretstorage``, on Linux."""
    fake = FakeSecretService()
    module = types.ModuleType("secretstorage")
    for name in ("dbus_init", "search_items", "get_default_collection"):
        setattr(module, name, getattr(fake, name))
    monkeypatch.setitem(sys.modules, "secretstorage", module)
    monkeypatch.setattr(secrets, "_PLATFORM", "linux")
    return fake


ATTRIBUTES = {"service": "com.example.app", "username": API_KEYS_ACCOUNT}


def test_linux_keeps_one_item_with_rusts_attributes(secret_service: FakeSecretService) -> None:
    store = KeyBundle.keyring("com.example.app")
    assert store.get("openai") is None
    store.set("openai", "k1")
    store.set("anthropic", "k2")
    # Created once, with exactly Rust's attributes and label; then updated in place.
    assert secret_service.created == [(f"keyring:{API_KEYS_ACCOUNT}@com.example.app", ATTRIBUTES)]
    assert [i.secret for i in secret_service.items] == [b'{"anthropic":"k2","openai":"k1"}']
    assert secret_service.open_connections == 0


def test_linux_updates_an_item_another_writer_made(secret_service: FakeSecretService) -> None:
    # Python's `keyring` adds `application`; such an item is found and updated, not doubled.
    secret_service.items.append(
        FakeItem({**ATTRIBUTES, "application": "Python keyring library"}, b'{"a":"1"}', True)
    )
    store = KeyBundle.keyring("com.example.app")
    store.set("b", "2")
    assert secret_service.created == []
    assert [i.secret for i in secret_service.items] == [b'{"a":"1","b":"2"}']


def test_linux_two_items_are_ambiguous(secret_service: FakeSecretService) -> None:
    secret_service.items = [FakeItem(dict(ATTRIBUTES), b"{}"), FakeItem(dict(ATTRIBUTES), b"{}")]
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    for call in (vault.read, lambda: vault.write('{"a":"1"}')):
        with pytest.raises(SecretError) as caught:
            call()
        assert caught.value.kind is SecretErrorKind.AMBIGUOUS_ITEM
    assert [i.secret for i in secret_service.items] == [b"{}", b"{}"]
    assert secret_service.open_connections == 0


def test_linux_a_secret_that_is_not_utf8_is_unreadable(secret_service: FakeSecretService) -> None:
    secret_service.items.append(FakeItem(dict(ATTRIBUTES), b'\xff{"openai":"sk-leaky"}'))
    with pytest.raises(SecretError) as caught:
        KeyBundle.keyring("com.example.app").get("openai")
    assert caught.value.kind is SecretErrorKind.UNREADABLE_ITEM
    assert caught.value.__context__ is None


def test_linux_a_dismissed_unlock_is_a_secret_error(secret_service: FakeSecretService) -> None:
    item = FakeItem(dict(ATTRIBUTES), b"{}", locked=True)
    item.dismiss = True
    secret_service.items.append(item)
    with pytest.raises(SecretError) as caught:
        KeyringVault("com.example.app", API_KEYS_ACCOUNT).read()
    assert caught.value.kind is SecretErrorKind.STORE
    assert "dismissed" in str(caught.value)
    assert secret_service.open_connections == 0


def test_linux_an_item_of_another_account_is_left_alone(
    secret_service: FakeSecretService,
) -> None:
    other = FakeItem({"service": "com.example.app", "username": "oauth-token"}, b"token")
    secret_service.items.append(other)
    store = KeyBundle.keyring("com.example.app")
    assert store.get("openai") is None
    store.set("openai", "k1")
    assert other.secret == b"token"
    assert secret_service.created == [(f"keyring:{API_KEYS_ACCOUNT}@com.example.app", ATTRIBUTES)]


@pytest.mark.parametrize("dismiss", [False, True])
def test_linux_a_locked_collection_is_unlocked_before_the_first_write(
    secret_service: FakeSecretService, dismiss: bool
) -> None:
    secret_service.collection.locked = True
    secret_service.collection.dismiss = dismiss
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    if dismiss:
        with pytest.raises(SecretError, match="dismissed") as caught:
            vault.write("{}")
        assert caught.value.kind is SecretErrorKind.STORE
        assert secret_service.items == []
    else:
        vault.write("{}")
        assert [i.secret for i in secret_service.items] == [b"{}"]
    assert secret_service.open_connections == 0


def test_linux_a_locked_item_is_unlocked_before_a_write(secret_service: FakeSecretService) -> None:
    item = FakeItem(dict(ATTRIBUTES), b"{}", locked=True)
    secret_service.items.append(item)
    KeyringVault("com.example.app", API_KEYS_ACCOUNT).write('{"a":"1"}')
    assert item.secret == b'{"a":"1"}'


def test_linux_no_secret_service_is_a_store_error(secret_service: FakeSecretService) -> None:
    secret_service.no_bus = True
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    for call in (vault.read, lambda: vault.write("{}")):
        with pytest.raises(SecretError) as caught:
            call()
        assert caught.value.kind is SecretErrorKind.STORE
        assert str(caught.value) == "RuntimeError: no D-Bus session bus"
        assert caught.value.__context__ is None


@pytest.mark.parametrize("platform", ["freebsd14", "openbsd7", "netbsd10", "dragonfly6"])
def test_the_bsds_use_the_secret_service_as_rust_does(
    secret_service: FakeSecretService, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(secrets, "_PLATFORM", platform)
    KeyBundle.keyring("com.example.app").set("openai", "k1")
    assert secret_service.created == [(f"keyring:{API_KEYS_ACCOUNT}@com.example.app", ATTRIBUTES)]


@pytest.mark.parametrize("owner", [API_KEYS_ACCOUNT, ""])
def test_windows_reads_and_names_a_credential_of_this_account(
    wincred: FakeCredentialManager, owner: str
) -> None:
    # "" is what Rust leaves if naming the account after its first write failed.
    wincred.creds["com.example.app"] = {
        "TargetName": "com.example.app",
        "UserName": owner,
        "CredentialBlob": '{"openai":"k1"}'.encode("utf-16-le"),
    }
    store = KeyBundle.keyring("com.example.app")
    assert store.get("openai") == "k1"
    store.set("anthropic", "k2")
    assert wincred.creds["com.example.app"]["UserName"] == API_KEYS_ACCOUNT


def test_windows_a_credential_of_another_account_is_refused_and_kept(
    wincred: FakeCredentialManager,
) -> None:
    # What Python's `keyring.set_password(service, "db", ...)` leaves at the service's name.
    theirs = {"TargetName": "com.example.app", "UserName": "db", "CredentialBlob": b"p\x00w\x00"}
    wincred.creds["com.example.app"] = dict(theirs)
    vault = KeyringVault("com.example.app", API_KEYS_ACCOUNT)
    for call in (vault.read, lambda: vault.write('{"openai":"k1"}')):
        with pytest.raises(SecretError) as caught:
            call()
        assert caught.value.kind is SecretErrorKind.STORE
        assert str(caught.value) == (
            "the keychain item 'com.example.app' belongs to account 'db', not 'api-keys'"
        )
    assert wincred.creds == {"com.example.app": theirs}
