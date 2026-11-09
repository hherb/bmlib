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

"""Tiered quality assessment for biomedical publications.

Tiered pipeline (cheapest first, escalating on demand):

- **Tier 1**: PubMed metadata classification (free, instant)
- **Tier 2**: LLM study-design classification (cheap model)
- **Tier 3**: Deep methodological assessment (capable model)
- **Tier 4**: Cochrane-aligned assessment — nine-domain risk of bias plus the
  study-characteristics table (capable model; opt-in, off by default)
"""

from bmlib.quality.cochrane_assessor import CochraneAssessor
from bmlib.quality.cochrane_models import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
    collapse_risk_of_bias,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
)
from bmlib.quality.data_models import (
    DESIGN_TO_SCORE,
    DESIGN_TO_TIER,
    BiasRisk,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    RctLayout,
    StudyDesign,
)
from bmlib.quality.design_agent import (
    DESIGN_CONDENSE_QUERY,
    DESIGN_PROMPT_VERSION,
    DESIGN_SYSTEM_PROMPT,
    DESIGN_TASK_PROMPT,
    DesignAgent,
    DesignProposal,
    LlmQueryCondenser,
    ProposalError,
    ProposalErrorKind,
    QueryCondenser,
    read_design_reply,
)
from bmlib.quality.evidence_ladder import (
    LADDER_VERSION,
    EffectScale,
    EvidenceLadder,
    LadderReason,
    LadderSettings,
    LadderStep,
    Level,
    ReportedResult,
    build_ladder,
    paper_weight,
    start_level,
)
from bmlib.quality.evidence_weight import (
    Assessed,
    AssessedOutcome,
    EvidenceWeight,
    EvidenceWeightError,
    NoPrimaryOutcome,
    NotCovered,
    assess_unattended,
    evidence_weight_from_dict,
)
from bmlib.quality.extractors import (
    extract_sample_size_dimension,
    extract_study_type,
)
from bmlib.quality.manager import QualityManager
from bmlib.quality.outcome_agent import (
    OUTCOME_CONDENSE_QUERY,
    OUTCOME_PROMPT_VERSION,
    OUTCOME_SYSTEM_PROMPT,
    OUTCOME_TASK_PROMPT,
    OutcomeAgent,
    OutcomeProposal,
    ProposedOutcome,
    read_outcome_reply,
)
from bmlib.quality.pico_agent import PICO_PARTS, PicoAgent, verify_quotes
from bmlib.quality.pico_models import (
    PicoCitation,
    PicoContradictedQuote,
    PicoError,
    PicoErrorKind,
    PicoExtraction,
    PicoPart,
    PicoPartState,
    PicoPrepared,
    PicoSuitability,
)
from bmlib.quality.rob2_agent import (
    NOT_APPLICABLE,
    ROB2_DOMAIN_PROMPT,
    ROB2_SYSTEM_PROMPT,
    DomainAssessment,
    DomainFailed,
    DomainResult,
    Escalation,
    MachineAnswer,
    OutcomeRob2,
    Rob2Agent,
    Rob2Run,
    Rob2RunError,
    Rob2RunErrorKind,
    domain_prompt,
    escalation_message,
    read_domain_reply,
    read_escalation_reply,
    rob2_overall,
)
from bmlib.quality.rob2_algorithm import (
    DOMAINS_ALL,
    ROB2_ALGORITHM_VERSION,
    Answer,
    Answers,
    Domain,
    Judgement,
    Rob2Error,
    Scope,
    applicable_questions,
    domain_judgement,
    needs_escalation_decision,
    overall_judgement,
    questions,
)
from bmlib.quality.scoring_models import AssessmentDetail, DimensionScore

__all__ = [
    "BiasRisk",
    "QualityAssessment",
    "QualityFilter",
    "QualityManager",
    "QualityTier",
    "StudyDesign",
    "DESIGN_TO_TIER",
    "DESIGN_TO_SCORE",
    # Cochrane-aligned models
    "CochraneAssessor",
    "CochraneInterventions",
    "CochraneNotes",
    "CochraneOutcomes",
    "CochraneParticipants",
    "CochraneRiskOfBias",
    "CochraneStudyAssessment",
    "CochraneStudyCharacteristics",
    "RiskOfBiasItem",
    "RiskOfBiasJudgement",
    "collapse_risk_of_bias",
    "create_default_cochrane_risk_of_bias",
    "create_default_risk_of_bias_item",
    # Rule-based extractors + audit-trail scoring models
    "AssessmentDetail",
    "DimensionScore",
    "extract_sample_size_dimension",
    "extract_study_type",
    # The PICO agent
    "PICO_PARTS",
    "PicoAgent",
    "PicoCitation",
    "PicoContradictedQuote",
    "PicoError",
    "PicoErrorKind",
    "PicoExtraction",
    "PicoPart",
    "PicoPartState",
    "PicoPrepared",
    "PicoSuitability",
    "verify_quotes",
    # The evidence ladder and the number
    "LADDER_VERSION",
    "EffectScale",
    "EvidenceLadder",
    "LadderReason",
    "LadderSettings",
    "LadderStep",
    "Level",
    "ReportedResult",
    "build_ladder",
    "paper_weight",
    "start_level",
    # The RoB 2 decision rules
    "DOMAINS_ALL",
    "ROB2_ALGORITHM_VERSION",
    "Answer",
    "Answers",
    "Domain",
    "Judgement",
    "Rob2Error",
    "Scope",
    "applicable_questions",
    "domain_judgement",
    "needs_escalation_decision",
    "overall_judgement",
    "questions",
    # The trial layout and the design proposal
    "RctLayout",
    "DESIGN_CONDENSE_QUERY",
    "DESIGN_PROMPT_VERSION",
    "DESIGN_SYSTEM_PROMPT",
    "DESIGN_TASK_PROMPT",
    "DesignAgent",
    "DesignProposal",
    "LlmQueryCondenser",
    "ProposalError",
    "ProposalErrorKind",
    "QueryCondenser",
    "read_design_reply",
    # Primary outcomes and their results
    "OUTCOME_CONDENSE_QUERY",
    "OUTCOME_PROMPT_VERSION",
    "OUTCOME_SYSTEM_PROMPT",
    "OUTCOME_TASK_PROMPT",
    "OutcomeAgent",
    "OutcomeProposal",
    "ProposedOutcome",
    "read_outcome_reply",
    # The RoB 2 assessor
    "NOT_APPLICABLE",
    "ROB2_DOMAIN_PROMPT",
    "ROB2_SYSTEM_PROMPT",
    "DomainAssessment",
    "DomainFailed",
    "DomainResult",
    "Escalation",
    "MachineAnswer",
    "OutcomeRob2",
    "Rob2Agent",
    "Rob2Run",
    "Rob2RunError",
    "Rob2RunErrorKind",
    "domain_prompt",
    "escalation_message",
    "read_domain_reply",
    "read_escalation_reply",
    "rob2_overall",
    # The unattended evidence-weight pipeline
    "Assessed",
    "AssessedOutcome",
    "EvidenceWeight",
    "EvidenceWeightError",
    "NoPrimaryOutcome",
    "NotCovered",
    "assess_unattended",
    "evidence_weight_from_dict",
]
