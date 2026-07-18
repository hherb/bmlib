# bmlib.fulltext — Full-Text Retrieval & JATS Parsing

Full-text retrieval service with JATS XML parsing for biomedical literature. Provides a multi-tier retrieval chain (cache → fetcher-provided sources → Europe PMC → Unpaywall → DOI), a SAX-based JATS parser that converts PubMed Central XML to structured data or HTML, a disk cache for downloaded content, and pluggable PDF-to-text conversion.

## Installation

```bash
pip install bmlib[publications]   # HTTP retrieval (httpx)
pip install bmlib[pdf]            # PDF-to-text conversion (PyMuPDF)
```

Retrieval requires `httpx` for HTTP requests to external APIs (shared with the `publications` dependency group). PDF conversion requires `pymupdf`, provided by the optional `pdf` extra.

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
)
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
    )
except FullTextError as e:
    print(f"No full text available: {e}")
else:
    if result.source == "europepmc":
        print(result.html[:200])   # Parsed HTML from JATS XML
    elif result.source == "unpaywall":
        print(result.pdf_url)      # Open-access PDF URL
    elif result.source == "doi":
        print(result.web_url)      # Publisher website fallback
```

### Parse JATS XML directly

```python
from pathlib import Path
from bmlib.fulltext import JATSParser

xml_bytes = Path("article.xml").read_bytes()

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
path = cache.save_pdf(pdf_bytes, identifier="34567890")

