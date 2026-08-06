# bmlib.citations — Citation Markers, Styles & Reference Lists

Citation-aware document tooling. Manuscript text carries inline citation markers in the `[@id:12345:Smith2023]` format; this package parses them, numbers the cited documents in order of first appearance, formats bibliographic references in Vancouver, APA, Harvard, or Chicago style, replaces the markers with inline citations (numbered or author–date, per style), and appends a markdown reference list.

Everything is pure stdlib and pure functions over strings: no database, no network, no optional dependency group. Document metadata is supplied by the caller as a `Mapping[int, DocumentMetadata]` — the upstream bmlibrarian implementation fetched it from PostgreSQL, and that coupling was severed in the port.

## Installation

```bash
pip install bmlib          # no extras needed
```

## Quick start

```python
from bmlib.citations import DocumentMetadata, format_document

metadata = {
    1: DocumentMetadata(
        document_id=1,
        title="Statin therapy and LDL reduction",
        authors=["John Smith", "Anna Johnson"],
        journal="Journal of Lipidology",
        year=2021,
        volume="12",
        issue="3",
        pages="200-210",
        doi="10.1000/jlip.2021.001",
    ),
    2: DocumentMetadata(
        document_id=2,
        title="Cardiovascular outcomes of statins",
        authors=["Jane Doe"],
        journal="Heart Journal",
        year=2022,
        doi="10.1000/heart.2022.042",
    ),
}

text = "Statins lower LDL [@id:1:Smith2021] [@id:2:Doe2022] in most patients."
print(format_document(text, metadata))
```

Output:

```markdown
Statins lower LDL [1,2] in most patients.
---

## References

1. Smith J, Johnson A. Statin therapy and LDL reduction. *Journal of Lipidology*. 2021;12(3):200-210. doi:10.1000/jlip.2021.001

2. Doe J. Cardiovascular outcomes of statins. *Heart Journal*. 2022. doi:10.1000/heart.2022.042
```

The appended list begins directly with `\n---` (upstream-faithful), so a document that does not end with a blank line puts the `---` rule immediately under the last line — which markdown renders as a setext heading. End your text with a blank line if you want the rule to render as a rule.

`build_references()` is the same operation without the appended list — it returns `(formatted_text, references)` where `references` is a `list[FormattedReference]` you can render yourself.

## The marker format

A marker is `[@id:<document_id>:<label>]`: the integer document id and a human-readable label, conventionally first-author surname plus year.

```python
from bmlib.citations import DocumentMetadata, create_citation_marker, validate_citation_marker

metadata = DocumentMetadata(document_id=12345, title="T", authors=["John Smith"], year=2023)
label = metadata.generate_label()                 # "Smith2023"
marker = create_citation_marker(12345, label)     # "[@id:12345:Smith2023]"

assert validate_citation_marker(marker) == (True, None)
assert validate_citation_marker(marker + " junk")[0] is False   # whole string must be the marker
```

`validate_citation_marker()` checks the whole string (a positive integer id, a label of at most 100 characters); `extract_document_id_from_citation()` and `extract_label_from_citation()` read one marker string and return `None` for anything else.

## Parsing utilities

All pure functions over the text, from `bmlib.citations`:

| Function | Returns |
|----------|---------|
| `parse_citations(text)` | Every marker as a `Citation` (id, label, position, text), in order of appearance |
| `unique_document_ids(text)` | Unique cited ids, order of first appearance |
| `count_citations(text)` / `count_unique_citations(text)` | Marker count / distinct-document count |
| `citation_positions(text)` | `dict[int, list[int]]` — marker offsets per document |
| `citations_in_range(text, start, end)` | Citations whose marker starts in `[start, end)` |
| `find_adjacent_citations(text)` | Markers separated only by whitespace/commas, grouped |
| `format_citation_group(citations, id_to_number)` | One group as `[1,2]`, `[1-3]`, or `[1-3,5]` |
| `replace_citation_with_number(text, document_id, number)` | One document's markers → `[N]` |
| `replace_all_citations_with_numbers(text, id_to_number)` | All mapped markers → `[N]`; unmapped markers stay |

## Citation styles

`CitationStyle` has four members; `DEFAULT_CITATION_STYLE` is `VANCOUVER`. One reference formatted per style, from the same metadata:

