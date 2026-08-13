# Junk PDF Metadata Titles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `SectionSegmenter._extract_title()` and `PyMuPDFConverter.convert()` believing junk PDF metadata titles, using a rule measured against a live-sampled corpus rather than a guessed reject-list.

**Architecture:** A metadata title is accepted only if the document's own first page corroborates it — normalise both sides to lowercase alphanumeric tokens and test containment. A reject-list survives only as a backstop, and each member must earn its place from the corpus. A new live sampler builds that corpus from Europe PMC (publisher-typeset, clean metadata — measures wrong rejections) and bioRxiv/medRxiv (author-submitted from Word/LaTeX — where the junk lives), self-labelling each row against the record title the API already states.

**Tech Stack:** Python 3.11+, pytest, PyMuPDF (`bmlib[pdf]`), httpx (live runner only), ruff 0.15.20.

**Spec:** [`docs/superpowers/specs/2026-08-13-pdf-metadata-title-corroboration-design.md`](../specs/2026-08-13-pdf-metadata-title-corroboration-design.md)

## Global Constraints

- AGPL-3 header on every new source file — copy verbatim from any existing file.
- `from __future__ import annotations` at the top of every module; lowercase builtin generics (`list[str]`, not `List[str]`).
- Type hints and docstrings on every public function, class and module (Google style in `fulltext/`).
- `uv` only, never bare pip. Tests: `uv run pytest tests/ -v`.
- Lint with the **CI-pinned** ruff, not `.venv`'s: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .` — line length 100, rules E, F, I, N, W, UP.
- No network in the pytest suite. Live runners live in `scripts/` and are covered offline through stubbed clients.
- Optional dependencies stay guarded at the call site; `bmlib/fulltext/_titles.py` must be **stdlib-only** (`re`, `unicodedata`), because `fulltext/` must import on a core install (issue #64).
- Ship rule, fixed before any number is seen (spec §7): corroboration must wrongly reject ≤1% of `match` rows and must reject ≥80% of `unrelated` rows; each backstop member ships only on ≥1 `unrelated` row it catches that corroboration accepted **and** 0 `match` rows it rejects.
- Branch: `fix/56-junk-pdf-metadata-titles`. Commit at the end of every task.

---

### Task 1: Extract the shared sampler helpers

The new sampler needs the per-host pacer, the clamped `Retry-After` handling and `wilson()`, all of which live in `scripts/sample_free_pdf_urls.py` today. The clamp rule was learned from that script's own first live run measuring its own throttling; it must not exist in two copies that can drift.

**Files:**
- Create: `scripts/_sampling.py`
- Modify: `scripts/sample_free_pdf_urls.py` (delete the moved definitions, import them instead)
- Create: `tests/test_sampling_helpers.py`
- Modify: `tests/test_free_pdf_sampler.py` (sys.path insert; move two test classes out)

**Interfaces:**
- Produces: `scripts/_sampling.py` exporting `_sleep_for(seconds: float) -> None`, `_retry_after_seconds(resp: Any) -> int | None`, `_throttle_delay(resp: Any, attempt: int) -> float`, `_make_pacer(interval: float, clock: Callable[[], float] = time.monotonic) -> Callable[[str], None]`, `wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]`, and the constants `MAX_PROBE_ATTEMPTS = 3`, `RETRY_BACKOFF_SECONDS = (2.0, 4.0)`, `MAX_RETRY_AFTER_SECONDS = 60.0`, `UNMEASURED_SHARE_ERROR_THRESHOLD = 0.20`.

- [ ] **Step 1: Create `scripts/_sampling.py`**

AGPL header, then this module docstring and the five functions **moved verbatim** from `scripts/sample_free_pdf_urls.py` (lines 336–384 for `_sleep_for`/`_retry_after_seconds`/`_throttle_delay`, 431–450 for `wilson`, 523–562 for `_make_pacer`), together with the four constants they read. Do not reword the docstrings — they carry the reasons, and a reworded reason is a lost one.

```python
"""Sampling helpers shared by the live runners in this directory.

The scripts in ``scripts/`` measure real API populations to settle questions
bmlib's code then encodes — a log level, an allow-list, a heuristic. Three
concerns recur in every one of them and are answered here once:

* **Pacing** per host, not globally (:func:`_make_pacer`).
* **Throttling** honoured through a ``Retry-After`` clamped at *both* ends
  (:func:`_throttle_delay`).
* **Intervals** rather than point estimates, wherever a rule has a threshold
  in it (:func:`wilson`).

Each rule was learned the expensive way — ``sample_free_pdf_urls.py``'s first
live run sampled one host 300 times in 300 seconds and measured its own
throttling rather than the population it was aiming at. They live here so that
a second sampler inherits the lesson instead of re-learning it.

Not a package: ``scripts/`` has no ``__init__.py``, and these modules are run
as ``uv run python scripts/<name>.py``, which puts this directory on
``sys.path`` as ``sys.path[0]``. The test files that load a sampler by path
insert it explicitly.
"""
```

- [ ] **Step 2: Import them in `sample_free_pdf_urls.py`**

Delete the moved definitions and constants, and add after the `bmlib` imports:

```python
from _sampling import (
    MAX_PROBE_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    RETRY_BACKOFF_SECONDS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _make_pacer,
    _retry_after_seconds,
    _sleep_for,
    _throttle_delay,
    wilson,
)
```

Keep every name importable from the sampler module: `tests/test_free_pdf_sampler.py` reaches `sampler.wilson`, `sampler._throttle_delay`, `sampler.MAX_RETRY_AFTER_SECONDS` and patches `sampler._sleep_for`, and a `from … import` binding keeps all of those working. Remove the now-unused `math` import if nothing else uses it; leave `time` if `time.monotonic`/`time.sleep` is still referenced.

**The one behaviour change to know about:** `_make_pacer` now calls `_sampling._sleep_for`, so `monkeypatch.setattr(sampler, "_sleep_for", …)` no longer reaches it. That is why the pacer tests move in Step 4. `probe()` and `_resolve_one_doi()` still call the name bound in the sampler's own namespace, so the tests that patch them keep working untouched.

- [ ] **Step 3: Add the sys.path insert to `tests/test_free_pdf_sampler.py`**

Before `_spec.loader.exec_module(sampler)`, and after the existing `sys.modules[_spec.name] = sampler` line:

```python
# The sampler does `from _sampling import …`, and `scripts/` is not a package.
# Running the script puts that directory on sys.path as sys.path[0]; loading it
# by path here does not, so insert it explicitly.
if str(_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_PATH.parent))
```

- [ ] **Step 4: Create `tests/test_sampling_helpers.py` and move two classes into it**

AGPL header, a module docstring, the same importlib-by-path loader pointing at `scripts/_sampling.py` (module name `bmlib_sampling_helpers`), then **move** — cut, do not copy — `TestTheIntervalIsComputedOverAttemptsActuallyMade` and `TestThePacerSpacesRequestsPerHostNotGlobally` out of `tests/test_free_pdf_sampler.py`, rewriting `sampler.` to `helpers.` throughout, including in the `monkeypatch.setattr(helpers, "_sleep_for", …)` calls.

```python
"""Tests for ``scripts/_sampling.py``.

The pacing and throttling rules these cover were learned from a live run that
measured its own throttling instead of the population it was aiming at. They
moved out of ``tests/test_free_pdf_sampler.py`` with the code they pin, so
that a second sampler inherits both.
"""
```

Leave `TestARetryAfterIsClampedAtBothEnds` where it is: one of its two tests drives `probe()`, which stays in the sampler, and `sampler._throttle_delay` still resolves through the import binding.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_free_pdf_sampler.py tests/test_sampling_helpers.py tests/test_databank_sampler.py -v`
Expected: PASS, with the same total count as before the move (nothing was deleted, only relocated).

Then the whole suite: `uv run pytest tests/ -q` → 1893 passed, 58 skipped.

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/_sampling.py scripts/sample_free_pdf_urls.py tests/test_sampling_helpers.py tests/test_free_pdf_sampler.py
git commit -m "refactor(scripts): share the pacing and throttling helpers between samplers"
```

---

### Task 2: The metadata-title sampler

A live runner that builds the corpus. It collects and labels; it does **not** implement the acceptance rule — that arrives in Task 4 and is evaluated by the metric test in Task 7. Keeping the rule out of the instrument is what stops the instrument confirming it.

**Files:**
- Create: `scripts/sample_pdf_metadata_titles.py`
- Create: `tests/test_pdf_title_sampler.py`

**Interfaces:**
- Consumes: `scripts/_sampling.py` (Task 1); `bmlib.fulltext.service._extract_free_pdf_url`, `bmlib.fulltext.pdf_converter.PyMuPDFConverter`, `bmlib.fulltext.segmenter._median_font_size`, `bmlib.publications.fetchers.biorxiv.fetch_biorxiv`.
- Produces: `classify(metadata_title: str, record_title: str) -> str` returning `"absent" | "match" | "truncated" | "unrelated"`; `row_from_pdf(pdf_bytes: bytes, source: str, identifier: str, record_title: str, tmpdir: Path) -> dict[str, Any] | None`; `download(client, url, pace) -> tuple[bytes | None, bool]`; `summarise(source: str, rows: list[dict], unmeasured: int) -> list[str]`; and the fixture `tests/data/pdf_metadata_titles.json`.

- [ ] **Step 1: Write the failing bucket tests**

`tests/test_pdf_title_sampler.py`, after the AGPL header, this docstring and the by-path loader (module name `bmlib_pdf_title_sampler`, with the same `sys.path` insert as Task 1 Step 3):

```python
"""Tests for ``scripts/sample_pdf_metadata_titles.py``.

The script is a live runner — it downloads real PDFs from Europe PMC and
bioRxiv — but the corpus it writes is the evidence for issue #56's acceptance
rule, so the labelling has to be trustworthy offline. Two properties are
pinned here.

**A PDF that could not be sampled is never a finding.** A 429 surviving its
retries, a transport exception, a non-200 or a file PyMuPDF cannot open is
*unmeasured*: excluded from every denominator, and reported as ERROR rather
than as a rate once it eats more than a fifth of a population. A zero junk
rate is what a healthy population looks like, and a dead host must not read as
one — the rule ``sample_free_pdf_urls.py`` already lives by.

**The buckets label against ground truth, not against the rule under test.**
The sampler never runs the acceptance rule; if it did, the corpus could only
ever confirm whatever the rule already believed.
"""
```

Then:

```python
class TestTheBucketsLabelAgainstTheRecordTitle:
    def test_an_exact_title_is_a_match(self) -> None:
        assert sampler.classify("Effects of aspirin on outcomes", "Effects of aspirin on outcomes") == "match"

    def test_case_and_punctuation_drift_is_still_a_match(self) -> None:
        """Metadata routinely drops the terminal period and re-cases."""
        assert sampler.classify("EFFECTS OF ASPIRIN ON OUTCOMES.", "Effects of aspirin on outcomes") == "match"

    def test_a_prefix_of_the_record_title_is_truncated_not_junk(self) -> None:
        assert sampler.classify("Effects of aspirin", "Effects of aspirin on outcomes") == "truncated"

    def test_a_word_processor_filename_is_unrelated(self) -> None:
        assert sampler.classify("Microsoft Word - manuscript.docx", "Effects of aspirin on outcomes") == "unrelated"

    def test_a_blank_metadata_title_is_absent_not_unrelated(self) -> None:
        """The falsy case already falls through to the font heuristic; it is
        not what #56 is about, and counting it as junk would inflate the rate."""
        assert sampler.classify("   ", "Effects of aspirin on outcomes") == "absent"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_pdf_title_sampler.py -v`
Expected: FAIL — the script does not exist, so the loader raises at import.

- [ ] **Step 3: Create the script with the bucket logic**

`scripts/sample_pdf_metadata_titles.py` — AGPL header, then a module docstring following `sample_free_pdf_urls.py`'s shape (what it measures, why two sources, what the buckets mean, usage, environment, exit codes), then:

```python
DEFAULT_TARGET = 150
DEFAULT_OUTPUT = Path("tests/data/pdf_metadata_titles.json")
# Enough of page 1 to carry a title, its authors and their affiliations —
# which is where the decision is made. Capped so the committed fixture stays
# a few hundred KB and carries no article prose.
PAGE_ONE_LINES_KEPT = 20
MAX_LINE_CHARS = 200
# A PDF larger than this is not downloaded: the corpus needs 300 title pages,
# not a supplement bundle, and one 400MB file can stall a paced run.
MAX_PDF_BYTES = 30 * 1024 * 1024

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens of *text*, diacritics folded away.

    The sampler's own normaliser, deliberately **not** imported from
    ``bmlib.fulltext._titles``: the buckets are ground truth, and labelling
    the corpus with the rule under test would let the corpus only ever
    confirm the rule.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _TOKEN_RE.findall(stripped.lower())


def classify(metadata_title: str, record_title: str) -> str:
    """Label one row against the title the source's own record states.

    Returns:
        ``"absent"`` when the PDF carries no metadata title — the falsy case
        that already falls through to the font heuristic; ``"match"`` when it
        agrees with the record title token for token; ``"truncated"`` when it
        is a strictly shorter prefix of it; ``"unrelated"`` otherwise, which
        is the junk issue #56 is about.
    """
    meta = _tokens(metadata_title)
    record = _tokens(record_title)
    if not meta:
        return "absent"
    if meta == record:
        return "match"
    if len(meta) < len(record) and record[: len(meta)] == meta:
        return "truncated"
    return "unrelated"
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_pdf_title_sampler.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Write the failing unmeasured-accounting tests**

```python
class TestAPDFThatCouldNotBeSampledIsNeverAFinding:
    def test_a_persistent_429_is_unmeasured_not_a_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _Client({"https://e/a.pdf": _Resp(429)})
        body, measured = sampler.download(client, "https://e/a.pdf", lambda url: None)
        assert body is None
        assert measured is False

    def test_a_429_that_clears_on_retry_is_measured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _SequencedClient([_Resp(429), _Resp(200, b"%PDF-1.7 body")])
        body, measured = sampler.download(client, "https://e/a.pdf", lambda url: None)
        assert body == b"%PDF-1.7 body"
        assert measured is True

    def test_a_transport_exception_is_unmeasured(self) -> None:
        """Unlike sample_free_pdf_urls.py, a failed download here is not a
        finding: this script measures *titles*, and a PDF it never got is a
        row it cannot label, not a bad title."""
        client = _Client({"https://e/a.pdf": OSError("boom")})
        body, measured = sampler.download(client, "https://e/a.pdf", lambda url: None)
        assert (body, measured) == (None, False)

    def test_a_body_that_is_not_a_pdf_is_unmeasured(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(200, b"<!DOCTYPE html>")})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (None, False)


