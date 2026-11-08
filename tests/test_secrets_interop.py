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

"""Rust and Python take turns on one real keychain item (#686).

Runs only where ``BMLIB_KEYCHAIN_INTEROP_BIN`` names Rust's
``keychain_vault`` example (in ``rust/``, ``cargo build -p bmlib --features
keyring --example keychain_vault``): CI's ``keychain-interop`` job, on
Windows and on Linux (gnome-keyring). That job also sets
``BMLIB_REQUIRE_KEYCHAIN_INTEROP=1``, which turns the skip into a failure, so
a lost variable cannot leave the tests unrun behind a green job. It writes
and then removes items under a fresh service name. Not on macOS, whose
item Python reaches through ``keyring`` as Rust names it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest

from bmlib.secrets import API_KEYS_ACCOUNT, KeyBundle, SecretError, SecretErrorKind

BINARY = os.environ.get("BMLIB_KEYCHAIN_INTEROP_BIN")

if not BINARY and os.environ.get("BMLIB_REQUIRE_KEYCHAIN_INTEROP") == "1":
    pytest.fail("BMLIB_REQUIRE_KEYCHAIN_INTEROP=1 but BMLIB_KEYCHAIN_INTEROP_BIN is not set")

pytestmark = pytest.mark.skipif(
    not BINARY or sys.platform == "darwin",
    reason="needs BMLIB_KEYCHAIN_INTEROP_BIN, on Windows or a Secret Service",
)

KEY = "ключ-🔑"
"""A key outside ASCII and the BMP: the encodings are the contract (UTF-16-LE
in a Windows credential, UTF-8 in a Secret Service item)."""


def rust(service: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Rust's ``KeyringVault`` on ``service``: ``read``, or ``write <text>``."""
    return subprocess.run(
        [BINARY or "", service, *args], capture_output=True, encoding="utf-8", errors="strict"
    )


def rust_read(service: str) -> str | None:
    """The item's text as Rust's ``KeyringVault`` reads it."""
    done = rust(service, "read")
    if done.returncode == 3:
        return None
    assert done.returncode == 0, done.stderr
    return done.stdout


def rust_write(service: str, text: str) -> None:
    """Write the item as Rust's ``KeyringVault`` writes it."""
    done = rust(service, "write", text)
    assert done.returncode == 0, done.stderr


def assert_both_refuse(
    service: str, kind: SecretErrorKind, message: str, rust_message: str | None = None
) -> None:
    """A read and a write in each language fail with ``kind``: Python's with
    ``message``, Rust's with one starting ``rust_message`` (``message`` if
    not given)."""
    for args in (("read",), ("write", "{}")):
        done = rust(service, *args)
        assert done.returncode == 1, done
        assert done.stderr.startswith(f"{kind}: {rust_message or message}"), done.stderr
    store = KeyBundle.keyring(service)
    for call in (lambda: store.get("a"), lambda: store.set("a", "1")):
        with pytest.raises(SecretError) as caught:
            call()
        assert (caught.value.kind, str(caught.value)) == (kind, message)


def platform_items(service: str) -> list[dict[str, str]]:
    """What the platform holds for ``service``, apart from bmlib: each Windows
    credential named by the service, ``keyring``'s ``account@service`` copy or
    the crate's default ``account.service``, or each Secret Service item
    carrying ``service``, as its attributes (``target`` and ``user`` on Windows)."""
    if sys.platform == "win32":
        from win32ctypes.pywin32 import pywintypes, win32cred

        found = []
        for target in (service, f"{API_KEYS_ACCOUNT}@{service}", f"{API_KEYS_ACCOUNT}.{service}"):
            try:
                cred = win32cred.CredRead(Type=win32cred.CRED_TYPE_GENERIC, TargetName=target)
            except pywintypes.error as e:
                assert e.winerror == 1168, e
                continue
            found.append({"target": target, "user": cred["UserName"]})
        return found
    import secretstorage

    connection = secretstorage.dbus_init()
    try:
        return [
            i.get_attributes() for i in secretstorage.search_items(connection, {"service": service})
        ]
    finally:
        connection.close()


