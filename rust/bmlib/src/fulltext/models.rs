// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Data models for full-text retrieval, JATS XML parsing, and PDF section
//! segmentation.
//!
//! The full-text and JATS types mirror the Swift BioMedLit library's
//! `JATSModels` and `FullTextResult` types. The PDF section-segmentation types —
//! [`SectionType`], [`TextBlock`], [`Section`], [`SegmentedDocument`] — are new
//! to this port and mirror nothing in Swift.
//!
//! # Defaults and required fields
//!
//! Every field is declared in the Python dataclass's order and carries the
//! Python default, which is not always `Default::default()`:
//! [`Section::confidence`] is `1.0` and not `0.0`, [`SegmentedDocument::metadata`]
//! is `{}` and not JSON `null`, and [`ContentKind`]'s `none` member is the
//! default member. A field the Python declares **without** a default is required
//! here too, which is what [`JATSArticle::new`] encodes: a [`JATSArticle`] cannot
//! be built without naming all fifteen of its structural fields, and it
//! deliberately has no `Default` impl, so a hand-built article has to state what
//! it holds rather than inheriting an empty one that reads as a real article.
//!
//! Every defaulted field carries `#[serde(default)]`, so JSON that predates a
//! field — or that omitted it because it was empty — deserialises to the
//! documented default rather than failing the whole document.

use serde::{Deserialize, Serialize};

/// Parsed author information from a JATS article.
///
/// JATS names a contributor with `(name | string-name | collab | …)`, and only
/// the first of those divides into parts. The other two give **one undivided
/// string**, so each has a field of its own rather than being folded into
/// [`JATSAuthorInfo::surname`] — keeping them out is what lets a consumer tell
/// them apart, `surname` being what downstream code sorts and de-duplicates on,
/// where an organisation silently sitting in it is indistinguishable from a
/// person. Emptiness is the predicate (`bool(collab)` asks *"is this an
/// organisation?"*), so no flag can disagree with the string it describes.
///
/// **Both undivided forms are held verbatim and are never split.** Deriving a
/// surname from *"Ahmed Al-Rashid"* means deciding about particles, multi-word
/// surnames and name order — assumed rather than measured, and wrong in a way
/// the caller cannot detect. A consumer that needs *"Smith J"* has the string and
/// can make that decision itself, knowing that it is making one.
///
/// **A contributor may carry an empty [`surname`](JATSAuthorInfo::surname).**
/// Before issues #120 and #140 a collaboration produced no entry at all, so code
/// reading `surname` unconditionally never saw one; sorting by it now front-loads
/// consortia and indexing its first character is now empty.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct JATSAuthorInfo {
    /// The structured surname of a `<name>` deposit.
    ///
    /// Empty where the deposit named the contributor only as a
    /// [`collab`](JATSAuthorInfo::collab) or a
    /// [`string_name`](JATSAuthorInfo::string_name), so downstream code that
    /// sorts or indexes this field must handle the empty case.
    pub surname: String,
    /// The given names of a `<name>` deposit; empty where it gave none.
    pub given_names: String,
    /// The contributor's affiliations.
    ///
    /// Reserved: nothing populates this today — the JATS parser has no `<aff>`
    /// handler, so it is always empty — but a parser that can fill it should not
    /// need a schema change. **Do not read it as "this contributor declared no
    /// affiliation".**
    pub affiliations: Vec<String>,
    /// A collaboration's name, where this contributor is one (issue #120).
    ///
    /// A consortium or group (*"the INHERIT Trial Group"*) — not a person at
    /// all. In the only draw that has measured it — 1,025 open-access articles,
    /// from the PR #118 review — a collaboration was always credited *beside* a
    /// structured name, so no article lost all its contributors to this
    /// spelling.
    pub collab: String,
    /// An undivided personal name, exactly as deposited (issue #140).
    ///
    /// *"Jane Q Smith"*, where the depositor did not split the name. A
    /// `<string-name>` **may** carry `<surname>` and `<given-names>` children,
    /// and where it does those fill the structured fields instead; this one holds
    /// the undivided case.
    pub string_name: String,
}

/// Parsed abstract section (e.g. Background, Methods).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JATSAbstractSection {
    /// The section heading the abstract deposited — *Background*, *Methods*.
    pub title: String,
    /// The section's prose.
    pub content: String,
}

/// A parsed body section with nested subsections.
///
/// The section list is not only the `<body>`'s: back matter follows the body and
/// front matter precedes it, both in document order, and
/// [`JATSArticle::has_body`] is the field that answers whether there is a body at
/// all.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JATSBodySection {
    /// The section's heading, as the document deposited it.
    ///
    /// Never a heading this library derived: a `<sec>`'s own `<title>`, or — for
    /// unsectioned prose in `<body>`, `<back>` and `<front>` (issues #224, #230)
    /// — the heading the element holding that prose deposited, an `<ack>`'s
    /// *Acknowledgements*, a `<glossary>`'s *Abbreviations* or an unsectioned
    /// body's `<def-list>` heading (issue #231).
    ///
    /// Prose the document heads with nothing keeps the empty string, because
    /// nothing is invented for it (issues #116, #162), so a caller rendering
    /// these must handle an untitled section rather than substituting a name of
    /// its own — and two untitled sections may be adjacent, loose `<body>` prose
    /// and loose `<back>` prose being two sections with nothing to head either.
    pub title: String,
    /// The section's paragraphs, in document order.
    #[serde(default)]
    pub paragraphs: Vec<String>,
    /// Nested `<sec>`s, in document order.
    #[serde(default)]
    pub subsections: Vec<JATSBodySection>,
}

