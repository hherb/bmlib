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

"""The renderer version has to move whenever the renderer can (#172).

``FullTextCache`` stamps every rendered entry with
:data:`bmlib.fulltext.jats_parser.RENDERER_VERSION` and treats an older stamp
as a miss, so a change to what ``to_html()`` emits reaches a populated cache
only if the constant was bumped with it. A constant someone has to remember to
bump is the rule-enforced-by-prose this repository keeps mechanising
(``TestTheAuditNetIsComplete``, ``TestEveryCounterIsInAGeneration``), so the
version is pinned here together with a digest of the renderer's source: any
change to that source fails this test until someone decides whether it can
move the output, bumps the version if it can, and re-pins.

The digest is of the *code*, not of the output over some fixture corpus. A
golden-output digest fires only for the shapes its fixtures happen to carry,
and almost every JATS fix moves the output for a shape no fixed corpus held,
so it would pass exactly the changes it exists to catch. The price is that a
change which cannot move the output — a rename, a refactor — also has to be
re-pinned; that costs a line, where a missed bump costs every cache serving a
superseded rendering, silently.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from pathlib import Path

import pytest

from bmlib.fulltext.jats_parser import RENDERER_VERSION

FULLTEXT = Path(__file__).resolve().parents[1] / "bmlib" / "fulltext"

# Every module whose code can move what ``to_html()`` returns. ``jats_parser``
# parses and renders; ``models`` holds the dataclasses it fills, and two of
# their properties (``JATSAuthorInfo.full_name``, ``formatted_citation``) are
# rendered verbatim. Kept equal to what the renderer imports by
# ``test_the_digest_covers_every_module_the_renderer_imports``.
RENDERER_SOURCES = ("jats_parser.py", "models.py")

# bmlib modules the renderer imports whose code cannot reach its output, each
# with the reason — so a new import has to be classified rather than slipping
# past the digest by default.
NOT_RENDERING = {
    # Reads the handler's unwound state and logs; returns nothing that
    # ``_build_html`` consumes.
    "bmlib.fulltext._parse_audit",
}

# (RENDERER_VERSION, digest of RENDERER_SOURCES). Re-pin together; see
# ``test_the_version_moves_with_the_renderer`` for what to decide first.
PINNED = (1, "a32646fa9c2d9b6df2e86743bb345088d32f657e6eadc02bdf1e78f48aa8eb71")

_SKIPPED_TOKENS = {"COMMENT", "NL", "ENCODING", "ENDMARKER"}


def _docstring_starts(tree: ast.Module) -> set[tuple[int, int]]:
    """Where each docstring's first token starts, as tokenize reports it."""
    starts: set[tuple[int, int]] = set()
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            starts.add((first.lineno, first.col_offset))
    return starts


def normalised_tokens(source: str) -> list[tuple[str, str]]:
    """The code of *source* as tokens, without what cannot change behaviour.

    Comments, non-logical line breaks and docstring text are dropped, and so
    are positions, so reflowing a comment or a docstring or re-spacing a line
    leaves the digest alone. ``NEWLINE``, ``INDENT`` and ``DEDENT`` are kept:
    they are syntax, and without them moving a statement out of a block would
    read as no change.

    Python 3.12 tokenizes an f-string as ``FSTRING_START`` … ``FSTRING_END``
    with its parts between, where 3.11 gives one ``STRING``; each f-string is
    folded back into one ``STRING`` spelled as the source spells it, so the
    digest is the same on every Python CI runs (3.11-3.13, and 3.14 checked by
    hand) and not merely on the one that pinned it.
    """
    lines = source.splitlines(keepends=True)

    def spelling(start: tuple[int, int], end: tuple[int, int]) -> str:
        (row0, col0), (row1, col1) = start, end
        if row0 == row1:
            return lines[row0 - 1][col0:col1]
        return lines[row0 - 1][col0:] + "".join(lines[row0 : row1 - 1]) + lines[row1 - 1][:col1]

    docstrings = _docstring_starts(ast.parse(source))
    tokens: list[tuple[str, str]] = []
    depth = 0
    opened_at = (0, 0)
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        name = tokenize.tok_name[tok.type]
        if name in ("FSTRING_START", "TSTRING_START"):
            if depth == 0:
                opened_at = tok.start
            depth += 1
        elif name in ("FSTRING_END", "TSTRING_END"):
            depth -= 1
            if depth == 0:
                tokens.append(("STRING", spelling(opened_at, tok.end)))
        elif depth or name in _SKIPPED_TOKENS:
            continue
        elif name == "STRING" and tok.start in docstrings:
            tokens.append(("DOCSTRING", ""))
        else:
            tokens.append((name, tok.string))
    return tokens


