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

//! Multi-API transparency analyzer.
//!
//! A port of `bmlib/transparency/analyzer.py`. Queries CrossRef, Europe PMC
//! (search and full text), PubMed, OpenAlex and ClinicalTrials.gov to assess
//! the transparency of a biomedical publication.
//!
//! # The shape of the port
//!
//! * **`analyze()` wraps none of its steps.** Every network step swallows its
//!   own request failure, because one dead API may cost a *component* and not
//!   the analysis. The per-step `Result`s are consumed inside the step, so the
//!   public method cannot be made to fail by remote data.
//! * **Every JSON shape a remote can send must not escape `analyze()`.**
//!   `request_json` promises an *object*, and the four coercers below
//!   ([`json_object`], [`json_text`], [`json_count`], [`json_bool`]) state the
//!   rule for a value inside it once, at the point of use. Issue #199 measured
//!   48 of 86 corpus rows escaping a public `analyze()` on the Python side.
//! * **`_BUG_TYPES` has no counterpart here**, exactly as the plan's §6 says:
//!   Python catches broadly and then asks "was this the shape of a bmlib
//!   defect?" by consulting an exception hierarchy
//!   (`json.JSONDecodeError` *is* a `ValueError`, `ET.ParseError` *is* a
//!   `SyntaxError`). [`FetchError`] is a closed enum, so the classification is
//!   the type and the two-level split — ERROR for a bmlib defect, WARNING for
//!   the environment — collapses into one WARNING for a transport failure.
//!   [`HttpClient`] has no "bmlib is wrong" channel to raise through.
//! * **This module deliberately does not import from `fulltext`.** The
//!   nested-article element set and the six-signal rules are *restated* rather
//!   than shared, because importing across would make `bmlib.fulltext` a
//!   runtime dependency of `bmlib.transparency`, which it is not. The two
//!   copies are pinned as agreeing by tests on each side.
//! * **It is a corrected port, not a transliteration.** Two filed defects are
//!   implemented as intended: `coi_disclosed` is never a determinate `true` on
//!   an `UNKNOWN` result (issue #306), and a CrossRef body with no readable
//!   `message` stores [`INDICATOR_FUNDERS_NOT_READABLE`] rather than
//!   [`INDICATOR_NO_FUNDER_INFO`] (issue #307). Both are marked at the site.
//!
//! # What could not be expressed
//!
//! * [`HttpClient`] carries neither query parameters nor headers. Parameters
//!   are interpolated into the URL by [`encode_query`]; the `User-Agent`
//!   ([`user_agent`]) and [`JSON_ACCEPT_HEADERS`] are exposed as values for a
//!   real transport to send, but nothing here can attach them.
//! * Python's `logging` has no facade in this crate. Levels are preserved as a
//!   prefix on `stderr`, matching `jats_reader`'s convention, so a measured
//!   WARNING is still distinguishable from a measured DEBUG.
//! * Python holds `_api_reachable` in `threading.local()` so concurrent
//!   `analyze()` calls cannot contaminate each other. Here it is a field on the
//!   per-call [`Analysis`] threaded through the request helpers — the plan's §6
//!   names this as the one place the Python design must change *shape*, and
//!   reachability then cannot leak by construction.

use std::sync::{LazyLock, Mutex, PoisonError};
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use regex::Regex;
use roxmltree::{Document, Node};
use serde_json::{Map, Value};

use crate::publications::fetchers::{HttpClient, HttpResponse};
use crate::transparency::models::{
    calculate_risk_level, FullTextStatus, TransparencyRisk, TransparencySettings,
    TransparencyUnknownReason, TrialResultsStatus,
};

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

/// The two levels this module logs at.
///
/// Python's third, ERROR, was the level for a `_BUG_TYPES` member — an
/// exception type that can only mean bmlib is wrong. It has no counterpart
/// here: [`FetchError`] is a closed enum whose only remotely-produced variant is
/// a transport failure, so there is nothing to route to it and no variant is
/// carried for a case no call site can reach.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Level {
    /// The ordinary outcome, measured to be so for this branch.
    Debug,
    /// Something the environment or the remote did that costs a component.
    Warning,
}

/// Write one log line to `stderr`.
///
/// The crate has no logging facade; `jats_reader` writes its ERROR lines with
/// `eprintln!` and this follows it. The level is a prefix on the message so
/// that a line's level — which is a measured claim in this module, per issue
/// #191 — survives without a logger to set.
fn log_line(level: Level, message: &str) {
    let prefix = match level {
        Level::Debug => "DEBUG",
        Level::Warning => "WARNING",
    };
    eprintln!("bmlib.transparency.analyzer {prefix}: {message}");
}

// ---------------------------------------------------------------------------
// Industry funder keywords
// ---------------------------------------------------------------------------

/// Substring stems, matched anywhere inside a funder name.
///
/// A stem has to match *inside* a longer word ("pharmaceutic" reaching
/// "Pharmaceuticals"); a whole word must not, because "inc" as a substring
/// matches "Lincoln", "Vincent" and "province". Applying word boundaries
/// uniformly (issue #36) would lose the stems; applying substrings uniformly is
/// what made "Pfizer Inc" a false negative in the first place.
///
/// Membership follows four rules, and rule 4 (a named collision with a form the
/// corpus cannot see) vetoes the other three. "pharma" and "biotech" were
/// disqualified *as stems* — 3 TP / 5 FP and 0 TP / 4 FP — and survive as bare
/// words below because a word cannot match more than the stem it replaced.
pub const INDUSTRY_STEMS: &[&str] = &["pharmaceutic", "therapeutics", "laboratories"];

/// Whole words, matched with `\b…\b`.
///
/// No trailing `\.?` is needed: `\b` already sits between the last letter and a
/// following ".", so "Inc" and "Inc." both match. Refused tokens are recorded
/// with the rule that refuses them in the Python source, whose test re-derives
/// every count from `tests/data/funder_names.json`; that corpus and its test do
/// not exist in this port, so the *table* is carried and its measurement is
/// not.
pub const INDUSTRY_WORDS: &[&str] = &[
    "pharma",
    "biotech",
    "incorporated",
    "inc",
    "corp",
    "limited",
    "ltd",
    "gmbh",
    "llc",
    "plc",
    "pty",
];

/// Compile the whole-word alternation the matcher applies to `words`.
///
/// Exists to be shared with a test that scores one token at a time, which
/// cannot use the single union over the whole tuple: with a second, hand-written
/// copy of `\b…\b`, dropping the leading `\b` moved four of the stated counts
/// while the whole-name agreement control stayed green (#112).
#[must_use]
pub fn compile_word_re(words: &[&str]) -> Regex {
    Regex::new(&format!(r"(?i)\b(?:{})\b", words.join("|"))).expect("industry word regex")
}

/// The whole-word alternation over [`INDUSTRY_WORDS`], case-insensitive.
pub static INDUSTRY_WORD_RE: LazyLock<Regex> = LazyLock::new(|| compile_word_re(INDUSTRY_WORDS));

/// Does a structured funder name look like a commercial entity?
///
/// The single predicate behind both funder sources — CrossRef `funder[].name`
/// and PubMed `<Grant><Agency>` — so there is one definition to test and one to
/// measure. Deliberately **not** applied to COI prose: that is a different
/// corpus with different failure modes, and [`INDUSTRY_COI_KEYWORDS`] is why.
#[must_use]
pub fn is_industry_funder(name: &str) -> bool {
    let lower = name.to_lowercase();
    if INDUSTRY_STEMS.iter().any(|stem| lower.contains(stem)) {
        return true;
    }
    INDUSTRY_WORD_RE.is_match(name)
}

/// Industry disclosure phrases, matched against a paper's COI statement.
///
/// Kept separate from the funder keywords above: the generic org suffixes match
/// far too freely in running text, while these phrases never occur in a funder
/// name.
pub const INDUSTRY_COI_KEYWORDS: &[&str] = &[
    "employee of",
    "speaker fee",
    "consultant for",
    "advisory board",
];

// ---------------------------------------------------------------------------
// Europe PMC REST
// ---------------------------------------------------------------------------

/// The base every Europe PMC call is built from.
///
/// One constant rather than two literals, because issue #184 was exactly the
/// two drifting apart: the search call was right and the full-text call was
/// not, and nothing said so.
///
/// **An article is addressed by its Europe PMC accession alone** — no
/// `{source}` segment. Measured, not inferred: on 2026-09-05 the
/// single-segment form served HTTP 200 for five PMC ids and six `PPR`
/// accessions, while `{source}/{ext_id}`, the bare numeric id and the PMID all
/// returned Europe PMC's own 404. The sibling two-segment endpoints 404 the
/// same way, so it is the path shape and not one endpoint.
///
/// The accession is **not** a PMCID and must not be normalised into one: 75,760
/// of Europe PMC's 12,220,678 `IN_EPMC:Y` records are preprints carrying no
/// `pmcid`, addressed by a `PPR…` accession that a PMC-only normaliser would
/// reject. This module and `fulltext` agree on the base and deliberately not on
/// the identifier.
pub const EUROPEPMC_REST_BASE: &str = "https://www.ebi.ac.uk/europepmc/webservices/rest";

/// The pattern an identifier must match, **case-insensitively**, to be fetched.
///
/// `fullmatch`, so `"PMC123\n"` is not an address. Case-insensitive because it
/// is measured and not a courtesy (PR #219's review): probed live on
/// 2026-09-09, `pmc4154587` and `ppr1301373` each serve **HTTP 200 with bytes
/// identical to the uppercase form** (53,167 and 143,394), so a case-sensitive
/// test refuses an address that serves — which is the failure this guard's own
/// argument calls worse than the request it saves.
///
/// **A shape test, not a `source` allow-list.** The two agree on every
/// population drawn and differ where an accession-shaped id arrives under an
/// unenumerated source, where the allow-list refuses a fetch that works.
pub static EUROPEPMC_ACCESSION_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\A(?:PMC|PPR)\d+\z").expect("accession regex"));

/// The other three endpoints.
///
/// Named constants rather than f-strings inside the methods, for the reason
/// [`EUROPEPMC_REST_BASE`] was extracted: a URL buried in a method body is a URL
/// nothing can pin, and issue #184 was one of those wrong for a whole release.
/// The identifier is interpolated with a literal replacement, so a DOI carrying
/// a brace is passed through unharmed.
pub const CROSSREF_WORKS_URL: &str = "https://api.crossref.org/works/{doi}";
/// OpenAlex's DOI-addressed work endpoint.
pub const OPENALEX_WORKS_URL: &str = "https://api.openalex.org/works/doi:{doi}";
/// ClinicalTrials.gov's v2 study endpoint.
pub const CLINICALTRIALS_STUDY_URL: &str = "https://clinicaltrials.gov/api/v2/studies/{nct_id}";

/// The per-request headers CrossRef and OpenAlex are sent.
///
/// [`HttpClient`] cannot carry a header, so this is a value a real transport
/// sends rather than something this module can attach. It is kept because the
/// Python side's sampler probes *these* strings, so a measurement cannot be of
/// a request bmlib does not make. Same reason as the URL constants.
pub const JSON_ACCEPT_HEADERS: &[(&str, &str)] = &[("Accept", "application/json")];

/// The Python library version this port tracks.
///
/// Python interpolates `bmlib.__version__` into the `User-Agent`. The crate's
/// own version tracks the *port*, which would silently change a header a remote
/// judges bmlib by, so the library version is named here instead.
pub const BMLIB_VERSION: &str = "0.10.0";

/// The `User-Agent` every request from this module carries.
///
/// **The trailing `python-httpx` token is load-bearing and is not decoration.**
/// ClinicalTrials.gov's edge refuses an unaccompanied bmlib identification with
/// a bare 134-byte `403 Forbidden` page, so every posted-results check ever
/// made was declined — returning `False`, which in a `bool` was
/// indistinguishable from "this trial posted no results" (issue #194).
/// `SCORE_RESULTS_POSTED` was therefore never awarded to any paper and
/// *"Registered trial without posted results"* was stored as a false claim
/// about every registered trial.
///
/// Measured 2026-09-06 against `/api/v2/studies/{nct}?fields=hasResults`: six
/// alternating rounds of bmlib's header against httpx's own default gave 403/200
/// six times of six. Of thirteen header shapes, the five carrying `python-httpx`
/// served 200, while `curl`, `python-requests`, `Python-urllib`,
/// `Go-http-client`, `PostmanRuntime` and a browser string were all refused. So
/// it is an allow-list on that one token, and its position does not matter.
///
/// The token is **appended to** bmlib's identification rather than replacing
/// it: CrossRef and NCBI both ask a caller to say who it is, and answering
/// `python-httpx` alone would trade one API's policy for two others'. It is not
/// a fiction — this module *is* httpx on the Python side.
///
/// This is a live-only property that **no test can hold**; the Python sampler
/// is the guard there and no equivalent exists here.
#[must_use]
pub fn user_agent(email: &str, version: &str) -> String {
    format!("bmlib/{BMLIB_VERSION} (mailto:{email}) python-httpx/{version}")
}

// ---------------------------------------------------------------------------
// Ordinary statuses
// ---------------------------------------------------------------------------

/// Statuses that log at DEBUG rather than WARNING, **per endpoint**.
///
/// Issue #191's rule: a level is a claim, and the branch it sits on must be no
/// wider than the draw that earned it. **Every one of them is empty, and that
/// is the measurement rather than a default.** 180 records drawn on 2026-09-06,
/// stratified over source × year, plus 60 for the trial population, produced
/// **0 non-200s at all five endpoints** (upper bounds 2.1%-6.8%). Read those as
/// bounds and not as proof: a zero says the ordinary outcome is a 200, not that
/// a 404 cannot happen. The contrast with the full-text 404 is the whole point —
/// there, 81 of 81 non-200s were 404.
pub const CROSSREF_ORDINARY_STATUSES: &[u16] = &[];
/// See [`CROSSREF_ORDINARY_STATUSES`].
pub const EUROPEPMC_SEARCH_ORDINARY_STATUSES: &[u16] = &[];
/// See [`CROSSREF_ORDINARY_STATUSES`].
pub const PUBMED_ORDINARY_STATUSES: &[u16] = &[];
/// See [`CROSSREF_ORDINARY_STATUSES`].
pub const OPENALEX_ORDINARY_STATUSES: &[u16] = &[];
/// See [`CROSSREF_ORDINARY_STATUSES`].
pub const CLINICALTRIALS_ORDINARY_STATUSES: &[u16] = &[];

// ---------------------------------------------------------------------------
// PubMed E-utilities
// ---------------------------------------------------------------------------

/// Microsoft… NCBI's single-record `efetch` endpoint.
pub const EFETCH_URL: &str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi";
/// NCBI asks every E-utilities caller to identify itself.
pub const EUTILS_TOOL_NAME: &str = "bmlib";
/// The root element of a PubMed record set.
///
/// Named because [`report_pubmed_without_citation`] both *tests* for it and
/// *prints* it, and a restated literal would let the test and the message drift
/// apart — the shape issue #184 lived a release on, one endpoint over.
pub const PUBMED_RECORD_SET_ROOT: &str = "PubmedArticleSet";
/// The record element bmlib declines by name.
///
/// Matched as a *child* of the set, never as a descendant: a mixed set is legal
/// and generalising a level past its own population is issue #191 exactly.
pub const PUBMED_BOOK_RECORD: &str = "PubmedBookArticle";

/// `DataBankName` values PubMed emits for clinical-trial registries, lowercased.
///
/// A name outside this set is not necessarily a data-deposition accession: it is
/// one only if it is also a key of [`DEPOSITION_DATABANK_LEVELS`]. A name in
/// neither is simply not scored.
///
/// Both spellings of UMIN's registry are kept: NLM's table says "UMIN CTR" but
/// the hyphenated form appears in older records. "jrct" and "iran registry of
/// clinical trials" are not in NLM's table and are kept anyway.
pub const TRIAL_REGISTRY_NAMES: &[&str] = &[
    "clinicaltrials.gov",
    "isrctn",
    "eudract",
    "anzctr",
    "chictr",
    "cris",
    "ctri",
    "drks",
    "iran registry of clinical trials",
    "irct",
    "japiccti",
    "jmacct",
    "jprn",
    "jrct",
    "ntr",
    "pactr",
    "rebec",
    "repec",
    "rpcec",
    "slctr",
    "tctr",
    "umin-ctr",
    "umin ctr",
];

/// The one registry whose accessions can be followed up.
pub const CLINICALTRIALS_GOV: &str = "clinicaltrials.gov";

/// Repository names and the data-availability level a deposit establishes.
///
/// A mapping rather than a set-per-level so that adding a repository cannot
/// silently inherit a default: the level is the value, so there is nowhere to
/// add a name without stating what a deposit into it is worth.
///
/// Curated from NLM's vocabulary, whose second table this splits in half. The
/// other half is deliberately excluded: dbSNP, GDB, OMIM, PIR, the PubChem
/// family, RefSeq, SWISSPROT, UniMES, UniParc, UniProtKB and UniRef are curated
/// *reference* databases. An OMIM number says the paper is about a known
/// condition; a RefSeq accession names a sequence NCBI curated, not one these
/// authors produced. Neither is evidence that these authors shared their own
/// data, which is what the data-availability component measures.
pub const DEPOSITION_DATABANK_LEVELS: &[(&str, &str)] = &[
    ("bioproject", "full_open"),
    ("dbvar", "full_open"),
    ("dryad", "full_open"),
    ("figshare", "full_open"),
    ("genbank", "full_open"),
    ("geo", "full_open"),
    ("pdb", "full_open"),
    ("sra", "full_open"),
    // Controlled access. The deposit is real, findable and citable, but a
    // reader needs Data Access Committee approval to obtain the data — which is
    // what `on_request` already means, so `full_open` would overstate what a
    // reader can actually get.
    ("dbgap", "on_request"),
];

/// Look a repository up by its lowercased PubMed spelling.
#[must_use]
pub fn deposition_databank_level(name: &str) -> Option<&'static str> {
    DEPOSITION_DATABANK_LEVELS
        .iter()
        .find(|(key, _)| *key == name)
        .map(|(_, level)| *level)
}

// ---------------------------------------------------------------------------
// Indicator strings
// ---------------------------------------------------------------------------

/// The COI claim written before PubMed is consulted when full text was scanned
/// and no disclosure was found.
pub const INDICATOR_NO_COI_IN_FULLTEXT: &str = "No COI disclosure found in full text";

/// **One claim**, and it used to be three lines carrying two each (issue #203).
///
/// The text was `"… unknown (full text unavailable)"`, with siblings reading
/// `"(full text served but not usable)"` and `"(Europe PMC lookup failed)"`.
/// Each parenthetical said what became of the full text — and all three sat in
/// [`INDICATORS_RETRACTED_BY_PUBMED_COI`], so a PubMed `<CoiStatement>` refuting
/// the COI half took the provenance with it, and a HIGH verdict with a tier
/// downgrade could carry a COI *success* as its only human-readable line.
pub const INDICATOR_COI_UNKNOWN: &str = "COI disclosure status unknown";

/// The COI line written when PubMed supplies a `<CoiStatement>`.
pub const INDICATOR_COI_IN_PUBMED: &str = "COI disclosure found in PubMed record";

/// The COI lines written before PubMed is consulted, every one of which claims
/// the status is undeterminable and **nothing else**.
///
/// A set rather than a tuple spelled out at the one call site: a third member
/// was once added at the appending site and not here, so a served-and-refused
/// full text with a PubMed statement stored "status unknown" beside "disclosure
/// found" — permanently, in a persisted field. A retraction is all-or-nothing,
/// so what a line must satisfy to belong here is stated: it asserts something
/// about the COI status and nothing else.
pub const INDICATORS_RETRACTED_BY_PUBMED_COI: &[&str] =
    &[INDICATOR_NO_COI_IN_FULLTEXT, INDICATOR_COI_UNKNOWN];

