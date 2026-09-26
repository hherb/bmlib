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

//! Publication models — the named tests.
//!
//! `pubmodels_oracle` diffs 88 cases against Python, 56 of them on the three
//! validators. This file states why those validators exist and what their
//! refusals protect, which is the part a corpus of messages cannot say.

use bmlib::publications::models::{
    now_utc, parse_iso8601, require_count, require_datetime, require_text, DayStatus, DownloadDay,
    Grant, PartCheckpoint, Publication, RetractionNature, RetractionNotice,
};
use serde_json::json;
use std::str::FromStr;

// ---------------------------------------------------------------------------
// `require_text` — a blank name silently matches nothing
// ---------------------------------------------------------------------------

/// A null `part_key` becoming the literal `"None"` is the failure this
/// prevents: it matches no planned part, so resume degrades to re-fetching
/// every unfinished day in full — a cost with no error and no line.
#[test]
fn a_blank_part_name_is_refused_rather_than_stored() {
    for blank in [json!(null), json!(""), json!("   "), json!("\t\n")] {
        assert!(
            require_text(Some(&blank), "part_key").is_err(),
            "{blank} must be refused"
        );
    }
    assert!(require_text(None, "part_key").is_err());
    assert_eq!(
        require_text(Some(&json!("k")), "part_key"),
        Ok("k".to_string())
    );
}

/// The refusal names the column, because a bulk deserialiser reports this and
/// an anonymous failure leaves it nothing to say.
#[test]
fn every_refusal_names_its_column() {
    let err = require_text(None, "part_scheme").expect_err("refused");
    assert!(err.to_string().starts_with("part_scheme is required"));

    let err = require_text(Some(&json!(5)), "part_key").expect_err("refused");
    assert!(err
        .to_string()
        .starts_with("part_key must be a string, got int"));
}

// ---------------------------------------------------------------------------
// `require_count` — `bool` is an `int`, and `int()` lies about why
// ---------------------------------------------------------------------------

/// `bool` is refused explicitly. Nothing else in the validator would catch it,
/// and `True` would otherwise be stored as a count of 1.
#[test]
fn a_boolean_is_not_a_count() {
    let err = require_count(Some(&json!(true)), "promised", 1).expect_err("refused");
    assert!(err.to_string().contains("got bool"), "{err}");
    assert!(require_count(Some(&json!(false)), "promised", 1).is_err());
}

/// A float is refused rather than truncated, and a numeric string is read —
/// Python's `int("12")` is 12.
#[test]
fn a_float_is_refused_but_a_numeric_string_is_read() {
    assert!(require_count(Some(&json!(3.5)), "promised", 1).is_err());
    assert_eq!(require_count(Some(&json!("12")), "promised", 1), Ok(12));
    assert_eq!(require_count(Some(&json!("0")), "record_count", 0), Ok(0));
}

/// An unreadable count reports the value, not a `TypeError` a caller catching
/// `ValueError` would miss (#99).
#[test]
fn an_unreadable_count_reports_the_value() {
    let err = require_count(Some(&json!("abc")), "promised", 1).expect_err("refused");
    assert_eq!(err.to_string(), "promised is not a readable integer: 'abc'");
}

/// The floor is the field's own: a planned part promises at least one record,
/// a stored part may have zero.
#[test]
fn the_floor_is_per_field() {
    assert!(require_count(Some(&json!(0)), "promised", 1).is_err());
    assert_eq!(require_count(Some(&json!(0)), "record_count", 0), Ok(0));
    let err = require_count(Some(&json!(-1)), "record_count", 0).expect_err("refused");
    assert_eq!(err.to_string(), "record_count must be at least 0, got -1");
}

// ---------------------------------------------------------------------------
// `require_datetime` — inventing *now* is the most durable lie
// ---------------------------------------------------------------------------

/// Substituting *now* for a missing `downloaded_at` is **the single most
/// durable-looking value** the day-selection rule can be handed: the day looks
/// fetched and is never fetched again (#98). The validator refuses instead.
#[test]
fn a_missing_downloaded_at_is_refused_not_stamped() {
    for absent in [None, Some(json!(null))] {
        let err = require_datetime(absent.as_ref(), "downloaded_at").expect_err("refused");
        assert!(err.to_string().contains("NOT NULL in the schema"), "{err}");
    }
    // And it must not quietly succeed with a current timestamp.
    assert!(require_datetime(Some(&json!("")), "downloaded_at").is_err());
}

/// A `date` looks accepted — `isinstance(dt, date)` is true of a `datetime` —
/// and is not. `require_datetime`'s message is worded differently from its
/// siblings' for exactly this reason.
#[test]
fn a_non_timestamp_names_what_it_wanted() {
    let err = require_datetime(Some(&json!(5)), "downloaded_at").expect_err("refused");
    assert_eq!(
        err.to_string(),
        "downloaded_at must be an ISO 8601 string or a datetime, got int"
    );
}

