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
``keychain_vault`` example (``cargo build --example keychain_vault
--features keyring``): CI's Windows and Linux (gnome-keyring) jobs. It
writes and then removes items under a fresh service name.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest

from bmlib.secrets import API_KEYS_ACCOUNT, KeyBundle

BINARY = os.environ.get("BMLIB_KEYCHAIN_INTEROP_BIN")

pytestmark = pytest.mark.skipif(not BINARY, reason="BMLIB_KEYCHAIN_INTEROP_BIN is not set")


def rust_read(service: str) -> str | None:
    """The item's text as Rust's ``KeyringVault`` reads it."""
    done = subprocess.run([BINARY or "", service, "read"], capture_output=True, text=True)
    if done.returncode == 3:
        return None
    assert done.returncode == 0, done.stderr
    return done.stdout


def rust_write(service: str, text: str) -> None:
    """Write the item as Rust's ``KeyringVault`` writes it."""
    done = subprocess.run([BINARY or "", service, "write", text], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def platform_items(service: str) -> list[dict[str, str]]:
    """What the platform holds for ``service``, apart from bmlib: each Windows
    credential under either name ``keyring`` uses, or each Secret Service item
    carrying ``service``, as its attributes (plus ``target``/``user`` on Windows)."""
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
    KeyBundle.keyring(service).set("a", "1")
    assert rust_read(service) == '{"a":"1"}'
    rust_write(service, '{"a":"1","b":"2"}')
    store = KeyBundle.keyring(service)
    assert store.get("b") == "2"
    store.set("c", "3")
    assert rust_read(service) == '{"a":"1","b":"2","c":"3"}'
    assert_one_item(service)
