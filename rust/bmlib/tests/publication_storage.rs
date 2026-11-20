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

//! Publication storage — the store path, over a real database.
//!
//! `storage_rules` diffs the pure rules against Python. This file exercises
//! what only a database can show: that the merge preserves what it promises,
//! that per-source scoping really leaves other sources alone, and that a split
//! identity consolidates rather than stranding two rows.

use bmlib::db::{open_memory, Db};
use bmlib::publications::models::{AuthorAffiliation, FullTextSource, Grant, Publication};
use bmlib::publications::schema::ensure_schema;
use bmlib::publications::storage::{
    get_author_affiliations, get_grants, get_publication_by_doi, get_publication_by_pmid,
    store_publication, store_publication_with_record_id, ReplaceError, StoreOutcome,
};

fn db() -> Box<dyn Db> {
    let mut conn = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut conn).expect("schema");
    Box::new(conn)
}

fn pub_with(title: &str, source: &str) -> Publication {
    Publication::new(title, source)
}

fn count(db: &mut dyn Db, sql: &str) -> i64 {
    use bmlib::db::fetch_scalar;
    use bmlib::db::Value;
    match fetch_scalar(db, sql, &[]).expect("scalar") {
        Some(Value::Int(i)) => i,
        other => panic!("expected an integer count, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// Dedup
// ---------------------------------------------------------------------------

/// Two records for the same paper are one row, whatever the title says — the
/// identifier is what makes them the same work.
#[test]
fn the_same_doi_is_added_once_and_merged_after() {
    let mut db = db();

    let mut a = pub_with("First title", "pubmed");
    a.doi = Some("10.1000/XYZ".to_string());
    assert_eq!(
        store_publication(&mut *db, &mut a, &[], &[], &[]).expect("store"),
        StoreOutcome::Added
    );

    let mut b = pub_with("Second title", "openalex");
    b.doi = Some("10.1000/xyz".to_string());
    assert_eq!(
        store_publication(&mut *db, &mut b, &[], &[], &[]).expect("store"),
        StoreOutcome::Merged
    );

    assert_eq!(count(&mut *db, "SELECT COUNT(*) FROM publications"), 1);
}

/// Case and prefix differ between sources, and the canonical form is what
/// makes them agree. This is the cross-source case the normaliser exists for.
#[test]
fn case_and_prefix_variants_dedup_to_one_row() {
    let mut db = db();

    let mut a = pub_with("T", "pubmed");
    a.doi = Some("10.1000/XYZ".to_string());
    store_publication(&mut *db, &mut a, &[], &[], &[]).expect("first");

    for variant in [
        "https://doi.org/10.1000/XYZ",
        "doi:10.1000/xyz",
        "  10.1000/xYz  ",
    ] {
        let mut b = pub_with("T", "other");
        b.doi = Some(variant.to_string());
        assert_eq!(
            store_publication(&mut *db, &mut b, &[], &[], &[]).expect("store"),
            StoreOutcome::Merged,
            "{variant:?} must dedup"
        );
    }
    assert_eq!(count(&mut *db, "SELECT COUNT(*) FROM publications"), 1);
}

/// A PMID identifies the same way, and the two lookups are independent so a
/// record carrying only one of them still dedups.
#[test]
fn a_pmid_dedups_without_a_doi() {
    let mut db = db();
    let mut a = pub_with("T", "pubmed");
    a.pmid = Some("12345".to_string());
    store_publication(&mut *db, &mut a, &[], &[], &[]).expect("first");

    let mut b = pub_with("T", "other");
    b.pmid = Some("  12345  ".to_string());
    assert_eq!(
        store_publication(&mut *db, &mut b, &[], &[], &[]).expect("store"),
        StoreOutcome::Merged
    );
    assert_eq!(count(&mut *db, "SELECT COUNT(*) FROM publications"), 1);
}

/// `store_publication` mutates the publication in place to hold the canonical
/// identifiers, so a caller can use the same object afterwards.
#[test]
fn the_publication_is_canonicalised_in_place() {
    let mut db = db();
    let mut a = pub_with("T", "s");
    a.doi = Some("HTTPS://DOI.ORG/10.1000/XYZ".to_string());
    a.pmid = Some("  99  ".to_string());
    store_publication(&mut *db, &mut a, &[], &[], &[]).expect("store");

    assert_eq!(a.doi.as_deref(), Some("10.1000/xyz"));
    assert_eq!(a.pmid.as_deref(), Some("99"));
}

// ---------------------------------------------------------------------------
// Merge preserves
// ---------------------------------------------------------------------------

/// "Fill, never overwrite" is the rule the docstring leads with, and the title
/// is the field it is easiest to check. A merge that overwrote would make a
/// record's title depend on which source synced last.
#[test]
fn a_merge_never_overwrites_a_populated_field() {
    let mut db = db();
    let mut a = pub_with("Original", "s");
    a.doi = Some("10.1/x".to_string());
    a.abstract_text = Some("Original abstract".to_string());
    store_publication(&mut *db, &mut a, &[], &[], &[]).expect("first");

    let mut b = pub_with("Replacement", "other");
    b.doi = Some("10.1/x".to_string());
    b.abstract_text = Some("New abstract".to_string());
    store_publication(&mut *db, &mut b, &[], &[], &[]).expect("merge");

    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert_eq!(found.title, "Original", "the title must not be overwritten");
    assert_eq!(
        found.abstract_text.as_deref(),
        Some("Original abstract"),
        "the abstract must not be overwritten"
    );
}

/// A `NULL` field *is* filled from the incoming record — that is the other half
/// of the same rule, and without it a record first seen as a bare identifier
/// would stay bare for ever.
#[test]
fn a_merge_fills_a_field_that_was_empty() {
    let mut db = db();
    let mut a = pub_with("T", "s");
    a.doi = Some("10.1/x".to_string());
    store_publication(&mut *db, &mut a, &[], &[], &[]).expect("first");
    assert!(a.abstract_text.is_none());

    let mut b = pub_with("T", "other");
    b.doi = Some("10.1/x".to_string());
    b.abstract_text = Some("Supplied later".to_string());
    b.journal = Some("J".to_string());
    store_publication(&mut *db, &mut b, &[], &[], &[]).expect("merge");

    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert_eq!(found.abstract_text.as_deref(), Some("Supplied later"));
    assert_eq!(found.journal.as_deref(), Some("J"));
}

/// The three **list** columns are not covered by `COALESCE` — they are decided
/// by `merge_json_list` and written explicitly — so "fill, never overwrite"
/// needs its own end-to-end case. A port that overwrote them would pass every
/// scalar check above and still erase authors that an earlier source supplied.
#[test]
fn a_merge_fills_list_columns_and_keeps_them_once_populated() {
    let mut db = db();
    let mut bare = pub_with("T", "s");
    bare.doi = Some("10.1/x".to_string());
    store_publication(&mut *db, &mut bare, &[], &[], &[]).expect("first");

    // A later source supplies the authors, types and keywords.
    let mut rich = pub_with("T", "other");
    rich.doi = Some("10.1/x".to_string());
    rich.authors = vec!["Smith, J".to_string()];
    rich.publication_types = vec!["RCT".to_string()];
    rich.keywords = vec!["metformin".to_string()];
    store_publication(&mut *db, &mut rich, &[], &[], &[]).expect("fill");

    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert_eq!(found.authors, vec!["Smith, J".to_string()], "filled");
    assert_eq!(found.publication_types, vec!["RCT".to_string()], "filled");
    assert_eq!(found.keywords, vec!["metformin".to_string()], "filled");

    // A third source carrying *different* values must not overwrite them.
    let mut rival = pub_with("T", "third");
    rival.doi = Some("10.1/x".to_string());
    rival.authors = vec!["Jones, A".to_string()];
    rival.keywords = vec!["aspirin".to_string()];
    store_publication(&mut *db, &mut rival, &[], &[], &[]).expect("rival");

    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert_eq!(
        found.authors,
        vec!["Smith, J".to_string()],
        "a populated author list must not be overwritten"
    );
    assert_eq!(
        found.keywords,
        vec!["metformin".to_string()],
        "a populated keyword list must not be overwritten"
    );
}

/// The source list unions across merges, so provenance is additive and a paper
/// fetched from three sources names all three.
#[test]
fn sources_union_across_merges() {
    let mut db = db();
    for source in ["pubmed", "openalex", "biorxiv"] {
        let mut p = pub_with("T", source);
        p.doi = Some("10.1/x".to_string());
        store_publication(&mut *db, &mut p, &[], &[], &[]).expect("store");
    }
    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert_eq!(found.sources, vec!["pubmed", "openalex", "biorxiv"]);
    assert_eq!(
        found.first_seen_source, "pubmed",
        "the first source to see it stays the first"
    );
}

/// Open access is a **one-way latch**: once any source reports it, it stays.
/// A later source that cannot tell must not turn it off.
#[test]
fn open_access_latches_on_and_never_off() {
    let mut db = db();
    let mut a = pub_with("T", "s");
    a.doi = Some("10.1/x".to_string());
    store_publication(&mut *db, &mut a, &[], &[], &[]).expect("first");

    let mut open = pub_with("T", "other");
    open.doi = Some("10.1/x".to_string());
    open.is_open_access = true;
    store_publication(&mut *db, &mut open, &[], &[], &[]).expect("open");

    let mut closed = pub_with("T", "third");
    closed.doi = Some("10.1/x".to_string());
    closed.is_open_access = false;
    store_publication(&mut *db, &mut closed, &[], &[], &[]).expect("closed");

    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert!(found.is_open_access, "the latch must not be released");
}

// ---------------------------------------------------------------------------
// Split identity
// ---------------------------------------------------------------------------

/// When a DOI and a PMID point at two different rows, the incoming record
/// consolidates them rather than failing on the `UNIQUE` constraint and leaving
/// the duplicates stranded for ever.
#[test]
fn a_split_identity_consolidates_into_one_row() {
    let mut db = db();

    let mut by_doi = pub_with("DOI row", "pubmed");
    by_doi.doi = Some("10.1/x".to_string());
    by_doi.abstract_text = Some("from doi".to_string());
    store_publication(&mut *db, &mut by_doi, &[], &[], &[]).expect("doi row");

    let mut by_pmid = pub_with("PMID row", "openalex");
    by_pmid.pmid = Some("777".to_string());
    by_pmid.journal = Some("J".to_string());
    store_publication(&mut *db, &mut by_pmid, &[], &[], &[]).expect("pmid row");
    assert_eq!(count(&mut *db, "SELECT COUNT(*) FROM publications"), 2);

    // Now a record carrying both, which is what reveals the split.
    let mut both = pub_with("Both", "third");
    both.doi = Some("10.1/x".to_string());
    both.pmid = Some("777".to_string());
    assert_eq!(
        store_publication(&mut *db, &mut both, &[], &[], &[]).expect("consolidate"),
        StoreOutcome::Merged
    );

    assert_eq!(
        count(&mut *db, "SELECT COUNT(*) FROM publications"),
        1,
        "the two rows must become one"
    );
    let found = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found");
    assert_eq!(found.title, "DOI row", "the DOI row is kept");
    assert_eq!(
        found.journal.as_deref(),
        Some("J"),
        "the drop row's data is folded in"
    );
    assert_eq!(found.abstract_text.as_deref(), Some("from doi"));
    assert_eq!(found.pmid.as_deref(), Some("777"));
}

// ---------------------------------------------------------------------------
// Child rows
// ---------------------------------------------------------------------------

/// Supplying grants **replaces** the stored rows for each source those rows
/// name, so a corrected grant supersedes the stale one instead of accumulating
/// beside it.
#[test]
fn grants_replace_within_their_own_source() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());

    let first = Grant {
        source: "pubmed".to_string(),
        agency: Some("NIH".to_string()),
        grant_id: Some("R01".to_string()),
        ..Grant::default()
    };
    store_publication(&mut *db, &mut p, &[], &[first], &[]).expect("first");

    let pub_id = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found")
        .id
        .expect("id");
    assert_eq!(get_grants(&mut *db, pub_id).expect("grants").len(), 1);

    // A corrected award replaces the first rather than adding beside it.
    let corrected = Grant {
        source: "pubmed".to_string(),
        agency: Some("NIH".to_string()),
        grant_id: Some("R02".to_string()),
        ..Grant::default()
    };
    store_publication(&mut *db, &mut p, &[], &[corrected], &[]).expect("corrected");

    let grants = get_grants(&mut *db, pub_id).expect("grants");
    assert_eq!(grants.len(), 1, "the stale row must be replaced");
    assert_eq!(grants[0].grant_id.as_deref(), Some("R02"));
}

/// Two sources' funding data coexist. This is the defect the per-source scoping
/// exists for: scoping by publication alone made the stored set depend on
/// whichever source synced last.
#[test]
fn one_sources_grants_do_not_replace_anothers() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());
    store_publication(
        &mut *db,
        &mut p,
        &[],
        &[Grant {
            source: "pubmed".to_string(),
            agency: Some("NIH".to_string()),
            ..Grant::default()
        }],
        &[],
    )
    .expect("pubmed grants");

    let pub_id = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found")
        .id
        .expect("id");

    // A *second* source's grants, stored for the same publication.
    let mut p2 = pub_with("T", "openalex");
    p2.doi = Some("10.1/x".to_string());
    store_publication(
        &mut *db,
        &mut p2,
        &[],
        &[Grant {
            source: "openalex".to_string(),
            agency: Some("Wellcome".to_string()),
            ..Grant::default()
        }],
        &[],
    )
    .expect("openalex grants");

    let grants = get_grants(&mut *db, pub_id).expect("grants");
    assert_eq!(grants.len(), 2, "both sources' grants must survive");
    let agencies: Vec<&str> = grants.iter().filter_map(|g| g.agency.as_deref()).collect();
    assert!(agencies.contains(&"NIH"));
    assert!(agencies.contains(&"Wellcome"));
}

