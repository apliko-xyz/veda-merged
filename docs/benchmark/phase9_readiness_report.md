# Phase 9 readiness report

## Git

```
origin  https://github.com/apliko-xyz/veda-merged (fetch)
origin  https://github.com/apliko-xyz/veda-merged (push)
```

The token in the live `git remote -v` URL is omitted here.

- Branch: `arcell/phase9-readiness`
- Base: `main`
- Pull request: https://github.com/apliko-xyz/veda-merged/pull/1 (draft; do not merge it from this kickoff).

Commits on the branch:

| Commit | Subject |
| --- | --- |
| `e83300c` | fix(usaspending): scope awards to the federal fiscal year |
| `93b8476` | fix(sec): fetch primary documents and anchor passage spans |
| `10aeba8` | feat(provenance): add a validated in-memory evidence graph |
| `9c615b8` | feat(benchmark): lock the NV012 contract v0.2 |
| `782943a` | docs(benchmark): record the phase 9 readiness report |

## Workstream 1 — USAspending

Award search now posts `filters.time_period` for the federal fiscal year (`{fy-1}-10-01` through `{fy}-09-30`), `award_type_codes` A–D, and the Defense Logistics Agency awarding-subtier filter (on by default). Documented fields only. `fiscal_year` is not a query parameter, and `Total Obligated Amount` is not requested.

`Award Amount` is `EvidenceCategory.PROCUREMENT_AWARD`, context-only, dated `FFY{year}` (`2023-10-01`..`2024-09-30` for FFY2024). It is not a company-FY obligation and not recognized revenue. Conflict comparison includes period start, end, and label, so FFY and company FY are not compared when both end in the same calendar year.

Pagination follows `page_metadata.hasNext` up to `usaspending_page_cap`. `pages_fetched`, `has_more`, and `truncated` are on the provider result. Truncation is a packet limitation.

Files: `src/veda/providers/usaspending.py`, `src/veda/providers/results.py`, `src/veda/providers/fixtures.py`, `src/veda/normalization/usaspending.py`, `src/veda/shared/periods.py`, `src/veda/pipeline/orchestrator.py`, `src/veda/pipeline/claim_extraction.py`, `src/veda/pipeline/conflict_detector.py`, and the provider, normalization, pipeline, smoke, CLI, API, and benchmark-adapter tests that had encoded the old obligation claim.

Tests added cover the request body, UEI preference, name-match metadata, the DLA filter switch, pagination stop and cap, the transaction flag, truncation as a limitation, and the FFY versus company-FY conflict key.

Deferred: period-scoped obligations are not summed. See open questions.

Tests whose expectations changed because they encoded blocker B1: smoke, orchestrator, end-to-end case 4, CLI, API, and the benchmark adapter no longer expect a `procurement_obligation` claim for the lifetime award. The award remains in the packet as context.

## Workstream 2 — SEC primary document, chunks, spans

The live filing provider reads `filings.recent.primaryDocument` aligned with `accessionNumber`, then fetches `https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primaryDocument}`. User-Agent checks and one retry on timeout or HTTP 5xx remain. An index URL or EDGAR index body is rejected.

`src/veda/normalization/chunking.py` converts HTML with `html.parser` (script and style dropped, paragraph and table-row breaks kept) and chunks on paragraph boundaries, `max_chars` 1500, no overlap. `chunk_id` is four digits, indexes 0–9999. Document `content_hash` is the SHA-256 of the normalized text.

Known hints locate by exact match, then case-insensitive match, then a documented regex (`revenues`, `subsidiary_relationship`, `government_exposure`, `customer_concentration`). Success sets `chunk_id`, `span_start`, and `span_end`. Failure or an unknown hint emits `MissingEvidence(EXTRACTION_FAILED)`. Unknown hints are not `GOVERNMENT_EXPOSURE`. A located revenue sentence without a parsed number is context-only. `$71,043 million` is not turned into a claim value.

`Assessment.chunks` carries the filing chunks.

Tests: `tests/normalization/test_chunking.py`, `tests/normalization/test_filings.py`, `tests/providers/test_sec_filings.py`.

Expectation changes: the Lockheed smoke packet is `SUPPORTED`. The old `SUPPORTED_WITH_LIMITATIONS` status came from treating the `revenues` hint as inferred government exposure. That was the defect. Orchestrator filing tests now require a span and no narrative claim.

## Workstream 3 — Provenance graph

New package `src/veda/provenance/`. `adapt_to_graph` returns that builder's canonical JSON.

