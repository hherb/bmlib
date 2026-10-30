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

"""Europe PMC's search record as a FetchedRecord (StudyAnalyzer item 5)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.fetchers.europepmc import normalize_europepmc

# A real `resultType=core` hit for PPR1325930, fetched 2026-10-30, trimmed to the
# fields the normaliser reads and its abstract cut mid-paragraph.
RECORD: dict[str, Any] = json.loads(
    (Path(__file__).parent / "data" / "europepmc_ppr_record.json").read_text(encoding="utf-8")
)


def _with(**changes: Any) -> dict[str, Any]:
    raw = copy.deepcopy(RECORD)
    raw.update(changes)
    return raw


class TestTheMeasuredRecord:
    def test_every_field(self):
        record = normalize_europepmc(RECORD)

        assert record.source == "europepmc"
        assert record.title.startswith("Economic evidence for a watch-and-wait strategy")
        assert record.doi == "10.21203/rs.3.rs-11130366/v1"
        assert record.pmid is None
        assert record.pmc_id is None
        assert record.authors == [
            "Thapa, Shabnam",
            "Payne, Kathrine",
            "Praveen",
            "Malcomson, Lee",
            "Renehan, Andrew",
        ]
        assert record.journal == "Research Square"
        assert record.publication_date == "2026-09-23"
        assert record.publication_types == ["Preprint"]
        assert record.is_open_access is True
        assert record.license == "cc by"
        assert record.extras == {"ppr_id": "PPR1325930"}

    def test_the_abstract_keeps_its_raw_ampersand(self):
        # The measured body carries `(W&W)` unescaped, so it is not XML: parsed
        # as XML, every such abstract would be lost.
        abstract = normalize_europepmc(RECORD).abstract
        assert abstract is not None
        assert abstract.startswith(
            "Abstract\n\nAim: Several studies report that watch-and-wait (W&W)"
        )

    def test_a_record_europe_pmc_holds_gets_its_full_text_address(self):
        assert normalize_europepmc(RECORD).fulltext_sources == [
            FullTextSourceEntry(
                url="https://www.ebi.ac.uk/europepmc/webservices/rest/PPR1325930/fullTextXML",
                format="xml",
                source="europepmc",
                open_access=True,
            )
        ]

    def test_a_record_europe_pmc_does_not_hold_gets_none(self):
        assert normalize_europepmc(_with(inEPMC="N")).fulltext_sources == []

    def test_a_pmcid_is_the_address_when_there_is_one(self):
        record = normalize_europepmc(_with(pmcid="PMC7745181"))
        assert record.pmc_id == "PMC7745181"
        assert [e.url for e in record.fulltext_sources] == [
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC7745181/fullTextXML"
        ]

    def test_a_med_record_is_not_addressed_by_its_pmid(self):
        # fullTextXML addresses a PMC or PPR accession only (#188).
        record = normalize_europepmc(_with(id="33301246", source="MED", pmid="33301246"))
        assert record.pmid == "33301246"
        assert record.fulltext_sources == []
        assert record.extras == {}

    def test_a_journal_title_is_preferred_to_the_server(self):
        raw = _with(journalInfo={"journal": {"title": "The Lancet"}})
        assert normalize_europepmc(raw).journal == "The Lancet"


class TestTheAbstract:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "<title>Abstract</title>  <p>  <bold>Aim:</bold> Several *studies*</p>",
                "Abstract\n\nAim: Several \\*studies\\*",
            ),
            ("<p>a < b</p>", "a < b"),
            ("<p>x &lt; y &amp; z &#65;&#x42;</p>", "x < y & z AB"),
            ("&nbsp;kept", "&nbsp;kept"),
            ("&#xD800;&#1114112;&#99999999999;", "&#xD800;&#1114112;&#99999999999;"),
            ('<p class="x">one</p><p>two\n  lines</p>', "one\n\ntwo lines"),
            ("plain text, no markup", "plain text, no markup"),
            ("<p>a\x1cb\u00a0c</p>", "a\x1cb\u00a0c"),
            ("<p> </p>", None),
            ("", None),
            ("<p>  </p><title></title>", None),
        ],
        ids=[
            "measured",
            "lone-lt",
            "entities",
            "unknown-entity",
            "no-such-character",
            "attributes",
            "plain",
            "not-ascii-space",
            "blank",
            "empty",
            "empty-blocks",
        ],
    )
    def test_the_abstract(self, text, expected):
        assert normalize_europepmc(_with(abstractText=text)).abstract == expected


class TestAWrongTypedFieldCostsThatField:
    @pytest.mark.parametrize(
        ("key", "value", "field", "empty"),
        [
            ("title", 5, "title", ""),
            ("doi", 5, "doi", None),
            ("authorList", "x", "authors", []),
            ("abstractText", 5, "abstract", None),
            ("pubTypeList", ["x"], "publication_types", []),
            ("bookOrReportDetails", "x", "journal", None),
            ("isOpenAccess", True, "is_open_access", False),
            ("license", 5, "license", None),
            ("firstPublicationDate", 5, "publication_date", None),
            ("id", 5, "extras", {}),
        ],
    )
    def test_a_wrong_typed_field_costs_that_field(self, key, value, field, empty):
        record = normalize_europepmc(_with(**{key: value}))
        assert getattr(record, field) == empty
        if field != "title":
            assert record.title == normalize_europepmc(RECORD).title

    def test_a_wrong_typed_author_is_skipped(self):
        raw = _with(authorList={"author": ["x", {"lastName": 5, "fullName": "Doe J"}]})
        assert normalize_europepmc(raw).authors == ["Doe J"]