/// Supplying **no** grants leaves the stored ones untouched — an absent
/// `<GrantList>` means the record did not carry the data, not that the funding
/// was withdrawn.
#[test]
fn an_absent_grant_supply_leaves_stored_grants_alone() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());
    store_publication(
        &mut *db,
        &mut p,
        &[],
        &[Grant {
            source: "pubmed".to_string(),
            agency: Some("NIH".to_string()),
            ..Grant::default()
        }],
        &[],
    )
    .expect("with grants");

    let pub_id = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found")
        .id
        .expect("id");

    // Re-store with no grants at all.
    let mut again = pub_with("T", "s");
    again.doi = Some("10.1/x".to_string());
    store_publication(&mut *db, &mut again, &[], &[], &[]).expect("without grants");

    assert_eq!(
        get_grants(&mut *db, pub_id).expect("grants").len(),
        1,
        "no grants supplied means no change"
    );
}

/// An unnamed grant is refused **before** anything is written, and the whole
/// store rolls back — a partial write would leave a publication with no grants
/// and no error to explain it.
#[test]
fn an_unnamed_grant_is_refused_and_rolls_the_store_back() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());

    let err = store_publication(
        &mut *db,
        &mut p,
        &[],
        &[Grant {
            source: String::new(),
            agency: Some("NIH".to_string()),
            ..Grant::default()
        }],
        &[],
    )
    .expect_err("refused");
    assert!(err.to_string().contains("must name the source"), "{err}");

    assert_eq!(
        count(&mut *db, "SELECT COUNT(*) FROM publications"),
        0,
        "the transaction must have rolled back"
    );
}

