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

import ast
import dataclasses
import pathlib
from html import escape as html_escape
from typing import TypeGuard

import pytest

import bmlib
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
            # Two populated fields, one printed run: a volume prefixes the
            # locator and a first page *replaces* it, both inside
            # ``_volume_info`` rather than beside it, so ``15:e7`` and ``5``
            # are each one component and the deposit wins.
            # Both printed the run until issue #268, which is why these two
            # rows are reversed and not removed — the decision they pinned
            # changed, and a locator with no work attached is what it refuses.
            ({"volume": "15"}, None, None),
            ({"first_page": "5"}, None, None),
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
        """The deposited string wins wherever one run is all that would print.

        Exact values from both renderers, so a component printing the locator
        alone — or nothing — cannot pass as "not the deposited string". Not
        phrased as "the locator is still the only run": for the ``first_page``
        row the page range *replaces* the locator, so the one run printed is
        not the locator at all, and the rule still holds.
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

    @pytest.mark.parametrize(
        ("model", "tail"),
        [
            (JATSReferenceInfo, ["elocation_id"]),
            # Issue #257 declared `funding_statements` after it, for the same reason.
            (JATSArticle, ["elocation_id", "funding_statements"]),
        ],
    )
    def test_later_fields_are_declared_last(self, model, tail):
        """So a positional construction written before issue #265 fills what it filled.

        Both neighbours are strings, so a field moved up would take a
        positional caller's ``doi`` or ``pmid`` with nothing raised. A field
        added after ``elocation_id`` goes after it, in the order it was added.
        """
        assert [f.name for f in dataclasses.fields(model)][-len(tail) :] == tail

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
#: its identity, and the deposited string the rule falls back to. Since #268
#: the locator is walked with the rest — it is one of the six runs a renderer
#: prints, not a case of its own.
_REFERENCE_NON_COMPONENTS = frozenset({"id", "label", "citation"})

_REFERENCE_COMPONENTS = [
    f.name for f in dataclasses.fields(JATSReferenceInfo) if f.name not in _REFERENCE_NON_COMPONENTS
]


def _is_deferral_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """Is ``node`` a call to ``_defers_to_the_deposit`` on anything?"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_defers_to_the_deposit"
    )


def _own_scope(func: ast.AST) -> list[ast.AST]:
    """Every node inside ``func`` that is not inside a scope of its own.

    A nested ``def``, ``lambda`` or ``class`` is *not* walked, so its appends
    and joins are not its enclosing function's. Descending let a renderer
    judge the rule on a list only a nested helper built and returned — which
    would raise ``NameError`` at runtime and passed this walk (measured in PR
    #277's review). A nested ``def`` is still reached in its own right, since
    ``ast.walk`` visits it, so every call is attributed to exactly one
    function: the innermost that encloses it.
    """
    scoped: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        scoped.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return scoped


def _deferral_call_sites(source: str, where: str) -> dict[str, str]:
    """Every ``_defers_to_the_deposit`` call in ``source``, and the list it counts.

    Raises ``AssertionError`` where a call does not pass ``len(x)`` for a list
    ``x`` that its own function both **builds** by appending and **joins into
    what it returns** — which is the whole of the rule the two renderers
    share. Testing "appended to" alone would let a function that builds two
    lists count the wrong one, and testing "joined anywhere" would let one
    join the right list for a log line and return something else; both are
    likelier drifts than a wholly new rule.

    **It fails closed on a call it cannot attribute**, which is the half that
    matters and the half a set of found sites cannot supply on its own: a call
    at module scope, in a ``lambda`` or in a class body outside a method
    reaches no function, contributes no entry, and so would leave a caller's
    equality assertion green while a third renderer judged the rule on
    anything at all (measured in PR #277's review — an ``async def`` renderer
    passing ``len(ref.authors)`` was invisible). Every call node in the tree
    is counted first and the walk must account for all of them, and
    ``async def`` is walked beside ``def`` as the two sibling nets in
    ``test_transparency.py`` and ``test_api_failure_sampler.py`` already do.
    A key is ``module.function``, so two same-named functions — or one
    function calling twice — cannot collapse into a single entry silently.
    """
    tree = ast.parse(source)
    every_call = [node for node in ast.walk(tree) if _is_deferral_call(node)]
    found: dict[str, str] = {}
    attributed: set[int] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        scoped = _own_scope(func)
        appended = {
            node.func.value.id
            for node in scoped
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
        }
        returned = {
            call.args[0].id
            for statement in scoped
            if isinstance(statement, ast.Return)
            for call in ast.walk(statement)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "join"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Name)
        }
        for node in scoped:
            if not _is_deferral_call(node):
                continue
            key = f"{where}.{func.name}"
            attributed.add(id(node))
            arguments = list(node.args) + [keyword.value for keyword in node.keywords]
            assert len(arguments) == 1, f"{key} does not pass exactly one count"
            argument = arguments[0]
            assert isinstance(argument, ast.Call), f"{key} does not pass a call"
            assert isinstance(argument.func, ast.Name) and argument.func.id == "len", (
                f"{key} does not count anything"
            )
            assert len(argument.args) == 1, f"{key} does not count exactly one list"
            counted = argument.args[0]
            assert isinstance(counted, ast.Name), f"{key} counts an expression, not a local list"
            assert counted.id in appended, f"{key} counts a list it does not build"
            assert counted.id in returned, f"{key} counts a list it does not render"
            assert key not in found, f"{key} is two call sites under one name"
            found[key] = counted.id
    unattributed = len(every_call) - len(attributed)
    assert not unattributed, (
        f"{where}: {unattributed} of {len(every_call)} call sites reached no function scope"
    )
    return found


