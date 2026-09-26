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

//! PubMed fetcher — E-utilities, and the `PubmedArticle` XML reader.
//!
//! A port of `bmlib/publications/fetchers/pubmed.py`.
//!
//! # The XML layer is a mapping task, not a transcription
//!
//! Python uses `xml.etree.ElementTree`, i.e. **expat**, and this port uses
//! `roxmltree`. The plan's §3 says the difference is not a blocker but is a
//! *mapping task with named unknowns*, and the one that matters here is which
//! documents each **rejects**: `_parse_article_xml` is reached only for XML that
//! already parsed, so a document `roxmltree` refuses and expat accepts changes
//! which records a day delivers. That is pinned by
//! [`tests::the_xml_layer_reports_what_it_refuses`] rather than assumed.
//!
//! # Markdown is a claim about the field, so it is escaped
//!
//! Titles and abstracts are declared Markdown, and the prose they are built from
//! is escaped on the way in (see [`escape_markdown`]) — otherwise declaring the
//! field Markdown would itself **corrupt values that were fine before**, such as
//! the star alleles in `CYP2C19 (*1, *2, *3)`. The escape set is
//! `[\\`*~^]` and was measured against 3,403 real titles and abstract sections,
//! where it alters 0.35% of them.
//!
//! `~` and `^` are in that set because **this module** made them meaningful —
//! they are the markers for `<sub>` and `<sup>`. A literal tilde is the
//! commonest hazard of the three (8 fields against the asterisk's 3): `"AUC ~
//! 0.80"` and `"(~88%)"` are ordinary scientific prose, and an unescaped pair
//! silently subscripts everything between them.

use chrono::NaiveDate;
use roxmltree::{Document, Node};

use crate::publications::fetchers::reconcile::reconcile_delivery;
use crate::publications::fetchers::registry::HttpClient;
use crate::publications::models::{AuthorAffiliation, FetchedRecord, Grant, PartCheckpoint};

/// The ESearch endpoint.
pub const ESEARCH_URL: &str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi";

/// The EFetch endpoint.
pub const EFETCH_URL: &str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi";

/// How many records one efetch page carries.
pub const EFETCH_PAGE_SIZE: usize = 500;

/// The most records one efetch request can retrieve.
pub const EFETCH_MAX_RETRIEVABLE: i64 = 9999;

/// The PMC article base URL.
pub const PMC_BASE_URL: &str = "https://www.ncbi.nlm.nih.gov/pmc/articles/";

/// The DOI base URL.
pub const DOI_BASE_URL: &str = "https://doi.org/";

/// The partitioning scheme this module writes into `part_scheme`.
pub const PART_SCHEME: &str = "edat-range";

/// The pause between requests, with an API key.
pub const RATE_LIMIT_WITH_KEY: f64 = 0.1;

/// The pause between requests, without one.
pub const RATE_LIMIT_WITHOUT_KEY: f64 = 0.34;

/// Month abbreviation → numeric month.
#[must_use]
pub fn month_map() -> std::collections::BTreeMap<&'static str, &'static str> {
    std::collections::BTreeMap::from([
        ("jan", "01"),
        ("feb", "02"),
        ("mar", "03"),
        ("apr", "04"),
        ("may", "05"),
        ("jun", "06"),
        ("jul", "07"),
        ("aug", "08"),
        ("sep", "09"),
        ("oct", "10"),
        ("nov", "11"),
        ("dec", "12"),
    ])
}

/// Inline markup PubMed carries inside titles and abstracts, and the Markdown
/// each maps to.
///
/// Scientific prose depends on these: without `sub`/`sup` a chemical formula and
/// an exponent both flatten into an ambiguous `"CO2"` / `"m2"`.
///
/// `u`/`underline` are **deliberately absent**, so they fall through to the
/// undecorated path. Markdown has no underline: `__x__` is *strong* emphasis, so
/// mapping `<u>` to it renders underlined text identically to `<b>` — the same
/// collapse this table exists to prevent for `sub`/`sup`, except that it also
/// asserts something false about the source. Underline is presentational, unlike
/// a subscript, so dropping it loses nothing a reader needs; claiming it was
/// bold does.
#[must_use]
pub fn inline_markup() -> std::collections::BTreeMap<&'static str, (&'static str, &'static str)> {
    std::collections::BTreeMap::from([
        ("b", ("**", "**")),
        ("bold", ("**", "**")),
        ("i", ("*", "*")),
        ("italic", ("*", "*")),
        ("sup", ("^", "^")),
        ("sub", ("~", "~")),
    ])
}

/// `NlmCategory` values that mean "this section has no label".
///
/// Rendering them as headings would put the word `UNASSIGNED` in front of the
/// prose.
pub const UNLABELLED_CATEGORIES: [&str; 2] = ["UNASSIGNED", "UNLABELLED"];

/// The Markdown-active characters escaped in a run of PubMed prose.
///
/// **Not** the obvious wider set. Intraword `_` is inert in CommonMark, so gene
/// names like `TP53_R175H` are already safe, and a bare `[...]` is not a link
/// without a following `(...)`; escaping both churned 4.3% of fields in the
/// measurement and fixed nothing further.
pub const MARKDOWN_SPECIALS: [char; 5] = ['\\', '`', '*', '~', '^'];

/// Escape the Markdown-active characters in a run of PubMed prose.
///
/// Applied to text taken from the document, **never** to the markers this module
/// emits. Whitespace is untouched, so a caller's lead/trail bookkeeping is
/// unaffected.
#[must_use]
pub fn escape_markdown(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        if MARKDOWN_SPECIALS.contains(&ch) {
            out.push('\\');
        }
        out.push(ch);
    }
    out
}

/// Extract an element's full text, mapping inline markup to Markdown.
///
/// Walks mixed content — the element's own text, each child's text, and the tail
/// following each child — so nothing is lost. A recognised inline tag is wrapped
/// in its markers; an unrecognised one contributes its text undecorated.
///
/// Note this is **not** interchangeable with [`element_text`], which reads only
/// the element's own text: for any element holding markup that is the text
/// before the first child, which truncates the value silently. A title holding a
/// chemical formula is exactly that case.
///
/// Whitespace at the edge of a formatted run is emitted **outside** that run's
/// markers, and stripped only once overall. Both halves matter, and upstream got
/// the first wrong in a way that produced broken Markdown rather than merely
/// ugly text: it stripped at every recursion level, so the space belonging to
/// `<b>Randomised </b><b>trial</b>` vanished and the runs welded into
/// `**Randomised****trial**`. Keeping the space where it sat is no better —
/// CommonMark requires an emphasis delimiter to be adjacent to non-whitespace,
/// so `**Randomised **` does not emphasise either. Moving it out yields
/// `**Randomised** **trial**`.
#[must_use]
pub fn text_with_formatting(el: Option<Node<'_, '_>>) -> String {
    walk_formatting(el).trim().to_string()
}

/// The recursive, non-stripping worker for [`text_with_formatting`].
///
/// Every text node is visited exactly once — the element's own text, then each
/// child's subtree, then the text that follows that child — and escaped as it is
/// read, so the markers added around a run are the only unescaped Markdown in
/// the result.
///
/// **The text-node model differs from ElementTree's, and this is where that
/// matters.** ElementTree splits mixed content into `el.text` and each child's
/// `.tail`; roxmltree keeps it as sibling text nodes. So a child's "tail" is the
/// **next sibling text node**, and the first text node is the element's own
/// text. Reading every text node as `own_text` of it, or scanning forward for
/// the next text sibling without stopping at an element, duplicates the
/// element's content — a first cut did exactly that and produced
/// `"tail**Bold** tail"`.
#[must_use]
pub fn walk_formatting(el: Option<Node<'_, '_>>) -> String {
    let Some(el) = el else {
        return String::new();
    };

    let mut parts: Vec<String> = vec![escape_markdown(&own_text(el))];
    for child in el.children() {
        if !child.is_element() {
            // A text node is either the element's own text — already emitted —
            // or the tail of the element before it, which that iteration
            // emitted. Emitting it here as well is the duplication above.
            continue;
        }
        let text = walk_formatting(Some(child));
        let markup = inline_markup();
        let (prefix, suffix) = markup
            .get(child.tag_name().name().to_lowercase().as_str())
            .copied()
            .unwrap_or(("", ""));
        let core = text.trim();
        if !core.is_empty() {
            let lead = &text[..text.len() - text.trim_start().len()];
            let trail = &text[text.trim_end().len()..];
            parts.push(format!("{lead}{prefix}{core}{suffix}{trail}"));
        } else {
            // An empty run would otherwise render as stray markers ("****").
            parts.push(text);
        }
        parts.push(escape_markdown(&tail_text(child)));
    }

    parts.join("")
}

