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

//! PubMed E-utilities walk — the oracle and the named tests.
//!
//! The corpus (27 cases) diffs the partition ladder and the session walk against
//! Python, with the counting function injected so a test can present **two
//! counts at two instants** — the input the derived-zero measurement exists for.

use bmlib::publications::fetchers::pubmed::{
    edat_range_term, part_key, plan_partitions, walk_session, EFetchPage, PlanError,
    EFETCH_MAX_RETRIEVABLE, EFETCH_PAGE_SIZE,
};
use chrono::NaiveDate;
use serde_json::{json, Value};
use std::collections::BTreeMap;

const CASES: &str = include_str!("data/pubmed_walk_cases.json");
const EXPECTED: &str = include_str!("data/pubmed_walk_expected.json");

fn date(raw: &str) -> NaiveDate {
    NaiveDate::parse_from_str(raw, "%Y-%m-%d").expect("corpus date")
}

/// A count function over a scripted map, recording every term asked.
struct ScriptedCounter {
    counts: BTreeMap<String, i64>,
    default: i64,
    terms: Vec<String>,
}

impl ScriptedCounter {
    fn call(&mut self, term: &str) -> Result<i64, String> {
        self.terms.push(term.to_string());
        Ok(self.counts.get(term).copied().unwrap_or(self.default))
    }
}

fn plan_value(case: &Value) -> Value {
    let args = &case["args"];
    let counts: BTreeMap<String, i64> = args
        .get("counts")
        .and_then(Value::as_object)
        .map(|m| {
            m.iter()
                .map(|(k, v)| (k.clone(), v.as_i64().unwrap_or(0)))
                .collect()
        })
        .unwrap_or_default();
    let mut counter = ScriptedCounter {
        counts,
        default: args.get("default").and_then(Value::as_i64).unwrap_or(0),
        terms: Vec::new(),
    };
    let day_term = args["day_term"].as_str().unwrap_or_default();
    let day_count = args["day_count"].as_i64().unwrap_or(0);
    let lo = date(
        args.get("lo")
            .and_then(Value::as_str)
            .unwrap_or("1900-01-01"),
    );
    let hi = date(
        args.get("hi")
            .and_then(Value::as_str)
            .unwrap_or("2100-12-31"),
    );
    let probe_root = args
        .get("probe_root")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let known_count = args.get("known_count").and_then(Value::as_i64);

    let mut count_fn = |term: &str| counter.call(term);
    let result = plan_partitions(
        &mut count_fn,
        day_term,
        day_count,
        lo,
        hi,
        probe_root,
        known_count,
    );
    let terms: Vec<Value> = counter.terms.iter().map(|t| json!(t)).collect();

    match result {
        Ok(parts) => json!({
            "ok": true,
            "parts": parts.iter().map(|p| json!([
                p.lo.format("%Y-%m-%d").to_string(),
                p.hi.format("%Y-%m-%d").to_string(),
                p.promised,
                p.key(),
            ])).collect::<Vec<_>>(),
            "terms": terms,
        }),
        Err(PlanError::Unsplittable { .. }) => {
            json!({"ok": false, "error": "Unsplittable", "terms": terms, "message": ""})
        }
        Err(PlanError::RootNotCovering { .. }) => {
            json!({"ok": false, "error": "RootNotCovering", "terms": terms, "message": ""})
        }
        Err(PlanError::InvertedRoot { .. }) => {
            json!({"ok": false, "error": "ValueError", "terms": terms, "message": ""})
        }
    }
}