/// One funder of an award, as an `<award-group>`'s `<funding-source>` names it
/// (issue #284).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct JATSFundingSource {
    /// The `<funding-source>`'s own text.
    ///
    /// The `<institution>`'s where it wraps one, the element's own where it does
    /// not: 732 of the served sources and 46,791 of the archive's name the funder
    /// bare.
    pub name: String,
    /// The funder's registry id, almost always a Funder Registry DOI, which is
    /// what an industry-funding check reads.
    ///
    /// Taken from the `<institution-wrap><institution-id>` beside the name, or
    /// from the `<named-content>` spelling of the same pair. It is a scalar
    /// because **no** `<funding-source>` on either artifact deposits two: the
    /// 8,118 served articles of `PMC10030002_PMC10040000.xml.gz` hold 7,187
    /// sources of which 4,181 carry exactly one id, and the 97,909 archive
    /// articles of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` 118,023 of
    /// which 47,869 do; neither holds one carrying two.
    ///
    /// **It is the deposit verbatim, and a Funder Registry id arrives in two
    /// spellings** — so a consumer testing `identifier.starts_with("10.13039/")`
    /// reads about half of them (PR #289's review). Over all `<institution-id>`
    /// inside a `<funding-source>`: served 2,697 bare `10.13039/…` against 2,369
    /// wrapped as `http(s)://(dx.)doi.org/…`, and archive 27,149 against 23,632,
    /// with 779 archive ids a bare registry number and 60 a ROR URL. The Tag
    /// Library's own canonical sample deposits the wrapped form. Nothing
    /// normalises it here, because this module records what the document says; a
    /// reader wanting one value space should fold the two. 63 served and 578
    /// archive deposits are the literal `NA`, which is a funder's id in no
    /// registry and is stored as deposited for the same reason.
    ///
    /// The `institution-id-type` attribute is not consulted: its vocabulary is
    /// open and its case varies — over the archive's ids, `doi` 20,381, `FundRef`
    /// 17,327, `funder-id` 5,276, `DOI` 3,334, `open-funder-registry` 490 and
    /// 1,061 carrying no type at all — so a reader gated on one spelling would
    /// store no id for the majority of deposits, and the value distinguishes the
    /// two namespaces by itself. The `content-type` on a `<named-content>` **is**
    /// read, and the two are not in tension: there the attribute would gate a
    /// value whose element already says it is an id, where a `<named-content>`
    /// pair gives the name and the id the same element name and nothing else
    /// tells them apart.
    pub identifier: String,
}

/// One `<award-group>`: who funded the work, and under which award (issue #284).
///
/// Both fields are lists because the content model makes them repeatable and
/// both repeats are deposited: 737 of the 7,171 served groups and 13,072 of the
/// 117,114 archive ones carry several `<award-id>`, and 15 and 803 several
/// `<funding-source>`. Holding one funder per award would drop the latter, and
/// splitting a multi-funder group into one award per funder would assert a
/// funder-to-award pairing the document does not state.
///
/// Either may be empty, as deposited. A group naming a funder and no award is
/// 2,211 of the 7,171 served groups and 30,619 of the 117,114 archive ones; one
/// naming an award and no funder is 0 served and 84 archive.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct JATSFundingAward {
    /// The `<funding-source>`s this group names, in document order — one or
    /// more, where the deposit gives several.
    #[serde(default)]
    pub sources: Vec<JATSFundingSource>,
    /// The `<award-id>`s this group names, in document order.
    #[serde(default)]
    pub award_ids: Vec<String>,
}

/// Parsed figure metadata.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JATSFigureInfo {
    /// The figure's id, as deposited.
    pub id: String,
    /// The figure's label — *Figure 1* — as deposited.
    pub label: String,
    /// The figure's caption.
    pub caption: String,
    /// The figure's `<graphic>` href, or `None` where it deposited none.
    ///
    /// `Option` rather than an empty string because it is a value the document
    /// either deposited or did not, and `""` is not a valid href — and because a
    /// caller writing `if x.graphic_url` over both exhibits should not have to
    /// know which class it is holding. See [`JATSTableInfo::graphic_url`], which
    /// carries the same argument.
    #[serde(default)]
    pub graphic_url: Option<String>,
    /// The notes deposited inside the `<fig>`, in document order.
    ///
    /// JATS admits `<fn>` there directly, with no wrapper, and each note has its
    /// own marker folded into it (issue #124); the figure's own `<attrib>` or its
    /// image's — *"Source: Authors' elaboration."*, an abbreviation list — is
    /// filed here too (issues #241, #248).
    ///
    /// **The two halves are measured far apart**: a marked note is deposited
    /// almost never on a figure in the rendition this parser is fed (2 across
    /// 8,118 served articles, against 16,933 on the table side), while an
    /// attribution is the figure side's larger population by far (125 served, 677
    /// across 97,909 archive articles). An attribution used to weld into the
    /// sentence around a figure deposited in a `<p>`, or reach nothing where the
    /// figure stood in a section. The field exists on both exhibits because one
    /// shared holder in the parser is what stops the two drifting apart. See
    /// [`JATSTableInfo::footnotes`], which carries the fuller argument.
    #[serde(default)]
    pub footnotes: Vec<String>,
}

