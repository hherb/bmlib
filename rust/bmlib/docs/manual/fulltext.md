# bmlib::fulltext — Full-Text Retrieval and Canonical Text

Tiered full-text retrieval over an injected transport, a JATS reader and
renderer, a disk cache, PDF text extraction with MuPDF, heading-driven section
segmentation, and `fulltext::canonical`: one canonical text per paper, its
chunks and a whole-paper check. The Python page is
[fulltext.md](../../../../docs/manual/fulltext.md); this one describes the Rust
API and where it differs.

| Need | Feature |
|---|---|
| `MupdfExtractor`, `MupdfPdfExtractor` (MuPDF, compiled from source) | `pdf` |
| `canonical::canonical_from_html` | `html` |
| `bmlib::http::UreqClient`, a real transport for the service | `http-ureq` (default) |
| The service, the JATS reader, the cache, the segmenter, every other converter | none |

Almost everything is re-exported from `bmlib::fulltext`; the canonical-text
API is under `bmlib::fulltext::canonical`.

## Reading a JATS document

`parse(xml)` returns a `JATSArticle`, or `JatsError::Xml` where the document
is not well-formed. `parse_with_pmc_id(xml, known)` seeds a PMC id the caller
already has (a bare number is prefixed `PMC`, and a typed `<article-id>` does
not replace it). `render_jats_html(&article)` is Python's `to_html()`: the
HTML the service returns and caches.

```rust
use bmlib::fulltext::{author_full_name, parse, render_jats_html};

let xml = r#"<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1/abc</article-id>
    <title-group><article-title>Aspirin and stroke</article-title></title-group>
    <contrib-group>
      <contrib contrib-type="author"><name><surname>Smith</surname><given-names>Jane</given-names></name></contrib>
      <contrib contrib-type="author"><collab>The ASPIRE Group</collab></contrib>
    </contrib-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    <abstract><p>We tested aspirin.</p></abstract>
  </article-meta></front>
  <body><sec><title>Methods</title><p>Patients were randomised.</p></sec></body>
</article>"#;

let article = parse(xml)?;
assert_eq!(article.title, "Aspirin and stroke");
assert_eq!(article.doi, "10.1/abc");
assert_eq!(article.year, "2024");
assert!(article.has_body);
assert_eq!(article.body_sections[0].title, "Methods");
assert_eq!(article.body_sections[0].paragraphs, ["Patients were randomised."]);

// A consortium is a contributor with an empty surname and its name in `collab`.
assert_eq!(article.authors[1].surname, "");
let names: Vec<String> = article.authors.iter().map(author_full_name).collect();
assert_eq!(names, ["Jane Smith", "The ASPIRE Group"]);

let html = render_jats_html(&article);
assert!(html.starts_with("<h1>Aspirin and stroke</h1>"));
assert!(html.contains("Patients were randomised."));
# Ok::<(), bmlib::fulltext::JatsError>(())
```

The fields are Python's, with Python's properties as free functions:
`author_full_name` is `JATSAuthorInfo.full_name` and `author_is_named` is
`is_named`. `has_body` is what the service tells an article from an abstract
by. A contributor may carry an empty `surname` (a `collab` or an undivided
`string_name`), so code sorting or indexing by it must handle that.

Elements are matched by their **local name**, so a namespaced `<jats:sec>`
is read here where Python reads such a document as empty; and a document using
`xlink:href` without declaring `xlink`, which Python's expat accepts, is
patched and read rather than refused.

### What the parse reports

`parse` and `parse_with_pmc_id` write the audit to stderr (the crate links no
logging facade): an `ERROR:` line per unwind imbalance, a `WARNING:` line per
counted loss. `parse_audited(xml, known_pmc_id)` returns a `JatsReport` with
the lines instead of only printing them:

```rust
use bmlib::fulltext::parse_audited;

let report = parse_audited("<article><body><p>Unsectioned prose.</p></body></article>", "")?;
assert!(report.diagnostics.is_empty()); // the reader unwound clean
assert_eq!(report.article.body_sections[0].paragraphs, ["Unsectioned prose."]);
// A counted loss of the deposit's: an article with no contributor list at all.
assert_eq!(report.warnings.len(), 1);
assert!(report.warnings[0].starts_with("produced no authors"));
# Ok::<(), bmlib::fulltext::JatsError>(())
```

