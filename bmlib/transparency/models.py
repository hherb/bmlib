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

"""Data models for transparency analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# Score at or below which risk is MEDIUM (unless other factors override)
MEDIUM_RISK_SCORE_THRESHOLD = 70


class TransparencyRisk(Enum):
    """Risk level based on transparency analysis."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class TransparencyUnknownReason(Enum):
    """Why an analysis could not determine a transparency score.

    ``UNKNOWN`` is returned for three unrelated reasons, and a caller may well
    want to treat them differently — retry an outage later, skip a disabled
    analyzer silently. Each is also named in ``risk_indicators``, but as prose
    for humans; this enum is the machine-readable form (issue #21).
    """

    DISABLED = "disabled"  # settings.enabled is False
    NO_IDENTIFIER = "no_identifier"  # neither PMID nor DOI was supplied
    UNREACHABLE = "unreachable"  # no external API answered


class FullTextStatus(Enum):
    """What became of this analysis's attempt to read the article's full text.

    ``full_text_analyzed`` says whether the findings came from full text; this
    says *why not* when they did not, which is a different question and the
    one a stored result could not previously answer (issue #161). Several
    outcomes used to be indistinguishable downstream — a non-200, a document
    left unsegmentable, and one that was entirely nested articles all reached
    storage as ``full_text_analyzed=False`` and the indicator *"COI disclosure
    status unknown (full text unavailable)"*, which is **false** for the
    latter two: Europe PMC served HTTP 200 with a document.

    That matters because results are cacheable
    (:attr:`TransparencySettings.cache_results`) and driven concurrently, so a
    refusal is stored, permanent and unmarked, while the score silently loses
    up to :data:`~bmlib.transparency.analyzer.SCORE_COI_DISCLOSED` +
    :data:`~bmlib.transparency.analyzer.SCORE_DATA_FULL_OPEN` — enough on its
    own to reach ``HIGH`` against the default ``score_threshold`` and set
    ``tier_downgrade_applied``. *"Which of my stored results were computed
    without the full text I was served?"* is the question, and it was
    answerable only by grepping logs and re-joining identifiers to a corpus.
    ``publications/`` added ``FetchResult.note`` -> ``SyncReport.notes`` for
    the identical reason: permanent *and* invisible is the pair these rules
    exist to break up.

    Each refusal is its own member rather than one ``REFUSED``, because they
    are different claims about what arrived and an operator acts on them
    differently — the argument issue #160 made for raising on an unterminated
    construct instead of folding it onto the ``None`` an unclosed region
    already returned. Use :attr:`is_refusal` for the grouping rather than
    enumerating the members at each call site; a member added later then has
    to choose a side.
    """

    #: No request was made, **and Europe PMC's own answer is why**. Four
    #: causes, and the level is part of what tells them apart:
    #:
    #: * it never claimed to hold full text (``inEPMC != "Y"``) — the
    #:   dominant case, an ordinary closed-access paper, and silent;
    #: * it answered with no record for this identifier, also silent;
    #: * the record claimed full text and carried an identifier that is not a
    #:   Europe PMC accession, so no URL could be built from it — at
    #:   **DEBUG**, since nothing is wrong: a ``MED`` record's bare PMID and a
    #:   Bookshelf ``bookid`` both land here, measured at 43 of 123 records in
    #:   a source-stratified draw (2026-09-09, issue #188);
    #: * the record claimed full text and carried **nothing at all** to
    #:   address it by — at **WARNING**, being a malformed record. Issue #207
    #:   is that this one alone makes the member's opening sentence false;
    #:   measured at 0 of 124.
    #:
    #: Distinct from a request that was made and answered — with a 404, which
    #: is :attr:`NOT_SERVED`, or any other way of producing no document,
    #: which is :attr:`REQUEST_FAILED`.
    #:
    #: **The list carried three causes and the level of one of them into the
    #: release that added the fourth** (PR #219's review). It named the
    #: WARNING and not the DEBUG, so a reader seeing this member and no
    #: warning concluded ``inEPMC != "Y"`` — for what is now the second
    #: largest cause. That is the drift :attr:`is_refusal`'s own docstring is
    #: pinned against twelve lines below, and this enumeration is not
    #: mechanisable the same way: a cause is a branch, not a member.
    #:
    #: **Narrowed in issue #193**, which is the same defect as #191 one step
    #: up the call chain: it also covered the case where the search that
    #: *gates* the full-text step never answered at all, so a stored result
    #: read "Europe PMC told us there is nothing here" for an outage in which
    #: Europe PMC told us nothing. That is :attr:`SEARCH_FAILED`.
    NOT_ATTEMPTED = "not_attempted"
    #: No full-text request was made because the Europe PMC **search** that
    #: gates it produced no answer — a non-200, or a request that raised.
    #:
    #: Every other member of this enum describes what became of the full-text
    #: request; this one says that step was never reached, and why. It exists
    #: because the alternative is worse than a gap: ``analyze()`` calls
    #: ``_check_europepmc`` only when the search returned a record, so during
    #: an outage the whole full-text path is skipped, the score loses up to
    #: :data:`~bmlib.transparency.analyzer.SCORE_COI_DISCLOSED` +
    #: :data:`~bmlib.transparency.analyzer.SCORE_DATA_FULL_OPEN`, and the
    #: result can reach ``HIGH`` with ``tier_downgrade_applied`` set — while
    #: storing a status a reader takes as *"we had no reason to ask"*.
    #:
    #: Not a refusal: nothing was served. *"Would re-running change this?"* is
    #: ``yes`` here, as it is for :attr:`REQUEST_FAILED`, and that is the
    #: question the two of them exist to make answerable — results are
    #: cacheable and nothing in ``transparency/`` retries.
    SEARCH_FAILED = "search_failed"
    #: Requested, and Europe PMC answered **HTTP 404**: it serves no
    #: open-access full text for this article. Together with
    #: :attr:`NOT_ATTEMPTED` — the dominant case, and every paper Europe PMC
    #: holds no open-access full text for — this is an outcome for which
    #: *no document arrived* is true. The refusals below are the ones for
    #: which it is false, which is what :attr:`is_refusal` groups; neither of
    #: these two is "the only" such outcome. (Phrased as *"full text
    #: unavailable"* until PR #205's review, after issue #203 deleted the
    #: indicator string that carried it.)
    #:
    #: **Narrowed to the 404 in issue #191.** It used to mean any non-200 and
    #: any raised request as well, which put a claim in Europe PMC's mouth
    #: that only the 404 makes: a 503 says nothing whatever about whether they
    #: hold this article. Everything else is :attr:`REQUEST_FAILED`.
    NOT_SERVED = "not_served"
    #: The attempt produced no document **and Europe PMC did not say it holds
    #: none** — the distinction from :attr:`NOT_SERVED`, which is exactly the
    #: 404. **Four** ways in — one per distinguishable behaviour, which is
    #: three ``return`` statements because two of them share a branch — and
    #: they share a status because they share that claim, not because they
    #: share a cause:
    #:
    #: * the request raised in the environment — a timeout, a reset, a DNS
    #:   failure (issue #187);
    #: * the request raised a bmlib defect, which also logs at ERROR and is
    #:   the only one of the four that is bmlib's fault (issue #187);
    #: * Europe PMC answered a non-404 non-200 — a 429, a 503, a 403, or a
    #:   redirect, which is not followed (issue #191). One branch and one log
    #:   line, so it is one way in and not two;
    #: * Europe PMC answered 200 with an **empty body**, which used to reach
    #:   the *entirely nested* branch and store a refusal that did not happen
    #:   (issue #190).
    #:
    #: Counted as five until the review of PR #192, which split the third
    #: bullet in two and so disagreed with every other statement of the same
    #: set — a count is of what you looked for, and a status vocabulary is
    #: counted by behaviour.
    #:
    #: "Full text unavailable" is true of it, so it is not a refusal. What it
    #: adds over :attr:`NOT_SERVED` is that *"would re-running change this?"*
    #: is answerable: results are cacheable
    #: (:attr:`TransparencySettings.cache_results`) and there is no retry
    #: anywhere in ``transparency/``, so without this member an outage window
    #: caches absences indistinguishable from legitimately closed-access
    #: papers, each having silently lost up to 30 points.
    REQUEST_FAILED = "request_failed"
    #: Served, segmented, and scanned as the article's own text.
    ANALYZED = "analyzed"
    #: Served, but the document did not arrive whole — it carries no
    #: ``</article>``, so its tail was lost in transit (issue #183).
    TRUNCATED = "truncated"
    #: Served, but a comment, CDATA section, processing instruction, doctype
    #: or nested-article tag opens and never closes (issue #160).
    UNTERMINATED_MARKUP = "unterminated_markup"
    #: Served, but a ``<sub-article>``/``<response>`` region is left open at
    #: the end of the document, so the article's own text cannot be told from
    #: a review round's (issue #119).
    UNCLOSED_REGION = "unclosed_region"
    #: Served, but nothing outside a nested-article region remained.
    ENTIRELY_NESTED = "entirely_nested"

    @property
    def is_refusal(self) -> bool:
        """Was full text served and then refused?

        ``True`` for exactly the outcomes where Europe PMC answered HTTP 200
        with a document that bmlib then declined to scan. ``False`` for
        :attr:`ANALYZED`, and for :attr:`NOT_SERVED`, :attr:`REQUEST_FAILED`,
        :attr:`SEARCH_FAILED` and :attr:`NOT_ATTEMPTED` — where nothing was
        served, so there is nothing to have refused. (That enumeration is
        prose beside a mechanised partition, and it went stale the first time
        a member was added — issue #193's, missed here while
        ``docs/manual/transparency.md`` was updated. The authority is
        :data:`_REFUSED_FULL_TEXT_STATUSES`, which
        ``test_every_status_chooses_a_side`` pins; this list is a reader's
        convenience and ``test_the_docstring_names_every_non_refusal`` is what
        stops it drifting again.) An HTTP 200 carrying an *empty* body is on
        that side too, and is why :attr:`REQUEST_FAILED` exists: 200 alone is
        not "a document arrived" (issue #190).
        """
        return self in _REFUSED_FULL_TEXT_STATUSES


