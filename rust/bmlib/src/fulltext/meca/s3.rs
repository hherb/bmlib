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

//! Read a requester-pays S3 bucket: list a prefix, download one object.
//!
//! Port of `bmlib/fulltext/meca/s3.py`, held to it by
//! `tests/fixtures/s3_responses.json` and `tests/fixtures/s3_sigv4.json`. The
//! transport runs over [`HttpClient`]; a download needs
//! [`HttpClient::get_with_headers_stream`].
//!
//! Every request carries `x-amz-request-payer: requester`: the caller's AWS
//! account pays for what it reads.

use super::sigv4::{sign_get, S3Credentials, SignError, SignedRequest};
use crate::publications::fetchers::registry::{FetchError, HttpClient};
use chrono::{DateTime, Utc};
use md5::{Digest, Md5};
use std::fmt;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

/// medRxiv's MECA archive (requester pays).
pub const MEDRXIV_MECA_BUCKET: &str = "medrxiv-src-monthly";

/// bioRxiv's MECA archive (requester pays).
pub const BIORXIV_MECA_BUCKET: &str = "biorxiv-src-monthly";

/// The region both MECA buckets are in.
pub const MECA_BUCKET_REGION: &str = "us-east-1";

/// The header that makes the caller's account pay for a request.
pub const REQUESTER_PAYS: (&str, &str) = ("x-amz-request-payer", "requester");

/// Objects per listing page: S3's maximum.
pub const LIST_MAX_KEYS: u32 = 1000;

/// Bytes read per block while downloading.
pub const DOWNLOAD_BLOCK_BYTES: usize = 1 << 20;

/// S3's XML namespace.
const S3_NS: &str = "http://s3.amazonaws.com/doc/2006-03-01/";

/// What went wrong, in the terms a user can act on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum S3ErrorKind {
    /// The key may not read the bucket.
    AccessDenied,
    /// AWS does not know the key ID.
    InvalidAccessKeyId,
    /// The secret does not belong to the key ID.
    SignatureDoesNotMatch,
    /// This computer's clock is too far from AWS's.
    RequestTimeTooSkewed,
    /// Temporary credentials expired.
    ExpiredToken,
    /// The object is gone.
    NoSuchKey,
    /// The bucket does not exist.
    NoSuchBucket,
    /// AWS is busy (`SlowDown`, 503, `InternalError`).
    RetryLater,
    /// Any other refusal, kept with its code.
    Other,
    /// An answer that could not be read.
    Malformed,
    /// No connection, or it broke off.
    Transport,
    /// The bytes received are not the listed size.
    SizeMismatch,
    /// The bytes' MD5 is not the listed single-part ETag.
    EtagMismatch,
    /// The caller stopped the download.
    Cancelled,
    /// **Rust only.** The [`HttpClient`] cannot make the request (it does not
    /// implement `get_with_headers_stream`). Python's equivalent is the caller
    /// passing something that is not an httpx client, which raises there.
    Unsupported,
}

impl S3ErrorKind {
    /// The name Python's `S3ErrorKind` value carries.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            S3ErrorKind::AccessDenied => "access_denied",
            S3ErrorKind::InvalidAccessKeyId => "invalid_access_key_id",
            S3ErrorKind::SignatureDoesNotMatch => "signature_does_not_match",
            S3ErrorKind::RequestTimeTooSkewed => "request_time_too_skewed",
            S3ErrorKind::ExpiredToken => "expired_token",
            S3ErrorKind::NoSuchKey => "no_such_key",
            S3ErrorKind::NoSuchBucket => "no_such_bucket",
            S3ErrorKind::RetryLater => "retry_later",
            S3ErrorKind::Other => "other",
            S3ErrorKind::Malformed => "malformed",
            S3ErrorKind::Transport => "transport",
            S3ErrorKind::SizeMismatch => "size_mismatch",
            S3ErrorKind::EtagMismatch => "etag_mismatch",
            S3ErrorKind::Cancelled => "cancelled",
            S3ErrorKind::Unsupported => "unsupported",
        }
    }

    /// The kind S3's error `code` names, if bmlib knows it.
    fn from_code(code: &str) -> Option<Self> {
        Some(match code {
            "AccessDenied" => S3ErrorKind::AccessDenied,
            "InvalidAccessKeyId" => S3ErrorKind::InvalidAccessKeyId,
            "SignatureDoesNotMatch" => S3ErrorKind::SignatureDoesNotMatch,
            "RequestTimeTooSkewed" => S3ErrorKind::RequestTimeTooSkewed,
            "ExpiredToken" => S3ErrorKind::ExpiredToken,
            "NoSuchKey" => S3ErrorKind::NoSuchKey,
            "NoSuchBucket" => S3ErrorKind::NoSuchBucket,
            "SlowDown" | "ServiceUnavailable" | "InternalError" => S3ErrorKind::RetryLater,
            _ => return None,
        })
    }

    /// A status's kind when the body names no code (S3 sends none for some 403s).
    fn from_status(status: u16) -> Self {
        match status {
            403 => S3ErrorKind::AccessDenied,
            500 | 503 => S3ErrorKind::RetryLater,
            _ => S3ErrorKind::Other,
        }
    }
}

