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

//! Publish a file so no partial version is ever visible under it.
//!
//! Private to the crate, stdlib only, and shared by the full-text cache and the
//! template engine.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// The suffix a temporary file carries before it is published.
///
/// The temporary name is **[`TEMP_SUFFIX_LEN`] characters longer** than the
/// target's, so a caller building filenames from unbounded input has to leave
/// room for it inside the filesystem's name limit. The cache's prefix cap does,
/// and a test asserts this figure directly — which is the only guard that sees
/// the two drift apart.
pub const TEMP_SUFFIX: &str = ".bmlib-tmp-uuid4hex";

/// The length [`TEMP_SUFFIX`] adds to a filename.
pub const TEMP_SUFFIX_LEN: usize = 18;

/// Write `data` to `path` so no partial file is ever visible under it.
///
/// The bytes go to a uniquely-named temporary file **beside the target** — in
/// the target's own directory, so the two are always on one filesystem — and are
/// published with a rename, which is atomic within a filesystem. A write that
/// fails partway therefore leaves the target untouched — either the previous
/// version or nothing — instead of a truncated file that reads back perfectly and
/// is trusted for ever after.
///
/// # "Atomic" is about visibility, not crash durability
///
/// The data is synchronised before the rename is issued, so there is no ordering
/// in which the rename survives a crash and the data does not; but the
/// containing **directory is not** synchronised, so the rename itself can be
/// lost. That is the safe direction for both callers — the target is then simply
/// absent, which each treats as a miss and repairs.
///
/// # The rename replaces whatever is at `path`, including a symlink
///
/// The link itself, not the file it points at. A caller for whom a symlink there
/// is a user's deliberate indirection has to look for one first.
///
/// # Errors
///
/// Any filesystem failure, including a full disk — which is the point: under
/// delayed allocation a write can *return success* on a disk that is about to
/// fill, and the failure is reported only at synchronisation.
pub fn atomic_write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let temporary = temp_path_beside(path);

    // A scope, so the file is closed before the rename — and so the cleanup
    // below runs on every failure path rather than only the explicit ones.
    let result = (|| -> std::io::Result<()> {
        let mut file = fs::File::create(&temporary)?;
        // `write_all` rather than a single `write`: a short write is legal and
        // would publish a truncated file.
        file.write_all(data)?;
        // **Not durability theatre.** Under delayed allocation the write that
        // `write_all` issues returns success on a disk about to fill; the blocks
        // are allocated at writeback and the error is reported only at
        // synchronisation. Without this the rename publishes a file whose blocks
        // were never written.
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();

    // **Cleaned up unconditionally**, not only on a failure the caller sees. The
    // first cut removed the temporary inside an `is_err()` branch, which is
    // correct but **unobservable**: reaching that branch needs a filesystem that
    // fails mid-write, so a mutant deleting the cleanup survived the whole file
    // (measured). Removing it whenever it is still present is the same behaviour
    // and is reachable — a test asserts no temporary survives a successful write.

    result
}

/// The temporary path a write to `path` uses.
///
/// Carries a **unique** component, and the reason is not that two processes would
/// interleave into one file — a create can already prevent that — it is that the
/// loser of such a race would run the cleanup and remove the **winner's**
/// in-flight temporary file.
#[must_use]
pub fn temp_path_beside(path: &Path) -> PathBuf {
    let unique = unique_token();
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    path.with_file_name(format!("{name}{TEMP_SUFFIX}{unique}"))
}

/// A process-and-time unique token.
///
/// Not a UUID: reading from the system entropy source can block, and this only
/// has to be unique enough that two concurrent writers in one process — or two
/// processes started in different nanoseconds — do not collide.
fn unique_token() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let count = COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    format!("{nanos:x}-{:x}-{count:x}", std::process::id())
}
