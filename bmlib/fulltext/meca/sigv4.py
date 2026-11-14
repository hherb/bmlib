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

"""AWS Signature Version 4 for S3 GET requests, as pure functions.

Only what reading a requester-pays bucket needs: a GET with an empty body,
signed in the ``Authorization`` header. The caller passes the time, so a
signature is a function of its inputs alone; ``tests/fixtures/s3_sigv4.json``
holds AWS's published S3 examples and the Rust port (``fulltext::meca::sigv4``)
asserts the same cases.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"
"""The signing algorithm named in the ``Authorization`` header."""

SERVICE = "s3"
"""The service name in the credential scope."""

EMPTY_PAYLOAD_SHA256 = hashlib.sha256(b"").hexdigest()
"""``x-amz-content-sha256`` of a GET: the SHA-256 of the empty body."""

_SAFE_IN_SEGMENT = "-_.~"
"""Characters S3 leaves unencoded besides letters and digits (RFC 3986 unreserved)."""

_REGION = re.compile(r"[a-z0-9-]+")
_HOST = re.compile(r"[A-Za-z0-9.-]+")
_KEY_TEXT = re.compile(r"[!-~]+")
"""A key id, secret or token: printable ASCII, no spaces."""

_SPACE_RUN = re.compile(r" +")


@dataclass(frozen=True)
class S3Credentials:
    """An AWS access key, its secret and, for temporary keys, the session token.

    The secret and the token never appear in ``repr``, so a credential logged or
    shown in a traceback gives away only the key id.

    Raises:
        ValueError: If a field is empty or holds anything but printable ASCII
            without spaces (a pasted key with a trailing newline included).
    """

    access_key_id: str
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Refuse a key that could not have been issued by AWS."""
        for name, value in (
            ("access_key_id", self.access_key_id),
            ("secret_access_key", self.secret_access_key),
        ):
            if not isinstance(value, str) or not _KEY_TEXT.fullmatch(value):
                raise ValueError(f"{name} must be printable ASCII without spaces")
        if self.session_token is not None and (
            not isinstance(self.session_token, str) or not _KEY_TEXT.fullmatch(self.session_token)
        ):
            raise ValueError("session_token must be printable ASCII without spaces")


@dataclass(frozen=True)
class SignedRequest:
    """A signed GET: where to send it, what to send, and how it was signed.

    ``headers`` are the ones to send (``Host`` excepted, which the HTTP client
    writes from the URL). They are kept out of ``repr`` because a session token
    travels in one of them.
    """

    url: str
    headers: dict[str, str] = field(repr=False)
    canonical_request: str = field(repr=False)
    string_to_sign: str = field(repr=False)
    signature: str


def uri_encode(value: str, *, encode_slash: bool) -> str:
    """Percent-encode *value* the way SigV4 canonicalises it.

    Every byte of the UTF-8 encoding is encoded as ``%XY`` (upper-case hex)
    except letters, digits and ``-_.~``; ``/`` is kept in a path and encoded
    in a query component.
    """
    return quote(value, safe=_SAFE_IN_SEGMENT if encode_slash else _SAFE_IN_SEGMENT + "/")


def canonical_query(params: Sequence[tuple[str, str]]) -> str:
    """Return the canonical query string: each pair encoded, then sorted.

    A key without a value (``?lifecycle``) is passed as ``("lifecycle", "")``
    and comes out as ``lifecycle=``.
    """
    encoded = sorted(
        (uri_encode(k, encode_slash=True), uri_encode(v, encode_slash=True)) for k, v in params
    )
    return "&".join(f"{k}={v}" for k, v in encoded)


def canonical_headers(headers: Mapping[str, str]) -> tuple[str, str]:
    """Return the canonical header block and the signed-headers list.

    Names are lower-cased and sorted; values are trimmed and runs of spaces
    collapsed to one.

    Raises:
        ValueError: If two names differ only by case, or a name or value holds a
            line break or other control character (it would split the header).
    """
    lowered: dict[str, str] = {}
    for name, value in headers.items():
        key = name.lower()
        if key in lowered:
            raise ValueError(f"header {name!r} is given twice")
        if not key or any(ord(c) < 0x21 or ord(c) > 0x7E or c == ":" for c in key):
            raise ValueError(f"header name {name!r} is not a token")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            raise ValueError(f"header {name!r} holds a control character")
        lowered[key] = _SPACE_RUN.sub(" ", value.strip(" "))
    names = sorted(lowered)
    block = "".join(f"{n}:{lowered[n]}\n" for n in names)
    return block, ";".join(names)