/// A request to S3 that did not give what was asked for.
///
/// `Display` is [`S3Error::reason`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct S3Error {
    /// What went wrong.
    pub kind: S3ErrorKind,
    /// The HTTP status, when S3 answered.
    pub status: Option<u16>,
    /// S3's error code (`AccessDenied`), when the body named one.
    pub code: Option<String>,
    /// S3's own message, or what bmlib found wrong; never a key.
    pub detail: String,
}

impl S3Error {
    /// An error of `kind` with `detail` and no status.
    #[must_use]
    pub fn new(kind: S3ErrorKind, detail: impl Into<String>) -> Self {
        S3Error {
            kind,
            status: None,
            code: None,
            detail: detail.into(),
        }
    }

    /// Whether the same request may succeed later unchanged.
    #[must_use]
    pub fn retryable(&self) -> bool {
        matches!(
            self.kind,
            S3ErrorKind::RetryLater
                | S3ErrorKind::Transport
                | S3ErrorKind::SizeMismatch
                | S3ErrorKind::EtagMismatch
        )
    }

    /// A plain-language sentence for the user, naming what to do where possible.
    #[must_use]
    pub fn reason(&self) -> String {
        let fixed = match self.kind {
            S3ErrorKind::AccessDenied => {
                "AWS refused the request (access denied). The key is not allowed to read \
                 this bucket: its IAM user needs a policy granting s3:ListBucket on the \
                 bucket and s3:GetObject on its objects, and the AWS account needs a \
                 payment method, because the bucket is requester pays."
            }
            S3ErrorKind::InvalidAccessKeyId => {
                "AWS does not know this access key ID. Check that it was copied whole \
                 and that the key is still active."
            }
            S3ErrorKind::SignatureDoesNotMatch => {
                "AWS did not accept the secret access key for this access key ID. Check \
                 that the secret was copied whole."
            }
            S3ErrorKind::RequestTimeTooSkewed => {
                "This computer's clock is more than 15 minutes away from AWS's. Set the \
                 clock to update automatically, then try again."
            }
            S3ErrorKind::ExpiredToken => {
                "The temporary AWS credentials have expired. Create new ones and enter them again."
            }
            S3ErrorKind::NoSuchKey => "The file is no longer in the bucket.",
            S3ErrorKind::NoSuchBucket => "The bucket does not exist.",
            S3ErrorKind::RetryLater => "AWS is busy and asked to try again later.",
            S3ErrorKind::Cancelled => "The download was stopped.",
            S3ErrorKind::Malformed => {
                return format!("AWS's answer could not be read: {}", self.detail)
            }
            S3ErrorKind::Transport => return format!("Could not reach AWS: {}", self.detail),
            S3ErrorKind::Unsupported => {
                return format!("This HTTP client cannot make the request: {}", self.detail)
            }
            S3ErrorKind::SizeMismatch | S3ErrorKind::EtagMismatch => {
                return format!(
                    "The downloaded file is not the one listed ({}), so it was discarded.",
                    self.detail
                )
            }
            S3ErrorKind::Other => {
                let status = self
                    .status
                    .map_or_else(|| "None".to_string(), |s| s.to_string());
                let mut said = format!("AWS answered HTTP {status}");
                if let Some(code) = &self.code {
                    said.push(' ');
                    said.push_str(code);
                }
                return if self.detail.is_empty() {
                    said
                } else {
                    format!("{said}: {}", self.detail)
                };
            }
        };
        fixed.to_string()
    }
}