#: Defined beside the enum rather than inside it. The obvious objection — that
#: a plain set attribute in an ``Enum`` body becomes a member — is answered by
#: ``enum.nonmember`` on the Python this package targets, so it is *not* the
#: reason: members are not bound during class-body execution, so a frozenset
#: written in the body would capture the raw **values** (``frozenset({'truncated',
#: ...})``) and force ``self.value in ...``, throwing away member identity for
#: no gain. Every other alternative (a nested class, a module-level function)
#: puts the grouping further from the members it groups. Read only through
#: :attr:`FullTextStatus.is_refusal`.
_REFUSED_FULL_TEXT_STATUSES = frozenset(
    {
        FullTextStatus.TRUNCATED,
        FullTextStatus.UNTERMINATED_MARKUP,
        FullTextStatus.UNCLOSED_REGION,
        FullTextStatus.ENTIRELY_NESTED,
    }
)

#: The other side, named rather than left implicit — the whole of *"a member
#: added later has to choose a side"*. Membership of the refused set alone
#: leaves the rule enforced by prose: a member added later and omitted from it
#: simply reads as ``is_refusal is False``, and the silent default runs the
#: wrong way — it reports a served-and-refused document as one that never
#: arrived, which is the falsehood issue #161 exists to remove. (It used to
#: say *"routes into the 'full text unavailable' indicator"*. Issue #203
#: deleted that routing and that string, and left no in-library reader of
#: :attr:`FullTextStatus.is_refusal` at all — the property is public API for
#: downstreams now, which is who the wrong answer would be given to. Corrected
#: in PR #205's review.) Naming
#: both sides lets ``test_every_status_chooses_a_side`` assert the partition,
#: which turns that member into a red test instead. It has since been
#: collected twice: :attr:`FullTextStatus.REQUEST_FAILED` was the first member
#: the rule was written against, and :attr:`FullTextStatus.SEARCH_FAILED` the
#: next, each made to choose by the partition test. Stated ordinal-free —
#: an earlier draft called ``REQUEST_FAILED`` "the eighth member" and the
#: next one "the ninth", which was one more count to keep in step for no
#: gain. The same rule
#: ``TestTheAuditNetIsComplete`` and ``TestEveryCounterIsInAGeneration`` make
#: in ``fulltext/``, and for the same reason: a rule enforced by prose is not
#: enforced.
_NOT_REFUSED_FULL_TEXT_STATUSES = frozenset(
    {
        FullTextStatus.NOT_ATTEMPTED,
        FullTextStatus.SEARCH_FAILED,
        FullTextStatus.NOT_SERVED,
        FullTextStatus.REQUEST_FAILED,
        FullTextStatus.ANALYZED,
    }
)


