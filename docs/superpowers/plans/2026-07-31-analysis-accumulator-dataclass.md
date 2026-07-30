# `_Analysis` Carrier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4-to-6-element accumulator tuples threaded through
`TransparencyAnalyzer.analyze()` with one mutable `_Analysis` dataclass that
every sub-step mutates in place.

**Architecture:** A module-private mutable dataclass holds the ten values
`analyze()` accumulates. Three named methods on it — `award_funder_info()`,
`note_industry_funder()`, `note_industry_coi()` — replace the positional
booleans and duplicated fold logic. Five sub-steps change from returning
tuples to returning `None`. The migration is incremental: `analyze()` carries
the carrier *and* the not-yet-migrated locals between tasks, so every commit is
green.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`, pytest, ruff.

**Spec:** [`docs/superpowers/specs/2026-07-31-analysis-accumulator-dataclass-design.md`](../specs/2026-07-31-analysis-accumulator-dataclass-design.md)

## Global Constraints

- Every source file keeps its AGPL-3 header. No file here is new, so no header
  needs writing.
- Type hints on every signature; docstrings on everything public. `_Analysis`
  is private but still gets a class docstring and per-method docstrings — the
  module's `_PubMedSignals` is the local precedent.
- `from __future__ import annotations` is already at the top of both files.
- Run tests with `uv run pytest`, never bare `pytest`. `uv` only, never `pip`.
- Lint with the **CI-pinned** ruff, not the one in `.venv`:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
  (`.venv` holds 0.6.5, which false-flags `UP038` on an unrelated file.)
- ruff config: line-length 100, target py311, rules E, F, I, N, W, UP.
- Baseline is **1033 passed, 32 skipped**. The count rises only where this plan
  adds a test; it must never fall.
- Work happens on branch `refactor/analysis-accumulator-dataclass`, already
  created, already carrying the design-doc commit.
- Only two files change: `bmlib/transparency/analyzer.py` and
  `tests/test_transparency.py` (plus `CHANGELOG.md`, `HANDOVER.md`,
  `ROADMAP.md` in the last task).

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `bmlib/transparency/analyzer.py` | The analyzer and its sub-steps | Add `_Analysis` + `_INDICATOR_INDUSTRY_COI`; convert five sub-steps to mutators; rewrite `analyze()`'s accumulation block |
| `tests/test_transparency.py` | Behaviour spec for the above | Migrate 27 white-box call sites; add 3 tests |
| `CHANGELOG.md` | Release notes | One `[Unreleased]` entry (Task 5) |
| `HANDOVER.md`, `ROADMAP.md` | Session state | Updated in Task 5 |

`analyzer.py` is 1225 lines and does warrant splitting eventually. That is
explicitly out of scope here — see the spec.

---

### Task 1: The `_Analysis` carrier and its three operations

Adds the type and its semantics. Nothing is migrated onto it yet, so the suite
must stay at 1033 passed plus whatever this task adds.

**Files:**
- Modify: `bmlib/transparency/analyzer.py` (indicator constants block ~line 199; new dataclass after `_PubMedSignals`, which ends at line 415)
- Test: `tests/test_transparency.py`

**Interfaces:**
- Consumes: `SCORE_FUNDER_INFO`, `DEFAULT_INDUSTRY_CONFIDENCE`,
  `TEXT_INDUSTRY_CONFIDENCE` — all module constants already in `analyzer.py`.
- Produces: `_Analysis` with fields `score: int`, `indicators: list[str]`,
  `industry_funding: bool`, `industry_confidence: float`, `data_level: str`,
  `coi_disclosed: bool | None`, `trial_registered: bool`,
  `results_compliant: bool`, `full_text_analyzed: bool`,
  `funder_info_scored: bool`; methods `award_funder_info() -> None`,
  `note_industry_funder(name: str) -> None`, `note_industry_coi() -> None`;
  and the module constant `_INDICATOR_INDUSTRY_COI`.

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/test_transparency.py`, immediately before
`class TestCheckEuropePMC:` (currently around line 166):

```python
class TestAnalysisCarrier:
    """The accumulator carrier's own semantics, before anything uses it."""

    def test_defaults_match_a_fresh_analysis(self):
        analysis = _Analysis()
        assert analysis.score == 0
        assert analysis.indicators == []
        assert analysis.industry_funding is False
        assert analysis.industry_confidence == 0.0
        assert analysis.data_level == "unknown"
        assert analysis.coi_disclosed is None
        assert analysis.trial_registered is False
        assert analysis.results_compliant is False
        assert analysis.full_text_analyzed is False
        assert analysis.funder_info_scored is False

    def test_each_carrier_gets_its_own_indicator_list(self):
        # A mutable default shared across instances would leak one analysis's
        # findings into the next.
        first, second = _Analysis(), _Analysis()
        first.indicators.append("x")
        assert second.indicators == []

    def test_funder_info_is_awarded_once(self):
        analysis = _Analysis()
        analysis.award_funder_info()
        analysis.award_funder_info()
        assert analysis.score == SCORE_FUNDER_INFO
        assert analysis.funder_info_scored is True

    def test_funder_info_is_not_awarded_when_already_spent(self):
        # The hazard the method exists for: whichever source runs first spends
        # the component, and the second must not spend it again.
        analysis = _Analysis(funder_info_scored=True)
        analysis.award_funder_info()
        assert analysis.score == 0

    def test_an_industry_funder_is_recorded_with_structured_confidence(self):
        analysis = _Analysis()
        analysis.note_industry_funder("Genentech Inc.")
        assert analysis.industry_funding is True
        assert analysis.industry_confidence == DEFAULT_INDUSTRY_CONFIDENCE
        assert analysis.indicators == ["Industry funder: Genentech Inc."]

    def test_one_funder_is_one_indicator_however_often_it_is_reported(self):
        analysis = _Analysis()
        analysis.note_industry_funder("Genentech Inc.")
        analysis.note_industry_funder("Genentech Inc.")
        assert analysis.indicators == ["Industry funder: Genentech Inc."]

    def test_a_funder_never_lowers_an_established_confidence(self):
        analysis = _Analysis(industry_confidence=0.95)
        analysis.note_industry_funder("Genentech Inc.")
        assert analysis.industry_confidence == 0.95

    def test_an_industry_coi_is_weaker_evidence_than_a_funder_record(self):
        analysis = _Analysis()
        analysis.note_industry_coi()
        assert analysis.industry_funding is True
        assert analysis.industry_confidence == TEXT_INDUSTRY_CONFIDENCE
        assert analysis.indicators == [_INDICATOR_INDUSTRY_COI]

    def test_a_coi_signal_never_lowers_a_funder_record_s_confidence(self):
        # Arrival order must not decide the confidence: a structured funder
        # record outranks COI prose whichever is seen first.
        analysis = _Analysis()
        analysis.note_industry_funder("Genentech Inc.")
        analysis.note_industry_coi()
        assert analysis.industry_confidence == DEFAULT_INDUSTRY_CONFIDENCE
```

