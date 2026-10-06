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

//! The JATS XML reader — Python's `_JATSHandler` and `JATSParser`.
//!
//! A JATS document becomes a [`JATSArticle`]. The rules that decide which
//! element's text reaches which field, and which of the two dozen stacks a
//! closing element pops, are the specification this module ports; where a rule
//! looks wrong it is reproduced and a `QUIRK:` comment says so.
//!
//! # Option (b): the stack machine, driven by a DOM walk
//!
//! Python's reader is an `xml.sax` content handler: `startElement`,
//! `characters` and `endElement` mutate a stack machine, and **the routing
//! logic is the stack discipline** — `element_stack[-2]` names a `<label>`'s
//! owner, `caption_stack[-1]` decides whether a `<p>` is caption text, and a
//! formula emits its chosen encoding at its own end tag. A recursive walk that
//! re-expressed those decisions as functions over DOM nodes would have to
//! reconstruct the very stacks it removed, and every "which element is closing
//! now" question would become an ancestry query with a different shape.
//!
//! So this port walks the `roxmltree` DOM in document order and emits
//! `start_element` / `characters` / `end_element` events into the **same**
//! stack machine, one text event per text node. `roxmltree` builds a tree where
//! Python's expat streams, and one difference follows from that: a SAX parser
//! may split a text node into several `characters` calls while a DOM walk emits
//! one. Nothing in the reader observes the difference — every consumer
//! concatenates (`text_stack`, `current_cell_text`) — so the two are
//! behaviourally identical.
//!
//! # What is reproduced verbatim
//!
//! * Offsets and lengths are **character** counts, not bytes, wherever the
//!   Python slices (`describe_article` truncates a title at 60 characters).
//! * A malformed document is an error ([`JatsError::Xml`]), as expat raising
//!   through `JATSParser.parse` is. The end-of-parse unwind audit does **not**
//!   raise: it reports.
//! * `roxmltree` is given `allow_dtd: true`, because a JATS deposit carries a
//!   DOCTYPE and Python's expat reads one. Its default refuses any document
//!   with a DTD, which would reject the corpus's own fixtures.
//! * `roxmltree` resolves namespace prefixes and Python's expat does not, so a
//!   document using `xlink:href` **without declaring `xlink`** — which expat
//!   reads as an ordinary attribute name — is patched and retried rather than
//!   refused. See `walk_document`; this was measured, not assumed: over the
//!   409 documents of `tests/test_jats_parser.py` that Python parses, the port
//!   agrees on every field of every one once the declaration is present, and
//!   64 of them are this shape.
//!
//! # One known, deliberate difference
//!
//! **Elements are matched by their local name, where Python matches the raw
//! qualified name.** `xml.sax.make_parser()` does not process namespaces, so
//! Python sees `<jats:sec>` as the name `"jats:sec"` and matches none of its
//! sets — it reads such a document as empty. This port matches `"sec"` and
//! reads it. That direction is the better one and no committed corpus
//! exercises it (the differential above would have shown it), but it is a
//! difference and not an equivalence: where Python drops a prefixed JATS
//! document's content, this returns it.
//!
//! The reverse mapping — recomputing the qualified name from
//! `Node::lookup_prefix` — is **not** taken, because it is only an
//! approximation of the prefix the source actually used (one URI may be bound
//! to several) and would introduce differences of its own.

use roxmltree::{Document, Node, NodeType, ParsingOptions};

use crate::fulltext::jats_text::{
    elocation_part_continues, latex_expression, normalize_whitespace, pad_as_deposited,
    render_formula,
};
use crate::fulltext::models::{
    JATSAbstractSection, JATSArticle, JATSAuthorInfo, JATSBodySection, JATSFigureInfo,
    JATSFundingAward, JATSFundingSource, JATSReferenceInfo, JATSTableInfo,
};
use crate::fulltext::parse_audit::{unwind_diagnostics, ParseUnwindState};

/// The XLink namespace URI, for `xlink:href`.
const XLINK_NS: &str = "http://www.w3.org/1999/xlink";

/// The URI bound to an undeclared prefix that is not `xlink`, when one is
/// spliced in so `roxmltree` will read a document expat accepts. Nothing the
/// reader matches lives in it.
const UNDECLARED_PREFIX_NAMESPACE: &str = "urn:x-bmlib:undeclared-prefix";

/// How many undeclared prefixes one document may carry before the splice give
/// up and the parse is refused.
const MAX_UNDECLARED_PREFIXES: usize = 8;

/// The widest `colspan` this reader will honour (`_MAX_COLSPAN`).
const MAX_COLSPAN: i128 = 1000;

/// The string a `<def-list>` term is folded into its definition with.
const DEFINITION_SEPARATOR: &str = " — ";

/// Elements that push a text buffer when they open.
const TEXT_ACCUMULATING: &[&str] = &[
    "p",
    "title",
    "article-title",
    "abstract",
    "sec",
    "surname",
    "given-names",
    "journal-title",
    "volume",
    "issue",
    "fpage",
    "lpage",
    "elocation-id",
    "year",
    "article-id",
    "label",
    "mixed-citation",
    "element-citation",
    // NLM 2.x and 3.0's spellings of the two above (issue #390).
    "citation",
    "nlm-citation",
    "caption",
    "bold",
    "b",
    "italic",
    "i",
    "sub",
    "sup",
    "monospace",
    "code",
    "xref",
    "ext-link",
    "uri",
    "email",
    "named-content",
    "list-item",
    "def",
    "term",
    "kwd",
    "alt-title",
    "inline-formula",
    "disp-formula",
    "tex-math",
    "source",
    "person-group",
    "pub-id",
    "collab",
    "string-name",
    "td",
    "th",
    "alt-text",
    "long-desc",
    "object-id",
    "permissions",
    "attrib",
    "funding-statement",
    "funding-source",
    "support-source",
    "award-id",
    "institution-id",
];

/// Elements whose buffer merges into the enclosing one when they close.
const INLINE_ELEMENTS: &[&str] = &[
    "bold",
    "b",
    "italic",
    "i",
    "sub",
    "sup",
    "monospace",
    "code",
    "xref",
    "ext-link",
    "uri",
    "email",
    "named-content",
    "inline-formula",
    "collab",
    "string-name",
    "elocation-id",
    "funding-source",
    "support-source",
    "award-id",
];

/// `<sub-article>` / `<response>`: a nested article, skipped whole.
const NESTED_ARTICLE_ELEMENTS: &[&str] = &["sub-article", "response"];

/// Wrappers that do not take ownership of a `<graphic>`.
const GRAPHIC_TRANSPARENT_WRAPPERS: &[&str] = &["alternatives", "p"];

/// The mixed-content citation spellings: JATS's `<mixed-citation>` and NLM
/// 2.x's `<citation>`, whose descendants are the citation's text.
const MIXED_CONTENT_CITATIONS: &[&str] = &["mixed-citation", "citation"];

/// The four citation spellings a `<ref>` may carry, by content model.
///
/// Mixed content: `<mixed-citation>` and NLM 2.x's `<citation>`. Element-only:
/// `<element-citation>` and `<nlm-citation>`, which author no string.
const CITATION_ELEMENTS: &[&str] = &[
    "mixed-citation",
    "citation",
    "element-citation",
    "nlm-citation",
];

/// The containers that spell one contributor's name several ways.
///
/// `<name-alternatives>` and `<collab-alternatives>` both hold one name — a
/// person's, or a consortium's in two languages — so their members are counted
/// by the container rather than again.
const ALTERNATIVE_NAME_CONTAINERS: &[&str] = &["name-alternatives", "collab-alternatives"];

/// Every element that names one contributor, for the zero-author detector.
const CONTRIBUTOR_NAME_SPELLINGS: &[&str] = &[
    "name",
    "string-name",
    "collab",
    "on-behalf-of",
    "name-alternatives",
    "collab-alternatives",
];

/// Elements that describe **another** work in place.
///
/// JATS 1.3 admits `<related-article>` and `<related-object>` inside a
/// citation, inside a `<p>` and inside an `<article-title>`, and `<product>` in
/// `<article-meta>`, and each holds `<article-title>`, `<source>`, `<year>`,
/// `<volume>`, `<fpage>` and the rest under the same names the enclosing work
/// uses. Two rules follow, and they are one claim read twice. **A related
/// work's parts are never a field of the work around it** — a reference's
/// volume is not its erratum's (issue #270). **And they are its text**, so each
/// merges back into whatever buffer the related work sits in, exactly as the
/// related work's own untagged characters already did (#267, #271).
const RELATED_WORK_ELEMENTS: &[&str] = &["related-article", "related-object", "product"];

/// What a `<contrib>` holds *about* its contributor rather than naming them.
///
/// A biography and an author comment, each of `<p>`. A name printed there is
/// prose, not the contributor's name (issue #258). `<p>` is listed beside its
/// two containers so a paragraph the model does not place is covered by the
/// same answer.
const CONTRIBUTOR_PROSE: &[&str] = &["bio", "author-comment", "p"];

/// Elements that claim their descendants' text.
const TEXT_CLAIMING_ELEMENTS: &[&str] = &["xref", "mixed-citation", "citation"];

/// An object's metadata, declined as prose.
const NON_PROSE_METADATA: &[&str] = &["alt-text", "long-desc", "object-id", "permissions"];

/// Declined metadata that still merges into an enclosing claimer.
const CLAIMABLE_ELEMENTS: &[&str] = &[
    "alt-text",
    "long-desc",
    "object-id",
    "permissions",
    "attrib",
];

/// Elements that name a contributor without dividing the name.
const UNDIVIDED_NAME_ELEMENTS: &[&str] = &["collab", "string-name"];

/// A `<graphic>`'s print-master mime subtypes.
const ARCHIVAL_MIME_SUBTYPES: &[&str] = &["tiff", "tif", "eps", "postscript"];

/// The same masters as file extensions.
const ARCHIVAL_EXTENSIONS: &[&str] = &[".tif", ".tiff", ".eps", ".ps"];

/// Funder `<named-content content-type>` values that name a registry id.
const FUNDER_IDENTIFIER_CONTENT_TYPES: &[&str] = &[
    "funder_identifier",
    "funder_id",
    "funder_doi",
    "funder_ror",
    "fundref_id",
    "doi",
    "project_funder_id",
    "project_funder_doi",
];

/// The funder spellings of an `<award-group>`.
const AWARD_FUNDER_ELEMENTS: &[&str] = &["funding-source", "support-source"];

/// Containers whose prose is an exhibit's own footnote matter.
const EXHIBIT_FOOTNOTE_CONTAINERS: &[&str] = &["table-wrap-foot", "fn", "fn-group"];

/// The subset of those that are a *block* — a heading there is dropped.
const EXHIBIT_FOOTNOTE_BLOCKS: &[&str] = &["table-wrap-foot", "fn-group"];

/// Table cell elements.
const TABLE_CELL_ELEMENTS: &[&str] = &["td", "th"];

/// The formula elements.
const FORMULA_ELEMENTS: &[&str] = &["inline-formula", "disp-formula"];

/// Formula elements plus the LaTeX holder: none of these may merge.
const FORMULA_PARTS: &[&str] = &["inline-formula", "disp-formula", "tex-math"];

/// Parents a display formula merges into rather than standing alone.
const DISPLAY_FORMULA_MERGE_PARENTS: &[&str] = &[
    "bold",
    "b",
    "italic",
    "i",
    "sub",
    "sup",
    "monospace",
    "code",
    "xref",
    "ext-link",
    "uri",
    "email",
    "named-content",
    "inline-formula",
    "collab",
    "string-name",
    "elocation-id",
    "funding-source",
    "support-source",
    "award-id",
    "p",
    "td",
    "th",
];

/// The article's own `<article-meta>` path.
const ARTICLE_META: &[&str] = &["front", "article-meta"];

/// The article's own `<journal-meta>` path.
const JOURNAL_META: &[&str] = &["front", "journal-meta"];

/// Wrappers a `<title-group>` article title may sit in.
const TITLE_WRAPPERS: &[&[&str]] = &[&["title-group"]];

/// Wrappers a `<year>` may sit in.
const YEAR_WRAPPERS: &[&[&str]] = &[&["pub-date"], &["pub-date", "string-date"]];

/// Wrappers a `<volume>`/`<issue>` may sit in.
const VOLUME_ISSUE_WRAPPERS: &[&[&str]] = &[&["volume-issue-group"]];

/// Wrappers a `<journal-title>` may sit in.
const JOURNAL_TITLE_WRAPPERS: &[&[&str]] = &[&["journal-title-group"]];

/// Wrappers a `<funding-statement>` may sit in.
const FUNDING_WRAPPERS: &[&[&str]] = &[&["funding-group"], &["support-group", "funding-group"]];

/// Wrappers an `<award-group>` may sit in.
const FUNDING_AWARD_WRAPPERS: &[&[&str]] = &[
    &["funding-group"],
    &["support-group", "funding-group"],
    &["support-group", "contributed-resource-group"],
];

/// `<pub-date>` type suffixes that name no publication.
const NON_PUBLICATION_DATE_SUFFIXES: &[&str] = &["-submitted", "-release"];

/// Every spelling of a contributor name counted inside `<front>`.
///
/// Used by the zero-author detector's tally only; see
/// [`JatsReport::warnings`].
const ROUTING_FLAG_NAMES: &[&str] = &[
    "in_front",
    "in_abstract",
    "in_body",
    "in_back",
    "in_ref_list",
    "in_ref",
    "in_ref_citation",
    "in_ref_person_group",
    "current_reference",
    "current_article_id_type",
    "current_pub_date_type",
    "current_pub_id_type",
    "current_xref_type",
    "current_xref_rid",
    "implicit_body_section",
    "implicit_back_section",
    "implicit_front_section",
];

/// Everything that can go wrong before a single field is stored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JatsError {
    /// The bytes are not a well-formed XML document.
    ///
    /// This is Python's expat raising through `JATSParser.parse`, and it is
    /// deliberately fatal: a document expat would reject is not a partial
    /// article.
    Xml(String),
}

impl std::fmt::Display for JatsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JatsError::Xml(message) => write!(f, "malformed JATS XML: {message}"),
        }
    }
}

impl std::error::Error for JatsError {}

/// A parsed article together with what the parse left behind.
///
/// The end-of-parse audit is a net over the **reader**, not over the
/// document: no well-formed document can make a non-empty [`diagnostics`]
/// (a conforming XML parser rejects an unbalanced document first), so every
/// entry is a claim that this port is wrong. They are logged at ERROR by
/// [`parse`] and [`parse_with_pmc_id`].
///
/// [`diagnostics`]: JatsReport::diagnostics
#[derive(Debug, Clone, PartialEq)]
pub struct JatsReport {
    /// The article the reader produced.
    pub article: JATSArticle,
    /// The routing state the parse ended with.
    pub unwind: ParseUnwindState,
    /// How the article identifies itself in a diagnostic line.
    pub article_label: String,
    /// The unwind audit's messages, one per imbalance. Empty on a clean parse.
    pub diagnostics: Vec<String>,
    /// The counted losses that are a publisher's deposit rather than a defect
    /// in the reader: refused colspans, contributors naming nobody, formulas
    /// that reached nowhere, and the rest. Empty on a clean parse.
    pub warnings: Vec<String>,
}

/// The full name Python's `JATSAuthorInfo.full_name` property derives.
///
/// A structured name wins over both undivided forms, because a `<contrib>`
/// carrying a `<name>` *and* a `<collab>` is *"Smith, on behalf of the Y
/// Group"* — the person is the contributor and the collaboration is an
/// attribution attached to them. The order between the two undivided forms is
/// arbitrary, fixed only so the rule is deterministic.
#[must_use]
pub fn author_full_name(author: &JATSAuthorInfo) -> String {
    if !author.surname.is_empty() || !author.given_names.is_empty() {
        format!("{} {}", author.given_names, author.surname)
            .trim()
            .to_string()
    } else if !author.collab.is_empty() {
        author.collab.clone()
    } else {
        author.string_name.clone()
    }
}

/// Did any spelling of a name arrive? Python's `JATSAuthorInfo.is_named`.
///
/// Reads through [`author_full_name`], so a field holding only whitespace
/// counts as unnamed.
#[must_use]
pub fn author_is_named(author: &JATSAuthorInfo) -> bool {
    !author_full_name(author).trim().is_empty()
}