/// Affiliations are ordered by author position, which is what makes the first
/// and senior authors findable at the ends.
#[test]
fn affiliations_come_back_in_author_order() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());

    let rows: Vec<AuthorAffiliation> = [("Third, C", 2), ("First, A", 0), ("Second, B", 1)]
        .iter()
        .map(|(author, position)| AuthorAffiliation {
            source: "pubmed".to_string(),
            author: (*author).to_string(),
            affiliation: "Uni".to_string(),
            position: *position,
            ..AuthorAffiliation::default()
        })
        .collect();

    store_publication(&mut *db, &mut p, &[], &[], &rows).expect("store");
    let pub_id = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found")
        .id
        .expect("id");

    let stored = get_author_affiliations(&mut *db, pub_id).expect("affiliations");
    let authors: Vec<&str> = stored.iter().map(|a| a.author.as_str()).collect();
    assert_eq!(authors, vec!["First, A", "Second, B", "Third, C"]);
}

// ---------------------------------------------------------------------------
// Full-text sources
// ---------------------------------------------------------------------------

/// Full-text locations **accumulate** across sources, and a repeat of the same
/// `(publication_id, url)` pair is a no-op rather than an error — which is what
/// makes re-storing idempotent.
#[test]
fn fulltext_sources_accumulate_and_dedupe_on_url() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());

    let sources = vec![
        FullTextSource::new(0, "europepmc", "http://a", "xml"),
        FullTextSource::new(0, "unpaywall", "http://b", "pdf"),
    ];
    store_publication(&mut *db, &mut p, &sources, &[], &[]).expect("store");

    let pub_id = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found")
        .id
        .expect("id");
    assert_eq!(
        count(
            &mut *db,
            &format!("SELECT COUNT(*) FROM fulltext_sources WHERE publication_id = {pub_id}")
        ),
        2
    );

    // Re-store the same two, plus one new: the duplicates are no-ops.
    let mut again = pub_with("T", "s");
    again.doi = Some("10.1/x".to_string());
    let more = vec![
        FullTextSource::new(0, "europepmc", "http://a", "xml"),
        FullTextSource::new(0, "doi", "http://c", "html"),
    ];
    store_publication(&mut *db, &mut again, &more, &[], &[]).expect("re-store");

    assert_eq!(
        count(
            &mut *db,
            &format!("SELECT COUNT(*) FROM fulltext_sources WHERE publication_id = {pub_id}")
        ),
        3,
        "the duplicate URL is not inserted twice"
    );
}

