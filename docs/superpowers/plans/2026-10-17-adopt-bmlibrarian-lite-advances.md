# Adopt bmlibrarian_lite's advances into bmlib — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring into bmlib the capabilities and fixes that bmlibrarian_lite (`~/src/bmlibrarian_lite`, commit `ca9de635`) has and bmlib (`f4b7c52`) lacks, and add the seams that let bmlibrarian_lite's desktop depend on bmlib instead of carrying its own copies.

**Architecture:**
- Three detailed tasks come first:
  - an injectable HTTP client (the seam every consumer needs);
  - the Unpaywall landing-page defect;
  - challenge-page recognition (so a bot wall is never cached as an article).
- The rest are work packages: new full-text sources, the wrong-article PDF check, transparency and JATS rules lite has measured, and the FFI crate that would let lite's Swift and Android apps call bmlib's Rust port.
- Each work package is expanded into its own plan, under bmlib's conventions, when it is picked up.
- Where lite already has a cross-platform contract JSON for a rule, bmlib takes that file as its test fixture (copied under `tests/data/contracts/`, with its provenance recorded), so one table states the rule in both repositories.

**Tech Stack:** Python ≥ 3.11, httpx, pytest; Rust (`rust/bmlib`) for each item's port follow-up; UniFFI for WP-B9.

