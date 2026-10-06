"""
File: src/veda/shared/ids.py
Title: Canonical Identifier Generation, Parsing, and Legacy Compatibility
Layer: Shared data contract
Status: Merged prototype foundation — patched v2 (digit-permitting segments)

Purpose
-------
Defines the single set of identifier formats used by the merged VEDA
system, the functions that generate them, and the parsers that recover
their constituent parts.

Every object in the system receives an ID through a function defined
here. No other module constructs an ID by hand.

Two identity layers exist, deliberately:

  1. Canonical merged IDs use a colon-separated form whose first
     segment names the object class:

         entity:sec_edgar:vendor:0000936468
         doc:sec_edgar:10k:000093646825000009
         chunk:doc:sec_edgar:10k:000093646825000009:0001
         evidence:sec_edgar:ab12cd34ef567890
         claim:9a8b7c6d5e4f3a21
         relationship:sec_edgar:1122334455667788
         conflict:aabbccddeeff0011
         assessment:0017370000000000_abcdef

  2. Legacy alias IDs use the underscore-prefixed forms produced by
     the two source prototypes:

         EV_SEC_AB12CD34
         EV_FILING_5411457C
         CLM_010
         CONF_001
         ASMT_...

     The legacy forms remain parseable so packets written by either
     prototype still validate during migration. They are not the
     primary identity for new objects.

Segment character rules
-----------------------
The source, entity_type, and doc_type segments accept lowercase
letters, digits, and underscores: [a-z0-9_]+.

Digits are required in practice. Common SEC document types include
10k, 10q, 8k, 20f, 40f, s1, s4, and def14a. Rejecting digits would
make it impossible to build IDs for the most common filing types.
The earlier pattern [a-z_]+ was a defect; this revision corrects it.

The native_id segment accepts alphanumeric characters plus dot,
underscore, and hyphen: [A-Za-z0-9._\-]+. This accommodates SEC CIKs,
USAspending UEIs, SEC accession numbers with hyphens, and similar
identifiers without change.

Responsibilities
----------------
- Define the prefix and slug for each source type.
- Generate canonical entity IDs.
- Generate canonical document IDs.
- Generate canonical chunk IDs.
- Generate canonical evidence IDs (content-addressed, SEC-aware).
- Generate canonical claim IDs (full-identity hash).
- Generate canonical relationship IDs.
- Generate canonical conflict IDs (order-independent).
- Generate unique assessment IDs.
- Generate legacy alias IDs for backward compatibility.
- Parse any canonical or legacy ID back into its constituent parts.
- Reject IDs that do not match any known format.

This file does not:
-------------------
- Make network requests.
- Retrieve or normalize evidence.
- Decide whether two pieces of evidence conflict.
- Build assessment packets.
- Build the provenance graph.

Design notes
------------
- Every canonical ID is human-readable at a glance. The first two
  segments name the object class and the source that minted it.

- Evidence IDs are content-addressed over a stable, JSON-serialized
  input list. The list is serialized as a single JSON value with
  nested structures preserved, so no delimiter character can cause
  two different input structures to produce the same byte stream.

- Claim IDs hash the full claim identity: canonical entity ID, claim
  type, reporting period, and the sorted list of canonical evidence
  IDs passed as a nested JSON list, not a joined string.

- Relationship IDs are deterministic from source type, subject ID,
  predicate, and object ID. The generator does not impose semantic
  graph rules; a self-referential relationship is allowed because
  some graph models use them.

- Conflict IDs sort the two claim IDs before hashing and require both
  inputs to be valid claim IDs (canonical or legacy).

- Assessment IDs are the only non-deterministic IDs. The ID embeds a
  millisecond-precision UTC timestamp zero-padded to 17 digits and a
  six-character random suffix.

- IDs never embed a status, a claim value, or any outcome.

- Unsupported source types raise ValueError. No silent fallback
  prefix is used.

Round-trip definition
---------------------
A canonical ID "round-trips" through its parser when
parse_<class>_id(generator(...)) returns a non-None dict with the
expected keys. It does not reconstruct the original inputs, because
for content-addressed IDs the hash is one-way.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from veda.shared.enums import SourceType


# --------------------------------------------------------------------
# Canonical source slugs and legacy prefixes
# --------------------------------------------------------------------
# Both SEC Company Facts and SEC Filing map to the SAME canonical slug,
# "sec_edgar". The source type remains distinct inside the evidence
# hash, so Company Facts evidence and Filing evidence never collide,
# but their IDs share a namespace prefix.

_CANONICAL_SOURCE_SLUG_BY_SOURCE: dict[SourceType, str] = {
    SourceType.SEC_COMPANY_FACTS: "sec_edgar",
    SourceType.SEC_FILING: "sec_edgar",
    SourceType.USASPENDING: "usaspending",
    SourceType.ANNUAL_REPORT: "annual_report",
    SourceType.GAO: "gao",
    SourceType.DODIG: "dodig",
    SourceType.SYNTHETIC: "synthetic",
}

_LEGACY_EVIDENCE_PREFIX_BY_SOURCE: dict[SourceType, str] = {
    SourceType.SEC_COMPANY_FACTS: "EV_SEC",
    SourceType.SEC_FILING: "EV_FILING",
    SourceType.USASPENDING: "EV_USA",
    SourceType.ANNUAL_REPORT: "EV_AR",
    SourceType.GAO: "EV_GAO",
    SourceType.DODIG: "EV_DODIG",
    SourceType.SYNTHETIC: "EV_SYN",
}


# --------------------------------------------------------------------
# Regex patterns
# --------------------------------------------------------------------
# Segment character rules:
#   source, entity_type, doc_type : [a-z0-9_]+
#   native_id                     : [A-Za-z0-9._\-]+
# Digits are permitted in the type segments because SEC document types
# such as 10k, 10q, 8k, 20f, s1, and def14a contain digits.

_CANONICAL_ENTITY_ID_RE = re.compile(
    r"^entity:(?P<source>[a-z0-9_]+):(?P<entity_type>[a-z0-9_]+):(?P<native_id>[A-Za-z0-9._\-]+)$"
)
_CANONICAL_DOC_ID_RE = re.compile(
    r"^doc:(?P<source>[a-z0-9_]+):(?P<doc_type>[a-z0-9_]+):(?P<native_id>[A-Za-z0-9._\-]+)$"
)
_CANONICAL_CHUNK_ID_RE = re.compile(
    r"^chunk:(?P<doc_id>doc:[a-z0-9_]+:[a-z0-9_]+:[A-Za-z0-9._\-]+):(?P<index>\d{4})$"
)
_CANONICAL_EVIDENCE_ID_RE = re.compile(
    r"^evidence:(?P<source>[a-z0-9_]+):(?P<hash>[0-9a-f]{16})$"
)
_CANONICAL_CLAIM_ID_RE = re.compile(
    r"^claim:(?P<hash>[0-9a-f]{16})$"
)
_CANONICAL_RELATIONSHIP_ID_RE = re.compile(
    r"^relationship:(?P<source>[a-z0-9_]+):(?P<hash>[0-9a-f]{16})$"
)
_CANONICAL_CONFLICT_ID_RE = re.compile(
    r"^conflict:(?P<hash>[0-9a-f]{16})$"
)
_CANONICAL_ASSESSMENT_ID_RE = re.compile(
    r"^assessment:(?P<ts>\d{17})_(?P<suffix>[0-9a-f]{6})$"
)

_LEGACY_EVIDENCE_ID_RE = re.compile(
    r"^EV_(?P<source>[A-Z]+)_(?P<hash>[0-9A-F]{8})$"
)
_LEGACY_CLAIM_ID_RE = re.compile(
    r"^CLM_(?P<hash>[0-9A-F]{8})$"
)
_LEGACY_CONFLICT_ID_RE = re.compile(
    r"^CONF_(?P<hash>[0-9A-F]{8})$"
)
_LEGACY_ASSESSMENT_ID_RE = re.compile(
    r"^ASMT_(?P<ts>\d{17})_(?P<suffix>[0-9a-f]{6})$"
)

_PREDICATE_RE = re.compile(r"^[a-z0-9_]+$")


# --------------------------------------------------------------------
# Internal hashing
# --------------------------------------------------------------------

def _stable_hash(parts: list[Any], length: int = 16) -> str:
    """
    Deterministic lowercase hex hash over a list of structured values.

    `parts` may contain nested lists. The list is serialized as a
    single JSON value with ensure_ascii=False and compact separators.
    JSON serialization preserves structural boundaries between nested
    lists and between string elements, so two structurally distinct
    inputs cannot produce the same byte stream even if a string element
    contains a comma, a pipe, or any other delimiter character.

    This is what makes claim_id() immune to delimiter ambiguity. The
    fix is structural, not escaping-based, and correct for all inputs.

    The hash is one-way and not a security boundary.
    """
    serialized = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha1(serialized.encode("utf-8")).hexdigest()
    return digest[:length]


# --------------------------------------------------------------------
# Canonical entity IDs
# --------------------------------------------------------------------

def entity_id(source: str, entity_type: str, native_id: str) -> str:
    """Build a canonical entity ID: entity:<source>:<type>:<native_id>."""
    if not re.fullmatch(r"[a-z0-9_]+", source):
        raise ValueError(f"entity_id source must be lowercase letters/digits/underscores: {source!r}")
    if not re.fullmatch(r"[a-z0-9_]+", entity_type):
        raise ValueError(f"entity_id entity_type must be lowercase letters/digits/underscores: {entity_type!r}")
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", native_id):
        raise ValueError(f"entity_id native_id must be alphanumeric with . _ - allowed: {native_id!r}")
    return f"entity:{source}:{entity_type}:{native_id}"


def parse_entity_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical entity ID into source, entity_type, native_id."""
    m = _CANONICAL_ENTITY_ID_RE.match(id_str)
    if m is None:
        return None
    return {
        "source": m.group("source"),
        "entity_type": m.group("entity_type"),
        "native_id": m.group("native_id"),
    }


