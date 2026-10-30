# Vector store for a paper's chunks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store a paper's chunks and their embeddings in SQLite or PostgreSQL, search them by a question with brute-force cosine, and refuse a stale set — in Python and Rust alike.

**Architecture:** One new module per language (`bmlib/embed/store.py`, `rust/bmlib/src/embed/store.rs`) holding a pure half (vector codec, label, scorer — held to a shared JSON fixture) and a database half (`ensure_schema`, `index_paper`, `search`, `delete_paper` over `bmlib.db`). The embedder is reached through a small Protocol/trait, so neither needs llama.cpp; the real `Embedder` gains a constructor that carries its `ModelFile`.

**Tech Stack:** Python 3.11+ stdlib + `bmlib.db`; Rust over `bmlib::db::Db`, `sha2`, `chrono`; pytest with the `backend_conn` fixture; cargo test, `BMLIB_PG_TESTS=1` for PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-10-30-vector-store-design.md`

## Global Constraints

- Vectors are `EMBEDDING_DIM` = 256 values, stored as exactly 1,024 bytes of little-endian IEEE-754 binary32 (`BLOB` on SQLite, `BYTEA` on PostgreSQL).
- A vector is stored only if it has 256 finite values whose norm, after rounding to binary32, is within **1e-4** of 1; otherwise `VectorError`.
- Score = dot product of binary32 values each widened to binary64, summed left to right over index 0…255 in binary64; the query is rounded to binary32 first. Python hand-rolls the sum (never `sum()`/`math.fsum`).
- Order: score descending, then `chunk_index` ascending; `k` a positive integer, a k above the set size returns the set.
- Label fields, in this order and with these wire names: `model_sha256`, `recipe_version`, `chunker_version`, `converter_version`, `text_sha256` (sha256 hex of `canonical.text` as UTF-8). A stale error names **every** differing field, in that order.
- The label is compared before any chunk row is read.
- `index_paper` embeds **before** opening its transaction; a failure or cancel leaves the old set untouched; cancel raises.
- An embedder with no `ModelFile` identity is refused before anything is embedded or read.
- Python text holding a lone surrogate → `ValueError` before anything is embedded.
- Tables must not collide with `publications.schema`'s; both backends; every placeholder from `placeholder(conn)`.
- AGPL header on every new file; ruff 0.15.20, mypy, both clean.
- Every refused case in Rust is an error variant mirroring a Python exception class (#532's rule).

## Review Focus

1. **An app indexes with Q8_0 then searches with F16** (both arctic files) — expect a stale error naming `model_sha256` only. → Task 3 `test_a_different_model_file_is_stale`.
2. **The app re-indexes after a crash left a set half-written by another process** — a set whose stored `chunk_count` disagrees with its rows must be refused as corrupt, never scored. → Task 3 `test_a_set_missing_rows_is_corrupt`.
3. **The embedder raises mid-indexing** (a `TextTooLongError` on chunk 40) — the old set must survive whole. → Task 3 `test_an_embedder_failure_keeps_the_old_set`.
4. **Foreign keys switched off** (Rust `open_path(…, foreign_keys=false)`) — `delete_paper` and re-indexing must still remove every chunk row, so deletes are explicit, not by cascade. → Task 3 `test_delete_needs_no_cascade`, Task 6 `delete_needs_no_cascade`.
5. **The canonical text passed to `search` is not the one indexed** (same converter, different text) — stale on `text_sha256`, never spans into the wrong text. → Task 3 `test_different_text_is_stale`.

---

### Task 1: Python — the recipe version and an embedder that knows its model

**Files:**
- Modify: `bmlib/embed/vectors.py` (add the constant), `bmlib/embed/embedder.py`, `bmlib/embed/__init__.py`
- Test: `tests/test_embed_vectors.py`, `tests/test_embed_live.py` (the model-free fake-llama section)

**Interfaces:**
- Produces: `EMBEDDING_RECIPE_VERSION: int = 1` in `bmlib.embed.vectors`, exported from `bmlib.embed`. `Embedder.model: ModelFile | None` (`None` from `Embedder(path)`); `Embedder.from_model(model: ModelFile, model_path: Path, *, gpu_layers: int = -1) -> Embedder` — loads exactly as `__init__`, does **not** hash the file, sets `.model = model`.

- [ ] **Step 1: Failing tests** — `test_the_recipe_version_is_one` (`assert EMBEDDING_RECIPE_VERSION == 1`, and a comment on the constant naming what bumps it: pooling, `QUERY_PREFIX`, `EMBEDDING_DIM`, normalisation); with the existing fake llama object: `test_a_path_built_embedder_has_no_model` (`Embedder(path).model is None`), `test_from_model_carries_the_model_file` (`Embedder.from_model(ARCTIC_EMBED_L_V2_F16, path).model is ARCTIC_EMBED_L_V2_F16`), `test_from_model_does_not_hash` (monkeypatch `bmlib.embed.model.verify_model` and `hashlib.sha256` to raise; construction succeeds).
- [ ] **Step 2:** `uv run pytest tests/test_embed_vectors.py tests/test_embed_live.py -v` → the four FAIL.
- [ ] **Step 3:** Implement; `from_model` is a classmethod calling `cls(model_path, gpu_layers=gpu_layers)` then setting `model`.
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5:** Commit `feat(embed): EMBEDDING_RECIPE_VERSION and Embedder.from_model`.

### Task 2: Python — the pure half (codec, label, scorer)

**Files:**
- Create: `bmlib/embed/store.py` (pure half first; Task 3 adds the database half)
- Test: `tests/test_embed_store.py`

**Interfaces:**
- Produces, all in `bmlib.embed.store`:
  - `encode_vector(values: Sequence[float]) -> bytes` — checks (Global Constraints), then `struct.pack("<256f", …)`; raises `VectorError`.
  - `decode_vector(blob: bytes) -> list[float]` — raises `CorruptChunkSetError` unless `len(blob) == 1024` and every value finite.
  - `score(query: Sequence[float], vector: Sequence[float]) -> float` — the exact rule; `query` rounded to binary32 by the caller-visible helper `round_to_binary32(values) -> list[float]`.
  - `rank(query: Sequence[float], vectors: Sequence[Sequence[float]], k: int) -> list[tuple[int, float]]` — `(chunk_index, score)`, rounds `query` itself, order and `k` per Global Constraints; `ValueError` for a bad `k`.
  - `@dataclass(frozen=True) ChunkSetLabel(model_sha256: str, recipe_version: int, chunker_version: int, converter_version: str, text_sha256: str)` with `differences(self, other: ChunkSetLabel) -> tuple[str, ...]` (wire names, label order) and `to_dict`/`from_dict`.
  - `LABEL_FIELDS: tuple[str, ...]` — the five wire names in order.
  - Errors: `ChunkSetError(ValueError)`; subclasses `NotIndexedError`, `StaleChunkSetError` (`.fields: tuple[str, ...]`, message names them), `CorruptChunkSetError`, `UnidentifiedEmbedderError`, `IndexingCancelled`. All pickle (the `__reduce__` pattern in `embedder.py`).

- [ ] **Step 1: Failing tests:** `test_a_vector_round_trips` (encode→decode of a normalised vector equals `round_to_binary32` of it); `test_encode_refuses` parametrized over 255 values, 257 values, a NaN, an infinity, norm 0.5, norm 1.001 → `VectorError`; `test_decode_refuses_a_short_blob` (1,023 bytes) and `..._a_nan` → `CorruptChunkSetError`; `test_score_sums_left_to_right` (for seeded random unit-vector pairs, seeds 0..49, `score` equals an explicit left-to-right binary64 loop over the binary32-rounded values, exactly; and for at least one seed that loop differs from `math.fsum` of the same products, asserted, so a compensated sum is caught); `test_rank_breaks_ties_by_chunk_index`; `test_rank_returns_the_set_when_k_exceeds_it`; `test_rank_refuses_k` over `0`, `-1`, `True`, `1.0`; `test_differences_names_every_field_in_order`; `test_errors_pickle`.
- [ ] **Step 2:** `uv run pytest tests/test_embed_store.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Same → PASS.
- [ ] **Step 5:** Commit `feat(embed): the vector store's codec, label and scorer`.

