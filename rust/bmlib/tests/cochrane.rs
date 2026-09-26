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

//! Cochrane models — the named tests.
//!
//! `cochrane_oracle` diffs 49 cases against Python. This file states what those
//! cases cannot: why the severity order is what it is, and which behaviours a
//! reasonable implementation gets wrong.

use bmlib::quality::cochrane_models::{
    bias_type_to_field, collapse_risk_of_bias, create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item, judgement_to_bias_risk, CochraneNotes, CochraneParticipants,
    RiskOfBiasJudgement, ROB_DOMAINS, ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_LOW, ROB_JUDGEMENT_UNCLEAR,
    SEVERITY_ORDER, VALID_ROB_JUDGEMENTS,
};

// ---------------------------------------------------------------------------
// The severity order is the crux of the collapse
// ---------------------------------------------------------------------------

/// `low < unclear < high`, and **`unclear` outranks `low`**. That is the whole
/// point: an unreported domain is not a clean bill of health. You cannot claim
/// low selection-bias risk when allocation concealment was never described.
#[test]
fn unclear_outranks_low_in_the_severity_order() {
    assert_eq!(SEVERITY_ORDER, ["low", "unclear", "high"]);
    let low = SEVERITY_ORDER
        .iter()
        .position(|s| *s == "low")
        .expect("low");
    let unclear = SEVERITY_ORDER
        .iter()
        .position(|s| *s == "unclear")
        .expect("unclear");
    let high = SEVERITY_ORDER
        .iter()
        .position(|s| *s == "high")
        .expect("high");
    assert!(low < unclear, "unclear must outrank low");
    assert!(unclear < high);
}

/// The consequence, end to end: three selection domains judged `"Low risk"`
/// leave the **fourth** at its `"Unclear risk"` default, so the collapsed
/// `selection` field reads `"unclear"`, not `"low"`.
#[test]
fn one_unreported_domain_promotes_the_collapsed_field() {
    let mut rob = create_default_cochrane_risk_of_bias();
    for domain in [
        &mut rob.random_sequence_generation,
        &mut rob.allocation_concealment,
        &mut rob.baseline_outcome_measurements,
    ] {
        domain.judgement = ROB_JUDGEMENT_LOW.to_string();
    }
    // baseline_characteristics is left deliberately untouched.

    let bias = collapse_risk_of_bias(&rob).expect("collapses");
    assert_eq!(
        bias.selection, "unclear",
        "three lows and one unreported must not read as low"
    );
}

/// With all four selection domains reported low, the field does read `"low"` —
/// so the previous test is about the unreported domain and not about the
/// collapse being stuck.
#[test]
fn four_reported_low_domains_do_collapse_to_low() {
    let mut rob = create_default_cochrane_risk_of_bias();
    for domain in [
        &mut rob.random_sequence_generation,
        &mut rob.allocation_concealment,
        &mut rob.baseline_outcome_measurements,
        &mut rob.baseline_characteristics,
    ] {
        domain.judgement = ROB_JUDGEMENT_LOW.to_string();
    }
    let bias = collapse_risk_of_bias(&rob).expect("collapses");
    assert_eq!(bias.selection, "low");
}

/// A high judgement outranks an unclear one, so the collapse takes the worst
/// **rank** and not the last-seen or the most-common.
#[test]
fn high_outranks_unclear() {
    let mut rob = create_default_cochrane_risk_of_bias();
    rob.random_sequence_generation.judgement = ROB_JUDGEMENT_HIGH.to_string();
    rob.allocation_concealment.judgement = ROB_JUDGEMENT_UNCLEAR.to_string();
    let bias = collapse_risk_of_bias(&rob).expect("collapses");
    assert_eq!(bias.selection, "high");
}

