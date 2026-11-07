// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! API keys in the OS keychain (#634, moved here from StudyAnalyzer), the
//! same layout as Python's `bmlib.secrets`.
//!
//! Every provider's key lives in **one** keychain item, a JSON object
//! `{provider: key}` ([`read_key_list`], [`write_key_list`]), read once for
//! every `get` and then cached ([`KeyBundle`]): macOS asks the user on every
//! item read, so one item means one prompt, not one per provider. A `set` or
//! `delete` reads the item again before rewriting it, so a key another writer
//! saved in the meantime is kept. The item is named by a
//! service (the app's bundle id) and an account, [`API_KEYS_ACCOUNT`].
//!
//! A [`SecretError`] never holds a key: its text is the platform's message
//! or bmlib's own, and an unreadable item is reported without quoting it.
//!
//! The OS keychain itself is `KeyringVault`, behind the `keyring` feature
//! (macOS Keychain, Windows Credential Manager, Linux Secret Service, through
//! the `keyring` crate's v1 stores). Everything else is always compiled, so a
//! build without it (the mobile apps) can bring its own [`Vault`].
//!
//! On macOS, Rust and Python address the same item. On Linux only while
//! Python writes first: Python's `keyring` adds an `application` attribute,
//! so its write beside an item Rust created makes a second one, and Rust then
//! finds two (`Ambiguous`). On Windows not yet: the `keyring` crate names the
//! credential `account.service`, Python's `keyring` `service`. Both are #686.

use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::sync::{Mutex, MutexGuard};

/// The account (keychain item name) holding every provider's key.
pub const API_KEYS_ACCOUNT: &str = "api-keys";

/// What [`read_key_list`] says of an item that is not a key list. It never
/// quotes the item, which may hold keys.
const UNREADABLE: &str = "the keychain item is not a valid key list";

/// What kind of failure a [`SecretError`] is, so an app can say what will
/// help (#689). The same three as Python's `SecretErrorKind`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum SecretErrorKind {
    /// The item is there but is not a key list (not JSON of strings, or not
    /// even text): saving a key replaces it with one holding just that key.
    UnreadableItem,
    /// More than one keychain item matches (Linux, see #686). Saving a key
    /// does not help; the user has to remove one in the keychain.
    AmbiguousItem,
    /// The store failed or refused: locked, access denied, no keychain at all.
    Store,
}

impl SecretErrorKind {
    /// The kind's name in the shared fixture and in Python: `unreadable-item`,
    /// `ambiguous-item`, `store`.
    pub fn as_str(self) -> &'static str {
        match self {
            SecretErrorKind::UnreadableItem => "unreadable-item",
            SecretErrorKind::AmbiguousItem => "ambiguous-item",
            SecretErrorKind::Store => "store",
        }
    }
}

/// A secret store failed. The text is the platform's message or bmlib's own,
/// and never holds a key; [`kind`](Self::kind) says which failure it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecretError {
    kind: SecretErrorKind,
    message: String,
}

impl SecretError {
    /// A [`SecretErrorKind::Store`] error saying `message`, which must not hold a key.
    pub fn new(message: impl Into<String>) -> Self {
        SecretError::with_kind(SecretErrorKind::Store, message)
    }

    /// An error of `kind` saying `message`, which must not hold a key (a
    /// [`Vault`] of an app's own reports an ambiguous item this way).
    pub fn with_kind(kind: SecretErrorKind, message: impl Into<String>) -> Self {
        SecretError {
            kind,
            message: message.into(),
        }
    }

    /// Which failure this is.
    pub fn kind(&self) -> SecretErrorKind {
        self.kind
    }

    /// The message, without the `secret store:` prefix `Display` adds.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for SecretError {
    /// `secret store: <message>`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "secret store: {}", self.message)
    }
}

impl std::error::Error for SecretError {}

