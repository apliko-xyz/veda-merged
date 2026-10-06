"""
File: src/veda/providers/annual_reports.py
Title: Annual Report Fixture Provider
Layer: Provider retrieval layer
Status: Merged prototype foundation — Phase 4

Purpose
-------
Provides deterministic annual-report passages keyed by company and
fiscal year.

Public classes
--------------
FixtureAnnualReportProvider

Network policy
--------------
This phase is fixture-only. No live annual-report provider exists and
this module performs no network I/O.
"""

from __future__ import annotations

from veda.providers.base import EvidenceProvider
from veda.providers.results import ProviderRequest, ProviderResult
from veda.shared.enums import ProviderStatus, SourceType


VERSION = "1"


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

    if (request.entity_id or "").endswith(":0008888888"):
        return "example vendor holdings, inc."

    return None


class FixtureAnnualReportProvider(EvidenceProvider):
    @property
    def source_type(self) -> SourceType:
        return SourceType.ANNUAL_REPORT

    @property
    def source_name(self) -> str:
        return "Annual Reports (fixture)"

    @property
    def is_fixture(self) -> bool:
        return True

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        import veda.providers.fixtures as fixtures

        key = _fixture_key(request)
        fiscal_year = request.requested_period.fiscal_year

        if key is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=(
                    "FixtureAnnualReportProvider requires company_name "
                    "or a supported entity_id"
                ),
            )

        passages = fixtures.ANNUAL_REPORT_PASSAGES_BY_NAME.get(key, [])
        filtered = [
            record
            for record in passages
            if record.get("fiscal_year") == fiscal_year
        ]

        if not filtered:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=(
                    f"No fixture annual-report passages for {key!r} "
                    f"in fiscal year {fiscal_year}"
                ),
                metadata={"fiscal_year": fiscal_year},
            )

        return _result(
            self,
            ProviderStatus.FOUND,
            raw_records=filtered,
            metadata={"fiscal_year": fiscal_year},
        )
