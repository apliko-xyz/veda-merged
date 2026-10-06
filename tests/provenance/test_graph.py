# filename: tests/provenance/test_graph.py
# title: Provenance graph
# layer: Test suite - provenance
# status: Phase 9 readiness

from __future__ import annotations

from veda.entity.resolver import FixtureEntityResolverSource
from veda.pipeline.orchestrator import ProviderBundle, run_assessment
from veda.provenance.graph import (
    EDGE_SUPERSEDES,
    EDGE_VERSION_OF,
    GraphEdge,
    GraphNode,
    ProvenanceGraph,
    assessment_node_id,
    build_graph,
)
from veda.providers.annual_reports import FixtureAnnualReportProvider
from veda.providers.sec_company_facts import FixtureSECCompanyFactsProvider
from veda.providers.sec_filings import FixtureSECFilingsProvider
from veda.providers.usaspending import FixtureUSAspendingProvider
from veda.shared.enums import (
    AssessmentStatus,
    ComparisonResult,
    EntityType,
    SourceType,
)
from veda.shared.ids import claim_id as make_claim_id
from veda.shared.ids import conflict_id as make_conflict_id
from veda.shared.ids import evidence_id as make_evidence_id
from veda.shared.models import Conflict
from veda.shared.periods import RequestedPeriod


def _bundle(*, with_filing: bool = True) -> ProviderBundle:
    return ProviderBundle(
        sec_company_facts=FixtureSECCompanyFactsProvider(),
        sec_filing=FixtureSECFilingsProvider(),
        usaspending=FixtureUSAspendingProvider(),
        annual_report=FixtureAnnualReportProvider(),
        sec_filing_accession_number="0000936468-25-000009" if with_filing else None,
        sec_filing_form="10-K" if with_filing else None,
        sec_filing_passage_hint="revenues" if with_filing else None,
    )


def _resolver() -> FixtureEntityResolverSource:
    return FixtureEntityResolverSource({
        "lockheed martin corp": [
            {"cik": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
        ],
    })


def _packet(vendor: str = "Lockheed Martin Corp", *, with_filing: bool = True):
    return run_assessment(
        vendor_name=vendor,
        requested_period=RequestedPeriod(fiscal_year=2024, raw="2024"),
        bundle=_bundle(with_filing=with_filing),
        resolver_source=_resolver(),
        user_agent="Test test@example.com",
    )


def test_fixture_graph_validates_and_claims_trace() -> None:
    packet = _packet()
    graph = build_graph([packet], packet.chunks)
    assert graph.validate() == []
    kinds = {
        node.attrs.get("kind")
        for node in graph.nodes.values()
        if node.node_type == "span"
    }
    assert "field" in kinds
    assert "row" in kinds
    assert "text_range" in kinds
    for claim in packet.claims:
        traced = graph.trace(claim.claim_id)
        assert traced["span"]
        assert traced["source_version"]
        assert traced["source_document"]
        assert traced["evidence_version"]
        assert traced["transformation"]
    field_spans = [
        node for node in graph.nodes.values()
        if node.node_type == "span" and node.attrs.get("kind") == "field"
    ]
    assert any("us-gaap:Revenues" in str(node.attrs.get("locator")) for node in field_spans)
    row_spans = [
        node for node in graph.nodes.values()
        if node.node_type == "span" and node.attrs.get("kind") == "row"
    ]
    assert row_spans
    assert row_spans[0].attrs["locator"]


def test_conflict_connects_every_claim() -> None:
    packet = _packet(with_filing=False)
    revenue = next(claim for claim in packet.claims if claim.claim_type == "total_revenue")
    original = next(item for item in packet.evidence if item.evidence_id in revenue.evidence_ids)
    restated_id = make_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        original.entity_id,
        "FY2024",
        "RevenuesRestated",
    )
    restated = original.model_copy(update={"evidence_id": restated_id, "raw_value": 1})
    restated_claim_id = make_claim_id(
        original.entity_id,
        "total_revenue",
        "FY2024",
        [restated_id],
    )
    restated_claim = revenue.model_copy(update={
        "claim_id": restated_claim_id,
        "value": 1,
        "evidence_ids": [restated_id],
    })
    conflict = Conflict(
        conflict_id=make_conflict_id(revenue.claim_id, restated_claim_id),
        claim_type="total_revenue",
        entity_id=original.entity_id,
        reporting_period=revenue.reporting_period,
        claim_ids=[revenue.claim_id, restated_claim_id],
        evidence_ids=[original.evidence_id, restated_id],
        conflicting_values=[revenue.value, 1],
        comparison_result=ComparisonResult.CONFLICTS,
        reason="Restated revenue disagrees.",
        requires_human_review=True,
    )
    conflicting = packet.model_copy(update={
        "evidence": [*packet.evidence, restated],
        "claims": [*packet.claims, restated_claim],
        "conflicts": [conflict],
        "assessment_status": AssessmentStatus.CONFLICTING_EVIDENCE,
    })
    graph = build_graph([conflicting])
    assert graph.validate() == []
    participants = {
        edge.source_id
        for edge in graph.edges.values()
        if edge.edge_type == "participates_in" and edge.target_id == conflict.conflict_id
    }
    assert participants == {revenue.claim_id, restated_claim_id}


