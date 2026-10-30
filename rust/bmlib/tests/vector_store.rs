// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! The vector store's database half, a test per Python test in
//! `tests/test_embed_store.py`. Each runs on SQLite and, in a
//! `--features postgres` build with `BMLIB_PG_TESTS=1`, again on a private
//! database on a real PostgreSQL server (`tests/common/live_pg.rs`), as the
//! Python suite runs on both backends. The few that need SQLite's own
//! behaviour are plain `#[test]`s and say why.

mod common;

use bmlib::db::{execute, fetch_scalar, open_memory, open_path, Db, DbError, Dialect, Row, Value};
use bmlib::embed::store::{
    delete_paper, ensure_schema, index_paper, search, BoxError, ChunkEmbedder, LabelField,
    StoreError, INDEX_BATCH_SIZE,
};
use bmlib::embed::{
    query_text, truncate_and_normalize, ModelFile, ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0,
    EMBEDDING_DIM,
};
use bmlib::fulltext::canonical::{
    canonical_from_markdown, canonical_from_plain_text, chunk_canonical, CanonicalText,
    DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS,
};
use sha2::{Digest, Sha256};

/// An embedder that needs no model: a text's vector comes from its sha256.
struct FakeEmbedder {
    model: Option<ModelFile>,
    documents: Vec<String>,
    queries: Vec<String>,
    batches: usize,
    fail_on_batch: Option<usize>,
    query_override: Option<String>,
    query_vector: Option<Vec<f32>>,
}

impl FakeEmbedder {
    fn new(model: Option<ModelFile>) -> Self {
        FakeEmbedder {
            model,
            documents: Vec::new(),
            queries: Vec::new(),
            batches: 0,
            fail_on_batch: None,
            query_override: None,
            query_vector: None,
        }
    }
}

fn f16() -> FakeEmbedder {
    FakeEmbedder::new(Some(ARCTIC_EMBED_L_V2_F16))
}

fn vector_of(text: &str) -> Vec<f32> {
    let digest = Sha256::digest(text.as_bytes()).repeat(8);
    let raw: Vec<f32> = digest[..EMBEDDING_DIM]
        .iter()
        .map(|&b| f32::from(b) - 127.5)
        .collect();
    truncate_and_normalize(&raw).unwrap()
}

impl ChunkEmbedder for FakeEmbedder {
    fn model(&self) -> Option<&ModelFile> {
        self.model.as_ref()
    }

    fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, BoxError> {
        self.batches += 1;
        if Some(self.batches) == self.fail_on_batch {
            return Err("text too long".into());
        }
        self.documents
            .extend(texts.iter().map(|t| (*t).to_string()));
        Ok(texts.iter().map(|t| vector_of(t)).collect())
    }

    fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, BoxError> {
        self.queries.push(query.to_string());
        if let Some(vector) = &self.query_vector {
            return Ok(vector.clone());
        }
        Ok(match &self.query_override {
            Some(text) => vector_of(text),
            None => vector_of(&query_text(query)),
        })
    }
}

fn paper(paragraphs: usize, word: &str) -> CanonicalText {
    let body: Vec<String> = (0..paragraphs)
        .map(|i| {
            format!("Patients given {word} in trial {i} had fewer events than controls. ").repeat(8)
        })
        .collect();
    canonical_from_plain_text(&body.join("\n\n"), "A trial")
}

/// One test per backend: SQLite always, PostgreSQL when the live suite is on.
/// The body gets a connection with the store's schema in place.
macro_rules! store_test {
    ($name:ident, $body:expr) => {
        mod $name {
            #[allow(unused_imports)]
            use super::*;

            #[test]
            fn sqlite() {
                crate::common::on_sqlite(|db| crate::with_schema(db, $body));
            }

            #[cfg(feature = "postgres")]
            #[test]
            fn postgres() {
                crate::common::on_postgres(|db| crate::with_schema(db, $body));
            }
        }
    };
}

fn with_schema(db: &mut dyn Db, body: impl FnOnce(&mut dyn Db)) {
    ensure_schema(db).unwrap();
    body(db);
}

/// A SQLite store, for the tests that need SQLite's own behaviour.
fn db() -> impl Db {
    let mut db = open_memory().unwrap();
    ensure_schema(&mut db).unwrap();
    db
}

