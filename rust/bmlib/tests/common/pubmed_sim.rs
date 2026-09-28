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

//! A scripted E-utilities transport, shared by every test that drives PubMed
//! through one.
//!
//! One implementation for the partitioned loop and for the fetcher, because they
//! script the *same* contract — `Eutils` — and a second copy would drift on the
//! two things that are easy to get subtly wrong: which searches carry a history
//! session, and which EDAT range a term restricts. It records every call, so a
//! test can also assert what was asked.

use std::collections::BTreeMap;
use std::sync::Mutex;

use chrono::NaiveDate;

use bmlib::publications::fetchers::pubmed::{EFetchPage, ESearchResult, Eutils, EFETCH_PAGE_SIZE};
use bmlib::publications::models::FetchedRecord;

/// A scripted transport, recording every call and serving counts/pages in order.
pub struct ScriptedEutils {
    /// Answers to plan-time searches, keyed by the term's EDAT range.
    pub counts: Mutex<BTreeMap<String, i64>>,
    /// Answers to session searches, keyed the same way.
    pub session_counts: Mutex<BTreeMap<String, i64>>,
    /// The page every fetch serves, and how many are left.
    ///
    /// A part's walk asks for one page per `EFETCH_PAGE_SIZE` records, so a
    /// scripted transport has to serve pages *per part*. `None` means "serve
    /// forever".
    pub page: Mutex<Option<EFetchPage>>,
    /// Fetches left before the transport refuses.
    pub pages_left: Mutex<Option<usize>>,
    /// Plan-time searches that **fail**, keyed like `counts`: the value is
    /// Python's `f"{type(exc).__name__}: {exc}"`, which is the contract
    /// `Eutils` documents (#359).
    pub plan_failures: Mutex<BTreeMap<String, String>>,
    /// Session searches that **fail**, keyed like `session_counts` and carrying
    /// the same contract. Separate from `plan_failures` because the two are
    /// different calls: a day's own search carries a history session and a plan's
    /// does not, which is what `Eutils::esearch`'s third argument decides.
    pub session_failures: Mutex<BTreeMap<String, String>>,
    /// Every term a plan-time search was asked for.
    pub plan_terms: Mutex<Vec<String>>,
    /// Every term a session search was asked for.
    pub session_terms: Mutex<Vec<String>>,
    /// Whether a session ESearch reports a WebEnv/QueryKey.
    pub session_available: bool,
    /// A **dense** source: `(lo, hi, per_day)` answers any EDAT range with
    /// `per_day` × the days it overlaps, and a term carrying no EDAT range — the
    /// day's own — with the window's whole total. A range in `counts` (or
    /// `session_counts`) still wins.
    ///
    /// The alternative is scripting a count per split point, which makes the test
    /// break whenever the ladder's arithmetic moves; the partitioned loop's own
    /// tests avoid that by injecting a narrow root, and the *fetcher* cannot,
    /// because `fetch_pubmed` uses the production one. Real records are spread like
    /// this anyway: a day's EDATs are deposit dates, not the publication day.
    pub dense: Option<(NaiveDate, NaiveDate, i64)>,
}

impl ScriptedEutils {
    pub fn empty() -> Self {
        ScriptedEutils {
            counts: Mutex::new(BTreeMap::new()),
            session_counts: Mutex::new(BTreeMap::new()),
            page: Mutex::new(None),
            pages_left: Mutex::new(None),
            plan_failures: Mutex::new(BTreeMap::new()),
            session_failures: Mutex::new(BTreeMap::new()),
            plan_terms: Mutex::new(Vec::new()),
            session_terms: Mutex::new(Vec::new()),
            session_available: true,
            dense: None,
        }
    }

    /// A transport whose plan and session searches agree, over one page per part
    /// delivering `per_part` records.
    pub fn uniform(counts: BTreeMap<String, i64>, per_part: i64) -> Self {
        let mut transport = ScriptedEutils::empty();
        *transport.counts.lock().expect("lock") = counts.clone();
        *transport.session_counts.lock().expect("lock") = counts;
        transport.set_pages(per_part);
        transport
    }

    /// Give every future page a fixed delivery, served without limit.
    ///
    /// A **page** carries at most
    /// [`EFETCH_PAGE_SIZE`](bmlib::publications::fetchers::pubmed::EFETCH_PAGE_SIZE)
    /// records — the walk asks for `retmax=500`, so a transport that answered
    /// with a whole part's worth in one page would make the walk deliver many
    /// times the part's promise. A first cut did that, and the day-total
    /// reconcile then passed by accident.
    pub fn set_pages(&mut self, per_part_promise: i64) {
        let delivered = per_part_promise.min(EFETCH_PAGE_SIZE as i64);
        let page = EFetchPage {
            articles: (0..delivered)
                .map(|i| FetchedRecord::new(format!("r{i}"), "pubmed"))
                .collect(),
            delivered,
        };
        *self.page.lock().expect("lock") = Some(page);
        *self.pages_left.lock().expect("lock") = None;
    }

    /// Serve at most `n` pages in total, then refuse.
    pub fn limit_pages(&mut self, n: usize) {
        *self.pages_left.lock().expect("lock") = Some(n);
    }

