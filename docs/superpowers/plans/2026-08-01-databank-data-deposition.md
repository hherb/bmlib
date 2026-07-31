# DataBankList Data Deposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Credit a paper's data-availability score from the repository accessions PubMed already reports in `<DataBankList>`, instead of only from a seven-substring scan of the full text.

**Architecture:** `data_level` gains a second producer, so sub-steps stop assigning it and start *nominating* through `_Analysis.note_data_level()`, which keeps the highest-ranked level; `analyze()` then awards the component once, after every step has run. `_parse_pubmed_signals()` collects deposition repository names from the `<DataBank>` entries it currently skips, and `_merge_pubmed_signals()` turns them into a level and an indicator line.

**Tech Stack:** Python 3.11+, stdlib `xml.etree.ElementTree`, pytest. No new dependencies.

**Design:** [`docs/superpowers/specs/2026-08-01-databank-data-deposition-design.md`](../specs/2026-08-01-databank-data-deposition-design.md) — read it first; it records what was rejected and why.

## Global Constraints

- **Every source file carries the AGPL-3 header.** All files here already exist and already have it; do not touch it.
- **Type hints and docstrings on everything public.** `_Analysis`, `_PubMedSignals` and the module functions here are private but the module documents them anyway — match the surrounding density.
- **Tests:** `uv run pytest tests/test_transparency.py -v`. Full suite before the final commit: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not `.venv`'s:** `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`. Line length 100.
- **`uv` only, never bare pip.**
- **All work happens on branch `feature/databank-data-deposition`**, which already exists and already holds the design commit.
- Only two files carry code changes: `bmlib/transparency/analyzer.py` and `tests/test_transparency.py`.

---

### Task 1: Fill the three gaps in `_TRIAL_REGISTRY_NAMES`

Independent of the rest of the plan and worth its own commit: it fixes an existing defect rather than building the feature. NLM's published vocabulary lists `JMACCT` and `REPEC`, neither of which bmlib knows, and spells UMIN's registry `UMIN CTR` where bmlib has only the hyphenated `umin-ctr` — so a paper registered in any of the three silently loses `SCORE_TRIAL_REGISTERED` today.

**Files:**
- Modify: `bmlib/transparency/analyzer.py:168-191` (`_TRIAL_REGISTRY_NAMES`)
- Test: `tests/test_transparency.py` (class `TestPubMedSignalParsing`, ~line 1281)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing later tasks depend on. `_TRIAL_REGISTRY_NAMES` keeps its name and type (`frozenset[str]`).

- [ ] **Step 1: Write the failing test**

Add to `TestPubMedSignalParsing` in `tests/test_transparency.py`:

```python
    @pytest.mark.parametrize("name", ["JMACCT", "REPEC", "UMIN CTR"])
    def test_registries_nlm_publishes_are_all_recognised(self, name):
        # All three appear in NLM's DataBankName vocabulary and none was in
        # bmlib's set: JMACCT and REPEC were missing outright, and UMIN's
        # registry was spelled "umin-ctr" where NLM's table says "UMIN CTR",
        # so the exact-match test failed on the string PubMed emits. Each
        # silently cost the paper SCORE_TRIAL_REGISTERED.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=((name, ("X1",)),)))
        assert signals.registration_not_checkable is True
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_transparency.py -k registries_nlm_publishes -v`
Expected: 3 FAILED, each `assert False is True` — the names fall through the `if name not in _TRIAL_REGISTRY_NAMES: continue` guard.

- [ ] **Step 3: Add the three names**

In `bmlib/transparency/analyzer.py`, add `"jmacct"` after `"japiccti"`, `"repec"` after `"rebec"`, and `"umin ctr"` beside `"umin-ctr"`, keeping the set alphabetical as it already is. Then extend the comment above the set:

```python
# `DataBankName` values PubMed emits for clinical-trial registries, lowercased
# for matching. Anything outside this set — GENBANK, PDB, SRA, Dryad, … — is a
# data-deposition accession, handled by `_DEPOSITION_DATABANK_NAMES` below.
#
# Curated from NLM's published vocabulary:
# https://www.nlm.nih.gov/bsd/medline_databank_source.html
# Both spellings of UMIN's registry are kept: NLM's table says "UMIN CTR" but
# the hyphenated form appears in older records. "jrct" and "iran registry of
# clinical trials" are not in NLM's table and are kept anyway — they cost
# nothing, and jRCT is the live successor to Japan's earlier registries.
```

- [ ] **Step 4: Run the test and the whole transparency file**

Run: `uv run pytest tests/test_transparency.py -v`
Expected: all PASS, 3 more tests than before.

- [ ] **Step 5: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "fix(transparency): recognise the trial registries NLM publishes