/// `/pubs` stores bioRxiv's v1 PDF with no version; `/details` later sends the
/// same URL as version "1" (#341), and the row takes it. Another source's row,
/// or one already labelled, is left as it stands, and none of it is an insert.
#[test]
fn a_version_labels_the_same_sources_unversioned_row_only() {
    use bmlib::db::fetch_all;
    use bmlib::publications::storage::add_fulltext_source;

    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1/x".to_string());
    store_publication(&mut *db, &mut p, &[], &[], &[]).expect("store");
    let id = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("lookup")
        .expect("found")
        .id
        .expect("id");
    let add = |db: &mut dyn Db, source: &str, url: &str, version: Option<&str>| {
        add_fulltext_source(db, id, source, url, "pdf", version).expect("add")
    };
    assert!(add(&mut *db, "medrxiv", "http://x/v1.pdf", None));
    assert!(add(&mut *db, "openalex", "http://x/oa", None));
    assert!(add(&mut *db, "medrxiv", "http://x/v2.pdf", Some("2")));

    for (url, version) in [
        ("v1.pdf", Some("1")),
        ("oa", Some("1")),
        ("v2.pdf", Some("3")),
        ("v1.pdf", None),
    ] {
        assert!(!add(
            &mut *db,
            "medrxiv",
            &format!("http://x/{url}"),
            version
        ));
    }

    let rows: Vec<(String, String, Option<String>)> = fetch_all(
        &mut *db,
        "SELECT source, url, version FROM fulltext_sources ORDER BY url",
        &[],
    )
    .expect("rows")
    .iter()
    .map(|row| {
        (
            row.get_str("source").expect("source").to_string(),
            row.get_str("url").expect("url").to_string(),
            row.get("version")
                .expect("version")
                .as_str()
                .map(str::to_string),
        )
    })
    .collect();
    assert_eq!(
        rows,
        [
            ("openalex", "http://x/oa", None),
            ("medrxiv", "http://x/v1.pdf", Some("1")),
            ("medrxiv", "http://x/v2.pdf", Some("2")),
        ]
        .map(|(s, u, v)| (s.to_string(), u.to_string(), v.map(str::to_string)))
    );
}

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