### Task 3: Python — the database half

**Files:**
- Modify: `bmlib/embed/store.py`, `bmlib/embed/__init__.py` (export the public names; `store` imports only `bmlib.db`, `bmlib.fulltext.canonical`, stdlib — keep `import bmlib.embed` free of llama)
- Test: `tests/test_embed_store.py` (uses `backend_conn` from `tests/conftest.py`, so every DB test runs on SQLite and PostgreSQL)

**Interfaces:**
- Consumes: Task 1's `Embedder.model`, `EMBEDDING_RECIPE_VERSION`; Task 2's names; `chunk_canonical`, `CHUNKER_VERSION`, `CanonicalText`.
- Produces:
  - `class ChunkEmbedder(Protocol)`: `model: ModelFile | None`; `embed_documents(texts: Sequence[str]) -> list[list[float]]`; `embed_query(query: str) -> list[float]`.
  - `@dataclass(frozen=True) ChunkHit(chunk_index: int, section_index: int, span: TextSpan, score: float)` with `to_dict`/`from_dict`.
  - `ensure_schema(conn) -> None` — tables `embedded_chunk_sets` (`paper_key TEXT PRIMARY KEY`, the five label columns, `chunk_count INTEGER NOT NULL`, `indexed_at TEXT NOT NULL`) and `embedded_chunks` (`paper_key TEXT NOT NULL REFERENCES embedded_chunk_sets(paper_key) ON DELETE CASCADE`, `chunk_index`, `section_index`, `span_start`, `span_end` INTEGER NOT NULL, `embedding BLOB`/`BYTEA NOT NULL`, `PRIMARY KEY (paper_key, chunk_index)`), via `create_tables`, DDL per dialect.
  - `current_label(canonical: CanonicalText, embedder: ChunkEmbedder) -> ChunkSetLabel`.
  - `index_paper(conn, key: str, canonical: CanonicalText, embedder: ChunkEmbedder, on_progress: Callable[[int, int], bool | None] | None = None) -> int` — order: validate `key` (`type(key) is str` and non-empty, else `ValueError`), identity (`UnidentifiedEmbedderError`), surrogates (`ValueError`); chunk; embed in batches of `INDEX_BATCH_SIZE = 16`, calling `on_progress(done, total)` after each batch, `False` → `IndexingCancelled`; encode every vector; then one `transaction(conn)`: delete `embedded_chunks` rows, delete the set row, insert the set row (`indexed_at` = UTC ISO 8601, seconds), `executemany` the chunks. Returns the chunk count (0 for an empty text, still stored).
  - `search(conn, key: str, canonical: CanonicalText, question: str, embedder: ChunkEmbedder, k: int) -> list[ChunkHit]` — order: validate `key`, `k`, identity, surrogates; read the set row (`NotIndexedError` if absent); compare `current_label` (`StaleChunkSetError`); `embed_query(question)`; read rows ordered by `chunk_index`; `CorruptChunkSetError` if the row count ≠ `chunk_count`, indices are not 0…n-1, a span lies outside `canonical.text`, or a blob fails `decode_vector`; `rank`; build hits. A zero-chunk set returns `[]` without calling `embed_query`.
  - `delete_paper(conn, key: str) -> None` — explicit delete of both tables in one transaction; absent key is fine.

