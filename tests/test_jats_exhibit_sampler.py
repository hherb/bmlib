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

"""Offline tests for ``scripts/sample_jats_exhibits.py``.

The script is a live runner — it fetches real JATS from Europe PMC — but the
populations it prints are the evidence behind the parser's exhibit rules, so
the counting has to be trustworthy offline. Four properties are pinned here.

**An article that could not be measured is never a finding.** A transport
failure, a non-200 or a document that will not parse is *unmeasured*: excluded
from every denominator, and reported as ERROR rather than as a rate once it
eats more than a fifth of the sample. A zero nesting rate is what some
populations genuinely look like, and a dead host must not read as one.

**The sampler does not share the parser's predicates.** A corpus labelled by
the rule under test can only confirm that rule, so the sampler carries its own
thumbnail and archival tests. The test below asserts they are actually
different, which is what a future "deduplication" would break.

**The sample is stratified.** A single cursor walk returns a contiguous block
of accessions; the first live run drew 120 articles of which 106 carried no
exhibit at all. The month windows are what spread it.

**Counting is checked against hand-built markup**, not against the parser.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import unquote

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sample_jats_exhibits.py"
_spec = importlib.util.spec_from_file_location("bmlib_jats_exhibit_sampler", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the sampler from {_PATH}")
sampler = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sampler
# The sampler does `from _sampling import …`, and `scripts/` is not a package.
# Running the script puts that directory on sys.path as sys.path[0]; loading it
# by path here does not, so insert it explicitly.
if str(_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_PATH.parent))
_spec.loader.exec_module(sampler)


def _section(report: str, heading: str) -> list[str]:
    """The report lines belonging to one numbered section.

    Several sections can print ``NOT MEASURED``, so an assertion made against
    the whole report cannot say which one did — and would pass for a run in
    which the section under test printed a rate it had no rows for.
    """
    lines = report.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    end = next(
        (i for i, line in enumerate(lines[start + 1 :], start + 1) if line[:1].isdigit()),
        len(lines),
    )
    return lines[start:end]


def _article(body: str) -> bytes:
    """Wrap *body* in a minimal article that actually declares XLink.

    Without the declaration the fixture is not well-formed and every row comes
    back ``None`` — which the sampler is right to treat as unmeasured, but
    which would make these tests measure nothing while appearing to pass.
    """
    return (
        f'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body>{body}</body></article>'
    ).encode()


def _package_run_args(**kwargs: Any) -> argparse.Namespace:
    """A `_validate_args` namespace for a `--package` run, defaults overridable.

    Shared by ``TestThePackageRunIsRefusedWhenItWouldMislead`` and
    ``TestTheCompareEuropepmcFlag`` — both build the same shape of namespace
    to drive the same function, and two copies of the default dict meant a
    new `_validate_args` argument had to be added in both places, with the
    missed copy failing every test that omitted it with `AttributeError`
    rather than with a meaningful assertion.
    """
    defaults = dict(
        target=10,
        months=24,
        months_ago=0,
        package=[],
        from_year=None,
        to_year=None,
        seed=0,
        output=sampler.DEFAULT_OUTPUT,
        compare_europepmc=0,
        measure_europepmc=False,
    )
    return argparse.Namespace(**{**defaults, **kwargs})


class TestCountingAgainstHandBuiltMarkup:
    """The populations, checked against markup whose answer is known by eye."""

    def test_a_direct_label_and_a_footnote_label_are_told_apart(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1"><label>Table 1</label>
              <table-wrap-foot><fn><label>a</label><p>note</p></fn></table-wrap-foot>
            </table-wrap>"""),
        )

        assert row.tables == 1
        assert row.exhibits_with_direct_label == 1
        assert row.label_parents == {"table-wrap": 1, "fn": 1}

    def test_an_exhibit_labelled_only_indirectly_violates_the_premise(self):
        """The negative control for the parent rule's premise.

        Every real article measured so far carries its exhibit labels as
        direct children, so the "premise holds" line would print for a
        sampler that could not detect a violation at all. This is the shape
        that must make it print the other thing.
        """
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><caption><label>Figure 1</label></caption></fig>"""),
        )

        assert row.exhibits_with_direct_label == 0
        assert row.exhibits_with_descendant_label == 1
        assert row.label_parents == {"caption": 1}

    def test_nesting_is_counted_at_depth_not_at_the_outermost(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><label>Figure 1</label>
              <fig id="f1s1"><label>Figure 1—supplement 1</label></fig>
            </fig>"""),
        )

        assert (row.figures, row.nested_figures) == (2, 1)

    def test_a_nested_table_wrap_is_counted_too(self):
        """`nested_tables` had never been shown able to count.

        Its only assertion anywhere was the corpus's own `== 0`, which is
        tautological for a counter that cannot fire — so the cited "0 nested
        `<table-wrap>` across 1,994 articles" rested on nothing. JATS admits
        a `<table-wrap>` inside another's `<table-wrap-foot>`, which is
        exactly the shape `jats_parser` cites as the reason a table's frame
        has to be a stack. The zero is real; this is what makes it evidence.
        """
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1"><label>Table 1</label>
              <table-wrap-foot>
                <table-wrap id="t1s1"><label>Table 1—supplement</label></table-wrap>
              </table-wrap-foot>
            </table-wrap>"""),
        )

        assert (row.tables, row.nested_tables) == (2, 1)

    def test_alternatives_members_are_counted_with_their_declarations(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><alternatives>
              <graphic xlink:href="f1.tif"/>
              <graphic mime-subtype="jpeg" xlink:href="f1.jpg"/>
            </alternatives></fig>"""),
        )

        assert row.alternatives_members == 2
        assert row.alternatives_declaring_mime == 1
        assert row.alternatives_archival == 1

    def test_a_thumbnail_is_located_at_whichever_end_it_sits(self):
        last = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><graphic xlink:href="a.jpg"/>
              <graphic content-type="thumb" xlink:href="a.gif"/></fig>"""),
        )
        first = sampler.measure_article(
            "PMC2",
            _article("""
            <fig id="f1"><graphic content-type="thumb" xlink:href="a.gif"/>
              <graphic xlink:href="a.jpg"/></fig>"""),
        )

        assert (last.figures_multi_graphic, last.last_is_thumb, last.first_is_thumb) == (1, 1, 0)
        assert (first.figures_multi_graphic, first.last_is_thumb, first.first_is_thumb) == (1, 0, 1)

    def test_a_graphic_owned_by_a_non_exhibit_is_recorded_with_its_owner(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><label>Figure 1</label>
              <fn><p><graphic xlink:href="icon.gif"/></p></fn>
              <graphic xlink:href="real.jpg"/>
            </fig>"""),
        )

        assert row.foreign_owned_graphics == {"fn": 1}

    def test_alternatives_and_p_do_not_take_ownership(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><p><graphic xlink:href="a.jpg"/></p>
              <alternatives><graphic xlink:href="b.jpg"/></alternatives></fig>"""),
        )

        assert row.foreign_owned_graphics == {}

    def test_a_table_deposited_as_an_image_is_counted(self):
        """Issue #127's population."""
        image_only = sampler.measure_article(
            "PMC1",
            _article('<table-wrap id="t1"><graphic xlink:href="scan.png"/></table-wrap>'),
        )
        with_table = sampler.measure_article(
            "PMC2",
            _article("""
            <table-wrap id="t1"><graphic xlink:href="scan.png"/>
              <table><tbody><tr><td>x</td></tr></tbody></table></table-wrap>"""),
        )

        assert image_only.tables_image_only == 1
        assert with_table.tables_image_only == 0

    def test_attribute_values_are_counted_before_any_allow_list(self):
        """Issue #79's rule: a value the parser never accepts must still show.

        Counted after filtering, the table could only ever confirm the
        allow-list, which is precisely the defect #79 was.
        """
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><graphic content-type="print-only" xlink:href="a.jpg"/>
              <graphic specific-use="THUMBNAIL" xlink:href="b.gif"/></fig>"""),
        )

        assert row.content_type_values == {"print-only": 1}
        assert row.specific_use_values == {"thumbnail": 1}


class TestTheCaptionAndTitleOwnerPopulations:
    """The premises behind the routing rules #123, #125 and #130 turn on.

    The ``<label>`` rule's premise was earned by counting direct-child labels
    against labels anywhere and finding the two equal. A ``<caption>`` and a
    section ``<title>`` are now routed by their parent for the same reason, so
    they owe the same evidence — and the populations they replace owe a size,
    since #123's prevalence had never been measured against anything.
    """

    def test_a_direct_caption_and_a_supplements_caption_are_told_apart(self):
        """eLife's shape: the sibling case a nesting depth never sees."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><label>Figure 1</label>
              <caption><title>Study flow.</title><p>Lead.</p></caption>
              <supplementary-material id="sd1">
                <caption><p>Raw counts.</p></caption></supplementary-material>
            </fig>"""),
        )

        assert row.exhibits_with_direct_caption == 1
        assert row.exhibit_caption_owners == {"fig": 1, "supplementary-material": 1}

    def test_an_exhibit_captioned_only_indirectly_violates_the_premise(self):
        """The negative control, as for ``<label>``: every real article
        measured so far captions its exhibits directly, so the "premise holds"
        line would print for an instrument that could not detect otherwise."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><alternatives><caption><p>Indirect.</p></caption></alternatives></fig>"""),
        )

        assert row.exhibits_with_direct_caption == 0
        assert row.exhibits_with_descendant_caption == 1

    def test_a_nested_caption_is_counted_as_one(self):
        """Issue #123's own population — a caption inside a caption."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><caption><p>Lead.</p>
              <p><media><caption><p>Video legend.</p></caption></media></p>
              <p>Tail.</p></caption></fig>"""),
        )

        assert (row.captions, row.nested_captions) == (2, 1)

    def test_a_title_owned_by_a_footnote_group_inside_a_section_is_counted(self):
        """Issue #125: the population that used to rename the section."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sec><title>Additional information</title>
              <fn-group><title>Competing interests</title><fn><p>None.</p></fn></fn-group>
            </sec>"""),
        )

        assert row.sections == 1
        assert row.sections_with_direct_title == 1
        assert row.section_renaming_titles == {"fn-group": 1}

    def test_a_boxed_texts_caption_title_inside_a_section_is_counted(self):
        """Issue #130, which reaches the same branch one container along."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sec><title>Results</title>
              <boxed-text><caption><title>Box 1.</title></caption><p>Box.</p></boxed-text>
            </sec>"""),
        )

        assert row.section_renaming_titles == {"caption": 1}

    def test_a_title_inside_an_exhibit_was_never_the_sections(self):
        """The exhibit branch already dropped these, so counting them would
        report a change this fix did not make."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sec><title>Results</title>
              <table-wrap id="t1"><caption><title>Table 1.</title></caption>
                <table-wrap-foot><fn-group><title>Abbreviations</title>
                  </fn-group></table-wrap-foot></table-wrap>
            </sec>"""),
        )

        assert row.section_renaming_titles == {}

    def test_a_title_outside_any_section_was_never_the_sections_either(self):
        """The usual position for a <ref-list> or an <app> is loose in
        <back>, where no section is open and nothing was ever overwritten."""
        row = sampler.measure_article(
            "PMC1",
            _article("""<ref-list><title>References</title></ref-list>"""),
        )

        assert row.section_renaming_titles == {}


