# `bmlib.fulltext` must import without httpx — design

_Issue [#64](https://github.com/hherb/bmlib/issues/64). Written 2026-08-08._

## The defect, as measured

Found smoke-testing the published 0.8.0 wheel in a clean core-only venv
(`bmlib`, `jinja2`, `markupsafe` and nothing else). Probing **every** module
in the package, **one fresh interpreter per module**:

```
FAIL bmlib.fulltext                          ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.fulltext.cache                    ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.fulltext.jats_parser              ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.fulltext.models                   ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.fulltext.pdf_converter            ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.fulltext.segmenter                ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.fulltext.service                  ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.publications.fetchers.biorxiv     ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.publications.fetchers.openalex    ModuleNotFoundError: No module named 'httpx'
FAIL bmlib.publications.fetchers.pubmed      ModuleNotFoundError: No module named 'httpx'

59 importable, 10 not
```

**Ten modules across two packages, from one line.** The issue reported one
(`SectionSegmenter`) and correctly warned that a single-process probe
under-reports — a failed import leaves the half-initialised parent in
`sys.modules`, and the siblings then falsely read as fine. The fresh-interpreter
measurement above is what the real number looks like.

### Cause

`bmlib/fulltext/__init__.py:47` eagerly re-exports the service:

```python
from bmlib.fulltext.service import FullTextError, FullTextService
```

and `bmlib/fulltext/service.py:35` has a hard top-level `import httpx`.
Importing any submodule imports its parent package first, so the whole
subpackage is gated behind httpx — including `models`, which is nothing but
dataclasses, and `segmenter`, which imports `re`, `statistics`, `typing` and
`bmlib.fulltext.models`.

That line is **the only top-level optional import left in bmlib**. Every other
one is already deferred: `transparency/analyzer.py:911`, `publications/sync.py:339`,
`fulltext/pdf_converter.py:239`, `db/connection.py:82`, `llm/providers/ollama.py:340`.

### Why the three fetchers are collateral, and why it matters

Each of `publications/fetchers/{pubmed,biorxiv,openalex}.py` opens with

```python
from bmlib.fulltext.models import FullTextSourceEntry
```

for one dataclass. None of the three imports httpx itself — all three take an
**injected** client (`client.get(url, params=...)`); `sync.py` builds the
default one behind its own deferred import. So they are genuinely usable
without httpx installed and are locked out for no reason of their own. This
is the part of the blast radius the issue did not record.

## Decision

All three options from the issue, because each fixes a different failure.
Approved 2026-08-08.

### 1. PEP 562 `__getattr__` in `fulltext/__init__.py`

`FullTextService` and `FullTextError` move into a `_LAZY_EXPORTS` frozenset
resolved on first attribute access, with a `TYPE_CHECKING` block so type
checkers still see them eagerly and a `__dir__` that lists them (the default
would omit them). Structurally copied from
`bmlib/context_processor/__init__.py:62-103`, which fixed this same shape one
package over: *"eager re-export pulled in `bmlib.templates` and jinja2, over
half the import cost, for callers who only wanted the harness."*

The public API and `__all__` are unchanged — `from bmlib.fulltext import
FullTextService` keeps working. **This change alone fixes all ten modules.**

### 2. Guarded import in `FullTextService.__init__`

After (1), a caller without httpx who asks for the service still gets a bare
`ModuleNotFoundError` from inside the lazy import, which is not the guarded
`ImportError` naming an extra that CLAUDE.md's convention requires.

`service.py` keeps `if TYPE_CHECKING: import httpx` for the
`_http_get(...) -> httpx.Response` annotation (`from __future__ import
annotations` is already in the module, so the annotation is a string at
runtime), and the real import moves into the constructor:

```python
try:
    import httpx
except ImportError as e:
    raise ImportError(
        "httpx is required for full-text retrieval. "
        "Install with: pip install bmlib[fulltext]"
    ) from e
self._httpx = httpx
```

with `_http_get` using `self._httpx.Client(...)`. This is exactly
`PyMuPDFConverter.__init__`'s shape one file over
(`pdf_converter.py:236-243`, storing `self._fitz`).

**The constructor, not `_http_get`.** The service builds its own client — it
is not dependency-injected the way the fetchers are — so deferring further
would not make the module usable without httpx, only make the failure arrive
later. A missing dependency should stop you when you construct the service,
not on the first request an hour into a batch job.

### 3. A `fulltext` extra

```toml
fulltext = ["httpx>=0.25"]
```

added to `all`. Today `docs/manual/fulltext.md:8` tells the reader to
`pip install bmlib[publications]` for `FullTextService`/`JATSParser` — a
publication-ingestion extra, installed for a PDF segmenter. (2)'s message
needs a name that matches the feature.

**httpx only; `pdf` stays separate.** Bundling pymupdf would duplicate an
extra that already exists and is separately documented, and would drag a
~20 MB binary wheel onto someone who only wants JATS retrieval.

**`publications` and `transparency` keep their httpx.** Removing either would
break existing installs; both genuinely need it.

## Testing

A new `TestPackageImports` class in `tests/test_fulltext_service.py`,
mirroring `tests/test_context_processor.py:971`. Every masked-httpx test runs
in a **subprocess** — `sys.modules` in the test process already holds httpx,
which is precisely the trap that mis-scoped the issue. Masking is a
`sys.meta_path` finder that raises `ModuleNotFoundError` for `httpx`.

| Test | Asserts |
|---|---|
| `test_the_mask_itself_blocks_httpx` | **Negative control.** With the mask installed, `import httpx` genuinely raises — otherwise every test below is vacuous |
| `test_the_stdlib_only_modules_import_without_httpx` | All seven `bmlib.fulltext.*` modules import, and `SectionSegmenter` is reachable from the package |
| `test_the_fetchers_import_without_httpx` | The three `publications.fetchers` modules import — the collateral half of the defect, in its own test so a regression names itself |
| `test_the_service_names_the_extra_when_httpx_is_missing` | `FullTextService(email=...)` raises `ImportError` mentioning `bmlib[fulltext]`, not a bare `ModuleNotFoundError` |
| `test_importing_the_package_does_not_import_httpx` | With httpx **present**, `import bmlib.fulltext` leaves `httpx` out of `sys.modules`. Proves the path is lazy, not merely that the modules import |
| `test_the_service_is_still_reachable_from_the_package` | Deferred, not removed: `FullTextService.__module__ == "bmlib.fulltext.service"` |
| `test_a_name_the_package_does_not_have_still_raises` | `AttributeError`, so `__getattr__` does not swallow typos |
| `test_dir_lists_the_deferred_names_too` | `dir()` includes both lazy names |

TDD: these are written first and watched fail.

## Documentation

`pyproject.toml`; README's extras table; `docs/manual/index.md`'s extras
table; `docs/manual/fulltext.md:8`; CLAUDE.md's extras table and its
"Optional dependencies guarded at the call site" pattern note;
`CHANGELOG.md` under `[Unreleased]`; ROADMAP and HANDOVER.

## Out of scope

- Issue **#56** (`_extract_title()` trusts junk PDF metadata titles) — a
  separate decision needing a measured corpus of real PDF metadata.
- Wiring `SectionSegmenter` into `FullTextService` or `quality/` — the
  roadmap item is unrelated to reachability.
- Severing the fetchers' dependency on `bmlib.fulltext.models`. It is one
  dataclass, the import is legitimate, and after (1) it costs nothing.
