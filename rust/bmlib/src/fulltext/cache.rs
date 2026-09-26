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

//! Disk cache for retrieved full text.

use crate::atomic::{atomic_write, TEMP_SUFFIX_LEN};
use regex::Regex;
use sha1::{Digest, Sha1};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// The bytes every PDF begins with.
pub const PDF_MAGIC_BYTES: &[u8] = b"%PDF";

/// Ceiling on the readable part of a cache filename.
///
/// The whole name has to fit in the filesystem's name limit **with room to
/// spare**, because the temporary name an atomic write uses is
/// [`TEMP_SUFFIX_LEN`] characters longer than the target's.
pub const MAX_PREFIX_CHARS: usize = 160;

/// The suffix a quarantined entry carries.
pub const CORRUPT_SUFFIX: &str = ".corrupt";

fn safe_identifier_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"^[\w.\-]+$").expect("a fixed pattern"))
}

/// Turn a DOI or other identifier into a safe, collision-free filename.
///
/// A readable prefix is kept for debuggability, but because many distinct
/// identifiers sanitise to the same string — every character outside `[\w.\-]`
/// maps to `_` — a short hash of the **raw** identifier is appended so two
/// different identifiers can never share a cache file.
///
/// The prefix is truncated to [`MAX_PREFIX_CHARS`]; it is only there to be read,
/// and the hash — taken over the *whole* raw identifier — is what carries the
/// collision guarantee, so shortening it costs nothing.
#[must_use]
pub fn sanitize_identifier(raw: &str) -> String {
    let replaced = Regex::new(r"[^\w.\-]")
        .expect("a fixed pattern")
        .replace_all(raw, "_");
    // Truncated by **characters**, because the cap is about the filesystem's
    // name limit and a multi-byte character is one position in a name.
    let safe: String = replaced.chars().take(MAX_PREFIX_CHARS).collect();
    let mut hasher = Sha1::new();
    hasher.update(raw.as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest.iter().take(5).map(|b| format!("{b:02x}")).collect();
    format!("{safe}_{hex}")
}

/// `identifier` if it is already filename-safe, else [`sanitize_identifier`].
///
/// Already-safe identifiers pass through unchanged so existing cache files remain
/// addressable; a raw identifier containing a path separator is sanitised here as
/// a **defence in depth**, so a direct caller passing a raw DOI cannot write
/// outside the cache directory.
///
/// An over-long identifier is sanitised **even when its characters are safe**,
/// since the pass-through is what would otherwise carry it past
/// [`MAX_PREFIX_CHARS`].
#[must_use]
pub fn safe_filename(identifier: &str) -> String {
    if safe_identifier_pattern().is_match(identifier)
        && identifier.chars().count() <= MAX_PREFIX_CHARS
    {
        return identifier.to_string();
    }
    sanitize_identifier(identifier)
}

/// Whether a cache entry can still be read back.
///
/// Read the way the entry's own getter reads it, since the two ways an entry goes
/// bad surface differently: an HTML file truncated mid-multibyte-sequence **opens
/// perfectly** and fails on the decode, while an entry the process cannot get at
/// at all — wrong permissions, an I/O fault, a directory standing where the file
/// should be — fails on the open.
#[must_use]
pub fn is_readable(path: &Path) -> bool {
    // **A directory is never readable**, and the guard is load-bearing here: Rust's
    // `File::open` *succeeds* on a directory (measured), where Python's
    // `path.open("rb")` raises `IsADirectoryError`. Without it the non-HTML branch
    // below would call every directory readable, and the two implementations would
    // disagree on exactly the shape quarantine exists to move aside.
    if path.is_dir() {
        return false;
    }
    if path.extension().and_then(|e| e.to_str()) == Some("html") {
        // Only the HTML branch must read the bytes: a truncated multibyte
        // sequence opens and fails on the decode. A PDF is opened and closed, as
        // Python does, so the check costs no I/O on a multi-megabyte entry that
        // this now sits in front of on every cache lookup (defect #309).
        return std::fs::read(path)
            .map(|bytes| String::from_utf8(bytes).is_ok())
            .unwrap_or(false);
    }
    std::fs::File::open(path).is_ok()
}

/// Remove a cache entry, **whatever shape it turned out to be**.
///
/// A plain unlink is not enough: an entry is normally a regular file, but the
/// cache is a directory on a filesystem other things can touch, and an entry that
/// is *not* a file is precisely the corrupt case an operator needs to clear.
#[must_use]
pub fn remove_entry(path: &Path) -> bool {
    if path.is_dir() && !path.is_symlink() {
        return std::fs::remove_dir_all(path).is_ok();
    }
    match std::fs::remove_file(path) {
        Ok(()) => true,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => true,
        Err(_) => false,
    }
}

/// A platform-appropriate default cache directory.
#[must_use]
pub fn default_cache_dir() -> PathBuf {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let base = if cfg!(target_os = "macos") {
        home.join("Library").join("Caches")
    } else if cfg!(target_os = "windows") {
        let local = home.join("AppData").join("Local");
        if local.exists() {
            local
        } else {
            home.join(".cache")
        }
    } else {
        home.join(".cache")
    };
    base.join("bmlib").join("fulltext_cache")
}

/// A disk cache for retrieved full text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullTextCache {
    /// The directory both sub-caches live under.
    pub cache_dir: PathBuf,
}

