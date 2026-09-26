// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! What a JATS parse left behind, and what that cost.
//!
//! A JATS reader carries two dozen stacks, depths and flags, and every one of
//! them decides where content is **routed** rather than merely what it looks
//! like. A parse that ends with one of them unbalanced returns a thin article, an
//! article missing its last sections, or an article whose remaining prose was
//! filed as caption text — and, before this module, said nothing at all.
//!
//! **This is a net, not an input check.** A conforming XML parser rejects an
//! unbalanced *document* before the reader returns, so nothing a publisher can
//! deposit reaches these predicates. They fire only when the **parser** is wrong.
//!
//! **It is prospective for most of that class.** The obvious precedents — a
//! nested `<fig>` overwriting its parent, a nested `<caption>` truncating the
//! enclosing one, a `<title>` routed by an ambient test — would each have unwound
//! *clean*: none left residue, which is precisely why all three went undetected
//! until they were found from outside. The one genuine precedent is a sibling
//! port where the same shape stranded a footnote counter above zero, so that
//! every remaining paragraph drained into the footnote branch and was discarded
//! one at a time, in silence — and survived to code review.
//!
//! Two rules hold this module together:
//!
//! * **every field defaults to its clean value**, which is what lets a test name
//!   only the imbalance it is about — and why [`ParseUnwindState::excess_text_buffers`]
//!   counts the *excess* rather than the depth, since the reader's text stack
//!   always holds one buffer. A field holding the raw length would read every
//!   clean parse as broken;
//! * **nothing here fails.** A partial article reported loudly beats no article.

/// The reader's routing state at the moment the parse ended.
///
/// Every field defaults to the value a well-formed, correctly-handled document
/// leaves behind, so a test sets only what it means to be wrong.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ParseUnwindState {
    /// `<sub-article>`/`<response>` elements still open.
    ///
    /// While this is above zero every handler is suppressed, so an imbalance
    /// discards the **remainder of the document**.
    pub nested_article_depth: u32,
    /// `<sec>` builders still on the stack. A section is emitted at its end tag,
    /// so one left open is never emitted at all.
    pub open_sections: u32,
    /// `<fig>` frames still on the stack.
    pub open_figures: u32,
    /// `<table-wrap>` frames still on the stack.
    pub open_tables: u32,
    /// `<caption>` owners still on the stack. While one is open, `<p>` and
    /// `<title>` are caption text rather than the section's prose and heading.
    pub open_captions: u32,
    /// `<inline-formula>`/`<disp-formula>` frames still on the stack.
    ///
    /// A formula emits its one chosen encoding at its end tag, so one left open
    /// never reaches the prose — and, while it is open, the text callback
    /// withholds every cell's text from the rendered table, so the imbalance
    /// costs the rest of that table too.
    pub open_formulas: u32,
    /// `<contrib-group>` role declarations still on the stack. A contributor
    /// inherits the innermost, so a stale entry hands a later `<contrib>` a role
    /// from a group that had closed.
    pub open_contrib_groups: u32,
    /// `<contrib>` frames still on the stack, including the `None` ones a
    /// non-author `<contrib>` pushes.
    ///
    /// An author frame left open is never built, and every `<surname>`,
    /// `<given-names>`, `<collab>` and `<string-name>` read while it is
    /// innermost goes to that stranded builder instead of the contributor it
    /// belongs to; a stranded non-author frame drops those names instead.
    pub open_contribs: u32,
    /// `<def-item>` elements still open.
    ///
    /// **Both directions cost something and they are opposites**: a frame
    /// carrying a word hands it to the next paragraph to arrive anywhere in the
    /// document, a definition's term prefixed onto prose that is not its
    /// definition; a frame carrying none masks the *enclosing* item's term and
    /// suppresses its fold for the rest of the parse, so the article silently
    /// loses a word instead of gaining one.
    ///
    /// Counted rather than grouped with `stuck_flags` because this is a **stack
    /// with a depth**: `stuck_flags` is a list of *names* built by truthiness,
    /// so seven stranded items would report as one name.
    pub open_definition_items: u32,
    /// `<award-group>` frames still on the stack.
    pub open_award_groups: u32,
    /// Funder `<named-content>` elements still open.
    pub open_funder_named_content: u32,
    /// Container headings still open, whose title would be applied to unrelated
    /// prose.
    pub open_container_headings: u32,
    /// Slots reserved by a `<contrib>` that never closed.
    ///
    /// `build_authors()` filters these out without a word, which is a silently
    /// missing contributor. Counted separately from `open_contribs` because the
    /// two can **diverge**: a non-author frame reserves no slot, and a
    /// `<contrib>` naming nobody gives its slot back, so neither number is
    /// derivable from the other.
    pub unfilled_author_slots: u32,
    /// Slots reserved by a `<fig>` that never closed.
    pub unfilled_figure_slots: u32,
    /// The same, for `<table-wrap>`.
    pub unfilled_table_slots: u32,
    /// Buffers on the text stack **above** the one that is always present.
    pub excess_text_buffers: u32,
    /// The element names still on the element stack, outermost first.
    ///
    /// Held as names rather than a depth because this stack answers **parent
    /// lookups** — the second-from-top for a `<label>`'s owner, the walk in
    /// `_graphic_owner` — so a stale entry mis-routes *by element*, and a depth
    /// would not say which. It also answers an *ancestor membership* question,
    /// whose failure mode is worse in kind: a stale `mixed-citation` entry does
    /// not mis-route one element, it makes every later accumulating close merge
    /// into its parent.
    pub open_elements: Vec<String>,
    /// The names of any boolean or single-slot value still set.
    ///
    /// Grouped into one field rather than one each: they all fail the same way
    /// and an operator reads them as a set.
    pub stuck_flags: Vec<String>,
}

