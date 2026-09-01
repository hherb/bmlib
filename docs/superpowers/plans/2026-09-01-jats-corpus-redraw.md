# JATS Corpus Redraw Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redraw the two committed JATS corpora from a named public artifact with an instrument that measures what the parser actually routes, and reconcile every figure cited from them.

**Architecture:** `scripts/sample_jats_exhibits.py` gains a second *source* — PMC OA baseline packages, read offline and drawn deterministically from `(packages, absolute window, target, seed)` — while its live Europe PMC path stays untouched. The walk stops descending at `<sub-article>`/`<response>` to match the parser's suppressed region, and records what the old unscoped walk would have said so the correction is measured rather than merely applied. Four new counter families size the populations #142, #143, #147 and #150 are waiting on. A paired live diff over 300 articles measures the archive-versus-`fullTextXML` rendition gap.

**Tech Stack:** Python 3.11+, stdlib `tarfile` / `xml.etree.ElementTree` / `random`, `httpx` (already required by the sampler), pytest.

**Spec:** [`docs/superpowers/specs/2026-09-01-jats-corpus-redraw-design.md`](../specs/2026-09-01-jats-corpus-redraw-design.md)

## Global Constraints

- **AGPL-3 header** on every source file — copy from any existing file. The sampler already has one; do not disturb it.
- **`from __future__ import annotations`**, type hints on every signature, Google-style docstrings on everything public. `scripts/` files follow the same rules as `bmlib/`.
- **`uv` only, never bare pip.** Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff**, not `.venv`'s: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`. Line length 100.
- **`uv run mypy` takes no arguments** and must not be given any. Its scope is `files = ["bmlib"]`, so `scripts/` is not type-checked by the gate — write it as if it were anyway.
- **The sampler must not import the parser's predicates.** `_ARCHIVAL_HINTS`, `_THUMB_PATTERN`, `_TRANSPARENT_WRAPPERS` are deliberately the sampler's own and deliberately differ from `jats_parser`'s. `tests/test_jats_exhibit_sampler.py` asserts the two sets differ; a future "deduplication" must break that test, not pass it.
- **Counter vocabularies are open.** Count a name under itself; never against a list this script wrote. Counted against a closed list, an unforeseen spelling falls into `(none)` and is *reported* as absent — #121's mis-certification inside the instrument built to detect the next #120.
- **A new counter generation needs a `NOT_MEASURED` sentinel tuple** and an entry in `from_dict`, or a corpus written before it sums to zero and reads as a genuine empty population. There are three such generations already (`_TABLE_SIDE_COUNTERS`, `_OWNER_SIDE_COUNTERS`, `_CONTRIB_SIDE_COUNTERS`); this plan adds the fourth and fifth.
- **Local data, not in the repo:** `/Users/hherb/pmc_archive/packages/` holds `PMC012xxxxxx/` unpacked (97,909 articles) and `oa_comm_xml.PMC0{00..12}xxxxxx.baseline.2025-06-26.tar.gz`. Tasks 1–5 and 7 need none of it; only Task 6 reads it.
- **Never write a closing keyword followed by an issue number** in a commit message, PR body, or any quotation of either — GitHub closes the issue whatever the sentence says. Four issues have been closed that way, most recently #160 by the commit documenting the mechanism. Write the number without its `#`, or describe the relationship.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `scripts/sample_jats_exhibits.py` | The instrument: sources, walk, counters, report, corpus writer | 1–5 |
| `tests/test_jats_exhibit_sampler.py` | Offline cover for all of it, plus the cited-populations net | 1–5, 7 |
| `tests/data/jats_exhibits.json` | Recent-window corpus (2023-07 – 2025-06), 1,000 rows | 6 |
| `tests/data/jats_exhibits.backfill.json` | Back-filled corpus (1996 – 1998), 1,000 rows | 6 |
| `tests/data/jats_exhibits.rendition.json` | The archive-vs-`fullTextXML` deltas, 300 articles | 6 |
| `bmlib/fulltext/jats_parser.py` | Comments citing the moved figures — prose only, no behaviour | 7 |
| `CLAUDE.md`, `docs/manual/fulltext.md`, `CHANGELOG.md`, `ROADMAP.md`, `HANDOVER.md` | The same figures, cited again | 7 |

The sampler is 1,184 lines and will grow by roughly 350. It is one file on purpose — it is a single instrument whose parts are read together, and the repo's other samplers follow the same shape — so do not split it. Shared helpers that a *second* sampler would want go to `scripts/_sampling.py` instead, with their tests.

---

### Task 1: Scope the walk, and measure what scoping changed

Issue #138. The walk descends into `<sub-article>`/`<response>`; the parser fires no handler inside one. Every counter is therefore a whole-document count. Stop descending — and record what the old walk would have said, so the corpus answers "how much was this inflated" for every population rather than for the one that was spot-checked.

**Files:**
- Modify: `scripts/sample_jats_exhibits.py` (constants block ~line 158; `ArticleMeasurement` ~line 260; `measure_article` ~line 358)
- Test: `tests/test_jats_exhibit_sampler.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_NESTED_ARTICLE_ELEMENTS: frozenset[str]` — `{"sub-article", "response"}`
  - `ArticleMeasurement.nested_article_regions: int`
  - `ArticleMeasurement.unscoped: dict[str, Any]` — only the fields whose scoped and unscoped values differ; ints stay ints, `Counter` fields become plain `dict[str, int]`
  - `_SCOPE_SIDE_COUNTERS: tuple[str, ...]` — `("nested_article_regions",)`
  - `measure_article(pmcid: str, xml: bytes) -> ArticleMeasurement | None` — signature unchanged

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_jats_exhibit_sampler.py`, in a new class:

```python
class TestTheWalkStopsWhereTheParserStops:
    """Issue #138 — the sampler must count what `jats_parser` routes.

    `<sub-article>` and `<response>` open a region in which the parser fires
    no handler at all (#110), so a whole-document count is a count of a
    different thing. The pair of tests below are the two halves: nothing
    inside a region contributes, and the row still says what the old walk
    would have said.
    """

    NESTED = """
        <fig id="f1"><label>Figure 1</label><caption><p>Ours.</p></caption>
          <graphic xlink:href="ours.jpg"/></fig>
        <sub-article article-type="peer-review">
          <front-stub><contrib-group><contrib contrib-type="author">
            <name><surname>Reviewer</surname></name></contrib></contrib-group></front-stub>
          <body>
            <fig id="rf1"><label>Figure R1</label><caption><p>Theirs.</p></caption>
              <graphic xlink:href="theirs.jpg"/></fig>
            <sec><title>Review</title><p>Prose.</p></sec>
          </body>
        </sub-article>"""

    def test_nothing_inside_a_nested_article_is_counted(self):
        row = sampler.measure_article("PMC1", _article(self.NESTED))

        assert row.figures == 1
        assert row.graphics == 1
        assert row.captions == 1
        assert row.contribs == 0
        assert row.sections == 0
        assert row.nested_article_regions == 1
        assert row.label_parents == {"fig": 1}

    def test_the_row_records_what_the_unscoped_walk_would_have_said(self):
        """The measurement #158 wants, not merely the correction #138 asks for."""
        row = sampler.measure_article("PMC1", _article(self.NESTED))

        assert row.unscoped["figures"] == 2
        assert row.unscoped["graphics"] == 2
        assert row.unscoped["contribs"] == 1
        assert row.unscoped["label_parents"] == {"fig": 2}
        # A field the region cannot move is absent, not zero-valued.
        assert "tables" not in row.unscoped

    def test_an_article_with_no_nested_article_records_no_difference(self):
        row = sampler.measure_article(
            "PMC1", _article("<fig id='f1'><label>Figure 1</label></fig>")
        )

        assert row.nested_article_regions == 0
        assert row.unscoped == {}

    def test_a_response_is_a_region_too_and_nesting_is_visible(self):
        """`<response>` is the other half of the two-element set, and JATS nests them."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sub-article><body><p>Round one.</p>
              <response><body><fig id="rf"><label>F</label></fig></body></response>
            </body></sub-article>"""),
        )

        assert row.figures == 0
        assert row.nested_article_regions == 1
        assert row.unscoped["nested_article_regions"] == 2

    def test_a_row_written_before_this_counter_reads_as_not_measured(self):
        """The `NOT_MEASURED` rule — a zero here is also a genuine empty draw."""
        row = sampler.ArticleMeasurement.from_dict({"pmcid": "PMC1", "figures": 3})

        assert row.nested_article_regions == sampler.NOT_MEASURED
        assert row.unscoped == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py::TestTheWalkStopsWhereTheParserStops -v`
Expected: FAIL — `AttributeError: 'ArticleMeasurement' object has no attribute 'nested_article_regions'`.

- [ ] **Step 3: Add the constant and the two fields**

In the constants block, after `_TRANSPARENT_WRAPPERS`:

```python
# The two elements the parser suppresses entirely (#110), restated here rather
# than imported for the same reason every other predicate in this file is: a
# corpus labelled by the rule under test can only confirm that rule. The set is
# complete for a structural reason — exactly three JATS elements admit
# `<front>`/`<front-stub>` and `<body>`, and the third is `<article>` itself.
_NESTED_ARTICLE_ELEMENTS = frozenset({"sub-article", "response"})

