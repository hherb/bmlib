# Changelog

All notable changes to bmlib are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); bmlib follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`ConversionResult.title`** — the document's title where page 1
  corroborates the metadata's claim to it, and `None` otherwise (#56).
  Purely additive, declared last so positional construction stays stable.
  **`metadata["title"]` is unchanged** and stays a verbatim record of what the
  PDF says, junk and all: `creator` and `producer` sit beside it unmodified,
  so sanitising one key would make the dict lie about its neighbours, and a
  caller debugging provenance needs the original. Read `result.title` for the
  judged answer.

- **`scripts/sample_pdf_metadata_titles.py`** — the instrument behind the rule
  above, and `scripts/_sampling.py`, which now shares the per-host pacer, the
  clamped `Retry-After` and `wilson()` between both live samplers rather than
  letting a rule learned from a bad run exist in two copies.

  A bioRxiv attempt records the **posting day** it came from, and an unmeasured
  one also records a `cause` and an `attempts` count. Without the day, a
  resumed run could not retry what it had lost: that walk covers
  `[today-30, today-49]` recomputed from `date.today()`, so it slides a day per
  calendar day and after 20 shares nothing with the window that produced the
  journal — an unmeasured attempt stayed open by design but became
  unreachable, permanently inflating the population's unmeasured share with no
  escape but deleting the journal and losing every good row. Days owed a retry
  are now walked before the fresh window and in addition to it, so retrying old
  work never costs the run its budget for new work. `MAX_UNMEASURED_ATTEMPTS`
  bounds the tail: a retired attempt stops being offered but keeps being
  counted, and `summarise()` names how many were retried out.

### Fixed