/// Parse a JATS document with no known PMC id.
///
/// # Errors
///
/// [`JatsError::Xml`] where the document is not well-formed.
pub fn parse(xml: &str) -> Result<JATSArticle, JatsError> {
    parse_with_pmc_id(xml, "")
}

/// Parse a JATS document, seeding the PMC id the caller already knows.
///
/// Python's `JATSParser(data, known_pmc_id=...)`: a bare numeric id is
/// prefixed with `PMC`, and a typed `<article-id pub-id-type="pmc">` does not
/// replace it (first wins).
///
/// # Errors
///
/// [`JatsError::Xml`] where the document is not well-formed.
pub fn parse_with_pmc_id(xml: &str, known_pmc_id: &str) -> Result<JATSArticle, JatsError> {
    Ok(parse_audited(xml, known_pmc_id)?.article)
}

/// Parse a JATS document and return the article **and** the audit.
///
/// The two `parse` functions are this one with the audit logged and dropped.
/// Every diagnostic is written to stderr at ERROR and every counted loss at
/// WARNING — the port links no logging facade, so stderr is the level's
/// mapping; a caller wanting the lines rather than the side effect reads them
/// from the returned [`JatsReport`].
///
/// # Errors
///
/// [`JatsError::Xml`] where the document is not well-formed.
pub fn parse_audited(xml: &str, known_pmc_id: &str) -> Result<JatsReport, JatsError> {
    let mut handler = Handler::new(known_pmc_id);
    walk_document(xml, &mut handler)?;

    let report = handler.finish();
    for message in &report.diagnostics {
        eprintln!("ERROR: JATS parse of {}: {}", report.article_label, message);
    }
    for message in &report.warnings {
        eprintln!(
            "WARNING: JATS parse of {}: {}",
            report.article_label, message
        );
    }
    Ok(report)
}

/// Parse `xml` and drive `handler` over its DOM.
///
/// **One deliberate leniency, and it is expat's.** Python reads the document
/// with `xml.sax.make_parser()`, whose namespace processing is *off*: a plain
/// `xlink:href` is an ordinary attribute name and an undeclared prefix is not
/// an error at all. `roxmltree` resolves names and refuses the same document.
/// So a parse that fails only because a prefix is undeclared gets the missing
/// `xmlns:` declaration spliced into the root start tag and is tried again, up
/// to [`MAX_UNDECLARED_PREFIXES`] times. That prefix is `xlink` in every
/// document this is about — a fragment deposited without the declaration the
/// whole-document rendition carries — and the real XLink URI is bound for it so
/// [`Attrs::get`] finds the href exactly as Python's qualified-name lookup
/// does. Any other prefix is bound to a synthetic URI; nothing the reader
/// matches is namespaced except `xlink:href`.
///
/// A document that fails for any other reason is refused as it always was.
fn walk_document(xml: &str, handler: &mut Handler) -> Result<(), JatsError> {
    let mut source = std::borrow::Cow::Borrowed(xml);
    let mut budget = MAX_UNDECLARED_PREFIXES;
    loop {
        let options = ParsingOptions {
            allow_dtd: true,
            ..ParsingOptions::default()
        };
        match Document::parse_with_options(source.as_ref(), options) {
            Ok(document) => {
                handler.walk(document.root_element());
                return Ok(());
            }
            Err(error) => {
                let message = error.to_string();
                let Some(prefix) = unknown_namespace_prefix(&message) else {
                    return Err(JatsError::Xml(message));
                };
                if budget == 0 {
                    return Err(JatsError::Xml(message));
                }
                let Some(patched) = declare_prefix(source.as_ref(), &prefix) else {
                    return Err(JatsError::Xml(message));
                };
                source = std::borrow::Cow::Owned(patched);
                budget -= 1;
            }
        }
    }
}

/// The prefix `roxmltree` named in an unknown-namespace error message.
fn unknown_namespace_prefix(message: &str) -> Option<String> {
    let marker = "an unknown namespace prefix '";
    let start = message.find(marker)? + marker.len();
    let end = message[start..].find('\'')? + start;
    Some(message[start..end].to_string())
}

/// Splice `xmlns:prefix` into the root element's start tag.
fn declare_prefix(xml: &str, prefix: &str) -> Option<String> {
    let uri = if prefix == "xlink" {
        XLINK_NS
    } else {
        UNDECLARED_PREFIX_NAMESPACE
    };
    let at = root_tag_insert_position(xml)?;
    let mut out = String::with_capacity(xml.len() + prefix.len() + uri.len() + 10);
    out.push_str(&xml[..at]);
    out.push_str(&format!(" xmlns:{prefix}=\"{uri}\""));
    out.push_str(&xml[at..]);
    Some(out)
}

/// The byte offset just after the root element's name, where an attribute may
/// be spliced in.
///
/// Skips the shapes that may precede the root: the XML declaration, comments
/// (which may contain `>`), and a DOCTYPE, whose internal subset may contain
/// both `>` and quoted strings.
fn root_tag_insert_position(xml: &str) -> Option<usize> {
    let bytes = xml.as_bytes();
    let mut i = 0;
    loop {
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= bytes.len() || bytes[i] != b'<' || i + 1 >= bytes.len() {
            return None;
        }
        if xml[i..].starts_with("<?") {
            i = xml[i..].find("?>")? + i + 2;
            continue;
        }
        if xml[i..].starts_with("<!--") {
            i = xml[i..].find("-->")? + i + 3;
            continue;
        }
        if xml[i..].starts_with("<!DOCTYPE") {
            let mut depth = 0i32;
            let mut quote: Option<u8> = None;
            let mut j = i + "<!DOCTYPE".len();
            while j < bytes.len() {
                let byte = bytes[j];
                if let Some(open) = quote {
                    if byte == open {
                        quote = None;
                    }
                } else if byte == b'"' || byte == b'\'' {
                    quote = Some(byte);
                } else if byte == b'[' {
                    depth += 1;
                } else if byte == b']' {
                    depth -= 1;
                } else if byte == b'>' && depth == 0 {
                    j += 1;
                    break;
                }
                j += 1;
            }
            i = j;
            continue;
        }
        if xml[i..].starts_with("<!") {
            i = xml[i..].find('>')? + i + 1;
            continue;
        }
        let mut j = i + 1;
        while j < bytes.len()
            && !bytes[j].is_ascii_whitespace()
            && bytes[j] != b'>'
            && bytes[j] != b'/'
        {
            j += 1;
        }
        return Some(j);
    }
}

// ---------------------------------------------------------------------------
// Element attributes
// ---------------------------------------------------------------------------

/// The attribute lookup Python's SAX handler is handed.
///
/// `xml.sax` without namespace processing reports an attribute by its **raw
/// qualified name**, so `attrs.get("xlink:href")` matches the literal prefix
/// `xlink` and nothing else. `roxmltree` resolves names, so the prefix is
/// recovered from the in-scope namespace binding to reproduce that: an
/// `xlink:href` written with any other prefix is *not* found here, exactly as
/// it is not in Python. A name without a colon is matched only when the
/// attribute is unnamespaced — a `foo:content-type` is not `content-type`.
struct Attrs<'a, 'input> {
    node: Node<'a, 'input>,
}

impl<'a, 'input> Attrs<'a, 'input> {
    /// The attribute's value, or `None` where the document does not carry it.
    fn get(&self, name: &str) -> Option<&'a str> {
        if let Some(local) = name.strip_prefix("xlink:") {
            if self.node.lookup_prefix(XLINK_NS) == Some("xlink") {
                return self
                    .node
                    .attributes()
                    .find(|a| a.namespace() == Some(XLINK_NS) && a.name() == local)
                    .map(|attribute| attribute.value());
            }
            return None;
        }
        self.node
            .attributes()
            .find(|a| a.namespace().is_none() && a.name() == name)
            .map(|attribute| attribute.value())
    }
}

// ---------------------------------------------------------------------------
// Builders
// ---------------------------------------------------------------------------

/// One `<contrib>` being read, in whichever spelling it names its contributor.
#[derive(Debug, Clone, Default)]
struct AuthorBuilder {
    surname: String,
    given_names: String,
    affiliations: Vec<String>,
    collab: String,
    string_name: String,
}

impl AuthorBuilder {
    /// The contributor, or `None` where the `<contrib>` named nobody.
    ///
    /// `None` means what it says — no spelling of a name arrived — rather than
    /// "no `<surname>`", which was true of every collaboration before issue
    /// #120.
    fn build(&self) -> Option<JATSAuthorInfo> {
        let info = JATSAuthorInfo {
            surname: self.surname.clone(),
            given_names: self.given_names.clone(),
            affiliations: self.affiliations.clone(),
            collab: self.collab.clone(),
            string_name: self.string_name.clone(),
        };
        if author_is_named(&info) {
            Some(info)
        } else {
            None
        }
    }
}

/// How well a `<graphic>` deposit serves as *the* image of its figure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum GraphicSuitability {
    /// A print master no browser renders: TIFF, EPS, PostScript.
    Archival = 1,
    /// A reduced preview. Renders, but is not the figure.
    Thumbnail = 2,
    /// Everything else — the ordinary case, and the one to keep.
    Full = 3,
}

/// Rank one `<graphic>` deposit by how well it serves as the figure.
fn graphic_suitability(attrs: &Attrs<'_, '_>, href: &str) -> GraphicSuitability {
    let content_type = attrs.get("content-type").unwrap_or("").to_lowercase();
    let specific_use = attrs.get("specific-use").unwrap_or("").to_lowercase();
    if content_type.contains("thumb") || specific_use.contains("thumb") {
        // QUIRK: a deposit marked *both* — a TIFF thumbnail — is ranked
        // `Thumbnail` rather than `Archival`, because this predicate is tested
        // first. Neither ranking serves it: the deposit is a TIFF either way,
        // so neither describes something a browser can show. Python records
        // the order as its reference implementation's rather than as measured,
        // and no test pins it; it is reproduced so the two agree.
        return GraphicSuitability::Thumbnail;
    }
    if ARCHIVAL_MIME_SUBTYPES.contains(
        &attrs
            .get("mime-subtype")
            .unwrap_or("")
            .to_lowercase()
            .as_str(),
    ) {
        return GraphicSuitability::Archival;
    }
    if has_archival_extension(href) {
        return GraphicSuitability::Archival;
    }
    GraphicSuitability::Full
}

/// Does `href` name a print master by its file extension?
fn has_archival_extension(href: &str) -> bool {
    let path = href
        .split('?')
        .next()
        .unwrap_or("")
        .split('#')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    ARCHIVAL_EXTENSIONS
        .iter()
        .any(|extension| path.ends_with(extension))
}

/// The half of an exhibit builder that chooses among `<graphic>` deposits.
#[derive(Debug, Clone, Default)]
struct GraphicHolder {
    href: String,
    rank: Option<GraphicSuitability>,
}

impl GraphicHolder {
    /// Keep `href` only if it is a strictly better deposit than the one held.
    fn offer(&mut self, href: &str, rank: GraphicSuitability) {
        if href.is_empty() {
            return;
        }
        if self.rank.is_none() || rank > self.rank.unwrap() {
            self.href = href.to_string();
            self.rank = Some(rank);
        }
    }
}

/// The half of an exhibit builder that collects its footnotes.
#[derive(Debug, Clone, Default)]
struct FootnoteHolder {
    footnotes: Vec<String>,
    pending_footnote_label: String,
    /// Where the first unmarked credit of the `<fn>` now open was filed, while
    /// a marker was pending — `None` otherwise.
    unmarked_credit_slot: Option<usize>,
}

impl FootnoteHolder {
    /// File `text` as a note of this exhibit, folding in a held marker.
    fn append_footnote(&mut self, text: &str, fold_marker: bool) {
        if text.is_empty() {
            return;
        }
        let mut text = text.to_string();
        if !self.pending_footnote_label.is_empty() {
            if fold_marker {
                text = format!("{} — {}", self.pending_footnote_label, text);
                self.pending_footnote_label.clear();
                self.unmarked_credit_slot = None;
            } else if self.unmarked_credit_slot.is_none() {
                self.unmarked_credit_slot = Some(self.footnotes.len());
            }
        }
        self.footnotes.push(text);
    }

    /// Hold `marker` for the `<fn>` now open, and say what it displaced.
    fn hold_footnote_label(&mut self, marker: &str) -> String {
        std::mem::replace(&mut self.pending_footnote_label, marker.to_string())
    }

    /// Settle an unspent marker at `</fn>`, and say what was lost.
    fn take_pending_footnote_label(&mut self) -> String {
        let marker = std::mem::take(&mut self.pending_footnote_label);
        let slot = self.unmarked_credit_slot.take();
        if !marker.is_empty() {
            if let Some(slot) = slot {
                self.footnotes[slot] = format!("{} — {}", marker, self.footnotes[slot]);
                return String::new();
            }
        }
        marker
    }
}

/// One open `<fig>`.
#[derive(Debug, Clone, Default)]
struct FigureBuilder {
    graphic: GraphicHolder,
    footnotes: FootnoteHolder,
    id: String,
    label: String,
    caption: String,
}

impl FigureBuilder {
    /// Build the model, dropping an absent graphic to `None`.
    fn build(&self) -> JATSFigureInfo {
        JATSFigureInfo {
            id: self.id.clone(),
            label: self.label.clone(),
            caption: self.caption.clone(),
            graphic_url: if self.graphic.href.is_empty() {
                None
            } else {
                Some(self.graphic.href.clone())
            },
            footnotes: self.footnotes.footnotes.clone(),
        }
    }
}

/// One open `<table-wrap>`, with the row/cell state its HTML needs.
#[derive(Debug, Clone)]
struct TableBuilder {
    graphic: GraphicHolder,
    footnotes: FootnoteHolder,
    id: String,
    label: String,
    caption: String,
    header_rows: Vec<Vec<String>>,
    body_rows: Vec<Vec<String>>,
    current_row: Vec<String>,
    current_cell_text: String,
    in_header: bool,
    in_body: bool,
    in_row: bool,
    in_cell: bool,
    current_row_has_header_cells: bool,
    current_row_cell_count: usize,
    current_row_header_cell_count: usize,
    current_colspan: i128,
}

impl Default for TableBuilder {
    fn default() -> Self {
        Self {
            graphic: GraphicHolder::default(),
            footnotes: FootnoteHolder::default(),
            id: String::new(),
            label: String::new(),
            caption: String::new(),
            header_rows: Vec::new(),
            body_rows: Vec::new(),
            current_row: Vec::new(),
            current_cell_text: String::new(),
            in_header: false,
            in_body: false,
            in_row: false,
            in_cell: false,
            current_row_has_header_cells: false,
            current_row_cell_count: 0,
            current_row_header_cell_count: 0,
            current_colspan: 1,
        }
    }
}

impl TableBuilder {
    fn start_header(&mut self) {
        self.in_header = true;
        self.in_body = false;
    }

    fn end_header(&mut self) {
        self.in_header = false;
    }

    fn start_body(&mut self) {
        self.in_body = true;
        self.in_header = false;
    }

    fn end_body(&mut self) {
        self.in_body = false;
    }

    fn start_row(&mut self) {
        self.in_row = true;
        self.current_row = Vec::new();
        self.current_row_has_header_cells = false;
        self.current_row_cell_count = 0;
        self.current_row_header_cell_count = 0;
    }

    fn end_row(&mut self) {
        if self.in_row && !self.current_row.is_empty() {
            // A row is a header when it is inside an explicit <thead>, or —
            // for tables lacking <thead>/<tbody> wrappers — when it is the
            // first row AND *every* cell is a header cell.
            let all_header_cells = self.current_row_cell_count > 0
                && self.current_row_header_cell_count == self.current_row_cell_count;
            if self.in_header || (all_header_cells && !self.in_body && self.header_rows.is_empty())
            {
                self.header_rows.push(std::mem::take(&mut self.current_row));
            } else {
                self.body_rows.push(std::mem::take(&mut self.current_row));
            }
        }
        self.in_row = false;
        self.current_row = Vec::new();
        self.current_row_has_header_cells = false;
        self.current_row_cell_count = 0;
        self.current_row_header_cell_count = 0;
    }

    fn start_cell(&mut self, is_header: bool, colspan: i128) {
        self.in_cell = true;
        self.current_cell_text = String::new();
        self.current_colspan = colspan.max(1);
        self.current_row_cell_count += 1;
        if is_header || self.in_header {
            self.current_row_has_header_cells = true;
            self.current_row_header_cell_count += 1;
        }
    }

