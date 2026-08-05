# bmlib.fulltext — Full-Text Retrieval, JATS Parsing & PDF Conversion

Full-text retrieval for biomedical literature. Provides a multi-tier retrieval chain (caller-supplied sources → Europe PMC → NCBI PMC → Unpaywall → DOI/PubMed), a SAX-based JATS parser that converts PubMed Central XML to structured data or HTML, a disk cache for downloaded content, and a pluggable PDF-to-text converter that the retrieval chain uses to make a PDF-only article readable inline.

## Installation

```bash
pip install bmlib[publications]     # FullTextService, JATSParser, FullTextCache (httpx)
pip install bmlib[pdf]              # PDF → text conversion (pymupdf)
```

`httpx` is required for HTTP requests to external APIs (shared with the `publications` dependency group). `pymupdf` is required **only** for `PyMuPDFConverter`; everything else in the module works without it.

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
| `cache` | `FullTextCache \| None` | Cache instance. When `None`, a default `FullTextCache()` is constructed, so a cache always exists |
| `convert_pdfs` | `bool` | Extract a retrieved PDF's text into `html` (default `True`). Requires `bmlib[pdf]`, and only applies once the PDF is cached — so `fetch_fulltext()` must be given an `identifier`. `pdf_url` and `file_path` stay set either way |
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

**Raises:** `FullTextError` only when no identifiers are provided at all. Every individual tier failure is swallowed and logged at `DEBUG` (with `exc_info=True`), then the next tier is tried.

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
| Tier 1d | A free PDF render URL was found | Take the `fullTextUrlList` entry with `documentStyle == "pdf"` and `availability == "Free"`; download and cache it | `"europepmc_pdf"` |
| Tier 2 | `doi` given | Unpaywall `GET .../{doi}?email=...`; picks `best_oa_location.url_for_pdf` or `.url`, else iterates `oa_locations`; downloads and caches | `"unpaywall"` |
| Tier 3 | `doi` given | Return `https://doi.org/{doi}` | `"doi"` |
| Final | `pmid` given | Return `https://pubmed.ncbi.nlm.nih.gov/{pmid}/` | `"pubmed"` |
| — | nothing given | `raise FullTextError("No identifiers provided")` | — |

Within Tier 0, an `xml` entry is fetched and JATS-parsed into HTML, a `pdf` entry sets `pdf_url` and is downloaded into the cache, and an `html` entry sets `web_url` only — HTML sources are never cached.

### Operational notes

- **No rate limiting.** The package sleeps for nothing and throttles nothing. Callers hitting Europe PMC, NCBI or Unpaywall in bulk must implement their own pacing. This matters most for NCBI: Tiers 1b′ and 1c add up to two NCBI requests per lookup that misses at Europe PMC, and NCBI enforces its limit — 3 requests/second per IP, or 10 with an `ncbi_api_key` — by blocking, where the other sources are more forgiving. Setting the key raises the ceiling; it does not add pacing.
- **No environment variables.** The two credential-like inputs are both constructor arguments: the Unpaywall contact email (also sent to NCBI as the `email` E-utilities parameter) and the optional `ncbi_api_key`. Neither is read from the environment.
- **One client per request.** Every HTTP call goes through an internal helper that opens a fresh `httpx.Client` with `follow_redirects=True`. There is no connection pooling across calls.
- **PDF download failure is non-fatal.** When a PDF cannot be downloaded or fails magic-byte validation, the result is still returned with `pdf_url` set, so the URL remains a usable fallback; only `file_path` is left unset. If a body-less JATS document was seen earlier in the chain, its abstract is merged into that result rather than discarded, so the caller gets an abstract plus a link instead of a bare link.
- **PDF text extraction is best-effort and logged.** A missing `bmlib[pdf]` extra, a corrupt PDF, or a scan with no extractable text all leave `html` unset and emit a `WARNING`; a partial extraction is attached but flagged. Nothing here aborts a retrieval.
- **Extracted PDF text is not cached; it is re-derived.** Only body-carrying JATS HTML is written to the HTML cache, so a cached HTML hit always means full text. A cached *PDF* hit re-runs extraction on the local file, so a second `fetch_fulltext()` returns the same `html` and `content_kind` as the first.
- **Caching is opt-in per call.** The service holds a `FullTextCache` unconditionally, but reads and writes only occur when `identifier` is passed.

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
    def delete(self, identifier: str) -> None: ...
    def clear(self) -> None: ...
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `save_pdf(data, id)` | `str \| None` | Save PDF bytes; returns the path, or `None` (with a warning log) if the data is not a valid PDF |
| `get_pdf(id)` | `str \| None` | Returns the cached file path, or `None` |
| `save_html(html, id)` | `str` | Save an HTML string as UTF-8; always returns the file path |
| `get_html(id)` | `str \| None` | Returns the cached HTML content, or `None` |
| `delete(id)` | `None` | Remove both cached files for the identifier (missing files are ignored) |
| `clear()` | `None` | Remove every file directly inside `pdfs/` and `html/`; subdirectories are skipped |

