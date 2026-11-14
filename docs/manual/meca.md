# bmlib.fulltext.meca — MECA Bulk Archives over Requester-Pays S3

*(unreleased)* medRxiv and bioRxiv publish every preprint as a MECA package
(a ZIP holding the JATS XML, the PDF and the supplementary files) in the S3
buckets `s3://medrxiv-src-monthly` and `s3://biorxiv-src-monthly`. Both
buckets are **requester pays**: the AWS account whose key signs the request
pays Amazon for the transfer. This module signs those requests (AWS
Signature Version 4), lists a prefix page by page and downloads one package
to a file, checked against the listing.

Reading a downloaded `.meca` package is not here yet: no real package has
been inspected, and the module will read the manifest in the shape a sample
of real packages shows, not a guessed one.

The Rust port is `bmlib::fulltext::meca` behind the `meca` feature. Python
and Rust are held to the same cases by `tests/fixtures/s3_sigv4.json` (AWS's
published signing examples plus Python's own) and
`tests/fixtures/s3_responses.json`.

## Installation

```bash
pip install bmlib[meca]    # httpx, for list_objects() and download_object()
```

`sigv4` and the reply readers (`parse_list_objects_v2`, `classify_error`)
are standard library only.

## The AWS key

The key needs an IAM policy that allows `s3:ListBucket` on
`arn:aws:s3:::medrxiv-src-monthly` and `s3:GetObject` on
`arn:aws:s3:::medrxiv-src-monthly/*`, and the account needs a payment method.
Without the policy, S3 answers every request with `AccessDenied`.

```python
from bmlib.fulltext.meca import S3Credentials

key = S3Credentials("AKIA...", "secret...")          # session_token= for temporary keys
repr(key)   # S3Credentials(access_key_id='AKIA...'): the secret and token are never shown
```

A key ID, secret or token that is empty or holds anything but printable
ASCII without spaces (a pasted key with a trailing newline, say) raises
`ValueError`.

## Listing and downloading

```python
from pathlib import Path

import httpx

from bmlib.fulltext.meca import (
    MECA_BUCKET_REGION,
    MEDRXIV_MECA_BUCKET,
    S3Error,
    download_object,
    list_objects,
)

with httpx.Client() as client:
    token = None
    while True:
        page = list_objects(client, key, MEDRXIV_MECA_BUCKET, MECA_BUCKET_REGION,
                            prefix="Current_Content/", continuation_token=token)
        for obj in page.objects:
            if obj.key.endswith(".meca"):
                download_object(client, key, MEDRXIV_MECA_BUCKET, MECA_BUCKET_REGION,
                                obj, Path("packages") / Path(obj.key).name)
        if not page.is_truncated:
            break
        token = page.next_continuation_token   # checkpoint this to resume
```

- **Every request pays.** `x-amz-request-payer: requester` is signed into
  each one.
- **No redirect is followed** (`follow_redirects=False` on each request; Rust's
  `UreqClient` streams with redirects off): one would carry the session token
  to another host without the signature. A 3xx is reported as `OTHER`.
- **Replies are UTF-8.** A body in any other encoding, or one declaring a DTD,
  is `MALFORMED`.
- **A listing page is all or nothing.** An object without a key, size, ETag
  or modification time, a size that is not a whole number, a `KeyCount` that
  disagrees with the objects delivered, or a truncated page without a token
  raises `S3Error` (`MALFORMED`). A package silently dropped would be a paper
  silently missing.
- **A download is published only once verified.** The body streams to a
  temporary file beside the destination (`.<name>.<uuid>.part`, opened
  exclusively), is synced to disk, and replaces the destination only if its
  length is the listed size and, for a single-part ETag (32 hex digits), its
  MD5 is the ETag. A multipart ETag (`…-12`) is not an MD5 and is not
  checked. On any failure the temporary file is removed and an existing file
  at the destination is left as it was.
- **An object stored with a `Content-Encoding`** cannot be checked against the
  listing. Python refuses it (`MALFORMED`); Rust's ureq decodes it and hides
  the header, so there it fails the size check (`SIZE_MISMATCH`). Neither
  publishes it. MECA packages are ZIPs, so none is expected.
- **Time limits are per step, not per body.** Connecting, sending and each
  read must happen within `S3_TIMEOUT_SECONDS` (45 s), so a large package is
  never cut off for taking long, only for stalling. Rust's `UreqClient` does
  the same through `get_with_headers_stream` (ureq's per-read limit).
- `download_object(..., progress=f)` calls `f(done_bytes, total_bytes)` per
  block; returning exactly `False` stops it (`CANCELLED`).

## Errors

Every failure is an `S3Error` with a `kind` (`S3ErrorKind`), the HTTP
`status` and S3's `code` when there was one, and `reason()` (also its
`str()`), a sentence for the user that says what to do:

| Kind | When | `retryable` |
|---|---|---|
| `ACCESS_DENIED` | `AccessDenied`, or a 403 naming no code | no |
| `INVALID_ACCESS_KEY_ID` | AWS does not know the key ID | no |
| `SIGNATURE_DOES_NOT_MATCH` | the secret is not the key ID's | no |
| `REQUEST_TIME_TOO_SKEWED` | this computer's clock is off by over 15 minutes | no |
| `EXPIRED_TOKEN` | temporary credentials expired | no |
| `NO_SUCH_KEY` / `NO_SUCH_BUCKET` | the object / bucket is gone | no |
| `RETRY_LATER` | `SlowDown`, `ServiceUnavailable`, `InternalError`, or a 500/503 naming no code | yes |
| `OTHER` | any other refusal, kept with its code and message | no |
| `MALFORMED` | a reply that could not be read (a DTD is refused) | no |
| `TRANSPORT` | no connection, or it broke off | yes |
| `SIZE_MISMATCH` / `ETAG_MISMATCH` | the bytes are not the listed object's | yes |
| `CANCELLED` | `progress` returned `False` | no |
| `Unsupported` (Rust only) | the `HttpClient` cannot stream (`get_with_headers_stream` not implemented) | no |

Only `<Code>` and `<Message>` are read from an error body: an
`InvalidAccessKeyId` or `SignatureDoesNotMatch` body also echoes the key ID
and the string to sign, and neither belongs in a message.

Malformed inputs (a bucket name with a dot, an empty key, a key with a `.`
or `..` segment, which httpx would rewrite after signing, a header with a line
break) raise `ValueError` in Python; Rust returns `SignError`, which
`list_objects` / `download_object` report as `MALFORMED`. A file-system
failure while downloading is an `OSError` (Rust: `DownloadError::Io`).

## Signing on its own

```python
from datetime import UTC, datetime

from bmlib.fulltext.meca.sigv4 import sign_get

signed = sign_get(host="examplebucket.s3.amazonaws.com", path="/test.txt", query=[],
                  credentials=key, region="us-east-1", now=datetime.now(UTC),
                  extra_headers={"Range": "bytes=0-9"})
signed.url, signed.headers   # send these; the HTTP client writes Host from the URL
```

Only GETs with an empty body are signed. `now` must be timezone-aware.
Header names must be ASCII tokens, names that differ only by case are
refused, values are trimmed and inner runs of spaces collapsed, and the
signer's own headers (`x-amz-date`, `x-amz-content-sha256`,
`x-amz-security-token`, `host`) cannot be passed in `extra_headers`.