/// What became of the full text, in prose, keyed on the status that decided it.
///
/// One line per outcome, and the mapping is the point: the three parentheticals
/// it replaces were written on three branches, so they could only ever be as
/// fine-grained as the branch, and `REQUEST_FAILED` shared *"full text
/// unavailable"* with the 404 it was split away from (issue #191). Keyed on the
/// enum, the prose is exactly as precise as the machine-readable half.
///
/// These lines are **never retracted**. That is structural rather than a
/// membership rule: [`note_full_text_provenance`] runs after every step, so
/// there is no window in which a retraction could reach them.
///
/// `NOT_ATTEMPTED`'s line **says only that no request was made**, which is the
/// member's own name. It read *"Europe PMC holds no open-access full text for
/// this article"* until PR #205's review, and that member has several causes of
/// which two contradict it outright. `risk_indicators` is persisted, so the
/// false claim reached storage.
#[must_use]
pub fn full_text_provenance_indicator(status: FullTextStatus) -> Option<&'static str> {
    // The partition is a `match`, so a member added to the enum later is a
    // *compile error* here rather than the silent omission Python's `KeyError`
    // guards against. That is strictly stronger and is the whole of what
    // `test_every_status_says_what_happened` asserts on the other side.
    match status {
        FullTextStatus::Analyzed => None,
        FullTextStatus::NotAttempted => {
            Some("Full text not scanned (no EuropePMC full-text request was made)")
        }
        FullTextStatus::SearchFailed => {
            Some("Full text not scanned (the EuropePMC search produced no answer)")
        }
        FullTextStatus::NotServed => {
            Some("Full text not scanned (EuropePMC served none for this article)")
        }
        FullTextStatus::RequestFailed => {
            Some("Full text not scanned (the request to EuropePMC produced no answer)")
        }
        FullTextStatus::Truncated => {
            Some("Full text not scanned (served, but the document did not arrive whole)")
        }
        FullTextStatus::UnterminatedMarkup => {
            Some("Full text not scanned (served, but its markup does not terminate)")
        }
        FullTextStatus::UnclosedRegion => {
            Some("Full text not scanned (served, but a nested-article region is left open)")
        }
        FullTextStatus::EntirelyNested => Some(
            "Full text not scanned (served, but nothing outside a nested-article region remained)",
        ),
    }
}

/// The one status with nothing to explain, so it has no provenance line.
pub const STATUSES_WITH_NO_PROVENANCE_LINE: &[FullTextStatus] = &[FullTextStatus::Analyzed];

/// The industry-COI line.
pub const INDICATOR_INDUSTRY_COI: &str = "Industry ties disclosed in COI statement";

/// The explicit-denial line.
pub const INDICATOR_DATA_NOT_AVAILABLE: &str = "Data explicitly not available";

/// A prefix, completed with the repository names.
///
/// `data_availability_level` alone cannot distinguish a hard accession from the
/// word "github" appearing somewhere in the full text, and `risk_indicators` is
/// the only channel the result has for that provenance — the same job
/// `Industry funder: X` does.
pub const INDICATOR_DATA_DEPOSITED_PREFIX: &str = "Data deposited: ";

/// The posted-results finding: the registry answered, and said results are not
/// posted.
pub const INDICATOR_NO_POSTED_RESULTS: &str = "Registered trial without posted results";

/// **Every member for which bmlib could not establish the posted-results status
/// shares this line; the enum carries which one it was.**
///
/// Deliberately does not name a registry. It covers a registration in another
/// registry *and* a ClinicalTrials.gov registration whose accession was missing
/// or malformed; saying "registered outside ClinicalTrials.gov" would be a plain
/// falsehood in the second case. The difference a caller can act on is *"would
/// re-running change this?"*, and that is [`TrialResultsStatus`]'s to carry.
pub const INDICATOR_RESULTS_NOT_CHECKABLE: &str =
    "Trial registration found; posted-results status could not be checked";

/// CrossRef holds no funder information for this DOI.
///
/// An absent `funder` key, or one present with an empty array. Both are CrossRef
/// *answering*, so the line is a claim about the record and it is true.
pub const INDICATOR_NO_FUNDER_INFO: &str = "No funder information in CrossRef";

/// CrossRef sent something under `funder` (or no readable `message` at all)
/// that this module cannot read.
///
/// Split out of the line above by PR #208's review, which is issue #191's rule
/// applied one endpoint over: a body that *was* served must not be reported as
/// one that carried nothing, because "CrossRef has no funders for this paper" is
/// a claim about the paper and this is a claim about the exchange. Issue #307
/// extends it one container up: a body with no readable `message` is an
/// exchange bmlib could not read, not a record stating no funders.
pub const INDICATOR_FUNDERS_NOT_READABLE: &str =
    "Funder information could not be read from CrossRef's response";

/// What the PubMed step supplies, named once so that **every** branch failing to
/// supply it says what was lost without restating the list.
///
/// Stated without a count on purpose: the first version of the Python sentence
/// said *"the three branches"* and was already wrong when written, so the
/// arithmetic in the comment justifying the constant was itself the drift the
/// constant exists to stop. The rule is *"interpolate this, never retype it"*.
pub const PUBMED_SIGNALS_LOST: &str = "no COI, trial-registration or grant signals are available";

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

/// Points for a funder record from any source, awarded once.
pub const SCORE_FUNDER_INFO: i64 = 15;
/// Points for an established COI disclosure.
pub const SCORE_COI_DISCLOSED: i64 = 10;
/// Points for data that is fully open.
pub const SCORE_DATA_FULL_OPEN: i64 = 20;
/// Points for data available on request.
pub const SCORE_DATA_ON_REQUEST: i64 = 10;
/// Points for an open-access work.
pub const SCORE_OPEN_ACCESS: i64 = 15;
/// Points for a cited work.
pub const SCORE_CITED: i64 = 5;
/// Points for an established trial registration.
pub const SCORE_TRIAL_REGISTERED: i64 = 20;
/// Points for results posted to a registry.
pub const SCORE_RESULTS_POSTED: i64 = 15;
/// The cap `analyze()` applies to the running total.
pub const MAX_TRANSPARENCY_SCORE: i64 = 100;

/// Data-availability levels ranked by how much data sharing is *established*.
///
/// A second producer of `data_level` can therefore be merged rather than having
/// to assume it runs last. An explicit denial outranks silence because it is a
/// finding rather than the absence of one; any positive level outranks the
/// denial. `calculate_risk_level` accepts two further levels, "restricted" and
/// "not_stated", which the analyzer has never produced — they are for callers
/// computing the level themselves, and are deliberately absent here so that
/// nominating one raises rather than ranking at zero.
pub const DATA_LEVEL_RANK: &[(&str, i64)] = &[
    ("unknown", 0),
    ("not_available", 1),
    ("on_request", 2),
    ("full_open", 3),
];

/// The rank of a level the analyzer produces, or `None` for one it does not.
#[must_use]
pub fn data_level_rank(level: &str) -> Option<i64> {
    DATA_LEVEL_RANK
        .iter()
        .find(|(name, _)| *name == level)
        .map(|(_, rank)| *rank)
}

/// How many trial accessions one paper's posted-results check will ask about.
pub const MAX_TRIAL_IDS_TO_CHECK: usize = 3;
/// Confidence in industry involvement established from structured metadata.
pub const DEFAULT_INDUSTRY_CONFIDENCE: f64 = 0.8;
/// Confidence in industry involvement inferred from COI prose.
///
/// Weaker evidence than a structured funder record, so it gets a moderate
/// confidence.
pub const TEXT_INDUSTRY_CONFIDENCE: f64 = 0.5;

/// An NCT id in an abstract only counts as *this* paper's own registered trial
/// when it appears next to registration language.
///
/// Reviews and pooled analyses that merely cite their constituent trials either
/// list the numbers without such language or list several of them, so those are
/// not credited. These patterns were calibrated against real Europe PMC
/// abstracts (registered RCTs vs. reviews): they credit ~97% of genuinely
/// registered single-trial abstracts while rejecting citation lists of three or
/// more distinct trials.
pub static NCT_ID_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)NCT\d{8}").expect("NCT id regex"));

/// [`NCT_ID_RE`], anchored for the `fullmatch` use.
pub static NCT_ID_FULL_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\ANCT\d{8}\z").expect("NCT id fullmatch regex"));

/// Registration language: ClinicalTrials.gov (tolerating a missing dot),
/// register/registered/registration/registry, or "NCT" as a label rather than an
/// id.
///
/// The `\bnct(?!\d)` negative lookahead is rewritten as `\bnct(?:\D|\z)`, which
/// is the same predicate: "NCT" not followed by a decimal digit, including at
/// end of input.
pub static REGISTRATION_CUE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)clinicaltrials?\.?gov|regist|\bnct(?:\D|\z)").expect("registration cue regex")
});

/// Characters on either side of an NCT id scanned for registration language.
///
/// The cue may precede — "registered under NCT…" — or follow the id —
/// "NCT…; registered at ClinicalTrials.gov".
pub const REGISTRATION_CUE_WINDOW: usize = 60;

/// A paper's own registration cites one (occasionally two linked) trial numbers;
/// three or more distinct ids indicate a citation list of constituent trials.
pub const MAX_OWN_TRIAL_IDS: usize = 2;

// ---------------------------------------------------------------------------
// COI detection patterns
// ---------------------------------------------------------------------------

/// Cue phrases whose presence in scanned text establishes a disclosure.
pub const COI_PATTERNS: &[&str] = &[
    "conflict of interest",
    "competing interest",
    "no conflict",
    "nothing to disclose",
    "declare no",
    "financial disclosure",
];

/// When the full text has no tagged COI section, scan a bounded window after
/// each COI cue phrase instead of the whole document, so industry phrases in
/// references or author affiliations are not misread as disclosures.
pub const COI_FALLBACK_WINDOW: usize = 1000;

/// Negation cues that turn an industry phrase into a denial.
///
/// "None of the authors served as a consultant for … any company" is scoped per
/// sentence: ICMJE-style disclosures routinely enumerate the relationship types
/// they deny, which would otherwise substring-match the disclosure keywords.
pub static NEGATION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(?:no|none|not|neither|nor|never|without|den(?:y|ies|ied))\b")
        .expect("negation regex")
});

/// Otherwise-industry phrases in a clearly non-industry context.
///
/// Being an employee of a university, hospital or government body, or sitting on
/// an editorial/community/safety advisory board, is a genuine disclosure but not
/// an industry tie. Matched spans are blanked before keyword matching, so the
/// rest of the sentence can still disclose a real industry relationship.
///
/// Curated employer nouns only — a generic word like "institute" would excuse
/// industry bodies such as the Novartis Institutes for BioMedical Research.
///
// QUIRK: **this pattern carries no `re.IGNORECASE` on the Python side**, unlike
// every sibling in the module (`flags == re.UNICODE`, 32), so its literals
// match lowercase text only and the guard is reproduced case-sensitively.
//
// That is **unobservable through `analyze()`** and harmless there: both
// branches of `extract_coi_text` lowercase the text before
// `discloses_industry_ties` sees it, so a real disclosure — "The authors are
// employee of the University of X" — arrives as "…of the university of x" and
// is blanked as intended. It is observable by calling the helper directly,
// which the corpus does in both directions: the mixed-case sentence flags and
// the lowercased one does not. Reproduced rather than repaired, the Python
// behaviour being the spec, and the enumerated corrections not including it.
pub static NON_INDUSTRY_CONTEXT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"employees? of (?:the |a |an )?(?:\w+ )?(?:universit\w*|hospitals?|colleges?|schools?|governments?|ministr\w*|national institutes of health|public health)|(?:editorial|community|data safety|safety) advisory board|advisory board of (?:the |this )?journal",
    )
    .expect("non-industry context regex")
});

/// Strips every tag for the text-scoped scans.
pub static TAG_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]+>").expect("tag regex"));

/// JATS containers that hold the COI/disclosure statement:
/// `<fn fn-type="COI-statement">`, `<sec sec-type="conflict">`,
/// `<notes notes-type="COI-statement">`, case variants.
///
/// **A rewrite, not a transliteration.** Python's `\2` backreference pins the
/// closing quote to the opening one, and the `regex` crate has no
/// backreferences (which is what keeps it linear). The honest equivalent is the
/// two explicit quoting alternatives below, whose inner character classes
/// exclude *both* quote characters exactly as Python's does — a fixed quote
/// class would match a mismatched pair, which is why the group exists.
///
/// The `</\1>` half is matched separately by [`tagged_coi_sections`], because a
/// backreference cannot be expressed and the closing element name is the group's
/// value.
pub static COI_SECTION_OPEN_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?is)<(fn|sec|notes)\b[^>]*-type="[^"']*(?:coi|conflict|competing)[^"']*"[^>]*>|<(fn|sec|notes)\b[^>]*-type='[^"']*(?:coi|conflict|competing)[^"']*'[^>]*>"#,
    )
    .expect("COI section opener regex")
});

/// Sections whose `<title>` names conflicts/competing interests but carry no
/// typed attribute. Group 1 is the body.
pub static COI_TITLED_SEC_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?is)<sec\b[^>]*>\s*<title>[^<]*(?:conflict|competing|disclosure)[^<]*</title>(.*?)</sec>",
    )
    .expect("COI titled section regex")
});

// ---------------------------------------------------------------------------
// The nested-article filter
// ---------------------------------------------------------------------------

/// The elements a nested article is carried in.
///
/// A `<sub-article>` or `<response>` is a complete article of its own — its own
/// `<front>`/`<front-stub>`, its own `<body>`, its own back matter — nested
/// inside the one that carries it, and nothing in it is this article's. Peer
/// review rounds, author responses, SciELO's translated full text, meeting
/// abstracts and Europe PMC's own injected "associated-data" block all arrive
/// that way, and reviewers write in exactly the vocabulary the scans hunt for: a
/// round's "the reviewers declare no competing interests" was read as the
/// *article's* disclosure (issue #119).
///
/// The two-element set is complete, and structurally so: of JATS's ~295 elements
/// exactly three admit `<front>`/`<front-stub>` and `<body>`, and the third is
/// `<article>` itself. `bmlib.fulltext.jats_parser` makes the same rule with the
/// same argument at greater length and it is **restated rather than imported**,
/// so `bmlib.transparency` needs nothing from `bmlib.fulltext`. Also structural
/// rather than by `@article-type`, which is `CDATA #IMPLIED`, has four published
/// vocabularies that disagree, and is deposited in none of them.
///
/// Measured over PMC's `oa_comm` baseline package `PMC012xxxxxx` (2025-06-26,
/// 97,909 open-access articles): 3,382 (3.45%) carry a region this removes, and
/// for 602 of those the scan outputs move once the regions go. Nesting is
/// exercised rather than defensive: 98 of the 3,382 carriers nest.
pub const NESTED_ARTICLE_ELEMENTS: &[&str] = &["sub-article", "response"];

/// The names [`UnterminatedMarkupError`] reports for a construct that never
/// closes, keyed on the whole opener.
///
/// Derived rather than enumerated separately on the Python side; here the map is
/// the list and [`unterminated_opener_name`] the fallback, which is provable:
/// every opener is either a key or one of the two element tags.
pub const UNTERMINATED_OPENER_NAMES: &[(&str, &str)] = &[
    ("<!--", "comment"),
    ("<![CDATA[", "CDATA section"),
    ("<?", "processing instruction"),
    ("<!DOCTYPE", "doctype"),
];

/// What an opener is called when the refusal names it.
#[must_use]
pub fn unterminated_opener_name(opener: &str) -> &'static str {
    UNTERMINATED_OPENER_NAMES
        .iter()
        .find(|(key, _)| *key == opener)
        .map_or("tag", |(_, name)| *name)
}

/// The root element's end tag.
///
/// A served `fullTextXML` body that does not contain it did not arrive whole
/// (issue #183). **Presence, not position**: the issue's own
/// `rstrip().endswith(...)` refuses complete articles, since trailing comments,
/// PIs and whitespace after the root are legal — 1,727 of 97,909 archive
/// articles (1.76%) and 23 of 8,118 served ones (0.28%) end
/// `</article><!--requester-ID …-->`.
pub const ROOT_END_TAG: &str = "</article>";

/// A construct in the served body never terminates, so it cannot be lexed.
///
/// Raised by [`strip_nested_articles`] and caught at its one call site, where it
/// becomes a WARNING and a fall back to the abstract. It is not a bmlib defect
/// and not a publisher's deposit either: **0 of 98,789 articles** carries an
/// unterminated construct, so what reaches this is a body truncated or corrupted
/// in transit, an HTTP 200 being no promise that the whole document arrived.
///
/// An error rather than the `None` the function also returns, because the two
/// refusals are different claims and an operator acts on them differently: an
/// unclosed region is a document bmlib will not segment, and this is a document
/// that did not arrive. The message names the construct and the offset.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnterminatedMarkupError {
    message: String,
}

impl UnterminatedMarkupError {
    /// Build the message Python builds, character offset included.
    #[must_use]
    pub fn new(opener: &str, offset: usize) -> Self {
        UnterminatedMarkupError {
            message: format!(
                "unterminated {} ('{}') at offset {}",
                unterminated_opener_name(opener),
                opener,
                offset
            ),
        }
    }

    /// The message, as Python's `str(exception)` would render it.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl std::fmt::Display for UnterminatedMarkupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for UnterminatedMarkupError {}

/// One token of the nested-article lexer.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Token {
    /// A comment, CDATA section, processing instruction or doctype: skipped.
    Skip { end: usize },
    /// A start, end or self-closing tag of one of the two elements.
    Tag {
        closing: bool,
        element: &'static str,
        self_closing: bool,
        end: usize,
    },
    /// A construct that opens and never closes.
    Unterminated { opener: String },
}

/// How many characters precede `byte` in `text`.
fn char_offset(text: &str, byte: usize) -> usize {
    text[..byte].chars().count()
}

/// The byte index `n` characters after `byte`, clamped to the end.
///
/// Python slices strings by *character* index — `abstract[match.end() + 60]`
/// counts characters, not bytes — so every window in this module is measured in
/// characters even though the scan positions are bytes.
fn advance_chars(text: &str, byte: usize, n: usize) -> usize {
    for (count, (index, _)) in text[byte..].char_indices().enumerate() {
        if count == n {
            return byte + index;
        }
    }
    text.len()
}

/// The byte index `n` characters before `byte`, clamped to the start.
fn retreat_chars(text: &str, byte: usize, n: usize) -> usize {
    let mut result = byte;
    let mut chars = text[..byte].char_indices().rev();
    for _ in 0..n {
        match chars.next() {
            Some((index, _)) => result = index,
            None => return 0,
        }
    }
    result
}

