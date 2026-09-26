# Porting bmlib to Rust — roadblocks

_Date: 2026-09-26. An analysis pass over bmlib 0.10.0 at `9dc981a`, to say what
stands in the way of a Rust port **before** any of it is written. It is a
roadblock register and a proposed order, not a plan of record and not an
estimate anyone should quote without reading "What this does not establish"._

Companion evidence already in the tree: `spikes/db-rs`, `spikes/db-async` and
`spikes/publications-rs` — three Rust crates built earlier, whose `FINDINGS.md`
files are the only measured Rust evidence this repository has.

---

## The scope, measured

| | Python |
|---|---|
| `bmlib/` | **19,727** lines across 50 files |
| `tests/` | **66,823** lines across 50 files |
| `tests/data/` | 11 MB, 8 files (JATS corpora, funder labels, PDF-title corpus) |
| public functions | 796 |
| `@dataclass` types | 85, with 28 `to_dict` / 28 `from_dict` pairs |
| `@property` | 65 |
| `enum` classes | 11 |
| `logger.*` call sites | 214 |
| `re.compile` sites | 33 (1 of them a 3.7 KB hand-rolled lexer) |

Per package, largest first: `fulltext` 11,855 · `transparency` 4,439 ·
`publications` 4,190 · `quality` 3,768 · `llm` 5,114 · `context_processor` 1,710 ·
`citations` 1,129 · `agents` 865 · `db` 787 · `templates` 229 · root 185.

