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

//! PubMed XML reader — the oracle and the named tests.
//!
//! The corpus (80 cases) diffs the reader against Python field by field. The
//! named tests state why the reader renders Markdown at all, and which
//! divergences from `ElementTree` are deliberate.

use bmlib::publications::fetchers::pubmed::{
    author_name, day_term, element_text, escape_markdown, format_abstract_markdown,
    parse_article_set, parse_grants, parse_pubdate, text_with_formatting,
};
use chrono::NaiveDate;
use roxmltree::{Document, Node};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/pubmed_cases.json");
const EXPECTED: &str = include_str!("data/pubmed_expected.json");

/// Parse a fragment and run `f` on its root element.
fn with_root<T>(xml: &str, f: impl FnOnce(Node<'_, '_>) -> T) -> T {
    let document = Document::parse(xml).expect("fragment parses");
    f(document.root_element())
}

fn record_json(record: &bmlib::publications::models::FetchedRecord) -> Value {
    json!({
        "title": record.title,
        "source": record.source,
        "doi": record.doi,
        "pmid": record.pmid,
        "pmc_id": record.pmc_id,
        "abstract": record.abstract_text,
        "authors": record.authors,
        "journal": record.journal,
        "publication_date": record.publication_date,
        "keywords": record.keywords,
        "publication_types": record.publication_types,
        "fulltext_sources": record.fulltext_sources,
        "grants": record.grants.iter().map(|g| g.to_json()).collect::<Vec<_>>(),
        "author_affiliations": record
            .author_affiliations
            .iter()
            .map(|a| a.to_json())
            .collect::<Vec<_>>(),
    })
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    let xml = args.get("xml").and_then(Value::as_str);

    match fn_name {
        "escape_markdown" => json!(escape_markdown(args["text"].as_str().unwrap_or_default())),
        "text_with_formatting" => with_root(xml.unwrap_or_default(), |root| {
            json!(text_with_formatting(Some(root)))
        }),
        "abstract_markdown" => {
            with_root(
                xml.unwrap_or_default(),
                |root| match format_abstract_markdown(Some(root)) {
                    Some(text) => json!(text),
                    None => Value::Null,
                },
            )
        }
        "parse_pubdate" => with_root(xml.unwrap_or_default(), |root| {
            match parse_pubdate(Some(root)) {
                Some(text) => json!(text),
                None => Value::Null,
            }
        }),
        "author_name" => with_root(xml.unwrap_or_default(), |root| match author_name(root) {
            Some(name) => json!(name),
            None => Value::Null,
        }),
        "grants" => with_root(xml.unwrap_or_default(), |root| {
            json!(parse_grants(root)
                .iter()
                .map(|g| g.to_json())
                .collect::<Vec<_>>())
        }),
        "parse" => {
            let records = parse_article_set(xml.unwrap_or_default()).expect("document parses");
            json!(records.iter().map(record_json).collect::<Vec<_>>())
        }
        "day_term" => {
            let date =
                NaiveDate::parse_from_str(args["date"].as_str().unwrap_or_default(), "%Y-%m-%d")
                    .expect("date");
            json!(day_term(date))
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

// ---------------------------------------------------------------------------
// Markdown escaping
// ---------------------------------------------------------------------------

/// Titles and abstracts are **declared Markdown**, so the prose they are built
/// from is escaped on the way in. Without it, declaring the field Markdown would
/// itself corrupt values that were fine before — the star alleles in
/// `CYP2C19 (*1, *2, *3)` would render as emphasis.
#[test]
fn declaring_a_field_markdown_escapes_the_prose_first() {
    assert_eq!(escape_markdown("a*b"), "a\\*b");
    assert_eq!(
        escape_markdown("CYP2C19 (*1, *2, *3)"),
        "CYP2C19 (\\*1, \\*2, \\*3)"
    );
    assert_eq!(escape_markdown("a\\b"), "a\\\\b");
    assert_eq!(escape_markdown("a`b"), "a\\`b");
    assert_eq!(escape_markdown("\\`*~^"), "\\\\\\`\\*\\~\\^");
}

/// The escape set is **deliberately narrow**, and the two exclusions are
/// measured rather than stylistic: intraword `_` is inert in CommonMark, so gene
/// names like `TP53_R175H` are already safe, and a bare `[...]` is not a link
/// without a following `(...)`. Escaping both churned 4.3% of fields and fixed
/// nothing further.
#[test]
fn the_escape_set_is_narrow_on_purpose() {
    assert_eq!(escape_markdown("TP53_R175H"), "TP53_R175H");
    assert_eq!(escape_markdown("[1,2]"), "[1,2]");
    assert_eq!(escape_markdown("plain text"), "plain text");
    assert_eq!(escape_markdown(""), "");
}

/// `~` and `^` are in the set because **this module made them meaningful** —
/// they are the `<sub>`/`<sup>` markers. An unescaped tilde pair silently
/// subscripts everything between them under a Pandoc renderer, and `"AUC ~
/// 0.80"` is ordinary scientific prose.
#[test]
fn the_markers_this_module_emits_are_escaped_in_the_source() {
    assert_eq!(escape_markdown("AUC ~ 0.80"), "AUC \\~ 0.80");
    assert_eq!(escape_markdown("(~88%)"), "(\\~88%)");
    assert_eq!(escape_markdown("m^2"), "m\\^2");
}

// ---------------------------------------------------------------------------
// Inline markup
// ---------------------------------------------------------------------------

/// Scientific prose depends on `sub`/`sup`: without them a chemical formula and
/// an exponent both flatten into an ambiguous `"CO2"` / `"m2"`.
#[test]
fn sub_and_sup_become_markdown() {
    assert_eq!(
        with_root("<T>CO<sub>2</sub> levels</T>", |r| text_with_formatting(
            Some(r)
        )),
        "CO~2~ levels"
    );
    assert_eq!(
        with_root("<T>m<sup>2</sup></T>", |r| text_with_formatting(Some(r))),
        "m^2^"
    );
    assert_eq!(
        with_root("<T>An <i>italic</i> word</T>", |r| text_with_formatting(
            Some(r)
        )),
        "An *italic* word"
    );
    assert_eq!(
        with_root("<T>A <b>bold</b> word</T>", |r| text_with_formatting(Some(
            r
        ))),
        "A **bold** word"
    );
}

/// `u`/`underline` is **deliberately absent** from the markup table, so it falls
/// through undecorated. Markdown has no underline: `__x__` is *strong* emphasis,
/// so mapping `<u>` to it renders underlined text identically to `<b>` — the
/// same collapse the table exists to prevent for `sub`/`sup`, except that it also
/// asserts something false about the source.
#[test]
fn underline_is_left_undecorated_rather_than_claimed_as_bold() {
    assert_eq!(
        with_root("<T><u>under</u>lined</T>", |r| text_with_formatting(Some(
            r
        ))),
        "underlined"
    );
    assert_eq!(
        with_root("<T><x>odd</x>text</T>", |r| text_with_formatting(Some(r))),
        "oddtext"
    );
    // And it must not silently become `**`.
    assert!(!with_root("<T><u>u</u></T>", |r| text_with_formatting(Some(r))).contains('*'));
}

/// The edge whitespace of a formatted run is emitted **outside** its markers.
///
/// Two failure modes this avoids, and the first was upstream's: stripping at
/// every recursion level welds the runs into `**Randomised****trial**`, and
/// keeping the space inside gives `**Randomised **`, which CommonMark does not
/// emphasise either because a delimiter must be adjacent to non-whitespace.
#[test]
fn a_formatted_runs_edge_space_lands_outside_its_markers() {
    assert_eq!(
        with_root("<T><b>Randomised </b><b>trial</b></T>", |r| {
            text_with_formatting(Some(r))
        }),
        "**Randomised** **trial**"
    );
    assert_eq!(
        with_root("<T><b>Bold </b>tail</T>", |r| text_with_formatting(Some(r))),
        "**Bold** tail"
    );
    // A run whose *content* both starts with a space and is preceded by one
    // document space yields **two** spaces: the document's is kept and the
    // run's is moved outside its markers. Python does exactly this, so it is
    // reproduced rather than tidied — "move the edge space out" is the rule,
    // and collapsing the pair afterwards would be a second rule that no
    // docstring states. (Pinned by the oracle's
    // `text_with_formatting/leading-space-in-run`.)
    assert_eq!(
        with_root("<T>lead <b> bold</b></T>", |r| text_with_formatting(Some(
            r
        ))),
        "lead  **bold**"
    );
    // With no document space before it, the moved space is the only one.
    assert_eq!(
        with_root("<T>lead<b> bold</b></T>", |r| text_with_formatting(Some(r))),
        "lead **bold**"
    );
    assert_eq!(
        with_root("<T>lead <b>bold</b></T>", |r| text_with_formatting(Some(r))),
        "lead **bold**"
    );
}

/// An **empty run renders no markers**: `****` is not emphasis, it is stray
/// punctuation in the middle of a sentence.
#[test]
fn an_empty_run_emits_no_markers() {
    assert_eq!(
        with_root("<T>a<b></b>b</T>", |r| text_with_formatting(Some(r))),
        "ab"
    );
    assert_eq!(
        with_root("<T>a<b> </b>b</T>", |r| text_with_formatting(Some(r))),
        "a b"
    );
}

/// The walker visits mixed content completely — an element's own text, each
/// child's subtree, and that child's tail — so a nested run loses nothing.
#[test]
fn the_walker_visits_all_mixed_content() {
    assert_eq!(
        with_root("<T>a<b>x<i>y</i>z</b>w</T>", |r| text_with_formatting(
            Some(r)
        )),
        "a**x*y*z**w"
    );
    assert_eq!(
        with_root("<T>a<b>x</b>tail</T>", |r| text_with_formatting(Some(r))),
        "a**x**tail"
    );
}

/// Two adjacent elements each keep their own text, and neither collects the
/// other's.
///
/// This is also where the tail scan's `is_element` stop is shown to be
/// **unreachable**: roxmltree guarantees a single text node in a gap, so the
/// scan meets the next element immediately after taking that node. The assertion
/// below is what pins the *behaviour*; the comment on `tail_text` records that
/// removing the stop changes nothing observable.
#[test]
fn adjacent_elements_do_not_collect_each_others_text() {
    assert_eq!(
        with_root("<T><b>x</b><i>y</i></T>", |r| text_with_formatting(Some(r))),
        "**x***y*"
    );
    assert_eq!(
        with_root("<T><b>x</b>tail<i>y</i></T>", |r| text_with_formatting(
            Some(r)
        )),
        "**x**tail*y*"
    );
}

/// The **markers are the only unescaped Markdown** in the result: text inside a
/// run is escaped, and so is a tail.
#[test]
fn only_the_emitted_markers_are_unescaped() {
    assert_eq!(
        with_root("<T>a<b>x*y</b>z</T>", |r| text_with_formatting(Some(r))),
        "a**x\\*y**z"
    );
    assert_eq!(
        with_root("<T><b>x</b>a*b</T>", |r| text_with_formatting(Some(r))),
        "**x**a\\*b"
    );
}

/// The result is stripped **once**, at the end — leading and trailing document
/// whitespace does not reach the field.
#[test]
fn the_result_is_stripped_once_at_the_end() {
    assert_eq!(
        with_root("<T>  spaced  </T>", |r| text_with_formatting(Some(r))),
        "spaced"
    );
    assert_eq!(with_root("<T/>", |r| text_with_formatting(Some(r))), "");
    assert_eq!(with_root("<T></T>", |r| text_with_formatting(Some(r))), "");
}

/// `element_text` reads **only the element's own text**, which is what Python's
/// `el.text` does. It is not interchangeable with the walker: for an element
/// holding markup, this truncates at the first child — silently, which is why
/// the fields that matter use the walker.
#[test]
fn element_text_truncates_at_the_first_child() {
    assert_eq!(
        with_root("<T>before<b>after</b></T>", |r| element_text(Some(r))),
        Some("before".to_string())
    );
    assert_eq!(
        with_root("<T>all of it</T>", |r| element_text(Some(r))),
        Some("all of it".to_string())
    );
    assert_eq!(element_text(None), None);
}

// ---------------------------------------------------------------------------
// The abstract
// ---------------------------------------------------------------------------

/// The label comes from `Label` **or** `NlmCategory`: PubMed uses either, and
/// reading only `Label` drops the heading from every section labelled the other
/// way, running it into its neighbour.
#[test]
fn a_section_label_comes_from_either_attribute() {
    assert_eq!(
        with_root(
            r#"<Abstract><AbstractText Label="Methods">B</AbstractText></Abstract>"#,
            |r| format_abstract_markdown(Some(r))
        ),
        Some("**METHODS:** B".to_string())
    );
    assert_eq!(
        with_root(
            r#"<Abstract><AbstractText NlmCategory="Results">B</AbstractText></Abstract>"#,
            |r| format_abstract_markdown(Some(r))
        ),
        Some("**RESULTS:** B".to_string())
    );
    // `Label` wins when both are present.
    assert_eq!(
        with_root(
            r#"<Abstract><AbstractText Label="L" NlmCategory="Results">B</AbstractText></Abstract>"#,
            |r| format_abstract_markdown(Some(r))
        ),
        Some("**L:** B".to_string())
    );
    // A blank label falls through to the category.
    assert_eq!(
        with_root(
            r#"<Abstract><AbstractText Label="  " NlmCategory="Results">B</AbstractText></Abstract>"#,
            |r| format_abstract_markdown(Some(r))
        ),
        Some("**RESULTS:** B".to_string())
    );
}

/// `UNASSIGNED` and `UNLABELLED` mean "this section has no label"; rendering
/// them as headings would put the word in front of the prose.
#[test]
fn an_unassigning_category_is_not_a_heading() {
    for category in ["UNASSIGNED", "UNLABELLED", "unassigned"] {
        assert_eq!(
            with_root(
                &format!(
                    r#"<Abstract><AbstractText NlmCategory="{category}">B</AbstractText></Abstract>"#
                ),
                |r| format_abstract_markdown(Some(r))
            ),
            Some("B".to_string()),
            "{category}"
        );
    }
}

/// Sections are separated by a **blank line**, so the structure survives into
/// Markdown rather than running the sections together.
#[test]
fn sections_are_separated_by_a_blank_line() {
    assert_eq!(
        with_root(
            r#"<Abstract><AbstractText Label="A">one</AbstractText><AbstractText Label="B">two</AbstractText></Abstract>"#,
            |r| format_abstract_markdown(Some(r))
        ),
        Some("**A:** one\n\n**B:** two".to_string())
    );
}

/// An empty document text is `None`, **not** an empty string: `abstract` is an
/// `Option`, and `""` would read as "this paper has a blank abstract" rather
/// than "none was given" — and would block the storage layer's `COALESCE`
/// fill-in from another source for ever.
#[test]
fn an_abstract_with_no_text_is_none() {
    assert_eq!(format_abstract_markdown(None), None);
    assert_eq!(
        with_root("<Abstract></Abstract>", |r| format_abstract_markdown(Some(
            r
        ))),
        None
    );
    assert_eq!(
        with_root("<Abstract><AbstractText></AbstractText></Abstract>", |r| {
            format_abstract_markdown(Some(r))
        }),
        None
    );
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

/// Both numeric and text months are read, and the day is zero-padded.
#[test]
fn numeric_and_text_months_both_parse() {
    assert_eq!(
        with_root(
            "<PubDate><Year>2024</Year><Month>03</Month><Day>09</Day></PubDate>",
            |r| parse_pubdate(Some(r))
        ),
        Some("2024-03-09".to_string())
    );
    assert_eq!(
        with_root(
            "<PubDate><Year>2024</Year><Month>Jan</Month><Day>2</Day></PubDate>",
            |r| parse_pubdate(Some(r))
        ),
        Some("2024-01-02".to_string())
    );
}

/// A season is **dropped rather than emitted**: `"2024-Winter"` is not a date,
/// and a partial `"2024"` is honest about what the record said.
#[test]
fn a_season_drops_the_month_rather_than_inventing_one() {
    assert_eq!(
        with_root(
            "<PubDate><Year>2024</Year><Month>Winter</Month></PubDate>",
            |r| parse_pubdate(Some(r))
        ),
        Some("2024".to_string())
    );
    assert_eq!(
        with_root("<PubDate><Year>2024</Year></PubDate>", |r| parse_pubdate(
            Some(r)
        )),
        Some("2024".to_string())
    );
    assert_eq!(
        with_root(
            "<PubDate><Year>2024</Year><Month>03</Month></PubDate>",
            |r| parse_pubdate(Some(r))
        ),
        Some("2024-03".to_string())
    );
}

/// `MedlineDate` is the fallback and contributes **only its first four
/// characters** — `"2024 Jan-Feb"` is a range, and the year is the part a
/// `YYYY-MM-DD` field can hold.
#[test]
fn a_medline_date_contributes_only_its_year() {
    assert_eq!(
        with_root(
            "<PubDate><MedlineDate>2024 Jan-Feb</MedlineDate></PubDate>",
            |r| parse_pubdate(Some(r))
        ),
        Some("2024".to_string())
    );
    // Too short to hold a year, so nothing.
    assert_eq!(
        with_root("<PubDate><MedlineDate>24</MedlineDate></PubDate>", |r| {
            parse_pubdate(Some(r))
        }),
        None
    );
}

// ---------------------------------------------------------------------------
// Authors and grants
// ---------------------------------------------------------------------------

/// A `<CollectiveName>` consortium has no personal name to render, so it is
/// `None` — and its affiliations are dropped with it, deliberately: the
/// affiliation table's `author` is contracted to match a name in `authors`,
/// which a consortium is absent from.
#[test]
fn a_collective_name_has_no_author_name() {
    assert_eq!(
        with_root(
            "<Author><CollectiveName>CONSORT Group</CollectiveName></Author>",
            author_name
        ),
        None
    );
    assert_eq!(
        with_root(
            "<Author><LastName>Smith</LastName><ForeName>Jane</ForeName></Author>",
            author_name
        ),
        Some("Smith, Jane".to_string())
    );
    // A last name alone renders alone, with no trailing comma.
    assert_eq!(
        with_root("<Author><LastName>Smith</LastName></Author>", |r| {
            author_name(r)
        }),
        Some("Smith".to_string())
    );
    // `Initials` is not `ForeName`, so it contributes nothing.
    assert_eq!(
        with_root(
            "<Author><LastName>Smith</LastName><Initials>J</Initials></Author>",
            author_name
        ),
        Some("Smith".to_string())
    );
}

/// PubMed really does repeat a `<Grant>` block verbatim — 31 of 575 entries
/// across 200 NIH-funded records, affecting 14 of them — and stored as separate
/// rows those inflate every count of a paper's funders, with no way for a reader
/// to tell PubMed's repetition from a genuine second award.
#[test]
fn an_exact_repeat_is_collapsed() {
    let grants = with_root(
        "<Article><GrantList>\
         <Grant><Agency>N</Agency><GrantID>R</GrantID></Grant>\
         <Grant><Agency>N</Agency><GrantID>R</GrantID></Grant>\
         </GrantList></Article>",
        parse_grants,
    );
    assert_eq!(grants.len(), 1);
}

/// `<Acronym>` is read by **neither the key nor the row**. It is an abbreviation
/// of `<Agency>` for one funder rather than an independent fact, so two entries
/// alike in agency, id and country but differing in acronym are the same award.
#[test]
fn a_differing_acronym_is_still_the_same_award() {
    let grants = with_root(
        "<Article><GrantList>\
         <Grant><Agency>N</Agency><GrantID>R</GrantID><Acronym>HL</Acronym></Grant>\
         <Grant><Agency>N</Agency><GrantID>R</GrantID><Acronym>GM</Acronym></Grant>\
         </GrantList></Article>",
        parse_grants,
    );
    assert_eq!(grants.len(), 1, "the acronym must not enter the key");
}

/// Two grants differing in **any** stored field are two grants.
#[test]
fn a_differing_field_makes_two_grants() {
    let grants = with_root(
        "<Article><GrantList>\
         <Grant><Agency>N</Agency><GrantID>R1</GrantID></Grant>\
         <Grant><Agency>N</Agency><GrantID>R2</GrantID></Grant>\
         </GrantList></Article>",
        parse_grants,
    );
    assert_eq!(grants.len(), 2);
}

/// A grant naming **neither** an agency nor an award id identifies no award;
/// storing it would put an empty row in front of anyone counting funders.
#[test]
fn a_grant_identifying_nothing_is_skipped() {
    let grants = with_root(
        "<Article><GrantList>\
         <Grant><Country>US</Country></Grant>\
         <Grant></Grant>\
         <Grant><Agency>X</Agency></Grant>\
         </GrantList></Article>",
        parse_grants,
    );
    assert_eq!(grants.len(), 1);
    assert_eq!(grants[0].agency.as_deref(), Some("X"));
}

// ---------------------------------------------------------------------------
// The whole document
// ---------------------------------------------------------------------------

/// A `<PubmedBookArticle>` yields a record that is **almost entirely empty**, and
/// it is yielded rather than skipped.
///
/// The shape differs (`<BookDocument>`, not `<MedlineCitation>`), so nothing
/// matches the paths this reader looks for — but the record still exists, because
/// reconciliation counts **what the server handed over**. Skipping the element
/// would make `delivered` lower than the count PubMed promised and report a
/// phantom shortfall on every day carrying a book chapter. That is exactly what
/// `reconcile_delivery`'s `delivered` parameter documents.
#[test]
fn a_book_article_yields_an_empty_record_rather_than_being_skipped() {
    let records = parse_article_set(
        "<PubmedArticleSet><PubmedBookArticle><BookDocument/></PubmedBookArticle></PubmedArticleSet>",
    )
    .expect("parses");
    assert_eq!(
        records.len(),
        1,
        "the delivered element must still be counted"
    );
    assert_eq!(records[0].title, "");
    assert_eq!(records[0].pmid, None);

    // Two papers and a book yields three records, in document order.
    let records = parse_article_set(
        "<PubmedArticleSet>\
         <PubmedArticle><MedlineCitation><PMID>1</PMID></MedlineCitation></PubmedArticle>\
         <PubmedBookArticle><BookDocument/></PubmedBookArticle>\
         <PubmedArticle><MedlineCitation><PMID>2</PMID></MedlineCitation></PubmedArticle>\
         </PubmedArticleSet>",
    )
    .expect("parses");
    assert_eq!(records.len(), 3);
    assert_eq!(records[0].pmid.as_deref(), Some("1"));
    assert_eq!(records[1].pmid, None, "the book article");
    assert_eq!(records[2].pmid.as_deref(), Some("2"));
}

/// The XML layer **reports what it refuses** rather than reading it as an empty
/// set. The plan's §3 names this the mapping task's unknown: expat and
/// `roxmltree` do not reject exactly the same documents, and a document this
/// layer refuses is a day that must not be recorded as complete.
#[test]
fn the_xml_layer_reports_what_it_refuses() {
    // A malformed document is an error, not an empty result.
    let err = parse_article_set("<PubmedArticleSet><PubmedArticle></PubmedArticleSet>")
        .expect_err("an unbalanced document must be refused");
    assert!(!err.is_empty());

    // An empty set is genuinely empty, and distinct from a refusal.
    let records = parse_article_set("<PubmedArticleSet></PubmedArticleSet>").expect("parses");
    assert!(records.is_empty());
}

/// Publication types are **trimmed**, and an empty one is dropped: the free
/// Tier 1 quality filter classifies study design from these, so a whitespace
/// entry would read as a study type that is not one.
#[test]
fn publication_types_are_trimmed_and_empties_dropped() {
    let records = parse_article_set(
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>\
         <PublicationTypeList>\
         <PublicationType>Journal Article</PublicationType>\
         <PublicationType>  RCT  </PublicationType>\
         <PublicationType>   </PublicationType>\
         </PublicationTypeList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>",
    )
    .expect("parses");
    assert_eq!(
        records[0].publication_types,
        vec!["Journal Article".to_string(), "RCT".to_string()]
    );
}

/// The date term is `[Date - Publication]`, **not** `[EDAT]`. The choice is
/// load-bearing: it is the field bmlib syncs by, and the two disagree by orders
/// of magnitude on exactly the days this module has to handle.
#[test]
fn the_day_term_uses_the_publication_date_field() {
    assert_eq!(
        day_term(NaiveDate::from_ymd_opt(2024, 6, 10).expect("date")),
        "(\"2024/06/10\"[Date - Publication])"
    );
}
