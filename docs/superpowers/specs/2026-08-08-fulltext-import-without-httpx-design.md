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

That line is **the only top-level optional import left in bmlib** — verified by
AST-scanning the top-level `Import`/`ImportFrom` nodes of all 69 modules, not
by grep. Every other optional import is already function-local:
`transparency/analyzer.py:911`, `publications/sync.py:339`,
`fulltext/pdf_converter.py:239`, `db/connection.py:82-83`,
`llm/providers/ollama.py:340`, `llm/providers/anthropic.py:135`,
`llm/providers/openai_compat.py:122`.

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
runtime), and the real import moves into a module-level helper:

```python
def _require_httpx() -> ModuleType:
    try:
        import httpx
    except ImportError as e:
        raise ImportError(
            f"httpx is required for full-text retrieval, but importing it failed "
            f"({e}). Install with: pip install bmlib[fulltext]"
        ) from e
    return httpx
```

called first thing in `__init__` (for the result's side effect only) and again
in `_http_get`, which binds it locally to build the client.

**The constructor, not only `_http_get`.** The service builds its own client —
it is not dependency-injected the way the fetchers are — so a missing
dependency should stop you when you construct the service, not on the first
request an hour into a batch job.

**Returned, not stored.** The first draft did what
`PyMuPDFConverter.__init__` does one file over (`pdf_converter.py:236-245`,
storing `self._fitz`) and kept `self._httpx`. Review rejected it on two
measured counts: a module object cannot be pickled, so a configured service
could no longer be handed to a `ProcessPoolExecutor` — a regression against
`main`, where every attribute was a `str`/`float`/`bool`/`FullTextCache` — and
reading the module back as *instance* state means anything reaching
`_http_get` without having run `__init__` raises `AttributeError`, which the
tier chain's nine `except Exception` blocks swallow at DEBUG and return a
success-shaped `FullTextResult` for. A `sys.modules` lookup per request, on a
path that then makes a network round-trip, costs nothing measurable.
`PyMuPDFConverter` is not changed to match: it was never picklable, so nothing
there regressed.

**The message reports the cause rather than asserting it.** `except
ImportError` also catches a `ModuleNotFoundError` raised by a *present* httpx
for its own missing dependency, and an `ImportError` from a version skew
inside httpx. Both were verified to produce the old "not installed" wording,
whose prescribed `pip install bmlib[fulltext]` then answers "Requirement
already satisfied" and changes nothing. `_attach_pdf_text` already documents
this exact reasoning for PyMuPDF (`service.py:573-585`): *"report what was
actually raised rather than asserting the cause, so a broken PyMuPDF install
is not misreported as an uninstalled one."*

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

Seventeen tests in all: fifteen in `TestPackageImports`, plus two in a new
`TestHttpGet` covering the request helper. The last six rows were added in
review — see "What review added" below.

| Test | Asserts |
|---|---|
| `test_the_mask_itself_blocks_httpx` | **Negative control.** With the mask installed, `import httpx` genuinely raises — otherwise the four masked tests below are vacuous |
| `test_the_stdlib_only_modules_import_without_httpx` | All seven `bmlib.fulltext.*` modules import, one fresh interpreter each |
| `test_the_segmenter_is_reachable_from_the_package_without_httpx` | The reported symptom: `from bmlib.fulltext import SectionSegmenter` resolves |
| `test_the_fetchers_import_without_httpx` | The three `publications.fetchers` modules import — the collateral half of the defect, in its own test so a regression names itself |
| `test_the_service_names_the_extra_when_httpx_is_missing` | `FullTextService(email=...)` raises `ImportError` mentioning `bmlib[fulltext]`, not a bare `ModuleNotFoundError`, and leaves the redirected home directory **entirely** empty |
| `test_importing_the_package_does_not_import_httpx` | With httpx **present**, `import bmlib.fulltext` leaves `httpx` out of `sys.modules`. Proves the path is lazy, not merely that the modules import |
| `test_importing_the_package_does_not_load_the_service` | With httpx present, `import bmlib.fulltext` leaves `bmlib.fulltext.service` out of `sys.modules`, and the first attribute access puts it there. **Added after mutation testing** — see below |
| `test_the_service_is_still_reachable_from_the_package` | Deferred, not removed: `FullTextService.__module__ == "bmlib.fulltext.service"` |
| `test_the_deferred_names_are_still_exported` | Both names remain in `__all__` |
| `test_a_name_the_package_does_not_have_still_raises` | `AttributeError`, so `__getattr__` does not swallow typos |
| `TestHttpGet::…_carries_the_configured_timeout_and_follows_redirects` | The client is built with `timeout=` and `follow_redirects=True`, and the URL and kwargs reach `client.get`. **The only test that executes `_http_get`'s body** |
| `TestHttpGet::…_is_closed_even_when_the_request_raises` | A raising GET still leaves the `with` block, so the socket is not leaked |
| `test_a_broken_httpx_is_not_reported_as_an_absent_one` | With a shim httpx that raises on import, the message carries the real cause *and* the extra, and `__cause__` survives |
| `test_the_service_survives_pickling_and_deep_copying` | The service holds no module object, so a process pool can take one |
| `test_the_extra_the_error_message_names_is_a_real_one` | `fulltext` is in the built distribution's `Provides-Extra`, so the message and `pyproject.toml` cannot drift apart |
| `test_dir_lists_the_deferred_names_without_hiding_anything` | `dir()` gains both lazy names **and** keeps the submodules and dunders |
| `test_a_resolved_name_is_bound_and_not_re_resolved` | The resolved name lands in `vars(package)`, so repeat access skips `__getattr__` |

TDD: the original ten were written first and watched fail. Five of them did
(the negative control and the API-preservation tests pass either way, which is
their job).

### What mutation testing found: the two changes overlap

Reverting each change separately, with `__pycache__` cleared between runs:

| Mutation | Expected to fail | Actually failed |
|---|---|---|
| Mask disabled in the test helper | `test_the_mask_itself_blocks_httpx` | ✅ that test, plus the extra-naming one |
| Guarded `ImportError` → bare `import httpx` in `__init__` | `test_the_service_names_the_extra_when_httpx_is_missing` | ✅ exactly that test |
| PEP 562 → eager re-export restored | an import test | ❌ **nothing failed** |

The third result is the interesting one. Once the httpx import has moved into
`FullTextService.__init__`, `service.py` has no top-level `import httpx` left,
so re-exporting it eagerly no longer gates anything. **Either change alone
restores importability** — they are not the independent halves the design
above implies, and the original test set pinned only one of them.

`test_importing_the_package_does_not_load_the_service` was added to state what
PEP 562 does buy on its own: `import bmlib.fulltext` does not load `service`
at all, and the first attribute access is what loads it. That is the
structural half of the guarantee — no future top-level import in `service.py`
can gate the parser, the models or the segmenter again — and it fails under
the third mutation, which is what makes it a real guard rather than a
restatement.

### What review added

A four-agent review of the PR confirmed the headline measurements — the ten
modules, the "last unguarded optional import" claim, and the mutation table
above all reproduced exactly — and found four things the tests had not.

| Finding | Change |
|---|---|
| `_http_get`'s body was executed by **no test**: all ~45 existing tests patch `_http_get` itself, and `--cov` reported its two lines missing. Replacing the body with `raise AssertionError` left the suite green | `TestHttpGet`, two tests |
| Storing `self._httpx` made the service unpicklable (`TypeError: cannot pickle 'module' object`) — a regression against `main` — and turned a skipped `__init__` into an `AttributeError` the tier chain swallows at DEBUG | `_require_httpx()` returns the module instead; pickling test |
| `except ImportError` relabelled a *broken* httpx as an absent one, prescribing a no-op `pip install` | Message interpolates the real cause; shim-httpx test |
| `__dir__` returning `__all__` alone dropped the submodules and dunders, and the test asserting presence passed under the narrowing | Union; test asserts both halves. Same fix in `context_processor` |

Three smaller ones: the subprocess helper replaced the environment rather than
merging it (dropping `PYTHONPATH`, and on Windows the `USERPROFILE` that
`Path.home()` actually reads there, which made the cache assertions vacuous);
the cache assertion named two of the three platform cache directories; and two
comments miscounted — `_FULLTEXT_MODULES`' "all ten names below" over a
seven-element list, and the negative control's "every test below" over four
masked ones.

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
- The tier chain's total-failure path (`service.py:379-380`) logging nothing
  above DEBUG, so "every tier raised" is indistinguishable from "this paper
  has no free full text". Pre-existing, surfaced by this review, filed
  separately rather than widened into this PR.
