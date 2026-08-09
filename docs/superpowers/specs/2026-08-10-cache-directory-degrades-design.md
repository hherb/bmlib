# A cache directory that cannot be created must not abort construction — design

_Issue [#75](https://github.com/hherb/bmlib/issues/75). Written 2026-08-10._

## The defect

`FullTextCache.__init__` makes three unguarded directory calls:

```python
self.cache_dir.mkdir(parents=True, exist_ok=True)
self._pdf_dir.mkdir(parents=True, exist_ok=True)
self._html_dir.mkdir(parents=True, exist_ok=True)
```

They run inside `FullTextService.__init__` whenever no cache is passed, so an
environment fault about the *cache* aborts the whole construction:

```
FullTextService(email=...) -> FileExistsError: [Errno 17] File exists: '.../notadir'
```

This is the last place in `fulltext/` where the cache is not best-effort.
Everywhere else it already degrades: a failed write warns once and retrieval
continues (#67), a failed read falls through to the network and quarantines the
entry (#71). The one place it is fatal is the one place the caller has done
nothing wrong yet — and `FullTextService.__init__`'s `Raises:` section lists
only `ImportError`, so it is undocumented as well as unguarded.

### What is actually reachable, measured

The three `mkdir` calls, verified on this platform:

| Environment fault | Raised |
|---|---|
| A file standing where the directory should be | `FileExistsError` — `exist_ok=True` suppresses this only when the target *is* a directory |
| A read-only parent | `PermissionError` |
| A file as an intermediate path component | `NotADirectoryError` |
| A full disk | `OSError` (ENOSPC) |

All four are `OSError`. But `_default_cache_dir()` runs first and calls
`Path.home()`, which raises **`RuntimeError`**, not `OSError`, when the home
directory cannot be determined — no `HOME`, no passwd entry, which is an
ordinary distroless container:

```
>>> with mock.patch('os.path.expanduser', lambda p: p): Path.home()
RuntimeError: Could not determine home directory.
```

So `except OSError` alone would fix the reported shape and leave the same
defect, one layer up, waiting to be re-filed.

## The decision

**Degrade in `FullTextService`, keep raising in `FullTextCache`.**

Only the *default* cache construction is guarded. `FullTextService(email=...)`
warns once naming what was raised, sets `self.cache = None`, and retrieves
normally without caching. A directly-constructed `FullTextCache()` still
raises: that caller asked for a cache specifically, and degrading would hand
back an object whose every method then fails one at a time instead of failing
once, clearly, at construction.

Two alternatives were rejected:

- **Guarding inside `FullTextCache.__init__`.** Simpler — one place, no
  service-side change — but it silences the direct caller above.
- **Keeping the raise and merely documenting it.** Cheapest and most explicit,
  but it leaves `fulltext/` with one place where an environment fault about the
  cache kills a run that could have proceeded, which is the defect class #71 was
  just fixed for.

## The change

A module-level helper in `service.py`, beside the existing `_require_httpx` /
`_normalise_pmc_id`:

```python
def _default_cache() -> FullTextCache | None:
    try:
        return FullTextCache()
    except (OSError, RuntimeError) as exc:
        logger.warning(
            "Could not create the full-text cache directory (%s: %s); retrieval "
            "still works, but nothing will be cached, so every run re-fetches.",
            type(exc).__name__, exc,
        )
        return None
```

and `service.py:280` becomes:

```python
self.cache: FullTextCache | None = cache if cache is not None else _default_cache()
```

Three details are load-bearing:

- **A helper, not an inline `try`.** It is testable on its own, and it keeps
  the failure out of `__init__`'s body, which already carries the `httpx`
  fail-fast the ordering depends on.
- **The wording mirrors `_warn_cache_write_failed`.** Same fault, same
  consequence for the operator — a corpus re-fetched over the network on every
  run, permanently — so an operator who meets either reads the same sentence.
- **The exception *type* is named as well as its message**, per #71's finding
  that a bmlib bug must not read as an ordinary environment fault. `str()` on a
  `FileExistsError` carries the errno and the path but never the class name.

The guard catches `OSError` and `RuntimeError` and not `Exception`: within this
one constructor `RuntimeError` has exactly one source, so the pair is narrow in
practice, while `Exception` would swallow a bmlib bug inside `FullTextCache`.

## The guard that starts lying

`self.cache` is already falsy-checked at three sites — `fetch_fulltext`'s cache
read (`service.py:329`), `_cache_html` (`754`) and `_download_and_cache_pdf`
(`775`). **Those branches are dead today**: `FullTextCache` defines no
`__bool__` or `__len__` so it is always truthy, and `self.cache` is never
`None`. This change makes them live, and one of them is then wrong:

```python
if not cache_id or not self.cache:
    if self.convert_pdfs:
        logger.info("convert_pdfs is on but no identifier was given — ...")
```

With a failed cache and an identifier in hand, that asserts a cause that is
false. The two conditions are split so each reports what actually happened, and
the no-cache branch logs at **DEBUG**, not INFO: `self.cache is None` is
reachable only through `_default_cache()`, which always warns at construction,
so an INFO line would repeat what the operator was already told once per paper
in a corpus. All three sites move from truthiness to `is None` / `is not None`
while they are being made live, since what they mean is "there is no cache",
not "the cache is falsy".

`_check_cache` dereferences `self.cache` without a `None` check; it is reached
only from the one guarded site, and that stays true.

## Non-goals

- **No writability probe at construction.** A directory that exists but is
  read-only passes `mkdir(exist_ok=True)`, and that case is already #67's
  warn-once on the first failed write. Probing would be TOCTOU and would litter
  the operator's cache directory with a test file.
- **No fallback location.** Silently relocating to a temp directory surprises a
  caller who set `cache_dir` deliberately, and a cache that vanishes on reboot
  looks like a working cache that never hits.
- **No `cache=False` parameter to disable caching explicitly.** Not asked for,
  and `None` already means "construct the default".
- **Not a mid-run check.** A cache directory that disappears after construction
  stays #67's territory.

## Tests

In `tests/test_fulltext_service.py`, against a **real filesystem fault** rather
than a stubbed constructor, so the test exercises the exception the platform
actually raises:

1. A file standing where the cache directory should be leaves construction
   succeeding and `service.cache is None`.
2. `Path.home` raising `RuntimeError` does the same — the half that
   `except OSError` alone would miss.
3. The warning fires and names the exception type.
4. **Retrieval still works end to end with no cache**: the test that proves
   degrading degrades rather than relocating the crash to the first fetch.
5. The PDF path no longer claims "no identifier was given" when the real cause
   is a cache that failed to construct.

Each carries the negative control the repo expects — a *valid* directory must
yield a non-`None` cache — since an assertion that `cache is None` passes
vacuously if the fault never fired.

In `tests/test_fulltext_cache.py`, one test pins that
`FullTextCache(cache_dir=<a file>)` **still raises**. It is the half of the
decision that would otherwise erode silently: nothing else fails if a later
session "tidies" the guard down into `FullTextCache`.

Every guard is verified by mutation. Narrowing `(OSError, RuntimeError)` to
`OSError`, and removing the `try` altogether, must each turn a named test red.

## Documentation

- `FullTextService.__init__`'s `cache:` argument gains the degradation note.
  Its `Raises: ImportError` needs no new entry — the change makes the existing
  section *true*, where today it omits the `OSError` the constructor can raise.
- `docs/manual/fulltext.md:194` says a default cache is constructed "so a cache
  always exists". That clause stops being true.
- `CHANGELOG.md` under `[Unreleased]`, the ROADMAP row for #75, and HANDOVER's
  open-issue list.
- `docs/DECISIONS.md`: "the service degrades but the cache still raises" is an
  asymmetry that reads like an inconsistency worth correcting, so it is
  recorded with the argument above.