/// Scan one token starting at the `<` at `pos`, or `None` if no branch matches.
///
/// This is the hand-rolled replacement for Python's five-branch
/// `_NESTED_ARTICLE_TOKEN_RE`. The plan's §2 is why: the pattern uses a negative
/// lookahead and possessive quantifiers the `regex` crate does not take as
/// written, and reaching for a backtracking engine would reintroduce exactly the
/// super-linear behaviour the Python pattern's structure was chosen to avoid, on
/// input that arrives from the network.
///
/// **The four skip branches are the complete set of places `<sub-article` is not
/// a start tag.** In well-formed XML a literal `<` can only open markup — a `<`
/// in text or in an attribute value has to be escaped — so a comment, a CDATA
/// section, a processing instruction and the DOCTYPE's internal subset are
/// exhaustive. Each is matched as a token and skipped, which is what makes this
/// exact rather than a list of hazards someone thought of. Case-sensitive,
/// because XML is, and `DOCTYPE`/`CDATA` are spelled by the spec.
///
/// The converse does *not* hold for `>`: it is legal unescaped in an attribute
/// value and inside a DOCTYPE's system literal, and `]` is legal inside an
/// entity's replacement text. Both the tag branch and the doctype branch
/// therefore step over quoted literals rather than scanning to the first `>`,
/// and the internal subset ends at the `]` that precedes the `>` rather than at
/// the first one.
fn scan_token(xml: &str, pos: usize) -> Option<Token> {
    let rest = &xml[pos..];
    if let Some(stripped) = rest.strip_prefix("<!--") {
        return Some(match stripped.find("-->") {
            Some(j) => Token::Skip {
                end: pos + 4 + j + 3,
            },
            None => Token::Unterminated {
                opener: "<!--".to_string(),
            },
        });
    }
    if let Some(stripped) = rest.strip_prefix("<![CDATA[") {
        return Some(match stripped.find("]]>") {
            Some(j) => Token::Skip {
                end: pos + 9 + j + 3,
            },
            None => Token::Unterminated {
                opener: "<![CDATA[".to_string(),
            },
        });
    }
    if let Some(stripped) = rest.strip_prefix("<?") {
        return Some(match stripped.find("?>") {
            Some(j) => Token::Skip {
                end: pos + 2 + j + 2,
            },
            None => Token::Unterminated {
                opener: "<?".to_string(),
            },
        });
    }
    if rest.starts_with("<!DOCTYPE") {
        return Some(match scan_doctype(xml, pos) {
            Some(end) => Token::Skip { end },
            None => Token::Unterminated {
                opener: "<!DOCTYPE".to_string(),
            },
        });
    }

    let bytes = xml.as_bytes();
    let mut p = pos + 1;
    let closing = xml[p..].starts_with('/');
    if closing {
        p += 1;
    }
    let (element, name_len) = if xml[p..].starts_with("sub-article") {
        ("sub-article", "sub-article".len())
    } else if xml[p..].starts_with("response") {
        ("response", "response".len())
    } else {
        return None;
    };
    let mut q = p + name_len;
    // The name is followed by a negative lookahead rather than `\b`, because
    // `\b` is a boundary at "-", "." and ":" — all legal in an XML name — so
    // <response-note> and <sub-article-x> matched, and stripped prose no JATS
    // element owns. `\w` is approximated as alphanumeric-or-underscore, which is
    // Python's `\w` for the ASCII names a tag can carry here.
    if let Some(c) = xml[q..].chars().next() {
        if c == '-' || c == '.' || c == ':' || c == '_' || c.is_alphanumeric() {
            return None;
        }
    }
    let attributes_start = q;
    loop {
        if q >= bytes.len() {
            return Some(Token::Unterminated {
                opener: format!("<{}{element}", if closing { "/" } else { "" }),
            });
        }
        match bytes[q] {
            b'>' => {
                // Self-closing is read off the attribute run's last character
                // rather than from a second group, which a "/" inside a quoted
                // value would otherwise have to be kept out of.
                let self_closing = xml[attributes_start..q].ends_with('/');
                return Some(Token::Tag {
                    closing,
                    element,
                    self_closing,
                    end: q + 1,
                });
            }
            b'"' => match xml[q + 1..].find('"') {
                Some(j) => q = q + 1 + j + 1,
                None => {
                    return Some(Token::Unterminated {
                        opener: format!("<{}{element}", if closing { "/" } else { "" }),
                    })
                }
            },
            b'\'' => match xml[q + 1..].find('\'') {
                Some(j) => q = q + 1 + j + 1,
                None => {
                    return Some(Token::Unterminated {
                        opener: format!("<{}{element}", if closing { "/" } else { "" }),
                    })
                }
            },
            _ => q += 1,
        }
    }
}

/// Scan a `<!DOCTYPE …>` declaration, returning the index just past its `>`.
///
/// `None` when it never terminates, which the caller reports as an unterminated
/// doctype. Quoted literals are stepped over and the internal subset is closed
/// at the `]` that precedes the `>`, matching Python's
/// `<!DOCTYPE(?:[^>"'\[]|"[^"]*"|'[^']*')*+(?:\[.*?\]\s*)?>`.
fn scan_doctype(xml: &str, pos: usize) -> Option<usize> {
    let bytes = xml.as_bytes();
    let mut p = pos + "<!DOCTYPE".len();
    while p < bytes.len() {
        match bytes[p] {
            b'>' => return Some(p + 1),
            b'[' => {
                // The internal subset: the first `]` after which optional
                // whitespace and a `>` follow.
                let mut from = p + 1;
                while let Some(rel) = xml[from..].find(']') {
                    let close = from + rel;
                    let tail = &xml[close + 1..];
                    let trimmed = tail.trim_start_matches(char::is_whitespace);
                    let skipped = tail.len() - trimmed.len();
                    if trimmed.starts_with('>') {
                        return Some(close + 1 + skipped + 1);
                    }
                    from = close + 1;
                }
                return None;
            }
            b'"' => {
                let j = xml[p + 1..].find('"')?;
                p = p + 1 + j + 1;
            }
            b'\'' => {
                let j = xml[p + 1..].find('\'')?;
                p = p + 1 + j + 1;
            }
            _ => p += 1,
        }
    }
    None
}

/// Return `xml` with every nested-article region removed.
///
/// A `<sub-article>` or `<response>` region — the element, its content and its
/// end tag — is cut out, and the text either side of it is kept verbatim, so the
/// JATS-tagged containers the COI scan matches on survive untouched.
///
/// Regions are held as a **stack of element names**, not as a flag and not as a
/// bare count, because JATS nests them: a `<response>` sits inside the
/// `<sub-article>` it answers, and an inner end tag would otherwise re-admit the
/// rest of the outer round as the article's own prose. Nesting is measured, not
/// hypothetical: 98 of the 3,382 carriers in the baseline corpus nest. Only a
/// document expat would reject can reach the mismatch case. A self-closing
/// `<sub-article/>` opens nothing.
///
/// # Errors
///
/// [`UnterminatedMarkupError`] when a comment, CDATA section, processing
/// instruction, doctype or nested-article tag opens and never closes, naming
/// which and where. The first one wins and the scan stops: continuing is the
/// quadratic half of issue #160, and there is nothing left to be right about
/// once the markup cannot be located. What it cost before that refusal existed
/// was not only time — 33.6 s for 256 kB of `<!DOCTYPE a[`, each doubling about
/// four times the last — but correctness, since the construct's own content was
/// then read as this article's markup.
pub fn strip_nested_articles(xml: &str) -> Result<Option<String>, UnterminatedMarkupError> {
    let bytes = xml.as_bytes();
    let mut kept = String::new();
    // The open regions, innermost last. A bare count read `</response>` as
    // closing a region a `<sub-article>` had opened, and the rest of the outer
    // round then came back as the article's own prose — the defect issue #119
    // removed, from inside the fix for it. Names cost one list.
    let mut open_elements: Vec<&'static str> = Vec::new();
    let mut resume_at = 0_usize;
    let mut i = 0_usize;

    while i < bytes.len() {
        let Some(rel) = xml[i..].find('<') else {
            break;
        };
        let pos = i + rel;
        match scan_token(xml, pos) {
            None => i = pos + 1,
            Some(Token::Skip { end }) => i = end,
            Some(Token::Unterminated { opener }) => {
                return Err(UnterminatedMarkupError::new(&opener, char_offset(xml, pos)))
            }
            Some(Token::Tag {
                closing,
                element,
                self_closing,
                end,
            }) => {
                if closing {
                    // A mismatch is ignored rather than refused here: at depth 0
                    // it is the harmless stray end tag the docstring scopes, and
                    // inside a region it leaves that region open, which the
                    // refusal below catches. Either way no nested prose reaches
                    // the scans.
                    if open_elements.last() == Some(&element) {
                        open_elements.pop();
                        if open_elements.is_empty() {
                            resume_at = end;
                        }
                    }
                } else if !self_closing {
                    if open_elements.is_empty() {
                        kept.push_str(&xml[resume_at..pos]);
                    }
                    open_elements.push(element);
                }
                i = end;
            }
        }
    }

    if !open_elements.is_empty() {
        // The caller has no full text rather than a guess. Both other readings
        // are worse: scanning the tail is the defect itself, and dropping it
        // silently manufactures the "No COI disclosure found in full text"
        // finding, which triggers the missing-COI downgrade.
        return Ok(None);
    }
    kept.push_str(&xml[resume_at..]);
    Ok(Some(kept))
}

// ---------------------------------------------------------------------------
// COI / data-availability text scans
// ---------------------------------------------------------------------------

/// Find the first `</name>` after `from`, ASCII-case-insensitively.
///
/// The `regex` crate has no backreferences, so Python's `</\1>` is matched in a
/// second step here. The closing name can only be one of `fn`, `sec` and
/// `notes`, whose case folding is ASCII, so an ASCII-insensitive scan is exact.
fn find_closing_tag(xml: &str, name: &str, from: usize) -> Option<usize> {
    let needle = format!("</{name}>");
    let hay = &xml[from..];
    for (index, _) in hay.match_indices("</") {
        let start = index;
        let end = start + needle.len();
        if end <= hay.len() && hay[start..end].eq_ignore_ascii_case(&needle) {
            return Some(from + start);
        }
    }
    None
}

/// The bodies of every tagged COI container, in document order.
///
/// `_COI_SECTION_RE`'s matches come first, then `_COI_TITLED_SEC_RE`'s, exactly
/// as Python concatenates the two lists, and the two are searched independently
/// so an opener inside a previously matched body is not re-matched.
fn tagged_coi_sections(full_text: &str) -> Vec<&str> {
    let mut sections: Vec<&str> = Vec::new();
    let mut search_from = 0_usize;
    for caps in COI_SECTION_OPEN_RE.captures_iter(full_text) {
        let Some(whole) = caps.get(0) else {
            continue;
        };
        if whole.start() < search_from {
            continue;
        }
        let Some(element) = caps.get(1).or_else(|| caps.get(2)) else {
            continue;
        };
        if let Some(close) = find_closing_tag(full_text, element.as_str(), whole.end()) {
            sections.push(&full_text[whole.end()..close]);
            search_from = close + element.as_str().len() + 3;
        }
    }
    for caps in COI_TITLED_SEC_RE.captures_iter(full_text) {
        if let Some(body) = caps.get(1) {
            sections.push(body.as_str());
        }
    }
    sections
}

/// The text of JATS-tagged COI containers, tag-stripped and lowercased.
///
/// Returns an empty string when the text carries no tagged COI section. A
/// non-blank result is structural proof that the paper has a COI/disclosure
/// statement, regardless of its wording (issue #13).
#[must_use]
pub fn extract_tagged_coi_text(full_text: &str) -> String {
    let sections = tagged_coi_sections(full_text);
    if sections.is_empty() {
        return String::new();
    }
    TAG_RE.replace_all(&sections.join(" "), " ").to_lowercase()
}

/// The COI/disclosure portion of `full_text`, tag-stripped and lowercased.
///
/// Prefers JATS-tagged COI containers (see [`extract_tagged_coi_text`]); falls
/// back to fixed-size windows following each COI cue phrase when the tagged text
/// is blank — a whitespace-only tagged section proves nothing, so an untagged
/// disclosure elsewhere in the text must still be found. Returns an empty string
/// when no COI-like region is found. Pass `tagged` to reuse an already-computed
/// [`extract_tagged_coi_text`] result instead of rescanning.
///
/// Known limitation: a fallback window is a fixed span, so it can bleed past the
/// end of a short disclosure into whatever follows (acknowledgements,
/// references). Accepted trade-off, matched by the moderate
/// [`TEXT_INDUSTRY_CONFIDENCE`] given to text-derived signals.
#[must_use]
pub fn extract_coi_text(full_text: &str, tagged: Option<&str>) -> String {
    let owned;
    let tagged = match tagged {
        Some(value) => value,
        None => {
            owned = extract_tagged_coi_text(full_text);
            &owned
        }
    };
    if !tagged.trim().is_empty() {
        return tagged.to_string();
    }

    let text = TAG_RE.replace_all(full_text, " ").to_lowercase();
    let mut windows: Vec<&str> = Vec::new();
    // Leftmost-first, non-overlapping, pattern order within a position — what
    // Python's `"|".join(...)` alternation does with `finditer`.
    let mut cursor = 0_usize;
    while cursor < text.len() {
        let mut matched = false;
        for pattern in COI_PATTERNS {
            if text[cursor..].starts_with(pattern) {
                let end = advance_chars(&text, cursor + pattern.len(), COI_FALLBACK_WINDOW);
                windows.push(&text[cursor..end]);
                cursor += pattern.len();
                matched = true;
                break;
            }
        }
        if !matched {
            match text[cursor..].chars().next() {
                Some(c) => cursor += c.len_utf8(),
                None => break,
            }
        }
    }
    windows.join(" ")
}

/// Does a COI sentence disclose (not deny) industry ties?
///
/// A sentence counts only when it contains an industry disclosure phrase (see
/// [`INDUSTRY_COI_KEYWORDS`]) and no negation cue, so an enumerated denial
/// ("none of the authors served as a consultant for …") is not misread as a
/// disclosure. A genuine disclosure alongside a denial sentence still counts,
/// since sentences are scored independently. Clearly non-industry contexts (see
/// [`NON_INDUSTRY_CONTEXT_RE`]) are blanked out before matching, so they neither
/// trigger a sentence nor mask an industry tie disclosed alongside them.
///
/// This is keyword matching, not entity recognition: an unlisted non-industry
/// employer ("employee of the World Bank") still flags. That residual fuzziness
/// is why text-derived signals carry only [`TEXT_INDUSTRY_CONFIDENCE`].
#[must_use]
pub fn discloses_industry_ties(coi_text: &str) -> bool {
    for sentence in coi_text.split(['.', ';']) {
        let blanked = NON_INDUSTRY_CONTEXT_RE.replace_all(sentence, " ");
        if INDUSTRY_COI_KEYWORDS.iter().any(|kw| blanked.contains(kw))
            && !NEGATION_RE.is_match(&blanked)
        {
            return true;
        }
    }
    false
}

/// Data-availability cue phrases and the level each establishes, in order.
///
/// Order matters: matching stops at the first hit, so the negated form ("not
/// available") is checked before the "…upon request" phrases. Otherwise a
/// statement like "data are not available upon reasonable request" would match
/// "upon reasonable request" and be scored as if data sharing were offered.
pub const DATA_PATTERNS: &[(&str, &str)] = &[
    ("not available", "not_available"),
    ("zenodo", "full_open"),
    ("figshare", "full_open"),
    ("dryad", "full_open"),
    ("github", "full_open"),
    ("available upon request", "on_request"),
    ("upon reasonable request", "on_request"),
];

// ---------------------------------------------------------------------------
// Reading a decoded JSON body
// ---------------------------------------------------------------------------
//
// `request_json` guarantees the body is a JSON *object*, and that is the whole
// of what a boundary can promise: every value inside it is still whatever the
// remote chose to send. Reading one as a mapping, a string or a number is
// therefore an assumption, and three of the four coercers below were measured
// escaping a public `analyze()` on the Python side as a `_BUG_TYPES` member —
// `.get()` on a list, `.lower()` on an object, `>` between a string and an int
// (issue #199).
//
// `json_bool` is the fourth and was measured differently, which is why it is
// worth naming separately: a wrong-typed boolean **raises nothing**, so no
// contract net can see it. `bool("no")` is `True`, and that read published
// *"results posted"* for a ClinicalTrials.gov body stating the opposite — a
// false claim about a trial (PR #208's review).
//
// They are coercers rather than guards on purpose: the caller's next line is a
// read, and a value of the wrong type is the *absence* of the value that was
// asked for, which is what the readers already do with an absent key.

/// An empty object, so [`json_object`] can return a reference for a non-object.
static EMPTY_OBJECT: LazyLock<Map<String, Value>> = LazyLock::new(Map::new);

/// `value` if it is a JSON object, else an empty one.
///
/// Replaces `x.get("k", {})`, which returns the default only for an **absent**
/// key — a key present with `null`, or with an array, hands the caller the wrong
/// type and the next `.get()` raises. That exact defect is already recorded
/// against `fulltext`'s free-PDF extractor one package over, which is why it is
/// worth a named helper rather than an `isinstance` at each site.
#[must_use]
pub fn json_object(value: &Value) -> &Map<String, Value> {
    value.as_object().unwrap_or(&EMPTY_OBJECT)
}

/// `value` if it is a JSON string, else `""`.
///
/// `(x.get("k") or "")` looks like this and is not: it rescues `null` and passes
/// an object or an array straight through to the `.lower()` or the regex that
/// follows.
#[must_use]
pub fn json_text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}

/// The text under `key`, or `""` when the key is absent or not a string.
#[must_use]
pub fn map_text<'a>(map: &'a Map<String, Value>, key: &str) -> &'a str {
    map.get(key).map_or("", |value| json_text(value))
}

/// `value` if it is a JSON integer, else `0`.
///
/// `bool` is excluded although it is trivially a number: a `"cited_by_count":
/// true` would otherwise compare greater than zero and award [`SCORE_CITED`] on
/// a body that stated no count at all. `serde_json`'s `as_i64` excludes both
/// booleans and floats, so the helper's name is not a lie about what it
/// validated; a `u64` above `i64::MAX` saturates rather than reading as absent,
/// since the only use is `> 0`.
#[must_use]
pub fn json_count(value: &Value) -> i64 {
    match value {
        Value::Number(number) => number
            .as_i64()
            .or_else(|| {
                number
                    .as_u64()
                    .map(|n| i64::try_from(n).unwrap_or(i64::MAX))
            })
            .unwrap_or(0),
        _ => 0,
    }
}

/// `value` if it is a JSON boolean, else `None`.
///
/// `None` and not `false`, because both callers need *"the remote did not say"*
/// to be a third answer rather than a negative one. This is the only coercer
/// whose absent value is not falsy, and the reason is that the other three
/// replace a read that **raised**: a wrong-typed boolean raises nothing, so the
/// defect it prevents is a value read *wrongly* and stored, which no contract
/// net can see (PR #208's review).
///
/// Measured, at both call sites: `{"hasResults": "no"}` scored
/// [`SCORE_RESULTS_POSTED`] and stored [`TrialResultsStatus::Posted`] with
/// `trial_results_compliant = true` — ClinicalTrials.gov stating *no results*
/// published as *results posted* — and `{"is_oa": "false"}` awarded
/// [`SCORE_OPEN_ACCESS`]. Both are truthy strings, so `bool()` inverts the
/// remote's answer rather than merely losing it.
#[must_use]
pub fn json_bool(value: &Value) -> Option<bool> {
    value.as_bool()
}