Extend the existing import block at the top of the file (currently lines 23-36)
so it reads:

```python
from bmlib.transparency.analyzer import (
    _INDICATOR_COI_IN_PUBMED,
    _INDICATOR_COI_UNKNOWN,
    _INDICATOR_INDUSTRY_COI,
    _INDICATOR_NO_COI_IN_FULLTEXT,
    _INDICATOR_NO_POSTED_RESULTS,
    _INDICATOR_RESULTS_NOT_CHECKABLE,
    DEFAULT_INDUSTRY_CONFIDENCE,
    SCORE_COI_DISCLOSED,
    SCORE_FUNDER_INFO,
    TEXT_INDUSTRY_CONFIDENCE,
    TransparencyAnalyzer,
    _Analysis,
    _merge_pubmed_signals,
    _parse_pubmed_signals,
    _PubMedSignals,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transparency.py -x -q`

Expected: collection error — `ImportError: cannot import name '_Analysis'`.
That is the correct red for a type that does not exist yet.

- [ ] **Step 3: Add the indicator constant**

In `bmlib/transparency/analyzer.py`, in the `# ---- Indicator strings ----`
block, after `_INDICATOR_COI_IN_PUBMED`:

```python
_INDICATOR_INDUSTRY_COI = "Industry ties disclosed in COI statement"
```

- [ ] **Step 4: Import `field`**

Change the existing import line:

```python
from dataclasses import dataclass
```

to:

```python
from dataclasses import dataclass, field
```

- [ ] **Step 5: Add the carrier**

Insert directly after `_parse_pubmed_signals()` ends (line 415) and before
`def _merge_pubmed_signals(`:

```python
@dataclass
class _Analysis:
    """Everything :meth:`TransparencyAnalyzer.analyze` accumulates.

    Passed to each sub-step and mutated in place. The alternative — passing
    each value in and unpacking a tuple back out — bound a value to its name by
    position alone, so a mis-ordered unpacking was a silent, type-compatible
    swap (``industry_funding`` and ``funder_info_scored`` are both ``bool``;
    ``score`` is interchangeable with any other ``int``) and adding one signal
    meant widening several signatures.

    Mutable by design, and private: it never leaves this module, which is why
    it carries no ``to_dict()``/``from_dict()`` — the same reasoning as the
    frozen :class:`_PubMedSignals` beside it, which is a message from one
    source rather than shared state.

    Attributes:
        score: Running transparency score, uncapped until ``analyze()`` ends.
        indicators: Human-readable findings, in the order they were made.
        industry_funding: Any industry involvement was detected.
        industry_confidence: Confidence in that detection; the strongest
            evidence seen wins, regardless of arrival order.
        data_level: Data-availability level from :data:`_DATA_PATTERNS`.
        coi_disclosed: Tri-state — ``True`` (statement found), ``False`` (full
            text scanned, none found), ``None`` (undeterminable).
        trial_registered: A trial registration was established.
        results_compliant: Posted results were found for a registered trial.
        full_text_analyzed: Findings came from full text, not just an abstract.
        funder_info_scored: :data:`SCORE_FUNDER_INFO` has been spent. Named
            state rather than a positional bool, so a third funder source
            cannot award it again by forgetting a convention.
    """

    score: int = 0
    indicators: list[str] = field(default_factory=list)
    industry_funding: bool = False
    industry_confidence: float = 0.0
    data_level: str = "unknown"
    coi_disclosed: bool | None = None
    trial_registered: bool = False
    results_compliant: bool = False
    full_text_analyzed: bool = False
    funder_info_scored: bool = False

    def award_funder_info(self) -> None:
        """Award :data:`SCORE_FUNDER_INFO` the first time any source reports funders.

        Two sources can report them — CrossRef funder records and PubMed's
        ``<GrantList>`` — and the component is worth 15 points once, not twice.
        Neither caller has to know whether the other ran first, which is what
        makes a third source safe to add.
        """
        if not self.funder_info_scored:
            self.score += SCORE_FUNDER_INFO
            self.funder_info_scored = True

    def note_industry_funder(self, name: str) -> None:
        """Record *name* as an industry funder named in structured metadata.

        The confidence is fixed at :data:`DEFAULT_INDUSTRY_CONFIDENCE` rather
        than passed in: "structured metadata" — a CrossRef funder record or a
        PubMed ``<Grant><Agency>`` — is exactly what distinguishes this from
        the weaker prose signal in :meth:`note_industry_coi`, and a caller free
        to choose the number could blur the two.

        The indicator is deduplicated. One funder is one finding however many
        sources report it, and however often a single source repeats it: both
        registries emit one record per award, so an organisation funding four
        awards on one paper appears four times upstream.
        """
        self.industry_funding = True
        self.industry_confidence = max(self.industry_confidence, DEFAULT_INDUSTRY_CONFIDENCE)
        line = f"Industry funder: {name}"
        if line not in self.indicators:
            self.indicators.append(line)

    def note_industry_coi(self) -> None:
        """Record industry ties disclosed in a full-text COI statement.

        Weaker evidence than a funder record — an inference from prose rather
        than a structured field — so it raises the confidence only to
        :data:`TEXT_INDUSTRY_CONFIDENCE` and never lowers a stronger one.
        """
        self.industry_funding = True
        self.industry_confidence = max(self.industry_confidence, TEXT_INDUSTRY_CONFIDENCE)
        self.indicators.append(_INDICATOR_INDUSTRY_COI)
```

