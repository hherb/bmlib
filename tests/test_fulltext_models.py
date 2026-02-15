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

"""Tests for bmlib.fulltext.models."""

from bmlib.fulltext.models import (
    FullTextResult,
    JATSAbstractSection,
    JATSArticle,
    JATSAuthorInfo,
    JATSBodySection,
    JATSFigureInfo,
    JATSReferenceInfo,
    JATSTableInfo,
)


class TestJATSAuthorInfo:
    def test_full_name(self):
        author = JATSAuthorInfo(surname="Smith", given_names="John A")
        assert author.full_name == "John A Smith"

    def test_full_name_no_given(self):
        author = JATSAuthorInfo(surname="Consortium")
        assert author.full_name == "Consortium"


class TestJATSReferenceInfo:
    def test_formatted_citation_structured(self):
        ref = JATSReferenceInfo(
            id="r1",
            label="1",
            citation="",
            authors=["Smith J", "Doe A"],
            article_title="A study",
            source="Nature",
            year="2024",
            volume="580",
            issue="3",
            first_page="123",
            last_page="130",
            doi="10.1038/example",
            pmid="12345678",
        )
        result = ref.formatted_citation
        assert "Smith J, Doe A" in result
        assert "A study" in result
        assert "Nature" in result
        assert "(2024)" in result
        assert "580(3):123-130" in result
        assert "doi:10.1038/example" in result

    def test_formatted_citation_fallback(self):
        ref = JATSReferenceInfo(
            id="r1",
            label="1",
            citation="Raw citation text.",
            authors=[],
            article_title="",
            source="",
            year="",
            volume="",
            issue="",
            first_page="",
            last_page="",
            doi="",
            pmid="",
        )
        assert ref.formatted_citation == "Raw citation text."

    def test_formatted_citation_et_al(self):
        ref = JATSReferenceInfo(
            id="r1",
            label="1",
            citation="",
            authors=["A", "B", "C", "D"],
            article_title="Title",
            source="J",
            year="2024",
            volume="",
            issue="",
            first_page="",
            last_page="",
            doi="",
            pmid="",
        )
        result = ref.formatted_citation
        assert "et al." in result


class TestFullTextResult:
    def test_europepmc(self):
        r = FullTextResult(source="europepmc", html="<p>content</p>")
        assert r.source == "europepmc"
        assert r.html == "<p>content</p>"
        assert r.pdf_url is None

    def test_unpaywall(self):
        r = FullTextResult(source="unpaywall", pdf_url="https://example.com/paper.pdf")
        assert r.pdf_url == "https://example.com/paper.pdf"

    def test_doi(self):
        r = FullTextResult(source="doi", web_url="https://doi.org/10.1234/test")
        assert r.web_url == "https://doi.org/10.1234/test"


class TestJATSBodySection:
    def test_nested(self):
        child = JATSBodySection(title="Methods", paragraphs=["We did X."])
        parent = JATSBodySection(title="Main", paragraphs=[], subsections=[child])
        assert parent.subsections[0].title == "Methods"


class TestJATSArticle:
    def test_construction(self):
        article = JATSArticle(
            title="Test",
            authors=[],
            journal="Nature",
            volume="1",
            issue="2",
            pages="3-4",
            year="2024",
            doi="10.1/t",
            pmc_id="PMC123",
            pmid="456",
            abstract_sections=[],
            body_sections=[],
            figures=[],
            tables=[],
            references=[],
        )
        assert article.title == "Test"
