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

//! AWS Signature Version 4 for S3 GET requests, as pure functions.
//!
//! Port of `bmlib/fulltext/meca/sigv4.py`. Only what reading a requester-pays
//! bucket needs: a GET with an empty body, signed in the `Authorization`
//! header. The caller passes the time. `tests/fixtures/s3_sigv4.json` holds
//! AWS's published S3 examples and Python's cases; `tests/meca_s3_contract.rs`
//! asserts both.

use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt;

/// The signing algorithm named in the `Authorization` header.
pub const ALGORITHM: &str = "AWS4-HMAC-SHA256";

/// The service name in the credential scope.
pub const SERVICE: &str = "s3";

/// `x-amz-content-sha256` of a GET: the SHA-256 of the empty body.
pub const EMPTY_PAYLOAD_SHA256: &str =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

/// Why a request could not be signed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignError(pub String);

impl fmt::Display for SignError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SignError {}

/// Whether `value` is printable ASCII without spaces, as every AWS key is.
fn is_key_text(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|b| (b'!'..=b'~').contains(&b))
}

/// An AWS access key, its secret and, for temporary keys, the session token.
///
/// `Debug` shows only the key ID, so a credential logged or shown in a panic
/// gives away nothing else.
#[derive(Clone, PartialEq, Eq)]
pub struct S3Credentials {
    access_key_id: String,
    secret_access_key: String,
    session_token: Option<String>,
}

impl S3Credentials {
    /// Build credentials.
    ///
    /// # Errors
    ///
    /// [`SignError`] if a field is empty or holds anything but printable ASCII
    /// without spaces (a pasted key with a trailing newline included).
    pub fn new(
        access_key_id: &str,
        secret_access_key: &str,
        session_token: Option<&str>,
    ) -> Result<Self, SignError> {
        if !is_key_text(access_key_id) {
            return Err(SignError(
                "access_key_id must be printable ASCII without spaces".into(),
            ));
        }
        if !is_key_text(secret_access_key) {
            return Err(SignError(
                "secret_access_key must be printable ASCII without spaces".into(),
            ));
        }
        if session_token.is_some_and(|t| !is_key_text(t)) {
            return Err(SignError(
                "session_token must be printable ASCII without spaces".into(),
            ));
        }
        Ok(S3Credentials {
            access_key_id: access_key_id.to_string(),
            secret_access_key: secret_access_key.to_string(),
            session_token: session_token.map(str::to_string),
        })
    }

    /// The access key ID.
    #[must_use]
    pub fn access_key_id(&self) -> &str {
        &self.access_key_id
    }
}

impl fmt::Debug for S3Credentials {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("S3Credentials")
            .field("access_key_id", &self.access_key_id)
            .finish_non_exhaustive()
    }
}

/// A signed GET: where to send it, what to send, and how it was signed.
///
/// `headers` are the ones to send (`Host` excepted, which the HTTP client
/// writes from the URL); `Debug` leaves them out, since a session token
/// travels in one of them.
#[derive(Clone, PartialEq, Eq)]
pub struct SignedRequest {
    /// The `https` URL.
    pub url: String,
    /// The headers to send, names lower-case.
    pub headers: BTreeMap<String, String>,
    /// The canonical request that was signed.
    pub canonical_request: String,
    /// The string that was signed.
    pub string_to_sign: String,
    /// The hex signature.
    pub signature: String,
}

impl fmt::Debug for SignedRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SignedRequest")
            .field("url", &self.url)
            .field("signature", &self.signature)
            .finish_non_exhaustive()
    }
}

