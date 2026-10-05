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

//! The JATS reader, against Python's own output for 60 committed documents.
//!
//! `oracle/dump_jats.py` renders the **whole** `JATSArticle` — every field of
//! every article, not the assertions one test happened to make — and this diffs
//! that rendering field by field, so a failure names the path that diverged
//! rather than printing two blobs.
//!
//! The renderer below mirrors `dump_jats.py`'s, including the three values that
//! are Python *properties* rather than dataclass fields: `full_name` and
//! `is_named` on a contributor, and `formatted_citation` on a reference. They
//! are derived here for the same reason the oracle derives them there — the
//! Rust models carry no such accessor — and [`author_full_name`] /
//! [`author_is_named`] are the reader's own functions rather than copies, so a
//! divergence between the reader's "is this contributor named?" and the
//! oracle's would show up as a diff.

use bmlib::fulltext::jats_reader::{author_full_name, author_is_named, parse, parse_audited};
use bmlib::fulltext::models::{
    join_citation_parts, JATSArticle, JATSAuthorInfo, JATSBodySection, JATSReferenceInfo,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/jats_cases.json");
const EXPECTED: &str = include_str!("data/jats_expected.json");

// ---------------------------------------------------------------------------
// The renderer, mirroring oracle/dump_jats.py
// ---------------------------------------------------------------------------

fn render_author(author: &JATSAuthorInfo) -> Value {
    json!({
        "surname": author.surname,
        "given_names": author.given_names,
        "affiliations": author.affiliations,
        "collab": author.collab,
        "string_name": author.string_name,
        "full_name": author_full_name(author),
        "is_named": author_is_named(author),
    })
}

fn render_body(section: &JATSBodySection) -> Value {
    json!({
        "title": section.title,
        "paragraphs": section.paragraphs,
        "subsections": section.subsections.iter().map(render_body).collect::<Vec<_>>(),
    })
}

/// Python's `JATSReferenceInfo._volume_info`, the `volume(issue):locator` run.
fn volume_info(reference: &JATSReferenceInfo) -> String {
    let mut volume_info = String::new();
    if !reference.volume.is_empty() {
        volume_info = reference.volume.clone();
        if !reference.issue.is_empty() {
            volume_info.push_str(&format!("({})", reference.issue));
        }
    }
    let mut page_range = reference.first_page.clone();
    if !page_range.is_empty() && !reference.last_page.is_empty() {
        page_range.push_str(&format!("-{}", reference.last_page));
    }
    let locator = if page_range.is_empty() {
        reference.elocation_id.clone()
    } else {
        page_range
    };
    if !locator.is_empty() {
        volume_info = if volume_info.is_empty() {
            locator
        } else {
            format!("{volume_info}:{locator}")
        };
    }
    volume_info
}

/// Python's `JATSReferenceInfo._defers_to_the_deposit` and `_names_a_work`.
fn defers_to_the_deposit(printed_part_count: usize, reference: &JATSReferenceInfo) -> bool {
    if printed_part_count == 0 {
        return true;
    }
    if reference.citation.is_empty() {
        return false;
    }
    let names_a_work = !reference.article_title.is_empty()
        || !reference.source.is_empty()
        || !reference.doi.is_empty();
    printed_part_count == 1 || !names_a_work
}

/// Python's `JATSReferenceInfo.formatted_citation`.
///
/// Joined through the crate's `join_citation_parts`, as `format_ref_html` in
/// `fulltext::service` is, so a component already ending a sentence (`et al.`)
/// takes no second mark (#385).
fn formatted_citation(reference: &JATSReferenceInfo) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !reference.authors.is_empty() {
        if reference.authors.len() <= 3 {
            parts.push(reference.authors.join(", "));
        } else {
            parts.push(format!(
                "{}, {}, et al.",
                reference.authors[0], reference.authors[1]
            ));
        }
    }
    if !reference.article_title.is_empty() {
        parts.push(reference.article_title.clone());
    }
    if !reference.source.is_empty() {
        parts.push(reference.source.clone());
    }
    if !reference.year.is_empty() {
        parts.push(format!("({})", reference.year));
    }
    let volume = volume_info(reference);
    if !volume.is_empty() {
        parts.push(volume);
    }
    if !reference.doi.is_empty() {
        parts.push(format!("doi:{}", reference.doi));
    }
    if defers_to_the_deposit(parts.len(), reference) {
        return reference.citation.clone();
    }
    join_citation_parts(&parts, false)
}

fn render_reference(reference: &JATSReferenceInfo) -> Value {
    json!({
        "id": reference.id,
        "label": reference.label,
        "citation": reference.citation,
        "authors": reference.authors,
        "article_title": reference.article_title,
        "source": reference.source,
        "year": reference.year,
        "volume": reference.volume,
        "issue": reference.issue,
        "first_page": reference.first_page,
        "last_page": reference.last_page,
        "doi": reference.doi,
        "pmid": reference.pmid,
        "elocation_id": reference.elocation_id,
        "formatted_citation": formatted_citation(reference),
    })
}

fn render_article(article: &JATSArticle) -> Value {
    json!({
        "title": article.title,
        "journal": article.journal,
        "volume": article.volume,
        "issue": article.issue,
        "pages": article.pages,
        "year": article.year,
        "doi": article.doi,
        "pmc_id": article.pmc_id,
        "pmid": article.pmid,
        "elocation_id": article.elocation_id,
        "has_body": article.has_body,
        "suppressed_nested_articles": article.suppressed_nested_articles,
        "authors": article.authors.iter().map(render_author).collect::<Vec<_>>(),
        "abstract_sections": article
            .abstract_sections
            .iter()
            .map(|section| json!({"title": section.title, "content": section.content}))
            .collect::<Vec<_>>(),
        "body_sections": article.body_sections.iter().map(render_body).collect::<Vec<_>>(),
        "figures": article
            .figures
            .iter()
            .map(|figure| json!({
                "id": figure.id,
                "label": figure.label,
                "caption": figure.caption,
                "graphic_url": figure.graphic_url,
                "footnotes": figure.footnotes,
            }))
            .collect::<Vec<_>>(),
        "tables": article
            .tables
            .iter()
            .map(|table| json!({
                "id": table.id,
                "label": table.label,
                "caption": table.caption,
                "html_content": table.html_content,
                "graphic_url": table.graphic_url,
                "footnotes": table.footnotes,
            }))
            .collect::<Vec<_>>(),
        "references": article.references.iter().map(render_reference).collect::<Vec<_>>(),
        "funding_statements": article.funding_statements,
        "funding_awards": article
            .funding_awards
            .iter()
            .map(|award| json!({
                "sources": award
                    .sources
                    .iter()
                    .map(|source| json!({"name": source.name, "identifier": source.identifier}))
                    .collect::<Vec<_>>(),
                "award_ids": award.award_ids,
            }))
            .collect::<Vec<_>>(),
    })
}