    fn end_cell(&mut self) {
        if self.in_cell {
            let normalized = normalize_whitespace(&self.current_cell_text);
            self.current_row.push(normalized);
            // QUIRK: a `colspan` is written as `span - 1` **empty** cells and
            // the row is truncated to the first row's width when it renders, so
            // a `colspan` wider than the table's frame is silently absorbed
            // rather than widening it — Python's `range(1, colspan)` and
            // `_pad_row`, reproduced.
            for _ in 1..self.current_colspan {
                self.current_row.push(String::new());
            }
        }
        self.in_cell = false;
        self.current_cell_text = String::new();
        self.current_colspan = 1;
    }

    // `#[allow]` is load-bearing and not tidiness: clippy would fold the two
    // ordered replaces into one pass over `['\n', '\r']`, which turns a CRLF
    // into ONE space where Python's two replaces turn it into two.
    #[allow(clippy::collapsible_str_replace)]
    fn append_cell_text(&mut self, text: &str) {
        if self.in_cell {
            // QUIRK: a line break inside a cell becomes a *space* and a CRLF
            // becomes two, because the two replaces run in this order and a
            // `\r\n` is hit twice. Python's `text.replace("\n", " ").replace(
            // "\r", " ")`, reproduced rather than tidied into one pass.
            self.current_cell_text
                .push_str(&text.replace('\n', " ").replace('\r', " "));
        }
    }

    /// Build the model, rendering the markup to HTML.
    fn build(&self) -> JATSTableInfo {
        JATSTableInfo {
            id: self.id.clone(),
            label: self.label.clone(),
            caption: self.caption.clone(),
            html_content: self.build_html_table(),
            graphic_url: if self.graphic.href.is_empty() {
                None
            } else {
                Some(self.graphic.href.clone())
            },
            footnotes: self.footnotes.footnotes.clone(),
        }
    }

    /// The `<table>` HTML, exactly the lines Python's `_build_html_table`
    /// joins.
    fn build_html_table(&self) -> String {
        if self.header_rows.is_empty() && self.body_rows.is_empty() {
            return String::new();
        }
        let col_count = std::cmp::max(
            self.header_rows.first().map_or(0, Vec::len),
            self.body_rows.first().map_or(0, Vec::len),
        );
        if col_count == 0 {
            return String::new();
        }
        let mut parts: Vec<String> = vec!["<table>".to_string()];
        if !self.header_rows.is_empty() {
            parts.push("  <thead>".to_string());
            for row in &self.header_rows {
                parts.push("    <tr>".to_string());
                for cell in pad_row(row.clone(), col_count) {
                    parts.push(format!("      <th>{}</th>", html_escape(&cell)));
                }
                parts.push("    </tr>".to_string());
            }
            parts.push("  </thead>".to_string());
        }
        parts.push("  <tbody>".to_string());
        for row in &self.body_rows {
            parts.push("    <tr>".to_string());
            for cell in pad_row(row.clone(), col_count) {
                parts.push(format!("      <td>{}</td>", html_escape(&cell)));
            }
            parts.push("    </tr>".to_string());
        }
        parts.push("  </tbody>".to_string());
        parts.push("</table>".to_string());
        parts.join("\n")
    }
}

/// One open `<sec>`, collecting its prose and nested sections.
#[derive(Debug, Clone, Default)]
struct SectionBuilder {
    title: String,
    paragraphs: Vec<String>,
    subsections: Vec<JATSBodySection>,
    /// The identity of the container heading this *implicit* section was
    /// opened under, or `None` for one opened under no heading.
    heading: Option<u64>,
}

impl SectionBuilder {
    fn build(&self) -> JATSBodySection {
        JATSBodySection {
            title: self.title.clone(),
            paragraphs: self.paragraphs.clone(),
            subsections: self.subsections.clone(),
        }
    }
}

/// One open `<fig>` or `<table-wrap>`.
#[derive(Debug, Clone)]
struct ExhibitFrame<T> {
    /// The index reserved in the owning slot list when the element opened.
    slot: usize,
    builder: T,
}

/// One open `<contrib>` collected as an author.
#[derive(Debug, Clone)]
struct ContribFrame {
    slot: usize,
    builder: AuthorBuilder,
}

/// A heading a container deposited for its own unsectioned prose (#231).
#[derive(Debug, Clone)]
struct HeadingFrame {
    /// Identity, because two sibling `<notes>` each depositing *Notes* are two
    /// frames and two sections.
    id: u64,
    title: String,
    /// `len(element_stack)` at which this frame's owner is innermost.
    owner_depth: usize,
}

/// One open `<inline-formula>` or `<disp-formula>`.
#[derive(Debug, Clone, Default)]
struct FormulaFrame {
    display: bool,
    label: String,
    latex: Vec<String>,
    alt_text: String,
}

/// One open `<def-item>`: the word it defines, and where it opened.
#[derive(Debug, Clone, Default)]
struct DefinitionFrame {
    term: Option<String>,
    exhibit_depth: usize,
}

/// One open `<award-group>`, though the content model does not nest them.
#[derive(Debug, Clone, Default)]
struct AwardFrame {
    sources: Vec<JATSFundingSource>,
    award_ids: Vec<String>,
    pending_identifier: String,
}

/// One open citation element in a `<ref>`: what its deposit has shown so far.
///
/// Pushed at the element's open and popped at its close, onto
/// `ReferenceBuilder::citation_frames`. A stack rather than a flag because
/// JATS admits a citation inside another's `<annotation>` or `<fn>`, through a
/// `<p>`, or a `<td>` of its `<alternatives>` (issue #414 corrected
/// `<comment>`, which admits none; the rest of #414 is ported by #421).
#[derive(Debug, Clone, Default)]
struct CitationFrame {
    /// Character data of its own that is not whitespace has arrived, directly
    /// or in an `<x>`: the deposit is typeset, so a `<citation>` writes its
    /// string as a `<mixed-citation>` does.
    ///
    /// It decides whether an NLM 2.x `<citation>` writes the reference's
    /// citation string (issue #390): the DTD makes it mixed content, but PMC
    /// deposits it element-only — 1,124,468 of the 1,155,505 served in
    /// PMC0–PMC1999999, all 81,681 in `PMC000xxxxxx` and 624,782 of 624,980 in
    /// `PMC001xxxxxx` carry no character data of their own — and the text of
    /// an element-only one is its parts run together, #314's glue in 75 times
    /// as many references as the 14,952 served element-only `<mixed-citation>`
    /// carrying it. Read by the *deposit* and not the spelling, so a
    /// `<citation>` that carries punctuation of its own writes its string as a
    /// `<mixed-citation>` does and one that carries none writes none, as an
    /// `<element-citation>` does. Whitespace alone is indentation, not text.
    /// Its own text is character data directly in it **or in an** `<x>`, JATS's
    /// element for generated punctuation.
    typeset: bool,
    /// A later citation element declaring itself `display-unstructured` — the
    /// typeset rendering of the same work its structured sibling tags — whose
    /// identifiers fill the reference's where the first part left them empty.
    fills_identifiers: bool,
    /// The `<elocation-id>` stored before this element's first *indented*
    /// join, or `None`. A `<citation>` whose own text has not arrived yet is
    /// read as element-only, so whitespace between two locator parts is
    /// indentation and they join; if typeset text arrives later, that
    /// whitespace was printed and they were two locators. The join is recorded
    /// and undone at the close.
    elocation_before_indented_join: Option<String>,
    /// How many parts have joined since the join above was recorded.
    elocation_parts_indented: u32,
}

/// One citation element, or one `<citation-alternatives>` group, open outside
/// any `<ref>` (Python's #391, #255).
///
/// A citation printed in prose builds no reference, so its close decides only
/// whether its text stays in the sentence. It stays where a `<ref>`'s citation
/// would write its string — a `<mixed-citation>`, and a typeset `<citation>` —
/// so one rule reads the same in a reference list and in a paragraph. An
/// element-only one authored no string and is counted
/// (`prose_citations_dropped`). Nothing routes on the stack: whether a `<p>` is
/// a citation's note is asked of `element_stack`
/// (`paragraph_is_a_prose_citations`), so a stranded frame costs that one
/// citation's accounting and not the rest of the article.
#[derive(Debug, Clone)]
struct ProseCitationFrame {
    /// The element that pushed the frame, which `is_group` and `prints` read.
    /// Required at every push, so the kind of frame is never left to a default.
    element: String,
    /// `element_stack.len()` with the element itself on it, so its own close is
    /// the one that finds it on top.
    depth: usize,
    /// Character data of its own that is not whitespace has arrived, directly
    /// or in an `<x>` — `CitationFrame::typeset`'s test, outside a `<ref>`.
    typeset: bool,
    /// Text that is not whitespace has reached it: character data arriving
    /// while it is the innermost prose citation, and the text of a nested one
    /// that merged into it. Its own buffer cannot say, since an element-only
    /// deposit's fields each take a buffer of their own and merge nothing back.
    carries_text: bool,
    /// A group's rendition that carried text merged into the sentence; an
    /// empty rendition prints nothing and excuses no element-only one.
    printed: bool,
    /// A group's rendition carried text and did not merge, and its text is not
    /// in a cell a table is collecting or declined as metadata.
    lost: bool,
}

impl ProseCitationFrame {
    /// A frame for `element`, opened at `depth`, with nothing yet arrived.
    fn new(element: &str, depth: usize) -> Self {
        Self {
            element: element.to_string(),
            depth,
            typeset: false,
            carries_text: false,
            printed: false,
            lost: false,
        }
    }

    /// Is this a `<citation-alternatives>` group printed in prose: renditions
    /// of one work, so an element-only rendition is no loss where another was
    /// printed, and the group counts once at its close?
    fn is_group(&self) -> bool {
        self.element == "citation-alternatives"
    }

    /// Does this citation element's text stay in the sentence around it?
    fn prints(&self) -> bool {
        self.element == "mixed-citation" || (self.element == "citation" && self.typeset)
    }
}

/// One open `<ref>`.
#[derive(Debug, Clone, Default)]
struct ReferenceBuilder {
    id: String,
    label: String,
    citation_parts: Vec<String>,
    citation_element_count: usize,
    /// One frame per citation element open in this `<ref>`, innermost last.
    citation_frames: Vec<CitationFrame>,
    authors: Vec<String>,
    current_author_surname: String,
    current_author_given_names: String,
    article_title: String,
    source: String,
    year: String,
    volume: String,
    issue: String,
    first_page: String,
    last_page: String,
    doi: String,
    /// Set once a declared `doi` wrote [`Self::doi`], so a DOI taken on its
    /// shape alone cannot replace it (#397).
    doi_is_typed: bool,
    pmid: String,
    /// Set once a declared `pmid`/`pubmed` wrote [`Self::pmid`], so a
    /// `medline` number never replaces it, in either order (#397).
    pmid_is_typed: bool,
    elocation_id: String,
    elocation_may_continue: bool,
}

impl ReferenceBuilder {
    /// Append the pending cited author, where a surname arrived or a `<name>`
    /// closed.
    ///
    /// `closes_a_name` marks the caller as a `<name>` closing. A `<name>`
    /// carrying `<given-names>` alone is a legal mononym, so there the given
    /// names are an author on their own. Only there: Wiley deposits some
    /// editors split across two `<person-group>`, a `<string-name>` carrying
    /// the given names in one and the surname in the next, and the pending
    /// given names are what reassemble those.
    fn finish_current_author(&mut self, closes_a_name: bool) {
        if self.current_author_surname.is_empty() && !closes_a_name {
            return;
        }
        let parts: Vec<&str> = [
            self.current_author_given_names.as_str(),
            self.current_author_surname.as_str(),
        ]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect();
        let name = parts.join(" ");
        if !name.is_empty() {
            self.authors.push(name);
        }
        self.current_author_surname.clear();
        self.current_author_given_names.clear();
    }

    fn build(&self) -> JATSReferenceInfo {
        JATSReferenceInfo {
            id: self.id.clone(),
            label: self.label.clone(),
            citation: normalize_whitespace(&self.citation_parts.join("")),
            authors: self.authors.clone(),
            article_title: self.article_title.clone(),
            source: self.source.clone(),
            year: self.year.clone(),
            volume: self.volume.clone(),
            issue: self.issue.clone(),
            first_page: self.first_page.clone(),
            last_page: self.last_page.clone(),
            doi: self.doi.clone(),
            pmid: self.pmid.clone(),
            elocation_id: self.elocation_id.clone(),
        }
    }
}

/// Which open exhibit a caption or footnote belongs to.
///
/// A stack **index** rather than a pointer: the innermost exhibit is the one at
/// the top of its stack, and it is that same frame again by the time a nested
/// exhibit — which pushes above and pops before the text arrives — has closed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ExhibitRef {
    /// Index into the figure stack.
    Figure(usize),
    /// Index into the table stack.
    Table(usize),
}

/// Escape text the way `html.escape(text)` does.
fn html_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(ch),
        }
    }
    out
}

/// Pad a row to `count` cells, truncating one already wider.
fn pad_row(row: Vec<String>, count: usize) -> Vec<String> {
    if row.len() >= count {
        return row[..count].to_vec();
    }
    let mut row = row;
    row.resize(count, String::new());
    row
}

/// Read a cell's `colspan`, reporting one this reader will not honour.
///
/// Python's `int(raw)` is reproduced for the shapes a `colspan` takes: it is
/// trimmed, an optional sign is folded, and `_` separators are ignored. A
/// value above [`MAX_COLSPAN`], or one that is not an integer at all, is
/// refused and the cell is treated as one column.
fn read_span(attrs: &Attrs<'_, '_>) -> (i128, bool) {
    let raw = attrs.get("colspan").unwrap_or("1");
    let raw = if raw.is_empty() { "1" } else { raw };
    match parse_python_int(raw) {
        Some(span) if span <= MAX_COLSPAN => (span, false),
        _ => (1, true),
    }
}

/// Parse an integer the way Python's `int(str)` does for a `colspan`.
fn parse_python_int(raw: &str) -> Option<i128> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    let (negative, digits) = match trimmed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, trimmed.strip_prefix('+').unwrap_or(trimmed)),
    };
    let mut value: i128 = 0;
    let mut seen = false;
    for ch in digits.chars() {
        if ch == '_' {
            continue;
        }
        let digit = ch.to_digit(10)? as i128;
        value = value.checked_mul(10)?.checked_add(digit)?;
        seen = true;
    }
    if !seen {
        return None;
    }
    Some(if negative { -value } else { value })
}

/// Python's `str.isspace`, which is false for the empty string.
///
/// Rust's `chars().all(char::is_whitespace)` is vacuously true there, and the
/// distinction decides whether an empty character-data callback marks a
/// citation as typeset; Python's `not "".isspace()` is `True`.
fn python_isspace(text: &str) -> bool {
    !text.is_empty() && text.chars().all(char::is_whitespace)
}

/// Which identifier field a cited `<pub-id>` writes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CitedId {
    Doi,
    Pmid,
}

/// Is `text` a non-empty run of ASCII `0`-`9`? Python's `_is_ascii_digits`:
/// no PMID is spelled in other digits (0 of 2,162,946 served `<pub-id>`).
fn is_ascii_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())
}

/// Python's `_classify_cited_pub_id` (#397): which identifier a cited
/// `<pub-id>` is, read from its declared `pub-id-type` first.
///
/// `doi` is the DOI where it has the `10.` prefix; `pmid`/`pubmed` the PMID
/// where it is ASCII digits, at any length. Under any other type, or none, a
/// number is never guessed, with two exceptions, both returned untyped: a DOI
/// shape (a `10.` prefix *and* a slash) is the DOI whatever it declares, and a
/// `medline` number — a MEDLINE UI in back-file deposits, the real PMID in
/// recent ones — is a PMID that only fills an empty field. `typed` is false
/// where the value may not replace a declared one.
fn classify_cited_pub_id(declared_type: Option<&str>, text: &str) -> Option<(CitedId, bool)> {
    let folded = declared_type.unwrap_or("").to_lowercase();
    match folded.as_str() {
        "doi" => text.starts_with("10.").then_some((CitedId::Doi, true)),
        "pmid" | "pubmed" => is_ascii_digits(text).then_some((CitedId::Pmid, true)),
        "medline" if is_ascii_digits(text) => Some((CitedId::Pmid, false)),
        _ if text.starts_with("10.") && text.contains('/') => Some((CitedId::Doi, false)),
        _ => None,
    }
}

