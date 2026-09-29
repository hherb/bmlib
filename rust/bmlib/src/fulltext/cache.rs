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
use std::ffi::OsString;
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

/// The longest key [`sanitize_identifier`] returns: the prefix, `_` and a
/// 10-character digest.
///
/// This, not [`MAX_PREFIX_CHARS`], is the bound the pass-through in
/// [`safe_filename`] needs — see there. Bounded at the prefix alone, every key
/// the service computed for a raw identifier of 150 characters or more was
/// hashed a second time, so the file written was not the documented
/// `sanitize_identifier(identifier)` and a lookup by that key missed it (#309).
/// The 214-character longest name the prefix cap leaves room for was always
/// computed over *this* length.
pub const MAX_KEY_CHARS: usize = MAX_PREFIX_CHARS + 11;

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
/// An over-long identifier — one longer than [`MAX_KEY_CHARS`], the longest key
/// [`sanitize_identifier`] returns — is sanitised **even when its characters are
/// safe**, because the pass-through is what would otherwise carry it past that
/// cap. The bound is the *key* length and not the prefix length (#309): every key
/// the sanitizer can return is at most `MAX_KEY_CHARS` characters, so every such
/// key passes through and is never hashed twice.
#[must_use]
pub fn safe_filename(identifier: &str) -> String {
    if safe_identifier_pattern().is_match(identifier) && identifier.chars().count() <= MAX_KEY_CHARS
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

/// The platform whose home-directory and cache rules apply.
///
/// Python asks `platform.system()` at run time; this is the compile target, which
/// is where the binary runs. The three arms are Python's, and the difference
/// between them is **not** cosmetic: on Windows `Path.home()` never consults
/// `HOME`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Platform {
    MacOs,
    Windows,
    Other,
}

impl Platform {
    /// The platform this build targets.
    const fn host() -> Self {
        if cfg!(target_os = "macos") {
            Platform::MacOs
        } else if cfg!(target_os = "windows") {
            Platform::Windows
        } else {
            Platform::Other
        }
    }

    /// The home directory this platform's rules find in `var`, or `None`.
    ///
    /// Reproduces `Path.home()`, whose lookup is platform-specific:
    /// `posixpath.expanduser` reads `HOME` and nothing else, while
    /// `ntpath.expanduser` reads `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`,
    /// and **never `HOME`**. Taking the lookup as a closure is what makes all
    /// three arms testable on one machine; the alternative is mutating
    /// process-global `HOME`, which is racy under `cargo test`'s threads.
    ///
    /// `None` is Python's `RuntimeError`: no home directory can be determined.
    fn home_from(self, var: impl Fn(&str) -> Option<OsString>) -> Option<PathBuf> {
        match self {
            Platform::Windows => var("USERPROFILE").map(PathBuf::from).or_else(|| {
                // Python **concatenates** the two strings rather than joining
                // them as paths, so a rooted `HOMEPATH` does not replace the
                // drive. `OsString::push` appends, which is that operation.
                let mut combined = var("HOMEDRIVE")?;
                combined.push(var("HOMEPATH")?);
                Some(PathBuf::from(combined))
            }),
            Platform::MacOs | Platform::Other => var("HOME").map(PathBuf::from),
        }
    }

    /// The cache root under `home`.
    fn cache_dir_under(self, home: &Path) -> PathBuf {
        let base = match self {
            Platform::MacOs => home.join("Library").join("Caches"),
            Platform::Windows => {
                // The one filesystem probe in the chain, and Python's:
                // `%LOCALAPPDATA%` is *not* read, and a machine without the
                // directory falls back to `~/.cache`.
                let local = home.join("AppData").join("Local");
                if local.exists() {
                    local
                } else {
                    home.join(".cache")
                }
            }
            Platform::Other => home.join(".cache"),
        };
        base.join("bmlib").join("fulltext_cache")
    }
}