class TestTheTableSideCountsWhatTheParserWouldRoute:
    """A ``<td>``'s inline image is not the table's own rendition.

    Issue #135 named this as a residual: the counters walked ``el.iter()``,
    a whole subtree, while the parser routes a ``<graphic>`` by its **owner**.
    The first live run made it real rather than theoretical — of the ten
    recent-window tables carrying a ``<graphic>`` anywhere, the four holding
    more than one are the two articles that between them deposit 35 of the
    draw's 36 ``<td>``-owned images. Reported unscoped, the table side would
    have read "40% of tables carry several deposits", which is a statement
    about cell decoration and not about the ranking rule it was measuring.

    The figure side deliberately keeps the subtree walk: its percentages are
    cited in ``jats_parser`` and in CLAUDE.md, and re-scoping them silently
    would invalidate every one. Both committed draws record zero nested
    exhibits, so no ``<td>`` sits under a ``<fig>`` in either and the two
    walks agree there anyway.
    """

    def test_a_cell_image_is_not_the_tables_own_deposit(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1"><label>Table 1</label>
              <graphic xlink:href="t1.jpg"/>
              <table><tbody><tr><td><graphic xlink:href="tick.gif"/></td></tr></tbody></table>
            </table-wrap>"""),
        )

        assert row.tables_with_graphic == 1
        assert row.tables_multi_graphic == 0
        assert row.foreign_owned_graphics == {"td": 1}

    def test_a_transparent_wrapper_still_hands_the_deposit_over(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1"><alternatives>
              <graphic xlink:href="t1.tif"/><graphic xlink:href="t1.jpg"/>
            </alternatives></table-wrap>"""),
        )

        assert (row.tables_with_graphic, row.tables_multi_graphic) == (1, 1)

    def test_an_image_only_table_is_judged_on_what_it_owns(self):
        """A table whose only graphic is a cell decoration still has its
        markup, so it is not the population issue #127 is about."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1">
              <table><tbody><tr><td><graphic xlink:href="tick.gif"/></td></tr></tbody></table>
            </table-wrap>"""),
        )

        assert (row.tables_image_only, row.tables_with_graphic) == (0, 0)

    def test_a_nested_exhibits_deposit_belongs_to_the_nested_exhibit(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1"><graphic xlink:href="t1.jpg"/>
              <table-wrap-foot><fig id="f1"><graphic xlink:href="f1.jpg"/></fig>
              </table-wrap-foot></table-wrap>"""),
        )

        assert (row.tables_with_graphic, row.tables_multi_graphic) == (1, 0)
        assert (row.figures_with_graphic, row.figures_multi_graphic) == (1, 0)


class TestAnArticleThatCouldNotBeMeasuredIsNeverAFinding:
    def test_unparseable_xml_is_unmeasured_rather_than_empty(self):
        assert sampler.measure_article("PMC1", b"<article><body>") is None

    @pytest.mark.parametrize(
        "body",
        [
            b"<html><body>Service temporarily unavailable</body></html>",
            b"<error><message>rate limited</message></error>",
            b"<responseWrapper><resultList/></responseWrapper>",
        ],
        ids=["html-error-page", "xml-error-envelope", "wrong-endpoint"],
    )
    def test_well_formed_non_jats_is_unmeasured_rather_than_all_zero(self, body):
        """The envelope check (issue #166).

        Each of these parses perfectly, so `ET.ParseError` never fires and
        every counter reads zero. Without a root-element test that row is
        added, journalled, and enters every denominator as a measured
        article — and on `--compare-europepmc` an outage is counted as a
        *rendition disagreement*. The corpora legitimately contain all-zero
        rows, so nothing downstream can tell the two apart afterwards.
        """
        assert sampler.measure_article("PMC1", body) is None

    def test_a_real_article_is_still_measured(self):
        """The negative control: the envelope check must not refuse everything.

        A root test that rejected every document would turn every population
        in both corpora to zero while passing the three cases above, so this
        is what stops the guard going vacuous in the useful direction. The
        namespaced spelling is here because JATS is routinely served with a
        default namespace, and a check on the raw `root.tag` rather than on
        its local name would refuse every real article.
        """
        assert sampler.measure_article("PMC1", _article('<fig id="f1"/>')) is not None
        namespaced = b'<article xmlns="http://jats.nlm.nih.gov"><body/></article>'
        assert sampler.measure_article("PMC1", namespaced) is not None

    def test_an_unmeasured_article_enters_no_denominator(self):
        totals = sampler.Totals()
        totals.add(sampler.measure_article("PMC1", _article('<fig id="f1"/>')))
        totals.unmeasured += 1

        assert totals.articles == 1
        assert totals.attempts == 2
        assert totals.sum_of("figures") == 1

    def test_a_sample_past_the_threshold_is_not_reportable(self):
        totals = sampler.Totals()
        for index in range(4):
            totals.add(sampler.measure_article(f"PMC{index}", _article('<fig id="f1"/>')))
        totals.unmeasured = 4

        assert totals.unmeasured_share > sampler.UNMEASURED_SHARE_ERROR_THRESHOLD
        assert totals.reportable is False

    def test_an_empty_sample_is_not_reportable_either(self):
        """Zero rows is not a clean run; it is no evidence at all."""
        assert sampler.Totals().reportable is False

    def test_the_report_refuses_to_print_rates_when_unreportable(self, capsys):
        totals = sampler.Totals()
        totals.add(sampler.measure_article("PMC1", _article('<fig id="f1"/>')))
        totals.unmeasured = 9

        assert sampler.print_report(totals) is False
        assert "ERROR" in capsys.readouterr().out

    def test_a_reportable_sample_prints_its_populations(self, capsys):
        """Asserted on the counts, not on a verdict word.

        It used to assert ``PREMISE HOLDS``, a verdict word ``print_report``
        no longer prints. Only one direction of that comparison was ever sound
        (issue #162), and it is kept as a statement of what was measured; the
        counts beside it are what a reader acts on, and unlike a verdict they
        cannot be right for the wrong reason.
        """
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article("PMC1", _article('<fig id="f1"><label>Figure 1</label></fig>'))
        )

        assert sampler.print_report(totals) is True
        out = capsys.readouterr().out
        assert "exhibits with a direct-child <label>      : 1" in out
        assert "exhibits with no <label> of their own     : 0" in out
        # The zero branch of the one direction these counters do decide. It is
        # a measured all-clear, not a verdict, and it has to be reachable or
        # restoring it bought nothing. Both sections, since the caption half
        # had no assertion of any kind before.
        assert "No exhibit on this draw holds a <label> below it" in out
        # The caption half is vacuous on this fixture — the figure carries no
        # <caption> anywhere — so it must report an absent denominator rather
        # than an all-clear. A zero over nothing is not a clean result, which
        # is #162's own lesson one step further out.
        assert "no exhibit in this draw carries a\n   <caption> anywhere" in out
        assert "No exhibit on this draw holds a <caption> below it" not in out

    def test_a_draw_carrying_no_label_anywhere_reports_an_absent_denominator(self, capsys):
        """The zero test is satisfied vacuously by a draw with no labels.

        The back-filled corpus reaches exactly this on ``<caption>`` (0 of
        627). Printing the clean result over it would report a confirmation
        the draw cannot give.
        """
        totals = sampler.Totals()
        totals.add(sampler.measure_article("PMC1", _article('<fig id="f1"/>')))

        assert sampler.print_report(totals) is True
        out = capsys.readouterr().out
        assert "no exhibit in this draw carries a <label>" in out
        assert "No exhibit on this draw holds a <label> below it" not in out

    def test_an_exhibit_holding_a_label_below_it_is_reported_as_that_and_no_more(self, capsys):
        """The non-zero branch: #162's whole subject, and previously unprinted.

        The fixture is PMC12011025's shape — a ``<table-wrap>`` with no label
        of its own carrying a ``<table-wrap-foot><fn>`` marker. The report must
        count it under "holding a <label> below" and must not read it as the
        table's own, which is what ``PREMISE VIOLATED`` did.
        """
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article(
                "PMC1",
                _article("""
                <table-wrap id="t1">
                  <table><tbody><tr><td>1</td></tr></tbody></table>
                  <table-wrap-foot><fn><label>*</label><p>Note.</p></fn></table-wrap-foot>
                </table-wrap>"""),
            )
        )

        assert sampler.print_report(totals) is True
        out = capsys.readouterr().out
        assert "exhibits with a direct-child <label>      : 0" in out
        assert "exhibits with no <label> of their own     : 1" in out
        assert "...of those, ones holding a <label> below : 1" in out
        # The all-clear must not print here, and no verdict word may return.
        assert "No exhibit on this draw holds a <label> below it" not in out
        assert "PREMISE" not in out

    def test_image_only_tables_are_reported_as_a_share_of_tables(self, capsys):
        """A bare count cannot be compared across two draws of different sizes.

        #127's population is the one measured on two windows — 0 of 662 recent
        tables against a double-digit share of older ones — so it has to print
        as a rate, and the denominator has to be the tables rather than every
        exhibit, or a figure-heavy draw dilutes it.
        """
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article(
                "PMC1",
                _article("""
                <table-wrap id="t1"><graphic xlink:href="scan.png"/></table-wrap>
                <table-wrap id="t2"><table><tbody><tr><td>1</td></tr></tbody></table></table-wrap>
                <fig id="f1"><graphic xlink:href="f1.jpg"/></fig>"""),
            )
        )

        assert sampler.print_report(totals) is True
        line = next(ln for ln in capsys.readouterr().out.splitlines() if "and no <table>" in ln)
        assert "50.0%" in line, line


class TestHowAContribNamesItsContributorIsCounted:
    """The populations behind issues #120 and #140.

    JATS names a contributor with ``(name | string-name | collab | ...)`` and
    bmlib extracted only the first, so a consortium author vanished and an
    article deposited with ``<string-name>`` parsed to no authors at all. The
    fix is spec-driven — the deposit gives one undivided string, and splitting
    it would be assumed rather than measured — so what these counters answer is
    how much of a corpus each spelling reaches, and whether a ``<contrib>``
    nests often enough for the parser's frame stack to earn its place.
    """

    def test_each_spelling_is_counted_under_its_own_name(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group content-type="author">
              <contrib><name><surname>Real</surname></name></contrib>
              <contrib><string-name>Jane Q Smith</string-name></contrib>
              <contrib><collab>the INHERIT Trial Group</collab></contrib>
              <contrib><anonymous/></contrib>
            </contrib-group>"""),
        )

        assert row.contribs == 4
        assert row.contrib_name_spellings == {
            "name": 1,
            "string-name": 1,
            "collab": 1,
            "anonymous": 1,
        }

    def test_a_spelling_nobody_listed_prints_as_itself(self):
        """The vocabulary is open, which is the whole reason it is a vocabulary.

        JATS's contributor model ends in ``...``, and ``<on-behalf-of>`` is in
        that tail. Counted against a closed set, an unforeseen spelling falls
        into ``"(none)"`` and the article is reported as naming nobody — #121's
        mis-certification, inside the instrument built to detect the next
        #120. #130's ``<list>`` is the standing precedent that an enumeration
        written from the issues to hand misses the container nobody thought of.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<contrib-group content-type="author">'
                "<contrib><on-behalf-of>the XYZ Group</on-behalf-of></contrib>"
                "<contrib><future-name>Nobody has listed this</future-name></contrib>"
                "</contrib-group>"
            ),
        )

        # The unlisted spelling prints under its own name *and* is counted as
        # naming nobody, because whether it names a contributor is exactly what
        # is not known about it. Two facts, not a conclusion — a reader sees
        # both the element and how bmlib's reading treated it.
        assert row.contrib_name_spellings == {
            "on-behalf-of": 1,
            "future-name": 1,
            "(none)": 1,
        }

    def test_only_the_spellings_bmlib_extracts_are_marked_collected(self, capsys):
        """``<anonymous/>`` names a contributor and is deliberately not collected.

        The annotation used to read "anything that is not ``(none)``", which
        printed a false claim in the evidence table a rule change is judged
        against.
        """
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article(
                "PMC1",
                _article(
                    '<contrib-group content-type="author">'
                    "<contrib><anonymous/></contrib>"
                    "<contrib><collab>the INHERIT Trial Group</collab></contrib>"
                    "</contrib-group>"
                ),
            )
        )

        assert sampler.print_report(totals) is True
        section = _section(capsys.readouterr().out, "11. HOW A <contrib>")
        anonymous = next(ln for ln in section if "anonymous" in ln)
        collab = next(ln for ln in section if "collab" in ln)
        assert "collected as an author" not in anonymous
        assert "collected as an author" in collab

    def test_a_contrib_naming_nobody_gets_its_own_vocabulary_entry(self):
        """The shape the parser reports at DEBUG and drops.

        Counted rather than passed over, because a draw in which it is common
        says something about a publisher and a silence says nothing.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<contrib-group content-type="author">'
                '<contrib><xref ref-type="aff" rid="a1"/></contrib>'
                "</contrib-group>"
            ),
        )

        assert row.contrib_name_spellings == {"(none)": 1}

    def test_a_rosters_names_are_not_credited_to_the_collaboration(self):
        """The owner-scoping rule, which a subtree walk gets wrong.

        A ``<collab>`` may carry a ``<contrib-group>`` of the collaboration's
        own members, so a ``<contrib>`` nests inside another. Counting the
        subtree credits each member's ``<name>`` to the consortium, which
        reports the article as naming every contributor the structured way —
        the manufactured population #135's residual is about, one element
        family over.
        """
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group content-type="author">
              <contrib><collab>the INHERIT Trial Group
                <contrib-group>
                  <contrib><name><surname>Member</surname></name></contrib>
                  <contrib><name><surname>Other</surname></name></contrib>
                </contrib-group>
              </collab></contrib>
            </contrib-group>"""),
        )

        assert row.contribs == 3
        assert row.contrib_name_spellings == {"collab": 1, "name": 2}
        assert row.nested_contribs == 2
        assert row.collabs_with_a_roster == 1

    def test_a_name_below_a_wrapper_still_counts(self):
        """``<name-alternatives>`` wraps a name without being one.

        Looked for at depth, the way the parser's own arms fire anywhere
        inside the open ``<contrib>`` — a direct-child test would report this
        contributor as naming nobody.
        """
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group content-type="author">
              <contrib><name-alternatives>
                <name><surname>Nakamura</surname></name>
                <string-name>Nakamura Kenji</string-name>
              </name-alternatives></contrib>
            </contrib-group>"""),
        )

        assert row.contrib_name_spellings == {"name": 1, "string-name": 1}

    def test_an_article_naming_nobody_with_name_loses_every_author(self):
        """#140's distinguishing claim, and the reason it is not #120.

        ``<collab>`` costs an article some of its contributors; an article
        naming all of them undivided loses the lot.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<contrib-group content-type="author">'
                "<contrib><string-name>Jane Q Smith</string-name></contrib>"
                "</contrib-group>"
            ),
        )

        assert row.articles_losing_every_author == 1

    def test_one_structured_name_is_enough_to_keep_the_article(self):
        """The negative control: a partial loss is #120, not #140."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group content-type="author">
              <contrib><name><surname>Real</surname></name></contrib>
              <contrib><collab>the INHERIT Trial Group</collab></contrib>
            </contrib-group>"""),
        )

        assert row.articles_losing_every_author == 0

    def test_a_row_written_before_these_counters_reads_as_not_measured(self):
        """A zero here is a real answer, so an absent counter must not read as one.

        The third generation of counters to arrive on this row; both earlier
        ones were mis-readable the same way, and #127 is the case where a
        population that genuinely measures zero in one window measures 11.8%
        in another.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<contrib-group content-type="author">'
                "<contrib><name><surname>Real</surname></name></contrib>"
                "</contrib-group>"
            ),
        )
        stale = row.to_dict()
        for name in sampler._CONTRIB_SIDE_COUNTERS:
            del stale[name]

        restored = sampler.ArticleMeasurement.from_dict(stale)
        totals = sampler.Totals()
        totals.add(restored)

        assert restored.contribs == sampler.NOT_MEASURED
        assert not totals.measured("contribs")
        assert totals.measured("figures")

    def test_the_report_says_so_under_this_section_and_not_another(self, capsys):
        """Asserted against the section's own lines, not against the whole report.

        Eight sections can print that phrase (5, 9-15), so a bare
        ``"NOT MEASURED" in out`` passes for a run in which *this* one printed
        a rate over rows that never carried the counter.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<contrib-group content-type="author">'
                "<contrib><collab>the INHERIT Trial Group</collab></contrib>"
                "</contrib-group>"
            ),
        )
        stale = row.to_dict()
        for name in sampler._CONTRIB_SIDE_COUNTERS:
            del stale[name]
        totals = sampler.Totals()
        totals.add(sampler.ArticleMeasurement.from_dict(stale))

        # `is False`: a sample short of a counter generation is not fully
        # reportable, so the corpus goes to `*.unreportable.json` rather than
        # to the canonical name with `-1` inline (issue #168).
        assert sampler.print_report(totals) is False
        section = _section(capsys.readouterr().out, "11. HOW A <contrib>")
        assert any("NOT MEASURED" in line for line in section)

    def test_a_freshly_measured_corpus_reports_the_spellings(self, capsys):
        """The negative control, again scoped to this section."""
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article(
                "PMC1",
                _article(
                    '<contrib-group content-type="author">'
                    "<contrib><collab>the INHERIT Trial Group</collab></contrib>"
                    "</contrib-group>"
                ),
            )
        )

        assert sampler.print_report(totals) is True
        section = _section(capsys.readouterr().out, "11. HOW A <contrib>")
        assert not any("NOT MEASURED" in line for line in section)
        assert any("collab" in line for line in section)


class TestTheWalkStopsWhereTheParserStops:
    """Issue #138 — the sampler must count what `jats_parser` routes.

    `<sub-article>` and `<response>` open a region in which the parser fires
    no handler at all (#110), so a whole-document count is a count of a
    different thing. The pair of tests below are the two halves: nothing
    inside a region contributes, and the row still says what the old walk
    would have said.
    """

    NESTED = """
        <fig id="f1"><label>Figure 1</label><caption><p>Ours.</p></caption>
          <graphic xlink:href="ours.jpg"/></fig>
        <sub-article article-type="peer-review">
          <front-stub><contrib-group><contrib contrib-type="author">
            <name><surname>Reviewer</surname></name></contrib></contrib-group></front-stub>
          <body>
            <fig id="rf1"><label>Figure R1</label><caption><p>Theirs.</p></caption>
              <graphic xlink:href="theirs.jpg"/></fig>
            <sec><title>Review</title><p>Prose.</p></sec>
          </body>
        </sub-article>"""

    def test_nothing_inside_a_nested_article_is_counted(self):
        row = sampler.measure_article("PMC1", _article(self.NESTED))

        assert row.figures == 1
        assert row.graphics == 1
        assert row.captions == 1
        assert row.contribs == 0
        assert row.sections == 0
        assert row.nested_article_regions == 1
        assert row.label_parents == {"fig": 1}

    def test_the_row_records_what_the_unscoped_walk_would_have_said(self):
        """The measurement #158 wants, not merely the correction #138 asks for."""
        row = sampler.measure_article("PMC1", _article(self.NESTED))

        assert row.unscoped["figures"] == 2
        assert row.unscoped["graphics"] == 2
        assert row.unscoped["contribs"] == 1
        assert row.unscoped["label_parents"] == {"fig": 2}
        # A field the region cannot move is absent, not zero-valued.
        assert "tables" not in row.unscoped

    def test_an_article_with_no_nested_article_records_no_difference(self):
        row = sampler.measure_article(
            "PMC1", _article("<fig id='f1'><label>Figure 1</label></fig>")
        )

        assert row.nested_article_regions == 0
        assert row.unscoped == {}

    def test_a_response_is_a_region_too_and_nesting_is_visible(self):
        """`<response>` is the other half of the two-element set, and JATS nests them."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sub-article><body><p>Round one.</p>
              <response><body><fig id="rf"><label>F</label></fig></body></response>
            </body></sub-article>"""),
        )

        assert row.figures == 0
        assert row.nested_article_regions == 1
        assert row.unscoped["nested_article_regions"] == 2

    def test_a_row_written_before_this_counter_reads_as_not_measured(self):
        """The `NOT_MEASURED` rule — a zero here is also a genuine empty draw."""
        row = sampler.ArticleMeasurement.from_dict({"pmcid": "PMC1", "figures": 3})

        assert row.nested_article_regions == sampler.NOT_MEASURED
        assert row.unscoped == {}

    def test_articles_losing_every_author_is_no_longer_silenced_by_a_reviewer(self):
        """The strongest case #138 has: the #140-vs-#120 flag, not merely a
        count, used to go silent on an article the old whole-document walk
        happened to reach a reviewer's `<name>` from.

        An article naming its own authors only as an undivided `<collab>`
        should set `articles_losing_every_author`. Before this fix, a
        peer-review `<sub-article>` naming its reviewer with `<name>` (the
        commonest reviewer construct) reached the same counter and cleared
        the flag — on the exact article the flag exists to catch.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<contrib-group content-type="author">'
                "<contrib><collab>the INHERIT Trial Group</collab></contrib>"
                "</contrib-group>"
                '<sub-article article-type="peer-review">'
                '<front-stub><contrib-group><contrib contrib-type="author">'
                "<name><surname>Reviewer</surname></name>"
                "</contrib></contrib-group></front-stub>"
                "<body><p>Round one.</p></body>"
                "</sub-article>"
            ),
        )

        assert row.articles_losing_every_author == 1
        assert row.unscoped == {
            "contribs": 2,
            "contrib_name_spellings": {"collab": 1, "name": 1},
            "articles_losing_every_author": 0,
        }


class TestAnEmptyMemberIsNotAFailedFetch:
    """`_measure_and_journal`'s `is None`, which nothing exercised.

    The comment at that call site calls the distinction load-bearing at
    length — only a failed live fetch is `None`, while an archive member
    that is present and *empty* is `b""`, falsy but retrieved — and yet
    `if xml is None:` → `if not xml:` survived the whole suite. An empty
    member is a truncated extraction or a zero-byte deposit: a corpus
    defect, reported as a live-source failure on an archive run that made no
    request at all.
    """

    def test_an_empty_member_is_unparseable_not_unavailable(self, tmp_path):
        journal = tmp_path / "j.jsonl"
        totals = sampler.Totals()

        with journal.open("w", encoding="utf-8") as handle:
            sampler._measure_and_journal(handle, totals, [("PMC1", b"")])

        assert totals.unmeasured == 1
        assert totals.unmeasured_causes == {"unparseable": 1}

    def test_a_missing_fetch_is_still_unavailable(self, tmp_path):
        """The negative control, and the other side of the same branch."""
        journal = tmp_path / "j.jsonl"
        totals = sampler.Totals()

        with journal.open("w", encoding="utf-8") as handle:
            sampler._measure_and_journal(handle, totals, [("PMC1", None)])

        assert totals.unmeasured_causes == {"europepmc_unavailable": 1}


class TestAFetchThatCouldNotBeMadeIsNeverAFinding:
    """`_fetch` was 100% unexercised — every test mocked it.

    This is the rule `tests/test_free_pdf_sampler.py` and
    `tests/test_databank_sampler.py` enforce for their own samplers, and it
    is sharper here: an HTTP error body that happens to parse used to become
    a measured all-zero row (issue #166 closes that half), and one that does
    not parse would be filed under the wrong cause.
    """

    class _Response:
        def __init__(self, status_code: int, content: bytes = b"body") -> None:
            self.status_code = status_code
            self.content = content
            self.headers: dict[str, str] = {}

    class _Client:
        def __init__(self, *responses):
            self._responses = list(responses)
            self.calls: list[str] = []

        def get(self, url):
            self.calls.append(url)
            return self._responses.pop(0)

    def test_a_non_200_is_unmeasured_rather_than_its_body(self):
        """Deleting this guard hands an error page's bytes back as content."""
        for status in (404, 500, 403):
            client = self._Client(self._Response(status, b"<html>nope</html>"))

            url = "https://example.org/PMC1/fullTextXML"

            assert sampler._fetch(client, url, lambda u: None) is None

    def test_a_200_returns_its_content(self):
        """The negative control: a guard that refused every response would
        empty every corpus while passing the test above."""
        client = self._Client(self._Response(200, b"<article/>"))

        assert (
            sampler._fetch(client, "https://example.org/PMC1/fullTextXML", lambda u: None)
            == b"<article/>"
        )

    def test_a_transport_error_is_unmeasured_not_an_exception(self):
        """An `httpx.HTTPError` out of `_fetch` would abort a whole run at
        whatever article the network happened to drop."""
        import httpx

        class _Failing:
            def get(self, url):
                raise httpx.ConnectError("no route")

        assert (
            sampler._fetch(_Failing(), "https://example.org/PMC1/fullTextXML", lambda u: None)
            is None
        )

    def test_an_unprobeable_url_is_refused_without_a_request(self):
        client = self._Client()

        assert sampler._fetch(client, "not-a-url", lambda u: None) is None
        assert client.calls == []


class TestTheServedRenditionIsAskedForByName:
    """The endpoint itself, unpinned at all three call sites.

    `fullTextXML` is what `FullTextService` feeds the parser, and it is the
    whole reason the corpora are measured from the served rendition rather
    than the archive one. Swapping it for another Europe PMC resource that
    still returns well-formed XML produced a full corpus of all-zero rows at
    exit 0; the fakes asserted only that the *pmcid* appeared in the URL.
    """

    def _package(self, tmp_path: Path) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "PMC1.xml").write_text(
            "<article><front><article-meta>"
            "<pub-date pub-type='epub'><year>2024</year></pub-date>"
            "</article-meta></front></article>"
        )
        return tmp_path

    def test_measure_europepmc_asks_for_full_text_xml(self, tmp_path):
        package = self._package(tmp_path / "package")
        output = tmp_path / "out" / "jats_exhibits.json"
        urls: list[str] = []

        def fake_fetch(client, url, pace):
            urls.append(url)
            return _article("<fig id='f1'/>")

        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "1",
            "-o",
            str(output),
            "--measure-europepmc",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(sampler, "_fetch", side_effect=fake_fetch):
                assert sampler.main() == 0

        assert urls
        for url in urls:
            assert url.endswith("/PMC1/fullTextXML"), url

    def test_the_comparison_asks_for_full_text_xml_too(self):
        urls: list[str] = []

        def fake_fetch(client, url, pace):
            urls.append(url)
            return _article("<fig id='f1'/>")

        with mock.patch.object(sampler, "_fetch", side_effect=fake_fetch):
            sampler.compare_renditions(
                object(), lambda url: None, [("PMC1", _article("<fig id='f1'/>"))]
            )

        assert urls == [f"{sampler.EUROPE_PMC}/PMC1/fullTextXML"]


class TestARefusalStopsTheRun:
    """~25 refusal assertions all call `_validate_args` directly.

    None asserted that `main()` acts on what it returns, so
    `if refusal is not None:` → `if False:` survived the whole suite. Every
    one of those refusals exists to stop a rate being printed over a draw
    nobody asked for; the wiring that makes that happen was unpinned.
    """

    def test_a_refused_argument_exits_non_zero_and_writes_nothing(self, tmp_path, capsys):
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(tmp_path / "does-not-exist"),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "-o",
            str(output),
        ]

        with mock.patch.object(sys, "argv", argv):
            code = sampler.main()

        assert code == 2
        assert not output.exists()
        assert not output.parent.exists()
        assert "--package" in capsys.readouterr().err

    def test_a_window_count_below_one_is_refused_through_main(self, tmp_path):
        """`--months 0`'s `_validate_args` branch was uncovered: the existing
        test drives `_month_windows`, not the flag."""
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = ["sample_jats_exhibits.py", "--months", "0", "-o", str(output)]

        with mock.patch.object(sys, "argv", argv):
            assert sampler.main() == 2

        assert not output.exists()


class TestTheSamplerDoesNotShareTheParsersPredicates:
    """A corpus labelled by the rule under test can only confirm that rule."""

    def test_the_archival_hints_are_wider_than_the_parsers(self):
        from bmlib.fulltext.jats_parser import _ARCHIVAL_EXTENSIONS, _ARCHIVAL_MIME_SUBTYPES

        parser_side = _ARCHIVAL_MIME_SUBTYPES | {e.lstrip(".") for e in _ARCHIVAL_EXTENSIONS}

        assert sampler._ARCHIVAL_HINTS - parser_side, (
            "the sampler must be able to report a deposit the parser does not "
            "classify as archival, or it can only confirm the parser's list"
        )

    def test_the_sampler_classifies_a_deposit_the_parser_would_not(self):
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<fig id="f1"><alternatives><graphic xlink:href="f1.svg"/></alternatives></fig>'
            ),
        )

        assert row.alternatives_archival == 1

    def test_a_declared_archival_mime_subtype_is_classified_without_the_extension(self):
        """The other half of "archival by either test", which was unexercised.

        Every `mime-subtype` fixture in this file declares `jpeg`, which is
        deliberately *not* archival, so both halves of the claim were driven
        through the extension alone — and a broken `mime-subtype` branch
        manufactures exactly the "none is archival by either test" zero that
        `CLAUDE.md` cites over 7,055 deposits. The href here is a plain
        `.jpg`, so only the declaration can classify it.
        """
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<fig id="f1"><alternatives>'
                '<graphic mime-subtype="tiff" xlink:href="f1.jpg"/>'
                "</alternatives></fig>"
            ),
        )

        assert row.alternatives_archival == 1


class TestTheSampleIsStratified:
    def test_the_windows_are_whole_calendar_months_most_recent_first(self):
        windows = sampler._month_windows(3, date(2026, 3, 15))

        assert windows == [
            ("2026-02-01", "2026-02-28"),
            ("2026-01-01", "2026-01-31"),
            ("2025-12-01", "2025-12-31"),
        ]

    def test_a_december_window_does_not_overflow_the_year(self):
        assert sampler._month_windows(1, date(2026, 1, 9)) == [("2025-12-01", "2025-12-31")]

    def test_a_leap_february_keeps_its_last_day(self):
        assert sampler._month_windows(1, date(2024, 3, 1)) == [("2024-02-01", "2024-02-29")]

    def test_an_offset_draws_from_older_months(self):
        """``skip`` is what lets the draw reach back-filled deposits.

        The default window is the last two years, which is born-digital XML;
        a table deposited as a scanned image is a property of older material,
        so measuring that population needs a draw that does not include the
        recent months at all.
        """
        windows = sampler._month_windows(2, date(2026, 3, 15), skip=12)

        assert windows == [("2025-02-01", "2025-02-28"), ("2025-01-01", "2025-01-31")]

    def test_an_offset_of_zero_is_the_undisplaced_window(self):
        """The negative control: the offset is not silently always applied."""
        assert sampler._month_windows(2, date(2026, 3, 15), skip=0) == sampler._month_windows(
            2, date(2026, 3, 15)
        )

    def test_an_offset_crossing_a_year_boundary_does_not_drift(self):
        """Skipping is the same arithmetic as taking, not a subtraction of years."""
        assert sampler._month_windows(1, date(2026, 3, 15), skip=14) == [
            ("2024-12-01", "2024-12-31")
        ]

    def test_the_offset_reaches_the_search_query(self):
        """A flag the walk does not honour measures the default window twice.

        The expected window is *derived* rather than written down. This walk
        reaches ``date.today()`` — the only test in this class that does, since
        ``open_access_pmcids`` owns the clock — so a literal year would be a
        dated CI failure: ``skip=600`` is exactly 50 years, and it rolls over
        to 1977 on 2027-02-01. Deriving it keeps the mutation the test exists
        for, because an unpassed offset still yields a recent month.
        """
        queries: list[str] = []

        def fake_fetch(client, url, pace):
            queries.append(url)
            return json.dumps({"resultList": {"result": [{"pmcid": "PMC1"}]}}).encode()

        with mock.patch.object(sampler, "_fetch", fake_fetch):
            sampler.open_access_pmcids(None, None, target=1, months=1, skip_months=600)

        first, last = sampler._month_windows(1, date.today(), skip=600)[0]
        assert queries, "the walk made no request"
        assert f"{first} TO {last}" in unquote(queries[0]), unquote(queries[0])

    def test_a_negative_offset_is_refused_rather_than_silently_shrinking(self):
        """``skip`` is both a loop bound and a slice index, so it degrades twice.

        ``skip=-1, months=24`` returned a single window from two years ago and
        ``skip=-24`` returned none at all — in both cases with a rate and a
        Wilson interval printed over it. Refusing at the entry is what
        ``sync()`` does with a negative ``recheck_days``, and for the reason.
        """
        with pytest.raises(ValueError, match="skip must not be negative"):
            sampler._month_windows(24, date(2026, 3, 15), skip=-1)

    def test_a_window_count_below_one_is_refused(self):
        with pytest.raises(ValueError, match="months must be at least 1"):
            sampler._month_windows(0, date(2026, 3, 15))


