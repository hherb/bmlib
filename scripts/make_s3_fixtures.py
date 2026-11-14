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

"""Write ``tests/fixtures/s3_sigv4.json`` and ``tests/fixtures/s3_responses.json``.

The shared S3 contracts of ``bmlib.fulltext.meca``, which Rust's
``rust/bmlib/tests/meca_s3_contract.rs`` replays.

``s3_sigv4.json``: signed requests. The three ``"source": "aws"`` cases are the
examples in AWS's S3 API reference ("Signature Calculations for the
Authorization Header", example key ``AKIAIOSFODNN7EXAMPLE``, 2013-05-24); their
signatures are AWS's, asserted below before anything is written, so they are
evidence independent of this code. The ``"python"`` cases are Python's output.

``s3_responses.json``: S3 replies in the shapes S3's API reference documents
(ListObjectsV2, the ``<Error>`` body), each with Python's reading.

    python scripts/make_s3_fixtures.py

``tests/test_meca_s3_contract.py`` checks that this script still builds both
committed fixtures, so add a case here and re-run it rather than editing the JSON.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bmlib.fulltext.meca.s3 import (
    S3Error,
    S3ErrorKind,
    classify_error,
    list_objects_request,
    object_request,
    parse_list_objects_v2,
)
from bmlib.fulltext.meca.sigv4 import S3Credentials, SignedRequest, sign_get

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SIGV4_OUTPUT = FIXTURES / "s3_sigv4.json"
RESPONSES_OUTPUT = FIXTURES / "s3_responses.json"

AWS_EXAMPLE_KEY = {
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}
"""AWS's documented example key (not a real one)."""

TEMPORARY_KEY = {
    "access_key_id": "ASIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "session_token": "IQoJb3JpZ2luX2VjEXAMPLETOKEN/+=",
}
"""A temporary key in the same made-up style, with a session token."""

AWS_SIGNATURES = {
    "aws-get-object": "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
    "aws-get-bucket-lifecycle": "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543",
    "aws-list-objects": "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7",
}
"""The signatures AWS publishes for its three GET examples."""

AWS_NOW = "2013-05-24T00:00:00Z"
NOW = "2026-11-14T10:20:30Z"
EXAMPLE_HOST = "examplebucket.s3.amazonaws.com"
S3_NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def _signing_cases() -> list[dict[str, Any]]:
    """The signing cases' inputs: which call, with what."""
    sign = "sign_get"
    return [
        {
            "name": "aws-get-object",
            "source": "aws",
            "call": sign,
            "credentials": AWS_EXAMPLE_KEY,
            "now": AWS_NOW,
            "args": {
                "host": EXAMPLE_HOST,
                "path": "/test.txt",
                "query": [],
                "region": "us-east-1",
                "extra_headers": {"Range": "bytes=0-9"},
            },
        },
        {
            "name": "aws-get-bucket-lifecycle",
            "source": "aws",
            "call": sign,
            "credentials": AWS_EXAMPLE_KEY,
            "now": AWS_NOW,
            "args": {
                "host": EXAMPLE_HOST,
                "path": "/",
                "query": [["lifecycle", ""]],
                "region": "us-east-1",
                "extra_headers": {},
            },
        },
        {
            "name": "aws-list-objects",
            "source": "aws",
            "call": sign,
            "credentials": AWS_EXAMPLE_KEY,
            "now": AWS_NOW,
            "args": {
                "host": EXAMPLE_HOST,
                "path": "/",
                "query": [["max-keys", "2"], ["prefix", "J"]],
                "region": "us-east-1",
                "extra_headers": {},
            },
        },
        {
            "name": "header-values-trimmed-and-collapsed",
            "source": "python",
            "call": sign,
            "credentials": AWS_EXAMPLE_KEY,
            "now": NOW,
            "args": {
                "host": EXAMPLE_HOST,
                "path": "/a/b c/ü.txt",
                "query": [["b", "2"], ["a", "x y"], ["a", "1"]],
                "region": "eu-west-2",
                "extra_headers": {"X-Custom": "  one   two  "},
            },
        },
        {
            "name": "medrxiv-listing-page-with-token",
            "source": "python",
            "call": "list_objects_request",
            "credentials": TEMPORARY_KEY,
            "now": NOW,
            "args": {
                "bucket": "medrxiv-src-monthly",
                "region": "us-east-1",
                "prefix": "Current_Content/November 2026/",
                "continuation_token": "1ueGcxLPRx1Tr/XYExHnhbYLgveDs2J/wm36Hy4vbOwM=",
            },
        },
        {
            "name": "medrxiv-first-listing-page",
            "source": "python",
            "call": "list_objects_request",
            "credentials": AWS_EXAMPLE_KEY,
            "now": NOW,
            "args": {
                "bucket": "medrxiv-src-monthly",
                "region": "us-east-1",
                "prefix": "",
                "continuation_token": None,
            },
        },
        {
            "name": "medrxiv-object-with-encoded-key",
            "source": "python",
            "call": "object_request",
            "credentials": AWS_EXAMPLE_KEY,
            "now": NOW,
            "args": {
                "bucket": "medrxiv-src-monthly",
                "region": "us-east-1",
                "key": "Back_Content/Batch 01/ü+x=1&y.meca",
            },
        },
    ]