/// The element's **own text**: what precedes its first child element.
///
/// ElementTree's `el.text` is exactly "the text before the first child", and
/// that qualifier is load-bearing rather than descriptive. roxmltree keeps mixed
/// content as sibling nodes, so an element that *leads* with a child has its
/// **tail** as the first text node — `<T><u>under</u>lined</T>` has one child,
/// `<u>`, and one text node, `"lined"`, which is `<u>`'s tail and not `<T>`'s own
/// text. Returning it here emits the tail twice: once as the element's own text
/// and once as the walker's tail, giving `"linedunderlined"`. That was a real
/// bug, caught by the oracle on the very first body that leads with markup.
///
/// So: scan the children in order, stop at the first element, and return the
/// text only if it came first.
fn own_text(el: Node<'_, '_>) -> String {
    for child in el.children() {
        if child.is_element() {
            return String::new();
        }
        if child.is_text() {
            return child.text().unwrap_or_default().to_string();
        }
    }
    String::new()
}

/// The text following an element, up to the next element sibling.
///
/// ElementTree's `child.tail`. In roxmltree that is the **next sibling text
/// node**, and the scan stops at the next element.
///
/// **The stop is defensive, not load-bearing**, and mutation is what showed it:
/// removing the `is_element` break changes no result any input can produce. The
/// reader guarantees only one text node between two element siblings, so the
/// scan collects at most that one node and then meets the following element —
/// where it stops with or without the check. There is no input with *two* text
/// nodes in one gap for the absent-stop version to over-collect.
///
/// It is kept because it states what a tail is — the text up to the next element
/// rather than the text up to the end of the parent — and because a parser that
/// merged adjacent text nodes differently would make it matter. A reader should
/// not add a test that tries to exercise it.
fn tail_text(child: Node<'_, '_>) -> String {
    let mut out = String::new();
    let mut next = child.next_sibling();
    while let Some(node) = next {
        if node.is_element() {
            break;
        }
        if node.is_text() {
            out.push_str(node.text().unwrap_or_default());
        }
        next = node.next_sibling();
    }
    out
}

/// Text content of an element, or `None`.
///
/// The counterpart to [`text_with_formatting`]: this reads only the element's
/// own text, which is what Python's `el.text` does and what most fields want.
///
/// An element with no text is `None`, **not** `""`. `ElementTree` gives
/// `el.text is None` for `<Year></Year>`, and the difference is observable:
/// `parse_pubdate` falls back to `MedlineDate` precisely when the year is
/// absent, so returning `""` there skips the fallback and yields an empty date
/// where Python yields the MedlineDate's year.
#[must_use]
pub fn element_text(el: Option<Node<'_, '_>>) -> Option<String> {
    let text = own_text(el?);
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

/// The first child element with the given tag name.
#[must_use]
pub fn child<'a, 'i>(el: Option<Node<'a, 'i>>, name: &str) -> Option<Node<'a, 'i>> {
    el?.children()
        .find(|c| c.is_element() && c.tag_name().name() == name)
}

/// Every descendant element with the given tag name, **not** just children.
///
/// Python's `el.findall("a/b")` is a path, and ElementTree's `findall` matches
/// direct children only; the paths used here (`"GrantList/Grant"`,
/// `"AffiliationInfo/Affiliation"`) are two levels, which [`path`] handles.
#[must_use]
pub fn descendants<'a, 'i>(el: Node<'a, 'i>, name: &'static str) -> Vec<Node<'a, 'i>> {
    el.descendants()
        .filter(|c| c.is_element() && c.tag_name().name() == name)
        .collect()
}

/// All direct child elements with the given tag name.
///
/// Python's `el.findall(name)`, and the reason this exists separately from
/// [`child`]: an `<AuthorList>` holds **several** `<Author>` elements, and a
/// function that returned the first would silently read one author of a paper.
/// That was a real bug here, caught by the oracle on the first multi-author
/// fixture.
#[must_use]
pub fn findall<'a, 'i>(el: Node<'a, 'i>, name: &str) -> Vec<Node<'a, 'i>> {
    el.children()
        .filter(|c| c.is_element() && c.tag_name().name() == name)
        .collect()
}

/// Walk a slash-separated child path, returning the matching elements.
///
/// Python's `el.findall(path)`. Every segment but the last takes the **first**
/// match, and the last returns **all** of them — so `"AuthorList/Author"` is
/// every author of the first `<AuthorList>`.
#[must_use]
pub fn path<'a, 'i>(el: Option<Node<'a, 'i>>, path: &str) -> Vec<Node<'a, 'i>> {
    let segments: Vec<&str> = path.split('/').collect();
    let mut current: Vec<Node<'a, 'i>> = match el {
        Some(el) => vec![el],
        None => return Vec::new(),
    };
    for (index, segment) in segments.iter().enumerate() {
        let last = index == segments.len() - 1;
        let mut next: Vec<Node<'a, 'i>> = Vec::new();
        for node in &current {
            let matches = findall(*node, segment);
            if last {
                next.extend(matches);
            } else {
                next.extend(matches.into_iter().take(1));
            }
        }
        current = next;
        if current.is_empty() {
            return Vec::new();
        }
    }
    current
}

/// Render an `Abstract` element as Markdown, or `None`.
///
/// Each `AbstractText` becomes one section. A section's label comes from the
/// `Label` attribute, falling back to `NlmCategory` — PubMed uses either, and
/// reading only `Label` drops the heading from every section labelled the other
/// way, running it into its neighbour. Labels render as `**HEADING:** text`;
/// sections are separated by a blank line so the structure survives into
/// Markdown.
///
/// Returns `None` when there is no text at all: `FetchedRecord.abstract` is an
/// `Option`, and an empty string would read as "this paper has a blank abstract"
/// rather than "none was given" — and would block the storage layer's
/// `COALESCE` fill-in from another source for ever.
#[must_use]
pub fn format_abstract_markdown(abstract_el: Option<Node<'_, '_>>) -> Option<String> {
    let abstract_el = abstract_el?;
    let mut sections: Vec<String> = Vec::new();
    for part in path(Some(abstract_el), "AbstractText") {
        let text = text_with_formatting(Some(part));
        if text.is_empty() {
            continue;
        }

        let mut label = part
            .attribute("Label")
            .unwrap_or_default()
            .trim()
            .to_string();
        if label.is_empty() {
            let category = part
                .attribute("NlmCategory")
                .unwrap_or_default()
                .trim()
                .to_string();
            if !category.is_empty()
                && !UNLABELLED_CATEGORIES.contains(&category.to_uppercase().as_str())
            {
                label = category;
            }
        }

        // The label is document text too, so it is escaped like any other run;
        // `text` arrives already escaped.
        sections.push(if label.is_empty() {
            text
        } else {
            format!("**{}:** {text}", escape_markdown(&label.to_uppercase()))
        });
    }

    if sections.is_empty() {
        None
    } else {
        Some(sections.join("\n\n"))
    }
}

/// Parse a `PubDate` element into a `YYYY-MM-DD` (or partial) date string.
///
/// Handles both numeric months and text abbreviations. Returns `None` if the
/// element is missing or has no `Year`.
#[must_use]
pub fn parse_pubdate(pubdate_el: Option<Node<'_, '_>>) -> Option<String> {
    let pubdate_el = pubdate_el?;
    let Some(year) = element_text(child(Some(pubdate_el), "Year")) else {
        // `MedlineDate` is the fallback, e.g. "2024 Jan-Feb".
        let medline_date = element_text(child(Some(pubdate_el), "MedlineDate"));
        return medline_date
            .filter(|d| d.len() >= 4)
            .map(|d| d[..4].to_string());
    };

    let Some(month_text) = element_text(child(Some(pubdate_el), "Month")) else {
        return Some(year);
    };

    // Accept known abbreviations or numeric months; for anything else (a season
    // like "Winter") drop the month rather than emit an invalid date such as
    // "2024-Winter".
    let stripped = month_text.trim();
    let month_key: String = stripped.to_lowercase().chars().take(3).collect();
    let month = if let Some(m) = month_map().get(month_key.as_str()) {
        (*m).to_string()
    } else if stripped.chars().all(|c| c.is_ascii_digit()) && !stripped.is_empty() {
        format!("{:0>2}", stripped)
    } else {
        return Some(year);
    };

    let Some(day_text) = element_text(child(Some(pubdate_el), "Day")) else {
        return Some(format!("{year}-{month}"));
    };
    Some(format!("{year}-{month}-{:0>2}", day_text))
}

/// Format one `<Author>` as `"Last, Fore"`, or `None`.
///
/// Returns `None` for an author with no `LastName` — a `<CollectiveName>`
/// consortium, which has no personal name to render.
#[must_use]
pub fn author_name(author_el: Node<'_, '_>) -> Option<String> {
    let last = element_text(child(Some(author_el), "LastName")).filter(|l| !l.is_empty())?;
    match element_text(child(Some(author_el), "ForeName")) {
        Some(fore) => Some(format!("{last}, {fore}")),
        None => Some(last),
    }
}

/// Extract funding awards from an `<Article>`'s `<GrantList>`.
///
/// A grant naming neither an agency nor an award id is skipped: it identifies no
/// award, and storing it would put an empty row in front of anyone counting a
/// paper's funders.
///
/// Exact repeats are collapsed, keeping first-occurrence order. PubMed really
/// does repeat a `<Grant>` block verbatim — 31 of 575 entries across 200
/// NIH-funded records, affecting 14 of them — and stored as separate rows those
/// inflate every count of a paper's funders, with no way for a reader to tell
/// PubMed's repetition from a genuine second award. Two grants differing in any
/// field are two grants.
///
/// `<Acronym>` — NIH's institute code, e.g. `"HL"` — is read by neither the key
/// nor the row. It is an abbreviation of `<Agency>` for one funder rather than
/// an independent fact, so two entries alike in agency, id and country but
/// differing in acronym are the same award; keeping it out of the key is what
/// lets those collapse.
#[must_use]
pub fn parse_grants(article_el: Node<'_, '_>) -> Vec<Grant> {
    let mut grants: Vec<Grant> = Vec::new();
    let mut seen: std::collections::BTreeSet<(String, String, String)> =
        std::collections::BTreeSet::new();
    for grant_el in path(Some(article_el), "GrantList/Grant") {
        let agency = element_text(child(Some(grant_el), "Agency"));
        let grant_id = element_text(child(Some(grant_el), "GrantID"));
        if agency.is_none() && grant_id.is_none() {
            continue;
        }
        let country = element_text(child(Some(grant_el), "Country"));
        let key = (
            agency.clone().unwrap_or_default(),
            grant_id.clone().unwrap_or_default(),
            country.clone().unwrap_or_default(),
        );
        if !seen.insert(key) {
            continue;
        }
        grants.push(Grant {
            agency,
            grant_id,
            country,
            ..Grant::default()
        });
    }
    grants
}

/// Parse a `PubmedArticle` element into a [`FetchedRecord`].
///
/// # Errors
///
/// Never — a document that parsed is read as far as it goes. A field that is
/// absent is absent, which is the state the storage layer's merge expects.
#[must_use]
pub fn parse_article(article_el: Node<'_, '_>) -> FetchedRecord {
    let medline = child(Some(article_el), "MedlineCitation");
    let article = child(medline, "Article");
    let pubmed_data = child(Some(article_el), "PubmedData");

    let pmid = element_text(child(medline, "PMID"));

    // Read with the walker, not `element_text`: a title holding markup (a
    // chemical formula, an italicised species name) is truncated at its first
    // child element by a bare read.
    let title = text_with_formatting(child(article, "ArticleTitle"));

    let abstract_text = format_abstract_markdown(child(article, "Abstract"));

    // Authors and their affiliations, from one pass over `<AuthorList>` so an
    // affiliation's author name is formatted by the same code that builds
    // `authors` — the two are meant to be matched, and separate formatting made
    // that guesswork.
    let mut authors: Vec<String> = Vec::new();
    let mut author_affiliations: Vec<AuthorAffiliation> = Vec::new();
    if let Some(author_list) = child(article, "AuthorList") {
        for (position, author_el) in path(Some(author_list), "Author").into_iter().enumerate() {
            let Some(name) = author_name(author_el) else {
                // A `<CollectiveName>` consortium. Its affiliations are dropped
                // with it, deliberately: `AuthorAffiliation.author` is
                // contracted to match a name in `authors`, which this entry is
                // absent from, so storing it would put a row in the table that
                // no join by author name can ever reach.
                continue;
            };
            authors.push(name.clone());
            // Deduplicated per author, for the same reason grants are — two
            // authors at one institution each keep it.
            let mut seen_affiliations: std::collections::BTreeSet<String> =
                std::collections::BTreeSet::new();
            for aff_el in path(Some(author_el), "AffiliationInfo/Affiliation") {
                // Read with the walker, not a bare read: NLM declares
                // `<Affiliation>` with the same `(%text;)*` content model as
                // `<ArticleTitle>`, so a superscript footnote marker truncates
                // the institution — and a *leading* one makes the bare read
                // `None`, which a truthiness guard then drops.
                let affiliation = text_with_formatting(Some(aff_el));
                if !affiliation.is_empty() && seen_affiliations.insert(affiliation.clone()) {
                    let mut row = AuthorAffiliation {
                        author: name.clone(),
                        affiliation,
                        position: position as i64,
                        ..AuthorAffiliation::default()
                    };
                    row.publication_id = 0;
                    author_affiliations.push(row);
                }
            }
        }
    }

    let journal = child(article, "Journal").and_then(|j| element_text(child(Some(j), "Title")));

    let pubdate_el = child(article, "Journal")
        .and_then(|j| child(Some(j), "JournalIssue"))
        .and_then(|ji| child(Some(ji), "PubDate"));
    let publication_date = parse_pubdate(pubdate_el);

    // DOI and PMC id from `ArticleIdList`.
    let mut doi: Option<String> = None;
    let mut pmc_id: Option<String> = None;
    if let Some(pubmed_data) = pubmed_data {
        for aid in path(Some(pubmed_data), "ArticleIdList/ArticleId") {
            match aid.attribute("IdType").unwrap_or_default() {
                "doi" => doi = own_text(aid).into(),
                "pmc" => pmc_id = own_text(aid).into(),
                _ => {}
            }
        }
    }

    // Keywords from MeSH headings.
    let mut keywords: Vec<String> = Vec::new();
    if let Some(medline) = medline {
        if let Some(mesh_list) = child(Some(medline), "MeshHeadingList") {
            for heading in path(Some(mesh_list), "MeshHeading") {
                for descriptor in path(Some(heading), "DescriptorName") {
                    let text = own_text(descriptor);
                    if !text.is_empty() {
                        keywords.push(text);
                    }
                }
            }
        }
    }

    // Publication types — the free Tier 1 quality filter classifies study design
    // from these, so a record without them skips straight to the paid LLM tiers.
    let mut publication_types: Vec<String> = Vec::new();
    if let Some(article) = article {
        for ptype in path(Some(article), "PublicationTypeList/PublicationType") {
            let text = own_text(ptype);
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                publication_types.push(trimmed.to_string());
            }
        }
    }

    // Full-text sources.
    let mut fulltext_sources: Vec<serde_json::Value> = Vec::new();
    if let Some(pmc_id) = pmc_id.as_deref() {
        fulltext_sources.push(serde_json::json!({
            "url": format!("{PMC_BASE_URL}{pmc_id}/"),
            "source": "pmc",
            "format": "html",
            "open_access": true,
        }));
    }
    if let Some(doi) = doi.as_deref() {
        fulltext_sources.push(serde_json::json!({
            "url": format!("{DOI_BASE_URL}{doi}"),
            "source": "publisher",
            "format": "html",
            "open_access": false,
        }));
    }

    let mut record = FetchedRecord::new(title, "pubmed");
    record.doi = doi;
    record.pmid = pmid;
    record.pmc_id = pmc_id;
    record.abstract_text = abstract_text;
    record.authors = authors;
    record.journal = journal;
    record.publication_date = publication_date;
    record.keywords = keywords;
    record.publication_types = publication_types;
    record.fulltext_sources = fulltext_sources;
    record.grants = article.map(parse_grants).unwrap_or_default();
    record.author_affiliations = author_affiliations;
    record
}

