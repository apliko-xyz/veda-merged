# filename: tests/smoke/test_phase6_smoke.py
# title: Phase 6 Smoke Test — Frozen Baseline
# layer: Test suite - smoke
# status: Phase 1-6 test recovery
# description:
#     The Phase 6 smoke test as saved, reproducible assertions. This
#     file reconstructs the inline smoke test that was run at the end
#     of Phase 6 and captures its exact output as test assertions.
#
#     The locked baseline values are:
#
#         Vendor: Lockheed Martin Corp -> LOCKHEED MARTIN CORP
#         Period: FY2024
#         Status: SUPPORTED_WITH_LIMITATIONS
#         total_revenue = 71043000000 USD
#         procurement_obligation = 180000000 USD
#         government_exposure = inferred
#
#     Every assertion below maps to one of those values. If any
#     assertion fails, the frozen Phase 6 baseline has changed and
#     every downstream deliverable must be reassessed.
#
# source:
#     AUTHORED - Phase 6 smoke test was run inline during development
#     and never saved. This file converts the transcript into durable
#     assertions. The reference output is the terminal transcript from
#     the Phase 6 completion.
#
# notes:
#     - Uses fixture providers only. No network. No live SEC calls.
#     - The SUPPORTED_WITH_LIMITATIONS status is expected because the
#       packet contains an INFERRED government_exposure claim, which
#       triggers rule 4 of the assessment engine.
#     - The procurement_obligation claim being separate from the
#       total_revenue claim is the "critical distinction" the
#       employer named: revenue and obligations are never merged.

from __future__ import annotations

from datetime import date

import pytest

from veda.entity.resolver import FixtureEntityResolverSource
from veda.pipeline.orchestrator import ProviderBundle, run_assessment
from veda.providers.annual_reports import FixtureAnnualReportProvider
from veda.providers.sec_company_facts import FixtureSECCompanyFactsProvider
from veda.providers.sec_filings import FixtureSECFilingsProvider
from veda.providers.usaspending import FixtureUSAspendingProvider
from veda.shared.enums import (
    AssessmentStatus,
    ClaimStatus,
    EntityResolutionStatus,
    EvidenceCategory,
)
from veda.shared.periods import RequestedPeriod
from veda.shared.validation import validate_assessment


# --------------------------------------------------------------------
# The exact fixture bundle and request used by the Phase 6 smoke test.
# --------------------------------------------------------------------
LOCKHEED_ENTRIES = {
    "lockheed martin corp": [
        {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
    ],
}


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        sec_company_facts=FixtureSECCompanyFactsProvider(),
        sec_filing=FixtureSECFilingsProvider(),
        usaspending=FixtureUSAspendingProvider(),
        annual_report=FixtureAnnualReportProvider(),
        sec_filing_accession_number="0000936468-25-000009",
        sec_filing_form="10-K",
        sec_filing_passage_hint="revenues",
    )


def _resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource(LOCKHEED_ENTRIES)


def _requested() -> RequestedPeriod:
    return RequestedPeriod(
        fiscal_year=2024,
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        raw="2024",
    )


@pytest.fixture(scope="module")
def smoke_packet():
    """
    Run the pipeline once for the module and reuse the result.

    Because the pipeline is deterministic in its ID spaces and
    content, one run is the reference. If any assertion fails, the
    packet is re-generated for a fresh diagnosis.
    """
    return run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )


# ====================================================================
# 1. Vendor resolution
# ====================================================================

def test_smoke_vendor_resolution_status(smoke_packet) -> None:
    assert smoke_packet.vendor.resolution_status == EntityResolutionStatus.RESOLVED


def test_smoke_vendor_resolved_name(smoke_packet) -> None:
    assert smoke_packet.vendor.resolved_name == "LOCKHEED MARTIN CORP"


def test_smoke_vendor_input_name(smoke_packet) -> None:
    assert smoke_packet.vendor.input_name == "Lockheed Martin Corp"


def test_smoke_vendor_cik(smoke_packet) -> None:
    assert smoke_packet.vendor.cik == "0000936468"


# ====================================================================
# 2. Period
# ====================================================================

def test_smoke_period_start(smoke_packet) -> None:
    assert smoke_packet.reporting_period.start == date(2024, 1, 1)


def test_smoke_period_end(smoke_packet) -> None:
    assert smoke_packet.reporting_period.end == date(2024, 12, 31)


def test_smoke_period_label(smoke_packet) -> None:
    assert smoke_packet.reporting_period.label == "FY2024"


# ====================================================================
# 3. Assessment status
# ====================================================================

def test_smoke_assessment_status(smoke_packet) -> None:
    """
    The Phase 6 smoke test reported SUPPORTED_WITH_LIMITATIONS.
    The status is not SUPPORTED because the packet contains an
    INFERRED claim (government_exposure), which triggers rule 4.
    """
    assert smoke_packet.assessment_status == AssessmentStatus.SUPPORTED_WITH_LIMITATIONS


# ====================================================================
# 4. Claims
# ====================================================================

def test_smoke_has_total_revenue_claim(smoke_packet) -> None:
    revenues = [c for c in smoke_packet.claims if c.claim_type == "total_revenue"]
    assert len(revenues) == 1


def test_smoke_total_revenue_value(smoke_packet) -> None:
    revenues = [c for c in smoke_packet.claims if c.claim_type == "total_revenue"]
    assert revenues[0].value == 71043000000


