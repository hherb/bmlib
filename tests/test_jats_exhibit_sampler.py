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

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
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

        Four sections can print that phrase, so a bare ``"NOT MEASURED" in
        out`` passes for a run in which *this* one printed a rate over rows
        that never carried the counter.
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