- [ ] **Step 6: Use the new constant in `analyze()`**

In `analyze()`, replace the literal (currently line 744):

```python
                    indicators.append("Industry ties disclosed in COI statement")
```

with:

```python
                    indicators.append(_INDICATOR_INDUSTRY_COI)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transparency.py -q`
Expected: PASS, 9 more tests than before, 0 failures.

Then the whole suite: `uv run pytest tests/ -q`
Expected: `1042 passed, 32 skipped`.

- [ ] **Step 8: Lint**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
Expected: `All checks passed!` and `N files already formatted`.

- [ ] **Step 9: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "refactor(transparency): add the _Analysis accumulator carrier

Adds the dataclass and its three named operations without migrating any
sub-step onto it yet. award_funder_info() turns 'spend this component at
most once' from a convention two call sites must both remember into a
mechanism; note_industry_funder() and note_industry_coi() carry the
confidence that belongs to each class of evidence.

Refs #37"
```

---

### Task 2: Migrate `_check_crossref` and `_check_openalex`

The two simplest steps, plus `analyze()` gaining the carrier. This is where the
one deliberate behaviour change lands: CrossRef's own duplicate funder names
are now deduped, because `note_industry_funder()` dedupes for every source.

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — `analyze()` (lines ~691-824), `_check_crossref` (lines ~828-864), `_check_openalex` (lines ~1001-1015)
- Modify: `tests/test_transparency.py` — the 2 `_check_crossref` call sites in `TestFunderInfoIsScoredOnce`

**Interfaces:**
- Consumes: `_Analysis` and its three methods from Task 1.
- Produces: `_check_crossref(self, client: Any, doi: str, analysis: _Analysis) -> None`
  and `_check_openalex(self, client: Any, doi: str, analysis: _Analysis) -> None`.

- [ ] **Step 1: Write the failing test for the behaviour change**

Add to `tests/test_transparency.py`, inside the existing
`class TestFunderInfoIsScoredOnce:` (after its last test):

```python
    def test_a_repeated_crossref_funder_is_one_indicator(self):
        # CrossRef lists one record per award, so an organisation funding two
        # awards on one paper appears twice. The indicator list is a set of
        # findings: one funder is one finding.
        client = _RecordingClient(
            crossref={
                "message": {
                    "funder": [{"name": "Genentech Inc."}, {"name": "Genentech Inc."}]
                }
            }
        )
        analysis = _Analysis()
        TransparencyAnalyzer()._check_crossref(client, "10.1234/x", analysis)
        assert analysis.indicators == ["Industry funder: Genentech Inc."]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_transparency.py::TestFunderInfoIsScoredOnce -q`

Expected: FAIL with `TypeError: _check_crossref() missing 4 required positional
arguments` — the method still takes the accumulators one by one.

- [ ] **Step 3: Convert `_check_crossref`**

Replace the whole method with:

```python
    def _check_crossref(self, client: Any, doi: str, analysis: _Analysis) -> None:
        """Query CrossRef for funder information and fold it into *analysis*.

        ``SCORE_FUNDER_INFO`` is spent through
        :meth:`_Analysis.award_funder_info`, so it stays a once-per-analysis
        component however many funder sources run and in whatever order — this
        step is merely the first one today.
        """
        cr = self._query_crossref(client, doi)
        if cr:
            funders = cr.get("message", {}).get("funder", [])
            if funders:
                analysis.award_funder_info()
                for funder in funders:
                    name = funder.get("name") or ""
                    if _is_industry_funder(name):
                        analysis.note_industry_funder(name)
            else:
                analysis.indicators.append("No funder information in CrossRef")
```

- [ ] **Step 4: Convert `_check_openalex`**

Replace the whole method with:

```python
    def _check_openalex(self, client: Any, doi: str, analysis: _Analysis) -> None:
        """Fold open-access status and citation count from OpenAlex into *analysis*."""
        oa = self._query_openalex(client, doi)
        if oa:
            oa_info = oa.get("open_access", {})
            if oa_info.get("is_oa"):
                analysis.score += SCORE_OPEN_ACCESS
            if oa.get("cited_by_count", 0) > 0:
                analysis.score += SCORE_CITED