# --------------------------------------------------------------------
# Canonical document IDs
# --------------------------------------------------------------------

def document_id(source: str, doc_type: str, native_id: str) -> str:
    """Build a canonical document ID: doc:<source>:<doc_type>:<native_id>."""
    if not re.fullmatch(r"[a-z0-9_]+", source):
        raise ValueError(f"document_id source must be lowercase letters/digits/underscores: {source!r}")
    if not re.fullmatch(r"[a-z0-9_]+", doc_type):
        raise ValueError(f"document_id doc_type must be lowercase letters/digits/underscores: {doc_type!r}")
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", native_id):
        raise ValueError(f"document_id native_id must be alphanumeric with . _ - allowed: {native_id!r}")
    return f"doc:{source}:{doc_type}:{native_id}"


def parse_document_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical document ID into source, doc_type, native_id."""
    m = _CANONICAL_DOC_ID_RE.match(id_str)
    if m is None:
        return None
    return {
        "source": m.group("source"),
        "doc_type": m.group("doc_type"),
        "native_id": m.group("native_id"),
    }


# --------------------------------------------------------------------
# Canonical chunk IDs
# --------------------------------------------------------------------

def chunk_id(doc_id: str, index: int) -> str:
    """Build a canonical chunk ID: chunk:<doc_id>:<index>."""
    if parse_document_id(doc_id) is None:
        raise ValueError(f"chunk_id parent is not a canonical document_id: {doc_id!r}")
    # Four digits covers a long 10-K at the default chunk size
    # (about 1,500 characters) without colliding with the document id.
    if not (0 <= index <= 9999):
        raise ValueError(f"chunk_id index must be 0-9999, got {index}")
    return f"chunk:{doc_id}:{index:04d}"


def parse_chunk_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical chunk ID into doc_id and index."""
    m = _CANONICAL_CHUNK_ID_RE.match(id_str)
    if m is None:
        return None
    return {"doc_id": m.group("doc_id"), "index": m.group("index")}