/// Parsed table metadata with pre-rendered HTML content.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JATSTableInfo {
    /// The table's id, as deposited.
    pub id: String,
    /// The table's label — *Table 1* — as deposited.
    pub label: String,
    /// The table's caption.
    pub caption: String,
    /// The `<table>` rendered to HTML; empty where the table-wrap deposited no
    /// markup.
    ///
    /// A `String` while [`graphic_url`](JATSTableInfo::graphic_url) beside it is
    /// an `Option`, which is deliberate on both counts: this is rendered output,
    /// where empty and absent are the same state and `""` is the natural
    /// bottom.
    #[serde(default)]
    pub html_content: String,
    /// The table's own `<graphic>` deposit, filled the way a figure's is and by
    /// the same ranking.
    ///
    /// A `<table-wrap>` whose only content is an image — a scanned or
    /// typographically complex table — otherwise carries an id, a label and a
    /// caption over nothing, which is indistinguishable from an empty one (issue
    /// #127). A `<table-wrap>` may carry both a `<table>` and a `<graphic>`, so
    /// both fields may be set; which one to show is the renderer's choice. A
    /// caller that wants the facsimile — because the markup lost a merged cell,
    /// or because it is showing the page as published — reads this field
    /// directly, and it is the only way to get at it: `FullTextService` discards
    /// the `JATSArticle` and caches the rendered HTML alone, so for a service
    /// consumer that renderer choice is permanent. Both populations are measured
    /// over the two committed draws (1,997 articles, 2,448 `<table-wrap>`, every
    /// one of them in the recent window): the image is the *only* rendition for
    /// **8**, and sits beside a `<table>` for **84**.
    #[serde(default)]
    pub graphic_url: Option<String>,
    /// The table's own notes, in document order.
    ///
    /// A `<table-wrap-foot>`'s `<fn>` prose, an `<fn-group>` (which JATS admits
    /// and neither measured artifact deposits inside an exhibit), the general
    /// note deposited as a loose `<p>` after the last marked one, and the table's
    /// own `<attrib>` or one inside its `<table-wrap-foot>` (issues #241, #248:
    /// 21 served and 192 archive attributions). Each marked note's marker is
    /// folded into its own string — `"a — Adjusted for age."` (issue #124), the
    /// separator being a measured choice: **2 of 16,947** footnote paragraphs in
    /// the served artifact contain `" — "` against 47 containing a spaced hyphen
    /// and 4,133 a colon, so an em dash collides an order of magnitude less often
    /// than either alternative. An attribution carries no marker, and an image
    /// credit inside a marked note is filed ahead of that note's prose unless the
    /// credit is all the note deposits, when it takes the marker.
    ///
    /// **Splitting on the separator does not recover the marker**: `" — "` is
    /// also the definition separator, and a `<def-list>`'s `<term>` is folded
    /// into its definition with the same string *before* this fold runs, so an
    /// abbreviations list deposited in a `<table-wrap-foot>` emits `"BMI — body
    /// mass index"` carrying no marker at all — measured on what the parser
    /// emits, **68 of the 16,935 notes, in 10 of the 8,118 served articles**. A
    /// consumer splitting unguarded reads `BMI` as a footnote marker; split only
    /// where the prefix is marker-shaped, or read the deposit.
    ///
    /// The population is **16,935 paragraphs in 3,707 of 8,118 served articles
    /// (45.7%)**, 2.37 MB of prose, over Europe PMC's
    /// `PMC10030002_PMC10040000.xml.gz`. Table footnotes carry the abbreviation
    /// expansions without which the cells are unreadable, and the per-table
    /// funding and disclosure notes `bmlib.transparency` scans for.
    #[serde(default)]
    pub footnotes: Vec<String>,
}

/// Parsed reference/citation information.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JATSReferenceInfo {
    /// The reference's id, as deposited.
    pub id: String,
    /// The reference's label — *1*, *[2]* — as deposited.
    pub label: String,
    /// Every descendant's text of a `<mixed-citation>`, in document order — the
    /// marked-up parts with whatever character data the depositor put between
    /// them (issue #146).
    ///
    /// Deliberately *not* "the reference as the publisher typeset it": a
    /// separator is often in the publisher's rendering stylesheet rather than the
    /// deposit, so adjacent elements with nothing between them concatenate —
    /// `<surname>`/`<given-names>` and repeated `<pub-id>` most often, measured
    /// at 13.2% of 3,798 citations carrying at least one such pair. That is
    /// faithful to what the document contains and a large improvement on the
    /// punctuation alone, but it is not a typeset string.
    ///
    /// An `<element-citation>` deposit leaves this **empty**, and that is not a
    /// gap: its content model is element-only, so the depositor authored no
    /// string and the whitespace between the children is insignificant. Where a
    /// `<ref>` carries both spellings, the `<mixed-citation>` wins regardless of
    /// deposit order.
    pub citation: String,
    /// The reference's authors, as deposited, in document order.
    #[serde(default)]
    pub authors: Vec<String>,
    /// The cited work's title.
    #[serde(default)]
    pub article_title: String,
    /// The cited work's source — the journal or book it appeared in.
    #[serde(default)]
    pub source: String,
    /// The year of publication, as a string.
    ///
    /// A string rather than a number because a deposit may carry a range, a
    /// season or nothing at all, and a caller rendering the reference wants it
    /// verbatim.
    #[serde(default)]
    pub year: String,
    /// The volume of the cited work.
    #[serde(default)]
    pub volume: String,
    /// The issue of the cited work.
    ///
    /// Printed only after a volume by the renderers, which is why it is not on
    /// its own sufficient to make a structured rendering.
    #[serde(default)]
    pub issue: String,
    /// The first page of the cited work's page range.
    #[serde(default)]
    pub first_page: String,
    /// The last page of the cited work's page range.
    #[serde(default)]
    pub last_page: String,
    /// The cited work's DOI.
    #[serde(default)]
    pub doi: String,
    /// The cited work's PubMed identifier.
    #[serde(default)]
    pub pmid: String,
    /// The cited work's `<elocation-id>`, an electronic locator such as
    /// `e0230000` (issue #265).
    ///
    /// Kept apart from [`first_page`](JATSReferenceInfo::first_page), which a
    /// caller reads as a page. The renderers print it only where there is no
    /// `first_page`, which keeps every reference depositing both rendered as it
    /// was: there neither element is reliably the locator — the `<elocation-id>`
    /// is, among other shapes, the `<fpage>`'s own value, a DOI or PII, an issue
    /// number or supplement suffix beside a range, or the true article number
    /// beside an issue deposited as `<fpage>`.
    #[serde(default)]
    pub elocation_id: String,
}