Nodes: source document, source version, span (`text_range`, `field`, `row`, `cell`), transformation, evidence, evidence version, entity, claim, conflict, missing evidence, assessment. `calculation` and `review_decision` schemas are defined and not emitted. `EvidenceTier` is derived: SEC recognized revenue is `authoritative_consolidated`, USAspending awards and obligations are `procurement_proxy`, otherwise `bounded_or_indeterminate`. `authoritative_internal` is reserved.

Edges include `version_of`, `part_of`, `produced_by`, `derived_from`, `about`, `supports`, `participates_in`, `used_in`, `requires`, `supersedes`, and `related_to`. No self-loops and no edges to missing nodes. Assessment status is an attribute. Missing-evidence ids hash the explanation and sources. Evidence versions hash the logical evidence id, raw value, unit, currency, and source digest. Restatements sort by numeric value when both values are numbers, then by version id; the later version supersedes the earlier one in either merge order.

`Relationship` is emitted only when `parent_entity` is a canonical entity id. A name string emits nothing. The orchestrator still does not pass a parent id into `ReportingBoundary`, so fixture runs do not invent one.

`validate`, `trace`, and `impacted_by` are covered in `tests/provenance/test_graph.py`. `veda graph` writes fixture graph JSON. Graph digest ignores pipeline timestamps and assessment ids, so two fixture runs match.

Adapter tests were updated. They previously required the vendor self-loop, the dangling status edge, and `metadata` instead of `attrs`.

`veda.pipeline` claim extraction copies `evidence_tier` onto claims. Provider and normalizer modules carry `VERSION = "1"`.

Deferred: the SEC company-facts normalizer still uses a bare `except` around construction of one selected fact and returns no evidence for that tag. It does not print. Changing it into a warning object would alter every company-facts caller, so it was left in place. Calculation and review nodes are schemas only.

## Workstream 4 — Benchmark contract v0.2

`docs/benchmark/contract_v0.2.md`, `src/veda/benchmark/schema.py`, and `python -m veda.benchmark.validate_bundle --bundle <path>`.

`docs/benchmark/legacy/schema_models.py` is not in this tree. Compatible field names are kept. ID formats follow `veda.shared.ids`, with the mapping in the contract doc. `q:nv012:0001` is the benchmark question id.

`distribution_profile = nv012_phase1b` enforces 4 + 2 + 1 + 1 + 2 = 10. The two-question synthetic example is `unconstrained`, because that example cannot also satisfy the ten-question table. Synthetic documents are allowed only for `insufficient_evidence` and cannot ground a public question. Procurement row amounts cannot answer a revenue question. `expected_evidence_tier` is on every ground truth. `representation_mode` is only on `RetrievalResult`.

`docs/benchmark/phase1_inventory.md` now records the schema, NV012, the id mapping, and the 9-versus-10 correction (`context_ablation` was a run condition).

No corpus, no generated questions, no Bedrock calls, no retrieval baseline.

## Tests

```
python3 -m pytest tests --disable-warnings -q -p no:cacheprovider --override-ini='addopts='
```

Result: 978 passed, 1 warning, in about 2.3s.

The warning is the pre-existing `SyntaxWarning` for an invalid escape in the `veda.shared.ids` module docstring, or the Starlette deprecation from FastAPI's test client, depending on filter settings. Neither is a failure. The suite does not touch the network. HTTP tests use respx.

## Open questions

- USAspending has no exact-UEI filter on `POST /api/v2/search/spending_by_award/`. A known UEI is sent as `recipient_search_text`, which the contract describes as a search over name, UEI, and DUNS. That can still include affiliates. Confirm whether a later contract adds an exact UEI filter.
- `POST /api/v2/search/spending_by_transaction/` was verified in the same contracts tree. `Transaction Amount` is a string in the sample. It is behind `ProviderRequest.include_transactions` (default off). Award Amount is not summed into a period obligation. `POST /api/v2/transactions/` is a different per-award history endpoint and is not used.
- No local checkout of `apliko-xyz/veda-defense-mvp` was available, so this report cannot list schema fields from a local read beyond the kickoff's concept list (SourceSpan, Transformation, Relationship origin and review state, DependencyResult, ReviewDecisionRecord, backward and forward trace). Those concepts are represented. The MVP's `prefix_hash` ids, edgeless edges, and excerpt re-hashing were not adopted.
- The repository is already under `apliko-xyz/veda-merged`.
