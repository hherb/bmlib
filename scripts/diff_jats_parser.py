#!/usr/bin/env python3
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

"""Diff two versions of Python's JATS parser over a real artifact.

The Rust port's corpora are evidence only while they regenerate, and a corpus
of crafted documents cannot tell you what a change moves on real markup.  This
answers the other question: given a ``jats_parser.py`` from another commit, how
many articles of a served bundle or a PMC archive does the current parser read
differently, and at which field?

Both versions render through ``rust/oracle/dump_jats.py``'s own renderer, so the
comparison covers every field of ``JATSArticle`` rather than the handful a test
happened to assert.

Usage (from the repository root)::

    # the parser before a change, checked out anywhere:
    git show <commit>:bmlib/fulltext/jats_parser.py > /tmp/jats_parser_before.py
    .venv/bin/python scripts/diff_jats_parser.py \\
        --old-source /tmp/jats_parser_before.py \\
        ~/europepmc/packages/PMC10030002_PMC10040000.xml.gz

Each artifact is either a ``.tar.gz`` of one XML document per member (a PMC
archive) or a gzipped ``<articles>`` concatenation (a Europe PMC served
bundle).  A parse failure on either side is reported as an error diff, and the
tail of the report says how many documents parsed at all — an unchanged article
where *both* readers raised is not a comparison.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import logging
import re
import sys
import tarfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# The oracle's own renderer, so both versions are rendered identically.
sys.path.insert(0, str(ROOT / "rust" / "oracle"))

from dump_jats import render_article  # noqa: E402


def load_parser(source: Path, module_name: str) -> Any:
    """Load a ``jats_parser.py`` from an arbitrary path as its own module.

    Its imports are absolute (``from bmlib.fulltext.models import ...``), so it
    binds to the installed models — which no change under test here touches.
    ``@dataclass`` resolves ``cls.__module__`` through ``sys.modules``, so the
    module must be registered before its body runs.
    """
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load a parser from {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def articles(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, bytes)`` for every article in an artifact."""
    name = str(path)
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path) as tar:
            for member in tar:
                if member.isfile() and member.name.endswith(".xml"):
                    handle = tar.extractfile(member)
                    if handle is not None:
                        yield member.name, handle.read()
        return
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    for index, match in enumerate(re.finditer(r"<article\b.*?</article>", text, re.S)):
        yield f"article_{index}", match.group(0).encode()


def diff(path: str, want: Any, got: Any, moves: dict[str, list[str]], limit: int = 3) -> None:
    """Record every leaf at which ``want`` and ``got`` differ, naming its path."""
    if isinstance(want, dict) and isinstance(got, dict):
        for key in want:
            diff(f"{path}.{key}", want.get(key), got.get(key), moves, limit)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            moves[path + "#len"].append(f"old {len(want)} != new {len(got)}")
        for index, (old_item, new_item) in enumerate(zip(want, got)):
            diff(f"{path}[{index}]", old_item, new_item, moves, limit)
    elif want != got:
        # A list index is a position, not a field: fold them so the report
        # counts `references[].volume` once rather than once per reference.
        key = re.sub(r"\[\d+\]", "[]", path)
        if len(moves[key]) < limit:
            moves[key].append(f"old {want!r} -> new {got!r}")


def render(parser: Any, data: bytes) -> dict[str, Any]:
    try:
        return render_article(parser(data).parse())
    except Exception as exc:  # noqa: BLE001 — the failure is the finding
        return {"__error__": f"{type(exc).__name__}: {exc}"[:200]}


def main() -> int:
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument(
        "--old-source",
        type=Path,
        required=True,
        help="a jats_parser.py from another commit, e.g. `git show <rev>:<path>`",
    )
    parser.add_argument("--limit", type=int, default=0, help="stop after N articles per artifact")
    args = parser.parse_args()

    old_parser = load_parser(args.old_source, "old_jats_parser").JATSParser
    from bmlib.fulltext.jats_parser import JATSParser

    new_parser = JATSParser

    for artifact in args.artifacts:
        moves: dict[str, list[str]] = defaultdict(list)
        counted: Counter = Counter()
        total = changed = errors = parsed = 0
        for name, data in articles(artifact):
            total += 1
            if args.limit and total > args.limit:
                total -= 1
                break
            before = render(old_parser, data)
            after = render(new_parser, data)
            parsed += 1 if "__error__" not in after else 0
            if before == after:
                continue
            changed += 1
            local: dict[str, list[str]] = defaultdict(list)
            diff("article", before, after, local)
            errors += sum(1 for key in local if key.startswith("article.__error__"))
            for key, examples in local.items():
                counted[key] += 1
                if len(moves[key]) < 3:
                    moves[key].extend(f"{name}: {e}" for e in examples)
        print(
            f"{artifact}: {total} articles, {changed} changed, {errors} error-diffs, "
            f"{parsed}/{total} parsed by the current parser"
        )
        for key, count in counted.most_common():
            print(f"  {count:6d}  {key}")
            for example in moves[key][:3]:
                print(f"          {example}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