class TrialResultsStatus(Enum):
    """What became of this analysis's attempt to establish posted results.

    ``trial_results_compliant`` answers *"were results posted?"* with a bare
    ``bool``, and ``False`` was four different things (issue #198): the trial
    was asked about and has none posted, nobody managed to ask, the
    registration is somewhere ClinicalTrials.gov has no answer to give, or
    there is no registered trial at all. ``risk_indicators`` distinguishes
    three of those in prose — but prose is a list a downstream has to
    string-match, and both known downstreams render the flag instead, so the
    field that is easiest to read is the one that cannot be read correctly.

    :class:`FullTextStatus`'s argument (issue #161) one endpoint over, and
    the rules come with it. Use :attr:`is_answered` for the grouping rather
    than enumerating the members at each call site, so a member added later
    has to choose a side. Members that share an indicator string are still
    separate here: :attr:`REQUEST_FAILED` and :attr:`NOT_CHECKABLE` answer
    *"would re-running change this?"* differently, which is the question
    results being cacheable makes worth storing, and the prose deliberately
    does not distinguish them because nothing downstream can act on the
    difference in a sentence.
    """

    #: No registration was established, so ClinicalTrials.gov was never asked
    #: — the ordinary case, every paper that reports no trial. Read with
    #: ``trial_registered``, which is the field that says whether a
    #: registration was found; this one says only that no accession was
    #: followed up. Note that ``trial_registered`` is itself ``False`` both
    #: for a paper with no trial and for one whose sources never answered
    #: (issue #204) — this member inherits that ambiguity and does not add
    #: to it.
    NOT_REGISTERED = "not_registered"
    #: ClinicalTrials.gov was asked and reports posted results. The only
    #: member for which ``trial_results_compliant`` is ``True``.
    POSTED = "posted"
    #: ClinicalTrials.gov was asked about **every accession the paper named**,
    #: and none reports posted results. **With :attr:`POSTED`, one of the two
    #: members that are findings about the trial**; :attr:`REQUEST_FAILED` and
    #: :attr:`NOT_CHECKABLE` are findings about bmlib's ability to ask, which
    #: is the whole distinction issue #194 turned out to rest on — the edge
    #: refused every request for a release, and a bare ``False`` published
    #: that as *"Registered trial without posted results"*. (Read *"the only
    #: member that is a finding about the trial"*, which is false of
    #: :attr:`POSTED`, and named the other two positionally — the ordinal
    #: construction :data:`_NOT_REFUSED_FULL_TEXT_STATUSES` retracts above.
    #: Corrected in PR #205's review.)
    #:
    #: It is a finding about **every** accession the paper named, which is
    #: what issue #206 made true: until then the loop stopped after
    #: ``MAX_TRIAL_IDS_TO_CHECK`` and treated one reply as enough, so an
    #: accession that was never asked about or never answered was not
    #: represented here and this member stood for a paper it had not
    #: established anything about. :attr:`PARTLY_ANSWERED` is that case now,
    #: and the hedge this comment used to carry is gone rather than reworded
    #: — in the **lead sentence** as well as here, which read *"every accession
    #: it answered for"* and so asserted nothing at all (PR #225's review).
    NOT_POSTED = "not_posted"
    #: Some accessions were asked about and answered, none reports posted
    #: results, and **the rest were not asked about or did not answer** —
    #: bmlib's own cap truncated the list, or a request was refused. Not a
    #: finding: the accession nobody reached may be the one with results, so
    #: :attr:`NOT_POSTED`'s claim about the paper cannot be made (issue #206).
    #:
    #: **The two causes share this member because they co-occur, not because
    #: the usual criterion merges them.** *"Would re-running change this?"*
    #: separates :attr:`REQUEST_FAILED` from :attr:`NOT_CHECKABLE`, and it
    #: does discriminate here: ``no`` for the cap, which truncates identically
    #: on a re-run, and ``yes`` for an accession that did not answer. The
    #: first draft of this comment reasoned about the cap alone and
    #: generalised to the member, which is issue #191's own defect (PR #225's
    #: review). What actually rules a split out is that the walk produces
    #: ``unestablished = dropped + asked - answered``, a **sum**: one paper
    #: can have both causes at once, so splitting would need three members or
    #: a second field. Which cause it was reaches an operator as a log line,
    #: where raising the cap is an action. The residual is that a downstream
    #: doing selective backfill cannot ask *"retry, or change the config?"* of
    #: the stored value — filed rather than argued away.
    #:
    #: Measured over the 30 papers naming a ClinicalTrials.gov accession in
    #: the 2026-09-08 trial-enriched draw: the cap truncates **8 of 30**, and
    #: a partly-answered check is **1 of 30**. Read the second as a **floor**
    #: and the first as measured: that draw predates PR #213's correction of
    #: ``TrialCheck.answered``, which counted HTTP 200 where bmlib counts a
    #: non-``None`` return and so *deflated* exactly this row, while the
    #: truncation count is derived from ``found > probed`` and is independent
    #: of it. The emphasis-reversing comparison rests on the 8, which needs no
    #: caveat (PR #225's review).
    PARTLY_ANSWERED = "partly_answered"
    #: Accessions were asked about and not one answered — a refusal, a 404, an
    #: unusable body, a request that raised, or a ``hasResults`` of a type
    #: ``_json_bool`` will not read (which is silent today, issue #226).
    #: **It also covers a paper whose list bmlib's own cap truncated**, when
    #: none of the accessions it did ask about answered: :attr:`PARTLY_ANSWERED`
    #: needs one answer to be partial, so a fully-unanswered walk lands here
    #: whether or not the list was complete, and *"was this paper's accession
    #: list complete?"* is unanswerable from storage in both members. Named
    #: rather than left to be discovered, which is the correction PR #219 made
    #: to :attr:`FullTextStatus.NOT_ATTEMPTED` one enum over. *"Would re-running
    #: change
    #: this?"* is ``yes``, and results are cacheable with no retry anywhere in
    #: ``transparency/``, so an outage window otherwise caches absences
    #: indistinguishable from real ones. :attr:`FullTextStatus.REQUEST_FAILED`
    #: exists for that reason and this member is its twin.
    REQUEST_FAILED = "request_failed"
    #: A registration ClinicalTrials.gov cannot be asked about: it belongs to
    #: another registry, or it is a ClinicalTrials.gov entry whose accession is
    #: missing or malformed. Deliberately not named *"registered elsewhere"*,
    #: which would be a plain falsehood in the second case — the same reason
    #: the indicator string it shares with :attr:`REQUEST_FAILED` names no
    #: registry. *"Would re-running change this?"* is ``no``.
    NOT_CHECKABLE = "not_checkable"

    @property
    def is_answered(self) -> bool:
        """Does ``trial_results_compliant`` mean what it says?

        ``True`` for exactly :attr:`POSTED` and :attr:`NOT_POSTED`, which are
        the outcomes where it does. For the rest the flag is ``False`` because
        **not enough** was established, not because the trial fell short — the
        read both downstreams get wrong today. The summary line asks the
        contract rather than *"did ClinicalTrials.gov answer?"*, whose plain
        answer for the partly-answered member is *"yes, partly"* while this
        property says ``False`` (PR #225's review); "nothing" was likewise
        false of that member alone. The authority is :data:`_ANSWERED_TRIAL_RESULTS_STATUSES`
        and the partition is pinned by ``test_every_status_chooses_a_side``.
        """
        return self in _ANSWERED_TRIAL_RESULTS_STATUSES