impl fmt::Display for S3Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.reason())
    }
}

impl std::error::Error for S3Error {}

/// A `MALFORMED` error.
fn malformed(detail: impl Into<String>) -> S3Error {
    S3Error::new(S3ErrorKind::Malformed, detail)
}

/// One object in a listing. `etag` is without its quotes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct S3Object {
    /// The object key.
    pub key: String,
    /// Its size in bytes.
    pub size: u64,
    /// Its ETag, unquoted.
    pub etag: String,
    /// S3's timestamp, as served.
    pub last_modified: String,
}

/// One page of a ListObjectsV2 listing.
///
/// `next_continuation_token` is set exactly when `is_truncated` is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListPage {
    /// The objects, in S3's order.
    pub objects: Vec<S3Object>,
    /// Whether more pages follow.
    pub is_truncated: bool,
    /// The token for the next page.
    pub next_continuation_token: Option<String>,
}

/// An element's name, or `{ns}name` for a namespace other than S3's.
fn local(node: roxmltree::Node<'_, '_>) -> String {
    let tag = node.tag_name();
    match tag.namespace() {
        None | Some(S3_NS) => tag.name().to_string(),
        Some(ns) => format!("{{{ns}}}{}", tag.name()),
    }
}

/// Parse an S3 reply, refusing a DTD.
fn parse(body: &[u8]) -> Result<roxmltree::Document<'_>, S3Error> {
    let text = std::str::from_utf8(body).map_err(|_| malformed("not UTF-8"))?;
    if text.contains("<!DOCTYPE") {
        return Err(malformed("the reply declares a DTD"));
    }
    roxmltree::Document::parse(text).map_err(|e| malformed(format!("not XML ({e})")))
}

/// The text of `element`'s only child called `name`; `None` when absent.
fn child_text(element: roxmltree::Node<'_, '_>, name: &str) -> Result<Option<String>, S3Error> {
    let mut found = element
        .children()
        .filter(|c| c.is_element() && local(*c) == name);
    let first = found.next();
    if found.next().is_some() {
        return Err(malformed(format!("<{name}> appears twice")));
    }
    Ok(first.map(|n| n.text().unwrap_or("").to_string()))
}

/// A child's text, refusing a missing or empty one.
fn required(element: roxmltree::Node<'_, '_>, name: &str, place: &str) -> Result<String, S3Error> {
    match child_text(element, name)? {
        Some(text) if !text.is_empty() => Ok(text),
        _ => Err(malformed(format!("{place} has no <{name}>"))),
    }
}

/// Whether `text` is one or more ASCII digits.
fn is_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())
}

/// Read one ListObjectsV2 reply.
///
/// Nothing is skipped: an object without a key, size or ETag, a size that is
/// not a whole number, a `KeyCount` that disagrees with the objects delivered,
/// or a truncated page without a continuation token fails the whole page.
///
/// # Errors
///
/// [`S3ErrorKind::Malformed`] for any of the above.
pub fn parse_list_objects_v2(body: &[u8]) -> Result<ListPage, S3Error> {
    let document = parse(body)?;
    let root = document.root_element();
    if local(root) != "ListBucketResult" {
        return Err(malformed(format!(
            "expected <ListBucketResult>, got <{}>",
            local(root)
        )));
    }
    let truncated = child_text(root, "IsTruncated")?;
    let is_truncated = match truncated.as_deref() {
        Some("true") => true,
        Some("false") => false,
        other => return Err(malformed(format!("<IsTruncated> is {other:?}"))),
    };
    let mut objects = Vec::new();
    let mut prefixes = 0usize;
    for child in root.children().filter(roxmltree::Node::is_element) {
        let name = local(child);
        if name == "CommonPrefixes" {
            prefixes += 1;
        }
        if name != "Contents" {
            continue;
        }
        let key = required(child, "Key", "an object")?;
        let place = format!("object {key:?}");
        let size_text = required(child, "Size", &place)?;
        let size = if is_digits(&size_text) {
            size_text.parse::<u64>().ok()
        } else {
            None
        }
        .ok_or_else(|| malformed(format!("{place} has size {size_text:?}")))?;
        let etag = required(child, "ETag", &place)?
            .trim_matches('"')
            .to_string();
        if etag.is_empty() {
            return Err(malformed(format!("{place} has an empty <ETag>")));
        }
        let last_modified = required(child, "LastModified", &place)?;
        objects.push(S3Object {
            key,
            size,
            etag,
            last_modified,
        });
    }
    if let Some(count) = child_text(root, "KeyCount")? {
        let delivered = objects.len() + prefixes;
        match count.parse::<usize>() {
            Ok(n) if is_digits(&count) && n == delivered => {}
            Ok(_) if is_digits(&count) => {
                return Err(malformed(format!(
                    "<KeyCount> says {count}, the page holds {delivered}"
                )))
            }
            _ => return Err(malformed(format!("<KeyCount> is {count:?}"))),
        }
    }
    let token = if is_truncated {
        child_text(root, "NextContinuationToken")?
    } else {
        None
    };
    if is_truncated && token.as_deref().is_none_or(str::is_empty) {
        return Err(malformed("a truncated page has no <NextContinuationToken>"));
    }
    Ok(ListPage {
        objects,
        is_truncated,
        next_continuation_token: token,
    })
}

