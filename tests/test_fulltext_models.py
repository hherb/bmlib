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

import dataclasses
from html import escape as html_escape

import pytest

from bmlib.fulltext.jats_parser import _format_ref_html
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

    def test_given_names_alone_render_as_the_full_name(self):
        """A mononym is a structured name with no ``<surname>``.

        Tested on ``surname`` alone, the branch falls through to the undivided
        forms and a contributor carrying only given names renders empty — and
        with neither undivided field set, :attr:`is_named` then calls a named
        contributor unnamed and the parser drops them.
        """
        author = JATSAuthorInfo(given_names="Prince")

        assert author.full_name == "Prince"
        assert author.is_named

    def test_a_contributor_carrying_no_spelling_at_all_is_not_named(self):
        """``<anonymous/>``, or a ``<contrib>`` carrying only an ``<xref>``.

        Well-formed JATS, so this is the document's answer rather than an
        error — which is why the predicate is a question on the type and not a
        raising ``__post_init__``. The parser is built inside a SAX callback,
        where a raise escapes into ``FullTextService``'s tier-level handler and
        costs the whole article (issue #129).
        """
        assert not JATSAuthorInfo().is_named

    def test_a_name_that_is_only_whitespace_is_not_a_name(self):
        """Reachable from a caller, though not from the parser, which strips.

        ``bmlib.citations`` already settled that a blank string is no author;
        reading the predicate through ``full_name`` keeps the two agreeing.
        """
        assert not JATSAuthorInfo(collab="   ").is_named

    def test_a_collaboration_wins_over_an_undivided_personal_name(self):
        """The documented order between the two undivided forms.

        **Arbitrary, and pinned as arbitrary.** No deposit carrying both has
        been measured, and the principle that settles the structured case does
        not reach this one — a ``string_name`` *is* a person, so "the person is
        the contributor" would argue the other way. Fixed only so the rule is
        deterministic, and pinned so the code keeps applying whichever rule the
        docstring states.
        """
        author = JATSAuthorInfo(collab="The CONSORT Group", string_name="Jane Q Smith")

        assert author.full_name == "The CONSORT Group"

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

    def test_an_elocation_id_is_the_locator_where_there_is_no_page_range(self):
        """Issue #265: a reference paginated electronically printed no locator."""
        ref = JATSReferenceInfo(
            id="r1",
            label="1",
            citation="",
            source="PLoS One",
            year="2020",
            volume="15",
            issue="3",
            elocation_id="e0230000",
        )
        assert ref.formatted_citation == "PLoS One. (2020). 15(3):e0230000"

    def test_an_elocation_id_without_a_volume_is_printed_bare(self):
        ref = JATSReferenceInfo(id="r1", label="1", citation="", source="J", elocation_id="e7")
        assert ref.formatted_citation == "J. e7"

    @pytest.mark.parametrize(
        ("citation", "expected"),
        [("Population of England and Wales, Accessed 2021.", None), ("", "e7")],
        ids=["deposited-string-wins", "nothing-else-to-print"],
    )
    def test_a_lone_elocation_id_defers_to_the_deposited_citation(self, citation, expected):
        """A locator alone is not enough to abandon ``citation`` (issue #265).

        Where the ``<elocation-id>`` is the only structured component, the
        deposited string is printed if there is one; an ``<element-citation>``
        leaves ``citation`` empty, and then the locator is all there is.
        """
        ref = JATSReferenceInfo(id="r1", label="1", citation=citation, elocation_id="e7")
        assert ref.formatted_citation == (citation if expected is None else expected)

    @pytest.mark.parametrize(
        ("component", "printed", "rendered"),
        [
            ({"authors": ["Smith J"]}, "Smith J. e7", "Smith J. e7"),
            ({"article_title": "A study"}, "A study. e7", "A study. e7"),
            ({"source": "J"}, "J. e7", "<em>J</em>. e7"),
            ({"year": "2020"}, "(2020). e7", "(2020). e7"),
            ({"volume": "15"}, "15:e7", "15:e7"),
            ({"first_page": "5"}, "5", "5"),
            (
                {"doi": "10.1/x"},
                "e7. doi:10.1/x",
                'e7. <a href="https://doi.org/10.1/x">doi:10.1/x</a>',
            ),
            # Populated, and printed by neither renderer on its own — an issue
            # only after a volume, a last page only after a first, a PMID never
            # — so the locator is still alone. Listing ``issue`` in the rule
            # printed ``e7`` here (PR #269's review).
            ({"issue": "3"}, None, None),
            ({"last_page": "9"}, None, None),
            ({"pmid": "12345678"}, None, None),
        ],
        ids=[
            "authors",
            "article_title",
            "source",
            "year",
            "volume",
            "first_page",
            "doi",
            "issue",
            "last_page",
            "pmid",
        ],
    )
    def test_a_lone_locator_is_judged_by_what_the_renderers_print(
        self, component, printed, rendered
    ):
        """The deposited string wins only where the locator is all that would print.

        Exact values from both renderers, so a component printing the locator
        alone — or nothing — cannot pass as "not the deposited string".
        """
        ref = JATSReferenceInfo(
            id="r1", label="1", citation="The deposited string.", elocation_id="e7", **component
        )

        assert ref.formatted_citation == (printed or "The deposited string.")
        assert _format_ref_html(ref) == (rendered or "The deposited string.")

    def test_an_element_citations_lone_locator_is_rendered_in_both(self):
        """With no ``citation`` to defer to, both renderers print the locator.

        ``_format_ref_html``'s fallback is gated on there being a ``citation``
        as the model's is; dropping that half rendered an empty list item
        while ``formatted_citation`` printed the locator (PR #269's review).
        """
        ref = JATSReferenceInfo(id="r1", label="1", citation="", elocation_id="e7")

        assert (ref.formatted_citation, _format_ref_html(ref)) == ("e7", "e7")

    @pytest.mark.parametrize("model", [JATSReferenceInfo, JATSArticle])
    def test_elocation_id_is_declared_last(self, model):
        """So a positional construction written before issue #265 fills what it filled.

        Both neighbours are strings, so a field moved up would take a
        positional caller's ``doi`` or ``pmid`` with nothing raised.
        """
        assert [f.name for f in dataclasses.fields(model)][-1] == "elocation_id"

    def test_a_page_range_is_printed_ahead_of_an_elocation_id(self):
        """Where a citation deposits both, the rendering stays what it was.

        Neither element is reliably the locator there: over 92 served and 340
        archive such references the ``<elocation-id>`` is the ``<fpage>``'s own
        value (33 / 41), a DOI or PII by a regex that misses some (43 / 112,
        this fixture's shape), or one of many other shapes — item ids, issue
        numbers, supplement suffixes, split locators. Printing the range keeps
        every one of them rendered as it was before the field.
        """
        ref = JATSReferenceInfo(
            id="r1",
            label="1",
            citation="",
            source="Accid Anal Prev",
            volume="109",
            first_page="123",
            last_page="31",
            elocation_id="S0001-4575(17)30300-X",
        )
        assert ref.formatted_citation == "Accid Anal Prev. 109:123-31"


