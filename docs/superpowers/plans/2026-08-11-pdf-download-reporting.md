# Silent PDF tier: report what it swallows, stop it discarding its input — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bmlib.fulltext`'s PDF tier take the free PDFs Europe PMC actually offers (#79), and report the two failure classes it currently swallows at DEBUG — a failed download (#68) and a swallowed bmlib defect (#72).

**Architecture:** One keyed one-shot warning mechanism (`FullTextService._warn_once`) serves both #68's exception path and #72's bug path, because both failure modes hit every article in a run when they hit at all. #79 lands first, since #68's log level is set from a live measurement and measuring a tier that discards 95.7% of its input would characterise a 4.3% tail. A committed sampler (`scripts/sample_free_pdf_urls.py`) supplies that measurement and keeps #79's allow-list answerable to the records.

**Tech Stack:** Python ≥3.11, pytest, ruff 0.15.20, httpx (guarded import in the script only), stdlib `math`/`argparse`.

**Design spec:** [`docs/superpowers/specs/2026-08-11-pdf-download-reporting-design.md`](../specs/2026-08-11-pdf-download-reporting-design.md) — read it before Task 1.

## Global Constraints

- **AGPL-3 header** at the top of every new source file. Copy verbatim from any existing file.
- **`from __future__ import annotations`** at the top of every new module, below the header and docstring.
- **Type hints** on every function signature (parameters and return). **Docstrings** on every public function, class and module; Google-style within a module.
- **`uv` only, never bare pip.** Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not `.venv`'s:** `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- Line length 100, target Python 3.11+, lint rules E, F, I, N, W, UP.
- Tests use in-memory SQLite and mocked HTTP. **No test may make a network request** — the sampler's tests drive it through a stubbed fetch.
- **TDD:** every step writes the test first and watches it fail before implementing.
- Branch is already created: `fix/68-72-79-pdf-download-reporting`. Commit after every task.
- **`git checkout -- <file>` destroys uncommitted work.** Commit before any mutation testing, and clear `__pycache__` after restoring a mutation (a same-length edit otherwise reads from a stale `.pyc`).

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `bmlib/fulltext/service.py` (modify) | `_extract_free_pdf_url` allow-list; `_warn_once`; `_TierFailures.on_bug`; `_download_and_cache_pdf` reporting | 1, 3, 4, 5 |
| `tests/test_fulltext_service.py` (modify) | All behaviour tests for the above | 1, 3, 4, 5 |
| `scripts/sample_free_pdf_urls.py` (create) | Live measurement of PDF-download failure rates, three populations | 2 |
| `tests/test_free_pdf_sampler.py` (create) | Offline tests for the sampler through a stubbed fetch | 2 |
| `CHANGELOG.md`, `ROADMAP.md`, `HANDOVER.md`, `docs/manual/fulltext.md` (modify) | Record the change | 6 |

---

### Task 1: #79 — take the free PDFs Europe PMC actually offers

**Files:**
- Modify: `bmlib/fulltext/service.py:186-204` (`_extract_free_pdf_url`)
- Test: `tests/test_fulltext_service.py` (new class at end of file)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_entry_is_free(entry: dict[str, object]) -> bool`, module-private, importable by tests. Constants `_FREE_PDF_AVAILABILITY_CODES: frozenset[str]` and `_FREE_PDF_AVAILABILITY_LABELS: frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestFreePDFAvailability:
    """Issue #79 — Tier 1d accepted ``availability == "Free"`` only.

    Europe PMC labels a ``fullTextUrl`` entry with a display string *and* a
    short code, and "Free" is the rare label. Measured over 600 recent
    MEDLINE records (``scripts/sample_free_pdf_urls.py``), of 326 entries with
    ``documentStyle == "pdf"``: 312 were "Open access" (95.7%) and 14 were
    "Free" (4.3%). Both are the identical ``?pdf=render`` URL on the identical
    host, so the tier was discarding about 95% of the PDFs it exists to find,
    with nothing logged at any level.
    """

    @staticmethod
    def _hit(style="pdf", availability="Open access", code="OA", url="https://ex/a.pdf"):
        """One search hit carrying a single ``fullTextUrl`` entry.

        One builder for every test in this class, so a rejection test cannot
        pass because its fixture was malformed in some unrelated way — the
        acceptance tests use the same builder and would fail too.
        """
        entry = {"documentStyle": style, "url": url}
        if availability is not None:
            entry["availability"] = availability
        if code is not None:
            entry["availabilityCode"] = code
        return {"fullTextUrlList": {"fullTextUrl": [entry]}}

    def test_an_open_access_pdf_is_taken(self):
        """The 95.7% case that was being discarded."""
        assert _extract_free_pdf_url(self._hit()) == "https://ex/a.pdf"

    def test_a_free_pdf_is_still_taken(self):
        """The 4.3% case that already worked — the widening must not narrow."""
        hit = self._hit(availability="Free", code="F")
        assert _extract_free_pdf_url(hit) == "https://ex/a.pdf"

    def test_a_subscription_entry_is_rejected(self):
        """The whole point of an allow-list: never download a paywalled PDF."""
        hit = self._hit(availability="Subscription required", code="S")
        assert _extract_free_pdf_url(hit) is None

    def test_an_entry_with_no_code_falls_back_to_the_label(self):
        """Every entry in the 1,263-entry sample carried a code; nothing
        documents that they must, so the display string stays a fallback."""
        hit = self._hit(code=None)
        assert _extract_free_pdf_url(hit) == "https://ex/a.pdf"

    def test_an_unknown_code_is_rejected_even_when_the_label_looks_free(self):
        """The under-credit rule, and the reason the code is authoritative.

        A future code bmlib has never seen must cost a retrieval rather than
        risk a paywalled download, so a present-but-unknown code is *not*
        allowed to fall back to the label it happens to carry.
        """
        hit = self._hit(availability="Open access", code="OA2")
        assert _extract_free_pdf_url(hit) is None

    def test_a_non_pdf_entry_is_rejected_however_free(self):
        """``documentStyle`` still gates: the HTML entry is not a PDF."""
        hit = self._hit(style="html")
        assert _extract_free_pdf_url(hit) is None

    def test_an_open_access_render_url_now_reaches_the_pdf_tier(self):
        """End to end: the tier fires where it used to fall through to a link."""
        search = MagicMock()
        search.status_code = 200
        search.json.return_value = {
            "resultList": {"result": [dict(self._hit(), inEPMC="N")]}
        }
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        with patch.object(
            service, "_http_get", side_effect=[search, _idconv_miss()]
        ):
            result = service.fetch_fulltext(doi="10.1/test", identifier=None)

        assert result.source == "europepmc_pdf"
        assert result.pdf_url == "https://ex/a.pdf"
```

Add `_extract_free_pdf_url` to the existing `from bmlib.fulltext.service import (...)` block at `tests/test_fulltext_service.py:35-42`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_fulltext_service.py::TestFreePDFAvailability -v
```

Expected: `ImportError: cannot import name '_extract_free_pdf_url'` is **wrong** — that name already exists. Expected instead: `test_an_open_access_pdf_is_taken`, `test_an_entry_with_no_code_falls_back_to_the_label`, `test_an_unknown_code_is_rejected...` and `test_an_open_access_render_url_now_reaches_the_pdf_tier` FAIL; the other three already PASS (they describe behaviour that is already correct and must stay correct).

- [ ] **Step 3: Implement the allow-list**

Replace `bmlib/fulltext/service.py:186-204` with:

