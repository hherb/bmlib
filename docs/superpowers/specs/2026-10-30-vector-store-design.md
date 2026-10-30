# Vector store for a paper's chunks (StudyAnalyzer item 8) — design

Date: 2026-10-30. Item 8 of StudyAnalyzer's bmlib list
(`~/src/studyanalyzer/docs/superpowers/specs/2026-10-21-studyanalyzer-design.md`
§7, §8): *vector storage for chunks + embeddings in SQLite*. The last
StudyAnalyzer item left in bmlib.

## Intent

StudyAnalyzer's Interrogator answers from retrieved passages when a paper does
not fit the model's context window. That needs each paper's chunks and their
embeddings stored (the app's rule: nothing found about a document lives only in
memory) and searched by a question. bmlib owns this so every app gets the same
store, the same ranking and the same staleness check, in Python and Rust alike.

The staleness check is the point of owning it: a vector is meaningful only
against vectors from the same model file over the same text cut the same way.
A question embedded by another model ranks the chunks meaninglessly and raises
nothing, so the store refuses that search instead.

## Goals

- **One call indexes a paper**: chunk a canonical text, embed every chunk,
  store the set, label it with how it was made. *(The maintainer's decision:
  one call in bmlib is more reliable and easier to maintain than three in the
  app.)*
- **One call searches it**: a question in, the best k chunks out, in an order
  both languages agree on exactly.
