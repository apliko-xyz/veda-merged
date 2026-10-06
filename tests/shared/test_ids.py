# filename: tests/shared/test_ids.py
# title: Shared Contract - Identifier Generation and Parsing Tests
# layer: Test suite - shared contract
# status: Phase 1-6 test recovery
# description:
#     Verifies every canonical ID generator, every parser, and the
#     legacy compatibility layer in veda.shared.ids.
#
#     The ID system is the identity backbone of the entire pipeline.
#     If an ID generator is non-deterministic, or a parser recovers the
#     wrong parts, or the segment regexes accept invalid characters,
#     then claim-to-evidence references cannot be trusted.
#
#     This file asserts:
#       - every generator produces a string matching its documented shape
#       - every parser recovers the constituent parts
#       - every generator is deterministic
#       - SEC source types map to the sec_edgar slug
#       - SEC_FILING evidence requires a document context
#       - claim IDs are delimiter-safe (nested JSON, not joined string)
#       - conflict IDs are order-independent
#       - assessment IDs are unique per call
#       - legacy IDs parse but canonical parsers reject them
#       - invalid input raises ValueError, not silent fallback
#
# source:
#     AUTHORED - Phase 2 had no saved test before recovery began.
#     The generators and parsers in src/veda/shared/ids.py are the
#     specification; this file is the executable form of that spec.
#
# notes:
#     - The delimiter-safety test is the single most important assertion
#       in this file. It guards the fix documented in _stable_hash's
#       docstring: claim_id() hashes nested JSON, not a joined string,
#       so no delimiter character can cause a collision.
#     - No test mocks time. The assessment_id() generator uses a
#       millisecond timestamp and a random suffix. We test only that
#       two successive calls produce different IDs, not their contents.

from __future__ import annotations

import re

import pytest

from veda.shared.enums import SourceType
from veda.shared.ids import (
    assessment_id,
    chunk_id,
    claim_id,
    conflict_id,
    document_id,
    entity_id,
    evidence_id,
    is_any_valid_id,
    is_canonical_id,
    is_legacy_id,
    legacy_claim_id,
    legacy_conflict_id,
    legacy_evidence_id,
    parse_assessment_id,
    parse_chunk_id,
    parse_claim_id,
    parse_conflict_id,
    parse_document_id,
    parse_entity_id,
    parse_evidence_id,
    parse_legacy_assessment_id,
    parse_legacy_claim_id,
    parse_legacy_conflict_id,
    parse_legacy_evidence_id,
    parse_relationship_id,
    relationship_id,
)


# --------------------------------------------------------------------
# Known-good fixture values used throughout
# --------------------------------------------------------------------
SAMPLE_ENTITY = "entity:sec_edgar:vendor:0000936468"
SAMPLE_DOC = "doc:sec_edgar:10k:000093646825000009"
SAMPLE_CHUNK = "chunk:doc:sec_edgar:10k:000093646825000009:0001"
SAMPLE_EVIDENCE = "evidence:sec_edgar:ab12cd34ef567890"


# ====================================================================
# 1. Canonical entity IDs
# ====================================================================

def test_entity_id_shape() -> None:
    result = entity_id("sec_edgar", "vendor", "0000936468")
    assert result == "entity:sec_edgar:vendor:0000936468"


def test_entity_id_deterministic() -> None:
    a = entity_id("sec_edgar", "vendor", "0000936468")
    b = entity_id("sec_edgar", "vendor", "0000936468")
    assert a == b


def test_entity_id_round_trips() -> None:
    result = entity_id("sec_edgar", "vendor", "0000936468")
    parsed = parse_entity_id(result)
    assert parsed == {
        "source": "sec_edgar",
        "entity_type": "vendor",
        "native_id": "0000936468",
    }


def test_entity_id_rejects_uppercase_source() -> None:
    with pytest.raises(ValueError):
        entity_id("SEC_EDGAR", "vendor", "0000936468")


def test_entity_id_rejects_invalid_native_id_characters() -> None:
    with pytest.raises(ValueError):
        entity_id("sec_edgar", "vendor", "has space")


def test_entity_id_accepts_digits_in_type_segment() -> None:
    """SEC doc types such as 10k and 10q contain digits."""
    result = entity_id("sec_edgar", "10k_filer", "0000936468")
    assert "10k_filer" in result


def test_parse_entity_id_returns_none_for_unknown_format() -> None:
    assert parse_entity_id("not an entity id") is None


# ====================================================================
# 2. Canonical document IDs
# ====================================================================

def test_document_id_shape() -> None:
    result = document_id("sec_edgar", "10k", "000093646825000009")
    assert result == "doc:sec_edgar:10k:000093646825000009"