/// API keys by provider name.
pub trait SecretStore: Send + Sync {
    /// The provider's key, or `None` when none is stored.
    fn get(&self, provider: &str) -> Result<Option<String>, SecretError>;
    /// Stores the provider's key, replacing any earlier one.
    fn set(&self, provider: &str, key: &str) -> Result<(), SecretError>;
    /// Removes the provider's key; an absent one is fine.
    fn delete(&self, provider: &str) -> Result<(), SecretError>;
}

/// An in-memory [`SecretStore`], for tests and for a session that keeps no
/// key past its end. Its `Debug` shows how many keys it holds, never a key.
#[derive(Default)]
pub struct MemorySecrets(Mutex<HashMap<String, String>>);

impl fmt::Debug for MemorySecrets {
    /// `MemorySecrets { keys: <count> }`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MemorySecrets")
            .field("keys", &self.keys().len())
            .finish()
    }
}

impl MemorySecrets {
    /// The map, even if another thread panicked while holding it (a map of
    /// strings cannot be left half-written).
    fn keys(&self) -> MutexGuard<'_, HashMap<String, String>> {
        self.0.lock().unwrap_or_else(|p| p.into_inner())
    }
}

impl SecretStore for MemorySecrets {
    /// The provider's key from the map.
    fn get(&self, provider: &str) -> Result<Option<String>, SecretError> {
        Ok(self.keys().get(provider).cloned())
    }
    /// Puts the key in the map.
    fn set(&self, provider: &str, key: &str) -> Result<(), SecretError> {
        self.keys().insert(provider.to_string(), key.to_string());
        Ok(())
    }
    /// Takes the key out of the map.
    fn delete(&self, provider: &str) -> Result<(), SecretError> {
        self.keys().remove(provider);
        Ok(())
    }
}

/// The one item, as a plain read and write: the only part that touches the OS.
pub trait Vault: Send + Sync {
    /// The item's text, or `None` when it does not exist yet.
    fn read(&self) -> Result<Option<String>, SecretError>;
    /// Replaces the item's text, creating the item if need be.
    fn write(&self, text: &str) -> Result<(), SecretError>;
}

/// The keys in the item, `{provider: key}`, in provider order.
pub type KeyList = BTreeMap<String, String>;

/// Reads an item's text as a key list: a JSON object whose every value is a
/// string. Anything else (and a lone surrogate, a byte-order mark) is a
/// [`SecretError`] that does not quote the text. A provider named twice
/// keeps its later key.
pub fn read_key_list(text: &str) -> Result<KeyList, SecretError> {
    // The parse error is dropped: it could quote the item's text.
    serde_json::from_str(text)
        .map_err(|_| SecretError::with_kind(SecretErrorKind::UnreadableItem, UNREADABLE))
}

/// The item's text for `keys`: compact JSON, providers sorted, non-ASCII
/// written as it is, as Python's `write_key_list` writes it.
pub fn write_key_list(keys: &KeyList) -> String {
    // serde_json fails only on a non-string map key or a failing writer; neither occurs here.
    serde_json::to_string(keys).expect("a map of strings always encodes")
}

/// A [`SecretStore`] over one [`Vault`] item. A `get` reads it once and then
/// answers from the cache; a `set` or `delete` reads it again before
/// rewriting it, so another writer's key is not lost. A failed read is not
/// cached, so the next call reads again. Its `Debug` shows whether the item
/// is loaded, never a key.
pub struct KeyBundle<V: Vault> {
    vault: V,
    /// `None` until the first successful read.
    cache: Mutex<Option<KeyList>>,
}

impl<V: Vault> fmt::Debug for KeyBundle<V> {
    /// `KeyBundle { loaded: <bool> }`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let loaded = self.lock().is_some();
        f.debug_struct("KeyBundle")
            .field("loaded", &loaded)
            .finish()
    }
}

impl<V: Vault> KeyBundle<V> {
    /// A store over `vault`, nothing read yet.
    pub fn new(vault: V) -> Self {
        KeyBundle {
            vault,
            cache: Mutex::new(None),
        }
    }

    /// The vault this store reads and writes.
    pub fn vault(&self) -> &V {
        &self.vault
    }