#: A reference's fields that are not structured components a renderer prints:
#: its identity, and the two the lone-locator rule is about.
_REFERENCE_NON_COMPONENTS = frozenset({"id", "label", "citation", "elocation_id"})

_REFERENCE_COMPONENTS = [
    f.name for f in dataclasses.fields(JATSReferenceInfo) if f.name not in _REFERENCE_NON_COMPONENTS
]


def _reference_carrying_only(name: str, **fields: str) -> JATSReferenceInfo:
    """A reference whose one component is ``name``, set to a sample value."""
    declared = next(f for f in dataclasses.fields(JATSReferenceInfo) if f.name == name)
    sample: object = ["Smith J"] if declared.default_factory is list else "x"
    return JATSReferenceInfo(**{"id": "r1", "label": "1", name: sample, **fields})


class TestTheLoneLocatorRuleIsWhatTheRenderersPrint:
    """``_carries_only_an_elocation_id`` is a list of fields, and a list drifts.

    Its first cut listed ``issue``, which neither renderer prints without a
    volume, so a reference tagging an issue and a locator printed the bare
    locator in place of its deposited citation — the loss the rule exists to
    prevent (PR #269's review). This holds the list to behaviour instead: a
    lone locator defers to ``citation`` exactly where, with the locator cleared,
    a renderer would print nothing. Every field of the dataclass is walked, so
    a field added later has to agree too.
    """

    @pytest.mark.parametrize("name", _REFERENCE_COMPONENTS)
    def test_the_rule_agrees_with_both_renderers(self, name):
        without_a_locator = _reference_carrying_only(name, citation="")
        prints_on_its_own = bool(without_a_locator.formatted_citation)
        assert bool(_format_ref_html(without_a_locator)) == prints_on_its_own

        ref = _reference_carrying_only(name, citation="Deposited <string>.", elocation_id="e7")
        structured = dataclasses.replace(ref, citation="")

        assert ref._carries_only_an_elocation_id is not prints_on_its_own
        if prints_on_its_own:
            assert ref.formatted_citation == structured.formatted_citation
            assert _format_ref_html(ref) == _format_ref_html(structured)
        else:
            assert ref.formatted_citation == "Deposited <string>."
            assert _format_ref_html(ref) == html_escape("Deposited <string>.")

    def test_the_walk_sees_both_kinds_of_field(self):
        """Anti-vacuity: a walk finding only one kind would pin half the rule."""
        printing = {
            name
            for name in _REFERENCE_COMPONENTS
            if _reference_carrying_only(name, citation="").formatted_citation
        }

        assert {"authors", "article_title", "source", "year", "volume", "first_page", "doi"} <= (
            printing
        )
        assert {"issue", "last_page", "pmid"} <= set(_REFERENCE_COMPONENTS) - printing


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
        # Declared last with a default, so a construction written before
        # issue #265 added it still works and reports no locator.
        assert article.elocation_id == ""
