# filename: tests/adapters/test_benchmark_adapter.py
# title: Adapter Layer - Benchmark Adapter Tests
# layer: Test suite - adapters
# status: Phase 8
# description:
#     Verifies adapt_to_benchmark: field coverage, entity
#     identity, claim and evidence counts, and JSON
#     serializability.

from __future__ import annotations

import json

from veda.adapters.benchmark_adapter import (
    adapt_to_benchmark,
    BENCHMARK_VERSION,
)
from veda.entity.resolver import FixtureEntityResolverSource
from veda.pipeline.orchestrator import ProviderBundle, run_assessment
from veda.providers.annual_reports import FixtureAnnualReportProvider
from veda.providers.sec_company_facts import FixtureSECCompanyFactsProvider
from veda.providers.sec_filings import FixtureSECFilingsProvider
from veda.providers.usaspending import FixtureUSAspendingProvider
from veda.shared.periods import RequestedPeriod


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        sec_company_facts=FixtureSECCompanyFactsProvider(),
        sec_filing=FixtureSECFilingsProvider(),
        usaspending=FixtureUSAspendingProvider(),
        annual_report=FixtureAnnualReportProvider(),
    )


def _resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource({
        "lockheed martin corp": [
            {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
        ],
    })


def _packet(vendor: str = "Lockheed Martin Corp", year: int = 2024):
    return run_assessment(
        vendor_name=vendor,
        requested_period=RequestedPeriod(fiscal_year=year, raw=str(year)),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )


def test_benchmark_version() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["benchmark_version"] == BENCHMARK_VERSION


def test_all_expected_keys_present() -> None:
    record = adapt_to_benchmark(_packet())
    expected = {
        "benchmark_version", "entity", "period", "evidence",
        "claims", "conflicts", "missing_evidence", "limitations",
        "assessment_status", "human_review_reason",
        "recommended_next_step", "run_metadata",
    }
    assert set(record.keys()) == expected


def test_entity_input_name() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["entity"]["input_name"] == "Lockheed Martin Corp"


def test_entity_resolved_name() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["entity"]["resolved_name"] == "LOCKHEED MARTIN CORP"


def test_entity_cik() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["entity"]["cik"] == "0000936468"


def test_period_label() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["period"]["label"] == "FY2024"


def test_period_start_is_isoformat() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["period"]["start"] == "2024-01-01"


def test_period_end_is_isoformat() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["period"]["end"] == "2024-12-31"


def test_evidence_count() -> None:
    record = adapt_to_benchmark(_packet())
    assert len(record["evidence"]) == 2


def test_claims_count() -> None:
    record = adapt_to_benchmark(_packet())
    # Revenue is a claim. The lifetime award stays context-only evidence.
    assert len(record["claims"]) == 1


def test_revenue_claim_present() -> None:
    record = adapt_to_benchmark(_packet())
    revenues = [c for c in record["claims"] if c["claim_type"] == "total_revenue"]
    assert len(revenues) == 1
    assert revenues[0]["value"] == 71043000000


def test_award_evidence_present_and_not_an_obligation_claim() -> None:
    record = adapt_to_benchmark(_packet())
    awards = [
        item for item in record["evidence"]
        if item["evidence_category"] == "procurement_award"
    ]
    assert len(awards) == 1
    assert awards[0]["raw_value"] == 180000000
    obligations = [
        c for c in record["claims"] if c["claim_type"] == "procurement_obligation"
    ]
    assert obligations == []


def test_conflicts_list_is_empty_for_supported_case() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["conflicts"] == []


def test_status_is_supported_for_clean_case() -> None:
    record = adapt_to_benchmark(_packet())
    assert record["assessment_status"] in ("supported", "supported_with_limitations")


def test_unresolved_vendor_entity_has_null_cik() -> None:
    record = adapt_to_benchmark(_packet("Totally Fake Vendor Name"))
    assert record["entity"]["cik"] is None
    assert record["entity"]["resolution_status"] == "not_found"


def test_unresolved_vendor_has_missing_evidence() -> None:
    record = adapt_to_benchmark(_packet("Totally Fake Vendor Name"))
    assert len(record["missing_evidence"]) >= 1


def test_unresolved_vendor_has_human_review_reason() -> None:
    record = adapt_to_benchmark(_packet("Totally Fake Vendor Name"))
    assert record["human_review_reason"] is not None


def test_record_is_json_serializable() -> None:
    record = adapt_to_benchmark(_packet())
    s = json.dumps(record)
    parsed = json.loads(s)
    assert parsed["benchmark_version"] == BENCHMARK_VERSION