/// The lookups normalise their argument, so a caller querying with any case or
/// prefix variant finds the canonical stored row.
#[test]
fn lookups_normalise_their_argument() {
    let mut db = db();
    let mut p = pub_with("T", "s");
    p.doi = Some("10.1000/XYZ".to_string());
    p.pmid = Some("42".to_string());
    store_publication(&mut *db, &mut p, &[], &[], &[]).expect("store");

    for variant in [
        "10.1000/xyz",
        "https://doi.org/10.1000/XYZ",
        "doi:10.1000/xyz",
    ] {
        assert!(
            get_publication_by_doi(&mut *db, variant)
                .expect("lookup")
                .is_some(),
            "{variant:?} must find the row"
        );
    }
    assert!(get_publication_by_pmid(&mut *db, "  42  ")
        .expect("lookup")
        .is_some());
    assert!(get_publication_by_doi(&mut *db, "10.9999/nope")
        .expect("lookup")
        .is_none());
}

// ---------------------------------------------------------------------------
// A record with neither DOI nor PMID (#794)
// ---------------------------------------------------------------------------

fn keyed(
    db: &mut dyn Db,
    doi: Option<&str>,
    pmid: Option<&str>,
    title: &str,
    record_id: Option<(&str, &str)>,
) -> StoreOutcome {
    let mut p = pub_with(title, "openalex");
    p.doi = doi.map(str::to_string);
    p.pmid = pmid.map(str::to_string);
    store_publication_with_record_id(db, &mut p, &[], &[], &[], record_id).expect("store")
}

fn rows(db: &mut dyn Db) -> (i64, i64) {
    (
        count(db, "SELECT COUNT(*) FROM publications"),
        count(db, "SELECT COUNT(*) FROM publication_record_ids"),
    )
}