/// Complete parsed JATS article data.
///
/// The fifteen structural fields are required: a `JATSArticle` is built through
/// [`JATSArticle::new`], or by naming every field, and there is deliberately no
/// `Default` impl. The five fields after them default, because each describes
/// something a document may simply not have deposited — but a hand-built article
/// then reports what it was given rather than what the document said.
///
/// **Field order matches the Python dataclass exactly.** Rust has no
/// required-before-defaulted restriction, so nothing had to move.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JATSArticle {
    /// The article's title.
    pub title: String,
    /// The article's contributors, in document order.
    pub authors: Vec<JATSAuthorInfo>,
    /// The journal the article appeared in.
    pub journal: String,
    /// The journal's volume.
    pub volume: String,
    /// The journal's issue.
    pub issue: String,
    /// The article's page range, as deposited.
    pub pages: String,
    /// The year of publication, as a string.
    pub year: String,
    /// The article's DOI.
    pub doi: String,
    /// The article's PubMed Central identifier.
    pub pmc_id: String,
    /// The article's PubMed identifier.
    pub pmid: String,
    /// The abstract's sections, in document order.
    pub abstract_sections: Vec<JATSAbstractSection>,
    /// The body's sections, in document order — front and back matter included.
    pub body_sections: Vec<JATSBodySection>,
    /// The article's figures, in document order.
    pub figures: Vec<JATSFigureInfo>,
    /// The article's tables, in document order.
    pub tables: Vec<JATSTableInfo>,
    /// The article's references, in document order.
    pub references: Vec<JATSReferenceInfo>,
    /// Did `<body>` hold at least one non-empty `<p>` inside a `<sec>` — that
    /// is, body prose that survived parsing?
    ///
    /// Some publishers (medRxiv among them) serve a JATS document made of
    /// `<front>` and `<back>` only; it parses cleanly but holds no article prose
    /// — its [`body_sections`](JATSArticle::body_sections) may still carry front
    /// and back matter (author notes, acknowledgements; issues #224, #230) — so
    /// callers must not mistake it for full text. It tracks what survived parsing
    /// rather than what the XML contained, and the default is `false`, so a
    /// hand-built article reports "no body" unless it says otherwise. Unsectioned
    /// prose does count: a `<p>` sitting directly in `<body>` with no enclosing
    /// `<sec>` is collected into an untitled section.
    #[serde(default)]
    pub has_body: bool,
    /// How many `<sub-article>`/`<response>` elements were skipped, counting a
    /// nested one separately.
    ///
    /// Nothing inside them is this article's, so they contribute nothing to the
    /// fields above — but they can hold most of a document's prose (a peer-review
    /// history, or the alternative-language full text SciELO deposits as
    /// `article-type="translation"`), and dropping that changes neither
    /// [`has_body`](JATSArticle::has_body) nor `FullTextResult.content_kind`,
    /// which between them report only *total* loss. This is the one field that
    /// says a nested article was there at all.
    #[serde(default)]
    pub suppressed_nested_articles: usize,
    /// The article's own `<elocation-id>`: the electronic locator JATS deposits
    /// *in place of* a page range.
    ///
    /// In valid JATS, and in every article of the four artifacts issue #265
    /// measured, [`pages`](JATSArticle::pages) is blank where this is set. The
    /// parser does not enforce that: an invalid deposit carrying both keeps both.
    /// An article paginated that way used to store no locator at all: 4,869 of the
    /// 8,118 served articles of Europe PMC's `PMC10030002_PMC10040000.xml.gz`,
    /// and 81,934 of the 97,909 of PMC's
    /// `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`. A field of its own
    /// rather than folded into `pages`, which a downstream reads and formats as a
    /// page range, and `e0123456` is not one.
    #[serde(default)]
    pub elocation_id: String,
    /// The article's own `<funding-statement>`s, in document order and
    /// whitespace-normalised (issue #257).
    ///
    /// A funding disclosure reached no field before: 1,337 of the 8,118 served
    /// articles of `PMC10030002_PMC10040000.xml.gz` and 41,260 of the 97,909 of
    /// `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` carried one that
    /// reached nothing. The structured `<award-group>` beside it is
    /// [`funding_awards`](JATSArticle::funding_awards): this field is the sentence
    /// the article prints, that one the values to match on. Where the publisher
    /// repeats the sentence in the article's prose as well — mostly a back-matter
    /// *Funding* note or section — it is here *and* in `body_sections`, as
    /// deposited. A statement that is *not* the article's own reaches no field and
    /// is counted, with one WARNING per article (issue #257, PR #285's review).
    #[serde(default)]
    pub funding_statements: Vec<String>,
    /// The article's own `<award-group>`s, in document order (issue #284): who
    /// funded the work, their Funder Registry id, and the award numbers.
    ///
    /// It is the *larger* half of the funding disclosure and reached nothing at
    /// all before — 3,066 of the 8,118 served articles of
    /// `PMC10030002_PMC10040000.xml.gz` carry an award against 1,367 carrying the
    /// statement above, and 49,652 of the 97,909 archive articles of
    /// `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` against 42,295 — so 2,292
    /// served and 27,602 archive articles, 28.2% of each, disclose their funding
    /// structurally and in no statement.
    ///
    /// Beside the statement and never instead of it: where a publisher deposits
    /// both they say the same thing twice in different shapes, and which one a
    /// downstream wants depends on what it is doing. An `<award-group>`'s
    /// `<principal-award-recipient>` is not modelled (#288).
    #[serde(default)]
    pub funding_awards: Vec<JATSFundingAward>,
}