# --------------------------------------------------------------------
# Canonical evidence IDs
# --------------------------------------------------------------------

def evidence_id(
    source_type: SourceType,
    entity_id_str: str,
    reporting_period: str,
    field_or_passage: str,
    document_context: Optional[str] = None,
) -> str:
    """Build a canonical, content-addressed evidence ID."""
    if _CANONICAL_SOURCE_SLUG_BY_SOURCE.get(source_type) is None:
        raise ValueError(f"evidence_id: unsupported source type {source_type!r}")

    if parse_entity_id(entity_id_str) is None:
        raise ValueError(
            f"evidence_id: entity_id_str must be a canonical entity ID, got {entity_id_str!r}"
        )

    if not reporting_period:
        raise ValueError("evidence_id: reporting_period must be non-empty")

    if not field_or_passage:
        raise ValueError("evidence_id: field_or_passage must be non-empty")

    if source_type is SourceType.SEC_FILING and document_context is None:
        raise ValueError(
            "evidence_id: SEC_FILING evidence requires document_context "
            "(a canonical document ID) to prevent cross-filing collisions"
        )

    if document_context is not None and parse_document_id(document_context) is None:
        raise ValueError(
            f"evidence_id: document_context must be a canonical document ID, got {document_context!r}"
        )

    hash_input: list[Any] = [
        source_type.value,
        entity_id_str,
        reporting_period,
        field_or_passage,
    ]
    if document_context is not None:
        hash_input.append(document_context)

    slug = _CANONICAL_SOURCE_SLUG_BY_SOURCE[source_type]
    h = _stable_hash(hash_input, length=16)
    return f"evidence:{slug}:{h}"


