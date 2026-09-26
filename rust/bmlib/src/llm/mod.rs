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

//! Unified LLM access — the **pure** half, so far.
//!
//! A port of `bmlib/llm/` (5,114 Python lines). What is here needs no
//! provider, client or network; the two wire protocols described in the port
//! plan's §4 are a later phase.
//!
//! | Python | Here | Status |
//! |---|---|---|
//! | `llm/text_utils.py` | [`text_utils`] | ported |
//! | `llm/json_repair.py` | [`json_repair`] | ported (fixes #299) |
//! | `llm/utils.py` | [`utils`] | ported |
//! | `llm/providers/*` | — | phase 3 |
//! | `llm/client.py` | — | phase 3 |

pub mod client;
pub mod data_types;
pub mod json_repair;
pub mod protocol;
pub mod text_utils;
pub mod token_tracker;
pub mod utils;

pub use client::{
    chat_body, chat_headers, chat_url, parse_model_string, parse_response, provider_specs,
    resolve_target, supports_tools, ChatError, ChatRequest, LlmClient, ProviderSpec, Target,
    DEFAULT_PROVIDER, TOOL_CAPABLE_PROVIDERS,
};
pub use data_types::{
    BatchEmbeddingResponse, EmbeddingResponse, LLMMessage, LLMResponse, LLMToolCall,
    LLMToolDefinition, Role,
};
pub use json_repair::{
    apply_repairs, extract_and_repair_json, repair_json, repair_json_default, safe_json_loads,
    salvage_json_fields, JsonRepairError, RepairError, MAX_JSON_LENGTH, MAX_REPAIR_ATTEMPTS,
    MAX_SALVAGE_MATCHES,
};
pub use protocol::{
    json_dumps_python, messages_to_anthropic, messages_to_openai, parse_anthropic_response,
    parse_openai_response, reasoning_from_openai, split_think_tags, tool_arguments_from_openai,
    tool_choice_to_openai, tool_def_to_openai, Protocol, ProtocolError, ANTHROPIC_VERSION,
};
pub use text_utils::{
    chunk_text, combine_title_and_text, get_text_with_priority, process_with_map_reduce,
    process_with_rolling_summary, ChunkInfo, ChunkerError, TextChunk, TextChunker,
    DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, DEFAULT_MIN_CHUNK_SIZE,
};
pub use token_tracker::{
    reset_token_tracker, with_token_tracker, ModelUsage, TokenTracker, TokenUsageRecord,
    TokenUsageSummary,
};
pub use utils::{extract_json, iter_json_spans};