/// Parse a whole `PubmedArticleSet` document into records.
///
/// Returns one record per **child element of the set**, whatever it is called.
/// A `<PubmedBookArticle>` therefore yields a record that is almost entirely
/// empty — it has a different shape (`<BookDocument>`, not `<MedlineCitation>`)
/// and nothing in it matches the paths this reader looks for.
///
/// That empty record is deliberate and load-bearing, on both sides:
///
/// - **Reconciliation counts what the server handed over**, not what the reader
///   found interesting. Skipping the element here would make `delivered` lower
///   than the count PubMed promised and report a phantom shortfall on every day
///   carrying a book chapter.
/// - **It is stored.** A record with no title and no identifier is dropped by
///   the storage layer's dedup, so nothing spurious is written; but it is what
///   the walk delivered.
///
/// A first cut filtered the children to `PubmedArticle` by name — a tidy-looking
/// improvement that the oracle caught, because Python passes every element to
/// `_parse_article_xml` and lets the paths find nothing.
///
/// # Errors
///
/// Naming the parse failure, so a caller can report the document rather than the
/// contract.
pub fn parse_article_set(xml: &str) -> Result<Vec<FetchedRecord>, String> {
    let document = Document::parse(xml).map_err(|e| format!("XML: {e}"))?;
    let root = document.root_element();
    Ok(root
        .children()
        .filter(roxmltree::Node::is_element)
        .map(parse_article)
        .collect())
}

/// The ESearch term for one publication day.
///
/// `[Date - Publication]` rather than `[EDAT]` is deliberate and **load-bearing**:
/// it is the field bmlib syncs by, and the two disagree by orders of magnitude
/// on exactly the days this module has to handle (see `docs/DECISIONS.md`).
#[must_use]
pub fn day_term(target_date: NaiveDate) -> String {
    format!(
        "(\"{}\"[Date - Publication])",
        target_date.format("%Y/%m/%d")
    )
}

