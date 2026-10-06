# filename: tests/normalization/test_filings.py
# title: Normalization Layer - SEC Filing Passage Tests
# layer: Test suite - normalization
# status: Phase 9 readiness
# description:
#     Verifies filing normalization: primary-document text becomes
#     Evidence with a span, known hints map to categories, and an
#     unknown hint or an EDGAR index page becomes MissingEvidence.

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from veda.normalization.chunking import normalize_filing_body
from veda.normalization.filings import normalize_filing_passage
from veda.providers.results import ProviderResult
from veda.shared.enums import (
    EvidenceCategory,
    ExtractionMethod,
    MissingEvidenceReason,
    ProviderStatus,
    SourceType,
)
from veda.shared.periods import RequestedPeriod


ENTITY_ID = "entity:sec_edgar:vendor:0000936468"
ACCN = "0000936468-25-000009"
FORM = "10-K"
PRIMARY_URL = (
    "https://www.sec.gov/Archives/edgar/data/"
    "936468/000093646825000009/lmt-20241231.htm"
)
INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/"
    "936468/000093646825000009/0000936468-25-000009-index.htm"
)
REVENUES_TEXT = (
    "Total revenues were $71,043 million for the year ended "
    "December 31, 2024."
)
GOVERNMENT_TEXT = (
    "A substantial portion of sales are from U.S. government contracts."
)
CUSTOMER_TEXT = (
    "Customer concentration: one major customer accounted for 15 percent."
)
SUBSIDIARY_TEXT = (
    "Lockheed Martin Corporation is the ultimate parent of its "
    "consolidated subsidiaries."
)
COMBINED_TEXT = f"{REVENUES_TEXT}\n{GOVERNMENT_TEXT}\n{SUBSIDIARY_TEXT}"


def _requested(year: int = 2024) -> RequestedPeriod:
    return RequestedPeriod(fiscal_year=year, raw=str(year))


def _record(
    *,
    text: str = REVENUES_TEXT,
    accession_number: str = ACCN,
    filing_form: str = FORM,
    field_or_passage_hint: str = "revenues",
    section: str | None = "MD&A",
    url: str | None = PRIMARY_URL,
) -> dict:
    return {
        "text": text,
        "accession_number": accession_number,
        "filing_form": filing_form,
        "field_or_passage_hint": field_or_passage_hint,
        "section": section,
        "url": url,
    }


def _result(*records: dict, is_fixture: bool = False) -> ProviderResult:
    return ProviderResult(
        status=ProviderStatus.FOUND,
        source_type=SourceType.SEC_FILING,
        source_name="SEC Filings",
        is_fixture=is_fixture,
        raw_records=list(records) if records else [_record()],
        retrieved_at=datetime(2025, 1, 28, tzinfo=timezone.utc),
    )


def _normalize(*records: dict, **kwargs):
    return normalize_filing_passage(
        _result(*records, **kwargs) if records or "is_fixture" not in kwargs else _result(**kwargs),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )


def _evidence(*records: dict):
    return normalize_filing_passage(
        _result(*records) if records else _result(),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    ).evidence


def _not_found_result() -> ProviderResult:
    return ProviderResult(
        status=ProviderStatus.NOT_FOUND,
        source_type=SourceType.SEC_FILING,
        source_name="SEC Filings",
        is_fixture=False,
        error_message="not found for test",
    )


def test_returns_one_evidence_for_valid_record() -> None:
    evidence = _evidence()
    assert len(evidence) == 1


def test_evidence_has_document() -> None:
    evidence = _evidence()
    assert evidence[0].document is not None
    assert evidence[0].document.accession_number == ACCN


def test_evidence_has_location() -> None:
    evidence = _evidence()
    assert evidence[0].location is not None
    assert evidence[0].location.field_or_passage == "revenues"
    assert evidence[0].location.chunk_id is not None
    assert evidence[0].location.span_start is not None
    assert evidence[0].location.span_end is not None


