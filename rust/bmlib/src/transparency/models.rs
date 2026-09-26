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

//! Transparency result types, and the rules that read them.

use serde::{Deserialize, Serialize};

/// The score at or below which a result is `MEDIUM` rather than `LOW`.
pub const MEDIUM_RISK_SCORE_THRESHOLD: i64 = 70;

/// How risky a paper looks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TransparencyRisk {
    /// Transparent on every signal checked.
    Low,
    /// Either the score is middling or industry funding is present.
    Medium,
    /// The score is below the threshold, or a downgrade condition fired.
    High,
    /// **Not a judgement about the paper.** No API was reachable, or the
    /// analysis was disabled, or there was no identifier to look one up with —
    /// so an unreachable network does not masquerade as a HIGH-risk paper.
    ///
    /// [`TransparencyResult::unknown_reason`] says which of the three, and is
    /// set **if and only if** this is the level.
    Unknown,
}

impl TransparencyRisk {
    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            TransparencyRisk::Low => "low",
            TransparencyRisk::Medium => "medium",
            TransparencyRisk::High => "high",
            TransparencyRisk::Unknown => "unknown",
        }
    }

    /// Every member, so a test can assert the port carries exactly these.
    pub const ALL: &'static [TransparencyRisk] = &[
        TransparencyRisk::Low,
        TransparencyRisk::Medium,
        TransparencyRisk::High,
        TransparencyRisk::Unknown,
    ];

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// The unrecognised name, quoted — a member that arrived and was refused is
    /// a fact worth naming, and defaulting would silently turn a new server
    /// vocabulary into `UNKNOWN`.
    pub fn parse(raw: &str) -> Result<Self, String> {
        match raw {
            "low" => Ok(TransparencyRisk::Low),
            "medium" => Ok(TransparencyRisk::Medium),
            "high" => Ok(TransparencyRisk::High),
            "unknown" => Ok(TransparencyRisk::Unknown),
            other => Err(format!("unknown transparency risk {other:?}")),
        }
    }
}

/// Why a result is [`TransparencyRisk::Unknown`].
///
/// Set **if and only if** the level is `UNKNOWN`: a determinate result carrying a
/// reason would be a contradiction, and a caller reading one would have to guess
/// which field to believe.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransparencyUnknownReason {
    /// The caller turned the analysis off, so nothing was measured.
    Disabled,
    /// There was no identifier to look the paper up with.
    NoIdentifier,
    /// Every API was unreachable, so nothing was measured.
    Unreachable,
}

impl TransparencyUnknownReason {
    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            TransparencyUnknownReason::Disabled => "disabled",
            TransparencyUnknownReason::NoIdentifier => "no_identifier",
            TransparencyUnknownReason::Unreachable => "unreachable",
        }
    }

    /// Every member.
    pub const ALL: &'static [TransparencyUnknownReason] = &[
        TransparencyUnknownReason::Disabled,
        TransparencyUnknownReason::NoIdentifier,
        TransparencyUnknownReason::Unreachable,
    ];

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// The unrecognised name, quoted.
    pub fn parse(raw: &str) -> Result<Self, String> {
        match raw {
            "disabled" => Ok(TransparencyUnknownReason::Disabled),
            "no_identifier" => Ok(TransparencyUnknownReason::NoIdentifier),
            "unreachable" => Ok(TransparencyUnknownReason::Unreachable),
            other => Err(format!("unknown unknown-reason {other:?}")),
        }
    }
}

/// What became of this analysis's attempt to read the article's full text.
///
/// **A tri-state the score depends on, not a diagnostic.** The distinction that
/// matters is between *nothing was served* and *something was served and
/// refused*: without it, every refusal reaches storage as
/// `full_text_analyzed = false` plus *"full text unavailable"*, which is false
/// for a document served at HTTP 200 and refused — while the score loses up to 30
/// points, enough to reach HIGH.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FullTextStatus {
    /// No request was made, and the source's own answer is why — the record
    /// names no accession. A 200 carrying an empty object **is** an answer and
    /// stays here; a failed *search* does not.
    NotAttempted,
    /// The search request failed, so there was no record to read an accession
    /// from. Its own member because it was the path that gated a whole fix: an
    /// outage stored `NOT_ATTEMPTED` — *"no request was made, and Europe PMC's
    /// own answer is why"* — at HIGH with a downgrade, from zero log lines.
    SearchFailed,
    /// **Requested and not served, and this is the 404 and nothing else.**
    ///
    /// Three other outcomes used to reach it, putting a claim in the remote's
    /// mouth that only the 404 makes: a bmlib defect on the request line, a
    /// 429/503/403, and an empty HTTP 200 body. All three are
    /// [`FullTextStatus::RequestFailed`] now.
    NotServed,
    /// Nobody answered: a non-404 status, or a request this end could not make.
    RequestFailed,
    /// Served, segmented, and scanned as the article's own text.
    Analyzed,
    /// Served, but the document did not arrive whole — it carries no
    /// `</article>`, so its tail was lost in transit.
    Truncated,
    /// Served, but a comment, CDATA section, processing instruction, doctype or
    /// nested-article tag opens and never closes.
    UnterminatedMarkup,
    /// Served, but a `<sub-article>`/`<response>` region is left open at the end,
    /// so the article's own text cannot be told from a review round's.
    UnclosedRegion,
    /// Served, but nothing outside a nested-article region remained.
    EntirelyNested,
}