```python
# Europe PMC labels a fullTextUrl entry's access twice over: a display string
# (`availability`) and a short controlled code (`availabilityCode`). Both are
# read — the code decides when present, the string is the fallback for an entry
# carrying none.
#
# An allow-list, never a deny-list on "Subscription required": an unknown future
# value must under-credit, costing one retrieval, rather than send bmlib to
# download a paywalled PDF. Transparency's _DEPOSITION_DATABANK_LEVELS is the
# same decision for the same reason.
#
# Measured over 600 recent MEDLINE records — all 1,263 fullTextUrl entries,
# of which 326 were documentStyle=pdf (scripts/sample_free_pdf_urls.py):
#
#     availability             code   pdf entries   share
#     Open access              OA             312   95.7%
#     Free                     F               14    4.3%
#     Subscription required    S                0      --
#
# There was no fourth value and every entry carried a code. Accepting only
# "Free" — which is what this did until issue #79 — therefore discarded 95.7%
# of the free PDFs Tier 1d exists to find, silently: both accepted labels are
# the identical https://europepmc.org/articles/PMC…?pdf=render shape on the
# identical host, and there is no log line for "a PDF entry was seen and not
# taken".
_FREE_PDF_AVAILABILITY_CODES = frozenset({"OA", "F"})
_FREE_PDF_AVAILABILITY_LABELS = frozenset({"Open access", "Free"})


def _entry_is_free(entry: dict[str, object]) -> bool:
    """Whether a ``fullTextUrl`` entry is one bmlib may download.

    Args:
        entry: One entry from Europe PMC's ``fullTextUrlList``.

    Returns:
        ``True`` when the entry's access code is one bmlib accepts, or — for an
        entry carrying no code — its display string is. A code that is present
        but unrecognised returns ``False`` **without** consulting the string:
        falling back there would let a future code bmlib has never evaluated
        through on the strength of a label, which is the opposite of the
        under-credit rule the allow-list exists to keep.
    """
    code = entry.get("availabilityCode")
    if isinstance(code, str) and code:
        return code in _FREE_PDF_AVAILABILITY_CODES
    return entry.get("availability") in _FREE_PDF_AVAILABILITY_LABELS


def _extract_free_pdf_url(result: dict[str, object]) -> str | None:
    """Extract a free PDF URL from Europe PMC's ``fullTextUrlList``.

    The search API includes ``fullTextUrlList`` with ``?pdf=render`` entries
    for PDFs it serves itself, even when JATS XML is unavailable — which is
    exactly when Tier 1d needs one.
    """
    url_list = result.get("fullTextUrlList")
    if not isinstance(url_list, dict):
        return None
    for entry in url_list.get("fullTextUrl", []):
        if (
            isinstance(entry, dict)
            and entry.get("documentStyle") == "pdf"
            and _entry_is_free(entry)
        ):
            url = entry.get("url")
            if isinstance(url, str):
                return url
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_fulltext_service.py -v
```

Expected: all PASS, including the pre-existing Tier 1d tests.

- [ ] **Step 5: Mutation-test the allow-list**

Change `_FREE_PDF_AVAILABILITY_CODES` to `frozenset({"F"})` (the old behaviour), run the class, confirm `test_an_open_access_pdf_is_taken` and `test_an_open_access_render_url_now_reaches_the_pdf_tier` FAIL. Then change it to `frozenset({"OA", "F", "S"})` and confirm `test_a_subscription_entry_is_rejected` FAILS. Restore, then:

```bash
find . -name __pycache__ -type d -exec rm -rf {} + ; uv run pytest tests/test_fulltext_service.py -q
```

- [ ] **Step 6: Commit**

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "fix(fulltext): take the free PDFs Europe PMC actually offers (#79)

_extract_free_pdf_url accepted availability == \"Free\" only. Measured over
600 recent MEDLINE records, that is the rare label: of 326 documentStyle=pdf
entries, 312 (95.7%) are \"Open access\" and 14 (4.3%) are \"Free\" — both the
identical ?pdf=render URL on the identical host. Tier 1d was discarding about
95% of the PDFs it exists to find, and there is no log line for a PDF entry
seen and not taken, so it was invisible.

Allow-lists on availabilityCode (OA, F), falling back to the display string
for an entry carrying no code, and rejecting a present-but-unknown code
rather than consulting the label — an unknown value must under-credit, not
risk a paywalled download.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The measurement — `scripts/sample_free_pdf_urls.py`

**Files:**
- Create: `scripts/sample_free_pdf_urls.py`
- Create: `tests/test_free_pdf_sampler.py`

**Interfaces:**
- Consumes: `_extract_free_pdf_url` from Task 1; `bmlib.publications.fetchers.biorxiv.fetch_biorxiv`.
- Produces (all module-level in the script, used by its tests):
  - `ProbeOutcome` dataclass — `ok: bool`, `cause: str | None`, `status: int | None`
  - `probe(client: object, url: str) -> ProbeOutcome`
  - `wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]`
  - `is_probeable(url: str) -> bool`
  - `summarise(name: str, outcomes: list[ProbeOutcome] | None) -> list[str]` — returns the report lines; `None` means the population could not be sampled and yields a single `ERROR` line.
  - `main() -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_free_pdf_sampler.py` (AGPL header first, copied from `tests/test_databank_sampler.py`):

```python
"""Tests for ``scripts/sample_free_pdf_urls.py``.

The script is a live runner — it probes Europe PMC, Unpaywall and publisher
hosts — but the rates it prints are the evidence for issue #68's log level and
issue #79's allow-list, so the table has to be trustworthy offline. What is
pinned here is the property that makes it so: **a population that could not be
sampled prints ERROR, never a zero.** A 0% failure rate is what a perfectly
healthy population looks like, and a dead Europe PMC must not be readable as
one.

Note the asymmetry, which is the whole design: an individual *probe* that
raises is a real finding — it is one of the three causes bmlib's
``_download_and_cache_pdf`` swallows — and is counted. It is the *sampling*
step that must report ERROR when it fails.

No network: every test drives the script through a stubbed client.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sample_free_pdf_urls.py"
_spec = importlib.util.spec_from_file_location("bmlib_free_pdf_sampler", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the sampler from {_PATH}")
sampler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sampler)


class _Resp:
    """A minimal stand-in for an httpx response."""

    def __init__(self, status_code: int, content: bytes = b"%PDF-1.7 ...") -> None:
        self.status_code = status_code
        self.content = content


class _Client:
    """Answers each URL from a script, or raises what the script holds."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.seen: list[str] = []

    def get(self, url: str, **kwargs: object) -> _Resp:
        self.seen.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestTheProbeSortsTheThreeCauses:
    """bmlib swallows three distinct outcomes; the measurement must keep them apart.

    Merging them would answer #68's level question with a number that cannot
    distinguish a full disk from a publisher 404 — which is the defect, not
    the measurement.
    """

    def test_a_pdf_is_a_success(self):
        client = _Client({"https://e/a.pdf": _Resp(200, b"%PDF-1.7 body")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is True
        assert outcome.cause is None

    def test_a_non_200_is_reported_with_its_status(self):
        client = _Client({"https://e/a.pdf": _Resp(404)})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "http-404"
        assert outcome.status == 404

    def test_a_landing_page_is_a_magic_byte_rejection_not_an_http_failure(self):
        """The Unpaywall failure mode: HTTP 200, and the bytes are HTML."""
        client = _Client({"https://e/a.pdf": _Resp(200, b"<!DOCTYPE html><html>")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "not-a-pdf"

    def test_an_exception_is_reported_by_type(self):
        client = _Client({"https://e/a.pdf": TimeoutError("timed out")})
        outcome = sampler.probe(client, "https://e/a.pdf")
        assert outcome.ok is False
        assert outcome.cause == "exception-TimeoutError"

    def test_a_non_http_url_is_not_probed_at_all(self):
        """The URLs come from third-party JSON.

        A ``file://`` or ``ftp://`` URL is not a download failure — counting it
        as one would put a scheme bmlib never fetches into the rate that sets a
        log level — and it must never be handed to a fetcher.
        """
        assert sampler.is_probeable("https://e/a.pdf") is True
        assert sampler.is_probeable("http://e/a.pdf") is True
        assert sampler.is_probeable("file:///etc/passwd") is False
        assert sampler.is_probeable("ftp://e/a.pdf") is False


class TestAFailedSampleNeverPrintsAsAFinding:
    """The property that makes the table trustworthy offline."""

    def test_an_unsampled_population_prints_error_not_a_zero_rate(self):
        lines = sampler.summarise("europepmc", None)
        assert any("ERROR" in line for line in lines)
        assert not any("0.0%" in line for line in lines)

    def test_a_sampled_population_with_no_failures_prints_a_rate(self):
        """The control: a genuine 0% must still be reportable as 0%."""
        outcomes = [sampler.ProbeOutcome(ok=True, cause=None, status=200)] * 10
        lines = sampler.summarise("europepmc", outcomes)
        assert not any("ERROR" in line for line in lines)
        assert any("0.0%" in line for line in lines)

    def test_an_empty_sample_is_an_error_not_a_perfect_score(self):
        """Sampling that returned zero URLs is not a population with no failures."""
        lines = sampler.summarise("europepmc", [])
        assert any("ERROR" in line for line in lines)


class TestTheIntervalIsComputedOverAttemptsActuallyMade:
    """A threshold rule (#68's 5%) needs an interval, not a point estimate."""

    def test_known_wilson_values(self):
        lo, hi = sampler.wilson(0, 300)
        assert lo == pytest.approx(0.0, abs=1e-9)
        assert hi == pytest.approx(0.012643, abs=1e-5)

        lo, hi = sampler.wilson(15, 300)
        assert lo == pytest.approx(0.030531, abs=1e-5)
        assert hi == pytest.approx(0.080848, abs=1e-5)

    def test_the_interval_straddles_the_decision_threshold_at_five_percent(self):
        """Why the spec asks for an interval: 15/300 is exactly 5%, and the
        sample does not actually settle which side of the rule it falls on."""
        lo, hi = sampler.wilson(15, 300)
        assert lo < 0.05 < hi

    def test_no_attempts_is_an_error_not_an_interval(self):
        with pytest.raises(ValueError):
            sampler.wilson(0, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_free_pdf_sampler.py -v
```