**Spec:** The 2026-10-17 audit, recorded in [Appendix A](#appendix-a-what-lite-has-that-bmlib-lacks). For each item, the lite module and contract named in its row state the rule. The opposite direction is `~/src/bmlibrarian_lite/doc/plans/2026-10-17-port-bmlib-advances.md`. Both directions are tracked in lite's `doc/cross_platform/bmlib_sync/ledger.json` (`to_bmlib` entries).

## Global Constraints

- bmlib's session rules bind (`docs/SESSION-RULES.md`, `CLAUDE.md`). In particular:
  - **measure the population before changing a rule**, and run one live probe at a time;
  - a `CHANGELOG.md` entry with a **"What moves"** paragraph for each change;
  - maintainer choices recorded in `docs/DECISIONS.md`;
  - `docs/manual/` updated with the behaviour;
  - a mutation sweep of new code, with every mutant killed or argued.
- **The Rust port follows each behavioural change in its own issue** ("The Rust port follows in #N"). `uv run python scripts/rerun_rust_oracle.py` must stay green on every PR.
- Lite's issue numbers are a different tracker. Write them as `lite#N`.
- A rule lifted from lite keeps lite's contract file byte-identical under `tests/data/contracts/<name>.json`, with a header comment in the test naming the lite path and commit. If bmlib's measurement disagrees with the contract, the disagreement goes back to lite as an issue; never fork the table silently.
- `fulltext` must still import on a core install (bmlib#64). New network code stays behind the `fulltext` extra.
- Gates: `uv run pytest tests/ -v`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` (no arguments), and the oracle rerun.

## Review Focus

1. **A caller-supplied `httpx.Client` that is already closed, or that has its own base URL or auth:** bmlib never closes a client it did not open, and sends absolute URLs only. Pinned in Task B1.
2. **An Unpaywall location whose `url_for_pdf` is null and whose `url` is an HTML landing page:** the landing page is read for `citation_pdf_url`, never downloaded or reported as the PDF. Pinned in Task B2.
3. **A 200 answer whose body is a Cloudflare or Akamai challenge page served with `Content-Type: application/pdf`:** recognised as a wall, not cached, and not counted as Europe PMC or the publisher holding no copy. Pinned in Task B3.
4. **A challenge page cached by an earlier bmlib:** it is never served from the cache. Pinned in Task B3.
5. **A landing page whose `citation_pdf_url` is relative or carries character references (`&amp;`):** resolved against the page URL and decoded, as lite's contract tables state. Pinned in Task B2.

---

### Task B1: Services accept the caller's HTTP client

`FullTextService._http_get` (`bmlib/fulltext/service.py:659`) and `TransparencyAnalyzer` (`bmlib/transparency/analyzer.py:3061`) each open a fresh `httpx.Client`. A consumer therefore cannot apply its own transport: lite's per-host pacing, retry budget and proxy settings. Without this seam, lite's desktop cannot adopt bmlib (lite plan, Phase 8).

**Files:**
- Modify: `bmlib/fulltext/service.py:596-662`
- Modify: `bmlib/transparency/analyzer.py:2796-2815, ~3055-3070`
- Test: `tests/test_fulltext_service.py`, `tests/test_transparency.py`
- Docs: `docs/manual/fulltext.md`, `docs/manual/transparency.md`, `CHANGELOG.md`, `docs/DECISIONS.md`

**Interfaces:**
- Produces: `FullTextService.__init__(..., http_client: httpx.Client | None = None)` and `TransparencyAnalyzer.__init__(..., http_client: httpx.Client | None = None)`.
  - When given, every request goes through it, with bmlib's per-request `timeout=` and `User-Agent` header passed per call. bmlib never closes it, and never mutates its headers, auth or base URL.
  - When omitted, behaviour is byte-for-byte today's.
- The parameter is typed with `httpx` under `TYPE_CHECKING` only, so a core install still imports.

- [ ] **Step 1: Write the failing tests:**

```python
def test_a_caller_client_carries_every_request() -> None:
    transport = httpx.MockTransport(recording_handler)
    with httpx.Client(transport=transport) as client:
        FullTextService(email="t@example.com", http_client=client).fetch_fulltext(doi="10.1/x")
    assert recorded_hosts  # every request went through the caller's transport

def test_bmlib_never_closes_a_client_it_did_not_open() -> None:
    client = httpx.Client(transport=httpx.MockTransport(ok_handler))
    FullTextService(email="t@example.com", http_client=client).fetch_fulltext(doi="10.1/x")
    assert not client.is_closed

def test_the_user_agent_is_sent_per_request() -> None:
    # a caller client with no headers of its own
    assert all(r.headers["User-Agent"] == _user_agent("t@example.com", httpx.__version__)
               for r in recorded_requests)

def test_without_a_client_behaviour_is_unchanged() -> None:  # control
    # patch httpx.Client; assert it is constructed with today's timeout and
    # follow_redirects=True, and closed after the call
```

Add the same three tests for `TransparencyAnalyzer.analyze`.

- [ ] **Step 2:** Run `uv run pytest tests/test_fulltext_service.py tests/test_transparency.py -k client -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Route `_http_get`, and the analyzer's `with httpx.Client(...)` block, through a small context manager that yields the caller's client un-closed, or a fresh one that it closes.
- [ ] **Step 4:** Run the full suite and the oracle rerun. Expected: PASS. Rust: no follow-up needed, since the crate already injects through its `HttpClient` trait. Record that in the CHANGELOG entry.
- [ ] **Step 5:** Commit: `feat(fulltext,transparency): accept the caller's httpx client`.

### Task B2: An Unpaywall landing page is read, never taken as the PDF

`_unpaywall_pdf_url` (`bmlib/fulltext/service.py:~391-399`) takes `url_for_pdf or url`. Unpaywall's `url` is usually the landing page, an HTML document. lite found this as lite#464/#465: on Android the Unpaywall tier had never once yielded a PDF. Lite's rule reads the landing page's Highwire `<meta name="citation_pdf_url">` instead.

**Files:**
- Create: `bmlib/fulltext/_landing_page.py`
- Create: `tests/data/contracts/unpaywall_landing_page.json` (a copy of lite's `doc/cross_platform/fulltext_parity/unpaywall_landing_page.json`, schema 3)
- Modify: `bmlib/fulltext/service.py:~385-400, 2249+`
- Test: `tests/test_landing_page.py`

**Interfaces:**
- Produces, mirroring lite's `oa_landing_page.py`:
  - `unpaywall_pdf_urls(response: Mapping[str, Any]) -> list[str]`
  - `choose_unpaywall_url(response) -> UnpaywallChoice` (a frozen dataclass `(url: str | None, is_landing_page: bool)`)
  - `citation_pdf_url(page_html: str, page_url: str) -> str | None`
  - `decode_character_references(value: str) -> str`
- The landing-page read is bounded: lite caps it at 2 MB. That bound is fine here, because a lookup page is not research content (lite memory, "truncation rule covers research content only"). Record the bound in `docs/DECISIONS.md`.

- [ ] **Step 1:** Write `tests/test_landing_page.py`. Parametrise over every row of each contract table (`unpaywall_choice`, `unpaywall_pdf_urls`, `citation_pdf_url`, `character_references`, `landing_page_status`). Add a test asserting that every table is read, so a row added upstream cannot go unasserted. Add a service-level test: an Unpaywall answer with only a landing page `url`, a mocked landing page declaring `citation_pdf_url`, and that PDF served; assert `pdf_url` is the declared PDF, never the landing page.
- [ ] **Step 2:** Run `uv run pytest tests/test_landing_page.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `_landing_page.py` and use it in the Unpaywall tier.
- [ ] **Step 4:** Run a live check, one probe at a time, over a small stratified DOI draw with `scripts/` conventions. Report how many PDFs the tier yields before and after in the CHANGELOG's "What moves".
- [ ] **Step 5:** Run the full suite, the oracle rerun and the mutation sweep. File the Rust follow-up. Commit: `fix(fulltext): an Unpaywall landing page is read for its PDF, never taken as one`.

### Task B3: A challenge page is never cached as an article

Publishers' bot walls (Cloudflare, Akamai, PerimeterX, DataDome) answer 200 or 403 with an HTML challenge, sometimes labelled as a PDF. bmlib checks `%PDF` magic before caching a PDF (bmlib#71), but an HTML or JATS tier can still cache a challenge body. Every such answer is also recorded as an ordinary failure, or as an absence. Lite's `bot_walls.py` recognises them from the status, headers and body prefix (lite#480; contract `doc/cross_platform/fulltext_parity/challenge_detection.json`).

**Files:**
- Create: `bmlib/fulltext/_bot_walls.py`
- Create: `tests/data/contracts/challenge_detection.json` (a copy of lite's)
- Modify: `bmlib/fulltext/service.py` (every tier that caches a body), `bmlib/fulltext/cache.py` (the read path), `bmlib/fulltext/models.py` (exhaustion report)
- Test: `tests/test_bot_walls.py`

**Interfaces:**
- Produces: `challenge_markers(status: int, headers: Mapping[str, str], body_prefix: bytes) -> tuple[str, ...]` (empty for an ordinary answer) and `WALL_MARKERS: Mapping[str, tuple[str, ...]]`, with the same semantics and body-prefix length as lite.
- Produces: a new exhaustion-report attempt outcome, `challenged`, naming the defences. It is a request failure, never an absence.

- [ ] **Step 1:** Write the tests:
  - every row of the contract;
  - `test_a_challenge_body_is_not_cached`, one per caching tier (JATS, HTML, PDF);
  - `test_a_cached_challenge_is_not_served`: write a challenge body into the cache the way an older bmlib would have; assert it is quarantined like a corrupt entry (bmlib#71) and not returned;
  - `test_a_challenge_is_reported_as_challenged_not_absent`.
- [ ] **Step 2:** Run `uv run pytest tests/test_bot_walls.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Check the answer before any cache write, and on the cache read path.
- [ ] **Step 4:** Run the full suite, the oracle rerun and the mutation sweep. File the Rust follow-up. Commit: `fix(fulltext): a bot-wall challenge is recognised, never cached as an article`.

---

## Work packages

Each becomes its own plan. Lite's module and contract are the spec; bmlib measures before adopting.

- **WP-B4 The wrong-article PDF check.** Lite's `pdf_title_check.py` (lite#532, #546; contract `pdf_title_check.json`):
  - the rule: at least 0.5 of the title's adjacent word pairs appear on the first page with text, or the whole title appears with word breaks ignored (at least 20 letters or digits);
  - no title, no text, or a translated `[title]` is *unchecked*, never a match;
  - the check runs **before** the PDF is cached (lite#560, #561: the download waits beside its cache name as `.unchecked`); a mismatch is set aside as `.mismatch`, and the consumer can mark it kept (`.kept`, lite#541).

  **PDF readers disagree on word boundaries** (PDFKit splits medRxiv title words, iText joins them). Re-measure the 0.5 share and the 20-character floor under PyMuPDF on a corpus with known titles before adopting the numbers. If PyMuPDF needs different values, raise it as a lite issue.
- **WP-B5 Full-text sources lite has.** Each is a new tier with lite's contract as its fixture:
  - **PMC's open-data S3 bucket** (JATS by PMC ID; `pmc_open_data.py`, `pmc_open_data.json`). It serves what the efetch tier serves, without NCBI's rate limit.
  - **OpenAlex locations** (PDFs by DOI; `openalex.py`, `openalex_locations.json`).
  - **Preprints by `PPR` accession**: Europe PMC `SRC:PPR` records carry no PMID or PMC ID (lite's `fulltext_accession`). A preprint is first-class in lite and is reachable only by luck here.
  - **CORE's extracted text** (user's key; `core_api.py`, `core_fulltext.json`).
  - **Elsevier's article API** (user's key, `10.1016/` only; `elsevier_api.py`, `elsevier_article.json`). Its key header survives redirects; a 200 `%PDF` may be page one only (`X-ELS-Status: WARNING`). Lite's fixtures follow Elsevier's documented shapes, and the live probe is still owed (lite HANDOVER), so adopt this one after that probe.
  - The keyed services share a session state (`keyed_service_session.py`): 429 pause, refused key, refused network, process-wide.
- **WP-B6 Per-host polite pacing as an httpx transport.** bmlib paces only inside one `TransparencyAnalyzer` (`analyzer.py:4201`) and in its scripts. Lite's contract is `doc/cross_platform/polite_request_pacing.md`:
  - one limiter per host, process-wide;
  - `Retry-After` is a pause, not a rate;
  - one retry budget, never two nested;
  - a 503 is a throttle for Europe PMC and an outage for NCBI;
  - loopback is never paced.

  Ship it as `bmlib.http.PoliteTransport`, so both bmlib's default client and any consumer (Task B1) can use it.
- **WP-B7 Transparency rules lite has measured:**
  - **funders recognised by brand, with a foundation guard** (lite#394; `sponsor_patterns.json` schema 4, which differs from `tests/data/funder_names.json`). Reconcile the two corpora into one and re-run the funder measurement, not a string diff;
  - **statement recognition caveats**: COI or data-sharing wording found where no statement was recognised (lite#420), and unrecognised end matter (lite#428);
  - **JATS-tagged statements as headings** in the full-text scan (`fn-type`, `notes-type`, `sec-type`; bold run-in headings split; COI in `author-notes`; de-duplication). Compare with bmlib's #119 and #257 handling first and adopt only what bmlib lacks. Mind lite's memories "more recognition can create charges" and "markup tolerance exposes loose heading rules": diff the charge outcomes old against new, not the section counts.
- **WP-B8 JATS consumer surface:**
  - structured parse warnings on `JATSArticle` (Swift shows them to the reader as `JATSParseWarnings`; bmlib only logs);
  - a warning when the deposit's PMC ID differs from the one requested (Swift).

  Decide each in `docs/DECISIONS.md`. Logging is not reporting.
- **WP-B9 An FFI crate for the mobile apps** (`rust/bmlib-ffi`, UniFFI). This is the bmlib half of lite's WP-2 spike:
  - a `mobile` feature set without `db`, `postgres` or `publications.sync`;
  - `HttpClient` exposed as a UniFFI callback interface, so the apps keep their own transports (pacing, bot-wall handling, the in-app browser rescue);
  - first surface: `parse_jats`, then `json_repair` / span location, the citation formatters, the quality extractors and narrowing, the retraction rule and funder matching;
  - CI builds an xcframework and Android `.so` files from a tag, so lite pins a version rather than a commit.
- **WP-B10 A conformance suite consumers can vendor.** Publish `rust/oracle/*_cases.json` with a version and a manifest (`rust/oracle/MANIFEST.json`: file, schema, bmlib commit), so lite's Swift and Kotlin tests can assert against the same cases the Rust port meets. Lite contract files adopted under Tasks B2, B3 and WP-B4/B5 go in the same manifest.
- **WP-B11 Decisions to revisit with lite's data (no code until decided):**
  - **skipping the Europe PMC full-text request when `isOpenAccess: N`**: bmlib refused this (bmlib#216); lite measured it (lite#432, #451) and accepts losing PMC9391270. Bring lite's measurement to the decision;
  - **no cap on own trial ids**: lite has none, bmlib caps at 2;
  - **`pty`** in the industry words: lite declined it for lack of corpus evidence.

## Appendix A: What lite has that bmlib lacks

| Item | Lite source | Contract | bmlib today | WP |
|---|---|---|---|---|
| Caller's HTTP client | `polite_session.py` (consumer need) | — | clients opened internally | B1 |
| Unpaywall landing page → `citation_pdf_url` | `oa_landing_page.py` (lite#464, #465) | `unpaywall_landing_page.json` | **defect**: `url_for_pdf or url` | B2 |
| Bot-wall recognition | `bot_walls.py` (lite#480) | `challenge_detection.json` | none | B3 |
| Wrong-article PDF check, checked before caching | `pdf_title_check.py`, `pdf_library_check.py` (lite#532, #541, #546, #560, #561) | `pdf_title_check.json` | none (`_titles.py` judges metadata titles only) | B4 |
| PMC open-data bucket | `pmc_open_data.py` | `pmc_open_data.json` | efetch tier only | B5 |
| OpenAlex PDF locations | `openalex.py` | `openalex_locations.json` | OpenAlex in `publications` only | B5 |
| Preprints by `PPR` accession | `fulltext_discovery.py` | `fulltext_retrieval.md` | not reachable by design | B5 |
| CORE text; Elsevier API; keyed session | `core_api.py`, `elsevier_api.py`, `keyed_service_session.py` | `core_fulltext.json`, `elsevier_article.json` | none | B5 |
| Per-host polite pacing | `rate_limit.py`, `polite_session.py` | `polite_request_pacing.md` | per-analyzer interval only | B6 |
| Funders by brand + foundation guard | `transparency/` (lite#394) | `sponsor_patterns.json` v4 | name lists | B7 |
| Statement caveats (lite#420, #428); tagged statements as headings | `transparency/assessment`, `jats_markdown.py` | `transparency_parity/` | partial | B7 |
| Parse warnings to the reader; PMC ID mismatch | Swift `JATSParseWarnings` | — | logged only | B8 |
| One implementation on mobile | — | — | Rust crate, no FFI | B9 |
| Shared conformance cases | `doc/cross_platform/*` | — | oracle cases, unversioned | B10 |
| `isOpenAccess` pre-check; trial-id cap; `pty` | lite#432, #451 | — | decided otherwise | B11 |

Not proposed: lite's relevance-scoring parser (bmlib has no scoring agent); the in-app browser rescue and review queue (GUI); checkpointing, sync and the error queue (app concerns).
