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

//! A paper's chunks and their embeddings, stored and searched (both backends).
//!
//! A port of `bmlib/embed/store.py` (StudyAnalyzer's item 8). [`index_paper`]
//! chunks a canonical text, embeds every chunk and stores the set under the
//! caller's key with a label of how it was made; [`search`] embeds a question
//! and returns the best chunks by brute-force cosine, refusing a set whose
//! label is not the current one. The pure half — [`encode_vector`],
//! [`decode_vector`], [`score`], [`rank`], [`ChunkSetLabel`] — is held to
//! Python by `tests/fixtures/vector_store.json`.
//!
//! A vector is stored as [`EMBEDDING_DIM`] little-endian IEEE-754 binary32
//! values (the layout sqlite-vec reads). A score is the dot product of
//! binary32 values, each widened to binary64 and summed left to right from
//! `0.0`, so both languages rank identically.
//!
//! Generic over [`Db`], so it needs neither backend feature nor `embed`; the
//! embedder is reached through [`ChunkEmbedder`].

use std::cmp::Ordering;
use std::error::Error;
use std::fmt;

use sha2::{Digest, Sha256};

use crate::db::{
    create_tables, execute, executemany, fetch_all, transaction_with, Db, DbError, Dialect, Row,
    Value,
};
use crate::fulltext::canonical::{
    chunk_canonical, CanonicalText, Chunk, TextSpan, CHUNKER_VERSION, DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
};

use super::model::ModelFile;
use super::vectors::{EMBEDDING_DIM, EMBEDDING_RECIPE_VERSION};

/// A stored vector's norm, after rounding to binary32, may miss 1 by this much.
pub const NORM_TOLERANCE: f64 = 1e-4;
/// Chunks embedded per call, and per progress report.
pub const INDEX_BATCH_SIZE: usize = 16;
/// The bytes of one stored vector.
pub const VECTOR_BYTES: usize = 4 * EMBEDDING_DIM;

/// An embedder's own error, carried through [`StoreError::Embed`].
pub type BoxError = Box<dyn Error + Send + Sync>;

/// What the store needs of an embedder; `bmlib::embed::Embedder` is one
/// (feature `embed`; not a link, since the item is absent without it).
pub trait ChunkEmbedder {
    /// The model file the vectors come from; `None` is refused.
    fn model(&self) -> Option<&ModelFile>;
    /// Embed texts as documents, in order.
    ///
    /// # Errors
    ///
    /// The embedder's own.
    fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, BoxError>;
    /// Embed a search query, with the model's query prompt.
    ///
    /// # Errors
    ///
    /// The embedder's own.
    fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, BoxError>;
}

#[cfg(feature = "embed")]
impl ChunkEmbedder for super::Embedder {
    fn model(&self) -> Option<&ModelFile> {
        super::Embedder::model(self)
    }

    fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, BoxError> {
        super::Embedder::embed_documents(self, texts).map_err(Into::into)
    }

    fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, BoxError> {
        super::Embedder::embed_query(self, query).map_err(Into::into)
    }
}

/// One field of a [`ChunkSetLabel`], in label order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LabelField {
    /// The model file's sha256.
    ModelSha256,
    /// [`EMBEDDING_RECIPE_VERSION`].
    RecipeVersion,
    /// [`CHUNKER_VERSION`].
    ChunkerVersion,
    /// The canonical text's converter version.
    ConverterVersion,
    /// The sha256 of the canonical text, as UTF-8.
    TextSha256,
}

impl LabelField {
    /// Every field, in label order.
    pub const ALL: [LabelField; 5] = [
        LabelField::ModelSha256,
        LabelField::RecipeVersion,
        LabelField::ChunkerVersion,
        LabelField::ConverterVersion,
        LabelField::TextSha256,
    ];

    /// The field's wire name, as Python's `LABEL_FIELDS` spells it.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            LabelField::ModelSha256 => "model_sha256",
            LabelField::RecipeVersion => "recipe_version",
            LabelField::ChunkerVersion => "chunker_version",
            LabelField::ConverterVersion => "converter_version",
            LabelField::TextSha256 => "text_sha256",
        }
    }
}

