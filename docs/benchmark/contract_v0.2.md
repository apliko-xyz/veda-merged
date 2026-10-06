# Benchmark contract v0.2

Status: locked for the NV012 kickoff. This file corrects the Phase 1A
inventory. It does not define a corpus.

## What this extends

Phase 1A said no benchmark schema existed. A prior Pydantic schema
(Entity, Relationship, EvidenceChunk, SourceDocument, CorpusManifest,
GroundTruth, HardNegative, DependencyPathStep, Question, RetrievalMode,
RetrievalResult) was the parent-task contract. `docs/benchmark/legacy/schema_models.py`
is not in this repository. Field names below stay compatible with that
list. Identifier formats follow `veda.shared.ids`.

| Prior field or id | v0.2 |
| --- | --- |
| `rel:<seq>` | `relationship:<source>:<hash>` from `veda.shared.ids.relationship_id` |
| `entity:<source>:<type>:<name>` | `entity:<source>:<type>:<native_id>` |
| `q:dla:0001` | `q:nv012:0001` (benchmark question id, not an evidence id) |
| `chunk:<doc_id>:<seq>` | `chunk:<doc_id>:<index>` with a 4-digit index from `veda.shared.ids.chunk_id` |
| `doc:<source>:<type>:<native_id>` | unchanged; produced by `veda.shared.ids.document_id` |
| dependency step `evidence_chunk_id` | `span_id` |
| `RetrievalMode` as a question category | `representation_mode` on `RetrievalResult` only |

Shared models are not redefined:

- `veda.shared.models.EvidenceChunk`
- `veda.shared.models.Relationship`
- `veda.provenance.graph.SourceSpan`

## Slice

- `domain_slice` is `NV012` only.
- `provenance_kind` is `public` or `synthetic` on every manifest document and every question.
- Synthetic documents are allowed only for `insufficient_evidence` (a private vendor with no revenue denominator, or a subsidiary whose financials are parent-consolidated). They are labeled and are never ground truth for a public-evidence question.

## Questions

Categories: `evidence_retrieval`, `relationship_matching`, `hard_negative_rejection`, `dependency_path`, `insufficient_evidence`.

`representation_mode` is `raw_text`, `metadata_enriched`, or `structured_context`. It is a run parameter on `RetrievalResult`, not a question category.

Answer types: `currency`, `date`, `number`, `boolean`, `string`, `entity_ref`, `no_answer`.

No-answer: `is_no_answer` true, `answer` null, and an abstention reason.

Each `GroundTruth` carries `expected_evidence_tier`.

## Distribution

The locked first slice (`distribution_profile = nv012_phase1b`) sums to 10:

| Category | Count |
| --- | --- |
| evidence_retrieval | 4 |
| hard_negative_rejection | 2 |
| relationship_matching | 1 |
| dependency_path | 1 |
| insufficient_evidence | 2 |

`context_ablation` is not a category. A bundle with `distribution_profile = unconstrained` may be smaller. The checked-in example is two synthetic `insufficient_evidence` questions and is unconstrained.

## Validation

`python -m veda.benchmark.validate_bundle --bundle <path>` exits 0 for a valid bundle and non-zero with one readable error per line otherwise.

Checks:

- supporting span and chunk ids exist on the manifest
- every hard negative cites a real document, a real span, and a reason
- a procurement amount is never the answer to a revenue question
- dependency paths reference real entity, relationship, and span ids
- synthetic documents follow the rule above
- `nv012_phase1b` bundles match the locked counts
