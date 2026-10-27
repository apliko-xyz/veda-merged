# B1 Repair Pass — Handoff for Repair 2 and Repair 1

**Date prepared:** 2026-10-27  
**Session:** Context boundary — Repair 3 complete, Repair 2/1 ready to start  
**Destination:** Next session resuming work on PR #2

---

## 1. Current PR #2 State

**Branch:** `b1-usaspending-corrections`

**PR:** https://github.com/apliko-xyz/veda-merged/pull/2

**Repairs Completed:**
- ✅ **Repair 4** (Remove Award-to-Obligation Mapping) — Commit `5a3456e`
- ✅ **Repair 3** (Pagination Metadata Validation) — Commit `51b965a`

**Test Status:**
- Full suite: **990 passed, 1 warning** in 54.94s
- Core B1 modules: **138 passing** (provider 38, claim extraction 28, normalization 19, orchestrator 30, resolver 27)

**Documentation:**
- Test counts **updated to 990** in:
  - `docs/b1-test-evidence.md`
  - `docs/b1-implementation.md`
  - `docs/build-report.md`
- All three documents must already have this commit with message: `docs(b1): update test counts after Repair 3 (+5 new tests)`
- **VERIFY THIS IS COMMITTED** before starting Repair 2

**Git status at handoff:**
```
Branch: b1-usaspending-corrections
Status: clean (no uncommitted changes)
Recent commits:
  040931d docs(b1): update test counts after Repair 3 (+5 new tests)
  51b965a Repair 3: Add focused regression tests for pagination validation
  2540e14 Repair 3: Validate pagination metadata strictly
  ff94e50 docs(b1): update test counts after Repair 4 (+1 new test)
  bfed01d docs(b1): record Repair 4 completion with test output
  5a3456e Repair 4: Remove PROCUREMENT_AWARD→procurement_obligation mapping
```

---

## 2. What Remains

### Repair 2 — Acquisition Failures and Request Scope Preservation
**Status:** NOT STARTED  
**Depends on:** Repair 4 ✅ and Repair 3 ✅  
**Blocks:** Repair 1

### Repair 1 — Recipient Identity Dispositions
**Status:** NOT STARTED  
**Depends on:** Repair 2 and Repair 3 ✅  
**Blocks:** Final PR acceptance

---

## 3. Repair 2 — Full Specification

### Alex's Requirement (Verbatim)
> "Acquisition failures: a later-page 429 loses its original error in the final assessment and becomes a misleading page-cap warning. First-page failures also lose their reason. Preserve provider status, request scope, actual error and completeness through export."

### Required Behavior

**Preserve through export:**
- Provider status (FOUND, NOT_FOUND, RATE_LIMITED, MALFORMED_RESPONSE, SOURCE_UNAVAILABLE, etc.)
- Original error message (not replaced by generic truncation message)
- Error class distinction:
  - Rate-limit/transport failure (429, 503, connection timeout)
  - Malformed response (invalid JSON, missing field, type mismatch)
  - Page-cap truncation (hit page limit, not an error)
- Request scope (recipient_search_text, federal_fiscal_year, awarding_agency, award_type_codes)
- Pages fetched (count of successful pages before failure)
- Completeness disposition (complete, page_cap_truncation, rate_limited, malformed, unknown)

**Distinguish clearly:**
- Page-cap truncation: `has_more=True` but hit `page_cap`
- Rate-limit/transport failure: HTTP 429, 503, connection error
- Malformed response: Invalid JSON, missing required field, type mismatch

**Preserve earlier rows on later-page failures:**
- If page 1-5 succeed but page 6 returns 429, keep results from pages 1-5
- Mark with `truncated=True` and include the original 429 error, not "page cap"

**Generic supported result does NOT imply complete procurement:**
- A ProviderResult with FOUND status and some records does not mean complete procurement data
- Must distinguish: did we get all records, or did we hit a page cap, or did we fail?
- Completeness must be explicit and preserved through packet export

### Files to Check/Modify
- `src/veda/providers/usaspending.py` — ProviderResult fields and error propagation
- `src/veda/pipeline/orchestrator.py` — Assessment assembly from ProviderResult
- `src/veda/pipeline/packet.py` — Export of status/error/scope/completeness
- `src/veda/shared/models.py` — ProviderResult and related data classes