class TestAnUnmeasuredPopulationPrintsErrorNotAZero:
    def test_a_fifth_unmeasured_still_reports(self) -> None:
        rows = [{"bucket": "match"}] * 8
        lines = sampler.summarise("europepmc", rows, unmeasured=2)
        assert not any("ERROR" in line for line in lines)

    def test_more_than_a_fifth_unmeasured_is_an_error_with_no_rate(self) -> None:
        rows = [{"bucket": "match"}] * 7
        lines = sampler.summarise("europepmc", rows, unmeasured=3)
        assert any("ERROR" in line for line in lines)
        assert "%" not in "\n".join(lines)

    def test_a_population_with_no_rows_is_an_error(self) -> None:
        assert any("ERROR" in line for line in sampler.summarise("biorxiv", [], unmeasured=0))
```

Copy `_Resp`, `_Client` and `_SequencedClient` from `tests/test_free_pdf_sampler.py` (lines 69–126) into this file — they are three tiny test doubles, and a shared conftest fixture for them would couple two independent sampler suites.

- [ ] **Step 6: Run to verify they fail, then implement `download` and `summarise`**

Run: `uv run pytest tests/test_pdf_title_sampler.py -v` → FAIL with `AttributeError: module has no attribute 'download'`.

```python
def download(client: Any, url: str, pace: Callable[[str], None]) -> tuple[bytes | None, bool]:
    """Fetch one PDF, retrying a 429/503 before giving up on it.

    Returns:
        ``(body, measured)``. ``measured`` is ``False`` whenever the row
        cannot be labelled — a throttled request, a non-200, a transport
        exception, an oversized body, or bytes that are not a PDF. This
        script measures titles, so anything short of a readable PDF is a
        question never asked, not an answer.
    """
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        pace(url)
        try:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})
        except Exception as exc:
            print(f"  download failed for {url}: {exc}", file=sys.stderr)
            return None, False
        if resp.status_code in (429, 503):
            if attempt == MAX_PROBE_ATTEMPTS:
                print(f"  throttled for {url}; unmeasured", file=sys.stderr)
                return None, False
            _sleep_for(_throttle_delay(resp, attempt))
            continue
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} for {url}", file=sys.stderr)
            return None, False
        body = resp.content
        if len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF"):
            return None, False
        return body, True
    raise AssertionError("unreachable: the loop above always returns")  # pragma: no cover