/// A platform-appropriate default cache directory, or `None` where the home
/// directory cannot be determined.
///
/// **`None` is the point.** It is the case Python's `Path.home()` raises
/// `RuntimeError` in — no `HOME` and no passwd entry on POSIX, as in a distroless
/// container. The port used to substitute `PathBuf::from(".")`, which wrote the
/// cache into whatever directory the process happened to be started in while
/// Python cached nothing at all; the `Option` is what makes that unrepresentable
/// rather than merely fixed. See `docs/DECISIONS.md` §"fulltext — the service
/// degrades but the cache still raises" (*"No fallback cache location"*) and
/// `docs/manual/fulltext.md` §"Default cache directory".
///
/// **One divergence, recorded in the port plan's §9.** `Path.home()`'s POSIX arm
/// falls back to the passwd database where `HOME` is unset; this does not, so a
/// POSIX machine with a passwd entry but no `HOME` caches nothing here where
/// Python caches under that entry.
#[must_use]
pub fn default_cache_dir() -> Option<PathBuf> {
    let platform = Platform::host();
    let home = platform.home_from(|name| std::env::var_os(name))?;
    Some(platform.cache_dir_under(&home))
}

/// A disk cache for retrieved full text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullTextCache {
    /// The directory both sub-caches live under.
    pub cache_dir: PathBuf,
}

// There is deliberately **no `Default` impl.** Python's `FullTextCache()` raises
// where no home directory can be determined, and `Default::default()` has no way
// to report that: an infallible default would have to panic on such a machine or
// invent a directory, and inventing one is the defect this replaced. A caller who
// wants the platform default asks for it and handles the `None`.