JMACCT and REPEC were missing from _TRIAL_REGISTRY_NAMES, and UMIN's
registry was spelled \"umin-ctr\" where NLM's vocabulary says \"UMIN CTR\" —
so the exact-match test failed on the string PubMed actually emits. A paper
registered in any of the three lost SCORE_TRIAL_REGISTERED silently."
```

---

### Task 2: `_Analysis.note_data_level()` and the level ranking

The merge mechanism, built and tested before anything produces a second level.

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — add `_DATA_LEVEL_RANK` after the `SCORE_*` block (~line 227), add the method to `_Analysis` (~line 508, after `note_industry_coi`)
- Test: `tests/test_transparency.py` (class `TestAnalysisCarrier`, ~line 240)

**Interfaces:**
- Consumes: the existing `_Analysis` dataclass and the level strings `_DATA_PATTERNS` already produces (`full_open`, `on_request`, `not_available`, plus the `unknown` default).
- Produces: `_DATA_LEVEL_RANK: dict[str, int]` and `_Analysis.note_data_level(level: str) -> None`. Tasks 3 and 5 both call the method; Task 3's test imports the rank map.

- [ ] **Step 1: Write the failing tests**

Add to `TestAnalysisCarrier` in `tests/test_transparency.py`:

```python
    @pytest.mark.parametrize(
        ("weaker", "stronger"),
        [
            ("unknown", "not_available"),
            ("unknown", "on_request"),
            ("unknown", "full_open"),
            ("not_available", "on_request"),
            ("not_available", "full_open"),
            ("on_request", "full_open"),
        ],
    )
    def test_the_stronger_data_level_wins_in_either_arrival_order(self, weaker, stronger):
        # Two sources produce `data_level` and neither can know which ran
        # first, so the merge must not depend on order — the same rule
        # `industry_confidence` follows.
        forwards, backwards = _Analysis(), _Analysis()
        forwards.note_data_level(weaker)
        forwards.note_data_level(stronger)
        backwards.note_data_level(stronger)
        backwards.note_data_level(weaker)
        assert forwards.data_level == stronger
        assert backwards.data_level == stronger

    def test_an_explicit_denial_outranks_silence(self):
        # `not_available` is a finding; `unknown` is the absence of one.
        analysis = _Analysis()
        analysis.note_data_level("not_available")
        analysis.note_data_level("unknown")
        assert analysis.data_level == "not_available"

    def test_a_level_outside_the_ranking_raises(self):
        # "restricted" is a level `calculate_risk_level` accepts from callers
        # who compute it themselves, and one the analyzer has never produced.
        # Ranking an unknown string at zero would silently demote it below
        # everything; failing loudly is the point.
        with pytest.raises(KeyError):
            _Analysis().note_data_level("restricted")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_transparency.py::TestAnalysisCarrier -v`
Expected: FAIL with `AttributeError: '_Analysis' object has no attribute 'note_data_level'`.

- [ ] **Step 3: Add the ranking and the method**

In `bmlib/transparency/analyzer.py`, immediately after `MAX_TRANSPARENCY_SCORE = 100`:

```python
# Data-availability levels ranked by how much data sharing is *established*,
# so a second producer of `data_level` can be merged rather than having to
# assume it runs last. An explicit denial outranks silence because it is a
# finding rather than the absence of one; any positive level outranks the
# denial. `calculate_risk_level()` accepts two further levels, "restricted"
# and "not_stated", which the analyzer has never produced — they are for
# callers computing the level themselves, and are deliberately absent here so
# that nominating one raises rather than ranking at zero.
_DATA_LEVEL_RANK = {
    "unknown": 0,
    "not_available": 1,
    "on_request": 2,
    "full_open": 3,
}
```

Then, in `_Analysis`, directly after `note_industry_coi()`:

```python
    def note_data_level(self, level: str) -> None:
        """Nominate *level* as the paper's data availability; the strongest wins.

        Two sources produce this — Europe PMC's full-text pattern scan and
        PubMed's ``<DataBankList>`` deposition accessions — and neither can
        know whether the other ran first, so the field is merged by rank
        rather than assigned. A source that found nothing nominates
        ``"unknown"``, which is a no-op: finding nothing is not evidence
        against what another source found.

        Args:
            level: A key of :data:`_DATA_LEVEL_RANK`.

        Raises:
            KeyError: If *level* is not a level the analyzer produces. A typo
                must fail loudly rather than silently rank below everything.
        """
        if _DATA_LEVEL_RANK[level] > _DATA_LEVEL_RANK[self.data_level]:
            self.data_level = level
```

Add to the `_Analysis` docstring's `data_level` attribute line:

```
        data_level: Data-availability level from :data:`_DATA_PATTERNS` or a
            PubMed deposition accession; the strongest evidence seen wins,
            regardless of arrival order. Set through
            :meth:`note_data_level`, never assigned.
```

- [ ] **Step 4: Run and watch them pass**

Run: `uv run pytest tests/test_transparency.py::TestAnalysisCarrier -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "feat(transparency): rank data-availability levels and merge by rank