def _reference_carrying_only(name: str, **fields: str) -> JATSReferenceInfo:
    """A reference whose one component is ``name``, set to a sample value."""
    declared = next(f for f in dataclasses.fields(JATSReferenceInfo) if f.name == name)
    sample: object = ["Smith J"] if declared.default_factory is list else "x"
    return JATSReferenceInfo(**{"id": "r1", "label": "1", name: sample, **fields})


class TestOneComponentNeverDisplacesTheDeposit:
    """The rule is a count of what a renderer built, so no list can drift from it.

    Its ancestor, ``_carries_only_an_elocation_id``, was a hand-written list of
    fields, and it drifted the day it was written: it listed ``issue``, which
    neither renderer prints without a volume, so a reference tagging an issue
    and a locator printed the bare locator in place of its deposited citation
    (PR #269's review). Issue #268 replaced the list with ``len(parts)``, which
    each renderer takes from its own parts, so "what would print" is not a
    claim about the fields any more.

    What can still drift is the *two renderers*, which build their parts
    separately. Every field of the dataclass is walked, so a field added later
    has to contribute a part to both or to neither.
    """

    @pytest.mark.parametrize("name", _REFERENCE_COMPONENTS)
    def test_both_renderers_agree_on_whether_the_field_prints(self, name):
        alone = _reference_carrying_only(name, citation="")
        prints_on_its_own = bool(alone.formatted_citation)

        assert bool(_format_ref_html(alone)) == prints_on_its_own

    @pytest.mark.parametrize("name", _REFERENCE_COMPONENTS)
    def test_the_field_alone_never_displaces_the_deposit(self, name):
        """One component is never a citation, whichever component it is."""
        lone = _reference_carrying_only(name, citation="Deposited <string>.")

        assert lone.formatted_citation == "Deposited <string>."
        assert _format_ref_html(lone) == html_escape("Deposited <string>.")

    @pytest.mark.parametrize("name", _REFERENCE_COMPONENTS)
    def test_a_second_component_is_what_earns_the_structured_rendering(self, name):
        """The rule is a count, so two components have to be enough.

        The partner is ``year`` (``source`` for ``year`` itself) rather than
        another locator field: ``volume``, ``issue``, ``first_page``,
        ``last_page`` and ``elocation_id`` all feed the one ``_volume_info``
        run, so a pair drawn from inside it is still one component.
        """
        partner = {"source": "J"} if name == "year" else {"year": "2020"}
        paired = _reference_carrying_only(name, citation="Deposited <string>.", **partner)
        prints_on_its_own = bool(_reference_carrying_only(name, citation="").formatted_citation)
        structured = dataclasses.replace(paired, citation="")

        if prints_on_its_own:
            assert paired.formatted_citation == structured.formatted_citation
            assert _format_ref_html(paired) == _format_ref_html(structured)
        else:
            # It prints nothing, so the partner is still the only component.
            assert paired.formatted_citation == "Deposited <string>."
            assert _format_ref_html(paired) == html_escape("Deposited <string>.")

    def test_the_walk_sees_both_kinds_of_field(self):
        """Anti-vacuity: a walk finding only one kind would pin half the rule."""
        printing = {
            name
            for name in _REFERENCE_COMPONENTS
            if _reference_carrying_only(name, citation="").formatted_citation
        }

        assert {
            "authors",
            "article_title",
            "source",
            "year",
            "volume",
            "first_page",
            "doi",
            "elocation_id",
        } <= printing
        assert {"issue", "last_page", "pmid"} <= set(_REFERENCE_COMPONENTS) - printing

    def test_every_call_site_passes_the_parts_it_built(self):
        """Mechanised, because the rule is only as good as its argument.

        ``_defers_to_the_deposit`` takes a count so that "what would print"
        cannot be a claim about the fields — its ancestor *was* such a claim
        and was wrong the day it was written. But a count is a claim too when
        a caller passes the wrong one, and nothing in the signature stops it:
        ``len(self.authors)`` type-checks. So every call site in the package
        is walked with ``ast`` and each must pass ``len(x)`` for a list ``x``
        that its own function both builds by appending *and* joins into what
        it returns — ``TestTheAuditNetIsComplete``'s rule, *a rule enforced by
        prose is not enforced*, two modules over.

        It holds the *set* of call sites and walks the whole package to do it,
        and the helper fails closed on a call it cannot attribute to a
        function, so a third renderer anywhere in ``bmlib`` has to be looked
        at rather than inheriting a green — including one written
        ``async def``, at module scope, in a ``lambda`` or in a class body,
        each of which was invisible here until PR #277's review measured it.
        """
        package = pathlib.Path(bmlib.__file__).parent
        found = {}
        for source in sorted(package.rglob("*.py")):
            where = str(source.relative_to(package.parent).with_suffix("")).replace("/", ".")
            found.update(_deferral_call_sites(source.read_text(encoding="utf-8"), where))

        assert found == {
            "bmlib.fulltext.models.formatted_citation": "parts",
            "bmlib.fulltext.jats_parser._format_ref_html": "parts",
        }

    @pytest.mark.parametrize(
        ("counted", "complaint"),
        [
            ("len(extras)", "counts a list it does not render"),
            ("len(ref.authors)", "counts an expression, not a local list"),
            ("2", "does not pass a call"),
            ("count(parts)", "does not count anything"),
            ("len(parts, 1)", "does not count exactly one list"),
            ("len(parts), True", "does not pass exactly one count"),
        ],
        ids=[
            "the-other-list",
            "an-attribute",
            "a-literal",
            "not-len",
            "two-lists",
            "a-second-argument",
        ],
    )
    def test_a_call_site_judging_something_else_is_reported(self, counted, complaint):
        """The teeth control: a walk that finds nothing passes.

        Six shapes, each reaching its own refusal. The first is the drift
        that matters — a renderer that builds a second list and judges the
        rule on that — and it is the one a rule keyed on "appended to
        somewhere in this function" would wave through. The last two reach
        the two arity refusals; before PR #277's review ``len(parts, 1)``
        raised ``ValueError`` from an unpack rather than naming anything.
        """
        source = (
            "def render(ref):\n"
            "    parts = []\n"
            "    parts.append(ref.year)\n"
            "    extras = []\n"
            "    extras.append(ref.doi)\n"
            f"    if ref._defers_to_the_deposit({counted}):\n"
            "        return ref.citation\n"
            "    return '. '.join(parts)\n"
        )

        with pytest.raises(AssertionError, match=complaint):
            _deferral_call_sites(source, "synthetic")

    def test_the_control_passes_when_the_call_site_is_right(self):
        """...and the same synthetic module, corrected, is accepted.

        Without this the controls above would also pass if the walker refused
        everything.
        """
        source = (
            "def render(ref):\n"
            "    parts = []\n"
            "    parts.append(ref.year)\n"
            "    extras = []\n"
            "    extras.append(ref.doi)\n"
            "    if ref._defers_to_the_deposit(len(parts)):\n"
            "        return ref.citation\n"
            "    return '. '.join(parts)\n"
        )

        assert _deferral_call_sites(source, "synthetic") == {"synthetic.render": "parts"}

    def test_the_keyword_form_of_a_right_call_is_accepted(self):
        """A correct call written with the parameter named is still correct.

        It reddened the net with *"does not pass exactly one count"* until PR
        #277's review — a message describing the opposite of what the caller
        had done, on the one shape a maintainer reaches by being explicit.
        """
        source = (
            "def render(ref):\n"
            "    parts = []\n"
            "    parts.append(ref.year)\n"
            "    if ref._defers_to_the_deposit(printed_part_count=len(parts)):\n"
            "        return ref.citation\n"
            "    return '. '.join(parts)\n"
        )

        assert _deferral_call_sites(source, "synthetic") == {"synthetic.render": "parts"}

    def test_an_async_renderer_is_walked_beside_a_plain_one(self):
        """``ast.AsyncFunctionDef`` is not an ``ast.FunctionDef``.

        So an ``async def`` renderer reached none of the five per-site
        refusals and contributed no entry, and the package-level equality
        stayed green with a blatantly wrong count inside it (measured in PR
        #277's review against the real package walk). The two sibling nets in
        ``test_transparency.py`` and ``test_api_failure_sampler.py`` already
        name both node types; this one did not.
        """
        source = (
            "async def render(ref):\n"
            "    parts = []\n"
            "    parts.append(ref.year)\n"
            "    if ref._defers_to_the_deposit(len(parts)):\n"
            "        return ref.citation\n"
            "    return '. '.join(parts)\n"
        )

        assert _deferral_call_sites(source, "synthetic") == {"synthetic.render": "parts"}

    @pytest.mark.parametrize(
        "source",
        [
            "parts = []\nparts.append(1)\nx = ref._defers_to_the_deposit(len(parts))\n",
            "render = lambda ref: ref.citation if ref._defers_to_the_deposit(0) else ''\n",
            "class R:\n    ok = property(lambda s: s._defers_to_the_deposit(2))\n",
        ],
        ids=["module-scope", "a-lambda", "a-class-body"],
    )
    def test_a_call_site_no_function_claims_is_reported(self, source):
        """The half a set of found sites cannot supply: an *absent* entry.

        A call the walk cannot attribute used to leave the set unchanged, so
        the caller's equality assertion passed and the site was never judged.
        Every call node in the tree is counted first now, and the walk has to
        account for all of them — the ``TestTheAuditNetIsComplete`` rule that
        an outcome the instrument cannot read is a finding, not a skip.
        """
        with pytest.raises(AssertionError, match="reached no function scope"):
            _deferral_call_sites(source, "synthetic")

    def test_two_call_sites_under_one_name_are_reported(self):
        """The key is ``module.function``, so two of them would collapse.

        ``models.py`` is a bag of dataclasses, so a second class with a
        ``formatted_citation`` is the plausible shape: it produced one entry,
        and the package-level equality could not tell it from one renderer
        (measured in PR #277's review).
        """
        source = (
            "class A:\n"
            "    def formatted_citation(self):\n"
            "        parts = []\n"
            "        parts.append(self.year)\n"
            "        if self._defers_to_the_deposit(len(parts)):\n"
            "            return self.citation\n"
            "        return '. '.join(parts)\n"
            "class B:\n"
            "    def formatted_citation(self):\n"
            "        parts = []\n"
            "        parts.append(self.doi)\n"
            "        if self._defers_to_the_deposit(len(parts)):\n"
            "            return self.citation\n"
            "        return '. '.join(parts)\n"
        )

        with pytest.raises(AssertionError, match="two call sites under one name"):
            _deferral_call_sites(source, "synthetic")

    def test_a_nested_helpers_list_is_not_its_callers(self):
        """``ast.walk`` descends into a nested ``def``, and that was wrong.

        A renderer judging the rule on a list only a nested helper builds
        would raise ``NameError`` at runtime, and passed this walk. Scoping
        each function to its own body refuses it — and still visits the
        nested ``def`` in its own right, so a legitimate nested renderer is
        judged rather than ignored.
        """
        source = (
            "def render(ref):\n"
            "    def inner():\n"
            "        parts = []\n"
            "        parts.append(ref.year)\n"
            "        return '. '.join(parts)\n"
            "    if ref._defers_to_the_deposit(len(parts)):\n"
            "        return ref.citation\n"
            "    return inner()\n"
        )

        with pytest.raises(AssertionError, match="counts a list it does not build"):
            _deferral_call_sites(source, "synthetic")

    def test_a_list_joined_outside_the_return_is_reported(self):
        """ "Joins into what it returns" is now checked, not just claimed.

        The docstring said it and the code accepted a ``.join`` anywhere, so
        a renderer joining ``parts`` for a log line and returning something
        else passed. ``acc85db`` was a commit about this docstring's accuracy
        and still overstated it (PR #277's review).
        """
        source = (
            "def render(ref, log):\n"
            "    parts = []\n"
            "    parts.append(ref.year)\n"
            "    log('. '.join(parts))\n"
            "    if ref._defers_to_the_deposit(len(parts)):\n"
            "        return ref.citation\n"
            "    return ref.article_title\n"
        )

        with pytest.raises(AssertionError, match="counts a list it does not render"):
            _deferral_call_sites(source, "synthetic")

    def test_the_locator_fields_are_one_component_between_them(self):
        """Anti-vacuity for the partner choice above, and the rule it rests on.

        ``volume`` and ``first_page`` are two populated fields and one printed
        run, so the deposit still wins. A rule counting *populated fields*
        rather than printed parts would render ``15:123`` here — a locator with
        no work attached, which is exactly what #265 refused for
        ``<elocation-id>`` and #268 generalised.
        """
        ref = JATSReferenceInfo(
            id="r1", label="1", citation="Deposited.", volume="15", first_page="123"
        )

        assert ref.formatted_citation == "Deposited."
        assert _format_ref_html(ref) == "Deposited."
        assert dataclasses.replace(ref, citation="").formatted_citation == "15:123"


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
