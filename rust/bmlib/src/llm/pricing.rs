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

//! What a call cost, and the price tables it is computed from.
//!
//! # Why the tables are here when the plan called them droppable
//!
//! The plan says the per-provider `MODEL_PRICING` tables are *"data that goes
//! stale"* and *"a product decision, not a porting one"* — while also saying to
//! **keep the cost calculation**. Those two together are only satisfiable if the
//! tables exist, because a cost calculation with no prices returns zero for ever,
//! which is indistinguishable from a free model. So they are ported, and the
//! staleness is handled the way the Python handles it: an **unknown model is billed
//! at its provider's fallback rate**, not at zero.
//!
//! # The prices are the Python's, verbatim
//!
//! Generated from the provider modules rather than transcribed, because a price
//! typed by hand is a price that is wrong in a way nothing detects — the cost
//! simply comes out slightly off. `tests/cost.rs` asserts the tables against the
//! Python's own values, so a divergence fails rather than drifts.
//!
//! Rates are **US dollars per million tokens**, which is the unit the providers
//! publish and the unit the Python stores.

use std::collections::BTreeMap;
use std::sync::OnceLock;

/// One model's rates, in dollars per million tokens.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ModelPricing {
    /// Dollars per million input tokens.
    pub input_cost: f64,
    /// Dollars per million output tokens.
    pub output_cost: f64,
}

/// The rate applied to a model a provider's table does not name.
const FALLBACK_PRICING: &[(&str, f64, f64)] = &[
    ("anthropic", 3.0, 15.0),
    ("deepseek", 1.0, 3.0),
    ("gemini", 1.0, 3.0),
    ("mistral", 1.0, 3.0),
    ("openai", 1.0, 3.0),
];

/// A local model server's rate: free.
///
/// **Zero and not a fallback**, which is the distinction that matters. A hosted
/// model missing from a table is billed at its provider's *estimate* so a stale
/// table does not silently report a paid call as free; a model served on the
/// caller's own machine genuinely costs nothing, and reporting an estimate for it
/// would invent a charge.
const LOCAL_PRICING: ModelPricing = ModelPricing {
    input_cost: 0.0,
    output_cost: 0.0,
};

/// The price table, keyed by `(provider, model)`.
///
/// A flat map rather than per-provider structs, because the protocol carries the
/// differences in this port and a price is not one of them — the one thing that
/// varies is the fallback, and that is a second small table.
fn pricing() -> &'static BTreeMap<(&'static str, &'static str), ModelPricing> {
    static TABLE: OnceLock<BTreeMap<(&'static str, &'static str), ModelPricing>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut table = BTreeMap::new();
        for (provider, model, input, output) in [
            ("anthropic", "claude-3-5-haiku-20241022", 1.0, 5.0),
            ("anthropic", "claude-3-5-sonnet-20241022", 3.0, 15.0),
            ("anthropic", "claude-3-haiku-20240307", 0.25, 1.25),
            ("anthropic", "claude-3-opus-20240229", 15.0, 75.0),
            ("anthropic", "claude-3-sonnet-20240229", 3.0, 15.0),
            ("anthropic", "claude-opus-4-20250514", 15.0, 75.0),
            ("anthropic", "claude-sonnet-4-20250514", 3.0, 15.0),
            ("anthropic", "claude-sonnet-4-5-20250929", 3.0, 15.0),
            ("deepseek", "deepseek-chat", 0.27, 1.1),
            ("deepseek", "deepseek-reasoner", 0.55, 2.19),
            ("gemini", "gemini-1.5-flash", 0.075, 0.3),
            ("gemini", "gemini-1.5-pro", 1.25, 5.0),
            ("gemini", "gemini-2.0-flash", 0.1, 0.4),
            ("gemini", "gemini-2.0-flash-lite", 0.0, 0.0),
            ("gemini", "gemini-2.5-flash-preview-05-20", 0.15, 0.6),
            ("gemini", "gemini-2.5-pro-preview-05-06", 1.25, 10.0),
            ("mistral", "codestral-latest", 0.3, 0.9),
            ("mistral", "ministral-8b-latest", 0.1, 0.1),
            ("mistral", "mistral-large-latest", 2.0, 6.0),
            ("mistral", "mistral-small-latest", 0.1, 0.3),
            ("mistral", "pixtral-large-latest", 2.0, 6.0),
            ("openai", "gpt-4-turbo", 10.0, 30.0),
            ("openai", "gpt-4o", 2.5, 10.0),
            ("openai", "gpt-4o-mini", 0.15, 0.6),
            ("openai", "o1", 15.0, 60.0),
            ("openai", "o1-mini", 3.0, 12.0),
            ("openai", "o3-mini", 1.1, 4.4),
        ] {
            table.insert(
                (provider, model),
                ModelPricing {
                    input_cost: input,
                    output_cost: output,
                },
            );
        }
        table
    })
}

/// The fallback rate for a provider, when its table does not name a model.
#[must_use]
pub fn fallback_pricing(provider: &str) -> ModelPricing {
    FALLBACK_PRICING
        .iter()
        .find(|(name, _, _)| *name == provider)
        .map(|(_, input, output)| ModelPricing {
            input_cost: *input,
            output_cost: *output,
        })
        .unwrap_or(LOCAL_PRICING)
}

/// The rate for one model.
///
/// A **local** provider is free; a hosted model the table does not name takes its
/// provider's fallback rather than zero. The two rules are separate because they
/// answer different questions — *"is this free?"* and *"what should I guess?"* —
/// and collapsing them makes one of the two wrong.
#[must_use]
pub fn model_pricing(provider: &str, model: &str) -> ModelPricing {
    if provider_is_local(provider) {
        return LOCAL_PRICING;
    }
    pricing()
        .get(&(provider, model))
        .copied()
        .unwrap_or_else(|| fallback_pricing(provider))
}

/// Whether a provider serves models from the caller's own machine.
#[must_use]
pub fn provider_is_local(provider: &str) -> bool {
    crate::llm::client::provider_specs()
        .get(provider)
        .is_some_and(|spec| spec.is_local)
}

/// What one call cost.
///
/// The Python's arithmetic, including its unit: `tokens / 1_000_000 * rate`.
/// A cost is a **float and not an integer of cents**, because the rates are
/// fractional and rounding here would lose the fraction before a caller can sum a
/// day's calls.
#[must_use]
pub fn calculate_cost(provider: &str, model: &str, input_tokens: i64, output_tokens: i64) -> f64 {
    let rates = model_pricing(provider, model);
    (input_tokens as f64 / 1_000_000.0) * rates.input_cost
        + (output_tokens as f64 / 1_000_000.0) * rates.output_cost
}

/// Every `(provider, model)` the table prices.
#[must_use]
pub fn priced_models() -> Vec<(&'static str, &'static str)> {
    pricing().keys().copied().collect()
}