def canonical_request(
    path: str,
    query: Sequence[tuple[str, str]],
    headers: Mapping[str, str],
    payload_hash: str = EMPTY_PAYLOAD_SHA256,
) -> tuple[str, str]:
    """Return a GET's canonical request and its signed-headers list.

    Args:
        path: The decoded path, starting with ``/`` (an object key prefixed with
            ``/``); it is encoded here, so pass ``/a b`` rather than ``/a%20b``.
        query: The query parameters, decoded.
        headers: Every header to sign, ``host`` included.
        payload_hash: The hex SHA-256 of the body.

    Raises:
        ValueError: If *path* does not start with ``/``, or a header is refused
            by :func:`canonical_headers`.
    """
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    block, signed = canonical_headers(headers)
    request = "\n".join(
        [
            "GET",
            uri_encode(path, encode_slash=False),
            canonical_query(query),
            block,
            signed,
            payload_hash,
        ]
    )
    return request, signed


def amz_date(now: datetime) -> str:
    """Return *now* as SigV4's ``YYYYMMDDTHHMMSSZ``, in UTC.

    Raises:
        ValueError: If *now* has no time zone (its instant would be a guess).
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def credential_scope(date: str, region: str) -> str:
    """Return the credential scope for *date* (``YYYYMMDD``) and *region*."""
    return f"{date}/{region}/{SERVICE}/aws4_request"


def string_to_sign(timestamp: str, scope: str, request: str) -> str:
    """Return the string to sign for a canonical *request*."""
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()
    return f"{ALGORITHM}\n{timestamp}\n{scope}\n{digest}"


def signing_key(secret_access_key: str, date: str, region: str) -> bytes:
    """Return the SigV4 signing key for *date* (``YYYYMMDD``) and *region*."""
    key = ("AWS4" + secret_access_key).encode("utf-8")
    for part in (date, region, SERVICE, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    return key


def sign_get(
    *,
    host: str,
    path: str,
    query: Sequence[tuple[str, str]],
    credentials: S3Credentials,
    region: str,
    now: datetime,
    extra_headers: Mapping[str, str] | None = None,
) -> SignedRequest:
    """Sign a GET with an empty body for S3.

    Adds ``x-amz-date``, ``x-amz-content-sha256`` and, for temporary keys,
    ``x-amz-security-token``, and signs them with ``host`` and *extra_headers*
    (``x-amz-request-payer``, ``range``).

    Args:
        host: The host the request goes to, without scheme or port.
        path: The decoded path, starting with ``/``.
        query: The decoded query parameters.
        credentials: The key to sign with.
        region: The bucket's region, such as ``us-east-1``.
        now: The signing time, timezone-aware.
        extra_headers: Further headers to sign and send.

    Returns:
        The URL (``https``) and the headers to send.

    Raises:
        ValueError: On a malformed host, region or path, a header refused by
            :func:`canonical_headers`, an extra header that would replace one
            this function sets, or a naive *now*.
    """
    if not _HOST.fullmatch(host):
        raise ValueError(f"host must be a bare host name: {host!r}")
    if not _REGION.fullmatch(region):
        raise ValueError(f"region must be lower-case letters, digits and '-': {region!r}")
    timestamp = amz_date(now)
    date = timestamp[:8]
    own = {"host": host, "x-amz-content-sha256": EMPTY_PAYLOAD_SHA256, "x-amz-date": timestamp}
    if credentials.session_token is not None:
        own["x-amz-security-token"] = credentials.session_token
    headers = dict(own)
    for name, value in (extra_headers or {}).items():
        if name.lower() in own:
            raise ValueError(f"header {name!r} is set by the signer")
        headers[name] = value
    request, signed = canonical_request(path, query, headers)
    scope = credential_scope(date, region)
    to_sign = string_to_sign(timestamp, scope, request)
    signature = hmac.new(
        signing_key(credentials.secret_access_key, date, region),
        to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    sent = {k.lower(): _SPACE_RUN.sub(" ", v.strip(" ")) for k, v in headers.items() if k != "host"}
    sent["authorization"] = (
        f"{ALGORITHM} Credential={credentials.access_key_id}/{scope},"
        f"SignedHeaders={signed},Signature={signature}"
    )
    url = f"https://{host}{uri_encode(path, encode_slash=False)}"
    if query:
        url += "?" + canonical_query(query)
    return SignedRequest(
        url=url,
        headers=sent,
        canonical_request=request,
        string_to_sign=to_sign,
        signature=signature,
    )
