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

//! Ported from `tests/test_backends.py::TestStorage` and
//! `::TestGrantAndAffiliationStorage`.
//!
//! Each test names its Python source. SQLite only — see FINDINGS.md.

use bmlib_db::operations::{create_tables, fetch_scalar};
use bmlib_db::{open_memory, transaction, Db, Value};
use bmlib_publications::schema::SCHEMA_SQLITE;
use bmlib_publications::storage::{
    add_fulltext_source, get_publication_by_doi, get_publication_by_pmid,
};
use bmlib_publications::{
    get_author_affiliations, get_grants, store_publication, AuthorAffiliation, Grant, Publication,
    PublicationError, Stored,
};

/// The Rust stand-in for `_pub(**kwargs)`: build with defaults, override the
/// fields the test cares about via struct-update syntax.
fn a_paper() -> Publication {
    Publication {
        title: "A paper".into(),
        sources: vec!["pubmed".into()],
        first_seen_source: "pubmed".into(),
        ..Default::default()
    }
}

fn with_schema() -> rusqlite::Connection {
    let mut conn = open_memory().unwrap();
    create_tables(&mut conn, SCHEMA_SQLITE).unwrap();
    conn
}

fn store(db: &mut dyn Db, mut p: Publication) -> Stored {
    store_publication(db, &mut p, &[], &[], &[]).unwrap()
}

fn store_with(
    db: &mut dyn Db,
    mut p: Publication,
    grants: &[Grant],
    affiliations: &[AuthorAffiliation],
) -> Stored {
    store_publication(db, &mut p, &[], grants, affiliations).unwrap()
}

fn count(db: &mut dyn Db, table: &str) -> i64 {
    fetch_scalar(db, &format!("SELECT COUNT(*) FROM {table}"), &[])
        .unwrap()
        .and_then(|v| v.as_i64())
        .unwrap()
}

fn grant(agency: &str, source: &str) -> Grant {
    Grant {
        agency: Some(agency.into()),
        source: source.into(),
        ..Default::default()
    }
}

// --- TestStorage ----------------------------------------------------------

#[test]
fn insert_then_lookup() {
    let mut db = with_schema();
    let outcome = store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            pmid: Some("111".into()),
            pmcid: Some("PMC9".into()),
            r#abstract: Some("Text.".into()),
            ..a_paper()
        },
    );
    assert_eq!(outcome, Stored::Added);

    let p = get_publication_by_doi(&mut db, "10.1234/abc")
        .unwrap()
        .unwrap();
    assert_eq!(p.pmid.as_deref(), Some("111"));
    assert_eq!(p.pmcid.as_deref(), Some("PMC9"));
    assert_eq!(p.r#abstract.as_deref(), Some("Text."));
    assert!(p.id.is_some());
}

#[test]
fn doi_is_normalised_on_store_and_lookup() {
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("https://doi.org/10.1234/AbC".into()),
            ..a_paper()
        },
    );
    assert!(get_publication_by_doi(&mut db, "10.1234/abc")
        .unwrap()
        .is_some());
    assert_eq!(count(&mut db, "publications"), 1);
}

#[test]
fn second_source_merges_rather_than_duplicates() {
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            ..a_paper()
        },
    );

    let outcome = store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            sources: vec!["openalex".into()],
            first_seen_source: "openalex".into(),
            ..a_paper()
        },
    );

    assert_eq!(outcome, Stored::Merged);
    assert_eq!(count(&mut db, "publications"), 1);
    let p = get_publication_by_doi(&mut db, "10.1234/abc")
        .unwrap()
        .unwrap();
    assert_eq!(p.sources, ["pubmed", "openalex"]);
}

#[test]
fn merge_fills_nulls_without_overwriting() {
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            title: "First".into(),
            ..a_paper()
        },
    );
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            title: "Second".into(),
            r#abstract: Some("Filled in".into()),
            journal: Some("Nature".into()),
            ..a_paper()
        },
    );

    let p = get_publication_by_doi(&mut db, "10.1234/abc")
        .unwrap()
        .unwrap();
    assert_eq!(p.title, "First");
    assert_eq!(p.r#abstract.as_deref(), Some("Filled in"));
    assert_eq!(p.journal.as_deref(), Some("Nature"));
}

#[test]
fn open_access_latches_on() {
    // Once any source reports open access, a later record cannot unset it.
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            is_open_access: true,
            ..a_paper()
        },
    );
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            is_open_access: false,
            ..a_paper()
        },
    );
    assert!(
        get_publication_by_doi(&mut db, "10.1234/abc")
            .unwrap()
            .unwrap()
            .is_open_access
    );
}