/// How a stored set was made; a set is current only if every field matches.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkSetLabel {
    /// The model file's sha256.
    pub model_sha256: String,
    /// [`EMBEDDING_RECIPE_VERSION`] when it was made.
    pub recipe_version: u32,
    /// [`CHUNKER_VERSION`] when it was made.
    pub chunker_version: u32,
    /// The canonical text's converter version.
    pub converter_version: String,
    /// The sha256 of the canonical text, as UTF-8, lower-case hex.
    pub text_sha256: String,
}

impl ChunkSetLabel {
    /// The fields that differ, in label order.
    #[must_use]
    pub fn differences(&self, other: &ChunkSetLabel) -> Vec<LabelField> {
        LabelField::ALL
            .into_iter()
            .filter(|field| match field {
                LabelField::ModelSha256 => self.model_sha256 != other.model_sha256,
                LabelField::RecipeVersion => self.recipe_version != other.recipe_version,
                LabelField::ChunkerVersion => self.chunker_version != other.chunker_version,
                LabelField::ConverterVersion => self.converter_version != other.converter_version,
                LabelField::TextSha256 => self.text_sha256 != other.text_sha256,
            })
            .collect()
    }
}

/// One search result: which chunk, its section, where it lies, its score.
#[derive(Debug, Clone, PartialEq)]
pub struct ChunkHit {
    /// The chunk's index in the stored set (0-based, document order).
    pub chunk_index: usize,
    /// Index into [`CanonicalText::sections`].
    pub section_index: usize,
    /// Where the chunk lies in the canonical text, in code points.
    pub span: TextSpan,
    /// The cosine between the question and the chunk.
    pub score: f64,
}

/// Why the store could not do what was asked; one variant per Python class.
#[derive(Debug)]
#[non_exhaustive]
pub enum StoreError {
    /// The database failed.
    Db(DbError),
    /// No set is stored under the key (`NotIndexedError`).
    NotIndexed(String),
    /// The stored label differs from the current one (`StaleChunkSetError`):
    /// every differing field, in label order. Index the paper again.
    Stale(Vec<LabelField>),
    /// A stored row cannot be read as part of the set (`CorruptChunkSetError`).
    Corrupt(String),
    /// The embedder carries no model identity (`UnidentifiedEmbedderError`);
    /// build it with `Embedder::load_model`.
    UnidentifiedEmbedder,
    /// The progress callback cancelled indexing; nothing was stored
    /// (`IndexingCancelled`).
    Cancelled,
    /// A vector cannot be stored (`VectorError`).
    Vector(String),
    /// The embedder failed (its own error, as Python lets it through).
    Embed(BoxError),
    /// A bad key or `k` (`ValueError`).
    InvalidArgument(String),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::Db(e) => write!(f, "database error: {e}"),
            StoreError::NotIndexed(key) => write!(f, "no chunk set is stored for {key:?}"),
            StoreError::Stale(fields) => {
                let names: Vec<&str> = fields.iter().map(|f| f.as_str()).collect();
                write!(
                    f,
                    "the stored chunk set is stale ({} changed); index it again",
                    names.join(", ")
                )
            }
            StoreError::Corrupt(why)
            | StoreError::Vector(why)
            | StoreError::InvalidArgument(why) => f.write_str(why),
            StoreError::UnidentifiedEmbedder => f.write_str(
                "the embedder carries no model identity; build it with Embedder::load_model",
            ),
            StoreError::Cancelled => f.write_str("indexing was cancelled; nothing was stored"),
            StoreError::Embed(e) => write!(f, "the embedder failed: {e}"),
        }
    }
}

impl Error for StoreError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            StoreError::Db(e) => Some(e),
            StoreError::Embed(e) => Some(e.as_ref()),
            _ => None,
        }
    }
}

impl From<DbError> for StoreError {
    fn from(e: DbError) -> Self {
        StoreError::Db(e)
    }
}

