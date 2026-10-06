"""
File: src/veda/providers/results.py
Title: Provider Request and Result Models
Layer: Provider retrieval layer
Status: Merged prototype foundation — Phase 4

Purpose
-------
Defines the typed request and result models crossing the provider
boundary.

Public API
----------
ProviderRequest
ProviderResult

Contract
--------
Reuses RequestedPeriod, ProviderStatus, and SourceType from Phase 2.
ProviderResult enforces explicit status, record, and error invariants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from veda.shared.enums import ProviderStatus, SourceType
from veda.shared.periods import RequestedPeriod


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderRequest(BaseModel):
    entity_id: Optional[str] = None
    cik: Optional[str] = None
    company_name: Optional[str] = None
    requested_period: RequestedPeriod
    field_or_passage_hint: Optional[str] = None
    accession_number: Optional[str] = None
    filing_form: Optional[str] = None
    recipient_uei: Optional[str] = Field(
        default=None,
        description=(
            "Recipient UEI when known. USAspending search prefers this "
            "over a name. Name search is recorded as affiliate-unsafe."
        ),
    )
    filter_awarding_subtier_dla: bool = Field(
        default=True,
        description=(
            "When true, restrict USAspending award search to the "
            "Defense Logistics Agency awarding subtier. Default on for VEDA."
        ),
    )
    usaspending_page_cap: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum USAspending result pages to fetch.",
    )
    include_transactions: bool = Field(
        default=False,
        description=(
            "When true, also query the documented spending_by_transaction "
            "endpoint. Off by default; award search remains the primary path."
        ),
    )

    @model_validator(mode="after")
    def _at_least_one_lookup_key(self) -> "ProviderRequest":
        if not any(
            isinstance(value, str) and value.strip()
            for value in (
                self.entity_id,
                self.cik,
                self.company_name,
            )
        ):
            raise ValueError(
                "ProviderRequest requires at least one non-blank lookup key"
            )
        return self


class ProviderResult(BaseModel):
    status: ProviderStatus
    source_type: SourceType
    source_name: str = Field(..., min_length=1)
    is_fixture: bool
    retrieved_at: datetime = Field(default_factory=_utcnow)
    raw_records: list[dict] = Field(default_factory=list)
    error_message: Optional[str] = None
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _status_invariants(self) -> "ProviderResult":
        if self.status == ProviderStatus.FOUND:
            if not self.raw_records:
                raise ValueError("FOUND requires non-empty raw_records")
            if self.error_message is not None:
                raise ValueError("FOUND requires error_message to be None")
            return self

        if self.status == ProviderStatus.NOT_FOUND:
            if self.raw_records:
                raise ValueError("NOT_FOUND requires empty raw_records")
            if (
                self.error_message is not None
                and not self.error_message.strip()
            ):
                raise ValueError(
                    "NOT_FOUND error_message must be None or non-empty"
                )
            return self

        if self.raw_records:
            raise ValueError(
                f"{self.status.value} requires empty raw_records"
            )

        if self.error_message is None or not self.error_message.strip():
            raise ValueError(
                f"{self.status.value} requires non-empty error_message"
            )

        return self