impl FullTextStatus {
    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            FullTextStatus::NotAttempted => "not_attempted",
            FullTextStatus::SearchFailed => "search_failed",
            FullTextStatus::NotServed => "not_served",
            FullTextStatus::RequestFailed => "request_failed",
            FullTextStatus::Analyzed => "analyzed",
            FullTextStatus::Truncated => "truncated",
            FullTextStatus::UnterminatedMarkup => "unterminated_markup",
            FullTextStatus::UnclosedRegion => "unclosed_region",
            FullTextStatus::EntirelyNested => "entirely_nested",
        }
    }

    /// Every member, so a test can assert the port carries exactly these.
    pub const ALL: &'static [FullTextStatus] = &[
        FullTextStatus::NotAttempted,
        FullTextStatus::SearchFailed,
        FullTextStatus::NotServed,
        FullTextStatus::RequestFailed,
        FullTextStatus::Analyzed,
        FullTextStatus::Truncated,
        FullTextStatus::UnterminatedMarkup,
        FullTextStatus::UnclosedRegion,
        FullTextStatus::EntirelyNested,
    ];

    /// Was full text **served** and then **refused**?
    ///
    /// `true` for exactly the outcomes where the source answered HTTP 200 with a
    /// document this library then declined to scan.
    #[must_use]
    pub fn is_refusal(self) -> bool {
        REFUSED_FULL_TEXT_STATUSES.contains(&self)
    }

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// The unrecognised name, quoted.
    pub fn parse(raw: &str) -> Result<Self, String> {
        FullTextStatus::ALL
            .iter()
            .copied()
            .find(|s| s.as_str() == raw)
            .ok_or_else(|| format!("unknown full text status {raw:?}"))
    }
}

/// The refusal side, **named**.
///
/// A member added later must choose a side, and the rule is **mechanised rather
/// than asserted**: both sides are named sets, and a test asserts the partition.
/// Membership of this set alone would leave the rule enforced by prose — a member
/// added later and omitted from it simply reads as `is_refusal` false, so the
/// silent default runs the wrong way and reports a *served-and-refused* document
/// as one that never arrived.
pub const REFUSED_FULL_TEXT_STATUSES: &[FullTextStatus] = &[
    FullTextStatus::Truncated,
    FullTextStatus::UnterminatedMarkup,
    FullTextStatus::UnclosedRegion,
    FullTextStatus::EntirelyNested,
];

/// The other side, named rather than left implicit.
pub const NOT_REFUSED_FULL_TEXT_STATUSES: &[FullTextStatus] = &[
    FullTextStatus::NotAttempted,
    FullTextStatus::SearchFailed,
    FullTextStatus::NotServed,
    FullTextStatus::RequestFailed,
    FullTextStatus::Analyzed,
];

/// What became of this analysis's attempt to establish posted trial results.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TrialResultsStatus {
    /// No trial accession at all, so there is nothing to ask about.
    NotRegistered,
    /// The registry answered: results are posted.
    Posted,
    /// The registry answered: results are not posted.
    NotPosted,
    /// **A partly-answered check is not a finding.** Several accessions were
    /// named and not all of them replied, so one reachable "no results" must not
    /// outvote an unreachable one.
    ///
    /// Shares its indicator string with [`TrialResultsStatus::RequestFailed`]
    /// because the prose cannot be acted on either way, and sits on the
    /// **unanswered** side of the partition because the compatibility flag is
    /// what both downstreams render — and `false` under `is_answered()` true reads
    /// as *"the trial fell short"*.
    PartlyAnswered,
    /// Nobody answered. *"Would re-running change this?"* is **yes**.
    RequestFailed,
    /// A registration this library cannot ask about: another registry, or an
    /// accession that is missing or malformed. *"Would re-running change this?"*
    /// is **no**.
    ///
    /// Deliberately not named *"registered elsewhere"*, which would be a plain
    /// falsehood in the malformed case.
    NotCheckable,
}

