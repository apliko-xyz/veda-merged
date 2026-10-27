# filename: tests/entity/test_resolver.py
# title: Entity Layer - Resolver Tests
# layer: Test suite - entity
# status: Phase 1-6 test recovery
# description:
#     Verifies entity resolution: the Protocol shape, the fixture
#     source, the live source (mocked), and EntityResolver's three
#     outcomes (RESOLVED, AMBIGUOUS, NOT_FOUND).
#
#     Entity resolution is the first stage of the pipeline. If it
#     silently guesses, every downstream stage inherits a wrong
#     entity. The tests here assert the resolver never guesses.
#
# source:
#     AUTHORED - Phase 3 had no saved test before recovery began.
#     The resolver in src/veda/entity/resolver.py is the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - Live source tests use respx to intercept the httpx call to
#       SEC's company_tickers.json. No test reaches the network.
#     - The resolver must return exactly one of three statuses:
#       RESOLVED, AMBIGUOUS, NOT_FOUND. It never returns a "best
#       guess" when multiple candidates match.

from __future__ import annotations

import httpx
import pytest
import respx

from veda.entity.resolver import (
    EntityCandidate,
    EntityResolver,
    EntityResolverSource,
    FixtureEntityResolverSource,
    LiveEntityResolverSource,
    SEC_TICKERS_URL,
)
from veda.shared.enums import EntityResolutionStatus


# --------------------------------------------------------------------
# Sample data
# --------------------------------------------------------------------
LOCKHEED_ENTRIES = {
    "lockheed martin corp": [
        {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP", "uei": "N8JGY9B7XW61"},
    ],
    "lockheed martin": [
        {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP", "uei": "N8JGY9B7XW61"},
    ],
}

AMBIGUOUS_ENTRIES = {
    "abc technologies": [
        {"cik": 1111111, "ticker": "ABC", "title": "ABC Technologies Inc."},
        {"cik": 2222222, "ticker": None, "title": "ABC Technologies LLC"},
    ],
}


def _fixture_source(entries=None) -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource(entries or LOCKHEED_ENTRIES)


# ====================================================================
# 1. Protocol conformance
# ====================================================================

def test_fixture_source_satisfies_protocol() -> None:
    source = _fixture_source()
    assert isinstance(source, EntityResolverSource)


def test_live_source_satisfies_protocol() -> None:
    source = LiveEntityResolverSource(user_agent="Test test@example.com")
    assert isinstance(source, EntityResolverSource)


# ====================================================================
# 2. FixtureEntityResolverSource
# ====================================================================

def test_fixture_source_lookup_by_name() -> None:
    source = _fixture_source()
    results = source.lookup("Lockheed Martin Corp")
    assert len(results) == 1
    assert results[0].cik == "0000936468"


def test_fixture_source_lookup_case_insensitive() -> None:
    source = _fixture_source()
    results = source.lookup("LOCKHEED MARTIN CORP")
    assert len(results) == 1


def test_fixture_source_lookup_by_ticker() -> None:
    source = _fixture_source()
    results = source.lookup("LMT")
    assert len(results) == 1
    assert results[0].cik == "0000936468"


def test_fixture_source_lookup_unknown_returns_empty() -> None:
    source = _fixture_source()
    results = source.lookup("Nonexistent Corp")
    assert results == []


def test_fixture_source_ambiguous_returns_all_candidates() -> None:
    source = _fixture_source(AMBIGUOUS_ENTRIES)
    results = source.lookup("ABC Technologies")
    assert len(results) == 2


def test_fixture_source_deduplicates_by_cik() -> None:
    """An entry that matches both name and ticker is returned once."""
    source = _fixture_source()
    results = source.lookup("LMT")
    ciks = [c.cik for c in results]
    assert len(ciks) == len(set(ciks))


# ====================================================================
# 3. LiveEntityResolverSource with respx
# ====================================================================

@respx.mock
def test_live_source_loads_from_sec() -> None:
    respx.get(SEC_TICKERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "0": {"cik_str": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
            },
        )
    )
    source = LiveEntityResolverSource(user_agent="Test test@example.com")
    results = source.lookup("Lockheed Martin Corp")
    assert len(results) == 1
    assert results[0].cik == "0000936468"


@respx.mock
def test_live_source_caches_after_first_load() -> None:
    """Second lookup uses the cache; no second HTTP call."""
    route = respx.get(SEC_TICKERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"0": {"cik_str": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"}},
        )
    )
    source = LiveEntityResolverSource(user_agent="Test test@example.com")
    source.lookup("Lockheed Martin Corp")
    source.lookup("Lockheed Martin Corp")
    assert route.call_count == 1


@respx.mock
def test_live_source_handles_malformed_response() -> None:
    respx.get(SEC_TICKERS_URL).mock(
        return_value=httpx.Response(200, json="not a dict"),
    )
    source = LiveEntityResolverSource(user_agent="Test test@example.com")
    with pytest.raises(ValueError):
        source.lookup("Lockheed Martin Corp")


@respx.mock
def test_live_source_rejects_missing_cik_str() -> None:
    respx.get(SEC_TICKERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"0": {"ticker": "LMT", "title": "LOCKHEED MARTIN CORP"}},
        )
    )
    source = LiveEntityResolverSource(user_agent="Test test@example.com")
    with pytest.raises(ValueError):
        source.lookup("Lockheed Martin Corp")


