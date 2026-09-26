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

//! Retraction Watch — the oracle and the named tests.
//!
//! The corpus (65 cases) covers the four pure rules and the whole parse path.
//! This file states the properties behind them, and the two that matter most:
//! the retraction rule is not "latest notice wins", and the export's own
//! sentinels are not identifiers.

use bmlib::publications::models::{RetractionNature, RetractionNotice};
use bmlib::publications::retractions::{
    clean_identifier, find_column, is_retracted, newest_first, parse_date,
    parse_retraction_watch_csv, row_to_notice, split_reasons, SkipReason, ABSENT_IDENTIFIER_VALUES,
    DATE_FORMATS,
};
use serde_json::Value;
use std::str::FromStr;

const CASES: &str = include_str!("data/retraction_cases.json");
const EXPECTED: &str = include_str!("data/retraction_expected.json");

/// The header the live export carries, in its own column order.
const HEADER: &str = "Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n";

#[allow(clippy::too_many_arguments)]
fn csv_row(
    record_id: &str,
    retraction_date: &str,
    retraction_doi: &str,
    retraction_pmid: &str,
    original_date: &str,
    original_doi: &str,
    original_pmid: &str,
    nature: &str,
    reason: &str,
    title: &str,
    journal: &str,
) -> String {
    format!(
        "{record_id},{title},Subject,Inst,{journal},Pub,AU,Author,URL,Article,\
         {retraction_date},{retraction_doi},{retraction_pmid},{original_date},\
         {original_doi},{original_pmid},{nature},{reason},No,Notes,\n"
    )
}

fn default_row() -> String {
    csv_row(
        "1",
        "3/9/2026 0:00",
        "10.1/notice",
        "87654321",
        "5/6/2023 0:00",
        "10.1/paper",
        "12345678",
        "Retraction",
        "Rogue Editor;",
        "A paper",
        "Soft Computing",
    )
}