# The fourth counter generation (issue #138), and the sentinel a row written
# before it is loaded with — same rule as the three above: an article carrying
# no nested article genuinely measures zero here, so zero cannot mean "absent".
_SCOPE_SIDE_COUNTERS = ("nested_article_regions",)
```

On `ArticleMeasurement`, after `articles_losing_every_author`:

```python
    nested_article_regions: int = 0
    # What the pre-#138 whole-document walk would have said, for the fields
    # where that differs — and *only* those, so an article carrying no nested
    # article contributes an empty mapping rather than a second copy of itself.
    # Recording it is what makes the redraw a measurement of the correction
    # rather than a silent application of it: #158's four disagreeing rates are
    # exactly the question "how much does the region inflate a count?".
    unscoped: dict[str, Any] = field(default_factory=dict)
```

In `from_dict`, extend the generation tuple:

```python
        for name in (
            *_TABLE_SIDE_COUNTERS,
            *_OWNER_SIDE_COUNTERS,
            *_CONTRIB_SIDE_COUNTERS,
            *_SCOPE_SIDE_COUNTERS,
        ):
            if name not in data:
                setattr(row, name, NOT_MEASURED)
```

- [ ] **Step 4: Split the walk out so it can run twice**

Replace the body of `measure_article` from `row = ArticleMeasurement(pmcid=pmcid)` to the `return row` with a call to a new module-level helper, and add the helper. The walk body itself is unchanged except for the nested-article branch.

```python
def _measure_tree(pmcid: str, root: ET.Element, *, scoped: bool) -> ArticleMeasurement:
    """Walk one parsed article and record every population it contributes to.

    Args:
        pmcid: The article's PMC identifier, used only to label the row.
        root: The parsed document.
        scoped: Whether to stop at a nested-article region, the way the parser
            does. ``False`` reproduces the pre-#138 whole-document walk, which
            is what the ``unscoped`` diff is taken against.

    Returns:
        The measurement.
    """
    row = ArticleMeasurement(pmcid=pmcid)
    transparent = set(_TRANSPARENT_WRAPPERS)

    def walk(el: ET.Element, exhibit: str | None, chain: list[str], exhibit_depth: int) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag in _NESTED_ARTICLE_ELEMENTS:
                # Counted either way — the count is #158's population — but
                # descended into only when reproducing the old walk. Scoped,
                # a region nested inside another is never reached, so the
                # scoped count is of top-level regions and the unscoped one
                # of all of them; the difference is the nesting rate.
                row.nested_article_regions += 1
                if scoped:
                    continue
            if tag in _EXHIBITS:
                _record_exhibit(child, tag, exhibit_depth, row)
                walk(child, tag, [tag], exhibit_depth + 1)
                continue
            ...  # the rest of the existing walk body, unchanged
            walk(child, exhibit, chain + [tag], exhibit_depth)

    walk(root, None, [], 0)
    undivided = row.contrib_name_spellings["string-name"] + row.contrib_name_spellings["collab"]
    if undivided and not row.contrib_name_spellings["name"]:
        row.articles_losing_every_author = 1
    return row
```

Note the nested-article branch does **not** `continue` when `scoped` is false — it falls through so the region's own children are walked exactly as before.

- [ ] **Step 5: Rewrite `measure_article` as the diff**

```python
def measure_article(pmcid: str, xml: bytes) -> ArticleMeasurement | None:
    """Walk one article's JATS and record every population it contributes to.

    The walk is scoped to what ``jats_parser`` routes: it stops at a
    ``<sub-article>`` or ``<response>``, in which the parser fires no handler
    (#110). It is then run a second time *unscoped*, and the fields that
    differ are recorded on the row — so the corpus says how much the old
    whole-document walk overstated each population, which is the measurement
    issue #158's four disagreeing rates are asking for.

    Args:
        pmcid: The article's PMC identifier, used only to label the row.
        xml: The raw JATS body.

    Returns:
        The measurement, or ``None`` if the document would not parse — which
        makes the article *unmeasured* rather than empty.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    row = _measure_tree(pmcid, root, scoped=True)
    if not row.nested_article_regions:
        # No region, so nothing can differ. Skipping the second walk here is
        # not only an optimisation: it keeps `unscoped` empty for the ~96% of
        # articles that carry no region, which is what stops the corpus
        # doubling in size.
        return row
    shadow = _measure_tree(pmcid, root, scoped=False)
    row.unscoped = _row_difference(row, shadow)
    return row


def _row_difference(scoped: ArticleMeasurement, shadow: ArticleMeasurement) -> dict[str, Any]:
    """The fields where the unscoped walk disagrees, and only those.

    Args:
        scoped: The row as the parser would see the document.
        shadow: The row the pre-#138 whole-document walk produces.

    Returns:
        A mapping from field name to the unscoped value, ``Counter`` fields
        rendered as plain dicts so the row serialises without special cases.
    """
    out: dict[str, Any] = {}
    for name, value in shadow.__dict__.items():
        if name in ("pmcid", "unscoped"):
            continue
        if value != getattr(scoped, name):
            out[name] = dict(value) if isinstance(value, Counter) else value
    return out
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py -v`
Expected: the new class passes. **Some pre-existing tests in `TestTheCitedPopulationsAreWhatTheCorporaHold` may now fail** — they read the committed corpora, which Task 6 replaces. Leave them failing; Task 7 reconciles them. Every *other* pre-existing test must still pass; if one does not, the walk body was altered by accident.

- [ ] **Step 7: Fix the corpus-summing helper**

`TestTheCitedPopulationsAreWhatTheCorporaHold._totals` sums every dict-valued row field as a `Counter`, which raises `TypeError` on `unscoped`'s nested dicts. Add the skip and say why:

```python
        for row in rows:
            for key, value in row.items():
                if key == "unscoped":
                    # Not a population: it is what the *pre-#138* walk would
                    # have said, kept so the correction is measurable. Summing
                    # it into the counters would restore exactly the inflation
                    # the scoping removed.
                    continue
                if isinstance(value, int):
```

- [ ] **Step 8: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/sample_jats_exhibits.py tests/test_jats_exhibit_sampler.py
git commit -m "fix(scripts): count what the parser routes, and measure what that changed"
```

---

### Task 2: Read a PMC OA baseline package

The offline source's data layer: enumerate candidates from a directory or a tarball, and date each one. No selection and no CLI yet.

**Files:**
- Modify: `scripts/sample_jats_exhibits.py`
- Test: `tests/test_jats_exhibit_sampler.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `_PUB_DATE_RE: re.Pattern[bytes]`, `_YEAR_RE: re.Pattern[bytes]`
  - `article_year(xml: bytes) -> int | None`
  - `iter_package_articles(path: Path) -> Iterator[tuple[str, bytes]]` — yields `(pmcid, raw_xml)` for every member, whole bytes, directory or `.tar.gz`
  - `PackageError(Exception)` — raised for a path that is neither

- [ ] **Step 1: Write the failing tests**

```python
class TestReadingABaselinePackage:
    """The offline source's data layer — one article at a time, whole."""

    def _write_tarball(self, tmp_path, members: dict[str, bytes]):
        import tarfile

        path = tmp_path / "oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(f"PMC000xxxxxx/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def test_a_directory_yields_every_article(self, tmp_path):
        (tmp_path / "PMC1.xml").write_bytes(b"<article/>")
        (tmp_path / "PMC2.xml").write_bytes(b"<article/>")
        (tmp_path / "notes.txt").write_bytes(b"ignored")

        found = dict(sampler.iter_package_articles(tmp_path))

        assert sorted(found) == ["PMC1", "PMC2"]

    def test_a_tarball_yields_every_article_without_unpacking(self, tmp_path):
        path = self._write_tarball(tmp_path, {"PMC7.xml": b"<article/>"})

        found = dict(sampler.iter_package_articles(path))

        assert list(found) == ["PMC7"]
        assert not (tmp_path / "PMC000xxxxxx").exists()

    def test_something_that_is_neither_is_refused(self, tmp_path):
        stray = tmp_path / "stray.xml"
        stray.write_bytes(b"<article/>")

        with pytest.raises(sampler.PackageError, match="stray.xml"):
            list(sampler.iter_package_articles(stray))

    def test_the_year_is_the_earliest_any_pub_date_declares(self):
        xml = b"""<article><front><article-meta>
            <pub-date pub-type="epub"><year>2024</year></pub-date>
            <pub-date pub-type="ppub"><year>2023</year></pub-date>
            </article-meta></front></article>"""

        assert sampler.article_year(xml) == 2023

    def test_the_kind_of_date_is_not_consulted(self):
        """Measured, not assumed: excluding deposit and submission kinds
        changes the earliest year in 0 of 3,000 articles in each window, and
        the attribute spelling is not one vocabulary — `pub-type="ppub"`
        dominates the back-filled range, `pub-type="epub"` the recent one, and
        JATS 1.x writes `date-type="pub" publication-format="electronic"`."""
        xml = b"""<article><front><article-meta>
            <pub-date date-type="pub" publication-format="electronic">
              <year>2019</year></pub-date>
            </article-meta></front></article>"""

        assert sampler.article_year(xml) == 2019

    def test_an_article_with_no_pub_date_has_no_year(self):
        assert sampler.article_year(b"<article><front/></article>") is None

    def test_a_year_outside_a_pub_date_is_not_a_publication_year(self):
        """A `<year>` in a reference is not this article's date."""
        xml = b"<article><back><ref><year>1999</year></ref></back></article>"

        assert sampler.article_year(xml) is None

    def test_the_whole_member_is_read_not_a_prefix(self):
        """The guard against the optimisation someone will reach for later.

        A prefix read is 49% faster, raises nothing, and finds no date for
        379 of 2,000 recent articles at 8 KB — a miss that tracks front-matter
        size, so it tracks publisher, which is the axis every population here
        varies along. Note the evidence is the *recent* window: on the
        back-filled one a prefix read misses nothing (3,141 either way over the
        whole of `PMC002xxxxxx`), so a rule drawn from that window alone would
        license the optimisation that costs a fifth of the other.
        """
        padding = b"<aff>" + b"x" * 20000 + b"</aff>"
        xml = (
            b"<article><front><article-meta>"
            + padding
            + b"<pub-date pub-type='epub'><year>2001</year></pub-date>"
            + b"</article-meta></front></article>"
        )

        assert sampler.article_year(xml) == 2001
