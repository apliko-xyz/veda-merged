# filename: tests/adapters/test_graph_adapter.py
# title: Adapter Layer - Graph Adapter Tests
# layer: Test suite - adapters
# status: Phase 9 readiness
# description:
#     adapt_to_graph returns the provenance builder's canonical JSON.
#     The previous shape (vendor self-loop, status node, metadata)
#     encoded defects and is no longer the contract.

from __future__ import annotations

from veda.adapters.graph_adapter import GRAPH_VERSION, adapt_to_graph
from veda.entity.resolver import FixtureEntityResolverSource
from veda.pipeline.orchestrator import ProviderBundle, run_assessment
from veda.providers.annual_reports import FixtureAnnualReportProvider
from veda.providers.sec_company_facts import FixtureSECCompanyFactsProvider
from veda.providers.sec_filings import FixtureSECFilingsProvider
from veda.providers.usaspending import FixtureUSAspendingProvider
from veda.shared.periods import RequestedPeriod


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        sec_company_facts=FixtureSECCompanyFactsProvider(),
        sec_filing=FixtureSECFilingsProvider(),
        usaspending=FixtureUSAspendingProvider(),
        annual_report=FixtureAnnualReportProvider(),
    )


def _resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource({
        "lockheed martin corp": [
            {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
        ],
    })


def _packet(vendor: str = "Lockheed Martin Corp", year: int = 2024):
    return run_assessment(
        vendor_name=vendor,
        requested_period=RequestedPeriod(fiscal_year=year, raw=str(year)),
        bundle=_bundle(),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )


def test_graph_version() -> None:
    graph = adapt_to_graph(_packet())
    assert graph["graph_version"] == GRAPH_VERSION


def test_returns_dict_with_nodes_and_edges() -> None:
    graph = adapt_to_graph(_packet())
    assert "nodes" in graph
    assert "edges" in graph
    assert "graph_digest" in graph


def test_entity_document_evidence_claim_and_assessment_nodes() -> None:
    graph = adapt_to_graph(_packet())
    types = {node["node_type"] for node in graph["nodes"]}
    assert "entity" in types
    assert "source_document" in types
    assert "evidence" in types
    assert "claim" in types
    assert "assessment" in types
    assert "span" in types
    assert "source_version" in types


def test_nodes_use_attrs() -> None:
    graph = adapt_to_graph(_packet())
    for node in graph["nodes"]:
        assert "node_id" in node
        assert "node_type" in node
        assert "label" in node
        assert "attrs" in node


def test_edges_have_ids_and_existing_endpoints() -> None:
    graph = adapt_to_graph(_packet())
    node_ids = {node["node_id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["edge_id"]
        assert edge["source_id"] in node_ids
        assert edge["target_id"] in node_ids
        assert edge["source_id"] != edge["target_id"]
        assert "attrs" in edge


def test_supports_and_used_in_edges_present() -> None:
    graph = adapt_to_graph(_packet())
    types = {edge["edge_type"] for edge in graph["edges"]}
    assert "supports" in types
    assert "used_in" in types
    assert "version_of" in types
    assert "derived_from" in types


def test_status_is_an_attribute() -> None:
    graph = adapt_to_graph(_packet())
    assessments = [
        node for node in graph["nodes"] if node["node_type"] == "assessment"
    ]
    assert assessments
    assert "assessment_status" in assessments[0]["attrs"]
    assert all(node["node_type"] != "status" for node in graph["nodes"])
    assert all(edge["edge_type"] != "assessment_yields_status" for edge in graph["edges"])


def test_nodes_and_edges_are_sorted() -> None:
    graph = adapt_to_graph(_packet())
    node_ids = [node["node_id"] for node in graph["nodes"]]
    edge_ids = [edge["edge_id"] for edge in graph["edges"]]
    assert node_ids == sorted(node_ids)
    assert edge_ids == sorted(edge_ids)


def test_deterministic_on_same_packet() -> None:
    packet = _packet()
    assert adapt_to_graph(packet) == adapt_to_graph(packet)


def test_unresolved_vendor_has_assessment_and_missing_evidence() -> None:
    graph = adapt_to_graph(_packet("Totally Fake Vendor Name"))
    types = {node["node_type"] for node in graph["nodes"]}
    assert "assessment" in types
    assert "missing_evidence" in types