### Tests Required (Focused Regression Tests)
1. **First-page 429** → explicit failure status, original 429 error preserved (not page-cap message)
2. **Later-page 429** → earlier rows preserved, original 429 error preserved, not described as page-cap
3. **Page cap reached** → truncation disposition set, distinct from failure
4. **Malformed response** → malformed disposition set, completeness explicit (not unknown)
5. **End-to-end status/error/scope/pages/completeness** → all fields present and correct in final packet

---

## 4. Repair 1 — Full Specification

### Alex's Requirement (Verbatim)
> "Recipient identity: mismatched and missing-UEI awards still normalize under the requested SEC entity, and the final packet loses the mismatch disposition. Preserve per-record matched/mismatched/unverified status and enforce exact-entity eligibility. Handle missing or malformed identifiers explicitly without losing the raw evidence."

### Required Behavior

**Preserve per-record:**
- `requested_uei` (what was sent to USAspending API)
- `returned_uei` (what USAspending API returned)
- `disposition` — one of:
  - `matched` — requested_uei == returned_uei (eligible for exact-entity)
  - `mismatched` — requested_uei != returned_uei (excluded from exact-entity)
  - `unverified` — request was name-only (no UEI sent), so no match possible
  - `missing_identifier` — returned_uei is None/blank/non-string (no match possible)

**Exact-entity eligibility:**
- Only records with `disposition="matched"` are eligible for exact-entity calculations
- Mismatched records quarantined or explicitly attributed, **never silently confirmed** under requested entity
- Must be explicit: "excluded due to mismatched UEI"

**Handle edge cases explicitly:**
- Missing/blank/non-string returned_uei → `disposition="missing_identifier"`, no crash
- Non-string returned_uei → catch AttributeError, set disposition, continue
- Name-only request (no UEI available) → `disposition="unverified"`, retain affiliate-inclusion limitation

**Raw evidence preserved:**
- Full Evidence record survives with all original fields
- Disposition fields added to Evidence model (or stored separately and merged in packet)

**Mixed results in same export:**
- Each record keeps its own disposition
- One record can be `matched`, another `mismatched`, another `unverified` in same batch
- Packet must preserve all dispositions

### Files to Check/Modify
- `src/veda/providers/usaspending.py` — _check_uei_mismatches() logic (already exists from B1 implementation)
- `src/veda/normalization/usaspending.py` — per-record disposition preservation
- `src/veda/shared/models.py` — Evidence or disposition fields (if needed)
- Packet assembly path — ensure dispositions survive export

### Tests Required (Focused Regression Tests)
1. **Matching UEI** → `disposition="matched"`, record eligible
2. **Mismatched UEI** → `disposition="mismatched"`, explicitly excluded
3. **Missing/blank/non-string UEI** → `disposition="missing_identifier"` or `unverified`, explicit limitation logged
4. **Non-string returned UEI** → no AttributeError, disposition set, record survives
5. **Name-only request** → `disposition="unverified"`, affiliate limitation present
6. **Mixed results** → each record keeps its own disposition in final packet
7. **Final packet** → disposition and raw evidence persist through export

---

## 5. Files to Read at Start of Next Session

1. `docs/b1-findings.md` — Background on B1 context and why these repairs matter
2. `docs/b1-implementation.md` — All implemented changes (including Repair 4 and 3)
3. `docs/b1-test-evidence.md` — Test count history and core B1 test breakdown
4. `docs/build-report.md` — Complete build report with commits, test results, CLI probes
5. `src/veda/providers/usaspending.py` — Main implementation file (very large; use grep/Read to navigate)
6. Alex's four-repair review (reference ClickUp task B1 or search PR #2 comments)

---

## 6. Remaining Definition of Done

- [ ] Repair 2 implemented with focused regression tests
- [ ] Repair 2 all tests passing (focused + full suite)
- [ ] Repair 1 implemented with focused regression tests
- [ ] Repair 1 all tests passing (focused + full suite)
- [ ] Full suite rerun, output recorded
- [ ] Final tested commit recorded
- [ ] Environment, commands, full outputs, before/after probes in `docs/build-report.md`
- [ ] "19 implemented / B1 complete" claims corrected in all four documents to "submitted for review; four repairs required"
- [ ] Historical evidence preserved (do not overwrite prior sections)
- [ ] PR #2 description updated with all four repairs and commit SHAs
- [ ] PR comment listing all four repairs and final commit SHA
- [ ] Alex tagged on ClickUp B1 task when ready for review

