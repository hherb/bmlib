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

"""What a JATS parse left behind, and what that cost (issue #134).

``_JATSHandler`` carries a dozen stacks, depths and flags, and every one of
them decides where content is *routed* rather than merely what it looks like.
A parse that ends with one of them unbalanced returns a thin article, an
article missing its last sections, or an article whose remaining prose was
filed as caption text — and, before this module, said nothing at all.

**This is a net, not an input check.** ``expat`` rejects an unbalanced
*document* before :meth:`~bmlib.fulltext.jats_parser.JATSParser.parse`
returns, so nothing a publisher can deposit reaches these predicates. They
fire only when the *parser* is wrong — which is not a hypothetical class here:
#115 (a nested ``<fig>`` overwrote its parent), #123 (a nested ``<caption>``
truncated the enclosing one) and #130 are all stack-handling defects, and in
the sibling Swift port the same shape stranded a footnote counter above zero
so that every remaining paragraph in the document drained into the footnote
branch and was discarded, one at a time, in silence. It survived to code
review.

Two rules hold this module together.

**The state is a struct, and every field defaults to its clean value.** That
is what lets a test name only the imbalance it is about, and it is why
:attr:`ParseUnwindState.excess_text_buffers` counts the *excess* rather than
the depth: the handler's ``text_stack`` always holds one buffer, so a field
holding the raw length would read every clean parse as broken and every
single-field test would report two diagnostics.

**Nothing here raises.** A partial article reported loudly beats no article —
which is issue #129's mistake in the other direction, where one malformed
attribute propagated out of a SAX callback and the tier chain swallowed the
whole document.

Adding a stack or a routing flag to the handler means adding a field here.
The audit is the one place that has to know about all of them, which is why
it is its own module rather than a helper inside a 2,000-line parser.

Private, and stdlib-only: nothing outside ``bmlib.fulltext`` has asked for
this, and ``fulltext`` must import on a core install (issue #64).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParseUnwindState:
    """The handler's routing state at the moment the parse ended.

    Every field defaults to the value a well-formed, correctly-handled
    document leaves behind, so a caller — in practice a test — sets only what
    it means to be wrong.

    Attributes:
        nested_article_depth: ``<sub-article>``/``<response>`` elements still
            open. While this is above zero every handler is suppressed, so an
            imbalance discards the remainder of the document.
        open_sections: ``<sec>`` builders still on the stack. A section is
            emitted at its end tag, so one left open is never emitted at all.
        open_figures: ``<fig>`` frames still on the stack.
        open_tables: ``<table-wrap>`` frames still on the stack.
        open_captions: ``<caption>`` owners still on the stack. While one is
            open, ``<p>`` and ``<title>`` are caption text rather than the
            section's prose and heading.
        open_contrib_groups: ``<contrib-group>`` role declarations still on
            the stack. A contributor inherits the innermost, so a stale entry
            hands a later ``<contrib>`` a role from a group that had closed.
        unfilled_figure_slots: Slots reserved by a ``<fig>`` that never
            closed. ``build_figures()`` filters these out without a word,
            which is a silently missing figure.
        unfilled_table_slots: The same, for ``<table-wrap>``.
        excess_text_buffers: Buffers on ``text_stack`` *above* the one that is
            always present. Text after the imbalance accumulated into the
            wrong element's buffer.
        open_elements: The element names still on ``element_stack``, outermost
            first. Held as names rather than a depth because this stack
            answers parent lookups — ``[-2]`` for a ``<label>``'s owner, the
            walk in ``_graphic_owner`` — so a stale entry mis-routes *by
            element*, and a depth would not say which.
        stuck_flags: The names of any boolean or single-slot builder still
            set. Grouped into one field rather than given one field each: they
            all fail the same way and an operator reads them as a set.
    """

    nested_article_depth: int = 0
    open_sections: int = 0
    open_figures: int = 0
    open_tables: int = 0
    open_captions: int = 0
    open_contrib_groups: int = 0
    unfilled_figure_slots: int = 0
    unfilled_table_slots: int = 0
    excess_text_buffers: int = 0
    open_elements: tuple[str, ...] = ()
    stuck_flags: tuple[str, ...] = field(default_factory=tuple)


def unwind_diagnostics(state: ParseUnwindState) -> list[str]:
    """Describe every imbalance in ``state``, one message per imbalance.

    Each message names what the imbalance *cost*, not merely what was left
    open: "2 ``<fig>`` still open" is not actionable on its own, and "their
    figures were never built" is. One line per imbalance rather than one
    summary, because an operator greps for one of them.

    The nested-article line comes first when several fire. It is the only
    imbalance that discards the *rest of the document* rather than the content
    it was routing, so it is the one to read first.

    Args:
        state: The handler's routing state at the end of the parse.

    Returns:
        The diagnostics, empty for a clean unwind. Callers log these at ERROR:
        no well-formed document can produce one, so every message is a claim
        that bmlib itself is wrong.
    """
    messages: list[str] = []

    if state.nested_article_depth:
        messages.append(
            f"{state.nested_article_depth} <sub-article>/<response> still open: "
            "everything after the imbalance was discarded as nested-article content"
        )
    if state.open_sections:
        messages.append(
            f"{state.open_sections} <sec> still open: a section is emitted at its "
            "end tag, so that many sections and their prose never reached the article"
        )
    if state.open_figures:
        messages.append(f"{state.open_figures} <fig> still open: their figures were never built")
    if state.open_tables:
        messages.append(
            f"{state.open_tables} <table-wrap> still open: their tables were never built"
        )
    if state.open_captions:
        messages.append(
            f"{state.open_captions} <caption> still open: prose after the imbalance "
            "was filed as caption text rather than as the section's"
        )
    if state.open_contrib_groups:
        messages.append(
            f"{state.open_contrib_groups} <contrib-group> still open: a later "
            "<contrib> inherited its role from a group that had already closed"
        )
    if state.unfilled_figure_slots:
        messages.append(
            f"{state.unfilled_figure_slots} figure slot(s) reserved and never filled: "
            "their figures were never built, and build_figures() dropped the holes"
        )
    if state.unfilled_table_slots:
        messages.append(
            f"{state.unfilled_table_slots} table slot(s) reserved and never filled: "
            "their tables were never built, and build_tables() dropped the holes"
        )
    if state.excess_text_buffers:
        messages.append(
            f"{state.excess_text_buffers} text buffer(s) left on the stack: text after "
            "the imbalance accumulated into the wrong element's buffer"
        )
    if state.open_elements:
        messages.append(
            "element stack not unwound (" + " > ".join(state.open_elements) + "): "
            "<label> and <graphic> owner lookups after the imbalance read the wrong parent"
        )
    if state.stuck_flags:
        messages.append(
            "routing flags still set (" + ", ".join(state.stuck_flags) + "): content "
            "after the imbalance was routed as if those elements were still open"
        )

    return messages