```

Add `import io` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py::TestReadingABaselinePackage -v`
Expected: FAIL — `AttributeError: module has no attribute 'iter_package_articles'`.

- [ ] **Step 3: Implement**

Add `import tarfile` and `from collections.abc import Iterator` to the sampler's imports. After `_extension`:

```python
# A publication date, read from the raw bytes rather than from a parsed tree:
# the scan touches every article in a package and parsing them all to pick two
# elements costs more than the walk that follows it.
_PUB_DATE_RE = re.compile(rb"<pub-date[^>]*>(.*?)</pub-date>", re.DOTALL)
_YEAR_RE = re.compile(rb"<year>\s*(\d{4})\s*</year>")


class PackageError(Exception):
    """A ``--package`` path that is neither a directory nor a tarball."""


def article_year(xml: bytes) -> int | None:
    """The earliest year any ``<pub-date>`` declares, or ``None``.

    The date's declared *kind* is deliberately not consulted. The attribute is
    not one vocabulary — the back-filled range is dominated by
    ``pub-type="ppub"`` (2,868 of 3,000 articles), the recent window by
    ``pub-type="epub"`` (2,704), JATS 1.x spells it
    ``date-type="pub" publication-format="electronic"``, and ``pmc-release``,
    ``nihms-submitted`` and ``epreprint`` all appear — so an enumeration would
    be the kind #130's ``<list>`` is the standing lesson against. The obvious
    refinement, excluding the deposit and submission kinds (the two that could
    pull a date away from publication), was measured against this rule and
    **changes the earliest year in 0 of 3,000 articles in each window**.

    The ``<year>`` must be read from *inside* a ``<pub-date>``. Matching the
    open tag lazily to the next ``<year>`` anywhere after it reaches into
    ``<ref>`` and reports a cited work's year as this article's — the first
    draw made that mistake and produced articles "published" in 1861.

    Args:
        xml: The article's raw bytes, **whole**. A prefix read is measured in
            this plan's spec as both lossy and wrong.

    Returns:
        The year, or ``None`` where the document declares no ``<pub-date>``
        carrying a ``<year>`` — which makes the article undated and so
        undrawable, never "published in year zero".
    """
    years = [
        int(year.group(1))
        for block in _PUB_DATE_RE.finditer(xml)
        if (year := _YEAR_RE.search(block.group(1)))
    ]
    return min(years) if years else None


def iter_package_articles(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(pmcid, raw_xml)`` for every article in one baseline package.

    A ``.tar.gz`` is streamed member by member and never unpacked; a directory
    is walked with ``glob``. Members are read **whole** — see
    :func:`article_year`.

    Args:
        path: A package directory, or a baseline ``.tar.gz``.

    Yields:
        The PMC identifier (the member's stem) and its bytes.

    Raises:
        PackageError: If *path* is neither a directory nor a tarball. Refused
            rather than skipped: a mistyped ``--package`` that silently
            contributed nothing would print a rate over a draw nobody asked
            for, which is what :func:`_validate_args` exists to prevent.
    """
    if path.is_dir():
        for entry in sorted(path.glob("*.xml")):
            yield entry.stem, entry.read_bytes()
        return
    if path.is_file() and tarfile.is_tarfile(path):
        with tarfile.open(path, "r|gz") as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith(".xml"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:  # pragma: no cover - a tarball oddity
                    continue
                yield Path(member.name).stem, handle.read()
        return
    raise PackageError(f"{path} is neither a package directory nor a tarball")
```

Note `"r|gz"` — the *streaming* mode. `"r:gz"` builds a full member index up front, which for a 12.7 GB package means decompressing it once before yielding anything.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py::TestReadingABaselinePackage -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/sample_jats_exhibits.py tests/test_jats_exhibit_sampler.py
git commit -m "feat(scripts): read a PMC OA baseline package, whole members only"
```

---

### Task 3: Draw deterministically, and wire up the CLI

The selection layer and the arguments. After this task the sampler can produce a corpus offline.

**Files:**
- Modify: `scripts/sample_jats_exhibits.py` (`_build_arg_parser`, `_validate_args`, `main`)
- Test: `tests/test_jats_exhibit_sampler.py`

**Interfaces:**
- Consumes: `iter_package_articles`, `article_year`, `PackageError` (Task 2).
- Produces:
  - `DEFAULT_SEED: int` = `0`
  - `package_candidates(paths: list[Path], first: int, last: int) -> list[str]`
  - `draw(candidates: list[str], target: int, seed: int) -> list[str]`
  - `read_package_articles(paths: list[Path], wanted: set[str]) -> Iterator[tuple[str, bytes]]`
  - CLI: `--package` (repeatable `Path`), `--from-year` / `--to-year` (`int`), `--seed` (`int`)

- [ ] **Step 1: Write the failing tests**

```python
class TestDrawingFromAPackage:
    """Selection: in-window, deterministic, and reproducible from the header."""

    def _package(self, tmp_path, years: dict[str, int | None]):
        for pmcid, year in years.items():
            date = (
                f"<pub-date pub-type='epub'><year>{year}</year></pub-date>"
                if year is not None
                else ""
            )
            (tmp_path / f"{pmcid}.xml").write_text(
                f"<article><front><article-meta>{date}</article-meta></front></article>"
            )
        return tmp_path

    def test_only_articles_inside_the_window_are_candidates(self, tmp_path):
        path = self._package(
            tmp_path, {"PMC1": 1995, "PMC2": 1996, "PMC3": 1998, "PMC4": 1999, "PMC5": None}
        )

        found = sampler.package_candidates([path], 1996, 1998)

        assert found == ["PMC2", "PMC3"]

    def test_the_draw_is_reproducible_from_the_seed(self, tmp_path):
        candidates = [f"PMC{n}" for n in range(100)]

        first = sampler.draw(candidates, 10, seed=0)
        again = sampler.draw(candidates, 10, seed=0)
        other = sampler.draw(candidates, 10, seed=1)

        assert first == again
        assert first != other
        assert len(first) == 10
        assert set(first) <= set(candidates)

    def test_the_draw_does_not_depend_on_candidate_order(self, tmp_path):
        """A directory's glob order is not stable across machines, so the
        draw sorts before sampling — otherwise the recorded seed reproduces
        the draw only on the machine that made it."""
        forwards = [f"PMC{n}" for n in range(100)]

        assert sampler.draw(forwards, 10, seed=0) == sampler.draw(forwards[::-1], 10, seed=0)

    def test_a_target_above_the_candidate_count_takes_them_all(self, tmp_path):
        assert sorted(sampler.draw(["PMC1", "PMC2"], 50, seed=0)) == ["PMC1", "PMC2"]

    def test_only_the_wanted_articles_are_read_back(self, tmp_path):
        path = self._package(tmp_path, {"PMC1": 2024, "PMC2": 2024, "PMC3": 2024})

        found = dict(sampler.read_package_articles([path], {"PMC1", "PMC3"}))

        assert sorted(found) == ["PMC1", "PMC3"]