/// Parse a case the way `dump_jats.py`'s `run` does: the bare document first,
/// then the `<article>`-wrapped form for a fragment that is not a whole one.
fn run(xml: &str) -> Result<Value, String> {
    if let Ok(article) = parse(xml) {
        return Ok(render_article(&article));
    }
    let wrapped = format!("<?xml version=\"1.0\"?>\n<article>{xml}</article>");
    parse(&wrapped)
        .map(|article| render_article(&article))
        .map_err(|error| error.to_string())
}

// ---------------------------------------------------------------------------
// The diff
// ---------------------------------------------------------------------------

/// Record every leaf at which `want` and `got` differ, naming its path.
fn diff(path: &str, want: &Value, got: &Value, out: &mut Vec<String>) {
    match (want, got) {
        (Value::Object(want), Value::Object(got)) => {
            for (key, value) in want {
                match got.get(key) {
                    Some(other) => diff(&format!("{path}.{key}"), value, other, out),
                    None => out.push(format!(
                        "{path}.{key}: absent from Rust (python {})",
                        compact(value)
                    )),
                }
            }
            for key in got.keys() {
                if !want.contains_key(key) {
                    out.push(format!("{path}.{key}: extra in Rust"));
                }
            }
        }
        (Value::Array(want), Value::Array(got)) => {
            if want.len() != got.len() {
                out.push(format!(
                    "{path}: length {} in python, {} in Rust",
                    want.len(),
                    got.len()
                ));
            }
            for index in 0..want.len().min(got.len()) {
                diff(&format!("{path}[{index}]"), &want[index], &got[index], out);
            }
        }
        _ => {
            if want != got {
                out.push(format!(
                    "{path}: python {} != rust {}",
                    compact(want),
                    compact(got)
                ));
            }
        }
    }
}

fn compact(value: &Value) -> String {
    let text = serde_json::to_string(value).unwrap_or_default();
    if text.chars().count() > 200 {
        let head: String = text.chars().take(200).collect();
        format!("{head}…")
    } else {
        text
    }
}

#[test]
fn the_port_agrees_with_python_on_every_article() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");
    // Anti-vacuity: the loop below would pass on an empty corpus, and a
    // regenerated corpus that silently shrank is the failure this pins.
    assert_eq!(cases.len(), 77, "the committed corpus is 77 documents");

    let mut matches = 0usize;
    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: python itself failed: {}",
            want["error"]
        );
        let xml = case["xml"].as_str().unwrap_or_default();
        match run(xml) {
            Err(error) => failures.push(format!("  {name}\n    rust refused: {error}")),
            Ok(got) => {
                let want = &want["value"];
                if &got == want {
                    matches += 1;
                } else {
                    let mut paths = Vec::new();
                    diff("article", want, &got, &mut paths);
                    failures.push(format!("  {name}\n    {}", paths.join("\n    ")));
                }
            }
        }
    }

    assert!(
        failures.is_empty(),
        "{} of {} documents match; {} diverge:\n{}",
        matches,
        cases.len(),
        failures.len(),
        failures.join("\n")
    );
}

// ---------------------------------------------------------------------------
// The named tests
// ---------------------------------------------------------------------------

/// **A malformed document is refused, not partially parsed.**
///
/// Python's `JATSParser.parse` raises out of expat and the article is never
/// built, so a caller sees an error rather than a thin article. The audit's
/// "nothing here fails" rule is about the *unwind state* — a net over the
/// reader — and says nothing about the input check expat performs one layer
/// down.
#[test]
fn a_malformed_document_is_refused() {
    assert!(parse("<article><sec></article>").is_err());
    assert!(parse("not xml at all").is_err());
    assert!(parse("<article><unclosed></article>").is_err());
}

/// **An undeclared `xlink` prefix is read, because expat reads it.**
///
/// `xml.sax.make_parser()` does not process namespaces, so `xlink:href` is an
/// ordinary attribute name and a document that never declares the prefix is
/// accepted — and its href is the figure's image. `roxmltree` resolves names
/// and refuses the same bytes, so the reader splices the declaration in and
/// retries. Without that, a deposit skipping the declaration would lose its
/// figure rather than its prefix.
#[test]
fn an_undeclared_xlink_prefix_is_read_as_python_reads_it() {
    let xml = concat!(
        "<article><front><article-meta></article-meta></front>",
        "<back><fig id=\"f1\"><graphic xlink:href=\"a.jpg\"/></fig></back></article>"
    );
    let article = parse(xml).expect("expat accepts an undeclared prefix, so this must too");
    assert_eq!(article.figures.len(), 1);
    assert_eq!(article.figures[0].graphic_url.as_deref(), Some("a.jpg"));

    // A declared document is untouched: the same href, read the same way.
    let declared = concat!(
        "<article xmlns:xlink=\"http://www.w3.org/1999/xlink\">",
        "<front><article-meta></article-meta></front>",
        "<back><fig id=\"f1\"><graphic xlink:href=\"a.jpg\"/></fig></back></article>"
    );
    let article = parse(declared).expect("a declared prefix parses");
    assert_eq!(article.figures[0].graphic_url.as_deref(), Some("a.jpg"));
}

/// **A well-formed document unwinds clean.**
///
/// The end-of-parse audit's whole contract is that no well-formed document can
/// produce a diagnostic, because a conforming XML parser rejects an unbalanced
/// one first — so this is the false-positive check every committed fixture is
/// entitled to, asserted on the sample article rather than only on the fact
/// that the eighteen matched.
#[test]
fn a_clean_parse_leaves_no_diagnostics() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    for case in cases.as_array().expect("cases is a list") {
        let xml = case["xml"].as_str().unwrap_or_default();
        let name = case["name"].as_str().unwrap_or_default();
        for attempt in [xml.to_string(), format!("<article>{xml}</article>")] {
            if let Ok(report) = parse_audited(&attempt, "") {
                assert!(
                    report.diagnostics.is_empty(),
                    "{name}: the audit fired on a well-formed document: {:?}",
                    report.diagnostics
                );
                assert_eq!(
                    report.unwind,
                    bmlib::fulltext::parse_audit::ParseUnwindState::default(),
                    "{name}: a clean parse must map to the state's defaults"
                );
                break;
            }
        }
    }
}

