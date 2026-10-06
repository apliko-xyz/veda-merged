"""
File: src/veda/benchmark/schema.py
Title: Benchmark contract v0.2
Layer: Benchmark
Status: Phase 9 readiness

Purpose
-------
Pydantic models for a NV012 benchmark bundle. Evidence chunks,
relationships, and source spans are the shared models. This module
does not invent a second identifier scheme for those objects.

Question ids use ``q:nv012:0001``. That id is a benchmark question
id, not an evidence id.

Does not
--------
Does not download filings, call Bedrock, or score a retrieval run.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from veda.provenance.graph import SourceSpan
from veda.shared.enums import EvidenceTier
from veda.shared.ids import parse_chunk_id, parse_document_id, parse_entity_id
from veda.shared.models import EvidenceChunk, Relationship


DOMAIN_SLICE = "NV012"
QID_RE = re.compile(r"^q:nv012:\d{4}$")

QuestionCategory = Literal[
    "evidence_retrieval",
    "relationship_matching",
    "hard_negative_rejection",
    "dependency_path",
    "insufficient_evidence",
]
AnswerType = Literal[
    "currency",
    "date",
    "number",
    "boolean",
    "string",
    "entity_ref",
    "no_answer",
]
RepresentationMode = Literal[
    "raw_text",
    "metadata_enriched",
    "structured_context",
]
ProvenanceKind = Literal["public", "synthetic"]
DistributionProfile = Literal["nv012_phase1b", "unconstrained"]
AsksFor = Literal["revenue", "procurement", "other"]

LOCKED_DISTRIBUTION: dict[str, int] = {
    "evidence_retrieval": 4,
    "hard_negative_rejection": 2,
    "relationship_matching": 1,
    "dependency_path": 1,
    "insufficient_evidence": 2,
}


class ManifestDocument(BaseModel):
    """One frozen corpus document and the spans it contributes."""

    source_url: str = Field(..., min_length=1)
    retrieval_timestamp: datetime
    native_id: str = Field(..., min_length=1)
    doc_id: str
    sha256: str
    doc_type: str = Field(..., min_length=1)
    uei: Optional[str] = None
    cage: Optional[str] = None
    cik: Optional[str] = None
    provenance_kind: ProvenanceKind
    limitations: list[str] = Field(default_factory=list)
    spans: list[SourceSpan] = Field(default_factory=list)

    @field_validator("doc_id")
    @classmethod
    def _doc_id(cls, value: str) -> str:
        if parse_document_id(value) is None:
            raise ValueError(f"doc_id is not canonical: {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class CorpusManifest(BaseModel):
    """Corpus header for one domain slice."""

    corpus_version: str = Field(..., min_length=1)
    domain_slice: str
    documents: list[ManifestDocument]
    entities: list[str] = Field(default_factory=list)
    distribution_profile: DistributionProfile = "unconstrained"

    @field_validator("domain_slice")
    @classmethod
    def _nv012_only(cls, value: str) -> str:
        if value != DOMAIN_SLICE:
            raise ValueError(f"domain_slice must be {DOMAIN_SLICE!r}, got {value!r}")
        return value

    @field_validator("entities")
    @classmethod
    def _entities_parse(cls, values: list[str]) -> list[str]:
        for value in values:
            if parse_entity_id(value) is None:
                raise ValueError(f"manifest entity is not canonical: {value!r}")
        return values


class DependencyPathStep(BaseModel):
    """One hop on a dependency path. The retrieval unit is a span."""

    entity_id: str
    relationship_id: str
    span_id: str

    @field_validator("entity_id")
    @classmethod
    def _entity(cls, value: str) -> str:
        if parse_entity_id(value) is None:
            raise ValueError(f"entity_id is not canonical: {value!r}")
        return value


class HardNegative(BaseModel):
    """A plausible wrong target that must be rejected."""

    doc_id: str
    span_id: str
    reason: str = Field(..., min_length=1)


class GroundTruth(BaseModel):
    """Expected answer and the spans that support it."""

    answer_type: AnswerType
    is_no_answer: bool = False
    answer: Any = None
    abstention_reason: Optional[str] = None
    expected_evidence_tier: EvidenceTier
    supporting_span_ids: list[str] = Field(default_factory=list)
    supporting_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_answer_shape(self) -> "GroundTruth":
        if self.is_no_answer or self.answer_type == "no_answer":
            if self.answer is not None:
                raise ValueError("no-answer ground truth requires answer null")
            if not self.abstention_reason:
                raise ValueError("no-answer ground truth requires an abstention reason")
        elif self.answer is None:
            raise ValueError("a scored answer requires a non-null answer")
        return self


class Question(BaseModel):
    """One benchmark question. ``qid`` is not an evidence identifier."""

    qid: str
    category: QuestionCategory
    provenance_kind: ProvenanceKind
    prompt: str = Field(..., min_length=1)
    asks_for: AsksFor = "other"
    ground_truth: GroundTruth
    hard_negatives: list[HardNegative] = Field(default_factory=list)
    dependency_path: list[DependencyPathStep] = Field(default_factory=list)

    @field_validator("qid")
    @classmethod
    def _qid(cls, value: str) -> str:
        if QID_RE.fullmatch(value) is None:
            raise ValueError(f"qid must look like q:nv012:0001, got {value!r}")
        return value


class RetrievalResult(BaseModel):
    """One retrieval run. representation_mode is a run parameter."""

    qid: str
    representation_mode: RepresentationMode
    retrieved_span_ids: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)

    @field_validator("qid")
    @classmethod
    def _qid(cls, value: str) -> str:
        if QID_RE.fullmatch(value) is None:
            raise ValueError(f"qid must look like q:nv012:0001, got {value!r}")
        return value


class BenchmarkBundle(BaseModel):
    """A manifest, its questions, and optional retrieval runs."""

    manifest: CorpusManifest
    questions: list[Question]
    relationships: list[Relationship] = Field(default_factory=list)
    chunks: list[EvidenceChunk] = Field(default_factory=list)
    retrieval_results: list[RetrievalResult] = Field(default_factory=list)


def validate_bundle(bundle: BenchmarkBundle) -> list[str]:
    """Return human-readable contract violations. Empty means valid."""
    errors: list[str] = []
    spans = _span_index(bundle)
    docs = {document.doc_id: document for document in bundle.manifest.documents}
    chunk_ids = {chunk.chunk_id: chunk for chunk in bundle.chunks}
    relationship_ids = {item.relationship_id for item in bundle.relationships}
    entity_ids = set(bundle.manifest.entities)
    for relationship in bundle.relationships:
        entity_ids.add(relationship.subject_id)
        entity_ids.add(relationship.object_id)
    procurement_values = _procurement_values(bundle)
    qids = [question.qid for question in bundle.questions]
    if len(qids) != len(set(qids)):
        errors.append("question ids must be unique")

    if bundle.manifest.distribution_profile == "nv012_phase1b":
        counts = {category: 0 for category in LOCKED_DISTRIBUTION}
        for question in bundle.questions:
            counts[question.category] = counts.get(question.category, 0) + 1
        if counts != LOCKED_DISTRIBUTION:
            errors.append(
                "nv012_phase1b distribution must be "
                "evidence_retrieval 4, hard_negative_rejection 2, "
                "relationship_matching 1, dependency_path 1, "
                f"insufficient_evidence 2; got {counts}"
            )

    cited_docs: dict[str, set[str]] = {doc_id: set() for doc_id in docs}
    for question in bundle.questions:
        errors.extend(_question_errors(
            question,
            spans=spans,
            docs=docs,
            chunk_ids=chunk_ids,
            relationship_ids=relationship_ids,
            entity_ids=entity_ids,
            procurement_values=procurement_values,
            cited_docs=cited_docs,
        ))

    for doc_id, document in docs.items():
        categories = cited_docs.get(doc_id, set())
        if document.provenance_kind != "synthetic":
            continue
        if not categories:
            errors.append(
                f"synthetic document {doc_id} is not used by an "
                "insufficient_evidence question"
            )
            continue
        if categories != {"insufficient_evidence"}:
            errors.append(
                f"synthetic document {doc_id} is cited by "
                f"{sorted(categories)}; synthetic documents are only "
                "allowed for insufficient_evidence"
            )

    known_qids = set(qids)
    for result in bundle.retrieval_results:
        if result.qid not in known_qids:
            errors.append(f"retrieval result qid {result.qid} is not in the bundle")
        if len(result.scores) != len(result.retrieved_span_ids):
            errors.append(
                f"retrieval result {result.qid} scores and span ids differ in length"
            )
        for span_id in result.retrieved_span_ids:
            if span_id not in spans:
                errors.append(
                    f"retrieval result {result.qid} cites unknown span {span_id}"
                )
    return errors


def _question_errors(
    question: Question,
    *,
    spans: dict[str, tuple[ManifestDocument, SourceSpan]],
    docs: dict[str, ManifestDocument],
    chunk_ids: dict[str, EvidenceChunk],
    relationship_ids: set[str],
    entity_ids: set[str],
    procurement_values: set[str],
    cited_docs: dict[str, set[str]],
) -> list[str]:
    errors: list[str] = []
    truth = question.ground_truth
    for span_id in truth.supporting_span_ids:
        located = spans.get(span_id)
        if located is None:
            errors.append(
                f"{question.qid} supporting span {span_id} is not in the manifest"
            )
            continue
        document, _span = located
        cited_docs.setdefault(document.doc_id, set()).add(question.category)
        if question.provenance_kind == "public" and document.provenance_kind == "synthetic":
            errors.append(
                f"{question.qid} uses synthetic document {document.doc_id} "
                "as ground truth for a public-evidence question"
            )
    for chunk_id in truth.supporting_chunk_ids:
        chunk = chunk_ids.get(chunk_id)
        if chunk is None or parse_chunk_id(chunk_id) is None:
            errors.append(
                f"{question.qid} supporting chunk {chunk_id} is not in the manifest"
            )
            continue
        document = docs.get(chunk.doc_id)
        if document is None:
            errors.append(
                f"{question.qid} chunk {chunk_id} has no manifest document"
            )
            continue
        cited_docs.setdefault(document.doc_id, set()).add(question.category)
        if question.provenance_kind == "public" and document.provenance_kind == "synthetic":
            errors.append(
                f"{question.qid} uses synthetic chunk {chunk_id} as public ground truth"
            )

    if question.asks_for == "revenue":
        if truth.expected_evidence_tier == EvidenceTier.PROCUREMENT_PROXY:
            errors.append(
                f"{question.qid} asks for revenue but expects a procurement tier"
            )
        if truth.answer is not None and str(truth.answer) in procurement_values:
            errors.append(
                f"{question.qid} uses a procurement amount as the revenue answer"
            )

    for negative in question.hard_negatives:
        if negative.doc_id not in docs:
            errors.append(
                f"{question.qid} hard negative cites unknown document {negative.doc_id}"
            )
        located = spans.get(negative.span_id)
        if located is None:
            errors.append(
                f"{question.qid} hard negative cites unknown span {negative.span_id}"
            )
        elif located[0].doc_id != negative.doc_id:
            errors.append(
                f"{question.qid} hard negative span {negative.span_id} "
                f"is not in document {negative.doc_id}"
            )
        if not negative.reason.strip():
            errors.append(f"{question.qid} hard negative is missing a reason")

    for step in question.dependency_path:
        if step.entity_id not in entity_ids:
            errors.append(
                f"{question.qid} dependency entity {step.entity_id} is not in the bundle"
            )
        if step.relationship_id not in relationship_ids:
            errors.append(
                f"{question.qid} dependency relationship {step.relationship_id} "
                "is not in the bundle"
            )
        if step.span_id not in spans:
            errors.append(
                f"{question.qid} dependency span {step.span_id} is not in the manifest"
            )
    return errors


def _span_index(
    bundle: BenchmarkBundle,
) -> dict[str, tuple[ManifestDocument, SourceSpan]]:
    index: dict[str, tuple[ManifestDocument, SourceSpan]] = {}
    for document in bundle.manifest.documents:
        for span in document.spans:
            index[span.span_id] = (document, span)
    return index


def _procurement_values(bundle: BenchmarkBundle) -> set[str]:
    values: set[str] = set()
    for document in bundle.manifest.documents:
        for span in document.spans:
            if span.kind == "row" and span.original_value is not None:
                values.add(str(span.original_value))
    return values


__all__ = [
    "LOCKED_DISTRIBUTION",
    "BenchmarkBundle",
    "CorpusManifest",
    "DependencyPathStep",
    "GroundTruth",
    "HardNegative",
    "ManifestDocument",
    "Question",
    "RetrievalResult",
    "validate_bundle",
]
