"""
File: src/veda/normalization/filings.py
Title: SEC Filing Passage Normalizer
Layer: Normalization layer
Status: Phase 9 readiness — primary-document spans

Purpose
-------
Turns a fetched SEC filing into Evidence, EvidenceChunk records, and
an explicit MissingEvidence record when a passage hint cannot be
located. Unknown hints are never labeled GOVERNMENT_EXPOSURE.

Hint location order, for a known hint only:
  1. Exact substring of the hint.
  2. Case-insensitive substring of the hint.
  3. The documented regular expression for that hint:

     revenues
         total revenues / revenues / net sales / revenue
     subsidiary_relationship
         subsidiary, subsidiaries, ultimate parent,
         consolidated subsidiaries
     government_exposure
         U.S. government, government contracts, government sales
     customer_concentration
         customer concentration, major customer(s),
         concentration of credit

A located recognized-revenue passage with no parsed number is
context-only. The normalizer does not invent a dollar amount from
prose such as "$71,043 million".

Public API
----------
normalize_filing_passage
FilingNormalization
HINT_PATTERNS
VERSION

Does not
--------
Does not fetch filings, call a model, or build an assessment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from veda.normalization.chunking import (
    chunk_for_span,
    chunk_text,
    locate_passage,
    looks_like_filing_index,
    normalize_filing_body,
    sha256_text,
)
from veda.normalization.evidence_ids import evidence_id_for
from veda.normalization.helpers import (
    cik_from_entity_id,
    make_document_id,
    provider_status_gate,
    validate_caller_entity_id,
    validate_requested_period,
)
from veda.providers.results import ProviderResult
from veda.shared.enums import (
    EvidenceCategory,
    ExtractionMethod,
    MissingEvidenceReason,
    SourceType,
)
from veda.shared.models import (
    Evidence,
    EvidenceChunk,
    EvidenceLocation,
    MissingEvidence,
    SourceDocument,
)
from veda.shared.periods import Period, RequestedPeriod


VERSION = "1"

# Documented hint patterns. Unknown hints are absent from this map
# and never receive a default category.
HINT_PATTERNS: dict[str, tuple[EvidenceCategory, re.Pattern[str]]] = {
    "revenues": (
        EvidenceCategory.RECOGNIZED_REVENUE,
        re.compile(
            r"(?i)\b(?:total\s+)?(?:net\s+)?(?:revenues?|net\s+sales)\b"
        ),
    ),
    "subsidiary_relationship": (
        EvidenceCategory.CORPORATE_RELATIONSHIP,
        re.compile(
            r"(?i)\b(?:subsidiar(?:y|ies)|ultimate\s+parent|"
            r"consolidated\s+subsidiaries)\b"
        ),
    ),
    "government_exposure": (
        EvidenceCategory.GOVERNMENT_EXPOSURE,
        re.compile(
            r"(?i)\b(?:u\.?s\.?\s+government|government\s+contracts?|"
            r"government\s+sales)\b"
        ),
    ),
    "customer_concentration": (
        EvidenceCategory.CUSTOMER_CONCENTRATION,
        re.compile(
            r"(?i)\b(?:customer\s+concentration|major\s+customers?|"
            r"concentration\s+of\s+credit)\b"
        ),
    ),
}


@dataclass
class FilingNormalization:
    """Evidence, chunks, and extraction failures from one filing result."""

    evidence: list[Evidence] = field(default_factory=list)
    chunks: list[EvidenceChunk] = field(default_factory=list)
    missing_evidence: list[MissingEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _form_slug(form: str) -> str:
    """Convert an SEC form into a valid document-type slug."""
    slug = form.strip().lower().replace("-", "")
    return "".join(
        character if character.isalnum() else "_"
        for character in slug
    )


def _missing(
    *,
    hint: str,
    entity_id: str,
    period: Period,
    doc_id: str | None,
    explanation: str,
) -> MissingEvidence:
    sources = ["SEC Filings"]
    if doc_id:
        sources.append(doc_id)
    return MissingEvidence(
        claim_type=hint,
        reason=MissingEvidenceReason.EXTRACTION_FAILED,
        explanation=explanation,
        entity_id=entity_id,
        reporting_period=period,
        sources_checked=sources,
    )


def normalize_filing_passage(
    result: ProviderResult,
    *,
    entity_id: str,
    requested_period: RequestedPeriod,
) -> FilingNormalization:
    """Normalize SEC filing passages into evidence, chunks, and gaps."""
    validate_caller_entity_id(entity_id)
    validate_requested_period(requested_period)

    output = FilingNormalization()
    if provider_status_gate(result) is not None:
        return output
    if result.source_type != SourceType.SEC_FILING:
        return output

    period_label = f"FY{requested_period.fiscal_year}"
    period = Period(label=period_label)
    # One body per document id. A second, different body would reuse
    # chunk IDs, so it is reported instead of silently merged.
    chunks_by_document: dict[str, tuple[str, list[EvidenceChunk]]] = {}

    for record in result.raw_records:
        if not isinstance(record, dict):
            output.warnings.append(
                "SEC filing record skipped: record is not an object"
            )
            continue

        text = record.get("text")
        accession = record.get("accession_number")
        form = record.get("filing_form")
        hint = record.get("field_or_passage_hint")
        url = record.get("url") if isinstance(record.get("url"), str) else None

        if not all(
            isinstance(value, str) and value.strip()
            for value in (text, accession, form, hint)
        ):
            output.warnings.append(
                "SEC filing record skipped: missing text, accession, "
                "form, or passage hint"
            )
            continue

        assert isinstance(text, str)
        assert isinstance(accession, str)
        assert isinstance(form, str)
        assert isinstance(hint, str)
        hint = hint.strip()

        try:
            form_slug = _form_slug(form)
            document_id = make_document_id("sec_edgar", form_slug, accession)
        except (TypeError, ValueError) as exc:
            output.warnings.append(
                "SEC filing record skipped: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        if looks_like_filing_index(text, url):
            output.missing_evidence.append(
                _missing(
                    hint=hint,
                    entity_id=entity_id,
                    period=period,
                    doc_id=document_id,
                    explanation=(
                        f"Passage hint {hint!r} was not extracted from "
                        f"{document_id}: the retrieved page is an EDGAR "
                        "filing index, not the primary filing document."
                    ),
                )
            )
            continue

        normalized = normalize_filing_body(text, url)
        body_hash = sha256_text(normalized)
        stored = chunks_by_document.get(document_id)
        if stored is None:
            chunks = chunk_text(document_id, normalized)
            chunks_by_document[document_id] = (body_hash, chunks)
        else:
            stored_hash, chunks = stored
            if stored_hash != body_hash:
                output.warnings.append(
                    "SEC filing record skipped: document "
                    f"{document_id} was already normalized from a "
                    "different body"
                )
                output.missing_evidence.append(
                    _missing(
                        hint=hint,
                        entity_id=entity_id,
                        period=period,
                        doc_id=document_id,
                        explanation=(
                            f"Passage hint {hint!r} was not extracted from "
                            f"{document_id}: a different normalized body "
                            "was already stored for that document."
                        ),
                    )
                )
                continue

        spec = HINT_PATTERNS.get(hint)
        if spec is None:
            output.missing_evidence.append(
                _missing(
                    hint=hint,
                    entity_id=entity_id,
                    period=period,
                    doc_id=document_id,
                    explanation=(
                        f"Passage hint {hint!r} is not a known filing "
                        f"hint for document {document_id}. Unknown hints "
                        "are not assigned an evidence category."
                    ),
                )
            )
            continue

        category, pattern = spec
        hit = locate_passage(normalized, hint, pattern)
        if hit is None:
            output.missing_evidence.append(
                _missing(
                    hint=hint,
                    entity_id=entity_id,
                    period=period,
                    doc_id=document_id,
                    explanation=(
                        f"Passage hint {hint!r} was not located in "
                        f"document {document_id}."
                    ),
                )
            )
            continue

        containing = chunk_for_span(chunks, hit.start, hit.end)
        if containing is None:
            output.missing_evidence.append(
                _missing(
                    hint=hint,
                    entity_id=entity_id,
                    period=period,
                    doc_id=document_id,
                    explanation=(
                        f"Passage hint {hint!r} was located in "
                        f"{document_id} but did not fall inside a chunk."
                    ),
                )
            )
            continue

        cik = cik_from_entity_id(entity_id)
        browse_url = (
            f"https://www.sec.gov/edgar/browse/?CIK={cik}"
            if cik is not None
            else None
        )
        document = SourceDocument(
            doc_id=document_id,
            source_type=SourceType.SEC_FILING,
            doc_type=form_slug,
            title=f"SEC filing {accession}",
            url=url,
            retrieved_at=result.retrieved_at,
            is_fixture=result.is_fixture,
            content_hash=sha256_text(normalized),
            accession_number=accession,
            filing_form=form,
        )
        location = EvidenceLocation(
            field_or_passage=hint,
            source_url=url,
            source_reference=hit.matched,
            section=record.get("section") if isinstance(record.get("section"), str) else None,
            chunk_id=containing.chunk_id,
            span_start=hit.start,
            span_end=hit.end,
        )
        # Narrative revenue prose is not a parsed number. Leaving
        # raw_value empty and marking the record context-only keeps
        # it out of claim extraction.
        context_only = category == EvidenceCategory.RECOGNIZED_REVENUE
        output.evidence.append(
            Evidence(
                evidence_id=evidence_id_for(
                    SourceType.SEC_FILING,
                    entity_id,
                    period_label,
                    hint,
                    document_id,
                ),
                source_type=SourceType.SEC_FILING,
                source_name=result.source_name,
                document=document,
                location=location,
                entity_id=entity_id,
                reporting_period=period,
                evidence_category=category,
                retrieval_method=ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION,
                retrieved_at=result.retrieved_at,
                is_fixture=result.is_fixture,
                is_context_only=context_only,
                sec_browse_url=browse_url,
                accession_number=accession,
                form=form,
            )
        )

    seen_chunk_ids: set[str] = set()
    for _body_hash, document_chunks in chunks_by_document.values():
        for chunk in document_chunks:
            if chunk.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk.chunk_id)
            output.chunks.append(chunk)
    output.chunks.sort(key=lambda chunk: chunk.chunk_id)
    return output


__all__ = [
    "HINT_PATTERNS",
    "VERSION",
    "FilingNormalization",
    "normalize_filing_passage",
]
