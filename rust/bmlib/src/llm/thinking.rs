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

//! How a request's `think` becomes Anthropic request fields (#707).
//!
//! Claude Opus 4.7 and later, Sonnet 5+, Haiku 5.5 and Fable refuse
//! `{"type": "enabled", "budget_tokens": N}` and think adaptively
//! (`{"type": "adaptive"}`, depth by `output_config.effort`); Opus 4.6 and
//! Sonnet 4.6 take both; the 4.5 models and the thinking models before them
//! take only a budget, and a model before Sonnet 3.7 does not think. Which one a model takes is
//! read off its own `GET /v1/models/{id}` body, never guessed from its name.
//! Python's `bmlib.llm.providers.anthropic.resolve_thinking` is the twin, both
//! held to `tests/fixtures/anthropic_thinking.json`.

use serde_json::{json, Map, Value};

/// The API's minimum thinking budget; a budget must also be below `max_tokens`.
pub const THINKING_BUDGET_MIN: i64 = 1024;

/// The budget `true` stands for on a model taking only budgets.
pub const DEFAULT_THINKING_BUDGET: i64 = 8192;

/// The budgets the effort levels stand for on a model taking only budgets.
pub const THINKING_EFFORT_BUDGETS: [(&str, i64); 3] =
    [("low", 2048), ("medium", 8192), ("high", 16384)];

/// Why a `think` request was refused before it was sent.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ThinkingRefusal {
    /// The model's metadata says nothing readable about thinking.
    NoCapabilities {
        /// The model asked about.
        model: String,
    },
    /// The model reports neither adaptive nor budgeted thinking.
    NotSupported {
        /// The model asked about.
        model: String,
    },
    /// A token budget for a model that thinks only adaptively.
    BudgetNotSupported {
        /// The model asked about.
        model: String,
        /// The budget asked for.
        budget: i64,
    },
    /// An effort level the model cannot take: on an adaptive model one it
    /// does not report under `capabilities.effort`, on a model taking only
    /// budgets one with no preset budget (not `low`, `medium` or `high`).
    EffortNotSupported {
        /// The model asked about.
        model: String,
        /// The level asked for, as given.
        effort: String,
        /// Whether the model thinks adaptively, which decides the rule.
        adaptive: bool,
    },
    /// A negative token budget.
    NegativeBudget(i64),
    /// `max_tokens` leaves no room for the minimum budget.
    MaxTokensTooSmall(i64),
}

impl ThinkingRefusal {
    /// The rule's name, as the shared fixture and Python's
    /// `ThinkingRefusedError.reason` spell it.
    #[must_use]
    pub fn reason(&self) -> &'static str {
        match self {
            ThinkingRefusal::NoCapabilities { .. } => "no-capabilities",
            ThinkingRefusal::NotSupported { .. } => "not-supported",
            ThinkingRefusal::BudgetNotSupported { .. } => "budget-not-supported",
            ThinkingRefusal::EffortNotSupported { .. } => "effort-not-supported",
            ThinkingRefusal::NegativeBudget(_) => "negative-budget",
            ThinkingRefusal::MaxTokensTooSmall(_) => "max-tokens-too-small",
        }
    }
}

impl std::fmt::Display for ThinkingRefusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ThinkingRefusal::NoCapabilities { model } => write!(
                f,
                "Anthropic reports no thinking capabilities for {model:?}, so think \
                 cannot be sent in a shape it takes"
            ),
            ThinkingRefusal::NotSupported { model } => {
                write!(f, "Anthropic model {model:?} reports no thinking support")
            }
            ThinkingRefusal::BudgetNotSupported { model, budget } => write!(
                f,
                "Anthropic model {model:?} takes no thinking budget (think={budget}); \
                 pass an effort level or true"
            ),
            ThinkingRefusal::EffortNotSupported {
                model,
                effort,
                adaptive: true,
            } => write!(
                f,
                "Anthropic model {model:?} does not report think effort level {effort:?}"
            ),
            ThinkingRefusal::EffortNotSupported {
                model,
                effort,
                adaptive: false,
            } => write!(
                f,
                "Anthropic model {model:?} takes only a thinking budget, and think={effort:?} \
                 has none (expected low, medium, high, an integer or true)"
            ),
            ThinkingRefusal::NegativeBudget(budget) => write!(
                f,
                "Negative thinking budget {budget}; expected a positive token count."
            ),
            ThinkingRefusal::MaxTokensTooSmall(max_tokens) => write!(
                f,
                "Anthropic extended thinking requires max_tokens > {THINKING_BUDGET_MIN} \
                 (the API minimum thinking budget); got max_tokens={max_tokens}. \
                 Increase max_tokens or disable think."
            ),
        }
    }
}