class TestThePackageRunIsRefusedWhenItWouldMislead:
    """`_validate_args`, which exists to stop a rate being printed over a draw
    nobody asked for."""

    def _args(self, **kwargs):
        defaults = dict(
            target=10, months=24, months_ago=0, package=[], from_year=None,
            to_year=None, seed=0, output=sampler.DEFAULT_OUTPUT, compare_europepmc=0,
        )
        return argparse.Namespace(**{**defaults, **kwargs})

    def test_a_package_run_needs_both_ends_of_the_window(self, tmp_path):
        refusal = sampler._validate_args(self._args(package=[tmp_path], from_year=1996))

        assert refusal is not None
        assert "--to-year" in refusal

    def test_a_window_that_runs_backwards_is_refused(self, tmp_path):
        refusal = sampler._validate_args(
            self._args(package=[tmp_path], from_year=1999, to_year=1996)
        )

        assert refusal is not None
        assert "1999" in refusal

    def test_a_window_without_a_package_is_refused(self):
        """The live path draws by month, not by year; accepting the flags
        there would silently ignore them."""
        refusal = sampler._validate_args(self._args(from_year=1996, to_year=1998))

        assert refusal is not None
        assert "--package" in refusal

    def test_a_displaced_package_window_may_not_land_on_the_default_output(self, tmp_path):
        """The rule `--months-ago` already carries, for the same reason: the
        journal is derived from `--output`, so two windows pool into one rate
        describing neither."""
        refusal = sampler._validate_args(
            self._args(package=[tmp_path], from_year=1996, to_year=1998)
        )

        assert refusal is not None
        assert "-o" in refusal

    def test_the_recent_window_may_use_the_default_output(self, tmp_path):
        refusal = sampler._validate_args(
            self._args(package=[tmp_path], from_year=2023, to_year=2025)
        )

        assert refusal is None
```

Add `import argparse` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py::TestDrawingFromAPackage tests/test_jats_exhibit_sampler.py::TestThePackageRunIsRefusedWhenItWouldMislead -v`
Expected: FAIL — `AttributeError: module has no attribute 'package_candidates'`.

- [ ] **Step 3: Implement selection**

Add `import random` to the imports, `DEFAULT_SEED = 0` to the constants, and after `iter_package_articles`:

```python
def package_candidates(paths: list[Path], first: int, last: int) -> list[str]:
    """Every article in *paths* published in ``[first, last]``, sorted.

    Args:
        paths: Package directories or tarballs.
        first: Earliest publication year to accept, inclusive.
        last: Latest publication year to accept, inclusive.

    Returns:
        The identifiers, sorted — the order a draw is taken against, so it
        must not depend on a directory's glob order.
    """
    found = [
        pmcid
        for path in paths
        for pmcid, xml in iter_package_articles(path)
        if (year := article_year(xml)) is not None and first <= year <= last
    ]
    return sorted(found)


def draw(candidates: list[str], target: int, seed: int) -> list[str]:
    """*target* identifiers from *candidates*, reproducibly.

    Sorted before sampling, because ``random.sample`` is a function of the
    sequence's order as well as of the seed: an unpacked directory's glob
    order is not stable across machines, so an unsorted draw would reproduce
    only where it was made — which is the property this whole change exists
    to give the corpora.

    Args:
        candidates: The identifiers to draw from.
        target: How many to take; taking them all is fine.
        seed: The recorded seed.

    Returns:
        The drawn identifiers, sorted.
    """
    pool = sorted(candidates)
    if target >= len(pool):
        return pool
    return sorted(random.Random(seed).sample(pool, target))


def read_package_articles(paths: list[Path], wanted: set[str]) -> Iterator[tuple[str, bytes]]:
    """Yield ``(pmcid, raw_xml)`` for the drawn articles, in package order.

    A second pass over the packages, rather than holding the first pass's
    bytes: the recent window has 97,668 in-window candidates, which at
    article sizes reaching 3.4 MB is not a thing to keep in memory. For a
    tarball the pass costs one more sequential decompression (16.5 s for
    `PMC002xxxxxx`).

    Args:
        paths: The same packages the candidates came from.
        wanted: The drawn identifiers.

    Yields:
        Each wanted article's identifier and bytes.
    """
    for path in paths:
        for pmcid, xml in iter_package_articles(path):
            if pmcid in wanted:
                yield pmcid, xml
```

- [ ] **Step 4: Add the arguments**

In `_build_arg_parser`, before `-o/--output`:

```python
    parser.add_argument(
        "--package",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Draw offline from a PMC OA baseline package — a directory of "
            "articles or a baseline .tar.gz. Repeatable. Requires --from-year "
            "and --to-year. A package draw is reproducible by any reader from "
            "(packages, window, target, seed); a live draw is not, which is "
            "what issue 132 is about."
        ),
    )
    parser.add_argument(
        "--from-year", type=int, default=None, help="Earliest publication year, inclusive."
    )
    parser.add_argument(
        "--to-year", type=int, default=None, help="Latest publication year, inclusive."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Seed for the package draw; recorded in the corpus either way.",
    )
```

- [ ] **Step 5: Add the refusals**

In `_validate_args`, before the existing `--months` check, and extend the docstring with a third rule ("*A package window is all-or-nothing.*"):

```python
    window = (args.from_year, args.to_year)
    if any(v is not None for v in window) and not args.package:
        return "--from-year/--to-year select from a --package; the live draw strata are months"
    if args.package and any(v is None for v in window):
        return "--package needs both --from-year and --to-year"
    if args.package and args.from_year > args.to_year:
        return f"--from-year {args.from_year} is after --to-year {args.to_year}"
    if args.package and args.to_year < _RECENT_WINDOW_FIRST_YEAR and args.output == DEFAULT_OUTPUT:
        return (
            f"a window ending {args.to_year} is a displaced draw, which must not be written "
            f"to {DEFAULT_OUTPUT} — that path is the recent draw, and its journal would pool "
            "the two. Pass an explicit -o, as tests/data/jats_exhibits.backfill.json was."
        )
```

with, in the constants block:

```python
# The first year of the recent window (see `main`'s window table). A draw
# ending before it is displaced, and the pooling rule `--months-ago` already
# carries applies to it for the same reason.
_RECENT_WINDOW_FIRST_YEAR = 2023
```

- [ ] **Step 6: Branch `main` on the source**

Replace `main`'s window resolution and fetch loop with a branch. The live half is moved verbatim; only the package half is new.

```python
    if args.package:
        window = {
            "source": "package",
            "packages": sorted(p.name for p in args.package),
            "first_year": args.from_year,
            "last_year": args.to_year,
            "target": args.target,
            "seed": args.seed,
        }
        candidates = package_candidates(args.package, args.from_year, args.to_year)
        print(f"{len(candidates)} candidates in {args.from_year}-{args.to_year}")
        wanted = {p for p in draw(candidates, args.target, args.seed) if p not in seen}
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as handle:
            for pmcid, xml in read_package_articles(args.package, wanted):
                row = measure_article(pmcid, xml)
                if row is None:
                    totals.unmeasured += 1
                    continue
                totals.add(row)
                handle.write(json.dumps(row.to_dict()) + "\n")
                handle.flush()
    else:
        ...  # the existing live path, unchanged, setting `window` from `_month_windows`
```

and the live branch sets:

```python
        windows = _month_windows(args.months, date.today(), skip=args.months_ago)
        window = {
            "source": "europepmc",
            "months": args.months,
            "months_ago": args.months_ago,
            "first": windows[-1][0],
            "last": windows[0][1],
        }
```

with the corpus writer using `"window": window` in place of the inline dict.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py -v`
Expected: the two new classes pass; `TestTheCitedPopulationsAreWhatTheCorporaHold` still fails from Task 1; nothing else fails.

- [ ] **Step 8: Smoke-test against real data**

```bash
uv run python scripts/sample_jats_exhibits.py \
    --package /Users/hherb/pmc_archive/packages/PMC012xxxxxx \
    --from-year 2023 --to-year 2025 --target 25 \
    -o /tmp/smoke.json
