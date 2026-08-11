# A silent PDF tier: report what it swallows, and stop it discarding 95% of its input — design

_Issues [#68](https://github.com/hherb/bmlib/issues/68),
[#72](https://github.com/hherb/bmlib/issues/72) and
[#79](https://github.com/hherb/bmlib/issues/79). Written 2026-08-11._

Three defects in one tier, taken together because they share one mechanism and
one measurement. #68 and #72 are the last two survivors of the #67 family — a
failure that reads as a success — and #79 was found while designing #68's
measurement, before the instrument existed.

## The three defects

### #79 — Tier 1d rejects about 95% of the free PDFs it exists to find

`_extract_free_pdf_url()` accepts a `fullTextUrlList` entry only when it is
`documentStyle == "pdf"` **and** `availability == "Free"`. Europe PMC uses three
values in that field, and `"Free"` is the rare one.

Measured over 600 recent MEDLINE records
(`(SRC:MED) AND (FIRST_PDATE:[2024-01-01 TO 2025-12-31])`, six cursor pages of
100, `resultType=core`) — 326 entries with `documentStyle == "pdf"`:

| `availability` | `availabilityCode` | pdf entries | share |
|---|---|---:|---:|
| `Open access` | `OA` | 312 | **95.7%** |
| `Free` | `F` | 14 | 4.3% |

The complete value space across all 1,263 `fullTextUrl` entries in that sample.
There is no fourth value, and every entry carried a code:

| `availability` | `availabilityCode` | entries |
|---|---|---:|
| `Open access` | `OA` | 628 |
| `Subscription required` | `S` | 596 |
| `Free` | `F` | 39 |

Both accepted labels are the identical shape on the identical host —
`https://europepmc.org/articles/PMC…?pdf=render`. Three `Open access` URLs
probed end to end returned HTTP 200, `content-type: application/pdf` and `%PDF-`
magic bytes: ordinary, working PDFs. If anything `Open access` is the *more*
permissive licence of the two.

So an article Europe PMC will serve as a free PDF falls through to Unpaywall or
to a bare DOI link about 95% of the time, and **nothing is logged at any
level** — there is no line for "a PDF entry was seen and not taken". This is
invisible in exactly the way #67, #68 and #72 are about.

Not a deliberate non-fix: no entry in `docs/DECISIONS.md`, and the originating
commit (f7d2a6c, "Add Europe PMC PDF render fallback") reads "when JATS XML is
unavailable from Europe PMC but a free PDF exists". The intent was permissive;
the implementation matched one label out of three. An unexamined narrowing
rather than a chosen one.

### #68 — a failed PDF download is invisible at default log level

`_download_and_cache_pdf` swallows three distinct outcomes at DEBUG: a non-200
for a URL some tier just declared a free PDF, a `save_pdf()` returning `None`
(magic-byte validation rejected the bytes), and any exception at all. In every
case the caller gets `pdf_url` set, `file_path` unset, `html` unset,
`content_kind="none"` — and with `convert_pdfs=True` they asked for text and got
none. A full disk across a 10,000-paper run looks exactly like 10,000 publishers
404ing.

It is milder than #67 — `pdf_url` with no `file_path` is a real signal, where
#67's result was byte-identical to a paywalled paper — and it genuinely cannot
feed #67's exhaustion report: all three call sites `return` immediately
afterwards, so a failure noted there could never reach the report that reads it.

### #72 — a bmlib bug stays at DEBUG whenever a later tier succeeds

`_TierFailures.describe()` is consulted at one exit: total exhaustion. An
`AttributeError` from every PMC tier — the shape a `JATSArticle` API change
takes — with Unpaywall healthy degrades a whole corpus from structured JATS to
bare links, reports success, and says nothing above DEBUG.

`except Exception` at the tier level is right for transport errors. It is wrong
to hold a `TypeError` at DEBUG under any circumstances.

## Why one design

#68's exception path and #72's bug path want the *same* mechanism: a warning
emitted once per distinct cause per service, because both failure modes hit
every article in a run when they hit at all. #79 has to land first or the
measurement #68 depends on would characterise a 4.3% tail of a tier that is
discarding the other 95.7%.

## A. #79 — the availability filter

Allow-list on `availabilityCode`:

```python
_FREE_PDF_AVAILABILITY_CODES = frozenset({"OA", "F"})
_FREE_PDF_AVAILABILITY_LABELS = frozenset({"Open access", "Free"})
```

An entry is accepted when its code is in the first set, or — for an entry
carrying no code — its display string is in the second.

**An allow-list rather than a deny-list on `S`.** An unknown future value must
under-credit rather than send bmlib to download a paywalled PDF. This is the
`_DEPOSITION_DATABANK_LEVELS` reasoning: a gap costs a retrieval, a
too-generous default costs a wrong one.

**The code is preferred over the string** because it is a controlled short
vocabulary where the display string is presentation, and the string is kept as a
fallback rather than dropped because absence of the code was measured at zero in
this sample but is not guaranteed by anything Europe PMC documents.

The measured value space above goes in the docstring as the evidence, and
`scripts/sample_free_pdf_urls.py` (below) is what keeps it answerable to the
records.

**Tests:** one per accepted code, one per accepted label with the code absent,
`"S"`/`"Subscription required"` rejected, an unknown code rejected, an entry
whose `documentStyle` is not `pdf` rejected regardless of availability, and a
negative control asserting the fixture would be accepted if the filter were
removed — so a test that cannot fail is not mistaken for one that passes.

## B. `scripts/sample_free_pdf_urls.py` — the measurement

### What it measures, and what it deliberately does not

The failure rate of a PDF download **given bmlib already holds the URL**. That
is the quantity #68's level turns on. It is deliberately *not* "how often does
Tier 1d fire" — reachability governs how often the code runs, not how often it
fails when it does, and conflating them would let #79's fix silently change the
number #68 was set from.

### Three populations, one per call site

`_download_and_cache_pdf` has three callers, and their URL populations are not
alike: Europe PMC serves its own host, Unpaywall points at arbitrary
repositories and often at a landing page rather than a PDF — which is exactly
the magic-byte rejection — and the fetchers build their own links.

| Population | Call site | Drawn from |
|---|---|---|
| Europe PMC render | Tier 1d | `fullTextUrlList` of the search sample |
| Unpaywall | Tier 2 | `_fetch_unpaywall`'s own logic over the sample's DOIs |
| bioRxiv / medRxiv | Tier 0 | `fetch_biorxiv` itself |

One Europe PMC search draws the first two on the *same* papers, which makes
their rates directly comparable. Unpaywall's half is drawn from
`inEPMC != "Y"` records, since those are the ones that actually reach Tier 2.
The third calls `fetch_biorxiv` rather than re-spelling its URL template, so the
URL under test is literally the one bmlib builds and cannot drift from it.

### The probe

A ranged `GET` for the first ~1 KB, recording both of bmlib's failure modes —
the status code, and whether the bytes begin `%PDF`. Ranged so that measuring
does not mean downloading 900 complete PDFs.

Identified as
`bmlib-sampler/<version> (+https://github.com/hherb/bmlib; <email>)`, which is
what Unpaywall and NCBI ask of callers, and which also protects the measurement:
some repositories serve a challenge page to an unidentified client, and a
challenge page is HTML, so being blocked would be miscounted as the very
magic-byte failure being measured.

~300 per population, one request per second per host.

### Output

Per population: attempts, non-200 by status, magic-byte rejections, exceptions
by type, and a Wilson interval on the total failure rate — an interval rather
than a point estimate because the decision rule below has a threshold in it, and
a point estimate near that threshold would misrepresent what the sample settles.

Europe PMC's is additionally split by `inEPMC`, since a record whose XML is
unusable may well have a worse PDF too, and that subgroup is the one Tier 1d
actually reaches. **Stated limitation:** `inEPMC` approximates "XML unusable";
measuring it exactly would cost one `fullTextXML` request per sampled record.

**A probe that could not be made prints `ERROR`, never a zero.**
`sample_databank_names.py`'s rule, for the same reason: a zero is what a genuine
finding looks like, and a transient network failure must not be readable as one
— here it would be evidence for the wrong log level.

### Tests

`tests/test_databank_sampler.py`'s shape — offline, through a stubbed fetch.
The module is loaded by path, `scripts/` not being a package. What the tests pin
is that a request that failed never prints as a finding, that a non-200 and a
magic-byte rejection are counted in their own buckets rather than merged, and
that the Wilson interval is computed over attempts actually made rather than
attempts planned.

## C. #72 — bug-shaped exceptions

```python
_BUG_TYPES = (TypeError, AttributeError, NameError, KeyError, IndexError)
```

A deny-list rather than an allow-list because the legitimate remote-data
failures are varied (`FullTextError`, `httpx.HTTPError`,
`json.JSONDecodeError`, `ET.ParseError`, `OSError`) while the set that always
means a bmlib defect is small and stable. `NameError` carries
`UnboundLocalError` in by inheritance.

### What must never be a member, and why

Verified on this platform rather than assumed:

| Excluded type | The MRO that forces it | Why it is excluded |
|---|---|---|
| `ValueError` | `JSONDecodeError → ValueError` | every `resp.json()` on a malformed body raises one |
| `SyntaxError` | `ET.ParseError → SyntaxError` | malformed remote XML raises one |
| `RuntimeError` | `RecursionError → RuntimeError` | that, and `Path.home()` raises one directly |
| `OSError` | — | environment, not defect |

The first two are the load-bearing ones: "a `SyntaxError` is always a bug" and
"a `ValueError` is always a bug" are both intuitive and both exactly backwards
here. Each exclusion gets a test naming the type it protects, because widening
a deny-list catches strictly more and no test that merely exercises a working
retrieval could fail on it.

### The honest caveat

`AttributeError` **is** reachable from remote data.
`data.get("resultList", {}).get("result", [])` raises it when Europe PMC returns
a non-dict for `resultList`, and `resp.json().get("records")` raises it when a
body is a JSON array. It stays in the list — that is a bmlib defect, a missing
shape check, and it should be fixed rather than tolerated — but the message
**describes what happened rather than accusing the article**, and names the
possibility that an unexpected API response provoked it.

### Where it fires

`_TierFailures` gains an `on_bug: Callable[[BaseException], None] | None = None`
supplied at construction, and `record()` calls it at the moment of the swallow.

Every alternative reads the record at an *exit*, which is precisely #72's
complaint: `describe()` is already consulted at one exit and that is why the bug
is invisible. Reporting at the swallow is the only shape that cannot be
re-broken by the next early return added to the chain. Defaulting to `None`
keeps the bare `_TierFailures()` construction in existing tests working.

**Cadence and level: WARNING, once per `(service, exception type)`.** Per type
rather than per service so that an `AttributeError` and a later `TypeError` are
both reported instead of the second hiding behind the first — the strict
one-flag precedent would reproduce #67's own "the more complete the failure, the
quieter it gets" shape. WARNING rather than ERROR keeps it on the same shelf as
every other "bmlib degraded and you should know" line in `fulltext/`, so an
operator filtering at WARNING sees the exhausted-chain report, the unwritable
cache, the missing PDF backend and this together.

## D. #68 — the download failure report

Three causes, named separately, keyed per `(result.source, cause)`. The source
is already on the `result` argument the method receives, so it costs nothing and
means a flaky Unpaywall cannot suppress a Europe PMC report.

### The exception path needs no measurement

A network error or an `OSError` fails *every* article once it starts failing, so
a per-article WARNING can never be right for it. **One-shot WARNING per
`(source, exception type)`** — the same mechanism as C, which is why #68 and #72
belong in one change rather than two.

### The other two are set by the measurement, against a rule fixed in advance

The rule is written here, before the numbers land, so it cannot be rationalised
afterwards:

- **< 5% of download attempts → per-article WARNING.** A 10,000-paper run yields
  under 500 lines, each naming one article an operator could chase.
- **≥ 5% → one-shot WARNING per `(source, cause)`, plus per-article DEBUG.**

Applied to the **worst** population, since one setting governs all three call
sites. The per-population numbers go into the code comment beside the choice, so
a later session can revisit it without rebuilding the instrument.

The two causes stay distinct in the message either way: a non-200 is the server
answering badly, a magic-byte rejection is the server answering with something
that is not a PDF — usually a landing page — and reporting one as the other is
the mistake `_save_pdf_to_cache` already avoids between a failed write and a
failed validation.

### One mechanism for all of it

```python
def _warn_once(self, key: str, msg: str, *args: object) -> None:
```

backed by `self._warned: set[str]`. The two existing booleans
(`_pdf_backend_warned`, `_cache_write_warned`) migrate onto it. Nothing
observable changes — no test references either attribute; both are asserted
through log output — and leaving two boolean flags beside a keyed set doing the
same job would read as accidental rather than chosen. A keyed set is also
strictly more capable, which is what C's per-type cadence needs.

## Verification

- **TDD throughout.** Behaviour tests first, watched red before any fix.
- **Mutation-test each load-bearing guard**, in particular the deny-list
  exclusions (narrow `_BUG_TYPES` to `TypeError` alone and the `AttributeError`
  test must fail), the `on_bug` call site, and #79's allow-list. Clear
  `__pycache__` after restoring each mutation.
- Full suite plus both `ruff@0.15.20` commands clean; the PostgreSQL half of
  `test_backends.py` run locally.
- One branch, one PR closing #68, #72 and #79.

## Release note

#79 is a `### Changed` behaviour entry, not merely a fix: many more articles
come back with `pdf_url`, `file_path` and extracted text instead of a bare link,
so downstream stored full text moves and is not comparable across the change. It
also increases outbound traffic, since PDFs that were previously skipped are now
downloaded. #68 and #72 add log lines only.

## Out of scope

- **A structured field on `FullTextResult` recording why a download failed.**
  Callers could then branch on the cause rather than reading logs. It is a
  public API addition, and 0.9.0's lesson was that those force a minor bump on
  their own. Worth its own issue if a downstream asks.
- **Shape validation on Europe PMC's JSON**, which is what would make the
  `AttributeError` above unreachable. Named by C's caveat; fixing it is a
  separate change with its own measurement of what the API actually returns.