/// The dot product, each binary32 term widened to binary64, summed left to
/// right from `0.0` (not `Iterator::sum`, which starts at `-0.0`).
#[must_use]
pub fn score(query: &[f32], vector: &[f32]) -> f64 {
    query
        .iter()
        .zip(vector)
        .fold(0.0, |total, (&x, &y)| total + f64::from(x) * f64::from(y))
}

/// A vector's stored form.
///
/// # Errors
///
/// [`StoreError::Vector`] unless it has exactly [`EMBEDDING_DIM`] finite
/// values whose norm is within [`NORM_TOLERANCE`] of 1.
pub fn encode_vector(values: &[f32]) -> Result<Vec<u8>, StoreError> {
    if values.len() != EMBEDDING_DIM {
        return Err(StoreError::Vector(format!(
            "a stored vector needs {EMBEDDING_DIM} values, got {}",
            values.len()
        )));
    }
    let norm = score(values, values).sqrt();
    // A NaN or infinity carries into the norm, and a NaN fails every comparison.
    let within = (norm - 1.0).abs() <= NORM_TOLERANCE;
    if !within {
        return Err(StoreError::Vector(format!(
            "a stored vector must be unit length, its norm is {norm}"
        )));
    }
    Ok(values.iter().flat_map(|v| v.to_le_bytes()).collect())
}

/// A stored vector's values.
///
/// # Errors
///
/// [`StoreError::Corrupt`] unless it is [`VECTOR_BYTES`] bytes of finite
/// values whose norm is within [`NORM_TOLERANCE`] of 1, as [`encode_vector`]
/// wrote it.
pub fn decode_vector(blob: &[u8]) -> Result<Vec<f32>, StoreError> {
    if blob.len() != VECTOR_BYTES {
        return Err(StoreError::Corrupt(format!(
            "a stored vector is {} bytes, not {VECTOR_BYTES}",
            blob.len()
        )));
    }
    let values: Vec<f32> = blob
        .chunks_exact(4)
        .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .collect();
    if !values.iter().all(|v| v.is_finite()) {
        return Err(StoreError::Corrupt(
            "a stored vector holds a value that is not finite".to_string(),
        ));
    }
    // Unchecked, one bad exponent ranked its chunk first on every question.
    let norm = score(&values, &values).sqrt();
    if (norm - 1.0).abs() > NORM_TOLERANCE {
        return Err(StoreError::Corrupt(format!(
            "a stored vector is not unit length, its norm is {norm}"
        )));
    }
    Ok(values)
}

/// The best `k` of `vectors` as `(index, score)`, best first; an exact tie
/// goes to the lower index, and a `k` above the count returns all.
///
/// # Errors
///
/// [`StoreError::InvalidArgument`] if `k` is 0.
pub fn rank(
    query: &[f32],
    vectors: &[Vec<f32>],
    k: usize,
) -> Result<Vec<(usize, f64)>, StoreError> {
    check_k(k)?;
    let mut scored: Vec<(usize, f64)> = vectors
        .iter()
        .enumerate()
        .map(|(index, vector)| (index, score(query, vector)))
        .collect();
    // partial_cmp, not total_cmp: -0.0 and 0.0 tie, as in Python.
    scored.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(Ordering::Equal)
            .then(a.0.cmp(&b.0))
    });
    scored.truncate(k);
    Ok(scored)
}

fn check_k(k: usize) -> Result<(), StoreError> {
    if k == 0 {
        return Err(StoreError::InvalidArgument(
            "k must be a positive integer, got 0".to_string(),
        ));
    }
    Ok(())
}