```

`summarise(source, rows, unmeasured)` prints the bucket distribution — count and share per bucket over `len(rows)` — and, when `unmeasured / (len(rows) + unmeasured) > UNMEASURED_SHARE_ERROR_THRESHOLD` or `not rows`, prints a single `ERROR` line naming the source and **no percentages at all**. Mirror `sample_free_pdf_urls.py:summarise`'s shape so the two tables read alike.

Run: `uv run pytest tests/test_pdf_title_sampler.py -v` → PASS.

- [ ] **Step 7: Write `row_from_pdf` and its test**

```python
class TestTheFixtureRowCarriesWhatTheMetricTestNeeds:
    def test_a_row_carries_the_document_median_not_the_title_pages(self, tmp_path: Path) -> None:
        """The metric test re-runs _extract_title, whose _TITLE_SIZE_RATIO
        compares against the *document's* median. Recomputing one from 20
        stored title-page lines would compare a heading against headings."""
        row = sampler.row_from_pdf(_PDF_BYTES, "europepmc", "PMC1", "A trial", tmp_path)
        assert row is not None
        assert row["median_font_size"] == pytest.approx(_BODY_FONT_SIZE)

    def test_page_one_lines_are_capped_and_in_reading_order(self, tmp_path: Path) -> None:
        row = sampler.row_from_pdf(_PDF_BYTES, "europepmc", "PMC1", "A trial", tmp_path)
        assert len(row["page_one_lines"]) <= sampler.PAGE_ONE_LINES_KEPT
        ys = [line["y"] for line in row["page_one_lines"]]
        assert ys == sorted(ys)

    def test_a_pdf_pymupdf_cannot_read_is_unmeasured_not_an_empty_row(self, tmp_path: Path) -> None:
        assert sampler.row_from_pdf(b"%PDF-1.7 truncated", "europepmc", "PMC1", "A trial", tmp_path) is None
