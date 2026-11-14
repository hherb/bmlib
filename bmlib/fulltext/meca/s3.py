# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Read a requester-pays S3 bucket: list a prefix, download one object.

The pure half (request building, :func:`parse_list_objects_v2`,
:func:`classify_error`) is stdlib only and held to the Rust port
(``fulltext::meca::s3``) by ``tests/fixtures/s3_responses.json`` and
``tests/fixtures/s3_sigv4.json``. The transport (:func:`list_objects`,
:func:`download_object`) takes an ``httpx.Client`` the caller owns
(``bmlib[meca]``); this module never imports httpx itself.

Every request carries ``x-amz-request-payer: requester``: the caller's AWS
account pays for what it reads, which is the only way medRxiv's and bioRxiv's
MECA buckets can be read at all.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from bmlib.fulltext.meca.sigv4 import S3Credentials, SignedRequest, sign_get

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

MEDRXIV_MECA_BUCKET = "medrxiv-src-monthly"
"""medRxiv's MECA archive (requester pays)."""

BIORXIV_MECA_BUCKET = "biorxiv-src-monthly"
"""bioRxiv's MECA archive (requester pays)."""

MECA_BUCKET_REGION = "us-east-1"
"""The region both MECA buckets are in."""

REQUESTER_PAYS = ("x-amz-request-payer", "requester")
"""The header that makes the caller's account pay for a request."""

LIST_MAX_KEYS = 1000
"""Objects per listing page: S3's maximum."""

S3_TIMEOUT_SECONDS = 45.0
"""Each step's limit: connecting, sending, and every read (an idle limit, not a
whole-body one, so a large object is not cut off). The Rust port's
``DEFAULT_TIMEOUT_SECONDS``."""

DOWNLOAD_BLOCK_BYTES = 1 << 20
"""Bytes read per block while downloading."""

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
_SINGLE_PART_ETAG = re.compile(r"[0-9a-f]{32}")
_DIGITS = re.compile(r"[0-9]+")
_MAX_SIZE = (1 << 64) - 1
"""The largest size the Rust port's ``u64`` holds; a larger one is refused in both."""


class S3ErrorKind(Enum):
    """What went wrong, in the terms a user can act on."""

    ACCESS_DENIED = "access_denied"
    INVALID_ACCESS_KEY_ID = "invalid_access_key_id"
    SIGNATURE_DOES_NOT_MATCH = "signature_does_not_match"
    REQUEST_TIME_TOO_SKEWED = "request_time_too_skewed"
    EXPIRED_TOKEN = "expired_token"
    NO_SUCH_KEY = "no_such_key"
    NO_SUCH_BUCKET = "no_such_bucket"
    RETRY_LATER = "retry_later"
    OTHER = "other"
    MALFORMED = "malformed"
    TRANSPORT = "transport"
    SIZE_MISMATCH = "size_mismatch"
    ETAG_MISMATCH = "etag_mismatch"
    CANCELLED = "cancelled"


_CODE_KINDS = {
    "AccessDenied": S3ErrorKind.ACCESS_DENIED,
    "InvalidAccessKeyId": S3ErrorKind.INVALID_ACCESS_KEY_ID,
    "SignatureDoesNotMatch": S3ErrorKind.SIGNATURE_DOES_NOT_MATCH,
    "RequestTimeTooSkewed": S3ErrorKind.REQUEST_TIME_TOO_SKEWED,
    "ExpiredToken": S3ErrorKind.EXPIRED_TOKEN,
    "NoSuchKey": S3ErrorKind.NO_SUCH_KEY,
    "NoSuchBucket": S3ErrorKind.NO_SUCH_BUCKET,
    "SlowDown": S3ErrorKind.RETRY_LATER,
    "ServiceUnavailable": S3ErrorKind.RETRY_LATER,
    "InternalError": S3ErrorKind.RETRY_LATER,
    "RequestTimeout": S3ErrorKind.RETRY_LATER,
}

_STATUS_KINDS = {
    403: S3ErrorKind.ACCESS_DENIED,
    500: S3ErrorKind.RETRY_LATER,
    502: S3ErrorKind.RETRY_LATER,
    503: S3ErrorKind.RETRY_LATER,
    504: S3ErrorKind.RETRY_LATER,
}
"""A status's kind when the body names no code (S3 sends none for some 403s), and
a 5xx's when its code is one bmlib does not know."""

