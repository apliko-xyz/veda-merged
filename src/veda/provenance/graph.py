"""
File: src/veda/provenance/graph.py
Title: In-memory provenance graph
Layer: Provenance
Status: Phase 9 readiness

Purpose
-------
Builds a validated, traversable graph from assessment packets. Spans
cover 10-K text, XBRL facts, and USAspending rows. Evidence versions
stay attached to a stable evidence id, so a restated value adds a
version instead of replacing the old one.

The graph is deterministic. Timestamps and pipeline assessment ids
are not part of node identity or the digest.

Does not
--------
Does not call a model, a database, or the network.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from veda.shared.enums import EntityResolutionStatus, SourceType
from veda.shared.ids import _stable_hash, parse_entity_id, relationship_id
from veda.shared.models import (
    Assessment,
    Evidence,
    EvidenceChunk,
    Relationship,
)


GRAPH_VERSION = "v0.2.0"

NODE_SOURCE_DOCUMENT = "source_document"
NODE_SOURCE_VERSION = "source_version"
NODE_SPAN = "span"
NODE_TRANSFORMATION = "transformation"
NODE_EVIDENCE = "evidence"
NODE_EVIDENCE_VERSION = "evidence_version"
NODE_ENTITY = "entity"
NODE_CLAIM = "claim"
NODE_CONFLICT = "conflict"
NODE_MISSING = "missing_evidence"
NODE_ASSESSMENT = "assessment"
NODE_CALCULATION = "calculation"
NODE_REVIEW = "review_decision"

EDGE_VERSION_OF = "version_of"
EDGE_PART_OF = "part_of"
EDGE_PRODUCED_BY = "produced_by"
EDGE_DERIVED_FROM = "derived_from"
EDGE_ABOUT = "about"
EDGE_SUPPORTS = "supports"
EDGE_PARTICIPATES = "participates_in"
EDGE_USED_IN = "used_in"
EDGE_REQUIRES = "requires"
EDGE_SUPERSEDES = "supersedes"
EDGE_NUMERATOR = "numerator_of"
EDGE_DENOMINATOR = "denominator_of"
EDGE_RELATED = "related_to"

_CYCLE_EDGE_TYPES = frozenset({EDGE_DERIVED_FROM, EDGE_PART_OF, EDGE_VERSION_OF})


def _hid(prefix: str, parts: list[Any]) -> str:
    return f"{prefix}:{_stable_hash(parts, length=16)}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


class GraphNode(BaseModel):
    """One provenance node. ``attrs`` holds type-specific fields."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    node_type: str
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """One directed edge. ``edge_id`` hashes type and endpoints."""

    model_config = ConfigDict(frozen=True)

    edge_id: str
    edge_type: str
    source_id: str
    target_id: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class SourceSpan(BaseModel):
    """A pointer into one source version. Not only a character offset."""

    model_config = ConfigDict(frozen=True)

    span_id: str
    source_version_id: str
    kind: str
    locator: str
    excerpt: str = ""
    original_value: Any = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None


class Transformation(BaseModel):
    """A versioned fetch, parse, chunk, normalize, extract, or compare step."""

    model_config = ConfigDict(frozen=True)

    transformation_id: str
    name: str
    version: str
    kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    transformation_hash: str


class Calculation(BaseModel):
    """Reserved dependency-result shape. The pipeline does not emit it."""

    model_config = ConfigDict(frozen=True)

    calculation_id: str
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    result_pct: Optional[float] = None
    lower_bound_pct: Optional[float] = None
    upper_bound_pct: Optional[float] = None
    measure_type: str = "indeterminate"
    evidence_tier: str = "bounded_or_indeterminate"
    entity_boundary: Optional[str] = None
    equation: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)
    reproduction_hash: Optional[str] = None


