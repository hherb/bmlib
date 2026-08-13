# bmlib.fulltext — Full-Text Retrieval, JATS Parsing & PDF Conversion

Full-text retrieval for biomedical literature. Provides a multi-tier retrieval chain (caller-supplied sources → Europe PMC → NCBI PMC → Unpaywall → DOI/PubMed), a SAX-based JATS parser that converts PubMed Central XML to structured data or HTML, a disk cache for downloaded content, and a pluggable PDF-to-text converter that the retrieval chain uses to make a PDF-only article readable inline.

## Installation

```bash
pip install bmlib                   # JATSParser, FullTextCache, SectionSegmenter, models
pip install bmlib[fulltext]         # + FullTextService retrieval (httpx)
pip install bmlib[pdf]              # + PDF → text conversion (pymupdf)
```

Only `FullTextService` needs `httpx`, and it is resolved on first access, so `import bmlib.fulltext` loads no httpx and does not even load `service` — the parser, the models, the cache and the segmenter all import on core bmlib alone. Constructing the service without the extra raises `ImportError: httpx is required for full-text retrieval, but importing it failed (No module named 'httpx'). Install with: pip install bmlib[fulltext]`. The parenthesised cause is what was actually raised, so a *broken* httpx install is not misreported as an absent one. `pymupdf` is required **only** for `PyMuPDFConverter`; everything else in the module works without it.

## Module layout

| Submodule | Contents | Part of the retrieval chain? |
|-----------|----------|------------------------------|
| `service` | `FullTextService`, `FullTextError` | Yes |
| `jats_parser` | `JATSParser` | Yes — used by the service to render XML |
| `cache` | `FullTextCache`, `sanitize_identifier()` | Yes — the service constructs one by default |
| `models` | `FullTextResult`, `FullTextSourceEntry`, `SegmentedDocument`, `Section`, `TextBlock`, `SectionType`, all `JATS*` dataclasses | Yes |
| `pdf_converter` | `ConversionResult`, `PDFConverter`, `PyMuPDFConverter`, `get_converter()`, `list_converters()`, `render_html()` | Yes — the service extracts a retrieved PDF's text |
| `segmenter` | `SectionSegmenter`, heading-detection patterns | Standalone — segments the text lines from `PyMuPDFConverter.extract_blocks()` |

