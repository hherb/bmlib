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

//! A scripted transport for the full-text service tests: it answers from a
//! script and records every URL it was asked. Shared by `tests/service.rs` and
//! `tests/service_library.rs`, which include this file by path so neither needs
//! the rest of `tests/common` (and its `sqlite` feature).

#![allow(dead_code)]

use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

/// One scripted answer: a response, or the transport failing.
pub type Answer = Result<HttpResponse, FetchError>;

/// A transport that answers from a script and records what it was asked.
pub struct ScriptedClient {
    script: Mutex<VecDeque<Answer>>,
    repeat: Option<Answer>,
    requests: Mutex<Vec<String>>,
}

impl ScriptedClient {
    pub fn new(script: Vec<Answer>) -> Arc<Self> {
        Arc::new(ScriptedClient {
            script: Mutex::new(script.into()),
            repeat: None,
            requests: Mutex::new(Vec::new()),
        })
    }

    /// Answer every request with the same response.
    pub fn always(response: HttpResponse) -> Arc<Self> {
        Arc::new(ScriptedClient {
            script: Mutex::new(VecDeque::new()),
            repeat: Some(Ok(response)),
            requests: Mutex::new(Vec::new()),
        })
    }

    /// Answer from `script`, then every later request with `response`.
    pub fn then_always(script: Vec<Answer>, response: HttpResponse) -> Arc<Self> {
        Arc::new(ScriptedClient {
            script: Mutex::new(script.into()),
            repeat: Some(Ok(response)),
            requests: Mutex::new(Vec::new()),
        })
    }

    /// The URLs requested, in order.
    pub fn requests(&self) -> Vec<String> {
        self.requests.lock().expect("lock").clone()
    }

    pub fn call_count(&self) -> usize {
        self.requests.lock().expect("lock").len()
    }
}

impl HttpClient for ScriptedClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.requests.lock().expect("lock").push(url.to_string());
        if let Some(answer) = self.script.lock().expect("lock").pop_front() {
            return answer;
        }
        match &self.repeat {
            Some(answer) => answer.clone(),
            // Exhausting the script is a test error, not a quiet 404: it makes
            // an over-run a recorded transport fault.
            None => Err(FetchError::Transport(format!(
                "scripted client exhausted at {url}"
            ))),
        }
    }
}
