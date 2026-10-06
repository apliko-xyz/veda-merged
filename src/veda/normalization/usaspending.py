"""
File: src/veda/normalization/usaspending.py
Title: USAspending Award Normalizer
Layer: Normalization layer
Status: Phase 9 readiness — award amounts are not period obligations

Purpose
-------
Converts USAspending award rows into Evidence. ``Award Amount`` is a
lifetime award value. It is labeled ``PROCUREMENT_AWARD`` and dated to
the federal fiscal year window that was searched. It is not labeled as
a procurement obligation or as recognized revenue.

Records that cannot be parsed are reported as warnings. They are not
printed and they are not dropped silently.

Public API
----------
normalize_usaspending_award
PERIOD_OBLIGATION_GAP
USAspendingNormalization

Does not
--------
Does not make network calls, import fixture data, relabel awards as
revenue, or build assessments.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from veda.normalization.evidence_ids import evidence_id_for
from veda.normalization.helpers import (
    document_native_token,
    make_document_id,
    provider_status_gate,
    validate_caller_entity_id,
    validate_requested_period,
)
from veda.providers.results import ProviderResult
from veda.providers.usaspending import canonical_award_payload
from veda.shared.enums import EvidenceCategory, ExtractionMethod, SourceType
from veda.shared.models import Evidence, EvidenceLocation, SourceDocument
from veda.shared.periods import Period, RequestedPeriod


VERSION = "1"

PERIOD_OBLIGATION_GAP = (
    "USAspending Award Amount is a lifetime award value, not a period "
    "obligation and not recognized revenue. The evidence period is the "
    "federal fiscal year window used for retrieval (FFY, 1 October "
    "through 30 September), which is distinct from the company fiscal "
    "year. Period-scoped obligations require transaction-level data "
    "from POST /api/v2/search/spending_by_transaction/."
)


@dataclass(frozen=True)
class USAspendingNormalization:
    """Evidence produced from one provider result, plus parse warnings."""

    evidence: list[Evidence]
    warnings: list[str]


def _amount(value: Any) -> float | None:
    """Accept a JSON number. Reject bools and strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _transaction_amount(value: Any) -> float | None:
    """
    Accept a JSON number or a decimal string.

    The spending_by_transaction contract sample returns
    ``Transaction Amount`` as a string. That parse is limited to a
    plain decimal; anything else is a warning.
    """
    numeric = _amount(value)
    if numeric is not None:
        return numeric
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _award_id(record: dict[str, Any]) -> str | None:
    for key in ("Award ID", "award_id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _content_hash(record: dict[str, Any]) -> str:
    return sha256(canonical_award_payload(record).encode("utf-8")).hexdigest()


def _document_for(
    record: dict[str, Any],
    result: ProviderResult,
    award_id: str,
) -> SourceDocument:
    native = record.get("generated_internal_id")
    if not isinstance(native, str) or not native.strip():
        native = award_id
    kind = record.get("_veda_record_kind") or "award"
    doc_type = "award" if kind != "transaction" else "transaction"
    return SourceDocument(
        doc_id=make_document_id(
            "usaspending",
            doc_type,
            document_native_token(native),
        ),
        source_type=SourceType.USASPENDING,
        doc_type=doc_type,
        title=f"USAspending {doc_type} {award_id}",
        content_hash=_content_hash(record),
        retrieved_at=result.retrieved_at,
        is_fixture=result.is_fixture,
    )


def _one_record(
    record: dict[str, Any],
    *,
    result: ProviderResult,
    entity_id: str,
    period: Period,
    index: int,
) -> tuple[Evidence | None, str | None]:
    if record.get("_veda_unparsed"):
        return None, (
            f"USAspending record {index} was not a JSON object and "
            "was not normalized."
        )

    kind = record.get("_veda_record_kind") or "award"
    award_id = _award_id(record)
    if award_id is None:
        return None, (
            f"USAspending record {index} has no Award ID and was not normalized."
        )

    if kind == "transaction":
        amount = _transaction_amount(record.get("Transaction Amount"))
        category = EvidenceCategory.PROCUREMENT_OBLIGATION
        if amount is None:
            return None, (
                f"USAspending transaction {award_id!r} has no numeric "
                "Transaction Amount and was not normalized."
            )
    else:
        amount = _amount(record.get("Award Amount"))
        if amount is None and "award_amount" in record:
            amount = _amount(record.get("award_amount"))
        category = EvidenceCategory.PROCUREMENT_AWARD
        if amount is None:
            return None, (
                f"USAspending award {award_id!r} has no numeric Award Amount "
                "and was not normalized. Total Obligated Amount is not used."
            )

    locator = record.get("generated_internal_id")
    if not isinstance(locator, str) or not locator.strip():
        locator = award_id

    try:
        evidence = Evidence(
            evidence_id=evidence_id_for(
                SourceType.USASPENDING,
                entity_id,
                period.label or "FFY",
                f"{kind}:{award_id}",
            ),
            source_type=SourceType.USASPENDING,
            source_name=result.source_name,
            document=_document_for(record, result, award_id),
            location=EvidenceLocation(
                field_or_passage=award_id,
                source_reference=locator,
                section=kind,
            ),
            raw_value=amount,
            unit="USD",
            currency="USD",
            entity_id=entity_id,
            reporting_period=period,
            evidence_category=category,
            retrieval_method=ExtractionMethod.DETERMINISTIC_JSON,
            retrieved_at=result.retrieved_at,
            is_fixture=result.is_fixture,
            is_context_only=True,
        )
    except Exception as exc:
        return None, (
            f"USAspending record {award_id!r} could not be normalized: "
            f"{type(exc).__name__}: {exc}"
        )
    return evidence, None


def normalize_usaspending_award(
    result: ProviderResult,
    *,
    entity_id: str,
    requested_period: RequestedPeriod,
) -> USAspendingNormalization:
    """Normalize award rows into federal-FY procurement-award evidence."""
    validate_caller_entity_id(entity_id)
    validate_requested_period(requested_period)

    if provider_status_gate(result) is not None:
        return USAspendingNormalization(evidence=[], warnings=[])
    if result.source_type != SourceType.USASPENDING:
        return USAspendingNormalization(evidence=[], warnings=[])

    period = Period.federal_fiscal_year(requested_period.fiscal_year)
    evidence: list[Evidence] = []
    warnings: list[str] = []

    for index, record in enumerate(result.raw_records):
        if not isinstance(record, dict):
            warnings.append(
                f"USAspending record {index} was not a JSON object "
                "and was not normalized."
            )
            continue
        item, warning = _one_record(
            record,
            result=result,
            entity_id=entity_id,
            period=period,
            index=index,
        )
        if warning:
            warnings.append(warning)
        if item is not None:
            evidence.append(item)

    return USAspendingNormalization(evidence=evidence, warnings=warnings)


__all__ = [
    "VERSION",
    "PERIOD_OBLIGATION_GAP",
    "USAspendingNormalization",
    "normalize_usaspending_award",
]