```

- [ ] **Step 5: Rewrite `analyze()`'s accumulation block**

Replace everything from `self._api_reachable = False` down to the end of the
`with httpx.Client(...)` block with the following. The three not-yet-migrated
steps keep their tuples but read and write the carrier's fields; the locals
that survive are only the ones those steps still own.

```python
        self._api_reachable = False
        analysis = _Analysis()
        # Locals for the sub-steps not yet migrated onto the carrier. They go
        # away as each is converted.
        coi_disclosed: bool | None = None
        data_level = "unknown"
        trial_registered = False
        results_compliant = False
        full_text_analyzed = False

        with httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/{__version__} (mailto:{self.email})"},
        ) as client:
            # --- CrossRef (funder info) ---
            if doi:
                self._check_crossref(client, doi, analysis)

            # --- EuropePMC (full text / abstract, COI, data availability) ---
            epmc = self._fetch_europepmc(client, pmid, doi)
            if epmc:
                (
                    coi_disclosed,
                    data_level,
                    analysis.score,
                    analysis.indicators,
                    full_text_analyzed,
                    industry_coi,
                ) = self._check_europepmc(client, epmc, analysis.score, analysis.indicators)
                if industry_coi:
                    analysis.note_industry_coi()

            # --- PubMed (structured COI, trial registration, grants) ---
            # Placed after Europe PMC so a DOI-only analysis can reuse the PMID
            # from the record already fetched, and before ClinicalTrials.gov so
            # a structured accession can feed the posted-results check.
            pubmed = self._check_pubmed(client, pmid or _pmid_from_epmc(epmc))
            (
                coi_disclosed,
                analysis.score,
                analysis.indicators,
                analysis.industry_funding,
                analysis.industry_confidence,
                analysis.funder_info_scored,
            ) = _merge_pubmed_signals(
                pubmed,
                coi_disclosed,
                analysis.score,
                analysis.indicators,
                analysis.industry_funding,
                analysis.industry_confidence,
                analysis.funder_info_scored,
            )

            # --- OpenAlex (additional metadata) ---
            if doi:
                self._check_openalex(client, doi, analysis)

            # --- ClinicalTrials.gov (trial registration) ---
            if doi or pmid:
                trial_registered, results_compliant, analysis.score, analysis.indicators = (
                    self._check_trial_registration(
                        client,
                        pmid,
                        doi,
                        analysis.score,
                        analysis.indicators,
                        epmc=epmc,
                        pubmed=pubmed,
                    )
                )
```

Then, below the unreachable-API guard, replace the score cap, the risk-level
call and the result construction so they read the carrier where it now owns the
value:

```python
        analysis.score = min(analysis.score, MAX_TRANSPARENCY_SCORE)

        risk_level = calculate_risk_level(
            score=analysis.score,
            industry_funding=analysis.industry_funding,
            data_availability=data_level,
            coi_disclosed=coi_disclosed,
            settings=self.settings,
        )

        return TransparencyResult(
            document_id=document_id,
            transparency_score=analysis.score,
            risk_level=risk_level,
            industry_funding_detected=analysis.industry_funding,
            industry_funding_confidence=analysis.industry_confidence,
            data_availability_level=data_level,
            coi_disclosed=coi_disclosed,
            trial_registered=trial_registered,
            trial_results_compliant=results_compliant,
            risk_indicators=analysis.indicators,
            full_text_analyzed=full_text_analyzed,
            tier_downgrade_applied=(
                self.settings.tier_downgrade_amount if risk_level == TransparencyRisk.HIGH else 0
            ),
        )
```

The long comment above the old `funder_info_scored = False` initialisation
moves onto `_Analysis.funder_info_scored`'s attribute docstring in Task 1 — do
not carry it here as well.

- [ ] **Step 6: Migrate the two `_check_crossref` test call sites**

In `class TestFunderInfoIsScoredOnce`, replace both existing tests with:

```python
    def test_crossref_respects_an_already_spent_component(self):
        client = _RecordingClient(crossref={"message": {"funder": [{"name": "Some Trust"}]}})
        analysis = _Analysis(funder_info_scored=True)
        TransparencyAnalyzer()._check_crossref(client, "10.1234/x", analysis)
        assert analysis.score == 0
        assert analysis.funder_info_scored is True

    def test_crossref_spends_it_when_nothing_has(self):
        client = _RecordingClient(crossref={"message": {"funder": [{"name": "Some Trust"}]}})
        analysis = _Analysis()
        TransparencyAnalyzer()._check_crossref(client, "10.1234/x", analysis)
        assert analysis.score == SCORE_FUNDER_INFO
        assert analysis.funder_info_scored is True
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/ -q`
Expected: `1043 passed, 32 skipped`.

If `test_one_industry_funder_is_one_indicator` fails, the dedup is not reaching
`_check_crossref` — check that `note_industry_funder` is being called rather
than `indicators.append`.

- [ ] **Step 8: Lint**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

- [ ] **Step 9: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "refactor(transparency): put CrossRef and OpenAlex on the carrier

Both sub-steps now mutate _Analysis and return None; analyze() carries the
carrier alongside the locals the three unmigrated steps still own.

Carries one behaviour change: note_industry_funder() deduplicates for
every source, so a CrossRef record naming one organisation once per award
now yields a single 'Industry funder: X' indicator instead of one per
award. This is the rule PubMed's grant list already followed. No score,
no detection flag and no risk level moves — only the indicator list.

Refs #37"
```

---

### Task 3: Migrate `_check_europepmc`

The largest test migration — 22 call sites — and the only step that folds a
finding it used to hand back.

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — `_check_europepmc` (lines ~879-965), `analyze()`'s Europe PMC hunk
- Modify: `tests/test_transparency.py` — all 22 `analyzer._check_europepmc(` call sites

**Interfaces:**
- Consumes: `_Analysis` (Task 1).
- Produces: `_check_europepmc(self, client: Any, epmc: dict, analysis: _Analysis) -> None`.

- [ ] **Step 1: Convert the method**

Replace the signature, docstring and body's local accumulators. The scanning
logic between them is unchanged — only what it writes to changes:

