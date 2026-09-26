#!/usr/bin/env python3
"""Dump bmlib.citations' behaviour as JSON, for the Rust port to be diffed against.

The port plan (docs/plans/2026-09-26-rust-port-roadblocks.md, §7) calls for a
differential oracle that compares *semantic* fields against the Python
implementation, not JSON text. This is the citations half of it.

It reads a JSON list of cases on stdin and writes a JSON list of results on
stdout. Each case names one public function and its arguments; the result is
whatever that function returned, normalised so a diff is readable.

Run from the repository root, with the Python library importable:

    .venv/bin/python rust/oracle/dump_citations.py < rust/oracle/cases.json
"""

from __future__ import annotations

import json
import sys

from bmlib.citations import (
    author_surname,
    build_references,
    citation_positions,
    citations_in_range,
    count_citations,
    count_unique_citations,
    create_citation_marker,
    extract_document_id_from_citation,
    extract_label_from_citation,
    find_adjacent_citations,
    find_missing_documents,
    format_citation_group,
    format_document,
    parse_citations,
    replace_all_citations_with_numbers,
    replace_citation_with_number,
    unique_document_ids,
    validate_citation_marker,
)
from bmlib.citations.formatter import CitationFormatter
from bmlib.citations.models import CitationStyle, DocumentMetadata


def metadata_from(spec: dict) -> DocumentMetadata:
    """Build metadata from either key spelling the library accepts.

    `DocumentMetadata.from_dict` reads `id` then `document_id`, so a spec
    carrying `document_id` works only when nothing else is wrong. This
    normalises to `id` explicitly so the corpus does not rely on that
    fallback, and refuses a non-integer id rather than letting `int()` raise
    somewhere less obvious.
    """
    spec = dict(spec)
    if "id" not in spec and "document_id" in spec:
        spec["id"] = spec["document_id"]
    spec.pop("document_id", None)
    return DocumentMetadata.from_dict(spec)


def run(case: dict):
    fn = case["fn"]
    args = case.get("args", {})

    def metadata_map() -> dict:
        """The `metadata` argument as an id-keyed map.

        Two shapes are in play: `format_reference` and friends take one
        `DocumentMetadata`, while `build_references` and friends take a map of
        them. Building the map eagerly for every case is what made a single
        document read as a map keyed by its own field names.
        """
        return {int(k): metadata_from(v) for k, v in args["metadata"].items()}

    if fn == "author_surname":
        return author_surname(args["author"])
    if fn == "parse_citations":
        return [c.to_dict() for c in parse_citations(args["text"])]
    if fn == "unique_document_ids":
        return unique_document_ids(args["text"])
    if fn == "count_citations":
        return count_citations(args["text"])
    if fn == "count_unique_citations":
        return count_unique_citations(args["text"])
    if fn == "citation_positions":
        return {str(k): v for k, v in citation_positions(args["text"]).items()}
    if fn == "citations_in_range":
        return [c.to_dict() for c in citations_in_range(args["text"], args["start"], args["end"])]
    if fn == "create_citation_marker":
        return create_citation_marker(args["document_id"], args["label"])
    if fn == "replace_citation_with_number":
        return replace_citation_with_number(args["text"], args["document_id"], args["number"])
    if fn == "replace_all_citations_with_numbers":
        return replace_all_citations_with_numbers(
            args["text"], {int(k): v for k, v in args["id_to_number"].items()}
        )
    if fn == "find_adjacent_citations":
        return [[c.to_dict() for c in g] for g in find_adjacent_citations(args["text"])]
    if fn == "format_citation_group":
        from bmlib.citations.models import Citation

        group = [Citation(**c) for c in args["citations"]]
        return format_citation_group(
            group, {int(k): v for k, v in args["id_to_number"].items()}, args.get("combine_sequential", True)
        )
    if fn == "validate_citation_marker":
        ok, reason = validate_citation_marker(args["marker"])
        return {"ok": ok, "reason": reason}
    if fn == "extract_label_from_citation":
        return extract_label_from_citation(args["marker"])
    if fn == "extract_document_id_from_citation":
        return extract_document_id_from_citation(args["marker"])
    if fn == "format_reference":
        f = CitationFormatter(CitationStyle(args["style"]))
        return f.format_reference(metadata_from(args["metadata"]), args.get("number"))
    if fn == "format_inline_citation":
        f = CitationFormatter(CitationStyle(args["style"]))
        return f.format_inline_citation(metadata_from(args["metadata"]), args.get("number"))
    if fn == "generate_label":
        return metadata_from(args["metadata"]).generate_label()
    if fn == "first_author_surname":
        return metadata_from(args["metadata"]).get_first_author_surname()
    if fn == "build_references":
        text, refs = build_references(
            args["text"], metadata_map(), CitationStyle(args.get("style", "vancouver")), args.get("combine_sequential", True)
        )
        return {"text": text, "references": [r.to_dict() for r in refs]}
    if fn == "format_document":
        return format_document(
            args["text"],
            metadata_map(),
            CitationStyle(args.get("style", "vancouver")),
            args.get("include_reference_list", True),
            args.get("combine_sequential", True),
        )
    if fn == "find_missing_documents":
        return [c.to_dict() for c in find_missing_documents(args["text"], metadata_map())]
    raise ValueError(f"unknown fn {fn!r}")


def main() -> int:
    cases = json.load(sys.stdin)
    results = []
    for case in cases:
        try:
            results.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001 - the oracle records failures too
            results.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(results, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