impl JATSArticle {
    /// Build an article from its fifteen structural fields.
    ///
    /// The five remaining fields start at their documented defaults —
    /// `has_body: false`, `suppressed_nested_articles: 0`, `elocation_id: ""`
    /// and empty funding lists — and are assigned afterwards by a caller that
    /// knows better.
    ///
    /// The argument list is long because it is the contract: this is the
    /// Python dataclass's positional order, and flattening it into options or a
    /// builder would let a caller omit a field the Python makes required.
    #[must_use]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        title: String,
        authors: Vec<JATSAuthorInfo>,
        journal: String,
        volume: String,
        issue: String,
        pages: String,
        year: String,
        doi: String,
        pmc_id: String,
        pmid: String,
        abstract_sections: Vec<JATSAbstractSection>,
        body_sections: Vec<JATSBodySection>,
        figures: Vec<JATSFigureInfo>,
        tables: Vec<JATSTableInfo>,
        references: Vec<JATSReferenceInfo>,
    ) -> Self {
        Self {
            title,
            authors,
            journal,
            volume,
            issue,
            pages,
            year,
            doi,
            pmc_id,
            pmid,
            abstract_sections,
            body_sections,
            figures,
            tables,
            references,
            has_body: false,
            suppressed_nested_articles: 0,
            elocation_id: String::new(),
            funding_statements: Vec::new(),
            funding_awards: Vec::new(),
        }
    }
}

/// A known full-text source URL discovered by a fetcher.
///
/// Produced by publication fetchers, consumed by `FullTextService`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FullTextSourceEntry {
    /// The address to fetch.
    pub url: String,
    /// The payload's format: `"pdf"`, `"xml"` or `"html"`.
    pub format: String,
    /// Where the URL came from, e.g. `"biorxiv"`, `"medrxiv"`, `"pmc"`,
    /// `"publisher"`.
    pub source: String,
    /// Whether the source declares the text open access.
    #[serde(default = "yes")]
    pub open_access: bool,
    /// The version the source describes, e.g. `"preprint"`, `"accepted"`,
    /// `"published"`; `None` where it says nothing.
    #[serde(default)]
    pub version: Option<String>,
}

/// Result of a full-text retrieval attempt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FullTextResult {
    /// Which tier or fetcher produced this: `"europepmc"`, `"europepmc_pdf"`,
    /// `"unpaywall"`, `"doi"`, `"pubmed"`, `"cached"`, or a fetcher source name
    /// (e.g. `"biorxiv"`) for known full-text URLs.
    pub source: String,
    /// The retrieved payload — HTML or JATS XML — or `None` where nothing was
    /// retrieved.
    ///
    /// What it actually holds is [`content_kind`](FullTextResult::content_kind),
    /// not this being set.
    #[serde(default)]
    pub html: Option<String>,
    /// A PDF address for the same work, where one is known.
    ///
    /// Worth offering even when [`html`](FullTextResult::html) holds extracted
    /// text, which is prose only and possibly not every page.
    #[serde(default)]
    pub pdf_url: Option<String>,
    /// A human-readable landing page for the work, where one is known.
    #[serde(default)]
    pub web_url: Option<String>,
    /// A local path the payload was cached at, where one is known.
    #[serde(default)]
    pub file_path: Option<String>,
    /// What [`html`](FullTextResult::html) actually holds.
    ///
    /// The service can tell an article body from an abstract and from
    /// PDF-extracted prose, so it says which rather than leaving every case
    /// looking alike. Callers that must not analyse an abstract as if it were an
    /// article should branch on this rather than on `html` being set.
    #[serde(default)]
    pub content_kind: ContentKind,
}

/// What a [`FullTextResult::html`] payload actually is.
///
/// The Python declares this as a `Literal`, not an `Enum`; it is a real enum
/// here so an unrecognised wire value is refused by name rather than travelling
/// on as a string nothing validates.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ContentKind {
    /// `html` is `None` — nothing was retrieved.
    #[default]
    None,
    /// A body-less JATS rendering, returned only as a last resort; there is no
    /// article text in it.
    Abstract,
    /// Text recovered from a PDF. Prose only: no figures, tables or layout, and
    /// possibly not every page, so `pdf_url`/`file_path` stay worth offering.
    Extracted,
    /// A JATS document that had a `<body>`.
    Fulltext,
}

impl ContentKind {
    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            ContentKind::None => "none",
            ContentKind::Abstract => "abstract",
            ContentKind::Extracted => "extracted",
            ContentKind::Fulltext => "fulltext",
        }
    }

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// An unrecognised kind, named, so a malformed payload says which value it
    /// carried rather than defaulting to one that would let an abstract be
    /// analysed as an article.
    pub fn parse(raw: &str) -> Result<ContentKind, String> {
        match raw {
            "none" => Ok(ContentKind::None),
            "abstract" => Ok(ContentKind::Abstract),
            "extracted" => Ok(ContentKind::Extracted),
            "fulltext" => Ok(ContentKind::Fulltext),
            other => Err(format!("unknown content kind {other:?}")),
        }
    }
}