def _refused_signing_cases() -> list[dict[str, Any]]:
    """Inputs each language must refuse, and which part refuses them."""
    good = {
        "host": EXAMPLE_HOST,
        "path": "/x",
        "query": [],
        "region": "us-east-1",
        "extra_headers": {},
    }
    return [
        {"name": "path-without-slash", "args": {**good, "path": "x"}},
        {"name": "host-with-scheme", "args": {**good, "host": "https://example.com"}},
        {"name": "upper-case-region", "args": {**good, "region": "US-EAST-1"}},
        {"name": "header-with-line-break", "args": {**good, "extra_headers": {"X-A": "a\r\nb"}}},
        {"name": "header-with-nul", "args": {**good, "extra_headers": {"X-A": "a\u0000b"}}},
        {"name": "header-with-delete", "args": {**good, "extra_headers": {"X-A": "a\u007fb"}}},
        {"name": "header-given-twice", "args": {**good, "extra_headers": {"X-A": "1", "x-a": "2"}}},
        {"name": "header-the-signer-sets", "args": {**good, "extra_headers": {"X-Amz-Date": "x"}}},
        {"name": "header-name-with-space", "args": {**good, "extra_headers": {"X A": "1"}}},
        {"name": "header-name-not-ascii", "args": {**good, "extra_headers": {"X-\u00dc": "1"}}},
    ]


def _refused_credentials() -> list[dict[str, Any]]:
    """Credentials each language must refuse."""
    return [
        {"name": "empty-key-id", "credentials": {**AWS_EXAMPLE_KEY, "access_key_id": ""}},
        {
            "name": "key-id-with-newline",
            "credentials": {**AWS_EXAMPLE_KEY, "access_key_id": "AKIAIOSFODNN7EXAMPLE\n"},
        },
        {
            "name": "secret-with-space",
            "credentials": {**AWS_EXAMPLE_KEY, "secret_access_key": "a b"},
        },
        {"name": "empty-session-token", "credentials": {**AWS_EXAMPLE_KEY, "session_token": ""}},
        {
            "name": "secret-with-a-pasted-no-break-space",
            "credentials": {**AWS_EXAMPLE_KEY, "secret_access_key": "abc\u00a0"},
        },
    ]


def _refused_object_keys() -> list[str]:
    """Object keys both languages refuse to sign (httpx would rewrite their path).

    Raises:
        AssertionError: If one is accepted.
    """
    keys = ["", "a/./b.meca", "../b.meca", "a/..", "."]
    for key in keys:
        if not _refuses(
            lambda k=key: object_request(
                _credentials(AWS_EXAMPLE_KEY), "medrxiv-src-monthly", "us-east-1", k, _now(NOW)
            )
        ):
            raise AssertionError(f"object key {key!r} was accepted")
    return keys


def _refused_buckets() -> list[str]:
    """Bucket names both languages refuse to sign for.

    Raises:
        AssertionError: If one is accepted.
    """
    buckets = ["my.bucket", "MyBucket", "-abc", "abc-", "ab", "a" * 64, "a_b", ""]
    for bucket in buckets:
        if not _refuses(
            lambda b=bucket: object_request(
                _credentials(AWS_EXAMPLE_KEY), b, "us-east-1", "a.meca", _now(NOW)
            )
        ):
            raise AssertionError(f"bucket {bucket!r} was accepted")
    return buckets


def _credentials(raw: dict[str, Any]) -> S3Credentials:
    """Build credentials from a case."""
    return S3Credentials(raw["access_key_id"], raw["secret_access_key"], raw.get("session_token"))


