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

//! The JATS reader, against Python's own output for 42 committed documents.
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
use bmlib::fulltext::models::{JATSArticle, JATSAuthorInfo, JATSBodySection, JATSReferenceInfo};
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

/// Python's `JATSReferenceInfo._defers_to_the_deposit`.
fn defers_to_the_deposit(printed_part_count: usize, citation: &str) -> bool {
    printed_part_count == 0 || (printed_part_count == 1 && !citation.is_empty())
}

/// Python's `JATSReferenceInfo.formatted_citation`.
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
    if defers_to_the_deposit(parts.len(), &reference.citation) {
        return reference.citation.clone();
    }
    parts.join(". ")
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
    assert_eq!(cases.len(), 42, "the committed corpus is 42 documents");

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