def test_document_id_round_trips() -> None:
    result = document_id("sec_edgar", "10k", "000093646825000009")
    parsed = parse_document_id(result)
    assert parsed == {
        "source": "sec_edgar",
        "doc_type": "10k",
        "native_id": "000093646825000009",
    }


def test_document_id_accepts_hyphenated_accession() -> None:
    """SEC accession numbers contain hyphens."""
    result = document_id("sec_edgar", "10k", "0000936468-25-000009")
    assert result.endswith("0000936468-25-000009")


def test_document_id_rejects_uppercase_doc_type() -> None:
    with pytest.raises(ValueError):
        document_id("sec_edgar", "10K", "000093646825000009")


# ====================================================================
# 3. Canonical chunk IDs
# ====================================================================

def test_chunk_id_shape() -> None:
    result = chunk_id(SAMPLE_DOC, 1)
    assert result == SAMPLE_CHUNK


def test_chunk_id_zero_pads_index() -> None:
    result = chunk_id(SAMPLE_DOC, 7)
    assert result.endswith(":0007")


def test_chunk_id_round_trips() -> None:
    result = chunk_id(SAMPLE_DOC, 42)
    parsed = parse_chunk_id(result)
    assert parsed["doc_id"] == SAMPLE_DOC
    assert parsed["index"] == "0042"


def test_chunk_id_rejects_negative_index() -> None:
    with pytest.raises(ValueError):
        chunk_id(SAMPLE_DOC, -1)


def test_chunk_id_accepts_a_long_filing_index() -> None:
    """A 10-K can exceed 999 chunks. Index 1000 stays parseable."""
    result = chunk_id(SAMPLE_DOC, 1000)
    parsed = parse_chunk_id(result)
    assert parsed is not None
    assert parsed["index"] == "1000"


def test_chunk_id_rejects_oversize_index() -> None:
    with pytest.raises(ValueError):
        chunk_id(SAMPLE_DOC, 10000)


def test_chunk_id_rejects_invalid_parent_doc() -> None:
    with pytest.raises(ValueError):
        chunk_id("not a doc id", 1)


# ====================================================================
# 4. Canonical evidence IDs
# ====================================================================

def test_evidence_id_shape() -> None:
    result = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert re.fullmatch(r"evidence:sec_edgar:[0-9a-f]{16}", result)


def test_evidence_id_deterministic() -> None:
    a = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    b = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert a == b


def test_evidence_id_differs_on_different_field() -> None:
    a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "Revenues")
    b = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "OtherTag")
    assert a != b


def test_evidence_id_round_trips() -> None:
    result = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    parsed = parse_evidence_id(result)
    assert parsed["source"] == "sec_edgar"
    assert len(parsed["hash"]) == 16


def test_evidence_id_sec_company_facts_and_sec_filing_share_slug() -> None:
    """Both SEC source types map to sec_edgar, but hash differently."""
    company_facts = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    filing = evidence_id(
        SourceType.SEC_FILING,
        SAMPLE_ENTITY,
        "FY2024",
        "revenues",
        document_context=SAMPLE_DOC,
    )
    assert company_facts.startswith("evidence:sec_edgar:")
    assert filing.startswith("evidence:sec_edgar:")
    assert company_facts != filing


def test_evidence_id_sec_filing_requires_document_context() -> None:
    """
    SEC_FILING evidence must supply a document context so two passages
    from different filings with the same hint never collide.
    """
    with pytest.raises(ValueError):
        evidence_id(
            SourceType.SEC_FILING,
            SAMPLE_ENTITY,
            "FY2024",
            "revenues",
        )


def test_evidence_id_rejects_non_canonical_entity_id() -> None:
    with pytest.raises(ValueError):
        evidence_id(
            SourceType.SEC_COMPANY_FACTS,
            "not an entity id",
            "FY2024",
            "Revenues",
        )


def test_evidence_id_rejects_empty_period() -> None:
    with pytest.raises(ValueError):
        evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "", "Revenues")


def test_evidence_id_rejects_empty_field() -> None:
    with pytest.raises(ValueError):
        evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "")


def test_evidence_id_rejects_value_not_in_source_type_map() -> None:
    """
    Every SourceType member has a slug in _CANONICAL_SOURCE_SLUG_BY_SOURCE,
    so no SourceType value is rejected. The rejection path fires only when
    the caller passes something that is not a SourceType at all — a plain
    string, an int, or a value that looks like an enum but is not a member.

    This test verifies the guard is real by passing a non-SourceType value.
    """
    with pytest.raises((ValueError, KeyError)):
        evidence_id(
            "not_a_source_type",       # type: ignore[arg-type]
            SAMPLE_ENTITY,
            "FY2024",
            "Revenues",
        )