_RETRYABLE = frozenset({S3ErrorKind.RETRY_LATER, S3ErrorKind.TRANSPORT})
"""Not ``SIZE_MISMATCH``/``ETAG_MISMATCH``: a body that breaks off is ``TRANSPORT``,
so the wrong bytes arrived whole, and the same request would pay for them again."""

_REASONS = {
    S3ErrorKind.ACCESS_DENIED: (
        "AWS refused the request (access denied). The key is not allowed to read "
        "this bucket: its IAM user needs a policy granting s3:ListBucket on the "
        "bucket and s3:GetObject on its objects, and the AWS account needs a "
        "payment method, because the bucket is requester pays."
    ),
    S3ErrorKind.INVALID_ACCESS_KEY_ID: (
        "AWS does not know this access key ID. Check that it was copied whole "
        "and that the key is still active."
    ),
    S3ErrorKind.SIGNATURE_DOES_NOT_MATCH: (
        "AWS did not accept the secret access key for this access key ID. Check "
        "that the secret was copied whole."
    ),
    S3ErrorKind.REQUEST_TIME_TOO_SKEWED: (
        "This computer's clock is more than 15 minutes away from AWS's. Set the "
        "clock to update automatically, then try again."
    ),
    S3ErrorKind.EXPIRED_TOKEN: (
        "The temporary AWS credentials have expired. Create new ones and enter them again."
    ),
    S3ErrorKind.NO_SUCH_KEY: "The file is no longer in the bucket.",
    S3ErrorKind.NO_SUCH_BUCKET: "The bucket does not exist.",
    S3ErrorKind.RETRY_LATER: "AWS is busy and asked to try again later.",
    S3ErrorKind.CANCELLED: "The download was stopped.",
}