fn check_key(key: &str) -> Result<(), StoreError> {
    if key.is_empty() {
        return Err(StoreError::InvalidArgument(
            "a paper's key must be a non-empty str, got \"\"".to_string(),
        ));
    }
    Ok(())
}

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS embedded_chunk_sets (
    paper_key TEXT PRIMARY KEY,
    model_sha256 TEXT NOT NULL,
    recipe_version INTEGER NOT NULL,
    chunker_version INTEGER NOT NULL,
    converter_version TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embedded_chunks (
    paper_key TEXT NOT NULL REFERENCES embedded_chunk_sets(paper_key) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    section_index INTEGER NOT NULL,
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    embedding {blob} NOT NULL,
    PRIMARY KEY (paper_key, chunk_index)
);
";

/// Create the store's two tables if they do not exist (SQLite or PostgreSQL).
///
/// # Errors
///
/// [`StoreError::Db`].
pub fn ensure_schema(db: &mut dyn Db) -> Result<(), StoreError> {
    let blob = if db.dialect() == Dialect::Sqlite {
        "BLOB"
    } else {
        "BYTEA"
    };
    create_tables(db, &SCHEMA.replace("{blob}", blob))?;
    Ok(())
}

/// The label a set indexed now from `canonical` would carry.
///
/// # Errors
///
/// [`StoreError::UnidentifiedEmbedder`] if the embedder carries no model.
pub fn current_label(
    canonical: &CanonicalText,
    embedder: &dyn ChunkEmbedder,
) -> Result<ChunkSetLabel, StoreError> {
    let model = embedder.model().ok_or(StoreError::UnidentifiedEmbedder)?;
    let digest = Sha256::digest(canonical.text.as_bytes());
    Ok(ChunkSetLabel {
        model_sha256: model.sha256.to_string(),
        recipe_version: EMBEDDING_RECIPE_VERSION,
        chunker_version: CHUNKER_VERSION,
        converter_version: canonical.converter_version.clone(),
        text_sha256: digest.iter().map(|b| format!("{b:02x}")).collect(),
    })
}

/// Chunk, embed and store `canonical` under `key`, replacing any set there.
///
/// Every chunk is embedded before the write transaction opens, so a failure
/// or a cancel leaves the stored set as it was. `on_progress(done, total)`
/// is called after each batch of [`INDEX_BATCH_SIZE`] chunks; returning
/// `false` cancels. A text with no chunks is stored as a set of none.
/// Returns the number of chunks stored.
///
/// # Errors
///
/// [`StoreError::InvalidArgument`] for an empty key, or if the chunker fails
/// or returns a span outside the text,
/// [`StoreError::UnidentifiedEmbedder`], [`StoreError::Cancelled`],
/// [`StoreError::Embed`], [`StoreError::Vector`] or [`StoreError::Db`].
pub fn index_paper(
    db: &mut dyn Db,
    key: &str,
    canonical: &CanonicalText,
    embedder: &mut dyn ChunkEmbedder,
    mut on_progress: Option<&mut dyn FnMut(usize, usize) -> bool>,
) -> Result<usize, StoreError> {
    check_key(key)?;
    let label = current_label(canonical, embedder)?;
    let chunks = chunk_canonical(canonical, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS)
        .map_err(|e| StoreError::InvalidArgument(e.to_string()))?;
    let mut blobs: Vec<Vec<u8>> = Vec::with_capacity(chunks.len());
    for batch in chunks.chunks(INDEX_BATCH_SIZE) {
        let texts = batch
            .iter()
            .map(|c| {
                canonical.span_text(c.span).ok_or_else(|| {
                    StoreError::InvalidArgument(format!(
                        "a chunk lies outside the text: {:?}",
                        c.span
                    ))
                })
            })
            .collect::<Result<Vec<&str>, StoreError>>()?;
        let vectors = embedder
            .embed_documents(&texts)
            .map_err(StoreError::Embed)?;
        if vectors.len() != texts.len() {
            return Err(StoreError::Vector(format!(
                "the embedder returned {} vectors for {}",
                vectors.len(),
                texts.len()
            )));
        }
        for vector in &vectors {
            blobs.push(encode_vector(vector)?);
        }
        if let Some(callback) = on_progress.as_mut() {
            if !callback(blobs.len(), chunks.len()) {
                return Err(StoreError::Cancelled);
            }
        }
    }
    let rows: Vec<Vec<Value>> = chunks
        .iter()
        .zip(blobs)
        .enumerate()
        .map(|(i, (c, blob))| {
            vec![
                Value::from(key),
                Value::from(i),
                Value::from(c.section_index),
                Value::from(c.span.start),
                Value::from(c.span.end),
                Value::from(blob),
            ]
        })
        .collect();
    let count = chunks.len();
    transaction_with(db, |tx| -> Result<(), StoreError> {
        delete(tx, key)?;
        execute(
            tx,
            "INSERT INTO embedded_chunk_sets (paper_key, model_sha256, recipe_version, \
             chunker_version, converter_version, text_sha256, chunk_count, indexed_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            &[
                Value::from(key),
                Value::from(label.model_sha256.as_str()),
                Value::from(i64::from(label.recipe_version)),
                Value::from(i64::from(label.chunker_version)),
                Value::from(label.converter_version.as_str()),
                Value::from(label.text_sha256.as_str()),
                Value::from(count),
                Value::from(now()),
            ],
        )?;
        executemany(
            tx,
            "INSERT INTO embedded_chunks (paper_key, chunk_index, section_index, span_start, \
             span_end, embedding) VALUES (?, ?, ?, ?, ?, ?)",
            &rows,
        )?;
        Ok(())
    })?;
    Ok(count)
}