fn count(db: &mut dyn Db, key: &str) -> i64 {
    match fetch_scalar(
        db,
        "SELECT COUNT(*) FROM embedded_chunks WHERE paper_key = ?",
        &[Value::from(key)],
    )
    .unwrap()
    {
        Some(Value::Int(n)) => n,
        other => panic!("{other:?}"),
    }
}

fn stale(db: &mut dyn Db, text: &CanonicalText, embedder: &mut FakeEmbedder) -> Vec<LabelField> {
    match search(db, "k", text, "q", embedder, 3) {
        Err(StoreError::Stale(fields)) => fields,
        other => panic!("expected a stale set, got {other:?}"),
    }
}

store_test!(ensure_schema_is_idempotent, |db: &mut dyn Db| {
    ensure_schema(db).unwrap();
});

store_test!(index_then_search, |db: &mut dyn Db| {
    let text = paper(40, "aspirin");
    let n = index_paper(db, "pmid:1", &text, &mut f16(), None).unwrap();
    assert!(n > INDEX_BATCH_SIZE);
    assert_eq!(count(db, "pmid:1"), i64::try_from(n).unwrap());
    let hits = search(db, "pmid:1", &text, "fewer events", &mut f16(), 5).unwrap();
    assert_eq!(hits.len(), 5);
    assert!(hits.windows(2).all(|w| w[0].score >= w[1].score));
    let len = text.text.chars().count();
    assert!(hits
        .iter()
        .all(|h| h.span.start < h.span.end && h.span.end <= len));
});

store_test!(
    the_best_chunk_is_the_one_the_question_matches,
    |db: &mut dyn Db| {
        let text = paper(40, "aspirin");
        let mut embedder = f16();
        index_paper(db, "k", &text, &mut embedder, None).unwrap();
        embedder.query_override = Some(embedder.documents[7].clone());
        let hits = search(db, "k", &text, "anything", &mut embedder, 1).unwrap();
        assert_eq!(hits[0].chunk_index, 7);
    }
);

store_test!(the_question_uses_the_query_path, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    let mut embedder = f16();
    index_paper(db, "k", &text, &mut embedder, None).unwrap();
    search(db, "k", &text, "fewer events", &mut embedder, 3).unwrap();
    assert_eq!(embedder.queries, ["fewer events"]);
    assert!(!embedder.documents.iter().any(|t| t.starts_with("query: ")));
});

store_test!(reindexing_replaces_the_set, |db: &mut dyn Db| {
    index_paper(db, "k", &paper(40, "aspirin"), &mut f16(), None).unwrap();
    let small = paper(1, "aspirin");
    let n = index_paper(db, "k", &small, &mut f16(), None).unwrap();
    assert_eq!(count(db, "k"), i64::try_from(n).unwrap());
    assert_eq!(
        search(db, "k", &small, "q", &mut f16(), 100).unwrap().len(),
        n
    );
});

store_test!(other_papers_are_untouched, |db: &mut dyn Db| {
    index_paper(db, "a", &paper(3, "aspirin"), &mut f16(), None).unwrap();
    let n = index_paper(db, "b", &paper(2, "aspirin"), &mut f16(), None).unwrap();
    index_paper(db, "a", &paper(1, "aspirin"), &mut f16(), None).unwrap();
    delete_paper(db, "a").unwrap();
    assert_eq!(count(db, "b"), i64::try_from(n).unwrap());
});

store_test!(an_empty_text_is_an_empty_set, |db: &mut dyn Db| {
    let empty = canonical_from_plain_text("", "");
    assert_eq!(index_paper(db, "k", &empty, &mut f16(), None).unwrap(), 0);
    let mut embedder = f16();
    assert!(search(db, "k", &empty, "q", &mut embedder, 3)
        .unwrap()
        .is_empty());
    assert!(embedder.queries.is_empty());
});

store_test!(a_never_indexed_key_raises, |db: &mut dyn Db| {
    let result = search(db, "nobody", &paper(1, "aspirin"), "q", &mut f16(), 3);
    assert!(
        matches!(result, Err(StoreError::NotIndexed(_))),
        "{result:?}"
    );
});

store_test!(a_different_model_file_is_stale, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    let mut q8 = FakeEmbedder::new(Some(ARCTIC_EMBED_L_V2_Q8_0));
    index_paper(db, "k", &text, &mut q8, None).unwrap();
    assert_eq!(stale(db, &text, &mut f16()), [LabelField::ModelSha256]);
});

