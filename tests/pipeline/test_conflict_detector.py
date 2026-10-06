# filename: tests/pipeline/test_conflict_detector.py
# title: Pipeline Layer - Conflict Detector Tests
# layer: Test suite - pipeline
# status: Phase 1-6 test recovery
# description:
#     THE SAFETY-CRITICAL TEST FILE FOR THE CONFLICT DETECTOR.
#
#     The conflict detector is comparability-aware. Two claims are
#     only compared when they share ALL SIX of:
#         entity_id
#         claim_type
#         period fiscal year
#         evidence_category
#         unit
#         currency
#
#     Because evidence_category is part of the comparability key, a
#     recognized revenue claim and a procurement obligation claim
#     are NEVER compared. Their numerical difference is NEVER
#     reported as a conflict.
#
#     This is the "critical distinction" the employer named:
#     "SEC recognized revenue and USAspending obligations are
#     different measures. Preserve both; do not flag their numerical
#     difference as a contradiction."
#
# source:
#     AUTHORED - Phase 6 had no saved test before recovery began.
#     The detector in src/veda/pipeline/conflict_detector.py is the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - The eligible claim statuses are SUPPORTED and INFERRED.
#       CONFLICTING, INSUFFICIENT_EVIDENCE, and REQUIRES_HUMAN_REVIEW
#       claims are skipped.
#     - Claims whose fiscal year cannot be determined are skipped.

from __future__ import annotations

from datetime import date

from veda.pipeline.conflict_detector import detect_conflicts
from veda.shared.enums import (
    ClaimStatus,
    ComparisonResult,
    ConfidenceLevel,
    EvidenceCategory,
    ExtractionMethod,
)
from veda.shared.ids import claim_id as make_claim_id
from veda.shared.models import Claim
from veda.shared.periods import Period


ENTITY_ID = "entity:sec_edgar:vendor:0000936468"
DATED_PERIOD = Period(start=date(2024, 1, 1), end=date(2024, 12, 31), label="FY2024")


def _claim(
    *,
    claim_type: str = "total_revenue",
    value: float | None = 71043000000,
    category: EvidenceCategory = EvidenceCategory.RECOGNIZED_REVENUE,
    status: ClaimStatus = ClaimStatus.SUPPORTED,
    entity_id: str = ENTITY_ID,
    period: Period = DATED_PERIOD,
    unit: str | None = "USD",
    currency: str | None = "USD",
    evidence_suffix: str = "a",
) -> Claim:
    """
    Build one Claim for the conflict detector.

    The claim_id is content-addressed over (entity, claim_type,
    period_label, evidence_ids). To produce two distinct claims with
    the same values, pass different evidence_suffix values.
    """
    period_label = period.label or "UNKNOWN"
    ev_id = f"evidence:sec_edgar:{evidence_suffix * 16}"
    cid = make_claim_id(entity_id, claim_type, period_label, [ev_id])
    return Claim(
        claim_id=cid,
        claim_type=claim_type,
        value=value,
        unit=unit,
        currency=currency,
        entity_id=entity_id,
        reporting_period=period,
        evidence_ids=[ev_id],
        evidence_category=category,
        extraction_method=ExtractionMethod.DETERMINISTIC_FIELD_EXTRACTION,
        confidence=ConfidenceLevel.HIGH,
        claim_status=status,
    )


# ====================================================================
# 1. THE CRITICAL ASSERTION
# ====================================================================

def test_federal_fy_is_not_compared_with_company_fy() -> None:
    """Same metric and end-year still do not conflict across FFY and FY."""
    company = _claim(value=100, evidence_suffix="a")
    federal = _claim(
        value=250,
        evidence_suffix="b",
        period=Period(
            start=date(2023, 10, 1),
            end=date(2024, 9, 30),
            label="FFY2024",
        ),
    )
    assert detect_conflicts([company, federal]) == []


def test_procurement_obligation_is_not_treated_as_revenue() -> None:
    """
    The single most important test in the file.

    A revenue claim and a procurement obligation claim for the same
    entity and period, with wildly different values, must not produce
    a conflict. They measure different things.
    """
    revenue = _claim(value=4200000000, category=EvidenceCategory.RECOGNIZED_REVENUE, evidence_suffix="a")
    obligation = _claim(
        value=180000000,
        claim_type="procurement_obligation",
        category=EvidenceCategory.PROCUREMENT_OBLIGATION,
        evidence_suffix="b",
    )
    conflicts = detect_conflicts([revenue, obligation])
    assert conflicts == []


def test_obligation_and_revenue_same_value_still_no_conflict() -> None:
    """
    Even if the numbers happen to be equal, they are different
    measures and must not be compared.
    """
    revenue = _claim(value=180000000, category=EvidenceCategory.RECOGNIZED_REVENUE, evidence_suffix="a")
    obligation = _claim(
        value=180000000,
        claim_type="procurement_obligation",
        category=EvidenceCategory.PROCUREMENT_OBLIGATION,
        evidence_suffix="b",
    )
    conflicts = detect_conflicts([revenue, obligation])
    assert conflicts == []


# ====================================================================
# 2. Same-metric conflict IS detected
# ====================================================================

def test_two_revenue_claims_with_different_values_conflict() -> None:
    """Two RECOGNIZED_REVENUE claims for the same entity/period must conflict."""
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1