/// The `k` chunks of `key`'s set that best match `question`, best first.
///
/// `canonical` is the text the app holds for the paper; the set must have
/// been indexed from exactly it, by the embedder's model, under the current
/// recipe and chunker. An exact tie goes to the lower chunk index.
///
/// # Errors
///
/// [`StoreError::InvalidArgument`] for an empty key, a `k` of 0 or a chunker
/// failure, [`StoreError::UnidentifiedEmbedder`], [`StoreError::NotIndexed`],
/// [`StoreError::Stale`], [`StoreError::Corrupt`] (including a stored chunk
/// other than the one `chunk_canonical` makes of `canonical`),
/// [`StoreError::Vector`] (the question's vector would not be stored),
/// [`StoreError::Embed`] or [`StoreError::Db`].
pub fn search(
    db: &mut dyn Db,
    key: &str,
    canonical: &CanonicalText,
    question: &str,
    embedder: &mut dyn ChunkEmbedder,
    k: usize,
) -> Result<Vec<ChunkHit>, StoreError> {
    check_key(key)?;
    check_k(k)?;
    let label = current_label(canonical, embedder)?;
    // One statement, so the label and the rows come from one snapshot: read
    // apart, a re-index between the two had another model's vectors scored.
    let rows = fetch_all(
        db,
        "SELECT s.model_sha256, s.recipe_version, s.chunker_version, s.converter_version, \
         s.text_sha256, s.chunk_count, c.chunk_index, c.section_index, c.span_start, \
         c.span_end, c.embedding FROM embedded_chunk_sets s \
         LEFT JOIN embedded_chunks c ON c.paper_key = s.paper_key \
         WHERE s.paper_key = ? ORDER BY c.chunk_index",
        &[Value::from(key)],
    )?;
    let first = rows
        .first()
        .ok_or_else(|| StoreError::NotIndexed(key.to_string()))?;
    let stored = stored_label(first).map_err(|e| {
        StoreError::Corrupt(format!("the stored label for {key:?} is unreadable: {e}"))
    })?;
    let differing = stored.differences(&label);
    if !differing.is_empty() {
        return Err(StoreError::Stale(differing));
    }
    let count = first
        .get_i64("chunk_count")
        .map_err(|e| StoreError::Corrupt(e.to_string()))?;
    let rows: Vec<&Row> = rows
        .iter()
        .filter(|r| r.get("chunk_index").is_ok_and(|v| !v.is_null()))
        .collect();
    let indices: Vec<Option<i64>> = rows.iter().map(|r| r.get_i64("chunk_index").ok()).collect();
    // The length first: a corrupt count of 10^12 must not be collected.
    if usize::try_from(count) != Ok(rows.len())
        || indices != (0..count).map(Some).collect::<Vec<_>>()
    {
        return Err(StoreError::Corrupt(format!(
            "the set for {key:?} records {count} chunks, not its rows"
        )));
    }
    // The label pins the text's bytes, the chunker and the converter's version
    // (held to its code by test_renderer_version.py), not the sections, so a
    // hand-built text sectioned otherwise lands here as corrupt too. Checked
    // against bounds alone, a stored span rewritten in bounds was returned.
    let expected = chunk_canonical(canonical, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS)
        .map_err(|e| StoreError::InvalidArgument(e.to_string()))?;
    if expected.len() != rows.len() {
        return Err(StoreError::Corrupt(format!(
            "the set for {key:?} records {count} chunks, the text makes {}",
            expected.len()
        )));
    }
    for (index, (&r, chunk)) in rows.iter().zip(&expected).enumerate() {
        check_span(index, r, chunk)?;
    }
    if rows.is_empty() {
        return Ok(Vec::new());
    }
    let mut vectors = Vec::with_capacity(rows.len());
    for &r in &rows {
        let blob = r
            .get("embedding")
            .ok()
            .and_then(Value::as_blob)
            .ok_or_else(|| StoreError::Corrupt("a stored vector is not a blob".to_string()))?;
        vectors.push(decode_vector(blob)?);
    }
    let query = embedder.embed_query(question).map_err(StoreError::Embed)?;
    encode_vector(&query)?; // held to the stored vectors' rules
    Ok(rank(&query, &vectors, k)?
        .into_iter()
        .map(|(index, value)| ChunkHit {
            chunk_index: index,
            section_index: expected[index].section_index,
            span: expected[index].span,
            score: value,
        })
        .collect())
}