```

Build `_PDF_BYTES` in the test module with PyMuPDF itself (`fitz.open()`, `page.insert_text(...)` at two font sizes, `doc.tobytes()`), skipping the class with `pytest.importorskip("fitz")` so the suite still passes where the `pdf` extra is absent — `tests/test_pdf_converter.py` already establishes that pattern; follow whatever it does rather than inventing a second one.

Implementation: write `pdf_bytes` to `tmpdir/"sample.pdf"`, run `PyMuPDFConverter().convert(path)` for `metadata` and `extract_blocks(path)` for the blocks, return `None` if either fails or `result.success` is `False`, and assemble the row exactly as the spec's §6 JSON shows — including `"bucket": classify(metadata.get("title", ""), record_title)`, `"file_name"` from the URL's last path segment, `median_font_size` from `bmlib.fulltext.segmenter._median_font_size(blocks)` over **all** blocks, and `page_one_lines` from the first `PAGE_ONE_LINES_KEPT` blocks with `page_num == 0` in list order, each `text` truncated to `MAX_LINE_CHARS`. Delete the temp file in a `finally`.

`row_from_pdf` must **not** read `result.title` — that field arrives in Task 6, and a corpus labelled by the rule under test can only ever confirm it.

Run: `uv run pytest tests/test_pdf_title_sampler.py -v` → PASS.

- [ ] **Step 8: Add the two source walks and `main()`**

`sample_europepmc_rows(client, target, pace)` — the same search as `sample_free_pdf_urls.py:sample_europepmc` (`query="(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])"`, `resultType=core`, `cursorMark` paging), taking `_extract_free_pdf_url(hit)` for the URL and `hit.get("title", "")` for ground truth, `hit.get("pmcid") or hit.get("id")` for the id. `sample_biorxiv_rows(client, target, pace, server)` — `fetch_biorxiv` walking back a day at a time from 30 days ago as `sample_biorxiv` does, taking `record.title` and the first `fulltext_sources` entry with `format == "pdf"`.

Both return `(rows, unmeasured)`. Both stop at `target` **rows**, not target URLs — a run that downloads 150 files and can label 90 has not sampled 150.

`main()` parses `--target`, `--per-host-interval`, `--source`, `-o/--output`; builds the pacer; runs the requested sources; writes the rows sorted by `(source, id)` to the output path; prints each source's table plus, for the `unrelated` bucket, **every metadata title verbatim** — that listing is what the junk shapes are read off in Task 3, and a summary would hide the shape that matters. Exits non-zero if any source printed `ERROR`.

- [ ] **Step 9: Test the exit status agrees with what was printed**

```python
class TestTheExitStatusAgreesWithWhatWasPrinted:
    def test_an_errored_population_exits_non_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sampler, "sample_europepmc_rows", lambda *a, **k: ([], 40))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: ([{"bucket": "match"}] * 10, 0))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(tmp_path / "out.json")])
        assert sampler.main() != 0