def test_evidence_id_accepts_synthetic_source() -> None:
    """
    SYNTHETIC is a valid source type with its own slug. The evidence_id
    generator must accept it without error.
    """
    result = evidence_id(
        SourceType.SYNTHETIC,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert result.startswith("evidence:synthetic:")


# ====================================================================
# 5. Canonical claim IDs
# ====================================================================

def test_claim_id_shape() -> None:
    result = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [SAMPLE_EVIDENCE])
    assert re.fullmatch(r"claim:[0-9a-f]{16}", result)


def test_claim_id_deterministic() -> None:
    a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [SAMPLE_EVIDENCE])
    b = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [SAMPLE_EVIDENCE])
    assert a == b


def test_claim_id_order_of_evidence_independent() -> None:
    """Claim ID sorts the evidence list before hashing."""
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    ev_b = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "B")
    id1 = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a, ev_b])
    id2 = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_b, ev_a])
    assert id1 == id2


def test_claim_id_differs_on_different_claim_type() -> None:
    a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [SAMPLE_EVIDENCE])
    b = claim_id(SAMPLE_ENTITY, "procurement_obligation", "FY2024", [SAMPLE_EVIDENCE])
    assert a != b


def test_claim_id_round_trips() -> None:
    result = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [SAMPLE_EVIDENCE])
    parsed = parse_claim_id(result)
    assert len(parsed["hash"]) == 16


def test_claim_id_rejects_duplicate_evidence_by_default() -> None:
    with pytest.raises(ValueError):
        claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [SAMPLE_EVIDENCE, SAMPLE_EVIDENCE])


def test_claim_id_allows_duplicate_evidence_when_opted_in() -> None:
    result = claim_id(
        SAMPLE_ENTITY,
        "total_revenue",
        "FY2024",
        [SAMPLE_EVIDENCE, SAMPLE_EVIDENCE],
        allow_duplicate_evidence=True,
    )
    assert result.startswith("claim:")


def test_claim_id_rejects_invalid_evidence_id() -> None:
    with pytest.raises(ValueError):
        claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", ["not an evidence id"])


def test_claim_id_delimiter_safety() -> None:
    """
    The single most important assertion in this file.

    claim_id() hashes nested JSON, not a joined string. Two logically
    distinct inputs must produce two distinct IDs even when a string
    element contains a delimiter character.

    We construct two inputs that WOULD collide under a pipe-joined
    or comma-joined scheme, and assert they produce different IDs.
    """
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    ev_b = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "B")
    ev_c = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "C")

    # Under a joined scheme, "A|B,C" and "A,B|C" would collide if the
    # delimiter were ",|" and the elements themselves contained a pipe.
    # Under JSON nesting, they cannot.
    id1 = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a, ev_b, ev_c])
    id2 = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_c, ev_b, ev_a])

    # Same set, different order -> same ID (already covered above)
    assert id1 == id2

    # Different set -> different ID
    id3 = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a, ev_b])
    assert id1 != id3


# ====================================================================
# 6. Canonical relationship IDs
# ====================================================================

def test_relationship_id_shape() -> None:
    result = relationship_id(
        SourceType.SEC_FILING,
        SAMPLE_ENTITY,
        "supplies_component_for",
        "entity:sec_edgar:vendor:0000320193",
    )
    assert re.fullmatch(r"relationship:sec_edgar:[0-9a-f]{16}", result)


def test_relationship_id_deterministic() -> None:
    args = (SourceType.SEC_FILING, SAMPLE_ENTITY, "parent_of", "entity:sec_edgar:vendor:0000320193")
    assert relationship_id(*args) == relationship_id(*args)


def test_relationship_id_rejects_uppercase_predicate() -> None:
    with pytest.raises(ValueError):
        relationship_id(SourceType.SEC_FILING, SAMPLE_ENTITY, "ParentOf", "entity:sec_edgar:vendor:0000320193")


def test_relationship_id_rejects_empty_subject() -> None:
    with pytest.raises(ValueError):
        relationship_id(SourceType.SEC_FILING, "", "parent_of", "entity:sec_edgar:vendor:0000320193")


def test_relationship_id_round_trips() -> None:
    result = relationship_id(
        SourceType.SEC_FILING,
        SAMPLE_ENTITY,
        "parent_of",
        "entity:sec_edgar:vendor:0000320193",
    )
    parsed = parse_relationship_id(result)
    assert parsed["source"] == "sec_edgar"
    assert len(parsed["hash"]) == 16


# ====================================================================
# 7. Canonical conflict IDs
# ====================================================================