impl std::error::Error for ThinkingRefusal {}

/// Whether `think` asks for thinking, by Python's truthiness of the value the
/// text stands for (the shared fixture's encoding: a bool as `true`/`false`,
/// a number as its digits): `""`, `"false"` and an integer 0 do not. Python's
/// *string* `"false"` is truthy, which this text cannot express.
#[must_use]
pub fn think_is_on(think: &str) -> bool {
    !(think.is_empty() || think == "false" || think.parse::<i64>() == Ok(0))
}

/// Whether the leaf at `path` under `node` has `supported` exactly `true`.
fn supported(node: &Value, path: &[&str]) -> bool {
    let mut node = node;
    for key in path {
        match node.get(key) {
            Some(child) => node = child,
            None => return false,
        }
    }
    node.get("supported") == Some(&Value::Bool(true))
}

fn budget_fields(budget: i64, max_tokens: i64) -> Result<Map<String, Value>, ThinkingRefusal> {
    if max_tokens <= THINKING_BUDGET_MIN {
        return Err(ThinkingRefusal::MaxTokensTooSmall(max_tokens));
    }
    let budget = budget.min(max_tokens - 1).max(THINKING_BUDGET_MIN);
    let mut fields = Map::new();
    fields.insert(
        "thinking".to_string(),
        json!({"type": "enabled", "budget_tokens": budget}),
    );
    Ok(fields)
}

/// The request fields `think` (on, by [`think_is_on`]) becomes for the model
/// `model_body` describes: its `GET /v1/models/{id}` body.
///
/// `think` is read as Python's value: `"true"` is the bool, an integer is a
/// token budget, any other text an effort level (folded to lower case).
/// Adaptive wins for `true` (the model's default effort) and for an effort
/// level the model reports under `capabilities.effort` (sent as
/// `output_config.effort`). A budget needs `enabled` thinking; with `enabled`
/// alone, `true` and `low`/`medium`/`high` are preset budgets. A budget is
/// clamped to `[THINKING_BUDGET_MIN, max_tokens - 1]`.
///
/// # Errors
///
/// A [`ThinkingRefusal`] when the model cannot take `think`, or its body says
/// nothing readable about thinking (refused, never guessed).
pub fn anthropic_thinking(
    think: &str,
    max_tokens: i64,
    model_body: &Value,
) -> Result<Map<String, Value>, ThinkingRefusal> {
    let model = model_body
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let caps = model_body.get("capabilities").unwrap_or(&Value::Null);
    let Some(types) = caps
        .get("thinking")
        .and_then(|t| t.get("types"))
        .filter(|t| t.is_object())
    else {
        return Err(ThinkingRefusal::NoCapabilities { model });
    };
    let adaptive = supported(types, &["adaptive"]);
    let enabled = supported(types, &["enabled"]);
    if !(adaptive || enabled) {
        return Err(ThinkingRefusal::NotSupported { model });
    }
    if think == "true" {
        if adaptive {
            let mut fields = Map::new();
            fields.insert("thinking".to_string(), json!({"type": "adaptive"}));
            return Ok(fields);
        }
        return budget_fields(DEFAULT_THINKING_BUDGET, max_tokens);
    }
    if let Ok(budget) = think.parse::<i64>() {
        if budget < 0 {
            return Err(ThinkingRefusal::NegativeBudget(budget));
        }
        if !enabled {
            return Err(ThinkingRefusal::BudgetNotSupported { model, budget });
        }
        return budget_fields(budget, max_tokens);
    }
    let level = think.to_lowercase();
    if adaptive {
        if supported(caps, &["effort", level.as_str()]) {
            let mut fields = Map::new();
            fields.insert("thinking".to_string(), json!({"type": "adaptive"}));
            fields.insert("output_config".to_string(), json!({"effort": level}));
            return Ok(fields);
        }
        return Err(ThinkingRefusal::EffortNotSupported {
            model,
            effort: think.to_string(),
            adaptive,
        });
    }
    if let Some((_, budget)) = THINKING_EFFORT_BUDGETS.iter().find(|(l, _)| *l == level) {
        return budget_fields(*budget, max_tokens);
    }
    Err(ThinkingRefusal::EffortNotSupported {
        model,
        effort: think.to_string(),
        adaptive,
    })
}