/// Python's `type(value).__name__`, for a log line that names the shape served.
fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(number) => {
            if number.is_f64() {
                "float"
            } else {
                "int"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// The leading run of object records in a Europe PMC search body.
///
/// `epmc["resultList"]["result"]` is walked by three readers —
/// [`find_trial_ids`], [`pmid_from_epmc`] and the Europe PMC step — which each
/// hand-rolled it on the Python side, and every level of it is a value the
/// remote chose: `resultList` may be an array, `result` may be an object
/// (`result[0]` then raised `KeyError`), and a record may be a bare string.
///
/// **The list is truncated at the first non-object, never filtered.** Europe PMC
/// returns best-match-first and every reader takes `records[0]` as *this paper*,
/// so an index is a rank: dropping a malformed record promotes the one behind
/// it, and a filter whose head was bad silently made a **different article** the
/// subject — its trial accession, its PMID sent on to `efetch`, its abstract
/// scanned for COI. That is worse than the `KeyError` it replaced, which at
/// least said so. Stopping keeps every record at its own index.
#[must_use]
pub fn epmc_records(epmc: &Value) -> Vec<&Map<String, Value>> {
    let result_list = json_object(epmc).get("resultList");
    let result = match result_list {
        Some(value) => json_object(value).get("result"),
        None => None,
    };
    let Some(array) = result.and_then(Value::as_array) else {
        return Vec::new();
    };
    let mut records = Vec::new();
    for record in array {
        match record.as_object() {
            Some(map) => records.push(map),
            None => break,
        }
    }
    records
}

/// NCT ids that identify *this* paper's own registered trial.
///
/// The abstract is scanned for `NCT` accession numbers, but a match is only
/// credited as the paper's own registration when it appears next to registration
/// language (see [`REGISTRATION_CUE_RE`]). Abstracts that list three or more
/// distinct ids are treated as citation lists — e.g. a systematic review or
/// pooled analysis enumerating its constituent trials — and return nothing, so a
/// review is not credited for registrations that belong to studies it merely
/// cites.
///
/// `epmc` is the record `analyze()` already fetched, and this reads it rather
/// than fetching anything: no client, and **no fallback query**. The Python
/// version took a client and documented *"falling back to a fresh query only if
/// it was not supplied, so the same search is not issued twice per document"* —
/// decided with `if data is None`, which is exactly what a **failed** search
/// returns, so during an outage the identical failing search went out twice and
/// was reported twice for one document (issue #202). Deleting the fallback fixes
/// it structurally: `None` returns nothing, quietly, the outage having already
/// been reported where it happened.
#[must_use]
pub fn find_trial_ids(epmc: Option<&Value>) -> Vec<String> {
    let Some(epmc) = epmc else {
        return Vec::new();
    };
    let records = epmc_records(epmc);
    if records.is_empty() {
        return Vec::new();
    }
    let abstract_text = TAG_RE
        .replace_all(map_text(records[0], "abstractText"), " ")
        .into_owned();

    let mut distinct_ids: Vec<String> = Vec::new();
    for found in NCT_ID_RE.find_iter(&abstract_text) {
        let upper = found.as_str().to_uppercase();
        if !distinct_ids.contains(&upper) {
            distinct_ids.push(upper);
        }
    }
    if distinct_ids.is_empty() || distinct_ids.len() > MAX_OWN_TRIAL_IDS {
        return Vec::new();
    }

    for found in NCT_ID_RE.find_iter(&abstract_text) {
        let start = retreat_chars(&abstract_text, found.start(), REGISTRATION_CUE_WINDOW);
        let end = advance_chars(&abstract_text, found.end(), REGISTRATION_CUE_WINDOW);
        if REGISTRATION_CUE_RE.is_match(&abstract_text[start..end]) {
            return distinct_ids;
        }
    }
    Vec::new()
}

/// The PMID carried by an already-fetched Europe PMC record.
///
/// Lets a DOI-only analysis reach PubMed without spending an extra request to
/// resolve the identifier.
///
/// This was the third hand-rolled walk of `resultList.result` and the one issue
/// #199's fix missed: reached as `pmid or pmid_from_epmc(epmc)`, it runs only
/// when the caller passed no PMID — exactly the path the issue's end-to-end net
/// never drove, every row of it supplying one.
///
/// The identifier is type-checked rather than stringified: `str({"a": 1})` is
/// truthy and would be sent to NCBI's `efetch` as the literal `"{'a': 1}"`. An
/// integer is still accepted — Europe PMC serves the field as a string, and
/// refusing a number would narrow behaviour on well-formed input for no gain —
/// while a fractional number is not, since Python's `isinstance(x, int)` rejects
/// it.
#[must_use]
pub fn pmid_from_epmc(epmc: Option<&Value>) -> Option<String> {
    let epmc = epmc?;
    let records = epmc_records(epmc);
    let first = records.first()?;
    let pmid = first.get("pmid")?;
    if pmid.is_boolean() {
        return None;
    }
    if let Some(number) = pmid.as_i64() {
        return Some(number.to_string());
    }
    if let Some(number) = pmid.as_u64() {
        return Some(number.to_string());
    }
    let text = json_text(pmid);
    if text.is_empty() {
        None
    } else {
        Some(text.to_string())
    }
}

// ---------------------------------------------------------------------------
// The accumulator
// ---------------------------------------------------------------------------

/// Everything [`TransparencyAnalyzer::analyze`] accumulates.
///
/// Public because the differential oracle and the named tests drive the merge
/// rules directly; Python's `_Analysis` is private but imported by its tests for
/// the same reason. It never leaves this crate's analysis path as a value: only
/// [`TransparencyResult`] does.
///
/// Passing one value through the sub-steps rather than unpacking a tuple binds a
/// value to its name, so a mis-ordered swap is not a silently type-compatible
/// one — `industry_funding` and `funder_info_scored` are both `bool`, and `score`
/// is interchangeable with any other `i64`.
#[derive(Debug, Clone)]
pub struct Analysis {
    /// Running transparency score, uncapped until `analyze()` ends.
    pub score: i64,
    /// Human-readable findings, in the order they were made.
    pub indicators: Vec<String>,
    /// Any industry involvement was detected.
    pub industry_funding: bool,
    /// Confidence in that detection; the strongest evidence seen wins,
    /// regardless of arrival order.
    pub industry_confidence: f64,
    /// Data-availability level; the strongest evidence seen wins, regardless of
    /// arrival order. Set through [`Analysis::note_data_level`], never assigned.
    pub data_level: String,
    /// Tri-state — `Some(true)` (statement found), `Some(false)` (full text
    /// scanned, none found), `None` (undeterminable).
    pub coi_disclosed: Option<bool>,
    /// A trial registration was established.
    pub trial_registered: bool,
    /// Posted results were found for a registered trial.
    pub results_compliant: bool,
    /// What became of the posted-results check.
    ///
    /// Defaults to [`TrialResultsStatus::NotRegistered`], which is what an
    /// analysis that never establishes a registration should carry. Never
    /// absent here: the carrier is built fresh by every analysis, so "not
    /// recorded" cannot arise.
    pub trial_results_status: TrialResultsStatus,
    /// Findings came from full text, not just an abstract.
    pub full_text_analyzed: bool,
    /// [`SCORE_FUNDER_INFO`] has been spent.
    ///
    /// Named state rather than a positional bool, so a third funder source gets
    /// the once-only rule from [`Analysis::award_funder_info`] instead of having
    /// to remember a convention. The field stays writable — the rule lives in
    /// the method, not the type — so a source that spends the component by hand
    /// can still double-score it. Go through `award_funder_info()`.
    pub funder_info_scored: bool,
    /// What became of the full-text attempt.
    ///
    /// Defaults to [`FullTextStatus::NotAttempted`], which is what an analysis
    /// that never reaches the Europe PMC step *because Europe PMC said so* (no
    /// record, or `inEPMC != "Y"`) should carry. There is a third way not to
    /// reach it — the search itself producing no answer — and the default is the
    /// wrong value for that one, which is why `analyze()` overwrites it with
    /// [`FullTextStatus::SearchFailed`] rather than leaving it (issue #193).
    pub full_text_status: FullTextStatus,
    /// Whether any external API answered during this analysis.
    ///
    /// Python holds this in `threading.local()` on the analyzer and documents
    /// why: concurrent `analyze()` calls must not contaminate each other, or a
    /// thread whose APIs were all down inherits a concurrent thread's success and
    /// gets scored 0 / HIGH instead of UNKNOWN, wrongly triggering a tier
    /// downgrade. Here it is per-call state, so it cannot leak by construction.
    pub api_reachable: bool,
}

impl Default for Analysis {
    fn default() -> Self {
        Analysis {
            score: 0,
            indicators: Vec::new(),
            industry_funding: false,
            industry_confidence: 0.0,
            data_level: "unknown".to_string(),
            coi_disclosed: None,
            trial_registered: false,
            results_compliant: false,
            trial_results_status: TrialResultsStatus::NotRegistered,
            full_text_analyzed: false,
            funder_info_scored: false,
            full_text_status: FullTextStatus::NotAttempted,
            api_reachable: false,
        }
    }
}

impl Analysis {
    /// Award [`SCORE_FUNDER_INFO`] the first time any source reports funders.
    ///
    /// Two sources can report them — CrossRef funder records and PubMed's
    /// `<GrantList>` — and the component is worth 15 points once, not twice.
    /// Neither caller has to know whether the other ran first, which is what
    /// makes a third source safe to add.
    pub fn award_funder_info(&mut self) {
        if !self.funder_info_scored {
            self.score += SCORE_FUNDER_INFO;
            self.funder_info_scored = true;
        }
    }

    /// Record `name` as an industry funder named in structured metadata.
    ///
    /// The confidence is fixed at [`DEFAULT_INDUSTRY_CONFIDENCE`] rather than
    /// passed in: "structured metadata" — a CrossRef funder record or a PubMed
    /// `<Grant><Agency>` — is exactly what distinguishes this from the weaker
    /// prose signal in [`Analysis::note_industry_coi`], and a caller free to
    /// choose the number could blur the two.
    ///
    /// The indicator is deduplicated. One funder is one finding however many
    /// sources report it, and however often a single source repeats it: both
    /// registries emit one record per award, so an organisation funding four
    /// awards on one paper appears four times upstream.
    pub fn note_industry_funder(&mut self, name: &str) {
        self.industry_funding = true;
        self.industry_confidence = self.industry_confidence.max(DEFAULT_INDUSTRY_CONFIDENCE);
        let line = format!("Industry funder: {name}");
        if !self.indicators.contains(&line) {
            self.indicators.push(line);
        }
    }

    /// Record industry ties disclosed in a full-text COI statement.
    ///
    /// Weaker evidence than a funder record — an inference from prose rather
    /// than a structured field — so it raises the confidence only to
    /// [`TEXT_INDUSTRY_CONFIDENCE`] and never lowers a stronger one.
    ///
    // QUIRK: unlike `note_industry_funder`, the indicator is **not**
    // deduplicated. The step runs at most once per analysis today, so the
    // duplicate is unreachable; a second call site would store the line twice,
    // and Python does the same. Reproduced rather than fixed, because the
    // dedupe question belongs to a change that adds the second call site.
    pub fn note_industry_coi(&mut self) {
        self.industry_funding = true;
        self.industry_confidence = self.industry_confidence.max(TEXT_INDUSTRY_CONFIDENCE);
        self.indicators.push(INDICATOR_INDUSTRY_COI.to_string());
    }

    /// Nominate `level` as the paper's data availability; the strongest wins.
    ///
    /// Two sources produce this — Europe PMC's full-text pattern scan and
    /// PubMed's `<DataBankList>` deposition accessions — and neither can know
    /// whether the other ran first, so the field is merged by rank rather than
    /// assigned. A source that found nothing nominates `"unknown"`, which is a
    /// no-op: finding nothing is not evidence against what another source found.
    ///
    /// # Panics
    ///
    /// If `level` is not a level the analyzer produces. A typo must fail loudly
    /// rather than silently rank below everything — Python raises `KeyError`
    /// here and the panic is its counterpart.
    pub fn note_data_level(&mut self, level: &str) {
        let rank = data_level_rank(level).unwrap_or_else(|| {
            panic!("unknown data level {level:?}, which this analyzer never produces")
        });
        let current = data_level_rank(&self.data_level).expect("carrier level is a known level");
        if rank > current {
            self.data_level = level.to_string();
        }
    }
}

/// Transparency signals carried by a PubMed record.
///
/// All are structured publisher-supplied metadata, which is why they outrank the
/// text heuristics elsewhere in this module. An empty instance is the result of
/// every failure path (no PMID, unreachable, unparsable), so callers never have
/// to distinguish "no signals" from "no answer".
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PubMedSignals {
    /// A non-blank `<CoiStatement>` is present.
    pub coi_statement: bool,
    /// ClinicalTrials.gov NCT ids, upper-cased, deduplicated in document order.
    pub trial_accessions: Vec<String>,
    /// A registration was recorded that ClinicalTrials.gov cannot be asked
    /// about — either it belongs to another registry, or it is a
    /// ClinicalTrials.gov entry whose accession is missing or malformed.
    /// Registration is established either way; followability is the separate
    /// fact this records.
    pub registration_not_checkable: bool,
    /// Distinct `<Grant><Agency>` names, in document order. PubMed emits one
    /// `<Grant>` per grant number, so a single agency funding four grants
    /// appears four times in the XML and once here.
    pub funders: Vec<String>,
    /// Repository names from `<DataBankList>` that carried at least one
    /// non-blank accession, in PubMed's own spelling and document order,
    /// deduplicated case-insensitively. Names rather than a level: this type
    /// reports what the record said, and [`merge_pubmed_signals`] decides what
    /// it is worth.
    pub deposition_databanks: Vec<String>,
}

/// What one full-text fetch produced, and what became of it.
///
/// Two fields because "no full text" was several different claims collapsed onto
/// one `None` — the whole of issue #161 — and the caller needs the reason to
/// choose an honest indicator and to store it.
///
/// `text` is `Some` if and only if `status` is [`FullTextStatus::Analyzed`]. That
/// is not enforced here: this type never leaves the module and has one producer,
/// where [`TransparencyResult`] is constructed by downstream projects and
/// enforces the matching rule in [`TransparencyResult::validate`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullTextFetch {
    /// The article's own markup, when one was served and segmented.
    pub text: Option<String>,
    /// What became of the attempt.
    pub status: FullTextStatus,
}

/// Say what a parsed `efetch` body carrying no `PubmedArticle` was.
///
/// **One branch was three populations, and they do not share a level** (issue
/// #218). Until this existed the branch returned empty signals in silence, while
/// both of its neighbours reported — a body that will not parse WARNs above, an
/// empty 200 body WARNs in the caller — and it was the *majority* outcome of the
/// draw that finally sized it: 50 of 60 served bodies on 2026-09-09.
///
/// Three branches:
///
/// * A set whose records are **all books or book chapters**: bmlib declines them
///   by name, and 0 of 60 `statpearls[book]` records and 0 of 100 drawn from
///   `pubmed books[filter]` carry a `<GrantList>`, `<CoiStatement>` or
///   `<DataBankList>`. Nothing was lost that could have been had. DEBUG.
///   **Every child, and children rather than descendants**: the draw is of
///   responses that *are* book records, so a mixed set is outside it and takes
///   the branch below.
/// * An **empty `PubmedArticleSet`** at HTTP 200. NCBI holds no record for an
///   identifier that came from the caller or from [`pmid_from_epmc`], so
///   something upstream is wrong and the signals are lost for a record that
///   would have had them. WARNING.
/// * **Anything else** that parses: bmlib does not recognise what it was served.
///   WARNING.
///
/// **The issue's own third population belongs to a request this module does not
/// make.** It named `<eFetchResult><ERROR>…` at HTTP 200, which is real: an
/// evicted history-session `efetch` serves exactly that, which is why the
/// publications fetcher refuses a root that is not a record set. This module
/// fetches **by id**, and the same probe read 400 for a malformed id list and an
/// empty record set for an id NCBI does not hold.
///
/// `pmid` is carried so a line can be attributed to one analysis, the `subject`
/// every request in this module carries.
pub fn report_pubmed_without_citation(root: &Node<'_, '_>, pmid: &str) {
    let book_records = root
        .children()
        .filter(|child| child.is_element() && child.tag_name().name() == PUBMED_BOOK_RECORD)
        .count();
    let all_children = root.children().filter(Node::is_element).count();
    if book_records > 0 && book_records == all_children {
        // The test is as narrow as the evidence. `PubmedArticleSet` is declared
        // `(PubmedArticle | PubmedBookArticle)*`, so a mixed set is legal, and a
        // descendant search matched one anywhere — tested first, so a
        // `<PubmedArticle>` that carries no `<MedlineCitation>` lost its signals
        // at the quiet level on the strength of a book neighbour.
        log_line(
            Level::Debug,
            &format!(
                "PubMed for {pmid}: the record is a book or book chapter, which carries none \
                 of the elements this step reads; {PUBMED_SIGNALS_LOST}"
            ),
        );
    } else if root.tag_name().name() == PUBMED_RECORD_SET_ROOT && all_children == 0 {
        // Printing the constant rather than `root.tag_name().name()` is an
        // equivalent mutant, not an untested choice: this arm is reached only
        // when the two are the same string. The constant is here because the
        // sentence is a claim about what bmlib expected, and because the *test*
        // above must not drift from the message beside it.
        log_line(
            Level::Warning,
            &format!(
                "PubMed for {pmid}: answered 200 with an empty {PUBMED_RECORD_SET_ROOT} — \
                 NCBI holds no record for this PMID; {PUBMED_SIGNALS_LOST}"
            ),
        );
    } else {
        log_line(
            Level::Warning,
            &format!(
                "PubMed for {pmid}: answered 200 with a <{}> document carrying no \
                 PubmedArticle; {PUBMED_SIGNALS_LOST}",
                root.tag_name().name()
            ),
        );
    }
}

/// The first direct child element with the given name.
fn child_element<'a, 'input>(node: &Node<'a, 'input>, name: &str) -> Option<Node<'a, 'input>> {
    node.children()
        .find(|child| child.is_element() && child.tag_name().name() == name)
}

/// Every element matching an ElementTree `findall` path, in document order.
fn collect_path<'a, 'input>(roots: &[Node<'a, 'input>], path: &[&str]) -> Vec<Node<'a, 'input>> {
    let Some((head, rest)) = path.split_first() else {
        return roots.to_vec();
    };
    let mut next = Vec::new();
    for root in roots {
        for child in root.children() {
            if child.is_element() && child.tag_name().name() == *head {
                next.push(child);
            }
        }
    }
    collect_path(&next, rest)
}

/// ElementTree's `itertext()`: every text node of the subtree in document order.
fn iter_text(node: Node<'_, '_>) -> String {
    let mut out = String::new();
    for child in node.children() {
        if child.is_text() {
            if let Some(text) = child.text() {
                out.push_str(text);
            }
        } else if child.is_element() {
            out.push_str(&iter_text(child));
        }
    }
    out
}

/// Extract transparency signals from a PubMed `efetch` response.
///
/// Returns empty signals for anything unusable — malformed XML, an empty result
/// set, a record without the relevant elements — so a surprising response
/// degrades the analysis rather than raising into it. **Every one of those
/// outcomes leaves a line**, which is the whole of issue #218.
///
/// The XML parser here is `roxmltree` where Python uses `ElementTree`; both
/// reject a malformed document and this reports the rejection rather than
/// propagating it. Elements are matched by local name, which is what
/// `ElementTree` does with an unprefixed PubMed document and is a divergence
/// only for a prefixed one, which NCBI does not serve.
#[must_use]
pub fn parse_pubmed_signals(xml_text: &str, pmid: &str) -> PubMedSignals {
    // `allow_dtd: true` because `ElementTree`'s expat reads a DOCTYPE and would
    // otherwise accept a body this refuses; the same option and the same reason
    // as `jats_reader`'s. Neither parser expands an internal entity the other
    // would, which is a divergence this module has no measured input for.
    let document = match Document::parse_with_options(
        xml_text,
        roxmltree::ParsingOptions {
            allow_dtd: true,
            ..roxmltree::ParsingOptions::default()
        },
    ) {
        Ok(document) => document,
        Err(error) => {
            // WARNING, not DEBUG: the request succeeded and the *body* is what is
            // wrong, so DEBUG names the wrong stage — and holding it there left
            // PubMed the one endpoint of five whose unusable 200 was invisible by
            // default (PR #195's review).
            log_line(
                Level::Warning,
                &format!(
                    "PubMed for {pmid}: answered 200 with a body that is not parsable XML: {error}"
                ),
            );
            return PubMedSignals::default();
        }
    };
    let root = document.root_element();

    // `.//PubmedArticle/MedlineCitation` is the first *pair* in document order,
    // not the MedlineCitation of the first PubmedArticle — so a malformed head
    // record does not hide a later good one.
    let citation = root
        .descendants()
        .skip(1)
        .filter(|node| node.is_element() && node.tag_name().name() == "PubmedArticle")
        .find_map(|article| child_element(&article, "MedlineCitation"));
    let Some(citation) = citation else {
        report_pubmed_without_citation(&root, pmid);
        return PubMedSignals::default();
    };

    // The MEDLINE DTD declares CoiStatement as (%text;)*, so inline markup
    // (<b>, <i>, <sup>, …) is legal inside it. Reading `.text` alone would miss
    // a statement that opens with a tag — "<b>Conflict of interest:</b> none" —
    // and report a disclosure as absent.
    let coi_statement = child_element(&citation, "CoiStatement")
        .is_some_and(|element| !iter_text(element).trim().is_empty());

    let mut accessions: Vec<String> = Vec::new();
    let mut registration_not_checkable = false;
    // Keyed by the lowercased name so a record naming one repository twice — or
    // once as "GENBANK" and once as "GenBank" — yields one entry. The value is
    // the first spelling seen, because it is rendered to humans.
    let mut deposition: Vec<(String, String)> = Vec::new();

    for databank in collect_path(&[citation], &["Article", "DataBankList", "DataBank"]) {
        let raw_name = child_element(&databank, "DataBankName")
            .and_then(|node| node.text())
            .unwrap_or("")
            .trim()
            .to_string();
        let name = raw_name.to_lowercase();
        let accession_numbers =
            collect_path(&[databank], &["AccessionNumberList", "AccessionNumber"]);

        if let Some(level) = deposition_databank_level(&name) {
            let _ = level;
            // A repository name with no accession is an assertion with no
            // referent — nothing a reader could go and fetch — so it is not the
            // structured proof of a deposit this signal claims to be.
            let has_accession = accession_numbers
                .iter()
                .any(|element| !element.text().unwrap_or("").trim().is_empty());
            if has_accession && !deposition.iter().any(|(key, _)| *key == name) {
                deposition.push((name.clone(), raw_name));
            }
            continue;
        }

        if !TRIAL_REGISTRY_NAMES.contains(&name.as_str()) {
            continue;
        }
        // Every accession is publisher-supplied text that would be interpolated
        // into a ClinicalTrials.gov URL path, so only a well-formed NCT id is
        // ever carried forward. A ClinicalTrials.gov entry whose accession is
        // missing or malformed still establishes registration — it just cannot
        // be followed up, which is what `registration_not_checkable` records.
        let usable: Vec<String> = accession_numbers
            .iter()
            .map(|element| element.text().unwrap_or("").trim().to_uppercase())
            .filter(|accession| NCT_ID_FULL_RE.is_match(accession))
            .collect();
        if name == CLINICALTRIALS_GOV && !usable.is_empty() {
            accessions.extend(usable);
        } else {
            if name == CLINICALTRIALS_GOV {
                // Not the same story as a registration in another registry, and
                // the only place the difference is visible — the result records
                // followability, not which of the two caused it.
                log_line(
                    Level::Debug,
                    &format!(
                        "PubMed for {pmid}: ClinicalTrials.gov databank carried no usable accession"
                    ),
                );
            }
            registration_not_checkable = true;
        }
    }

    // Deduplicated: PubMed emits one <Grant> per grant number, so an agency
    // funding several grants on one paper would otherwise repeat — and each
    // repeat would add its own "Industry funder: …" line to the result.
    let mut funders: Vec<String> = Vec::new();
    for agency in collect_path(&[citation], &["Article", "GrantList", "Grant", "Agency"]) {
        let name = agency.text().unwrap_or("").trim();
        if !name.is_empty() && !funders.iter().any(|existing| existing == name) {
            funders.push(name.to_string());
        }
    }

    // **Deduplicated for the reason `funders` is, and since issue #206 for a
    // sharper one.** MEDLINE's `<DataBankList>` is `(DataBank+)` and each
    // `<DataBank>` carries its own `<AccessionNumberList>`, so one paper naming
    // one trial twice is well-formed input. While `answered` was a `bool` and
    // the cap was silent, a repeat cost only a redundant request. It no longer
    // is: `len(ct_ids)` is the denominator of a WARNING and `dropped` decides
    // `PARTLY_ANSWERED`, so four entries naming one trial would report *"1 of
    // this paper's 4 accessions were not checked"* and retract a `NOT_POSTED`
    // that ClinicalTrials.gov had answered for every distinct trial the paper
    // named. Order-preserving, because the accession order is the paper's and
    // the cap slices by it.
    let mut trial_accessions: Vec<String> = Vec::new();
    for accession in accessions {
        if !trial_accessions.contains(&accession) {
            trial_accessions.push(accession);
        }
    }

    PubMedSignals {
        coi_statement,
        trial_accessions,
        registration_not_checkable,
        funders,
        deposition_databanks: deposition.into_iter().map(|(_, raw)| raw).collect(),
    }
}

/// Fold PubMed's structured signals into `analysis`.
///
/// A free function rather than a method because it needs no HTTP client; trial
/// registration is handled separately, in the trial step, because that one does.
///
/// Each score component is awarded at most once. `coi_disclosed != Some(true)` is
/// a reliable guard rather than an incidental one: the only branch that sets
/// `Some(true)` is the same branch that adds [`SCORE_COI_DISCLOSED`].
///
/// `<DataBankList>` deposition accessions nominate the data-availability level
/// [`DEPOSITION_DATABANK_LEVELS`] maps their repository to, through
/// [`Analysis::note_data_level`], so the strongest evidence wins whichever
/// source ran first. The component itself is scored later, by
/// [`score_data_availability`].
pub fn merge_pubmed_signals(pubmed: &PubMedSignals, analysis: &mut Analysis) {
    if pubmed.coi_statement && analysis.coi_disclosed != Some(true) {
        analysis.coi_disclosed = Some(true);
        analysis.score += SCORE_COI_DISCLOSED;
        // Every one of those lines was written before PubMed was consulted and
        // would now contradict the result, so they are retracted rather than
        // left to be reconciled by whoever reads the indicators. Read from
        // `INDICATORS_RETRACTED_BY_PUBMED_COI` rather than enumerated here: this
        // site is where the third one went missing on the Python side.
        analysis
            .indicators
            .retain(|line| !INDICATORS_RETRACTED_BY_PUBMED_COI.contains(&line.as_str()));
        analysis
            .indicators
            .push(INDICATOR_COI_IN_PUBMED.to_string());
    }

    // A missing <CoiStatement> deliberately does not demote `None` to
    // `Some(false)`: it means the publisher supplied no statement to PubMed, not
    // that the paper carries none, and `Some(false)` would trigger the
    // missing-COI downgrade on no evidence.

    if !pubmed.funders.is_empty() {
        analysis.award_funder_info();
        for agency in &pubmed.funders {
            if is_industry_funder(agency) {
                // A grant agency is structured metadata, the same class of
                // evidence as a CrossRef funder record — not the weaker signal
                // inferred from COI prose. `note_industry_funder` deduplicates.
                analysis.note_industry_funder(agency);
            }
        }
    }

    if !pubmed.deposition_databanks.is_empty() {
        for name in &pubmed.deposition_databanks {
            // The parser collected the name; deciding what a deposit into it is
            // worth is this step's job, which is why the signals carry names
            // rather than a level. Subscripted rather than defaulted: the parser
            // admits a name only if it is a key here, so a name that is not one
            // is a bug in this module and panics, the same way
            // `note_data_level()` panics on a level outside the ranking.
            let level = deposition_databank_level(&name.to_lowercase())
                .unwrap_or_else(|| panic!("deposition databank {name:?} has no level"));
            analysis.note_data_level(level);
        }
        // Written whether or not the level above won: it reports what PubMed
        // said, which stays true either way.
        analysis.indicators.push(format!(
            "{INDICATOR_DATA_DEPOSITED_PREFIX}{}",
            pubmed.deposition_databanks.join(", ")
        ));
    }
}

/// Record what became of the full text, as prose that cannot be retracted.
///
/// A free function for [`merge_pubmed_signals`]'s reason — it needs no HTTP
/// client — and called from `analyze()` **after every step has run**, which is
/// the whole design rather than an ordering detail. The information used to be a
/// parenthetical inside a COI line, which
/// [`INDICATORS_RETRACTED_BY_PUBMED_COI`] removes wholesale when PubMed supplies
/// a `<CoiStatement>`; appending here puts it structurally beyond that
/// retraction, where keeping it out of the set would leave the rule enforced by
/// set membership — and membership is exactly what went wrong twice already
/// (issues #161 and #193).
///
/// Silent for [`FullTextStatus::Analyzed`], the one member with nothing to
/// explain. Every other member has a line; see
/// [`full_text_provenance_indicator`], whose exhaustive `match` makes the
/// partition a compile-time rule rather than the runtime `KeyError` Python
/// guards with.
pub fn note_full_text_provenance(analysis: &mut Analysis) {
    if let Some(line) = full_text_provenance_indicator(analysis.full_text_status) {
        analysis.indicators.push(line.to_string());
    }
}

/// Award the data-availability component once, for the level that won.
///
/// Called by `analyze()` after every sub-step has nominated, rather than by the
/// step that finds a level. With two producers — Europe PMC's text scan and
/// PubMed's deposition accessions — scoring at the point of discovery would
/// either spend the component twice or spend it on a level later beaten. The
/// sub-steps only ever call [`Analysis::note_data_level`], which nominates and
/// cannot add points, so neither is capable of scoring this component at all.
///
/// Unlike [`Analysis::award_funder_info`], this carries no "already spent" flag:
/// what holds it to one award is that `analyze()` calls it from exactly one
/// place, so a re-score or retry path added there would have to bring its own
/// guard.
///
/// Deferring is also what keeps [`INDICATOR_DATA_NOT_AVAILABLE`] honest: the
/// line is written only if that level survived the merge, so it never has to be
/// retracted the way the PubMed COI lines are.
pub fn score_data_availability(analysis: &mut Analysis) {
    match analysis.data_level.as_str() {
        "full_open" => analysis.score += SCORE_DATA_FULL_OPEN,
        "on_request" => analysis.score += SCORE_DATA_ON_REQUEST,
        "not_available" => analysis
            .indicators
            .push(INDICATOR_DATA_NOT_AVAILABLE.to_string()),
        _ => {}
    }
}

// ---------------------------------------------------------------------------
// The result
// ---------------------------------------------------------------------------

/// The version string a result carries.
pub const ANALYZER_VERSION: &str = "1.0";

/// How long the transport should wait on one request.
///
/// The Python module hands this to `httpx.Client`. [`HttpClient`] owns its own
/// timeout here, so the constant records the value a transport should use rather
/// than a value this module can enforce.
pub const HTTP_TIMEOUT_SECONDS: f64 = 15.0;

/// Minimum interval between outgoing HTTP requests.
pub const MIN_REQUEST_INTERVAL_SECONDS: f64 = 0.35;

/// Result of a transparency analysis for a single document.
///
/// **`coi_disclosed` has no determinate default.** Python's dataclass defaults it
/// to `True`, which is what made every `UNKNOWN` path store a determinate claim
/// about a run that measured nothing (issue #306) — the sibling statuses are set
/// explicitly *because* a default would be that claim. Here the default is
/// `None`, and every path that knows something sets it.
///
/// The three trailing fields are `Option` for the reason the Python model gives:
/// `None` means *not recorded*, never the enum's "nothing happened" member. A
/// result persisted before the field existed may perfectly well carry
/// `full_text_analyzed = true` or `trial_results_compliant = true`, and reading
/// that back as a determinate "nothing was attempted" would be a worse answer
/// than admitting the field was not written. The Python type enforces the
/// matching pair of rules in `__post_init__`; [`TransparencyResult::validate`] is
/// the counterpart here.
#[derive(Debug, Clone, PartialEq)]
pub struct TransparencyResult {
    /// The caller's own identifier for this document.
    pub document_id: String,
    /// The transparency score, capped at [`MAX_TRANSPARENCY_SCORE`].
    pub transparency_score: i64,
    /// How risky the paper looks.
    pub risk_level: TransparencyRisk,
    /// Any industry involvement was detected.
    pub industry_funding_detected: bool,
    /// Confidence in that detection.
    pub industry_funding_confidence: f64,
    /// Data-availability level, one the analyzer produces.
    pub data_availability_level: String,
    /// A COI statement was found (`Some(true)`), full text was scanned and none
    /// exists (`Some(false)`), or the status could not be determined (`None`).
    ///
    /// Only an explicit `Some(false)` triggers the missing-COI downgrade.
    pub coi_disclosed: Option<bool>,
    /// A trial registration was established.
    pub trial_registered: bool,
    /// Posted results were found for a registered trial.
    ///
    /// The compatibility field a downstream already renders; branch on
    /// [`TransparencyResult::trial_results_status`] instead.
    pub trial_results_compliant: bool,
    /// Reserved: no detection is implemented, so this is always `false`.
    ///
    /// Deciding it would mean comparing a trial's pre-registered primary
    /// outcomes against those actually reported. Kept so persisted results do
    /// not need migrating when it lands.
    pub outcome_switching_detected: bool,
    /// Human-readable findings, in the order they were made.
    pub risk_indicators: Vec<String>,
    /// How many tiers a HIGH verdict costs.
    pub tier_downgrade_applied: i64,
    /// When the analysis ran.
    pub analyzed_at: DateTime<Utc>,
    /// The analyzer version, mirroring Python's `"1.0"`.
    pub analyzer_version: String,
    /// Findings came from full text, not just an abstract.
    ///
    /// Only when this is `true` does `coi_disclosed = Some(false)` mean "scanned
    /// and absent" rather than "undeterminable".
    pub full_text_analyzed: bool,
    /// Why the result is [`TransparencyRisk::Unknown`].
    ///
    /// Set if and only if the level is `UNKNOWN`; see
    /// [`TransparencyResult::validate`].
    pub unknown_reason: Option<TransparencyUnknownReason>,
    /// What became of the full-text attempt.
    pub full_text_status: Option<FullTextStatus>,
    /// What became of the posted-results check.
    pub trial_results_status: Option<TrialResultsStatus>,
}

impl TransparencyResult {
    /// A result with the two required fields and every optional field at its
    /// *not recorded* value.
    ///
    /// `coi_disclosed` is `None` here — see the type's own documentation for why
    /// that is the correction and not the Python default.
    #[must_use]
    pub fn new(
        document_id: impl Into<String>,
        transparency_score: i64,
        risk_level: TransparencyRisk,
    ) -> Self {
        TransparencyResult {
            document_id: document_id.into(),
            transparency_score,
            risk_level,
            industry_funding_detected: false,
            industry_funding_confidence: 0.0,
            data_availability_level: "unknown".to_string(),
            coi_disclosed: None,
            trial_registered: false,
            trial_results_compliant: false,
            outcome_switching_detected: false,
            risk_indicators: Vec::new(),
            tier_downgrade_applied: 0,
            analyzed_at: Utc::now(),
            analyzer_version: ANALYZER_VERSION.to_string(),
            full_text_analyzed: false,
            unknown_reason: None,
            full_text_status: None,
            trial_results_status: None,
        }
    }

    /// Reject a reason on a result that is not `UNKNOWN`, and a status that
    /// contradicts the flag beside it.
    ///
    /// Python's `__post_init__`, which raises `ValueError`; this returns the
    /// message a caller would have seen. Only one direction of each rule is
    /// enforced: the converse — every `UNKNOWN` carries a reason — holds for
    /// anything `analyze()` produces, but results persisted before the field
    /// existed load with `None`, and refusing those would make the field a
    /// breaking change rather than an additive one.
    ///
    /// # Errors
    ///
    /// The contradiction, worded as Python words it.
    pub fn validate(&self) -> Result<(), String> {
        if let Some(reason) = self.unknown_reason {
            if self.risk_level != TransparencyRisk::Unknown {
                return Err(format!(
                    "unknown_reason={:?} is meaningless on a {:?} result; it is set only when \
                     risk_level is UNKNOWN",
                    reason.as_str(),
                    self.risk_level.as_str()
                ));
            }
        }
        if let Some(status) = self.full_text_status {
            if (status == FullTextStatus::Analyzed) != self.full_text_analyzed {
                return Err(format!(
                    "full_text_status={:?} contradicts full_text_analyzed={}; the flag is set if \
                     and only if the status is {:?}",
                    status.as_str(),
                    self.full_text_analyzed,
                    FullTextStatus::Analyzed.as_str()
                ));
            }
        }
        if let Some(status) = self.trial_results_status {
            if (status == TrialResultsStatus::Posted) != self.trial_results_compliant {
                return Err(format!(
                    "trial_results_status={:?} contradicts trial_results_compliant={}; the flag is \
                     set if and only if the status is {:?}",
                    status.as_str(),
                    self.trial_results_compliant,
                    TrialResultsStatus::Posted.as_str()
                ));
            }
        }
        Ok(())
    }

    /// Serialise to a JSON-safe object, in the Python's own field order and
    /// spellings.
    ///
    /// **The three enum fields are `null` when unrecorded**, never the enum's
    /// "nothing happened" member: `None` means *not recorded*, and writing
    /// `"not_attempted"` for it would turn an absent field into a claim that a
    /// request was made. The Python guards each with a truthiness test on the
    /// member's *value*, which is why the spelling matters — every member's value
    /// is a non-empty string, so the test is really "is it `None`".
    ///
    /// `analyzed_at` is written as an ISO-8601 instant. Python writes
    /// `datetime.isoformat()` (`...+00:00`) and this writes RFC 3339 (`...Z`); the
    /// two are the same instant and each parser accepts the other's spelling, so a
    /// row written by either round-trips through both. The **string** differs,
    /// which is recorded in the plan's §9 rather than papered over.
    #[must_use]
    pub fn to_dict(&self) -> serde_json::Value {
        use serde_json::json;
        json!({
            "document_id": self.document_id,
            "transparency_score": self.transparency_score,
            "risk_level": self.risk_level.as_str(),
            "industry_funding_detected": self.industry_funding_detected,
            "industry_funding_confidence": self.industry_funding_confidence,
            "data_availability_level": self.data_availability_level,
            "coi_disclosed": self.coi_disclosed,
            "trial_registered": self.trial_registered,
            "trial_results_compliant": self.trial_results_compliant,
            "outcome_switching_detected": self.outcome_switching_detected,
            "risk_indicators": self.risk_indicators,
            "tier_downgrade_applied": self.tier_downgrade_applied,
            "analyzed_at": self.analyzed_at.to_rfc3339(),
            "analyzer_version": self.analyzer_version,
            "full_text_analyzed": self.full_text_analyzed,
            "unknown_reason": self.unknown_reason.map(|r| r.as_str()),
            "full_text_status": self.full_text_status.map(|s| s.as_str()),
            "trial_results_status": self.trial_results_status.map(|s| s.as_str()),
        })
    }

    /// Deserialise from an object produced by [`TransparencyResult::to_dict`].
    ///
    /// # Errors
    ///
    /// A missing required field (`document_id`, `transparency_score`,
    /// `risk_level`), a field of the wrong JSON type, or an unrecognised enum
    /// spelling. **The Python raises for the first two and for the third**, so this
    /// is the same contract; what differs is that a `Result` says so at the type
    /// level rather than at the call.
    ///
    /// Every optional field takes the Python's own default, spelled at the point it
    /// applies rather than derived — and `coi_disclosed` takes **`None`**, not the
    /// Python's `True`. That is #306's correction: the source's dataclass default
    /// `True` asserts *"a COI statement was found"* for a row that recorded
    /// nothing, which is the false claim the issue is about. A row this method
    /// reads back therefore reports *not recorded* where the Python would report a
    /// disclosure, and a caller that needs the old reading passes it explicitly.
    pub fn from_dict(data: &serde_json::Value) -> Result<Self, String> {
        let required_string = |key: &str| -> Result<String, String> {
            data.get(key)
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
                .ok_or_else(|| format!("{key} is required and must be a string"))
        };
        let optional_string = |key: &str| -> Option<String> {
            data.get(key)
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
        };

        let document_id = required_string("document_id")?;
        let transparency_score = data
            .get("transparency_score")
            .and_then(serde_json::Value::as_i64)
            .ok_or_else(|| "transparency_score is required and must be an integer".to_string())?;
        let risk_level = required_string("risk_level").and_then(|raw| {
            serde_json::from_value::<TransparencyRisk>(serde_json::Value::String(raw.clone()))
                .map_err(|_| format!("unrecognised risk_level: {raw:?}"))
        })?;

        // `fromisoformat` on the Python side, which accepts both the `+00:00` it
        // writes and the `Z` this writes. An absent timestamp is *now*.
        let analyzed_at = match optional_string("analyzed_at") {
            Some(raw) => chrono::DateTime::parse_from_rfc3339(&raw)
                .map(|parsed| parsed.with_timezone(&chrono::Utc))
                .map_err(|e| format!("analyzed_at is not an ISO-8601 instant: {e}"))?,
            None => chrono::Utc::now(),
        };

        let unknown_reason = match optional_string("unknown_reason") {
            Some(raw) => Some(
                serde_json::from_value::<TransparencyUnknownReason>(serde_json::Value::String(
                    raw.clone(),
                ))
                .map_err(|_| format!("unrecognised unknown_reason: {raw:?}"))?,
            ),
            None => None,
        };
        let full_text_status = match optional_string("full_text_status") {
            Some(raw) => Some(
                serde_json::from_value::<FullTextStatus>(serde_json::Value::String(raw.clone()))
                    .map_err(|_| format!("unrecognised full_text_status: {raw:?}"))?,
            ),
            None => None,
        };
        let trial_results_status = match optional_string("trial_results_status") {
            Some(raw) => Some(
                serde_json::from_value::<TrialResultsStatus>(serde_json::Value::String(
                    raw.clone(),
                ))
                .map_err(|_| format!("unrecognised trial_results_status: {raw:?}"))?,
            ),
            None => None,
        };

        let risk_indicators = match data.get("risk_indicators") {
            None | Some(serde_json::Value::Null) => Vec::new(),
            Some(serde_json::Value::Array(items)) => items
                .iter()
                .map(|item| {
                    item.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| "risk_indicators must be an array of strings".to_string())
                })
                .collect::<Result<Vec<_>, _>>()?,
            Some(_) => return Err("risk_indicators must be an array of strings".to_string()),
        };

        Ok(TransparencyResult {
            document_id,
            transparency_score,
            risk_level,
            industry_funding_detected: data
                .get("industry_funding_detected")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            industry_funding_confidence: data
                .get("industry_funding_confidence")
                .and_then(serde_json::Value::as_f64)
                .unwrap_or(0.0),
            data_availability_level: optional_string("data_availability_level")
                .unwrap_or_else(|| "unknown".to_string()),
            // **`None`, not the Python's `True`** — #306's correction.
            coi_disclosed: data
                .get("coi_disclosed")
                .and_then(serde_json::Value::as_bool),
            trial_registered: data
                .get("trial_registered")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            trial_results_compliant: data
                .get("trial_results_compliant")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            outcome_switching_detected: data
                .get("outcome_switching_detected")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            risk_indicators,
            tier_downgrade_applied: data
                .get("tier_downgrade_applied")
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(0),
            analyzed_at,
            analyzer_version: optional_string("analyzer_version")
                .unwrap_or_else(|| "1.0".to_string()),
            full_text_analyzed: data
                .get("full_text_analyzed")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            unknown_reason,
            full_text_status,
            trial_results_status,
        })
    }
}

