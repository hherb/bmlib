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
        totals = sampler.Totals()
        totals.add(
            sampler.measure_article("PMC1", _article('<fig id="f1"><label>Figure 1</label></fig>'))
        )

        assert sampler.print_report(totals) is True
        assert "PREMISE HOLDS" in capsys.readouterr().out

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

        assert sampler.print_report(totals) is True
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

        assert sampler.print_report(totals) is True
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

        assert sampler.print_report(totals) is True
        line = next(ln for ln in capsys.readouterr().out.splitlines() if "AND a <table>" in ln)
        assert "NOT MEASURED" in line, line

    def test_one_stale_row_among_fresh_ones_still_reports_not_measured(self, capsys):
        """The sentinel is small and the sum is not.

        One row predating the counter contributes -1 while three hundred fresh
        ones contribute real counts, so the total stays positive and the
        population would print as a rate that silently omits the stale row. A
        journal is topped up across runs, so a mixed one is the ordinary case.
        """
        image_only = _article('<table-wrap id="t1"><graphic xlink:href="scan.png"/></table-wrap>')
        stale = sampler.measure_article("PMC1", image_only).to_dict()
        for key in sampler._TABLE_SIDE_COUNTERS:
            del stale[key]
        totals = sampler.Totals()
        totals.add(sampler.ArticleMeasurement.from_dict(stale))
        for n in range(2, 30):
            totals.add(sampler.measure_article(f"PMC{n}", image_only))

        assert totals.sum_of("tables_with_graphic") > 0, "the sum must stay positive"
        assert sampler.print_report(totals) is True
        assert "NOT MEASURED" in capsys.readouterr().out


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

    def test_the_title_owner_population_is_what_the_comments_cite(self):
        """The #125/#130 population, cited in five files and wrong in one."""
        recent, recent_maps, recent_articles = self._totals(self.RECENT)
        backfill, backfill_maps, backfill_articles = self._totals(self.BACKFILL)

        assert sum(recent_maps["section_renaming_titles"].values()) == 69
        assert recent_articles == 31
        assert set(recent_maps["section_renaming_titles"]) == {"caption"}
        assert sum(backfill_maps["section_renaming_titles"].values()) == 13
        assert backfill_articles == 1
        assert set(backfill_maps["section_renaming_titles"]) == {"list"}
        assert recent["captions"] == 1550
        assert backfill["captions"] == 288

    def test_the_caption_premise_and_its_empty_populations_hold(self):
        """#123's premise measures full and both its own populations empty."""
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert recent["exhibits_with_direct_caption"] == 1413
        assert recent["exhibits_with_descendant_caption"] == 1413
        assert backfill["exhibits_with_direct_caption"] == 288
        assert backfill["exhibits_with_descendant_caption"] == 288
        assert recent["nested_captions"] == 0
        assert backfill["nested_captions"] == 0
        assert set(recent_maps["exhibit_caption_owners"]) == {"fig", "table-wrap"}
        assert set(backfill_maps["exhibit_caption_owners"]) == {"fig", "table-wrap"}

    def test_the_label_premise_holds_on_both_windows(self):
        """#116's premise, and the denominator the comment must not overstate."""
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert recent["exhibits_with_direct_label"] == 1446
        assert recent["exhibits_with_descendant_label"] == 1446
        assert backfill["exhibits_with_direct_label"] == 365
        assert backfill["exhibits_with_descendant_label"] == 365
        # 1,446 of the *labelled* exhibits, not of all 1,500 — 54 carry none.
        assert recent["figures"] + recent["tables"] == 1500
        assert set(recent_maps["label_parents"]) == {"fig", "table-wrap", "fn", "list-item"}

    def test_the_graphic_populations_are_what_offer_graphic_cites(self):
        """#117's shares and #127's two renditions, both cited as percentages."""
        recent, _, _ = self._totals(self.RECENT)
        backfill, _, _ = self._totals(self.BACKFILL)

        assert (recent["figures_with_graphic"], backfill["figures_with_graphic"]) == (828, 276)
        assert (recent["figures_multi_graphic"], backfill["figures_multi_graphic"]) == (437, 168)
        assert (recent["last_is_thumb"], backfill["last_is_thumb"]) == (434, 165)
        assert (recent["first_is_thumb"], backfill["first_is_thumb"]) == (0, 0)
        assert recent["graphics"] + backfill["graphics"] == 2397
        assert recent["alternatives_members"] + backfill["alternatives_members"] == 1329
        assert recent["alternatives_declaring_mime"] + backfill["alternatives_declaring_mime"] == 0
        assert recent["alternatives_archival"] + backfill["alternatives_archival"] == 0

    def test_the_table_side_answers_135_as_an_empty_population(self):
        """#135, and the #127 windows the ROADMAP and the sampler both cite."""
        recent, recent_maps, _ = self._totals(self.RECENT)
        backfill, backfill_maps, _ = self._totals(self.BACKFILL)

        assert recent["tables"] + backfill["tables"] == 755
        assert recent["tables_with_graphic"] + backfill["tables_with_graphic"] == 16
        assert recent["tables_multi_graphic"] + backfill["tables_multi_graphic"] == 0
        assert (recent["tables"], recent["tables_image_only"]) == (662, 0)
        assert (backfill["tables"], backfill["tables_image_only"]) == (93, 11)
        assert (recent["tables_with_both"], backfill["tables_with_both"]) == (5, 0)
        assert sum(recent_maps["foreign_owned_graphics"].values()) == 36
        assert set(recent_maps["foreign_owned_graphics"]) == {"td"}
        assert backfill_maps.get("foreign_owned_graphics", {}) == {}
        assert recent["nested_figures"] + recent["nested_tables"] == 0
        assert backfill["nested_figures"] + backfill["nested_tables"] == 0


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

    def test_iter_package_articles_routes_through_is_package_path(self, tmp_path, monkeypatch):
        """Proof, not inference, that the draw's own guard calls the shared
        predicate rather than a re-inlined copy of it (issue 138, fix round
        3): patch `_is_package_path` to refuse everything and a path that
        would otherwise succeed must fail too. A guard that silently
        re-derives the same disjunction instead of calling this function —
        exactly the shape round 2 left in place — would ignore the patch
        and keep succeeding, which is what would make this test catch it."""
        (tmp_path / "PMC1.xml").write_bytes(b"<article/>")
        assert list(sampler.iter_package_articles(tmp_path)) == [("PMC1", b"<article/>")]

        monkeypatch.setattr(sampler, "_is_package_path", lambda path: False)

        with pytest.raises(sampler.PackageError):
            list(sampler.iter_package_articles(tmp_path))

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

    def test_an_article_with_no_pub_date_has_no_year(self):
        assert sampler.article_year(b"<article><front/></article>") is None

    def test_a_year_outside_a_pub_date_is_not_a_publication_year(self):
        """A `<year>` in a reference is not this article's date."""
        xml = b"<article><back><ref><year>1999</year></ref></back></article>"

        assert sampler.article_year(xml) is None

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
        refusal = sampler._validate_args(
            _package_run_args(package=[tmp_path], from_year=1999, to_year=1996)
        )

        assert refusal is not None
        assert "1999" in refusal

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

    def test_the_net_fails_the_exit_code_even_when_the_corpus_is_reportable(self, tmp_path):
        """Mutant F, from a review: `return 0 if ok else 1`, dropping
        `rendition_ok`, left every existing test green, because
        `test_a_target_of_zero_refuses_the_canonical_rendition_name` above
        reaches the net through a *fresh* `--target 0` run, which also
        empties `totals.rows` and makes the corpus's own `ok` False on its
        own — so `ok` alone already produced the right exit code, and the
        mutant was invisible.

        This gives the net a run where the corpus is independently
        reportable — a journal populated by a prior real draw — so `ok` is
        `True` and only `rendition_ok` can account for a non-zero exit."""
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
            "0",
            "-o",
            str(output),
            "--compare-europepmc",
            "2",
        ]
        with mock.patch.object(sys, "argv", net_argv):
            second_code = sampler.main()

        assert second_code != 0
        # The corpus itself is untouched and still reportable — the net is
        # the only thing failing this run.
        assert not output.with_suffix(".unreportable.json").exists()
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

    def _package(self, path: Path, pmcids: list[str], n_figs: int) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
        for pmcid in pmcids:
            (path / f"{pmcid}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
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

    def _package(self, path: Path, pmcids: list[str], n_figs: int) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        figs = "".join(f"<fig id='f{j}'/>" for j in range(n_figs))
        for pmcid in pmcids:
            (path / f"{pmcid}.xml").write_text(
                '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<front><article-meta>"
                "<pub-date pub-type='epub'><year>2024</year></pub-date>"
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