def test_evidence_content_hash_is_sha256_of_normalized_text() -> None:
    evidence = _evidence()
    assert evidence[0].document is not None
    normalized = normalize_filing_body(REVENUES_TEXT, PRIMARY_URL)
    expected = sha256(normalized.encode("utf-8")).hexdigest()
    assert evidence[0].document.content_hash == expected


def test_evidence_retrieval_method_is_text_extraction() -> None:
    evidence = _evidence()
    assert evidence[0].retrieval_method == ExtractionMethod.DETERMINISTIC_TEXT_EXTRACTION


def test_evidence_accession_number_on_record() -> None:
    evidence = _evidence()
    assert evidence[0].accession_number == ACCN


def test_revenues_hint_maps_to_recognized_revenue_and_is_context_only() -> None:
    evidence = _evidence()
    assert evidence[0].evidence_category == EvidenceCategory.RECOGNIZED_REVENUE
    assert evidence[0].raw_value is None
    assert evidence[0].is_context_only is True


def test_span_text_equals_located_passage() -> None:
    normalized = normalize_filing_passage(
        _result(),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    evidence = normalized.evidence[0]
    location = evidence.location
    assert location is not None
    body = normalize_filing_body(REVENUES_TEXT, PRIMARY_URL)
    assert location.span_start is not None and location.span_end is not None
    assert body[location.span_start:location.span_end] == "revenues"
    chunk = normalized.chunks[0]
    relative = location.span_start - chunk.char_start
    length = location.span_end - location.span_start
    assert chunk.text[relative:relative + length] == "revenues"


def test_subsidiary_relationship_maps_to_corporate_relationship() -> None:
    rec = _record(
        field_or_passage_hint="subsidiary_relationship",
        text=SUBSIDIARY_TEXT,
    )
    evidence = _evidence(rec)
    assert evidence[0].evidence_category == EvidenceCategory.CORPORATE_RELATIONSHIP
    assert evidence[0].location is not None
    assert evidence[0].location.source_reference == "ultimate parent"


def test_government_exposure_maps_to_government_exposure() -> None:
    rec = _record(
        field_or_passage_hint="government_exposure",
        text=GOVERNMENT_TEXT,
    )
    evidence = _evidence(rec)
    assert evidence[0].evidence_category == EvidenceCategory.GOVERNMENT_EXPOSURE


def test_customer_concentration_maps_to_customer_concentration() -> None:
    rec = _record(
        field_or_passage_hint="customer_concentration",
        text=CUSTOMER_TEXT,
    )
    evidence = _evidence(rec)
    assert evidence[0].evidence_category == EvidenceCategory.CUSTOMER_CONCENTRATION


def test_unknown_hint_produces_missing_evidence() -> None:
    rec = _record(field_or_passage_hint="unknown_hint")
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert len(normalized.missing_evidence) == 1
    missing = normalized.missing_evidence[0]
    assert missing.reason == MissingEvidenceReason.EXTRACTION_FAILED
    assert "unknown_hint" in missing.explanation
    assert missing.sources_checked[-1].startswith("doc:")


def test_known_hint_not_in_text_produces_missing_evidence() -> None:
    rec = _record(
        field_or_passage_hint="government_exposure",
        text=REVENUES_TEXT,
    )
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.missing_evidence[0].reason == MissingEvidenceReason.EXTRACTION_FAILED


def test_document_id_uses_form_slug() -> None:
    evidence = _evidence()
    assert evidence[0].document is not None
    assert evidence[0].document.doc_id == f"doc:sec_edgar:10k:{ACCN}"


def test_document_id_lowercases_form() -> None:
    rec = _record(filing_form="10-K")
    evidence = _evidence(rec)
    assert evidence[0].document is not None
    assert "10k" in evidence[0].document.doc_id


def test_missing_text_records_a_warning() -> None:
    rec = _record()
    del rec["text"]
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.warnings


def test_missing_accession_records_a_warning() -> None:
    rec = _record()
    del rec["accession_number"]
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.warnings


def test_missing_form_records_a_warning() -> None:
    rec = _record()
    del rec["filing_form"]
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.warnings


def test_missing_hint_records_a_warning() -> None:
    rec = _record()
    del rec["field_or_passage_hint"]
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.warnings


def test_blank_text_records_a_warning() -> None:
    rec = _record(text="   ")
    normalized = normalize_filing_passage(
        _result(rec),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.warnings


def test_malformed_record_does_not_break_valid_one() -> None:
    bad = _record()
    del bad["text"]
    good = _record(
        field_or_passage_hint="government_exposure",
        text=GOVERNMENT_TEXT,
    )
    normalized = normalize_filing_passage(
        _result(bad, good),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert len(normalized.evidence) == 1
    assert normalized.warnings


def test_not_found_result_returns_empty() -> None:
    normalized = normalize_filing_passage(
        _not_found_result(),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.chunks == []
    assert normalized.missing_evidence == []


def test_wrong_source_type_returns_empty() -> None:
    result = ProviderResult(
        status=ProviderStatus.FOUND,
        source_type=SourceType.SEC_COMPANY_FACTS,
        source_name="SEC Company Facts",
        is_fixture=False,
        raw_records=[_record()],
    )
    normalized = normalize_filing_passage(
        result,
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []


def test_multiple_valid_records_produce_multiple_evidence() -> None:
    rec_a = _record(
        field_or_passage_hint="government_exposure",
        text=COMBINED_TEXT,
    )
    rec_b = _record(
        field_or_passage_hint="subsidiary_relationship",
        text=COMBINED_TEXT,
    )
    normalized = normalize_filing_passage(
        _result(rec_a, rec_b),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert len(normalized.evidence) == 2
    assert len({chunk.chunk_id for chunk in normalized.chunks}) == len(normalized.chunks)


def test_evidence_ids_are_unique() -> None:
    rec_a = _record(
        field_or_passage_hint="government_exposure",
        text=COMBINED_TEXT,
    )
    rec_b = _record(
        field_or_passage_hint="subsidiary_relationship",
        text=COMBINED_TEXT,
    )
    evidence = _evidence(rec_a, rec_b)
    ids = [item.evidence_id for item in evidence]
    assert len(ids) == len(set(ids))


def test_index_page_html_is_not_filing_content() -> None:
    html = (
        "<html><head><title>EDGAR Filing Documents</title></head>"
        "<body><h1>Filing Detail</h1>"
        "<table><tr><td>Document Format Files</td></tr></table>"
        "<p>Total revenues were $71,043 million.</p></body></html>"
    )
    normalized = normalize_filing_passage(
        _result(_record(text=html, url=INDEX_URL)),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert normalized.evidence == []
    assert normalized.chunks == []
    assert normalized.missing_evidence[0].reason == MissingEvidenceReason.EXTRACTION_FAILED
    assert "index" in normalized.missing_evidence[0].explanation


def test_same_html_produces_identical_chunks() -> None:
    html = (
        "<html><head><style>p{color:red}</style>"
        "<script>alert(1)</script></head><body>"
        "<p>Total revenues were $71,043 million for the year ended "
        "December 31, 2024.</p>"
        "<p>Lockheed Martin Corporation is the ultimate parent of its "
        "consolidated subsidiaries.</p>"
        "</body></html>"
    )
    record = _record(text=html, url=PRIMARY_URL)
    first = normalize_filing_passage(
        _result(record),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    second = normalize_filing_passage(
        _result(record),
        entity_id=ENTITY_ID,
        requested_period=_requested(2024),
    )
    assert [chunk.model_dump() for chunk in first.chunks] == [
        chunk.model_dump() for chunk in second.chunks
    ]
    assert first.evidence[0].location is not None
    assert first.evidence[0].location.chunk_id == second.evidence[0].location.chunk_id
    assert "alert" not in first.chunks[0].text
    assert "color:red" not in first.chunks[0].text