/// Standard sections of a biomedical publication.
///
/// [`SectionType::Title`] is reserved: the segmenter carries the document title
/// on [`SegmentedDocument::title`] and never emits a title section, but the
/// member stays as the name a caller building one by hand would reach for.
/// [`SectionType::FrontMatter`] and [`SectionType::Unknown`] are containers, not
/// classifications — what precedes the first detected heading, and text no
/// heading claimed. Every other member has at least one heading pattern in the
/// segmenter.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SectionType {
    /// Reserved: the document title, which the segmenter never emits as a
    /// section.
    Title,
    /// The abstract.
    Abstract,
    /// The introduction.
    Introduction,
    /// The background section.
    Background,
    /// The methods section.
    Methods,
    /// The results section.
    Results,
    /// The discussion.
    Discussion,
    /// The conclusion.
    Conclusion,
    /// The acknowledgements.
    Acknowledgments,
    /// The reference list.
    References,
    /// Supplementary material.
    Supplementary,
    /// An appendix.
    Appendix,
    /// A funding statement.
    Funding,
    /// A conflicts-of-interest statement.
    Conflicts,
    /// A data-availability statement.
    DataAvailability,
    /// An author-contributions statement.
    AuthorContributions,
    /// A container: what precedes the first detected heading.
    FrontMatter,
    /// A container: text no heading claimed.
    Unknown,
}

impl SectionType {
    /// Every member, in declaration order.
    ///
    /// Exists so a test can assert the port carries **exactly** Python's members
    /// rather than only that each of Python's is present — an extra member is
    /// invisible to a one-way check, and a member Python does not have is a
    /// classification the segmenter could emit and nothing downstream expects.
    pub const ALL: &'static [SectionType] = &[
        SectionType::Title,
        SectionType::Abstract,
        SectionType::Introduction,
        SectionType::Background,
        SectionType::Methods,
        SectionType::Results,
        SectionType::Discussion,
        SectionType::Conclusion,
        SectionType::Acknowledgments,
        SectionType::References,
        SectionType::Supplementary,
        SectionType::Appendix,
        SectionType::Funding,
        SectionType::Conflicts,
        SectionType::DataAvailability,
        SectionType::AuthorContributions,
        SectionType::FrontMatter,
        SectionType::Unknown,
    ];

    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            SectionType::Title => "title",
            SectionType::Abstract => "abstract",
            SectionType::Introduction => "introduction",
            SectionType::Background => "background",
            SectionType::Methods => "methods",
            SectionType::Results => "results",
            SectionType::Discussion => "discussion",
            SectionType::Conclusion => "conclusion",
            SectionType::Acknowledgments => "acknowledgments",
            SectionType::References => "references",
            SectionType::Supplementary => "supplementary",
            SectionType::Appendix => "appendix",
            SectionType::Funding => "funding",
            SectionType::Conflicts => "conflicts",
            SectionType::DataAvailability => "data_availability",
            SectionType::AuthorContributions => "author_contributions",
            SectionType::FrontMatter => "front_matter",
            SectionType::Unknown => "unknown",
        }
    }

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// An unrecognised section type, named, so a malformed payload says which
    /// value it carried rather than defaulting to
    /// [`Unknown`](SectionType::Unknown), which is a classification the
    /// segmenter may already have made.
    pub fn parse(raw: &str) -> Result<SectionType, String> {
        match raw {
            "title" => Ok(SectionType::Title),
            "abstract" => Ok(SectionType::Abstract),
            "introduction" => Ok(SectionType::Introduction),
            "background" => Ok(SectionType::Background),
            "methods" => Ok(SectionType::Methods),
            "results" => Ok(SectionType::Results),
            "discussion" => Ok(SectionType::Discussion),
            "conclusion" => Ok(SectionType::Conclusion),
            "acknowledgments" => Ok(SectionType::Acknowledgments),
            "references" => Ok(SectionType::References),
            "supplementary" => Ok(SectionType::Supplementary),
            "appendix" => Ok(SectionType::Appendix),
            "funding" => Ok(SectionType::Funding),
            "conflicts" => Ok(SectionType::Conflicts),
            "data_availability" => Ok(SectionType::DataAvailability),
            "author_contributions" => Ok(SectionType::AuthorContributions),
            "front_matter" => Ok(SectionType::FrontMatter),
            "unknown" => Ok(SectionType::Unknown),
            other => Err(format!("unknown section type {other:?}")),
        }
    }
}

/// One text line of a PDF with its layout and font attributes.
///
/// A line, not a span: PyMuPDF starts a new span at every font change, so a
/// heading numbered in a different weight or a sentence holding an italic gene
/// name would shatter into fragments no anchored heading pattern can match.
/// Font attributes are those of the line's dominant span.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TextBlock {
    /// The line's text.
    pub text: String,
    /// The page the line sits on, 0-indexed.
    pub page_num: i64,
    /// The dominant span's font size.
    pub font_size: f64,
    /// The dominant span's font name.
    pub font_name: String,
    /// Whether the dominant span is bold.
    pub is_bold: bool,
    /// Whether the dominant span is italic.
    pub is_italic: bool,
    /// The x coordinate of the line's origin, in the page's coordinate space.
    pub x: f64,
    /// The y coordinate of the line's origin, in the page's coordinate space.
    pub y: f64,
    /// The line's width, in the page's coordinate space.
    pub width: f64,
    /// The line's height, in the page's coordinate space.
    pub height: f64,
}