/// The stored identity of the partition spanning `lo` to `hi`.
///
/// **The one constructor**, because the resume skip rule compares this string:
/// a second spelling of the same range matches no checkpoint, so resume degrades
/// to a full re-fetch **with nothing raised**. Pinned by a test that compares it
/// against Python's own output.
///
/// Not scoped by `part_scheme`: the unique key is `(source, date, part_key)` and
/// the skip rule compares this string alone, so a second scheme reusing this
/// spelling would match rather than be isolated. What the scheme column buys is
/// that stale rows can be *recognised* and dropped deliberately.
///
/// A first cut of this port wrote `"{lo}_{hi}"` — a plausible-looking key that
/// would have matched no row Python ever wrote, silently re-fetching every
/// partitioned day. The oracle caught it.
#[must_use]
pub fn part_key(lo: NaiveDate, hi: NaiveDate) -> String {
    format!("edat:{}:{}", lo.format("%Y-%m-%d"), hi.format("%Y-%m-%d"))
}

/// The EDAT range term for one partition.
#[must_use]
pub fn edat_range_term(day_term: &str, lo: NaiveDate, hi: NaiveDate) -> String {
    format!(
        "{day_term} AND (\"{}\"[EDAT] : \"{}\"[EDAT])",
        lo.format("%Y/%m/%d"),
        hi.format("%Y/%m/%d")
    )
}

// ---------------------------------------------------------------------------
// The E-utilities walk
// ---------------------------------------------------------------------------

/// Why a day could not be planned into parts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlanError {
    /// The root range holds fewer records than the day does, so some record of
    /// the day is indexed outside it and would be silently absent.
    RootNotCovering {
        /// The root range's first day, ISO.
        lo: String,
        /// The root range's last day, ISO.
        hi: String,
        /// What the root range holds.
        root_count: i64,
        /// What the day holds.
        day_count: i64,
    },
    /// A single Entrez date exceeds what one history session can serve, and an
    /// Entrez date cannot be split further.
    Unsplittable {
        /// The Entrez date, ISO.
        edat_day: String,
        /// How many records share it.
        count: i64,
    },
    /// The ladder's root range is inverted.
    InvertedRoot {
        /// The root range's first day, ISO.
        lo: String,
        /// The root range's last day, ISO.
        hi: String,
    },
}

impl std::fmt::Display for PlanError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PlanError::RootNotCovering {
                lo,
                hi,
                root_count,
                day_count,
            } => write!(
                f,
                "the Entrez-date range {lo}..{hi} holds {root_count} of this day's \
                 {day_count} records, so {} of them lie outside the ladder and would \
                 be silently absent; refusing the day",
                day_count - root_count
            ),
            PlanError::Unsplittable { edat_day, count } => write!(
                f,
                "{count} records share the Entrez date {edat_day}, above the \
                 {EFETCH_MAX_RETRIEVABLE} a history session serves, and an Entrez date \
                 cannot be split further; refusing the day"
            ),
            PlanError::InvertedRoot { lo, hi } => {
                write!(f, "the ladder's root range is inverted: {lo} is after {hi}")
            }
        }
    }
}

impl std::error::Error for PlanError {}

/// One Entrez-date range of a day, small enough to fetch in one session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Partition {
    /// The range's first (inclusive) Entrez date.
    pub lo: NaiveDate,
    /// The range's last (inclusive) Entrez date.
    pub hi: NaiveDate,
    /// The count the range holds.
    pub promised: i64,
}

impl Partition {
    /// This part's identity in `download_day_parts`.
    #[must_use]
    pub fn key(&self) -> String {
        part_key(self.lo, self.hi)
    }
}

/// The ladder's root range start.
#[must_use]
pub fn edat_root_lo() -> NaiveDate {
    NaiveDate::from_ymd_opt(1900, 1, 1).expect("a literal date")
}

/// The ladder's root range end.
#[must_use]
pub fn edat_root_hi() -> NaiveDate {
    NaiveDate::from_ymd_opt(2100, 12, 31).expect("a literal date")
}

/// Split a day into Entrez-date ranges that each fit in one session.
///
/// `[lo, mid]` and `[mid+1, hi]` tile `[lo, hi]` as arithmetic and every record
/// carries exactly one Entrez date, so the parts are disjoint and covering by
/// construction — below the root. At the root that is an empirical claim, so
/// `probe_root` verifies it.
///
/// Only the left child is counted; the right is the parent's count minus it,
/// which the tiling makes exact and which halves the ladder's cost.
///
/// `count_fn` is injected rather than reached for, so the ladder is testable
/// without HTTP — and, more importantly, so a test can present **two counts at
/// two instants**, which is the input the `right == 0` measurement exists for.
///
/// `known_count` is the re-partition path: a part's own session ESearch has
/// already reported its count above the cap, and re-counting the same range
/// fresh risks a *lower* answer that collapses back to a single partition
/// spanning the identical range — pushed onto the queue, fetched again, over-cap
/// again, looping against NCBI for ever. Passing the count that triggered the
/// re-plan guarantees the descent narrows (or raises [`PlanError::Unsplittable`]
/// at `lo == hi`), so it always terminates.
///
/// # Errors
///
/// [`PlanError::InvertedRoot`], [`PlanError::RootNotCovering`] or
/// [`PlanError::Unsplittable`]; also whatever `count_fn` raises.
pub fn plan_partitions(
    count_fn: &mut dyn FnMut(&str) -> Result<i64, String>,
    day_term: &str,
    day_count: i64,
    lo: NaiveDate,
    hi: NaiveDate,
    probe_root: bool,
    known_count: Option<i64>,
) -> Result<Vec<Partition>, PlanError> {
    if lo > hi {
        return Err(PlanError::InvertedRoot {
            lo: lo.format("%Y-%m-%d").to_string(),
            hi: hi.format("%Y-%m-%d").to_string(),
        });
    }

    let root_count = match known_count {
        Some(n) => n,
        None => {
            let n = count_fn(&edat_range_term(day_term, lo, hi)).map_err(|_| {
                PlanError::RootNotCovering {
                    lo: lo.format("%Y-%m-%d").to_string(),
                    hi: hi.format("%Y-%m-%d").to_string(),
                    root_count: 0,
                    day_count,
                }
            })?;
            if probe_root && n < day_count {
                return Err(PlanError::RootNotCovering {
                    lo: lo.format("%Y-%m-%d").to_string(),
                    hi: hi.format("%Y-%m-%d").to_string(),
                    root_count: n,
                    day_count,
                });
            }
            n
        }
    };

    let mut parts: Vec<Partition> = Vec::new();
    descend(
        count_fn,
        day_term,
        lo,
        hi,
        root_count,
        lo,
        hi,
        known_count.is_some(),
        &mut parts,
    )?;
    Ok(parts)
}

/// One node of the ladder. Iterative rather than recursive so a pathological
/// range cannot exhaust the stack; the recursion depth here is bounded by
/// `log2(days)`, but an explicit stack costs nothing and does not depend on
/// that remaining true.
#[allow(clippy::too_many_arguments)]
fn descend(
    count_fn: &mut dyn FnMut(&str) -> Result<i64, String>,
    day_term: &str,
    root_lo: NaiveDate,
    root_hi: NaiveDate,
    root_count: i64,
    lo: NaiveDate,
    hi: NaiveDate,
    known_count_given: bool,
    parts: &mut Vec<Partition>,
) -> Result<(), PlanError> {
    let mut stack: Vec<(NaiveDate, NaiveDate, i64)> = vec![(lo, hi, root_count)];
    while let Some((lo, hi, n)) = stack.pop() {
        if n <= 0 {
            continue;
        }
        if n <= EFETCH_MAX_RETRIEVABLE {
            parts.push(Partition {
                lo,
                hi,
                promised: n,
            });
            continue;
        }
        if lo == hi {
            if (lo, hi) == (root_lo, root_hi) && known_count_given {
                // The re-partition path: `n` is the part's own session ESearch,
                // so it is measured already, and measuring again is what
                // `known_count` exists to prevent.
                return Err(PlanError::Unsplittable {
                    edat_day: lo.format("%Y-%m-%d").to_string(),
                    count: n,
                });
            }
            // `n` may have arrived by subtraction, so measure before refusing a
            // whole day on it. Two things follow from the true count, and both
            // matter more than the one ESearch they cost on a path that is
            // otherwise about to abandon hundreds.
            //
            // A parent counted higher than its children really hold parks the
            // surplus on the right, and the root reaches 2100 — so the surplus
            // walks down a structurally empty tail to a single future date
            // claiming tens of thousands of records. Refused on that, the day
            // fails and is re-fetched on every later run (~562 requests and
            // ~1 GB each time) over a range PubMed has never indexed anything
            // into. Measured, the phantom is 0 and simply disappears.
            //
            // And a date the subtraction merely overstated is an ordinary part.
            let measured = count_fn(&edat_range_term(day_term, lo, hi)).map_err(|_| {
                PlanError::Unsplittable {
                    edat_day: lo.format("%Y-%m-%d").to_string(),
                    count: n,
                }
            })?;
            if measured <= 0 {
                continue;
            }
            if measured <= EFETCH_MAX_RETRIEVABLE {
                parts.push(Partition {
                    lo,
                    hi,
                    promised: measured,
                });
                continue;
            }
            return Err(PlanError::Unsplittable {
                edat_day: lo.format("%Y-%m-%d").to_string(),
                count: measured,
            });
        }

        let mid = lo + chrono::Duration::days((hi - lo).num_days() / 2);
        let mut left =
            count_fn(&edat_range_term(day_term, lo, mid)).map_err(|_| PlanError::Unsplittable {
                edat_day: lo.format("%Y-%m-%d").to_string(),
                count: n,
            })?;
        let mut right = n - left;

        if right <= 0 {
            // A **derived zero** is the one wrong derivation that cannot heal,
            // so it is measured instead of trusted. Every other error in `right`
            // still yields a part, and a part re-counts itself when its session
            // opens; a zero yields no part at all, so the range is never
            // visited, every part planned around it reconciles perfectly, and
            // the shortfall reaches only the day total — where anything under
            // the floor completes on a note. `completed` is durable, so those
            // records are never sought again.
            right = count_fn(&edat_range_term(
                day_term,
                mid + chrono::Duration::days(1),
                hi,
            ))
            .map_err(|_| PlanError::Unsplittable {
                edat_day: lo.format("%Y-%m-%d").to_string(),
                count: n,
            })?;
            let _ = &mut left;
        }

        // Push right first so left is popped first, keeping the parts in
        // ascending range order.
        stack.push((mid + chrono::Duration::days(1), hi, right));
        stack.push((lo, mid, left));
    }
    Ok(())
}