```
Vancouver: 1. Smith J, Johnson A, Williams B. Title of the article. *Journal Name*. 2023;45(2):123-134. doi:10.1234/example
APA:       Smith, J., Johnson, A., & Williams, B. (2023) Title of the article. *Journal Name*, *45*(2), 123-134. https://doi.org/10.1234/example
Harvard:   Smith, J., Johnson, A. and Williams, B. (2023) 'Title of the article', *Journal Name*, 45(2), pp. 123-134. doi: 10.1234/example.
Chicago:   Smith, John, Anna Johnson, and Brian Williams. 2023. "Title of the article." *Journal Name* 45 (2): 123-134. https://doi.org/10.1234/example.
```

Inline citations follow the style too: Vancouver renders `[N]`, APA `(Smith et al., 2023)`, Harvard `(Smith and Johnson, 2023)`, Chicago `(Smith et al. 2023)`. Author lists truncate beyond `MAX_AUTHORS_BEFORE_ET_AL` (6) authors, each style in its own shape (Vancouver/Harvard append `et al`, APA elides the middle authors with `...` and keeps the last).

The `CitationFormatter` facade selects the style at runtime:

```python
from bmlib.citations import CitationFormatter, CitationStyle, DocumentMetadata

metadata = DocumentMetadata(document_id=7, title="T", authors=["John Smith"], year=2023)
formatter = CitationFormatter(CitationStyle.APA)
formatter.format_inline_citation(metadata)   # "(Smith, 2023)"
formatter.style = CitationStyle.VANCOUVER    # switch in place
formatter.format_inline_citation(metadata, number=4)   # "[4]"
```

The style classes (`VancouverFormatter`, `APAFormatter`, `HarvardFormatter`, `ChicagoFormatter`) are public for callers that want exactly one style; `CitationFormatter.get_available_styles()` and `get_style_description()` support building a style picker.

Journal names are italicised with markdown asterisks — the output is markdown, not plain text.

## Building reference lists

`build_references(text, metadata, style=DEFAULT_CITATION_STYLE, combine_sequential=True)`:

- **Numbering** is by order of first appearance in the text, and repeats reuse their number.
- **Adjacent markers** (separated only by whitespace/commas) combine in Vancouver output: `[1] [2] [3]` → `[1-3]`; pass `combine_sequential=False` for `[1,2,3]`.
- **Author–date styles** replace each marker with the style's inline citation — `(Doe & Roe, 2022)` — not a number.
- **A cited id missing from `metadata`** yields a visible `N. [Document N not found]` placeholder reference rather than disappearing. Its inline marker becomes `[N]` in Vancouver; in author–date styles the marker stays verbatim, since an author–date citation needs the metadata's surname.

```python
from bmlib.citations import build_references, find_missing_documents

formatted, references = build_references(text, metadata)
for reference in references:
    print(reference.number, reference.formatted_text)

missing = find_missing_documents(text, metadata)   # one Citation per unresolved marker
```

Check `find_missing_documents()` before publishing output — a placeholder in a reference list is visible, but it is still a placeholder.

## Differences from bmlibrarian

The port is output-faithful to upstream's code, with five defects fixed (each carries a named regression test) and the app-editor pieces left behind. Full reasoning: `docs/superpowers/specs/2026-08-06-citations-port-design.md`.

- `DocumentMetadata.from_dict()` no longer shatters a semicolon-separated author string of inverted names (`"Smith, John; Doe, Jane"` was four authors upstream). A *lone* inverted name as a bare string (`"Smith, John"`, no semicolon) is inherently ambiguous and still splits into two authors — pass `authors` as a list when exactness matters.
- `validate_citation_marker()` validates the whole string; upstream anchored only the start, so trailing junk validated.
- Author–date styles get author–date inline citations; upstream replaced markers with `[N]` in every style.
- APA/Chicago author blocks no longer double the terminal period (`"Williams, B.."`).
- A whitespace-only author entry no longer crashes reference formatting (upstream raised `IndexError` in every style); blank entries are dropped, and an all-blank list reads "Unknown author".
- The stateless parser class became module functions; `Citation` compares by all fields (upstream: by `document_id` alone); `WritingDocument`, `DocumentVersion`, `document_store`, and the editor/autosave constants were not ported.
