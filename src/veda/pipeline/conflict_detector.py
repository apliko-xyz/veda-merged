"""
File: src/veda/pipeline/conflict_detector.py
Title: Comparability-Aware Conflict Detection
Layer: Pipeline layer
Status: Merged prototype foundation — Phase 6

Purpose
-------
Detects conflicts between claims that are genuinely comparable.
Two claims are comparable only when all of these match:

    entity_id
    claim_type
    period fiscal year
    period start
    period end
    period label
    evidence_category
    unit
    currency

A federal fiscal year (label FFY2024, ending 30 September) and a
company fiscal year (label FY2024, ending 31 December) share an end
year and are still not comparable. The start, end, and label are
part of the key so those periods are never treated as the same
reporting window.

Because evidence_category is part of the key, recognized revenue and
procurement obligations are never compared. Their numerical
difference is never reported as a conflict.

Public API
----------
detect_conflicts(claims) -> list[Conflict]

Does not
--------
Does not make network calls.
Does not import veda.providers.fixtures.
Does not create conflicts across different evidence categories.
Does not create conflicts for missing values.
Does not resolve conflicts.
Does not mutate the input claims.

Design notes
------------
Determinism, frozen:

    claim_ids = sorted([claim_id_a, claim_id_b])
    evidence_ids = sorted(set(union of both claims' evidence_ids))
    conflicting_values follow the sorted claim_ids order
    conflict_id = conflict_id(claim_ids[0], claim_ids[1])
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from veda.shared.enums import ClaimStatus, ComparisonResult
from veda.shared.ids import conflict_id as make_conflict_id
from veda.shared.models import Claim, Conflict


_ELIGIBLE_STATUSES = frozenset({
    ClaimStatus.SUPPORTED,
    ClaimStatus.INFERRED,
})


def _comparability_key(claim: Claim) -> tuple:
    """Return the six-part comparability key for a claim."""
    period = claim.reporting_period
    return (
        claim.entity_id,
        claim.claim_type,
        period.fiscal_year(),
        period.start,
        period.end,
        period.label,
        claim.evidence_category,
        claim.unit,
        claim.currency,
    )


def detect_conflicts(claims: list[Claim]) -> list[Conflict]:
    """
    Return every comparable claim pair that disagrees.

    Claims in different comparability groups are never compared.
    Claims with a None value are skipped. Claims whose fiscal year
    cannot be determined are skipped.

    The returned list is sorted by conflict_id ascending.
    """
    groups: dict[tuple, list[Claim]] = defaultdict(list)

    for claim in claims:
        if claim.claim_status not in _ELIGIBLE_STATUSES:
            continue
        if claim.reporting_period.fiscal_year() is None:
            continue
        groups[_comparability_key(claim)].append(claim)

    conflicts: list[Conflict] = []

    for group in groups.values():
        if len(group) < 2:
            continue

        ordered = sorted(group, key=lambda c: c.claim_id)

        for a, b in combinations(ordered, 2):
            if a.value is None or b.value is None:
                continue
            if a.value == b.value:
                continue

            sorted_ids = sorted([a.claim_id, b.claim_id])
            value_by_id = {a.claim_id: a.value, b.claim_id: b.value}
            evidence_ids = sorted(set(a.evidence_ids) | set(b.evidence_ids))

            cid = make_conflict_id(sorted_ids[0], sorted_ids[1])

            conflicts.append(
                Conflict(
                    conflict_id=cid,
                    claim_type=a.claim_type,
                    entity_id=a.entity_id,
                    reporting_period=a.reporting_period,
                    claim_ids=sorted_ids,
                    evidence_ids=evidence_ids,
                    conflicting_values=[
                        value_by_id[sorted_ids[0]],
                        value_by_id[sorted_ids[1]],
                    ],
                    comparison_result=ComparisonResult.CONFLICTS,
                    reason=(
                        f"Claims for {a.claim_type!r} report different "
                        "values from independent sources for the same "
                        "entity and period."
                    ),
                    requires_human_review=True,
                )
            )

    conflicts.sort(key=lambda c: c.conflict_id)
    return conflicts


__all__ = ["detect_conflicts"]