PDF validation uses magic-byte checking against `PDF_MAGIC_BYTES = b"%PDF"`. Non-PDF data is **rejected with a warning log and a `None` return** — no exception is raised.

The cache has **no TTL, no size limit, and no eviction policy.** Entries live until `delete()` or `clear()` is called, or the directory is removed. Long-running processes should prune it themselves.

### Cache keys

Cache filenames are derived by `sanitize_identifier()`:

```python
from bmlib.fulltext.cache import sanitize_identifier

sanitize_identifier("10.1/a:b")   # "10.1_a_b_3f15c10f6c"
sanitize_identifier("10.1/a/b")   # "10.1_a_b_0f7d1c325e"
```

```python
safe   = re.sub(r"[^\w.\-]", "_", raw)
digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
return f"{safe}_{digest}"
```

The readable prefix is kept for debuggability; the ten-character digest of the **raw** identifier makes the key collision-free.

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

Despite the module docstring's reference to the XDG convention, **`XDG_CACHE_HOME` is not read**; the Linux path is hardcoded to `Path.home() / ".cache"`. Set `cache_dir` explicitly if you need to honour XDG.

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
page with no extractable text simply contributes no blocks.

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
| `title` | Metadata title if present, else the largest first-page line when it clears the median font size by 1.5× |
| `authors` | **Reserved** — never populated today |
| `sections` | Flat list, document order; `Section.subsections` is likewise reserved |
| `metadata` | Whatever was passed to `segment_document()`, stored as-is |
| `get_section(t)` | First section of that type, or `None` — an empty-content section means the heading exists with no body |
| `to_markdown()` | Title, authors, then each section preceded by a `---`/bold-uppercase-title banner before its `##` heading |
| `to_dict()` / `from_dict()` | JSON-safe round trip (`SectionType` serialises as its value); on `TextBlock` and `Section` too. `metadata` rides along as-is, so it is JSON-safe only if what the caller passed in was |

`segment_document()`'s `metadata` argument is optional; only `title` and
`file_path` are read from it.

---

## FullTextError

```python
class FullTextError(Exception):
    """Error during full-text retrieval."""
```

Defined in `bmlib.fulltext.service` and used in two ways:

- **Internally**, by the fetch helpers to signal a failed tier (`Europe PMC HTTP 503`, `No open-access PDF found for DOI ...`, and so on). These are caught by `fetch_fulltext()`, logged at `DEBUG`, and never reach the caller.
- **Externally**, from `fetch_fulltext()` itself — the only escaping case is `FullTextError("No identifiers provided")`, raised when `fulltext_sources`, `pmc_id`, `doi` and `pmid` are all empty.

Helpers called directly (e.g. `JATSParser`) may raise their own exceptions unchanged.

---

## Integration Example

The service, cache and — separately — the PDF converter, in one workflow. Note that the conversion step is an explicit call by the caller; the service does not perform it.

```python
from pathlib import Path

from bmlib.fulltext import FullTextError, FullTextService, get_converter

service = FullTextService(
    email="lab@university.edu",
    cache=None,          # a default FullTextCache() is created
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
