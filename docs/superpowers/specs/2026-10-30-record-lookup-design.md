# One paper's record, by identifier

_StudyAnalyzer's item 5 (its design §4, §7). Python and Rust, held to each
other by a shared contract fixture. Written 2026-10-30 with the maintainer.
Two PRs, in order: **part 1** fixes `FullTextService`'s first tier, **part 2**
is the lookup._

## Why

StudyAnalyzer opens a paper from a PMID, a DOI, a PMC ID or a Europe PMC
preprint accession (`PPR` + digits), typed on its start screen or carried by a
hand-off file. It needs the paper's title, authors, journal, date and abstract
to show and store, and it then asks `FullTextService` for the full text. bmlib
parses a PubMed record and an OpenAlex work into a `FetchedRecord` in both
languages, but only inside the day-by-day sync walkers: no call takes one
identifier and returns one record. Shared logic lives in bmlib (the
maintainer's rule), so the lookup is written there for every app.

**Getting as much full text as possible matters most** (the maintainer). The
record is what the app hands to
`FullTextService.fetch_fulltext(fulltext_sources=…, pmc_id=…, doi=…, pmid=…)`,
so every identifier and open-access location the lookup misses is a tier of
the full-text chain that never runs. That is why every source is asked, and
not only as a fallback, and why part 1 comes first.

## Part 1: a caller's link no longer ends the chain

**The defect** (found reviewing this design; pre-existing, both languages).
`FullTextService` tries the caller's `fulltext_sources` first (Tier 0), in the
order xml, pdf, html, and an `html` entry **returns at once** as
`FullTextResult(web_url=…)`: nothing is fetched, and Europe PMC, the PMC
tiers, Unpaywall and Tier 3 never run. The PubMed parser gives every record
with a PMC ID an `html` entry for its PMC page and every record with a DOI an
`html` entry for `https://doi.org/{doi}` (`pubmed.py` `_parse_article_xml`;
Rust `parse_article`), and OpenAlex's normaliser one per location's landing
page. So a caller passing a synced record's stored sources — BioMedicalNews's
`gui/routes/papers.py` does — gets a publisher link where Europe PMC holds the
article.

**The fix** (the maintainer's decision: fix `FullTextService`, own PR, Python
and Rust): Tier 0 tries the caller's `xml` and `pdf` entries as now, and an
`html` entry is **held** instead of returned. The chain runs on through every
tier that can fetch text. Only when none returns text is a held link used: the
first held `html` entry, ahead of Tier 3's generated DOI / PubMed link (the
caller's link is the caller's choice; Tier 3's is bmlib's guess). The
abstract-beside-a-link merge (`_with_abstract_fallback`) applies to it as it
does to Tier 3's. Nothing else in the chain moves.

**What moves for a downstream**: a call that used to return a link now
returns full text where any tier finds it, at the cost of the requests those
tiers make. A CHANGELOG entry under *Changed*; `docs/manual/fulltext.md`
states the new order. Issue to be filed for it, and the PR closes it.

## Part 2: the lookup

### Goals

1. **One call**: given any of a PMID, a DOI, a PMC ID and a PPR accession,
   return the paper's `FetchedRecord` (the existing type, unchanged).