Expected: FAIL at collection — `ImportError: cannot load the sampler from .../scripts/sample_free_pdf_urls.py`.

- [ ] **Step 3: Write the script**

Create `scripts/sample_free_pdf_urls.py`. AGPL header (with `#!/usr/bin/env python3` as line 1, as `sample_databank_names.py` has), then:

```python
"""Measure how often a PDF bmlib decides to download actually fails.

``FullTextService._download_and_cache_pdf`` swallows three distinct outcomes at
DEBUG — a non-200, a magic-byte rejection, and any exception — so a full disk
across a 10,000-paper run looks exactly like 10,000 publishers 404ing
(issue #68). Choosing a log level for those is a noise question, and this repo
settles noise questions by measuring rather than by taste.

What is measured is the failure rate **given bmlib already holds the URL**.
That is deliberately not "how often does Tier 1d fire": reachability governs how
often the code runs, not how often it fails when it does, and conflating the two
would let issue #79's fix silently move the number issue #68 was set from.

Three populations, one per call site of ``_download_and_cache_pdf``, because
they are not alike — Europe PMC serves its own host, Unpaywall points at
arbitrary repositories and often at a landing page rather than a PDF (which is
exactly the magic-byte rejection), and the fetchers build their own links:

===============  ========  ===================================================
Population       Tier      Drawn from
===============  ========  ===================================================
europepmc        1d        ``fullTextUrlList`` of one Europe PMC search
unpaywall        2         that search's DOIs, resolved as ``_fetch_unpaywall``
biorxiv          0         ``fetch_biorxiv`` itself
===============  ========  ===================================================

The first two come from the *same* papers, which makes their rates directly
comparable; Unpaywall's half is drawn from ``inEPMC != "Y"`` records, since
those are the ones that reach Tier 2. The third calls ``fetch_biorxiv`` rather
than re-spelling its URL template, so the URL under test cannot drift from the
one bmlib builds.

Probes are a ranged GET for the first kilobyte, so measuring does not mean
downloading 900 whole PDFs, and they record both of bmlib's failure modes: the
status code, and whether the bytes begin ``%PDF``.

A population that could not be sampled prints ``ERROR``, never a zero — a 0%
failure rate is what a perfectly healthy population looks like. An individual
probe that raises is the opposite: that is a real finding, one of the three
causes bmlib swallows, and it is counted.

    uv run python scripts/sample_free_pdf_urls.py --email you@example.org

Companion to ``scripts/sample_databank_names.py`` and
``scripts/sample_funder_names.py``. Run it before changing
``_FREE_PDF_AVAILABILITY_CODES`` or the log levels in
``_download_and_cache_pdf``.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

try:
    import httpx
except ImportError:  # pragma: no cover - the script is a live runner
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(1) from None

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from bmlib import __version__  # noqa: E402
from bmlib.fulltext.service import _extract_free_pdf_url  # noqa: E402
from bmlib.publications.fetchers.biorxiv import fetch_biorxiv  # noqa: E402

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
PAGE_SIZE = 100
PROBE_BYTES = 1024
# One request per second per host. The probe walks third-party publisher hosts
# that never agreed to be measured; Europe PMC's own guidance is the ceiling,
# not the target.
REQUEST_INTERVAL_SECONDS = 1.0
DEFAULT_TARGET = 300


@dataclass(frozen=True)
class ProbeOutcome:
    """What one download attempt would have produced for bmlib.

    Attributes:
        ok: Whether bmlib would have cached a PDF.
        cause: ``None`` on success, else the failure bucket — ``http-<status>``,
            ``not-a-pdf``, or ``exception-<TypeName>``. The three are kept
            apart because they are the three bmlib swallows, and merging them
            would answer #68's question with a number that cannot tell a full
            disk from a publisher 404.
        status: The HTTP status, when there was one.
    """

    ok: bool
    cause: str | None
    status: int | None


def is_probeable(url: str) -> bool:
    """Whether *url* is one bmlib would actually fetch.

    The URLs come from third-party JSON — Europe PMC's ``fullTextUrlList`` and
    Unpaywall's locations — so the scheme is not bmlib's to assume. A
    ``file://`` or ``ftp://`` URL is not a *download failure*; counting it as
    one would put a scheme bmlib never fetches into the rate that sets a log
    level. The same reasoning as ``_normalise_base_url`` in the Ollama
    provider, for the same class of input.
    """
    return urlsplit(url).scheme in ("http", "https")


def probe(client: Any, url: str) -> ProbeOutcome:
    """Attempt *url* the way ``_download_and_cache_pdf`` would, and classify it.

    Args:
        client: An HTTP client with ``get(url, headers=...)``.
        url: The PDF URL to probe.

    Returns:
        The outcome, in one of the three buckets bmlib swallows.
    """
    try:
        resp = client.get(url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"})
    except Exception as exc:
        return ProbeOutcome(ok=False, cause=f"exception-{type(exc).__name__}", status=None)
    # 206 Partial Content is the success for a ranged GET; a server ignoring
    # Range answers 200 with the whole body, which is equally fine.
    if resp.status_code not in (200, 206):
        return ProbeOutcome(
            ok=False, cause=f"http-{resp.status_code}", status=resp.status_code
        )
    if not resp.content.startswith(b"%PDF"):
        return ProbeOutcome(ok=False, cause="not-a-pdf", status=resp.status_code)
    return ProbeOutcome(ok=True, cause=None, status=resp.status_code)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A Wilson score interval for *k* failures in *n* attempts.

    An interval rather than a point estimate because issue #68's rule has a
    threshold in it (5%), and a point estimate near that threshold would
    misrepresent what the sample settles: 15 failures in 300 is exactly 5.0%
    and its interval runs from 3.1% to 8.1%.

    Raises:
        ValueError: If *n* is zero. There is no interval over no attempts, and
            returning ``(0.0, 0.0)`` would print as a perfect score.
    """
    if n <= 0:
        raise ValueError("no attempts to compute an interval over")
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def summarise(name: str, outcomes: list[ProbeOutcome] | None) -> list[str]:
    """Render one population's result as report lines.

    Args:
        name: The population's name.
        outcomes: Its probe outcomes, or ``None`` when the *sampling* failed.
            An empty list is treated the same as ``None``: zero URLs sampled is
            not a population with no failures.

    Returns:
        The lines to print. A population that could not be measured yields a
        single ``ERROR`` line and no rate, because a zero is exactly what a
        healthy population looks like.
    """
    if not outcomes:
        return [f"{name:<12} ERROR — could not sample this population; no rate is reported"]
    n = len(outcomes)
    failures = [o for o in outcomes if not o.ok]
    lo, hi = wilson(len(failures), n)
    lines = [
        f"{name:<12} {n:>4} probed   "
        f"{len(failures):>4} failed = {100 * len(failures) / n:.1f}%   "
        f"95% CI [{100 * lo:.1f}%, {100 * hi:.1f}%]"
    ]
    for cause, count in sorted(Counter(o.cause for o in failures).items()):
        lines.append(f"{'':<12}   {cause:<28} {count:>4}")
    return lines
```