_Analysis.note_data_level() keeps the strongest level any source found,
regardless of arrival order — the rule industry_confidence already follows,
and the precondition for data_level having a second producer."
```

---

### Task 3: Award the data component once, at the end of `analyze()`

`_check_europepmc` currently nominates the level and spends the points in one breath. Two producers make that double-count. Sub-steps stop scoring; `analyze()` scores the winner.

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — add `_INDICATOR_DATA_NOT_AVAILABLE` (~line 210), add `_score_data_availability()` after `_merge_pubmed_signals` (~line 551), edit `_check_europepmc` (lines 920-933), edit `analyze()` (~line 798)
- Test: `tests/test_transparency.py` (class `TestDataAvailabilityPatterns`, lines 745-807)

**Interfaces:**
- Consumes: `_Analysis.note_data_level()` from Task 2.
- Produces: `_score_data_availability(analysis: _Analysis) -> None` and `_INDICATOR_DATA_NOT_AVAILABLE: str`. Task 5's tests import both.

- [ ] **Step 1: Update the existing tests to the new split**

Three tests in `TestDataAvailabilityPatterns` assert a score that `_check_europepmc` no longer awards. Rewrite the class body so each nominates and then scores explicitly. Replace lines 745-807 entirely with:

```python
class TestDataAvailabilityPatterns:
    """Negated data-availability phrasing must not read as data sharing."""

    def _europepmc_level(self, abstract: str) -> _Analysis:
        """Run the Europe PMC step over *abstract* and return the carrier."""
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        analyzer._check_europepmc(
            _FakeFullTextClient(None), _epmc_record(abstract, in_epmc="N"), analysis
        )
        return analysis

    def test_not_available_upon_request_is_not_available(self):
        analysis = self._europepmc_level("The data are not available upon reasonable request.")
        assert analysis.data_level == "not_available"
        _score_data_availability(analysis)
        assert analysis.score == 0  # no on_request credit awarded
        assert analysis.indicators == [_INDICATOR_DATA_NOT_AVAILABLE]

    def test_available_upon_request_still_credited(self):
        analysis = self._europepmc_level(
            "Data are available from the authors upon reasonable request."
        )
        assert analysis.data_level == "on_request"
        # The step nominates; analyze() scores the winner exactly once.
        assert analysis.score == 0
        _score_data_availability(analysis)
        assert analysis.score == SCORE_DATA_ON_REQUEST

    def test_mixed_statement_negation_takes_precedence(self):
        # Deliberate: when an abstract carries both a sharing cue and a
        # negation ("code on GitHub" + "data not available"), the conservative
        # negation-first ordering of _DATA_PATTERNS wins.
        analysis = self._europepmc_level(
            "Analysis code is available on GitHub; individual patient data are not available."
        )
        assert analysis.data_level == "not_available"
        _score_data_availability(analysis)
        assert analysis.score == 0

    def test_a_step_that_found_nothing_does_not_lower_an_established_level(self):
        # This replaces `test_a_level_this_step_did_not_find_is_not_scored`,
        # which pinned the pre-merge rule that this step assigns `data_level`
        # outright. With a second producer that rule inverts: finding nothing
        # is not evidence against what another source found, so nominating
        # "unknown" must be a no-op rather than a demotion. The half that
        # still holds — this step never scores a level it did not find — now
        # holds because the step scores nothing at all.
        analysis = _Analysis(data_level="full_open")
        analyzer = TransparencyAnalyzer()
        analyzer._check_europepmc(
            _FakeFullTextClient(None),
            _epmc_record("This abstract says nothing about data.", in_epmc="N"),
            analysis,
        )
        assert analysis.data_level == "full_open"
        assert analysis.score == 0

    def test_the_component_is_awarded_once_however_many_sources_nominated(self):
        # The hazard deferring the award exists to remove.
        analysis = _Analysis()
        analysis.note_data_level("full_open")
        analysis.note_data_level("full_open")
        _score_data_availability(analysis)
        assert analysis.score == SCORE_DATA_FULL_OPEN
```

Add `SCORE_DATA_FULL_OPEN`, `SCORE_DATA_ON_REQUEST`, `_INDICATOR_DATA_NOT_AVAILABLE` and `_score_data_availability` to the import block at the top of `tests/test_transparency.py` (lines 23-41), keeping it alphabetically sorted the way ruff's `I` rule requires — underscore-prefixed names sort before the capitalised ones, as the existing block shows.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_transparency.py::TestDataAvailabilityPatterns -v`
Expected: collection error — `ImportError: cannot import name '_score_data_availability'`.

- [ ] **Step 3: Add the indicator constant and the scoring function**

In `bmlib/transparency/analyzer.py`, with the other indicator constants (after `_INDICATOR_INDUSTRY_COI`):