/// **A structured name wins over the two undivided forms, and emptiness is the
/// predicate for "named".**
///
/// Pinned here rather than only through the oracle because the reader's
/// `build_authors` gate and the oracle's `full_name` must agree: if they did
/// not, a contributor would be built here and rendered differently there.
#[test]
fn author_name_precedence_and_namedness() {
    let mut author = JATSAuthorInfo {
        surname: "Smith".to_string(),
        given_names: "Jane Q".to_string(),
        collab: "the Y Group".to_string(),
        string_name: "J Q Smith".to_string(),
        ..JATSAuthorInfo::default()
    };
    assert_eq!(author_full_name(&author), "Jane Q Smith");
    assert!(author_is_named(&author));

    // A collaboration alone: no surname, and still named.
    author = JATSAuthorInfo {
        collab: "the Y Group".to_string(),
        ..JATSAuthorInfo::default()
    };
    assert_eq!(author_full_name(&author), "the Y Group");
    assert!(author_is_named(&author));

    // An undivided personal name is the fallback.
    author = JATSAuthorInfo {
        string_name: "Ahmed Al-Rashid".to_string(),
        ..JATSAuthorInfo::default()
    };
    assert_eq!(author_full_name(&author), "Ahmed Al-Rashid");
    assert!(author_is_named(&author));

    // A structured field holding only whitespace is trimmed away, so the
    // contributor is unnamed and `build_authors` drops it.
    author = JATSAuthorInfo {
        given_names: "  ".to_string(),
        ..JATSAuthorInfo::default()
    };
    assert_eq!(author_full_name(&author), "");
    assert!(!author_is_named(&author), "whitespace is not a name");

    // A `<contrib>` naming nobody is unnamed, which is what drops it.
    assert!(!author_is_named(&JATSAuthorInfo::default()));
}

// ---------------------------------------------------------------------------
// Who owns a value: this work, or the work it names
// ---------------------------------------------------------------------------

/// The minimal article Python's `_article_with` builds: `front` inside
/// `<article-meta>`, body and back verbatim.
fn article_with(front: &str, body: &str, back: &str) -> String {
    format!(
        "<?xml version=\"1.0\"?><article><front><article-meta>\
         <article-id pub-id-type=\"pmc\">PMC1</article-id>\
         {front}</article-meta></front><body>{body}</body>\
         <back>{back}</back></article>"
    )
}

fn reference(xml: &str) -> JATSReferenceInfo {
    let article = parse(xml).expect("the fixture parses");
    assert_eq!(article.references.len(), 1, "the fixture has one reference");
    article.references[0].clone()
}

/// **A related work nested in a citation writes none of its fields** (#270).
///
/// `<related-object>`, `<related-article>` and `<product>` hold the same child
/// names the enclosing work uses, and the reference's structured-field arms
/// used to read them from anywhere under the citation — so an erratum's `99:7`
/// replaced the cited work's `1:2`. A blank is the honest answer where the
/// reference states nothing.
#[test]
fn a_related_work_in_a_citation_is_not_the_reference() {
    let erratum = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation><source>J</source>\
         <related-object>Erratum <volume>99</volume><fpage>7</fpage></related-object>\
         </element-citation></ref></ref-list>",
    ));
    assert_eq!(erratum.source, "J");
    assert_eq!(
        erratum.volume, "",
        "the erratum's volume is not the reference's"
    );
    assert_eq!(erratum.first_page, "");

    let own = "<article-title>Own title</article-title><source>Own J</source><year>2001</year>\
               <volume>1</volume><issue>2</issue><fpage>3</fpage><lpage>4</lpage>\
               <pub-id pub-id-type=\"doi\">10.1/own</pub-id>";
    for element in ["related-object", "related-article", "product"] {
        let other = format!(
            "<{element}><article-title>Other title</article-title><source>Other J</source>\
             <year>1999</year><volume>99</volume><issue>98</issue><fpage>97</fpage>\
             <lpage>96</lpage><pub-id pub-id-type=\"doi\">10.1/other</pub-id></{element}>"
        );
        // Either order: the reference's own values are the only ones that land.
        for citation in [format!("{own}{other}"), format!("{other}{own}")] {
            let cited = reference(&article_with(
                "",
                "",
                &format!(
                    "<ref-list><ref id=\"r1\"><element-citation>{citation}\
                     </element-citation></ref></ref-list>"
                ),
            ));
            assert_eq!(cited.article_title, "Own title");
            assert_eq!(cited.source, "Own J");
            assert_eq!(cited.year, "2001");
            assert_eq!(cited.volume, "1");
            assert_eq!(cited.issue, "2");
            assert_eq!(cited.first_page, "3");
            assert_eq!(cited.last_page, "4");
            assert_eq!(cited.doi, "10.1/own");
        }
    }

    // A related work's byline is not the reference's authors.
    let names = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation>\
         <person-group><name><surname>Own</surname><given-names>A</given-names></name>\
         </person-group><related-article><person-group><name><surname>Other</surname>\
         <given-names>B</given-names></name></person-group><collab>Other Group</collab>\
         <string-name>C Other</string-name></related-article></element-citation>\
         </ref></ref-list>",
    ));
    assert_eq!(names.authors, vec!["A Own".to_string()]);

    // Refusing the field is not deleting the text: a `<mixed-citation>` still
    // prints the related work where it was typeset.
    let mixed = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><mixed-citation><source>J</source> \
         <volume>1</volume>:<fpage>2</fpage>; erratum <related-object><volume>99</volume>:\
         <fpage>7</fpage></related-object>.</mixed-citation></ref></ref-list>",
    ));
    assert_eq!(
        (mixed.volume.as_str(), mixed.first_page.as_str()),
        ("1", "2")
    );
    assert_eq!(mixed.citation, "J 1:2; erratum 99:7.");
}