def test_source_hash_impact_is_limited_to_dependent_assessments() -> None:
    packet = _packet(with_filing=False)
    other = _packet("Totally Fake Vendor Name", with_filing=False)
    target = next(item for item in packet.evidence if item.source_type == SourceType.SEC_COMPANY_FACTS)
    digest = "ab" * 32
    document = target.document.model_copy(update={"content_hash": digest})
    replaced = target.model_copy(update={"document": document})
    mutated = packet.model_copy(update={
        "evidence": [
            replaced if item.evidence_id == target.evidence_id else item
            for item in packet.evidence
        ]
    })
    graph = build_graph([mutated, other])
    source_version = next(
        node.node_id
        for node in graph.nodes.values()
        if node.node_type == "source_version" and node.attrs.get("content_hash") == digest
    )
    impact = graph.impacted_by(source_version)
    assert impact.assessment_ids == [assessment_node_id(mutated)]
    assert assessment_node_id(other) not in impact.assessment_ids


def test_normalizer_version_impacts_only_its_evidence(monkeypatch) -> None:
    packet = _packet(with_filing=False)
    monkeypatch.setattr("veda.normalization.usaspending.VERSION", "2")
    graph = build_graph([packet])
    transformation = next(
        node for node in graph.nodes.values()
        if node.node_type == "transformation"
        and node.attrs.get("name") == "usaspending.normalize"
        and node.attrs.get("version") == "2"
    )
    impact = graph.impacted_by(transformation.node_id)
    usa_ids = {
        item.evidence_id
        for item in packet.evidence
        if item.source_type == SourceType.USASPENDING
    }
    sec_ids = {
        item.evidence_id
        for item in packet.evidence
        if item.source_type == SourceType.SEC_COMPANY_FACTS
    }
    assert set(impact.evidence_ids) == usa_ids
    assert not sec_ids & set(impact.evidence_ids)
    sec_transformation = next(
        node for node in graph.nodes.values()
        if node.attrs.get("name") == "sec_company_facts.normalize"
    )
    assert sec_transformation.attrs["version"] == "1"