Then the three samplers and `main()`:

```python
def _sleep() -> None:
    """Pace requests. Separated so tests can stub it out."""
    time.sleep(REQUEST_INTERVAL_SECONDS)


def sample_europepmc(client: Any, target: int) -> tuple[list[str], list[str]] | None:
    """Collect free PDF render URLs, split by whether the record is in EPMC.

    Returns:
        ``(in_epmc_urls, not_in_epmc_urls)``, or ``None`` when the search could
        not be completed — the caller must then print ``ERROR`` rather than a
        rate. The split is the spec's stated approximation of "XML unusable",
        which is the subgroup Tier 1d actually reaches; measuring it exactly
        would cost one ``fullTextXML`` request per sampled record.
    """
    query = "(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])"
    inside: list[str] = []
    outside: list[str] = []
    dois: list[str] = []
    cursor = "*"
    while len(inside) + len(outside) < target:
        _sleep()
        try:
            resp = client.get(
                EUROPE_PMC_SEARCH,
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": PAGE_SIZE,
                    "cursorMark": cursor,
                },
            )
            if resp.status_code != 200:
                print(f"  Europe PMC search HTTP {resp.status_code}", file=sys.stderr)
                return None
            payload = resp.json()
        except Exception as exc:
            print(f"  Europe PMC search failed: {exc}", file=sys.stderr)
            return None
        hits = payload.get("resultList", {}).get("result", [])
        if not hits:
            break
        for hit in hits:
            if hit.get("doi") and hit.get("inEPMC") != "Y":
                dois.append(hit["doi"])
            url = _extract_free_pdf_url(hit)
            if url and is_probeable(url):
                (inside if hit.get("inEPMC") == "Y" else outside).append(url)
        cursor = payload.get("nextCursorMark") or ""
        if not cursor:
            break
    sample_europepmc.dois = dois  # consumed by sample_unpaywall
    return inside[:target], outside[:target]
```

> **Note for the implementer:** the `sample_europepmc.dois` attribute above is a
> deliberate simplification — the two populations must come from the same
> papers, and threading a second return value through would not change the
> measurement. If you prefer, return a 3-tuple and adjust `main()`; either is
> acceptable, but do not sample Unpaywall from a *separate* search, which would
> destroy the comparability the spec relies on.

```python
def sample_unpaywall(client: Any, dois: list[str], email: str, target: int) -> list[str] | None:
    """Resolve DOIs to open-access PDF URLs exactly as ``_fetch_unpaywall`` does."""
    urls: list[str] = []
    asked = 0
    for doi in dois:
        if len(urls) >= target:
            break
        asked += 1
        _sleep()
        try:
            resp = client.get(
                f"{UNPAYWALL_BASE}/{quote(doi, safe='')}?email={quote(email, safe='')}"
            )
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                print(f"  Unpaywall HTTP {resp.status_code} for {doi}", file=sys.stderr)
                continue
            data = resp.json()
        except Exception as exc:
            print(f"  Unpaywall failed for {doi}: {exc}", file=sys.stderr)
            continue
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        if not url:
            for loc in data.get("oa_locations") or []:
                url = loc.get("url_for_pdf") or loc.get("url")
                if url:
                    break
        if url and is_probeable(url):
            urls.append(url)
    # Nothing resolved out of nothing asked is a failed sample, not a finding.
    return urls if asked else None


def sample_biorxiv(client: Any, target: int, server: str = "biorxiv") -> list[str] | None:
    """Collect the PDF URLs ``fetch_biorxiv`` itself builds."""
    urls: list[str] = []
    day = date.today() - timedelta(days=30)
    for _ in range(10):
        if len(urls) >= target:
            break
        records: list[Any] = []
        _sleep()
        try:
            fetch_biorxiv(client, day, on_record=records.append, server=server)
        except Exception as exc:
            print(f"  bioRxiv fetch failed for {day}: {exc}", file=sys.stderr)
            day -= timedelta(days=1)
            continue
        for record in records:
            for entry in record.fulltext_sources:
                if entry.format == "pdf" and is_probeable(entry.url):
                    urls.append(entry.url)
        day -= timedelta(days=1)
    return urls or None


def main() -> int:
    """Probe all three populations and print the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address Unpaywall requires.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="URLs per population.")
    args = parser.parse_args()

    headers = {
        "User-Agent": (
            f"bmlib-sampler/{__version__} "
            f"(+https://github.com/hherb/bmlib; {args.email})"
        )
    }
    with httpx.Client(timeout=45.0, headers=headers, follow_redirects=True) as client:
        epmc = sample_europepmc(client, args.target)
        epmc_in, epmc_out = epmc if epmc else (None, None)
        dois = getattr(sample_europepmc, "dois", [])
        unpaywall = sample_unpaywall(client, dois, args.email, args.target) if epmc else None
        biorxiv = sample_biorxiv(client, args.target)

        def run(urls: list[str] | None) -> list[ProbeOutcome] | None:
            if urls is None:
                return None
            outcomes = []
            for url in urls:
                _sleep()
                outcomes.append(probe(client, url))
            return outcomes

        populations = [
            ("europepmc/in", run(epmc_in)),
            ("europepmc/out", run(epmc_out)),
            ("unpaywall", run(unpaywall)),
            ("biorxiv", run(biorxiv)),
        ]

    print("\nPDF download failure rates, by population\n")
    for name, outcomes in populations:
        for line in summarise(name, outcomes):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_free_pdf_sampler.py -v
```

Expected: all PASS. If `_sleep` makes a test slow, the tests never call the samplers — only `probe`, `wilson`, `is_probeable` and `summarise` — so no stubbing is needed.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/sample_free_pdf_urls.py tests/test_free_pdf_sampler.py
git commit -m "test(scripts): measure PDF download failure rates per call site

The instrument for issue #68's log level, and what keeps #79's allow-list
answerable to the records. Three populations, one per call site of
_download_and_cache_pdf, because they are not alike: Europe PMC serves its own
host, Unpaywall points at arbitrary repositories and often at a landing page
rather than a PDF, and the fetchers build their own links.

A population that could not be sampled prints ERROR, never a zero — a 0%
failure rate is what a healthy population looks like. An individual probe that
raises is the opposite: a real finding, and counted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Start the live run in the background**

