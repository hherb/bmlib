# Junk PDF Metadata Titles: Corroboration, Measured — Design

**Date:** 2026-08-13
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #56
**Ships as:** its own PR

## Problem

`SectionSegmenter._extract_title()` (`bmlib/fulltext/segmenter.py:392`) returns
any truthy `metadata["title"]` verbatim:

```python
title = metadata.get("title")
if title:
    return str(title)
```

Real PDFs frequently carry junk there — `"Microsoft Word - manuscript.docx"`,
`"untitled"`, the source file's name, the typesetter's job number — so junk
metadata beats a perfectly good large-font first-page line. The empty-string
case already falls through to the font-size fallback; what is missing is
rejecting *plausible-looking* junk before believing it.

`PyMuPDFConverter.convert()` (`pdf_converter.py:305`) copies the same raw
string onto `ConversionResult.metadata["title"]`, so every caller reading that
key inherits the same defect. Unlike the segmenter, that path has callers
today.

Deferred from PR #55's review as a heuristic refinement. The issue's own
framing — reject a file-extension suffix, a `Microsoft Word - ` prefix, the
literal `untitled` — is a reject-list, and this repo settles list-shaped
questions by measuring a corpus rather than by taste (the industry-funder
stems, the Markdown escape set, the `DataBankName` allow-list, the free-PDF
availability codes are all precedents).

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| Acceptance rule | **Corroboration against the PDF's own first page, with a measured reject-list as backstop** | Reject-list alone (unbounded tail: every junk shape not in the sample still wins); corroboration alone (cannot catch junk that does appear on page 1, e.g. a running header) |
| Ground truth | **The record title from the API that served the PDF** | Hand-labelling (unnecessary here — unlike funder names, the truth is free and per-row) |
| Corpus | **150 PDFs each from Europe PMC and bioRxiv/medRxiv, fixture committed with per-row page-1 lines** | Europe PMC only (publisher-typeset PDFs have clean metadata — flatters the rule); bioRxiv only (hides the false-positive cost); verdicts-only fixture (a later normaliser change becomes unauditable) |
| Unrunnable corroboration (no text on page 1) | **Accept the metadata title** | Reject (would blank the title of every image-only scan, whose metadata is then the only signal there is) |
| Ties | **Go to rejection** | Go to acceptance |
| Converter | **Additive `ConversionResult.title`; `metadata` stays verbatim** | Sanitise `metadata["title"]` in place (makes one key of a verbatim dict non-verbatim while its neighbours stay raw, and moves what downstream stores); export `title_corroborated` and let every caller re-decide |
| Sampler helpers | **Extract `scripts/_sampling.py`, shared by both samplers** | Duplicate the pacing/`Retry-After` clamp into the new script |

## Design

### 1. Corroboration, and why it is the rule rather than a list

A junk metadata title has one property every shape of it shares, whether or not
we sampled that shape: **it does not appear in the document.** A real title
does — it is printed on page 1. So the test is not "does this look like junk"
but "does the document itself say this":

```python
title = metadata.get("title")
if title and _corroborated_title(title, page_one_text, metadata):
    return str(title)
# fall through to the existing large-font first-page candidate
```

That rejects `"Microsoft Word - manuscript.docx"`, `"untitled"`, `"Document1"`,
a DVI job number and every shape nobody thought to enumerate, under one bounded
rule. The reject-list survives only as a **backstop** for junk that *is* printed
on page 1 — a running header, the journal name — and each member has to earn its
place from the corpus (§4).

### 2. Normalisation

Both sides are normalised before the containment test:

- Unicode NFKD, then drop combining marks — so `Effets d'une thérapie` and its
  decomposed twin compare equal, and a ligature `ﬁ` becomes `fi`.
- Lowercase.
- Keep `[a-z0-9]+` runs as tokens; join with a single space.

That absorbs, in one step, the differences that separate a correct metadata
title from its printed form: case, the trailing period metadata usually drops,
en-dash versus hyphen, `&` versus `and` spacing, and diacritics.

The line break a wrapped title carries needs one step **before** that, when
page 1's lines are joined into the text to search: a line ending in a hyphen is
joined to the next with **no** separator, every other line with a single space.
Without it a title typeset as `Randomised con-` / `trolled trial` normalises to
`randomised con trolled trial`, the metadata title `randomised controlled trial`
is not contained in it, and a perfectly good title is rejected — a
false-positive source that would count against ship rule 1 with nothing in the
output to explain it. De-hyphenation is only ever applied to the page side; a
metadata title carries no line breaks.

