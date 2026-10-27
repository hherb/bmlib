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

"""The shared ``FullTextResult.jats_xml`` contract.

The same file Rust's ``rust/bmlib/tests/fulltext_source_contract.rs`` asserts
against: the service hands back the JATS it rendered, byte for byte as served,
and the cache keeps it beside the HTML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.fulltext.cache import FullTextCache, sanitize_identifier
from bmlib.fulltext.models import FullTextResult, FullTextSourceEntry
from bmlib.fulltext.service import FullTextService

FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = json.loads((FIXTURES / "fulltext_source.json").read_text(encoding="utf-8"))


def _body(name: str | None) -> bytes | None:
    """The bytes of fixture file *name*, or ``None`` for a null."""
    return None if name is None else (FIXTURES / name).read_bytes()


def _response(answer: dict[str, Any]) -> MagicMock:
    """One scripted HTTP response, as ``_http_get`` returns it."""
    resp = MagicMock()
    resp.status_code = answer["status"]
    resp.content = _body(answer.get("body_file")) or b""
    return resp


def _fetch(
    service: FullTextService, request: dict[str, Any], answers: list[dict[str, Any]]
) -> FullTextResult:
    """Run ``fetch_fulltext`` for *request* against a transport answering *answers* in order.

    Asserts every answer was asked for and no more, so a port that requests in
    another order or an extra time fails here rather than quietly reading a
    different body.
    """
    script = [_response(answer) for answer in answers]
    asked: list[str] = []

    def http_get(url: str, **_kwargs: object) -> MagicMock:
        asked.append(url)
        assert script, f"transport exhausted at {url}"
        return script.pop(0)

    sources = [FullTextSourceEntry.from_dict(s) for s in request.get("fulltext_sources", [])]
    with patch.object(service, "_http_get", side_effect=http_get):
        result = service.fetch_fulltext(
            fulltext_sources=sources or None,
            pmc_id=request.get("pmc_id"),
            identifier=request.get("identifier"),
        )
    assert len(asked) == len(answers), asked
    return result


def _check(result: FullTextResult, expected: dict[str, Any]) -> None:
    """Assert *result* carries what the contract's *expected* block names."""
    assert result.source == expected["source"]
    assert result.content_kind == expected["content_kind"]
    assert result.jats_xml == _body(expected["jats_xml"])


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda case: case["name"])
def test_contract_case(case: dict[str, Any], tmp_path: Path) -> None:
    cache = FullTextCache(tmp_path)
    key = sanitize_identifier(case["request"].get("identifier", ""))
    jats_entry = tmp_path / "jats" / f"{key}.xml"
    if "seed_html" in case:
        cache.save_html(case["seed_html"], key)
    if case.get("seed_unreadable_jats"):
        jats_entry.mkdir(parents=True)
    service = FullTextService(email="test@example.com", cache=cache)

    _check(_fetch(service, case["request"], case["responses"]), case["expected"])
    if case.get("moved_aside"):
        assert not jats_entry.exists()
        assert jats_entry.with_name(f"{jats_entry.name}.corrupt").exists()
    if "then" in case:
        _check(_fetch(service, case["request"], []), case["then"])