/// What one history session's page walk produced.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct WalkOutcome {
    /// Records parsed and handed to the caller.
    pub processed: i64,
    /// Record elements the server handed over — **never** `processed`.
    pub delivered: i64,
    /// A page delivered nothing while the promise was still unmet.
    pub stalled: bool,
    /// Set when a page raised; the walk stops and the caller fails the day.
    pub error: Option<String>,
}

/// One efetch page: what will be parsed, and what was delivered.
///
/// A plain `(Vec<..>, i64)` invites the one misreading this pair exists to
/// prevent — that the count is the list's length. **It is not**, and the gap
/// between them is the whole point.
#[derive(Debug, Clone, Default)]
pub struct EFetchPage {
    /// `PubmedArticle` elements, the only kind the fetcher parses.
    pub articles: Vec<FetchedRecord>,
    /// Record elements the server handed over — never `articles.len()`.
    pub delivered: i64,
}

/// The two record element names a `PubmedArticleSet` may carry.
///
/// Counted **by name** rather than taking every child of the set.
/// `<DeleteCitation>` is also a legal child, and counting it as a delivery is
/// wrong in the expensive direction twice over: it inflates delivery so a real
/// shortfall clears the floor, and — because the stall rule is `delivered == 0`
/// — a page carrying nothing but one of them stops looking like the stall it is.
pub const RECORD_ELEMENTS: [&str; 2] = ["PubmedArticle", "PubmedBookArticle"];

/// Count the record elements in a parsed `PubmedArticleSet`.
///
/// # Errors
///
/// Naming a root that is not a `PubmedArticleSet`. NCBI answers an expired or
/// evicted history session with `<eFetchResult><ERROR>…</ERROR></eFetchResult>`
/// at **HTTP 200**, so a status check never fires and a child search returns an
/// empty list — a rejected page wearing the shape of an exhausted one.
///
/// **The 200 is a property of the history-session request, not of efetch.** An
/// id-based efetch, which `transparency/analyzer.py` makes, answered 400 for a
/// malformed id list and an empty record set for an id NCBI does not hold. So
/// that module reaches this envelope by no measured route and guards a different
/// shape.
pub fn count_delivered(xml: &str) -> Result<(Vec<FetchedRecord>, i64), String> {
    let document = Document::parse(xml).map_err(|e| format!("XML: {e}"))?;
    let root = document.root_element();
    if root.tag_name().name() != "PubmedArticleSet" {
        let error = path(Some(root), "ERROR")
            .first()
            .and_then(|e| element_text(Some(*e)))
            .or_else(|| {
                root.descendants()
                    .find(|n| n.is_element() && n.tag_name().name() == "ERROR")
                    .and_then(|e| element_text(Some(e)))
            });
        return Err(format!(
            "efetch returned <{}> rather than <PubmedArticleSet>{}",
            root.tag_name().name(),
            error.map_or(String::new(), |e| format!(" (NCBI said: {e})"))
        ));
    }
    let articles: Vec<FetchedRecord> = path(Some(root), "PubmedArticle")
        .into_iter()
        .map(parse_article)
        .collect();
    let delivered = RECORD_ELEMENTS
        .iter()
        .map(|name| findall(root, name).len() as i64)
        .sum();
    Ok((articles, delivered))
}

/// Walk one history session's pages.
///
/// `retstart` indexes the **session's UID list**, not the records delivered so
/// far: page k covers the UIDs at `[k·EFETCH_PAGE_SIZE, (k+1)·EFETCH_PAGE_SIZE)`
/// whether or not every one of them yields a record. Named for the constant that
/// actually strides, since the claim holds only while the walk's step and the
/// page's `retmax` stay equal. Measured 2026-08-20 (#96): a page's record
/// elements are exactly that slice of esearch's own `IdList`, in order,
/// `<PubmedBookArticle>` entries included.
///
/// **Advancing by what arrived — the fix #96 proposed — would re-request the tail
/// of every short page, deliver those records twice, and count the duplicates as
/// delivery**, which is precisely what would hide a real shortfall from the
/// reconciliation.
#[must_use]
pub fn walk_session(
    promised: i64,
    mut fetch_page: impl FnMut(usize) -> Result<EFetchPage, String>,
    on_record: &mut dyn FnMut(FetchedRecord),
    on_page: &mut dyn FnMut(i64),
) -> WalkOutcome {
    let mut outcome = WalkOutcome::default();
    let mut retstart = 0usize;
    while (retstart as i64) < promised {
        let page = match fetch_page(retstart) {
            Ok(page) => page,
            Err(error) => {
                outcome.error = Some(error);
                return outcome;
            }
        };

        outcome.delivered += page.delivered;
        for record in page.articles {
            on_record(record);
            outcome.processed += 1;
        }

        if page.delivered == 0 {
            // The session holds `promised` UIDs, so an empty page before the
            // walk is done means it stopped serving them. Paging on costs a
            // request per remaining page and returns nothing — up to 9 of them
            // on the 5,000-record day measured for #88, which is 10 pages of
            // 500.
            outcome.stalled = true;
            return outcome;
        }

        on_page(outcome.processed);
        retstart += EFETCH_PAGE_SIZE;
    }
    outcome
}

// ---------------------------------------------------------------------------
// One part's itinerary
// ---------------------------------------------------------------------------

/// What to do with one planned part when its turn comes.
///
/// Extracted from `_fetch_partitioned`'s loop because the decision is pure and
/// the loop around it is not. Each variant carries the reason, so a caller
/// logging what happened does not have to re-derive it — and so a test can ask
/// "was this skipped, and on what grounds" without a transport or a database.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PartStep {
    /// Skip it: a checkpoint describes the same range with the same promise.
    Skip {
        /// The credit to the day's delivered total, which is the checkpoint's
        /// own `promised`.
        credited: i64,
    },
    /// Re-fetch it after all: a checkpoint exists but its promise has moved.
    RefetchBecauseCountMoved {
        /// What the checkpoint held.
        was: i64,
        /// What this run's plan says now.
        now: i64,
    },
    /// Walk it as a fresh session.
    Walk,
}

/// Decide what to do with one planned part.
///
/// **The promise is compared, not only the key.** Skipping on the key alone
/// would permanently lose every record a part gained since it was checkpointed,
/// so a count that has moved forces a re-fetch. And a skipped part is **credited
/// at its stored promise**: the day-total reconciliation judges every part's
/// delivery against the day's own count, and a resumed run never issues the
/// skipped part's own EFetch, so without the credit every resumed day would
/// fail.
#[must_use]
pub fn part_step(planned_promised: i64, checkpoint_promised: Option<i64>) -> PartStep {
    match checkpoint_promised {
        None => PartStep::Walk,
        Some(was) if was == planned_promised => PartStep::Skip { credited: was },
        Some(was) => PartStep::RefetchBecauseCountMoved {
            was,
            now: planned_promised,
        },
    }
}

