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

//! The embedding model files and a verified download.
//!
//! A file is never visible at its final path unless its size and sha256
//! matched.

/// A model file pinned to a repository revision, size and checksum.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModelFile {
    /// Hugging Face repository.
    pub repo: &'static str,
    /// Commit the file is pinned to.
    pub revision: &'static str,
    /// File name within the repository.
    pub file: &'static str,
    /// Exact size in bytes.
    pub size: u64,
    /// Lower-case hex sha256.
    pub sha256: &'static str,
}

impl ModelFile {
    /// The revision-pinned download URL.
    #[must_use]
    pub fn url(&self) -> String {
        format!(
            "https://huggingface.co/{}/resolve/{}/{}",
            self.repo, self.revision, self.file
        )
    }
}

const REPO: &str = "hherb/snowflake-arctic-embed-l-v2.0-gguf";
const REVISION: &str = "c0a1ca8894e853259b9e2aeb1a873f6d50ae5701";

/// Snowflake Arctic Embed L v2.0, half precision.
pub const ARCTIC_EMBED_L_V2_F16: ModelFile = ModelFile {
    repo: REPO,
    revision: REVISION,
    file: "snowflake-arctic-embed-l-v2.0-F16.gguf",
    size: 1_157_672_160,
    sha256: "19227acecdaead5b4272216bda90c9025fa25c243f2abffe755cf89c7fc1b719",
};

/// Snowflake Arctic Embed L v2.0, 8-bit quantised.
pub const ARCTIC_EMBED_L_V2_Q8_0: ModelFile = ModelFile {
    repo: REPO,
    revision: REVISION,
    file: "snowflake-arctic-embed-l-v2.0-Q8_0.gguf",
    size: 634_554_720,
    sha256: "363d89f6c2476bd5dcb010780884ab19ba3ba19b17aed0953a53877524323b88",
};

#[cfg(feature = "embed")]
mod verified {
    use super::ModelFile;
    use sha2::{Digest, Sha256};
    use std::fmt;
    use std::fs::File;
    use std::io::Read;
    use std::path::Path;

    /// Read and hash block size.
    pub(super) const BLOCK_SIZE: usize = 1024 * 1024;

    /// Why a model file could not be verified or downloaded.
    #[derive(Debug)]
    pub enum ModelError {
        /// A file-system failure.
        Io(std::io::Error),
        /// The size is not the pinned one.
        SizeMismatch {
            /// Pinned size.
            expected: u64,
            /// Size found.
            actual: u64,
        },
        /// The sha256 is not the pinned one.
        ChecksumMismatch {
            /// Pinned digest.
            expected: String,
            /// Digest found.
            actual: String,
        },
        /// A network failure or a non-success status.
        Http(String),
    }

    impl fmt::Display for ModelError {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            match self {
                ModelError::Io(e) => write!(f, "model file error: {e}"),
                ModelError::SizeMismatch { expected, actual } => {
                    write!(f, "model size is {actual}, expected {expected}")
                }
                ModelError::ChecksumMismatch { expected, actual } => {
                    write!(f, "model sha256 is {actual}, expected {expected}")
                }
                ModelError::Http(m) => write!(f, "model download failed: {m}"),
            }
        }
    }

    impl std::error::Error for ModelError {
        fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
            match self {
                ModelError::Io(e) => Some(e),
                _ => None,
            }
        }
    }

    impl From<std::io::Error> for ModelError {
        fn from(e: std::io::Error) -> Self {
            ModelError::Io(e)
        }
    }

    /// Compare a measured size and digest with the pinned ones, size first.
    pub(super) fn check(model: &ModelFile, size: u64, sha256: &str) -> Result<(), ModelError> {
        if size != model.size {
            return Err(ModelError::SizeMismatch {
                expected: model.size,
                actual: size,
            });
        }
        if sha256 != model.sha256 {
            return Err(ModelError::ChecksumMismatch {
                expected: model.sha256.to_string(),
                actual: sha256.to_string(),
            });
        }
        Ok(())
    }

    /// Check a file's size, then its streamed sha256.
    ///
    /// # Errors
    ///
    /// [`ModelError::SizeMismatch`] or [`ModelError::ChecksumMismatch`] on a
    /// mismatch; [`ModelError::Io`] if the file cannot be read.
    pub fn verify_model(path: &Path, model: &ModelFile) -> Result<(), ModelError> {
        let size = std::fs::metadata(path)?.len();
        if size != model.size {
            return Err(ModelError::SizeMismatch {
                expected: model.size,
                actual: size,
            });
        }
        let mut file = File::open(path)?;
        let mut hasher = Sha256::new();
        let mut block = vec![0u8; BLOCK_SIZE];
        loop {
            let n = file.read(&mut block)?;
            if n == 0 {
                break;
            }
            hasher.update(&block[..n]);
        }
        check(model, size, &format!("{:x}", hasher.finalize()))
    }
}

#[cfg(feature = "embed")]
pub use verified::{verify_model, ModelError};