---

## 7. Work Order for Next Session

1. **Verify test count commit exists** — check that `docs/b1-*` files show 990 in committed state
2. **Start Repair 2** — implement, write focused tests, run full suite
3. **Document Repair 2 completion** — add results to `docs/build-report.md`
4. **Start Repair 1** — implement, write focused tests, run full suite
5. **Document Repair 1 completion** — add results to `docs/build-report.md`
6. **Update all four docs** — correct "19 implemented" to "submitted for review; four repairs complete"
7. **Final full suite run** — record output
8. **Commit and push** — each repair as separate commit with tests
9. **Update PR #2** — add comment with all four repairs and final SHA
10. **Tag Alex** — on ClickUp B1 task when ready for review

---

## 8. Boundaries (Still Enforced)

- ❌ **No transaction aggregation** — stays deferred
- ❌ **No new identity service** — use existing resolver
- ❌ **No resilience-library adoption** — status quo error handling
- ❌ **No B3 activation** — only B1
- ❌ **No Core integration** — stay isolated
- ❌ **No B2/B4 date changes**
- ✅ **No new branch** — repair within `b1-usaspending-corrections` / PR #2
- ⏸️ **No leave decision until Alex confirms break schedule**

---

## 9. Last Terminal Output (To Record in Build Report)

```
============================== 990 passed, 1 warning in 54.94s ==============================
```

**Breakdown:**
- Core B1 modules: 138 passing
  - provider: 38 (added 5 Repair 3 tests)
  - claim extraction: 28
  - normalization: 19
  - orchestrator: 30
  - resolver: 27
- Other modules: 852 passing
- Total: 990 passing

---

## 10. Critical Assumptions NOT to Make

- ⚠️ **File list may be incomplete** — locate actual files before editing
  - Check `src/veda/shared/models.py` to verify ProviderResult structure
  - Check `src/veda/pipeline/packet.py` to understand export flow
  - Grep for actual field names before writing tests
- ⚠️ **Do NOT overwrite historical evidence** in the four documents
  - Add a "Repair 2 Completion" and "Repair 1 Completion" section
  - Preserve all prior repair records and test output
- ⚠️ **Do NOT claim B1 complete**
  - B1 is "submitted for review; four repairs required"
  - Update all four docs to reflect this
- ⚠️ **Do NOT start Repair 1 until Repair 2 is verified**
  - Repair 2 must have all focused tests passing
  - Repair 2 must have full suite passing
  - Only then proceed to Repair 1
- ⚠️ **Do NOT assume ProviderResult or Assessment structure**
  - Read the actual models to verify field names
  - Check what _check_uei_mismatches() already returns
  - Verify retrieval_metadata and pagination_error fields exist and work

---

## 11. Quick Reference: Repair Commits So Far

| Repair | Commit | Change | Tests |
|--------|--------|--------|-------|
| 4 | `5a3456e` | Remove PROCUREMENT_AWARD mapping | +1 (984→985) |
| 3 | `51b965a` | Strict pagination validation + focused tests | +5 (985→990) |
| 2 | — | **NOT STARTED** | — |
| 1 | — | **NOT STARTED** | — |

---

## 12. How to Resume

**In the next session, start with:**

```bash
cd c:\Users\HP\Desktop\veda-merged
git status
# Should show: On branch b1-usaspending-corrections, nothing to commit, working tree clean

git log --oneline | head -10
# Verify 040931d (test count update) is the most recent commit

python -m pytest tests/ -q
# Should show: 990 passed, 1 warning in ~55s

# Then:
# 1. Read the five files listed in section 5
# 2. Grep for ProviderResult and retrieval_metadata to understand current structure
# 3. Map Repair 2 requirements to specific code locations
# 4. Write focused tests first
# 5. Implement changes
# 6. Run tests, record output
# 7. Repeat for Repair 1
```

---

## 13. Contact / Escalation

If the next session discovers:
- Missing or renamed fields in ProviderResult or Assessment
- Inconsistency between Repair 2 spec and actual code structure
- Test output that contradicts the required behavior
- Merge conflicts or unexplained test failures

**Then:** Do NOT proceed blindly. Document the issue, read the relevant code carefully, and ask for clarification before making assumptions.

---

**End of Handoff Document**

This handoff is complete and self-contained. Paste it into the next session at the start to resume Repair 2 and Repair 1 without reconstructing from prior chat history.
