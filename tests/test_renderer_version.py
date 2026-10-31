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
change to its code — comments, docstrings and spacing aside — fails this test
until someone decides whether it can
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

from bmlib.fulltext.canonical import (
    CHUNKER_VERSION,
    HTML_CANONICAL_VERSION,
    JATS_CANONICAL_VERSION,
    MARKDOWN_VERSION,
    PDF_CANONICAL_VERSION,
    PLAIN_TEXT_VERSION,
)
from bmlib.fulltext.jats_parser import RENDERER_VERSION

FULLTEXT = Path(__file__).resolve().parents[1] / "bmlib" / "fulltext"

# Every bmlib module whose code can move what ``to_html()`` returns. (The
# standard library can too — ``html.escape``, expat behind ``xml.sax`` — and is
# outside this net.) ``jats_parser`` parses and renders; ``models`` holds the
# dataclasses it fills, and ``_build_html`` renders through their members
# (``JATSAuthorInfo.full_name``, and a reference's ``_author_text``,
# ``_volume_info`` and ``_defers_to_the_deposit``, with what those call, such
# as ``_names_a_work``). ``models`` also holds types the renderer never
# touches — ``FullTextResult``, ``FullTextSourceEntry`` and the segmenter's
# ``TextBlock``/``Section``/``SegmentedDocument`` — so an edit to those trips
# the pin too, and is re-pinned without a bump. Kept covering what the renderer
# imports by ``test_the_digest_covers_every_module_the_renderer_imports``.
RENDERER_SOURCES = ("jats_parser.py", "models.py")

#: What the JATS canonical text is computed by: the reader and its models, and
#: the converter and the layout it hands its drafts to.
CANONICAL_SOURCES = (*RENDERER_SOURCES, "canonical/model.py", "canonical/jats.py")

#: What the plain-text and Markdown canonical texts are computed by (the
#: Markdown converter reads lines with the plain-text one's ``split_lines``).
PLAIN_TEXT_SOURCES = ("canonical/model.py", "canonical/plain.py")
MARKDOWN_SOURCES = (*PLAIN_TEXT_SOURCES, "canonical/markdown.py")

#: What a PDF's canonical text is computed by: the converter, the layout and
#: the paragraph rule it shares with plain text, and the segmenter and PyMuPDF
#: line extraction that build the ``SegmentedDocument`` it reads, with what
#: they import. A segmenter change moves the text as surely as a converter one.
PDF_SOURCES = (
    *PLAIN_TEXT_SOURCES,
    "canonical/pdf.py",
    "models.py",
    "_titles.py",
    "segmenter.py",
    "pdf_converter.py",
)

#: What an HTML page's canonical text is computed by: the converter and the
#: layout. The parser is outside this net: a markupever release (it has no upper
#: bound) or Rust's next html5ever (#558) can move the output with no pin
#: firing, so the shared fixture's malformed-markup cases are what catch that.
HTML_SOURCES = ("canonical/model.py", "canonical/html.py")

#: What the chunks of a canonical text are computed by (the chunker reads the
#: model's spans and ``is_white_space``).
CHUNKER_SOURCES = ("canonical/model.py", "canonical/chunk.py")

#: The version constant each source holds, whose value no digest reads.
VERSION_CONSTANTS = {
    "jats_parser.py": "RENDERER_VERSION",
    "canonical/jats.py": "JATS_CANONICAL_VERSION",
    "canonical/plain.py": "PLAIN_TEXT_VERSION",
    "canonical/markdown.py": "MARKDOWN_VERSION",
    "canonical/pdf.py": "PDF_CANONICAL_VERSION",
    "canonical/html.py": "HTML_CANONICAL_VERSION",
    "canonical/chunk.py": "CHUNKER_VERSION",
}

# bmlib modules the renderer imports whose code cannot reach its output, each
# with the reason — so a new import has to be classified rather than slipping
# past the digest by default.
NOT_RENDERING = {
    # Pure: turns an unwind state into diagnostic strings, which
    # ``jats_parser._audit_parse`` logs; nothing reaches ``_build_html``.
    "bmlib.fulltext._parse_audit",
}

