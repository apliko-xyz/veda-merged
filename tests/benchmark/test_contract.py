# filename: tests/benchmark/test_contract.py
# title: Benchmark contract v0.2
# layer: Test suite - benchmark
# status: Phase 9 readiness

from __future__ import annotations

import json
from pathlib import Path

from veda.benchmark.schema import (
    LOCKED_DISTRIBUTION,
    BenchmarkBundle,
    DependencyPathStep,
    validate_bundle,
)
from veda.benchmark.validate_bundle import main


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> BenchmarkBundle:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return BenchmarkBundle.model_validate(payload)


def test_tiny_valid_bundle_passes() -> None:
    bundle = _load("tiny_valid.json")
    assert bundle.manifest.domain_slice == "NV012"
    assert bundle.manifest.distribution_profile == "unconstrained"
    assert validate_bundle(bundle) == []
    assert {question.category for question in bundle.questions} == {"insufficient_evidence"}
    assert all(document.provenance_kind == "synthetic" for document in bundle.manifest.documents)


def test_tiny_invalid_bundle_reports_readable_errors() -> None:
    errors = validate_bundle(_load("tiny_invalid.json"))
    text = "\n".join(errors)
    assert "procurement amount" in text
    assert "synthetic document" in text
    assert "unknown span" in text
    assert "procurement tier" in text


def test_phase1b_distribution_is_locked_at_ten() -> None:
    bundle = _load("tiny_valid.json")
    questions = []
    sequence = 1
    for category, count in LOCKED_DISTRIBUTION.items():
        for _ in range(count):
            sample = bundle.questions[0].model_copy(update={
                "qid": f"q:nv012:{sequence:04d}",
                "category": category,
                "provenance_kind": "synthetic",
            })
            questions.append(sample)
            sequence += 1
    # The copied questions still cite the synthetic insufficient-evidence
    # documents. Rebuild them without those citations so only the
    # insufficient_evidence items keep the synthetic ground truth.
    cleaned = []
    for question in questions:
        if question.category == "insufficient_evidence":
            cleaned.append(question)
            continue
        cleaned.append(question.model_copy(update={
            "provenance_kind": "public",
            "asks_for": "other",
            "ground_truth": question.ground_truth.model_copy(update={
                "answer_type": "string",
                "is_no_answer": False,
                "answer": "placeholder",
                "abstention_reason": None,
                "expected_evidence_tier": "bounded_or_indeterminate",
                "supporting_span_ids": [],
                "supporting_chunk_ids": [],
            }),
        }))
    manifest = bundle.manifest.model_copy(update={
        "distribution_profile": "nv012_phase1b",
        "documents": [bundle.manifest.documents[0]],
    })
    valid = bundle.model_copy(update={
        "manifest": manifest,
        "questions": cleaned,
        "chunks": [bundle.chunks[0]],
    })
    assert sum(LOCKED_DISTRIBUTION.values()) == 10
    assert validate_bundle(valid) == []
    short = valid.model_copy(update={"questions": cleaned[:-1]})
    assert validate_bundle(short)


def test_dependency_path_must_cite_real_ids() -> None:
    bundle = _load("tiny_valid.json")
    question = bundle.questions[0].model_copy(update={
        "category": "dependency_path",
        "dependency_path": [DependencyPathStep(
            entity_id="entity:sec_edgar:vendor:0000936468",
            relationship_id="relationship:sec_edgar:0123456789abcdef",
            span_id="span:missing",
        )],
    })
    errors = validate_bundle(bundle.model_copy(update={"questions": [question, bundle.questions[1]]}))
    text = "\n".join(errors)
    assert "dependency entity" in text
    assert "dependency relationship" in text
    assert "dependency span" in text


def test_validate_bundle_cli_exit_codes() -> None:
    assert main(["--bundle", str(FIXTURES / "tiny_valid.json")]) == 0
    assert main(["--bundle", str(FIXTURES / "tiny_invalid.json")]) == 1
