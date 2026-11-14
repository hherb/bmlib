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

//! #769, on a device: a file iCloud Drive evicted to a dataless file under its
//! real name is reported as not downloaded, at once, and stays evicted.
//! Python's `tests/test_library_dataless.py` is the twin.
//!
//! Opt-in, as it needs iCloud Drive and `brctl`: set `BMLIB_ICLOUD_DIR` to a
//! folder in iCloud Drive and run `cargo test --test library_dataless --
//! --ignored`.
#![cfg(target_os = "macos")]

use std::fs;
use std::os::macos::fs::MetadataExt;
use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use bmlib::library::{
    Installation, Library, LibraryError, PaperIds, PaperInfo, Problem, SourceKind, PAPERS_DIR,
    RECORDS_DIR,
};

/// `SF_DATALESS` in `<sys/stat.h>`'s `st_flags`.
const SF_DATALESS: u32 = 0x4000_0000;
/// How long iCloud may take to upload a new file before it can be evicted.
const UPLOAD_WAIT: Duration = Duration::from_secs(180);

fn dataless(path: &Path) -> bool {
    fs::metadata(path).unwrap().st_flags() & SF_DATALESS != 0
}

/// Evict `path`, retrying while iCloud has not uploaded it yet.
fn evict(path: &Path) {
    let start = Instant::now();
    while !dataless(path) {
        let _ = Command::new("brctl").arg("evict").arg(path).output();
        assert!(
            start.elapsed() < UPLOAD_WAIT,
            "{} was never evicted",
            path.display()
        );
        std::thread::sleep(Duration::from_secs(2));
    }
}

#[test]
#[ignore = "needs iCloud Drive: set BMLIB_ICLOUD_DIR"]
fn an_evicted_file_and_record_read_as_not_downloaded() {
    let base = std::env::var("BMLIB_ICLOUD_DIR").expect("BMLIB_ICLOUD_DIR");
    let root = Path::new(&base).join(format!("bmlib-dataless-rs-{}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    let lib = Library::open(&root).unwrap();
    let who = Installation::new("studyanalyzer", "aaaa", "Mac").unwrap();
    let paper = PaperInfo {
        ids: PaperIds::canonical(Some("111"), None, None).unwrap(),
        ..PaperInfo::default()
    };
    let file = lib
        .save(
            &who,
            &paper,
            b"%PDF an evicted paper",
            SourceKind::Pdf,
            "test",
        )
        .unwrap();
    let folder = root.join(PAPERS_DIR).join(&file.folder);

    let source = folder.join(&file.name);
    evict(&source);
    let start = Instant::now();
    let err = lib.read(&file).unwrap_err();
    assert!(start.elapsed() < Duration::from_secs(1), "the read waited");
    assert!(
        matches!(err, LibraryError::NotDownloaded { ref name, .. } if *name == file.name),
        "{err:?}"
    );
    assert!(dataless(&source), "the read downloaded it");

    let record = folder.join(RECORDS_DIR).join(who.record_file_name());
    evict(&record);
    let scan = lib.scan().unwrap();
    assert!(scan.problems.iter().any(|p| matches!(
        p,
        Problem::RecordNotDownloaded { file, .. } if *file == who.record_file_name()
    )));
    let err = lib.release(&who, &file).unwrap_err();
    assert!(matches!(err, LibraryError::NotDownloaded { .. }), "{err:?}");
    assert!(dataless(&record), "a read downloaded the record");

    fs::remove_dir_all(&root).unwrap();
}