/// Build a CSV from positional overrides, named in the corpus's own words.
fn rows_from_spec(specs: &Value) -> String {
    let mut doc = String::from(HEADER);
    for spec in specs.as_array().expect("list") {
        let get = |key: &str, default: &str| -> String {
            spec.get(key)
                .and_then(Value::as_str)
                .unwrap_or(default)
                .to_string()
        };
        doc.push_str(&csv_row(
            &get("record_id", "1"),
            &get("retraction_date", "3/9/2026 0:00"),
            &get("retraction_doi", "10.1/notice"),
            &get("retraction_pmid", "87654321"),
            &get("original_date", "5/6/2023 0:00"),
            &get("original_doi", "10.1/paper"),
            &get("original_pmid", "12345678"),
            &get("nature", "Retraction"),
            &get("reason", "Rogue Editor;"),
            &get("title", "A paper"),
            &get("journal", "Soft Computing"),
        ));
    }
    doc
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "columns" => {
            use bmlib::publications::retractions as r;
            serde_json::json!({
                "record_id": r::RECORD_ID_COLUMNS,
                "doi": r::DOI_COLUMNS,
                "pmid": r::PMID_COLUMNS,
                "notice_doi": r::NOTICE_DOI_COLUMNS,
                "notice_pmid": r::NOTICE_PMID_COLUMNS,
                "nature": r::NATURE_COLUMNS,
                "reason": r::REASON_COLUMNS,
                "title": r::TITLE_COLUMNS,
                "journal": r::JOURNAL_COLUMNS,
                "retraction_date": r::RETRACTION_DATE_COLUMNS,
                "original_date": r::ORIGINAL_DATE_COLUMNS,
            })
        }
        "clean_identifier" => {
            serde_json::json!(clean_identifier(args.get("value").and_then(Value::as_str)))
        }
        "split_reasons" => {
            serde_json::json!(split_reasons(args.get("value").and_then(Value::as_str)))
        }
        "parse_date" => {
            serde_json::json!(parse_date(args.get("value").and_then(Value::as_str)))
        }
        "find_column" => {
            // The corpus row may carry a null value, which Rust's map cannot
            // hold as a `&str`; a null is exactly the absent case.
            let row: Vec<(String, String)> = args["row"]
                .as_object()
                .map(|m| {
                    m.iter()
                        .map(|(k, v)| (k.clone(), v.as_str().unwrap_or_default().to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let candidates: Vec<&str> = args["candidates"]
                .as_array()
                .map(|a| a.iter().filter_map(Value::as_str).collect())
                .unwrap_or_default();
            match find_column(&row, &candidates) {
                Some(v) => serde_json::json!(v),
                None => Value::Null,
            }
        }
        "is_retracted" => {
            let notices: Vec<RetractionNotice> = args["notices"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(|n| {
                            let mut notice = RetractionNotice::new(
                                n["record_id"].as_str().unwrap_or_default(),
                                RetractionNature::from_str(
                                    n["nature"].as_str().unwrap_or_default(),
                                )
                                .expect("valid nature"),
                            );
                            notice.retraction_date = n
                                .get("retraction_date")
                                .and_then(Value::as_str)
                                .map(str::to_string);
                            notice
                        })
                        .collect()
                })
                .unwrap_or_default();
            serde_json::json!(is_retracted(&notices))
        }
        "parse" => {
            let doc = args["csv"].as_str().unwrap_or_default();
            match parse_retraction_watch_csv(doc.as_bytes()) {
                Ok(outcome) => outcome_json(&outcome),
                Err(e) => serde_json::json!({"__error": e.to_string()}),
            }
        }
        "parse_rows" => {
            let doc = rows_from_spec(&args["rows"]);
            match parse_retraction_watch_csv(doc.as_bytes()) {
                Ok(outcome) => outcome_json(&outcome),
                Err(e) => serde_json::json!({"__error": e.to_string()}),
            }
        }
        other => panic!("unknown fn {other:?}"),
    }
}

fn outcome_json(outcome: &bmlib::publications::retractions::ParseOutcome) -> Value {
    let notices: Vec<Value> = outcome.notices.iter().map(|n| n.to_json()).collect();
    let skipped: Vec<Value> = outcome
        .skipped
        .iter()
        .map(|s| serde_json::json!([s.line, s.reason.as_str()]))
        .collect();
    serde_json::json!({"notices": notices, "skipped": skipped})
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
// The retraction rule
// ---------------------------------------------------------------------------

fn notice(id: &str, nature: RetractionNature, date: Option<&str>) -> RetractionNotice {
    let mut n = RetractionNotice::new(id, nature);
    n.retraction_date = date.map(str::to_string);
    n
}

/// **The rule is not "latest notice wins".** A paper retracted in 2011 and
/// corrected in 2017 is still retracted — the correction is not evidence either
/// way — and 52 papers in the live export have exactly that shape. A flat
/// last-wins rule answers "not retracted" for all 52.
#[test]
fn a_correction_does_not_undo_a_retraction() {
    assert!(is_retracted(&[
        notice("1", RetractionNature::Retraction, Some("2011-01-01")),
        notice("2", RetractionNature::Correction, Some("2017-01-01")),
    ]));
}

/// A **reinstatement does** undo a retraction, and only when it is newer.
#[test]
fn a_reinstatement_undoes_a_retraction_only_when_newer() {
    assert!(!is_retracted(&[
        notice("1", RetractionNature::Retraction, Some("2011-01-01")),
        notice("2", RetractionNature::Reinstatement, Some("2017-01-01")),
    ]));
    assert!(is_retracted(&[
        notice("1", RetractionNature::Reinstatement, Some("2011-01-01")),
        notice("2", RetractionNature::Retraction, Some("2017-01-01")),
    ]));
}

/// An unknown nature is **skipped**, not treated as decisive. It is the one
/// distinction between "read this and it tells you nothing" and "read this and
/// it settles the question" — and treating `Other` as a decisive *false* means
/// an unknown notice *newer* than a real retraction answers "not retracted".
/// Retraction Watch re-wording `"Retraction"` is exactly how that arises, and
/// it is this feature's worst failure. Mutation found no test for it, because
/// the corpus had no case pairing `Other` with a Retraction.
#[test]
fn an_unknown_nature_is_skipped_rather_than_decisive() {
    assert!(
        is_retracted(&[
            notice("1", RetractionNature::Other, Some("2020-01-01")),
            notice("2", RetractionNature::Retraction, Some("2019-01-01")),
        ]),
        "an unknown notice must not defeat a real retraction"
    );
    assert!(!is_retracted(&[notice(
        "1",
        RetractionNature::Other,
        Some("2020-01-01")
    )]));
}

/// An Expression of Concern is evidence of nothing either way, so a paper with
/// only that notice is **not** reported as retracted.
#[test]
fn an_expression_of_concern_is_not_a_retraction() {
    assert!(!is_retracted(&[notice(
        "1",
        RetractionNature::ExpressionOfConcern,
        Some("2020-01-01")
    )]));
    assert!(!is_retracted(&[notice(
        "1",
        RetractionNature::Other,
        Some("2020-01-01")
    )]));
}

/// An undated notice sorts **last**, so it never displaces a dated one. `""`
/// sorts below any ISO date, and the sort is stable so the input order holds
/// within a tie.
#[test]
fn an_undated_notice_sorts_last() {
    let sorted = newest_first(&[
        notice("undated", RetractionNature::Retraction, None),
        notice("dated", RetractionNature::Reinstatement, Some("2017-01-01")),
    ]);
    assert_eq!(sorted[0].record_id, "dated");
    assert_eq!(sorted[1].record_id, "undated");

    // Hence a dated reinstatement defeats an undated retraction.
    assert!(!is_retracted(&[
        notice("1", RetractionNature::Retraction, None),
        notice("2", RetractionNature::Reinstatement, Some("2017-01-01")),
    ]));
}

/// The sort is stable, so two notices sharing a date keep the order the lookup
/// returned — which is what makes the answer reproducible across backends.
#[test]
fn a_tie_keeps_the_input_order() {
    let sorted = newest_first(&[
        notice("first", RetractionNature::Reinstatement, Some("2020-01-01")),
        notice("second", RetractionNature::Retraction, Some("2020-01-01")),
    ]);
    assert_eq!(sorted[0].record_id, "first");
    assert_eq!(sorted[1].record_id, "second");
}

/// No notices is not retracted — the empty answer, not an error.
#[test]
fn no_notices_is_not_retracted() {
    assert!(!is_retracted(&[]));
}

// ---------------------------------------------------------------------------
// The export's sentinels
// ---------------------------------------------------------------------------

/// `"0"` and `"unavailable"` mean "no identifier here" **and neither is
/// falsy**, so a truthiness test stores them. Measured over the 2026-08-03
/// export: `"0"` in 46.04% of PubMed ID cells, `"unavailable"` in 4.80% of DOI
/// cells. Storing them collapses tens of thousands of unrelated notices onto
/// one key.
#[test]
fn the_exports_sentinels_are_not_identifiers() {
    for sentinel in ABSENT_IDENTIFIER_VALUES {
        assert_eq!(clean_identifier(Some(sentinel)), None, "{sentinel:?}");
    }
    // Both casings occur in the export.
    assert_eq!(clean_identifier(Some("Unavailable")), None);
    assert_eq!(clean_identifier(Some("UNAVAILABLE")), None);
    // Padding is stripped before the comparison.
    assert_eq!(clean_identifier(Some("  0  ")), None);
    assert_eq!(clean_identifier(Some("")), None);
    assert_eq!(clean_identifier(Some("   ")), None);
    assert_eq!(clean_identifier(None), None);
}

/// A `0` that is *part of* a real identifier is untouched — the comparison is
/// on the whole stripped value, not a substring.
#[test]
fn a_zero_inside_a_real_identifier_survives() {
    assert_eq!(clean_identifier(Some("10.1/x0")), Some("10.1/x0".into()));
    assert_eq!(clean_identifier(Some("0123")), Some("0123".into()));
    assert_eq!(clean_identifier(Some("10.1/x")), Some("10.1/x".into()));
}

/// The same screen guards the tool that reads a stored identifier, which is why
/// it is a public function: a caller whose PMID column stores `"0"` for
/// "absent" would otherwise query for a paper that cannot exist and read the
/// empty result as "not retracted".
#[test]
fn a_sentinel_from_a_callers_column_is_also_unusable() {
    use bmlib::publications::retractions::unusable_lookup_identifier;
    assert!(unusable_lookup_identifier(Some("Unavailable"), Some("0")));
    assert!(unusable_lookup_identifier(None, None));
    assert!(!unusable_lookup_identifier(Some("10.1/x"), None));
    assert!(!unusable_lookup_identifier(None, Some("42")));
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

/// The export's `M/D/YYYY H:MM` is parsed, and the time component is discarded.
#[test]
fn the_export_date_form_is_parsed() {
    assert_eq!(parse_date(Some("3/9/2026 0:00")), Some("2026-03-09".into()));
    assert_eq!(
        parse_date(Some("12/25/2021 0:00")),
        Some("2021-12-25".into())
    );
}

/// **Month-first is the documented ambiguity resolution**, and `"5/6/2024"` is
/// the case that pins it: both fields are ≤ 12, so nothing in the row says
/// which was meant. `%d/%m/%Y` first would produce `2024-06-05`.
#[test]
fn an_ambiguous_date_resolves_month_first() {
    assert_eq!(parse_date(Some("5/6/2024 0:00")), Some("2024-05-06".into()));
    // And a day over 12 can only be day-first, which the fallback reaches.
    assert_eq!(
        parse_date(Some("25/12/2021 0:00")),
        Some("2021-12-25".into())
    );
}

/// An unparseable date is `None`, not a failed row — a missing date is worth
/// less than a lost retraction.
#[test]
fn an_unparseable_date_is_none_not_an_error() {
    assert_eq!(parse_date(Some("not a date")), None);
    assert_eq!(parse_date(Some("")), None);
    assert_eq!(parse_date(None), None);
    assert_eq!(parse_date(Some("   ")), None);
}

/// The formats validate the calendar, so an impossible date is refused rather
/// than rolled forward.
#[test]
fn an_impossible_date_is_refused() {
    assert_eq!(parse_date(Some("2/29/2024")), Some("2024-02-29".into()));
    assert_eq!(
        parse_date(Some("2/29/2023")),
        None,
        "2023 is not a leap year"
    );
    assert_eq!(parse_date(Some("2/30/2024")), None);
    // `13/1/2024` is *not* refused: month-first fails, but the day-first
    // fallback parses it as 13 January. A day over 31 is what no format
    // accepts.
    assert_eq!(parse_date(Some("13/1/2024")), Some("2024-01-13".into()));
    assert_eq!(parse_date(Some("32/1/2024")), None);
    assert_eq!(parse_date(Some("13/13/2024")), None);
    assert_eq!(
        parse_date(Some("3/9/26")),
        None,
        "a 2-digit year is not a format"
    );
}

/// `DATE_FORMATS` puts month-first immediately ahead of day-first, and **that
/// relative order is the ambiguity resolution** rather than an optimisation.
#[test]
fn the_month_first_format_precedes_the_day_first_one() {
    assert_eq!(DATE_FORMATS[0], "%m/%d/%Y");
    assert_eq!(DATE_FORMATS[1], "%d/%m/%Y");
}

// ---------------------------------------------------------------------------
// Reasons
// ---------------------------------------------------------------------------

/// Every populated row of the export ends with `;`, so the trailing empty part
/// is dropped rather than becoming a blank reason.
#[test]
fn a_trailing_semicolon_does_not_become_a_reason() {
    assert_eq!(split_reasons(Some("Rogue Editor;")), vec!["Rogue Editor"]);
    assert_eq!(
        split_reasons(Some("Plagiarism; Duplicate publication;")),
        vec!["Plagiarism", "Duplicate publication"]
    );
    assert_eq!(split_reasons(Some(";;a;;")), vec!["a"]);
}

/// A single leading `+` belongs to Retraction Watch's own export rather than
/// Crossref's, and is stripped.
#[test]
fn a_leading_plus_is_stripped() {
    assert_eq!(split_reasons(Some("+Rogue Editor;")), vec!["Rogue Editor"]);
    assert_eq!(
        split_reasons(Some("+ Plagiarism; Data fabricated")),
        vec!["Plagiarism", "Data fabricated"]
    );
    // Only one, and only at the start.
    assert_eq!(split_reasons(Some("++x")), vec!["+x"]);
}

// ---------------------------------------------------------------------------
// Column resolution
// ---------------------------------------------------------------------------

/// The retracted paper's identifier is preferred to the notice's, and a bare
/// `DOI`/`PMID` column is deliberately not a candidate — such a column could
/// mean either paper, and guessing is what let upstream's resolution return the
/// notice's identifier for the retracted paper.
#[test]
fn the_retracted_papers_identifier_columns_exclude_the_bare_names() {
    use bmlib::publications::retractions::{
        DOI_COLUMNS, NOTICE_DOI_COLUMNS, NOTICE_PMID_COLUMNS, PMID_COLUMNS,
    };
    for candidates in [&DOI_COLUMNS[..], &PMID_COLUMNS[..]] {
        assert!(
            !candidates.contains(&"DOI") && !candidates.contains(&"PMID"),
            "a bare name must not be a candidate: {candidates:?}"
        );
    }
    // The two pairs are disjoint, so the two papers cannot be conflated.
    for name in NOTICE_DOI_COLUMNS {
        assert!(!DOI_COLUMNS.contains(&name));
    }
    for name in NOTICE_PMID_COLUMNS {
        assert!(!PMID_COLUMNS.contains(&name));
    }
}

/// A blank cell falls through to the next candidate; a null reads as absent.
#[test]
fn a_blank_cell_falls_through_to_the_next_candidate() {
    let row = vec![
        ("Record ID".to_string(), "   ".to_string()),
        ("RecordID".to_string(), "9".to_string()),
    ];
    assert_eq!(find_column(&row, &["Record ID", "RecordID"]), Some("9"));
    let absent = vec![("x".to_string(), "1".to_string())];
    assert_eq!(find_column(&absent, &["Record ID"]), None);
}

// ---------------------------------------------------------------------------
// Parsing a document
// ---------------------------------------------------------------------------

fn parse_doc(doc: &str) -> bmlib::publications::retractions::ParseOutcome {
    parse_retraction_watch_csv(doc.as_bytes()).expect("parses")
}

/// A good row becomes a notice with **both** identifier pairs, distinguished.
#[test]
fn a_row_becomes_a_notice_with_both_identifier_pairs() {
    let outcome = parse_doc(&(HEADER.to_string() + &default_row()));
    assert_eq!(outcome.notices.len(), 1);
    let n = &outcome.notices[0];
    assert_eq!(n.record_id, "1");
    assert_eq!(n.doi.as_deref(), Some("10.1/paper"), "the retracted paper");
    assert_eq!(n.notice_doi.as_deref(), Some("10.1/notice"), "the notice");
    assert_eq!(n.pmid.as_deref(), Some("12345678"));
    assert_eq!(n.notice_pmid.as_deref(), Some("87654321"));
    assert_eq!(n.retraction_date.as_deref(), Some("2026-03-09"));
    assert_eq!(n.original_paper_date.as_deref(), Some("2023-05-06"));
    assert_eq!(n.reasons, vec!["Rogue Editor"]);
    assert_eq!(n.nature, RetractionNature::Retraction);
    assert!(outcome.skipped.is_empty());
}

/// A row with no usable identifier is **reported, not stored** — and the line
/// number is the physical one.
#[test]
fn an_unusable_row_is_reported_with_its_line() {
    let bad = csv_row(
        "3",
        "3/9/2026 0:00",
        "10.1/n",
        "1",
        "5/6/2023 0:00",
        "Unavailable",
        "0",
        "Retraction",
        "x;",
        "T",
        "J",
    );
    let doc = HEADER.to_string() + &default_row() + &bad;
    let outcome = parse_doc(&doc);
    assert_eq!(outcome.notices.len(), 1);
    assert_eq!(outcome.skipped.len(), 1);
    assert_eq!(outcome.skipped[0].line, 3, "header 1, row 2, bad row 3");
    assert_eq!(outcome.skipped[0].reason, SkipReason::NoIdentifier);
}

/// A quoted field holding a newline makes one CSV record span two physical
/// lines, and the reported line must account for it. A per-record counter would
/// report 3 where an editor shows 4.
#[test]
fn a_reported_line_accounts_for_an_embedded_newline() {
    let multiline = csv_row(
        "2",
        "3/9/2026 0:00",
        "10.1/n",
        "1",
        "5/6/2023 0:00",
        "10.1/a",
        "2",
        "Retraction",
        "x;",
        "\"multi\nline title\"",
        "J",
    );
    let unusable = csv_row(
        "3",
        "3/9/2026 0:00",
        "10.1/n",
        "1",
        "5/6/2023 0:00",
        "Unavailable",
        "0",
        "Retraction",
        "x;",
        "T",
        "J",
    );
    let doc = HEADER.to_string() + &default_row() + &multiline + &unusable;
    let outcome = parse_doc(&doc);
    assert_eq!(outcome.notices.len(), 2);
    // Lines: 1 header, 2 row 1, 3-4 the two-line row, 5 the unusable row.
    assert_eq!(outcome.skipped[0].line, 5);
}

/// An unknown nature still yields a notice — it must cost one row of fidelity
/// rather than abort the import — but it is **reported once per distinct
/// value**, because `is_retracted` reads it as evidence of nothing and a silent
/// import answering "not retracted" for every paper is this feature's worst
/// failure.
#[test]
fn an_unknown_nature_is_preserved_and_reported_once() {
    let unknown = |id: &str| {
        csv_row(
            id,
            "3/9/2026 0:00",
            "10.1/n",
            "1",
            "5/6/2023 0:00",
            "10.1/a",
            "2",
            "Novel kind",
            "x;",
            "T",
            "J",
        )
    };
    let doc = HEADER.to_string() + &unknown("1") + &unknown("2");
    let outcome = parse_doc(&doc);

    assert_eq!(outcome.notices.len(), 2, "both rows are kept");
    assert_eq!(outcome.notices[0].nature, RetractionNature::Other);
    assert_eq!(
        outcome.notices[0].raw_nature.as_deref(),
        Some("Novel kind"),
        "the original string is kept"
    );
    assert_eq!(
        outcome.unknown_natures.len(),
        1,
        "reported once, not once per row"
    );
}

/// The `Expression of concern` spelling the export actually uses maps
/// correctly, and is not reported as unknown.
#[test]
fn the_exports_expression_of_concern_is_recognised() {
    let row = csv_row(
        "1",
        "3/9/2026 0:00",
        "10.1/n",
        "1",
        "5/6/2023 0:00",
        "10.1/a",
        "2",
        "Expression of concern",
        "x;",
        "T",
        "J",
    );
    let outcome = parse_doc(&(HEADER.to_string() + &row));
    assert_eq!(
        outcome.notices[0].nature,
        RetractionNature::ExpressionOfConcern
    );
    assert!(outcome.unknown_natures.is_empty());
}

/// An entirely empty row has no `Record ID` and is skipped — the live export
/// ends with 190 of them.
#[test]
fn an_empty_row_is_skipped() {
    let doc = "Record ID,Title\n,,\n,,\n";
    let outcome = parse_doc(doc);
    assert!(outcome.notices.is_empty());
    assert_eq!(outcome.skipped.len(), 2);
    assert_eq!(outcome.skipped[0].reason, SkipReason::NoRecordId);
}

/// `row_to_notice` is usable on its own, which is what a caller parsing a row
/// from another source needs.
#[test]
fn a_row_can_be_converted_without_a_document() {
    let fields: Vec<(String, String)> = vec![
        ("Record ID".to_string(), "R1".to_string()),
        ("OriginalPaperDOI".to_string(), "10.1/x".to_string()),
        ("RetractionNature".to_string(), "Retraction".to_string()),
    ];
    let mut unknown = Vec::new();
    let notice = row_to_notice(&fields, &mut unknown).expect("usable");
    assert_eq!(notice.record_id, "R1");
    assert!(unknown.is_empty());

    let no_id: Vec<(String, String)> = vec![("OriginalPaperDOI".to_string(), "10.1/x".to_string())];
    assert_eq!(
        row_to_notice(&no_id, &mut unknown).unwrap_err(),
        SkipReason::NoRecordId
    );
}
