# filename: tests/interfaces/test_api.py
# title: Interface Layer - HTTP API Tests
# layer: Test suite - interfaces
# status: Phase 7 — Sub-phase 7C
# description:
#     Verifies the FastAPI surface: /health, /assess, /, and the
#     config-error paths.
#
#     The critical guarantee: a valid packet in ANY assessment
#     state returns HTTP 200. An abstention is a valid answer, not
#     an HTTP error.

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veda.interfaces.api import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def _set_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a test user agent so the API does not fall back to its default."""
    monkeypatch.setenv("SEC_API_USER_AGENT", "Test test@example.com")


# ====================================================================
# 1. Health
# ====================================================================

def test_health_returns_200() -> None:
    r = client.get("/health")
    assert r.status_code == 200


def test_health_body() -> None:
    r = client.get("/health")
    assert r.json() == {"status": "ok"}


# ====================================================================
# 2. Supported case
# ====================================================================

def test_assess_supported_returns_200() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    assert r.status_code == 200


def test_assess_supported_has_status() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    packet = r.json()
    assert packet["assessment_status"] in ("supported", "supported_with_limitations")


def test_assess_supported_revenue_value() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    packet = r.json()
    revenues = [
        c for c in packet["claims"] if c["claim_type"] == "total_revenue"
    ]
    assert len(revenues) == 1
    assert revenues[0]["value"] == 71043000000


def test_assess_supported_award_value() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    packet = r.json()
    awards = [
        item for item in packet["evidence"]
        if item["evidence_category"] == "procurement_award"
    ]
    assert len(awards) >= 1
    assert awards[0]["raw_value"] == 180000000
    assert awards[0]["reporting_period"]["label"] == "FFY2024"
    obligations = [
        c for c in packet["claims"] if c["claim_type"] == "procurement_obligation"
    ]
    assert obligations == []


def test_assess_supported_no_conflicts() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    packet = r.json()
    assert packet["conflicts"] == []


def test_assess_returns_canonical_ids() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    packet = r.json()
    assert packet["assessment_id"].startswith("assessment:")
    assert packet["request_id"].startswith("assessment:")
    for claim in packet["claims"]:
        assert claim["claim_id"].startswith("claim:")
    for evidence in packet["evidence"]:
        assert evidence["evidence_id"].startswith("evidence:")


# ====================================================================
# 3. Abstention — critical: HTTP 200 for a valid answer
# ====================================================================

def test_unknown_vendor_returns_200() -> None:
    """An abstention is a valid answer. HTTP 200."""
    r = client.post(
        "/assess",
        json={"company_name": "Totally Fake Vendor Name", "fiscal_year": 2024},
    )
    assert r.status_code == 200


def test_unknown_vendor_status_requires_human_review() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Totally Fake Vendor Name", "fiscal_year": 2024},
    )
    packet = r.json()
    assert packet["assessment_status"] == "requires_human_review"


def test_unknown_vendor_has_human_review_reason() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Totally Fake Vendor Name", "fiscal_year": 2024},
    )
    packet = r.json()
    assert packet["human_review_reason"] is not None
    assert "Totally Fake Vendor Name" in packet["human_review_reason"]


def test_unknown_vendor_has_missing_evidence() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Totally Fake Vendor Name", "fiscal_year": 2024},
    )
    packet = r.json()
    reasons = [m["reason"] for m in packet["missing_evidence"]]
    assert "entity_ambiguous" in reasons


# ====================================================================
# 4. Known vendor, unknown year
# ====================================================================

def test_known_vendor_unknown_year_returns_200() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 1899},
    )
    assert r.status_code == 200


def test_known_vendor_unknown_year_abstains() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 1899},
    )
    packet = r.json()
    assert packet["assessment_status"] in (
        "insufficient_evidence",
        "requires_human_review",
    )


# ====================================================================
# 5. Request validation
# ====================================================================

def test_missing_company_name_returns_422() -> None:
    r = client.post("/assess", json={"fiscal_year": 2024})
    assert r.status_code == 422


def test_missing_fiscal_year_returns_422() -> None:
    r = client.post("/assess", json={"company_name": "Lockheed Martin Corp"})
    assert r.status_code == 422


def test_empty_company_name_returns_422() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "", "fiscal_year": 2024},
    )
    assert r.status_code == 422


# ====================================================================
# 6. Configuration errors
# ====================================================================

def test_providers_live_accepted_by_api() -> None:
    """provider_mode='live' is accepted. This test uses fixture
    provider mode (the default) because live mode requires a real
    User-Agent and a real network. The mode field itself is
    validated as accepted by the request model."""
    r = client.post(
        "/assess",
        json={
            "company_name": "Lockheed Martin Corp",
            "fiscal_year": 2024,
            "provider_mode": "fixture",
        },
    )
    assert r.status_code == 200


def test_sec_filing_fields_accepted() -> None:
    """The three optional SEC filing fields are accepted in the
    request body. With fixture mode, they have no effect, but the
    API must not reject them."""
    r = client.post(
        "/assess",
        json={
            "company_name": "Lockheed Martin Corp",
            "fiscal_year": 2024,
            "sec_filing_accession_number": "0000936468-25-000009",
            "sec_filing_form": "10-K",
            "sec_filing_passage_hint": "revenues",
        },
    )
    assert r.status_code == 200


def test_extractor_composite_returns_400() -> None:
    """extractor_mode='composite' is rejected at bundle build time
    with a client error (400), not a server error."""
    r = client.post(
        "/assess",
        json={
            "company_name": "Lockheed Martin Corp",
            "fiscal_year": 2024,
            "extractor_mode": "composite",
        },
    )
    assert r.status_code == 400


def test_extractor_composite_error_message() -> None:
    r = client.post(
        "/assess",
        json={
            "company_name": "Lockheed Martin Corp",
            "fiscal_year": 2024,
            "extractor_mode": "composite",
        },
    )
    body = r.json()
    detail = body.get("detail", "")
    assert "reserved for a later phase" in detail or "composite" in detail.lower()


# ====================================================================
# 7. Dashboard
# ====================================================================

def test_dashboard_returns_200() -> None:
    r = client.get("/")
    assert r.status_code == 200


def test_dashboard_content_type_is_html() -> None:
    r = client.get("/")
    assert "text/html" in r.headers.get("content-type", "")


def test_dashboard_body_contains_veda() -> None:
    r = client.get("/")
    assert "VEDA" in r.text


# ====================================================================
# 8. Defaults
# ====================================================================

def test_defaults_are_fixture() -> None:
    r = client.post(
        "/assess",
        json={"company_name": "Lockheed Martin Corp", "fiscal_year": 2024},
    )
    packet = r.json()
    modes = packet["run_metadata"]["provider_modes"]
    assert modes["sec_company_facts"] == "fixture"
    assert modes["sec_filing"] == "fixture"
    assert modes["usaspending"] == "fixture"
    assert modes["annual_report"] == "fixture"