// ---------------------------------------------------------------------------
// Query strings
// ---------------------------------------------------------------------------

/// Percent-encode one query key or value (RFC 3986 unreserved characters).
///
/// [`HttpClient::get`] takes a whole URL, so the parameters Python hands to
/// `httpx` have to be interpolated here. The choice of `%20` over `+` for a
/// space is a port decision: nothing compares this against a live client, and
/// both are accepted by the endpoints this module calls.
#[must_use]
pub fn percent_encode(raw: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut out = String::with_capacity(raw.len());
    for byte in raw.as_bytes() {
        let c = *byte as char;
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~') {
            out.push(c);
        } else {
            out.push('%');
            out.push(HEX[(byte >> 4) as usize] as char);
            out.push(HEX[(byte & 0x0f) as usize] as char);
        }
    }
    out
}

/// Render query parameters in the order given.
#[must_use]
pub fn encode_query(params: &[(&str, &str)]) -> String {
    params
        .iter()
        .map(|(key, value)| format!("{}={}", percent_encode(key), percent_encode(value)))
        .collect::<Vec<_>>()
        .join("&")
}

// ---------------------------------------------------------------------------
// The analyzer
// ---------------------------------------------------------------------------

/// Python's `%r` on a value that may be absent, for a log line.
fn py_repr(value: Option<&str>) -> String {
    match value {
        None => "None".to_string(),
        Some(text) => format!("'{text}'"),
    }
}

