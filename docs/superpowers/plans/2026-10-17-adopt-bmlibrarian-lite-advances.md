# Adopt bmlibrarian_lite's advances into bmlib — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **The skills apply to Tasks B1-B3 only.** WP-B4 to WP-B11 are scoped here, not planned: each becomes its own plan, with its own bmlib issue, when it is picked up.

**Goal:** Bring into bmlib the capabilities and fixes that bmlibrarian_lite has and bmlib lacks, and add the seams that let bmlibrarian_lite's desktop depend on bmlib instead of carrying its own copies. Compared: lite at `ca9de635` (on lite's `master`, merged as `d3ae21db` by hherb/bmlibrarian_lite#562) against bmlib at `f4b7c52`.

**Architecture:**
- **The `B` prefix** marks an item of this plan (B for "into bmlib"; lite's plan in the other direction numbers its own). `B2` is never an issue number.
- Three detailed tasks come first:
  - B1, an injectable HTTP client (the seam every consumer needs);
  - B2, the Unpaywall landing-page defect;
  - B3, bot-wall challenge pages: measured first, then labelled where they are met.
- The rest are work packages (WP-B4 to WP-B11): the wrong-article PDF check, new full-text sources, polite pacing, transparency and JATS rules lite has measured, the FFI crate that lets lite's Swift and Android apps call bmlib's Rust port, and a vendorable conformance suite.
- Where lite already has a cross-platform contract JSON for a rule, bmlib takes that file as its test fixture (copied under `tests/data/contracts/`, with its provenance and SHA-256 recorded), so one table states the rule in both repositories.

**Tech Stack:** Python ≥ 3.11, httpx, pytest; Rust (`rust/bmlib`) for each item's port follow-up; UniFFI (`rust/bmlib-ffi`, PR #487) for WP-B9.

**Spec:** The 2026-10-17 audit, recorded in [Appendix A](#appendix-a-what-lite-has-that-bmlib-lacks). For each item, the lite module and contract named in its row state the rule; lite paths are relative to bmlibrarian_lite's root and read at `ca9de635` unless another commit or PR is named. The plan for the opposite direction is lite's `doc/plans/2026-10-17-port-bmlib-advances.md`, and the cross-repo pairing is tracked in lite's `doc/cross_platform/bmlib_sync/ledger.json` (`to_bmlib` entries). **Neither file is at `ca9de635` or on lite's `master`**: both are in hherb/bmlibrarian_lite#564 (open, branch `fix/bmlib-sync-ledger`, head `274aa511`).

**Tracking:** bmlib-side status lives in `ROADMAP.md`, which is canonical for bmlib: each item gets a row there when its issue is filed, and the row is what changes when it lands. Lite's `ledger.json` tracks the pairing between the two repositories, not bmlib's status.

**References:** bmlib's own issues and PRs are written `#N`. Lite's are written `hherb/bmlibrarian_lite#N`, every number prefixed, because a bare `#N` links to bmlib's issue N.

## Global Constraints

- bmlib's session rules bind (`docs/SESSION-RULES.md`, `CLAUDE.md`). In particular:
  - **measure the population before changing a rule**, and run one live probe at a time;
  - a `CHANGELOG.md` entry with a **"What moves"** paragraph for each change;
  - maintainer choices recorded in `docs/DECISIONS.md`;
  - `docs/manual/` updated with the behaviour;
  - a mutation sweep of new code, with every mutant killed or argued.
- **The Rust port follows each behavioural change in its own issue** ("The Rust port follows in #N"). `uv run python scripts/rerun_rust_oracle.py` must stay green on every PR.
- A rule lifted from lite keeps lite's contract file byte-identical under `tests/data/contracts/<name>.json`. The test reading it carries a header comment naming the lite path and commit **and asserts the file's SHA-256 against a value recorded beside them**, so a local edit fails the suite rather than resting on prose. If bmlib's measurement disagrees with the contract, the disagreement goes back to lite as an issue; never fork the table silently.
- `fulltext` must still import on a core install (#64). New network code is guarded at the call site, never imported at a module's top level.
- Gates: `uv run pytest tests/ -v`; `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .` (the CI-pinned ruff, not `.venv`'s); `uv run mypy` (no arguments); and the oracle rerun.
- **Never write a GitHub closing keyword immediately before an issue number**, in a commit, a PR body or a quotation of either.

## Order and dependencies

- **B1 first.** B2's landing-page request and every new tier go through B1's client seam; WP-B6's transport is something a caller hands to that seam.
- **WP-B6 after B1**, for the same reason.
- **B2's landing-page request goes through B1's client**, with B2's own per-call `Accept` header and read cap.
- **WP-B5's new PDF tiers after B3's measurement.** Each new tier downloads from hosts that may wall it, so B3 decides how a wall is reported before more tiers meet one.
- **WP-B4 and B3 must state their order on the download path.** Both act on a downloaded body before `FullTextCache.save_pdf`. A challenge page fails `%PDF`, and the title check needs a PDF, so the expected order is: B3's labelling on the refused branch, then `%PDF`, then WP-B4's title check, then the cache write. WP-B4's plan states it and pins it.
- **WP-B8 overlaps PR #487**, whose `parse_jats_audited` already returns warnings and diagnostics on the Rust/FFI side (see WP-B8).
- **WP-B10 depends on B2-B5**, whose adopted contracts it lists.

## Review Focus

1. **A caller-supplied `httpx.Client` that is already closed, that has its own base URL, headers or auth, or whose `follow_redirects` is the opposite of the module's:** bmlib never closes a client it did not open, never mutates one, sends absolute URLs only, and passes its own `timeout=` and `follow_redirects=` (and, in the analyzer, its `User-Agent`) per call. A closed client is a reported request failure, never an absence or a finding. Pinned in Task B1.
2. **A `FullTextService` given a client is not picklable**, a client holding a connection pool. One built without a client must stay picklable (the `_require_httpx` rule in `CLAUDE.md`). Pinned in Task B1.
3. **An Unpaywall location whose `url_for_pdf` is null and whose `url` is an HTML landing page:** the landing page is read for `citation_pdf_url`, never downloaded or reported as the PDF. Pinned in Task B2.
4. **A landing page whose `citation_pdf_url` is relative or carries character references (`&amp;`):** resolved against the page URL and decoded, as lite's contract tables state. Pinned in Task B2.
5. **The `_pick_oa_pdf_url` contract change reaches `scripts/sample_free_pdf_urls.py`**, which imports it so the sampler measures what bmlib downloads. The sampler must follow the new choice, or #68's measured rate stops describing the code. Pinned in Task B2.
6. **A bot-wall challenge is labelled as one where it is met, and is never cached.** The second half already holds (the PDF cache refuses a body not starting `%PDF`); B3 changes only what the failure is called, and only if the measurement finds a population. Task B3.

---

### Task B1: Services accept the caller's HTTP client

`FullTextService._http_get` (`bmlib/fulltext/service.py`) opens a fresh `httpx.Client(timeout=self.timeout, follow_redirects=True)` per request, and `TransparencyAnalyzer._run_steps` (`bmlib/transparency/analyzer.py`) opens one per analysis with `timeout=_HTTP_TIMEOUT_SECONDS` and a `User-Agent` header, leaving `follow_redirects` at httpx's default `False`. A consumer therefore cannot apply its own transport: lite's per-host pacing, retry budget and proxy settings. Without this seam, lite's desktop cannot adopt bmlib (lite's plan, "Phase 8 — The desktop depends on bmlib").

**B1 must not change what a default service or analyzer sends.** In particular `FullTextService` sends **no** `User-Agent` of its own today (httpx's default goes out), and B1 does not add one.

**Files:**
- Modify: `bmlib/fulltext/service.py` (`FullTextService.__init__`, `_http_get`)
- Modify: `bmlib/transparency/analyzer.py` (`TransparencyAnalyzer.__init__`, `_run_steps`)
- Test: `tests/test_fulltext_service.py`, `tests/test_transparency.py`
- Docs: `docs/manual/fulltext.md`, `docs/manual/transparency.md`, `CHANGELOG.md`, `docs/DECISIONS.md`, `ROADMAP.md`

**Interfaces:**
- Produces: `FullTextService.__init__(..., http_client: httpx.Client | None = None)` and `TransparencyAnalyzer.__init__(..., http_client: httpx.Client | None = None)`, each declared last for positional stability.
  - When given, every request goes through it with bmlib's own `timeout=`, `follow_redirects=` and (analyzer only) `User-Agent` passed per call:
    - `fulltext`: `timeout=self.timeout`, `follow_redirects=True`, no header added;
    - `transparency`: `timeout=_HTTP_TIMEOUT_SECONDS`, `follow_redirects=False` (a 3xx is a failed request there, stored as `REQUEST_FAILED`; the API sampler once measured a different client for exactly this setting), and `headers={"User-Agent": user_agent(...)}` from `bmlib._user_agent`, the shared helper that transparency's private `_user_agent` wraps.
  - bmlib never closes the caller's client, and never mutates its headers, auth, base URL, timeout or redirect setting.
  - When omitted, construction is today's: `fulltext` one client per request, the analyzer one per analysis, with today's arguments.
- The parameter is typed with `httpx` under `TYPE_CHECKING` only (both modules already import it that way), so a core install still imports.

- [ ] **Step 1: Write the failing tests.** Each passes `cache=FullTextCache(cache_dir=tmp_path)`, so no test creates the default home-directory cache.

```python
def test_a_caller_client_carries_every_request(tmp_path, monkeypatch) -> None:
    transport, seen = recording_transport(not_found)
    built: list[dict] = []
    real = httpx.Client
    with real(transport=transport) as client:
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: built.append(k) or real(*a, **k))
        service(tmp_path, client).fetch_fulltext(doi="10.1/x")
    assert seen            # the chain made requests ...
    assert built == []     # ... and bmlib built no client of its own

def test_bmlib_never_closes_a_client_it_did_not_open(tmp_path) -> None:
    client = httpx.Client(transport=recording_transport(not_found)[0])
    service(tmp_path, client).fetch_fulltext(doi="10.1/x")
    assert not client.is_closed

def test_each_request_carries_the_modules_timeout_and_redirect_policy(tmp_path) -> None:
    # the caller's client says follow_redirects=False and timeout=99;
    # the first request answers 302 to a second URL
    # assert the redirect was followed (fulltext follows) and every request's
    # extensions["timeout"] is the service's own timeout

def test_no_user_agent_is_added(tmp_path) -> None:
    # every request carries the caller client's own User-Agent, unchanged

def test_a_caller_clients_settings_are_left_as_they_were(tmp_path) -> None:
    # headers, auth, base_url, timeout and follow_redirects equal before and after

def test_a_base_url_does_not_redirect_a_request(tmp_path) -> None:
    # client base_url="https://elsewhere.invalid"; no recorded host is elsewhere.invalid

def test_a_closed_client_is_a_fault_not_an_absence(tmp_path) -> None:
    # the exhaustion report (_TierFailures) names the error among its faults,
    # and counts no absence for it

def test_without_a_client_behaviour_is_unchanged(tmp_path, monkeypatch) -> None:  # control
    # patch httpx.Client; assert it is constructed with timeout=service.timeout
    # and follow_redirects=True and no headers, and closed after each request

def test_a_service_without_a_client_still_pickles(tmp_path) -> None:
    pickle.dumps(service(tmp_path, None))
```

That is **nine** tests for `FullTextService`. Write the analyzer's eight in `tests/test_transparency.py` as their own, not copies (no pickle test: the analyzer holds a lock):
- the redirect test is the mirror image: the caller's client says `follow_redirects=True`, a 302 is **not** followed (only the first URL is recorded) and the component reports a failed request;
- the `User-Agent` test asserts every request carries `user_agent("t@example.com", httpx.__version__)`, including when the caller's client sets a `User-Agent` of its own;
- the closed-client test asserts `analyze()` does not raise and the result is `UNKNOWN` with `TransparencyUnknownReason.UNREACHABLE`;
- the control asserts today's `timeout=_HTTP_TIMEOUT_SECONDS`, the `User-Agent` header, no `follow_redirects` argument, and the client closed after the analysis.

- [ ] **Step 2:** Run `uv run pytest tests/test_fulltext_service.py tests/test_transparency.py -k client -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Route `_http_get`, and `_run_steps`'s `with httpx.Client(...)` block, through a small context manager that yields the caller's client un-closed, or a fresh one built with today's arguments that it closes. Pass each module's per-call kwargs on every request in both cases (equal to the fresh client's own settings, so the default path sends the same requests).
- [ ] **Step 4:** Run the full suite and the oracle rerun. Expected: PASS. **What moves: nothing for a caller passing no client.** Rust: no follow-up needed, since the crate's fulltext service and analyzer already take an injected `HttpClient` (the trait in `rust/bmlib/src/publications/fetchers/registry.rs`). Record that in the CHANGELOG entry.
- [ ] **Step 5:** Commit: `feat(fulltext,transparency): accept the caller's httpx client`.

**Open question, not part of B1:** should `FullTextService` identify itself with `bmlib._user_agent.user_agent`? Measure before deciding: ClinicalTrials.gov refused 8 of the 13 header shapes probed for #194, and `fulltext` reaches publisher hosts no sampler has presented a header to. File it as its own issue when B1 lands.

### Task B2: An Unpaywall landing page is read, never taken as the PDF

`_pick_oa_pdf_url` (`bmlib/fulltext/service.py`, called by `FullTextService._fetch_unpaywall`) takes `url_for_pdf or url`. Unpaywall's `url` is usually the landing page, an HTML document.

**bmlib measured this population first.** Release 0.9.1's `scripts/sample_free_pdf_urls.py --target 150 --per-host-interval 4.0` (2026-08-11) found 18 failures in 28 Unpaywall probes (64.3%, 95% CI 45.8-79.3%), **14 of them landing pages** (`not-a-pdf`) and 4 HTTP 403. That rate chose Unpaywall's one-shot WARNING under #68's 5% rule (the docstring of `FullTextService._report_pdf_download_failure`, `ROADMAP.md`'s #68 row, `docs/DECISIONS.md` "fulltext — the free-PDF tier and what it reports", `docs/manual/fulltext.md`), and DECISIONS says re-deciding it means re-running the sampler. bmlib reported the landing pages and never read them. Lite found the same defect independently as hherb/bmlibrarian_lite#464 (lite's PR hherb/bmlibrarian_lite#465 changed all three platforms): on Android the Unpaywall tier had never once yielded a PDF. Lite's rule reads the landing page's Highwire `<meta name="citation_pdf_url">` instead.

**Files:**
- Create: `bmlib/fulltext/_landing_page.py`
- Create: `tests/data/contracts/unpaywall_landing_page.json` (lite's `doc/cross_platform/fulltext_parity/unpaywall_landing_page.json` at `ca9de635`, `schema_version` 3)
- Modify: `bmlib/fulltext/service.py` (`_pick_oa_pdf_url`, `FullTextService._fetch_unpaywall`, and a streamed read beside `_http_get` through B1's seam)
- Modify: `scripts/sample_free_pdf_urls.py` (imports `_pick_oa_pdf_url`; its Unpaywall population must probe what bmlib now downloads)
- Test: `tests/test_landing_page.py`, `tests/test_fulltext_service.py`, `tests/test_free_pdf_sampler.py`
- Docs: `docs/manual/fulltext.md`, `CHANGELOG.md`, `docs/DECISIONS.md`, `ROADMAP.md`

**Interfaces:**
- Produces, mirroring lite's `src/bmlibrarian_lite/oa_landing_page.py`:
  - `unpaywall_pdf_urls(response: Mapping[str, Any]) -> list[str]`
  - `choose_unpaywall_url(response: Mapping[str, Any]) -> UnpaywallChoice`, a frozen dataclass with lite's fields: `pdf_url: str | None`, `landing_page: str | None`, and `location: Mapping[str, Any] | None` (`compare=False`, `repr=False`; not part of the choice's identity, which is what the contract pins). `__post_init__` raises `ValueError` when both `pdf_url` and `landing_page` are set. The contract's `unpaywall_choice` rows are keyed `pdf_url` / `landing_page`, so the test reads them as they stand.
  - `citation_pdf_url(page_html: str, page_url: str) -> str | None`
  - `decode_character_references(value: str) -> str`
- `_pick_oa_pdf_url` returns the `UnpaywallChoice` (or is replaced by `choose_unpaywall_url`); its one other caller is the sampler.
- **The landing-page read is bounded at 2 MB** (lite's `LANDING_PAGE_MAX_BYTES = 2 * 1024 * 1024` in `src/bmlibrarian_lite/constants.py`). The argument, stated there and decided by lite's maintainer under hherb/bmlibrarian_lite#464: the tag sits in `<head>`, so a page cut at the cap still declares its PDF, and a lookup page is not research content, so the cap loses no evidence. Measure landing-page sizes on the same Unpaywall draw as Step 4 and record the bound and its measurement in `docs/DECISIONS.md`.

- [ ] **Step 1:** Write `tests/test_landing_page.py`. Parametrise over every row of each contract table (`unpaywall_choice`, `unpaywall_pdf_urls`, `citation_pdf_url`, `character_references`, `landing_page_status`), and assert the file's recorded SHA-256. Add a test asserting that every table is read, so a row added upstream cannot go unasserted. `landing_page_status` marks each error status `unsettled` or not; check its mapping against bmlib's absence/fault split (`FullTextUnavailableError` for an absence) before adopting it, and take any disagreement (403 is settled there and a fault here) back to lite. In `tests/test_fulltext_service.py`, add a service-level test: an Unpaywall answer with only a landing page `url`, a mocked landing page declaring `citation_pdf_url`, and that PDF served; assert `pdf_url` is the declared PDF, never the landing page. In `tests/test_free_pdf_sampler.py`, pin that the sampler's Unpaywall population probes the choice's PDF, or the PDF its landing page declares.
- [ ] **Step 2:** Run `uv run pytest tests/test_landing_page.py tests/test_fulltext_service.py tests/test_free_pdf_sampler.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `_landing_page.py`, use it in the Unpaywall tier, and follow it in the sampler.
- [ ] **Step 4:** Re-run `scripts/sample_free_pdf_urls.py --target 150 --per-host-interval 4.0` (the 2026-08-11 arguments) on `main` before the change and on the branch after, one run at a time. Report in the CHANGELOG's "What moves" the Unpaywall `not-a-pdf` count of both runs against the 14-of-28 baseline, and how many PDFs the tier yields before and after. Then re-check the log-level choice resting on that rate: if Unpaywall's failure rate falls under #68's 5% threshold, the one-shot variant's justification goes, and the decision is re-made in `docs/DECISIONS.md`, not assumed.
- [ ] **Step 5:** Run the full suite, the oracle rerun and the mutation sweep. File the Rust follow-up. Commit: `fix(fulltext): an Unpaywall landing page is read for its PDF, never taken as one`.

### Task B3: Measure bot-wall challenge pages, then label them where they are met

Lite's `src/bmlibrarian_lite/bot_walls.py` recognises a publisher's bot wall (Cloudflare, Akamai, PerimeterX, DataDome and others) from the status, the header names and the body's first `body_prefix_bytes` = **65,536** bytes (contract `doc/cross_platform/fulltext_parity/challenge_detection.json`; hherb/bmlibrarian_lite#480, stage 1 in hherb/bmlibrarian_lite#521). Lite's committed survey (`doc/developer/unpaywall_pdf_survey/README.md`, 400 Unpaywall-named PDF addresses, 2026-10-04) found its clients obtained 28% of them, and 81% of the rest were bot walls.

**Most of lite's premise does not hold in bmlib**, and the plan does not pretend otherwise:
- **Nothing caches a wall today.** A Tier 0 `html` source returns `web_url` only and never fetches a body. The `html/` cache holds only `JATSParser`-rendered output, written by `_cache_html` after a JATS parse that found a body (renderer-stamped since #172), so lite's served-body markers would test the wrong layer there. The PDF cache already refuses any body not starting `%PDF` (`FullTextCache.save_pdf`, `PDF_MAGIC_BYTES`), reported as `not-a-pdf`; lite's contract itself says a body beginning `%PDF` is never a challenge.
- **A 403 is already a fault, not an absence.** Absences are a 404 and "no content" (`FullTextUnavailableError`); anything else is a `FullTextError`. The exhaustion report is `_TierFailures` in `service.py` (a list of faults plus an absence count); nothing in `models.py` holds an attempt-outcome vocabulary.
- **An entry already on disk is out of scope.** #70's DECISIONS entry accepts that its protection is prospective-only, and a challenge page reaching the HTML cache is a population that is probably empty.

What is left is **labelling**: a wall currently reads as `http-403` or `not-a-pdf` in `_report_pdf_download_failure`'s one-shot `(origin, cause)` key, so an operator cannot tell "this host walls bmlib" from "this address serves no PDF".

**Files:**
- Modify: `scripts/sample_free_pdf_urls.py` (Step 1)
- Test: `tests/test_free_pdf_sampler.py`
- Only if Step 1 finds a population: create `bmlib/fulltext/_bot_walls.py` and `tests/data/contracts/challenge_detection.json` (lite's file at `ca9de635`); modify `bmlib/fulltext/service.py` (`_download_and_cache_pdf`, `_report_pdf_download_failure`); test `tests/test_bot_walls.py`, `tests/test_fulltext_service.py`.

- [ ] **Step 1: Measure.** Extend `scripts/sample_free_pdf_urls.py` (a re-derivable instrument, not an ad-hoc probe) to classify each failed probe as a challenge or not, per population (`europepmc`, `unpaywall`, `biorxiv`) and per status. The sampler carries its own classifier rather than importing the rule under test (the samplers' standing rule); its agreement with the contract's `cases` is a test of the instrument in `tests/test_free_pdf_sampler.py`. Run it with the 2026-08-11 arguments, after B2 so the Unpaywall population is the one bmlib downloads. Report which tiers meet walls, at which status, and how many.
- [ ] **Step 2: Decide.** If no population is found, record the measurement in `docs/DECISIONS.md` and stop. If one is, the likely change is a cause of its own in `_report_pdf_download_failure` (`challenged-<defence>`, the defence names being a closed set from the contract's `markers`, so the warning keys stay bounded), and the same name in the fault `_TierFailures` records, not a new cache guard. Record the choice in `docs/DECISIONS.md`.
- [ ] **Step 3 (if Step 2 chose a change):** Write the tests (every row of the contract with its SHA-256 asserted, and a service-level test that a walled PDF download is reported under its defence and counted as a fault), run them failing, implement, run the full suite, the oracle rerun and the mutation sweep, file the Rust follow-up. Commit: `feat(fulltext): a bot-wall challenge is reported as one`.

**What moves** (if Step 3 runs): the one-shot WARNING's cause for a walled download, from `http-403` or `not-a-pdf` to `challenged-<defence>`, and the type name in the exhaustion summary. No stored field, no cache entry and no `FullTextResult` moves.

### Done when (B1-B3)

- B1: both constructors take `http_client`; the seventeen tests pass; a caller passing no client sends exactly today's requests (the controls); `docs/manual/` documents what a caller's client must not rely on (bmlib's per-call `timeout=`, `follow_redirects=` and, in the analyzer, `User-Agent` override it); the `FullTextService` `User-Agent` question is filed as its own issue.
- B2: every contract row is asserted and the contract's SHA-256 pinned; the Unpaywall tier never reports a landing page as `pdf_url`; the sampler measures the new choice; the before/after sampler runs are in "What moves" against the 14-of-28 baseline; the log-level decision is re-checked in `docs/DECISIONS.md`; the landing-page size bound is measured and recorded.
- B3: the measurement is committed (the sampler's output, by tier and status) and its decision is in `docs/DECISIONS.md`, whether or not code follows.
- All three: a `ROADMAP.md` row each, a CHANGELOG entry with "What moves", the gates green, mutation sweeps argued, and each Rust follow-up filed (or, for B1, its absence stated).

---

## Work packages

Each becomes its own plan with its own bmlib issue. Lite's module and contract are the spec; bmlib measures before adopting.

- **WP-B4 The wrong-article PDF check.** Lite's `src/bmlibrarian_lite/pdf_title_check.py` (hherb/bmlibrarian_lite#532, hherb/bmlibrarian_lite#546; contract `doc/cross_platform/fulltext_parity/pdf_title_check.json`):
  - the rule: at least 0.5 of the title's adjacent word pairs appear on the first page with text, or the whole title appears with word breaks ignored (at least 20 letters or digits);
  - no title, no text, or a translated `[title]` is *unchecked*, never a match;
  - the check runs **before** the PDF is cached (hherb/bmlibrarian_lite#560, hherb/bmlibrarian_lite#561: the download waits beside its cache name as `.unchecked`); a mismatch is set aside as `.mismatch`, and the consumer can mark it kept (`.kept`, hherb/bmlibrarian_lite#541).

  **Where lite's numbers come from** (`src/bmlibrarian_lite/constants.py` at `ca9de635`): `PDF_TITLE_MIN_PAIR_SHARE = 0.5` was measured on 500 PDFs of lite's knowledgebase against their own titles and the most similar other title (9 of 499 right PDFs below it, 3 of 500 wrong titles reaching it), with two held-out samples of 500; the comment does not name the PDF reader, though lite's desktop reads with PyMuPDF. `PDF_TITLE_MIN_JOINED_CHARACTERS = 20` was measured on 1,491 knowledgebase PDFs under three readers (MuPDF, PDFKit, iText). The knowledgebase is a private library, so bmlib cannot re-derive either. **PDF readers disagree on word boundaries** (PDFKit splits medRxiv title words, iText joins them): re-measure both values under PyMuPDF on a re-derivable corpus with known titles before adopting them. If PyMuPDF needs different values, raise it as a lite issue. State the order against B3 and `%PDF` (see Order and dependencies).
- **WP-B5 Full-text sources lite has.** Each is a new tier with lite's contract as its fixture, taken after B3's measurement:
  - **PMC's open-data S3 bucket** (JATS by PMC ID; `pmc_open_data.py`, `pmc_open_data.json`). It serves what the efetch tier serves, without NCBI's rate limit.
  - **OpenAlex locations by DOI** (`openalex.py`, `openalex_locations.json`). bmlib's OpenAlex fetcher already emits each location's PDF as a `FullTextSourceEntry`, which Tier 0 downloads; the gap is a DOI-only lookup for an article bmlib did not ingest from OpenAlex.
  - **Preprints by `PPR` accession**: Europe PMC `SRC:PPR` records carry no PMID or PMC ID (lite's `fulltext_accession`). `bmlib/fulltext/service.py` has no `PPR` handling at all, so such a preprint is reached only through a caller-supplied source or a DOI. (`transparency` addresses `PPR` accessions; `fulltext` does not.)
  - **CORE's extracted text** (user's key; `core_api.py`, `core_fulltext.json`).
  - **Elsevier's article API** (user's key, `10.1016/` only; `elsevier_api.py`, `elsevier_article.json`). Its key header survives redirects; a 200 `%PDF` may be page one only (`X-ELS-Status: WARNING`). Lite's fixtures follow Elsevier's documented shapes, and the live probe was owed as of lite `ca9de635`, 2026-10-17 (lite's `HANDOVER.md`, "C2's probe, off-network"), so adopt this one after that probe.
  - The keyed services share a session state (`keyed_service_session.py`): 429 pause, refused key, refused network, process-wide.
- **WP-B6 Per-host polite pacing as an httpx transport.** bmlib paces today in three unrelated ways: `TransparencyAnalyzer._rate_limit` (one interval per analyzer instance), fixed sleeps in the PubMed, OpenAlex and bioRxiv fetchers (`RATE_LIMIT_WITH_KEY` / `RATE_LIMIT_WITHOUT_KEY`, `_RATE_LIMIT_SECONDS`, `RATE_LIMIT_SECONDS`), and the samplers' per-host pacer in `scripts/_sampling.py`. `ROADMAP.md`'s "Rate limiting" row is ⬜ Planned, and its "the package throttles nothing" understates those three; correct it when this is taken. Lite's contract is `doc/cross_platform/polite_request_pacing.md`:
  - one limiter per host, process-wide;
  - `Retry-After` is a pause, not a rate;
  - one retry budget, never two nested (and one pacer: the fetchers' own sleeps must not stack under it);
  - a 503 is a throttle for Europe PMC and an outage for NCBI — **re-measure before adopting**: bmlib's Europe PMC `fullTextXML` answers 500 for an article it does not serve (#457, #462), and `scripts/sample_api_failures.py`'s `_THROTTLE_STATUSES` treats 503 as a throttle at every host;
  - loopback is never paced.

  Ship it so a consumer can hand it to B1's seam, **without a top-level httpx import**: a subclass of `httpx.BaseTransport` cannot be defined at a module's top level without importing httpx, which breaks `CLAUDE.md`'s "no top-level optional import remains in the package". So expose a factory (for example `bmlib.http.polite_transport(...)`) that imports httpx at the call site and builds the subclass there, the `_require_httpx` pattern. httpx comes from the `fulltext`, `transparency` and `publications` extras.
- **WP-B7 Transparency rules lite has measured:**
  - **funders recognised by brand, with a foundation guard** (hherb/bmlibrarian_lite#394). Lite's `doc/cross_platform/transparency_parity/sponsor_patterns.json` (schema 4) is a **pattern table** (patterns, confidences, `pattern_probes`, `industry_brands`), while bmlib's `tests/data/funder_names.json` is a **labelled corpus** that `TestTheStatedCountsAreWhatTheCorpusHolds` re-derives every count from. They are not two corpora to merge. The work is to run lite's brand rule against bmlib's labelled corpus and report what it gains and costs. `ROADMAP.md` already holds this as "Recognise a funder named by brand alone" (⬜ Planned, "Owes #154 first"), and `HANDOVER.md` says any session extending a funder list owes #154 first. Both bind;
  - **statement recognition**: front-matter COI and data statements reaching the analysis (hherb/bmlibrarian_lite#420), and charging a missing statement only when every end-matter heading was classified (hherb/bmlibrarian_lite#428);
  - **JATS-tagged statements as headings** in the full-text scan (`fn-type`, `notes-type`, `sec-type`; bold run-in headings split; COI in `author-notes`; de-duplication). Compare with bmlib's own handling first: `transparency` regexes the raw `fullTextXML` and by rule never reads `JATSParser` output (#119), and already matches typed COI containers (`_COI_SECTION_RE`) beside its cue-phrase scan. Adopt only what bmlib lacks. Recognising more headings can create charges as well as remove them (the shape hherb/bmlibrarian_lite#428 exists for), so diff the charge outcomes old against new, not the section counts.
- **WP-B8 JATS consumer surface:**
  - structured parse warnings on `JATSArticle` (Swift shows them to the reader as `JATSParseWarnings`; bmlib's Python parser only logs);
  - a warning when the deposit's PMC ID differs from the one requested (Swift).

  **This overlaps PR #487**: its `parse_jats_audited(xml, known_pmc_id)` already returns `warnings` (publisher losses) and `diagnostics` (reader defects) on the Rust/FFI side. WP-B8 is the Python half, and its shape should match that report rather than invent a second one. Decide each in `docs/DECISIONS.md`. Logging is not reporting.
- **WP-B9 An FFI crate for the mobile apps** (`rust/bmlib-ffi`, UniFFI). This is the bmlib half of lite's WP-2 spike, spiked in PR #487 (the lite half is hherb/bmlibrarian_lite#572, whose decision record is `doc/cross_platform/bmlib_sync/uniffi_spike.md` on lite's `spike/uniffi-jats`):
  - **first surface: `parse_jats`** (and `parse_jats_audited`), as spiked in PR #487;
  - **then a table-body renderer**, so lite's desktop and both apps share one table rendering (the maintainer's choice, 2026-10-17, recorded in `uniffi_spike.md` "Maintainer decisions"); PR #487 leaves it to its own PR;
  - then the rest: `json_repair` / span location, the citation formatters, the quality extractors and narrowing, the retraction rule and funder matching;
  - **features, as PR #487 does them:** `sqlite` and `http-ureq` become default features of the `bmlib` crate, and `bmlib-ffi` depends on it with `default-features = false`. This needs maintainer review: a downstream already depending with `default-features = false` loses SQLite and HTTP;
  - `HttpClient` exposed as a UniFFI callback interface (stubbed in PR #487), so the apps keep their own transports (pacing, bot-wall handling, the in-app browser rescue);
  - CI builds an xcframework and Android `.so` files from a tag, so lite pins a version and checksum rather than a commit.
- **WP-B10 A conformance suite consumers can vendor.** Publish `rust/oracle/*cases.json` (38 files at `f4b7c52`, `cases.json` among them) with a version and a manifest, `rust/oracle/MANIFEST.json`: file (repo-relative), schema, bmlib commit, SHA-256. The manifest also lists the lite contracts adopted under `tests/data/contracts/` (B2, B3, WP-B4, WP-B5), each with its lite path and lite commit beside its hash, so lite's Swift and Kotlin tests can assert against the same cases the Rust port meets. Depends on B2-B5.
- **WP-B11 Decisions to revisit with lite's data (no code until decided):**
  - **skipping the Europe PMC full-text request when `isOpenAccess: N`**: bmlib refused the gate (#216, restated under #457 in `docs/DECISIONS.md`) and reads the flag only to interpret a 500. bmlib's own run of 2026-10-15 (#462) saw 37 of 37 `isOpenAccess: N` records answer 500; lite measured the same (hherb/bmlibrarian_lite#432, hherb/bmlibrarian_lite#451) and accepts losing PMC9391270. Bring both to the decision;
  - **the trial-id cap**: bmlib checks at most `MAX_TRIAL_IDS_TO_CHECK = 3` accessions per paper and reports the cap rather than raising it; `docs/DECISIONS.md` (#206) says not to raise it on the 8-of-30 evidence, which sizes how often the cap bites and not by how much. Lite having no cap is not a measurement. What would decide it is the per-paper accession-count distribution, which `scripts/sample_api_failures.py` records before the cap and does not yet print.

  `pty` is **not** reopened here. `docs/DECISIONS.md` (#112) keeps it under rule 2, an incorporation suffix being a prior where the corpus is silent, and refutes the "*0 TP means excluded*" reading, which is exactly lite's stated reason for declining it. That disagreement goes back to lite as an issue, per the Global Constraints.

## Appendix A: What lite has that bmlib lacks

| Item | Lite source | Contract | bmlib at `f4b7c52` | WP |
|---|---|---|---|---|
| Caller's HTTP client | `polite_session.py` (consumer need) | — | clients opened internally (`_http_get`, `_run_steps`) | B1 |
| Unpaywall landing page → `citation_pdf_url` | `oa_landing_page.py` (hherb/bmlibrarian_lite#464, hherb/bmlibrarian_lite#465) | `unpaywall_landing_page.json` | **defect**: `_pick_oa_pdf_url` takes `url_for_pdf or url`; measured 14 of 28 landing pages (2026-08-11) | B2 |
| Bot-wall recognition | `bot_walls.py` (hherb/bmlibrarian_lite#480) | `challenge_detection.json` | no wall cached (`%PDF` check); a wall reads as `http-403` or `not-a-pdf` | B3 |
| Wrong-article PDF check, checked before caching | `pdf_title_check.py`, `pdf_library_check.py` (hherb/bmlibrarian_lite#532, hherb/bmlibrarian_lite#541, hherb/bmlibrarian_lite#546, hherb/bmlibrarian_lite#560, hherb/bmlibrarian_lite#561) | `pdf_title_check.json` | none (`_titles.py` judges metadata titles only) | B4 |
| PMC open-data bucket | `pmc_open_data.py` | `pmc_open_data.json` | efetch tier only | B5 |
| OpenAlex PDF locations by DOI | `openalex.py` | `openalex_locations.json` | OpenAlex fetcher's PDF locations reach Tier 0; no DOI-only lookup | B5 |
| Preprints by `PPR` accession | `fulltext_discovery.py` | `fulltext_retrieval.md` | no `PPR` handling in `fulltext` | B5 |
| CORE text; Elsevier API; keyed session | `core_api.py`, `elsevier_api.py`, `keyed_service_session.py` | `core_fulltext.json`, `elsevier_article.json` | none | B5 |
| Per-host polite pacing | `rate_limit.py`, `polite_session.py` | `polite_request_pacing.md` | `_rate_limit` per analyzer; fixed sleeps in the three fetchers | B6 |
| Funders by brand + foundation guard | `transparency/` (hherb/bmlibrarian_lite#394) | `sponsor_patterns.json` v4 | name lists, labelled corpus | B7 |
| Statement recognition (hherb/bmlibrarian_lite#420, hherb/bmlibrarian_lite#428); tagged statements as headings | `transparency/assessment`, `jats_markdown.py` | `transparency_parity/` | partial (`_COI_SECTION_RE`, cue phrases) | B7 |
| Parse warnings to the reader; PMC ID mismatch | Swift `JATSParseWarnings` | — | logged only (Python); `parse_jats_audited` in PR #487 (Rust/FFI) | B8 |
| One implementation on mobile | — | — | Rust crate; FFI spiked in PR #487 | B9 |
| Shared conformance cases | `doc/cross_platform/*` | — | oracle cases, unversioned | B10 |
| `isOpenAccess` pre-check; trial-id cap | hherb/bmlibrarian_lite#432, hherb/bmlibrarian_lite#451 | — | decided otherwise (#216, #457, #206) | B11 |

Not proposed: lite's relevance-scoring parser (bmlib has no scoring agent); the in-app browser rescue and review queue (GUI); checkpointing, sync and the error queue (app concerns).