@respx.mock
def test_live_source_rejects_missing_title() -> None:
    respx.get(SEC_TICKERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"0": {"cik_str": 936468, "ticker": "LMT"}},
        )
    )
    source = LiveEntityResolverSource(user_agent="Test test@example.com")
    with pytest.raises(ValueError):
        source.lookup("Lockheed Martin Corp")


def test_live_source_requires_user_agent() -> None:
    with pytest.raises(ValueError):
        LiveEntityResolverSource(user_agent="")


# ====================================================================
# 4. EntityResolver.resolve() outcomes
# ====================================================================

def test_resolve_exact_name_match() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("Lockheed Martin Corp")
    assert result.resolution_status == EntityResolutionStatus.RESOLVED
    assert result.cik == "0000936468"
    assert result.resolved_name == "LOCKHEED MARTIN CORP"
    assert result.entity_id is not None


def test_resolve_ticker_match() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("LMT")
    assert result.resolution_status == EntityResolutionStatus.RESOLVED
    assert result.resolution_method == "exact_ticker"


def test_resolve_name_match_uses_exact_name_method() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("Lockheed Martin Corp")
    assert result.resolution_method == "exact_name"


def test_resolve_unknown_returns_not_found() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("Totally Fake Vendor Name")
    assert result.resolution_status == EntityResolutionStatus.NOT_FOUND
    assert result.entity_id is None
    assert result.cik is None


def test_resolve_ambiguous_returns_ambiguous() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(AMBIGUOUS_ENTRIES),
    )
    result = resolver.resolve("ABC Technologies")
    assert result.resolution_status == EntityResolutionStatus.AMBIGUOUS
    assert len(result.candidates) == 2
    assert result.entity_id is None


def test_resolve_empty_input_returns_not_found() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("")
    assert result.resolution_status == EntityResolutionStatus.NOT_FOUND
    assert result.resolution_method == "empty_input"


def test_resolve_whitespace_only_returns_not_found() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("   ")
    assert result.resolution_status == EntityResolutionStatus.NOT_FOUND


def test_resolve_trims_input() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("  Lockheed Martin Corp  ")
    assert result.resolution_status == EntityResolutionStatus.RESOLVED


def test_resolve_rejects_non_string_input() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    with pytest.raises(ValueError):
        resolver.resolve(12345)   # type: ignore[arg-type]


def test_resolve_populates_sec_browse_url() -> None:
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=_fixture_source(),
    )
    result = resolver.resolve("Lockheed Martin Corp")
    assert result.sec_browse_url == "https://www.sec.gov/edgar/browse/?CIK=0000936468"


def test_resolve_constructor_rejects_non_protocol_source() -> None:
    with pytest.raises(TypeError):
        EntityResolver(
            user_agent="Test test@example.com",
            source="not a source",   # type: ignore[arg-type]
        )


def test_resolve_propagates_uei_from_candidate() -> None:
    """Verify that UEI from EntityCandidate is propagated to ResolvedEntity (B1-01)."""
    entries_with_uei = {
        "lockheed martin corp": [
            {
                "cik": 936468,
                "ticker": "LMT",
                "title": "LOCKHEED MARTIN CORP",
                "uei": "N8JGY9B7XW61",
            },
        ],
    }
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=FixtureEntityResolverSource(entries_with_uei),
    )
    result = resolver.resolve("Lockheed Martin Corp")
    assert result.resolution_status == EntityResolutionStatus.RESOLVED
    assert result.uei == "N8JGY9B7XW61"


def test_resolve_uei_none_when_not_provided() -> None:
    """Verify that UEI is None when EntityCandidate does not provide it."""
    entries_without_uei = {
        "example corp": [
            {
                "cik": 1234567,
                "ticker": "EXP",
                "title": "EXAMPLE CORP",
                # no uei field
            },
        ],
    }
    resolver = EntityResolver(
        user_agent="Test test@example.com",
        source=FixtureEntityResolverSource(entries_without_uei),
    )
    result = resolver.resolve("Example Corp")
    assert result.resolution_status == EntityResolutionStatus.RESOLVED
    assert result.uei is None