impl Default for FullTextCache {
    fn default() -> Self {
        FullTextCache {
            cache_dir: default_cache_dir(),
        }
    }
}

impl FullTextCache {
    /// A cache under `cache_dir`, or the platform default when `None`.
    #[must_use]
    pub fn new(cache_dir: Option<PathBuf>) -> Self {
        FullTextCache {
            cache_dir: cache_dir.unwrap_or_else(default_cache_dir),
        }
    }

    /// Where PDFs live.
    #[must_use]
    pub fn pdf_dir(&self) -> PathBuf {
        self.cache_dir.join("pdfs")
    }

    /// Where parsed HTML lives.
    #[must_use]
    pub fn html_dir(&self) -> PathBuf {
        self.cache_dir.join("html")
    }

    /// Save PDF data **if it passes magic-byte validation**.
    ///
    /// The file is published atomically, so a write that fails partway leaves no
    /// half-written PDF behind.
    ///
    /// # Errors
    ///
    /// A filesystem failure — a full disk, a read-only directory. `Ok(None)` is
    /// the *rejection*, which is a different outcome from a write that failed:
    /// the caller reports the first and must not swallow the second.
    pub fn save_pdf(&self, data: &[u8], identifier: &str) -> std::io::Result<Option<PathBuf>> {
        if data.len() < PDF_MAGIC_BYTES.len() || &data[..PDF_MAGIC_BYTES.len()] != PDF_MAGIC_BYTES {
            // A rejection, not an error — the population it happens in is
            // ordinary (an Unpaywall URL resolving to a landing page).
            return Ok(None);
        }
        let path = self
            .pdf_dir()
            .join(format!("{}.pdf", safe_filename(identifier)));
        atomic_write(&path, data)?;
        Ok(Some(path))
    }

    /// The cached PDF path, or `None` if not cached **or unreadable**.
    ///
    /// **Presence is not a hit** (defect #309). Python's `get_pdf` tests only
    /// `path.exists()`, so anything that is not a regular readable file at the
    /// entry's path — a directory, an unreadable file — is returned as a cached
    /// PDF. The conversion that follows fails, `_attach_pdf_text` swallows it, and
    /// the same bogus hit is served on every later run; `_is_readable` already
    /// opens PDFs `rb` for exactly this purpose and was not consulted. Consulted
    /// here, so a direct caller gets the cache contract the docstring states.
    #[must_use]
    pub fn get_pdf(&self, identifier: &str) -> Option<PathBuf> {
        let path = self
            .pdf_dir()
            .join(format!("{}.pdf", safe_filename(identifier)));
        if !path.exists() || !is_readable(&path) {
            return None;
        }
        Some(path)
    }

