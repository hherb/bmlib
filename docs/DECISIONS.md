# Deliberate non-fixes — do not "fix" these

Each entry below was investigated and closed as correct. Reopening one wastes
a session. Entries marked "argued inline" carry their full reasoning as
comments in the named source file; the entry here is the pointer, not the
argument — write new entries in that shape, and put the argument where the
code is.

Split out of `HANDOVER.md` on 2026-08-09, which the register had grown to
dominate. `HANDOVER.md` is for what still needs doing; this file is for what
must not be re-done.

## Transparency

- **`_INDUSTRY_STEMS` and `_INDUSTRY_WORDS` must not be merged into one
  list**, and neither may be extended without re-running
  `scripts/sample_funder_names.py` against `tests/data/funder_names.json` —
  the corpus *removed* intuitive members (`pharma`, `biotech`) on measured
  false positives. Metric test:
  `tests/test_funder_matching.py::TestAgainstTheLabelledCorpus`.
- **`_is_industry_funder()` is deliberately not applied to COI prose**;
  `_INDUSTRY_COI_KEYWORDS` stays separate — org suffixes match far too
  freely in running text.
- **Every `analyze()` sub-step takes `_Analysis`, mutates it, and returns
  `None`** — one step threading a value while four mutate is the
  inconsistency that makes the next contributor guess. Pinned by
  `test_the_merge_applies_both_of_its_branches_to_one_list`.
- **The data-deposition rank-merge machinery** (`_DATA_LEVEL_RANK`,
  `note_data_level()`, `_DEPOSITION_DATABANK_LEVELS`) is argued inline in
  `transparency/analyzer.py`. Two rules: every producible level must be a
  key of the ranking or `note_data_level()` raises by design; the deposition
  list deliberately excludes reference-only databases (dbSNP, OMIM, RefSeq…).
  Three tests in `test_transparency.py` pin it.
- **Four more, each argued where it lives and each with a test naming it:**
  `TransparencySettings.filtering_enabled` / `max_concurrent_analyses` /
  `cache_results` are caller-owned orchestration hints, not dead code;
  `outcome_switching_detected` stays reserved and always `False` (kept in the
  schema so persisted results need no migration when detection lands); a
  PubMed record with no `<CoiStatement>` leaves `coi_disclosed` alone (absence
  means the publisher supplied none); and `<DataBankList>` accessions are
  validated as `NCT\d{8}` before becoming a URL, though an entry failing
  validation still counts as registered — registration is separate from
  followability.

## Positional stability

- **`Publication.pmcid`, `BaseAgent.__init__`'s `embedding_model`, and
  `TransparencyResult.unknown_reason` are each declared last** on their
  dataclass/signature — downstream projects construct positionally, and any
  other placement shifts every following argument silently. Pinned by
  `test_positional_construction_is_stable_across_versions`.

## db / llm / agents

- **PostgreSQL transaction nesting is detected from bmlib's own open-block
  count, not psycopg2's status**, keyed by *(thread, `id(conn)`)* — see
  CLAUDE.md for why both parts are load-bearing.
- **The Ollama raw `/api/tags` path re-implements httpx's safety defaults on
  purpose** (HTTP(S)-only scheme, bearer token stripped across cross-origin
  redirects, `"<word>:<digits>"` read as host:port). Each has a regression
  test naming it.