- [ ] **Step 1: Failing tests** (a `FakeEmbedder` in the test file: `.model = ARCTIC_EMBED_L_V2_F16`, deterministic unit vectors from a sha256 of each text, counts calls; a `CanonicalText` built with `canonical_from_plain_text`): `test_ensure_schema_is_idempotent`; `test_index_then_search` (hits are `ChunkHit`s, best first, spans inside the text); `test_the_question_uses_the_query_path` (fake records `embed_query` was called once); `test_reindexing_replaces_the_set`; `test_an_empty_text_is_an_empty_set` (count 0, `search` → `[]`, no `embed_query` call); `test_a_never_indexed_key_raises`; `test_a_different_model_file_is_stale` (index with `.model = ARCTIC_EMBED_L_V2_Q8_0`, search with F16 → `.fields == ("model_sha256",)`); `test_a_new_recipe_is_stale`, `test_a_new_chunker_is_stale` (monkeypatch the constant in `bmlib.embed.store`); `test_different_text_is_stale` (`("text_sha256",)`); `test_another_converter_is_stale` (same text, a different `converter_version` → `("converter_version",)`); `test_an_embedder_without_identity_is_refused` (both calls, nothing embedded, nothing written); `test_an_embedder_failure_keeps_the_old_set` (fake raises `TextTooLongError` on the 2nd batch); `test_a_cancel_keeps_the_old_set` (callback returns `False` after the first batch → `IndexingCancelled`); `test_progress_reaches_the_total`; `test_a_set_missing_rows_is_corrupt` (delete one row by hand); `test_a_bad_blob_is_corrupt` (update one row to 1,023 bytes); `test_delete_paper` and `test_delete_needs_no_cascade` (SQLite with `PRAGMA foreign_keys = OFF`: no orphan rows after delete and after re-index); `test_a_lone_surrogate_is_refused`; `test_bad_keys_are_refused` over `""`, `None`, `1`.
- [ ] **Step 2:** `uv run pytest tests/test_embed_store.py -v` → the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Same → PASS; with `BMLIB_TEST_POSTGRESQL_DSN="host=localhost port=5432 dbname=bmlib_test user=postgres" BMLIB_REQUIRE_POSTGRESQL=1` → PASS on both halves.
- [ ] **Step 5:** Commit `feat(embed): index and search a paper's chunks (both backends)`.