```bash
uv run python scripts/sample_free_pdf_urls.py --email horst.herb@gmail.com --target 300 \
  > /tmp/free_pdf_sample.txt 2>&1
```

Run this with `run_in_background: true`. It takes roughly 20–25 minutes. **Do not block on it** — proceed to Task 3, and read `/tmp/free_pdf_sample.txt` when Task 5 needs it.

---

### Task 3: One keyed one-shot warning mechanism

**Files:**
- Modify: `bmlib/fulltext/service.py:360-364` (`__init__`), `:822-843` (`_warn_cache_write_failed`), `:993-1001` (`_attach_pdf_text`)
- Test: `tests/test_fulltext_service.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `FullTextService._warn_once(self, key: str, msg: str, *args: object) -> None` and the attribute `self._warned: set[str]`. Tasks 4 and 5 both call it. The attributes `_pdf_backend_warned` and `_cache_write_warned` **cease to exist**.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestWarnOnce:
    """The one-shot mechanism shared by #68's exception path and #72's bug path.

    Both failure modes hit every article in a run when they hit at all, so
    per-article is never right for either; and both need *per-cause* keys, so
    a second distinct fault is not hidden by the first — the "the more complete
    the failure, the quieter it gets" shape #67 existed to fix.
    """

    def test_the_same_key_warns_once(self, caplog):
        service = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            service._warn_once("k", "something went wrong: %s", "detail")
            service._warn_once("k", "something went wrong: %s", "detail")
        assert caplog.text.count("something went wrong") == 1

    def test_different_keys_each_warn(self, caplog):
        """The reason it is a keyed set and not a boolean."""
        service = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            service._warn_once("a", "first fault")
            service._warn_once("b", "second fault")
        assert "first fault" in caplog.text
        assert "second fault" in caplog.text

    def test_two_services_do_not_share_suppression(self, caplog):
        """Per service, like the booleans it replaces — not process-wide."""
        one = FullTextService(email="test@example.com")
        two = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            one._warn_once("k", "the fault")
            two._warn_once("k", "the fault")
        assert caplog.text.count("the fault") == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_fulltext_service.py::TestWarnOnce -v
```

Expected: FAIL with `AttributeError: 'FullTextService' object has no attribute '_warn_once'`.

- [ ] **Step 3: Implement, and migrate both booleans onto it**

In `__init__`, replace the two boolean lines with:

```python
        # Faults that are a property of the environment or of bmlib itself
        # rather than of one article: warned once each, keyed by cause, so a
        # second distinct fault is still reported instead of hiding behind the
        # first. Per service, not process-wide — a caller that builds a fresh
        # service has a fresh environment to learn about.
        self._warned: set[str] = set()
```

Add the method next to `_warn_cache_write_failed`:

```python
    def _warn_once(self, key: str, msg: str, *args: object) -> None:
        """Emit a WARNING the first time *key* is seen on this service.

        Args:
            key: What is being reported. Include the *cause* — not just the
                site — so two different faults at one site are both reported.
            msg: A ``%``-style format string, interpolated lazily by logging.
            args: Its arguments.
        """
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(msg, *args)
```

Rewrite `_warn_cache_write_failed`'s body (keep its docstring) as:

```python
        self._warn_once(
            "cache-write",
            "Could not write to the full-text cache (%s); retrieval still "
            "works, but nothing is being cached, so every run re-fetches.",
            exc,
        )
```

In `_attach_pdf_text`, replace the `if not self._pdf_backend_warned:` block with:

```python
            self._warn_once(
                f"pdf-backend:{type(e).__name__}",
                "convert_pdfs is enabled but no PDF backend is usable (%s: %s); "
                "PDFs will be returned as links only. Install bmlib[pdf] if the "
                "extra is missing.",
                type(e).__name__,
                e,
            )
            return
```

- [ ] **Step 4: Run the whole file to verify nothing regressed**

```bash
uv run pytest tests/test_fulltext_service.py -v
```

Expected: all PASS. `TestCacheWriteFailuresAreReported` and `TestPDFTextExtraction` assert on log output, not on the removed attributes, so they must be unaffected. If either fails, the migration changed observable behaviour — fix the migration, not the test.

- [ ] **Step 5: Commit**

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "refactor(fulltext): one keyed one-shot warning, replacing two booleans

Preparation for #68 and #72, which each add a one-shot warning site and each
need per-cause keys rather than one flag per site. Leaving two booleans beside
a keyed set doing the same job would read as accidental.

Nothing observable changes: no test referenced either attribute, both were
asserted through log output. The PDF-backend warning gains a per-exception-type
key, so a second, different backend fault is now reported instead of being
hidden by the first.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: #72 — a swallowed bmlib defect must not stay at DEBUG

**Files:**
- Modify: `bmlib/fulltext/service.py:97-152` (`_TierFailures`), `:458` (its construction)
- Test: `tests/test_fulltext_service.py`

**Interfaces:**
- Consumes: `_warn_once` from Task 3.
- Produces: `_BUG_TYPES: tuple[type[BaseException], ...]`; `_TierFailures.on_bug: Callable[[BaseException], None] | None`; `FullTextService._warn_swallowed_bug(self, exc: BaseException) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestASwallowedBugDoesNotStayAtDebug:
    """Issue #72 — ``describe()`` is consulted at one exit: total exhaustion.

    A bug that every PMC tier hits, papered over by one tier that still works,
    was reported nowhere. The scenario: an ``AttributeError`` from every PMC
    tier — the shape a ``JATSArticle`` API change takes — with Unpaywall
    healthy. Every article in a corpus silently drops from structured JATS to a
    bare ``pdf_url``, and the library reports success.
    """

    @staticmethod
    def _unpaywall_ok() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"best_oa_location": {"url_for_pdf": "https://e/a.pdf"}}
        return resp

    def _run(self, service, exc, caplog):
        """Europe PMC's search raises *exc*; Unpaywall then succeeds."""
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                side_effect=[exc, _idconv_miss(), self._unpaywall_ok()],
            ),
        ):
            return service.fetch_fulltext(doi="10.1/test")

    def test_an_attribute_error_is_reported_even_though_a_later_tier_succeeds(self, caplog):
        """The issue verbatim."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        result = self._run(service, AttributeError("no attribute 'has_body'"), caplog)

        assert result.source == "unpaywall"  # the run still "succeeds"
        assert "AttributeError" in caplog.text
        assert "defect" in caplog.text

    def test_a_type_error_is_reported(self, caplog):
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, TypeError("str expected"), caplog)
        assert "TypeError" in caplog.text

    def test_a_network_error_is_not_reported_as_a_defect(self, caplog):
        """The control. An unreachable host is not a bmlib bug, and #67's
        exhaustion report already covers it."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, OSError("network is down"), caplog)
        assert "defect" not in caplog.text

    def test_a_malformed_json_body_is_not_reported_as_a_defect(self, caplog):
        """``json.JSONDecodeError`` **is** a ``ValueError``.

        This is why ``ValueError`` can never be a member of ``_BUG_TYPES``:
        every ``resp.json()`` on a malformed body raises one, and they are
        ordinary remote-data failures.
        """
        import json

        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, json.JSONDecodeError("bad", "doc", 0), caplog)
        assert "defect" not in caplog.text

    def test_malformed_xml_is_not_reported_as_a_defect(self, caplog):
        """``ET.ParseError`` **is** a ``SyntaxError``.

        The companion exclusion, and the less obvious of the two: "a
        SyntaxError is always a bug" is intuitive and exactly backwards here.
        """
        import xml.etree.ElementTree as ET

        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, ET.ParseError("mismatched tag"), caplog)
        assert "defect" not in caplog.text

    def test_two_different_defect_types_are_both_reported(self, caplog):
        """Per type, not per service: the second must not hide behind the first."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                side_effect=[
                    AttributeError("first"),
                    _idconv_miss(),
                    TypeError("second"),
                ],
            ),
        ):
            service.fetch_fulltext(doi="10.1/test")

        assert "AttributeError" in caplog.text
        assert "TypeError" in caplog.text

    def test_the_same_defect_type_is_reported_once_per_service(self, caplog):
        """A defect that hits every article must not produce a line per article."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        for _ in range(3):
            with (
                caplog.at_level("WARNING"),
                patch.object(
                    service, "_http_get", side_effect=AttributeError("boom")
                ),
            ):
                service.fetch_fulltext(doi="10.1/test")

        assert caplog.text.count("which bmlib does not raise deliberately") == 1

    def test_a_bare_tier_failures_record_still_works(self):
        """``on_bug`` defaults to ``None``: existing direct construction is safe."""
        failures = _TierFailures()
        failures.record(TypeError("boom"))
        assert failures.faults == ["TypeError"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_fulltext_service.py::TestASwallowedBugDoesNotStayAtDebug -v
```

