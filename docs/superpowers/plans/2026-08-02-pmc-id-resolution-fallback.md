# PMC ID Resolution Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `FullTextService` a second way to reach a PMC ID (NCBI's ID Converter) and a second place to spend one (NCBI's `efetch db=pmc`), so a paper that PMC holds but Europe PMC does not serve returns full text instead of a bare DOI link.

**Architecture:** Two new private helpers on `FullTextService`, both reached through the existing `_http_get` seam. `_resolve_pmc_id_via_idconv()` is consulted from Tier 1b only when the Europe PMC search comes back without a `pmcid`; `_fetch_ncbi_pmc()` becomes a new Tier 1c that fires for whichever PMC ID is in hand — caller-supplied or discovered — pushing the existing free-PDF tier to 1d. A shared `_normalise_pmc_id()` validates `PMC\d+` at the two points where a PMC ID becomes a URL.

**Tech Stack:** Python 3.11+, httpx (already a dependency of this module), pytest with `unittest.mock`, ruff.

**Design:** `docs/superpowers/specs/2026-08-02-pmc-id-resolution-fallback-design.md`. Read it before starting — it records why the converter runs *second*, why `_resolve_pmc_id_and_pdf_url()` is left alone, and why an efetch stub must raise.

## Global Constraints

- **AGPL-3 header** on every source file. Both files this plan touches already have one; do not disturb it.
- **Type hints** on every parameter and return; **docstrings** on every public function, class and module. These helpers are private (`_`-prefixed) but still get docstrings — every other helper in the file has one.
- **ruff**: line-length 100, target Python 3.11+. Lint with the CI-pinned version, not `.venv`'s: `uvx ruff@0.15.20 check .` and `uvx ruff@0.15.20 format --check .`.
- **`uv` only, never bare pip.** Tests: `uv run pytest tests/ -v`.
- **No new dependency.** `httpx` is already imported by `bmlib/fulltext/service.py`.
- **Positional stability:** `ncbi_api_key` is declared **last** in `FullTextService.__init__`. Downstream projects construct it positionally; any other placement lands a caller's argument in the wrong field with no error.
- **Baseline before you start:** `1114 passed, 32 skipped`. Every task ends green against that number plus whatever it added.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `bmlib/fulltext/service.py` | The retrieval chain and its fetch helpers | Modify: 3 new module constants, 1 new module function, 2 new methods, 1 new constructor parameter, wiring in `fetch_fulltext()` |
| `tests/test_fulltext_service.py` | Mocked-HTTP tests for the chain | Modify: 3 new test classes; 10 existing tests gain mocks for the new requests |
| `tests/fixtures/ncbi_pmc_stub.xml` | An efetch reply carrying no article | Create |
| `docs/manual/fulltext.md` | Reference manual for the module | Modify: tier table, constructor table, source-string table |
| `CHANGELOG.md` | Release notes | Modify: `[Unreleased] / Added` |
| `ROADMAP.md`, `HANDOVER.md` | Progress records | Modify: flip the planned row, record the session |

No new module. The two helpers belong beside `_fetch_europepmc()` and `_fetch_unpaywall()` in the `--- Fetch helpers ---` section, which is what every other source in this chain does.

---

### Task 1: The ID Converter helper