```

Run: `uv run pytest tests/test_pdf_title_sampler.py -v` → PASS.

- [ ] **Step 10: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
uv run pytest tests/ -q
git add scripts/sample_pdf_metadata_titles.py tests/test_pdf_title_sampler.py
git commit -m "test(scripts): sample real PDF metadata titles against their record titles (#56)"
```

---

### Task 3: Run it live and read the corpus

**Files:**
- Create: `tests/data/pdf_metadata_titles.json`

- [ ] **Step 1: Run the sampler**

```bash
uv run python scripts/sample_pdf_metadata_titles.py --target 150 --per-host-interval 3.0
```

Roughly 300 downloads over ~20 minutes. Run it in the background and do not poll it faster than it can produce output.

- [ ] **Step 2: Check the run measured something**

Exit status 0, and neither source printed `ERROR`. If either did, the population was too throttled or too broken to trust: re-run that source alone with a larger `--per-host-interval` rather than reading the table anyway.

- [ ] **Step 3: Read the `unrelated` listing and write down the shapes**

Group the verbatim junk titles by shape. Expect the issue's candidates (`Microsoft Word - …`, a bare filename, `untitled`) and note any shape nobody predicted — that listing, not intuition, is what Task 4's backstop may draw from.

- [ ] **Step 4: Commit the fixture**

```bash
git add tests/data/pdf_metadata_titles.json
git commit -m "test(data): the measured PDF metadata title corpus (#56)"
```

Record in the commit message: rows per source, the bucket distribution, and the unmeasured count.

---

### Task 4: The corroboration rule

**Files:**
- Create: `bmlib/fulltext/_titles.py`
- Create: `tests/test_fulltext_titles.py`

**Interfaces:**
- Produces: `normalise(text: str) -> str`; `accepted_metadata_title(metadata: Mapping[str, Any], page_one_text: str) -> str | None`; `looks_like_junk(title: str, metadata: Mapping[str, Any]) -> bool`.

- [ ] **Step 1: Write the failing normaliser tests**

```python
class TestTheNormaliserAbsorbsTypesettingNotMeaning:
    def test_case_and_terminal_period_do_not_separate_a_title_from_itself(self) -> None:
        assert titles.normalise("Effects of Aspirin.") == titles.normalise("effects of aspirin")

    def test_an_en_dash_and_a_hyphen_normalise_alike(self) -> None:
        assert titles.normalise("dose–response") == titles.normalise("dose-response")

    def test_diacritics_fold(self) -> None:
        assert titles.normalise("thérapie") == titles.normalise("therapie")

    def test_a_title_wrapped_across_lines_joins_on_a_space(self) -> None:
        assert titles.normalise("Randomised\ncontrolled trial") == "randomised controlled trial"

    def test_a_hyphen_at_a_line_break_is_closed_up_not_spaced(self) -> None:
        """Typesetting hyphenation. Joining on a space instead leaves
        `con trolled`, the metadata title is then not contained in the page,
        and a perfectly good title is rejected with nothing explaining it."""
        assert titles.normalise("Randomised con-\ntrolled trial") == "randomised controlled trial"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_fulltext_titles.py -v`
Expected: FAIL — `ModuleNotFoundError: bmlib.fulltext._titles`.

- [ ] **Step 3: Implement the normaliser**

`bmlib/fulltext/_titles.py` — AGPL header, `from __future__ import annotations`, a module docstring naming the issue and the rule, then:

```python
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# A hyphen (or one of its Unicode spellings) immediately before a line break
# is typesetting, not spelling: the word continues on the next line.
_LINE_BREAK_HYPHEN_RE = re.compile(r"[-‐‑­]\s*\n\s*")


def normalise(text: str) -> str:
    """Reduce *text* to the form both sides of the corroboration test compare in.

    Line-break hyphenation is closed up first, then the text is decomposed to
    NFKD, its combining marks dropped, lowercased, and reduced to its
    ``[a-z0-9]+`` runs joined by single spaces. That absorbs the differences
    that separate a correct metadata title from its printed form — case, the
    terminal period metadata usually drops, en-dash versus hyphen, ligatures,
    diacritics and the line break a wrapped title carries — while keeping
    every difference that changes what the string says.
    """
    closed = _LINE_BREAK_HYPHEN_RE.sub("", text)
    decomposed = unicodedata.normalize("NFKD", closed)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_TOKEN_RE.findall(stripped.lower()))
```

Run: `uv run pytest tests/test_fulltext_titles.py -v` → PASS.

- [ ] **Step 4: Write the failing acceptance tests**