### Task 4: The shared fixture

**Files:**
- Create: `scripts/make_vector_store_fixture.py`, `tests/fixtures/vector_store.json`, `tests/test_vector_store_contract.py`

**Interfaces:**
- Consumes: Task 2's pure names.
- Produces: the JSON `{"embedding_dim": 256, "label_fields": [...], "codec": [...], "decode": [...], "rank": [...], "labels": [...]}`:
  - `codec`: `{"name", "values_f64_hex": [16-hex big-endian binary64 bits per value], "blob_hex" | "refused": true}` — round trips and every refusal of Task 2 Step 1's `test_encode_refuses`.
  - `decode`: `{"name", "blob_hex", "refused": true}` for 1,023 bytes and a NaN.
  - `rank`: `{"name", "query_f64_hex": [...], "vectors_blob_hex": [...], "k", "expected": [{"chunk_index", "score_f64_hex"}]}` — at least: 12 seeded random unit vectors with k=5, a k above the set, three identical vectors (tie), k=1. Vectors from `random.Random(seed)` Gaussian values passed through `truncate_and_normalize`.
  - `labels`: `{"stored": {label}, "current": {label}, "differs": [...]}` — none, each single field, all five.
  - Rust reads binary64 values via `f64::from_bits(u64::from_str_radix(h, 16))`, never decimal.
