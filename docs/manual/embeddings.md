# `bmlib.embed` — in-process embeddings

Turns text into 256-dimension unit vectors without a server, using our own GGUF
conversion of Snowflake's arctic-embed-l-v2.0 through llama-cpp-python.
Install with `pip install 'bmlib[embed]'` (this compiles llama.cpp).

## The model

`ARCTIC_EMBED_L_V2_F16` (full precision) and `ARCTIC_EMBED_L_V2_Q8_0` (smaller)
are pinned by size and sha256. The base model is released under Apache-2.0.

```python
from pathlib import Path
from bmlib.embed import ARCTIC_EMBED_L_V2_Q8_0, Embedder, download_model

path = download_model(ARCTIC_EMBED_L_V2_Q8_0, Path("~/.cache/bmlib/models").expanduser())
embedder = Embedder(path)
```

`download_model` streams through a `.part` file and keeps nothing that fails its
size or sha256 check (`ModelVerificationError`).

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
- `bmlib[embed]` not installed.

A missing or unloadable model file is not an `EmbedError`: llama-cpp-python
raises its own `ValueError`.

Vectors match the reference fixture at cosine >= 0.999 (F16) and >= 0.998 (Q8_0);
run the live tests with `BMLIB_EMBED_MODEL=/path/to/file.gguf pytest tests/test_embed_live.py`.

## Rust

The same embedder is `bmlib::embed::Embedder`, over llama-cpp-2, behind the
`embed` feature (which compiles llama.cpp, so it needs cmake and a C++ compiler).
On Apple platforms llama.cpp builds with Metal either way; `embed-metal` adds
the crate's `metal` feature.

```rust
use std::path::Path;
use bmlib::embed::Embedder;

let mut embedder = Embedder::load(Path::new("snowflake-arctic-embed-l-v2.0-Q8_0.gguf"))?;
let docs = embedder.embed_documents(&["Aspirin lowers risk...", "Insulin resistance..."])?;
let query = embedder.embed_query("what treats malaria")?;
```

`ModelFile`, `verify_model` and `download_model` (with the default `http-ureq`
feature) mirror the Python ones; a failed check is a `ModelError`.

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
`BMLIB_EMBED_MODEL=/path/to/file.gguf cargo test -p bmlib --features embed --test embed_live`
(in `rust/`), to the same tolerances.