    /// Save parsed HTML full text.
    ///
    /// # Errors
    ///
    /// A filesystem failure, as [`FullTextCache::save_pdf`].
    pub fn save_html(&self, html: &str, identifier: &str) -> std::io::Result<PathBuf> {
        let path = self
            .html_dir()
            .join(format!("{}.html", safe_filename(identifier)));
        atomic_write(&path, html.as_bytes())?;
        Ok(path)
    }

    /// The cached HTML content, or `None` if not cached **or unreadable**.
    ///
    /// An unreadable entry returns `None` rather than an error: the caller's next
    /// step is the same either way, and the entry is quarantined by
    /// [`FullTextCache::quarantine`] rather than reported here.
    #[must_use]
    pub fn get_html(&self, identifier: &str) -> Option<String> {
        let path = self
            .html_dir()
            .join(format!("{}.html", safe_filename(identifier)));
        if !path.exists() {
            return None;
        }
        std::fs::read(&path)
            .ok()
            .and_then(|bytes| String::from_utf8(bytes).ok())
    }

    /// Move any unreadable entry for `identifier` out of the lookup path.
    ///
    /// An entry corrupted by something outside this library is **not deleted** —
    /// a failed re-fetch should leave the evidence — but leaving it in place is
    /// not viable either: a cached HTML file that cannot be decoded is consulted
    /// before the PDF, so it hides a perfectly good PDF entry behind it, and every
    /// later run repeats the same warning and the same network fetch, for ever.
    /// Renaming it aside satisfies both: the next lookup is a clean miss the
    /// retrieval chain can heal, and the bytes are still there for an operator to
    /// inspect.
    ///
    /// **Only entries that genuinely fail to read are moved**; a readable one
    /// beside a corrupt one is left alone. Best effort throughout — this runs
    /// while another failure is already being handled, so a rename that cannot
    /// proceed must not become the error the caller sees.
    ///
    /// Returns the paths moved aside, in the order they were checked.
    #[must_use]
    pub fn quarantine(&self, identifier: &str) -> Vec<PathBuf> {
        let mut moved = Vec::new();
        let name = safe_filename(identifier);
        for path in [
            self.html_dir().join(format!("{name}.html")),
            self.pdf_dir().join(format!("{name}.pdf")),
        ] {
            if !path.exists() || is_readable(&path) {
                continue;
            }
            let aside = path.with_file_name(format!(
                "{}{CORRUPT_SUFFIX}",
                path.file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default()
            ));
            if std::fs::rename(&path, &aside).is_ok() {
                moved.push(aside);
            }
        }
        moved
    }

    /// Delete every cached file for `identifier`, PDF and HTML.
    pub fn delete(&self, identifier: &str) {
        let name = safe_filename(identifier);
        // Best effort: a caller removing an entry has no recovery either way,
        // and the next lookup is a miss regardless.
        let _ = remove_entry(&self.html_dir().join(format!("{name}.html")));
        let _ = remove_entry(&self.pdf_dir().join(format!("{name}.pdf")));
    }

    /// Remove **all** cached files, quarantined and temporary ones included.
    ///
    /// Every entry is removed, not only the regular files: an entry that is a
    /// directory is exactly the corrupt case this exists to clear, and skipping
    /// it silently is how both documented ways to remove a bad entry failed on
    /// the same one.
    pub fn clear(&self) -> std::io::Result<()> {
        for directory in [self.pdf_dir(), self.html_dir()] {
            let Ok(entries) = std::fs::read_dir(&directory) else {
                continue;
            };
            for entry in entries.flatten() {
                // One unremovable entry must not stop the sweep.
                let _ = remove_entry(&entry.path());
            }
        }
        Ok(())
    }
}

/// The length [`TEMP_SUFFIX_LEN`] must leave for, re-exported so the cache's cap
/// and the temporary name are asserted against **one** figure.
pub const TEMP_ROOM: usize = TEMP_SUFFIX_LEN;