/// Why a part ended the day.
///
/// Every failure path fails the **whole day**, because a day recorded
/// `completed` is never re-offered: a part that could not be verified must not
/// be allowed to leave the day looking whole.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PartRefusal {
    /// The part grew between planning and fetching, so it is split again rather
    /// than walked — the last page of an over-cap session is silently clamped,
    /// and walking would look like an ordinary short day.
    Replan {
        /// The count that triggered the re-plan.
        count: i64,
    },
    /// The part's own ESearch reported fewer records than planning measured,
    /// below the shortfall floor.
    ///
    /// **The asymmetry is the tell**: a part that *delivers* 1 of 5,000 fails
    /// the day, so a part that *claims* 1 having been measured at 5,000 thirty
    /// seconds ago cannot pass either. Reconciled with the same floor as every
    /// other comparison rather than a new constant — equality would fail a day
    /// for the one-record drift two requests at two instants routinely show, and
    /// a day recorded `failed` is re-fetched on every later run for the life of
    /// the installation.
    ClaimedTooLittle {
        /// What the session's own search reported.
        claimed: i64,
        /// What planning measured.
        measured: i64,
    },
    /// The part came back with a count but **no history session**, so there is
    /// nothing to walk.
    NoSession,
    /// A page of the part failed.
    WalkFailed {
        /// The walker's message.
        message: String,
    },
    /// The walk finished but delivered too little.
    DeliveredTooLittle {
        /// The reconciliation's message.
        message: String,
    },
}

impl std::fmt::Display for PartRefusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PartRefusal::Replan { count } => write!(
                f,
                "part grew to {count} records between planning and fetching"
            ),
            PartRefusal::ClaimedTooLittle { claimed, measured } => write!(
                f,
                "part's session reported {claimed} records where planning measured \
                 {measured}, below the floor"
            ),
            PartRefusal::NoSession => {
                write!(f, "returned a count without a history session")
            }
            PartRefusal::WalkFailed { message } => write!(f, "{message}"),
            PartRefusal::DeliveredTooLittle { message } => write!(f, "{message}"),
        }
    }
}

/// Whether a part may be **checkpointed** once it has been walked.
///
/// Flushing and checkpointing are **deliberately different questions**, and
/// collapsing them was a real defect (#105 review, F1):
///
/// - The records must **always** be flushed, because the caller's per-part
///   callback is the only thing that empties its buffer. Reporting only clean
///   parts made the per-part memory bound conditional on the source behaving —
///   37 noted parts of a 242,216-record day would be held in memory in their
///   entirety, precisely when NCBI is degraded.
/// - But a noted part must **not** be checkpointed: skipping it on a later
///   resumed run would credit it at its full `promised` and manufacture the very
///   records the note is reporting missing, with no note surviving into that
///   run's result to say so.
///
/// Both reconciles have to be clean, for one reason: **a note dies with the run
/// that produced it.**
#[must_use]
pub fn may_checkpoint(plan_noted: bool, walk_noted: bool) -> bool {
    !plan_noted && !walk_noted
}

/// The counts a part contributes to the day.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct PartCredit {
    /// Records this run walked — what the returned result and progress report.
    pub processed: i64,
    /// Record elements the server handed over, plus the promised count of every
    /// skipped part.
    pub delivered: i64,
}

impl PartCredit {
    /// The credit for a skipped part.
    ///
    /// A skipped part is credited to **delivered** (what the day-total
    /// reconciliation judges) but **never to processed** (what progress and the
    /// returned record count report): those two count only records this run
    /// itself walked. On a resumed day the returned count and the progress total
    /// are therefore both less than the day's real size by however many records
    /// the skipped parts hold — correct for "what did this run do", not for "how
    /// big is this day".
    #[must_use]
    pub fn skipped(promised: i64) -> Self {
        PartCredit {
            processed: 0,
            delivered: promised,
        }
    }
}

// ---------------------------------------------------------------------------
// The E-utilities transport
// ---------------------------------------------------------------------------

/// What an ESearch returned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ESearchResult {
    /// The record count the source reported.
    pub count: i64,
    /// The history session's `WebEnv`, when one was opened.
    pub web_env: Option<String>,
    /// The history session's `QueryKey`, when one was opened.
    pub query_key: Option<String>,
}

/// The E-utilities calls the walk makes, so a test scripts them.
///
/// One trait rather than two so a scripted source is one object: the part loop
/// mixes planning searches with page fetches, and splitting them would mean
/// scripting the same day twice.
pub trait Eutils {
    /// Run an ESearch for a term.
    ///
    /// # Errors
    ///
    /// Naming the failure, so the caller reports the cause rather than an empty
    /// message.
    fn esearch(
        &self,
        term: &str,
        api_key: Option<&str>,
        use_history: bool,
    ) -> Result<ESearchResult, String>;

    /// Fetch one page of a history session.
    ///
    /// # Errors
    ///
    /// Naming the failure.
    fn efetch(
        &self,
        web_env: &str,
        query_key: &str,
        retstart: usize,
        api_key: Option<&str>,
    ) -> Result<EFetchPage, String>;
}

/// Read an ESearch response body.
///
/// # Errors
///
/// A response with no usable `<Count>`. NCBI answers a bad request — an unknown
/// db, an invalid term, a throttled key — with **HTTP 200 and an `<ERROR>`
/// document that has no `<Count>` at all**, so treating an absent element as zero
/// would report a rejected search as a day with no publications.
///
/// `element_text` returns `None` for both an absent and an empty element, so a
/// truthiness fallback would collapse "NCBI rejected the search" into "the day
/// was quiet" — the same silent failure the session guard exists to prevent,
/// reached one step earlier and past that guard, since an `<ERROR>` document
/// carries no session either.
pub fn read_esearch(xml: &str) -> Result<ESearchResult, String> {
    let document = Document::parse(xml).map_err(|e| format!("XML: {e}"))?;
    let root = document.root_element();

    let raw_count = element_text(child(Some(root), "Count"));
    let usable = raw_count
        .as_deref()
        .map(str::trim)
        .filter(|c| !c.is_empty() && c.chars().all(|ch| ch.is_ascii_digit()));
    let Some(raw_count) = usable else {
        let error = element_text(child(Some(root), "ERROR"))
            .or_else(|| element_text(child(Some(root), "ErrorList")));
        return Err(format!(
            "esearch returned no usable <Count>{}",
            error.map_or(String::new(), |e| format!(" (NCBI said: {e})"))
        ));
    };

    Ok(ESearchResult {
        count: raw_count.parse::<i64>().map_err(|e| e.to_string())?,
        web_env: element_text(child(Some(root), "WebEnv")),
        query_key: element_text(child(Some(root), "QueryKey")),
    })
}

/// The E-utilities transport over an [`HttpClient`].
pub struct HttpEutils {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
}

impl Eutils for HttpEutils {
    fn esearch(
        &self,
        term: &str,
        api_key: Option<&str>,
        use_history: bool,
    ) -> Result<ESearchResult, String> {
        let mut query = vec![
            ("db", "pubmed".to_string()),
            ("term", term.to_string()),
            ("retmax", "0".to_string()),
        ];
        if use_history {
            query.push(("usehistory", "y".to_string()));
        }
        if let Some(key) = api_key {
            query.push(("api_key", key.to_string()));
        }
        let url = format!("{ESEARCH_URL}?{}", encode_query(&query));
        let response = self.client.get(&url).map_err(|e| e.to_string())?;
        if !response.is_success() {
            return Err(format!("{url} returned HTTP {}", response.status));
        }
        read_esearch(response.text().map_err(|e| e.to_string())?)
    }

    fn efetch(
        &self,
        web_env: &str,
        query_key: &str,
        retstart: usize,
        api_key: Option<&str>,
    ) -> Result<EFetchPage, String> {
        let mut query = vec![
            ("db", "pubmed".to_string()),
            ("query_key", query_key.to_string()),
            ("WebEnv", web_env.to_string()),
            ("retstart", retstart.to_string()),
            ("retmax", EFETCH_PAGE_SIZE.to_string()),
            ("retmode", "xml".to_string()),
        ];
        if let Some(key) = api_key {
            query.push(("api_key", key.to_string()));
        }
        let url = format!("{EFETCH_URL}?{}", encode_query(&query));
        let response = self.client.get(&url).map_err(|e| e.to_string())?;
        if !response.is_success() {
            return Err(format!("{url} returned HTTP {}", response.status));
        }
        let (articles, delivered) = count_delivered(response.text().map_err(|e| e.to_string())?)?;
        Ok(EFetchPage {
            articles,
            delivered,
        })
    }
}

/// Percent-encode a query, leaving the characters E-utilities accepts bare.
fn encode_query(pairs: &[(&str, String)]) -> String {
    pairs
        .iter()
        .map(|(k, v)| format!("{k}={}", encode_component(v)))
        .collect::<Vec<_>>()
        .join("&")
}

/// Percent-encode one query component.
///
/// Only the characters that would change the parse are encoded, so a term is
/// legible in a log — which is the point of logging the URL at all.
fn encode_component(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Fetching a day
// ---------------------------------------------------------------------------

/// What one day's fetch produced.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PubMedResult {
    /// Records **this run** walked.
    ///
    /// Never includes a skipped part's credited records: those were stored by an
    /// earlier run, and this counts what this run did.
    pub processed: i64,
    /// `"completed"` or `"failed"`.
    pub status: String,
    /// The failure, when the day failed.
    pub error: Option<String>,
    /// Shortfall notes for a day that nevertheless completed.
    pub note: Option<String>,
}