```python
_INDICATOR_DATA_NOT_AVAILABLE = "Data explicitly not available"
```

After `_merge_pubmed_signals()`:

```python
def _score_data_availability(analysis: _Analysis) -> None:
    """Award the data-availability component once, for the level that won.

    Called by :meth:`TransparencyAnalyzer.analyze` after every sub-step has
    nominated, rather than by the step that finds a level. With two producers
    — Europe PMC's text scan and PubMed's deposition accessions — scoring at
    the point of discovery would either spend the component twice or spend it
    on a level later beaten; deferring makes both unrepresentable rather than
    guarded against, which is what :meth:`_Analysis.award_funder_info` does
    for the funder component.

    It is also what keeps :data:`_INDICATOR_DATA_NOT_AVAILABLE` honest: the
    line is written only if that level survived the merge, so it never has to
    be retracted the way the PubMed COI lines are.
    """
    if analysis.data_level == "full_open":
        analysis.score += SCORE_DATA_FULL_OPEN
    elif analysis.data_level == "on_request":
        analysis.score += SCORE_DATA_ON_REQUEST
    elif analysis.data_level == "not_available":
        analysis.indicators.append(_INDICATOR_DATA_NOT_AVAILABLE)
```

- [ ] **Step 4: Make `_check_europepmc` nominate instead of score**

Replace the data-availability block in `_check_europepmc` (currently lines 920-933) with:

```python
        # Data availability. The level is found into a local and nominated
        # once: this step is one of two producers, and the winner is scored by
        # `_score_data_availability()` after every step has run. Nominating
        # unconditionally — including the "unknown" this falls through to —
        # keeps the step free of a "is this worth reporting?" judgement only
        # the carrier can make.
        data_level = "unknown"
        for pattern, level in _DATA_PATTERNS.items():
            if pattern in search_text:
                data_level = level
                break
        analysis.note_data_level(data_level)
```

Update the method's docstring paragraph that says it is the field's only producer — replace the sentence "This step is the field's only producer today; a second one has to bring a merge rule with it." wherever it survives, since the second producer now exists.

- [ ] **Step 5: Call it from `analyze()`**

In `analyze()`, between the `if not self._api_reachable:` block and `analysis.score = min(...)`:

```python
        # Awarded here rather than by the step that found the level: two
        # sources nominate one, and the component is worth its points once.
        _score_data_availability(analysis)

        analysis.score = min(analysis.score, MAX_TRANSPARENCY_SCORE)
```

- [ ] **Step 6: Run the whole transparency file**

Run: `uv run pytest tests/test_transparency.py -v`
Expected: all PASS. If any test outside `TestDataAvailabilityPatterns` fails, it asserted a score that included a data component awarded by `_check_europepmc`; the fix is the same split — assert the level, then call `_score_data_availability`.

- [ ] **Step 7: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "refactor(transparency): award the data component once, at the end

_check_europepmc nominated the level and spent the points in one breath,
which double-counts as soon as a second source nominates. Sub-steps now only
nominate; analyze() scores the winner through _score_data_availability().
'Data explicitly not available' is written only if that level survived, so it
never needs retracting."
```

---

### Task 4: Collect deposition repositories in `_parse_pubmed_signals`

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — add the two allow-lists after `_TRIAL_REGISTRY_NAMES` (~line 192), add the field to `_PubMedSignals` (~line 337), rewrite the `<DataBank>` loop (lines 369-396)
- Test: `tests/test_transparency.py` — extend `_pubmed_xml` (~line 1168), add tests to `TestPubMedSignalParsing`

**Interfaces:**
- Consumes: `_pubmed_xml` test helper, `_PubMedSignals`.
- Produces: `_PubMedSignals.deposition_databanks: tuple[str, ...]`, `_DEPOSITION_DATABANK_NAMES: frozenset[str]`, `_CONTROLLED_DEPOSITION_DATABANK_NAMES: frozenset[str]`. Task 5 consumes all three.

- [ ] **Step 1: Let the test helper omit `<AccessionNumberList>`**

In `tests/test_transparency.py`, change `_pubmed_xml`'s signature and databank rendering so a `None` accession value omits the element entirely (an empty tuple keeps rendering an empty list, which is the other case worth testing):

```python
def _pubmed_xml(
    *,
    coi: str | None = None,
    databanks: tuple[tuple[str, tuple[str, ...] | None], ...] = (),
    agencies: tuple[str, ...] = (),
) -> str:
    """Build a minimal PubmedArticleSet response.

    *databanks* is a tuple of ``(DataBankName, accession numbers)`` pairs.
    Accessions of ``None`` omit ``<AccessionNumberList>`` altogether; an empty
    tuple emits it empty. PubMed produces both.
    """
    databank_xml = "".join(
        f"<DataBank><DataBankName>{name}</DataBankName>"
        + (
            ""
            if accessions is None
            else "<AccessionNumberList>"
            + "".join(f"<AccessionNumber>{a}</AccessionNumber>" for a in accessions)
            + "</AccessionNumberList>"
        )
        + "</DataBank>"
        for name, accessions in databanks
    )
