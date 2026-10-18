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

//! The FFI copy holds what bmlib produced, value for value.
//!
//! The exhaustive destructuring in the `From` impls makes a *new* bmlib field a
//! compile error; these tests hold the values: a swapped pair of `String`
//! fields (`first_page`/`last_page`, `warnings`/`diagnostics`) compiles, and a
//! comparison of the two serialisations is what catches it.

use serde_json::Value;

use super::*;

/// Serialise `v` to JSON.
fn json<T: serde::Serialize>(v: &T) -> Value {
    serde_json::to_value(v).expect("serialisable")
}

/// Remove every author's `full_name` from an FFI article's JSON, checking each
/// against bmlib's rule on the way, so what is left compares with bmlib's own.
fn strip_full_names(article: &mut Value, core: &m::JATSArticle) {
    let authors = article["authors"].as_array_mut().expect("authors");
    assert_eq!(authors.len(), core.authors.len());
    for (ffi, core) in authors.iter_mut().zip(&core.authors) {
        let full_name = ffi
            .as_object_mut()
            .expect("author object")
            .remove("full_name")
            .expect("full_name present");
        assert_eq!(
            full_name,
            Value::from(bmlib::fulltext::author_full_name(core))
        );
    }
}

/// Every leaf of `v` that is empty: `""`, `null`, `0`, `false` or `[]`. An
/// empty `subsections` list is allowed, since a section tree has leaves.
fn empty_leaves(path: &str, v: &Value, out: &mut Vec<String>) {
    match v {
        Value::Object(o) => o
            .iter()
            .for_each(|(k, v)| empty_leaves(&format!("{path}.{k}"), v, out)),
        Value::Array(a) if a.is_empty() => {
            if !path.ends_with(".subsections") {
                out.push(path.to_string());
            }
        }
        Value::Array(a) => a
            .iter()
            .enumerate()
            .for_each(|(i, v)| empty_leaves(&format!("{path}[{i}]"), v, out)),
        Value::String(s) if s.is_empty() => out.push(path.to_string()),
        Value::Null | Value::Bool(false) => out.push(path.to_string()),
        Value::Number(n) if n.as_u64() == Some(0) => out.push(path.to_string()),
        _ => {}
    }
}

/// A bmlib article with a distinct, non-empty value in every field at every
/// level, built by struct literal so a field bmlib adds fails to compile here
/// too until it is given a value.
fn every_field_set() -> m::JATSArticle {
    let section = |n: &str, subsections| m::JATSBodySection {
        title: format!("{n} title"),
        paragraphs: vec![format!("{n} p1"), format!("{n} p2")],
        subsections,
    };
    m::JATSArticle {
        title: "article title".into(),
        authors: vec![
            m::JATSAuthorInfo {
                surname: "Smith".into(),
                given_names: "Jane Q".into(),
                affiliations: vec!["aff one".into(), "aff two".into()],
                collab: "the Y Group".into(),
                string_name: "J Q Smith".into(),
            },
            m::JATSAuthorInfo {
                surname: "Doe".into(),
                given_names: "John".into(),
                affiliations: vec!["aff three".into()],
                collab: "the Z Consortium".into(),
                string_name: "John Doe".into(),
            },
        ],
        journal: "journal".into(),
        volume: "12".into(),
        issue: "3".into(),
        pages: "100-110".into(),
        year: "2024".into(),
        doi: "10.1000/xyz".into(),
        pmc_id: "PMC123".into(),
        pmid: "456".into(),
        abstract_sections: vec![m::JATSAbstractSection {
            title: "Background".into(),
            content: "abstract content".into(),
        }],
        body_sections: vec![section("outer", vec![section("inner", vec![])])],
        figures: vec![m::JATSFigureInfo {
            id: "f1".into(),
            label: "Figure 1".into(),
            caption: "figure caption".into(),
            graphic_url: Some("fig.g001".into()),
            footnotes: vec!["a — figure note".into()],
        }],
        tables: vec![m::JATSTableInfo {
            id: "t1".into(),
            label: "Table 1".into(),
            caption: "table caption".into(),
            html_content: "<table><tr><td>1</td></tr></table>".into(),
            graphic_url: Some("tab.g001".into()),
            footnotes: vec!["b — table note".into()],
        }],
        references: vec![m::JATSReferenceInfo {
            id: "r1".into(),
            label: "1".into(),
            citation: "cited text".into(),
            authors: vec!["Roe R".into()],
            article_title: "cited title".into(),
            source: "cited journal".into(),
            year: "2020".into(),
            volume: "7".into(),
            issue: "2".into(),
            first_page: "11".into(),
            last_page: "19".into(),
            doi: "10.1000/cited".into(),
            pmid: "789".into(),
            elocation_id: "e42".into(),
        }],
        has_body: true,
        suppressed_nested_articles: 3,
        elocation_id: "e1001".into(),
        funding_statements: vec!["funded by".into()],
        funding_awards: vec![m::JATSFundingAward {
            sources: vec![m::JATSFundingSource {
                name: "A Funder".into(),
                identifier: "10.13039/100000001".into(),
            }],
            award_ids: vec!["R01-1".into()],
        }],
    }
}