// Rust cannot patch a constant as Python's test does, so the stored row is
// made older instead; the comparison under test is the same.
store_test!(a_new_recipe_is_stale, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    execute(db, "UPDATE embedded_chunk_sets SET recipe_version = 0", &[]).unwrap();
    assert_eq!(stale(db, &text, &mut f16()), [LabelField::RecipeVersion]);
});

store_test!(a_new_chunker_is_stale, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    execute(
        db,
        "UPDATE embedded_chunk_sets SET chunker_version = 0",
        &[],
    )
    .unwrap();
    assert_eq!(stale(db, &text, &mut f16()), [LabelField::ChunkerVersion]);
});

store_test!(another_converter_is_stale, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    let mut other = text.clone();
    other.converter_version = "plain-99".to_string();
    assert_eq!(
        stale(db, &other, &mut f16()),
        [LabelField::ConverterVersion]
    );
});

store_test!(different_text_is_stale, |db: &mut dyn Db| {
    index_paper(db, "k", &paper(2, "aspirin"), &mut f16(), None).unwrap();
    assert_eq!(
        stale(db, &paper(2, "heparin"), &mut f16()),
        [LabelField::TextSha256]
    );
});

store_test!(
    an_embedder_without_identity_is_refused,
    |db: &mut dyn Db| {
        let text = paper(2, "aspirin");
        let mut anonymous = FakeEmbedder::new(None);
        let result = index_paper(db, "k", &text, &mut anonymous, None);
        assert!(
            matches!(result, Err(StoreError::UnidentifiedEmbedder)),
            "{result:?}"
        );
        assert!(anonymous.documents.is_empty());
        assert_eq!(count(db, "k"), 0);
        index_paper(db, "k", &text, &mut f16(), None).unwrap();
        let result = search(db, "k", &text, "q", &mut anonymous, 3);
        assert!(
            matches!(result, Err(StoreError::UnidentifiedEmbedder)),
            "{result:?}"
        );
        assert!(anonymous.queries.is_empty());
    }
);

store_test!(an_embedder_failure_keeps_the_old_set, |db: &mut dyn Db| {
    let old = paper(3, "aspirin");
    let n = index_paper(db, "k", &old, &mut f16(), None).unwrap();
    let mut failing = f16();
    failing.fail_on_batch = Some(2);
    let result = index_paper(db, "k", &paper(40, "heparin"), &mut failing, None);
    assert!(matches!(result, Err(StoreError::Embed(_))), "{result:?}");
    assert_eq!(count(db, "k"), i64::try_from(n).unwrap());
    assert!(!search(db, "k", &old, "q", &mut f16(), 1)
        .unwrap()
        .is_empty());
});

store_test!(a_cancel_keeps_the_old_set, |db: &mut dyn Db| {
    let n = index_paper(db, "k", &paper(3, "aspirin"), &mut f16(), None).unwrap();
    let mut cancel = |_: usize, _: usize| false;
    let result = index_paper(
        db,
        "k",
        &paper(40, "heparin"),
        &mut f16(),
        Some(&mut cancel),
    );
    assert!(matches!(result, Err(StoreError::Cancelled)), "{result:?}");
    assert_eq!(count(db, "k"), i64::try_from(n).unwrap());
});

store_test!(progress_reaches_the_total, |db: &mut dyn Db| {
    let mut seen: Vec<(usize, usize)> = Vec::new();
    let mut record = |done: usize, total: usize| {
        seen.push((done, total));
        true
    };
    let n = index_paper(
        db,
        "k",
        &paper(40, "aspirin"),
        &mut f16(),
        Some(&mut record),
    )
    .unwrap();
    assert_eq!(seen.first(), Some(&(INDEX_BATCH_SIZE, n)));
    assert_eq!(seen.last(), Some(&(n, n)));
    assert!(seen.windows(2).all(|w| w[0].0 < w[1].0));
});

/// Index a paper, run `sql` against the store, and expect the search refused.
fn corrupt_after(db: &mut dyn Db, sql: &str, value: Value) {
    let text = paper(3, "aspirin");
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    execute(db, sql, &[value]).unwrap();
    let result = search(db, "k", &text, "q", &mut f16(), 3);
    assert!(matches!(result, Err(StoreError::Corrupt(_))), "{result:?}");
}

store_test!(a_set_missing_rows_is_corrupt, |db: &mut dyn Db| {
    corrupt_after(
        db,
        "DELETE FROM embedded_chunks WHERE chunk_index = ?",
        Value::from(1_i64),
    );
});

