# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Write ``tests/fixtures/full_text_assessment.json`` from the Python check.

The shared contract for ``assess_full_text``, which Python and Rust both assert
against. Each case is Markdown (its converter already on contract), and its
expected assessment is the Python check's output, so a case is only as good as
the eye that checked it: every verdict is printed on stderr for that.

    python scripts/make_full_text_assessment.py [tests/fixtures/full_text_assessment.json]

``tests/test_full_text_assessment_contract.py`` checks that this script still
builds the committed fixture, so add a case here and re-run it rather than
editing the JSON by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bmlib.fulltext.canonical import MIN_BODY_WORDS, assess_full_text, canonical_from_markdown

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "full_text_assessment.json"
)


def words(n: int) -> str:
    """*n* words."""
    return " ".join(["word"] * n)


def written_cases() -> list[tuple[str, str]]:
    """``(name, markdown)`` for each case, sized against :data:`MIN_BODY_WORDS`."""
    n = MIN_BODY_WORDS
    row = f"| {words(5)} | {words(5)} |"
    return [
        (
            "imrad-paper",
            f"# Trial\n\n## Introduction\n\n{words(200)}\n\n## Methods\n\n{words(n - 400)}"
            f"\n\n## Results\n\n{words(150)}\n\n## Discussion\n\n{words(50)}",
        ),
        ("methods-and-discussion", f"## Materials and methods\n\n{words(n)}\n\n## Discussion\n\nx"),
        (
            "results-and-discussion-heading",
            f"## 2. Methods\n\n{words(n)}\n\n## 3. Results and discussion\n\nx",
        ),
        (
            "numbered-and-partial-headings",
            f"## 2.1 Statistical methods\n\n{words(n)}\n\n## Trial results:\n\nx",
        ),
        ("no-methods", f"## Introduction\n\n{words(n)}\n\n## Results\n\nx\n\n## Discussion\n\ny"),
        ("methods-only", f"## Methods\n\n{words(n)}"),
        (
            "subsection-after-the-abstract",
            f"## Abstract\n\na\n\n## Methods\n\n### Participants\n\n{words(n)}\n\n## Results",
        ),
        ("one-word-short", f"## Methods\n\n{words(n - 2)}\n\n## Results\n\nx"),
        ("exactly-enough", f"## Methods\n\n{words(n - 1)}\n\n## Results\n\nx"),
        (
            "structured-abstract-page",
            f"# Trial\n\n## Abstract\n\n### Background\n\n{words(100)}\n\n### Methods\n\n"
            f"{words(n)}\n\n### Results\n\n{words(100)}\n\n### Conclusions\n\n{words(50)}"
            f"\n\n## References\n\n{words(n)}",
        ),
        (
            "flat-structured-abstract",
            f"## Abstract\n\n## Methods\n\n{words(150)}\n\n## Results\n\n{words(100)}",
        ),
        (
            "abstract-then-body",
            f"## Abstract\n\n{words(n)}\n\n## Methods\n\n{words(n)}\n\n## Results\n\nx",
        ),
        (
            "references-excluded",
            f"## Methods\n\nx\n\n## Results\n\ny\n\n## References\n\n{words(n)}"
            f"\n\n### Further reading\n\n{words(n)}",
        ),
        ("untitled-lead-counts", f"{words(n)}\n\n## Methods\n\n## Results"),
        ("table-rows-count", "## Methods\n\n" + "\n".join([row] * (n // 10)) + "\n\n## Results"),
        # U+001C is not White_Space; U+00A0 and U+3000 are: four words.
        ("white-space-rule", "## Methods\n\na\u001cb c\u00a0d\u3000e\n\n## Results"),
        ("no-headings", words(n)),
        ("empty", ""),
    ]


def build_contract(show: bool = False) -> dict[str, Any]:
    """The whole contract; with *show*, each case's verdict on stderr."""
    contract: dict[str, Any] = {"min_body_words": MIN_BODY_WORDS, "cases": []}
    for name, markdown in written_cases():
        expected = assess_full_text(canonical_from_markdown(markdown)).to_dict()
        contract["cases"].append({"name": name, "markdown": markdown, "expected": expected})
        if show:
            print(f"{name}: {expected}", file=sys.stderr)
    return contract


def main() -> None:
    """Write the contract to the path given, or to the committed fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    text = json.dumps(build_contract(show=True), ensure_ascii=False, indent=1) + "\n"
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
