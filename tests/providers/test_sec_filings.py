# filename: tests/providers/test_sec_filings.py
# title: Provider Layer - SEC Filings Tests
# layer: Test suite - providers
# status: Phase 1-6 test recovery
# description:
#     Verifies both SEC Filings providers: the fixture provider
#     (deterministic, no network) and the live provider (networked,
#     mocked via respx).
#
#     The SEC Filings provider is different from SEC Company Facts:
#     it requires a specific filing to be identified by accession
#     number, form, and passage hint. Without any of those, it
#     returns NOT_FOUND immediately.
#
# source:
#     AUTHORED - Phase 4 had no saved test before recovery began.
#     The providers in src/veda/providers/sec_filings.py are the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - The live provider reads submissions JSON, then fetches
#       primaryDocument. The EDGAR index page is not filing content.
#         submissions: https://data.sec.gov/submissions/CIK{cik10}.json
#         document:    https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{primaryDocument}

from __future__ import annotations

import httpx
import pytest
import respx

from veda.providers.results import ProviderRequest
from veda.providers.sec_filings import (
    FixtureSECFilingsProvider,
    SECFilingsProvider,
)
from veda.shared.enums import ProviderStatus, SourceType
from veda.shared.periods import RequestedPeriod


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
LOCKHEED_CIK = "0000936468"
LOCKHEED_ACCN = "0000936468-25-000009"
LOCKHEED_FORM = "10-K"
LOCKHEED_HINT = "revenues"


def _rp() -> RequestedPeriod:
    return RequestedPeriod(fiscal_year=2024, raw="2024")


def _request_full() -> ProviderRequest:
    return ProviderRequest(
        cik=LOCKHEED_CIK,
        company_name="Lockheed Martin Corp",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        filing_form=LOCKHEED_FORM,
        field_or_passage_hint=LOCKHEED_HINT,
    )


PRIMARY_DOCUMENT = "lmt-20241231.htm"
FILING_HTML = (
    "<html><body><p>Total revenues were $71,043 million "
    "for the year ended December 31, 2024.</p></body></html>"
)
INDEX_HTML = (
    "<html><head><title>EDGAR Filing Documents</title></head>"
    "<body><h1>Filing Detail</h1>"
    "<p>Document Format Files</p></body></html>"
)


def _submissions_url() -> str:
    return "https://data.sec.gov/submissions/CIK0000936468.json"


def _document_url() -> str:
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        "936468/000093646825000009/lmt-20241231.htm"
    )


def _submissions_payload(
    primary: str = PRIMARY_DOCUMENT,
    accession: str = LOCKHEED_ACCN,
) -> dict:
    return {
        "filings": {
            "recent": {
                "accessionNumber": [accession, "0000936468-24-000001"],
                "primaryDocument": [primary, "older.htm"],
                "form": ["10-K", "10-K"],
            }
        }
    }


def _mock_found(primary_text: str = FILING_HTML, primary: str = PRIMARY_DOCUMENT):
    respx.get(_submissions_url()).mock(
        return_value=httpx.Response(200, json=_submissions_payload(primary))
    )
    return respx.get(_document_url() if primary == PRIMARY_DOCUMENT else _document_url().rsplit("/", 1)[0] + "/" + primary).mock(
        return_value=httpx.Response(200, text=primary_text)
    )


# ====================================================================
# 1. Fixture provider
# ====================================================================

def test_fixture_provider_source_type() -> None:
    p = FixtureSECFilingsProvider()
    assert p.source_type == SourceType.SEC_FILING


def test_fixture_provider_is_fixture_true() -> None:
    p = FixtureSECFilingsProvider()
    assert p.is_fixture is True


def test_fixture_provider_returns_passage() -> None:
    p = FixtureSECFilingsProvider()
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.FOUND
    assert len(result.raw_records) == 1
    assert "text" in result.raw_records[0]


