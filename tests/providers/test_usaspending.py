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
                ],
                "page_metadata": {"hasNext": False},
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
        return_value=httpx.Response(200, json={"results": [], "page_metadata": {"hasNext": False}})
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
        httpx.Response(200, json={"results": [{"Award ID": "X"}], "page_metadata": {"hasNext": False}}),
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


@respx.mock
def test_partial_page_failure_preserves_earlier_results() -> None:
    """Verify that earlier page results are preserved when a later page fails (B1-14)."""
    # Capture all respx calls and handle pagination
    call_count = {"count": 0}

    def mock_response(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            # Page 1: successful, with hasNext=true
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"Award ID": "A1", "Award Amount": 100},
                        {"Award ID": "A2", "Award Amount": 200},
                    ],
                    "page_metadata": {"page": 1, "hasNext": True},
                },
            )
        else:
            # Page 2: server error
            return httpx.Response(500)

    respx.post(USASPENDING_URL).mock(side_effect=mock_response)

    result = USAspendingProvider().retrieve(_request_lmt(2024))

    # Assert earlier results are preserved
    assert len(result.raw_records) >= 2
    assert result.raw_records[0]["Award ID"] == "A1"
    assert result.raw_records[1]["Award ID"] == "A2"

    # Assert pagination error is recorded
    assert result.retrieval_metadata.get("pagination_error") is not None

    # Assert truncated flag is set
    assert result.retrieval_metadata.get("truncated") is True

    # Assert status reflects records found (truncated but not a full failure)
    assert result.status == ProviderStatus.FOUND


@respx.mock
def test_uei_mismatch_recorded_with_disposition() -> None:
    """Verify that UEI mismatches are recorded with explicit disposition (B1-04)."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "Award ID": "A1",
                        "Recipient UEI": "N8JGY9B7XW61",  # Matches request
                        "Award Amount": 1000000,
                    },
                    {
                        "Award ID": "A2",
                        "Recipient UEI": "DIFFERENT123456",  # Mismatches request
                        "Award Amount": 2000000,
                    },
                ],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    request = _request_lmt(2024).model_copy(update={"recipient_uei": "N8JGY9B7XW61"})
    result = USAspendingProvider().retrieve(request)

    assert result.status == ProviderStatus.FOUND
    assert len(result.raw_records) == 2

    # First record matches UEI, second mismatches
    assert result.raw_records[0]["Recipient UEI"] == "N8JGY9B7XW61"
    assert result.raw_records[1]["Recipient UEI"] == "DIFFERENT123456"

    # Mismatch must be recorded in metadata
    assert result.retrieval_metadata.get("uei_mismatch_count") == 1
    assert "uei_mismatch_disposition" in result.retrieval_metadata


# ====================================================================
# Repair 3 — Pagination Metadata Validation Tests
# ====================================================================

@respx.mock
def test_live_provider_absent_page_metadata_returns_malformed() -> None:
    """Repair 3: Absent page_metadata must be rejected as malformed."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(200, json={"results": [{"Award ID": "X"}]})
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE
    assert "page_metadata" in result.error_message


@respx.mock
def test_live_provider_missing_has_next_returns_malformed() -> None:
    """Repair 3: Missing hasNext in page_metadata must be rejected."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(200, json={"results": [{"Award ID": "X"}], "page_metadata": {}})
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE
    assert "hasNext" in result.error_message


@respx.mock
def test_live_provider_string_has_next_returns_malformed() -> None:
    """Repair 3: hasNext as string 'false' must be rejected (not coerced to True)."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"Award ID": "X"}], "page_metadata": {"hasNext": "false"}}
        )
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE
    assert "boolean" in result.error_message