2. **PubMed first** (the maintainer's decision): when the paper has a PMID
   (given, or learned from another source) and PubMed holds it, PubMed's
   record is the record (`source="pubmed"`), with its grants, affiliations and
   publication types.
3. **OpenAlex second** (the maintainer's decision), and **always asked**:
   where PubMed has the paper, OpenAlex adds only what helps find full text —
   an identifier PubMed's record lacks, and its open-access locations,
   appended to `fulltext_sources`. Where PubMed does not, OpenAlex's record is
   the record (`source="openalex"`).
4. **Europe PMC for a PPR accession** (the maintainer's decision), and only
   for one: its record supplies the preprint's DOI, from which OpenAlex and
   PubMed are asked as above; see *A preprint* for whose record it is.
5. **Outcomes an app can tell apart, per source** (below). An outage must
   never read as "no such paper" (StudyAnalyzer plan 2a's rule).
6. **A partial answer is an answer**: one source failing still returns the
   others' record, and that source's outcome says it failed, so the app can
   retry later for more full-text links.
7. **Both languages**: Python `bmlib.publications`, Rust `bmlib::publications`,
   the same result for the same responses, held by one committed fixture both
   suites replay.

### Non-goals

- Fetching full text: `FullTextService` does that, called next by the app.
- The NCBI ID Converter and Unpaywall: `FullTextService` asks both already.
- Crossref; bioRxiv's API; Europe PMC for anything but a PPR accession.
- Merging sources field by field: the record's own source's metadata stands
  whole; only identifiers and full-text locations are added.
- Storing anything, and retrying (no bmlib request retries; the app does).

### Anti-goals

- **A record for a different paper, or a different version of it.** The
  PubMed record used is the `<PubmedArticle>` whose own `MedlineCitation/PMID`
  is the PMID sent (#474's rule). An OpenAlex work must carry the identifier
  it was looked up by. A preprint's record carries no identifier of its
  journal version (*A preprint*, below).
- **An outage read as an absence**, at any source.
- **Overwriting what the caller gave.**

### The flow

1. **Validate at the entry, before any request** — a `ValueError` in Python
   and an error in Rust, naming the identifier — and canonicalise:
   - **PMID**: transparency's caller rule (`_caller_pmid`: stripped, ASCII
     digits, not all zeros, leading zeros dropped; #470). Python restates it
     privately with a test holding it to agree, as `fulltext` already does;
     Rust has `retractions::canonical_pmid`.
   - **DOI**: stripped, a blank one none, a `doi:` / `https://doi.org/` prefix
     removed and lower-cased (`normalize_doi`'s rule); **no shape check**
     (#475, the maintainer's earlier choice).
   - **PMC ID**: `PMC` + ASCII digits, the `PMC` optional and case-folded
     (`fulltext`'s `_normalise_pmc_id` rule, raising `ValueError` here).
   - **PPR**: `PPR` + ASCII digits, case-folded (transparency's
     `(?:PMC|PPR)\d+` rule, `re.ASCII`).
   - None at all is the same error. *The identity* of the lookup is the first
     present of PMID, PMC ID, PPR, DOI; the others only fill empty fields.
2. **Europe PMC, for a PPR only**: `search?query=EXT_ID:{ppr} AND SRC:PPR
   &resultType=core&format=json` (measured 2026-10-30: a hit carries `doi`,
   `title`, `authorList`, `abstractText` as JATS-style markup,
   `firstPublicationDate`, `bookOrReportDetails.publisher` (the server),
   `grantsList`, `fullTextUrlList`, `isOpenAccess`, `inEPMC`, `license`;
   an unknown accession is `hitCount: 0`). A new normaliser into
   `FetchedRecord` (`source="europepmc"`), the accession kept in
   `extras["ppr_id"]`. Where `inEPMC` is `Y`, `fulltext_sources` gains an
   `xml` entry for `…/rest/{ppr}/fullTextXML` (measured 200 for the probe),
   which `FullTextService`'s Tier 0 fetches and parses — the only way it can
   reach a PPR's full text, since its PMC tiers refuse a PPR.
3. **OpenAlex, once**: `GET https://api.openalex.org/works/{key}` by the
   first present of `pmid:`, `pmcid:`, `doi:` (the PPR's DOI counts), with
   `mailto` and `api_key` if given. Measured 2026-10-30: all three forms
   answer 200 with the work and an unknown id a plain HTTP 404 (an HTML page).
4. **The PMID** is the caller's, else OpenAlex's.
5. **PubMed**, when there is a PMID: one efetch by id (`db=pubmed`,
   `retmode=xml`, `api_key` if given), parsed by the existing PubMed parser,
   keeping the matching `<PubmedArticle>`.
6. **Assemble** (*A preprint* first decides whether PubMed's record and
   OpenAlex's PMID and PMC ID belong to this paper): the record is PubMed's
   if found, else Europe PMC's, else OpenAlex's, else none. Each empty
   identifier is filled from the caller, then from the other sources;
   `fulltext_sources` gains every other source's entry whose URL is not
   already present; `is_open_access` is true if any source says so. A DOI or
   PPR lookup PubMed did not confirm takes neither OpenAlex's PMID nor its PMC
   ID (the PR review's rule: for a merged preprint they are the journal
   version's).

### A preprint (the maintainer's decision)

OpenAlex merges a preprint with its journal version: measured 2026-10-30, the
work for bioRxiv's `10.1101/2020.03.22.002386` carries that DOI **and** the
Nature paper's PMID (32511329) and PMC ID. The maintainer's choice: **the
preprint's own record, with no identifier of the published version mixed in,
and the published version named separately** so the app can offer it.

- **Detected** where the lookup's identity is a DOI or a PPR and the PubMed
  record reached through OpenAlex's PMID carries a different DOI (both
  canonical). Then that PMID and OpenAlex's PMC ID are the published
  version's.
- **The record** is the preprint's: Europe PMC's for a PPR, else OpenAlex's
  without its PMID and PMC ID, and with only the locations whose OpenAlex
  `version` is `submittedVersion` (the others are the published version's).
- **The published version** is returned beside the record — its PMID, DOI
  and PMC ID — and not in it. The app may look it up by its PMID.

Whether OpenAlex's PMC ID always belongs to the published version, and not to
a preprint PMC holds itself, is not measured; it goes with the PMID.

### What the call returns

A result type (Python dataclass with `to_dict` / `from_dict`; Rust struct):

- `record`: the `FetchedRecord`, or none.
- `published_version`: the identifiers of a later version, or none.
- `pubmed`, `openalex`, `europepmc`: one outcome each, with a short message
  naming what happened (the status, or the parse failure):
  - `found`;
  - `not_found` — OpenAlex's 404, PubMed's empty `<PubmedArticleSet>`, Europe
    PMC's `hitCount: 0`;
  - `request_failed` — no connection, a timeout, any other non-200;
  - `unreadable` — a 200 whose body bmlib cannot read: not JSON / not XML, a
    root that is not `PubmedArticleSet`, an envelope of the wrong shape;
  - `other_record` — served, but not the paper asked about: efetch with no
    `<PubmedArticle>` carrying the PMID sent, an OpenAlex work not carrying
    the identifier it was looked up by (its PMID is then not followed);
  - `book_record` — the matching record is a `<PubmedBookArticle>` (a
    Bookshelf chapter, transparency's #218 population); the parser reads no
    article from it, so another source's record is used;
  - `not_asked` — PubMed with no PMID, Europe PMC with no PPR.

### On the way: OpenAlex's PMC ID

Both languages' OpenAlex normaliser read `ids.pmid` and drop `ids.pmcid`,
served as `https://www.ncbi.nlm.nih.gov/pmc/articles/7745181` (measured: no
`PMC` prefix). It is read into `pmc_id` as `PMC7745181`. This also moves what
the OpenAlex **sync** fetcher stores (`publications.pmcid` filled where it was
empty): a CHANGELOG line.

### Shared contract

`tests/fixtures/record_lookup.json`: each case gives the caller's identifiers,
the scripted response for each URL requested (status and body, or a transport
failure) and the expected result, including **which URLs were requested, in
order**. Python and Rust both replay it. Cases cover at least: found
everywhere; PubMed only; OpenAlex only, with and without a PMID; a PPR found
and not found; neither; each outcome at each source; an efetch serving another
PMID; a book record; a merged preprint; an invalid identifier of each kind; a
caller identifier disagreeing with a source's.

## My calls (the builder may revisit)

- *The identity* is the first present of PMID, PMC ID, PPR, DOI; a caller's
  other identifiers fill only empty fields, and a disagreement with the
  record's is reported in the outcome message rather than refused — except the
  preprint case above, which is a decision.
- `other_record` and `book_record` are outcomes of their own, as in
  transparency's `PubMedStatus`, since they call for different handling from
  an unreadable body.
- Identifiers compare canonical: DOI lower-cased, PMC ID with its prefix.
- A field of the wrong type in an OpenAlex or Europe PMC body costs that field
  and not the record, in both languages alike (transparency's #199 rule); a
  body that is not an object, or carries no `id`, is `unreadable`. If the
  Python OpenAlex normaliser changes for it, the sync fetcher changes with it.
- Part 1's held link goes ahead of Tier 3's.

## Left to the builder

- Module and function names (`bmlib.publications.lookup` / `lookup_record`
  suggested), and the outcome type's spelling.
- Signatures following the existing fetchers: an injected httpx-style client
  in Python, `&dyn HttpClient` in Rust; `email` required (OpenAlex's polite
  pool), the API keys optional.
- How the Europe PMC abstract's markup becomes the record's Markdown (the
  PubMed parser's rules are the model).
- Timeouts (the injected client's), log levels (`docs/SESSION-RULES.md`).

## For StudyAnalyzer

Its `Identifiers` puts a PPR accession in `pmid`; the lookup takes it as its
own argument, so the app passes it separately. Show `published_version` as
"a published version exists", and treat each `request_failed` as worth a retry.