store_test!(a_bad_blob_is_corrupt, |db: &mut dyn Db| {
    corrupt_after(
        db,
        "UPDATE embedded_chunks SET embedding = ?",
        Value::from(vec![0_u8; 1023]),
    );
});

store_test!(a_span_outside_the_text_is_corrupt, |db: &mut dyn Db| {
    corrupt_after(
        db,
        "UPDATE embedded_chunks SET span_end = ?",
        Value::from(1_000_000_i64),
    );
});

store_test!(delete_paper_removes_the_set, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    delete_paper(db, "k").unwrap();
    delete_paper(db, "k").unwrap(); // an absent key is not an error
    assert_eq!(count(db, "k"), 0);
    let result = search(db, "k", &text, "q", &mut f16(), 3);
    assert!(
        matches!(result, Err(StoreError::NotIndexed(_))),
        "{result:?}"
    );
});

// SQLite only: PostgreSQL cannot turn its foreign keys off per connection.
#[test]
fn delete_needs_no_cascade() {
    let mut db = db();
    execute(&mut db, "PRAGMA foreign_keys = OFF", &[]).unwrap();
    index_paper(&mut db, "k", &paper(3, "aspirin"), &mut f16(), None).unwrap();
    let n = index_paper(&mut db, "k", &paper(1, "aspirin"), &mut f16(), None).unwrap();
    assert_eq!(count(&mut db, "k"), i64::try_from(n).unwrap());
    delete_paper(&mut db, "k").unwrap();
    assert_eq!(count(&mut db, "k"), 0);
}

store_test!(bad_keys_and_k_are_refused, |db: &mut dyn Db| {
    let text = paper(1, "aspirin");
    let result = index_paper(db, "", &text, &mut f16(), None);
    assert!(
        matches!(result, Err(StoreError::InvalidArgument(_))),
        "{result:?}"
    );
    let result = search(db, "", &text, "q", &mut f16(), 3);
    assert!(
        matches!(result, Err(StoreError::InvalidArgument(_))),
        "{result:?}"
    );
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    let result = search(db, "k", &text, "q", &mut f16(), 0);
    assert!(
        matches!(result, Err(StoreError::InvalidArgument(_))),
        "{result:?}"
    );
});

store_test!(a_hit_carries_its_section, |db: &mut dyn Db| {
    let text = paper(2, "aspirin");
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    for hit in search(db, "k", &text, "q", &mut f16(), 10).unwrap() {
        let blocks = &text.sections[hit.section_index].blocks;
        assert!(blocks[0].span.start <= hit.span.start);
        assert!(hit.span.end <= blocks.last().unwrap().span.end);
    }
});

store_test!(a_stale_error_names_every_field, |db: &mut dyn Db| {
    index_paper(db, "k", &paper(2, "aspirin"), &mut f16(), None).unwrap();
    let mut q8 = FakeEmbedder::new(Some(ARCTIC_EMBED_L_V2_Q8_0));
    let fields = stale(db, &paper(2, "heparin"), &mut q8);
    assert_eq!(fields, [LabelField::ModelSha256, LabelField::TextSha256]);
    let message = StoreError::Stale(fields).to_string();
    assert!(message.contains("model_sha256, text_sha256"), "{message}");
});

/// Another model: different vectors for the same texts.
struct SaltedEmbedder(FakeEmbedder);

impl ChunkEmbedder for SaltedEmbedder {
    fn model(&self) -> Option<&ModelFile> {
        self.0.model()
    }
    fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, BoxError> {
        Ok(texts
            .iter()
            .map(|t| vector_of(&format!("salt{t}")))
            .collect())
    }
    fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, BoxError> {
        self.0.embed_query(query)
    }
}

/// A connection that, after its first query, lets another process re-index
/// the paper with another model.
struct Racing<'a> {
    inner: &'a mut dyn Db,
    racer: Option<Box<dyn FnOnce() + 'a>>,
}