Expected: the four `is_reported` / `are_both_reported` / `once_per_service` tests FAIL (no such warning); the three control tests and `test_a_bare_tier_failures_record_still_works` PASS.

- [ ] **Step 3: Implement**

Add `Callable` to the `typing` import at `bmlib/fulltext/service.py:35` — use `from collections.abc import Callable` on its own line, which is what ruff's `UP` rules require.

Add above `_TierFailures`:

```python
# Exception types that can only mean a bmlib defect, never a remote-data or
# environment failure. `except Exception` at the tier level is right for
# transport errors; it is wrong to hold a TypeError at DEBUG under any
# circumstances (issue #72).
#
# A deny-list rather than an allow-list because the legitimate failures are
# varied — FullTextError, httpx.HTTPError, json.JSONDecodeError, ET.ParseError,
# OSError — while the set that always means a defect is small and stable.
# NameError carries UnboundLocalError in by inheritance.
#
# What is *excluded* is the load-bearing part, and two of them are counter-
# intuitive, so they are named rather than left to be rediscovered:
#
#   ValueError    json.JSONDecodeError IS a ValueError — every resp.json() on
#                 a malformed body raises one.
#   SyntaxError   xml.etree.ElementTree.ParseError IS a SyntaxError — malformed
#                 remote XML raises one.
#   RuntimeError  RecursionError is one, and Path.home() raises one.
#   OSError       environment, not defect.
#
# AttributeError is knowingly imperfect in the other direction: it is reachable
# from remote data, since `data.get("resultList", {}).get("result", [])` raises
# it when Europe PMC returns a non-dict there. It stays, because that is a bmlib
# defect too — a missing shape check — and the message describes what happened
# rather than accusing the article.
_BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
)
```

In `_TierFailures`, add the field after `absences` and extend `record`:

```python
    faults: list[str] = field(default_factory=list)
    absences: int = 0
    # Called at the moment a defect-shaped exception is swallowed, not at an
    # exit. Every alternative reads this record at some exit, which is exactly
    # issue #72: `describe()` is already consulted at one exit, and that is why
    # the bug was invisible. Reporting at the swallow is the only shape a new
    # early return cannot silently re-break. Optional so that a bare
    # `_TierFailures()` — which the tests construct — still works.
    on_bug: Callable[[BaseException], None] | None = None

    def record(self, exc: BaseException) -> None:
        """Note one swallowed exception, filed by what it means."""
        if isinstance(exc, FullTextUnavailableError):
            self.absences += 1
            return
        self.faults.append(type(exc).__name__)
        if self.on_bug is not None and isinstance(exc, _BUG_TYPES):
            self.on_bug(exc)
```

Add the reporter to `FullTextService`, next to `_warn_cache_write_failed`:

```python
    def _warn_swallowed_bug(self, exc: BaseException) -> None:
        """Report a tier failure that can only be a bmlib defect.

        Once per exception type per service: a defect that hits one tier hits
        it for every article, so a line per article would be unreadable at
        exactly the moment it mattered — but a *second*, different defect must
        still be reported rather than hidden by the first.
        """
        name = type(exc).__name__
        self._warn_once(
            f"bug:{name}",
            "A full-text tier failed with %s (%s), which bmlib does not raise "
            "deliberately — this is a defect, possibly provoked by an "
            "unexpected API response. Full text may be silently degraded for "
            "every article in this run while later tiers keep succeeding. Run "
            "with DEBUG logging for the traceback and please report it; "
            "further %s failures will not be repeated.",
            name,
            exc,
            name,
        )
```

At `bmlib/fulltext/service.py:458`, change the construction to:

```python
        failures = _TierFailures(on_bug=self._warn_swallowed_bug)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_fulltext_service.py -v
```

Expected: all PASS.

- [ ] **Step 5: Mutation-test the exclusions**

This is the step that matters most — widening a deny-list catches strictly more, and no test that merely exercises a working retrieval could fail on it.

1. Add `ValueError` to `_BUG_TYPES`; confirm `test_a_malformed_json_body_is_not_reported_as_a_defect` FAILS.
2. Restore. Add `SyntaxError`; confirm `test_malformed_xml_is_not_reported_as_a_defect` FAILS.
3. Restore. Add `OSError`; confirm `test_a_network_error_is_not_reported_as_a_defect` FAILS.
4. Restore. Narrow `_BUG_TYPES` to `(TypeError,)`; confirm `test_an_attribute_error_is_reported_even_though_a_later_tier_succeeds` FAILS.
5. Restore. Remove the `self.on_bug(exc)` call; confirm the reporting tests FAIL.

After each restore:

```bash
find . -name __pycache__ -type d -exec rm -rf {} + ; uv run pytest tests/test_fulltext_service.py -q
```

- [ ] **Step 6: Commit**

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "fix(fulltext): a swallowed bmlib defect no longer stays at DEBUG (#72)

_TierFailures.describe() is consulted at one exit — total exhaustion — so a
bug that every PMC tier hits, papered over by one tier that still works, was
reported nowhere. An AttributeError from every PMC tier with Unpaywall healthy
degrades a whole corpus from structured JATS to bare links and reports success.

_TierFailures gains an on_bug callback invoked at the moment of the swallow,
not at an exit: every alternative reads the record at some exit, which is the
defect itself, and would be re-broken by the next early return.

The deny-list's exclusions are the load-bearing part and two are counter-
intuitive, each pinned by its own test and verified by mutation:
json.JSONDecodeError IS a ValueError and ET.ParseError IS a SyntaxError, so
neither type may ever be a member.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: #68 — say when a free PDF could not be downloaded

**Files:**
- Modify: `bmlib/fulltext/service.py:867-960` (`_download_and_cache_pdf`, `_save_pdf_to_cache`)
- Test: `tests/test_fulltext_service.py`

**Interfaces:**
- Consumes: `_warn_once` from Task 3.
- Produces: `_save_pdf_to_cache(...) -> tuple[str | None, str]` — the second element is `"saved"`, `"write-failed"` or `"not-a-pdf"`. **This is a signature change**; its only caller is `_download_and_cache_pdf`.

- [ ] **Step 1: Read the measurement and apply the pre-registered rule**

```bash
cat /tmp/free_pdf_sample.txt
```

The rule, fixed in the spec before the numbers landed:

- **Worst population's failure rate < 5%** → **Variant A** (per-article WARNING) in Step 4.
- **≥ 5%** → **Variant B** (one-shot per `(source, cause)` plus per-article DEBUG) in Step 4.

