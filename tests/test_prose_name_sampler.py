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
        "citation-cell",
        _article(
            back=_ref(
                "<mixed-citation>Foo <alternatives><table><tbody><tr><td><element-citation>"
                f"<person-group>{NAME}</person-group></element-citation></td></tr></tbody>"
                "</table></alternatives>.</mixed-citation>"
            )
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


def _surname_survives(document: str) -> bool:
    article = JATSParser(document.encode()).parse()
    return any(SURNAME in text for text in _strings(article))


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
        dropped = sampler.FATES[context] == sampler.DROPPED
        assert _surname_survives(document) is not dropped, (
            f"{label}: the instrument says {sampler.FATES[context]!r}, and the parser "
            f"{'kept' if not dropped else 'dropped'} nothing of the kind"
        )

    def test_every_context_has_a_fixture(self) -> None:
        assert {row[1] for row in FIXTURES} == {c.name for c in sampler.CONTEXTS}

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

    def test_only_a_later_citation_element_is_marked(self) -> None:
        root = ET.fromstring(
            _article(
                back=_ref(
                    f"<mixed-citation>{NAME}</mixed-citation><mixed-citation>{NAME}</mixed-citation>"
                )
            ).encode()
        )
        first, second = sampler.walk(root).holders
        assert first.later_citations == frozenset()
        assert second.later_citations == frozenset({second.ancestors.index("mixed-citation")})

    @pytest.mark.parametrize(
        ("first", "contexts", "kept"),
        [
            # The first rendition stores an author: the later one's is discarded.
            (
                f"<person-group>{NAME.replace(SURNAME, 'Other')}</person-group>. Foo.",
                ["citation-author", "element-citation-unread"],
                False,
            ),
            # An empty <name> stores no author, so the later one's fills the list.
            (
                "<person-group><name><surname/></name></person-group>. Foo.",
                ["citation-author", "citation-author"],
                True,
            ),
            # A related work's byline is not the reference's (#270).
            (
                f"Foo. <related-object><person-group>{NAME.replace(SURNAME, 'Rel')}"
                "</person-group></related-object>",
                ["mixed-citation-glued", "citation-author"],
                True,
            ),
        ],
        ids=["first-named", "first-empty-name", "first-related-work"],
    )
    def test_a_later_alternative_fills_only_an_empty_author_list(
        self, first: str, contexts: list[str], kept: bool
    ) -> None:
        document = _article(
            back=_alternatives(f"<mixed-citation>{first}</mixed-citation>", LATER_TAGGED)
        )
        assert [sampler.classify(h) for h in _holders(document)] == contexts
        assert _surname_survives(document) is kept

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