/// Remove `key`'s set; an absent key is not an error.
///
/// # Errors
///
/// [`StoreError::InvalidArgument`] for an empty key, or [`StoreError::Db`].
pub fn delete_paper(db: &mut dyn Db, key: &str) -> Result<(), StoreError> {
    check_key(key)?;
    transaction_with(db, |tx| -> Result<(), StoreError> { delete(tx, key) })
}

// Explicit, not by cascade: a connection may have foreign keys off.
fn delete(db: &mut dyn Db, key: &str) -> Result<(), StoreError> {
    execute(
        db,
        "DELETE FROM embedded_chunks WHERE paper_key = ?",
        &[Value::from(key)],
    )?;
    execute(
        db,
        "DELETE FROM embedded_chunk_sets WHERE paper_key = ?",
        &[Value::from(key)],
    )?;
    Ok(())
}

fn stored_label(row: &Row) -> Result<ChunkSetLabel, String> {
    let text = |name: &str| {
        row.get_str(name)
            .map(str::to_string)
            .map_err(|e| e.to_string())
    };
    let version = |name: &str| {
        row.get_i64(name)
            .map_err(|e| e.to_string())
            .and_then(|v| u32::try_from(v).map_err(|_| format!("{name} is {v}")))
    };
    Ok(ChunkSetLabel {
        model_sha256: text("model_sha256")?,
        recipe_version: version("recipe_version")?,
        chunker_version: version("chunker_version")?,
        converter_version: text("converter_version")?,
        text_sha256: text("text_sha256")?,
    })
}

fn check_span(index: usize, row: &Row, chunk: &Chunk) -> Result<(), StoreError> {
    let field = |name: &str| row.get_i64(name).ok().and_then(|v| usize::try_from(v).ok());
    let stored = (
        field("section_index"),
        field("span_start"),
        field("span_end"),
    );
    if stored
        == (
            Some(chunk.section_index),
            Some(chunk.span.start),
            Some(chunk.span.end),
        )
    {
        Ok(())
    } else {
        Err(StoreError::Corrupt(format!(
            "stored chunk {index} is not the one the chunker makes of this text: {stored:?}"
        )))
    }
}

fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false)
}