fn walk_value(case: &Value) -> Value {
    let args = &case["args"];
    let promised = args["promised"].as_i64().unwrap_or(0);
    let pages: Vec<Value> = args["pages"].as_array().cloned().unwrap_or_default();
    let mut remaining = pages.into_iter();
    let mut retstarts: Vec<i64> = Vec::new();
    let mut progress: Vec<i64> = Vec::new();

    let outcome = walk_session(
        promised,
        |retstart| {
            retstarts.push(retstart as i64);
            let page = remaining.next().unwrap_or_else(|| json!({"delivered": 0}));
            if let Some(message) = page.as_str() {
                // Python's `_efetch_page` raises, and the walker's message
                // carries the exception type name — which is what separates a
                // bmlib defect from a bad response.
                return Err(format!("ValueError: {message}"));
            }
            // The corpus states how many elements were *articles*; the rest of
            // the delivery is book articles, which are delivered and not parsed.
            let articles = page.get("articles").and_then(Value::as_i64).unwrap_or(0) as usize;
            Ok(EFetchPage {
                articles: (0..articles)
                    .map(|i| {
                        bmlib::publications::models::FetchedRecord::new(format!("a{i}"), "pubmed")
                    })
                    .collect(),
                delivered: page.get("delivered").and_then(Value::as_i64).unwrap_or(0),
            })
        },
        &mut |_record| {},
        &mut |processed| progress.push(processed),
    );

    json!({
        "processed": outcome.processed,
        "delivered": outcome.delivered,
        "stalled": outcome.stalled,
        "error": outcome.error,
        "retstarts": retstarts,
        "progress": progress,
    })
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    match fn_name {
        "part_key" => json!(part_key(
            date(args["lo"].as_str().unwrap_or_default()),
            date(args["hi"].as_str().unwrap_or_default())
        )),
        "edat_range_term" => json!(edat_range_term(
            args["day_term"].as_str().unwrap_or_default(),
            date(args["lo"].as_str().unwrap_or_default()),
            date(args["hi"].as_str().unwrap_or_default())
        )),
        "plan" => plan_value(case),
        "walk" => walk_value(case),
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
        // The corpus carries Python's exception *message* for the two plan
        // refusals; this port's `Display` reproduces them, so only the variant
        // name is compared here and the messages are pinned in the named tests.
        let mut expected_value = want["value"].clone();
        if let Some(message) = expected_value.get("message") {
            if message.is_string() {
                expected_value["message"] = json!("");
            }
        }
        if got != expected_value {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&expected_value).unwrap_or_default(),
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
// The part key
// ---------------------------------------------------------------------------

/// The key is the **one constructor** for a part's stored identity, and the
/// resume skip rule compares it as a string: a second spelling of the same range
/// matches no checkpoint, so resume degrades to a full re-fetch **with nothing
/// raised**.
#[test]
fn the_part_key_matches_pythons_exact_spelling() {
    assert_eq!(
        part_key(date("2024-06-10"), date("2024-06-12")),
        "edat:2024-06-10:2024-06-12"
    );
    // Not an underscore separator, and not missing the scheme prefix. A first
    // cut of this port wrote `"{lo}_{hi}"`, which would have matched no row
    // Python ever wrote.
    let key = part_key(date("2024-06-10"), date("2024-06-12"));
    assert!(key.starts_with("edat:"), "{key}");
    assert!(!key.contains('_'), "{key}");
}

/// The EDAT range term restricts the day's own term to an inclusive Entrez-date
/// range, so the ladder's parts are disjoint and covering.
#[test]
fn the_edat_range_term_restricts_the_day_term() {
    assert_eq!(
        edat_range_term(
            "(\"2024/06/10\"[Date - Publication])",
            date("2024-06-10"),
            date("2024-06-12")
        ),
        "(\"2024/06/10\"[Date - Publication]) AND \
         (\"2024/06/10\"[EDAT] : \"2024/06/12\"[EDAT])"
    );
}

// ---------------------------------------------------------------------------
// The ladder
// ---------------------------------------------------------------------------

/// A day at or under the cap is one part; above it, the ladder splits. The cap
/// is inclusive because it is what one history session can *serve*.
#[test]
fn a_day_at_the_cap_is_one_part_and_above_it_splits() {
    let mut at_cap = |_: &str| Ok(EFETCH_MAX_RETRIEVABLE);
    let parts = plan_partitions(
        &mut at_cap,
        "T",
        EFETCH_MAX_RETRIEVABLE,
        date("2024-06-01"),
        date("2024-06-30"),
        true,
        None,
    )
    .expect("plans");
    assert_eq!(parts.len(), 1);
    assert_eq!(parts[0].promised, EFETCH_MAX_RETRIEVABLE);

    let mut over = |term: &str| {
        if term == edat_range_term("T", date("2024-06-01"), date("2024-06-30")) {
            Ok(EFETCH_MAX_RETRIEVABLE + 1)
        } else {
            Ok(6000)
        }
    };
    let parts = plan_partitions(
        &mut over,
        "T",
        EFETCH_MAX_RETRIEVABLE + 1,
        date("2024-06-01"),
        date("2024-06-30"),
        true,
        None,
    )
    .expect("plans");
    assert!(parts.len() > 1, "an over-cap day must split");
    for part in &parts {
        assert!(part.promised <= EFETCH_MAX_RETRIEVABLE);
    }
}

/// **The root probe**: coming up short at the root means some record of the day
/// is indexed outside the ladder and would be silently absent, so the day is
/// refused rather than fetched as an incomplete set.
#[test]
fn a_root_that_does_not_cover_the_day_refuses_it() {
    let mut short = |_: &str| Ok(50);
    let err = plan_partitions(
        &mut short,
        "T",
        100,
        date("2024-06-01"),
        date("2024-06-30"),
        true,
        None,
    )
    .expect_err("refused");
    match err {
        PlanError::RootNotCovering {
            root_count,
            day_count,
            ..
        } => {
            assert_eq!(root_count, 50);
            assert_eq!(day_count, 100);
        }
        other => panic!("expected RootNotCovering, got {other:?}"),
    }

    // And the refusal names both numbers, because the operator's next question
    // is how many records are missing.
    let message = err.to_string();
    assert!(message.contains("50"), "{message}");
    assert!(message.contains("100"), "{message}");
    assert!(message.contains("silently absent"), "{message}");
}

/// An **inverted** root range is refused: the ladder cannot tile it.
#[test]
fn an_inverted_root_is_refused() {
    let mut never = |_: &str| Ok(0);
    let err = plan_partitions(
        &mut never,
        "T",
        0,
        date("2024-06-30"),
        date("2024-06-01"),
        true,
        None,
    )
    .expect_err("refused");
    assert!(matches!(err, PlanError::InvertedRoot { .. }));
    assert!(err.to_string().contains("inverted"), "{err}");
}

/// A single Entrez date above the cap **cannot be split further**, and the
/// refusal names the count an ESearch actually returned.
#[test]
fn an_unsplittable_entrez_date_is_refused() {
    let mut big = |_: &str| Ok(20_000);
    let err = plan_partitions(
        &mut big,
        "T",
        20_000,
        date("2024-06-10"),
        date("2024-06-10"),
        true,
        Some(20_000),
    )
    .expect_err("refused");
    match err {
        PlanError::Unsplittable { count, .. } => assert_eq!(count, 20_000),
        other => panic!("expected Unsplittable, got {other:?}"),
    }
    let message = err.to_string();
    assert!(message.contains("cannot be split further"), "{message}");
    assert!(
        message.contains(&EFETCH_MAX_RETRIEVABLE.to_string()),
        "{message}"
    );
}

/// **A derived zero is measured, not trusted**, and this is the one wrong
/// derivation that cannot heal.
///
/// Every other error in the right half still yields a part, and a part re-counts
/// itself when its session opens; a zero yields **no part at all**, so the range
/// is never visited, every part planned around it reconciles perfectly, and the
/// shortfall reaches only the day total — where anything under the floor
/// completes on a note. `completed` is durable, so those records are never
/// sought again.
///
/// The corpus's `plan/derived-zero-is-measured` is this case, and its recorded
/// terms show the measurement happening: the right half is asked for *before* it
/// is written off.
#[test]
fn a_derived_zero_is_measured_rather_than_trusted() {
    let day_term = "T";
    let range = |lo: &str, hi: &str| edat_range_term(day_term, date(lo), date(hi));
    let root = range("2024-06-01", "2024-06-30");
    let left = range("2024-06-01", "2024-06-15");
    let left_left = range("2024-06-01", "2024-06-08");
    let left_right = range("2024-06-09", "2024-06-15");
    let derived_empty = range("2024-06-16", "2024-06-30");

    // The corpus's counts: the parent and its left child both report 11,000, so
    // subtraction derives zero for the right sibling — while the records are in
    // fact all in the left child's own halves, which measure 6,000 and 5,000.
    let mut asked: Vec<String> = Vec::new();
    let mut scripted = |term: &str| {
        asked.push(term.to_string());
        Ok(if term == root.as_str() || term == left.as_str() {
            11_000
        } else if term == left_left.as_str() {
            6_000
        } else if term == left_right.as_str() {
            5_000
        } else {
            0
        })
    };
    let parts = plan_partitions(
        &mut scripted,
        day_term,
        11_000,
        date("2024-06-01"),
        date("2024-06-30"),
        true,
        None,
    )
    .expect("plans");

    // The zero is measured before it is trusted, which is the whole point.
    assert!(
        asked.contains(&derived_empty),
        "the derived zero must have been measured, not trusted: {asked:?}"
    );
    // Both real halves are found, by counting rather than by subtraction.
    let ranges: Vec<(String, String)> = parts
        .iter()
        .map(|p| {
            (
                p.lo.format("%Y-%m-%d").to_string(),
                p.hi.format("%Y-%m-%d").to_string(),
            )
        })
        .collect();
    assert_eq!(
        ranges,
        vec![
            ("2024-06-01".to_string(), "2024-06-08".to_string()),
            ("2024-06-09".to_string(), "2024-06-15".to_string()),
        ]
    );
    // And the two together hold every record the day had.
    let total: i64 = parts.iter().map(|p| p.promised).sum();
    assert_eq!(total, 11_000, "every record must land in a part: {parts:?}");
}

/// A range the subtraction wrote off as empty is **measured**, and when it
/// really is empty it contributes no part and does not fail the day.
///
/// A parent counted higher than its children hold parks the surplus on the
/// right, and the root reaches 2100 — so the surplus can walk down a
/// structurally empty tail to a single future date claiming tens of thousands of
/// records. Refused on that, the day fails and is re-fetched on every later run
/// (~562 requests and ~1 GB each time) over a range PubMed has never indexed
/// anything into. Measured, the phantom is 0 and simply disappears.
#[test]
fn a_phantom_half_measured_empty_disappears_rather_than_failing_the_day() {
    let day_term = "T";
    let range = |lo: &str, hi: &str| edat_range_term(day_term, date(lo), date(hi));
    let root = range("2024-06-01", "2024-06-30");
    let top_left = range("2024-06-01", "2024-06-15");
    let right_half = range("2024-06-16", "2024-06-30");

    // The root promises 20,000 and its left child reports 15,000, so
    // subtraction parks a 5,000 surplus on the right. The right half really
    // holds those 5,000, and every range *inside* the left child measures 0 —
    // the left child's own report was the overstatement. Without the
    // whole-range measurement the ladder walks that empty tail down to a single
    // date, measures 0 there, and the day fails as unsplittable.
    let mut asked: Vec<String> = Vec::new();
    let mut scripted = |term: &str| {
        asked.push(term.to_string());
        Ok(if term == root.as_str() {
            20_000
        } else if term == top_left.as_str() {
            15_000
        } else if term == right_half.as_str() {
            5_000
        } else {
            0
        })
    };
    let parts = plan_partitions(
        &mut scripted,
        day_term,
        15_000,
        date("2024-06-01"),
        date("2024-06-30"),
        true,
        None,
    )
    .expect("the day must plan, not fail");

    // The empty left tail contributes nothing, and the real right half is the
    // one part.
    assert_eq!(parts.len(), 1, "{parts:?}");
    assert_eq!(parts[0].promised, 5_000);
    assert_eq!(parts[0].lo.format("%Y-%m-%d").to_string(), "2024-06-16");
}

/// **`known_count` skips the root probe**, and that is what makes the
/// re-partition path terminate: re-counting the same range fresh risks a *lower*
/// answer that collapses back to a single partition spanning the identical
/// range — pushed onto the queue, fetched again, over-cap again, looping against
/// NCBI for ever.
#[test]
fn a_known_count_skips_the_probe_and_never_grows() {
    let mut asked = 0usize;
    let mut counter = |_: &str| {
        asked += 1;
        Ok(0)
    };
    // The day count is far above the known count, which would refuse on a
    // probe. With `known_count` given, no probe runs.
    let parts = plan_partitions(
        &mut counter,
        "T",
        999_999,
        date("2024-06-10"),
        date("2024-06-10"),
        true,
        Some(5),
    )
    .expect("plans");
    assert_eq!(asked, 0, "no request may be made for a known count");
    assert_eq!(parts.len(), 1);
    assert_eq!(parts[0].promised, 5);
}

// ---------------------------------------------------------------------------
// The session walk
// ---------------------------------------------------------------------------

/// A page is strided by **`EFETCH_PAGE_SIZE`**, not by what it delivered.
///
/// `retstart` indexes the session's UID list, not the records delivered so far.
/// Advancing by what arrived — the fix #96 proposed — would re-request the tail
/// of every short page, deliver those records twice, and count the duplicates as
/// delivery, which is precisely what would hide a real shortfall from the
/// reconciliation.
#[test]
fn a_short_page_still_strides_by_the_page_size() {
    let mut pages = vec![
        Ok(EFetchPage {
            articles: Vec::new(),
            delivered: 3,
        });
        3
    ];
    let mut retstarts = Vec::new();
    let outcome = walk_session(
        1200,
        |retstart| {
            retstarts.push(retstart);
            pages.remove(0)
        },
        &mut |_| {},
        &mut |_| {},
    );
    assert_eq!(outcome.delivered, 9);
    assert_eq!(retstarts, vec![0, EFETCH_PAGE_SIZE, 2 * EFETCH_PAGE_SIZE]);
    assert!(outcome.error.is_none());
}

/// **`delivered` counts what the server handed over, not what was parsed.** A
/// `<PubmedBookArticle>` is delivered and deliberately not parsed, so counting
/// parsed records would report a phantom shortfall on every day carrying a book
/// chapter.
#[test]
fn book_articles_count_as_delivered_and_not_as_processed() {
    let mut pages = vec![Ok(EFetchPage {
        articles: Vec::new(),
        delivered: 3,
    })];
    let outcome = walk_session(3, |_| pages.remove(0), &mut |_| {}, &mut |_| {});
    assert_eq!(outcome.delivered, 3, "the server handed over three");
    assert_eq!(outcome.processed, 0, "none of them was a paper");
    assert!(!outcome.stalled);
}

/// An empty page while the session still holds UIDs is a **stall**, and the walk
/// stops rather than paging on: paging on costs a request per remaining page and
/// returns nothing — up to 9 of them on the 5,000-record day measured for #88.
#[test]
fn an_empty_page_before_the_walk_is_done_is_a_stall() {
    let mut pages = vec![
        Ok(EFetchPage {
            articles: Vec::new(),
            delivered: 500,
        }),
        Ok(EFetchPage {
            articles: Vec::new(),
            delivered: 0,
        }),
    ];
    let mut retstarts = Vec::new();
    let outcome = walk_session(
        1000,
        |r| {
            retstarts.push(r);
            pages.remove(0)
        },
        &mut |_| {},
        &mut |_| {},
    );
    assert!(outcome.stalled);
    assert_eq!(outcome.delivered, 500);
    assert_eq!(retstarts.len(), 2, "the walk must stop, not page on");
    assert!(outcome.error.is_none());
}

/// A page that **raises** stops the walk and reports the error, with the
/// delivered and processed counts kept — the caller fails the day from it.
#[test]
fn a_page_error_stops_the_walk_and_is_reported() {
    let mut pages: Vec<Result<EFetchPage, String>> = vec![
        Ok(EFetchPage {
            articles: Vec::new(),
            delivered: 500,
        }),
        Err("efetch returned <eFetchResult>".to_string()),
    ];
    let mut retstarts = Vec::new();
    let outcome = walk_session(
        1000,
        |r| {
            retstarts.push(r);
            pages.remove(0)
        },
        &mut |_| {},
        &mut |_| {},
    );
    assert_eq!(
        outcome.error.as_deref(),
        Some("efetch returned <eFetchResult>")
    );
    assert_eq!(outcome.delivered, 500, "what arrived before the failure");
    assert!(!outcome.stalled, "an error is not a stall");
    assert_eq!(retstarts.len(), 2);
}

/// Progress is reported after each page that delivered, with the running
/// processed count — and **not** after a stalled or failed page.
#[test]
fn progress_is_reported_only_after_a_delivering_page() {
    let mut pages = vec![
        Ok(EFetchPage {
            articles: Vec::new(),
            delivered: 500,
        }),
        Ok(EFetchPage {
            articles: Vec::new(),
            delivered: 0,
        }),
    ];
    let mut progress = Vec::new();
    let _ = walk_session(1000, |_| pages.remove(0), &mut |_| {}, &mut |p| {
        progress.push(p)
    });
    assert_eq!(progress, vec![0], "one delivering page, one report");
}

/// A promise of zero walks nothing at all.
#[test]
fn a_promise_of_zero_makes_no_request() {
    let mut asked = 0usize;
    let outcome = walk_session(
        0,
        |_| {
            asked += 1;
            Ok(EFetchPage::default())
        },
        &mut |_| {},
        &mut |_| {},
    );
    assert_eq!(asked, 0);
    assert!(!outcome.stalled);
    assert_eq!(outcome.delivered, 0);
}