/// S3's `Code` and `Message` from an `<Error>` body, if it is one.
fn error_fields(body: &[u8]) -> (Option<String>, String) {
    let Ok(document) = parse(body) else {
        return (None, String::new());
    };
    let root = document.root_element();
    if local(root) != "Error" {
        return (None, String::new());
    }
    match (child_text(root, "Code"), child_text(root, "Message")) {
        (Ok(code), Ok(message)) => (
            code.filter(|c| !c.is_empty()),
            message.unwrap_or_default().trim().to_string(),
        ),
        _ => (None, String::new()),
    }
}

/// Turn a non-success reply into an [`S3Error`].
///
/// The kind comes from S3's `<Error><Code>`; a body without one falls back on
/// the status. Only `Code` and `Message` are read: an `InvalidAccessKeyId` or
/// `SignatureDoesNotMatch` body also echoes the key ID and the string to sign.
#[must_use]
pub fn classify_error(status: u16, body: &[u8]) -> S3Error {
    let (code, message) = if body.trim_ascii().is_empty() {
        (None, String::new())
    } else {
        error_fields(body)
    };
    let kind = match &code {
        Some(code) => S3ErrorKind::from_code(code).unwrap_or(S3ErrorKind::Other),
        None => S3ErrorKind::from_status(status),
    };
    S3Error {
        kind,
        status: Some(status),
        code,
        detail: message,
    }
}

/// Whether `bucket` is a DNS-safe bucket name without dots.
fn is_bucket(bucket: &str) -> bool {
    let bytes = bucket.as_bytes();
    (3..=63).contains(&bytes.len())
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-')
        && bytes[0] != b'-'
        && bytes[bytes.len() - 1] != b'-'
}

/// The virtual-hosted host name of `bucket` in `region`.
///
/// # Errors
///
/// [`SignError`] if `bucket` is not a dot-free S3 bucket name (a dot would
/// break the certificate match on the virtual-hosted name).
pub fn bucket_host(bucket: &str, region: &str) -> Result<String, SignError> {
    if !is_bucket(bucket) {
        return Err(SignError(format!(
            "not a dot-free S3 bucket name: {bucket:?}"
        )));
    }
    Ok(format!("{bucket}.s3.{region}.amazonaws.com"))
}

/// The requester-pays header, as signing takes it.
fn payer() -> Vec<(String, String)> {
    vec![(REQUESTER_PAYS.0.to_string(), REQUESTER_PAYS.1.to_string())]
}