```

Expected: a candidate count of 97,668, 25 rows, a printed report, `/tmp/smoke.json` written with a `window` recording `source`, `packages`, `seed`. Delete `/tmp/smoke.json` and `/tmp/smoke.journal.jsonl` afterwards.

- [ ] **Step 9: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/sample_jats_exhibits.py tests/test_jats_exhibit_sampler.py
git commit -m "feat(scripts): draw a corpus offline and reproducibly from a named package"
```

---

### Task 4: Size the four waiting populations

Issues #142, #143, #147 and #150 each name this sampler as what should measure them before a rule can be picked. One walk answers all four.

**Files:**
- Modify: `scripts/sample_jats_exhibits.py` (`ArticleMeasurement`, `_record_contrib`, `_measure_tree`'s walk, `print_report`)
- Test: `tests/test_jats_exhibit_sampler.py`

**Interfaces:**
- Consumes: `_measure_tree` (Task 1).
- Produces, on `ArticleMeasurement`:
  - #142: `collab_children: Counter[str]`, `collabs_with_element_children: int`
  - #143: `contribs_multi_collab: int`, `contribs_multi_string_name: int`, `name_alternatives: int`
  - #147: `disp_formulas: int`, `inline_formulas: int`, `tex_math: int`, `mml_math: int`, `formula_alternatives_both: int`, `disp_formulas_with_label: int`
  - #150: `refs: int`, `refs_note_only: int`, `ref_child_kinds: Counter[str]`
  - `_WAITING_SIDE_COUNTERS: tuple[str, ...]` — the fifth generation's sentinel tuple, holding every integer counter above

- [ ] **Step 1: Write the failing tests**

```python
class TestTheFourWaitingPopulations:
    """Issues 142, 143, 147 and 150 — measured here, decided in their own PRs."""

    def test_a_collab_records_the_children_it_carries(self):
        """142: `<institution>` and `<addr-line>` are legal inside `<collab>`
        and run together in `JATSAuthorInfo.collab` with no separator. Which
        of the two candidate fixes is right is a question about how they are
        actually deposited."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group><contrib contrib-type="author"><collab>
              <institution>The Y Consortium</institution><addr-line>Boston MA</addr-line>
            </collab></contrib></contrib-group>"""),
        )

        assert row.collab_children == {"institution": 1, "addr-line": 1}
        assert row.collabs_with_element_children == 1

    def test_a_collab_of_bare_text_carries_no_children(self):
        row = sampler.measure_article(
            "PMC1",
            _article("<contrib-group><contrib><collab>The Y Group</collab></contrib></contrib-group>"),
        )

        assert row.collab_children == {}
        assert row.collabs_with_element_children == 0

    def test_multiplicity_is_counted_per_contrib(self):
        """143: section 11 counts spellings per *article*, so a contributor
        carrying two `<collab>` is invisible in it."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <contrib-group>
              <contrib><collab>First Group</collab><collab>Second Group</collab></contrib>
              <contrib><name-alternatives><name><surname>Latin</surname></name>
                <name><surname>Japanese</surname></name></name-alternatives></contrib>
            </contrib-group>"""),
        )

        assert row.contribs_multi_collab == 1
        assert row.contribs_multi_string_name == 0
        assert row.name_alternatives == 1

    def test_formulas_are_counted_by_kind(self):
        """147: a `<tex-math>` is dropped from the prose containing it and a
        `<disp-formula>` from the article outright. `<alternatives>` holding
        both encodings is why the fix is not one more inline element."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sec><p>The model is <inline-formula><tex-math>y = mx</tex-math></inline-formula>.</p>
            <disp-formula id="e1"><label>(1)</label>
              <alternatives><tex-math>E = mc^2</tex-math>
                <mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>E</mml:mi></mml:math>
              </alternatives></disp-formula></sec>"""),
        )

        assert (row.disp_formulas, row.inline_formulas) == (1, 1)
        assert (row.tex_math, row.mml_math) == (2, 1)
        assert row.formula_alternatives_both == 1
        assert row.disp_formulas_with_label == 1

    def test_a_note_only_reference_is_counted_apart(self):
        """150: it renders as an empty `<li>`, renumbering every entry after
        it relative to the publisher's own numbering."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <back><ref-list>
              <ref id="c1"><mixed-citation>Smith 2020.</mixed-citation></ref>
              <ref id="c2"><note><p>Deposited at the CCDC.</p></note></ref>
              <ref id="c3"><label>3</label><note><p>Also a note.</p></note></ref>
            </ref-list></back>"""),
        )

        assert row.refs == 3
        assert row.refs_note_only == 2
        assert row.ref_child_kinds == {"mixed-citation": 1, "note": 2, "label": 1}

    def test_a_nested_article_contributes_none_of_them(self):
        """Task 1's scoping has to reach the new counters too — a peer-review
        round is full of references and formulas."""
        row = sampler.measure_article(
            "PMC1",
            _article("""
            <sub-article><body><disp-formula><tex-math>x</tex-math></disp-formula>
              <back><ref-list><ref><note><p>n</p></note></ref></ref-list></back>
            </body></sub-article>"""),
        )

        assert (row.disp_formulas, row.tex_math, row.refs, row.refs_note_only) == (0, 0, 0, 0)
        assert row.unscoped["refs_note_only"] == 1

    def test_a_row_written_before_these_counters_reads_as_not_measured(self):
        row = sampler.ArticleMeasurement.from_dict({"pmcid": "PMC1"})

        assert row.refs == sampler.NOT_MEASURED
        assert row.disp_formulas == sampler.NOT_MEASURED
        assert row.contribs_multi_collab == sampler.NOT_MEASURED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py::TestTheFourWaitingPopulations -v`
Expected: FAIL — `AttributeError: 'ArticleMeasurement' object has no attribute 'collab_children'`.

- [ ] **Step 3: Add the fields and the sentinel tuple**

On `ArticleMeasurement`, after `unscoped`:

```python
    # Issue #142 — what a `<collab>` carries besides text. `<institution>` and
    # `<addr-line>` are legal children and are concatenated with no separator,
    # and which of the two candidate fixes is right is a question about how
    # publishers actually deposit it.
    collab_children: Counter[str] = field(default_factory=Counter)
    collabs_with_element_children: int = 0
    # Issue #143 — multiplicity, which section 11 cannot see: it counts
    # spellings per *article*, so one `<contrib>` carrying two `<collab>` is
    # invisible there. #117 is the precedent that "how many does one element
    # deposit?" decides between first-wins, last-wins and ranking.
    contribs_multi_collab: int = 0
    contribs_multi_string_name: int = 0
    name_alternatives: int = 0
    # Issue #147 — `<tex-math>` is taken from the prose containing it and
    # `<disp-formula>` dropped outright. `formula_alternatives_both` is the
    # count that rules out "add them to `_INLINE_ELEMENTS`": an `<alternatives>`
    # holding both encodings of one formula would emit it twice.
    disp_formulas: int = 0
    inline_formulas: int = 0
    tex_math: int = 0
    mml_math: int = 0
    formula_alternatives_both: int = 0
    disp_formulas_with_label: int = 0
    # Issue #150 — a `<ref>` whose only content is a `<note>` renders as an
    # empty `<li>`. The vocabulary is open, so a `<ref>` child nobody has
    # listed prints as itself rather than as evidence of nothing.
    refs: int = 0
    refs_note_only: int = 0
    ref_child_kinds: Counter[str] = field(default_factory=Counter)
```

In the constants block, after `_SCOPE_SIDE_COUNTERS`:

```python
# The fifth counter generation (issues #142, #143, #147, #150), and the same
# sentinel rule as the four before it: an article citing nothing and printing
# no formula genuinely measures zero here, so zero cannot also mean "this row
# predates the counter".
_WAITING_SIDE_COUNTERS = (
    "collabs_with_element_children",
    "contribs_multi_collab",
    "contribs_multi_string_name",
    "name_alternatives",
    "disp_formulas",
    "inline_formulas",
    "tex_math",
    "mml_math",
    "formula_alternatives_both",
    "disp_formulas_with_label",
    "refs",
    "refs_note_only",
)
```

and add `*_WAITING_SIDE_COUNTERS` to the `from_dict` loop.

- [ ] **Step 4: Count them in the walk**

In `_measure_tree`'s `walk`, in the `elif` chain after the `contrib` branch:

```python
            elif tag == "disp-formula":
                row.disp_formulas += 1
                if any(_local(c.tag) == "label" for c in child):
                    row.disp_formulas_with_label += 1
                _record_formula_alternatives(child, row)
            elif tag == "inline-formula":
                row.inline_formulas += 1
                _record_formula_alternatives(child, row)
            elif tag == "tex-math":
                row.tex_math += 1
            elif tag == "math":
                row.mml_math += 1
            elif tag == "ref":
                _record_ref(child, row)
```

and after `_record_contrib`, two helpers:

```python
def _record_formula_alternatives(el: ET.Element, row: ArticleMeasurement) -> None:
    """Count a formula whose ``<alternatives>`` holds two encodings of itself.

    This is the count that decides #147's shape: where one formula is
    deposited as both LaTeX and MathML, merging every accumulating child would
    emit it twice, so the fix cannot be one more ``_INLINE_ELEMENTS`` member.

    Args:
        el: The ``<disp-formula>`` or ``<inline-formula>``.
        row: The measurement to count into.
    """
    for child in el:
        if _local(child.tag) != "alternatives":
            continue
        kinds = {_local(g.tag) for g in child}
        if "tex-math" in kinds and "math" in kinds:
            row.formula_alternatives_both += 1


def _record_ref(el: ET.Element, row: ArticleMeasurement) -> None:
    """Count one ``<ref>`` and the kinds of child it carries.

    A ``<ref>`` whose only content is a ``<note>`` — ``<label>`` aside, which
    is the publisher's own number and not content — carries no citation for
    ``_format_ref_html`` to render, so it becomes an empty ``<li>`` (#150).
    The child vocabulary is open: JATS models ``<ref>`` as
    ``(label?, (citation | element-citation | mixed-citation | note | p | x)*)``
    and a spelling counted against a list this script wrote would be reported
    as absent.

    Args:
        el: The ``<ref>`` element.
        row: The measurement to count into.
    """
    row.refs += 1
    kinds = [_local(child.tag) for child in el]
    row.ref_child_kinds.update(kinds)
    content = [k for k in kinds if k != "label"]
    if content and set(content) == {"note"}:
        row.refs_note_only += 1
```

- [ ] **Step 5: Count #142 and #143 in `_record_contrib`**

Inside `_record_contrib`'s `descend`, in the branch that counts a spelling:

```python
            row.contrib_name_spellings[name] += 1
            if name in _CONTRIB_NAMING_ELEMENTS:
                found += 1
            if name == "collab":
                children = [_local(c.tag) for c in child]
                if children:
                    row.collab_children.update(children)
                    row.collabs_with_element_children += 1
                if any(_local(c.tag) == "contrib-group" for c in child):
                    row.collabs_with_a_roster += 1
```

replacing the existing `collab`/roster line, and after `descend(el)`:

```python
    descend(el)
    direct = Counter(_local(child.tag) for child in el)
    if direct["collab"] > 1:
        row.contribs_multi_collab += 1
    if direct["string-name"] > 1:
        row.contribs_multi_string_name += 1
    row.name_alternatives += direct["name-alternatives"]
    if not found:
```

Note `collab_children` counts a `<collab>`'s *element* children, so `<collab>The Y Group</collab>` contributes nothing — which is the distinction #142 turns on.

- [ ] **Step 6: Add report sections**

In `print_report`, after the existing section 11, add sections 12–15 following the same shape as the sections above them (a heading line, the counts, and `NOT MEASURED` where `totals.measured(...)` is false). Each heading names its issue so a reader knows what the number is for:

```
12. A <collab>'s element children (issue 142)
13. Contributor multiplicity per <contrib> (issue 143)
14. Formulas (issue 147)
15. References carrying only a <note> (issue 150)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py -v`
Expected: the new class passes; `TestTheCitedPopulationsAreWhatTheCorporaHold` still fails; nothing else does.

- [ ] **Step 8: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/sample_jats_exhibits.py tests/test_jats_exhibit_sampler.py
git commit -m "feat(scripts): size the four populations waiting on a measurement"
```

---

### Task 5: Measure the rendition gap

The cited corpus is the *archive* rendition; `FullTextService` feeds the parser Europe PMC's `fullTextXML`. #119 established these differ in a way that matters. Measure it rather than caveat it.

**Files:**
- Modify: `scripts/sample_jats_exhibits.py`
- Test: `tests/test_jats_exhibit_sampler.py`

**Interfaces:**
- Consumes: `measure_article` (Task 1), `_fetch` and `_make_pacer` (existing).
- Produces:
  - `RENDITION_OUTPUT: Path` = `Path("tests/data/jats_exhibits.rendition.json")`
  - `rendition_delta(archive: ArticleMeasurement, served: ArticleMeasurement) -> dict[str, Any]`
  - `compare_renditions(client, pace, articles: list[tuple[str, bytes]]) -> dict[str, Any]`
  - CLI: `--compare-europepmc N` (`int`, default `0` — off)

- [ ] **Step 1: Write the failing tests**

```python
class TestTheRenditionGapIsMeasured:
    """The archive rendition is not what bmlib parses, so the gap is measured.

    #119 found the difference is real: Springer's commented-out
    `<authorqueries>` block is in the archive copy of three articles and
    absent from Europe PMC's copy of the same three.
    """

    def test_identical_renditions_produce_no_delta(self):
        xml = _article("<fig id='f1'><label>Figure 1</label></fig>")
        archive = sampler.measure_article("PMC1", xml)
        served = sampler.measure_article("PMC1", xml)

        assert sampler.rendition_delta(archive, served) == {}

    def test_a_differing_field_is_named_with_both_values(self):
        archive = sampler.measure_article("PMC1", _article("<fig id='f1'/><fig id='f2'/>"))
        served = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))

        delta = sampler.rendition_delta(archive, served)

        assert delta["figures"] == {"archive": 2, "europepmc": 1}

    def test_a_counter_field_is_compared_as_a_mapping(self):
        archive = sampler.measure_article(
            "PMC1", _article("<fig id='f1'><label>F</label></fig>")
        )
        served = sampler.measure_article("PMC1", _article("<fig id='f1'/>"))

        delta = sampler.rendition_delta(archive, served)

        assert delta["label_parents"] == {"archive": {"fig": 1}, "europepmc": {}}

    def test_an_article_europe_pmc_will_not_serve_is_unmeasured(self):
        """Not "the renditions agree" — the distinction every population here
        is accounted by."""
        with mock.patch.object(sampler, "_fetch", return_value=None):
            report = sampler.compare_renditions(
                object(), lambda url: None, [("PMC1", _article("<fig id='f1'/>"))]
            )

        assert report["compared"] == 0
        assert report["unmeasured"] == 1
        assert report["articles_differing"] == 0

    def test_agreement_is_reported_as_a_population_not_as_silence(self):
        xml = _article("<fig id='f1'><label>F</label></fig>")
        with mock.patch.object(sampler, "_fetch", return_value=xml):
            report = sampler.compare_renditions(
                object(), lambda url: None, [("PMC1", xml), ("PMC2", xml)]
            )

        assert report["compared"] == 2
        assert report["unmeasured"] == 0
        assert report["articles_differing"] == 0
        assert report["fields_differing"] == {}
        assert report["deltas"] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py::TestTheRenditionGapIsMeasured -v`
Expected: FAIL — `AttributeError: module has no attribute 'rendition_delta'`.

- [ ] **Step 3: Implement**

```python
RENDITION_OUTPUT = Path("tests/data/jats_exhibits.rendition.json")