#: Both sides named, for :data:`_REFUSED_FULL_TEXT_STATUSES`' reason: a member
#: added later and omitted from the answered set would simply read as *not
#: answered*, which is the plausible-looking default, so the rule would be
#: enforced by prose alone. Naming both makes the omission a red test.
_ANSWERED_TRIAL_RESULTS_STATUSES = frozenset(
    {
        TrialResultsStatus.POSTED,
        TrialResultsStatus.NOT_POSTED,
    }
)

#: The other side. See :data:`_ANSWERED_TRIAL_RESULTS_STATUSES`.
#:
#: :attr:`TrialResultsStatus.PARTLY_ANSWERED` sits here even though
#: ClinicalTrials.gov did answer for some accessions, and that is the
#: load-bearing half of the member (issue #206). ``is_answered`` exists so a
#: downstream knows whether ``trial_results_compliant`` means what it says,
#: and both known downstreams render that flag: ``False`` under
#: ``is_answered`` ``True`` reads as *"the trial fell short"*, which is the
#: unearned sentence issue #198 exists to stop being published.
_UNANSWERED_TRIAL_RESULTS_STATUSES = frozenset(
    {
        TrialResultsStatus.NOT_REGISTERED,
        TrialResultsStatus.REQUEST_FAILED,
        TrialResultsStatus.NOT_CHECKABLE,
        TrialResultsStatus.PARTLY_ANSWERED,
    }
)