impl PubMedResult {
    fn completed(processed: i64, notes: Vec<String>) -> Self {
        PubMedResult {
            processed,
            status: "completed".to_string(),
            error: None,
            note: if notes.is_empty() {
                None
            } else {
                Some(notes.join("; "))
            },
        }
    }

    fn failed(processed: i64, error: impl Into<String>) -> Self {
        PubMedResult {
            processed,
            status: "failed".to_string(),
            error: Some(error.into()),
            note: None,
        }
    }

    /// Whether the day completed.
    #[must_use]
    pub fn is_completed(&self) -> bool {
        self.status == "completed"
    }
}

/// The day a partitioned fetch is for.
pub struct PartitionRequest<'a> {
    /// The publication day.
    pub target_date: NaiveDate,
    /// The day's own `[Date - Publication]` search term.
    pub day_term: &'a str,
    /// The day's own record count, above the cap.
    ///
    /// Used to validate the root probe and to judge the **day-total**
    /// reconciliation at the end, so it is deliberately independent of the
    /// ladder's root: a ladder whose root promises more than the day does is the
    /// phantom the whole-range measurement exists for, and a test that wants to
    /// exercise the part loop without that arithmetic states both numbers.
    pub day_count: i64,
    /// Optional NCBI API key.
    pub api_key: Option<&'a str>,
    /// Parts a previous run finished, keyed by part key.
    pub completed_parts: &'a std::collections::BTreeMap<String, PartCheckpoint>,
    /// The ladder's root range.
    ///
    /// `1900-01-01..2100-12-31` in production, and injectable because the root
    /// is what the ladder splits from: a test that scripts counts by range has
    /// to know which ranges will be asked for, and deriving the production
    /// split points from a black-box script is both fragile and unnecessary. The
    /// real one is wide enough to cover any Entrez date PubMed can hold, and the
    /// root probe is what refuses a day whose records lie outside it.
    pub root_lo: NaiveDate,
    /// The ladder's root range end.
    pub root_hi: NaiveDate,
}

impl<'a> PartitionRequest<'a> {
    /// A request whose ladder root is `root` and whose day count is its own.
    ///
    /// The two are separate fields because they answer different questions; this
    /// sets both to `count`, which is what the production caller wants.
    #[must_use]
    pub fn over(
        target_date: NaiveDate,
        day_term: &'a str,
        count: i64,
        completed_parts: &'a std::collections::BTreeMap<String, PartCheckpoint>,
        root: (NaiveDate, NaiveDate),
    ) -> Self {
        PartitionRequest {
            target_date,
            day_term,
            day_count: count,
            api_key: None,
            completed_parts,
            root_lo: root.0,
            root_hi: root.1,
        }
    }
}

impl<'a> PartitionRequest<'a> {
    /// A request over the production root range.
    #[must_use]
    pub fn new(
        target_date: NaiveDate,
        day_term: &'a str,
        day_count: i64,
        completed_parts: &'a std::collections::BTreeMap<String, PartCheckpoint>,
    ) -> Self {
        PartitionRequest {
            target_date,
            day_term,
            day_count,
            api_key: None,
            completed_parts,
            root_lo: edat_root_lo(),
            root_hi: edat_root_hi(),
        }
    }
}

/// The per-part callbacks a caller supplies.
pub struct PartCallbacks<'a> {
    /// Called with a checkpoint for a part that reconciled clean on both counts,
    /// or `None` for one that came up short on either.
    ///
    /// **Called for every part that finished without failing**, which is what
    /// drains the caller's buffer — so it may not be conditional on the part
    /// being clean. See [`may_checkpoint`] for why the *checkpoint* is.
    pub on_part_finished: &'a mut dyn FnMut(Option<PartCheckpoint>),
    /// Called with the part key of every part skipped because a checkpoint still
    /// describes it.
    pub on_part_skipped: &'a mut dyn FnMut(&str),
    /// Called with the running processed count and the day's own total.
    pub on_progress: &'a mut dyn FnMut(i64, i64, &str),
}

/// Fetch a day too large for one history session, as Entrez-date parts.
///
/// A history session serves only its first [`EFETCH_MAX_RETRIEVABLE`] records, so
/// a day above that cannot be completed through one. It is split into
/// Entrez-date ranges that each fit — disjoint and covering, so every record is
/// fetched exactly once — and each part is walked as an ordinary session.
///
/// **Every failure path fails the whole day.** A day recorded `completed` is
/// never re-offered, so a part that could not be verified must not be allowed to
/// leave the day looking whole.
#[must_use]
pub fn fetch_partitioned(
    transport: &dyn Eutils,
    request: &PartitionRequest<'_>,
    callbacks: &mut PartCallbacks<'_>,
    on_record: &mut dyn FnMut(FetchedRecord),
) -> PubMedResult {
    let PartitionRequest {
        target_date,
        day_term,
        day_count,
        api_key,
        completed_parts,
        root_lo,
        root_hi,
    } = *request;
    let date_str = target_date.format("%Y-%m-%d").to_string();
    let mut notes: Vec<String> = Vec::new();
    let mut processed = 0i64;
    let mut delivered = 0i64;

    // Planning is ESearch, so it fails the way every other request here does.
    // The under-cap path returns a failed result for exactly this, and one
    // public function must not answer the same transient with a return value or
    // an exception depending on how large the day happened to be.
    let mut count_fn =
        |term: &str| -> Result<i64, String> { Ok(transport.esearch(term, api_key, false)?.count) };
    let mut parts = match plan_partitions(
        &mut count_fn,
        day_term,
        day_count,
        root_lo,
        root_hi,
        true,
        None,
    ) {
        Ok(parts) => parts,
        Err(e) => return PubMedResult::failed(0, e.to_string()),
    };

    // A queue, because a part that grew is replaced by its children at the front.
    let mut pending: std::collections::VecDeque<Partition> = parts.drain(..).collect();

    while let Some(part) = pending.pop_front() {
        // The skip decision, from the checkpoint that describes this part.
        let checkpoint = completed_parts.get(&part.key()).map(|c| c.promised);
        match part_step(part.promised, checkpoint) {
            PartStep::Skip { credited } => {
                // Counted as delivered because a previous run delivered it. The
                // checkpoint is written only after that part reconciled, so
                // without this credit the day-total reconcile below would fail
                // every resumed day.
                let credit = PartCredit::skipped(credited);
                delivered += credit.delivered;
                (callbacks.on_part_skipped)(&part.key());
                continue;
            }
            PartStep::RefetchBecauseCountMoved { .. } | PartStep::Walk => {}
        }

        let term = edat_range_term(day_term, part.lo, part.hi);

        let session = match transport.esearch(&term, api_key, true) {
            Ok(session) => session,
            Err(e) => {
                // The type, like every other handler here: `str()` of a bare
                // transport error is empty, so without it this day fails on every
                // later run reporting `part edat:a:b: ` and no cause at all.
                return PubMedResult::failed(processed, format!("part {}: {e}", part.key()));
            }
        };

        if session.count > EFETCH_MAX_RETRIEVABLE {
            // It grew between planning and fetching. Split it again rather than
            // walk it: the last page of an over-cap session is silently clamped,
            // so walking would look like an ordinary short day. `known_count`
            // drives the re-plan off the count that triggered it rather than a
            // fresh recount, which is what guarantees the descent narrows
            // instead of handing back the same range for ever.
            match plan_partitions(
                &mut count_fn,
                day_term,
                session.count,
                part.lo,
                part.hi,
                true,
                Some(session.count),
            ) {
                Ok(children) => {
                    for child in children.into_iter().rev() {
                        pending.push_front(child);
                    }
                }
                Err(e) => return PubMedResult::failed(processed, e.to_string()),
            }
            continue;
        }

        // Planning measured this range at `part.promised`; the part's own ESearch
        // has just reported `session.count`. Two of bmlib's own measurements, and
        // the weaker one does not get to decide.
        let plan_verdict = reconcile_delivery(
            "pubmed",
            &format!(
                "{date_str} part {} (its count when its session opened)",
                part.key()
            ),
            session.count,
            Some(part.promised),
            false,
        );
        if let Some(failure) = plan_verdict.failure {
            return PubMedResult::failed(processed, failure);
        }
        let plan_noted = plan_verdict.note.is_some();
        if let Some(note) = plan_verdict.note {
            notes.push(note);
        }

        let (Some(web_env), Some(query_key)) =
            (session.web_env.as_deref(), session.query_key.as_deref())
        else {
            return PubMedResult::failed(
                processed,
                format!(
                    "part {} returned count={} without a history session",
                    part.key(),
                    session.count
                ),
            );
        };

        let before = processed;
        let part_key = part.key();
        let outcome = walk_session(
            session.count,
            |retstart| transport.efetch(web_env, query_key, retstart, api_key),
            on_record,
            &mut |part_processed| {
                (callbacks.on_progress)(before + part_processed, day_count, &part_key);
            },
        );
        processed += outcome.processed;
        delivered += outcome.delivered;

        if let Some(error) = outcome.error {
            return PubMedResult::failed(processed, format!("part {}: {error}", part.key()));
        }

        let verdict = reconcile_delivery(
            "pubmed",
            &format!("{date_str} part {}", part.key()),
            outcome.delivered,
            Some(session.count),
            outcome.stalled,
        );
        if let Some(failure) = verdict.failure {
            return PubMedResult::failed(processed, failure);
        }
        let walk_noted = verdict.note.is_some();
        if let Some(note) = verdict.note {
            notes.push(note);
        }

        // Every part that reached here is reported, so its records leave the
        // caller's buffer; **only a part that reconciled with no note carries a
        // checkpoint**. Two rules, and they must not be collapsed back into one.
        let checkpoint = if may_checkpoint(plan_noted, walk_noted) {
            Some(PartCheckpoint {
                part_scheme: PART_SCHEME.to_string(),
                part_key: part.key(),
                promised: session.count,
                record_count: outcome.processed,
            })
        } else {
            None
        };
        (callbacks.on_part_finished)(checkpoint);
    }

    let day_verdict = reconcile_delivery("pubmed", &date_str, delivered, Some(day_count), false);
    if let Some(failure) = day_verdict.failure {
        return PubMedResult::failed(processed, failure);
    }
    if let Some(note) = day_verdict.note {
        notes.push(note);
    }

    PubMedResult::completed(processed, notes)
}

