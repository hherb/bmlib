# bmlib::citations — Citation Markers, Styles and Reference Lists

Manuscript text carries citation markers like `[@id:12345:Smith2023]`. This
module parses them, numbers the cited documents by first appearance, formats
references in Vancouver, APA, Harvard or Chicago style, replaces the markers
with inline citations, and appends a markdown reference list. It also locates
an LLM's near-verbatim quote in its source (`find_passage`). The Python page
is [citations.md](../../../../docs/manual/citations.md); this one describes
the Rust API and where it differs.

| Need | Feature |
|---|---|
| Everything in this module | none |

Pure functions over strings: no database, no network. The caller supplies
metadata as a `HashMap<i64, DocumentMetadata>`.

## Formatting a document

```rust
use std::collections::HashMap;
use bmlib::citations::{format_document, CitationStyle, DocumentMetadata};

let mut smith = DocumentMetadata::new(1, "Statin therapy and LDL reduction");
smith.authors = vec!["John Smith".into(), "Anna Johnson".into()];
smith.journal = Some("Journal of Lipidology".into());
smith.year = Some(2021);
smith.volume = Some("12".into());
smith.issue = Some("3".into());
smith.pages = Some("200-210".into());
smith.doi = Some("10.1000/jlip.2021.001".into());

let mut doe = DocumentMetadata::new(2, "Cardiovascular outcomes of statins");
doe.authors = vec!["Jane Doe".into()];
doe.journal = Some("Heart Journal".into());
doe.year = Some(2022);

let metadata = HashMap::from([(1, smith), (2, doe)]);
let text = "Statins lower LDL [@id:1:Smith2021] [@id:2:Doe2022] in most patients.\n";

let out = format_document(text, &metadata, CitationStyle::Vancouver, true, true);
assert!(out.starts_with("Statins lower LDL [1,2] in most patients.\n\n---\n\n## References\n"));
assert!(out.contains(
    "1. Smith J, Johnson A. Statin therapy and LDL reduction. *Journal of Lipidology*. \
     2021;12(3):200-210. doi:10.1000/jlip.2021.001"
));
```

`format_document(text, metadata, style, include_reference_list,
combine_sequential)` takes every argument Python defaults; `DEFAULT_CITATION_STYLE`
is `CitationStyle::Vancouver`. The list begins `"\n---"`, as in Python, so end
the text with a newline if the rule should not sit under its last line.

`build_references(text, metadata, style, combine_sequential)` is the same
without the appended list. It returns `(String, Vec<FormattedReference>)`
(`number`, `document_id`, `formatted_text`, `metadata`), to render yourself;
`build_references_default(text, metadata)` uses Vancouver and combining.

- Numbering is by first appearance; a repeat reuses its number.
- In Vancouver, markers separated only by whitespace or commas combine:
  three in a row become `[1-3]` (two stay `[1,2]`); `combine_sequential =
  false` gives `[1,2,3]`.
- APA, Harvard and Chicago replace each marker with the style's author–date
  citation, `(Smith & Johnson, 2021)` in APA.
- A cited id missing from `metadata` yields a visible `N. [Document N not
  found]` reference. Its marker becomes `[N]` in Vancouver and stays verbatim
  in the author–date styles. `find_missing_documents(text, metadata)` returns
  one `Citation` per unresolved marker; check it before publishing.

```rust
use std::collections::HashMap;
use bmlib::citations::{build_references, find_missing_documents, CitationStyle, DocumentMetadata};

let mut doc = DocumentMetadata::new(1, "A trial");
doc.authors = vec!["John Smith".into(), "Anna Johnson".into()];
doc.year = Some(2021);
let metadata = HashMap::from([(1, doc)]);
let text = "See [@id:1:Smith2021] and [@id:9:Gone2020].";

let (apa, references) = build_references(text, &metadata, CitationStyle::Apa, true);
assert_eq!(apa, "See (Smith & Johnson, 2021) and [@id:9:Gone2020].");
assert_eq!(references[1].formatted_text, "2. [Document 9 not found]");
assert_eq!(find_missing_documents(text, &metadata)[0].document_id, 9);
```

## Markers

```rust
use bmlib::citations::{
    create_citation_marker, extract_document_id_from_citation, parse_citations, validate_marker,
    DocumentMetadata,
};

let mut doc = DocumentMetadata::new(12345, "T");
doc.authors = vec!["John Smith".into()];
doc.year = Some(2023);
let marker = create_citation_marker(12345, &doc.generate_label());
assert_eq!(marker, "[@id:12345:Smith2023]");

assert_eq!(validate_marker(&marker), Ok(()));
assert!(validate_marker(&format!("{marker} junk")).is_err()); // the whole string must be one marker
assert_eq!(extract_document_id_from_citation(&marker), Some(12345));

let found = parse_citations("Ünïcode [@id:7:Lee2020]");
assert_eq!(found[0].position, 8); // a char offset, not a byte offset
```

