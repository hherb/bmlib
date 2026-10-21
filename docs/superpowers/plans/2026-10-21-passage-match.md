# Passage matcher (`find_passage`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `find_passage` to bmlib in Python and Rust, on par. It locates a language model's near-verbatim quote in a source text and returns the source's own span and wording, or rejects a paraphrase.

**Architecture:** Python: bmlibrarian's `utils/passage_match.py` moves unchanged into `bmlib.citations.passage_match` (stdlib only, as the rest of `citations/`). Rust: `citations::passage_match`, with a crate-private port of `difflib.SequenceMatcher` in `citations::sequence_matcher`. Parity is held two ways, following bmlib's precedents:
- a **hand-written contract fixture** that both test suites assert, as retraction status (#504) does;
- a **Python-generated oracle corpus** that the Rust suite asserts with exact equality, including the `f64` similarity, as every Rust port module does (`scripts/rerun_rust_oracle.py`).

**Tech Stack:** Python ≥ 3.11 stdlib (`difflib`, `unicodedata`); Rust 1.85 / edition 2021, `unicode-normalization` 0.1 (already a dependency), `serde_json` in tests.

**Spec:** `/Users/hherb/src/studyanalyzer/docs/superpowers/specs/2026-10-21-studyanalyzer-design.md` — §6.3 (citations), §7 item 2. Source being ported: `/Users/hherb/src/bmlibrarian/src/bmlibrarian/utils/passage_match.py` and `/Users/hherb/src/bmlibrarian/tests/test_passage_match.py` (72 tests once parametrised cases are expanded).

## Global Constraints

- Work on branch `feat/passage-match` off `main` in `~/src/bmlib`. One PR.
- Every new source and test file carries bmlib's AGPL header (copy it from `bmlib/citations/parser.py` / `rust/bmlib/src/citations/parser.rs`).
- Python `bmlib.citations` stays **stdlib only**. Rust adds **no new dependency**.
- **Offsets are Unicode code-point indices** in both languages: Python `str` indices, and Rust `char` indices — **never byte offsets**.
- **Whitespace is Python's `str.isspace`**. In Rust: `c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)`. Two private copies of this rule already exist (`fulltext/service.rs:444`, `fulltext/jats_reader.rs:1543`); add a third private one in `passage_match.rs` rather than refactoring them.
- **"Combining"** means Unicode canonical combining class ≠ 0: Python `unicodedata.combining(c)`, Rust `unicode_normalization::char::canonical_combining_class(c)`.
- Constants are copied unchanged: `SEED_MIN_LENGTH = 4`, `SEED_MAX_OCCURRENCES = 64`, `MAX_CANDIDATE_REGIONS = 5`, `REFINEMENT_PASSES = 2`, `EDGE_BLOCK_MIN_LENGTH = 2 * SEED_MIN_LENGTH`, `EDGE_SEARCH_MARGIN = 16`. So are the typographic fold tables and the negation marks `U+0338 U+20D2 U+20E5`.
- **bmlib sets no default threshold**: callers pass `min_similarity`. bmlibrarian's `CITATION_MIN_SIMILARITY = 0.95` is where StudyAnalyzer starts. Measuring the threshold is not part of this plan.
- bmlibrarian is not modified; it keeps its own copy.
- Gates before the PR: `uv run pytest tests/ -q`, `uv run ruff check .`, `uv run mypy bmlib`, `cd rust && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test -p bmlib`, `.venv/bin/python scripts/rerun_rust_oracle.py`.

## Review Focus

1. **Characters outside the Basic Multilingual Plane** (`𝛂` U+1D6C2, emoji) before a match must give the same `start`/`end` in Rust as in Python. This is the place a Rust port slips into byte offsets. *Pinned by contract case `non-bmp-before-match` (Task 2).*
2. **A quote that crosses a paragraph break**, where the text has `"\n\n"` and the quote has one space, is an **exact** match whose span includes the break. *Pinned by contract case `across-paragraph-break` and Python test `test_quote_across_paragraph_break_matches_exactly` (Task 1).*
3. **A quote the source contains twice** returns the **first** occurrence. *Pinned by contract case `repeated-quote-first-occurrence` and `test_repeated_quote_returns_first_occurrence` (Task 1).*
4. **An elided quote** (`"first sentence … last sentence"`, about 3 000 characters apart in the source) is **rejected**; it never highlights the long span between. *Pinned by contract case `elided-quote-rejected` and `test_elided_quote_is_rejected` (Task 1).*
5. **Information separators U+001C–U+001F**, as found in PDF-extracted text, count as whitespace in both languages, although Rust's `is_whitespace` says otherwise. *Pinned by contract case `information-separator-is-space` and `test_information_separators_are_whitespace` (Task 1).*

---

### Task 1: Python `bmlib.citations.passage_match`

**Files:**
- Create: `bmlib/citations/passage_match.py`: bmlibrarian's module verbatim, with only the header swapped to bmlib's.
- Create: `tests/test_passage_match.py`: bmlibrarian's test file, with only the import path changed. Then add the four Review Focus tests below.
- Modify: `bmlib/citations/__init__.py`: export `PassageMatch`, `find_passage`, `occurs_once`, `normalize_with_offsets`, `fold_char`, `fits_text`, `max_edit_points` (add them to `__all__` if the module uses one).
- Modify: `CLAUDE.md`: add `passage_match.py` to the `citations/` directory tree with the line "find_passage — locate an LLM's near-verbatim quote, return the source's own span".
- Modify: `docs/manual/citations.md`: add a "Locating quoted passages" section with a usage example, what is forgiven (case, whitespace, typographic quotes and dashes, accents, compatibility forms, invisible characters), and what `similarity` 0.0 means (no candidate location, as opposed to a near miss).
- Modify: `CHANGELOG.md` → `[Unreleased]` → `### Added`.

**Interfaces:**
- Produces (Python, unchanged from bmlibrarian):
  - `find_passage(passage: str, text: str, min_similarity: float) -> PassageMatch`
  - `PassageMatch(similarity, start, end, text, exact)` with the `.matched` property
  - `occurs_once(fragment: str, text: str) -> bool`
  - `normalize_with_offsets(text: str) -> tuple[str, list[int]]`
  - `fold_char(char: str) -> str`
  - `fits_text(passage_length: int, text_length: int, min_similarity: float) -> bool`
  - `max_edit_points(passage_length: int, min_similarity: float) -> int`

- [ ] **Step 1: Copy the test file and change its import to `from bmlib.citations.passage_match import …`**

- [ ] **Step 2: Run it and confirm it fails on the import**

Run: `uv run pytest tests/test_passage_match.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'bmlib.citations.passage_match'`

- [ ] **Step 3: Copy the module, swap the header, and add the exports**

- [ ] **Step 4: Run it and confirm all 72 pass**

Run: `uv run pytest tests/test_passage_match.py -q`
Expected: `72 passed`

- [ ] **Step 5: Add the Review Focus tests to `tests/test_passage_match.py`**

```python
def test_quote_across_paragraph_break_matches_exactly() -> None:
    text = "Results\n\nAmong patients aged 75 years or older,\n\nthe hazard ratio was 0.91."
    match = find_passage(
        "Among patients aged 75 years or older, the hazard ratio was 0.91.", text, 0.95
    )
    assert match.exact
    assert match.text == "Among patients aged 75 years or older,\n\nthe hazard ratio was 0.91."


def test_repeated_quote_returns_first_occurrence() -> None:
    text = "Abstract. The effect was null. Discussion. The effect was null."
    match = find_passage("The effect was null.", text, 0.95)
    assert match.start == text.index("The effect was null.")


def test_elided_quote_is_rejected() -> None:
    first = "Patients were randomly assigned to drug X or warfarin."
    last = "Major bleeding occurred in 3.1% versus 2.2% of patients."
    filler = " ".join(SENTENCES) * 3  # about 3 000 characters between the two
    text = f"{first} {filler} {last}"
    assert not find_passage(f"{first} … {last}", text, 0.95).matched


def test_information_separators_are_whitespace() -> None:
    match = find_passage("hazard ratio", "the hazard\x1eratio was low", 0.95)
    assert match.exact and match.text == "hazard\x1eratio"
```

- [ ] **Step 6: Run the file, ruff and mypy**

Run: `uv run pytest tests/test_passage_match.py -q && uv run ruff check bmlib/citations tests/test_passage_match.py && uv run mypy bmlib/citations`
Expected: `76 passed`, then no ruff or mypy findings.

- [ ] **Step 7: Update the docs and changelog, then commit**

```bash
git add bmlib/citations tests/test_passage_match.py CLAUDE.md docs/manual/citations.md CHANGELOG.md
git commit -m "feat(citations): find_passage — locate a quoted passage in its source"
```

### Task 2: The shared contract fixture (Python side)

**Files:**
- Create: `tests/fixtures/passage_match.json`
- Create: `tests/test_passage_match_contract.py`

**Interfaces:**
- Consumes: Task 1's Python API.
- Produces: the fixture format that Task 5's Rust contract test reads:

```json
{
  "_readme": "Shared contract for find_passage. bmlib's Python (tests/test_passage_match_contract.py) and Rust (rust/bmlib/tests/passage_match_contract.rs) assert against this file. Expected values are hand-written, never generated by the code under test. Offsets are Unicode code-point indices. similarity is asserted only where present.",
  "cases": [
    {"name": "…", "passage": "…", "text": "…", "min_similarity": 0.95,
     "expected": {"matched": true, "exact": true, "start": 0, "end": 10, "text": "…", "similarity": 1.0}}
  ],
  "invalid_thresholds": [0.0, -0.5, 1.0000001]
}
```

`start`, `end` and `text` are `null` on a rejection. Include `similarity` only where its value is certain by construction: `1.0` for exact matches, and `0.0` for "no candidate" or "too long to fit". For fuzzy cases, put a lower bound in `"min_similarity_at_least"` instead.

- [ ] **Step 1: Write the fixture with these cases** (all `min_similarity` 0.95 unless noted). Work out each expected span by hand.
  - `exact-verbatim`
  - `case-and-whitespace`
  - `curly-quotes-and-en-dash` (the quote has `"` and `-`; the source has `“ ”` and `–`)
  - `nfd-accent-at-end` (the source ends in `e` + U+0301; the span keeps the mark)
  - `ligature-fi` (the source has `ﬁ`)
  - `superscript-two` (the source has `m²`, the quote `m2`)
  - `soft-hyphen-inside-word`
  - `negation-overlay-not-equal` (the quote `a = b` against a source with `a ≠ b`: rejected)
  - `one-typo-accepted` (span = the source sentence, `min_similarity_at_least` 0.95)
  - `paraphrase-rejected` (not matched, similarity above 0)
  - `empty-passage`
  - `whitespace-only-passage`
  - `empty-text`
  - `passage-longer-than-text` (similarity 0.0)
  - plus the five Review Focus cases: `non-bmp-before-match` (text `"𝛂 and 😀 then the hazard ratio was 0.91"`, so `start` = 13), `across-paragraph-break`, `repeated-quote-first-occurrence`, `elided-quote-rejected`, `information-separator-is-space`.

- [ ] **Step 2: Write `tests/test_passage_match_contract.py`**

```python
FIXTURE = Path(__file__).parent / "fixtures" / "passage_match.json"

@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_contract_case(case: dict[str, Any]) -> None:
    match = find_passage(case["passage"], case["text"], case["min_similarity"])
    expected = case["expected"]
    assert match.matched is expected["matched"]
    assert match.exact is expected["exact"]
    assert (match.start, match.end, match.text) == (expected["start"], expected["end"], expected["text"])
    if "similarity" in expected:
        assert match.similarity == expected["similarity"]
    if "min_similarity_at_least" in expected:
        assert match.similarity >= expected["min_similarity_at_least"]

@pytest.mark.parametrize("threshold", INVALID, ids=str)
def test_contract_invalid_threshold(threshold: float) -> None:
    with pytest.raises(ValueError):
        find_passage("a", "a", threshold)
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_passage_match_contract.py -q`
Expected: all pass. If a case fails, work out which is wrong, your hand-written value or the code. **Do not copy the code's output into the fixture.** A real disagreement with bmlibrarian's behaviour gets reported, not papered over.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/passage_match.json tests/test_passage_match_contract.py
git commit -m "test(citations): shared find_passage contract fixture"
```

### Task 3: Rust `SequenceMatcher` (crate-private)

**Files:**
- Create: `rust/bmlib/src/citations/sequence_matcher.rs` (with unit tests in a `#[cfg(test)] mod tests`)
- Modify: `rust/bmlib/src/citations/mod.rs`: add `mod sequence_matcher;` (private)

**Interfaces:**
- Produces (crate-private):

```rust
pub(crate) struct Block { pub(crate) a: usize, pub(crate) b: usize, pub(crate) size: usize }
pub(crate) struct SequenceMatcher { /* b, b2j, a */ }
impl SequenceMatcher {
    pub(crate) fn new(b: &[char]) -> Self;          // seq2 fixed: the passage
    pub(crate) fn set_a(&mut self, a: &[char]);     // seq1: a text window
    pub(crate) fn matching_blocks(&self) -> Vec<Block>; // ends with the (len_a, len_b, 0) sentinel
    pub(crate) fn ratio(&self) -> f64;
    pub(crate) fn quick_ratio(&self) -> f64;
    pub(crate) fn real_quick_ratio(&self) -> f64;
}
```

Port CPython's `Lib/difflib.py` `find_longest_match`, `get_matching_blocks` (including its merge of adjacent blocks), `ratio`, `quick_ratio`, `real_quick_ratio` and `_calculate_ratio` (`1.0` when both are empty), for `isjunk=None, autojunk=False` only. Drop the junk and popular-element branches, which are unreachable here. Keep the tie-breaking: earliest `i`, then earliest `j`. Python keeps `matching_blocks` and `fullbcount` caches; Rust may recompute them.

- [ ] **Step 1: Write the failing unit tests.** In each case `a` is the window and `b` is the passage. The values come from CPython 3 `SequenceMatcher(None, a, b, autojunk=False)`.

```rust
#[test] fn blocks_and_ratios_match_cpython() {
    // (a, b, blocks, ratio, quick_ratio, real_quick_ratio)
    check("abxcd", "abcd", &[(0,0,2),(3,2,2),(5,4,0)], 0.8888888888888888, 0.8888888888888888, 0.8888888888888888);
    check("abab", "ab", &[(0,0,2),(4,2,0)], 0.6666666666666666, 0.6666666666666666, 0.6666666666666666);
    check("hazard ratio was 0.91", "the hazard ratio was 0.19",
          &[(0,4,19),(19,24,1),(21,25,0)], 0.8695652173913043, 0.9130434782608695, 0.9130434782608695);
    check("", "", &[(0,0,0)], 1.0, 1.0, 1.0);
    check("abc", "", &[(3,0,0)], 0.0, 0.0, 0.0);
}
```

Assert the floats with `==`, not with an epsilon. `check` builds the matcher with `new(&b_chars)`, then `set_a(&a_chars)`.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd rust && cargo test -p bmlib --lib citations::sequence_matcher`
Expected: compile error (`SequenceMatcher` not found).

- [ ] **Step 3: Implement the port**

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd rust && cargo test -p bmlib --lib citations::sequence_matcher`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add rust/bmlib/src/citations/sequence_matcher.rs rust/bmlib/src/citations/mod.rs
git commit -m "feat(rust/citations): crate-private port of difflib.SequenceMatcher"
```

### Task 4: Rust normalisation

**Files:**
- Create: `rust/bmlib/src/citations/passage_match.rs` (this task adds its normalisation half)
- Modify: `rust/bmlib/src/citations/mod.rs`: `pub mod passage_match;` plus re-exports of the public names from Tasks 4 and 5
- Create: `rust/bmlib/tests/passage_match.rs`: a translation of Python's `TestNormalizeWithOffsets` and `TestUnicodeFolding`, one Rust `#[test]` per Python test with the same snake_case name and the same assertions

**Interfaces:**
- Produces:
  - `pub fn fold_char(c: char) -> String`
  - `pub fn normalize_with_offsets(text: &str) -> (String, Vec<usize>)`. `offsets[i]` is the **char** index in `text` of the character that produced the `i`-th **char** of the normalised string.
  - `pub fn occurs_once(fragment: &str, text: &str) -> bool`
  - crate-internal `fn normalize_chars(text: &str) -> (Vec<char>, Vec<usize>)`, which the public function wraps and Task 5 uses.

`fold_char` is applied in the same order as Python:
1. the typographic fold;
2. NFKD of that result (`unicode_normalization::UnicodeNormalization::nfkd`);
3. the typographic fold again;
4. drop combining code points (except the negation marks) and Python-whitespace code points;
5. lowercase each remaining char (`char::to_lowercase`).

A fold result of `None` in Python's table means the character is dropped. Python's `lru_cache` is not ported.

- [ ] **Step 1: Translate the two Python test classes into `rust/bmlib/tests/passage_match.rs`**

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd rust && cargo test -p bmlib --test passage_match`
Expected: compile error (`bmlib::citations::passage_match` not found).

- [ ] **Step 3: Implement `fold_char`, `normalize_chars`, `normalize_with_offsets` and `occurs_once`**

- [ ] **Step 4: Run them and confirm they pass**

Run: `cd rust && cargo test -p bmlib --test passage_match`
Expected: every translated test passes.

- [ ] **Step 5: Commit**

```bash
git add rust/bmlib/src/citations rust/bmlib/tests/passage_match.rs
git commit -m "feat(rust/citations): passage normalisation with an offset map"
```

### Task 5: Rust `find_passage` and the contract test

**Files:**
- Modify: `rust/bmlib/src/citations/passage_match.rs`
- Modify: `rust/bmlib/tests/passage_match.rs`: translate the remaining Python classes (`TestExactMatch`, `TestFuzzyMatch`, `TestRejection`, `TestFitsText`, `TestPassageMatchInvariants`, `TestValidation`) and the exhaustive-search property test
- Create: `rust/bmlib/tests/passage_match_contract.rs`: reads `include_str!("../../../tests/fixtures/passage_match.json")`
- Modify: `rust/bmlib/CHANGELOG.md` → `[Unreleased]`: an "Added — follows Python's find_passage" entry; `docs/manual/citations.md`: say the section applies to both languages and that offsets are char indices in Rust

**Interfaces:**
- Consumes: `SequenceMatcher` (Task 3), `normalize_chars` (Task 4).
- Produces:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PassageSpan { pub start: usize, pub end: usize, pub text: String } // char indices
#[derive(Debug, Clone, PartialEq)]
pub struct PassageMatch { pub similarity: f64, pub span: Option<PassageSpan>, pub exact: bool }
impl PassageMatch { pub fn matched(&self) -> bool }
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct InvalidSimilarity(pub f64); // Display: "min_similarity must be in (0, 1], got {0}"; impl std::error::Error
pub fn find_passage(passage: &str, text: &str, min_similarity: f64) -> Result<PassageMatch, InvalidSimilarity>;
pub fn fits_text(passage_length: usize, text_length: usize, min_similarity: f64) -> bool;
pub fn max_edit_points(passage_length: usize, min_similarity: f64) -> usize;
```

`Option<PassageSpan>` replaces Python's `__post_init__` all-or-none check. `exact` is set only together with `Some(span)`. Reject NaN as an invalid threshold too. Port the private helpers one-to-one. Four places where the Rust code must match Python's floating-point and ordering behaviour exactly:
- `fits_text`: `passage_length as f64 * s <= text_length as f64 * (2.0 - s)`.
- `max_edit_points`: `((2 * passage_length) as f64 * (1.0 - s) / s).floor() as usize`, in that order of operations, so the result is bit-identical to Python's.
- Anchors are `isize` (they can be negative). The candidate ranking sorts by `(Reverse(neighbourhood), Reverse(votes), offset)`, which is Python's stable sort over ascending offsets. `_closest` picks the minimum by `(abs diff, candidate)`.
- `_accepted` trims Python whitespace from the span's edges.

- [ ] **Step 1: Translate the remaining Python tests.** The property test cannot reproduce Python's `random.Random`, so use a small inline LCG seeded with `20261017`. Keep `PROPERTY_TRIALS = 60`, `PROPERTY_THRESHOLD = 0.95` and `PROPERTY_SCORE_TOLERANCE = 0.01`. Port `_exhaustive_best` and `_random_edits` as test helpers. For `FUZZY_TIME_BUDGET_SECONDS = 2.0`, keep the same budget, measured with `std::time::Instant`.

- [ ] **Step 2: Write `passage_match_contract.rs`.** It holds the same two tests as Task 2's Python file, plus `every_case_name_is_unique`. Compare `similarity` with `==` where the fixture gives it.

- [ ] **Step 3: Run both and confirm they fail**

Run: `cd rust && cargo test -p bmlib --test passage_match --test passage_match_contract`
Expected: compile error (`find_passage` not found).

- [ ] **Step 4: Implement `find_passage` and its helpers**

- [ ] **Step 5: Run both and confirm they pass**

Run: `cd rust && cargo test -p bmlib --test passage_match --test passage_match_contract`
Expected: all pass, including `non-bmp-before-match`.

- [ ] **Step 6: Run fmt and clippy, update the docs and changelog, then commit**

Run: `cd rust && cargo fmt --check && cargo clippy --all-targets -- -D warnings`
Expected: clean.

```bash
git add rust/bmlib docs/manual/citations.md
git commit -m "feat(rust/citations): find_passage, held to Python by the shared contract"
```

### Task 6: The differential oracle corpus

**Files:**
- Create: `rust/oracle/dump_passage_match.py`: reads cases on stdin and writes results on stdout. It follows `dump_citations.py`'s shape and docstring.
- Create: `rust/bmlib/tests/data/passage_match_cases.json`: the single copy, read by both the dumper and the Rust test, as `service` does.
- Create: `rust/bmlib/tests/data/passage_match_expected.json`: generated by the dumper.
- Create: `rust/bmlib/tests/passage_match_oracle.rs`
- Modify: `scripts/rerun_rust_oracle.py`: add `("dump_passage_match.py", "passage_match_cases.json", "passage_match_expected.json")` to `CORPORA`.

**Interfaces:**
- Case record: `{"name": str, "fn": "find_passage" | "normalize_with_offsets" | "occurs_once" | "fits_text" | "max_edit_points", "args": {…}}`, with args named as in Python.
- Result for `find_passage`: `{"similarity": float, "start": int|null, "end": int|null, "text": str|null, "exact": bool}`. For `normalize_with_offsets`: `[normalised, offsets]`. For the others: the bare value.

- [ ] **Step 1: Write the cases file.** It contains:
  - every `(passage, text, min_similarity)` used in `tests/test_passage_match.py`'s fuzzy, exact and rejection classes;
  - 60 random-edit cases (`find_passage`, threshold 0.95). Produce these once with Python's `random.Random(20261017)` and bmlibrarian's `_random_edits` over `ABSTRACT`, and commit them as literal cases;
  - `normalize_with_offsets` over the contract fixture's texts;
  - `fits_text` / `max_edit_points` at thresholds 0.5, 0.8, 0.9, 0.95, 0.99, 1.0 for lengths 1, 7, 100 and 2 000.

- [ ] **Step 2: Write the dumper and generate the expectations**

Run: `.venv/bin/python rust/oracle/dump_passage_match.py < rust/bmlib/tests/data/passage_match_cases.json > rust/bmlib/tests/data/passage_match_expected.json`
Expected: a JSON list as long as the cases list.

- [ ] **Step 3: Write `passage_match_oracle.rs`.** It runs each case through the Rust port and asserts `serde_json::Value` equality with the expectation. `similarity` is compared as `f64` with `==`; on a mismatch, report the case name.

- [ ] **Step 4: Run the oracle and the rerun script**

Run: `cd rust && cargo test -p bmlib --test passage_match_oracle && cd .. && .venv/bin/python scripts/rerun_rust_oracle.py --only passage_match`
Expected: the Rust test passes, and the script reports the corpus clean.

A Rust/Python mismatch is a port bug: fix the port, never the expectation. The likeliest causes are byte vs char indices, `isspace`, and sort-tie order.

- [ ] **Step 5: Run every gate from Global Constraints, then commit**

```bash
git add rust/oracle/dump_passage_match.py rust/bmlib/tests/data/passage_match_*.json rust/bmlib/tests/passage_match_oracle.rs scripts/rerun_rust_oracle.py
git commit -m "test(rust/citations): find_passage differential oracle corpus"
```
