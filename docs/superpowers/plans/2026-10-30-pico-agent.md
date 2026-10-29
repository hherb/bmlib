# PICO Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port bmlibrarian's PICO agent into bmlib's `quality` package, in Python and Rust. Each of its four parts carries quotes verified against the paper by `find_passage`.

**Architecture:** A `PicoAgent` beside `CochraneAssessor` in each language, built the same way:
- **Model calls:** through `chat_json` (Python's `BaseAgent`, Rust's `JsonChat` seam).
- **Oversized text:** condensed by the Cochrane assessor's map-reduce, made generic over its prompts.
- **Reading the reply:** pure functions that read it and verify quotes against the original text.

Python is the reference. Rust is held to it by a whole-agent oracle over scripted model replies.

**Tech Stack:** Python 3.11 (pytest, ruff 0.15.20, mypy 2.3.0), Rust (`bmlib` crate, `serde_json`), `bmlib.citations.find_passage` and `bmlib::citations::find_passage`.

**Spec:** `docs/superpowers/specs/2026-10-30-pico-agent-design.md`. Read it first; every rule below argues from it.

## Global Constraints

- Every new source file carries the AGPL-3 header (copy from any existing file).
- Use `uv`, never `pip`. Run Python as `env -u VIRTUAL_ENV uv run …` in the worktree `/Users/hherb/src/bmlib-pico`.
- Lint with `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`. Run `uv run mypy` with no arguments. Rust needs `cargo test -p bmlib` and `cargo clippy -p bmlib --all-targets -- -D warnings` under `rust/`.
- Citation offsets are **code points** into the caller's `text`: Python `str` indices and Rust `char` indices, never bytes.
- `min_similarity` is required on `extract`, with no default anywhere. An invalid value raises `ValueError` in Python and returns `PicoError::InvalidSimilarity` in Rust, **before any model call**.
- Nothing is truncated. Text over `condense_config.max_context_chars` (default `DEFAULT_CONDENSE_THRESHOLD_CHARS`, 48,000) is condensed. Quotes are always verified against the **original** text.
- No extraction is discarded for low confidence; there is no `min_confidence` parameter.
- The prompts are byte-identical in the two languages. The oracle pins them through the recorded messages.
- Defaults match `CochraneAssessor`: temperature 0.1, max_tokens 4096, max_retries 3, unusable-parse attempts 2.
- Dataclass models get `to_dict()`/`from_dict()`. Rust models get `to_json() -> serde_json::Value` producing Python's `to_dict()` exactly.

## Review Focus

1. **Characters outside the BMP** (`𝛼`, an emoji) before a quote. The span must be the same code-point offsets in both languages (oracle case `astral_before_quote`, Task 7).
2. **`quotes` arriving as a single string, not a list.** It reads as no quotes and counts one dropped, and the part is *unverified*, never a crash (Python Task 3 test `test_quotes_as_a_bare_string`; oracle case `quotes_bare_string`).
3. **`reported: false` sent together with quotes.** The part is *not reported*, its quotes are ignored, and it is left out of the overall mean (Task 3 test `test_not_reported_ignores_its_quotes`; oracle case `not_reported_with_quotes`).
4. **A reply missing a part on the first try and whole on the second** succeeds, using two model calls (Task 4 test `test_a_missing_part_is_retried_once`; oracle case `missing_part_then_whole`).
5. **A title with blank text.** The model is still called; every quote is then lost and every reported part is *unverified* (Task 4 test `test_blank_text_with_a_title_still_calls_the_model`).

---

### Task 1: Python `chat_json` raises one type per cause

**Files:**
- Modify: `bmlib/agents/base.py`: add the exception classes, then raise them in `chat_json` (both truncation raises, the shape raise, and the final raise after the loop).
- Modify: `bmlib/agents/__init__.py`: export the five names.
- Test: `tests/test_agents.py`