/// The canonical form is `datetime.isoformat()`'s, which is what the oracle
/// compares. Three details a hand-written parser normally gets wrong, all
/// pinned: a bare date gains midnight, a naive time stays naive, and an
/// offset is echoed as written.
#[test]
fn the_iso_form_matches_pythons_fromisoformat() {
    assert_eq!(
        parse_iso8601("2024-01-02"),
        Some("2024-01-02T00:00:00".into())
    );
    assert_eq!(
        parse_iso8601("2024-01-02T03:04:05"),
        Some("2024-01-02T03:04:05".into()),
        "an absent offset is absent, not UTC"
    );
    assert_eq!(
        parse_iso8601("2024-01-02T03:04:05Z"),
        Some("2024-01-02T03:04:05+00:00".into())
    );
    assert_eq!(
        parse_iso8601("2024-01-02T03:04:05-05:00"),
        Some("2024-01-02T03:04:05-05:00".into())
    );
    assert_eq!(
        parse_iso8601("2024-01-02 03:04:05+00:00"),
        Some("2024-01-02T03:04:05+00:00".into()),
        "a space separator is accepted and normalised to T"
    );
    assert_eq!(
        parse_iso8601("2024-01-02T03:04:05.123456+00:00"),
        Some("2024-01-02T03:04:05.123456+00:00".into())
    );
}

/// The calendar check is real, not shape-only: February 29 is accepted in a
/// leap year and refused otherwise, and a 13th month is refused.
#[test]
fn an_impossible_date_is_refused() {
    assert!(parse_iso8601("2024-02-29T00:00:00+00:00").is_some());
    assert!(parse_iso8601("2023-02-29T00:00:00+00:00").is_none());
    assert!(parse_iso8601("2024-13-02T00:00:00+00:00").is_none());
    assert!(parse_iso8601("2024-01-32T00:00:00+00:00").is_none());
    assert!(parse_iso8601("2024-01-02T25:00:00+00:00").is_none());
    assert!(parse_iso8601("not-a-date").is_none());
    assert!(parse_iso8601("").is_none());
}

/// `now_utc` emits the same shape, so a freshly stamped row and a parsed one
/// are comparable.
#[test]
fn now_utc_is_in_the_same_form() {
    let now = now_utc();
    assert_eq!(now.len(), 25, "{now}");
    assert!(now.ends_with("+00:00"), "{now}");
    assert_eq!(parse_iso8601(&now), Some(now.clone()));
}

// ---------------------------------------------------------------------------
// DayStatus
// ---------------------------------------------------------------------------

/// Anything that is not `"completed"` needs a re-fetch, so an unrecognised
/// stored status silently changes which days are ever fetched again. The
/// reader refuses rather than passing it through.
#[test]
fn an_unrecognised_day_status_is_refused() {
    assert_eq!(DayStatus::from_str("completed"), Ok(DayStatus::Completed));
    assert_eq!(DayStatus::from_str("failed"), Ok(DayStatus::Failed));

    let err = DayStatus::from_str("done").expect_err("refused");
    assert!(err.contains("re-fetched"), "{err}");
    // Case matters: the column holds these two spellings exactly.
    assert!(DayStatus::from_str("Completed").is_err());
}

// ---------------------------------------------------------------------------
// PartCheckpoint
// ---------------------------------------------------------------------------

/// A checkpoint asserts a part finished, so it must describe one: no blank
/// names, at least one promised record, no negative stored count.
#[test]
fn a_checkpoint_must_describe_a_finished_part() {
    assert!(PartCheckpoint::new("s", "k", 1, 0).is_ok());
    assert!(PartCheckpoint::new("", "k", 1, 0).is_err());
    assert!(PartCheckpoint::new("s", "  ", 1, 0).is_err());
    assert!(PartCheckpoint::new("s", "k", 0, 0).is_err());
    assert!(PartCheckpoint::new("s", "k", 1, -1).is_err());
}

/// There is deliberately **no** `record_count <= promised` rule: the two count
/// different things — one the records the server delivered, the other what was
/// parsed and stored — and `reconcile_delivery` treats delivery at or above
/// the promise as clean.
#[test]
fn a_stored_count_may_exceed_the_promise() {
    let c = PartCheckpoint::new("s", "k", 1, 5).expect("allowed");
    assert_eq!(c.record_count, 5);
}

/// The serialised form is the four columns that describe the part, not the
/// whole row: `source` and `date` are the caller's context and `completed_at`
/// is stamped by the writer and never read back.
#[test]
fn a_checkpoint_does_not_round_trip_a_whole_row() {
    let c = PartCheckpoint::new("scheme", "key", 10, 9).expect("valid");
    let d = c.to_json();
    let obj = d.as_object().expect("object");
    assert_eq!(obj.len(), 4);
    for absent in ["source", "date", "completed_at", "id"] {
        assert!(!obj.contains_key(absent), "{absent} is not the part's own");
    }
}

