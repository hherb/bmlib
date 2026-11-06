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
once for every ``get`` and then cached (:class:`KeyBundle`): macOS asks the
user on every item read, so one item means one prompt, not one per provider.
A ``set`` or ``delete`` reads the item again before rewriting it, so a key
another writer saved in the meantime is kept. The item is named by a service
(the app's bundle id) and an account, :data:`API_KEYS_ACCOUNT`.

A :class:`SecretError` never holds a key: its text is the platform's message
or bmlib's own, an unreadable item is reported without quoting it, and it is
raised with no exception chained to it (one could hold the item's text). Its
:attr:`~SecretError.kind` says whether saving a key will help (#689).

The OS keychain itself is :class:`KeyringVault`, over the ``keyring`` package
(extra ``secrets``), imported only when it is used. On macOS it addresses
the same item as Rust's ``KeyringVault``. On Linux only while Python writes
first: this package adds an ``application`` attribute, so its write beside an
item Rust created makes a second one, which Rust then reads as ambiguous. On
Windows not yet (Rust's ``keyring`` crate names the credential
``account.service``, this package ``service``). Both are #686.

The shared contract is ``tests/fixtures/secret_key_list.json``.
"""

from __future__ import annotations

import json
import logging
import threading
from enum import StrEnum
from types import ModuleType
from typing import Protocol

logger = logging.getLogger(__name__)

API_KEYS_ACCOUNT = "api-keys"
"""The account (keychain item name) holding every provider's key."""

_UNREADABLE = "the keychain item is not a valid key list"
"""What :func:`read_key_list` says of an item that is not a key list. It
never quotes the item, which may hold keys."""


_NO_KEYRING = "the keyring package could not be imported (extra `secrets`)"
"""The start of the error when :class:`KeyringVault` cannot import ``keyring``;
what the import raised follows it."""

_NOT_TEXT = "a provider or key that is not valid text"
"""The error when :meth:`KeyBundle.set` is given a lone surrogate, which no
keychain can store and :func:`read_key_list` would refuse to read back."""


class SecretErrorKind(StrEnum):
    """What kind of failure a :class:`SecretError` is, so an app can say what
    will help (#689). The same three as Rust's ``SecretErrorKind``."""

    UNREADABLE_ITEM = "unreadable-item"
    """The item is there but is not a key list (not JSON of strings, or not
    even text): saving a key replaces it with one holding just that key."""

    AMBIGUOUS_ITEM = "ambiguous-item"
    """More than one keychain item matches. Saving a key does not help; the
    user has to remove one in the keychain. :class:`KeyringVault` never
    reports it (Python's ``keyring`` reads whichever item comes first, #686);
    a :class:`Vault` of an app's own may."""

    STORE = "store"
    """The store failed or refused: locked, access denied, no keychain at all,
    or a provider or key no keychain can hold."""


class SecretError(Exception):
    """A secret store failed. The text is the platform's message or bmlib's
    own, and never holds a key; :attr:`kind` says which failure it is."""

    def __init__(self, message: str, kind: SecretErrorKind = SecretErrorKind.STORE) -> None:
        super().__init__(message)
        # A kind's name becomes the member, so `kind is ...` holds; an unknown one raises.
        self.kind = SecretErrorKind(kind)


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
    # Raised after the except clauses, so no parse error (which can quote the
    # item's text) is chained to it, not even as __context__.
    try:
        keys = json.loads(text, object_pairs_hook=_string_pairs)
    except (ValueError, RecursionError):
        keys = None
    # A lone surrogate decodes in Python but not in Rust, and no keychain can store it.
    if not isinstance(keys, dict) or not all(_is_text(k) and _is_text(v) for k, v in keys.items()):
        raise SecretError(_UNREADABLE, SecretErrorKind.UNREADABLE_ITEM)
    return keys


def _is_text(s: str) -> bool:
    """Whether ``s`` encodes as UTF-8 (holds no lone surrogate)."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _string_pairs(pairs: list[tuple[str, object]]) -> dict[str, str]:
    """A JSON object as a key list, refusing any value that is not a string as
    it is read (a ``ValueError``), as serde does: checked after ``json.loads``,
    a non-string under a provider named twice would be hidden by the later
    value. Any nested object therefore refuses too."""
    keys: dict[str, str] = {}
    for k, v in pairs:
        if not isinstance(v, str):
            raise ValueError("not a key list")
        keys[k] = v
    return keys


def write_key_list(keys: dict[str, str]) -> str:
    """The item's text for ``keys``: compact JSON, providers sorted,
    non-ASCII written as it is, as Rust's ``write_key_list`` writes it."""
    return json.dumps(keys, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class KeyBundle:
    """A :class:`SecretStore` over one :class:`Vault` item. A ``get`` reads it
    once and then answers from the cache; a ``set`` or ``delete`` reads it
    again before rewriting it, so another writer's key is not lost. A failed
    read is not cached, so the next call reads again."""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._cache: dict[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def vault(self) -> Vault:
        """The vault this store reads and writes."""
        return self._vault

    @classmethod
    def keyring(cls, service: str) -> KeyBundle:
        """The API keys of ``service`` in the OS keychain, item :data:`API_KEYS_ACCOUNT`."""
        return cls(KeyringVault(service, API_KEYS_ACCOUNT))

    def _loaded(self, *, fresh: bool, replace_unreadable: bool) -> dict[str, str]:
        """A copy of the keys, read from the vault into the cache if nothing
        is cached yet or ``fresh`` (a write is about to follow, and must start
        from the item as it is now). Call with the lock held. An item that is
        not a key list (or that the vault reports as unreadable) raises, unless
        ``replace_unreadable`` (a key is about to be saved): then, logged, the
        keys start empty and the cache is left as
        it was until they are written."""
        if self._cache is not None and not fresh:
            return dict(self._cache)
        try:
            text = self._vault.read()
            keys = {} if text is None else read_key_list(text)
        except SecretError as e:
            # The vault may report an unreadable item itself; a set replaces it either way.
            if not (replace_unreadable and e.kind is SecretErrorKind.UNREADABLE_ITEM):
                raise
            logger.warning("%s; replacing it with the key being saved", e)
            return {}
        self._cache = dict(keys)
        return keys

    def _store(self, keys: dict[str, str]) -> None:
        """Write ``keys`` to the vault, and only on success make them the
        cache. Call with the lock held."""
        self._vault.write(write_key_list(keys))
        self._cache = keys

    def get(self, provider: str) -> str | None:
        """The provider's key from the cache."""
        with self._lock:
            return self._loaded(fresh=False, replace_unreadable=False).get(provider)

    def set(self, provider: str, key: str) -> None:
        """Read the item again, rewrite it with the key added, then the cache.
        An item that is not a key list is replaced by one holding just this
        key, so saving a key is the way out of a damaged item. A provider or
        key holding a lone surrogate raises and writes nothing."""
        if not (_is_text(provider) and _is_text(key)):
            raise SecretError(_NOT_TEXT)
        with self._lock:
            keys = self._loaded(fresh=True, replace_unreadable=True)
            keys[provider] = key
            self._store(keys)

    def delete(self, provider: str) -> None:
        """Read the item again, rewrite it without the key, then the cache. An
        absent key writes nothing."""
        with self._lock:
            keys = self._loaded(fresh=True, replace_unreadable=False)
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
        """Read the item (an absent one is ``None``). An item that is not
        UTF-8 reads as text that is not a key list, so a :class:`KeyBundle`
        reports it as unreadable and a ``set`` replaces it, as Rust's does."""
        keyring = _import_keyring()
        error: str | None = None
        # Each error is raised after its except clause, so the backend's
        # exception (a UnicodeError holds the item's bytes) is not chained.
        try:
            return keyring.get_password(self.service, self.account)
        except UnicodeDecodeError:
            return ""
        # A backend raises its platform's own errors, not only KeyringError.
        except Exception as e:
            error = _describe(e)
        raise SecretError(error)

    def write(self, text: str) -> None:
        """Write the item."""
        keyring = _import_keyring()
        error: str | None = None
        try:
            keyring.set_password(self.service, self.account, text)
            return
        except Exception as e:
            error = _describe(e)
        raise SecretError(error)


def _import_keyring() -> ModuleType:
    """The ``keyring`` package, or a :class:`SecretError` saying what its
    import raised (a present package can fail on its own dependency)."""
    try:
        import keyring
    except ImportError as e:
        raise SecretError(f"{_NO_KEYRING}: {_describe(e)}") from e
    return keyring


def _describe(e: BaseException) -> str:
    """A failure as text: its type and its message, which holds no key."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