    /// The cache. A poisoned lock only means another thread panicked; the
    /// cache is only ever replaced whole, so it is still valid.
    fn lock(&self) -> MutexGuard<'_, Option<KeyList>> {
        self.cache.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// The cache, and the keys in it: read from the vault into the cache if
    /// nothing is cached yet or `fresh` (a write is about to follow, and must
    /// start from the item as it is now). An item that is not a key list (or
    /// that the vault reports as unreadable) is an error, unless `replace_unreadable` (a key is about to be saved): then,
    /// logged, the keys start empty and the cache is left as it was until they
    /// are written.
    fn loaded(
        &self,
        fresh: bool,
        replace_unreadable: bool,
    ) -> Result<(MutexGuard<'_, Option<KeyList>>, KeyList), SecretError> {
        let mut guard = self.lock();
        if let (false, Some(keys)) = (fresh, guard.as_ref()) {
            let keys = keys.clone();
            return Ok((guard, keys));
        }
        // The vault may report an unreadable item itself; a set replaces it either way.
        let read = self.vault.read().and_then(|text| match text {
            None => Ok(KeyList::new()),
            Some(text) => read_key_list(&text),
        });
        let keys = match read {
            Ok(keys) => keys,
            Err(e) if replace_unreadable && e.kind() == SecretErrorKind::UnreadableItem => {
                log::warn!(
                    target: "bmlib.secrets",
                    "{}; replacing it with the key being saved",
                    e.message()
                );
                return Ok((guard, KeyList::new()));
            }
            Err(e) => return Err(e),
        };
        *guard = Some(keys.clone());
        Ok((guard, keys))
    }

    /// Writes `keys` to the vault, and only on success makes them the cache.
    fn store(
        &self,
        guard: &mut MutexGuard<'_, Option<KeyList>>,
        keys: KeyList,
    ) -> Result<(), SecretError> {
        self.vault.write(&write_key_list(&keys))?;
        **guard = Some(keys);
        Ok(())
    }
}

impl<V: Vault> SecretStore for KeyBundle<V> {
    /// The provider's key from the cache.
    fn get(&self, provider: &str) -> Result<Option<String>, SecretError> {
        Ok(self.loaded(false, false)?.1.get(provider).cloned())
    }

    /// Reads the item again, rewrites it with the key added, then the cache.
    /// An item that is not a key list is replaced by one holding just this
    /// key, so saving a key is the way out of a damaged item.
    fn set(&self, provider: &str, key: &str) -> Result<(), SecretError> {
        let (mut guard, mut keys) = self.loaded(true, true)?;
        keys.insert(provider.to_string(), key.to_string());
        self.store(&mut guard, keys)
    }

    /// Reads the item again, rewrites it without the key, then the cache. An
    /// absent key writes nothing.
    fn delete(&self, provider: &str) -> Result<(), SecretError> {
        let (mut guard, mut keys) = self.loaded(true, false)?;
        if keys.remove(provider).is_none() {
            return Ok(());
        }
        self.store(&mut guard, keys)
    }
}

/// The OS keychain item named by `service` and `account`.
#[cfg(feature = "keyring")]
#[derive(Debug, Clone)]
pub struct KeyringVault {
    service: String,
    account: String,
}

#[cfg(feature = "keyring")]
impl KeyringVault {
    /// The item `account` of `service` (an app's bundle id, say).
    pub fn new(service: &str, account: &str) -> Self {
        KeyringVault {
            service: service.to_string(),
            account: account.to_string(),
        }
    }

    /// The keychain entry for the item. When the platform store could not be
    /// set up, the error says why, not only that there is no store.
    fn entry(&self) -> Result<keyring::v1::Entry, SecretError> {
        keyring::v1::Entry::new(&self.service, &self.account).map_err(|e| {
            match (&e, keyring::v1::Entry::store_status()) {
                (keyring::v1::Error::NoDefaultStore, Err(why)) => {
                    SecretError::new(format!("{e}: {why}"))
                }
                _ => keyring_error(e),
            }
        })
    }
}

