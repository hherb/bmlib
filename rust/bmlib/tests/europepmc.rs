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

//! Europe PMC's search record as a `FetchedRecord` — the named tests mirror
//! Python's `tests/test_europepmc_normalizer.py` over the same measured record,
//! and the record-lookup fixture replays both through the whole lookup.

use bmlib::publications::fetchers::europepmc::normalize;
use serde_json::{json, Value};

const RECORD: &str = include_str!("../../../tests/data/europepmc_ppr_record.json");

fn record() -> Value {
    serde_json::from_str(RECORD).expect("the record is JSON")
}

fn with(key: &str, value: Value) -> Value {
    let mut raw = record();
    raw[key] = value;
    raw
}

#[test]
fn every_field_of_the_measured_record() {
    let record = normalize(&record()).expect("an object");
    assert_eq!(record.source, "europepmc");
    assert!(record
        .title
        .starts_with("Economic evidence for a watch-and-wait strategy"));
    assert_eq!(record.doi.as_deref(), Some("10.21203/rs.3.rs-11130366/v1"));
    assert_eq!(record.pmid, None);
    assert_eq!(record.pmc_id, None);
    assert_eq!(
        record.authors,
        [
            "Thapa, Shabnam",
            "Payne, Kathrine",
            "Praveen",
            "Malcomson, Lee",
            "Renehan, Andrew"
        ]
    );
    assert_eq!(record.journal.as_deref(), Some("Research Square"));
    assert_eq!(record.publication_date.as_deref(), Some("2026-09-23"));
    assert_eq!(record.publication_types, ["Preprint"]);
    assert!(record.is_open_access);
    assert_eq!(record.license.as_deref(), Some("cc by"));
    assert_eq!(record.extras.get("ppr_id"), Some(&json!("PPR1325930")));
    assert_eq!(
        record.fulltext_sources,
        [json!({
            "url": "https://www.ebi.ac.uk/europepmc/webservices/rest/PPR1325930/fullTextXML",
            "format": "xml",
            "source": "europepmc",
            "open_access": true,
        })]
    );
    assert!(record
        .abstract_text
        .expect("abstract")
        .starts_with("Abstract\n\nAim: Several studies report that watch-and-wait (W&W)"));
}

#[test]
fn a_record_europe_pmc_does_not_hold_gets_no_address() {
    let record = normalize(&with("inEPMC", json!("N"))).expect("an object");
    assert!(record.fulltext_sources.is_empty());
}

#[test]
fn a_pmcid_is_the_address_when_there_is_one() {
    let record = normalize(&with("pmcid", json!("PMC7745181"))).expect("an object");
    assert_eq!(record.pmc_id.as_deref(), Some("PMC7745181"));
    assert_eq!(
        record.fulltext_sources[0]["url"],
        "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC7745181/fullTextXML"
    );
}

#[test]
fn a_med_record_is_not_addressed_by_its_pmid() {
    let mut raw = with("id", json!("33301246"));
    raw["pmid"] = json!("33301246");
    let record = normalize(&raw).expect("an object");
    assert_eq!(record.pmid.as_deref(), Some("33301246"));
    assert!(record.fulltext_sources.is_empty());
    assert!(record.extras.is_empty());
}

#[test]
fn a_journal_title_is_preferred_to_the_server() {
    let raw = with("journalInfo", json!({"journal": {"title": "The Lancet"}}));
    assert_eq!(
        normalize(&raw).expect("an object").journal.as_deref(),
        Some("The Lancet")
    );
}

#[test]
fn the_abstract() {
    let cases: [(&str, Option<&str>); 11] = [
        (
            "<title>Abstract</title>  <p>  <bold>Aim:</bold> Several *studies*</p>",
            Some("Abstract\n\nAim: Several \\*studies\\*"),
        ),
        ("<p>a < b</p>", Some("a < b")),
        ("<p>x &lt; y &amp; z &#65;&#x42;</p>", Some("x < y & z AB")),
        ("&nbsp;kept", Some("&nbsp;kept")),
        (
            "&#xD800;&#1114112;&#99999999999;",
            Some("&#xD800;&#1114112;&#99999999999;"),
        ),
        (
            "<p class=\"x\">one</p><p>two\n  lines</p>",
            Some("one\n\ntwo lines"),
        ),
        ("plain text, no markup", Some("plain text, no markup")),
        ("<p>a\u{1c}b\u{a0}c</p>", Some("a\u{1c}b\u{a0}c")),
        ("<p> </p>", None),
        ("", None),
        ("<p>  </p><title></title>", None),
    ];
    for (text, expected) in cases {
        let record = normalize(&with("abstractText", json!(text))).expect("an object");
        assert_eq!(record.abstract_text.as_deref(), expected, "{text:?}");
    }
}

#[test]
fn a_wrong_typed_field_costs_that_field() {
    let check = |key: &str, value: Value| normalize(&with(key, value)).expect("an object");
    assert_eq!(check("title", json!(5)).title, "");
    assert_eq!(check("doi", json!(5)).doi, None);
    assert!(check("authorList", json!("x")).authors.is_empty());
    assert_eq!(check("abstractText", json!(5)).abstract_text, None);
    assert!(check("pubTypeList", json!(["x"]))
        .publication_types
        .is_empty());
    assert_eq!(check("bookOrReportDetails", json!("x")).journal, None);
    assert!(!check("isOpenAccess", json!(true)).is_open_access);
    assert_eq!(check("license", json!(5)).license, None);
    assert_eq!(
        check("firstPublicationDate", json!(5)).publication_date,
        None
    );
    assert!(check("id", json!(5)).extras.is_empty());
    let authors = check(
        "authorList",
        json!({"author": ["x", {"lastName": 5, "fullName": "Doe J"}]}),
    )
    .authors;
    assert_eq!(authors, ["Doe J"]);
}

#[test]
fn a_record_that_is_not_an_object_is_none() {
    assert!(normalize(&json!([1])).is_none());
}
