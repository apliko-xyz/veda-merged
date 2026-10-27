"""
File: src/veda/entity/resolver.py
Title: Entity Resolver
Layer: Entity resolution and reporting boundary
Status: Merged prototype foundation — Phase 3
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field, field_validator

from veda.shared.enums import (
    ConfidenceLevel,
    EntityResolutionStatus,
    EntityType,
)
from veda.shared.ids import entity_id as make_entity_id
from veda.shared.models import ResolvedEntity


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


class EntityCandidate(BaseModel):
    """Typed entity candidate returned by a resolver source."""

    cik: str = Field(..., description="Numeric CIK, normalized to 10 digits.")
    ticker: Optional[str] = None
    title: str = Field(..., min_length=1)
    entity_type: Optional[EntityType] = None
    parent_entity: Optional[str] = None
    uei: Optional[str] = Field(
        default=None,
        description="Unique Entity Identifier (UEI) when known."
    )

    @field_validator("cik", mode="before")
    @classmethod
    def _normalize_cik(cls, value: object) -> str:
        text = str(value).strip()
        if not text.isdigit():
            raise ValueError(f"EntityCandidate.cik must be numeric, got {value!r}")
        if len(text) > 10:
            raise ValueError(
                f"EntityCandidate.cik must be at most 10 digits, got {value!r}"
            )
        return text.zfill(10)

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().upper()
        return normalized or None


@runtime_checkable
class EntityResolverSource(Protocol):
    """Protocol implemented by fixture and live resolver sources."""

    def lookup(self, query: str) -> list[EntityCandidate]:
        ...


class FixtureEntityResolverSource:
    """Deterministic, offline resolver source."""

    def __init__(
        self,
        entries: dict[str, list[dict | EntityCandidate]],
    ) -> None:
        self._by_name: dict[str, list[EntityCandidate]] = {}
        self._by_ticker: dict[str, list[EntityCandidate]] = {}

        for raw_key, raw_candidates in entries.items():
            candidates = [
                candidate
                if isinstance(candidate, EntityCandidate)
                else EntityCandidate(**candidate)
                for candidate in raw_candidates
            ]

            self._by_name.setdefault(
                raw_key.strip().casefold(),
                [],
            ).extend(candidates)

            for candidate in candidates:
                if candidate.ticker:
                    self._by_ticker.setdefault(
                        candidate.ticker,
                        [],
                    ).append(candidate)

    def lookup(self, query: str) -> list[EntityCandidate]:
        name_key = query.strip().casefold()
        ticker_key = query.strip().upper()

        by_cik: dict[str, EntityCandidate] = {}

        for candidate in self._by_name.get(name_key, []):
            by_cik[candidate.cik] = candidate

        for candidate in self._by_ticker.get(ticker_key, []):
            by_cik[candidate.cik] = candidate

        return list(by_cik.values())


class LiveEntityResolverSource:
    """SEC company-tickers source with per-instance in-memory caching."""

    def __init__(self, user_agent: str, timeout: float = 30.0) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("LiveEntityResolverSource requires a non-empty user_agent")

        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._by_name: dict[str, list[EntityCandidate]] = {}
        self._by_ticker: dict[str, list[EntityCandidate]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return

        with httpx.Client(
            headers=self._headers,
            timeout=self._timeout,
        ) as client:
            response = client.get(SEC_TICKERS_URL)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("SEC ticker response must be a JSON object")

        for index, row in enumerate(payload.values()):
            if not isinstance(row, dict):
                raise ValueError(f"SEC row {index} must be an object")
            if "cik_str" not in row:
                raise ValueError(
                    f"SEC row {index} missing required key 'cik_str': {row!r}"
                )
            if "title" not in row:
                raise ValueError(
                    f"SEC row {index} missing required key 'title': {row!r}"
                )

            candidate = EntityCandidate(
                cik=row["cik_str"],
                ticker=row.get("ticker"),
                title=row["title"],
            )

            self._by_name.setdefault(
                candidate.title.strip().casefold(),
                [],
            ).append(candidate)

            if candidate.ticker:
                self._by_ticker.setdefault(
                    candidate.ticker,
                    [],
                ).append(candidate)

        self._loaded = True

    def lookup(self, query: str) -> list[EntityCandidate]:
        self._load()

        name_key = query.strip().casefold()
        ticker_key = query.strip().upper()

        by_cik: dict[str, EntityCandidate] = {}

        for candidate in self._by_name.get(name_key, []):
            by_cik[candidate.cik] = candidate

        for candidate in self._by_ticker.get(ticker_key, []):
            by_cik[candidate.cik] = candidate

        return list(by_cik.values())


class EntityResolver:
    """Resolve an exact normalized name or ticker without guessing."""

    def __init__(
        self,
        user_agent: str,
        source: EntityResolverSource,
    ) -> None:
        if not isinstance(source, EntityResolverSource):
            raise TypeError(
                "source must implement EntityResolverSource "
                f"(got {type(source).__name__})"
            )

        self._user_agent = user_agent
        self._source = source

    def resolve(self, input_name: str) -> ResolvedEntity:
        if not isinstance(input_name, str):
            raise ValueError(
                "EntityResolver.resolve input_name must be a string, "
                f"got {type(input_name).__name__}"
            )

        trimmed = input_name.strip()

        if not trimmed:
            return ResolvedEntity(
                input_name="<empty>",
                resolution_method="empty_input",
                resolution_status=EntityResolutionStatus.NOT_FOUND,
                ambiguity_notes="Input name is empty after trimming.",
            )

        candidates = self._source.lookup(trimmed)

        if not candidates:
            return ResolvedEntity(
                input_name=trimmed,
                resolution_method="exact_name",
                resolution_status=EntityResolutionStatus.NOT_FOUND,
                ambiguity_notes=(
                    f"No exact name or ticker match found for {trimmed!r}."
                ),
            )

        if len(candidates) > 1:
            return ResolvedEntity(
                input_name=trimmed,
                resolution_method="exact_name",
                resolution_status=EntityResolutionStatus.AMBIGUOUS,
                ambiguity_notes=(
                    f"{len(candidates)} exact candidates found; "
                    "human disambiguation is required."
                ),
                candidates=[candidate.title for candidate in candidates],
            )

        candidate = candidates[0]
        canonical_id = make_entity_id(
            "sec_edgar",
            "vendor",
            candidate.cik,
        )

        method = (
            "exact_ticker"
            if candidate.ticker == trimmed.upper()
            else "exact_name"
        )

        return ResolvedEntity(
            input_name=trimmed,
            resolved_name=candidate.title,
            cik=candidate.cik,
            entity_id=canonical_id,
            uei=candidate.uei,
            entity_type=candidate.entity_type or EntityType.PUBLIC_COMPANY,
            parent_entity=candidate.parent_entity,
            resolution_method=method,
            resolution_status=EntityResolutionStatus.RESOLVED,
            resolution_confidence=ConfidenceLevel.HIGH,
            sec_browse_url=(
                "https://www.sec.gov/edgar/browse/"
                f"?CIK={candidate.cik}"
            ),
        )