def remove(service: str) -> None:
    """Delete whatever :func:`platform_items` finds."""
    if sys.platform == "win32":
        from win32ctypes.pywin32 import win32cred

        for item in platform_items(service):
            win32cred.CredDelete(item["target"], win32cred.CRED_TYPE_GENERIC)
        return
    import secretstorage

    connection = secretstorage.dbus_init()
    try:
        for item in list(secretstorage.search_items(connection, {"service": service})):
            item.delete()
    finally:
        connection.close()


@pytest.fixture
def service() -> Iterator[str]:
    """A service name no other run uses, removed afterwards."""
    name = f"org.bmlib.interop.{uuid.uuid4().hex}"
    yield name
    remove(name)


def assert_one_item(service: str) -> None:
    """Exactly one item, named as both languages name it."""
    items = platform_items(service)
    if sys.platform == "win32":
        assert items == [{"target": service, "user": API_KEYS_ACCOUNT}]
    else:
        assert [{k: v for k, v in i.items() if k in ("service", "username")} for i in items] == [
            {"service": service, "username": API_KEYS_ACCOUNT}
        ]


def test_rust_writes_first(service: str) -> None:
    rust_write(service, '{"a":"1"}')
    assert_one_item(service)  # Rust names the account itself (Windows' `name_the_account`)
    store = KeyBundle.keyring(service)
    assert store.get("a") == "1"
    store.set("b", "2")
    assert rust_read(service) == '{"a":"1","b":"2"}'
    store.delete("a")
    assert rust_read(service) == '{"b":"2"}'
    rust_write(service, '{"b":"2","c":"3"}')
    assert KeyBundle.keyring(service).get("c") == "3"
    assert_one_item(service)


def test_python_writes_first(service: str) -> None:
    assert rust_read(service) is None
    KeyBundle.keyring(service).set("a", KEY)
    assert rust_read(service) == f'{{"a":"{KEY}"}}'
    rust_write(service, f'{{"a":"{KEY}","b":"{KEY}2"}}')
    store = KeyBundle.keyring(service)
    assert store.get("b") == f"{KEY}2"
    store.set("c", "3")
    assert rust_read(service) == f'{{"a":"{KEY}","b":"{KEY}2","c":"3"}}'
    assert_one_item(service)


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows credential's UserName")
def test_a_credential_of_another_account_is_refused_and_kept(service: str) -> None:
    from win32ctypes.pywin32 import win32cred

    theirs = {
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": service,
        "UserName": "db",
        "CredentialBlob": "secret",
        "Persist": win32cred.CRED_PERSIST_ENTERPRISE,
    }
    win32cred.CredWrite(theirs, 0)
    assert_both_refuse(
        service,
        SecretErrorKind.STORE,
        f"the keychain item '{service}' belongs to account 'db', not '{API_KEYS_ACCOUNT}'",
    )
    cred = win32cred.CredRead(Type=win32cred.CRED_TYPE_GENERIC, TargetName=service)
    assert (cred["UserName"], bytes(cred["CredentialBlob"]).decode("utf-16-le")) == ("db", "secret")


@pytest.mark.skipif(sys.platform == "win32", reason="Secret Service items")
def test_two_items_are_ambiguous_in_both_languages(service: str) -> None:
    import secretstorage

    rust_write(service, '{"a":"1"}')
    connection = secretstorage.dbus_init()
    try:
        secretstorage.get_default_collection(connection).create_item(
            "a second one",
            {"service": service, "username": API_KEYS_ACCOUNT, "application": "other"},
            b'{"b":"2"}',
        )
    finally:
        connection.close()
    assert_both_refuse(
        service,
        SecretErrorKind.AMBIGUOUS_ITEM,
        f"2 keychain items match service {service!r} and account {API_KEYS_ACCOUNT!r}",
        "Entry is matched by 2 credentials",
    )
