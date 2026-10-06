"""
File: src/veda/pipeline/claim_extraction.py
Title: Evidence to Claim Extraction
Layer: Pipeline layer
Status: Merged prototype foundation — Phase 6

Purpose
-------
Converts a list of Evidence objects into a list of typed Claim
objects. Exactly one Evidence produces exactly one Claim, or the
Evidence is ignored. Claim extraction never produces an
INSUFFICIENT_EVIDENCE claim; that state is produced by the
assessment engine.

Public API
----------
extract_claims(evidences) -> list[Claim]

Does not
--------
Does not make network calls.
Does not import veda.providers.fixtures.
Does not produce INSUFFICIENT_EVIDENCE claims.
Does not mutate the input Evidence list.

Design notes
------------
- A structured numeric evidence record (RECOGNIZED_REVENUE or
  PROCUREMENT_OBLIGATION) with raw_value=None is ignored. No claim
  is produced.
- The claim's value, unit, and currency are copied directly from the
  Evidence. No transformation.
- claim_status is SUPPORTED for structured extraction and INFERRED
  for text extraction.
- assumptions is always [] in Phase 6.
"""

from __future__ import annotations

from veda.pipeline.claim_typing import classify_evidence
from veda.pipeline.extraction_protocol import ClaimExtractor
from veda.shared.enums import (
    ClaimStatus,
    EvidenceCategory,
    ExtractionMethod,
    SourceType,
)
from veda.shared.ids import claim_id as make_claim_id
from veda.shared.models import Claim, Evidence


VERSION = "1"

_STRUCTURED_METHODS = frozenset({
    ExtractionMethod.DETERMINISTIC_FIELD_EXTRACTION,
    ExtractionMethod.DETERMINISTIC_JSON,
})

_SKIPPED_SOURCE_TYPES = frozenset({
    SourceType.GAO,
    SourceType.DODIG,
    SourceType.SYNTHETIC,
})

# Evidence categories whose claim must carry a numeric value.
# If the value is missing, the evidence cannot produce a claim.
_NUMERIC_CATEGORIES = frozenset({
    EvidenceCategory.RECOGNIZED_REVENUE,
    EvidenceCategory.PROCUREMENT_OBLIGATION,
})


def _should_ignore(evidence: Evidence) -> bool:
    """
    Return True when the evidence must not produce a claim.

    Rules:
        - source_type in {GAO, DODIG, SYNTHETIC} -> ignore
        - numeric category with raw_value=None -> ignore
    """
    if evidence.source_type in _SKIPPED_SOURCE_TYPES:
        return True

    # Context-only records are informational. They must not become
    # claims. Lifetime USAspending award amounts use this flag because
    # they are not company-fiscal-year obligations.
    if evidence.is_context_only:
        return True

    if (
        evidence.raw_value is None
        and evidence.evidence_category in _NUMERIC_CATEGORIES
    ):
        return True

    return False


def _claim_status_for(evidence: Evidence) -> ClaimStatus:
    """Return SUPPORTED for structured extraction, INFERRED otherwise."""
    if evidence.retrieval_method in _STRUCTURED_METHODS:
        return ClaimStatus.SUPPORTED
    return ClaimStatus.INFERRED


def extract_claims(evidences: list[Evidence]) -> list[Claim]:
    """
    Convert Evidence objects into Claim objects.

    Returns a list of claims ordered by claim_id ascending. Never
    raises for individual evidence objects; evidence that cannot
    produce a claim is skipped.
    """
    claims: list[Claim] = []

    for evidence in evidences:
        if _should_ignore(evidence):
            continue

        classification = classify_evidence(evidence)
        if classification is None:
            continue

        claim_type, confidence = classification

        fiscal_year = evidence.reporting_period.fiscal_year()
        period_label = (
            f"FY{fiscal_year}" if fiscal_year is not None else "UNKNOWN"
        )

        cid = make_claim_id(
            evidence.entity_id,
            claim_type,
            period_label,
            [evidence.evidence_id],
        )

        claim = Claim(
            claim_id=cid,
            claim_type=claim_type,
            value=evidence.raw_value,
            unit=evidence.unit,
            currency=evidence.currency,
            entity_id=evidence.entity_id,
            reporting_period=evidence.reporting_period,
            evidence_ids=[evidence.evidence_id],
            evidence_category=evidence.evidence_category,
            extraction_method=evidence.retrieval_method,
            confidence=confidence,
            assumptions=[],
            claim_status=_claim_status_for(evidence),
            evidence_tier=evidence.evidence_tier,
        )
        claims.append(claim)

    claims.sort(key=lambda c: c.claim_id)
    return claims


class RuleBasedClaimExtractor(ClaimExtractor):
    """
    The deterministic claim extractor.

    Wraps the module-level extract_claims() function unchanged so
    the pipeline's default behavior is identical to Phase 6.

    This is the only extractor constructed by the default
    configuration. It imports no model SDK, reads no API key, and
    makes no network calls.
    """

    @property
    def extraction_method_name(self) -> str:
        return "rule_based"

    @property
    def is_llm_backed(self) -> bool:
        return False

    def extract(self, evidence: list[Evidence]) -> list[Claim]:
        """Delegate to the module-level extract_claims()."""
        return extract_claims(evidence)


__all__ = ["extract_claims", "RuleBasedClaimExtractor"]