#[test]
fn every_field_reaches_the_ffi_record_unchanged() {
    let core = every_field_set();
    let core_json = json(&core);

    // The fixture is only a check where no field is empty: an empty value
    // dropped or swapped reads the same on both sides.
    let mut empty = Vec::new();
    empty_leaves("article", &core_json, &mut empty);
    assert!(empty.is_empty(), "fixture leaves empty: {empty:?}");

    let mut ffi = json(&JatsArticle::from(core.clone()));
    strip_full_names(&mut ffi, &core);
    assert_eq!(ffi, core_json);
}

#[test]
fn the_ffi_field_order_is_bmlibs() {
    // The generated Kotlin and Swift take fields by position, so the order is
    // contract; matching bmlib's own keeps the two readable side by side.
    // serde_json's map sorts its keys, so the declared order is read from the
    // Debug output instead — with every nested list emptied, so each top-level
    // key appears exactly once (a reference also has a `year`, a `doi`…).
    let mut core = every_field_set();
    core.authors.clear();
    core.abstract_sections.clear();
    core.body_sections.clear();
    core.figures.clear();
    core.tables.clear();
    core.references.clear();
    core.funding_awards.clear();
    let keys: Vec<String> = json(&core).as_object().unwrap().keys().cloned().collect();
    let declared = |debug: String| -> Vec<String> {
        let order: std::collections::BTreeMap<usize, &String> = keys
            .iter()
            .map(|k| {
                let needle = format!(" {k}: ");
                assert_eq!(debug.matches(&needle).count(), 1, "{k} in {debug}");
                (debug.find(&needle).unwrap(), k)
            })
            .collect();
        order.into_values().cloned().collect()
    };
    assert_eq!(
        declared(format!("{:?}", JatsArticle::from(core.clone()))),
        declared(format!("{core:?}"))
    );
}

#[test]
fn the_audit_reaches_the_ffi_report_unchanged() {
    let core = bmlib::fulltext::JatsReport {
        article: every_field_set(),
        unwind: Default::default(),
        article_label: "PMC123".into(),
        diagnostics: vec!["a diagnostic".into()],
        warnings: vec!["a warning".into(), "another warning".into()],
    };
    let ffi = JatsParseReport::from(core.clone());
    assert_eq!(ffi.article_label, "PMC123");
    assert_eq!(ffi.diagnostics, vec!["a diagnostic".to_string()]);
    assert_eq!(
        ffi.warnings,
        vec!["a warning".to_string(), "another warning".to_string()]
    );
}

/// The corpus bmlib's own reader is held to Python on.
const JATS_CASES: &str = include_str!("../../bmlib/tests/data/jats_cases.json");

/// Parse as the core corpus test does: a fragment is retried inside an
/// `<article>` wrapper.
fn parse_both(xml: &str) -> (Result<m::JATSArticle, String>, Result<JatsArticle, String>) {
    let core = bmlib::fulltext::parse(xml).map_err(|e| e.to_string());
    let ffi = parse_jats(xml.to_string()).map_err(|e| e.to_string());
    if core.is_ok() {
        return (core, ffi);
    }
    let wrapped = format!("<?xml version=\"1.0\"?>\n<article>{xml}</article>");
    (
        bmlib::fulltext::parse(&wrapped).map_err(|e| e.to_string()),
        parse_jats(wrapped).map_err(|e| e.to_string()),
    )
}

