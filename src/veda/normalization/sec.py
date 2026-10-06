"""
File: src/veda/normalization/sec.py
Title: SEC Company Facts Normalizer
Layer: Normalization layer
Status: Merged prototype foundation — Phase 5

Purpose
-------
Converts raw SEC Company Facts ProviderResult records into canonical
revenue Evidence. Selection is based on actual start/end dates and
full-year spans, not SEC fy/fp labels.

Public API
----------
normalize_company_facts
    Convert a Company Facts result into zero or one Evidence objects.

Does not
--------
Does not make network calls, import fixture data, use fy/fp as
authoritative fields, combine revenue tags, or build assessments.

Design notes
------------
Revenue tags are tried in priority order and the first tag with valid
candidates wins. Equal values select the earliest filing; differing
values select the latest filing. Malformed records are skipped.
"""

from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from typing import Any

from veda.normalization.evidence_ids import evidence_id_for
from veda.normalization.helpers import (
    cik_from_entity_id,
    make_document_id,
    provider_status_gate,
    validate_caller_entity_id,
    validate_requested_period,
)
from veda.providers.results import ProviderRequest, ProviderResult
from veda.shared.enums import EvidenceCategory, ExtractionMethod, SourceType
from veda.shared.models import Evidence, EvidenceLocation, SourceDocument
from veda.shared.periods import Period, RequestedPeriod


VERSION = "1"


REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
)


def _content_hash_for_entry(entry: dict) -> str:
    """
    Return a deterministic lowercase SHA-256 hash of one SEC fact entry.

    The hash is over a canonical JSON serialization of the entry's
    identifying fields, so the same entry always produces the same
    hash. Used as SourceDocument.content_hash so fixture-backed
    evidence satisfies the model rule that is_fixture=True requires a
    content hash.
    """
    canonical = {
        "start": entry.get("start"),
        "end": entry.get("end"),
        "val": entry.get("val"),
        "fy": entry.get("fy"),
        "fp": entry.get("fp"),
        "form": entry.get("form"),
        "filed": entry.get("filed"),
        "accn": entry.get("accn"),
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def _build_filing_index_url(
    cik: str | None,
    accession_number: str | None,
) -> str | None:
    """
    Build the SEC filing index URL for a specific accession.

    Ported from the personal prototype's extractor.py, which built this
    URL from the CIK (with leading zeros stripped) and the accession
    number (with dashes stripped) so a claim could carry a click-through
    to the exact filing page.

    Returns None when either input is missing or malformed, so the
    evidence simply has no filing URL rather than a broken one.
    """
    if not isinstance(cik, str) or not cik:
        return None
    if not isinstance(accession_number, str) or not accession_number:
        return None
    if not cik.isdigit():
        return None

    cik_no_zeros = str(int(cik))
    accn_no_dashes = accession_number.replace("-", "")
    if not accn_no_dashes:
        return None

    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik_no_zeros}/{accn_no_dashes}/{accession_number}-index.htm"
    )