- [ ] **Step 1:** Write `tests/test_vector_store_contract.py`: `test_the_fixture_is_what_the_script_writes` (import the script by path, the `make_canonical_chunks` precedent; its builder's output equals the committed JSON) and one test per section asserting Task 2's functions reproduce every case exactly.
- [ ] **Step 2:** Run → FAIL (no fixture).
- [ ] **Step 3:** Write the script (`--write` writes, default checks and exits 1 on drift); run it with `--write`.
- [ ] **Step 4:** `uv run pytest tests/test_vector_store_contract.py -v` → PASS.
- [ ] **Step 5:** Commit `test(embed): the vector store's shared fixture`.

### Task 5: Rust — recipe version, `Embedder::load_model`, `sha2` for the store

**Files:**
- Modify: `rust/bmlib/src/embed/vectors.rs`, `rust/bmlib/src/embed/embedder.rs`, `rust/bmlib/src/embed/mod.rs`, `rust/bmlib/Cargo.toml` (`sha2` no longer optional; `embed` drops `dep:sha2`)
- Test: `rust/bmlib/tests/embed_contract.rs`, `rust/bmlib/tests/embed_live.rs`

**Interfaces:**
- Produces: `pub const EMBEDDING_RECIPE_VERSION: u32 = 1;` (re-exported); `Embedder::load_model(model: &ModelFile, path: &Path) -> Result<Embedder, EmbedError>` (no hashing) and `Embedder::model(&self) -> Option<&ModelFile>`; `Embedder::load` leaves it `None`.
- [ ] **Step 1:** Tests: `recipe_version_is_one` in `embed_contract.rs` (no feature needed); in `embed_live.rs` (gated as the file already is) `load_model_carries_the_model` and `load_leaves_no_model`.
- [ ] **Step 2:** `cd rust && cargo test -p bmlib --test embed_contract` → FAIL.
- [ ] **Step 3:** Implement; `ModelFile` must be `Clone` if not already (store an owned copy).
- [ ] **Step 4:** `cargo test -p bmlib --test embed_contract`, `cargo build -p bmlib --no-default-features`, `cargo build -p bmlib-ffi` → PASS.
- [ ] **Step 5:** Commit `feat(rust/embed): EMBEDDING_RECIPE_VERSION and Embedder::load_model`.

### Task 6: Rust — the store

**Files:**
- Create: `rust/bmlib/src/embed/store.rs`; `rust/bmlib/tests/vector_store.rs`, `rust/bmlib/tests/vector_store_contract.rs`
- Modify: `rust/bmlib/src/embed/mod.rs`; `rust/bmlib/tests/postgres_live.rs` (one round trip)

**Interfaces:**
- Consumes: Task 5; `bmlib::db::{Db, transaction_with, create_tables, Value}`; `chunk_canonical`, `CHUNKER_VERSION`, `DEFAULT_MAX_CHARS`, `DEFAULT_OVERLAP_CHARS`, `CanonicalText`.
- Produces, in `bmlib::embed` (not feature-gated):
  - `pub trait ChunkEmbedder { fn model(&self) -> Option<&ModelFile>; fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, Box<dyn std::error::Error + Send + Sync>>; fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, Box<dyn std::error::Error + Send + Sync>>; }`, implemented for `Embedder` under `feature = "embed"`.
  - `encode_vector(&[f32]) -> Result<Vec<u8>, StoreError>`, `decode_vector(&[u8]) -> Result<Vec<f32>, StoreError>`, `score(&[f32], &[f32]) -> f64`, `rank(&[f32], &[Vec<f32>], usize) -> Result<Vec<(usize, f64)>, StoreError>`, `ChunkSetLabel` (same fields; `differences(&self, &Self) -> Vec<LabelField>`), `pub enum LabelField { ModelSha256, RecipeVersion, ChunkerVersion, ConverterVersion, TextSha256 }` with `as_str()` giving the wire names, `ChunkHit { chunk_index, section_index, span, score }`.
  - `#[non_exhaustive] pub enum StoreError { Db(DbError), NotIndexed, Stale(Vec<LabelField>), Corrupt(String), UnidentifiedEmbedder, Cancelled, Vector(String), Embed(Box<dyn Error + Send + Sync>), InvalidArgument(String) }`, `Display` + `Error`.
  - `ensure_schema(&mut dyn Db) -> Result<(), StoreError>`, `index_paper(&mut dyn Db, &str, &CanonicalText, &mut dyn ChunkEmbedder, Option<&mut dyn FnMut(usize, usize) -> bool>) -> Result<usize, StoreError>`, `search(&mut dyn Db, &str, &CanonicalText, &str, &mut dyn ChunkEmbedder, usize) -> Result<Vec<ChunkHit>, StoreError>`, `delete_paper(&mut dyn Db, &str) -> Result<(), StoreError>` — Python's rules, same SQL, same order of checks, same batch size 16. Rust scores binary32 query values as given (already binary32).
- [ ] **Step 1:** `vector_store_contract.rs`: every fixture section reproduced exactly (binary64 by bits). `vector_store.rs` (SQLite in memory via `open_memory`, a fake embedder like Python's): one test per Task 3 test, same names in snake_case, plus `delete_needs_no_cascade` with `open_path(…, false, false)` on a temp file. `postgres_live.rs`: `vector_store_round_trip` (index, search, stale, delete) under the file's existing gate.
- [ ] **Step 2:** `cargo test -p bmlib --test vector_store_contract --test vector_store` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Same → PASS; `BMLIB_PG_TESTS=1 cargo test -p bmlib --features postgres --test postgres_live` → PASS; `cargo clippy -p bmlib --all-targets -- -D warnings`, `cargo build -p bmlib --no-default-features`.
- [ ] **Step 5:** Commit `feat(rust/embed): the vector store`.

### Task 7: Docs, mutation check, the records

**Files:**
- Modify: `docs/manual/embeddings.md` (a *Storing and searching chunks* section, every exported name), `rust/bmlib/docs/manual/` (an `embed.md` page, `db.md` as the template, its examples as doctests over a fake embedder), `CHANGELOG.md` (`[Unreleased]` entry *A paper's chunks, stored and searched*), `rust/bmlib/CHANGELOG.md`, `rust/README.md` (port table row), `ROADMAP.md` (a row, `*(unreleased)*`), `HANDOVER.md` (this session; #580/#584 merged; item 8 done — no StudyAnalyzer item left in bmlib; the app's how-to), `CLAUDE.md` (directory tree line for `embed/store.py`, test-mapping row).
- [ ] **Step 1:** Mutation-check the scorer, `rank`'s order, `differences` and `encode_vector`'s bounds in both languages (back the file up to disk, clear `__pycache__`); every mutant killed or a test added.
- [ ] **Step 2:** Full gates: `uv run pytest tests/ -v`, `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`, `uv run mypy`, `uv run python scripts/rerun_rust_oracle.py`, `cd rust && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --doc -p bmlib`.
- [ ] **Step 3:** Commit `docs: the vector store in the manuals, CHANGELOG, ROADMAP, HANDOVER and CLAUDE.md`.