@dataclass
class TransparencySettings:
    """User-configurable transparency thresholds and orchestration hints.

    Two groups of fields, with different owners:

    *Honoured by the analyzer* — ``enabled`` short-circuits
    :meth:`~bmlib.transparency.analyzer.TransparencyAnalyzer.analyze`, and
    ``score_threshold``, ``industry_funding_triggers_downgrade``,
    ``missing_coi_triggers_downgrade`` and ``tier_downgrade_amount`` feed
    :func:`calculate_risk_level` and the tier downgrade.

    *Honoured by the caller* — ``filtering_enabled``, ``max_concurrent_analyses``
    and ``cache_results`` describe how a consuming application should
    orchestrate analyses. The library analyses one document per call and does
    no filtering, threading, or caching of its own; it carries these so an
    application has a single place to configure transparency behaviour.
    """

    # --- Honoured by the analyzer ---
    enabled: bool = True
    score_threshold: int = 40  # Below this -> HIGH risk
    industry_funding_triggers_downgrade: bool = True
    missing_coi_triggers_downgrade: bool = True
    tier_downgrade_amount: int = 1

    # --- Honoured by the caller (see class docstring) ---
    filtering_enabled: bool = False  # Whether to exclude high-risk papers
    max_concurrent_analyses: int = 3
    cache_results: bool = True