> **A retrieved PDF is extracted into `FullTextResult.html`.**
> When the `bmlib[pdf]` extra is installed, the retrieval chain runs a cached PDF through `render_html()` and puts the result in [`FullTextResult.html`](#fulltextresult) with `content_kind="extracted"`. Two conditions apply: extraction happens only *after* the PDF is cached, so `fetch_fulltext()` must be given an `identifier`; and without the extra the result simply carries no HTML. Opt out with `FullTextService(convert_pdfs=False)`.
>
> `pdf_url` and `file_path` stay populated either way — extraction recovers the prose but not figures, tables or layout, so the original PDF is still worth offering.

## Imports

```python
from bmlib.fulltext import (
    # Service
    FullTextService,
    FullTextError,
    # Parser
    JATSParser,
    # Cache
    FullTextCache,
    # PDF conversion
    ConversionResult,
    PDFConverter,
    PyMuPDFConverter,
    get_converter,
    list_converters,
    # Data models
    FullTextResult,
    FullTextSourceEntry,
    JATSArticle,
    JATSAuthorInfo,
    JATSAbstractSection,
    JATSBodySection,
    JATSFigureInfo,
    JATSTableInfo,
    JATSReferenceInfo,
    # PDF conversion
    ConversionResult,
    PDFConverter,
    PyMuPDFConverter,
    get_converter,
    list_converters,
    render_html,
)
```

The list above is the complete `bmlib.fulltext.__all__` (18 names). A few public symbols are **not** re-exported at package level and must be imported from their submodule:

```python
from bmlib.fulltext.cache import sanitize_identifier
from bmlib.fulltext.pdf_converter import CONVERTER_PYMUPDF, DEFAULT_CONVERTER
```

---

## Quick Start

### Retrieve full text for a paper

```python
from bmlib.fulltext import FullTextService, FullTextError

service = FullTextService(email="researcher@example.com")

try:
    result = service.fetch_fulltext(
        pmc_id="PMC7614751",
        doi="10.1234/example",
        pmid="34567890",
        identifier="10.1234/example",   # enables the disk cache
    )
except FullTextError as e:
    print(f"No identifiers to work with: {e}")
else:
    print(result.source)     # "europepmc", "unpaywall", "cached", ...
    if result.html:
        print(result.html[:200])   # Parsed HTML from JATS XML
    if result.file_path:
        print(result.file_path)    # Locally cached PDF
    if result.pdf_url:
        print(result.pdf_url)      # Open-access PDF URL
    if result.web_url:
        print(result.web_url)      # Publisher or PubMed landing page
```

### Parse JATS XML directly

```python
from pathlib import Path
from bmlib.fulltext import JATSParser

xml_bytes = Path("article.xml").read_bytes()   # bytes, not str

# Get structured data
article = JATSParser(xml_bytes).parse()
print(article.title)
print(article.authors[0].full_name)
for sec in article.abstract_sections:
    print(f"  {sec.title}: {sec.content[:80]}...")

# Get HTML rendering
html = JATSParser(xml_bytes, known_pmc_id="PMC7614751").to_html()
```

### Cache downloaded content

```python
from bmlib.fulltext import FullTextCache

# Uses platform-appropriate default directory
cache = FullTextCache()

# Or specify a custom directory
cache = FullTextCache(cache_dir="/data/fulltext_cache")

# Cache HTML from Europe PMC
cache.save_html(result.html, identifier="PMC7614751")

# Later, retrieve it
html = cache.get_html("PMC7614751")

# Cache a PDF
path = cache.save_pdf(pdf_bytes, identifier="10.1234/example")

# Retrieve cached PDF path
pdf_path = cache.get_pdf("10.1234/example")
```

### Convert a PDF to text

```python
from pathlib import Path
from bmlib.fulltext import get_converter

result = get_converter().convert(Path("paper.pdf"))
if result.success:
    print(result.text)
```

---

## FullTextService

Retrieves full text from several sources, falling back through a fixed sequence.

```python
class FullTextService:
    def __init__(
        self,
        email: str,
        timeout: float = 30.0,
        cache: FullTextCache | None = None,
        convert_pdfs: bool = True,
    ) -> None: ...

    def fetch_fulltext(
        self,
        *,
        fulltext_sources: list[FullTextSourceEntry] | None = None,
        pmc_id: str | None = None,
        doi: str | None = None,
        pmid: str = "",
        identifier: str | None = None,
    ) -> FullTextResult: ...
```

`fetch_fulltext()` is the only public method.

| Parameter | Type | Description |
|-----------|------|-------------|
| `email` | `str` | Contact email, required by the Unpaywall API. No default — always supply a real address |
| `timeout` | `float` | HTTP request timeout in seconds (default `30.0`) |
| `cache` | `FullTextCache \| None` | Cache instance. When `None`, a default `FullTextCache()` is constructed — and if that directory cannot be created, the service warns once and runs uncached rather than raising, so `service.cache` may be `None` |
| `convert_pdfs` | `bool` | Extract a retrieved PDF's text into `html` (default `True`). Requires `bmlib[pdf]`, and only applies once the PDF is cached — so `fetch_fulltext()` must be given an `identifier` **and** the service must have a cache. `pdf_url` stays set either way; `file_path` too, whenever the PDF was actually downloaded |
| `ncbi_api_key` | `str \| None` | Optional NCBI API key (default `None`), sent with the Tier 1b′ and Tier 1c requests. Moves them into the key's 10 requests/second allowance instead of the 3/s shared by everything on the IP. It does **not** change bmlib's pacing — the package still throttles nothing. Declared last, so positional construction stays stable |

### `fetch_fulltext()`

All arguments are **keyword-only**.

| Parameter | Type | Description |
|-----------|------|-------------|
| `fulltext_sources` | `list[FullTextSourceEntry] \| None` | Known source URLs from a publication fetcher — tried first (Tier 0) |
| `pmc_id` | `str \| None` | PubMed Central ID (e.g. `"PMC7614751"`) — triggers Tier 1a, and Tier 1c if Europe PMC gives no body for it. A bare numeric ID is prefixed with `PMC`; anything that is not then `PMC` followed by digits is rejected before it reaches a URL |
| `doi` | `str \| None` | Digital Object Identifier — drives Tiers 1b, 1b′, 2 and 3 |
| `pmid` | `str` | PubMed ID — used for the Tier 1b and 1b′ lookups (the converter prefers it over the DOI) and as the final fallback URL |
| `identifier` | `str \| None` | Cache key, typically the DOI. **Disk caching only happens when this is supplied**; without it nothing is read from or written to the cache |

**Returns:** `FullTextResult` — always populated with at least a `source` and one of `html` / `pdf_url` / `web_url` / `file_path`.

**Raises:** `FullTextError`, in exactly two cases, both needing `doi` and `pmid` to be absent:

- `"No identifiers provided"` — `fulltext_sources`, `pmc_id`, `doi` and `pmid` are all empty. Raised before any request, and before the warning below, since nothing was asked of any source.
- `"Nothing retrieved and no DOI or PMID to fall back on — <summary>"` — a `pmc_id` or `fulltext_sources` *was* given and the chain exhausted, leaving no link to degrade to. It used to raise the message above, which sent the reader looking for a missing argument that was not missing. Every individual tier failure is swallowed and logged at `DEBUG` (with `exc_info=True`), then the next tier is tried — but a chain that ends up empty-handed reports itself at `WARNING` (see "Telling a failure from an absence" below).

### Retrieval sequence

The chain is longer than three tiers. In order:

| Step | Condition | Action | `source` on success |
|------|-----------|--------|---------------------|
| Cache | `identifier` given | Look up `sanitize_identifier(identifier)`; HTML is checked before PDF | `"cached"` |
| Tier 0 | `fulltext_sources` given | Try entries in priority order `xml` (0) > `pdf` (1) > `html` (2), unknown formats last (99) | `entry.source` (e.g. `"biorxiv"`) |
| Tier 1a | `pmc_id` given | `GET .../{PMCxxxx}/fullTextXML`, parsed to HTML by `JATSParser` | `"europepmc"` |
| Tier 1b | `pmc_id` **not** given, and `doi` or `pmid` given | Europe PMC search (`resultType=core&pageSize=1`, query `DOI:{doi}` else `EXT_ID:{pmid}`); the PMC ID is used only if `inEPMC == "Y"`, then fetched as in 1a | `"europepmc"` |
| Tier 1b′ | The search reported no PMC ID, **or the search itself failed** | NCBI's [ID Converter](https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/), asked by PMID when there is one and DOI otherwise. Consulted *second* — the Europe PMC search returns the PMC ID and the free-PDF URL in one request, so asking the converter first would cost a request on every lookup or forfeit that URL. But it is consulted even when that search raised, since a second independent resolver is worth most exactly then. A record that is `status: error`, not `live`, or carries a PMC ID failing `PMC\d+` resolves to nothing | — |
| Tier 1c | A PMC ID is in hand — the caller's, or one either resolver found — and Europe PMC gave no body for it | `GET eutils…/efetch.fcgi?db=pmc&id={digits}&retmode=xml`, parsed by `JATSParser`. Europe PMC serves the corpus its `inEPMC` flag describes; NCBI serves PMC itself. A reply carrying neither body nor abstract — efetch's answer for an article whose publisher does not release XML — is treated as a failure, not as an abstract | `"ncbi_pmc"` |
| PDF-URL recovery | Tier 1a failed and no render URL known yet | Re-run the same search purely to obtain a PDF URL | — |
| Tier 1d | A free PDF render URL was found | Take the `fullTextUrlList` entry with `documentStyle == "pdf"` and an accepted availability — `availabilityCode` `OA` or `F`, falling back to the `availability` display string (`"Open access"`/`"Free"`) only for an entry carrying no code; a present-but-unknown code is rejected outright — download and cache it | `"europepmc_pdf"` |
| Tier 2 | `doi` given | Unpaywall `GET .../{doi}?email=...`; picks `best_oa_location.url_for_pdf` or `.url`, else iterates `oa_locations`; downloads and caches | `"unpaywall"` |
| Tier 3 | `doi` given | Return `https://doi.org/{doi}` | `"doi"` |
| Final | `pmid` given | Return `https://pubmed.ncbi.nlm.nih.gov/{pmid}/` | `"pubmed"` |
| — | no `doi` and no `pmid`, and nothing was retrieved | `raise FullTextError` — `"No identifiers provided"` when nothing at all was given, otherwise a message naming the missing fallback and summarising the failures | — |

Within Tier 0, an `xml` entry is fetched and JATS-parsed into HTML, a `pdf` entry sets `pdf_url` and is downloaded into the cache, and an `html` entry sets `web_url` only — HTML sources are never cached.

Every "downloads and caches" above is conditional on there being somewhere to put the file. When `fetch_fulltext()` was given no `identifier`, or `service.cache is None` (see [the operational notes](#operational-notes)), the PDF tiers set `pdf_url` and stop there: no request is made and `file_path` stays unset. The tier still counts as having produced a result, so the chain does not fall through to the next one.

### Operational notes

- **No rate limiting.** The package sleeps for nothing and throttles nothing. Callers hitting Europe PMC, NCBI or Unpaywall in bulk must implement their own pacing. This matters most for NCBI: Tiers 1b′ and 1c add up to two NCBI requests per lookup that misses at Europe PMC, and NCBI enforces its limit — 3 requests/second per IP, or 10 with an `ncbi_api_key` — by blocking, where the other sources are more forgiving. Setting the key raises the ceiling; it does not add pacing.
- **No environment variables.** The two credential-like inputs are both constructor arguments: the Unpaywall contact email (also sent to NCBI as the `email` E-utilities parameter) and the optional `ncbi_api_key`. Neither is read from the environment.
- **One client per request.** Every HTTP call goes through an internal helper that opens a fresh `httpx.Client` with `follow_redirects=True`. There is no connection pooling across calls.
- **PDF download failure is non-fatal, and now reported (#68).** When a PDF cannot be downloaded or fails magic-byte validation, the result is still returned with `pdf_url` set, so the URL remains a usable fallback; only `file_path` is left unset. If a body-less JATS document was seen earlier in the chain, its abstract is merged into that result rather than discarded, so the caller gets an abstract plus a link instead of a bare link. Three distinct outcomes, previously all logged at `DEBUG` and so silent at every level above it, are now told apart:

  ```
  Could not download a europepmc_pdf PDF (HTTP 404; first seen at https://europepmc.org/articles/PMC1234567?pdf=render). The URL is left on the result, but there is no file and no extracted text. This is reported once — run with DEBUG logging to see every affected article.

  Could not download a unpaywall PDF (the response is not a PDF; first seen at https://.../landing-page). The URL is left on the result, but there is no file and no extracted text. This is reported once — run with DEBUG logging to see every affected article.

  Could not download a unpaywall PDF (ConnectError: ...). The URL is left on the result, but there is no file and no extracted text, and this will affect every article served this way. Further ConnectError failures will not be repeated.
  ```

  The first two — a non-200 response and a payload that fails magic-byte validation (Unpaywall's usual failure: a landing page rather than a PDF) — are server-side outcomes reported once per `(tier, cause)`, with the per-article detail staying at `DEBUG`. The level was set from a measured rate, not by taste, against a rule fixed beforehand: under 5% of attempts a per-article `WARNING` is affordable, at or above it a bulk run's log would be drowned exactly when it mattered. Measured with `scripts/sample_free_pdf_urls.py`: `europepmc` 0.7% failed, `unpaywall` 64.3% failed (14 of its 18 failures landing pages, the other 4 HTTP 403), `biorxiv` 0.7% failed — see CHANGELOG for the full figures and confidence intervals. The third — any exception, meaning the environment rather than the server (a lost network, a full disk) — needed no measurement, since it fails every article once it starts failing, and is reported once per `(tier, exception type)` regardless of the rate rule above. A failed cache *write* is reported through the cache-write warning below instead of either of these: `_save_pdf_to_cache` tells a failed write apart from a failed validation, so a read-only cache directory is never blamed on the publisher's bytes.

  **The key is the downloading tier — `europepmc_pdf`, `unpaywall` or `known_source` — not the `source` the message names.** For Tiers 1d and 2 those coincide, but a Tier 0 `source` comes from the fetcher's `FullTextSourceEntry`, and OpenAlex derives it from the location's venue display name: one distinct string per journal or repository. Keyed on that, the "reported once" above would be one warning *per article* over a bulk sync — potentially at the arbitrary-repository population Unpaywall draws from, which measured worst at 64.3%; Tier 0 shares that population for an OpenAlex-supplied link, though the one Tier 0 population actually sampled (bioRxiv, serving its own host) measured 0.7%. The venue still appears in the message text, so the first report names it.

  The message says the report is one-shot; it does not say the failure is common, and the difference is deliberate. Widening Tier 1d's availability allow-list (#79) made `europepmc_pdf` the dominant emitter of this line, and Europe PMC recorded **zero** server-side failures in the measurement above.

- **A swallowed bmlib defect is reported immediately, not only once the chain is fully exhausted (#72).** `TypeError`, `AttributeError`, `NameError`, `KeyError` and `IndexError` are held to mean a defect in bmlib itself rather than a remote-data or environment failure — `ValueError` and `SyntaxError` are deliberately excluded from that list, since `json.JSONDecodeError` *is* a `ValueError` and `xml.etree.ElementTree.ParseError` *is* a `SyntaxError`, and both of those are ordinary remote-data failures rather than bugs. The list is knowingly over-inclusive in the other direction: a malformed payload *can* provoke one of these — `data.get("resultList", {}).get("result", [])` raises `AttributeError` when Europe PMC returns a non-dict there — and the answer is that such a case is a missing shape check, which is a bmlib defect too. The message describes what happened rather than accusing the article. Previously, an exception of this shape was swallowed exactly like any other tier failure, so it surfaced only inside the exhaustion summary's "attempts failed" bucket (see below) — and that summary is consulted only on *total* exhaustion. An `AttributeError` from every PMC tier, with Unpaywall still healthy, therefore degraded a whole corpus from structured JATS to bare links while reporting success throughout. It now emits its own `WARNING` at the moment the exception is swallowed, independent of whether a later tier goes on to succeed:

  ```
  A full-text tier failed with AttributeError (no attribute 'has_body'), which bmlib does not raise deliberately — this is a defect, possibly provoked by an unexpected API response. Full text may be silently degraded for every article in this run while later tiers keep succeeding. Run with DEBUG logging for the traceback and please report it; further AttributeError failures will not be repeated.
  ```

  Once per `(service, exception type)`: a defect that hits one tier hits it for every article in the run, so per-article would be unreadable exactly when it mattered most, but a second, different defect still gets its own line rather than hiding behind the first.
- **PDF text extraction is best-effort and logged.** A missing `bmlib[pdf]` extra, a corrupt PDF, or a scan with no extractable text all leave `html` unset and emit a `WARNING`; a partial extraction is attached but flagged. Nothing here aborts a retrieval.
- **Extracted PDF text is not cached; it is re-derived.** Only body-carrying JATS HTML is written to the HTML cache, so a cached HTML hit always means full text. A cached *PDF* hit re-runs extraction on the local file, so a second `fetch_fulltext()` returns the same `html` and `content_kind` as the first.
- **Caching is opt-in per call.** The service normally holds a `FullTextCache`, but reads and writes only occur when `identifier` is passed.
- **A cache directory that cannot be *created* does not fail construction.** When the service builds the default cache itself and the directory cannot be made — a file standing where it should be, a read-only parent, no determinable home directory — it emits one `WARNING` naming what was raised, sets `service.cache` to `None`, and retrieves without caching. Retrieval never needed a cache, so aborting there would have taken down a run that had every chance of succeeding. A cache you construct and pass in yourself still raises; see [FullTextCache](#fulltextcache).
- **Without a cache, a PDF is not downloaded at all.** This is the half of the degraded state worth knowing before you rely on it, and the `WARNING` above says so: a PDF is fetched *into* the cache, so with `service.cache is None` the download is skipped, `file_path` is never set, and `convert_pdfs` has nothing to extract from. A PDF-only article therefore comes back carrying `pdf_url` alone — lost content, not merely repeated network traffic. JATS full text is unaffected: it still parses and is still returned, only the write is skipped. The per-article line about the skipped download stays at `DEBUG`, since the construction warning already named the consequence; it is *not* gated on `convert_pdfs`, because `file_path` is lost whatever that flag says.
- **A cache that cannot be written to is reported once.** A read-only cache directory or a full disk does not fail a retrieval — the content is already in hand — but it means every later run re-fetches the whole corpus over the network. The first failed write emits a `WARNING` naming what was raised; the rest stay at `DEBUG`, since the cause is a property of the directory rather than of the article. HTML and PDF writes share the one warning, so a PDF-only corpus is not left silent.
- **A cache entry is never half-written.** Both writes go to a temporary file and are published with `os.replace`, so a write that fails partway leaves the previous entry — or nothing — rather than a truncated article. This matters because a truncated HTML file decodes perfectly and would then be served as `content_kind="fulltext"` from `source="cached"` on every later run, with nothing logged at any level: `quality/` would score a paper whose Methods and Results do not exist. Note the scope: this closes the window in which such an entry is *written*. It does not detect one already on disk — a truncation of English-language prose usually lands on an ASCII boundary and decodes fine — so a cache written by bmlib before 0.9.0 is best cleared once.
- **A cache entry that cannot be *read* does not fail the retrieval either.** An entry corrupted by something outside bmlib — a killed process, a manual edit, a filesystem fault — is reported with a `WARNING` naming the cache key and the exception type, and the retrieval chain runs as though the cache had missed. Unlike the write warning above this is emitted per article, because the cause is a property of that one file. The bad entry is not deleted, but it *is* renamed with a `.corrupt` suffix, which takes it out of the lookup path while leaving the bytes for you to inspect. Leaving it in place was not viable: only a re-fetch that returns JATS full text overwrites the HTML entry, so an article served as a PDF kept warning and re-downloading on every run — the undecodable entry is read first, so it hid the freshly cached PDF behind it. `clear()` sweeps the `.corrupt` files up.

### Telling a failure from an absence

Most papers have no free full text, and for those the chain legitimately ends at Tier 3 with a bare link. A caller who has lost the network, hit a bmlib bug or misconfigured the service gets a result of exactly the same shape — so `fetch_fulltext()` emits one `WARNING` whenever it comes up empty-handed, and that line carries the evidence needed to tell the two apart:

```
No full text found for doi=10.1/x pmid=456 — nothing was retrieved; 3 attempts failed (ConnectError)
No full text found for doi=10.1/x pmid=456 — nothing was retrieved; 3 sources had nothing
No full text found for doi=10.1/x pmid=456 — returning the abstract only; 2 attempts failed (OSError); 1 source had nothing
```

Read it in two halves. The first says what came back — one of three:

- `nothing was retrieved` — the chain fell through to a DOI or PubMed link.
- `returning the abstract only` — a body-less JATS document was held back earlier and is returned with the link hung off it.
- `nothing was retrieved and there is no link to fall back on` — no `doi` and no `pmid`, so there is nothing to degrade to and `fetch_fulltext()` raises after this line.

The second half sorts what happened into two buckets, and only one of them is worth acting on:

- **`N sources had nothing`** — that many sources answered, and answered that they hold no free full text. This is the ordinary outcome for a paywalled paper, and a corpus of these lines is not a problem.
- **`N attempts failed (types…)`** — that many attempts could not get an answer. `ConnectError` or `ReadTimeout` across a corpus is a network or firewall problem; a bare `FullTextError` is a source returning a 5xx or unparseable data; `TypeError` or `AttributeError` is a bug worth reporting. A bug of that shape does not wait for this summary to say so: it gets its own immediate `WARNING`, described under "Operational notes" above, the moment it is swallowed — which is what lets it surface even when a later tier goes on to succeed and this exhaustion summary is never reached.

The two are decided by exception class, not by wording: `FullTextUnavailableError` (a subclass of `FullTextError`) is what a source raises when it answered and has nothing, and a source that reports an absence by *returning* is counted in the same bucket. So an Unpaywall 404 and an Unpaywall 503 no longer look alike.

The word is **attempts**, not tiers: Tier 0 makes one attempt per fetcher-supplied source, so the number is not bounded by the chain's eight tiers. Which attempt failed, and its traceback, stays at `DEBUG` on `bmlib.fulltext.service`.

`no attempt reported a failure` is the remaining case: nothing raised and no source reported an absence, which happens when every attempt returned something — a body-less JATS document, say — rather than failing.

A successful retrieval emits no *exhaustion* warning. It may still warn about something else: a cache it could not write to, or a PDF whose text would not extract.

---

## FullTextResult

Result of a full-text retrieval attempt.

```python
@dataclass
class FullTextResult:
    source: str                    # see table below
    html: str | None = None        # Rendered article HTML
    pdf_url: str | None = None     # Open-access PDF URL
    web_url: str | None = None     # Publisher / PubMed website URL
    file_path: str | None = None   # Local cached file path
    content_kind: ContentKind = "none"   # what `html` actually is
```

### `content_kind`

`html` being set does **not** mean you have the article. `content_kind` says
which of three quite different things it holds:

| Value | `html` holds | Notes |
|-------|--------------|-------|
| `"fulltext"` | A JATS document that had a `<body>` | The real thing |
| `"abstract"` | A body-less JATS rendering — abstract and metadata only | Returned only as a last resort, when no tier found the article. Never cached. `web_url` is attached so the reader has somewhere to go |
| `"extracted"` | Prose recovered from a PDF | No figures, tables or layout, and possibly not every page — `pdf_url`/`file_path` remain worth offering |
| `"none"` | Nothing — `html` is `None` | |

Code that scores, summarises or analyses an article should check
`content_kind == "fulltext"` rather than `if result.html`. Some publishers
(medRxiv among them) serve a JATS document made of `<front>` and `<back>`
alone: it returns HTTP 200 and parses cleanly, but there is no article in it.

The `source` values the service can emit:

| `source` | Meaning | Typically populated |
|----------|---------|---------------------|
| `"cached"` | Served from the disk cache | `html` or `file_path` |
| *fetcher name* | A Tier 0 `FullTextSourceEntry` — the entry's own `source` string, e.g. `"biorxiv"`, `"medrxiv"`, `"pmc"`, `"publisher"` | `html`, or `pdf_url` (+ `file_path`), or `web_url` |
| `"europepmc"` | JATS XML from Europe PMC, rendered to HTML | `html` |
| `"europepmc_pdf"` | Free PDF render URL from Europe PMC | `pdf_url` (+ `file_path` if cached) |
| `"ncbi_pmc"` | Tier 1c — NCBI's own copy of the PMC article, via E-utilities `efetch`. Distinct from a Tier 0 entry whose fetcher named itself `"pmc"` | `html` |
| `"unpaywall"` | Open-access PDF located via Unpaywall | `pdf_url` (+ `file_path` if cached) |
| `"doi"` | DOI resolution fallback | `web_url` |
| `"pubmed"` | PubMed landing-page fallback | `web_url` |

The dataclass comment lists only `europepmc`, `unpaywall`, `doi`, `pubmed`, `cached`; treat the table above as authoritative, since Tier 0, Tier 1c and Tier 1d also emit values.

---

## FullTextSourceEntry

A known full-text source URL discovered by a publication fetcher, consumed by `FullTextService` as Tier 0.

```python
@dataclass
class FullTextSourceEntry:
    url: str
    format: str                    # "pdf", "xml", "html"
    source: str                    # e.g. "biorxiv", "medrxiv", "pmc", "publisher"
    open_access: bool = True
    version: str | None = None     # e.g. "preprint", "accepted", "published"

    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullTextSourceEntry: ...
```

`to_dict()` omits `version` when it is falsy. `from_dict()` defaults `open_access` to `True` and `version` to `None`.

```python
from bmlib.fulltext import FullTextService, FullTextSourceEntry

sources = [
    FullTextSourceEntry(url="https://.../v1.full.pdf", format="pdf", source="biorxiv"),
    FullTextSourceEntry(url="https://.../v1.source.xml", format="xml", source="biorxiv"),
]

# The XML entry is tried first regardless of list order.
result = FullTextService(email="me@example.com").fetch_fulltext(
    fulltext_sources=sources,
    doi="10.1101/2024.01.01.573000",
)
```

---

## JATSParser

SAX-based parser for JATS (Journal Article Tag Suite) XML, the standard format used by PubMed Central and Europe PMC. Ported from the Swift BioMedLit library. It is the only public symbol in `jats_parser`.

```python
class JATSParser:
    def __init__(self, data: bytes, known_pmc_id: str = "") -> None: ...

    def parse(self) -> JATSArticle: ...
    def to_html(self) -> str: ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `bytes` | Raw JATS XML content — **bytes, not `str`** |
| `known_pmc_id` | `str` | Optional PMC ID for constructing figure URLs. A bare numeric ID is prefixed with `PMC` |

**Security:** external entity loading is disabled on the SAX parser (`feature_external_ges` and `feature_external_pes` are both set to `False`), so hostile XML cannot pull in external resources.

**Performance:** `parse()` and `to_html()` each run a *fresh* SAX pass over the stored bytes. Calling both on the same instance parses the document twice — use `parse_with_html()`, which parses once and returns `(JATSArticle, str)`.

### `parse()` → `JATSArticle`

Returns a structured `JATSArticle` containing all parsed metadata, abstract sections, body sections, figures, tables, and references.

### `to_html()` → `str`

Returns an HTML string with semantic markup:
- `<h1>` title, `<h2>` section headings
- `<p class="authors">` with author list
- `<p class="journal-info">` with journal, volume, year
- `<p class="identifiers">` with linked DOI, PMC, PMID
- `<h2>Abstract</h2>` with `<strong>` section labels
- `<h2>`–`<h6>` for nested body sections
- `<figure>` with `<img>` and `<figcaption>` for figures
- `<div class="table-container">` with `<table>` for tables
- `<ol class="references">` for bibliography

### `parse_with_html()` → `tuple[JATSArticle, str]`

Parses once and returns both representations. Prefer this over calling
`parse()` and `to_html()` in turn, which runs two SAX passes over the same
bytes. The service uses it to render a document and check `has_body` in one
pass.

### Supported JATS elements

| JATS element | Parsed as |
|-------------|-----------|
| `front/article-meta` | Title, authors, journal, identifiers |
| `abstract/sec/title/p` | Structured abstract sections |
| `body/sec/title/p` | Body sections with nesting |
| `body/p` (no enclosing `<sec>`) | A titleless body section — see below |
| `fig/graphic/label/caption` | Figures with Europe PMC image URLs |
| `caption/title` + `caption/p` | Caption text, space-joined in document order |
| `table-wrap/thead/tbody/tr/th/td` | Tables (rendered as HTML `<table>`) |
| `ref-list/ref/element-citation` | Structured references |
| `bold/italic/sub/sup/monospace` | Inline formatting |
| `xref` | Cross-reference anchor links |

> **`<sec>` is optional inside `<body>`.** Prose in bare `<p>` children of
> `<body>` is collected into a `JATSBodySection` with an empty `title` — no
> heading is invented, so `to_html()` renders the paragraphs without one.
> Loose prose is flushed at each `<sec>` boundary, so an article that mixes
> the two keeps its document order and real sections stay top-level rather
> than becoming subsections of the loose prose. Such paragraphs count towards
> [`has_body`](#jatsarticle); empty ones are dropped, so a `<body>` holding
> only whitespace still reports no body.

> **Captions belong to their figure or table, wherever it sits.** JATS carries
> caption body in `<p>` and the caption lead in `<title>` — the same elements
> that carry section prose and section headings — so a `<fig>` inside a `<sec>`
> (the ordinary PMC layout), one floated directly under `<body>` after the last
> section, and one in back matter all resolve the same way: the text lands in
> `JATSFigureInfo.caption` / `JATSTableInfo.caption`, never in the enclosing
> section's paragraphs or title. A `<caption>` holding both a `<title>` and one
> or more `<p>` is space-joined in document order.
>
> Everything else inside a `<fig>` or `<table-wrap>` is furniture and is kept
> out of the prose: table cell text reaches `html_content` through the table
> renderer, and cell and `<table-wrap-foot>` paragraphs are not repeated into
> `body_sections`. Nothing inside a figure or table counts towards
> [`has_body`](#jatsarticle), so a `<body>` carrying only a captioned figure
> still reports no body.

---

## JATSArticle

Complete parsed article data. The first fifteen fields are required — construct it via `JATSParser.parse()` rather than by hand. `has_body` defaults to `False`, so a hand-built article reports "no body" unless it says otherwise.

```python
@dataclass
class JATSArticle:
    title: str
    authors: list[JATSAuthorInfo]
    journal: str
    volume: str
    issue: str
    pages: str
    year: str
    doi: str
    pmc_id: str
    pmid: str
    abstract_sections: list[JATSAbstractSection]
    body_sections: list[JATSBodySection]
    figures: list[JATSFigureInfo]
    tables: list[JATSTableInfo]
    references: list[JATSReferenceInfo]
    has_body: bool = False
```

**`has_body`** is `True` when `<body>` held at least one non-empty `<p>`
inside a `<sec>` — that is, body prose that survived parsing. It is what lets
`FullTextService` tell a real article from a body-less document that parses
cleanly but carries only the abstract.

It deliberately counts body paragraphs rather than `body_sections`, because
back-matter sections land in the latter too: a "Data Availability" section
would otherwise pass for an article body. Two consequences follow from
tracking what survived parsing rather than what the XML contained. A `<p>`
sitting directly in `<body>` with no enclosing `<sec>` is dropped by the
handler and so does not count — consistent with the rendered HTML, which has
no body prose either, but it means a valid article of that shape reads as
abstract-only. And the default is `False`, so a hand-built `JATSArticle`
reports "no body" unless it says otherwise.

### JATSAuthorInfo

```python
@dataclass
class JATSAuthorInfo:
    surname: str
    given_names: str = ""
    affiliations: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str: ...  # "John A Smith", or just the surname when
                                     # given_names is empty (e.g. "Consortium")
```

### JATSAbstractSection

```python
@dataclass
class JATSAbstractSection:
    title: str     # e.g. "Background", "Methods", "Results"
    content: str   # Section text
```

### JATSBodySection

```python
@dataclass
class JATSBodySection:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    subsections: list[JATSBodySection] = field(default_factory=list)
```

### JATSFigureInfo

```python
@dataclass
class JATSFigureInfo:
    id: str                        # XML id for cross-references
    label: str                     # e.g. "Figure 1"
    caption: str
    graphic_url: str | None = None # Constructed Europe PMC image URL
```

### JATSTableInfo

```python
@dataclass
class JATSTableInfo:
    id: str
    label: str              # e.g. "Table 1"
    caption: str
    html_content: str = ""  # Pre-rendered HTML <table>
```

### JATSReferenceInfo

```python
@dataclass
class JATSReferenceInfo:
    id: str
    label: str
    citation: str                            # Raw citation text (fallback)
    authors: list[str] = field(default_factory=list)
    article_title: str = ""
    source: str = ""                         # Journal name
    year: str = ""
    volume: str = ""
    issue: str = ""
    first_page: str = ""
    last_page: str = ""
    doi: str = ""
    pmid: str = ""

    @property
    def formatted_citation(self) -> str: ...
```

`formatted_citation` joins the populated components with `". "`. More than three authors collapse to `"first, second, et al."`; three or fewer are listed in full. When no structured component is populated at all, it returns the raw `citation` string unchanged.

---

## FullTextCache

Disk cache for downloaded PDFs and parsed HTML, organised into `pdfs/` and `html/` subdirectories.

**The constructor raises if it cannot create those directories** — a file standing where the directory should be, a read-only parent, a full disk. `FullTextService` does *not*: when it builds the default cache itself and that fails, it warns once and runs uncached, leaving `service.cache` as `None`. The asymmetry is deliberate. A caller who constructs a `FullTextCache` asked for a cache specifically, and handing back an object whose every method then failed one at a time would be worse than failing once, clearly, here.

Note that it makes **three** `mkdir` calls — the root, then `pdfs/` and `html/` — and only the first is suppressed by `exist_ok=True`. So a read-only root whose subdirectories do not yet exist raises here, at construction; it is not the "unwritable cache" case reported once on the first failed write. That case is reached when the subdirectories already exist and the write itself fails — an unwritable subdirectory, or a full disk.

```python
class FullTextCache:
    def __init__(self, cache_dir: str | Path | None = None) -> None: ...

    # PDF operations
    def save_pdf(self, data: bytes, identifier: str) -> str | None: ...
    def get_pdf(self, identifier: str) -> str | None: ...

    # HTML operations
    def save_html(self, html: str, identifier: str) -> str: ...
    def get_html(self, identifier: str) -> str | None: ...

    # Shared
    def quarantine(self, identifier: str) -> list[str]: ...
    def delete(self, identifier: str) -> None: ...
    def clear(self) -> None: ...
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `save_pdf(data, id)` | `str \| None` | Save PDF bytes; returns the path, or `None` (with a warning log) if the data is not a valid PDF. Raises `OSError` if the write fails |
| `get_pdf(id)` | `str \| None` | Returns the cached file path, or `None` |
| `save_html(html, id)` | `str` | Save an HTML string as UTF-8; returns the file path. Raises `OSError` if the write fails |
| `get_html(id)` | `str \| None` | Returns the cached HTML content, or `None` |
| `quarantine(id)` | `list[str]` | Rename any entry for the identifier that cannot be read to `<name>.corrupt`; returns the paths moved. A readable entry is left alone |
| `delete(id)` | `None` | Remove both cached entries for the identifier (missing ones are ignored) |
| `clear()` | `None` | Remove everything directly inside `pdfs/` and `html/`, including `.corrupt` and leftover temporary files |

PDF validation uses magic-byte checking against `PDF_MAGIC_BYTES = b"%PDF"`. Non-PDF data is **rejected with a warning log and a `None` return** — no exception is raised.

Both saves are **atomic**: the bytes go to a uniquely-named temporary file beside the target and are published with `os.replace`, so a write that runs out of space raises `OSError` and leaves the previous entry intact instead of a truncated one. The temporary file is dot-prefixed and removed on failure; `clear()` sweeps up any left by a killed process. The file's permissions are those an ordinary write would produce (0666 filtered by the umask), so a cache directory shared between users keeps working.

**Both saves can raise `OSError`**, and for a direct caller this is a real change rather than a relocation: a bare `write_text` under delayed allocation *returned a path* on a disk that was about to fill, leaving a truncated file behind. `FullTextService` catches it at both call sites and reports it once per service.

Reads carry no such guarantee, and **`get_html()` can raise** — a file corrupted by something other than bmlib fails its UTF-8 decode. `FullTextService` guards its own read, falls through to the network, and calls `quarantine()` so the next run is a clean miss; a direct caller of `FullTextCache` sees the error and can call `quarantine()` itself.

The cache has **no TTL, no size limit, and no eviction policy.** Entries live until `delete()` or `clear()` is called, or the directory is removed. Long-running processes should prune it themselves.

### Cache keys

Cache filenames are derived by `sanitize_identifier()`:

```python
from bmlib.fulltext.cache import sanitize_identifier

sanitize_identifier("10.1/a:b")   # "10.1_a_b_3f15c10f6c"
sanitize_identifier("10.1/a/b")   # "10.1_a_b_0f7d1c325e"
```

```python
safe   = re.sub(r"[^\w.\-]", "_", raw)[:160]
digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
return f"{safe}_{digest}"
```

The readable prefix is kept for debuggability; the ten-character digest of the **raw** identifier makes the key collision-free. The prefix is capped at 160 characters because the atomic write's temporary name adds 38 more, which would otherwise put a long identifier over `NAME_MAX` and fail a write that used to succeed. Truncation cannot cause a collision — the digest is taken over the whole raw identifier — but it does change the key for an identifier longer than that, so an entry cached for one by an older version is orphaned and re-fetched once.

> **This replaces a plain `re.sub(r"[^\w.\-]", "_", raw)` key, which was a correctness bug.**
> Every character outside `[\w.\-]` mapped to `_`, so distinct DOIs such as `10.1/a:b` and `10.1/a/b` collapsed onto the same cache file — and a lookup for one could return the **wrong article's** full text. Old cache files written under the un-hashed scheme are not found by the new key and are simply re-fetched; delete them or clear the cache directory to reclaim the space.

A second layer, applied inside *every* cache method, decides whether to sanitize at all: an identifier that already matches `[\w.\-]+` in full passes through **unchanged**, and anything else is run through `sanitize_identifier()`. Two consequences:

- `FullTextService` sanitizes once before calling the cache, and the already-safe result passes through untouched — the key is never double-hashed.
- A direct caller who passes a raw DOI containing `/` cannot escape the cache directory. This is defence in depth, not the primary path.

### Default cache directory

When `cache_dir` is not specified, the cache uses a platform-appropriate default:

| Platform | Default path |
|----------|-------------|
| macOS (`Darwin`) | `~/Library/Caches/bmlib/fulltext_cache/` |
| Windows | `~/AppData/Local/bmlib/fulltext_cache/`, falling back to `~/.cache/bmlib/fulltext_cache/` if that directory does not exist |
| Linux / other | `~/.cache/bmlib/fulltext_cache/` |

Despite the module docstring's reference to the XDG convention, **no environment variable is read at all** — neither `XDG_CACHE_HOME` on Linux nor `%LOCALAPPDATA%` on Windows. Every path above is built from `Path.home()`. Set `cache_dir` explicitly if you need to honour either.

That matters beyond pedantry: `Path.home()` raises `RuntimeError` — not `OSError` — where the home directory cannot be determined, which on POSIX means no `HOME` and no passwd entry, as in a distroless container. (On Windows the lookup consults `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`, and never `HOME`.) `FullTextService` catches that alongside the directory errors and degrades to no caching; a `FullTextCache()` constructed directly propagates it. Passing `cache_dir` avoids the call entirely.

### Directory layout

```
fulltext_cache/
├── pdfs/
│   ├── 10.1234_example_7ddfc9f2f4.pdf
│   └── 10.1101_2024.01.01.573000_75b26bb777.pdf
└── html/
    ├── 10.1234_example_7ddfc9f2f4.html
    └── PMC7614751.html
```

The hashed names come from `FullTextService`, which always sanitizes the `identifier` it is given. The bare `PMC7614751.html` is what a direct `cache.save_html(html, "PMC7614751")` produces — the identifier already matches `[\w.\-]+`, so it is used verbatim. The same string routed through the service would instead land in `PMC7614751_158cdf8b74.html`, so pick one access path per identifier and stay with it.

---

## PDF Conversion

Pluggable PDF-to-text conversion behind a stable `ConversionResult`, prioritising completeness of extracted text over formatting.

> **Also reachable through the retrieval chain.** `FullTextService` runs a cached PDF through `render_html()` and puts the prose in [`FullTextResult.html`](#fulltextresult) — see [Module layout](#module-layout) for the conditions. This section documents the direct API: `get_converter()` is the entry point, and you invoke it on a file path yourself.

The only built-in backend is `PyMuPDFConverter`, which needs the optional `pymupdf` dependency. **Importing `bmlib.fulltext` or `bmlib.fulltext.pdf_converter` never requires PyMuPDF** — the import is deferred to `PyMuPDFConverter.__init__`, so the cost is only paid when a converter is actually constructed.

### Registry

```python
CONVERTER_PYMUPDF = "pymupdf"
DEFAULT_CONVERTER = CONVERTER_PYMUPDF

def get_converter(name: str = DEFAULT_CONVERTER) -> PDFConverter: ...
def list_converters() -> list[str]: ...
```

| Function | Returns | Description |
|----------|---------|-------------|
| `get_converter(name)` | `PDFConverter` | An initialised converter (default `"pymupdf"`) |
| `list_converters()` | `list[str]` | Names of all registered converters — currently `["pymupdf"]` |

**Raises:** `ValueError` for an unknown name (`Unknown converter: '<name>'. Available converters: pymupdf`), or `ImportError` propagated from the converter's constructor when its optional dependency is missing (`Install with: pip install bmlib[pdf]`).

> **The converter registry is private and has no registration function.**
> Unlike the [LLM provider registry](llm.md) (`register_provider()`) and the publication fetcher registry (`bmlib.publications.register_source()`), `pdf_converter` exposes no public hook. Adding a backend means editing the module-level `_CONVERTER_REGISTRY` dict in `bmlib/fulltext/pdf_converter.py`. A third-party `PDFConverter` subclass is perfectly usable — just instantiate and call it directly; `get_converter()` will not find it.

### ConversionResult

```python
@dataclass
class ConversionResult:
    success: bool
    text: str
    format: str                    # "plaintext" or "markdown"
    page_count: int
    converted_pages: int
    char_count: int
    warnings: list[str] = field(default_factory=list)
    converter_name: str = ""
    converter_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    page_texts: list[str] = field(default_factory=list)
    title: str | None = None       # unreleased

    @property
    def is_complete(self) -> bool: ...
    @property
    def completion_ratio(self) -> float: ...
    def __str__(self) -> str: ...
```

| Member | Semantics |
|--------|-----------|
| `is_complete` | `success and page_count == converted_pages and char_count > 0` |
| `completion_ratio` | `converted_pages / page_count`, or `0.0` when `page_count == 0` |
| `__str__` | `ConversionResult(SUCCESS, complete, 12/12 pages, 41893 chars, converter=pymupdf)` |

`format` is documented as `"plaintext"` or `"markdown"`, but **`PyMuPDFConverter` always emits `"plaintext"`** — no built-in backend produces markdown. The field exists for future backends such as `pymupdf4llm`.

**`page_texts`** holds the text of each page *that yielded any*, in order. A
page with no extractable text (an image-only scan) contributes no entry, so
this is **not indexable by page number** and its length can be less than
`page_count`. It exists because page boundaries are what let `render_html()`
recognise furniture that repeats across pages. A backend that cannot report
pages separately leaves it empty.

**`title`** (unreleased) is the document's title where page 1 corroborates the
metadata's claim to it, and `None` otherwise — the same rule the segmenter
applies, described under [How the title is chosen](#how-the-title-is-chosen-unreleased).
**Read this rather than `metadata["title"]`**, which stays a verbatim record
of what the PDF says, junk and all: a caller debugging provenance needs the
raw string, and `creator`/`producer` sit beside it unmodified. A PDF whose
metadata title is `"Microsoft Word - manuscript.docx"` therefore has
`result.title is None` and `result.metadata["title"] == "Microsoft Word -
manuscript.docx"`.

### `render_html(result: ConversionResult) -> str`

Renders extracted PDF text as readable HTML — a fragment of `<p>` elements,
escaped. Returns `""` when the conversion failed or produced no text.

Two heuristics, both publisher-agnostic:

- **Repeated-line stripping.** A line appearing on at least
  `REPEATED_LINE_RATIO` (0.6) of the pages is treated as a running head,
  footer or watermark and dropped. `ceil()` and a floor of
  `REPEATED_LINE_MIN_PAGES` (3) push the effective share higher on short
  documents — 100% at 3 pages, 75% at 4 — converging on 0.6 as the page count
  grows. Below 3 pages nothing is stripped, since a repeat is as likely to be
  prose. Lines are counted once per page, so a phrase repeating *within* one
  page is not mistaken for furniture.
- **Paragraph reflow.** A PDF carries no paragraph marks: text wraps hard at
  the column edge, so a line falling below `PARAGRAPH_BREAK_RATIO` (0.85) of
  the column width is where the paragraph ended. The width is estimated as
  the 90th-percentile line length, which discards over-long outliers; when
  fewer than a tenth of the lines run full width — a reference list, a table,
  a two-column extraction — that estimate lands on a stub and the longest
  line is used instead.

It recovers prose, not layout: figures, tables and formatting are lost, which
is why `FullTextService` keeps `pdf_url` and `file_path` populated alongside
the extracted `html`.

### PDFConverter

Abstract base class for converter backends.

```python
class PDFConverter(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def convert(self, pdf_path: Path) -> ConversionResult: ...

    def validate_pdf_path(self, pdf_path: Path) -> None: ...
```

`validate_pdf_path()` is concrete and shared by subclasses. It raises:

| Condition | Exception | Message |
|-----------|-----------|---------|
| Path does not exist | `FileNotFoundError` | `PDF file not found: {path}` |
| Path is not a file | `ValueError` | `Path is not a file: {path}` |
| Suffix is not `.pdf` (compared case-insensitively) | `ValueError` | `File is not a PDF: {path}` |

### PyMuPDFConverter

```python
class PyMuPDFConverter(PDFConverter):
    def __init__(self) -> None: ...   # raises ImportError if pymupdf is missing
```

| Member | Value |
|--------|-------|
| `name` | `"pymupdf"` |
| `version` | PyMuPDF's `fitz.version[0]` |

Conversion behaviour:

- The document is opened with `with fitz.open(...)`, so it is closed even if extraction raises mid-way.
- Metadata extraction is **best-effort**: it maps to the keys `title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creation_date`, `modification_date`. On failure it appends a `Failed to extract metadata: {e}` warning and leaves `metadata` empty rather than aborting.
- A page whose text is whitespace-only (an image-only scan, say) **still counts as converted** but adds the warning `Page {n}: No extractable text`. Watch for this: `is_complete` can be `True` for a scanned PDF that yielded almost no text — check `char_count` and `warnings` too.
- A page that raises adds `Page {n}: Extraction failed - {e}` and does **not** increment `converted_pages`, so `completion_ratio` drops below 1.0.
- Page texts are joined with `"\n\n"`.
- `fitz.FileDataError` returns `success=False` with `error_message=f"Invalid or corrupted PDF: {e}"` and `converted_pages=0`. Any other exception also returns `success=False`, but keeps the running `converted_pages`.
- A PDF needing a password to open returns `success=False` with `error_message="PDF is password-protected"`. PyMuPDF opens such a file without the password and only fails on use, so without this check every page failed inside the per-page handler and the file came back as a *success* carrying no text — a caller testing `success` alone read an unreadable file as a paper with no text. The test is `doc.needs_pass`, not `doc.is_encrypted`: an owner password restricts permissions without blocking reads, and such a file converts normally.
- Exceptions from `validate_pdf_path()` **propagate** — a missing or non-PDF path raises rather than returning a failed `ConversionResult`.

### Example

```python
from pathlib import Path
from bmlib.fulltext import get_converter, list_converters

print(list_converters())            # ['pymupdf']

converter = get_converter()         # default: "pymupdf"
print(converter.name, converter.version)

result = converter.convert(Path("paper.pdf"))

if not result.success:
    print("conversion failed:", result.error_message)
else:
    print(result)                   # ConversionResult(SUCCESS, complete, 12/12 pages, ...)
    print(f"{result.completion_ratio:.0%} of pages, {result.char_count} chars")
    print(result.metadata.get("title", ""))
    for warning in result.warnings:
        print("warning:", warning)

    if result.is_complete:
        print(result.text[:500])
```

Guarding the optional dependency:

```python
from bmlib.fulltext import get_converter

try:
    converter = get_converter("pymupdf")
except ImportError:
    converter = None   # pip install bmlib[pdf] to enable
```

---

## Section Segmentation

Split a PDF's text into the standard sections of a biomedical paper —
abstract, introduction, methods, results, discussion, funding, conflicts,
data availability, and the rest of `SectionType`. Extraction needs
`bmlib[pdf]`; the segmenter itself is pure and works on any
`list[TextBlock]`.

```python
from pathlib import Path
from bmlib.fulltext import SectionSegmenter, SectionType, get_converter

converter = get_converter("pymupdf")
blocks = converter.extract_blocks(Path("paper.pdf"))       # list[TextBlock]
document = SectionSegmenter().segment_document(blocks)     # SegmentedDocument

methods = document.get_section(SectionType.METHODS)
if methods is not None:
    print(methods.title, methods.confidence)
    print(methods.content[:200])

print(document.to_markdown())
```

### How sections are found

A line is a candidate heading when its font size clears the document's
median by the configured factor (`font_size_threshold`, default 1.2) — or
fails that but is bold — and it is short (≤100 characters) and contains at
least one letter. A line's font must also reach the absolute floor
`min_heading_size` (a constructor parameter, default 10.0) before it can be
a heading at all, regardless of boldness or how it compares to the median.
Candidate headings are classified against an anchored, case-insensitive
pattern table (`"3.  Results"` matches: leading numbering and trailing
punctuation are stripped first). A heading no anchored pattern claims gets a
second, word-bounded partial pass at 0.7 confidence
(`"Supplementary materials online"` → `SUPPLEMENTARY`). A 0.7 match is a
lower-confidence candidate — a bold "Summary of findings" line or a figure
caption can produce one — so callers reasoning about section content should
check `Section.confidence` and filter to 1.0 for certainty. Duplicate
section types are possible; `get_section()` returns the first.

Sections are the text between consecutive headings. Three container rules:

| Situation | Result |
|---|---|
| Text before the first heading | A `FRONT_MATTER` section, confidence 0.5 |
| No headings detected at all | One `UNKNOWN` section titled "Full Text", confidence 0.5 |
| A heading directly followed by another | Reported with `content == ""`, not dropped |

### `TextBlock` granularity

`extract_blocks()` emits one `TextBlock` per text **line**. PyMuPDF starts
a new span at every font change, so span-level blocks would shatter a
mixed-font heading into fragments no anchored pattern can match. Font
attributes (`font_size`, `font_name`, `is_bold`, `is_italic`) are those of
the line's *dominant* span — the one contributing the most non-whitespace
characters — so a superscript reference marker cannot restyle its line.

`extract_blocks()` **raises** (`FileNotFoundError`, `ValueError`) rather
than returning a partial result: unlike `convert()`, whose partial text is
useful, a partial block list is indistinguishable from a sparse PDF. A
page with no extractable text simply contributes no blocks. A
password-protected PDF is rejected by the same explicit `needs_pass` check
`convert()` uses — it already raised, but only because `get_text()` failed
of its own accord, under a message naming two causes at once; an extraction
that stopped raising would have returned `[]`, which is exactly what an
image-only scan returns.

Blocks arrive in the PDF's *content-stream* order. For born-digital
papers that is almost always reading order — column by column — but a
PDF whose stream interleaves its columns will interleave here too, and
the section boundaries the segmenter draws from these blocks inherit
that ordering.

Only `PyMuPDFConverter` implements extraction; test for the capability
with `isinstance(converter, LayoutExtractor)`.

### `SegmentedDocument`

| Field / method | Notes |
|---|---|
| `title` | The metadata title **where page 1 corroborates it**, else the largest first-page line when it clears the median font size by 1.5× — see below |
| `authors` | **Reserved** — never populated today |
| `sections` | Flat list, document order; `Section.subsections` is likewise reserved |
| `metadata` | Whatever was passed to `segment_document()`, stored as-is |
| `get_section(t)` | First section of that type, or `None` — an empty-content section means the heading exists with no body |
| `to_markdown()` | Title, authors, then each section preceded by a `---`/bold-uppercase-title banner before its `##` heading |
| `to_dict()` / `from_dict()` | JSON-safe round trip (`SectionType` serialises as its value); on `TextBlock` and `Section` too. `metadata` rides along as-is, so it is JSON-safe only if what the caller passed in was |

`segment_document()`'s `metadata` argument is optional; only `title` and
`file_path` are read from it.

#### How the title is chosen (unreleased)

Real PDFs put junk in `/Title` — `"Microsoft Word - manuscript.docx"`,
`"untitled"`, the typesetter's job number — and the segmenter used to return
any non-empty value there verbatim, so junk beat a perfectly good large-font
first-page line (issue #56).

A metadata title is now believed only where **the document itself prints it**.
Both sides are normalised — line-break hyphenation closed up, then NFKD,
combining marks dropped, lowercased, reduced to `[a-z0-9]+` tokens joined by
single spaces — and the title is accepted when it is contained in page 1's
normalised text. That absorbs case, the terminal period metadata usually
drops, en-dash versus hyphen, ligatures, diacritics and the line break a
wrapped title carries, while rejecting a string the paper never states.

Two consequences worth knowing:

- **A page 1 with no extractable text accepts the metadata title.** An
  image-only scan makes corroboration a test that *cannot be run*, not one
  that failed, and the metadata is then the only title signal there is.
- **A rejected title falls through to the font heuristic**, which may return
  the largest first-page line or nothing at all. Losing a title is the
  intended trade: a junk title is asserted as fact by a document the caller
  trusts, while a wrongly rejected one is usually recovered off the page.

The rule and its reject-list backstop are measured against
`tests/data/pdf_metadata_titles.json`, a corpus of real Europe PMC and
bioRxiv PDFs collected by `scripts/sample_pdf_metadata_titles.py`. **Run that
sampler before changing either.**

---

## FullTextError and FullTextUnavailableError

```python
class FullTextError(Exception):
    """Error during full-text retrieval."""


class FullTextUnavailableError(FullTextError):
    """A source answered, and it has no free full text for this article."""
```

Both are defined in `bmlib.fulltext.service` and exported from `bmlib.fulltext`. `FullTextError` is used in two ways:

- **Internally**, by the fetch helpers to signal a failed attempt (`Europe PMC HTTP 503`, `Unpaywall HTTP 500`, and so on). These are caught by `fetch_fulltext()`, logged individually at `DEBUG`, and never reach the caller — though their *number* and type reach the log at `WARNING` if the whole chain then comes up empty.
- **Externally**, from `fetch_fulltext()` itself, when nothing was retrieved and neither a `doi` nor a `pmid` is available for a fallback URL. See **Raises** under `fetch_fulltext()` for the two messages.

`FullTextUnavailableError` is the subclass raised where a source *answered* and had nothing: any 404 from Europe PMC, NCBI, Unpaywall or a fetcher-supplied URL, an Unpaywall record with no OA location, an NCBI reply carrying no article at all. It is an **internal signal and never escapes** — every tier swallows it, and both of `fetch_fulltext()`'s own raises construct a plain `FullTextError`. It exists so the exhaustion summary can separate "this paper is paywalled" from "this source is down", which one type could not do (`Unpaywall HTTP 503` and `DOI not found in Unpaywall` were both plain `FullTextError`). Nothing that catches `FullTextError` is affected by the split.

Helpers called directly (e.g. `JATSParser`) may raise their own exceptions unchanged.

---

## Integration Example

The service, cache and — separately — the PDF converter, in one workflow. Note that the conversion step is an explicit call by the caller; the service does not perform it.

```python
from pathlib import Path

from bmlib.fulltext import FullTextError, FullTextService, get_converter

service = FullTextService(
    email="lab@university.edu",
    cache=None,          # a default FullTextCache() is created, or None if it cannot be
)

def get_fulltext_text(doi: str, pmc_id: str = "", pmid: str = "") -> str | None:
    """Return article text, preferring parsed HTML and falling back to PDF text."""
    try:
        result = service.fetch_fulltext(
            pmc_id=pmc_id or None,
            doi=doi or None,
            pmid=pmid,
            identifier=doi,      # required for the disk cache to engage
        )
    except FullTextError:
        return None

    # Best case: JATS XML rendered to HTML (also written to the cache).
    if result.html:
        return result.html

    # A PDF was downloaded and cached — convert it ourselves.
    if result.file_path:
        conversion = get_converter().convert(Path(result.file_path))
        if conversion.success and conversion.char_count > 0:
            return conversion.text

    # Only a URL is available (pdf_url or web_url); nothing to extract.
    return None
```

Bulk callers should add their own delay between `fetch_fulltext()` calls — bmlib does no rate limiting.