/// A keyring failure as a [`SecretError`]: the platform's message, which holds
/// no key (keyring-core's `Display` prints no item bytes). Two matching items
/// are [`SecretErrorKind::AmbiguousItem`], on a read or a write; the rest
/// [`SecretErrorKind::Store`].
#[cfg(feature = "keyring")]
fn keyring_error(e: keyring::v1::Error) -> SecretError {
    let kind = match e {
        keyring::v1::Error::Ambiguous(_) => SecretErrorKind::AmbiguousItem,
        _ => SecretErrorKind::Store,
    };
    SecretError::with_kind(kind, e.to_string())
}

/// What a read of the item came to: its text, `None` when there is no item,
/// and an item the store holds but cannot give back as text (not UTF-8, or
/// not in the store's own format) as text that is not a key list, so a
/// `KeyBundle` reports it as unreadable and a `set` replaces it.
#[cfg(feature = "keyring")]
fn read_result(r: keyring::v1::Result<String>) -> Result<Option<String>, SecretError> {
    match r {
        Ok(text) => Ok(Some(text)),
        Err(keyring::v1::Error::NoEntry) => Ok(None),
        Err(keyring::v1::Error::BadEncoding(_) | keyring::v1::Error::BadDataFormat(..)) => {
            Ok(Some(String::new()))
        }
        Err(e) => Err(keyring_error(e)),
    }
}

/// What a write of the item came to (see [`keyring_error`]).
#[cfg(feature = "keyring")]
fn write_result(r: keyring::v1::Result<()>) -> Result<(), SecretError> {
    r.map_err(keyring_error)
}

#[cfg(feature = "keyring")]
impl Vault for KeyringVault {
    /// Reads the item (an absent one is `None`; one that is not text reads as
    /// unreadable, see `read_result`).
    fn read(&self) -> Result<Option<String>, SecretError> {
        read_result(self.entry()?.get_password())
    }
    /// Writes the item.
    fn write(&self, text: &str) -> Result<(), SecretError> {
        write_result(self.entry()?.set_password(text))
    }
}