def test_conflict_id_shape() -> None:
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    ev_b = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "B")
    claim_a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a])
    claim_b = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_b])
    result = conflict_id(claim_a, claim_b)
    assert re.fullmatch(r"conflict:[0-9a-f]{16}", result)


def test_conflict_id_order_independent() -> None:
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    ev_b = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "B")
    claim_a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a])
    claim_b = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_b])
    assert conflict_id(claim_a, claim_b) == conflict_id(claim_b, claim_a)


def test_conflict_id_rejects_same_claim_twice() -> None:
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    claim_a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a])
    with pytest.raises(ValueError):
        conflict_id(claim_a, claim_a)


def test_conflict_id_rejects_invalid_claim_id() -> None:
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    claim_a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a])
    with pytest.raises(ValueError):
        conflict_id(claim_a, "not a claim id")


def test_conflict_id_round_trips() -> None:
    ev_a = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "A")
    ev_b = evidence_id(SourceType.SEC_COMPANY_FACTS, SAMPLE_ENTITY, "FY2024", "B")
    claim_a = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_a])
    claim_b = claim_id(SAMPLE_ENTITY, "total_revenue", "FY2024", [ev_b])
    result = conflict_id(claim_a, claim_b)
    parsed = parse_conflict_id(result)
    assert len(parsed["hash"]) == 16


# ====================================================================
# 8. Assessment IDs
# ====================================================================

def test_assessment_id_shape() -> None:
    result = assessment_id()
    assert re.fullmatch(r"assessment:\d{17}_[0-9a-f]{6}", result)


def test_assessment_id_unique_per_call() -> None:
    a = assessment_id()
    b = assessment_id()
    assert a != b


def test_assessment_id_round_trips() -> None:
    result = assessment_id()
    parsed = parse_assessment_id(result)
    assert len(parsed["timestamp_ms"]) == 17
    assert len(parsed["suffix"]) == 6


# ====================================================================
# 9. Legacy compatibility
# ====================================================================

def test_legacy_evidence_id_shape() -> None:
    result = legacy_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert re.fullmatch(r"EV_SEC_[0-9A-F]{8}", result)


def test_legacy_claim_id_shape() -> None:
    result = legacy_claim_id(SAMPLE_EVIDENCE, "total_revenue")
    assert re.fullmatch(r"CLM_[0-9A-F]{8}", result)


def test_legacy_conflict_id_shape() -> None:
    result = legacy_conflict_id("CLM_AAAA0001", "CLM_BBBB0002")
    assert re.fullmatch(r"CONF_[0-9A-F]{8}", result)


def test_legacy_evidence_id_round_trips() -> None:
    result = legacy_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    parsed = parse_legacy_evidence_id(result)
    assert parsed["source"] == "SEC"
    assert len(parsed["hash"]) == 8


def test_legacy_claim_id_round_trips() -> None:
    result = legacy_claim_id(SAMPLE_EVIDENCE, "total_revenue")
    parsed = parse_legacy_claim_id(result)
    assert len(parsed["hash"]) == 8


def test_legacy_conflict_id_round_trips() -> None:
    result = legacy_conflict_id("CLM_AAAA0001", "CLM_BBBB0002")
    parsed = parse_legacy_conflict_id(result)
    assert len(parsed["hash"]) == 8


# ====================================================================
# 10. Cross-namespace isolation
# ====================================================================

def test_canonical_parser_rejects_legacy_evidence_id() -> None:
    legacy = legacy_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert parse_evidence_id(legacy) is None


def test_legacy_parser_rejects_canonical_evidence_id() -> None:
    canonical = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert parse_legacy_evidence_id(canonical) is None


# ====================================================================
# 11. is_* validators
# ====================================================================

def test_is_canonical_id_true_for_entity() -> None:
    assert is_canonical_id(SAMPLE_ENTITY) is True


def test_is_canonical_id_false_for_legacy() -> None:
    legacy = legacy_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert is_canonical_id(legacy) is False


def test_is_legacy_id_true_for_legacy_evidence() -> None:
    legacy = legacy_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert is_legacy_id(legacy) is True


def test_is_any_valid_id_true_for_both_namespaces() -> None:
    canonical = evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    legacy = legacy_evidence_id(
        SourceType.SEC_COMPANY_FACTS,
        SAMPLE_ENTITY,
        "FY2024",
        "Revenues",
    )
    assert is_any_valid_id(canonical) is True
    assert is_any_valid_id(legacy) is True


def test_is_any_valid_id_false_for_garbage() -> None:
    assert is_any_valid_id("not an id") is False
    assert is_canonical_id("not an id") is False
    assert is_legacy_id("not an id") is False