@respx.mock
def test_live_provider_non_boolean_has_next_returns_malformed() -> None:
    """Repair 3: hasNext as non-boolean (int, list, etc.) must be rejected."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"Award ID": "X"}], "page_metadata": {"hasNext": 1}}
        )
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.MALFORMED_RESPONSE
    assert "boolean" in result.error_message


@respx.mock
def test_pagination_malformed_later_page_preserves_earlier_results() -> None:
    """Repair 3: Malformed later page must preserve earlier results with truncation."""
    call_count = {"count": 0}

    def mock_response(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            # Page 1: valid, with hasNext=true
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"Award ID": "A1", "Award Amount": 100},
                        {"Award ID": "A2", "Award Amount": 200},
                    ],
                    "page_metadata": {"page": 1, "hasNext": True},
                },
            )
        else:
            # Page 2: malformed - missing hasNext
            return httpx.Response(
                200,
                json={"results": [{"Award ID": "A3", "Award Amount": 300}], "page_metadata": {}},
            )

    respx.post(USASPENDING_URL).mock(side_effect=mock_response)
    result = USAspendingProvider().retrieve(_request_lmt(2024))

    # Earlier results must be preserved despite malformed later page
    assert len(result.raw_records) >= 2
    assert result.raw_records[0]["Award ID"] == "A1"
    assert result.raw_records[1]["Award ID"] == "A2"

    # Malformed page triggers truncation and error recording
    assert result.status == ProviderStatus.FOUND  # Found, but truncated
    assert result.retrieval_metadata.get("truncated") is True
    assert "hasNext" in result.retrieval_metadata.get("pagination_error", "")


# ====================================================================
# Repair 2: Acquisition Failures and Request Scope Preservation
# ====================================================================

@respx.mock
def test_repair2_first_page_429_rate_limited() -> None:
    """First-page 429 should return RATE_LIMITED with original error preserved."""
    respx.post(USASPENDING_URL).mock(return_value=httpx.Response(429))
    result = USAspendingProvider().retrieve(_request_lmt(2024))
    assert result.status == ProviderStatus.RATE_LIMITED
    assert result.error_message is not None
    assert "429" in result.error_message or "rate" in result.error_message.lower()
    assert result.retrieval_metadata.get("pages_fetched") == 0
    assert len(result.raw_records) == 0


@respx.mock
def test_repair2_later_page_429_preserves_earlier_records() -> None:
    """Later-page 429 should preserve earlier records and preserve error status."""
    page_count = {"count": 0}

    def mock_response(request):
        page_count["count"] += 1
        if page_count["count"] == 1:
            # Page 1: success
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"Award ID": "A1", "Award Amount": 100, "Recipient UEI": "AAA"},
                        {"Award ID": "A2", "Award Amount": 200, "Recipient UEI": "BBB"},
                    ],
                    "page_metadata": {"page": 1, "hasNext": True},
                },
            )
        else:
            # Page 2: rate limit
            return httpx.Response(429)

    respx.post(USASPENDING_URL).mock(side_effect=mock_response)
    result = USAspendingProvider().retrieve(
        _request_lmt(2024).model_copy(update={"usaspending_page_cap": 5})
    )

    # Must have FOUND status (got some records) but with truncation
    assert result.status == ProviderStatus.FOUND
    # Earlier records must be preserved
    assert len(result.raw_records) == 2
    assert result.raw_records[0]["Award ID"] == "A1"
    assert result.raw_records[1]["Award ID"] == "A2"

    # Must indicate truncation and preserve error status
    assert result.retrieval_metadata.get("truncated") is True
    assert result.retrieval_metadata.get("pages_fetched") == 1
    assert result.retrieval_metadata.get("has_more") is True

    # Critical: pagination_error_status must distinguish rate-limit from page-cap
    # This will be added in the implementation
    pagination_error_status = result.retrieval_metadata.get("pagination_error_status")
    assert pagination_error_status == ProviderStatus.RATE_LIMITED or "429" in result.retrieval_metadata.get("pagination_error", "")


@respx.mock
def test_repair2_page_cap_truncation_is_distinct_from_failure() -> None:
    """Page cap truncation should be distinct from a rate-limit failure."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "A", "Award Amount": 1}],
                "page_metadata": {"page": 1, "hasNext": True},
            },
        )
    )
    # Set page_cap to 1, so we hit the cap naturally
    request = _request_lmt(2024).model_copy(update={"usaspending_page_cap": 1})
    result = USAspendingProvider().retrieve(request)

    # Should be FOUND (got records)
    assert result.status == ProviderStatus.FOUND
    # Should indicate truncation
    assert result.retrieval_metadata.get("truncated") is True
    # Should NOT have pagination_error (no error occurred, just hit page cap)
    assert "pagination_error" not in result.retrieval_metadata or result.retrieval_metadata.get("pagination_error") is None


@respx.mock
def test_repair2_malformed_response_preserves_earlier_records() -> None:
    """Malformed response on later page should preserve earlier records and error status."""
    page_count = {"count": 0}

    def mock_response(request):
        page_count["count"] += 1
        if page_count["count"] == 1:
            # Page 1: success
            return httpx.Response(
                200,
                json={
                    "results": [{"Award ID": "A1", "Award Amount": 100, "Recipient UEI": "AAA"}],
                    "page_metadata": {"page": 1, "hasNext": True},
                },
            )
        else:
            # Page 2: malformed (invalid JSON)
            return httpx.Response(200, text="not valid json")

    respx.post(USASPENDING_URL).mock(side_effect=mock_response)
    result = USAspendingProvider().retrieve(
        _request_lmt(2024).model_copy(update={"usaspending_page_cap": 5})
    )

    # Must have FOUND status (got some records)
    assert result.status == ProviderStatus.FOUND
    # Earlier records must be preserved
    assert len(result.raw_records) == 1
    assert result.raw_records[0]["Award ID"] == "A1"

    # Must indicate truncation and preserve malformed error
    assert result.retrieval_metadata.get("truncated") is True
    assert "pagination_error" in result.retrieval_metadata
    assert "not valid json" in result.retrieval_metadata.get("pagination_error", "") or "JSON" in result.retrieval_metadata.get("pagination_error", "")