- **A junk PDF metadata title no longer beats the title on the page** (#56).
  `SectionSegmenter._extract_title()` returned any truthy `metadata["title"]`
  verbatim, so a typesetter's job number won over a perfectly good large-font
  first-page line. The issue proposed a reject-list of junk shapes; ground
  truth turned out to be free — every free PDF comes from a record that
  already states the article's title — so the rule was **measured** instead:
  a metadata title is believed only where page 1 prints it. Both sides
  normalise to lowercase alphanumeric tokens (line-break hyphenation closed
  up, line numbers dropped, NFKD, combining marks removed), which absorbs
  case, the terminal period, en-dash versus hyphen, ligatures, diacritics and
  a wrapped title's line break, while rejecting a string the paper never
  states. Containment is **anchored to whole tokens**: an unanchored substring
  test matches inside a word, in the accepting direction, so a `/Title`
  truncated mid-word — which producers emit routinely — was returned verbatim
  *and* beat the font-size fallback that would have recovered the whole line.
  Anchoring changes no verdict on any of the 235 measured rows.

  Measured over **235 real PDFs** (`tests/data/pdf_metadata_titles.json`;
  Europe PMC 175, bioRxiv 60), against a rule fixed before the corpus was
  collected: **0 of 126 conclusive good titles wrongly rejected** (ceiling
  1%; 95% CI [0%, 3.0%]) and **34 of 35 junk titles rejected** (floor 80%;
  95% CI [85.5%, 99.5%]). Both rules are thresholds, so both need the
  interval and not just the point estimate — and the two answer differently.
  The junk floor holds at confidence: its lower bound clears 80%. The
  wrong-rejection ceiling does **not** — 126 rows bound that rate at about
  3%, roughly triple the 1% named, so the corpus establishes ≤3% and a reader
  should not take ≤1% as measured. The one junk title
  accepted is not junk — the PDF's title reads "Drive" where the record reads
  "Drives", so the rule sided with the document in front of it. Where a junk
  title is rejected, the font-size fallback returns *some* title in 44% of
  cases (15 of 34) and nothing in the rest — but it returns one line, so what
  it recovers is the title's **first line** (38%, 13 of 34) and **never the
  complete record title** (0 of 34). A missing title is the intended trade,
  since a junk title is asserted as fact by a document the caller trusts.

  Two findings the issue could not have guessed. **Nearly 40% of Europe PMC's
  publisher-typeset PDFs carry no metadata title at all**, so the affected
  population is smaller than it looks. And **not one of the shapes the issue
  proposed** — `.docx`, `"untitled"`, the file stem — appears anywhere in the
  235; what appears is typesetter output: bare Appligent AppendPDF job
  numbers (14 of bioRxiv's 16 junk titles), Arbortext job numbers with page
  ranges (`"ma5c03166 1..10"`), QuarkXPress's `"Layout 1"`, InDesign template
  codes, an InDesign source filename, and a journal name truncated mid-word.
  A reject-list written from the issue's examples would have caught none of
  them.

  The reject-list survives only as a **backstop** for junk the document does
  print, and exactly one member earned its place under the same rule: a title
  of fewer than three words. It rejects `"Nepal Journ"` — a journal name in a
  running header, which page 1 genuinely prints, so corroboration has nothing
  to object to — and rejects no row whose metadata title matched its record;
  the shortest genuine title measured is five words. Short article titles do
  exist in the wild, so that member's false-positive risk is bounded by the
  corpus rather than disproven, and a title it rejects still falls through to
  the font heuristic.

  **Not a behaviour change for stored data**: `SectionSegmenter` has no
  consumer inside bmlib yet, and the converter's change is a new field.

### Changed

- **Europe PMC's free PDFs are now taken under their common label, not just
  their rare one** (#79). `_extract_free_pdf_url` accepted
  `availability == "Free"` only. Measured over 600 recent MEDLINE records,
  that is the rare label: of 326 `documentStyle=pdf` entries, 312 (95.7%) read
  `"Open access"` and 14 (4.3%) read `"Free"` — both the identical
  `?pdf=render` URL on the identical host. Tier 1d was silently discarding
  about 95% of the PDFs it exists to find; there is no log line for "a PDF
  entry was seen and not taken." It now allow-lists on `availabilityCode`
  (`OA`, `F`), falls back to the display string only for an entry carrying no
  code, and rejects a present-but-unknown code rather than trusting the label
  — an unknown value must under-credit, not risk a paywalled download. **This
  moves what downstream stores**: many more articles now come back with
  `pdf_url` / `file_path` / extracted text instead of a bare link, so a
  corpus's stored full text is not comparable across the change, and outbound
  traffic to Europe PMC rises, since PDFs the old code skipped are now
  downloaded.

### Fixed

- **A failed PDF download is no longer invisible** (#68).
  `_download_and_cache_pdf` swallowed a non-200 response, a failed
  magic-byte validation, and any exception, all at `DEBUG` — so with
  `convert_pdfs=True` the caller asked for text, got a bare `pdf_url`, and
  could not tell a full disk from a publisher 404. The two server-side
  causes are now reported per `(tier, cause)`, at a level chosen from a
  measured rate against a rule fixed beforehand: under 5% of attempts, a
  per-article `WARNING`; at or above it, one line per `(tier, cause)` plus
  per-article `DEBUG`. Measured with `scripts/sample_free_pdf_urls.py
  --target 150 --per-host-interval 4.0`: `europepmc` 0.7% failed (n=150, 95%
  CI [0.1%, 3.7%], 1 transport exception), `unpaywall` 64.3% failed (n=28,
  95% CI [45.8%, 79.3%], 4 HTTP 403 + 14 not-a-pdf), `biorxiv` 0.7% failed
  (n=150, 95% CI [0.1%, 3.7%], 1 transport exception). Europe PMC and
  bioRxiv had **zero** server-side failures — every one of the 18 counted
  above is Unpaywall's, and 14 of those are landing pages rather than PDFs —
  so Unpaywall's rate, whose CI lower bound is roughly 9x the threshold,
  selected the one-shot variant. The exception path (a lost network, a full
  disk) is separate and needed no measurement: it fails every article once
  it starts failing, so it is one-shot per `(tier, exception type)`
  regardless of the rate rule. `_save_pdf_to_cache` now returns
  `tuple[str | None, Literal["saved", "write-failed", "not-a-pdf"]]` so a
  failed cache *write* is reported as a write failure rather than blamed on
  the publisher's bytes. `FullTextCache.save_pdf`'s own magic-byte rejection
  drops to `DEBUG` with it: at `WARNING` it emitted a line per article for
  the dominant measured failure — Unpaywall landing pages, 14 of 28 probes —
  behind a message promising the report was one-shot, defeating the one-shot
  for the very cause the 5% rule selected it for.

  Both keys are built from a bounded `origin` — `"europepmc_pdf"`,
  `"unpaywall"` or `"known_source"`, written out at each of the three call
  sites — rather than from `result.source`. For Tiers 1d and 2 those
  coincide, but a Tier 0 `source` comes from the fetcher's
  `FullTextSourceEntry`, and OpenAlex derives it from the location's venue
  display name: one distinct, remote-data-derived string per journal or
  repository, which would turn "reported once" into one warning per article
  over a bulk sync. The source still appears in the message, so the first
  report names the specific venue. The message says the report is one-shot
  without asserting the failure is common: #79 makes `europepmc_pdf` the
  dominant emitter, and Europe PMC measured zero server-side failures.

- **A bmlib bug no longer hides behind a tier that still works** (#72).
  `_TierFailures.describe()` is consulted only on total exhaustion, so an
  `AttributeError` raised by every PMC tier — the shape a `JATSArticle` API
  change takes — with Unpaywall still healthy silently degraded a whole
  corpus from structured JATS to bare links, reporting success throughout.
  `_TierFailures` gains an `on_bug` callback fired at the moment a
  defect-shaped exception is swallowed, not at an exit: every exit-based
  alternative is the defect itself, since the next early return would
  silently re-break it. `_BUG_TYPES` deny-lists `TypeError`,
  `AttributeError`, `NameError`, `KeyError`, `IndexError` — a deny-list
  because the legitimate failures are varied (`FullTextError`,
  `httpx.HTTPError`, `OSError`, ...) while the always-a-defect set is small;
  `ValueError` and `SyntaxError` are deliberately excluded, since
  `json.JSONDecodeError` *is* a `ValueError` and
  `xml.etree.ElementTree.ParseError` *is* a `SyntaxError`, so either would
  misreport an ordinary malformed remote response as a bmlib defect.
  `WARNING`, once per `(service, exception type)` — a defect that hits one
  tier hits it for every article, so per-article would be unreadable exactly
  when it mattered, but a second, different defect still gets its own line.
  `on_bug` is a mandatory field, not an optional one: an unwired callback is
  not a quieter channel but total silence, since `describe()` is read only at
  the exit this case never reaches. `_TierFailures.unreported()` is the
  deliberate opt-out for direct helper calls and tests.

- **A malformed `fullTextUrlList` is skipped, not reported as a bmlib defect.**
  `_extract_free_pdf_url` iterated `.get("fullTextUrl", [])`, which is `None`
  rather than `[]` for a key present with a JSON null, and the resulting
  `TypeError` is a `_BUG_TYPES` member — so Europe PMC's malformed bytes were
  reported as a defect in bmlib *and* spent the one-shot `bug:TypeError` slot
  a later genuine defect needs. `_entry_is_free` guards its own two reads the
  same way; this is the container one level up.

- **A cache-write failure is reported per cause, not per site.** The key was
  the bare literal `"cache-write"` while `_warn_once`'s own documented rule is
  to name the cause. Both writers catch bare `Exception` and funnel here, so a
  transient `OSError` early in a run permanently silenced a genuine bmlib
  `TypeError` inside `save_pdf` — held at `DEBUG`, which is the failure mode
  #72 exists to fix — and, in the other order, presented a type error to the
  operator as a full disk.

- **An unquarantinable cache entry is reported** — the last swallow-to-`DEBUG`
  of a bmlib defect in `fulltext/service.py`. The consequence is permanent:
  the corrupt entry stays in the lookup path, so the per-article "could not
  read the cached full text" warning repeats every run for that article for
  ever, and an undecodable HTML entry keeps hiding a good PDF behind it. The
  operator saw the symptom on every run and never the cause.

- **A failing text extraction is no longer reported as a failed download.**
  `_attach_pdf_text` ran under `_download_and_cache_pdf`'s handler, after
  `result.file_path` was set, so an exception escaping it produced "there is
  no file and no extracted text" about an article whose file was cached and on
  the result — and a defect-shaped exception was filed as a transport fault.
  It now reports as the defect it is and keeps the cached PDF, since the
  download did succeed.

### Added

- **`scripts/sample_free_pdf_urls.py` now measures the access-label
  distribution** it was already cited as the evidence for. It read neither
  `availability` nor `availabilityCode`, so a maintainer following the
  instruction to run it before changing `_FREE_PDF_AVAILABILITY_CODES` got a
  failure-rate table and no evidence either way. It counts every
  `documentStyle=pdf` entry by `(availability, availabilityCode)` and marks
  each row taken/SKIPPED — counted **before** the allow-list filters, since a
  distribution counted after it could only ever confirm it, and #79 was
  precisely a value that never appeared in what bmlib accepted.

  Three further corrections to the instrument: a 429/503 in the Unpaywall
  *resolution* phase is now unmeasured rather than invisible (that is where
  that API's limiter bites, and a throttled resolution phase printed as a
  confident rate over whatever got through first); `Retry-After` is clamped
  at a maximum as well as at zero, since an honoured `86400` is a run that
  prints nothing, gets killed, and loses every population — the same loss the
  zero clamp was reasoned about preventing; and `ProbeOutcome.ok` becomes a
  property of `cause`, because `ok=True` beside `cause="http-403"`
  constructed happily and would silently lower the rate that sets a
  production log level. bioRxiv now honours `--target`, and `main()` exits
  non-zero when any population printed `ERROR`.

## [0.9.0] — 2026-08-10

Five fixes, every one of them in the full-text retrieval path and every one of
them the kind a bugfix release exists for: a failure that looked like a
success. A headline 0.8.0 addition — the stdlib-only `SectionSegmenter` —
turned out to be unreachable for anyone who installed core bmlib; an exhausted
retrieval chain returned a result byte-identical to a paywalled paper's; a
cache file truncated by a full disk was served as a complete article forever
after; one corrupt entry aborted a whole bulk sync; and a cache directory that
could not be created killed `FullTextService` construction outright, on a run
that would have succeeded without a cache at all.

None was found by a failing test. Three came out of reviewing the previous fix
in the chain — #70 and #71 from #67's, #75 from #74's — and #64 from
smoke-testing the published 0.8.0 wheel in a venv holding nothing else.

**Nothing stored moves.** No score, no parsed value and no cached content
changes shape, so unlike 0.6.0 through 0.8.0 — which each moved stored values,
compounding — this release needs no re-sync. The only new output is log lines,
and a retrieval that succeeds without a cache fault emits none of them. A
cache that cannot be written to or read back now warns on a run that otherwise
succeeds, which is the point of those two fixes.

**A minor bump, for a release that is only bugfixes.** Three of the fixes
change a public API — `save_html`/`save_pdf` raise where they used to write a
partial file, `sanitize_identifier()`'s output moves for a long identifier,
and `FullTextService.cache` is now nullable. "Nothing stored moves" is a
statement about *data*, not about the API, and bmlib's downstream pins are
written on the convention that a minor bump is the one that may change the
API. A patch number would have delivered all three to anyone on a `<0.9.0`
range with no decision on their part.

**Four API notes.** `save_html`/`save_pdf` now raise `OSError` where they
previously wrote a partial file — a break for a *direct* `FullTextCache`
caller only, both `FullTextService` call sites having already reported a
failed cache write. `quarantine()` is new. `sanitize_identifier()` caps its
readable prefix at 160 characters, and this one reaches every caller rather
than only a direct one, since `fetch_fulltext()` builds its cache key through
it: an entry cached under a longer identifier is orphaned and re-fetched once.
The fourth reaches anyone who dereferences the attribute:
**`FullTextService.cache` is now `FullTextCache | None`**, so
`service.cache.clear()` wants a `None` check.
Because bmlib ships `py.typed`, a downstream running mypy or pyright sees a
new error on that line even though bmlib's own ruff-only CI does not.

**One note for operators.** #70 closes the window in which a truncated cache
entry is *written*; it does not detect one already on disk, and a truncation
of English-language prose usually lands on an ASCII boundary and decodes
perfectly. A cache written by an older version is best cleared once.

### Added

- **`fulltext` extra** (`pip install bmlib[fulltext]`, httpx), included in
  `all`. The manual previously sent readers to `bmlib[publications]` — a
  publication-ingestion extra — for a PDF segmenter. `pdf` stays separate:
  bundling pymupdf would duplicate an existing extra and drag a ~20 MB binary
  wheel onto anyone who only wants JATS retrieval. `publications` and
  `transparency` keep their own httpx, so no existing install changes.

### Changed

- **`FullTextService.cache` is now `FullTextCache | None`** (#75). It is `None`
  only when the `cache` argument was omitted *and* the default could not be
  built — a caller who passes a cache always gets it back. Code that calls a
  method on the attribute (`service.cache.clear()`) needs a `None` check, and
  because bmlib ships `py.typed`, a downstream running mypy or pyright will
  see a new error on that line even though bmlib's own CI, which runs ruff
  only, does not. Flagged separately from the fix below because the break is
  latent: it never fires on a developer machine or in CI, only in the broken
  environment where the operator already has a problem, and there it turns a
  `FileExistsError` naming the cache directory into an `AttributeError` far
  from its cause.

### Fixed

- **A total full-text retrieval failure no longer reads as "no free full
  text"** (#67). `fetch_fulltext()` wraps each of the swallowers on its path
  in `except Exception` that logs at `DEBUG` and moves on — right in itself,
  since an unreachable Unpaywall must not cost the DOI fallback — but the only
  `WARNING` on the path sat inside the `if abstract_only is not None:` branch,
  so the *more* complete the failure, the quieter it got. A caller who had
  lost the network, hit a bmlib bug or misconfigured the service received
  `source="doi"`, `html=None`, `content_kind="none"` for every paper in a
  corpus — byte-identical to the legitimate outcome for a paywalled paper —
  with nothing above `DEBUG` to say so. Attempts on the tier chain are now
  accounted for — the download half of the PDF tier is deliberately not, since
  every one of its call sites returns immediately after it and it could never
  feed the report (#68) — and the warning moved out to cover every
  empty-handed exit, sorting what happened into the two buckets that read
  differently: `3 attempts failed (ConnectError)` is a broken network,
  `3 sources had nothing` is an ordinary paywalled paper, and
  `TypeError`/`AttributeError` among the failures is a bug. `FullTextResult`
  is unchanged, and a successful retrieval emits no exhaustion warning.

- **An unreachable source no longer counts as an absence** (#67). Two things
  made a broken chain look like a paywalled one even with the report above.
  Both resolvers signalled an HTTP failure by *returning* what an empty result
  set returns, so a Europe PMC or NCBI outage was counted as nothing having
  happened. Both now raise, but only one raises to its caller: the search
  resolver's `FullTextError` reaches the callers that already caught it, and
  they record the fault, while the ID converter's is caught by its own handler,
  which records the fault and still returns `None` — a caller that already
  holds a free-PDF URL by that point must not be made to pay for the converter
  being down. And `FullTextError` was raised alike for `Unpaywall HTTP 503`
  and `DOI not found in Unpaywall`, so an outage and a paper nobody serves for
  free produced byte-identical summaries. The absences now raise
  `FullTextUnavailableError`, a subclass, so nothing that catches
  `FullTextError` is affected; it is an internal signal and never escapes
  `fetch_fulltext()`. An article 404 is an absence from
  *every* source — Europe PMC, NCBI, Unpaywall and a fetcher-supplied URL
  alike, where all four used to raise the same `FullTextError` a 5xx did —
  since a stored source URL going stale is ordinary, and counting it as broken
  inflates the one bucket the summary asks the operator to act on. A 404 from
  a *search* endpoint stays a fault: Europe PMC answers "no such paper" with
  HTTP 200 and an empty list, so a 404 there means the API path is wrong.

- **A call whose identifiers all failed is no longer told it gave none**
  (#67). With a `pmc_id` or `fulltext_sources` but no `doi`/`pmid`, an
  exhausted chain raised `FullTextError("No identifiers provided")` and
  skipped the summary entirely — the same misdirection as the bug above, on
  the one path with no result to return. It now reports the failures and says
  what was actually missing.

- **A cache that cannot be written to says so, once** (#67, same file).
  `_cache_html` swallowed every exception at `DEBUG`, so a read-only cache
  directory or a full disk meant the whole corpus was silently re-fetched over
  the network on every run, permanently. The first failed write now emits a
  `WARNING` naming what was raised; later ones stay at `DEBUG`, since the
  cause is a property of the directory rather than of the article — the
  one-shot pattern the missing-`bmlib[pdf]` warning already used. HTML and PDF
  writes share that one warning: the PDF write was folded into the download's
  own handler, so it was reported as "PDF download failed" and never reached
  the warning at all — leaving a corpus served mostly by Unpaywall, which
  never writes HTML, completely silent.

- **A truncated cache file is no longer written** (#70, found reviewing #67's
  fix). `save_html` and `save_pdf` wrote with a bare
  `write_text`/`write_bytes` and `get_html` read back with no validation, so a
  disk that filled mid-write — one of the two causes the warning above names —
  left a truncated file that decodes perfectly and was then returned as
  `content_kind="fulltext"` from `source="cached"` on every later run, with no
  log at any level: `quality/` would score a paper whose Methods and Results
  do not exist. Strictly worse than #67, which lost data in a shape resembling
  absence. Both writes now go to a uniquely-named temporary file beside the
  target and are published with `os.replace`, so a failed write leaves the
  previous entry or nothing. The headline says *written* deliberately: this
  closes the window rather than detecting an entry already truncated on disk,
  and a real truncation of English-language prose usually lands on an ASCII
  boundary and decodes fine, so `clear()` is the remedy for a cache an older
  version wrote. Several details are load-bearing, and each has a named test
  except `O_BINARY`, which no run off Windows can observe:
  the `fsync` before the replace is not durability theatre — under delayed
  allocation the `write(2)` that `flush()` issues returns success and ENOSPC
  reaches userspace only at `fsync`, so without it `os.replace` would publish
  a file whose blocks were never written; the temporary name carries a UUID,
  because the loser of a race between two processes would otherwise unlink the
  winner's in-flight temp file and leave neither having cached anything;
  `O_BINARY` is added where the platform has it, since a descriptor `os.open`
  leaves in the CRT's default text mode would rewrite every LF in a cached PDF
  as CRLF on Windows; the mode is 0666 filtered by the umask — exactly what
  `write_bytes` requests, and neither `tempfile.mkstemp`'s 0600 nor 0644,
  both of which silently break a cache directory shared between users; and the
  cleanup's own `unlink` is guarded so it cannot replace the exception it is
  tidying up after. `sanitize_identifier` now truncates its readable prefix,
  since the temporary name is 38 characters longer than the entry's and a long
  identifier would otherwise fail a write that used to succeed. `save_html`
  and `save_pdf` now raise `OSError` where they previously wrote a partial
  file — both call sites in `FullTextService` already report a failed cache
  write, so a retrieval is unaffected, and both docstrings carry a `Raises:`
  section for direct callers.

- **A corrupt cache entry no longer aborts the run** (#71, same review).
  `_check_cache` was called unguarded and `get_html` does a bare `read_text`,
  so an entry truncated mid-multibyte-sequence raised `UnicodeDecodeError`
  straight out of `fetch_fulltext()`: it broke the documented
  `FullTextError`-only contract, contradicted #67's own new bullet that a bad
  cache does not fail a retrieval, and was a hard stop where re-fetching over
  the network was available — one bad file made a paper permanently
  unfetchable and took a bulk sync down mid-corpus. A cache *read* is now
  best-effort exactly as a cache write is. The guard is deliberately broad: a
  decode failure is only the shape it was reported in, and a file the process
  cannot read raises `OSError` instead, so narrowing it to `UnicodeDecodeError`
  restores the bug — pinned by its own test after mutation testing found the
  first cut survived that narrowing. It reports the exception type as well as
  its message, so a bmlib bug does not read as an ordinary bad file. Warned
  per article rather than once per service, unlike the write warning above: an
  unwritable directory is a property of the directory, an unreadable file is a
  property of that file. The unreadable entry is not deleted but **moved aside**
  to a `.corrupt` name by the new `FullTextCache.quarantine()`: leaving it in
  place healed only when the re-fetch happened to return JATS full text, since
  an article served as a PDF never rewrites the HTML entry and the undecodable
  entry is read first — so it hid the freshly cached PDF behind it and the
  article warned and re-downloaded on every run, forever. `delete()` and
  `clear()` now also remove an entry that is not a regular file, which is the
  corrupt shape an operator is most likely to meet and the one both of them
  previously failed on.

- **A cache directory that cannot be created no longer aborts construction**
  (#75, found reviewing PR #74). `FullTextCache.__init__`'s three `mkdir`
  calls were unguarded and ran inside `FullTextService.__init__` whenever no
  cache was passed, so a file standing where the cache directory should be —
  or a read-only parent, or a full disk — took down a run that had every
  chance of succeeding without a cache. It was the last place *`FullTextService`
  touches the cache* that was not best-effort: a failed write already warned
  once (#67) and a failed read already fell through to the network (#71). The
  default construction now warns once, naming what was raised, and leaves
  `service.cache` as `None`; retrieval proceeds and caches nothing. A
  `FullTextCache` constructed *directly* still raises — that caller asked for
  a cache specifically, and degrading would return an object whose every
  method then failed one at a time instead of failing once at construction.
  The scoping in that first sentence is meant literally: `FullTextCache`'s own
  methods are unchanged and still raise to a direct caller, so "the cache is
  best-effort" is true of the service, not of the class.
  The warning says what the degraded run costs rather than only that it is
  degraded — a PDF is fetched *into* the cache, so with no cache there is no
  download at all and a PDF-only article comes back as a bare URL. That is
  lost content, not merely repeated traffic, and an operator told only that
  "nothing will be cached" would go looking for a network fault. It names
  `cache=FullTextCache(cache_dir=...)` as the remedy, as the missing-`bmlib[pdf]`
  warning already names its extra.
  The guard catches `RuntimeError` as well as `OSError`, because
  `_default_cache_dir()` runs before any `mkdir` and calls `Path.home()`,
  which raises the former where there is no `HOME` and no passwd entry — an
  ordinary distroless container — so `except OSError` would have fixed the
  reported shape and left the same defect one layer up. It is not
  `except Exception`: inside that one constructor `RuntimeError` has exactly
  one *source*, so the guard stays narrow enough that a bmlib bug still
  surfaces as one — pinned by a test that raises a `ValueError` from the
  constructor and demands it escape, since widening a guard catches strictly
  more and no test that merely uses the cache could fail on it.
  `FullTextService.__init__`'s `Raises:` section, which documented only
  `ImportError`, becomes accurate rather than needing a new entry. One log
  line changes: `_download_and_cache_pdf`'s `self.cache` check was dead code —
  `FullTextCache` is always truthy and `self.cache` could not be `None` — and
  reaching it now would have printed "no identifier was given" when an
  identifier had been given, so the two conditions are split. The no-cache one
  logs at `DEBUG`, the construction warning having already named that exact
  consequence, and unlike its sibling it is *not* gated on `convert_pdfs`: the
  download is skipped either way, so `file_path` is lost even for the caller
  who turned extraction off precisely because they wanted the file.

- **`bmlib.fulltext` imports on a core install** (#64). `fulltext/__init__.py`
  eagerly re-exported `service`, whose top-level `import httpx` was the last
  unguarded optional import in bmlib — and since importing a submodule imports
  its parent package first, it gated everything beside it. Measured against the
  published 0.8.0 wheel in a venv holding only `bmlib`, `jinja2` and
  `markupsafe`, **one fresh interpreter per module**: **ten** modules across two
  packages raised a bare `ModuleNotFoundError`. All seven of `bmlib.fulltext.*`,
  including the pure-dataclass `models` and the stdlib-only `SectionSegmenter`
  — one of 0.8.0's headline additions, documented as standalone and making no
  HTTP request — plus `publications.fetchers.{pubmed,biorxiv,openalex}`, which
  borrow one dataclass from `models` and take an injected HTTP client rather
  than importing httpx themselves. `FullTextService` and `FullTextError` now
  resolve through a PEP 562 `__getattr__`, as `bmlib.context_processor` already
  does for its LLM-backed half; the public API and `__all__` are unchanged, and
  the same probe now reports **69 importable, 0 not**. What deferring adds over
  the guarded import below — which restores importability by itself, as
  mutation testing showed — is that `import bmlib.fulltext` does not load
  `service` at all, so no future top-level import in that module can gate the
  parser, the models or the segmenter again.

- **Constructing `FullTextService` without httpx names the extra.** The import
  moved out of the module top level into `_require_httpx()`, called first thing
  in `__init__` so the failure lands at construction rather than on the first
  request, and again in `_http_get` where the client is actually built. The
  module is deliberately **not** stored on the instance: a module object cannot
  be pickled, so holding one would have broken handing a configured service to
  a process pool, and reading it back as instance state would let anything that
  reached `_http_get` without running `__init__` fail with an `AttributeError`
  that the tier chain swallows at DEBUG. The check is the first statement, so a
  failed construction leaves no cache directory behind.

- **A broken httpx is no longer reported as an absent one.** `except
  ImportError` also catches the `ModuleNotFoundError` a *present* httpx raises
  for its own missing dependency, so the message now reports what was actually
  raised — `httpx is required for full-text retrieval, but importing it failed
  (…). Install with: pip install bmlib[fulltext]`. Asserting the cause instead
  prescribed a `pip install` that answers "Requirement already satisfied" and
  changes nothing, leaving the reader to run it, see success, retry and hit the
  identical error. This is the reasoning `_attach_pdf_text` already spells out
  for the analogous PyMuPDF case.

- **`dir()` on `bmlib.fulltext` and `bmlib.context_processor` no longer hides
  the submodules.** Both `__dir__` implementations returned `__all__` alone,
  which added the two deferred names while dropping `cache`, `models`,
  `segmenter` and every dunder — breaking REPL completion for
  `bmlib.fulltext.models` and shrinking `inspect.getmembers()`. They now return
  the union. Resolved lazy names are also bound into `globals()`, as PEP 562
  recommends, so repeat access skips `__getattr__` entirely.

## [0.8.0] — 2026-08-08

Phase 2 of the bmlibrarian port, complete — four ports in one release. A new
pure-stdlib `bmlib.citations` numbers and formats reference lists in four
styles; `SectionSegmenter` turns a PDF's text lines into typed sections;
`CochraneAssessor` becomes the quality pipeline's Tier 4, condensing an
oversized paper to an evidence digest rather than truncating it; and the
PubMed fetcher grafts on `<GrantList>` and `<AffiliationInfo>` as child rows
while ending the silent truncation of every title that carried markup.

Everything is additive, so a minor bump. But **three of the four move stored
values**, and they compound: the PubMed change alters every synced title and
abstract, a Cochrane-enriched assessment reports different bias domains, and
a PDF that previously "converted" to empty text is now a failure. Anything
persisting these should re-sync or accept a mix; each entry below says what
moved.

### Added

- **`bmlib.citations`** — citation-marker parsing, four citation styles, and
  reference-list building, ported from bmlibrarian's `writing` package
  (Phase 2 row 4 of the porting analysis). `parse_citations()` and friends
  read the `[@id:12345:Smith2023]` marker format as pure functions;
  `CitationFormatter` renders references and inline citations in Vancouver,
  APA, Harvard, or Chicago style; `build_references()` /
  `format_document()` number citations by order of first appearance,
  combine adjacent markers (`[1-3]`), and append a markdown reference list,
  with document metadata injected by the caller as
  `Mapping[int, DocumentMetadata]` instead of fetched from a database. Five
  upstream defects fixed, each with a named regression test: a
  semicolon-separated author string of inverted names was shattered into
  fragments (`"Smith, John; Doe, Jane"` became four authors); marker
  validation anchored only the start, so trailing junk validated;
  author–date styles (APA/Harvard/Chicago) received numeric `[N]` inline
  citations against an unnumbered reference list; APA/Chicago author
  blocks doubled the terminal period (`"Williams, B.."`); and a
  whitespace-only author entry crashed every style's reference formatting
  with an `IndexError` (blank entries are now dropped). The app-editor
  pieces (`document_store`, `WritingDocument`, autosave/editor constants)
  were deliberately not ported.
- **PDF section segmenter** (`bmlib.fulltext.SectionSegmenter`) — Phase 2
  row 8 of the bmlibrarian port. `segment_document()` turns a PDF's text
  lines into a `SegmentedDocument` of typed, titled `Section`s, located by
  heading detection (font size against the document's median, bold as the
  rescue for body-sized headings) and an anchored pattern table covering
  every producible `SectionType`. Three content-losing upstream defects are
  fixed, each with a named regression test: everything before the first
  detected heading was silently dropped (now a `FRONT_MATTER` section at
  0.5 confidence); a heading with no body vanished along with its heading
  text (now reported with empty content); and the partial-match fallback
  compared regex *source* against the heading as literal text, which killed
  every multi-word pattern and classified a heading "A" as ABSTRACT (now an
  unanchored, word-bounded search of the same compiled pattern, at 0.7).
  Enum members no pattern could produce were not ported from upstream
  (`MATERIALS_AND_METHODS`, `CONCLUSIONS` — duplicates of the members that
  own their patterns), or were given patterns instead (`APPENDIX`); `TITLE`
  stays, reserved for callers. "Financial disclosure(s)" classifies as
  `CONFLICTS` in both numbers — the singular once sat in `FUNDING`'s list
  too, so the two numbers landed in different sections, decided by dict
  order. `TextBlock`, `Section` and `SegmentedDocument` carry
  `to_dict()`/`from_dict()` for JSON-safe persistence of a segmentation.
- **`PyMuPDFConverter.extract_blocks()`** and the `LayoutExtractor`
  protocol (`bmlib.fulltext`) — one `TextBlock` per text *line*, not per
  span. PyMuPDF starts a new span at every font change, so upstream's
  span-level extraction shattered a mixed-font heading ("2." + "Materials
  and Methods") into fragments no anchored pattern could match, and split
  sentences at every italic word. Font attributes come from the line's
  dominant span, so a superscript marker cannot restyle a line. Declared as
  a protocol rather than on the `PDFConverter` ABC so a backend that cannot
  report line geometry is not forced to fake it. Raises on a corrupt file
  rather than returning a partial list — unlike `convert()`, whose partial
  text is useful, a partial block list is indistinguishable from a sparse
  PDF.
- **Cochrane assessment agent** (`bmlib.quality.CochraneAssessor`) — Phase 2
  row 9 of the bmlibrarian port, and the producer `cochrane_models.py` has
  been waiting for since 0.4.0. `assess()` turns a title and text into a
  `CochraneStudyAssessment`: the Cochrane Handbook's five-section
  study-characteristics table plus a judgement and supporting text for each of
  the nine Risk-of-Bias domains. Text larger than the configured context is
  first reduced to an evidence digest by `bmlib.context_processor`, so the
  nine-domain judgement is always made once, over content that fits —
  enforced by measuring the digest itself rather than trusting
  `ProcessingStatus` to imply it, since a `TRUNCATED` run names the harness's
  recursion ceiling, not the size of what it produced. `condensed_from_chars`
  says when condensation happened and `condensation_status` says how it
  finished (`"completed"`, `"partial"`, `"truncated"`), because a judgement
  made over a digest — especially an incomplete one — is weaker evidence than
  one made over the paper. Truncating instead was rejected: allocation
  concealment and blinding live in Methods and attrition in Results, so a
  head-of-string cut drops exactly the evidence the domains rest on. Failure
  returns `None`, not an all-"Unclear risk" stand-in that would be
  indistinguishable from a real assessment.
- **`collapse_risk_of_bias()`** — the nine Cochrane domains reduced to the
  five `BiasRisk` domains, closing the `BiasRisk` ↔ `CochraneRiskOfBias` gap.
  The grouping is derived from each item's own `bias_type` rather than written
  out per domain; where several collapse onto one field the worst wins, with
  `unclear` outranking `low` because an unreported domain is not a clean bill
  of health. An unrecognised `bias_type` raises rather than returning a
  `BiasRisk` that looks complete.
- **`QualityFilter(use_cochrane_assessment=True)`** and a `full_text=` keyword
  on `QualityManager.assess()`. The Cochrane pass *enriches* a classification
  rather than replacing it — the classification supplies the study design,
  quality tier/score and confidence a Cochrane assessment does not produce,
  the Cochrane pass supplies the bias detail no classification tier can see —
  and attaches the full assessment to the new
  `QualityAssessment.cochrane_assessment`. Which classification depends on
  Tier 1: a confident metadata result is the base and Tier 2 is skipped, but
  an inconclusive one is not, because enriching it would return
  `study_design=UNKNOWN` at score 0.0 and confidence 0.0 with a full
  nine-domain bias table attached — worse than the Tier 2 answer the caller
  had enabled. So when Tier 1 is inconclusive and `use_llm_classification` is
  set (the default), the cheap classifier runs first and its result is the
  base. That is the common path for preprints, which carry no PubMed
  publication types at all. Neither `evidence_level` nor `confidence` is
  copied across: both are foreign vocabularies (Cochrane's `evidence_level`
  is free-form model text against the classification's Oxford CEBM, and
  `overall_confidence` describes the model's certainty about blinding and
  allocation concealment, not about the `study_design` the classification
  already supplied); both stay reachable on the attached object. A successful
  pass supersedes Tier 3 when both are requested; a *failed* pass falls
  through to Tier 3 and then Tier 2 exactly as if the flag had not been set,
  rather than returning the Tier 1 result outright — "supersedes" means "runs
  instead of, when it works", not "suppresses even on failure". With neither
  Tier 3 nor Tier 2 requested a failed pass still ends at the Tier 1 result,
  unchanged. Additive: `assessment_tier=4` is new, the flag is off by
  default, and no stored value moves.

  Six upstream defects were fixed in the port, each with a named regression
  test: `min_confidence` was accepted and never read; `success_rate` could
  only ever report 1.0, because the attempt total was incremented on the
  success path alone; judgement strings bypassed
  `RiskOfBiasJudgement.from_string()`, so a model answering `"low"` rather
  than `"Low risk"` stored an invalid value that `get_summary_counts()` then
  skipped, silently reporting eight domains of nine; `overall_confidence` was
  unclamped, so a model reporting 1.4 outranked every honest result; a reply
  carrying no `risk_of_bias` section at all was accepted and turned into nine
  fabricated defaults; and the study label was derived by
  `first_author.split()[-1]`, which reads "van der Berg" as "Berg".
- **PubMed grants and author affiliations** (`bmlib.publications.Grant`,
  `AuthorAffiliation`) — Phase 2 row 11 of the bmlibrarian port, and the last
  Phase 2 row. The PubMed fetcher now reads `<GrantList>` awards and
  `<AffiliationInfo>` affiliations, and both are persisted: two new tables,
  `publication_grants` and `publication_affiliations`, created by
  `ensure_schema()` on both backends, read back by the new `get_grants()` and
  `get_author_affiliations()`. They are child rows of a publication, following
  the `FullTextSource` precedent, so `Publication` and its `to_dict()`
  contract are unchanged; the new `FetchedRecord.grants` and
  `FetchedRecord.author_affiliations` are declared last, for positional
  stability. Affiliations are stored one row per *(author, affiliation)* pair
  rather than upstream's nested grouping — the relational shape, which makes
  "which papers have an author at this institution?" a join rather than a scan
  through nested JSON (only `publication_id` is indexed; an index suiting a
  search *by* institution is the consumer's to add) — and
  carry the author's `position` in the `<AuthorList>`, because first-author
  and senior-author affiliation are the conflict-of-interest signals and the
  name alone cannot recover the ordering.

  Both tables carry a `source` column, and storage is **replace-per-source**:
  a record's rows replace the stored rows for the source that asserted them
  and leave every other source's alone, so re-syncing PubMed cannot disturb
  what OpenAlex found. That gives idempotent re-syncs and self-correcting
  updates while letting two sources coexist — scoping by publication alone
  made the stored set depend on whichever source synced last, flip-flopping on
  every sync with no error and no warning, which matters because OpenAlex's
  API does carry funder data. `sync()` stamps the column from the record's own
  source rather than each fetcher setting it, so a new fetcher cannot forget.
  A record carrying no rows at all still leaves everything alone: an absent
  `<GrantList>` means the record did not carry the data, not that the funding
  was withdrawn. A row naming *no* source raises `ValueError` rather than
  being stored, because scoping is the whole mechanism: an unnamed row is
  unreachable, so no later sync can replace it and each one stacks a
  correctly-labelled duplicate beside it. The check is in the storage layer
  rather than left to the `NOT NULL` column because the column rejects `None`
  while `""` — the dataclass default, and so the value a forgetful caller
  actually produces — was stored happily.

  There is no UNIQUE constraint on the natural key, deliberately — every
  column of a grant proper is nullable and both backends treat NULL as
  *distinct* in a unique index, so it would protect nothing while appearing
  to. Nothing is left for one to catch: exact repeats are collapsed at parse
  time, since PubMed emits a `<Grant>` block verbatim twice often enough to
  matter (31 of 575 entries across 200 NIH-funded records, affecting 14 of
  them), and stored separately they inflate every count of a paper's funders.

  Two upstream defects fixed, each with a named regression test: a grant
  naming neither an agency nor an award id was stored as a row identifying no
  award, and affiliations named their author `"Smith John"` while the author
  list said `"Smith, John A"`, so joining the two was guesswork — one pass
  over `<AuthorList>` now formats both. Which elements are read with the
  formatting walker below is decided by NLM's DTD rather than by eye:
  `<Affiliation>` is declared with the same `(%text;)*` content model as
  `<ArticleTitle>`, so it gets the walker too — a trailing superscript
  footnote marker would otherwise truncate the institution, and a *leading*
  one would drop the affiliation row entirely. Upstream's `is_retracted` was
  deliberately not ported: `publication_types` already carries "Retracted
  Publication" verbatim, `bmlib.publications.retractions` answers the question
  authoritatively, and upstream treats RefType `RetractionOf` as retracted
  when it marks an article as *being* the retraction notice.

### Changed

- **PubMed titles and abstracts preserve inline markup, and abstracts are
  Markdown.** `_text()` read `el.text`, which is the text *before the first
  child element*, so any PubMed title carrying markup was truncated there and
  the loss was silent: `"Effects of H<sub>2</sub>O and <i>E. coli</i> on
  outcomes"` parsed as `"Effects of H"`. Titles drive dedup display, quality
  assessment and citation building, and chemical formulas and italicised
  species names are ordinary in PubMed titles. Titles and abstracts are now
  read with a mixed-content walker that maps `<b>`/`<i>`/`<sup>`/`<sub>`
  to Markdown, and each `AbstractText` becomes a `**LABEL:** text` section
  separated by a blank line, with the label taken from `Label` *or*
  `NlmCategory` — reading only `Label` dropped the heading from every section
  labelled the other way, running it into its neighbour.

  Prose taken from the document is escaped (`` \ ` * ~ ^ ``), so a field
  *declared* Markdown cannot be re-read as markup it never carried. Without
  this the change would corrupt values that were fine before: `CYP2C19 (*1,
  *2, *3, *17 alleles)`, the standard star-allele notation, renders as
  `(<em>1, </em>2, …)`, and the `~` of "AUC ~ 0.80" pairs with the next one to
  subscript half a sentence — a hazard the `~x~` mapping itself created. The
  escape set is measured against 3,403 real titles and abstract sections: it
  alters 0.35% of them and removes every construct a CommonMark parser found,
  while also escaping `_` and `[`/`]` churned 4.3% and fixed nothing further
  (intraword `_` is inert in CommonMark, and a bare `[…]` is not a link).

  `<u>`/`<underline>` is **not** mapped, and passes through undecorated.
  Markdown has no underline — `__x__` is *strong* emphasis, so mapping `<u>`
  to it renders underlined text identically to `<b>` while asserting the
  source said "bold", which is exactly the ambiguity `<sub>`/`<sup>` earned
  their Pandoc markers to avoid. Underline is presentational, unlike a
  subscript, so dropping it loses nothing a reader needs.

  A second upstream defect fixed on the way: upstream stripped whitespace at
  every recursion level, so the space inside `<b>Randomised </b><b>trial</b>`
  vanished and the runs welded into `**Randomised****trial**`, which is broken
  Markdown rather than merely ugly text. Leaving the space where it sits is no
  better — CommonMark requires an emphasis delimiter to be adjacent to
  non-whitespace — so a run's edge whitespace is re-emitted *outside* its
  markers, giving `**Randomised** **trial**`.

  **Not comparable with previously stored values.** Every synced PubMed title
  and abstract changes shape: titles because they were being truncated,
  abstracts because they gain recovered `NlmCategory` labels, blank-line
  section breaks, and `CO~2~` / `m^2^` where the old flattening produced an
  ambiguous `CO2` / `m2`. Anything persisting abstracts should re-sync or
  accept the mix.

### Fixed

- **A password-protected PDF is a failed conversion, not an empty successful
  one** (#57). `PyMuPDFConverter.convert()` returned `success=True` with
  `text=""`, `converted_pages=0` and only warnings to show for it: PyMuPDF
  opens an encrypted document without its password and fails only on *use*,
  so metadata extraction and every page's `get_text()` failed inside the
  handlers that exist to stop one bad page aborting the rest. A caller
  testing `success` alone therefore read an unreadable file as a paper that
  happens to contain no text — and the two need different responses, since
  one is worth retrying from another source and the other is not.
  `convert()` now checks `doc.needs_pass` immediately after opening and
  returns `success=False` with `error_message="PDF is password-protected"`.
  `extract_blocks()` gets the same explicit check: it already raised on such
  a file, but only because `get_text()` failed of its own accord, under
  PyMuPDF's message naming two causes at once ("document closed or
  encrypted") — and had that call ever stopped raising it would have
  returned `[]`, which is precisely what a legitimate image-only scan
  returns. The test is `needs_pass`, not `is_encrypted`: an *owner* password
  restricts permissions without blocking reads, so such a file is encrypted
  and converts perfectly. Four regression tests, each guard paired with that
  owner-password negative control.

## [0.7.0] — 2026-08-04

Two new capabilities and two widened ones. `bmlib.publications` can answer
"is this paper retracted?"; the new `bmlib.context_processor` works through
more content than one context window holds; `bmlib.fulltext` reaches PMC
through a second resolver and reads NCBI's own copy; and the transparency
analyzer credits data deposition that PubMed reports in a structured field
rather than only what a paper's prose happens to say.

No public signature changed incompatibly and nothing was removed, so the bump
is minor. **Four changes move stored values**, none of them behind a flag:

- `transparency_score` rises and `data_availability_level` strengthens — the
  two sources are merged by rank, so it can only move up — for papers whose
  PubMed record names a deposition repository.
- `trial_registered` becomes `True` and `transparency_score` rises by 20 for
  papers registered in `JMACCT`, `REPEC` or `UMIN CTR`, which
  `_TRIAL_REGISTRY_NAMES` did not recognise.
- `risk_indicators` collapses a funder CrossRef names repeatedly to a single
  line; no score and no risk level moves with it.
- `FullTextResult.source` gains `"ncbi_pmc"`, and papers that previously fell
  through to a bare DOI link can now return real full text.

Each entry below says exactly who is affected. Retraction Watch and
`context_processor` are purely additive — a new module each, nothing existing
changed.

### Added

- **Retraction Watch notices: answer "is this paper retracted?"** Ported from
  bmlibrarian (Phase 2 row 10 of the porting analysis). A biomedical
  literature tool must not present a retracted paper as evidence, and bmlib
  had no way to tell. `parse_retraction_watch_csv()` streams the
  Crossref-distributed export (65 MB, 71,306 rows) into `RetractionNotice`
  records; `store_retraction_notices()` upserts them on Retraction Watch's own
  `record_id`, so re-importing the monthly file updates rather than
  duplicates; `lookup_retractions()` returns every notice about one paper,
  newest first, and the pure `is_retracted()` reduces them to a boolean.

  Purely additive — a new table and a new module, nothing existing changed, so
  no stored value moves.

  This is deliberately **not** a registered source fetcher. Fetchers are a
  date-keyed feed protocol producing publications; a retraction notice
  annotates a paper that is usually not in the caller's `publications` table
  at all.

  A row describes **two** papers, so both identifier pairs are kept under
  names that say which is which: `doi`/`pmid` are always the retracted paper,
  `notice_doi`/`notice_pmid` the notice.

  Five defects in the upstream implementation are fixed, each pinned by a
  regression test named for it:

  1. **The PMID match path was dead.** Its candidate column tuple contained
     none of the export's real names (`OriginalPaperPubMedID`,
     `RetractionPubMedID`), so every row matched `None`.
  2. **A failed encoding attempt duplicated every row already read.** The row
     accumulator was created outside the encoding retry loop and never
     cleared, so `utf-8` failing part-way through left those rows in place and
     the next encoding appended the whole file again. The port scans the
     whole file through an incremental decoder before committing to an
     encoding, then streams it once with that choice — so a decode failure
     is caught before the first row is ever yielded, and a partially-read
     accumulator can no longer exist to be duplicated.
  3. **A byte-order mark hid the first column.** `utf-8` was tried before
     `utf-8-sig`; on a BOM'd file it succeeds and glues the BOM to the first
     field name, so `Record ID` became unfindable.
  4. **Every row was stored as retracted** — including Corrections,
     Expressions of Concern, and Reinstatements, which are the opposite.
  5. **Missing identifiers are truthy sentinels.** The export writes `0` for
     an absent PubMed ID (46.04% of rows) and `Unavailable`/`unavailable` for
     an absent DOI, none of them falsy, so a truthiness test accepts them and
     collapses tens of thousands of unrelated notices onto a single fake key.

  The retraction rule is deliberately not "latest notice wins": scanning
  newest-first, only a Retraction or a Reinstatement decides, because a
  correction does not undo a retraction. 52 papers in the live export are
  retracted while carrying a later Correction or Expression of Concern.

  Every way this feature can degrade rather than fail is reported, because
  each one degrades into an import that looks successful:

  - `lookup_retractions()` rejects the same sentinels the parser does, so
    `pmid="0"` or `doi="Unavailable"` raises rather than returning `[]` — a
    caller whose own PMID column stores `"0"` for "absent" would otherwise
    read a paper it knows nothing about as not retracted.
  - Falling back off `utf-8-sig` to `cp1252` or `latin-1` logs at `WARNING`.
    Neither fallback can fail, so one corrupt byte would otherwise re-read
    the whole export under an encoding that mis-renders every non-ASCII
    character in 66,000 rows, in silence.
  - A `RetractionNature` value this version cannot map logs at `WARNING`,
    once per distinct value. `is_retracted()` reads `OTHER` as evidence of
    nothing, so a reworded `"Retraction"` upstream would answer "not
    retracted" for every paper in the file.
  - A malformed CSV raises `ValueError` naming the last line read whole,
    rather than a bare `csv.Error` reading as a bmlib bug.
  - A stream that is text rather than binary, or not seekable, raises at the
    call rather than at the first iteration — which for the documented usage
    means at the caller's mistake rather than from inside
    `store_retraction_notices()`'s open transaction.

- **`context_processor`: process more content than one context window holds.**
  Ported from bmlibrarian (Phase 1 item 2, issue #49). Hierarchical
  map-reduce: batch the items to fit, extract from each batch, then feed the
  extractions back in as items and repeat until what remains fits in a single
  context. The alternative — truncating — loses information silently and
  leaves no way to tell an answer drawn from everything apart from one drawn
  from the first 4,000 characters.

  `IterativeContextProcessor` is the harness and has **no LLM dependency**: it
  is batching, recursion, consolidation, progress and failure accounting over
  caller-supplied items. Subclasses supply `format_item()` and
  `extract_from_batch()`. `LLMChunkProcessor` is a ready-made subclass that
  runs every model call through a `BaseAgent`, so token accounting, retries
  and JSON repair are the ones the rest of bmlib uses; it accepts plain
  strings or `(text, score)` tuples, the shape a semantic search returns.
  Upstream's equivalent called the raw Ollama client directly and was
  rewritten rather than copied. `create_prisma_chunk_processor` was
  deliberately not ported: PRISMA 2020 is an application concept.

  `bmlib.llm.text_utils.process_with_map_reduce()` is the shallow case of this
  — one map, one reduce, over one string — and stays. The processor uses
  `TextChunker` from that module when it has to split an oversized item, so
  pieces break on paragraph and sentence boundaries instead of mid-word.

  Four defects in the upstream implementation are fixed in the port, each
  pinned by a regression test named for it:

  1. **The bin-packing ran twice per level.** `process()` re-ran the whole
     packing purely to record the batch count in its statistics —
     re-formatting every item, re-splitting every oversized one, and
     re-emitting every skip and split log line, so the logs claimed twice the
     skips that happened. `_process_level()` now returns the count it has.
  2. **Split pieces were measured before formatting.** Pieces were cut to
     `max_chars` of *raw* content but measured after `format_item()` added its
     decoration, so a piece cut to exactly the limit exceeded it — breaking
     the one guarantee `max_context_chars` makes. The overflow is now measured
     and the budget reduced by exactly that much, then verified.
  3. **`OversizedItemStrategy.TRUNCATE` double-decorated.** It truncated the
     *formatted* item and returned it as an ordinary item, which the batcher
     then decorated again — over the limit once more, by the width of the
     second decoration.
  4. **Boundary items were measured at the wrong index.** The item that
     *starts* a new batch was measured with the outgoing batch's index, so
     `total_chars` under-counted wherever `format_item()` renders the index.
     Items are now measured at the position they land in, and `total_chars`
     equals the length of the content the extractor receives.

  Two upstream shapes were changed rather than carried over.
  `estimate_item_size()` is not ported — the batcher must call `format_item()`
  on every item anyway, so the estimate saved nothing while letting the
  oversized decision and the packing measurement disagree, which is how an
  underestimated item was never split and silently overflowed its batch. And
  the recursion now wraps results in a `ConsolidatedItem` instead of an
  anonymous `(content, metadata)` tuple, which makes upstream's
  `format_consolidated_item()` live code: it was defined and never called, so
  every subclass had to sniff tuple shapes inside `format_item()` to tell a
  consolidated result from one of its own items.

  `ProcessingConfig` is frozen, and rejects an `overlap_chars` above half of
  `max_context_chars`. The stride of a split is the difference between them,
  and the piece count grows without bound as it shrinks: one below the
  window, a split advances a character at a time, so a megabyte-long item
  becomes a million batches and a million model calls with nothing to warn
  the caller. Half is the largest overlap keeping the piece count within
  twice its minimum.

  Review of the port closed a further set of defects, each with a regression
  test verified by reverting the fix:

  - **`ProgressInfo.progress_percent` could never be anything but 0.0.**
    Nothing ever set `current_item`. `_process_level()` now counts items off
    as their batch completes — and counts an item dropped by the oversized
    strategy the moment the batcher drops it, since no extraction will reach
    it and a bar waiting for one would never fill.
  - **A query containing the literal `{content}` had the batch spliced into
    it.** Prompt rendering chained two `str.replace` calls, so the second ran
    over what the first substituted — doubling a prompt sized to fit exactly,
    which is the overflow the module exists to prevent. Substitution is now
    a single pass.
  - **A run that lost every item reported "All batches failed" and a
    `success_rate` of 1.0.** With every item dropped as oversized, no batch
    was ever built: the message named a failure that had not happened, and
    the ratio read as a clean run. The message now names both counts, and
    `success_rate` answers 0.0 when a batch-less run lost something and 1.0
    only when there was nothing to lose.
  - **The strict `FAIL` strategy was reported as an unexpected error**, with
    a full traceback, though it is the configuration doing exactly what it
    was asked. It raises `OversizedItemError` — still a `ValueError`, as
    documented — which `process()` reports plainly.
  - **`CONCATENATE` and `WEIGHTED` disagreed about the same results.** The
    former averaged only confidences above zero, so a batch the extractor had
    no confidence in *raised* the merged confidence. Every valid result now
    counts under both.
  - **`process()` kept its statistics on the instance**, so two concurrent
    runs on one processor interleaved and each could return the other's
    counts. They are a local.
  - `batch_metadata["item_indices"]` and the result's `source_indices` were
    both the `Batch`'s own list, and merging a lone result copied it
    shallowly. Nothing handed to a caller now shares a list with anything
    else.
  - Importing the package eagerly re-exported `LLMChunkProcessor`, and with
    it `BaseAgent`, `bmlib.templates` and jinja2 — over half the import cost
    for callers wanting only the LLM-free harness. A :pep:`562` `__getattr__`
    defers it, making the "no LLM dependency" claim true of the package and
    not merely of `base.py`.
  - `("text", True)` rendered as `score 1.00`, `bool` being an `int`. A
    boolean is no longer taken for a relevance score.

- **`fulltext`: a second source for PMC ID resolution, and NCBI as a full-text
  tier.** `FullTextService` could reach a PMC ID exactly one way — Europe PMC's
  search, gated on `inEPMC == "Y"`, which requires Europe PMC *both* to have
  indexed the paper and to hold its full text. A paper in PMC failing either
  condition skipped Tiers 1a/1b and fell through to Unpaywall or a bare DOI
  link. Two changes close that:

  `_resolve_pmc_id_via_idconv()` asks NCBI's ID Converter — the authoritative
  DOI/PMID→PMCID mapping, which depends on neither condition — but only when
  the Europe PMC search reported no PMC ID or could not be reached at all.
  Second, never first: that one search also returns the free-PDF URL the
  render tier needs, so asking the converter first would cost a request on
  every lookup or forfeit that URL. But it is consulted even when that search
  raised — a search that failed is when a second, independent resolver is
  worth most. It is asked by PMID when there is one, DOI otherwise, and never
  raises.

  `_fetch_ncbi_pmc()` becomes a new **Tier 1c**, reading NCBI's own copy via
  E-utilities `efetch` for whichever PMC ID is in hand — the caller's or a
  discovered one. Europe PMC serves the corpus its `inEPMC` flag describes;
  NCBI serves PMC itself, so this answers where Europe PMC cannot. It sits
  ahead of the free-PDF tier (renumbered to **1d**) because structured JATS
  beats a PDF that needs the optional `bmlib[pdf]` extra to read at all. An
  efetch reply carrying neither body nor abstract — what a publisher who does
  not release XML produces — raises rather than becoming a near-empty
  last-resort abstract.

  A PMC ID is now validated as `PMC\d+` in both PMC fetch helpers, at the point
  where it becomes a URL path rather than at each of the three places it
  arrives from.

  New constructor parameter `ncbi_api_key`, **declared last** for positional
  stability, sent with both NCBI requests. As with
  `TransparencyAnalyzer.pubmed_api_key` it changes which NCBI allowance the
  requests draw on, not bmlib's own pacing — the package still throttles
  nothing.

  **Moves stored values, not behind a flag:** `FullTextResult.source` gains
  `"ncbi_pmc"`, and results that were `content_kind="abstract"` or a bare
  `web_url` can now be `"fulltext"`. A caller who supplies `pmc_id` whose
  Europe PMC XML fails, or looks up an identifier Europe PMC cannot resolve,
  pays one or two extra requests in exactly the cases that previously ended at
  Unpaywall or Tier 3. Closes #47. Design:
  `docs/superpowers/specs/2026-08-02-pmc-id-resolution-fallback-design.md`.

- **`transparency`: `<DataBankList>` deposition accessions now score as
  data-availability evidence.** `bmlib.transparency` decided data availability
  by scanning full text for seven substrings (`"zenodo"`, `"figshare"`,
  `"dryad"`, `"github"`, `"available upon request"`, `"upon reasonable
  request"`, `"not available"`) — a paper that deposited its sequences in
  GenBank and said so in a structured field earned nothing unless one of
  those words happened to appear in its prose, and a closed-access paper has
  no full text to scan at all. `_parse_pubmed_signals()` now also collects
  `DataBankName` values against a curated allow-list drawn from
  [NLM's published vocabulary](https://www.nlm.nih.gov/bsd/medline_databank_source.html):
  `_DEPOSITION_DATABANK_LEVELS` maps each repository to the level a deposit
  into it establishes — BioProject, dbVar, Dryad, figshare, GenBank, GEO, PDB
  and SRA nominate `full_open`; dbGaP nominates only `on_request`, since its
  data needs Data Access Committee approval. A mapping rather than a
  set-per-level so that adding a repository has to state what a deposit into
  it is worth instead of inheriting the generous default. NLM's remaining
  names — dbSNP, GDB, OMIM, PIR,
  the three PubChem tables, RefSeq, SWISSPROT, UniMES, UniParc, UniProtKB,
  UniRef — are curated *reference* databases and score nothing (an OMIM
  number says the paper is about a known condition, not that these authors
  shared data of their own); a `<DataBank>` entry needs at least one
  non-blank accession to count.

  `data_level` now has two producers — this one and Europe PMC's existing
  prose scan — so `_Analysis` gained `note_data_level()`: each sub-step
  nominates a level and the strongest wins by rank (`_DATA_LEVEL_RANK`:
  `unknown` < `not_available` < `on_request` < `full_open`), mirroring the
  rule `industry_confidence` already follows. The winning level's points are
  now awarded once, by a new `_score_data_availability()` called from
  `analyze()` after every sub-step has run, rather than by the step that
  finds the level — with two producers, scoring at the point of discovery
  would double-count or spend points on a level a later nomination beats.
  PubMed's deposits are also reported verbatim as a new `risk_indicators`
  line, `Data deposited: GENBANK, PDB`, written whenever PubMed reported a
  deposit — even when the level it nominated lost the merge.

  **Moves stored values, not behind a flag:** `transparency_score` rises by
  10 or 20 for papers whose PubMed record names a deposition repository the
  prose scan missed. `data_availability_level` can move off
  `"not_available"`, which can in turn lift a `HIGH` result the
  industry-funding + restricted-data rule produced — `calculate_risk_level()`
  treats `"not_available"` as restricted. It can also move off `"unknown"`,
  but `"unknown"` was never restricted, so that move cannot affect the
  industry-funding rule; it can still turn a score-threshold `HIGH` into
  something else, since the added points can carry the score past
  `score_threshold`. And `"Data explicitly not available"` moves to the end
  of `risk_indicators` (it is now appended by `_score_data_availability()`
  after every step has run, rather than by the step that found the level),
  with `"Data deposited: …"` a new line alongside it. See
  `docs/superpowers/specs/2026-08-01-databank-data-deposition-design.md` for
  the rejected alternatives — a fifth `"deposited"` level distinct from
  `full_open`, scoring inside `note_data_level()` with a refund pass, and
  PubMed awarding only the diff against Europe PMC.

- **`scripts/sample_databank_names.py` — measures the two `DataBankName`
  allow-lists against real PubMed records.** `_TRIAL_REGISTRY_NAMES` and
  `_DEPOSITION_DATABANK_LEVELS` are curated from NLM's published vocabulary,
  and curation is the part that goes stale: the script counts records per
  candidate name, reads the literal spelling off the XML, and reports how
  bmlib classifies each — so a repository NLM adds shows up as `unclassified`
  with a non-zero count, and a member earning nothing shows up as dead weight.
  It also reports the *level* a deposit establishes, since that is a mapping
  rather than a membership test. Candidates include the deliberate exclusions
  (OMIM, RefSeq, dbSNP, PubChem-\*, the UniProt family): their counts are the
  evidence for leaving them out. A live runner like
  `scripts/sample_funder_names.py`, but covered offline by
  `tests/test_databank_sampler.py`, which pins the one property that makes its
  table trustworthy — a failed request never prints as a finding.

### Changed

- **`transparency`: `analyze()`'s accumulators moved onto one `_Analysis`
  carrier.** Ten values were passed into each sub-step and unpacked back out of
  a 4-to-6-element tuple, where element order was the only thing binding a
  value to its name — so a mis-ordered unpacking was a silent, type-compatible
  swap, and adding one signal meant widening several signatures. All five
  sub-steps now mutate the carrier and return `None`. `SCORE_FUNDER_INFO` is
  spent through a named `award_funder_info()` method, which makes "award this
  component at most once" a mechanism rather than a convention two call sites
  had to remember. Internal only: no public signature changed
  ([#37](https://github.com/hherb/bmlib/issues/37)).
- **`transparency`: a funder named repeatedly by CrossRef now yields one
  `Industry funder: X` indicator, not one per award record.** CrossRef emits
  one record per award, so an organisation funding several awards on a paper
  repeated in `risk_indicators`. PubMed's grant list already deduplicated;
  both sources now go through the same `note_industry_funder()` and follow the
  same rule. No score, no `industry_funding_detected` and no risk level moves
  — only the length of `risk_indicators` for affected papers.

### Fixed

- **`transparency`: `_TRIAL_REGISTRY_NAMES` was missing three registry names
  PubMed actually emits** — `JMACCT`, `REPEC`, and NLM's own spelling of
  UMIN's registry, `"UMIN CTR"` (bmlib had only the hyphenated
  `"umin-ctr"`, so the exact-match test failed on the string PubMed sends;
  both spellings are now kept, since the hyphenated form appears in older
  records). A paper registered in any of the three silently lost
  `SCORE_TRIAL_REGISTERED`. **Moves stored values:** `trial_registered`
  becomes `True` and `transparency_score` rises by 20 for affected papers;
  a paper registered in one of the three with no NCT id credited in its
  abstract also gains a new `risk_indicators` line, `"Trial registration
  found; posted-results status could not be checked"`, from the
  `_check_trial_registration` branch that `registration_not_checkable` now
  reaches for these names. Found while curating the deposition allow-lists
  above against NLM's vocabulary table; it is a pre-existing bug rather than
  part of that feature, so it gets its own entry.

## [0.6.0] — 2026-07-30

The largest release since 0.4.0. `bmlib.publications` runs on PostgreSQL,
`FullTextService` reads PDFs, `BaseAgent` gained per-agent metrics and
embeddings, the transparency analyzer queries PubMed, and the JSON extraction
path was consolidated and two silent-truncation defects fixed.

No public signature changed incompatibly and nothing was removed, so the bump
is minor. **Three behaviour changes make stored results non-comparable**, none
of them behind an opt-in flag: transparency scores can rise (the PubMed step),
`industry_funding_detected` moves in *both* directions (the measured funder
matcher), and an unfenced or truncated array of objects now extracts whole
where it used to arrive as its first element. See **Compatibility** at the end
of this section for exactly who is affected.

### Added

- **`bmlib.publications` works on PostgreSQL.** `schema.py`, `storage.py` and
  `sync.py` were SQLite-only (`?` placeholders, `cur.lastrowid`,
  `UPDATE OR IGNORE`, `AUTOINCREMENT`) even though `bmlib.db` has supported
  both backends all along. Every statement is now written for both, and
  `ensure_schema()` picks the matching DDL. The behaviour is pinned by
  `tests/test_backends.py`, which runs each test against both backends.
- `bmlib.db.is_sqlite()`, `placeholder()` and `placeholders()` — the backend
  detection every dual-dialect module needs, promoted out of the private
  helpers in `db/migrations.py`.
- `publications.pmcid` — a column, a `Publication` field, and the conversion
  in `sync._record_to_publication()`. `FetchedRecord.pmc_id` was being dropped
  on store, so full-text retrieval could not use the PMC id a fetcher had
  already found. `ensure_schema()` adds the column to databases created by an
  earlier bmlib. The field is declared **last** on the dataclass, not beside
  `pmid` where it reads best: `Publication` is constructed positionally by
  downstream projects, so any other placement would shift every following
  argument and land a caller's `abstract` in `pmcid` with no error anywhere.
  Pinned by `test_positional_construction_is_stable_across_versions`.
- `bmlib.db.transaction_depth()` / `owns_commit()` — how many `transaction()`
  blocks the calling thread has open on a connection.
- Opt-in PostgreSQL test coverage: set `BMLIB_TEST_POSTGRESQL_DSN` to run the
  two-backend suite against a live server. Unset, those parameterisations skip
  and the suite is unchanged. CI runs it against a `postgres:16` service on
  every matrix entry, with `BMLIB_REQUIRE_POSTGRESQL=1` so a missing or broken
  DSN fails the build instead of skipping behind a green check.
- **`FullTextService` extracts a retrieved PDF's text into
  `FullTextResult.html`**, so a PDF-only article can be read inline. Needs the
  `bmlib[pdf]` extra and a cached PDF (that is, an `identifier`); opt out with
  `FullTextService(convert_pdfs=False)`. `pdf_url` and `file_path` stay
  populated, since extraction recovers prose but not figures, tables or
  layout. This closes the ROADMAP item that had the converter standalone.
- `fulltext.render_html()` — renders extracted PDF text as HTML, stripping
  repeated page furniture (running heads, footers, publisher watermarks) by a
  frequency rule that needs no per-publisher knowledge, and reflowing
  hard-wrapped lines back into paragraphs.
- `FullTextResult.content_kind` — says whether `html` holds a real article
  (`"fulltext"`), only an abstract (`"abstract"`), or prose extracted from a
  PDF (`"extracted"`). Code that scores or summarises an article should branch
  on this rather than on `html` being set.
- `JATSArticle.has_body` — whether `<body>` carried actual prose. It counts
  body paragraphs rather than `body_sections`, because back-matter sections
  land in the latter and a "Data Availability" section was otherwise passing
  for an article body.
- `JATSParser.parse_with_html()` — parses once and returns both the article
  and its HTML, instead of the two SAX passes `parse()` + `to_html()` cost.
- `ConversionResult.page_texts` — the text of each page that yielded any.
  Page boundaries are what let `render_html()` spot repeated furniture.
- `bmlib.agents.PerformanceMetrics` — thread-safe per-agent call accounting
  (prompt/completion/total tokens, request and retry counts, wall time),
  independent of the process-wide `TokenTracker`: `PerformanceMetrics` answers
  "what did this agent do", `TokenTracker` answers "what has this process
  spent". `BaseAgent` gained the matching accessors — `metrics` (an
  independent snapshot), `reset_metrics()`, `start_metrics()`,
  `stop_metrics()`, and `format_metrics_report()`. `chat()` times every call
  and records it into the metrics only on success; a call that raises records
  nothing, so a burst of failures cannot deflate `tokens_per_second`.
- `BaseAgent.embed()` / `embed_batch()` / `test_connection()`, and the
  `embedding_model` constructor parameter. `embedding_model` is declared
  **last**, after `max_tokens`, so existing positional construction is
  unaffected. Embedding calls are deliberately excluded from
  `PerformanceMetrics` — mixing them into `tokens_per_second`, a figure about
  generation throughput, would distort it.
- `BaseAgent.chat_json(..., retry_context: str = "")` — a label naming the
  task being attempted, folded into every retry, error, and failure message,
  including the temperature-0 truncation raise. Empty by default, so existing
  log lines are unchanged for callers that do not pass it.
- `bmlib.llm.utils.iter_json_spans()` — the locator now shared by
  `extract_json()` and `extract_and_repair_json()` (see Changed, below).
  Yields JSON candidate spans best-first without validating them: fenced
  ` ```json ` blocks, other JSON-shaped fences, remaining fences, balanced
  `{...}`/`[...]` spans, brace-only spans nested inside an already-yielded
  span, and — only when nothing balanced, i.e. truncated output — the text
  from the first opener to the end.
- `bmlib.llm.json_repair.salvage_json_fields()` — recovers individually named
  fields from a response `extract_and_repair_json()` gives up on entirely.
  Two-phase per key, both phases bounded: a fast `raw_decode` pass over the
  first `MAX_SALVAGE_MATCHES` (200) matches, then at most one `repair_json`
  attempt, at the last match, if no fast attempt succeeded. Both bounds
  matter, because every failed decode scans forward to the end of the
  document and a repetition-looping model — the failure mode salvage exists
  for — is what produces thousands of matches: unbounded repair made 3,000
  matches take 135s, and an unbounded fast pass left the whole function
  quadratic at ~1.0s for 50,000 matches. Bounded, that case is ~0.08s. Never
  raises on malformed text — including `RecursionError`, which `raw_decode()`
  throws rather than `ValueError` on input nested past the interpreter's stack
  limit; returns `{}` when nothing is found. Not wired into `parse_json()` —
  silently returning partial data would turn a loud failure into a quiet wrong
  answer, so callers opt in after catching the `ValueError`.
- **`TransparencyAnalyzer` queries PubMed, and `pubmed_api_key` finally does
  something** (closes #18). The parameter has always been accepted and never
  read — the port from bmlibrarian dropped the client that used it. There is
  now one E-utilities `efetch` request per analysis at most, placed after
  Europe PMC (so a DOI-only analysis can reuse the PMID from the record
  already fetched) and before ClinicalTrials.gov (so a structured accession
  can feed the posted-results check). No PMID from either source and the step
  is skipped entirely. It contributes three signals, all publisher-supplied
  structured metadata, each closing a gap Europe PMC leaves on closed-access
  papers:
  - `<CoiStatement>` establishes a COI disclosure with no full text to scan.
    A *missing* statement never demotes `coi_disclosed` from `None` to
    `False`: it means the publisher supplied none, not that the paper carries
    none, and `False` would trigger the missing-COI downgrade on no evidence.
  - `<DataBankList>` trial-registry accessions are trusted directly, skipping
    the abstract heuristic's registration-cue window and two-id cap — those
    exist only because scraping NCT ids out of prose cannot tell a paper's own
    registration from a review's citation list, which a databank entry
    already distinguishes. Registration in a registry other than
    ClinicalTrials.gov now counts too, with a distinct indicator, since its
    posted-results status cannot be looked up there.
  - `<GrantList>` gives a PMID-only analysis its first funder signal; an
    industry agency carries `DEFAULT_INDUSTRY_CONFIDENCE`, the same as a
    CrossRef funder record, both being structured metadata.

  What the key buys, stated precisely: NCBI meters unkeyed E-utilities traffic
  at 3 requests/second per IP and keyed traffic at 10 requests/second per key,
  so passing it moves bmlib's request out of the bucket the calling
  application's own E-utilities traffic already competes for. It does not
  change bmlib's own pacing, which stays on the 350 ms interval shared with
  the other APIs.
- `TransparencyUnknownReason` (`DISABLED` / `NO_IDENTIFIER` / `UNREACHABLE`)
  and `TransparencyResult.unknown_reason` (closes #21). `analyze()` returns
  `UNKNOWN` at score 0 for three unrelated reasons, and telling them apart
  meant matching `risk_indicators` prose — documentation, not API. The
  strings stay for humans. Set if and only if `risk_level` is `UNKNOWN`:
  `calculate_risk_level()` never returns `UNKNOWN`, so every one comes from a
  known early return. Serialised by value like `risk_level`, and the *key* is
  read defensively on the way back in, so results persisted before the field
  existed still load — a present-but-unrecognised value still raises, exactly
  as `risk_level` does. `__post_init__` enforces the invariant in the one
  direction that cannot collide with those legacy results: a reason on a
  non-`UNKNOWN` result raises `ValueError`, while an `UNKNOWN` without a
  reason is accepted. Declared **last** on the dataclass, for the same reason
  as `Publication.pmcid`.
- **`require_dict` on `BaseAgent.parse_json()` and `chat_json()`** (part of
  #33) — opt-in strictness for callers that need a JSON object rather than
  whatever the model happened to emit. `parse_json(require_dict=True)` raises
  `ValueError` naming the shape it got. `chat_json(require_dict=True)` treats a
  wrong shape as a retryable failure inside its existing backoff loop, so a
  model that answered with an array gets up to `max_retries` attempts at a
  usable answer — **except at temperature 0**, where it raises on the first
  one, mirroring the truncation path: greedy sampling returns the same array
  from the same messages, so the retry is provably futile. The shape failure is
  reported separately from `"unparseable response"`: the response *was* valid
  JSON, just the wrong shape, and `chat_json()` runs its own `isinstance` check
  rather than message-sniffing a `ValueError` to tell the two apart. Both
  return paths are covered, including the truncation path's `_try_parse()`
  shortcut. `@overload` on `Literal[True]` narrows the return to `dict` for
  strict callers, so the widened annotation costs them no `isinstance`
  friction; CI runs ruff only, so the `@overload`/`@staticmethod` stacking
  order was verified once against mypy outside the build. A **third overload
  taking a plain `bool`** keeps `require_dict=self.strict` type-checkable —
  mypy does not expand `bool` into `Literal[True] | Literal[False]` to match
  one of the other two, so without it a caller holding a runtime flag gets
  "no overload variant matches" and no way to satisfy it.
- **`allow_fragments` on `bmlib.llm.utils.extract_json()`** — when False the
  last-resort second walk is skipped, so *text* comes back unchanged rather
  than an object dug out of the inside of a span. A caller that can *repair*
  has something better to try than a fragment; `BaseAgent.parse_json()` is the
  one caller that does. The providers' `json_mode` path takes the default,
  since it has no repair stage.

### Changed

- **`extract_json()` prefers a whole span over a nested fragment** (part of
  #33). The acceptance policy is split out as a private `_first_acceptable()`
  and run twice: once over whole spans only, and — only when nothing there
  parsed — once more with the nested-object stage enabled. An object dug out
  of the inside of another span is now a last resort rather than a preference,
  so a response whose JSON is an **array of objects** is returned whole where
  it was previously reduced to its first element.

  The non-dict fallback within each walk is *ranked* rather than
  first-parseable: a span that is a list holding at least one object beats any
  other non-dict span. Without that, an incidental parseable span earlier in
  the response (`'[] and [{"a": 1}]'`) would be accepted by the first walk, the
  second walk would never run, and the caller would receive unrelated data
  that parses cleanly and survives every downstream shape check — a worse
  failure than the truncation being fixed.

  `extract_and_repair_json()` deliberately has **no** equivalent second walk.
  Validating a nested fragment reports what is there; repairing one closes
  brackets around it and fabricates a structure the model never emitted.
- **`BaseAgent.parse_json()` and `chat_json()` are annotated `dict | list`**
  (closes #33). They always returned whatever the response parsed to, so a
  model answering with a top-level array handed back a list; the annotation
  now says so. Raising on a non-dict was considered and rejected: it would
  have hidden the fragment loss above rather than repairing it — and
  inconsistently, since `parse_json()` tries `json.loads(text)` first, so a
  bare array would have raised while the same array in prose came back as its
  first element and passed as a dict. It would also lock out the array-shaped
  agents queued for the bmlibrarian port. Callers needing an object say so
  with `require_dict` instead; `_try_parse()` widens to `dict | list | None`
  to match.

  `dict | list` is now the whole contract and is **enforced**, not merely
  annotated: a response that parses to a bare scalar — `42`, `"done"`,
  `true`, `null` — raises `ValueError` naming the type it got, where it used
  to be handed back past an annotation that excluded it. A scalar is not a
  structured answer to a `json_mode` request, and returning one only defers
  the failure to the caller's first subscript. Inside `chat_json()` it
  surfaces as an ordinary unparseable response and is retried.
- **`BaseAgent.parse_json()` defers the nested fragment past its repair
  stage.** It now asks `extract_json()` for whole spans only, tries repair,
  and re-asks with fragments allowed only if repair also failed. A *truncated*
  array of objects — `'[{"a": 1}, {"b": 2}'` — never balances, so extraction
  could only ever offer the first object: taking it dropped the sibling and
  skipped repair's truncation WARNING, while repair closes the bracket and
  recovers the whole array. This is the same silent loss the whole-span
  preference fixes one level up, on the shape most likely to arrive that way.
  `'[{"a": 1}, invalid junk]'` — nothing whole to recover — still returns the
  fragment.
- **`extract_json()` and `extract_and_repair_json()` are rebuilt on the
  shared locator `iter_json_spans()`** (closes #17). Behaviour deltas fall
  out of the consolidation:
  - Bare top-level arrays are now visible to `extract_json()` — previously an
    unfenced `[...]` response with no object anywhere fell through to the
    raw, unparsed input.
  - **Dict preference (`extract_json()` only):** when a response contains a
    **top-level** object alongside an incidental array, `extract_json()`
    returns the object, because the object is what a `json_mode` caller
    actually asked for. This is not new: the pre-consolidation brace-only scan
    was object-only, so it already returned `{"a": 1}` for
    `extract_json('[1, 2] then {"a": 1}')`. What *is* new is that a **fenced**
    candidate now outranks dict preference — a fence is the model's own
    delimitation of its answer, so a fenced JSON array must not be reduced to
    an object plucked from inside it by a later, unfenced stage — and that the
    preference no longer extends to an object reachable only from *inside*
    another span; see "prefers a whole span over a nested fragment" below.
  - **Fence priority (`extract_json()` only):** a ` ```json `-tagged fence now
    wins over an earlier untagged fence, instead of whichever fence comes
    first in document order winning regardless of its language tag.
    `extract_and_repair_json()` already prioritised ` ```json ` fences before
    this branch, so this delta is new only for `extract_json()`.
  - `extract_and_repair_json()` now walks candidates instead of staking
    everything on a single span: a candidate that fails to parse or repair no
    longer ends the search, so the next one gets a chance. With
    `repair=False`, this raises a plain `ValueError` on the final exhausted
    candidate where the pre-consolidation code re-raised the original
    `json.JSONDecodeError`. `JSONDecodeError` subclasses `ValueError`, so
    `except ValueError` callers are unaffected; `except json.JSONDecodeError`
    specifically no longer catches it.
  - `iter_json_spans()` yields no span twice, compared by text rather than
    position. The stages overlap — stages 4 and 5 rescan fence interiors as
    plain text, so every fenced body reached the balanced scan a second time
    — and a repeated candidate only buys a second run of `repair_json()`'s
    attempt loop on a span that has already failed.
  - `RecursionError` is caught alongside `JSONDecodeError` wherever a
    candidate is decoded — in `extract_json()`, `extract_and_repair_json()`
    and `BaseAgent.parse_json()`. `json.loads()` descends recursively, so
    text nested past the interpreter's stack limit (`'{"j": ' * 20000`, the
    shape a repetition-looping model emits) blows the stack rather than
    failing to decode, and each of those functions documents a
    never-raise-or-`ValueError` contract that the escape broke.
    `extract_json()` is the one that matters: it runs unconditionally on
    every `json_mode` response in both the Anthropic and OpenAI-compatible
    providers, and the stage-6 tail candidate hands it the whole nested run
    where the pre-consolidation brace scan found nothing balanced and
    returned the input untouched.
- `BaseAgent.parse_json()` now logs a WARNING when its repair stage is what
  rescued the response — repair closes brackets, so a truncated response can
  parse into a valid but incomplete object, and the log line says so.
- `PerformanceMetrics.elapsed_time_seconds` is measured on `time.monotonic()`
  rather than as a difference of the `time.time()` timestamps in `start_time`
  / `end_time`, which remain absolute so a caller can still render them as
  dates. A wall-clock difference can be distorted — or made negative — by an
  NTP step or a DST change mid-run, and `format_report()` prints this figure
  directly against `total_wall_time_seconds`, which `BaseAgent` accumulates
  from `time.monotonic()`; two clocks either side of that comparison is how
  "12.3s elapsed (14.1s in requests)" gets printed. `snapshot()` carries the
  monotonic marks across; an instance rebuilt by `from_dict()` has none —
  they are not meaningful between processes, so they are not serialised —
  and falls back to the timestamp difference.
- `TransparencyResult.trial_registered` can now be `True` for a registration
  in a registry other than ClinicalTrials.gov, which PubMed's `<DataBankList>`
  makes visible for the first time. `trial_results_compliant` stays `False`
  there — ClinicalTrials.gov has no answer for an ISRCTN number — so the
  indicator says `"Trial registration found; posted-results status could not
  be checked"` rather than the misleading `"Registered trial without posted
  results"`. Read the indicator, not the flag, to tell "checked and absent"
  from "not checkable". The line names the consequence rather than the cause
  because it also covers a *ClinicalTrials.gov* registration whose accession
  was unusable, for which "registered outside ClinicalTrials.gov" would be
  false.
- A COI disclosure found in PubMed retracts the two full-text COI indicators
  (`"No COI disclosure found in full text"`, `"COI disclosure status unknown
  (full text unavailable)"`) rather than leaving them to contradict
  `coi_disclosed=True`, and appends `"COI disclosure found in PubMed record"`
  in their place.

### Fixed

- **Industry-funder matching was punctuation-dependent, and measurably
  imprecise** (closes #36). `_INDUSTRY_KEYWORDS` tested substrings, so `"inc."`
  had to carry its trailing dot as a crude word-boundary substitute — and
  therefore missed an NLM-normalised `"Pfizer Inc"`. The list is now split into
  substring stems and whole-word terms behind one `_is_industry_funder()`
  predicate, used by both structured funder sources.

  The recalibration was **measured** rather than assumed, because
  `industry_funding_detected` feeds a HIGH-risk rule and HIGH applies
  `tier_downgrade_amount`. Against 833 real names sampled from CrossRef
  `funder[].name` and PubMed `<Grant><Agency>` (`scripts/sample_funder_names.py`,
  a live runner outside the pytest suite), 417 of them hand-labelled and
  committed as `tests/data/funder_names.json`:

  | Matcher | Precision | Recall |
  |---|---|---|
  | Substring (before) | 0.400 | 0.176 |
  | Split (now) | 0.917 | 0.324 |

  The corpus overturned two members that looked obviously right:
  - `"pharma"` scored 3 true positives against 5 false ones, reaching
    `"Faculty of Pharmacy"`, `"Pharmacogenetics …"` and `"Clinical Pharmacy"`.
    Narrowed to `"pharmaceutic"`, which keeps every true positive; the bare
    word is retained separately for `"Novartis Pharma AG"`.
  - `"biotech"` scored 0 true positives against 4 false ones — an Indian
    ministry department and a UK research council. *Biotechnology* names a
    field, not a company type. Only the bare word survives.

  Added on measured evidence: `"llc"`, `"incorporated"`, `"limited"` (2/1/1 true
  positives, no false ones; `\binc\b` cannot reach `"Incorporated"`). Rejected
  on it: `"co"` (collides with the English prefix) and `"corporation"` (US
  non-profits use it). `"ab"` and `"labs"` passed the count but were excluded
  because they collide with province codes and national laboratories, which the
  corpus happens not to contain — costing two true positives, named in the
  source comment.

  **Detection moves in both directions**, so stored `industry_funding_detected`
  values and the scores derived from them are not comparable across this change.
  Papers funded by `"… Inc"`, `"… LLC"`, `"… Limited"` or `"… Incorporated"`
  start being flagged; papers whose only match was a pharmacy department, a
  biotechnology ministry or a research council stop being flagged. The second
  group is the larger one, and every one of them was a false positive.
- **`extract_json()` silently dropped every sibling of an unfenced array of
  objects** (part of #33). `iter_json_spans()` offers the array at stage 4 and
  the object nested inside it at stage 5, and the dict preference accepted the
  fragment — so `'[{"a": 1}, {"b": 2}]'` in prose returned `{"a": 1}` with no
  error anywhere. See the two-walk policy under **Changed** for the fix, and
  **Compatibility** for who is affected.
- **A wrong-shaped response cost the two quality tiers a whole assessment.**
  `StudyClassifier.classify()` and `QualityAgent.assess()` hand `chat_json()`'s
  result to a `_parse_data()` that calls `.get()`, so a list raised
  `AttributeError` into a broad `except Exception` and degraded the paper to
  `UNCLASSIFIED` — no retry, and nothing in the log naming the shape. Both now
  pass `require_dict=True`, and both run at temperature > 0, so the wrong shape
  buys up to three attempts at a usable answer instead of one silent failure.
  Note the cost on the other side: `assess_batch()` is a serial loop and the
  backoff is a blocking `sleep`, so a model that answers *every* request with
  the wrong shape now spends 3 calls plus ~3s per paper where it spent 1 call
  and no sleep.
- **A truncated array of objects reached the caller as its first element.**
  `require_dict=True` was no defence: `chat_json()`'s truncation branch asked
  `_try_parse()` first, `parse_json()`'s extraction stage returned the object
  dug out of the unbalanced array, and the result passed the `isinstance`
  check as a dict — so the response came back as `{"a": 1}` on the first
  attempt with no truncation error and no repair WARNING, under a comment
  claiming the JSON "happens to be complete". `parse_json()` now holds the
  fragment back until repair has had its turn; repair closes the bracket and
  recovers the whole array.
- **An unsectioned JATS `<body>` lost all its prose.** `<sec>` is optional
  inside `<body>`, but the handler recorded a `<p>` only when a section was
  open, so an article whose body is bare `<p>` children was parsed as having
  no body at all — the paragraphs reached neither `body_sections` nor the
  rendered HTML. Since `has_body` landed, that also cost a permanent cache
  miss: `FullTextService` read such an article as abstract-only, declined to
  cache it, and re-fetched it on every request. Loose prose now becomes a
  `JATSBodySection` with an empty `title` — no heading is invented — flushed
  at each `<sec>` boundary so document order survives and real sections stay
  top-level instead of nesting inside it. Empty paragraphs are dropped, so a
  whitespace-only `<body>` still reports no body.
- **Figure and table captions were lost whenever the figure sat inside a
  `<sec>`** — the ordinary PMC layout. JATS carries caption body in `<p>` and
  the caption lead in `<title>`, the same elements that carry section prose
  and section headings, and the handler routed them by whichever `in_*` flag
  was set rather than by the enclosing `<caption>`. Inside a section the
  section branch won, so `JATSFigureInfo.caption` and `JATSTableInfo.caption`
  came back empty, the caption text was reprinted as article prose, and a
  `<caption><title>` **renamed the enclosing section** after the figure.
  Captions are now routed on `<caption>` itself and survive in every document
  shape. Non-caption `<p>` inside a figure or table — cell text, table
  footnotes — no longer leaks either: cells reach the rendered table through
  `characters()`, so passing them on had been duplicating them into
  `body_sections` and counting them towards `has_body`, and outside a `<sec>`
  appending them to the caption.
- **A body-less JATS document was mistaken for full text.** medRxiv's
  `jatsxml` URL serves, for some preprints, a document made of `<front>` and
  `<back>` alone. It returns HTTP 200 and parses cleanly, so the retrieval
  chain — which sorts `xml` ahead of `pdf` — treated it as a successful
  retrieval, never tried the PDF holding the actual article, and cached the
  abstract-only rendering permanently. Body presence varies per paper rather
  than per publisher, so this is fixed generically: such a document is now
  detected, never cached, and held back as a last resort while the chain keeps
  looking. If nothing better turns up it is returned with any resolved link
  attached, so the reader gets the abstract *and* somewhere to go.
- **Text extracted from a PDF was produced once and then lost.** Only the PDF
  bytes were cached, so a second `fetch_fulltext()` for the same identifier
  returned a bare `file_path` and the inline article text silently
  disappeared. A cached PDF hit now re-derives it.
- **A missing abstract killed the whole scoring batch.** A record with no
  abstract arrives as `None` from a nullable column, and both LLM tiers sliced
  it unguarded, so a `TypeError` escaped the assessment and took every later
  paper down with it. Both tiers now tolerate a `None` title or abstract. With
  *both* missing they return `unclassified()` without calling the model, since
  an empty prompt yields not an empty answer but an invented one that nothing
  downstream can tell from a real assessment.
- **The Tier 2 classifier's token budget could not be raised.** `classify()`
  repeated `temperature` and `max_tokens` at the call site, silently
  overriding the constructor. The classification JSON is ~50 tokens, but small
  local models preface it with commentary despite being asked for JSON alone,
  and the 256-token ceiling truncated the preamble and lost the JSON with it —
  affected papers fell back to `UNCLASSIFIED` with only a warning. The
  overrides are gone and the budget is now 1024, matching the assessor. Both
  agents carry their tuned sampling as constructor defaults, so it holds
  however they are built rather than only via `QualityManager`.
- **A PDF that yielded no text failed silently.** `PyMuPDFConverter.convert()`
  reports failure in its result rather than raising, so a corrupt PDF, an
  image-only scan, or a partial extraction all passed unlogged. Each is now
  reported at WARNING, and a partial extraction is flagged rather than
  attached as if it were the whole article.
- **`render_html()` collapsed a document into a single paragraph** when fewer
  than a tenth of its lines ran full width — a reference list, a table, a
  two-column extraction. The wrap-width estimate landed on a stub line, so no
  line ever counted as short enough to end a paragraph.
- **`fetch_scalar()` always returned `None` on PostgreSQL.** psycopg2's
  `RealDictRow` is keyed by column name, so `row[0]` raised `KeyError` and was
  swallowed by the fallback. It now reads the first value on dict-like rows.
- **`transaction()` now nests on PostgreSQL**, via savepoints, as it already
  did on SQLite. Previously an inner block committed connection-wide, so a
  batch's partial writes could not be rolled back — `publications.sync()`'s
  one-commit-per-day batching silently degraded to one commit per record.
  Nesting is detected from bmlib's own open-block count, *not* psycopg2's
  transaction status: psycopg2 opens a transaction on the first statement of
  any kind, so a bare `SELECT` would have made every following block look
  nested and stop committing. Un-nested blocks commit exactly as before.
  The count is kept per *(thread, connection)*: nesting describes one call
  stack, and counting by connection alone let a block open on one thread make
  an unrelated outermost block on another thread look nested — that block
  opened a savepoint, never committed, and its write was lost silently.
- `create_tables()` no longer commits mid-migration on PostgreSQL, so a
  migration that fails part-way rolls back whole. It already behaved this way
  on SQLite.
- `ensure_schema()` looks for existing columns in `current_schema()` only.
  `information_schema.columns` spans every schema the connected user can see,
  so on a database shared with another consumer the check could answer about
  *their* `publications` table — reporting `pmcid` present, skipping the
  `ALTER`, and failing the next write on the missing column.

### Documentation

- `docs/manual/fulltext.md` carried the `## PDF Conversion` section **twice**,
  with overlapping but non-identical content, so every converter API change
  had to be made in two places or the page contradicted itself — which it
  did: one copy called the converter a standalone module "nothing in the
  retrieval chain calls", while the Module layout table above it correctly
  said the service extracts a retrieved PDF's text. The two copies are merged
  into one, keeping the fuller reference and folding in the `page_texts` and
  `render_html()` material the other copy held alone. A stray cache-key
  paragraph that had been duplicated into the same region, restating what
  "Cache keys" already covers, is gone too.
- `docs/manual/transparency.md` contradicted itself on thread safety: the
  constructor section said "do not share one analyzer across threads" —
  guidance from before 0.4.0 made it thread-safe — while the concurrency
  section 300 lines below correctly recommended sharing one instance. The
  stale sentence is gone. A paragraph about the COI fallback window's known
  limitation also appeared twice, in slightly different words; the two are
  merged.

### Added — development tooling

- `scripts/sample_funder_names.py` — samples funder names live from CrossRef and
  PubMed to build the labelled corpus behind `_is_industry_funder()`. A live
  runner outside the pytest suite, like `scripts/smoke_test_tool_calling.py`;
  the suite consumes only its committed, hand-labelled output, so tests stay
  offline.

### Compatibility

No public signature changed and nothing was removed. SQLite behaviour is
byte-for-byte unchanged — the full pre-existing suite passes untouched. On
PostgreSQL the changes above are strictly fixes to paths that were broken or
absent. Databases created by an earlier bmlib pick up the new `pmcid` column
on the next `ensure_schema()` call, which `sync()` makes for you.

This section is otherwise about additive and fix-only changes, but the JSON
extraction deltas above (dict/fence-priority ordering and the whole-span
preference in `extract_json()`; walk-past-a-bad-candidate policy in
`extract_and_repair_json()`) are the first **behaviour** change here on a
genuinely hot path: both `bmlib/llm/providers/anthropic.py` and
`openai_compat.py` call `extract_json()` on every `json_mode` response,
unconditionally, not from an opt-in code path.

**Who is affected by the whole-span preference.** In `extract_json()` — the
function the two providers call — exactly one response shape: an **array of
objects sitting unfenced in prose**. Such a response now arrives whole where it
previously arrived as its first element. Two neighbouring shapes are unchanged
there — a fenced array already came back whole, and a bare array parses at the
provider's own `json.loads()` guard and never reaches `extract_json()` at all —
and an array of scalars had no nested candidate to lose to. Code that relied on
receiving the first element will now receive a list; `BaseAgent` callers wanting
the old dict-or-nothing guarantee should pass `require_dict=True`, which turns
the wrong shape into a diagnosed retry rather than a silent truncation.

**A second shape changes through `BaseAgent.parse_json()` only:** a **truncated**
array of objects, `'[{"a": 1}, {"b": 2}'`. It never balances, so `extract_json()`
still has only the first object to offer and is unchanged for the providers —
but `parse_json()` now lets its repair stage go first, so the response arrives
as the whole array with the usual possibly-truncated WARNING instead of as
`{"a": 1}` in silence. Under `require_dict=True` the recovered list is the wrong
shape, so it becomes a diagnosed retry — this is the one case where adding
`require_dict=True` can turn a previously "successful" call into a raise. That
call was returning a single record out of two.

`parse_json()` and `chat_json()` return `dict | list` where they were annotated
`-> dict`. No runtime behaviour changed for a response that parses to an
object, and nothing was removed — the annotation was always wrong for an array
response, which is what #33 reported. The one narrowing is that `dict | list`
is now enforced: a bare scalar response raises where it used to be returned.

Two details worth knowing when upgrading:

- **`ensure_schema()` is required after upgrading, not optional.** Reads
  tolerate a database that has not been through it — `storage` treats a
  post-release column as absent rather than raising — but writes name every
  column and will fail on one the database lacks. `sync()` calls it for you;
  code that goes straight to `store_publication()` must call it itself.
- `Publication` gained a field. Positional construction and `from_dict()` on a
  dict serialised by an older bmlib both behave exactly as before.
- `TransparencyResult` likewise gained `unknown_reason`, declared last, so
  positional construction is unaffected and a dict without the key loads with
  it set to `None`.

The transparency analyzer's behaviour does change, in ways worth planning for
even though no signature did:

- **One more outgoing request per analysis** (~0.35 s of enforced interval)
  whenever a PMID is available, which is most of the time. An analysis with
  neither a supplied PMID nor one in the Europe PMC record costs exactly what
  it did before.
- **Scores can go up.** A closed-access paper that previously scored 0 for COI
  and funding can now earn both from PubMed metadata, which may move a paper
  across `score_threshold` and out of HIGH. Stored scores from an earlier
  bmlib are not comparable with new ones for the same paper.
- `coi_disclosed` can now be `True` where it was `None`, and `False` is
  correspondingly rarer: it now means neither the full text nor PubMed had a
  statement.
- **`industry_funding_detected` moves in both directions** with the #36 matcher
  recalibration — see **Fixed** for the measured numbers. Papers matched only by
  a pharmacy department, a biotechnology ministry or a research council stop
  being flagged (all false positives); papers funded by `"… Inc"` without a dot,
  or by an LLC, stop being missed. Precision rose from 0.400 to 0.917 on the
  labelled corpus, so the net effect is fewer spurious tier downgrades.

## [0.5.1] — 2026-07-21

All changes are confined to `bmlib/llm/providers/ollama.py`. No public
signature changed incompatibly; `list_models()` gained an optional keyword.

### Changed

- **`OllamaProvider.list_models()` now costs one HTTP request** regardless of
  how many models are installed, instead of one `/api/show` per model. On a
  server with 139 models the call went from minutes to 64 ms. It reads
  `/api/tags` as raw JSON rather than through the `ollama` SDK, whose Pydantic
  model silently drops the per-model `capabilities` array and
  `details.context_length`.
- `list_models()` results are cached for `CACHE_TTL_SECONDS` (60); pass the new
  `force_refresh=True` to bypass the cache. The cache is cleared only on a
  successful fetch, so a refused connection no longer discards accumulated
  results.
- Models whose `/api/tags` entry omits `context_length` return metadata whose
  `context_window` — and `capabilities.max_context_window` — resolves via a
  memoised `show()` call on first read, not at list time. `__repr__` renders
  `<unresolved>` rather than fetching, so logging a model list stays free.
  These objects degrade to plain `ModelMetadata` / `ProviderCapabilities` when
  copied, pickled, or passed through `dataclasses.replace()`. This is the only
  place in bmlib where attribute access performs I/O.

### Fixed

- **Capability flags from `list_models()` were always `False`.**
  `supports_function_calling` and `supports_vision` are now derived from the
  `/api/tags` capabilities array. They are a **lower bound**: `/api/show`,
  reached via `get_model_metadata()`, reports a superset for these two flags
  (across 139 local models, tags reported 77 tool-capable against show's 102,
  and 32 vision-capable against 44). Filter by capability with
  `get_model_metadata()` when completeness matters — but note it is
  authoritative only when its `show()` call succeeds; for a cloud model on a
  server with cloud disabled, `show()` returns 403 and the fallback is
  *weaker* than the listing.
- **Context windows resolved to the 8192 fallback for every model.**
  `_extract_context_window` looked up `model_info`, which `ShowResponse`
  declares as `modelinfo` with `model_info` only as an alias, so on a real SDK
  response the lookup returned `None`. Real windows (131072, 128000, …) now
  resolve. The string-valued `parameters` fallback was dead for the same
  reason and now works.
- `get_model_metadata()` hardcoded its capability flags to `False`, so it
  contradicted `list_models()` for the same model. It now derives them from
  `ShowResponse.capabilities`.
- GGUF emits both `<arch>.context_length` and
  `<arch>.rope.scaling.original_context_length` — 9 of 139 models carry both,
  differing by up to two orders of magnitude. The exact key now wins outright
  instead of the first loose "context" match, removing a dependence on key
  emission order.

### Security

- `OLLAMA_API_KEY` is no longer leaked across a redirect. `urllib` re-sends
  every header to any host on redirect, so a gateway answering `/api/tags`
  with a 302 elsewhere received the bearer token in full. The raw fetch now
  builds an opener that strips `Authorization` when the target origin differs,
  matching the SDK path; same-origin redirects keep it.
- `OLLAMA_HOST` is restricted to HTTP(S). `urlopen` honours whatever scheme it
  is given, so `OLLAMA_HOST=file://…` read a local path straight into
  `json.loads`.
- Scheme-less `OLLAMA_HOST` values work again. `urlsplit` reads the
  conventional `localhost:11434` as scheme `localhost`; a `<word>:<digits>`
  form is now treated as host:port.

## [0.5.0] — 2026-07-20

### Added

- **Batch embedding.** `LLMClient.embed_batch(texts, model=..., max_batch_size=None)`
  embeds many texts per provider round-trip instead of one request per text,
  returning a new `BatchEmbeddingResponse` (`embeddings` — one vector per input
  in input order, `model`, `dimensions`, `input_tokens` summed across requests).
  Measured on 32 chunks against a local Ollama server: 0.59 s batched vs 4.48 s
  looped (7.6×). `BaseProvider.embed_batch()` is a concrete default raising
  `NotImplementedError`, mirroring `embed()`, so third-party providers are
  unaffected; only Ollama overrides it. Batching is bounded — texts are sent in
  groups of at most `max_batch_size` (Ollama default:
  `DEFAULT_EMBED_BATCH_SIZE = 256`) so a large corpus does not become one
  enormous request; pass `max_batch_size=len(texts)` to force a single
  round-trip. Not atomic: if a later group fails, vectors already computed for
  earlier groups are discarded with the exception. A vector-count mismatch
  raises `ValueError`; request failure raises `ConnectionError` as before.
- Ollama `embed()` / `embed_batch()` now forward `**kwargs` verbatim to the
  ollama SDK (`truncate`, `options`, `keep_alive`); previously they were
  accepted and silently discarded, so `truncate=False` could not be set.
- **Thinking/reasoning support across providers.** `LLMResponse` gained an
  optional `thinking` field (appended after `tool_calls`, so positional
  construction is unaffected) carrying the model's reasoning trace separated
  from `content`. The `think` kwarg on `LLMClient.chat()` is now interpreted
  by every built-in provider, not just Ollama: `bool` toggles thinking, a
  `"low"`/`"medium"`/`"high"` string sets effort, an `int` sets a token
  budget. Ollama forwards `think` natively and extracts `message.thinking`;
  Anthropic enables extended thinking (`budget_tokens` clamped to
  `[1024, max_tokens - 1]`, sampling params omitted as the API requires) and
  extracts `thinking` content blocks; OpenAI-compatible providers send
  `reasoning_effort` for effort strings on reasoning models and extract
  `reasoning_content` / `reasoning` response fields, with an opt-in
  `<think>…</think>` content split for local servers that emit reasoning
  inline. Callers that never pass `think` see identical requests and
  untouched `content`. Known limitation: Anthropic thinking does not compose
  with multi-turn tool loops (thinking blocks are not round-tripped into
  follow-up requests) — see `docs/manual/llm.md` and ROADMAP.md.
- OpenAI-compatible providers accept an `extra_body` kwarg forwarded verbatim
  to the SDK, as the escape hatch for server-specific parameters (e.g. vLLM's
  `chat_template_kwargs`).

### Changed — breaking

- **Ollama embeddings moved to the `/api/embed` endpoint, changing vector
  scale.** `OllamaProvider.embed()` previously called the deprecated
  `/api/embeddings` endpoint, which returned **raw** vectors; it now delegates
  to `embed_batch()` and so uses `/api/embed`, which returns **L2-normalised**
  vectors. This keeps `embed(t)` and `embed_batch([t]).embeddings[0]` in
  permanent agreement — keeping the old endpoint for single embeds would have
  made them disagree in scale forever.

  Cosine similarity is scale-invariant and is unaffected. **Raw dot-product or
  Euclidean (L2) comparisons are affected**, and the failure is silent: mixing
  vectors stored before this change with vectors produced after it degrades
  retrieval quality with no exception and no warning. If your store uses a
  non-cosine distance metric, **re-embed the corpus**. Callers on cosine
  similarity need do nothing.

## [0.4.0] — 2026-07-19

### Changed — breaking

- **`bmlib.db.transaction()` no longer commits when joining an open
  transaction** (SQLite savepoint path). Previously, a `transaction(conn)`
  block entered while the connection already held uncommitted writes would
  call `conn.commit()` on success, committing the caller's pending writes
  along with its own. Now the block joins via a savepoint and the owner of
  the enclosing transaction commits. Code that relied on `transaction()` as
  a durability checkpoint after bare `execute()` writes must commit
  explicitly (or wrap the whole batch in an outer `transaction()`). The same
  applies to `run_migrations()` when called with a transaction already open.
  On PostgreSQL the old connection-wide commit behaviour is unchanged (no
  savepoint nesting is implemented there).
- **`bmlib.publications.sync()` buffers each day's records and stores them
  after the fetch.** The `on_record` callback now fires while the fetcher
  streams, *before* the record is stored — callbacks must not expect to read
  the record back from the database. Writes cost one commit per day instead
  of one per statement, and SQLite's write lock is no longer held across
  network I/O; in exchange, a day's records are held in memory during the
  fetch.
- `TransparencyAnalyzer._check_europepmc()` now returns a 6-tuple (adds
  `industry_coi`).

### Added

- transparency: industry conflict-of-interest detection in full-text
  COI/disclosure statements — negation-aware, scoped to the COI region, with
  a guard for non-industry contexts (university/government employment,
  editorial boards). ORs into `industry_funding_detected` at moderate
  confidence (#7).
- llm: embedding support in the LLM abstraction layer (`LLMClient.embed()`,
  `EmbeddingResponse`). Implemented by the Ollama provider; other providers
  inherit `BaseProvider.embed()`, which raises `NotImplementedError`.
- llm: tool calling — `LLMClient.chat()` accepts `tools` and `tool_choice`,
  with the new `LLMToolDefinition` and `LLMToolCall` data types,
  `LLMResponse.tool_calls`, and `LLMMessage.tool_calls` / `tool_call_id` for
  multi-turn tool conversations. Implemented for Anthropic, Ollama, and the
  OpenAI-compatible providers (OpenAI, DeepSeek, Mistral, Gemini). Passing
  `tools` to a provider that does not support them raises
  `NotImplementedError` before any network call. Ollama accepts but ignores
  `tool_choice` — its native API has no equivalent.
- llm: `supports_tools()` — public probe for the tool-calling allowlist, so
  callers can test support for a provider name or `"provider:model"` string
  without catching `NotImplementedError`.
- db: nested `transaction()` blocks on SQLite are now composable (savepoint
  join; the outer block owns the commit).
- llm: `bmlib.llm.json_repair` — repairs malformed LLM JSON (single quotes,
  trailing/missing commas, unescaped control chars, truncation, unquoted
  keys) via `repair_json()`, `safe_json_loads()`, `extract_and_repair_json()`.
  `BaseAgent.parse_json()` now uses it as a last-resort fallback. Ported from
  bmlibrarian.
- llm: `bmlib.llm.text_utils` — boundary-aware text chunking (`TextChunk`,
  `TextChunker`, `chunk_text`) that never drops text, plus map-reduce /
  rolling-summary long-document processing and document-text helpers. Ported
  and consolidated from bmlibrarian's two chunkers.
- quality: `bmlib.quality.cochrane_models` and `cochrane_formatter` —
  Cochrane-aligned nine-domain Risk-of-Bias models with judgement + rationale,
  the full study-characteristics table, and Markdown/HTML renderers. A strict
  superset of `BiasRisk`. Ported from bmlibrarian.
- quality: `bmlib.quality.extractors` and `scoring_models` — rule-based
  (LLM-free) study-type detection with exclusion-context guarding and
  sample-size scoring, producing `DimensionScore` audit trails. Ported from
  bmlibrarian's paper_weight.
- fulltext: `bmlib.fulltext.pdf_converter` — pluggable PDF→text conversion
  (`ConversionResult`, `PDFConverter`, `get_converter`, `list_converters`)
  with a PyMuPDF backend behind the new optional `bmlib[pdf]` extra. Ported
  from bmlibrarian.

### Fixed

- transparency: a JATS-tagged COI section now counts as `coi_disclosed=True`
  even when its wording contains no cue phrase — the tag is structural proof
  of a disclosure; the cue-phrase scan remains the fallback for untagged text
  (#13).
- llm: `list_models()` on the Anthropic and OpenAI-compatible providers now
  returns a copy of the cached model list; mutating a returned list no longer
  corrupts the cache for subsequent callers (#12).
- publications: batched database commits — one commit per stored publication
  and one per synced day instead of one per statement (#8).
- llm: `get_llm_client()` singleton creation is now thread-safe; the
  openai-compat `list_models()` caches a successful-but-empty response for
  the TTL instead of re-hitting the API every call; the Anthropic provider
  warns (once per model per instance) when an unknown model id falls back to
  estimated pricing (#9).
- fulltext: `FullTextCache` sanitizes identifiers internally, so a raw DOI or
  path-traversal string cannot write outside the cache directory;
  already-safe identifiers keep their exact filenames (#9).
- publications: the OpenAlex fetcher tolerates a `"meta": null` page instead
  of raising `AttributeError` (#9).
- agents: `chat_json()` now fails fast with the real cause when a response is
  truncated at the `max_tokens` ceiling, instead of reporting a generic
  "unparseable response". At `temperature == 0.0` it raises immediately —
  greedy sampling reproduces the identical truncation, so retrying only pays
  for it again; above 0.0 it retries, since a different sample may fit. A
  response that is complete JSON despite hitting the ceiling is returned
  rather than rejected. Truncation detection covers Anthropic's
  `stop_reason="max_tokens"` and the OpenAI-compatible `"length"`, and empty
  responses are now treated as retryable transport errors.
- fulltext: cache keys are now `{sanitized}_{sha1[:10]}`, so DOIs that
  differed only in characters the sanitizer collapsed (for example
  `10.1/a:b` and `10.1/a/b`) no longer share a cache file and serve each
  other's full text.
- fulltext: JATS parsing no longer drops abstract sections, mislabels table
  headers, or loses figure and table captions.
- fulltext: the final fallback result is labelled `source="pubmed"` rather
  than `"doi"` when it resolves to a PubMed URL.
- db: `create_tables()` no longer uses SQLite's `executescript()`, whose
  implicit `COMMIT` broke a surrounding `transaction()` block and left
  migrations non-atomic. Statements are split and executed individually.
- llm: provider names are normalised to lowercase in client routing, so
  `"Anthropic:claude-..."` resolves like `"anthropic:claude-..."`.
- llm: JSON extraction handles responses containing multiple objects and
  braces inside strings.
- llm: OpenAI reasoning models receive `max_completion_tokens` instead of the
  rejected `max_tokens`.
- llm: the Ollama provider no longer clobbers a legitimate zero token count
  when recording usage.
- quality: the Tier 1 metadata filter no longer misclassifies study designs
  from ambiguous PubMed publication types, and `QualityAssessment` records
  `is_randomized` from the new `DESIGN_TO_RANDOMIZED` mapping, so
  `QualityFilter.require_randomization` recognises a Tier 1/2 RCT instead of
  rejecting it.
- transparency: conflict-of-interest detection and the ClinicalTrials.gov
  posted-results check were both under-detecting — the latter requested
  `ResultsSection` but read `resultsSection`. The analyzer now returns an
  `UNKNOWN` risk level with score 0 when no external API was reachable,
  rather than letting an all-zero score read as HIGH risk.
- publications: full-text sources are no longer silently dropped during sync.
- publications: the bioRxiv fetcher records the correct PDF version, and the
  PubMed fetcher handles non-numeric month names in publication dates.
- publications: `fetch_pubmed()` now populates `publication_types` from
  `PublicationTypeList`. It never did, yet the free Tier 1 quality filter
  classifies study design from exactly that field — so every synced PubMed
  record skipped the free tier and fell through to the paid LLM classifier.
- publications: `register_source()` now registers the built-ins before
  writing its entry, so registering under a built-in name actually overrides
  it. Previously an override installed before the first lookup was silently
  reverted the moment lazy registration ran.
- publications: the three built-in fetchers annotated `on_record` as
  `Callable[[dict], None]` while passing a `FetchedRecord`; the annotations
  now match the behaviour, which is unchanged.
- transparency: `TransparencyAnalyzer` is now safe to share across threads,
  which is what makes `settings.max_concurrent_analyses` usable. Rate-limit
  state is mutex-guarded (the interval throttles a shared remote API, so it
  must apply across threads); reachability is held per-thread, since it
  describes a single analysis. Previously two concurrent `analyze()` calls
  contaminated each other: a thread whose APIs were all down inherited a
  concurrent thread's success and was scored 0 / HIGH instead of UNKNOWN,
  wrongly triggering a tier downgrade.
- transparency: `settings.enabled` is now honoured. `enabled=False`
  short-circuits `analyze()` before any HTTP — and before the `httpx` import,
  so a disabled analyzer does not require the optional extra. It was
  previously ignored and analysis ran regardless.
- transparency: `TransparencyResult.to_dict()` now round-trips
  `full_text_analyzed`. Dropping it made a persisted `coi_disclosed=False`
  uninterpretable, since that value only means "scanned and absent" when the
  full text really was read.
- transparency: removed the unreachable `resultsSection` fallback in
  `_check_trial_results()`. The request is narrowed to `fields=hasResults`,
  so no other key can come back; the fallback implied a robustness it could
  not provide.
- db: `create_tables()` now parses `CREATE TRIGGER ... BEGIN ... END;`.
  Splitting on the semicolons inside a trigger body handed SQLite a fragment
  and raised `OperationalError: incomplete input`. Nesting counts
  `BEGIN`/`CASE` against `END`, so a `CASE ... END` inside a body does not
  close it early and a bare `BEGIN;` is not mistaken for one.

### Documented

- transparency: `TransparencySettings` now states which fields the analyzer
  honours and which are orchestration hints for the calling application
  (`filtering_enabled`, `max_concurrent_analyses`, `cache_results` — the
  library analyses one document per call and does no filtering, threading,
  or caching of its own).
- transparency: `outcome_switching_detected` is documented as reserved and
  always `False`. Deciding it means comparing a trial's pre-registered
  primary outcomes against those reported; it is kept in the schema so
  persisted results need no migration when detection lands.

## [0.3.0]

Never released. The version string was bumped in-tree when embedding support
landed, but no release was cut; those changes ship as part of 0.4.0 above.

## [0.2.1] and earlier

No changelog was kept; see the git history.