/// Does `declared_type` name a publication date?
fn names_a_publication_date(declared_type: Option<&str>) -> bool {
    match declared_type {
        None => true,
        Some(declared_type) => {
            if declared_type.is_empty() {
                return true;
            }
            let folded = declared_type.trim().to_lowercase();
            !NON_PUBLICATION_DATE_SUFFIXES
                .iter()
                .any(|suffix| folded.ends_with(suffix))
        }
    }
}

// ---------------------------------------------------------------------------
// The handler
// ---------------------------------------------------------------------------

/// The stack machine Python's `_JATSHandler` is, fed by a DOM walk.
struct Handler {
    // Parsed content
    title: String,
    author_slots: Vec<Option<JATSAuthorInfo>>,
    journal: String,
    volume: String,
    issue: String,
    pages: String,
    elocation_id: String,
    page_range_awaits_last_page: bool,
    funding_statements: Vec<String>,
    funding_awards: Vec<JATSFundingAward>,
    award_stack: Vec<AwardFrame>,
    funder_named_content_types: Vec<String>,
    year: String,
    doi: String,
    doi_is_typed: bool,
    pmc_id: String,
    pmid: String,
    abstract_sections: Vec<JATSAbstractSection>,
    body_sections: Vec<JATSBodySection>,
    references: Vec<JATSReferenceInfo>,

    // Parsing state
    element_stack: Vec<String>,
    text_stack: Vec<String>,
    nested_article_depth: u32,
    suppressed_nested_articles: u32,

    // Article metadata state
    in_front: bool,
    contrib_group_stack: Vec<Option<String>>,
    contrib_stack: Vec<Option<ContribFrame>>,
    front_contributor_name_count: u32,
    rejected_spans: u32,
    contribs_naming_nobody: u32,
    formulas_dropped: u32,
    refused_apparatus_prose: u32,
    def_item_stack: Vec<DefinitionFrame>,
    /// Citation elements open outside any `<ref>`, innermost last (#391).
    prose_citation_stack: Vec<ProseCitationFrame>,
    heading_stack: Vec<HeadingFrame>,
    next_heading_id: u64,
    definition_terms_dropped: u32,
    footnote_markers_dropped: u32,
    footnote_headings_dropped: u32,
    footnote_graphics_dropped: u32,
    cell_text_dropped: u32,
    attributions_dropped: u32,
    funding_statements_dropped: u32,
    prose_citations_dropped: u32,
    elocation_parts_dropped: u32,
    last_pages_dropped: u32,
    non_publication_years_refused: u32,
    current_article_id_type: Option<String>,
    current_pub_date_type: Option<String>,
    /// The type the open `<pub-id>` declared, read at its close by
    /// [`classify_cited_pub_id`] (#397). `<pub-id>` is `(#PCDATA)`, so no
    /// element opens inside it to clear the slot early.
    current_pub_id_type: Option<String>,

    // Abstract state
    in_abstract: bool,
    current_abstract_title: String,
    current_abstract_text: Vec<String>,

    // Body / back state
    in_body: bool,
    in_back: bool,
    section_stack: Vec<SectionBuilder>,
    implicit_body_section: Option<SectionBuilder>,
    implicit_back_section: Option<SectionBuilder>,
    implicit_front_section: Option<SectionBuilder>,
    body_paragraph_count: u32,

    // Figure / table state
    figure_slots: Vec<Option<JATSFigureInfo>>,
    table_slots: Vec<Option<JATSTableInfo>>,
    figure_stack: Vec<ExhibitFrame<FigureBuilder>>,
    table_stack: Vec<ExhibitFrame<TableBuilder>>,
    caption_stack: Vec<Option<ExhibitRef>>,
    formula_stack: Vec<FormulaFrame>,

    // Reference state
    in_ref_list: bool,
    in_ref: bool,
    in_ref_citation: bool,
    in_ref_person_group: bool,
    current_reference: Option<ReferenceBuilder>,

    // Cross-reference state
    current_xref_type: Option<String>,
    current_xref_rid: Option<String>,
}

impl Handler {
    fn new(known_pmc_id: &str) -> Self {
        let pmc_id = if !known_pmc_id.is_empty() && !known_pmc_id.starts_with("PMC") {
            format!("PMC{known_pmc_id}")
        } else {
            known_pmc_id.to_string()
        };
        Self {
            title: String::new(),
            author_slots: Vec::new(),
            journal: String::new(),
            volume: String::new(),
            issue: String::new(),
            pages: String::new(),
            elocation_id: String::new(),
            page_range_awaits_last_page: false,
            funding_statements: Vec::new(),
            funding_awards: Vec::new(),
            award_stack: Vec::new(),
            funder_named_content_types: Vec::new(),
            year: String::new(),
            doi: String::new(),
            doi_is_typed: false,
            pmc_id,
            pmid: String::new(),
            abstract_sections: Vec::new(),
            body_sections: Vec::new(),
            references: Vec::new(),
            element_stack: Vec::new(),
            text_stack: vec![String::new()],
            nested_article_depth: 0,
            suppressed_nested_articles: 0,
            in_front: false,
            contrib_group_stack: Vec::new(),
            contrib_stack: Vec::new(),
            front_contributor_name_count: 0,
            rejected_spans: 0,
            contribs_naming_nobody: 0,
            formulas_dropped: 0,
            refused_apparatus_prose: 0,
            def_item_stack: Vec::new(),
            prose_citation_stack: Vec::new(),
            heading_stack: Vec::new(),
            next_heading_id: 0,
            definition_terms_dropped: 0,
            footnote_markers_dropped: 0,
            footnote_headings_dropped: 0,
            footnote_graphics_dropped: 0,
            cell_text_dropped: 0,
            attributions_dropped: 0,
            funding_statements_dropped: 0,
            prose_citations_dropped: 0,
            elocation_parts_dropped: 0,
            last_pages_dropped: 0,
            non_publication_years_refused: 0,
            current_article_id_type: None,
            current_pub_date_type: None,
            current_pub_id_type: None,
            in_abstract: false,
            current_abstract_title: String::new(),
            current_abstract_text: Vec::new(),
            in_body: false,
            in_back: false,
            section_stack: Vec::new(),
            implicit_body_section: None,
            implicit_back_section: None,
            implicit_front_section: None,
            body_paragraph_count: 0,
            figure_slots: Vec::new(),
            table_slots: Vec::new(),
            figure_stack: Vec::new(),
            table_stack: Vec::new(),
            caption_stack: Vec::new(),
            formula_stack: Vec::new(),
            in_ref_list: false,
            in_ref: false,
            in_ref_citation: false,
            in_ref_person_group: false,
            current_reference: None,
            current_xref_type: None,
            current_xref_rid: None,
        }
    }

    /// Walk the DOM in document order, emitting SAX-shaped events.
    fn walk(&mut self, node: Node<'_, '_>) {
        match node.node_type() {
            NodeType::Element => {
                let name = node.tag_name().name();
                self.start_element(name, &Attrs { node });
                for child in node.children() {
                    self.walk(child);
                }
                self.end_element(name);
            }
            NodeType::Text => {
                if let Some(text) = node.text() {
                    self.characters(text);
                }
            }
            _ => {}
        }
    }

    // -- Derived queries ----------------------------------------------------

    fn in_figure(&self) -> bool {
        !self.figure_stack.is_empty()
    }

    fn in_table_wrap(&self) -> bool {
        !self.table_stack.is_empty()
    }

    fn current_figure_ref(&self) -> Option<ExhibitRef> {
        if self.figure_stack.is_empty() {
            None
        } else {
            Some(ExhibitRef::Figure(self.figure_stack.len() - 1))
        }
    }

    fn current_table_ref(&self) -> Option<ExhibitRef> {
        if self.table_stack.is_empty() {
            None
        } else {
            Some(ExhibitRef::Table(self.table_stack.len() - 1))
        }
    }

    fn current_author(&self) -> Option<&AuthorBuilder> {
        match self.contrib_stack.last() {
            Some(Some(frame)) => Some(&frame.builder),
            _ => None,
        }
    }

    fn current_author_mut(&mut self) -> Option<&mut AuthorBuilder> {
        match self.contrib_stack.last_mut() {
            Some(Some(frame)) => Some(&mut frame.builder),
            _ => None,
        }
    }

    fn in_contrib(&self) -> bool {
        self.current_author().is_some()
    }

    fn build_authors(&self) -> Vec<JATSAuthorInfo> {
        self.author_slots.iter().flatten().cloned().collect()
    }

    fn build_figures(&self) -> Vec<JATSFigureInfo> {
        self.figure_slots.iter().flatten().cloned().collect()
    }

    fn build_tables(&self) -> Vec<JATSTableInfo> {
        self.table_slots.iter().flatten().cloned().collect()
    }

    /// The element a `<graphic>` belongs to.
    fn graphic_owner(&self, closing_child: bool) -> String {
        let cut = if closing_child { 2 } else { 1 };
        let end = self.element_stack.len().saturating_sub(cut);
        for name in self.element_stack[..end].iter().rev() {
            if !GRAPHIC_TRANSPARENT_WRAPPERS.contains(&name.as_str()) {
                return name.clone();
            }
        }
        String::new()
    }

    // -- Text stack ---------------------------------------------------------

    fn current_text(&self) -> &str {
        self.text_stack.last().map_or("", String::as_str)
    }

    fn append_text(&mut self, text: &str) {
        if let Some(buffer) = self.text_stack.last_mut() {
            buffer.push_str(text);
        }
    }

    fn push_text_buffer(&mut self) {
        self.text_stack.push(String::new());
    }

    fn pop_text_buffer(&mut self, merge_with_parent: bool) -> String {
        if self.text_stack.len() <= 1 {
            let text = self.text_stack.first().cloned().unwrap_or_default();
            if let Some(first) = self.text_stack.first_mut() {
                first.clear();
            }
            return text;
        }
        let text = self.text_stack.pop().unwrap_or_default();
        if merge_with_parent && !text.is_empty() {
            if let Some(parent) = self.text_stack.last_mut() {
                parent.push_str(&text);
            }
        }
        text
    }

    // -- Ancestor and context tests -----------------------------------------

    fn inside_mixed_citation(&self) -> bool {
        let end = self.element_stack.len().saturating_sub(1);
        self.element_stack[..end]
            .iter()
            .any(|element| MIXED_CONTENT_CITATIONS.contains(&element.as_str()))
    }

    /// Is the element now closing a *descendant* of a related work?
    ///
    /// See [`RELATED_WORK_ELEMENTS`]: a related work's parts are its text, so
    /// each merges back into the buffer the related work sits in (issues
    /// #267, #271) — the `<mixed-citation>` rule of [`Self::inside_mixed_citation`],
    /// and an ancestor test for the same reason, since the parts nest (a
    /// `<surname>` in a `<person-group>`). Strict, so the related work's own
    /// element is not asked about itself; none of the three accumulates today,
    /// so that half is prospective.
    fn inside_related_work(&self) -> bool {
        let end = self.element_stack.len().saturating_sub(1);
        self.element_stack[..end]
            .iter()
            .any(|element| RELATED_WORK_ELEMENTS.contains(&element.as_str()))
    }

    /// May the element on top of the stack write a reference's structured fields?
    ///
    /// The reference's structured-field arms were gated on `in_ref_citation`
    /// alone, which is *ambient* — true anywhere under the reference's first
    /// citation element — so a `<related-object>` or `<related-article>` nested
    /// in the citation wrote *its* volume, pages, title and names onto the
    /// reference (issue #270). An ancestor walk from the element up to the
    /// nearest citation element, refusing where a related work stands between
    /// them — not the parent test `<elocation-id>` uses, because a `<year>` may
    /// sit in a `<date>` and a `<surname>` in a `<person-group>`, so a parent
    /// test would refuse the reference's own values. Read at an open and at the
    /// matching close alike: the slice asks about ancestors only.
    fn cited_reference(&self) -> bool {
        if !(self.in_ref_citation && self.current_reference.is_some()) {
            return false;
        }
        let end = self.element_stack.len().saturating_sub(1);
        for ancestor in self.element_stack[..end].iter().rev() {
            if CITATION_ELEMENTS.contains(&ancestor.as_str()) {
                return true;
            }
            if RELATED_WORK_ELEMENTS.contains(&ancestor.as_str()) {
                return false;
            }
        }
        // Unreachable while `in_ref_citation` is set, which only a citation
        // element's open sets; kept permissive so the gate stays what it was.
        true
    }

    /// Is the name element on top of the stack its contributor's own name?
    ///
    /// A `<contrib>` holds prose *about* the contributor as well as the
    /// contributor's name — a `<bio>` and an `<author-comment>`, each of `<p>`
    /// — and every name arm read `in_contrib` alone, so a name printed in the
    /// biography replaced the author's own (issue #258) and a `<collab>` there
    /// became the author's collaboration. The same test decides the merge
    /// refusal of [`UNDIVIDED_NAME_ELEMENTS`], which cut a `<string-name>` or
    /// `<collab>` out of the bio paragraph that printed it.
    ///
    /// The walk stops at the innermost `<contrib>`, so #120's roster — a
    /// `<contrib>` inside a `<collab>`'s `<contrib-group>` — is still answered
    /// by the member's own position.
    fn contrib_owns_name(&self) -> bool {
        let end = self.element_stack.len().saturating_sub(1);
        for ancestor in self.element_stack[..end].iter().rev() {
            if ancestor == "contrib" {
                return true;
            }
            if CONTRIBUTOR_PROSE.contains(&ancestor.as_str()) {
                return false;
            }
        }
        true
    }

    /// Is the `<abstract>` on top of the stack the article's own?
    ///
    /// Only a direct child of `front > article-meta` is. JATS 1.3 admits an
    /// `<abstract>` in fifteen containers, and each other one describes an
    /// object — a `<supplementary-material>`, a `<media>`, a `<graphic>`, a
    /// `<statement>`, a `<sec-meta>` — not the article; but the arms accepted
    /// any, so a dataset's summary joined `abstract_sections` (issue #266).
    /// Refused, its prose routes as that object's other prose does. The same
    /// test ends issue #249's latent erasure: a `<fig>` in the article's own
    /// abstract carrying an `<abstract>` of its own opened and cleared the
    /// abstract state, discarding everything before it.
    fn is_articles_abstract(&self) -> bool {
        self.owned_by(ARTICLE_META)
    }

    /// Is the `<contrib>` now opening in the article's own contributor list?
    ///
    /// `<contrib-group>` sits in `<journal-meta>` (the journal's editors), in
    /// `<supplement>`, in `<sec-meta>` and — through a `<supplement>` — inside
    /// another work, and a `<contrib>` declaring no role there was collected as
    /// an author wherever it sat (issue #266). The article's list is the
    /// outermost `<contrib-group>` at `front > article-meta`; everything inside
    /// it — #120's roster in a `<collab>`, #111's group-declared roles — is
    /// decided exactly as before.
    ///
    /// A `<contrib>` with no `<contrib-group>` at all is out of place for JATS,
    /// and the lenient reading the reader gives it is kept where it stands at
    /// the article's own position, directly in `<article-meta>`.
    fn in_articles_contributor_list(&self) -> bool {
        let stack = &self.element_stack;
        let anchor = stack
            .iter()
            .position(|element| element == "contrib-group")
            .unwrap_or_else(|| stack.len().saturating_sub(1));
        let start = anchor.saturating_sub(ARTICLE_META.len());
        stack[start..anchor]
            .iter()
            .map(String::as_str)
            .eq(ARTICLE_META.iter().copied())
    }

    /// May a closing `<surname>` or `<given-names>` name a cited author here?
    ///
    /// Two positions. Inside the reference's `<person-group>`, as before —
    /// which is also where a divided `<string-name>`'s parts are read. And in
    /// a `<name>` deposited directly in the citation, which JATS 1.3 admits in
    /// both citation elements: gated on `in_ref_person_group` alone, no arm
    /// fired there and the reference stored no authors, while the
    /// `<string-name>` and `<collab>` arms beside them had long read the same
    /// position. The second half is a **parent** test, so a `<string-name>`
    /// carrying a `<surname>` child outside a group keeps the verbatim reading
    /// its own arm gives it.
    fn cited_name_part_reference(&self) -> bool {
        self.cited_reference() && (self.in_ref_person_group || self.parent_element() == "name")
    }