The test is `normalise(metadata_title) in normalise(page_one_text)` —
containment, not equality, because page 1 holds authors and an abstract as well.

### 3. The unrunnable case

A page 1 with no extractable text (an image-only scan) makes corroboration a
test that **cannot be run**, not a test that failed. It is accepted, exactly as
the free-PDF sampler counts a 429 as unmeasured rather than as a failure. The
alternative blanks the title of every scanned paper, where the metadata is the
only title signal that exists.

This asymmetry gets its own test, since nothing else would notice it being
tidied away into a uniform "no corroboration → reject".

### 4. Where the logic lives

A new private `bmlib/fulltext/_titles.py`, stdlib-only (`re`, `unicodedata`),
holding the normaliser, the containment test and the backstop predicate. Both
call sites import it:

- `SectionSegmenter._extract_title()` passes the text of the blocks whose
  `page_num == 0`, which it already has in hand.
- `PyMuPDFConverter.convert()` passes page 0's text read from the open `doc`.
  It must **not** use `ConversionResult.page_texts[0]`: that list omits pages
  yielding no text, so its first entry is page 1's text only when page 1 had
  any — precisely the case this rule treats specially.

Private, not public: nothing outside `fulltext` has asked for it, and a public
`is_corroborated_title()` would be an API to keep.

### 5. The converter change

`ConversionResult` gains

```python
title: str | None = None
```

declared **last**, per the repo's positional-stability rule, carrying the
corroborated title or `None`. `metadata` is untouched and stays a verbatim
record of what the PDF says — a caller debugging provenance needs the raw
string, and `creator`/`producer` sit beside it unmodified.

Purely additive: no existing behaviour changes, nothing stored moves.
`docs/manual/fulltext.md` points readers at `result.title` and says plainly
that `metadata["title"]` is the unjudged original.

### 6. The instrument

`scripts/sample_pdf_metadata_titles.py`, a live runner outside the pytest
suite, in the established pattern of `sample_funder_names.py`,
`sample_databank_names.py` and `sample_free_pdf_urls.py`.

**Two sources, for opposite reasons.** Europe PMC `?pdf=render` serves
publisher-typeset PDFs, whose metadata is mostly clean — that population
measures how often the rule wrongly rejects a *good* title, which is the cost
side. bioRxiv/medRxiv serves author-submitted PDFs straight out of Word and
LaTeX, which is where the junk actually lives. Sampling only the first would
flatter the rule; only the second would hide its cost. 150 rows each.

**Ground truth is free.** The Europe PMC search result and the bioRxiv details
API each state the article's title, so every row self-labels by comparing the
metadata title against the record title under the same normaliser:

| Bucket | Test | What it measures |
|---|---|---|
| `match` | normalised equality | the population a wrong rejection would damage |
| `truncated` | metadata title is a normalised prefix of the record title, ≥1 token shorter | a partial title — not junk, but not the whole title either |
| `unrelated` | neither | the junk this issue is about |

**Each PDF goes through bmlib's own code.** Downloaded to a temp file, read
with `PyMuPDFConverter.extract_blocks()`, then deleted — so the committed
fixture holds blocks produced by the same code path the library runs, not by a
parallel implementation in the script that could drift from it.

**A PDF that could not be sampled is never a finding.** A non-200, a 429/503
surviving its retries, a transport exception, a file that is not a PDF, or a
PyMuPDF failure counts as *unmeasured* and is excluded from every denominator.
A source more than 20% unmeasured prints `ERROR` instead of a rate, and the
script exits non-zero — the same rule `sample_free_pdf_urls.py` applies, for
the same reason: a zero is what a healthy population looks like.

Pacing is per host, `Retry-After` honoured and **clamped at both ends**, up to
three attempts. Those helpers, plus `wilson()`, move out of
`sample_free_pdf_urls.py` into a new `scripts/_sampling.py` that both samplers
import — the clamp rule was learned from that script's own first live run
measuring its own throttling, and a rule learned that expensively should not
exist in two copies that can drift. `scripts/` is not a package, but
`sys.path[0]` is the script's own directory when run as
`uv run python scripts/…`; the two test files that load a sampler by path insert
that directory explicitly.

**Output.** `tests/data/pdf_metadata_titles.json`, one row per sampled PDF:

```json
{
  "source": "europepmc",
  "id": "PMC1234567",
  "record_title": "…",
  "metadata_title": "…",
  "creator": "…",
  "producer": "…",
  "file_name": "…",
  "median_font_size": 9.96,
  "page_one_lines": [{"text": "…", "size": 17.2, "bold": true, "y": 72.0}]
}
```

