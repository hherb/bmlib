<p align="center">
  <img src="assets/bmlib-logo.webp" alt="bmlib Biomedical Literature Research Library" width="600">
</p>

# bmlib

[![CI](https://github.com/hherb/bmlib/actions/workflows/ci.yml/badge.svg)](https://github.com/hherb/bmlib/actions/workflows/ci.yml)

Shared library for biomedical literature tools, in **Python** and **Rust**.
It covers the plumbing such a tool would otherwise rewrite:

- **Ingestion and lookup.** PubMed, bioRxiv/medRxiv and OpenAlex sync with
  dedup by DOI/PMID. One paper's record by PMID, DOI, PMC ID or preprint
  accession.
- **Retractions.** Retraction Watch lookup, and one four-state verdict
  (retracted / concern / clear / unchecked).
- **Full text.** Tiered retrieval (Europe PMC, Unpaywall, DOI), JATS XML
  parsing, PDF and HTML conversion, and one *canonical text* per paper with
  retrieval chunks.
- **Embedding.** snowflake-arctic-embed-l-v2.0 in process, with a per-paper
  vector store.
- **Assessment.** Tiered study-quality assessment up to a Cochrane nine-domain
  risk of bias. PICO extraction whose every part is backed by the paper's own
  quoted passages. Transparency and bias analysis over five public APIs.
- **LLM layer.** A unified client (Anthropic, OpenAI, Ollama, DeepSeek,
  Mistral, Gemini and OpenAI-compatible servers) with streaming, tool calling,
  reasoning traces, context windows, JSON repair and chunking.
- **Infrastructure.** Citation formatting, a database layer over SQLite and
  PostgreSQL, prompt templates, and API keys in the OS keychain.

| | Python package `bmlib` | Rust crate `bmlib` |
|---|---|---|
| **Version** | 0.13.0 | 0.4.0 (released with Python 0.13.0) |
| **Requires** | Python ≥ 3.11 | Rust ≥ 1.85 (edition 2021) |
| **Install** | `pip install bmlib` | `cargo add bmlib` |
| **Source** | [`bmlib/`](bmlib/) | [`rust/bmlib/`](rust/bmlib/) |
| **Manual** | [`docs/manual/`](docs/manual/index.md) | [`rust/bmlib/docs/manual/`](rust/bmlib/docs/manual/index.md) |
| **Changelog** | [`CHANGELOG.md`](CHANGELOG.md) | [`rust/bmlib/CHANGELOG.md`](rust/bmlib/CHANGELOG.md) |

**License:** AGPL-3.0-or-later, both languages.

The Rust crate is a functional port of the Python library. It also adds UniFFI
bindings for the Swift and Kotlin mobile apps. See
[The Rust crate](#the-rust-crate) below.

---

## Contents

- [Python: installation](#installation)
- [Modules](#modules)
- [Python quick start](#quick-start)
- [The Rust crate](#the-rust-crate)
- [Repository layout](#repository-layout)
- [Development](#development)
- [Documentation](#documentation)

## Installation

```bash
# Core (jinja2 is the only dependency)
pip install bmlib

# Everything at runtime except the embedder
pip install "bmlib[all]"

# Editable development install
uv pip install -e ".[all,dev]"
```

### Optional dependency groups

Each optional import is deferred to the function that needs it. Importing a
module never pulls in an extra. For example, the JATS parser, the full-text
models and `SectionSegmenter` all import on a core install.

| Group | Install command | Provides |
|-------|----------------|----------|
| `anthropic` | `pip install bmlib[anthropic]` | Anthropic Claude provider |
| `ollama` | `pip install bmlib[ollama]` | Ollama local provider |
| `openai` | `pip install bmlib[openai]` | OpenAI, DeepSeek, Mistral, Gemini and OpenAI-compatible providers |
| `postgresql` | `pip install bmlib[postgresql]` | PostgreSQL backend (psycopg2) |
| `transparency` | `pip install bmlib[transparency]` | Transparency analysis (httpx) |
| `publications` | `pip install bmlib[publications]` | Publication sync and record lookup (httpx) |
| `fulltext` | `pip install bmlib[fulltext]` | `FullTextService` retrieval (httpx) |
| `pdf` | `pip install bmlib[pdf]` | PDF → text (PyMuPDF) |
| `html` | `pip install bmlib[html]` | `canonical_from_html`: an HTML page's main content as canonical text (markupever, a binding to the html5ever parser, run in a child process that psutil watches) |
| `embed` | `pip install bmlib[embed]` | `bmlib.embed.Embedder` (llama-cpp-python). It compiles llama.cpp, so it is not in `all` |
| `secrets` | `pip install bmlib[secrets]` | `bmlib.secrets.KeyringVault`: API keys in the OS keychain (keyring) |
| `dev` | `pip install bmlib[dev]` | pytest, pytest-cov, ruff, mypy, types-psycopg2, types-psutil |
| `all` | `pip install bmlib[all]` | Every runtime extra except `embed` (and **not** `dev`). Use `.[all,embed]` for both |

### Upgrading to 0.13

0.13.0 breaks public APIs in `llm`, `quality`, `citations`,
`context_processor`, `fulltext` and `embed`. Each break is listed under
[*Changed — breaking*](CHANGELOG.md#changed--breaking) in the CHANGELOG.

It also changes some stored data:

- A stored PDF or Markdown canonical text is stale (`PDF_CANONICAL_VERSION` 7,
  `MARKDOWN_VERSION` 2), so `bmlib.embed.store` reads its chunk set as
  `Stale`: re-convert, then re-index.
- bmlib's full-text cache is kept (`RENDERER_VERSION` stays 7).

`ensure_schema()` is unchanged. The CHANGELOG's 0.13.0 summary says what each
of these changes costs. If you are upgrading from 0.11 or earlier, read the
0.12.0 summary as well (and 0.11.0's from 0.10 or earlier). The
[unreleased](CHANGELOG.md#unreleased) section covers what is on `main` since
then.

## Modules

Both languages have the same modules. Each row links to that module's page in
the Python manual. The Rust manual has a page for each module too.

| Module | What it does |
|--------|-------------|
| [**db**](docs/manual/database.md) | Thin database layer over SQLite and PostgreSQL: pure functions over DB-API connections, composable transactions with savepoints, and migrations |
| [**llm**](docs/manual/llm.md) | One client with pluggable providers (Anthropic, OpenAI, Ollama, DeepSeek, Mistral, Gemini, OpenAI-compatible). Covers chat, **streaming with cancellation**, tool calling, reasoning traces, **context-window lookup**, embeddings (Ollama), model listing, JSON repair, chunking and token tracking |
| [**templates**](docs/manual/templates.md) | Jinja2 prompt templates that look in a user directory first, then a default directory |
| [**agents**](docs/manual/agents.md) | `BaseAgent` for LLM-driven tasks: `chat_json` with retry and truncation handling, and per-agent metrics |
| [**context_processor**](docs/manual/context_processor.md) | Hierarchical map-reduce over content larger than one context window. No batch is ever larger than `max_context_chars` |
| [**quality**](docs/manual/quality.md) | Four-tier quality assessment (PubMed metadata → LLM classifier → deep assessment → Cochrane nine-domain risk of bias), rule-based study-type and sample-size extractors, and **`PicoAgent`**, whose PICO parts carry quotes verified against the source |
| [**transparency**](docs/manual/transparency.md) | A 0–100 transparency score (funding, COI, data availability, trial registration and results, open access) from CrossRef, Europe PMC, PubMed, OpenAlex and ClinicalTrials.gov. A failed source is recorded as failed, never scored as a negative finding |
| [**publications**](docs/manual/publications.md) | Ingestion from PubMed, bioRxiv, medRxiv and OpenAlex, with dedup, merge-on-upsert and date-range sync whose day bookkeeping never marks a short day complete. Also covers grants and affiliations, **`lookup_record`** for one paper, **Retraction Watch** import and lookup, and a **four-state retraction verdict** |
| [**fulltext**](docs/manual/fulltext.md) | Tiered retrieval (your own sources → Europe PMC XML/PDF → Unpaywall → DOI), a JATS parser that reports what it drops, PDF → text, section segmentation and a disk cache. **`fulltext.canonical`** gives one canonical text per paper from JATS, Markdown, plain text, a PDF or HTML, with every piece a code-point span, plus sentence-aware chunks and a check of whether the text is a whole paper |
| [**embed**](docs/manual/embeddings.md) | snowflake-arctic-embed-l-v2.0 run in process from pinned, checksum-verified GGUF files. `embed.store` keeps a paper's chunks and vectors in the database and ranks them by cosine similarity, and refuses a vector set that is stale |
| [**citations**](docs/manual/citations.md) | `[@id:N:Label]` citation markers, Vancouver/APA/Harvard/Chicago formatting, reference lists, and **`find_passage`**, which finds an LLM's near-verbatim quote in its source and returns the source's own wording. Pure stdlib |
| [**secrets**](docs/manual/secrets.md) | API keys by provider in one OS keychain item, read once and cached. It is the same item as the Rust crate's *(0.13.0)* |

## Quick Start

### Database

```python
from bmlib.db import connect_sqlite, execute, fetch_all, transaction

conn = connect_sqlite("~/.myapp/data.db")
with transaction(conn):
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "A paper"))
rows = fetch_all(conn, "SELECT * FROM papers")
```

`transaction()` blocks nest. An inner block becomes a savepoint, and only the
outermost block commits.

### LLM

```python
from bmlib.llm import LLMClient, LLMMessage

client = LLMClient(default_provider="ollama")
response = client.chat(
    messages=[LLMMessage(role="user", content="Summarise this paper.")],
    model="ollama:medgemma4B_it_q8",
)
print(response.content)
```

Model strings take the form `"provider:model_name"`, for example
`"anthropic:claude-sonnet-5-5"`, `"openai:gpt-4o"`, `"ollama:qwen3:8b"`,
`"deepseek:deepseek-chat"`, `"mistral:mistral-large-latest"` or
`"gemini:gemini-flash-lite-latest"`. `bmlib.llm.providers.list_providers()` lists
the providers whose SDK is installed.

### Streaming

```python
import threading

stop = threading.Event()  # set it from a Stop button

def show(delta):
    print(delta.text, end="", flush=True)  # delta.kind is "text" or "thinking"

response = client.chat_stream(messages, show, model="ollama:qwen3:32b", cancel=stop)
if response.stop_reason == "cancelled":
    print("\n[stopped]")
```

`client.context_window(model)` returns the context window the provider
states, or `None` if the provider does not say. It never guesses, and it
raises on an error rather than hiding it.

### Tool Calling

```python
from bmlib.llm import LLMToolDefinition

search = LLMToolDefinition(
    name="search_pubmed",
    description="Search PubMed for articles matching a query.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)

response = client.chat(
    messages=[LLMMessage(role="user", content="Find recent trials on statins.")],
    model="anthropic:claude-sonnet-5-5",
    tools=[search],
)
for call in response.tool_calls or []:
    print(call.name, call.arguments)  # arguments is already a parsed dict
```

To continue the conversation, append the assistant message that carries
`tool_calls`, then one `role="tool"` message per call with the matching
`tool_call_id`, and send the whole list again.

### Reasoning Traces

```python
response = client.chat(
    messages=[LLMMessage(role="user", content="Complex reasoning task...")],
    model="ollama:qwen3:8b",
    think=True,  # or "low"/"medium"/"high", or an int token budget
)
print(response.thinking)  # the reasoning trace, or None
print(response.content)   # the final answer, never mixed with the trace
```

Each provider maps `think=` onto its own parameter. A provider with no native
mapping still extracts the reasoning from the response. The per-provider
table is in [docs/manual/llm.md](docs/manual/llm.md).

### Publication Sync

```python
from datetime import date
from bmlib.db import connect_sqlite
from bmlib.publications import sync

conn = connect_sqlite("publications.db")
report = sync(
    conn,
    sources=["pubmed", "biorxiv"],
    # An ordinary week. A window containing the first of a month includes a
    # day of tens of thousands of records. See docs/manual/publications.md.
    date_from=date(2025, 3, 3),
    date_to=date(2025, 3, 9),
    email="researcher@example.com",
)
print(f"Added: {report.records_added}, Merged: {report.records_merged}")
print(report.errors, report.notes)  # failed days; days that completed but came up short
```

A day is marked complete only once the source has delivered every record it
promised. PubMed days too large for one history session are split into parts
and checkpointed. A failed day is offered again on the next run.

### Looking Up One Paper

```python
import httpx
from bmlib.publications import lookup_record

with httpx.Client(timeout=15.0) as http:
    found = lookup_record(http, doi="10.1056/NEJMoa2034577", email="you@example.org")

if found.record is not None:
    print(found.record.title, found.record.pmid, found.record.pmc_id)
```

The lookup asks PubMed first and then OpenAlex, plus Europe PMC for a
preprint. Each source reports its own outcome, so you can tell "no source
holds this paper" from "a source could not be reached".

### Retractions

```python
from bmlib.publications import import_retraction_watch_export, is_retracted, lookup_retractions

# The CC0 export Crossref distributes. Download it yourself first: it is
# about 65 MB, and Crossref can take minutes to serve it.
import_retraction_watch_export(conn, "retraction_watch.csv")

notices = lookup_retractions(conn, doi="10.1234/example")
print(is_retracted(notices))
```

`bmlib.publications.retraction_status.retraction_status()` combines the
export with Crossref's `updated-by` relations and PubMed's
*Retracted Publication* label into one verdict: `retracted`, `concern`,
`clear` or `unchecked`. A paper is called `clear` only when both the export
and Crossref have answered.

### Full-Text Retrieval

```python
from bmlib.fulltext import FullTextService

service = FullTextService(email="researcher@example.com")

# Passing identifier= turns on the disk cache (in the platform's default directory).
result = service.fetch_fulltext(
    pmc_id="PMC7614751", doi="10.1234/example", identifier="PMC7614751"
)

print(result.content_kind)  # "fulltext", "abstract" or "extracted" (from a PDF)
if result.html:
    print(result.html[:200])
```

### Canonical Text and Chunks

```python
from bmlib.fulltext import JATSParser
from bmlib.fulltext.canonical import canonical_from_jats, chunk_canonical

doc = canonical_from_jats(JATSParser(result.jats_xml).parse())
for section in doc.sections:
    heading = section.heading_span and doc.span_text(section.heading_span)
    print(section.level, heading)

for chunk in chunk_canonical(doc):
    print(doc.span_text(chunk.span))  # sentence-aware; every chunk is a span of doc.text
```

The same `CanonicalText` model comes from `canonical_from_markdown`,
`canonical_from_plain_text`, `canonical_from_segmented` (a PDF) and
`canonical_from_html`. Spans count Unicode code points, and the Rust crate
builds the same text from the same input.

### Embeddings

```python
from pathlib import Path
from bmlib.embed import ARCTIC_EMBED_L_V2_Q8_0, Embedder, download_model
from bmlib.embed.store import ensure_schema, index_paper, search

path = download_model(ARCTIC_EMBED_L_V2_Q8_0, Path("~/.cache/bmlib/models").expanduser())
embedder = Embedder.from_model(ARCTIC_EMBED_L_V2_Q8_0, path)  # needs bmlib[embed]

ensure_schema(conn)
index_paper(conn, "pmid:12345", doc, embedder, on_progress=lambda done, total: True)
for hit in search(conn, "pmid:12345", doc, "Was the trial blinded?", embedder, k=8):
    print(hit.score, doc.text[hit.span.start : hit.span.end])
```

`download_model` checks the file's size and sha256 before publishing it.
Every stored vector set is labelled with the model, the embedding recipe, the
chunker and the text it came from. The store refuses to rank a set whose label
no longer matches.

### Quality Assessment

```python
from bmlib.quality import QualityManager

manager = QualityManager(
    llm=client,
    classifier_model="anthropic:claude-haiku-5-5",
    assessor_model="anthropic:claude-sonnet-5-5",
)
assessment = manager.assess(
    title="A Randomized Controlled Trial of ...",
    abstract="We conducted a double-blind RCT ...",
    publication_types=["Randomized Controlled Trial"],
)
print(assessment.study_design, assessment.quality_tier)
```

Pass `QualityFilter(use_cochrane_assessment=True)` to add Tier 4, the Cochrane
nine-domain risk of bias with a study-characteristics table.

### PICO Extraction

```python
from bmlib.quality import PicoAgent, PicoPartState

agent = PicoAgent(llm=client, model="anthropic:claude-sonnet-5-5")
if agent.check_suitability(title, abstract).is_suitable:
    pico = agent.extract(title, full_text, min_similarity=0.9)
    for name in ("population", "intervention", "comparison", "outcome"):
        part = getattr(pico, name)
        flag = "" if part.state is PicoPartState.VERIFIED else f" ({part.state.value})"
        print(f"{name}{flag}: {part.description}", [(c.start, c.end) for c in part.citations])
```

Each part's citations are spans of the text you passed in, found by
`find_passage`. A part is never backed by the model's own copy of a quote.

### Citations

```python
from bmlib.citations import CitationStyle, DocumentMetadata, find_passage, format_document

text = "Statins reduce mortality [@id:12345:Smith2023]."
documents = {
    12345: DocumentMetadata(
        document_id=12345,
        title="Statins and mortality",
        authors=["John Smith", "Anna Johnson"],  # "Given Surname"
        journal="Lancet",
        year=2023,
    )
}
# Markers are numbered in order of first appearance, and the reference list
# is appended. Pass include_reference_list=False to leave it out.
print(format_document(text, documents, style=CitationStyle.VANCOUVER))

match = find_passage(llm_quote, source_text, min_similarity=0.95)
if match.text is not None:
    print(match.start, match.end, match.text)  # the source's own wording
```

### Transparency Analysis

```python
from bmlib.transparency import TransparencyAnalyzer

analyzer = TransparencyAnalyzer(email="researcher@example.com")
result = analyzer.analyze("doc-001", doi="10.1038/s41586-024-00001-0")
print(result.transparency_score, result.risk_level)
print(result.full_text_status, result.trial_results_status)  # what each step found
```

### API Keys in the Keychain

```python
from bmlib.secrets import KeyBundle, SecretError

keys = KeyBundle.keyring("com.example.myapp")  # the service is your app's bundle id
keys.set("openai", "sk-...")
try:
    key = keys.get("openai")  # None if no key is stored
except SecretError as e:
    print(e)                   # never includes the key
```

## The Rust crate

[`rust/bmlib/`](rust/bmlib/) is a port of the Python library, published on
crates.io as [`bmlib`](https://crates.io/crates/bmlib). It has the same modules
under the same names, in Rust spelling. Where Python returns `None`, Rust
returns `Option`. Where Python raises, Rust returns a `Result` with one error
enum per module. Nothing is `async`: every fetcher, the LLM client and the
transparency analyzer take an injected `HttpClient`.

It goes beyond reproducing Python in two ways:

- **It matches a corrected Python.** Where the Python library was found to be
  wrong while planning the port, Rust implements the intended behaviour rather
  than the defect. Python has since adopted many of them. What is deliberately
  kept is the register of investigated non-fixes,
  [`docs/DECISIONS.md`](docs/DECISIONS.md). It is part of the specification for
  both languages.
- **It is held to Python by tests, not by reading the code.** Generators in
  [`rust/oracle/`](rust/oracle/) run cases through the live Python library and
  commit the results, and the Rust tests must match them case by case. CI
  checks that the committed results still describe current Python
  (`scripts/rerun_rust_oracle.py`). The shared contracts in
  [`tests/fixtures/`](tests/fixtures/) are asserted by both test suites. These
  cover canonical text, passage matching, the vector store, the embedding
  model, record lookup, retraction status, PDF extraction, streaming and the
  keychain format.

### Install

```bash
cargo add bmlib
cargo add bmlib --features pdf,html,postgres,embed
# A build that brings its own storage and transport (as the mobile apps do).
# Neither SQLite's C build nor rustls/ring is compiled.
cargo add bmlib --no-default-features
```

| Feature | Default | Adds |
|---|---|---|
| `sqlite` | on | SQLite backend (`rusqlite`, with SQLite bundled): `db::open_memory`, `db::open_path` |
| `http-ureq` | on | `bmlib::http::UreqClient`, the real `HttpClient`. Without it, pass in your own transport |
| `pdf` | off | PDF → text with MuPDF, the engine PyMuPDF binds, compiled from source and linked statically. Needs a C compiler and libclang |
| `html` | off | HTML → canonical text over html5ever, the parser Python's `html` extra uses |
| `postgres` | off | A PostgreSQL driver. Dialect handling and the PostgreSQL DDL are always present |
| `embed` / `embed-metal` | off | `embed::Embedder` over llama.cpp (`llama-cpp-2`, pinned exactly). Needs cmake and a C++ compiler |
| `keyring` | off | `secrets::KeyringVault`, the OS keychain |

The constants and pure rules of every module need no feature. These include the
canonical-text model, JSON repair, citation formatting and the vector rules.

### Quick start

```rust
use bmlib::db::{execute, fetch_scalar, open_memory};
use bmlib::llm::json_repair::repair_json_default;
use bmlib::{params, Value};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut db = open_memory()?;
    execute(&mut db, "CREATE TABLE paper (id INTEGER PRIMARY KEY, pmid TEXT)", &[])?;
    execute(&mut db, "INSERT INTO paper (pmid) VALUES (?)", &params!["31912345"])?;
    let pmid = fetch_scalar(&mut db, "SELECT pmid FROM paper WHERE id = ?", &params![1])?;
    assert_eq!(pmid, Some(Value::Text("31912345".into())));

    // Models emit JSON that is not quite JSON.
    let fixed = repair_json_default("{'title': 'A study', 'year': 2024,}")?;
    assert_eq!(fixed, r#"{"title": "A study", "year": 2024}"#);
    Ok(())
}
```

Every `rust` block in the [Rust manual](rust/bmlib/docs/manual/index.md) runs
as a doc-test, so its examples always match the current API.

### Mobile bindings

[`rust/bmlib-ffi/`](rust/bmlib-ffi/) exposes part of the crate to Swift
(iOS/macOS) and Kotlin (Android) through UniFFI. It currently covers the JATS
reader with its audit, the retraction verdict, Crossref request and response
handling, and `RetractionIndex`, which packs the Retraction Watch export into
one file an app can store. It is a spike and is not published. Its
[README](rust/bmlib-ffi/README.md) covers the contract and the build.

### Testing the Rust side

```bash
cd rust
cargo test                    # opens no socket
cargo test --features pdf     # + MuPDF and the PDF-bytes contract
cargo clippy --all-targets && cargo fmt --check
BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1
```

The PostgreSQL and live-network suites skip unless their environment variable
is set. CI runs the default, `pdf`, `postgres` and `embed` configurations, and
`bmlib-ffi`. [`rust/README.md`](rust/README.md) is the contributor's guide. It
covers the module-by-module port status, the oracle corpora and what is not
yet done.

## Repository layout

```
bmlib/                 the Python package
tests/                 Python test suite (pytest)
tests/fixtures/        shared contracts both languages assert against
tests/data/            committed measurement corpora behind calibrated rules
scripts/               live samplers that re-measure those corpora, and fixture generators
docs/manual/           Python manual, one page per module
docs/DECISIONS.md      deliberate non-fixes, each with the test that pins it
rust/bmlib/            the Rust crate (its own README, CHANGELOG and manual)
rust/bmlib-ffi/        UniFFI bindings for the mobile apps (spike)
rust/oracle/           generators that run cases through Python for the Rust tests
spikes/                exploratory Rust prototypes, not part of either library
```

## Development

```bash
# Python
uv pip install -e ".[all,dev]"
uv run pytest tests/ -v
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .   # CI's pinned ruff
uv run mypy                                  # no arguments: the scope is in pyproject.toml
uv run python scripts/rerun_rust_oracle.py   # a behaviour change can make the Rust corpora stale

# The PostgreSQL half of tests/test_backends.py. The database must be one
# the tests may drop every table in.
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/pgrun port=5432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py
```

Some notes on the commands above:

- **Use `uv`, never bare pip.**
- **Run mypy in the dev environment.** It needs the extras installed. Run
  against a bare interpreter, it reports them, and jinja2, as missing stubs.
- **Use the pinned ruff.** CI pins ruff 0.15.20. A stale local ruff flags rules
  that newer releases removed.
- **PostgreSQL is a gate in CI.** CI runs the PostgreSQL half of the backend
  tests against a `postgres:18` service and treats a skipped half as a failure.
- **Read the session rules before changing code.**
  [`CLAUDE.md`](CLAUDE.md) and [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)
  hold the conventions and the standing rules for changes. Read
  [`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything that looks
  wrong.

## Documentation

| Where | What |
|-------|------|
| [docs/manual/](docs/manual/index.md) | Python API manual, one page per module |
| [rust/bmlib/docs/manual/](rust/bmlib/docs/manual/index.md) | Rust manual, one page per module, plus `cargo doc -p bmlib --open` for each item |
| [CHANGELOG.md](CHANGELOG.md) · [rust/bmlib/CHANGELOG.md](rust/bmlib/CHANGELOG.md) | What changed, per release and unreleased |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Deliberate non-fixes: things that look like bugs but were investigated and closed as correct |
| [docs/SESSION-RULES.md](docs/SESSION-RULES.md) | Standing rules for measuring, instrumenting and logging |
| [ROADMAP.md](ROADMAP.md) · [HANDOVER.md](HANDOVER.md) · [HANDOVER_RUST.md](HANDOVER_RUST.md) | Planned work, and what is still open in each language |
| [rust/README.md](rust/README.md) | The Rust port in detail: status, oracle, gaps |
| [docs/plans/2026-09-26-rust-port-roadblocks.md](docs/plans/2026-09-26-rust-port-roadblocks.md) | The analysis behind the port: scope, dependency policy, phase order |

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
