# `bmlib.embed` — in-process embeddings *(unreleased)*

Turns text into 256-dimension unit vectors without a server, using our own GGUF
conversion of Snowflake's arctic-embed-l-v2.0 through llama-cpp-python.
Install with `pip install 'bmlib[embed]'` (llama-cpp-python>=0.3.36; this
compiles llama.cpp, so `embed` is not part of `bmlib[all]`: ask for it
explicitly, e.g. `bmlib[all,embed]`). Only `Embedder` needs the extra: the
constants, the vector rules and the model download are stdlib only.

## The model

`ARCTIC_EMBED_L_V2_F16` (full precision) and `ARCTIC_EMBED_L_V2_Q8_0` (smaller)
are pinned by size and sha256. The base model is released under Apache-2.0.

```python
from pathlib import Path
from bmlib.embed import ARCTIC_EMBED_L_V2_Q8_0, Embedder, download_model

path = download_model(ARCTIC_EMBED_L_V2_Q8_0, Path("~/.cache/bmlib/models").expanduser())
embedder = Embedder.from_model(ARCTIC_EMBED_L_V2_Q8_0, path)
```

`Embedder.from_model(model, path, *, gpu_layers=-1)` *(unreleased)* loads as
`Embedder(path)` does and keeps `model` as `embedder.model`, without hashing
the file again (it trusts `download_model`'s check). The chunk store needs that
identity; `Embedder(path)` leaves `model` as `None`.

`download_model` streams into a hidden temporary file of its own
(`.<file>.<id>.part`), syncs it to disk, and renames it into place only once
its size and sha256 match; nothing that fails the check is kept
(`ModelVerificationError`, raised as `SizeMismatchError` or
`ChecksumMismatchError`, whose class attribute `.what` is `"size"` or
`"sha256"` as well).

- It re-hashes an existing file on every call (about 1 s per GB): call it once
  per launch, not before each use. A file that fails the check is deleted,
  with a WARNING, and fetched again.
- Concurrent calls for one directory are safe: each downloads into its own
  temporary file, so a second call costs bandwidth, never a corrupt model.
- `progress(done_bytes, total_bytes)`, if given, runs after each block of up
  to 1 MiB. Returning exactly `False` cancels: the temporary file is removed
  and `DownloadCancelled` is raised. Returning `None` or `True` carries on.
- A `ModelFile` must name a 40-hex revision, a bare file name, a positive size
  and a lower-case sha256; anything else is a `ValueError` when it is built.
  Its fields are `repo`, `revision`, `file`, `size` and `sha256`; `url()` is
  the revision-pinned Hugging Face URL.
- `download_model(model, directory, progress=None)` returns the final path
  (`directory / model.file`), creating `directory` if missing. A network or
  file-system failure is an `OSError` (`URLError` included). It sends
  `User-Agent: bmlib/<version>`.

`verify_model(path, model)` is the check alone: the size, then the streamed
sha256, raising `SizeMismatchError` / `ChecksumMismatchError` (or `OSError`
if the file cannot be read) and returning `None` when the file is the pinned
one.

## Embedding

```python
docs = embedder.embed_documents(["Aspirin lowers risk...", "Insulin resistance..."])
query = embedder.embed_query("what treats malaria")
```

`Embedder(model_path, *, gpu_layers=-1)` loads the GGUF; `gpu_layers` is the
number of layers offloaded to the GPU (-1 for all).

- `embed_documents` embeds texts as given.
- `embed_query` prepends `QUERY_PREFIX` (`"query: "`) first, because the model was trained with
  that prefix on search queries; documents carry none.
- Both return `list[list[float]]` / `list[float]` of `EMBEDDING_DIM` (256)
  values, unit length. The model's native vector is longer; the first 256
  values are kept and re-normalised (Matryoshka truncation). Every stored vector
  must therefore be 256 long.

## Vector rules

The pure half of the module, shared with the Rust crate and pinned by
`tests/fixtures/embedding_contract.json`, which both test suites read:

| Name | What it is |
|------|------------|
| `EMBEDDING_DIM` | 256, the length of every vector returned |
| `MAX_TOKENS` | 8192, the longest text embedded (in tokens, BOS included) |
| `QUERY_PREFIX` | `"query: "` |
| `query_text(query)` | `QUERY_PREFIX + query`, the text `embed_query` embeds |
| `check_texts(texts)` | Returns `None`, or raises `EmptyTextError` for the first empty or whitespace-only text |
| `truncate_and_normalize(vector)` | The first 256 values scaled to unit length; values past 256 are dropped unread. Raises `VectorError` for a vector shorter than 256, a NaN or infinite kept value, or a kept norm below `1e-12` (or overflowing) *(unreleased — #530: a non-finite value used to pass through and a zero head came back as a zero "unit" vector)* |

```python
from bmlib.embed import query_text, truncate_and_normalize

query_text("what treats malaria")                         # "query: what treats malaria"
truncate_and_normalize([3.0, 4.0] + [0.0] * 300)[:2]      # [0.6, 0.8]
```

## Errors

Every failure of a call given a list of texts is an `EmbedError`; its
`.index` is the offending text's position (None when no text is at fault).
Each kind is a subclass of its own, named after the Rust variant it mirrors
*(unreleased — #532)*:

- `EmptyTextError` (also a `ValueError`): an empty or whitespace-only text,
  checked before anything is embedded; an empty query is index 0.
- `TextTooLongError`: a text over `MAX_TOKENS` (8192) tokens; `.tokens` is its
  count. It is refused, never cut short.
- `EmbedError` itself, with `.index`: a text that cannot be tokenized — one
  carrying a lone surrogate, which will not encode, or one llama-cpp-python's
  tokenizer refuses. Rust has no variant for it: its strings are valid UTF-8 and
  its tokenizer cannot fail.
- `UnusableOutputError`: a text whose model output is not an embedding: a NaN
  or infinite value among the 256 kept, or a norm below `1e-12` (an all-zero
  head). A healthy model produces neither, but a corrupt GGUF or a backend
  fault can, and such a vector would sit at a fixed end of every cosine
  ranking, so it is refused rather than stored. `.error` (and `__cause__`) is
  the `VectorError` (a `ValueError`) that `truncate_and_normalize` raised,
  which it also raises for a vector shorter than 256. When several texts fail,
  `.index` is the lowest. *(unreleased — #530)*
- `ModelLoadError`: llama-cpp-python cannot be imported (the message says what
  was raised; it comes with `bmlib[embed]`), the model file is missing or will
  not load, llama.cpp cannot create its context, or the model embeds to fewer
  than 256 values. No `.index`. llama-cpp-python creates the context with the
  model, so a context failure (say, too little memory for 8192 tokens) is this
  error here and Rust's `Decode`, at the first call, there.
- `BatchDecodeError`: llama.cpp fails to decode a batch. No `.index`.

Each survives pickling, so an error raised in a process pool's worker reaches
the pool intact.

Passing one `str` to `embed_documents` (instead of a list) is a `TypeError`.

An `Embedder` is not thread-safe: use one per thread, or a lock round each
call. `close()` frees the model, as does leaving a `with Embedder(path) as
embedder:` block.

The embedder runs with flash attention on. Without it llama.cpp reserves the
whole non-causal attention matrix for an 8192-token batch up front, about
4.5 GiB instead of about 350 MiB.

Vectors match the reference fixture at cosine >= 0.999 (F16) and >= 0.998 (Q8_0);
run the live tests with `BMLIB_EMBED_MODEL=/path/to/file.gguf pytest tests/test_embed_live.py`
(`.[all,embed,dev]` installed).

## Storing and searching chunks *(unreleased)*

`bmlib.embed.store` keeps one paper's chunks and their embeddings in a database
opened with `bmlib.db` (SQLite or PostgreSQL, whichever the connection is) and
searches them by brute-force cosine. It is imported as `bmlib.embed.store`, not
from `bmlib.embed`, so the package itself stays free of `bmlib.db`.

```python
from bmlib.db import connect_sqlite
from bmlib.embed.store import ensure_schema, index_paper, search

conn = connect_sqlite("study.db")
ensure_schema(conn)
index_paper(conn, "pmid:12345", canonical, embedder, on_progress=lambda done, total: True)
for hit in search(conn, "pmid:12345", canonical, "Was the trial blinded?", embedder, k=8):
    print(hit.score, canonical.text[hit.span.start : hit.span.end])
```

- `ensure_schema(conn)` creates `embedded_chunk_sets` (one row per paper) and
  `embedded_chunks` (one per chunk), if absent.
- `index_paper(conn, key, canonical, embedder, on_progress=None)` chunks the
  canonical text with `chunk_canonical`'s defaults, embeds every chunk as a
  document, in batches of `INDEX_BATCH_SIZE` (16), and replaces whatever set
  `key` had, in one transaction opened only after the last chunk is embedded:
  a failure or a cancel leaves the old set as it was. `on_progress(done,
  total)` runs after each batch; returning `False` raises `IndexingCancelled`
  and stores nothing. It returns the number of chunks; an empty text is stored
  as a set of none, whose search returns `[]`.
- `search(conn, key, canonical, question, embedder, k)` returns up to `k`
  `ChunkHit`s (`chunk_index`, `section_index`, `span`, `score`), best first,
  an exact tie going to the lower chunk index. `canonical` must be the text the
  set was indexed from.
- `delete_paper(conn, key)` removes a set; an absent key is not an error.

`search` reads the label and the rows in one statement, so a re-index by
another process while it runs is seen whole or not at all. Two processes
indexing the same key at once leave one whole set, the later writer's; on
PostgreSQL, if the key had no set yet, the later one can instead fail with the
driver's unique-violation error (reasoned, not measured), and can be retried. The question's
vector is held to the stored vectors' rules (`VectorError` otherwise).

`key` is the caller's own identifier, any non-empty `str`. `embedder` is
anything with `model`, `embed_documents` and `embed_query` (the `ChunkEmbedder`
protocol); `Embedder` built by `from_model` is one.

**A stored set is labelled with how it was made**, and `search` refuses one
whose label is not the current one rather than ranking meaninglessly: the
model file's sha256, `EMBEDDING_RECIPE_VERSION` (bumped when bmlib changes
pooling, the query prefix, the 256-value cut or normalisation),
`CHUNKER_VERSION`, the canonical text's `converter_version`, and the sha256 of
its text. `ChunkSetLabel` holds the five; `current_label(canonical, embedder)`
gives the current one. Any difference raises `StaleChunkSetError`, whose
`fields` names every field that differs (`LABEL_FIELDS` order); the remedy is
always `index_paper` again, with a re-converted text if the converter changed.
Moving between the F16 and Q8_0 files is a different model.

Errors, all `ChunkSetError` subclasses of `ValueError` (and picklable):
`NotIndexedError` (no set under the key), `StaleChunkSetError`,
`CorruptChunkSetError` (a row that cannot be part of the set — its count, an
index, a span outside the text, a vector that is not 1,024 bytes of finite
values; never scored), `UnidentifiedEmbedderError` (an embedder with no
`model`, refused before anything is embedded or read) and `IndexingCancelled`.
An empty or non-`str` key, a `k` that is not a positive integer and a text
holding a lone surrogate are plain `ValueError`s; the embedder's own errors
pass through.

A vector is stored as 256 little-endian binary32 values, the layout sqlite-vec
reads, and only if it has 256 finite values whose norm is within
`NORM_TOLERANCE` (1e-4) of 1 (`VectorError` otherwise). The score is the dot
product of the binary32 values, each widened to binary64 and summed left to
right; the question's vector is rounded to binary32 first, so Python and Rust
score identically. The pure helpers are public: `encode_vector`,
`decode_vector`, `round_to_binary32`, `score` and `rank`. The shared fixture
`tests/fixtures/vector_store.json` (written by
`scripts/make_vector_store_fixture.py`) holds both languages to them.

## Rust

The same embedder is `bmlib::embed::Embedder`, over llama-cpp-2, behind the
`embed` feature (which compiles llama.cpp, so it needs cmake and a C++ compiler).
On Apple platforms `embed` already uses Metal (llama.cpp's default);
`embed-metal` makes it explicit. A CPU-only build on macOS needs
`GGML_METAL=OFF` in the environment.

```rust
use std::path::Path;
use bmlib::embed::Embedder;

let mut embedder = Embedder::load(Path::new("snowflake-arctic-embed-l-v2.0-Q8_0.gguf"))?;
let docs = embedder.embed_documents(&["Aspirin lowers risk...", "Insulin resistance..."])?;
let query = embedder.embed_query("what treats malaria")?;
```

`ModelFile`, `verify_model` and `download_model` (with the default `http-ureq`
feature) mirror the Python ones; a failed check is a `ModelError`. The same
rules hold: one call per launch, and concurrent calls are safe. The `progress`
hook is `&mut dyn FnMut(u64, u64) -> bool`; returning `false` cancels, removes
the temporary file and returns `ModelError::Cancelled`. A `ModelFile` that
cannot be pinned (`ModelFile::check`) is `ModelError::Invalid`, before any
request.

Flash attention is left to llama.cpp's AUTO policy, which turns it on for CPU
and Metal after a probe. A backend that fails the probe falls back to the
large attention buffer (about 4.5 GiB at 8192 tokens).

`Embedder` is `Send` (checked at compile time), so it can live in Tauri's
managed state.

The vectors are Python's: same prefix, same 256 values, same order as the input.
Every failure, model loading included, is an `EmbedError`:

- `EmptyText { index }`: an empty or whitespace-only text, or query.
- `TooLong { index, tokens }`: a text over `MAX_TOKENS` tokens, refused, never cut short.
- `Vector { index, error }`: the text's model output is not an embedding
  (`VectorError::NotScalable`: a NaN or infinite kept value, or a norm below
  `1e-12`); `index` is the first such text in input order. `truncate_and_normalize`
  returns `VectorError`, which also has `TooShort { len }`; the embedder never
  sees it, refusing a narrow model at load. *(unreleased — #530)*
- `Load(message)`: llama.cpp's backend will not start, the model file is missing
  or will not load, or the model embeds to fewer than 256 values.
- `Decode(message)`: llama.cpp fails while embedding, creating its context
  included (Python, which creates it with the model, reports that at load). *(unreleased — #532 split
  `Llama` into these two)*

Drop the `Embedder` before the process exits. On Metal, llama.cpp aborts at
`exit()` while a model is loaded. That covers `std::process::exit`, an
`Embedder` in a `static`, and a Tauri/tao event loop, which exits without
dropping managed state. Keep it in an `Option` (e.g. `Mutex<Option<Embedder>>`
in app state) and take it out on `RunEvent::ExitRequested` / `RunEvent::Exit`.

Run the live tests with
`BMLIB_EMBED_MODEL=/path/to/file.gguf cargo test -p bmlib --features embed --test embed_live -- --test-threads=1`
(in `rust/`), to the same tolerances. One thread: each test loads its own
model.

### The chunk store *(unreleased)*

`bmlib::embed::store` is the same store, generic over `&mut dyn Db`, so it
needs neither backend feature nor `embed`. `ensure_schema`, `index_paper`,
`search` and `delete_paper` take the same arguments; `on_progress` is
`Option<&mut dyn FnMut(usize, usize) -> bool>`, `false` cancelling.
`Embedder::load_model(&model, path)` carries the identity (`Embedder::model()`);
`Embedder::load` leaves it `None`. Any type implementing `ChunkEmbedder`
(`model`, `embed_documents`, `embed_query`, errors as `BoxError`) can index;
`Embedder` does under `embed`. Each Python error class is a `StoreError`
variant: `NotIndexed`, `Stale(Vec<LabelField>)`, `Corrupt`,
`UnidentifiedEmbedder`, `Cancelled`, `Vector`, `InvalidArgument`, plus `Db`
and `Embed` for the database's and the embedder's own failures.