def rendition_delta(
    archive: ArticleMeasurement, served: ArticleMeasurement
) -> dict[str, Any]:
    """The fields where two renditions of one article disagree.

    Args:
        archive: The row measured from the baseline package's bytes.
        served: The row measured from Europe PMC's ``fullTextXML``.

    Returns:
        A mapping from field name to both values, empty where they agree.
        ``unscoped`` is skipped — it is itself a diff, so a difference in it
        is already reported by the fields it is a diff of.
    """
    out: dict[str, Any] = {}
    for name, value in archive.__dict__.items():
        if name in ("pmcid", "unscoped"):
            continue
        other = getattr(served, name)
        if value != other:
            out[name] = {
                "archive": dict(value) if isinstance(value, Counter) else value,
                "europepmc": dict(other) if isinstance(other, Counter) else other,
            }
    return out


def compare_renditions(
    client: Any, pace: Any, articles: list[tuple[str, bytes]]
) -> dict[str, Any]:
    """Measure each article in both renditions and report where they disagree.

    The citable corpus is the *archive* rendition; ``FullTextService`` feeds
    the parser Europe PMC's ``fullTextXML``. #119 measured that these differ
    in a way that reaches a scan — Springer's commented-out ``<authorqueries>``
    block is in the archive copy of three articles and absent from Europe
    PMC's copy of the same three — so citing an archive figure for a parser
    fed by Europe PMC is a claim, and this is what tests it.

    Args:
        client: An ``httpx.Client``.
        pace: The per-host pacer.
        articles: ``(pmcid, archive_bytes)`` pairs drawn from the corpus.

    Returns:
        The comparison: how many were compared, how many could not be
        (unmeasured, entering no denominator), how many differ at all, which
        fields differ and how often, and the per-article deltas.
    """
    compared = unmeasured = 0
    fields: Counter[str] = Counter()
    deltas: dict[str, dict[str, Any]] = {}
    for pmcid, archive_xml in articles:
        served_xml = _fetch(client, f"{EUROPE_PMC}/{pmcid}/fullTextXML", pace)
        archive = measure_article(pmcid, archive_xml)
        served = measure_article(pmcid, served_xml) if served_xml else None
        if archive is None or served is None:
            unmeasured += 1
            continue
        compared += 1
        delta = rendition_delta(archive, served)
        if delta:
            deltas[pmcid] = delta
            fields.update(delta)
    return {
        "compared": compared,
        "unmeasured": unmeasured,
        "articles_differing": len(deltas),
        "fields_differing": dict(fields),
        "deltas": deltas,
    }