```

The rest of the function is unchanged.

- [ ] **Step 2: Write the failing tests**

Add to `TestPubMedSignalParsing`:

```python
    def test_a_deposition_accession_is_collected(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)))
        assert signals.deposition_databanks == ("GENBANK",)

    def test_pubmeds_own_spelling_is_kept(self):
        # The name is rendered to humans in the indicator line.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GenBank", ("MN908947",)),)))
        assert signals.deposition_databanks == ("GenBank",)

    def test_repository_matching_ignores_case(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("figshare", ("10.6084/m9",)),)))
        assert signals.deposition_databanks == ("figshare",)

    def test_one_repository_named_twice_is_one_entry(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("GENBANK", ("A1",)), ("GenBank", ("A2",))))
        )
        assert signals.deposition_databanks == ("GENBANK",)

    def test_repositories_are_kept_in_document_order(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("PDB", ("1ABC",)), ("SRA", ("SRP000001",))))
        )
        assert signals.deposition_databanks == ("PDB", "SRA")

    @pytest.mark.parametrize("accessions", [None, (), ("",), ("   ",)])
    def test_a_repository_without_a_usable_accession_proves_nothing(self, accessions):
        # A repository name with no accession is an assertion with no referent
        # — nothing a reader could go and fetch — so it is not the structured
        # proof of a deposit this signal claims to be.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", accessions),)))
        assert signals.deposition_databanks == ()

    @pytest.mark.parametrize("name", ["OMIM", "RefSeq", "UniProtKB", "PubChem-Compound", "GDB"])
    def test_a_curated_reference_database_is_not_a_deposit(self, name):
        # NLM lists these beside the deposition repositories, but an OMIM
        # number says the paper is about a known condition and a RefSeq
        # accession names a sequence NCBI curated — neither is evidence that
        # these authors shared their data.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=((name, ("X1",)),)))
        assert signals.deposition_databanks == ()

    def test_a_controlled_access_repository_is_collected_too(self):
        # dbGaP is genuine deposition; the merge step is what knows it is
        # controlled-access and worth `on_request` rather than `full_open`.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("dbGaP", ("phs000001",)),)))
        assert signals.deposition_databanks == ("dbGaP",)

    def test_a_registry_and_a_repository_in_one_list_feed_both_branches(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(
                databanks=(
                    ("ClinicalTrials.gov", ("NCT01234567",)),
                    ("GENBANK", ("MN908947",)),
                )
            )
        )
        assert signals.trial_accessions == ("NCT01234567",)
        assert signals.deposition_databanks == ("GENBANK",)

    def test_an_unrecognised_databank_name_is_ignored(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("SomeNewRegistry", ("X1",)),)))
        assert signals.deposition_databanks == ()
        assert signals.registration_not_checkable is False
```

- [ ] **Step 3: Run and watch them fail**

Run: `uv run pytest tests/test_transparency.py::TestPubMedSignalParsing -v`
Expected: FAIL with `AttributeError: '_PubMedSignals' object has no attribute 'deposition_databanks'`.

- [ ] **Step 4: Add the allow-lists**

In `bmlib/transparency/analyzer.py`, after `_CLINICALTRIALS_GOV = "clinicaltrials.gov"`:

```python
# `DataBankName` values naming a repository authors *deposit into*, lowercased.
# Curated from the same NLM vocabulary as the registries above, whose second
# table this splits in half. The other half is deliberately excluded: GDB,
# OMIM, PIR, PubChem-BioAssay, PubChem-Compound, PubChem-Substance, RefSeq,
# SWISSPROT, UniMES, UniParc, UniProtKB and UniRef are curated *reference*
# databases. An OMIM number says the paper is about a known condition; a
# RefSeq accession names a sequence NCBI curated, not one these authors
# produced. Neither is evidence that these authors shared their own data,
# which is what the data-availability component measures — so adding one back
# would award 20 points for a citation.
#
# Zenodo is absent because NLM's vocabulary does not carry it, so PubMed never
# emits it. `_DATA_PATTERNS` already matches "zenodo" in prose.
_DEPOSITION_DATABANK_NAMES = frozenset(
    {
        "bioproject",
        "dbvar",
        "dryad",
        "figshare",
        "genbank",
        "geo",
        "pdb",
        "sra",
    }
)

# Deposition into a controlled-access repository. The deposit is real,
# findable and citable, but a reader needs Data Access Committee approval to
# obtain the data — which is what `on_request` already means, so scoring it
# `full_open` would overstate what a reader can actually get.
_CONTROLLED_DEPOSITION_DATABANK_NAMES = frozenset({"dbgap"})
```

- [ ] **Step 5: Add the field**

In `_PubMedSignals`, add after `funders` (last, so the frozen dataclass's positional order stays stable for anything constructing it that way):

```python
    deposition_databanks: tuple[str, ...] = ()
