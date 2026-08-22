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

## Repository process

- **Squash, rebase and merge commits are all enabled on purpose — do not
  disable two of them to protect the release tag** (#78, closed 2026-08-13).
  The old release recipe required `--merge` so the tag landed on `main`'s
  first-parent line, and #78 proposed enforcing that with one
  `gh api -X PATCH`. Measured before acting: **8 of the last 40 merged PRs
  landed as single-parent commits** (#60, #62, #63, #65, #66, #69, #74, #76),
  each collapsing a 3–7 commit branch, so squash is a deliberate habit for
  ordinary feature PRs and not an accident waiting to bite. GitHub cannot
  condition the merge method on the branch — `allow_squash_merge` is
  repo-wide and no ruleset rule expresses "release PRs must be merge
  commits" — so enforcement would have cost the habit to protect one PR a
  month.

  **The requirement was removed rather than enforced.** `main`'s tip is on
  `main`'s first-parent line under every merge strategy, so the recipe tags
  `main`'s tip after `git pull --ff-only`. The two guards it carries replace
  a constraint that never checked what mattered: `HEAD == origin/main`
  catches a stale local `main`, and grepping `__version__` at the tag target
  catches tagging a commit that does not carry the version — which is the
  failure `release.yml` would otherwise find *after* the release is public
  and the version burned. Neither is something a merge method could have
  caught. The recipe is in `HANDOVER.md` under "Cutting a release".

  The `protect_main` ruleset is a separate thing and closes none of this: it
  covers deletion, non-fast-forward pushes and code scanning, and is silent
  on merge strategy.

## Type checking (#81)

- **`**kwargs: object` and `**kwargs: Any` coexist on purpose — do not make
  the 25 sites uniform.** Seven bags are `Any`, eighteen are `object`. The
  trigger is narrower than "is it splatted?", and getting it wrong in either
  direction is why this entry exists: a bag needs `Any` when it is splatted
  into a callee that still has **a typed named parameter the call does not
  itself fill**. `object` is the stricter annotation and cannot survive that
  case — a parameter declared `str | None` will not accept an `object`, so
  `**dict[str, object]` makes the forwarding call *unchecked* rather than
  checked. It produced nine of the errors #81 fixed.

  "Splatted" alone does not decide it. `LLMClient.chat` and `LLMClient.embed`
  are both splatted into typed signatures and both correctly keep `object`,
  because they pass every named parameter of the callee explicitly and the
  residual can only land on the callee's own `**kwargs: object`. A reader
  applying the looser rule would "fix" two sites that are already right.

  The seven: `agents/base.py`, `llm/client.py` (×2, `generate` and
  `embed_batch`), `llm/providers/get_provider()` — where the rule is written
  out and which the other three point back to — plus `providers/ollama.py`
  (×3, at the two `ProviderCapabilities`/`ModelMetadata` subclass
  constructors and `embed`), which predate #81 and obey the same rule.

  What the widening does **not** cost is the boundary: `object` already
  accepts every keyword argument a caller can pass, so the two annotations
  are indistinguishable from outside. The loss is confined to the body, and
  all seven bodies only forward. That is what makes keeping the other
  eighteen at `object` worth doing rather than merely tidy — and why
  widening them would weaken annotations that cost nothing today, while
  narrowing the seven re-breaks the calls.
- **`_reject_unusable_stream()`'s `isinstance(handle, io.TextIOBase)` is
  unreachable per the annotation, and stays.** Nothing can subclass both
  `IO[bytes]` and `TextIOBase`, so `warn_unreachable` calls the body dead. The
  annotation is a request, not an enforcement, and the guard exists for the
  caller who passes `open(path)` in text mode — which the signature's `str |
  Path` branch makes a plausible slip. Deleting it to satisfy the checker
  restores a failure inside `codecs` reading "can't concat str to bytes",
  which names nothing the caller did. Argued inline; carries
  `# type: ignore[unreachable]`.
- **Deliberately-unchecked code takes an inline `# type: ignore[code]`, never
  a per-module `ignore_missing_imports` override.** `warn_unused_ignores`
  reports an inline ignore the day it stops suppressing anything; it can never
  report a stale override. #81 removed a stale `# type: ignore[arg-type]` in
  `retractions.py` that this setting caught. bmlib now has **no untyped
  imports at all** — see the next bullet.
- **`pdf_converter.py` imports `pymupdf`, not the legacy `fitz` alias, and
  the `pdf` extra floors at a release that ships `py.typed`.** PyMuPDF added
  the marker in 1.27.1, but `setup.py` writes it only into the `pymupdf`
  package; the three modules it copies into `fitz/` are never covered. So
  `import fitz` costs a `# type: ignore[import-untyped]` that **no PyMuPDF
  release can ever retire**, and that ignore switches off type checking for
  the whole module — verified: under the alias, a call to a non-existent
  PyMuPDF attribute is not reported; under `import pymupdf` it is an
  `attr-defined` error. This is the case the previous bullet's convention
  cannot handle, because the ignore would never go stale and so would never
  be revisited. The floor is `>=1.28.2` (current when set); `>=1.27.1` is
  the minimum the type reason justifies, with the module name itself
  arriving in 1.24.3.
- **`fetch_pubmed()`'s `count == 0` return stays *ahead* of the
  history-session guard, and that is only safe because `_esearch()` refuses
  an absent `<Count>`.** With `usehistory=y` NCBI returns a session even for
  a zero-hit day, so an empty day legitimately needs no session and must
  report `completed` — moving the guard first turns every quiet day into a
  `failed` fetch that `sync` retries forever. But the ordering means
  anything that reaches `count == 0` bypasses the guard entirely, which is
  how a rejected search used to sync as a quiet day: `_text()` returns
  `None` for an absent element and `or "0"` made an `<ERROR>` document a
  count of zero. The two decisions hold each other up — keep the ordering,
  keep the refusal, and do not collapse the refusal back into `or "0"`.
  Pinned by `test_a_rejected_search_is_not_reported_as_a_quiet_day` and
  `test_a_genuinely_empty_day_still_completes`; both verified by mutation.
- **mypy must run in the dev venv, and `uv run mypy` takes no arguments.**
  Every extra but psycopg2 ships its own `py.typed` — that one is covered by
  `types-psycopg2` in the `dev` extra — so against a bare interpreter mypy
  reports the optional imports *and `jinja2`, a core dependency*, as missing
  stubs. #81 opened claiming 24 errors in 15 files because of exactly this.
  Installing the extras took it to 22 in 11; adding `types-psycopg2`, which
  #81 also did, retires the two `psycopg2` errors and leaves **20 in 10** —
  which is what re-running the gate against `main` in a `.[all,dev]` venv
  reproduces today. Scope and settings live in `pyproject.toml` so the bare
  command is what CI's `types` job runs.

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
  prose branch, and routes the caption's own content by the open
  `<caption>`'s owner (`caption_stack`)** — asking about the section first
  blanks the caption and renames the section; the same branch deliberately
  drops non-caption `<p>` inside figures/tables. The owner replaced a stored
  `in_caption` boolean in issue #123, which both mis-routed a nested
  caption and truncated the enclosing one at its close. Pinned by
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
  the counter.** All three call sites return immediately after it, so a
  recorded failure could never be reported; threading it would be dead
  plumbing that reads as coverage. A comment at the handler says so, since
  the other eight swallowers were wired. This is not the same as saying the
  failure goes unreported — #68 gave that half its own keyed one-shot
  warning, which is a separate channel from the exhaustion report and reaches
  the operator on a run that otherwise succeeds. See the #68 entries below. Its *cache-write* half was
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

**The helper itself now lives in `bmlib/_atomic.py` as `atomic_write()`**,
promoted out of `fulltext/cache.py` by #73 when `templates/` turned out to
need the identical publish. Everything below still holds of it word for word
— that is the point of having promoted it rather than copied it — and every
test named below still lives where it did. What the promotion added is
`tests/test_atomic.py`, which pins the few guarantees that belong to the
helper rather than to either call site: the 38-character temporary-name
overhead the cap below is arithmetic over, and the exception the caller gets
back.

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

## templates — install_defaults writes atomically (#73)

The same defect as #70, found in a second place, which is what made
`atomic_write` shared rather than copied. Four choices here read as
tidy-ups and are not; all four are pinned by
`test_templates.py::TestInstallingDefaultsIsAtomic`, and six mutations were
run against it, six caught.

Read this section knowing that **bmlib ships no templates of its own** —
there is no `bmlib/templates/defaults/`, and `package-data` is `py.typed`
alone. `default_dir` is always the caller's own prompt directory, which is
what makes the second and fourth bullets matter rather than being theoretical.

- **`if not dest.exists()` was left exactly as it was.** It looks like the
  bug — it is the line that turns one truncated file into a permanently
  truncated file — but it is not what needed fixing, and "repair a template
  that looks wrong" is not implementable: a user edit is the whole point of
  the user directory, so a file differing from the default is the *expected*
  state and cannot be told from a truncated one. Making the write atomic is
  what makes the guard true: a faulted copy publishes nothing, so `dest`
  does not exist and the next call installs it. Reverting the write to
  `write_text` fails both of the original tests.
- **The copy is bytes, not text.** `src.read_text()` applies universal
  newlines and `write_text` translates back through `os.linesep`, so the
  installed template's line endings need not be the source's. **This is not
  Windows-only, and the round trip is not lossless on POSIX** — an earlier
  draft of this entry said it was, and the CRLF fixture in
  `test_a_template_is_copied_byte_for_byte` fails *here*, on Linux CI,
  against a re-encoding implementation. Both platforms corrupt, in opposite
  directions; the round trip preserves bytes only where the source's endings
  already match the platform's, and `default_dir` belongs to the caller, so
  they need not. What the change buys is fidelity of the installed
  **artefact**, for whatever editor or tool the user opens it with. It is
  deliberately *not* a claim about what reaches a model — `_FallbackLoader`
  reads every template with `read_text` too, so Jinja2 sees `\n` either way,
  and "a prompt is sent verbatim" was the wrong reason for a right change.
  An implementation that is atomic but still re-encodes fails that one test
  and nothing else.
- **A dangling symlink at the destination is skipped, not published over.**
  This is the one line the atomic publish *made* necessary rather than
  fixed. `exists()` follows symlinks, so one pointing at a missing target
  reads as absent — and `os.replace` then replaces the link itself, where
  the `write_text` it replaced wrote *through* the link and raised
  `FileNotFoundError`. A user who symlinks a prompt at a volume that is
  unmounted at startup came back, before the guard, to find the link gone
  and the default in its place, announced by an `INFO` line indistinguishable
  from an ordinary first install. Skipping is right rather than "install
  anyway": the link is the user's stated intent about where that prompt
  lives, and this method's whole job is to not overwrite the user. It is
  `WARNING` rather than silent because rendering then falls back to the
  default with the user's own version unreachable, which is exactly the
  substitution the guard exists to make visible. Note that the cache side
  has the same exposure by construction and does *not* guard: `save_pdf` /
  `save_html` overwrite unconditionally, a cache entry is bmlib's own
  storage rather than a user's file, and `clear()` would unlink such a
  symlink regardless.
- **`OSError` propagates, aborting the templates after the one that
  failed.** Collecting the errors and continuing looks kinder and is worse:
  the next call installs whatever is still missing, so the loop is already
  self-repairing, and a caller who cannot write is better told once than
  handed a partial installation and a summary. This is the same call
  `save_html` / `save_pdf` make. Review of the first cut found this bullet
  *unpinned* — both original tests installed a single template, so an
  implementation that rolled every success back on failure, the exact
  opposite of the documented contract, passed them both.
  `test_a_faulted_copy_leaves_the_ones_already_installed_alone` is the
  three-template case that closes it, and it is also why the scan is now
  `sorted()`: which templates are left uninstalled has to be reproducible
  before it can be asserted.

One thing deliberately *not* asserted: the fault is injected at `os.fsync`,
so on the unfixed code the test fails with `DID NOT RAISE` rather than by
finding a truncated file. That is honest rather than convenient — the
unfixed code has no `fsync` to fault, and a test faulting `write` instead
would pass against an implementation that omits the `fsync` and so
publishes a file whose blocks were never written. The directory is asserted
*empty*, not merely free of `scoring.txt`, because a leftover temporary file
is the other way this can go wrong: dropping the cleanup `unlink` fails that
assertion and nothing else in the suite.

The other thing an error injection cannot reach is the **killed process**,
which is half of what the issue names. Injecting at `fsync` only proves the
tidy-up: with nothing to overwrite, a plain `open(dest, "wb")` that unlinks
on failure is indistinguishable from an atomic publish *after the fact*, and
review confirmed by mutation that such an implementation passed every test
in the class. They differ only while the bytes are in flight, which is
precisely what survives `SIGKILL`, so
`test_the_destination_never_exists_until_it_is_complete` asserts on that
instant instead — the target name is absent, and the bytes are staged under
another name, at the moment `os.replace` is called. Without it the house
rule in CLAUDE.md ("a new writer of user-visible files uses this helper") is
unenforced at the very call site it was written for.

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

## fulltext — the free-PDF tier and what it reports (#68, #72, #79, PR #80)

- **The free-PDF allow-list is answerable to the records, not to taste.**
  `_FREE_PDF_AVAILABILITY_CODES` allow-lists `availabilityCode` (`OA`, `F`)
  and consults the `availability` display string *only* for an entry carrying
  no code; a present-but-unknown code is rejected without reading the label,
  because an unknown value must under-credit rather than risk a paywalled
  download. **Run `scripts/sample_free_pdf_urls.py` before changing either
  list** — #79 was precisely a value (`"Open access"`, 95.7% of free-PDF
  entries) that never appeared in what bmlib accepted, and the sampler counts
  the distribution *before* the allow-list filters for that reason: counted
  after it, it could only ever confirm the list. Pinned by
  `test_fulltext_service.py::TestFreePDFAvailability`.
- **Both access fields are type-checked before the membership test, and that
  is not defensive padding.** `x in frozenset` *hashes* `x`, so a JSON object
  where a string was expected raises `TypeError` — a `_BUG_TYPES` member,
  which would report Europe PMC's malformed bytes as a bmlib defect *and*
  spend the one-shot `bug:TypeError` slot a later real defect needs.
  `_extract_free_pdf_url` guards the container one level up for the same
  reason: `.get("fullTextUrl", [])` returns `None`, not `[]`, for a key
  present with a JSON null.
- **`_BUG_TYPES` is a deny-list, and `ValueError`/`SyntaxError` are
  deliberately outside it.** `json.JSONDecodeError` *is* a `ValueError` and
  `xml.etree.ElementTree.ParseError` *is* a `SyntaxError`, so admitting
  either would file an ordinary malformed remote response as a bmlib defect.
  A deny-list because the legitimate failures are varied while the
  always-a-defect set is small. Argued inline at `_BUG_TYPES`.
- **`on_bug` fires at the moment the exception is swallowed, and is a
  mandatory field.** Every exit-based alternative is the defect itself:
  `describe()` is read only on total exhaustion, which is the exit this case
  never reaches, so the next early return would silently re-break it. An
  unwired callback is not a quieter channel but total silence — hence no
  default. `_TierFailures.unreported()` is the deliberate opt-out for direct
  helper calls and tests. Pinned by
  `TestASwallowedBugDoesNotStayAtDebug` and
  `test_a_record_cannot_be_built_without_deciding_about_on_bug`.
- **The one-shot warning keys are built from a bounded `origin` written out
  at each call site, never from `result.source`.** Tier 0's `source` comes
  from the fetcher's `FullTextSourceEntry`, and OpenAlex derives it from the
  location's venue display name: one distinct, remote-data-derived string per
  journal, which turns "reported once" into one warning per article over a
  bulk sync. The source still appears in the message.
- **A `_warn_once` key names the *cause*, not the site.** `"cache-write"` as
  a bare literal let a transient `OSError` early in a run permanently silence
  a genuine `TypeError` inside `save_pdf` — the failure #72 exists to fix —
  and, in the other order, presented a type error to the operator as a full
  disk. Pinned by `test_html_and_pdf_write_failures_share_one_warning` and
  its per-cause companions.
- **`FullTextCache.save_pdf`'s own magic-byte rejection sits at `DEBUG` on
  purpose.** At `WARNING` it emitted a line per article for the dominant
  measured failure — Unpaywall landing pages, 14 of 28 probes — underneath a
  message promising the report was one-shot, defeating the one-shot for the
  very cause the 5% rule selected it for. The article-level detail is still
  there at `DEBUG`; `TestThePerArticleDetailThatTheWarningPromises` pins it.
- **The `WARNING`-level split is a measured rate against a rule fixed
  beforehand**, not a preference: under 5% of attempts, per-article
  `WARNING`; at or above it, one line per `(tier, cause)` plus per-article
  `DEBUG`. Re-deciding it means re-running the sampler, not re-reading the
  code. The *exception* path is one-shot per `(tier, exception type)`
  regardless of the rate, because it fails every article once it starts
  failing.

## fulltext — a PDF's metadata title (#56, PRs #82, #83)

The argument lives in `bmlib/fulltext/_titles.py`, which is unusually heavily
commented for that reason. The entries here are the pointers.

- **`metadata["title"]` stays verbatim; the judged answer is
  `ConversionResult.title`.** Sanitising the one key would make the dict lie
  about `creator` and `producer` beside it, and a caller debugging provenance
  needs the original string, junk and all.
- **The reject-list has exactly one member, and a shape the corpus never
  showed does not become a member however obvious it looks.** That is the
  reject-list this design exists to avoid — not one of the shapes issue #56
  proposed (`.docx`, `"untitled"`, the file stem) appears anywhere in the 235
  measured PDFs. **Run `scripts/sample_pdf_metadata_titles.py` before
  changing it.** `_MIN_TITLE_WORDS` is now kept as defence-in-depth rather
  than as a member the corpus earns — anchored containment rejects the row
  that admitted it — and says so at its definition;
  `TestTheOneBackstopMember` pins both halves.
- **`looks_like_junk` takes the title alone.** It carried the whole metadata
  dict against the day a member wanted `creator` or `producer`, but an
  argument added for a member the corpus never earned is the same species of
  speculation as the reject-list entry. Re-adding it is one line, here and at
  the single call site; what the measurement actually said about `creator` is
  recorded above `_MIN_TITLE_WORDS`, so the next reader does not re-derive it
  and reach the opposite conclusion.
- **Containment is anchored to whole tokens — do not simplify it back to
  `wanted in page`.** `normalise` exists to produce tokens, and a bare
  substring test throws those boundaries away in the *accepting* direction:
  a `/Title` truncated mid-word, which producers emit routinely, matched the
  page it was cut from and then beat the fallback that would have recovered
  the whole line. Pinned by `TestCorroborationIsAnchoredToWholeTokens`.
- **An empty page accepts and an unreadable page rejects.** The asymmetry is
  the distinction the samplers draw between an unmeasured probe and a failed
  one: a page read as carrying no text makes corroboration a test that
  *cannot be run* — rejecting would blank the title of every image-only scan
  — while a page whose extraction *raised* is a test that failed, and a fault
  is where there is least reason to trust what a file claims about itself.
  The backstop applies in both, so an unrunnable check is never a free pass.
- **The empty-normalisation guard is masked twice and still load-bearing
  once.** The backstop rejects a zero-word title before it and anchoring
  would reject one after — but neither covers a title normalising to nothing
  against a page that is *also* empty, which would otherwise hand back
  `"###"` as an image-only scan's title. Argued inline;
  `test_the_empty_normalisation_guard_stands_on_its_own` pins it.
- **`_LINE_NUMBER_RE` rests on its unit tests, not on the corpus.** Four
  independent mutations of it change the answer on zero of the 235 rows, so a
  green corpus run has *not* checked it —
  `TestALineNumberedManuscriptStillCorroborates` has. Both digit bounds are
  deliberate and argued at the pattern.
- **`accepted_metadata_title` returns `str | None` and sends its four
  rejection reasons to the log.** Every caller asks one binary question and
  would discard a richer answer; the one party who wants the reasons is the
  human debugging why a title vanished from one PDF, and `DEBUG` is where
  they get them. `TestARejectionSaysWhy` pins each line, with a control that
  an accepted title logs no rejection.
- **The sampler deliberately does not import `_titles.normalise`, and a
  future refactor must not "deduplicate" the two.** A corpus labelled by the
  rule under test can only ever confirm that rule. For the same reason the
  sampler writes to `*.unreportable.json` when a population trips the
  unmeasured-share threshold: a throttled run must not replace evidence a
  later reader takes as measured.
- **A bioRxiv attempt records its posting *day* rather than the run pinning
  its window.** Pinning would make one date range serve both "what am I
  sampling" and "what do I owe", and those diverge by a day every day.
  `MAX_UNMEASURED_ATTEMPTS` retires an attempt from being *offered* while it
  keeps being *counted*, in `tally_previous` and in the ERROR rule — because
  forgetting it is the silent loss the accounting exists to prevent.

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

## publications — a completed day is a durable claim (#88–#91, PR #93)

**CLAUDE.md's "A completed day is a durable claim" argues this in full — read
it there.** It settles the two-rules-of-different-kinds split, why the
shortfall rule is a floor rather than strict inequality, why PubMed counts
delivered elements, why each envelope is checked rather than defaulted, and
why `sync()`'s status handling is an allowlist. Every guard below is pinned by
a named test and was verified by mutation. Only what it omits:

- **A small shortfall completes, and that is not an oversight.** Making
  `reconcile_delivery` strict — any shortfall fails — is the obvious
  "simplification" and it is a real defect: a `failed` day is re-offered by
  `_days_needing_fetch()` on *every* later run, so a gap that is benign and
  permanent re-fetches and re-merges the whole day for the rest of an
  installation's life, growing with the date range. Pinned by
  `test_a_small_shortfall_completes` and its per-fetcher twins.
- **"A completed day is never offered again" is shorthand, not the rule.**
  `_days_needing_fetch()` also re-offers a completed day that was fetched
  before the day was over, and one whose `recheck_days` window has passed.
  Neither weakens the argument — the default is `recheck_days=0`, and a past
  day fetched after it ended is what the reconciliation rules protect — but
  the unqualified form is false and was written into six documents in #88's
  first round. The `today` half of it was its own defect, **#95**, now fixed;
  see the next entry.
- **`SHORTFALL_FAILURE_RATIO = 0.5` is fixed before measurement**, unlike
  every other threshold in bmlib. Do not cite it as measured and do not
  tighten it by taste — **#92** is the sampler that would earn a different
  number. The docstring says so; keep that paragraph honest if the number
  moves. **#105 raised what that unmeasured floor exposes by roughly 24×,
  and did not touch the constant.** Before it, no PubMed day above 9,999
  records was walked at all, so a `completed` PubMed day could lack at most
  4,999 records; now every part of a partitioned day may be up to half short
  without failing, and the day's total is judged against the same floor, so a
  242,216-record day can be `completed` missing some 121,000. The rule is
  unchanged and the population it applies to is not — which is the argument
  for #92 being more urgent than when it was filed, and equally the reason
  #105 did not quietly move the number instead.
- **The floor is exclusive.** Delivering exactly half passes. Pinned by
  `test_exactly_the_floor_completes`, which exists because `<` versus `<=`
  here is a one-character edit no other test notices.
- **`stalled` is not redundant with the floor**, though most reproductions
  trip both. It is the only rule that catches a session expiring on a *late*
  page — 500 of 1,000 clears the floor. Pinned by
  `test_a_session_dying_on_a_late_page_fails` and
  `test_an_empty_page_with_only_a_few_records_outstanding_fails`, written
  after mutation showed the earlier tests survived removing it.
- **bioRxiv's envelope check is deliberately the weakest of the three, and
  the obvious tightening is wrong.** It refuses a body carrying *neither* a
  `collection` key *nor* messages — one making no claim about the day — and
  does **not** require a list `collection`. The first round of #88 wrote
  `data.get("collection", [])` guarded by an `isinstance`, which only fires
  when the key is present and non-list, so `{"error": ...}` and `{}` still
  completed as quiet days: the very bug the guard was added for.
  `isinstance(data.get("collection"), list)` is the tempting fix and is
  **not** safe: bioRxiv is known to report a quiet day by omitting `total`,
  but whether it also omits `collection` is unmeasured, and a wrong
  tightening fails every quiet day on every run for ever. The residual —
  an error body carrying messages and no collection still reads as quiet —
  is irreducible without knowing the `messages[0].status` vocabulary.
  **#94** is the sampler that would measure both and let this be tightened.
  Pinned by `test_a_body_carrying_neither_a_collection_nor_messages_fails`
  and `test_a_quiet_day_completes_whether_or_not_it_sends_a_collection`,
  which asserts *both* possible quiet-day shapes so the guard cannot come to
  depend on the unmeasured answer.
- **An absent count is `None`, never `0`.** `promised=0` is a source saying
  the day is empty, which any delivery satisfies; `promised=None` is a source
  saying nothing. bioRxiv's `records_total or 0` collapsed the two, which
  silently disabled *both* the shortfall and the stalled rules — the stalled
  flag is conditioned on knowing the total — so a first page carrying records
  and no `total` followed by an empty page completed as a whole day. Records
  delivered against `None` now fail; nothing delivered against `None` is the
  quiet day and passes. Pinned by
  `test_records_delivered_without_a_total_cannot_complete` and its negative
  control `test_nothing_delivered_against_no_count_is_a_quiet_day`.
- **Every fetcher must compute `stalled` itself.** It defaults to `False`,
  which is the value that *disables* the strongest rule, and OpenAlex took
  that default through #88's first round — so the one source whose cursor can
  be invalidated mid-walk was judged by the floor alone, and 600 of 1,000
  completed. An empty page also ends the walk, which additionally bounds a
  loop that `while cursor is not None` does not. Pinned by
  `test_a_walk_that_stops_serving_mid_count_fails` and
  `test_an_empty_page_ends_the_walk_rather_than_repeating_it`.
- **PubMed counts delivered records by element name, not by child count.**
  `len(list(root))` counts every child of `<PubmedArticleSet>`, and
  `<DeleteCitation>` is a legal one. Counting it as delivery is wrong twice:
  it inflates the count so a real shortfall clears the floor, and it makes a
  page carrying nothing else fail the `delivered == 0` stall test. The
  book-chapter test cannot catch this — it separates *delivered* from
  *parsed*, which any child-counting expression also satisfies. Pinned by
  `test_a_page_of_delete_citations_is_not_delivery`.
- **A shortfall that completes is returned, not only logged.** Up to half a
  day's records can go missing on that path, and the day is never re-offered,
  so a log line is not a surface any caller can query afterwards.
  `FetchResult.note` carries it to `SyncReport.notes`, deliberately *not* to
  `errors`: an error names a day that will be retried, a note names one that
  will not. Pinned by `test_a_short_day_that_completes_is_reported`.
- **An OpenAlex page emits its valid records before the page is refused**, so
  a first page with `"meta": null` fails with `record_count=1`, not 0. Those
  records were already handed to `on_record` and are stored; the day is
  retried regardless. The ordering of the three checks in the loop is what
  makes that true — do not "tidy" them into one block.
- **A permanently-unstorable record pins its day into a retry on every run.**
  Accepted knowingly (#90): loud — an ERROR and a `SyncReport.errors` line
  each time — beats a day silently missing a record it holds by name. The
  alternative considered and rejected was failing only on a *total* store
  failure.
- **The per-record `except Exception` in `sync()` stays broad.** One bad
  record must not lose the batch. What changed is that it logs the exception
  *type*, which is what tells a bmlib defect from bad source data; narrowing
  the guard instead would abort the day. Pinned by
  `test_the_store_failure_log_names_the_exception_type`.

## publications — a day fetched before it ended is not durable (#95)

`_days_needing_fetch()` re-offers a completed day whose `downloaded_at`
precedes **12:00 UTC on the following day**, which replaced an unconditional
`if current == today` branch. The manual's *When a day is over* argues it in
full. What is easy to get wrong later:

- **The 12:00 is not a safety margin, and must not be "simplified" to a date
  comparison.** Day *D* ends last in UTC−12, whose midnight is noon UTC on
  *D+1*. All three built-in sources are US-based (UTC−5 to UTC−8), so
  comparing UTC *dates* calls a fetch at 00:30 UTC on *D+1* durable while
  PubMed's day *D* still has four and a half hours to run (US Eastern in
  winter; three and a half on daylight time, and longer still for a
  Pacific-time source); comparing *local* dates is up to 16 hours out for a
  machine in Sydney. Pinned by
  `test_a_second_before_noon_utc_the_next_day_is_not` and
  `test_an_offset_timestamp_is_compared_as_an_instant_not_as_a_wall_clock`.
  **Both figures were rounded wrong in the first round** — "five hours" and
  "15 hours" — across five documents; they are arithmetic, so check them
  rather than copying them.
- **The comparison is `>=`.** A fetch at exactly the boundary saw the whole
  day everywhere. `<` versus `<=` here is a one-character edit only
  `test_noon_utc_the_next_day_is_late_enough` notices, which is why it exists
  — the same reason `test_exactly_the_floor_completes` does. That claim was
  **false when first written**: the negative control beside it,
  `test_a_day_fetched_after_it_ended_everywhere_is_not_offered_again`, used
  the identical boundary timestamp, so the two tests were the same test twice
  and died to the same mutation. The control now sits days past the boundary.
  A test asserting it is the sole pin for something is worth checking against
  its neighbours before it is believed.
- **Every day in a window is judged against its own boundary.** Passing
  `date_from` where the loop passes `current` **survived the entire suite**
  when this landed, because all eleven of the rule's tests used a one-day
  window — and it silently reintroduces #95 for any cron after 12:00 UTC.
  Pinned by `test_each_day_in_a_window_is_judged_against_its_own_boundary`.
  The general lesson: a rule that selects over a range needs at least one test
  whose range has more than one answer in it.
- **Removing the `today` branch was not a simplification for its own sake.**
  That instant is also exactly the point beyond which "now" cannot fall inside
  day *D* anywhere, so the timestamp rule *subsumes* the special case rather
  than approximating it — and with it gone, the wall clock no longer *decides*
  whether a completed day is done, which is what makes the rule testable
  without faking the clock. It is still read, but only as the upper bound
  below, which can move the answer towards a re-fetch and never away from one.
  Pinned by `test_today_is_still_offered_although_the_special_case_is_gone`.
- **A `downloaded_at` that cannot be read fails closed, and "unusable" is
  three shapes, not one.** Naive, unparseable, and not-a-string all mean the
  same thing to the rule; the naive case is the one that would otherwise raise
  `TypeError` from inside day selection and abort a whole sync before a record
  was fetched. Not-a-string is the shape a change of the PostgreSQL DDL to a
  real timestamp type would produce, which
  `test_the_durability_rule_can_read_what_each_backend_stores` guards from the
  other side — a **CI-only** guard, since that failure is reachable only
  through psycopg2 and the SQLite half proves nothing `test_sync.py` does not.
- **A `downloaded_at` that reads cleanly but cannot be true fails closed too,
  and that gap was live for a round.** A fetch cannot have happened in the
  future, so a restored backup, a bad RTC or an external writer could put a
  past-the-boundary timestamp on a running day and every such day read durable
  forever. The guard was loud about a value it could not parse and silent
  about one asserting the day was fetched tomorrow — #95's own failure mode.
  `_CLOCK_SKEW_TOLERANCE` is five minutes and is **a fixed choice, not a
  measured one**; unlike `SHORTFALL_FAILURE_RATIO` it is bounded on both sides
  by an asymmetry rather than by taste — too tight costs one merged re-fetch,
  too loose loses a day permanently — which is the argument for keeping it
  small rather than generous. Pinned by
  `test_a_timestamp_from_the_future_is_not_read_as_durable` and its negative
  control `test_a_clock_a_few_minutes_fast_is_still_believed`.
- **`last_verified_at` gets its own reader, deliberately laxer.** Only the
  calendar date is used, so a naive value is perfectly usable here where it is
  not for the durability rule; routing it through `_read_aware_timestamp`
  would fail closed on every naive row and re-fetch the whole window on every
  run for a `recheck_days` caller. What the two share is why they exist: read
  raw — as this column was for a round after #95 landed — a corrupt value
  raises `ValueError` from inside day selection, which escapes `sync()` (whose
  `try` carries only a `finally`) and kills the whole multi-source run before
  a single record is fetched, `SyncReport` and all. That is worse than the
  per-day losses the rest of these rules guard against, because it is total.
  A stored `NULL` is **not** unusable — it is the documented "never verified"
  state — so it rechecks without a warning; warning on it would fire for every
  row of a fresh install and tune the real warning out. Pinned by
  `test_an_unusable_last_verified_at_rechecks_rather_than_raising`,
  `test_a_non_string_last_verified_at_rechecks_rather_than_raising` and
  `test_a_null_last_verified_at_rechecks_without_warning`. The non-string case
  needs its own test even though `downloaded_at` short-circuits before rule 4
  is reached: a DDL change moving *both* columns never gets here, only one
  moving this column alone does — and without the test, removing the
  `isinstance` guard survived the whole suite while turning a recheck into a
  `TypeError` that aborts the run.
- **It does not fix late indexing, and must not be stretched to.** A record
  that appears for day *D* three days later is not covered by any rule about
  when *D* ended. `recheck_days` is what exists for that.
- **The extra re-fetch is the fix working, not a cost to optimise away** — but
  state it correctly. Under the default window `[yesterday, today]` it is
  exactly one extra per run, two rather than one, since day *D* is offered
  again on *D+1*. A window of three days or more, run before 12:00 UTC, pays
  one more (three); it does not grow with the window beyond that, and vanishes
  for a run at or after 12:00 UTC. On the **first run after upgrading** it is
  larger and one-off: every row the old code stored was written while its own
  day was current, so none is durable and the whole window is re-fetched once
  — measured at 29 of 29 days for a 30-day window, per source. All merged by
  `store_publication()`. The first round of docs said this "costs nothing
  under the default window", which is the one claim here that was simply
  arithmetic and wrong.
- **The default two-day window never certifies a day for a run before 12:00
  UTC**, and that is a property of the window, not a defect in the rule. Day
  *D* is fetched on *D*, re-fetched on *D+1* at the same hour — still short of
  its own boundary — and then the window slides past it. No records are lost:
  the *D+1* fetch happens after day *D* ended for every US-based source. But
  the row is left permanently non-durable, so a caller who later widens the
  window re-fetches it. Running at or after 12:00 UTC, or with a window of
  three days or more, settles every day.

## publications — what the durability rule refuses to guess (#98, #99)

Both were raised by the review of PR #97, both pre-existing, and both are
about the same thing from opposite ends: a value the day-durability rule
cannot honestly read. Argued inline in `publications/models.py`
(`_require_datetime`) and `publications/sync.py` (`_validate_window`).

- **`DownloadDay.from_dict()` raises on an absent `downloaded_at` rather than
  defaulting it to now (#98)** — the column is `NOT NULL` in both DDLs, so a
  dict lacking it did not come from the database, and *now* is the single most
  durable-looking value `_day_was_over_when_fetched()` can be handed. The SQL
  path fails **closed** on that column (unreadable, or in the future); the
  model must not disagree with the rule about what an absent value means. The
  other two options were weighed and rejected: returning `None` makes the
  field `datetime | None`, a typing break on a `py.typed` package that pushes
  the decision onto every caller for a state none of them can do anything
  about; keeping the default and logging leaves the fail-open in place, and
  "logged but wrong" is exactly what the #88–#95 family is a register of.
  Pinned by `TestDownloadDayRequiresTheTimestampTheRuleReads`.
- **The dataclass default that stamps now is deliberately kept**, and the
  asymmetry with `from_dict()` is the point: a freshly constructed
  `DownloadDay` describes a fetch that has just happened, while `from_dict()`
  deserialises a row that was already stored. Pinned by
  `test_constructing_a_row_still_stamps_now`, or nothing would notice it being
  "tidied" into consistency.
- **`from_dict()` does not re-judge a timestamp it *can* read** — naive, or in
  the future, both deserialise fine. Faithful deserialisation is the model's
  contract and usability is the rule's; duplicating the rule here would reject
  rows the database legitimately holds and which the rule already answers by
  re-fetching. `test_a_stored_timestamp_is_read_verbatim` is the negative
  control for the ordinary value, and
  `test_a_naive_or_future_timestamp_still_deserialises` pins the two cases
  the claim actually rests on — the review that added it found the claim
  unpinned, since neither of the two named values was covered by anything.
- **Every rejection is a `ValueError` naming the field.** Delegating straight
  to `_parse_datetime` did not deliver the contract the docstring advertised:
  a non-`str` escaped as `TypeError` out of `fromisoformat`, so a caller
  writing the documented `except ValueError` got an uncaught crash, and an
  unreadable string raised `Invalid isoformat string: ''` — which names
  neither the column nor the row, leaving a bulk deserialiser nothing to
  report. A plain `date` is the trap worth naming: `isinstance(datetime_value,
  date)` is true but the converse is not, so it looked accepted and was not.
  Nothing here could ever fail *open* — the durability rule refuses every one
  of these values — so this is a contract fix, not a safety one.
- **`Publication.created_at` / `updated_at` keep the same defaulting
  `from_dict()` just lost**, on purpose. Nothing decides whether work may be
  skipped from them, so *now* is a harmless default there and a load-bearing
  one for `downloaded_at`. The fix is scoped to the column a rule reads.
- **`sync()` validates `date_from`, `date_to` and `recheck_days` at its
  entry, and the helpers do not catch `OverflowError` (#99)** — an `except
  OverflowError` around the arithmetic converts a caller bug into a day that
  quietly looks like it needs no fetch, which is the failure mode this whole
  family exists to remove. A negative `recheck_days`, until now silently
  swallowed by `recheck_days > 0`, is rejected for the same reason.
- **The guard is a *type* check as well as a range check, and the type half
  is the one that matters most.** `datetime` subclasses `date`, so
  `sync(date_to=datetime.now())` satisfies the annotation and every type
  checker, and no value check can see it — `datetime.max == date.max` is
  `False`. Mistaking `datetime.now()` for `date.today()` is a likelier slip
  than any input #99 originally named, and it fails in two shapes: mixed with
  a `date` it raises `TypeError` from the comparison and loses the run's
  report; on **both** ends it raises *nothing*, and writes
  `download_days.date` values carrying a time component that no date-keyed
  lookup can ever match. That row is re-fetched for the life of the
  installation and the table accumulates rows nothing reads. The silent shape
  is why this is a type check and not another value check, and why it is
  worth spending a branch on an input the annotation already claims to
  exclude. `float('nan')` is the same lesson on the other parameter: it slips
  both range checks, because every comparison against it is `False`, and then
  disables rechecking in silence. Pinned by
  `test_a_datetime_window_never_reaches_the_download_days_table` and
  `test_a_recheck_days_that_is_not_a_whole_number_is_refused`.
- **Not every rejection is guarding an exception, and the docs must not say
  it is.** Two of them — a negative `recheck_days`, and `nan` — walked fine
  and were swallowed silently. An earlier draft of `docs/manual/publications.md`
  summarised the table as "each previously raised `OverflowError`", two lines
  below a row saying one of them was silently ignored. The distinction is the
  entire point of the write-up: two were a total run loss, two are silent
  no-ops, one is silent corruption.
- **A window reaching into the *future* is NOT rejected; it returns a
  `SyncReport.notes` line and logs a WARNING.** `_day_was_over_when_fetched()`
  needs a fetch at or after 12:00 UTC on the following day, which a day that
  has not happened can never satisfy — so the row is stored `completed` and
  re-offered on every run forever, and until now at no log level and in no
  field of the report. Permanent *and* invisible is the pair the shortfall
  rule and `FetchResult.note` exist to break up, so this takes the same
  answer they do. Rejecting was weighed and refused: the past half of a
  window ending tomorrow is perfectly fetchable, and raising would discard it
  along with the unreachable half. Pinned by
  `TestAWindowReachingIntoTheFutureSaysSo`, whose negative control keeps the
  ordinary `date_to=today` window quiet.
- **A fetcher that returns a non-`FetchResult` fails its own day, not the
  run.** The `except Exception` around the call already absorbed a fetcher
  that *raises*; one that *returns* — successfully — something without a
  `.status` reached `_resolve_day_status` outside that handler, and the
  `AttributeError` propagated through the `finally` and out of `sync()`,
  losing every source's report while leaving earlier days committed.
  `register_source()` is public, so the caller getting this wrong is a third
  party; this is the same allowlist reasoning that already records an
  unrecognised `status` as failed. Pinned by
  `TestAFetcherThatBreaksItsContractFailsOnlyItsDay`.
- **An *empty* window (`date_from` after `date_to`) is deliberately NOT
  rejected**, and this is the entry most likely to be re-opened, since it sits
  one line from three validations that do raise. It is what the ordinary
  incremental-sync idiom produces the moment it has caught up —
  `date_from = last_synced + 1 day`, `date_to = today` — so raising would turn
  a caller that is simply up to date into a crashing one. Unlike every
  rejection above it writes no row and claims no day, so it loses nothing.
  `test_an_empty_window_is_still_the_ordinary_way_to_ask_for_nothing` is the
  sole pin, verified by mutation: adding the rejection fails that test and no
  other.
- **The boundaries are pinned from both sides, and the placement is pinned at
  all.** The first round's mutation set was chosen from the same mental model
  as the code, so it contained no boundary-shift and no call-relocation
  mutant: `recheck_days=10**9` against a bound of ~739,842 left every value
  in between indistinguishable — including one that accepts a `recheck_days`
  which really does overflow — and moving `_validate_window` below the
  `httpx.Client` build passed the entire suite, though the client is created
  *outside* the `try` whose `finally` closes it, so a raise there strands the
  pool. `test_the_deepest_recheck_the_calendar_allows_is_accepted`,
  `test_one_day_deeper_than_the_calendar_is_rejected`,
  `test_a_window_ending_one_day_earlier_still_runs` and
  `test_the_window_is_refused_before_an_http_client_is_built` close those.
  The lesson generalises: a mutation set written by the author of the guard
  tends to test that the guard *exists*, not that it is *correctly bounded*
  or *correctly placed*.

## publications — how far a PubMed history session can be walked (#96, #105)

Issue #96 asked whether `fetch_pubmed`'s `range(0, count, EFETCH_PAGE_SIZE)`
skips records: `retstart` advances by the page size *requested*, so a short
non-empty page would seem to leave the records between what arrived and the
next offset never asked for. It was found by reading, not reproduced, and it
is **closed as correct** — but measuring it to answer that turned up a
different defect at the same call site, which is #105. Both are argued inline
in `fetchers/pubmed.py`; `scripts/sample_efetch_paging.py` is the instrument.
`--skip-day-sizes` re-runs the three **session** probes in a fixed 23 requests
(1 esearch + 2 bounds + 17 binary-search steps + 1 straddle + 2 slice); the
day-size populations need a full run, about 150 requests at `--days 120`. Two
figures below the sampler does **not** reproduce, and both say so where they
appear: the 500-of-500 walk was a one-off probe, and the `[EDAT]` comparison
has no flag — `measure_day_sizes` always asks for `[Date - Publication]`, and
a test pins that it cannot ask for anything else.

- **`retstart` indexes the session's UID list, not the records delivered.**
  Measured 2026-08-20: a page's record elements are exactly that slice of
  esearch's own `IdList`, in document order, `<PubmedBookArticle>` entries
  included — 50 of 50, and again 500 of 500 across a full 13-page walk of a
  6,403-record day (6,403 delivered, 6,403 promised). The 50 is what the
  sampler re-runs; the 13-page walk was an ad-hoc probe on 2026-08-19 and is
  deliberately not in it, since it downloads ~25 MB for no extra evidence. So
  a record missing from
  a page is a UID the server had nothing to return for, not one postponed to
  the next page: it *was* requested. **Advancing by what arrived would be the
  bug**, not the fix — it would re-request the tail of every short page,
  deliver those records twice, and count the duplicates as delivery, which is
  exactly what would hide a real shortfall from `reconcile_delivery`. Pinned
  by `TestTheWalkIsIndexedByTheSetNotByWhatArrived`, whose two tests fail
  against #96's proposed fix.
- **The session serves only its first 9,999 records** (#105), and says so:
  `retstart=9999` is HTTP 400 — *"'retstart' cannot be larger than 9998. For
  PubMed, ESearch can only retrieve the first 9,999 records matching the
  query. To obtain more than 9,999 PubMed records, consider using EDirect…"*.
  The quiet half matters more: a page whose window crosses the boundary is
  **clamped without a word** — `retstart=9500&retmax=500` returned 499 records
  at HTTP 200. So "walk as far as the server will go" is not a safe fallback;
  the last page it yields is indistinguishable from a day missing a record.
- **Under `[Date - Publication]`, the field bmlib queries, that cap is not an
  edge case — it is a second population.** A record carrying only a year and a
  month is indexed at day 1 of that month and one carrying only a year at 1
  January, so those days are structurally enormous. Measured 2026-08-20:
  **0 of 58 ordinary days** were over the cap (median 4,890, max 8,150) and
  **16 of 16 month firsts and 1 Januarys** were (median 73,266, max 315,282 —
  month firsts 49,543–90,571, 1 January 212,439–315,282). A 60-day sync window
  meets 2. Measuring `[EDAT]` instead gives 4 of 120 days, none above 12,096 —
  so the largest day the right field finds is 26× the largest the wrong one
  does (315,282 against 12,096). The wrong field understates the magnitude by
  that much and attributes to load spikes what the indexing convention does.
  Worth stating because that is the reading a reader reproduces if they sample
  the field the *name* suggests; it was a one-off probe on 2026-08-20 and the
  sampler has no flag to repeat it.
- **An over-cap day was refused before a single record was fetched — and that
  is now history, kept because the reasoning still binds the one case left.**
  Such a day cannot be `completed` — that would durably lose the remainder,
  which is the whole point of the family above — so it was `failed`, and a
  failed day is re-offered on *every* later run, which also meant
  `SyncReport.errors` never returned to empty while such a day was in the
  window (issue #107, dissolved rather than answered — see the partitioning
  entries below). That made the only real question what the doomed run costs.
  Walking first would buy the first 9,999 records once and then
  re-fetch them forever: a six-year backfill carries some 72 structural days,
  ~3 GB per run, storing nothing new. Refusing costs one esearch. The
  trade-off was deliberate and was the maintainer's call: **while it stood,
  those days had no records at all rather than the reachable 9,999** — a
  containment, not a fix, and "no publication is missed" was false for as long
  as one was in the window. **#105 has since landed and partitions such a day
  into sub-queries that each fit**, so the refusal now applies only where the
  ladder cannot reach: a single Entrez date over the cap. The absolute rather
  than the fraction is still the right way to read the loss it was containing:
  9,999 is a fifth only of the *smallest* structural day, a seventh of the
  median month first and a thirty-second of 1 January.
- **Before the containment, an over-cap day *mostly* failed anyway —
  inscrutably.** The walk asked for record 10,000, `raise_for_status()` fired
  on the 400 before the body was read, and the day failed with `Client error
  '400 Bad Request'`: the right verdict, reached after twenty pointless
  requests, naming neither the cause nor the remedy. **With one exception, and
  it is the one that matters.** A day of *exactly* 10,000 records never issues
  a `retstart` above 9,998 — `range(0, 10000, 500)` stops at 9,500 — so it
  never met the 400 at all. It walked to its natural end, its last page was
  silently clamped to 499, and it delivered 9,999 against a promise of 10,000:
  a shortfall of one record in ten thousand, far above
  `SHORTFALL_FAILURE_RATIO`, so the day was recorded **`completed`** —
  durable, never re-offered, one record lost with only a note. Finding that is
  what made the containment worth having: it moved exactly one day-size from
  *silent* success to loud failure, closing a durable loss rather than merely
  improving an error message. **All of that is now history too.** #105 has
  landed, `count > EFETCH_MAX_RETRIEVABLE` routes to `_fetch_partitioned`, and
  a day of exactly 10,000 records is partitioned like any other over-cap day —
  its ten-thousandth record is requested with the rest, so no day-size is
  refused on the cap any more, and none is silently clamped into completing
  short of its promise. Both halves of that are claims about the cap, not a
  guarantee that a day arrives whole: a walk still completes on a note when it
  comes up short while clearing `SHORTFALL_FAILURE_RATIO`, as it does for
  every source. The bullet is kept because the silent-clamp mechanism it
  documents is still what the page walk is written around, and because a
  reader who finds this page while wondering why the walk looks the way it
  does should not have to rediscover the 10,000-record case to learn that it
  was real.
- **The cap is a hard-coded 9,999 and that is a cost, not an oversight.** If
  NCBI *raises* it, bmlib partitions days it could now have walked in one
  session — extra ESearches and one session per part, with no record lost and
  nothing logged above INFO, which is why that direction is acceptable. (Until
  #105 it was acceptable for the opposite reason: bmlib *refused* those days,
  loudly, in an ERROR naming the cap. The change made that direction cheaper
  and quieter, so it is now the sampler and not a log line that would tell
  you.) If NCBI
  *lowers* it, the guard does **not** reliably fail closed, and it is worth
  being exact rather than reassuring about why. The walk meets a 400 only when
  it *requests* a page starting past the live limit, so for any count between
  the lowered cap and the next page boundary — a band up to `EFETCH_PAGE_SIZE`
  wide — no page is ever requested past it: the straddling page is silently
  clamped, the walk ends naturally, and the shortfall is at most 499 records,
  which is far above the failure floor, so the day completes on a note.
  Simulated against a cap of 4,750, counts 4,751–5,000 all completed having
  lost up to 250 records apiece and only 5,200 upwards raised the 400. What
  makes the *current* pairing safe is that 9,999 sits exactly one below a
  500-record page boundary — a coincidence of the two constants, not a
  property of the design. **`scripts/sample_efetch_paging.py` is therefore the
  real guard against a moved cap**, in either direction: re-run it before
  touching either constant, and it reports agreement or `DISAGREES` against
  the live backend.

The rest of this section is #105's fix — the partitioning that replaced that
refusal — and the choices inside it that read as arbitrary and are not. The
full argument is in
`docs/superpowers/specs/2026-08-21-pubmed-day-partitioning-design.md`.

- **Why an Entrez-date range, and not a facet.** Any predicate `P` splits a
  day into `AND P` and `NOT P`, which is disjoint and covering by
  construction — but only if `P` is something every record either satisfies or
  does not, *and* both halves are subdividable by the same step. The facets a
  reader reaches for first fail one of those, and the first failure is the
  dangerous one. **Publication type, MeSH term**: a record carries several, so
  `AND pt1` and `AND pt2` overlap, the same record is fetched twice, and
  delivery is inflated *past the day's own count* — which is exactly what
  would hide a real shortfall from `reconcile_delivery`, the guard this whole
  family exists to keep working. **Journal, language**: can be absent, and the
  vocabulary is unbounded and heavily skewed, so a ladder over one neither
  terminates predictably nor covers. **`NOT P` chains generally**: the
  complement of a facet value is not itself subdividable by the same
  mechanism, so the recursion has no uniform step. A numeric range has both
  properties as arithmetic: `[lo, mid]` and `[mid+1, hi]` tile `[lo, hi]`, and
  each half is the same kind of thing as its parent, so one step recurses to
  any depth. Entrez date is the range every record has exactly one of — and,
  because a structural day's records were indexed across decades, it shards
  such a day well rather than piling it into one bucket.
- **The ladder root is a fixed `1900/01/01 – 2100/12/31`, not derived from the
  target day.** Deriving it would mean assuming how far before or after
  publication a record may be indexed, which is the thing the root probe
  exists to *verify*. Sibling counts come from subtraction (`right = parent −
  left`) rather than a second ESearch, which is sound only because the halves
  tile — measured below, not assumed. A zero-count range is skipped rather
  than recursed, so the decades at either end of the root that hold nothing
  cost no further requests.

The ladder was measured before it was implemented, and again afterwards — the
second time by a descent that deliberately does not import the one it measures
(`scripts/sample_efetch_paging.py --partition`), since a corpus labelled by
the rule under test can only confirm that rule. Every probe is against the
live backend on 2026-08-21:

| Day | Probed by | Count | Root probe | Parts | Depth | ESearch calls | Sum of parts | Stuck |
|---|---|---|---|---|---|---|---|---|
| 2025/01/01 | sampler | 266,421 | = day count | 44 | 13 | 51 | exact | 0 |
| 2024/01/01 | design | 242,216 | = day count | 37 | 13 | 40 | exact | 0 |
| 2024/01/01 | sampler | 242,216 | = day count | 37 | 13 | 40 | exact | 0 |
| 2023/01/01 | sampler | 257,836 | = day count | 41 | 13 | 44 | exact | 0 |
| 2020/01/01 | design | 234,972 | = day count | 37 | 13 | 40 | exact | 0 |
| 2015/01/01 | design | 227,173 | = day count | 36 | 13 | 40 | exact | 0 |

Four things that establishes, each of which the design would otherwise be
assuming: that an `[EDAT]` range term composes with a `[Date - Publication]`
term at all; that the root **covers**, since `count(day AND root)` equalled
`count(day)` on every day probed; that the halves **tile exactly**, since the
leaves summed to the root with no residue, which is what makes subtraction
sound and double-counting absent; and that the ladder **terminates well above
its floor** — depth 13 of the ~17 halvings the root span allows, and no single
Entrez date over the cap on any day probed. The design's own run measured the
largest leaf at 9,931 and a single Entrez day of 2024/01/01 at 2,026 records.
Both bounds matter: leaves sit *just* under the cap by construction, since
halving stops the moment a part fits, which is why a part re-checks its own
count when its session opens and re-partitions rather than walks if it has
crossed the cap since planning.

- **The root probe tolerates long and refuses short, and the asymmetry is the
  same one that governs the day total.** Short (`root < day`) fails the day:
  records of this day are indexed outside the ladder, they are in no part's
  promise, and so every part would reconcile perfectly while the day is
  silently incomplete — the exact durable, invisible loss #88–#95 exist to
  prevent. Long (`root > day`) proceeds: the two counts are two ESearches at
  two instants, and a record indexed between them is stamped EDAT=today, which
  is *inside* the range, so it inflates rather than hides. Requiring equality
  would fail a correct ladder on ordinary drift, and the day-total reconcile
  still judges what actually arrived.
- **A removal between the two root counts also reads as short, and fails the
  day. That is accepted, not overlooked.** The alternative is a tolerance
  band, which would be a second threshold nothing has measured — bmlib already
  carries one (`SHORTFALL_FAILURE_RATIO`'s 0.5, which issue #92 exists to
  measure), and that is one more than is comfortable. The verdict is
  recoverable rather than durable: a failed day is re-offered on the next run,
  which re-probes, so a genuine removal costs one re-planned day and not a
  record.
- **`part_key` is opaque to storage, and the cost of that is a silent
  re-fetch.** The storage layer does not know how a fetcher partitions, so a
  second rung — or another fetcher splitting some other way — needs no schema
  change; typed `edat_lo`/`edat_hi` columns would bake one fetcher's scheme
  into the shared schema. What it costs is worth naming, because it is the
  worst kind: the skip rule is a **string comparison**, so a key format that
  drifts between releases matches nothing, resume degrades to re-fetching
  every unfinished day in full, and *nothing is raised* — a cost with no
  error. Two things answer that, and both must be kept: `_part_key()` is the
  one constructor for the string, with a test pinning its exact output
  literally; and the `part_scheme` column records which scheme wrote the key,
  so a scheme change is visible in the data and stale rows can be recognised
  and dropped deliberately rather than silently mismatching. `part_scheme` is
  written on every row and read back into `PartCheckpoint`, but **nothing
  branches on it** — that is the point of it, not an oversight, and a future
  scheme change is what it is waiting for.
- **A part is not checkpointed unless it reconciled clean, and not unless
  every one of its records stored — but it is always *flushed*.** Three rules,
  all fail-closed, all learned in review of this change. Flushing is the one
  that is unconditional, and separating it from checkpointing was the third
  round's fix: the callback that hands a finished part to `sync()` is the only
  thing that empties the record buffer, so calling it only for a part that
  reconciled clean made the per-part memory bound conditional on the source
  behaving. A degraded NCBI returning 37 short-but-not-failing parts of a
  242,216-record day would then hold every one of those 242,216 records in
  memory at once — the exact peak the per-part flush exists to remove, reached
  precisely when the source is misbehaving. The callback therefore carries
  `PartCheckpoint | None`: the records are stored either way, and the
  checkpoint is what the part has to earn. The two remaining rules are what it
  earns them against. A part that reconciled with a *note* delivered
  short of its own promise without clearing the failure floor; checkpointing
  it would let a later run skip it and credit the full `promised` it never
  delivered, manufacturing the records the note was reporting missing — and
  that later run would carry no note at all, since this run's note dies with
  it if a subsequent part fails. A part holding a record that would not store
  is the same shape: the store failure records the day `failed`, so it is
  re-offered, and a checkpoint written beside the gap would make that retry
  skip the one part holding the missing record. `_store_records` swallows a
  record's own exception so one bad record cannot lose the batch, which is
  precisely why the failure count has to be read back and acted on at the
  checkpoint boundary.
- **A part that reports 0 records having been measured non-empty at planning
  fails the day; it is not dropped.** Two of bmlib's own measurements
  disagree — planning counted this Entrez-date range at *n* > 0, the part's
  own session ESearch now says 0 — and the weaker one does not get to decide.
  Dropping it at INFO was silent at a scale nothing downstream catches:
  fourteen such parts of a 37-part day still deliver 62% of the day, which
  clears the day-level floor, so the day would be `completed`, never
  re-offered, and ~92,000 records permanently absent behind one shortfall
  note. The asymmetry is what settles it — a part that *delivers* 1 of 5,000
  fails the day, so a part that *claims* 0 having been measured at 5,000
  thirty seconds earlier cannot pass. It is reconciled like any other part
  (`delivered=0` against the *planned* promise), which always fails. **The
  cost is a re-fetch of one day**, and it is bounded: the parts already walked
  are checkpointed, and a range that genuinely emptied returns no partition at
  all when the next run plans the day, so it self-heals in one extra day-fetch.
  One residual, named so it is not re-discovered as a bug — and not confined to
  a re-plan. A re-plan carries `known_count` forward from the part's own session
  ESearch while its child counts come from fresh planning probes, so a count
  that *shrank* between the two hands the surplus to a right-hand child by
  subtraction — but `_plan_partitions`'s `descend` derives every right-hand
  child's count the same way (`right = n - left`), so the **first** plan carries
  the identical exposure, not only a re-plan. A reviewer reproduced it on a
  first plan: one record withdrawn immediately after the root probe — a benign
  cause `fetchers/_reconcile.py` itself names — parks the surplus on a
  structurally empty tail leaf such as `edat:2050-10-02:2100-12-31 promised=1,
  actual=0`, because the ladder's root spans 1900–2100. That day previously
  completed on a one-record note; it now fails. The verdict stands — failing
  closed beats the silent loss — and the case is recoverable the same way the
  root probe's own removal is: the next run re-plans from fresh probes, the
  phantom does not recur, the parts already checkpointed are skipped, and the
  day completes. But "recoverable" is not "cheap": a day that partitions at all
  is already over 9,999 records, so the re-fetch this costs is up to ~580
  requests and ~1 GB, not the one day-fetch the phrase implies.

  **Narrowed by the review of PR #114, in the two directions where the
  derivation is unrecoverable rather than merely wrong.** A phantom that
  reaches a *single date* is now measured before the day is refused on it, so
  the `edat:2050-10-02:2100-12-31` shape above measures 0 and disappears
  instead of failing the day — the surplus walks down the empty tail to a leaf
  and dies there. And a derived count of **zero** is measured rather than
  trusted anywhere in the descent, because it is the one derivation nothing
  downstream can repair: any other error still yields a part, and a part
  re-counts itself when its session opens, but a zero yields no part at all, so
  the range is never visited, every part planned around it reconciles
  perfectly, and the shortfall reaches only the day total — where anything
  under the floor completes on a note, durably. Reproduced end to end before
  the fix: a six-record day fetched five, returned `completed`, and carried
  neither note nor error.

  What is *not* narrowed, and stays exactly as recorded above: a phantom whose
  derived count is positive and lands on a range wide enough to become a part
  without reaching a leaf. That part's own ESearch reports 0 when its session
  opens, and the day fails there. Measuring every derived count at plan time
  would close it, at one ESearch per part — some 37 more on a 37-part day —
  and the verdict above already prefers the loud failure to that standing cost.

  Measured cost of what *was* changed: +3 planning probes on a synthetic
  401,500-record, 64-part day (68 → 71), and 24 on a day whose records all
  share one Entrez date. Not re-measured against the live backend, so the
  "40 planning ESearches" in the table above is the ladder **as it was
  measured** and the current one spends a little more.
- **A planning ESearch failure is a returned `FetchResult`, not a raise.** The
  ladder's counting probes are ordinary ESearch requests and fail like any
  other — a 500, a dropped connection, an `<ERROR>` document `_esearch`
  reports as a `ValueError`. `sync()` absorbs a raise and fails the day either
  way, so this costs no records; what it costs is coherence. The under-cap
  path returns `failed` for exactly this, and one public function answering
  the same transient with a return value or an exception depending on how
  large the day happened to be is a contract with a hole in it — the manual's
  own table of what a partitioned day returns lists return values only. Both
  planning call sites (first plan, and the re-plan a part triggers) now return
  it.
- **The PubMed FTP baseline is not the route, and this is recorded so it is
  not re-derived.** NCBI's own 400 suggests EDirect, and the annual baseline
  plus daily update files at `ftp.ncbi.nlm.nih.gov/pubmed/` are the documented
  way to retrieve in bulk. They lose here on three counts. The baseline is the
  **whole corpus** (~37M records, tens of GB) with no publication-date
  selectivity, so reaching one day's 242,216 records means reading all 37M and
  discarding 99.3% of them — two orders of magnitude worse, per day, than the
  ~562 requests the ladder costs (its ESearches measured, its EFetch pages
  arithmetic over the record count; the comparison survives either way). It
  only wins for a *full-corpus* load, at which point `download_days`' entire
  per-day model is beside the point and the question is no longer "how does
  this fetcher walk a day" but "does bmlib have a second ingestion mode",
  which is a product decision and not this fix. And it has no path at all for
  the ordinary incremental case, which is what `sync()` is. A whole-corpus
  ingestion mode remains open as its own question; nothing in #105 forecloses
  it.
- **A part's own count is reconciled against planning's with the existing
  floor, not with `== 0`.** The rule shipped in #105 refused a part whose
  session ESearch reported zero where planning had measured records, on the
  ground that two of bmlib's own measurements disagree and the weaker one does
  not decide. The review of PR #114 found the argument never depended on the
  collapsed count being *zero*: a part reporting 1 where planning measured
  5,000 was walked, delivered its 1, reconciled that 1 against **itself** —
  which always passes — and was checkpointed as clean. Reproduced: 8 of 20
  parts collapsing 10 → 1 completed the day holding 128 of 200 records, with
  eight parts recorded as having reconciled cleanly. The asymmetry settles it
  unchanged: a part that *delivers* 1 of 5,000 fails the day, so a part that
  *claims* 1 cannot pass. It reuses `SHORTFALL_FAILURE_RATIO` rather than
  demanding equality, because two requests at two instants routinely differ by
  a record and a day recorded `failed` is re-fetched for the life of the
  installation; and a part collapsing to 0 still always fails, since no planned
  part promises fewer than one record. A checkpoint now requires **both**
  reconciles clean, for the reason the delivery one already did: a note dies
  with the run that produced it, so checkpointing a short part lets a later run
  skip it and report no note at all.
- **A day-level count of zero does not overrule that day's own checkpoints.**
  The same rule one level up, where partitioning had newly made it reachable: a
  run can leave a day `failed` with most of its parts stored and checkpointed,
  and if the day-level ESearch then answers a soft zero — the mechanism the
  part-level rule exists for — `fetch_pubmed` completed the day at zero records
  without consulting `completed_parts`, while `sync()` deleted the day's part
  rows in the same transaction. The write that lost the records destroyed the
  checkpoints that would have made re-fetching them cheap. Reproduced: 20
  checkpointed parts and 130,000 records became `completed` at `record_count=0`
  with no part rows, empty `errors`, empty `notes`, and the day was never
  offered again. It fails instead, and the message carries the part count and
  record total because the remedy — deleting the rows if the day really was
  withdrawn — is a judgement an operator makes from it. A quiet day with no
  stored parts is untouched, which is what keeps this from being a blanket
  refusal of every empty day. The accepted cost is that a day PubMed genuinely
  empties stays `failed` until an operator drops its rows; PubMed emptying a
  120,000-record day is not a thing it does, and the alternative is the silent
  permanent loss above.
- **An over-cap day is partitioned before the history session is checked.** The
  session opened by the day-level ESearch is unused on the partitioned path —
  `_fetch_partitioned` opens one per part — so a count without a `WebEnv` was
  costing a day that was perfectly fetchable, and a failed day is re-offered on
  every later run. The guard still stands for the under-cap path, and if the
  anomaly is not transient each part's own session guard fails the day anyway.
- **`resumable=True` is checked against the fetcher's signature at
  registration.** `sync()` reads the *descriptor* to decide whether to pass the
  three resume keywords, so a descriptor declaring more than its fetcher
  accepts raised `TypeError` inside the per-day handler — recording every day
  of the range `failed`, on every run, forever. Loud, but once per day rather
  than once per mistake, and at a place naming the day instead of the
  registration. A `**kwargs` parameter satisfies the check, since that is how
  the built-in fetchers absorb per-source configuration, and a callable with no
  introspectable signature falls through to the old behaviour rather than being
  refused on an absence of evidence.
- **`PartCheckpoint` is read as strictly as `DownloadDay`, and guarded at the
  same place.** It is the other model on the day-selection path, and #98/#99's
  rules had not been carried across. `from_dict` used `str()` and `int()`,
  which accept everything: a missing column raised `KeyError` and a null raised
  `TypeError` — neither caught by the documented `except ValueError` — a bad
  value reported `invalid literal for int()`, naming neither column nor row,
  and `str(None)` became the literal `"None"`, so a null `part_key`
  deserialised into a key matching no plan and resume degraded to re-fetching
  every unfinished day with nothing raised. `__post_init__` refuses what cannot
  describe a finished part, but imposes **no** `record_count <= promised` rule:
  `promised` counts record elements the server delivered and `record_count`
  those the fetcher parsed, so the two are not commensurable — the conflation
  `_EFetchPage` exists to prevent. And `_load_day_parts` ran *before* the
  per-day handler, inside a source loop carrying only a `finally`, so one
  malformed row escaped `sync()` and left the whole multi-source run with no
  `SyncReport` at all — reproduced with a text value in `promised`, which
  SQLite stores into `INTEGER NOT NULL` without complaint. It fails the day
  instead. Failing *safe* — proceeding with no checkpoints — was rejected:
  fetching a day from scratch would be correct, but recording it `completed` on
  a run that could not read what an earlier run stored is recording success
  over an unknown, and `completed` is never re-offered.

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
