# `bmlib.embed` — in-process embeddings

Turns text into 256-dimension unit vectors without a server, using our own GGUF
conversion of Snowflake's arctic-embed-l-v2.0 through llama-cpp-python.
Install with `pip install 'bmlib[embed]'` (this compiles llama.cpp, so `embed`
is not part of `bmlib[all]`: ask for it explicitly).

## The model

`ARCTIC_EMBED_L_V2_F16` (full precision) and `ARCTIC_EMBED_L_V2_Q8_0` (smaller)
are pinned by size and sha256. The base model is released under Apache-2.0.

```python
from pathlib import Path
from bmlib.embed import ARCTIC_EMBED_L_V2_Q8_0, Embedder, download_model

path = download_model(ARCTIC_EMBED_L_V2_Q8_0, Path("~/.cache/bmlib/models").expanduser())
embedder = Embedder(path)
```

`download_model` streams into a hidden temporary file of its own
(`.<file>.<id>.part`), syncs it to disk, and renames it into place only once
its size and sha256 match; nothing that fails the check is kept
(`ModelVerificationError`).

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

## Embedding

```python
docs = embedder.embed_documents(["Aspirin lowers risk...", "Insulin resistance..."])
query = embedder.embed_query("what treats malaria")
```

- `embed_documents` embeds texts as given.
- `embed_query` prepends `"query: "` first, because the model was trained with
  that prefix on search queries; documents carry none.
- Both return `list[list[float]]` / `list[float]` of `EMBEDDING_DIM` (256)
  values, unit length. The model's native vector is longer; the first 256
  values are kept and re-normalised (Matryoshka truncation). Every stored vector
  must therefore be 256 long.

## Errors

These raise `EmbedError`; its `.index` is the offending text's position (None
when no text is at fault).

- An empty or whitespace-only text.
- A text over `MAX_TOKENS` (8192) tokens. It is refused, never cut short.
- llama-cpp-python cannot be imported (the message says what was raised; it
  comes with `bmlib[embed]`).
- A missing or unloadable model file, or llama.cpp failing to decode a batch.

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
- `Llama(message)`: the model file is missing or will not load, or llama.cpp fails.

Drop the `Embedder` before the process exits. On Metal, llama.cpp aborts at
`exit()` while a model is loaded. That covers `std::process::exit`, an
`Embedder` in a `static`, and a Tauri/tao event loop, which exits without
dropping managed state. Keep it in an `Option` (e.g. `Mutex<Option<Embedder>>`
in app state) and take it out on `RunEvent::ExitRequested` / `RunEvent::Exit`.

Run the live tests with
`BMLIB_EMBED_MODEL=/path/to/file.gguf cargo test -p bmlib --features embed --test embed_live -- --test-threads=1`
(in `rust/`), to the same tolerances. One thread: each test loads its own
model.