```python
    def _check_europepmc(self, client: Any, epmc: dict, analysis: _Analysis) -> None:
        """Fold COI and data-availability signals from EuropePMC into *analysis*.

        COI and data-availability statements live in a paper's full text, not
        its abstract.  We therefore fetch the full text from EuropePMC when it
        is available (open-access articles) and scan that; we fall back to the
        abstract only when full text cannot be retrieved.

        Sets ``coi_disclosed`` tri-state: ``True`` (statement found), ``False``
        (full text scanned, none found), or ``None`` — left as it was —
        (undeterminable: full text unavailable and no abstract signal).

        Industry ties disclosed in the COI statement itself (consultancies,
        speaker fees, …) are recorded through
        :meth:`_Analysis.note_industry_coi`, which is why this step needs no
        return value: it is only ever reached when full text was analyzed, and
        the confidence that belongs to a prose signal is the method's business
        rather than the caller's.
        """
        result_list = epmc.get("resultList", {}).get("result", [])
        if not result_list:
            return

        record = result_list[0]
        abstract_text = (record.get("abstractText") or "").lower()

        # Prefer full text — COI / data-availability statements are not in the
        # abstract. EuropePMC serves full text for open-access records.
        search_text = abstract_text
        if record.get("inEPMC") == "Y":
            full_text = self._fetch_europepmc_fulltext(
                client,
                record.get("source"),
                record.get("pmcid") or record.get("id"),
            )
            if full_text:
                search_text = full_text.lower()
                analysis.full_text_analyzed = True

        # COI detection (a COI/disclosure statement counts as "disclosed",
        # including a statement that there is nothing to declare). A non-blank
        # JATS-tagged COI section is structural proof of a disclosure even
        # when its wording contains no cue phrase (issue #13); the cue-phrase
        # scan remains the fallback for untagged text.
        tagged_coi = _extract_tagged_coi_text(search_text)
        if tagged_coi.strip() or any(pat in search_text for pat in _COI_PATTERNS):
            analysis.coi_disclosed = True
            analysis.score += SCORE_COI_DISCLOSED
        elif analysis.full_text_analyzed:
            # Full text inspected and no COI statement found -> explicitly absent.
            analysis.coi_disclosed = False
            analysis.indicators.append(_INDICATOR_NO_COI_IN_FULLTEXT)
        else:
            # Could not inspect full text; status is genuinely unknown.
            analysis.indicators.append(_INDICATOR_COI_UNKNOWN)

        # Data availability
        for pattern, level in _DATA_PATTERNS.items():
            if pattern in search_text:
                analysis.data_level = level
                break
        if analysis.data_level == "full_open":
            analysis.score += SCORE_DATA_FULL_OPEN
        elif analysis.data_level == "on_request":
            analysis.score += SCORE_DATA_ON_REQUEST
        elif analysis.data_level == "not_available":
            analysis.indicators.append("Data explicitly not available")

        # Industry ties disclosed in the COI statement itself. Scanned only in
        # full text — an abstract rarely carries a real disclosure statement —
        # and only within the COI/disclosure region to avoid false positives
        # from references or affiliations. Folded in last so the indicator
        # order is COI, then data availability, then this.
        if analysis.full_text_analyzed and _discloses_industry_ties(
            _extract_coi_text(search_text, tagged=tagged_coi)
        ):
            analysis.note_industry_coi()
```

Note the two deliberate ordering points: `full_text_analyzed` is set on the
carrier *before* the COI branch reads it, and the industry-COI fold moves to
the end of the body — which is where `analyze()` performs it today, so the
indicator order is unchanged.

- [ ] **Step 2: Update `analyze()`'s Europe PMC hunk**

Replace:

```python
            if epmc:
                (
                    coi_disclosed,
                    data_level,
                    analysis.score,
                    analysis.indicators,
                    full_text_analyzed,
                    industry_coi,
                ) = self._check_europepmc(client, epmc, analysis.score, analysis.indicators)
                if industry_coi:
                    analysis.note_industry_coi()
```

with:

```python
            if epmc:
                self._check_europepmc(client, epmc, analysis)
```

Delete the now-unused `data_level` and `full_text_analyzed` locals from the
initialisation block, and change `coi_disclosed`'s remaining use — the PubMed
merge — so the tuple unpacks into `analysis.coi_disclosed`:

```python
            (
                analysis.coi_disclosed,
                analysis.score,
                analysis.indicators,
                analysis.industry_funding,
                analysis.industry_confidence,
                analysis.funder_info_scored,
            ) = _merge_pubmed_signals(
                pubmed,
                analysis.coi_disclosed,
                analysis.score,
                analysis.indicators,
                analysis.industry_funding,
                analysis.industry_confidence,
                analysis.funder_info_scored,
            )
```

Delete the `coi_disclosed` local too. In the risk-level call and the result
construction, replace `data_availability=data_level` with
`data_availability=analysis.data_level`, `coi_disclosed=coi_disclosed` with
`coi_disclosed=analysis.coi_disclosed`, `data_availability_level=data_level`
with `data_availability_level=analysis.data_level`, and
`full_text_analyzed=full_text_analyzed` with
`full_text_analyzed=analysis.full_text_analyzed`. Only `trial_registered` and
`results_compliant` remain as locals.

- [ ] **Step 3: Migrate the 22 test call sites**

Every call of the form:

```python
        coi, _level, score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(...), score=0, indicators=[]
        )
```

becomes:

```python
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(...), analysis)
```

with each assertion rewritten against the field that held that tuple position.
Carry the assertions over unchanged in *meaning* — this is the regression net
for the refactor, so do not adjust an expected value to make a test pass.

| Tuple position | Old local (typical) | New expression |
|---|---|---|
| 0 | `coi` | `analysis.coi_disclosed` |
| 1 | `_level` / `level` | `analysis.data_level` |
| 2 | `score` | `analysis.score` |
| 3 | `_ind` | `analysis.indicators` |
| 4 | `ft` | `analysis.full_text_analyzed` |
| 5 | `industry` / `_ind_coi` | `analysis.industry_funding` |

Position 5 is the one rename that is not mechanical: `industry_coi` was a
returned finding and is now folded in, so `assert industry is True` becomes
`assert analysis.industry_funding is True`. That is equivalent in these tests
because each calls `_check_europepmc` on a fresh `_Analysis()`, where
`industry_funding` starts `False` and nothing else can set it.

Worked example — `test_coi_detected_in_full_text` becomes:

```python
    def test_coi_detected_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True
        assert analysis.full_text_analyzed is True
        assert analysis.score == 10  # SCORE_COI_DISCLOSED
```