/// Describe every imbalance in `state`, one message per imbalance.
///
/// Each message names what the imbalance **cost**, not merely what was left open:
/// *"2 `<fig>` still open"* is not actionable on its own, and *"their figures were
/// never built"* is. One line per imbalance rather than one summary, because an
/// operator greps for one of them.
///
/// **The nested-article line comes first when several fire**: it is the only
/// imbalance that discards the rest of the document rather than the content it was
/// routing, so it is the one to read first.
///
/// A clean unwind yields no messages. Callers log these at ERROR: no well-formed
/// document can produce one, so every message is a claim that the parser itself is
/// wrong.
#[must_use]
pub fn unwind_diagnostics(state: &ParseUnwindState) -> Vec<String> {
    let mut messages: Vec<String> = Vec::new();

    if state.nested_article_depth != 0 {
        messages.push(format!(
            "{} <sub-article>/<response> still open: everything after the imbalance \
             was discarded as nested-article content",
            state.nested_article_depth
        ));
    }
    if state.open_sections != 0 {
        messages.push(format!(
            "{} <sec> still open: a section is emitted at its end tag, so that many \
             sections and their prose never reached the article",
            state.open_sections
        ));
    }
    if state.open_figures != 0 {
        messages.push(format!(
            "{} <fig> still open: their figures were never built",
            state.open_figures
        ));
    }
    if state.open_tables != 0 {
        messages.push(format!(
            "{} <table-wrap> still open: their tables were never built",
            state.open_tables
        ));
    }
    if state.open_captions != 0 {
        messages.push(format!(
            "{} <caption> still open: prose after the imbalance was filed as caption \
             text rather than as the section's",
            state.open_captions
        ));
    }
    if state.open_formulas != 0 {
        messages.push(format!(
            "{} <inline-formula>/<disp-formula> still open: their formulas were never \
             emitted, and every table cell after the imbalance lost its text",
            state.open_formulas
        ));
    }
    if state.open_contrib_groups != 0 {
        messages.push(format!(
            "{} <contrib-group> still open: a later <contrib> inherited its role from a \
             group that had already closed",
            state.open_contrib_groups
        ));
    }
    if state.open_contribs != 0 {
        messages.push(format!(
            "{} <contrib> still open: their contributors were never built, and every \
             contributor name read after the imbalance went to the stranded builder",
            state.open_contribs
        ));
    }
    if state.open_definition_items != 0 {
        messages.push(format!(
            "{} <def-item> still open: their terms were never filed, so any paragraph \
             arriving after the imbalance took the innermost one's term as a prefix — \
             or, where that frame held no term, went without the enclosing item's",
            state.open_definition_items
        ));
    }
    if state.open_award_groups != 0 {
        messages.push(format!(
            "{} <award-group> still open: their awards were never filed, so the article \
             lost that funding outright — its funder, its Funder Registry id and its \
             award number",
            state.open_award_groups
        ));
    }
    if state.open_funder_named_content != 0 {
        messages.push(format!(
            "{} funder <named-content> still open: the next such deposit was read \
             against its neighbour's content-type, so a funder's name may have been \
             stored as its registry id, or its id welded onto the name",
            state.open_funder_named_content
        ));
    }
    if state.open_container_headings != 0 {
        messages.push(format!(
            "{} container heading(s) still open: every later run of unsectioned prose \
             took the innermost one as its section title, so the article carries a \
             heading over prose that is not under it",
            state.open_container_headings
        ));
    }
    if state.unfilled_author_slots != 0 {
        messages.push(format!(
            "{} author slot(s) reserved and never filled: their contributors were never \
             built, and build_authors() dropped the holes",
            state.unfilled_author_slots
        ));
    }
    if state.unfilled_figure_slots != 0 {
        messages.push(format!(
            "{} figure slot(s) reserved and never filled: their figures were never \
             built, and build_figures() dropped the holes",
            state.unfilled_figure_slots
        ));
    }
    if state.unfilled_table_slots != 0 {
        messages.push(format!(
            "{} table slot(s) reserved and never filled: their tables were never built, \
             and build_tables() dropped the holes",
            state.unfilled_table_slots
        ));
    }
    if state.excess_text_buffers != 0 {
        messages.push(format!(
            "{} text buffer(s) left on the stack: text after the imbalance accumulated \
             into the wrong element's buffer",
            state.excess_text_buffers
        ));
    }
    if !state.open_elements.is_empty() {
        messages.push(format!(
            "element stack not unwound ({}): parent lookups (<label>, <graphic>, \
             <caption>, <title>, <article-id>) after the imbalance read the wrong \
             parent, and a stranded <mixed-citation> merged every later element's text \
             into its parent",
            state.open_elements.join(" > ")
        ));
    }
    if !state.stuck_flags.is_empty() {
        messages.push(format!(
            "routing flags still set ({}): content after the imbalance was routed as if \
             those elements were still open",
            state.stuck_flags.join(", ")
        ));
    }

    messages
}