# (RENDERER_VERSION, digest of RENDERER_SOURCES). Re-pin together; see
# ``test_the_version_moves_with_the_renderer`` for what to decide first.
CANONICAL_PINNED = (1, "1680e53bda16b390f35c23aac041e2e86dce744c8edc9bab21c34a85eebcc33c")
PLAIN_TEXT_PINNED = (1, "0bcaf643207714fffded4b3b666145131f2988cf43fc1a21bf57089997c6a34d")
MARKDOWN_PINNED = (1, "8b7077cd0ddc7b5803a465044b9531699f550f4e7c914d77858c6c202179115e")
PDF_PINNED = (3, "9f1e8126d6f32a1ef5c0e9a7afbce4441fe75ffcde66d4feb054c77ea6cfa9bf")
HTML_PINNED = (1, "bf07e27dfcec51a6951a16fa120d1a0ae2bdcb88b21d1be4ef279466388558b4")
CHUNKER_PINNED = (1, "ef8ad47f8c4ee52e960c7349272ff39813c62b0f5ac34b2ba09fc5e41771f845")

PINNED = (7, "23ff1c1901e9967dd44d2416ca9081830797f59b0697098dbc7d8b03d3270e6c")

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
    are positions and the width of an indent, so reflowing a comment or a
    docstring, re-spacing a line or re-indenting a block leaves the digest
    alone — outside string literals: an f-string is kept as spelled, so
    re-spacing inside a replacement field, or a comment inside a multi-line
    f-string on 3.12+, moves it. ``NEWLINE``, ``INDENT`` and ``DEDENT`` are kept: they are syntax,
    and without them moving a statement out of a block would read as no
    change.

    Python 3.12 tokenizes an f-string as ``FSTRING_START`` … ``FSTRING_END``
    with its parts between, where 3.11 gives one ``STRING``; each f-string is
    folded back into one ``STRING`` spelled as the source spells it, so the
    digest is the same on every Python CI runs on (3.11-3.13, and 3.14 checked
    by hand) and not merely on the one that pinned it.
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
        elif name == "INDENT":
            tokens.append((name, ""))
        else:
            tokens.append((name, tok.string))
    return tokens