def test_restatement_adds_supersedes_independent_of_order() -> None:
    packet = _packet(with_filing=False)
    revenue_evidence = next(
        item for item in packet.evidence
        if item.source_type == SourceType.SEC_COMPANY_FACTS
    )
    lower = packet.model_copy(update={
        "evidence": [
            item.model_copy(update={"raw_value": 100})
            if item.evidence_id == revenue_evidence.evidence_id else item
            for item in packet.evidence
        ]
    })
    higher = packet.model_copy(update={
        "evidence": [
            item.model_copy(update={"raw_value": 200})
            if item.evidence_id == revenue_evidence.evidence_id else item
            for item in packet.evidence
        ]
    })
    forward = build_graph([lower]).merge(build_graph([higher]))
    backward = build_graph([higher]).merge(build_graph([lower]))
    assert forward.graph_digest() == backward.graph_digest()
    supersedes = [
        edge for edge in forward.edges.values() if edge.edge_type == EDGE_SUPERSEDES
    ]
    assert len(supersedes) == 1
    newer = forward.nodes[supersedes[0].source_id]
    older = forward.nodes[supersedes[0].target_id]
    assert newer.attrs["raw_value"] == 200
    assert older.attrs["raw_value"] == 100
    assert newer.attrs["evidence_id"] == older.attrs["evidence_id"]


def test_merge_is_idempotent_and_runs_share_a_digest() -> None:
    first = _packet()
    second = _packet()
    graph = build_graph([first], first.chunks)
    assert graph.merge(graph).graph_digest() == graph.graph_digest()
    assert graph.graph_digest() == build_graph([second], second.chunks).graph_digest()
    assert first.assessment_id != second.assessment_id


def test_string_parent_emits_no_relationship_and_entity_id_does() -> None:
    packet = _packet(with_filing=False)
    named = packet.model_copy(update={
        "vendor": packet.vendor.model_copy(update={
            "entity_type": EntityType.SUBSIDIARY,
            "parent_entity": "LOCKHEED MARTIN CORP",
        })
    })
    named_graph = build_graph([named])
    assert all(edge.edge_type != "related_to" for edge in named_graph.edges.values())
    parent_id = "entity:sec_edgar:vendor:0000000001"
    linked = packet.model_copy(update={
        "vendor": packet.vendor.model_copy(update={
            "entity_type": EntityType.SUBSIDIARY,
            "parent_entity": parent_id,
        })
    })
    linked_graph = build_graph([linked])
    related = [edge for edge in linked_graph.edges.values() if edge.edge_type == "related_to"]
    assert len(related) == 1
    assert related[0].source_id == packet.vendor.entity_id
    assert related[0].target_id == parent_id
    assert related[0].source_id != related[0].target_id
    assert related[0].attrs["origin"] == "declared"


def test_missing_evidence_ids_do_not_collide() -> None:
    packet = _packet("Totally Fake Vendor Name", with_filing=False)
    first = packet.missing_evidence[0]
    extra = first.model_copy(update={"explanation": first.explanation + " (second)"})
    widened = packet.model_copy(update={"missing_evidence": [first, extra]})
    graph = build_graph([widened])
    missing = [node for node in graph.nodes.values() if node.node_type == "missing_evidence"]
    assert len(missing) == 2
    assert len({node.node_id for node in missing}) == 2


def test_validator_reports_structural_defects() -> None:
    node = GraphNode(node_id="n1", node_type="claim", label="claim")
    other = GraphNode(node_id="n2", node_type="span", label="span")
    loop = GraphEdge(
        edge_id="edge:loop",
        edge_type=EDGE_VERSION_OF,
        source_id="n1",
        target_id="n1",
    )
    dangling = GraphEdge(
        edge_id="edge:dangling",
        edge_type="supports",
        source_id="n1",
        target_id="missing",
    )
    _, violations = ProvenanceGraph.from_lists([node, node, other], [loop, dangling])
    text = "\n".join(violations)
    assert "duplicate node id n1" in text
    assert "self-loop" in text
    assert "dangling target missing" in text

    packet = _packet(with_filing=False)
    graph = build_graph([packet])
    document = next(item for item in graph.nodes.values() if item.node_type == "source_document")
    version = next(
        edge.source_id
        for edge in graph.edges.values()
        if edge.edge_type == EDGE_VERSION_OF and edge.target_id == document.node_id
    )
    graph.edges["edge:cycle"] = GraphEdge(
        edge_id="edge:cycle",
        edge_type=EDGE_VERSION_OF,
        source_id=document.node_id,
        target_id=version,
    )
    assert any("cycle" in item for item in graph.validate())
