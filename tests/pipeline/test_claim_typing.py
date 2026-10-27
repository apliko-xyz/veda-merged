# filename: tests/pipeline/test_claim_typing.py
# title: Pipeline Layer - Claim Type Classification Tests
# layer: Test suite - pipeline
# status: Phase 1-6 test recovery
# description:
#     Verifies the frozen mapping table in claim_typing.py: each
#     EvidenceCategory maps to exactly one (claim_type, confidence)
#     pair. This mapping is the single source of truth for what claim
#     type a piece of evidence produces.
#
# source:
#     AUTHORED - Phase 6 had no saved test before recovery began.
#     The mapping in src/veda/pipeline/claim_typing.py is the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - The mapping is a frozen dict. Adding a new category requires
#       updating claim_typing.py and this test file in lockstep.
#     - Unknown categories return None; callers skip the evidence.

from __future__ import annotations

from datetime import date

from veda.pipeline.claim_typing import classify_evidence
from veda.shared.enums import ConfidenceLevel, EvidenceCategory, ExtractionMethod, SourceType
from veda.shared.models import Evidence, EvidenceLocation
from veda.shared.periods import Period


ENTITY_ID = "entity:sec_edgar:vendor:0000936468"


def _evidence(category: EvidenceCategory) -> Evidence:
    return Evidence(
        evidence_id=f"evidence:sec_edgar:{'a' * 16}",
        source_type=SourceType.SEC_COMPANY_FACTS,
        source_name="test",
        location=EvidenceLocation(field_or_passage="test"),
        entity_id=ENTITY_ID,
        reporting_period=Period(start=date(2024, 1, 1), end=date(2024, 12, 31)),
        evidence_category=category,
        retrieval_method=ExtractionMethod.DETERMINISTIC_FIELD_EXTRACTION,
    )


# ====================================================================
# 1. Every category produces a mapping
# ====================================================================

def test_recognized_revenue_maps_to_total_revenue_high() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.RECOGNIZED_REVENUE))
    assert result == ("total_revenue", ConfidenceLevel.HIGH)


def test_procurement_obligation_maps_to_procurement_obligation_high() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.PROCUREMENT_OBLIGATION))
    assert result == ("procurement_obligation", ConfidenceLevel.HIGH)


def test_procurement_award_has_no_mapping() -> None:
    """PROCUREMENT_AWARD is removed from the mapping (category-safety fix for Repair 4)."""
    result = classify_evidence(_evidence(EvidenceCategory.PROCUREMENT_AWARD))
    assert result is None


def test_corporate_relationship_maps_to_corporate_relationship_medium() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.CORPORATE_RELATIONSHIP))
    assert result == ("corporate_relationship", ConfidenceLevel.MEDIUM)


def test_customer_concentration_maps_to_customer_concentration_medium() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.CUSTOMER_CONCENTRATION))
    assert result == ("customer_concentration", ConfidenceLevel.MEDIUM)


def test_government_exposure_maps_to_government_exposure_medium() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.GOVERNMENT_EXPOSURE))
    assert result == ("government_exposure", ConfidenceLevel.MEDIUM)


def test_estimate_maps_to_total_revenue_low() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.ESTIMATE))
    assert result == ("total_revenue", ConfidenceLevel.LOW)


def test_proxy_maps_to_total_revenue_low() -> None:
    result = classify_evidence(_evidence(EvidenceCategory.PROXY))
    assert result == ("total_revenue", ConfidenceLevel.LOW)


# ====================================================================
# 2. Every enum value is mapped (no silent gaps)
# ====================================================================

def test_every_evidence_category_has_a_mapping() -> None:
    """Every EvidenceCategory value must produce a mapping (except PROCUREMENT_AWARD which is unmapped per Repair 4)."""
    for category in EvidenceCategory:
        result = classify_evidence(_evidence(category))
        if category == EvidenceCategory.PROCUREMENT_AWARD:
            assert result is None, f"{category} should have no mapping"
        else:
            assert result is not None, f"{category} has no mapping"


# ====================================================================
# 3. Revenue vs procurement are distinct
# ====================================================================

def test_recognized_revenue_and_procurement_obligation_distinct() -> None:
    """
    The mapping must never collapse revenue and obligation into the
    same claim type.
    """
    revenue = classify_evidence(_evidence(EvidenceCategory.RECOGNIZED_REVENUE))
    obligation = classify_evidence(_evidence(EvidenceCategory.PROCUREMENT_OBLIGATION))
    assert revenue is not None
    assert obligation is not None
    assert revenue[0] != obligation[0]