#[test]
fn open_access_can_be_set_by_a_later_record() {
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            is_open_access: false,
            ..a_paper()
        },
    );
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            is_open_access: true,
            ..a_paper()
        },
    );
    assert!(
        get_publication_by_doi(&mut db, "10.1234/abc")
            .unwrap()
            .unwrap()
            .is_open_access
    );
}

#[test]
fn split_identity_is_consolidated() {
    // A DOI row and a PMID row for one work collapse when a record links them.
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            title: "By DOI".into(),
            ..a_paper()
        },
    );
    store(
        &mut db,
        Publication {
            pmid: Some("999".into()),
            title: "By PMID".into(),
            journal: Some("Cell".into()),
            ..a_paper()
        },
    );
    assert_eq!(count(&mut db, "publications"), 2);

    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            pmid: Some("999".into()),
            ..a_paper()
        },
    );

    assert_eq!(count(&mut db, "publications"), 1);
    let p = get_publication_by_doi(&mut db, "10.1234/abc")
        .unwrap()
        .unwrap();
    assert_eq!(p.pmid.as_deref(), Some("999"));
    assert_eq!(p.title, "By DOI");
    assert_eq!(p.journal.as_deref(), Some("Cell"));
    assert_eq!(
        get_publication_by_pmid(&mut db, "999").unwrap().unwrap().id,
        p.id
    );
}

#[test]
fn consolidation_moves_fulltext_sources_and_drops_duplicates() {
    let mut db = with_schema();
    let mut keep = Publication {
        doi: Some("10.1234/abc".into()),
        ..a_paper()
    };
    store_publication(
        &mut db,
        &mut keep,
        &[("epmc".into(), "https://a".into())],
        &[],
        &[],
    )
    .unwrap();
    let mut drop = Publication {
        pmid: Some("999".into()),
        ..a_paper()
    };
    store_publication(
        &mut db,
        &mut drop,
        &[
            ("epmc".into(), "https://a".into()),
            ("epmc".into(), "https://b".into()),
        ],
        &[],
        &[],
    )
    .unwrap();

    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            pmid: Some("999".into()),
            ..a_paper()
        },
    );

    assert_eq!(count(&mut db, "publications"), 1);
    // "https://a" was already on the keep row, so only "https://b" moves.
    assert_eq!(count(&mut db, "fulltext_sources"), 2);
}

#[test]
fn add_fulltext_source_reports_whether_it_inserted() {
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1234/abc".into()),
            ..a_paper()
        },
    );
    let id = get_publication_by_doi(&mut db, "10.1234/abc")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    assert!(add_fulltext_source(&mut db, id, "epmc", "https://a", "now").unwrap());
    assert!(!add_fulltext_source(&mut db, id, "epmc", "https://a", "now").unwrap());
}

// --- TestGrantAndAffiliationStorage ---------------------------------------

#[test]
fn grants_and_affiliations_round_trip() {
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[Grant {
            agency: Some("NHLBI".into()),
            grant_id: Some("R01".into()),
            country: Some("United States".into()),
            source: "pubmed".into(),
            ..Default::default()
        }],
        &[AuthorAffiliation {
            author: "Smith, J".into(),
            affiliation: "St Elsewhere".into(),
            position: 0,
            source: "pubmed".into(),
            ..Default::default()
        }],
    );

    let id = get_publication_by_pmid(&mut db, "1")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    let grants = get_grants(&mut db, id).unwrap();
    assert_eq!(
        grants
            .iter()
            .map(|g| (
                g.agency.as_deref(),
                g.grant_id.as_deref(),
                g.country.as_deref()
            ))
            .collect::<Vec<_>>(),
        [(Some("NHLBI"), Some("R01"), Some("United States"))]
    );
    let affiliations = get_author_affiliations(&mut db, id).unwrap();
    assert_eq!(
        affiliations
            .iter()
            .map(|a| (a.author.as_str(), a.affiliation.as_str(), a.position))
            .collect::<Vec<_>>(),
        [("Smith, J", "St Elsewhere", 0)]
    );
}

#[test]
fn a_grant_with_null_columns_round_trips() {
    // Every column of a grant proper is nullable, which is why the child table
    // carries no UNIQUE constraint on its natural key.
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[grant("Wellcome Trust", "pubmed")],
        &[],
    );
    let id = get_publication_by_pmid(&mut db, "1")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    let stored = get_grants(&mut db, id).unwrap();
    assert_eq!(stored[0].agency.as_deref(), Some("Wellcome Trust"));
    assert_eq!(stored[0].grant_id, None);
    assert_eq!(stored[0].country, None);
}