/// A typed, titled span of a segmented document.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Section {
    /// The section's classification.
    pub section_type: SectionType,
    /// The heading the section was detected under.
    pub title: String,
    /// The section's text, as extracted.
    pub content: String,
    /// The first page the section covers, 0-indexed.
    ///
    /// For a heading with no body this is the heading's page.
    pub page_start: i64,
    /// The last page the section covers, 0-indexed.
    ///
    /// For a heading with no body this is the heading's page.
    pub page_end: i64,
    /// How sure the segmenter is of the classification.
    ///
    /// `1.0` for an exact heading match, `0.7` for a partial one, and `0.5` for
    /// the two container sections (front matter, the no-headings fallback).
    #[serde(default = "one")]
    pub confidence: f64,
    /// Nested sections.
    ///
    /// Carried for callers but never populated by the segmenter, which emits a
    /// flat list.
    #[serde(default)]
    pub subsections: Vec<Section>,
}

/// A publication segmented into typed sections.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SegmentedDocument {
    /// The path the segmented PDF was read from; empty for a document built in
    /// memory.
    #[serde(default)]
    pub file_path: String,
    /// The document's title, where the caller or the front matter supplied one.
    #[serde(default)]
    pub title: Option<String>,
    /// The document's authors.
    ///
    /// Reserved: nothing populates it today — author extraction from PDF front
    /// matter is its own heuristic problem — but a parser that can fill it should
    /// not need a schema change.
    #[serde(default)]
    pub authors: Vec<String>,
    /// The document's sections, in document order.
    #[serde(default)]
    pub sections: Vec<Section>,
    /// Whatever the caller passed to the segmenter, stored as-is.
    ///
    /// The default is an empty object (`{}`) and not JSON `null`, matching the
    /// Python `field(default_factory=dict)`.
    #[serde(default = "empty_object")]
    pub metadata: serde_json::Value,
}

impl Default for SegmentedDocument {
    fn default() -> Self {
        Self {
            file_path: String::new(),
            title: None,
            authors: Vec::new(),
            sections: Vec::new(),
            metadata: empty_object(),
        }
    }
}

/// The default for [`FullTextSourceEntry::open_access`].
fn yes() -> bool {
    true
}

/// The default for [`Section::confidence`].
fn one() -> f64 {
    1.0
}

