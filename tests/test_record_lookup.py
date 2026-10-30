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

"""One paper's record by identifier (StudyAnalyzer item 5).

The spec is ``docs/superpowers/specs/2026-10-30-record-lookup-design.md``. The
shared fixture ``tests/fixtures/record_lookup.json`` holds Rust to these
results; the tests here pin the Python, its messages included.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
import pytest

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications import lookup
from bmlib.publications.fetchers.pubmed import EFETCH_URL
from bmlib.publications.lookup import (
    EUROPEPMC_SEARCH_URL,
    OPENALEX_WORKS_URL,
    PublishedVersion,
    RecordLookup,
    SourceOutcome,
    lookup_record,
)

EMAIL = "a@b.c"
DOCTYPE = (
    '<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, '
    '1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">\n'
)


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.content = text.encode("utf-8")


class FakeClient:
    """Serves a scripted answer per request, in order, and records each request."""

    def __init__(self, *answers: FakeResponse | BaseException) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.requests.append((url, dict(params or {})))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def ok(body: Any) -> FakeResponse:
    return FakeResponse(200, body if isinstance(body, str) else json.dumps(body))


def status(code: int) -> FakeResponse:
    return FakeResponse(code, "<!doctype html><title>Not Found</title>")


def work(
    *,
    doi: str | None = "10.1/x",
    pmid: str | None = None,
    pmcid: str | None = None,
    title: str = "OpenAlex title",
    locations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ids: dict[str, Any] = {"openalex": "https://openalex.org/W1"}
    if pmid:
        ids["pmid"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"
    if pmcid:
        ids["pmcid"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}"
    return {
        "id": "https://openalex.org/W1",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "ids": ids,
        "title": title,
        "open_access": {"is_oa": True},
        "locations": locations
        if locations is not None
        else [{"pdf_url": "https://repo/x.pdf", "is_oa": True, "version": "acceptedVersion"}],
    }


def article(
    pmid: str, *, doi: str | None = None, pmc: str | None = None, title: str = "PubMed title"
) -> str:
    ids = "".join(
        f'<ArticleId IdType="{kind}">{value}</ArticleId>'
        for kind, value in (("doi", doi), ("pmc", pmc))
        if value
    )
    return (
        f'<PubmedArticle><MedlineCitation><PMID Version="1">{pmid}</PMID><Article>'
        f"<Journal><Title>The Journal</Title></Journal><ArticleTitle>{title}</ArticleTitle>"
        "<AuthorList><Author><LastName>Smith</LastName><ForeName>Jo</ForeName></Author></AuthorList>"
        f"</Article></MedlineCitation><PubmedData><ArticleIdList>{ids}</ArticleIdList></PubmedData>"
        "</PubmedArticle>"
    )


def article_set(*children: str) -> str:
    return f"{DOCTYPE}<PubmedArticleSet>{''.join(children)}</PubmedArticleSet>"


def epmc_hit(
    ppr: str = "PPR1325930", *, doi: str | None = "10.21203/rs.3.rs-1/v1"
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": ppr,
        "source": "PPR",
        "title": "The preprint",
        "inEPMC": "Y",
        "isOpenAccess": "Y",
    }
    if doi:
        record["doi"] = doi
    return {"hitCount": 1, "resultList": {"result": [record]}}


def openalex_url(key: str) -> str:
    return f"{OPENALEX_WORKS_URL}/{key}"


class TestValidation:
    @pytest.mark.parametrize(
        ("kwargs", "argument"),
        [
            ({"pmid": "12a"}, "pmid"),
            ({"pmid": "0"}, "pmid"),
            ({"pmid": "١٢٣"}, "pmid"),
            ({"pmc_id": "PMC١٢٣"}, "pmc_id"),
            ({"pmc_id": "12x"}, "pmc_id"),
            ({"ppr_id": "PR12"}, "ppr_id"),
            ({"ppr_id": "PPR"}, "ppr_id"),
        ],
    )
    def test_a_malformed_identifier_raises_before_any_request(self, kwargs, argument):
        client = FakeClient()
        with pytest.raises(ValueError, match=rf"^{argument} "):
            lookup_record(client, email=EMAIL, **kwargs)
        assert client.requests == []

    @pytest.mark.parametrize("kwargs", [{}, {"pmid": "  ", "doi": "", "pmc_id": " ", "ppr_id": ""}])
    def test_no_identifier_raises(self, kwargs):
        with pytest.raises(
            ValueError, match="^lookup_record needs a PMID, DOI, PMC ID or PPR accession$"
        ):
            lookup_record(FakeClient(), email=EMAIL, **kwargs)

    def test_a_pmid_that_is_not_text_is_a_type_error(self):
        with pytest.raises(TypeError, match="^pmid must be a str"):
            lookup_record(FakeClient(), email=EMAIL, pmid=123)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("kwargs", "key"),
        [
            ({"pmid": " 0012345 "}, "pmid:12345"),
            ({"pmc_id": "pmc7745181"}, "pmcid:PMC7745181"),
            ({"pmc_id": "7745181"}, "pmcid:PMC7745181"),
            ({"doi": "DOI:10.1056/NEJMoa2034577"}, "doi:10.1056/nejmoa2034577"),
            ({"doi": "https://doi.org/10.1/a?b#c d"}, "doi:10.1/a%3Fb%23c%20d"),
        ],
    )
    def test_the_identifier_is_canonical_in_the_request(self, kwargs, key):
        client = FakeClient(status(404), ok(article_set()))
        lookup_record(client, email=EMAIL, **kwargs)
        assert client.requests[0] == (openalex_url(key), {"mailto": EMAIL})

    def test_a_ppr_accession_is_case_folded(self):
        client = FakeClient(ok({"hitCount": 0, "resultList": {"result": []}}))
        lookup_record(client, email=EMAIL, ppr_id="ppr1325930")
        assert client.requests[0][1]["query"] == "EXT_ID:PPR1325930 AND SRC:PPR"

    @pytest.mark.parametrize("value", ["1", " 001 ", "0", "12a", "١٢٣", "", "  ", "0012"])
    def test_the_pmid_rule_agrees_with_transparency(self, value):
        from bmlib.transparency.analyzer import _caller_pmid

        def outcome(rule):
            try:
                return rule(value)
            except ValueError:
                return "refused"

        assert outcome(lookup._lookup_pmid) == outcome(_caller_pmid)


class TestTheSources:
    def test_pubmed_is_the_record_and_openalex_adds_full_text(self):
        client = FakeClient(
            ok(work(pmid="123", pmcid="7745181")),
            ok(article_set(article("123", doi="10.1/X"))),
        )
        result = lookup_record(client, email=EMAIL, pmid="123", ncbi_api_key="k")

        record = result.record
        assert record is not None
        assert (record.source, record.title, record.authors) == (
            "pubmed",
            "PubMed title",
            ["Smith, Jo"],
        )
        assert (record.pmid, record.doi, record.pmc_id) == ("123", "10.1/X", "PMC7745181")
        assert [e.url for e in record.fulltext_sources] == [
            "https://doi.org/10.1/X",
            "https://repo/x.pdf",
        ]
        assert record.is_open_access is True
        assert (result.pubmed.outcome, result.openalex.outcome, result.europepmc.outcome) == (
            SourceOutcome.FOUND,
            SourceOutcome.FOUND,
            SourceOutcome.NOT_ASKED,
        )
        assert client.requests == [
            (openalex_url("pmid:123"), {"mailto": EMAIL}),
            (EFETCH_URL, {"db": "pubmed", "id": "123", "retmode": "xml", "api_key": "k"}),
        ]

    def test_openalex_supplies_the_pmid(self):
        client = FakeClient(ok(work(pmid="123")), ok(article_set(article("123", doi="10.1/x"))))
        result = lookup_record(client, email=EMAIL, doi="10.1/x")
        assert result.record is not None and result.record.source == "pubmed"
        assert client.requests[1][1]["id"] == "123"
        assert result.published_version is None

    def test_openalex_is_the_record_when_pubmed_has_none(self):
        client = FakeClient(ok(work()))
        result = lookup_record(client, email=EMAIL, doi="10.1/x", openalex_api_key="ok")
        assert result.record is not None and result.record.source == "openalex"
        assert result.pubmed == lookup.SourceReport(SourceOutcome.NOT_ASKED, "no PMID")
        assert client.requests == [(openalex_url("doi:10.1/x"), {"mailto": EMAIL, "api_key": "ok"})]

    def test_a_failing_source_leaves_the_others_record(self):
        client = FakeClient(status(503), ok(article_set(article("123"))))
        result = lookup_record(client, email=EMAIL, pmid="123")
        assert result.record is not None and result.record.source == "pubmed"
        assert result.openalex == lookup.SourceReport(SourceOutcome.REQUEST_FAILED, "HTTP 503")

    def test_neither_source_has_it(self):
        client = FakeClient(status(404), ok(article_set()))
        result = lookup_record(client, email=EMAIL, pmid="123")
        assert result.record is None
        assert result.openalex.outcome == result.pubmed.outcome == SourceOutcome.NOT_FOUND

    def test_a_bug_type_from_the_client_propagates(self):
        with pytest.raises(TypeError):
            lookup_record(FakeClient(TypeError("bmlib is wrong")), email=EMAIL, pmid="1")


class TestTheBodyIsReadAsUtf8:
    @pytest.mark.parametrize("charset", ["rot13", "base64", "latin-1"])
    def test_a_declared_charset_is_not_trusted(self, charset):
        # rot13 and base64 do not decode bytes to text: response.text raised.
        answer = httpx.Response(
            200,
            headers={"content-type": f"application/json; charset={charset}"},
            content=json.dumps(work(title="Café")).encode("utf-8"),
        )
        result = lookup_record(FakeClient(answer), email=EMAIL, doi="10.1/x")
        assert result.openalex.outcome is SourceOutcome.FOUND
        assert result.record is not None and result.record.title == "Café"

    @pytest.mark.parametrize("source", ["openalex", "pubmed", "europepmc"])
    def test_a_body_that_is_not_utf8_is_unreadable(self, source):
        bad = httpx.Response(200, content=b'{"title": "a\xff"}')
        answers = {
            "openalex": (dict(pmid="123"), [bad, ok(article_set(article("123")))]),
            "pubmed": (dict(pmid="123"), [status(404), bad]),
            "europepmc": (dict(ppr_id="PPR1"), [bad]),
        }
        kwargs, scripted = answers[source]
        result = lookup_record(FakeClient(*scripted), email=EMAIL, **kwargs)
        report = getattr(result, source)
        assert report == lookup.SourceReport(SourceOutcome.UNREADABLE, "the body is not UTF-8 text")

    def test_json_nested_past_the_decoder_is_unreadable(self):
        result = lookup_record(FakeClient(ok("[" * 100_000)), email=EMAIL, doi="10.1/x")
        assert result.openalex == lookup.SourceReport(
            SourceOutcome.UNREADABLE, "the body is not JSON"
        )

    def test_a_lone_surrogate_in_a_body_is_unreadable(self):
        # It raised UnicodeEncodeError out of quote() once Europe PMC's DOI reached it.
        client = FakeClient(ok(epmc_hit(doi="10.1/\ud800x")))
        result = lookup_record(client, email=EMAIL, ppr_id="PPR1325930")
        assert result.europepmc == lookup.SourceReport(
            SourceOutcome.UNREADABLE, "the body carries a lone surrogate"
        )
        assert result.record is None and len(client.requests) == 1

    def test_a_callers_doi_with_a_lone_surrogate_raises_before_any_request(self):
        client = FakeClient()
        with pytest.raises(ValueError, match=r"^doi '10\.1/\\ud800' carries a lone surrogate$"):
            lookup_record(client, email=EMAIL, ppr_id="PPR1", doi="10.1/\ud800")
        assert client.requests == []


class TestTheLogLevels:
    def test_a_source_that_could_not_be_used_warns_and_an_answer_does_not(self, caplog):
        client = FakeClient(ok(work(pmid="123")), status(503))
        with caplog.at_level(logging.DEBUG, logger="bmlib.publications.lookup"):
            lookup_record(client, email=EMAIL, pmid="123")
        assert caplog.record_tuples == [
            (
                "bmlib.publications.lookup",
                logging.DEBUG,
                "lookup_record: europepmc: not_asked (no PPR accession)",
            ),
            ("bmlib.publications.lookup", logging.DEBUG, "lookup_record: openalex: found"),
            (
                "bmlib.publications.lookup",
                logging.WARNING,
                "lookup_record: pubmed: request_failed (HTTP 503)",
            ),
        ]

    def test_dois_differing_only_in_case_do_not_warn(self, caplog):
        client = FakeClient(status(404), ok(article_set(article("123", doi="10.1/X"))))
        with caplog.at_level(logging.WARNING, logger="bmlib.publications.lookup"):
            lookup_record(client, email=EMAIL, pmid="123", doi="10.1/x")
        assert caplog.records == []


def test_a_full_text_source_two_records_carry_is_listed_once():
    entry = FullTextSourceEntry(url="https://repo/x.pdf", format="pdf", source="pubmed")
    twin = FullTextSourceEntry(url="https://repo/x.pdf", format="pdf", source="openalex")
    from bmlib.publications.models import FetchedRecord

    base = FetchedRecord(title="T", source="pubmed", fulltext_sources=[entry])
    other = FetchedRecord(title="T", source="openalex", fulltext_sources=[twin])
    caller = {"pmid": None, "doi": None, "pmc_id": None}
    assert lookup._assemble(base, [other], caller, None).fulltext_sources == [entry]


class TestEachOutcome:
    @pytest.mark.parametrize(
        ("answer", "outcome", "message"),
        [
            (status(404), SourceOutcome.NOT_FOUND, "OpenAlex holds no work for doi:10.1/x"),
            (status(503), SourceOutcome.REQUEST_FAILED, "HTTP 503"),
            (httpx.ConnectError("down"), SourceOutcome.REQUEST_FAILED, "ConnectError: down"),
            (ok("<html>"), SourceOutcome.UNREADABLE, "the body is not JSON"),
            (ok([]), SourceOutcome.UNREADABLE, "the body is a list, not a JSON object"),
            (
                ok(work(doi="10.1/other", pmid="9")),
                SourceOutcome.OTHER_RECORD,
                "OpenAlex's work for doi:10.1/x carries doi '10.1/other'",
            ),
        ],
    )
    def test_openalex(self, answer, outcome, message):
        client = FakeClient(answer)
        result = lookup_record(client, email=EMAIL, doi="10.1/x")
        assert result.openalex == lookup.SourceReport(outcome, message)
        assert result.record is None
        assert len(client.requests) == 1  # another work's PMID is not followed

    @pytest.mark.parametrize(
        ("body", "outcome", "message"),
        [
            (article_set(), SourceOutcome.NOT_FOUND, "PubMed holds no record for PMID 123"),
            (
                "<eFetchResult><ERROR>x</ERROR></eFetchResult>",
                SourceOutcome.UNREADABLE,
                "efetch answered with an <eFetchResult> root, not <PubmedArticleSet>",
            ),
            ("not xml", SourceOutcome.UNREADABLE, "the body is not XML"),
            (
                article_set(article("999")),
                SourceOutcome.OTHER_RECORD,
                "efetch served no record for PMID 123",
            ),
            (
                article_set(
                    "<PubmedBookArticle><BookDocument><PMID>123</PMID></BookDocument>"
                    "</PubmedBookArticle>"
                ),
                SourceOutcome.BOOK_RECORD,
                "PMID 123 is a Bookshelf record",
            ),
        ],
    )
    def test_pubmed(self, body, outcome, message):
        client = FakeClient(ok(work(pmid="123")), ok(body))
        result = lookup_record(client, email=EMAIL, pmid="123")
        assert result.pubmed == lookup.SourceReport(outcome, message)
        # OpenAlex's record stands in wherever PubMed's is not usable.
        assert result.record is not None and result.record.source == "openalex"

    def test_a_matching_article_beside_others_is_found(self):
        client = FakeClient(status(404), ok(article_set(article("999"), article("123"))))
        result = lookup_record(client, email=EMAIL, pmid="123")
        assert result.record is not None and result.record.pmid == "123"

    @pytest.mark.parametrize(
        ("body", "outcome", "message"),
        [
            (
                {"hitCount": 0, "resultList": {"result": []}},
                SourceOutcome.NOT_FOUND,
                "Europe PMC holds no record for PPR1325930",
            ),
            (
                {"resultList": None},
                SourceOutcome.UNREADABLE,
                "the body carries no resultList.result list",
            ),
            (
                epmc_hit("PPR9"),
                SourceOutcome.OTHER_RECORD,
                "Europe PMC answered with 'PPR9', not PPR1325930",
            ),
        ],
    )
    def test_europepmc(self, body, outcome, message):
        client = FakeClient(ok(body))
        result = lookup_record(client, email=EMAIL, ppr_id="PPR1325930")
        assert result.europepmc == lookup.SourceReport(outcome, message)
        assert result.openalex.outcome == SourceOutcome.NOT_ASKED
        assert result.record is None


class TestAPreprint:
    def test_a_merged_preprint_returns_the_preprint(self):
        preprint = "10.1101/2020.03.22.002386"
        client = FakeClient(
            ok(
                work(
                    doi=preprint,
                    pmid="32511329",
                    pmcid="7239059",
                    locations=[
                        {
                            "pdf_url": "https://biorxiv/x.pdf",
                            "version": "submittedVersion",
                            "is_oa": True,
                        },
                        {
                            "pdf_url": "https://nature/x.pdf",
                            "version": "publishedVersion",
                            "is_oa": True,
                        },
                    ],
                )
            ),
            ok(article_set(article("32511329", doi="10.1038/S41586-020-2286-9", pmc="PMC7239059"))),
        )
        result = lookup_record(client, email=EMAIL, doi=preprint)

        record = result.record
        assert record is not None
        assert (record.source, record.doi, record.pmid, record.pmc_id) == (
            "openalex",
            preprint,
            None,
            None,
        )
        assert [e.url for e in record.fulltext_sources] == ["https://biorxiv/x.pdf"]
        assert result.published_version == PublishedVersion(
            "32511329", "10.1038/S41586-020-2286-9", "PMC7239059"
        )
        assert result.pubmed.outcome == SourceOutcome.FOUND
        assert result.pubmed.message == (
            "PMID 32511329 is a published version (DOI 10.1038/S41586-020-2286-9), "
            "returned as published_version"
        )

    @pytest.mark.parametrize(
        "pubmed_answer",
        [status(503), ok(article_set()), ok(article_set(article("999")))],
        ids=["outage", "not-found", "other-record"],
    )
    def test_an_unconfirmed_pmid_does_not_enter_a_doi_lookups_record(self, pubmed_answer):
        # A merged work's PMID and PMC ID are the journal version's; only a
        # PubMed record compared against the DOI can say so, so without one
        # they stay out (PR review: an outage decided which version you got).
        preprint = "10.1101/2020.03.22.002386"
        client = FakeClient(ok(work(doi=preprint, pmid="32511329", pmcid="7239059")), pubmed_answer)
        result = lookup_record(client, email=EMAIL, doi=preprint)

        record = result.record
        assert record is not None and record.source == "openalex"
        assert (record.doi, record.pmid, record.pmc_id) == (preprint, None, None)
        assert result.published_version is None

    def test_an_unconfirmed_pmid_still_enters_a_pmc_lookups_record(self):
        # Asked by PMC ID, the work carries that PMC ID, so its PMID is that paper's.
        client = FakeClient(ok(work(pmid="123", pmcid="7745181")), status(503))
        result = lookup_record(client, email=EMAIL, pmc_id="PMC7745181")
        assert result.record is not None and result.record.pmid == "123"

    def test_the_dois_compare_case_folded(self):
        client = FakeClient(
            ok(work(doi="10.1/x", pmid="123")), ok(article_set(article("123", doi="10.1/X")))
        )
        result = lookup_record(client, email=EMAIL, doi="10.1/x")
        assert result.published_version is None
        assert result.record is not None and result.record.source == "pubmed"

    def test_a_pubmed_record_with_no_doi_is_the_same_paper(self):
        client = FakeClient(ok(work(doi="10.1/x", pmid="123")), ok(article_set(article("123"))))
        result = lookup_record(client, email=EMAIL, doi="10.1/x")
        assert result.published_version is None
        assert result.record is not None and result.record.doi == "10.1/x"

    def test_a_ppr_is_looked_up_through_europepmc(self):
        client = FakeClient(ok(epmc_hit()), ok(work(doi="10.21203/rs.3.rs-1/v1")))
        result = lookup_record(client, email=EMAIL, ppr_id="PPR1325930")

        record = result.record
        assert record is not None
        assert record.source == "europepmc"
        assert record.extras == {"ppr_id": "PPR1325930"}
        assert [e.format for e in record.fulltext_sources] == ["xml", "pdf"]
        assert client.requests[0] == (
            EUROPEPMC_SEARCH_URL,
            {"query": "EXT_ID:PPR1325930 AND SRC:PPR", "resultType": "core", "format": "json"},
        )
        assert client.requests[1][0] == openalex_url("doi:10.21203/rs.3.rs-1/v1")

    def test_a_ppr_with_a_published_version(self):
        client = FakeClient(
            ok(epmc_hit()),
            ok(work(doi="10.21203/rs.3.rs-1/v1", pmid="77")),
            ok(article_set(article("77", doi="10.1/published"))),
        )
        result = lookup_record(client, email=EMAIL, ppr_id="PPR1325930")
        assert result.record is not None and result.record.source == "europepmc"
        assert result.record.pmid is None
        assert result.published_version == PublishedVersion("77", "10.1/published", None)


class TestTheCallersIdentifiers:
    def test_a_caller_identifier_fills_an_empty_field(self):
        client = FakeClient(status(404), ok(article_set(article("123"))))
        result = lookup_record(client, email=EMAIL, pmid="123", doi="10.1/Given")
        assert result.record is not None and result.record.doi == "10.1/given"

    def test_a_differing_caller_identifier_never_replaces(self, caplog):
        client = FakeClient(status(404), ok(article_set(article("123", doi="10.1/pubmed"))))
        with caplog.at_level(logging.WARNING, logger="bmlib.publications.lookup"):
            result = lookup_record(client, email=EMAIL, pmid="123", doi="10.1/caller")
        assert result.record is not None and result.record.doi == "10.1/pubmed"
        assert [r.getMessage() for r in caplog.records] == [
            "lookup_record: the caller's doi '10.1/caller' differs from the record's "
            "'10.1/pubmed'; keeping the record's"
        ]

    def test_the_ppr_accession_rides_on_any_record(self):
        client = FakeClient(
            ok({"hitCount": 0, "resultList": {"result": []}}),
            status(404),
            ok(article_set(article("123"))),
        )
        result = lookup_record(client, email=EMAIL, pmid="123", ppr_id="PPR5")
        assert result.record is not None and result.record.extras == {"ppr_id": "PPR5"}


class TestSerialisation:
    def test_to_dict_round_trips(self):
        client = FakeClient(
            ok(work(pmid="123")), ok(article_set(article("123", doi="10.1/X", pmc="PMC1")))
        )
        result = lookup_record(client, email=EMAIL, pmid="123")
        assert RecordLookup.from_dict(json.loads(json.dumps(result.to_dict()))) == result

    def test_from_dict_refuses_a_foreign_record(self):
        data = RecordLookup(
            record=None,
            published_version=None,
            pubmed=lookup.SourceReport(SourceOutcome.NOT_ASKED, "no PMID"),
            openalex=lookup.SourceReport(SourceOutcome.NOT_FOUND, "x"),
            europepmc=lookup.SourceReport(SourceOutcome.NOT_ASKED, "no PPR accession"),
        ).to_dict()
        data["pubmed"]["outcome"] = "maybe"
        with pytest.raises(ValueError):
            RecordLookup.from_dict(data)
        with pytest.raises(ValueError):
            RecordLookup.from_dict({"record": None})

    @pytest.mark.parametrize("key", ["fulltext_sources", "grants", "author_affiliations"])
    def test_a_fetched_record_with_a_malformed_list_is_a_value_error(self, key):
        from bmlib.publications.models import FetchedRecord

        data = FetchedRecord(title="T", source="s").to_dict()
        data[key] = "x"
        with pytest.raises(ValueError):
            FetchedRecord.from_dict(data)

    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("title", 5, "FetchedRecord.title is not text"),
            ("pmid", ["x"], "FetchedRecord.pmid is not text or None"),
            ("authors", "abc", "FetchedRecord.authors is not a list of text"),
            ("keywords", [1], "FetchedRecord.keywords is not a list of text"),
            ("is_open_access", "false", "FetchedRecord.is_open_access is not a boolean"),
            ("extras", "s", "FetchedRecord.extras is not an object"),
            ("grants", ["x"], "FetchedRecord.grants[0] is not an entry"),
            ("grants", [{}], "FetchedRecord.grants[0] is not an entry"),
            ("fulltext_sources", [{"url": 1}], "FetchedRecord.fulltext_sources[0] is not an entry"),
            ("author_affiliations", [None], "FetchedRecord.author_affiliations[0] is not an entry"),
        ],
    )
    def test_a_fetched_record_field_of_the_wrong_type_is_named(self, key, value, message):
        from bmlib.publications.models import FetchedRecord

        data = FetchedRecord(title="T", source="s").to_dict()
        data[key] = value
        with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
            FetchedRecord.from_dict(data)

    def test_a_source_report_names_a_member_or_raises(self):
        assert lookup.SourceReport("found", "x").outcome is SourceOutcome.FOUND  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            lookup.SourceReport("maybe", "x")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="^a SourceReport message is text$"):
            lookup.SourceReport(SourceOutcome.FOUND, 5)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("record", "published", "found", "message"),
        [
            (True, False, (), "a RecordLookup carries a record exactly"),
            (False, False, ("openalex",), "a RecordLookup carries a record exactly"),
            (True, True, ("openalex",), "a published version is named only beside"),
            (False, True, (), "a published version is named only beside"),
        ],
    )
    def test_a_lookup_that_contradicts_itself_is_refused(self, record, published, found, message):
        from bmlib.publications.models import FetchedRecord

        reports = {
            source: lookup.SourceReport(
                SourceOutcome.FOUND if source in found else SourceOutcome.NOT_FOUND, ""
            )
            for source in ("pubmed", "openalex", "europepmc")
        }
        with pytest.raises(ValueError, match=f"^{message}"):
            RecordLookup(
                record=FetchedRecord(title="T", source="s") if record else None,
                published_version=PublishedVersion("1", "10.1/x", None) if published else None,
                **reports,
            )

    @pytest.mark.parametrize(
        "published",
        [
            {"pmid": 5, "doi": "d", "pmc_id": None},
            {"pmid": "1", "doi": None, "pmc_id": None},
            {"pmid": "1", "doi": "d", "pmc_id": {}},
        ],
    )
    def test_a_published_version_of_the_wrong_type_is_refused(self, published):
        with pytest.raises(ValueError, match="^a PublishedVersion's"):
            PublishedVersion(**published)

    def test_a_fetched_record_round_trips(self):
        from bmlib.publications.models import FetchedRecord

        record = FetchedRecord(
            title="T",
            source="pubmed",
            fulltext_sources=[FullTextSourceEntry(url="u", format="pdf", source="s")],
            extras={"ppr_id": "PPR1"},
        )
        assert FetchedRecord.from_dict(record.to_dict()) == record