def _without_the_version(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Blank the value of ``RENDERER_VERSION = N``, which must appear once.

    The constant lives in the source it versions, so without this a bump
    would move the digest it is pinned beside and every bump would need two
    edits made in a fixed order. Raises rather than returning the tokens
    unchanged if the assignment is missing or doubled: a digest silently
    covering the version again is the two-edit dance back, and one covering
    no assignment at all means the constant moved somewhere this net does not
    look.
    """
    found = [
        i
        for i in range(len(tokens) - 2)
        if tokens[i] == ("NAME", "RENDERER_VERSION")
        and tokens[i + 1] == ("OP", "=")
        and tokens[i + 2][0] == "NUMBER"
    ]
    if len(found) != 1:
        raise AssertionError(f"expected one RENDERER_VERSION = <number>, found {len(found)}")
    blanked = list(tokens)
    blanked[found[0] + 2] = ("NUMBER", "<version>")
    return blanked


def renderer_digest() -> str:
    """SHA-256 over the normalised tokens of every module in RENDERER_SOURCES.

    The value of ``RENDERER_VERSION`` is left out (:func:`_without_the_version`),
    so bumping it leaves this digest where it was.
    """
    digest = hashlib.sha256()
    for name in RENDERER_SOURCES:
        tokens = normalised_tokens((FULLTEXT / name).read_text(encoding="utf-8"))
        if name == "jats_parser.py":
            tokens = _without_the_version(tokens)
        digest.update(f"{name}\0".encode())
        for kind, text in tokens:
            digest.update(f"{kind}\0{text}\0".encode())
    return digest.hexdigest()


def test_the_version_moves_with_the_renderer() -> None:
    actual = (RENDERER_VERSION, renderer_digest())
    assert actual == PINNED, (
        "The JATS renderer's code changed (bmlib/fulltext/jats_parser.py or models.py).\n"
        "If the change can move what to_html() returns for ANY document, bump\n"
        "RENDERER_VERSION in jats_parser.py, so every cache entry written before it is\n"
        "re-fetched rather than served. Either way, then re-pin PINNED in this file\n"
        f"to (RENDERER_VERSION, {actual[1]!r}) — the digest does not depend on the\n"
        "version, so this one stays valid after the bump."
    )


def bmlib_imports(source: str) -> set[str]:
    """Every bmlib module *source* (a module in ``bmlib.fulltext``) imports.

    A relative import is resolved against ``bmlib.fulltext``: none is written
    today, and an absolute-only walk would let the first one pass unseen.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                package = ["bmlib", "fulltext"][: 3 - node.level]
                imported.add(".".join([*package, *([node.module] if node.module else [])]))
            elif node.module:
                imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return {m for m in imported if m == "bmlib" or m.startswith("bmlib.")}


def unclassified_imports(sources: dict[str, str]) -> set[str]:
    """bmlib modules the renderer imports that the digest neither covers nor excuses."""
    covered = {f"bmlib.fulltext.{name.removesuffix('.py')}" for name in sources}
    imported = set().union(*(bmlib_imports(text) for text in sources.values()))
    return imported - covered - NOT_RENDERING


def test_the_digest_covers_every_module_the_renderer_imports() -> None:
    """A bmlib module the renderer starts importing must be classified.

    Left out of RENDERER_SOURCES by default, a helper moved out of
    ``jats_parser`` would take its code out of the digest with it, and every
    later change to it would reach the cache unstamped.
    """
    sources = {name: (FULLTEXT / name).read_text(encoding="utf-8") for name in RENDERER_SOURCES}
    imported = set().union(*(bmlib_imports(text) for text in sources.values()))

    assert unclassified_imports(sources) == set()
    assert NOT_RENDERING <= imported, "an exclusion the renderer no longer imports is stale"


class TestTheImportNetCanFail:
    """The real sources pass, so on them alone a net that sees nothing passes too."""

    @pytest.mark.parametrize(
        ("line", "module"),
        [
            pytest.param(
                "from bmlib.fulltext.helpers import x\n", "bmlib.fulltext.helpers", id="from"
            ),
            pytest.param(
                "import bmlib.citations.formatter\n", "bmlib.citations.formatter", id="import"
            ),
            pytest.param("from .helpers import x\n", "bmlib.fulltext.helpers", id="relative"),
            pytest.param("from ..citations import y\n", "bmlib.citations", id="parent"),
        ],
    )
    def test_an_unlisted_bmlib_import_is_reported(self, line, module):
        assert unclassified_imports({"jats_parser.py": line}) == {module}

    def test_a_listed_or_excused_import_is_not(self):
        sources = {
            "jats_parser.py": "from bmlib.fulltext.models import A\n"
            "from bmlib.fulltext._parse_audit import B\nimport re\n",
            "models.py": "import re\n",
        }

        assert unclassified_imports(sources) == set()


class TestTheNormalisationIgnoresOnlyWhatCannotChangeBehaviour:
    BASE = 'def f(x):\n    """Doc."""\n    # note\n    return g(x, "a")\n'

    def _same(self, other: str) -> bool:
        return normalised_tokens(other) == normalised_tokens(self.BASE)

    @pytest.mark.parametrize(
        "variant",
        [
            pytest.param(
                'def f(x):\n    """Doc."""\n    # another note\n    return g(x, "a")\n',
                id="comment",
            ),
            pytest.param(
                'def f(x):\n    """A longer\n    docstring."""\n    # note\n    return g(x, "a")\n',
                id="docstring",
            ),
            pytest.param(
                'def f(x):\n    """Doc."""\n    # note\n\n    return g( x,  "a" )\n',
                id="spacing",
            ),
        ],
    )
    def test_what_it_ignores(self, variant):
        assert self._same(variant)

    @pytest.mark.parametrize(
        "variant",
        [
            pytest.param('def f(x):\n    """Doc."""\n    return g(x, "b")\n', id="string-literal"),
            pytest.param('def f(x):\n    """Doc."""\n    return h(x, "a")\n', id="name"),
            pytest.param('def f(x):\n    """Doc."""\n    return g(x)\n', id="argument"),
        ],
    )
    def test_what_it_sees(self, variant):
        assert not self._same(variant)

    def test_moving_a_statement_out_of_a_block_is_a_change(self):
        inside = "if a:\n    x()\n    y()\n"
        outside = "if a:\n    x()\ny()\n"

        assert normalised_tokens(inside) != normalised_tokens(outside)

    def test_an_f_string_is_one_token_spelled_as_written(self):
        # Nested in the other quote: reusing one is legal only from 3.12.
        source = "x = f\"<p>{a!r} and {f'{b}'}</p>\"\n"

        assert normalised_tokens(source) == [
            ("NAME", "x"),
            ("OP", "="),
            ("STRING", "f\"<p>{a!r} and {f'{b}'}</p>\""),
            ("NEWLINE", "\n"),
        ]

    def test_a_string_statement_that_is_not_a_docstring_is_code(self):
        first = 'x = 1\n"not a docstring"\n'
        second = 'x = 1\n"still not one"\n'

        assert normalised_tokens(first) != normalised_tokens(second)


class TestTheVersionItselfIsNotInTheDigest:
    def test_a_bump_leaves_the_tokens_unchanged(self):
        before = normalised_tokens("RENDERER_VERSION = 3\nx = 1\n")
        after = normalised_tokens("RENDERER_VERSION = 4\nx = 1\n")

        assert _without_the_version(before) == _without_the_version(after)

    def test_only_the_value_is_blanked(self):
        before = normalised_tokens("RENDERER_VERSION = 3\nx = 1\n")
        after = normalised_tokens("RENDERER_VERSION = 3\nx = 2\n")

        assert _without_the_version(before) != _without_the_version(after)

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("x = 1\n", id="missing"),
            pytest.param("RENDERER_VERSION = 1\nRENDERER_VERSION = 2\n", id="doubled"),
            pytest.param("RENDERER_VERSION = OTHER\n", id="not-a-number"),
        ],
    )
    def test_anything_but_one_assignment_fails_closed(self, source):
        with pytest.raises(AssertionError, match="expected one RENDERER_VERSION"):
            _without_the_version(normalised_tokens(source))