- **A stale set is refused, never searched** *(the maintainer's decision)*,
  with an error naming what changed; the app's remedy is always to call the
  indexing function again.
- **Both backends** *(the maintainer's decision)*: SQLite for a quick install
  and trying it out, PostgreSQL for serious users. The caller chooses by the
  connection it opens, as everywhere else in bmlib.
- **Python and Rust on par**, in the same PR, held together by a shared fixture
  (StudyAnalyzer §7's rule).

## Non-goals

- **sqlite-vec, pgvector or any index.** Search is brute-force cosine *(the
  maintainer's decision)*: one paper is ~50–150 chunks of 256 values, scored in
  well under a millisecond (both estimates, not measured), and sqlite-vec's `vec0` is itself a flat scan today.
  sqlite-vec would add a pre-1.0 native dependency that iOS and the App Store
  sandbox cannot load at run time and some Python builds cannot load at all.
- **Searching across papers.** A shared vector database with bmlibrarian is a
  StudyAnalyzer non-goal. The byte format below is the one sqlite-vec reads,
  so a move to it migrates no data; pgvector's binary format differs
  (big-endian, with a dimension header), so a move there would convert rows.
- **Rebuilding the canonical text.** A converter change means the text must be
  re-converted from the original file, which only the app holds.
- **Storing chunk text.** Spans are stored; the app keeps the canonical text.

## Anti-goals

- A search answering from vectors that do not match the question's model, the
  chunker or the text — silently.
- Half a set: a failed or cancelled indexing that leaves some chunks of the new
  set beside some of the old.
- Python and Rust ranking the same stored set differently.

## The API

Names are the builder's within this shape. Python in a new module beside
`bmlib/embed/` (it needs `bmlib.db` and `bmlib.fulltext.canonical`, both core);
Rust under `bmlib::embed`, generic over `dyn Db` (which no feature gates) and
**not** requiring `embed` (see *Embedders*). The text digest needs `sha2`,
which the crate has today only under `embed`, so `Cargo.toml` makes it
available to the store without that feature.

- `ensure_schema(conn)` — creates the two tables if absent, both backends, the
  `publications.ensure_schema` precedent (DDL per dialect, `create_tables`).
- `index_paper(conn, key, canonical, embedder, on_progress=None)` →
  the number of chunks stored.
  1. `chunk_canonical(canonical)` with its defaults.
  2. Embed every chunk's text (`canonical.text[span]`) as a **document** (no
     query prompt), reporting progress per chunk or per batch;
     `on_progress(done, total)` returning `False` cancels, and the call
     **raises** a cancelled error, storing nothing *(my call: the
     `download_model` precedent, a long job that produced nothing, rather than
     `chat_stream`'s, which returns the partial answer it has)*.
  3. **Then**, in one `transaction()`: replace the paper's label row and every
     chunk row. Embedding happens before the transaction opens, so a slow
     phone holds no write lock, and a failure or cancel before the commit
     leaves the old set untouched.
- `search(conn, key, canonical, question, embedder, k)` → up to k hits, each
  `(chunk_index, section_index, span, score)`.
  1. Read the label; refuse if the paper was never indexed, or if the label
     differs from the current one (below).
  2. Embed `question` with the embedder's query path (the query prompt).
  3. Score every stored chunk; return the best k.
- `delete_paper(conn, key)` — drop a paper's set (the app's "forget this
  paper"); deleting an absent key is not an error.

`key` is the app's own identifier for the paper, a non-empty string; bmlib does
not interpret it. A canonical text that yields **no chunks** (empty text) is
stored as an indexed set of zero chunks, and searching it returns no hits
rather than failing *(my call)*.

## The label

Stored once per paper; compared field by field at every search. A set is
current only if every field matches:

| Field | From | Changes when |
|---|---|---|
| model sha256 | the embedder's `ModelFile` (see *The model's identity*) | a new model or quantisation (F16 → Q8_0 counts) |
| chunker version | `CHUNKER_VERSION` | the chunker's rules change |
| converter version | `canonical.converter_version` | the text converter changes |
| text digest | sha256 of `canonical.text` as UTF-8 | the app re-pastes or re-fetches different text under the same key |
| embedding recipe version | a new constant beside `EMBEDDING_DIM`, both languages | bmlib changes pooling, `QUERY_PREFIX`, `EMBEDDING_DIM` or normalisation with the model file unchanged |

The recipe version is *my call*, from the builder check: without it, a change
to the query prompt or pooling leaves every other field matching and the search
ranks meaninglessly in silence, and a change to `EMBEDDING_DIM` would surface
as a corrupt row rather than as a stale set. The label is compared **before**
any row is read, so a set made under another recipe is always reported stale.
Bump it with any change that can move a vector for the same text and model.

The text digest is *my call*: without it, a paper whose text changed under an
unchanged converter (a re-paste, a different source) keeps spans pointing into
text that is not there. A Python text holding a lone surrogate cannot be
encoded to UTF-8 and is refused with a `ValueError` before anything is
embedded (a Rust `str` cannot hold one). It also lets `search` trust that the spans it returns
index the `canonical` it was handed.

A mismatch raises a **stale-set error carrying every field that differs** (not
only the first), so the app can say why it is re-indexing. A key never indexed
raises a **different** error. Both are distinguishable by type in both
languages (Python exception subclasses; Rust enum variants), matching #532's
precedent of one Rust variant per Python subclass.

## Storage

Two tables, both backends:

- **`chunk_sets`** — one row per paper: `key` (primary key), the five label
  fields, `chunk_count`, `indexed_at` (UTC ISO 8601).
- **`chunk_vectors`** — one row per chunk: `key` (foreign key to `chunk_sets`,
  cascading delete), `chunk_index` (0-based, primary key with `key`),
  `section_index`, `span_start`, `span_end`, `embedding`.

`embedding` is a `BLOB` on SQLite and `BYTEA` on PostgreSQL holding exactly
**256 little-endian IEEE-754 binary32 values (1,024 bytes)** — the format
sqlite-vec reads. A row of any other length is refused on read as a corrupt
store, never scored. Table names are the builder's, but must not collide with
`publications.schema`'s.

**On store**, a vector is refused unless it has exactly `EMBEDDING_DIM` finite
values with a norm within 1e-4 of 1 after rounding to binary32 *(my call: the
embedders already guarantee this, so a failure is a bmlib defect or a foreign
embedder, and storing it would put it at a fixed end of every ranking — the
reason #530 refused it)*.

## Scoring, exactly

So both languages produce identical scores and order:

1. The query vector is rounded to binary32 first, as the stored ones are
   (Python's embedder computes in binary64; Rust's in binary32).
2. Score = dot product, each binary32 term widened to binary64, multiplied, and
   **summed left to right over index 0…255 in binary64**. Hand-rolled in
   Python — `sum()` compensates on 3.12+ and not on 3.11 (the PICO agent's
   lesson, caught by its oracle).
3. Order: score descending, then `chunk_index` ascending on an exact tie.
4. `k` must be a positive integer; a k larger than the set returns the whole set.

The score is the cosine, every vector being unit length to within the store's
tolerance.

## Embedders

`index_paper` and `search` take anything with `embed_documents(texts)`,
`embed_query(text)` and the model's identity — a Python `Protocol` and a Rust
trait implemented by `Embedder` — so the store, its tests and the shared
fixture run without llama.cpp, and Rust's store does not need the `embed`
feature. *(My call; the existing embedder tests already run model-free against
a fake.)*

## The model's identity

Neither `Embedder` knows which file it loaded today: both are built from a
path alone. **The maintainer chose** (option (c) of three): a **new
constructor beside the current one** builds an `Embedder` from a `ModelFile`
and the path `download_model` returned, and the embedder carries that
`ModelFile`'s `sha256`. It does **not** re-hash at load — it relies on
`download_model`'s check when the file was written or verified, so a launch
hashes the 1.1 GB file once, not twice. The existing constructors are
unchanged (nothing breaks), and the store **refuses** an embedder built the old
way, which carries no identity, with an error saying so. Rejected: (a) a
`ModelFile` passed to `index_paper`/`search` beside the embedder, which
nothing ties to the file actually loaded; (b) hashing at every load, seconds on
a phone.

## Testing

- **Shared fixture** `tests/fixtures/vector_store.json`, read by both
  languages: scoring cases (stored vectors given as binary32 hex, a query,
  `k`, the expected order and the exact binary64 scores), tie cases, label
  cases (which field differs → which fields the error names), and refused
  vectors. Generated by a committed script, the `make_*` precedent.
- Per language, on both backends (`tests/test_backends.py`'s pattern;
  PostgreSQL required in CI): index then search round trip; re-index replaces;
  a failure and a cancel mid-embedding leave the old set; a stale set and a
  never-indexed key raise their own errors; a corrupt embedding row is refused;
  delete cascades.
- Mutation-check the scorer and the label comparison, as the PICO agent's
  reducer was.

## Left to the builder

Module and function names; batch size for embedding and how often progress is
reported; the Rust trait's exact shape; whether `index_paper` also returns the
label; the fixture generator's form; docs in `docs/manual/` and the Rust
manual.
