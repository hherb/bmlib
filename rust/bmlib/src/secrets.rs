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
//! `{provider: key}` ([`read_key_list`], [`write_key_list`]), read once and
//! then cached ([`KeyBundle`]): macOS asks the user on every item read, so
//! one item means one prompt, not one per provider. The item is named by a
//! service (the app's bundle id) and an account, [`API_KEYS_ACCOUNT`].
//!
//! A [`SecretError`] never holds a key: its text is the platform's message
//! or bmlib's own, and an unreadable item is reported without quoting it.
//!
//! The OS keychain itself is [`KeyringVault`], behind the `keyring` feature
//! (macOS Keychain, Windows Credential Manager, Linux Secret Service, through
//! the `keyring` crate's v1 stores). Everything else is always compiled, so a
//! build without it (the mobile apps) can bring its own [`Vault`].
//!
//! On macOS and Linux, Rust and Python address the same item. On Windows
//! they do not yet: the `keyring` crate names the credential
//! `account.service` and Python's `keyring` names it `service` (#686).

use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::sync::{Mutex, MutexGuard};

/// The account (keychain item name) holding every provider's key.
pub const API_KEYS_ACCOUNT: &str = "api-keys";

/// What [`read_key_list`] says of an item that is not a key list. It never
/// quotes the item, which may hold keys.
const UNREADABLE: &str = "the keychain item is not a valid key list";

/// A secret store failed. The text is the platform's message or bmlib's own,
/// and never holds a key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecretError(pub String);

impl fmt::Display for SecretError {
    /// `secret store: <message>`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "secret store: {}", self.0)
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
/// key past its end.
#[derive(Debug, Default)]
pub struct MemorySecrets(Mutex<HashMap<String, String>>);

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
    serde_json::from_str(text).map_err(|_| SecretError(UNREADABLE.into()))
}

/// The item's text for `keys`: compact JSON, providers sorted, non-ASCII
/// written as it is, as Python's `write_key_list` writes it.
pub fn write_key_list(keys: &KeyList) -> String {
    // serde_json fails only on a non-string map key or a failing writer; neither occurs here.
    serde_json::to_string(keys).expect("a map of strings always encodes")
}

/// A [`SecretStore`] over one [`Vault`] item, read once and then cached for
/// the store's life. A failed read is not cached, so the next call reads again.
pub struct KeyBundle<V: Vault> {
    vault: V,
    /// `None` until the first successful read.
    cache: Mutex<Option<KeyList>>,
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

    /// The cache, loaded from the vault if this is the first use, and the keys
    /// in it. A poisoned lock only means another thread panicked; the map is
    /// still valid. An item that is not a key list is an error, unless
    /// `replace_unreadable` (a key is about to be saved): then, logged, the
    /// keys start empty and the cache stays unloaded until they are written.
    fn loaded(
        &self,
        replace_unreadable: bool,
    ) -> Result<(MutexGuard<'_, Option<KeyList>>, KeyList), SecretError> {
        let mut guard = self.cache.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(keys) = guard.as_ref() {
            let keys = keys.clone();
            return Ok((guard, keys));
        }
        let keys = match self.vault.read()? {
            None => KeyList::new(),
            Some(text) => match read_key_list(&text) {
                Ok(keys) => keys,
                Err(_) if replace_unreadable => {
                    eprintln!(
                        "bmlib.secrets WARNING: {UNREADABLE}; replacing it with the key being saved"
                    );
                    return Ok((guard, KeyList::new()));
                }
                Err(e) => return Err(e),
            },
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
        Ok(self.loaded(false)?.1.get(provider).cloned())
    }

    /// Rewrites the item with the key added, then the cache. An item that is
    /// not a key list is replaced by one holding just this key, so saving a
    /// key is the way out of a damaged item.
    fn set(&self, provider: &str, key: &str) -> Result<(), SecretError> {
        let (mut guard, mut keys) = self.loaded(true)?;
        keys.insert(provider.to_string(), key.to_string());
        self.store(&mut guard, keys)
    }

    /// Rewrites the item without the key, then the cache. An absent key
    /// writes nothing.
    fn delete(&self, provider: &str) -> Result<(), SecretError> {
        let (mut guard, mut keys) = self.loaded(false)?;
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

    /// The keychain entry for the item.
    fn entry(&self) -> Result<keyring::v1::Entry, SecretError> {
        keyring::v1::Entry::new(&self.service, &self.account).map_err(keyring_error)
    }
}

/// A keyring failure as a [`SecretError`]: the platform's message, which holds no key.
#[cfg(feature = "keyring")]
fn keyring_error(e: keyring::v1::Error) -> SecretError {
    SecretError(e.to_string())
}

#[cfg(feature = "keyring")]
impl Vault for KeyringVault {
    /// Reads the item (an absent one is `None`).
    fn read(&self) -> Result<Option<String>, SecretError> {
        match self.entry()?.get_password() {
            Ok(text) => Ok(Some(text)),
            Err(keyring::v1::Error::NoEntry) => Ok(None),
            Err(e) => Err(keyring_error(e)),
        }
    }
    /// Writes the item.
    fn write(&self, text: &str) -> Result<(), SecretError> {
        self.entry()?.set_password(text).map_err(keyring_error)
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
            Err(SecretError("denied".into()))
        }
    }

    /// An unreadable item's error names the item and quotes none of it.
    #[test]
    fn an_unreadable_item_is_not_quoted() {
        let e = read_key_list("not json sk-leaky").unwrap_err();
        assert_eq!(e.0, UNREADABLE);
        assert!(!e.to_string().contains("sk-leaky"));
    }

    /// A vault's own error reaches the caller as it is.
    #[test]
    fn a_vault_error_passes_through() {
        let s = KeyBundle::new(Locked);
        assert_eq!(s.set("openai", "k1"), Err(SecretError("denied".into())));
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

    /// The display form says where the error came from.
    #[test]
    fn display_names_the_store() {
        assert_eq!(SecretError("x".into()).to_string(), "secret store: x");
    }
}
