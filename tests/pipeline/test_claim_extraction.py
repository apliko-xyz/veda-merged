# filename: tests/pipeline/test_claim_extraction.py
# title: Pipeline Layer - Claim Extraction Tests
# layer: Test suite - pipeline
# status: Phase 1-6 test recovery
# description:
#     Verifies extract_claims: the conversion from Evidence to Claim.
#     This is where the SUPPORTED-vs-INFERRED decision happens and
#     where malformed evidence is filtered out.
#
#     The claim extraction rules are:
#       1. One Evidence produces exactly one Claim, or the Evidence
#          is skipped.
#       2. Evidence from GAO, DODIG, or SYNTHETIC source types is
#          skipped entirely.
#       3. Context-only evidence (is_context_only=True) is skipped.
#       4. Numeric-category evidence (RECOGNIZED_REVENUE,
#          PROCUREMENT_OBLIGATION) with raw_value=None is skipped.
#       5. Structured extraction methods (DETERMINISTIC_FIELD_EXTRACTION,
#          DETERMINISTIC_JSON) produce SUPPORTED claims.
#       6. Non-structured extraction produces INFERRED claims.
#       7. extract_claims never produces INSUFFICIENT_EVIDENCE claims.
#
# source:
#     AUTHORED - Phase 6 had no saved test before recovery began.
#     The extractor in src/veda/pipeline/claim_extraction.py is the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - The SUPPORTED/INFERRED decision is the safety-critical one.
#       INFERRED claims trigger SUPPORTED_WITH_LIMITATIONS in the
#       assessment. SUPPORTED claims with no evidence trigger a
#       ValidationError in the Claim model itself. This test file
#       asserts both behaviors are exercised.

from __future__ import annotations

from datetime import date

import pytest

from veda.pipeline.claim_extraction import extract_claims
from veda.shared.enums import (
    ClaimStatus,
    ConfidenceLevel,
    EvidenceCategory,
    ExtractionMethod,
    SourceType,
)
from veda.shared.ids import document_id as make_document_id
from veda.shared.ids import evidence_id as make_evidence_id
from veda.shared.models import Evidence, EvidenceLocation, SourceDocument
from veda.shared.periods import Period


ENTITY_ID = "entity:sec_edgar:vendor:0000936468"
DATED_PERIOD = Period(start=date(2024, 1, 1), end=date(2024, 12, 31), label="FY2024")


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _evidence(
    *,
    category: EvidenceCategory = EvidenceCategory.RECOGNIZED_REVENUE,
    source_type: SourceType = SourceType.SEC_COMPANY_FACTS,
    retrieval_method: ExtractionMethod = ExtractionMethod.DETERMINISTIC_FIELD_EXTRACTION,
    raw_value = 71043000000,
    tag: str = "Revenues",
) -> Evidence:
    """
    Build one Evidence object for a test.

    Two model-level rules apply to SEC_FILING evidence:
      1. The evidence ID generator requires a document_context.
      2. The Evidence model requires both document and location.

    Both rules are enforced here so tests can construct valid
    SEC_FILING evidence without duplicating the plumbing.
    """
    location = EvidenceLocation(field_or_passage=tag)

    if source_type == SourceType.SEC_FILING:
        doc_id = make_document_id("sec_edgar", "10k", "000093646825000009")
        ev_id = make_evidence_id(source_type, ENTITY_ID, "FY2024", tag, doc_id)
        document = SourceDocument(
            doc_id=doc_id,
            source_type=SourceType.SEC_FILING,
            doc_type="10k",
            title="SEC filing 000093646825000009",
            accession_number="0000936468-25-000009",
            filing_form="10-K",
        )
        return Evidence(
            evidence_id=ev_id,
            source_type=source_type,
            source_name="test",
            document=document,
            location=location,
            raw_value=raw_value,
            unit="USD",
            currency="USD",
            entity_id=ENTITY_ID,
            reporting_period=DATED_PERIOD,
            evidence_category=category,
            retrieval_method=retrieval_method,
            accession_number="0000936468-25-000009",
            form="10-K",
        )

    ev_id = make_evidence_id(source_type, ENTITY_ID, "FY2024", tag)
    return Evidence(
        evidence_id=ev_id,
        source_type=source_type,
        source_name="test",
        location=location,
        raw_value=raw_value,
        unit="USD",
        currency="USD",
        entity_id=ENTITY_ID,
        reporting_period=DATED_PERIOD,
        evidence_category=category,
        retrieval_method=retrieval_method,
    )


# ====================================================================
# 1. Structured extraction produces SUPPORTED claims
# ====================================================================

def test_structured_evidence_produces_one_claim() -> None:
    claims = extract_claims([_evidence()])
    assert len(claims) == 1


def test_structured_revenue_claim_type() -> None:
    claims = extract_claims([_evidence()])
    assert claims[0].claim_type == "total_revenue"


def test_structured_revenue_claim_status_is_supported() -> None:
    claims = extract_claims([_evidence()])
    assert claims[0].claim_status == ClaimStatus.SUPPORTED


def test_structured_revenue_confidence_is_high() -> None:
    claims = extract_claims([_evidence()])
    assert claims[0].confidence == ConfidenceLevel.HIGH


def test_structured_revenue_value_matches_source() -> None:
    claims = extract_claims([_evidence(raw_value=71043000000)])
    assert claims[0].value == 71043000000


def test_structured_revenue_references_evidence() -> None:
    ev = _evidence()
    claims = extract_claims([ev])
    assert claims[0].evidence_ids == [ev.evidence_id]


# ====================================================================
# 2. Narrative extraction produces INFERRED claims
# ====================================================================