/// A field with no reporting domain at all keeps `BiasRisk`'s default. The
/// collapse writes only the fields it has items for.
#[test]
fn the_collapse_covers_all_five_fields() {
    let bias = collapse_risk_of_bias(&create_default_cochrane_risk_of_bias()).expect("collapses");
    // All-unclear input maps all five fields to "unclear".
    assert_eq!(bias.selection, "unclear");
    assert_eq!(bias.performance, "unclear");
    assert_eq!(bias.detection, "unclear");
    assert_eq!(bias.attrition, "unclear");
    assert_eq!(bias.reporting, "unclear");
}

/// The nine domains group onto five fields by `bias_type`, and the grouping is
/// *derived* rather than written per domain — so a tenth domain of an existing
/// type collapses correctly without the function being touched.
#[test]
fn the_nine_to_five_grouping_is_derived_from_bias_type() {
    assert_eq!(ROB_DOMAINS.len(), 9);
    assert_eq!(bias_type_to_field("selection bias"), Some("selection"));
    assert_eq!(bias_type_to_field("detection bias"), Some("detection"));
    // Case and padding are tolerated, which is what makes a model's spelling
    // harmless.
    assert_eq!(bias_type_to_field("  Selection Bias  "), Some("selection"));
    assert_eq!(bias_type_to_field("novel bias"), None);
}

/// A `bias_type` outside the five categories **raises** rather than being
/// dropped. Silently dropping one would return a `BiasRisk` that looks complete
/// and is not.
#[test]
fn an_unknown_bias_type_is_refused_not_dropped() {
    let mut rob = create_default_cochrane_risk_of_bias();
    rob.random_sequence_generation.bias_type = "novel bias".to_string();
    let err = collapse_risk_of_bias(&rob).expect_err("must refuse");
    assert_eq!(err.bias_type, "novel bias");
    assert_eq!(err.domain, "Random sequence generation");
    assert!(err.to_string().contains("Expected one of"));
}

// ---------------------------------------------------------------------------
// Judgement parsing is deliberately lenient
// ---------------------------------------------------------------------------

/// Nine spellings are accepted and anything else becomes `Unclear` — a
/// judgement is a *reading of evidence*, and refusing to produce one because a
/// model spelled it oddly would lose the domain entirely.
#[test]
fn judgement_parsing_accepts_variants_and_defaults_to_unclear() {
    for (input, expected) in [
        ("Low risk", RiskOfBiasJudgement::Low),
        ("low", RiskOfBiasJudgement::Low),
        ("LOW RISK", RiskOfBiasJudgement::Low),
        ("low_risk", RiskOfBiasJudgement::Low),
        ("  Low Risk  ", RiskOfBiasJudgement::Low),
        ("High risk", RiskOfBiasJudgement::High),
        ("high_risk", RiskOfBiasJudgement::High),
        ("Unclear risk", RiskOfBiasJudgement::Unclear),
        ("unclear", RiskOfBiasJudgement::Unclear),
        ("unclear_risk", RiskOfBiasJudgement::Unclear),
        ("unknown", RiskOfBiasJudgement::Unclear),
        ("probably fine", RiskOfBiasJudgement::Unclear),
        ("", RiskOfBiasJudgement::Unclear),
    ] {
        assert_eq!(
            RiskOfBiasJudgement::from_string(input),
            expected,
            "{input:?}"
        );
    }
}

/// Every judgement maps to one of `BiasRisk`'s three words, so the collapse
/// can never index outside its own order.
#[test]
fn every_judgement_maps_into_the_severity_vocabulary() {
    for judgement in VALID_ROB_JUDGEMENTS {
        let word = judgement_to_bias_risk(judgement);
        assert!(
            SEVERITY_ORDER.contains(&word),
            "{judgement} -> {word} is outside the order"
        );
    }
    assert_eq!(judgement_to_bias_risk("something else"), "unclear");
}

// ---------------------------------------------------------------------------
// Serialisation shape
// ---------------------------------------------------------------------------

