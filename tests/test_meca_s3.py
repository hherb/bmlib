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

"""``bmlib.fulltext.meca``: signing rules and the transport over a fake S3."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from bmlib.fulltext.meca import s3
from bmlib.fulltext.meca.s3 import (
    S3Error,
    S3ErrorKind,
    S3Object,
    download_object,
    etag_is_md5,
    list_objects,
)
from bmlib.fulltext.meca.sigv4 import S3Credentials, amz_date, sign_get

KEY = S3Credentials("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "TOKEN+/=")
NOW = datetime(2026, 11, 14, 10, 20, 30, tzinfo=UTC)
BUCKET = s3.MEDRXIV_MECA_BUCKET
REGION = s3.MECA_BUCKET_REGION
BODY = b"PK\x03\x04 a pretend package " * 1000


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class _Stream(httpx.SyncByteStream):
    """A body served in blocks, as a real connection serves it.

    ``httpx.Response(content=...)`` is read before the caller sees it, so its raw
    stream (what ``download_object`` reads) would already be consumed.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __iter__(self):  # type: ignore[no-untyped-def]  # httpx's own signature
        for start in range(0, len(self._body), 4096):
            yield self._body[start : start + 4096]


def _served(body: bytes) -> httpx.Response:
    return httpx.Response(200, stream=_Stream(body))


def _page(keys: list[str], token: str | None) -> bytes:
    contents = "".join(
        f"<Contents><Key>{k}</Key><LastModified>2024-01-01T00:00:00.000Z</LastModified>"
        f'<ETag>"{"0" * 32}"</ETag><Size>1</Size></Contents>'
        for k in keys
    )
    truncated = "true" if token else "false"
    next_token = f"<NextContinuationToken>{token}</NextContinuationToken>" if token else ""
    return (
        f"<ListBucketResult><IsTruncated>{truncated}</IsTruncated>{next_token}{contents}"
        "</ListBucketResult>"
    ).encode()


def _object(body: bytes = BODY, etag: str | None = None) -> S3Object:
    return S3Object(
        key="Current_Content/January_2024/0a1b.meca",
        size=len(body),
        etag=etag if etag is not None else hashlib.md5(body).hexdigest(),
        last_modified="2024-01-01T00:00:00.000Z",
    )


class TestCredentials:
    def test_repr_shows_only_the_key_id(self) -> None:
        text = repr(KEY)
        assert "AKIAIOSFODNN7EXAMPLE" in text
        assert "wJalr" not in text and "TOKEN" not in text

    def test_a_signed_request_keeps_its_headers_out_of_repr(self) -> None:
        signed = s3.object_request(KEY, BUCKET, REGION, "a.meca", NOW)
        assert "TOKEN" not in repr(signed)
        assert signed.headers["x-amz-security-token"] == "TOKEN+/="


class TestSigning:
    def test_a_naive_time_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            amz_date(datetime(2026, 1, 1))  # noqa: DTZ001 - the point of the test

    def test_another_zone_is_converted_to_utc(self) -> None:
        brisbane = timezone(timedelta(hours=10))
        assert amz_date(datetime(2026, 11, 14, 20, 20, 30, tzinfo=brisbane)) == "20261114T102030Z"

    def test_the_signature_moves_with_the_time(self) -> None:
        def sign(now: datetime) -> str:
            return sign_get(
                host="b.s3.us-east-1.amazonaws.com",
                path="/k",
                query=[],
                credentials=KEY,
                region="us-east-1",
                now=now,
            ).signature

        assert sign(NOW) != sign(NOW + timedelta(seconds=1))

    def test_a_bucket_name_with_a_dot_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bucket"):
            s3.bucket_host("my.bucket", REGION)

    def test_an_empty_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            s3.object_request(KEY, BUCKET, REGION, "", NOW)

    @pytest.mark.parametrize(
        ("etag", "checked"),
        [("0" * 32, True), ("d41d8cd98f00b204e9800998ecf8427e-3", False), ("ABC", False)],
    )
    def test_only_a_single_part_etag_is_an_md5(self, etag: str, checked: bool) -> None:
        assert etag_is_md5(etag) is checked


