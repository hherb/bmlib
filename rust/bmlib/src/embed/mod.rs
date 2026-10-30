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

//! In-process text embedding: the pinned model files, the pure vector rules, a
//! verified download and the `Embedder` (feature `embed`).
//!
//! A port of `bmlib/embed` (Python). The vector rules are held to the shared
//! `tests/fixtures/embedding_contract.json`; the model constants to
//! `tests/fixtures/embedding_model_manifest.json`.

#[cfg(feature = "embed")]
mod embedder;
mod model;
mod vectors;

#[cfg(feature = "embed")]
pub use embedder::{EmbedError, Embedder};

#[cfg(all(feature = "embed", feature = "http-ureq"))]
pub use model::{download_model, download_model_from};
#[cfg(feature = "embed")]
pub use model::{verify_model, ModelError};
pub use model::{ModelFile, ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0};
pub use vectors::{
    check_texts, query_text, truncate_and_normalize, EmptyText, VectorError, EMBEDDING_DIM,
    EMBEDDING_RECIPE_VERSION, MAX_TOKENS, QUERY_PREFIX,
};
