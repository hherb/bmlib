# bmlib.secrets — API keys in the OS keychain

*(unreleased, #634)* An app's API keys, by provider name, in **one** OS
keychain item: a JSON object `{provider: key}`. The item is read once for
every `get` and cached, because macOS asks the user on every item read, so one
item means one prompt, not one per provider; a `set` or `delete` reads it again
before rewriting it, so a key another process saved meanwhile is kept. Rust's
`bmlib::secrets` uses the same layout (on macOS the same item; see
*Platforms*), held to this module by `tests/fixtures/secret_key_list.json`.

```bash
pip install bmlib[secrets]    # the keyring package (MIT); also in `all`
```

```python
from bmlib.secrets import KeyBundle, SecretError, SecretErrorKind

keys = KeyBundle.keyring("com.example.myapp")   # service: the app's bundle id
keys.set("openai", "sk-...")
try:
    key = keys.get("openai")    # None when no key is stored
except SecretError as e:
    key = None                  # fall back to the env var
    print(e)                    # the platform's message; never a key
    if e.kind is SecretErrorKind.UNREADABLE_ITEM:
        print("Saving a key replaces the damaged keychain item.")
```

| Name | What it is |
|---|---|
| `SecretStore` | Protocol: `get(provider) -> str \| None`, `set(provider, key)`, `delete(provider)` (an absent key is fine) |
| `KeyBundle(vault)` | A `SecretStore` over one `Vault` item: `get` reads it once and answers from the cache, `set` and `delete` read it again and rewrite it. A failed read is not cached. `set` over an item that is not a key list (or not UTF-8) replaces it with one holding just the new key, dropping whatever it held (logged at WARNING); `get` and `delete` raise. `set` refuses a provider or key holding a lone surrogate. A failed write leaves the cache as it was. `vault` is read-only. `KeyBundle.keyring(service)` is the OS keychain item `API_KEYS_ACCOUNT` of `service` |
| `Vault` | Protocol: `read() -> str \| None`, `write(text)` — the one item, the only part that touches the OS |
| `KeyringVault(service, account)` | The OS keychain item, through `keyring` (imported when used; when it cannot be imported, a `SecretError` saying what the import raised). An item that is not UTF-8 reads as unreadable. Any other backend failure is a `SecretError` naming its type and message, with no exception chained to it (one could hold the item) |
| `MemorySecrets()` | An in-memory `SecretStore`, for tests or a session that keeps no key |
| `read_key_list(text)` / `write_key_list(keys)` | The item's text ↔ `dict[str, str]`. Reading refuses anything but an object of strings (and a lone surrogate, a byte-order mark) with a `SecretError` that does not quote the text; writing is compact, sorted by provider, non-ASCII as is |
| `SecretError(message, kind=SecretErrorKind.STORE)` | Every failure: `str()` is the platform's message or bmlib's own, never a key; `kind` says which failure (#689), and a kind given by name becomes the member (an unknown one raises `ValueError`). Pickles with its kind |
| `SecretErrorKind` | `UNREADABLE_ITEM` (`"unreadable-item"`: not a key list, or not UTF-8; `set` replaces it, also when a `Vault` of an app's own raises it from `read`), `AMBIGUOUS_ITEM` (`"ambiguous-item"`: two items match; `set` does not replace it, the user removes one in the keychain; `KeyringVault` reports it on Linux, as Rust's does, and a `Vault` of an app's own may), `STORE` (`"store"`: locked, denied, no keychain, the `keyring` package missing, or a provider or key no keychain holds). Rust's `SecretErrorKind`, by the same names |
| `API_KEYS_ACCOUNT` | `"api-keys"`, the item's account |

**Platforms** (#686). Both languages address one item. On macOS it is the
login keychain's generic password (service and account), through `keyring`.
On Windows it is the generic credential named by the service alone, its
`UserName` the account; this package writes it through `win32cred` and not
`keyring.set_password`, which first copies the old value to
`account@service`, so a deleted key would stay in Credential Manager. On
Linux it is the Secret Service item matched by `service` and `username`
alone, created in the default collection; this package goes through
`secretstorage`, since `keyring` adds an `application` attribute (a second
item beside Rust's) and reads the first of two. Two matching items are
`AMBIGUOUS_ITEM` in both languages. So on Windows and Linux `KeyringVault`
uses the platform's own store, not a `keyring` backend configured otherwise.
CI's `keychain-interop` job checks both live, each language reading what the
other wrote (`tests/test_secrets_interop.py`).