- `diagnostics` is the **parse audit**: one message per stack, depth or flag
  `report.unwind` (a `ParseUnwindState`) left unbalanced. A well-formed
  document cannot produce one, so each is a claim that the reader is wrong.
  `unwind_diagnostics(&state)` is the pure half, for a state of your own.
- `warnings` are counted losses that are the deposit's, not the reader's:
  refused `colspan`s, contributors naming nobody, formulas that reached no
  field, and the rest, each once per article.
- `article_label` is how the article names itself in those lines.

## Retrieving full text

`FullTextService` runs the Python tier chain (known sources, Europe PMC,
NCBI, Europe PMC's free PDF, Unpaywall, then a DOI or PubMed link) over a
`HttpClient` you hand it: `bmlib::http::UreqClient` in production, or a stub.
The trait lives in `bmlib::publications::fetchers` and only `get` is needed
here. This stub serves one JATS document:

```rust
use std::sync::{Arc, Mutex};
use bmlib::fulltext::{ContentKind, FullTextCache, FullTextRequest, FullTextService};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};

const JATS: &str = "<article><front><article-meta>\
    <title-group><article-title>A trial</article-title></title-group>\
    </article-meta></front><body><sec><title>Results</title>\
    <p>Mortality fell.</p></sec></body></article>";

#[derive(Default)]
struct Stub(Mutex<Vec<String>>);

impl HttpClient for Stub {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.0.lock().unwrap().push(url.to_string());
        if url.ends_with("/PMC123/fullTextXML") {
            Ok(HttpResponse::ok_text(JATS))
        } else {
            Ok(HttpResponse::from_bytes(404, Vec::new()))
        }
    }
}

let dir = std::env::temp_dir().join(format!("bmlib-manual-fulltext-svc-{}", std::process::id()));
let client = Arc::new(Stub::default());
let service = FullTextService::new(client.clone(), "you@example.org")
    .with_cache(FullTextCache::new(Some(dir.clone())));

let request = FullTextRequest {
    pmc_id: Some("PMC123".into()),
    doi: Some("10.1/trial".into()),
    identifier: Some("10.1/trial".into()), // the cache key
    ..FullTextRequest::default()
};

let result = service.fetch_fulltext(&request)?;
assert_eq!(result.source, "europepmc");
assert_eq!(result.content_kind, ContentKind::Fulltext);
assert!(result.html.as_deref().unwrap().contains("Mortality fell."));
assert_eq!(result.jats_xml.as_deref(), Some(JATS.as_bytes())); // the bytes as served

// The second call is a cache hit and asks nothing.
let again = service.fetch_fulltext(&request)?;
assert_eq!(again.source, "cached");
assert_eq!(client.0.lock().unwrap().len(), 1);
# std::fs::remove_dir_all(&dir).ok();
# Ok::<(), bmlib::fulltext::FullTextError>(())
```

`FullTextRequest` is Python's keyword arguments as one value:
`fulltext_sources` (a fetcher's `FullTextSourceEntry` list), `pmc_id`, `doi`,
`pmid` and `identifier`. An empty string is treated as absent, as Python's
truthiness treats it. With a real transport:

```rust,ignore
use std::sync::Arc;
use bmlib::fulltext::FullTextService;
use bmlib::http::UreqClient;

let service = FullTextService::new(Arc::new(UreqClient::new()), "you@example.org")
    .with_default_cache()          // Python's default directory, or none if it cannot be made
    .with_ncbi_api_key(Some(key))
    .with_pdf_extractor(Arc::new(bmlib::fulltext::MupdfPdfExtractor::new())); // `pdf`
```

How construction differs from Python:

- **`new` touches no filesystem and has no cache.** `with_cache(Some(..))` or
  `with_default_cache()` is the explicit request; `default_cache()` degrades to
  `None` with a warning where the directory cannot be made.
- **No PDF extractor by default.** Without `with_pdf_extractor`, a PDF is
  still cached and its `pdf_url` reported, as Python without `bmlib[pdf]`;
  `with_convert_pdfs(false)` turns extraction off where one is set.
- **`with_timeout` reaches no request**: the injected transport owns its
  timeout and redirects. The value is kept for fidelity.
- **Log lines are kept on the service** as well as written to stderr:
  `log_lines()` (each a `LogLine` with a `LogLevel`), `warnings()`,
  `clear_log()`. The exhaustion report, *"N sources had nothing"* against
  *"N attempts failed"*, is Python's, and so is its meaning.

`fetch_fulltext` returns `Err(FullTextError::Other)` when no identifier was
given at all, and when the chain found nothing and has no DOI or PMID link to
degrade to. A tier failing never aborts the chain. `FullTextError::Unavailable`
is Python's `FullTextUnavailableError`: a source that answered and has
nothing.

### What came back

| `FullTextResult` field | Holds |
|---|---|
| `source` | the tier or fetcher: `"europepmc"`, `"europepmc_pdf"`, `"ncbi_pmc"`, `"unpaywall"`, `"doi"`, `"pubmed"`, `"cached"`, or a fetcher source such as `"biorxiv"` |
| `html` | the payload, or `None` |
| `content_kind` | what `html` is: `ContentKind::Fulltext` (JATS with a body), `Abstract` (a body-less JATS rendering, returned only as a last resort), `Extracted` (a PDF's prose), `None` (nothing) |
| `jats_xml` | the JATS bytes `html` was rendered from, as served, for `Fulltext` and `Abstract` from JATS |
| `pdf_url`, `web_url`, `file_path` | a PDF address, a landing page, the cached file |
| `pdf_download_failure` | why a PDF tier's download left no `file_path`: `"http-<status>"`, `"not-a-pdf"`, `"request-failed"` or `"write-failed"`; `None` when the PDF was cached or no download was tried |
| `pdf_extraction_failure` | why the cached PDF's text did not extract, a `PdfErrorKind` wire name: `"encrypted"` or `"unreadable"` (the file's), `"engine"` (not the file's, and a `PdfText` with `success` false, which names no kind), `"cancelled"`, `"timed-out"`; `None` when it extracted, was not tried, or was a scan yielding no text *(unreleased)* |

**Branch on `content_kind`, not on `html` being set**, or an abstract is
analysed as an article. `pdf_download_failure` tells a free PDF a retry may
fetch from a bare link, and `pdf_extraction_failure` a damaged file from an
engine fault; a `FullTextResult` struct literal must name both. A
`PdfExtractor` reports a failed conversion as
`PdfExtractError::Conversion(PdfError)`, whose kind becomes
`pdf_extraction_failure` *(unreleased; it carried a `String`)*.

## The cache

`FullTextCache::new(Some(dir))` is a cache under `dir`; `new(None)` is the
platform default (`default_cache_dir()`), and `None` comes back where no home
directory can be found. Construction creates nothing: each `save_*` creates
its own subdirectory (`pdfs/`, `html/`, `abstracts/`, `jats/`), and every
write is atomic.

```rust
use bmlib::fulltext::{sanitize_identifier, FullTextCache};

let dir = std::env::temp_dir().join(format!("bmlib-manual-fulltext-cache-{}", std::process::id()));
let cache = FullTextCache::new(Some(dir.clone())).expect("an explicit directory");

let key = sanitize_identifier("10.1000/xyz:123");
assert!(key.starts_with("10.1000_xyz_123_"));   // readable prefix, then a hash of the raw id

cache.save_html("<h1>Cached</h1>", &key)?;
assert_eq!(cache.get_html(&key).as_deref(), Some("<h1>Cached</h1>"));

assert_eq!(cache.save_pdf(b"not a pdf", &key)?, None); // refused: no %PDF magic
let pdf = cache.save_pdf(b"%PDF-1.4 ...", &key)?.expect("saved");
assert_eq!(cache.get_pdf(&key), Some(pdf));

cache.delete(&key);                              // every entry for the key
assert!(cache.get_html(&key).is_none());
# std::fs::remove_dir_all(&dir).ok();
# Ok::<(), std::io::Error>(())
```

- `save_pdf` returns `Ok(None)` for bytes that are not a PDF and `Err` for a
  write that failed: two different outcomes.
- `get_html`, `get_pdf` and `get_abstract` return `None` for a missing **or
  unreadable** entry, while `get_jats` returns `io::Result` and is `Err` for a
  read that failed other than a missing file;
  `quarantine(key)` renames the unreadable ones aside (`.corrupt`) so the next
  lookup is a clean miss, and `clear()` removes everything.
- `sanitize_identifier` truncates the readable part to `MAX_PREFIX_CHARS` and
  keeps the collision guarantee in the hash of the whole raw identifier. The
  service applies it to `FullTextRequest::identifier` itself; a blank
  identifier is no key.

**Unlike Python, a rendered entry carries no renderer stamp**: Rust neither
writes Python's `<!-- bmlib-fulltext-renderer: N -->` line nor re-fetches an
entry an older renderer wrote, so do not share one cache directory between the
two languages and expect Python's staleness rule to hold.

## PDF to text

The backend is a trait, `PdfTextExtractor`: `extract_blocks(path)` gives one
`TextBlock` per visual line (text, page, font size and name, bold, italic,
box) for the segmenter, and `page_texts(path)` the text of each page.
`MupdfExtractor` (feature `pdf`) implements it over MuPDF, the engine Python's
PyMuPDF binds, compiled from source and linked statically, so the two
languages read the same lines from the same PDF.

```rust,ignore
use std::path::Path;
use std::sync::{atomic::AtomicBool, Arc};
use std::time::{Duration, Instant};
use bmlib::fulltext::{MupdfExtractor, PdfErrorKind, PdfLimit, PdfTextExtractor};

let cancel = Arc::new(AtomicBool::new(false)); // set it from another thread to stop
let limit = PdfLimit {
    cancel: Some(cancel.clone()),
    deadline: Some(Instant::now() + Duration::from_secs(20)),
};
match MupdfExtractor::new().extract_blocks_within(Path::new("paper.pdf"), &limit) {
    Ok(blocks) => println!("{} lines", blocks.len()),
    Err(e) if e.kind.is_the_files() => eprintln!("pick another file: {e}"), // Encrypted, Unreadable
    Err(e) if matches!(e.kind, PdfErrorKind::Cancelled | PdfErrorKind::TimedOut) => {}
    Err(e) => eprintln!("engine fault, a retry may work: {e}"),            // Engine
}
```

- **Errors are typed.** `PdfError { kind, message }`: `PdfErrorKind::Encrypted`
  (the message says to remove the password), `Unreadable` (damaged,
  truncated, unsupported), `Engine` (anything else, including causes the
  backend cannot place), and `Cancelled` / `TimedOut` for a reached limit.
  `is_the_files()` is the split to act on; `as_str()` is Python's
  `PDFErrorKind` value. The enum is `#[non_exhaustive]`. The message never
  names the path.
- **`PdfLimit` stops a page while it runs**, where Python checks between
  pages: while a limit is set, a watcher checks it every
  `mupdf_backend::LIMIT_WATCH_INTERVAL` (10 ms) and aborts MuPDF. A stopped extraction is an
  error, never a short result. `extract_blocks` and `page_texts` are
  `extract_blocks_within` and `page_texts_within` with `PdfLimit::default()`,
  which stops never; an implementor of the trait writes the two `_within`
  methods and `name()`.
- **What MuPDF reads past is reported** *(#599)*:
  `extract_blocks_with_warnings` returns a `PdfBlocks` (`blocks`, `warnings`),
  MuPDF's messages while the PDF was read — a repaired xref, a page stopped
  after too many syntax errors, a font with bogus metrics — none of which
  fails the call, the path replaced by "the file".
  `MupdfExtractor::page_texts_with_warnings` gives the same beside page texts.
  The trait's default reports nothing. On a failure the messages are dropped.
  `is_about_the_file` sets aside MuPDF's `found duplicate … in the store`.
- **The service takes a different trait.** `FullTextService` wants HTML for
  the whole document from `PdfExtractor::extract`, and `MupdfPdfExtractor` is
  that adapter over `MupdfExtractor::page_texts`, with no limit. It returns
  `PdfText` (`html`, page and character counts, `is_complete()`, and MuPDF's
  messages in `warnings`, which the service logs as one WARNING line).
  `pdf_text::render_html(success, text, page_texts)` is the reflow it uses:
  page furniture stripped, wrapped lines rejoined into `<p>` paragraphs.

There is no `convert()` here: `ConversionResult` exists, with `error_kind`, but
nothing in the crate builds one from a PDF, and the backend does not read the
PDF's `/Title`. Pass a title you have to the segmenter instead.

## Section segmentation

`SectionSegmenter::default().segment_document(blocks, file_path,
metadata_title, metadata)` turns lines into a `SegmentedDocument` of typed
`Section`s. Page furniture (running heads, page numbers) is set aside first;
a heading is a short line larger than the body median by
`font_size_threshold`, or a bold one; it is classified by
`match_section_type`; text before the first heading is `FrontMatter`, and a
document with no heading is one `Unknown` section titled `"Full Text"`. The
title is the metadata title where page 1 prints it (`accepted_metadata_title`),
else a first-page line half again larger than the body.

`canonical_from_segmented` then makes it a canonical text. The blocks would
come from `MupdfExtractor`; here they are built by hand:

```rust
use bmlib::fulltext::canonical::canonical_from_segmented;
use bmlib::fulltext::{SectionSegmenter, SectionType, TextBlock};

let line = |text: &str, y: f64, size: f64| TextBlock {
    text: text.into(), page_num: 0, font_size: size, font_name: "Times".into(),
    is_bold: false, is_italic: false, x: 72.0, y, width: 400.0, height: size,
};
let blocks = [
    line("Methods", 100.0, 16.0),
    line("We enrolled forty patients.", 130.0, 10.0),
    line("Results", 160.0, 16.0),
    line("Mortality fell.", 190.0, 10.0),
];
let doc = SectionSegmenter::default().segment_document(
    &blocks, "paper.pdf", Some("A trial"), serde_json::Value::Null,
);
let types: Vec<SectionType> = doc.sections.iter().map(|s| s.section_type).collect();
assert_eq!(types, [SectionType::Methods, SectionType::Results]);
assert_eq!(doc.title, None); // "A trial" is not printed on page 1, so it is not believed

let canonical = canonical_from_segmented(&doc);
assert_eq!(canonical.converter_version, "pdf-3");
```

## Canonical text

A `CanonicalText` is one string, `text`, and every piece of it as a
`TextSpan` into it: `title_span`, and for each of the flat, ordered
`sections` a `level` (1 is top), an optional `heading_span` and its `blocks`
(each a `BlockKind::Paragraph` or `TableRow`, cells joined by a tab). Pieces
are joined by `BLOCK_SEPARATOR` (`"\n\n"`) and nothing else.
`source_format` and `converter_version` (`"jats-1"`, `"markdown-1"`,
`"plain_text-1"`) say what made it.

**Spans count Unicode code points** (`char`s), half-open, never bytes, as
Python's string indices do. Converting to UTF-16 or bytes is the caller's job,
at its own boundary. `span_text(span)` returns `Option<&str>` where Python's
raises.

```rust
use bmlib::fulltext::canonical::{
    canonical_from_jats, canonical_from_markdown, canonical_from_plain_text, BlockKind,
};
use bmlib::fulltext::parse;

let md = canonical_from_markdown("# Aspirin and stroke\n\n## Methods\n\nWe *randomised* 40 patients.\n");
assert_eq!(md.span_text(md.title_span.unwrap()), Some("Aspirin and stroke"));
let methods = &md.sections[0];
assert_eq!(md.span_text(methods.heading_span.unwrap()), Some("Methods"));
assert_eq!(methods.blocks[0].kind, BlockKind::Paragraph);
assert_eq!(md.span_text(methods.blocks[0].span), Some("We randomised 40 patients.")); // markup stripped

let plain = canonical_from_plain_text("First line\nwraps.\n\nSecond paragraph.", "A title");
assert_eq!(plain.text, "A title\n\nFirst line wraps.\n\nSecond paragraph.");

let article = parse("<article><front><article-meta><title-group>\
    <article-title>A trial</article-title></title-group>\
    <abstract><p>In brief.</p></abstract></article-meta></front>\
    <body><sec><title>Results</title><p>Mortality fell.</p></sec></body></article>")?;
let jats = canonical_from_jats(&article);
let headings: Vec<&str> = jats.sections.iter()
    .filter_map(|s| s.heading_span.and_then(|h| jats.span_text(h)))
    .collect();
assert_eq!(headings, ["Abstract", "Results"]);
assert!(jats.validate().is_ok());
# Ok::<(), bmlib::fulltext::JatsError>(())
```

The converters, each with its own version constant:

| Function | From | Notes |
|---|---|---|
| `canonical_from_jats(&article)` | a parsed `JATSArticle` | title; `Abstract`; the body; `Funding`; `Figures`; `Tables`; `References` |
| `canonical_from_markdown(text)` | a hand-written Markdown subset | headings make sections, inline markup is stripped |
| `canonical_from_plain_text(text, title)` | plain text | blank lines separate paragraphs; one untitled section |
| `canonical_from_segmented(&doc)` | a `SegmentedDocument` | a soft hyphen at a line end is joined; an ordinary hyphen is kept |
| `canonical_from_html(html)` | an HTML page's main content | feature `html`; html5ever through `scraper` |

Your own converter builds `SectionDraft`s of `BlockDraft::paragraph` /
`BlockDraft::row` and calls `assemble(title, &sections, format, version)`.
It **panics** on a section level below `TOP_LEVEL`, where Python's raises
`ValueError`. A text read back from storage is checked with `validate()`, or
built with `CanonicalText::from_value(json)`, which validates.

```rust,ignore
use bmlib::fulltext::canonical::canonical_from_html; // feature `html`

let page = canonical_from_html(&std::fs::read_to_string("article.html")?);
```

### Chunks for retrieval

`chunk_canonical(&text, max_chars, overlap_chars)` splits a text section by
section into sentence-aware `Chunk`s, each a `section_index` and a `span`
that starts and ends on non-whitespace and is at most `max_chars` code points.
Python's defaults are explicit here: `DEFAULT_MAX_CHARS` (1000) and
`DEFAULT_OVERLAP_CHARS` (100). Titles and headings are not chunked; reach a
chunk's heading through its section. A chunk carries no `CHUNKER_VERSION`:
store it beside the text's `converter_version`.

```rust
use bmlib::fulltext::canonical::{
    canonical_from_markdown, chunk_canonical, ChunkError, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS,
};

let text = canonical_from_markdown("## Methods\n\nWe enrolled 40 patients (see Fig. 2). Dose was 5 mg.\n\n## Results\n\nMortality fell.\n");
let chunks = chunk_canonical(&text, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS)?;
let shown: Vec<(usize, &str)> = chunks.iter()
    .map(|c| (c.section_index, text.span_text(c.span).unwrap()))
    .collect();
assert_eq!(shown, [
    (0, "We enrolled 40 patients (see Fig. 2). Dose was 5 mg."),
    (1, "Mortality fell."),
]);

let small = chunk_canonical(&text, 30, 0)?;   // sentences packed up to 30 code points
assert!(small.iter().all(|c| c.span.end - c.span.start <= 30));

assert!(matches!(chunk_canonical(&text, 10, 10), Err(ChunkError::Sizes { .. })));
# Ok::<(), ChunkError>(())
```

`bmlib::embed::store` indexes a paper's chunks with these defaults; see
[embed.md](embed.md).

### Is it the whole paper?

A landing page converts as cleanly as the article, so `assess_full_text(&text)`
reads the result, from any format: a text is full text when a *Methods*
heading and a *Results* or *Discussion* one lie outside the abstract and the
references, and the body holds at least `MIN_BODY_WORDS` (1000) words. It is a
heuristic and **unmeasured**; a case report or a letter fails it.

```rust
use bmlib::fulltext::canonical::{assess_full_text, canonical_from_markdown};
use bmlib::fulltext::SectionType;

let text = canonical_from_markdown("## Abstract\n\n### Methods\n\nA trial.\n\n## Methods\n\nWe enrolled.\n\n## Results\n\nIt worked.\n");
let verdict = assess_full_text(&text)?;
assert_eq!(verdict.section_types, [SectionType::Abstract, SectionType::Methods, SectionType::Results]);
assert_eq!(verdict.body_words, 4);         // the abstract's own words are not the body
assert!(!verdict.is_full_text);            // the headings are there; the words are not
# Ok::<(), bmlib::fulltext::canonical::CanonicalError>(())
```

`is_full_text` is a field here (a property in Python); `FullTextAssessment` is
`#[non_exhaustive]` and built only by `assess_full_text`, so it always agrees
with the other two. The function returns `CanonicalError` for a text that
fails validation.