```

and to its docstring's `Attributes:` block:

```
        deposition_databanks: Repository names from ``<DataBankList>`` that
            carried at least one non-blank accession, in PubMed's own
            spelling and document order, deduplicated case-insensitively.
            Names rather than a level: this class reports what the record
            said, and :func:`_merge_pubmed_signals` decides what it is worth
            — the same division `funders` already follows.
```

- [ ] **Step 6: Rewrite the `<DataBank>` loop**

Replace lines 369-396 of `bmlib/transparency/analyzer.py` with:

```python
    accessions: list[str] = []
    registration_not_checkable = False
    # Keyed by the lowercased name so a record naming one repository twice —
    # or once as "GENBANK" and once as "GenBank" — yields one entry. The value
    # is the first spelling seen, because it is rendered to humans.
    deposition: dict[str, str] = {}
    for databank in citation.findall("Article/DataBankList/DataBank"):
        raw_name = (databank.findtext("DataBankName") or "").strip()
        name = raw_name.lower()

        if name in _DEPOSITION_DATABANK_NAMES or name in _CONTROLLED_DEPOSITION_DATABANK_NAMES:
            # A repository name with no accession is an assertion with no
            # referent — nothing a reader could go and fetch — so it is not
            # the structured proof of a deposit this signal claims to be.
            if any(
                (el.text or "").strip()
                for el in databank.findall("AccessionNumberList/AccessionNumber")
            ):
                deposition.setdefault(name, raw_name)
            continue

        if name not in _TRIAL_REGISTRY_NAMES:
            continue
        # Every accession is publisher-supplied text that would be interpolated
        # into a ClinicalTrials.gov URL path, so only a well-formed NCT id is
        # ever carried forward. A ClinicalTrials.gov entry whose accession is
        # missing or malformed still establishes registration — it just cannot
        # be followed up, which is what `registration_not_checkable` records.
        usable = [
            acc
            for acc in (
                (el.text or "").strip().upper()
                for el in databank.findall("AccessionNumberList/AccessionNumber")
            )
            if _NCT_ID_RE.fullmatch(acc)
        ]
        if name == _CLINICALTRIALS_GOV and usable:
            accessions.extend(usable)
        else:
            if name == _CLINICALTRIALS_GOV:
                # Not the same story as a registration in another registry, and
                # the only place the difference is visible — the result records
                # followability, not which of the two caused it.
                logger.debug("ClinicalTrials.gov databank carried no usable accession")
            registration_not_checkable = True
```

Then add the field to the constructor call at the end of the function:

```python
    return _PubMedSignals(
        coi_statement=coi_statement,
        trial_accessions=tuple(accessions),
        registration_not_checkable=registration_not_checkable,
        funders=funders,
        deposition_databanks=tuple(deposition.values()),
    )
```

Note the deposition branch is tested **before** the registry branch: no name is in both sets, so the order is arbitrary for correctness, but putting it first keeps the registry block below exactly as it was.

- [ ] **Step 7: Run and watch them pass**

Run: `uv run pytest tests/test_transparency.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "feat(transparency): collect data-deposition repositories from PubMed

_parse_pubmed_signals() stepped over every <DataBank> that was not a trial
registry. The deposition half of NLM's vocabulary — GENBANK, GEO, SRA, PDB,
BioProject, dbVar, Dryad, figshare, and controlled-access dbGaP — is now
collected, requiring at least one non-blank accession. The curated reference
databases beside them (OMIM, RefSeq, UniProtKB, PubChem-*, GDB, PIR) are
recognised and deliberately score nothing: citing a curated record is not
evidence these authors shared their data."
```

---

### Task 5: Fold deposition into the analysis

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — add `_INDICATOR_DATA_DEPOSITED_PREFIX` (~line 211), extend `_merge_pubmed_signals` (~line 542)
- Test: `tests/test_transparency.py` — add a class after `TestPubMedSignalMerge` (~line 1554)

**Interfaces:**
- Consumes: everything from Tasks 2, 3 and 4.
- Produces: `_INDICATOR_DATA_DEPOSITED_PREFIX: str`. Nothing later depends on it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transparency.py`, after the existing `TestPubMedSignalMerge` class:

