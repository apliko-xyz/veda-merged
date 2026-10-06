"""
File: src/veda/shared/enums.py
Title: Canonical Enumerations
Layer: Shared data contract
Status: Merged prototype foundation

Purpose
-------
Defines the single controlled vocabulary used by the merged VEDA system.

The personal prototype used inline Literal values such as "supported",
"insufficient", "answerable", and "insufficient_evidence". The original
VEDA prototype used standalone enums for entity resolution, evidence
categories, claim status, assessment status, missing evidence, and
comparison results.

This merged module reconciles those values so that models, providers,
normalizers, pipeline stages, interfaces, future provenance code, and
future benchmark code all use the same vocabulary.

No other module in src/veda/ may define duplicate enums.

Responsibilities
----------------
- Define source types.
- Define entity and relationship-related types.
- Define entity-resolution outcomes.
- Define evidence categories.
- Define extraction methods.
- Define confidence levels.
- Define claim statuses.
- Define assessment statuses.
- Define missing-evidence reasons.
- Define comparison outcomes.
- Define reporting-period matching states.
- Define provider result states.

This file does not:
-------------------
- Make network requests.
- Resolve entities.
- Normalize source records.
- Extract claims.
- Detect conflicts.
- Build assessment packets.
- Build the provenance graph.
- Run the benchmark.

Migration notes
---------------
- The personal prototype's "deterministic_json" extraction method is
  preserved as ExtractionMethod.DETERMINISTIC_JSON.
- The personal prototype's "answerable" packet status is migrated to
  AssessmentStatus.SUPPORTED. The old string is not silently accepted as
  a new canonical output value.
- The original VEDA prototype's "insufficient" idea is represented by
  ClaimStatus.INSUFFICIENT_EVIDENCE.
- Claims and assessments deliberately use different status enums because
  a supported claim can exist inside an assessment that requires review
  due to another claim or conflict.

Serialization
-------------
Every enum subclasses str and Enum so Pydantic serializes its members as
plain JSON strings.
"""

from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    """
    Identifies the origin of a source record or evidence object.
    """

    SEC_COMPANY_FACTS = "sec_company_facts"
    SEC_FILING = "sec_filing"
    USASPENDING = "usaspending"
    ANNUAL_REPORT = "annual_report"
    GAO = "gao"
    DODIG = "dodig"
    SYNTHETIC = "synthetic"


class EntityType(str, Enum):
    """
    Describes the kind of entity represented in the canonical model.

    The initial merged prototype primarily uses companies, subsidiaries,
    agencies, and documents. The additional values support the planned
    relationship graph and benchmark without requiring a later enum
    redesign.
    """

    PUBLIC_COMPANY = "public_company"
    PRIVATE_COMPANY = "private_company"
    VENDOR = "vendor"
    PARENT = "parent"
    SUBSIDIARY = "subsidiary"
    AFFILIATE = "affiliate"
    JOINT_VENTURE = "joint_venture"
    GOVERNMENT_AGENCY = "government_agency"
    CONTRACT = "contract"
    AWARD = "award"
    COMPONENT = "component"
    SYSTEM = "system"
    EVENT = "event"
    DOCUMENT = "document"
    FINDING = "finding"
    AGREEMENT = "agreement"
    UNKNOWN = "unknown"


class EntityResolutionStatus(str, Enum):
    """
    Describes the result of resolving a raw vendor name.
    """

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class EvidenceCategory(str, Enum):
    """
    Identifies what a piece of evidence measures or describes.

    Evidence category is required for safe comparability decisions.
    Recognized revenue and procurement obligations, for example, are
    different measures and must not be treated as a conflict solely
    because both contain dollar values.
    """

    RECOGNIZED_REVENUE = "recognized_revenue"
    PROCUREMENT_AWARD = "procurement_award"
    PROCUREMENT_OBLIGATION = "procurement_obligation"
    GOVERNMENT_EXPOSURE = "government_exposure"
    CUSTOMER_CONCENTRATION = "customer_concentration"
    CORPORATE_RELATIONSHIP = "corporate_relationship"
    ESTIMATE = "estimate"
    PROXY = "proxy"