class TestListObjects:
    def test_pages_are_followed_with_the_token_and_every_request_pays(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            token = request.url.params.get("continuation-token")
            if token is None:
                return httpx.Response(200, content=_page(["a.meca"], "T/1="))
            assert token == "T/1="
            return httpx.Response(200, content=_page(["b.meca"], None))

        with _client(handler) as client:
            first = list_objects(client, KEY, BUCKET, REGION, now=NOW)
            second = list_objects(
                client,
                KEY,
                BUCKET,
                REGION,
                continuation_token=first.next_continuation_token,
                now=NOW,
            )
        assert [o.key for o in first.objects + second.objects] == ["a.meca", "b.meca"]
        assert not second.is_truncated
        for request in seen:
            assert request.headers["x-amz-request-payer"] == "requester"
            assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIA")
            assert request.url.host == "medrxiv-src-monthly.s3.us-east-1.amazonaws.com"
            assert request.url.params["list-type"] == "2"

    def test_a_refusal_is_classified(self) -> None:
        body = b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
        with (
            _client(lambda r: httpx.Response(403, content=body)) as client,
            pytest.raises(S3Error) as caught,
        ):
            list_objects(client, KEY, BUCKET, REGION, now=NOW)
        assert caught.value.kind is S3ErrorKind.ACCESS_DENIED
        assert "s3:ListBucket" in str(caught.value)

    def test_no_connection_is_a_transport_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with _client(handler) as client, pytest.raises(S3Error) as caught:
            list_objects(client, KEY, BUCKET, REGION, now=NOW)
        assert caught.value.kind is S3ErrorKind.TRANSPORT
        assert caught.value.retryable


class TestDownloadObject:
    def test_a_verified_body_is_published_only_after_it_is_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        destination = tmp_path / "pkg" / "0a1b.meca"
        replaced: list[bool] = []
        real_replace = os.replace

        def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            replaced.append(Path(dst).exists())
            real_replace(src, dst)

        monkeypatch.setattr(s3.os, "replace", spy)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _served(BODY)

        with _client(handler) as client:
            result = download_object(client, KEY, BUCKET, REGION, _object(), destination, now=NOW)
        assert result == destination
        assert destination.read_bytes() == BODY
        assert replaced == [False]  # absent until the one rename
        assert requests[0].url.path == "/Current_Content/January_2024/0a1b.meca"
        assert requests[0].headers["x-amz-request-payer"] == "requester"
        assert list(destination.parent.iterdir()) == [destination]

    @pytest.mark.parametrize(
        ("served", "listed", "kind"),
        [
            (BODY[:-1], _object(), S3ErrorKind.SIZE_MISMATCH),
            (BODY + b"x", _object(), S3ErrorKind.SIZE_MISMATCH),
            (BODY, _object(etag="0" * 32), S3ErrorKind.ETAG_MISMATCH),
        ],
    )
    def test_the_wrong_bytes_leave_nothing_behind(
        self, tmp_path: Path, served: bytes, listed: S3Object, kind: S3ErrorKind
    ) -> None:
        destination = tmp_path / "0a1b.meca"
        with _client(lambda r: _served(served)) as client:
            with pytest.raises(S3Error) as caught:
                download_object(client, KEY, BUCKET, REGION, listed, destination, now=NOW)
        assert caught.value.kind is kind
        assert list(tmp_path.iterdir()) == []

    def test_a_multipart_etag_is_not_checked(self, tmp_path: Path) -> None:
        listed = _object(etag="d41d8cd98f00b204e9800998ecf8427e-3")
        with _client(lambda r: _served(BODY)) as client:
            path = download_object(client, KEY, BUCKET, REGION, listed, tmp_path / "x", now=NOW)
        assert path.read_bytes() == BODY

    def test_an_existing_file_is_kept_when_the_download_fails(self, tmp_path: Path) -> None:
        destination = tmp_path / "x.meca"
        destination.write_bytes(b"old")
        body = b"<Error><Code>NoSuchKey</Code><Message>gone</Message></Error>"
        with _client(lambda r: httpx.Response(404, content=body)) as client:
            with pytest.raises(S3Error) as caught:
                download_object(client, KEY, BUCKET, REGION, _object(), destination, now=NOW)
        assert caught.value.kind is S3ErrorKind.NO_SUCH_KEY
        assert destination.read_bytes() == b"old"
        assert list(tmp_path.iterdir()) == [destination]

    def test_progress_returning_false_stops_it(self, tmp_path: Path) -> None:
        calls: list[tuple[int, int]] = []

        def progress(done: int, total: int) -> bool:
            calls.append((done, total))
            return False

        with _client(lambda r: _served(BODY)) as client:
            with pytest.raises(S3Error) as caught:
                download_object(
                    client,
                    KEY,
                    BUCKET,
                    REGION,
                    _object(),
                    tmp_path / "x",
                    now=NOW,
                    progress=progress,
                )
        assert caught.value.kind is S3ErrorKind.CANCELLED
        assert calls and calls[0][1] == len(BODY)
        assert list(tmp_path.iterdir()) == []

    def test_a_broken_connection_is_a_transport_error_and_leaves_nothing(
        self, tmp_path: Path
    ) -> None:
        class Breaks(httpx.SyncByteStream):
            def __iter__(self):  # type: ignore[no-untyped-def]  # httpx's own signature
                yield BODY[:100]
                raise httpx.ReadError("reset")

        with _client(lambda r: httpx.Response(200, stream=Breaks())) as client:
            with pytest.raises(S3Error) as caught:
                download_object(client, KEY, BUCKET, REGION, _object(), tmp_path / "x", now=NOW)
        assert caught.value.kind is S3ErrorKind.TRANSPORT
        assert list(tmp_path.iterdir()) == []


class TestWhatIsRefused:
    def test_a_redirect_is_not_followed(self, tmp_path: Path) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(307, headers={"Location": "https://elsewhere.example/x"})

        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with pytest.raises(S3Error) as listing:
                list_objects(client, KEY, BUCKET, REGION, now=NOW)
            with pytest.raises(S3Error) as download:
                download_object(client, KEY, BUCKET, REGION, _object(), tmp_path / "x", now=NOW)
        assert listing.value.status == download.value.status == 307
        assert [r.url.host for r in seen] == [s3.bucket_host(BUCKET, REGION)] * 2

    def test_a_body_stored_with_a_content_encoding_is_refused(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=_Stream(BODY))

        with _client(handler) as client, pytest.raises(S3Error) as caught:
            download_object(client, KEY, BUCKET, REGION, _object(), tmp_path / "x", now=NOW)
        assert caught.value.kind is S3ErrorKind.MALFORMED
        assert "Content-Encoding" in str(caught.value)
        assert list(tmp_path.iterdir()) == []

    def test_without_httpx_the_extra_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "httpx", None)
        with pytest.raises(ImportError, match=r"bmlib\[meca\]"):
            list_objects(object(), KEY, BUCKET, REGION, now=NOW)  # type: ignore[arg-type]

    @pytest.mark.parametrize("key", ["a/./b.meca", "../b.meca", "a/.."])
    def test_a_key_with_a_dot_segment_is_refused(self, key: str) -> None:
        with pytest.raises(ValueError, match="segment"):
            s3.object_request(KEY, BUCKET, REGION, key, NOW)

    def test_a_body_that_is_not_utf8_is_malformed(self) -> None:
        body = "<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>"
        with pytest.raises(S3Error) as caught:
            s3.parse_list_objects_v2(body.encode("utf-16"))
        assert caught.value.kind is S3ErrorKind.MALFORMED