#[test]
fn parse_jats_agrees_with_bmlib_on_every_corpus_document() {
    let cases: Vec<Value> = serde_json::from_str(JATS_CASES).expect("corpus");
    // A floor, not the count: bmlib's own corpus test pins the exact size,
    // which grows as cases are added. Restating it here would fail on every
    // addition; a corpus that shrank would still pass this test vacuously.
    assert!(cases.len() >= 85, "the corpus shrank to {}", cases.len());
    let mut compared = 0;
    for case in &cases {
        let name = case["name"].as_str().expect("name");
        let xml = case["xml"].as_str().expect("xml");
        match parse_both(xml) {
            (Ok(core), Ok(ffi)) => {
                let mut ffi = json(&ffi);
                strip_full_names(&mut ffi, &core);
                assert_eq!(ffi, json(&core), "{name}");
                compared += 1;
            }
            (Err(_), Err(_)) => {}
            (core, ffi) => panic!("{name}: bmlib {:?}, ffi {:?}", core.err(), ffi.err()),
        }
    }
    assert!(compared > 80, "only {compared} documents parsed");
}

/// A table whose `colspan` cannot be read: bmlib counts it as a warning.
const REFUSED_COLSPAN: &str = "<article><front><article-meta><article-id \
    pub-id-type=\"doi\">10.1/x</article-id></article-meta></front><body><sec>\
    <title>T</title><p>Prose.</p><table-wrap id=\"t1\"><table><tr>\
    <td colspan=\"wide\">a</td></tr></table></table-wrap></sec></body></article>";

#[test]
fn the_audited_parse_returns_the_lines_bmlib_counted() {
    let report = parse_jats_audited(REFUSED_COLSPAN.into(), String::new()).expect("parses");
    let core = bmlib::fulltext::parse_audited(REFUSED_COLSPAN, "").expect("parses");
    assert!(
        !report.warnings.is_empty(),
        "the fixture must produce a warning"
    );
    assert!(report.diagnostics.is_empty());
    assert_eq!(report.warnings, core.warnings);
    assert_eq!(report.article_label, core.article_label);
    let plain = parse_jats(REFUSED_COLSPAN.into()).expect("parses");
    assert_eq!(json(&report.article), json(&plain));
}

#[test]
fn a_bare_known_pmc_id_is_prefixed_and_is_the_second_argument() {
    let report = parse_jats_audited(REFUSED_COLSPAN.into(), "12345".into()).expect("parses");
    assert_eq!(report.article.pmc_id, "PMC12345");
}

#[test]
fn a_malformed_document_is_an_xml_error_carrying_a_reason() {
    for xml in ["", "not xml", "<article><sec></article>", "<article><p>"] {
        for result in [
            parse_jats(xml.into()).map(|_| ()),
            parse_jats_audited(xml.into(), String::new()).map(|_| ()),
        ] {
            match result {
                Err(JatsError::Xml { reason }) => assert!(!reason.is_empty(), "{xml:?}"),
                other => panic!("{xml:?}: {other:?}"),
            }
        }
    }
}

/// A document whose deepest element sits `depth` levels down.
fn nested(depth: usize) -> String {
    // <article><body><p> are three levels; the rest are <italic>.
    let inner = depth - 3;
    format!(
        "<article><body><p>{}x{}</p></body></article>",
        "<italic>".repeat(inner),
        "</italic>".repeat(inner)
    )
}