    /// May a `display-unstructured` part's identifier fill the reference?
    ///
    /// Where [`Self::cited_reference`] refuses because this is not the
    /// `<ref>`'s first citation element, a later one declaring itself the
    /// display rendering of the same work may still supply an identifier the
    /// first left empty. The same ancestor walk, so a related work's
    /// identifier inside it is refused as it is in the first part.
    fn display_part_reference(&self) -> bool {
        let Some(reference) = self.current_reference.as_ref() else {
            return false;
        };
        if !(self.in_ref
            && reference
                .citation_frames
                .last()
                .is_some_and(|f| f.fills_identifiers))
        {
            return false;
        }
        let end = self.element_stack.len().saturating_sub(1);
        for ancestor in self.element_stack[..end].iter().rev() {
            if CITATION_ELEMENTS.contains(&ancestor.as_str()) {
                return true;
            }
            if RELATED_WORK_ELEMENTS.contains(&ancestor.as_str()) {
                return false;
            }
        }
        false
    }

    /// Does the name element now closing count toward the zero-author detector?
    ///
    /// The detector's evidence is how many names the article's own contributor
    /// list carries. It counted every spelling anywhere in `<front>`, and
    /// `<front>` also holds other works — a retraction notice's
    /// `<related-article>`, a book review's `<product>`, a citation in
    /// abstract prose — and the journal's editors in `<journal-meta>`, so an
    /// author-less notice about another paper was reported as a routing
    /// failure (issue #264). Scoped here to the list
    /// [`Self::in_articles_contributor_list`] admits, and **structural, never
    /// the role**: a contributor whose role `is_author_contrib` refuses still
    /// counts, since that refusal is the mis-routing the detector exists to
    /// report.
    ///
    /// One name counts once: an element inside another spelling, before the
    /// nearest `<contrib>` or `<contrib-group>`, is that spelling's part and
    /// is counted by its container. The walk stops at either, so a roster
    /// member inside a `<collab>` is a name of its own.
    fn names_articles_contributor(&self) -> bool {
        let end = self.element_stack.len().saturating_sub(1);
        let ancestors = &self.element_stack[..end];
        for ancestor in ancestors.iter().rev() {
            if ancestor == "contrib" || ancestor == "contrib-group" {
                break;
            }
            if CONTRIBUTOR_NAME_SPELLINGS.contains(&ancestor.as_str()) {
                return false;
            }
        }
        for anchor_name in ["contrib-group", "contrib"] {
            if let Some(anchor) = ancestors.iter().position(|element| element == anchor_name) {
                let start = anchor.saturating_sub(ARTICLE_META.len());
                return ancestors[start..anchor]
                    .iter()
                    .map(String::as_str)
                    .eq(ARTICLE_META.iter().copied());
            }
        }
        false
    }

    /// The prose citation frame the element now closing pushed, if any —
    /// matched by depth, so a citation element that pushed none (one inside a
    /// `<ref>`) is never answered with its enclosing prose citation's frame.
    /// That half is prospective, as Python's docstring says: JATS admits no
    /// `<ref>` inside a citation, so taking the top of the stack is an
    /// equivalent mutant on valid input. Kept so a frame is not tied to
    /// whichever citation closes next.
    fn closing_prose_citation(&self) -> Option<&ProseCitationFrame> {
        self.prose_citation_stack
            .last()
            .filter(|frame| frame.depth == self.element_stack.len())
    }

    /// Is the `<p>` now closing a prose citation's own text (#391)? Asked of
    /// `element_stack`, not of the frame stack, so a stranded frame cannot
    /// withhold every later paragraph. The walk ends at a `<fig>` or
    /// `<table-wrap>`, which routes its own caption and footnotes as it does
    /// inside a `<ref>`'s citation; outside a `<ref>` only.
    fn paragraph_is_a_prose_citations(&self) -> bool {
        if self.in_ref {
            return false;
        }
        let end = self.element_stack.len().saturating_sub(1);
        for element in self.element_stack[..end].iter().rev() {
            if CITATION_ELEMENTS.contains(&element.as_str()) {
                return true;
            }
            if element == "fig" || element == "table-wrap" {
                return false;
            }
        }
        false
    }

    fn inside_declined_metadata(&self) -> bool {
        for element in &self.element_stack {
            if TEXT_CLAIMING_ELEMENTS.contains(&element.as_str()) {
                return false;
            }
            if NON_PROSE_METADATA.contains(&element.as_str()) {
                return true;
            }
        }
        false
    }

    fn inside_text_claiming_element(&self) -> bool {
        let end = self.element_stack.len().saturating_sub(1);
        self.element_stack[..end]
            .iter()
            .any(|element| TEXT_CLAIMING_ELEMENTS.contains(&element.as_str()))
    }

    fn inside_table_cell(&self) -> bool {
        let end = self.element_stack.len().saturating_sub(1);
        for element in self.element_stack[..end].iter().rev() {
            if TABLE_CELL_ELEMENTS.contains(&element.as_str()) {
                return true;
            }
            if element == "table-wrap" {
                return false;
            }
        }
        false
    }

    fn offer_cell_text(&mut self, text: &str) {
        if self.table_stack.is_empty() {
            return;
        }
        if self.inside_declined_metadata() {
            return;
        }
        if let Some(table) = self.table_stack.last_mut() {
            table.builder.append_cell_text(text);
        }
    }

    /// The exhibit whose footnote the element atop the stack sits in, if any.
    fn owning_exhibit_footnote(&self, including_self: bool) -> Option<ExhibitRef> {
        let end = if including_self {
            self.element_stack.len()
        } else {
            self.element_stack.len().saturating_sub(1)
        };
        let mut saw_container = false;
        for element in self.element_stack[..end].iter().rev() {
            if EXHIBIT_FOOTNOTE_CONTAINERS.contains(&element.as_str()) {
                saw_container = true;
            } else if TABLE_CELL_ELEMENTS.contains(&element.as_str())
                || CITATION_ELEMENTS.contains(&element.as_str())
            {
                // A citation's `<fn>` is its own note, printed in its string,
                // and not the exhibit's footnote matter (PR #440's review).
                return None;
            } else if element == "fig" {
                return if saw_container {
                    self.current_figure_ref()
                } else {
                    None
                };
            } else if element == "table-wrap" {
                return if saw_container {
                    self.current_table_ref()
                } else {
                    None
                };
            }
        }
        None
    }

    /// The builder a direct child of `parent` describes, if this reader models
    /// it.
    fn exhibit_named(&self, parent: &str) -> Option<ExhibitRef> {
        if parent == "fig" {
            return self.current_figure_ref();
        }
        if parent == "table-wrap" {
            return self.current_table_ref();
        }
        None
    }

    fn funder_identifier_is_open(&self) -> bool {
        match self.funder_named_content_types.last() {
            None => false,
            Some(declared) => {
                let folded = declared.trim().to_lowercase().replace('-', "_");
                FUNDER_IDENTIFIER_CONTENT_TYPES.contains(&folded.as_str())
            }
        }
    }

    fn is_award_funder_child(&self) -> bool {
        self.element_stack.len() >= 3
            && AWARD_FUNDER_ELEMENTS
                .contains(&self.element_stack[self.element_stack.len() - 2].as_str())
            && self.element_stack[self.element_stack.len() - 3] == "award-group"
    }

    fn parent_element(&self) -> String {
        if self.element_stack.len() >= 2 {
            self.element_stack[self.element_stack.len() - 2].clone()
        } else {
            String::new()
        }
    }

    fn owned_by(&self, path: &[&str]) -> bool {
        let count = path.len();
        if self.element_stack.len() < count + 1 {
            return false;
        }
        let end = self.element_stack.len() - 1;
        let start = end - count;
        self.element_stack[start..end]
            .iter()
            .map(String::as_str)
            .eq(path.iter().copied())
    }

    fn in_own_metadata(&self, container: &[&str], wrappers: &[&[&str]]) -> bool {
        if self.owned_by(container) {
            return true;
        }
        wrappers.iter().any(|wrapper| {
            let count = container.len() + wrapper.len();
            if self.element_stack.len() < count + 1 {
                return false;
            }
            let end = self.element_stack.len() - 1;
            let start = end - count;
            self.element_stack[start..start + container.len()]
                .iter()
                .map(String::as_str)
                .eq(container.iter().copied())
                && self.element_stack[start + container.len()..end]
                    .iter()
                    .map(String::as_str)
                    .eq(wrapper.iter().copied())
        })
    }

    // -- Prose routing ------------------------------------------------------

    fn prose_reaches_output(&self) -> bool {
        if self.inside_declined_metadata() {
            return false;
        }
        if self.in_figure() || self.in_table_wrap() {
            if let Some(caption) = self.caption_stack.last() {
                return caption.is_some();
            }
            return self.owning_exhibit_footnote(false).is_some();
        }
        if self.in_abstract {
            return true;
        }
        if (self.in_body || self.in_back || self.in_front) && !self.section_stack.is_empty() {
            return true;
        }
        self.unsectioned_prose_is_the_articles()
    }

    fn prose_is_refused_apparatus(&self) -> bool {
        if self.in_figure() || self.in_table_wrap() || self.in_abstract {
            return false;
        }
        if !self.section_stack.is_empty() {
            return false;
        }
        (self.in_back || self.in_front) && !self.unsectioned_prose_is_the_articles()
    }

    fn unsectioned_prose_is_the_articles(&self) -> bool {
        if self.in_body {
            return true;
        }
        if self.in_back || self.in_front {
            let end = self.element_stack.len().saturating_sub(1);
            return !self.element_stack[..end]
                .iter()
                .any(|element| element == "ref-list");
        }
        false
    }

    fn heading_is_its_containers_own(&self) -> bool {
        if self.in_figure() || self.in_table_wrap() {
            return false;
        }
        let end = self.element_stack.len().saturating_sub(1);
        if self.element_stack[..end]
            .iter()
            .any(|element| element == "abstract")
        {
            return false;
        }
        if !self.section_stack.is_empty() {
            return false;
        }
        if self.inside_declined_metadata() {
            return false;
        }
        self.unsectioned_prose_is_the_articles()
    }

    fn recover_container_heading(&mut self, title: &str) {
        if title.is_empty() {
            return;
        }
        let owner_depth = self.element_stack.len() - 1;
        let frame = HeadingFrame {
            id: self.next_heading_id,
            title: title.to_string(),
            owner_depth,
        };
        self.next_heading_id += 1;
        if self
            .heading_stack
            .last()
            .is_some_and(|last| last.owner_depth == owner_depth)
        {
            *self.heading_stack.last_mut().unwrap() = frame;
            return;
        }
        self.heading_stack.push(frame);
    }

    fn close_container_heading(&mut self) {
        if self
            .heading_stack
            .last()
            .is_some_and(|last| last.owner_depth == self.element_stack.len())
            && self.nested_article_depth == 0
        {
            self.heading_stack.pop();
        }
    }

    /// Open the implicit section the next unsectioned run joins, if needed.
    fn ensure_implicit_section(&mut self) {
        let heading_id = self.heading_stack.last().map(|frame| frame.id);
        let heading_title = self
            .heading_stack
            .last()
            .map(|frame| frame.title.clone())
            .unwrap_or_default();
        let current_heading = if self.in_body {
            self.implicit_body_section.as_ref()
        } else if self.in_back {
            self.implicit_back_section.as_ref()
        } else {
            self.implicit_front_section.as_ref()
        }
        .map(|builder| builder.heading);
        if let Some(existing) = current_heading {
            if existing == heading_id {
                return;
            }
            self.flush_implicit_section();
        }
        let builder = SectionBuilder {
            title: heading_title,
            paragraphs: Vec::new(),
            subsections: Vec::new(),
            heading: heading_id,
        };
        if self.in_body {
            self.implicit_body_section = Some(builder);
        } else if self.in_back {
            self.implicit_back_section = Some(builder);
        } else {
            self.implicit_front_section = Some(builder);
        }
    }

    /// Append to the implicit section the run joins, opening one if needed.
    fn push_unsectioned_prose(&mut self, text: String) {
        self.ensure_implicit_section();
        let target = if self.in_body {
            self.implicit_body_section.as_mut()
        } else if self.in_back {
            self.implicit_back_section.as_mut()
        } else {
            self.implicit_front_section.as_mut()
        };
        if let Some(builder) = target {
            builder.paragraphs.push(text);
        }
    }

    fn prefix_pending_definition_term(&mut self, text: &str) -> String {
        if text.is_empty() || self.def_item_stack.is_empty() {
            return text.to_string();
        }
        let (term, exhibit_depth) = match &self.def_item_stack.last().unwrap().term {
            None => return text.to_string(),
            Some(term) => (
                term.clone(),
                self.def_item_stack.last().unwrap().exhibit_depth,
            ),
        };
        if self.figure_stack.len() + self.table_stack.len() > exhibit_depth {
            return text.to_string();
        }
        if !(self.prose_reaches_output() || self.prose_is_refused_apparatus()) {
            return text.to_string();
        }
        self.def_item_stack.last_mut().unwrap().term = None;
        format!("{term}{DEFINITION_SEPARATOR}{text}")
    }

    fn append_prose(&mut self, text: &str, keep_empty: bool, spend_pending: bool) {
        if self.inside_declined_metadata() {
            return;
        }
        let mut text = text.to_string();
        if spend_pending {
            text = self.prefix_pending_definition_term(&text);
        }
        if self.in_figure() || self.in_table_wrap() {
            if !self.caption_stack.is_empty() {
                self.append_caption_text(&text);
            } else if let Some(owner) = self.owning_exhibit_footnote(false) {
                self.append_footnote_to(owner, &text, spend_pending);
            }
        } else if self.in_abstract {
            if !text.is_empty() {
                self.current_abstract_text.push(text);
            }
        } else if (self.in_body || self.in_back || self.in_front) && !self.section_stack.is_empty()
        {
            if text.is_empty() && !keep_empty {
                return;
            }
            if self.in_body && !text.is_empty() {
                self.body_paragraph_count += 1;
            }
            self.section_stack.last_mut().unwrap().paragraphs.push(text);
        } else if !text.is_empty() && self.unsectioned_prose_is_the_articles() {
            if self.in_body {
                self.body_paragraph_count += 1;
            }
            self.push_unsectioned_prose(text);
        } else if !text.is_empty() && self.prose_is_refused_apparatus() {
            // The <ref-list> refusal. Counted rather than dropped in silence:
            // a refusal this reader argued for earns a line.
            self.refused_apparatus_prose += 1;
        }
    }

    fn append_caption_text(&mut self, text: &str) {
        let Some(Some(owner)) = self.caption_stack.last().copied() else {
            return;
        };
        if text.is_empty() {
            return;
        }
        let caption = match owner {
            ExhibitRef::Figure(index) => &mut self.figure_stack[index].builder.caption,
            ExhibitRef::Table(index) => &mut self.table_stack[index].builder.caption,
        };
        if !caption.is_empty() {
            caption.push(' ');
        }
        caption.push_str(text);
    }

    fn append_footnote_to(&mut self, owner: ExhibitRef, text: &str, fold_marker: bool) {
        match owner {
            ExhibitRef::Figure(index) => {
                self.figure_stack[index]
                    .builder
                    .footnotes
                    .append_footnote(text, fold_marker);
            }
            ExhibitRef::Table(index) => {
                self.table_stack[index]
                    .builder
                    .footnotes
                    .append_footnote(text, fold_marker);
            }
        }
    }

    fn flush_implicit_section(&mut self) {
        let pending = if self.in_body {
            self.implicit_body_section.take()
        } else if self.in_back {
            self.implicit_back_section.take()
        } else if self.in_front {
            self.implicit_front_section.take()
        } else {
            return;
        };
        if let Some(builder) = pending {
            self.body_sections.push(builder.build());
        }
    }

    // -- SAX events ---------------------------------------------------------