impl FullTextCache {
    /// A cache under `cache_dir`, or the platform default when `None`.
    ///
    /// **`None` in, `Option` out.** `FullTextCache::new(None)` is Python's
    /// `FullTextCache()`, which raises `RuntimeError` when no home directory
    /// exists to build the default under; `None` returned is that case. Passing
    /// `Some` never fails here because the Rust cache creates no directory on
    /// construction — Python's constructor does, which is where its `OSError` arm
    /// comes from and why `default_cache` is the call that degrades.
    #[must_use]
    pub fn new(cache_dir: Option<PathBuf>) -> Option<Self> {
        match cache_dir {
            Some(dir) => Some(FullTextCache { cache_dir: dir }),
            None => default_cache_dir().map(|dir| FullTextCache { cache_dir: dir }),
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

    /// Where the abstract a cached PDF was returned with lives.
    ///
    /// A directory of its own because `html/` is served as full text, and these
    /// entries are read **only** beside a cached PDF — never as a hit on their
    /// own (#305). It is created by the first [`FullTextCache::save_abstract`],
    /// not by construction, so a cache built by an earlier bmlib — possibly
    /// read-only — still constructs. Python's comment is explicit: creating it
    /// at construction would make a read-only cache that serves hits today
    /// raise instead.
    #[must_use]
    pub fn abstract_dir(&self) -> PathBuf {
        self.cache_dir.join("abstracts")
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

    /// Save the abstract a cached PDF was returned with.
    ///
    /// [`FullTextService`](crate::fulltext::FullTextService) holds a body-less
    /// JATS rendering back as a last resort and pairs it with a PDF that yields
    /// no text; it saves the abstract here whenever a PDF was cached with one
    /// held back, whether or not the PDF yielded text that time. Once the PDF is
    /// cached the retrieval chain never runs again for that identifier, so
    /// without this entry every later hit that yields no text lost the abstract
    /// the first call returned (#305).
    ///
    /// Published atomically, like the other entries. The directory is created
    /// here rather than at construction, so a cache built by an earlier bmlib —
    /// possibly read-only — still constructs.
    ///
    /// # Errors
    ///
    /// A filesystem failure — the directory cannot be created, or the write
    /// fails (a full disk, a read-only cache root).
    pub fn save_abstract(&self, html: &str, identifier: &str) -> std::io::Result<PathBuf> {
        let path = self
            .abstract_dir()
            .join(format!("{}.html", safe_filename(identifier)));
        std::fs::create_dir_all(self.abstract_dir())?;
        atomic_write(&path, html.as_bytes())?;
        Ok(path)
    }

    /// The abstract cached beside a PDF, or `None` if there is none **or it
    /// cannot be read**.
    ///
    /// An unreadable entry returns `None` rather than an error, exactly as
    /// [`FullTextCache::get_html`] does — the two ways it fails are the same two
    /// — and the caller quarantines it in the same way.
    #[must_use]
    pub fn get_abstract(&self, identifier: &str) -> Option<String> {
        let path = self
            .abstract_dir()
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
            self.abstract_dir().join(format!("{name}.html")),
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

    /// Delete every cached file for `identifier` — HTML, PDF and abstract.
    pub fn delete(&self, identifier: &str) {
        let name = safe_filename(identifier);
        // Best effort: a caller removing an entry has no recovery either way,
        // and the next lookup is a miss regardless.
        let _ = remove_entry(&self.html_dir().join(format!("{name}.html")));
        let _ = remove_entry(&self.pdf_dir().join(format!("{name}.pdf")));
        let _ = remove_entry(&self.abstract_dir().join(format!("{name}.html")));
    }

    /// Remove **all** cached files, quarantined and temporary ones included.
    ///
    /// Every entry is removed, not only the regular files: an entry that is a
    /// directory is exactly the corrupt case this exists to clear, and skipping
    /// it silently is how both documented ways to remove a bad entry failed on
    /// the same one. A subdirectory that is **absent** is skipped, which the
    /// loop's `else` already does: `abstracts/` exists only once something has
    /// been saved there, so an older cache lacks it.
    pub fn clear(&self) -> std::io::Result<()> {
        for directory in [self.pdf_dir(), self.html_dir(), self.abstract_dir()] {
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

#[cfg(test)]
mod tests {
    use super::*;

    /// `Platform::home_from`'s Windows arm, which is the one Python's two
    /// implementations actually disagree about.
    fn windows_home(vars: &[(&str, &str)]) -> Option<PathBuf> {
        Platform::Windows.home_from(|name| {
            vars.iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| OsString::from(value))
        })
    }

    fn posix_home(platform: Platform, vars: &[(&str, &str)]) -> Option<PathBuf> {
        platform.home_from(|name| {
            vars.iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| OsString::from(value))
        })
    }

    /// POSIX reads `HOME`, and a missing `HOME` is `None` rather than a
    /// relative path — the defect the `Option` return exists to make
    /// unrepresentable.
    #[test]
    fn a_posix_home_is_home_and_nothing_else() {
        for platform in [Platform::MacOs, Platform::Other] {
            assert_eq!(
                posix_home(platform, &[("HOME", "/home/ada")]),
                Some(PathBuf::from("/home/ada"))
            );
            assert_eq!(posix_home(platform, &[]), None);
            // `USERPROFILE` is a Windows variable and must not be read here.
            assert_eq!(
                posix_home(platform, &[("USERPROFILE", "C:\\Users\\ada")]),
                None
            );
        }
    }

    /// **Windows never reads `HOME`** — `ntpath.expanduser` consults
    /// `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`. Reading `HOME` there is
    /// what the port used to do, and it finds a different directory.
    #[test]
    fn a_windows_home_ignores_home_and_prefers_userprofile() {
        assert_eq!(
            windows_home(&[("HOME", "C:\\wrong"), ("USERPROFILE", "C:\\Users\\ada")]),
            Some(PathBuf::from("C:\\Users\\ada"))
        );
        assert_eq!(
            windows_home(&[("HOME", "C:\\wrong")]),
            None,
            "HOME is not a Windows home"
        );
    }

    /// The `HOMEDRIVE` + `HOMEPATH` pair, and the concatenation rule: Python
    /// adds the two strings, so a rooted `HOMEPATH` keeps the drive.
    #[test]
    fn a_windows_home_falls_back_to_the_drive_and_path_pair() {
        assert_eq!(
            windows_home(&[("HOMEDRIVE", "C:"), ("HOMEPATH", "\\Users\\ada")]),
            Some(PathBuf::from("C:\\Users\\ada"))
        );
        // A half pair is not a home, exactly as Python's two `.get()`s find.
        assert_eq!(windows_home(&[("HOMEDRIVE", "C:")]), None);
        assert_eq!(windows_home(&[("HOMEPATH", "\\Users\\ada")]), None);
    }

    /// The platform table, including the Windows fallback that is decided by a
    /// directory *existing* rather than by an environment variable.
    #[test]
    fn the_cache_root_is_the_documented_one_per_platform() {
        let home = Path::new("/home/ada");
        assert_eq!(
            Platform::MacOs.cache_dir_under(home),
            PathBuf::from("/home/ada/Library/Caches/bmlib/fulltext_cache")
        );
        assert_eq!(
            Platform::Other.cache_dir_under(home),
            PathBuf::from("/home/ada/.cache/bmlib/fulltext_cache")
        );

        let dir = std::env::temp_dir().join(format!("bmlib-cache-root-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a temporary home");
        assert_eq!(
            Platform::Windows.cache_dir_under(&dir),
            dir.join(".cache").join("bmlib").join("fulltext_cache"),
            "a home without AppData/Local falls back to .cache"
        );
        std::fs::create_dir_all(dir.join("AppData").join("Local")).expect("the probe's directory");
        assert_eq!(
            Platform::Windows.cache_dir_under(&dir),
            dir.join("AppData")
                .join("Local")
                .join("bmlib")
                .join("fulltext_cache"),
            "and uses it once it exists"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `Some` in, `Some` out — the explicit-directory path never fails, because
    /// this cache creates no directory on construction.
    #[test]
    fn an_explicit_directory_is_used_verbatim() {
        let cache = FullTextCache::new(Some(PathBuf::from("/tmp/explicit")))
            .expect("an explicit directory needs no home");
        assert_eq!(cache.cache_dir, PathBuf::from("/tmp/explicit"));
    }

    /// The default path follows the platform this build targets, and the
    /// directory it names is the documented one. A machine with no `HOME` is
    /// `None` — asserted through the same closure the constructor uses, since
    /// mutating `HOME` is process-global and racy.
    #[test]
    fn the_default_cache_directory_follows_the_platform() {
        let platform = Platform::host();
        let home = Path::new("/home/ada");
        let expected = platform.cache_dir_under(home);
        assert!(expected.ends_with(Path::new("bmlib").join("fulltext_cache")));
        // The composition `default_cache_dir` makes, with the home supplied.
        assert_eq!(
            platform.home_from(|_| Some(OsString::from("/home/ada"))),
            Some(home.to_path_buf())
        );
        // And with no home at all: the whole chain is `None`, **not** a relative
        // path. This is the assertion that fails if a `"."` fallback comes back,
        // which is what the `Option` return exists to prevent.
        let from_nothing = platform
            .home_from(|_| None)
            .map(|home| platform.cache_dir_under(&home));
        assert_eq!(
            from_nothing, None,
            "no home must mean no default directory, never a relative one"
        );
    }

    /// Whatever this machine's environment produces is an **absolute** path, or
    /// nothing. A relative one is the shape the old `"."` fallback had, and the
    /// reason `None` is the right answer: caching nothing beats writing the cache
    /// into an unrelated working directory.
    #[test]
    fn a_default_directory_is_absolute_when_there_is_one() {
        if let Some(dir) = default_cache_dir() {
            assert!(dir.is_absolute(), "{} is relative", dir.display());
            assert!(dir.ends_with(Path::new("bmlib").join("fulltext_cache")));
        }
    }
}