#[test]
fn a_keyless_record_is_stored_once_by_its_record_id() {
    let mut db = db();
    let id = Some(("europepmc", "PPR:PPR123"));
    assert_eq!(keyed(&mut *db, None, None, "P", id), StoreOutcome::Added);
    assert_eq!(keyed(&mut *db, None, None, "P", id), StoreOutcome::Merged);
    assert_eq!(keyed(&mut *db, None, None, "P", id), StoreOutcome::Merged);
    assert_eq!(rows(&mut *db), (1, 1));
}

#[test]
fn one_id_in_two_sources_names_two_records() {
    let mut db = db();
    keyed(&mut *db, None, None, "P", Some(("openalex", "W1")));
    keyed(&mut *db, None, None, "P", Some(("europepmc", "W1")));
    assert_eq!(rows(&mut *db), (2, 2));
}

#[test]
fn a_keyless_row_joins_the_doi_row_once_the_record_gains_a_doi() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), None, "By DOI", None);
    keyed(&mut *db, None, None, "Keyless", Some(("openalex", "W1")));
    assert_eq!(rows(&mut *db).0, 2);

    keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "P",
        Some(("openalex", "W1")),
    );

    assert_eq!(rows(&mut *db), (1, 1));
    let kept = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("read")
        .expect("row");
    assert_eq!(kept.title, "By DOI");
}

#[test]
fn three_rows_one_work_become_one() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), None, "By DOI", None);
    keyed(&mut *db, None, Some("9"), "By PMID", None);
    keyed(&mut *db, None, None, "Keyless", Some(("openalex", "W1")));
    assert_eq!(rows(&mut *db).0, 3);

    keyed(
        &mut *db,
        Some("10.1/x"),
        Some("9"),
        "P",
        Some(("openalex", "W1")),
    );

    assert_eq!(rows(&mut *db), (1, 1));
    let kept = get_publication_by_doi(&mut *db, "10.1/x")
        .expect("read")
        .expect("row");
    assert_eq!(
        (kept.title.as_str(), kept.pmid.as_deref()),
        ("By DOI", Some("9"))
    );
}

/// The DOI and the record ID find the same row: it must not be consolidated
/// into itself, which would delete it.
#[test]
fn a_re_fetch_of_a_keyed_record_keeps_its_row_and_children() {
    let mut db = db();
    let mut p = pub_with("P", "openalex");
    p.doi = Some("10.1/x".to_string());
    let fts = [FullTextSource::new(0, "oa", "http://x/1", "pdf")];
    store_publication_with_record_id(&mut *db, &mut p, &fts, &[], &[], Some(("openalex", "W1")))
        .expect("store");

    let outcome = keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "P",
        Some(("openalex", "W1")),
    );

    assert_eq!(outcome, StoreOutcome::Merged);
    assert_eq!(rows(&mut *db), (1, 1));
    assert_eq!(count(&mut *db, "SELECT COUNT(*) FROM fulltext_sources"), 1);
}

#[test]
fn a_row_keeps_every_id_it_was_given() {
    let mut db = db();
    keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "P",
        Some(("openalex", "W1")),
    );
    keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "P",
        Some(("europepmc", "PPR:PPR1")),
    );

    keyed(&mut *db, None, None, "Again", Some(("openalex", "W1")));
    keyed(
        &mut *db,
        None,
        None,
        "Again",
        Some(("europepmc", "PPR:PPR1")),
    );

    assert_eq!(rows(&mut *db), (1, 2));
}

#[test]
fn an_empty_record_id_is_refused_before_anything_is_written() {
    let mut db = db();
    for record_id in [("", "W1"), ("openalex", "")] {
        let mut p = pub_with("P", "openalex");
        let err =
            store_publication_with_record_id(&mut *db, &mut p, &[], &[], &[], Some(record_id))
                .expect_err("refused");
        assert!(matches!(err, ReplaceError::EmptyRecordId));
    }
    assert_eq!(rows(&mut *db), (0, 0));
}

fn papers(db: &mut dyn Db) -> Vec<(String, Option<String>, Option<String>)> {
    let text = |row: &bmlib::db::Row, key: &str| {
        row.get(key).expect("column").as_str().map(str::to_string)
    };
    bmlib::db::fetch_all(
        db,
        "SELECT title, doi, pmid FROM publications ORDER BY id",
        &[],
    )
    .expect("rows")
    .iter()
    .map(|row| {
        (
            text(row, "title").expect("title"),
            text(row, "doi"),
            text(row, "pmid"),
        )
    })
    .collect()
}

