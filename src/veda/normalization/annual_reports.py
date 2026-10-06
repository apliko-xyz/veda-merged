"""
File: src/veda/normalization/annual_reports.py
Title: Annual Report Passage Normalizer
Layer: Normalization layer
Status: Merged prototype foundation — Phase 5
"""

from __future__ import annotations

from hashlib import sha256

from veda.normalization.evidence_ids import evidence_id_for
from veda.normalization.helpers import (
    document_native_token,
    make_document_id,
    provider_status_gate,
    validate_caller_entity_id,
    validate_requested_period,
)
from veda.providers.results import ProviderResult
from veda.shared.enums import (
    EvidenceCategory,
    ExtractionMethod,
    SourceType,
)
from veda.shared.models import (
    Evidence,
    EvidenceLocation,
    SourceDocument,
)
from veda.shared.periods import Period, RequestedPeriod


def _content_hash(text: str) -> str:
    """Return a deterministic lowercase SHA-256 hash."""
    return sha256(text.encode("utf-8")).hexdigest()


def normalize_annual_report_passage(
    result: ProviderResult,
    *,
    entity_id: str,
    requested_period: RequestedPeriod,
    warnings: list[str] | None = None,
) -> list[Evidence]:
    """Normalize valid annual-report passages into revenue Evidence."""
    validate_caller_entity_id(entity_id)
    validate_requested_period(requested_period)

    if provider_status_gate(result) is not None:
        return []

    if result.source_type != SourceType.ANNUAL_REPORT:
        return []

    period_label = f"FY{requested_period.fiscal_year}"
    output: list[Evidence] = []

    for record in result.raw_records:
        if not isinstance(record, dict):
            continue

        text = record.get("text")
        source_name = record.get("source_name")
        source_url = record.get("source_url")
        fiscal_year = record.get("fiscal_year")

        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                text,
                source_name,
                source_url,
            )
        ):
            continue

        if not isinstance(fiscal_year, int):
            continue

        if fiscal_year != requested_period.fiscal_year:
            continue

        native_id = document_native_token(
            f"{entity_id}-{fiscal_year}-{source_url}"
        )

        try:
            document = SourceDocument(
                doc_id=make_document_id(
                    "annual_report",
                    "ir_pdf",
                    native_id,
                ),
                source_type=SourceType.ANNUAL_REPORT,
                doc_type="ir_pdf",
                title=source_name,
                url=source_url,
                retrieved_at=result.retrieved_at,
                is_fixture=result.is_fixture,
                content_hash=_content_hash(text),
            )

            location = EvidenceLocation(
                field_or_passage=text[:120],
                source_url=source_url,
            )

            evidence = Evidence(
                evidence_id=evidence_id_for(
                    SourceType.ANNUAL_REPORT,
                    entity_id,
                    period_label,
                    text[:64],
                ),
                source_type=SourceType.ANNUAL_REPORT,
                source_name=result.source_name,
                document=document,
                location=location,
                entity_id=entity_id,
                reporting_period=Period(label=period_label),
                evidence_category=EvidenceCategory.RECOGNIZED_REVENUE,
                retrieval_method=(
                    ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION
                ),
                retrieved_at=result.retrieved_at,
                is_fixture=result.is_fixture,
            )

        except Exception as exc:
            message = (
                "Annual report normalization could not parse a record: "
                f"{type(exc).__name__}: {exc}"
            )
            if warnings is not None:
                warnings.append(message)
            else:
                raise ValueError(message) from exc
            continue

        output.append(evidence)

    return output


__all__ = ["normalize_annual_report_passage"]