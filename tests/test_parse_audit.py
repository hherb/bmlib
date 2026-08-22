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

"""Tests for bmlib.fulltext._parse_audit — the JATS end-of-parse audit.

Only the pure half lives here: what an imbalance reads as, given a state. That
the state is *captured* correctly, and that every entry point reaches it, is
pinned at the call site in ``test_jats_parser.py`` — the behaviour is
delivered there, and a state struct that agreed with a capture agreeing with
nothing would pass every test in this file.

Every field defaults to its clean value, so each test below names only the
imbalance it is about. That is the whole reason for the struct: no well-formed
document can reach this code, so handing it the residue a defect would leave
is the only way to exercise it.
"""

import dataclasses

from bmlib.fulltext._parse_audit import ParseUnwindState, unwind_diagnostics


class TestACleanUnwindReportsNothing:
    def test_the_default_state_is_silent(self):
        assert unwind_diagnostics(ParseUnwindState()) == []

    def test_every_field_defaults_to_its_clean_value(self):
        """The default *is* the clean state, which is what the tests below assume.

        Without this, a field defaulting to something the audit reads as an
        imbalance would make every single-field test below report two
        diagnostics, and a field defaulting to something it can never read as
        an imbalance would make its own test vacuous.
        """
        defaults = {
            f.name: getattr(ParseUnwindState(), f.name)
            for f in dataclasses.fields(ParseUnwindState)
        }

        assert all(not value for value in defaults.values()), defaults


class TestEachImbalanceIsReportedWithItsCost:
    """One diagnostic per imbalance, each naming what the imbalance *cost*.

    "A stack was left open" is not actionable on its own; "their figures were
    never built" is. Each assertion below names a phrase unique to its own
    line — a bare count or the word "open" matches most of the others.
    """

    def test_a_nested_article_left_open_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(nested_article_depth=1))

        assert "discarded" in message

    def test_a_section_left_open_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(open_sections=2))

        assert "2" in message and "<sec>" in message

    def test_a_figure_left_open_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(open_figures=1))

        assert "<fig>" in message

    def test_a_table_left_open_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(open_tables=1))

        assert "<table-wrap>" in message

    def test_a_caption_left_open_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(open_captions=1))

        assert "<caption>" in message

    def test_a_contrib_group_left_open_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(open_contrib_groups=1))

        assert "<contrib-group>" in message

    def test_an_unfilled_figure_slot_is_reported(self):
        """The hole ``build_figures()`` filters out without a word.

        Its docstring calls that filter unreachable. If it ever is reached the
        article silently loses a figure, so the audit says so rather than
        letting the filter absorb it.
        """
        [message] = unwind_diagnostics(ParseUnwindState(unfilled_figure_slots=1))

        assert "never built" in message

    def test_an_unfilled_table_slot_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(unfilled_table_slots=1))

        assert "never built" in message

    def test_a_leftover_text_buffer_is_reported(self):
        [message] = unwind_diagnostics(ParseUnwindState(excess_text_buffers=1))

        assert "text buffer" in message

    def test_an_unwound_element_stack_names_the_elements(self):
        """The names, not the depth: they are what identifies the defect.

        ``element_stack`` answers parent lookups — ``[-2]`` for a ``<label>``'s
        owner, the walk in ``_graphic_owner`` — so a stale entry mis-routes by
        *element*, and a bare depth would not say which.
        """
        [message] = unwind_diagnostics(ParseUnwindState(open_elements=("fig", "caption")))

        assert "fig > caption" in message

    def test_a_stuck_routing_flag_names_the_flags(self):
        [message] = unwind_diagnostics(ParseUnwindState(stuck_flags=("in_abstract", "in_ref")))

        assert "in_abstract" in message and "in_ref" in message


class TestSeveralImbalancesAreReportedSeparately:
    def test_two_imbalances_give_two_lines(self):
        """One line per imbalance, not one summary — an operator greps for one.

        Also the negative control on the per-field tests above: each asserts a
        list of exactly one, which would hold just as well if the function
        returned the first diagnostic it found and stopped.
        """
        messages = unwind_diagnostics(ParseUnwindState(open_figures=1, open_sections=1))

        assert len(messages) == 2

    def test_the_nested_article_comes_first(self):
        """It is the one imbalance that discards *everything* after it.

        The others cost the content they were routing; this one costs the rest
        of the document, so it is the line to read first when several fire.
        """
        messages = unwind_diagnostics(
            ParseUnwindState(open_sections=1, nested_article_depth=1, open_figures=1)
        )

        assert "discarded" in messages[0]