```python
_PAGE = "Effects of aspirin on outcomes\nJane Smith, John Doe\nAbstract\nWe studied…"


class TestAMetadataTitleIsBelievedOnlyWhereTheDocumentSaysIt:
    def test_a_title_printed_on_page_one_is_accepted(self) -> None:
        """The negative control. Without it, a rule that rejects everything
        passes every other test in this class."""
        assert titles.accepted_metadata_title(
            {"title": "Effects of aspirin on outcomes"}, _PAGE
        ) == "Effects of aspirin on outcomes"

    def test_a_word_processor_filename_is_rejected(self) -> None:
        assert titles.accepted_metadata_title({"title": "Microsoft Word - ms.docx"}, _PAGE) is None

    def test_a_blank_title_is_rejected_without_consulting_the_page(self) -> None:
        assert titles.accepted_metadata_title({"title": "   "}, _PAGE) is None

    def test_a_title_with_no_word_characters_is_rejected(self) -> None:
        """`normalise` reduces it to "", and "" is contained in every page."""
        assert titles.accepted_metadata_title({"title": "###"}, _PAGE) is None

    def test_a_page_with_no_text_accepts_the_metadata_title(self) -> None:
        """An image-only scan makes corroboration a test that cannot be run,
        not one that failed — the same distinction the samplers draw between
        an unmeasured probe and a failed one. Rejecting here would blank the
        title of every scanned paper, whose metadata is the only signal there
        is."""
        assert titles.accepted_metadata_title(
            {"title": "Effects of aspirin on outcomes"}, ""
        ) == "Effects of aspirin on outcomes"

    def test_junk_is_still_rejected_on_a_page_with_no_text(self) -> None:
        """The backstop does not depend on the page, so an unrunnable
        corroboration must not become a free pass for a known junk shape."""
        assert titles.accepted_metadata_title({"title": "untitled"}, "") is None
```

- [ ] **Step 5: Run to verify they fail, then implement**

Run: `uv run pytest tests/test_fulltext_titles.py -v` → FAIL, `AttributeError`.

```python
def accepted_metadata_title(metadata: Mapping[str, Any], page_one_text: str) -> str | None:
    """The PDF's own metadata title, where the document corroborates it.

    A junk metadata title — ``"Microsoft Word - manuscript.docx"``,
    ``"untitled"``, a typesetter's job number — has one property every shape
    of it shares, whether or not anyone sampled that shape: it is not printed
    in the document. A real title is. So the test is not "does this look like
    junk" but "does the document itself say this".

    Args:
        metadata: The converter's metadata dict; ``title`` is read, and
            :func:`looks_like_junk` may read its neighbours.
        page_one_text: Page 1's text, newline-separated. Empty when page 1
            carries none.

    Returns:
        The metadata title as given (stripped), or ``None`` when it is blank,
        a known junk shape, or absent from a page that had text to check
        against.
    """
    raw = metadata.get("title")
    title = str(raw).strip() if raw else ""
    if not title:
        return None
    if looks_like_junk(title, metadata):
        return None
    wanted = normalise(title)
    if not wanted:
        return None
    page = normalise(page_one_text)
    if not page:
        return title
    return title if wanted in page else None
```

Run: `uv run pytest tests/test_fulltext_titles.py -v` → PASS.

- [ ] **Step 6: Implement `looks_like_junk` from the corpus, one member at a time**

For each shape Task 3's listing showed, check it against the ship rule with a throwaway script over the fixture: does the candidate reject ≥1 `unrelated` row that corroboration accepted, and 0 `match` rows? Add only the members that pass, each with a comment naming the count it earned its place with, and a test named for the shape. **Do not add a member the corpus never showed**, however obvious it looks — that is the reject-list this design exists to avoid, and the design records the rejection.

If no member earns its place, ship `looks_like_junk` returning `False` with a comment recording that the corpus found nothing corroboration did not already catch, and say so in the CHANGELOG. That is a result, not a gap.

- [ ] **Step 7: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/fulltext/_titles.py tests/test_fulltext_titles.py
git commit -m "feat(fulltext): believe a PDF metadata title only where page 1 corroborates it (#56)"
```

---

### Task 5: Wire it into the segmenter

**Files:**
- Modify: `bmlib/fulltext/segmenter.py:392-410`
- Modify: `tests/test_segmenter.py`

- [ ] **Step 1: Write the failing tests**

Use the file's existing `block()` helper (`tests/test_segmenter.py:35`) and its
`BODY_SIZE` / `TITLE_SIZE` constants — do not introduce a second way to build
a `TextBlock` in the same file.

The title is **split across two lines** on purpose: it makes the two paths give
different answers, so each test says which path produced the result. With the
title on one line, a rule that rejected every metadata title would pass the
first test by accident.

```python
class TestAJunkMetadataTitleDoesNotBeatTheLargeFontLine:
    """Issue #56: a metadata title is believed only where page 1 prints it."""

    def _blocks(self) -> list[TextBlock]:
        return [
            block("Effects of aspirin", size=TITLE_SIZE, y=72.0),
            block("on outcomes", size=TITLE_SIZE, y=100.0),
            block("Jane Smith, John Doe", y=130.0),
            block("We studied aspirin in 400 adults.", y=150.0),
        ]

    def test_junk_falls_through_to_the_font_candidate(self):
        doc = SectionSegmenter().segment_document(
            self._blocks(), {"title": "Microsoft Word - ms.docx"}
        )
        assert doc.title == "Effects of aspirin"

    def test_a_corroborated_title_still_wins(self):
        """The negative control. The metadata title spans both title lines,
        so only the metadata path can return it — a rule that rejected
        everything would fail here and pass the test above."""
        doc = SectionSegmenter().segment_document(
            self._blocks(), {"title": "Effects of aspirin on outcomes"}
        )
        assert doc.title == "Effects of aspirin on outcomes"

    def test_junk_with_no_font_candidate_yields_no_title(self):
        """Rejection must not invent a title from an ordinary body line."""
        flat = [block("We studied aspirin in 400 adults.", y=72.0)]
        doc = SectionSegmenter().segment_document(flat, {"title": "untitled"})
        assert doc.title is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_segmenter.py -v -k Junk`
Expected: FAIL — `doc.title` is the junk string.

- [ ] **Step 3: Change `_extract_title`**

```python
    def _extract_title(
        self, blocks: list[TextBlock], metadata: dict[str, Any], median_font_size: float
    ) -> str | None:
        """Document title from corroborated metadata, else the largest first-page line.

        The metadata title is believed only where page 1 prints it — real
        PDFs carry filenames, ``"untitled"`` and typesetter job numbers
        there, and such a title used to beat a perfectly good large-font line
        (issue #56). The fallback is believed only when it exceeds the body
        median by half again — otherwise an ordinary line would become the
        title of every PDF whose metadata is blank.
        """
        first_page = [b for b in blocks if b.page_num == 0]
        title = accepted_metadata_title(metadata, "\n".join(b.text for b in first_page))
        if title:
            return title
        if not first_page:
            return None
        candidate = max(first_page, key=lambda b: b.font_size)
        if candidate.font_size > median_font_size * _TITLE_SIZE_RATIO:
            return candidate.text
        return None