# Retrieve cached PDF path
pdf_path = cache.get_pdf("34567890")
```

---

## FullTextService

Retrieves full text using a multi-tier fallback chain:

0. **Cache / known sources** — disk cache hit (when `identifier` is given), then fetcher-provided `FullTextSourceEntry` URLs in priority order JATS XML > PDF > HTML
1. **Europe PMC** — fetches JATS XML (known or discovered PMC ID), parses to HTML via `JATSParser`; falls back to a free PDF render URL when XML is unavailable
2. **Unpaywall** — queries for open-access PDF URL
3. **DOI resolution** — falls back to publisher website URL (or PubMed URL)

```python
class FullTextService:
    def __init__(
        self,
        email: str,
        timeout: float = 30.0,
        cache: FullTextCache | None = None,
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

| Parameter | Type | Description |
|-----------|------|-------------|
| `email` | `str` | Contact email, required by Unpaywall API |
| `timeout` | `float` | HTTP request timeout in seconds (default 30) |
| `cache` | `FullTextCache \| None` | Disk cache used when `identifier` is passed to `fetch_fulltext()`; defaults to a new `FullTextCache()` |

### `fetch_fulltext()`

| Parameter | Type | Description |
|-----------|------|-------------|
| `fulltext_sources` | `list[FullTextSourceEntry] \| None` | Known source URLs from a publication fetcher — tried first (Tier 0) |
| `pmc_id` | `str \| None` | PubMed Central ID (e.g. `"PMC7614751"`) — triggers Tier 1 |
| `doi` | `str \| None` | Digital Object Identifier — triggers PMC ID discovery, Tier 2 and 3 |
| `pmid` | `str` | PubMed ID — used for PMC ID discovery and as final fallback URL |
| `identifier` | `str \| None` | Cache key (typically the DOI); when provided, enables disk caching of retrieved HTML and downloaded PDFs |

**Returns:** `FullTextResult` with the source and content.

**Raises:** `FullTextError` if no identifiers are provided at all.

When a tier yields a PDF URL and `identifier` is given, the service also downloads the PDF into the cache and sets `FullTextResult.file_path` (leaving `pdf_url` usable as a fallback if the download fails).

### Fallback behaviour

```
identifier given? ──yes──▶ disk cache ──hit──▶ return cached HTML or PDF path
                                │
                              miss
                                ▼
fulltext_sources? ──yes─▶ known URLs (XML ▶ PDF ▶ HTML) ──success──▶ return
                                │
                              fail
                                ▼
PMC ID known, or discovered via Europe PMC search (DOI/PMID)?
                ──yes──▶ Europe PMC XML ──success──▶ return HTML
                                │
                              fail
                                ▼
Free PDF in Europe PMC search result? ──yes──▶ return PDF render URL
                                │
                               no
                                ▼
DOI provided? ───yes──▶ Unpaywall API ──success──▶ return PDF URL
                                │
                              fail
                                ▼
DOI provided? ───yes──▶ return DOI URL (publisher website)
                                │
                               no
                                ▼
PMID provided? ──yes──▶ return PubMed URL
                                │
                               no
                                ▼
                        raise FullTextError
```

---

## FullTextResult

Result of a full-text retrieval attempt.

```python
@dataclass
class FullTextResult:
    source: str                    # "europepmc", "europepmc_pdf", "unpaywall",
                                   # "doi", "pubmed", "cached", or a fetcher
                                   # source name (e.g. "biorxiv")
    html: str | None = None        # Parsed HTML (from JATS XML)
    pdf_url: str | None = None     # Open-access PDF URL
    web_url: str | None = None     # Publisher website URL
    file_path: str | None = None   # Local cached file path
```

| Field | Populated when |
|-------|---------------|
| `html` | `source == "europepmc"`, a cached HTML hit, or a known XML source — full article HTML from parsed JATS XML |
| `pdf_url` | `source == "europepmc_pdf"`, `"unpaywall"`, or a known PDF source — direct link to open-access PDF |
| `web_url` | `source == "doi"`, `"pubmed"`, or a known HTML source — link to publisher page or PubMed |
| `file_path` | `source == "cached"` (PDF cache hit), or set alongside `pdf_url` after a successful download into the cache |

---

## FullTextSourceEntry

A known full-text source URL discovered by a publication fetcher. Produced by `bmlib.publications` fetchers, consumed by `FullTextService.fetch_fulltext(fulltext_sources=...)` as Tier 0.

```python
@dataclass
class FullTextSourceEntry:
    url: str
    format: str                 # "pdf", "xml", "html"
    source: str                 # e.g. "biorxiv", "medrxiv", "pmc", "publisher"
    open_access: bool = True
    version: str | None = None  # e.g. "preprint", "accepted", "published"

    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullTextSourceEntry: ...
```

---

## JATSParser

SAX-based parser for JATS (Journal Article Tag Suite) XML, the standard format used by PubMed Central and Europe PMC. Ported from the Swift BioMedLit library.

```python
class JATSParser:
    def __init__(self, data: bytes, known_pmc_id: str = "") -> None: ...

    def parse(self) -> JATSArticle: ...
    def to_html(self) -> str: ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `bytes` | Raw JATS XML content |
| `known_pmc_id` | `str` | Optional PMC ID for constructing figure URLs |

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

### Supported JATS elements

| JATS element | Parsed as |
|-------------|-----------|
| `front/article-meta` | Title, authors, journal, identifiers |
| `abstract/sec/title/p` | Structured abstract sections |
| `body/sec/title/p` | Body sections with nesting |
| `fig/graphic/label/caption` | Figures with Europe PMC image URLs |
| `table-wrap/thead/tbody/tr/th/td` | Tables (rendered as HTML `<table>`) |
| `ref-list/ref/element-citation` | Structured references |
| `bold/italic/sub/sup/monospace` | Inline formatting |
| `xref` | Cross-reference anchor links |

---

## JATSArticle

Complete parsed article data.

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
```

### JATSAuthorInfo

```python
@dataclass
class JATSAuthorInfo:
    surname: str
    given_names: str = ""
    affiliations: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str: ...  # "John A Smith" or "Consortium"
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
    label: str            # e.g. "Table 1"
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
    authors: list[str] = field(...)
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
    def formatted_citation(self) -> str: ... # Structured or raw fallback
```

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

### Default cache directory

When `cache_dir` is not specified, the cache uses a platform-appropriate default:

| Platform | Default path |
|----------|-------------|
| macOS | `~/Library/Caches/bmlib/fulltext_cache/` |
| Linux | `~/.cache/bmlib/fulltext_cache/` |
| Windows | `%LOCALAPPDATA%/bmlib/fulltext_cache/` |

### Directory layout

```
fulltext_cache/
├── pdfs/
│   ├── 34567890.pdf
│   └── 45678901.pdf
└── html/
    ├── PMC7614751.html
    └── PMC8123456.html
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `save_pdf(data, id)` | `str \| None` | Save PDF bytes; returns path or `None` if not valid PDF |
| `get_pdf(id)` | `str \| None` | Returns cached file path, or `None` |
| `save_html(html, id)` | `str` | Save HTML string; returns file path |
| `get_html(id)` | `str \| None` | Returns cached HTML content, or `None` |
| `delete(id)` | `None` | Remove all cached files for identifier |
| `clear()` | `None` | Remove all cached files |

PDF validation uses magic-byte checking (`%PDF` header). Non-PDF data is rejected with a warning log.

Identifiers that are not filename-safe (e.g. raw DOIs containing `/`) are sanitized into `<safe>_<hash>` filenames, where `<hash>` is a short SHA-1 prefix of the raw identifier, so distinct identifiers can never collide. Already-safe identifiers (PMIDs, PMC IDs) are used verbatim.

---

## PDF Conversion

Pluggable PDF-to-text conversion behind a small registry, prioritising completeness of extracted text over formatting. The only built-in backend is `PyMuPDFConverter`, which requires the optional `pymupdf` dependency (`pip install bmlib[pdf]`); it is loaded lazily, so importing `bmlib.fulltext` never requires PyMuPDF.

### Registry

```python
def get_converter(name: str = "pymupdf") -> PDFConverter: ...
def list_converters() -> list[str]: ...
```

| Function | Returns | Description |
|----------|---------|-------------|
| `get_converter(name)` | `PDFConverter` | Initialised converter by name (default `"pymupdf"`) |
| `list_converters()` | `list[str]` | Names of all registered converters |

**Raises:** `get_converter()` raises `ValueError` for an unknown name, and `ImportError` if the converter's optional dependency is missing (`Install with: pip install bmlib[pdf]`).

### PDFConverter

Abstract base class for PDF converters.

```python
class PDFConverter(ABC):
    @property
    def name(self) -> str: ...      # abstract — converter name identifier
    @property
    def version(self) -> str: ...   # abstract — converter version string

    def convert(self, pdf_path: Path) -> ConversionResult: ...  # abstract
    def validate_pdf_path(self, pdf_path: Path) -> None: ...
```

| Method | Description |
|--------|-------------|
| `convert(pdf_path)` | Convert a PDF to text; raises `FileNotFoundError` if the file does not exist, `ValueError` if the path is not a PDF file |
| `validate_pdf_path(pdf_path)` | Check the path exists, is a file, and has a `.pdf` suffix (same exceptions as above) |

### ConversionResult

Result of a PDF-to-text conversion — a stable interface across backends.

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

    @property
    def is_complete(self) -> bool: ...        # all pages converted and some text extracted
    @property
    def completion_ratio(self) -> float: ...  # converted_pages / page_count (0.0 when no pages)
```

### PyMuPDFConverter

Built-in backend backed by PyMuPDF (`fitz`); registered as `"pymupdf"`. The constructor raises `ImportError` if `pymupdf` is not installed.

- Extracts plaintext from every page; pages with no extractable text (e.g. image-only) are still counted as converted, with a warning.
- A single failing page does not abort the rest — a warning is recorded instead.
- PDF metadata (title, author, subject, keywords, creator, producer, creation/modification dates) is collected best-effort into `metadata`.
- Invalid or corrupted PDFs return `success=False` with `error_message` set rather than raising.

### Example

```python
from pathlib import Path
from bmlib.fulltext import get_converter, list_converters

print(list_converters())        # ["pymupdf"]

converter = get_converter()     # default: "pymupdf"
result = converter.convert(Path("paper.pdf"))

if result.success and result.is_complete:
    print(result.text[:200])
else:
    print(result.error_message or result.warnings)
```

---

## FullTextError

```python
class FullTextError(Exception):
    """Error during full-text retrieval."""
```

Raised when `fetch_fulltext()` cannot produce any result — typically when no identifiers are provided.

---

## Integration Example

Combining the service, cache, and PDF conversion for a complete workflow:

```python
from pathlib import Path
from bmlib.fulltext import FullTextService, FullTextCache, FullTextError, get_converter

cache = FullTextCache(cache_dir="/data/papers/cache")
service = FullTextService(email="lab@university.edu", cache=cache)

def get_fulltext(pmc_id: str, doi: str, pmid: str) -> str | None:
    """Get full text as HTML or plaintext, caching on disk."""
    try:
        result = service.fetch_fulltext(
            pmc_id=pmc_id or None,
            doi=doi or None,
            pmid=pmid,
            identifier=doi or pmc_id or pmid,  # enables disk caching
        )
    except FullTextError:
        return None

    if result.html:
        # Parsed JATS HTML (fresh or cached)
        return result.html

    if result.file_path:
        # Downloaded (or cached) PDF — convert to plaintext
        conversion = get_converter().convert(Path(result.file_path))
        if conversion.success:
            return conversion.text

    return None
```