/// **A related work's parts are its text wherever it sits** (#267, #271).
///
/// `<article-title>` and its siblings accumulate rather than inlining, so
/// outside a citation their text used to be cut out of the sentence printing
/// it: a retraction notice read `titled ","` and a reply lost the work it
/// answers. The related work's own untagged characters always landed in place,
/// so tagging a word must not move it.
#[test]
fn a_related_works_parts_stay_in_the_text() {
    for element in ["related-article", "related-object"] {
        let reply = parse(&article_with(
            &format!(
                "<title-group><article-title>Reply to <{element}>\
                 <article-title>Old paper</article-title></{element}>, a comment\
                 </article-title></title-group>"
            ),
            "",
            "",
        ))
        .expect("the fixture parses");
        assert_eq!(reply.title, "Reply to Old paper, a comment");
    }

    // The shape of PMC12105076, the archive's own instance (#271).
    let notice = parse(&article_with(
        "",
        "<p>This article titled <bold>“<related-article \
         related-article-type=\"retracted-article\"><article-title>Optimized Turmeric \
         Extract</article-title></related-article>,”</bold> published in <bold>Volume 9\
         </bold>, <related-article><source>Curr Alzheimer Res</source> \
         <year>2012</year></related-article>, is retracted.</p>",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        notice.body_sections[0].paragraphs,
        vec![
            "This article titled “Optimized Turmeric Extract,” published in Volume 9, \
             Curr Alzheimer Res 2012, is retracted."
                .to_string()
        ]
    );

    // The owner paths from #254 still refuse a related work's fields, even as
    // its prose merges back in.
    let correction = parse(&article_with(
        "<title-group><article-title>Correction</article-title></title-group>\
         <related-article><article-title>Corrected paper</article-title>\
         <volume>9</volume><fpage>5</fpage></related-article>",
        "",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        (
            correction.title.as_str(),
            correction.volume.as_str(),
            correction.pages.as_str()
        ),
        ("Correction", "", "")
    );
}

/// **A name printed in a contributor's prose is not the contributor's** (#258).
///
/// A `<bio>` and an `<author-comment>` hold prose *about* the contributor. A
/// name there replaced the author's own, and the undivided-name merge refusal
/// then cut it out of the paragraph that printed it.
#[test]
fn a_name_in_a_contributors_prose_is_not_theirs() {
    for container in ["bio", "author-comment"] {
        let article = parse(&article_with(
            &format!(
                "<contrib-group><contrib contrib-type=\"author\">\
                 <name><surname>Smith</surname><given-names>Jane</given-names></name>\
                 <{container}><p>Jane trained with \
                 <name><surname>Jones</surname><given-names>Bob</given-names></name>, \
                 <string-name>Ann Lee</string-name> and the \
                 <collab>INHERIT Group</collab>.</p></{container}></contrib>\
                 </contrib-group>"
            ),
            "",
            "",
        ))
        .expect("the fixture parses");
        let author = &article.authors[0];
        assert_eq!(
            (
                author.surname.as_str(),
                author.given_names.as_str(),
                author.string_name.as_str(),
                author.collab.as_str()
            ),
            ("Smith", "Jane", "", ""),
            "the {container}'s names are prose"
        );
        // The names merge back into the paragraph rather than vanishing.
        assert_eq!(
            article.body_sections[0].paragraphs,
            vec!["Jane trained with , Ann Lee and the INHERIT Group.".to_string()]
        );
    }

    // An undivided author's own name is not overwritten by one in the bio.
    let undivided = parse(&article_with(
        "<contrib-group><contrib contrib-type=\"author\">\
         <string-name>Jane Smith</string-name><bio><p>With \
         <string-name>Ann Lee</string-name>.</p></bio></contrib></contrib-group>",
        "",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(undivided.authors[0].string_name, "Jane Smith");

    // #120's roster still resolves: the walk stops at the innermost <contrib>.
    let roster = parse(&article_with(
        "<contrib-group><contrib contrib-type=\"author\">\
         <collab>The Group<contrib-group><contrib>\
         <name><surname>Member</surname><given-names>M</given-names></name>\
         </contrib></contrib-group></collab></contrib></contrib-group>",
        "",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        roster
            .authors
            .iter()
            .map(|a| (a.collab.as_str(), a.surname.as_str()))
            .collect::<Vec<_>>(),
        vec![("The Group", ""), ("", "Member")]
    );
}

/// **A structured `<name>` printed in body prose is cut out of the sentence**
/// (#382), and the port reproduces that rather than correcting it.
///
/// `<surname>` and `<given-names>` each accumulate their own text, and the arms
/// that read it fire only inside a citation's `<person-group>` or a `<contrib>`
/// that owns the name. In body prose neither runs, so the text is discarded — the
/// shape `a_name_in_a_contributors_prose_is_not_theirs` pins in a `<bio>`, reached
/// here with no `<contrib>` at all.
///
/// **Reproduced, not fixed.** #382 is filed, and it is outside the plan's
/// enumeration of defects the port corrects (§0), so the port follows Python:
/// `prose/382-a-name-in-a-body-paragraph-is-lost` pins that answer against the live
/// library. `<string-name>` and `<collab>` are inline and stay in the sentence,
/// which is the boundary this test draws. How often the dropped form occurs is
/// `scripts/measure_jats_prose_names.py`'s question, not this test's.
///
/// **The inline half is asked where no `<p>` stands above the name too**, because
/// in a paragraph `contrib_owns_name` already refuses on the `<p>` and the
/// `!self.contrib_stack.is_empty()` term of `is_owned_name` decides nothing — a
/// mutant deleting that term kept all 947 tests green (PR #389's review). In a
/// section `<title>` and a reference's `<mixed-citation>` that term alone keeps
/// the name, and Python keeps it in both.
#[test]
fn a_name_in_body_prose_is_lost() {
    let structured = parse(&article_with(
        "",
        "<sec><title>S</title><p>Named after \
         <name><surname>Jones</surname><given-names>Bob</given-names></name> in 1990.</p></sec>",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        structured.body_sections[0].paragraphs,
        vec!["Named after in 1990.".to_string()],
        "#382: the <name>'s parts are accumulated and read by no arm, so the name is gone"
    );

    for (inline, expected) in [
        (
            "<string-name>Jones Bob</string-name>",
            "Named after Jones Bob in 1990.",
        ),
        (
            "<collab>The Jones Group</collab>",
            "Named after The Jones Group in 1990.",
        ),
    ] {
        let article = parse(&article_with(
            "",
            &format!("<sec><title>S</title><p>Named after {inline} in 1990.</p></sec>"),
            "",
        ))
        .expect("the fixture parses");
        assert_eq!(
            article.body_sections[0].paragraphs,
            vec![expected.to_string()],
            "{inline} is inline, so the name stays in the sentence"
        );

        let titled = parse(&article_with(
            "",
            &format!("<sec><title>After {inline}</title><p>x</p></sec>"),
            "",
        ))
        .expect("the fixture parses");
        let text = inline_text(inline);
        assert_eq!(
            titled.body_sections[0].title,
            format!("After {text}"),
            "{inline} in a <title>: no <contrib> is open, so the name merges"
        );

        let cited = reference(&article_with(
            "",
            "",
            &format!(
                "<ref-list><ref id=\"r1\"><mixed-citation>{inline}. Title. J.</mixed-citation>\
                 </ref></ref-list>"
            ),
        ));
        assert_eq!(
            cited.citation,
            format!("{text}. Title. J."),
            "{inline} in a <mixed-citation>: the citation string keeps the name"
        );
    }
}

/// The text an inline name fixture carries: `<x>Jones Bob</x>` -> `Jones Bob`.
fn inline_text(inline: &str) -> &str {
    let open_end = inline.find('>').expect("an element") + 1;
    let close_start = inline.rfind("</").expect("an end tag");
    &inline[open_end..close_start]
}

/// **A citation printed in a paragraph stays there where it is typeset** (#391,
/// #255; the port follows Python's fix).
///
/// A `<mixed-citation>`, or a `<citation>` carrying text of its own, printed in
/// prose outside a `<ref>` merges back into its sentence — the rule a `<ref>`'s
/// citation follows for its string. An element-only one authored no string, so
/// it is cut out as before and now counted, with one warning per article. A
/// `<p>` in a prose citation's note is the citation's text and is not routed as
/// a paragraph as well.
#[test]
fn a_citation_in_prose_stays_where_it_is_typeset() {
    let mixed = parse(&article_with(
        "",
        "<p>As shown <mixed-citation>Smith J. <source>J</source>. (2001).</mixed-citation> \
         in the text.</p>",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        mixed.body_sections[0].paragraphs,
        vec!["As shown Smith J. J. (2001). in the text.".to_string()]
    );

    let report = parse_audited(
        &article_with(
            "",
            "<p>See <element-citation><source>J</source><year>2001</year></element-citation> \
             for details.</p>",
            "",
        ),
        "",
    )
    .expect("the fixture parses");
    assert_eq!(
        report.article.body_sections[0].paragraphs,
        vec!["See for details.".to_string()]
    );
    let lines: Vec<_> = report
        .warnings
        .iter()
        .filter(|line| line.contains("citation(s) printed outside a reference list"))
        .collect();
    assert_eq!(lines.len(), 1, "{:?}", report.warnings);
    assert!(
        lines[0].contains("1 citation(s) printed outside"),
        "{lines:?}"
    );

    let note = parse(&article_with(
        "",
        "<p>See <mixed-citation>Smith J. <source>S</source>. <annotation><p>See note</p>\
         </annotation>.</mixed-citation> here.</p>",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        note.body_sections[0].paragraphs,
        vec!["See Smith J. S. See note. here.".to_string()]
    );
}

/// **Authors and abstracts are the article's own `article-meta`'s** (#266).
///
/// A `<contrib>` with no declared role was collected wherever its group sat,
/// and an `<abstract>` nested in another object joined the article's — which
/// also ends #249's latent erasure of the article's abstract by a figure's.
#[test]
fn the_articles_own_contributors_and_abstract() {
    let own = "<contrib-group><contrib contrib-type=\"author\">\
               <name><surname>Author</surname><given-names>A</given-names></name>\
               </contrib></contrib-group>";
    let editor = "<contrib-group><contrib><name><surname>Editor</surname>\
                  <given-names>X</given-names></name></contrib></contrib-group>";

    let journal = parse(&format!(
        "<?xml version=\"1.0\"?><article><front><journal-meta>{editor}</journal-meta>\
         <article-meta>{own}</article-meta></front><body><p>t</p></body></article>"
    ))
    .expect("the fixture parses");
    assert_eq!(journal.authors.len(), 1);
    assert_eq!(journal.authors[0].surname, "Author");

    for (front, body) in [
        (
            format!("{own}<supplement>{editor}</supplement>"),
            String::new(),
        ),
        (
            own.to_string(),
            format!("<sec><sec-meta>{editor}</sec-meta><title>S</title><p>t</p></sec>"),
        ),
    ] {
        let article = parse(&article_with(&front, &body, "")).expect("the fixture parses");
        assert_eq!(article.authors.len(), 1, "an editor is not an author");
        assert_eq!(article.authors[0].surname, "Author");
    }

    // Out of place for JATS; still read leniently where it stands.
    let stray = parse(&article_with(
        "<contrib contrib-type=\"author\"><name><surname>Stray</surname>\
         <given-names>S</given-names></name></contrib>",
        "",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(stray.authors[0].surname, "Stray");

    // An object's abstract is not the article's, and is not lost either: it
    // routes as that object's other prose does.
    let object = parse(&article_with(
        "<supplementary-material><abstract><p>Dataset abstract.</p></abstract>\
         </supplementary-material><abstract><p>Own abstract.</p></abstract>",
        "",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        object
            .abstract_sections
            .iter()
            .map(|s| s.content.as_str())
            .collect::<Vec<_>>(),
        vec!["Own abstract."]
    );
    assert_eq!(
        object.body_sections[0].paragraphs,
        vec!["Dataset abstract.".to_string()]
    );

    // #249's latent half: a figure's abstract no longer opens and clears the
    // article's, discarding what came before it.
    let figure = parse(&article_with(
        "<abstract><title>Summary</title><p>Before fig.</p><fig id=\"f1\">\
         <caption><p>Cap.</p></caption><abstract abstract-type=\"fig_caption\">\
         <title>EN</title><p>English.</p></abstract></fig><p>After fig.</p></abstract>",
        "",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        figure
            .abstract_sections
            .iter()
            .map(|s| (s.title.as_str(), s.content.as_str()))
            .collect::<Vec<_>>(),
        vec![("Summary", "Before fig. After fig.")]
    );
}

// ---------------------------------------------------------------------------
// The citation spellings, and the names deposited in them
// ---------------------------------------------------------------------------

/// **A cited `<name>` outside a `<person-group>` is an author** (PR #387).
///
/// JATS 1.3 admits `<name>` directly in both citation elements, and the
/// structured part arms were gated on `in_ref_person_group` alone, so the
/// reference stored no authors and the rendered bibliography printed none. The
/// widened gate is a **parent** test, so a bare `<string-name>` keeps the
/// verbatim reading its own arm gives it. A mononym — a `<name>` carrying
/// `<given-names>` alone — is a legal name and used to be dropped, its given
/// names left pending for the next cited surname.
#[test]
fn a_cited_name_outside_a_person_group_is_an_author() {
    // Directly in the citation, with no <person-group>.
    let direct = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation>\
         <name><surname>Smith</surname><given-names>Jane</given-names></name>\
         <article-title>T</article-title><source>J</source></element-citation></ref></ref-list>",
    ));
    assert_eq!(direct.authors, vec!["Jane Smith".to_string()]);

    // The parent test's boundary: a bare <string-name> outside a group is not
    // read as structured, so what sits between its parts is not dropped.
    let bare = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation><string-name>Tan J</string-name>\
         <source>J</source></element-citation></ref></ref-list>",
    ));
    assert_eq!(bare.authors, vec!["Tan J".to_string()]);

    // A mononym is its own author and does not weld onto the next surname.
    let mononym = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation><person-group>\
         <name><given-names>Madonna</given-names></name>\
         <name><surname>Smith</surname><given-names>John</given-names></name>\
         </person-group><source>J</source></element-citation></ref></ref-list>",
    ));
    assert_eq!(
        mononym.authors,
        vec!["Madonna".to_string(), "John Smith".to_string()]
    );

    // The direction the mononym rule must not move: Wiley deposits one editor
    // across two <person-group>, the given names in the first and the surname
    // in the second, and the pending given names are what reassemble them.
    let split = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation>\
         <person-group><string-name><given-names>J.</given-names></string-name></person-group>\
         <person-group><string-name><surname>Tan</surname></string-name></person-group>\
         <source>J</source></element-citation></ref></ref-list>",
    ));
    assert_eq!(split.authors, vec!["J. Tan".to_string()]);
}

/// **A reference naming no work prints its deposit, however many components**
/// (#276).
///
/// Two components earn the structured rendering only where one of them names
/// the work. `R Core Team. (2019)` for a whole software citation is a pair the
/// count let through; the deposit is what the publisher actually printed.
#[test]
fn a_reference_naming_no_work_prints_its_deposit() {
    let software = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><mixed-citation><person-group>\
         <name><surname>R Core</surname></name></person-group>(<year>2019</year>)\
         </mixed-citation></ref></ref-list>",
    ));
    assert_eq!(software.article_title, "");
    assert_eq!(software.source, "");
    assert_eq!(software.doi, "");
    assert_eq!(software.citation, "R Core(2019)");
    assert_eq!(
        formatted_citation(&software),
        "R Core(2019)",
        "no component names the work, so the deposit is printed"
    );

    // The boundary: one naming component among two keeps the structured
    // rendering, so `source`+`year` still prints a journal and a year.
    let named = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><mixed-citation><person-group>\
         <name><surname>Smith</surname><given-names>J</given-names></name></person-group>\
         <source>J</source>(<year>2019</year>)</mixed-citation></ref></ref-list>",
    ));
    assert_eq!(named.source, "J");
    assert_eq!(formatted_citation(&named), "J Smith. J. (2019)");

    // A DOI names the work on its own, which is what keeps
    // `(2019). doi:10.1/x` from deferring.
    let doi = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><mixed-citation>(<year>2019</year>)\
         <pub-id pub-id-type=\"doi\">10.1/x</pub-id></mixed-citation></ref></ref-list>",
    ));
    assert_eq!(formatted_citation(&doi), "(2019). doi:10.1/x");
}

