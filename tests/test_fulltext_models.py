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
    JATSArticle,
    JATSAuthorInfo,
    JATSBodySection,
    JATSReferenceInfo,
)


class TestJATSAuthorInfo:
    def test_full_name(self):
        author = JATSAuthorInfo(surname="Smith", given_names="John A")
        assert author.full_name == "John A Smith"

    def test_full_name_no_given(self):
        author = JATSAuthorInfo(surname="Consortium")
        assert author.full_name == "Consortium"


class TestAnUndividedContributorName:
    """What the model holds when the deposit gives one undivided string.

    JATS names a contributor with ``(name | string-name | collab | ...)``.
    Only the first of those divides into a surname and given names; a
    ``<collab>`` names a group and a ``<string-name>`` a person the depositor
    did not split. Issues #120 and #140 are the two spellings, and they share
    one decision: the undivided form is held verbatim, in a field that says
    which kind it is, so a consumer sorting or de-duplicating by ``surname``
    can tell "the INHERIT Trial Group" from a person.
    """

    def test_a_collaboration_renders_as_its_own_name(self):
        author = JATSAuthorInfo(collab="the INHERIT Trial Group")

        assert author.full_name == "the INHERIT Trial Group"

    def test_an_undivided_personal_name_renders_verbatim(self):
        author = JATSAuthorInfo(string_name="Jane Q Smith")

        assert author.full_name == "Jane Q Smith"

    def test_a_structured_name_wins_over_a_collaboration(self):
        """ "Smith, on behalf of the Y Group" is Smith's paper.

        A ``<contrib>`` may carry both, and the person is the contributor —
        the collaboration is an attribution attached to them. Both are kept;
        only the rendering has to choose.
        """
        author = JATSAuthorInfo(
            surname="Smith", given_names="Jane", collab="on behalf of the Y Group"
        )

        assert author.full_name == "Jane Smith"
        assert author.collab == "on behalf of the Y Group"

    def test_a_structured_name_wins_over_an_undivided_one(self):
        author = JATSAuthorInfo(surname="Smith", given_names="Jane", string_name="Smith, Jane Q")

        assert author.full_name == "Jane Smith"

    def test_a_collaboration_carries_no_surname(self):
        """The point of the separate field, stated as an assertion.

        Overloading ``surname`` would render identically and silently mix
        organisations into a key that is sorted and de-duplicated on.
        """
        author = JATSAuthorInfo(collab="the INHERIT Trial Group")

        assert author.surname == ""
        assert author.given_names == ""

    def test_the_undivided_forms_default_to_empty(self):
        author = JATSAuthorInfo(surname="Smith")

        assert author.collab == ""
        assert author.string_name == ""


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