```

- [ ] **Step 4: Wire the flag**

Add to `_build_arg_parser`:

```python
    parser.add_argument(
        "--compare-europepmc",
        type=int,
        default=0,
        metavar="N",
        help=(
            "After a --package draw, re-fetch N of the drawn articles from "
            "Europe PMC and report where the two renditions disagree. This is "
            "what licenses citing an archive-drawn figure for a parser fed by "
            "fullTextXML."
        ),
    )
```

a refusal in `_validate_args`:

```python
    if args.compare_europepmc and not args.package:
        return "--compare-europepmc compares a --package draw against Europe PMC"
    if args.compare_europepmc < 0:
        return f"--compare-europepmc must not be negative, got {args.compare_europepmc}"
```

and, in `main`'s package branch after the measuring loop, hold the first N drawn `(pmcid, xml)` pairs, then after `print_report`:

```python
    if args.compare_europepmc:
        pace = _make_pacer(args.per_host_interval)
        with httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=60.0,
                          follow_redirects=True) as client:
            comparison = compare_renditions(client, pace, for_comparison)
        RENDITION_OUTPUT.write_text(
            json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"\nRendition: {comparison['compared']} compared, "
            f"{comparison['unmeasured']} unmeasured, "
            f"{comparison['articles_differing']} differing"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jats_exhibit_sampler.py -v`
Expected: the new class passes; only `TestTheCitedPopulationsAreWhatTheCorporaHold` still fails.

- [ ] **Step 6: Commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add scripts/sample_jats_exhibits.py tests/test_jats_exhibit_sampler.py
git commit -m "feat(scripts): measure the archive-versus-served rendition gap"
```

---

### Task 6: Draw the corpora

The measurement run. No code changes — if a defect surfaces here, fix it in the task that owns it and re-run.

**Files:**
- Create/replace: `tests/data/jats_exhibits.json`, `tests/data/jats_exhibits.backfill.json`, `tests/data/jats_exhibits.rendition.json`
- Delete: the two stale `*.journal.jsonl` files — they hold rows from the *unscoped* walk, and the journal tops a sample up rather than starting over, so leaving them pools two instruments' output into one corpus.

- [ ] **Step 1: Clear the stale journals**

```bash
git rm tests/data/jats_exhibits.journal.jsonl tests/data/jats_exhibits.backfill.journal.jsonl
```

The journals are rows measured by the pre-#138 walk. Keeping them would silently mix scoped and unscoped rows in one corpus — the exact confusion this whole change removes.

- [ ] **Step 2: Draw the recent window with the rendition comparison**

```bash
uv run python scripts/sample_jats_exhibits.py \
    --package /Users/hherb/pmc_archive/packages/PMC012xxxxxx \
    --from-year 2023 --to-year 2025 --target 1000 --seed 0 \
    --compare-europepmc 300
```

Expected: **97,668 candidates**, 1,000 rows, a full report, `tests/data/jats_exhibits.json` and `tests/data/jats_exhibits.rendition.json` written, exit 0. **A non-zero exit means a population was unreportable** and the corpus went to `*.unreportable.json`; read the report, fix the cause, re-run. Do not commit an unreportable draw.

- [ ] **Step 3: Draw the back-filled window**

```bash
uv run python scripts/sample_jats_exhibits.py \
    --package /Users/hherb/pmc_archive/packages/oa_comm_xml.PMC002xxxxxx.baseline.2025-06-26.tar.gz \
    --from-year 1996 --to-year 1998 --target 1000 --seed 0 \
    -o tests/data/jats_exhibits.backfill.json
```

Expected: **3,141 candidates**, 1,000 rows, exit 0. Two sequential passes over one 1.6 GB tarball, roughly 35 seconds.

`PMC000xxxxxx` and `PMC001xxxxxx` are deliberately *not* passed: both measure
**0** articles published 1996–1998, because accession order is deposit order
rather than publication order. Naming them would put packages in the corpus
header that contributed nothing. If the candidate count differs from 3,141,
report it in the PR rather than adjusting the plan.

- [ ] **Step 4: Record the report output**

Save both runs' printed reports; Task 7 reconciles prose against them, and the PR body quotes them. Put them in the scratchpad, not in the repo.

- [ ] **Step 5: Commit the corpora**

```bash
git add tests/data/jats_exhibits.json tests/data/jats_exhibits.backfill.json \
        tests/data/jats_exhibits.rendition.json
git commit -m "test(data): redraw both corpora from the named baseline packages"
```

---

### Task 6a: Measure the rendition the parser is actually fed

**Found by Task 5's instrument during Task 6's draw, and it invalidates the
plan's original premise.** The citable corpus was to be the archive rendition
of a named baseline package. The rendition comparison says that will not do:
288 of 294 compared articles differ, and they differ on precisely the
populations this repo cites.

> **SUPERSEDED — every number in this section is from the artifact as it stood
> before the `_YEAR_RE` fix.** That commit redrew the recent window and
> regenerated `tests/data/jats_exhibits.rendition.json`, which now holds
> **289 differing of 300 compared** (`unmeasured` 0) and `last_is_thumb`
> **156 / 0 / 781**. Every row of the table below moved with it. The committed
> artifact is the evidence; this section is kept as the record of what the
> instrument found at the time it forced the design reversal. Quote
> `tests/data/jats_exhibits.rendition.json`, never this table.

**The column below is "where they differ", and mislabelling it "archive
total" is a live error this plan made in its own first draft.**
`rendition_delta` records a field *only* where the two renditions disagree, so
an agreeing article appears nowhere in the artifact and these sums are sums
over the disagreements — not corpus totals. Summed over every recorded article
the numbers are larger (`figures_multi_graphic` 21, `graphics` 1,733,
`alternatives_members` 410, `sections` 5,606), and the true archive totals over
all 294 are not derivable from the file at all.

| population | articles differing (of 294) | archive, where they differ | Europe PMC, where they differ |
|---|---|---|---|
| `last_is_thumb` | 153 | **0** | 641 |
| `figures_multi_graphic` | 152 | 8 | 636 |
| `alternatives_members` | 151 | 190 | 1,288 |
| `graphics` | 152 | 862 | 1,388 |
| `sections` | 174 | 3,355 | 5,070 |

Spot-checked directly on `PMC12000032`: the archive deposits
`<graphic xlink:href="fpsyg-16-1522092-g001" position="float"/>` — no
extension, no `content-type` — while Europe PMC emits an `image`/`thumb` pair
with `.jpg`/`.gif`. **That is one article's shape and not a general
mechanism**: `PMC12169732`, in the same artifact, deposits its own four
thumbnails as `specific-use="thumbnail"` where Europe PMC re-labels them
`content-type="thumb"`, and records no `last_is_thumb` delta at all because
both renditions measure four.

The conclusion survives the correction intact: issue #117's population (the
ranking rule, "carries several graphics", "ends on a thumbnail") is a property
of the served rendition, and `FullTextService` feeds the parser `fullTextXML`,
so archive figures would state the opposite of the truth for what bmlib parses
— a fresh instance of the defect #132 and #158 exist to remove.

**What changes, and what does not.** The *sample* stays package-defined and
deterministic: any reader re-derives the identifier list from
`(packages, window, target, seed)`. Only the *bytes measured* move to
`fullTextXML`. Re-derivability survives intact; the rendition mismatch does
not.

**Files:**
- Modify: `scripts/sample_jats_exhibits.py`
- Test: `tests/test_jats_exhibit_sampler.py`

**Interfaces:**
- Consumes: `package_candidates`, `draw`, `_hold_for_comparison`, `_fetch`,
  `_make_pacer`, `measure_article` (Tasks 2, 3, 5).
- Produces: CLI `--measure-europepmc` (flag, default off); the corpus header
  gains `rendition: "europepmc" | "archive"`.

- [ ] **Step 1: Write the failing tests**

Cover, with `_fetch` mocked and no network:
- with the flag, rows are measured from the *served* bytes, not the package's
  — a fixture where the two renditions differ must produce the served row;