**Files:**
- Modify: `bmlib/fulltext/service.py` (constants near line 47; `__init__` at 82-114; new method in the `--- Fetch helpers ---` section after `_resolve_pmc_id_and_pdf_url`, ~line 567)
- Test: `tests/test_fulltext_service.py` (new class `TestIDConverter`, append at end of file)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `FullTextService.__init__(..., ncbi_api_key: str | None = None)` — attribute `self.ncbi_api_key`, used by Task 2 as well.
  - `FullTextService._resolve_pmc_id_via_idconv(*, doi: str | None = None, pmid: str = "") -> str | None` — used by Task 3.
  - `FullTextService._ncbi_params(**params: str) -> dict[str, str]` — adds `tool`, `email` and the optional `api_key`. Used by Task 2.
  - Module constants `NCBI_IDCONV_URL`, `EUTILS_EFETCH_URL`, `EUTILS_TOOL_NAME`, `_PMC_ID_RE` — Task 2 uses the last three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestIDConverter:
    """NCBI's ID Converter — the second source for a PMC ID.

    Europe PMC's search only reports a PMC ID when it *both* indexed the paper
    and flagged its full text as available there. The converter depends on
    neither, so it is what rescues a paper Europe PMC's index missed. It is
    third-party text on the way to a URL, and it is consulted on a path that
    already holds a free-PDF URL, so the two properties that matter are that a
    malformed id never reaches a URL and that a failure here costs nothing
    that was already found.
    """

    @staticmethod
    def _reply(**fields: object) -> MagicMock:
        """One converter record, as the API returns it."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok", "records": [fields]}
        return resp

    def test_a_pmcid_is_returned(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC7614751")):
            assert service._resolve_pmc_id_via_idconv(doi="10.1/test") == "PMC7614751"

    def test_the_pmid_is_preferred_when_both_are_known(self):
        """A PMID is an exact key; a DOI is text whose formatting is what missed."""
        service = FullTextService(email="test@example.com")
        with patch.object(
            service, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            service._resolve_pmc_id_via_idconv(doi="10.1/test", pmid="12345")

        assert mock_get.call_args.kwargs["params"]["ids"] == "12345"

    def test_the_doi_is_used_when_there_is_no_pmid(self):
        service = FullTextService(email="test@example.com")
        with patch.object(
            service, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            service._resolve_pmc_id_via_idconv(doi="10.1/test")

        assert mock_get.call_args.kwargs["params"]["ids"] == "10.1/test"

    def test_no_identifier_makes_no_request(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            assert service._resolve_pmc_id_via_idconv() is None
            mock_get.assert_not_called()

    def test_an_error_record_resolves_to_nothing(self):
        """`status: error` is how the converter reports an id it cannot map."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(status="error", errmsg="invalid article id")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_record_no_longer_live_resolves_to_nothing(self):
        """`live: "false"` means PMC no longer serves it — the fetch would fail."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(pmcid="PMC123", live="false")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_malformed_pmcid_is_refused(self):
        """It would otherwise be interpolated into a URL path unchecked."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(pmcid="../../etc/passwd")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_an_empty_record_list_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok", "records": []}
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_failed_request_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 500
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_transport_failure_is_not_raised(self):
        """It is called where a free-PDF URL is already in hand.

        Letting the exception out would leave the enclosing ``except`` to
        swallow it and skip the rest of the block — trading a working PDF tier
        for a failed converter lookup.
        """
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=RuntimeError("connection reset")):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_unparseable_json_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_the_api_key_is_sent_only_when_configured(self):
        without = FullTextService(email="test@example.com")
        with patch.object(
            without, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            without._resolve_pmc_id_via_idconv(pmid="99")
        assert "api_key" not in mock_get.call_args.kwargs["params"]

        with_key = FullTextService(email="test@example.com", ncbi_api_key="secret")
        with patch.object(
            with_key, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            with_key._resolve_pmc_id_via_idconv(pmid="99")
        assert mock_get.call_args.kwargs["params"]["api_key"] == "secret"

    def test_the_caller_is_identified_to_ncbi(self):
        """NCBI asks for tool and email on every request."""
        service = FullTextService(email="test@example.com")
        with patch.object(
            service, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            service._resolve_pmc_id_via_idconv(pmid="99")

        params = mock_get.call_args.kwargs["params"]
        assert params["tool"] == "bmlib"
        assert params["email"] == "test@example.com"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fulltext_service.py::TestIDConverter -v`
Expected: FAIL — `AttributeError: 'FullTextService' object has no attribute '_resolve_pmc_id_via_idconv'`, and `TypeError: __init__() got an unexpected keyword argument 'ncbi_api_key'` on the two key tests.

- [ ] **Step 3: Add the constants**

In `bmlib/fulltext/service.py`, add `import re` immediately after `import logging` on line 28 (the plain imports are alphabetical, and `from pathlib import Path` follows on line 29), then extend the constant block at lines 47-51:

```python
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
DOI_BASE = "https://doi.org"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"
NCBI_IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
EUTILS_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUTILS_TOOL_NAME = "bmlib"
TIMEOUT = 30.0

# A PMC ID reaches a URL path in two fetch helpers, and one of its sources is
# third-party JSON. Validated where it is used rather than where it arrives,
# so caller-supplied, Europe-PMC-supplied and converter-supplied ids are
# covered by one guard.
_PMC_ID_RE = re.compile(r"^PMC\d+$")
```

- [ ] **Step 4: Add the constructor parameter**

In `FullTextService.__init__`, append the parameter **last** and set the attribute:

```python
    def __init__(
        self,
        email: str,
        timeout: float = TIMEOUT,
        cache: FullTextCache | None = None,
        convert_pdfs: bool = True,
        ncbi_api_key: str | None = None,
    ) -> None:
```

Add to the docstring's `Args:` block, after `convert_pdfs`:

```
            ncbi_api_key: Optional NCBI API key, sent with the ID Converter and
                ``efetch`` requests. It does not change this service's pacing —
                bmlib throttles nothing — but it moves those requests into the
                key's 10 requests/second allowance instead of the 3
                requests/second shared by everything on the IP. Declared last
                so positional construction stays stable.
```

And in the body, after `self.convert_pdfs = convert_pdfs`:

```python
        self.ncbi_api_key = ncbi_api_key
```

- [ ] **Step 5: Implement the helper**

In `bmlib/fulltext/service.py`, immediately after `_resolve_pmc_id_and_pdf_url()` (before `_fetch_europepmc`):

```python
    def _ncbi_params(self, **params: str) -> dict[str, str]:
        """Add the identification NCBI asks of every caller.

        ``tool`` and ``email`` identify bmlib; ``api_key`` is sent only when
        configured, and moves the request into the key's allowance rather than
        the 3 requests/second shared by everything on the IP.
        """
        params.update(tool=EUTILS_TOOL_NAME, email=self.email)
        if self.ncbi_api_key:
            params["api_key"] = self.ncbi_api_key
        return params

    def _resolve_pmc_id_via_idconv(
        self,
        *,
        doi: str | None = None,
        pmid: str = "",
    ) -> str | None:
        """Resolve a PMC ID through NCBI's ID Converter.

        The second source for a PMC ID, consulted only when the Europe PMC
        search returned none. Europe PMC reports one only when it both indexed
        the paper and flagged its full text as available there; the converter
        depends on neither.

        Asked by PMID when there is one — an exact numeric key — and by DOI
        otherwise, since a DOI-formatting miss is one of the divergences this
        recovers.

        Returns:
            The PMC ID, or ``None`` if the converter has no live record for the
            identifier, reports an error, answers with something unusable, or
            cannot be reached. It never raises: the caller has a free-PDF URL
            in hand by this point, and an exception would cost it.
        """
        if pmid:
            ids = pmid
        elif doi:
            ids = doi
        else:
            return None

        try:
            resp = self._http_get(
                NCBI_IDCONV_URL,
                params=self._ncbi_params(ids=ids, format="json"),
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug("ID Converter HTTP %s for %s", resp.status_code, ids)
                return None

            records = resp.json().get("records") or []
            if not records:
                return None

            record = records[0]
            if record.get("status") == "error":
                logger.debug("ID Converter has no record for %s: %s", ids, record.get("errmsg"))
                return None
            # Reported as the string "false" for a record PMC no longer serves.
            if str(record.get("live", "true")).lower() == "false":
                logger.debug("ID Converter record for %s is no longer live", ids)
                return None

            pmc_id = record.get("pmcid")
            if not isinstance(pmc_id, str) or not _PMC_ID_RE.match(pmc_id):
                if pmc_id:
                    logger.warning("ID Converter returned an unusable PMC ID: %r", pmc_id)
                return None

            logger.info("PMC ID %s resolved via NCBI ID Converter for %s", pmc_id, ids)
            return pmc_id
        except Exception:
            logger.debug("ID Converter lookup failed for %s", ids, exc_info=True)
            return None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fulltext_service.py::TestIDConverter -v`
Expected: PASS, 13 tests.

Then the whole suite, which must be unchanged at this point — nothing calls the new helper yet:
Run: `uv run pytest tests/ -q`
Expected: `1127 passed, 32 skipped`.

- [ ] **Step 7: Lint**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
Expected: `All checks passed!` and `92 files already formatted`.

- [ ] **Step 8: Commit**

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "feat(fulltext): resolve a PMC ID through NCBI's ID Converter

Europe PMC reports a PMC ID only when it both indexed the paper and flagged
its full text as available there. The converter depends on neither, and is
the authoritative DOI/PMID to PMCID mapping.

Nothing calls it yet. It never raises — the call site it is destined for
already holds a free-PDF URL, and an exception there would cost it — and it
refuses a pmcid that does not match PMC\\d+, since the value is third-party
text on its way into a URL path.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The NCBI PMC fetch helper

**Files:**
- Modify: `bmlib/fulltext/service.py` (new module function beside `_extract_free_pdf_url`, ~line 58; new method after `_fetch_europepmc`, ~line 588; `_fetch_europepmc` itself at 569-587)
- Create: `tests/fixtures/ncbi_pmc_stub.xml`
- Test: `tests/test_fulltext_service.py` (new class `TestNCBIPMCFetch`, append at end)

**Interfaces:**
- Consumes: `EUTILS_EFETCH_URL`, `_PMC_ID_RE`, `self._ncbi_params()` from Task 1.
- Produces:
  - `_normalise_pmc_id(pmc_id: str) -> str` — module-level, raises `FullTextError` on a value that is not `PMC\d+` after prefixing.
  - `FullTextService._fetch_ncbi_pmc(pmc_id: str) -> tuple[str, bool]` — returns `(html, has_body)`, the same contract as `_fetch_europepmc()`. Used by Task 3.

- [ ] **Step 1: Create the stub fixture**

`tests/fixtures/ncbi_pmc_stub.xml` — what efetch answers for an article whose publisher does not release XML. It is HTTP 200 and parses cleanly, which is exactly the hazard:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<pmc-articleset>
  <Reply>The publisher of this article does not allow downloading of the full text in XML form.</Reply>
</pmc-articleset>
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestNCBIPMCFetch:
    """NCBI's own copy of a PMC article, via ``efetch db=pmc``.

    Europe PMC's ``fullTextXML`` endpoint serves the corpus its ``inEPMC``
    flag describes. When that flag says no — or the article store simply does
    not have it — NCBI is the source that does, and it is reachable with the
    same PMC ID.
    """

    def test_full_text_is_parsed(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            html, has_body = service._fetch_ncbi_pmc("PMC123")

        assert has_body is True
        assert "<h1>" in html

    def test_the_numeric_id_is_sent(self):
        """efetch's documented form for db=pmc is the digits alone."""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp) as mock_get:
            service._fetch_ncbi_pmc("PMC123")

        params = mock_get.call_args.kwargs["params"]
        assert params["id"] == "123"
        assert params["db"] == "pmc"

    def test_a_bare_numeric_id_is_accepted(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp) as mock_get:
            service._fetch_ncbi_pmc("123")

        assert mock_get.call_args.kwargs["params"]["id"] == "123"

    def test_a_malformed_pmc_id_never_reaches_a_url(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            with pytest.raises(FullTextError):
                service._fetch_ncbi_pmc("../../etc/passwd")
            mock_get.assert_not_called()

    def test_a_stub_with_no_article_raises(self):
        """A non-OA reply parses cleanly into nothing.

        Returned rather than raised, it would be promoted to the last-resort
        abstract — near-empty HTML labelled ``content_kind="abstract"``, worse
        than the DOI link it displaced and permanent for a caller that
        persists results.
        """
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "ncbi_pmc_stub.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            with pytest.raises(FullTextError):
                service._fetch_ncbi_pmc("PMC123")

    def test_a_body_less_article_with_an_abstract_is_returned(self):
        """Front matter carrying a real abstract is worth having.

        This is the case the stub guard must not swallow: it is the same
        body-less document Europe PMC serves, and the caller holds it back as
        a last resort exactly as it does there.
        """
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            html, has_body = service._fetch_ncbi_pmc("PMC123")

        assert has_body is False
        assert html

    def test_a_failed_request_raises(self):
        resp = MagicMock()
        resp.status_code = 503

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            with pytest.raises(FullTextError):
                service._fetch_ncbi_pmc("PMC123")

    def test_the_api_key_is_sent_only_when_configured(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        without = FullTextService(email="test@example.com")
        with patch.object(without, "_http_get", return_value=resp) as mock_get:
            without._fetch_ncbi_pmc("PMC123")
        assert "api_key" not in mock_get.call_args.kwargs["params"]

        with_key = FullTextService(email="test@example.com", ncbi_api_key="secret")
        with patch.object(with_key, "_http_get", return_value=resp) as mock_get:
            with_key._fetch_ncbi_pmc("PMC123")
        assert mock_get.call_args.kwargs["params"]["api_key"] == "secret"


class TestPMCIDValidation:
    """``PMC\\d+`` enforced where the id becomes a URL, not where it arrives."""

    def test_a_bare_number_is_prefixed(self):
        assert _normalise_pmc_id("123") == "PMC123"

    def test_a_prefixed_id_is_unchanged(self):
        assert _normalise_pmc_id("PMC123") == "PMC123"

    @pytest.mark.parametrize(
        "value",
        ["", "PMC", "PMC12a", "pmc123", "PMC123/../etc", "PMC 123", "http://x/PMC123"],
    )
    def test_anything_else_raises(self, value):
        with pytest.raises(FullTextError):
            _normalise_pmc_id(value)

    def test_europe_pmc_validates_too(self):
        """One guard, both fetch helpers — Europe PMC's id is third-party too."""
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            with pytest.raises(FullTextError):
                service._fetch_europepmc("../../etc/passwd")
            mock_get.assert_not_called()
```

Add `_normalise_pmc_id` to the import at the top of the test file:

```python
from bmlib.fulltext.service import (
    FullTextError,
    FullTextService,
    _normalise_pmc_id,
    _sanitize_identifier,
)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fulltext_service.py::TestNCBIPMCFetch tests/test_fulltext_service.py::TestPMCIDValidation -v`
Expected: FAIL at collection — `ImportError: cannot import name '_normalise_pmc_id'`.

- [ ] **Step 4: Implement the validator**

In `bmlib/fulltext/service.py`, after `_extract_free_pdf_url()`:

```python
def _normalise_pmc_id(pmc_id: str) -> str:
    """Prefix a bare numeric PMC ID and validate the result.

    A PMC ID is interpolated into a URL path by both PMC fetch helpers, and
    reaches them from three places: the caller, Europe PMC's search response
    and NCBI's ID Converter. Only the first is under bmlib's control, so the
    check lives at the point of use and covers all three.

    Args:
        pmc_id: A PMC ID, with or without the ``PMC`` prefix.

    Returns:
        The prefixed, validated ID.

    Raises:
        FullTextError: If the value is not ``PMC`` followed by digits. Every
            tier already catches this and moves on, so a malformed ID costs a
            log line rather than a request.
    """
    normalized = pmc_id if pmc_id.startswith("PMC") else f"PMC{pmc_id}"
    if not _PMC_ID_RE.match(normalized):
        raise FullTextError(f"Not a usable PMC ID: {pmc_id!r}")
    return normalized
```

- [ ] **Step 5: Route `_fetch_europepmc` through it**

Replace the first line of `_fetch_europepmc()`'s body:

```python
        normalized = pmc_id if pmc_id.startswith("PMC") else f"PMC{pmc_id}"
```

with:

```python
        normalized = _normalise_pmc_id(pmc_id)
```

- [ ] **Step 6: Implement the fetch helper**

Immediately after `_fetch_europepmc()`:

```python
    def _fetch_ncbi_pmc(self, pmc_id: str) -> tuple[str, bool]:
        """Fetch a PMC article from NCBI's own copy via E-utilities ``efetch``.

        Europe PMC's ``fullTextXML`` serves the corpus its ``inEPMC`` flag
        describes; NCBI serves PMC itself. For an article PMC holds and Europe
        PMC does not, this is the only source that answers.

        Returns:
            A tuple of the rendered HTML and whether the document had a body,
            as for :meth:`_fetch_europepmc`.

        Raises:
            FullTextError: On a bad ID, a non-200 response, or a reply
                carrying no article at all. That last case is efetch's answer
                for an article whose publisher does not release XML: it is
                HTTP 200 and parses cleanly into a document with no body *and*
                no abstract. Returned rather than raised, it would be promoted
                to the last-resort abstract and become near-empty HTML
                labelled as one.
        """
        normalized = _normalise_pmc_id(pmc_id)
        resp = self._http_get(
            EUTILS_EFETCH_URL,
            params=self._ncbi_params(
                db="pmc",
                id=normalized.removeprefix("PMC"),
                retmode="xml",
            ),
            headers={"Accept": "application/xml"},
        )
        if resp.status_code != 200:
            raise FullTextError(f"NCBI PMC HTTP {resp.status_code}")

        article, html = JATSParser(resp.content, known_pmc_id=normalized).parse_with_html()
        if not article.has_body and not article.abstract_sections:
            raise FullTextError(f"NCBI PMC returned no article content for {normalized}")
        return html, article.has_body
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fulltext_service.py::TestNCBIPMCFetch tests/test_fulltext_service.py::TestPMCIDValidation -v`
Expected: PASS, 8 + 10 tests.

Run: `uv run pytest tests/ -q`
Expected: `1145 passed, 32 skipped`. Still nothing wired into the chain, so no existing test moves.

- [ ] **Step 8: Lint and commit**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py tests/fixtures/ncbi_pmc_stub.xml
git commit -m "feat(fulltext): fetch a PMC article from NCBI's own copy

efetch db=pmc reaches PMC directly, so it answers for an article Europe PMC's
fullTextXML does not serve — the case its inEPMC flag describes.

A non-OA reply is HTTP 200 and parses cleanly into a document with neither
body nor abstract. Returned, it would be promoted to the last-resort abstract
and become near-empty HTML labelled as one, so it raises instead. A genuine
body-less article carrying a real abstract still returns.

PMC\\d+ is now enforced in both PMC fetch helpers, at the point where the id
becomes a URL path rather than at each of the three places it arrives from.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire both into the chain

**Files:**
- Modify: `bmlib/fulltext/service.py` (`fetch_fulltext()` docstring at 140-148 and body at 168-238)
- Test: `tests/test_fulltext_service.py` (10 existing tests updated; new class `TestPMCIDFallbackChain`)

**Interfaces:**
- Consumes: `_resolve_pmc_id_via_idconv()` (Task 1) and `_fetch_ncbi_pmc()` (Task 2).
- Produces: `FullTextResult.source == "ncbi_pmc"` — a new value in the public source vocabulary, documented in Task 4.

- [ ] **Step 1: Write the failing chain tests**

Append to `tests/test_fulltext_service.py`:

```python
class TestPMCIDFallbackChain:
    """Where the two new steps sit in the chain, and what they must not cost.

    The order is the load-bearing part. Europe PMC's search returns the PMC ID
    *and* the free-PDF URL in one request, so the converter is consulted only
    after that search comes back without an id — never before it.
    """

    @staticmethod
    def _search(pmcid: str | None = None, pdf_url: str | None = None) -> MagicMock:
        hit: dict = {}
        if pmcid:
            hit["pmcid"] = pmcid
            hit["inEPMC"] = "Y"
        if pdf_url:
            hit["fullTextUrlList"] = {
                "fullTextUrl": [{"documentStyle": "pdf", "availability": "Free", "url": pdf_url}]
            }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"resultList": {"result": [hit] if hit else []}}
        return resp

    @staticmethod
    def _idconv(pmcid: str | None = None) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        records = [{"pmcid": pmcid}] if pmcid else []
        resp.json.return_value = {"status": "ok", "records": records}
        return resp

    @staticmethod
    def _xml(name: str = "sample_article.xml") -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / name).read_bytes()
        return resp

    def test_the_converter_rescues_a_search_that_found_nothing(self):
        """Europe PMC's index missed it; NCBI's mapping did not."""
        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(), self._idconv("PMC999"), self._xml()],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"

    def test_the_converter_is_not_consulted_when_the_search_found_an_id(self):
        """It costs a request, so it is spent only where the service gave up."""
        service = FullTextService(email="test@example.com")
        with patch.object(
            service, "_http_get", side_effect=[self._search(pmcid="PMC1"), self._xml()]
        ) as mock_get:
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc"
        assert mock_get.call_count == 2

    def test_a_converter_failure_does_not_cost_the_free_pdf_url(self):
        """The search already paid for that URL before the converter ran.

        No ``identifier``, so there is no cache and ``_download_and_cache_pdf``
        returns before making a request — two mocks, not three.
        """
        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                self._search(pdf_url="https://europepmc.org/x.pdf"),
                RuntimeError("connection reset"),
            ],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc_pdf"
        assert result.pdf_url == "https://europepmc.org/x.pdf"

    def test_ncbi_is_tried_for_a_caller_supplied_id(self):
        """The gap is the same whoever found the id — Tier 1a gets it too."""
        epmc_404 = MagicMock()
        epmc_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=[epmc_404, self._xml()]):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "ncbi_pmc"
        assert result.content_kind == "fulltext"

    def test_ncbi_is_tried_for_a_converter_discovered_id(self):
        epmc_404 = MagicMock()
        epmc_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(), self._idconv("PMC999"), epmc_404, self._xml()],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "ncbi_pmc"

    def test_ncbi_full_text_beats_the_free_pdf_beneath_it(self):
        """Structured JATS outranks a PDF that needs an optional extra to read."""
        epmc_404 = MagicMock()
        epmc_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                epmc_404,
                self._xml(),
                self._search(pdf_url="https://europepmc.org/x.pdf"),
            ],
        ) as mock_get:
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "ncbi_pmc"
        # The PDF-recovery search was never reached: NCBI answered first.
        assert mock_get.call_count == 2

    def test_an_ncbi_stub_does_not_become_the_last_resort_abstract(self):
        """The stub carries no text; the DOI link it would displace is better."""
        epmc_404 = MagicMock()
        epmc_404.status_code = 404
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                epmc_404,
                self._xml("ncbi_pmc_stub.xml"),
                self._search(),
                unpaywall_404,
            ],
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "doi"
        assert result.html is None

    def test_ncbi_is_not_tried_without_a_pmc_id(self):
        """Neither the caller nor either resolver produced one."""
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(), self._idconv(), unpaywall_404],
        ) as mock_get:
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "doi"
        assert mock_get.call_count == 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_fulltext_service.py::TestPMCIDFallbackChain -v`
Expected: FAIL — 8 tests. The converter ones fail on `StopIteration` or a `doi` result; the NCBI ones on `assert 'doi' == 'ncbi_pmc'`.

- [ ] **Step 3: Wire Tier 1b′ and Tier 1c**

In `fetch_fulltext()`, replace lines 168-238 (from `# Tier 1a` through the existing Tier 1c block) with:

```python
        # Tier 1a: Europe PMC with known PMC ID
        xml_failed = False
        # Whichever PMC ID we end up holding — the caller's or a resolved one.
        # NCBI's tier below spends it, so it is set before the fetch that may
        # raise, not after.
        resolved_pmc_id: str | None = pmc_id
        if pmc_id:
            try:
                html, has_body = self._fetch_europepmc(pmc_id)
                if has_body:
                    logger.info("Full text retrieved from Europe PMC for %s", pmc_id)
                    self._cache_html(html, cache_id)
                    return FullTextResult(source="europepmc", html=html, content_kind="fulltext")
                logger.info("Europe PMC XML for %s has no body — looking further", pmc_id)
                if abstract_only is None:
                    abstract_only = FullTextResult(
                        source="europepmc", html=html, content_kind="abstract"
                    )
                # Treated as a failure so the free-PDF lookup below still runs.
                xml_failed = True
            except Exception:
                logger.debug("Europe PMC failed for %s", pmc_id, exc_info=True)
                xml_failed = True

        # Tier 1b: Discover PMC ID via Europe PMC search, then fetch XML
        pdf_render_url: str | None = None
        if not pmc_id and (doi or pmid):
            try:
                discovered_pmc_id, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi, pmid=pmid
                )
                # Tier 1b′: the search reports a PMC ID only for what Europe PMC
                # both indexed and holds. NCBI's converter depends on neither,
                # and is asked second because that one search also returned the
                # free-PDF URL Tier 1d needs.
                if not discovered_pmc_id:
                    discovered_pmc_id = self._resolve_pmc_id_via_idconv(doi=doi, pmid=pmid)
                if discovered_pmc_id:
                    resolved_pmc_id = discovered_pmc_id
                    html, has_body = self._fetch_europepmc(discovered_pmc_id)
                    if has_body:
                        logger.info(
                            "Full text retrieved from Europe PMC via discovered %s",
                            discovered_pmc_id,
                        )
                        self._cache_html(html, cache_id)
                        return FullTextResult(
                            source="europepmc", html=html, content_kind="fulltext"
                        )
                    logger.info(
                        "Europe PMC XML for discovered %s has no body — looking further",
                        discovered_pmc_id,
                    )
                    if abstract_only is None:
                        abstract_only = FullTextResult(
                            source="europepmc", html=html, content_kind="abstract"
                        )
            except Exception:
                logger.debug(
                    "Europe PMC discovery failed for doi=%s pmid=%s",
                    doi,
                    pmid,
                    exc_info=True,
                )

        # Tier 1c: NCBI's own copy, for whichever PMC ID we hold. Reaching here
        # means Europe PMC gave no body for it — it serves the corpus its
        # inEPMC flag describes, and NCBI serves PMC itself. Ahead of the PDF
        # tier because structured JATS beats a PDF that needs bmlib[pdf] to
        # read at all.
        if resolved_pmc_id:
            try:
                html, has_body = self._fetch_ncbi_pmc(resolved_pmc_id)
                if has_body:
                    logger.info("Full text retrieved from NCBI PMC for %s", resolved_pmc_id)
                    self._cache_html(html, cache_id)
                    return FullTextResult(source="ncbi_pmc", html=html, content_kind="fulltext")
                logger.info("NCBI PMC XML for %s has no body — looking further", resolved_pmc_id)
                if abstract_only is None:
                    abstract_only = FullTextResult(
                        source="ncbi_pmc", html=html, content_kind="abstract"
                    )
            except Exception:
                logger.debug("NCBI PMC failed for %s", resolved_pmc_id, exc_info=True)

        # When XML failed with a known PMC ID, search for PDF render URL
        if xml_failed and not pdf_render_url and (doi or pmid):
            try:
                _, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi,
                    pmid=pmid,
                )
            except Exception:
                logger.debug("PDF URL resolution failed", exc_info=True)

        # Tier 1d: Europe PMC PDF render (when XML unavailable but free PDF exists)
        if pdf_render_url:
            logger.info("PDF available from Europe PMC render: %s", pdf_render_url)
            result = FullTextResult(source="europepmc_pdf", pdf_url=pdf_render_url)
            self._download_and_cache_pdf(pdf_render_url, cache_id, result)
            return self._with_abstract_fallback(result, abstract_only)
```

- [ ] **Step 4: Update both tier listings in the docstrings**

The module docstring at lines 17-24 also enumerates the tiers. Replace it:

```python
"""Full-text retrieval service with multi-tier fallback chain.

Tier 1a: Europe PMC XML -> JATS parser -> HTML
Tier 1b: Discover PMC ID via search, then Europe PMC XML
Tier 1b': Discover PMC ID via NCBI's ID Converter when the search found none
Tier 1c: NCBI PMC efetch for whichever PMC ID was resolved
Tier 1d: Europe PMC PDF render URL (when XML unavailable but free PDF exists)
Tier 2:  Unpaywall -> open-access PDF URL
Tier 3:  DOI resolution -> publisher website URL
"""
```

Then replace the `Tries:` block in `fetch_fulltext()` (lines 140-148):

```
        Tries:
          Cache: check disk cache for HTML/PDF (if identifier given)
          0.  Known sources from fetcher (JATS XML > PDF > HTML)
          1a. Europe PMC XML (known PMC ID)
          1b. Discover PMC ID via Europe PMC search, then fetch XML
          1b'. Discover PMC ID via NCBI's ID Converter when the search
               reported none, then fetch XML
          1c. NCBI PMC efetch for whichever PMC ID was resolved
          1d. Europe PMC PDF render URL (free PDF when XML unavailable)
          2.  Unpaywall PDF URL
          3.  DOI / PubMed URL fallback
        """
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/test_fulltext_service.py::TestPMCIDFallbackChain -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Update the existing tests that pin request sequences**

Run: `uv run pytest tests/test_fulltext_service.py -v`
Expected: failures and — worse — passes for the wrong reason. Ten tests mock an exact sequence and now see extra requests. **Do not let them pass by coincidence:** an exhausted `side_effect` raises `StopIteration`, which is an `Exception` and gets swallowed by the new guards, and a search-shaped mock read as a converter reply resolves to `None` by the ordinary rules. Every mock must name the request it answers.

Add these two helpers at module level in the test file, just below `FIXTURES`:

```python
def _idconv_miss() -> MagicMock:
    """NCBI's ID Converter with no record for the identifier."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "ok", "records": []}
    return resp


def _ncbi_miss() -> MagicMock:
    """NCBI's efetch with nothing for this PMC ID."""
    resp = MagicMock()
    resp.status_code = 404
    return resp
```

Then apply exactly these edits:

| Test | Current `side_effect` | New `side_effect` |
|---|---|---|
| `TestFetchEuropePMC::test_404_falls_through` | `[mock_404, mock_search_no_pdf, mock_unpaywall_404]` | `[mock_404, _ncbi_miss(), mock_search_no_pdf, mock_unpaywall_404]` |
| `TestDiscoverPMCID::test_not_in_epmc_falls_through` | `[mock_search, mock_unpaywall_404]` | `[mock_search, _idconv_miss(), mock_unpaywall_404]` |
| `TestFetchUnpaywall::test_success` | `[mock_pmc_404, mock_search_no_pdf, mock_unpaywall]` | `[mock_pmc_404, _ncbi_miss(), mock_search_no_pdf, mock_unpaywall]` |
| `TestFetchDOIFallback::test_no_pmc_no_unpaywall` | `[mock_search_empty, mock_unpaywall_404]` | `[mock_search_empty, _idconv_miss(), mock_unpaywall_404]` |
| `TestCacheIntegration::test_pdf_downloaded_and_cached` | `[mock_search_empty, mock_unpaywall, mock_pdf]` | `[mock_search_empty, _idconv_miss(), mock_unpaywall, mock_pdf]` |
| `TestCacheIntegration::test_invalid_pdf_rejected_keeps_url` | `[mock_search_empty, mock_unpaywall, mock_pdf]` | `[mock_search_empty, _idconv_miss(), mock_unpaywall, mock_pdf]` |
| `TestBodylessJATS::test_used_as_last_resort` | `[mock_xml, mock_search, mock_search]` | `[mock_xml, mock_search, _idconv_miss(), mock_search]` |
| `TestBodylessJATS::test_last_resort_carries_the_resolved_link` | `[mock_xml, mock_search, mock_search]` | `[mock_xml, mock_search, _idconv_miss(), mock_search]` |
| `TestBodylessJATS::test_last_resort_abstract_is_never_cached` | `[mock_xml, mock_search, mock_search]` | `[mock_xml, mock_search, _idconv_miss(), mock_search]` |
| `TestBodylessEuropePMC::test_known_pmc_id_falls_through_to_the_free_pdf` | `[self._bodyless(), self._search(pdf_url=...), pdf]` | `[self._bodyless(), _ncbi_miss(), self._search(pdf_url=...), pdf]` |
| `TestBodylessEuropePMC::test_known_pmc_id_body_less_xml_is_not_cached` | `[self._bodyless(), self._search(), self._search()]` | `[self._bodyless(), _ncbi_miss(), self._search(), self._search()]` |
| `TestBodylessEuropePMC::test_discovered_pmc_id_body_less_xml_is_not_full_text` | `[self._search(pmcid="PMC999"), self._bodyless(), unpaywall]` | `[self._search(pmcid="PMC999"), self._bodyless(), _ncbi_miss(), unpaywall]` |

In the three `TestBodylessJATS` cases the trailing `mock_search` is the Unpaywall response, not a second search — a 200 whose JSON has no `best_oa_location`, which raises and falls to Tier 3. Leave it where it is; only the converter reply is inserted.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: `1153 passed, 32 skipped`.

If anything still fails, read the failure before changing a mock: the tier order is `1a → 1b → 1b′ → 1c → PDF recovery → 1d`, and the request count per test follows from it.

- [ ] **Step 8: Lint and commit**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

```bash
git add bmlib/fulltext/service.py tests/test_fulltext_service.py
git commit -m "feat(fulltext): resolve PMC ids from NCBI, and read PMC from it

Closes #47.

Tier 1b now consults NCBI's ID Converter when Europe PMC's search reports no
PMC ID — second, never first, because that one search also returns the
free-PDF URL the render tier needs.

A new Tier 1c reads NCBI's own copy for whichever PMC ID is in hand, the
caller's or a discovered one, and sits ahead of the free-PDF tier because
structured JATS beats a PDF that needs bmlib[pdf] to read at all. The
free-PDF tier renumbers to 1d.

Ten tests pinned exact request sequences and now see additional requests.
Left alone they would have passed for the wrong reason — an exhausted
side_effect raises StopIteration, which the new guards swallow — so each
gained a mock naming the request it answers.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Documentation

**Files:**
- Modify: `docs/manual/fulltext.md` (line 3 summary; constructor table ~line 195; tier table 213-224; source table ~279-286)
- Modify: `CHANGELOG.md` (`[Unreleased]` / `### Added`)
- Modify: `ROADMAP.md` (the `⬜ Planned | Second source for PMC ID resolution` row)
- Modify: `HANDOVER.md` (open-issues section, "Deliberate non-fixes")

**Interfaces:**
- Consumes: everything from Tasks 1-3. No code changes.

- [ ] **Step 1: Update the manual's tier table**

In `docs/manual/fulltext.md`, the retrieval-chain table currently ends Tier 1b with "the PMC ID is used only if `inEPMC == \"Y\"`". Replace the `Tier 1b`, `PDF-URL recovery` and `Tier 1c` rows with:

```markdown
| Tier 1b | `pmc_id` **not** given, and `doi` or `pmid` given | Europe PMC search (`resultType=core&pageSize=1`, query `DOI:{doi}` else `EXT_ID:{pmid}`); the PMC ID is used only if `inEPMC == "Y"`, then fetched as in 1a | `"europepmc"` |
| Tier 1b′ | The search reported no PMC ID | NCBI's [ID Converter](https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/), asked by PMID when there is one and DOI otherwise. Consulted *second* — the Europe PMC search returns the PMC ID and the free-PDF URL in one request, so asking the converter first would cost a request on every lookup or forfeit that URL. A record that is `status: error`, not `live`, or carries a PMC ID failing `PMC\d+` resolves to nothing | — |
| Tier 1c | A PMC ID is in hand — the caller's, or one either resolver found — and Europe PMC gave no body for it | `GET eutils…/efetch.fcgi?db=pmc&id={digits}&retmode=xml`, parsed by `JATSParser`. Europe PMC serves the corpus its `inEPMC` flag describes; NCBI serves PMC itself. A reply carrying neither body nor abstract — efetch's answer for an article whose publisher does not release XML — is treated as a failure, not as an abstract | `"ncbi_pmc"` |
| PDF-URL recovery | Tier 1a failed and no render URL known yet | Re-run the same search purely to obtain a PDF URL | — |
| Tier 1d | A free PDF render URL was found | Take the `fullTextUrlList` entry with `documentStyle == "pdf"` and `availability == "Free"`; download and cache it | `"europepmc_pdf"` |
```

- [ ] **Step 2: Update the manual's constructor and source tables**

Add to the constructor parameter table:

```markdown
| `ncbi_api_key` | `str \| None` | `None` | Optional NCBI API key, sent with the Tier 1b′ and Tier 1c requests. Moves them into the key's 10 requests/second allowance instead of the 3/s shared by everything on the IP. It does **not** change bmlib's pacing — the package still throttles nothing. Declared last, so positional construction stays stable |
```

Add to the `result.source` table, after the `"europepmc_pdf"` row:

```markdown
| `"ncbi_pmc"` | Tier 1c — NCBI's own copy of the PMC article, via E-utilities `efetch`. Distinct from a Tier 0 entry whose fetcher named itself `"pmc"` | `html` |
```

Update the page's opening summary (line 3) so the chain it names includes NCBI: `(caller-supplied sources → Europe PMC → NCBI PMC → Unpaywall → DOI/PubMed)`.

- [ ] **Step 3: Add the CHANGELOG entry**

Under `## [Unreleased]` / `### Added` in `CHANGELOG.md`, above the existing `<DataBankList>` entry:

```markdown
- **`fulltext`: a second source for PMC ID resolution, and NCBI as a full-text
  tier.** `FullTextService` could reach a PMC ID exactly one way — Europe PMC's
  search, gated on `inEPMC == "Y"`, which requires Europe PMC *both* to have
  indexed the paper and to hold its full text. A paper in PMC failing either
  condition skipped Tiers 1a/1b and fell through to Unpaywall or a bare DOI
  link. Two changes close that:

  `_resolve_pmc_id_via_idconv()` asks NCBI's ID Converter — the authoritative
  DOI/PMID→PMCID mapping, which depends on neither condition — but only when
  the Europe PMC search reported no PMC ID. Second, never first: that one
  search also returns the free-PDF URL the render tier needs, so asking the
  converter first would cost a request on every lookup or forfeit that URL. It
  is asked by PMID when there is one, DOI otherwise, and never raises.

  `_fetch_ncbi_pmc()` becomes a new **Tier 1c**, reading NCBI's own copy via
  E-utilities `efetch` for whichever PMC ID is in hand — the caller's or a
  discovered one. Europe PMC serves the corpus its `inEPMC` flag describes;
  NCBI serves PMC itself, so this answers where Europe PMC cannot. It sits
  ahead of the free-PDF tier (renumbered to **1d**) because structured JATS
  beats a PDF that needs the optional `bmlib[pdf]` extra to read at all. An
  efetch reply carrying neither body nor abstract — what a publisher who does
  not release XML produces — raises rather than becoming a near-empty
  last-resort abstract.

  New constructor parameter `ncbi_api_key`, **declared last** for positional
  stability, sent with both NCBI requests. As with
  `TransparencyAnalyzer.pubmed_api_key` it changes which NCBI allowance the
  requests draw on, not bmlib's own pacing — the package still throttles
  nothing.

  **Moves stored values, not behind a flag:** `FullTextResult.source` gains
  `"ncbi_pmc"`, and results that were `content_kind="abstract"` or a bare
  `web_url` can now be `"fulltext"`. A caller who supplies `pmc_id` whose
  Europe PMC XML fails, or looks up an identifier Europe PMC cannot resolve,
  pays one or two extra requests in exactly the cases that previously ended at
  Unpaywall or Tier 3. Design:
  `docs/superpowers/specs/2026-08-02-pmc-id-resolution-fallback-design.md`.
```

- [ ] **Step 4: Flip the ROADMAP row**

Replace the `⬜ Planned | Second source for PMC ID resolution` row added earlier in this session with:

```markdown
| ✅ Done | Second source for PMC ID resolution | Issue #47 — `_resolve_pmc_id_and_pdf_url()` read a PMC ID only from Europe PMC's search, gated on `inEPMC == "Y"`, so a paper in PMC that Europe PMC had not indexed (or had not flagged) skipped Tiers 1a/1b entirely. NCBI's ID Converter is now consulted when that search reports no id — second, never first, since the one search also carries the free-PDF URL Tier 1d needs — and a new Tier 1c reads NCBI's own copy via `efetch` for whichever PMC ID is in hand, caller-supplied or discovered, ahead of the free-PDF tier because structured JATS beats a PDF needing `bmlib[pdf]`. An efetch stub carrying no article raises rather than becoming a last-resort abstract. New `ncbi_api_key`, declared last (unreleased) |
```

- [ ] **Step 5: Update HANDOVER**

In `HANDOVER.md`: change the open-issues section to say **None** again (keeping the closed-issue paragraph beneath it and adding #47 to it), add this work to the "Unreleased since 0.6.0" list, and add one entry to "Deliberate non-fixes":

```markdown
- **NCBI's ID Converter is consulted *after* the Europe PMC search, not
  before.** The search returns the PMC ID **and** the free-PDF URL that feeds
  Tier 1d in a single request. Querying the converter first would either cost
  a second HTTP request on every lookup or forfeit that URL — and a deleted
  prior branch did exactly that, which is why issue #47 recorded the ordering
  as the part to invert. Pinned by
  `test_the_converter_is_not_consulted_when_the_search_found_an_id`.
- **A converter-discovered PMC ID is tried at Europe PMC even when the search
  hit said `inEPMC="N"`.** For that sub-case Europe PMC has already said it
  lacks the full text, so the attempt is near-certainly a 404 before NCBI gets
  the ID. Believing the flag needs a third value out of
  `_resolve_pmc_id_and_pdf_url()` — the multi-element tuple PR #42 spent a
  whole change removing from this module's neighbour — plus the state to carry
  it, and a stale flag is one of the reasons the converter exists. Deferred
  deliberately: revisit only if it measures as a real cost in a bulk run.
- **`_fetch_ncbi_pmc()` raises on a reply with neither body nor abstract.**
  efetch answers an article whose publisher does not release XML with a stub
  that is HTTP 200 and parses cleanly. Returned rather than raised, the
  body-less machinery promotes it to `abstract_only` and the caller gets
  near-empty HTML labelled `content_kind="abstract"` — worse than the DOI link
  it displaced, and permanent for anything persisting results. A genuine
  body-less article carrying a real abstract still returns. Pinned by
  `test_a_stub_with_no_article_raises` and
  `test_a_body_less_article_with_an_abstract_is_returned`.
```

- [ ] **Step 6: Verify the docs against the code**

Run: `uv run pytest tests/ -q && uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
Expected: `1153 passed, 32 skipped`, both ruff commands clean.

Re-read the manual's tier table against `fetch_fulltext()` and confirm each row's condition and source string matches the code. Documentation drift is treated as a regression in this repo, not expected staleness.

- [ ] **Step 7: Commit**

```bash
git add docs/manual/fulltext.md CHANGELOG.md ROADMAP.md HANDOVER.md
git commit -m "docs: record NCBI id resolution and the PMC efetch tier

The manual's tier table gains 1b' and 1c and renumbers the free-PDF tier to
1d; the constructor table gains ncbi_api_key and the source table gains
ncbi_pmc, which is deliberately not the 'pmc' a Tier 0 fetcher can emit.

CHANGELOG names the two stored values that move: source gains a value, and
results that were abstract-only or a bare link can now be full text.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification checklist

Before opening the PR:

- [ ] `uv run pytest tests/ -q` → `1153 passed, 32 skipped`
- [ ] `uvx ruff@0.15.20 check .` → clean
- [ ] `uvx ruff@0.15.20 format --check .` → clean
- [ ] No test passes because an exhausted `side_effect` was swallowed — each of the 12 updated sequences names every request it answers
- [ ] `ncbi_api_key` is the **last** parameter of `FullTextService.__init__`
- [ ] The manual's tier table matches `fetch_fulltext()` row for row
- [ ] PR body links issue #47 with `Closes #47`
