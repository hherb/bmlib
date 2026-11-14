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

//! `fulltext::meca::s3`'s transport over a fake S3: the same behaviours
//! Python's `tests/test_meca_s3.py` pins.

use bmlib::fulltext::meca::s3::{
    download_object, list_objects, DownloadError, S3ErrorKind, S3Object, MECA_BUCKET_REGION,
    MEDRXIV_MECA_BUCKET,
};
use bmlib::fulltext::meca::sigv4::S3Credentials;
use bmlib::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse, HttpStream};
use chrono::{TimeZone, Utc};
use md5::{Digest, Md5};
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;

/// What the fake serves for one request.
enum Answer {
    /// A status and a whole body.
    Body(u16, Vec<u8>),
    /// A response with this status whose body breaks off after these bytes.
    Breaks(u16, Vec<u8>),
    /// No connection.
    Refused,
}

/// A fake S3: answers in order and records each request's URL and headers.
struct FakeS3 {
    answers: RefCell<Vec<Answer>>,
    seen: RefCell<Vec<(String, BTreeMap<String, String>)>>,
}

impl FakeS3 {
    fn new(answers: Vec<Answer>) -> Self {
        FakeS3 {
            answers: RefCell::new(answers),
            seen: RefCell::new(Vec::new()),
        }
    }

    fn next(&self, url: &str, headers: &BTreeMap<String, String>) -> Answer {
        self.seen
            .borrow_mut()
            .push((url.to_string(), headers.clone()));
        self.answers.borrow_mut().remove(0)
    }
}

/// A body that serves `served`, then fails like a reset connection.
struct Broken {
    served: std::io::Cursor<Vec<u8>>,
}

impl Read for Broken {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        match self.served.read(buf)? {
            0 => Err(std::io::Error::new(
                std::io::ErrorKind::ConnectionReset,
                "reset",
            )),
            n => Ok(n),
        }
    }
}

impl HttpClient for FakeS3 {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        panic!("an unsigned GET {url}");
    }

    fn get_with_headers_stream(
        &self,
        url: &str,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        Ok(match self.next(url, headers) {
            Answer::Body(status, body) => HttpStream {
                status,
                body: Box::new(std::io::Cursor::new(body)),
            },
            Answer::Breaks(status, served) => HttpStream {
                status,
                body: Box::new(Broken {
                    served: std::io::Cursor::new(served),
                }),
            },
            Answer::Refused => return Err(FetchError::Transport("refused".into())),
        })
    }
}

/// A temporary directory that cleans up after itself (as `tests/cache.rs`'s).
struct TempDir(std::path::PathBuf);

