"""
File: src/veda/pipeline/claim_typing.py
Title: Claim Type Classification
Layer: Pipeline layer
Status: Merged prototype foundation — Phase 6

Purpose
-------
Maps one Evidence object's evidence_category to the claim type string
and confidence level the pipeline will attach to the Claim it
produces. This is the only place that mapping lives.

The mapping is deterministic and uses the frozen Phase 2 enums. No
new enum is introduced. Unknown categories return None so callers
skip the evidence rather than raise.

Public API
----------
classify_evidence(evidence) -> Optional[tuple[str, ConfidenceLevel]]

Does not
--------
Does not construct Claim objects.
Does not mutate Evidence.
Does not read any field other than evidence.evidence_category.
Does not raise on unknown category.

Design notes
------------
The mapping is a frozen dict. Adding a new category requires
updating this file and only this file.
"""

from __future__ import annotations

from typing import Optional

from veda.shared.enums import ConfidenceLevel, EvidenceCategory
from veda.shared.models import Evidence


_EVIDENCE_CATEGORY_TO_CLAIM: dict[EvidenceCategory, tuple[str, ConfidenceLevel]] = {
    EvidenceCategory.RECOGNIZED_REVENUE:     ("total_revenue",           ConfidenceLevel.HIGH),
    EvidenceCategory.PROCUREMENT_OBLIGATION: ("procurement_obligation",  ConfidenceLevel.HIGH),
    EvidenceCategory.CORPORATE_RELATIONSHIP: ("corporate_relationship",  ConfidenceLevel.MEDIUM),
    EvidenceCategory.CUSTOMER_CONCENTRATION: ("customer_concentration",  ConfidenceLevel.MEDIUM),
    EvidenceCategory.GOVERNMENT_EXPOSURE:    ("government_exposure",     ConfidenceLevel.MEDIUM),
    EvidenceCategory.ESTIMATE:               ("total_revenue",           ConfidenceLevel.LOW),
    EvidenceCategory.PROXY:                  ("total_revenue",           ConfidenceLevel.LOW),
}


def classify_evidence(
    evidence: Evidence,
) -> Optional[tuple[str, ConfidenceLevel]]:
    """
    Return (claim_type, confidence) for the evidence's category,
    or None when the category has no mapping.

    Never raises. Callers treat None as "skip this evidence".
    """
    return _EVIDENCE_CATEGORY_TO_CLAIM.get(evidence.evidence_category)


__all__ = ["classify_evidence"]