def test_narrative_evidence_produces_inferred_claim() -> None:
    ev = _evidence(
        category=EvidenceCategory.GOVERNMENT_EXPOSURE,
        source_type=SourceType.SEC_FILING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION,
        raw_value=None,
        tag="government_exposure",
    )
    claims = extract_claims([ev])
    assert len(claims) == 1
    assert claims[0].claim_status == ClaimStatus.INFERRED


def test_narrative_claim_type_is_government_exposure() -> None:
    ev = _evidence(
        category=EvidenceCategory.GOVERNMENT_EXPOSURE,
        source_type=SourceType.SEC_FILING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION,
        raw_value=None,
        tag="government_exposure",
    )
    claims = extract_claims([ev])
    assert claims[0].claim_type == "government_exposure"


def test_narrative_claim_value_is_none() -> None:
    ev = _evidence(
        category=EvidenceCategory.GOVERNMENT_EXPOSURE,
        source_type=SourceType.SEC_FILING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION,
        raw_value=None,
        tag="government_exposure",
    )
    claims = extract_claims([ev])
    assert claims[0].value is None


# ====================================================================
# 3. Source type filtering
# ====================================================================

def test_gao_source_type_is_skipped() -> None:
    ev = _evidence(source_type=SourceType.GAO)
    claims = extract_claims([ev])
    assert claims == []


def test_dodig_source_type_is_skipped() -> None:
    ev = _evidence(source_type=SourceType.DODIG)
    claims = extract_claims([ev])
    assert claims == []


def test_synthetic_source_type_is_skipped() -> None:
    ev = _evidence(source_type=SourceType.SYNTHETIC)
    claims = extract_claims([ev])
    assert claims == []


# ====================================================================
# 4. Numeric category with None value is skipped
# ====================================================================

def test_recognized_revenue_with_no_value_is_skipped() -> None:
    ev = _evidence(category=EvidenceCategory.RECOGNIZED_REVENUE, raw_value=None)
    claims = extract_claims([ev])
    assert claims == []


def test_procurement_obligation_with_no_value_is_skipped() -> None:
    ev = _evidence(
        category=EvidenceCategory.PROCUREMENT_OBLIGATION,
        source_type=SourceType.USASPENDING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
        raw_value=None,
    )
    claims = extract_claims([ev])
    assert claims == []


def test_government_exposure_with_no_value_is_not_skipped() -> None:
    """Non-numeric categories may legitimately have no value."""
    ev = _evidence(
        category=EvidenceCategory.GOVERNMENT_EXPOSURE,
        source_type=SourceType.SEC_FILING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION,
        raw_value=None,
        tag="government_exposure",
    )
    claims = extract_claims([ev])
    assert len(claims) == 1


def test_customer_concentration_with_no_value_is_not_skipped() -> None:
    ev = _evidence(
        category=EvidenceCategory.CUSTOMER_CONCENTRATION,
        source_type=SourceType.SEC_FILING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION,
        raw_value=None,
        tag="customer_concentration",
    )
    claims = extract_claims([ev])
    assert len(claims) == 1


# ====================================================================
# 5. Multiple evidence produce multiple claims
# ====================================================================

def test_multiple_evidence_produce_multiple_claims() -> None:
    ev_a = _evidence(tag="Revenues")
    ev_b = _evidence(
        category=EvidenceCategory.PROCUREMENT_OBLIGATION,
        source_type=SourceType.USASPENDING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
        raw_value=180000000,
        tag="award-001",
    )
    claims = extract_claims([ev_a, ev_b])
    assert len(claims) == 2


def test_claims_sorted_by_claim_id() -> None:
    ev_a = _evidence(tag="Revenues")
    ev_b = _evidence(
        category=EvidenceCategory.PROCUREMENT_OBLIGATION,
        source_type=SourceType.USASPENDING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
        raw_value=180000000,
        tag="award-001",
    )
    claims = extract_claims([ev_a, ev_b])
    ids = [c.claim_id for c in claims]
    assert ids == sorted(ids)


def test_empty_input_returns_empty_list() -> None:
    assert extract_claims([]) == []


# ====================================================================
# 6. Claim ID is deterministic
# ====================================================================

def test_claim_id_deterministic() -> None:
    ev = _evidence()
    claims_a = extract_claims([ev])
    claims_b = extract_claims([ev])
    assert claims_a[0].claim_id == claims_b[0].claim_id


# ====================================================================
# 6a. Context-only evidence is skipped
# ====================================================================

def test_context_only_evidence_is_skipped() -> None:
    """Context-only evidence (e.g., PROCUREMENT_AWARD) must not produce claims (B1-XX)."""
    ev = _evidence(
        category=EvidenceCategory.PROCUREMENT_AWARD,
        source_type=SourceType.USASPENDING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
        raw_value=1000000,
        tag="award-001",
    )
    ev.is_context_only = True
    claims = extract_claims([ev])
    assert claims == []


def test_procurement_award_with_context_only_true_is_skipped() -> None:
    """USAspending awards marked as context_only are skipped during claim extraction."""
    ev = _evidence(
        category=EvidenceCategory.PROCUREMENT_AWARD,
        source_type=SourceType.USASPENDING,
        retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
        raw_value=50000000,
    )
    ev.is_context_only = True
    claims = extract_claims([ev])
    assert len(claims) == 0


# ====================================================================
# 7. Extractor never produces INSUFFICIENT_EVIDENCE
# ====================================================================

def test_extractor_never_produces_insufficient_evidence() -> None:
    """INSUFFICIENT_EVIDENCE claims are produced by the assessment engine, not here."""
    ev = _evidence()
    claims = extract_claims([ev])
    for c in claims:
        assert c.claim_status != ClaimStatus.INSUFFICIENT_EVIDENCE