**Interfaces:**
- Produces:
  ```python
  class ChatJsonError(ValueError): ...          # base; still a ValueError
  class ChatJsonTruncated(ChatJsonError): ...
  class ChatJsonWrongShape(ChatJsonError): ...
  class ChatJsonEmpty(ChatJsonError): ...
  class ChatJsonUnparseable(ChatJsonError): ...
  ```
  The final raise after the retries are spent uses the class of the **last** attempt's failure: truncated, wrong shape, empty or unparseable. It keeps today's message text unchanged. A provider exception from `self.chat` still propagates as raised.

- [ ] **Step 1: Write the failing tests.** In `tests/test_agents.py`, add a class `TestChatJsonSaysWhy` with these tests:
  - `test_truncation_at_temperature_zero_is_truncated`
  - `test_retried_truncation_ends_truncated` (temperature 0.5, three truncated replies)
  - `test_wrong_shape_is_wrong_shape`
  - `test_three_empty_replies_are_empty`
  - `test_three_unparseable_replies_are_unparseable`
  - `test_the_last_failure_names_the_class`: empty, then unparseable, then empty gives `ChatJsonEmpty`.
  - `test_every_class_is_still_a_value_error`

  Each asserts with `pytest.raises(<Class>)`, and the old message substring still matches (e.g. `"unparseable response"`). Patch `bmlib.agents.base.time.sleep`. Use the fake-LLM pattern the file already uses.
