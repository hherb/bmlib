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

"""API keys in the OS keychain (#634), the same layout as Rust's ``bmlib::secrets``.

Every provider's key lives in **one** keychain item, a JSON object
``{provider: key}`` (:func:`read_key_list`, :func:`write_key_list`), read
once and then cached (:class:`KeyBundle`): macOS asks the user on every item
read, so one item means one prompt, not one per provider. The item is named
by a service (the app's bundle id) and an account, :data:`API_KEYS_ACCOUNT`.

A :class:`SecretError` never holds a key: its text is the platform's message
or bmlib's own, and an unreadable item is reported without quoting it.

The OS keychain itself is :class:`KeyringVault`, over the ``keyring`` package
(extra ``secrets``), imported only when it is used. On macOS and Linux it
addresses the same item as Rust's ``KeyringVault``; on Windows it does not
yet (Rust's ``keyring`` crate names the credential ``account.service``,
this package ``service``; #686).

The shared contract is ``tests/fixtures/secret_key_list.json``.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Protocol

logger = logging.getLogger(__name__)

API_KEYS_ACCOUNT = "api-keys"
"""The account (keychain item name) holding every provider's key."""

_UNREADABLE = "the keychain item is not a valid key list"
"""What :func:`read_key_list` says of an item that is not a key list. It
never quotes the item, which may hold keys."""


_NO_KEYRING = "the keyring package is not installed (extra `secrets`)"
"""The error when :class:`KeyringVault` is used without its extra."""


class SecretError(Exception):
    """A secret store failed. The text is the platform's message or bmlib's
    own, and never holds a key."""


class SecretStore(Protocol):
    """API keys by provider name."""

    def get(self, provider: str) -> str | None:
        """The provider's key, or ``None`` when none is stored."""
        ...

    def set(self, provider: str, key: str) -> None:
        """Store the provider's key, replacing any earlier one."""
        ...

    def delete(self, provider: str) -> None:
        """Remove the provider's key; an absent one is fine."""
        ...


class MemorySecrets:
    """An in-memory :class:`SecretStore`, for tests and for a session that
    keeps no key past its end."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, provider: str) -> str | None:
        """The provider's key from the map."""
        with self._lock:
            return self._keys.get(provider)

    def set(self, provider: str, key: str) -> None:
        """Put the key in the map."""
        with self._lock:
            self._keys[provider] = key

    def delete(self, provider: str) -> None:
        """Take the key out of the map."""
        with self._lock:
            self._keys.pop(provider, None)


class Vault(Protocol):
    """The one item, as a plain read and write: the only part that touches the OS."""

    def read(self) -> str | None:
        """The item's text, or ``None`` when it does not exist yet. Raises
        :class:`SecretError`."""
        ...

    def write(self, text: str) -> None:
        """Replace the item's text, creating the item if need be. Raises
        :class:`SecretError`."""
        ...


def read_key_list(text: str) -> dict[str, str]:
    """Read an item's text as a key list: a JSON object whose every value is
    a string. Anything else (and a lone surrogate, a byte-order mark) raises
    a :class:`SecretError` that does not quote the text. A provider named
    twice keeps its later key."""
    try:
        keys = json.loads(text)
    except (ValueError, RecursionError):
        # The parse error is dropped: it could quote the item's text.
        raise SecretError(_UNREADABLE) from None
    if not isinstance(keys, dict) or not all(isinstance(v, str) for v in keys.values()):
        raise SecretError(_UNREADABLE)
    try:
        # A lone surrogate decodes in Python but not in Rust, and no keychain can store it.
        for k, v in keys.items():
            k.encode("utf-8")
            v.encode("utf-8")
    except UnicodeEncodeError:
        raise SecretError(_UNREADABLE) from None
    return keys


def write_key_list(keys: dict[str, str]) -> str:
    """The item's text for ``keys``: compact JSON, providers sorted,
    non-ASCII written as it is, as Rust's ``write_key_list`` writes it."""
    return json.dumps(keys, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class KeyBundle:
    """A :class:`SecretStore` over one :class:`Vault` item, read once and then
    cached for the store's life. A failed read is not cached, so the next
    call reads again."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._cache: dict[str, str] | None = None
        self._lock = threading.Lock()

    @classmethod
    def keyring(cls, service: str) -> KeyBundle:
        """The API keys of ``service`` in the OS keychain, item :data:`API_KEYS_ACCOUNT`."""
        return cls(KeyringVault(service, API_KEYS_ACCOUNT))

    def _loaded(self, replace_unreadable: bool) -> dict[str, str]:
        """A copy of the keys, loaded from the vault into the cache if this is
        the first use. Call with the lock held. An item that is not a key list
        raises, unless ``replace_unreadable`` (a key is about to be saved):
        then, logged, the keys start empty and the cache stays unloaded until
        they are written."""
        if self._cache is not None:
            return dict(self._cache)
        text = self.vault.read()
        if text is None:
            keys: dict[str, str] = {}
        else:
            try:
                keys = read_key_list(text)
            except SecretError:
                if not replace_unreadable:
                    raise
                logger.warning("%s; replacing it with the key being saved", _UNREADABLE)
                return {}
        self._cache = dict(keys)
        return keys

    def _store(self, keys: dict[str, str]) -> None:
        """Write ``keys`` to the vault, and only on success make them the
        cache. Call with the lock held."""
        self.vault.write(write_key_list(keys))
        self._cache = keys

    def get(self, provider: str) -> str | None:
        """The provider's key from the cache."""
        with self._lock:
            return self._loaded(False).get(provider)

    def set(self, provider: str, key: str) -> None:
        """Rewrite the item with the key added, then the cache. An item that
        is not a key list is replaced by one holding just this key, so saving
        a key is the way out of a damaged item."""
        with self._lock:
            keys = self._loaded(True)
            keys[provider] = key
            self._store(keys)

    def delete(self, provider: str) -> None:
        """Rewrite the item without the key, then the cache. An absent key
        writes nothing."""
        with self._lock:
            keys = self._loaded(False)
            if keys.pop(provider, None) is None:
                return
            self._store(keys)


class KeyringVault:
    """The OS keychain item named by ``service`` and ``account``, through the
    ``keyring`` package (extra ``secrets``)."""

    def __init__(self, service: str, account: str) -> None:
        self.service = service
        self.account = account

    def read(self) -> str | None:
        """Read the item (an absent one is ``None``)."""
        try:
            import keyring
        except ImportError:
            raise SecretError(_NO_KEYRING) from None
        try:
            return keyring.get_password(self.service, self.account)
        # A backend raises its platform's own errors, not only KeyringError.
        except Exception as e:
            raise SecretError(_describe(e)) from e

    def write(self, text: str) -> None:
        """Write the item."""
        try:
            import keyring
        except ImportError:
            raise SecretError(_NO_KEYRING) from None
        try:
            keyring.set_password(self.service, self.account, text)
        except Exception as e:
            raise SecretError(_describe(e)) from e


def _describe(e: Exception) -> str:
    """A keyring failure as text: its type and the platform's message, which
    holds no key."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
