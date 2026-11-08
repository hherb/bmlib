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

The OS keychain itself is :class:`KeyringVault` (extra ``secrets``), which
addresses the same item as Rust's ``KeyringVault`` on macOS, Windows, Linux
and the BSDs (#686): through the ``keyring`` package on macOS, and elsewhere
through the platform library ``keyring`` itself uses, with Rust's naming.

The shared contract is ``tests/fixtures/secret_key_list.json``.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Callable
from contextlib import closing
from enum import StrEnum
from types import ModuleType
from typing import Any, Protocol, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_PLATFORM = sys.platform
"""Which keychain :class:`KeyringVault` addresses; a constant so a test can choose."""

_SECRET_SERVICE_PLATFORMS = ("linux", "freebsd", "openbsd", "netbsd", "dragonfly")
"""The ``sys.platform`` prefixes whose keychain is the Secret Service, the
non-Apple Unixes on which Rust's ``keyring`` uses it."""

API_KEYS_ACCOUNT = "api-keys"
"""The account (keychain item name) holding every provider's key."""

_UNREADABLE = "the keychain item is not a valid key list"
"""What :func:`read_key_list` says of an item that is not a key list. It
never quotes the item, which may hold keys."""


_NO_KEYRING = "a package KeyringVault needs could not be imported (extra `secrets`)"
"""The start of the error when :class:`KeyringVault` cannot import ``keyring``,
``win32ctypes`` or ``secretstorage``; what the import raised follows it."""

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
    user has to remove one in the keychain. :class:`KeyringVault` reports
    it where the keychain is the Secret Service, as Rust's does (#686); a
    :class:`Vault` of an app's own may."""

    STORE = "store"
    """The store failed or refused: locked, access denied, no keychain at all,
    a Windows credential of the same name another account holds, or a
    provider or key no keychain can hold."""


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
    """The OS keychain item named by ``service`` and ``account``: the item
    Rust's ``KeyringVault`` reads and writes (#686). On macOS through the
    ``keyring`` package; on Windows the generic credential named by the
    service alone, with ``UserName`` the account, through ``win32cred``; on
    Linux and the BSDs the Secret Service item carrying exactly the
    attributes ``service`` and ``username``, through ``secretstorage``. Both
    are installed by the extra ``secrets``. On Windows a credential of that
    name whose ``UserName`` is another account is refused, on a read or a
    write, and left as it is; on the Secret Service two matching items are
    :attr:`SecretErrorKind.AMBIGUOUS_ITEM`, on a read or a write, as in Rust."""

    def __init__(self, service: str, account: str) -> None:
        self.service = service
        self.account = account

    def read(self) -> str | None:
        """Read the item (an absent one is ``None``). An item that is not
        text reads as text that is not a key list, so a :class:`KeyBundle`
        reports it as unreadable and a ``set`` replaces it, as Rust's does."""
        return _guarded(_platform_read, self.service, self.account)

    def write(self, text: str) -> None:
        """Write the item, creating it if need be."""
        _guarded(_platform_write, self.service, self.account, text)


def _guarded(op: Callable[..., T], *args: str) -> T:
    """``op(*args)``, with any failure but a :class:`SecretError` raised as one
    saying what was raised. Raised after the except clause, so the backend's
    exception (a UnicodeError holds the item's bytes) is not chained."""
    error: str | None = None
    try:
        return op(*args)
    except SecretError:
        raise
    # A backend raises its platform's own errors, not only KeyringError.
    except Exception as e:
        error = _describe(e)
    raise SecretError(error)


def _platform_read(service: str, account: str) -> str | None:
    """The item's text on this platform (see :class:`KeyringVault`)."""
    if _PLATFORM == "win32":
        return _wincred_read(service, account)
    if _PLATFORM.startswith(_SECRET_SERVICE_PLATFORMS):
        return _secret_service_read(service, account)
    try:
        return _import_keyring().get_password(service, account)
    except UnicodeDecodeError:
        return ""


def _platform_write(service: str, account: str, text: str) -> None:
    """Write the item's text on this platform (see :class:`KeyringVault`)."""
    if _PLATFORM == "win32":
        _wincred_write(service, account, text)
    elif _PLATFORM.startswith(_SECRET_SERVICE_PLATFORMS):
        _secret_service_write(service, account, text)
    else:
        _import_keyring().set_password(service, account, text)


_CRED_NOT_FOUND = 1168
"""Windows' ``ERROR_NOT_FOUND``, what ``CredRead`` raises for no credential."""


def _wincred_credential(service: str, account: str) -> dict[str, Any] | None:
    """The generic credential named ``service``, or ``None``. One whose
    ``UserName`` is another account raises: it is not this item, and a write
    would destroy it. ``""`` is this item, what Rust leaves when naming the
    account after its first write failed."""
    pywintypes, win32cred = _import_wincred()
    try:
        cred: dict[str, Any] = win32cred.CredRead(
            Type=win32cred.CRED_TYPE_GENERIC, TargetName=service
        )
    except pywintypes.error as e:
        if e.winerror == _CRED_NOT_FOUND:
            return None
        raise
    owner = cred.get("UserName") or ""
    if owner not in ("", account):
        raise SecretError(
            f"the keychain item {service!r} belongs to account {owner!r}, not {account!r}"
        )
    return cred


def _wincred_read(service: str, account: str) -> str | None:
    """The credential's blob decoded as UTF-16 (the encoding Python's
    ``keyring`` and Rust's crate both write); one that will not decode reads
    as ``""``."""
    cred = _wincred_credential(service, account)
    if cred is None:
        return None
    try:
        return bytes(cred["CredentialBlob"]).decode("utf-16-le")
    except UnicodeDecodeError:
        return ""


def _wincred_write(service: str, account: str, text: str) -> None:
    """Write the credential named ``service`` in place. Not through
    ``keyring.set_password``, which first copies the old value to
    ``account@service``, so a deleted key would stay in Credential Manager."""
    _wincred_credential(service, account)
    _, win32cred = _import_wincred()
    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": service,
            "UserName": account,
            "CredentialBlob": text,
            "Persist": win32cred.CRED_PERSIST_ENTERPRISE,
        },
        0,
    )