#[cfg(feature = "keyring")]
impl KeyBundle<KeyringVault> {
    /// The API keys of `service` in the OS keychain, item [`API_KEYS_ACCOUNT`].
    pub fn keyring(service: &str) -> Self {
        KeyBundle::new(KeyringVault::new(service, API_KEYS_ACCOUNT))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An in-memory vault that refuses writes.
    struct Locked;

    impl Vault for Locked {
        fn read(&self) -> Result<Option<String>, SecretError> {
            Ok(None)
        }
        fn write(&self, _: &str) -> Result<(), SecretError> {
            Err(SecretError::new("denied"))
        }
    }

    /// An unreadable item's error names the item and quotes none of it.
    #[test]
    fn an_unreadable_item_is_not_quoted() {
        let e = read_key_list("not json sk-leaky").unwrap_err();
        assert_eq!(e.message(), UNREADABLE);
        assert_eq!(e.kind(), SecretErrorKind::UnreadableItem);
        assert!(!e.to_string().contains("sk-leaky"));
    }

    /// Nesting past any parser's depth limit is unreadable (Python's twin
    /// `test_nesting_past_the_recursion_limit_is_unreadable`).
    #[test]
    fn deep_nesting_is_unreadable() {
        let deep = format!(
            "{{\"openai\":{}{}}}",
            "[".repeat(100_000),
            "]".repeat(100_000)
        );
        assert!(read_key_list(&deep).is_err());
    }

    /// A vault's own error reaches the caller as it is.
    #[test]
    fn a_vault_error_passes_through() {
        let s = KeyBundle::new(Locked);
        assert_eq!(s.set("openai", "k1"), Err(SecretError::new("denied")));
        assert_eq!(s.get("openai"), Ok(None));
    }

    /// The in-memory store keeps, replaces and forgets keys.
    #[test]
    fn memory_secrets_set_get_delete() {
        let s = MemorySecrets::default();
        s.set("openai", "k1").unwrap();
        s.set("openai", "k2").unwrap();
        assert_eq!(s.get("openai").unwrap().as_deref(), Some("k2"));
        s.delete("openai").unwrap();
        s.delete("openai").unwrap();
        assert_eq!(s.get("openai").unwrap(), None);
    }

    /// `Debug` counts the keys and shows none.
    #[test]
    fn memory_secrets_debug_hides_keys() {
        let s = MemorySecrets::default();
        s.set("openai", "sk-leaky").unwrap();
        assert_eq!(format!("{s:?}"), "MemorySecrets { keys: 1 }");
    }

    /// The display form says where the error came from.
    #[test]
    fn display_names_the_store() {
        assert_eq!(SecretError::new("x").to_string(), "secret store: x");
    }

    /// `Debug` says whether the item is loaded and shows no key.
    #[test]
    fn key_bundle_debug_hides_keys() {
        struct One;
        impl Vault for One {
            fn read(&self) -> Result<Option<String>, SecretError> {
                Ok(Some("{\"openai\":\"sk-leaky\"}".into()))
            }
            fn write(&self, _: &str) -> Result<(), SecretError> {
                Ok(())
            }
        }
        let s = KeyBundle::new(One);
        assert_eq!(format!("{s:?}"), "KeyBundle { loaded: false }");
        s.get("openai").unwrap();
        assert_eq!(format!("{s:?}"), "KeyBundle { loaded: true }");
    }

    /// A keychain read: no item is `None`, an item that is not text reads as
    /// unreadable (so `set` can replace it), any other failure is an error.
    #[cfg(feature = "keyring")]
    #[test]
    fn a_keychain_read_maps_each_outcome() {
        use keyring::v1::Error as E;
        assert_eq!(read_result(Ok("{}".into())), Ok(Some("{}".into())));
        assert_eq!(read_result(Err(E::NoEntry)), Ok(None));
        let not_text = read_result(Err(E::BadEncoding(b"\xff sk-leaky".to_vec()))).unwrap();
        assert_eq!(
            not_text.as_deref().map(read_key_list),
            Some(Err(SecretError::with_kind(
                SecretErrorKind::UnreadableItem,
                UNREADABLE
            )))
        );
        let other = read_result(Err(E::Invalid("service".into(), "empty".into()))).unwrap_err();
        assert!(!other.message().is_empty());
        assert_eq!(other.kind(), SecretErrorKind::Store);
        let two = read_result(Err(E::Ambiguous(vec![]))).unwrap_err();
        assert_eq!(two.kind(), SecretErrorKind::AmbiguousItem);
    }

    /// A keychain write naming two items is ambiguous too; the rest are store failures.
    #[cfg(feature = "keyring")]
    #[test]
    fn a_keychain_error_keeps_its_kind() {
        use keyring::v1::Error as E;
        assert_eq!(
            keyring_error(E::Ambiguous(vec![])).kind(),
            SecretErrorKind::AmbiguousItem
        );
        assert_eq!(
            keyring_error(E::NoStorageAccess("locked".into())).kind(),
            SecretErrorKind::Store
        );
        assert_eq!(
            write_result(Err(E::Ambiguous(vec![]))).unwrap_err().kind(),
            SecretErrorKind::AmbiguousItem
        );
    }

    /// Every kind by name, matched without a wildcard so a kind added later
    /// fails to compile here until it is named (the contract test holds the
    /// same names to the fixture's `kinds`).
    #[test]
    fn every_kind_is_listed() {
        use SecretErrorKind::{AmbiguousItem, Store, UnreadableItem};
        let kinds = [UnreadableItem, AmbiguousItem, Store];
        for k in kinds {
            match k {
                UnreadableItem | AmbiguousItem | Store => {}
            }
        }
        assert_eq!(
            kinds.map(SecretErrorKind::as_str),
            ["unreadable-item", "ambiguous-item", "store"]
        );
    }
}