`page_one_lines` is capped at the first 20 lines in reading order — a title,
its authors and their affiliations, which is where the decision is made — so
the fixture stays a few hundred KB and carries no article prose. Rows are
sorted by `(source, id)` so a re-run produces a reviewable diff rather than a
reshuffle.

### 7. Ship rule, fixed before the data is seen

1. Corroboration must wrongly reject a `match`-bucket title in **≤1%** of
   `match` rows. The measured rate and its Wilson interval go in the CHANGELOG.
2. It must reject **≥80%** of `unrelated` rows.
3. Every backstop candidate — the file-extension suffix, the
   `Microsoft Word - ` / `Microsoft PowerPoint - ` prefix, the literal
   `untitled` / `no title` / `document1`, equality with the file's own stem, a
   bare DOI, and a minimum-token guard — is measured **independently** and
   ships only on **≥1 `unrelated` row it rejects that corroboration accepted,
   and 0 `match` rows it rejects.** A backstop the corpus does not need does
   not ship, and the design records what was rejected and why. The file-stem
   candidate is expected to fail that bar for a reason the corpus cannot see:
   in production `metadata["file_path"]` is the cache name `fetch_fulltext()`
   built through `sanitize_identifier()`, not the publisher's filename, so the
   rule can only fire for a caller who converted a file of their own naming.
4. Ties go to rejection: a junk title is asserted as fact, while a wrongly
   rejected good title falls through to the font heuristic that usually
   recovers it.
5. If rule 1 or 2 fails, the finding and the numbers go in the PR description
   and the design is revisited — not quietly relaxed to fit.

The corpus additionally reports, for the rows where a metadata title is
rejected, **how often the font-size fallback then recovers the record title.**
That number does not gate the ship rule — a junk title is worse than no title
either way — but it is the honest measure of what the fix buys, and it belongs
in the CHANGELOG rather than in nobody's head.

## Testing

`tests/test_segmenter.py`:

- one case per junk shape the corpus actually shows, each named for the shape;
- the negative control: a legitimate title printed on page 1 is **accepted** —
  without it, a rule that rejects everything passes every other test;
- rejection falls through to the large-font candidate, and to `None` when that
  candidate does not clear `_TITLE_SIZE_RATIO`;
- the unrunnable case: no text on page 1 → the metadata title is accepted;
- the normaliser's own cases: case, trailing period, wrapped line, en-dash,
  diacritics.

`tests/test_pdf_converter.py`: `ConversionResult.title` is the corroborated
title, is `None` when the metadata title is junk, and `metadata["title"]`
remains verbatim in both cases.

`tests/test_pdf_metadata_titles.py`: the metric test. Loads the committed
fixture, rebuilds `TextBlock`s from `page_one_lines`, runs the **real**
`_extract_title` over every row, and asserts the measured rates meet the floors
this PR establishes. Offline, per the repo's standing no-network rule. It calls
`_extract_title` directly rather than `segment_document`, passing the row's
stored `median_font_size`: the median is a parameter of that method, and
recomputing it from 20 stored page-1 lines would measure a title-page median
instead of the document's — which is exactly the comparison
`_TITLE_SIZE_RATIO` exists to make.

`tests/test_pdf_title_sampler.py`: the script through a stubbed client, on the
principle its two predecessors already pin — a PDF that could not be sampled
must never print as a finding, and an unmeasured share above the threshold must
print `ERROR` rather than a flattering number.

`tests/test_free_pdf_sampler.py`: unchanged in substance; gains the `sys.path`
insert the extraction requires, and its coverage of the moved helpers follows
them to `tests/test_sampling_helpers.py`.

## Documentation

- `CHANGELOG.md` under `[Unreleased]`: the rule, the measured numbers, the
  additive `ConversionResult.title`, and the note that `metadata["title"]`
  deliberately stays verbatim.
- `docs/manual/fulltext.md`: what corroboration is, why the unrunnable case is
  accepted, and which field to read.
- `CLAUDE.md`: the test-file mapping row for the new script, and a line in the
  `fulltext/` description naming the corroboration rule — with the instruction
  to **run the sampler before changing the reject-list**, as the DataBankName
  and free-PDF allow-lists already carry.
- `ROADMAP.md` and `HANDOVER.md`: the closed issue and the new instrument.

## Verification

`uv run pytest tests/ -v`, then `uvx ruff@0.15.20 check . && uvx ruff@0.15.20
format --check .` — the CI-pinned ruff, not the older one in `.venv`.
