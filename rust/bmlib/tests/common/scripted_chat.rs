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

//! A scripted model for the quality agents' tests: [`ScriptedChat`] is the real
//! `bmlib::agents::chat_json` over a [`Script`] of responses, so a test drives
//! an agent through the same retry and parsing rules production does.

use bmlib::agents::{chat_json, ChatJsonError, ChatJsonOutcome, ChatSource};
use bmlib::llm::{LLMMessage, LLMResponse};
use bmlib::quality::agent_chat::JsonChat;

// ---------------------------------------------------------------------------
// The scripted source
// ---------------------------------------------------------------------------

/// One response per call, repeating the last, and a record of the backoffs.
pub struct Script {
    pub responses: Vec<Result<LLMResponse, String>>,
    pub calls: usize,
    pub backoffs: Vec<usize>,
}

impl Script {
    pub fn new(responses: Vec<Result<LLMResponse, String>>) -> Self {
        Script {
            responses,
            calls: 0,
            backoffs: Vec::new(),
        }
    }

    /// A source answering `content` with `stop_reason`, forever.
    pub fn answering(content: &str, stop_reason: Option<&str>) -> Self {
        Script::new(vec![Ok(response(content, stop_reason))])
    }
}

pub fn response(content: &str, stop_reason: Option<&str>) -> LLMResponse {
    LLMResponse {
        content: content.to_string(),
        stop_reason: stop_reason.map(str::to_string),
        ..Default::default()
    }
}

impl ChatSource for Script {
    fn chat(&mut self, _attempt: usize) -> Result<LLMResponse, String> {
        let index = self.calls.min(self.responses.len().saturating_sub(1));
        self.calls += 1;
        self.responses[index].clone()
    }

    fn backoff(&mut self, attempt: usize) {
        self.backoffs.push(attempt);
    }
}

/// What one tier asked for.
#[derive(Debug, Clone, PartialEq)]
pub struct Call {
    pub temperature: f64,
    pub max_tokens: i64,
    pub max_retries: usize,
    pub require_dict: bool,
}

/// A [`JsonChat`] that is really [`bmlib::agents::chat_json`] over a [`Script`].
pub struct ScriptedChat {
    pub source: Script,
    pub seen: Vec<Vec<LLMMessage>>,
    pub calls: Vec<Call>,
}

impl ScriptedChat {
    pub fn new(source: Script) -> Self {
        ScriptedChat {
            source,
            seen: Vec::new(),
            calls: Vec::new(),
        }
    }

    pub fn answering(content: &str, stop_reason: Option<&str>) -> Self {
        ScriptedChat::new(Script::answering(content, stop_reason))
    }

    /// The user turn of the last call, or `""` when nothing was sent.
    pub fn last_user_message(&self) -> &str {
        self.seen
            .last()
            .and_then(|messages| messages.iter().find(|m| m.role == bmlib::llm::Role::User))
            .map(|m| m.content.as_str())
            .unwrap_or("")
    }
}

impl JsonChat for ScriptedChat {
    fn chat_json(
        &mut self,
        messages: &[LLMMessage],
        temperature: f64,
        max_tokens: i64,
        max_retries: usize,
        require_dict: bool,
    ) -> Result<ChatJsonOutcome, ChatJsonError> {
        self.seen.push(messages.to_vec());
        self.calls.push(Call {
            temperature,
            max_tokens,
            max_retries,
            require_dict,
        });
        chat_json(
            &mut self.source,
            max_retries,
            temperature,
            Some(max_tokens),
            max_tokens,
            require_dict,
        )
    }
}