impl Db for Racing<'_> {
    fn dialect(&self) -> Dialect {
        self.inner.dialect()
    }
    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> bmlib::db::Result<u64> {
        self.inner.execute_raw(sql, params)
    }
    fn query_raw(&mut self, sql: &str, params: &[Value]) -> bmlib::db::Result<Vec<Row>> {
        let rows = self.inner.query_raw(sql, params);
        if let Some(race) = self.racer.take() {
            race();
        }
        rows
    }
    fn last_insert_rowid(&self) -> Option<i64> {
        self.inner.last_insert_rowid()
    }
    fn begin(&mut self) -> bmlib::db::Result<Box<dyn Db + '_>> {
        self.inner.begin()
    }
    fn commit(self: Box<Self>) -> bmlib::db::Result<()> {
        Err(DbError::Backend("not a transaction".to_string()))
    }
    fn rollback(self: Box<Self>) -> bmlib::db::Result<()> {
        Err(DbError::Backend("not a transaction".to_string()))
    }
    fn owns_commit(&self) -> bool {
        self.inner.owns_commit()
    }
}

// Review finding: the label and the rows were two statements, so a re-index
// between them had another model's vectors scored as this one's. SQLite
// only: the race needs two connections to one file.
#[test]
fn a_search_racing_a_reindex_scores_one_snapshot() {
    let dir = std::env::temp_dir().join(format!("bmlib-race-{}", std::process::id()));
    let path = dir.join("store.db");
    let mut searching = open_path(&path, true, true).unwrap();
    ensure_schema(&mut searching).unwrap();
    let text = paper(3, "aspirin");
    let q8 = || FakeEmbedder::new(Some(ARCTIC_EMBED_L_V2_Q8_0));
    index_paper(&mut searching, "k", &text, &mut q8(), None).unwrap();
    let expected = search(&mut searching, "k", &text, "q", &mut q8(), 5).unwrap();
    let racing_text = text.clone();
    let racing_path = path.clone();
    let mut wrapped = Racing {
        inner: &mut searching,
        racer: Some(Box::new(move || {
            let mut racing = open_path(&racing_path, true, true).unwrap();
            let mut f16 = SaltedEmbedder(FakeEmbedder::new(Some(ARCTIC_EMBED_L_V2_F16)));
            index_paper(&mut racing, "k", &racing_text, &mut f16, None).unwrap();
        })),
    };
    let raced = search(&mut wrapped, "k", &text, "q", &mut q8(), 5).unwrap();
    let ran = wrapped.racer.is_none();
    drop(wrapped);
    assert!(ran, "the race never ran");
    assert_eq!(raced, expected);
    let next = search(&mut searching, "k", &text, "q", &mut q8(), 5);
    assert!(matches!(next, Err(StoreError::Stale(_))), "{next:?}");
    drop(searching);
    std::fs::remove_dir_all(&dir).ok();
}

store_test!(a_bad_query_vector_is_refused, |db: &mut dyn Db| {
    let good = vector_of("q");
    let mut nan = good.clone();
    nan[0] = f32::NAN;
    let queries = [
        good[..128].to_vec(),
        good.repeat(4),
        nan,
        good.iter().map(|v| v * 0.5).collect(),
    ];
    for query in queries {
        let text = paper(2, "aspirin");
        index_paper(db, "k", &text, &mut f16(), None).unwrap();
        let mut embedder = f16();
        embedder.query_vector = Some(query.clone());
        let result = search(db, "k", &text, "q", &mut embedder, 3);
        assert!(
            matches!(result, Err(StoreError::Vector(_))),
            "{} values: {result:?}",
            query.len()
        );
    }
});

store_test!(
    a_set_recording_no_chunks_beside_rows_is_corrupt,
    |db: &mut dyn Db| {
        corrupt_after(
            db,
            "UPDATE embedded_chunk_sets SET chunk_count = ?",
            Value::from(0_i64),
        );
    }
);

store_test!(a_negative_stored_version_is_corrupt, |db: &mut dyn Db| {
    corrupt_after(
        db,
        "UPDATE embedded_chunk_sets SET recipe_version = ?",
        Value::from(-1_i64),
    );
});

// SQLite only: PostgreSQL's INTEGER cannot hold it, so the case cannot arise there.
#[test]
fn a_stored_version_past_u32_is_corrupt() {
    corrupt_after(
        &mut db(),
        "UPDATE embedded_chunk_sets SET recipe_version = ?",
        Value::from(1_i64 << 32),
    );
}

// --- PR #586's review --------------------------------------------------------

// SQLite only: its BLOB column takes any type, PostgreSQL's BYTEA does not.
#[test]
fn a_stored_vector_that_is_not_a_blob_is_corrupt() {
    corrupt_after(
        &mut db(),
        "UPDATE embedded_chunks SET embedding = ? WHERE chunk_index = 0",
        Value::from(1024_i64),
    );
}