def _secret_service_items(connection: Any, service: str, account: str) -> list[Any]:
    """Every item, in any collection, matching ``service`` and ``account``
    (Rust's search). Two or more raise :attr:`SecretErrorKind.AMBIGUOUS_ITEM`."""
    secretstorage = _import_secretstorage()
    items = list(secretstorage.search_items(connection, {"service": service, "username": account}))
    if len(items) > 1:
        raise SecretError(
            f"{len(items)} keychain items match service {service!r} and account {account!r}",
            SecretErrorKind.AMBIGUOUS_ITEM,
        )
    return items


def _unlocked(thing: Any) -> None:
    """Unlock a Secret Service item or collection, asking the user if need be."""
    if thing.is_locked() and thing.unlock():
        raise SecretError("the keychain stayed locked: the unlock prompt was dismissed")


def _secret_service_read(service: str, account: str) -> str | None:
    """The matching item's secret as UTF-8 text; one that will not decode
    reads as ``""``."""
    secretstorage = _import_secretstorage()
    connection = secretstorage.dbus_init()
    with closing(connection):
        items = _secret_service_items(connection, service, account)
        if not items:
            return None
        _unlocked(items[0])
        secret = items[0].get_secret()
    try:
        return bytes(secret).decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _secret_service_write(service: str, account: str, text: str) -> None:
    """Replace the matching item's secret, or create the item in the default
    collection with just ``service`` and ``username``, as Rust creates it."""
    secretstorage = _import_secretstorage()
    connection = secretstorage.dbus_init()
    with closing(connection):
        items = _secret_service_items(connection, service, account)
        if items:
            _unlocked(items[0])
            items[0].set_secret(text.encode("utf-8"))
            return
        collection = secretstorage.get_default_collection(connection)
        _unlocked(collection)
        collection.create_item(
            f"keyring:{account}@{service}",
            {"service": service, "username": account},
            text.encode("utf-8"),
            replace=True,
        )


def _import_keyring() -> ModuleType:
    """The ``keyring`` package, or a :class:`SecretError` saying what its
    import raised (a present package can fail on its own dependency)."""
    try:
        import keyring
    except ImportError as e:
        raise SecretError(f"{_NO_KEYRING}: {_describe(e)}") from e
    return keyring


def _import_wincred() -> tuple[ModuleType, ModuleType]:
    """``pywintypes`` and ``win32cred`` from ``pywin32-ctypes`` (installed
    with ``keyring`` on Windows), or a :class:`SecretError`."""
    try:
        # Windows only; `unused-ignore` where it is installed.
        from win32ctypes.pywin32 import (  # type: ignore[import-not-found, unused-ignore]
            pywintypes,
            win32cred,
        )
    except ImportError as e:
        raise SecretError(f"{_NO_KEYRING}: {_describe(e)}") from e
    return pywintypes, win32cred


def _import_secretstorage() -> ModuleType:
    """``secretstorage`` (installed with ``keyring`` on Linux), or a
    :class:`SecretError`."""
    try:
        # Linux only; `unused-ignore` where it is installed.
        import secretstorage  # type: ignore[import-not-found, unused-ignore]
    except ImportError as e:
        raise SecretError(f"{_NO_KEYRING}: {_describe(e)}") from e
    return secretstorage


def _describe(e: BaseException) -> str:
    """A failure as text: its type and its message, which holds no key."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