def test_fixture_provider_missing_cik_returns_not_found() -> None:
    p = FixtureSECFilingsProvider()
    req = ProviderRequest(
        company_name="X",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        filing_form=LOCKHEED_FORM,
        field_or_passage_hint=LOCKHEED_HINT,
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_missing_accession_returns_not_found() -> None:
    p = FixtureSECFilingsProvider()
    req = ProviderRequest(
        cik=LOCKHEED_CIK,
        company_name="X",
        requested_period=_rp(),
        filing_form=LOCKHEED_FORM,
        field_or_passage_hint=LOCKHEED_HINT,
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_missing_form_returns_not_found() -> None:
    p = FixtureSECFilingsProvider()
    req = ProviderRequest(
        cik=LOCKHEED_CIK,
        company_name="X",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        field_or_passage_hint=LOCKHEED_HINT,
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_missing_hint_returns_not_found() -> None:
    p = FixtureSECFilingsProvider()
    req = ProviderRequest(
        cik=LOCKHEED_CIK,
        company_name="X",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        filing_form=LOCKHEED_FORM,
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


def test_fixture_provider_unknown_passage_returns_not_found() -> None:
    p = FixtureSECFilingsProvider()
    req = ProviderRequest(
        cik=LOCKHEED_CIK,
        company_name="X",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        filing_form=LOCKHEED_FORM,
        field_or_passage_hint="nonexistent_passage",
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


# ====================================================================
# 2. Live provider constructor
# ====================================================================

def test_live_provider_requires_user_agent() -> None:
    with pytest.raises(ValueError):
        SECFilingsProvider(user_agent="")


def test_live_provider_source_type() -> None:
    p = SECFilingsProvider(user_agent="Test test@example.com")
    assert p.source_type == SourceType.SEC_FILING


def test_live_provider_is_fixture_false() -> None:
    p = SECFilingsProvider(user_agent="Test test@example.com")
    assert p.is_fixture is False


# ====================================================================
# 3. Live provider - missing identifiers
# ====================================================================

def test_live_provider_missing_cik_returns_not_found() -> None:
    p = SECFilingsProvider(user_agent="Test test@example.com")
    req = ProviderRequest(
        company_name="X",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        filing_form=LOCKHEED_FORM,
        field_or_passage_hint=LOCKHEED_HINT,
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


def test_live_provider_invalid_cik_returns_not_found() -> None:
    p = SECFilingsProvider(user_agent="Test test@example.com")
    req = ProviderRequest(
        cik="abc",
        company_name="X",
        requested_period=_rp(),
        accession_number=LOCKHEED_ACCN,
        filing_form=LOCKHEED_FORM,
        field_or_passage_hint=LOCKHEED_HINT,
    )
    result = p.retrieve(req)
    assert result.status == ProviderStatus.NOT_FOUND


# ====================================================================
# 4. Live provider - HTTP responses
# ====================================================================

@respx.mock
def test_live_provider_200_returns_primary_document() -> None:
    _mock_found()
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.FOUND
    assert "Total revenues" in result.raw_records[0]["text"]
    assert result.raw_records[0]["url"] == _document_url()
    assert result.retrieval_metadata["primary_document"] == PRIMARY_DOCUMENT
    assert result.retrieval_metadata["document_kind"] == "primary"
    assert "-index.htm" not in result.raw_records[0]["url"]


@respx.mock
def test_live_provider_empty_body_returns_malformed_response() -> None:
    respx.get(_submissions_url()).mock(
        return_value=httpx.Response(200, json=_submissions_payload())
    )
    respx.get(_document_url()).mock(return_value=httpx.Response(200, text=""))
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.MALFORMED_RESPONSE


@respx.mock
def test_live_provider_index_html_is_not_filing_content() -> None:
    respx.get(_submissions_url()).mock(
        return_value=httpx.Response(200, json=_submissions_payload())
    )
    respx.get(_document_url()).mock(return_value=httpx.Response(200, text=INDEX_HTML))
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.MALFORMED_RESPONSE
    assert result.raw_records == []


@respx.mock
def test_live_provider_rejects_index_primary_document_name() -> None:
    respx.get(_submissions_url()).mock(
        return_value=httpx.Response(
            200,
            json=_submissions_payload(primary="0000936468-25-000009-index.htm"),
        )
    )
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.MALFORMED_RESPONSE
    assert result.raw_records == []


@respx.mock
def test_live_provider_404_returns_not_found() -> None:
    route = respx.get(_submissions_url()).mock(return_value=httpx.Response(404))
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.NOT_FOUND
    assert route.call_count == 1


@respx.mock
def test_live_provider_404_not_retried() -> None:
    route = respx.get(_submissions_url()).mock(return_value=httpx.Response(404))
    p = SECFilingsProvider(user_agent="Test test@example.com")
    p.retrieve(_request_full())
    assert route.call_count == 1


@respx.mock
def test_live_provider_429_returns_rate_limited() -> None:
    respx.get(_submissions_url()).mock(return_value=httpx.Response(429))
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.RATE_LIMITED


@respx.mock
def test_live_provider_500_retries_once() -> None:
    route = respx.get(_submissions_url()).mock(return_value=httpx.Response(500))
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.SOURCE_UNAVAILABLE
    assert route.call_count == 2


@respx.mock
def test_live_provider_500_then_200_succeeds() -> None:
    route = respx.get(_submissions_url())
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json=_submissions_payload()),
    ]
    respx.get(_document_url()).mock(
        return_value=httpx.Response(200, text=FILING_HTML)
    )
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.FOUND
    assert route.call_count == 2


@respx.mock
def test_live_provider_timeout_retries_once() -> None:
    route = respx.get(_submissions_url())
    route.side_effect = [
        httpx.TimeoutException("timeout"),
        httpx.TimeoutException("timeout"),
    ]
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.status == ProviderStatus.SOURCE_UNAVAILABLE
    assert route.call_count == 2


@respx.mock
def test_live_provider_record_contains_accession() -> None:
    _mock_found()
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.raw_records[0]["accession_number"] == LOCKHEED_ACCN


@respx.mock
def test_live_provider_record_contains_form() -> None:
    _mock_found()
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.raw_records[0]["filing_form"] == LOCKHEED_FORM


@respx.mock
def test_live_provider_record_contains_url() -> None:
    _mock_found()
    p = SECFilingsProvider(user_agent="Test test@example.com")
    result = p.retrieve(_request_full())
    assert result.raw_records[0]["url"] == _document_url()