```

Run: `uv run pytest tests/test_segmenter.py -v` → PASS, all of it.

- [ ] **Step 4: Commit**

```bash
git add bmlib/fulltext/segmenter.py tests/test_segmenter.py
git commit -m "fix(fulltext): stop a junk metadata title beating the title on the page (#56)"
```

---

### Task 6: Wire it into the converter

**Files:**
- Modify: `bmlib/fulltext/pdf_converter.py` (`ConversionResult`, `PyMuPDFConverter.convert`)
- Modify: `tests/test_pdf_converter.py`

- [ ] **Step 1: Write the failing tests**

Follow the file's existing shape: a `@pytest.mark.skipif(not _HAS_FITZ, …)`
class with a static PDF writer, as `TestExtractBlocks` does at
`tests/test_pdf_converter.py:470`.

```python
@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF not installed")
class TestTheConvertedResultCarriesAJudgedTitle:
    """Issue #56: real PDFs carry filenames and "untitled" in their metadata."""

    @staticmethod
    def _write_pdf(path, metadata_title: str):
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Effects of aspirin on outcomes", fontname="hebo", fontsize=18)
        page.insert_text((72, 110), "We randomised 400 adults.", fontname="helv", fontsize=10)
        doc.set_metadata({"title": metadata_title})
        doc.save(str(path))
        doc.close()
        return path

    def test_a_corroborated_metadata_title_reaches_result_title(self, tmp_path):
        pdf = self._write_pdf(tmp_path / "good.pdf", "Effects of aspirin on outcomes")
        assert get_converter("pymupdf").convert(pdf).title == "Effects of aspirin on outcomes"

    def test_a_junk_metadata_title_leaves_result_title_none(self, tmp_path):
        pdf = self._write_pdf(tmp_path / "junk.pdf", "Microsoft Word - ms.docx")
        assert get_converter("pymupdf").convert(pdf).title is None

    def test_metadata_title_stays_verbatim_either_way(self, tmp_path):
        """`metadata` is what the PDF says. A caller debugging provenance
        needs the raw string, and `creator`/`producer` sit beside it
        unmodified — sanitising one key of a verbatim dict would make the
        dict lie about its neighbours."""
        pdf = self._write_pdf(tmp_path / "junk2.pdf", "Microsoft Word - ms.docx")
        result = get_converter("pymupdf").convert(pdf)
        assert result.metadata["title"] == "Microsoft Word - ms.docx"
        assert result.title is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_pdf_converter.py -v -k Judged`
Expected: FAIL — `ConversionResult` has no attribute `title`.

- [ ] **Step 3: Add the field and populate it**

On `ConversionResult`, **declared last** so positional construction stays stable:

```python
    # The metadata title, where page 1 corroborates it (issue #56); None when
    # the PDF carries junk there, which is common. `metadata["title"]` keeps
    # the verbatim original beside it.
    title: str | None = None
```

In `convert()`, capture page 0's text inside the existing page loop and judge after it:

```python
                first_page_text = ""
                for page_num in range(page_count):
                    try:
                        page_text = doc[page_num].get_text()
                        if page_num == 0:
                            first_page_text = page_text
                        ...
```

then pass `title=accepted_metadata_title(metadata, first_page_text)` into the success `ConversionResult`. **Not** `page_texts[0]`: that list omits pages yielding no text, so its first entry is page 1's only when page 1 had any — precisely the case the rule treats specially.

Run: `uv run pytest tests/test_pdf_converter.py -v` → PASS.

- [ ] **Step 4: Commit**

```bash
git add bmlib/fulltext/pdf_converter.py tests/test_pdf_converter.py
git commit -m "feat(fulltext): ConversionResult.title carries the judged title (#56)"
```

---

### Task 7: The metric test

**Files:**
- Create: `tests/test_pdf_metadata_titles.py`

- [ ] **Step 1: Write the metric test**

The two ship-rule rates are measured at the **rule**, through
`accepted_metadata_title`, not at `_extract_title`'s output: after a rejection
the font fallback often returns the same title off the page, so an
output-level measurement would score a wrong rejection as a success and
report a rule better than the one that shipped. `_extract_title` is exercised
separately, for the recovery number that goes in the CHANGELOG.

```python
_FIXTURE = Path(__file__).resolve().parent / "data" / "pdf_metadata_titles.json"