impl TempDir {
    fn new() -> Self {
        static COUNT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let n = COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("bmlib-meca-s3-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).expect("temp dir");
        TempDir(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn key() -> S3Credentials {
    S3Credentials::new(
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        Some("TOKEN+/="),
    )
    .unwrap()
}

fn now() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 11, 14, 10, 20, 30).unwrap()
}

fn body() -> Vec<u8> {
    b"PK\x03\x04 a pretend package ".repeat(1000)
}

fn md5_hex(data: &[u8]) -> String {
    Md5::digest(data)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn object(served: &[u8], etag: Option<&str>) -> S3Object {
    S3Object {
        key: "Current_Content/January_2024/0a1b.meca".into(),
        size: served.len() as u64,
        etag: etag.map_or_else(|| md5_hex(served), str::to_string),
        last_modified: "2024-01-01T00:00:00.000Z".into(),
    }
}

fn page(keys: &[&str], token: Option<&str>) -> Vec<u8> {
    let contents: String = keys
        .iter()
        .map(|k| {
            format!(
                "<Contents><Key>{k}</Key><LastModified>2024-01-01T00:00:00.000Z</LastModified>\
                 <ETag>\"{}\"</ETag><Size>1</Size></Contents>",
                "0".repeat(32)
            )
        })
        .collect();
    let (truncated, next) = match token {
        Some(t) => (
            "true",
            format!("<NextContinuationToken>{t}</NextContinuationToken>"),
        ),
        None => ("false", String::new()),
    };
    format!("<ListBucketResult><IsTruncated>{truncated}</IsTruncated>{next}{contents}</ListBucketResult>")
        .into_bytes()
}

fn download(
    s3: &FakeS3,
    listed: &S3Object,
    destination: &Path,
) -> Result<std::path::PathBuf, DownloadError> {
    download_object(
        s3,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        listed,
        destination,
        now(),
        &mut |_, _| true,
    )
}

fn entries(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
        .collect();
    names.sort();
    names
}

fn kind(result: Result<std::path::PathBuf, DownloadError>) -> S3ErrorKind {
    match result {
        Err(DownloadError::S3(e)) => e.kind,
        other => panic!("expected an S3 error, got {other:?}"),
    }
}

#[test]
fn pages_are_followed_with_the_token_and_every_request_pays() {
    let s3 = FakeS3::new(vec![
        Answer::Body(200, page(&["a.meca"], Some("T/1="))),
        Answer::Body(200, page(&["b.meca"], None)),
    ]);
    let first = list_objects(
        &s3,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        "",
        None,
        now(),
    )
    .unwrap();
    let second = list_objects(
        &s3,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        "",
        first.next_continuation_token.as_deref(),
        now(),
    )
    .unwrap();
    let keys: Vec<_> = first
        .objects
        .iter()
        .chain(&second.objects)
        .map(|o| o.key.as_str())
        .collect();
    assert_eq!(keys, ["a.meca", "b.meca"]);
    let seen = s3.seen.borrow();
    assert!(seen[1].0.contains("continuation-token=T%2F1%3D"));
    for (url, headers) in seen.iter() {
        assert!(url.starts_with("https://medrxiv-src-monthly.s3.us-east-1.amazonaws.com/?"));
        assert_eq!(headers["x-amz-request-payer"], "requester");
        assert!(headers["authorization"].starts_with("AWS4-HMAC-SHA256 Credential=AKIA"));
    }
}

#[test]
fn a_refused_listing_is_classified() {
    let body = b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>";
    let s3 = FakeS3::new(vec![Answer::Body(403, body.to_vec())]);
    let error = list_objects(
        &s3,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        "",
        None,
        now(),
    )
    .unwrap_err();
    assert_eq!(error.kind, S3ErrorKind::AccessDenied);
    assert!(error.to_string().contains("s3:ListBucket"));
}

#[test]
fn no_connection_is_a_retryable_transport_error() {
    let s3 = FakeS3::new(vec![Answer::Refused]);
    let error = list_objects(
        &s3,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        "",
        None,
        now(),
    )
    .unwrap_err();
    assert_eq!(error.kind, S3ErrorKind::Transport);
    assert!(error.retryable());
}

#[test]
fn a_verified_body_is_published() {
    let dir = TempDir::new();
    let destination = dir.path().join("pkg").join("0a1b.meca");
    let served = body();
    let s3 = FakeS3::new(vec![Answer::Body(200, served.clone())]);
    let path = download(&s3, &object(&served, None), &destination).unwrap();
    assert_eq!(path, destination);
    assert_eq!(std::fs::read(&destination).unwrap(), served);
    assert_eq!(entries(&dir.path().join("pkg")), ["0a1b.meca"]);
    let seen = s3.seen.borrow();
    assert_eq!(
        seen[0].0,
        "https://medrxiv-src-monthly.s3.us-east-1.amazonaws.com/Current_Content/January_2024/0a1b.meca"
    );
    assert_eq!(seen[0].1["x-amz-request-payer"], "requester");
    assert_eq!(seen[0].1["x-amz-security-token"], "TOKEN+/=");
}

#[test]
fn the_wrong_bytes_leave_nothing_behind() {
    let served = body();
    let cases = [
        (
            served[..served.len() - 1].to_vec(),
            object(&served, None),
            S3ErrorKind::SizeMismatch,
        ),
        (
            [served.clone(), b"x".to_vec()].concat(),
            object(&served, None),
            S3ErrorKind::SizeMismatch,
        ),
        (
            served.clone(),
            object(&served, Some(&"0".repeat(32))),
            S3ErrorKind::EtagMismatch,
        ),
    ];
    for (sent, listed, expected) in cases {
        let dir = TempDir::new();
        let s3 = FakeS3::new(vec![Answer::Body(200, sent)]);
        let got = kind(download(&s3, &listed, &dir.path().join("x.meca")));
        assert_eq!(got, expected);
        assert!(entries(dir.path()).is_empty());
    }
}

#[test]
fn a_multipart_etag_is_not_checked() {
    let dir = TempDir::new();
    let served = body();
    let s3 = FakeS3::new(vec![Answer::Body(200, served.clone())]);
    let listed = object(&served, Some("d41d8cd98f00b204e9800998ecf8427e-3"));
    download(&s3, &listed, &dir.path().join("x")).unwrap();
}

#[test]
fn an_existing_file_is_kept_when_the_download_fails() {
    let dir = TempDir::new();
    let destination = dir.path().join("x.meca");
    std::fs::write(&destination, b"old").unwrap();
    let error = b"<Error><Code>NoSuchKey</Code><Message>gone</Message></Error>";
    let s3 = FakeS3::new(vec![Answer::Body(404, error.to_vec())]);
    let served = body();
    assert_eq!(
        kind(download(&s3, &object(&served, None), &destination)),
        S3ErrorKind::NoSuchKey
    );
    assert_eq!(std::fs::read(&destination).unwrap(), b"old");
    assert_eq!(entries(dir.path()), ["x.meca"]);
}

#[test]
fn progress_returning_false_stops_it() {
    let dir = TempDir::new();
    let served = body();
    let s3 = FakeS3::new(vec![Answer::Body(200, served.clone())]);
    let mut calls = Vec::new();
    let result = download_object(
        &s3,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        &object(&served, None),
        &dir.path().join("x"),
        now(),
        &mut |done, total| {
            calls.push((done, total));
            false
        },
    );
    assert_eq!(kind(result), S3ErrorKind::Cancelled);
    assert_eq!(calls[0].1, served.len() as u64);
    assert!(entries(dir.path()).is_empty());
}

#[test]
fn a_broken_connection_is_a_transport_error_and_leaves_nothing() {
    let dir = TempDir::new();
    let served = body();
    let s3 = FakeS3::new(vec![Answer::Breaks(200, served[..100].to_vec())]);
    assert_eq!(
        kind(download(&s3, &object(&served, None), &dir.path().join("x"))),
        S3ErrorKind::Transport
    );
    assert!(entries(dir.path()).is_empty());
}

#[test]
fn a_client_that_cannot_stream_is_a_transport_error() {
    struct Plain;
    impl HttpClient for Plain {
        fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
            unreachable!()
        }
    }
    let dir = TempDir::new();
    let served = body();
    let result = download_object(
        &Plain,
        &key(),
        MEDRXIV_MECA_BUCKET,
        MECA_BUCKET_REGION,
        &object(&served, None),
        &dir.path().join("x"),
        now(),
        &mut |_, _| true,
    );
    match result {
        Err(DownloadError::S3(e)) => {
            assert_eq!(e.kind, S3ErrorKind::Unsupported);
            assert!(!e.retryable(), "a retry would fail the same way");
            assert!(e.detail.contains("get_with_headers_stream"));
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn an_error_body_that_breaks_off_is_a_transport_error() {
    let dir = TempDir::new();
    let served = body();
    let s3 = FakeS3::new(vec![Answer::Breaks(404, b"<Error><Code>NoSu".to_vec())]);
    assert_eq!(
        kind(download(&s3, &object(&served, None), &dir.path().join("x"))),
        S3ErrorKind::Transport
    );
}

#[test]
fn a_panicking_progress_leaves_nothing_behind() {
    let dir = TempDir::new();
    let served = body();
    let s3 = FakeS3::new(vec![Answer::Body(200, served.clone())]);
    let listed = object(&served, None);
    let destination = dir.path().join("x");
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        download_object(
            &s3,
            &key(),
            MEDRXIV_MECA_BUCKET,
            MECA_BUCKET_REGION,
            &listed,
            &destination,
            now(),
            &mut |_, _| panic!("the caller's callback"),
        )
    }));
    assert!(outcome.is_err());
    assert!(entries(dir.path()).is_empty());
}