def parse_evidence_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical evidence ID into source slug and hash."""
    m = _CANONICAL_EVIDENCE_ID_RE.match(id_str)
    if m is None:
        return None
    return {"source": m.group("source"), "hash": m.group("hash")}


# --------------------------------------------------------------------
# Canonical claim IDs
# --------------------------------------------------------------------

def claim_id(
    entity_id_str: str,
    claim_type: str,
    reporting_period: str,
    evidence_ids: list[str],
    allow_duplicate_evidence: bool = False,
) -> str:
    """Build a canonical claim ID from the full claim identity."""
    if parse_entity_id(entity_id_str) is None:
        raise ValueError(
            f"claim_id: entity_id_str must be a canonical entity ID, got {entity_id_str!r}"
        )
    if not claim_type:
        raise ValueError("claim_id: claim_type must be non-empty")
    if not reporting_period:
        raise ValueError("claim_id: reporting_period must be non-empty")

    for ev in evidence_ids:
        if parse_evidence_id(ev) is None and parse_legacy_evidence_id(ev) is None:
            raise ValueError(f"claim_id: {ev!r} is not a valid evidence ID")

    if not allow_duplicate_evidence and len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("claim_id: duplicate evidence IDs are not allowed")

    hash_input: list[Any] = [
        entity_id_str,
        claim_type,
        reporting_period,
        sorted(evidence_ids),
    ]
    h = _stable_hash(hash_input, length=16)
    return f"claim:{h}"


def parse_claim_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical claim ID into its hash."""
    m = _CANONICAL_CLAIM_ID_RE.match(id_str)
    if m is None:
        return None
    return {"hash": m.group("hash")}


# --------------------------------------------------------------------
# Canonical relationship IDs
# --------------------------------------------------------------------

def relationship_id(
    source_type: SourceType,
    subject_id: str,
    predicate: str,
    object_id: str,
) -> str:
    """Build a canonical relationship ID."""
    if _CANONICAL_SOURCE_SLUG_BY_SOURCE.get(source_type) is None:
        raise ValueError(f"relationship_id: unsupported source type {source_type!r}")
    if not subject_id:
        raise ValueError("relationship_id: subject_id must be non-empty")
    if not predicate:
        raise ValueError("relationship_id: predicate must be non-empty")
    if not _PREDICATE_RE.match(predicate):
        raise ValueError(f"relationship_id: predicate must match [a-z0-9_]+, got {predicate!r}")
    if not object_id:
        raise ValueError("relationship_id: object_id must be non-empty")

    hash_input: list[Any] = [source_type.value, subject_id, predicate, object_id]
    slug = _CANONICAL_SOURCE_SLUG_BY_SOURCE[source_type]
    h = _stable_hash(hash_input, length=16)
    return f"relationship:{slug}:{h}"


def parse_relationship_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical relationship ID into source slug and hash."""
    m = _CANONICAL_RELATIONSHIP_ID_RE.match(id_str)
    if m is None:
        return None
    return {"source": m.group("source"), "hash": m.group("hash")}


# --------------------------------------------------------------------
# Canonical conflict IDs
# --------------------------------------------------------------------

def conflict_id(claim_id_a: str, claim_id_b: str) -> str:
    """Build a canonical conflict ID from two conflicting claims."""
    for name, cid in (("claim_id_a", claim_id_a), ("claim_id_b", claim_id_b)):
        if parse_claim_id(cid) is None and parse_legacy_claim_id(cid) is None:
            raise ValueError(f"conflict_id: {name}={cid!r} is not a valid claim ID")
    if claim_id_a == claim_id_b:
        raise ValueError("conflict_id: claim IDs must differ")

    a, b = sorted([claim_id_a, claim_id_b])
    h = _stable_hash([a, b], length=16)
    return f"conflict:{h}"


def parse_conflict_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical conflict ID into its hash."""
    m = _CANONICAL_CONFLICT_ID_RE.match(id_str)
    if m is None:
        return None
    return {"hash": m.group("hash")}


# --------------------------------------------------------------------
# Canonical assessment IDs
# --------------------------------------------------------------------

def assessment_id() -> str:
    """Build a unique canonical assessment ID."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    suffix = uuid.uuid4().hex[:6]
    return f"assessment:{now_ms:017d}_{suffix}"


def parse_assessment_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a canonical assessment ID into timestamp and suffix."""
    m = _CANONICAL_ASSESSMENT_ID_RE.match(id_str)
    if m is None:
        return None
    return {"timestamp_ms": m.group("ts"), "suffix": m.group("suffix")}


# --------------------------------------------------------------------
# Legacy alias IDs
# --------------------------------------------------------------------