#[cfg(all(feature = "embed", feature = "http-ureq"))]
mod download {
    use super::verified::{check, BLOCK_SIZE};
    use super::{verify_model, ModelError, ModelFile};
    use sha2::{Digest, Sha256};
    use std::fs::File;
    use std::io::{Read, Write};
    use std::path::{Path, PathBuf};
    use std::sync::mpsc::RecvTimeoutError;
    use std::time::Duration;

    /// Longest wait for a connection, the response headers or one body block.
    const IDLE_TIMEOUT: Duration = Duration::from_secs(60);

    /// Removes the `.part` file on drop (an error or a panic), unless disarmed.
    struct PartGuard<'a> {
        path: &'a Path,
        armed: bool,
    }

    impl Drop for PartGuard<'_> {
        fn drop(&mut self) {
            if self.armed {
                let _ = std::fs::remove_file(self.path);
            }
        }
    }

    /// Return the verified model file in `directory`, downloading it if needed.
    ///
    /// An already-verified file costs no network. A mismatched one is deleted
    /// and fetched again. The download goes through `<file>.part`, which is
    /// removed on any failure.
    ///
    /// # Errors
    ///
    /// [`ModelError`]: a mismatch (a body longer or shorter than the pinned
    /// size included), a file-system failure, or a network failure.
    pub fn download_model(
        model: &ModelFile,
        directory: &Path,
        progress: &mut dyn FnMut(u64, u64),
    ) -> Result<PathBuf, ModelError> {
        download_model_from(&model.url(), model, directory, progress)
    }

    /// [`download_model`] from an explicit `url`, which tests point at a
    /// loopback server.
    ///
    /// # Errors
    ///
    /// As [`download_model`].
    pub fn download_model_from(
        url: &str,
        model: &ModelFile,
        directory: &Path,
        progress: &mut dyn FnMut(u64, u64),
    ) -> Result<PathBuf, ModelError> {
        std::fs::create_dir_all(directory)?;
        let final_path = directory.join(model.file);
        if final_path.exists() {
            match verify_model(&final_path, model) {
                Ok(()) => return Ok(final_path),
                Err(ModelError::SizeMismatch { .. } | ModelError::ChecksumMismatch { .. }) => {
                    std::fs::remove_file(&final_path)?;
                }
                Err(other) => return Err(other),
            }
        }
        let part = directory.join(format!("{}.part", model.file));
        let mut guard = PartGuard {
            path: &part,
            armed: true,
        };
        fetch(url, model, &part, progress)?;
        std::fs::rename(&part, &final_path)?;
        guard.armed = false;
        Ok(final_path)
    }

    /// Stream `url` into `part`, failing as soon as more than `model.size`
    /// bytes arrive, then check size and sha256.
    fn fetch(
        url: &str,
        model: &ModelFile,
        part: &Path,
        progress: &mut dyn FnMut(u64, u64),
    ) -> Result<(), ModelError> {
        let config = ureq::Agent::config_builder()
            // ureq has no per-read body timeout (`timeout_recv_body` is a total
            // budget), so these bound connecting and the headers, and the body
            // loop below bounds each block: Python's idle `timeout=60`.
            .timeout_resolve(Some(IDLE_TIMEOUT))
            .timeout_connect(Some(IDLE_TIMEOUT))
            .timeout_send_request(Some(IDLE_TIMEOUT))
            .timeout_recv_response(Some(IDLE_TIMEOUT))
            .user_agent(crate::http::default_user_agent())
            .build();
        let agent = ureq::Agent::new_with_config(config);
        let response = agent
            .get(url)
            .call()
            .map_err(|e| ModelError::Http(e.to_string()))?;
        let mut reader = response.into_body().into_reader();
        // A thread reads, so the loop can give up on a stalled block.
        let (tx, rx) = std::sync::mpsc::sync_channel::<std::io::Result<Vec<u8>>>(1);
        std::thread::spawn(move || loop {
            let mut buf = vec![0u8; BLOCK_SIZE];
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    buf.truncate(n);
                    if tx.send(Ok(buf)).is_err() {
                        break;
                    }
                }
                Err(e) => {
                    let _ = tx.send(Err(e));
                    break;
                }
            }
        });
        let mut out = File::create(part)?;
        let mut hasher = Sha256::new();
        let mut done: u64 = 0;
        loop {
            let block = match rx.recv_timeout(IDLE_TIMEOUT) {
                Ok(block) => block?,
                Err(RecvTimeoutError::Timeout) => {
                    return Err(ModelError::Http("download stalled".to_string()));
                }
                Err(RecvTimeoutError::Disconnected) => break,
            };
            out.write_all(&block)?;
            hasher.update(&block);
            done += block.len() as u64;
            if done > model.size {
                return Err(ModelError::SizeMismatch {
                    expected: model.size,
                    actual: done,
                });
            }
            progress(done, model.size);
        }
        out.flush()?;
        check(model, done, &format!("{:x}", hasher.finalize()))
    }
}

#[cfg(all(feature = "embed", feature = "http-ureq"))]
pub use download::{download_model, download_model_from};