- the identifier list is unchanged by the flag, so the same
  `(packages, window, target, seed)` draws the same articles either way;
- an article Europe PMC will not serve is **unmeasured**, entering no
  denominator — never silently measured from the archive copy instead, which
  would mix renditions inside one corpus;
- the header records `rendition`, and records it on both settings;
- the unmeasured-share rule applies, so a throttled run writes
  `*.unreportable.json` rather than a thin corpus.

- [ ] **Step 2: Run them and watch them fail**

`uv run pytest tests/test_jats_exhibit_sampler.py -k rendition -v`

- [ ] **Step 3: Implement**

Reuse the comparison path's plumbing rather than adding a second HTTP route.
The package branch of `main` already draws identifiers; with the flag set,
fetch each drawn identifier's `fullTextXML` through the existing paced client
and measure *that*. Mixing renditions within one corpus is the one outcome
that must be impossible — an article whose fetch fails is unmeasured, never
back-filled from the package.

- [ ] **Step 4: Record the artifact identity properly**

The recent corpus's header reads `packages: ['PMC012xxxxxx']`, which is a
directory name on one machine, not the public artifact — it loses the
`baseline.2025-06-26` that makes the draw re-derivable, which is the whole
point. Record the baseline package identity whether the source given is the
tarball or a directory extracted from it.

- [ ] **Step 5: Verify, lint, commit**

---

### Task 6b: Redraw both windows on the served rendition

**Files:** replaces `tests/data/jats_exhibits.json` and
`tests/data/jats_exhibits.backfill.json`. The archive-drawn corpora and
`jats_exhibits.rendition.json` stay in git history as the evidence for why
this task exists.

- [ ] **Step 1: Clear both journals** — they hold archive-rendition rows, and
  the journal tops a sample up rather than starting over.

- [ ] **Step 2: Recent window**, `--measure-europepmc`, target 1,000. Roughly
  12 minutes of paced fetches.

- [ ] **Step 3: Back-filled window**, same. Europe PMC's coverage of 1996-1998
  `oa_comm` articles is **unmeasured** — if it is poor, the unmeasured-share
  rule refuses the draw, and that refusal is a finding to report rather than a
  failure to work around.

- [ ] **Step 4: Re-check the three flags Task 6 raised**, each of which may
  have been an archive artifact:
  - the `<label>` direct-child premise measured **violated** (6,701 of 6,708),
    the first violation ever recorded — CLAUDE.md says it measures full
    (2,033/2,033, 1,446/1,446, 365/365). If it still fails on the served
    rendition, that is a real finding about the parent rule and wants an issue,
    not a prose edit.
  - the back-filled window held **0** `<table-wrap>` and 13 `<ref>` across
    1,000 articles, against 93 tables in 300 in the old draw — so #127's
    image-only-table population may not be in this window at all.
  - whether the `<sub-article>` scoping correction still moves what Task 1
    measured, now that the bytes are different.

- [ ] **Step 5: Commit the corpora.**

---

### Task 7: Reconcile every figure cited from the corpora

`TestTheCitedPopulationsAreWhatTheCorporaHold` has been failing since Task 1. Its failures are the checklist, and its docstring says so: *"A redraw is meant to break it — that is the signal to reconcile the comments, and the failure names the file to reconcile."*

**Files:**
- Modify: `tests/test_jats_exhibit_sampler.py` (the cited-populations class), `bmlib/fulltext/jats_parser.py` (comments only), `scripts/sample_jats_exhibits.py` (module docstring), `CLAUDE.md`, `docs/manual/fulltext.md`, `CHANGELOG.md`, `ROADMAP.md`, `HANDOVER.md`

- [ ] **Step 1: Read the new numbers out of the corpora**

```bash
uv run pytest tests/test_jats_exhibit_sampler.py::TestTheCitedPopulationsAreWhatTheCorporaHold -v
```

Each failure names an assertion and prints the corpus's actual value. Work through them one at a time; do not batch-edit numbers from the report, because the report and the corpus are two renderings and the test reads the corpus.

- [ ] **Step 2: Update the assertions to the redrawn values**

Keep every assertion — they are the net. Only the numbers move. Add assertions for the new populations so they are pinned like the rest, and one for the corpus header:

```python
    def test_the_corpora_say_which_artifact_they_were_drawn_from(self):
        """The whole point of the redraw: a reader can re-derive these."""
        for path in (self.RECENT, self.BACKFILL):
            window = json.loads(path.read_text())["window"]
            assert window["source"] == "package"
            assert all("baseline.2025-06-26" in p or p.startswith("PMC") for p in window["packages"])
            assert window["seed"] == 0
```

- [ ] **Step 3: Reconcile `bmlib/fulltext/jats_parser.py`**

Comments only — **no behaviour changes in this task**. Every figure drawn from these corpora moves. Grep for the ones the spec names:

```bash
grep -n "276\|0\.7%\|49\.9\|49\.5\|58\.0\|52\.9\|1,446\|1,413\|755\|662\|93\b" bmlib/fulltext/jats_parser.py
```

The framing is **"redrawn from a named public artifact"**, not "corrected": every figure moves both because the walk is scoped and because the sample is different, and attributing the movement to the scoping alone is a claim the draw cannot support. Where a figure came from the vanished 276-article draw, say it is superseded and name the package.

- [ ] **Step 4: Reconcile the other five files**

```bash
grep -rn "276-article\|0\.7%\|49\.9%\|58\.0%\|288 of 1,022\|4 in 249\|6 of 876" \
    CLAUDE.md docs/manual/fulltext.md CHANGELOG.md ROADMAP.md HANDOVER.md
```

`CHANGELOG.md`'s entries are still under `[Unreleased]`, so they are edited rather than corrected — which is why the spec argues this had to happen before the release.

**#158 specifically:** the four disagreeing nested-article rates now have one measurement over a named corpus. Reconcile all four citation sites, and say which population each number is of — "carries a region" and "loses body text to one" are different claims, the first bounding the second.

- [ ] **Step 5: Update the sampler's own module docstring**

The "One scope the walk does not share with the parser" paragraph is now false — that is what Task 1 fixed. Replace it with what the walk does now and what `unscoped` records. Update the usage examples to the package form, and the `--months-ago` paragraph to say the live path remains for the rendition it measures.

- [ ] **Step 6: Add the new evidence to `CLAUDE.md`**

Under the `fulltext/` entry, state the rendition gap as a measured population and the four newly sized ones, each as a number with its corpus named. Follow the file's existing voice: a rule, the measurement behind it, and what goes wrong without it.

- [ ] **Step 7: Run everything**

```bash
uv run pytest tests/ -v
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
uv run mypy
```

Expected: all green. Baseline before this branch was **2837 passed, 63 skipped**; the count rises by the tests added in Tasks 1–5.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "docs: reconcile every figure cited from the redrawn corpora"
```

- [ ] **Step 9: Post the measured populations to the four waiting issues**

Each of 142, 143, 147 and 150 gets a comment with its measured population from both windows, naming the corpus. **They stay open** — the measurement makes the rule decidable, it does not decide it. Use `gh issue comment`, and write no closing keyword before any issue number.

---

## Self-Review

**Spec coverage.** Spec §1 (second source) → Task 2 + Task 3 Step 4. §2 (absolute windows, determinism, header) → Task 3. §3 (scoping, both counts) → Task 1. §4 (four counter families) → Task 4. §5 (rendition gap) → Task 5. §6 (reconciliation) → Task 7. The two draws → Task 6. The spec's "what this PR does not do" is honoured: Task 7 Step 3 forbids behaviour changes in `jats_parser.py`, and Step 9 keeps the four issues open.

**Type consistency.** `measure_article(pmcid: str, xml: bytes) -> ArticleMeasurement | None` is unchanged throughout and is what Tasks 5 and 6 call. `_measure_tree(pmcid, root, *, scoped)` returns a bare `ArticleMeasurement`, never `None` — only `measure_article` handles the parse failure. `iter_package_articles` yields `(str, bytes)` in Tasks 2, 3 and 6; `read_package_articles` yields the same. `rendition_delta` takes two rows and returns `dict[str, Any]`; `_row_difference` takes two rows and returns `dict[str, Any]` but keys it by *unscoped value* alone, where `rendition_delta` keys it by both values — deliberately different shapes for different questions, and each has a test asserting its own shape.

**Sentinel completeness.** Two new counter generations are added (`_SCOPE_SIDE_COUNTERS` in Task 1, `_WAITING_SIDE_COUNTERS` in Task 4) and both are wired into `from_dict`, each with a test asserting an older row reads as `NOT_MEASURED`. `unscoped` deliberately has *no* sentinel: an empty mapping is the correct reading for both "no nested article" and "written before this existed", because in neither case is there a difference to report.
