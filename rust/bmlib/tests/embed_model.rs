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

//! The verified model download, against a loopback server only.

#![cfg(all(feature = "embed", feature = "http-ureq"))]

use bmlib::embed::{download_model_from, verify_model, ModelError, ModelFile};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

const MIB: usize = 1024 * 1024;

fn body() -> Vec<u8> {
    (0..3 * MIB).map(|i| (i % 256) as u8).collect() // 3 MiB
}

fn hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

struct Server {
    url: String,
    requests: Arc<AtomicUsize>,
}

/// Serve `payload` to every request; a `truncate`d one declares the full
/// length but sends half, then closes.
fn serve(payload: Vec<u8>, truncate: bool) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/m.gguf", listener.local_addr().unwrap());
    let requests = Arc::new(AtomicUsize::new(0));
    let count = Arc::clone(&requests);
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { break };
            let mut request = [0u8; 4096];
            let _ = stream.read(&mut request);
            count.fetch_add(1, Ordering::SeqCst);
            let sent = if truncate {
                payload.len() / 2
            } else {
                payload.len()
            };
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                payload.len()
            );
            let _ = stream.write_all(head.as_bytes());
            let _ = stream.write_all(&payload[..sent]);
        }
    });
    Server { url, requests }
}

fn model(expected: &[u8]) -> ModelFile {
    ModelFile {
        repo: "r",
        revision: "v",
        file: "m.gguf",
        size: expected.len() as u64,
        sha256: Box::leak(hex(expected).into_boxed_str()),
    }
}

fn dir_is_empty(dir: &Path) -> bool {
    std::fs::read_dir(dir).unwrap().next().is_none()
}

fn download(
    server: &Server,
    model: &ModelFile,
    dir: &Path,
) -> Result<std::path::PathBuf, ModelError> {
    download_model_from(&server.url, model, dir, &mut |_, _| {})
}

#[test]
fn download_fetches_and_verifies() {
    let server = serve(body(), false);
    let tmp = tempdir("fetches");
    let path = download(&server, &model(&body()), &tmp).unwrap();
    assert_eq!(path, tmp.join("m.gguf"));
    assert_eq!(std::fs::read(&path).unwrap(), body());
    assert!(!tmp.join("m.gguf.part").exists());
}

#[test]
fn download_skips_verified_file() {
    let server = serve(body(), false);
    let tmp = tempdir("skips");
    std::fs::write(tmp.join("m.gguf"), body()).unwrap();
    download(&server, &model(&body()), &tmp).unwrap();
    assert_eq!(server.requests.load(Ordering::SeqCst), 0);
}

#[test]
fn download_truncated_body_leaves_no_model() {
    let server = serve(body(), true);
    let tmp = tempdir("truncated");
    let err = download(&server, &model(&body()), &tmp).unwrap_err();
    match err {
        ModelError::Io(e) => assert_eq!(e.kind(), std::io::ErrorKind::UnexpectedEof),
        other => panic!("{other:?}"),
    }
    assert!(dir_is_empty(&tmp));
}

#[test]
fn download_short_body_with_matching_length_is_size_mismatch() {
    let full = body();
    let server = serve(full[..full.len() / 2].to_vec(), false);
    let tmp = tempdir("short");
    match download(&server, &model(&full), &tmp).unwrap_err() {
        ModelError::SizeMismatch { expected, actual } => {
            assert_eq!(
                (expected, actual),
                (full.len() as u64, full.len() as u64 / 2)
            );
        }
        other => panic!("{other:?}"),
    }
    assert!(dir_is_empty(&tmp));
}

#[test]
fn download_oversized_body_fails_early() {
    let server = serve(body(), false); // 3 MiB sent, 1 MiB expected
    let tmp = tempdir("oversized");
    let err = download(&server, &model(&body()[..MIB]), &tmp).unwrap_err();
    match err {
        ModelError::SizeMismatch { expected, actual } => {
            assert_eq!(expected, MIB as u64);
            assert!(actual > expected && actual < 3 * MIB as u64, "{actual}");
        }
        other => panic!("{other}"),
    }
    assert!(dir_is_empty(&tmp));
}

#[test]
fn download_wrong_bytes_is_checksum_error() {
    let server = serve(body(), false);
    let tmp = tempdir("checksum");
    let mut wrong = model(&body());
    wrong.sha256 = "0000000000000000000000000000000000000000000000000000000000000000";
    match download(&server, &wrong, &tmp).unwrap_err() {
        ModelError::ChecksumMismatch { expected, actual } => {
            assert_eq!(expected, wrong.sha256);
            assert_eq!(actual, hex(&body()));
        }
        other => panic!("{other}"),
    }
    assert!(dir_is_empty(&tmp));
}

#[test]
fn existing_mismatched_file_is_refetched() {
    let server = serve(body(), false);
    let tmp = tempdir("refetch");
    std::fs::write(tmp.join("m.gguf"), b"stale").unwrap();
    download(&server, &model(&body()), &tmp).unwrap();
    assert_eq!(server.requests.load(Ordering::SeqCst), 1);
    assert_eq!(std::fs::read(tmp.join("m.gguf")).unwrap(), body());
}

#[test]
fn progress_reports_reach_size() {
    let server = serve(body(), false);
    let tmp = tempdir("progress");
    let mut seen: Vec<(u64, u64)> = Vec::new();
    let m = model(&body());
    download_model_from(&server.url, &m, &tmp, &mut |d, t| seen.push((d, t))).unwrap();
    let size = body().len() as u64;
    assert_eq!(seen.last(), Some(&(size, size)));
    assert!(seen.windows(2).all(|w| w[0].0 <= w[1].0));
}

#[test]
fn panicking_progress_leaves_no_part_file() {
    let server = serve(body(), false);
    let tmp = tempdir("panic");
    let m = model(&body());
    let caught = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = download_model_from(&server.url, &m, &tmp, &mut |_, _| panic!("boom"));
    }));
    assert!(caught.is_err());
    assert!(dir_is_empty(&tmp));
}

#[test]
fn verify_model_size_mismatch() {
    let tmp = tempdir("verify");
    std::fs::write(tmp.join("m.gguf"), b"x").unwrap();
    match verify_model(&tmp.join("m.gguf"), &model(&body())).unwrap_err() {
        ModelError::SizeMismatch { expected, actual } => {
            assert_eq!((expected, actual), (body().len() as u64, 1));
        }
        other => panic!("{other}"),
    }
}

/// A fresh empty directory under the system temp dir (no `tempfile` dependency).
fn tempdir(tag: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("bmlib-embed-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}