Use the point estimate of the **worst** population. If a population printed `ERROR`, it was not measured — re-run the sampler for that population before deciding; do not treat an unmeasured population as healthy. Record the actual numbers, and which variant they selected, in the code comment in Step 4 and in the commit message.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_fulltext_service.py`. These assert the *distinctions*, which both variants must make, so they do not depend on which variant Step 4 selects:

```python
class TestAFailedPDFDownloadIsReported:
    """Issue #68 — three distinct outcomes, all swallowed at DEBUG.

    A non-200 for a URL some tier just declared a free PDF, a magic-byte
    rejection, and any exception at all. With ``convert_pdfs=True`` the caller
    asked for text and got none, and a full disk across a 10,000-paper run
    looked exactly like 10,000 publishers 404ing.

    The three stay distinguishable in the message: reporting a read-only
    directory as "PDF validation failed" is the mistake ``_save_pdf_to_cache``
    already avoids between a failed write and a failed validation.
    """

    @staticmethod
    def _service(tmp_path):
        return FullTextService(
            email="test@example.com",
            cache=FullTextCache(cache_dir=tmp_path),
            convert_pdfs=False,
        )

    def _fetch(self, service, response_or_exc, caplog):
        search = MagicMock()
        search.status_code = 200
        search.json.return_value = {
            "resultList": {
                "result": [
                    {
                        "inEPMC": "N",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "documentStyle": "pdf",
                                    "availabilityCode": "OA",
                                    "url": "https://e/a.pdf",
                                }
                            ]
                        },
                    }
                ]
            }
        }
        with (
            caplog.at_level("DEBUG"),
            patch.object(
                service, "_http_get", side_effect=[search, _idconv_miss(), response_or_exc]
            ),
        ):
            return service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

    def test_a_404_is_reported_as_an_http_failure(self, tmp_path, caplog):
        resp = MagicMock()
        resp.status_code = 404
        result = self._fetch(self._service(tmp_path), resp, caplog)

        assert result.pdf_url == "https://e/a.pdf"
        assert result.file_path is None
        assert "404" in caplog.text

    def test_a_landing_page_is_reported_as_not_a_pdf(self, tmp_path, caplog):
        """HTTP 200 whose body is HTML — the Unpaywall failure mode."""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<!DOCTYPE html><html>not a pdf</html>"
        self._fetch(self._service(tmp_path), resp, caplog)

        assert "not a PDF" in caplog.text
        assert "404" not in caplog.text

    def test_a_network_failure_is_reported_as_an_exception(self, tmp_path, caplog):
        self._fetch(self._service(tmp_path), OSError("no route to host"), caplog)
        assert "OSError" in caplog.text

    def test_an_exception_is_warned_once_per_source_and_type(self, tmp_path, caplog):
        """A lost network fails every article, so this can never be per-article
        — the one cause whose cadence needed no measurement."""
        service = self._service(tmp_path)
        with caplog.at_level("WARNING"):
            for _ in range(3):
                self._fetch(service, OSError("no route to host"), caplog)
        assert caplog.text.count("no route to host") == 1

    def test_a_successful_download_reports_nothing(self, tmp_path, caplog):
        """The control: a working download must stay quiet."""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"%PDF-1.7\n" + b"x" * 100
        result = self._fetch(self._service(tmp_path), resp, caplog)

        assert result.file_path is not None
        assert "not a PDF" not in caplog.text
        assert "could not" not in caplog.text.lower()

    def test_a_failed_cache_write_is_not_reported_as_a_bad_pdf(self, tmp_path, caplog):
        """The distinction the signature change exists for.

        ``_save_pdf_to_cache`` returned ``None`` for both a failed write and a
        failed validation, so reporting on ``None`` alone would blame a
        read-only directory on the publisher's bytes.
        """
        service = self._service(tmp_path)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"%PDF-1.7\n" + b"x" * 100
        with patch.object(
            service.cache, "save_pdf", side_effect=OSError("read-only file system")
        ):
            self._fetch(service, resp, caplog)

        assert "nothing is being cached" in caplog.text
        assert "not a PDF" not in caplog.text
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/test_fulltext_service.py::TestAFailedPDFDownloadIsReported -v
```

Expected: `test_a_landing_page_is_reported_as_not_a_pdf`, `test_a_network_failure_is_reported_as_an_exception`, `test_an_exception_is_warned_once_per_source_and_type` and `test_a_failed_cache_write_is_not_reported_as_a_bad_pdf` FAIL.

- [ ] **Step 4: Implement**

First, change `_save_pdf_to_cache` to distinguish its two failures. Replace its return statements and update its `Returns:` docstring section:

```python
    def _save_pdf_to_cache(
        self, cache: FullTextCache, data: bytes, cache_id: str
    ) -> tuple[str | None, str]:
        """Write a downloaded PDF to the disk cache, best-effort.

        Returns:
            ``(path, outcome)``. ``outcome`` is ``"saved"``, ``"write-failed"``
            or ``"not-a-pdf"``. The two failures are told apart rather than
            both returning ``None``, because the caller reports them and
            blaming a read-only directory on the publisher's bytes is the
            mistake this method already avoids in its own logging.
        """
        try:
            path = cache.save_pdf(data, cache_id)
        except Exception as e:
            self._warn_cache_write_failed(e)
            logger.debug("Failed to cache PDF for %s", cache_id, exc_info=True)
            return None, "write-failed"
        if not path:
            logger.debug("PDF failed magic-byte validation for %s", cache_id)
            return None, "not-a-pdf"
        return path, "saved"
```

Then replace `_download_and_cache_pdf`'s `try` block (keep everything above it unchanged) with:

```python
        try:
            resp = self._http_get(pdf_url)
            if resp.status_code != 200:
                self._report_pdf_download_failure(
                    result.source, pdf_url, f"HTTP {resp.status_code}", f"http-{resp.status_code}"
                )
                return
            path, outcome = self._save_pdf_to_cache(self.cache, resp.content, cache_id)
            if outcome == "not-a-pdf":
                self._report_pdf_download_failure(
                    result.source, pdf_url, "the response is not a PDF", "not-a-pdf"
                )
                return
            if path is None:
                # write-failed: _warn_cache_write_failed has already spoken, and
                # it names the right cause. Saying anything more here would
                # blame the publisher for a read-only directory.
                return
            result.file_path = path
            logger.info("PDF cached to %s", path)
            self._attach_pdf_text(path, result)
        except Exception as exc:
            # The environment, not the server. A lost network or a full disk
            # fails *every* article once it starts failing, so this is one-shot
            # per (source, type) and needed no measurement to decide — unlike
            # the two server-side causes above.
            #
            # Still not recorded on the exhaustion report: all three call sites
            # return the result immediately after this, so a failure noted
            # there could never reach the report that reads it (issue #68).
            self._warn_once(
                f"pdf-download:{result.source}:{type(exc).__name__}",
                "Could not download a %s PDF (%s: %s). The URL is left on the "
                "result, but there is no file and no extracted text, and this "
                "will affect every article served this way. Further %s "
                "failures will not be repeated.",
                result.source,
                type(exc).__name__,
                exc,
                type(exc).__name__,
            )
            logger.debug("PDF download failed for %s", pdf_url, exc_info=True)
```

Now add `_report_pdf_download_failure`. **Use the variant Step 1 selected**, and put the measured numbers in its docstring.

**Variant A — worst measured rate < 5%:**

```python
    def _report_pdf_download_failure(
        self, source: str, pdf_url: str, reason: str, cause: str
    ) -> None:
        """Report a server-side PDF download failure (issue #68).

        Per article, at WARNING. The level was chosen from a measured rate
        rather than by taste, against a rule fixed before the numbers landed:
        under 5% of attempts, a per-article WARNING is affordable — a
        10,000-paper run yields under 500 lines, each naming one article an
        operator could chase — and at or above it the report collapses to one
        line per (source, cause).

        Measured by ``scripts/sample_free_pdf_urls.py``: <FILL IN THE ACTUAL
        PER-POPULATION RATES FROM /tmp/free_pdf_sample.txt HERE, e.g.
        "europepmc 0.7%, unpaywall 3.1%, biorxiv 0.0%">. Re-run it before
        revisiting this.
        """
        logger.warning(
            "Could not download the %s PDF at %s — %s. The URL is left on the "
            "result, but there is no file and no extracted text.",
            source,
            pdf_url,
            reason,
        )
        logger.debug("PDF download failed (%s) for %s", cause, pdf_url)
