# Retraction status from three sources, one rule (#504)

Requested by bmlibrarian_lite (its spec
`docs/superpowers/specs/2026-10-18-retraction-checking-design.md`): every
paper gets `retracted`, `concern`, `clear` or `unchecked`, decided by one rule
that Python, Rust and the apps' three platforms share.

## Shape

- **Python** — `bmlib/publications/retraction_status.py`:
  `RetractionEvidence`, `RetractionVerdict`, `CrossrefResult`,
  `retraction_status()`, `crossref_retraction_requests()`,
  `parse_crossref_retraction_response()`, `evidence_from_notices()`. The
  desktop's export stays in the existing `retraction_notices` table.
- **Rust** — `rust/bmlib/src/publications/retraction_status.rs`: the same,
  plus `RetractionIndex` (the export as one versioned file, built by the
  existing oracle-checked CSV parser).
- **FFI** — `rust/bmlib-ffi/src/retraction.rs`: owned copies with exhaustive
  conversions both ways, the four functions, and `RetractionIndex` as an
  object.
- **Contract** — `tests/fixtures/retraction_status.json`: rule cases go
  through the Crossref parser, so "did Crossref answer?" is pinned end to
  end. Python, Rust and the FFI each run it.

## Decisions (the maintainer's, 2026-10-18)

1. The notice guard covers `publisher` relations only. The guard as the
   issue worded it discards the Surgisphere Lancet paper's real retractions,
   because the paper lists its notices in its own `update-to`.
2. A DOI Crossref does not know is `unchecked`.
3. The apps keep a Rust-built index file, not SQLite and not a
   bmlib-published artifact.

`docs/DECISIONS.md` ("publications — retraction status from three sources")
has the measurements behind each, and the smaller choices: the rule takes
lookup results rather than an `answered` set, the deciding entry is fixed by
the evidence rather than its order, and Python decodes Crossref bodies as
UTF-8 itself.

## Measured (2026-10-18)

- Crossref batching: 25 to 200 DOIs per `filter=doi:…` request all served
  (200 DOIs = 6,002-character URL). Kept at 50, cut under 4,000 characters.
- Special characters: DOIs with `:`, `;`, `<`, `>` are served
  percent-encoded with `/()` kept. No export DOI contains a comma (0 of
  69,318), which the filter syntax cannot carry; such a DOI is `unsendable`.
- Guard: 310 random notice→paper pairs. The worded guard lost 1–3 retracted
  papers per draw; the publisher-only guard lost 0–1, and the remaining case
  is a publisher-only symmetric deposit.