def test_conflict_result_is_conflicts() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts[0].comparison_result == ComparisonResult.CONFLICTS


def test_conflict_preserves_both_values() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert sorted(conflicts[0].conflicting_values) == [4000000000, 4200000000]


def test_conflict_references_both_claims() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert set(conflicts[0].claim_ids) == {a.claim_id, b.claim_id}


def test_conflict_requires_human_review() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts[0].requires_human_review is True


# ====================================================================
# 3. Equal values do not conflict
# ====================================================================

def test_same_value_same_category_no_conflict() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4200000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 4. Different entity no conflict
# ====================================================================

def test_different_entity_no_conflict() -> None:
    other = "entity:sec_edgar:vendor:0000320193"
    a = _claim(value=4200000000, entity_id=ENTITY_ID, evidence_suffix="a")
    b = _claim(value=4000000000, entity_id=other, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 5. Different period no conflict
# ====================================================================

def test_different_period_no_conflict() -> None:
    """FY2023 and FY2024 claims are not comparable."""
    prev = Period(start=date(2023, 1, 1), end=date(2023, 12, 31), label="FY2023")
    a = _claim(value=4200000000, period=DATED_PERIOD, evidence_suffix="a")
    b = _claim(value=4000000000, period=prev, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


def test_undated_period_claims_are_skipped() -> None:
    """A claim whose fiscal year cannot be determined is skipped."""
    undated = Period(label="FY2024")
    a = _claim(value=4200000000, period=undated, evidence_suffix="a")
    b = _claim(value=4000000000, period=undated, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 6. Different unit or currency no conflict
# ====================================================================

def test_different_unit_no_conflict() -> None:
    a = _claim(value=4200000000, unit="USD", evidence_suffix="a")
    b = _claim(value=4000000000, unit="EUR", evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


def test_different_currency_no_conflict() -> None:
    a = _claim(value=4200000000, currency="USD", evidence_suffix="a")
    b = _claim(value=4000000000, currency="EUR", evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 7. Different claim type no conflict
# ====================================================================

def test_different_claim_type_no_conflict() -> None:
    a = _claim(claim_type="total_revenue", value=4200000000, evidence_suffix="a")
    b = _claim(claim_type="government_exposure", value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 8. Missing values are skipped
# ====================================================================

def test_claim_with_none_value_is_skipped() -> None:
    a = _claim(value=None, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


def test_both_none_values_no_conflict() -> None:
    a = _claim(value=None, evidence_suffix="a")
    b = _claim(value=None, evidence_suffix="b")
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 9. Ineligible claim statuses are skipped
# ====================================================================

def test_insufficient_evidence_claims_are_skipped() -> None:
    a = _claim(
        value=4200000000,
        status=ClaimStatus.INSUFFICIENT_EVIDENCE,
        evidence_suffix="a",
    )
    b = _claim(
        value=4000000000,
        status=ClaimStatus.INSUFFICIENT_EVIDENCE,
        evidence_suffix="b",
    )
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


def test_conflicting_claims_are_skipped() -> None:
    """A claim already marked CONFLICTING is not re-evaluated."""
    a = _claim(
        value=4200000000,
        status=ClaimStatus.CONFLICTING,
        evidence_suffix="a",
    )
    b = _claim(
        value=4000000000,
        status=ClaimStatus.CONFLICTING,
        evidence_suffix="b",
    )
    conflicts = detect_conflicts([a, b])
    assert conflicts == []


# ====================================================================
# 10. Determinism
# ====================================================================

def test_conflict_id_is_order_independent() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    conflicts_ab = detect_conflicts([a, b])
    conflicts_ba = detect_conflicts([b, a])
    assert conflicts_ab[0].conflict_id == conflicts_ba[0].conflict_id


def test_detect_conflicts_sorted_by_id() -> None:
    a = _claim(value=4200000000, evidence_suffix="a")
    b = _claim(value=4000000000, evidence_suffix="b")
    c = _claim(value=3000000000, evidence_suffix="c")
    conflicts = detect_conflicts([a, b, c])
    ids = [cf.conflict_id for cf in conflicts]
    assert ids == sorted(ids)


# ====================================================================
# 11. Empty input
# ====================================================================

def test_empty_input_returns_empty_list() -> None:
    assert detect_conflicts([]) == []


def test_single_claim_no_conflict() -> None:
    a = _claim()
    assert detect_conflicts([a]) == []


# ====================================================================
# 12. Revenue vs obligation with different entity AND category
# ====================================================================

def test_multi_dimensional_incompatibility() -> None:
    """
    Claims differing on both category and entity must not conflict.
    This exercises the full comparability key: any one mismatch
    is sufficient to prevent comparison.
    """
    other = "entity:sec_edgar:vendor:0000320193"
    revenue = _claim(
        value=4200000000,
        entity_id=ENTITY_ID,
        category=EvidenceCategory.RECOGNIZED_REVENUE,
        evidence_suffix="a",
    )
    obligation = _claim(
        value=180000000,
        entity_id=other,
        claim_type="procurement_obligation",
        category=EvidenceCategory.PROCUREMENT_OBLIGATION,
        evidence_suffix="b",
    )
    conflicts = detect_conflicts([revenue, obligation])
    assert conflicts == []