/// **An NLM 2.x `<citation>` is read by its deposit** (#390).
///
/// The DTD makes it mixed content, but PMC deposits it element-only — 1,124,468
/// of 1,155,505 served — and the text of an element-only one is its parts run
/// together. So a `<citation>` writes its string only where it carries typeset
/// text of its own, directly or in an `<x>`, and an element-only one behaves as
/// an `<element-citation>` does. A typeset `<citation>` printed outside a
/// `<ref>` stays in its sentence and an element-only one is cut out (#391); a later `display-unstructured` part fills an identifier the
/// first left empty.
#[test]
fn an_nlm_citation_is_read_by_its_deposit() {
    let element_only = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><citation><person-group>\
         <name><surname>Smith</surname><given-names>J</given-names></name></person-group>\
         <article-title>T</article-title><source>J</source><year>2001</year>\
         </citation></ref></ref-list>",
    ));
    assert_eq!(element_only.article_title, "T");
    assert_eq!(element_only.source, "J");
    assert_eq!(
        element_only.citation, "",
        "element-only deposits author no string"
    );

    let typeset = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><citation>Smith J. <article-title>T</article-title>. \
         <source>J</source> (2001).</citation></ref></ref-list>",
    ));
    assert_eq!(
        typeset.citation, "Smith J. T. J (2001).",
        "its own punctuation makes it typeset"
    );

    // An <x> holding a typeset separator is the citation's own text.
    let with_x = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><citation><person-group>\
         <name><surname>Smith</surname><given-names>J</given-names></name>\
         </person-group><x>, </x><source>J</source></citation></ref></ref-list>",
    ));
    assert_eq!(with_x.citation, "SmithJ, J");

    // Printed in prose, a typeset one stays in its sentence, whole, and an
    // element-only one is cut out and counted, as an <element-citation> is
    // (#391, which replaced PR #394's whole-merge of either).
    let prose = parse(&article_with(
        "",
        "<p>As shown <citation><article-title>X</article-title>, 2001</citation> in the text.</p>",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        prose.body_sections[0].paragraphs,
        vec!["As shown X, 2001 in the text.".to_string()]
    );
    let element_only_prose = parse(&article_with(
        "",
        "<p>As shown <citation><article-title>X</article-title></citation> in the text.</p>",
        "",
    ))
    .expect("the fixture parses");
    assert_eq!(
        element_only_prose.body_sections[0].paragraphs,
        vec!["As shown in the text.".to_string()]
    );

    // A display-unstructured second part fills a PMID the first left empty,
    // and nothing else.
    let display = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><element-citation><article-title>T</article-title>\
         <source>J</source></element-citation><citation citation-type=\"display-unstructured\">\
         <article-title>T</article-title><source>J</source>\
         <pub-id pub-id-type=\"pmid\">12345678</pub-id></citation></ref></ref-list>",
    ));
    assert_eq!(display.pmid, "12345678");

    // Two locator parts joined across whitespace are read as element-only
    // while the citation has shown no text; if text arrives later that
    // whitespace was printed and the join is undone, the rest counted.
    let joined = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><citation><elocation-id>e1</elocation-id> \
         <elocation-id>e2</elocation-id></citation></ref></ref-list>",
    ));
    assert_eq!(joined.elocation_id, "e1e2");

    let undone = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><citation><elocation-id>e1</elocation-id> \
         <elocation-id>e2</elocation-id><x>.</x></citation></ref></ref-list>",
    ));
    assert_eq!(
        undone.elocation_id, "e1",
        "the whitespace was printed, so the parts were two locators"
    );
}