/// Percent-encode `value` the way SigV4 canonicalises it.
///
/// Every byte of the UTF-8 encoding is `%XY` (upper-case hex) except letters,
/// digits and `-_.~`; `/` is kept unless `encode_slash`.
#[must_use]
pub fn uri_encode(value: &str, encode_slash: bool) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        let keep = byte.is_ascii_alphanumeric()
            || matches!(byte, b'-' | b'_' | b'.' | b'~')
            || (byte == b'/' && !encode_slash);
        if keep {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// The canonical query string: each pair encoded, then sorted.
///
/// A key without a value (`?lifecycle`) is `("lifecycle", "")` and comes out
/// as `lifecycle=`.
#[must_use]
pub fn canonical_query(params: &[(String, String)]) -> String {
    let mut encoded: Vec<(String, String)> = params
        .iter()
        .map(|(k, v)| (uri_encode(k, true), uri_encode(v, true)))
        .collect();
    encoded.sort();
    encoded
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("&")
}

/// A header value trimmed of spaces at both ends, inner runs collapsed to one.
fn normalise_value(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut space = false;
    for c in value.trim_matches(' ').chars() {
        if c == ' ' {
            if !space {
                out.push(' ');
            }
            space = true;
        } else {
            out.push(c);
            space = false;
        }
    }
    out
}

/// Whether `c` would split or corrupt a header line.
fn is_control(c: char) -> bool {
    (c as u32) < 0x20 || c as u32 == 0x7F
}

/// The canonical header block and the signed-headers list.
///
/// Headers are given as pairs, in the caller's order, so two names differing
/// only by case can be seen and refused.
///
/// # Errors
///
/// [`SignError`] if two names differ only by case, or a name or value holds a
/// line break or other control character.
pub fn canonical_headers(headers: &[(String, String)]) -> Result<(String, String), SignError> {
    let mut lowered: BTreeMap<String, String> = BTreeMap::new();
    for (name, value) in headers {
        let key = name.to_lowercase();
        if lowered.contains_key(&key) {
            return Err(SignError(format!("header {name:?} is given twice")));
        }
        if key.is_empty()
            || key
                .chars()
                .any(|c| (c as u32) < 0x21 || (c as u32) > 0x7E || c == ':')
        {
            return Err(SignError(format!("header name {name:?} is not a token")));
        }
        if value.chars().any(is_control) {
            return Err(SignError(format!(
                "header {name:?} holds a control character"
            )));
        }
        lowered.insert(key, normalise_value(value));
    }
    let block: String = lowered.iter().map(|(n, v)| format!("{n}:{v}\n")).collect();
    let signed = lowered.keys().cloned().collect::<Vec<_>>().join(";");
    Ok((block, signed))
}

/// A GET's canonical request and its signed-headers list.
///
/// `path` is decoded and starts with `/`; it is encoded here.
///
/// # Errors
///
/// [`SignError`] if `path` does not start with `/`, or a header is refused by
/// [`canonical_headers`].
pub fn canonical_request(
    path: &str,
    query: &[(String, String)],
    headers: &[(String, String)],
    payload_hash: &str,
) -> Result<(String, String), SignError> {
    if !path.starts_with('/') {
        return Err(SignError(format!("path must start with '/': {path:?}")));
    }
    let (block, signed) = canonical_headers(headers)?;
    let request = [
        "GET".to_string(),
        uri_encode(path, false),
        canonical_query(query),
        block,
        signed.clone(),
        payload_hash.to_string(),
    ]
    .join("\n");
    Ok((request, signed))
}

/// `now` as SigV4's `YYYYMMDDTHHMMSSZ`.
#[must_use]
pub fn amz_date(now: DateTime<Utc>) -> String {
    now.format("%Y%m%dT%H%M%SZ").to_string()
}

/// The credential scope for `date` (`YYYYMMDD`) and `region`.
#[must_use]
pub fn credential_scope(date: &str, region: &str) -> String {
    format!("{date}/{region}/{SERVICE}/aws4_request")
}

/// Lower-case hex of `bytes`.
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The string to sign for a canonical `request`.
#[must_use]
pub fn string_to_sign(timestamp: &str, scope: &str, request: &str) -> String {
    let digest = hex(&Sha256::digest(request.as_bytes()));
    format!("{ALGORITHM}\n{timestamp}\n{scope}\n{digest}")
}

/// HMAC-SHA256 of `data` under `key`.
fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    // `new_from_slice` refuses no key length for HMAC; the `expect` documents it.
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("HMAC takes a key of any length");
    mac.update(data);
    mac.finalize().into_bytes().to_vec()
}