/// Sign one ListObjectsV2 page request (requester pays, 1000 keys).
///
/// # Errors
///
/// [`SignError`] for a bad bucket or region.
pub fn list_objects_request(
    credentials: &S3Credentials,
    bucket: &str,
    region: &str,
    now: DateTime<Utc>,
    prefix: &str,
    continuation_token: Option<&str>,
) -> Result<SignedRequest, SignError> {
    let mut query = vec![
        ("list-type".to_string(), "2".to_string()),
        ("max-keys".to_string(), LIST_MAX_KEYS.to_string()),
    ];
    if !prefix.is_empty() {
        query.push(("prefix".to_string(), prefix.to_string()));
    }
    if let Some(token) = continuation_token {
        query.push(("continuation-token".to_string(), token.to_string()));
    }
    sign_get(
        &bucket_host(bucket, region)?,
        "/",
        &query,
        credentials,
        region,
        now,
        &payer(),
    )
}

/// Sign a GET of one object (requester pays).
///
/// # Errors
///
/// [`SignError`] for an empty key, a bad bucket or region.
pub fn object_request(
    credentials: &S3Credentials,
    bucket: &str,
    region: &str,
    key: &str,
    now: DateTime<Utc>,
) -> Result<SignedRequest, SignError> {
    if key.is_empty() {
        return Err(SignError("an object key must not be empty".into()));
    }
    if key.split('/').any(|s| s == "." || s == "..") {
        // Python's httpx collapses dot segments after signing; refused in both
        // ports so the two agree on which keys can be fetched.
        return Err(SignError(format!(
            "an object key must not hold a '.' or '..' segment: {key:?}"
        )));
    }
    sign_get(
        &bucket_host(bucket, region)?,
        &format!("/{key}"),
        &[],
        credentials,
        region,
        now,
        &payer(),
    )
}