#[test]
fn the_nesting_bound_counts_what_the_reader_would_recurse_into() {
    assert_eq!(nesting_depth_bound(&nested(10)), 10);
    assert_eq!(nesting_depth_bound("<a/><a><b/></a>"), 1);
    assert_eq!(nesting_depth_bound("<a href=\"x/\"><b>t</b></a>"), 2);
    assert_eq!(nesting_depth_bound("<a t='>'><b/></a>"), 1);
    // Markup inside a comment, CDATA or a PI is not an element.
    assert_eq!(
        nesting_depth_bound("<a><!-- <b><b><b> --><![CDATA[<c><c>]]><?pi <d><d>?></a>"),
        1
    );
    // A quote inside a comment in the internal subset must not swallow the
    // body: the body's depth is still counted.
    assert_eq!(
        nesting_depth_bound("<!DOCTYPE a [ <!-- it's --> ]><a><b><c/></b></a>"),
        2
    );
    // Elements an entity can expand to are added on top.
    assert_eq!(
        nesting_depth_bound("<!DOCTYPE a [ <!ENTITY e \"<x><y/></x>\"> ]><a>&e;</a>"),
        3
    );
    // An unclosed run counts as deep as it goes.
    assert_eq!(nesting_depth_bound("<a><a><a>"), 3);
}

#[test]
fn a_document_at_the_limit_parses() {
    let article = parse_jats(nested(MAX_NESTING_DEPTH)).expect("at the limit");
    assert!(article.has_body);
}

#[test]
fn a_document_past_the_limit_is_refused_by_depth() {
    match parse_jats(nested(MAX_NESTING_DEPTH + 1)) {
        Err(JatsError::NestingTooDeep { depth, limit }) => {
            assert_eq!(depth, MAX_NESTING_DEPTH as u64 + 1);
            assert_eq!(limit, MAX_NESTING_DEPTH as u64);
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn a_hostile_depth_is_refused_on_a_small_stack_without_overflowing() {
    // A 64 KiB stack is far below an iOS secondary thread's 512 KiB: neither
    // the pre-scan nor anything before the parse thread may recurse.
    let result = std::thread::Builder::new()
        .stack_size(64 << 10)
        .spawn(|| parse_jats(nested(200_000)).map(|_| ()))
        .expect("thread")
        .join()
        .expect("no panic");
    assert!(matches!(result, Err(JatsError::NestingTooDeep { .. })));
}

#[test]
fn the_parse_does_not_run_on_the_callers_stack() {
    // At the limit the reader needs far more than this caller's 128 KiB in an
    // unoptimised build; it succeeds because the parse runs on its own thread.
    let result = std::thread::Builder::new()
        .stack_size(128 << 10)
        .spawn(|| parse_jats(nested(MAX_NESTING_DEPTH)).map(|_| ()))
        .expect("thread")
        .join()
        .expect("no overflow, no panic");
    assert!(result.is_ok());
}

#[test]
fn a_record_at_the_limit_lowers_on_an_ios_sized_stack() {
    // UniFFI lowers the returned record on the calling thread, recursively for
    // nested sections. The deepest section tree the limit admits must lower on
    // a 512 KiB stack (an iOS secondary thread's default).
    let mut section = JatsBodySection {
        title: "leaf".into(),
        paragraphs: vec![],
        subsections: vec![],
    };
    for _ in 0..MAX_NESTING_DEPTH {
        section = JatsBodySection {
            title: "s".into(),
            paragraphs: vec![],
            subsections: vec![section],
        };
    }
    let mut article = JatsArticle::from(every_field_set());
    article.body_sections = vec![section];
    std::thread::Builder::new()
        .stack_size(512 << 10)
        .spawn(move || {
            let buffer = <JatsArticle as uniffi::Lower<crate::UniFfiTag>>::lower(article);
            buffer.destroy();
        })
        .expect("thread")
        .join()
        .expect("lowered without overflow");
}

#[test]
fn a_panic_on_the_parse_thread_reaches_the_caller_as_a_panic() {
    // UniFFI turns a panic into a foreign error only if it unwinds through the
    // exported function; re-raising it keeps that true across the thread.
    let caught = std::panic::catch_unwind(|| {
        parse_bounded::<(), _>("<a/>".into(), |_| panic!("reader defect"))
    });
    let payload = caught.expect_err("the panic must propagate");
    assert_eq!(payload.downcast_ref::<&str>(), Some(&"reader defect"));
}

#[test]
fn bmlib_version_is_the_bmlib_crates() {
    // `bmlib_version()` reports bmlib-ffi's own version; it is bmlib's only
    // because both crates inherit the workspace's. Hold all three together.
    let workspace = include_str!("../../Cargo.toml");
    let package = workspace
        .split("[workspace.package]")
        .nth(1)
        .expect("[workspace.package]");
    let version = package
        .lines()
        .find_map(|l| l.trim().strip_prefix("version = \""))
        .and_then(|v| v.strip_suffix('"'))
        .expect("workspace version");
    assert_eq!(bmlib_version(), version);
    for manifest in [
        include_str!("../Cargo.toml"),
        include_str!("../../bmlib/Cargo.toml"),
    ] {
        assert!(manifest.contains("\nversion.workspace = true\n"));
    }
}

/// The shared retraction contract, through the FFI functions alone — what an
/// app calls — so a lost field or a swapped conversion fails here.
#[test]
fn the_retraction_contract_holds_through_the_ffi() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/retraction_status.json"
    ))
    .expect("fixture");
    let strings = |v: &Value| -> Vec<String> {
        v.as_array()
            .expect("list")
            .iter()
            .map(|s| s.as_str().expect("text").to_string())
            .collect()
    };
    let body = |c: &Value| match c.get("body_text") {
        Some(text) => text.as_str().expect("text").as_bytes().to_vec(),
        None => serde_json::to_vec(&c["body"]).expect("body"),
    };
    let evidence = |v: &Value| -> RetractionEvidence {
        let core: bmlib::publications::retraction_status::RetractionEvidence =
            serde_json::from_value(v.clone()).expect("evidence");
        core.into()
    };
    let cases = fixture["rule_cases"].as_array().expect("cases");
    for case in cases {
        let doi = case["doi"].as_str().map(str::to_string);
        let crossref = (!case["crossref"].is_null()).then(|| {
            let results = parse_crossref_retraction_response(
                u16::try_from(case["crossref"]["status"].as_u64().expect("status")).expect("u16"),
                body(&case["crossref"]),
                vec![doi.clone().expect("a doi")],
            );
            results.into_values().next().expect("one result")
        });
        let export = case["export"]
            .as_array()
            .map(|list| list.iter().map(evidence).collect());
        let verdict = retraction_status(doi, export, crossref, strings(&case["publication_types"]));
        assert_eq!(json(&verdict), case["expected"], "{}", case["name"]);
    }
    for case in fixture["request_cases"].as_array().expect("cases") {
        let got = crossref_retraction_requests(
            strings(&case["dois"]),
            case["mailto"].as_str().map(str::to_string),
        );
        let batches: Vec<Value> = got
            .batches
            .iter()
            .map(|b| serde_json::json!({"url": b.url, "dois": b.dois}))
            .collect();
        assert_eq!(
            serde_json::json!({"batches": batches, "unsendable": got.unsendable}),
            case["expected"],
            "{}",
            case["name"]
        );
    }
}