class S3Error(Exception):
    """A request to S3 that did not give what was asked for.

    Attributes:
        kind: What went wrong.
        status: The HTTP status, when S3 answered.
        code: S3's error code (``AccessDenied``), when the body named one.
        detail: S3's own message, or what bmlib found wrong; never a key.
    """

    def __init__(
        self,
        kind: S3ErrorKind,
        detail: str = "",
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        """Build the error; ``str()`` is :meth:`reason`."""
        # ``args`` are the positional fields, so a pickled error is rebuilt whole
        # (``status`` and ``code`` come back with ``__dict__``).
        super().__init__(kind, detail)
        self.kind = kind
        self.status = status
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        """Return :meth:`reason`."""
        return self.reason()

    @property
    def retryable(self) -> bool:
        """Whether the same request may succeed later unchanged."""
        return self.kind in _RETRYABLE

    def reason(self) -> str:
        """A plain-language sentence for the user, naming what to do where possible."""
        fixed = _REASONS.get(self.kind)
        if fixed is not None:
            return fixed
        if self.kind is S3ErrorKind.MALFORMED:
            return f"AWS's answer could not be read: {self.detail}"
        if self.kind is S3ErrorKind.TRANSPORT:
            return f"Could not reach AWS: {self.detail}"
        if self.kind in (S3ErrorKind.SIZE_MISMATCH, S3ErrorKind.ETAG_MISMATCH):
            return (
                f"The downloaded file is not the one listed ({self.detail}), so it was "
                "discarded. The file may have changed since it was listed: list it again."
            )
        said = f"AWS answered HTTP {self.status}"
        if self.code:
            said += f" {self.code}"
        return f"{said}: {self.detail}" if self.detail else said


@dataclass(frozen=True)
class S3Object:
    """One object in a listing.

    ``etag`` is without its quotes. ``last_modified`` is S3's timestamp as served.
    """

    key: str
    size: int
    etag: str
    last_modified: str


@dataclass(frozen=True)
class ListPage:
    """One page of a ListObjectsV2 listing.

    ``next_continuation_token`` is set exactly when ``is_truncated`` is.
    """

    objects: tuple[S3Object, ...]
    is_truncated: bool
    next_continuation_token: str | None

    def __post_init__(self) -> None:
        """Refuse a token without truncation, or truncation without a token."""
        if self.is_truncated != bool(self.next_continuation_token):
            raise ValueError("next_continuation_token is set exactly when is_truncated is")


def _local(tag: object) -> str:
    """Return *tag* without its namespace; ``""`` for a comment or processing instruction."""
    if not isinstance(tag, str):
        return ""
    return tag[len(_S3_NS) :] if tag.startswith(_S3_NS) else tag


def _parse(body: bytes) -> ET.Element:
    """Parse an S3 reply: UTF-8 only, and no DTD (as the Rust port's parser reads it).

    S3 sends UTF-8. The body is decoded here, strictly, rather than handed to
    expat as bytes, which would honour any encoding the body declares (a UTF-16
    body would slip a DTD past the check below, and Rust would refuse it).
    Comments and processing instructions are kept in the tree, so a field
    holding one is seen and refused (:func:`_child_text`), as Rust sees it.

    Raises:
        S3Error: ``MALFORMED`` if the body is not UTF-8, not XML, or declares a DTD.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise S3Error(S3ErrorKind.MALFORMED, "not UTF-8") from None
    if "<!DOCTYPE" in text:
        raise S3Error(S3ErrorKind.MALFORMED, "the reply declares a DTD")
    try:
        builder = ET.TreeBuilder(insert_comments=True, insert_pis=True)
        return ET.fromstring(text, parser=ET.XMLParser(target=builder))
    except ET.ParseError as exc:
        raise S3Error(S3ErrorKind.MALFORMED, f"not XML ({exc})") from None


def _child_text(element: ET.Element, name: str) -> str | None:
    """Return the text of *element*'s only child called *name*, or ``None``.

    Raises:
        S3Error: ``MALFORMED`` if there are two, or the child holds anything but
            text (an element, a comment or a processing instruction).
    """
    found = [c for c in element if _local(c.tag) == name]
    if len(found) > 1:
        raise S3Error(S3ErrorKind.MALFORMED, f"<{name}> appears twice")
    if not found:
        return None
    if len(found[0]):
        raise S3Error(S3ErrorKind.MALFORMED, f"<{name}> holds markup, not only text")
    return found[0].text or ""


def _required(element: ET.Element, name: str, where: str) -> str:
    """Return a child's text, refusing a missing or empty one."""
    text = _child_text(element, name)
    if not text:
        raise S3Error(S3ErrorKind.MALFORMED, f"{where} has no <{name}>")
    return text


def parse_list_objects_v2(body: bytes) -> ListPage:
    """Read one ListObjectsV2 reply.

    Nothing is skipped: an object without a key, size, ETag or modification
    time, a size that is not a whole number (or too large for a ``u64``), a
    ``KeyCount`` that disagrees with the objects delivered, or a truncated page
    without a continuation token fails the whole page, because a package
    silently dropped is a paper silently missing.

    Raises:
        S3Error: ``MALFORMED`` for any of the above.
    """
    root = _parse(body)
    if _local(root.tag) != "ListBucketResult":
        raise S3Error(
            S3ErrorKind.MALFORMED, f"expected <ListBucketResult>, got <{_local(root.tag)}>"
        )
    truncated_text = _child_text(root, "IsTruncated")
    if truncated_text not in ("true", "false"):
        raise S3Error(S3ErrorKind.MALFORMED, f"<IsTruncated> is {truncated_text!r}")
    is_truncated = truncated_text == "true"
    objects = []
    prefixes = 0
    for child in root:
        name = _local(child.tag)
        if name == "CommonPrefixes":
            prefixes += 1
        if name != "Contents":
            continue
        key = _required(child, "Key", "an object")
        size_text = _required(child, "Size", f"object {key!r}")
        if not _DIGITS.fullmatch(size_text) or int(size_text) > _MAX_SIZE:
            raise S3Error(S3ErrorKind.MALFORMED, f"object {key!r} has size {size_text!r}")
        etag = _required(child, "ETag", f"object {key!r}").strip('"')
        if not etag:
            raise S3Error(S3ErrorKind.MALFORMED, f"object {key!r} has an empty <ETag>")
        modified = _required(child, "LastModified", f"object {key!r}")
        objects.append(S3Object(key=key, size=int(size_text), etag=etag, last_modified=modified))
    key_count = _child_text(root, "KeyCount")
    if key_count is not None:
        if not _DIGITS.fullmatch(key_count):
            raise S3Error(S3ErrorKind.MALFORMED, f"<KeyCount> is {key_count!r}")
        if int(key_count) != len(objects) + prefixes:
            raise S3Error(
                S3ErrorKind.MALFORMED,
                f"<KeyCount> says {key_count}, the page holds {len(objects) + prefixes}",
            )
    token = _child_text(root, "NextContinuationToken") if is_truncated else None
    if is_truncated and not token:
        raise S3Error(S3ErrorKind.MALFORMED, "a truncated page has no <NextContinuationToken>")
    return ListPage(
        objects=tuple(objects), is_truncated=is_truncated, next_continuation_token=token
    )


def classify_error(status: int, body: bytes) -> S3Error:
    """Turn a non-success reply into an :class:`S3Error`.

    The kind comes from S3's ``<Error><Code>``; a body without one (S3 sends
    none for some 403s, a proxy may send HTML) falls back on the status, and so
    does a 5xx whose code bmlib does not know (it is still a server failing).
    Only
    ``Code`` and ``Message`` are read: an ``InvalidAccessKeyId`` or
    ``SignatureDoesNotMatch`` body also echoes the key ID and the string to
    sign, and neither belongs in a message.
    """
    code: str | None = None
    message = ""
    if body.strip():
        try:
            root = _parse(body)
        except S3Error:
            root = None
        if root is not None and _local(root.tag) == "Error":
            try:
                code = _child_text(root, "Code") or None
                message = (_child_text(root, "Message") or "").strip()
            except S3Error:
                code, message = None, ""
    kind = _CODE_KINDS.get(code) if code else None
    if kind is None:
        by_status = code is None or status >= 500
        kind = _STATUS_KINDS.get(status, S3ErrorKind.OTHER) if by_status else S3ErrorKind.OTHER
    return S3Error(kind, message, status=status, code=code)


def bucket_host(bucket: str, region: str) -> str:
    """Return the virtual-hosted host name of *bucket* in *region*.

    Raises:
        ValueError: If *bucket* is not a DNS-safe bucket name without dots (a dot
            would break the certificate match on the virtual-hosted name).
    """
    if not _BUCKET.fullmatch(bucket):
        raise ValueError(f"not a dot-free S3 bucket name: {bucket!r}")
    return f"{bucket}.s3.{region}.amazonaws.com"


def list_objects_request(
    credentials: S3Credentials,
    bucket: str,
    region: str,
    now: datetime,
    prefix: str = "",
    continuation_token: str | None = None,
) -> SignedRequest:
    """Sign one ListObjectsV2 page request (requester pays, 1000 keys)."""
    query = [("list-type", "2"), ("max-keys", str(LIST_MAX_KEYS))]
    if prefix:
        query.append(("prefix", prefix))
    if continuation_token is not None:
        query.append(("continuation-token", continuation_token))
    return sign_get(
        host=bucket_host(bucket, region),
        path="/",
        query=query,
        credentials=credentials,
        region=region,
        now=now,
        extra_headers=dict([REQUESTER_PAYS]),
    )


def object_request(
    credentials: S3Credentials, bucket: str, region: str, key: str, now: datetime
) -> SignedRequest:
    """Sign a GET of one object (requester pays).

    Raises:
        ValueError: If *key* is empty or holds a ``.``/``..`` segment, *bucket* is
            not a dot-free bucket name, or :func:`~.sigv4.sign_get` refuses the
            region.
    """
    if not key:
        raise ValueError("an object key must not be empty")
    if any(segment in (".", "..") for segment in key.split("/")):
        # httpx collapses dot segments after signing, so the path sent would not
        # be the path signed (SignatureDoesNotMatch, read as a bad secret).
        raise ValueError(f"an object key must not hold a '.' or '..' segment: {key!r}")
    return sign_get(
        host=bucket_host(bucket, region),
        path="/" + key,
        query=[],
        credentials=credentials,
        region=region,
        now=now,
        extra_headers=dict([REQUESTER_PAYS]),
    )


def etag_is_md5(etag: str) -> bool:
    """Whether *etag* can be checked as the MD5 of the body.

    A multipart upload's ETag (``…-12``) is not an MD5, so it is not checked.
    """
    return bool(_SINGLE_PART_ETAG.fullmatch(etag))


def _now(now: datetime | None) -> datetime:
    """Return *now*, or the current UTC time."""
    return now if now is not None else datetime.now(UTC)


def _httpx() -> ModuleType:
    """Return httpx, or say which extra installs it.

    Raises:
        ImportError: If httpx is not installed.
    """
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(f"Install with: pip install bmlib[meca] ({exc})") from exc
    return httpx


def _content_encoding_refused(encoding: str | None) -> S3Error | None:
    """``MALFORMED`` for a body stored with a ``Content-Encoding``.

    The listing's size and ETag describe the stored bytes, which ``iter_raw``
    reads; refused anyway, as the Rust port refuses it: its ureq decodes a gzip
    body and hides the header, so there a gzip object fails the size check (a
    divergence noted at its call site), and any other encoding is refused as
    here. Neither port publishes such an object.
    """
    if encoding is None or encoding.strip().lower() in ("", "identity"):
        return None
    return S3Error(
        S3ErrorKind.MALFORMED,
        f"the object is stored with Content-Encoding {encoding.strip()!r}, "
        "so its bytes cannot be checked against the listing",
    )


def _request_error(httpx: ModuleType, exc: Exception) -> S3Error:
    """An httpx failure, naming its type: ``MALFORMED`` for a body httpx could not
    decode (a broken ``Content-Encoding``), else ``TRANSPORT``."""
    kind = S3ErrorKind.MALFORMED if isinstance(exc, httpx.DecodingError) else S3ErrorKind.TRANSPORT
    return S3Error(kind, f"{type(exc).__name__}: {exc}")


def _discard(part: Path) -> None:
    """Remove a temporary file; a failure is logged, never raised over the
    original error."""
    try:
        part.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove the temporary file %s", part, exc_info=True)


def list_objects(
    client: httpx.Client,
    credentials: S3Credentials,
    bucket: str,
    region: str,
    prefix: str = "",
    continuation_token: str | None = None,
    *,
    now: datetime | None = None,
) -> ListPage:
    """Fetch one listing page; pass the previous page's token for the next.

    Raises:
        S3Error: For a refused request, an unreadable reply or no connection.
        ValueError: If the bucket or region cannot be signed for.
    """
    httpx = _httpx()
    request = list_objects_request(
        credentials, bucket, region, _now(now), prefix, continuation_token
    )
    try:
        # No redirect is followed: httpx would carry the session token to the new host.
        response = client.get(
            request.url,
            headers=request.headers,
            timeout=S3_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except (httpx.TransportError, httpx.DecodingError) as exc:
        raise _request_error(httpx, exc) from exc
    if response.status_code != 200:
        raise classify_error(response.status_code, response.content)
    return parse_list_objects_v2(response.content)


def download_object(
    client: httpx.Client,
    credentials: S3Credentials,
    bucket: str,
    region: str,
    obj: S3Object,
    destination: Path,
    *,
    now: datetime | None = None,
    progress: Callable[[int, int], bool | None] | None = None,
) -> Path:
    """Download *obj* to *destination*, published only once it is verified.

    The body goes to a temporary file beside *destination*
    (``.<name>.<uuid>.part``, opened exclusively), synced to disk, and is
    renamed over *destination* only if its length is the listed size and, for a
    single-part ETag, its MD5 is the ETag. On any failure or cancel the
    temporary file is removed and *destination* is left as it was. (The Rust
    port names its temporary file differently.)

    Args:
        progress: Called as ``progress(done_bytes, obj.size)`` per block;
            returning exactly ``False`` stops the download.

    Raises:
        S3Error: ``SIZE_MISMATCH``/``ETAG_MISMATCH`` for the wrong bytes,
            ``CANCELLED`` when *progress* stopped it, ``TRANSPORT`` for a lost
            connection, ``MALFORMED`` for a body stored with a
            ``Content-Encoding``, or the classified refusal.
        ValueError: If *obj*'s ETag still carries its quotes (its MD5 could not
            be checked), or :func:`object_request` refuses the request.
        OSError: On a file-system failure.
    """
    httpx = _httpx()
    if '"' in obj.etag:
        raise ValueError(f"an ETag is given without its quotes: {obj.etag!r}")
    request = object_request(credentials, bucket, region, obj.key, _now(now))
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.part"
    try:
        digest = hashlib.md5(usedforsecurity=False)
        done = 0
        with client.stream(
            "GET",
            request.url,
            headers=request.headers,
            timeout=S3_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise classify_error(response.status_code, response.read())
            refused = _content_encoding_refused(response.headers.get("content-encoding"))
            if refused is not None:
                raise refused
            with part.open("xb") as out:
                for block in response.iter_raw(DOWNLOAD_BLOCK_BYTES):
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    if done > obj.size:
                        raise S3Error(
                            S3ErrorKind.SIZE_MISMATCH, f"more than the listed {obj.size} bytes"
                        )
                    if progress is not None and progress(done, obj.size) is False:
                        raise S3Error(S3ErrorKind.CANCELLED)
                out.flush()
                os.fsync(out.fileno())
        if done != obj.size:
            raise S3Error(S3ErrorKind.SIZE_MISMATCH, f"{done} of the listed {obj.size} bytes")
        if etag_is_md5(obj.etag) and digest.hexdigest() != obj.etag:
            raise S3Error(S3ErrorKind.ETAG_MISMATCH, "its MD5 is not the listed ETag")
        os.replace(part, destination)
    except (httpx.TransportError, httpx.DecodingError) as exc:
        _discard(part)
        raise _request_error(httpx, exc) from exc
    except BaseException:
        _discard(part)
        raise
    return destination
