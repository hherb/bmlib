# A second source for PMC ID resolution, and NCBI as a full-text tier

_Design, 2026-08-02. Implements issue #47._

## The problem

`FullTextService` can reach a PMC ID exactly one way — `_resolve_pmc_id_and_pdf_url()`,
which searches Europe PMC and then reads:

```python
pmc_id = hit.get("pmcid") if hit.get("inEPMC") == "Y" else None
```

That is gated on Europe PMC *both* indexing the paper **and** flagging its full
text as available there. A paper in PMC that fails either condition resolves to
no PMC ID, so Tiers 1a/1b are skipped and retrieval falls through to Unpaywall
or a bare DOI link.

NCBI's [ID Converter](https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/) is
the authoritative DOI/PMID→PMCID mapping and depends on neither condition. The
divergences are real but not large — Europe PMC mirrors PMC comprehensively —
and are mostly recent deposits inside Europe PMC's indexing lag, author
manuscripts, and DOI-formatting misses where the search returns no hit at all.

But a discovered PMC ID is only worth having if there is somewhere to send it.
Today the only destination is `_fetch_europepmc()`, which reads Europe PMC's
`fullTextXML` endpoint — the same corpus `inEPMC` flags. For a hit flagged
`"N"`, Europe PMC has just said it does not hold that full text, so the
converter would buy a 404. **The converter needs a second full-text source to
pay off, and NCBI is already the one that has the article.** So this design
does both: it adds the converter as a resolution fallback and NCBI's own
`efetch db=pmc` as a retrieval tier.

## Shape

Two new private helpers on `FullTextService`, both reached through the existing
`_http_get` seam that the tests patch:

```python
def _resolve_pmc_id_via_idconv(self, *, doi: str | None = None, pmid: str = "") -> str | None
def _fetch_ncbi_pmc(self, pmc_id: str) -> tuple[str, bool]
```

`_fetch_ncbi_pmc()` returns `(html, has_body)`, the same contract as
`_fetch_europepmc()`, so the caller's body-less handling is unchanged.

## Decisions

### NCBI is a tier of its own, for any PMC ID in hand

efetch pays off whenever PMC holds an article Europe PMC does not serve. That
is just as true for a PMC ID the *caller* supplied — Tier 1a, whose failure is
already tracked in `xml_failed` — as for one the converter discovered.
Attaching efetch to the discovery path alone would leave the identical gap open
on Tier 1a, one conditional away from the code that fixes it.

So the chain gains one tier, which fires whenever a PMC ID is in hand —
supplied by the caller or discovered by either resolver. Reaching it at all
means no tier has produced full text, since every success returns immediately:

```
Tier 1a  Europe PMC XML, caller-supplied PMC ID          (unchanged)
Tier 1b  Europe PMC search → PMC ID → XML                (unchanged)
   1b′   …search gave no PMC ID → ID Converter → XML     (new)
Tier 1c  NCBI PMC efetch, for whichever PMC ID we hold   (new)
Tier 1d  Europe PMC free PDF render                      (renumbered from 1c)
Tier 2   Unpaywall                                       (unchanged)
Tier 3   DOI / PubMed URL                                (unchanged)
```

NCBI's XML goes **before** the free-PDF tier. Structured JATS beats a PDF that
needs the optional `bmlib[pdf]` extra to read at all and loses figures and
layout when it is read.

The renumbering is real and the manual's tier table changes with it. Tiers are
an order, and the new one belongs in the middle of it; keeping the old numbers
by calling the new tier `1b″` would preserve a label at the cost of the
ordering the label exists to convey.

### The converter is consulted second, never first

Europe PMC's search returns the PMC ID **and** the free-PDF URL that feeds the
render tier in a single request. Querying the converter first would either cost
a second HTTP request on every lookup, or forfeit that PDF URL. Consulting it
second spends the extra request only in the case where the service currently
gives up.