/// **The zero-author detector counts only the article's own contributors**
/// (#264).
///
/// It counted every name spelling anywhere in `<front>`, so a journal's
/// editors or a retraction notice's byline made an author-less notice read as a
/// routing failure. The scope is structural, never the role: a contributor the
/// article's own list carries whose role the reader refuses still counts, since
/// that refusal is the mis-routing the detector exists to report.
#[test]
fn the_zero_author_detector_counts_only_the_articles_contributors() {
    let notice = "<?xml version=\"1.0\"?><article><front><journal-meta>\
        <contrib-group><contrib><name><surname>Editor</surname><given-names>X</given-names>\
        </name></contrib></contrib-group></journal-meta><article-meta>\
        <related-article><person-group><name><surname>Retracted</surname>\
        <given-names>R</given-names></name></person-group></related-article>\
        </article-meta></front><body><p>t</p></body></article>";
    let report = parse_audited(notice, "").expect("the fixture parses");
    assert!(report.article.authors.is_empty());
    assert!(
        report
            .warnings
            .iter()
            .all(|line| !line.contains("contributor(s): they were")),
        "a name outside the contributor list must not make the detector loud: {:?}",
        report.warnings
    );

    let refused_role = "<?xml version=\"1.0\"?><article><front><article-meta>\
        <contrib-group><contrib contrib-type=\"editor\"><name><surname>Editor</surname>\
        <given-names>E</given-names></name></contrib></contrib-group></article-meta>\
        </front><body><p>t</p></body></article>";
    let report = parse_audited(refused_role, "").expect("the fixture parses");
    assert!(report.article.authors.is_empty());
    assert!(
        report
            .warnings
            .iter()
            .any(|line| line.contains("contributor list named 1 contributor(s)")),
        "the article's own list names one: {:?}",
        report.warnings
    );
}

// ---------------------------------------------------------------------------
// Reproductions of open Python defects
// ---------------------------------------------------------------------------
//
// Each of these is Python's behaviour today, filed upstream and reproduced here
// rather than fixed: the port's brief is functional equivalence to a *corrected*
// bmlib, and a defect outside the plan's enumerated list is reproduced and
// filed. A reproduction in the corpus is what makes Python's fix force the port
// to follow it — the mechanism #382's and #391's cases already use.

