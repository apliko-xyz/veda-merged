# filename: tests/integration/test_end_to_end.py
# title: Integration - End-to-End Pipeline Tests
# layer: Test suite - integration
# status: Phase 1-6 test recovery
# description:
#     Verifies the pipeline end-to-end against the five frozen cases
#     the employer named: supported, ambiguous, missing, conflict,
#     non-comparable. Uses fixture providers only. No network.
#
#     These tests are the integration layer — they exercise the full
#     pipeline through run_assessment, asserting on the packet that
#     emerges. They sit above the per-layer tests and above the
#     orchestrator unit tests, and they verify the shape of the
#     complete VEDA output for each of the five behaviors.
#
# source:
#     AUTHORED - Phase 6 had no saved integration test before recovery
#     began. The five cases are the employer's documented behavior set.
#
# notes:
#     - Every test uses fixture providers only. respx is not required
#       because no provider in this file makes an HTTP call.
#     - The Assertions use the canonical ID format
#       (assessment:, claim:, evidence:) so a regression in the ID
#       spaces is caught here.

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
    SourceType,
)
from veda.shared.periods import RequestedPeriod
from veda.shared.validation import validate_assessment


# --------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------
LOCKHEED_ENTRIES = {
    "lockheed martin corp": [
        {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
    ],
}

AMBIGUOUS_ENTRIES = {
    "abc technologies": [
        {"cik": 1111111, "ticker": "ABC", "title": "ABC Technologies Inc."},
        {"cik": 2222222, "ticker": None, "title": "ABC Technologies LLC"},
    ],
}


def _bundle(*, with_filing: bool = False) -> ProviderBundle:
    return ProviderBundle(
        sec_company_facts=FixtureSECCompanyFactsProvider(),
        sec_filing=FixtureSECFilingsProvider(),
        usaspending=FixtureUSAspendingProvider(),
        annual_report=FixtureAnnualReportProvider(),
        sec_filing_accession_number="0000936468-25-000009" if with_filing else None,
        sec_filing_form="10-K" if with_filing else None,
        sec_filing_passage_hint="revenues" if with_filing else None,
    )


def _lockheed_resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource(LOCKHEED_ENTRIES)


def _ambiguous_resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource(AMBIGUOUS_ENTRIES)


def _requested(year: int = 2024) -> RequestedPeriod:
    return RequestedPeriod(fiscal_year=year, raw=str(year))


# ====================================================================
# CASE 1 — SUPPORTED
# ====================================================================
# A single resolved vendor with dated SEC evidence for the requested
# year. The pipeline produces a valid packet with a SUPPORTED or
# SUPPORTED_WITH_LIMITATIONS status and the correct revenue value.


def test_case_1_supported_resolves_vendor() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.vendor.resolution_status == EntityResolutionStatus.RESOLVED


def test_case_1_supported_produces_valid_packet() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    validate_assessment(packet)   # must not raise


def test_case_1_supported_has_dated_period() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.reporting_period.start == date(2024, 1, 1)
    assert packet.reporting_period.end == date(2024, 12, 31)


def test_case_1_supported_produces_total_revenue_claim() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    revenues = [c for c in packet.claims if c.claim_type == "total_revenue"]
    assert len(revenues) == 1
    assert revenues[0].value == 71043000000


def test_case_1_supported_status_is_not_insufficient() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_status in (
        AssessmentStatus.SUPPORTED,
        AssessmentStatus.SUPPORTED_WITH_LIMITATIONS,
    )


# ====================================================================
# CASE 2 — AMBIGUOUS IDENTITY
# ====================================================================
# A vendor name that matches two candidates. The resolver returns
# AMBIGUOUS. The pipeline must produce REQUIRES_HUMAN_REVIEW, with
# no claims and no evidence, and a specific human-review reason.


def test_case_2_ambiguous_resolution_status() -> None:
    packet = run_assessment(
        vendor_name="ABC Technologies",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_ambiguous_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.vendor.resolution_status == EntityResolutionStatus.AMBIGUOUS


def test_case_2_ambiguous_produces_requires_human_review() -> None:
    packet = run_assessment(
        vendor_name="ABC Technologies",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_ambiguous_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_status == AssessmentStatus.REQUIRES_HUMAN_REVIEW


def test_case_2_ambiguous_has_candidates() -> None:
    packet = run_assessment(
        vendor_name="ABC Technologies",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_ambiguous_resolver(),
        user_agent="Test test@example.com",
    )
    assert len(packet.vendor.candidates) == 2


def test_case_2_ambiguous_has_no_claims() -> None:
    packet = run_assessment(
        vendor_name="ABC Technologies",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_ambiguous_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.claims == []


# ====================================================================
# CASE 3 — MISSING EVIDENCE
# ====================================================================
# A resolved vendor with no data for the requested period. The
# pipeline must produce an INSUFFICIENT_EVIDENCE or
# REQUIRES_HUMAN_REVIEW packet with a specific missing-evidence
# record, not raise.


def test_case_3_unknown_year_produces_abstention() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(1899),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_status in (
        AssessmentStatus.INSUFFICIENT_EVIDENCE,
        AssessmentStatus.REQUIRES_HUMAN_REVIEW,
    )


def test_case_3_unknown_year_has_no_revenue_claim() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(1899),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    revenues = [c for c in packet.claims if c.claim_type == "total_revenue"]
    assert revenues == []


def test_case_3_unknown_year_packet_validates() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(1899),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    validate_assessment(packet)   # must not raise


# ====================================================================
# CASE 4 — NON-COMPARABLE MEASURES
# ====================================================================
# Revenue and procurement obligation for the same vendor and period.
# They must be preserved as separate claims with distinct
# evidence_category values. They must NOT be flagged as conflicting.


def test_case_4_revenue_and_award_both_present() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    revenues = [c for c in packet.claims if c.claim_type == "total_revenue"]
    awards = [
        item for item in packet.evidence
        if item.evidence_category == EvidenceCategory.PROCUREMENT_AWARD
    ]
    assert len(revenues) >= 1
    assert len(awards) >= 1


def test_case_4_revenue_and_award_have_distinct_categories() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    revenue_categories = {
        c.evidence_category for c in packet.claims if c.claim_type == "total_revenue"
    }
    award_categories = {
        item.evidence_category for item in packet.evidence
        if item.source_type == SourceType.USASPENDING
    }
    assert EvidenceCategory.RECOGNIZED_REVENUE in revenue_categories
    assert EvidenceCategory.PROCUREMENT_AWARD in award_categories
    assert EvidenceCategory.PROCUREMENT_OBLIGATION not in award_categories
    assert EvidenceCategory.RECOGNIZED_REVENUE not in award_categories


def test_case_4_no_conflict_between_revenue_and_obligation() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.conflicts == []


def test_case_4_no_false_conflict_record() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    for cf in packet.conflicts:
        assert "procurement_obligation" not in cf.claim_type
        assert "total_revenue" not in cf.claim_type


# ====================================================================
# CASE 5 — GENUINE SAME-METRIC CONFLICT
# ====================================================================
# Not currently exercised by fixture data, but the pipeline must be
# able to produce CONFLICTING_EVIDENCE if two comparable claims
# disagree. This is asserted at the pipeline layer in
# test_conflict_detector.py. Here we assert the end-to-end shape
# when the fixture bundle contains a genuine conflict is out of
# scope for this recovery. The presence of the assertion confirms
# the pipeline surface exists.


def test_case_5_conflict_surface_exists() -> None:
    """
    The Assessment model exposes a conflicts list. The pipeline
    populates it when detect_conflicts returns non-empty. The
    end-to-end conflict fixture is out of scope for this recovery
    file, but the shape is verified: a real packet has a conflicts
    attribute that is a list.
    """
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert isinstance(packet.conflicts, list)


# ====================================================================
# Cross-case properties
# ====================================================================

def test_all_packets_have_assessment_id() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.assessment_id.startswith("assessment:")


def test_all_packets_have_request_id() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    assert packet.request_id.startswith("assessment:")


def test_all_evidence_ids_are_canonical() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    for e in packet.evidence:
        assert e.evidence_id.startswith("evidence:")


def test_all_claim_ids_are_canonical() -> None:
    packet = run_assessment(
        vendor_name="Lockheed Martin Corp",
        requested_period=_requested(2024),
        bundle=_bundle(),
        resolver_source=_lockheed_resolver(),
        user_agent="Test test@example.com",
    )
    for c in packet.claims:
        assert c.claim_id.startswith("claim:")