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

"""Tests for the Europe PMC day-by-day sync fetcher (``fetch_europepmc``).

The shared oracle corpus (``rust/oracle/europepmc_sync_cases.json``) pins every
outcome in both languages; these pin what it cannot see: the pause between
requests, the registry and ``sync()``'s default set, and a failing ``on_record``.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from bmlib.db import connect_sqlite
from bmlib.publications.fetchers import ALL_SOURCES
from bmlib.publications.fetchers.europepmc import EUROPEPMC_REST_BASE, fetch_europepmc
from bmlib.publications.fetchers.registry import _ensure_builtins, get_source
from bmlib.publications.models import FetchedRecord, FetchResult
from bmlib.publications.sync import sync

DAY = date(2024, 3, 5)
QUERY = 'OPEN_ACCESS:Y AND (TITLE:"depression")'


def _hit(n: int, *, pmcid: str | None = None, open_access: bool = True) -> dict:
    """One ``resultType=core`` hit, first indexed on :data:`DAY`."""
    hit: dict = {
        "id": str(40000000 + n),
        "source": "MED",
        "pmid": str(40000000 + n),
        "title": f"Trial {n}",
        "firstPublicationDate": "2024-01-20",
        "firstIndexDate": "2024-03-05",
        "isOpenAccess": "Y" if open_access else "N",
        "inEPMC": "Y" if pmcid else "N",
    }
    if pmcid:
        hit["pmcid"] = pmcid
    return hit


def _page(hits: list[object], hit_count: object, next_cursor: str | None) -> dict:
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


def _run(
    client: MagicMock, query: str = QUERY, email: str | None = None
) -> tuple[FetchResult, list[FetchedRecord], MagicMock]:
    records: list[FetchedRecord] = []
    with patch("bmlib.publications.fetchers.europepmc.time.sleep") as sleep:
        result = fetch_europepmc(client, DAY, on_record=records.append, query=query, email=email)
    return result, records, sleep


def _params(client: MagicMock, n: int) -> dict:
    return client.get.call_args_list[n].kwargs["params"]


def test_query_gets_the_index_day_clause():
    client = _client([_page([_hit(1)], 1, None)])
    _run(client)
    assert client.get.call_args_list[0].args[0] == f"{EUROPEPMC_REST_BASE}/search"
    assert _params(client, 0) == {
        "query": f"({QUERY}) AND FIRST_IDATE:[2024-03-05 TO 2024-03-05]",
        "resultType": "core",
        "format": "json",
        "pageSize": 1000,
        "cursorMark": "*",
    }


def test_two_pages_follow_the_cursor_with_one_pause_between():
    client = _client([_page([_hit(1), _hit(2)], 3, "AoE1"), _page([_hit(3)], 3, None)])
    result, records, sleep = _run(client)
    assert [r.title for r in records] == ["Trial 1", "Trial 2", "Trial 3"]
    assert _params(client, 1)["cursorMark"] == "AoE1"
    assert result == FetchResult(
        source="europepmc", date="2024-03-05", record_count=3, status="completed"
    )
    assert sleep.call_args_list == [call(1.0)]


def test_a_one_page_day_does_not_pause():
    client = _client([_page([_hit(1)], 1, None)])
    result, _, sleep = _run(client)
    assert result.status == "completed"
    sleep.assert_not_called()


def test_an_empty_day_pauses_before_checking_the_query_with_the_email():
    client = _client([_page([], 0, None), {"hitCount": 9, "resultList": {"result": []}}])
    result, _, sleep = _run(client, email="a@b.org")
    assert result.status == "completed"
    assert _params(client, 0)["email"] == "a@b.org"
    assert _params(client, 1) == {
        "query": f"({QUERY})",
        "resultType": "lite",
        "format": "json",
        "pageSize": 1,
        "email": "a@b.org",
    }
    assert sleep.call_args_list == [call(1.0)]


def test_a_walk_short_of_its_count_fails_however_little_is_missing():
    client = _client([_page([_hit(n) for n in range(1, 100)], 100, None)])
    result, records, _ = _run(client)
    assert len(records) == 99
    assert result.status == "failed"
    assert result.record_count == 99
    assert result.error == (
        "europepmc delivered 99 of 100 records for 2024-03-05 and then sent no new"
        " cursor, so the walk stopped short"
    )


def test_transport_error_fails_day_naming_it():
    client = MagicMock()
    client.get.side_effect = OSError("network is unreachable")
    result, _, _ = _run(client)
    assert result.status == "failed"
    assert result.error == "OSError: network is unreachable"


def test_a_failing_on_record_is_not_blamed_on_europe_pmc():
    client = _client([_page([_hit(1), _hit(2)], 2, None)])

    def on_record(record: FetchedRecord) -> None:
        if record.title == "Trial 2":
            raise RuntimeError("database is locked")

    with patch("bmlib.publications.fetchers.europepmc.time.sleep"):
        result = fetch_europepmc(client, DAY, on_record=on_record, query=QUERY)
    assert result.status == "failed"
    assert result.record_count == 1
    assert result.error == (
        "Handing on a Europe PMC record for 2024-03-05 failed: RuntimeError: database is locked"
    )


def test_oa_record_has_fulltext_xml_source():
    client = _client([_page([_hit(1, pmcid="PMC123")], 1, None)])
    _, records, _ = _run(client)
    [source] = records[0].fulltext_sources
    assert source.url.endswith("/PMC123/fullTextXML")
    assert source.format == "xml"


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_blank_query_fails_without_a_request(query):
    client = MagicMock()
    result, _, _ = _run(client, query=query)
    assert result.status == "failed"
    assert result.error == "Europe PMC needs a search: the query parameter is empty"
    client.get.assert_not_called()


def test_a_query_blank_only_to_python_is_sent_as_rust_sends_it():
    """U+001C is whitespace to ``str.strip`` but not to Rust's ``trim`` (#557)."""
    client = _client([_page([], 0, None), {"hitCount": 0, "resultList": {"result": []}}])
    result, _, _ = _run(client, query="\x1c")
    assert client.get.call_count == 2
    assert result.status == "failed"
    assert "the query is broken" in result.error


