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

"""Tests for ``rust/oracle/_oracle.py``, the dumpers' shared vocabulary.

The two readers — this one and ``rust/bmlib/tests/common/oracle.rs`` — are
deliberately **two implementations of one contract**: the corpus's response
shapes. The dumpers are Python and the harnesses are Rust, so neither can import
the other; what the shared modules buy is that neither side grows a second copy
of a *reinterpretation*. Each therefore needs its own tests against the same
shapes, and these are the Python half.

What is pinned is what a corpus case would otherwise be unable to see: the
exception *name* Python's handlers write, and that every marker **fails closed**
— a marker read as a body is what made ``fetch/http-error`` vacuous for a
release (#349).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HELPERS_PATH = Path(__file__).resolve().parent.parent / "rust" / "oracle" / "_oracle.py"
_spec = importlib.util.spec_from_file_location("bmlib_rust_oracle_helpers", _HELPERS_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the module is in-tree
    raise ImportError(f"cannot load the oracle helpers from {_HELPERS_PATH}")
oracle = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = oracle
_spec.loader.exec_module(oracle)


class TestTheNamedException:
    """`type(exc).__name__` is the half the port cannot reproduce."""

    def test_the_name_and_message_are_split_at_the_first_separator(self) -> None:
        exc = oracle.named_exception("ConnectError: connection refused")
        assert type(exc).__name__ == "ConnectError"
        assert str(exc) == "connection refused"

    def test_a_message_carrying_a_separator_keeps_the_rest(self) -> None:
        """Only the *first* `": "` separates, so a message may contain one."""
        exc = oracle.named_exception("ReadTimeout: timed out: no bytes for 30s")
        assert type(exc).__name__ == "ReadTimeout"
        assert str(exc) == "timed out: no bytes for 30s"

    def test_a_message_with_no_name_is_the_base_exception(self) -> None:
        """`type(exc).__name__` for a bare `Exception` — assumed, not invented."""
        exc = oracle.named_exception("connection refused")
        assert type(exc).__name__ == "Exception"
        assert str(exc) == "connection refused"

    def test_a_name_with_no_message_keeps_an_empty_one(self) -> None:
        exc = oracle.named_exception("ReadTimeout: ")
        assert type(exc).__name__ == "ReadTimeout"
        assert str(exc) == ""


class TestTheResponseVocabulary:
    """Three shapes, and the whole key set is what identifies a marker."""

    def test_a_bare_payload_is_a_body(self) -> None:
        assert oracle.response_marker({"collection": []}) is None
        assert oracle.response_marker("text") is None
        assert oracle.response_marker([1, 2]) is None
        assert oracle.response_marker(None) is None

    def test_a_status_marker(self) -> None:
        assert oracle.response_marker({"http_status": 500, "body": {"a": 1}}) == (
            "http_status",
            {"a": 1},
            500,
        )

    def test_a_status_marker_with_no_body(self) -> None:
        assert oracle.response_marker({"http_status": 429}) == ("http_status", None, 429)

    def test_a_transport_marker(self) -> None:
        assert oracle.response_marker(
            {"transport_error": {"name": "ConnectError", "message": "connection refused"}}
        ) == ("transport_error", "ConnectError", "connection refused")

    def test_a_marker_is_recognised_by_its_whole_key_set(self) -> None:
        """An object that merely *carries* a marker key is a body, so a real
        payload cannot be mistaken for a marker."""
        assert oracle.response_marker({"http_status": 500, "extra": 1}) is None
        assert oracle.response_marker({"transport_error": "boom", "extra": 1}) is None

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"http_status": "500"}, "http_status must be an integer"),
            ({"http_status": True}, "http_status must be an integer"),
            ({"transport_error": "boom"}, "transport_error must carry exactly"),
            ({"transport_error": {"name": "ConnectError"}}, "must carry exactly"),
            (
                {"transport_error": {"name": "X", "message": "m", "extra": 1}},
                "must carry exactly",
            ),
            ({"transport_error": {"name": 7, "message": "m"}}, "name must be a non-empty"),
            ({"transport_error": {"name": "", "message": "m"}}, "name must be a non-empty"),
            ({"transport_error": {"name": "X", "message": 7}}, "message must be a string"),
        ],
    )
    def test_every_marker_fails_closed(self, payload: object, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            oracle.response_marker(payload)