/// The SigV4 signing key for `date` (`YYYYMMDD`) and `region`.
#[must_use]
pub fn signing_key(secret_access_key: &str, date: &str, region: &str) -> Vec<u8> {
    let mut key = format!("AWS4{secret_access_key}").into_bytes();
    for part in [date, region, SERVICE, "aws4_request"] {
        key = hmac_sha256(&key, part.as_bytes());
    }
    key
}

/// Whether `host` is a bare host name (no scheme, port or path).
fn is_host(host: &str) -> bool {
    !host.is_empty()
        && host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
}

/// Whether `region` is lower-case letters, digits and `-`.
fn is_region(region: &str) -> bool {
    !region.is_empty()
        && region
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// Sign a GET with an empty body for S3.
///
/// Adds `x-amz-date`, `x-amz-content-sha256` and, for temporary keys,
/// `x-amz-security-token`, and signs them with `host` and `extra_headers`
/// (`x-amz-request-payer`, `range`), given as pairs in the caller's order.
///
/// # Errors
///
/// [`SignError`] on a malformed host, region or path, a header refused by
/// [`canonical_headers`], or an extra header that would replace one this
/// function sets.
pub fn sign_get(
    host: &str,
    path: &str,
    query: &[(String, String)],
    credentials: &S3Credentials,
    region: &str,
    now: DateTime<Utc>,
    extra_headers: &[(String, String)],
) -> Result<SignedRequest, SignError> {
    if !is_host(host) {
        return Err(SignError(format!(
            "host must be a bare host name: {host:?}"
        )));
    }
    if !is_region(region) {
        return Err(SignError(format!(
            "region must be lower-case letters, digits and '-': {region:?}"
        )));
    }
    let timestamp = amz_date(now);
    let date = &timestamp[..8];
    let mut own = vec![
        ("host".to_string(), host.to_string()),
        (
            "x-amz-content-sha256".to_string(),
            EMPTY_PAYLOAD_SHA256.to_string(),
        ),
        ("x-amz-date".to_string(), timestamp.clone()),
    ];
    if let Some(token) = &credentials.session_token {
        own.push(("x-amz-security-token".to_string(), token.clone()));
    }
    let mut headers = own.clone();
    for (name, value) in extra_headers {
        if own.iter().any(|(n, _)| *n == name.to_lowercase()) {
            return Err(SignError(format!("header {name:?} is set by the signer")));
        }
        headers.push((name.clone(), value.clone()));
    }
    let (request, signed) = canonical_request(path, query, &headers, EMPTY_PAYLOAD_SHA256)?;
    let scope = credential_scope(date, region);
    let to_sign = string_to_sign(&timestamp, &scope, &request);
    let signature = hex(&hmac_sha256(
        &signing_key(&credentials.secret_access_key, date, region),
        to_sign.as_bytes(),
    ));
    let mut sent: BTreeMap<String, String> = headers
        .iter()
        .filter(|(name, _)| name != "host")
        .map(|(name, value)| (name.to_lowercase(), normalise_value(value)))
        .collect();
    sent.insert(
        "authorization".to_string(),
        format!(
            "{ALGORITHM} Credential={}/{scope},SignedHeaders={signed},Signature={signature}",
            credentials.access_key_id
        ),
    );
    let mut url = format!("https://{host}{}", uri_encode(path, false));
    if !query.is_empty() {
        url.push('?');
        url.push_str(&canonical_query(query));
    }
    Ok(SignedRequest {
        url,
        headers: sent,
        canonical_request: request,
        string_to_sign: to_sign,
        signature,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn debug_shows_only_the_key_id() {
        let key = S3Credentials::new("AKIDEXAMPLE", "SECRETVALUE", Some("TOKENVALUE")).unwrap();
        let text = format!("{key:?}");
        assert!(text.contains("AKIDEXAMPLE"));
        assert!(!text.contains("SECRETVALUE") && !text.contains("TOKENVALUE"));
    }

    #[test]
    fn a_value_keeps_inner_single_spaces() {
        assert_eq!(normalise_value("  a   b c  "), "a b c");
    }

    #[test]
    fn non_ascii_is_encoded_byte_by_byte() {
        assert_eq!(uri_encode("ü/ x", false), "%C3%BC/%20x");
        assert_eq!(uri_encode("ü/ x", true), "%C3%BC%2F%20x");
    }
}