def _now(text: str) -> datetime:
    """Read a case's time."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def run_signing_case(case: dict[str, Any]) -> SignedRequest:
    """Run one signing case through the Python code."""
    credentials = _credentials(case["credentials"])
    now = _now(case["now"])
    args = case["args"]
    if case["call"] == "sign_get":
        return sign_get(
            host=args["host"],
            path=args["path"],
            query=[tuple(pair) for pair in args["query"]],
            credentials=credentials,
            region=args["region"],
            now=now,
            extra_headers=args["extra_headers"],
        )
    if case["call"] == "list_objects_request":
        return list_objects_request(
            credentials,
            args["bucket"],
            args["region"],
            now,
            args["prefix"],
            args["continuation_token"],
        )
    if case["call"] == "object_request":
        return object_request(credentials, args["bucket"], args["region"], args["key"], now)
    raise ValueError(f"unknown call {case['call']!r}")


def _refuses(action: Any) -> bool:
    """Whether *action* raises ``ValueError``."""
    try:
        action()
    except ValueError:
        return True
    return False


def build_sigv4() -> dict[str, Any]:
    """The whole signing fixture.

    Raises:
        AssertionError: If an AWS case's signature is not AWS's, or a refusal case
            is accepted.
    """
    cases = []
    for case in _signing_cases():
        signed = run_signing_case(case)
        if case["source"] == "aws" and signed.signature != AWS_SIGNATURES[case["name"]]:
            raise AssertionError(f"{case['name']}: {signed.signature} is not AWS's signature")
        cases.append(
            {
                **case,
                "expected": {
                    "url": signed.url,
                    "headers": signed.headers,
                    "canonical_request": signed.canonical_request,
                    "string_to_sign": signed.string_to_sign,
                    "signature": signed.signature,
                },
            }
        )
    refused = _refused_signing_cases()
    for case in refused:
        if not _refuses(
            lambda c=case: run_signing_case(
                {"call": "sign_get", "credentials": AWS_EXAMPLE_KEY, "now": NOW, **c}
            )
        ):
            raise AssertionError(f"{case['name']} was accepted")
    bad_keys = _refused_credentials()
    for case in bad_keys:
        if not _refuses(lambda c=case: _credentials(c["credentials"])):
            raise AssertionError(f"{case['name']} was accepted")
    return {
        "refused_now": NOW,
        "refused_credentials": AWS_EXAMPLE_KEY,
        "cases": cases,
        "refused": refused,
        "refused_keys": bad_keys,
        "refused_object_keys": _refused_object_keys(),
        "refused_buckets": _refused_buckets(),
    }


def _contents(key: str, size: str, etag: str = '"9b2cf535f27731c974343645a3985328"') -> str:
    """One ``<Contents>`` element."""
    return (
        f"<Contents><Key>{key}</Key><LastModified>2024-02-01T03:04:05.000Z</LastModified>"
        f"<ETag>{etag}</ETag><Size>{size}</Size><StorageClass>STANDARD</StorageClass></Contents>"
    )


def _page(
    *contents: str, truncated: str = "false", token: str = "", count: str | None = None
) -> str:
    """A ListObjectsV2 reply."""
    key_count = str(len(contents)) if count is None else count
    next_token = f"<NextContinuationToken>{token}</NextContinuationToken>" if token else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<ListBucketResult {S3_NS}>'
        f"<Name>medrxiv-src-monthly</Name><Prefix></Prefix><KeyCount>{key_count}</KeyCount>"
        f"<MaxKeys>1000</MaxKeys><IsTruncated>{truncated}</IsTruncated>{next_token}"
        + "".join(contents)
        + "</ListBucketResult>"
    )


def _list_page_cases() -> list[tuple[str, str | bytes]]:
    """Listing bodies, read and refused (every name in :data:`_REFUSED_PAGES` is refused)."""
    one = _contents("Current_Content/January_2024/0a1b.meca", "48213")
    two = _contents(
        "Current_Content/January_2024/R&amp;D 2.meca",
        "1048576",
        '"&quot;d41d8cd98f00b204e9800998ecf8427e-3&quot;"',
    )
    return [
        ("truncated-page", _page(one, two, truncated="true", token="1ueGcxLPRx1Tr/XYE=")),
        ("last-page", _page(one)),
        ("empty-page", _page()),
        ("page-without-key-count", _page(one).replace("<KeyCount>1</KeyCount>", "")),
        ("object-without-size", _page(_contents("a.meca", "1").replace("<Size>1</Size>", ""))),
        ("negative-size", _page(_contents("a.meca", "-1"))),
        ("fractional-size", _page(_contents("a.meca", "1.5"))),
        (
            "object-without-etag",
            _page(one.replace('<ETag>"9b2cf535f27731c974343645a3985328"</ETag>', "")),
        ),
        ("object-without-key", _page(_contents("", "1"))),
        ("key-given-twice", _page(one.replace("<Key>", "<Key>x</Key><Key>", 1))),
        ("truncated-without-token", _page(one, truncated="true")),
        ("key-count-disagrees", _page(one, count="2")),
        ("is-truncated-not-a-boolean", _page(one, truncated="yes")),
        ("declares-a-dtd", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>' + _page(one)[39:]),
        ("not-xml", "<ListBucketResult"),
        ("latin-1-body", _page(one).replace("0a1b", "caf\u00e9").encode("latin-1")),
        (
            "utf-16-body-hiding-a-dtd",
            (
                '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY e "EVIL">]>'
                + _page(one)[39:]
            ).encode("utf-16"),
        ),
        (
            "an-error-body",
            "<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>",
        ),
        (
            "page-without-is-truncated",
            _page(one).replace("<IsTruncated>false</IsTruncated>", ""),
        ),
        (
            "truncated-with-an-empty-token",
            _page(one, truncated="true").replace(
                "<MaxKeys>", "<NextContinuationToken></NextContinuationToken><MaxKeys>"
            ),
        ),
        ("key-count-with-a-sign", _page(one, count="+1")),
        ("key-count-with-a-space", _page(one, count=" 1")),
        (
            "page-with-common-prefixes",
            _page(one, count="2").replace(
                "</ListBucketResult>",
                "<CommonPrefixes><Prefix>Back_Content/</Prefix></CommonPrefixes>"
                "</ListBucketResult>",
            ),
        ),
        ("wrong-root", _page(one).replace("ListBucketResult", "ListAllMyBucketsResult")),
        (
            "another-namespace",
            _page(one).replace(S3_NS, 'xmlns="http://example.com/not-s3/"'),
        ),
        ("comment-inside-a-key", _page(one.replace("0a1b", "0a<!--x-->1b"))),
        ("comment-before-a-key-text", _page(one.replace("<Key>", "<Key><!--x-->"))),
        ("instruction-inside-a-key", _page(one.replace("0a1b", "0a<?pi x?>1b"))),
        ("element-inside-a-key", _page(one.replace("0a1b", "0a<b/>1b"))),
        (
            "comment-between-objects",
            _page(one, one.replace("0a1b", "2c3d")).replace(
                "</Contents><Contents>", "</Contents><!-- between --><Contents>"
            ),
        ),
        ("size-at-the-u64-limit", _page(_contents("a.meca", str((1 << 64) - 1)))),
        ("size-past-the-u64-limit", _page(_contents("a.meca", str(1 << 64)))),
    ]


_REFUSED_PAGES = frozenset(
    {
        "object-without-size",
        "negative-size",
        "fractional-size",
        "object-without-etag",
        "object-without-key",
        "key-given-twice",
        "truncated-without-token",
        "key-count-disagrees",
        "is-truncated-not-a-boolean",
        "declares-a-dtd",
        "not-xml",
        "latin-1-body",
        "utf-16-body-hiding-a-dtd",
        "an-error-body",
        "page-without-is-truncated",
        "truncated-with-an-empty-token",
        "key-count-with-a-sign",
        "key-count-with-a-space",
        "wrong-root",
        "another-namespace",
        "comment-inside-a-key",
        "comment-before-a-key-text",
        "instruction-inside-a-key",
        "element-inside-a-key",
        "size-past-the-u64-limit",
    }
)
"""The listing cases that must fail the page, asserted when the fixture is built, so
a regression cannot be written into the fixture as a success."""


def _error_body(code: str, message: str, extra: str = "") -> str:
    """An S3 ``<Error>`` body."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>{code}</Code>'
        f"<Message>{message}</Message>{extra}<RequestId>4442587FB7D0A2F9</RequestId>"
        "<HostId>example-host-id</HostId></Error>"
    )