def _parse_date(value: Any) -> date | None:
    """Parse an ISO date string, returning None for malformed input."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _valid_candidates(entries: list[Any], fiscal_year: int) -> list[dict]:
    """Return valid full-year 10-K candidates for the requested year."""
    candidates: list[dict] = []

    for entry in entries:
        if not isinstance(entry, dict) or entry.get("form") != "10-K":
            continue

        start = _parse_date(entry.get("start"))
        end = _parse_date(entry.get("end"))
        value = entry.get("val")

        if start is None or end is None:
            continue
        if end.year != fiscal_year:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue

        period = Period.from_iso_strings(
            start.isoformat(),
            end.isoformat(),
        )
        if not period.is_full_year():
            continue

        candidates.append(entry)

    return candidates


def _filed_key(entry: dict) -> tuple[int, str, str]:
    """Return a deterministic key distinguishing valid and invalid filed dates."""
    filed = _parse_date(entry.get("filed"))
    if filed is None:
        return (1, "", str(entry.get("accn") or ""))
    return (0, filed.isoformat(), str(entry.get("accn") or ""))


def _select_candidate(candidates: list[dict]) -> dict:
    """Apply the approved unchanged-value/restatement tie-break."""
    same_value = len({candidate.get("val") for candidate in candidates}) == 1

    if same_value:
        valid_filed = [candidate for candidate in candidates if _parse_date(candidate.get("filed"))]
        if valid_filed:
            return min(valid_filed, key=_filed_key)
        return min(candidates, key=_filed_key)

    valid_filed = [candidate for candidate in candidates if _parse_date(candidate.get("filed"))]
    if valid_filed:
        return max(valid_filed, key=_filed_key)
    return min(candidates, key=_filed_key)


def normalize_company_facts(
    result: ProviderResult,
    *,
    entity_id: str,
    requested_period: RequestedPeriod,
) -> list[Evidence]:
    """Normalize SEC Company Facts, returning [] for provider or record failures."""
    validate_caller_entity_id(entity_id)
    validate_requested_period(requested_period)

    if provider_status_gate(result) is not None:
        return []
    if result.source_type != SourceType.SEC_COMPANY_FACTS:
        return []
    if not result.raw_records or not isinstance(result.raw_records[0], dict):
        return []

    payload = result.raw_records[0]
    facts = payload.get("facts")
    us_gaap = facts.get("us-gaap") if isinstance(facts, dict) else None
    if not isinstance(us_gaap, dict):
        return []

    fiscal_year = requested_period.fiscal_year

    for tag in REVENUE_TAGS:
        tag_data = us_gaap.get(tag)
        units = tag_data.get("units") if isinstance(tag_data, dict) else None
        entries = units.get("USD") if isinstance(units, dict) else None

        if not isinstance(entries, list):
            continue

        candidates = _valid_candidates(entries, fiscal_year)
        if not candidates:
            continue

        selected = _select_candidate(candidates)
        period = Period.from_iso_strings(
            selected["start"],
            selected["end"],
            label=f"FY{fiscal_year}",
        )
        cik = cik_from_entity_id(entity_id)
        browse_url = (
            f"https://www.sec.gov/edgar/browse/?CIK={cik}"
            if cik is not None
            else None
        )

        accession_number = selected.get("accn")
        filing_form = selected.get("form")

        filing_index_url = _build_filing_index_url(
            cik=cik,
            accession_number=accession_number,
        )

        document: SourceDocument | None = None
        if filing_form and accession_number and filing_index_url:
            form_slug = filing_form.strip().lower().replace("-", "")
            document = SourceDocument(
                doc_id=make_document_id(
                    "sec_edgar",
                    form_slug,
                    accession_number,
                ),
                source_type=SourceType.SEC_COMPANY_FACTS,
                doc_type=form_slug,
                title=f"SEC filing {accession_number}",
                url=filing_index_url,
                content_hash=_content_hash_for_entry(selected),
                retrieved_at=result.retrieved_at,
                is_fixture=result.is_fixture,
                accession_number=accession_number,
                filing_form=filing_form,
                filing_date=_parse_date(selected.get('filed')),
                reporting_period=period,
            )

        try:
            evidence = Evidence(
                evidence_id=evidence_id_for(
                    SourceType.SEC_COMPANY_FACTS,
                    entity_id,
                    f"FY{fiscal_year}",
                    tag,
                ),
                source_type=SourceType.SEC_COMPANY_FACTS,
                source_name=result.source_name,
                document=document,
                location=EvidenceLocation(
                    field_or_passage=tag,
                    source_url=filing_index_url,
                    source_reference=tag,
                ),
                raw_value=selected["val"],
                unit="USD",
                currency="USD",
                entity_id=entity_id,
                reporting_period=period,
                evidence_category=EvidenceCategory.RECOGNIZED_REVENUE,
                retrieval_method=ExtractionMethod.DETERMINISTIC_FIELD_EXTRACTION,
                retrieved_at=result.retrieved_at,
                is_fixture=result.is_fixture,
                accession_number=accession_number,
                xbrl_tag=tag,
                form=filing_form,
                sec_browse_url=browse_url,
            )
        except Exception:
            continue

        return [evidence]

    return []


__all__ = ["normalize_company_facts"]