/// How a day-level ESearch's count was handled before walking.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DayStep {
    /// A quiet day: no records, and nothing was checkpointed. Complete it.
    QuietDay,
    /// No records reported, but an earlier run checkpointed parts. **Refuse** —
    /// see [`DayStep::RefusedForCheckpoints`].
    CheckpointedButEmpty {
        /// How many parts the earlier run checkpointed.
        parts: usize,
        /// How many records those checkpoints attest to.
        records: i64,
    },
    /// Over the cap: fetch it as parts.
    Partitioned,
    /// Under the cap with a history session: walk it as one session.
    SingleSession,
    /// A count but no session, which would walk the whole count in useless
    /// requests and report `completed` with nothing.
    NoSession,
}

impl DayStep {
    /// Whether the day must be refused before walking.
    #[must_use]
    pub fn is_refusal(&self) -> bool {
        matches!(
            self,
            DayStep::CheckpointedButEmpty { .. } | DayStep::NoSession
        )
    }
}

/// Decide how to fetch a day, from its ESearch result.
///
/// The three branches are ordered, and **the order is load-bearing**: the
/// over-cap branch sits ahead of the session guard, because the session opened
/// at day level is unused on that path — `fetch_partitioned` opens one per part —
/// so a day-level search reporting a count without a `WebEnv` is no obstacle to
/// fetching it, and refusing would lose a fetchable day to a re-offer on every
/// later run.
#[must_use]
pub fn day_step(count: i64, has_session: bool, checkpointed_parts: usize) -> DayStep {
    if count == 0 {
        if checkpointed_parts > 0 {
            return DayStep::CheckpointedButEmpty {
                parts: checkpointed_parts,
                records: 0,
            };
        }
        return DayStep::QuietDay;
    }
    if count > EFETCH_MAX_RETRIEVABLE {
        return DayStep::Partitioned;
    }
    if !has_session {
        return DayStep::NoSession;
    }
    DayStep::SingleSession
}

/// The message for a day that reports no records while parts are checkpointed.
///
/// Two of bmlib's own counts, and this pair is the widest of them: an earlier run
/// walked, stored and checkpointed these parts, and the day now claims to hold
/// nothing at all. Completing on the weaker one is worse here than at part level,
/// because `sync` drops this day's part rows the moment it completes — so the
/// same transaction that loses the records destroys the checkpoints that would
/// have made re-fetching them cheap.
///
/// A day genuinely emptying between two runs is not a thing PubMed does; a soft
/// zero under load is, which is exactly why the part level refuses one.
#[must_use]
pub fn checkpointed_but_empty_message(date_str: &str, parts: usize, records: i64) -> String {
    format!(
        "PubMed reports 0 records for {date_str}, but {parts} part(s) of this day were \
         checkpointed by an earlier run ({records} records); refusing to record it complete \
         on the weaker of two of our own counts. Delete this day's download_day_parts rows \
         if the day really is empty"
    )
}

/// The callbacks and resume state a day's fetch takes.
pub struct DayCallbacks<'a> {
    /// Called with every parsed record, **before** it is stored.
    ///
    /// A callback, not a returned `Vec`: the caller stores per part so a write
    /// transaction never spans the network I/O, and a 242,216-record day is not
    /// held in memory to do it.
    pub on_record: &'a mut dyn FnMut(FetchedRecord),
    /// Called with the running processed count after each page.
    pub on_progress: &'a mut dyn FnMut(i64, i64, &str),
    /// Parts a previous run finished, keyed by part key. Only consulted for a
    /// day large enough to be partitioned.
    pub completed_parts: &'a std::collections::BTreeMap<String, PartCheckpoint>,
    /// Called for every part that finished without failing.
    pub on_part_finished: &'a mut dyn FnMut(Option<PartCheckpoint>),
    /// Called with the key of every part skipped.
    pub on_part_skipped: &'a mut dyn FnMut(&str),
}

/// Fetch all PubMed articles published on one day.
///
/// The three arms, in the order [`day_step`] decides them: a quiet day, a day
/// that must be refused, a day fetched as Entrez-date parts, or a day walked as
/// one history session.
#[must_use]
pub fn fetch_pubmed(
    transport: &dyn Eutils,
    target_date: NaiveDate,
    api_key: Option<&str>,
    callbacks: &mut DayCallbacks<'_>,
) -> PubMedResult {
    let date_str = target_date.format("%Y-%m-%d").to_string();
    let day_term = day_term(target_date);

    let day = match transport.esearch(&day_term, api_key, true) {
        Ok(result) => result,
        Err(e) => {
            // A bare `ReadTimeout` or `ConnectError` stringifies to the empty
            // string, and `sync` records the error verbatim.
            return PubMedResult::failed(0, e);
        }
    };

    let checkpointed = callbacks.completed_parts.len();
    match day_step(
        day.count,
        day.web_env.is_some() && day.query_key.is_some(),
        checkpointed,
    ) {
        DayStep::QuietDay => return PubMedResult::completed(0, Vec::new()),
        DayStep::CheckpointedButEmpty { parts, .. } => {
            // Two of bmlib's own counts, and this pair is the widest of them.
            // The records the checkpoints attest to are reported by the caller,
            // which holds the map.
            let records: i64 = callbacks.completed_parts.values().map(|c| c.promised).sum();
            return PubMedResult::failed(
                0,
                checkpointed_but_empty_message(&date_str, parts, records),
            );
        }
        DayStep::Partitioned => {
            // Ahead of the session guard, and deliberately: the session opened
            // here is unused on this path, since each part opens its own.
            let request = PartitionRequest {
                target_date,
                day_term: &day_term,
                day_count: day.count,
                api_key,
                completed_parts: callbacks.completed_parts,
                root_lo: edat_root_lo(),
                root_hi: edat_root_hi(),
            };
            let mut part_callbacks = PartCallbacks {
                on_part_finished: callbacks.on_part_finished,
                on_part_skipped: callbacks.on_part_skipped,
                on_progress: callbacks.on_progress,
            };
            return fetch_partitioned(
                transport,
                &request,
                &mut part_callbacks,
                callbacks.on_record,
            );
        }
        DayStep::NoSession => {
            return PubMedResult::failed(
                0,
                format!(
                    "esearch returned count={} without a history session (WebEnv/QueryKey)",
                    day.count
                ),
            );
        }
        DayStep::SingleSession => {}
    }

    let (Some(web_env), Some(query_key)) = (day.web_env.as_deref(), day.query_key.as_deref())
    else {
        // Unreachable: `day_step` answered `SingleSession` only with a session.
        return PubMedResult::failed(0, "esearch returned no history session".to_string());
    };

    let count = day.count;
    // Reborrowed rather than moved: `callbacks` is still borrowed for its other
    // fields above, and a `&mut dyn` is not `Copy`.
    let on_record = &mut *callbacks.on_record;
    let on_progress = &mut *callbacks.on_progress;
    let mut progress = |processed: i64| {
        on_progress(
            processed,
            count,
            &format!("Fetched {processed}/{count} records"),
        );
    };
    let outcome = walk_session(
        count,
        |retstart| transport.efetch(web_env, query_key, retstart, api_key),
        on_record,
        &mut progress,
    );

    if let Some(error) = outcome.error {
        return PubMedResult::failed(outcome.processed, error);
    }

    let verdict = reconcile_delivery(
        "pubmed",
        &date_str,
        outcome.delivered,
        Some(count),
        outcome.stalled,
    );
    if let Some(failure) = verdict.failure {
        return PubMedResult::failed(outcome.processed, failure);
    }

    PubMedResult {
        processed: outcome.processed,
        status: "completed".to_string(),
        error: None,
        note: verdict.note,
    }
}
