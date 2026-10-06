"""
File: src/veda/providers/usaspending.py
Title: USAspending Providers
Layer: Provider retrieval layer
Status: Phase 9 readiness — award search scoped to the documented contract

Purpose
-------
Retrieves USAspending award records for a recipient and a federal
fiscal year. The live provider posts to the documented
``spending_by_award`` endpoint. Filters, fields, and pagination follow
that contract. A transaction-level search is available behind
``ProviderRequest.include_transactions`` and is off by default.

Public classes
--------------
USAspendingProvider
FixtureUSAspendingProvider

Network policy
--------------
The live provider does not send ``fiscal_year`` as a query parameter.
It retries only once per page for timeouts and HTTP 5xx responses.
The fixture provider performs no network I/O.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from veda.providers.base import EvidenceProvider
from veda.providers.results import ProviderRequest, ProviderResult
from veda.shared.enums import ProviderStatus, SourceType
from veda.shared.periods import Period


VERSION = "1"

USASPENDING_SEARCH_URL = (
    "https://api.usaspending.gov/api/v2/search/spending_by_award/"
)
USASPENDING_TRANSACTION_URL = (
    "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
)
TIMEOUT_SECONDS = 30.0
PAGE_LIMIT = 100

# Documented contract-award type codes (A BPA, B purchase order,
# C delivery order, D definitive contract). Not assistance or IDV codes.
CONTRACT_AWARD_TYPE_CODES = ["A", "B", "C", "D"]

AWARD_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Award Amount",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Start Date",
    "End Date",
    "generated_internal_id",
]

# Field names copied from the spending_by_transaction contract.
TRANSACTION_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Transaction Amount",
    "Action Date",
    "Awarding Agency",
    "Awarding Sub Agency",
    "generated_internal_id",
    "Mod",
]

DLA_AWARDING_SUBTIER = {
    "type": "awarding",
    "tier": "subtier",
    "name": "Defense Logistics Agency",
    "toptier_name": "Department of Defense",
}

NAME_MATCH_NOTE = (
    "Recipient match is name-based (recipient_search_text). "
    "It may include affiliates and other entities that share the name."
)

TRANSACTION_GAP_NOTE = (
    "Period-scoped obligations are not computed from Award Amount. "
    "The documented transaction endpoint is "
    "POST /api/v2/search/spending_by_transaction/. "
    "It was not requested for this run."
)


def federal_fy_bounds(fiscal_year: int) -> dict[str, str]:
    """Return the documented time_period object for one federal FY."""
    period = Period.federal_fiscal_year(fiscal_year)
    assert period.start is not None and period.end is not None
    return {
        "start_date": period.start.isoformat(),
        "end_date": period.end.isoformat(),
    }


def build_award_search_body(request: ProviderRequest, *, page: int) -> dict[str, Any]:
    """
    Build a spending_by_award request body from documented fields only.

    ``fiscal_year`` is not a parameter of this endpoint. The federal
    fiscal year is expressed as ``filters.time_period``.
    """
    fiscal_year = request.requested_period.fiscal_year
    uei = (request.recipient_uei or "").strip()
    if uei:
        recipient_values = [uei]
    else:
        recipient_values = [request.company_name or request.entity_id or ""]

    filters: dict[str, Any] = {
        "time_period": [federal_fy_bounds(fiscal_year)],
        "award_type_codes": list(CONTRACT_AWARD_TYPE_CODES),
        "recipient_search_text": recipient_values,
    }
    if request.filter_awarding_subtier_dla:
        filters["agencies"] = [dict(DLA_AWARDING_SUBTIER)]

    return {
        "filters": filters,
        "fields": list(AWARD_FIELDS),
        "page": page,
        "limit": PAGE_LIMIT,
        "sort": "Award Amount",
        "order": "desc",
        "subawards": False,
    }


def build_transaction_search_body(
    request: ProviderRequest,
    *,
    page: int,
) -> dict[str, Any]:
    """
    Build a spending_by_transaction request body.

    Field names and the required ``award_type_codes`` filter are taken
    from the published contract. This body is used only when
    ``include_transactions`` is true.
    """
    award_body = build_award_search_body(request, page=page)
    return {
        "filters": award_body["filters"],
        "fields": list(TRANSACTION_FIELDS),
        "page": page,
        "limit": PAGE_LIMIT,
        "sort": "Transaction Amount",
        "order": "desc",
    }


def _result(
    provider: EvidenceProvider,
    status: ProviderStatus,
    *,
    raw_records: list[dict] | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> ProviderResult:
    return ProviderResult(
        status=status,
        source_type=provider.source_type,
        source_name=provider.source_name,
        is_fixture=provider.is_fixture,
        raw_records=raw_records or [],
        error_message=error_message,
        retrieval_metadata=metadata or {},
    )


def _fixture_key(request: ProviderRequest) -> str | None:
    if request.company_name and request.company_name.strip():
        return request.company_name.strip().lower()

    if (request.entity_id or "").endswith(":0000936468"):
        return "lockheed martin corp"

    return None


def _recipient_metadata(request: ProviderRequest) -> dict[str, Any]:
    uei = (request.recipient_uei or "").strip()
    if uei:
        return {
            "recipient_match": "uei",
            "recipient_uei": uei,
        }
    return {
        "recipient_match": "name",
        "recipient_match_note": NAME_MATCH_NOTE,
    }


def _base_metadata(request: ProviderRequest) -> dict[str, Any]:
    fiscal_year = request.requested_period.fiscal_year
    metadata: dict[str, Any] = {
        "fiscal_year": fiscal_year,
        "time_period": federal_fy_bounds(fiscal_year),
        "award_type_codes": list(CONTRACT_AWARD_TYPE_CODES),
        "dla_subtier_filter": bool(request.filter_awarding_subtier_dla),
        "page_cap": request.usaspending_page_cap,
        "pages_fetched": 0,
        "has_more": False,
        "truncated": False,
        "include_transactions": bool(request.include_transactions),
        "transaction_endpoint": USASPENDING_TRANSACTION_URL,
        "transaction_endpoint_verified": True,
    }
    metadata.update(_recipient_metadata(request))
    if not request.include_transactions:
        metadata["transaction_gap"] = TRANSACTION_GAP_NOTE
    return metadata


class _PageFetch:
    """One page outcome. ``fatal`` ends the whole retrieval."""

    def __init__(
        self,
        *,
        records: list[dict] | None = None,
        has_next: bool = False,
        fatal_status: ProviderStatus | None = None,
        fatal_message: str | None = None,
    ) -> None:
        self.records = records or []
        self.has_next = has_next
        self.fatal_status = fatal_status
        self.fatal_message = fatal_message


def _decode_page(response: httpx.Response) -> _PageFetch:
    """Turn one HTTP response into a page outcome. No network I/O."""
    if response.status_code == 404:
        return _PageFetch(
            fatal_status=ProviderStatus.NOT_FOUND,
            fatal_message="USAspending endpoint returned 404",
        )
    if response.status_code == 429:
        return _PageFetch(
            fatal_status=ProviderStatus.RATE_LIMITED,
            fatal_message="USAspending rate limit (429)",
        )
    if 400 <= response.status_code < 500:
        return _PageFetch(
            fatal_status=ProviderStatus.SOURCE_UNAVAILABLE,
            fatal_message=f"USAspending returned HTTP {response.status_code}",
        )
    if response.status_code != 200:
        return _PageFetch(
            fatal_status=ProviderStatus.SOURCE_UNAVAILABLE,
            fatal_message=f"USAspending returned HTTP {response.status_code}",
        )

    try:
        body = response.json()
    except ValueError:
        return _PageFetch(
            fatal_status=ProviderStatus.MALFORMED_RESPONSE,
            fatal_message="USAspending response was not valid JSON",
        )
    if not isinstance(body, dict):
        return _PageFetch(
            fatal_status=ProviderStatus.MALFORMED_RESPONSE,
            fatal_message="USAspending response was not a JSON object",
        )
    records = body.get("results", [])
    if not isinstance(records, list):
        return _PageFetch(
            fatal_status=ProviderStatus.MALFORMED_RESPONSE,
            fatal_message="USAspending 'results' was not a list",
        )
    page_metadata = body.get("page_metadata")
    has_next = False
    if isinstance(page_metadata, dict):
        has_next = bool(page_metadata.get("hasNext"))
    return _PageFetch(records=records, has_next=has_next)


def _post_page(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
) -> _PageFetch:
    """POST one page, retrying once on timeout or HTTP 5xx."""
    last_message = "USAspending source unavailable after retry"
    saw_retryable = False

    for attempt in range(2):
        try:
            response = client.post(url, json=payload)
        except httpx.TimeoutException:
            last_message = "USAspending request timed out"
            saw_retryable = True
            if attempt == 0:
                continue
            break
        except httpx.HTTPError as exc:
            return _PageFetch(
                fatal_status=ProviderStatus.SOURCE_UNAVAILABLE,
                fatal_message=f"USAspending transport error: {exc}",
            )

        if response.status_code >= 500:
            last_message = f"USAspending returned HTTP {response.status_code}"
            saw_retryable = True
            if attempt == 0:
                continue
            break

        decoded = _decode_page(response)
        if (
            decoded.fatal_status == ProviderStatus.SOURCE_UNAVAILABLE
            and response.status_code >= 500
        ):
            continue
        return decoded

    if saw_retryable:
        return _PageFetch(
            fatal_status=ProviderStatus.SOURCE_UNAVAILABLE,
            fatal_message=last_message,
        )
    return _PageFetch(
        fatal_status=ProviderStatus.SOURCE_UNAVAILABLE,
        fatal_message=last_message,
    )


def _paginate(
    client: httpx.Client,
    url: str,
    body_for_page,
    page_cap: int,
    *,
    record_kind: str,
) -> tuple[list[dict], dict[str, Any], _PageFetch | None]:
    """
    Walk pages until ``hasNext`` is false or ``page_cap`` is reached.

    Returns records, pagination metadata, and a fatal page when the
    endpoint itself failed. An empty last page is not fatal.
    """
    collected: list[dict] = []
    pages_fetched = 0
    has_more = False
    truncated = False
    stopped_on_has_next_false = False

    for page in range(1, page_cap + 1):
        fetched = _post_page(client, url, body_for_page(page))
        if fetched.fatal_status is not None:
            if collected and fetched.fatal_status != ProviderStatus.NOT_FOUND:
                # A later page failed after earlier pages succeeded.
                # Keep the records and record the failure as truncation
                # metadata rather than dropping the pages already accepted.
                has_more = True
                truncated = True
                return collected, {
                    "pages_fetched": pages_fetched,
                    "has_more": has_more,
                    "truncated": truncated,
                    "pagination_error": fetched.fatal_message,
                }, None
            return [], {
                "pages_fetched": pages_fetched,
                "has_more": False,
                "truncated": False,
            }, fetched

        pages_fetched += 1
        for record in fetched.records:
            if isinstance(record, dict):
                stamped = dict(record)
                stamped["_veda_record_kind"] = record_kind
                collected.append(stamped)
            else:
                collected.append({
                    "_veda_record_kind": record_kind,
                    "_veda_unparsed": True,
                    "_veda_raw": record,
                })

        if not fetched.has_next:
            stopped_on_has_next_false = True
            has_more = False
            truncated = False
            break

        if page >= page_cap:
            has_more = True
            truncated = True
            break

    return collected, {
        "pages_fetched": pages_fetched,
        "has_more": has_more,
        "truncated": truncated,
        "stopped_on_has_next_false": stopped_on_has_next_false,
    }, None


class USAspendingProvider(EvidenceProvider):
    @property
    def source_type(self) -> SourceType:
        return SourceType.USASPENDING

    @property
    def source_name(self) -> str:
        return "USAspending"

    @property
    def is_fixture(self) -> bool:
        return False

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        fiscal_year = request.requested_period.fiscal_year
        metadata = _base_metadata(request)
        page_cap = request.usaspending_page_cap

        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            records, page_meta, fatal = _paginate(
                client,
                USASPENDING_SEARCH_URL,
                lambda page: build_award_search_body(request, page=page),
                page_cap,
                record_kind="award",
            )
            metadata.update(page_meta)

            if fatal is not None and fatal.fatal_status is not None:
                return _result(
                    self,
                    fatal.fatal_status,
                    error_message=fatal.fatal_message,
                    metadata=metadata,
                )

            if request.include_transactions:
                txn_records, txn_meta, txn_fatal = _paginate(
                    client,
                    USASPENDING_TRANSACTION_URL,
                    lambda page: build_transaction_search_body(request, page=page),
                    page_cap,
                    record_kind="transaction",
                )
                metadata["transaction_pages_fetched"] = txn_meta.get("pages_fetched", 0)
                metadata["transaction_truncated"] = bool(txn_meta.get("truncated"))
                metadata["transaction_gap"] = (
                    "Transaction rows were requested from "
                    "POST /api/v2/search/spending_by_transaction/. "
                    "Each row is a transaction amount, not a summed "
                    "period obligation."
                )
                if txn_fatal is not None and txn_fatal.fatal_message:
                    metadata["transaction_error"] = txn_fatal.fatal_message
                else:
                    records.extend(txn_records)
                    if txn_meta.get("truncated"):
                        metadata["truncated"] = True
                        metadata["has_more"] = True

        if not records:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=f"No awards for federal fiscal year {fiscal_year}",
                metadata=metadata,
            )

        return _result(
            self,
            ProviderStatus.FOUND,
            raw_records=records,
            metadata=metadata,
        )


class FixtureUSAspendingProvider(EvidenceProvider):
    @property
    def source_type(self) -> SourceType:
        return SourceType.USASPENDING

    @property
    def source_name(self) -> str:
        return "USAspending (fixture)"

    @property
    def is_fixture(self) -> bool:
        return True

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        import veda.providers.fixtures as fixtures

        key = _fixture_key(request)
        fiscal_year = request.requested_period.fiscal_year
        metadata = _base_metadata(request)

        if key is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=(
                    "FixtureUSAspendingProvider requires company_name "
                    "or a supported entity_id"
                ),
                metadata=metadata,
            )

        awards = fixtures.USASPENDING_AWARDS_BY_NAME.get(key, [])
        filtered = [
            dict(record)
            for record in awards
            if record.get("fiscal_year") == fiscal_year
        ]
        for record in filtered:
            record["_veda_record_kind"] = "award"

        metadata["pages_fetched"] = 1 if filtered else 0
        metadata["has_more"] = False
        metadata["truncated"] = False
        metadata["stopped_on_has_next_false"] = True

        if not filtered:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=(
                    f"No fixture awards for {key!r} "
                    f"in fiscal year {fiscal_year}"
                ),
                metadata=metadata,
            )

        return _result(
            self,
            ProviderStatus.FOUND,
            raw_records=filtered,
            metadata=metadata,
        )


def canonical_award_payload(record: dict[str, Any]) -> str:
    """Stable JSON for hashing an award. Excludes retrieval timestamps."""
    keys = [
        "Award ID",
        "Recipient Name",
        "Recipient UEI",
        "Award Amount",
        "Awarding Agency",
        "Awarding Sub Agency",
        "Start Date",
        "End Date",
        "generated_internal_id",
        "Transaction Amount",
        "Action Date",
        "Mod",
        "_veda_record_kind",
    ]
    payload = {key: record.get(key) for key in keys if key in record}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "VERSION",
    "USASPENDING_SEARCH_URL",
    "USASPENDING_TRANSACTION_URL",
    "AWARD_FIELDS",
    "CONTRACT_AWARD_TYPE_CODES",
    "DLA_AWARDING_SUBTIER",
    "NAME_MATCH_NOTE",
    "TRANSACTION_GAP_NOTE",
    "USAspendingProvider",
    "FixtureUSAspendingProvider",
    "build_award_search_body",
    "build_transaction_search_body",
    "federal_fy_bounds",
]
