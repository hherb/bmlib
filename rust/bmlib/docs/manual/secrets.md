# secrets — API keys in the OS keychain

*(unreleased, #634)* An app's API keys, by provider name, in **one** OS
keychain item: a JSON object `{provider: key}`, read once for every `get` and
cached, because macOS asks the user on every item read; a `set` or `delete`
reads it again before rewriting it, so a key another process saved meanwhile
is kept. Python's `bmlib.secrets` uses the same layout
([secrets.md](../../../../docs/manual/secrets.md); on macOS the same item, see
*Platforms*), held together by `tests/fixtures/secret_key_list.json`.

The OS keychain is `KeyringVault`, behind the `keyring` feature (the
`keyring` crate's v1 stores: macOS Keychain, Windows Credential Manager,
Linux Secret Service). Everything else is always compiled, so a build without
it (the mobile apps) brings its own `Vault`:

```rust
use bmlib::secrets::{KeyBundle, SecretError, SecretStore, Vault};
use std::sync::Mutex;

/// A vault in memory, standing in for a platform's own secure store.
#[derive(Default)]
struct InMemory(Mutex<Option<String>>);

impl Vault for InMemory {
    fn read(&self) -> Result<Option<String>, SecretError> {
        Ok(self.0.lock().unwrap().clone())
    }
    fn write(&self, text: &str) -> Result<(), SecretError> {
        *self.0.lock().unwrap() = Some(text.to_string());
        Ok(())
    }
}

let keys = KeyBundle::new(InMemory::default());
keys.set("openai", "sk-1")?;
keys.set("anthropic", "sk-2")?;
assert_eq!(keys.get("openai")?.as_deref(), Some("sk-1"));
assert_eq!(
    keys.vault().0.lock().unwrap().as_deref(),
    Some(r#"{"anthropic":"sk-2","openai":"sk-1"}"#)
);
# Ok::<(), SecretError>(())
```

With the feature, `KeyBundle::keyring("com.example.myapp")` is the item
`API_KEYS_ACCOUNT` (`"api-keys"`) of that service.

| Item | What it is |
|---|---|
| `SecretStore` | `get(provider) -> Option<String>`, `set(provider, key)`, `delete(provider)` (absent is fine); `Send + Sync` |
| `KeyBundle<V: Vault>` | A `SecretStore` over one item: `get` reads it once and answers from the cache, `set` and `delete` read it again and rewrite it; a failed read is not cached. `set` over an item that is not a key list replaces it with one holding just the new key, dropping whatever it held (a WARN record under `bmlib.secrets`); `get` and `delete` report it (`UnreadableItem`). A failed write leaves the cache as it was. Its `Debug` shows `loaded`, never a key |
| `Vault` | `read() -> Option<String>`, `write(text)`: the one item, the only part that touches the OS |
| `KeyringVault::new(service, account)` | *(feature `keyring`)* The OS keychain item; a missing item reads as `None`, one that is not text (or not in the store's format) as unreadable, so `set` replaces it; a Windows credential of the item's name that another account holds is `Store`, read or written, and left as it is; two matching items (`Ambiguous`, on a read or a write) are `AmbiguousItem`, which `set` does not replace. A store that could not be set up says why |
| `MemorySecrets` | An in-memory `SecretStore` |
| `read_key_list` / `write_key_list` | The item's text ↔ `KeyList` (`BTreeMap<String, String>`): an object of strings or a `SecretError` that does not quote the text; compact, sorted, non-ASCII as is |
| `SecretError` | `SecretError::new(message)` (kind `Store`), `with_kind(kind, message)`, `kind()`, `message()`: the platform's message or bmlib's own; never a key. `Display` adds `secret store: `, which Python's `str()` does not |
| `SecretErrorKind` | *(non-exhaustive, #689)* What will help: `UnreadableItem` (not a key list, or not text; saving a key replaces it, also when a `Vault` of an app's own returns it from `read`), `AmbiguousItem` (two items match; the user removes one in the keychain), `Store` (locked, denied, no keychain, another account's Windows credential). `as_str()` is Python's name (`"unreadable-item"`, `"ambiguous-item"`, `"store"`) |

**Platforms** (#686). macOS, Windows, Linux and the BSDs address the same
item as Python: on macOS the login keychain's generic password (service and
account); on Windows the generic credential named by the service alone (the
`target` modifier, where the crate's default is `account.service`), its
`UserName` set to the account after a write (a failure there is logged at
WARNING, not a failed write: the key list was saved, and an empty `UserName`
reads as this item in both languages), and one whose `UserName` is another
account refused rather than overwritten; on Linux and the BSDs the Secret
Service item matched by `service` and `username`, which Python's
`KeyringVault` writes with just those two attributes too, so two matching
items are `AmbiguousItem` in both languages. CI's `keychain-interop` job checks Windows and Linux live, through
the `keychain_vault` example. iOS needs the crate's `protected` store, which the v1
stores this feature uses do not configure; a mobile app brings its own
`Vault`.