@dataclass
class TransparencyResult:
    """Result of a transparency analysis for a single document."""

    document_id: str
    transparency_score: int  # 0-100
    risk_level: TransparencyRisk

    industry_funding_detected: bool = False
    industry_funding_confidence: float = 0.0
    data_availability_level: str = "unknown"
    coi_disclosed: bool | None = True
    trial_registered: bool = False
    trial_results_compliant: bool = False
    # Reserved: no detection is implemented, so this is always False.
    # Deciding it would mean comparing a trial's pre-registered primary
    # outcomes against those actually reported — see ROADMAP.md. Kept in the
    # schema so persisted results do not need migrating when it lands.
    outcome_switching_detected: bool = False

    risk_indicators: list[str] = field(default_factory=list)
    tier_downgrade_applied: int = 0

    analyzed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    analyzer_version: str = "1.0"
    full_text_analyzed: bool = False

    # Set if and only if `risk_level` is UNKNOWN. Declared last for the same
    # reason as `Publication.pmcid`: downstream projects construct this
    # dataclass positionally, so inserting a field beside its logical
    # neighbours (`risk_level`, `risk_indicators`) would shift every following
    # argument by one with no error raised anywhere.
    unknown_reason: TransparencyUnknownReason | None = None

    # What became of the full-text attempt (issue #161). Declared last for the
    # same positional-stability reason as `unknown_reason` above, and after it
    # because it was added later — the rule is "append", not "sort".
    #
    # `None` means *not recorded*, never `NOT_ATTEMPTED`. Results persisted
    # before the field existed load with the default, and one of those may
    # perfectly well carry `full_text_analyzed=True`; reading that back as a
    # determinate "nothing was attempted" would be a worse answer than
    # admitting the field was not written. Same argument as `unknown_reason`'s
    # defensive `.get()` in `from_dict`.
    full_text_status: FullTextStatus | None = None

    # What became of the posted-results check (issue #198). Appended for the
    # positional-stability reason above — the rule is "append", not "sort" —
    # and `None` means *not recorded* here too, never `NOT_REGISTERED`: a
    # result persisted before the field existed may carry
    # `trial_results_compliant=True`, and reading that back as "no
    # registration" would be a determinate answer to a question that was
    # never asked. `trial_results_compliant` stays as the compatibility
    # field; this is the one to branch on.
    trial_results_status: TrialResultsStatus | None = None

    def __post_init__(self) -> None:
        """Reject a reason on a result that is not ``UNKNOWN``.

        Only this direction is enforced. The converse — every ``UNKNOWN``
        carries a reason — holds for anything :meth:`analyze` produces, but
        results persisted before the field existed load with ``None``, and
        refusing to construct those would make the field a breaking change
        rather than an additive one.

        :attr:`full_text_status` is held to the matching pair of rules, and
        for the matching reason. When it is recorded it must agree with
        :attr:`full_text_analyzed` — that flag is what qualifies a stored
        ``coi_disclosed=False`` as *scanned and absent* rather than
        *undeterminable*, so a status disagreeing with it makes the pair
        uninterpretable. When it is ``None`` nothing is imposed, since that is
        every result written before the field existed.

        :attr:`trial_results_status` is the third of the same shape, against
        :attr:`trial_results_compliant`. The flag is the compatibility field a
        downstream already renders, so a result where the two disagree is
        uninterpretable whichever one is believed — and only
        :attr:`TrialResultsStatus.POSTED` supports the flag, every other
        member describing an outcome in which nothing was established.
        """
        if self.unknown_reason is not None and self.risk_level is not TransparencyRisk.UNKNOWN:
            raise ValueError(
                f"unknown_reason={self.unknown_reason.value!r} is meaningless on a "
                f"{self.risk_level.value!r} result; it is set only when risk_level is UNKNOWN"
            )
        if (
            self.full_text_status is not None
            and (self.full_text_status is FullTextStatus.ANALYZED) != self.full_text_analyzed
        ):
            raise ValueError(
                f"full_text_status={self.full_text_status.value!r} contradicts "
                f"full_text_analyzed={self.full_text_analyzed!r}; the flag is set if and "
                f"only if the status is {FullTextStatus.ANALYZED.value!r}"
            )
        if (
            self.trial_results_status is not None
            and (self.trial_results_status is TrialResultsStatus.POSTED)
            != self.trial_results_compliant
        ):
            raise ValueError(
                f"trial_results_status={self.trial_results_status.value!r} contradicts "
                f"trial_results_compliant={self.trial_results_compliant!r}; the flag is set "
                f"if and only if the status is {TrialResultsStatus.POSTED.value!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "document_id": self.document_id,
            "transparency_score": self.transparency_score,
            "risk_level": self.risk_level.value,
            "industry_funding_detected": self.industry_funding_detected,
            "industry_funding_confidence": self.industry_funding_confidence,
            "data_availability_level": self.data_availability_level,
            "coi_disclosed": self.coi_disclosed,
            "trial_registered": self.trial_registered,
            "trial_results_compliant": self.trial_results_compliant,
            "outcome_switching_detected": self.outcome_switching_detected,
            "risk_indicators": self.risk_indicators,
            "tier_downgrade_applied": self.tier_downgrade_applied,
            "analyzed_at": self.analyzed_at.isoformat(),
            "analyzer_version": self.analyzer_version,
            # Provenance, not a finding — but it qualifies `coi_disclosed`:
            # only when the full text was read does `False` mean "scanned and
            # absent" rather than "undeterminable". Dropping it on the way to
            # storage made a persisted `coi_disclosed=False` uninterpretable.
            "full_text_analyzed": self.full_text_analyzed,
            # Enum serialised by value, mirroring `risk_level`.
            "unknown_reason": self.unknown_reason.value if self.unknown_reason else None,
            # Likewise. `full_text_analyzed` above says whether full text was
            # read; this says what became of the attempt when it was not, so
            # dropping it here would put the answer back in the log only —
            # which is issue #161 exactly.
            "full_text_status": self.full_text_status.value if self.full_text_status else None,
            # Likewise again. `trial_results_compliant` above says whether
            # results were posted; this says what was established, and for
            # every member but `POSTED` the flag is `False` for a reason the
            # flag cannot carry (issue #198).
            "trial_results_status": (
                self.trial_results_status.value if self.trial_results_status else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransparencyResult:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        analyzed_at_raw = data.get("analyzed_at")
        if analyzed_at_raw:
            analyzed_at = datetime.fromisoformat(analyzed_at_raw)
        else:
            analyzed_at = datetime.now(tz=UTC)

        # Absent from results persisted before the field existed, and null on
        # every determinate result, so the *key* is read defensively rather
        # than indexed. A present, **non-empty** and unrecognised value still
        # raises, exactly as `risk_level` does: a member this version does not
        # know about is a result it cannot interpret, and inventing `None` for
        # it would report a determinate analysis. The empty string is the one
        # present value that reads as `None` instead, and belongs there on the
        # same argument — it names no member, so there is no determinate
        # analysis to misreport, and a NOT NULL text column defaulting to `''`
        # is exactly a row that never recorded the field. The comment claimed
        # every present value raises until PR #205's review; all three of
        # these reads have always behaved this way.
        unknown_reason_raw = data.get("unknown_reason")
        unknown_reason = (
            TransparencyUnknownReason(unknown_reason_raw) if unknown_reason_raw else None
        )

        # Read the same way and for the same reasons: absent from results
        # persisted before the field existed, so the key is not indexed — but
        # a present, non-empty and unrecognised value still raises rather than
        # loading as `None`, since a member this version does not know about
        # is a result it cannot interpret, and `None` would report it as never
        # recorded.
        full_text_status_raw = data.get("full_text_status")
        full_text_status = FullTextStatus(full_text_status_raw) if full_text_status_raw else None

        # And the same again, for the same three reasons.
        trial_results_status_raw = data.get("trial_results_status")
        trial_results_status = (
            TrialResultsStatus(trial_results_status_raw) if trial_results_status_raw else None
        )

        return cls(
            document_id=data["document_id"],
            transparency_score=data["transparency_score"],
            risk_level=TransparencyRisk(data["risk_level"]),
            industry_funding_detected=data.get("industry_funding_detected", False),
            industry_funding_confidence=data.get("industry_funding_confidence", 0.0),
            data_availability_level=data.get("data_availability_level", "unknown"),
            coi_disclosed=data.get("coi_disclosed", True),
            trial_registered=data.get("trial_registered", False),
            trial_results_compliant=data.get("trial_results_compliant", False),
            outcome_switching_detected=data.get("outcome_switching_detected", False),
            risk_indicators=data.get("risk_indicators", []),
            tier_downgrade_applied=data.get("tier_downgrade_applied", 0),
            analyzed_at=analyzed_at,
            analyzer_version=data.get("analyzer_version", "1.0"),
            full_text_analyzed=data.get("full_text_analyzed", False),
            unknown_reason=unknown_reason,
            full_text_status=full_text_status,
            trial_results_status=trial_results_status,
        )


def calculate_risk_level(
    score: int,
    industry_funding: bool,
    data_availability: str,
    coi_disclosed: bool | None,
    settings: TransparencySettings,
) -> TransparencyRisk:
    """Determine risk level from transparency metrics.

    Risk levels:
    - HIGH: score < threshold OR (industry + restricted data) OR missing COI
    - MEDIUM: score <= 70 OR industry funding present
    - LOW: score > 70 and transparent

    ``coi_disclosed`` is tri-state: ``True`` (a COI statement was found),
    ``False`` (full text was inspected and no COI statement exists), or
    ``None`` (could not be determined — e.g. full text unavailable). Only an
    *explicit* ``False`` triggers the missing-COI downgrade; ``None`` does not,
    to avoid penalising papers merely because their COI status is unknown.
    """
    if score < settings.score_threshold:
        return TransparencyRisk.HIGH

    if settings.industry_funding_triggers_downgrade:
        restricted = data_availability in ("restricted", "not_available", "not_stated")
        if industry_funding and restricted:
            return TransparencyRisk.HIGH

    if settings.missing_coi_triggers_downgrade and coi_disclosed is False:
        return TransparencyRisk.HIGH

    if score <= MEDIUM_RISK_SCORE_THRESHOLD:
        return TransparencyRisk.MEDIUM

    if industry_funding:
        return TransparencyRisk.MEDIUM

    return TransparencyRisk.LOW