class EvidenceTier(str, Enum):
    """
    How close a figure is to the reporting entity's own books.

    ``authoritative_internal`` is reserved. The public pipeline does
    not produce it. SEC recognized revenue is consolidated disclosure.
    USAspending award amounts are a procurement proxy.
    """

    AUTHORITATIVE_INTERNAL = "authoritative_internal"
    AUTHORITATIVE_CONSOLIDATED = "authoritative_consolidated"
    PROCUREMENT_PROXY = "procurement_proxy"
    BOUNDED_OR_INDETERMINATE = "bounded_or_indeterminate"


def derive_evidence_tier(
    source_type: "SourceType",
    evidence_category: EvidenceCategory,
) -> EvidenceTier:
    """Map a source and category onto an evidence tier."""
    if (
        source_type in (SourceType.SEC_COMPANY_FACTS, SourceType.SEC_FILING)
        and evidence_category == EvidenceCategory.RECOGNIZED_REVENUE
    ):
        return EvidenceTier.AUTHORITATIVE_CONSOLIDATED
    if (
        source_type == SourceType.USASPENDING
        and evidence_category in (
            EvidenceCategory.PROCUREMENT_AWARD,
            EvidenceCategory.PROCUREMENT_OBLIGATION,
        )
    ):
        return EvidenceTier.PROCUREMENT_PROXY
    return EvidenceTier.BOUNDED_OR_INDETERMINATE


class ExtractionMethod(str, Enum):
    """
    Identifies how a claim or structured interpretation was produced.
    """

    DETERMINISTIC_FIELD_EXTRACTION = "deterministic_field_extraction"
    DETERMINISTIC_TEXT_EXTRACTION = "deterministic_text_extraction"
    DETERMINISTIC_JSON = "deterministic_json"
    LLM_STRUCTURED_EXTRACTION = "llm_structured_extraction"
    HUMAN_REVIEW = "human_review"
    MANUAL = "manual"


class ConfidenceLevel(str, Enum):
    """
    Qualitative evidence-quality tier.

    Confidence is not the same as assessment status. A high-confidence
    claim can still participate in a conflict and require human review.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClaimStatus(str, Enum):
    """
    Describes the evidentiary status of one claim.
    """

    SUPPORTED = "supported"
    INFERRED = "inferred"
    CONFLICTING = "conflicting"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class ComparisonResult(str, Enum):
    """
    Describes the result of comparing two candidate claims.
    """

    AGREES = "agrees"
    CONFLICTS = "conflicts"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_CONTEXT = "insufficient_context"


class MissingEvidenceReason(str, Enum):
    """
    Explains why expected evidence is absent or insufficient.

    Technical source failures remain distinct from true data absence.
    """

    SOURCE_UNAVAILABLE = "source_unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED_RESPONSE = "malformed_response"
    DATA_NOT_FOUND = "data_not_found"
    ENTITY_AMBIGUOUS = "entity_ambiguous"
    PERIOD_NOT_AVAILABLE = "period_not_available"
    PERIOD_MISMATCH = "period_mismatch"
    EVIDENCE_FOUND_BUT_NOT_SUFFICIENT = "evidence_found_but_not_sufficient"
    EVIDENCE_CONFLICTS = "evidence_conflicts"
    EXTRACTION_FAILED = "extraction_failed"


class AssessmentStatus(str, Enum):
    """
    Describes the final status of a complete assessment packet.

    The personal prototype's "answerable" status maps to SUPPORTED in
    the merged canonical output. The old string is intentionally not
    added as an alias because it would create two serialized vocabularies
    for the same final state.
    """

    SUPPORTED = "supported"
    SUPPORTED_WITH_LIMITATIONS = "supported_with_limitations"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class PeriodMatchStatus(str, Enum):
    """
    Describes how evidence timing relates to the requested period.
    """

    EXACT = "exact"
    ADJACENT = "adjacent"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ProviderStatus(str, Enum):
    """
    Describes the result of a provider retrieval operation.

    This prevents providers from collapsing every failure into None
    before the assessment policy can explain what happened.
    """

    FOUND = "found"
    NOT_FOUND = "not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED_RESPONSE = "malformed_response"


__all__ = [
    "AssessmentStatus",
    "ClaimStatus",
    "ComparisonResult",
    "ConfidenceLevel",
    "EntityResolutionStatus",
    "EntityType",
    "EvidenceCategory",
    "EvidenceTier",
    "derive_evidence_tier",
    "ExtractionMethod",
    "MissingEvidenceReason",
    "PeriodMatchStatus",
    "ProviderStatus",
    "SourceType",
]