def legacy_evidence_id(
    source_type: SourceType,
    entity_id_str: str,
    reporting_period: str,
    field_or_passage: str,
) -> str:
    """Build a legacy alias evidence ID (EV_<PREFIX>_<8-hex>)."""
    prefix = _LEGACY_EVIDENCE_PREFIX_BY_SOURCE.get(source_type)
    if prefix is None:
        raise ValueError(f"legacy_evidence_id: unsupported source type {source_type!r}")
    h = _stable_hash(
        [source_type.value, entity_id_str, reporting_period, field_or_passage],
        length=8,
    ).upper()
    return f"{prefix}_{h}"


def legacy_claim_id(evidence_id_str: str, claim_type: str) -> str:
    """Build a legacy alias claim ID (CLM_<8-hex>)."""
    h = _stable_hash([evidence_id_str, claim_type], length=8).upper()
    return f"CLM_{h}"


def legacy_conflict_id(claim_id_a: str, claim_id_b: str) -> str:
    """Build a legacy alias conflict ID (CONF_<8-hex>)."""
    a, b = sorted([claim_id_a, claim_id_b])
    h = _stable_hash([a, b], length=8).upper()
    return f"CONF_{h}"


def parse_legacy_evidence_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a legacy evidence ID (EV_*) into source prefix and hash."""
    m = _LEGACY_EVIDENCE_ID_RE.match(id_str)
    if m is None:
        return None
    return {"source": m.group("source"), "hash": m.group("hash")}


def parse_legacy_claim_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a legacy claim ID (CLM_*) into its hash."""
    m = _LEGACY_CLAIM_ID_RE.match(id_str)
    if m is None:
        return None
    return {"hash": m.group("hash")}


def parse_legacy_conflict_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a legacy conflict ID (CONF_*) into its hash."""
    m = _LEGACY_CONFLICT_ID_RE.match(id_str)
    if m is None:
        return None
    return {"hash": m.group("hash")}


def parse_legacy_assessment_id(id_str: str) -> Optional[dict[str, str]]:
    """Parse a legacy assessment ID (ASMT_*) into timestamp and suffix."""
    m = _LEGACY_ASSESSMENT_ID_RE.match(id_str)
    if m is None:
        return None
    return {"timestamp_ms": m.group("ts"), "suffix": m.group("suffix")}


# --------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------

def is_canonical_id(id_str: str) -> bool:
    """Return True if the string matches any canonical ID format."""
    return any(
        pattern.match(id_str) is not None
        for pattern in (
            _CANONICAL_ENTITY_ID_RE,
            _CANONICAL_DOC_ID_RE,
            _CANONICAL_CHUNK_ID_RE,
            _CANONICAL_EVIDENCE_ID_RE,
            _CANONICAL_CLAIM_ID_RE,
            _CANONICAL_RELATIONSHIP_ID_RE,
            _CANONICAL_CONFLICT_ID_RE,
            _CANONICAL_ASSESSMENT_ID_RE,
        )
    )


def is_legacy_id(id_str: str) -> bool:
    """Return True if the string matches any legacy alias ID format."""
    return any(
        pattern.match(id_str) is not None
        for pattern in (
            _LEGACY_EVIDENCE_ID_RE,
            _LEGACY_CLAIM_ID_RE,
            _LEGACY_CONFLICT_ID_RE,
            _LEGACY_ASSESSMENT_ID_RE,
        )
    )


def is_any_valid_id(id_str: str) -> bool:
    """Return True if the string matches any canonical or legacy format."""
    return is_canonical_id(id_str) or is_legacy_id(id_str)


# Backward-compatible alias. is_valid_id behaves like is_canonical_id.
is_valid_id = is_canonical_id


# --------------------------------------------------------------------
# Module exports
# --------------------------------------------------------------------

__all__ = [
    "entity_id",
    "document_id",
    "chunk_id",
    "evidence_id",
    "claim_id",
    "relationship_id",
    "conflict_id",
    "assessment_id",
    "parse_entity_id",
    "parse_document_id",
    "parse_chunk_id",
    "parse_evidence_id",
    "parse_claim_id",
    "parse_relationship_id",
    "parse_conflict_id",
    "parse_assessment_id",
    "legacy_evidence_id",
    "legacy_claim_id",
    "legacy_conflict_id",
    "parse_legacy_evidence_id",
    "parse_legacy_claim_id",
    "parse_legacy_conflict_id",
    "parse_legacy_assessment_id",
    "is_canonical_id",
    "is_legacy_id",
    "is_any_valid_id",
    "is_valid_id",
]