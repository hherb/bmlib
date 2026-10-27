# Retrieval returns its source (StudyAnalyzer item 9)

**Goal.** `FullTextService` hands back the JATS it fetched, not only the HTML
it rendered, so StudyAnalyzer's canonical text (item 4) can build sections
from a fetched paper. Spec: `~/src/studyanalyzer/docs/superpowers/specs/2026-10-21-studyanalyzer-design.md` §5, §7 row 9 (option a).

**Maintainer's calls (2026-10-27).**

1. Raw bytes: `FullTextResult.jats_xml: bytes | None` / `Option<Vec<u8>>`,
   exactly as served, so the declared encoding is honoured (#491's lesson).
2. A cached HTML entry with no XML beside it (written by an earlier bmlib)
   is served with `jats_xml=None`, not re-fetched. StudyAnalyzer keeps its own
   cache directory, which starts empty.
3. A PDF is handed back by the existing `file_path`; no `pdf_bytes`.

**Steps, each in Python and Rust.**

1. `FullTextResult.jats_xml`, last field, default none.
2. The three JATS fetch helpers return `(html, has_body, xml)`; the full-text
   and abstract results carry the XML; `_with_abstract_fallback` carries it
   onto a PDF link with the abstract.
3. Cache: `jats/<key>.xml`, `save_jats` / `get_jats`, created on first save;
   `delete`, `clear`, `quarantine` reach it. The service writes the XML first
   and skips the HTML when it fails. A cache hit reads it; an unreadable one
   is quarantined and re-fetched, as an unreadable HTML entry is.
4. Shared contract `tests/fixtures/fulltext_source.json` (five scripted
   cases), run by `tests/test_fulltext_source_contract.py` and
   `rust/bmlib/tests/fulltext_source_contract.rs`; unit tests for the cache
   entry and the write order.
5. Re-pin `tests/test_renderer_version.py` without a bump (the renderer is
   untouched). CHANGELOG, manual, ROADMAP, HANDOVER.

**Out of scope.** A non-UTF-8 JATS body is refused by the Rust tiers before
the XML is kept (#467/#491), so the contract has no such case.