#[test]
fn re_storing_does_not_duplicate() {
    let mut db = with_schema();
    for _ in 0..3 {
        store_with(
            &mut db,
            Publication {
                pmid: Some("1".into()),
                ..a_paper()
            },
            &[Grant {
                agency: Some("NHLBI".into()),
                grant_id: Some("R01".into()),
                source: "pubmed".into(),
                ..Default::default()
            }],
            &[],
        );
    }
    assert_eq!(count(&mut db, "publication_grants"), 1);
}

#[test]
fn a_record_without_grants_does_not_erase_them() {
    // An absent <GrantList> means the record did not carry the data, not that
    // the funding was withdrawn.
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[grant("NHLBI", "pubmed")],
        &[],
    );
    store(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            sources: vec!["biorxiv".into()],
            ..a_paper()
        },
    );
    assert_eq!(count(&mut db, "publication_grants"), 1);
}

#[test]
fn two_sources_grants_coexist() {
    // Scoping by publication alone made the last sync win, silently.
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[grant("NHLBI", "pubmed")],
        &[],
    );
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            sources: vec!["openalex".into()],
            ..a_paper()
        },
        &[grant("Wellcome Trust", "openalex")],
        &[],
    );

    let id = get_publication_by_pmid(&mut db, "1")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    let mut stored: Vec<(String, String)> = get_grants(&mut db, id)
        .unwrap()
        .into_iter()
        .map(|g| (g.source, g.agency.unwrap()))
        .collect();
    stored.sort();
    assert_eq!(
        stored,
        [
            ("openalex".to_string(), "Wellcome Trust".to_string()),
            ("pubmed".to_string(), "NHLBI".to_string())
        ]
    );
}

#[test]
fn re_syncing_one_source_leaves_the_other_alone() {
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            sources: vec!["openalex".into()],
            ..a_paper()
        },
        &[grant("Wellcome Trust", "openalex")],
        &[],
    );
    for agency in ["Typo Foundation", "NHLBI"] {
        store_with(
            &mut db,
            Publication {
                pmid: Some("1".into()),
                ..a_paper()
            },
            &[grant(agency, "pubmed")],
            &[],
        );
    }

    let id = get_publication_by_pmid(&mut db, "1")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    let mut agencies: Vec<String> = get_grants(&mut db, id)
        .unwrap()
        .into_iter()
        .map(|g| g.agency.unwrap())
        .collect();
    agencies.sort();
    assert_eq!(agencies, ["NHLBI", "Wellcome Trust"]);
}

#[test]
fn a_split_identity_merge_relocates_child_rows() {
    // Foreign keys are on, so a grant left pointing at the dropped publication
    // makes the DELETE raise and aborts the whole store.
    let mut db = with_schema();
    store(
        &mut db,
        Publication {
            doi: Some("10.1/x".into()),
            sources: vec!["openalex".into()],
            ..a_paper()
        },
    );
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[grant("NHLBI", "pubmed")],
        &[AuthorAffiliation {
            author: "Smith, J".into(),
            affiliation: "St Elsewhere".into(),
            source: "pubmed".into(),
            ..Default::default()
        }],
    );

    store(
        &mut db,
        Publication {
            doi: Some("10.1/x".into()),
            pmid: Some("1".into()),
            ..a_paper()
        },
    );

    assert_eq!(count(&mut db, "publications"), 1);
    let kept = get_publication_by_doi(&mut db, "10.1/x").unwrap().unwrap();
    let id = kept.id.unwrap();
    assert_eq!(
        get_grants(&mut db, id)
            .unwrap()
            .into_iter()
            .map(|g| g.agency.unwrap())
            .collect::<Vec<_>>(),
        ["NHLBI"]
    );
    assert_eq!(
        get_author_affiliations(&mut db, id)
            .unwrap()
            .into_iter()
            .map(|a| a.author)
            .collect::<Vec<_>>(),
        ["Smith, J"]
    );
}

#[test]
fn the_kept_rows_children_win_a_merge() {
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            doi: Some("10.1/x".into()),
            sources: vec!["openalex".into()],
            ..a_paper()
        },
        &[grant("Keep Foundation", "pubmed")],
        &[],
    );
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[grant("Drop Foundation", "pubmed")],
        &[],
    );

    store(
        &mut db,
        Publication {
            doi: Some("10.1/x".into()),
            pmid: Some("1".into()),
            ..a_paper()
        },
    );

    let id = get_publication_by_doi(&mut db, "10.1/x")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    assert_eq!(
        get_grants(&mut db, id)
            .unwrap()
            .into_iter()
            .map(|g| g.agency.unwrap())
            .collect::<Vec<_>>(),
        ["Keep Foundation"]
    );
    assert_eq!(count(&mut db, "publication_grants"), 1);
}