fn owner(db: &mut dyn Db, record_id: &str) -> i64 {
    count(
        db,
        &format!(
            "SELECT publication_id FROM publication_record_ids WHERE record_id = '{record_id}'"
        ),
    )
}

fn paper(
    title: &str,
    doi: Option<&str>,
    pmid: Option<&str>,
) -> (String, Option<String>, Option<String>) {
    (
        title.to_string(),
        doi.map(str::to_string),
        pmid.map(str::to_string),
    )
}

#[test]
fn the_pmid_row_is_kept_before_the_record_id_row() {
    let mut db = db();
    keyed(&mut *db, None, Some("9"), "By PMID", None);
    keyed(&mut *db, None, None, "Keyless", Some(("openalex", "W1")));

    keyed(&mut *db, None, Some("9"), "P", Some(("openalex", "W1")));

    assert_eq!(papers(&mut *db), [paper("By PMID", None, Some("9"))]);
}

/// The source moves its ID to a work whose DOI already has its own row
/// (OpenAlex merging a preprint into its journal version): the row the ID
/// first named keeps its DOI, and the ID follows the record's DOI.
#[test]
fn an_id_now_naming_another_doi_never_merges_two_papers() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), None, "Preprint", None);
    keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "P",
        Some(("openalex", "W1")),
    );
    keyed(&mut *db, Some("10.1/y"), None, "Journal", None);

    let outcome = keyed(
        &mut *db,
        Some("10.1/y"),
        None,
        "P",
        Some(("openalex", "W1")),
    );

    assert_eq!(outcome, StoreOutcome::Merged);
    assert_eq!(
        papers(&mut *db),
        [
            paper("Preprint", Some("10.1/x"), None),
            paper("Journal", Some("10.1/y"), None)
        ]
    );
    assert_eq!(owner(&mut *db, "W1"), 2);
}

#[test]
fn an_id_now_naming_a_new_doi_adds_the_paper() {
    let mut db = db();
    keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "Preprint",
        Some(("openalex", "W1")),
    );

    let outcome = keyed(
        &mut *db,
        Some("10.1/y"),
        None,
        "Journal",
        Some(("openalex", "W1")),
    );

    assert_eq!(outcome, StoreOutcome::Added);
    assert_eq!(
        papers(&mut *db),
        [
            paper("Preprint", Some("10.1/x"), None),
            paper("Journal", Some("10.1/y"), None)
        ]
    );
    assert_eq!(owner(&mut *db, "W1"), 2);
}

#[test]
fn an_id_now_naming_another_pmid_never_merges_two_papers() {
    let mut db = db();
    keyed(&mut *db, None, Some("1"), "First", Some(("openalex", "W1")));
    keyed(&mut *db, None, Some("2"), "Second", None);

    keyed(&mut *db, None, Some("2"), "P", Some(("openalex", "W1")));

    assert_eq!(
        papers(&mut *db),
        [
            paper("First", None, Some("1")),
            paper("Second", None, Some("2"))
        ]
    );
    assert_eq!(owner(&mut *db, "W1"), 2);
}

/// The record names no DOI, but the PMID row it joins has one, and the ID's
/// row has another: folding them would lose one DOI.
#[test]
fn an_id_row_conflicting_with_the_pmid_rows_doi_is_kept() {
    let mut db = db();
    keyed(
        &mut *db,
        Some("10.1/x"),
        None,
        "ID row",
        Some(("openalex", "W1")),
    );
    keyed(&mut *db, Some("10.1/y"), Some("2"), "PMID row", None);

    keyed(&mut *db, None, Some("2"), "P", Some(("openalex", "W1")));

    assert_eq!(
        papers(&mut *db),
        [
            paper("ID row", Some("10.1/x"), None),
            paper("PMID row", Some("10.1/y"), Some("2"))
        ]
    );
    assert_eq!(owner(&mut *db, "W1"), 2);
}

#[test]
fn an_id_row_with_the_same_or_no_doi_still_joins() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), None, "By DOI", None);
    keyed(
        &mut *db,
        None,
        Some("9"),
        "ID row",
        Some(("openalex", "W1")),
    );

    keyed(
        &mut *db,
        Some("10.1/x"),
        Some("9"),
        "P",
        Some(("openalex", "W1")),
    );

    assert_eq!(
        papers(&mut *db),
        [paper("By DOI", Some("10.1/x"), Some("9"))]
    );
}