#[test]
fn the_retraction_index_round_trips_through_the_ffi() {
    let csv = "Record ID,RetractionDate,RetractionDOI,OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature\n\
               4036,2/6/2010 0:00,10.1016/S0140-6736(10)60175-4,10.1016/S0140-6736(97)11096-0,9500320,Retraction\n";
    let build =
        build_retraction_index(csv.as_bytes().to_vec(), "2026-10-18".to_string()).expect("builds");
    assert_eq!(build.skipped_rows, 0);
    let index = RetractionIndex::from_bytes(build.index.to_bytes()).expect("reopens");
    assert_eq!(index.data_as_of(), "2026-10-18");
    assert_eq!(index.notice_count(), 1);
    let found = index
        .lookup(None, Some("9500320".to_string()))
        .expect("consulted");
    assert_eq!(found[0].kind, RetractionKind::Retraction);
    assert_eq!(found[0].record_id.as_deref(), Some("4036"));
    assert_eq!(index.lookup(None, Some("0".to_string())), None);
    assert!(matches!(
        RetractionIndex::from_bytes(b"not an index".to_vec()),
        Err(RetractionIndexError::Unusable { .. })
    ));
    let verdict = retraction_status(
        Some("10.1016/S0140-6736(97)11096-0".to_string()),
        index.lookup(Some("10.1016/S0140-6736(97)11096-0".to_string()), None),
        None,
        vec![],
    );
    assert_eq!(verdict.status, RetractionStatus::Retracted);
    assert_eq!(verdict.unanswered, vec![RetractionLookup::Crossref]);
}