```python
class TestDataDepositionMerge:
    """PubMed's deposition accessions are the second producer of `data_level`."""

    def test_a_deposition_accession_establishes_full_open(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("GENBANK",)), analysis)
        assert analysis.data_level == "full_open"

    def test_a_controlled_access_deposit_is_only_on_request(self):
        # dbGaP data needs Data Access Committee approval, which is what
        # `on_request` already means.
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP",)), analysis)
        assert analysis.data_level == "on_request"

    def test_the_strongest_of_several_deposits_wins(self):
        analysis = _Analysis()
        _merge_pubmed_signals(
            _PubMedSignals(deposition_databanks=("dbGaP", "GENBANK")), analysis
        )
        assert analysis.data_level == "full_open"

    def test_an_accession_outranks_a_full_text_denial(self):
        # The consequential case. A clinical paper's "data are not available"
        # is routinely about individual patient records, while the accession
        # is a sequence on a public server right now. Hard evidence of a real
        # deposit beats a substring match whose subject we cannot determine —
        # and the denial indicator is never written, so nothing contradicts.
        analysis = _Analysis()
        analysis.note_data_level("not_available")
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("GENBANK",)), analysis)
        _score_data_availability(analysis)
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN
        assert _INDICATOR_DATA_NOT_AVAILABLE not in analysis.indicators

    def test_a_deposit_never_lowers_a_stronger_established_level(self):
        analysis = _Analysis()
        analysis.note_data_level("full_open")
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP",)), analysis)
        _score_data_availability(analysis)
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN  # 20, not 20 + 10

    def test_the_repositories_are_named_in_an_indicator(self):
        analysis = _Analysis()
        _merge_pubmed_signals(
            _PubMedSignals(deposition_databanks=("GENBANK", "PDB")), analysis
        )
        assert _INDICATOR_DATA_DEPOSITED_PREFIX + "GENBANK, PDB" in analysis.indicators

    def test_the_indicator_is_written_even_when_the_level_it_nominated_lost(self):
        # The line reports what PubMed said, which stays true regardless of
        # which level won. A sub-step publishes its own finding; it does not
        # read the merged field back to decide whether to mention it.
        analysis = _Analysis()
        analysis.note_data_level("full_open")
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP",)), analysis)
        assert _INDICATOR_DATA_DEPOSITED_PREFIX + "dbGaP" in analysis.indicators

    def test_no_deposits_means_no_indicator_and_no_level(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(), analysis)
        assert analysis.data_level == "unknown"
        assert analysis.indicators == []

    def test_analyze_credits_a_deposition_accession_end_to_end(self, monkeypatch):
        client = _RecordingClient(
            epmc=_epmc_payload(abstract="A study of a virus.", pmid="12345678"),
            pubmed=_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="12345678")
        assert result.data_availability_level == "full_open"
        assert result.transparency_score == SCORE_DATA_FULL_OPEN
        assert _INDICATOR_DATA_DEPOSITED_PREFIX + "GENBANK" in result.risk_indicators
```

Add `SCORE_DATA_FULL_OPEN` and `_INDICATOR_DATA_DEPOSITED_PREFIX` to the test module's import block if Task 3 did not already add the former.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_transparency.py::TestDataDepositionMerge -v`
Expected: collection error — `ImportError: cannot import name '_INDICATOR_DATA_DEPOSITED_PREFIX'`.

- [ ] **Step 3: Add the indicator prefix**

Beside `_INDICATOR_DATA_NOT_AVAILABLE` in `bmlib/transparency/analyzer.py`:

```python
# A prefix, completed with the repository names. `data_availability_level`
# alone cannot distinguish a hard accession from the word "github" appearing
# somewhere in the full text, and `risk_indicators` is the only channel the
# result has for that provenance — the same job `Industry funder: X` does.
_INDICATOR_DATA_DEPOSITED_PREFIX = "Data deposited: "
```

- [ ] **Step 4: Extend `_merge_pubmed_signals`**

Add at the end of the function, after the `if pubmed.funders:` block:

```python
    if pubmed.deposition_databanks:
        for name in pubmed.deposition_databanks:
            # The parser collected the name; deciding what a deposit into it
            # is worth is this step's job, which is why the signals carry
            # names rather than a level.
            analysis.note_data_level(
                "on_request"
                if name.lower() in _CONTROLLED_DEPOSITION_DATABANK_NAMES
                else "full_open"
            )
        # Written whether or not the level above won: it reports what PubMed
        # said, which stays true either way.
        analysis.indicators.append(
            _INDICATOR_DATA_DEPOSITED_PREFIX + ", ".join(pubmed.deposition_databanks)
        )
```

Extend the function's docstring with a sentence naming the third signal it now folds in:

```
    ``<DataBankList>`` deposition accessions nominate a data-availability
    level — ``on_request`` for a controlled-access repository, ``full_open``
    otherwise — through :meth:`_Analysis.note_data_level`, so the strongest
    evidence wins whichever source ran first. The component itself is scored
    later, by :func:`_score_data_availability`.
```

- [ ] **Step 5: Run and watch them pass**