An earlier unmerged branch (recorded in issue #47, since deleted) tried the
converter first. That ordering is the one this design deliberately inverts.

### `_resolve_pmc_id_and_pdf_url()` keeps its name, its job and its arity

The issue proposed extending that method to consult the converter. It is called
from two places, and the second one — the PDF-URL recovery at `service.py:225`
— discards the PMC ID with `_,` because a PMC ID was already known and its XML
had just failed. Folding the converter in would make that call site pay for a
request whose result it throws away, which a keyword flag could suppress at the
cost of a parameter whose only purpose is to say "not from here".

Instead the converter is a separate helper called from Tier 1b, where the PMC
ID is actually wanted. The search method keeps one job — the Europe PMC search
— and its 2-tuple return. Distinguishing "no hit" from "hit flagged `N`" would
need a third element, and PR #42 has just moved this module's neighbour off
multi-element tuples for reasons that apply here unchanged.

### A converter-discovered ID is tried at Europe PMC even when the hit said `inEPMC="N"`

This is a deliberate redundancy: for that sub-case Europe PMC has already said
it lacks the full text, so the attempt is near-certainly a 404 before NCBI gets
the ID.

Believing the flag would save one request per affected paper and needs the
third return value the section above rejects, plus the state to carry it. A
stale or wrong flag is also one of the reasons the converter exists, so
believing it forfeits part of what is being bought. **Deferred, not dismissed:**
if this shows up as a measurable cost in a bulk run, it is a self-contained
optimisation — the information is available at the call site.

### `PMC\d+` is enforced where a PMC ID becomes a URL

The converter's `pmcid` is third-party text interpolated into a URL path — the
hazard `bmlib.transparency` already answers by validating `<DataBankList>`
accessions as `NCT\d{8}` before they reach a ClinicalTrials.gov URL.

Validating inside the two fetch helpers, rather than at the converter, covers
converter-supplied, Europe-PMC-supplied and caller-supplied IDs with one guard,
at the only point where the value is dangerous. A malformed ID raises
`FullTextError`, which every tier already catches and logs — the same outcome a
404 produced, minus the request.

`_fetch_europepmc()` keeps prefixing a bare numeric ID with `PMC`, so validation
happens after normalisation. `_fetch_ncbi_pmc()` sends the digits alone, which
is efetch's documented form.

### The converter helper never raises, and never returns a non-live record

`_resolve_pmc_id_via_idconv()` returns `None` on every failure: non-200,
unparseable JSON, no `records`, `record["status"] == "error"`,
`live == "false"`, a missing `pmcid`, or a `pmcid` that fails the pattern.

It catches `Exception` internally rather than relying on Tier 1b's `except`.
An escape there would skip the rest of the block and discard the free-PDF URL
the Europe PMC search had *already* returned — trading a working PDF tier for a
failed converter lookup.

`live == "false"` means PMC no longer serves the record. Treating it as no ID
costs nothing: the fetch that followed would fail anyway, one request later.

### An efetch stub raises rather than becoming an abstract

efetch answers a non-OA article with a stub rather than a refusal. Parsed, that
yields a document with neither body nor abstract, and the existing body-less
machinery would promote it to `abstract_only` — returning near-empty HTML
labelled `content_kind="abstract"`, which is worse than the bare DOI link it
displaced, and permanent for callers that persist the result.

So `_fetch_ncbi_pmc()` raises `FullTextError` when the parse has neither
`has_body` nor `abstract_sections`. A genuine body-less NCBI article — front
matter carrying a real abstract — still flows into `abstract_only`, exactly as
Europe PMC's does.

### `ncbi_api_key`, declared last

Both new requests hit NCBI, which meters anonymous callers at 3 requests/second
per IP and 10/second with a key. `FullTextService.__init__` gains
`ncbi_api_key: str | None = None` **declared last**, for the same reason as
`Publication.pmcid` and `BaseAgent.embedding_model`: downstream projects
construct these positionally, and any other placement lands a caller's argument
in the wrong field with no error anywhere.

It mirrors `TransparencyAnalyzer.pubmed_api_key`, including the limits of the
promise: the key changes which NCBI allowance the request draws on, not bmlib's
own pacing. bmlib still throttles nothing, and the manual says so.

Both requests also carry `tool` and `email`, as NCBI asks.

### The new source string is `"ncbi_pmc"`, not `"pmc"`

`"pmc"` is already in use as a Tier 0 `FullTextSourceEntry.source` value from a
publication fetcher. Reusing it would make `result.source` ambiguous about
which of two very different paths produced the text.

## Behaviour changes

None is behind a flag.

- **A caller supplying `pmc_id` whose Europe PMC XML fails now costs one extra
  request** — the new NCBI tier — before the chain moves on. In exchange, a
  paper PMC serves and Europe PMC does not now returns full text where it
  previously returned a PDF link or a bare DOI.
- **A lookup by DOI or PMID that Europe PMC cannot resolve now costs one extra
  request** (the converter), and one more (efetch) when the converter finds an
  ID. Both spend only in cases that currently reach Unpaywall or Tier 3.
- **`FullTextResult.source` gains the value `"ncbi_pmc"`.** Callers switching on
  `source` see a value they have not seen before.
- **Some results move from `content_kind="abstract"` or a bare `web_url` to
  `content_kind="fulltext"`.** That is the point of the change, but a caller
  that cached or persisted the earlier outcome will find the two
  non-comparable.

## Testing

Mocked HTTP throughout, following `tests/test_fulltext_service.py`'s
`patch.object(service, "_http_get", side_effect=[...])` pattern. No new
fixtures beyond an efetch stub document and a reuse of the existing
`abstract_only_article.xml`.

New coverage:

- the converter is consulted when the search returns no hit, and when it
  returns a hit without a usable `pmcid`
- it is **not** consulted when the search found one
- a malformed `pmcid` never reaches a URL
- `status: "error"` and `live: "false"` records resolve to no ID
- a converter failure does not cost the free-PDF URL the search already found
- efetch is reached from a caller-supplied ID and from a discovered one
- the efetch stub raises rather than becoming an abstract-only result
- `api_key` appears in both NCBI requests when configured, and in neither when
  not

**Known blast radius.** Tests that pin exact request sequences — `TestBodylessJATS`
around `tests/test_fulltext_service.py:639` uses
`side_effect=[mock_xml, mock_search, mock_search]` — now see additional
requests. Left alone they would not fail loudly: `StopIteration` from an
exhausted `side_effect` is an `Exception` and would be swallowed by the new
converter guard, and a search-shaped mock read as a converter response yields
`None` by the ordinary rules. They must therefore be updated so each mock names
the request it answers, not left to pass by coincidence.

## Out of scope

- **Rate limiting.** bmlib throttles nothing and this does not change that;
  bulk callers self-throttle. Tracked separately in ROADMAP.
- **Skipping the Europe PMC attempt on an `inEPMC="N"` hit.** Deferred above,
  to be revisited only if measured.
- **The deleted branch's other three parts** — a constructed PMC PDF URL,
  `%PDF` magic-byte validation at the download site, and `pdf_url` on a
  successful XML result. Issue #47 records why each is superseded by something
  already on main.