and `test_coi_unknown_when_no_full_text` becomes:

```python
    def test_coi_unknown_when_no_full_text(self):
        analyzer = TransparencyAnalyzer()
        # inEPMC == "N" so no full text is fetched, abstract has no COI signal.
        client = _FakeFullTextClient(None)
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(in_epmc="N"), analysis)
        assert analysis.coi_disclosed is None  # undeterminable, not "absent"
        assert analysis.full_text_analyzed is False
```

The `_epmc_record` helper is unchanged — it builds the envelope, which this
task does not touch.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/ -q`
Expected: `1043 passed, 32 skipped` — the same count as Task 2; this task adds
no tests, it migrates them.

- [ ] **Step 5: Lint**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

- [ ] **Step 6: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "refactor(transparency): put the Europe PMC step on the carrier

Its six-element return is gone; industry_coi stops being a finding handed
back for analyze() to fold and is recorded through note_industry_coi() at
the end of the body — the same position, so indicator order is unchanged.

Refs #37"
```

---

### Task 4: Migrate `_merge_pubmed_signals` and `_check_trial_registration`

Finishes the migration: after this task `analyze()` holds no accumulator
locals. `_merge_pubmed_signals`' "mutates nothing it is given" contract inverts
here, so its test does too.

**Files:**
- Modify: `bmlib/transparency/analyzer.py` — `_merge_pubmed_signals` (lines ~418-489), `_check_trial_registration` (lines ~1017-1061), `analyze()`
- Modify: `tests/test_transparency.py` — 2 `_check_trial_registration` call sites, 1 `_merge_pubmed_signals` call site

**Interfaces:**
- Consumes: `_Analysis` (Task 1).
- Produces: `_merge_pubmed_signals(pubmed: _PubMedSignals, analysis: _Analysis) -> None`
  and `_check_trial_registration(self, client: Any, pmid: str | None, doi: str | None,
  analysis: _Analysis, *, epmc: dict | None = None, pubmed: _PubMedSignals | None = None) -> None`.

- [ ] **Step 1: Write the two new tests**

Replace `test_the_merge_does_not_mutate_the_caller_s_indicators` in
`class TestPubMedSignalMerge` with the test of the inverted contract:

```python
    def test_the_merge_applies_both_of_its_branches_to_one_list(self):
        # The COI branch retracts lines while the funder branch appends. When
        # the merge returned a copy, a caller that ignored the return value
        # saw a half-applied merge; mutating the carrier makes that
        # unrepresentable. This pins that both branches land together.
        analysis = _Analysis(indicators=[_INDICATOR_NO_COI_IN_FULLTEXT], coi_disclosed=False)
        _merge_pubmed_signals(
            _PubMedSignals(coi_statement=True, funders=("Genentech Inc.",)),
            analysis,
        )
        assert _INDICATOR_NO_COI_IN_FULLTEXT not in analysis.indicators
        assert _INDICATOR_COI_IN_PUBMED in analysis.indicators
        assert "Industry funder: Genentech Inc." in analysis.indicators
        assert analysis.coi_disclosed is True
```

And add, to `class TestFunderInfoIsScoredOnce`, the composition test that
nothing currently pins:

```python
    def test_two_sources_reporting_funders_spend_the_component_once(self, monkeypatch):
        # CrossRef funder records *and* PubMed grants on the same paper. The
        # sub-step test above pins each in isolation; this pins the composition
        # that analyze() actually runs.
        client = _RecordingClient(
            crossref={"message": {"funder": [{"name": "Some Trust"}]}},
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(agencies=("Another Trust",)),
        )
        TestPubMedRequest._install(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc1", doi="10.1234/x")
        assert result.transparency_score == SCORE_FUNDER_INFO
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_transparency.py::TestPubMedSignalMerge -q`

Expected: FAIL with `TypeError: _merge_pubmed_signals() missing 5 required
positional arguments` for the first. The second passes already if CrossRef's
`Some Trust` is not an industry funder and nothing else scores — run it and
note the actual figure; if it fails, the composition is already double-scoring
and that is a genuine bug to report before continuing.

- [ ] **Step 3: Convert `_merge_pubmed_signals`**

```python
def _merge_pubmed_signals(pubmed: _PubMedSignals, analysis: _Analysis) -> None:
    """Fold PubMed's structured signals into *analysis*.

    A module-level function rather than a method because it needs no HTTP
    client; trial registration is handled separately, in
    :meth:`TransparencyAnalyzer._check_trial_registration`, because that step
    does.

    Each score component is awarded at most once. ``coi_disclosed is not
    True`` is a reliable guard rather than an incidental one: the only
    branch that sets ``True`` is the same branch that adds
    ``SCORE_COI_DISCLOSED``.
    """
    if pubmed.coi_statement and analysis.coi_disclosed is not True:
        analysis.coi_disclosed = True
        analysis.score += SCORE_COI_DISCLOSED
        # Both lines were written before PubMed was consulted and would now
        # contradict the result, so they are retracted rather than left to
        # be reconciled by whoever reads the indicators.
        analysis.indicators = [
            ind
            for ind in analysis.indicators
            if ind not in (_INDICATOR_NO_COI_IN_FULLTEXT, _INDICATOR_COI_UNKNOWN)
        ]
        analysis.indicators.append(_INDICATOR_COI_IN_PUBMED)

    # A missing <CoiStatement> deliberately does not demote `None` to
    # `False`: it means the publisher supplied no statement to PubMed, not
    # that the paper carries none, and `False` would trigger the
    # missing-COI downgrade on no evidence.

    if pubmed.funders:
        analysis.award_funder_info()
        for agency in pubmed.funders:
            if _is_industry_funder(agency):
                # A grant agency is structured metadata, the same class of
                # evidence as a CrossRef funder record — not the weaker
                # signal inferred from COI prose. CrossRef may already have
                # named this funder; note_industry_funder() deduplicates.
                analysis.note_industry_funder(agency)
```