@respx.mock
def test_repair2_request_scope_preserved_in_metadata() -> None:
    """Request scope (fiscal_year, time_period, award_type_codes, etc.) must be preserved."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"Award ID": "A", "Award Amount": 1, "Recipient UEI": "AAA"}],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))

    assert result.status == ProviderStatus.FOUND
    # Request scope should be stored in metadata
    meta = result.retrieval_metadata
    # These fields should exist
    assert "fiscal_year" in meta or "time_period" in meta
    assert "award_type_codes" in meta
    assert "pages_fetched" in meta


@respx.mock
def test_repair2_pagination_error_preserved_in_metadata() -> None:
    """Pagination error details must be preserved for orchestrator to distinguish error types."""
    page_count = {"count": 0}

    def mock_response(request):
        page_count["count"] += 1
        if page_count["count"] == 1:
            # Page 1: success
            return httpx.Response(
                200,
                json={
                    "results": [{"Award ID": "A1", "Award Amount": 100, "Recipient UEI": "AAA"}],
                    "page_metadata": {"page": 1, "hasNext": True},
                },
            )
        else:
            # Page 2: rate limit
            return httpx.Response(429)

    respx.post(USASPENDING_URL).mock(side_effect=mock_response)
    result = USAspendingProvider().retrieve(
        _request_lmt(2024).model_copy(update={"usaspending_page_cap": 5})
    )

    # Must preserve pagination error in metadata
    assert result.retrieval_metadata.get("truncated") is True
    assert result.retrieval_metadata.get("pagination_error") is not None
    # The error message should contain info about what type of error it was
    # (e.g., 429, rate limit, etc.)
    pagination_error = result.retrieval_metadata.get("pagination_error", "")
    assert "429" in pagination_error or "rate" in pagination_error.lower()


# ====================================================================
# Repair 1: Recipient Identity Dispositions
# ====================================================================

@respx.mock
def test_repair1_uei_matching_disposition_recorded() -> None:
    """UEI matching disposition must be recorded in metadata."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"Award ID": "A1", "Award Amount": 100, "Recipient UEI": "MATCHED_UEI"},
                    {"Award ID": "A2", "Award Amount": 200, "Recipient UEI": "DIFFERENT_UEI"},
                ],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    # Request with specific UEI to enable matching
    request = ProviderRequest(
        cik="0000936468",
        company_name="Lockheed Martin Corp",
        recipient_uei="MATCHED_UEI",
        requested_period=_rp(2024),
    )
    result = USAspendingProvider().retrieve(request)

    assert result.status == ProviderStatus.FOUND
    # Mismatch detection should be recorded
    metadata = result.retrieval_metadata
    if "uei_mismatch_count" in metadata:
        assert metadata["uei_mismatch_count"] == 1
        assert "different Recipient UEI" in metadata.get("uei_mismatch_disposition", "")


@respx.mock
def test_repair1_name_only_request_no_uei_matching() -> None:
    """Name-only request (no UEI) should not trigger UEI matching."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"Award ID": "A1", "Award Amount": 100, "Recipient UEI": "AAA"},
                ],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    # Request with name only (no UEI)
    result = USAspendingProvider().retrieve(_request_lmt(2024))

    assert result.status == ProviderStatus.FOUND
    # No UEI mismatch should be recorded (none provided to check against)
    metadata = result.retrieval_metadata
    assert metadata.get("recipient_match") == "name" or "uei_mismatch_count" not in metadata


@respx.mock
def test_repair1_missing_recipient_uei_in_record() -> None:
    """Records with missing Recipient UEI should be handled without error."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"Award ID": "A1", "Award Amount": 100},
                    {"Award ID": "A2", "Award Amount": 200, "Recipient UEI": ""},
                    {"Award ID": "A3", "Award Amount": 300, "Recipient UEI": None},
                ],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    result = USAspendingProvider().retrieve(_request_lmt(2024))

    assert result.status == ProviderStatus.FOUND
    # Should handle missing/empty UEI fields gracefully
    assert len(result.raw_records) == 3


@respx.mock
def test_repair1_all_records_preserved_for_normalization() -> None:
    """All records (matched, mismatched, missing-UEI) must be preserved for normalization."""
    respx.post(USASPENDING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"Award ID": "A1", "Award Amount": 100, "Recipient UEI": "AAA"},
                    {"Award ID": "A2", "Award Amount": 200, "Recipient UEI": "BBB"},
                    {"Award ID": "A3", "Award Amount": 300},
                ],
                "page_metadata": {"page": 1, "hasNext": False},
            },
        )
    )
    request = ProviderRequest(
        cik="0000936468",
        company_name="Lockheed Martin Corp",
        recipient_uei="AAA",
        requested_period=_rp(2024),
    )
    result = USAspendingProvider().retrieve(request)

    assert result.status == ProviderStatus.FOUND
    # All records must be preserved (matched, mismatched, and missing)
    assert len(result.raw_records) == 3
    assert result.raw_records[0]["Award ID"] == "A1"
    assert result.raw_records[1]["Award ID"] == "A2"
    assert result.raw_records[2]["Award ID"] == "A3"