store_test!(
    a_stored_vector_not_of_unit_length_is_corrupt,
    |db: &mut dyn Db| {
        // Unchecked, the huge one ranked its chunk first at a score of ~1e28.
        let mut huge = vec![0_u8; 4 * EMBEDDING_DIM];
        huge[..4].copy_from_slice(&1e30_f32.to_le_bytes());
        for blob in [vec![0_u8; 4 * EMBEDDING_DIM], huge] {
            corrupt_after(
                db,
                "UPDATE embedded_chunks SET embedding = ? WHERE chunk_index = 0",
                Value::from(blob),
            );
        }
    }
);

// The largest count PostgreSQL's INTEGER holds; collected, it would be 32 GB.
store_test!(
    a_huge_chunk_count_is_corrupt_without_collecting_it,
    |db: &mut dyn Db| {
        corrupt_after(
            db,
            "UPDATE embedded_chunk_sets SET chunk_count = ?",
            Value::from(i64::from(i32::MAX)),
        );
    }
);

store_test!(
    a_set_whose_rows_all_vanished_is_corrupt,
    |db: &mut dyn Db| {
        corrupt_after(
            db,
            "DELETE FROM embedded_chunks WHERE paper_key = ?",
            Value::from("k"),
        );
    }
);

store_test!(
    an_empty_set_recording_a_negative_count_is_corrupt,
    |db: &mut dyn Db| {
        let empty = canonical_from_plain_text("", "");
        index_paper(db, "k", &empty, &mut f16(), None).unwrap();
        execute(
            db,
            "UPDATE embedded_chunk_sets SET chunk_count = ?",
            &[Value::from(-1_i64)],
        )
        .unwrap();
        let result = search(db, "k", &empty, "q", &mut f16(), 3);
        assert!(matches!(result, Err(StoreError::Corrupt(_))), "{result:?}");
    }
);

store_test!(
    a_span_that_cannot_be_a_chunk_is_corrupt,
    |db: &mut dyn Db| {
        corrupt_after(
            db,
            "UPDATE embedded_chunks SET section_index = ? WHERE chunk_index = 0",
            Value::from(99_i64),
        );
        corrupt_after(
            db,
            "UPDATE embedded_chunks SET span_start = span_end WHERE chunk_index = ?",
            Value::from(0_i64),
        );
    }
);

// --- #587: a stored span is the chunker's, or the set is corrupt ---------------

fn two_sections() -> CanonicalText {
    let body: Vec<String> = (0..3)
        .map(|i| format!("Patients given aspirin in trial {i} had fewer events. ").repeat(12))
        .collect();
    let body = body.join("\n\n");
    canonical_from_markdown(&format!("# Methods\n\n{body}\n\n# Results\n\n{body}"))
}

store_test!(
    a_span_in_bounds_but_not_the_chunkers_is_corrupt,
    |db: &mut dyn Db| {
        // Checked against the text's bounds alone, the last chunk came back as (0, 3).
        let text = two_sections();
        let chunks = chunk_canonical(&text, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS).unwrap();
        let last = chunks.len() - 1;
        assert_eq!(chunks[last].section_index, 1);
        for assignment in [
            "span_start = 0, span_end = 3",
            "section_index = 0",
            "span_start = span_start + 1",
        ] {
            index_paper(db, "k", &text, &mut f16(), None).unwrap();
            execute(
                db,
                &format!("UPDATE embedded_chunks SET {assignment} WHERE chunk_index = ?"),
                &[Value::from(i64::try_from(last).unwrap())],
            )
            .unwrap();
            match search(db, "k", &text, "q", &mut f16(), 3) {
                Err(StoreError::Corrupt(why)) => assert!(
                    why.starts_with(&format!("stored chunk {last} is not the one")),
                    "{assignment}: {why}"
                ),
                other => panic!("{assignment}: {other:?}"),
            }
        }
    }
);

store_test!(
    a_set_emptied_of_rows_and_count_is_corrupt,
    |db: &mut dyn Db| {
        // Count and rows agreed at zero, so a text with chunks searched as none.
        let text = paper(3, "aspirin");
        index_paper(db, "k", &text, &mut f16(), None).unwrap();
        execute(db, "DELETE FROM embedded_chunks", &[]).unwrap();
        execute(db, "UPDATE embedded_chunk_sets SET chunk_count = 0", &[]).unwrap();
        match search(db, "k", &text, "q", &mut f16(), 3) {
            Err(StoreError::Corrupt(why)) => assert!(why.contains("records 0 chunks"), "{why}"),
            other => panic!("{other:?}"),
        }
    }
);

