# A cache directory that cannot be created must not abort construction — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `FullTextService(email=...)` must survive a cache directory it cannot create — warning once and retrieving without a cache — while a directly-constructed `FullTextCache` still raises.

**Architecture:** One new module-level helper in `bmlib/fulltext/service.py` wraps the *default* cache construction in `try/except (OSError, RuntimeError)`, warns naming the exception type, and returns `None`. `FullTextCache` is untouched. Three existing `self.cache` falsy-checks are dead code today and become live; they move to explicit `is None` / `is not None`, and the one that logs a cause is split so it stops asserting a false one.

**Tech Stack:** Python 3.11+, pytest, ruff. Standard library only — no new dependency.

**Spec:** [`docs/superpowers/specs/2026-08-10-cache-directory-degrades-design.md`](../specs/2026-08-10-cache-directory-degrades-design.md). Issue [#75](https://github.com/hherb/bmlib/issues/75).

## Global Constraints

- **Branch:** `fix/75-cache-directory-degrades` (already created, spec already committed on it).
- **TDD, always.** Write the failing test first, run it, watch it fail *for the stated reason*, then implement. A bug in a test you wrote is fixed in the test, not in correct code.
- **AGPL-3 header** on every source file. Both files touched here already have one; do not disturb it.
- **Type hints and docstrings** on everything public. Google-style within these two modules, matching what is already there.
- **`uv` only, never bare pip.** Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not `.venv`'s:** `uvx ruff@0.15.20 check .` and `uvx ruff@0.15.20 format --check .`. Line length 100.
- **Baseline is 1758 passed + 58 skipped.** Every task must leave the whole suite green; the count only goes up.
- **Never catch bare `Exception` in the new guard.** `(OSError, RuntimeError)` exactly — the spec records why.
- **Report what was raised; never assert a cause.** Log messages interpolate `type(exc).__name__` *and* `exc`.
- **Do not modify `bmlib/fulltext/cache.py` at all.** Keeping `FullTextCache` raising is half the decision.

---

### Task 1: Pin that a directly-constructed cache still raises

The half of the decision with no code behind it. Nothing fails if a later session "tidies" the guard down into `FullTextCache`, so this test is the only thing standing between that and a silent behaviour change. Written first, against today's unmodified code, so it is a genuine characterisation test rather than one shaped to fit the fix.

**Files:**
- Test: `tests/test_fulltext_cache.py` (append a new class at the end)

**Interfaces:**
- Consumes: `FullTextCache` from `bmlib.fulltext.cache` (already imported in this test file — verify at the top rather than adding a duplicate import).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the test**

Append to `tests/test_fulltext_cache.py`:

```python
class TestADirectlyConstructedCacheStillRaises:
    """The half of #75's decision that has no code behind it.

    ``FullTextService`` degrades when the *default* cache cannot be built,
    but a caller who constructed a ``FullTextCache`` asked for a cache
    specifically: degrading there would hand back an object whose every
    method then fails one at a time, instead of failing once, clearly, at
    construction. Nothing else in the suite would notice if that guard were
    "tidied" down into this class, so this test is what holds the asymmetry.
    """

    def test_a_file_where_the_cache_directory_should_be_raises(self, tmp_path):
        blocker = tmp_path / "notadir"
        blocker.write_text("I am a file, not a directory")

        with pytest.raises(OSError):
            FullTextCache(cache_dir=blocker)

    def test_a_usable_directory_still_constructs(self, tmp_path):
        """Negative control: the raise above must come from the fault.

        Without this, a ``FullTextCache`` that raised for some unrelated
        reason — a bad default, a broken import — would satisfy the test
        above while telling us nothing about the fault it names.
        """
        cache = FullTextCache(cache_dir=tmp_path / "fresh")
        assert (tmp_path / "fresh" / "pdfs").is_dir()
        assert (tmp_path / "fresh" / "html").is_dir()
```

- [ ] **Step 2: Run it — it must pass immediately**

```bash
uv run pytest tests/test_fulltext_cache.py::TestADirectlyConstructedCacheStillRaises -v
```

Expected: **2 passed.** This is the one test in the plan that starts green — it pins behaviour that already exists and must not change. If it fails, stop: the assumption the whole design rests on is wrong.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fulltext_cache.py
git commit -m "test(fulltext): pin that a directly-constructed cache still raises (#75)"
```

---

### Task 2: Degrade instead of aborting construction

**Files:**
- Modify: `bmlib/fulltext/service.py` — add `_default_cache()` beside `_normalise_pmc_id` (around line 230, before `class FullTextService`), and change the `self.cache` assignment at line 280.
- Test: `tests/test_fulltext_service.py` (append a new class at the end)

**Interfaces:**
- Consumes: `FullTextCache` and `logger`, both already module-level in `service.py`.
- Produces: `_default_cache() -> FullTextCache | None` — a module-level function in `bmlib.fulltext.service`, imported by name in the tests. `FullTextService.cache` becomes `FullTextCache | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestAnUncreatableCacheDirectoryDoesNotAbortConstruction:
    """#75 — the last place in fulltext/ where the cache was not best-effort.

    Everywhere else already degrades: a failed write warns once (#67), a
    failed read falls through to the network and quarantines the entry (#71).
    The one place it was fatal is the one place the caller has done nothing
    wrong yet.

    The faults are real filesystem and stdlib faults, not a stubbed
    constructor, so each test exercises the exception the platform actually
    raises rather than one chosen to match the guard.
    """

    @staticmethod
    def _blocked_default_dir(tmp_path, monkeypatch) -> Path:
        """Point the default cache location at a file, and return it."""
        blocker = tmp_path / "notadir"
        blocker.write_text("I am a file, not a directory")
        monkeypatch.setattr(
            "bmlib.fulltext.cache._default_cache_dir", lambda: blocker
        )
        return blocker

    def test_a_file_in_the_way_leaves_a_service_with_no_cache(
        self, tmp_path, monkeypatch
    ):
        self._blocked_default_dir(tmp_path, monkeypatch)

        service = FullTextService(email="test@example.com")

        assert service.cache is None

    def test_a_usable_default_directory_still_yields_a_cache(
        self, tmp_path, monkeypatch
    ):
        """Negative control for every ``cache is None`` assertion here.

        A guard that returned ``None`` unconditionally — or a fault that
        never fired — would satisfy those assertions while proving nothing.
        """
        monkeypatch.setattr(
            "bmlib.fulltext.cache._default_cache_dir", lambda: tmp_path / "fresh"
        )

        service = FullTextService(email="test@example.com")

        assert isinstance(service.cache, FullTextCache)

    def test_a_home_directory_that_cannot_be_determined_is_survived(
        self, monkeypatch
    ):
        """The half ``except OSError`` alone would miss.

        ``_default_cache_dir()`` runs before any ``mkdir`` and calls
        ``Path.home()``, which raises ``RuntimeError`` — not ``OSError`` —
        when there is no ``HOME`` and no passwd entry, which is an ordinary
        distroless container.
        """
        monkeypatch.setattr(os.path, "expanduser", lambda p: p)
        # Precondition: assert the mechanism, so that a future Python
        # changing how Path.home() resolves fails loudly here instead of
        # leaving the test passing for the wrong reason.
        with pytest.raises(RuntimeError):
            Path.home()

        service = FullTextService(email="test@example.com")

        assert service.cache is None

    def test_the_warning_names_what_was_raised(self, tmp_path, monkeypatch, caplog):
        """A bmlib bug must not read as an ordinary environment fault (#71).

        ``str()`` on a ``FileExistsError`` carries the errno and the path but
        never the class name, so the type is interpolated separately.
        """
        blocker = self._blocked_default_dir(tmp_path, monkeypatch)

        with caplog.at_level("WARNING"):
            FullTextService(email="test@example.com")

        assert "FileExistsError" in caplog.text
        assert str(blocker) in caplog.text
        # Says what it costs the operator, in the words the unwritable-cache
        # warning already uses — the same fault with the same consequence.
        assert "re-fetch" in caplog.text.lower()

    def test_a_caller_supplied_cache_never_reaches_the_guard(
        self, tmp_path, monkeypatch
    ):
        """An explicit cache is used as given, fault in the default or not."""
        self._blocked_default_dir(tmp_path, monkeypatch)
        supplied = FullTextCache(cache_dir=tmp_path / "mine")

        service = FullTextService(email="test@example.com", cache=supplied)

        assert service.cache is supplied
```

Note: `os`, `Path`, `pytest`, `FullTextCache` and `FullTextService` are all already imported at the top of this file. Verify rather than re-adding.

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/test_fulltext_service.py::TestAnUncreatableCacheDirectoryDoesNotAbortConstruction -v
```

Expected: **3 failed, 2 passed.** The failures are `FileExistsError` and `RuntimeError` escaping `FullTextService(...)` — the bug itself. The two that pass are the negative control and the caller-supplied case, which already work. If a failure is anything other than those two exception types escaping the constructor, the test is wrong — fix the test.

- [ ] **Step 3: Add the helper**

In `bmlib/fulltext/service.py`, immediately after `_normalise_pmc_id` and before `class FullTextService`:

```python
def _default_cache() -> FullTextCache | None:
    """Construct the default disk cache, or degrade to no caching.

    The cache is best-effort everywhere else in this module — a failed write
    warns once and retrieval continues, a failed read falls through to the
    network — and construction was the last place an environment fault about
    the *cache* could abort a run that had every chance of succeeding without
    one.

    Only the *default* is guarded. A caller who constructs a
    :class:`~bmlib.fulltext.cache.FullTextCache` themselves asked for a cache
    specifically, and still gets the raise: degrading there would return an
    object whose every method then fails one at a time, rather than failing
    once, clearly, at construction.

    ``OSError`` covers the three ``mkdir`` calls — a file standing where the
    directory should be (``FileExistsError``; ``exist_ok=True`` suppresses
    that only when the target *is* a directory), a read-only parent, a file as
    an intermediate component, a full disk. ``RuntimeError`` covers the step
    before them: ``_default_cache_dir()`` calls ``Path.home()``, which raises
    that, not ``OSError``, when there is no ``HOME`` and no passwd entry. The
    pair is deliberately not ``Exception`` — inside this one constructor
    ``RuntimeError`` has exactly one source, so the guard stays narrow enough
    that a bmlib bug still surfaces as one.

    Returns:
        The cache, or ``None`` if it could not be created.
    """
    try:
        return FullTextCache()
    except (OSError, RuntimeError) as exc:
        logger.warning(
            "Could not create the full-text cache directory (%s: %s); retrieval "
            "still works, but nothing will be cached, so every run re-fetches.",
            type(exc).__name__,
            exc,
        )
        return None
```

- [ ] **Step 4: Wire it in**

In `FullTextService.__init__`, replace line 280:

```python
        self.cache = cache if cache is not None else FullTextCache()
```

with:

```python
        self.cache: FullTextCache | None = (
            cache if cache is not None else _default_cache()
        )
```

- [ ] **Step 5: Run the new tests, then the whole suite**

```bash
uv run pytest tests/test_fulltext_service.py::TestAnUncreatableCacheDirectoryDoesNotAbortConstruction -v
uv run pytest tests/ -q
```

Expected: 5 passed, then the whole suite green at **1765 passed, 58 skipped** (1758 + 5 here + 2 from Task 1).

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "fix(fulltext): degrade to no caching when the cache dir cannot be created (#75)"
```

---

### Task 3: Prove the degraded service actually retrieves

Task 2 makes construction survive. That is worthless if the resulting object crashes on the first fetch — which is exactly what would happen if any `self.cache` dereference were unguarded. This task is the test that tells the two apart, and it is why the change needs no new `None` plumbing: the guards already exist.

**Files:**
- Test: `tests/test_fulltext_service.py` (append to the class from Task 2)

**Interfaces:**
- Consumes: `_default_cache`/`FullTextService.cache` from Task 2; `FIXTURES / "sample_article.xml"`, the JATS fixture the existing cache tests use.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the test**

Append these two methods to `TestAnUncreatableCacheDirectoryDoesNotAbortConstruction`:

```python
    def test_retrieval_still_works_with_no_cache(self, tmp_path, monkeypatch):
        """Degrading must degrade, not relocate the crash to the first fetch.

        Every ``self.cache`` use site is already guarded, so no new plumbing
        was needed — but "already guarded" is a claim about code that had
        never run, since ``self.cache`` could not be ``None`` before #75.
        This is what executes it.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com")
        assert service.cache is None

        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        with patch.object(service, "_http_get", return_value=resp):
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"
        assert result.html is not None

    def test_nothing_is_written_where_the_cache_would_have_gone(
        self, tmp_path, monkeypatch
    ):
        """The blocking file is left exactly as it was.

        A guard that swallowed the fault but left a half-built cache behind
        would pass every assertion above.
        """
        blocker = self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com")

        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        with patch.object(service, "_http_get", return_value=resp):
            service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/test")

        assert blocker.is_file()
        assert blocker.read_text() == "I am a file, not a directory"
```

- [ ] **Step 2: Run them**

```bash
uv run pytest tests/test_fulltext_service.py::TestAnUncreatableCacheDirectoryDoesNotAbortConstruction -v
```

Expected: **7 passed.** These should pass with no production change — that is the finding. If either fails with `AttributeError: 'NoneType' object has no attribute ...`, a `self.cache` use site is unguarded: add the guard, and say which one in the commit message.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fulltext_service.py
git commit -m "test(fulltext): a service with no cache still retrieves (#75)"
```

---

### Task 4: Stop the PDF path asserting a cause that is false

`self.cache` is falsy-checked at three sites. All three are **dead code today** — `FullTextCache` defines no `__bool__` or `__len__`, so it is always truthy, and `self.cache` was never `None`. Task 2 makes them live, and one of them then reports a cause that is wrong.

**Files:**
- Modify: `bmlib/fulltext/service.py` — lines 329, 754 and 775-782.
- Test: `tests/test_fulltext_service.py` (append to the class from Task 2)

**Interfaces:**
- Consumes: everything from Task 2.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Append to `TestAnUncreatableCacheDirectoryDoesNotAbortConstruction`:

```python
    @staticmethod
    def _unpaywall_pdf_only() -> list[MagicMock]:
        """Europe PMC finds nothing; Unpaywall offers a free PDF."""
        search = MagicMock()
        search.status_code = 200
        search.json.return_value = {"resultList": {"result": []}}

        idconv = _idconv_miss()

        unpaywall = MagicMock()
        unpaywall.status_code = 200
        unpaywall.json.return_value = {
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        }
        return [search, idconv, unpaywall]

    def test_a_pdf_left_as_a_url_does_not_blame_a_missing_identifier(
        self, tmp_path, monkeypatch, caplog
    ):
        """The one dead branch that starts lying once it is reachable.

        ``if not cache_id or not self.cache:`` logged "no identifier was
        given" for both. With a failed cache and an identifier in hand that
        is simply false, and it is the only line the operator gets about why
        a PDF they asked to have extracted was left as a URL.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com", convert_pdfs=True)

        with (
            caplog.at_level("DEBUG"),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            result = service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

        assert result.pdf_url == "https://example.com/paper.pdf"
        assert "no identifier was given" not in caplog.text
        assert "no cache" in caplog.text.lower()

    def test_a_genuinely_missing_identifier_still_says_so(self, tmp_path, caplog):
        """Negative control: the message above is suppressed, not deleted.

        Without this, deleting the "no identifier was given" line outright
        would pass the test above while losing a real diagnostic.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(
            email="test@example.com", cache=cache, convert_pdfs=True
        )

        with (
            caplog.at_level("INFO"),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.pdf_url == "https://example.com/paper.pdf"
        assert "no identifier was given" in caplog.text
```

Two notes on the fixtures above. `_idconv_miss` is a module-level helper already defined at the top of this test file. And the three-response side-effect list is complete on purpose: `TestCacheIntegration.test_pdf_downloaded_and_cached` needs a fourth (the PDF download itself), but both tests here return from `_download_and_cache_pdf` before it is issued — the degraded one because there is no cache, the control because there is no `cache_id`.

- [ ] **Step 2: Run them to verify the first fails**

```bash
uv run pytest tests/test_fulltext_service.py::TestAnUncreatableCacheDirectoryDoesNotAbortConstruction -v -k "pdf_left_as_a_url or genuinely_missing"
```

Expected: **1 failed, 1 passed.** The failure is `assert "no identifier was given" not in caplog.text` — the false statement, printed. The negative control passes, which is what makes the failure meaningful.

- [ ] **Step 3: Split the condition**

In `bmlib/fulltext/service.py`, replace lines 775-782:

```python
        if not cache_id or not self.cache:
            if self.convert_pdfs:
                logger.info(
                    "convert_pdfs is on but no identifier was given — a PDF is only "
                    "extracted once cached, so %s is left as a URL",
                    pdf_url,
                )
            return
```

with:

```python
        if self.cache is None:
            # Reachable only when the default cache could not be created,
            # which already warned once at construction (#75). Repeating it
            # per article would put one line per paper into a bulk run's log
            # to say what the operator has already been told, so this stays
            # at DEBUG — and it must not borrow the message below, which
            # would assert a cause that is false.
            if self.convert_pdfs:
                logger.debug(
                    "convert_pdfs is on but no cache is configured — a PDF is only "
                    "extracted once cached, so %s is left as a URL",
                    pdf_url,
                )
            return
        if not cache_id:
            if self.convert_pdfs:
                logger.info(
                    "convert_pdfs is on but no identifier was given — a PDF is only "
                    "extracted once cached, so %s is left as a URL",
                    pdf_url,
                )
            return
```

- [ ] **Step 4: Make the other two checks explicit**

Line 329 — `if cache_id and self.cache:` → `if cache_id and self.cache is not None:`

Line 754 (in `_cache_html`) — `if cache_id and self.cache:` → `if cache_id and self.cache is not None:`

These say what they mean now that the branch is live: "there is no cache", not "the cache is falsy". Behaviour is unchanged — `FullTextCache` has no `__bool__` — so no test moves.

- [ ] **Step 5: Run the class, then the whole suite**

```bash
uv run pytest tests/test_fulltext_service.py::TestAnUncreatableCacheDirectoryDoesNotAbortConstruction -v
uv run pytest tests/ -q
```

Expected: 9 passed in the class; **1769 passed, 58 skipped** overall (1758 baseline + 2 from Task 1 + 5 + 2 + 2).

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "fix(fulltext): a PDF left as a URL reports the real reason (#75)"
```

---

### Task 5: Verify every guard by mutation

The repo's habit, and the reason several of #70/#71's tests exist in the shape they do: a test that cannot fail is not a guard. Each mutation below must turn a **named** test red.

**Files:** none committed — every edit here is reverted.

**Interfaces:** consumes the code from Tasks 2 and 4.

> **Stale bytecode warning:** after restoring each mutation, run
> `find . -name __pycache__ -type d -prune -exec rm -rf {} +`. A same-length
> edit can otherwise leave pytest running the previous bytecode and report a
> mutation as caught when it was never executed.

- [ ] **Step 1: Narrow the guard to `OSError`**

Change `except (OSError, RuntimeError) as exc:` to `except OSError as exc:`.

```bash
uv run pytest tests/test_fulltext_service.py -q -k "home_directory_that_cannot_be_determined"
```

Expected: **FAILS** with `RuntimeError: Could not determine home directory.` If it passes, the `RuntimeError` half is untested and the widening was unjustified — stop and fix the test.

Restore, then clear `__pycache__`.

- [ ] **Step 2: Remove the guard entirely**

Change `_default_cache`'s body to a bare `return FullTextCache()`.

```bash
uv run pytest tests/test_fulltext_service.py -q -k "AnUncreatableCacheDirectory"
```

Expected: **multiple FAILS** with `FileExistsError` / `RuntimeError` out of the constructor.

Restore, then clear `__pycache__`.

- [ ] **Step 3: Make the guard return a cache instead of `None`**

Change `return None` in the `except` branch to `return FullTextCache(cache_dir=Path("/tmp/bmlib-mutation"))`.

```bash
uv run pytest tests/test_fulltext_service.py -q -k "AnUncreatableCacheDirectory"
```

Expected: **FAILS** on `assert service.cache is None`. This is what pins the non-goal "no fallback location" — a future session adding one has to delete a test to do it.

Restore, then clear `__pycache__`. Also `rm -rf /tmp/bmlib-mutation`.

- [ ] **Step 4: Drop the exception type from the warning**

Remove `type(exc).__name__,` from the `logger.warning` call and its `%s: ` from the format string.

```bash
uv run pytest tests/test_fulltext_service.py -q -k "warning_names_what_was_raised"
```

Expected: **FAILS** on `assert "FileExistsError" in caplog.text`.

Restore, then clear `__pycache__`.

- [ ] **Step 5: Restore the merged PDF condition**

Put lines 775-782 back to the single `if not cache_id or not self.cache:` with the "no identifier was given" message.

```bash
uv run pytest tests/test_fulltext_service.py -q -k "pdf_left_as_a_url"
```

Expected: **FAILS** on `assert "no identifier was given" not in caplog.text`.

Restore, then clear `__pycache__`.

- [ ] **Step 6: Confirm the tree is clean and green**

```bash
git status --short          # must be empty
uv run pytest tests/ -q     # 1769 passed, 58 skipped
```

Nothing to commit. If `git status` is not empty, a mutation was not fully restored — restore it before going on.

---

### Task 6: Documentation

**Files:**
- Modify: `bmlib/fulltext/service.py` — the `cache:` argument in `__init__`'s docstring.
- Modify: `docs/manual/fulltext.md:194` and the surrounding cache section.
- Modify: `CHANGELOG.md` — under `[Unreleased]` → `### Fixed`.
- Modify: `docs/DECISIONS.md` — a new entry under the `fulltext` heading.
- Modify: `ROADMAP.md` — the #75 row.
- Modify: `HANDOVER.md` — the open-issue list and the header count.

**Interfaces:** consumes the finished behaviour from Tasks 2 and 4.

- [ ] **Step 1: The constructor docstring**

In `FullTextService.__init__`, replace the `cache:` line:

```python
            cache: Disk cache to use. A default one is created when omitted.
```

with:

```python
            cache: Disk cache to use. A default one is created when omitted;
                if that directory cannot be created — a file standing where it
                should be, a read-only parent, no determinable home directory —
                the service warns once and runs without a cache rather than
                failing to construct, since retrieval does not need one. A
                cache passed here is used as given, and one constructed
                directly still raises. See :func:`_default_cache`.
```

Leave the `Raises:` section alone: it lists `ImportError` only, and this change is what makes that *true* — before it, the constructor could also raise `OSError` undocumented.

- [ ] **Step 2: The manual**

In `docs/manual/fulltext.md`, the `cache` row of the constructor table currently reads:

```
| `cache` | `FullTextCache \| None` | Cache instance. When `None`, a default `FullTextCache()` is constructed, so a cache always exists |
```

Replace with:

```
| `cache` | `FullTextCache \| None` | Cache instance. When `None`, a default `FullTextCache()` is constructed — and if that directory cannot be created, the service warns once and runs uncached rather than raising, so `service.cache` may be `None` |
```

Then, in the worked example near line 989, replace:

```python
    cache=None,          # a default FullTextCache() is created
```

with:

```python
    cache=None,          # a default FullTextCache() is created, or None if it cannot be
```

Then read the `## Caching` section in full (not a grep — the claim may be phrased differently) and correct any other statement that a cache always exists. Add a short paragraph there recording the asymmetry: the service degrades, a directly constructed `FullTextCache` still raises.

- [ ] **Step 3: CHANGELOG**

Add to `CHANGELOG.md` under `## [Unreleased]` → `### Fixed`, after the #71 entry:

```markdown
- **A cache directory that cannot be created no longer aborts construction**
  (#75, found reviewing PR #74). `FullTextCache.__init__`'s three `mkdir`
  calls were unguarded and ran inside `FullTextService.__init__` whenever no
  cache was passed, so a file standing where the cache directory should be —
  or a read-only parent, or a full disk — took down a run that had every
  chance of succeeding without a cache. It was the last place in `fulltext/`
  where the cache was not best-effort: a failed write already warned once
  (#67) and a failed read already fell through to the network (#71). The
  default construction now warns once, naming what was raised, and leaves
  `service.cache` as `None`; retrieval proceeds and caches nothing. A
  `FullTextCache` constructed *directly* still raises — that caller asked for
  a cache specifically, and degrading would return an object whose every
  method then failed one at a time instead of failing once at construction.
  The guard catches `RuntimeError` as well as `OSError`, because
  `_default_cache_dir()` runs before any `mkdir` and calls `Path.home()`,
  which raises the former when there is no `HOME` and no passwd entry — so
  `except OSError` would have fixed the reported shape and left the same
  defect one layer up. `FullTextService.__init__`'s `Raises:` section, which
  documented only `ImportError`, becomes accurate rather than needing a new
  entry. One log line changes: `_download_and_cache_pdf`'s `self.cache` check
  was dead code (`FullTextCache` is always truthy and `self.cache` could not
  be `None`), and reaching it now would have printed "no identifier was given"
  when an identifier had been given, so the two conditions are split and the
  no-cache one logs at `DEBUG` — the construction warning has already said it.
```

- [ ] **Step 4: DECISIONS.md**

Append to the `fulltext` section:

```markdown
## fulltext — the service degrades but the cache still raises (#75)

`FullTextService` survives a cache directory it cannot create;
`FullTextCache(cache_dir=...)` constructed directly still raises. **This
asymmetry is deliberate — do not "make it consistent".** A caller who
constructs a cache asked for one specifically, and returning an object whose
every method then fails one at a time is worse than failing once, clearly, at
construction. Pinned by
`test_fulltext_cache.py::TestADirectlyConstructedCacheStillRaises`, which is
the only thing standing between the decision and a silent tidy-up.

Three further choices, each with a named test:

- **The guard catches `RuntimeError` as well as `OSError`.** Not defensive
  padding: `_default_cache_dir()` runs before any `mkdir` and calls
  `Path.home()`, which raises `RuntimeError` when there is no `HOME` and no
  passwd entry. Narrowing to `OSError` fixes the shape #75 was reported in and
  leaves the identical defect one layer up.
- **It does not catch `Exception`.** Inside that one constructor
  `RuntimeError` has exactly one source, so the pair stays narrow enough that
  a bmlib bug still surfaces as one.
- **No fallback cache location, and no writability probe.** Relocating to a
  temp directory surprises a caller who set `cache_dir` deliberately, and a
  cache that vanishes on reboot looks like one that never hits. A directory
  that exists but is read-only passes `mkdir(exist_ok=True)` and is already
  #67's warn-once on the first failed write; probing would be TOCTOU and would
  litter the operator's cache directory.
```

- [ ] **Step 5: ROADMAP and HANDOVER**

In `ROADMAP.md`, replace the `⬜ Planned` row for #75 (under **Full text (`bmlib.fulltext`)**) with:

```markdown
| ✅ Done | A cache directory that cannot be created does not abort construction | Issue #75, found reviewing PR #74 — `FullTextCache.__init__`'s three `mkdir` calls were unguarded and ran inside `FullTextService.__init__` whenever no cache was passed, so a file standing where the cache directory should be raised `FileExistsError` out of the constructor. The last place in `fulltext/` where the cache was not best-effort, next to a failed write that warns once (#67) and a failed read that falls through to the network (#71). The *default* construction now warns once naming what was raised and leaves `service.cache` as `None`; retrieval proceeds and caches nothing. A `FullTextCache` constructed **directly** still raises — that caller asked for a cache specifically, and degrading would return an object whose every method then failed one at a time instead of failing once at construction; the asymmetry is pinned by its own test, since nothing else would notice it being "tidied" away. The guard catches `RuntimeError` as well as `OSError`: `_default_cache_dir()` runs before any `mkdir` and calls `Path.home()`, which raises the former when there is no `HOME` and no passwd entry, so `except OSError` would have fixed the reported shape and left the identical defect one layer up. Not `except Exception` — inside that one constructor `RuntimeError` has exactly one source. No fallback location and no writability probe: relocating to a temp directory surprises a caller who set `cache_dir`, and a directory that exists but is read-only passes `mkdir(exist_ok=True)` and is already #67's territory. `__init__`'s `Raises:` section, which listed only `ImportError`, becomes accurate rather than needing a new entry. One log line moves: `_download_and_cache_pdf`'s `self.cache` check was dead code — `FullTextCache` is always truthy and `self.cache` could not be `None` — and once reachable it printed "no identifier was given" when one had been given, so the two conditions are split and the no-cache branch logs at `DEBUG`, the construction warning having already said it (unreleased) |
```

In `HANDOVER.md`: drop #75 from the "Open GitHub issues" list, change "Five open issues" to "Four" in both the header paragraph and that section's opening line, and add #75 to the `[Unreleased]` inventory in "Worth doing" — it is a fifth fix for 0.8.1, and its API note is that `FullTextService.cache` may now be `None`.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add -A
git commit -m "docs: record #75 — the cache degrades, the service does not abort"
```

---

## Done criteria

- `uv run pytest tests/ -v` → **1769 passed, 58 skipped**, no failures.
- `uvx ruff@0.15.20 check .` and `uvx ruff@0.15.20 format --check .` both clean.
- All five mutations in Task 5 confirmed to turn a named test red, tree restored.
- `FullTextService(email=...)` constructs on a machine where the cache directory cannot be created, warns once naming the exception type, and retrieves full text.
- `FullTextCache(cache_dir=<a file>)` still raises `OSError`.
- CHANGELOG, DECISIONS, manual, ROADMAP and HANDOVER all updated.
- PR opened against `main`, linked to #75.
