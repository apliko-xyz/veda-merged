# filename: tests/providers/test_usaspending.py
# title: Provider Layer - USAspending Tests
# layer: Test suite - providers
# status: Phase 1-6 test recovery
# description:
#     Verifies both USAspending providers: the fixture provider
#     (deterministic, no network) and the live provider (networked,
#     mocked via respx).
#
#     The USAspending provider is the second source. Its purpose is to
#     retrieve procurement obligations, which are never treated as
#     revenue. The provider itself only retrieves; the distinction is
#     enforced in normalization and conflict detection. But the
#     provider must return the obligation records with the correct
#     fiscal year filter, so the distinction has data to work with.
#
# source:
#     AUTHORED - Phase 4 had no saved test before recovery began.
#     The providers in src/veda/providers/usaspending.py are the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - The live provider posts to:
#         https://api.usaspending.gov/api/v2/search/spending_by_award/
#       with the fiscal year as a query parameter.
#     - The fixture provider looks up awards by lowercased
#       company_name, or by supported entity_id suffixes.

from __future__ import annotations

import json

import httpx
import pytest
import respx

from veda.providers.results import ProviderRequest
from veda.providers.usaspending import (
    AWARD_FIELDS,
    CONTRACT_AWARD_TYPE_CODES,
    DLA_AWARDING_SUBTIER,
    USASPENDING_TRANSACTION_URL,
    FixtureUSAspendingProvider,
    USAspendingProvider,
)
from veda.shared.enums import ProviderStatus, SourceType
from veda.shared.periods import RequestedPeriod


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"


def _rp(year: int = 2024) -> RequestedPeriod:
    return RequestedPeriod(fiscal_year=year, raw=str(year))


def _request_lmt(year: int = 2024) -> ProviderRequest:
    return ProviderRequest(
        cik="0000936468",
        company_name="Lockheed Martin Corp",
        requested_period=_rp(year),
    )


def _request_unknown() -> ProviderRequest:
    return ProviderRequest(
        company_name="Nonexistent Vendor Corp",
        requested_period=_rp(),
    )


# ====================================================================
# 1. Fixture provider
# ====================================================================

def test_fixture_provider_source_type() -> None:
    p = FixtureUSAspendingProvider()
    assert p.source_type == SourceType.USASPENDING


def test_fixture_provider_is_fixture_true() -> None:
    p = FixtureUSAspendingProvider()
    assert p.is_fixture is True


def test_fixture_provider_returns_lockheed_2024_awards() -> None:
    p = FixtureUSAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.FOUND
    assert len(result.raw_records) >= 1


def test_fixture_provider_award_has_award_amount() -> None:
    p = FixtureUSAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    record = result.raw_records[0]
    assert record["Award Amount"] == 180000000
    assert "Total Obligated Amount" not in record
    assert result.retrieval_metadata["truncated"] is False
    assert result.retrieval_metadata["time_period"]["start_date"] == "2023-10-01"
    assert result.retrieval_metadata["time_period"]["end_date"] == "2024-09-30"


def test_fixture_provider_returns_no_awards_for_wrong_year() -> None:
    p = FixtureUSAspendingProvider()
    result = p.retrieve(_request_lmt(2023))
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_returns_not_found_for_unknown() -> None:
    p = FixtureUSAspendingProvider()
    result = p.retrieve(_request_unknown())
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_requires_company_name_or_entity_id() -> None:
    p = FixtureUSAspendingProvider()
    req = ProviderRequest(cik="0000000001", requested_period=_rp())
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_key_lowercased() -> None:
    """Lookup is case-insensitive for company_name."""
    p = FixtureUSAspendingProvider()
    req = ProviderRequest(
        company_name="LOCKHEED MARTIN CORP",
        requested_period=_rp(2024),
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.FOUND


def test_fixture_provider_metadata_includes_fiscal_year() -> None:
    p = FixtureUSAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.retrieval_metadata["fiscal_year"] == 2024


# ====================================================================
# 2. Live provider constructor
# ====================================================================

def test_live_provider_source_type() -> None:
    p = USAspendingProvider()
    assert p.source_type == SourceType.USASPENDING


def test_live_provider_is_fixture_false() -> None:
    p = USAspendingProvider()
    assert p.is_fixture is False


# ====================================================================
# 3. Live provider - HTTP responses
# ====================================================================

@respx.mock
def test_live_provider_200_with_results_returns_found() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "Award ID": "USASPEND-LMT-2024-0001",
                        "Recipient Name": "LOCKHEED MARTIN CORP",
                        "Awarding Agency": "Department of Defense",
                        "Total Obligated Amount": 180000000,
                    }
                ]
            },
        )
    )
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.FOUND
    assert len(result.raw_records) == 1


@respx.mock
def test_live_provider_200_with_empty_results_returns_not_found() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.NOT_FOUND


@respx.mock
def test_live_provider_200_with_non_dict_returns_malformed() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE


@respx.mock
def test_live_provider_200_with_non_list_results_returns_malformed() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(200, json={"results": "not a list"})
    )
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE


@respx.mock
def test_live_provider_200_with_invalid_json_returns_malformed() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(200, text="not json{")
    )
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE


@respx.mock
def test_live_provider_404_returns_not_found() -> None:
    respx.post(USASPENDING_URL).mock(return_value=httpx.Response(404))
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.NOT_FOUND


@respx.mock
def test_live_provider_429_returns_rate_limited() -> None:
    respx.post(USASPENDING_URL).mock(return_value=httpx.Response(429))
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.RATE_LIMITED


@respx.mock
def test_live_provider_400_returns_source_unavailable() -> None:
    respx.post(USASPENDING_URL).mock(return_value=httpx.Response(400))
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.SOURCE_UNAVAILABLE


