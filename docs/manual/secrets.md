# bmlib.secrets — API keys in the OS keychain

*(unreleased, #634)* An app's API keys, by provider name, in **one** OS
keychain item: a JSON object `{provider: key}`. The item is read once and
cached, because macOS asks the user on every item read, so one item means one
prompt, not one per provider. Rust's `bmlib::secrets` reads and writes the
same item, held to this module by `tests/fixtures/secret_key_list.json`.

```bash
pip install bmlib[secrets]    # the keyring package (MIT); also in `all`
```

```python
from bmlib.secrets import KeyBundle, SecretError

keys = KeyBundle.keyring("com.example.myapp")   # service: the app's bundle id
keys.set("openai", "sk-...")
try:
    key = keys.get("openai")    # None when no key is stored
except SecretError as e:
    key = None                  # the keychain is locked or refused: fall back to the env var
    print(e)                    # the platform's message; never a key
```

| Name | What it is |
|---|---|
| `SecretStore` | Protocol: `get(provider) -> str \| None`, `set(provider, key)`, `delete(provider)` (an absent key is fine) |
| `KeyBundle(vault)` | A `SecretStore` over one `Vault` item, read once and cached. A failed read is not cached. `set` over an item that is not a key list replaces it with one holding just the new key (logged); `get` and `delete` raise. A failed write leaves the cache as it was. `KeyBundle.keyring(service)` is the OS keychain item `API_KEYS_ACCOUNT` of `service` |
| `Vault` | Protocol: `read() -> str \| None`, `write(text)` — the one item, the only part that touches the OS |
| `KeyringVault(service, account)` | The OS keychain item, through `keyring` (imported when used; without it, `SecretError`). Any backend failure is a `SecretError` naming its type and message |
| `MemorySecrets()` | An in-memory `SecretStore`, for tests or a session that keeps no key |
| `read_key_list(text)` / `write_key_list(keys)` | The item's text ↔ `dict[str, str]`. Reading refuses anything but an object of strings (and a lone surrogate, a byte-order mark) with a `SecretError` that does not quote the text; writing is compact, sorted by provider, non-ASCII as is |
| `API_KEYS_ACCOUNT` | `"api-keys"`, the item's account |

**Platforms.** On macOS (the login keychain's generic password, service and
account) and Linux (Secret Service, attributes `service` and `username`) both
languages address one item. On Windows they do not yet: Rust's `keyring`
crate names the credential `account.service`, this package `service` (#686).
