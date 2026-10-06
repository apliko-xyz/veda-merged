# filename: docs/benchmark/phase1_inventory.md
# title: Phase 1A - Benchmark Inventory and Contract
# layer: Benchmark documentation
# phase: Phase 1A
# status: Complete
# description:
#     Inventory of existing benchmark-related artifacts in veda-merged,
#     with the authoritative-version decision, schema-existence decision,
#     locked contract decisions, and the first bounded slice.
# responsibilities:
#     - Record what exists, what is empty, and what is missing.
#     - Lock the schema decision (CREATE schema_v0.2).
#     - Lock contract decisions for Phase 1B onward.
# non-responsibilities:
#     - Does not define the schema itself.
#     - Does not build the corpus.
#     - Does not generate questions.
# source:
#     AUTHORED - Phase 1A inspection, executed 2026-09-24.

# Phase 1A - Benchmark Inventory and Contract

Corrected 2026-10-06. The 2026-09-24 inventory said no schema existed,
used a DLA slice, and listed a distribution that sums to 9. Those
statements were wrong. Contract v0.2 in `docs/benchmark/contract_v0.2.md`
is the current contract. The notes below keep the original inventory
and record the correction.

- **Date:** 2026-09-24, corrected 2026-10-06
- **Owner:** Alexander Cyril
- **Repository:** veda-merged
- **Phase:** 1A - Inventory and Contract
- **Status:** CORRECTED by contract v0.2

---

## 1. Existing Artifacts

| Artifact | Path | Status | Next Action |
|---|---|---|---|
| Benchmark adapter | src/veda/adapters/benchmark_adapter.py | Exists (5235 bytes, Phase 8) | Preserve; do not modify |
| Benchmark adapter tests | tests/adapters/test_benchmark_adapter.py | Exists (5129 bytes) | Preserve |
| Graph adapter | src/veda/adapters/graph_adapter.py | Exists (10520 bytes) | Preserve |
| Benchmark package | src/veda/benchmark/ | Empty (no .py files) | Populate in Phase 1B |
| Provenance package | src/veda/provenance/ | Empty | Populate later phases |
| Fixtures directories | fixtures/ (5 subdirs) | Empty directories | Populate in Phase 1C |
| Docs directory | docs/ | Empty | Populate in Phase 1A-1H |
| Examples directory | examples/ | Empty | Not used in Phase 1 |
| Archive | archive/ (2 subdirs) | Empty | Not used in Phase 1 |
| Top-level benchmark dir | benchmark/ | NOT PRESENT | Create in Phase 1B |
| Benchmark tests dir | tests/benchmark/ | NOT PRESENT | Create in Phase 1E-1G |
| Benchmark scripts dir | scripts/benchmark/ | NOT PRESENT | Create in Phase 1C |

---

## 2. Authoritative Versions

- **Benchmark adapter version marker:** BENCHMARK_VERSION = "v0.1.0" (adapter output shape only - not the benchmark schema contract).
- **Benchmark schema:** A prior schema existed outside this tree (it is not at `docs/benchmark/legacy/schema_models.py`). Contract v0.2 extends its field names and uses `veda.shared.ids` where the id formats disagreed. See `docs/benchmark/contract_v0.2.md` and `src/veda/benchmark/schema.py`.
- **Questions:** NONE EXIST in this repository. The tiny fixtures under `tests/benchmark/fixtures/` are contract examples, not the NV012 corpus.
- **Corpus:** NONE EXISTS.
- **Fixtures:** Empty directories only.
- **Validation command:** PLANNED, not yet implemented: python -m veda.benchmark.validate_bundle --bundle <path>.

---

## 3. Schema-Existence Check

- **Authoritative schema exists?** YES, as of contract v0.2. The 2026-09-24 check recorded NO because `docs/benchmark/legacy/schema_models.py` is absent. The parent-task schema still defined the field names this contract extends.
- **Location:** `src/veda/benchmark/schema.py` and `docs/benchmark/contract_v0.2.md`
- **Legacy benchmark work in archive?** The legacy module is not in this repository.
- **Reusable fixtures?** NO (empty directories only)
- **Decision:** CREATE schema_v0.2 in Phase 1B

---

## 4. Schema Blockers

