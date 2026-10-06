# filename: tests/shared/test_evidence_tier.py
# title: Evidence tier mapping
# layer: Test suite - shared

from __future__ import annotations

from datetime import date

from veda.pipeline.claim_extraction import extract_claims
from veda.shared.enums import (
    EvidenceCategory,
    EvidenceTier,
    ExtractionMethod,
    SourceType,
    derive_evidence_tier,
)
from veda.shared.ids import evidence_id as make_evidence_id
from veda.shared.models import Evidence, EvidenceLocation
from veda.shared.periods import Period


ENTITY_ID = "entity:sec_edgar:vendor:0000936468"
PERIOD = Period(start=date(2024, 1, 1), end=date(2024, 12, 31), label="FY2024")


def _evidence(source_type: SourceType, category: EvidenceCategory, tag: str) -> Evidence:
    return Evidence(
        evidence_id=make_evidence_id(source_type, ENTITY_ID, "FY2024", tag),
        source_type=source_type,
        source_name="test",
        location=EvidenceLocation(field_or_passage=tag, source_reference=tag),
        raw_value=10,
        unit="USD",
        currency="USD",
        entity_id=ENTITY_ID,
        reporting_period=PERIOD,
        evidence_category=category,
        retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
        xbrl_tag=tag if source_type == SourceType.SEC_COMPANY_FACTS else None,
    )


def test_tier_mapping() -> None:
    assert derive_evidence_tier(
        SourceType.SEC_COMPANY_FACTS,
        EvidenceCategory.RECOGNIZED_REVENUE,
    ) == EvidenceTier.AUTHORITATIVE_CONSOLIDATED
    sec = _evidence(
        SourceType.SEC_COMPANY_FACTS,
        EvidenceCategory.RECOGNIZED_REVENUE,
        "Revenues",
    )
    assert sec.evidence_tier == EvidenceTier.AUTHORITATIVE_CONSOLIDATED
    award = _evidence(
        SourceType.USASPENDING,
        EvidenceCategory.PROCUREMENT_AWARD,
        "award:1",
    )
    assert award.evidence_tier == EvidenceTier.PROCUREMENT_PROXY
    narrative = _evidence(
        SourceType.ANNUAL_REPORT,
        EvidenceCategory.GOVERNMENT_EXPOSURE,
        "government_exposure",
    )
    assert narrative.evidence_tier == EvidenceTier.BOUNDED_OR_INDETERMINATE
    assert EvidenceTier.AUTHORITATIVE_INTERNAL not in {
        sec.evidence_tier,
        award.evidence_tier,
        narrative.evidence_tier,
    }
    claims = extract_claims([sec])
    assert claims[0].evidence_tier == EvidenceTier.AUTHORITATIVE_CONSOLIDATED