/// **A component already ending a sentence takes no second mark** (#385, fixed
/// upstream in the same PR that ported it here).
///
/// `et al.` ends in a period and both renderers joined the parts with `". "`,
/// so a fourth author printed `et al..`; a deposited `Nat Commun.` and a title
/// ending `?` doubled the same way. Measured upstream: 223,334 of 356,304
/// served references in 6,819 of 8,118 articles. A deposited trailing `,` is
/// left as deposited.
#[test]
fn a_sentence_ending_takes_no_second_mark() {
    let citation = |authors: &str, source: &str| {
        reference(&article_with(
            "",
            "",
            &format!(
                "<ref-list><ref id=\"r1\"><element-citation publication-type=\"journal\">\
                 <person-group person-group-type=\"author\">{authors}</person-group>\
                 <article-title>DNA damage response</article-title>\
                 <source>{source}</source><year>2008</year>\
                 </element-citation></ref></ref-list>"
            ),
        ))
    };
    let four = citation(
        "<name><surname>Ahamed</surname><given-names>M</given-names></name>\
         <name><surname>Karns</surname><given-names>M</given-names></name>\
         <name><surname>Goodson</surname><given-names>M</given-names></name>\
         <name><surname>Rowe</surname><given-names>J</given-names></name>",
        "Toxicol Appl Pharmacol",
    );
    assert_eq!(
        formatted_citation(&four),
        "M Ahamed, M Karns, et al. DNA damage response. Toxicol Appl Pharmacol. (2008)"
    );

    let three = citation(
        "<name><surname>Ahamed</surname><given-names>M</given-names></name>\
         <name><surname>Karns</surname><given-names>M</given-names></name>\
         <name><surname>Goodson</surname><given-names>M</given-names></name>",
        "Nat Commun.",
    );
    assert_eq!(
        formatted_citation(&three),
        "M Ahamed, M Karns, M Goodson. DNA damage response. Nat Commun. (2008)"
    );

    let debris = citation("", "Neurophysiol.,");
    assert_eq!(
        formatted_citation(&debris),
        "DNA damage response. Neurophysiol.,. (2008)",
        "a deposited comma is not a sentence ending"
    );
}

/// The shared joiner reads an HTML component by the text its tags close.
#[test]
fn the_joiner_reads_past_a_closing_tag_only_in_markup() {
    let parts = |items: &[&str]| items.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    assert_eq!(
        join_citation_parts(&parts(&["<em>Nat Commun.</em>", "(2024)"]), true),
        "<em>Nat Commun.</em> (2024)"
    );
    assert_eq!(
        join_citation_parts(&parts(&["<em>J</em>", "(2024)"]), true),
        "<em>J</em>. (2024)"
    );
    assert_eq!(
        join_citation_parts(&parts(&["Title.</i>", "(2024)"]), false),
        "Title.</i>. (2024)"
    );
    assert_eq!(
        join_citation_parts(&parts(&["Is it safe?", "Lancet"]), false),
        "Is it safe? Lancet"
    );
    assert_eq!(join_citation_parts(&parts(&["Only."]), false), "Only.");
    assert_eq!(join_citation_parts(&[], false), "");
}

/// **A cited `<pub-id>` is read by its declared type** (#397, fixed upstream in
/// the same PR that ported it here).
///
/// `pmid`/`pubmed` is the PMID at any length (a six-digit one was refused by
/// shape); a number under any other type is never a PMID; a DOI shape is the
/// DOI whatever it declares but never over a declared one; and a `medline`
/// number fills only an empty PMID, since it is a MEDLINE UI in back-file
/// deposits and the real PMID in recent ones.
#[test]
fn a_cited_pub_id_is_read_by_its_declared_type() {
    let ids = |pub_ids: &str| {
        let r = reference(&article_with(
            "",
            "",
            &format!(
                "<ref-list><ref id=\"r1\"><element-citation publication-type=\"journal\">\
                 <source>Lancet</source><year>1962</year>{pub_ids}</element-citation></ref>\
                 </ref-list>"
            ),
        ));
        (r.pmid, r.doi)
    };
    let pair = |pmid: &str, doi: &str| (pmid.to_string(), doi.to_string());
    assert_eq!(
        ids("<pub-id pub-id-type=\"pmid\">138412</pub-id>"),
        pair("138412", "")
    );
    assert_eq!(
        ids("<pub-id pub-id-type=\"PubMed\">138412</pub-id>"),
        pair("138412", "")
    );
    assert_eq!(
        ids("<pub-id pub-id-type=\"pmid\">１３８４１２</pub-id>"),
        pair("", "")
    );
    assert_eq!(
        ids("<pub-id pub-id-type=\"publisher-id\">9908450</pub-id>"),
        pair("", "")
    );
    assert_eq!(ids("<pub-id>93348485</pub-id>"), pair("", ""));
    assert_eq!(
        ids("<pub-id pub-id-type=\"medline\">36644110</pub-id>"),
        pair("36644110", "")
    );
    for order in [
        "<pub-id pub-id-type=\"pmid\">8346438</pub-id><pub-id pub-id-type=\"medline\">93348485</pub-id>",
        "<pub-id pub-id-type=\"medline\">93348485</pub-id><pub-id pub-id-type=\"pmid\">8346438</pub-id>",
    ] {
        assert_eq!(ids(order), pair("8346438", ""), "a MUI never replaces a declared PMID");
    }
    assert_eq!(
        ids("<pub-id pub-id-type=\"pii\">10.1186/s12888-021-03469-8</pub-id>"),
        pair("", "10.1186/s12888-021-03469-8")
    );
    assert_eq!(
        ids("<pub-id pub-id-type=\"pii\">10.1177_2055207618797554</pub-id>"),
        pair("", "")
    );
    for order in [
        "<pub-id pub-id-type=\"doi\">10.1000/declared</pub-id><pub-id pub-id-type=\"pii\">10.1000/shaped</pub-id>",
        "<pub-id pub-id-type=\"pii\">10.1000/shaped</pub-id><pub-id pub-id-type=\"doi\">10.1000/declared</pub-id>",
    ] {
        assert_eq!(ids(order), pair("", "10.1000/declared"));
    }

    // A `<mixed-citation>` reads the field the same way, and keeps the
    // number in the typeset string as well.
    let mixed = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><mixed-citation>Lancet. 1962. \
         <pub-id pub-id-type=\"pmid\">138412</pub-id></mixed-citation></ref></ref-list>",
    ));
    assert_eq!(mixed.pmid, "138412");
    assert_eq!(mixed.citation, "Lancet. 1962. 138412");
}