/// The per-request shape [`TransparencyAnalyzer::request`] needs.
///
/// Python's version of this helper takes three keyword arguments and a
/// `frozenset`; grouping them is what keeps the argument count down and mirrors
/// the keyword-only call sites.
struct RequestSpec<'a> {
    url: &'a str,
    api: &'a str,
    subject: &'a str,
    quiet_statuses: &'a [u16],
}

/// Analyze transparency of a biomedical publication via external APIs.
///
/// # Concurrency
///
/// The rate limiter's state is shared behind a [`Mutex`], because the interval
/// throttles a *remote API* and so is enforced across all threads using one
/// analyzer. Reachability, by contrast, describes a single analysis and lives on
/// the per-call [`Analysis`], so concurrent `analyze()` calls cannot contaminate
/// each other.
pub struct TransparencyAnalyzer {
    /// Contact email for API politeness parameters.
    pub email: String,
    /// Optional NCBI API key.
    ///
    /// Sent with the PubMed `efetch` request, which moves it out of NCBI's 3
    /// requests/second per-IP bucket and into the key's 10 requests/second one —
    /// so bmlib's traffic stops competing with the calling application's own
    /// E-utilities requests. It does not change bmlib's own pacing.
    pub pubmed_api_key: Option<String>,
    /// Thresholds and orchestration hints.
    pub settings: TransparencySettings,
    /// Minimum interval this analyzer enforces between its own requests.
    ///
    /// Defaults to [`MIN_REQUEST_INTERVAL_SECONDS`]. Exposed because the Python
    /// interval is a module constant a test cannot reach, and a suite that
    /// exercises end-to-end paths pays 0.35 s per request; a test sets this to
    /// [`Duration::ZERO`] and the behaviour under test is unchanged.
    pub min_request_interval: Duration,
    last_request: Mutex<Option<Instant>>,
}

impl TransparencyAnalyzer {
    /// A new analyzer.
    #[must_use]
    pub fn new(
        email: impl Into<String>,
        pubmed_api_key: Option<String>,
        settings: TransparencySettings,
    ) -> Self {
        TransparencyAnalyzer {
            email: email.into(),
            pubmed_api_key,
            settings,
            min_request_interval: Duration::from_secs_f64(MIN_REQUEST_INTERVAL_SECONDS),
            last_request: Mutex::new(None),
        }
    }

    /// Run transparency analysis for a single document.
    ///
    /// At least one of `pmid` and `doi` must be given — and, as in Python, an
    /// *empty* one counts as absent, because `if not pmid` is what the original
    /// tests.
    ///
    /// Returns an `UNKNOWN` result when `settings.enabled` is false, when
    /// neither identifier is given, or when every external API was unreachable —
    /// three distinct cases, each named in `risk_indicators` for humans and in
    /// [`TransparencyResult::unknown_reason`] for callers that branch on the
    /// cause. The first two cases contact no API at all.
    ///
    /// `unknown_reason` is set if and only if `risk_level` is `UNKNOWN`:
    /// [`calculate_risk_level`] never returns `UNKNOWN`, so every `UNKNOWN`
    /// originates in one of the three early returns below. `UNKNOWN` never
    /// triggers a quality tier downgrade, so a paper we learned nothing about is
    /// not penalised.
    ///
    /// # Panics
    ///
    /// Never on remote data: each step swallows its own request failure and each
    /// JSON read is type-checked before use. The one deliberate panic in this
    /// module is [`Analysis::note_data_level`] on a level the analyzer itself
    /// does not produce, which can only mean a bmlib defect.
    #[must_use]
    pub fn analyze(
        &self,
        client: &dyn HttpClient,
        document_id: &str,
        pmid: Option<&str>,
        doi: Option<&str>,
    ) -> TransparencyResult {
        let pmid = pmid.filter(|value| !value.is_empty());
        let doi = doi.filter(|value| !value.is_empty());

        // Checked before any request is made: a disabled analyzer does no HTTP.
        // Its statuses are recorded rather than left `None`, because `None` means
        // *this result predates the field* and a version that leaves it unset on
        // any path makes a current row indistinguishable from a legacy one.
        if !self.settings.enabled {
            return TransparencyResult {
                risk_indicators: vec!["Transparency analysis disabled in settings".to_string()],
                unknown_reason: Some(TransparencyUnknownReason::Disabled),
                full_text_status: Some(FullTextStatus::NotAttempted),
                trial_results_status: Some(TrialResultsStatus::NotRegistered),
                // #306: no request was made, so nothing measured the COI status.
                // `None`, never Python's dataclass default of a determinate
                // `true`.
                coi_disclosed: None,
                ..TransparencyResult::new(document_id, 0, TransparencyRisk::Unknown)
            };
        }

        if pmid.is_none() && doi.is_none() {
            return TransparencyResult {
                risk_indicators: vec!["No PMID or DOI provided".to_string()],
                unknown_reason: Some(TransparencyUnknownReason::NoIdentifier),
                full_text_status: Some(FullTextStatus::NotAttempted),
                trial_results_status: Some(TrialResultsStatus::NotRegistered),
                // Likewise: no identifier, so no request was made (#306).
                coi_disclosed: None,
                ..TransparencyResult::new(document_id, 0, TransparencyRisk::Unknown)
            };
        }

        let mut analysis = Analysis::default();

        // --- CrossRef (funder info) ---
        if let Some(doi) = doi {
            self.check_crossref(client, &mut analysis, doi);
        }

        // --- Europe PMC (full text / abstract, COI, data availability) ---
        let epmc = self.fetch_europepmc(client, &mut analysis, pmid, doi);
        match &epmc {
            None => {
                // The search produced no answer, so the whole full-text step is
                // skipped — and it used to be skipped in silence, storing
                // `NOT_ATTEMPTED`, whose documented meaning is that Europe PMC's
                // own answer is the reason (issue #193).
                //
                // `None` and not emptiness: a 200 carrying an empty object is
                // Europe PMC answering, and answering with no record for this
                // identifier is exactly what `NOT_ATTEMPTED` is for.
                analysis.full_text_status = FullTextStatus::SearchFailed;
                analysis.indicators.push(INDICATOR_COI_UNKNOWN.to_string());
                let subject = if !document_id.is_empty() {
                    document_id
                } else if let Some(pmid) = pmid {
                    pmid
                } else {
                    doi.unwrap_or("")
                };
                log_line(
                    Level::Warning,
                    &format!(
                        "EuropePMC search produced no answer for {subject}, so no full-text \
                         request was made; COI and data-availability findings are unavailable and \
                         up to {} points are not scored",
                        SCORE_COI_DISCLOSED + SCORE_DATA_FULL_OPEN
                    ),
                );
            }
            Some(value) => {
                if value.as_object().is_some_and(|map| !map.is_empty()) {
                    self.check_europepmc(client, value, &mut analysis, document_id);
                }
            }
        }

        // --- PubMed (structured COI, trial registration, grants) ---
        // Placed after Europe PMC so a DOI-only analysis can reuse the PMID from
        // the record already fetched, and before ClinicalTrials.gov so a
        // structured accession can feed the posted-results check.
        let derived_pmid = pmid_from_epmc(epmc.as_ref());
        let pubmed = self.check_pubmed(client, &mut analysis, pmid.or(derived_pmid.as_deref()));
        merge_pubmed_signals(&pubmed, &mut analysis);

        // --- OpenAlex (additional metadata) ---
        if let Some(doi) = doi {
            self.check_openalex(client, &mut analysis, doi);
        }

        // --- ClinicalTrials.gov (trial registration) ---
        if pmid.is_some() || doi.is_some() {
            self.check_trial_registration(client, &mut analysis, epmc.as_ref(), &pubmed);
        }

        // If not one external API responded, we measured nothing: report the
        // result as UNKNOWN rather than letting an all-zero score read as HIGH
        // risk, which would be indistinguishable from a genuinely opaque paper
        // and would wrongly trigger a quality-tier downgrade.
        if !analysis.api_reachable {
            return TransparencyResult {
                risk_indicators: vec![
                    "Transparency APIs unreachable — score not determinable".to_string()
                ],
                unknown_reason: Some(TransparencyUnknownReason::Unreachable),
                // Read from the carrier, never written as a literal: both
                // `NOT_ATTEMPTED` and `SEARCH_FAILED` are reachable here, and
                // `SEARCH_FAILED` is the *typical* one, a total outage being
                // precisely the case where the search produced no answer.
                full_text_status: Some(analysis.full_text_status),
                trial_results_status: Some(analysis.trial_results_status),
                // The carrier's own value, which cannot be `Some` on this path:
                // every route that sets it needs a 200 that would have marked
                // the analysis reachable. `None` is the honest answer either way,
                // and reading it keeps the correction structural (#306).
                coi_disclosed: analysis.coi_disclosed,
                ..TransparencyResult::new(document_id, 0, TransparencyRisk::Unknown)
            };
        }

        // After every step, and deliberately after `merge_pubmed_signals`: this
        // line says what became of the full text, which a PubMed `<CoiStatement>`
        // refutes no part of, so it must be out of reach of the retraction rather
        // than merely absent from its set (issue #203). Placed after the
        // UNREACHABLE return above because that result substitutes its own
        // indicator — an UNKNOWN verdict reports no findings at all, and
        // `full_text_status` carries the finer answer there.
        note_full_text_provenance(&mut analysis);

        // Awarded here rather than by the step that found the level: two sources
        // nominate one, and the component is worth its points once.
        score_data_availability(&mut analysis);

        analysis.score = analysis.score.min(MAX_TRANSPARENCY_SCORE);

        let risk_level = calculate_risk_level(
            analysis.score,
            analysis.industry_funding,
            &analysis.data_level,
            analysis.coi_disclosed,
            &self.settings,
        );

        let result = TransparencyResult {
            transparency_score: analysis.score,
            risk_level,
            industry_funding_detected: analysis.industry_funding,
            industry_funding_confidence: analysis.industry_confidence,
            data_availability_level: analysis.data_level,
            coi_disclosed: analysis.coi_disclosed,
            trial_registered: analysis.trial_registered,
            trial_results_compliant: analysis.results_compliant,
            trial_results_status: Some(analysis.trial_results_status),
            risk_indicators: analysis.indicators,
            full_text_analyzed: analysis.full_text_analyzed,
            full_text_status: Some(analysis.full_text_status),
            tier_downgrade_applied: if risk_level == TransparencyRisk::High {
                self.settings.tier_downgrade_amount
            } else {
                0
            },
            ..TransparencyResult::new(document_id, analysis.score, risk_level)
        };
        // Python's `__post_init__`, run in debug builds: the three pairs it
        // holds together are what make a stored `coi_disclosed = Some(false)` or
        // `trial_results_compliant` interpretable, and an inconsistency here
        // would be a defect in this module.
        debug_assert!(
            result.validate().is_ok(),
            "analyze built an inconsistent result: {:?}",
            result.validate()
        );
        result
    }

    // --- Analysis sub-steps ---

    /// Query CrossRef for funder information and fold it into `analysis`.
    ///
    /// [`SCORE_FUNDER_INFO`] is spent through [`Analysis::award_funder_info`], so
    /// it stays a once-per-analysis component however many funder sources run and
    /// in whatever order.
    ///
    /// **Corrected against issue #307.** Python reads the `funder` value out of
    /// `_json_object(cr.get("message"))` — an empty object for a `message` that
    /// is absent, `null`, a string, a number or an array — so a body with no
    /// readable `message` stores *"No funder information in CrossRef"*, a claim
    /// about the record where the truth is a claim about the exchange. That is
    /// the rule [`INDICATOR_FUNDERS_NOT_READABLE`] was split out for, one
    /// container up, and this port applies it there.
    fn check_crossref(&self, client: &dyn HttpClient, analysis: &mut Analysis, doi: &str) {
        let Some(crossref) = self.query_crossref(client, analysis, doi) else {
            return;
        };
        // Python's `if cr:` — an empty object is CrossRef answering nothing that
        // this component can report, so no indicator is stored at all. The
        // distinction matters: an empty body is not a claim that the record has
        // no funders.
        //
        // QUIRK: the asymmetry with the branch below is deliberate and is
        // reproduced. A `200 {}` stores **no** funder line, while a
        // `200 {"message": {}}` stores "No funder information in CrossRef" — so
        // two bodies that both carry no funder evidence differ in what the
        // persisted `risk_indicators` say. The truthiness guard is the original
        // behaviour and the split is a claim about how much of the record was
        // readable; nothing in the enumerated corrections touches it.
        if !crossref.as_object().is_some_and(|map| !map.is_empty()) {
            return;
        }
        let Some(message) = crossref.get("message").and_then(Value::as_object) else {
            // #307: the served body names no readable message, so nothing about
            // this record's funders can be asserted either way.
            analysis
                .indicators
                .push(INDICATOR_FUNDERS_NOT_READABLE.to_string());
            return;
        };
        match message.get("funder") {
            Some(Value::Array(funders)) if !funders.is_empty() => {
                analysis.award_funder_info();
                for funder in funders {
                    let name = json_object(funder)
                        .get("name")
                        .map_or("", |value| json_text(value));
                    if is_industry_funder(name) {
                        analysis.note_industry_funder(name);
                    }
                }
            }
            // CrossRef answered and holds nothing — the only two shapes that
            // mean that, and the only two this line may claim.
            None | Some(Value::Null) | Some(Value::Array(_)) => analysis
                .indicators
                .push(INDICATOR_NO_FUNDER_INFO.to_string()),
            // CrossRef sent *something* under `funder` that this module cannot
            // read. Reporting that as "no funder information" is a false claim
            // about the record — measured on the Python side: a `funder`
            // arriving as `{"name": "Acme Pharmaceuticals Inc"}` stored "CrossRef
            // has none" for a body naming an industry funder (PR #208's review).
            Some(_) => analysis
                .indicators
                .push(INDICATOR_FUNDERS_NOT_READABLE.to_string()),
        }
    }

    /// Fetch a paper record from Europe PMC.
    fn fetch_europepmc(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        pmid: Option<&str>,
        doi: Option<&str>,
    ) -> Option<Value> {
        if let Some(doi) = doi {
            return self.query_europepmc(client, analysis, &format!("DOI:\"{doi}\""));
        }
        if let Some(pmid) = pmid {
            return self.query_europepmc(client, analysis, &format!("EXT_ID:{pmid}"));
        }
        None
    }