- [ ] **Step 2: Run them.** `env -u VIRTUAL_ENV uv run pytest tests/test_agents.py -k ChatJsonSaysWhy -v`. Expected: FAIL (the names do not exist).
- [ ] **Step 3: Implement.** Track the class beside `last_error` (e.g. `last_cls: type[ChatJsonError]`). `reject_shape` raises `ChatJsonWrongShape`, and the two truncation raises use `ChatJsonTruncated`. Update the docstring's *Raises*.
- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/test_agents.py tests/test_cochrane_assessor.py tests/test_quality.py -q`. Expected: all PASS, since every existing `except ValueError` still catches.
- [ ] **Step 5: Commit** `feat(agents): chat_json raises one ValueError subclass per cause`.

### Task 2: The condensation seam takes any prompts (both languages)

**Files:**
- Create: `bmlib/quality/_condense.py` (private, AGPL header)
- Modify: `bmlib/quality/cochrane_assessor.py`: `_condense` delegates to the new helper.
- Modify: `rust/bmlib/src/quality/cochrane_assessor.rs`: `LlmCondenser` stores its templates and query; the body of `fn condense` moves into `condense_checked`.
- Test: `tests/test_cochrane_assessor.py` (existing tests must stay green; add one test for the helper's refusals); `rust/bmlib/tests/cochrane.rs` (one test for `with_prompts`)

**Interfaces:**
- Produces (Python):
  ```python
  def condense_text(
      agent: BaseAgent, text: str, label: str, config: ProcessingConfig, *,
      extraction_prompt: str, consolidation_prompt: str, query: str,
  ) -> tuple[str, list[str], ProcessingStatus] | None
  ```
  This is exactly today's `CochraneAssessor._condense` body and logging, parameterised by prompts and query. The note wording becomes *"Source text was condensed before analysis; …"*. That changes the Cochrane note's wording, so update any test asserting *"before assessment"*. No Rust oracle case pins this note (verify with `grep -rn "before assessment" rust/`), and if one does, re-run the oracle in Task 7.
- Produces (Rust):
  ```rust
  impl<'a> LlmCondenser<'a> {
      pub fn with_prompts(model: &'a dyn ContextModel, templates: PromptTemplates,
                          query: &'static str, temperature: f64, max_tokens: i64)
          -> Result<Self, String>;   // validates templates like `new`
  }
  pub(crate) fn condense_checked(condenser: &mut dyn Condenser, text: &str, label: &str,
                                 config: &ProcessingConfig)
      -> Option<(String, Vec<String>, ProcessingStatus)>;
  ```
  `new` keeps its signature and Cochrane's prompts. The Rust note wording changes the same way as Python's.

- [ ] **Step 1: Write the tests.** Python: `test_condense_text_refuses_an_oversized_digest`, which feeds a fake processor result whose digest exceeds `config.max_context_chars` and expects `None`. Rust: `llm_condenser_with_prompts_uses_its_own_query`, which checks a recording `ContextModel` sees the custom query string.
- [ ] **Step 2: Run them.** Expected: FAIL (the helper and constructor do not exist).
- [ ] **Step 3: Implement** by moving code, not rewriting it. The Cochrane assessor calls `condense_text(self, text, label, self.condense_config, extraction_prompt=CONDENSE_EXTRACTION_PROMPT, …)`. Rust `CochraneAssessor::condense` calls `condense_checked`.
- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/test_cochrane_assessor.py -q` and `cd rust && cargo test -p bmlib --test cochrane --test cochrane_oracle`. Expected: PASS.
- [ ] **Step 5: Commit** `refactor(quality): condensation takes its prompts, for a second agent`.

### Task 3: Python PICO models, reply reading and quote verification

**Files:**
- Create: `bmlib/quality/pico_models.py`
- Create: `bmlib/quality/pico_agent.py` (this task: the module docstring and the pure functions only)
- Test: `tests/test_pico_agent.py`

**Interfaces:**
- Produces (`pico_models.py`):
  ```python
  class PicoPartState(Enum): VERIFIED = "verified"; UNVERIFIED = "unverified"; NOT_REPORTED = "not_reported"
  class PicoErrorKind(Enum): EMPTY_INPUT = "empty_input"; REQUEST_FAILED = "request_failed"
      TRUNCATED = "truncated"; UNUSABLE_REPLY = "unusable_reply"; CONDENSATION_FAILED = "condensation_failed"
  class PicoError(Exception):  # .kind: PicoErrorKind, message in str(); chained with `from`
      def __init__(self, kind: PicoErrorKind, message: str) -> None
  @dataclass(frozen=True) class PicoCitation: start: int; end: int; text: str; similarity: float; exact: bool
  @dataclass class PicoPart:
      description: str; state: PicoPartState; confidence: float | None
      citations: list[PicoCitation] = field(default_factory=list)
      quotes_not_found: list[str] = field(default_factory=list)   # the model's copies
      quotes_dropped: int = 0                                     # non-strings, blanks
  @dataclass class PicoExtraction:
      population: PicoPart; intervention: PicoPart; comparison: PicoPart; outcome: PicoPart
      study_type: str | None; sample_size: str | None; overall_confidence: float | None
      condensed_from_chars: int | None = None; condensation_status: str | None = None
      notes: list[str] = field(default_factory=list)
  @dataclass class PicoSuitability:
      is_suitable: bool; is_intervention_study: bool | None; has_comparison: bool | None
      study_type: str | None; confidence: float | None; rationale: str | None
      condensed_from_chars: int | None = None; condensation_status: str | None = None
      notes: list[str] = field(default_factory=list)
  ```
  Every dataclass has `to_dict()`/`from_dict()`. An enum serialises as its `.value`, a citation as a dict, and there is no `created_at` (the app stamps runs).
- Produces (`pico_agent.py`, public, pure):
  ```python
  PICO_PARTS: tuple[str, ...] = ("population", "intervention", "comparison", "outcome")
  def verify_quotes(quotes: object, text: str, min_similarity: float) -> tuple[list[PicoCitation], list[str], int]
  def read_part(value: object, text: str, min_similarity: float) -> PicoPart     # raises ValueError if not usable
  def read_extraction(data: dict[str, Any], text: str, min_similarity: float) -> PicoExtraction
  def read_suitability(data: dict[str, Any]) -> PicoSuitability                   # raises ValueError if is_suitable not bool
  ```
  The rules, all from the spec:
  - **`verify_quotes`** returns `(citations, not_found, dropped)`. A `quotes` value that is not a list counts 1 dropped. A member that is not a `str`, or is blank after `strip()`, counts 1 dropped. Each remaining quote goes to `find_passage(quote, text, min_similarity)`: found when `start is not None`, else appended to `not_found` verbatim. Citations keep the order the quotes came in, and a repeated `(start, end)` is kept once.
  - **`read_part`** raises `ValueError` unless `value` is a dict whose `description` is a `str`.
    - `reported`: a non-bool is treated as `True`.
    - `reported is False`: `NOT_REPORTED`, with no verification, no citations and quotes ignored.
    - Otherwise: `VERIFIED` if any citation was found, else `UNVERIFIED`.
    - `confidence`: through the Cochrane module's `_clamped_confidence` rule. Import it, renamed public as `clamped_confidence` in `cochrane_assessor.py`, keeping the old private name as an alias so nothing else moves.
  - **`read_extraction`**: all four parts are required, so `read_part` raises for a missing one. `study_type`/`sample_size` go through `as_text`. `overall_confidence` is the mean, in P, I, C, O order, of the confidences of the parts whose state is not `NOT_REPORTED` and whose confidence is not `None`, or `None` when there are none. Compute it as `sum(values) / len(values)` so Rust can reproduce it bit for bit.
  - **`read_suitability`**: `is_suitable` must be a `bool`, else `ValueError`. The rest go through `as_bool` / `as_text` / `clamped_confidence`, and a value that is absent or of the wrong type becomes `None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_pico_agent.py`, class `TestReadingAReply`, using `text = "Adults aged 40-65 were randomised to metformin or placebo."`:
  - `test_a_found_quote_is_the_papers_span`: the quote `"adults aged 40–65"` (en dash, lower case) gives `state is VERIFIED` and `citations[0].text == "Adults aged 40-65"`, with `start == 0`.
  - `test_some_found_some_lost`: one found and one paraphrase give `VERIFIED`, `len(citations) == 1`, and `quotes_not_found == ["<the paraphrase>"]`.
  - `test_none_found_is_unverified_and_keeps_the_description`: `UNVERIFIED`, with `description` unchanged.
  - `test_no_quotes_given_is_unverified`
  - `test_not_reported_ignores_its_quotes` (Review Focus 3): `NOT_REPORTED`, `citations == []`, `quotes_not_found == []`.
  - `test_quotes_as_a_bare_string` (Review Focus 2): `UNVERIFIED`, `quotes_dropped == 1`.
  - `test_a_non_string_quote_is_dropped_and_counted`: `[3, "  ", "metformin"]` gives `quotes_dropped == 2`, one citation.
  - `test_a_non_bool_reported_counts_as_reported`
  - `test_a_missing_part_raises`
  - `test_overall_confidence_skips_unreported_parts`: P 0.9, I 0.7, C not reported at 0.1, O unset gives `overall_confidence == (0.9 + 0.7) / 2`.
  - `test_confidence_is_clamped`: 1.4 gives 1.0; `True` gives `None`.
  - `test_suitability_requires_a_bool_is_suitable`: `"yes"` raises `ValueError`.
  - `test_suitability_reads_wrong_types_as_unstated`
  - `test_models_round_trip`: `PicoExtraction.from_dict(x.to_dict()) == x`, and the same for suitability.
- [ ] **Step 2: Run them.** `env -u VIRTUAL_ENV uv run pytest tests/test_pico_agent.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement** `pico_models.py` and the four functions.
- [ ] **Step 4: Run them again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(quality): PICO models, reply reading and quote verification`.

### Task 4: Python `PicoAgent`

**Files:**
- Modify: `bmlib/quality/pico_agent.py`
- Modify: `bmlib/quality/__init__.py`: export `PicoAgent`, the models, `PicoError`, `PicoErrorKind`, `PicoPartState`, `verify_quotes`, `PICO_PARTS`.
- Test: `tests/test_pico_agent.py`

**Interfaces:**
- Consumes: Task 1's exception classes, Task 2's `condense_text`, Task 3's readers.
- Produces:
  ```python
  SUITABILITY_SYSTEM_PROMPT, SUITABILITY_PROMPT, EXTRACTION_SYSTEM_PROMPT, EXTRACTION_PROMPT,
  PICO_CONDENSE_QUERY, PICO_CONDENSE_EXTRACTION_PROMPT, PICO_CONDENSE_CONSOLIDATION_PROMPT: str
  class PicoAgent(BaseAgent):
      def __init__(self, llm: LLMClient, model: str, template_engine: TemplateEngine | None = None,
                   temperature: float = 0.1, max_tokens: int = 4096,
                   condense_config: ProcessingConfig | None = None) -> None
      def check_suitability(self, title: str | None, text: str | None) -> PicoSuitability
      def extract(self, title: str | None, text: str | None, *, min_similarity: float) -> PicoExtraction
  ```
  The user message is `f"{PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{text}"`, with the prompt's own response-format block last. It is built with plain concatenation, never `str.format` over paper text.

  The prompts start from upstream's wording (`/Users/hherb/src/bmlibrarian/src/bmlibrarian/agents/pico_agent.py`), with these changes:
  - The suitability prompt drops the `[:3000]` slice.
  - The extraction prompt's reply format becomes one object per part, `{"description": str, "reported": bool, "confidence": number, "quotes": [str, ...]}`, plus top-level `study_type` and `sample_size`. `overall_confidence` is removed.
  - The extraction prompt says: quote **verbatim**, 1–3 quotes per reported part; `reported: false` only when the paper says nothing on the part; and a single-arm study's comparison is *reported*, quoting the sentence that says so.
  - The condensation prompts ask for PICO evidence as verbatim quotes, in the shape of the Cochrane `CONDENSE_*` prompts.

  Both calls follow `CochraneAssessor.assess`. Strip `title` and `text`. If both are empty, raise `PicoError(EMPTY_INPUT)` with no call made. Condense through `condense_text(..., label=title[:60])` when `len(text) > condense_config.max_context_chars`, and on `None` raise `PicoError(CONDENSATION_FAILED)`. Then loop up to 2 attempts:
  - `chat_json(..., require_dict=True)`.
  - `ChatJsonTruncated` → `TRUNCATED`.
  - Any other `ChatJsonError` → `UNUSABLE_REPLY`.
  - Any other `Exception` → `REQUEST_FAILED`.
  - Every raise uses `from exc`.
  - A reader's `ValueError` → log a warning and retry; after the 2nd → `UNUSABLE_REPLY`.

  `extract` validates `min_similarity` first: it raises `ValueError` unless `0 < x <= 1` and the value is a non-bool real. It then passes the **original** stripped text to `read_extraction` and fills `condensed_from_chars`, `condensation_status` and `notes` from the condensation.

- [ ] **Step 1: Write the failing tests**, class `TestTheAgent`, ported from upstream's `test_pico_agent.py` where the behaviour survives, with the fake-LLM helper from `tests/test_cochrane_assessor.py::make_assessor`:
  - `test_a_whole_extraction` (upstream `test_extract_pico_success`, with quotes)
  - `test_low_confidence_is_kept` (rewritten from upstream's `test_extract_pico_low_confidence`)
  - `test_empty_input_makes_no_call` (upstream `..._no_abstract`): `PicoError` with kind `EMPTY_INPUT`, and `llm.chat.call_count == 0`.
  - `test_a_missing_part_is_retried_once` (Review Focus 4; rewritten from `..._missing_fields`): two calls, then success.
  - `test_a_part_missing_twice_is_unusable`
  - `test_unparseable_is_unusable` (`..._json_parse_error`)
  - `test_a_provider_exception_is_request_failed` (`..._connection_failure`): `ConnectionError` from `llm.chat`, with `__cause__` kept.
  - `test_truncation_is_truncated`: `stop_reason="max_tokens"` at temperature 0.
  - `test_long_text_is_condensed_and_verified_against_the_original` (rewritten from `test_text_not_truncated`). Set `condense_config.max_context_chars` to 100 and monkeypatch `bmlib.quality.pico_agent.condense_text` to return a digest without the quoted sentence. A quote present in the original is `VERIFIED`; `condensed_from_chars == len(text)`.
  - `test_condensation_failure_is_reported`
  - `test_the_text_is_sent_whole`: a 40,000-char text appears whole in the user message.
  - `test_blank_text_with_a_title_still_calls_the_model` (Review Focus 5)
  - `test_an_invalid_min_similarity_makes_no_call`: `0`, `1.5`, `True`, `nan`.
  - Suitability, ported from `TestPICOSuitability`: `test_suitability_rct`, `test_suitability_systematic_review`, `test_suitability_case_report`, `test_suitability_empty_input`, `test_suitability_request_failed`, `test_suitability_unparseable`, `test_suitability_low_confidence_kept`, `test_suitability_text_not_cut`. The last one has a 5,000-char abstract whose tail reaches the prompt.

  Not ported: initialization, batch, progress, stats, the summary formatter, both exports, the callback, and the `to_dict` and dataclass tests (Task 3 covers round-trips).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/test_pico_agent.py -v && uv run mypy`. Expected: PASS, and mypy clean.
- [ ] **Step 5: Commit** `feat(quality): PicoAgent, suitability and extraction with verified quotes`.

### Task 5: Rust models, reply reading and quote verification

**Files:**
- Create: `rust/bmlib/src/quality/pico_models.rs`
- Create: `rust/bmlib/src/quality/pico_agent.rs` (this task: the pure functions)
- Modify: `rust/bmlib/src/quality/mod.rs`: add `pub mod` lines and re-exports.
- Modify: `rust/bmlib/src/quality/cochrane_assessor.rs`: make `clamped_confidence` `pub(crate)`.
- Test: unit tests in `pico_agent.rs` (`#[cfg(test)]`). The cross-language proof is Task 7.

**Interfaces:**
- Produces: Rust mirrors of Task 3, using the same names in Rust case. `PicoPartState`, `PicoErrorKind`'s variants as `PicoError`:
  ```rust
  pub enum PicoError { EmptyInput, RequestFailed(String), Truncated(String), UnusableReply(String),
                       CondensationFailed(String), InvalidSimilarity(f64) }   // Display + Error
  pub struct PicoCitation { pub start: usize, pub end: usize, pub text: String, pub similarity: f64, pub exact: bool }
  pub struct PicoPart { pub description: String, pub state: PicoPartState, pub confidence: Option<f64>,
                        pub citations: Vec<PicoCitation>, pub quotes_not_found: Vec<String>, pub quotes_dropped: usize }
  pub struct PicoExtraction { /* Task 3's fields, Option<i64> for condensed_from_chars */ }
  pub struct PicoSuitability { /* Task 3's fields */ }
  pub const PICO_PARTS: [&str; 4];
  pub fn verify_quotes(quotes: Option<&Value>, text: &str, min_similarity: f64)
      -> (Vec<PicoCitation>, Vec<String>, usize);
  pub fn read_part(value: Option<&Value>, text: &str, min_similarity: f64) -> Result<PicoPart, String>;
  pub fn read_extraction(data: &Value, text: &str, min_similarity: f64) -> Result<PicoExtraction, String>;
  pub fn read_suitability(data: &Value) -> Result<PicoSuitability, String>;
  ```
  Each model has `to_json(&self) -> Value` giving Python's `to_dict()` key for key. An absent `quotes` (`None`) is "no quotes, 0 dropped". The rules are Task 3's.
- [ ] **Step 1: Write the failing unit tests**: the Rust twins of `test_a_found_quote_is_the_papers_span`, `test_quotes_as_a_bare_string` and `test_overall_confidence_skips_unreported_parts`.
- [ ] **Step 2: Run them.** `cd rust && cargo test -p bmlib pico`. Expected: FAIL (compile).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cargo test -p bmlib pico && cargo clippy -p bmlib --all-targets -- -D warnings`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(rust/quality): PICO models, reply reading and quote verification`.

### Task 6: Rust `PicoAgent`

**Files:**
- Modify: `rust/bmlib/src/quality/pico_agent.rs`, `rust/bmlib/src/quality/mod.rs`

**Interfaces:**
- Consumes: `JsonChat`, `Condenser`, `LlmCondenser::with_prompts`, `condense_checked` (Task 2); Task 5's readers.
- Produces:
  ```rust
  pub const SUITABILITY_SYSTEM_PROMPT: &str; /* … the seven prompts, byte-identical to Python */
  pub struct PicoAgent<'a> { /* chat, condenser, temperature, max_tokens, max_retries, condense_config */ }
  impl<'a> PicoAgent<'a> {
      pub fn new(chat: &'a mut dyn JsonChat) -> Self;
      pub fn with_condenser(self, condenser: &'a mut dyn Condenser) -> Self;
      pub fn with_temperature(self, t: f64) -> Self;  pub fn with_max_tokens(self, n: i64) -> Self;
      pub fn with_max_retries(self, n: usize) -> Self; pub fn with_condense_config(self, c: ProcessingConfig) -> Self;
      pub fn pico_condenser(model: &'a dyn ContextModel, temperature: f64, max_tokens: i64)
          -> Result<LlmCondenser<'a>, String>;   // LlmCondenser::with_prompts over the PICO prompts
      pub fn check_suitability(&mut self, title: &str, text: &str) -> Result<PicoSuitability, PicoError>;
      pub fn extract(&mut self, title: &str, text: &str, min_similarity: f64) -> Result<PicoExtraction, PicoError>;
  }
  ```
  The error mapping follows Task 4: `ChatJsonError::Truncated` → `Truncated`; `WrongShape`/`Empty`/`Unparseable` → `UnusableReply`; `Transport` → `RequestFailed`. Each carries the error's `to_string()`. Oversized text with no condenser configured → `CondensationFailed("no condenser configured")`. Lengths are compared as `chars().count()`.
- [ ] **Step 1: Write the failing tests** in `rust/bmlib/tests/pico_agent.rs`:
  - `oversized_text_without_a_condenser_is_condensation_failed`
  - `invalid_similarity_makes_no_call`
  - `empty_input_makes_no_call`

  Use `ScriptedChat`, moved from `rust/bmlib/tests/quality_agents.rs` into `rust/bmlib/tests/common/scripted_chat.rs` (declared in `common/mod.rs`; update `quality_agents.rs` to import it).
- [ ] **Step 2: Run them.** `cargo test -p bmlib --test pico_agent`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cargo test -p bmlib --test pico_agent --test quality_agents && cargo clippy -p bmlib --all-targets -- -D warnings`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(rust/quality): PicoAgent`.

### Task 7: The oracle — Rust held to Python over whole agent runs

**Files:**
- Create: `rust/oracle/dump_pico_agent.py`, `rust/oracle/pico_agent_cases.json`
- Create (generated): `rust/bmlib/tests/data/pico_agent_expected.json`
- Create: `rust/bmlib/tests/pico_oracle.rs`
- Modify: `scripts/rerun_rust_oracle.py`: add `("dump_pico_agent.py", "pico_agent_cases.json", "pico_agent_expected.json")` to `CORPORA`.

**Interfaces:**
- **A case** is `{"name", "fn": "extract" | "suitability", "args": {"title", "text", "min_similarity"?, "replies": [str], "stop_reasons"?: [str|null], "max_context_chars"?: int, "digest"?: str}}`.
- **The dumper** builds `PicoAgent` over a fake `llm` whose `chat` returns the scripted `LLMResponse`s in order, with `time.sleep` patched to a no-op. When `digest` is given, it patches `condense_text` to return `(digest, [], ProcessingStatus.COMPLETED)`.
- **What the dumper records:** `{"result": obj.to_dict()}` or `{"error": kind.value}`, plus `"messages"`. That is the list of `[{"role", "content"}]` for each `llm.chat` call, read from the mock's `call_args_list`.
- **The Rust test** replays each case through `ScriptedChat`. When `digest` is set, it uses a fake `Condenser` returning a `ProcessingResult` with that content and status `Completed`. It asserts equality of the result JSON (or the error kind's wire name) and of the messages ScriptedChat saw.
- **Cases (at least these):**
  - `all_verified`
  - `one_part_unverified`
  - `found_and_lost_in_one_part`
  - `not_reported_with_quotes` (Review Focus 3)
  - `quotes_bare_string` (Review Focus 2)
  - `astral_before_quote` (Review Focus 1): text `"𝛼-blockers 🙂: adults aged 40-65 were enrolled."`
  - `single_arm_comparison_reported`
  - `missing_part_then_whole` (Review Focus 4)
  - `missing_part_twice`
  - `unparseable_three_times`
  - `truncated_at_zero`
  - `empty_input`
  - `condensed_quote_found_in_original`
  - `overall_confidence_mean`
  - `suitable_rct`
  - `unsuitable_review`
  - `suitability_wrong_types`
  - `suitability_is_suitable_not_bool`

- [ ] **Step 1: Write the cases file and the dumper. Generate** with `env -u VIRTUAL_ENV uv run python scripts/rerun_rust_oracle.py --only pico_agent --write`, after checking the script's `--only` argument form in its docstring.
- [ ] **Step 2: Write `pico_oracle.rs`** following `rust/bmlib/tests/cochrane_oracle.rs`'s loader. Run `cargo test -p bmlib --test pico_oracle`. Expected: PASS. Any mismatch is a Rust bug unless it reveals Python behaviour the spec forbids. Fix the side that is wrong, and never edit the expected file by hand.
- [ ] **Step 3: Run** `env -u VIRTUAL_ENV uv run python scripts/rerun_rust_oracle.py`. Expected: every corpus unchanged except the new one, or, if Task 2's note wording moved a Cochrane corpus, regenerate it and say so in the commit.
- [ ] **Step 4: Commit** `test(rust): the PICO agent oracle`.

### Task 8: Documentation and the session records

**Files:**
- Modify: `docs/manual/quality.md`: a *PICO agent* section with the API, the three part states, the error kinds, the condensing rule and a usage example. Add the new exception classes to `docs/manual/agents.md`.
- Modify: `CHANGELOG.md`: an `[Unreleased]` entry, *The PICO agent, with verified quotes (StudyAnalyzer item 7)*, carrying the argument (the spec's decisions and the maintainer's) and the `chat_json` exception classes. The condensation note's wording change goes under *Changed*.
- Modify: `ROADMAP.md`: a `✅ Done *(unreleased)*` row beside item 11's.
- Modify: `HANDOVER.md`: *This session* becomes item 7, and the previous section is item 11. Add what StudyAnalyzer plan 2c builds on: `check_suitability` then `extract`, the states, `min_similarity` from 2b's measurement, the error kinds. Re-count the open issues.
- Modify: `CLAUDE.md`: add `pico_agent.py`, `pico_models.py` and `_condense.py` to the `quality/` tree, and `test_pico_agent.py` to the test-file mapping.

- [ ] **Step 1: Write the docs.** Grep `docs/manual/` for every new public name, and each must appear.
- [ ] **Step 2: Run the full gates**:
  ```bash
  env -u VIRTUAL_ENV uv run pytest tests/ -q
  uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
  env -u VIRTUAL_ENV uv run mypy
  env -u VIRTUAL_ENV uv run python scripts/rerun_rust_oracle.py
  cd rust && cargo test -p bmlib && cargo clippy -p bmlib --all-targets -- -D warnings && cargo fmt --check
  ```
  Expected: all green, with the test count recorded in HANDOVER.
- [ ] **Step 3: Commit** `docs: the PICO agent (StudyAnalyzer item 7)`. Push, and open the PR to `main` with the spec and plan linked. Stop there: the maintainer merges.
