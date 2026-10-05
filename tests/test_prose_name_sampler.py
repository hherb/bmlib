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

"""Tests for ``scripts/measure_jats_prose_names.py`` (issue #382's instrument).

The instrument's claim is about **routing** — which of a structured name's
contexts the parser reads, keeps, glues or drops — so the central test here
parses one fixture per context with the real parser and holds the instrument's
fate to what the parse kept. The script itself restates the parser's sets and
imports nothing from ``bmlib``; the agreement lives here, where it is checked.

Its first cut had no such test, and PR #389's review found it filing 3.7
million names the parser then dropped as read or glued: every NLM 2.x
``<citation>``, every bare name in an ``<element-citation>`` (authors since PR
#387), and every citation printed in prose outside a ``<ref>``.
"""

from __future__ import annotations

import dataclasses
import gzip
import importlib.util
import io
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext import jats_parser
from bmlib.fulltext.jats_parser import JATSParser

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "measure_jats_prose_names.py"
_spec = importlib.util.spec_from_file_location("bmlib_prose_name_sampler", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the instrument from {_PATH}")
sampler = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sampler
_spec.loader.exec_module(sampler)

# A surname no fixture prints anywhere else, so its presence in the parse is
# the name's and nothing else's.
SURNAME = "Zyxwvut"
NAME = f"<name><surname>{SURNAME}</surname><given-names>Qponml</given-names></name>"
PARTS_IN_STRING_NAME = (
    f"<string-name><surname>{SURNAME}</surname> <given-names>Qponml</given-names></string-name>"
)


def _article(*, meta: str = "", body: str = "", back: str = "") -> str:
    return (
        '<?xml version="1.0"?><article><front><article-meta>'
        f'<article-id pub-id-type="pmc">PMC1</article-id>{meta}</article-meta></front>'
        f"<body><sec><title>S</title><p>Body.</p>{body}</sec></body><back>{back}</back></article>"
    )


def _ref(citation: str) -> str:
    return f'<ref-list><ref id="r1">{citation}</ref></ref-list>'


def _alternatives(*parts: str) -> str:
    return _ref(f"<citation-alternatives>{''.join(parts)}</citation-alternatives>")


# A later <citation-alternatives> rendition that tags a name (issue #417).
LATER_TAGGED = (
    f"<element-citation><person-group>{NAME}</person-group><source>J</source></element-citation>"
)


# (label, expected context, document). One row per context at least; the
# anti-vacuity test below holds the set of contexts to all of them.
FIXTURES: list[tuple[str, str, str]] = [
    (
        "person-group in a mixed-citation",
        "citation-author",
        _article(
            back=_ref(f"<mixed-citation><person-group>{NAME}</person-group>. T.</mixed-citation>")
        ),
    ),
    (
        "person-group in an element-citation",
        "citation-author",
        _article(
            back=_ref(
                f"<element-citation><person-group>{NAME}</person-group>"
                "<source>J</source></element-citation>"
            )
        ),
    ),
    (
        "an author contributor's own name",
        "contributor-own",
        _article(
            meta=f'<contrib-group><contrib contrib-type="author">{NAME}</contrib></contrib-group>'
        ),
    ),
    (
        "a name in a table cell",
        "table-cell",
        _article(
            body=f"<table-wrap><table><tbody><tr><td>{NAME}</td></tr></tbody></table></table-wrap>"
        ),
    ),
    (
        "a related work's byline in a paragraph",
        "related-work-in-prose",
        _article(
            body=f"<p>Reply to <related-article><person-group>{NAME}</person-group>"
            "</related-article> here.</p>"
        ),
    ),
    (
        "a bare name in a mixed-citation (PR #387)",
        "citation-author",
        _article(back=_ref(f"<mixed-citation>{NAME}. T.</mixed-citation>")),
    ),
    (
        "a person-group in a ref's second mixed-citation (#149)",
        "mixed-citation-glued",
        _article(
            back=_ref(
                "<mixed-citation>First.</mixed-citation>"
                f"<mixed-citation><person-group>{NAME}</person-group>. T.</mixed-citation>"
            )
        ),
    ),
    (
        "a related work's byline inside a mixed-citation (#270)",
        "mixed-citation-glued",
        _article(
            back=_ref(
                "<mixed-citation>T. Erratum in: <related-object><person-group>"
                f"{NAME}</person-group></related-object>.</mixed-citation>"
            )
        ),
    ),
    (
        "a related work's byline inside an element-citation (#270)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<element-citation><source>J</source><related-object><person-group>"
                f"{NAME}</person-group></related-object></element-citation>"
            )
        ),
    ),
    (
        "a bare name in an element-citation (PR #387)",
        "citation-author",
        _article(back=_ref(f"<element-citation>{NAME}<source>J</source></element-citation>")),
    ),
    (
        "a string-name depositing its parts in a mixed-citation",
        "citation-author",
        _article(back=_ref(f"<mixed-citation>{PARTS_IN_STRING_NAME}. T.</mixed-citation>")),
    ),
    (
        "a string-name depositing its parts in an element-citation (#415)",
        "citation-author",
        _article(
            back=_ref(
                f"<element-citation>{PARTS_IN_STRING_NAME}<source>J</source></element-citation>"
            )
        ),
    ),
    (
        "a person-group in a ref's second element-citation (#149)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<element-citation><source>First</source></element-citation>"
                f"<element-citation><person-group>{NAME}</person-group>"
                "<source>J</source></element-citation>"
            )
        ),
    ),
    (
        "a person-group in an element-only NLM 2.x citation (#390)",
        "citation-author",
        _article(back=_ref(f"<citation><person-group>{NAME}</person-group></citation>")),
    ),
    (
        "a bare name in a typeset NLM 2.x citation (#390)",
        "citation-author",
        _article(back=_ref(f"<citation>{NAME}. T. J 1999.</citation>")),
    ),
    (
        "a string-name depositing its parts in an NLM 2.x citation",
        "citation-author",
        _article(back=_ref(f"<citation>{PARTS_IN_STRING_NAME}<source>J</source></citation>")),
    ),
    (
        "a name in a ref's second, typeset citation",
        "nlm-citation-glued",
        _article(
            back=_ref(f"<citation><source>First</source></citation><citation>By {NAME}.</citation>")
        ),
    ),
    (
        "a name in a ref's second, element-only citation",
        "nlm-citation-unread",
        _article(
            back=_ref(
                "<citation><source>First</source></citation>"
                f"<citation><person-group>{NAME}</person-group><source>J</source></citation>"
            )
        ),
    ),
    (
        "a later alternative's person-group, the first naming nobody (#407, #417)",
        "citation-author",
        _article(back=_alternatives("<mixed-citation>Foo.</mixed-citation>", LATER_TAGGED)),
    ),
    (
        "a later alternative's divided string-name, the first naming nobody (#417)",
        "citation-author",
        _article(
            back=_alternatives(
                "<mixed-citation>Foo.</mixed-citation>",
                f"<element-citation>{PARTS_IN_STRING_NAME}<source>J</source></element-citation>",
            )
        ),
    ),
    (
        "a later alternative's name, the first naming a consortium (#407)",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<mixed-citation><collab>WHO</collab>. Foo.</mixed-citation>", LATER_TAGGED
            )
        ),
    ),
    (
        "a later alternative's name, the first naming an undivided string-name (#407)",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<mixed-citation><string-name>Jane Roe</string-name>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium only a marker (#425)",
        "citation-author",
        _article(
            back=_alternatives(
                '<mixed-citation><collab><xref ref-type="fn" rid="f1">*</xref></collab>. Foo.'
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a third alternative's name, the second having named a consortium (#407)",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<mixed-citation>Foo.</mixed-citation>",
                "<element-citation><collab>WHO</collab><source>J</source></element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a name in a second citation-alternatives group (#407)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<citation-alternatives><mixed-citation>Foo.</mixed-citation>"
                f"</citation-alternatives><citation-alternatives>{LATER_TAGGED}"
                "</citation-alternatives>"
            )
        ),
    ),
    (
        "a name in a group after a first citation deposited bare (#407)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<mixed-citation>Foo.</mixed-citation>"
                f"<citation-alternatives>{LATER_TAGGED}</citation-alternatives>"
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium in a related work (#270)",
        "citation-author",
        _article(
            back=_alternatives(
                "<mixed-citation>Foo. <related-object><collab>Rel</collab></related-object>"
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, a consortium cited in the first's note (#414)",
        "citation-author",
        _article(
            back=_alternatives(
                "<mixed-citation>Foo.<annotation><p><element-citation><collab>WHO</collab>"
                "</element-citation></p></annotation></mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, a group nested in the first's note (#407)",
        "citation-author",
        _article(
            back=_alternatives(
                "<element-citation><source>J</source><annotation><p><citation-alternatives>"
                "<mixed-citation>X</mixed-citation></citation-alternatives></p></annotation>"
                "</element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        # Not JATS, which admits only citation elements in the group, but expat
        # parses it: the parser asks for the group's direct child.
        "a name in a citation inside, not directly in, the first's group",
        "element-citation-unread",
        _article(
            back=_alternatives("<mixed-citation>Foo.</mixed-citation>", f"<x>{LATER_TAGGED}</x>")
        ),
    ),
    (
        "a citation nested in an element-citation's note (#414)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<element-citation><source>J</source><annotation><p><mixed-citation>"
                f"<person-group>{NAME}</person-group>. T.</mixed-citation></p></annotation>"
                "</element-citation>"
            )
        ),
    ),
    (
        "a citation nested in a cell of a citation's alternatives table (#414)",
        "isolated-cell",
        _article(
            back=_ref(
                "<mixed-citation>Foo <alternatives><table><tbody><tr><td><element-citation>"
                f"<person-group>{NAME}</person-group></element-citation></td></tr></tbody>"
                "</table></alternatives>.</mixed-citation>"
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium text only in a <p>",
        "citation-author",
        _article(
            back=_alternatives(
                "<element-citation><collab><bio><p>About</p></bio></collab>"
                "<source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's string-name only declined metadata",
        "citation-author",
        _article(
            back=_alternatives(
                "<element-citation><string-name><inline-graphic><alt-text>Logo</alt-text>"
                "</inline-graphic></string-name><source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium only a note",
        "citation-author",
        _article(
            back=_alternatives(
                "<mixed-citation><collab><fn><p>note</p></fn></collab>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium only a roster (#429)",
        "citation-author",
        _article(
            back=_alternatives(
                "<element-citation><collab><contrib-group><contrib><aff>Uni</aff></contrib>"
                "</contrib-group></collab><source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium a marker and whitespace",
        "citation-author",
        _article(
            back=_alternatives(
                '<mixed-citation><collab> <xref ref-type="fn" rid="f1">*</xref> </collab>. Foo.'
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium named after its marker",
        "element-citation-unread",
        _article(
            back=_alternatives(
                '<mixed-citation><collab><xref ref-type="fn" rid="f1">*</xref>WHO</collab>. Foo.'
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's empty consortium after a named one",
        "element-citation-unread",
        _article(
            back=_alternatives(
                '<mixed-citation><collab>WHO</collab><collab><xref ref-type="fn" rid="f">*'
                "</xref></collab>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        # Not JATS, which admits only citation elements in a group, but expat
        # parses it: the innermost group open is the one asked.
        "a later alternative in a group directly inside the group holding the first",
        "citation-author",
        _article(
            back=_ref(
                "<citation-alternatives><citation-alternatives><mixed-citation>Foo."
                f"</mixed-citation>{LATER_TAGGED}</citation-alternatives></citation-alternatives>"
            )
        ),
    ),
    (
        "a name in a group nested in the first's note (#407, #414)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<citation-alternatives><element-citation><source>J</source><annotation><p>"
                f"<citation-alternatives><mixed-citation><person-group>{NAME}</person-group>"
                "</mixed-citation></citation-alternatives></p></annotation></element-citation>"
                "</citation-alternatives>"
            )
        ),
    ),
    (
        "a mononym in a person-group: given names alone close a <name>",
        "citation-author",
        _article(
            back=_ref(
                f"<element-citation><person-group><name><given-names>{SURNAME}</given-names>"
                "</name></person-group><source>F</source></element-citation>"
            )
        ),
    ),
    (
        "given names awaiting a surname that never comes (#417)",
        "element-citation-unread",
        _article(
            back=_ref(
                "<element-citation><person-group><string-name><given-names>"
                f"{SURNAME}</given-names></string-name></person-group><source>F</source>"
                "</element-citation>"
            )
        ),
    ),
    (
        "given names awaiting a surname in a mixed-citation, glued into its string",
        "mixed-citation-glued",
        _article(
            back=_ref(
                "<mixed-citation><person-group><string-name><given-names>"
                f"{SURNAME}</given-names></string-name></person-group>. T.</mixed-citation>"
            )
        ),
    ),
    (
        "a consortium carrying name parts in a mixed-citation, read whole",
        "citation-author",
        _article(
            back=_ref(
                f"<mixed-citation><collab><surname>{SURNAME}</surname></collab>. T."
                "</mixed-citation>"
            )
        ),
    ),
    (
        "a name in a cell of the first citation's alternatives table",
        "citation-author",
        _article(
            back=_ref(
                "<mixed-citation>Foo <alternatives><table><tbody><tr><td>"
                f"{NAME}</td></tr></tbody></table></alternatives>.</mixed-citation>"
            )
        ),
    ),
    (
        "a name in a cell of a prose citation's alternatives table",
        "isolated-cell",
        _article(
            body="<p>See <citation>Foo <alternatives><table><tbody><tr><td>"
            f"{NAME}</td></tr></tbody></table></alternatives>.</citation> here.</p>"
        ),
    ),
    (
        "a related work's byline in a cell of an array in a paragraph",
        "isolated-cell",
        _article(
            body="<p>See <array><tbody><tr><td><related-object><person-group>"
            f"{NAME}</person-group></related-object></td></tr></tbody></array> here.</p>"
        ),
    ),
    (
        "a related work's byline in a ref-list's own paragraph (#224 refuses it)",
        "related-work-metadata",
        _article(
            back="<ref-list><p>See <related-object><person-group>"
            f"{NAME}</person-group></related-object>.</p></ref-list>"
        ),
    ),
    (
        "a related work's byline in a floats-group's paragraph (#253)",
        "related-work-metadata",
        _article(
            back="</back><floats-group><boxed-text><p>See <related-object><person-group>"
            f"{NAME}</person-group></related-object>.</p></boxed-text></floats-group><back>"
        ),
    ),
    (
        "an NLM 2.x citation in a floats-group's paragraph (#253)",
        "citation-in-prose",
        _article(
            back="</back><floats-group><boxed-text><p>See <citation><person-group>"
            f"{NAME}</person-group><source>J</source></citation>.</p></boxed-text>"
            "</floats-group><back>"
        ),
    ),
    (
        "an editor contributor's own name (#111)",
        "contributor-declined",
        _article(
            meta=f'<contrib-group><contrib contrib-type="editor">{NAME}</contrib></contrib-group>'
        ),
    ),
    (
        "a contributor in a group declaring editors (#111)",
        "contributor-declined",
        _article(
            meta=f'<contrib-group content-type="editor"><contrib>{NAME}</contrib></contrib-group>'
        ),
    ),
    (
        "an author contributor's own name in a group declaring authors",
        "contributor-own",
        _article(
            meta=f'<contrib-group content-type="author"><contrib>{NAME}</contrib></contrib-group>'
        ),
    ),
    (
        "an author contributor outside the article's own list (#266)",
        "contributor-declined",
        _article(
            meta='<supplement><contrib-group><contrib contrib-type="author">'
            f"{NAME}</contrib></contrib-group></supplement>"
        ),
    ),
    (
        # Not JATS: the parser holds one reference open, and the inner one's
        # close leaves none for the outer's citation.
        "a citation after a <ref> nested in its own <ref>",
        "reference-discarded",
        _article(
            back='<ref-list><ref id="r1"><ref id="r2"><mixed-citation>Inner.</mixed-citation>'
            f"</ref><element-citation><person-group>{NAME}</person-group><source>J</source>"
            "</element-citation></ref></ref-list>"
        ),
    ),
    (
        "a citation before a <ref> nested in its own <ref>",
        "reference-discarded",
        _article(
            back=f'<ref-list><ref id="r1"><mixed-citation><person-group>{NAME}</person-group>'
            '. T.</mixed-citation><ref id="r2"><mixed-citation>Inner.</mixed-citation></ref>'
            "</ref></ref-list>"
        ),
    ),
    (
        # Not JATS: the innermost group open is the one a citation is asked about.
        "a later rendition in a group nested directly in the first's group",
        "element-citation-unread",
        _article(
            back=_ref(
                "<citation-alternatives><mixed-citation>Foo.</mixed-citation>"
                f"<citation-alternatives>{LATER_TAGGED}</citation-alternatives>"
                "</citation-alternatives>"
            )
        ),
    ),
    (
        "a mononym string-name in an element-citation, outside any group (#415)",
        "citation-author",
        _article(
            back=_ref(
                f"<element-citation><string-name><given-names>{SURNAME}</given-names>"
                "</string-name><source>F</source></element-citation>"
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium printing an <fn>'s own text",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<mixed-citation><collab><fn>Note</fn></collab>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's consortium in a mixed-citation's <label>",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<mixed-citation><collab><label>WHO</label></collab>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's element-only consortium in <italic>",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<element-citation><collab><italic>WHO</italic></collab><source>F</source>"
                "</element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a later alternative's name, the first's element-only consortium a formula",
        "element-citation-unread",
        _article(
            back=_alternatives(
                "<element-citation><collab><inline-formula><tex-math>X</tex-math>"
                "</inline-formula></collab><source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
    ),
    (
        "a name in a paragraph in a cell of an array",
        "other",
        _article(
            body=f"<array><tbody><tr><td><p>Named after {NAME}.</p></td></tr></tbody></array>"
        ),
    ),
    (
        "a ref's second citation, the ref in a cell of an array",
        "mixed-citation-glued",
        _article(
            body='<p>See</p><array><tbody><tr><td><ref-list><ref id="r1"><mixed-citation>'
            f"First.</mixed-citation><mixed-citation><person-group>{NAME}</person-group>. T."
            "</mixed-citation></ref></ref-list></td></tr></tbody></array>"
        ),
    ),
    (
        "a person-group in an nlm-citation (#390)",
        "citation-author",
        _article(back=_ref(f"<nlm-citation><person-group>{NAME}</person-group></nlm-citation>")),
    ),
    (
        "a person-group in a ref's second nlm-citation",
        "nlm-citation-unread",
        _article(
            back=_ref(
                "<nlm-citation><source>First</source></nlm-citation>"
                f"<nlm-citation><person-group>{NAME}</person-group></nlm-citation>"
            )
        ),
    ),
    (
        "an NLM 2.x citation printed in a paragraph",
        "nlm-citation-in-prose",
        _article(
            body=f"<p>See <citation><person-group>{NAME}</person-group>"
            "<source>J</source></citation> here.</p>"
        ),
    ),
    (
        "an NLM 2.x citation standing in a section, in no paragraph",
        "citation-in-prose",
        _article(
            body=f"<citation><person-group>{NAME}</person-group><source>J</source></citation>"
        ),
    ),
    (
        "an NLM 2.x citation in a ref-list's own paragraph (#224 refuses it)",
        "citation-in-prose",
        _article(
            back="<ref-list><p>See <citation>"
            f"<person-group>{NAME}</person-group><source>J</source></citation>.</p></ref-list>"
        ),
    ),
    (
        "an NLM 2.x citation in a related article's metadata",
        "citation-in-prose",
        _article(
            meta='<related-article related-article-type="corrected-article"><citation>'
            f"<person-group>{NAME}</person-group><source>J</source></citation></related-article>"
        ),
    ),
    (
        "a name in a ref's second citation, punctuated only in <x>",
        "nlm-citation-glued",
        _article(
            back=_ref(
                "<citation><source>First</source></citation>"
                f"<citation><person-group>{NAME}</person-group><x>. </x>"
                "<source>J</source></citation>"
            )
        ),
    ),
    (
        "an nlm-citation printed in a paragraph",
        "citation-in-prose",
        _article(
            body=f"<p>See <nlm-citation><person-group>{NAME}</person-group>"
            "<source>J</source></nlm-citation> here.</p>"
        ),
    ),
    (
        "a mixed-citation printed in a paragraph",
        "citation-in-prose",
        _article(
            body=f"<p>See <mixed-citation><person-group>{NAME}</person-group>. T."
            "</mixed-citation> here.</p>"
        ),
    ),
    (
        "an element-citation printed in a paragraph",
        "citation-in-prose",
        _article(
            body=f"<p>Data: <element-citation><person-group>{NAME}</person-group>"
            "<source>Zenodo</source></element-citation> here.</p>"
        ),
    ),
    (
        "a product's byline in article-meta",
        "related-work-metadata",
        _article(meta=f"<product><person-group>{NAME}</person-group><source>B</source></product>"),
    ),
    (
        "a name in a contributor's bio (#382)",
        "contributor-prose",
        _article(
            meta='<contrib-group><contrib contrib-type="author"><string-name>Jane Smith'
            f"</string-name><bio><p>Trained with {NAME}.</p></bio></contrib></contrib-group>"
        ),
    ),
    (
        "a name in a body paragraph (#382)",
        "other",
        _article(body=f"<p>Named after {NAME} in 1990.</p>"),
    ),
    (
        "a string-name depositing its parts in a body paragraph",
        "other",
        _article(body=f"<p>Named after {PARTS_IN_STRING_NAME} in 1990.</p>"),
    ),
    (
        "a funding award's recipient",
        "other",
        _article(
            meta="<funding-group><award-group><principal-award-recipient>"
            f"{NAME}</principal-award-recipient></award-group></funding-group>"
        ),
    ),
]


def _named(surname: str) -> str:
    return NAME.replace(SURNAME, surname)


# (label, document, [(marker, expected context)] per holder in document order):
# documents carrying several holders, each holder's own fate held to the
# parser's by a marker printed nowhere else in the document — or, for a
# holder carrying no text, no marker, there being nothing to lose.
SEVERAL: list[tuple[str, str, list[tuple[str | None, str]]]] = [
    (
        "the first stores an author: the later alternative's is discarded (#407)",
        _article(
            back=_alternatives(
                f"<mixed-citation><person-group>{_named('Wvqxpo')}</person-group>. Foo."
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "citation-author"), (SURNAME, "element-citation-unread")],
    ),
    (
        "an empty <name> stores no author, so the later one's fills the list",
        _article(
            back=_alternatives(
                "<mixed-citation><person-group><name><surname/></name></person-group>. Foo."
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [(None, "citation-author"), (SURNAME, "citation-author")],
    ),
    (
        "a whitespace surname stores no author either",
        _article(
            back=_alternatives(
                "<mixed-citation><person-group><name><surname> </surname></name>"
                "</person-group>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [(None, "citation-author"), (SURNAME, "citation-author")],
    ),
    (
        "a <name> holding only a suffix stores no author, and prints its suffix",
        _article(
            back=_alternatives(
                "<mixed-citation><person-group><name><surname/><suffix>Jr</suffix></name>"
                "</person-group>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [("Jr", "mixed-citation-glued"), (SURNAME, "citation-author")],
    ),
    (
        "an empty <name> after a named one leaves the list stored",
        _article(
            back=_alternatives(
                f"<mixed-citation><person-group>{_named('Wvqxpo')}<name><surname/></name>"
                "</person-group>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [
            ("Wvqxpo", "citation-author"),
            (None, "citation-author"),
            (SURNAME, "element-citation-unread"),
        ],
    ),
    (
        "a related work's byline is not the reference's (#270)",
        _article(
            back=_alternatives(
                "<mixed-citation>Foo. <related-object><person-group>"
                f"{_named('Wvqxpo')}</person-group></related-object></mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "mixed-citation-glued"), (SURNAME, "citation-author")],
    ),
    (
        "a name in a citation nested in the first's note stores nothing (#414)",
        _article(
            back=_alternatives(
                "<element-citation><source>J</source><annotation><p><mixed-citation>"
                f"<person-group>{_named('Wvqxpo')}</person-group></mixed-citation></p>"
                "</annotation></element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "element-citation-unread"), (SURNAME, "citation-author")],
    ),
    (
        "a name in a group nested in the first's note stores nothing (#407, #414)",
        _article(
            back=_alternatives(
                "<element-citation><source>J</source><annotation><p><citation-alternatives>"
                f"<mixed-citation><person-group>{_named('Wvqxpo')}</person-group>"
                "</mixed-citation></citation-alternatives></p></annotation></element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "element-citation-unread"), (SURNAME, "citation-author")],
    ),
    (
        "the first's given names await a surname, so the later one's fills the list",
        _article(
            back=_alternatives(
                "<mixed-citation><person-group><string-name><given-names>Wvqxpo"
                "</given-names></string-name></person-group>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "mixed-citation-glued"), (SURNAME, "citation-author")],
    ),
    (
        "the first's group holds given names alone, so the later one's fills the list",
        _article(
            back=_alternatives(
                "<element-citation><person-group><given-names>Wvqxpo</given-names>"
                "</person-group><source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "element-citation-unread"), (SURNAME, "citation-author")],
    ),
    (
        "pending given names make an undivided string-name flush, storing neither",
        _article(
            back=_alternatives(
                "<element-citation><person-group><string-name><given-names>Wvqxpo"
                "</given-names></string-name><string-name>Roe J</string-name></person-group>"
                "<source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "element-citation-unread"), (SURNAME, "citation-author")],
    ),
    (
        "the first's string-name read verbatim round an empty part stores it",
        _article(
            back=_alternatives(
                "<mixed-citation><string-name><surname/>Wvqxpo Roe</string-name>. Foo."
                "</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "citation-author"), (SURNAME, "element-citation-unread")],
    ),
    (
        "the first's element-only string-name read whole round an empty part",
        _article(
            back=_alternatives(
                "<element-citation><string-name>Wvqxpo Roe<surname/></string-name>"
                "<source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "citation-author"), (SURNAME, "element-citation-unread")],
    ),
    (
        "an editor split across two groups is reassembled (Wiley)",
        _article(
            back=_ref(
                "<element-citation><person-group><string-name><given-names>Wvqxpo"
                "</given-names></string-name></person-group><person-group><string-name>"
                f"<surname>{SURNAME}</surname></string-name></person-group><source>F</source>"
                "</element-citation>"
            )
        ),
        [("Wvqxpo", "citation-author"), (SURNAME, "citation-author")],
    ),
    (
        "an empty surname leaves the given names it follows to be overwritten",
        _article(
            back=_ref(
                "<element-citation><person-group><string-name><given-names>Wvqxpo"
                f"</given-names><surname/></string-name><name><given-names>{SURNAME}"
                "</given-names></name></person-group><source>F</source></element-citation>"
            )
        ),
        [("Wvqxpo", "element-citation-unread"), (SURNAME, "citation-author")],
    ),
    (
        "each <person-group> holding bare parts flushes its own author",
        _article(
            back=_ref(
                "<element-citation><person-group><surname>Wvqxpo</surname></person-group>"
                f"<person-group><surname>{SURNAME}</surname></person-group>"
                "<source>F</source></element-citation>"
            )
        ),
        [("Wvqxpo", "citation-author"), (SURNAME, "citation-author")],
    ),
    (
        "a surname deposited after a person-group closed is not read",
        _article(
            back=_ref(
                f"<mixed-citation><person-group>{_named('Wvqxpo')}</person-group>"
                f"<surname>{SURNAME}</surname>. T.</mixed-citation>"
            )
        ),
        [(SURNAME, "mixed-citation-glued"), ("Wvqxpo", "citation-author")],
    ),
    (
        "a related work's person-group sets no flag for a part after it (#270)",
        _article(
            back=_ref(
                f"<mixed-citation><related-object><person-group>{_named('Wvqxpo')}"
                f"</person-group></related-object><surname>{SURNAME}</surname>. T."
                "</mixed-citation>"
            )
        ),
        [("Wvqxpo", "mixed-citation-glued"), (SURNAME, "mixed-citation-glued")],
    ),
    (
        # Not JATS: the inner <ref>'s close leaves no reference and no group open.
        "a <ref> nested in a person-group leaves no flag to the next <ref>",
        _article(
            back='<ref-list><ref id="r1"><element-citation><person-group><ref id="r2">'
            "<mixed-citation>Inner.</mixed-citation></ref></person-group></element-citation>"
            f'</ref><ref id="r3"><mixed-citation><surname>{SURNAME}</surname>. T.'
            "</mixed-citation></ref></ref-list>"
        ),
        [(SURNAME, "mixed-citation-glued")],
    ),
    (
        "a later alternative's name, the first's string-name a marker round empty parts",
        _article(
            back=_alternatives(
                '<mixed-citation><string-name><surname/><xref ref-type="fn" rid="f1">Wvqxpo'
                "</xref></string-name>. Foo.</mixed-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "mixed-citation-glued"), (SURNAME, "citation-author")],
    ),
    (
        "the first's surname only a figure link stores the link (`_name_part`)",
        _article(
            back=_alternatives(
                '<element-citation><person-group><name><surname><xref ref-type="fig" rid="f1"/>'
                "</surname></name></person-group><source>F</source></element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Figure", "citation-author"), (SURNAME, "element-citation-unread")],
    ),
    (
        "the first's surname only a marker stores the marker (`_name_part`)",
        _article(
            back=_alternatives(
                '<element-citation><person-group><name><surname><xref ref-type="fn" rid="f1">'
                "Wvqxpo</xref></surname></name></person-group><source>F</source>"
                "</element-citation>",
                LATER_TAGGED,
            )
        ),
        [("Wvqxpo", "citation-author"), (SURNAME, "element-citation-unread")],
    ),
    (
        "a contributor's name-alternatives keep the last spelling (#143)",
        _article(
            meta='<contrib-group><contrib contrib-type="author"><name-alternatives>'
            f'<name name-style="eastern">{_named("Wvqxpo")}</name>{NAME}</name-alternatives>'
            "</contrib></contrib-group>"
        ),
        [("Wvqxpo", "contributor-overwritten"), (SURNAME, "contributor-own")],
    ),
    (
        "an empty later spelling overwrites the parts it repeats (#143)",
        _article(
            meta='<contrib-group><contrib contrib-type="author"><name-alternatives>'
            f"{NAME}<name><surname/><given-names>Wvqxpo</given-names></name>"
            "</name-alternatives></contrib></contrib-group>"
        ),
        [(SURNAME, "contributor-overwritten"), ("Wvqxpo", "contributor-own")],
    ),
    (
        "an editor in a collaboration's roster is declined; its consortium is not (#120)",
        _article(
            meta='<contrib-group><contrib contrib-type="author"><collab>Group<contrib-group>'
            f'<contrib contrib-type="editor">{_named("Wvqxpo")}</contrib><contrib>{NAME}'
            "</contrib></contrib-group></collab></contrib></contrib-group>"
        ),
        [("Wvqxpo", "contributor-declined"), (SURNAME, "contributor-own")],
    ),
    (
        "an empty first spelling has nothing to lose (#143)",
        _article(
            meta='<contrib-group><contrib contrib-type="author"><name-alternatives>'
            f"<name><surname/></name>{NAME}</name-alternatives></contrib></contrib-group>"
        ),
        [(None, "contributor-own"), (SURNAME, "contributor-own")],
    ),
    (
        "an empty surname a mononym leaves standing keeps nothing of its spelling (#143)",
        _article(
            meta='<contrib-group><contrib contrib-type="author"><name-alternatives>'
            "<name><surname/><given-names>Wvqxpo</given-names></name>"
            f"<name><given-names>{SURNAME}</given-names></name></name-alternatives>"
            "</contrib></contrib-group>"
        ),
        [("Wvqxpo", "contributor-overwritten"), (SURNAME, "contributor-own")],
    ),
    (
        "a name in the contributor's bio overwrites nothing (#258)",
        _article(
            meta=f'<contrib-group><contrib contrib-type="author">{NAME}<bio><p>Trained with '
            f"{_named('Wvqxpo')}.</p></bio></contrib></contrib-group>"
        ),
        [(SURNAME, "contributor-own"), ("Wvqxpo", "contributor-prose")],
    ),
]


def _strings(value: Any) -> list[str]:
    """Every string a parsed article holds, walked through its dataclasses."""
    if isinstance(value, str):
        return [value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [s for f in dataclasses.fields(value) for s in _strings(getattr(value, f.name))]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def _parsed_fate(document: str, marker: str = SURNAME) -> str:
    """What the real parser did with ``marker``: ``stored`` as an author of a
    reference or of the article, ``printed`` in some other field, or ``absent``."""
    article = JATSParser(document.encode()).parse()
    authors = [name for reference in article.references for name in reference.authors]
    authors += _strings(article.authors)
    if any(marker in name for name in authors):
        return "stored"
    return "printed" if any(marker in text for text in _strings(article)) else "absent"


# The parser's outcome each fate claims: a field reads a name only by storing
# it as an author, and a kept or glued one is printed somewhere else.
OUTCOMES = {
    sampler.READ: "stored",
    sampler.KEPT: "printed",
    sampler.GLUED: "printed",
    sampler.DROPPED: "absent",
}


def _surname_survives(document: str) -> bool:
    return _parsed_fate(document) != "absent"


def _holders(document: str) -> list[Any]:
    return list(sampler.walk(ET.fromstring(document.encode())).holders)


class TestTheInstrumentAgreesWithTheParser:
    """Each context's fate is what the real parser does with a name there."""

    @pytest.mark.parametrize(
        ("label", "context", "document"), FIXTURES, ids=[row[0] for row in FIXTURES]
    )
    def test_the_context_is_the_expected_one(self, label: str, context: str, document: str) -> None:
        holders = _holders(document)
        assert [sampler.classify(h) for h in holders] == [context], label

    @pytest.mark.parametrize(
        ("label", "context", "document"), FIXTURES, ids=[row[0] for row in FIXTURES]
    )
    def test_the_fate_is_what_the_parser_kept(
        self, label: str, context: str, document: str
    ) -> None:
        fate = sampler.FATES[context]
        assert _parsed_fate(document) == OUTCOMES[fate], (
            f"{label}: the instrument says {fate!r}, and the parser's outcome was "
            f"{_parsed_fate(document)!r}"
        )

    @pytest.mark.parametrize(
        ("restated", "parsers"),
        [
            ("NESTED_ARTICLES", "_NESTED_ARTICLE_ELEMENTS"),
            ("READ_CITATIONS", "_CITATION_ELEMENTS"),
            ("MIXED_CITATIONS", "_MIXED_CONTENT_CITATIONS"),
            ("RELATED_WORK", "_RELATED_WORK_ELEMENTS"),
            ("CONTRIBUTOR_PROSE", "_CONTRIBUTOR_PROSE"),
            ("TABLE_CELLS", "_TABLE_CELL_ELEMENTS"),
            ("TEXT_ACCUMULATING", "_TEXT_ACCUMULATING"),
            ("INLINE_ELEMENTS", "_INLINE_ELEMENTS"),
            ("FORMULA_PARTS", "_FORMULA_PARTS"),
            ("NOTE_ELEMENTS", "_NOTE_ELEMENTS"),
            ("NOT_A_NAMES_TEXT", "_NOT_A_NAMES_TEXT"),
            ("ARTICLE_META", "_ARTICLE_META"),
        ],
    )
    def test_a_restated_routing_set_is_the_parsers(self, restated: str, parsers: str) -> None:
        # Routing scope rather than a judgement under test, so the instrument
        # must not be free to disagree on it: a restated set that drifts
        # measures a parser that no longer exists.
        assert getattr(sampler, restated) == getattr(jats_parser, parsers)

    def test_every_context_has_a_fixture(self) -> None:
        # An overwritten spelling has a sibling by construction, so a context
        # may be pinned by a several-holder row instead.
        pinned = {row[1] for row in FIXTURES} | {c for row in SEVERAL for _, c in row[2]}
        assert pinned == {c.name for c in sampler.CONTEXTS}

    def test_the_fates_are_the_four_the_report_reads(self) -> None:
        assert set(sampler.FATES.values()) == {
            sampler.READ,
            sampler.KEPT,
            sampler.GLUED,
            sampler.DROPPED,
        }


class TestTheWalk:
    def test_a_suppressed_region_is_counted_and_not_walked(self) -> None:
        document = _article(
            back=f"<sub-article><front-stub/><body><p>By {NAME}.</p></body></sub-article>"
        )
        assert _surname_survives(document) is False
        walked = sampler.walk(ET.fromstring(document.encode()))
        assert walked.holders == []
        assert walked.suppressed == 1

    def test_a_namespace_is_stripped(self) -> None:
        root = ET.fromstring(
            f'<j:article xmlns:j="urn:x"><j:p><j:name><j:surname>{SURNAME}</j:surname>'
            "</j:name></j:p></j:article>"
        )
        (holder,) = sampler.walk(root).holders
        assert (holder.tag, holder.ancestors) == ("name", ("article", "p"))

    def test_only_the_first_citation_elements_name_is_read(self) -> None:
        root = ET.fromstring(
            _article(
                back=_ref(
                    f"<mixed-citation>{NAME}</mixed-citation><mixed-citation>{NAME}</mixed-citation>"
                )
            ).encode()
        )
        first, second = sampler.walk(root).holders
        assert (first.read_as_author, second.read_as_author) == (True, False)

    @pytest.mark.parametrize(
        ("label", "document", "expected"), SEVERAL, ids=[row[0] for row in SEVERAL]
    )
    def test_every_holder_meets_the_fate_the_parser_gave_it(
        self, label: str, document: str, expected: list[tuple[str | None, str]]
    ) -> None:
        assert [sampler.classify(h) for h in _holders(document)] == [c for _, c in expected]
        for marker, context in expected:
            if marker is None:
                continue
            fate = sampler.FATES[context]
            assert _parsed_fate(document, marker) == OUTCOMES[fate], (label, marker)

    def test_the_walk_survives_a_deep_document(self) -> None:
        depth = 3000
        root = ET.fromstring(("<p>" * depth) + NAME + ("</p>" * depth))
        assert len(sampler.walk(root).holders) == 1


def _bundle(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bundle.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(f"<articles>{body}</articles>")
    return path


class TestReadingAnArtifact:
    def test_a_bundle_splits_on_its_roots_not_on_article_meta(self, tmp_path: Path) -> None:
        one = _article(meta="<article-categories/>").split("?>", 1)[1]
        report = sampler.measure(_bundle(tmp_path, one + "\n" + one))
        assert (report.documents, report.parsed, report.problems) == (2, 2, [])

    def test_an_article_missing_its_end_tag_is_reported(self, tmp_path: Path) -> None:
        one = _article().split("?>", 1)[1]
        truncated = one.removesuffix("</article>")
        report = sampler.measure(_bundle(tmp_path, one + truncated + one))
        assert any("openers" in problem for problem in report.problems)

    def test_an_unparsed_document_is_out_of_the_denominator_and_reported(
        self, tmp_path: Path
    ) -> None:
        good = _article().split("?>", 1)[1]
        bad = "<article><p></article>"
        report = sampler.measure(_bundle(tmp_path, good + bad))
        assert (report.documents, report.parsed) == (2, 1)
        assert any("did not parse" in problem for problem in report.problems)

    def test_a_tar_skips_and_counts_what_is_not_xml(self, tmp_path: Path) -> None:
        path = tmp_path / "package.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, text in (("a/PMC1.xml", _article()), ("a/README.txt", "x")):
                data = text.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        report = sampler.measure(path)
        assert (report.documents, report.stats.skipped_members, report.problems) == (1, 1, [])

    def test_an_empty_artifact_is_a_problem_not_a_zero(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        report = sampler.measure(tmp_path / "empty")
        assert report.problems == ["no document was read"]

    def test_the_exit_status_follows_the_problems(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        (tmp_path / "one").mkdir()
        (tmp_path / "one" / "PMC1.xml").write_text(_article(body=f"<p>{NAME}</p>"))
        argv = ["measure", str(tmp_path / "one")]
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sys, "argv", argv)
            assert sampler.main() == 0
            patch.setattr(sys, "argv", [*argv, str(tmp_path / "empty")])
            assert sampler.main() == 1

    def test_a_dropped_name_in_prose_is_counted_as_such(self, tmp_path: Path) -> None:
        (tmp_path / "PMC1.xml").write_text(_article(body=f"<p>Named after {NAME}.</p>"))
        report = sampler.measure(tmp_path)
        assert (report.holders["other"], report.dropped_in_prose) == (1, 1)
        assert report.paths["other"] == {"article/body/sec/p/name": 1}
