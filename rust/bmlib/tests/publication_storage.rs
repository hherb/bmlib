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
    store_publication, StoreOutcome,
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
