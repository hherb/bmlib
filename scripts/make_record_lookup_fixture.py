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

"""Write ``tests/fixtures/record_lookup.json`` from the Python ``lookup_record``.

The shared record-lookup contract, which Rust's
``rust/bmlib/tests/record_lookup.rs`` replays. Each case gives the caller's
identifiers and the scripted answer to each request in order (a status and a
body, or a transport failure); the expectation is the requests Python made and
its result, every report's ``message`` left out — a transport failure's text is
the HTTP library's, and differs between httpx and ureq. Python's messages are
pinned by ``tests/test_record_lookup.py``, the hand-checked half; this file's
expectations are the Python's output, so a case is only as good as the tests
that pinned its behaviour there.

    python scripts/make_record_lookup_fixture.py [tests/fixtures/record_lookup.json]

``tests/test_record_lookup_contract.py`` checks that this script still builds
the committed fixture, so add a case here and re-run it rather than editing the
JSON by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

from bmlib.publications.lookup import lookup_record

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "record_lookup.json"
)
EMAIL = "a@b.c"
# Every live efetch answer carries this prologue, and Rust's parser refuses a
# DTD unless told otherwise (`parse_ncbi_xml`), so every PubMed body has it.
DOCTYPE = (
    '<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, '
    '1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">\n'
)
TRANSPORT = {"raises": "transport"}


def ok(body: Any) -> dict[str, Any]:
    return {"status": 200, "body": body if isinstance(body, str) else json.dumps(body)}


def status(code: int) -> dict[str, Any]:
    return {"status": code, "body": "<!doctype html><title>Not Found</title>"}


def work(
    *,
    doi: str | None = "10.1/x",
    pmid: str | None = None,
    pmcid: str | None = None,
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
        "title": "OpenAlex title",
        "authorships": [{"author": {"display_name": "Jo Smith"}}],
        "primary_location": {"source": {"display_name": "A Venue"}},
        "publication_date": "2024-06-15",
        "open_access": {"is_oa": True},
        "locations": locations
        if locations is not None
        else [
            {
                "source": {"display_name": "A Repository"},
                "landing_page_url": "https://repo/x",
                "pdf_url": "https://repo/x.pdf",
                "is_oa": True,
                "version": "acceptedVersion",
            }
        ],
    }


def article(pmid: str, *, doi: str | None = None, pmc: str | None = None) -> str:
    ids = "".join(
        f'<ArticleId IdType="{kind}">{value}</ArticleId>'
        for kind, value in (("pubmed", pmid), ("doi", doi), ("pmc", pmc))
        if value
    )
    return (
        f'<PubmedArticle><MedlineCitation><PMID Version="1">{pmid}</PMID><Article>'
        "<Journal><JournalIssue><PubDate><Year>2024</Year><Month>Jun</Month></PubDate>"
        "</JournalIssue><Title>The Journal</Title></Journal>"
        "<ArticleTitle>Effects of H<sub>2</sub>O</ArticleTitle>"
        '<Abstract><AbstractText Label="Background">Why *it* matters.</AbstractText></Abstract>'
        "<AuthorList><Author><LastName>Smith</LastName><ForeName>Jo</ForeName>"
        "<AffiliationInfo><Affiliation>A University</Affiliation></AffiliationInfo></Author>"
        "</AuthorList><GrantList><Grant><GrantID>R01</GrantID><Agency>NIH</Agency>"
        "<Country>United States</Country></Grant></GrantList>"
        "<PublicationTypeList><PublicationType>Journal Article</PublicationType>"
        "</PublicationTypeList></Article></MedlineCitation>"
        f"<PubmedData><ArticleIdList>{ids}</ArticleIdList></PubmedData></PubmedArticle>"
    )


def article_set(*children: str) -> str:
    return f"{DOCTYPE}<PubmedArticleSet>{''.join(children)}</PubmedArticleSet>"


BOOK = (
    '<PubmedBookArticle><BookDocument><PMID Version="1">123</PMID></BookDocument>'
    "</PubmedBookArticle>"
)


def epmc_hit(
    ppr: str = "PPR1325930", *, doi: str | None = "10.21203/rs.3.rs-1/v1"
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": ppr,
        "source": "PPR",
        "title": "The preprint",
        "authorList": {
            "author": [{"fullName": "Thapa S", "firstName": "Shabnam", "lastName": "Thapa"}]
        },
        "abstractText": "<title>Abstract</title><p>W&W <bold>works</bold>.</p>",
        "bookOrReportDetails": {"publisher": "Research Square"},
        "firstPublicationDate": "2026-09-23",
        "pubTypeList": {"pubType": ["Preprint"]},
        "inEPMC": "Y",
        "isOpenAccess": "Y",
        "license": "cc by",
    }
    if doi:
        record["doi"] = doi
    return {"hitCount": 1, "resultList": {"result": [record]}}


EMPTY_EPMC = {"hitCount": 0, "resultList": {"result": []}}
PREPRINT = "10.1101/2020.03.22.002386"
MERGED_WORK = work(
    doi=PREPRINT,
    pmid="32511329",
    pmcid="7239059",
    locations=[
        {"pdf_url": "https://biorxiv/x.pdf", "version": "submittedVersion", "is_oa": True},
        {"pdf_url": "https://nature/x.pdf", "version": "publishedVersion", "is_oa": True},
    ],
)
PUBLISHED = article_set(article("32511329", doi="10.1038/S41586-020-2286-9", pmc="PMC7239059"))


def cases() -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    """``(name, the caller's arguments, the answers in request order)``."""
    found = article_set(article("123", doi="10.1/X"))
    return [
        ("found-everywhere", {"pmid": "123"}, [ok(work(pmid="123", pmcid="7745181")), ok(found)]),
        (
            "api-keys-are-sent",
            {"pmid": "123", "ncbi_api_key": "nk", "openalex_api_key": "ok"},
            [ok(work(pmid="123")), ok(found)],
        ),
        ("pubmed-only", {"pmid": "123"}, [status(404), ok(found)]),
        ("openalex-only", {"doi": "10.1/x"}, [ok(work())]),
        ("openalex-supplies-the-pmid", {"doi": "10.1/X"}, [ok(work(pmid="123")), ok(found)]),
        (
            "openalex-pmid-with-leading-zeros",
            {"doi": "10.1/x"},
            [ok(work(pmid="000123")), ok(found)],
        ),
        ("openalex-pmid-not-digits", {"doi": "10.1/x"}, [ok(work(pmid="12a"))]),
        (
            "pmc-id-asks-openalex-by-pmcid",
            {"pmc_id": "pmc7745181"},
            [ok(work(pmid="123", pmcid="7745181")), ok(found)],
        ),
        ("neither", {"pmid": "123"}, [status(404), ok(article_set())]),
        ("openalex-503", {"pmid": "123"}, [status(503), ok(found)]),
        ("openalex-transport", {"pmid": "123"}, [TRANSPORT, ok(found)]),
        ("openalex-not-json", {"doi": "10.1/x"}, [ok("<html>")]),
        ("openalex-a-list", {"doi": "10.1/x"}, [ok([])]),
        ("openalex-other-record", {"doi": "10.1/x"}, [ok(work(doi="10.1/other", pmid="9"))]),
        ("openalex-other-pmid", {"pmid": "123"}, [ok(work(pmid="124")), ok(found)]),
        ("pubmed-503", {"pmid": "123"}, [ok(work(pmid="123")), status(503)]),
        ("pubmed-transport", {"pmid": "123"}, [ok(work(pmid="123")), TRANSPORT]),
        ("pubmed-not-xml", {"pmid": "123"}, [ok(work(pmid="123")), ok("not xml")]),
        (
            "pubmed-wrong-root",
            {"pmid": "123"},
            [ok(work(pmid="123")), ok("<eFetchResult><ERROR>x</ERROR></eFetchResult>")],
        ),
        (
            "pubmed-other-pmid",
            {"pmid": "123"},
            [ok(work(pmid="123")), ok(article_set(article("999")))],
        ),
        ("pubmed-book", {"pmid": "123"}, [ok(work(pmid="123")), ok(article_set(BOOK))]),
        (
            "pubmed-match-beside-others",
            {"pmid": "123"},
            [status(404), ok(article_set(article("999"), article("123")))],
        ),
        (
            "ppr-found",
            {"ppr_id": "ppr1325930"},
            [ok(epmc_hit()), ok(work(doi="10.21203/rs.3.rs-1/v1"))],
        ),
        ("ppr-without-a-doi", {"ppr_id": "PPR1325930"}, [ok(epmc_hit(doi=None))]),
        ("ppr-not-found", {"ppr_id": "PPR1325930"}, [ok(EMPTY_EPMC)]),
        ("ppr-unreadable", {"ppr_id": "PPR1325930"}, [ok({"resultList": None})]),
        ("ppr-other-record", {"ppr_id": "PPR1325930"}, [ok(epmc_hit("PPR9"))]),
        ("ppr-503", {"ppr_id": "PPR1325930"}, [status(503)]),
        ("ppr-transport", {"ppr_id": "PPR1325930"}, [TRANSPORT]),
        (
            "ppr-beside-a-pmid",
            {"pmid": "123", "ppr_id": "PPR5"},
            [ok(EMPTY_EPMC), status(404), ok(found)],
        ),
        ("merged-preprint", {"doi": PREPRINT}, [ok(MERGED_WORK), ok(PUBLISHED)]),
        ("merged-preprint-pubmed-down", {"doi": PREPRINT}, [ok(MERGED_WORK), status(503)]),
        ("merged-preprint-pubmed-empty", {"doi": PREPRINT}, [ok(MERGED_WORK), ok(article_set())]),
        (
            "ppr-merged-pubmed-down",
            {"ppr_id": "PPR1325930"},
            [
                ok(epmc_hit()),
                ok(work(doi="10.21203/rs.3.rs-1/v1", pmid="77", pmcid="88")),
                status(503),
            ],
        ),
        (
            "ppr-with-a-published-version",
            {"ppr_id": "PPR1325930"},
            [
                ok(epmc_hit()),
                ok(work(doi="10.21203/rs.3.rs-1/v1", pmid="77")),
                ok(article_set(article("77", doi="10.1/published"))),
            ],
        ),
        ("same-doi-in-another-case", {"doi": "10.1/x"}, [ok(work(pmid="123")), ok(found)]),
        (
            "pubmed-record-without-a-doi",
            {"doi": "10.1/x"},
            [ok(work(pmid="123")), ok(article_set(article("123")))],
        ),
        (
            "caller-doi-fills-an-empty-field",
            {"pmid": "123", "doi": "10.1/Given"},
            [status(404), ok(article_set(article("123")))],
        ),
        ("caller-doi-disagrees", {"pmid": "123", "doi": "10.1/caller"}, [status(404), ok(found)]),
        ("doi-with-reserved-characters", {"doi": "https://doi.org/10.1/a?b#c d"}, [status(404)]),
        ("doi-non-ascii", {"doi": "10.1/é"}, [status(404)]),
        ("invalid-pmid", {"pmid": "12a"}, []),
        ("invalid-pmid-all-zeros", {"pmid": "000"}, []),
        ("invalid-pmid-non-ascii", {"pmid": "١٢٣"}, []),
        ("invalid-pmc-id", {"pmc_id": "PMC١٢٣"}, []),
        ("invalid-ppr-id", {"ppr_id": "PR12"}, []),
        ("no-identifier", {"pmid": "  ", "doi": "", "pmc_id": " "}, []),
    ]


class _Replay:
    """Serves a case's answers in order and records each request."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self.requests.append({"url": url, "params": dict(params or {})})
        answer = self.answers.pop(0)
        if "raises" in answer:
            raise httpx.ConnectError("scripted transport failure")
        return httpx.Response(answer["status"], text=answer["body"])


def _without_messages(result: dict[str, Any]) -> dict[str, Any]:
    for source in ("pubmed", "openalex", "europepmc"):
        result[source] = {"outcome": result[source]["outcome"]}
    return result


def run_case(arguments: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    """The expectation for one case."""
    client = _Replay(answers)
    try:
        result = lookup_record(client, email=EMAIL, **arguments)
    except ValueError as exc:
        argument = str(exc).split(" ", 1)[0]
        error = (
            {"error": "no_identifier"}
            if argument == "lookup_record"
            else {
                "error": "invalid",
                "argument": argument,
            }
        )
        return {"requests": client.requests, **error}
    if client.answers:
        raise AssertionError(f"{len(client.answers)} scripted answers were never asked for")
    return {"requests": client.requests, "result": _without_messages(result.to_dict())}


def build_contract() -> dict[str, Any]:
    """The whole fixture."""
    return {
        "email": EMAIL,
        "cases": [
            {
                "name": name,
                "input": arguments,
                "responses": answers,
                "expected": run_case(arguments, answers),
            }
            for name, arguments, answers in cases()
        ],
    }


def main() -> None:
    """Write the fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    text = json.dumps(build_contract(), ensure_ascii=False, indent=1) + "\n"
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
