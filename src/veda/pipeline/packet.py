"""
File: src/veda/pipeline/packet.py
Title: Assessment Packet Assembly
Layer: Pipeline layer
Status: Merged prototype foundation — Phase 6

Purpose
-------
Assembles the canonical Assessment packet from the outputs of every
prior stage. packet.py produces one validated Assessment object. It
never produces a dictionary that is validated later.

Public API
----------
build_packet(...) -> Assessment

Does not
--------
Does not make network calls.
Does not import veda.providers.fixtures.
Does not mutate inputs.
Does not silently swallow validation errors.

Design notes
------------
All lists are sorted deterministically before being handed to the
Assessment model:

    claims              sorted by claim_id
    evidence            sorted by evidence_id
    chunks              sorted by chunk_id
    conflicts           sorted by conflict_id
    missing_evidence    sorted by (claim_type, reason.value)
    limitations         sorted lexicographically

The Assessment model applies its model-local validators on
construction. The caller is expected to run
veda.shared.validation.validate_assessment on the result.
"""

from __future__ import annotations

from typing import Optional

from veda.shared.enums import AssessmentStatus
from veda.shared.models import (
    Assessment,
    Claim,
    Conflict,
    Evidence,
    EvidenceChunk,
    MissingEvidence,
    ResolvedEntity,
    RunMetadata,
)
from veda.shared.periods import Period


def build_packet(
    *,
    request_id: str,
    assessment_id: str,
    vendor: ResolvedEntity,
    reporting_period: Period,
    claims: list[Claim],
    evidence: list[Evidence],
    conflicts: list[Conflict],
    missing_evidence: list[MissingEvidence],
    chunks: list[EvidenceChunk] | None = None,
    assessment_status: AssessmentStatus,
    limitations: list[str],
    run_metadata: RunMetadata,
    human_review_reason: Optional[str] = None,
    recommended_next_step: Optional[str] = None,
) -> Assessment:
    """
    Assemble the final Assessment packet.

    Lists are sorted deterministically. The Assessment model is
    constructed directly (no intermediate dict). Its model-local
    validators run on construction.
    """
    sorted_claims = sorted(claims, key=lambda c: c.claim_id)
    sorted_evidence = sorted(evidence, key=lambda e: e.evidence_id)
    sorted_chunks = sorted(chunks or [], key=lambda c: c.chunk_id)
    sorted_conflicts = sorted(conflicts, key=lambda c: c.conflict_id)
    sorted_missing = sorted(
        missing_evidence,
        key=lambda m: (m.claim_type, m.reason.value),
    )
    sorted_limitations = sorted(limitations)

    return Assessment(
        assessment_id=assessment_id,
        request_id=request_id,
        vendor=vendor,
        reporting_period=reporting_period,
        claims=sorted_claims,
        evidence=sorted_evidence,
        chunks=sorted_chunks,
        conflicts=sorted_conflicts,
        missing_evidence=sorted_missing,
        limitations=sorted_limitations,
        assessment_status=assessment_status,
        human_review_reason=human_review_reason,
        recommended_next_step=recommended_next_step,
        run_metadata=run_metadata,
    )


__all__ = ["build_packet"]