def _without_the_version(
    tokens: list[tuple[str, str]], constant: str = "RENDERER_VERSION"
) -> list[tuple[str, str]]:
    """Blank the value of ``<constant> = N``, which must appear once, alone.

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
        for i in range(len(tokens) - 3)
        if tokens[i] == ("NAME", constant)
        and tokens[i + 1] == ("OP", "=")
        and tokens[i + 2][0] == "NUMBER"
        and tokens[i + 3 : i + 4] == [("NEWLINE", "\n")]
    ]
    if len(found) != 1:
        raise AssertionError(f"expected one {constant} = <number>, found {len(found)}")
    blanked = list(tokens)
    blanked[found[0] + 2] = ("NUMBER", "<version>")
    return blanked


def _digest(root: Path, sources: tuple[str, ...]) -> str:
    """SHA-256 over the normalised tokens of *sources*, every version value blanked.

    The value of each version constant (:data:`VERSION_CONSTANTS`) is left out
    (:func:`_without_the_version`), so bumping one leaves the digest where it
    was.
    """
    digest = hashlib.sha256()
    for name in sources:
        tokens = normalised_tokens((root / name).read_text(encoding="utf-8"))
        if name in VERSION_CONSTANTS:
            tokens = _without_the_version(tokens, VERSION_CONSTANTS[name])
        digest.update(f"{name}\0".encode())
        for kind, text in tokens:
            digest.update(f"{kind}\0{text}\0".encode())
    return digest.hexdigest()


def renderer_digest(root: Path = FULLTEXT) -> str:
    """SHA-256 over the normalised tokens of every module in RENDERER_SOURCES.

    *root* is the directory the sources are read from, a copy of it in the
    test that the digest moves.
    """
    return _digest(root, RENDERER_SOURCES)


def canonical_digest(root: Path = FULLTEXT) -> str:
    """SHA-256 over the normalised tokens of every module in CANONICAL_SOURCES."""
    return _digest(root, CANONICAL_SOURCES)


def test_the_canonical_version_moves_with_its_converter() -> None:
    actual = (JATS_CANONICAL_VERSION, canonical_digest())
    assert actual == CANONICAL_PINNED, (
        "The JATS canonical text's code changed (the JATS reader, the models, or\n"
        "bmlib/fulltext/canonical/model.py or jats.py).\n"
        "If the change can move what canonical_from_jats() returns for ANY document,\n"
        "bump JATS_CANONICAL_VERSION in canonical/jats.py, so a stored canonical text\n"
        "and everything pointing into it can tell it is stale; a change to assemble()\n"
        "in model.py bumps PLAIN_TEXT_VERSION, MARKDOWN_VERSION, PDF_CANONICAL_VERSION\n"
        "and HTML_CANONICAL_VERSION too. Decide on RENDERER_VERSION beside it.\n"
        "Either way, then re-pin\n"
        f"CANONICAL_PINNED in this file to (JATS_CANONICAL_VERSION, {actual[1]!r})."
    )


@pytest.mark.parametrize(
    ("constant", "version", "sources", "pinned"),
    [
        pytest.param(
            "PLAIN_TEXT_VERSION",
            PLAIN_TEXT_VERSION,
            PLAIN_TEXT_SOURCES,
            PLAIN_TEXT_PINNED,
            id="plain-text",
        ),
        pytest.param(
            "MARKDOWN_VERSION",
            MARKDOWN_VERSION,
            MARKDOWN_SOURCES,
            MARKDOWN_PINNED,
            id="markdown",
        ),
        pytest.param(
            "PDF_CANONICAL_VERSION",
            PDF_CANONICAL_VERSION,
            PDF_SOURCES,
            PDF_PINNED,
            id="pdf",
        ),
        pytest.param(
            "HTML_CANONICAL_VERSION",
            HTML_CANONICAL_VERSION,
            HTML_SOURCES,
            HTML_PINNED,
            id="html",
        ),
        pytest.param(
            "CHUNKER_VERSION",
            CHUNKER_VERSION,
            CHUNKER_SOURCES,
            CHUNKER_PINNED,
            id="chunker",
        ),
    ],
)
def test_a_text_converters_version_moves_with_its_code(
    constant: str, version: int, sources: tuple[str, ...], pinned: tuple[int, str]
) -> None:
    actual = (version, _digest(FULLTEXT, sources))
    assert actual == pinned, (
        f"The code {constant} versions changed ({', '.join(sources)}).\n"
        "If the change can move what the converter returns for ANY text, bump\n"
        f"{constant}; a change to assemble() in model.py bumps every converter's\n"
        "version, JATS_CANONICAL_VERSION included. Either way, then re-pin its\n"
        f"*_PINNED in this file to ({constant}, {actual[1]!r})."
    )


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


class TestTheDigestReadsTheRealSources:
    """``renderer_digest`` itself, over copies of the two files, not just its tokens.

    The pin catches a later change to the digest function, but not a mistake
    already in it when the pin was taken — a source list it never reads, say.
    """

    @pytest.fixture
    def copy(self, tmp_path: Path) -> Path:
        for name in CANONICAL_SOURCES:
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(
                (FULLTEXT / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        return tmp_path

    def test_an_unchanged_copy_digests_as_the_original(self, copy: Path) -> None:
        assert renderer_digest(copy) == renderer_digest()

    @pytest.mark.parametrize("name", RENDERER_SOURCES)
    def test_a_code_change_to_either_file_moves_it(self, copy: Path, name: str) -> None:
        path = copy / name
        path.write_text(path.read_text(encoding="utf-8") + "\n_TRIPWIRE = 1\n", encoding="utf-8")

        assert renderer_digest(copy) != renderer_digest()

    @pytest.mark.parametrize("name", RENDERER_SOURCES)
    def test_a_comment_added_to_either_file_does_not(self, copy: Path, name: str) -> None:
        path = copy / name
        path.write_text(path.read_text(encoding="utf-8") + "\n# a comment\n", encoding="utf-8")

        assert renderer_digest(copy) == renderer_digest()

    def test_a_bump_does_not(self, copy: Path) -> None:
        path = copy / "jats_parser.py"
        text = path.read_text(encoding="utf-8")
        line = f"RENDERER_VERSION = {RENDERER_VERSION}\n"
        assert text.count(line) == 1
        path.write_text(text.replace(line, f"RENDERER_VERSION = {RENDERER_VERSION + 1}\n"))

        assert renderer_digest(copy) == renderer_digest()

    @pytest.mark.parametrize("name", CANONICAL_SOURCES)
    def test_a_code_change_to_a_canonical_source_moves_its_digest(
        self, copy: Path, name: str
    ) -> None:
        path = copy / name
        path.write_text(path.read_text(encoding="utf-8") + "\n_TRIPWIRE = 1\n", encoding="utf-8")

        assert canonical_digest(copy) != canonical_digest()

    def test_a_canonical_bump_does_not(self, copy: Path) -> None:
        path = copy / "canonical" / "jats.py"
        text = path.read_text(encoding="utf-8")
        line = f"JATS_CANONICAL_VERSION = {JATS_CANONICAL_VERSION}\n"
        assert text.count(line) == 1
        path.write_text(
            text.replace(line, f"JATS_CANONICAL_VERSION = {JATS_CANONICAL_VERSION + 1}\n")
        )

        assert canonical_digest(copy) == canonical_digest()


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
    covered = {f"bmlib.fulltext.{name.removesuffix('.py').replace('/', '.')}" for name in sources}
    imported = set().union(*(bmlib_imports(text) for text in sources.values()))
    return imported - covered - NOT_RENDERING


@pytest.mark.parametrize(
    "names",
    [
        RENDERER_SOURCES,
        CANONICAL_SOURCES,
        PLAIN_TEXT_SOURCES,
        MARKDOWN_SOURCES,
        PDF_SOURCES,
        CHUNKER_SOURCES,
    ],
    ids=["renderer", "jats-canonical", "plain-text", "markdown", "pdf", "chunker"],
)
def test_the_digest_covers_every_module_the_renderer_imports(names: tuple[str, ...]) -> None:
    """A bmlib module a pinned source starts importing must be classified.

    Left out of the source list by default, a helper moved out of
    ``jats_parser`` (or a converter) would take its code out of the digest with
    it, and every later change to it would move output unstamped.
    """
    sources = {name: (FULLTEXT / name).read_text(encoding="utf-8") for name in names}

    assert unclassified_imports(sources) == set()


def test_every_exclusion_is_still_imported() -> None:
    sources = {name: (FULLTEXT / name).read_text(encoding="utf-8") for name in RENDERER_SOURCES}
    imported = set().union(*(bmlib_imports(text) for text in sources.values()))

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

    def test_a_source_in_a_subpackage_covers_its_own_module(self):
        sources = {
            "canonical/jats.py": "from bmlib.fulltext.canonical.model import A\n",
            "canonical/model.py": "import re\n",
        }

        assert unclassified_imports(sources) == set()
        assert unclassified_imports({"canonical/jats.py": sources["canonical/jats.py"]}) == {
            "bmlib.fulltext.canonical.model"
        }

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

    def test_re_indenting_a_block_is_not(self):
        assert normalised_tokens("if a:\n    x()\n") == normalised_tokens("if a:\n  x()\n")

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
            pytest.param("RENDERER_VERSION = 1 + 1\n", id="an-expression"),
        ],
    )
    def test_anything_but_one_assignment_fails_closed(self, source):
        with pytest.raises(AssertionError, match="expected one RENDERER_VERSION"):
            _without_the_version(normalised_tokens(source))
