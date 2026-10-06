"""
File: src/veda/providers/sec_filings.py
Title: SEC Filing Providers
Layer: Provider retrieval layer
Status: Merged prototype foundation — Phase 4

Purpose
-------
Retrieves the primary document for a specifically identified SEC
filing. The accession is resolved through the submissions JSON
(``filings.recent.primaryDocument`` aligned with ``accessionNumber``),
then the primary file is fetched. The EDGAR index page is never
returned as filing content.

Public classes
--------------
SECFilingsProvider
FixtureSECFilingsProvider

Network policy
--------------
The live provider requires a non-empty SEC User-Agent and retries only
once for timeouts and HTTP 5xx responses. The fixture provider performs
no network I/O.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from veda.providers.base import EvidenceProvider
from veda.providers.results import ProviderRequest, ProviderResult
from veda.shared.enums import ProviderStatus, SourceType


VERSION = "1"

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_PRIMARY_DOCUMENT_URL = (
    "https://www.sec.gov/Archives/edgar/data/"
    "{cik_no_zeros}/{accn_no_dashes}/{primary_document}"
)
TIMEOUT_SECONDS = 30.0
_PRIMARY_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def _normalize_cik(value: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.isdigit() or len(text) > 10:
        return None
    return text.zfill(10)


def _missing_identifier(request: ProviderRequest) -> Optional[str]:
    if not request.cik:
        return "cik"
    if not request.accession_number:
        return "accession_number"
    if not request.filing_form:
        return "filing_form"
    if not request.field_or_passage_hint:
        return "field_or_passage_hint"
    return None


def _body_is_filing_index(text: str, url: str) -> bool:
    """True when a fetched page is an EDGAR index rather than the filing."""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith("-index.htm") or path.endswith("-index.html"):
        return True
    sample = text.lower()
    if "edgar filing documents" in sample:
        return True
    if "filing detail" in sample and "document format files" in sample:
        return True
    return False


def _primary_document_name(name: str) -> Optional[str]:
    """Accept a submissions primaryDocument path that is not an index."""
    text = name.strip()
    if not text or text.startswith("/") or "\\" in text:
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if not all(_PRIMARY_SEGMENT.fullmatch(part) for part in parts):
        return None
    lowered = parts[-1].lower()
    if lowered.endswith("-index.htm") or lowered.endswith("-index.html"):
        return None
    return text


def _aligned_primary_document(payload: object, accession: str) -> tuple[str | None, str | None]:
    """
    Return ``(primary_document, error)``.

    ``error`` is ``"malformed"`` when the submissions shape cannot be
    read, ``"not_found"`` when the accession is absent, and ``"index"``
    when the aligned file is an EDGAR index page.
    """
    if not isinstance(payload, dict):
        return None, "malformed"
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        return None, "malformed"
    accessions = recent.get("accessionNumber")
    documents = recent.get("primaryDocument")
    if not isinstance(accessions, list) or not isinstance(documents, list):
        return None, "malformed"
    target = accession.strip()
    for index, candidate in enumerate(accessions):
        if not isinstance(candidate, str) or candidate.strip() != target:
            continue
        if index >= len(documents) or not isinstance(documents[index], str):
            return None, "malformed"
        primary = _primary_document_name(documents[index])
        if primary is None:
            return None, "index"
        return primary, None
    return None, "not_found"


def _fetch(url: str, headers: dict[str, str]) -> tuple[httpx.Response | None, str, int | None]:
    """GET ``url`` once, and once more on timeout or HTTP 5xx."""
    last_error = "SEC filing source unavailable after retry"
    last_status: int | None = None
    for attempt in range(2):
        try:
            with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS) as client:
                response = client.get(url)
            last_status = response.status_code
            if response.status_code == 200 or response.status_code == 404 or response.status_code == 429:
                return response, "", last_status
            if 400 <= response.status_code < 500:
                return response, "", last_status
            last_error = f"SEC returned HTTP {response.status_code}"
            if attempt == 0:
                continue
        except httpx.TimeoutException:
            last_error = "SEC filing request timed out"
            if attempt == 0:
                continue
        except httpx.HTTPError as exc:
            last_error = f"SEC filing transport error: {exc}"
            break
    return None, last_error, last_status


def _status_result(
    provider: EvidenceProvider,
    response: httpx.Response,
    *,
    url: str,
    not_found_message: str,
) -> ProviderResult | None:
    """Map a non-200 response. ``None`` means the caller may continue."""
    code = response.status_code
    if code == 200:
        return None
    if code == 404:
        return _result(
            provider,
            ProviderStatus.NOT_FOUND,
            error_message=not_found_message,
            metadata={"url": url, "status_code": 404},
        )
    if code == 429:
        return _result(
            provider,
            ProviderStatus.RATE_LIMITED,
            error_message="SEC rate limit (429)",
            metadata={"url": url, "status_code": 429},
        )
    return _result(
        provider,
        ProviderStatus.SOURCE_UNAVAILABLE,
        error_message=f"SEC returned HTTP {code}",
        metadata={"url": url, "status_code": code},
    )


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


class SECFilingsProvider(EvidenceProvider):
    def __init__(self, user_agent: str) -> None:
        if not isinstance(user_agent, str):
            raise ValueError(
                "SECFilingsProvider requires a non-empty user_agent"
            )
        stripped = user_agent.strip()
        if not stripped:
            raise ValueError(
                "SEC Filings requires a User-Agent of the form "
                "'Name email@example.com'. The supplied value is empty. "
                "SEC rejects requests without a descriptive User-Agent."
            )
        if "@" not in stripped or "." not in stripped.split("@")[-1]:
            raise ValueError(
                "SEC Filings requires a User-Agent that contains a "
                "name and a contact email (e.g. 'Jane Doe jane@example.com'). "
                f"The supplied value {stripped!r} does not appear to contain "
                "an email address. SEC rejects requests without one."
            )
        self._user_agent = stripped

    @property
    def source_type(self) -> SourceType:
        return SourceType.SEC_FILING

    @property
    def source_name(self) -> str:
        return "SEC Filings"

    @property
    def is_fixture(self) -> bool:
        return False

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        missing = _missing_identifier(request)
        if missing is not None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=f"SECFilingsProvider requires request.{missing}",
            )

        cik = _normalize_cik(request.cik or "")
        if cik is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message="A valid numeric CIK is required",
            )

        accession = request.accession_number or ""
        headers = {"User-Agent": self._user_agent}
        submissions_url = SEC_SUBMISSIONS_URL.format(cik=cik)
        submissions, error, last_status = _fetch(submissions_url, headers)
        if submissions is None:
            return _result(
                self,
                ProviderStatus.SOURCE_UNAVAILABLE,
                error_message=error,
                metadata={
                    "url": submissions_url,
                    "last_status_code": last_status,
                },
            )
        mapped = _status_result(
            self,
            submissions,
            url=submissions_url,
            not_found_message="SEC submissions not found",
        )
        if mapped is not None:
            return mapped

        try:
            payload = submissions.json()
        except ValueError:
            return _result(
                self,
                ProviderStatus.MALFORMED_RESPONSE,
                error_message="SEC submissions JSON could not be parsed",
                metadata={"url": submissions_url, "status_code": 200},
            )

        primary, primary_error = _aligned_primary_document(payload, accession)
        if primary_error == "malformed":
            return _result(
                self,
                ProviderStatus.MALFORMED_RESPONSE,
                error_message=(
                    "SEC submissions JSON is missing aligned "
                    "accessionNumber and primaryDocument lists"
                ),
                metadata={"url": submissions_url, "status_code": 200},
            )
        if primary_error == "index" or primary is None and primary_error != "not_found":
            return _result(
                self,
                ProviderStatus.MALFORMED_RESPONSE,
                error_message=(
                    "SEC primaryDocument is an EDGAR index page, "
                    "which is not filing content"
                ),
                metadata={
                    "url": submissions_url,
                    "accession_number": accession,
                    "status_code": 200,
                },
            )
        if primary is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=(
                    f"Accession {accession} is not in SEC submissions"
                ),
                metadata={
                    "url": submissions_url,
                    "accession_number": accession,
                    "status_code": 200,
                },
            )

        document_url = SEC_PRIMARY_DOCUMENT_URL.format(
            cik_no_zeros=str(int(cik)),
            accn_no_dashes=accession.replace("-", ""),
            primary_document=primary,
        )
        document, error, last_status = _fetch(document_url, headers)
        if document is None:
            return _result(
                self,
                ProviderStatus.SOURCE_UNAVAILABLE,
                error_message=error,
                metadata={
                    "url": document_url,
                    "submissions_url": submissions_url,
                    "primary_document": primary,
                    "last_status_code": last_status,
                },
            )
        mapped = _status_result(
            self,
            document,
            url=document_url,
            not_found_message="SEC primary document not found",
        )
        if mapped is not None:
            return mapped

        body = document.text.strip()
        if not body:
            return _result(
                self,
                ProviderStatus.MALFORMED_RESPONSE,
                error_message="SEC filing returned an empty body",
                metadata={
                    "url": document_url,
                    "primary_document": primary,
                    "status_code": 200,
                },
            )
        if _body_is_filing_index(body, document_url):
            return _result(
                self,
                ProviderStatus.MALFORMED_RESPONSE,
                error_message=(
                    "Retrieved page is an EDGAR filing index, "
                    "not the primary filing document"
                ),
                metadata={
                    "url": document_url,
                    "primary_document": primary,
                    "status_code": 200,
                },
            )

        return _result(
            self,
            ProviderStatus.FOUND,
            raw_records=[{
                "text": body,
                "accession_number": accession,
                "filing_form": request.filing_form,
                "field_or_passage_hint": request.field_or_passage_hint,
                "url": document_url,
                "primary_document": primary,
            }],
            metadata={
                "url": document_url,
                "submissions_url": submissions_url,
                "primary_document": primary,
                "document_kind": "primary",
                "status_code": 200,
            },
        )


class FixtureSECFilingsProvider(EvidenceProvider):
    @property
    def source_type(self) -> SourceType:
        return SourceType.SEC_FILING

    @property
    def source_name(self) -> str:
        return "SEC Filings (fixture)"

    @property
    def is_fixture(self) -> bool:
        return True

    def retrieve(self, request: ProviderRequest) -> ProviderResult:
        import veda.providers.fixtures as fixtures

        missing = _missing_identifier(request)
        if missing is not None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=f"FixtureSECFilingsProvider requires request.{missing}",
            )

        cik = _normalize_cik(request.cik or "")
        if cik is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message="A valid numeric CIK is required",
            )

        key = (
            cik,
            request.accession_number,
            request.filing_form,
            request.field_or_passage_hint.strip(),
        )
        passage = fixtures.SEC_FILING_PASSAGES.get(key)

        if passage is None:
            return _result(
                self,
                ProviderStatus.NOT_FOUND,
                error_message=f"No fixture passage for {key!r}",
            )

        return _result(
            self,
            ProviderStatus.FOUND,
            raw_records=[{
                "text": passage["text"],
                "section": passage.get("section"),
                "accession_number": request.accession_number,
                "filing_form": request.filing_form,
                "field_or_passage_hint": request.field_or_passage_hint,
            }],
        )