The old paragraph beginning "Mutates nothing it is given" is deleted: with no
return value there is nothing for a caller to ignore, so the half-applied-merge
hazard it guarded against cannot arise.

- [ ] **Step 4: Convert `_check_trial_registration`**

Keep the whole docstring as it stands and change the signature, the local
accumulators and the return:

```python
    def _check_trial_registration(
        self,
        client: Any,
        pmid: str | None,
        doi: str | None,
        analysis: _Analysis,
        *,
        epmc: dict | None = None,
        pubmed: _PubMedSignals | None = None,
    ) -> None:
```

and the body after the docstring:

```python
        pubmed = pubmed or _PubMedSignals()

        ct_ids = list(pubmed.trial_accessions) or self._find_trial_ids(client, pmid, doi, epmc=epmc)
        if ct_ids or pubmed.registration_not_checkable:
            analysis.trial_registered = True
            analysis.score += SCORE_TRIAL_REGISTERED

        if ct_ids:
            for tid in ct_ids[:MAX_TRIAL_IDS_TO_CHECK]:
                if self._check_trial_results(client, tid):
                    analysis.results_compliant = True
                    analysis.score += SCORE_RESULTS_POSTED
                    break
            if not analysis.results_compliant:
                analysis.indicators.append(_INDICATOR_NO_POSTED_RESULTS)
        elif pubmed.registration_not_checkable:
            analysis.indicators.append(_INDICATOR_RESULTS_NOT_CHECKABLE)
```

- [ ] **Step 5: Finish `analyze()`**

The accumulation block becomes, in full:

```python
        self._api_reachable = False
        analysis = _Analysis()

        with httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/{__version__} (mailto:{self.email})"},
        ) as client:
            # --- CrossRef (funder info) ---
            if doi:
                self._check_crossref(client, doi, analysis)

            # --- EuropePMC (full text / abstract, COI, data availability) ---
            epmc = self._fetch_europepmc(client, pmid, doi)
            if epmc:
                self._check_europepmc(client, epmc, analysis)

            # --- PubMed (structured COI, trial registration, grants) ---
            # Placed after Europe PMC so a DOI-only analysis can reuse the PMID
            # from the record already fetched, and before ClinicalTrials.gov so
            # a structured accession can feed the posted-results check.
            pubmed = self._check_pubmed(client, pmid or _pmid_from_epmc(epmc))
            _merge_pubmed_signals(pubmed, analysis)

            # --- OpenAlex (additional metadata) ---
            if doi:
                self._check_openalex(client, doi, analysis)

            # --- ClinicalTrials.gov (trial registration) ---
            if doi or pmid:
                self._check_trial_registration(
                    client, pmid, doi, analysis, epmc=epmc, pubmed=pubmed
                )
```

and the result construction reads `analysis.trial_registered` and
`analysis.results_compliant` in place of the last two locals:

```python
            trial_registered=analysis.trial_registered,
            trial_results_compliant=analysis.results_compliant,
```

- [ ] **Step 6: Migrate the two `_check_trial_registration` test call sites**

Both are in the trial-registration test class. Each becomes, e.g.:

```python
        epmc = _epmc_record("We included three trials (NCT01111111, NCT02222222, NCT03333333).")
        analysis = _Analysis()
        analyzer._check_trial_registration(_Client(), pmid="123", doi=None, analysis=analysis, epmc=epmc)
        assert analysis.trial_registered is False
        assert analysis.results_compliant is False
        assert analysis.score == 0
```

and:

```python
        epmc = _epmc_record("ClinicalTrials.gov number, NCT01206062.")
        analysis = _Analysis()
        analyzer._check_trial_registration(_Client(), pmid="123", doi=None, analysis=analysis, epmc=epmc)
        assert analysis.trial_registered is True
        assert analysis.score == 20  # SCORE_TRIAL_REGISTERED
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/ -q`
Expected: `1044 passed, 32 skipped` (Task 3's 1043, plus the composition test;
the merge test replaced one rather than adding one).

- [ ] **Step 8: Verify no accumulator threading survives**

Run:

```bash
grep -n "score=score\|indicators=indicators\|, score,\|, indicators," bmlib/transparency/analyzer.py
```

Expected: no matches. Any hit is a sub-step still taking an accumulator by
position.

- [ ] **Step 9: Lint**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

- [ ] **Step 10: Commit**

```bash
git add bmlib/transparency/analyzer.py tests/test_transparency.py
git commit -m "refactor(transparency): finish the carrier migration

_merge_pubmed_signals and _check_trial_registration mutate the carrier;
analyze() now holds no accumulator locals at all and every sub-step
returns None.

_merge_pubmed_signals' 'mutates nothing it is given' contract inverts: it
copied the indicator list because its COI branch rebound while its funder
branch appended, so a caller ignoring the return value saw a half-applied
merge. With no return value that is unrepresentable, and the test pinning
the copy is replaced by one pinning that both branches land together.

Refs #37"
```

---

### Task 5: Documentation, verification and PR

**Files:**
- Modify: `CHANGELOG.md`, `HANDOVER.md`, `ROADMAP.md`
- Check: `docs/manual/transparency.md`

- [ ] **Step 1: Check whether the manual documents any changed signature**

Run:

```bash
grep -n "_check_\|_merge_pubmed\|industry_coi\|Industry funder" docs/manual/transparency.md
```

Every sub-step converted here is private, so the expected result is no hit that
needs changing. If the manual does document one, update it to the new
signature. If it documents `risk_indicators` containing a line per funder
record, correct that to one line per funder.

- [ ] **Step 2: Add the CHANGELOG entry**

Under `## [Unreleased]`, in a `### Changed` subsection (create it if absent):

