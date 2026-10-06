# filename: tests/pipeline/test_orchestrator.py
# title: Pipeline Layer - Orchestrator Tests
# layer: Test suite - pipeline
# status: Phase 1-6 test recovery
# description:
#     Verifies run_assessment: the single entry point of the merged
#     VEDA pipeline. This file exercises the full flow end-to-end
#     using fixture providers only. No network. No LLM.
#
#     The orchestrator composes:
#         entity resolution
#         provider retrieval (4 providers)
#         normalization (4 normalizers)
#         period backfill
#         claim extraction
#         conflict detection
#         missing-evidence calculation
#         assessment status
#         packet assembly
#         packet validation
#
#     The tests below cover:
#         the supported case (Lockheed Martin FY2024)
#         the unresolved vendor case
#         the no-data case
#         the partial-data case (SEC facts present, no filing)
#         determinism of the packet ID spaces
#         validation always runs before the packet is returned
#
# source:
#     AUTHORED - Phase 6 had no saved test before recovery began.
#     The orchestrator in src/veda/pipeline/orchestrator.py is the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - ProviderBundle carries four providers plus optional SEC
#       filing metadata. When the SEC filing metadata is None, the
#       filing provider returns NOT_FOUND for the passage lookup.
#       That is a legitimate outcome and does not raise.
#     - The locked Phase 6 smoke test output is the reference for
#       the supported case:
#         status = SUPPORTED_WITH_LIMITATIONS
#         total_revenue = 71043000000
#         procurement_obligation = 180000000
#         government_exposure = INFERRED

from __future__ import annotations

from datetime import date

import pytest