```

**Variant B — worst measured rate ≥ 5%:**

```python
    def _report_pdf_download_failure(
        self, source: str, pdf_url: str, reason: str, cause: str
    ) -> None:
        """Report a server-side PDF download failure (issue #68).

        Once per (source, cause), with the per-article detail at DEBUG. The
        level was chosen from a measured rate rather than by taste, against a
        rule fixed before the numbers landed: under 5% of attempts a
        per-article WARNING is affordable, and at or above it a bulk run's log
        would be drowned at exactly the moment it mattered.

        Measured by ``scripts/sample_free_pdf_urls.py``: <FILL IN THE ACTUAL
        PER-POPULATION RATES FROM /tmp/free_pdf_sample.txt HERE>. Re-run it
        before revisiting this.
        """
        self._warn_once(
            f"pdf-download:{source}:{cause}",
            "Could not download a %s PDF (%s; first seen at %s). The URL is "
            "left on the result, but there is no file and no extracted text. "
            "This is common enough that it is reported once — run with DEBUG "
            "logging to see every affected article.",
            source,
            reason,
            pdf_url,
        )
        logger.debug("PDF download failed (%s) for %s", cause, pdf_url)
```

> The `<FILL IN …>` marker is the one place this plan cannot supply a value,
> because the value is produced by Step 1's live run. Replace it with the
> actual figures before committing — a commit still containing that marker is
> a failed task.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_fulltext_service.py -v
```

Expected: all PASS. `TestPDFTextExtraction` and `TestCacheIntegration` exercise `_save_pdf_to_cache`'s caller and must be unaffected by the signature change; if one fails, the tuple unpacking is wrong.

- [ ] **Step 6: Mutation-test the write/validation distinction**

Make `_save_pdf_to_cache` return `"not-a-pdf"` for the write failure too; confirm `test_a_failed_cache_write_is_not_reported_as_a_bad_pdf` FAILS. Restore, clear `__pycache__`, re-run.

- [ ] **Step 7: Commit**

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "fix(fulltext): say when a free PDF could not be downloaded (#68)

_download_and_cache_pdf swallowed three distinct outcomes at DEBUG alike, so
with convert_pdfs=True the caller asked for text, got a bare pdf_url, and could
not tell a full disk from a publisher 404.

The exception path is one-shot per (source, type) and needed no measurement: a
lost network fails every article once it starts failing. The two server-side
causes were set from a live measurement against a rule fixed beforehand — under
5% of attempts, per-article WARNING; at or above it, one line per (source,
cause). Measured rates are in the docstring.

_save_pdf_to_cache now returns its outcome as well as its path: it returned
None for both a failed write and a failed validation, so reporting on None
alone would blame a read-only directory on the publisher's bytes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation, verification and the PR

**Files:**
- Modify: `CHANGELOG.md`, `ROADMAP.md`, `HANDOVER.md`, `docs/manual/fulltext.md`, `CLAUDE.md`

- [ ] **Step 1: CHANGELOG**

Under `## [Unreleased]`, add a `### Fixed` entry each for #68 and #72 and a **`### Changed`** entry for #79 — it moves what downstream stores, so it is not merely a fix. State plainly that many more articles now return `pdf_url`/`file_path`/extracted text instead of a bare link, that stored full text is therefore not comparable across the change, and that outbound traffic rises because PDFs previously skipped are now downloaded.

- [ ] **Step 2: ROADMAP**

- Move the `⬜ Planned | Say when a free PDF could not be downloaded` row (Issue #68) to `✅ Done`, with the measured rates.
- Move the `⬜ Planned | A bmlib bug must not hide behind a working tier` row (Issue #72) to `✅ Done`.
- Add a `✅ Done` row for #79 under **Full text**.
- Add a `✅ Done` row for `scripts/sample_free_pdf_urls.py` under **Quality & maintenance**, beside the DataBankName sampler row.

- [ ] **Step 3: HANDOVER**

- Update the header block: version stays 0.9.0, test counts change, open issues drop to **#56, #73, #78**.
- **Fix the pre-existing drift:** the "Open GitHub issues" section says "Four" and omits #78, which lives only in ROADMAP. Make the count right and list every open issue.
- Remove the #68 and #72 paragraphs from "Next up"; #56 and #73 stay.

- [ ] **Step 4: `docs/manual/fulltext.md` and `CLAUDE.md`**

Add the new log lines to the manual's `FullTextService` section, and note in `CLAUDE.md`'s test-file mapping table that `scripts/` now maps to `test_databank_sampler.py` **and** `test_free_pdf_sampler.py`. Add a short paragraph to `CLAUDE.md`'s `fulltext/` module description covering the availability allow-list.

- [ ] **Step 5: Full verification**

```bash
uv run pytest tests/ -v
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

Then the PostgreSQL half, per HANDOVER's recipe:

```bash
PGBIN=/Applications/Postgres.app/Contents/Versions/16/bin
mkdir -p /tmp/bmlpg/run
$PGBIN/initdb -D /tmp/bmlpg/data -U postgres --auth=trust
$PGBIN/pg_ctl -D /tmp/bmlpg/data \
    -o "-k /tmp/bmlpg/run -p 55432 -c listen_addresses=''" -l /tmp/bmlpg/pg.log start
$PGBIN/createdb -h /tmp/bmlpg/run -p 55432 -U postgres bmlib_test
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/bmlpg/run port=55432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py -q
```

Expected: everything passes; the counts are 1774 + the new tests.

- [ ] **Step 6: Commit and open the PR**

```bash
git add -A && git commit -m "docs: record the PDF tier fixes (#68, #72, #79)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push -u origin fix/68-72-79-pdf-download-reporting
gh pr create --base main --title "fix(fulltext): take the free PDFs on offer, and report what the PDF tier swallows (#79, #68, #72)" --body "..."
```

The PR body must include `Closes #79`, `Closes #68`, `Closes #72`, the measured tables for both #79 and #68, which variant the 5% rule selected and why, and the reviewer note that #79 is a behaviour change moving downstream stored full text.

---

## Self-Review

**Spec coverage.** Spec §A → Task 1. §B → Task 2. §C → Task 4. §D → Tasks 3 and 5. §"Verification" → Tasks 1/4/5 mutation steps and Task 6 Step 5. §"Release note" → Task 6 Step 1. §"Out of scope" carries no task, correctly.

**Placeholder scan.** One deliberate `<FILL IN …>` in Task 5 Step 4, which the plan flags explicitly as the single value it cannot supply (it is produced by Step 1's live run) and marks a commit containing it as a failed task. The `--body "..."` in Task 6 Step 6 is specified in the sentence beneath it.

**Type consistency.** `_entry_is_free` (Task 1) is called only from `_extract_free_pdf_url`. `_warn_once(key, msg, *args)` (Task 3) is called with those exact arguments in Tasks 4 and 5. `_TierFailures.on_bug` (Task 4) matches `_warn_swallowed_bug(exc)`. `_save_pdf_to_cache`'s new `tuple[str | None, str]` (Task 5) has exactly one caller, updated in the same step. `ProbeOutcome`/`probe`/`wilson`/`is_probeable`/`summarise` (Task 2) match their test uses.