```markdown
- **`transparency`: `analyze()`'s accumulators moved onto one `_Analysis`
  carrier.** The five sub-steps mutate it in place instead of taking each value
  as a parameter and returning a 4-to-6-element tuple, where element order was
  the only thing binding a value to its name. `SCORE_FUNDER_INFO` is now spent
  through a named `award_funder_info()` method, so a funder source added ahead
  of CrossRef cannot award it twice. Internal: no public signature changes.
- **`transparency`: a funder named repeatedly by CrossRef now yields one
  `Industry funder: X` indicator, not one per award record.** CrossRef emits
  one record per award, so an organisation funding several awards on a paper
  repeated in `risk_indicators`; PubMed's grant list already deduplicated, and
  both sources now follow the same rule. No score, no
  `industry_funding_detected` and no risk level changes — only the length of
  `risk_indicators` for affected papers.
```

- [ ] **Step 3: Update ROADMAP.md**

Change the `⬜ Planned | Replace `analyze()`'s accumulator tuples` row to
`✅ Done`, and rewrite its detail cell to record what shipped:

```markdown
| ✅ Done | Replace `analyze()`'s accumulator tuples | Issue #37 — ten accumulators moved onto a mutable module-private `_Analysis` dataclass that every sub-step mutates in place; all five sub-steps return `None`. `award_funder_info()` makes "spend this component once" a mechanism rather than a convention two call sites must both remember, which is what makes the queued `<DataBankList>` signal safe to add. One behaviour change: `note_industry_funder()` deduplicates for every source, so CrossRef's own repeats of one organisation collapse to a single indicator line (unreleased) |
```

- [ ] **Step 4: Update HANDOVER.md**

- In **Current state**, update the test count to the final figure.
- In **Open GitHub issues**, remove #37 and note that no issue remains open.
- In **Worth doing, not yet an issue**, note that the `<DataBankList>`
  data-deposition follow-up is now the natural next change to `analyze()`, and
  that the carrier is what it should extend.
- Add to **Deliberate non-fixes** the one entry this work creates:

```markdown
- **`_merge_pubmed_signals()` mutates the `_Analysis` it is given and returns
  nothing.** It used to copy `indicators` on the way in and return the copy,
  because its COI branch rebinds the list while its funder branch appends — so
  a caller that ignored the return value got a half-applied merge. With no
  return value that is unrepresentable, and restoring the copy would silently
  discard the merge. Pinned by
  `test_the_merge_applies_both_of_its_branches_to_one_list`.
```

- [ ] **Step 5: Full verification**

Run all three, and read the output rather than assuming:

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check .
uvx ruff@0.15.20 format --check .
```

Expected: `1044 passed, 32 skipped`, `All checks passed!`, and all files
already formatted.

- [ ] **Step 6: Confirm the diff touches nothing unexpected**

```bash
git diff main --stat
```

Expected: only `bmlib/transparency/analyzer.py`, `tests/test_transparency.py`,
`CHANGELOG.md`, `HANDOVER.md`, `ROADMAP.md`, and the two `docs/superpowers/`
files.

- [ ] **Step 7: Commit and push**

```bash
git add CHANGELOG.md HANDOVER.md ROADMAP.md
git commit -m "docs: record the _Analysis carrier and the funder dedup

Refs #37"
git push -u origin refactor/analysis-accumulator-dataclass
```

- [ ] **Step 8: Open the PR**

```bash
gh pr create --base main --title "refactor(transparency): carry analyze()'s accumulators on one object" --body "$(cat <<'EOF'
Closes #37.

## What

`TransparencyAnalyzer.analyze()` accumulated ten values across five sub-steps
and passed each in and out by position. This replaces that with one mutable
module-private `_Analysis` dataclass which every sub-step mutates in place;
all five now return `None`.

## Why

Element order was the only thing binding a value to its name, so a mis-ordered
unpacking was a silent, type-compatible swap — `industry_funding` and
`funder_info_scored` are both `bool`, `score` is interchangeable with any other
`int` — and PR #35 had to widen two signatures to add one boolean.

`funder_info_scored` in particular invited the bug it was added to fix: it
exists so `SCORE_FUNDER_INFO` cannot be awarded by both CrossRef and PubMed,
and the fix was a convention two call sites had to remember. It is now
`award_funder_info()`, a mechanism. That matters because the queued
`<DataBankList>` data-deposition signal is the next thing to land here.

## Behaviour change

One, deliberate: `note_industry_funder()` deduplicates for every source, so a
CrossRef record naming one organisation once per award now yields a single
`Industry funder: X` line instead of one per award. PubMed's grant list already
followed this rule. No score, no `industry_funding_detected` and no risk level
moves — only the length of `risk_indicators` for affected papers. CHANGELOG
entry included.

## Review notes

- The migration is incremental across four commits; every one is green.
- The existing suite is the regression net: the 27 white-box call sites in
  `tests/test_transparency.py` were migrated with their assertions carried over
  unchanged in meaning, not adjusted to pass.
- `_merge_pubmed_signals`' "mutates nothing it is given" contract inverts — see
  the commit message on 'finish the carrier migration' for why the hazard it
  guarded against is now unrepresentable.

Design: `docs/superpowers/specs/2026-07-31-analysis-accumulator-dataclass-design.md`
Plan: `docs/superpowers/plans/2026-07-31-analysis-accumulator-dataclass.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes for the implementer

- **Never adjust an expected value to make a migrated test pass.** Those
  assertions are the only thing distinguishing this refactor from a rewrite. If
  one fails, the implementation is wrong.
- The three `UNKNOWN` early returns in `analyze()` are untouched throughout —
  they precede any accumulation.
- `_PubMedSignals` stays frozen. It is a message from one source; the carrier
  is shared state.
- `_fetch_europepmc`, `_check_pubmed`, `_find_trial_ids`, `_check_trial_results`
  and the `_query_*` helpers are unchanged: they fetch, they do not accumulate.