/// `outcome_type` is omitted when `None` **and when empty** — Python's guard is
/// `if self.outcome_type:`, and an empty string is falsy. The distinction
/// matters for the detection-bias domains, whose label depends on it.
#[test]
fn outcome_type_is_omitted_when_absent_or_empty() {
    let item = create_default_risk_of_bias_item("D", "selection bias", None);
    assert!(!item
        .to_json()
        .as_object()
        .expect("object")
        .contains_key("outcome_type"));

    let item = create_default_risk_of_bias_item("D", "detection bias", Some(""));
    assert!(
        !item
            .to_json()
            .as_object()
            .expect("object")
            .contains_key("outcome_type"),
        "an empty string is omitted, as Python's truthiness test does"
    );

    let item = create_default_risk_of_bias_item("D", "detection bias", Some("subjective"));
    assert_eq!(item.to_json()["outcome_type"], "subjective");
}

/// The nine domains round-trip through JSON by name, so a rename would be
/// caught rather than silently moving a judgement to the wrong field.
#[test]
fn the_nine_domains_round_trip_by_name() {
    let mut rob = create_default_cochrane_risk_of_bias();
    rob.selective_reporting.judgement = ROB_JUDGEMENT_HIGH.to_string();
    let back = bmlib::quality::cochrane_models::CochraneRiskOfBias::from_json(&rob.to_json())
        .expect("round trip");
    assert_eq!(back.selective_reporting.judgement, ROB_JUDGEMENT_HIGH);
    assert_eq!(back, rob);
}

/// All three judgement keys are always present in the summary, including at
/// zero, and a malformed judgement is **skipped** rather than adding a fourth
/// key — which would quietly change what a total means.
#[test]
fn summary_counts_always_has_three_keys() {
    let counts = create_default_cochrane_risk_of_bias().summary_counts();
    assert_eq!(counts.len(), 3);
    assert_eq!(counts[ROB_JUDGEMENT_LOW], 0);
    assert_eq!(counts[ROB_JUDGEMENT_HIGH], 0);
    assert_eq!(counts[ROB_JUDGEMENT_UNCLEAR], 9);

    let mut rob = create_default_cochrane_risk_of_bias();
    rob.selective_reporting.judgement = "nonsense".to_string();
    let counts = rob.summary_counts();
    assert_eq!(counts.len(), 3, "no fourth key for an unreadable judgement");
    assert_eq!(
        counts[ROB_JUDGEMENT_UNCLEAR], 8,
        "the malformed one is skipped"
    );
}

/// The notes table renders `"No additional notes"` when empty and joins its
/// blocks with a blank line — which the Markdown renderer's split relies on.
#[test]
fn notes_render_as_a_blank_line_separated_block_or_a_placeholder() {
    assert_eq!(
        CochraneNotes::default().format_for_table(),
        "No additional notes"
    );

    let notes = CochraneNotes {
        funding_source: Some("F".to_string()),
        ethical_approval: Some("E".to_string()),
        ..CochraneNotes::default()
    };
    assert_eq!(
        notes.format_for_table(),
        "Funding: F\n\nEthical approval: E"
    );
}

/// The participants table renders `N=…`, with per-group counts in parentheses
/// when they are known.
#[test]
fn participants_render_their_totals_and_groups() {
    let mut p = CochraneParticipants::new("Romania", "Heart failure");
    assert_eq!(p.format_for_table(), "Setting: Romania\n\nHeart failure");

    p.total_participants = Some(45);
    assert_eq!(
        p.format_for_table(),
        "Setting: Romania\n\nHeart failure\nN=45"
    );

    p.group_sizes = Some(serde_json::json!({"home": 23}));
    assert_eq!(
        p.format_for_table(),
        "Setting: Romania\n\nHeart failure\nN=45 (home: 23)"
    );
}

/// A **zero** total is omitted, not rendered as `N=0` — Python's truthiness
/// test again, and `N=0` would be a claim the record did not make.
#[test]
fn a_zero_participant_total_is_omitted() {
    let mut p = CochraneParticipants::new("S", "P");
    p.total_participants = Some(0);
    assert_eq!(p.format_for_table(), "Setting: S\n\nP");
}