/// `sync` keys a stored record by its source's ID, so a keyless record
/// fetched three times is one row (Python's
/// `test_a_keyless_record_is_found_again_by_its_record_id`).
#[test]
fn sync_stores_a_keyless_record_once_by_its_record_id() {
    use bmlib::publications::models::FetchedRecord;
    use bmlib::publications::sync::store_records;
    let mut db = db();
    let mut record = FetchedRecord::new("P", "europepmc");
    record.source_record_id = Some("PPR:PPR1".to_string());

    let counts = store_records(
        &mut *db,
        "europepmc",
        &[record.clone(), record.clone(), record],
    )
    .expect("store");

    assert_eq!(counts, (1, 2, 0));
    assert_eq!(rows(&mut *db), (1, 1));
}

/// An empty ID fails the record, as in Python: stored unkeyed it would
/// duplicate on every re-fetch (#794).
#[test]
fn sync_fails_a_record_with_an_empty_record_id() {
    use bmlib::publications::models::FetchedRecord;
    use bmlib::publications::sync::store_records;
    let mut db = db();
    let mut record = FetchedRecord::new("P", "europepmc");
    record.source_record_id = Some(String::new());

    let counts = store_records(&mut *db, "europepmc", &[record]).expect("store");

    assert_eq!(counts, (0, 0, 1));
    assert_eq!(rows(&mut *db), (0, 0));
}

// A record whose DOI and PMID find two rows that each hold a different value
// of the other identifier names two papers (#803): neither row is folded into
// the other, and the record merges into the DOI's row without its PMID.

#[test]
fn a_doi_and_a_pmid_naming_two_keyed_papers_never_merge_them() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), Some("5"), "Paper A", None);
    keyed(&mut *db, Some("10.1/y"), Some("9"), "Paper B", None);
    let mut bad = pub_with("Bad link", "openalex");
    bad.doi = Some("10.1/x".into());
    bad.pmid = Some("9".into());
    bad.journal = Some("Cell".into());

    let outcome = store_publication(&mut *db, &mut bad, &[], &[], &[]).expect("store");

    assert_eq!(outcome, StoreOutcome::Merged);
    assert_eq!(
        papers(&mut *db),
        [
            paper("Paper A", Some("10.1/x"), Some("5")),
            paper("Paper B", Some("10.1/y"), Some("9"))
        ]
    );
    assert_eq!(
        count(
            &mut *db,
            "SELECT COUNT(*) FROM publications WHERE journal = 'Cell' AND doi = '10.1/x'"
        ),
        1
    );
    assert_eq!(bad.pmid.as_deref(), Some("9"));
}

/// The plain merge would write PMID 9 onto the DOI row, which the PMID row
/// already holds.
#[test]
fn a_doi_row_without_a_pmid_does_not_take_the_other_papers() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), None, "Paper A", None);
    keyed(&mut *db, Some("10.1/y"), Some("9"), "Paper B", None);

    keyed(&mut *db, Some("10.1/x"), Some("9"), "P", None);

    assert_eq!(
        papers(&mut *db),
        [
            paper("Paper A", Some("10.1/x"), None),
            paper("Paper B", Some("10.1/y"), Some("9"))
        ]
    );
}

#[test]
fn a_pmid_row_without_a_doi_is_kept_when_the_doi_row_has_a_pmid() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), Some("5"), "Paper A", None);
    keyed(&mut *db, None, Some("9"), "Paper B", None);

    keyed(&mut *db, Some("10.1/x"), Some("9"), "P", None);

    assert_eq!(
        papers(&mut *db),
        [
            paper("Paper A", Some("10.1/x"), Some("5")),
            paper("Paper B", None, Some("9"))
        ]
    );
}

#[test]
fn the_record_id_follows_the_record_to_the_doi_row() {
    let mut db = db();
    keyed(&mut *db, Some("10.1/x"), Some("5"), "Paper A", None);
    keyed(
        &mut *db,
        Some("10.1/y"),
        Some("9"),
        "Paper B",
        Some(("openalex", "W1")),
    );

    keyed(
        &mut *db,
        Some("10.1/x"),
        Some("9"),
        "P",
        Some(("openalex", "W1")),
    );

    assert_eq!(papers(&mut *db).len(), 2);
    assert_eq!(owner(&mut *db, "W1"), 1);
}