@pytest.mark.parametrize("hit_count", [None, "3", True, 2.0, -1])
def test_a_hit_count_that_is_not_a_count_fails_day(hit_count):
    client = _client([_page([], hit_count, None)])
    result, _, _ = _run(client)
    assert result.status == "failed"
    assert result.error == (
        "Europe PMC returned a page with no hitCount that is a count for 2024-03-05"
    )


def test_registered_as_an_opt_in_source_with_a_required_query():
    _ensure_builtins()
    descriptor, fetcher = get_source("europepmc")
    assert fetcher is fetch_europepmc
    assert descriptor.display_name == "Europe PMC"
    assert [(p.name, p.required) for p in descriptor.params] == [
        ("query", True),
        ("email", False),
    ]
    assert descriptor.opt_in is True
    assert descriptor.settle_days == 3


def test_sync_with_no_sources_named_leaves_europepmc_out():
    """Without the query it needs it would fail every default run's days, for ever."""
    ran: list[str] = []

    def fake_get_fetcher(name):
        def fetcher(client, target_date, *, on_record, on_progress=None, **kwargs):
            ran.append(name)
            return FetchResult(
                source=name, date=target_date.isoformat(), record_count=0, status="completed"
            )

        return fetcher

    conn = connect_sqlite(":memory:")
    with patch("bmlib.publications.sync.get_fetcher", side_effect=fake_get_fetcher):
        report = sync(conn, date_from=DAY, date_to=DAY)
    assert "europepmc" in ALL_SOURCES
    assert sorted(report.sources_synced) == sorted(s for s in ALL_SOURCES if s != "europepmc")
    assert "europepmc" not in ran
