# bmlib::atomic — Publishing a File Atomically

`atomic_write` writes a file so that no partial version is ever visible under
its name: the bytes go to a uniquely-named temporary file beside the target,
are synchronised to disk, and are then renamed over the target. The full-text
cache and `TemplateEngine::install_defaults` write every file this way. Python
has the same helper as the private `bmlib/_atomic.py`, so there is no Python
page; in Rust the module is public, for a host app with the same need.

| Need | Feature |
|---|---|
| `atomic_write`, `temp_path_beside`, `TEMP_SUFFIX`, `TEMP_SUFFIX_LEN` | none |

## Writing a file

```rust
use bmlib::atomic::atomic_write;

let dir = std::env::temp_dir().join("bmlib-manual-atomic");
std::fs::create_dir_all(&dir)?;
let target = dir.join("summary.txt");

atomic_write(&target, b"first version")?;
atomic_write(&target, b"second version")?;          // replaces it whole
assert_eq!(std::fs::read(&target)?, b"second version");
# std::fs::remove_dir_all(&dir).ok();
# Ok::<(), std::io::Error>(())
```

The parent directory must exist; `atomic_write` does not create it. Any
filesystem failure is an `Err(std::io::Error)`, including a full disk, which
under delayed allocation is reported only when the data is synchronised. On a
failure the target is left as it was, the previous version or nothing.

What it does and does not promise:

- **Visibility, not crash durability.** The data is synchronised before the
  rename, but the directory is not, so after a crash the rename can be lost
  and the target is simply absent. Both bmlib callers treat absence as a miss
  and repair it.
- **The rename replaces whatever is at the path, a symlink included** — the
  link, not the file it points at. Check for a symlink first if one there is
  the user's own indirection (`install_defaults` skips a dangling one).
- **The temporary name is the target's plus `TEMP_SUFFIX` and a unique token**
  (`temp_path_beside(path)` returns it). A caller building filenames from
  unbounded input must leave room for it under the filesystem's name limit.
  The token is fixed-width, so `TEMP_SUFFIX_LEN` (61) is exactly what the
  name adds, and it is what the full-text cache reserves. The temporary is
  removed whether or not the write succeeded, as Python's helper does.

Python's temporary name is `.<name>.<hex>.tmp`, a hidden file; Rust's is not
hidden.