class TestRowsSurviveTheJournal:
    def test_a_row_round_trips_through_its_dict_form(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <fig id="f1"><label>Figure 1</label>
              <fn><label>a</label></fn>
              <graphic content-type="thumb" xlink:href="a.gif"/>
              <graphic xlink:href="a.jpg"/></fig>"""),
        )

        restored = sampler.ArticleMeasurement.from_dict(row.to_dict())

        assert restored.to_dict() == row.to_dict()
        assert restored.label_parents == row.label_parents

    def test_unscoped_survives_the_round_trip(self):
        """`unscoped` is a plain ``dict``, not a ``Counter`` — `from_dict`
        must not special-case it away. It currently survives only because an
        empty dict is not ``None``, which is coincidental rather than
        asserted anywhere else."""
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<fig id="f1"><graphic xlink:href="ours.jpg"/></fig>'
                "<sub-article><body>"
                '<fig id="rf"><graphic xlink:href="theirs.jpg"/></fig>'
                "</body></sub-article>"
            ),
        )

        restored = sampler.ArticleMeasurement.from_dict(row.to_dict())

        assert row.unscoped, "the fixture must actually exercise a non-empty diff"
        assert restored.unscoped == row.unscoped

    def test_wilson_refuses_an_empty_denominator(self):
        """Borrowed from ``_sampling``; a zero-attempt interval would print as
        a perfect score."""
        with pytest.raises(ValueError):
            sampler.wilson(0, 0)


class TestTheCommandLineWiring:
    """The flag has to survive argparse and reach the walk.

    ``open_access_pmcids`` is called positionally in ``main``, and nothing
    covered that call or the parser defaults — so a swapped argument or a
    changed default would silently measure the wrong window and write the
    result out as evidence.
    """

    def test_the_offset_defaults_to_the_undisplaced_window(self):
        args = sampler._build_arg_parser().parse_args([])

        assert args.months_ago == 0
        assert args.months == sampler.SAMPLE_MONTHS

    def test_the_offset_is_parsed_from_the_command_line(self):
        args = sampler._build_arg_parser().parse_args(["--months-ago", "336"])

        assert args.months_ago == 336

    def test_a_displaced_draw_may_not_overwrite_the_default_corpus(self):
        """The default path is the *recent* draw, and the journal follows it.

        So a displaced run without ``-o`` either replaces that corpus with an
        older window under its name, or tops its rows up with another window's
        and prints the pooled result as one rate. #127's whole claim is that
        the window decides the answer, so a pooled number describes neither.
        """
        args = sampler._build_arg_parser().parse_args(["--months-ago", "336"])

        refusal = sampler._validate_args(args)

        assert refusal is not None
        assert "-o" in refusal

    def test_the_undisplaced_draw_still_writes_the_default_corpus(self):
        """The negative control: the guard is not refusing every run."""
        assert sampler._validate_args(sampler._build_arg_parser().parse_args([])) is None

    def test_a_displaced_draw_naming_its_own_output_is_allowed(self):
        args = sampler._build_arg_parser().parse_args(
            ["--months-ago", "336", "-o", "tests/data/jats_exhibits.backfill.json"]
        )

        assert sampler._validate_args(args) is None

    def test_a_negative_offset_is_refused_at_the_command_line(self):
        args = sampler._build_arg_parser().parse_args(["--months-ago", "-5"])

        assert sampler._validate_args(args) is not None


class TestTheTableSideOfTheRankingIsCounted:
    """Issue #135 — #127 routes a table's deposits through #117's ranking.

    That rule was measured on figures alone. These counters are what a later
    draw would settle it with, and they are kept *separate* from the figure
    ones: the figure percentages are cited in ``jats_parser`` and CLAUDE.md,
    and widening their denominator would invalidate every one of them.
    """

    def test_a_tables_deposits_are_counted_apart_from_a_figures(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1">
              <graphic xlink:href="t1.jpg"/>
              <graphic content-type="thumb" xlink:href="t1-thumb.gif"/></table-wrap>
            <fig id="f1"><graphic xlink:href="f1.jpg"/></fig>"""),
        )

        assert (row.tables_with_graphic, row.tables_multi_graphic) == (1, 1)
        assert (row.tables_last_is_thumb, row.tables_first_is_thumb) == (1, 0)
        assert (row.figures_with_graphic, row.figures_multi_graphic) == (1, 0)

    def test_a_thumbnail_deposited_first_is_located_at_that_end(self):
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1">
              <graphic specific-use="thumbnail" xlink:href="t1-thumb.gif"/>
              <graphic xlink:href="t1.jpg"/></table-wrap>"""),
        )

        assert (row.tables_first_is_thumb, row.tables_last_is_thumb) == (1, 0)

    def test_a_table_carrying_both_renditions_is_counted_apart(self):
        """The population ``to_html()`` discards, measured like the kept one."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <table-wrap id="t1"><graphic xlink:href="scan.png"/>
              <table><tbody><tr><td>1</td></tr></tbody></table></table-wrap>
            <table-wrap id="t2"><graphic xlink:href="only.png"/></table-wrap>"""),
        )

        assert (row.tables_with_both, row.tables_image_only) == (1, 1)
        assert row.tables_with_graphic == 2

    def test_a_corpus_predating_the_counter_says_so_rather_than_printing_zero(self, capsys):
        """A silent zero is what this population looks like in the wrong window.

        The four counters arrived with #135, so rows written before it sum to
        zero on every one — indistinguishable from a draw in which no table
        deposits an image, which is precisely the reading #127 spent two
        windows disproving. The identity is exact by construction, so a
        disagreement can only mean the rows predate the counter.
        """
        row = sampler.measure_article(
            "PMC1",
            _article('<table-wrap id="t1"><graphic xlink:href="scan.png"/></table-wrap>'),
        )
        stale = row.to_dict()
        for key in sampler._TABLE_SIDE_COUNTERS:
            del stale[key]
        totals = sampler.Totals()
        totals.add(sampler.ArticleMeasurement.from_dict(stale))

        # `is False`: a sample short of a counter generation is not fully
        # reportable, so the corpus goes to `*.unreportable.json` rather than
        # to the canonical name with `-1` inline (issue #168).
        assert sampler.print_report(totals) is False
        assert "NOT MEASURED" in capsys.readouterr().out

    def test_a_freshly_measured_corpus_prints_the_rates(self, capsys):
        """The negative control: the guard is not permanently on."""
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article(
                "PMC1",
                _article('<table-wrap id="t1"><graphic xlink:href="scan.png"/></table-wrap>'),
            )
        )

        assert sampler.print_report(totals) is True
        assert "NOT MEASURED" not in capsys.readouterr().out

    def test_the_both_renditions_row_is_not_reported_as_zero_either(self, capsys):
        """The recent corpus has no image-only table, so its counters agree at
        zero and nothing about the *identity* between them reveals the gap.
        Only an explicit sentinel does — which is why absence is recorded at
        load rather than inferred at report."""
        row = sampler.measure_article(
            "PMC1",
            _article(
                '<table-wrap id="t1"><table><tbody><tr><td>1</td></tr></tbody></table></table-wrap>'
            ),
        )
        stale = row.to_dict()
        for key in sampler._TABLE_SIDE_COUNTERS:
            del stale[key]
        totals = sampler.Totals()
        totals.add(sampler.ArticleMeasurement.from_dict(stale))

        # `is False`: a sample short of a counter generation is not fully
        # reportable, so the corpus goes to `*.unreportable.json` rather than
        # to the canonical name with `-1` inline (issue #168).
        assert sampler.print_report(totals) is False
        line = next(ln for ln in capsys.readouterr().out.splitlines() if "AND a <table>" in ln)
        assert "NOT MEASURED" in line, line

    def test_one_stale_row_among_fresh_ones_still_reports_not_measured(self, capsys):
        """The sentinel is small and the sum is not.

        One row predating the counter contributes -1 while three hundred fresh
        ones contribute real counts, so the total stays positive and the
        population would print as a rate that silently omits the stale row. A
        journal is topped up across runs, so a mixed one is the ordinary case.

        Asserted through `_section`, and with a fixture that makes **all five**
        `_TABLE_SIDE_COUNTERS` positive, because neither half is optional. This
        test used to assert `"NOT MEASURED" in out` over the whole report,
        against a fixture whose fresh rows moved one counter — so mutating
        `Totals.measured` to ask the *sum* rather than each row (the exact rule
        this test names) left it green twice over: four of the five counters
        still summed to -1 and fired the sentinel in section 5 anyway, and
        section 6's `tables_with_both` line prints the same string. A
        whole-report substring cannot say which section answered, which is what
        `_section` exists for and what the sibling test above already uses.
        """
        both = _article(
            '<table-wrap id="t1">'
            "<table><tbody><tr><td>1</td></tr></tbody></table>"
            '<graphic content-type="thumb" xlink:href="a.gif"/>'
            '<graphic content-type="thumb" xlink:href="b.gif"/>'
            "</table-wrap>"
        )
        stale = sampler.measure_article("PMC1", both).to_dict()
        for key in sampler._TABLE_SIDE_COUNTERS:
            del stale[key]
        totals = sampler.Totals()
        totals.add(sampler.ArticleMeasurement.from_dict(stale))
        for n in range(2, 30):
            totals.add(sampler.measure_article(f"PMC{n}", both))

        for name in sampler._TABLE_SIDE_COUNTERS:
            assert totals.sum_of(name) > 0, f"{name} must stay positive for this to bite"
        # `is False`: a sample short of a counter generation is not fully
        # reportable, so the corpus goes to `*.unreportable.json` rather than
        # to the canonical name with `-1` inline (issue #168).
        assert sampler.print_report(totals) is False
        section = _section(capsys.readouterr().out, "5. SEVERAL <graphic> PER TABLE")
        assert any("NOT MEASURED" in line for line in section), section


class TestEveryCounterIsInAGeneration:
    """The `TestTheAuditNetIsComplete` precedent, applied to the sentinel.

    `_COUNTER_GENERATIONS` is a hand-maintained map from a generation's name
    to the field names added with it, and until this test existed nothing
    kept it in step with `ArticleMeasurement` — the rule lived in prose, and
    this repo's standing finding is that a rule enforced by prose is not
    enforced. Both directions fail, for different reasons.
    """

    def _fields(self):
        import dataclasses

        return {
            f.name: f
            for f in dataclasses.fields(sampler.ArticleMeasurement)
            if f.name not in {"pmcid", "unscoped"}
        }

    def test_every_counter_reaches_a_generation_or_a_named_exclusion(self):
        """A field added and forgotten defaults to 0 on a stale row.

        That reads as measured-empty, which is exactly the collapse the
        sentinel exists to prevent — and it is silent, permanent, and lands
        in the committed corpus.
        """
        covered = set(sampler._FIRST_GENERATION_COUNTERS)
        for names in sampler._COUNTER_GENERATIONS.values():
            covered |= set(names)

        orphans = sorted(
            name
            for name, field in self._fields().items()
            if "Counter" not in str(field.type) and name not in covered
        )

        assert orphans == [], (
            f"{orphans} reach no counter generation and no named exclusion. "
            "Add each to `_COUNTER_GENERATIONS` (a new generation, if it ships "
            "in its own commit) or to `_FIRST_GENERATION_COUNTERS`."
        )

    def test_no_generation_names_something_that_is_not_a_field(self):
        """The other direction, and the sharper one.

        `from_dict` sets the sentinel with `setattr`, and a non-slots
        dataclass accepts any attribute — so a rename or a typo in a tuple
        creates a phantom `-1` on a **fresh** row. That row is written into
        the corpus, `sum_of` subtracts it, and the section it gates prints
        NOT MEASURED forever over a perfectly good draw.
        """
        fields = set(self._fields())
        named = set(sampler._FIRST_GENERATION_COUNTERS)
        for names in sampler._COUNTER_GENERATIONS.values():
            named |= set(names)

        assert sorted(named - fields) == []

    def test_the_generations_do_not_overlap(self):
        """A field in two generations is a field whose provenance is unclear.

        Harmless today — `measured` is an `all()` either way — but it means
        two commits both claim to have introduced it, and the report would
        gate two sections on one absence.
        """
        seen: dict[str, str] = {}
        clashes = []
        for label, names in sampler._COUNTER_GENERATIONS.items():
            for name in names:
                if name in seen:
                    clashes.append((name, seen[name], label))
                seen[name] = label

        assert clashes == []

    def test_a_sentinel_row_is_not_counted_as_an_article_that_carries_it(self):
        """`> 0`, never truthiness (issue #168).

        `NOT_MEASURED` is `-1`, which is truthy, so a row that measured
        *nothing* counted as an article that carries the thing — inflating
        the article-level denominators the comments cite beside a counter
        total ("387 titles in 94 articles" is two populations, and this is
        the second). It moves the two numbers in *opposite* directions:
        `sum_of` subtracts the sentinel while `articles_where` added one, so
        neither error is self-cancelling and neither looks like a sentinel.
        """
        fresh = sampler.measure_article("PMC1", _article('<fig id="f1"/>'))
        stale_dict = fresh.to_dict()
        for name in sampler._WAITING_SIDE_COUNTERS:
            del stale_dict[name]
        stale = sampler.ArticleMeasurement.from_dict(stale_dict)
        assert stale.refs == sampler.NOT_MEASURED

        totals = sampler.Totals()
        totals.add(stale)

        assert totals.articles_where("refs") == 0
        assert totals.measured("refs") is False

    def test_a_counter_field_is_never_treated_as_a_count(self):
        """`measured` used to raise `TypeError` on all eleven `Counter` fields.

        A `Counter` carries no sentinel — there is no negative dict — so an
        empty one is genuinely measured-and-empty. Comparing it against an
        `int` made the accessor unusable rather than answering, which is why
        no generation could contain one.
        """
        totals = sampler.Totals()
        totals.add(sampler.measure_article("PMC1", _article('<fig id="f1"/>')))

        for name, field in self._fields().items():
            if "Counter" in str(field.type):
                assert totals.measured(name) is True
                assert totals.articles_where(name) == 0


class TestTheCitedPopulationsAreWhatTheCorporaHold:
    """Every number a comment cites has to be re-derivable from the repo.

    This is the property the two committed corpora exist for, and it is not
    self-enforcing: PR #136 redrew both windows and left three figures in
    ``jats_parser.py`` at their pre-redraw values — ``1,556`` captions,
    ``1,416`` direct-child captions, ``71 titles in 32 of 300`` — while
    ``CLAUDE.md``, ``CHANGELOG.md``, ``ROADMAP.md`` and ``docs/manual`` all
    carried the corpus values. They were internally coherent (the cited
    Wilson interval is exactly the one ``32/300`` gives), so nothing looked
    wrong; only summing the JSON found them.

    The precedent is ``test_pdf_metadata_titles.py``, whose header asks for
    numbers "computed rather than written down, so a re-sampled corpus moves
    the reported bound instead of leaving a stale number in a docstring".
    A comment cannot compute, so the next best thing is a test that fails
    when the corpus moves under it.

    Deliberately *not* a test of the parser or the sampler: it asserts only
    that the prose and the data agree. A redraw is meant to break it — that
    is the signal to reconcile the comments, and the failure names the file
    to reconcile.
    """

    RECENT = Path(__file__).resolve().parent / "data" / "jats_exhibits.json"
    BACKFILL = Path(__file__).resolve().parent / "data" / "jats_exhibits.backfill.json"
    RENDITION = Path(__file__).resolve().parent / "data" / "jats_exhibits.rendition.json"

    def _totals(self, path: Path) -> tuple[dict[str, int], dict[str, dict[str, int]], int]:
        """Sum one corpus the way :func:`print_report` does.

        Returns:
            The integer counters, the ``Counter``-backed ones, and the number
            of articles contributing at least one section-renaming title.
        """
        rows = json.loads(path.read_text())["rows"]
        ints: dict[str, int] = {}
        maps: dict[str, dict[str, int]] = {}
        for row in rows:
            for key, value in row.items():
                if key == "unscoped":
                    # Not a population: it is what the *pre-#138* walk would
                    # have said, kept so the correction is measurable. Summing
                    # it into the counters would restore exactly the inflation
                    # the scoping removed.
                    continue
                if isinstance(value, int):
                    ints[key] = ints.get(key, 0) + value
                elif isinstance(value, dict):
                    bucket = maps.setdefault(key, {})
                    for name, count in value.items():
                        bucket[name] = bucket.get(name, 0) + count
        renaming_articles = sum(1 for row in rows if row.get("section_renaming_titles"))
        return ints, maps, renaming_articles

    def _articles_with(self, path: Path, field: str) -> int:
        """How many rows carry a non-zero, non-empty ``field``.

        The article-level denominator several comments cite alongside the
        counter total — "387 titles in 94 articles" is two populations, and
        summing the counter answers only the first.
        """
        rows = json.loads(path.read_text())["rows"]
        return sum(1 for row in rows if row.get(field))

    def test_the_corpora_say_which_artifact_they_were_drawn_from(self):
        """The whole point of the redraw: a reader can re-derive these.

        Before #138 the two corpora were live stratified draws counted back
        from *today*, so "the recent window" named a sample nobody else could
        take. The draw is now `(packages, window, target, seed)` and every one
        of those is in the file. The rendition is here too because it is the
        other half of re-derivability: the identifier list comes from the
        package, the *bytes* come from Europe PMC's `fullTextXML`, and the two
        disagree on exactly the populations cited below — see
        `tests/data/jats_exhibits.rendition.json`.
        """
        for path, first, last, package in (
            (self.RECENT, 2023, 2025, "oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz"),
            (self.BACKFILL, 1996, 1998, "oa_comm_xml.PMC002xxxxxx.baseline.2025-06-26.tar.gz"),
        ):
            corpus = json.loads(path.read_text())
            window = corpus["window"]
            assert window["source"] == "package"
            assert window["packages"] == [package]
            assert (window["first_year"], window["last_year"]) == (first, last)
            assert window["target"] == 1000
            assert window["seed"] == 0
            # The bytes measured, not the bytes drawn from.
            assert window["rendition"] == "europepmc"
            assert (corpus["articles"], corpus["unmeasured"]) == (997, 3)
        # Why the three were unmeasured, in the vocabulary the rendition
        # artifact already used — a bare count cannot be read as permanent
        # (an article Europe PMC does not serve) or transient (a throttled
        # fetch), and those call for different actions. The back-filled
        # corpus predates the field and is asserted as *absent* rather than
        # as `{}`: filling it means re-drawing that window, which would move
        # every back-filled figure for a provenance key, so it is left as
        # the older format deliberately.
        assert json.loads(self.RECENT.read_text())["unmeasured_causes"] == {
            "europepmc_unavailable": 3
        }
        assert "unmeasured_causes" not in json.loads(self.BACKFILL.read_text())

    def test_the_title_owner_population_is_what_the_comments_cite(self):
        """The #125/#130 population, cited in five files.

        The redraw both grew it and widened it. `<caption>` is still the bulk
        (387 of 411), but `<def-list>` and `<fn-group>` appear beside it at 12
        each — two owners no enumeration written from #125 and #130 would have
        held, which is the parent test's whole argument restated on new
        evidence. The back-filled window's `<list>`, which used to be that
        argument's only example, is gone with the draw that held it: this
        window carries no renaming title at all, so the argument now rests on
        the recent one.
        """
        recent, recent_maps, recent_articles = self._totals(self.RECENT)
        backfill, backfill_maps, backfill_articles = self._totals(self.BACKFILL)

        assert sum(recent_maps["section_renaming_titles"].values()) == 411
        assert recent_articles == 104
        assert recent_maps["section_renaming_titles"] == {
            "caption": 387,
            "def-list": 12,
            "fn-group": 12,
        }
        assert sum(backfill_maps.get("section_renaming_titles", {}).values()) == 0
        assert backfill_articles == 0
        assert backfill_maps.get("section_renaming_titles", {}) == {}
        assert recent["captions"] == 8111
        # Not "no exhibit is captioned" but "this window deposits no
        # <caption> at all". Why is inferred rather than counted: 0 tables
        # beside 627 figures and 3,873 `.png` deposits reads as scanned page
        # images, and no counter measures that.
        assert backfill["captions"] == 0

    def test_the_caption_premise_and_its_empty_populations_hold(self):
        """#123's premise measures full; one of its two populations no longer
        measures empty.

        Every draw before this one found **0** nested `<caption>` and every
        `<caption>` inside an exhibit owned by that exhibit. This one finds
        **6** of 8,111 nesting, and 6 owned by a `<supplementary-material>`
        rather than by the exhibit enclosing it — all of them one eLife
        article, `PMC12143881`, which also carries every nested `<fig>` in the
        window. That is #115's and #123's shared premise reproduced from a
        committed corpus for the first time, and it is the house style both
        comments predicted rather than a new one: a figure supplement is a
        `<supplementary-material>` inside the `<fig>` it belongs to, carrying
        its own `<label>` and `<caption>`. The stacks and the owner test are
        what keep those 6 off the enclosing figure.
        """
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert recent["exhibits_with_direct_caption"] == 6938
        assert recent["exhibits_with_descendant_caption"] == 6938
        # Vacuous in this window rather than confirming: 0 of 0.
        assert backfill["exhibits_with_direct_caption"] == 0
        assert backfill["exhibits_with_descendant_caption"] == 0
        assert recent["nested_captions"] == 6
        assert backfill["nested_captions"] == 0
        assert recent_maps["exhibit_caption_owners"] == {
            "fig": 4591,
            "table-wrap": 2347,
            "supplementary-material": 6,
        }
        assert backfill_maps.get("exhibit_caption_owners", {}) == {}

    def test_an_exhibit_with_no_label_of_its_own_is_a_measured_population(self):
        """What this pair of counters supports, and what it does not.

        It used to be asserted as "the premise is violated": 6,937 direct
        against 6,944 carrying one anywhere, read as seven exhibits whose own
        label sits indirectly. `exhibits_with_descendant_label` counts an
        exhibit holding **any** `<label>` in its subtree, so the difference is
        the set a descendant-search fallback would *fire* on, and nothing
        about where an exhibit's own label sits (issue #162). Fetched from
        Europe PMC on 2026-09-02, all seven are a `<table-wrap>` carrying no
        `<label>` and no `<caption>`, and every label below them is a
        `<table-wrap-foot><fn>` marker (`*`, `**`, the empty string) or a
        `<list-item>` bullet inside a cell (`1.`, `-`, `•`) — the two
        containers #116 was about. A descendant search would have corrupted 7
        of 7. Four are deposited under ids their publisher reserves for an
        unnumbered table (`array1`, `array2`, `utbl0001`).

        So the premise is not refuted by this corpus, and it is not confirmed
        by it either: deciding it needs a rule for which descendant label
        would have been "the exhibit's own", which is the rule under test.
        What *is* measured, from `figures + tables` against the direct count,
        is the population a reader feels — 121 exhibits in 83 articles
        carrying no label of their own, which until #162 were rendered with an
        invented number.

        The seven are asserted by name because they are the fixture set for
        the live spot-check above, not because seven is stable: the count was
        7 in both draws on this rendition while the *articles* moved from 4 to
        7.
        """
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert recent["exhibits_with_direct_label"] == 6937
        assert recent["exhibits_with_descendant_label"] == 6944
        # The population #162 acts on: every exhibit the deposit did not number.
        assert recent["figures"] + recent["tables"] == 7058
        assert recent["figures"] + recent["tables"] - recent["exhibits_with_direct_label"] == 121
        rows = json.loads(self.RECENT.read_text())["rows"]
        unlabelled_articles = [
            row["pmcid"]
            for row in rows
            if row["figures"] + row["tables"] > row["exhibits_with_direct_label"]
        ]
        assert len(unlabelled_articles) == 83
        # The seven a descendant-search fallback would fire on — a subset of
        # those 83, and the fixture set for the spot-check in the docstring.
        would_fire = [
            row["pmcid"]
            for row in rows
            if row["exhibits_with_descendant_label"] > row["exhibits_with_direct_label"]
        ]
        assert set(would_fire) <= set(unlabelled_articles)
        assert would_fire == [
            "PMC12011025",
            "PMC12111618",
            "PMC12115352",
            "PMC12149983",
            "PMC12154067",
            "PMC12159547",
            "PMC12177175",
        ]
        # The back-filled window numbers every exhibit it deposits, so it
        # contributes nothing to #162's population.
        assert backfill["exhibits_with_direct_label"] == 627
        assert backfill["exhibits_with_descendant_label"] == 627
        assert backfill["figures"] + backfill["tables"] == 627
        assert recent_maps["label_parents"] == {
            "fig": 4593,
            "table-wrap": 2344,
            "fn": 330,
            "list-item": 225,
            "supplementary-material": 6,
        }
        assert backfill_maps["label_parents"] == {"fig": 627}

    def test_the_graphic_populations_are_what_offer_graphic_cites(self):
        """#117's shares and #127's two renditions, both cited as percentages."""
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert (recent["figures_with_graphic"], backfill["figures_with_graphic"]) == (4602, 627)
        assert (recent["figures_multi_graphic"], backfill["figures_multi_graphic"]) == (2676, 276)
        assert (recent["last_is_thumb"], backfill["last_is_thumb"]) == (2639, 276)
        assert (recent["first_is_thumb"], backfill["first_is_thumb"]) == (0, 0)
        assert recent["graphics"] + backfill["graphics"] == 13617
        assert recent["alternatives_members"] + backfill["alternatives_members"] == 7055
        assert recent["alternatives_declaring_mime"] + backfill["alternatives_declaring_mime"] == 0
        assert recent["alternatives_archival"] + backfill["alternatives_archival"] == 0
        # Every deposit in both windows names an extension, so the
        # `_ARCHIVAL_EXTENSIONS` fallback always has something to read. That
        # is a property of the *served* rendition and not a publisher habit:
        # `graphic_extensions` disagrees in 272 of 300 compared articles, and
        # on the archive side of those it records 1,262 extensionless hrefs of
        # 2,046 deposits in 241 articles — all four pinned in
        # `test_the_rendition_gap_is_what_its_own_evidence_file_holds`. Read
        # that at that scope: the delta file records only disagreements, so it
        # says nothing about the 24 articles that agree, and no single
        # mechanism accounts for the gap.
        assert set(recent_maps["graphic_extensions"]) == {".jpg", ".gif"}
        assert set(backfill_maps["graphic_extensions"]) == {".png", ".jpg", ".gif"}
        # The `.png` count `jats_parser.py` and CLAUDE.md both read as
        # "scanned page images". It is an *inference* from a count, so the
        # count at least has to be the corpus's — it was cited in four files
        # and asserted in none.
        assert backfill_maps["graphic_extensions"][".png"] == 3873

    def test_the_table_side_answers_135_as_an_empty_population(self):
        """#135, and the #127 window neither corpus can reproduce any more.

        The back-filled window used to hold #127's whole image-only-table
        population — 11 of 93 tables in 2 articles. It holds **no
        `<table-wrap>` at all** now: `oa_comm`'s 1996-1998 material is scanned
        page images with no tabular markup. That 0 is the absence of a
        denominator, not a measurement of the population, and #127's evidence
        is historical from here on.

        #115's nesting population, empty in every draw before this one, is
        **not** empty here: 7 nested `<fig>` and 0 nested `<table-wrap>`, all
        7 in one eLife article (`PMC12143881`). So the stack that #115
        installed is exercised by a committed corpus rather than only argued
        for — and by exactly the publisher the comment names.
        """
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert recent["tables"] + backfill["tables"] == 2448
        assert recent["tables_with_graphic"] + backfill["tables_with_graphic"] == 92
        assert recent["tables_multi_graphic"] + backfill["tables_multi_graphic"] == 0
        assert (recent["tables"], recent["tables_image_only"]) == (2448, 8)
        assert (backfill["tables"], backfill["tables_image_only"]) == (0, 0)
        assert (recent["tables_with_both"], backfill["tables_with_both"]) == (84, 0)
        assert sum(recent_maps["foreign_owned_graphics"].values()) == 153
        assert recent_maps["foreign_owned_graphics"] == {
            "td": 82,
            "inline-formula": 69,
            "disp-formula": 2,
        }
        assert self._articles_with(self.RECENT, "foreign_owned_graphics") == 12
        assert backfill_maps.get("foreign_owned_graphics", {}) == {}
        assert (recent["nested_figures"], recent["nested_tables"]) == (7, 0)
        assert backfill["nested_figures"] + backfill["nested_tables"] == 0

    def test_the_nested_article_scoping_is_what_the_corpora_record(self):
        """#138's own population, and the one #158 cites four ways.

        "Carries a region" and "loses body text to one" are different claims,
        the first bounding the second: 145 regions in 29 of 997 recent
        articles is what the *walk* now excludes, and every one of those 29
        rows carries an `unscoped` entry, so the scoping moved a counter for
        all of them. The back-filled window has none, peer review not having
        been deposited that way in 1996-1998.
        """
        recent, _, _ = self._totals(self.RECENT)
        backfill, _, _ = self._totals(self.BACKFILL)

        assert recent["nested_article_regions"] == 145
        assert self._articles_with(self.RECENT, "nested_article_regions") == 29
        assert self._articles_with(self.RECENT, "unscoped") == 29
        assert backfill["nested_article_regions"] == 0
        assert self._articles_with(self.BACKFILL, "unscoped") == 0

    def test_the_four_waiting_issues_now_have_a_population(self):
        """#142, #143, #147 and #150 — measured, and three of them empty.

        Each counter was added ahead of the redraw so the rule it belongs to
        would be *decidable*; the measurement does not decide it, which is why
        all four issues stay open. Three measure empty on both windows and one
        does not: `<tex-math>` and `<disp-formula>` are a live population, and
        1,087 `<alternatives>` holding both a MathML and a TeX encoding is
        what rules out adding `<tex-math>` to `_INLINE_ELEMENTS` (#147).

        **#150's own population is empty in this draw and was not in the
        last** — 0 `<ref>` carrying only a `<note>`, against 2 before, while
        one `<ref>` still carries a `<note>` beside other children. So the
        issue is unreached by this corpus rather than answered by it, and the
        `note` key is asserted so its presence stays distinguishable from a
        counter that never fired.
        """
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        # #142 — a <collab>'s element children.
        assert recent["collabs_with_element_children"] == 0
        assert backfill["collabs_with_element_children"] == 0
        assert recent_maps.get("collab_children", {}) == {}
        assert backfill_maps.get("collab_children", {}) == {}
        # #143 — contributor multiplicity per <contrib>.
        for totals in (recent, backfill):
            assert totals["contribs_multi_collab"] == 0
            assert totals["contribs_multi_string_name"] == 0
            assert totals["name_alternatives"] == 0
            assert totals["collab_alternatives"] == 0
        # #147 — formulas. The one non-empty population of the four.
        assert (recent["disp_formulas"], backfill["disp_formulas"]) == (1915, 0)
        assert (recent["disp_formulas_with_label"], backfill["disp_formulas_with_label"]) == (
            1459,
            0,
        )
        assert (recent["disp_formulas_image_only"], backfill["disp_formulas_image_only"]) == (
            140,
            0,
        )
        assert (recent["inline_formulas"], backfill["inline_formulas"]) == (9221, 0)
        assert (recent["tex_math"], backfill["tex_math"]) == (1398, 0)
        assert (recent["mml_math"], backfill["mml_math"]) == (10202, 0)
        assert (recent["formula_alternatives_both"], backfill["formula_alternatives_both"]) == (
            1087,
            0,
        )
        # #150 — a <ref> carrying only a <note>.
        assert (recent["refs"], backfill["refs"]) == (52969, 19447)
        assert (recent["refs_note_only"], backfill["refs_note_only"]) == (0, 0)
        assert recent_maps["ref_child_kinds"]["note"] == 1
        assert recent_maps["ref_child_kinds"]["citation-alternatives"] == 2709
        assert "citation-alternatives" not in backfill_maps["ref_child_kinds"]

    def test_the_contributor_counters_are_what_120_and_140_wait_on(self):
        """#120/#140's spellings, sized for the first time (section 11).

        The rate quoted for both issues has always been #120's own 34 of
        1,025 — a count of `<contrib>` elements carrying no `<surname>`, which
        is a set *both* spellings share and so a rate for neither. These are
        the per-spelling counts, scoped the way the parser routes: `<collab>`
        names 14 contributors in the recent window and `<string-name>` names
        none at all.

        `<collab>` **carrying a roster** — the `<contrib-group>` of a
        consortium's own members that #120 argues the `<contrib>` stack for —
        measures **0** in both windows here, where the previous draw held 1.
        It is a population this corpus does not reach, not one it refutes;
        the same goes for `<contrib>` nested inside another, 20 then and 0
        now.

        **The `<string-name>` zero is measured, not missing**, and the
        distinction is the load-bearing one in this repo. Section 11's
        vocabulary is *open*: every child of a `<contrib>` is counted under
        its own name unless it is in `_CONTRIB_NON_NAME_CHILDREN` (skipped) or
        `_CONTRIB_NAME_WRAPPERS` (descended through), and `string-name` is in
        neither — so one occurrence anywhere in either window would have
        printed under its own key. An absent key is therefore 0 of 12,650
        `<contrib>`, upper bound 0.03%, which is what `CLAUDE.md`,
        `CHANGELOG.md` and `ROADMAP.md` all say. That openness is exactly what
        #121 argued for: against a closed list the unforeseen spelling falls
        into `(none)` and reads as a contributor naming nobody, which is a
        missing measurement wearing a measurement's clothes.
        """
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert (recent["contribs"], backfill["contribs"]) == (7798, 4852)
        assert recent_maps["contrib_name_spellings"] == {
            "name": 7784,
            "contrib-id": 823,
            "degrees": 276,
            "collab": 14,
            "ext-link": 1,
        }
        assert backfill_maps["contrib_name_spellings"] == {"name": 4852}
        assert "string-name" not in recent_maps["contrib_name_spellings"]
        assert (recent["nested_contribs"], backfill["nested_contribs"]) == (0, 0)
        assert (recent["collabs_with_a_roster"], backfill["collabs_with_a_roster"]) == (0, 0)
        assert (
            recent["articles_losing_every_author"],
            backfill["articles_losing_every_author"],
        ) == (2, 0)

    def test_the_rendition_gap_is_what_its_own_evidence_file_holds(self):
        """The claim licensing the whole redraw, answerable to its artifact.

        The corpora above are drawn from a package and measured from Europe
        PMC, and the reason is that the two renditions disagree. That reason
        is cited in seven files and, until this test, was pinned by nothing —
        `jats_exhibits.rendition.json` was committed as evidence and never
        read back. It went wrong immediately: the first account of it summed
        the deltas and reported the sum as an *archive total*, which the file
        cannot support.

        **A delta file is not a corpus.** `rendition_delta` records a field
        only where the two renditions disagree, so an agreeing article
        contributes to neither side and never appears; a sum over
        `deltas` is a sum over disagreements. The only honest form of the
        headline number is "differs in N of 300, and where it differs archive
        A against served B", and that is what this asserts.

        **The provenance is asserted at the width it can move.** This used to
        check `source`, `first_year` and `last_year` alone — the three fields
        that did *not* move when `_package_identity` and `corpus_rendition`
        were added — so the artifact went stale under it: it kept recording
        `"packages": ["PMC012xxxxxx"]`, the bare directory basename
        `_package_identity` exists to stop recording, and carried no
        `corpus_rendition` at all, leaving a reader unable to tell which
        rendition the corpus beside it had been measured in. The payload was
        sound the whole time, which is exactly why nothing caught it.
        """
        report = json.loads(self.RENDITION.read_text())
        comparison = report["comparison"]

        assert (report["source"], report["first_year"], report["last_year"]) == (
            "package",
            2023,
            2025,
        )
        # The public artifact name, not a directory basename — the same
        # identity the corpora themselves record, and what makes this
        # comparison's own sample re-derivable.
        assert report["packages"] == ["oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz"]
        assert (report["target"], report["seed"]) == (1000, 0)
        assert (report["requested"], report["held"]) == (300, 300)
        # Which rendition the *corpus* this run also drew was measured from —
        # deliberately renamed on the way in, because the comparison beside it
        # is always archive-against-served whatever the corpus was.
        assert report["corpus_rendition"] == "europepmc"
        assert comparison["compared"] == 300
        assert comparison["unmeasured"] == 0
        assert comparison["articles_differing"] == 289

        deltas = comparison["deltas"]
        differing = comparison["fields_differing"]

        def where_they_differ(field: str) -> tuple[int, int, int]:
            """``(articles differing, archive sum, served sum)`` — over those alone."""
            rows = [row[field] for row in deltas.values() if field in row]
            return (
                len(rows),
                sum(side["archive"] for side in rows),
                sum(side["europepmc"] for side in rows),
            )

        # The headline: #117's ranking rule is unreachable on archive bytes.
        assert where_they_differ("last_is_thumb") == (156, 0, 781)
        assert differing["last_is_thumb"] == 156

        # The counter-example that retired the mechanism sentence —
        # `PMC12169732`, whose thumbnails are the *archive's* own, spelled
        # `specific-use="thumbnail"` where Europe PMC re-labels to
        # `content-type="thumb"`, so both renditions measure 4 and only the
        # spelling moves — **is not in this artifact and is not asserted
        # here.** It was drawn into the previous held sample and out of this
        # one, so it is now a live spot-check (reproduced 2026-09-02: archive
        # `{'thumbnail': 4}` against served `{'image': 4, 'thumb': 4}`, with
        # `last_is_thumb` 4 on both sides), cited as one wherever it appears.
        #
        # What this file *can* say is that the archive-side spelling appears
        # nowhere in these 300 — which is the weaker claim, and the one to
        # make. It does not license "the archive deposits one bare `…-g001`
        # per figure": `rendition_delta` records only disagreements, so the
        # 11 agreeing articles contribute nothing either way, and an archive
        # thumbnail spelled the same as the served one would also be absent
        # from this map. Absence here is absence *of a recorded
        # disagreement*, never absence from the corpus.
        archive_thumbs = sum(
            row["specific_use_values"]["archive"].get("thumbnail", 0)
            + row["content_type_values"]["archive"].get("thumb", 0)
            for row in deltas.values()
            if "specific_use_values" in row and "content_type_values" in row
        )
        assert archive_thumbs == 0
        assert all(
            row["last_is_thumb"]["archive"] == 0
            for row in deltas.values()
            if "last_is_thumb" in row
        )

        # #158: the served rendition *adds* nested-article regions, so the
        # sampler's 2.5% and transparency's 3.45% are not one measurement.
        assert where_they_differ("nested_article_regions") == (5, 27, 32)

        # The extension population `_ARCHIVAL_EXTENSIONS` reads. All four
        # numbers `jats_parser.py` cites from this file are derived here —
        # "1,161 extensionless of 1,733 deposits, in 241 articles" used to
        # have only its first term pinned, and 241 appeared in no assertion
        # anywhere in the repo. A comment claiming a net exists is worse than
        # no comment.
        extension_rows = [
            row["graphic_extensions"] for row in deltas.values() if "graphic_extensions" in row
        ]
        extensionless = sum(row["archive"].get("(none)", 0) for row in extension_rows)
        deposits = sum(sum(row["archive"].values()) for row in extension_rows)
        articles_with_one = sum(1 for row in extension_rows if row["archive"].get("(none)", 0))
        assert differing["graphic_extensions"] == 272
        assert (extensionless, deposits, articles_with_one) == (1262, 2046, 241)


class TestReadingABaselinePackage:
    """The offline source's data layer — one article at a time, whole."""

    def _write_tarball(self, tmp_path, members: dict[str, bytes]):
        import tarfile

        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(f"PMC000xxxxxx/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def test_a_directory_yields_every_article(self, tmp_path):
        (tmp_path / "PMC1.xml").write_bytes(b"<article/>")
        (tmp_path / "PMC2.xml").write_bytes(b"<article/>")
        (tmp_path / "notes.txt").write_bytes(b"ignored")

        found = dict(sampler.iter_package_articles(tmp_path))

        assert sorted(found) == ["PMC1", "PMC2"]

    def test_a_tarball_yields_every_article_without_unpacking(self, tmp_path):
        path = self._write_tarball(tmp_path, {"PMC7.xml": b"<article/>"})

        found = dict(sampler.iter_package_articles(path))

        assert list(found) == ["PMC7"]
        assert not (tmp_path / "PMC000xxxxxx").exists()

    def test_something_that_is_neither_is_refused(self, tmp_path):
        stray = tmp_path / "stray.xml"
        stray.write_bytes(b"<article/>")

        with pytest.raises(sampler.PackageError, match="stray.xml"):
            list(sampler.iter_package_articles(stray))

    def test_an_uncompressed_tar_is_refused_like_any_other_non_package(self, tmp_path):
        """`tarfile.is_tarfile()` accepts an uncompressed `.tar` — it really
        is a tarball — but the `"r|gz"` open mode this module always uses
        cannot read one. `_is_package_path` is this function's very first
        check, and it is false here (no gzip magic bytes), so an
        uncompressed `.tar` never reaches `tarfile.open` at all — it is
        refused up front, with the same message as any other non-package
        path."""
        import tarfile

        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar"
        with tarfile.open(path, "w") as tar:
            data = b"<article/>"
            info = tarfile.TarInfo("PMC000xxxxxx/PMC1.xml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        with pytest.raises(sampler.PackageError, match="gzip-compressed tarball"):
            list(sampler.iter_package_articles(path))

    def test_gzip_compressed_content_that_is_not_a_tarball_is_a_package_error(self, tmp_path):
        """The complementary case `_is_gzip_file` cannot catch: real gzip
        magic bytes, but the decompressed stream is not a tar archive. This
        is the one shape that still reaches `tarfile.open` and has to be
        converted from `tarfile.ReadError` to `PackageError` there."""
        import gzip

        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        path.write_bytes(gzip.compress(b"not a tar file at all, just some bytes"))

        with pytest.raises(sampler.PackageError, match="gzip-compressed but not a tarball"):
            list(sampler.iter_package_articles(path))

    def test_both_callers_route_through_the_shared_predicate(self, tmp_path, monkeypatch):
        """Proof, not inference, that the draw's own guard and `_validate_args`
        both call the shared predicate rather than a re-inlined copy of it
        (issue 138, fix round 3): patch `_package_path_refusal` to refuse
        everything and a path that would otherwise succeed must fail in both
        places. A guard that silently re-derives the same disjunction instead
        of calling this function — exactly the shape round 2 left in place —
        would ignore the patch and keep succeeding, which is what would make
        this test catch it.

        Both callers, not just the draw: `_validate_args` is the one that
        turns the condition into a one-line refusal, and the two disagreeing
        is what let a gzipped non-tar past validation and into an uncaught
        `PackageError` (issue #165).
        """
        (tmp_path / "PMC1.xml").write_bytes(b"<article/>")
        assert list(sampler.iter_package_articles(tmp_path)) == [("PMC1", b"<article/>")]
        assert (
            sampler._validate_args(
                _package_run_args(package=[tmp_path], from_year=2023, to_year=2025)
            )
            is None
        )

        monkeypatch.setattr(sampler, "_package_path_refusal", lambda path: f"{path} refused")

        with pytest.raises(sampler.PackageError):
            list(sampler.iter_package_articles(tmp_path))
        assert (
            sampler._validate_args(
                _package_run_args(package=[tmp_path], from_year=2023, to_year=2025)
            )
            is not None
        )

    def test_an_undated_article_is_named_rather_than_silently_dropped(self, capsys, tmp_path):
        """The accounting the `_YEAR_RE` fix did not add.

        An undated article is *undrawable* — a draw is by year — so dropping
        it is the only option; saying nothing about it is not. That silence
        is the exact shape the last cause took: absent from the pool, never
        counted as unmeasured, exit 0, and publisher-clustered rather than
        noise. `<pub-date>` may legally carry a `<string-date>` and no
        `<year>` at all, so the input is not hypothetical even though the
        population measures 0 of 220,485 today.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "PMC1.xml").write_bytes(
            b"<article><front><article-meta>"
            b"<pub-date pub-type='epub'><year>2024</year></pub-date>"
            b"</article-meta></front></article>"
        )
        (package / "PMC2.xml").write_bytes(
            b"<article><front><article-meta>"
            b"<pub-date pub-type='epub'><string-date>Spring 2024</string-date></pub-date>"
            b"</article-meta></front></article>"
        )

        assert sampler.package_candidates([package], 2023, 2025) == ["PMC1"]

        err = capsys.readouterr().err
        assert "PMC2" in err
        assert "undrawable" in err

    def test_a_fully_dated_package_says_nothing(self, capsys, tmp_path):
        """The negative control: the report must not fire on every run.

        A warning printed by an ordinary draw is one a reader learns to skip,
        which would cost exactly the signal this exists to give.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "PMC1.xml").write_bytes(
            b"<article><front><article-meta>"
            b"<pub-date pub-type='epub'><year>2024</year></pub-date>"
            b"</article-meta></front></article>"
        )

        assert sampler.package_candidates([package], 2023, 2025) == ["PMC1"]
        assert capsys.readouterr().err == ""

    def test_a_package_read_as_a_directory_and_as_a_tarball_names_one_corpus(self, tmp_path):
        """One artifact, two spellings, and they must draw the same corpus.

        The directory branch globbed non-recursively while the tar branch
        walks members at any depth, so the same package unpacked and packed
        yielded different candidate sets — and therefore a different
        `draw()`, under the same `(packages, window, target, seed)`. That is
        the reproducibility claim #138 and #132 are built on: a reader
        re-deriving the committed corpora downloads the *tarball* while the
        corpora were drawn from an unpacked *directory*.

        The local mirror happens to be flat, so nothing in the committed
        evidence moves — which is exactly why this needed a test rather than
        an inspection.
        """
        import tarfile

        package = tmp_path / "pkg"
        (package / "sub").mkdir(parents=True)
        dated = (
            b"<article><front><article-meta>"
            b"<pub-date pub-type='epub'><year>2024</year></pub-date>"
            b"</article-meta></front></article>"
        )
        (package / "PMC1.xml").write_bytes(dated)
        (package / "sub" / "PMC2.xml").write_bytes(dated)

        tarball = tmp_path / "pkg.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(package / "PMC1.xml", arcname="pkg/PMC1.xml")
            tar.add(package / "sub" / "PMC2.xml", arcname="pkg/sub/PMC2.xml")

        assert sorted(i for i, _ in sampler.iter_package_articles(package)) == ["PMC1", "PMC2"]
        assert sampler.package_candidates([package], 2023, 2025) == sampler.package_candidates(
            [tarball], 2023, 2025
        )
        assert sampler.draw(
            sampler.package_candidates([package], 2023, 2025), 1, 0
        ) == sampler.draw(sampler.package_candidates([tarball], 2023, 2025), 1, 0)

    def test_is_gzip_file_treats_an_unreadable_path_as_not_gzip(self, tmp_path):
        """The `except OSError: return False` branch, exercised rather than
        only reasoned about: a path that does not exist raises
        `FileNotFoundError` (an `OSError`) on `.open()`, and that must read
        as "not gzip," not propagate — the caller's own attempt to read the
        path is what should report a vanished or unreadable file."""
        missing = tmp_path / "does-not-exist"

        assert sampler._is_gzip_file(missing) is False

    def test_the_year_is_the_earliest_any_pub_date_declares(self):
        xml = b"""<article><front><article-meta>
            <pub-date pub-type="epub"><year>2024</year></pub-date>
            <pub-date pub-type="ppub"><year>2023</year></pub-date>
            </article-meta></front></article>"""

        assert sampler.article_year(xml) == 2023

    def test_the_kind_of_date_is_not_consulted(self):
        """Measured, not assumed: excluding deposit and submission kinds
        changes the earliest year in 0 of 3,000 articles in each window, and
        the attribute spelling is not one vocabulary — `pub-type="ppub"`
        dominates the back-filled range, `pub-type="epub"` the recent one, and
        JATS 1.x writes `date-type="pub" publication-format="electronic"`."""
        xml = b"""<article><front><article-meta>
            <pub-date date-type="pub" publication-format="electronic">
              <year>2019</year></pub-date>
            </article-meta></front></article>"""

        assert sampler.article_year(xml) == 2019

    def test_a_year_carrying_attributes_is_still_a_year(self):
        """The regression the final whole-branch review found.

        `<year>` legally carries `@iso-8601-date`, `@calendar` and
        `@content-type`, and the pattern used to require a bare open tag — so
        an attributed one made the article *undated*, which makes it
        undrawable: absent from the candidate pool, never counted as
        unmeasured, exit 0. Measured over `PMC012xxxxxx`: 17 of 97,909, every
        one of them `<year iso-8601-date="2025">2025</year>`, every one inside
        the recent window, and **14 of the 17 one contiguous journal block**
        (PMC12085917-PMC12085930) — publisher-clustered, so bias rather than
        noise, which is exactly what the whole-member read is required for one
        function up.
        """
        xml = b"""<article><front><article-meta>
            <pub-date pub-type="epub" date-type="pub">
              <year iso-8601-date="2025">2025</year></pub-date>
            </article-meta></front></article>"""

        assert sampler.article_year(xml) == 2025

    def test_the_element_name_has_to_be_the_whole_name(self):
        """`<year[^>]*>` is the obvious widening and it is too wide: it accepts
        any element whose name merely *starts* with `year`. The open tag has to
        end at a `>` or continue with whitespace."""
        xml = b"<article><front><pub-date><yearly>2024</yearly></pub-date></front></article>"

        assert sampler.article_year(xml) is None

    def test_a_malformed_five_digit_year_is_not_read_as_four(self):
        """What the dropped `</year>` anchor used to buy, kept explicitly: a
        `(?!\\d)` boundary, so `20255` is refused rather than reported as
        2025."""
        xml = b"<article><front><pub-date><year>20255</year></pub-date></front></article>"

        assert sampler.article_year(xml) is None

    def test_an_article_with_no_pub_date_has_no_year(self):
        assert sampler.article_year(b"<article><front/></article>") is None

    def test_a_year_outside_a_pub_date_is_not_a_publication_year(self):
        """A `<year>` in a reference is not this article's date."""
        xml = b"<article><back><ref><year>1999</year></ref></back></article>"

        assert sampler.article_year(xml) is None

    def test_a_pub_date_does_not_reach_forward_into_a_reference(self):
        """The defect that produced the first draw's articles "published" in 1861.

        The test above cannot catch it: with no `<pub-date>` in the document
        at all, a lazy `<pub-date[^>]*>(.*?)` matching forward to the next
        `<year>` anywhere finds no match either and returns `None` for the
        wrong reason. The fixture that distinguishes the two needs a
        `<pub-date>` that carries **no** `<year>` — JATS models the element
        as `((day?, month?, year, era?) | (season, year) | string-date)`, so
        the `<string-date>` arm is an ordinary deposit, not a contrivance —
        followed by a `<ref>` that has one.
        """
        xml = (
            b"<article><front><article-meta>"
            b"<pub-date pub-type='epub'><string-date>March 2024</string-date></pub-date>"
            b"</article-meta></front>"
            b"<back><ref><year>1861</year></ref></back></article>"
        )

        assert sampler.article_year(xml) is None

    def test_a_real_pub_date_is_still_read_when_a_reference_follows_it(self):
        """The negative control for the test above.

        A regex anchored so tightly that it stopped matching real dates would
        pass that test by finding nothing anywhere, and empty both corpora.
        """
        xml = (
            b"<article><front><article-meta>"
            b"<pub-date pub-type='epub'><year>2024</year></pub-date>"
            b"</article-meta></front>"
            b"<back><ref><year>1861</year></ref></back></article>"
        )

        assert sampler.article_year(xml) == 2024

    def test_the_whole_member_is_read_not_a_prefix(self):
        """The guard against the optimisation someone will reach for later.

        A prefix read is 49% faster, raises nothing, and finds no date for
        379 of 2,000 recent articles at 8 KB — a miss that tracks front-matter
        size, so it tracks publisher, which is the axis every population here
        varies along. Note the evidence is the *recent* window: on the
        back-filled one a prefix read misses nothing (3,141 either way over the
        whole of `PMC002xxxxxx`), so a rule drawn from that window alone would
        license the optimisation that costs a fifth of the other.
        """
        padding = b"<aff>" + b"x" * 20000 + b"</aff>"
        xml = (
            b"<article><front><article-meta>"
            + padding
            + b"<pub-date pub-type='epub'><year>2001</year></pub-date>"
            + b"</article-meta></front></article>"
        )

        assert sampler.article_year(xml) == 2001


class TestThePackageIdentityIsRecorded:
    """`_package_identity` — Task 6a Step 4, hardened in review round 1.

    The recent corpus's header used to read ``packages: ['PMC012xxxxxx']``: a
    directory's own basename, which is what a plain extraction of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` leaves on disk —
    losing the subset prefix and the dated snapshot that make the draw
    re-derivable by another reader. Nothing inside the extracted files can
    recover that identity (no manifest, no embedded date), so the only place
    it can still be found is the tarball's own filename, if that tarball
    still sits beside the directory it was extracted into — and even then,
    only after confirming the two actually correspond (a name match alone
    is not proof: two OA subsets partition one accession range into
    disjoint articles, so an unrelated tarball can still match by name).
    """

    def _write_tarball(self, path: Path, members: dict[str, bytes]) -> Path:
        """A real, minimal, valid gzip tarball — never a magic-bytes stub.

        `_package_identity`'s content check opens a candidate sibling with
        `iter_package_articles`, which really parses it as a tar stream; a
        fixture that is only the two gzip magic bytes plus filler used to
        pass when the identity check was name-only, but now raises
        `tarfile.CompressionError` (a real, if malformed, gzip file that
        `_package_identity` must survive, not crash on) rather than
        confirming anything.
        """
        import tarfile as _tarfile

        with _tarfile.open(path, "w:gz") as tar:
            for name, data in members.items():
                info = _tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def test_a_tarball_reports_its_own_name(self, tmp_path):
        """A tarball's filename already carries the whole identity; nothing
        to recover."""
        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        path.write_bytes(b"\x1f\x8b" + b"0" * 10)

        assert sampler._package_identity(path) == path.name

    def test_a_directory_recovers_its_sibling_tarballs_identity(self, tmp_path):
        """The exact shape the recent corpus's own package was laid out in:
        a directory and the tarball it was extracted from, side by side —
        and the directory holds the tarball's own first article, which is
        what the content check confirms."""
        directory = tmp_path / "PMC012xxxxxx"
        directory.mkdir()
        (directory / "PMC12000001.xml").write_bytes(b"<article/>")
        sibling = tmp_path / "oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz"
        self._write_tarball(sibling, {"PMC12000001.xml": b"<article/>"})

        assert sampler._package_identity(directory) == sibling.name

    def test_a_directory_with_no_sibling_falls_back_to_its_bare_name(self, tmp_path, capsys):
        """No sibling tarball exists — nothing to recover the lost identity
        from, so the bare name is reported rather than a guess, and the
        fallback is reported rather than silent."""
        directory = tmp_path / "PMC000xxxxxx"
        directory.mkdir()

        assert sampler._package_identity(directory) == "PMC000xxxxxx"
        assert "no sibling" in capsys.readouterr().err

    def test_an_ambiguous_sibling_match_falls_back_rather_than_guesses(self, tmp_path, capsys):
        """Two candidate tarballs (different subsets, both naming this
        directory and a baseline snapshot) must not be resolved by picking
        either one — a wrong guess names the wrong snapshot, which is worse
        than an incomplete identity that names none. Fake bytes are enough
        here: the ambiguity is caught by the name-match count alone, before
        either candidate's content is ever read."""
        directory = tmp_path / "PMC001xxxxxx"
        directory.mkdir()
        (tmp_path / "oa_comm_xml.PMC001xxxxxx.baseline.2025-06-26.tar.gz").write_bytes(
            b"\x1f\x8b" + b"0" * 10
        )
        (tmp_path / "oa_noncomm_xml.PMC001xxxxxx.baseline.2025-01-01.tar.gz").write_bytes(
            b"\x1f\x8b" + b"0" * 10
        )

        assert sampler._package_identity(directory) == "PMC001xxxxxx"
        # Not `"ambiguous" in ...`: review round 2 found that bare substring
        # is also a literal fragment of pytest's own generated `tmp_path`
        # directory name for *this test's own name*
        # (`.../test_an_ambiguous_sibling_matc0/...`), so it would pass even
        # if the code printed the wrong (no-candidate) message here verbatim
        # — which is exactly the confusion MINOR 4 was about. This phrase is
        # unique to the ambiguous branch's own message and cannot leak in
        # from the path.
        assert "candidate baseline tarballs match" in capsys.readouterr().err

    def test_a_non_gzip_sibling_naming_the_same_baseline_is_not_mistaken_for_the_tarball(
        self, tmp_path
    ):
        """A PMC baseline distribution also ships a plaintext file list beside
        the tarball (`*.filelist.csv`) that names the same directory and the
        same word "baseline" — exactly the kind of sibling that must not be
        picked in the tarball's place. Filtering on the gzip magic bytes,
        not merely the name pattern, is what tells the two apart."""
        directory = tmp_path / "PMC000xxxxxx"
        directory.mkdir()
        (directory / "PMC1.xml").write_bytes(b"<article/>")
        (tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.filelist.csv").write_text(
            "not gzip, just a file list"
        )
        tarball = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        self._write_tarball(tarball, {"PMC1.xml": b"<article/>"})

        assert sampler._package_identity(directory) == tarball.name

    def test_a_sibling_that_is_the_wrong_subset_is_not_mistaken_for_the_real_one(
        self, tmp_path, capsys
    ):
        """MINOR 3 from review round 1: a lone, unambiguous, gzip-compressed
        sibling can still be the *wrong* tarball — two OA subsets
        (`oa_comm_xml`, `oa_noncomm_xml`) partition one accession range into
        disjoint articles, so a directory genuinely extracted from one
        subset can still have exactly one gzip sibling from the *other*,
        with no comm tarball present at all. The name match alone would
        have recorded that wrong identity; the content check (does the
        sibling's first article exist in this directory?) catches it,
        because the two subsets never share an article."""
        directory = tmp_path / "PMC001xxxxxx"
        directory.mkdir()
        # This directory's own articles — genuinely from a noncomm draw.
        (directory / "PMC10000002.xml").write_bytes(b"<article/>")
        # The only sibling on disk is the *comm* tarball for the same
        # accession range, naming different articles entirely.
        wrong_sibling = tmp_path / "oa_comm_xml.PMC001xxxxxx.baseline.2025-06-26.tar.gz"
        self._write_tarball(wrong_sibling, {"PMC10000001.xml": b"<article/>"})

        assert sampler._package_identity(directory) == "PMC001xxxxxx"
        err = capsys.readouterr().err
        assert "does not look like" in err

    def test_an_unreadable_sibling_is_not_mistaken_for_the_real_one(self, tmp_path, capsys):
        """A sibling that passes the gzip-magic-bytes filter but is not
        actually a readable tar stream (truncated, corrupted) must not
        crash `_package_identity` — the broad `tarfile.TarError` catch is
        what turns a real download's corruption into a safe fallback rather
        than an unhandled exception out of `main()`."""
        directory = tmp_path / "PMC000xxxxxx"
        directory.mkdir()
        (directory / "PMC1.xml").write_bytes(b"<article/>")
        corrupt = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        corrupt.write_bytes(b"\x1f\x8b" + b"0" * 10)  # gzip magic, garbage after

        assert sampler._package_identity(directory) == "PMC000xxxxxx"
        assert "does not look like" in capsys.readouterr().err

    def test_a_glob_metacharacter_in_the_directory_name_is_matched_literally(self, tmp_path):
        """MINOR 5 from review round 1: `path.name` is interpolated into a
        glob pattern. Unescaped, a directory named with bracket characters
        would have them read as a glob character class instead of literal
        text, so a real, correctly-named sibling would go unmatched. PMC's
        own accession-range names never contain these characters — this is
        a synthetic worst case, not a realistic package name — but the
        escaping code path still has to be exercised and correct."""
        directory = tmp_path / "PMC[0]xxxxxx"
        directory.mkdir()
        (directory / "PMC1.xml").write_bytes(b"<article/>")
        sibling = tmp_path / "oa_comm_xml.PMC[0]xxxxxx.baseline.2025-06-26.tar.gz"
        self._write_tarball(sibling, {"PMC1.xml": b"<article/>"})

        assert sampler._package_identity(directory) == sibling.name

    def test_the_recorded_window_uses_the_recovered_identity(self, tmp_path):
        """Wired into `main()`, not merely a standalone helper: a package
        draw against a bare directory records the sibling tarball's name in
        the corpus header, not the directory's own basename."""
        directory = tmp_path / "package" / "PMC000xxxxxx"
        directory.mkdir(parents=True)
        (directory / "PMC1.xml").write_text(
            "<article><front><article-meta>"
            "<pub-date pub-type='epub'><year>2024</year></pub-date>"
            "</article-meta></front></article>"
        )
        sibling = tmp_path / "package" / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        self._write_tarball(sibling, {"PMC1.xml": b"<article/>"})
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(directory),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "1",
            "-o",
            str(output),
        ]

        with mock.patch.object(sys, "argv", argv):
            code = sampler.main()

        assert code == 0
        written = json.loads(output.read_text())
        assert written["window"]["packages"] == [sibling.name]


class TestDrawingFromAPackage:
    """Selection: in-window, deterministic, and reproducible from the header."""

    def _package(self, tmp_path, years: dict[str, int | None]):
        for pmcid, year in years.items():
            date = (
                f"<pub-date pub-type='epub'><year>{year}</year></pub-date>"
                if year is not None
                else ""
            )
            (tmp_path / f"{pmcid}.xml").write_text(
                f"<article><front><article-meta>{date}</article-meta></front></article>"
            )
        return tmp_path

    def test_only_articles_inside_the_window_are_candidates(self, tmp_path):
        path = self._package(
            tmp_path, {"PMC1": 1995, "PMC2": 1996, "PMC3": 1998, "PMC4": 1999, "PMC5": None}
        )

        found = sampler.package_candidates([path], 1996, 1998)

        assert found == ["PMC2", "PMC3"]

    def test_the_draw_is_reproducible_from_the_seed(self, tmp_path):
        candidates = [f"PMC{n}" for n in range(100)]

        first = sampler.draw(candidates, 10, seed=0)
        again = sampler.draw(candidates, 10, seed=0)
        other = sampler.draw(candidates, 10, seed=1)

        assert first == again
        assert first != other
        assert len(first) == 10
        assert set(first) <= set(candidates)

    def test_the_draw_does_not_depend_on_candidate_order(self, tmp_path):
        """A directory's glob order is not stable across machines, so the
        draw sorts before sampling — otherwise the recorded seed reproduces
        the draw only on the machine that made it."""
        forwards = [f"PMC{n}" for n in range(100)]

        assert sampler.draw(forwards, 10, seed=0) == sampler.draw(forwards[::-1], 10, seed=0)

    def test_a_target_above_the_candidate_count_takes_them_all(self, tmp_path):
        assert sorted(sampler.draw(["PMC1", "PMC2"], 50, seed=0)) == ["PMC1", "PMC2"]

    def test_only_the_wanted_articles_are_read_back(self, tmp_path):
        path = self._package(tmp_path, {"PMC1": 2024, "PMC2": 2024, "PMC3": 2024})

        found = dict(sampler.read_package_articles([path], {"PMC1", "PMC3"}))

        assert sorted(found) == ["PMC1", "PMC3"]

    def test_a_multi_package_merge_is_sorted_regardless_of_package_order(self, tmp_path):
        """`iter_package_articles` already sorts one directory's own glob, so
        a single-package fixture can't tell `package_candidates`'s own
        trailing `sorted(found)` apart from a no-op. Two packages can: merged
        in package order the two interleave, and only the sort restores one
        global order — the same order however the two paths are given."""
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        self._package(first, {"PMC3": 2024, "PMC1": 2024})
        self._package(second, {"PMC4": 2024, "PMC2": 2024})

        expected = ["PMC1", "PMC2", "PMC3", "PMC4"]
        assert sampler.package_candidates([first, second], 2024, 2024) == expected
        assert sampler.package_candidates([second, first], 2024, 2024) == expected

    def test_an_article_in_two_given_packages_is_one_candidate(self, tmp_path):
        """`--package <dir> --package <its own tarball>` is the layout
        `_package_identity` documents as the ordinary local-mirror shape, so
        the overlap is a plausible command line. Undeduplicated, every article
        enters the pool twice: `draw` can then select one identifier twice, so
        `wanted` is smaller than `--target` with nothing said, and
        `_comparison_reportable`'s denominator inflates the same way. Exit 0,
        plausible numbers, nothing raised.
        """
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        self._package(first, {"PMC1": 2024, "PMC2": 2024})
        self._package(second, {"PMC2": 2024, "PMC3": 2024})

        found = sampler.package_candidates([first, second], 2024, 2024)

        assert found == ["PMC1", "PMC2", "PMC3"]

    def test_an_article_in_two_given_packages_is_read_back_once(self, tmp_path):
        """The other half, and the half that costs a doubled *measurement*:
        `Totals.add` counts every row it is handed, and `_measure_and_journal`
        writes every one to the journal, so a duplicated read-back doubles
        every population and leaves a journal with two rows for one PMCID.
        Deduplicating the candidate pool alone would not stop it — the pool is
        a set of identifiers and this walk is over packages.
        """
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        self._package(first, {"PMC1": 2024, "PMC2": 2024})
        self._package(second, {"PMC2": 2024, "PMC3": 2024})

        read = [pmcid for pmcid, _ in sampler.read_package_articles([first, second], {"PMC2"})]

        assert read == ["PMC2"]


class TestHoldingArticlesForComparison:
    """`_hold_for_comparison` is the fix for a review-caught Critical: the
    articles a `--compare-europepmc` run reads must be a genuine sample of
    the corpus draw, selected by membership, and independent of which of
    them a resumed run has already measured (that half is pinned at the
    `main()` level, in `TestCompareEuropepmcSurvivesAJournalResume`, since it
    is a fact about how `main` calls this function rather than about the
    function itself)."""

    def _package(self, tmp_path, pmcids):
        for pmcid in pmcids:
            (tmp_path / f"{pmcid}.xml").write_text(
                "<article><front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
                "</article-meta></front></article>"
            )
        return tmp_path

    def test_zero_holds_nothing(self, tmp_path):
        path = self._package(tmp_path, ["PMC1", "PMC2"])

        assert sampler._hold_for_comparison([path], ["PMC1", "PMC2"], 0, seed=0) == []

    def test_the_held_set_is_drawn_not_positional(self, tmp_path):
        """`read_package_articles` yields in package order, so taking its
        first N pairs would return a contiguous accession block — the exact
        defect a review caught (a synthetic run held `PMC12000001-4` of 8).
        That is systematically unrepresentative, since the rendition gap is
        a per-publisher deposit property and publishers cluster in accession
        ranges — the same reason the corpus draw itself is stratified by
        month rather than taken as one contiguous walk. Membership must
        instead come from the same seeded `draw()` the corpus uses."""
        drawn = [f"PMC{n:02d}" for n in range(20)]
        path = self._package(tmp_path, drawn)

        held = sampler._hold_for_comparison([path], drawn, 5, seed=3)
        held_ids = {pmcid for pmcid, _ in held}

        assert held_ids == set(sampler.draw(drawn, 5, seed=3))
        assert held_ids != set(drawn[:5])

    def test_the_held_set_is_reproducible_from_the_seed(self, tmp_path):
        drawn = [f"PMC{n:02d}" for n in range(20)]
        path = self._package(tmp_path, drawn)

        first = {p for p, _ in sampler._hold_for_comparison([path], drawn, 5, seed=1)}
        again = {p for p, _ in sampler._hold_for_comparison([path], drawn, 5, seed=1)}

        assert first == again

    def test_asking_for_more_than_the_draw_holds_every_drawn_article(self, tmp_path):
        drawn = ["PMC1", "PMC2", "PMC3"]
        path = self._package(tmp_path, drawn)

        held = sampler._hold_for_comparison([path], drawn, 50, seed=0)

        assert {p for p, _ in held} == set(drawn)


class TestThePackageRunIsRefusedWhenItWouldMislead:
    """`_validate_args`, which exists to stop a rate being printed over a draw
    nobody asked for."""

    def test_a_package_run_needs_both_ends_of_the_window(self, tmp_path):
        refusal = sampler._validate_args(_package_run_args(package=[tmp_path], from_year=1996))

        assert refusal is not None
        assert "--to-year" in refusal

    def test_a_window_that_runs_backwards_is_refused(self, tmp_path):
        """Asserted on the refusal's own words, at a non-default `-o`.

        At `DEFAULT_OUTPUT` the *displaced-window* rule below answers first for
        `1999-1996` — it interpolates both years too — so `"1999" in refusal`
        passed whichever rule fired, and would have kept passing with the
        backwards check deleted outright. `-o` moves the displaced rule out of
        the way, and `"is after"` belongs to this refusal alone.
        """
        refusal = sampler._validate_args(
            _package_run_args(
                package=[tmp_path], from_year=1999, to_year=1996, output=tmp_path / "out.json"
            )
        )

        assert refusal is not None
        assert "is after" in refusal, refusal
        assert "1999" in refusal and "1996" in refusal

    def test_a_negative_target_is_refused(self, tmp_path):
        """The one argument `_validate_args` did not check, and it degrades
        differently on each source.

        On the package branch `--target -5` reaches `random.sample` and raises
        `ValueError` out of the middle of the draw — loud, but a stack trace
        where every other bad argument here gets a one-line refusal before
        anything is touched. On the live branch it raises nothing at all: it
        asks `open_access_pmcids` for 145 identifiers rather than 150, spends
        the search requests for them, and then breaks out of the measuring
        loop before the first fetch.
        """
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025, target=-5)
        )

        assert refusal is not None
        assert "--target" in refusal and "-5" in refusal, refusal

    def test_a_negative_target_is_refused_on_the_live_source_too(self):
        """The branch where nothing else raises, so nothing else can catch it."""
        refusal = sampler._validate_args(_package_run_args(target=-5))

        assert refusal is not None
        assert "--target" in refusal

    def test_a_target_of_zero_is_not_refused(self, tmp_path):
        """The negative control, and a deliberate exclusion rather than an
        oversight: a fresh zero-target draw is already unreportable
        (`Totals.reportable` is `bool(self.rows) and …`), so it exits non-zero
        and writes `*.unreportable.json` without this rule — and
        `TestTheEmptyComparisonNet` uses it as its only unmocked way to hold
        nothing for comparison."""
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025, target=0)
        )

        assert refusal is None

    def test_a_window_without_a_package_is_refused(self):
        """The live path draws by month, not by year; accepting the flags
        there would silently ignore them."""
        refusal = sampler._validate_args(_package_run_args(from_year=1996, to_year=1998))

        assert refusal is not None
        assert "--package" in refusal

    def test_a_displaced_package_window_may_not_land_on_the_default_output(self, tmp_path):
        """The rule `--months-ago` already carries, for the same reason: the
        journal is derived from `--output`, so two windows pool into one rate
        describing neither."""
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=1996, to_year=1998)
        )

        assert refusal is not None
        assert "-o" in refusal

    def test_the_recent_window_may_use_the_default_output(self, tmp_path):
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025)
        )

        assert refusal is None

    def test_a_nonexistent_package_path_is_refused(self, tmp_path):
        missing = tmp_path / "does-not-exist"

        refusal = sampler._validate_args(
            _package_run_args(package=[missing], from_year=2023, to_year=2025)
        )

        assert refusal is not None
        assert str(missing) in refusal

    def test_a_window_only_partly_overlapping_the_recent_one_is_still_displaced(self, tmp_path):
        """Containment, not merely `to_year >= _RECENT_WINDOW_FIRST_YEAR`: a
        `--from-year` that reaches decades below the recent window still
        pools that whole span into the recent draw's journal under the
        recent draw's name, even though the window's tail end looks recent.
        The old `to_year < _RECENT_WINDOW_FIRST_YEAR` test would have let
        exactly this window through onto the default output."""
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=1996, to_year=2025)
        )

        assert refusal is not None
        assert "-o" in refusal

    def test_a_window_reaching_past_the_recent_one_is_also_displaced(self, tmp_path):
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2024, to_year=2030)
        )

        assert refusal is not None
        assert "-o" in refusal

    def test_seed_on_a_live_run_is_refused_rather_than_ignored(self):
        refusal = sampler._validate_args(_package_run_args(seed=7))

        assert refusal is not None
        assert "--seed" in refusal

    def test_the_default_seed_on_a_live_run_is_not_refused(self):
        """Negative control: an untouched default must not trip the guard,
        since there is no way to tell it apart from an explicit default."""
        assert sampler._validate_args(_package_run_args()) is None

    def test_months_on_a_package_run_is_refused(self, tmp_path):
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025, months=12)
        )

        assert refusal is not None
        assert "--months" in refusal

    def test_months_ago_on_a_package_run_is_refused_for_the_right_reason(self, tmp_path):
        """This is the bug: the pre-existing `--months-ago`/default-output
        check knows nothing about `--package` and used to fire here with a
        message about a displaced *live* window this run is not drawing."""
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025, months_ago=3)
        )

        assert refusal is not None
        assert "--months" in refusal
        assert "displaced window" not in refusal

    def test_an_uncompressed_tar_is_refused_up_front(self, tmp_path):
        """The eager check must accept exactly what the draw accepts — a
        directory, or a gzip-compressed tarball — not merely a real
        tarball. `tarfile.is_tarfile()` alone would let an uncompressed
        `.tar` slip past here and fail deep inside `package_candidates`
        instead; this is the gap issue 138's fix round 2 closes."""
        import tarfile

        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar"
        with tarfile.open(path, "w") as tar:
            data = b"<article/>"
            info = tarfile.TarInfo("PMC000xxxxxx/PMC1.xml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        refusal = sampler._validate_args(
            _package_run_args(package=[path], from_year=2023, to_year=2025)
        )

        assert refusal is not None
        assert "gzip-compressed tarball" in refusal

    def test_a_real_gzip_tarball_still_passes_validation(self, tmp_path):
        """The negative control for the check above: a genuine `.tar.gz`
        must not be caught by it."""
        import tarfile

        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            data = b"<article/>"
            info = tarfile.TarInfo("PMC000xxxxxx/PMC1.xml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        refusal = sampler._validate_args(
            _package_run_args(package=[path], from_year=2023, to_year=2025)
        )

        assert refusal is None

    def test_validate_args_and_iter_package_articles_agree_on_every_path(self, tmp_path):
        """`_validate_args`'s eager check and `iter_package_articles`'s own
        guard are meant to test the same predicate (`_is_package_path`) —
        this builds one accepted path and one refused path and checks both
        functions reach the same verdict on each, so a future edit to one
        cannot silently stop matching the other."""
        import tarfile

        accepted = tmp_path / "accepted.tar.gz"
        with tarfile.open(accepted, "w:gz") as tar:
            data = b"<article/>"
            info = tarfile.TarInfo("PMC1.xml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        refused = tmp_path / "refused.tar"
        with tarfile.open(refused, "w") as tar:
            data = b"<article/>"
            info = tarfile.TarInfo("PMC1.xml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        for path in (accepted, refused):
            validated = (
                sampler._validate_args(
                    _package_run_args(package=[path], from_year=2023, to_year=2025)
                )
                is None
            )
            try:
                list(sampler.iter_package_articles(path))
                iterated = True
            except sampler.PackageError:
                iterated = False

            assert validated == iterated, path

    def test_a_gzipped_non_tarball_is_refused_before_the_draw_begins(self, tmp_path):
        """The one input for which the two predicates used to disagree.

        `_is_package_path` accepts *any* gzip file, so a `.tar.gz` that is a
        gzipped non-tar passed validation and raised `PackageError` out of
        `package_candidates` — uncaught, after the journal header had already
        been written, leaving a header with no rows. The predicate now opens
        the stream far enough to know, so the refusal is the one-line message
        `_validate_args` exists to give.
        """
        import gzip

        bad = tmp_path / "notatar.tar.gz"
        bad.write_bytes(gzip.compress(b"this is not a tar archive"))

        refusal = sampler._validate_args(
            _package_run_args(package=[bad], from_year=2023, to_year=2025)
        )

        assert refusal is not None
        assert "gzip-compressed but not a tarball" in refusal
        with pytest.raises(sampler.PackageError):
            list(sampler.iter_package_articles(bad))

    @pytest.mark.parametrize(
        "spelling",
        [
            lambda p: p,
            lambda p: Path(".") / p,
            lambda p: p.resolve(),
            lambda p: p.parent / ".." / p.parent.name / p.name,
        ],
        ids=["bare", "dot-prefixed", "absolute", "dot-dot"],
    )
    def test_every_spelling_of_the_default_corpus_is_refused(self, spelling, tmp_path, monkeypatch):
        """The guard must be about the *file*, not about how it was typed.

        Raw `PurePath` equality matched only the two spellings the committed
        command line happens to use, so `-o "$PWD/tests/data/jats_exhibits.json"`
        with the back-filled package overwrote the *recent* corpus at exit 0.
        No journal is committed, so on a fresh clone `_journal_disagreement`
        cannot catch it either — this guard is the only protection. Resolved
        on both sides, the rule `_package_location` already applies for
        exactly this reason.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "PMC1.xml").write_bytes(b"<article/>")
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)

        refusal = sampler._validate_args(
            _package_run_args(
                package=[package],
                from_year=1996,
                to_year=1998,
                output=spelling(sampler.DEFAULT_OUTPUT),
            )
        )

        assert refusal is not None
        assert "must not" in refusal

    def test_a_genuinely_different_output_is_still_allowed(self, tmp_path, monkeypatch):
        """The negative control: resolving must not refuse every path.

        A guard that refused everything would pass the four cases above while
        making the displaced draw — which is how
        `tests/data/jats_exhibits.backfill.json` was produced — impossible.
        """
        package = tmp_path / "pkg"
        package.mkdir()
        (package / "PMC1.xml").write_bytes(b"<article/>")
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)

        refusal = sampler._validate_args(
            _package_run_args(
                package=[package],
                from_year=1996,
                to_year=1998,
                output=tmp_path / "elsewhere.json",
            )
        )

        assert refusal is None


class TestTheFourWaitingPopulations:
    """Issues 142, 143, 147 and 150 — measured here, decided in their own PRs."""

    def test_a_collab_records_the_children_it_carries(self):
        """142: `<institution>` and `<addr-line>` are legal inside `<collab>`
        and run together in `JATSAuthorInfo.collab` with no separator. Which
        of the two candidate fixes is right is a question about how they are
        actually deposited."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group><contrib contrib-type="author"><collab>
              <institution>The Y Consortium</institution><addr-line>Boston MA</addr-line>
            </collab></contrib></contrib-group>"""),
        )

        assert row.collab_children == {"institution": 1, "addr-line": 1}
        assert row.collabs_with_element_children == 1

    def test_a_collab_of_bare_text_carries_no_children(self):
        row = sampler.measure_article(
            "PMC1",
            _article(
                "<contrib-group><contrib><collab>The Y Group</collab></contrib></contrib-group>"
            ),
        )

        assert row.collab_children == {}
        assert row.collabs_with_element_children == 0

    def test_multiplicity_is_counted_per_contrib(self):
        """143: section 11 counts spellings per *article*, so a contributor
        carrying two `<collab>` is invisible in it."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group>
              <contrib><collab>First Group</collab><collab>Second Group</collab></contrib>
              <contrib><name-alternatives><name><surname>Latin</surname></name>
                <name><surname>Japanese</surname></name></name-alternatives></contrib>
            </contrib-group>"""),
        )

        assert row.contribs_multi_collab == 1
        assert row.contribs_multi_string_name == 0
        assert row.name_alternatives == 1

    def test_formulas_are_counted_by_kind(self):
        """147: a `<tex-math>` is dropped from the prose containing it and a
        `<disp-formula>` from the article outright. `<alternatives>` holding
        both encodings is why the fix is not one more inline element."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sec><p>The model is <inline-formula><tex-math>y = mx</tex-math></inline-formula>.</p>
            <disp-formula id="e1"><label>(1)</label>
              <alternatives><tex-math>E = mc^2</tex-math>
                <mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>E</mml:mi></mml:math>
              </alternatives></disp-formula></sec>"""),
        )

        assert (row.disp_formulas, row.inline_formulas) == (1, 1)
        assert (row.tex_math, row.mml_math) == (2, 1)
        assert row.formula_alternatives_both == 1
        assert row.disp_formulas_with_label == 1

    def test_a_note_only_reference_is_counted_apart(self):
        """150: it renders as an empty `<li>`, renumbering every entry after
        it relative to the publisher's own numbering."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <back><ref-list>
              <ref id="c1"><mixed-citation>Smith 2020.</mixed-citation></ref>
              <ref id="c2"><note><p>Deposited at the CCDC.</p></note></ref>
              <ref id="c3"><label>3</label><note><p>Also a note.</p></note></ref>
            </ref-list></back>"""),
        )

        assert row.refs == 3
        assert row.refs_note_only == 2
        assert row.ref_child_kinds == {"mixed-citation": 1, "note": 2, "label": 1}

    def test_a_nested_article_contributes_none_of_them(self):
        """Task 1's scoping has to reach the new counters too — a peer-review
        round is full of references and formulas."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sub-article><body><disp-formula><tex-math>x</tex-math></disp-formula>
              <back><ref-list><ref><note><p>n</p></note></ref></ref-list></back>
            </body></sub-article>"""),
        )

        assert (row.disp_formulas, row.tex_math, row.refs, row.refs_note_only) == (0, 0, 0, 0)
        assert row.unscoped["refs_note_only"] == 1

    def test_a_row_written_before_these_counters_reads_as_not_measured(self):
        row = sampler.ArticleMeasurement.from_dict({"pmcid": "PMC1"})

        assert row.refs == sampler.NOT_MEASURED
        assert row.disp_formulas == sampler.NOT_MEASURED
        assert row.contribs_multi_collab == sampler.NOT_MEASURED

    def test_a_collab_alternatives_is_counted_as_multiple_collabs(self):
        """Fix round 1, IMPORTANT 1: one collaboration deposited in two
        scripts — the canonical multi-`<collab>` deposit, and precisely the
        last-wins shape #143 is about, since the parser has no
        `<collab-alternatives>` handling and fires `endElement("collab")`
        twice into one contrib frame. A direct-child-only count read this as
        zero of everything; the members sit one level deeper than `<contrib>`
        itself."""
        row = sampler.measure_article(
            "PMC1",
            _article(
                "<contrib-group><contrib><collab-alternatives>"
                "<collab>The Y Group</collab><collab>Y集団</collab>"
                "</collab-alternatives></contrib></contrib-group>"
            ),
        )

        assert row.contribs_multi_collab == 1
        assert row.collab_alternatives == 1
        assert row.contrib_name_spellings == {"collab": 2}

    def test_a_name_alternatives_wrapping_two_string_names_is_counted_as_multiple(self):
        """The less severe sibling of the case above: `name_alternatives`
        already flagged this row (it is a direct child of `<contrib>`), but
        `contribs_multi_string_name` still read zero before this fix, since
        the two `<string-name>` sit inside the wrapper rather than directly
        on `<contrib>`."""
        row = sampler.measure_article(
            "PMC1",
            _article(
                "<contrib-group><contrib><name-alternatives>"
                "<string-name>Jane Q Smith</string-name>"
                "<string-name>スミス ジェーン</string-name>"
                "</name-alternatives></contrib></contrib-group>"
            ),
        )

        assert row.contribs_multi_string_name == 1
        assert row.name_alternatives == 1

    def test_a_collabs_roster_does_not_count_as_an_element_child(self):
        """Fix round 1, IMPORTANT 2: a member roster is issue #120's shape,
        already counted by `collabs_with_a_roster` — it does not exhibit
        #142's defect at all, since the parser's `_UNDIVIDED_NAME_ELEMENTS`
        guard refuses the merge while a `<contrib>` is open. Before this fix,
        `collab_children`/`collabs_with_element_children` counted a roster
        too, mixing #142's population with #120's unrelated one."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group><contrib><collab>The Y Group
              <contrib-group><contrib><name><surname>Member</surname></name></contrib></contrib-group>
            </collab></contrib></contrib-group>"""),
        )

        assert row.collab_children == {}
        assert row.collabs_with_element_children == 0
        assert row.collabs_with_a_roster == 1

    def test_a_disp_formula_deposited_as_only_an_image_is_counted_apart(self):
        """The reviewer's ruling on issue #147's residual: a `<disp-formula>`
        whose only content is a `<graphic>` was deposited as an image, which
        no fix for the text-taking defect can recover as text under any of
        its candidate rules."""
        row = sampler.measure_article(
            "PMC1",
            _article('<disp-formula><graphic xlink:href="eq1.png"/></disp-formula>'),
        )

        assert row.disp_formulas == 1
        assert row.disp_formulas_image_only == 1
        assert row.disp_formulas_with_label == 0


class TestTheRenditionGapIsMeasured:
    """The archive rendition is not what bmlib parses, so the gap is measured.

    #119 found the difference is real: Springer's commented-out
    `<authorqueries>` block is in the archive copy of three articles and
    absent from Europe PMC's copy of the same three.
    """

    def test_identical_renditions_produce_no_delta(self):
        xml = _article("<fig id='f1'><label>Figure 1</label></fig>")
        archive = sampler.measure_article("PMC1", xml)
        served = sampler.measure_article("PMC1", xml)

        assert sampler.rendition_delta(archive, served) == {}

    def test_a_differing_field_is_named_with_both_values(self):
        archive = sampler.measure_article("PMC1", _article("<fig id='f1'/><fig id='f2'/>"))
        served = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))

        delta = sampler.rendition_delta(archive, served)

        assert delta["figures"] == {"archive": 2, "europepmc": 1}

    def test_a_counter_field_is_compared_as_a_mapping(self):
        """The conversion has to earn its name: `Counter({"fig": 1}) ==
        {"fig": 1}` and `json.dumps` serialises a `Counter` natively, so an
        equality check alone passes whether or not `dict(value) if
        isinstance(value, Counter) else value` runs at all. Asserting the
        type is what actually pins the conversion."""
        archive = sampler.measure_article("PMC1", _article("<fig id='f1'><label>F</label></fig>"))
        served = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))

        delta = sampler.rendition_delta(archive, served)

        assert delta["label_parents"] == {"archive": {"fig": 1}, "europepmc": {}}
        assert type(delta["label_parents"]["archive"]) is dict
        assert type(delta["label_parents"]["europepmc"]) is dict

    def test_unscoped_is_skipped_even_when_it_differs_on_its_own(self):
        """`unscoped` is a *within-rendition* property — the scoped-vs-
        unscoped walk of one document — while `rendition_delta` reports
        *between-rendition* facts, so it is out of scope here rather than
        redundant with what it reports. It is not true in general that a
        difference in `unscoped` is "already reported by the named field it
        is a diff of": two rows can disagree in `unscoped` while every named
        field, including `nested_article_regions`, still agrees — one
        rendition's region holds three figures, the other's holds none, with
        neither renditon's own *count* moving — and then this function
        reports it nowhere at all. That gap is real and is not what this
        test is about; this test only pins that the skip does not turn a
        within-rendition-only disagreement into a spurious `unscoped` field
        of its own."""
        archive = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))
        served = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))
        archive.unscoped = {"contribs": {"scoped": 5, "unscoped": 8}}
        served.unscoped = {}

        assert sampler.rendition_delta(archive, served) == {}

    def test_an_article_europe_pmc_will_not_serve_is_unmeasured(self):
        """Not "the renditions agree" — the distinction every population here
        is accounted by."""
        with mock.patch.object(sampler, "_fetch", return_value=None):
            report = sampler.compare_renditions(
                object(), lambda url: None, [("PMC1", _article("<fig id='f1'/>"))]
            )

        assert report["compared"] == 0
        assert report["unmeasured"] == 1
        assert report["articles_differing"] == 0

    def test_agreement_is_reported_as_a_population_not_as_silence(self):
        xml = _article("<fig id='f1'><label>F</label></fig>")
        with mock.patch.object(sampler, "_fetch", return_value=xml):
            report = sampler.compare_renditions(
                object(), lambda url: None, [("PMC1", xml), ("PMC2", xml)]
            )

        assert report["compared"] == 2
        assert report["unmeasured"] == 0
        assert report["articles_differing"] == 0
        assert report["fields_differing"] == {}
        assert report["deltas"] == {}

    def test_a_disagreeing_article_is_named_in_the_deltas_and_tallied_by_field(self):
        """`fields_differing` and `deltas` are two views of the same
        disagreement — a mismatch in one against the other would mean the
        headline count and the per-article evidence disagree.

        This single article already pins the worse half of the defect
        `fields.update(delta.keys())` fixes: under the naive
        `fields.update(delta)`, `Counter.update()` on a mapping takes a
        plain-`dict.update()` fast path while it is still empty, so even one
        differing article would have written the raw
        `{"archive": 2, "europepmc": 1}` payload into `fields_differing` in
        place of `1` — a reader would see something that looks like evidence
        and conclude differing *fields* were being counted, when they were
        not."""
        archive_xml = _article("<fig id='f1'/><fig id='f2'/>")
        served_xml = _article("<fig id='f1'/>")
        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            report = sampler.compare_renditions(object(), lambda url: None, [("PMC1", archive_xml)])

        assert report["compared"] == 1
        assert report["articles_differing"] == 1
        assert report["fields_differing"] == {"figures": 1}
        assert report["deltas"]["PMC1"]["figures"] == {"archive": 2, "europepmc": 1}

    def test_two_articles_disagreeing_on_the_same_field_are_both_tallied(self):
        """The other half of the same defect: once `fields_differing` holds
        a raw payload dict for a field (see the test above), a *second*
        article disagreeing on that same field tries to add its own payload
        dict to the first — `dict + dict` — and `Counter.update()` raises
        `TypeError`, not merely mis-reports. `fields.update(delta.keys())`
        counts articles, never payloads, and cannot raise either way."""
        archive_xml = _article("<fig id='f1'/><fig id='f2'/>")
        served_xml = _article("<fig id='f1'/>")
        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            report = sampler.compare_renditions(
                object(),
                lambda url: None,
                [("PMC1", archive_xml), ("PMC2", archive_xml)],
            )

        assert report["compared"] == 2
        assert report["articles_differing"] == 2
        assert report["fields_differing"] == {"figures": 2}

    def test_the_archive_side_is_measured_from_the_bytes_passed_in_not_refetched(self):
        """`_fetch` is called only for the served side — the archive bytes
        were already read from the package, so re-fetching them would be a
        second, needless network path and would silently substitute Europe
        PMC's copy for the archive's on both sides."""
        xml = _article("<fig id='f1'/>")
        with mock.patch.object(sampler, "_fetch", return_value=xml) as fetch:
            sampler.compare_renditions(object(), lambda url: None, [("PMC1", xml)])

        assert fetch.call_count == 1

    def test_an_unparseable_archive_article_costs_no_europepmc_request(self):
        """The archive side costs nothing to check — its bytes are already
        in hand — so it is checked before `_fetch` runs. An archive article
        that will not parse is a corpus property, not a fact about what
        Europe PMC will serve, so paying a paced request to find that out
        would waste it and blur the two causes together."""
        with mock.patch.object(sampler, "_fetch") as fetch:
            report = sampler.compare_renditions(object(), lambda url: None, [("PMC1", b"<not-xml")])

        assert fetch.call_count == 0
        assert report["compared"] == 0
        assert report["unmeasured"] == 1

    def test_unmeasured_causes_are_told_apart(self):
        """`unmeasured` is one number, but a corpus property (the archive
        would not parse) and a fact about the live source (Europe PMC would
        not serve it) are different causes, and only the second is what this
        instrument sizes."""
        with mock.patch.object(sampler, "_fetch", return_value=None):
            report = sampler.compare_renditions(
                object(),
                lambda url: None,
                [("PMC1", b"<not-xml"), ("PMC2", _article("<fig id='f1'/>"))],
            )

        assert report["unmeasured"] == 2
        assert report["unmeasured_causes"] == {
            "archive_unparseable": 1,
            "europepmc_unavailable": 1,
        }

    def test_a_served_body_that_will_not_parse_is_not_called_unavailable(self):
        """Issue #167: the third cause, which used to be folded into the second.

        `_measure_and_journal` draws exactly this distinction with a nine-line
        comment arguing `is None` over falsiness, and this function collapsed
        it — so a body that arrived whole and would not parse was recorded as
        `europepmc_unavailable`, i.e. transient, in the artifact this branch
        commits as evidence. A re-run recovers a failed fetch and never
        recovers an unparseable document, so the two call for different
        actions from the reader.

        The empty body is the case falsiness got wrong outright: `b""` is not
        `None` — the source served *something* — and it must not be reported
        as a fetch that never happened.
        """
        served = {"PMC1": b"<html>error page</html>", "PMC2": b""}
        with mock.patch.object(
            sampler, "_fetch", side_effect=lambda client, url, pace: served[url.split("/")[-2]]
        ):
            report = sampler.compare_renditions(
                object(),
                lambda url: None,
                [("PMC1", _article("<fig id='f1'/>")), ("PMC2", _article("<fig id='f2'/>"))],
            )

        assert report["compared"] == 0
        assert report["unmeasured"] == 2
        assert report["unmeasured_causes"] == {"served_unparseable": 2}


class TestTheCompareEuropepmcFlag:
    """`--compare-europepmc` only means anything against a `--package` draw,
    and a negative count cannot be a request for a rate — the same shape of
    guard `_validate_args` already applies to `--seed` and the year window."""

    def test_the_flag_defaults_to_off(self):
        args = sampler._build_arg_parser().parse_args([])

        assert args.compare_europepmc == 0

    def test_the_flag_is_parsed_from_the_command_line(self):
        args = sampler._build_arg_parser().parse_args(["--compare-europepmc", "5"])

        assert args.compare_europepmc == 5

    def test_it_is_refused_without_a_package_draw(self):
        refusal = sampler._validate_args(_package_run_args(compare_europepmc=5))

        assert refusal is not None
        assert "--compare-europepmc" in refusal

    def test_a_negative_count_is_refused(self, tmp_path):
        refusal = sampler._validate_args(
            _package_run_args(
                package=[tmp_path], from_year=2023, to_year=2025, compare_europepmc=-1
            )
        )

        assert refusal is not None
        assert "--compare-europepmc" in refusal

    def test_a_positive_count_on_a_package_draw_is_not_refused(self, tmp_path):
        """Negative control: the guard is not refusing every use of the flag."""
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025, compare_europepmc=5)
        )

        assert refusal is None

    def test_the_default_off_on_a_live_run_is_not_refused(self):
        """Negative control: an untouched default must not trip the guard."""
        assert sampler._validate_args(_package_run_args()) is None


class TestTheMeasureEuropepmcFlag:
    """`--measure-europepmc` — Task 6a. Only means anything against a
    `--package` draw (the live source's rows already are Europe PMC's
    rendition). Deliberately *not* refused alongside `--compare-europepmc`:
    an earlier version of this guard claimed the combination would "report
    Europe PMC disagreeing with itself," which review round 1 found false —
    `_hold_for_comparison` reads the archive bytes back from the package
    directly and `compare_renditions` fetches the served side itself,
    neither consulting which rendition this run's own corpus rows came
    from, so the comparison means exactly what it always means either way.
    """

    def test_the_flag_defaults_to_off(self):
        args = sampler._build_arg_parser().parse_args([])

        assert args.measure_europepmc is False

    def test_the_flag_is_parsed_from_the_command_line(self):
        args = sampler._build_arg_parser().parse_args(["--measure-europepmc"])

        assert args.measure_europepmc is True

    def test_it_is_refused_without_a_package_draw(self):
        refusal = sampler._validate_args(_package_run_args(measure_europepmc=True))

        assert refusal is not None
        assert "--measure-europepmc" in refusal

    def test_it_is_not_refused_alongside_compare_europepmc(self, tmp_path):
        """The combination this module refused until review round 1 caught
        the refusal's own justification as false. Both are independent and
        meaningful together: a corpus measured from Europe PMC, plus an
        independent archive-vs-served comparison over a subsample."""
        refusal = sampler._validate_args(
            _package_run_args(
                package=[tmp_path],
                from_year=2023,
                to_year=2025,
                measure_europepmc=True,
                compare_europepmc=5,
            )
        )

        assert refusal is None

    def test_alone_on_a_package_draw_it_is_not_refused(self, tmp_path):
        """Negative control: the guard is not refusing every use of the flag."""
        refusal = sampler._validate_args(
            _package_run_args(
                package=[tmp_path], from_year=2023, to_year=2025, measure_europepmc=True
            )
        )

        assert refusal is None

    def test_the_default_off_alongside_compare_europepmc_is_not_refused(self, tmp_path):
        """Negative control: `--compare-europepmc` alone, without
        `--measure-europepmc`, is the combination Task 5 already exercises
        and must still be accepted."""
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=2023, to_year=2025, compare_europepmc=5)
        )

        assert refusal is None


class TestTheComparisonUnmeasuredShareIsReportable:
    """Finding 3(c) from a review: the unreportable rule was applied to
    "nothing held" (`TestTheEmptyComparisonNet`) but not to "almost nothing
    served". A run where Europe PMC serves only a small minority of the held
    articles must not write the canonical artifact at exit 0 — the same
    `UNMEASURED_SHARE_ERROR_THRESHOLD` rule `Totals.reportable` applies to
    the corpus draw, applied here to what was held for comparison. The
    review's own live repro: `_fetch` serving 1 of 8 held wrote
    `tests/data/jats_exhibits.rendition.json` at exit 0 with `{"compared": 1,
    "unmeasured": 7, ...}` — 87.5% unmeasured against a 20% threshold.
    """

    def test_a_share_just_past_the_threshold_is_not_reportable(self):
        """21 of 100 held unmeasured is 21% — just past the 20% threshold,
        not the review's own 87.5% example. The boundary is what a
        threshold check can get wrong; the middle of the range is not."""
        assert sampler._comparison_reportable({"unmeasured": 21}, held=100) is False

    def test_a_share_exactly_at_the_threshold_is_still_reportable(self):
        """20 of 100 is exactly 20% — `Totals.reportable` uses `<=`, so the
        threshold itself is inside the reportable range, not outside it."""
        assert sampler._comparison_reportable({"unmeasured": 20}, held=100) is True

    def test_zero_unmeasured_is_reportable(self):
        """Negative control at the other end: every held article measured
        is unambiguously reportable."""
        assert sampler._comparison_reportable({"unmeasured": 0}, held=8) is True

    def _package(self, tmp_path: Path, n: int, n_figs: int) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
            (tmp_path / f"PMC{i:08d}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
                f"</article-meta></front><body>{figs}</body></article>"
            )
        return tmp_path

    def test_a_throttled_live_run_refuses_the_canonical_name(self, tmp_path):
        """The review's own reproduction, end to end through `main()`: 8
        held, only 1 served — 87.5% unmeasured. This is the wiring check
        (`main` actually routes `_comparison_reportable`'s verdict to
        `rendition_ok`, the exit code and the file name); the two tests
        above pin the boundary precisely, which this scenario does not."""
        package = self._package(tmp_path / "package", n=8, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article("<fig id='f1'/>")
        # Only PMC00000000 is served; the other 7 are refused by Europe PMC.
        responses = {"PMC00000000": served_xml}

        def fake_fetch(client, url, pace):
            for pmcid, xml in responses.items():
                if pmcid in url:
                    return xml
            return None

        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "8",
            "-o",
            str(output),
            "--compare-europepmc",
            "8",
        ]
        with mock.patch.object(sampler, "_fetch", side_effect=fake_fetch):
            with mock.patch.object(sys, "argv", argv):
                code = sampler.main()

        assert code != 0
        assert not output.with_suffix(".rendition.json").exists()
        unreportable = json.loads(output.with_suffix(".rendition.unreportable.json").read_text())
        assert unreportable["comparison"]["compared"] == 1
        assert unreportable["comparison"]["unmeasured"] == 7


class TestCompareEuropepmcSurvivesAJournalResume:
    """The Critical a review caught: `main()`'s `--compare-europepmc` must
    not silently measure nothing on the ordinary workflow of drawing a
    corpus and then, in a later invocation, adding `--compare-europepmc`.

    A review reproduced this on a synthetic 8-article package where every
    served rendition differs from its archive one::

        FRESH   --compare-europepmc 4 -> "4 compared, 0 unmeasured, 4 differing"
        RESUMED --compare-europepmc 4 -> "0 compared, 0 unmeasured, 0 differing"   exit 0

    because ``for_comparison`` was filled from ``read_package_articles(args.package,
    wanted)``, and ``wanted`` is the corpus draw *minus* whatever the journal
    already holds — empty on the second invocation, since the first one
    measured every drawn article. The written file was "0 compared, 0
    differing", indistinguishable from a genuine null result, at exit 0.

    This runs `main()` itself — the only test in this module to do so —
    because the defect is in how `main` threads state between two
    invocations via the journal, which no pure function's unit tests can
    observe. `_fetch` is mocked throughout, so no network request is made.
    """

    def _package(self, tmp_path: Path, n: int, n_figs: int) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
            (tmp_path / f"PMC{i:08d}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
                f"</article-meta></front><body>{figs}</body></article>"
            )
        return tmp_path

    def _run(self, package: Path, output: Path) -> int:
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "8",
            "--seed",
            "0",
            "-o",
            str(output),
            "--compare-europepmc",
            "4",
        ]
        with mock.patch.object(sys, "argv", argv):
            return sampler.main()

    def test_a_resumed_run_compares_the_same_articles_as_a_fresh_one(self, tmp_path):
        # 12 articles, target 8: `drawn` (8 ids) and `candidates` (12 ids)
        # genuinely differ. At `target == len(package)` the two pools are
        # identical and no assertion here could tell a `candidates`-for-
        # `drawn` mix-up in `_hold_for_comparison`'s call site apart from
        # the fix — a review-caught gap.
        package = self._package(tmp_path / "package", n=12, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"
        # Every archive article carries 2 figures; the served rendition
        # (every fetch, mocked) carries 1 — so every compared article
        # differs, the same shape the review's synthetic repro used.
        served_xml = _article("<fig id='f1'/>")

        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            fresh_code = self._run(package, output)
            fresh = json.loads(output.with_suffix(".rendition.json").read_text())

            # No files are removed between runs — the journal and corpus
            # from the "fresh" run above are exactly what makes this
            # invocation the "resumed" one.
            resumed_code = self._run(package, output)
            resumed = json.loads(output.with_suffix(".rendition.json").read_text())

        assert fresh_code == 0
        assert fresh["held"] == 4
        assert fresh["comparison"]["compared"] == 4
        assert fresh["comparison"]["unmeasured"] == 0
        assert fresh["comparison"]["articles_differing"] == 4

        # The artifact is re-derivable, not just a set of counts: it must
        # carry the same provenance the corpus itself records. A review
        # caught this assertion missing (dropping `**window` from
        # `provenance` left every existing test green).
        assert fresh["source"] == "package"
        assert fresh["packages"] == [package.name]
        assert fresh["first_year"] == 2024
        assert fresh["last_year"] == 2024
        assert fresh["target"] == 8
        assert fresh["seed"] == 0
        assert fresh["requested"] == 4
        # Review round 2: `window`'s own "rendition" describes the corpus
        # draw, not the comparison written beside it (which is always
        # archive-vs-served) — renamed on the way into this file's
        # provenance so a reader cannot mistake one for the other.
        assert fresh["corpus_rendition"] == "archive"
        assert "rendition" not in fresh

        # The held articles are a seeded sample of the corpus's own 8-article
        # draw, never of the wider 12-article candidate pool — pinned by
        # identity, not merely by count, since a `candidates`-for-`drawn`
        # mix-up would still hold exactly 4 articles.
        candidate_ids = [f"PMC{i:08d}" for i in range(12)]
        expected_drawn = sampler.draw(candidate_ids, 8, seed=0)
        expected_held = set(sampler.draw(expected_drawn, 4, seed=0))
        assert set(fresh["comparison"]["deltas"]) == expected_held

        # The regression: not merely "non-zero", but identical to the fresh
        # run — the same 4 articles, sampled the same way, from a draw that
        # does not depend on the journal at all.
        assert resumed_code == 0
        assert resumed == fresh

    def test_a_resumed_run_still_leaves_the_corpus_at_the_reportable_name(self, tmp_path):
        """Verified-good property this fix must not disturb: the comparison
        runs after the corpus is written and must not move it to the
        unreportable path or change its content between the two runs."""
        package = self._package(tmp_path / "package", n=12, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article("<fig id='f1'/>")

        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            self._run(package, output)
            first_corpus = output.read_text()
            self._run(package, output)
            second_corpus = output.read_text()

        assert not output.with_suffix(".unreportable.json").exists()
        assert first_corpus == second_corpus


class TestTheCorpusHoldsOnlyTheRowsItsHeaderExplains:
    """Issue #169. The journal outlives one draw; the corpus must not.

    `_journal_disagreement` deliberately excludes `target` from the draw
    identity, so a top-up resumes rather than being refused — and every
    journalled row was then written into the corpus under *this* run's
    `window`, which records `target`. A reader following this module's own
    re-derivation recipe got one identifier list against a file holding
    another.
    """

    def _package(self, tmp_path: Path, n: int) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (tmp_path / f"PMC{i:08d}.xml").write_text(
                "<article><front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
                "</article-meta></front></article>"
            )
        return tmp_path

    def _run(self, package: Path, output: Path, target: int) -> int:
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            str(target),
            "-o",
            str(output),
        ]
        with mock.patch.object(sys, "argv", argv):
            return sampler.main()

    def test_a_shrunk_target_does_not_leave_the_earlier_rows_in_the_corpus(self, tmp_path):
        """The failure the issue names, at the shape a real re-run takes.

        Draw 8, then re-run the same command at `--target 3`. The header
        agrees (target is not part of the draw identity), so the run resumes
        and used to write `"target": 3` above 8 rows.
        """
        package = self._package(tmp_path / "package", n=20)
        output = tmp_path / "out" / "jats_exhibits.json"

        assert self._run(package, output, 8) == 0
        assert json.loads(output.read_text())["articles"] == 8

        assert self._run(package, output, 3) == 0
        written = json.loads(output.read_text())

        assert written["window"]["target"] == 3
        assert written["articles"] == 3
        assert len(written["rows"]) == 3
        # And they are the draw's own rows, not the first three of the eight.
        candidates = sampler.package_candidates([package], 2024, 2024)
        assert sorted(r["pmcid"] for r in written["rows"]) == sorted(sampler.draw(candidates, 3, 0))

    def test_nothing_measured_is_lost_the_journal_still_holds_it(self, tmp_path):
        """The rows are dropped from the *corpus*, never from the journal.

        Otherwise the fix would trade a wrong corpus for a re-fetch of work
        already done, which for a live `--measure-europepmc` draw is the
        expensive half of the run.
        """
        package = self._package(tmp_path / "package", n=20)
        output = tmp_path / "out" / "jats_exhibits.json"

        self._run(package, output, 8)
        self._run(package, output, 3)
        journal = output.with_suffix(".journal.jsonl")
        rows = [ln for ln in journal.read_text().splitlines()[1:] if ln.strip()]

        assert len(rows) == 8

        # And a run back at the larger target picks them all up again,
        # without re-measuring: the top-up workflow is undisturbed.
        assert self._run(package, output, 8) == 0
        assert json.loads(output.read_text())["articles"] == 8

    def test_a_full_draw_drops_nothing(self, tmp_path):
        """The negative control: a reconcile that dropped rows from an
        ordinary run would empty every corpus this script writes."""
        package = self._package(tmp_path / "package", n=20)
        output = tmp_path / "out" / "jats_exhibits.json"

        assert self._run(package, output, 8) == 0
        assert self._run(package, output, 8) == 0

        assert json.loads(output.read_text())["articles"] == 8


class TestTheEmptyComparisonNet:
    """The second half of the Critical fix: even with the resumed-journal
    cause closed, `main()` refuses to write a comparison result at the
    canonical name when `for_comparison` is empty for *any* reason — "the
    net for whatever else empties it later." `--target 0` is an independent
    way to empty it (nothing is drawn, so nothing can be held), unrelated to
    the journal/`seen` cause the rest of this file's `--compare-europepmc`
    tests are about."""

    def _package(self, tmp_path: Path, n: int) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (tmp_path / f"PMC{i:08d}.xml").write_text(
                "<article><front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
                "</article-meta></front></article>"
            )
        return tmp_path

    def test_a_target_of_zero_refuses_the_canonical_rendition_name(self, tmp_path):
        package = self._package(tmp_path / "package", n=3)
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "0",
            "-o",
            str(output),
            "--compare-europepmc",
            "2",
        ]

        with mock.patch.object(sys, "argv", argv):
            code = sampler.main()

        assert code != 0
        assert not output.with_suffix(".rendition.json").exists()
        unreportable = json.loads(output.with_suffix(".rendition.unreportable.json").read_text())
        assert unreportable["held"] == 0
        assert unreportable["comparison"] is None

    def test_a_hold_short_of_what_was_asked_for_refuses_the_canonical_name(self, tmp_path):
        """Issue #170: `held` was never compared against `requested`.

        `_comparison_reportable` guards the served side against `held`, so a
        run that held 12 of 300 and served all 12 was "reportable" and
        overwrote the canonical artifact with `compared: 12` at exit 0 — the
        headline this repo quotes off that file silently becoming a
        12-article claim under the same filename. `_hold_for_comparison`
        returns `min(n, len(drawn))` by design, and nothing relates
        `--compare-europepmc` to `--target`.
        """
        package = self._package(tmp_path / "package", n=3)
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "2",
            "-o",
            str(output),
            "--compare-europepmc",
            "300",
        ]
        served = _article("<fig id='f1'/>")
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(sampler, "_fetch", return_value=served):
                code = sampler.main()

        rendition = output.with_name("jats_exhibits.rendition.json")
        assert code != 0
        assert not rendition.exists()
        # The comparison is still computed and kept — refusing the canonical
        # name must not throw away work that was actually done.
        unreportable = rendition.with_suffix(".unreportable.json")
        assert unreportable.exists()
        held = json.loads(unreportable.read_text())
        assert (held["requested"], held["held"]) == (300, 2)
        assert held["comparison"]["compared"] == 2

    def test_a_hold_that_matches_the_request_still_writes_the_canonical_name(self, tmp_path):
        """The negative control: the guard must not refuse an honest run.

        A guard comparing the wrong way round would refuse every comparison
        this script has ever written, including the committed one.
        """
        package = self._package(tmp_path / "package", n=3)
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "3",
            "-o",
            str(output),
            "--compare-europepmc",
            "2",
        ]
        served = _article("<fig id='f1'/>")
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(sampler, "_fetch", return_value=served):
                code = sampler.main()

        rendition = output.with_name("jats_exhibits.rendition.json")
        assert code == 0
        assert rendition.exists()
        assert json.loads(rendition.read_text())["comparison"]["compared"] == 2

    def test_the_net_fails_the_exit_code_even_when_the_corpus_is_reportable(self, tmp_path):
        """Mutant F, from a review: `return 0 if ok else 1`, dropping
        `rendition_ok`, left every existing test green, because
        `test_a_target_of_zero_refuses_the_canonical_rendition_name` above
        reaches the net through a *fresh* `--target 0` run, which also
        empties `totals.rows` and makes the corpus's own `ok` False on its
        own — so `ok` alone already produced the right exit code, and the
        mutant was invisible.

        This gives the net a run where the corpus is independently
        reportable — the same full draw again, so every journalled row is
        inside it and `ok` is `True` — leaving only `rendition_ok` to account
        for a non-zero exit.

        It reaches `rendition_ok` through the *unmeasured share* rather than
        through the empty net, because the two can no longer be separated:
        `--target 0` draws nothing, and since issue #169 a corpus holds only
        the rows its own draw explains, so an empty draw now correctly
        empties the corpus and makes `ok` False on its own — which is the
        blindness this test exists to avoid."""
        package = self._package(tmp_path / "package", n=3)
        output = tmp_path / "out" / "jats_exhibits.json"

        draw_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "3",
            "-o",
            str(output),
        ]
        with mock.patch.object(sys, "argv", draw_argv):
            first_code = sampler.main()

        assert first_code == 0
        assert not output.with_suffix(".unreportable.json").exists()

        net_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "3",
            "-o",
            str(output),
            "--compare-europepmc",
            "2",
        ]
        # Every held article comes back unserved, so the comparison's
        # unmeasured share is 1.0 and `rendition_ok` is False while the
        # corpus is untouched and fully reportable.
        with mock.patch.object(sys, "argv", net_argv):
            with mock.patch.object(sampler, "_fetch", return_value=None):
                second_code = sampler.main()

        assert second_code != 0
        # The corpus itself is untouched and still reportable — the
        # comparison is the only thing failing this run.
        assert not output.with_suffix(".unreportable.json").exists()
        assert json.loads(output.read_text())["articles"] == 3
        assert output.with_suffix(".rendition.unreportable.json").exists()


class TestTheJournalDoesNotPoolRenditions:
    """CRITICAL 1, reproduced in review round 1, then again in round 2.

    `main()` reads the journal into `totals`/`seen` *before* branching on
    source/rendition, and a row carries no marker of its own — so a journal
    shared between two different draws let a resumed run silently carry an
    earlier run's rows into a corpus stamped with the *new* run's own
    label, at exit 0, with zero HTTP requests issued.

    Round 1's fix (a rendition-qualified journal filename,
    `*.europepmc.journal.jsonl` vs `*.journal.jsonl`) closed the exact
    sequence it was built from but not the property: round 2's re-review
    reproduced two more collisions the filename cannot rule out —
    `Path.with_suffix()` is not injective over `(output, rendition)`
    (`"jats_exhibits.json".with_suffix(".europepmc.journal.jsonl")` equals
    `"jats_exhibits.europepmc.json".with_suffix(".journal.jsonl")`), and the
    live source's journal name is never rendition-qualified at all, so it
    collides outright with a `--package` archive draw's default journal.
    Both are pinned below (`test_a_journal_at_a_colliding_output_path_is_
    refused_not_pooled`, `test_a_live_run_sharing_the_default_journal_
    with_a_package_draw_is_refused`).

    Fixed for real by giving the journal its own header line naming
    `(source, rendition)` — data travelling with the file, not a fact
    inferred from its name — and refusing to resume from a journal whose
    header disagrees with the current run (`_journal_disagreement`). The
    rendition-qualified filename is kept (it is a friendlier failure: most
    `--measure-europepmc` runs get a fresh journal rather than a refusal on
    their very first invocation), but it is no longer what the guarantee
    rests on.

    Round 3 closed the same defect one axis over: an agreeing
    `(source, rendition)` said nothing about *which draw* — two archive
    runs at one `-o` over different packages/year windows, or over
    different seeds, both pooled silently the same way, live-reproduced
    against this exact code. The header now also carries a `draw` identity
    (packages, year window and seed for a `--package` run; month window for
    the live source) and is compared alongside `(source, rendition)`.
    `target` is deliberately excluded — a resumed run growing its target is
    the ordinary top-up workflow, pinned as a negative control below
    (`test_a_larger_target_on_resume_is_not_a_disagreement`).
    """

    def _package(self, path: Path, pmcids: list[str], n_figs: int, year: int = 2024) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
        for pmcid in pmcids:
            (path / f"{pmcid}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                f"<pub-date pub-type='epub'><year>{year}</year></pub-date>"
                f"</article-meta></front><body>{figs}</body></article>"
            )
        return path

    def test_a_resumed_measure_europepmc_run_does_not_pool_the_archive_journal(self, tmp_path):
        """The reviewer's exact scenario: draw a corpus at the archive
        rendition, then re-run the identical command plus
        `--measure-europepmc` against the same `-o`. Before the fix this
        computed `wanted = ∅` (every id already `seen` in the shared
        journal) and rewrote the corpus `"rendition": "europepmc"` over the
        unchanged archive rows, with `_fetch` never called. After the fix,
        `--measure-europepmc` has its own, initially-empty journal, so
        every id is `wanted` again and genuinely re-measured from served
        bytes."""
        pmcids = [f"PMC{i:08d}" for i in range(6)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article(
            "<fig id='s1'/><fig id='s2'/><fig id='s3'/><fig id='s4'/><fig id='s5'/>"
        )
        base_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "6",
            "--seed",
            "0",
            "-o",
            str(output),
        ]

        with mock.patch.object(sys, "argv", base_argv):
            run1_code = sampler.main()
        run1_corpus = json.loads(output.read_text())

        fetch_calls: list[str] = []

        def counting_fetch(client, url, pace):
            fetch_calls.append(url)
            return served_xml

        with mock.patch.object(sampler, "_fetch", side_effect=counting_fetch):
            with mock.patch.object(sys, "argv", [*base_argv, "--measure-europepmc"]):
                run2_code = sampler.main()
        run2_corpus = json.loads(output.read_text())

        assert run1_code == 0
        assert run1_corpus["window"]["rendition"] == "archive"
        assert run1_corpus["articles"] == 6
        assert [row["figures"] for row in run1_corpus["rows"]] == [2] * 6

        assert run2_code == 0
        assert run2_corpus["window"]["rendition"] == "europepmc"
        assert run2_corpus["articles"] == 6
        # The property that must hold: every row in a corpus stamped
        # "europepmc" is genuinely measured from served bytes, not carried
        # over from the archive journal. Before the fix this was
        # `[2, 2, 2, 2, 2, 2]` with `fetch_calls == []`.
        assert all(row["figures"] == 5 for row in run2_corpus["rows"])
        assert len(fetch_calls) == 6, "every article must be genuinely re-fetched, not pooled"

    def test_an_archive_run_after_a_served_one_is_also_not_pooled(self, tmp_path):
        """The reverse order: `--measure-europepmc` first, then an
        ordinary archive run at the same `-o`. The archive run must read
        its own journal, not the one `--measure-europepmc` just wrote."""
        pmcids = [f"PMC{i:08d}" for i in range(4)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article("<fig id='s1'/>")
        base_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "4",
            "--seed",
            "0",
            "-o",
            str(output),
        ]

        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            with mock.patch.object(sys, "argv", [*base_argv, "--measure-europepmc"]):
                served_code = sampler.main()
        with mock.patch.object(sys, "argv", base_argv):
            archive_code = sampler.main()
        archive_corpus = json.loads(output.read_text())

        assert served_code == 0
        assert archive_code == 0
        assert archive_corpus["window"]["rendition"] == "archive"
        assert all(row["figures"] == 2 for row in archive_corpus["rows"])

    def test_a_journal_at_a_colliding_output_path_is_refused_not_pooled(self, tmp_path):
        """Round 2's first reproduction: `with_suffix` is not injective over
        `(output, rendition)`. An archive run at `-o
        out/jats_exhibits.europepmc.json` writes to the same journal path
        (`out/jats_exhibits.europepmc.journal.jsonl`) that a
        `--measure-europepmc` run at `-o out/jats_exhibits.json` also
        computes — before the header check, this silently carried the
        first run's archive rows into the second run's corpus, stamped
        `"europepmc"`. The header now catches it: refused, exit 2, neither
        file touched by the second run."""
        pmcids = [f"PMC{i:08d}" for i in range(4)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        out_dir = tmp_path / "out"
        run1_output = out_dir / "jats_exhibits.europepmc.json"
        run2_output = out_dir / "jats_exhibits.json"
        assert run1_output.with_suffix(".journal.jsonl") == run2_output.with_suffix(
            ".europepmc.journal.jsonl"
        ), "the fixture must actually exercise the with_suffix collision"

        run1_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "4",
            "--seed",
            "0",
            "-o",
            str(run1_output),
        ]
        with mock.patch.object(sys, "argv", run1_argv):
            run1_code = sampler.main()

        run2_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "4",
            "--seed",
            "0",
            "-o",
            str(run2_output),
            "--measure-europepmc",
        ]
        served_xml = _article("<fig id='s1'/>")
        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            with mock.patch.object(sys, "argv", run2_argv):
                run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code != 0
        # The property that must hold: run 2 never got to write a corpus at
        # all, stamped with either label, from the colliding journal.
        assert not run2_output.exists()
        assert not run2_output.with_suffix(".unreportable.json").exists()

    def test_a_live_run_sharing_the_default_journal_with_a_package_draw_is_refused(
        self, tmp_path, monkeypatch
    ):
        """Round 2's second reproduction: the live source's journal name is
        never rendition-qualified, so a `--package` archive draw and a
        plain live run at the same `-o` collide outright. Before the
        header check, the live branch's own `totals.articles >=
        args.target` break fired immediately against the pooled archive
        rows, issuing zero fetches and writing `"source": "europepmc"`
        over the package's own pmcids and figure counts — exactly the
        reviewer's second reproduction, said to be staged on this
        machine's real journal already."""
        pmcids = [f"PMC{i:08d}" for i in range(3)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"

        package_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "3",
            "--seed",
            "0",
            "-o",
            str(output),
        ]
        with mock.patch.object(sys, "argv", package_argv):
            package_code = sampler.main()

        monkeypatch.setattr(sampler, "open_access_pmcids", lambda *a, **k: iter(["PMCLIVE1"]))
        served_xml = _article("<fig id='s1'/>")
        live_argv = ["sample_jats_exhibits.py", "--target", "3", "-o", str(output)]
        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            with mock.patch.object(sys, "argv", live_argv):
                live_code = sampler.main()

        assert package_code == 0
        assert live_code != 0
        # The corpus the package run wrote is untouched by the refused live
        # run — never silently relabelled "source": "europepmc".
        package_corpus = json.loads(output.read_text())
        assert package_corpus["window"]["source"] == "package"
        assert {row["pmcid"] for row in package_corpus["rows"]} == set(pmcids)

    def test_two_archive_runs_over_different_packages_and_windows_is_refused(self, tmp_path):
        """Review round 3, finding 2: an agreeing `(source, rendition)` is
        not enough — two archive runs at one `-o` over genuinely different
        packages and year windows both used to exit 0 and produce a corpus
        stamped with the *second* run's own `packages`/`first_year` sitting
        over a mix of both runs' rows. Reproduced with two distinct
        packages (different pmcids, different figure counts) and different
        year windows, same `-o`."""
        package_a = self._package(tmp_path / "pkgA", [f"A{i:08d}" for i in range(4)], n_figs=2)
        package_b = self._package(tmp_path / "pkgB", [f"B{i:08d}" for i in range(4)], n_figs=7)
        output = tmp_path / "out" / "jats_exhibits.json"

        run1_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package_a),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "4",
            "--seed",
            "0",
            "-o",
            str(output),
        ]
        with mock.patch.object(sys, "argv", run1_argv):
            run1_code = sampler.main()

        run2_argv = [
            "sample_jats_exhibits.py",
            "--package",
            str(package_b),
            "--from-year",
            "1996",
            "--to-year",
            "1997",
            "--target",
            "4",
            "--seed",
            "0",
            "-o",
            str(output),
        ]
        with mock.patch.object(sys, "argv", run2_argv):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code != 0
        # The property that must hold: run 2 never got to write a corpus
        # pooling package A's rows under package B's own header.
        first_corpus = json.loads(output.read_text())
        assert first_corpus["window"]["packages"] == [package_a.name]
        assert {row["pmcid"] for row in first_corpus["rows"]} == {f"A{i:08d}" for i in range(4)}

    def test_only_the_year_window_differing_is_refused(self, tmp_path):
        """The probe that isolates the year fields.

        The test above varies the package, its path *and* the year window all
        at once, so any one of the three answers for the other two: deleting
        `first_year`/`last_year` from the package branch's `draw_identity`
        left the whole suite green. Here the package and its path are
        identical and only the window moves — and it moves onto a genuinely
        different candidate pool, since the package holds a 2023 article the
        first window excludes.
        """
        package = tmp_path / "package"
        self._package(package, [f"PMC{i:08d}" for i in range(4)], n_figs=2, year=2024)
        self._package(package, [f"PMC1{i:07d}" for i in range(4)], n_figs=7, year=2023)
        output = tmp_path / "out" / "jats_exhibits.json"

        def argv_for(first: str) -> list[str]:
            return [
                "sample_jats_exhibits.py",
                "--package",
                str(package),
                "--from-year",
                first,
                "--to-year",
                "2024",
                "--target",
                "4",
                "--seed",
                "0",
                "-o",
                str(output),
            ]

        with mock.patch.object(sys, "argv", argv_for("2024")):
            run1_code = sampler.main()
        with mock.patch.object(sys, "argv", argv_for("2023")):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code != 0
        corpus = json.loads(output.read_text())
        assert corpus["window"]["first_year"] == 2024
        assert {row["pmcid"] for row in corpus["rows"]} == {f"PMC{i:08d}" for i in range(4)}

    def test_a_live_draw_whose_window_has_moved_on_is_refused(self, tmp_path, monkeypatch):
        """The live source's identity, which was pinned by nothing at all:
        setting `draw_identity = {}` for this branch left all 201 tests green.

        Neither `months` nor `months_ago` was asserted, and — the half that
        matters — neither *is* the draw. The boundaries come from
        `date.today()`, so the identical command names a different window each
        month: a journal written on the last day of a month resumes cleanly
        the day after, under a window shifted by a whole month, and the corpus
        is stamped with the second run's `first`/`last` over a mix of both
        runs' rows. Same argv both times here; only the calendar moves.
        """
        output = tmp_path / "out" / "jats_exhibits.json"
        argv = ["sample_jats_exhibits.py", "--target", "1", "-o", str(output)]

        class _FrozenDate(date):
            frozen = date(2026, 8, 31)

            @classmethod
            def today(cls):
                return cls.frozen

        monkeypatch.setattr(sampler, "date", _FrozenDate)
        monkeypatch.setattr(sampler, "open_access_pmcids", lambda *a, **k: iter(["PMCLIVE1"]))
        with mock.patch.object(sampler, "_fetch", return_value=_article("<fig id='s1'/>")):
            with mock.patch.object(sys, "argv", argv):
                run1_code = sampler.main()
            _FrozenDate.frozen = date(2026, 9, 2)
            with mock.patch.object(sys, "argv", argv):
                run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code != 0
        corpus = json.loads(output.read_text())
        # August's window is the second run's; the corpus must still carry the
        # first run's own, undisturbed.
        assert corpus["window"]["last"] == "2026-07-31"

    def test_a_different_seed_at_one_output_is_refused(self, tmp_path):
        """Review round 3's second live reproduction: same package, same
        year window, only `--seed` differs — a different seed draws a
        different sample, so pooling the two under one seed's label is the
        same defect as the packages/window case above, one field over."""
        pmcids = [f"PMC{i:08d}" for i in range(20)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"

        def argv_for(seed: str) -> list[str]:
            return [
                "sample_jats_exhibits.py",
                "--package",
                str(package),
                "--from-year",
                "2024",
                "--to-year",
                "2024",
                "--target",
                "5",
                "--seed",
                seed,
                "-o",
                str(output),
            ]

        with mock.patch.object(sys, "argv", argv_for("0")):
            run1_code = sampler.main()
        with mock.patch.object(sys, "argv", argv_for("99")):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code != 0
        first_corpus = json.loads(output.read_text())
        assert first_corpus["window"]["seed"] == 0
        assert len(first_corpus["rows"]) == 5

    def test_a_larger_target_on_resume_is_not_a_disagreement(self, tmp_path):
        """Negative control the draw-identity check must not break: growing
        `--target` on an otherwise-identical resume is the ordinary top-up
        workflow this whole journal mechanism exists for, not a
        disagreement — `target` is deliberately excluded from `draw`."""
        pmcids = [f"PMC{i:08d}" for i in range(10)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"

        def argv_for(target: str) -> list[str]:
            return [
                "sample_jats_exhibits.py",
                "--package",
                str(package),
                "--from-year",
                "2024",
                "--to-year",
                "2024",
                "--target",
                target,
                "--seed",
                "0",
                "-o",
                str(output),
            ]

        with mock.patch.object(sys, "argv", argv_for("5")):
            run1_code = sampler.main()
        with mock.patch.object(sys, "argv", argv_for("10")):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code == 0
        second_corpus = json.loads(output.read_text())
        assert second_corpus["articles"] == 10

    def _argv(self, package: Path, output: Path) -> list[str]:
        """One `--package` command line, so only the package path varies below."""
        return [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            "4",
            "--seed",
            "0",
            "-o",
            str(output),
        ]

    def test_two_packages_sharing_a_basename_at_different_paths_is_refused(self, tmp_path):
        """Review round 4, reproduced live: `_package_identity` returns a bare
        *name* on every branch, so two genuinely different package
        directories sharing a basename under different parents produce one
        identity string, `draw["packages"]` matches, and the journal pools
        them. Not contrived — PMC's own baseline extraction names a directory
        by accession range alone, independent of subset and snapshot date,
        which is the layout this repo's packages already use. Before the fix
        run 2 exited 0 and the corpus held all 8 rows (figure counts 2 and 7
        side by side) under `"packages": ["PMC012xxxxxx"]`."""
        package_a = self._package(
            tmp_path / "locationA" / "PMC012xxxxxx", [f"A{i:08d}" for i in range(4)], n_figs=2
        )
        package_b = self._package(
            tmp_path / "locationB" / "PMC012xxxxxx", [f"B{i:08d}" for i in range(4)], n_figs=7
        )
        output = tmp_path / "out" / "jats_exhibits.json"
        # The axis under test, isolated: every other field of the draw
        # identity is identical between the two runs by construction, and
        # the artifact-name field agrees too — so a refusal below can only
        # come from the resolved path.
        assert sampler._package_identity(package_a) == sampler._package_identity(package_b)

        with mock.patch.object(sys, "argv", self._argv(package_a, output)):
            run1_code = sampler.main()
        with mock.patch.object(sys, "argv", self._argv(package_b, output)):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code != 0
        # Package A's corpus is untouched: never grown by package B's rows,
        # never restamped.
        corpus = json.loads(output.read_text())
        assert {row["pmcid"] for row in corpus["rows"]} == {f"A{i:08d}" for i in range(4)}
        assert sorted(row["figures"] for row in corpus["rows"]) == [2, 2, 2, 2]

    def test_the_same_package_at_the_same_path_still_resumes(self, tmp_path):
        """The negative control the path check must not break: resuming the
        identical draw from the identical directory is the journal's entire
        purpose, so a check strict enough to refuse it would trade one defect
        for another. Run 2 re-reads run 1's rows and completes."""
        package = self._package(
            tmp_path / "locationA" / "PMC012xxxxxx", [f"A{i:08d}" for i in range(4)], n_figs=2
        )
        output = tmp_path / "out" / "jats_exhibits.json"

        with mock.patch.object(sys, "argv", self._argv(package, output)):
            run1_code = sampler.main()
        with mock.patch.object(sys, "argv", self._argv(package, output)):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code == 0
        corpus = json.loads(output.read_text())
        assert corpus["articles"] == 4
        assert {row["pmcid"] for row in corpus["rows"]} == {f"A{i:08d}" for i in range(4)}

    def test_the_same_package_reached_by_another_spelling_still_resumes(self, tmp_path):
        """Resolved, not merely stringified: one directory reached by two
        spellings of its path is one location, so an un-normalised `a/../a`
        resumes rather than being refused as a second package. A raw
        `str(path)` in the draw identity would refuse this, which is a
        false refusal on the ordinary shell shape of the same argument."""
        package = self._package(
            tmp_path / "locationA" / "PMC012xxxxxx", [f"A{i:08d}" for i in range(4)], n_figs=2
        )
        detoured = tmp_path / "locationA" / ".." / "locationA" / "PMC012xxxxxx"
        assert str(detoured) != str(package)
        assert detoured.resolve() == package.resolve()
        output = tmp_path / "out" / "jats_exhibits.json"

        with mock.patch.object(sys, "argv", self._argv(package, output)):
            run1_code = sampler.main()
        with mock.patch.object(sys, "argv", self._argv(detoured, output)):
            run2_code = sampler.main()

        assert run1_code == 0
        assert run2_code == 0
        assert json.loads(output.read_text())["articles"] == 4

    def test_the_journal_records_the_path_and_the_corpus_records_the_artifact(self, tmp_path):
        """The two identities are separate on purpose and must stay separate.

        The journal's draw identity is a question about *this machine* — did
        these rows come from the bytes this run is about to read — so it
        carries the resolved path. The corpus header's `packages` is the
        *public artifact name*, whose job is to let a reader re-download it
        and re-derive the draw; a machine path there would defeat that. This
        pins both halves, so "deduplicating" the two fields into one breaks a
        test whichever direction it is done in."""
        package = self._package(
            tmp_path / "locationA" / "PMC012xxxxxx", [f"A{i:08d}" for i in range(4)], n_figs=2
        )
        output = tmp_path / "out" / "jats_exhibits.json"
        with mock.patch.object(sys, "argv", self._argv(package, output)):
            assert sampler.main() == 0

        journal = output.with_suffix(".journal.jsonl")
        header = json.loads(journal.read_text().splitlines()[0])
        assert header[sampler._JOURNAL_HEADER_KEY] is True
        assert header["draw"]["package_paths"] == [str(package.resolve())]
        assert header["draw"]["packages"] == ["PMC012xxxxxx"]

        window = json.loads(output.read_text())["window"]
        assert window["packages"] == ["PMC012xxxxxx"]
        # No machine path anywhere in the public header — the whole reason
        # the two identities are not one field.
        assert str(tmp_path) not in json.dumps(window)


class TestTheJournalHeaderDisagreementCheck:
    """`_journal_disagreement` and `_ensure_journal_header` in isolation —
    the pure mechanism `main()` calls, tested directly rather than only
    through a full `main()` run, so the boundary cases (empty file, legacy
    header-less file, agreeing header, undecodable file) are each pinned
    precisely. `_DRAW` is a fixed, arbitrary draw identity used wherever a
    test is not itself about the `draw` comparison, so those tests are not
    accidentally exercising it."""

    _DRAW = {"packages": ["pkg"], "first_year": 2024, "last_year": 2024, "seed": 0}

    def test_no_journal_is_not_a_disagreement(self, tmp_path):
        journal = tmp_path / "does-not-exist.journal.jsonl"

        assert sampler._journal_disagreement(journal, "package", "archive", self._DRAW) is None

    def test_an_empty_journal_is_not_a_disagreement(self, tmp_path):
        """An existing-but-empty file — e.g. created by a prior run that
        crashed before writing anything — has no header to disagree with."""
        journal = tmp_path / "empty.journal.jsonl"
        journal.write_text("")

        assert sampler._journal_disagreement(journal, "package", "archive", self._DRAW) is None

    def test_a_whitespace_only_journal_is_not_a_disagreement(self, tmp_path):
        """Review round 3: `_journal_disagreement` and
        `_ensure_journal_header` must agree on what "empty" means. A
        newline-only file is one line by `splitlines()` (`['']`, not `[]`),
        so treating "empty" as "no lines" would try to parse `''` as JSON
        and refuse it as a legacy journal — this pins the fix (both
        functions test `not text.strip()` instead)."""
        journal = tmp_path / "whitespace.journal.jsonl"
        journal.write_text("\n")

        assert sampler._journal_disagreement(journal, "package", "archive", self._DRAW) is None

    def test_a_matching_header_is_not_a_disagreement(self, tmp_path):
        journal = tmp_path / "matches.journal.jsonl"
        journal.write_text(sampler._journal_header_line("package", "europepmc", self._DRAW))

        assert sampler._journal_disagreement(journal, "package", "europepmc", self._DRAW) is None

    def test_a_mismatched_rendition_is_a_disagreement(self, tmp_path):
        journal = tmp_path / "mismatch.journal.jsonl"
        journal.write_text(sampler._journal_header_line("package", "archive", self._DRAW))

        reason = sampler._journal_disagreement(journal, "package", "europepmc", self._DRAW)

        assert reason is not None
        assert "archive" in reason
        assert "europepmc" in reason

    def test_a_mismatched_source_is_a_disagreement(self, tmp_path):
        """Isolated from the rendition check: the header's own rendition
        (`"europepmc"`) matches what this run is asking for, and only the
        `source` differs — this is what proves `source` is actually
        compared and not merely along for the ride with `rendition` (a
        mutant that dropped the `source` half of the comparison passed
        `test_a_mismatched_rendition_is_a_disagreement` above and only
        reddened here)."""
        journal = tmp_path / "mismatch.journal.jsonl"
        journal.write_text(sampler._journal_header_line("package", "europepmc", self._DRAW))

        reason = sampler._journal_disagreement(journal, "europepmc", "europepmc", self._DRAW)

        assert reason is not None
        assert "package" in reason

    def test_a_mismatched_draw_is_a_disagreement(self, tmp_path):
        """Review round 3, finding 2: the header-agreeing `(source,
        rendition)` pair is not enough on its own — two archive runs at one
        `-o` over different packages/years (or seeds) pooled silently
        before this. Isolated from both other checks: `source` and
        `rendition` both agree, only `draw` differs."""
        journal = tmp_path / "mismatch.journal.jsonl"
        journal.write_text(sampler._journal_header_line("package", "archive", self._DRAW))
        other_draw = {**self._DRAW, "seed": 99}

        reason = sampler._journal_disagreement(journal, "package", "archive", other_draw)

        assert reason is not None
        assert "draw" in reason

    def test_a_legacy_header_less_journal_is_a_disagreement_not_a_crash(self, tmp_path):
        """A journal written before this check existed opens with a real
        `ArticleMeasurement` row, not a header — must be refused explicitly,
        never trusted blind and never an unhandled exception."""
        journal = tmp_path / "legacy.journal.jsonl"
        row = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))
        journal.write_text(json.dumps(row.to_dict()) + "\n")

        reason = sampler._journal_disagreement(journal, "package", "archive", self._DRAW)

        assert reason is not None
        assert "no rendition header" in reason

    def test_a_line_that_is_not_even_json_is_a_disagreement_not_a_crash(self, tmp_path):
        journal = tmp_path / "garbage.journal.jsonl"
        journal.write_text("not json at all\n")

        reason = sampler._journal_disagreement(journal, "package", "archive", self._DRAW)

        assert reason is not None

    def test_a_journal_truncated_mid_multibyte_character_is_a_disagreement_not_a_crash(
        self, tmp_path
    ):
        """Review round 3, finding 3, reproduced: a journal cut off exactly
        inside a multibyte UTF-8 character (the header's own JSON can
        legitimately contain one — an em dash, say) raised
        `UnicodeDecodeError` straight out of `_journal_disagreement`
        (`Path.read_text` decodes eagerly) rather than refusing like every
        other unreadable-journal shape above."""
        journal = tmp_path / "truncated.journal.jsonl"
        valid_header = sampler._journal_header_line("package", "archive", self._DRAW)
        broken_tail = "\u2014".encode("utf-8")[:-1]  # 2 of an em dash's 3 UTF-8 bytes
        with pytest.raises(UnicodeDecodeError):
            broken_tail.decode("utf-8")  # the fixture must actually be broken UTF-8
        journal.write_bytes(valid_header.encode("utf-8") + broken_tail)

        reason = sampler._journal_disagreement(journal, "package", "archive", self._DRAW)

        assert reason is not None
        assert "cannot be read as UTF-8" in reason

    def test_ensure_journal_header_creates_one_when_absent(self, tmp_path):
        journal = tmp_path / "nested" / "new.journal.jsonl"

        sampler._ensure_journal_header(journal, "package", "archive", self._DRAW)

        header = json.loads(journal.read_text().splitlines()[0])
        assert header["source"] == "package"
        assert header["rendition"] == "archive"
        assert header["draw"] == self._DRAW

    def test_ensure_journal_header_overwrites_an_empty_file(self, tmp_path):
        journal = tmp_path / "empty.journal.jsonl"
        journal.write_text("")

        sampler._ensure_journal_header(journal, "europepmc", "europepmc", self._DRAW)

        header = json.loads(journal.read_text().splitlines()[0])
        assert header["source"] == "europepmc"

    def test_ensure_journal_header_overwrites_a_whitespace_only_file(self, tmp_path):
        """The other half of agreeing on "empty" (see the disagreement-check
        test above): a newline-only file is also safe for this function to
        claim, not merely for the disagreement check to pass over."""
        journal = tmp_path / "whitespace.journal.jsonl"
        journal.write_text("\n\n")

        sampler._ensure_journal_header(journal, "package", "archive", self._DRAW)

        header = json.loads(journal.read_text().splitlines()[0])
        assert header["source"] == "package"

    def test_ensure_journal_header_leaves_an_agreeing_file_alone(self, tmp_path):
        journal = tmp_path / "existing.journal.jsonl"
        row = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))
        original = (
            sampler._journal_header_line("package", "archive", self._DRAW)
            + json.dumps(row.to_dict())
            + "\n"
        )
        journal.write_text(original)

        sampler._ensure_journal_header(journal, "package", "archive", self._DRAW)

        assert journal.read_text() == original

    def test_ensure_journal_header_leaves_an_undecodable_file_alone(self, tmp_path):
        """Not "empty" and not decodable — `_journal_disagreement` would
        already have refused a run over this file, so reaching this
        function with one at all means it was called directly. Destroying
        unreadable content on a guess is worse than doing nothing."""
        journal = tmp_path / "truncated.journal.jsonl"
        valid_header = sampler._journal_header_line("package", "archive", self._DRAW)
        broken_tail = "\u2014".encode("utf-8")[:-1]
        broken = valid_header.encode("utf-8") + broken_tail
        journal.write_bytes(broken)

        sampler._ensure_journal_header(journal, "package", "archive", self._DRAW)

        assert journal.read_bytes() == broken


class TestTheMeasureEuropepmcFlagMeasuresTheServedRendition:
    """Task 6a. `--measure-europepmc` moves *which bytes* a `--package`
    draw's own rows are measured from — Europe PMC's ``fullTextXML`` instead
    of the package's archive bytes — without moving the drawn identifier
    list. Mixing renditions inside one corpus is the one outcome that must
    be impossible: an article Europe PMC will not serve is unmeasured,
    entering no denominator, and is never silently measured from the
    archive copy instead. `_fetch` is mocked throughout; no network request
    is made.
    """

    def _package(self, path: Path, pmcids: list[str], n_figs: int, year: int = 2024) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
        for pmcid in pmcids:
            (path / f"{pmcid}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                f"<pub-date pub-type='epub'><year>{year}</year></pub-date>"
                f"</article-meta></front><body>{figs}</body></article>"
            )
        return path

    def _argv(
        self, package: Path, output: Path, *, target: int, extra: list[str] | None = None
    ) -> list[str]:
        return [
            "sample_jats_exhibits.py",
            "--package",
            str(package),
            "--from-year",
            "2024",
            "--to-year",
            "2024",
            "--target",
            str(target),
            "--seed",
            "0",
            "-o",
            str(output),
            *(extra or []),
        ]

    def test_rows_are_measured_from_the_served_bytes_not_the_packages(self, tmp_path):
        """The fixture where the two renditions differ: the archive carries
        2 figures per article, the mocked served rendition carries 5. A row
        measured from the wrong source is caught by the figure count alone."""
        pmcids = [f"PMC{i:08d}" for i in range(3)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article(
            "<fig id='s1'/><fig id='s2'/><fig id='s3'/><fig id='s4'/><fig id='s5'/>"
        )

        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            argv = self._argv(package, output, target=3, extra=["--measure-europepmc"])
            with mock.patch.object(sys, "argv", argv):
                code = sampler.main()

        assert code == 0
        written = json.loads(output.read_text())
        assert written["window"]["rendition"] == "europepmc"
        assert written["articles"] == 3
        assert written["rows"], "the fixture must actually produce rows to check"
        for row in written["rows"]:
            assert row["figures"] == 5, (
                "a row must be measured from the served bytes (5 figures), never the "
                "archive's own 2"
            )

    def test_the_identifier_list_is_unchanged_by_the_flag(self, tmp_path):
        """The same `(packages, window, target, seed)` draws the same
        articles whether or not `--measure-europepmc` is set — only the
        bytes measured move."""
        pmcids = [f"PMC{i:08d}" for i in range(6)]
        package = self._package(tmp_path / "package", pmcids, n_figs=2)
        served_xml = _article("<fig id='s1'/>")
        archive_output = tmp_path / "archive" / "jats_exhibits.json"
        served_output = tmp_path / "served" / "jats_exhibits.json"

        with mock.patch.object(sys, "argv", self._argv(package, archive_output, target=3)):
            archive_code = sampler.main()
        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            argv = self._argv(package, served_output, target=3, extra=["--measure-europepmc"])
            with mock.patch.object(sys, "argv", argv):
                served_code = sampler.main()

        assert archive_code == 0
        assert served_code == 0
        archive_ids = {row["pmcid"] for row in json.loads(archive_output.read_text())["rows"]}
        served_ids = {row["pmcid"] for row in json.loads(served_output.read_text())["rows"]}
        assert archive_ids == served_ids
        assert len(archive_ids) == 3

    def test_an_article_europe_pmc_will_not_serve_is_unmeasured_never_backfilled(self, tmp_path):
        """The one outcome that must be impossible: a fetch failure must
        never fall back to the archive's own bytes for that article. Rigged
        so a silent fallback would be caught immediately — the refused
        article's archive copy carries 99 figures, a count nothing else in
        this test produces, so its presence anywhere in the written rows
        would prove the fallback happened. 6 articles, 1 refused, keeps the
        corpus-level unmeasured share (1/6 ≈ 16.7%) under the reportable
        threshold, so the canonical path is what gets written."""
        pmcids = [f"PMC{i:08d}" for i in range(6)]
        refused = pmcids[0]
        package = tmp_path / "package"
        package.mkdir()
        for pmcid in pmcids:
            n_figs = 99 if pmcid == refused else 2
            figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
            (package / f"{pmcid}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
                f"</article-meta></front><body>{figs}</body></article>"
            )
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article("<fig id='s1'/>")

        def fake_fetch(client, url, pace):
            if refused in url:
                return None
            return served_xml

        with mock.patch.object(sampler, "_fetch", side_effect=fake_fetch):
            argv = self._argv(package, output, target=6, extra=["--measure-europepmc"])
            with mock.patch.object(sys, "argv", argv):
                code = sampler.main()

        assert code == 0
        written = json.loads(output.read_text())
        assert written["unmeasured"] == 1
        written_ids = {row["pmcid"] for row in written["rows"]}
        assert refused not in written_ids
        assert all(row["figures"] != 99 for row in written["rows"])

    def test_the_header_records_rendition_on_both_settings(self, tmp_path):
        pmcids = [f"PMC{i:08d}" for i in range(2)]
        package = self._package(tmp_path / "package", pmcids, n_figs=1)
        archive_output = tmp_path / "archive" / "jats_exhibits.json"
        served_output = tmp_path / "served" / "jats_exhibits.json"
        served_xml = _article("<fig id='s1'/>")

        with mock.patch.object(sys, "argv", self._argv(package, archive_output, target=2)):
            sampler.main()
        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            argv = self._argv(package, served_output, target=2, extra=["--measure-europepmc"])
            with mock.patch.object(sys, "argv", argv):
                sampler.main()

        assert json.loads(archive_output.read_text())["window"]["rendition"] == "archive"
        assert json.loads(served_output.read_text())["window"]["rendition"] == "europepmc"

    def test_the_live_source_also_records_its_rendition(self, tmp_path, monkeypatch):
        """Negative control on the other branch: the live Europe PMC source
        has no archive rendition to choose between, and always records
        `"europepmc"` — with no `--package` at all, so this exercises the
        `else` branch of `main`'s window construction."""
        monkeypatch.setattr(sampler, "open_access_pmcids", lambda *a, **k: iter(["PMC1"]))
        served_xml = _article("<fig id='s1'/>")
        output = tmp_path / "live" / "jats_exhibits.json"
        argv = ["sample_jats_exhibits.py", "--target", "1", "-o", str(output)]

        with mock.patch.object(sampler, "_fetch", return_value=served_xml):
            with mock.patch.object(sys, "argv", argv):
                code = sampler.main()

        assert code == 0
        assert json.loads(output.read_text())["window"]["rendition"] == "europepmc"

    def test_a_throttled_run_writes_unreportable_rather_than_a_thin_corpus(self, tmp_path):
        """The unmeasured-share rule applies here exactly as it does to the
        archive path: 4 of 5 held articles unmeasured is 80%, past
        `UNMEASURED_SHARE_ERROR_THRESHOLD`, so the run must refuse the
        canonical name rather than write a corpus a later reader would take
        as a clean 20%-nesting-rate measurement."""
        pmcids = [f"PMC{i:08d}" for i in range(5)]
        package = self._package(tmp_path / "package", pmcids, n_figs=1)
        served = pmcids[0]
        output = tmp_path / "out" / "jats_exhibits.json"
        served_xml = _article("<fig id='s1'/>")

        def fake_fetch(client, url, pace):
            if served in url:
                return served_xml
            return None

        with mock.patch.object(sampler, "_fetch", side_effect=fake_fetch):
            argv = self._argv(package, output, target=5, extra=["--measure-europepmc"])
            with mock.patch.object(sys, "argv", argv):
                code = sampler.main()

        assert code != 0
        assert not output.exists()
        unreportable = json.loads(output.with_suffix(".unreportable.json").read_text())
        assert unreportable["unmeasured"] == 4
        assert unreportable["articles"] == 1
