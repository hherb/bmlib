# bmlib::embed — Embeddings and the Chunk Store

In-process text embedding with arctic-embed-l-v2.0, and a store for one
paper's chunks and their embeddings, searched by brute-force cosine. The
Python page is [embeddings.md](../../../../docs/manual/embeddings.md); this one
describes the Rust API and where it differs.

| Need | Feature |
|---|---|
| `Embedder`, `verify_model`, `ModelError` | `embed` (builds llama.cpp) |
| `download_model` | `embed` and `http-ureq` (default) |
| The constants, `ModelFile`, the vector rules, `embed::store` | none |

## Loading the model

```rust,ignore
use std::path::Path;
use bmlib::embed::{download_model, Embedder, ARCTIC_EMBED_L_V2_Q8_0};

let path = download_model(&ARCTIC_EMBED_L_V2_Q8_0, Path::new("models"), None)?;
let mut embedder = Embedder::load_model(&ARCTIC_EMBED_L_V2_Q8_0, &path)?;
let query = embedder.embed_query("what treats malaria")?; // 256 values, unit length
```

`Embedder::load_model` loads as `Embedder::load` does and keeps the
`ModelFile` (`embedder.model()`), without hashing the file again — it trusts
`download_model`'s check. The chunk store refuses an embedder without that
identity, so build the one you index with this way. Drop the `Embedder` before
the process exits (llama.cpp aborts at `exit()` on Metal while a model is
loaded).

## Storing and searching a paper's chunks

`embed::store` takes any `&mut dyn Db` and any [`ChunkEmbedder`]; `Embedder`
is one under `embed`. Here a stand-in embeds every text to the same vector:

```rust
use bmlib::db::open_memory;
use bmlib::embed::store::{ensure_schema, index_paper, search, BoxError, ChunkEmbedder};
use bmlib::embed::{ModelFile, ARCTIC_EMBED_L_V2_F16, EMBEDDING_DIM};
use bmlib::fulltext::canonical::canonical_from_plain_text;

struct Constant;

impl ChunkEmbedder for Constant {
    fn model(&self) -> Option<&ModelFile> {
        Some(&ARCTIC_EMBED_L_V2_F16)
    }
    fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, BoxError> {
        Ok(texts.iter().map(|_| self.embed_query("").unwrap()).collect())
    }
    fn embed_query(&mut self, _query: &str) -> Result<Vec<f32>, BoxError> {
        let mut v = vec![0.0; EMBEDDING_DIM];
        v[0] = 1.0;
        Ok(v)
    }
}

let mut db = open_memory()?;
ensure_schema(&mut db)?;
let paper = canonical_from_plain_text("Methods were blinded.\n\nResults were null.", "A trial");
let mut done = |indexed: usize, total: usize| indexed <= total; // false would cancel
let count = index_paper(&mut db, "pmid:1", &paper, &mut Constant, Some(&mut done))?;
let hits = search(&mut db, "pmid:1", &paper, "Was it blinded?", &mut Constant, 8)?;
assert_eq!(hits.len(), count);
assert!(hits.windows(2).all(|w| w[0].chunk_index < w[1].chunk_index)); // ties: lower index first
println!("{}", paper.span_text(hits[0].span).unwrap());
# Ok::<(), Box<dyn std::error::Error>>(())
```

- `index_paper` chunks with `chunk_canonical`'s defaults, embeds in batches of
  `INDEX_BATCH_SIZE` (16) and only then replaces the paper's set in one
  transaction, so a failure or a cancel (`StoreError::Cancelled`) leaves the
  old set. It returns the number of chunks; an empty text is a set of none.
- `search` returns up to `k` `ChunkHit`s (`chunk_index`, `section_index`,
  `span`, `score`), best first. `paper` must be the text that was indexed.
- `delete_paper` removes a set; deletes are explicit, so foreign keys may be off.

**A set whose label is not the current one is refused**, never ranked:
`StoreError::Stale(fields)` names every `LabelField` that differs — the model
file's sha256, `EMBEDDING_RECIPE_VERSION`, `CHUNKER_VERSION`, the text's
`converter_version`, the text's sha256. Index the paper again. The other
variants mirror Python's classes: `NotIndexed`, `Corrupt` (a row that cannot be
part of the set), `UnidentifiedEmbedder`, `Vector`, `InvalidArgument` (an empty
key, a `k` of 0), plus `Db` and `Embed` carrying the database's and the
embedder's own errors.

The scoring is Python's to the bit: vectors stored as 256 little-endian
binary32 values, the dot product summed left to right in binary64 from `0.0`,
held by `tests/fixtures/vector_store.json`.
