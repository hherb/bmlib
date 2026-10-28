# RAG chunker over the canonical text (StudyAnalyzer item 3)

**Goal:** split a paper's `CanonicalText` into sentence-aware chunks of about
1,000 code points with up to 100 of overlap, each labelled with the section it
lies in and recorded as a span into the canonical text, identically in Python
and Rust.

**Architecture:** one pure function, `chunk_canonical(text, max_chars,
overlap_chars)`, in `bmlib.fulltext.canonical.chunk` /
`bmlib::fulltext::canonical::chunk`. It splits each block into *units*
(sentences, or pieces of an over-long sentence) and packs a section's units
greedily into chunks. The shared contract is
`tests/fixtures/canonical_chunks.json`.

**Spec:** StudyAnalyzer design §5.1 (offsets in code points, into the
canonical text), §6.2 (retrieval when the paper does not fit), §7 row 3 (size
close to bmlibrarian's `chunk_embedder.py`: 1,000 and 100).

## Decisions (builder's calls, §11)

- **A chunk never crosses a section**, so its label is exact. It may cross
  blocks within a section (the `"\n\n"` between them lies inside its span).
  The title and headings are labels, not chunk text: they are reachable from
  `section_index`, and a heading-only section has no chunk.
- **Sentence ends** follow bmlibrarian's `adaptive_chunker_optimized.py`: a
  `.`, `!` or `?` followed by whitespace, unless the token before it is an
  abbreviation. The regex lookbehinds become a whole-token check (Rust's
  `regex` has no lookbehind): the run of non-whitespace before the mark, its
  leading opening brackets and quotes stripped, compared ASCII-case-blind with
  `ABBREVIATIONS` (bmlibrarian's fourteen plus `figs`, `e.g`, `i.e`, `cf`,
  `approx`). A block's end is always a unit's end. "Whitespace" is
  `is_white_space` from the model in Python and `char::is_whitespace` in Rust,
  which it is defined to match.
- **`max_chars` is a hard cap.** A unit longer than it is cut at the last
  whitespace that keeps the piece within the cap, or at the cap itself when
  there is none. Nothing is dropped (house rule 6): every non-whitespace code
  point of every block lies in some chunk. bmlibrarian keeps an over-long
  sentence whole instead; a hard cap keeps every chunk inside the embedder's
  window whatever the converter (4b's PDF prose may lack full stops).
- **Overlap is by whole units**, as in bmlibrarian: the next chunk restarts at
  the first unit of the last one, after its first, that begins within
  `overlap_chars` of its end (the first is never carried, so every chunk moves
  on); none if no such unit, and none if the carried units and the next new one
  would together pass the cap (so every chunk adds text).
- **Version.** `CHUNKER_VERSION = 1`, pinned against the chunker's sources in
  `tests/test_renderer_version.py`; a `Chunk` does not carry it, so a caller
  storing chunks records it beside the canonical text's `converter_version`. The fixture carries it, so Rust must
  agree.
- **Refusals.** `max_chars < 1`, `overlap_chars < 0`, `overlap_chars >=
  max_chars`, checked first; then the canonical text is validated.

## Tasks

1. Python `chunk.py` and unit tests (`tests/test_canonical_chunk.py`).
2. Fixture generated from Python by a scratch script, every expected chunk
   checked by eye; `tests/test_canonical_chunks_contract.py`.
3. Rust `chunk.rs`, unit tests, `rust/bmlib/tests/canonical_chunks_contract.rs`.
4. Version pin, CHANGELOG, ROADMAP row, HANDOVER; both languages' gates.