def _error_cases() -> list[tuple[str, int, str | bytes]]:
    """Error replies: a name, the status and the body."""
    return [
        ("access-denied", 403, _error_body("AccessDenied", "Access Denied")),
        (
            "invalid-access-key-id",
            403,
            _error_body(
                "InvalidAccessKeyId",
                "The AWS Access Key Id you provided does not exist in our records.",
                "<AWSAccessKeyId>AKIAIOSFODNN7EXAMPLE</AWSAccessKeyId>",
            ),
        ),
        (
            "signature-does-not-match",
            403,
            _error_body(
                "SignatureDoesNotMatch",
                "The request signature we calculated does not match the signature you provided. "
                "Check your key and signing method.",
                "<AWSAccessKeyId>AKIAIOSFODNN7EXAMPLE</AWSAccessKeyId>"
                "<StringToSign>AWS4-HMAC-SHA256\n20261114T102030Z</StringToSign>",
            ),
        ),
        (
            "request-time-too-skewed",
            403,
            _error_body(
                "RequestTimeTooSkewed",
                "The difference between the request time and the current time is too large.",
            ),
        ),
        ("expired-token", 400, _error_body("ExpiredToken", "The provided token has expired.")),
        ("no-such-key", 404, _error_body("NoSuchKey", "The specified key does not exist.")),
        ("no-such-bucket", 404, _error_body("NoSuchBucket", "The specified bucket does not exist")),
        ("slow-down", 503, _error_body("SlowDown", "Please reduce your request rate.")),
        ("internal-error", 500, _error_body("InternalError", "We encountered an internal error.")),
        ("empty-503", 503, ""),
        ("empty-500", 500, ""),
        ("empty-504", 504, ""),
        (
            "service-unavailable",
            503,
            _error_body("ServiceUnavailable", "Please reduce your request rate."),
        ),
        (
            "request-timeout",
            400,
            _error_body(
                "RequestTimeout",
                "Your socket connection to the server was not read from or written to "
                "within the timeout period.",
            ),
        ),
        ("unknown-code-503", 503, _error_body("SomethingNew", "A new failure.")),
        ("unknown-code-403", 403, _error_body("AllAccessDisabled", "All access disabled.")),
        ("empty-code", 403, _error_body("", "No code given.")),
        (
            "comment-inside-a-code",
            404,
            "<Error><Code>No<!--x-->SuchKey</Code><Message>gone</Message></Error>",
        ),
        ("empty-403", 403, ""),
        ("html-from-a-proxy", 502, "<html><body>Bad Gateway</body></html>"),
        (
            "other-code-kept",
            400,
            _error_body("InvalidArgument", "Invalid Argument"),
        ),
        ("unreadable-body-404", 404, "not xml at all"),
        (
            "utf-16-error-body",
            404,
            "<Error><Code>NoSuchKey</Code><Message>gone</Message></Error>".encode("utf-16"),
        ),
    ]