- **The JSON extractors prefer a whole span to a nested fragment in three
  places** (argued inline in `llm/utils.py`, `llm/json_repair.py`,
  `agents/base.py` — all guarding #33's silent truncation), **a fenced
  candidate wins on parse alone**, **`parse_json()` enforces `dict | list`**
  (a bare scalar raises → retry inside `chat_json()`), **`require_dict` has
  a third `bool` overload** (mypy does not expand `bool` into the two
  `Literal`s; CI runs ruff only, so nothing catches its removal), and
  **`salvage_json_fields()` bounds both passes with `RecursionError` caught
  wherever a candidate is decoded**. `iter_json_spans()` dedupes candidates by
  text, not position. Eleven tests pin these — seven in
  `test_json_extraction.py` (from `TestExtractJsonPrefersWholeSpans`) and four
  in `test_agents.py`.
- **`PerformanceMetrics.elapsed_time_seconds` reads `time.monotonic()`**,
  not the wall-clock timestamps it stores; `snapshot()` must copy the
  monotonic marks by hand (`init=False`). Model-inference and prompt-eval
  timers are deliberately omitted — no provider reports them through bmlib.
  Pinned by `test_elapsed_survives_a_wall_clock_step` and
  `test_snapshot_carries_the_monotonic_marks`.

## context_processor

- **The batcher measures the string it will actually send; it never assumes
  a size.** Three tempting arithmetic shortcuts each re-break
  `max_context_chars` the way upstream did; the invariant is
  `Batch.total_chars == len(_format_batch_content(batch, config))`.
  `estimate_item_size()` was deliberately not ported — it let the oversized
  decision disagree with the packing measurement.
- **Six more load-bearing "simplifications" refused**, each with a named test
  in `test_context_processor.py` / `test_llm_chunk_processor.py`: `_render()`
  substitutes in one regex pass (two-pass `.replace()` splices the batch into
  a query containing `{content}`); the package `__init__` reaches
  `llm_processor` through PEP 562 `__getattr__` (a plain re-export drags
  jinja2 into the LLM-free harness); `process()` keeps statistics in a local,
  not on `self`; `success_rate` cannot return 1.0 for a batch-less run that
  dropped everything; the recursion wraps results in `ConsolidatedItem`, not
  a tuple (what made upstream's `format_consolidated_item()` dead code), and
  `min_items_for_recursion` stopping at one result is correct; and
  `LLMChunkProcessor` renders with `str.replace`, not `str.format` (templates
  legitimately hold literal braces).

## fulltext — retrieval and JATS

- **`_JATSHandler.endElement` tests `in_figure or in_table_wrap` before any
  prose branch and routes on `in_caption`** — asking about the section first
  blanks the caption and renames the section; the same branch deliberately
  drops non-caption `<p>` inside figures/tables. Pinned by
  `TestJATSParserCaptionScoping` and
  `TestJATSParserUnsectionedBodyFurniture`.
- **NCBI's ID Converter is consulted *after* the Europe PMC search** (the
  search also carries the free-PDF URL) **but *outside* the search's
  `except`, in its own statement** — a search that raised is exactly when a
  second resolver is worth its request, and one enclosing handler would
  swallow the error before the converter was reached. A converter-discovered
  PMC ID is tried at Europe PMC even when the search said `inEPMC="N"`, since
  a stale flag is one reason the converter exists. Two tests in
  `test_fulltext_service.py` pin it, starting at
  `test_the_converter_is_consulted_when_the_search_itself_failed`.
- **`_fetch_ncbi_pmc()` raises on a reply with neither body nor abstract** —
  efetch answers a publisher-withheld article with a stub that is HTTP 200
  and parses cleanly; returned instead of raised, it becomes near-empty HTML
  labelled `content_kind="abstract"`. Pinned by
  `test_a_stub_with_no_article_raises` and
  `test_a_body_less_article_with_an_abstract_is_returned`.

## fulltext — importable on a core install (#64, PR #66)

**CLAUDE.md argues this one in full**, under "Optional dependencies guarded
at the call site". What it omits:

- **Both halves of the fix stay, and the reason is counter-intuitive — read
  the mutation table in
  `docs/superpowers/specs/2026-08-08-fulltext-import-without-httpx-design.md`
  before removing either.** They overlap: once httpx moved into
  `FullTextService.__init__`, restoring the eager re-export gated nothing and
  **no test failed**. The deferral's own contribution is that
  `import bmlib.fulltext` never loads `service`, so no future top-level import
  there can gate the parser, models or segmenter again;
  `test_importing_the_package_does_not_load_the_service` isolates it, written
  *because* mutation testing found nothing else did.
- **`_http_get` had no test at all** until that review — all ~45 tests in the
  file patch it, so replacing its body with `raise AssertionError` left the
  suite green. `TestHttpGet` covers it now.
- **`fulltext = ["httpx>=0.25"]` is httpx only**; `pdf` stays separate (a
  ~20 MB wheel for anyone who only wants JATS).
  `test_the_extra_the_error_message_names_is_a_real_one` reads
  `Provides-Extra` from the installed metadata, so the message and
  `pyproject.toml` cannot drift apart.

## fulltext — the exhausted-chain report (#67, PR #69)

- **The warning belongs outside the `if abstract_only is not None:` branch,
  and that placement is the whole fix.** Inside it, the *more* complete
  failure was the quieter one. Mutation testing confirms it: putting the
  warning back inside the branch — the original bug — fails two tests.
- **Faults and absences are counted apart, and that is the discrimination
  the report exists to make.** `N attempts failed (ConnectError)` is a lost
  network; `N sources had nothing` is an ordinary paywalled paper; a
  `TypeError` among the faults is a bug. A single count could not say this.
- **The first cut still printed the reassuring line during a total outage,
  for two reasons worth remembering.** (1) Both resolvers reported an HTTP
  failure by *returning* `(None, None)` — which is also what an empty result
  set returns — so a 503 incremented nothing. A swallowed exception is not
  the only way a tier goes wrong; anything reporting failure in the same
  shape as absence has this bug. (2) `FullTextError` was raised alike for
  `Unpaywall HTTP 503` and `DOI not found in Unpaywall`. Hence
  `FullTextUnavailableError`, and hence `note_absence()`.
- **The counter says *attempts*, never tiers.** `_try_known_sources` records
  once per fetcher-supplied source, so it is not bounded by the chain's eight
  tiers — "9 tiers raised" was emittable from a run that attempted four.
- **`_download_and_cache_pdf`'s *download* half is deliberately not wired to
  the counter** — see #68 above. All three call sites return immediately
  after it, so a recorded failure could never be reported; threading it would
  be dead plumbing that reads as coverage. A comment at the handler says so,
  since the other eight swallowers were wired. Its *cache-write* half was
  split out into `_save_pdf_to_cache`: an unwritable directory is not a
  download failure, and folding the two left a PDF-only corpus with no cache
  warning at all.
- **`_resolve_pmc_id_via_idconv` takes `failures` as *optional*** because of
  its fourteen direct callers in the tests, which have no report to feed it.
  Not "because it swallows its own exceptions": Tier 0 swallows too and takes
  the parameter as required.
- **A successful retrieval emits no *exhaustion* warning** — pinned by two
  controls, one where nothing fails and one where an attempt fails and a
  later tier recovers. The narrow wording is deliberate: a success may still
  warn about an unwritable cache or an unextractable PDF.
- **A 404 is an absence from an *article* endpoint and a fault from a
  *search* endpoint.** Europe PMC answers "no such paper" with HTTP 200 and
  an empty list, so a 404 on the search path means the API moved; on an
  article path it means the paper is not there, which for a stored fetcher
  URL is ordinary staleness. Three of the four article fetchers called it a
  fault until review caught it.
- **`describe()`'s wording is pinned at its source**, not only through a tier
  chain. It is a documented interface — the manual tells operators to grep
  for it — and asserting it through `fetch_fulltext` left the singular branch
  untested and the counts matched by substring, where `"13 attempts failed"`
  satisfies `"3 attempts failed"`.

## fulltext — the cache is written atomically (#70, #71, PR #74)

Each of these looks like a line worth simplifying, and each re-opens the bug.
All are pinned by a named test in `test_fulltext_cache.py` /
`test_fulltext_service.py`, and every one was verified by mutation — review of
the first cut found two bullets here whose named test did not exist, and one
whose stated reason was wrong, so the claim is meant literally.

- **The `os.fsync()` before `os.replace` is not durability theatre.** Under
  delayed allocation the `write(2)` that `flush()` issues *returns success* on
  a disk about to fill; the blocks are allocated at writeback and ENOSPC
  reaches userspace only at `fsync`. Without it `os.replace` publishes a file
  whose blocks were never written, which is #70 again one layer down. The
  `flush()` is needed for a separate reason — `os.fsync` acts on the
  descriptor, so anything still in Python's `BufferedWriter` is not covered.
  Removing the two lines fails four tests. Note what those four *can* pin:
  delayed allocation is not observable from userspace, so they assert that
  `os.fsync` is called, not the kernel behaviour making it necessary. A
  refactor reaching the same guarantee via `O_DSYNC` would break them while
  being correct.
- **The temp file's name carries a UUID — but not for the reason first
  given.** Two processes cannot interleave into one temp file: `O_EXCL`
  already stops that. The real hazard is that the loser of the race runs the
  cleanup handler and unlinks the *winner's* in-flight file, whose
  `os.replace` then fails with `FileNotFoundError`, leaving neither writer
  having cached anything. Mutating the UUID to `os.getpid()` survived the
  whole suite *and* ruff until
  `test_two_writers_racing_on_one_article_do_not_destroy_each_other` was
  added.
- **The mode is `0o666` filtered by the umask, not `tempfile.mkstemp`'s
  0600 — and not `0o644` either.** A cache directory shared between users
  otherwise breaks silently: the second user cannot read what the first
  cached, re-fetches everything, and replaces the file with one the first then
  cannot read. `0o666` is what `write_bytes` requests, so the umask does the
  narrowing; the first cut requested `0o644`, which is the same bug a step
  smaller — it drops the group-write bit a umask of 002 grants, which is
  precisely the shared-group case, and it made the pinning test fail under
  that umask while passing under 022.
- **`os.open` adds `O_BINARY` where the platform has it.** Windows only, and
  a no-op elsewhere via `getattr(os, "O_BINARY", 0)`. Without it the CRT opens
  the descriptor in text mode — `os.fdopen(fd, "wb")` cannot undo that, since
  only `io.FileIO`'s path-opening branch sets the flag — and every LF in a
  cached PDF is written as CRLF, which is #70's own failure mode restored on
  one platform. The `PDF_MAGIC` fixture carries LF and CRLF bytes so the
  round-trip assertions are able to see it.
- **The cleanup's `unlink` is itself guarded.** `missing_ok=True` covers only
  `ENOENT`; an unlink failing for any other reason replaces the original
  exception, and that exception is what `FullTextService` interpolates into
  the one warning an operator sees — reporting a full disk as a permissions
  problem.
- **`sanitize_identifier` truncates its readable prefix.** The temp name is 38
  characters longer than the entry's, which lowered the effective `NAME_MAX`
  ceiling to ~217 and made a long identifier fail a write a bare `write_text`
  had completed. That per-article fault then tripped the once-per-service
  "nothing is being cached" warning — untrue, and it silences the
  directory-wide fault that warning exists to report. The prefix is only there
  to be read; the hash over the whole raw identifier carries the collision
  guarantee.
- **`save_html`/`save_pdf` raise rather than swallowing.** Both
  `FullTextService` call sites already report a failed cache write (#67), and
  a caller told nothing would believe the article was cached. Both docstrings
  carry a `Raises:` section, because for a direct caller this is a real change
  and not a relocation: under delayed allocation `write_text` *returned a
  path* in exactly the case that now raises.
- **#71's guard is `except Exception`, and narrowing it restores the bug.** A
  decode failure is only the shape #71 was reported in; a cached file the
  process cannot read raises `OSError` instead. Mutation testing found the
  first cut *survived* narrowing to `UnicodeDecodeError` — the extra test
  (`test_an_entry_that_fails_for_any_other_reason_falls_through_too`, which
  puts a directory where the file should be, so it raises for root too) exists
  because of that.
- **The guard reports the exception *type*, not just its message.** The same
  reason `_TierFailures` does: a `TypeError` printed under a sentence about an
  unreadable file reads as a bad cache entry rather than the bmlib bug it is,
  and a bare `OSError()` renders as an empty pair of brackets.
- **The unreadable file is not deleted — it is moved aside.** Deleting a
  user's data on a read error is a larger action than the bug asks for, so a
  failed re-fetch must leave the evidence. But leaving it *in place* does not
  work: the first cut justified that on "a successful re-fetch overwrites it",
  which holds only when the chain returns JATS full text. An article served as
  a PDF writes `pdfs/` and never touches `html/`, and since the undecodable
  HTML entry is read *first*, it hides the freshly cached PDF behind it — the
  article then warns and re-downloads on every run, forever. `quarantine()`
  renames it to `.corrupt`: out of the lookup path, still on disk. Only
  entries that actually fail to read are moved, pinned by a negative control.
- **`_remove` handles an entry that is not a regular file.** The corrupt shape
  the #71 test itself constructs is a directory standing where the file should
  be, and both documented ways to clear it failed on it: `delete()` raised and
  `clear()` skipped it silently, while the warning told the operator to go and
  delete that file.
- **That warning is per article, where the *write* warning is once per
  service.** An unwritable directory is a property of the directory; an
  unreadable file is a property of that one file. Pinned with two *different*
  corrupt articles rather than two runs over one, since a run now heals the
  entry it could not read. It is also not counted on #67's exhaustion report:
  the cache is not a retrieval attempt, and the line already says more than
  that report's two buckets could.
- **`_attach_pdf_text` catches everything `get_converter()` can raise, not
  just `ImportError`.** `_check_cache` re-extracts a cached PDF, so it runs
  inside #71's guard: narrowed, a `ValueError` for an unknown backend name (or
  anything a third-party backend's `__init__` raises) escaped this method and
  surfaced two frames up as "could not read the cached full text" — blaming a
  cached PDF that read perfectly, and re-downloading it into the identical
  deterministic fault.
- **#70's fix is prospective, and that is accepted.** It stops a truncated
  entry being *written*; it does not detect one already on disk. A real
  truncation of English-language biomedical HTML almost always lands on an
  ASCII boundary and decodes perfectly, so such an entry is still served as
  `content_kind="fulltext"` with nothing logged. Detecting it needs a length
  or checksum sidecar beside every entry — a cache format change, for a
  window that closes as entries are rewritten. Not done; `clear()` is the
  remedy for a cache written by an older version.

## fulltext — the service degrades but the cache still raises (#75)

`FullTextService` survives a cache directory it cannot create;
`FullTextCache(cache_dir=...)` constructed directly still raises. **This
asymmetry is deliberate — do not "make it consistent".** A caller who
constructs a cache asked for one specifically, and returning an object whose
every method then fails one at a time is worse than failing once, clearly, at
construction. Pinned by
`test_fulltext_cache.py::TestADirectlyConstructedCacheStillRaises`, which is
the only thing standing between the decision and a silent tidy-up.

Three further choices, each with a named test and each verified by mutation:

- **The guard catches `RuntimeError` as well as `OSError`.** Not defensive
  padding: `_default_cache_dir()` runs before any `mkdir` and calls
  `Path.home()`, which raises `RuntimeError` where there is no `HOME` and no
  passwd entry. Narrowing to `OSError` fixes the shape #75 was reported in and
  leaves the identical defect one layer up —
  `test_a_home_directory_that_cannot_be_determined_is_survived` fails under
  exactly that mutation.
- **It does not catch `Exception`.** Inside that one constructor
  `RuntimeError` has exactly one *source*, so the pair stays narrow enough
  that a bmlib bug still surfaces as one. Widening a guard catches strictly
  more, so no test that merely uses the cache can fail on it — which is why
  `test_an_unexpected_error_from_the_cache_still_propagates` exists and does
  nothing else: it raises a `ValueError` from the constructor and demands it
  escape. Without it this bullet was prose with nothing behind it.
- **No fallback cache location, and no writability probe.** Relocating to a
  temp directory surprises a caller who set `cache_dir` deliberately, and a
  cache that vanishes on reboot looks like one that never hits; probing would
  be TOCTOU and would litter the operator's cache directory with a file that
  is not an article. Pinned by
  `test_a_file_in_the_way_leaves_a_service_with_no_cache` (a relocating guard
  leaves `service.cache` set) and
  `test_nothing_is_written_where_the_cache_would_have_gone`, which asserts the
  directory holds nothing but the blocking file.

**`test_retrieval_still_works_with_no_cache` asserts the retrieval logs
nothing at `WARNING` or above. That assertion is not log tidiness — it is the
only thing pinning the two `self.cache is not None` guards.** Delete either
one and the retrieval still succeeds: `_check_cache(None, ...)` raises
`AttributeError` into #71's best-effort read handler and `_cache_html` raises
into #67's write handler, so the whole suite stays green and the only symptom
is a pair of WARNINGs blaming the environment for a bmlib bug, per article,
per run — the exact failure those two issues exist to prevent. Measured: with
both guards removed and the assertion absent, 1774 tests pass. Do not relax it
to "no errors" or drop it as noise.

**A read-only cache directory splits between #75 and #67, and the boundary is
not where it looks.** `FullTextCache.__init__` makes *three* `mkdir` calls and
only the first is suppressed by `exist_ok=True`, so a read-only root whose
`pdfs/` and `html/` do not yet exist raises `PermissionError` from the second
— #75's degrade, not #67's warn-once. #67 is reached only when the
subdirectories already exist and the *write* fails: an unwritable subdirectory,
or a full disk. Measured, not reasoned: `mkdir(exist_ok=True)` on a `0o555`
root gives `PermissionError: [Errno 13] … /pdfs`. The earlier version of this
entry claimed the whole read-only case was #67's and used that to justify the
no-probe non-goal; the non-goal stands on the two reasons above without it.

One consequence worth not undoing: **the three *post-check* cache helpers take
the cache as a parameter** rather than reading `self.cache`. Once `self.cache`
became optional their precondition — "the caller checked" — was a comment a
caller could forget; as a parameter the narrowing and the use sit in one
function body, where a type checker can discharge it. Note what that does and
does not claim: nothing in this repo checks it, since CI runs ruff and not
mypy, so unlike `sync._stamp_source()` — which raises `ValueError` at runtime —
the guarantee here is one a downstream's checker gets and a reader can verify
locally. `_cache_html` and `_download_and_cache_pdf` are deliberately *not*
in the set: they are the sites that do the checking, and giving them the same
shape would push one branch out into their seven unconditional call sites.

## fulltext — the PDF converter (PR #60)

- **A password-protected PDF is rejected on `doc.needs_pass`, never on
  `doc.is_encrypted`.** An *owner* password restricts permissions without
  blocking reads, so such a file is encrypted and converts perfectly;
  widening the check to `is_encrypted` would reject it. Both guards carry an
  owner-password negative control for exactly that
  (`test_an_owner_password_alone_does_not_block_conversion` /
  `..._extraction`), so neither is a check that cannot fail.
- **`extract_blocks()` keeps its explicit check even though it already
  raised** — it raised only because `get_text()` failed of its own accord,
  and had that stopped, it would have returned `[]`, exactly what an
  image-only scan returns. The general lesson, and the reason #57 existed:
  `except` blocks written to keep one bad page from aborting the rest will
  also absorb a whole-file failure, and the result reports as a success.

## fulltext — PDF section segmenter (PR #55)

- **`TextBlock` is one PDF *line*, not a span, with font attributes from the
  dominant span** (most non-whitespace characters, ties to the first).
  PyMuPDF starts a new span at every font change, so upstream's span-level
  blocks shattered a mixed-font heading into fragments no anchored pattern
  could match. Pinned by `test_a_heading_split_across_spans_is_one_block` and
  `test_a_superscript_marker_does_not_restyle_the_line`.
- **Nothing is dropped for being empty or unclassified**, each with a named
  test: front matter is a 0.5-confidence section (if the real first heading
  was missed, it has swallowed the introduction); a heading with no body is
  reported with `content == ""`; and `SectionType.TITLE`,
  `SegmentedDocument.authors` and `Section.subsections` are reserved, not
  dead (the `outcome_switching_detected` precedent).
- **`extract_blocks()` raises where `convert()` returns a failed result** —
  a partial block list is indistinguishable from a sparse PDF, so degrading
  would be silent, where `converted_pages` says how partial a conversion was.
  Pinned by `test_a_corrupt_pdf_raises_rather_than_degrading`.
- **A negative vertical gap (column/page boundary) inserts no paragraph
  break** — a PDF gives no signal distinguishing a paragraph continuing
  across a page from one ending at it. The `height == 0` degenerate-bbox case
  is acknowledged in `_join_blocks` and left.
- **CONFLICTS owns the disclosure family, in both numbers** — listing the
  singular under FUNDING put the two numbers of one heading in different
  sections, decided by dict iteration order. A comment wards off re-adding it.
- **Two spec-level limits are documented in `docs/manual/fulltext.md` rather
  than fixed:** the 0.7 partial-match pass can fire on a bold figure caption
  ("Fig. 3 Study results" → RESULTS), and `min_heading_size` is an absolute
  floor (10.0) in an otherwise median-relative design, so it can silence the
  segmenter on a 9pt two-column layout. Callers check `Section.confidence`.

## citations (merged, PR #58)

**Argued in full in `docs/manual/citations.md` and
`docs/superpowers/specs/2026-08-06-citations-port-design.md` — read them
before "correcting" anything here.** Upstream's *code* is the output spec,
not its docstrings, where the two disagree. Five upstream-faithful oddities
are kept rather than unified (per-style empty-title rendering, the ambiguous
bare inverted `authors` string, `"\n---"` with no leading blank line,
`"Smithn.d."`, `author_surname("Jan van der Berg") == "Berg"`), each pinned
by a test naming it. Two deliberate departures: `Citation` compares by all
fields, and marker ids stay `int` only. Five upstream defects were fixed,
the fifth from PR #58's review — a whitespace-only author entry crashed every
style with `IndexError`.

## publications — PubMed metadata graft (PR #59)

**CLAUDE.md argues most of this port in full — read it there, and do not
re-derive any of it.** "Replace-per-source child rows" settles the `source`
column and scoped delete, `_stamp_source()`, the `ValueError` on an unnamed
row, the absent UNIQUE constraint, the empty guard and `_consolidate_rows()`;
"Markdown, measured against the markup" settles the mixed-content walker,
strip-once, edge whitespace outside the markers, `Label` **or**
`NlmCategory`, the measured escape set, and `<u>`. Each is pinned by a named
test on both backends, several verified by mutation. Only what it omits:

- **PubMed repeats a `<Grant>` block verbatim** — 31 of 575 entries across 200
  NIH-funded records — so `_parse_grants()` collapses exact repeats, keeping
  first-occurrence order. Two grants differing in any field are two grants.
- **`position` indexes `<AuthorList>`, not `Publication.authors`** — it counts
  the `<CollectiveName>` consortia that `authors` skips, so the two differ in
  length whenever one is present and `authors[a.position]` is the wrong way to
  resolve an affiliation's author (match on `author`). What position is *for*
  — first or senior author — is right either way. Accepted knock-on: a
  consortium stating an affiliation loses it, since recording it would put an
  `author` in the table that is absent from `authors`, breaking the one join
  the column exists for. Pinned by
  `test_position_indexes_the_xml_author_list_not_the_authors_field`.
- **`store_publication()` does not write `publication_id` back onto the
  `Grant` / `AuthorAffiliation` objects it is given**, unlike `pub`, which it
  mutates in place and documents as such (the `FullTextSource` precedent). The
  failure would be silent — the field reads `0`, a plausible id rather than an
  obvious sentinel. Pinned by `test_the_caller_s_objects_are_not_mutated`.
- **`is_retracted` and upstream's `_extract_date` were not ported.**
  `publication_types` already carries "Retracted Publication",
  `retractions.py` answers authoritatively, and upstream reads RefType
  `RetractionOf` (this article *is* the notice) as retracted. `_parse_pubdate`
  is strictly better than `_extract_date`, which defaults a missing month and
  day to `01` — inventing precision — and swallows every failure bare.
- **`~x~` / `^x^` are Pandoc extensions, knowingly.** A renderer without them
  shows the tildes literally; the alternative flattened `CO<sub>2</sub>` and
  `CO<sup>2</sup>` to the same ambiguous `CO2`. Documented in the manual.
- **Which elements get the formatting walker is decided by NLM's DTD.**
  `ArticleTitle`, `AbstractText` and `Affiliation` are declared `(%text;)*` —
  `#PCDATA | b | i | sup | sub | u` — so all three use
  `_text_with_formatting`. `Journal/Title`, `DescriptorName` and
  `PublicationType` are `(#PCDATA)`, genuine leaves, and keep plain `.text`.
  Do not widen or narrow this list by eye; check the DTD.

## publications — retractions

- **`bmlib.publications.retractions` has no downloader** (the Crossref
  endpoint 504s freely), **is not a fetcher and never will be without a
  protocol change** (a notice annotates a paper usually not in the caller's
  table — see the design doc's "Why this is not a fetcher"), **is not wired
  into `transparency/` or `quality/`** (both are scoring changes moving
  stored values), and **has no `is_paper_retracted()` wrapper** (keeping the
  pure rule separable from the I/O is what makes it testable).
- **Two values measured against the live export, not reasoned about**: the
  `%m/%d/%Y` / `%d/%m/%Y` ambiguity resolves US-first (confirmed by same-file
  dates whose day exceeds 12), and `_ABSENT_IDENTIFIER_VALUES` holds exactly
  `{"0", "unavailable"}` — a third sentinel needs its own measurement.

## quality — Cochrane assessor (merged, PR #54)

Full reasoning in `docs/superpowers/specs/2026-08-05-cochrane-assessor-design.md`
and `docs/manual/quality.md`; every claim below has a named test.

- **Nothing is fabricated to fill a gap:** `assess()` returns `None` on
  failure rather than nine defaulted "Unclear risk" domains;
  `collapse_risk_of_bias()` raises on an unrecognised `bias_type` rather than
  skipping it into a `BiasRisk` that looks complete; `unclear` outranks `low`
  in its worst-wins reduction; `_enrich_with_cochrane()` does not copy
  Cochrane's `evidence_level` onto the assessment's; `study_id` comes from the
  caller, never parsed from an author list.
- **`_ASSESSMENT_ATTEMPTS = 2`, not 1 or 3** — `chat_json()` already retries
  inside each attempt; two keeps the worst case at six model calls.
- **Oversized text is condensed in exactly two passes** — digest, then one
  nine-domain judgement, no per-chunk verdicts to merge (blinding needs the
  whole Methods in view) — and **`_condense()` checks `len(digest)` against
  the budget, not `ProcessingStatus`**: `TRUNCATED` names the recursion
  ceiling, not the size of what it produced (a 21,269-char digest was measured
  emerging from a 200-char budget). Carries a negative control,
  `test_the_guard_does_not_reject_a_digest_that_actually_fits`.