def _rows() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": row["metadata_title"],
        "creator": row["creator"],
        "producer": row["producer"],
        "file_path": row["file_name"],
    }


def _page_one_text(row: dict[str, Any]) -> str:
    return "\n".join(line["text"] for line in row["page_one_lines"])


def _accepted(row: dict[str, Any]) -> str | None:
    return accepted_metadata_title(_metadata(row), _page_one_text(row))


def _bucket(name: str) -> list[dict[str, Any]]:
    return [row for row in _rows() if row["bucket"] == name]


class TestTheRuleMeetsTheFloorsItShippedOn:
    """Ship rule from the design, fixed before the corpus was collected."""

    def test_a_good_title_is_almost_never_rejected(self) -> None:
        """Rule 1: ≤1% of `match` rows wrongly rejected."""
        rows = _bucket("match")
        rejected = [row for row in rows if _accepted(row) is None]
        assert len(rejected) / len(rows) <= 0.01, [r["id"] for r in rejected]

    def test_most_junk_is_rejected(self) -> None:
        """Rule 2: ≥80% of `unrelated` rows rejected."""
        rows = _bucket("unrelated")
        rejected = [row for row in rows if _accepted(row) is None]
        assert len(rejected) / len(rows) >= 0.80, [r["metadata_title"] for r in rows if _accepted(r)]

    def test_the_corpus_still_holds_both_populations(self) -> None:
        """The control on the two tests above. A fixture that lost its
        `unrelated` rows would satisfy rule 2 over an empty set, and one that
        lost its `match` rows would satisfy rule 1 the same way — both
        divisions would raise ZeroDivisionError rather than pass, but a
        fixture thinned to three rows apiece would pass while measuring
        nothing."""
        assert len(_bucket("match")) >= 50
        assert len(_bucket("unrelated")) >= 5

    def test_the_segmenter_agrees_with_the_rule_on_every_row(self) -> None:
        """The wiring, not the rule: whatever `accepted_metadata_title`
        returns for a row, `_extract_title` must return it too rather than
        some other candidate."""
        for row in _rows():
            accepted = _accepted(row)
            if accepted is None:
                continue
            blocks = _blocks_of(row)
            assert (
                SectionSegmenter()._extract_title(blocks, _metadata(row), row["median_font_size"])
                == accepted
            )
```

`_blocks_of(row)` rebuilds `TextBlock`s from `page_one_lines` with
`page_num=0`, `font_size=line["size"]`, `is_bold=line["bold"]`, `y=line["y"]`,
and the layout fields it does not carry defaulted (`font_name=""`, `x=0.0`,
`width=0.0`, `height=12.0`). It passes `row["median_font_size"]` rather than
recomputing one: `_TITLE_SIZE_RATIO` compares a candidate against the
*document's* body median, and 20 stored title-page lines are not that
document.

Assert against the spec's floors, never against whatever the run produced — a
test written to the observed value pins an accident rather than a requirement.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_pdf_metadata_titles.py -v` → PASS. If rule 1 or 2 fails, stop: the finding and the numbers go in the PR description and the design is revisited, not the floors relaxed.

- [ ] **Step 3: Record the numbers**

Print, for the PR and CHANGELOG: rows per source, bucket distribution, the wrong-rejection rate with its Wilson interval, the junk-rejection rate, and how often the font fallback recovered the record title after a rejection.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pdf_metadata_titles.py
git commit -m "test(fulltext): pin the corroboration rule against the measured corpus (#56)"
```

---

### Task 8: Documentation and the PR

**Files:**
- Modify: `CHANGELOG.md`, `docs/manual/fulltext.md`, `CLAUDE.md`, `ROADMAP.md`, `HANDOVER.md`

- [ ] **Step 1: CHANGELOG under `[Unreleased]`**

`### Fixed` for the segmenter, `### Added` for `ConversionResult.title`. Carry the measured numbers, name what the fallback recovers, and say plainly that `metadata["title"]` stays verbatim on purpose.

- [ ] **Step 2: `docs/manual/fulltext.md`**

In the segmenter and PDF-conversion sections: what corroboration is, why an image-only page 1 accepts, and which field to read (`result.title`, with `metadata["title"]` documented as the unjudged original).

- [ ] **Step 3: `CLAUDE.md`**

Add `test_pdf_title_sampler.py` and `test_pdf_metadata_titles.py` to the test-file mapping table, note `scripts/_sampling.py` and `scripts/sample_pdf_metadata_titles.py` beside the other live runners with **run it before changing the reject-list**, and add the corroboration rule to the `fulltext/` description.

- [ ] **Step 4: `ROADMAP.md` and `HANDOVER.md`**

A ✅ row for #56 under **Full text**, and one under **Quality & maintenance** for the new instrument. In HANDOVER: drop #56 from the open list, correct the count (it currently says "Three" above four issues), and note the branch state.

- [ ] **Step 5: Full verification**

```bash
uv run pytest tests/ -v
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

Both clean, with the PostgreSQL DSN half run too if a server is up.

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin fix/56-junk-pdf-metadata-titles
gh pr create --base main --title "fix(fulltext): believe a PDF metadata title only where the document corroborates it (#56)" --body "…Closes #56…"
```

The body carries: the problem, the measured corpus (sources, n, bucket distribution), the rule, which backstop members earned their place and which were rejected by the corpus, the two ship-rule numbers with intervals, and the additive converter API.