Python's `validate_citation_marker` returns `(bool, str | None)`; Rust's is
`validate_marker`, returning `Result<(), String>` with the same messages. The
other parsing functions keep their Python names and meanings:
`unique_document_ids`, `count_citations`, `count_unique_citations`,
`citation_positions` (a `Vec<(i64, Vec<usize>)>` in first-appearance order,
where Python returns a dict), `citations_in_range`, `find_adjacent_citations`,
`format_citation_group`, `replace_citation_with_number`,
`replace_all_citations_with_numbers`, `extract_label_from_citation`. Rust
adds `marker_spans(text)`, every marker's `(start, end)` char span.

Two differences in the scanner. `CITATION_PATTERN` is the pattern as a plain
`&str`, not a compiled regex; the scanner is hand-written. And the id must be
ASCII digits, where Python's `\d` also accepts other scripts' digits.

## Styles

`CitationFormatter::new(style)` formats one reference
(`format_reference(&metadata, number)`), one inline citation
(`format_inline_citation(&metadata, number)`) or a whole list
(`format_reference_list(&references)`); `set_style` switches it in place,
where Python assigns `formatter.style`. `CitationFormatter::available_styles()`
and `style_description(style)` feed a style picker, as do
`CitationStyle::all()`, `as_str()` (`"vancouver"`, …) and `description()`.

```rust
use bmlib::citations::{CitationFormatter, CitationStyle, DocumentMetadata};

let mut doc = DocumentMetadata::new(7, "T");
doc.authors = vec!["John Smith".into()];
doc.year = Some(2023);

let mut formatter = CitationFormatter::new(CitationStyle::Apa);
assert_eq!(formatter.format_inline_citation(&doc, None), "(Smith, 2023)");
formatter.set_style(CitationStyle::Vancouver);
assert_eq!(formatter.format_inline_citation(&doc, Some(4)), "[4]");
```

The single-style formatters (`VancouverFormatter`, `ApaFormatter`,
`HarvardFormatter`, `ChicagoFormatter`) implement the `StyleFormatter` trait,
Python's `BaseFormatter`; a custom style implements its two methods.
`CitationFormatter` only selects among the four built-in ones. Author lists
truncate beyond `MAX_AUTHORS_BEFORE_ET_AL` (6), each style its own way, and
`author_surname` is the surname rule they share (`"Unknown"` for a blank
name). A blank author entry counts as no author everywhere
(`DocumentMetadata::named_authors`).

`DocumentMetadata::from_json` / `to_json` and `FormattedReference::from_json`
/ `to_json` are Python's `from_dict` / `to_dict`; `from_json` accepts `id` for
`document_id` and `authors` as a list or a `;`- or `,`-separated string, as
Python does. `Citation` compares by all four fields, as in Python.

## Locating a quoted passage

`find_passage(passage, text, min_similarity)` finds where an LLM's quote
really is in its source and returns the source's own wording. It forgives
case, whitespace, typographic quotes and dashes, accents and compatibility
forms, and rejects a paraphrase below the threshold; an exact match never
starts or ends inside a word. The Python page's section on it applies here
unchanged — the two are held to the same shared fixture — except for the
types:

```rust
use bmlib::citations::{find_passage, InvalidSimilarity};

let source = "Patients were randomised.  The \u{201c}hazard ratio\u{201d} was 0.81.";
let found = find_passage("the \"hazard ratio\" was 0.81", source, 0.95)?;
let span = found.span.as_ref().expect("found");
assert!(found.exact);
assert_eq!(span.text, "The \u{201c}hazard ratio\u{201d} was 0.81");
assert_eq!((span.start, span.end), (27, 54)); // char indices, not bytes

assert!(!find_passage("HR 0.9", "HR 0.95", 0.95)?.matched());
assert!(find_passage("x", source, 1.5).is_err());
# Ok::<(), InvalidSimilarity>(())
```

The span is `Option<PassageSpan>` (`start`, `end`, `text`), in `char` indices;
`matched()` is `span.is_some()`. Python's `start`/`end`/`text` sit on the
match itself. An invalid `min_similarity`, NaN included, is an
`Err(InvalidSimilarity)`. `PassageMatch` and `PassageSpan` are
`#[non_exhaustive]`, so only `find_passage` builds them. `fold_char`,
`normalize_with_offsets` and `occurs_once` are the public normalisation, as in
Python.