    fn start_element(&mut self, name: &str, attrs: &Attrs<'_, '_>) {
        self.element_stack.push(name.to_string());

        if NESTED_ARTICLE_ELEMENTS.contains(&name) {
            self.nested_article_depth += 1;
            self.suppressed_nested_articles += 1;
        }

        if TEXT_ACCUMULATING.contains(&name) {
            self.push_text_buffer();
        }

        if self.nested_article_depth > 0 {
            // Inside a nested article. An open leaves state behind, so the
            // suppression covers the opening tag too.
            return;
        }

        if name == "front" {
            self.in_front = true;
        } else if name == "contrib-group" {
            self.contrib_group_stack
                .push(attrs.get("content-type").map(str::to_string));
        } else if name == "contrib" {
            // Only the article's own list, not a journal's editors or a
            // <supplement>'s contributors (issue #266); within it, the role is
            // decided as before.
            if self.in_articles_contributor_list()
                && self.is_author_contrib(attrs.get("contrib-type"))
            {
                self.author_slots.push(None);
                let slot = self.author_slots.len() - 1;
                self.contrib_stack.push(Some(ContribFrame {
                    slot,
                    builder: AuthorBuilder::default(),
                }));
            } else {
                self.contrib_stack.push(None);
            }
        } else if name == "abstract" && self.is_articles_abstract() {
            self.in_abstract = true;
            self.current_abstract_title = String::new();
            self.current_abstract_text = Vec::new();
        } else if name == "body" {
            self.in_body = true;
        } else if name == "back" {
            self.in_back = true;
        } else if name == "sec" {
            if !self.in_abstract {
                self.flush_implicit_section();
                self.section_stack.push(SectionBuilder::default());
            }
        } else if name == "def-item" {
            self.def_item_stack.push(DefinitionFrame {
                term: None,
                exhibit_depth: self.figure_stack.len() + self.table_stack.len(),
            });
        } else if name == "fig" {
            self.figure_slots.push(None);
            let slot = self.figure_slots.len() - 1;
            self.figure_stack.push(ExhibitFrame {
                slot,
                builder: FigureBuilder {
                    id: attrs.get("id").unwrap_or("").to_string(),
                    ..FigureBuilder::default()
                },
            });
        } else if FORMULA_ELEMENTS.contains(&name) {
            self.formula_stack.push(FormulaFrame {
                display: name == "disp-formula",
                ..FormulaFrame::default()
            });
        } else if name == "caption" {
            let parent = self.parent_element();
            self.caption_stack.push(self.exhibit_named(&parent));
        } else if name == "graphic" {
            // Stripped because an href of spaces is truthy and would take the
            // ranking slot; see Python's `offer_graphic` note.
            let href = attrs
                .get("xlink:href")
                .filter(|value| !value.is_empty())
                .or_else(|| attrs.get("href").filter(|value| !value.is_empty()))
                .or_else(|| attrs.get("xlink-href").filter(|value| !value.is_empty()))
                .unwrap_or("")
                .trim();
            let rank = graphic_suitability(attrs, href);
            let owner = self.graphic_owner(false);
            let owner = owner.as_str();
            if owner == "fig" {
                if let Some(frame) = self.figure_stack.last_mut() {
                    frame.builder.graphic.offer(href, rank);
                }
            } else if owner == "table-wrap" {
                if let Some(frame) = self.table_stack.last_mut() {
                    frame.builder.graphic.offer(href, rank);
                }
            } else if !href.is_empty()
                && EXHIBIT_FOOTNOTE_CONTAINERS.contains(&owner)
                && self.owning_exhibit_footnote(false).is_some()
            {
                self.footnote_graphics_dropped += 1;
            }
        } else if name == "table-wrap" {
            self.table_slots.push(None);
            let slot = self.table_slots.len() - 1;
            self.table_stack.push(ExhibitFrame {
                slot,
                builder: TableBuilder {
                    id: attrs.get("id").unwrap_or("").to_string(),
                    ..TableBuilder::default()
                },
            });
        } else if name == "thead" {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.start_header();
            }
        } else if name == "tbody" {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.start_body();
            }
        } else if name == "tr" {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.start_row();
            }
        } else if name == "th" {
            let span = self.cell_span(attrs);
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.start_cell(true, span);
            }
        } else if name == "td" {
            let span = self.cell_span(attrs);
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.start_cell(false, span);
            }
        } else if name == "ref-list" {
            self.in_ref_list = true;
        } else if name == "ref" {
            self.in_ref = true;
            self.current_reference = Some(ReferenceBuilder {
                id: attrs.get("id").unwrap_or("").to_string(),
                ..ReferenceBuilder::default()
            });
        } else if CITATION_ELEMENTS.contains(&name) {
            if self.in_ref {
                let citation_type = attrs
                    .get("citation-type")
                    .unwrap_or("")
                    .trim()
                    .to_lowercase();
                let mut first = false;
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.citation_element_count += 1;
                    // One frame per open element, innermost last: a flag
                    // cleared at an inner open would forget what the outer had
                    // already shown.
                    let fills_identifiers = reference.citation_element_count > 1
                        && citation_type == "display-unstructured";
                    reference.citation_frames.push(CitationFrame {
                        fills_identifiers,
                        ..CitationFrame::default()
                    });
                    first = reference.citation_element_count == 1;
                }
                if first {
                    self.in_ref_citation = true;
                }
            } else {
                // Printed in prose: the close decides whether its text stays in
                // the sentence (#391, #255).
                self.prose_citation_stack
                    .push(ProseCitationFrame::new(name, self.element_stack.len()));
            }
        } else if name == "citation-alternatives" {
            // A group printed in prose is one work, settled at its close
            // (#391). Inside a `<ref>` the group is #407, not ported (#411).
            if !self.in_ref {
                self.prose_citation_stack
                    .push(ProseCitationFrame::new(name, self.element_stack.len()));
            }
        } else if name == "person-group" {
            // Not a related work's byline nested in the citation (issue #270).
            if self.cited_reference() {
                self.in_ref_person_group = true;
            }
        } else if name == "article-id" {
            self.current_article_id_type = attrs
                .get("pub-id-type")
                .filter(|value| !value.is_empty())
                .map(str::to_string);
        } else if name == "pub-id" {
            self.current_pub_id_type = attrs.get("pub-id-type").map(str::to_string);
        } else if name == "pub-date" {
            self.current_pub_date_type = attrs
                .get("pub-type")
                .filter(|value| !value.is_empty())
                .or_else(|| attrs.get("date-type").filter(|value| !value.is_empty()))
                .map(str::to_string);
        } else if name == "award-group" {
            // QUIRK: `<principal-award-recipient>` has no arm at all. Its
            // `<surname>`/`<given-names>` are consumed by their own arms, each
            // accumulating and neither merging back, so the recipient reaches no
            // field of the award — and no article author either (#288, open
            // upstream). The maintainer left it out of #284's structured funding
            // deliberately, as a second design question. Pinned by
            // `a_principal_award_recipient_reaches_no_field` and the
            // `cited/288-*` case.
            self.award_stack.push(AwardFrame::default());
        } else if name == "named-content" && self.is_award_funder_child() {
            self.funder_named_content_types
                .push(attrs.get("content-type").unwrap_or("").to_string());
        } else if name == "xref" {
            self.current_xref_type = attrs.get("ref-type").map(str::to_string);
            self.current_xref_rid = attrs.get("rid").map(str::to_string);
        }
    }

    fn cell_span(&mut self, attrs: &Attrs<'_, '_>) -> i128 {
        let (span, rejected) = read_span(attrs);
        if rejected {
            self.rejected_spans += 1;
        }
        span
    }

    fn characters(&mut self, content: &str) {
        if self.nested_article_depth > 0 {
            return;
        }
        self.append_text(content);
        if self.in_ref && !python_isspace(content) {
            let on_citation_text = self.element_stack.last().is_some_and(|element| {
                MIXED_CONTENT_CITATIONS.contains(&element.as_str()) || element == "x"
            });
            if on_citation_text {
                if let Some(frame) = self
                    .current_reference
                    .as_mut()
                    .and_then(|reference| reference.citation_frames.last_mut())
                {
                    // Character data directly in a citation, or in the `<x>`
                    // that holds a typeset separator, rather than in a field:
                    // the deposit is typeset (issue #390).
                    frame.typeset = true;
                }
            }
        }
        if !python_isspace(content) {
            let on_citation_text = self.element_stack.last().is_some_and(|element| {
                MIXED_CONTENT_CITATIONS.contains(&element.as_str()) || element == "x"
            });
            if let Some(frame) = self.prose_citation_stack.last_mut() {
                // A citation printed in prose (#391): the innermost carries this
                // text, and is typeset by the test above.
                frame.carries_text = true;
                if on_citation_text {
                    frame.typeset = true;
                }
            }
        }
        if !self.formula_stack.is_empty() {
            // A formula's chosen encoding is emitted by its own arm, so its
            // text is held back from the cell here.
            return;
        }
        self.offer_cell_text(content);
    }

    #[allow(clippy::too_many_lines)]
    fn end_element(&mut self, name: &str) {
        let (element_text, merged) = if TEXT_ACCUMULATING.contains(&name) {
            let is_inline = INLINE_ELEMENTS.contains(&name);
            let is_fig_table_xref = name == "xref"
                && matches!(
                    self.current_xref_type.as_deref(),
                    Some("fig" | "figure" | "table" | "table-wrap")
                );
            let is_owned_name = UNDIVIDED_NAME_ELEMENTS.contains(&name)
                && !self.contrib_stack.is_empty()
                && self.contrib_owns_name();
            let is_formula_part = FORMULA_PARTS.contains(&name);
            let is_cell = TABLE_CELL_ELEMENTS.contains(&name);
            let is_claimed =
                CLAIMABLE_ELEMENTS.contains(&name) && self.inside_text_claiming_element();
            let is_funder_identifier = name == "named-content"
                && self.is_award_funder_child()
                && self.funder_identifier_is_open();
            // A citation printed outside a `<ref>` stays in its sentence, whole,
            // where its deposit is typeset (#391, #255): a `<mixed-citation>`,
            // or a `<citation>` carrying text of its own. An element-only one
            // keeps the blank and is counted at its arm.
            let is_prose_citation = CITATION_ELEMENTS.contains(&name)
                && self
                    .closing_prose_citation()
                    .is_some_and(ProseCitationFrame::prints);
            let merge = (is_inline
                || is_prose_citation
                || self.inside_mixed_citation()
                || self.inside_related_work()
                || is_claimed)
                && !is_fig_table_xref
                && !is_owned_name
                && !is_formula_part
                && !is_cell
                && !is_funder_identifier;
            (self.pop_text_buffer(merge), merge)
        } else {
            (self.current_text().to_string(), false)
        };

        if NESTED_ARTICLE_ELEMENTS.contains(&name) && self.nested_article_depth > 0 {
            self.nested_article_depth -= 1;
        }

        let text = element_text.trim().to_string();
        let normalized_text = normalize_whitespace(&element_text);

        if name != "elocation-id"
            && self.current_reference.is_some()
            && !self
                .element_stack
                .iter()
                .any(|element| element == "elocation-id")
        {
            if let Some(reference) = self.current_reference.as_mut() {
                reference.elocation_may_continue = false;
            }
        }

        if self.nested_article_depth > 0 {
            // Still inside a nested article: this close is not the article's.
        } else if name == "front" {
            self.flush_implicit_section();
            self.in_front = false;
        } else if name == "contrib-group" {
            self.contrib_group_stack.pop();
        } else if name == "contrib" {
            if let Some(frame) = self.contrib_stack.pop().flatten() {
                match frame.builder.build() {
                    Some(author) => self.author_slots[frame.slot] = Some(author),
                    None => {
                        // Give the reservation back, so an unfilled slot keeps
                        // meaning "a <contrib> that never closed".
                        //
                        // QUIRK: removing the slot shifts every later slot index
                        // down by one. Python argues this is safe because the
                        // stack is LIFO — every frame with a higher index opened
                        // *inside* this `<contrib>` and has therefore already
                        // been resolved, so the deletion shifts only entries no
                        // live frame indexes. Reproduced: a live frame holding a
                        // stale index would write its contributor onto another's
                        // slot.
                        self.author_slots.remove(frame.slot);
                        self.contribs_naming_nobody += 1;
                    }
                }
            }
        } else if name == "journal-title" {
            if self.in_own_metadata(JOURNAL_META, JOURNAL_TITLE_WRAPPERS) {
                self.journal = text;
            }
        } else if name == "article-id" {
            if self.owned_by(ARTICLE_META) {
                match self.current_article_id_type.as_deref() {
                    Some(declared) => {
                        let id_type = declared.to_lowercase();
                        if id_type == "doi" {
                            self.doi = text;
                            self.doi_is_typed = true;
                        } else if ["pmc", "pmcid", "pmcid-ver", "pmcaid", "pmcaiid"]
                            .contains(&id_type.as_str())
                        {
                            // QUIRK: `pmcid-ver`/`pmcaid`/`pmcaiid` are
                            // recognised as PMC identifiers and then stored
                            // *nowhere* — the guard below admits only the two
                            // canonical spellings. Python reproduces this
                            // deliberately ("store the canonical PMC ID only
                            // from pmc or pmcid variants"), so a document whose
                            // only PMC id is a versioned one reports no
                            // `pmc_id`.
                            if (id_type == "pmc" || id_type == "pmcid") && self.pmc_id.is_empty() {
                                self.pmc_id = text;
                            }
                        } else if id_type == "pmid" || id_type == "pubmed" {
                            self.pmid = text;
                        } else {
                            self.classify_article_id(&text);
                        }
                    }
                    None => self.classify_article_id(&text),
                }
            }
            self.current_article_id_type = None;
        } else if name == "abstract" && self.is_articles_abstract() {
            if !self.current_abstract_text.is_empty() || !self.current_abstract_title.is_empty() {
                let content = self.current_abstract_text.join(" ");
                self.abstract_sections.push(JATSAbstractSection {
                    title: self.current_abstract_title.clone(),
                    content,
                });
            }
            self.in_abstract = false;
        } else if name == "title" {
            let parent = self.parent_element();
            if parent == "caption" {
                self.append_caption_text(&normalized_text);
            } else if self.in_abstract && !(self.in_figure() || self.in_table_wrap()) {
                if !self.current_abstract_text.is_empty() || !self.current_abstract_title.is_empty()
                {
                    let content = self.current_abstract_text.join(" ");
                    self.abstract_sections.push(JATSAbstractSection {
                        title: self.current_abstract_title.clone(),
                        content,
                    });
                    self.current_abstract_text = Vec::new();
                }
                // QUIRK: `text` (end-stripped) rather than `normalized_text`,
                // where the `<sec>` title a few lines down uses the normalised
                // form. An abstract heading that wraps a source line therefore
                // keeps the line break inside `abstract_sections[].title`.
                // Python writes `text` here and normalises for the section, and
                // the asymmetry is reproduced rather than tidied.
                self.current_abstract_title = text.clone();
            } else if parent == "sec" && !self.section_stack.is_empty() {
                self.section_stack.last_mut().unwrap().title = normalized_text.clone();
            } else if !normalized_text.is_empty()
                && EXHIBIT_FOOTNOTE_BLOCKS.contains(&parent.as_str())
                && self.owning_exhibit_footnote(false).is_some()
            {
                self.footnote_headings_dropped += 1;
            } else if self.heading_is_its_containers_own() {
                self.recover_container_heading(&normalized_text);
            }
        } else if name == "p" {
            // A `<p>` inside a prose citation — through its `<annotation>` or
            // `<fn>` — is the citation's text and not a paragraph (#391): it
            // merged into a mixed-content citation at the pop, or is lost and
            // counted with an element-only one. Routed as well, a typeset
            // citation's note was printed twice.
            if !self.paragraph_is_a_prose_citations() {
                self.append_prose(&normalized_text, true, true);
            }
        } else if name == "attrib" {
            let mut credited = self.parent_element();
            if credited == "graphic" {
                credited = self.graphic_owner(true);
            }
            let exhibit = self.exhibit_named(&credited);
            if self.inside_text_claiming_element() {
                // Merged into the citation or the link label at the pop.
            } else if let Some(owner) = exhibit {
                self.append_footnote_to(owner, &normalized_text, false);
            } else if normalized_text.is_empty() || self.inside_declined_metadata() {
                // Declined with the metadata around it.
            } else if self.inside_table_cell() {
                self.append_text(&element_text);
            } else if self.prose_reaches_output() || self.prose_is_refused_apparatus() {
                self.append_prose(&normalized_text, false, false);
            } else {
                self.attributions_dropped += 1;
            }
        } else if name == "funding-statement" {
            if normalized_text.is_empty() || self.inside_declined_metadata() {
                // Nothing stated, or metadata this reader declines.
            } else if self.in_own_metadata(ARTICLE_META, FUNDING_WRAPPERS) {
                self.funding_statements.push(normalized_text);
            } else if self.inside_mixed_citation() || self.inside_table_cell() {
                // Already in the citation string or in the cell.
            } else {
                self.funding_statements_dropped += 1;
            }
        } else if name == "institution-id" {
            let owns = AWARD_FUNDER_ELEMENTS
                .iter()
                .any(|funder| self.owned_by(&["award-group", funder, "institution-wrap"]));
            if !self.award_stack.is_empty()
                && !normalized_text.is_empty()
                && self
                    .award_stack
                    .last()
                    .unwrap()
                    .pending_identifier
                    .is_empty()
                && owns
            {
                self.award_stack.last_mut().unwrap().pending_identifier = normalized_text;
            }
        } else if name == "named-content" {
            if self.is_award_funder_child() && !self.funder_named_content_types.is_empty() {
                let is_identifier = self.funder_identifier_is_open();
                self.funder_named_content_types.pop();
                if is_identifier
                    && !self.award_stack.is_empty()
                    && !normalized_text.is_empty()
                    && self
                        .award_stack
                        .last()
                        .unwrap()
                        .pending_identifier
                        .is_empty()
                {
                    self.award_stack.last_mut().unwrap().pending_identifier = normalized_text;
                }
            }
        } else if AWARD_FUNDER_ELEMENTS.contains(&name) {
            if !self.award_stack.is_empty() && self.parent_element() == "award-group" {
                let identifier = self
                    .award_stack
                    .last_mut()
                    .unwrap()
                    .pending_identifier
                    .clone();
                self.award_stack
                    .last_mut()
                    .unwrap()
                    .pending_identifier
                    .clear();
                if !normalized_text.is_empty() || !identifier.is_empty() {
                    self.award_stack
                        .last_mut()
                        .unwrap()
                        .sources
                        .push(JATSFundingSource {
                            name: normalized_text,
                            identifier,
                        });
                }
            }
        } else if name == "award-id" {
            if !self.award_stack.is_empty()
                && !normalized_text.is_empty()
                && self.parent_element() == "award-group"
            {
                self.award_stack
                    .last_mut()
                    .unwrap()
                    .award_ids
                    .push(normalized_text);
            }
        } else if name == "award-group" {
            if let Some(award) = self.award_stack.pop() {
                if (!award.sources.is_empty() || !award.award_ids.is_empty())
                    && self.in_own_metadata(ARTICLE_META, FUNDING_AWARD_WRAPPERS)
                {
                    self.funding_awards.push(JATSFundingAward {
                        sources: award.sources,
                        award_ids: award.award_ids,
                    });
                }
            }
        } else if name == "alt-text" {
            if !self.formula_stack.is_empty() && !normalized_text.is_empty() {
                let formula = self.formula_stack.last_mut().unwrap();
                if formula.alt_text.is_empty() {
                    formula.alt_text = normalized_text;
                }
            }
        } else if name == "tex-math" {
            if !self.formula_stack.is_empty() {
                self.formula_stack
                    .last_mut()
                    .unwrap()
                    .latex
                    .push(element_text.clone());
            } else {
                let rendered = latex_expression(&element_text, false);
                self.append_text(&rendered);
            }
        } else if FORMULA_ELEMENTS.contains(&name) {
            if let Some(formula) = self.formula_stack.pop() {
                let parent = self.parent_element();
                let standalone =
                    formula.display && !DISPLAY_FORMULA_MERGE_PARENTS.contains(&parent.as_str());
                let numbered = standalone
                    || (formula.display && TABLE_CELL_ELEMENTS.contains(&parent.as_str()));
                let mut rendered = render_formula(
                    &formula.latex,
                    &element_text,
                    &formula.alt_text,
                    &formula.label,
                    formula.display,
                    numbered,
                );
                if standalone {
                    if !rendered.is_empty()
                        && !self.prose_reaches_output()
                        && !self.prose_is_refused_apparatus()
                        && !self.inside_declined_metadata()
                    {
                        self.formulas_dropped += 1;
                    }
                    self.append_prose(&rendered, false, true);
                } else {
                    if !rendered.is_empty() {
                        rendered = pad_as_deposited(&rendered, &element_text, formula.display);
                    }
                    self.append_text(&rendered);
                    if self.formula_stack.is_empty() {
                        self.offer_cell_text(&rendered);
                    }
                }
            }
        } else if name == "body" {
            self.flush_implicit_section();
            self.in_body = false;
        } else if name == "back" {
            self.flush_implicit_section();
            self.in_back = false;
        } else if name == "sec" {
            if !self.in_abstract && !self.section_stack.is_empty() {
                let builder = self.section_stack.pop().unwrap();
                let section = builder.build();
                if let Some(parent) = self.section_stack.last_mut() {
                    parent.subsections.push(section);
                } else {
                    self.body_sections.push(section);
                }
            }
        } else if name == "fig" {
            if let Some(frame) = self.figure_stack.pop() {
                self.figure_slots[frame.slot] = Some(frame.builder.build());
            }
        } else if name == "caption" {
            self.caption_stack.pop();
        } else if name == "label" {
            let parent = self.parent_element();
            if FORMULA_ELEMENTS.contains(&parent.as_str()) && !self.formula_stack.is_empty() {
                self.formula_stack.last_mut().unwrap().label = text;
            } else if parent == "fig" && !self.figure_stack.is_empty() {
                self.figure_stack.last_mut().unwrap().builder.label = text;
            } else if parent == "table-wrap" && !self.table_stack.is_empty() {
                self.table_stack.last_mut().unwrap().builder.label = text;
            } else if parent == "ref" && self.current_reference.is_some() {
                self.current_reference.as_mut().unwrap().label = text;
            } else if parent == "fn" {
                if let Some(ExhibitRef::Figure(index)) = self.owning_exhibit_footnote(false) {
                    let displaced = self.figure_stack[index]
                        .builder
                        .footnotes
                        .hold_footnote_label(&text);
                    if !displaced.is_empty() {
                        self.footnote_markers_dropped += 1;
                    }
                } else if let Some(ExhibitRef::Table(index)) = self.owning_exhibit_footnote(false) {
                    let displaced = self.table_stack[index]
                        .builder
                        .footnotes
                        .hold_footnote_label(&text);
                    if !displaced.is_empty() {
                        self.footnote_markers_dropped += 1;
                    }
                }
            }
        } else if name == "fn" {
            let owner = self.owning_exhibit_footnote(true);
            let lost = match owner {
                Some(ExhibitRef::Figure(index)) => self.figure_stack[index]
                    .builder
                    .footnotes
                    .take_pending_footnote_label(),
                Some(ExhibitRef::Table(index)) => self.table_stack[index]
                    .builder
                    .footnotes
                    .take_pending_footnote_label(),
                None => String::new(),
            };
            if !lost.is_empty() {
                self.footnote_markers_dropped += 1;
            }
        } else if name == "term" {
            if self.parent_element() == "def-item" && !self.def_item_stack.is_empty() {
                let frame = self.def_item_stack.last_mut().unwrap();
                if frame.term.is_some() {
                    self.definition_terms_dropped += 1;
                }
                frame.term = if normalized_text.is_empty() {
                    None
                } else {
                    Some(normalized_text)
                };
            } else if !normalized_text.is_empty() {
                self.definition_terms_dropped += 1;
            }
        } else if name == "def-item" {
            if let Some(closed) = self.def_item_stack.pop() {
                if closed.term.is_some() {
                    self.definition_terms_dropped += 1;
                }
            }
        } else if name == "thead" {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.end_header();
            }
        } else if name == "tbody" {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.end_body();
            }
        } else if name == "tr" {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.end_row();
            }
        } else if TABLE_CELL_ELEMENTS.contains(&name) {
            if let Some(frame) = self.table_stack.last_mut() {
                frame.builder.end_cell();
            } else if !text.is_empty() {
                self.cell_text_dropped += 1;
            }
        } else if name == "table-wrap" {
            if let Some(frame) = self.table_stack.pop() {
                self.table_slots[frame.slot] = Some(frame.builder.build());
            }
        } else if name == "ref-list" {
            self.in_ref_list = false;
        } else if name == "ref" {
            // QUIRK: JATS models `<ref>` as `(label?, (citation |
            // element-citation | mixed-citation | note | p | x)*)`, and a
            // `<note>` has no arm — so a reference that is *only* a note (RSC
            // deposits an explanatory footnote that way) keeps no citation and no
            // structured field, and the HTML reference list gets an empty `<li>`
            // (#150, open upstream). Pinned by
            // `a_ref_of_only_a_note_renders_nothing` and both `cited/150-*` and
            // `build_html/ref_a_note_only_reference_*` corpus cases.
            if let Some(reference) = self.current_reference.as_mut() {
                reference.finish_current_author(false);
                let built = reference.build();
                self.references.push(built);
            }
            self.in_ref = false;
            self.in_ref_citation = false;
            self.in_ref_person_group = false;
            self.current_reference = None;
        } else if CITATION_ELEMENTS.contains(&name) {
            // QUIRK: an element-only citation writes no `citation` string, by
            // design (#146), and only the fields the reference arms read are
            // extracted — so a child no arm reads is dropped with nothing
            // counted and no line logged (#396, open upstream): an `<edition>`,
            // a `<publisher-loc>`/`<publisher-name>`, a `<comment>`, a
            // conference name, or a URL whose text lives only in an attribute.
            // Where *nothing* is read, `formatted_citation` is `""` and the HTML
            // reference list gets an empty `<li>` (#393, open upstream). The
            // module's standing rule is that a drop it argues for is counted and
            // reported once per article; these two are filed as the question of
            // whether these children deserve fields. Reproduced, and pinned by
            // `an_element_only_citation_no_field_reads_renders_nothing`,
            // `a_books_edition_publisher_and_comment_are_dropped` and the
            // `cited/393-*`, `cited/396-*` and `build_html/ref_no_field_reads_*`
            // corpus cases.
            if self.in_ref {
                let mut indented_parts_dropped = 0u32;
                if let Some(reference) = self.current_reference.as_mut() {
                    let frame = reference.citation_frames.pop();
                    let typeset = frame.as_ref().is_some_and(|frame| frame.typeset);
                    if typeset {
                        if let Some(frame) = &frame {
                            if let Some(before) = &frame.elocation_before_indented_join {
                                // Its own text arrived after two locator parts
                                // it joined across whitespace: that whitespace
                                // was printed, so read them as a
                                // `<mixed-citation>`'s are.
                                reference.elocation_id = before.clone();
                                indented_parts_dropped = frame.elocation_parts_indented;
                            }
                        }
                    }
                    // NLM 2.x's `<citation>` is read by its deposit rather
                    // than its spelling: typeset, it writes as a
                    // `<mixed-citation>`; element-only, as an
                    // `<element-citation>`.
                    if name == "mixed-citation" || (name == "citation" && typeset) {
                        // QUIRK: the deposited string is every descendant
                        // concatenated with no separator (the #146 merge), so a
                        // `<name>`'s `<surname>` and `<given-names>` glue
                        // together in it — `KalahastyR, MotatiL` — while the
                        // reference's structured `authors` are right. Real PMC
                        // deposits put no whitespace between a name's parts
                        // (#314, open upstream). Pinned by
                        // `a_names_parts_glue_in_a_mixed_citations_deposit` and
                        // the `cited/314-*` and `build_html/ref_a_glued_*` cases.
                        reference.citation_parts.push(element_text.clone());
                    }
                }
                self.elocation_parts_dropped += indented_parts_dropped;
                self.in_ref_citation = false;
            } else if self.closing_prose_citation().is_some() {
                // Printed in prose (#391, #255): a typeset deposit merged into
                // the sentence at the pop. One that did not merge is a blank,
                // counted unless its text is already in a cell or was declined
                // as metadata.
                if let Some(frame) = self.prose_citation_stack.pop() {
                    let depth = self.element_stack.len();
                    let in_group = self
                        .prose_citation_stack
                        .last()
                        .is_some_and(|outer| outer.is_group() && outer.depth + 1 == depth);
                    if merged {
                        if let Some(outer) = self.prose_citation_stack.last_mut() {
                            if in_group && frame.carries_text {
                                outer.printed = true;
                            }
                            if frame.carries_text {
                                outer.carries_text = true;
                            }
                        }
                    } else if frame.carries_text
                        // A cell a table is collecting: an `<array>`'s has no
                        // builder and received none of the citation's text.
                        && (self.table_stack.is_empty() || !self.inside_table_cell())
                        && !self.inside_declined_metadata()
                    {
                        if in_group {
                            if let Some(outer) = self.prose_citation_stack.last_mut() {
                                outer.lost = true;
                            }
                        } else {
                            self.prose_citations_dropped += 1;
                        }
                    }
                }
            }
        } else if name == "citation-alternatives" {
            if !self.in_ref && self.closing_prose_citation().is_some() {
                if let Some(group) = self.prose_citation_stack.pop() {
                    if group.printed {
                        // A printed rendition carried text into the group.
                        if let Some(outer) = self.prose_citation_stack.last_mut() {
                            outer.carries_text = true;
                        }
                    } else if group.lost {
                        self.prose_citations_dropped += 1;
                    }
                }
            }
        } else if name == "person-group" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.finish_current_author(false);
                }
                self.in_ref_person_group = false;
            }
        } else if name == "surname" {
            // QUIRK: where neither arm below reads the text (nor the matching
            // two in `given-names`), it survives only if the pop above merged
            // it — inside a `<mixed-citation>` or a related work — or a table
            // cell took it from `characters`. Everywhere else it is
            // **discarded**, so a `<name>` printed in prose is cut out of the
            // sentence: Python stores `"Named after in 1990."` for
            // `<p>Named after <name><surname>Jones</surname>…</name> in 1990.</p>`.
            // Reproduced, not fixed: #382 is filed and is outside the plan's list
            // of corrected defects, and `prose/382-a-name-in-a-body-paragraph-is-lost`
            // pins Python's answer against the live library.
            if self.cited_name_part_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.current_author_surname = text;
                }
            } else if self.in_contrib() && self.contrib_owns_name() {
                if let Some(author) = self.current_author_mut() {
                    author.surname = text;
                }
            }
        } else if name == "given-names" {
            if self.cited_name_part_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.current_author_given_names = text;
                }
            } else if self.in_contrib() && self.contrib_owns_name() {
                if let Some(author) = self.current_author_mut() {
                    author.given_names = text;
                }
            }
        } else if name == "name" {
            if self.names_articles_contributor() {
                self.front_contributor_name_count += 1;
            }
            // Every cited `<name>`, in a `<person-group>` or directly in the
            // citation; see `cited_name_part_reference`.
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.finish_current_author(true);
                }
            }
        } else if ALTERNATIVE_NAME_CONTAINERS.contains(&name) {
            // One name spelled several ways, counted here so its members are
            // not counted again. Which member is *extracted* is issue #143's
            // (the last wins today).
            if self.names_articles_contributor() {
                self.front_contributor_name_count += 1;
            }
        } else if name == "collab" {
            if self.names_articles_contributor() {
                self.front_contributor_name_count += 1;
            }
            if self.cited_reference() && !text.is_empty() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.authors.push(normalized_text);
                }
            } else if self.in_contrib() && !text.is_empty() && self.contrib_owns_name() {
                if let Some(author) = self.current_author_mut() {
                    author.collab = text;
                }
            }
        } else if name == "on-behalf-of" {
            // Counted, not extracted. JATS 1.2 admits `<on-behalf-of>` as a
            // `<contrib>`'s name, and an article naming its only contributor
            // that way parses to no authors and then reached the *quiet*
            // branch of the zero-author detector.
            if self.names_articles_contributor() {
                self.front_contributor_name_count += 1;
            }
        } else if name == "string-name" {
            if self.names_articles_contributor() {
                self.front_contributor_name_count += 1;
            }
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    let divided = !reference.current_author_surname.is_empty()
                        || !reference.current_author_given_names.is_empty();
                    if divided {
                        reference.finish_current_author(false);
                    } else if !text.is_empty() {
                        reference.authors.push(normalized_text);
                    }
                }
            } else if self.in_contrib() && !text.is_empty() && self.contrib_owns_name() {
                let structured = self
                    .current_author()
                    .is_some_and(|a| !a.surname.is_empty() || !a.given_names.is_empty());
                if !structured {
                    if let Some(author) = self.current_author_mut() {
                        author.string_name = text;
                    }
                }
            }
        } else if name == "article-title" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.article_title = normalized_text;
                }
            } else if self.in_own_metadata(ARTICLE_META, TITLE_WRAPPERS) {
                self.title = normalized_text;
            }
        } else if name == "source" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.source = text;
                }
            }
        } else if name == "year" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.year = text;
                }
            } else if self.in_own_metadata(ARTICLE_META, YEAR_WRAPPERS) && self.year.is_empty() {
                if names_a_publication_date(self.current_pub_date_type.as_deref()) {
                    self.year = text;
                } else if !text.is_empty() {
                    self.non_publication_years_refused += 1;
                }
            }
        } else if name == "pub-date" {
            self.current_pub_date_type = None;
        } else if name == "volume" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.volume = text;
                }
            } else if !text.is_empty() && self.in_own_metadata(ARTICLE_META, VOLUME_ISSUE_WRAPPERS)
            {
                self.volume = text;
            }
        } else if name == "issue" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.issue = text;
                }
            } else if !text.is_empty() && self.in_own_metadata(ARTICLE_META, VOLUME_ISSUE_WRAPPERS)
            {
                self.issue = text;
            }
        } else if name == "fpage" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.first_page = text;
                }
            } else if !text.is_empty() && self.owned_by(ARTICLE_META) {
                self.pages = text;
                self.page_range_awaits_last_page = true;
            }
        } else if name == "lpage" {
            if self.cited_reference() {
                if let Some(reference) = self.current_reference.as_mut() {
                    reference.last_page = text;
                }
            } else if !text.is_empty() && self.owned_by(ARTICLE_META) {
                if self.page_range_awaits_last_page {
                    self.pages = format!("{}-{}", self.pages, text);
                    self.page_range_awaits_last_page = false;
                } else {
                    self.last_pages_dropped += 1;
                }
            }
        } else if name == "elocation-id" {
            let parent = self.parent_element();
            if self.in_ref_citation
                && self.current_reference.is_some()
                && CITATION_ELEMENTS.contains(&parent.as_str())
            {
                // Read before the reference is borrowed: the buffer belongs to
                // the text stack, and the join rule reads it.
                let buffer = self.current_text().to_string();
                let mut indented_dropped = 0u32;
                if !text.is_empty() {
                    if let Some(reference) = self.current_reference.as_mut() {
                        let stored = reference.elocation_id.clone();
                        if stored.is_empty() {
                            reference.elocation_id = text;
                        } else if text != stored {
                            let joined = format!("{stored}{text}");
                            // An NLM `<citation>` is read by its deposit: one
                            // carrying no text of its own so far is read as
                            // element-only, so the whitespace between its
                            // parts is indentation. That is provisional — its
                            // own text may yet arrive — so a join the typeset
                            // reading would refuse is recorded on the frame
                            // and settled at the citation's close.
                            let mut spelling = parent.clone();
                            let undecided = spelling == "citation"
                                && reference
                                    .citation_frames
                                    .last()
                                    .is_some_and(|frame| !frame.typeset);
                            if undecided {
                                spelling = "element-citation".to_string();
                            }
                            if reference.elocation_may_continue
                                && elocation_part_continues(&buffer, &joined, &spelling)
                            {
                                if undecided
                                    && !elocation_part_continues(&buffer, &joined, "citation")
                                {
                                    if let Some(frame) = reference.citation_frames.last_mut() {
                                        if frame.elocation_before_indented_join.is_none() {
                                            frame.elocation_before_indented_join = Some(stored);
                                        }
                                        frame.elocation_parts_indented += 1;
                                    }
                                }
                                reference.elocation_id = joined;
                            } else {
                                indented_dropped += 1;
                            }
                        }
                        reference.elocation_may_continue = true;
                    }
                }
                self.elocation_parts_dropped += indented_dropped;
            } else if !text.is_empty() && self.owned_by(ARTICLE_META) {
                self.elocation_id = text;
            }
        } else if name == "pub-id" {
            // Read by the declared type first (#397): see `classify_cited_pub_id`.
            if let Some((kind, typed)) =
                classify_cited_pub_id(self.current_pub_id_type.as_deref(), &text)
            {
                if self.cited_reference() {
                    if let Some(reference) = self.current_reference.as_mut() {
                        match kind {
                            CitedId::Pmid => {
                                // Declared, last writer as Python's `main`; a
                                // `medline` number fills only an empty PMID.
                                if typed || reference.pmid.is_empty() {
                                    reference.pmid = text;
                                    reference.pmid_is_typed |= typed;
                                }
                            }
                            CitedId::Doi => {
                                if typed || !reference.doi_is_typed {
                                    reference.doi = text;
                                    reference.doi_is_typed |= typed;
                                }
                            }
                        }
                    }
                } else if self.display_part_reference() {
                    // The display rendering of the work the first part tags
                    // fills an identifier that part left empty, and nothing
                    // else — except that a declared value replaces one taken
                    // untyped. A declared value is never replaced.
                    if let Some(reference) = self.current_reference.as_mut() {
                        match kind {
                            CitedId::Pmid => {
                                if reference.pmid.is_empty() || (typed && !reference.pmid_is_typed)
                                {
                                    reference.pmid = text;
                                    reference.pmid_is_typed |= typed;
                                }
                            }
                            CitedId::Doi => {
                                if reference.doi.is_empty() || (typed && !reference.doi_is_typed) {
                                    reference.doi = text;
                                    reference.doi_is_typed |= typed;
                                }
                            }
                        }
                    }
                }
            }
            // Cleared for every `<pub-id>`, cited or not: the open sets it
            // unconditionally.
            self.current_pub_id_type = None;
        } else if name == "xref" {
            if let (Some(xref_type), Some(rid)) = (
                self.current_xref_type.as_deref(),
                self.current_xref_rid.as_deref(),
            ) {
                if xref_type == "fig" || xref_type == "figure" {
                    let link_text = if text.is_empty() {
                        "Figure".to_string()
                    } else {
                        text.clone()
                    };
                    self.append_text(&format!("[{link_text}](#{rid})"));
                } else if xref_type == "table" || xref_type == "table-wrap" {
                    let link_text = if text.is_empty() {
                        "Table".to_string()
                    } else {
                        text.clone()
                    };
                    self.append_text(&format!("[{link_text}](#{rid})"));
                }
            }
            self.current_xref_type = None;
            self.current_xref_rid = None;
        }

        self.close_container_heading();
        self.element_stack.pop();
    }

    /// Is this `<contrib>` one of the article's authors?
    fn is_author_contrib(&self, contrib_type: Option<&str>) -> bool {
        if let Some(declared) = contrib_type.filter(|value| !value.is_empty()) {
            return declared.to_lowercase() == "author";
        }
        let group_type = self
            .contrib_group_stack
            .iter()
            .rev()
            .find_map(|declared| declared.as_deref().filter(|value| !value.is_empty()));
        match group_type {
            None => true,
            Some(declared) => declared.to_lowercase() == "author",
        }
    }

    /// Classify an `<article-id>` whose `pub-id-type` was absent or unknown.
    fn classify_article_id(&mut self, text: &str) {
        if text.starts_with("10.") && text.contains('/') {
            if !self.doi_is_typed {
                self.doi = text.to_string();
            }
        } else if text.starts_with("PMC") && self.pmc_id.is_empty() {
            self.pmc_id = text.to_string();
        }
        // QUIRK: a bare numeric id of seven digits or more is **dropped**.
        // Python classifies it nowhere and logs at DEBUG ("Never guess"), so an
        // untyped `1234567` contributes no pmid and no pmc_id — reproduced.
    }

    // -- End-of-parse audit -------------------------------------------------

    fn describe_article(&self) -> String {
        for identifier in [&self.pmc_id, &self.doi, &self.pmid] {
            if !identifier.is_empty() {
                return identifier.clone();
            }
        }
        if !self.title.is_empty() {
            let head: String = self.title.chars().take(60).collect();
            return format!("'{head}'");
        }
        "an article carrying no identifier or title".to_string()
    }

    fn routing_flags(&self) -> Vec<String> {
        let mut flags = Vec::new();
        let set: [(usize, bool); 17] = [
            (0, self.in_front),
            (1, self.in_abstract),
            (2, self.in_body),
            (3, self.in_back),
            (4, self.in_ref_list),
            (5, self.in_ref),
            (6, self.in_ref_citation),
            (7, self.in_ref_person_group),
            (8, self.current_reference.is_some()),
            (9, self.current_article_id_type.is_some()),
            (10, self.current_pub_date_type.is_some()),
            (11, self.current_pub_id_type.is_some()),
            (12, self.current_xref_type.is_some()),
            (13, self.current_xref_rid.is_some()),
            (14, self.implicit_body_section.is_some()),
            (15, self.implicit_back_section.is_some()),
            (16, self.implicit_front_section.is_some()),
        ];
        for (index, set_now) in set {
            if set_now && index < ROUTING_FLAG_NAMES.len() {
                flags.push(ROUTING_FLAG_NAMES[index].to_string());
            }
        }
        flags
    }

    fn unwind_state(&self) -> ParseUnwindState {
        ParseUnwindState {
            nested_article_depth: self.nested_article_depth,
            open_sections: self.section_stack.len() as u32,
            open_figures: self.figure_stack.len() as u32,
            open_tables: self.table_stack.len() as u32,
            open_captions: self.caption_stack.len() as u32,
            open_formulas: self.formula_stack.len() as u32,
            open_contrib_groups: self.contrib_group_stack.len() as u32,
            open_contribs: self.contrib_stack.len() as u32,
            open_definition_items: self.def_item_stack.len() as u32,
            open_award_groups: self.award_stack.len() as u32,
            open_funder_named_content: self.funder_named_content_types.len() as u32,
            open_container_headings: self.heading_stack.len() as u32,
            open_prose_citations: self.prose_citation_stack.len() as u32,
            unfilled_author_slots: self
                .author_slots
                .iter()
                .filter(|slot| slot.is_none())
                .count() as u32,
            unfilled_figure_slots: self
                .figure_slots
                .iter()
                .filter(|slot| slot.is_none())
                .count() as u32,
            unfilled_table_slots: self
                .table_slots
                .iter()
                .filter(|slot| slot.is_none())
                .count() as u32,
            excess_text_buffers: self.text_stack.len().saturating_sub(1) as u32,
            // The port has no note spans yet (#426), so it cannot drift.
            misaligned_note_spans: 0,
            open_elements: self.element_stack.clone(),
            stuck_flags: self.routing_flags(),
        }
    }

    /// The WARNING-level tallies, in the order Python's `_audit_parse` reports
    /// them.
    fn warning_lines(&self) -> Vec<String> {
        let mut lines = Vec::new();
        if self.rejected_spans != 0 {
            lines.push(format!(
                "{} table cell(s) declared a colspan this parser would not honour and were \
                 rendered as one column — every later cell in those rows sits one column left \
                 of where the document put it",
                self.rejected_spans
            ));
        }
        if self.contribs_naming_nobody != 0 {
            lines.push(format!(
                "{} <contrib>(s) collected as an author yielded no name bmlib could read, so \
                 those contributors are missing from the author list",
                self.contribs_naming_nobody
            ));
        }
        if self.formulas_dropped != 0 {
            lines.push(format!(
                "{} display formula(s) were rendered but reached no section, caption, cell or \
                 footnote, so their equations are missing from the article (issue #177)",
                self.formulas_dropped
            ));
        }
        if self.refused_apparatus_prose != 0 {
            lines.push(format!(
                "{} <ref-list> item(s) were refused as bibliography apparatus rather than \
                 article prose, so they are missing from the article (issue #224)",
                self.refused_apparatus_prose
            ));
        }
        if self.definition_terms_dropped != 0 {
            lines.push(format!(
                "{} <def-list> term(s) were read and reached no definition this parser could \
                 file, so those words are missing from the article's prose (issue #228)",
                self.definition_terms_dropped
            ));
        }
        if self.footnote_markers_dropped != 0 {
            lines.push(format!(
                "{} footnote marker(s) were read for an exhibit footnote that bmlib filed no \
                 prose for, so those markers are missing from the article (issue #124)",
                self.footnote_markers_dropped
            ));
        }
        if self.footnote_headings_dropped != 0 {
            lines.push(format!(
                "{} heading(s) of an exhibit's footnote block were read and filed nowhere, so \
                 those headings are missing from the article (issue #238)",
                self.footnote_headings_dropped
            ));
        }
        if self.footnote_graphics_dropped != 0 {
            lines.push(format!(
                "{} graphic deposit(s) in an exhibit's footnote matter were read and filed \
                 nowhere, so the image(s) they encode are missing from the article (issue #238)",
                self.footnote_graphics_dropped
            ));
        }
        if self.cell_text_dropped != 0 {
            lines.push(format!(
                "{} table cell(s) carried text that reached no table, no <table-wrap> having \
                 opened one (an <array> in every case measured), so that content is missing \
                 from the article (issue #245)",
                self.cell_text_dropped
            ));
        }
        if self.attributions_dropped != 0 {
            lines.push(format!(
                "{} attribution(s) were read and filed nowhere, so those credits are missing \
                 from the article (issues #241, #248)",
                self.attributions_dropped
            ));
        }
        if self.funding_statements_dropped != 0 {
            lines.push(format!(
                "{} <funding-statement>(s) are not the article's own and were stored nowhere, \
                 so those disclosures are missing from the article (issue #257)",
                self.funding_statements_dropped
            ));
        }
        if self.prose_citations_dropped != 0 {
            lines.push(format!(
                "{} citation(s) deposited outside any <ref> are element-only, authoring no \
                 string of their own to keep in their sentence, so their text is missing from \
                 the article (issue #391)",
                self.prose_citations_dropped
            ));
        }
        if self.elocation_parts_dropped != 0 {
            lines.push(format!(
                "{} <elocation-id> part(s) did not continue the reference's own locator and \
                 were not stored in its elocation_id, which keeps the first (issue #265)",
                self.elocation_parts_dropped
            ));
        }
        if self.last_pages_dropped != 0 {
            lines.push(format!(
                "{} <lpage> value(s) completed no page range this parser had open, so those \
                 page numbers are missing from the article (issue #272)",
                self.last_pages_dropped
            ));
        }
        if self.non_publication_years_refused != 0 && self.year.is_empty() {
            lines.push(format!(
                "{} <pub-date> year(s) name no publication date (a *-submitted or *-release \
                 type) and no other <pub-date> supplied a year, so no year was stored \
                 (issue #261)",
                self.non_publication_years_refused
            ));
        }
        if self.build_authors().is_empty() {
            if self.front_contributor_name_count != 0 {
                lines.push(format!(
                    "produced no authors, but its contributor list named {} contributor(s): they \
                     were most likely routed elsewhere",
                    self.front_contributor_name_count
                ));
            } else {
                lines.push(
                    "produced no authors, and its contributor list named no contributor via \
                     <name>, <string-name>, <collab> or <on-behalf-of>"
                        .to_string(),
                );
            }
        }
        lines
    }

    /// Build the article, snapshot the routing state, and report.
    fn finish(&self) -> JatsReport {
        let article = JATSArticle {
            title: self.title.clone(),
            authors: self.build_authors(),
            journal: self.journal.clone(),
            volume: self.volume.clone(),
            issue: self.issue.clone(),
            pages: self.pages.clone(),
            year: self.year.clone(),
            doi: self.doi.clone(),
            pmc_id: self.pmc_id.clone(),
            pmid: self.pmid.clone(),
            abstract_sections: self.abstract_sections.clone(),
            body_sections: self.body_sections.clone(),
            figures: self.build_figures(),
            tables: self.build_tables(),
            references: self.references.clone(),
            has_body: self.body_paragraph_count > 0,
            suppressed_nested_articles: self.suppressed_nested_articles as usize,
            elocation_id: self.elocation_id.clone(),
            funding_statements: self.funding_statements.clone(),
            funding_awards: self.funding_awards.clone(),
        };
        let unwind = self.unwind_state();
        JatsReport {
            article,
            diagnostics: unwind_diagnostics(&unwind),
            article_label: self.describe_article(),
            unwind,
            warnings: self.warning_lines(),
        }
    }
}