def _raw(body: str | bytes) -> bytes:
    """A case's body as the bytes S3 would send."""
    return body if isinstance(body, bytes) else body.encode("utf-8")


def _stored(body: str | bytes) -> dict[str, str]:
    """A body as the fixture holds it: text, or hex for bytes that are not UTF-8."""
    return {"body_hex": body.hex()} if isinstance(body, bytes) else {"body": body}


def _read_page(body: str | bytes) -> dict[str, Any]:
    """Python's reading of a listing body."""
    try:
        page = parse_list_objects_v2(_raw(body))
    except S3Error as exc:
        return {"error": exc.kind.value}
    return {
        "objects": [
            {"key": o.key, "size": o.size, "etag": o.etag, "last_modified": o.last_modified}
            for o in page.objects
        ],
        "is_truncated": page.is_truncated,
        "next_continuation_token": page.next_continuation_token,
    }


def _read_error(status: int, body: str | bytes) -> dict[str, Any]:
    """Python's reading of an error reply."""
    error = classify_error(status, _raw(body))
    return {
        "kind": error.kind.value,
        "code": error.code,
        "detail": error.detail,
        "retryable": error.retryable,
        "reason": error.reason(),
    }


def build_responses() -> dict[str, Any]:
    """The whole replies fixture.

    Raises:
        AssertionError: If a case in :data:`_REFUSED_PAGES` is read, or another
            is refused, or a name is unknown.
    """
    pages = _list_page_cases()
    names = {name for name, _ in pages}
    if not _REFUSED_PAGES <= names:
        raise AssertionError(f"unknown refused pages: {sorted(_REFUSED_PAGES - names)}")
    list_pages = []
    for name, body in pages:
        expected = _read_page(body)
        refused = expected == {"error": S3ErrorKind.MALFORMED.value}
        if refused != (name in _REFUSED_PAGES):
            raise AssertionError(f"{name}: read as {expected}")
        list_pages.append({"name": name, **_stored(body), "expected": expected})
    return {
        "list_pages": list_pages,
        "errors": [
            {
                "name": name,
                "status": status,
                **_stored(body),
                "expected": _read_error(status, body),
            }
            for name, status, body in _error_cases()
        ],
    }


def _write(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as the fixtures are written."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    """Write both fixtures."""
    _write(SIGV4_OUTPUT, build_sigv4())
    _write(RESPONSES_OUTPUT, build_responses())


if __name__ == "__main__":
    main()