from veda.entity.resolver import FixtureEntityResolverSource
from veda.pipeline.orchestrator import ProviderBundle, run_assessment
from veda.providers.base import EvidenceProvider
from veda.providers.results import ProviderRequest, ProviderResult
from veda.shared.enums import ProviderStatus, SourceType
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


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------
LOCKHEED_ENTRIES = {
    "lockheed martin corp": [
        {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
    ],
}


def _bundle(*, with_filing: bool = False) -> ProviderBundle:
    """
    Build a provider bundle using only fixture providers.

    When with_filing is True, the SEC filing metadata is supplied so
    the filing provider returns a passage. When False, the filing
    provider returns NOT_FOUND and the pipeline proceeds without the
    narrative claims.
    """
    return ProviderBundle(
        sec_company_facts=FixtureSECCompanyFactsProvider(),
        sec_filing=FixtureSECFilingsProvider(),
        usaspending=FixtureUSAspendingProvider(),
        annual_report=FixtureAnnualReportProvider(),
        sec_filing_accession_number="0000936468-25-000009" if with_filing else None,
        sec_filing_form="10-K" if with_filing else None,
        sec_filing_passage_hint="revenues" if with_filing else None,
    )


def _resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource(LOCKHEED_ENTRIES)


def _requested(year: int = 2024) -> RequestedPeriod:
    return RequestedPeriod(fiscal_year=year, raw=str(year))


# ====================================================================
# 1. Supported case — Lockheed Martin FY2024
# ====================================================================

def test_supported_case_runs() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet is not None


def test_supported_case_returns_resolved_vendor() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.vendor.resolution_status == EntityResolutionStatus.RESOLVED


def test_supported_case_status_is_supported_or_supported_with_limitations() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_status in (
        AssessmentStatus.SUPPORTED,
        AssessmentStatus.SUPPORTED_WITH_LIMITATIONS,
    )


def test_supported_case_produces_total_revenue_claim() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    revenue_claims = [c for c in packet.claims if c.claim_type == "total_revenue"]
    assert len(revenue_claims) >= 1


def test_supported_case_revenue_value_is_correct() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    revenue_claims = [c for c in packet.claims if c.claim_type == "total_revenue"]
    values = {c.value for c in revenue_claims}
    assert 71043000000 in values


def test_supported_case_award_is_separate_from_revenue() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    awards = [
        item for item in packet.evidence
        if item.evidence_category == EvidenceCategory.PROCUREMENT_AWARD
    ]
    assert len(awards) >= 1
    assert all(item.evidence_category != EvidenceCategory.RECOGNIZED_REVENUE for item in awards)
    assert [c for c in packet.claims if c.claim_type == "procurement_obligation"] == []


def test_supported_case_award_value_is_correct() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    awards = [
        item for item in packet.evidence
        if item.evidence_category == EvidenceCategory.PROCUREMENT_AWARD
    ]
    assert 180000000 in {item.raw_value for item in awards}


def test_supported_case_no_false_conflict() -> None:
    """
    Revenue and obligation are different measures. They must not be
    flagged as conflicting with each other.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.conflicts == []


# ====================================================================
# 2. Filing metadata changes the packet
# ====================================================================

def test_with_filing_metadata_produces_narrative_claim() -> None:
    """
    When SEC filing metadata is supplied, the filing provider returns
    a passage and the pipeline produces a narrative claim.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(with_filing=True),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    narrative = [c for c in packet.claims if c.claim_type != "total_revenue" and c.claim_type != "procurement_obligation"]
    assert len(narrative) >= 1


def test_with_filing_metadata_status_reflects_inferred() -> None:
    """
    When a narrative claim is INFERRED, the assessment status becomes
    SUPPORTED_WITH_LIMITATIONS, not SUPPORTED.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(with_filing=True),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    inferred = [c for c in packet.claims if c.claim_status == ClaimStatus.INFERRED]
    if inferred:
        assert packet.assessment_status == AssessmentStatus.SUPPORTED_WITH_LIMITATIONS


# ====================================================================
# 3. Unresolved vendor
# ====================================================================

def test_unknown_vendor_returns_requires_human_review() -> None:
    packet = run_assessment(
        vendor_name="Totally Fake Vendor Name",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_status == AssessmentStatus.REQUIRES_HUMAN_REVIEW


def test_unknown_vendor_has_empty_claims_and_evidence() -> None:
    packet = run_assessment(
        vendor_name="Totally Fake Vendor Name",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.claims == []
    assert packet.evidence == []


def test_unknown_vendor_has_human_review_reason() -> None:
    packet = run_assessment(
        vendor_name="Totally Fake Vendor Name",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.human_review_reason is not None
    assert "Totally Fake Vendor Name" in packet.human_review_reason


# ====================================================================
# 4. No-data case
# ====================================================================

def test_known_vendor_unknown_year_returns_insufficient() -> None:
    """Lockheed Martin is resolvable but has no data for FY1899."""
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(1899),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_status in (
        AssessmentStatus.INSUFFICIENT_EVIDENCE,
        AssessmentStatus.REQUIRES_HUMAN_REVIEW,
    )


# ====================================================================
# 5. Packet structure
# ====================================================================

def test_packet_has_assessment_id() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_id is not None
    assert packet.assessment_id.startswith("assessment:")


def test_packet_has_request_id() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.request_id is not None


def test_packet_has_run_metadata() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.run_metadata is not None
    assert packet.run_metadata.schema_version == "v0.1.0"
    assert packet.run_metadata.pipeline_version == "v0.1.0"


def test_packet_run_metadata_records_provider_modes() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    modes = packet.run_metadata.provider_modes
    assert modes["sec_company_facts"] == "fixture"
    assert modes["sec_filing"] == "fixture"
    assert modes["usaspending"] == "fixture"
    assert modes["annual_report"] == "fixture"


# ====================================================================
# 6. Determinism of ID spaces
# ====================================================================

def test_two_runs_produce_different_assessment_ids() -> None:
    """Assessment IDs are timestamped + random, so two runs differ."""
    packet_a = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    packet_b = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet_a.assessment_id != packet_b.assessment_id


def test_two_runs_produce_same_claim_ids() -> None:
    """Claim IDs are content-addressed, so two runs produce the same IDs."""
    packet_a = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    packet_b = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    ids_a = sorted(c.claim_id for c in packet_a.claims)
    ids_b = sorted(c.claim_id for c in packet_b.claims)
    assert ids_a == ids_b


def test_two_runs_produce_same_evidence_ids() -> None:
    packet_a = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    packet_b = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    ids_a = sorted(e.evidence_id for e in packet_a.evidence)
    ids_b = sorted(e.evidence_id for e in packet_b.evidence)
    assert ids_a == ids_b


# ====================================================================
# 7. Validation always runs
# ====================================================================

def test_packet_passes_validation_on_return() -> None:
    """
    run_assessment calls validate_assessment before returning. If any
    invariant failed, the function would have raised. The fact that a
    packet is returned proves the invariant held.
    """
    from veda.shared.validation import validate_assessment

    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    # Calling validate_assessment again must not raise.
    validate_assessment(packet)


# ====================================================================
# 8. Period-resolution tests — locked to prevent regression of Fix A
# ====================================================================
#
# These tests guard the pipeline against the bare-year defect that
# Fix A corrected. They cover the three period paths the pipeline
# must handle:
#
#   1. Bare-year request with dated SEC evidence.
#      The pipeline derives dates from SEC Company Facts and backfills.
#
#   2. Explicit dated request.
#      The pipeline preserves the caller's dates unchanged.
#
#   3. Bare-year request with no dated evidence.
#      The pipeline returns an undated period and a valid abstention
#      packet. It does NOT raise.
#
# If any of these break, a natural CLI invocation such as
# "veda 'Lockheed Martin Corp' 2024" would fail on first use.


def test_bare_year_request_with_dated_sec_evidence_succeeds() -> None:
    """
    The primary defect Fix A corrected.

    A bare-year RequestedPeriod (start=None, end=None) must produce a
    valid packet when SEC Company Facts evidence supplies the dates.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet is not None
    assert packet.reporting_period.start is not None
    assert packet.reporting_period.end is not None


def test_bare_year_request_backfills_all_claim_periods() -> None:
    """
    Every claim in the packet must carry a dated period after the
    backfill runs. A label-only claim would fail validation.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    for claim in packet.claims:
        assert claim.reporting_period.start is not None, (
            f"claim {claim.claim_id!r} ({claim.claim_type}) has no start date"
        )
        assert claim.reporting_period.end is not None, (
            f"claim {claim.claim_id!r} ({claim.claim_type}) has no end date"
        )


def test_usaspending_truncation_is_a_packet_limitation() -> None:
    class _TruncatingUSAspending(EvidenceProvider):
        @property
        def source_type(self):
            return SourceType.USASPENDING

        @property
        def source_name(self):
            return "USAspending"

        @property
        def is_fixture(self):
            return True

        def retrieve(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                status=ProviderStatus.FOUND,
                source_type=SourceType.USASPENDING,
                source_name=self.source_name,
                is_fixture=True,
                raw_records=[{
                    "Award ID": "TRUNC-1",
                    "Award Amount": 10,
                    "generated_internal_id": "CONT_AWD_TRUNC1",
                    "_veda_record_kind": "award",
                }],
                retrieval_metadata={
                    "pages_fetched": 1,
                    "has_more": True,
                    "truncated": True,
                    "recipient_match": "uei",
                },
            )

    bundle = _bundle()
    bundle = ProviderBundle(
        sec_company_facts=bundle.sec_company_facts,
        sec_filing=bundle.sec_filing,
        usaspending=_TruncatingUSAspending(),
        annual_report=bundle.annual_report,
    )
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=bundle,
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert any("truncated" in item.lower() for item in packet.limitations)


def test_bare_year_request_award_keeps_federal_fy_dates() -> None:
    """
    Award evidence keeps the federal FY window. It is not backfilled
    onto the company fiscal year resolved from SEC dates.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    awards = [
        item for item in packet.evidence
        if item.evidence_category == EvidenceCategory.PROCUREMENT_AWARD
    ]
    assert len(awards) >= 1
    for item in awards:
        assert item.reporting_period.label == "FFY2024"
        assert item.reporting_period.start != packet.reporting_period.start
        assert item.reporting_period.end != packet.reporting_period.end


def test_explicit_dated_request_preserves_caller_dates() -> None:
    """
    An explicit dated request must be used unchanged. The pipeline
    must not override the caller's dates with evidence-derived dates.
    """
    explicit = RequestedPeriod(
        fiscal_year=2024,
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        raw="2024-01-01..2024-12-31",
    )
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=explicit,
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.reporting_period.start == date(2024, 1, 1)
    assert packet.reporting_period.end == date(2024, 12, 31)


def test_bare_year_request_with_no_dated_evidence_does_not_raise() -> None:
    """
    When no dated evidence exists — an unresolved vendor, or a
    resolved vendor with no data for the requested year — the
    pipeline must return an abstention packet, not raise.

    This is the second-order defect Fix A revealed and corrected.
    """
    packet = run_assessment(
        vendor_name="Totally Fake Vendor Name",
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet is not None
    # Unresolved vendor → REQUIRES_HUMAN_REVIEW
    assert packet.assessment_status == AssessmentStatus.REQUIRES_HUMAN_REVIEW


def test_bare_year_request_with_no_data_returns_undated_period() -> None:
    """
    When there is no dated evidence, the packet's period is undated.
    The Assessment model accepts this because claims and conflicts
    are empty. validate_assessment's period-scope rule also returns
    early in that case.
    """
    packet = run_assessment(
        vendor_name="Totally Fake Vendor Name",
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.reporting_period.start is None
    assert packet.reporting_period.end is None
    assert packet.reporting_period.label == "FY2024"


def test_bare_year_request_no_data_still_passes_validation() -> None:
    """
    The abstention packet must pass validate_assessment. This proves
    the pipeline emits a valid packet even when there are no dates
    to resolve.
    """
    from veda.shared.validation import validate_assessment

    packet = run_assessment(
        vendor_name="Totally Fake Vendor Name",
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    validate_assessment(packet)   # must not raise


def test_known_vendor_unknown_year_abstention() -> None:
    """
    Lockheed Martin FY1899 is resolvable but has no data. The
    pipeline must return an abstention packet with an undated
    period, not raise.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=RequestedPeriod(fiscal_year=1899, raw="1899"),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet is not None
    assert packet.reporting_period.label == "FY1899"
    assert packet.assessment_status in (
        AssessmentStatus.INSUFFICIENT_EVIDENCE,
        AssessmentStatus.REQUIRES_HUMAN_REVIEW,
    )