// ---------------------------------------------------------------------------
// RetractionNature — two vocabularies
// ---------------------------------------------------------------------------

/// The export writes spaces and any case (`"Expression of concern"`, lower-case
/// `c`); the enum's own values use underscores. Conflating them maps every
/// expression of concern to `Other`.
#[test]
fn the_exports_vocabulary_is_not_the_enums() {
    assert_eq!(
        RetractionNature::from_raw(Some("Expression of concern")),
        RetractionNature::ExpressionOfConcern
    );
    assert_eq!(
        RetractionNature::from_raw(Some("  EXPRESSION OF CONCERN  ")),
        RetractionNature::ExpressionOfConcern
    );
    // The underscored spelling is *not* the file's wording, so it is unknown.
    assert_eq!(
        RetractionNature::from_raw(Some("expression_of_concern")),
        RetractionNature::Other
    );
    // ... but it is the enum's own, which `to_json` round-trips.
    assert_eq!(
        RetractionNature::from_str("expression_of_concern"),
        Ok(RetractionNature::ExpressionOfConcern)
    );
}

/// The vocabulary belongs to Retraction Watch, so an unknown value must cost
/// one row of fidelity rather than abort the import.
#[test]
fn an_unknown_nature_reads_as_other_rather_than_failing() {
    assert_eq!(
        RetractionNature::from_raw(Some("Novel kind")),
        RetractionNature::Other
    );
    assert_eq!(RetractionNature::from_raw(None), RetractionNature::Other);
    assert_eq!(
        RetractionNature::from_raw(Some("")),
        RetractionNature::Other
    );
}

/// `RetractionNotice` carries **two** papers, so the identifier pairs are named
/// for which is which — and the distinction survives a round trip.
#[test]
fn the_two_identifier_pairs_stay_distinct() {
    let r = RetractionNotice::from_json(&json!({
        "record_id": "R1",
        "nature": "retraction",
        "doi": "10.1/original",
        "pmid": "111",
        "notice_doi": "10.1/notice",
        "notice_pmid": "222",
    }))
    .expect("valid");

    assert_eq!(r.doi.as_deref(), Some("10.1/original"));
    assert_eq!(r.notice_doi.as_deref(), Some("10.1/notice"));
    assert_ne!(r.doi, r.notice_doi, "the two papers must not be conflated");

    let back = RetractionNotice::from_json(&r.to_json()).expect("round trip");
    assert_eq!(back, r);
}

// ---------------------------------------------------------------------------
// Publication
// ---------------------------------------------------------------------------

/// A new publication carries a source list with its first source in it, and is
/// stamped with a timestamp rather than an empty string.
#[test]
fn a_new_publication_records_its_first_source() {
    let p = Publication::new("T", "pubmed");
    assert_eq!(p.sources, vec!["pubmed".to_string()]);
    assert_eq!(p.first_seen_source, "pubmed");
    assert!(!p.created_at.is_empty());
    assert!(!p.updated_at.is_empty());
    assert_eq!(p.id, None);
}

/// `pmcid` was appended rather than placed beside `pmid` for positional
/// stability, and it defaults to `None` — a caller that omits it must not get
/// somebody else's field.
#[test]
fn pmcid_is_absent_by_default() {
    let p = Publication::new("T", "s");
    assert_eq!(p.pmcid, None);
    let back = Publication::from_json(&p.to_json()).expect("round trip");
    assert_eq!(back.pmcid, None);
}

/// A grant naming neither an agency nor an award id carries no information,
/// which is what `store_publication` uses this predicate for.
#[test]
fn a_grant_with_nothing_in_it_is_not_informative() {
    assert!(!Grant::default().is_informative());
    assert!(Grant {
        agency: Some("NIH".into()),
        ..Grant::default()
    }
    .is_informative());
    assert!(Grant {
        grant_id: Some("R01".into()),
        ..Grant::default()
    }
    .is_informative());
    // A country alone names no funder and no award.
    assert!(!Grant {
        country: Some("US".into()),
        ..Grant::default()
    }
    .is_informative());
}

/// A downloaded day's own constructor stamps the time, but reading a **stored**
/// row must not: that would invent the timestamp the durability rule reads.
#[test]
fn a_stored_day_requires_its_timestamp_but_a_new_one_is_stamped() {
    let fresh = DownloadDay::new("s", "2024-01-02", "completed", 10);
    assert!(!fresh.downloaded_at.is_empty());
    assert_eq!(fresh.last_verified_at, None);

    let stored = json!({
        "source": "s", "date": "2024-01-02", "status": "completed", "record_count": 10
    });
    assert!(DownloadDay::from_json(&stored).is_err());

    let with_stamp = json!({
        "source": "s", "date": "2024-01-02", "status": "completed", "record_count": 10,
        "downloaded_at": "2024-01-02T03:04:05+00:00"
    });
    assert!(DownloadDay::from_json(&with_stamp).is_ok());
}