/// Whether `etag` can be checked as the MD5 of the body.
///
/// A multipart upload's ETag (`…-12`) is not an MD5, so it is not checked.
#[must_use]
pub fn etag_is_md5(etag: &str) -> bool {
    etag.len() == 32
        && etag
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// A failed signing as an error the caller can show (it is bmlib's input, not S3's).
fn sign_failure(error: SignError) -> S3Error {
    S3Error::new(S3ErrorKind::Malformed, error.0)
}

/// A failed request as an [`S3Error`]: only a request that never completed
/// is `Transport` (retryable); a client that cannot make it is `Unsupported`,
/// and a body refused by the client (a `Content-Encoding`) is `Malformed`.
fn request_failure(error: &FetchError) -> S3Error {
    let kind = match error {
        FetchError::Transport(_) => S3ErrorKind::Transport,
        FetchError::Config(_) => S3ErrorKind::Unsupported,
        FetchError::Malformed(_) => S3ErrorKind::Malformed,
        FetchError::HttpStatus { .. } | FetchError::ResumeUnreadable(_) => S3ErrorKind::Other,
    };
    S3Error::new(kind, error.to_string())
}

/// A streamed body read to the end; a read that fails is `Transport`, as a
/// body breaking off is in Python.
fn read_whole(body: &mut dyn Read) -> Result<Vec<u8>, S3Error> {
    let mut bytes = Vec::new();
    body.read_to_end(&mut bytes)
        .map_err(|e| S3Error::new(S3ErrorKind::Transport, e.to_string()))?;
    Ok(bytes)
}

/// Removes the temporary file unless the download was published, also when
/// `progress` panics.
struct PartFile {
    path: PathBuf,
    published: bool,
}

impl Drop for PartFile {
    fn drop(&mut self) {
        if !self.published {
            // Never speak over the original error.
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

/// Fetch one listing page; pass the previous page's token for the next.
///
/// # Errors
///
/// [`S3Error`] for a refused request, an unreadable reply, no connection, or
/// inputs that cannot be signed (`Malformed`).
pub fn list_objects(
    client: &dyn HttpClient,
    credentials: &S3Credentials,
    bucket: &str,
    region: &str,
    prefix: &str,
    continuation_token: Option<&str>,
    now: DateTime<Utc>,
) -> Result<ListPage, S3Error> {
    let request =
        list_objects_request(credentials, bucket, region, now, prefix, continuation_token)
            .map_err(sign_failure)?;
    // Streamed, so the request goes through the client's no-redirect,
    // per-read-limited path, as a download's does.
    let mut stream = client
        .get_with_headers_stream(&request.url, &request.headers)
        .map_err(|e| request_failure(&e))?;
    let body = read_whole(&mut stream.body)?;
    if stream.status != 200 {
        return Err(classify_error(stream.status, &body));
    }
    parse_list_objects_v2(&body)
}

/// Why [`download_object`] failed.
#[derive(Debug)]
pub enum DownloadError {
    /// S3 refused, the connection failed, or the bytes were wrong.
    S3(S3Error),
    /// Writing the file failed.
    Io(std::io::Error),
}

impl fmt::Display for DownloadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DownloadError::S3(e) => write!(f, "{e}"),
            DownloadError::Io(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for DownloadError {}

impl From<S3Error> for DownloadError {
    fn from(e: S3Error) -> Self {
        DownloadError::S3(e)
    }
}

impl From<std::io::Error> for DownloadError {
    fn from(e: std::io::Error) -> Self {
        DownloadError::Io(e)
    }
}

/// Download `object` to `destination`, published only once it is verified.
///
/// The body goes to a temporary file beside `destination`, synced to disk,
/// and is renamed over `destination` only if its length is the listed size
/// and, for a single-part ETag, its MD5 is the ETag. On any failure or cancel
/// the temporary file is removed and `destination` is left as it was.
///
/// `progress(done_bytes, object.size)` is called per block; returning `false`
/// stops the download.
///
/// # Errors
///
/// [`DownloadError::S3`] with `SizeMismatch`/`EtagMismatch` for the wrong
/// bytes, `Cancelled` when `progress` stopped it, `Transport` for a lost
/// connection, or the classified refusal; [`DownloadError::Io`] when the
/// file could not be written.
#[allow(clippy::too_many_arguments)] // shortcut: mirrors Python's signature; a request struct if it grows
pub fn download_object(
    client: &dyn HttpClient,
    credentials: &S3Credentials,
    bucket: &str,
    region: &str,
    object: &S3Object,
    destination: &Path,
    now: DateTime<Utc>,
    progress: &mut dyn FnMut(u64, u64) -> bool,
) -> Result<PathBuf, DownloadError> {
    let request =
        object_request(credentials, bucket, region, &object.key, now).map_err(sign_failure)?;
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut stream = client
        .get_with_headers_stream(&request.url, &request.headers)
        .map_err(|e| request_failure(&e))?;
    if stream.status != 200 {
        let body = read_whole(&mut stream.body)?;
        return Err(classify_error(stream.status, &body).into());
    }
    // shortcut: `ureq` decodes a body stored with `Content-Encoding: gzip`
    // and hides the header, so such an object fails here as `SizeMismatch`
    // (retryable) where Python refuses it as `Malformed`; neither publishes
    // it. Read the raw body if Task M0 finds an encoded MECA package.
    let mut part = PartFile {
        path: crate::atomic::temp_path_beside(destination),
        published: false,
    };
    let result = (|| -> Result<(), DownloadError> {
        let mut out = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&part.path)?;
        let mut digest = Md5::new();
        let mut done: u64 = 0;
        let mut buffer = vec![0u8; DOWNLOAD_BLOCK_BYTES];
        loop {
            let read = match stream.body.read(&mut buffer) {
                Ok(0) => break,
                Ok(n) => n,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(S3Error::new(S3ErrorKind::Transport, e.to_string()).into()),
            };
            out.write_all(&buffer[..read])?;
            digest.update(&buffer[..read]);
            done += read as u64;
            if done > object.size {
                return Err(S3Error::new(
                    S3ErrorKind::SizeMismatch,
                    format!("more than the listed {} bytes", object.size),
                )
                .into());
            }
            if !progress(done, object.size) {
                return Err(S3Error::new(S3ErrorKind::Cancelled, "").into());
            }
        }
        out.flush()?;
        out.sync_all()?;
        drop(out);
        if done != object.size {
            return Err(S3Error::new(
                S3ErrorKind::SizeMismatch,
                format!("{done} of the listed {} bytes", object.size),
            )
            .into());
        }
        let md5: String = digest
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        if etag_is_md5(&object.etag) && md5 != object.etag {
            return Err(
                S3Error::new(S3ErrorKind::EtagMismatch, "its MD5 is not the listed ETag").into(),
            );
        }
        std::fs::rename(&part.path, destination)?;
        part.published = true;
        Ok(())
    })();
    // `part` is removed on drop unless the rename published it.
    result?;
    Ok(destination.to_path_buf())
}