@respx.mock
def test_live_provider_500_retries_once() -> None:
    route = respx.post(USASPENDING_URL).mock(return_value=httpx.Response(500))
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.SOURCE_UNAVAILABLE
    assert route.call_count == 2


@respx.mock
def test_live_provider_500_then_200_succeeds() -> None:
    route = respx.post(USASPENDING_URL)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={"results": [{"Award ID": "X"}]}),
    ]
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.FOUND


@respx.mock
def test_live_provider_timeout_retries_once() -> None:
    route = respx.post(USASPENDING_URL)
    route.side_effect = [
        httpx.TimeoutException("timeout"),
        httpx.TimeoutException("timeout"),
    ]
    p = USAspendingProvider()
    result = p.retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.SOURCE_UNAVAILABLE
    assert route.call_count == 2


@respx.mock
def test_live_provider_does_not_send_fiscal_year_query_param() -> None:
    """fiscal_year is not a documented parameter of spending_by_award."""
    route = respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"Award ID": "X", "Award Amount": 1}], "page_metadata": {"hasNext": False, "page": 1}},
        )
    )
    p = USAspendingProvider()
    p.retrieve(_request_lmt(2024))
    assert route.calls.last.request.url.params.get("fiscal_year") is None


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_live_request_body_has_time_period_award_types_and_dla() -> None:
    route = respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "X", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    USAspendingProvider().retrieve(_request_lmt(2024))
    body = _body(route)
    assert body["filters"]["time_period"] == [
        {"start_date": "2023-10-01", "end_date": "2024-09-30"}
    ]
    assert body["filters"]["award_type_codes"] == CONTRACT_AWARD_TYPE_CODES
    assert body["filters"]["agencies"] == [DLA_AWARDING_SUBTIER]
    assert body["fields"] == AWARD_FIELDS
    assert "Total Obligated Amount" not in body["fields"]
    assert "fiscal_year" not in body
    assert body["filters"]["recipient_search_text"] == ["Lockheed Martin Corp"]


@respx.mock
def test_live_request_prefers_uei_over_name() -> None:
    route = respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "X", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    request = _request_lmt(2024).model_copy(update={"recipient_uei": "GLNEA3M9Y8Y3"})
    result = USAspendingProvider().retrieve(request)
    body = _body(route)
    assert body["filters"]["recipient_search_text"] == ["GLNEA3M9Y8Y3"]
    assert result.retrieval_metadata["recipient_match"] == "uei"
    assert "recipient_match_note" not in result.retrieval_metadata


@respx.mock
def test_name_match_is_recorded() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "X", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert result.retrieval_metadata["recipient_match"] == "name"
    assert "affiliates" in result.retrieval_metadata["recipient_match_note"]


@respx.mock
def test_dla_filter_can_be_turned_off() -> None:
    route = respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "X", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    request = _request_lmt(2024).model_copy(update={"filter_awarding_subtier_dla": False})
    USAspendingProvider().retrieve(request)
    assert "agencies" not in _body(route)["filters"]


@respx.mock
def test_pagination_stops_when_has_next_is_false() -> None:
    route = respx.post(USASPENDING_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [{"Award ID": "A", "Award Amount": 1}],
                    "page_metadata": {"page": 1, "hasNext": True},
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [{"Award ID": "B", "Award Amount": 2}],
                    "page_metadata": {"page": 2, "hasNext": False},
                },
            ),
        ]
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert route.call_count == 2
    assert result.status == ProviderStatus.FOUND
    assert [row["Award ID"] for row in result.raw_records] == ["A", "B"]
    assert result.retrieval_metadata["pages_fetched"] == 2
    assert result.retrieval_metadata["has_more"] is False
    assert result.retrieval_metadata["truncated"] is False


@respx.mock
def test_pagination_stops_at_page_cap_and_records_truncation() -> None:
    route = respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "A", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": True},
            },
        )
    )
    request = _request_lmt(2024).model_copy(update={"usaspending_page_cap": 1})
    result = USAspendingProvider().retrieve(request)
    assert route.call_count == 1
    assert result.retrieval_metadata["pages_fetched"] == 1
    assert result.retrieval_metadata["has_more"] is True
    assert result.retrieval_metadata["truncated"] is True


@respx.mock
def test_transaction_endpoint_is_off_by_default() -> None:
    award_route = respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "A", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    txn_route = respx.post(USASPENDING_TRANSACTION_URL).mock(
        return_value=httpx.Response(500)
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert award_route.call_count == 1
    assert txn_route.call_count == 0
    assert result.retrieval_metadata["include_transactions"] is False
    assert "spending_by_transaction" in result.retrieval_metadata["transaction_gap"]


@respx.mock
def test_transaction_endpoint_is_requested_when_flagged() -> None:
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "A", "Award Amount": 10}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    txn_route = respx.post(USASPENDING_TRANSACTION_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{
                    "Award ID": "A",
                    "Transaction Amount": "15.50",
                    "Action Date": "2024-01-04",
                }],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    request = _request_lmt(2024).model_copy(update={"include_transactions": True})
    result = USAspendingProvider().retrieve(request)
    assert txn_route.call_count == 1
    kinds = [row["_veda_record_kind"] for row in result.raw_records]
    assert kinds == ["award", "transaction"]
    txn_body = json.loads(txn_route.calls.last.request.content)
    assert txn_body["sort"] == "Transaction Amount"
    assert "Transaction Amount" in txn_body["fields"]
    assert txn_body["filters"]["time_period"][0]["start_date"] == "2023-10-01"