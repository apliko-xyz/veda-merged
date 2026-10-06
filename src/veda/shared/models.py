"""
File: src/veda/shared/models.py
Title: Canonical Data Models
Layer: Shared data contract
Status: Merged prototype foundation — approved design, revised v3

Purpose
-------
Defines the canonical Pydantic models the pipeline moves between
stages. Every module downstream of this file — providers,
normalization, pipeline, interfaces, adapters, and the future graph
and benchmark — composes its outputs from these models. No other
module defines a data shape.

This file is the *shape* of the system. It does not contain retrieval
logic, extraction logic, conflict-detection logic, or assessment
logic. Those live in dedicated modules. This file only declares what
an object is and what invariants it must satisfy at construction.

Composition
-----------
- Enums: imported from veda.shared.enums. Not redeclared.
- Periods: Period and RequestedPeriod from veda.shared.periods.
  Every reporting-period field is typed Period. RequestedPeriod
  appears only in AssessmentRequest.
- IDs: every ID field is validated by calling the corresponding
  parser from veda.shared.ids. No ID is reconstructed locally, and
  no local regex is used to check an ID format.

The four fiscal-year concepts
-----------------------------
The system distinguishes four things that all sound like "the fiscal
year." They must never be conflated:

  1. Requested fiscal year
     Where: AssessmentRequest.fiscal_year (int) and
            AssessmentRequest.requested_period.fiscal_year
     Meaning: what the caller asked for.
     Can it be wrong? No. It is the input.

  2. Actual period end date
     Where: Period.end (date)
     Meaning: the real end date of the reported period, as recorded
              in the source.
     Can it be wrong? No. This is ground truth from the source.

  3. Evidence fiscal year
     Where: Period.fiscal_year(), derived from Period.end
     Meaning: the year the evidence actually covers.
     Can it be wrong? No. It is derived from (2).

  4. SEC fy/fp labels
     Where: raw SEC record metadata
     Meaning: SEC's own labeling of the filing and period.
     Can it be wrong? YES. The SEC prototype discovered during live
              testing that SEC's "fy" label reflects the FILING's
              year, not the value's year, and its "fp" label can
              mark a three-month span as a full fiscal year.

Matching rule
-------------
Evidence is matched against the request via
Period.match_status_against(RequestedPeriod). Nothing else decides
whether evidence answers the request. SEC's fy/fp labels are retained
only for audit and diagnostic purposes; they are never consulted for
matching.

Consistency invariant
---------------------
AssessmentRequest.fiscal_year must equal
AssessmentRequest.requested_period.fiscal_year. Enforced by a model
validator. Two sources of truth are allowed only when they are proven
equal on construction.

Missing-evidence identity
-------------------------
MissingEvidence records are embedded inside an Assessment and have no
independent identity. If future provenance-graph work requires them to
be independently referenced, a missing_evidence_id will be added to
File 8 at that time.

This file does not:
-------------------
- Make network requests.
- Retrieve or normalize evidence.
- Extract claims.
- Detect conflicts.
- Compare periods (periods compare themselves via
  Period.match_status_against).
- Generate IDs (IDs come from veda.shared.ids).
- Validate cross-record references (that is File 10,
  veda/shared/validation.py).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from veda.shared.enums import (
    AssessmentStatus,
    ClaimStatus,
    ComparisonResult,
    ConfidenceLevel,
    EntityResolutionStatus,
    EntityType,
    EvidenceCategory,
    EvidenceTier,
    ExtractionMethod,
    derive_evidence_tier,
    MissingEvidenceReason,
    SourceType,
)
from veda.shared.ids import (
    parse_assessment_id,
    parse_chunk_id,
    parse_claim_id,
    parse_conflict_id,
    parse_document_id,
    parse_entity_id,
    parse_evidence_id,
    parse_legacy_claim_id,
    parse_legacy_evidence_id,
    parse_relationship_id,
)
from veda.shared.periods import Period, RequestedPeriod


def _utcnow() -> datetime:
    """Single source of truth for the packet timestamp default."""
    return datetime.now(timezone.utc)


# XBRL-derived extraction methods. When an evidence record was produced
# by one of these methods, it must carry an XBRL tag (either on the
# Evidence or as its location.source_reference). When the evidence came
# from narrative text, this requirement does not apply.
_XBRL_DERIVED_METHODS = frozenset({
    ExtractionMethod.DETERMINISTIC_FIELD_EXTRACTION,
    ExtractionMethod.DETERMINISTIC_JSON,
})


# ====================================================================
# 1. AssessmentRequest
# ====================================================================

class AssessmentRequest(BaseModel):
    """
    The input to one pipeline run.

    A request is what the caller supplies before any retrieval begins.
    It carries both the requested fiscal year as an integer (for
    direct access by the SEC pipeline) and the structured
    RequestedPeriod (for the unified period contract). The two must
    agree; the validator enforces it.

    The request's fiscal_year is the target the pipeline filters
    against. It is never used as a substitute for actual evidence
    dates: the SEC extractor derives the evidence fiscal year from
    each record's real end date and matches that against this target.
    """

    request_id: str = Field(
        ...,
        description=(
            "Identity for this request. Temporarily generated via "
            "shared.ids.assessment_id(); File 8 may later gain a "
            "dedicated request_id() generator."
        ),
    )
    company_name: str = Field(..., min_length=1, description="Raw vendor name as supplied by the caller.")
    fiscal_year: int = Field(
        ...,
        description=(
            "The requested fiscal year. Kept explicitly so the SEC "
            "extractor has a target to filter evidence against. Must "
            "equal requested_period.fiscal_year."
        ),
    )
    requested_period: RequestedPeriod = Field(
        ...,
        description="Structured form of the requested period.",
    )
    requested_claim_types: list[str] = Field(default_factory=list)
    requested_at: datetime = Field(default_factory=_utcnow)

    @field_validator("request_id")
    @classmethod
    def _request_id_parses(cls, v: str) -> str:
        if parse_assessment_id(v) is None:
            raise ValueError(f"request_id is not a valid assessment-namespace ID: {v!r}")
        return v

    @field_validator("requested_claim_types")
    @classmethod
    def _no_blank_claim_types(cls, v: list[str]) -> list[str]:
        for ct in v:
            if not ct or not ct.strip():
                raise ValueError("requested_claim_types entries must be non-empty")
        return v

    @model_validator(mode="after")
    def _fiscal_year_matches_requested_period(self) -> "AssessmentRequest":
        if self.fiscal_year != self.requested_period.fiscal_year:
            raise ValueError(
                "AssessmentRequest.fiscal_year must equal "
                "AssessmentRequest.requested_period.fiscal_year "
                f"(got {self.fiscal_year} vs {self.requested_period.fiscal_year})"
            )
        return self


# ====================================================================
# 2. ResolvedEntity
# ====================================================================

class ResolvedEntity(BaseModel):
    """
    The output of entity resolution.

    Identity is never guessed. When resolution cannot decide, the
    record says so explicitly via resolution_status and does not carry
    a CIK, an entity_id, or a canonical name.

    Invariants (enforced by a model validator):
      - If entity_id is present, it must parse as a canonical entity ID.
      - RESOLVED requires entity_id, resolved_name, and cik.
      - NOT_FOUND requires entity_id to be None.
      - AMBIGUOUS and REQUIRES_HUMAN_REVIEW must carry candidates.
    """

    input_name: str = Field(..., min_length=1)
    resolved_name: Optional[str] = None
    cik: Optional[str] = Field(default=None, description="SEC CIK. Numeric string when present.")
    entity_id: Optional[str] = Field(
        default=None,
        description="Canonical entity ID produced by shared.ids.entity_id().",
    )
    entity_type: Optional[EntityType] = None
    parent_entity: Optional[str] = None
    sec_browse_url: Optional[str] = Field(
        default=None,
        description=(
            "Click-through to SEC EDGAR's company browse page. "
            "Preserved from the personal prototype."
        ),
    )
    resolution_method: str = Field(..., min_length=1)
    resolution_status: EntityResolutionStatus
    resolution_confidence: Optional[ConfidenceLevel] = Field(
        default=None,
        description=(
            "Qualitative confidence. Never a float — the personal "
            "prototype's 0.98 was replaced with a qualitative tier."
        ),
    )
    ambiguity_notes: Optional[str] = None
    candidates: list[str] = Field(default_factory=list)

    @field_validator("cik")
    @classmethod
    def _cik_numeric_if_present(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.isdigit():
            raise ValueError(f"cik must be numeric when present, got {v!r}")
        return v

    @model_validator(mode="after")
    def _resolution_invariants(self) -> "ResolvedEntity":
        if self.entity_id is not None and parse_entity_id(self.entity_id) is None:
            raise ValueError(f"entity_id does not parse as a canonical entity ID: {self.entity_id!r}")

        if self.resolution_status == EntityResolutionStatus.RESOLVED:
            if not self.entity_id:
                raise ValueError("RESOLVED requires entity_id")
            if not self.resolved_name:
                raise ValueError("RESOLVED requires resolved_name")
            if not self.cik:
                raise ValueError("RESOLVED requires cik")

        if self.resolution_status == EntityResolutionStatus.NOT_FOUND:
            if self.entity_id is not None:
                raise ValueError("NOT_FOUND must not carry an entity_id")

        if self.resolution_status in (
            EntityResolutionStatus.AMBIGUOUS,
            EntityResolutionStatus.REQUIRES_HUMAN_REVIEW,
        ):
            if not self.candidates:
                raise ValueError(
                    f"{self.resolution_status.value} requires at least one candidate"
                )
        return self


# ====================================================================
# 3. SourceDocument
# ====================================================================

class SourceDocument(BaseModel):
    """
    Metadata for one retrieved document.

    SEC-specific fields are present but optional, because a document
    may come from a non-SEC source (USAspending, annual report, GAO,
    DoD OIG).

    content_hash is required when is_fixture is True (frozen fixtures
    must be reproducible), and optional otherwise (live retrieval may
    not produce a hash before the record is stored).
    """

    doc_id: str = Field(..., description="Canonical document ID from shared.ids.document_id().")
    source_type: SourceType
    doc_type: str = Field(..., min_length=1, description="e.g. 10k, 10q, 8k, annual_report, gao_report.")
    title: str = Field(..., min_length=1)
    url: Optional[str] = None
    publish_date: Optional[date] = None
    content_hash: Optional[str] = Field(
        default=None,
        description="sha256 hex of the frozen fixture text. Required when is_fixture=True.",
    )
    retrieved_at: datetime = Field(default_factory=_utcnow)
    is_fixture: bool = False

    # SEC-specific metadata, optional
    accession_number: Optional[str] = None
    filing_form: Optional[str] = None
    filing_date: Optional[date] = None
    fiscal_year: Optional[int] = None
    reporting_period: Optional[Period] = None

    @field_validator("doc_id")
    @classmethod
    def _doc_id_parses(cls, v: str) -> str:
        if parse_document_id(v) is None:
            raise ValueError(f"doc_id does not parse as a canonical document ID: {v!r}")
        return v

    @field_validator("content_hash")
    @classmethod
    def _content_hash_sha256(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("content_hash must be a 64-character lowercase hex sha256")
        return v

    @model_validator(mode="after")
    def _content_hash_required_for_fixtures(self) -> "SourceDocument":
        if self.is_fixture and not self.content_hash:
            raise ValueError("is_fixture=True requires content_hash")
        return self


# ====================================================================
# 4. EvidenceLocation
# ====================================================================

class EvidenceLocation(BaseModel):
    """
    Where inside a document a piece of evidence lives.

    The personal prototype contributed source_url (a direct filing
    index link) and source_reference (the XBRL tag name). Both are
    preserved here.
    """

    field_or_passage: str = Field(
        ...,
        min_length=1,
        description="XBRL tag name, exact passage text, or award ID.",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Click-through link to the exact filing page. Preserved from the personal prototype.",
    )
    source_reference: Optional[str] = Field(
        default=None,
        description="XBRL tag name from the personal prototype (e.g. 'Revenues').",
    )
    section: Optional[str] = None
    chunk_id: Optional[str] = Field(
        default=None,
        description="Canonical chunk ID, when the evidence is anchored to a chunk.",
    )
    span_start: Optional[int] = Field(default=None, ge=0)
    span_end: Optional[int] = Field(default=None, ge=0)

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_parses(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if parse_chunk_id(v) is None:
            raise ValueError(f"chunk_id does not parse as a canonical chunk ID: {v!r}")
        return v

    @model_validator(mode="after")
    def _span_order(self) -> "EvidenceLocation":
        if self.span_start is not None and self.span_end is not None:
            if self.span_end < self.span_start:
                raise ValueError("span_end must be >= span_start")
        return self


# ====================================================================
# 4b. EvidenceChunk
# ====================================================================

class EvidenceChunk(BaseModel):
    """
    One deterministic slice of a normalized source document.

    Offsets are absolute indexes into the normalized document text.
    ``text`` is exactly ``normalized[char_start:char_end]``.
    """

    chunk_id: str = Field(..., description="Canonical chunk ID from shared.ids.chunk_id().")
    doc_id: str = Field(..., description="Canonical document ID this chunk belongs to.")
    index: int = Field(..., ge=0, le=9999)
    char_start: int = Field(..., ge=0)
    char_end: int = Field(..., ge=0)
    text: str
    text_sha256: str = Field(..., description="sha256 hex of this chunk's text.")

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_parses(cls, v: str) -> str:
        if parse_chunk_id(v) is None:
            raise ValueError(f"chunk_id does not parse as a canonical chunk ID: {v!r}")
        return v

    @field_validator("doc_id")
    @classmethod
    def _doc_id_parses(cls, v: str) -> str:
        if parse_document_id(v) is None:
            raise ValueError(f"doc_id does not parse as a canonical document ID: {v!r}")
        return v

    @field_validator("text_sha256")
    @classmethod
    def _text_sha256_hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("text_sha256 must be a 64-character lowercase hex sha256")
        return v

    @model_validator(mode="after")
    def _span_matches_text(self) -> "EvidenceChunk":
        if self.char_end < self.char_start:
            raise ValueError("char_end must be >= char_start")
        if len(self.text) != self.char_end - self.char_start:
            raise ValueError("chunk text length must equal char_end - char_start")
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if digest != self.text_sha256:
            raise ValueError("text_sha256 does not match chunk text")
        parsed = parse_chunk_id(self.chunk_id)
        if parsed is not None:
            if parsed["doc_id"] != self.doc_id:
                raise ValueError("chunk_id document does not match doc_id")
            if int(parsed["index"]) != self.index:
                raise ValueError("chunk_id index does not match index")
        return self


# ====================================================================
# 5. Evidence
# ====================================================================

class Evidence(BaseModel):
    """
    One fact with full provenance.

    Every Evidence must have at least one provenance path: a document
    or a location. SEC filing evidence must have both.

    SEC-specific fields (accession_number, xbrl_tag, form,
    sec_browse_url) are carried directly on Evidence, not only on
    SourceDocument, because SEC Company Facts evidence may not always
    have a populated document object.

    SEC provenance consistency rules (all local, no provider access):
      - SEC_FILING requires accession_number, either on the Evidence
        or on Evidence.document.
      - SEC_FILING evidence produced by an XBRL-derived extraction
        method requires an XBRL tag, either on Evidence.xbrl_tag or
        as Evidence.location.source_reference.
      - Narrative-passage evidence (LLM-extracted or text-extracted)
        is not required to carry an XBRL tag, even when the evidence
        category is RECOGNIZED_REVENUE. A revenue figure disclosed
        in narrative prose is still recognized revenue; it simply is
        not XBRL-tagged.
    """

    evidence_id: str = Field(..., description="Canonical evidence ID from shared.ids.evidence_id().")
    source_type: SourceType
    source_name: str = Field(..., min_length=1)
    document: Optional[SourceDocument] = None
    location: Optional[EvidenceLocation] = None

    raw_value: Any = None
    unit: Optional[str] = None
    currency: Optional[str] = None

    entity_id: str = Field(..., description="Canonical entity ID.")
    reporting_period: Period = Field(..., description="Structured period derived from actual dates.")
    evidence_category: EvidenceCategory
    retrieval_method: ExtractionMethod
    retrieved_at: datetime = Field(default_factory=_utcnow)
    is_fixture: bool = False

    is_context_only: bool = Field(
        default=False,
        description=(
            "True when this evidence record is included in the packet as "
            "informational context and is not referenced by any claim. "
            "A record with is_context_only=False must be referenced by at "
            "least one claim; File 10 enforces this. Default False."
        ),
    )
    evidence_tier: EvidenceTier = Field(
        default=EvidenceTier.BOUNDED_OR_INDETERMINATE,
        description=(
            "Derived from source_type and evidence_category. "
            "authoritative_internal is reserved and is not produced."
        ),
    )

    # SEC-specific metadata, optional
    accession_number: Optional[str] = None
    xbrl_tag: Optional[str] = None
    form: Optional[str] = None
    sec_browse_url: Optional[str] = None

    @field_validator("evidence_id")
    @classmethod
    def _evidence_id_parses(cls, v: str) -> str:
        if parse_evidence_id(v) is None and parse_legacy_evidence_id(v) is None:
            raise ValueError(f"evidence_id is neither a canonical nor a legacy evidence ID: {v!r}")
        return v

    @field_validator("entity_id")
    @classmethod
    def _entity_id_parses(cls, v: str) -> str:
        if parse_entity_id(v) is None:
            raise ValueError(f"entity_id does not parse as a canonical entity ID: {v!r}")
        return v

    @model_validator(mode="after")
    def _requires_provenance(self) -> "Evidence":
        if self.document is None and self.location is None:
            raise ValueError("Evidence requires at least one of document or location")

        if self.source_type == SourceType.SEC_FILING:
            if self.document is None or self.location is None:
                raise ValueError("SEC_FILING evidence requires both document and location")

            acc = self.accession_number or (self.document.accession_number if self.document else None)
            if not acc:
                raise ValueError(
                    "SEC_FILING evidence requires accession_number "
                    "(on Evidence or on Evidence.document)"
                )

            if self.retrieval_method in _XBRL_DERIVED_METHODS:
                tag = self.xbrl_tag or (self.location.source_reference if self.location else None)
                if not tag:
                    raise ValueError(
                        "SEC_FILING XBRL-derived evidence requires xbrl_tag "
                        "(on Evidence or as location.source_reference)"
                    )
        tier = derive_evidence_tier(self.source_type, self.evidence_category)
        if self.evidence_tier != tier:
            object.__setattr__(self, "evidence_tier", tier)
        return self


# ====================================================================
# 6. Claim
# ====================================================================

class Claim(BaseModel):
    """
    A typed assertion that references the evidence it was derived from.

    A claim does not own evidence. It references it by ID. A claim
    whose claim_status is SUPPORTED must carry at least one
    evidence_id. A claim with no evidence can only be represented as
    INSUFFICIENT_EVIDENCE.
    """

    claim_id: str = Field(..., description="Canonical claim ID from shared.ids.claim_id().")
    claim_type: str = Field(..., min_length=1)
    value: Any = None
    unit: Optional[str] = None
    currency: Optional[str] = None

    entity_id: str = Field(..., description="Canonical entity ID.")
    reporting_period: Period
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_category: EvidenceCategory
    extraction_method: ExtractionMethod
    confidence: ConfidenceLevel
    assumptions: list[str] = Field(default_factory=list)
    claim_status: ClaimStatus
    evidence_tier: EvidenceTier = Field(
        default=EvidenceTier.BOUNDED_OR_INDETERMINATE,
        description="Copied from the evidence this claim was extracted from.",
    )

    @field_validator("claim_id")
    @classmethod
    def _claim_id_parses(cls, v: str) -> str:
        if parse_claim_id(v) is None and parse_legacy_claim_id(v) is None:
            raise ValueError(f"claim_id is neither a canonical nor a legacy claim ID: {v!r}")
        return v

    @field_validator("entity_id")
    @classmethod
    def _entity_id_parses(cls, v: str) -> str:
        if parse_entity_id(v) is None:
            raise ValueError(f"entity_id does not parse as a canonical entity ID: {v!r}")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def _each_evidence_id_parses(cls, v: list[str]) -> list[str]:
        for ev in v:
            if parse_evidence_id(ev) is None and parse_legacy_evidence_id(ev) is None:
                raise ValueError(f"{ev!r} is not a canonical or legacy evidence ID")
        return v

    @model_validator(mode="after")
    def _supported_requires_evidence(self) -> "Claim":
        if self.claim_status == ClaimStatus.SUPPORTED and not self.evidence_ids:
            raise ValueError("SUPPORTED claim must carry at least one evidence_id")
        return self


# ====================================================================
# 7. Conflict
# ====================================================================

class Conflict(BaseModel):
    """
    Two comparable claims that disagree.

    A conflict is only recorded when claims are comparable: same
    entity, same metric, same reporting period, same unit, same
    evidence category. A difference between recognized revenue and a
    procurement obligation is NEVER a conflict; they measure
    different things.

    requires_human_review is an explicit field. It is never inferred
    from comparison_result; callers must set it.
    """

    conflict_id: str = Field(..., description="Canonical conflict ID from shared.ids.conflict_id().")
    claim_type: str = Field(..., min_length=1)
    entity_id: str = Field(..., description="Canonical entity ID.")
    reporting_period: Period
    claim_ids: list[str] = Field(..., min_length=2)
    evidence_ids: list[str] = Field(default_factory=list)
    conflicting_values: list[Any] = Field(default_factory=list)
    comparison_result: ComparisonResult
    reason: str = Field(..., min_length=1)
    requires_human_review: bool = True

    @field_validator("conflict_id")
    @classmethod
    def _conflict_id_parses(cls, v: str) -> str:
        if parse_conflict_id(v) is None:
            raise ValueError(f"conflict_id does not parse as a canonical conflict ID: {v!r}")
        return v

    @field_validator("entity_id")
    @classmethod
    def _entity_id_parses(cls, v: str) -> str:
        if parse_entity_id(v) is None:
            raise ValueError(f"entity_id does not parse as a canonical entity ID: {v!r}")
        return v

    @field_validator("claim_ids")
    @classmethod
    def _claim_ids_parse(cls, v: list[str]) -> list[str]:
        for cid in v:
            if parse_claim_id(cid) is None and parse_legacy_claim_id(cid) is None:
                raise ValueError(f"{cid!r} is not a canonical or legacy claim ID")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_parse(cls, v: list[str]) -> list[str]:
        for ev in v:
            if parse_evidence_id(ev) is None and parse_legacy_evidence_id(ev) is None:
                raise ValueError(f"{ev!r} is not a canonical or legacy evidence ID")
        return v

    @model_validator(mode="after")
    def _conflict_invariants(self) -> "Conflict":
        if len(self.claim_ids) < 2:
            raise ValueError("Conflict requires at least two claim IDs")
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("Conflict claim IDs must be distinct")
        if self.comparison_result != ComparisonResult.INSUFFICIENT_CONTEXT:
            if len(self.conflicting_values) < 2:
                raise ValueError(
                    "Conflict requires at least two conflicting values "
                    "unless comparison_result is INSUFFICIENT_CONTEXT"
                )
        return self


# ====================================================================
# 8. MissingEvidence
# ====================================================================

class MissingEvidence(BaseModel):
    """
    Expected evidence that could not be found or could not be used.

    MissingEvidence records are embedded inside an Assessment and have
    no independent identity. They are not independently referenced by
    any other record.
    """

    claim_type: str = Field(..., min_length=1)
    reason: MissingEvidenceReason
    explanation: str = Field(..., min_length=1)
    entity_id: Optional[str] = None
    reporting_period: Optional[Period] = None
    sources_checked: list[str] = Field(default_factory=list)

    @field_validator("entity_id")
    @classmethod
    def _entity_id_parses(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if parse_entity_id(v) is None:
            raise ValueError(f"entity_id does not parse: {v!r}")
        return v


# ====================================================================
# 8b. Relationship
# ====================================================================

class Relationship(BaseModel):
    """
    A directed link between two canonical entities.

    A string parent name is not a relationship. ``object_id`` must be
    a canonical entity ID. ``origin`` records whether the link was
    declared by a source or inferred. ``review_state`` is reserved
    for a later human review pass.
    """

    relationship_id: str
    subject_id: str
    predicate: str = Field(..., min_length=1)
    object_id: str
    origin: str = Field(default="declared")
    relationship_status: str = Field(default="declared")
    review_state: str = Field(default="unreviewed")
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    evidence_ids: list[str] = Field(default_factory=list)
    span_ids: list[str] = Field(default_factory=list)

    @field_validator("relationship_id")
    @classmethod
    def _relationship_id_parses(cls, v: str) -> str:
        if parse_relationship_id(v) is None:
            raise ValueError(
                f"relationship_id does not parse as a canonical relationship ID: {v!r}"
            )
        return v

    @field_validator("subject_id", "object_id")
    @classmethod
    def _entity_ids_parse(cls, v: str) -> str:
        if parse_entity_id(v) is None:
            raise ValueError(f"relationship endpoint is not a canonical entity ID: {v!r}")
        return v

    @field_validator("origin")
    @classmethod
    def _origin_known(cls, v: str) -> str:
        if v not in {"declared", "inferred"}:
            raise ValueError(f"origin must be declared or inferred, got {v!r}")
        return v

    @field_validator("relationship_status")
    @classmethod
    def _status_known(cls, v: str) -> str:
        allowed = {"declared", "observed", "inferred", "unknown"}
        if v not in allowed:
            raise ValueError(f"relationship_status must be one of {sorted(allowed)}")
        return v

    @model_validator(mode="after")
    def _no_self_relationship(self) -> "Relationship":
        if self.subject_id == self.object_id:
            raise ValueError("relationship subject and object must differ")
        return self


# ====================================================================
# 9. RunMetadata
# ====================================================================

class RunMetadata(BaseModel):
    """
    Execution metadata for one pipeline run.

    run_id is distinct from Assessment.assessment_id and from
    AssessmentRequest.request_id. All three currently use the
    assessment-namespace ID generator from shared.ids; File 8 may
    later gain a dedicated request_id() or run_id() generator.
    """

    run_id: str = Field(..., description="Identity for one pipeline execution.")
    schema_version: str = Field(..., min_length=1)
    pipeline_version: str = Field(..., min_length=1)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None
    provider_modes: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    user_agent: Optional[str] = Field(
        default=None,
        description=(
            "The SEC User-Agent used for this run. Null for "
            "fixture-only runs where no SEC request was made."
        ),
    )

    @field_validator("user_agent")
    @classmethod
    def _empty_user_agent_becomes_none(cls, v):
        """Treat an empty or whitespace-only User-Agent as None."""
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        stripped = v.strip()
        return stripped if stripped else None

    @field_validator("run_id")
    @classmethod
    def _run_id_parses(cls, v: str) -> str:
        if parse_assessment_id(v) is None:
            raise ValueError(f"run_id is not a valid assessment-namespace ID: {v!r}")
        return v

    @model_validator(mode="after")
    def _time_order(self) -> "RunMetadata":
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not be before started_at")
        return self


# ====================================================================
# 10. Assessment
# ====================================================================

class Assessment(BaseModel):
    """
    The final evidence packet.

    An Assessment is a self-contained record of one pipeline decision:
    what was asked, what was found, what disagreed, what was missing,
    what requires human review, and how confident the system is in
    each part. A downstream reader can reconstruct the entire
    reasoning trail from this object alone.

    Four invariants are enforced by model validators:
      - INSUFFICIENT_EVIDENCE requires non-empty missing_evidence.
      - CONFLICTING_EVIDENCE requires non-empty conflicts.
      - REQUIRES_HUMAN_REVIEW requires at least one of:
          * an unresolved or review-required vendor status, or
          * non-empty conflicts, or
          * a non-empty human_review_reason.
      - SUPPORTED and SUPPORTED_WITH_LIMITATIONS require a Period
        with known start and end dates. INSUFFICIENT_EVIDENCE and
        REQUIRES_HUMAN_REVIEW may carry a Period with missing dates,
        because the correct output is that the period could not be
        established.
    """

    assessment_id: str = Field(..., description="Canonical assessment ID from shared.ids.assessment_id().")
    request_id: str = Field(
        ...,
        description=(
            "AssessmentRequest.request_id for the request that produced "
            "this packet. Distinct from assessment_id and "
            "run_metadata.run_id."
        ),
    )
    vendor: ResolvedEntity
    reporting_period: Period

    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    chunks: list[EvidenceChunk] = Field(
        default_factory=list,
        description=(
            "Normalized SEC filing chunks for this run. Empty when no "
            "filing document was chunked."
        ),
    )
    conflicts: list[Conflict] = Field(default_factory=list)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)

    limitations: list[str] = Field(default_factory=list)
    assessment_status: AssessmentStatus
    human_review_reason: Optional[str] = Field(
        default=None,
        description=(
            "Structured reason for REQUIRES_HUMAN_REVIEW. Machine-checkable. "
            "Never inferred from prose."
        ),
    )
    recommended_next_step: Optional[str] = None

    generated_at: datetime = Field(default_factory=_utcnow)
    run_metadata: RunMetadata

    @field_validator("assessment_id")
    @classmethod
    def _assessment_id_parses(cls, v: str) -> str:
        if parse_assessment_id(v) is None:
            raise ValueError(f"assessment_id does not parse: {v!r}")
        return v

    @field_validator("request_id")
    @classmethod
    def _request_id_parses(cls, v: str) -> str:
        if parse_assessment_id(v) is None:
            raise ValueError(f"request_id does not parse: {v!r}")
        return v

    @model_validator(mode="after")
    def _assessment_status_invariants(self) -> "Assessment":
        if self.assessment_status == AssessmentStatus.INSUFFICIENT_EVIDENCE:
            if not self.missing_evidence:
                raise ValueError("INSUFFICIENT_EVIDENCE requires non-empty missing_evidence")

        if self.assessment_status == AssessmentStatus.CONFLICTING_EVIDENCE:
            if not self.conflicts:
                raise ValueError("CONFLICTING_EVIDENCE requires non-empty conflicts")

        if self.assessment_status == AssessmentStatus.REQUIRES_HUMAN_REVIEW:
            unresolved_vendor = self.vendor.resolution_status in (
                EntityResolutionStatus.AMBIGUOUS,
                EntityResolutionStatus.NOT_FOUND,
                EntityResolutionStatus.REQUIRES_HUMAN_REVIEW,
            )
            has_conflict = bool(self.conflicts)
            has_reason = bool(self.human_review_reason)
            if not (unresolved_vendor or has_conflict or has_reason):
                raise ValueError(
                    "REQUIRES_HUMAN_REVIEW requires an unresolved entity, "
                    "a conflict, or a human_review_reason"
                )
        return self

    @model_validator(mode="after")
    def _reporting_period_meaningful(self) -> "Assessment":
        if self.assessment_status in (
            AssessmentStatus.SUPPORTED,
            AssessmentStatus.SUPPORTED_WITH_LIMITATIONS,
        ):
            if self.reporting_period.start is None or self.reporting_period.end is None:
                raise ValueError(
                    f"{self.assessment_status.value} requires a reporting_period "
                    "with known start and end dates"
                )
        return self

    def human_summary(self) -> str:
        """
        Plain-text rendering of this packet.

        Introduces no new facts. Every line maps to a field above.
        Preserved from veda; used by the CLI and dashboard.
        """
        lines: list[str] = []
        lines.append(f"Vendor: {self.vendor.input_name} -> {self.vendor.resolved_name or '(unresolved)'}")
        lines.append(f"Period: {self.reporting_period.label or '(unspecified)'}")
        lines.append(f"Assessment ID: {self.assessment_id}")
        lines.append(f"Request ID:    {self.request_id}")
        lines.append(f"Run ID:        {self.run_metadata.run_id}")
        lines.append(f"Status: {self.assessment_status.value.upper()}")
        lines.append("")

        if self.claims:
            lines.append("Claims:")
            for c in self.claims:
                evidence_str = ", ".join(c.evidence_ids) if c.evidence_ids else "none"
                value_str = f"{c.value} {c.unit or ''}".strip()
                lines.append(
                    f"  [{c.claim_status.value.upper()}] {c.claim_type} = "
                    f"{value_str or '(no numeric value)'} "
                    f"(confidence: {c.confidence.value}, evidence: {evidence_str})"
                )
            lines.append("")

        if self.conflicts:
            lines.append("Conflicts:")
            for cf in self.conflicts:
                lines.append(f"  - {cf.claim_type}: {cf.conflicting_values} ({cf.reason})")
            lines.append("")

        if self.missing_evidence:
            lines.append("Missing evidence:")
            for m in self.missing_evidence:
                lines.append(f"  - {m.claim_type} ({m.reason.value}): {m.explanation}")
            lines.append("")

        if self.limitations:
            lines.append("Limitations:")
            for lim in self.limitations:
                lines.append(f"  - {lim}")
            lines.append("")

        if self.human_review_reason:
            lines.append(f"Human review reason: {self.human_review_reason}")
            lines.append("")

        if self.recommended_next_step:
            lines.append(f"Recommended next step: {self.recommended_next_step}")

        return "\n".join(lines)


__all__ = [
    "AssessmentRequest",
    "ResolvedEntity",
    "SourceDocument",
    "EvidenceLocation",
    "Evidence",
    "Claim",
    "Conflict",
    "MissingEvidence",
    "RunMetadata",
    "Assessment",
]