class ReviewDecision(BaseModel):
    """Reserved review record. The pipeline does not emit it."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    target_id: str
    action: str
    rationale: str
    reviewer: str
    decided_at: str
    superseded_by: Optional[str] = None


@dataclass
class ImpactResult:
    """Forward impact of a source version, span, or transformation."""

    evidence_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    calculation_ids: list[str] = field(default_factory=list)
    assessment_ids: list[str] = field(default_factory=list)


@dataclass
class ProvenanceGraph:
    """Nodes and edges keyed by id. Merge is a set union plus supersedes."""

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[str, GraphEdge] = field(default_factory=dict)

    def merge(self, other: "ProvenanceGraph") -> "ProvenanceGraph":
        """Return the union of both graphs. Order does not matter."""
        merged = ProvenanceGraph()
        for graph in (self, other):
            for node in graph.nodes.values():
                current = merged.nodes.get(node.node_id)
                if current is None:
                    merged.nodes[node.node_id] = node
                    continue
                merged.nodes[node.node_id] = GraphNode(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    label=current.label,
                    attrs=_merge_attrs(current.attrs, node.attrs),
                )
            for edge in graph.edges.values():
                merged.edges[edge.edge_id] = edge
        merged._rebuild_supersedes()
        return merged

    def validate(self) -> list[str]:
        """Return sorted integrity violations. An empty list is a valid graph."""
        violations: list[str] = []
        node_ids = set(self.nodes)
        for edge in self.edges.values():
            if edge.source_id == edge.target_id:
                violations.append(
                    f"self-loop {edge.edge_id} on {edge.source_id}"
                )
            if edge.source_id not in node_ids:
                violations.append(
                    f"dangling source {edge.source_id} on {edge.edge_id}"
                )
            if edge.target_id not in node_ids:
                violations.append(
                    f"dangling target {edge.target_id} on {edge.edge_id}"
                )

        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges.values():
            if edge.edge_type not in _CYCLE_EDGE_TYPES:
                continue
            if edge.source_id in node_ids and edge.target_id in node_ids:
                outgoing[edge.source_id].append(edge.target_id)
        violations.extend(_cycle_violations(outgoing))

        for node in self.nodes.values():
            if node.node_type == NODE_CLAIM and not _claim_reaches_span(self, node.node_id):
                violations.append(
                    f"claim {node.node_id} has no supports path to a span and source_version"
                )
            if node.node_type == NODE_EVIDENCE_VERSION:
                kinds = {
                    edge.edge_type
                    for edge in self.edges.values()
                    if edge.source_id == node.node_id
                }
                if EDGE_DERIVED_FROM not in kinds:
                    violations.append(
                        f"evidence_version {node.node_id} has no derived_from"
                    )
                if EDGE_PRODUCED_BY not in kinds:
                    violations.append(
                        f"evidence_version {node.node_id} has no produced_by"
                    )
        return sorted(violations)

    def trace(self, node_id: str) -> dict[str, list[dict[str, Any]]]:
        """
        Backward trace.

        Levels are claim, evidence_version, transformation, span,
        source_version, source_document. Span entries include locator
        and excerpt.
        """
        if node_id not in self.nodes:
            return _empty_trace()
        start = self.nodes[node_id]
        if start.node_type == NODE_CLAIM:
            claim_ids = [node_id]
        elif start.node_type == NODE_ASSESSMENT:
            claim_ids = sorted(
                edge.source_id
                for edge in self.edges.values()
                if edge.edge_type == EDGE_USED_IN
                and edge.target_id == node_id
                and self.nodes.get(edge.source_id, GraphNode(node_id="", node_type="", label="")).node_type == NODE_CLAIM
            )
        else:
            claim_ids = []

        version_ids = sorted({
            edge.source_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_SUPPORTS and edge.target_id in set(claim_ids)
        })
        span_ids = sorted({
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_DERIVED_FROM and edge.source_id in set(version_ids)
        })
        transformation_ids = sorted({
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_PRODUCED_BY
            and edge.source_id in set(version_ids) | set(span_ids)
        })
        source_version_ids = sorted({
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_PART_OF and edge.source_id in set(span_ids)
        })
        document_ids = sorted({
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_VERSION_OF and edge.source_id in set(source_version_ids)
        })
        return {
            "claim": [_public_node(self.nodes[item]) for item in claim_ids if item in self.nodes],
            "evidence_version": [_public_node(self.nodes[item]) for item in version_ids if item in self.nodes],
            "transformation": [_public_node(self.nodes[item]) for item in transformation_ids if item in self.nodes],
            "span": [_span_view(self.nodes[item]) for item in span_ids if item in self.nodes],
            "source_version": [_public_node(self.nodes[item]) for item in source_version_ids if item in self.nodes],
            "source_document": [_public_node(self.nodes[item]) for item in document_ids if item in self.nodes],
        }

    def impacted_by(self, node_id: str) -> ImpactResult:
        """
        Forward impact from a source version, span, or transformation.

        Also accepts a source document, which fans out through its versions.
        """
        if node_id not in self.nodes:
            return ImpactResult()
        node = self.nodes[node_id]
        span_ids: set[str] = set()
        version_ids: set[str] = set()

        if node.node_type == NODE_SOURCE_DOCUMENT:
            version_nodes = [
                edge.source_id
                for edge in self.edges.values()
                if edge.edge_type == EDGE_VERSION_OF and edge.target_id == node_id
            ]
            for version_node in version_nodes:
                span_ids.update(_incoming(self, EDGE_PART_OF, version_node))
        elif node.node_type == NODE_SOURCE_VERSION:
            span_ids.update(_incoming(self, EDGE_PART_OF, node_id))
        elif node.node_type == NODE_SPAN:
            span_ids.add(node_id)
        elif node.node_type == NODE_TRANSFORMATION:
            for producer in _incoming(self, EDGE_PRODUCED_BY, node_id):
                produced = self.nodes.get(producer)
                if produced is None:
                    continue
                if produced.node_type == NODE_SPAN:
                    span_ids.add(producer)
                elif produced.node_type == NODE_EVIDENCE_VERSION:
                    version_ids.add(producer)
        else:
            return ImpactResult()

        for span_id in span_ids:
            version_ids.update(_incoming(self, EDGE_DERIVED_FROM, span_id))

        evidence_ids: set[str] = set()
        assessment_ids: set[str] = set()
        for version_id in version_ids:
            version = self.nodes.get(version_id)
            if version is None:
                continue
            evidence_ids.update(
                edge.target_id
                for edge in self.edges.values()
                if edge.edge_type == EDGE_VERSION_OF and edge.source_id == version_id
            )
            assessment_ids.update(version.attrs.get("assessment_node_ids") or [])

        claim_ids = {
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_SUPPORTS and edge.source_id in version_ids
        }
        conflict_ids = {
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_PARTICIPATES and edge.source_id in claim_ids
        }
        assessment_ids.update(
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type == EDGE_USED_IN and edge.source_id in claim_ids
        )
        calculation_ids = {
            edge.target_id
            for edge in self.edges.values()
            if edge.edge_type in {EDGE_NUMERATOR, EDGE_DENOMINATOR}
            and edge.source_id in version_ids
        }
        return ImpactResult(
            evidence_ids=sorted(evidence_ids),
            claim_ids=sorted(claim_ids),
            conflict_ids=sorted(conflict_ids),
            calculation_ids=sorted(calculation_ids),
            assessment_ids=sorted(assessment_ids),
        )

    def canonical_body(self) -> dict[str, Any]:
        """Sorted JSON-ready body. Digest is computed from this body."""
        nodes = [
            _jsonable(node.model_dump())
            for node in sorted(self.nodes.values(), key=lambda item: item.node_id)
        ]
        edges = [
            _jsonable(edge.model_dump())
            for edge in sorted(self.edges.values(), key=lambda item: item.edge_id)
        ]
        return {
            "edges": edges,
            "graph_version": GRAPH_VERSION,
            "nodes": nodes,
        }

    def graph_digest(self) -> str:
        """SHA-256 of the canonical body. Timestamps are not in the body."""
        encoded = json.dumps(
            self.canonical_body(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Canonical body plus its digest."""
        body = self.canonical_body()
        body["graph_digest"] = self.graph_digest()
        return body

    @staticmethod
    def from_lists(
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> tuple["ProvenanceGraph", list[str]]:
        """Build a graph from raw lists and report duplicate ids."""
        violations: list[str] = []
        node_map: dict[str, GraphNode] = {}
        edge_map: dict[str, GraphEdge] = {}
        for node in nodes:
            if node.node_id in node_map:
                violations.append(f"duplicate node id {node.node_id}")
            node_map[node.node_id] = node
        for edge in edges:
            if edge.edge_id in edge_map:
                violations.append(f"duplicate edge id {edge.edge_id}")
            edge_map[edge.edge_id] = edge
        graph = ProvenanceGraph(nodes=node_map, edges=edge_map)
        return graph, sorted(set(violations + graph.validate()))

    def _rebuild_supersedes(self) -> None:
        """Chain evidence versions by value, independent of insertion order."""
        self.edges = {
            edge_id: edge
            for edge_id, edge in self.edges.items()
            if edge.edge_type != EDGE_SUPERSEDES
        }
        grouped: dict[str, list[GraphNode]] = defaultdict(list)
        for node in self.nodes.values():
            if node.node_type != NODE_EVIDENCE_VERSION:
                continue
            evidence_key = node.attrs.get("evidence_id")
            if isinstance(evidence_key, str):
                grouped[evidence_key].append(node)
        for versions in grouped.values():
            ordered = sorted(versions, key=_version_sort_key)
            for older, newer in zip(ordered, ordered[1:]):
                self._add_edge(
                    EDGE_SUPERSEDES,
                    newer.node_id,
                    older.node_id,
                    attrs={"evidence_id": newer.attrs.get("evidence_id")},
                )

    def _add_node(self, node: GraphNode) -> None:
        current = self.nodes.get(node.node_id)
        if current is None:
            self.nodes[node.node_id] = node
            return
        self.nodes[node.node_id] = GraphNode(
            node_id=node.node_id,
            node_type=node.node_type,
            label=current.label,
            attrs=_merge_attrs(current.attrs, node.attrs),
        )

    def _add_edge(
        self,
        edge_type: str,
        source_id: str,
        target_id: str,
        *,
        attrs: Optional[dict[str, Any]] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> None:
        if source_id == target_id:
            return
        edge_key = _hid("edge", [edge_type, source_id, target_id])
        self.edges[edge_key] = GraphEdge(
            edge_id=edge_key,
            edge_type=edge_type,
            source_id=source_id,
            target_id=target_id,
            attrs=attrs or {},
            evidence_ids=sorted(evidence_ids or []),
        )


def assessment_node_id(packet: Assessment) -> str:
    """Stable assessment identity for digest and impact. Ignores timestamps."""
    period = packet.reporting_period
    parts: list[Any] = [
        packet.vendor.entity_id or packet.vendor.input_name,
        period.label,
        period.start.isoformat() if period.start else None,
        period.end.isoformat() if period.end else None,
        packet.assessment_status.value,
        sorted(claim.claim_id for claim in packet.claims),
        sorted(item.evidence_id for item in packet.evidence),
        sorted(
            (item.claim_type, item.reason.value, item.explanation)
            for item in packet.missing_evidence
        ),
    ]
    return _hid("assessment", parts)


def build_graph(
    assessments: Iterable[Assessment],
    chunks: Optional[Iterable[EvidenceChunk]] = None,
    relationships: Optional[Iterable[Relationship]] = None,
) -> ProvenanceGraph:
    """Build one graph from packets, filing chunks, and known relationships."""
    graph = ProvenanceGraph()
    packets = list(assessments)
    extra_chunks = list(chunks or [])
    extra_relationships = list(relationships or [])
    versions = _component_versions()

    for packet in packets:
        _add_packet(graph, packet, extra_chunks, extra_relationships, versions)
    graph._rebuild_supersedes()
    return graph


def _add_packet(
    graph: ProvenanceGraph,
    packet: Assessment,
    extra_chunks: list[EvidenceChunk],
    extra_relationships: list[Relationship],
    versions: dict[str, str],
) -> None:
    assessment_id = assessment_node_id(packet)
    entity_node = _entity_node(packet)
    graph._add_node(entity_node)
    graph._add_node(
        GraphNode(
            node_id=assessment_id,
            node_type=NODE_ASSESSMENT,
            label=packet.vendor.resolved_name or packet.vendor.input_name,
            attrs={
                "assessment_status": packet.assessment_status.value,
                "period_label": packet.reporting_period.label,
                "entity_id": packet.vendor.entity_id,
            },
        )
    )

    chunk_pool: dict[str, EvidenceChunk] = {
        chunk.chunk_id: chunk for chunk in packet.chunks
    }
    for chunk in extra_chunks:
        chunk_pool.setdefault(chunk.chunk_id, chunk)

    evidence_versions: dict[str, str] = {}
    for evidence in packet.evidence:
        version_node, span = _add_evidence(
            graph,
            evidence,
            assessment_id,
            entity_node.node_id,
            versions,
        )
        evidence_versions[evidence.evidence_id] = version_node
        if span is not None and evidence.location and evidence.location.chunk_id:
            chunk = chunk_pool.get(evidence.location.chunk_id)
            if chunk is not None:
                _add_chunk_span(graph, chunk, span.source_version_id, versions)

    for chunk in chunk_pool.values():
        _attach_orphan_chunk(graph, chunk, versions)

    for claim in packet.claims:
        graph._add_node(
            GraphNode(
                node_id=claim.claim_id,
                node_type=NODE_CLAIM,
                label=claim.claim_type,
                attrs={
                    "claim_type": claim.claim_type,
                    "value": claim.value,
                    "unit": claim.unit,
                    "currency": claim.currency,
                    "evidence_tier": claim.evidence_tier.value,
                    "claim_status": claim.claim_status.value,
                    "assessment_node_ids": [assessment_id],
                },
            )
        )
        if entity_node.node_id:
            graph._add_edge(EDGE_ABOUT, claim.claim_id, entity_node.node_id)
        graph._add_edge(EDGE_USED_IN, claim.claim_id, assessment_id)
        for evidence_key in claim.evidence_ids:
            version_node = evidence_versions.get(evidence_key)
            if version_node is None:
                continue
            graph._add_edge(
                EDGE_SUPPORTS,
                version_node,
                claim.claim_id,
                evidence_ids=[evidence_key],
            )

    for conflict in packet.conflicts:
        graph._add_node(
            GraphNode(
                node_id=conflict.conflict_id,
                node_type=NODE_CONFLICT,
                label=conflict.claim_type,
                attrs={
                    "claim_type": conflict.claim_type,
                    "reason": conflict.reason,
                    "assessment_node_ids": [assessment_id],
                },
            )
        )
        for claim_key in sorted(set(conflict.claim_ids)):
            if claim_key in graph.nodes:
                graph._add_edge(EDGE_PARTICIPATES, claim_key, conflict.conflict_id)

    for ordinal, missing in enumerate(
        sorted(
            packet.missing_evidence,
            key=lambda item: (item.claim_type, item.reason.value, item.explanation),
        )
    ):
        missing_id = _hid(
            "missing_evidence",
            [
                assessment_id,
                ordinal,
                missing.claim_type,
                missing.reason.value,
                missing.explanation,
                sorted(missing.sources_checked),
            ],
        )
        graph._add_node(
            GraphNode(
                node_id=missing_id,
                node_type=NODE_MISSING,
                label=missing.reason.value,
                attrs={
                    "claim_type": missing.claim_type,
                    "reason": missing.reason.value,
                    "explanation": missing.explanation,
                    "sources_checked": sorted(missing.sources_checked),
                    "ordinal": ordinal,
                },
            )
        )
        graph._add_edge(EDGE_REQUIRES, assessment_id, missing_id)

    for relationship in _relationships_for(packet, extra_relationships):
        _add_relationship(graph, relationship)


def _add_evidence(
    graph: ProvenanceGraph,
    evidence: Evidence,
    assessment_id: str,
    entity_node_id: str,
    versions: dict[str, str],
) -> tuple[str, SourceSpan | None]:
    document_node, version_node, digest = _source_nodes(graph, evidence)
    span = _span_for(evidence, version_node, digest)
    profile = _profile(evidence.source_type, versions)
    parse_name, parse_version, parse_kind = profile["span"]
    normalize_name, normalize_version, normalize_kind = profile["evidence"]
    span_transformation = _transformation(
        graph, parse_name, parse_version, parse_kind
    )
    evidence_transformation = _transformation(
        graph, normalize_name, normalize_version, normalize_kind
    )
    if span is not None:
        graph._add_node(
            GraphNode(
                node_id=span.span_id,
                node_type=NODE_SPAN,
                label=span.locator,
                attrs=span.model_dump(),
            )
        )
        graph._add_edge(EDGE_PART_OF, span.span_id, version_node)
        graph._add_edge(EDGE_PRODUCED_BY, span.span_id, span_transformation)
    version_id = _hid(
        "evidence_version",
        [
            evidence.evidence_id,
            evidence.raw_value,
            evidence.unit,
            evidence.currency,
            digest,
        ],
    )
    graph._add_node(
        GraphNode(
            node_id=evidence.evidence_id,
            node_type=NODE_EVIDENCE,
            label=evidence.evidence_category.value,
            attrs={
                "evidence_category": evidence.evidence_category.value,
                "evidence_tier": evidence.evidence_tier.value,
                "source_type": evidence.source_type.value,
                "is_context_only": evidence.is_context_only,
            },
        )
    )
    graph._add_node(
        GraphNode(
            node_id=version_id,
            node_type=NODE_EVIDENCE_VERSION,
            label=evidence.evidence_category.value,
            attrs={
                "evidence_id": evidence.evidence_id,
                "raw_value": evidence.raw_value,
                "unit": evidence.unit,
                "currency": evidence.currency,
                "source_digest": digest,
                "evidence_tier": evidence.evidence_tier.value,
                "assessment_node_ids": [assessment_id],
                "source_document_id": document_node,
            },
        )
    )
    graph._add_edge(EDGE_VERSION_OF, version_id, evidence.evidence_id)
    if span is not None:
        graph._add_edge(
            EDGE_DERIVED_FROM,
            version_id,
            span.span_id,
            evidence_ids=[evidence.evidence_id],
        )
    graph._add_edge(EDGE_PRODUCED_BY, version_id, evidence_transformation)
    graph._add_edge(
        EDGE_ABOUT,
        evidence.evidence_id,
        entity_node_id,
        evidence_ids=[evidence.evidence_id],
    )
    return version_id, span


def _source_nodes(graph: ProvenanceGraph, evidence: Evidence) -> tuple[str, str, str]:
    if evidence.document is not None:
        doc_key = evidence.document.doc_id
        label = evidence.document.title
        digest = evidence.document.content_hash or evidence.accession_number or evidence.evidence_id
        doc_attrs = {
            "doc_id": doc_key,
            "doc_type": evidence.document.doc_type,
            "source_type": evidence.document.source_type.value,
            "url": evidence.document.url,
        }
    else:
        doc_key = evidence.evidence_id
        label = evidence.source_name
        digest = evidence.accession_number or evidence.evidence_id
        doc_attrs = {
            "doc_id": None,
            "source_type": evidence.source_type.value,
        }
    document_id = f"source_document:{doc_key}"
    version_id = _hid("source_version", [document_id, digest])
    graph._add_node(
        GraphNode(
            node_id=document_id,
            node_type=NODE_SOURCE_DOCUMENT,
            label=label,
            attrs=doc_attrs,
        )
    )
    graph._add_node(
        GraphNode(
            node_id=version_id,
            node_type=NODE_SOURCE_VERSION,
            label=str(digest),
            attrs={"content_hash": digest, "source_document_id": document_id},
        )
    )
    graph._add_edge(EDGE_VERSION_OF, version_id, document_id)
    return document_id, version_id, str(digest)


def _span_for(evidence: Evidence, source_version_id: str, digest: str) -> SourceSpan:
    location = evidence.location
    if (
        evidence.source_type == SourceType.SEC_FILING
        and location is not None
        and location.span_start is not None
        and location.span_end is not None
    ):
        kind = "text_range"
        locator = location.chunk_id or (
            f"{location.span_start}:{location.span_end}"
        )
        excerpt = location.source_reference or location.field_or_passage
        char_start = location.span_start
        char_end = location.span_end
        original = evidence.raw_value
    elif evidence.source_type == SourceType.SEC_COMPANY_FACTS or evidence.xbrl_tag:
        tag = evidence.xbrl_tag or (
            location.source_reference if location is not None else "Revenues"
        )
        if ":" not in str(tag):
            tag = f"us-gaap:{tag}"
        currency = evidence.currency or evidence.unit or "USD"
        start = evidence.reporting_period.start
        end = evidence.reporting_period.end
        window = (
            f"{start.isoformat()}..{end.isoformat()}"
            if start is not None and end is not None
            else evidence.reporting_period.label or ""
        )
        accession = evidence.accession_number or ""
        kind = "field"
        locator = f"{tag}|{currency}|{window}|{accession}"
        excerpt = "" if evidence.raw_value is None else str(evidence.raw_value)
        char_start = None
        char_end = None
        original = evidence.raw_value
    elif evidence.source_type == SourceType.USASPENDING:
        locator = ""
        if location is not None and location.source_reference:
            locator = location.source_reference
        elif location is not None:
            locator = location.field_or_passage
        else:
            locator = evidence.evidence_id
        kind = "row"
        excerpt = "" if evidence.raw_value is None else str(evidence.raw_value)
        char_start = None
        char_end = None
        original = evidence.raw_value
    else:
        kind = "field"
        locator = location.field_or_passage if location is not None else evidence.evidence_id
        excerpt = "" if evidence.raw_value is None else str(evidence.raw_value)
        char_start = None
        char_end = None
        original = evidence.raw_value
    span_id = _hid(
        "span",
        [source_version_id, kind, locator, char_start, char_end, original],
    )
    return SourceSpan(
        span_id=span_id,
        source_version_id=source_version_id,
        kind=kind,
        locator=locator,
        excerpt=excerpt,
        original_value=original,
        char_start=char_start,
        char_end=char_end,
    )


def _add_chunk_span(
    graph: ProvenanceGraph,
    chunk: EvidenceChunk,
    source_version_id: str,
    versions: dict[str, str],
) -> None:
    span_id = _hid(
        "span",
        [source_version_id, "text_range", chunk.chunk_id, chunk.char_start, chunk.char_end],
    )
    graph._add_node(
        GraphNode(
            node_id=span_id,
            node_type=NODE_SPAN,
            label=chunk.chunk_id,
            attrs={
                "span_id": span_id,
                "source_version_id": source_version_id,
                "kind": "text_range",
                "locator": chunk.chunk_id,
                "excerpt": chunk.text[:240],
                "original_value": None,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
            },
        )
    )
    graph._add_edge(EDGE_PART_OF, span_id, source_version_id)
    transformation = _transformation(
        graph,
        "filing.chunk",
        versions["chunk"],
        "chunk",
    )
    graph._add_edge(EDGE_PRODUCED_BY, span_id, transformation)


def _attach_orphan_chunk(
    graph: ProvenanceGraph,
    chunk: EvidenceChunk,
    versions: dict[str, str],
) -> None:
    """Attach a chunk whose document already has a source version."""
    if any(node.attrs.get("locator") == chunk.chunk_id for node in graph.nodes.values()):
        return
    document_id = f"source_document:{chunk.doc_id}"
    if document_id not in graph.nodes:
        return
    version_ids = [
        edge.source_id
        for edge in graph.edges.values()
        if edge.edge_type == EDGE_VERSION_OF and edge.target_id == document_id
    ]
    if not version_ids:
        return
    _add_chunk_span(graph, chunk, sorted(version_ids)[0], versions)


def _transformation(
    graph: ProvenanceGraph,
    name: str,
    version: str,
    kind: str,
) -> str:
    parameters = {"component": name, "version": version}
    transformation_id = _hid("transformation", [name, version, kind, parameters])
    digest = hashlib.sha256(
        json.dumps(
            {"kind": kind, "name": name, "parameters": parameters, "version": version},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    record = Transformation(
        transformation_id=transformation_id,
        name=name,
        version=version,
        kind=kind,
        parameters=parameters,
        transformation_hash=digest,
    )
    graph._add_node(
        GraphNode(
            node_id=transformation_id,
            node_type=NODE_TRANSFORMATION,
            label=f"{name}@{version}",
            attrs=record.model_dump(),
        )
    )
    return transformation_id


def _component_versions() -> dict[str, str]:
    from veda.normalization import annual_reports as annual_norm
    from veda.normalization import chunking as chunking_mod
    from veda.normalization import filings as filings_norm
    from veda.normalization import sec as sec_norm
    from veda.normalization import usaspending as usa_norm
    from veda.pipeline import claim_extraction as claims
    from veda.pipeline import conflict_detector as conflicts
    from veda.providers import annual_reports as annual_provider
    from veda.providers import sec_company_facts as facts_provider
    from veda.providers import sec_filings as filings_provider
    from veda.providers import usaspending as usa_provider

    return {
        "usaspending.fetch": usa_provider.VERSION,
        "usaspending.normalize": usa_norm.VERSION,
        "sec_facts.fetch": facts_provider.VERSION,
        "sec_facts.normalize": sec_norm.VERSION,
        "sec_filings.fetch": filings_provider.VERSION,
        "sec_filings.normalize": filings_norm.VERSION,
        "chunk": chunking_mod.VERSION,
        "annual.fetch": annual_provider.VERSION,
        "annual.normalize": annual_norm.VERSION,
        "extract": claims.VERSION,
        "compare": conflicts.VERSION,
    }


def _profile(source_type: SourceType, versions: dict[str, str]) -> dict[str, tuple[str, str, str]]:
    if source_type == SourceType.USASPENDING:
        version = versions["usaspending.normalize"]
        return {
            "span": ("usaspending.parse", version, "parse"),
            "evidence": ("usaspending.normalize", version, "normalize"),
        }
    if source_type == SourceType.SEC_COMPANY_FACTS:
        version = versions["sec_facts.normalize"]
        return {
            "span": ("sec_company_facts.parse", version, "parse"),
            "evidence": ("sec_company_facts.normalize", version, "normalize"),
        }
    if source_type == SourceType.SEC_FILING:
        return {
            "span": ("filing.chunk", versions["chunk"], "chunk"),
            "evidence": (
                "sec_filings.normalize",
                versions["sec_filings.normalize"],
                "normalize",
            ),
        }
    if source_type == SourceType.ANNUAL_REPORT:
        version = versions["annual.normalize"]
        return {
            "span": ("annual_report.parse", version, "parse"),
            "evidence": ("annual_report.normalize", version, "normalize"),
        }
    return {
        "span": ("generic.parse", "1", "parse"),
        "evidence": ("generic.normalize", "1", "normalize"),
    }


def _entity_node(packet: Assessment) -> GraphNode:
    entity_key = packet.vendor.entity_id
    if entity_key:
        node_id = entity_key
    else:
        node_id = _hid("entity", ["unresolved", packet.vendor.input_name])
    return GraphNode(
        node_id=node_id,
        node_type=NODE_ENTITY,
        label=packet.vendor.resolved_name or packet.vendor.input_name,
        attrs={
            "input_name": packet.vendor.input_name,
            "resolved_name": packet.vendor.resolved_name,
            "cik": packet.vendor.cik,
            "resolution_status": packet.vendor.resolution_status.value,
        },
    )


def _relationships_for(
    packet: Assessment,
    extra: list[Relationship],
) -> list[Relationship]:
    found = list(extra)
    parent = packet.vendor.parent_entity
    subject = packet.vendor.entity_id
    if (
        packet.vendor.resolution_status == EntityResolutionStatus.RESOLVED
        and subject
        and isinstance(parent, str)
        and parse_entity_id(parent) is not None
        and parent != subject
    ):
        found.append(
            Relationship(
                relationship_id=relationship_id(
                    SourceType.SEC_COMPANY_FACTS,
                    subject,
                    "subsidiary_of",
                    parent,
                ),
                subject_id=subject,
                predicate="subsidiary_of",
                object_id=parent,
                origin="declared",
                relationship_status="declared",
                review_state="unreviewed",
            )
        )
    return found


def _add_relationship(graph: ProvenanceGraph, relationship: Relationship) -> None:
    for entity_key, label in (
        (relationship.subject_id, relationship.subject_id),
        (relationship.object_id, relationship.object_id),
    ):
        if entity_key not in graph.nodes:
            graph._add_node(
                GraphNode(
                    node_id=entity_key,
                    node_type=NODE_ENTITY,
                    label=label,
                    attrs={"entity_id": entity_key},
                )
            )
    graph._add_edge(
        EDGE_RELATED,
        relationship.subject_id,
        relationship.object_id,
        attrs={
            "predicate": relationship.predicate,
            "origin": relationship.origin,
            "relationship_status": relationship.relationship_status,
            "review_state": relationship.review_state,
            "relationship_id": relationship.relationship_id,
            "span_ids": list(relationship.span_ids),
        },
        evidence_ids=list(relationship.evidence_ids),
    )


def _merge_attrs(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key == "assessment_node_ids":
            merged[key] = sorted(set(merged.get(key) or []) | set(value or []))
        elif key not in merged:
            merged[key] = value
    return merged


def _version_sort_key(node: GraphNode) -> tuple[Any, str]:
    raw = node.attrs.get("raw_value")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        encoded = json.dumps(raw, sort_keys=True, default=str)
        return (1, encoded, node.node_id)
    return (0, float(raw), node.node_id)


def _incoming(graph: ProvenanceGraph, edge_type: str, target_id: str) -> set[str]:
    return {
        edge.source_id
        for edge in graph.edges.values()
        if edge.edge_type == edge_type and edge.target_id == target_id
    }


def _public_node(node: GraphNode) -> dict[str, Any]:
    return {
        "attrs": _jsonable(node.attrs),
        "label": node.label,
        "node_id": node.node_id,
        "node_type": node.node_type,
    }


def _span_view(node: GraphNode) -> dict[str, Any]:
    view = _public_node(node)
    view["locator"] = node.attrs.get("locator")
    view["excerpt"] = node.attrs.get("excerpt")
    view["kind"] = node.attrs.get("kind")
    return view


def _empty_trace() -> dict[str, list[dict[str, Any]]]:
    return {
        "claim": [],
        "evidence_version": [],
        "transformation": [],
        "span": [],
        "source_version": [],
        "source_document": [],
    }


def _claim_reaches_span(graph: ProvenanceGraph, claim_id: str) -> bool:
    versions = [
        edge.source_id
        for edge in graph.edges.values()
        if edge.edge_type == EDGE_SUPPORTS and edge.target_id == claim_id
    ]
    for version_id in versions:
        spans = [
            edge.target_id
            for edge in graph.edges.values()
            if edge.edge_type == EDGE_DERIVED_FROM and edge.source_id == version_id
        ]
        for span_id in spans:
            if any(
                edge.edge_type == EDGE_PART_OF and edge.source_id == span_id
                for edge in graph.edges.values()
            ):
                return True
    return False


def _cycle_violations(outgoing: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    found: list[str] = []

    def walk(node_id: str) -> None:
        if node_id in visiting:
            found.append(f"cycle through {node_id}")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in outgoing.get(node_id, []):
            walk(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(outgoing):
        walk(node_id)
    return found


__all__ = [
    "GRAPH_VERSION",
    "Calculation",
    "GraphEdge",
    "GraphNode",
    "ImpactResult",
    "ProvenanceGraph",
    "ReviewDecision",
    "SourceSpan",
    "Transformation",
    "assessment_node_id",
    "build_graph",
]