impl TrialResultsStatus {
    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            TrialResultsStatus::NotRegistered => "not_registered",
            TrialResultsStatus::Posted => "posted",
            TrialResultsStatus::NotPosted => "not_posted",
            TrialResultsStatus::PartlyAnswered => "partly_answered",
            TrialResultsStatus::RequestFailed => "request_failed",
            TrialResultsStatus::NotCheckable => "not_checkable",
        }
    }

    /// Every member.
    pub const ALL: &'static [TrialResultsStatus] = &[
        TrialResultsStatus::NotRegistered,
        TrialResultsStatus::Posted,
        TrialResultsStatus::NotPosted,
        TrialResultsStatus::PartlyAnswered,
        TrialResultsStatus::RequestFailed,
        TrialResultsStatus::NotCheckable,
    ];

    /// Does the compatibility flag mean what it says?
    ///
    /// `true` for exactly [`TrialResultsStatus::Posted`] and
    /// [`TrialResultsStatus::NotPosted`], which are the outcomes where it does.
    /// For the rest the flag is `false` because **not enough** was established,
    /// not because the trial fell short — the read both downstreams get wrong.
    #[must_use]
    pub fn is_answered(self) -> bool {
        ANSWERED_TRIAL_RESULTS_STATUSES.contains(&self)
    }

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// The unrecognised name, quoted.
    pub fn parse(raw: &str) -> Result<Self, String> {
        TrialResultsStatus::ALL
            .iter()
            .copied()
            .find(|s| s.as_str() == raw)
            .ok_or_else(|| format!("unknown trial results status {raw:?}"))
    }
}

/// The answered side, named.
pub const ANSWERED_TRIAL_RESULTS_STATUSES: &[TrialResultsStatus] =
    &[TrialResultsStatus::Posted, TrialResultsStatus::NotPosted];

/// The other side, named rather than left implicit.
pub const NOT_ANSWERED_TRIAL_RESULTS_STATUSES: &[TrialResultsStatus] = &[
    TrialResultsStatus::NotRegistered,
    TrialResultsStatus::PartlyAnswered,
    TrialResultsStatus::RequestFailed,
    TrialResultsStatus::NotCheckable,
];

/// User-configurable thresholds and orchestration hints.
///
/// Two groups of fields, with **different owners**:
///
/// * *honoured by the analyzer* — `enabled` short-circuits the analysis, and
///   `score_threshold`, `industry_funding_triggers_downgrade`,
///   `missing_coi_triggers_downgrade` and `tier_downgrade_amount` feed
///   [`calculate_risk_level`] and the tier downgrade;
/// * *honoured by the caller* — `filtering_enabled`, `max_concurrent_analyses`
///   and `cache_results` describe how a consuming application should orchestrate
///   analyses. **The library analyses one document per call and does no
///   filtering, threading or caching of its own**; it carries these so an
///   application has one place to configure transparency behaviour.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TransparencySettings {
    /// Whether to analyse at all.
    pub enabled: bool,
    /// Below this, `HIGH`.
    pub score_threshold: i64,
    /// Whether industry funding plus restricted data is a downgrade.
    pub industry_funding_triggers_downgrade: bool,
    /// Whether a missing COI statement is a downgrade.
    pub missing_coi_triggers_downgrade: bool,
    /// How many tiers a downgrade costs.
    pub tier_downgrade_amount: i64,
    /// Whether the caller should exclude high-risk papers.
    pub filtering_enabled: bool,
    /// How many analyses the caller should run at once.
    pub max_concurrent_analyses: i64,
    /// Whether the caller should cache results.
    pub cache_results: bool,
}

impl Default for TransparencySettings {
    fn default() -> Self {
        TransparencySettings {
            enabled: true,
            score_threshold: 40,
            industry_funding_triggers_downgrade: true,
            missing_coi_triggers_downgrade: true,
            tier_downgrade_amount: 1,
            filtering_enabled: false,
            max_concurrent_analyses: 3,
            cache_results: true,
        }
    }
}

/// Determine the risk level from transparency metrics.
///
/// **`coi_disclosed` is tri-state**, and only an *explicit* `false` triggers the
/// missing-COI downgrade:
///
/// * `Some(true)` — a COI statement was found;
/// * `Some(false)` — full text was inspected and no COI statement exists;
/// * `None` — could not be determined, e.g. the full text was unavailable.
///
/// `None` does not downgrade, so a paper is not penalised merely because its COI
/// status is unknown. That is the difference the `Option` carries: collapsing
/// `None` to `false` would downgrade every closed-access paper, which is exactly
/// the class the full-text statuses exist to report honestly.
#[must_use]
pub fn calculate_risk_level(
    score: i64,
    industry_funding: bool,
    data_availability: &str,
    coi_disclosed: Option<bool>,
    settings: &TransparencySettings,
) -> TransparencyRisk {
    if score < settings.score_threshold {
        return TransparencyRisk::High;
    }

    if settings.industry_funding_triggers_downgrade {
        let restricted = matches!(
            data_availability,
            "restricted" | "not_available" | "not_stated"
        );
        if industry_funding && restricted {
            return TransparencyRisk::High;
        }
    }

    if settings.missing_coi_triggers_downgrade && coi_disclosed == Some(false) {
        return TransparencyRisk::High;
    }

    if score <= MEDIUM_RISK_SCORE_THRESHOLD {
        return TransparencyRisk::Medium;
    }

    if industry_funding {
        return TransparencyRisk::Medium;
    }

    TransparencyRisk::Low
}