store_test!(every_hit_is_a_chunk_the_chunker_makes, |db: &mut dyn Db| {
    let text = two_sections();
    let chunks = chunk_canonical(&text, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS).unwrap();
    index_paper(db, "k", &text, &mut f16(), None).unwrap();
    let mut hits: Vec<_> = search(db, "k", &text, "aspirin", &mut f16(), chunks.len())
        .unwrap()
        .into_iter()
        .map(|h| (h.chunk_index, h.section_index, h.span))
        .collect();
    hits.sort_by_key(|&(index, _, _)| index);
    let expected: Vec<_> = chunks
        .iter()
        .enumerate()
        .map(|(i, c)| (i, c.section_index, c.span))
        .collect();
    assert_eq!(hits, expected);
});

/// What an embedder hands back for a batch of texts.
type Batch = fn(&[&str]) -> Vec<Vec<f32>>;

/// An embedder whose second batch is `second(texts)`.
struct Misbehaving {
    inner: FakeEmbedder,
    second: Batch,
}

impl ChunkEmbedder for Misbehaving {
    fn model(&self) -> Option<&ModelFile> {
        self.inner.model()
    }
    fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, BoxError> {
        let good = self.inner.embed_documents(texts)?;
        Ok(if self.inner.batches == 2 {
            (self.second)(texts)
        } else {
            good
        })
    }
    fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, BoxError> {
        self.inner.embed_query(query)
    }
}

fn vectors_of(texts: &[&str]) -> Vec<Vec<f32>> {
    texts.iter().map(|t| vector_of(t)).collect()
}

store_test!(
    bad_embedder_output_is_refused_and_keeps_the_old_set,
    |db: &mut dyn Db| {
        let cases: [Batch; 4] = [
            |texts| {
                let mut v = vectors_of(texts);
                v.push(vector_of("x"));
                v
            },
            |texts| {
                let mut v = vectors_of(texts);
                v.pop();
                v
            },
            |texts| {
                vectors_of(texts)
                    .into_iter()
                    .map(|v| v.iter().map(|x| x * 0.5).collect())
                    .collect()
            },
            |texts| vec![vec![f32::NAN; EMBEDDING_DIM]; texts.len()],
        ];
        for (i, second) in cases.into_iter().enumerate() {
            let old = paper(3, "aspirin");
            let n = index_paper(db, "k", &old, &mut f16(), None).unwrap();
            let expected = search(db, "k", &old, "q", &mut f16(), 3).unwrap();
            let mut embedder = Misbehaving {
                inner: f16(),
                second,
            };
            let result = index_paper(db, "k", &paper(40, "heparin"), &mut embedder, None);
            assert!(
                matches!(result, Err(StoreError::Vector(_))),
                "case {i}: {result:?}"
            );
            assert_eq!(count(db, "k"), i64::try_from(n).unwrap(), "case {i}");
            assert_eq!(search(db, "k", &old, "q", &mut f16(), 3).unwrap(), expected);
        }
    }
);

// SQLite only: the failure is a trigger calling SQLite's RAISE.
#[test]
fn a_failure_inside_the_write_keeps_the_old_set() {
    let mut db = db();
    let old = paper(3, "aspirin");
    let n = index_paper(&mut db, "k", &old, &mut f16(), None).unwrap();
    let expected = search(&mut db, "k", &old, "q", &mut f16(), 3).unwrap();
    // After the old set's delete and the new set's row: only a transaction undoes both.
    execute(
        &mut db,
        "CREATE TRIGGER fail BEFORE INSERT ON embedded_chunks \
         BEGIN SELECT RAISE(ABORT, 'the disk filled'); END",
        &[],
    )
    .unwrap();
    let result = index_paper(&mut db, "k", &paper(40, "heparin"), &mut f16(), None);
    assert!(matches!(result, Err(StoreError::Db(_))), "{result:?}");
    execute(&mut db, "DROP TRIGGER fail", &[]).unwrap();
    assert_eq!(count(&mut db, "k"), i64::try_from(n).unwrap());
    assert_eq!(
        search(&mut db, "k", &old, "q", &mut f16(), 3).unwrap(),
        expected
    );
}