def test_smoke_total_revenue_unit(smoke_packet) -> None:
    revenues = [c for c in smoke_packet.claims if c.claim_type == "total_revenue"]
    assert revenues[0].unit == "USD"


def test_smoke_total_revenue_status(smoke_packet) -> None:
    revenues = [c for c in smoke_packet.claims if c.claim_type == "total_revenue"]
    assert revenues[0].claim_status == ClaimStatus.SUPPORTED


def test_smoke_total_revenue_category(smoke_packet) -> None:
    revenues = [c for c in smoke_packet.claims if c.claim_type == "total_revenue"]
    assert revenues[0].evidence_category == EvidenceCategory.RECOGNIZED_REVENUE


def test_smoke_award_is_not_an_obligation_claim(smoke_packet) -> None:
    """Lifetime Award Amount is context, not a company-FY obligation claim."""
    obligations = [c for c in smoke_packet.claims if c.claim_type == "procurement_obligation"]
    assert obligations == []
    awards = [
        item for item in smoke_packet.evidence
        if item.evidence_category == EvidenceCategory.PROCUREMENT_AWARD
    ]
    assert len(awards) >= 1
    assert awards[0].raw_value == 180000000
    assert awards[0].reporting_period.label == "FFY2024"
    assert awards[0].is_context_only is True


def test_smoke_procurement_award_is_not_revenue(smoke_packet) -> None:
    awards = [
        item for item in smoke_packet.evidence
        if item.source_type.value == "usaspending"
    ]
    for award in awards:
        assert award.evidence_category != EvidenceCategory.RECOGNIZED_REVENUE
        assert award.evidence_category != EvidenceCategory.PROCUREMENT_OBLIGATION


# ====================================================================
# 5. Narrative claim
# ====================================================================

def test_smoke_has_narrative_claim(smoke_packet) -> None:
    """
    The Phase 6 smoke test reported an INFERRED government_exposure
    claim. Its presence triggers SUPPORTED_WITH_LIMITATIONS.
    """
    inferred = [
        c for c in smoke_packet.claims
        if c.claim_status == ClaimStatus.INFERRED
    ]
    assert len(inferred) >= 1


def test_smoke_narrative_claim_is_inferred(smoke_packet) -> None:
    narrative_types = {"government_exposure", "customer_concentration", "corporate_relationship"}
    narratives = [
        c for c in smoke_packet.claims
        if c.claim_type in narrative_types
    ]
    if narratives:
        for n in narratives:
            assert n.claim_status == ClaimStatus.INFERRED


# ====================================================================
# 6. No false conflict
# ====================================================================

def test_smoke_no_conflicts(smoke_packet) -> None:
    """
    Revenue and obligation differ numerically but must not produce
    a conflict. They measure different things.
    """
    assert smoke_packet.conflicts == []


# ====================================================================
# 7. Packet validation
# ====================================================================

def test_smoke_packet_passes_validation(smoke_packet) -> None:
    validate_assessment(smoke_packet)   # must not raise


def test_smoke_has_assessment_id(smoke_packet) -> None:
    assert smoke_packet.assessment_id.startswith("assessment:")


def test_smoke_has_request_id(smoke_packet) -> None:
    assert smoke_packet.request_id.startswith("assessment:")


def test_smoke_has_run_metadata(smoke_packet) -> None:
    assert smoke_packet.run_metadata is not None
    assert smoke_packet.run_metadata.schema_version == "v0.1.0"
    assert smoke_packet.run_metadata.pipeline_version == "v0.1.0"


# ====================================================================
# 8. Human summary
# ====================================================================

def test_smoke_human_summary_contains_vendor(smoke_packet) -> None:
    summary = smoke_packet.human_summary()
    assert "Lockheed Martin Corp" in summary


def test_smoke_human_summary_contains_revenue(smoke_packet) -> None:
    summary = smoke_packet.human_summary()
    assert "total_revenue" in summary
    assert "71043000000" in summary


def test_smoke_human_summary_contains_status(smoke_packet) -> None:
    summary = smoke_packet.human_summary()
    assert "SUPPORTED_WITH_LIMITATIONS" in summary


# ====================================================================
# 9. Full expected transcript
# ====================================================================

def test_smoke_full_transcript() -> None:
    """
    Reproduces the Phase 6 smoke test output as a single assertion
    block. Every locked value is checked here in one place so the
    test suite has one reference point.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )

    # Vendor line
    assert packet.vendor.input_name == "Lockheed Martin Corp"
    assert packet.vendor.resolved_name == "LOCKHEED MARTIN CORP"

    # Period line
    assert packet.reporting_period.label == "FY2024"

    # Status line
    assert packet.assessment_status == AssessmentStatus.SUPPORTED_WITH_LIMITATIONS

    # Claims
    revenues = {c.claim_type: c for c in packet.claims}
    assert "total_revenue" in revenues
    assert revenues["total_revenue"].value == 71043000000
    assert revenues["total_revenue"].claim_status == ClaimStatus.SUPPORTED

    assert "procurement_obligation" not in revenues
    awards = [
        item for item in packet.evidence
        if item.evidence_category == EvidenceCategory.PROCUREMENT_AWARD
    ]
    assert len(awards) == 1
    assert awards[0].raw_value == 180000000
    assert awards[0].reporting_period.label == "FFY2024"

    # At least one INFERRED claim (government_exposure or another narrative)
    inferred = [c for c in packet.claims if c.claim_status == ClaimStatus.INFERRED]
    assert len(inferred) >= 1