`fulltext/jats_parser.py` alone is **7,090 lines** — 36% of the library — and it
is a SAX state machine whose authors have already ported it once (Python from
Swift's `XMLParserDelegate`). That is the single largest chunk of work and the
one with the least ecosystem risk, because the algorithm is portable verbatim.

---

## Verdict

**No roadblock is fatal, and there are now three rather than four.** Every
module has a defensible Rust shape. But the port is not uniform: roughly two
thirds of the library is control flow, SQL, regex and data models that
transliterate at or below parity, and the remainder carries decisions that must
be made *before* the first line is written, because they decide the shape of
everything above them.

The three that genuinely block:

1. **`fulltext/pdf_converter.py`** — PyMuPDF's layout extraction has no pure-Rust
   equivalent. This is the only component where the port's *fidelity* is in
   question rather than its effort. Resolved by the dependency policy (§5):
   link PDFium, do not reimplement it. See §1.
2. **Python regex features the `regex` crate does not have** — concentrated in
   `transparency/analyzer.py`. One of the six sites (a `\2` backreference) has
   no rewrite onto the standard crate; `fancy-regex` covers all six, at the cost
   of reintroducing backtracking on a pattern that was deliberately shaped to
   avoid it. See §2.
3. **`xml.sax`/expat semantics** — `jats_parser` depends on expat *rejecting*
   malformed input, and says so in its audit design. `quick-xml` is the answer;
   the work is naming the differences. See §3.

**The LLM provider layer is no longer a blocker** (§4). Collapsing to two wire
protocols — OpenAI-compatible and Anthropic-compatible — turns it from *"hand-roll
seven SDK clients"* into *"hand-roll two REST clients"*, which is a normal
`reqwest` + `serde` job.

## Scope decisions taken since the first pass

Recorded because each one changes what the rest of this document means.

| Decision | Effect |
|---|---|
| **Target a corrected bmlib, not `main`** | The port is functionally equivalent to a version in which the sixteen defects of the Appendix are **fixed**, not reproduced. Correctness beats transliteration wherever the two conflict. §0. |
| **A native library may be linked where no close Rust equivalent exists** | PDF is the case that matters. A Python module cannot be linked into a Rust binary, and a Swift one can but at real cost, so the policy is: **link a C/C++ library where one exists, otherwise hand-roll.** The line between "link" and "hand-roll" is drawn in §5. |
| **Two LLM protocols, not seven providers** | OpenAI-compatible and Anthropic-compatible. Removes the Anthropic and OpenAI SDKs and the OpenAI-compatible wrapper entirely. §4. |

The rest of this document is the three blockers in detail, then the
mechanical-but-large items, then a proposed order and what is explicitly *not*
established.

---

## 0. The fidelity contract: functionally equivalent to a corrected bmlib

**Decided by the maintainer, 2026-09-26, and then narrowed the same day.** The
port must produce *equivalent output for equivalent input*; it need not reproduce
Python byte for byte. **And it is equivalent to a _corrected_ bmlib, not to
`main`:** where the Python library is wrong, the Rust port implements the correct
behaviour. Correctness beats transliteration wherever the two conflict.

That second half is the load-bearing one, because it splits the repository's
behaviour into two piles that a porting session will otherwise conflate:

| | What it is | What the port does |
|---|---|---|
| **The Appendix's sixteen defects** | Accidental bugs, each filed with a reproduction | **Fix them.** Port the intended behaviour, not the observed one. |
| **`docs/DECISIONS.md`'s register** | Deliberate non-fixes, each investigated and closed as correct, each with the test that pins it | **Keep them.** They are the specification, not a defect list. |

The distinction matters because the two look alike from the outside: both are
places where "the obvious thing" and "what the code does" disagree. The Appendix
entries are all *contradicted by their own comments or the manual*; the register
entries are argued in full and closed. When a Rust implementation is about to
"clean up" something, the question is **"is this in `DECISIONS.md`?"** — and if
the answer is yes, it stays.

Three of the Appendix entries need a *decision* rather than just a fix, because
the correct behaviour is not determined by the code or the docs:

- [#304](https://github.com/hherb/bmlib/issues/304) — whether a discovered PMC ID
  may override a well-formed but unserved caller-supplied one. The malformed case
  is unambiguous; this one is a design question.
- [#309](https://github.com/hherb/bmlib/issues/309) part 1 — which cache-key
  derivation is canonical. The port must pick one, and the documentation and code
  currently disagree.
- [#298](https://github.com/hherb/bmlib/issues/298) — what the study-type
  priority order *should* be when a lower tier's mention is contrastive.

The rest are mechanical once someone decides to change the Python library; the
Rust port can simply implement the correct behaviour.

What the fidelity contract decides:

- **§1, the PDF back end.** `pdfium-render` is acceptable **without** the
  235-PDF diff being a go/no-go. A functionally-equivalent converter is allowed
  to segment differently, so the experiment drops from a gate to a *quality
  measurement*: run it to learn how much the section segmentation moves, then
  decide whether that is tolerable. It stops being able to block Phase 4.
- **§2, the regexes.** The `regex` crate's linear-time engine is preferred over
  `fancy-regex`, because equivalence is judged on matches, not on the shape of
  the pattern. The possessive-quantifier question collapses to a per-pattern
  check, and `_COI_SECTION_RE`'s `\2` may be a duplicated-quote alternative.
  The `_NESTED_ARTICLE_RE` rewrite stays a rewrite — not for fidelity, but
  because a hand lexer is *simpler* than working around four missing features.
- **§3, XML.** `quick-xml`'s exact rejection contract no longer has to match
  expat's. `_parse_audit.py` only needs a well-formedness signal, and any
  parser that provides one faithfully is equivalent for the audit's purpose.
- **§6, templating.** `minijinja` no longer has to render byte-identically to
  Jinja2 — but only for the *port*, not for the prompt. Two things it may not
  change: a template that Jinja2 would reject must not silently render, and a
  variable Jinja2 leaves undefined must not silently become empty text in a
  prompt sent to a model. "Equivalent modulo whitespace" is fine for a rendered
  prompt; "equivalent modulo a missing interpolation" is not.
- **§7, the differential oracle.** It compares *semantic* fields — parsed
  structures, scores, enum members, indicator strings — not JSON text. The
  spike's open question (*"JSON columns go through `serde_json` here, `json`
  there; byte-identical output was not checked"*) is therefore **dissolved
  rather than answered**: `1.0` vs `1`, key order and float formatting are not
  divergences under this contract.

**A caution the "fix the bugs" instruction creates.** The oracle in §7 diffs Rust
against Python. Once the port fixes a defect, that diff *will* disagree — by
design — and the disagreement is exactly the sixteen entries above and nothing
else. So the oracle needs the Appendix as an explicit, enumerated exception list,
not a tolerance. An unexplained divergence must still fail; a divergence whose
issue number is in the list must be checked against the *intended* behaviour
instead. Without that list, sixteen known-and-accepted differences quietly become
the ambient noise level, and the seventeenth defect hides in it.

What it does **not** license, and this is the part to hold onto:

- **The values that reach a downstream database are not free.** `CHANGELOG.md`
  and `ROADMAP.md` price every behaviour change as a *blast radius* — "prose
  moves in 5,990 of 8,118 articles", "`body_sections` is the only field that
  moves". Those figures only mean something because the stored column is
  compared against a previous run. A Rust port that is functionally equivalent
  but *differently* equivalent on the same article makes a downstream's cached
  full text incomparable, which is the cost those documents exist to quantify.
  Equivalence must therefore be **per semantic field, measured over
  `tests/data/`'s corpora**, not argued from the code.
- **"Equivalent" is not a synonym for "close enough to skip the corpus".** The
  corpora are the contract's only instrument. The port should carry its own
  equivalent of a blast-radius diff before it is trusted, exactly as this
  repository requires of its own changes (`docs/SESSION-RULES.md`: *state a
  blast radius from a diff, not from the call graph*).
- **The deliberate non-fixes in `docs/DECISIONS.md` are not "divergences to
  tidy".** A functionally-equivalent port that simplifies one of them
  reintroduces a fixed bug. That register is the port's specification for the
  behaviour it must reproduce, and it is why "equivalent" and "cleaner" must
  not be allowed to trade.

---

## 1. PDF layout extraction — where the port stops being pure Rust

### What is used

`pdf_converter.py` is 610 lines and uses a narrow slice of PyMuPDF:

- `len(doc)`, `doc.needs_pass`, `doc.metadata`, `doc.close()` (`with` protocol)
- `page.get_text()` — plain text, for `ConversionResult.text`
- `page.get_text("dict")` — per-page `blocks` → `lines` → `spans`, from which it
  takes, **per line**: concatenated span `text`, `bbox`, and from the *dominant*
  span (most non-whitespace characters, ties to the first) the `size`, `font`,
  and `flags` bits for bold (`1<<4`) and italic (`1<<1`).

`segmenter.py` (418 lines) then consumes `TextBlock` — one per PDF *line*, with
`font_size`, `font_name`, `is_bold`, `is_italic`, `x`, `y`, `width`, `height` —
and classifies headings by median font size, a size ratio, and boldness. It is
pure logic over that struct; nothing in it is PDF-specific.

### Why it is a roadblock

PyMuPDF is a C library (MuPDF/PDFium lineage) with its own line-and-span
segmentation, and the *exact* segmentation is observable: a heading numbered in
a different weight produces two spans, which `_line_to_block` deliberately
rejoins into one block; whether that rejoin happens depends on where the PDF
library decides a line ends. Likewise "dominant span" only means something if
span boundaries land where PyMuPDF puts them.

Rust has no drop-in equivalent:

- **`pdfium-render`** wraps the same PDFium C library that Chromium uses, and is
  the closest thing available. Read off its 0.8.34 API rather than assumed: a
  page exposes `text().segments()`, each `PdfPageTextSegment` has `bounds()`,
  `width()`, `height()`, `text()` and `chars()`, and each `PdfPageTextChar`
  carries `font_name()`, `unscaled_font_size()` / `scaled_font_size()`,
  `font_weight()` (a `PdfFontWeight` enum), `font_is_italic()`, `origin()`,
  `tight_bounds()` / `loose_bounds()` and `is_hyphen()`. So **the fields
  `TextBlock` needs all exist** — which is the good news, and better than this
  analysis first assumed.
- **It is not a crate alone.** `pdfium-render` does not ship PDFium; it binds at
  run time (or links statically) to a `libpdfium` built per platform. That is a
  packaging and CI change — a dynamic library beside the executable, or
  `PDFIUM_DYNAMIC_LIB_PATH` / `PDFIUM_STATIC_LIB_PATH` at build time — not a
  `Cargo.toml` line. It also forces a version choice from a long list of
  `pdfium_7881`/`pdfium_7763`/… features, each binding a different C API.
- **Pure-Rust parsers** (`lopdf`, `pdf-extract`) decode content streams but do
  not reproduce a line/span/dominant-font model at all. Text order and
  de-hyphenation are their own known weakness. There is no middle option.

So the decision is **"link PDFium, do not reimplement it"** (§5), and the
question that remains is a *quality* one rather than a go/no-go: how far does the
section segmentation move?

- It is not nothing. PDFium merges characters into a segment only when they share
  **a baseline and font settings**; PyMuPDF segments by its own rules and
  `_line_to_block` then takes the *dominant span* of each line. Two different
  merge rules produce different segment boundaries, which change the dominant
  font size, which changes the median, which changes which lines are headings.
  PDFium's own documentation also warns that `font_weight()` *"may not reliably
  return the correct value"* for built-in fonts — and boldness is one of the two
  inputs to `_is_potential_header`.
- It is measurable: `tests/data/pdf_metadata_titles.json` names 235 real PDFs
  used to calibrate title acceptance, and `scripts/sample_pdf_metadata_titles.py`
  knows how to fetch them. Extracting all 235 with both backends and diffing
  `TextBlock` lists gives the answer in a day. **Size the PDF half from that
  diff, not from this document.**

A third thing PDFium changes, worth deciding early: it is **not thread-safe**,
and `pdfium-render`'s `thread_safe` feature (on by default) serialises every
call behind a mutex. Its own documentation recommends *parallel processing* over
multithreading. A Rust port that wants to convert PDFs on a thread pool —
something the Python version does not do today, since PyMuPDF's Python binding
is also not thread-safe — must use one process or one locked `Pdfium` per
process, not `rayon::par_iter` over one handle.

### What is *not* a problem here

`ConversionResult` is a plain data contract; `PDFConverter` is an ABC and
`LayoutExtractor` a `runtime_checkable` Protocol, both of which map cleanly onto
a Rust trait (the Protocol's `isinstance` check becomes a separate trait or an
`Option<&dyn LayoutExtractor>`). The furniture-stripping, paragraph-reflow,
title-corroboration and section-classification logic — the majority of both
files — is pure and portable as-is.

---

## 2. Regex: six sites the `regex` crate cannot take as written

Rust's `regex` crate is linear-time by construction and therefore has **no
backreferences, no lookaround, no possessive quantifiers, and no atomic
groups**. bmlib uses all four, in five places across three packages:

| Location | Feature | Migratable? |
|---|---|---|
| `transparency/analyzer.py:903` `_REGISTRATION_CUE_RE` | negative lookahead `\bnct(?!\d)` | Rewrite as three alternatives, or a two-pass scan |
| `transparency/analyzer.py:929` `_COI_SECTION_RE` | backreference `\2` pinning the closing quote to the opening one | **No** — must be rewritten as matching `"..."` *or* `'...'` alternatives, or as a small hand lexer |
| `transparency/analyzer.py:1844,1886` `_NESTED_ARTICLE_RE` | negative lookahead `(?![-.:\w])` | yes |
| `transparency/analyzer.py:1834,1845` `_NESTED_ARTICLE_RE` | **possessive** stars `(?:…)*+` — line 1834 is the DOCTYPE branch, 1845 the attribute run | Maybe — check per pattern, see below |
| `llm/providers/ollama.py:100` | `(?!\d+$)` | yes |
| `quality/extractors.py:206` | `(?<!\w)` … `(?!\w)` around a dynamic keyword | yes — `\b` is equivalent for this use |
| `publications/fetchers/pubmed.py:231` | `\1` in a `re.sub` replacement (not a pattern backref) | yes — Rust's `Regex::replace_all` uses `$1` |

Everything else (28 of 33 compiled patterns) is plain `regex`-crate syntax.

### Why this matters more than its size suggests

`_NESTED_ARTICLE_RE` is not incidental. It is the lexer that strips
`<sub-article>`/`<response>` regions from Europe PMC full text before the COI and
data-availability scans run, and `docs/DECISIONS.md` records that it was tuned
for three configurations (13.4 ms unguarded, 26.6 ms with the literal outside
the group, 191 ms with it inside) precisely because a naive alternative is
quadratic. A Rust port that reaches for `fancy-regex` — which *does* support
lookaround and backreferences, by adding backtracking — reintroduces exactly the
super-linear behaviour that the Python pattern's structure was chosen to avoid,
on input that reaches it from the network.

**The possessive quantifiers are the one gap that is smaller than it looks, and
it should be checked rather than assumed.** Line 1845's `(?:…)*+` is over
alternatives whose FIRST branch is `[^>"']`; neither `"…"` nor `'…'` can match a
bare `>`, so the greedy star can never overrun past a `>` that the real match
needed, and a backtracking engine would find the same match without the `+`. The
`+` buys the avoidance of pathological backtracking, not a different result — so
a Rust transliteration may be able to drop it, which the port must demonstrate
per pattern rather than take on faith. Line 1834's DOCTYPE branch, followed by
`(?:\[.*?\]\s*)?>`, is the less obvious of the two and is where the check should
start.

**What each site costs.** The `\2` backreference (`_COI_SECTION_RE`, line 929) is
the only one with no rewrite onto the standard crate: matching
`-type="coi"` or `-type='coi'` as two explicit alternatives is equivalent *for
this pattern* — the group exists to pin a closing quote to an opening one
because a fixed quote class would match a mismatched pair — so the honest fix is
either duplicated quoting alternatives or a two-step match whose second step
compares the captured quotes. The lookarounds are all rewritable (`\b`, an
explicit split, or a second pass), and `pubmed.py:231`'s `\1` is a `re.sub`
*replacement* backreference with no Rust-pattern equivalent — it becomes `$1`.

**Recommendation.** Treat `_NESTED_ARTICLE_RE`'s replacement as a rewrite, not a
transliteration: it is already a five-branch lexer in disguise (comment, CDATA,
processing instruction, DOCTYPE internal subset, tag), and the code comments
enumerate the branches. A hand-written Rust lexer over `&[u8]`/`char_indices`
would be linear by construction, would keep the "every branch opens with the
same literal" property the Python relies on for `sre`'s prefix optimisation, and
would remove the timeout question entirely. `fancy-regex` is acceptable for the
other four, none of which is called on adversarial input in a hot loop.

One caveat to verify rather than assume: Python's `\w` is Unicode-aware,
including in lookaround. Any transliteration must use Rust's `\b`/`(?u:\w)`
equivalents deliberately, or word-boundary behaviour shifts on accented text —
which is common in author names and funder names.

**A rewrite now has an oracle, which is new.** The transparency audit ran a
20,000-document differential fuzz of `_strip_nested_articles` against an
independent `ElementTree`-derived implementation — nesting, `>` inside quoted
attribute values, comments, CDATA, processing instructions, DOCTYPE internal
subsets — and found **0 divergences and 0 false refusals**. So the Python lexer is
measured-correct on that population, and a Rust lexer has something to be held
to beyond the tests: run the same generator against both and diff the stripped
output. That converts §2's rewrite from "rewrite carefully" into "rewrite against
a differential oracle", which is the same instrument §0 requires for the port as
a whole.

---

## 3. XML: the parser's *rejections* are load-bearing

Two different XML consumers, two different risks.

### `jats_parser.py` — SAX, and expat's strictness is a design input

The parser implements three SAX callbacks (`startElement`, `characters`,
`endElement`) on top of `xml.sax`, i.e. expat, and its own comments say it
depends on expat's behaviour: "expat rejects an unbalanced *document*", "only a
document expat would reject can carry either". `_parse_audit.py` (345 lines) is
built on the same premise — it is a *net over the handler*, not an input check,
and it is meaningful only if the XML layer refuses malformed documents.

A Rust port has `quick-xml` (streaming, the direct analogue of a SAX pull
parser) but **not** expat's exact rejection contract. Points to pin:

- **Unbalanced tags**: does the chosen parser reject, and with what error?
- **Undeclared entities and DOCTYPE internal subsets**: `quick-xml` is
  deliberately lenient and does not expand external entities. bmlib's own
  `analyzer.py` comments treat DOCTYPE internal subsets as a lexing hazard, so
  this area is already known to be subtle.
- **Character encoding**: expat handles the XML declaration; `quick-xml` needs
  the encoding feature to do the same.
- **Error type**: `analyzer.py`'s `_BUG_TYPES` reasoning rests on
  `ET.ParseError IS SyntaxError` — a *hierarchy* claim that exists only to say
  "a malformed remote body is an ordinary outcome, not a bmlib defect". In Rust
  that distinction becomes a typed error variant, which is the better design and
  also means some Python tests have no Rust counterpart (as the db spike found
  for `datetime`/`date`).

This is not a blocker; it is a **mapping task with named unknowns**, and each of
those should be a test written before the parser body is ported.

### `analyzer.py` and `pubmed.py` — tree parsing

Both use `ElementTree.fromstring` on a complete in-memory document. Idiomatic
Rust would be a borrowed tree DOM (`roxmltree`) rather than an owned
`Vec<Element>` arena, which is a better fit for the read-mostly access patterns
(`_text(el)`, child walks) and avoids a lot of ownership ceremony. `roxmltree`
rejects some documents `expat` accepts and vice versa — the same mapping test
applies.

---

## 4. The LLM layer: two protocols, and the SDKs disappear

**Decided: the port speaks two wire protocols — OpenAI-compatible and
Anthropic-compatible.** That is the whole provider layer, and it is a smaller
job than the module's 5,114 lines suggest, because bmlib's seven "providers" are
already mostly one:

| Provider | Python implementation | Under two protocols |
|---|---|---|
| OpenAI | `openai_compat.py` (476 lines) — the real implementation | **Protocol A** |
| DeepSeek | 56 lines, subclasses `OpenAICompatibleProvider`, six constants | Protocol A + constants |
| Mistral | 71 lines, same shape | Protocol A + constants |
| Gemini | 78 lines, same shape (Google's `/v1beta/openai/` endpoint) | Protocol A + constants |
| OpenAI-compatible servers | already the point of `openai_compat.py` | Protocol A |
| Anthropic | 551 lines, `anthropic` SDK, different message/tool/thinking shapes | **Protocol B** |
| Ollama | 1,231 lines — already raw HTTP, plus model discovery and cost metadata | Protocol A where it fits, native `/api/*` where it does not |

So the port is **one shared `reqwest` + `serde` client, one OpenAI-compatible
implementation, one Anthropic implementation**, and a constants table where the
three thin subclasses were. The `anthropic`, `openai` and `ollama` optional
extras disappear, which is the win: the Rust library becomes self-contained over
one HTTP client.

`BaseProvider` is a 205-line surface of which four methods are abstract — `chat`,
`list_models`, `test_connection`, `count_tokens` — plus `embed`/`embed_batch`
with `NotImplementedError` defaults. Two protocol implementations satisfy all of
it.

### The four things that actually cost effort

1. **Duck-typed responses become `serde` structs.** `openai_compat.py` reads
   `getattr(choice.message, attr, None)`, `getattr(raw, "tool_calls", None)`;
   `anthropic.py` walks `getattr(block, "type")`. The SDK's permissiveness — an
   absent field, a present `null`, a tool call assembled across streaming chunks
   — is behaviour that must now be re-derived from the wire format rather than
   inherited. **Streaming tool-call assembly is the fiddly part**, and the one
   place where "the SDK did it for us" was doing real work.
2. **Safety defaults.** `ollama.py` documents re-implementing *"the SDK's HTTP
   safety defaults: HTTP(S)-only scheme, and the bearer token stripped across
   cross-origin redirects"*. A Rust HTTP client gives neither for free, and the
   recent security fix that hardened this in Python (`b75a035`) is a reminder
   that the obvious implementation is the vulnerable one — see
   [#301](https://github.com/hherb/bmlib/issues/301), where that same fix
   regressed `host:port/path`.
3. **`count_tokens` is provider-specific.** All providers implement it
   (`BaseProvider:175`); the naive version is a character heuristic. Worth
   checking whether anything downstream depends on its accuracy before porting
   it as-is.
4. **Thinking/reasoning is the least standardised surface.** bmlib maps a `think=`
   kwarg (bool / effort string / int budget) onto each provider's native
   parameter and returns `LLMResponse.thinking`. Anthropic's extended thinking has
   a documented open issue in bmlib already (signature-preserving block storage
   for tool loops, `ROADMAP.md`), so the port should expect to implement less
   than the Python does here rather than more.

### What is deliberately dropped, and the cost of dropping it

Stated so it is a decision rather than an oversight:

- **Per-provider `MODEL_PRICING` tables.** Their only consumer is
  `calculate_cost()`; keep the cost calculation, but the tables are data that
  goes stale and are not protocol-level. Whether the port ships them is a
  product decision, not a porting one.
- **Ollama's `list_models`/`show()` metadata** (`capabilities`,
  `details.context_length`). This is ~600 of the module's 5,114 lines, and it
  exists because the Ollama Python SDK's Pydantic model dropped two fields.
  Under two protocols, Ollama model listing is `/api/tags` and possibly
  `/api/show`; the lazy context-window lookup and its memoisation can go unless a
  downstream needs the window size.
- **`list_providers()`'s documented semantics** change rather than port —
  [#303](https://github.com/hherb/bmlib/issues/303) is the Python defect where it
  always returns all six. In Rust, provider availability is a compile-time
  property, so decide what the function should mean (see the issue).

### What is not yet established

No Rust crate was evaluated for either protocol. The honest position is that
**writing two REST clients is ordinary work with one genuinely fiddly corner** —
streaming tool-call assembly — and that the corner is where a spike would pay for
itself. Everything else here is a `serde` derive and a constants table.

---

## 5. Dependency policy: link a native library, or hand-roll it small

**Decided: where no reasonably close Rust equivalent exists, link a native
library; where the missing piece is small, hand-roll it in Rust.** The useful
work is drawing that line, and the line is not "how hard does it look" but **how
much of the algorithm is the value.**

One correction to the framing, because it changes what is available: **a Python
module cannot be integrated into a Rust binary.** There is no CPython-ABI bridge
worth building for this, and an embedded interpreter would drag the GIL, the
packaging and the whole dependency tree back in — it would defeat the port
rather than accelerate it. A **Swift** module can be reached, but only through a
generated C ABI and a build-system arrangement per platform; that is a real cost
and only worth paying for something large and already correct. So the realistic
choices are *link a C/C++ library* or *hand-roll*.

### The decision table

| # | Missing piece | Size of what is needed | Verdict |
|---|---|---|---|
| 1 | **PDF text + layout extraction** (`fulltext/pdf_converter.py`, 610 lines; `segmenter.py`, 418) | Page/line/span segmentation with font name, size, weight, italic flag and geometry, out of a compressed, encrypted, subset-font PDF. Thousands of lines of C, and a decade of font and encoding edge cases. | **Link PDFium** via `pdfium-render`. Reimplementing the *extractor* is out of proportion; reimplementing bmlib's *wrapper* is not (see below). |
| 2 | **XML parsing** (`jats_parser.py`, 7,090 lines; `analyzer.py`, `pubmed.py`) | A well-formedness-checking parser with correct entity, encoding and DOCTYPE handling. | **Link `quick-xml`** (Rust, streaming, SAX-shaped — the same model as `xml.sax`) and **`roxmltree`** for the two tree-DOM consumers. |
| 3 | **HTTP + TLS** | Connection pooling, redirect policy, timeouts, cross-origin credential stripping. | **Link `reqwest`/`hyper` + `rustls`.** Where the *policy* lives (scheme allow-list, token stripping on cross-origin redirect) is hand-rolled on top — §4. |
| 4 | **Jinja2 templating** (`templates/engine.py`, 189 lines) | `{{ var }}`, `{% for %}`, `{% if %}`, loader fallback, `keep_trailing_newline`. | **Link `minijinja`**, then *measure the divergence set* against real prompts. This is dependency #1 by risk of silent wrongness, not by size. |
| 5 | **SQLite / PostgreSQL** (`db/`, 787 lines) | Driver + dialect handling. | **Link `rusqlite`/`postgres`** — already spiked and measured. |
| 7 | **Dates and instants** (`sync.py`, 1,219 lines) | Calendar arithmetic, ISO 8601 with offsets, and **`datetime`/`date` semantics**. | **Link `chrono`** (`std`,`clock`, no default features). The rules are wall-clock-sensitive by nature, and hand-rolling the calendar is the wrong place to be clever — but note the port found *two* places where chrono's range is wider than Python's (`NaiveDate::MIN`/`MAX` are years -262143/262143 against 0001/9999), so the bounds are named constants rather than the library's, because the bound appears in a caller-facing error message. |
| 6 | **CSV** (`retractions.py`, 735 lines) | RFC 4180 quoting + **Python's `line_num`** — the physical line a record ended on, which is what a skip report names. | **Link `csv`** for the grammar (a solved general problem, and it exposes the raw byte offset needed: counting `\n` before `ByteRecord::position()` + length *is* `line_num`). Hand-roll nothing: a first cut wrote the forty-line parser and was replaced, because the crate is correct and the line count is a five-line layer over it. |
| 6 | **The nested-article lexer** (`transparency/analyzer.py`, one 3.7 KB regex) | Five-branch linear lex over a tag grammar bmlib defines itself. | **Hand-roll.** ~150 lines, no dependency, linear by construction, and now has a differential oracle (§2). This is the clearest "small → hand-roll" case. |
| 7 | **The five other regexes** (lookaround, `\2`, possessive) | Six sites across three packages. | **Hand-roll the simple ones** (`\b` rewrites), **link `fancy-regex`** where a backreference is genuinely needed. |
| 8 | **JSON repair** (`json_repair.py`, 637 lines) | A quote-state machine and iterative reparse, plus `serde_json`. | **Hand-roll the repair**, link `serde_json`. The heuristics are bmlib's own and survived 23k-document fuzzing — they are the value, and no crate has them. |
| 9 | **Unicode normalisation, HTML escaping, path expansion** | NFKD + combining-mark removal, entity escaping, `~`. | **Link small crates** (`unicode-normalization`, `html-escape`, `shellexpand`/`dirs`). Hand-rolling Unicode tables is the worst trade available. |
| 10 | **Atomic file publish** (`_atomic.py`, 166 lines) | temp + `fsync` + `rename`, mode handling, symlink decisions. | **Link `tempfile` for the temp file, hand-roll the policy.** The load-bearing parts (fsync before rename, the symlink skip, 0666-and-umask) are bmlib's decisions, not the crate's. |
| 11 | **The Sampler scripts** (`scripts/`, ~500 KB) | Offline probe harnesses that make live requests. | **Out of scope for the port.** They are instruments, not library code; they can stay Python, and the repository's own rule is that they exist to produce *measurements*, which do not need to be re-implemented to remain valid. |

### The rule that falls out

**Link what is a solved general problem** — parsing formats, driving databases,
speaking TLS, normalising Unicode. **Hand-roll what is bmlib's own decision** —
which is most of what this library actually is: the routing, the invariants, the
heuristics, the log levels, the lexer, the repair state machine. The line is not
"lines of code", because `_atomic.py` is small *and* decision-dense, and the PDF
wrapper is moderate *and* mostly decision-dense:

**A note on the PDF case specifically, since it is the one real link.** `PyMuPDF`
is doing the extraction; bmlib's 610 lines around it are furniture-stripping,
paragraph reflow, metadata-title corroboration, page accounting and the
result contract — all of which are bmlib's and all of which port as-is. So
"link PDFium" does **not** mean "give up on `pdf_converter.py`": it means the
`PDFConverter` backend is a ~200-line Rust wrapper over `pdfium-render`, and the
other ~400 lines port normally. That split is already in the Python code — it is
why `PDFConverter` is an ABC and `LayoutExtractor` a separate protocol.

### What is not established

No crate in this table was compiled or benchmarked for this pass. The two worth
verifying before anything is scheduled are **PDFium linking on all target
platforms** (it is a dynamic library, not a crate — §1) and **`minijinja`'s
divergence from Jinja2 on real prompts** (the one dependency whose failure mode
is a silently different prompt, §6).

---

## 6. Python constructs that do not transliterate

None of these is individually hard; together they are the reason a mechanical
translation loses.

### Exception hierarchies used as classification

`_BUG_TYPES` (the set of exception types that mean "bmlib is wrong") appears in
`fulltext/service.py` and again in `transparency/analyzer.py`, and both are
tested for *agreement*, deliberately, because the classification rests on
Python's hierarchy: `json.JSONDecodeError` **is a** `ValueError`, `ET.ParseError`
**is a** `SyntaxError`. So the Python code can catch broadly and then ask "was
this the shape of a bmlib defect?". In Rust, `?` and a typed error enum give the
same information for free — but the Python tests that pin the hierarchy
(`TestTheRestatedBugTypesMatchTheOtherModules`) have no Rust counterpart and
must not be transliterated into a test that asserts nothing.

### Concurrency primitives

- `threading.Lock()` in `TokenTracker`, `PerformanceMetrics` (`agents/metrics.py`,
  a dataclass with a lock field, `compare=False`, `repr=False`), the LLM client
  singleton, `db/transactions.py`'s depth table, and the analyzer's rate limiter.
  All become `Mutex`/`RwLock` — **but the analyzer's rate limiter is
  per-instance and therefore needs `Arc<Mutex<..>>`**, because the analyzer is
  otherwise a shared read-only object.
- `threading.local()` in `analyzer.py:2199` holds `_api_reachable`, documented as
  "reachability describes a single analysis… held per-thread so that concurrent
  `analyze()` calls cannot contaminate each other". **This is the one place where
  the Python design must change shape rather than be translated.** The correct
  Rust answer is not `thread_local!` (it would be global, and `analyze()` may be
  concurrent *and* async) but to make reachability a local variable threaded
  through the query helpers, or a `&mut` accumulator handed to `analyze()`. That
  is a signature change across the analyzer's private helpers — contained, but
  it must be designed, not discovered.

### Reflection

`publications/fetchers/registry.py:82` calls `inspect.signature(fetcher)` to
enforce that a fetcher declared `resumable=True` accepts the resume keyword
arguments (or `**kwargs`). That check is a runtime guard against a
mis-declaration. Rust cannot introspect a function's parameter names this way;
the guard should become part of the `Source` trait (a `resumable()` method plus
an associated keyword type), which is strictly stronger. Note the Python guard
already falls through for C builtins, so it is best-effort today.

### Templating

`templates/engine.py` is 189 lines over `jinja2.Environment`, with
`keep_trailing_newline=True` and `autoescape=False`. `minijinja` is the natural
Rust analogue and is a reimplementation, not a binding — so its divergence set
(undefined-variable handling, whitespace control, filters, final-newline
behaviour) must be enumerated against real prompts. **bmlib ships no templates
of its own** — `default_dir` is always the caller's directory — so this cannot
be settled inside bmlib. It is a question for whichever downstream consumes the
port, and it is cheap to answer there: point `minijinja` at a real prompt
directory and diff the rendered output.

### Paths and platform details

- `Path.expanduser()` on user directories → `shellexpand`/`dirs`.
- `_atomic.py`'s write-then-`fsync`-then-`os.replace` with a UUID temp name,
  `O_BINARY` where available, mode 0666-filtered-by-umask, and a dangling-symlink
  decision in `templates/install_defaults()`. Rust `tempfile` + `persist` is the
  analogue; the *mode* and the symlink behaviour are deliberate and must be
  carried, not defaulted.
- `_titles.py`'s title corroboration normalises with NFKD and drops combining
  marks → `unicode-normalization`.
- `html.escape` in the JATS and PDF converters → `html-escape`.

### Value and error modelling

Already measured by the spikes: `db/` came out at **1.23×** Python's line count,
almost all of it `Value`/`Row`/`DbError` replacing what duck typing and
exceptions gave free, and that cost is paid *once*. Above it, `storage.py` came
out at **parity**, and the three `publications` model types came out **77%
smaller** because `serde` replaces 28 hand-written `to_dict`/`from_dict` pairs.
The line ratio is not the concern; the boundary type is, and the spike's own
advice — get `Value`/`Row` right early — stands.

---

## 7. The test suite is the port's real asset, with one seam to redesign

66,823 lines of tests, and they are unusually good material for a port:

- **768 `monkeypatch` uses and 34 `responses` uses**, i.e. no live network in
  the suite. The HTTP paths are already tested through seams.
- `tests/data/` (11 MB) and `tests/fixtures/` carry real corpora: two JATS
  article sets, the funder-label corpus, the PDF-title corpus. They port
  unchanged.
- The project's conventions already demand the thing a port needs most —
  *"state a blast radius from a diff, not from the call graph"*
  (`docs/SESSION-RULES.md`) — and that harness style transfers directly to
  differential Rust-vs-Python testing.
- `docs/DECISIONS.md` (257 KB) is a register of *deliberate non-fixes* with the
  test that pins each one. A port that "tidies up" any of them silently
  reintroduces a fixed bug. **This is the single most valuable document for the
  port and it has no Rust equivalent — it must be read, not ported.**

The seam that does **not** port: mocks are injected by patching Python objects,
including at boundaries the port will express as traits and generics
(`_require_httpx()`, the injected `httpx` client in the publication fetchers,
the fake LLM providers). Each such test needs its Rust seam designed — which is
a feature, since a trait-based seam is checkable at compile time — but it means
the tests are not free.

Two specific couplings worth naming before the count is read as a workload:

- **The clock is patched, heavily.** `tests/test_agents.py` alone has 33
  `@patch("bmlib.agents.base.time.sleep", ...)` sites and
  `test_pubmed_fetcher.py` 70 `patch("bmlib....")` sites, because retry backoff
  and the per-host request pacer must not actually sleep. A Rust port that calls
  `std::thread::sleep` directly cannot be tested this way; the sleep must be a
  seam (`Fn(Duration)` or a `Clock` trait) from the start, or those tests become
  slow or unwritable.
- **Module-level mutable state.** `_global_client`, `_global_tracker`,
  `_builtins_registered` (twice — providers and fetcher sources), and
  `db/transactions.py`'s `_depths` table. In Rust these are `static`s, so each
  needs `OnceLock`/`Mutex`/`thread_local` chosen deliberately, and the test
  helpers `reset_llm_client()` / `reset_token_tracker()` have to keep working —
  which for a `OnceLock` means they cannot be written naively.

The **differential oracle** worth building early: a small harness that runs both
implementations over `tests/data/` and diffs serialised output. The spike
(`publications-rs/FINDINGS.md`) notes that JSON columns go through `serde_json`
in Rust and `json` in Python and **byte-identical output was never checked** —
that is exactly the class of divergence the oracle exists to catch.

---

## 8. Proposed order

Ordered by dependency and by "what de-risks what", not by size.

A per-package read, so the phase list below is checkable against something:

| Package | Lines | Portability |
|---|---|---|
| `citations/` | 1,129 | **Pure stdlib.** Straight transliteration (with [#296](https://github.com/hherb/bmlib/issues/296) fixed). |
| `db/` | 787 | **Spiked and measured** (62 Rust tests). 1.23× line cost, all in `Value`/`Row`/`DbError`. |
| `context_processor/` | 1,710 | **Pure**, no LLM import by design. ABC → trait. |
| `agents/` | 865 | **Ported** — `metrics.py` and `base.py`, the latter fixing [#300](https://github.com/hherb/bmlib/issues/300). The retry/truncation loop and the metrics report are mutation-tested. |
| `templates/` | 229 | Mechanical over `minijinja`, but the Jinja2 divergence set is unmeasured (§6). |
| `quality/` | 3,768 | Extractors and Cochrane models/formatters are pure — **all ported and green** (defects fixed: [#294](https://github.com/hherb/bmlib/issues/294)/[#297](https://github.com/hherb/bmlib/issues/297)/[#298](https://github.com/hherb/bmlib/issues/298)/[#312](https://github.com/hherb/bmlib/issues/312), plus [#310](https://github.com/hherb/bmlib/issues/310) found while porting); the LLM tiers' answer-reading rules are ported ([#295](https://github.com/hherb/bmlib/issues/295) fixed); Tier 1's mapping, the tiering rule and the Cochrane enrichment are ported too. **The `quality/` package is complete.** One defect found in this port's own Phase 1 work and fixed: a derived `Default` on `QualityFilter` set `use_llm_classification` false where Python's constructor makes it true, so `QualityFilter::default()` silently meant Tier 1 only. |
| `llm/` | 5,114 | `json_repair`/`text_utils`/`utils` are pure and ported ([#299](https://github.com/hherb/bmlib/issues/299) fixed); `data_types` and the **two protocols**' message/tool/response transforms are ported and mutation-tested (§4). The HTTP call, the client router and the tool allowlist are ported too — a provider is a **row of data**, not a class. **The `llm/` transport is complete**; what remains of the package is nothing. |
| `publications/` | 4,190 | `models.py`, `schema.py`, `storage.py`, `retractions.py`, `sync.py`'s rules and `fetchers/_reconcile.py` + `fetchers/registry.py` **ported and green** (every rule mutation-tested). **One dependency this plan did not foresee**: `unicode-normalization`, for the PDF-title check's NFKD. The policy's "hand-roll it if it is small" does not reach this — the fold from a precomposed character to its base plus marks covers **~1,900** characters, and a partial table fails **silently in the dangerous direction**: two spellings of one title compare unequal, so a good title is dropped with only a DEBUG line, and the missing row is invisible because the symptom is an absence. A hand-rolled first cut guessed the Latin Extended-A offsets and the oracle refuted it on the first run — it folded `Ł` to `l` where NFKD leaves it alone, and left `Đ` unfolded where a *different* guess claimed NFKD strips it to `D` (it does not; both are separators). The fold is data, not reasoning.

**Phase 4 in progress.** Done: `jats_parser.py`'s text primitives, `_parse_audit.py`, `_titles.py`, `fulltext/models.py` (every field list diffed against Python's, as data), and `transparency/models.py` — the enum partitions and `calculate_risk_level`. The **SAX reader is ported** (`jats_reader.rs`, 3,038 lines) against a **737-test specification and an 18-document differential oracle** — all 18 match byte-for-byte on the whole rendered article, and I verified the three committed fixtures against Python independently. `segmenter.py` is ported too (125 oracle cases), and so are `_atomic.py` and `fulltext/cache.py` (31 oracle cases). **A gap this plan missed entirely, now closed**: the `HttpClient` trait had **no production implementation**. Every fetcher, the LLM transport and the transparency analyzer were written against it and tested with fakes, so the crate compiled and 627 tests passed while nothing could actually fetch a URL. `ureq` now backs it, tested against a local socket. And `transparency/analyzer.py` (3,800 lines) plus `fulltext/service.py` (2,836 lines) are ported.

**A second defect the port introduced, filed as [#316](https://github.com/hherb/bmlib/issues/316) and being fixed**: `HttpResponse::body` was a `String`, so a binary PDF arrived at the cache with every non-UTF-8 byte replaced by U+FFFD — and **nothing could detect it**, because U+FFFD is itself valid UTF-8, `%PDF` is ASCII and survives the loss, and the cache's only check is that prefix. The symptom is a corrupt binary asset rather than a failed call, which is why it survived the whole service port and was found by reading the seam rather than by a test.

**`pdf_converter.py`'s pure half is ported** as `fulltext/pdf_text.rs` — span dominance, one block per line, furniture detection, paragraph reflow and the HTML render (53 oracle cases). The **backend is a trait**, not a binding: linking a PDF library is the one remaining integration, and the plan's §5 already prices it as a ~200-line wrapper.

**A completeness audit** found five pieces still unported, which the phase list had understated: `llm/token_tracker.py` (ported, 8 tests, 6 mutants caught), the three LLM-backed agents (`quality/study_classifier.py`, `quality/quality_agent.py`, `quality/cochrane_assessor.py` — ported), `context_processor/llm_processor.py` (ported, 30 oracle cases), `templates/engine.py` (ported, 11 tests, 22 oracle cases — with the Jinja2 subset **refused by name** rather than silently rendered blank, a new §9 divergence) and the PDF backend (**now ported** — `pdfium-render` behind the optional `pdf` feature, with the service adapter; 8 tests against real PDFs). **Phase 4 is complete.**

## Final verification (round 39)

Run on a **clean build** (`cargo clean`, 11.2 GiB removed) so no artefact could be stale —
the mtime trap that bit this port twice:

- **789 tests pass, 0 fail** (default features), plus 797 with `--features pdf` and 3 doc-tests.
- `cargo clippy --all-targets`: **0 warnings**, in both feature configurations. `cargo fmt --check` clean.
- **Every one of the 35 corpora regenerates from the Python library and matches what is committed** —
  2,092 cases. This is the check that makes the corpora evidence rather than fixtures: a dumper run
  against the *live* Python and diffed against the committed expectation.
- **The Python library is untouched**: `git status --porcelain bmlib/` is empty.
- Every enumerated defect (#294–#309) is accounted for: nine with `DEFECT-FIX` markers in the source,
  and #301/#302/#303/#308/#309 as *not applicable* to the port — each verified in code rather than
  assumed. #308 (`get_recent_records(0)`) and the #309 cache-key half are both pinned by tests.
. The audit is worth repeating before any completion claim: the per-phase table read as finished while five modules were absent.

**Four more defects filed** by the quality agents' reports and verified independently before filing: [#317](https://github.com/hherb/bmlib/issues/317) (`cochrane_models.from_dict` stores `None` for a null field, which `COCHRANE_RESPONSE_FORMAT`'s *"Use null for any field the text does not report"* instructs the model to emit — #295's exact shape one level below the `_as_dict` guard), [#318](https://github.com/hherb/bmlib/issues/318), [#319](https://github.com/hherb/bmlib/issues/319), [#320](https://github.com/hherb/bmlib/issues/320) (the Tier 2/3 readers do not narrow to their annotated types; `int(True)` reads a boolean `sample_size` as 1).

Two more dependencies the plan did not foresee: `sha1` (the cache's collision guarantee — a subtly wrong hand-rolled hash fails *silently* by collision) and `regex` (the section-heading table). Eight `QUIRK:` sites record behaviour reproduced rather than fixed, per the fidelity contract.

**Phase 4 has started**: `jats_parser.py`'s text primitives — whitespace, `<elocation-id>` joining, LaTeX deposits, formula spacing and the equation-number rule — are ported and mutation-tested (74 oracle cases, 12 mutants), and so is `_parse_audit.py` (42 cases, 10 mutants). **The reader's real size is 1,816 code lines, not 4,000** — the rest is docstring and comment — which makes it reachable.

**`biorxiv.py`, `openalex.py` and `pubmed.py` are ported too** — including the EDAT ladder (`_plan_partitions`) and the history-session walk (`_walk_session`), each mutation-tested. `_fetch_partitioned` is **ported whole** — the part loop, its skip/refetch/replan branches and the checkpoint condition — and so is the E-utilities transport (`_esearch` reading, the ESearch/EFetch request builders) and the day-level branch (`fetch_pubmed`'s three arms). `fetch_pubmed`'s assembly is ported too (the four arms, over a scripted transport). `sync()`'s per-source and per-day loop is **ported too** (day selection, the per-day store, the carried credit, the failure count, and the `SyncReport`), each rule mutation-tested — **Phase 2 is complete**. One deliberate divergence is recorded in §9 below. The `Fetcher` trait is defined and the registry holds it; the resume-keyword check that Python does by signature introspection has no counterpart, because the mistake it prevents is unrepresentable in the types. |
| `transparency/` | 4,439 | The §2 regex rewrite and the §6 thread-local change both land here. Otherwise HTTP + data. Two defects to fix ([#306](https://github.com/hherb/bmlib/issues/306)/[#307](https://github.com/hherb/bmlib/issues/307)). |
| `fulltext/` | 11,855 | `jats_parser.py` (7,090) is the largest unit and algorithmically portable (§3); the PDF half is a PDFium link behind a ~200-line wrapper (§1, §5). Three defects to fix ([#304](https://github.com/hherb/bmlib/issues/304)/[#305](https://github.com/hherb/bmlib/issues/305)/[#309](https://github.com/hherb/bmlib/issues/309)). |

**Phase 0 — decisions, before any port code**

- ~~Decide the port's fidelity contract~~ — **decided: functionally equivalent to
  a corrected bmlib** (§0).
- Resolve the three Appendix entries that are open *design* questions rather than
  fixes ([#304](https://github.com/hherb/bmlib/issues/304),
  [#309](https://github.com/hherb/bmlib/issues/309) part 1,
  [#298](https://github.com/hherb/bmlib/issues/298)). The other thirteen have a
  determined answer and need no decision.
- Write the XML conformance test set (§3) and the regex rewrite plan (§2).
- Build the differential oracle (§7) over `tests/data/`'s corpora, comparing
  semantic fields — **with the Appendix as an enumerated exception list**, since
  the port is now deliberately *not* equivalent on those sixteen points.
- Spike the two dependency risks: PDFium linking on each target platform (§1,
  §5) and `minijinja` against a real prompt directory (§6). Both are small and
  both can invalidate a plan.
- Run the two-backend PDF extraction diff over the 235 named PDFs (§1) —
  now a quality measurement rather than a go/no-go, and therefore sizeable
  *after* the PDF module exists rather than before.

**Phase 1 — pure, already de-risked**

`db/` (spike exists, 62 tests) → `Value`/`Row` frozen → `publications/storage`
(spike exists, 22 tests) → `citations/` → `quality/` (the rule-based extractors,
Cochrane models and formatters are pure) → `context_processor/` (no LLM
dependency by design) → `llm/json_repair.py`, `llm/text_utils.py`,
`llm/utils.py` → the JATS/QA model dataclasses via `serde`.

The Appendix's fixes land *here*, one per module, as each is ported — not as a
later pass. A ported module that reproduces its filed defect is a module that has
to be revisited.

**Phase 2 — HTTP with injected clients**

`publications/fetchers/` (already take an injected client) → `publications/sync.py`
→ `sync`'s day rules, which are the densest invariants in the repository and
untouched by any spike.

**Phase 3 — the two LLM protocols**

One shared `reqwest` + `serde` transport → the OpenAI-compatible implementation →
the Anthropic implementation → `agents/` → the three LLM tiers of `quality/`.
Smallest of the four phases in *scope* after the two-protocol decision (§4), but
it carries the one genuinely fiddly corner in the port (streaming tool-call
assembly), so it should start before it is needed rather than when it is.

**Phase 3 — the two big state machines**

`jats_parser.py` via `quick-xml` (largest single unit) → `transparency/analyzer.py`
(the regex rewrite lands here).

**Phase 4 — the layers that need a live network design**

LLM providers over one native HTTP client → `agents/` → `fulltext/service.py` →
`fulltext/pdf_converter.py` + `segmenter.py` (last: it is the one component whose
output quality is measured rather than asserted — see §1 and §0).

---

## What this does not establish

Read this before quoting anything above.

- **No Rust code was written for this pass, and no Rust crate was compiled.**
  Every dependency in §5 is a recommendation from documentation, not a build or a
  benchmark. The `pdfium-render` claims in §1 are read off its 0.8.34 published
  API and source, which is stronger than assumption but still not a link on any
  platform; the `regex`-crate feature gaps are read off its documented limits.
  Phase 0 names the two that can invalidate a plan (PDFium linking, `minijinja`
  divergence).
- **No effort estimate.** The measured evidence (`db` 1.23×, storage 1.0×, models
  0.23×) covers the two easiest packages. The blockers above are unestimated, and
  the PDF one is unmeasured until Phase 0's diff is run.
- **No Rust LLM crate was evaluated**, because the two-protocol decision (§4) made
  the question *"which crate"* rather than *"how many SDK clients"*. The
  transport is `reqwest` + `serde` either way; whether a thin existing crate
  helps with either protocol is unexamined.
- **The PDF extraction diff has not been run.** Nothing here says a PDFium-backed
  converter and PyMuPDF agree on any real PDF.
- **The dependency policy's §5 table was reasoned, not tested.** In particular
  "hand-roll the JSON repair" assumes bmlib's heuristics are the value and no
  crate reproduces them — that claim was not checked against crates.io.
- **`spikes/*` are not a foundation.** They are exploratory, unpublished, and
  the publications spike covers the store path only — no `sync.py`, no fetchers,
  no PostgreSQL backend that was ever executed.
- **Downstream consumers were not surveyed.** The template question (§5, §6)
  genuinely cannot be answered from inside bmlib, and the "who calls this" half
  of an API-compatibility contract is therefore an open question.
- **No performance measurement of any kind**, and no decision about async,
  other than what `spikes/db-async` established for SQLite: keep the sync `Db`
  design, bridge with a pool plus `spawn_blocking`, because
  `rusqlite::Transaction` is not `Send` and no async SQLite trait can be
  implemented at all.

---

## 9. Deliberate divergences in the port

Behaviours the port does **not** reproduce, each with its reason. The Appendix is
the other list: defects that are *fixed*. These are not defects.

| Divergence | Why |
|---|---|
| `CochraneStudyCharacteristics::new` leaves `created_at` `None` | The Python reads the wall clock. A rule that reads a global clock is a rule whose tests are about the clock, so the port threads the instant where a caller wants one. |
| `ModelError::MissingKey` prints the bare key | Reproduces `KeyError.args[0]`, which the oracle compares verbatim. |
| `WindowError::NotADate` / `NotAWholeNumber` are unreachable | Retained for API symmetry with the Python's validation order; the Rust types make the states unrepresentable. |
| **Anthropic receives every system message, joined** | The Python assigns (`system_content = msg.content`), so a conversation with two system turns keeps only the last and drops the first with no error — and only on Anthropic, since the OpenAI path emits every message. Filed as [#315](https://github.com/hherb/bmlib/issues/315); the port joins them with a blank line. |
| **`sync` buffers a whole day's records rather than one part's** | Python stores the buffer **per part** (`flush_part`), so its peak memory on a day too large for one session is one part's records. The port stores once, after the fetch returns, so its peak is the day's — on the 242,216-record day measured for #105, 500 records against the whole day. Closing it needs the records to arrive through a caller-supplied callback so a part boundary can drain them; `Fetcher::fetch` takes only `on_progress` and returns its records in `FetchOutcome`. That is a change to `Fetcher` **and to all three fetchers**, rewriting working walk loops for a bound that only bites on ~240k-record days. The per-part **checkpoint** still works, because the skipped keys are collected from the walk — what is lost is the memory bound, not the resume. Filed as the first task of Phase 5. |

## Appendix — the defects the port fixes rather than reproduces

The port's brief is to leave the Python library untouched, so each of these was
**filed rather than fixed**. Under the corrected-target contract (§0) they are
now the Rust port's **specification for the corrected behaviour**: the port
implements the intended behaviour, not the observed one.

**Nineteen** were filed — sixteen across four adversarial audits (transparency,
fulltext, `llm/`+`agents/`, `quality/`+`citations/`) plus two found while porting,
every one reproduced independently here before filing and checked against
`docs/DECISIONS.md`, `CHANGELOG.md`, `ROADMAP.md` and `gh issue list --state all`
so a deliberate non-fix was not reopened.

The three found while porting are the same *class* as the sixteen — a field or a
value silently dropped, with a test suite that cannot see it — which is why they
join the list rather than being pinned as faithful behaviour. Both are recorded
below with the reason they qualify; if either should instead be reproduced
verbatim, that is a one-line revert in the module and its oracle case.

**Silent wrong values and losses** — the class that matters most, since
transliterating them carries them into Rust without a compiler complaint:

| Issue | Where | What it costs |
|---|---|---|
| [#299](https://github.com/hherb/bmlib/issues/299) | `llm/json_repair.py:400-401` | `_fix_truncated_json` appends every `]` then every `}`, not reverse-open order. `[{"a": 1}, {"b": 2` cannot be repaired, so `parse_json` falls to the fragment extractor and returns `{"a": 1}` — **the sibling object is dropped with no error**, precisely when the model hit its output ceiling. |
| [#300](https://github.com/hherb/bmlib/issues/300) | `agents/base.py:288-295` | `chat_json`'s truncation shortcut asks "did it parse?", and `parse_json` repairs — so `{"summary": "The study found that metformin` is returned as a complete string field, and `{"n": 12` fabricates a number. |
| [#294](https://github.com/hherb/bmlib/issues/294) | `quality/extractors.py:167-176` | A digit-grouped `n` reads as a fragment (`12,345` → `345`, `n = 12,345` → `12`) or as nothing (`1,000,000` → `None`, score 0.0). The test that owns the input writes the comma form in its comment and not its fixture. |
| [#304](https://github.com/hherb/bmlib/issues/304) | `fulltext/service.py:674` | Tier 1b is gated on `pmc_id` being *empty*, not *usable* — so an unusable caller id suppresses the DOI-discovered PMC fetch, and supplying an id returns strictly less than omitting it. |
| [#305](https://github.com/hherb/bmlib/issues/305) | `fulltext/service.py:969-978` | A cached-PDF hit returns `content_kind="none"` with no abstract where call 1 returned `"abstract"`, and issues no request — so the chain that produced the abstract never runs again. Permanent for that identifier. |
| [#295](https://github.com/hherb/bmlib/issues/295) | `quality/quality_agent.py:167-171` | A field answered `null`, which the prompt *sanctions*, raises in `_parse_data` and degrades the paper to UNCLASSIFIED — and Tier 3 replaces a conclusive Tier 1. |
| [#306](https://github.com/hherb/bmlib/issues/306) | `transparency/analyzer.py:2241,2267,2354` | All three `UNKNOWN` paths store `coi_disclosed=True`, a determinate claim on a run that measured nothing. The sibling statuses are set explicitly *because* a default would be that claim. |

**False claims in persisted prose** — the value is not wrong, the sentence is:

| Issue | Where | What it costs |
|---|---|---|
| [#297](https://github.com/hherb/bmlib/issues/297) | `quality/extractors.py:286-303` | *"No power calculation was performed and confidence intervals were not reported"* is awarded both bonuses **and the audit trail records the opposite**. The module has exclusion machinery and applies it to study type only. |
| [#307](https://github.com/hherb/bmlib/issues/307) | `transparency/analyzer.py:2438` | A CrossRef body with no readable `message` stores *"No funder information in CrossRef"* — the same rule `_INDICATOR_FUNDERS_NOT_READABLE` was split out for, one container up. |
| [#296](https://github.com/hherb/bmlib/issues/296) | `citations/formatter.py:189,267,340` | The inline renderers count whitespace-only authors the reference renderers drop, so a blank first author is attributed to `Unknown` — which `generate_label()` writes into a stored marker. |

**Contract violations and hard failures:**

| Issue | Where | What it costs |
|---|---|---|
| [#301](https://github.com/hherb/bmlib/issues/301) | `llm/providers/ollama.py:100` | `OLLAMA_HOST=localhost:11434/ollama` is read as scheme `localhost` and the provider refuses to construct. A regression from the `file://`/`data:` fix; the regression test covers only the bare form. |
| [#303](https://github.com/hherb/bmlib/issues/303) | `llm/providers/__init__.py:99-147` | `list_providers()` always returns all six, so the documented *"SDK missing"* omission never happens — every provider now imports its SDK lazily, leaving six `except ImportError` branches dead. |
| [#302](https://github.com/hherb/bmlib/issues/302) | `llm/client.py:337,315,373` | `list_models("Ollama")` returns `[]` where `list_models("ollama")` returns 74 models; `get_provider_info("Ollama")` raises. Case is normalised everywhere else in the class. |
| [#309](https://github.com/hherb/bmlib/issues/309) | `fulltext/cache.py:101,239` | Two cache-contract defects: the key is double-hashed for identifiers over 149 chars (the manual says *"the key is never double-hashed"*), and an unreadable PDF entry is served as a hit and never quarantined. |
| [#298](https://github.com/hherb/bmlib/issues/298) | `quality/extractors.py:366-376` | A contrastive mention of a lower study type outranks the paper's own clean higher-tier self-description. Filed as the weakest, with the objection stated. |
| [#308](https://github.com/hherb/bmlib/issues/308) | `llm/token_tracker.py:144` | `get_recent_records(0)` returns every record, since `self._records[-0:]` is `[0:]`. Boundary only; positive counts are correct. |
| [#312](https://github.com/hherb/bmlib/issues/312) | `quality/cochrane_formatter.py:127` | The assessment-summary guard tests `overall_quality_score is not None or assessment.evidence_level` and omits `overall_confidence`, which the block *renders* — so a confidence set on its own is dropped from the output entirely. Every Python fixture sets a score and an evidence level alongside the confidence, so the guard is always satisfied and the line always appears. Found while porting the formatter; the same class as [#299](https://github.com/hherb/bmlib/issues/299) and [#300](https://github.com/hherb/bmlib/issues/300), a value lost with no error and no failing test. |
| [#313](https://github.com/hherb/bmlib/issues/313) | `publications/fetchers/openalex.py:322` | The promised record count is validated with `isinstance(meta.get("count"), int)`, which a **boolean passes** — so `True` becomes `promised` and reaches a caller-facing message as the literal `True`, while `False` records a promise of zero. The comment beside the check states the rule it fails to enforce ("a count is never sent as one"), and `transparency/analyzer.py`'s `_json_count` already excludes `bool` **by name** with `CHANGELOG.md` recording it as a fixed defect — so the library has decided this twice and this site did not apply it. Found while porting `openalex.py`; the port applies the sibling rule. |
| [#310](https://github.com/hherb/bmlib/issues/310) | `quality/cochrane_models.py:493-498` | `CochraneStudyCharacteristics.from_dict` reads `study_id`, `methods` and the four sections by direct index while the five optional fields beside them use `.get()` — in one method, over one dict, with no rule separating them. `QualityAssessment.cochrane_assessment` is typed `Any` and its `to_dict` deliberately tolerates a plain dict, so the write path produces a value the read path raises `KeyError` on. Found while porting; the port reads all six leniently, which is the read path agreeing with the documented write path. |

### What the audits cleared

Worth recording, because a clean audit is evidence and the negatives were the
expensive part:

- **`transparency/` had no high-severity defect survive.** A 20,000-document
  differential fuzz of `_strip_nested_articles` against an independent
  `ElementTree` oracle (nesting, `>` inside quoted attributes, comments, CDATA,
  processing instructions, DOCTYPE internal subsets) found **0 divergences and 0
  false refusals**; `ET.fromstring` raised only `ParseError` across encoding
  declarations, BOM, NUL, bad character references, undeclared entities and deep
  nesting, all caught; all 648 valid enum combinations round-trip
  `from_dict(to_dict(x)) == x`; 200 interleaved two-thread analyses leaked no
  `_api_reachable` state; and both status partitions are true partitions whose
  tests read the same sets the properties do.
- **`llm/json_repair.py`'s comma and single-quote heuristics survived fuzzing** —
  20,000 single-quote and 3,000 comma-deleted documents plus an adversarial case
  list. Every failure was a loud `JSONRepairError`, never a valid-but-wrong
  structure. That is the property the file exists to hold.
- **No defect found** in the segmenter's heading tables, `_titles`' rules, the
  PDF reflow/furniture heuristics, the citation marker parser's numbering and
  adjacency, or `collapse_risk_of_bias()` — each re-derived line by line against
  every documented claim.

Three findings are reported with their counter-argument in the issue itself
rather than suppressed, because the register leaves the decision to the
maintainer: [#296](https://github.com/hherb/bmlib/issues/296) (the citations
register puts upstream behaviour above its own docstrings),
[#309](https://github.com/hherb/bmlib/issues/309) part 1 (the docstring
acknowledges the re-sanitisation, so it may be misdocumented rather than wrong)
and [#298](https://github.com/hherb/bmlib/issues/298).

**For the port, the tables are the specification.** Each row is a behaviour the
Rust implementation must get *right*, and the issue body carries the evidence
and the counter-arguments, so a porter does not have to re-derive whether the
Python really does that. Three observations about the set as a whole:

- **Three of the sixteen are cases where the comment above the line states the
  correct behaviour and the code does the other** — [#299](https://github.com/hherb/bmlib/issues/299),
  [#300](https://github.com/hherb/bmlib/issues/300),
  [#297](https://github.com/hherb/bmlib/issues/297). These are the ones a porting
  session gets wrong *in good faith*, by reading the comment and never checking
  the code. They are worth reading before their module is ported, not after.
- **Three need a decision, not a fix**, because the correct behaviour is not
  determined by code or documentation: [#304](https://github.com/hherb/bmlib/issues/304)
  (may a discovered PMC ID override a well-formed but unserved caller id),
  [#309](https://github.com/hherb/bmlib/issues/309) part 1 (which cache-key
  derivation is canonical), [#298](https://github.com/hherb/bmlib/issues/298)
  (what the study-type priority *should* be). Phase 0 lists them.
- **Thirteen are silent** — they store a wrong value or drop data without
  raising. That is why they survived a 1,700-test suite, and why the Rust port
  cannot rely on its own test suite to catch a regression in them: the tests that
  would catch them are the ones that do not exist yet, and writing them is part
  of porting the module.