/// **An element-only citation whose every child is one no field reads renders
/// nothing** (#393, open upstream) — the empty `<li>`.
///
/// Three shapes are named in the issue and all three are pinned here: a web
/// reference whose address is an attribute and whose only text a `<comment>`; a
/// conference deposit tagged only `<conf-name>`/`<conf-date>`/`<conf-loc>`; and a
/// `<comment>` holding the whole reference around an `<ext-link>`. An
/// element-only citation writes no `citation` string by design, and no
/// structured field reads any of those children, so `formatted_citation` is `""`
/// and the HTML reference list gets an empty `<li>`.
#[test]
fn an_element_only_citation_no_field_reads_renders_nothing() {
    let shapes = [
        "<ref-list><ref id=\"r12\"><citation citation-type=\"other\">\
         <ext-link ext-link-type=\"uri\"/><comment>accessed March 28, 2005</comment>\
         </citation></ref></ref-list>",
        "<ref-list><ref id=\"r3\"><element-citation publication-type=\"conference\">\
         <conf-name>Annual Meeting of the Society</conf-name><conf-date>2004</conf-date>\
         <conf-loc>Boston</conf-loc></element-citation></ref></ref-list>",
        "<ref-list><ref id=\"r4\"><element-citation publication-type=\"other\">\
         <comment>See <ext-link ext-link-type=\"uri\">the report</ext-link> for details\
         </comment></element-citation></ref></ref-list>",
    ];
    for (index, shape) in shapes.iter().enumerate() {
        let empty = reference(&article_with("", "", shape));
        assert_eq!(empty.citation, "", "shape {index}: no string is written");
        assert_eq!(empty.article_title, "", "shape {index}");
        assert_eq!(empty.source, "", "shape {index}");
        assert_eq!(empty.year, "", "shape {index}");
        assert_eq!(
            formatted_citation(&empty),
            "",
            "shape {index} must render nothing at all"
        );
    }
}

/// **An element-only citation's edition, publisher-loc, publisher-name and
/// comment reach no field and no counter** (#396, open upstream).
///
/// The reference renders from the fields that *are* read — authors, source,
/// year — and everything else is dropped silently, where the module's standing
/// rule for a drop it argues for is to count and report it once per article.
/// #393 is the subset where nothing renders at all.
#[test]
fn a_books_edition_publisher_and_comment_are_dropped() {
    let fixture = article_with(
        // The article's own contributor, so the zero-author detector is quiet:
        // the warning it would otherwise raise is about the article, not about
        // the reference drop this test is measuring.
        "<contrib-group><contrib contrib-type=\"author\"><name><surname>Smith</surname>\
         <given-names>J</given-names></name></contrib></contrib-group>",
        "",
        "<ref-list><ref id=\"r1\"><citation citation-type=\"book\">\
         <person-group><name><surname>Proctor</surname><given-names>RN</given-names></name>\
         </person-group><source>The Nazi War on Cancer</source><edition>2nd</edition>\
         <publisher-loc>Princeton</publisher-loc>\
         <publisher-name>Princeton University Press</publisher-name>\
         <year>1999</year><comment>In press</comment></citation></ref></ref-list>",
    );
    let book = reference(&fixture);
    assert_eq!(book.authors, vec!["RN Proctor".to_string()]);
    assert_eq!(book.source, "The Nazi War on Cancer");
    assert_eq!(book.year, "1999");
    assert_eq!(
        formatted_citation(&book),
        "RN Proctor. The Nazi War on Cancer. (1999)",
        "the edition, the place, the publisher and the comment are gone"
    );

    // **Nothing counts the drop and no line is logged**: the audit's two
    // channels — the unwind diagnostics and the counted losses — are both empty
    // for this document, so an operator sees no sign that four deposited parts
    // were discarded.
    let report = parse_audited(&fixture, "").expect("the fixture parses");
    assert!(report.diagnostics.is_empty(), "{:?}", report.diagnostics);
    assert!(report.warnings.is_empty(), "{:?}", report.warnings);
}

/// **A `<mixed-citation>`'s deposit glues a name's parts together** (#314, open
/// upstream).
///
/// Real PMC deposits put no whitespace between a `<name>`'s parts, and #146
/// merges every descendant of a `<mixed-citation>` into its string — so the
/// printed deposit reads `KalahastyR, MotatiL` while the **structured** authors
/// are right. The string is the deposit's, not the model's, which is why the
/// issue is cosmetic and why it is pinned rather than repaired.
#[test]
fn a_names_parts_glue_in_a_mixed_citations_deposit() {
    let glued = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"r1\"><mixed-citation publication-type=\"journal\">\
         <person-group person-group-type=\"author\">\
         <name><surname>Kalahasty</surname><given-names>R</given-names></name>, \
         <name><surname>Motati</surname><given-names>L</given-names></name>\
         </person-group>. Strokesight: a novel system. arXiv 2022</mixed-citation></ref></ref-list>",
    ));
    assert_eq!(
        glued.authors,
        vec!["R Kalahasty".to_string(), "L Motati".to_string()],
        "the model's authors are not glued"
    );
    assert_eq!(
        glued.citation, "KalahastyR, MotatiL. Strokesight: a novel system. arXiv 2022",
        "the deposited string is"
    );
    assert_eq!(formatted_citation(&glued), glued.citation);
}

/// **An `<award-group>`'s `<principal-award-recipient>` reaches no field**
/// (#288, open upstream).
///
/// The funder and the award number are modelled; the recipient beside them is
/// not — no arm reads the element, and its `<name>` joins no field, the
/// article's authors included. The maintainer left it out of #284's structured
/// funding deliberately, as a second design question, so this pins the gap
/// rather than guessing an answer.
#[test]
fn a_principal_award_recipient_reaches_no_field() {
    let article = parse(&article_with(
        "<funding-group><award-group><funding-source><institution-wrap>\
         <institution>NIH</institution>\
         <institution-id institution-id-type=\"doi\">10.13039/100000002</institution-id>\
         </institution-wrap></funding-source><award-id>R01 GM123456</award-id>\
         <principal-award-recipient><name><surname>Smith</surname>\
         <given-names>Jane Q</given-names></name>\
         <contrib-id contrib-id-type=\"orcid\">https://orcid.org/0000-0002-1</contrib-id>\
         </principal-award-recipient></award-group></funding-group>",
        "",
        "",
    ))
    .expect("the fixture parses");

    assert_eq!(article.funding_awards.len(), 1);
    let award = &article.funding_awards[0];
    assert_eq!(award.award_ids, vec!["R01 GM123456".to_string()]);
    assert_eq!(
        award
            .sources
            .iter()
            .map(|s| s.name.as_str())
            .collect::<Vec<_>>(),
        vec!["NIH"]
    );
    assert_eq!(award.sources[0].identifier, "10.13039/100000002");
    // The recipient is nowhere: not on the award, and not an article author.
    assert!(
        article.authors.is_empty(),
        "the recipient's name must not become an article author: {:?}",
        article.authors
    );
}

/// **A `<ref>` carrying only a `<note>` renders as an empty `<li>`** (#150,
/// open upstream).
///
/// JATS models `<ref>` as `(label?, (citation | element-citation |
/// mixed-citation | note | p | x)*)`, and RSC deposits an explanatory footnote
/// in the bibliography that way. No arm collects it, so the reference parses
/// with no citation and no structured field.
#[test]
fn a_ref_of_only_a_note_renders_nothing() {
    let note = reference(&article_with(
        "",
        "",
        "<ref-list><ref id=\"cit20\"><note><p>The crystal structure has been \
         deposited at the CCDC.</p></note></ref></ref-list>",
    ));
    assert_eq!(note.citation, "", "nothing collects the note");
    assert_eq!(note.article_title, "");
    assert_eq!(note.source, "");
    assert_eq!(formatted_citation(&note), "", "and nothing renders");
}
