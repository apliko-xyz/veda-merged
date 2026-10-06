"""
File: src/veda/providers/sec_company_facts.py
Title: SEC Company Facts Providers
Layer: Provider retrieval layer
Status: Merged prototype foundation — Phase 4

Purpose
-------
Retrieves raw SEC Company Facts data by CIK through either the live SEC
endpoint or deterministic fixture data.

Public classes
--------------
SECCompanyFactsProvider
FixtureSECCompanyFactsProvider

Network policy
--------------
The live provider uses data.sec.gov, requires a non-empty User-Agent,
and retries only once for timeouts and HTTP 5xx responses. The fixture
provider performs no network I/O and lazily imports fixture data.
"""

from __future__ import annotations

from typing import Optional

import httpx

from veda.providers.base import EvidenceProvider
from veda.providers.results import ProviderRequest, ProviderResult
from veda.shared.enums import ProviderStatus, SourceType


VERSION = "1"

SEC_COMPANY_FACTS_URL = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)
TIMEOUT_SECONDS = 30.0


def _normalize_cik(value: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.isdigit() or len(text) > 10:
        return None
    return text.zfill(10)


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


class SECCompanyFactsProvider(EvidenceProvider):
    def __init__(self, user_agent: str) -> None:
        if not isinstance(user_agent, str):
            raise ValueError(
                "SECCompanyFactsProvider requires a non-empty user_agent"
            )
        stripped = user_agent.strip()
        if not stripped:
            raise ValueError(
                "SEC Company Facts requires a User-Agent of the form "
                "'Name email@example.com'. The supplied value is empty. "
                "SEC rejects requests without a descriptive User-Agent."
            )
        if "@" not in stripped or "." not in stripped.split("@")[-1]:
            raise ValueError(
                "SEC Company Facts requires a User-Agent that contains a "
                "name and a contact email (e.g. 'Jane Doe jane@example.com'). "
                f"The supplied value {stripped!r} does not appear to contain "
                "an email address. SEC rejects requests without one."
            )
        self._user_agent = stripped

    @property
    def source_type(self) -> SourceType:
        return SourceType.SEC_COMPANY_FACTS

    @property
    def source_name(self) -> str:
        return "SEC Company Facts"

    @property
    def is_fixture(self) -> bool:
        return False

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        cik = _normalize_cik(request.cik or "")
        if cik is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message="A valid numeric CIK is required",
            )

        url = SEC_COMPANY_FACTS_URL.format(cik=cik)
        headers = {"User-Agent": self._user_agent}
        last_error = "SEC source unavailable after retry"
        last_status: int | None = None

        for attempt in range(2):
            try:
                with httpx.Client(
                    headers=headers,
                    timeout=TIMEOUT_SECONDS,
                ) as client:
                    response = client.get(url)

                last_status = response.status_code

                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        return _result(
                            self,
                            ProviderStatus.MALFORMED_RESPONSE,
                            error_message="SEC Company Facts response was not valid JSON",
                            metadata={"url": url, "status_code": 200},
                        )

                    if not isinstance(payload, dict):
                        return _result(
                            self,
                            ProviderStatus.MALFORMED_RESPONSE,
                            error_message="SEC Company Facts response was not a JSON object",
                            metadata={"url": url, "status_code": 200},
                        )

                    return _result(
                        self,
                        ProviderStatus.FOUND,
                        raw_records=[payload],
                        metadata={"url": url, "status_code": 200},
                    )

                if response.status_code == 404:
                    return _result(
                        self,
                        ProviderStatus.NOT_FOUND,
                        error_message=f"No SEC Company Facts for CIK {cik}",
                        metadata={"url": url, "status_code": 404},
                    )

                if response.status_code == 429:
                    return _result(
                        self,
                        ProviderStatus.RATE_LIMITED,
                        error_message="SEC rate limit (429)",
                        metadata={"url": url, "status_code": 429},
                    )

                if 400 <= response.status_code < 500:
                    return _result(
                        self,
                        ProviderStatus.SOURCE_UNAVAILABLE,
                        error_message=f"SEC returned HTTP {response.status_code}",
                        metadata={"url": url, "status_code": response.status_code},
                    )

                last_error = f"SEC returned HTTP {response.status_code}"
                if attempt == 0:
                    continue

            except httpx.TimeoutException:
                last_error = "SEC request timed out"
                if attempt == 0:
                    continue
            except httpx.HTTPError as exc:
                last_error = f"SEC transport error: {exc}"
                break

        return _result(
            self,
            ProviderStatus.SOURCE_UNAVAILABLE,
            error_message=last_error,
            metadata={"url": url, "last_status_code": last_status},
        )


class FixtureSECCompanyFactsProvider(EvidenceProvider):
    @property
    def source_type(self) -> SourceType:
        return SourceType.SEC_COMPANY_FACTS

    @property
    def source_name(self) -> str:
        return "SEC Company Facts (fixture)"

    @property
    def is_fixture(self) -> bool:
        return True

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        import veda.providers.fixtures as fixtures

        cik = _normalize_cik(request.cik or "")
        if cik is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message="A valid numeric CIK is required",
            )

        payload = fixtures.SEC_COMPANY_FACTS_BY_CIK.get(cik)
        if payload is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=f"No fixture Company Facts for CIK {cik}",
            )

        return _result(
            self,
            ProviderStatus.FOUND,
            raw_records=[payload],
        )