#[test]
fn consolidation_moves_only_sources_the_keep_row_lacks() {
    let mut db = with_schema();
    store_with(
        &mut db,
        Publication {
            doi: Some("10.1/x".into()),
            ..a_paper()
        },
        &[grant("Keep NHLBI", "pubmed")],
        &[],
    );
    store_with(
        &mut db,
        Publication {
            pmid: Some("1".into()),
            ..a_paper()
        },
        &[
            grant("Drop NHLBI", "pubmed"),
            grant("Wellcome Trust", "openalex"),
        ],
        &[],
    );

    store(
        &mut db,
        Publication {
            doi: Some("10.1/x".into()),
            pmid: Some("1".into()),
            ..a_paper()
        },
    );

    let id = get_publication_by_doi(&mut db, "10.1/x")
        .unwrap()
        .unwrap()
        .id
        .unwrap();
    let mut agencies: Vec<String> = get_grants(&mut db, id)
        .unwrap()
        .into_iter()
        .map(|g| g.agency.unwrap())
        .collect();
    agencies.sort();
    assert_eq!(agencies, ["Keep NHLBI", "Wellcome Trust"]);
    assert_eq!(count(&mut db, "publication_grants"), 2);
}

#[test]
fn a_row_naming_no_source_is_refused() {
    // Python must guard both `None` and `""`. Rust makes `None`
    // unrepresentable — `Grant.source` is a `String` — so only the empty
    // string survives, which is exactly the value the dataclass default
    // produced and the NOT NULL column accepted happily.
    let mut db = with_schema();
    let mut p = Publication {
        pmid: Some("1".into()),
        ..a_paper()
    };
    let err = store_publication(
        &mut db,
        &mut p,
        &[],
        &[Grant {
            agency: Some("NHLBI".into()),
            source: String::new(),
            ..Default::default()
        }],
        &[],
    );
    assert!(matches!(
        err,
        Err(PublicationError::UnnamedSource {
            table: "publication_grants"
        })
    ));
    // And nothing was left behind: the whole store rolled back.
    assert_eq!(count(&mut db, "publications"), 0);
}

// --- The batching property, at realistic scale ----------------------------

#[test]
fn a_days_records_commit_together() {
    // This is `publications.sync()`'s one-commit-per-day shape, now running
    // against the real store path rather than a toy helper: many
    // `store_publication` calls — each of which opens its own transaction —
    // inside one caller block that pays a single commit.
    let mut db = with_schema();
    transaction(&mut db, |tx| {
        for i in 0..200 {
            let mut p = Publication {
                doi: Some(format!("10.1234/day-{i}")),
                ..a_paper()
            };
            store_publication(tx, &mut p, &[], &[grant("NHLBI", "pubmed")], &[]).expect("store");
        }
        Ok(())
    })
    .unwrap();

    assert_eq!(count(&mut db, "publications"), 200);
    assert_eq!(count(&mut db, "publication_grants"), 200);
}

#[test]
fn a_failed_day_takes_every_record_with_it() {
    let mut db = with_schema();
    let outcome = transaction(&mut db, |tx| {
        for i in 0..50 {
            let mut p = Publication {
                doi: Some(format!("10.1234/day-{i}")),
                ..a_paper()
            };
            store_publication(tx, &mut p, &[], &[], &[]).expect("store");
        }
        Err::<(), _>(bmlib_db::DbError::abort(
            "the day failed after every record stored",
        ))
    });

    assert!(outcome.is_err());
    assert_eq!(count(&mut db, "publications"), 0);
}

#[test]
fn value_round_trips_every_column_type() {
    // `Value` is the boundary type every ported module passes through, so it
    // is worth one test that is about the type rather than about storage.
    let mut db = with_schema();
    let mut p = Publication {
        doi: Some("10.1234/types".into()),
        pmid: None,
        title: "Effects of H₂O and β-blockers — a study".into(),
        r#abstract: None,
        authors: vec!["Müller, K".into(), "李, 明".into()],
        keywords: vec![],
        is_open_access: true,
        ..a_paper()
    };
    store_publication(&mut db, &mut p, &[], &[], &[]).unwrap();

    let back = get_publication_by_doi(&mut db, "10.1234/types")
        .unwrap()
        .unwrap();
    assert_eq!(back.title, "Effects of H₂O and β-blockers — a study");
    assert_eq!(back.authors, ["Müller, K", "李, 明"]);
    assert!(back.keywords.is_empty());
    assert!(back.is_open_access);
    assert_eq!(back.pmid, None);
    assert_eq!(back.r#abstract, None);
    assert_eq!(
        fetch_scalar(&mut db, "SELECT is_open_access FROM publications", &[]).unwrap(),
        Some(Value::Int(1)),
        "SQLite has no boolean; it must land as 0/1 exactly as the Python port stores it"
    );
}