/// The default for [`SegmentedDocument::metadata`].
fn empty_object() -> serde_json::Value {
    serde_json::Value::Object(serde_json::Map::new())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Compile-time probe: does `T` implement [`Default`]?
    ///
    /// The inherent associated const is selected only where `T: Default` holds;
    /// otherwise resolution falls back to the blanket trait impl, which answers
    /// `false`. That is what lets a test assert the *absence* of an impl, which
    /// no runtime value can show.
    struct ProbeDefault<T>(std::marker::PhantomData<T>);

    trait ProbeDefaultFallback {
        const IS_DEFAULT: bool = false;
    }

    impl<T> ProbeDefaultFallback for ProbeDefault<T> {}

    impl<T: Default> ProbeDefault<T> {
        const IS_DEFAULT: bool = true;
    }

    const CONTENT_KINDS: [ContentKind; 4] = [
        ContentKind::None,
        ContentKind::Abstract,
        ContentKind::Extracted,
        ContentKind::Fulltext,
    ];

    const SECTION_TYPES: [SectionType; 18] = [
        SectionType::Title,
        SectionType::Abstract,
        SectionType::Introduction,
        SectionType::Background,
        SectionType::Methods,
        SectionType::Results,
        SectionType::Discussion,
        SectionType::Conclusion,
        SectionType::Acknowledgments,
        SectionType::References,
        SectionType::Supplementary,
        SectionType::Appendix,
        SectionType::Funding,
        SectionType::Conflicts,
        SectionType::DataAvailability,
        SectionType::AuthorContributions,
        SectionType::FrontMatter,
        SectionType::Unknown,
    ];

    fn example_article() -> JATSArticle {
        let mut article = JATSArticle::new(
            "A measured title".to_string(),
            vec![JATSAuthorInfo {
                surname: "Smith".to_string(),
                given_names: "Jane Q".to_string(),
                affiliations: vec!["Dept of Things".to_string()],
                collab: "the INHERIT Trial Group".to_string(),
                string_name: "Jane Q Smith".to_string(),
            }],
            "Journal of Measurements".to_string(),
            "12".to_string(),
            "3".to_string(),
            "100-110".to_string(),
            "2025".to_string(),
            "10.1000/example".to_string(),
            "PMC1234567".to_string(),
            "12345678".to_string(),
            vec![JATSAbstractSection {
                title: "Background".to_string(),
                content: "An abstract.".to_string(),
            }],
            vec![JATSBodySection {
                title: "Methods".to_string(),
                paragraphs: vec!["First.".to_string(), "Second.".to_string()],
                subsections: vec![JATSBodySection {
                    title: "Sub".to_string(),
                    paragraphs: Vec::new(),
                    subsections: Vec::new(),
                }],
            }],
            vec![JATSFigureInfo {
                id: "fig1".to_string(),
                label: "Figure 1".to_string(),
                caption: "A figure.".to_string(),
                graphic_url: Some("fig1.jpg".to_string()),
                footnotes: vec!["a — A note.".to_string()],
            }],
            vec![JATSTableInfo {
                id: "tbl1".to_string(),
                label: "Table 1".to_string(),
                caption: "A table.".to_string(),
                html_content: "<table></table>".to_string(),
                graphic_url: None,
                footnotes: vec!["A note.".to_string()],
            }],
            vec![JATSReferenceInfo {
                id: "ref1".to_string(),
                label: "1".to_string(),
                citation: "Smith J. A title. J Meas. 2024.".to_string(),
                authors: vec!["Smith J".to_string()],
                article_title: "A cited title".to_string(),
                source: "J Meas".to_string(),
                year: "2024".to_string(),
                volume: "11".to_string(),
                issue: "2".to_string(),
                first_page: "1".to_string(),
                last_page: "9".to_string(),
                doi: "10.1000/cited".to_string(),
                pmid: "87654321".to_string(),
                elocation_id: "e0123456".to_string(),
            }],
        );
        article.has_body = true;
        article.suppressed_nested_articles = 2;
        article.elocation_id = "e0230000".to_string();
        article.funding_statements = vec!["Funded by someone.".to_string()];
        article.funding_awards = vec![JATSFundingAward {
            sources: vec![JATSFundingSource {
                name: "A Funder".to_string(),
                identifier: "10.13039/100000001".to_string(),
            }],
            award_ids: vec!["AWARD-1".to_string()],
        }];
        article
    }

    #[test]
    fn jats_article_round_trips_through_json() {
        let article = example_article();
        let encoded = serde_json::to_string(&article).expect("serialises");
        let decoded: JATSArticle = serde_json::from_str(&encoded).expect("deserialises");
        assert_eq!(decoded, article);
    }

    #[test]
    fn every_enum_member_round_trips_and_a_bad_value_is_named() {
        for kind in CONTENT_KINDS {
            assert_eq!(ContentKind::parse(kind.as_str()), Ok(kind));
        }
        for section_type in SECTION_TYPES {
            assert_eq!(SectionType::parse(section_type.as_str()), Ok(section_type));
        }

        // The multi-word spellings are the ones an assumption would get wrong.
        assert_eq!(SectionType::DataAvailability.as_str(), "data_availability");
        assert_eq!(
            SectionType::AuthorContributions.as_str(),
            "author_contributions"
        );
        assert_eq!(SectionType::FrontMatter.as_str(), "front_matter");

        let kind_error = ContentKind::parse("banana").expect_err("refuses");
        assert!(kind_error.contains("banana"), "got {kind_error:?}");
        let section_error = SectionType::parse("banana").expect_err("refuses");
        assert!(section_error.contains("banana"), "got {section_error:?}");
    }

    #[test]
    fn defaults_are_the_python_defaults() {
        let author = JATSAuthorInfo::default();
        assert_eq!(author.surname, "");
        assert_eq!(author.given_names, "");
        assert!(author.affiliations.is_empty());
        assert_eq!(author.collab, "");
        assert_eq!(author.string_name, "");

        let source = JATSFundingSource::default();
        assert_eq!(source.name, "");
        assert_eq!(source.identifier, "");
        assert_eq!(source, JATSFundingSource::default());

        let award = JATSFundingAward::default();
        assert!(award.sources.is_empty());
        assert!(award.award_ids.is_empty());

        let document = SegmentedDocument::default();
        assert_eq!(document.file_path, "");
        assert_eq!(document.title, None);
        assert!(document.authors.is_empty());
        assert!(document.sections.is_empty());
        assert_eq!(document.metadata, json!({}));
        assert_ne!(document.metadata, serde_json::Value::Null);

        // A partial payload deserialises to the documented defaults.
        let entry: FullTextSourceEntry =
            serde_json::from_value(json!({"url": "u", "format": "pdf", "source": "pmc"}))
                .expect("deserialises");
        assert!(entry.open_access);
        assert_eq!(entry.version, None);

        let section: Section = serde_json::from_value(json!({
            "section_type": "methods",
            "title": "Methods",
            "content": "c",
            "page_start": 0,
            "page_end": 1
        }))
        .expect("deserialises");
        assert_eq!(section.confidence, 1.0);
        assert!(section.subsections.is_empty());

        let table: JATSTableInfo =
            serde_json::from_value(json!({"id": "t1", "label": "Table 1", "caption": "cap"}))
                .expect("deserialises");
        assert_eq!(table.html_content, "");
        assert_eq!(table.graphic_url, None);
        assert!(table.footnotes.is_empty());

        // JATSArticle deliberately has no Default: its fifteen structural fields
        // must be named. Both assertions are const-evaluated, so the absence of
        // the impl is a compile-time failure and not merely a red test.
        const { assert!(!ProbeDefault::<JATSArticle>::IS_DEFAULT) };
        const { assert!(ProbeDefault::<JATSAuthorInfo>::IS_DEFAULT) };

        let article = JATSArticle::new(
            String::new(),
            Vec::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            Vec::new(),
            Vec::new(),
            Vec::new(),
            Vec::new(),
            Vec::new(),
        );
        assert!(!article.has_body);
        assert_eq!(article.suppressed_nested_articles, 0);
        assert_eq!(article.elocation_id, "");
        assert!(article.funding_statements.is_empty());
        assert!(article.funding_awards.is_empty());
    }

    #[test]
    fn content_kind_defaults_to_the_none_member_by_name() {
        assert_eq!(ContentKind::default(), ContentKind::None);
        assert_eq!(ContentKind::default().as_str(), "none");

        let result: FullTextResult =
            serde_json::from_value(json!({"source": "europepmc"})).expect("deserialises");
        assert_eq!(result.content_kind, ContentKind::None);
        assert_eq!(result.content_kind.as_str(), "none");
        assert_eq!(result.html, None);
        assert_eq!(result.pdf_url, None);
        assert_eq!(result.web_url, None);
        assert_eq!(result.file_path, None);
    }
}
