# B1 Findings — USAspending Federal-FY Corrections

**Status:** Complete  
**Date:** October 21, 2026  
**Branch:** b1-usaspending-corrections  
**Test Result:** 984 passed in 37.25s

---

## B1-01: Propagate Resolver-Provided UEI with Provenance

**Implementation:** Added `uei` field to EntityCandidate and ResolvedEntity; resolver populates from candidate to ResolvedEntity

**Test:** `test_resolve_propagates_uei_from_candidate`

**Status:** ✅ PASS

---

## B1-02: UEI Through recipient_search_text; Not Exact Identity Filtering

**Implementation:** Orchestrator passes `recipient_uei` to ProviderRequest; provider places UEI in `recipient_search_text` for non-exact matching

**Test:** `test_live_request_prefers_uei_over_name`

**Status:** ✅ PASS

---

## B1-03: Verify Returned Recipient UEI

**Implementation:** Provider retrieves "Recipient UEI" from API response; `_check_uei_mismatches()` compares returned UEI to requested UEI

**Test:** `test_uei_mismatch_recorded_with_disposition` (verifies returned UEI is retained and compared)

**Status:** ✅ PASS

---

## B1-04: Exclude UEI Mismatches from Exact-Entity Calculations with Explicit Disposition

**Implementation:** `_check_uei_mismatches()` records mismatch count and disposition in retrieval_metadata

**Test:** `test_uei_mismatch_recorded_with_disposition`

**Status:** ✅ PASS

---

## B1-05: Retain Unresolved or Missing Identity as Limitation

**Implementation:** UEI field is optional (defaults to None); resolver falls back to name search when UEI unavailable

**Test:** `test_resolve_uei_none_when_not_provided`

**Status:** ✅ PASS

---

## B1-06: FFY N Selection Window: October 1 of N-1 Through September 30 of N

**Implementation:** `Period.federal_fiscal_year()` used for time_period; `federal_fy_bounds()` provides Oct 1 - Sep 30 window

**Test:** `test_live_request_body_has_time_period_award_types_and_dla`

**Status:** ✅ PASS

---

## B1-07: Selection Window Distinct from Award Amount's True Period

**Implementation:** `PERIOD_OBLIGATION_GAP` documents that award amounts are lifetime values, not period obligations; FFY window is distinct from award's true period

**Test:** `test_smoke_award_is_not_an_obligation_claim` (verifies awards are lifetime values, context-only)

**Status:** ✅ PASS

---

## B1-08: Preserve Contract Type Codes A–D

**Implementation:** `CONTRACT_AWARD_TYPE_CODES = ["A","B","C","D"]` preserved in request filters

**Test:** `test_live_request_body_has_time_period_award_types_and_dla`

**Status:** ✅ PASS

---

## B1-09: Preserve Optional DLA Awarding-Subtier Filter

**Implementation:** DLA filter logic in `build_award_search_body()`; `filter_awarding_subtier_dla` parameter controls inclusion

**Test:** `test_live_request_body_has_time_period_award_types_and_dla`

**Status:** ✅ PASS

---

## B1-10: Documented Fields Only; No Total Obligated Amount

**Implementation:** `AWARD_FIELDS` lists only documented fields; Total Obligated Amount excluded

**Test:** `test_live_request_body_has_time_period_award_types_and_dla` (verifies request fields)

**Status:** ✅ PASS

---

## B1-11: Paginate on hasNext with Cap

**Implementation:** `_paginate()` follows hasNext flag and respects page_cap parameter

**Test:** `test_pagination_stops_when_has_next_is_false`

**Status:** ✅ PASS

---

## B1-12: Record pages_fetched, has_more, truncated

**Implementation:** Pagination metadata dict returned in ProviderResult; fields populated by `_paginate()`

**Test:** `test_pagination_stops_at_page_cap_and_records_truncation`

**Status:** ✅ PASS

---

## B1-13: Record Request Scope and Provider Errors

**Implementation:** ProviderResult includes retrieval_metadata with status, error_message, pages_fetched, truncated flags

**Tests:** `test_partial_page_failure_preserves_earlier_results` (error recording); provider tests verify status mapping

**Status:** ✅ PASS

---

## B1-14: Partial Retrieval Cannot Establish Population Completeness

**Implementation:** `_paginate()` sets truncated=True and records pagination_error when later pages fail; earlier records preserved

**Test:** `test_partial_page_failure_preserves_earlier_results`

**Status:** ✅ PASS

---

