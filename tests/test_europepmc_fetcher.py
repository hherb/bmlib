"""Tests for the Europe PMC day-by-day sync fetcher (``fetch_europepmc``)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from bmlib.publications.fetchers.europepmc import EUROPEPMC_REST_BASE, fetch_europepmc
from bmlib.publications.fetchers.registry import _ensure_builtins, get_source
from bmlib.publications.models import FetchedRecord

DAY = date(2024, 3, 5)
QUERY = 'OPEN_ACCESS:Y AND (TITLE:"depression")'


def _hit(n: int, *, pmcid: str | None = None, open_access: bool = True) -> dict:
    """One ``resultType=core`` hit."""
    hit: dict = {
        "id": str(40000000 + n),
        "source": "MED",
        "pmid": str(40000000 + n),
        "title": f"Trial {n}",
        "firstPublicationDate": "2024-03-05",
        "isOpenAccess": "Y" if open_access else "N",
        "inEPMC": "Y" if pmcid else "N",
    }
    if pmcid:
        hit["pmcid"] = pmcid
    return hit


def _page(hits: list[dict], hit_count: int, next_cursor: str | None) -> dict:
    """A Europe PMC search envelope."""
    page: dict = {"hitCount": hit_count, "resultList": {"result": hits}}
    if next_cursor is not None:
        page["nextCursorMark"] = next_cursor
    return page


def _client(pages: list[object]) -> MagicMock:
    """A mock httpx client answering *pages* in order."""
    client = MagicMock()
    responses = []
    for body in pages:
        response = MagicMock()
        response.json.return_value = body
        response.raise_for_status.return_value = None
        responses.append(response)
    client.get.side_effect = responses
    return client


def _run(client: MagicMock, query: str = QUERY) -> tuple[object, list[FetchedRecord]]:
    records: list[FetchedRecord] = []
    with patch("bmlib.publications.fetchers.europepmc.time.sleep"):
        result = fetch_europepmc(client, DAY, on_record=records.append, query=query)
    return result, records


def _params(client: MagicMock, call: int) -> dict:
    return client.get.call_args_list[call].kwargs["params"]


def test_query_gets_the_day_clause():
    client = _client([_page([_hit(1)], 1, "*")])
    _run(client)
    url = client.get.call_args_list[0].args[0]
    params = _params(client, 0)
    assert url == f"{EUROPEPMC_REST_BASE}/search"
    assert params["query"] == f"({QUERY}) AND FIRST_PDATE:[2024-03-05 TO 2024-03-05]"
    assert params["resultType"] == "core"
    assert params["format"] == "json"
    assert params["pageSize"] == 1000
    assert params["cursorMark"] == "*"


def test_two_pages_follow_the_cursor():
    client = _client(
        [
            _page([_hit(1), _hit(2)], 3, "AoE1"),
            _page([_hit(3)], 3, "AoE1"),
        ]
    )
    result, records = _run(client)
    assert [r.title for r in records] == ["Trial 1", "Trial 2", "Trial 3"]
    assert _params(client, 1)["cursorMark"] == "AoE1"
    assert client.get.call_count == 2
    assert result.status == "completed"
    assert result.record_count == 3
    assert result.source == "europepmc"


def test_unchanged_cursor_on_first_page_stops():
    client = _client([_page([_hit(1)], 1, "*")])
    result, records = _run(client)
    assert client.get.call_count == 1
    assert len(records) == 1
    assert result.status == "completed"


def test_error_body_fails_day():
    client = _client([{"errMsg": "Invalid query"}])
    result, records = _run(client)
    assert result.status == "failed"
    assert "Europe PMC" in result.error
    assert records == []


def test_short_walk_fails_day():
    client = _client(
        [
            _page([_hit(1), _hit(2)], 5, "AoE1"),
            _page([], 5, "AoE2"),
        ]
    )
    result, records = _run(client)
    assert len(records) == 2
    assert result.status == "failed"
    assert result.record_count == 2


def test_transport_error_fails_day():
    client = MagicMock()
    client.get.side_effect = OSError("network is unreachable")
    result, _ = _run(client)
    assert result.status == "failed"
    assert "OSError" in result.error


def test_oa_record_has_fulltext_xml_source():
    client = _client([_page([_hit(1, pmcid="PMC123")], 1, "*")])
    _, records = _run(client)
    [source] = records[0].fulltext_sources
    assert source.url.endswith("/PMC123/fullTextXML")
    assert source.format == "xml"


def test_blank_query_fails_without_a_request():
    client = MagicMock()
    result, _ = _run(client, query="   ")
    assert result.status == "failed"
    assert "query" in result.error
    client.get.assert_not_called()


def test_email_is_sent_when_given():
    client = _client([_page([], 0, "*")])
    with patch("bmlib.publications.fetchers.europepmc.time.sleep"):
        fetch_europepmc(client, DAY, on_record=lambda r: None, query=QUERY, email="a@b.org")
    assert _params(client, 0)["email"] == "a@b.org"


def test_registered_with_a_required_query():
    _ensure_builtins()
    descriptor, fetcher = get_source("europepmc")
    assert fetcher is fetch_europepmc
    assert descriptor.display_name == "Europe PMC"
    query = [p for p in descriptor.params if p.name == "query"]
    assert query and query[0].required


@pytest.mark.parametrize("hit_count", [None, "3", True])
def test_a_non_numeric_hit_count_fails_day(hit_count):
    client = _client([{"hitCount": hit_count, "resultList": {"result": []}}])
    result, _ = _run(client)
    assert result.status == "failed"


def test_a_non_object_hit_fails_day():
    client = _client([_page([_hit(1), "not a hit"], 2, "*")])
    result, records = _run(client)
    assert result.status == "failed"
    assert "could not normalise" in result.error
    assert len(records) == 1