    /// Fold COI and data-availability signals from Europe PMC into `analysis`.
    ///
    /// COI and data-availability statements live in a paper's full text, not its
    /// abstract, so the full text is fetched when available and the abstract is
    /// the fallback only when it cannot be retrieved.
    ///
    /// Sets `coi_disclosed` tri-state: `Some(true)` (statement found),
    /// `Some(false)` (full text scanned, none found), or — left as it was —
    /// `None` (undeterminable: full text not usable and no abstract signal).
    ///
    /// Industry ties disclosed in the COI statement itself are recorded through
    /// [`Analysis::note_industry_coi`], which is why this step needs no return
    /// value: it is only ever reached when full text was analyzed.
    fn check_europepmc(
        &self,
        client: &dyn HttpClient,
        epmc: &Value,
        analysis: &mut Analysis,
        document_id: &str,
    ) {
        let records = epmc_records(epmc);
        let Some(record) = records.first() else {
            return;
        };

        let mut search_text = map_text(record, "abstractText").to_lowercase();

        // Prefer full text — COI / data-availability statements are not in the
        // abstract. Europe PMC serves full text for open-access records.
        //
        // QUIRK: `== "Y"` exactly — a JSON `true` or `1` is not the string
        // Europe PMC documents, and `True == "Y"` is false in Python too. So a
        // record whose `inEPMC` arrives as any other truthy shape silently skips
        // the whole full-text step: no request, no log line, `coi_disclosed`
        // left `None` and up to 30 points unscored. Reproduced rather than
        // widened to a truthiness test, because the field is Europe PMC's own
        // documented vocabulary (`"Y"` / `"N"`) and no draw has shown another
        // shape — the port does not get to guess at one.
        if record.get("inEPMC").and_then(Value::as_str) == Some("Y") {
            // Both coerced: a mistyped accession is truthy, so on the Python side
            // it was interpolated into the URL (`.../{'a': 1}/fullTextXML`), spent
            // a rate-limited request, and stored the resulting 404 as
            // `NOT_SERVED` — a claim in Europe PMC's mouth for a URL bmlib
            // mangled. Empty is the true answer, and the guard inside already
            // reads it as `NOT_ATTEMPTED` (PR #208's review).
            let source = record
                .get("source")
                .map(json_text)
                .filter(|value| !value.is_empty());
            let ext_id = record
                .get("pmcid")
                .map(json_text)
                .filter(|value| !value.is_empty())
                .or_else(|| {
                    record
                        .get("id")
                        .map(json_text)
                        .filter(|value| !value.is_empty())
                });
            let fetch = self.fetch_europepmc_fulltext(client, source, ext_id, document_id);
            analysis.full_text_status = fetch.status;
            if let Some(text) = fetch.text {
                if !text.is_empty() {
                    search_text = text.to_lowercase();
                    analysis.full_text_analyzed = true;
                }
            }
        }

        // COI detection: a COI/disclosure statement counts as "disclosed",
        // including a statement that there is nothing to declare. A non-blank
        // JATS-tagged COI section is structural proof of a disclosure even when
        // its wording contains no cue phrase (issue #13); the cue-phrase scan
        // remains the fallback for untagged text.
        let tagged_coi = extract_tagged_coi_text(&search_text);
        if !tagged_coi.trim().is_empty()
            || COI_PATTERNS
                .iter()
                .any(|pattern| search_text.contains(pattern))
        {
            analysis.coi_disclosed = Some(true);
            analysis.score += SCORE_COI_DISCLOSED;
        } else if analysis.full_text_analyzed {
            // Full text inspected and no COI statement found -> explicitly
            // absent.
            analysis.coi_disclosed = Some(false);
            analysis
                .indicators
                .push(INDICATOR_NO_COI_IN_FULLTEXT.to_string());
        } else {
            // Could not inspect full text; the COI status is genuinely unknown —
            // and that is the whole of what this line says. It used to fork on
            // the refusal status to append one of two strings whose
            // parentheticals said *why* the text was not scanned, which made a
            // single line carry two claims and put the provenance inside the
            // reach of the PubMed retraction (issue #203). The why belongs to
            // `note_full_text_provenance`, keyed on the status rather than on
            // this branch.
            analysis.indicators.push(INDICATOR_COI_UNKNOWN.to_string());
        }

        // Data availability. The level is found into a local and nominated once:
        // this step is one of two producers, and the winner is scored by
        // `score_data_availability()` after every step has run. Nominating
        // unconditionally — including the "unknown" this falls through to —
        // keeps the step free of a "is this worth reporting?" judgement only the
        // carrier can make.
        let mut data_level = "unknown";
        for (pattern, level) in DATA_PATTERNS {
            if search_text.contains(pattern) {
                data_level = level;
                break;
            }
        }
        analysis.note_data_level(data_level);

        // Industry ties disclosed in the COI statement itself ("consultant for
        // X", "speaker fees from Y"). Scanned only in full text — an abstract
        // rarely carries a real disclosure statement — and only within the
        // COI/disclosure region to avoid false positives from references or
        // affiliations. Folded in last so the indicator order stays COI, then
        // data availability, then this.
        if analysis.full_text_analyzed
            && discloses_industry_ties(&extract_coi_text(&search_text, Some(&tagged_coi)))
        {
            analysis.note_industry_coi();
        }
    }