    /// Make the **plan-time** search for one range fail, as a transport failure
    /// arrives.
    pub fn fail_plan(&mut self, lo: &str, hi: &str, message: &str) {
        self.plan_failures
            .lock()
            .expect("lock")
            .insert(Self::range_key(lo, hi), message.to_string());
    }

    /// The EDAT range a term restricts, when it restricts one.
    fn term_range(term: &str) -> Option<(NaiveDate, NaiveDate)> {
        let mut dates: Vec<NaiveDate> = Vec::new();
        for (start, _) in term.match_indices('"') {
            let rest = &term[start + 1..];
            if let Some(end) = rest.find('"') {
                let candidate = &rest[..end];
                if candidate.len() == 10 && rest[end + 1..].starts_with("[EDAT]") {
                    if let Ok(date) =
                        NaiveDate::parse_from_str(&candidate.replace('/', "-"), "%Y-%m-%d")
                    {
                        dates.push(date);
                    }
                }
            }
        }
        match dates.as_slice() {
            [lo, hi] => Some((*lo, *hi)),
            _ => None,
        }
    }

    /// How many days of `[lo, hi]` fall inside `[start, end]`, inclusive.
    fn overlap_days(start: NaiveDate, end: NaiveDate, lo: NaiveDate, hi: NaiveDate) -> i64 {
        let from = start.max(lo);
        let to = end.min(hi);
        if to < from {
            0
        } else {
            (to - from).num_days() + 1
        }
    }

    /// What a count map says for `key`, falling back to [`ScriptedEutils::dense`].
    fn scripted_count(&self, map: &BTreeMap<String, i64>, key: &str, term: &str) -> i64 {
        if let Some(value) = map.get(key) {
            return *value;
        }
        let Some((lo, hi, per_day)) = self.dense else {
            return 0;
        };
        match Self::term_range(term) {
            Some((start, end)) => Self::overlap_days(start, end, lo, hi) * per_day,
            None => Self::overlap_days(lo, hi, lo, hi) * per_day,
        }
    }

    /// The key of an EDAT range term, for the count maps.
    pub fn range_key(lo: &str, hi: &str) -> String {
        format!("{lo}..{hi}")
    }

    /// The EDAT range a term restricts, as a `lo..hi` key.
    ///
    /// Only the dates **followed by `[EDAT]`** count: the day term carries its
    /// own `"YYYY/MM/DD"[Date - Publication]`, and taking every quoted date in
    /// the term yields three, which matches no key and silently reads as zero —
    /// so the ladder's root probe refuses a day that is perfectly fetchable. A
    /// first cut did exactly that.
    pub fn term_key(term: &str) -> String {
        // Each EDAT date is a quoted span whose closing quote is followed by
        // `[EDAT]`. Taking *every* quoted span instead picks up the day term's
        // own `"YYYY/MM/DD"[Date - Publication]`, which makes three dates, no
        // match, and a silent zero.
        let mut dates: Vec<String> = Vec::new();
        for (start, _) in term.match_indices('"') {
            let rest = &term[start + 1..];
            if let Some(end) = rest.find('"') {
                let candidate = &rest[..end];
                if candidate.len() == 10 && rest[end + 1..].starts_with("[EDAT]") {
                    dates.push(candidate.replace('/', "-"));
                }
            }
        }
        match dates.as_slice() {
            [lo, hi] => Self::range_key(lo, hi),
            _ => term.to_string(),
        }
    }
}

impl Eutils for ScriptedEutils {
    fn esearch(
        &self,
        term: &str,
        _api_key: Option<&str>,
        use_history: bool,
    ) -> Result<ESearchResult, String> {
        let key = Self::term_key(term);
        if use_history {
            self.session_terms.lock().expect("lock").push(key.clone());
            if let Some(message) = self.session_failures.lock().expect("lock").get(&key) {
                return Err(message.clone());
            }
            let counts = self.session_counts.lock().expect("lock");
            let count = self.scripted_count(&counts, &key, term);
            return Ok(ESearchResult {
                count,
                web_env: self.session_available.then(|| "W".to_string()),
                query_key: self.session_available.then(|| "1".to_string()),
            });
        }
        self.plan_terms.lock().expect("lock").push(key.clone());
        if let Some(message) = self.plan_failures.lock().expect("lock").get(&key) {
            return Err(message.clone());
        }
        let counts = self.counts.lock().expect("lock");
        Ok(ESearchResult {
            count: self.scripted_count(&counts, &key, term),
            web_env: None,
            query_key: None,
        })
    }

    fn efetch(
        &self,
        _web_env: &str,
        _query_key: &str,
        _retstart: usize,
        _api_key: Option<&str>,
    ) -> Result<EFetchPage, String> {
        let mut left = self.pages_left.lock().expect("lock");
        if let Some(remaining) = *left {
            if remaining == 0 {
                return Err("no more pages scripted".to_string());
            }
            *left = Some(remaining - 1);
        }
        self.page
            .lock()
            .expect("lock")
            .clone()
            .ok_or_else(|| "no page scripted".to_string())
    }
}
