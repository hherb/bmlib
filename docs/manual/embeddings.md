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

Everything raises `EmbedError`; its `.index` is the offending text's position.

- An empty or whitespace-only text.
- A text over `MAX_TOKENS` (8192) tokens. It is refused, never cut short.
- `bmlib[embed]` not installed.

Vectors match the reference fixture at cosine >= 0.999 (F16) and >= 0.998 (Q8_0);
run the live tests with `BMLIB_EMBED_MODEL=/path/to/file.gguf pytest tests/test_embed_live.py`.