    /// Fetch this article's own full-text XML for an open-access Europe PMC
    /// record.
    ///
    /// Nested articles are removed here rather than at each scan, so there is one
    /// door into the module for a string that has to be the article's: every
    /// reader downstream — the tagged-COI match, the cue-phrase scan, the
    /// data-availability patterns and the industry-COI extraction — takes it from
    /// this return value.
    ///
    /// A [`FullTextFetch`] rather than a bare `Option<String>`, because "no full
    /// text" is several different claims and collapsing them is issue #161: none
    /// was served, or what was served could not be segmented into the article's
    /// own text, or none of it was the article's.
    ///
    /// **The five refusals are ordered most-specific-first, and the order is
    /// load-bearing.** A truncated body can satisfy several of them at once —
    /// truncation is the cause and the rest are symptoms — and each of the first
    /// three knows something the completeness check does not: which construct and
    /// at what offset, that a nested region was left open, that nothing outside a
    /// nested region arrived. Put the completeness check ahead of the lex and
    /// issue #160's message becomes unreachable for the body that most often
    /// produces it; put it ahead of the entirely-nested report and *that* becomes
    /// unreachable, since a body of nothing but `<sub-article>` carries no
    /// `</article>` either. So it runs last and reports only what nothing more
    /// specific claimed.
    ///
    /// Every refusal WARNs, naming which it was, the `document_id` that joins the
    /// line to a stored result, and how much was served in bytes. **Exactly one
    /// outcome is quiet, and it is the only one measured to be ordinary:** a
    /// **404** logs the URL at DEBUG. Europe PMC answered, and its answer is that
    /// it serves no open-access full text here — the majority outcome of the
    /// `inEPMC` gate this module uses, where 88 of 150 stratified records 404'd
    /// and 0 of 53 `isOpenAccess: N` records served. Every other way of getting
    /// no document WARNs and stores [`FullTextStatus::RequestFailed`] rather than
    /// `NotServed`, because none of them is Europe PMC saying anything about this
    /// article (issues #187, #190, #191).
    ///
    /// The identifier is what a record must carry for a request to leave at all.
    /// A record with no accession, or with one that is not a
    /// [`EUROPEPMC_ACCESSION_RE`] accession — overwhelmingly a `MED` record's
    /// bare PMID, measured at 0 of 43 served on 2026-09-09 — stores
    /// `NOT_ATTEMPTED` and makes no request. A request whose answer is known
    /// before it leaves is not made.
    fn fetch_europepmc_fulltext(
        &self,
        client: &dyn HttpClient,
        source: Option<&str>,
        ext_id: Option<&str>,
        document_id: &str,
    ) -> FullTextFetch {
        let source = source.filter(|value| !value.is_empty());
        let ext_id = ext_id.filter(|value| !value.is_empty());

        let Some(ext_id) = ext_id else {
            // Reachable only under `inEPMC == "Y"`, so Europe PMC has positively
            // claimed to hold the full text and then given nothing to address it
            // by. That is a malformed record rather than an ordinary closed-access
            // paper, and it used to be indistinguishable from one: no request, no
            // log at any level, and a status a reader would take as "we had no
            // reason to ask". A deposit can reach it, so WARNING.
            //
            // **`source` is deliberately not required.** It was, while it was a
            // path segment; since issue #184 it addresses nothing, and a guard
            // kept past the reason for it refuses a fetch that would have worked.
            log_line(
                Level::Warning,
                &format!(
                    "EuropePMC says it holds full text for document {} but the record carries no \
                     address for it (source={}, id={}); scanning the abstract instead",
                    if document_id.is_empty() {
                        "?"
                    } else {
                        document_id
                    },
                    py_repr(source),
                    py_repr(None)
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::NotAttempted,
            };
        };

        if !EUROPEPMC_ACCESSION_RE.is_match(ext_id) {
            // Issue #188. The record carries an identifier and it does not
            // address full text here. The request that used to follow **was
            // measured at 0 of 43 served**, and stored `NOT_SERVED` for it.
            //
            // *Not "could only 404"*: the draw bounds the served share at 8.2%
            // and does not zero it, and a request refused or dropped rather than
            // answered stored `REQUEST_FAILED` at WARNING. Generalising a
            // measured 0 into an impossibility is issue #191's own move, one
            // branch earlier.
            //
            // **DEBUG, and the asymmetry with the guard above is the whole point
            // of having two.** That one fires on a record claiming `inEPMC: Y`
            // and carrying nothing at all, which is malformed; this one fires on
            // a perfectly ordinary record whose full text Europe PMC holds under
            // an identifier this endpoint does not serve — a book chapter, most
            // often. Nothing is wrong, so a WARNING would be noise on a large
            // share of a corpus.
            //
            // `NOT_ATTEMPTED` and not a new member: its documented meaning is
            // *"no request was made, and Europe PMC's own answer is why"*, and
            // the record **is** Europe PMC's answer — it names no accession for
            // this article.
            log_line(
                Level::Debug,
                &format!(
                    "EuropePMC full text for document {} is not addressable: source={} carries \
                     identifier={}, which is not a EuropePMC accession; scanning the abstract \
                     instead",
                    if document_id.is_empty() {
                        "?"
                    } else {
                        document_id
                    },
                    py_repr(source),
                    py_repr(Some(ext_id))
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::NotAttempted,
            };
        }

        // `{source}/{ext_id}` only while a source was named. `source` may be
        // `None` — it addresses nothing — and the unconditional form then renders
        // `None/PMC123`: a two-segment path, in the one module whose signature
        // defect *was* a spurious two-segment path.
        let mut subject = match source {
            Some(source) => format!("{source}/{ext_id}"),
            None => ext_id.to_string(),
        };
        if !document_id.is_empty() {
            subject = format!("{subject} (document {document_id})");
        }

        self.rate_limit();
        let url = format!("{EUROPEPMC_REST_BASE}/{ext_id}/fullTextXML");
        let response = match client.get(&url) {
            Ok(response) => response,
            Err(error) => {
                // `REQUEST_FAILED`, not `NOT_SERVED` (issue #187): "requested and
                // not served" is a claim about *Europe PMC*, and nothing here
                // licenses one — the request never reached an answer.
                //
                // It does **not** re-raise. Every network step in this module
                // swallows its own request so one dead API cannot cost an
                // analysis; `analyze()` itself wraps nothing, which is why each
                // step must. The Python branch reported a `_BUG_TYPES` member at
                // ERROR and everything else at WARNING; [`FetchError`] carries no
                // such hierarchy, so a transport failure is the one class left.
                log_line(
                    Level::Warning,
                    &format!(
                        "EuropePMC full-text request for {subject} failed ({error}); scanning the \
                         abstract instead"
                    ),
                );
                return FullTextFetch {
                    text: None,
                    status: FullTextStatus::RequestFailed,
                };
            }
        };

        if response.status == 404 {
            // DEBUG, and the level is measured rather than chosen. Issue #184
            // proposed raising it, on the argument that a non-200 for a record
            // whose own metadata says `inEPMC: Y` is Europe PMC contradicting
            // itself. It is not: `inEPMC` says Europe PMC *holds* the text, while
            // `fullTextXML` serves the open-access subset of it.
            //
            // **That draw is of 404s, and so is this branch, since issue #191.**
            // It used to take every status code, which generalised the
            // measurement past what it looked for: a 429, a 503 or a 403 is the
            // ordinary outcome of nothing. Re-probed on 2026-09-05 over 200
            // `IN_EPMC:Y` records, **81 of the 81 non-200s were 404** — the
            // eligible denominator, the other 119 having served.
            //
            // It is logged rather than dropped, because #184 lived a whole
            // release inside this silence: every request 404'd and nothing said
            // so at any level. The URL is what names the defect, so the URL is
            // what the line carries.
            log_line(
                Level::Debug,
                &format!(
                    "EuropePMC served no full text for {subject}: HTTP {} from {url}",
                    response.status
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::NotServed,
            };
        }

        if response.status != 200 {
            // Issue #191. Every status that is neither the 200 below nor the 404
            // above: a 429, a 503, a 403. None of them is a statement about
            // whether Europe PMC holds this article's full text, which is the one
            // thing `NOT_SERVED` asserts — so `REQUEST_FAILED`.
            //
            // WARNING, and the asymmetry with the 404 is the measurement, not a
            // preference. This branch took 0 of the 81 non-200s among 200 live
            // probes, so it fires on nothing in a healthy draw and a WARNING is
            // not noise. It is the level the consequence needs: results are
            // cacheable and nothing in this package retries, so an outage window
            // silently caches a corpus of absences.
            log_line(
                Level::Warning,
                &format!(
                    "EuropePMC answered HTTP {} for {subject} from {url}, which is not an answer \
                     about whether it holds this article; scanning the abstract instead",
                    response.status
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::RequestFailed,
            };
        }

        // **Strict, because `fullTextXML` is XML.** A body that is not valid
        // UTF-8 cannot be the document this endpoint promises, and a lossy read
        // would scan U+FFFD for the article's prose and report it as analyzed.
        // `RequestFailed` rather than a refusal member: none of those describes a
        // document that cannot be read at all, and this is not a shape claim.
        let served = match response.text() {
            Ok(text) => text,
            Err(_) => {
                log_line(
                    Level::Warning,
                    &format!(
                        "EuropePMC full text for {subject} from {url} is not valid UTF-8; scanning \
                         the abstract instead"
                    ),
                );
                return FullTextFetch {
                    text: None,
                    status: FullTextStatus::RequestFailed,
                };
            }
        };
        if served.is_empty() {
            // Issue #190, and it runs here rather than beside the other refusals
            // because it is not a claim about a document's shape at all — nothing
            // arrived to have a shape. Left to fall through, an empty body
            // reached the *entirely nested* branch: `strip_nested_articles("")`
            // returns `Some("")`, so the unclosed-region check passed it and the
            // emptiness check claimed everything served was nested, storing
            // `ENTIRELY_NESTED` — so the caller persisted "full text served but
            // not usable" for a response that carried no document.
            //
            // **`served.is_empty()`, not `served.trim().is_empty()`.** A body
            // carrying bytes that strip to nothing did arrive, and is a
            // document-shaped claim; the stricter test would take a
            // wholly-whitespace body out of the entirely-nested branch and report
            // it as nothing served, which is a claim about the transport the
            // response refutes.
            //
            // Measured empty, so the guard is carried by the branch it lands in
            // being wrong rather than by a rate: of 119 bodies served across 200
            // live probes, **none was empty** — the smallest 2,622 bytes, the
            // median 85,925.
            log_line(
                Level::Warning,
                &format!(
                    "EuropePMC answered HTTP 200 with an empty body for {subject} from {url}; \
                     scanning the abstract instead"
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::RequestFailed,
            };
        }

        // Bytes, not the character count: Python quantifies a refusal in the
        // response's *encoded* length, so its `len(resp.content)` under-reports
        // nothing for a body carrying non-ASCII, where `resp.text`'s length
        // would. `String::len` is the UTF-8 byte length, which is that number for
        // a UTF-8 body — the encoding every `fullTextXML` response this module
        // has been served uses.
        let served_bytes = served.len();

        // The one documented refusal, on its own line and caught on its own
        // terms: a truncated body can reach it, so it is not a bmlib defect, and
        // the wider request handler above would have logged it as a fetch
        // failure — the mischaracterisation #159 moved this call out of that
        // block to avoid.
        let article_xml = match strip_nested_articles(served) {
            Err(error) => {
                log_line(
                    Level::Warning,
                    &format!(
                        "EuropePMC full text for {subject} is not well-formed ({error}) in \
                         {served_bytes} bytes served; scanning the abstract instead"
                    ),
                );
                return FullTextFetch {
                    text: None,
                    status: FullTextStatus::UnterminatedMarkup,
                };
            }
            Ok(None) => {
                // A deposit can reach this, so it is not a bmlib defect:
                // WARNING, and the analysis proceeds on the abstract.
                log_line(
                    Level::Warning,
                    &format!(
                        "EuropePMC full text for {subject} leaves an unclosed nested article in \
                         {served_bytes} bytes served; scanning the abstract instead"
                    ),
                );
                return FullTextFetch {
                    text: None,
                    status: FullTextStatus::UnclosedRegion,
                };
            }
            Ok(Some(article_xml)) => article_xml,
        };

        if article_xml.trim().is_empty() {
            // Everything served was nested. The caller's `if fetch.text:` would
            // read the empty string as "nothing was served" and fall back
            // silently, so it is reported here instead. Measured empty: all 3,389
            // carriers across the baseline corpus and an 880-article Europe PMC
            // draw keep their `<body>`, the least of them retaining 32.2% of its
            // bytes.
            log_line(
                Level::Warning,
                &format!(
                    "EuropePMC full text for {subject} is entirely nested articles \
                     ({served_bytes} bytes served); scanning the abstract instead"
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::EntirelyNested,
            };
        }

        if !served.contains(ROOT_END_TAG) {
            // Issue #183, and the last check for the reason given above: a body
            // truncated *between* tags opens no unterminated construct, leaves no
            // region open and empties nothing, so all three checks above pass it.
            // Scanned as a complete article it would yield
            // `coi_disclosed = Some(false)` — "No COI disclosure found in full
            // text" — for a disclosure that was in the lost tail, which is the
            // missing-COI HIGH downgrade fired on evidence that does not exist.
            //
            // **Presence, not position.** Issue #183 proposed
            // `rstrip().endswith(_ROOT_END_TAG)`; measured, that refuses complete
            // articles at a real rate, because trailing comments, PIs and
            // whitespace after the root are legal XML — 1,727 of the 97,909
            // archive articles (1.76%) and 23 of the 8,118 served ones (0.28%)
            // end `</article><!--requester-ID …-->`. The presence test's own 0 is
            // measured on the archive half alone: the served bundle is one
            // concatenation split on `</article>`, so containment there is true
            // by construction. A truncation removes the tail and the root's end
            // tag *is* in the tail; `</sub-article>` does not contain the
            // substring, so what the strip removes cannot affect this.
            log_line(
                Level::Warning,
                &format!(
                    "EuropePMC full text for {subject} did not arrive whole: no {ROOT_END_TAG} in \
                     {served_bytes} bytes served; scanning the abstract instead"
                ),
            );
            return FullTextFetch {
                text: None,
                status: FullTextStatus::Truncated,
            };
        }

        FullTextFetch {
            text: Some(article_xml),
            status: FullTextStatus::Analyzed,
        }
    }

    /// Fetch and parse the PubMed record for `pmid`.
    ///
    /// Returns empty signals when there is no PMID to look up or the request
    /// fails, so the step is optional in every sense: it costs no request without
    /// an identifier and never breaks an analysis when NCBI is down.
    ///
    /// **An empty 200 body is reported, not dropped.** The query helper returns
    /// `None` having already logged, but it returns `Some("")` for a 200 carrying
    /// nothing, and a bare falsy test cannot tell the two apart — so until PR
    /// #195's review this was the one endpoint of five where *"answered, and the
    /// answer is unusable"* left no line at any level. It is not cosmetic: empty
    /// signals mean no `<CoiStatement>`, so nothing in
    /// [`INDICATORS_RETRACTED_BY_PUBMED_COI`] is retracted, "COI disclosure
    /// status unknown" stands, and the missing-COI downgrade can fire.
    fn check_pubmed(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        pmid: Option<&str>,
    ) -> PubMedSignals {
        let Some(pmid) = pmid else {
            return PubMedSignals::default();
        };
        let Some(xml_text) = self.query_pubmed(client, analysis, pmid) else {
            return PubMedSignals::default();
        };
        if xml_text.is_empty() {
            log_line(
                Level::Warning,
                &format!(
                    "PubMed for {pmid}: answered 200 with an empty body; {PUBMED_SIGNALS_LOST}"
                ),
            );
            return PubMedSignals::default();
        }
        parse_pubmed_signals(&xml_text, pmid)
    }

    /// Fold open-access status and citation count from OpenAlex into `analysis`.
    fn check_openalex(&self, client: &dyn HttpClient, analysis: &mut Analysis, doi: &str) {
        let Some(openalex) = self.query_openalex(client, analysis, doi) else {
            return;
        };
        if !openalex.as_object().is_some_and(|map| !map.is_empty()) {
            return;
        }
        // `json_bool` and not truthiness: `{"is_oa": "false"}` is a truthy string,
        // so a bare read awarded `SCORE_OPEN_ACCESS` for a body stating the
        // opposite (PR #208's review). `None` — the remote did not say — is not
        // open access, which is what the existing absent-key behaviour already
        // was.
        if map_object(openalex.as_object(), "open_access")
            .get("is_oa")
            .and_then(json_bool)
            == Some(true)
        {
            analysis.score += SCORE_OPEN_ACCESS;
        }
        if json_count(openalex.get("cited_by_count").unwrap_or(&Value::Null)) > 0 {
            analysis.score += SCORE_CITED;
        }
    }

    /// Check trial registration and, where possible, results posting.
    ///
    /// PubMed's `DataBankList` is preferred over the abstract heuristic when
    /// present: it is the publisher asserting *this* paper's registration, so
    /// none of the heuristic's defences against a review's citation list apply to
    /// it. The heuristic remains the fallback for records PubMed does not cover.
    ///
    /// A registration ClinicalTrials.gov cannot be asked about — another
    /// registry, or a ClinicalTrials.gov entry with an unusable accession —
    /// counts as registered, but no claim is made about posted results either
    /// way.
    ///
    /// Takes no `pmid`/`doi`: they existed only to let the heuristic re-issue the
    /// Europe PMC search, which is what issue #202 removed.
    fn check_trial_registration(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        epmc: Option<&Value>,
        pubmed: &PubMedSignals,
    ) {
        let mut ct_ids = pubmed.trial_accessions.clone();
        if ct_ids.is_empty() {
            ct_ids = find_trial_ids(epmc);
        }
        if !ct_ids.is_empty() || pubmed.registration_not_checkable {
            analysis.trial_registered = true;
            analysis.score += SCORE_TRIAL_REGISTERED;
        }

        if !ct_ids.is_empty() {
            // **Four outcomes, not two** (issue #195's review, and three of them
            // until issue #206 added `PARTLY_ANSWERED`). Until then the Python
            // code was `any(...)` over a `bool`, so a trial nobody managed to ask
            // about was indistinguishable from one that answered "none posted" —
            // and the `else` stored *"Registered trial without posted results"*, a
            // false claim about the trial, in a persisted field. That is what made
            // issue #194 silent for a release.
            //
            // The loop still stops at the first trial with posted results, as the
            // `any()` it replaces did, because that answer is final. It cannot
            // stop early on any other, since a later accession may be the one that
            // answers.
            //
            // **The cap and the unanswered accession are one question** (issue
            // #206). Both mean *"bmlib did not ask about every accession this
            // paper named"*, and while either is true `INDICATOR_NO_POSTED_RESULTS`
            // is a claim the walk has not earned: the accession nobody reached may
            // be the one with results.
            let asked: Vec<String> = ct_ids
                .iter()
                .take(MAX_TRIAL_IDS_TO_CHECK)
                .cloned()
                .collect();
            let dropped = ct_ids.len() - asked.len();
            let mut answered = 0_usize;
            let mut compliant = false;
            for tid in &asked {
                match self.check_trial_results(client, analysis, tid) {
                    None => continue,
                    Some(posted) => {
                        answered += 1;
                        if posted {
                            compliant = true;
                            break;
                        }
                    }
                }
            }

            if dropped > 0 && !compliant {
                // **Only the cap gets a line, and it gets one whenever it could
                // have changed the outcome** — which is every walk that did not
                // find posted results, not only the partly-answered one. WARNING
                // because this is bmlib choosing to stop asking, which is the one
                // cause of the two an operator can act on: by raising the cap.
                //
                // **The accession that was asked about and did not answer is not
                // always logged** (PR #225's review). Four of the five ways the
                // results check returns `None` leave a line naming the accession
                // and the status; the fifth does not, because `json_bool` refuses a
                // wrong-typed value in silence. That is issue #226 — issue #209's
                // residual at this one site, where it decides a *stored status*.
                //
                // The dropped accessions are named because they are the whole of
                // what an operator recovers by raising the cap, and because this
                // line otherwise had no subject at all.
                log_line(
                    Level::Warning,
                    &format!(
                        "ClinicalTrials.gov: {dropped} of this paper's {} accessions were not \
                         checked (MAX_TRIAL_IDS_TO_CHECK is {MAX_TRIAL_IDS_TO_CHECK}) — {}; \
                         posted results cannot be ruled out for this paper",
                        ct_ids.len(),
                        ct_ids[MAX_TRIAL_IDS_TO_CHECK..].join(", ")
                    ),
                );
            }

            if compliant {
                analysis.results_compliant = true;
                analysis.trial_results_status = TrialResultsStatus::Posted;
                analysis.score += SCORE_RESULTS_POSTED;
            } else {
                // Accessions this walk established nothing about: the ones the cap
                // dropped, plus the ones asked about that did not answer. Computed
                // **inside the non-compliant arm** rather than beside the loop,
                // because the `break` leaves the un-walked tail out of `answered`
                // and the number is wrong there.
                let unestablished = dropped + asked.len() - answered;
                if answered > 0 && unestablished > 0 {
                    analysis.trial_results_status = TrialResultsStatus::PartlyAnswered;
                    // The line `NOT_CHECKABLE` and `REQUEST_FAILED` already share,
                    // for the reason they share it: the claim a human can act on is
                    // identical — bmlib could not establish the status — and it
                    // puts nothing in ClinicalTrials.gov's mouth. What a caller can
                    // act on is the enum, which is where the difference is carried.
                    analysis
                        .indicators
                        .push(INDICATOR_RESULTS_NOT_CHECKABLE.to_string());
                } else if answered > 0 {
                    analysis.trial_results_status = TrialResultsStatus::NotPosted;
                    analysis
                        .indicators
                        .push(INDICATOR_NO_POSTED_RESULTS.to_string());
                } else {
                    // Asked, and not one accession answered. The same line the
                    // other-registry case gets, because the claim is identical —
                    // *"could not be checked"* — and it puts nothing in
                    // ClinicalTrials.gov's mouth.
                    //
                    // The **status** does split them (issue #198): *"would
                    // re-running change this?"* is `yes` here and `no` for the
                    // other-registry case below, which is the question
                    // `FullTextStatus::RequestFailed` exists to answer one endpoint
                    // over.
                    //
                    // Reached only because `MAX_TRIAL_IDS_TO_CHECK` is at least 1:
                    // at 0 `asked` would be empty and this arm would claim a
                    // refusal for a walk that made no request.
                    analysis.trial_results_status = TrialResultsStatus::RequestFailed;
                    analysis
                        .indicators
                        .push(INDICATOR_RESULTS_NOT_CHECKABLE.to_string());
                }
            }
        } else if pubmed.registration_not_checkable {
            analysis.trial_results_status = TrialResultsStatus::NotCheckable;
            analysis
                .indicators
                .push(INDICATOR_RESULTS_NOT_CHECKABLE.to_string());
        }
    }

    // --- API query helpers ---

    /// Enforce the minimum interval between outgoing HTTP requests.
    ///
    /// The lock is held across the sleep so concurrent callers queue rather than
    /// all observing the same stale timestamp and firing simultaneously —
    /// serialising here is the point of a rate limiter.
    fn rate_limit(&self) {
        let mut last = self
            .last_request
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(previous) = *last {
            let elapsed = previous.elapsed();
            if elapsed < self.min_request_interval {
                std::thread::sleep(self.min_request_interval - elapsed);
            }
        }
        *last = Some(Instant::now());
    }

    /// Make one paced request and return the 200 response, or `None`.
    ///
    /// **One helper, because the shape it replaces had been got wrong in five
    /// copies** (issue #193). Each was `try` → `if status == 200: return` →
    /// `except Exception: logger.debug` → `return None`, which is two silences of
    /// different kinds: a bmlib defect held at a level nobody enables (issue
    /// #187), and **a non-200 falling off the end with no line at any level.**
    /// The latter is not a level problem but an absence: the `except` catches only
    /// raises, so a 429, a 503 or a 403 simply reached `return None` and there was
    /// no DEBUG line for an operator to enable.
    ///
    /// It does **not** propagate the transport error, for the reason argued at
    /// [`TransparencyAnalyzer::fetch_europepmc_fulltext`]'s own handler:
    /// `analyze()` wraps none of these, so every step must swallow its own request
    /// or one dead API costs the analysis.
    ///
    /// **The response is returned rather than the decoded body**, and the decode
    /// lives in the two helpers above the same reporting boundary. A body that
    /// will not parse is the remote's failure, and moving the decode outside any
    /// handler would let it escape a public `analyze()`.
    fn request(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        spec: &RequestSpec<'_>,
    ) -> Option<HttpResponse> {
        self.rate_limit();
        let response = match client.get(spec.url) {
            Ok(response) => response,
            Err(error) => {
                // One reporter, because the two-level split is the whole of issue
                // #187's rule and a second copy of it is a second place to get it
                // wrong. The ERROR branch Python keeps for a `_BUG_TYPES` member
                // has no counterpart: `FetchError` is a closed enum and a
                // transport failure is the environment's.
                log_line(
                    Level::Warning,
                    &format!(
                        "{} for {}: the request failed ({error})",
                        spec.api, spec.subject
                    ),
                );
                return None;
            }
        };
        if response.status == 200 {
            // Set on the 200 and before the body is read: a remote that answered
            // 200 and then sent something unreadable *was* reachable, and
            // demoting the whole analysis to UNKNOWN over a malformed body would
            // claim more than the evidence supports. The results check marks
            // reachability too, where Python's did not; that is unobservable and
            // deliberate, every path to it needing a 200 already.
            analysis.api_reachable = true;
            return Some(response);
        }
        let level = if spec.quiet_statuses.contains(&response.status) {
            Level::Debug
        } else {
            Level::Warning
        };
        // **The consequence is the caller's to state, not this helper's.** The
        // Python line used to end "that component is not scored", which is true
        // for CrossRef and OpenAlex and wrong for the other three: a refused
        // ClinicalTrials.gov request used to *manufacture* a scored finding (issue
        // #194), and a failed Europe PMC search gates the whole full-text step
        // rather than one component. A helper shared by five call sites cannot
        // know which, so it reports the request and `analyze()` reports what was
        // lost.
        log_line(
            level,
            &format!(
                "{} answered HTTP {} for {} from {}; that request produced no answer",
                spec.api, response.status, spec.subject, spec.url
            ),
        );
        None
    }

    /// [`TransparencyAnalyzer::request`], decoded as a JSON **object**, or `None`.
    ///
    /// A 200 carrying something that is not JSON used to be logged as *"query
    /// failed"* at DEBUG, which names the wrong stage: the request succeeded and
    /// the body is what is wrong.
    ///
    /// **It promises an object, not merely valid JSON** (issue #199). JSON's top
    /// level may be an array, a string, a number, `true` or `null`, and every
    /// caller here reads the body with `.get()`. A **truthy** non-object therefore
    /// raised `AttributeError` out of a public `analyze`, which wraps none of its
    /// steps — truthy because `null`, `[]`, `false` and `0` are refused a step
    /// earlier by the callers' own `if cr:` / `elif epmc:` / `if oa:`.
    ///
    /// The refusal belongs at this layer and not at the caller: the request
    /// helper pushes the *consequence* out to the caller because five call sites
    /// lose different things, while the *body* is this layer's subject already.
    fn request_json(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        spec: &RequestSpec<'_>,
    ) -> Option<Value> {
        let response = self.request(client, analysis, spec)?;
        let text = match response.text() {
            Ok(text) => text,
            Err(_) => {
                // Not valid UTF-8, so not JSON either: the remote answered and
                // sent a body no reader here can use, which is the remote's
                // failure — the same boundary as a body that will not parse,
                // one step earlier.
                log_line(
                    Level::Warning,
                    &format!(
                        "{} for {}: answered 200 with a body that is not valid UTF-8; that request \
                         produced no answer",
                        spec.api, spec.subject
                    ),
                );
                return None;
            }
        };
        let data: Value = match serde_json::from_str(text) {
            Ok(data) => data,
            Err(_) => {
                log_line(
                    Level::Warning,
                    &format!(
                        "{} for {}: answered 200 with a body that is not JSON; that request \
                         produced no answer",
                        spec.api, spec.subject
                    ),
                );
                return None;
            }
        };
        if !data.is_object() {
            // WARNING for the reason a body that will not parse warns: the remote
            // answered and sent a shape no reader here can use, which is the
            // remote's failure and not bmlib's. The type is named, since "not an
            // object" does not say whether an array or a bare string arrived.
            log_line(
                Level::Warning,
                &format!(
                    "{} for {}: answered 200 with JSON that is not an object ({}) from {}; that \
                     request produced no answer",
                    spec.api,
                    spec.subject,
                    json_type_name(&data),
                    spec.url
                ),
            );
            return None;
        }
        Some(data)
    }

    /// [`TransparencyAnalyzer::request`], read as text.
    ///
    /// Separate from the JSON reader rather than a flag on it, because the two
    /// return different types and a caller that got the wrong one would find out
    /// at a `.get()` several frames away. It takes no headers, unlike the JSON
    /// reader, and the asymmetry is deliberate rather than an omission: PubMed's
    /// `efetch` is the only text endpoint and asks for none. Add the parameter
    /// when a second one arrives, not before.
    fn request_text(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        spec: &RequestSpec<'_>,
    ) -> Option<String> {
        let response = self.request(client, analysis, spec)?;
        match response.text() {
            Ok(text) => Some(text.to_string()),
            Err(_) => {
                // PubMed's `efetch` is XML; a body that is not valid UTF-8 is not
                // a document this step can read, and saying so is the whole
                // reason the read is strict.
                log_line(
                    Level::Warning,
                    &format!(
                        "{} for {}: answered 200 with a body that is not valid UTF-8; that request \
                         produced no answer",
                        spec.api, spec.subject
                    ),
                );
                None
            }
        }
    }

    /// Query the CrossRef API for a DOI.
    fn query_crossref(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        doi: &str,
    ) -> Option<Value> {
        let url = CROSSREF_WORKS_URL.replace("{doi}", doi);
        self.request_json(
            client,
            analysis,
            &RequestSpec {
                url: &url,
                api: "CrossRef",
                subject: doi,
                quiet_statuses: CROSSREF_ORDINARY_STATUSES,
            },
        )
    }

    /// Query the Europe PMC search API.
    fn query_europepmc(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        query: &str,
    ) -> Option<Value> {
        let url = format!(
            "{EUROPEPMC_REST_BASE}/search?{}",
            encode_query(&[("query", query), ("format", "json"), ("resultType", "core"),])
        );
        self.request_json(
            client,
            analysis,
            &RequestSpec {
                url: &url,
                api: "EuropePMC",
                subject: query,
                quiet_statuses: EUROPEPMC_SEARCH_ORDINARY_STATUSES,
            },
        )
    }

    /// Fetch a single PubMed record as XML via E-utilities `efetch`.
    ///
    /// `tool` and `email` identify the caller, as NCBI asks. `api_key` is sent
    /// when configured: it does not change this client's pacing, but it moves the
    /// request into the key's 10 requests/second allowance instead of the 3
    /// requests/second shared by everything on the IP.
    fn query_pubmed(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        pmid: &str,
    ) -> Option<String> {
        let mut params: Vec<(&str, &str)> = vec![
            ("db", "pubmed"),
            ("id", pmid),
            ("retmode", "xml"),
            ("tool", EUTILS_TOOL_NAME),
            ("email", &self.email),
        ];
        if let Some(api_key) = &self.pubmed_api_key {
            params.push(("api_key", api_key));
        }
        let url = format!("{EFETCH_URL}?{}", encode_query(&params));
        self.request_text(
            client,
            analysis,
            &RequestSpec {
                url: &url,
                api: "PubMed",
                subject: pmid,
                quiet_statuses: PUBMED_ORDINARY_STATUSES,
            },
        )
    }

    /// Query the OpenAlex API for a DOI.
    fn query_openalex(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        doi: &str,
    ) -> Option<Value> {
        let url = OPENALEX_WORKS_URL.replace("{doi}", doi);
        self.request_json(
            client,
            analysis,
            &RequestSpec {
                url: &url,
                api: "OpenAlex",
                subject: doi,
                quiet_statuses: OPENALEX_ORDINARY_STATUSES,
            },
        )
    }

    /// Check whether a ClinicalTrials.gov trial has posted results.
    ///
    /// Uses the v2 API's top-level `hasResults` boolean. An earlier Python
    /// implementation requested a `ResultsSection` field but read a
    /// `resultsSection` key, so it under-detected posted results.
    ///
    /// The request is narrowed to `hasResults`, so that is the only key the
    /// response can carry; a missing key means the API did not answer the question
    /// and is reported as "no posted results" rather than guessed at from a
    /// payload that was never requested.
    ///
    /// **A `bool` could not distinguish "no results posted" from "not answered",
    /// and that is what made issue #194 silent**: the edge refused bmlib's
    /// `User-Agent` with a 403, this returned `False`, and the caller stored
    /// *"Registered trial without posted results"* — a false claim about the trial
    /// — for every registered trial bmlib ever analysed. Correcting the header
    /// made the requests succeed and left the conflation in place, so a 404, a 403
    /// or a body that will not decode still manufactured the same false finding.
    ///
    /// Returns `Some(true)` when ClinicalTrials.gov said results are posted,
    /// `Some(false)` when it said they are not, and `None` when it did not answer
    /// the question — a request that failed, a non-200, a body that will not
    /// decode, or a 200 carrying something that is not a JSON object. Every one of
    /// those is *"we do not know"*.
    fn check_trial_results(
        &self,
        client: &dyn HttpClient,
        analysis: &mut Analysis,
        nct_id: &str,
    ) -> Option<bool> {
        let url = format!(
            "{}?fields=hasResults",
            CLINICALTRIALS_STUDY_URL.replace("{nct_id}", nct_id)
        );
        let data = self.request_json(
            client,
            analysis,
            &RequestSpec {
                url: &url,
                api: "ClinicalTrials.gov",
                subject: nct_id,
                quiet_statuses: CLINICALTRIALS_ORDINARY_STATUSES,
            },
        )?;
        let Some(map) = data.as_object() else {
            // A JSON body that is not an object answers the question no more than
            // a 404 does — so `None`, rather than a `False` that was a *finding*
            // manufactured out of an unusable body.
            //
            // **Since issue #199 this branch is reached only for `None`**, the
            // non-object body being refused a layer down. It is kept rather than
            // narrowed, as the second of two independent protections at the one
            // site where an unusable body did not merely raise but *published a
            // false finding about a trial* for a whole release (issue #194).
            return None;
        };
        match map.get("hasResults") {
            // `_json_bool` and not `bool()`. This is the value the guard above
            // never covered: `bool("no")` is `True`, so ClinicalTrials.gov stating
            // *no results* was stored as `POSTED` with `trial_results_compliant`
            // set and `SCORE_RESULTS_POSTED` awarded — a false claim in the
            // affirmative about a trial.
            //
            // QUIRK: an **absent** key keeps Python's old answer of `false`,
            // deliberately and not by oversight. Python's own comment says "an
            // absent key means unanswered" and then reports a finding, which is
            // the conflation issues #195/#198 removed everywhere else; routing it
            // to `None` would move a stored value for a *well-formed* body. Filed
            // as issue #210 rather than settled in passing, and reproduced here.
            None | Some(Value::Null) => Some(false),
            Some(value) => json_bool(value),
        }
    }
}

/// The `object` at `key`, or an empty one.
fn map_object<'a>(map: Option<&'a Map<String, Value>>, key: &str) -> &'a Map<String, Value> {
    match map.and_then(|map| map.get(key)) {
        Some(value) => json_object(value),
        None => &EMPTY_OBJECT,
    }
}

impl Default for TransparencyAnalyzer {
    fn default() -> Self {
        TransparencyAnalyzer::new("user@example.com", None, TransparencySettings::default())
    }
}
