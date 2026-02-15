# bmlib.fulltext — Full-Text Retrieval & JATS Parsing

Full-text retrieval service with JATS XML parsing for biomedical literature. Provides a 3-tier retrieval chain (Europe PMC → Unpaywall → DOI), a SAX-based JATS parser that converts PubMed Central XML to structured data or HTML, and a disk cache for downloaded content.

## Installation

```bash
pip install bmlib[publications]
```

Requires `httpx` for HTTP requests to external APIs (shared with the `publications` dependency group).

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
    # Data models
    FullTextResult,
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

Retrieves full text using a 3-tier fallback chain:

1. **Europe PMC** — fetches JATS XML, parses to HTML via `JATSParser`
2. **Unpaywall** — queries for open-access PDF URL
3. **DOI resolution** — falls back to publisher website URL

```python
class FullTextService:
    def __init__(self, email: str, timeout: float = 30.0) -> None: ...

    def fetch_fulltext(
        self,
        *,
        pmc_id: str | None = None,
        doi: str | None = None,
        pmid: str = "",
    ) -> FullTextResult: ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `email` | `str` | Contact email, required by Unpaywall API |
| `timeout` | `float` | HTTP request timeout in seconds (default 30) |

### `fetch_fulltext()`

| Parameter | Type | Description |
|-----------|------|-------------|
| `pmc_id` | `str \| None` | PubMed Central ID (e.g. `"PMC7614751"`) — triggers Tier 1 |
| `doi` | `str \| None` | Digital Object Identifier — triggers Tier 2 and 3 |
| `pmid` | `str` | PubMed ID — used as final fallback URL |

**Returns:** `FullTextResult` with the source and content.

**Raises:** `FullTextError` if no identifiers are provided at all.

### Fallback behaviour

```
PMC ID provided? ──yes──▶ Europe PMC XML ──success──▶ return HTML
                                │
                              fail
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
    source: str                    # "europepmc", "unpaywall", "doi", "cached"
    html: str | None = None        # Parsed HTML (from JATS XML)
    pdf_url: str | None = None     # Open-access PDF URL
    web_url: str | None = None     # Publisher website URL
    file_path: str | None = None   # Local cached file path
```

| Field | Populated when |
|-------|---------------|
| `html` | `source == "europepmc"` — full article HTML from parsed JATS XML |
| `pdf_url` | `source == "unpaywall"` — direct link to open-access PDF |
| `web_url` | `source == "doi"` — link to publisher page or PubMed |
| `file_path` | `source == "cached"` — path to locally cached file |

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

---

## FullTextError

```python
class FullTextError(Exception):
    """Error during full-text retrieval."""
```

Raised when `fetch_fulltext()` cannot produce any result — typically when no identifiers are provided.

---

## Integration Example

Combining the service, parser, and cache for a complete workflow:

```python
from bmlib.fulltext import FullTextService, FullTextCache, FullTextError

cache = FullTextCache(cache_dir="/data/papers/cache")
service = FullTextService(email="lab@university.edu")

def get_fulltext(pmc_id: str, doi: str, pmid: str) -> str | None:
    """Get full text HTML, using cache when available."""
    # Check cache first
    cached = cache.get_html(pmc_id or pmid)
    if cached:
        return cached

    try:
        result = service.fetch_fulltext(
            pmc_id=pmc_id or None,
            doi=doi or None,
            pmid=pmid,
        )
    except FullTextError:
        return None

    if result.source == "europepmc" and result.html:
        # Cache the parsed HTML for future use
        cache.save_html(result.html, pmc_id or pmid)
        return result.html

    return None
```
