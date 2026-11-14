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

"""The shared S3 contracts: ``tests/fixtures/s3_sigv4.json`` and ``s3_responses.json``.

Written by ``scripts/make_s3_fixtures.py``, which must still build both; Rust's
``rust/bmlib/tests/meca_s3_contract.rs`` replays them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
SIGV4: dict[str, Any] = json.loads((FIXTURES / "s3_sigv4.json").read_text(encoding="utf-8"))
RESPONSES: dict[str, Any] = json.loads((FIXTURES / "s3_responses.json").read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_s3_fixtures.py"

# AWS's published signatures, restated here rather than read from the script, so
# an edit to the script cannot move both sides at once.
_AWS = {
    "aws-get-object": "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
    "aws-get-bucket-lifecycle": "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543",
    "aws-list-objects": "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7",
}


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("bmlib_make_s3_fixtures", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_generator_still_builds_the_signing_fixture() -> None:
    assert _generator().build_sigv4() == SIGV4


def test_the_generator_still_builds_the_replies_fixture() -> None:
    assert _generator().build_responses() == RESPONSES


def test_the_aws_cases_carry_aws_signatures() -> None:
    aws = {c["name"]: c["expected"]["signature"] for c in SIGV4["cases"] if c["source"] == "aws"}
    assert aws == _AWS


def test_every_request_pays_and_signs_the_payer_header_where_it_reads_a_bucket() -> None:
    for case in SIGV4["cases"]:
        if case["call"] == "sign_get":
            continue
        headers = case["expected"]["headers"]
        assert headers["x-amz-request-payer"] == "requester", case["name"]
        assert "x-amz-request-payer" in headers["authorization"], case["name"]


def test_no_reply_reading_repeats_a_key_id() -> None:
    for case in RESPONSES["errors"]:
        expected = case["expected"]
        assert "AKIA" not in expected["detail"] + expected["reason"], case["name"]