Run: `uv run pytest tests/test_transparency.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "feat(transparency): score data deposition from PubMed accessions

A GENBANK/GEO/SRA/PDB/BioProject/dbVar/Dryad/figshare accession nominates
full_open; dbGaP nominates on_request, since its data needs Data Access
Committee approval. The strongest level wins whichever source found it, so a
live public accession outranks a full-text 'data are not available' — which
in a clinical paper is routinely about patient records, not the sequence.

'Data deposited: GENBANK, PDB' records the provenance the level cannot."
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/manual/transparency.md` (the `Article/DataBankList` row ~line 71, the out-of-scope paragraph ~lines 74-77, the data-pattern table ~lines 461-468, the scoring table ~lines 348-349)
- Modify: `CHANGELOG.md` (`[Unreleased]`), `ROADMAP.md`, `HANDOVER.md`

**Interfaces:**
- Consumes: the finished behaviour from Tasks 1-5.
- Produces: nothing.

- [ ] **Step 1: Update the reference manual**

Read the four regions above. The page currently states the analyzer reads `DataBankList` for registries only and carries an explicit paragraph saying data deposition is "deliberately out of scope" — that paragraph is now false and must be replaced by a description of what shipped, including the two allow-lists, the non-blank-accession requirement, the `dbGaP → on_request` mapping and the rank-based merge. Add `_DATA_LEVEL_RANK` to the data-availability section, and note that the component is awarded once at the end of `analyze()` rather than by the step that finds the level.

- [ ] **Step 2: Write the CHANGELOG entries**

Under `## [Unreleased]`, add an `### Added` section for the feature and put the registry fix under `### Fixed`. Both move stored values, so both say so explicitly — follow the tone of the `industry_funding_detected` entry in `## [0.6.0]`. Cover all four behaviour changes from the design's "Behaviour changes" section, including the `"Data explicitly not available"` indicator moving to the end of `risk_indicators`.

- [ ] **Step 3: Update ROADMAP.md**

Change the `⬜ Planned | Data deposition from `<DataBankList>`` row to `✅ Done` and rewrite its Details cell to describe what shipped — the deposition/reference split, dbGaP, the rank merge, the once-only award — ending with `(unreleased)`.

- [ ] **Step 4: Update HANDOVER.md**

- Remove the "Data-deposition accessions in PubMed's `<DataBankList>`" entry from "Worth doing, not yet an issue" — it is done.
- Update "Unreleased since 0.6.0" to list this change alongside the `_Analysis` carrier, with its behaviour changes.
- Update the test count to whatever `uv run pytest tests/ -q` reports.
- Amend the "deliberate non-fix" entry that ends *"The queued `<DataBankList>` data-deposition signal is that second producer for `data_level`, so it has to bring a merge rule…"* — the signal has landed and brought one. Say what the rule is (`note_data_level`, rank-based) and that `test_a_level_this_step_did_not_find_is_not_scored` was replaced by `test_a_step_that_found_nothing_does_not_lower_an_established_level`, with the reason the old assertion inverted.
- Add a non-fix entry for the deposition/reference split, so nobody "completes" the allow-list by adding OMIM and RefSeq.
- Note the branch/PR state under "Current state".

- [ ] **Step 5: Verify everything, then commit**

```bash
uv run pytest tests/ -v
uvx ruff@0.15.20 check .
uvx ruff@0.15.20 format --check .
```

Expected: all tests PASS (32 skipped as before), both ruff commands clean.

```bash
git add -A
git commit -m "docs: record data deposition from <DataBankList>"
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin feature/databank-data-deposition
gh pr create --base main \
  --title "feat(transparency): data deposition from PubMed's <DataBankList>" \
  --body-file /path/to/body.md
```

Write the body first, covering, in this order: what the feature reads and why PubMed's structured field beats the seven-substring prose scan; the deposition-vs-reference split with the reason OMIM and RefSeq score nothing; `dbGaP → on_request`; the rank-based merge and the once-only award; the three trial registries fixed in passing; and a **"Behaviour changes"** heading listing all four stored-value movements from the design doc, since a reviewer holding historical assessments needs them called out rather than buried. Link the design and plan docs. There is no GitHub issue to link — this was carried in HANDOVER's "worth doing" list rather than filed.

---

## Self-review notes

**Spec coverage.** Merge rule → Task 2. Deposition-only allow-list with the reference names excluded by comment → Task 4. dbGaP → `on_request` → Tasks 4 and 5. Non-blank accession required → Task 4. Score once at the end → Task 3. Indicator provenance → Task 5. Registry gaps → Task 1. Behaviour changes and docs → Task 6. Every test named in the spec's Testing section appears in Tasks 1, 2, 4 or 5.

**Known collateral.** `TestDataAvailabilityPatterns` asserts scores that `_check_europepmc` stops awarding, and `test_a_level_this_step_did_not_find_is_not_scored` pins an invariant this change deliberately inverts. Task 3 Step 1 rewrites the whole class rather than leaving it to be discovered at Step 6.
