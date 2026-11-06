# secrets — API keys in the OS keychain

*(unreleased, #634)* An app's API keys, by provider name, in **one** OS
keychain item: a JSON object `{provider: key}`, read once and cached, because
macOS asks the user on every item read. Python's `bmlib.secrets` reads and
writes the same item ([secrets.md](../../../../docs/manual/secrets.md)), held
together by `tests/fixtures/secret_key_list.json`.

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
| `KeyBundle<V: Vault>` | A `SecretStore` over one item, read once and cached; a failed read is not cached. `set` over an item that is not a key list replaces it with one holding just the new key (logged to stderr); `get` and `delete` report it. A failed write leaves the cache as it was |
| `Vault` | `read() -> Option<String>`, `write(text)`: the one item, the only part that touches the OS |
| `KeyringVault::new(service, account)` | *(feature `keyring`)* The OS keychain item; a missing item reads as `None` |
| `MemorySecrets` | An in-memory `SecretStore` |
| `read_key_list` / `write_key_list` | The item's text ↔ `KeyList` (`BTreeMap<String, String>`): an object of strings or a `SecretError` that does not quote the text; compact, sorted, non-ASCII as is |
| `SecretError(String)` | The platform's message or bmlib's own; never a key |

**Platforms** (#686). macOS addresses the same item as Python. Linux does
only while Python writes first: Python's `keyring` adds an `application`
attribute, so its write beside an item this crate created makes a second
item, and this crate's lookup then fails as ambiguous. Windows does not yet:
the `keyring` crate names the credential `account.service`, Python's
`keyring` `service`. iOS needs the crate's `protected` store, which the v1
stores this feature uses do not configure; a mobile app brings its own
`Vault`.