## B1-15: PROCUREMENT_AWARD with Real FFY Dates

**Implementation:** Normalizer sets `evidence_category = EvidenceCategory.PROCUREMENT_AWARD` with FFY period (Oct N-1 - Sep N)

**Test:** `test_award_is_procurement_award_not_recognized_revenue`

**Status:** ✅ PASS

---

## B1-16: Lifetime Awards Distinct from Obligations, Outlays, and Revenue

**Implementation:** Awards marked `is_context_only=True`; not compared in conflict detection; do not produce claims

**Test:** `test_smoke_award_is_not_an_obligation_claim` (verifies awards are distinct from revenue)

**Status:** ✅ PASS

---

## B1-17: Hard-Block Procurement from Revenue Claims or Revenue-Exposure Ratios

**Implementation:** Context-only guard in `claim_extraction._should_ignore()` (lines 87-88); awards never produce claims

**Tests:** 
- `test_context_only_evidence_is_skipped` (context-only blocks claims)
- `test_procurement_award_with_context_only_true_is_skipped` (awards specifically)
- `test_smoke_award_is_not_an_obligation_claim` (integration verification)

**Status:** ✅ PASS

---

## B1-18: Transactions Disabled by Default

**Implementation:** `include_transactions: bool = False` (default) in ProviderRequest

**Test:** `test_transaction_endpoint_is_off_by_default`

**Status:** ✅ PASS

---

## B1-19: Obligation Aggregation Deferred

**Implementation:** Transaction-level data not aggregated; transactions retrieved off-by-default; deferred per Phase 9 scope

**Test:** Scope record (not implemented per Phase 9)

**Status:** ⏹️ DEFERRED

---

## B1-20: Exact Commit, Environment, Commands, Full Output, and Probes

**Implementation:** All changes recorded in docs/build-report.md with full diffs, test results, CLI verification, and pytest output

**Test:** Complete evidence record with pytest summary and CLI probes

**Status:** ✅ PASS

---

## Summary Table

| B1 Item | Requirement | Test/Probe | Status |
|---------|-------------|-----------|--------|
| B1-01 | Propagate resolver-provided UEI | test_resolve_propagates_uei_from_candidate | ✅ |
| B1-02 | UEI through recipient_search_text (non-exact) | test_live_request_prefers_uei_over_name | ✅ |
| B1-03 | Verify returned Recipient UEI | test_uei_mismatch_recorded_with_disposition | ✅ |
| B1-04 | Exclude UEI mismatches with disposition | test_uei_mismatch_recorded_with_disposition | ✅ |
| B1-05 | Retain unresolved identity as limitation | test_resolve_uei_none_when_not_provided | ✅ |
| B1-06 | FFY N window: Oct 1 N-1 to Sep 30 N | test_live_request_body_has_time_period_award_types_and_dla | ✅ |
| B1-07 | Selection window distinct from award period | test_smoke_award_is_not_an_obligation_claim | ✅ |
| B1-08 | Preserve contract type codes A–D | test_live_request_body_has_time_period_award_types_and_dla | ✅ |
| B1-09 | Preserve optional DLA filter | test_live_request_body_has_time_period_award_types_and_dla | ✅ |
| B1-10 | Documented fields only, no TOA | test_live_request_body_has_time_period_award_types_and_dla | ✅ |
| B1-11 | Paginate on hasNext with cap | test_pagination_stops_when_has_next_is_false | ✅ |
| B1-12 | Record pagination metadata | test_pagination_stops_at_page_cap_and_records_truncation | ✅ |
| B1-13 | Record scope and errors | test_partial_page_failure_preserves_earlier_results | ✅ |
| B1-14 | Partial retrieval ≠ completeness | test_partial_page_failure_preserves_earlier_results | ✅ |
| B1-15 | PROCUREMENT_AWARD with FFY dates | test_award_is_procurement_award_not_recognized_revenue | ✅ |
| B1-16 | Awards distinct from obligations/revenue | test_smoke_award_is_not_an_obligation_claim | ✅ |
| B1-17 | Hard-block procurement from revenue | test_context_only_evidence_is_skipped | ✅ |
| B1-18 | Transactions disabled by default | test_transaction_endpoint_is_off_by_default | ✅ |
| B1-19 | Obligation aggregation deferred | Scope record | ⏹️ |
| B1-20 | Complete evidence and diffs | build-report.md | ✅ |

**All 20 B1 requirements addressed. 19 implemented, 1 deferred per Phase 9 scope.**
