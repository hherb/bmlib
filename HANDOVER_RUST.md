# HANDOVER — the Rust port of bmlib

_Last updated: 2026-09-29 (round 64). **`bmlib` 0.2.0 is published** — 2026-09-28T04:16Z, from
`0efd488`, the merge of PR #362 — and the port is functionally complete. **Round 63 landed #356**
(PR #399): the port follows Python's PR #355 on all three `fulltext` decisions — a caller PMC ID
that fails is superseded by the Europe PMC search hit's, a cached PDF keeps the abstract its
retrieval returned in an `abstracts/` sidecar, and the cache key's pass-through bound is the
longest sanitized key (171) — and the cache corpus's last `corrected` block is retired, Python
having adopted the port's side of #309 part 1. **Round 64 then pinned the four open JATS defects
as reproductions** (#385, #393, #396, #397): the JATS corpus goes **60 → 68 documents** and the
service corpus **69 → 71 cases**, with `QUIRK:` comments at the four sites and five named tests,
so Python's fix arrives as a changed expectation the port must follow.

**`origin/main` is `47d3a29`** — the merge of PR #399, round 63's landing; round 62's Python tip
was `296381b` (the merge of PR #394). `296381b..47d3a29` moves **no Python file**. Rounds 52-59
landed through **#380**
(`fix/rust-land-rounds-52-56`), which merged the two stranded branches — `fix/rust-doc-links`
(#371-#374, #378) and `fix/rust-oracle-in-ci` (#377, whose own branch is deleted upstream) — so
**nothing is stranded any more**, and the check that found it stands: `gh pr list --state merged`
reports a *stacked* PR merged whether it landed in `main` or in the branch beneath it, so check
ancestry with `git merge-base --is-ancestor <branch> origin/main`. **Fetch before trusting the
ref.** The next release is a **0.3.0** (#371 changes the `Fetcher` trait).

The Python library was **not modified** by the port — `git status --porcelain bmlib/` is
empty, and that is the state to preserve. The Rust crate is released; **0.1.1 was a plan and
not a release**, and 0.2.0 carries what `main` held on release day.

**Read [`rust/README.md`](rust/README.md) first for how to build and run it, and
`docs/plans/2026-09-26-rust-port-roadblocks.md` §0 and §9 for the fidelity contract
and the divergence register.** This file is the one that says what is *left*, and
what will bite you.

## Where it stands

| | |
|---|---|
| Tests | **979 passing, 0 failing** on this branch: **987** `pdf`, **989** `postgres` (whose 10 extra tests are the live suite and **skip** unless `BMLIB_PG_TESTS=1`), **997** `--all-features`. Every figure from a **clean worktree** — see the gotchas |
| Lint | `cargo clippy --all-targets --all-features -- -D warnings` **0 warnings**; `cargo fmt --check` clean; **`cargo doc --no-deps --all-features` 0 warnings** with `RUSTDOCFLAGS=-D warnings`, which CI runs as a step; `ruff check .` clean |
| Size | 77,270 lines of Rust — 78 source files, 72 test files |
| Oracles | **41** `oracle/dump_*.py` drivers, **3,224** committed case entries (the sum of the case files `rerun_rust_oracle.py`'s `CORPORA` reads; the `funder_matcher` corpus is 417 funder names counted separately), **+7** from round 63. **All 41 regenerate and match** as of round 64 — re-run them with `scripts/rerun_rust_oracle.py`, which CI runs as a step |
| Python | untouched |

Build and test:

```bash
cd rust
CARGO_HOME="$PWD/.cargo-home" cargo test          # a sandbox that denies ~/.cargo
cargo test --features pdf                          # + 8 PDFium tests over real PDFs
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1   # 6 live requests
BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live    # 10 against a real server
```

**The network command needs `--test-threads=1`**: NCBI rate-limits by source address,
so a concurrent run draws 429s that read as parse failures. The live suite is
**gated** — the default `cargo test` opens no socket. Without the variable its six
tests each return immediately, so the binary reports `ok. 6 passed` in ~0.09s; the
count is the same either way, which is deliberate (a suite that silently
disappeared would be worse than one that runs). The PostgreSQL suite is gated the
same way and needs no `--test-threads=1`, because each test creates its own
database.

## Publishing to crates.io

**`bmlib` 0.2.0 is published** (2026-09-28T04:16Z) from `0efd488`, the merge of PR #362 and
the last merge commit on `main` when it went out; `.cargo_vcs_info.json` records that commit
and no `dirty` flag, which is what publishing from a clean worktree of `main` buys. **0.1.0**
is published (2026-09-27) from `677d545`, the merge of PR #336. The crate is `rust/bmlib` and
the name was free.

**The published crate has been consumed, not just packaged**: a separate crate depending on the
registry's `bmlib = "0.2.0"` fetched it, compiled it and called
`fulltext::cache::sanitize_identifier` on two identifiers, matching this repository's Python
byte for byte (`10.1234_jbr.2024.001_21e540af57`, `PMC7614751_158cdf8b74`). `cargo publish`
compiling the packaged tarball is a different claim from the registry's copy being usable by a
dependent, and only the second is what a user experiences.

**The token link is removed again**, as it is after every release; remaking it is the first step
of the next one. The procedure above is otherwise unchanged, and the one thing this release added
to it is a lesson: `gh pr list --state merged` says a *stacked* PR merged whether it landed in
`main` or in the branch under it, so **check ancestry, not state** —
`git merge-base --is-ancestor <branch> origin/main` — before treating a release's prerequisites
as met.

**0.2.0 exists because 0.1.0 is wrong, not because anything was added.** Three commits
landed after the release commit — `6424410` and `ca14621` (09:24 and 09:33) and
`1430af6` (09:44), against a release cut at 08:48 — so crates.io's 0.1.0 still carries
the quality-reader defects described in the round-43 note below. Those fixes ship
together with the three changed `fulltext::cache` signatures.

**0.1.1 was a plan, not a release, and it was deliberately skipped.** It was to carry the
quality-reader fixes on their own so a caller pinned to `0.1.x` would get them from a
patch. Nobody is: the crate had been public for hours and the fixes were to be superseded
by 0.2.0 the same day, so a separate patch meant a second merge-and-publish cycle and a
changelog heading for a version nobody could install. Its entries are folded into 0.2.0.

The version literal is `rust/Cargo.toml`'s `[workspace.package] version`, and what moved
is written up in `rust/bmlib/CHANGELOG.md`, which the crate now ships.

Cargo reads the token from `$CARGO_HOME/credentials.toml`. Pointing `CARGO_HOME`
inside the workspace — which a sandbox that denies writes to `~/.cargo` requires,
and which `cargo publish` needs twice while it updates the index — therefore
hides the token that lives in `~/.cargo`. Link it rather than copy it, so the
secret keeps one home:

```bash
ln -sfn ~/.cargo/credentials.toml rust/.cargo-home/credentials.toml
```

`.cargo-home/` is gitignored, so the link never enters the repository. It is
**removed after a release** and has to be remade before the next one; leaving it
in place is a standing grant nobody asked for.

```bash
cd rust
CARGO_HOME="$PWD/.cargo-home" cargo publish --dry-run -p bmlib   # review, then:
CARGO_HOME="$PWD/.cargo-home" cargo publish -p bmlib
```

`--dry-run` needs no token, so the packaging step is reviewable before the link
exists. It is also the step that catches a `CHANGELOG.md` or `README.md` the crate
does not carry.

**A release cannot be pushed straight to `main`.** The `protect_main` ruleset
requires CodeQL results *for the exact commit*, and CodeQL here is GitHub's
default setup: there is no workflow file for it in `.github/workflows/`, and it
analyses pull requests, not direct pushes to a protected branch. A new commit
pushed at `main` is declined with *"push declined due to repository rule
violations"* while `git push --dry-run` reports a clean fast-forward, because the
rule is enforced on receive rather than on the probe. Land the release as a PR,
let CodeQL run, merge, and publish from the merge commit. That is the sequence
0.1.0 went through, and it is why the crate's `.cargo_vcs_info.json` names the
merge commit and carries no `dirty` flag.

## Session note (round 62) — two Python JATS landings had no Rust counterpart, and the oracle that said otherwise was green and hollow

**The scope was round 61's question asked again of `origin/main`: which Python change since the
port's last landing has no counterpart here?** Two PRs, and the round had to **fetch first**:
the local `origin/main` ref still pointed at `fab0612` (PR #392), while `gh pr list` reported
PR #394 *merged*. A `git fetch` moved `origin/main` to **`296381b`**, the merge of #394, and
the two landings below were both missing:

- **[#388](https://github.com/hherb/bmlib/issues/388)** — PR #387 (`fca0f71`): a `<name>`
  deposited directly in a citation is a cited author; a mononym `<name>` (`<given-names>`
  alone) is its own author and flushes at `</name>` only; **#276**'s rule that a reference
  naming no work prints its deposit however many components it has; and **#264**'s scope for
  the zero-author detector — the article's own contributor list, not the whole of `<front>`.
- **[#395](https://github.com/hherb/bmlib/issues/395)** — PR #394 (`296381b`): NLM 2.x's
  `<citation>` is a reference, `<nlm-citation>` joins the element-only spellings, and a
  `<citation>` is read **by its deposit** — it writes its string only where it carries typeset
  text of its own (directly or in an `<x>`), which PMC's element-only deposits (1,124,468 of
  1,155,505 served) do not.

The port had none of it: `finish_current_author` took no `closes_a_name`, `CITATION_ELEMENTS`
held two spellings, `defers_to_the_deposit` had no work-naming rule, and the name arms and
counter were gated on `in_front`. `rust/bmlib/src/fulltext/jats_reader.rs` and `service.rs` are
the change; `jats_text.rs`'s restated locator-spelling set gains `citation`.

**All 41 oracles were clean before the change, and that was the round's own trap.** The JATS
corpus was **43 documents** and not one reached a cited `<name>` outside a `<person-group>`, a
mononym, a two-component citation that names no work, or an NLM `<citation>` at all — so the
port and its committed expectation agreed with each other and disagreed with the library, which
is #349's shape (round 47/48) reached from the other side. The corpus is now **60 documents**,
and which of the 17 added cases is which was measured rather than assumed: **9 move** against
the pre-#387/#394 Python (`scripts/diff_jats_parser.py`'s trick run over the corpus with the old
`jats_parser.py` loaded as a standalone module via `dump_jats.py`'s own renderer) and **6 do
not**, kept for that — the split editor across two `<person-group>`, a bare `<string-name>`
outside a group, `source`+`year` and `doi`+`year` which still name the work, and an
element-only `<citation>` alternative that must not disturb the mixed sibling. Two more were added
to **pin an open Python defect the port reproduces**: #391, a `<mixed-citation>` or
`<element-citation>` printed in prose being cut out of the sentence
(`prose/391-a-mixed-citation-in-a-paragraph-is-cut-out` and its element sibling), asserted by
`a_citation_in_prose_is_cut_out` and flagged with a `QUIRK:` at the merge site — a reproduction in
the corpus is what makes Python's fix force the port to follow, as #382's did. The service
corpus went **67 → 69**: one case reaching #276's deposit rule through the real
`render_jats_html`, one its boundary. Five named tests in `tests/jats_reader.rs` state the
rules, the zero-author one through `parse_audited`'s warning lines, since the counter is not a
field the JATS oracle renders.

**The port now follows Python on all four decisions**, including the two that are measurements
rather than rules — the `display-unstructured` part filling only an identifier the first part
left empty, and a locator join made provisionally across whitespace being undone when typeset
text arrives before the close (both from PR #394's own review). No Python file was modified:
`git status --porcelain bmlib/` is empty.

**Gates:** `cargo test` **952 passing, 0 failing** (960 `pdf`, 962 `postgres`, 970
`--all-features`), `cargo clippy --all-targets --all-features -- -D warnings` 0 warnings,
`cargo fmt --check` clean, `cargo doc --no-deps --all-features` with `RUSTDOCFLAGS=-D warnings`
clean, and **41 of 41 oracles regenerate and match**.

**One gotcha re-earned this round:** a *stacked* PR's state is not its ancestry, and neither is
a stale remote-tracking ref. `gh pr view 394` said `MERGED` with merge commit `296381b` while
`git merge-base --is-ancestor 296381b origin/main` said no — because `origin/main` had not been
fetched. Fetch before trusting the tip; the merge-commit id from `gh` is the cheap cross-check.

## Session note (round 64) — the four open JATS defects are pinned as reproductions, and the corpus is what makes Python's fix force the port to follow

**The scope question came back empty again.** `296381b..47d3a29` (round 62's Python tip to
`main` after round 63's landing) moves **no Python file**: `831a365` is the merge of PR #398 and
`47d3a29` the merge of PR #399, both Rust-only. So the round took the handover's own next item —
the four open JATS issues (#385, #393, #396, #397) — whose work is **reproduction, not repair**:
they are Python defects the port mirrors, and a case in the corpus is what makes Python's fix
arrive as a changed expectation the port must follow, the mechanism #382's and #391's cases
already use.

**Every fixture was measured against the live Python before it entered the corpus**, and the port
then agreed on all 65 documents:

- **#385 — a fourth author doubles the period.** `M Ahamed, M Karns, et al.. DNA damage response.
  Toxicol Appl Pharmacol. (2008)`: `et al.` ends in a period and both renderers join with `". "`.
  `cited/385-a-fourth-author-doubles-the-period` and
  `build_html/ref_four_authors_double_the_period` pin the two renderers;
  `cited/385-exactly-three-authors-do-not` pins the arm boundary, since exactly three authors take
  the other arm.
- **#397 — a declared six-digit PMID is refused.** The `<pub-id>` arm classifies by shape
  (`isdigit` and at least seven digits, or a `10.` DOI) and ignores `pub-id-type`, so `138412` is
  dropped where `14163512` is read. One case carries both, refused beside read.
- **#393 — an element-only citation no field reads renders nothing.** A web reference's address is
  an attribute and its only text a `<comment>`, so `formatted_citation` is `""` and the HTML list
  gets `<li id="ref-r2"></li>`. Pinned in the reader corpus and, through the real
  `render_jats_html`, in the service corpus.
- **#396 — a book's edition, publisher and comment reach no field and no counter.** The reference
  renders `RN Proctor. The Nazi War on Cancer. (1999)` and the rest is dropped silently, where the
  module's standing rule for a drop it argues for is to count and report it once per article. #393
  is the subset where *nothing* renders.

**The corpus grows 60 → 68 JATS documents and 69 → 71 service cases**, four named tests are added
to `tests/jats_reader.rs` (15 → 19) and one to `tests/service.rs` (62 → 63), and `QUIRK:` comments
now sit at the four sites that cause the behaviour: `format_ref_html`'s `et al.` join, the
`<pub-id>` arm, and the `CITATION_ELEMENTS` close arm that reads an element-only citation's
children and silently drops the ones no field covers. The oracle's own
`formatted_citation` transcription carries the #385 note too, since a Python fix arrives there
first.

**A review pass over the PR found two things, and both are fixed in it.** The first cut rewrote
the two case files with the dumper's `indent=2` where the committed ones use `indent=1`, so
**3,564 changed lines of pure reformatting** — both files rewritten end to end — buried six added
cases; the diff against `main` is now **76 added lines and 2 deleted**. The second was **coverage**: the issues name shapes the first cut did not reach,
so a declared six-digit PMID is now refused in a `<mixed-citation>` too (where the number
survives inside the typeset string the field refused), and #393's conference-only and
`<comment>`-holding-the-reference shapes are pinned beside the web-reference one. The #396 test
also asserts what the issue claims about the *audit*: the report's diagnostics **and** its
counted-loss warnings are both empty for that document, so nothing counts the drop and no line is
logged.

**Gates:** `cargo test` **979 passing, 0 failing** (987 `pdf`, 989 `postgres`, 997
`--all-features`, all from a clean worktree), `cargo clippy --all-targets --all-features -- -D
warnings` 0 warnings, `cargo fmt --check` clean, and **41 of 41 oracles regenerate and match**.

**No Python file was modified**: `git status --porcelain bmlib/` is empty.

## Session note (round 63) — #356: Python's three `fulltext` decisions, the PR review's fourth, and one defect of the port's own

**The scope was round 62's question asked again of `origin/main`: which Python change since the
port's last landing has no counterpart here?** The answer was *none* — `296381b..831a365` is the
merge of round 62's own PR #398 and moves no Python file — so the round took the top of *What is
left*: **[#356](https://github.com/hherb/bmlib/issues/356)**, the last behavioural gap, which is
Python's **PR #355** (`07f4fb3`, `ae080da`, `bc85c63`, `c6f381e`; merged as `86ef0e2`).

**The issue names three decisions and the PR's own review added a fourth — and one of the four
was a defect in this port rather than a missing feature**, which is the round's finding, because
the handover had described only the first three:

- **A caller's PMC ID (#304).** The port is restructured onto Python's shape: `try_pmc_id` runs
  **Europe PMC then NCBI for one ID**, and `discover_pmc_id` runs the search and, when it found
  none *and no usable caller ID failed*, the ID Converter. A malformed caller ID is normalised,
  recorded as a `FullTextError` fault and treated as absent — so discovery runs in full, the
  converter included — where the port had fetched Europe PMC with the raw id and recorded
  nothing. A well-formed caller ID that neither source serves is **superseded by the search
  hit's**, which the port had explicitly refused (round 40's
  `a_well_formed_caller_pmc_id_still_suppresses_the_discovery_search` pinned the old side and is
  rewritten). The pre-#355 extra
  "XML failed, search again for a PDF URL" step is deleted: `discover_pmc_id` now always runs
  when there is a DOI or PMID, so it is subsumed.
- **A superseded ID's abstract gives way to the superseding one's**, while an earlier tier's
  keeps first-wins. Python's `abstract_only is caller_abstract` identity test is a flag here
  (`abstract_came_from_caller`), which is exact because nothing between the caller's tier and the
  discovered one touches `abstract_only`; the reasoning is on the declaration.
- **A cached PDF's abstract (#305)** lives in an `abstracts/` sidecar now: `FullTextCache` gains
  `abstract_dir`, `save_abstract` and `get_abstract`, and `delete`, `quarantine` (HTML, PDF,
  abstract — Python's `_entries` order) and `clear` (which skips an absent directory) cover it.
  `check_cache` reads it back only on a PDF hit that yields no text. **The port's own round-39
  correction was wrong and is reverted**: it treated a text-less PDF hit as a *miss* and re-ran
  the chain, which the maintainer rejected because every PDF hit becomes a network re-fetch for a
  `convert_pdfs = false` caller. Python's fix is the sidecar alone; a text-less PDF with no
  sidecar is a hit with `content_kind = none` and `file_path`.
- **The cache key (#309 part 1)** — `safe_filename`'s pass-through bound moves from
  `MAX_PREFIX_CHARS` (160) to the new `MAX_KEY_CHARS` (171, the longest key `sanitize_identifier`
  returns), so the service's key is never hashed twice. The cache corpus's **last `corrected`
  block** (`safe_filename/161`) is retired, and its companion test now asserts the list is empty;
  `safe_filename/171` and `/172` are added, and a named test pins the property directly —
  `safe_filename(sanitize_identifier(raw)) == sanitize_identifier(raw)` for raw lengths from 150
  to 400.
- **A malformed remote `pmcid` is the fourth site, and it is Python's review fix rather than a
  port defect** — the port reproduced pre-#355 Python, which let the raw value through and raised
  `AttributeError` inside `fetch_europepmc` for a non-string one. `resolve_pmc_id_and_pdf_url`
  returned the search hit's `pmcid` raw, so a *bare numeric* id did not compare equal to the
  caller's prefixed one and a malformed one reached both fetch helpers — two faults and no
  converter request. It is validated where it is read now: normalised, or a WARNING, a recorded
  fault and `None` so the converter is asked. The new `normalise_pmc_value` is Python's
  `_normalise_pmc_id(object)` for a decoded JSON value.

**The `abstracts/` directory is created by the first save, not by construction** — the PR's
follow-up `bc85c63` — because a cache an earlier version built, possibly read-only, must still
construct. `clear` skipping an absent subdirectory is that commit too, and the failed-sidecar
warning has its **own** one-shot key (`abstract-write:<kind>`), so it cannot spend the cache-write
key and silence a later directory-wide fault of the same type.

**The corpus work was small and the named tests carry the round**: the cache corpus goes 31 → 33
cases and regenerates against the live Python; the service corpus is unchanged (round 62 took it
67 → 69) because the tier chain is driven by a scripted `HttpClient` and its assertions are named
tests. Fifteen service tests and seven cache tests are added, two service tests are rewritten to
the new behaviour, and the cache test that named the register's one correction now asserts there
is none. The plan's §9 `_safe_filename` row is retired rather than kept, and the Appendix's #304,
#305 and #309 rows now record Python's adoption; the "Three need a decision" passage says two are
now settled.

**A review pass over the new branches found four unpinned ones, and each new test was
mutation-checked.** The port's own `check_cache` branch for an *undecodable* abstract, the
`truthy` guard on the search's `pmcid`, the `result.file_path` gate on the sidecar write, and the
`if result.html` guard that keeps extracted text from being displaced by an abstract could each
be deleted with the whole suite green; they are pinned now by
`an_undecodable_abstract_on_a_pdf_hit_is_quarantined_and_the_chain_runs`,
`a_falsy_search_pmcid_is_not_a_fault`, `no_abstract_sidecar_is_written_when_no_pdf_was_cached`
and `a_pdf_hit_with_text_ignores_the_abstract_sidecar`, and by two cache tests that assert the
`quarantine`/`delete` entry order and set. Six mutants (each branch disabled in turn) redden
exactly those tests, and the bound reverted to `MAX_PREFIX_CHARS` reddens four more, the oracle
test among them. **One test was wrong on its first cut**: the new raw-identifier lookup test
wrote by the *raw* identifier, which passes through as itself under the bound and is a different
question from the one #309 asks — Python's version writes by the sanitized key and reads by the
raw DOI, and the test now does the same.

**The Python PR's own test set was then mapped onto the Rust suite one test at a time**, which
found three more behaviours the port had no named test for — an identifier ending in `abstract`
must not collide with the `abstracts/` directory, the longest name must still fit `NAME_MAX`
with the temporary affix, and a stale caller ID must still find the free PDF the search offers —
and extended the sanitizer's pass-through property over Python's own length parametrisation (1,
149, 150, 151, 160, 161, 171, 172, 400). Its
`test_a_caller_id_of_the_wrong_type_is_treated_as_absent` has no Rust counterpart by
construction — `FullTextRequest::pmc_id` is `Option<String>`, so a non-string caller id is
unrepresentable, which is the port's usual answer to a dynamic type.

**No Python file was modified**: `git status --porcelain bmlib/` is empty.

**Gates:** `cargo test` **974 passing, 0 failing** (982 `pdf`, 984 `postgres`, 992
`--all-features`, all from a clean worktree), `cargo clippy --all-targets --all-features -- -D
warnings` 0 warnings, `cargo fmt --check` clean, `cargo doc --no-deps --all-features` with
`RUSTDOCFLAGS=-D warnings` clean, `BMLIB_LIVE_TESTS=1 cargo test --test live_network --
--test-threads=1` **6 passed** (69s), and **41 of 41 oracles regenerate and match**.

**One method note re-earned:** the first count of the default configuration came out **963**,
because it ran while another `cargo test --all-features` still held the shared `target/`.
Re-measured in a clean `git worktree` the same tree is **967**, and the final tree **974** —
round 62's 952/960/962/970 plus exactly the **22** tests this round adds (seven cache, fifteen
service).
The gotcha in the list below is the reason the worktree exists.

## Session note (round 61) — #382's own shape has no population, the instrument that said so had to be rewritten, and two Python defects came out of the rewrite

**The scope was round 60's question asked again of `origin/main`: which Python change since the
port's last landing has no counterpart here?** The answer is *none*. `d481cab..origin/main` moves
exactly one Python file, by five lines added and one removed, and it is a **docstring** — #266's
measured population note inside `jats_parser.py`, no behaviour. So the round went after an open
Python JATS defect that asked for a population and that the corpus did not reach:
**[#382](https://github.com/hherb/bmlib/issues/382)**, a structured `<name>` printed in prose
losing its `<surname>`/`<given-names>` from the sentence.

**The issue asks for a population before a fix is chosen, and the honest answer needed the
question split** — by the fate the parser gives each name, not by the issue's own phrasing, which
pools unrelated defects. `scripts/measure_jats_prose_names.py` classifies every element holding a
`<surname>`/`<given-names>` (a `<name>`, or a `<string-name>` depositing its parts) into one of
eleven contexts, each with one fate — read, kept, glued (#314) or dropped — decided in the parser's
own order, and skips `<sub-article>`/`<response>` regions as the parser does. Over the four
artifacts round 60 used, **136,570 documents, all of which parse**, against the parser as it
stands after PR #387 (which made a `<name>` deposited directly in a citation an author, so the
three citation rows are not the pre-#387 figures the #382 comment first carried):

| context | fate | served (8,118) | PMC000 (3,028) | PMC001 (27,515) | PMC012 (97,909) |
|---|---|---|---|---|---|
| citation-author | read | 1,393,877 | 57,705 | 869,924 | 22,483,560 |
| contributor-own | read | 56,539 | 13,547 | 136,906 | 757,254 |
| mixed-citation, not an author (#314) | glued | 3,787 | 0 | 0 | 67,337 |
| element-citation, not an author | dropped | 2,624 | 3 | 0 | 40,474 |
| NLM `<citation>` (**#390**) | dropped | 0 | 358,132 | 2,822,441 | 0 |
| citation outside a `<ref>` (**#391**, #255) | dropped | 1,708 | 0 | 0 | 28,020 |
| related work in `<article-meta>` | dropped | 4 | 17 | 163 | 56 |
| **contributor prose (#382)** | dropped | **0** | **0** | **0** | **0** |
| other — every one a `<principal-award-recipient>` (#288) | dropped | 2,912 | 0 | 0 | 26,382 |

(`table-cell` and `related-work-in-prose`, both kept, are 0 everywhere.) **#382's own shape is 0**:
no name sits in contributor prose, and every `other` name is a funding `<award-group>`'s
`<principal-award-recipient>` — #288, an **open defect**, not a container the reader declines on
purpose. PR #387 carries #382's closure on that measurement and on the Tag Library, which admits `<name>` in
no `<p>`. **But prose is not free of lost names**: a citation printed in a paragraph is cut out of
the sentence whole, names included — 31 served and 387 PMC012 articles beyond #255's front-matter
shape, now #391.

**The first cut of the instrument got every label but #382's own wrong, and PR #389 merged it.**
Its `classify()` counted `<citation>`/`<nlm-citation>` as citation elements (the parser reads
neither, so 3.18M back-file names it filed as *read* reach no field — #390), glued any citation's
bare name (only `<mixed-citation>` merges, so 538,631 `<element-citation>` names were *dropped*,
not #314's — the population PR #387 then made authors), and never asked for a `<ref>` (so a citation in prose read as a reference author). The
review ran after the merge; the correction is its own branch. **What stops a repeat is the test,
not the rewrite**: `tests/test_prose_name_sampler.py` parses one fixture per context with the
real parser and holds each context's fate to whether the surname survived anywhere in the
article, and each of the first cut's mistakes — plus #149's first-wins, #270's related-work
refusal, the cell route and the suppression — reddens it when put back (11 mutants, all killed).
The script restates the parser's sets and imports nothing from `bmlib`; the agreement lives in the
test. It also stopped reporting in silence: it splits a bundle on `<article[\s>]` and checks the
split against an independent count of roots, keeps an unparsed document out of the denominators,
counts skipped archive members, prints every dropped path (or a remainder line), walks
iteratively, and exits non-zero when a count is not reportable. The #382 comment carries the
corrected table.

**The port reproduces the defect, and the gap was that nothing said so.** #382 is outside the
plan's enumeration of defects the port corrects (§0), so the port was already faithful — it drops
the name exactly as Python does. What the round added is the record, three ways:
`prose/382-a-name-in-a-body-paragraph-is-lost` joins the JATS corpus (**42 → 43 documents**, and
the anti-vacuity count with it), `a_name_in_body_prose_is_lost` asserts the dropped sentence and
the `<string-name>`/`<collab>` inline boundary beside it, and a `QUIRK:` at the `surname` arm
names the discard and the issue. **If Python fixes #382 the oracle re-run goes stale on that case
and forces the port to follow** — which is the reason to pin a reproduction in the corpus rather
than describe it in prose. The review found the test's inline half decided nothing new: in a `<p>`,
`contrib_owns_name` already refuses, so deleting `!self.contrib_stack.is_empty()` from
`is_owned_name` kept all 947 tests green. It now asks the same two spellings in a section `<title>`
and a reference's `<mixed-citation>`, where that term alone keeps the name, and the mutant fails
it. The `QUIRK:` also over-claimed — a `<mixed-citation>` or related work merges the text, and a
table cell keeps it — and says so now.

**The bioRxiv prose PR #362 claimed to fix but did not.** #362's own body says *"the doc comment
keeps the history in one place"*; the only file it changed is `tests/live_network.rs`, so the
**fetcher's** doc comment still asserted `/details` answers a zero-byte body, which its own PR had
just measured false. Re-probed by hand on 2026-09-29 (no committed instrument takes these;
the gated live suite covers 2024-01-15 only): **eight of eight** combinations (bioRxiv/medRxiv ×
2024-01-15/2025-06-01, twice each) served JSON, and all six URL shapes the Python docstring calls
empty answer 200 — the date-interval shape `biorxiv.py` itself builds with 64,657 bytes for
bioRxiv and 75,608 for medRxiv. `BASE_URL`'s doc now records the restoration and says why the
choice stands.

**Four Python-side findings are filed or recorded, and no Python library file was modified**
(the instrument and its test are `scripts/` and `tests/`). #390 and #391 are above. The stale
Python docstring is **[#386](https://github.com/hherb/bmlib/issues/386)** — `biorxiv.py`'s module
and `BASE_URL` docstrings still state, in the present tense, that `/details` is dead, which is the
premise #325's option-2 decision rests on and which #341's population gap inherits. The population
measurement is commented on #382, and corrected there after the review. `git status --porcelain
bmlib/` is empty.

**Five stale-open Rust issues were closed, each re-verified against `origin/main` before it was
touched.** #354, #359, #361 and #365 were fixed by #360, #363, #364 and round 51's PR, and #366 by
round 59 — yet GitHub closed none of them, because their numbers sat in *stacked* PR bodies rather
than after a closing keyword. That is the same failure PR #383 cleaned up on the Python side, and
it is why *What is left* below no longer repeats the old claim that they closed with their merges.
**#332** stays open for 0.3.0, as its own note says, and **#356 is genuinely open** — the one
behavioural gap the port still carries, now first on the list.

**Gates on this branch, all clean:** `cargo test` **947 passing, 0 failing** (955 `pdf`, 957
`postgres`, 965 `--all-features`), `cargo clippy --all-targets --all-features -- -D warnings` 0
warnings, `cargo fmt --check` clean, `cargo doc --no-deps --all-features` with
`RUSTDOCFLAGS=-D warnings` clean, **41 of 41 oracles regenerate and match**, and the gated live
suite **6/6** including the bioRxiv gap measurement. The live suite is where both of `/details`'
changes were found — its death, then its return on 2026-09-27; it remains the cheapest high-yield
check here, though no CI job runs it (`BMLIB_LIVE_TESTS=1`), which `BASE_URL`'s doc now says.

## Session note (round 60) — Python fixed five JATS owner tests, and the corpus that should have caught them was green and hollow


**The scope was one question asked of `origin/main`: which Python change since the port's
last landing has no counterpart here?** PR #381 (`aff4ee3`) rewrote `bmlib/fulltext/jats_parser.py`
by 251 lines for five issues and **touched no Rust** — its only Rust commit (`8ad724a`) is
the separate #186 analyzer fix that made Python's CI green again. So the port diverged on
all five, and nothing said so.

**Why nothing said so is the round's real finding.** `dump_jats.py` ran clean before the
change and after it: its corpus was **18 documents**, and none reaches a related work in a
citation, a name in a contributor's `<bio>`, a `<contrib-group>` outside `article-meta`, or a
nested `<abstract>`. A corpus that never enters the code a change edits agrees with Python
before and after — the same instrument, facing the way round 47/48 described. The oracle
being green is not evidence about a change **unless a case reaches it**, and no count of
clean corpora can say that.

**The measurement, taken before the port.** 24 documents were added (18 → 42): one per shape
in Python's own new test classes, the boundary each fix must *not* move (a `<contrib>` with no
group, the #120 roster, a `pmc-articleset` wrapper, a body figure's abstract, a
`related-article` in `article_meta`), and the archive's **two real instances** — #271's own
`PMC12105076` and `PMC12180358`, 8.3 kB and 6.5 kB, the only two articles either of the four
artifacts moves. Against `origin/main`'s reader, **18 of the 42 diverged**, every one at the
path its case was named for:

- **`cited_reference` (#270)** — a `<related-object>`/`<related-article>`/`<product>` nested
  in a citation wrote its own title, source, year, volume, issue, pages and DOI onto the
  reference; an erratum's `99:7` replaced the cited work's `1:2`, last writer winning. The
  same ambient `in_ref_citation` gate let a related work's byline become the reference's
  authors.
- **`inside_related_work` (#267, #271)** — a related work's accumulating children were cut
  out of the sentence printing them: `Reply to , a comment`, and a retraction notice reading
  `titled “,”`.
- **`contrib_owns_name` (#258)** — a `<name>` in a `<bio>` **replaced** the author's own
  (`Smith, Jane` stored as `Bob Jones`), a `<collab>` there became the author's, and the
  undivided-name merge refusal cut `<string-name>`/`<collab>` out of the paragraph.
- **`is_articles_abstract` / `in_articles_contributor_list` (#266, #249's latent half)** — a
  dataset's abstract joined `abstract_sections` and an editor in `<journal-meta>`, a
  `<supplement>` or a `<sec-meta>` became an author; a `<fig>` in the article's own abstract
  erased everything before it.

After the port, **42 of 42 match**, the four new named tests in `tests/jats_reader.rs` assert
the same conclusions directly, and all **41** oracle drivers regenerate clean.

**The blast radius the PR shipped without.** PR #381 says in its own body that *no artifact
was diffed against `main`*, because its container could not reach Europe PMC or NCBI. This
clone can, so it was run with a new committed instrument, `scripts/diff_jats_parser.py`
(`--old-source <a jats_parser.py from another commit>`), which loads the pre-fix parser as a
standalone module beside the current one and diffs every rendered field:

| artifact | articles | changed |
|---|---|---|
| served `PMC10030002_PMC10040000.xml.gz` | 8,118 | **0** |
| archive `PMC000xxxxxx` | 3,028 | **0** |
| archive `PMC001xxxxxx` | 27,515 | **0** |
| archive `PMC012xxxxxx` | 97,909 | **2** |

The two are `PMC12105076` and `PMC12180358`, the retraction and correction notices #271
itself named, and the only field that moves in either is
`body_sections[].paragraphs[]` — the retracted or original title inserted back into the
sentence, exactly the fix's intent. **#270, #258 and #266 move nothing on any artifact**,
which matches each issue's own measured 0. #266's body population is the half the PR could not
measure, and 0 differential moves is that population being 0: a body `<abstract>` outside an
exhibit would have re-routed its prose and shown as a move, and the only body abstracts these
artifacts hold are a `<fig>`'s or a `<table-wrap>`'s — #249's population, which the fix leaves
alone. **All 136,570 documents parsed** (the instrument's own
`{parsed}/{total}` column), so an unchanged case is a comparison and not an error both readers
raised; there were no error diffs.

Run the instrument the same way before any release that touches the JATS reader:

```bash
git show <pre-change-rev>:bmlib/fulltext/jats_parser.py > /tmp/jats_before.py
.venv/bin/python scripts/diff_jats_parser.py --old-source /tmp/jats_before.py <artifact>...
```

**What the corpus deliberately keeps.** Five of the 24 cases did **not** diverge and are kept
for that: they pin the direction the fix must not move — the lenient bare `<contrib>`, #120's
roster (the walk stops at the innermost `<contrib>`), the wrapper suffix test, #254's owner
paths refusing a related work's *fields*, and a body figure's abstract (still #249's first
half, unmeasured population and all). A case that cannot fail is not coverage; it is the
boundary, and saying which is which is the point.

**The port follows Python exactly**, including the body half of #266's abstract rule that
Python's PR flagged as unmeasured — now measured 0 over all four artifacts. No Python file was
modified.

## Session note (round 59) — the oracle found three corrections Python had decided against, and a window that ended before its keyword

**Step 2 of *"If you are starting fresh"* found one stale corpus of 41** —
`dump_quality.py` — and asking **which side moved** is what made the round.
Python's extractor audit (`01feb93`, PR #370) rewrote `bmlib/quality/extractors.py`
from 487 to 753 lines after this port's quality corpus was dumped, and it had
investigated all three defects the port corrected and **decided each one**:

- **#294 (a digit-grouped sample size) was adopted.** Python's `_COUNT` is the
  port's pattern, lookarounds and all; six cases came out identical.
- **#297 (negation-blind power/CI bonuses) was replaced by a better fix.** The
  port guarded a mention with a ±40-character window of negation words. On the
  draw that window refused **16 genuine confidence-interval reports and found no
  real denial**, because a CI is reported next to exactly that vocabulary
  (`"HR 0.96, 95% CI 0.46-1.49), with no difference"`). Python's `is_denied` is
  narrow on purpose.
- **#298 (priority over evidence) was refused.** The port's contrastive veto
  moved **55 study-type answers over the draw and none for the better**, and the
  shape it was written for occurs in **0 of 914** RCT abstracts.

**So the module is now a transcription rather than a correction.** All thirteen
`corrected` blocks are retired, `is_negated`/`NEGATION_WORDS`/
`NEGATION_CONTEXT_WINDOW`/`NUMBER` are gone, and `is_denied`, `COUNT`,
`CI_PATTERNS`, `POWER_CALCULATION_*` and `DENIAL_LOOKAROUND` are Python's. The
rule tables use lookbehind, lookahead, scoped case folding and possessive
quantifiers, so they compile through **`fancy-regex`** — which the plan's §2
allows for exactly these sites and refuses for the ones a network reaches. **The
argument is the input, not convenience**: Python runs the same patterns on the
same bytes through `re`, also a backtracking engine, so a backtracking engine
here is fidelity-preserving rather than a new worst case, and the possessive
quantifiers are transcribed as themselves rather than dropped. One new
dependency, one new public surface, and a §2 paragraph.

**The draw also found a defect in the port that all three corrections had left
standing** (#366's item 1): `has_exclusion_pattern` scanned a window that *ended
before* the keyword, where Python's includes it. For `"non-randomised controlled
trial"` the keyword is found *inside* the negation — the hyphen is a word
boundary — so the exclusion that has to fire is the one containing the keyword
itself, and **27 `Controlled Clinical Trial` abstracts read as `rct`**: the design
the paper explicitly says it is not. That is the class of defect only a
population finds, and it is why the corpus is now 575 cases rather than 76.

**What the transcription cost, and what it bought.** The first run of 191 cases
had **one** divergence: I dropped a literal colon from Python's optional
`(?:: space)?` group in `_DENIED_AFTER`, so a blank line stopped ending a
denial's reach. One named test asserted the opposite of Python (`"not
predetermined by a power calculation"` **is** a denial — `"by"` is not among the
prepositions that end the reach, which the docstring says and I read backwards);
the corpus caught nothing there because the assertion was mine, not Python's.
Then a **generated cross-product of 3,715 cases** — power phrases × denial
shapes, CI tokens × denial shapes, study-type frames × designs — passed with **no
divergence**. It is not committed: two 700 kB case files is out of line with every
corpus here. The committed corpus is an **axis-complete 575**, ~106 kB a copy.

**Ten mutants, each rule reverted, and the sweep earned its keep twice.** The
window that ends before the keyword, the literal colon, `"between"` dropped from
the preposition list, a stated interval no longer exempting a mention, the
study-power range widened to include 1%, non-ASCII digits refused, the
sample-size search text lower-cased again, the CI bonus's evidence dropped, the
predictive/laser-word veto removed, and the mention-candidate ordering — all
killed by `quality_extractors` and/or `quality_oracle`.

Two were not clean on the first pass, and both are the reason a sweep is worth
running. **M3's mutation did not apply** (its escaping was wrong), and a mutant
that does not apply is not a surviving mutant — it is a mutation that was never
tested. **M9 survived**: dropping the position sort changes no boolean, because
"is there a mention that is not denied" is order-independent — it moves only
*which* mention the audit trail quotes. The corpus had no case with two
same-kind mentions, so the rule was unpinned. Two `ev/*-echo-quotes-the-first-mention`
cases now put a later pattern's mention earlier in the text than an earlier
pattern's, and both mutants die on the recorded excerpt. **A corpus that compared
booleans would still not see it**, which is the argument for keying the oracle on
the whole `DimensionScore`.

**A transcription inherits the engine's character classes, and that is the one
divergence left.** Rust's `\w` is
`[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]` where Python's is
`[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]`, and Rust's `\s` is `\p{White_Space}` where
Python's `str.isspace()` also holds `U+001C`–`U+001F` — so a combining mark
abuts a keyword for Python and not here. **Measured and pinned**, not reasoned
about: three cases carry `corrected` blocks
(`cw/combining-mark-before-keyword`, `cw/combining-mark-before-ci`,
`cw/file-separator-in-a-denial`) and a fourth
(`cw/accented-letter-before-keyword`) agrees on both sides, because the two
classes differ on combining marks and not on letters. §9 carries the row and the
reason the rewrite was declined: every `\b` becomes a four-branch lookaround
alternation and every `[^\S\n]` a class difference, over fifteen patterns, to
reach a character no biomedical abstract carries — and being diffable against
Python's source line for line is the property the transcription exists for.

**The Python library is untouched** (`git status --porcelain bmlib/` empty), and
no new Python issue was filed — the staleness was the instrument working. #366's
first item is fixed here and its other two are what Python decided; the issue is
commented with the outcome rather than closed from this side.

**And the round's first act was a merge, not a port.** `main` was at `fe255e7`
with rounds 52-58 stranded on branches that each report "merged" because they
merged into the branch beneath them, one of them deleted upstream with the two
commits of #377 existing only in this clone. This branch is the landing; the
release that follows it is a 0.3.0.

## Session note (round 55) — eighteen doc warnings, and the wrong names inside them

**`cargo doc` was not a gate, so nothing read the doc comments** — `clippy` does not, and
neither does `cargo test`. It reported eighteen warnings, and they were not lint noise:

* **three named `RepairError` variants that do not exist.** `repair_json`'s and
  `extract_and_repair_json`'s `# Errors` sections promised `RepairError::Empty`, where the
  enum has `EmptyRepair`, `EmptyParse` and `EmptyExtract` — a caller catching the documented
  variant would catch nothing, and would not learn it from any test, because no test reads
  a doc comment.
* **five named items that do not exist at all**: `PerformanceMetrics::start_time` (the field
  belongs to `MetricsSnapshot`), `PerformanceMetrics::from_dict` (the port's is
  `MetricsSnapshot::from_json`), `CsvError::TooManyFields` (the enum has `NoHeader` and
  `Malformed`), `DayStep::RefusedForCheckpoints` (the variant is `CheckpointedButEmpty`) and
  `DayPartsUnreadable` (the type is `LoadPartsError`).
* **four links to private items** from public docs (`walk_document`, `first_acceptable`,
  `tagged_coi_sections`, and the `pyvalue` pair from round 54), plus one to a **test**
  (`tests::the_xml_layer_reports_what_it_refuses`).
* **five cases of bracket prose read as a link** — `[2]` in a reference label, `[params]`,
  `[postgres]`, which is a *feature* — where the fix is to escape or unwrap rather than to
  add a target.

**Each is right now**, and the ones that named the wrong item keep the wrong name quoted in
place, because the useful question a reader has is "was it always `Empty`?" — no; the port's
`repair_json` has always returned `EmptyRepair`, and the doc was written against a sketch of
the enum. The `# Errors` claim is the only kind of defect here that a test cannot see.

**And the gate exists now.** `cargo doc --no-deps` with `RUSTDOCFLAGS=-D warnings` is a step
in CI's Rust lint job, beside `rustfmt` and the two `clippy` runs, so the count is zero and
stays there — the same move as the one-homes of rounds 51-53, one level down. The README's
own gate block carried a stale figure with it ("861 tests + 3 doc-tests" against a measured
937), which is corrected in the same pass; the block is what a reader trusts and nothing
checks.

## Session note (round 54) — `db/` gets a corpus, and it finds a row count that belonged to another statement

**`db/` was the last package without one**, and the reason it was last is that almost
none of it is a *rule*: it is thin wrappers over two drivers. What is diffable is what a
caller can see — the statements the splitter produces, the dialect's placeholder
spellings, the value shapes a fetch returns, which tables exist, and what a nested
`transaction` block commits when it raises — so those are the cases, over a small step
vocabulary. `rust/oracle/dump_db.py` runs them against Python and `tests/db_oracle.rs`
replays them; Python's *mechanism* for nesting has no counterpart (`_depths`,
`_depth_key` become the type of the value in hand), so only outcomes are compared, which
is the right contract for a layer whose job is to hide a driver.

**It found a defect on its first run.** `execute` returned `sqlite3_changes()` — the most
recent INSERT/UPDATE/DELETE's count, which is **not reset** by a statement that changes
nothing. So `execute("CREATE TABLE b")` after an `UPDATE` of two rows reported **two**: a
caller logging "2 rows affected" for a DDL, from a statement that changed none. The fix
asks `total_changes()` whether the statement changed anything *at all* and reads
`changes()` for the count — the pair SQLite offers that keeps a trigger's rows out of it,
as Python's `cursor.rowcount` does. **The tempting fix is the `total_changes` delta, which
is right for a DDL and wrong the moment a trigger fires**; this schema's FTS triggers make
that a one-call mistake, so the named test asserts the trigger case and the sweep includes
that mutant (killed).

**One divergence remains, and it is a §9 row**: Python's cursor answers `-1` for a
statement that changed nothing (meaning *not applicable*), the port answers `0` (meaning
*nothing changed*). The port cannot spell `-1` — `execute` returns `u64` — and the two
agree in meaning. The corrected case pins the value *and* that a DDL after a DML reports
nothing.

**What the corpus does not cover, and why**: PostgreSQL. Python would need a server to
answer at all, so the diff runs on SQLite; the dialect-specific surface is the placeholder
spelling, which this corpus covers on the SQLite side and `tests/dialect.rs` covers on
both, with `tests/postgres_live.rs` behind a live server. The README says so.

**Five mutants killed**: the stale row count, the delta-only fix (by the trigger
assertion), the splitter's block-comment terminator (the port's own documented fix, now
caught by a corpus as well as by `tests/split.rs`), an inverted `owns_commit`, and a
migration runner that re-applies an applied version.

**And the pass turned up four doc defects of its own kind.** Three module tables claimed
work was unported that is ported — `publications/mod.rs` called `fetchers/` "in progress",
`context_processor/mod.rs` gave `llm_processor` no Rust column, and
`quality/cochrane_assessor.rs` said in prose that `LLMChunkProcessor` "is not ported (it
follows the `llm` package), so the map-reduce is a `Condenser` the caller supplies" — while
its own `use` line imported it and `LlmCondenser` below *was* that map-reduce. Each is now
correct and the corrected one keeps the wrong sentences quoted, because the failure was a
paragraph not revisited after the code arrived.

**`cargo doc` is not a gate, and it reports 19 warnings** — unresolved intra-doc links,
mostly bracket prose (`[2]`, `[params]`) that rustdoc reads as links, plus two
`PerformanceMetrics::*` paths. Four more of the same kind were **mine** (public docs
linking private `pyvalue` items, and a `ContextModel` link with no path); those are fixed,
and the 19 are named here so a future round can decide between fixing them and adding the
gate rather than rediscovering them.

## Session note (round 53) — the PubMed fetcher, and the registry that makes a source fetchable

**The gap was the last item on this file's own list.** `fetch_pubmed` existed and was
tested, but nothing implemented `Fetcher` over it and nothing wired the built-in sources
into a registry, so `sync()` over `"pubmed"` recorded `No fetcher found for source: pubmed`
while `builtin_descriptors()` went on describing it. `PubMedFetcher` and
`builtin_registry(client)` close both halves.

**One thing in the fetcher was not plumbing.** `DayCallbacks` holds five separate
`&mut dyn FnMut`, and five closures cannot each hold `&mut` to the caller's sink, so the
sink goes into one `RefCell` — the alternative being a second buffer, which is what the
sink exists to remove. That is the only place the trait's shape and PubMed's callback shape
disagree, and it is stated where it happens.

**The mapping is tested through the fetcher, not only through the walk** — six tests in
`tests/pubmed_fetcher.rs`, and the two that matter most are the partition ones. They needed
a source whose records spread over a **window** of Entrez dates, which is what a real
publication day looks like — its EDATs are deposit dates, not the day — so
`ScriptedEutils` gained a `dense` mode. The partitioned loop's own tests avoid the ladder's
arithmetic by injecting a narrow root; the fetcher cannot, because `fetch_pubmed` uses the
production root. With that, the second test runs a full walk, takes the checkpoints **out of
the sink**, hands them straight back as resume state, and asserts every part is skipped —
the round trip `sync`'s resume depends on, with nothing in the test knowing how the ladder
divided the day.

**Eight mutants killed**, one of which is the point of the round: a registry that wires only
its first descriptor. The test that catches it uses a client which panics if anything is
fetched, so it also pins that building a registry is wiring rather than I/O.

`ScriptedEutils` moved to `tests/common/pubmed_sim.rs` rather than being copied a third
time, and gained a `session_failures` map symmetric with `plan_failures`: a day's own search
and a plan's are different calls, and the fixture could only script a failure of the second.

## Session note (round 52) — the §9 row was wrong about the half it said still worked

**The row said the port lost the memory bound and kept the resume. It lost both.**

`sync` collected every `Progress::PartFinished(Completed { checkpoint })` into a `Vec` and
dropped it — nothing in the crate called `record_day_part` outside a test — so **no sync had
ever written a `download_day_parts` row**. A partitioned day interrupted at part 200 of 500
restarted from part 1, and `carried_credit` and `skipped_keys` were dead outside the test
suite. The same `Vec` also held every `Progress` event of the walk: a second unbounded step in
the same place.

The row's other error was about the *work*: "a change to `Fetcher` **and to all three
fetchers**". There are **two** `Fetcher` impls (`biorxiv`, `openalex`). `pubmed`'s
`fetch_pubmed` is already callback-shaped — `on_record`, `on_part_finished`,
`on_part_skipped` — and has **no `Fetcher` impl at all**: nothing in the crate wires a built-in
registry, so a caller registers the two fetchers by hand and a `sync()` of `"pubmed"` records
`No fetcher found for source: pubmed`. That is now in the README's open work, because the
module's row said "ported" and a reader takes that to mean fetchable.

**What the change is.** `Fetcher::fetch` takes `&mut dyn FetchSink` — Python's `on_record` and
`on_progress` as one object, because the flush needs the day's buffer *and* its connection at
the same moment and two closures cannot both hold `&mut` to those. `FetchOutcome.records`
becomes a `record_count`, and `CountingSink` is how a walk keeps the count it reports equal to
what its caller received — which `records.len()` used to guarantee by construction.
`PartDisposition::Completed`'s checkpoint becomes an `Option`: with a bare checkpoint, a part
that came up short could only claim one it had not earned or report no boundary at all, and no
boundary is the peak the drain exists to remove, on exactly the degraded days it matters most.

**Three more defects were in the path being rewritten**, each now pinned by a test: a hard
`Err` from a fetcher discarded every record the walk had delivered (Python's closing store
keeps them); a failed day's row was written from the *failure's* count, which counts the buffer
a second time once the closing store has folded it in; and the day's store and status row were
never one transaction, which every storage helper's doc claimed ("the caller's per-day
transaction") and nothing opened.

**Eight mutants killed**, one of them — moving the closing store out of its transaction — by
the new atomicity test alone; every other test stays green without it, which is the whole
argument for having written it.

**A retired §9 row is worth more than a deleted one.** The row is replaced by a paragraph
saying *which* half was wrong and why, and the widened `PartDisposition` payload is recorded
there rather than left in the diff: the next reader of that table is looking for exactly this
kind of claim.

## Session note (round 51) — one home for `repr()`, five copies of a type name, and a count that was wrong

**#365, and it is #350 one function over.** The crate had three `repr()` copies beside
`pyvalue::python_repr` and **five** `json_type_name`s, one of which spelled its number arm
`is_i64() || is_u64()` where the other four spelled it `is_f64()` — the same latent drift,
found the same way (by reading, not by a failing test). `pyvalue` now holds
`python_repr`, `repr_str` and `json_type_name`; the private copies are deleted from
`biorxiv`, `openalex` and `analyzer`, and `sync`'s `python_repr_str` with them; the three
**public** names keep their signatures and delegate, so nothing downstream breaks.

**The one behaviour change is the container spelling, and measuring it was the point.**
`publications::models::python_repr` wrote JSON text (`[1,2]`, `{"a":1}`) where Python's
`%r`/`{value!r}` writes its repr (`[1, 2]`, `{'a': 1}`). **Nothing the oracle compares
moved**, and that is a finding rather than a reassurance:

- its three call sites in `models.rs` each narrow to a `Value::String` first, so the
  container arm was unreachable from inside the crate; and
- the one site a container *can* reach — `biorxiv`'s non-numeric-`total` refusal, the only
  place `!r` prints a value that may be a container — had **no corpus case**.

Two were added with the change (`fetch/non-numeric-total-object`,
`fetch/non-numeric-total-list`), and reverting the delegation reddens both. So the
spelling is pinned by the oracle rather than by the doc comment that claimed it.

**And the one-home property is mechanised, because no behavioural test can keep it.** A
re-introduced copy that spells the same answer passes every test there is — which is
exactly how these copies survived two refactors. `the_crate_defines_each_of_these_once`
reads the crate's own `src/`, walks it, and requires exactly one implementation of each of
the three; the public delegators are allowed once each, identified by their bodies going
through `pyvalue`. It **fails closed** (fewer than 50 source files found is a panic, not a
pass). The crate's Python side does the same thing with `ast`
(`TestOnlyTheHelperWalksTheEuropePMCResultList`), and this port now has both.

**One type name still differs from Python, and it is a range limit rather than a
spelling**: `serde_json` without `arbitrary_precision` parses an integer outside
`i64`/`u64` as an `f64`, so `18446744073709551616` answers `float` where Python's
arbitrary-precision `int` answers `int` (measured 2026-09-27; `u64::MAX` agrees on both
sides). `python_str` has the same limit for the same reason, so both say so where they are
documented, a unit test sits on each side of the boundary, and §9 carries the row.

**Six mutants killed**, and one survives **by construction**: the container spelling (by
the corpus *and* the unit tests), the number arm collapsing to `int`, the `Null` arm,
`repr_str`'s quotes, `models::python_repr` re-inlined, and a same-named `json_type_name`
copy added to another module — the last two caught only by the structural test, which is
why it exists. The survivor is the number arm swapped for the other spelling: the doc
claims the two are unobservable, and a `serde_json` change would make them observable.

**And the round found a measurement error of its own — in the handover.** Round 50 recorded
`main` as **885 passing**; a clean worktree says **888**, reproducibly. The 885 came from
running `cargo test` in the shared `target/` right after a `git checkout` of another branch,
and the *merged-result* figure taken the same way matched its clean measurement exactly — so
the staleness is **intermittent and invisible from inside**. Two more counts were caught by
the same check this round (a branch figure of 917 that is 918). Every figure in the table
above is now taken in a `git worktree add --detach` and thrown away afterwards.

## Session note (round 50) — the planner invented a refusal, and a corpus case that wasn't testing what it was named for

**#359, and it was four sites rather than the two the issue named.** `plan_partitions`
returns `Result<Vec<Partition>, PlanError>`, and `PlanError` had three *structural*
variants — so a `count_fn` failure had nowhere to go and every probe site mapped it onto
one of two fabrications:

| site | what a caller read |
|---|---|
| root probe | *"the Entrez-date range A..B holds **0** of this day's N records, so N of them lie outside the ladder and would be silently absent; refusing the day"* |
| descent's left child | *"**n** records share the Entrez date A, above the 10000 a history session serves…"* |
| the `lo == hi` measurement | the same, naming a count nothing measured |
| the derived-zero re-measure | the same |

Python propagates the exception and `_fetch_partitioned` reports it under one of **two
arms**: the two structural refusals verbatim, and everything else —
`f"planning the Entrez-date parts failed: {type(exc).__name__}: {exc}"` at the day level,
`f"re-partitioning part {part.key} failed: …"` at the re-plan. The port wrote
`failed(0, e.to_string())` at both call sites, so even the *prefix* was missing.

`PlanError::CountFailed { name, message }` now carries it, `plan_failure` and
`replan_failure` build the two sites' messages, and `named()` renders Python's
`f"{type(exc).__name__}: {exc}"`. The name/message split is made once, at the constructor,
because `Eutils` documents its message as that whole string (#354); a message with no
`": "` is named `Exception`, which is what `type(exc).__name__` answers for a bare
`Exception` — assumed at its base, never invented.

**Ten mutants, each fix reverted, all killed** — the four probe sites, both verbatim arms,
the `ValueError:` prefix an inverted root needs, `named()`, and the constructor's
fallback and split point. (The first pass counted a non-compiling mutant as a kill;
`M5`/`M6` were re-run with compilable ones before believing them.)

**And the corpus case I went in to extend was hollow.** `plan/unsplittable-measured`
keyed its `counts` on the **wide** range `2024/06/01 : 2024/06/30` while asking for
`lo = 2024-06-01, hi = 2024-06-02`, so the root probe fell to the default 0 and the case
reached `RootNotCovering` — *"holds 0"* — instead of the measured descent it is named for.
Both sides agreed on that wrong answer, so it was green. The check that found it is one
line: **compute the term the case will actually probe and ask whether the fixture
carries it.** Four `probe-fails-*` cases now cover the four sites, one per probe, and the
fixed fixture reaches the `Unsplittable` it names.

**A diagnostics gap fell out and is in §9 rather than fixed.** Python logs a WARNING when
a child reports *more* than its parent — *"the counts moved between probes"* — the one
inequality two counts of one instant cannot both satisfy. The port notices the same
inequality (it is what sends it to the re-measure) and carries on silently:
`pubmed.rs` has no logging, and **the parts planned are identical**. Same shape as Rule
5's unreadable-row row, and it wants the same decision.

**#361 was then answered by the maintainer's call, and is PR #364.** Every transport
failure had been named `RemoteProtocolError` in four modules and `TransportError` in
`fulltext/service.rs`. Measured against httpx 0.28.1 (four probes): a refused connection
and a DNS failure raise `ConnectError`, a server that accepts and never answers
`ReadTimeout`, a connection reset `ReadError` — **all subclasses of
`httpx.TransportError`**. `FetchError::Transport` is one variant for the lot, because
`http.rs` sees only what `ureq` reports, so the **base** name is the only one that is true
whichever happened; `RemoteProtocolError` is the narrowest of the four and a false claim
about the peer for three of them, which is #349's defect one layer up. The call was to name
the base rather than model the taxonomy, and the residual is a §9 row — with the note that
**no corpus case covers a transport failure at all**, the hole #349 found for statuses, so
the four tables are pinned by named tests. The rule is stated **once**, on
`FetchError::Transport`, and the four tables point at it; `openalex.rs`'s had no doc comment
before this. Four mutants, one per table, all killed. This moves the stored error string for
every failed day whose request never arrived.

**And the channel those four tables had no coverage for is now in the corpus.** `fetch/transport-error`
in the bioRxiv and OpenAlex corpora feeds a
`{"transport_error": {"name": …, "message": …}}` marker through the page source, with a
`corrected` block recording that the port says the base class where Python says the subclass.
Two copies had to be removed to do it, which is the #358 review's lesson applied:
- **One home on each side.** `rust/oracle/_oracle.py` holds the corpus's response vocabulary
  (`response_marker`, `named_exception`) and `rust/bmlib/tests/common/oracle.rs` is its Rust half
  — the reader both harnesses had grown a copy of for #349, and which the transport marker would
  have made a third. They are deliberately **two implementations of one contract**, because the
  dumpers are Python and the harnesses Rust, so each has its own tests:
  `tests/test_rust_oracle_helpers.py` (17) and `bmlib/tests/oracle_response.rs` (8, in **its own
  binary** — everything under `tests/common/` is compiled into all five binaries that declare
  `mod common;`, so a test written there runs five times while looking like one).
- **Every marker fails closed**, recognised by its whole key set, panicking on a wrong key or
  type rather than being read as a body — which is precisely the defect #349 was.
**Ten mutants killed**: both harnesses' transport arm, the reader's marker recognition, its
whole-key rule, its status type check and its empty-name assert, `_oracle`'s key rule, its split
point and its unnamed fallback, and the Python dumper's transport branch — the last caught by the
companion test's anti-vacuity assertion once the expectation is regenerated.

Measured **on the stack as pushed** — which is based on `b164126`, before #358 merged —
**898 tests default, 906 with `pdf`, 908 with `postgres`, 916 with `--all-features`** (890 +
the reader's 8 and the corpus cases); `clippy --all-targets` and `cargo fmt --check` clean;
**40/40 oracle corpora regenerate** — now **38 corpora / 2,629 cases**, two of them the
transport failures — and the Python side is clean too (`ruff` 0.15.20, and the two tooling
test files at 40 passed). The same stack merged onto the new main is 914/932, below.

**And the open PRs were merged into a throwaway branch and re-measured, because green
branches are not one green tree.** Three times, and the last is the one to believe: #358
**merged into `main` while round 50 was running** (`8c36073`), so the remaining five were
merged onto that new main and the whole gate set was re-run. The merge is clean (no
conflicts), and the result is **914 tests default, 932 `--all-features`, 0 failing, 40/40
corpora, `clippy`/`fmt` clean, the Python tooling tests 40 passed, and the live network suite
6/6** — the last of which is the point: **`main`'s live network suite is red until #362
lands**, because bioRxiv restored `/details` mid-round-49. Any branch of the `#357 → #360 →
#363 → #364` stack that does not also include #362 fails that one test, which is expected and
not a defect; the merged result does not. The PostgreSQL suite is green on the stack itself
(locally and in CI).

**Rebase before merging, and re-measure.** #358 landing mid-round moved the base of everything
below it, so a figure taken before that is a figure for a tree that no longer exists.

## Session note (round 49) — a corpus case that had never made a request, and the same hole three layers down

Steps 2–4 of *"If you are starting fresh"* were run first, on `main` (`b164126`), and were
clean: **40/40 oracle corpora** regenerate and match, **872 tests** default / **880** `pdf`,
`clippy --all-targets` and `cargo fmt --check` clean, the **live network suite 6/6** and the
**live PostgreSQL suite 10/10**. Then the open Rust-port issues, in the order this file
recommends. Three PRs and three newly filed issues.

- **#344 is closed.** Its three items are all in the port: the settle period as
  `SourceDescriptor::settle_days` with `MAX_SETTLE_DAYS`/`check_settle_days`/
  `with_settle_days`/`BIORXIV_SETTLE_DAYS` (#348) and the day selection that uses it
  (#353), the two `published_*` extras, and `_field`'s truthiness in `field_value`. Every
  test the issue asked to mirror exists; the comment on the issue names them.
- **#346 is not Rust work, and the port must keep reproducing it.** It is Python's
  `_upsert_download_day` replacing a stored `record_count` with a lower one, it needs a
  maintainer decision from three options, and it is not one of the enumerated corrections
  — so by §0 the port follows Python. `sync::upsert_download_day` is the **one** site
  either implementation writes a day's count, and the issue now records that a decision
  applies to both. Nothing changed here.
- **#349 fixed — PR #357.** `fetch/http-error` had **never made a request that carried a
  status**. Both corpora encoded the page as `[body, 500]`; the Python dumpers recognised
  that pair only as a Python *tuple*, which JSON cannot express, and the Rust harness
  wrapped every payload in `Ok(..)` with no status channel. On both sides the case was
  served a *list body*, its committed expectation was the `fetch/non-object-payload`
  refusal, and it passed green. The port meanwhile rendered a 4xx/5xx as
  `RemoteProtocolError` — a protocol violation, which is not what a 500 is, and exactly
  the defect #349's own sibling had just fixed one layer up.
  - The corpus marker is `{"http_status": N, "body": B}`, read by both harnesses; the
    unreachable tuple convention is gone from all three fetcher dumpers.
  - `FetchError` gained `HttpStatus { url, status }`, named `HTTPStatusError`, and the
    same table in `sync.rs` and `fulltext/service.rs` follows.
  - Two cases per source reach the status path, one with a full page already delivered;
    the message wording (the port's own, where Python's is httpx's) is a `corrected`
    block and a new §9 row.
  - **A corpus case is not the whole net**: `the_real_page_source_refuses_a_status_as_a_status_error`
    and its OpenAlex twin drive the real `HttpPageSource`/`HttpCursorPages` over a
    scripted `HttpClient`. Nothing but the gated live suite touched those types, so the
    scripted `PageSource` both corpora use was a second implementation of the same
    branch — three of the nine mutants live there.
  - **The anti-vacuity assertion is the part worth copying.** "Python differs from the
    corrected value" does not mean the case reached its rule: a dumper that stopped
    honouring the marker would serve a list payload, which *still* differs, and the case
    would stay green while testing the refusal it used to duplicate. The companion test
    therefore asserts on the **committed expectation** that Python's answer is a status
    failure (and that the page-1 case delivered records first) — which is what kills the
    Python-dumper mutant once the expectation is regenerated.
- **#350 fixed — PR #358, and its review found more than the refactor did.** The copies had
  drifted in the `Number` arm (`is_some_and` against `is_none_or`), and the **majority
  spelling is the wrong one** — `as_f64()` answering `None` means a magnitude too large for
  an `f64`, which Python calls truthy where `is_some_and` called it *zero*; `serde_json`
  cannot reach that state as built, so `src/pyvalue.rs` splits the decision into
  `number_is_truthy(Option<f64>)` with a test over `None`/`inf`/`-inf`/`NaN`. That much
  held. **What the review (`95dd83e`) corrected is worth more than the refactor:**
  - **The count was five copies, not three-of-one-and-two-of-the-other, and there was a
    sixth.** `templates::render_value` was a third `str()` — and the *better* one, writing a
    container as Python's `repr` (`[1, 2]`, `{'b': 'deep'}`) where both `python_str` copies
    wrote JSON text. So "one home" still held two answers; `python_str` now takes
    `render_value`'s container spelling, `templates` calls it, and the helper is
    `pyvalue::python_repr`.
  - **The ID Converter's log lines use `%r`**, so Python prints an unusable `pmcid` quoted
    and the port did not, and an absent `errmsg` logged `""` where Python's `%s` of the
    absent key is `None`.
  - **The float-exponent approximation I documented did not exist.** serde_json 1.0.151
    writes `1e+100` as Python does; the comment was inherited from the cochrane copy and was
    true under the older ryu formatter. The real differences are a float below 1e-4, an
    integer outside i64/u64, and the literal `-0` — now measured and pinned.
  - **Three of my doc claims were false**, including `truthy`'s saying a present falsy value
    "is not an absent one", which is the opposite of what it does, and a
    `raw.get(key).filter(truthy)` example that does not compile (E0631).
  - **Two mutants survived the whole suite in the file the refactor touched** — a dropped
    `to_lowercase()` (a withdrawn record fetched) and `is_some()` for `is_some_and(truthy)`
    (an empty `pmcid` filed as a fault). My eleven were the ones I had written tests for;
    that is not the same claim as "the rules in this file are covered".
  - The crate's other `repr()` copies and five `json_type_name`s were **#365**, fixed in
    round 51 below — and the oracle check that comment asked for is what found that the
    spelling was wrong and nothing measured it.

  **The lesson, which cost a round and is cheap to state:** a refactor that consolidates a
  rule inherits responsibility for *every* copy and every claim about it, and "no behaviour
  change" is a claim to be measured, not asserted — the review's fix moved log and fault
  wording.
- **#354 fixed — PR #360 (stacked on #357).** Every PubMed handler in Python stores
  `f"{type(exc).__name__}: {exc}"`, and the part-level one says why in a comment; the
  port's `Eutils` returns a `String` where Python raises, and `HttpEutils` produced bare
  messages, so a 500 was stored as `{url} returned HTTP 500` and `read_esearch`'s
  `ValueError` reached the day unnamed. The name now comes from the same `error_type_name`
  table the three sibling modules keep — **and the first cut hard-coded `"HTTPStatusError"`
  at the two call sites, leaving the table's `HttpStatus` arm dead; a mutant said so**,
  which is the round-49 lesson in miniature.
- **Filed, not fixed.** #354 was filed and then fixed here. Two more came out of reading
  the same paths and are **Rust-side** work for the next round:
  - **#359** — a transient ESearch failure **while planning parts** is reported as a
    structural refusal about the source (`RootNotCovering` with `root_count: 0`, or
    `Unsplittable`), where Python propagates the exception and prefixes `planning the
    Entrez-date parts failed: `. `PlanError` has nowhere to carry a `count_fn` error and
    the doc comment above `plan_partitions` claims the opposite; no corpus case calls
    `plan_partitions` at all.
  - **#361** — every transport failure is named `RemoteProtocolError` in four modules and
    `TransportError` in `fulltext/service.rs`. Measured against httpx 0.28.1, Python
    raises `ConnectError` (refused, DNS), `ReadTimeout` (silent server) and `ReadError`
    (reset), all subclasses of `httpx.TransportError`; the port collapses them and picks
    the *narrowest* name, which is a false claim about the peer for three of the four.
    `TransportError` is the base and is already the spelling in one module.

**The thread through all three fixes is one gap, at three layers: no corpus case had ever
made a request that carried a status, produced a transport failure, or called
`plan_partitions`.** Each was found by reading a case that was named for the rule and
asking what it actually reached — the round-47/48 lesson (*"check that a case marked green
actually reached the rule it is named for"*), and #359 is that question asked one layer
further up.

**And the live suite found something, which is the fifth round running that it has.** The
verification pass at the top of this round reported **6/6**; running it again at the end
failed, because **bioRxiv restored `/details`** — the endpoint #325 is about — between the
two runs. The port read 200 with a zero-byte body in every probe recorded in this
repository, and now reads **64,657 bytes of JSON**. Measured 2026-09-27, two servers and
two days, `/details` against `/pubs` (which the fetcher reads, because Python adopted it in
#343):

| server | day | `/details` | `/pubs` |
|---|---|---|---|
| biorxiv | 2024-01-15 | 207 | 34 |
| biorxiv | 2025-06-01 | 195 | 6 |
| medrxiv | 2024-01-15 | 28 | 10 |
| medrxiv | 2025-06-01 | 26 | 3 |

So `/pubs` is now the **narrower** endpoint — only preprints already paired with a journal
publication, which is exactly the gap #341 records — and #325's option 1 is available
again. **The fetcher's URL was not changed**: which population a source collects is a
product decision, and both issues now carry the table. What changed is the test (PR #362):
it no longer pins `/details` as dead, it **measures the gap** — `/details` must declare a
total at least `/pubs`'s for the same day — so every live run re-reports it and a
third-party change cannot pass unnoticed a second time. This is also why the round's first
live run being green is not evidence that the *next* one will be: the suite's value is that
it reads a service, and a service moves.

## Session note (round 40) — two Appendix defects the port still reproduced

Round 39's audit claimed every enumerated defect was accounted for. Re-deriving
each Appendix row against the Rust **source** rather than its module docs found
**two rows still reproduced**; both are now fixed, with tests, and the Python
library is still untouched (`git status --porcelain bmlib/` empty):

- **#310** — `CochraneStudyCharacteristics::from_json`, and the assessment reader
  above it, required the keys Python indexes directly, so the partial
  `cochrane_assessment` the permissive write path invites could not be read back.
  Both now read leniently. Pinned by the cochrane corpus's **first two `corrected`
  oracle cases** (the Python side still asserted to raise `KeyError: 'methods'`),
  with a companion test asserting both cite #310. *Superseded in round 43*: Python
  adopted the fix, the assessment reader now requires both sections as Python's
  does, and the corrections were retired.
- **#309 part 2** — `FullTextCache::get_pdf` was `path.exists().then_some(path)`,
  so a directory at the PDF entry was a cache hit for ever. It now consults
  `is_readable` and the service quarantines the entry as its HTML branch already
  did. Two tests cover it (cache and service level).

Everything else re-derived clean: all 40 dumpers regenerate from the live Python
and match what is committed, `cargo clippy --all-targets` is clean,
`cargo fmt --check` is clean, and the gated live suite passes 6/6. The plan's
round-39 section is annotated, and a round-40 section records the two fixes.

## Session note (round 41) — the oracle was stale in three corpora

Step 2 of *"If you are starting fresh"* below — re-run every dumper against the
live Python — was executed, and it **found three stale corpora**. The claim just
above, that all 40 dumpers regenerate and match, was false when written. The
cause is a merge ordering the round-40 pass did not account for: `e9db0f9`,
committed two hours *after* the port commit `4ba04a1`, **fixed seven Appendix
defects in Python** — #299, #300, #301, #302, #303, #308 and #315 — so the
`corrected` blocks that pinned the port's deliberate disagreement were left
describing a Python that no longer existed.

- **`json`** — the four #299 corrections were retired; all 64 cases now diff
  strictly, with a companion test that still names the four interleaved-truncation
  cases so a corpus edit cannot drop them silently.
- **`protocol`** — the #315 system-message correction was retired, and §9 of the
  plan no longer lists it as a divergence: both implementations join now, and the
  source comment on `messages_to_anthropic` records the fix.
- **`sync`** — `window/huge-recheck` read the real clock on both sides, so its
  committed expectation (`739884`) expired at the next midnight and could never
  be regenerated. Both now read the case's pinned `now`; regenerated, the case
  reads `739051` and is reproducible on any day.

**No port defect was found.** The Rust code already implemented #299's closer
order and #315's join; it was the instrument describing the old library. The
plan's round-41 section carries the detail.

**The lesson is the one already in this file**, restated because it cost a round:
an oracle that is not re-run does not merely fail to catch drift — it *agrees*
with a library that has moved, and its `corrected`-block assertions pass only
while nobody regenerates the expectations.

## Session note (round 43) — Python adopted the quality fixes, and the port was still refusing them

Step 2 of *"If you are starting fresh"* — re-run every oracle against the live
Python — was executed over all 40 corpora. **Four were stale, and the port itself
was wrong on three readers.** `07335c1` and `d4a82a0` (2026-09-27) fixed #295,
#310, #312 and #317–#320 **in Python** — the seven defects the Rust quality port
was written against — so the 22 `corrected` blocks pinning the port's deliberate
divergence had become stale notes describing a library that no longer existed.

Re-deriving each block against the live Python split them three ways:

- **19 cases: Python now agreed** with the port's corrected value. The blocks
  were retired; the cases diff strictly with no port change.
- **4 cases: Python had moved to the correct answer and the port had not.** Two
  are [#332](https://github.com/hherb/bmlib/issues/332)'s named divergences, and
  `docs/DECISIONS.md` had already ruled on both: `clamped_confidence` now refuses
  a boolean and a non-finite number (the port read `true` → `1.0` and `"nan"` →
  `0.0`), and `CochraneStudyAssessment::from_json` **refuses an absent
  `risk_of_bias`** rather than defaulting nine "Unclear risk" domains — the
  register names the port's reading as "the fabrication the bullet above
  refuses". The other two are #332's "smaller difference" and one it did not
  name: `methods`/`support_for_judgement` read as a **string** or unstated
  (only a risk-of-bias `judgement` still stringifies, being a vocabulary
  lookup), and `group_sizes` goes through `as_int_map`.

**#332 is answered** — see the review follow-up below for the third part.

**The re-run is a script now**: `scripts/rerun_rust_oracle.py` (add `--write` to
regenerate). It diffs each dumper's output parsed against the committed
expectation and also checks that a corpus keeping two copies of its cases has not
let them drift apart. This is the second round it has found something, and the
first where the finding was a port defect rather than a stale expectation — which
is exactly the case for making it one command.

Verified after the fix: 40/40 corpora regenerate and match (2,552 cases),
`cargo test` **827**, `--features pdf` **835**, `--features postgres` **837**,
clippy and fmt clean, live network **6/6**, live PostgreSQL **10/10**, and
`git status --porcelain bmlib/` empty.

**The review of round 43 found the fix half-done, and closed it.** A
five-reviewer pass over the change, each finding re-checked against Python on
`main`, found `CochraneStudyAssessment::from_json` still defaulting an absent
`study_characteristics` (Python refuses it, and checks it first); #332's "smaller
difference" unaddressed, and wider than filed (`"45.5"` → 45, `"nan"` → 0,
`"inf"` → `i64::MAX` for a count, and `"nan"`/`"inf"` accepted by the Tier 2/3
float reader); the Cochrane counts and scores ignoring a numeric string; the new
`f64::clamp` storing `-0.0` where Python stores `0.0`; and the Cochrane oracle
harness passing any case Python raised on. All fixed: one private
`quality/json_fields.rs` now states Python's `as_int`/`as_float` and its signed-zero
clamp once, `clamped_confidence` logs a refused value at WARNING as Python does,
the risk-of-bias refusals use Python's wording, and the Cochrane harness compares
a refusal's message and cannot pass a Python exception on an op that cannot
refuse. 14 cochrane, 7 assessor and 11 LLM-reader cases were added, and 10
mutants (each fix reverted) were all killed. `scripts/rerun_rust_oracle.py` now
fails a run for a broken dumper, drifted case copies, an unlisted `dump_*.py` or a
`--only` that selects nothing, `--write` or not, and has a test file
(`tests/test_rerun_rust_oracle.py`). **With that, all three parts of #332 are
answered.**

## Session note (round 44) — a flaky test, the funder measurements, and a published crate that predates its own fixes

Step 2 of *"If you are starting fresh"* (re-run every oracle) and steps 3 and 4 (the gates
and the live suites) were executed. **All 40 corpora regenerate and match, and the gates
are clean on every feature set** — 843 tests default, 851 with `pdf`, 853 with `postgres`,
861 with `--all-features`, `clippy --all-targets` at 0 warnings for both, `cargo fmt
--check` clean — **and both gated live suites pass**: network **6/6** against the real
services and PostgreSQL **10/10** against the local server. Three
things were found; two are fixed, one needs a decision.

- **A flaky test in `tests/http.rs`, found by the pdf-feature gate.** Its last failure
  aborted that whole suite, and it passed every time it was run alone.
  `an_undecodable_body_is_carried_and_refused_by_text` hand-rolled an HTTP server that
  wrote its response and closed **without reading the request**. A socket closed with
  bytes still unread in its receive queue is answered with a reset rather than a FIN,
  and the reset can beat the client's read of the response already written — so the
  caller saw `Transport("io: Invalid argument (os error 22)")` for a request that was
  served. Measured, not reasoned: **the ten-test binary failed 1 run in 200** with the
  hand-rolled server and **0 runs in 1000** after routing it through `serve_once`,
  which reads the request already and now says why in its doc comment. This is the
  same class as the two `cargo` gotchas below: a passing suite that lies about *when*.
- **The funder-count measurements are ported** (`rust/bmlib/tests/funder_matching.rs`).
  The port carried the matcher's token tuple but not the evidence for it — eight of the
  Python's twenty-five canonical rows are tokens that were *considered and refused*,
  they are in neither tuple, and four are two-character forms the corpus holds no trace
  of, so re-adding one changed no measured count and no name-agreement case either.
  `transparency/analyzer.rs` now carries the row table (the four membership rules, each
  row's `in`/`out`, its measured `N TP / M FP` and the rule that decided it) and the new
  test re-derives all of it over the 407 scoring entries, including the corpus's own
  size and a per-token control. Its stale claim that the corpus *"does not exist in this
  port"* is gone — it is vendored as `funder_matcher_expected.json`. **10 mutants, each
  fix reverted, were all killed; the inert control (reordering `INDUSTRY_WORDS`)
  survived**, so the sweep discriminates rather than reddening everything.
- **The published 0.1.0 predates the round-43 fixes.** `git
  merge-base --is-ancestor` puts `6424410`, `ca14621` and `1430af6` *outside* `677d545`
  (the release commit, 08:48) — they landed 09:24–09:44. So the crate on crates.io
  carries the quality-reader defects round 43 fixed: a `CochraneStudyAssessment` read
  with an absent `risk_of_bias` fabricates nine "Unclear risk" domains, `"45.5"` reads as
  a count of 45 and `"nan"` as 0, the Tier 2/3 float reader accepts `"inf"`, and
  `f64::clamp` stores `-0.0` where Python stores `0.0`. **Nothing on `main` is wrong —
  the release is simply older than the fixes.** #332's own note says to close it *"with
  the next release"*, and this is that release.
- **One undocumented divergence, found while starting the `default_cache()` gap.**
  `fulltext/cache.rs::default_cache_dir` reads `HOME` on every platform and falls back
  to `PathBuf::from(".")`. Python reads `HOME` only on POSIX — on Windows it consults
  `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`, and **never** `HOME` — and where no home
  can be determined `Path.home()` raises, which `FullTextService` catches and degrades
  to no caching (see `docs/manual/fulltext.md` §"Default cache directory" and
  `docs/DECISIONS.md` §"fulltext — the service degrades but the cache still raises").
  Rust therefore writes the cache **into the process's working directory** in the case
  where Python caches nothing, and finds a different home on Windows. It is not in §9,
  so by §0 it is a bug. Closing it needs a decision, because `default_cache_dir()`
  returns `PathBuf` and cannot express failure — see *What is left*.

**Documentation drift, repaired.** The header above said `main` was at `de513f3` and the
figures were one round stale (827/835 tests, 67,767 lines, 2,552 cases). All are
re-measured here. `rust/README.md`'s status table had **five rows duplicated** —
`http`, `fulltext/service`, `transparency/analyzer` and both `pdf_converter` halves
appeared twice, and the two `fulltext/service` rows disagreed (30 named tests where the
file has 42) — which is the "two copies that drifted apart" hazard this repository
keeps catching; the second block is deleted.

## Session note (round 47/48) — Python adopted #313, and a regenerated oracle that was green and hollow

**Two reviews of the same instrument, and both found the *test* rather than the code.**

**Python's PR #347 adopted four of the port's filed corrections** (#306, #307, #313 and #296).
The oracle re-run caught the one that mattered to a corpus: `dump_openalex.py` went stale on its
single `corrected` case, `fetch/count-bool-is-not-a-count` — Python now refuses a boolean
`meta.count` itself, with the port's own sentence. The block is **retired**, the case diffs
strictly, and the named test reworded from *"corrected from Python (#313)"* to an agreement. The
plan's Appendix records the adoption beside the row.

Note what the port's own test did when Python moved: `openalex.rs` asserted
`assert_ne!(want["value"], payload, "…so Python has changed")`, and it kept passing — because
`want["value"]` comes from the *committed* expectation, which was still the old Python. The
assertion only fires once the corpus is regenerated. **The oracle re-run is the detector, not the
test**, which is the fourth time this file has had to say so.

**The other finding was about work in this file, and it is the sharper one.** #348's oracle
regeneration was reported as *"40/40 clean"* and was **green and hollow**: #343 made a DOI
mandatory, the `fetch/*` fixtures carried none, and so **10 of 22** cases failed on their first
record and never reached the stall, shortfall or unreconcilable rule each is named for. A
regenerated expectation agreeing with a port that agrees with itself is not evidence — the
fixtures had to be given DOIs so every case reaches its subject again, and a failed walk now
**keeps the records it delivered** rather than reporting none. Both are `b50a350`.

The lesson to carry: **a `corrected` block and a regenerated corpus are the same instrument
facing opposite ways.** One is stale when Python moves towards it; the other is vacuous when the
fixtures move away from it. Ask, of both, *"would this still fail if the behaviour were wrong?"* —
and check that a case marked green actually reached the rule it is named for.

## Session note (round 46) — the rendering hooks were dead, and the condensation seam had no implementation

This closes the last item on round 44's list, and it took two defects to get there. Both
were found by *building* what the gap was about rather than by reading it.

**The hooks the last item needed were unreachable.** `IterativeContextProcessor`'s
`format_item` and `format_consolidated_item` had **no call site anywhere in the crate**:
`render_one`'s default body was `item.render(index)` while its own doc comment described
the routing. Measured, not read — a probe whose `format_item` panics ran a whole
`run_all` without reaching it, and the extractor was handed the bare `Item::render`
output. Two tests *appeared* to pin the hooks and passed with them dead:
`an_item_is_measured_at_the_position_it_lands_in` batches apart because the default
separator is nine characters wide, not because its decoration grows, and
`truncating_does_not_decorate_twice` passes because `Preformatted` is what its strategy
produces.

`Item` now declares its own routing — `ItemRouting::{Processor, Consolidated,
Preformatted}`, defaulted so existing implementors compile unchanged — and `render_one`
routes as Python's `_format_one` does. **Behaviour is unchanged for every processor in
the tree** (all 853 pre-existing tests passed unchanged), because their defaults coincide
with `Item::render`; what changes is that a processor that decorates has its decoration
delivered.

**A second defect fell out of making the hook live.** `split_to_fit` did
`budget -= widest - limit` on a `usize`, so a decoration wider than the whole budget went
below zero and **panicked** in a debug build — and wrapped in a release one, a different
answer for the same input. Python's budget goes negative and its `budget <= 0` guard ends
the search, skipping the item with *"no split budget small enough to fit the
decoration"*; saturating at zero reproduces that in both builds.

**Then the seam got an implementation.** `LlmChunkProcessor`
(`context_processor::llm_processor`) binds the ported harness to a `ContextModel`, and
`LlmCondenser` (`quality::cochrane_assessor`, beside its own trait) is the production
`Condenser`: `CONDENSE_EXTRACTION_PROMPT`/`CONDENSE_CONSOLIDATION_PROMPT` over
`CONDENSE_QUERY`, `use_structured_output` false as Python leaves it. Until this, the only
`Condenser` in the crate was a test stub, so *"the condensation map-reduce runs only
against a stub"* was exact and a caller outside the test suite had no way to condense at
all. Nothing else had been missing: the harness, the two prompts, the query, the
per-batch call and the answer readers were all ported and tested — the absent part was
the binding. **The reduce stage's `[Consolidated level N, item M]` header is exactly what
the dead hook was hiding**, which is why the two defects are one story.

Also added: `ChunkItem` (Python's two renderings, score kept on every split piece, which
`ConsolidationStrategy::Weighted` sorts on) and `ProcessingResult::failed` (a run that
never started, as opposed to an extraction that failed).

**Eleven new tests, 10 mutants killed** across the two commits — the suite went 853 to
864 — each fix reverted, with the two inert controls surviving (a reordered `match`, and
reordered struct fields). Gates: 864 default, 872 `pdf`, 874 `postgres`, 882
`--all-features`, clippy 0 warnings on every feature set, `cargo fmt --check` clean, and
all 40 corpora still regenerate and match.

**Release sequencing.** This is stacked on #340 and both PRs merge **before** anything is
published, so the version literal is the 0.2.0 that #340 set: one release carrying
everything since 0.1.0. Round 46's changes are additive — `ItemRouting`,
`LlmChunkProcessor`, `ChunkItem`, `LlmCondenser`, `ProcessingResult::failed` — apart from
the harness now honouring the hooks its own documentation describes, which is the part
that moves behaviour. The changelog's `### Fixed`/`### Changed` entries for them belong
under the same 0.2.0 heading, not a version of their own.

## Session note (round 45) — the cache directory, and the test that was hiding a defect

Round 44 closed with one open decision. This is the answer to it. It was first cut as a
separate PR so that the quality-reader fixes could ship as a 0.1.1 patch with no API
change; round 46 collapsed that plan, so both PRs now merge before **0.2.0** is published
once.

**The defect.** `default_cache_dir` read `HOME` on every platform and fell back to
`PathBuf::from(".")`, so a process with no home directory wrote its cache into whatever
directory it happened to be started in. Python reads `HOME` only on POSIX —
`ntpath.expanduser` consults `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`, and **never
`HOME`** — and where no home can be determined `Path.home()` raises, which
`FullTextService` catches and degrades to no caching. So the port diverged twice: a
different directory on Windows whenever a POSIX-flavoured shell had set `HOME`, and a
*cache* where Python has none. Neither half was in §9, so by §0 both were bugs, and
`docs/DECISIONS.md`'s *"No fallback cache location"* settles the direction.

**Why the missing test mattered.** The gap was on record as *"`default_cache()` has no
test (it would mutate process-global `$HOME`)"* — and that objection was the defect's
cover. The only way to reach the no-home case was to unset `HOME`, which is racy under
`cargo test`'s threads, so nobody did, so the `"."` fallback was never exercised.
Taking the environment lookup and the home directory as **arguments**
(`Platform::home_from(impl Fn(&str) -> Option<OsString>)`, `cache_dir_under(&Path)`)
makes all three platforms testable from one machine with no global state — and the
`Option` return is what makes the relocation unrepresentable rather than merely fixed.

- **`default_cache_dir() -> Option<PathBuf>`**, **`FullTextCache::new(Option<PathBuf>)
  -> Option<Self>`**, and **`impl Default for FullTextCache` removed**: Python's
  `FullTextCache()` raises where there is no home, and an infallible `Default` could only
  panic or invent a directory. That is three signatures, so the release is 0.2.0.
- **`default_cache()`'s two causes now have two sentences**, as Python's guard does — a
  caller who cannot find a home directory is not helped by being told to choose a
  writable location. Its body is `default_cache_at`, a private helper whose both arms a
  test reaches; the public function still takes no parameters, which is what keeps the
  degrading path unreachable for a caller who supplied a `cache_dir`.
- **Ten new tests; 6 mutants, each fix reverted, all killed, and the inert control
  (swapping the two `mkdir` calls) survived.** The Windows arms are exercised for real:
  `USERPROFILE` first, then the `HOMEDRIVE` + `HOMEPATH` **string concatenation** — not a
  path join, so a rooted `HOMEPATH` keeps the drive — and `HOME` ignored even when set.
- **One divergence survives and is in §9 now**: the POSIX arm reads the environment and
  not the passwd database, so a POSIX machine with a passwd entry and no `HOME` caches
  nothing here where Python caches under that entry. Reaching it needs `libc` and
  `unsafe` for a case no deployment has been shown to hit.

The suite is **853** default (843 + 10), clippy and fmt clean on every feature set, and
all 40 corpora still regenerate and match.

## Session note (round 42) — the PostgreSQL backend, against a real server

The one thing `rust/README.md` still listed as *not yet done* in the code half
was the backend `Dialect::Postgres` had never had: the kind, the numbering, the
catalog SQL and the PostgreSQL DDL all existed and were exercised through a
simulated connection, but nothing could open a socket. It now can.

- **`db/postgres.rs`**, behind an optional `postgres` feature (matching Python's
  optional `psycopg2` extra, so a SQLite-only caller links neither a Postgres
  client nor its async runtime). `postgres` is the *blocking* wrapper, so the
  sync `Db` trait stays sync. It needs **two** `Db` impls, not three: the crate's
  `Transaction` covers a savepoint as well, and `Transaction::transaction()`
  opens the nested one.
- **Ten live tests** in `tests/postgres_live.rs`, each creating and dropping its
  own database, gated on `BMLIB_PG_TESTS=1` exactly as the network suite is
  gated on `BMLIB_LIVE_TESTS`. They cover the boolean mapping, `SERIAL` +
  `RETURNING id`, nested-savepoint rollback, migrations, the publications
  schema, and child reparenting.
- **Three defects the run found**, all invisible to every existing test and all
  written up in `rust/README.md` §"What the live PostgreSQL run found": a Rust
  line continuation that turned `information_schema.columns WHERE` into
  `columnsWHERE` (and three sibling SQL strings in `publications/storage.rs`),
  `postgres::Error`'s `Display` reducing every server rejection to the bare
  string `"db error"`, and the `PgSim` catalog shim that could not have caught
  the first. Fixing the second is what made the first legible.
- **The Python library is still untouched.**

The pattern is the one the DTD defect taught: a full suite of scripted fixtures
proves the code matches the fixtures, not the service. Both times the service
disagreed.

## The method, which is the part worth keeping

Every module was ported against a **differential oracle**, not written to match a
reading of the Python:

1. Write `rust/oracle/dump_<thing>.py` that runs the **live Python** over a JSON
   case corpus on stdin and writes expectations on stdout.
2. Commit the cases *and* the expectations under `rust/bmlib/tests/data/`.
3. The Rust test diffs **parsed** values, never text.

**All 40 corpora regenerate from the live Python and match what is committed** —
with the round-41 and round-43 corrections above: this was *not* true when first
written, and the re-run is what found it both times. That is what makes them
evidence rather than fixtures, and it is now one command:

```bash
cd /Users/hherb/src/bmlib
.venv/bin/python scripts/rerun_rust_oracle.py            # report drift
.venv/bin/python scripts/rerun_rust_oracle.py --write    # regenerate in place
```

It diffs each dumper's output against the committed expectation **parsed**, and it
also fails if a corpus keeping two copies of its cases (see below) has let them
drift apart. Before it existed the recipe was per-corpus and hand-typed:

```bash
.venv/bin/python rust/oracle/dump_cache.py < rust/bmlib/tests/data/cache_cases.json \
  > /tmp/fresh.json
diff <(python3 -m json.tool /tmp/fresh.json) \
     <(python3 -m json.tool rust/bmlib/tests/data/cache_expected.json)
```

Re-run it before believing anything below — and note that a
case edit must land in the copy the test reads: **most corpora keep two copies of
their cases**, one under `rust/oracle/` (the dumper's input in this recipe's
shape) and one under `rust/bmlib/tests/data/` (what `include_str!` pulls in), and
the two must stay identical. There are two exceptions: citations is
`oracle/cases.json` against `tests/data/citations_cases.json`, and `service` has
no `oracle/` copy at all. A **stale oracle is worse than no oracle**: it agrees
with a port that has drifted.

A case may carry a `corrected` block where the port deliberately differs, with its
reason. Those are the §9 divergences and they are the only differences to accept
silently — **unless Python has since adopted the fix**, in which case the block is
a stale note that must be retired, not kept. `json`'s four #299 corrections and
`protocol`'s #315 one were retired that way in round 41, and `cochrane`,
`cochrane_assessor`, `formatter` and `quality_llm` retired all 22 of theirs in
round 43 when Python's quality-narrowing batch adopted #295, #310, #312 and
#317–#320. **A retirement is not always the whole fix**: of round 43's 22, four
turned out to be *port* defects Python had moved past, so a stale block can be
hiding a wrong implementation rather than a stale expectation — ask which side
moved before regenerating.

## What is left

**Nothing that blocks a release.** Rounds 52-59 landed on `main` through **#380**
(`fix/rust-land-rounds-52-56`), so the stranded-stack item that used to open this list is
closed.

1. **Cut the 0.3.0 release when ready.** `main` holds the whole port plus rounds 52-61; round 62
   (the JATS cited-name and NLM-citation follow-ups, #388 and #395) is on
   `fix/rust-jats-387-394`. The only breaking change outstanding is #371's `Fetcher` trait,
   which is what makes the next release a minor rather than a patch. Follow *Publishing to
   crates.io* above: land anything new as a PR (the `protect_main` ruleset wants CodeQL results
   for the exact commit), merge, then publish from the merge commit. **Before releasing, re-run
   `scripts/rerun_rust_oracle.py`** — a corpus regenerates against the Python at the tip, and
   a stale expectation agrees with a library that has moved.

### 1. Deliberate scope decisions, documented — do not "fix" these

§"What is deliberately dropped" of the plan covers them. In short:

- **Ollama's `list_models`/`show()` metadata** (~600 lines of Python; it exists
  because the Ollama SDK's Pydantic model dropped two fields).
- **Streaming tool-call assembly** — the plan calls it *"the fiddly part"* and
  expects less than the Python does.
- **`list_models`, `test_connection`, `count_tokens`, `embed`/`embed_batch`,
  `get_model_metadata`, `get_provider_info`** are absent. `chat` is complete
  including `tools`, `tool_choice`, `think` and `json_mode`. `count_tokens`'s
  accuracy was never established downstream, which is why it was not ported as-is.
- **No process-wide `get_llm_client()` singleton**, with the reasoning on
  `LlmClient` itself: the Python singleton made construction cheap (an SDK import,
  environment credentials, a pool) and `LlmClient::new` does none of that.

### 2. Documentation drift to repair

- **Fixed.** The plan's §"Phase 4" no longer says the PDF backend is
  "outstanding" — `4a3593e` corrected it when this handover was added. The
  round-41 pass corrected the rest: the plan's §9 no longer lists #315 as a
  divergence, its Appendix records the seven defects Python has since adopted,
  and `rust/README.md`'s oracle section and *Not yet done* list were brought in
  line with the code — `RETURNING id` **is** implemented (in
  `insert_publication`), and `db/` is the only package without a corpus.

### 3. Verification that would raise confidence, not features

These are real and open, and each is a *measurement* rather than an implementation:

- **The network live suite is not a CI gate, deliberately.** An outage, an egress
  block or a rate-limit would redden it for a reason that is not this code. If you
  want it scheduled on top of the weekly run, a `workflow_dispatch`-style job is
  the shape — but decide whether a red run would mean anything before adding it.
- **The PostgreSQL suite *is* a CI gate now**, and the difference from the line
  above is the whole argument: a server it may create databases on is something CI
  can *provide* deterministically — the `rust-postgres` job in `ci.yml` runs over a
  `postgres:16` service and sets `BMLIB_PG_TESTS=1` — whereas NCBI's rate limiter is
  not. Run it the same way locally before a release that touches `db/` or
  `publications/`; it found three defects on its first run.
- **`TransparencyResult::to_dict`/`from_dict` diverges from Python on
  `coi_disclosed`** (#306's correction reaching the persistence path): a row with no
  `coi_disclosed` reads back as `None` here where Python's dataclass default gives
  `True`. Intentional and recorded; a downstream round-tripping rows across the two
  implementations sees it.
- **The coverage gaps this list used to name are all closed**, and each was larger than
  the sentence that listed it — do not re-open one without reading the note:
  - **`default_cache()`** — round 45. The missing test was covering a defect: the
    function substituted `PathBuf::from(".")` for a home directory it could not find,
    so a process with no home cached into its working directory where Python caches
    nothing. Fixed as the breaking change 0.2.0. What remains open is only the
    divergence §9 records — the POSIX arm reads the environment, never the passwd
    database.
  - **the funder-count measurements** — round 44. Eight of the twenty-five canonical
    rows are tokens considered and *refused*, in neither tuple, so re-adding one
    changed no measured count and the name-agreement oracle stayed green.
  - **the condensation map-reduce** — round 46. Two defects stood behind it: the
    harness's rendering hooks had no call site anywhere in the crate, so a processor
    could not decorate a consolidation level at all; and `split_to_fit` underflowed on
    a `usize`, panicking on a decoration wider than the budget.
- **`HttpResponse.body` is `Vec<u8>` and the live backend is exercised, but no
  test drives a real provider chat call.** The LLM transport is scripted. A live
  chat test needs a key and would cost money, which is why it does not exist; if
  you add one, gate it exactly as `live_network.rs` is gated.
- **0.2.0 is published; the next release is planned.** It went out from `0efd488` carrying what
  `main` held then — **not** the whole stack, because the stack's PRs merged into the branches
  beneath each other rather than into `main` (see *Publishing to crates.io*, which now says how
  to check that the hard way: ancestry, not GitHub's state). **The next release carries the
  branches above** — #354, #359, #361, #365 — **plus #371-#374**, and **#371 changes the
  `Fetcher` trait**, so it is a **0.3.0**: `fetch` takes a `FetchSink` and
  `FetchOutcome::records` becomes a count. Publish it from the last merge commit once the stack
  lands, with the `~/.cargo/credentials.toml` link remade (it is removed after each release) and
  the changelog's `[Unreleased]` section renamed to the version. #332 is closable from it, as
  0.2.0 already was.
- **No Rust issue from rounds 49–51 is still open** — round 61 closed #354, #359, #361, #365 and
  #366 on the strength of #360, #363, #364, round 51's PR and `3c695f7`, each re-verified against
  `origin/main` first. **#332** stays open deliberately: 0.3.0 closes it. **#388 and #395 were
  closed the same way in round 62**, and **#356 in round 63**, ported rather than answered — so
  the port has **no open behavioural gap** left.
- **What is left after them, in the order this file would take it:**
  1. **The two §9 diagnostics gaps** (Rule 5's unreadable row, the planner's "counts moved"),
     which the maintainer decided in round 50 to leave as recorded divergences.
  2. **The PubMed/`sync` residue of the transport channel**, below.
  3. **A live end-to-end `sync()`.** Each half is tested — `live_network.rs` reaches the real
     bioRxiv, PubMed and OpenAlex endpoints through the transports, and the fetcher layer is
     tested over scripted ones — but nothing runs `sync()` against a live source through
     `builtin_registry`, deliberately: it would write to a database from a test that cannot
     run offline.
  4. **The doc-comment backlog beyond the links.** `cargo doc` is now clean and gated, but it
     checks *links* only: a `# Errors` section naming the wrong failure, or prose that has
     outlived its code, is invisible to every gate the port has. Round 55 found six of those
     by reading; there is no instrument for the rest.
  5. **The four open JATS defects are pinned as reproductions** (round 64) — #385, #393, #396
     and #397 each have corpus cases, a named test and a `QUIRK:` comment at the site that causes
     them, so Python's eventual fix arrives as a changed expectation the port must follow. What
     remains for them is Python's fix, not port work. The next JATS candidates are the other open
     issues — #314 (a `<mixed-citation>`'s deposit glues a surname to its given names), #291, #290,
     #288, #287 and the rest of the list — each worth a reproduction when a round has room.
  6. **#356 is done** (round 63): the caller's PMC ID, the cached PDF's abstract and the cache
     key all follow Python's PR #355, and the plan's §9 row for `_safe_filename` is retired
     with the cache corpus's last `corrected` block.
- **The transport-failure corpus channel is in, for two of the four tables.** `biorxiv` and
  `openalex` carry `fetch/transport-error`; the **PubMed transport's table and `sync.rs`'s** are
  still pinned by named tests alone, because the PubMed oracle deliberately does not diff
  request-shape cases and `sync.rs`'s table is reached only through a `Fetcher`'s `Err`. That is
  the shape #349 found, narrowed rather than closed, and §9 says so.
- **The round-47/48 gap generalised, three rounds running.** #349's case, #354's PubMed path,
  #359's planner — and, inside #359, the `plan/unsplittable-measured` case that was *already*
  vacuous before it was touched: a corpus case **named** for a rule and never reaching it.
  Before adding a case, ask what its Python answer actually is — not just that Rust agrees
  with the committed expectation, which a stale or vacuous fixture makes true either way
  (round 41, round 43, round 47/48, round 49, round 50). The cheapest version of the check for
  a fixture that scripts a **term** (a search term, a range key, a URL) is one line: compute
  the key the case will actually ask for and see whether the fixture carries it.
- **Python's #343 has landed, and it moved the port.** The oracle re-run is what found it:
  `dump_biorxiv.py` was stale in **all 43 cases** while `cargo test` was **green**, because the
  committed expectation had been dumped from the pre-#343 Python — the port and its fixture
  agreed with each other and disagreed with the library. Round 47 ported the two halves that
  are **in**:
  - `normalize` emits `extras["published_date"]` and `["published_journal"]`, and the five extras
    now follow Python's *expressions* rather than readings of them (`_field`'s truthiness,
    `.get(k) or ""`, and `.get(k, default)` keeping a present `null`) — which fixed two
    pre-existing divergences the new cases exposed.
  - **A record with no DOI fails the day**, before that record is kept.
  - **Its review found the regenerated corpus hollow**: #343 made a DOI mandatory and the
    `fetch/*` fixtures carried none, so 10 of 22 fetch cases expected the DOI refusal on their
    first record and no longer reached the stall, shortfall or unreconcilable rules they are
    named for — green, and testing nothing. Every fixture record carries a DOI now, and 17
    cases were added (64 in all), pinning each arm of `truthy` and `field_value` that twelve
    surviving mutants showed unpinned.
  - **A failed walk keeps the records that arrived before the failure** (`walk_into`). Python
    has already handed them to `on_record`, and `record_count` counts them; the port discarded
    its buffer on every `Err`, so a day failing on page 2 stored nothing from page 1 — 0 where
    Python says 100. OpenAlex's walker already kept them, so bioRxiv was the odd one out.
    `walk`, the strict form, still discards them and says so.
  The §9 row that recorded the `/pubs` divergence is **retired**: Python made the same correction.

- **`settle_days` is ported, both halves** — and it was worth the round: making room for the
  rule found a divergence in the port's own use of `chrono`.
  - **The descriptor half** (#348): `SourceDescriptor::settle_days` (a `u32`, `0` by default),
    `MAX_SETTLE_DAYS = 3650`, `check_settle_days()`, a validating `with_settle_days` builder,
    the re-check in `Registry::register` (now fallible), and `BIORXIV_SETTLE_DAYS = 90` on both
    preprint descriptors. Python also refuses a boolean, a non-integer and a negative; a `u32`
    cannot hold them, and §9 records the three unreachable refusals.
  - **The day-selection half**: `day_was_over_when_fetched` takes the period and compares a
    **difference** (`fetched_at - day_over_everywhere >= settle_days`), never
    `boundary + settle_days` — rule 5 reads rows of any date, and adding to a day near the end of
    the calendar overflows *outside* every per-day handler; rule 5 itself, gated on
    `settle_days > 0`, offers every row outside the window that is not `completed` or whose
    completed day has not settled, a **failed** row included, and sorts the two lists together;
    `sync()` resolves the period and skips a source whose descriptor declares an unusable one,
    with a line; and the row load is **every row for the source**, since Python's bounded window
    query plus its deliberately unbounded outside query are that set.
  - **The divergence that fell out**: the end-of-calendar guard was `NaiveDate::MAX` — year
    262143, because chrono's calendar is wider than Python's — so a row carrying Python's
    `date.max` (9999-12-31) compared unequal to it and would have been re-offered on **every**
    run for ever. The bound is Python's, and it is the bound because the durability rule needs
    the day *after* it, which Python cannot represent. The oracle caught it on the first run of
    the new cases; four named tests state the rules' reasons.
  - **One diagnostics gap, recorded in §9 rather than hidden**: Python logs a WARNING for a row
    outside the window whose date cannot be read or is `date.max`; the port skips the row
    silently, having no logger in that module and no report to write to. Which days are selected
    is identical.

- **A regression in the port cannot be caught by the port's own name-agreement oracle
  alone.** `tests/funder_matching.rs` is the worked example: the agreement oracle passes
  for any tuple edit the corpus cannot see, and only the stated-evidence rows catch it.
  Worth asking of any other module whose *rules* are carried as prose — `transparency`
  and `quality/extractors` are the two with tables of this shape. **Round 59 supplied a
  second worked example, from the other direction**: dropping the extractors'
  mention-candidate sort survived every test, because it moves no boolean and only the
  *excerpt* the audit trail quotes. A corpus keyed on `DimensionScore` sees it; one keyed
  on the answers would not.

## Open Python-side issues the port surfaced

**These are Python work, not Rust work, and they are the most valuable things this
session produced.** Each came from an instrument rather than a reading.

- **[#385](https://github.com/hherb/bmlib/issues/385), [#393](https://github.com/hherb/bmlib/issues/393),
  [#396](https://github.com/hherb/bmlib/issues/396) and [#397](https://github.com/hherb/bmlib/issues/397)
  — filed by round 62's review, open, and the port reproduces all four (round 64).** #385 is the
  doubled period after `et al.` in both renderers — 150,831 of 356,304 references (42.3%) in 5,921
  of 8,118 served articles; #397 is a cited identifier classified by shape rather than the
  declared `pub-id-type`, so a six-digit PMID (11,242 of 1,190,287 declared `pmid` values, 0.94%)
  is refused in `<element-citation>` and `<mixed-citation>` alike; #393 is the empty `<li>` an
  element-only citation renders when every child is one no field reads — the web reference, the
  conference deposit and the `<comment>`-held reference are all pinned; #396 is the larger
  population where such a citation renders and silently loses an edition, a publisher or a
  comment, with the audit asserted empty. Each now has corpus cases, a named test and a `QUIRK:`
  comment, so Python's fix forces the port to follow.
- **[#386](https://github.com/hherb/bmlib/issues/386) — filed in round 61, open, Python
  documentation only.** `biorxiv.py`'s module and `BASE_URL` docstrings still state, in the
  present tense, that `/details` answers a zero-byte body. It came back on 2026-09-27 (PR
  #362), and round 61 re-probed six URL shapes — all HTTP 200 with JSON, the date-interval
  shape `biorxiv.py` builds with 64,657 bytes for bioRxiv and 75,608 for medRxiv. The stale
  sentence is the premise #325's option-2 decision rests on, and #341 inherits the gap it
  created.
- **[#390](https://github.com/hherb/bmlib/issues/390) — filed from PR #389's review, open,
  and the port has the same gap.** An NLM 2.x `<citation>` (or `<nlm-citation>`) reference
  parses with **every field empty**, because `_CITATION_ELEMENTS` lists only the two JATS
  spellings: 3,681 of 3,681 in the served back-file bundle `PMC100320_PMC107849.xml.gz`, and
  81,681 references in 2,296 of 3,028 `PMC000` articles. The recent windows carry none. Most
  back-filled bibliographies are empty in the model and the HTML. Mixed-content reading is the
  nearer rule; the choice needs the blast-radius diff. The port mirrors `CITATION_ELEMENTS` and
  follows whatever Python decides.
- **[#391](https://github.com/hherb/bmlib/issues/391) — filed from PR #389's review, open.** A
  `<mixed-citation>` or `<element-citation>` printed in a `<p>` outside a `<ref>` is cut out of
  the sentence whole — #255's mechanism beyond #255's front-matter self-citation shape: 31 of
  8,118 served and 387 of 97,909 PMC012 articles (body, back matter, the abstract), a floor
  since a citation carrying no name part is not counted. The port reproduces it.
- **[#382](https://github.com/hherb/bmlib/issues/382) — filed by the #381 work, measured in
  round 61, and the port reproduces it.** A structured `<name>` printed in prose loses its
  `<surname>`/`<given-names>` from the sentence. Its own shape — contributor prose, or a
  paragraph outside any citation — is **0 in all 136,570 documents** of the four named
  artifacts; PR #387 closes it on that and on the Tag Library, which admits `<name>` in no
  `<p>`. Every `other` name is a `<principal-award-recipient>` (#288, open). The first cut of
  the instrument mislabelled the rest (3.18M NLM `<citation>` names as read, 538,631 dropped
  `<element-citation>` names as #314's glue); the round-61 note has the corrected table. The
  port pins the reproduction: `prose/382-a-name-in-a-body-paragraph-is-lost` and
  `a_name_in_body_prose_is_lost`.
- **[#366](https://github.com/hherb/bmlib/issues/366) — answered, and one part of
  it was a port defect.** It was filed from Python's extractor audit and names
  three things: an exclusion window that dropped the keyword, a contrastive veto
  that was net-negative, and a negation window that removed genuine CI reports.
  The first was **the port's** — `has_exclusion_pattern` ended its window before
  the keyword, where Python's includes it, so 27 `Controlled Clinical Trial`
  abstracts read as `rct` — and it is fixed in round 59. The other two are what
  Python's #370 decided, and the port now follows both. No Python change was
  needed for any of the three; the issue is commented with the outcome.
- **[#376](https://github.com/hherb/bmlib/issues/376) — fixed in Python by PR #355; the divergence
  is retired.** `fulltext/cache._safe_filename` passed an over-long *safe* identifier through
  where its own docstring said it sanitized it, so a 161-character identifier addressed two cache
  files. The port followed the docstring, pinned by a `corrected` block on `safe_filename/161`.
  Python's #309 part 1 moved the guard to `_MAX_KEY_CHARS` (171, the longest key the sanitiser
  returns) in the same PR, which is what the docstring always claimed — so the two agree, the
  `corrected` block is gone, and `safe_filename/160`, `/161`, `/171` and `/172` are all strict
  oracle cases now. The issue can be closed. **The mechanism stays**: the oracle runner is a CI
  step, which is what would have caught the original drift on the commit that caused it.
- **[#325](https://github.com/hherb/bmlib/issues/325) — closed, and the port followed
  Python onto the endpoint it chose.** `biorxiv.py` read `/details`, which answered **HTTP 200
  with a zero-byte body**, so **every bioRxiv sync day errored**; Python moved to `/pubs`,
  whose field names differ (`preprint_doi`, not `doi`) and whose population is the published
  pairs — 34 for 2024-01-15 where `/details` declares 207. The port follows (`BASE_URL` is
  `/pubs`, `normalize` reads both spellings). **`/details` came back on 2026-09-27**, found by
  the port's own live suite, so the narrowing is a live choice again and is
  **[#341](https://github.com/hherb/bmlib/issues/341)**. Round 61 repaired the port's stale
  prose about it and filed **#386** for Python's.
- **[#317](https://github.com/hherb/bmlib/issues/317)–[#320](https://github.com/hherb/bmlib/issues/320)
  — fixed in Python** (`07335c1`, `d4a82a0`, 2026-09-27). Four type-contract defects
  in `cochrane_models` and the quality readers, found by the delegated ports and
  verified independently before filing. #317 was the interesting one:
  `COCHRANE_RESPONSE_FORMAT` tells the model *"Use null for any field the text does
  not report"*, so a **compliant** model reached `data.get(k, default)` returning
  `None` for a `str`-annotated field. The fix covered #295, #310 and #312 as well,
  which is what made this port's four corpora stale and turned up the three port
  defects round 43 fixed — see the session note above.
- **[#316](https://github.com/hherb/bmlib/issues/316) — fixed.** It was a defect in
  the port, not the Python: `HttpResponse.body` was a `String`, so a binary PDF
  reached the cache with every non-UTF-8 byte replaced by U+FFFD, **undetectably**
  (`%PDF` is ASCII and survives; the cache's only check is that prefix).
- **[#332](https://github.com/hherb/bmlib/issues/332) — answered in round 43**, and
  it is the one issue that was *about* the port: two quality readers reproduced
  Python behaviour the narrowing batch changed, and it asked the port to decide
  whether to follow. It follows — `docs/DECISIONS.md` had already ruled on the
  interesting half. Close it with the next release; the three divergences it names
  are now agreements.

## Gotchas that cost this session real time

Each of these is written down because it produced a **wrong answer**, not merely
lost minutes.

- **`cargo`'s coarse mtime staleness.** An edit and a rebuild inside the same
  second can read a stale rlib, so the binary behaves like the old code. It bit
  this port **twice**. When behaviour looks impossible, `cargo clean -p bmlib`.
- **A test count is only a fact when it comes from a clean worktree.** `main` was
  recorded as 885 for a round; a clean `git worktree add --detach` says **888**,
  reproducibly, and two more counts were wrong in the same round (a branch figure of
  917 that is 918). The wrong numbers came from `cargo test` in a **shared** `target/`
  right after a `git checkout` of another branch. What makes it dangerous is that it is
  *intermittent*: the merged-result figure taken the same way matched its clean
  measurement exactly, so the method looks sound until it is not. Measure in
  `git worktree add --detach /tmp/check <ref>`, run there, `git worktree remove` — a
  rebuild of minutes buys a number nobody has to re-derive.
- **Check the gate by grepping the whole output, not a pipe.** Counting
  `^warning:` on a partially-consumed stream reported "clean" while four warnings
  existed. It fails in the **reassuring** direction, which is the worst kind.
- **Concurrent edits produce phantom failures.** While subagents worked, an exit
  code 101 with no matching failure line was a mid-write file, not a regression.
  Re-run before investigating.
- **A process-wide global makes tests order-dependent.** The recording tests in
  `cost.rs` take a lock, because `TokenTracker` is process-wide and two tests
  interleaved their before/after snapshots. Assert **deltas**, and serialise.
- **Mutation testing: assert the pattern matches exactly once, and restore from a
  separate backup.** A `cp`-based restore once clobbered a test file with an
  early snapshot. Brace-balancing mutations often fail to apply — a mutation that
  does not apply is not a passing test.
- **`Document::parse` in `roxmltree` refuses a DTD by default.** Every live NCBI
  response carries one, so the whole PubMed path was dead against the real service
  while every fixture passed. `parse_ncbi_xml` sets `allow_dtd`. **This is the
  single best argument for the live suite.**
- **PDFium's `font_weight()` and `font_is_italic()` return nothing usable for a
  base-14 face** (measured against PyMuPDF on the same files). Bold and italic are
  derived from the **font name**. The measurement is in the module docs.
- **`ruff`'s version matters.** `.venv` has 0.15.1; CI pins **0.15.20**. The older
  one flags `UP012` in a file this work never touches. Use the pinned one:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

## If you are starting fresh, do these in order

1. **Read `rust/README.md`, then the plan's §0 (fidelity) and §9 (divergences).**
   A Rust/Python difference that is *not* in §9 is a bug.
2. **Re-run every oracle** against the live Python:
   `.venv/bin/python scripts/rerun_rust_oracle.py`. If one is stale, that is a
   real find and more important than anything you were about to build — and ask
   **which side moved**: round 41's staleness was an expectation describing an
   old library, but round 43's was a *port defect* the expectation was hiding.
3. **Run the three gates on a clean build** — `cargo test`, `cargo clippy
   --all-targets`, `cargo fmt --check` — plus `cargo test --features pdf` with
   `PDFIUM_BUNDLED_CACHE_DIR` pointed inside the workspace (a sandbox denies the
   platform default).
4. **Run the live suite once** with `BMLIB_LIVE_TESTS=1 --test-threads=1`. It found
   a service-level defect within a minute of existing; it is the cheapest
   high-yield check in the repository.
5. Then pick up from *What is left*. **Nothing there is urgent**, so prefer the
   verification items over the documentation ones, and treat #325 as the highest
   value work — it is a live defect in the Python library.

## The one thing not to do

**Do not modify the Python library as part of port work.** The port's brief was
functional equivalence to a *corrected* bmlib, and the corrections are an enumerated
list (#294–#309). A defect outside that list was **reproduced and filed**, never
fixed in place — that is why #316–#320 and #325 exist as issues rather than as
diffs. The one deliberate exception on the Rust side was the bioRxiv URL, made on
explicit instruction and recorded in §9 — retired in round 47, when Python's #343 made the
same correction.