- No prior benchmark schema to extend or reconcile.
- src/veda/benchmark/ is empty - clean slate.
- fixtures/ is empty - clean slate.
- docs/ is empty - clean slate.
- Working tree is clean (git status --short returned no output).

No blockers. No legacy conflicts.

---

## 5. Contract Decisions (Locked)

| Decision | Value |
|---|---|
| Schema version | schema_v0.2 |
| Question ID format | q:nv012:0001 (benchmark question id; not an evidence id). Replaces q:dla:0001. |
| Document ID format | doc:<source>:<type>:<native_id> via veda.shared.ids.document_id |
| Chunk ID format | chunk:<doc_id>:<4-digit index> via veda.shared.ids.chunk_id. Replaces chunk:<doc_id>:<seq>. |
| Entity ID format | entity:<source>:<type>:<native_id> via veda.shared.ids.entity_id. Replaces entity:<source>:<type>:<name>. |
| Relationship ID format | relationship:<source>:<hash> via veda.shared.ids.relationship_id. Replaces rel:<seq>. |
| Domain slices | NV012 only for this slice. DLA was the wrong slice name. NSF stays out. |
| Question categories | evidence_retrieval, relationship_matching, hard_negative_rejection, dependency_path, insufficient_evidence. context_ablation is a run condition, not a category. |
| Answer types | currency, date, number, boolean, string, entity_ref, no_answer |
| No-answer representation | is_no_answer: true, answer: null, plus an abstention reason |
| Evidence tier | expected_evidence_tier on every GroundTruth |
| Provenance label | provenance_kind public or synthetic on every document and question |
| Dependency path format | sequence of {entity_id, relationship_id, span_id}. Replaces evidence_chunk_id. |
| Representation mode | raw_text, metadata_enriched, structured_context on RetrievalResult only |
| Validation command | python -m veda.benchmark.validate_bundle --bundle <path> |

---

## 6. First Bounded Slice

- **Domain:** NV012 (the 2026-09-24 "DLA" label was ambiguous and is withdrawn)
- **Corpus size:** 8-15 documents
- **Question count:** 10
- **NSF:** excluded from this slice
- **Sources:** SEC EDGAR, SEC Company Facts, USAspending, GAO/DoD OIG (if available), 2+ distractors

---

## 7. Question Distribution (Locked, = 10)

| Category | Count |
|---|---|
| evidence_retrieval | 4 |
| hard_negative_rejection | 2 |
| relationship_matching | 1 |
| dependency_path | 1 |
| insufficient_evidence | 2 |
| **Total** | **10** |

The 2026-09-24 table assigned the last question to `context_ablation` and the five listed counts were 4+2+1+1+1 = 9 while the total was labeled 10. `context_ablation` is a run condition (`representation_mode`), not a category. The two boundary cases are `insufficient_evidence`.

No categories outside the locked enum. `distribution_profile = nv012_phase1b` enforces this table. The two-question synthetic example is `unconstrained` and is not the slice.

---

## 8. Phase 1A Acceptance

- [x] Existing artifacts inventoried
- [x] Authoritative versions identified
- [x] Schema existence checked (decision: CREATE schema_v0.2)
- [x] Schema blockers documented (none)
- [x] DLA slice selected
- [x] Contract decisions locked
- [x] Working tree clean before writing

---

## 9. Repository Observations

- .env.example requires only SEC_API_USER_AGENT. No Bedrock/AWS variables yet.
- .gitignore protects .env, *.env, .venv/, caches. No changes needed for Phase 1.
- pyproject.toml uses src-layout; pytest runs from tests/; console entry point veda = veda.interfaces.cli:app.
- Bedrock credentials for Phase 1E must be provided outside the repo (Colab secrets or environment), never committed.

---

## 10. Phase 1A Completion

=================================================
PHASE 1A - INVENTORY AND CONTRACT - COMPLETE
=================================================
Existing artifacts:        INVENTORIED
Authoritative versions:    IDENTIFIED
Schema existence:          CHECKED (CREATE schema_v0.2)
Schema blockers:           NONE
DLA slice:                 SELECTED
Contract decisions:        LOCKED
Working tree:              CLEAN
Next phase:                PHASE 1B - BOUNDED CORPUS
=================================================