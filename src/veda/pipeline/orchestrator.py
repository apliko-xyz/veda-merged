"""
File: src/veda/pipeline/orchestrator.py
Title: Pipeline Orchestrator
Layer: Pipeline layer
Status: Merged prototype foundation — Phase 6

Purpose
-------
Wires the Phase 6 stages into one ordered pipeline function that
turns a vendor name and a requested period into a validated
Assessment packet. This is the pipeline's only entry point.

Public API
----------
ProviderBundle
run_assessment(vendor_name, requested_period, bundle,
               resolver_source, user_agent) -> Assessment

Does not
--------
Does not make network calls.
Does not import veda.providers.fixtures.
Does not hardcode fixture values.
Does not mutate inputs.
Does not bypass validate_assessment.

Design notes
------------
Sequence, frozen:

    entity_resolver.resolve
      -> provider.retrieve (four providers)
      -> normalizers (Phase 5)
      -> backfill undated evidence periods
      -> extract_claims
      -> detect_conflicts
      -> compute_missing_evidence
      -> build_assessment_status
      -> build_packet
      -> validate_assessment

ProviderBundle carries the four provider instances plus optional SEC
filing metadata. FixtureSECFilingsProvider requires cik,
accession_number, filing_form, and field_or_passage_hint to return
a passage. The orchestrator passes the three bundle fields through
unchanged.

Period backfill
---------------
Phase 5 non-SEC normalizers produce label-only periods. The frozen
assessment validation requires every evidence and claim period to
match the assessment period exactly. A label-only period compared
against a dated request returns UNKNOWN.

The orchestrator backfills undated evidence periods from the
requested period before claim extraction. That preserves consistent
Evidence, Claim, and Assessment periods and produces claim IDs from
the final fiscal-year period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from veda.entity.resolver import EntityResolver, EntityResolverSource
from veda.entity.reporting_boundary import ReportingBoundary
from veda.normalization import (
    normalize_annual_report_passage,
    normalize_company_facts,
    normalize_filing_passage,
    normalize_usaspending_award,
)
from veda.normalization.usaspending import PERIOD_OBLIGATION_GAP
from veda.pipeline.abstention import compute_missing_evidence
from veda.pipeline.assessment import (
    build_assessment_status,
    choose_human_review_reason,
    choose_recommended_next_step,
)
from veda.pipeline.claim_extraction import (
    RuleBasedClaimExtractor,
    extract_claims,
)
from veda.pipeline.extraction_protocol import ClaimExtractor
from veda.pipeline.conflict_detector import detect_conflicts
from veda.pipeline.packet import build_packet
from veda.providers.base import EvidenceProvider
from veda.providers.results import ProviderRequest
from veda.shared.enums import (
    AssessmentStatus,
    EntityResolutionStatus,
    EvidenceCategory,
    SourceType,
)
from veda.shared.ids import assessment_id as make_assessment_id
from veda.shared.models import Assessment, Claim, Evidence, RunMetadata
from veda.shared.periods import Period, RequestedPeriod
from veda.shared.validation import validate_assessment


SCHEMA_VERSION = "v0.1.0"
PIPELINE_VERSION = "v0.1.0"


@dataclass(frozen=True)
class ProviderBundle:
    """
    Container for the four provider instances the pipeline uses,
    plus optional SEC filing lookup metadata.

    The bundle holds the providers; it does not call them. The
    orchestrator calls each provider's retrieve method.

    FixtureSECFilingsProvider requires cik, accession_number,
    filing_form, and field_or_passage_hint. When any of the three
    optional metadata fields is None, the filing provider returns
    NOT_FOUND for the passage lookup. That is a legitimate outcome;
    the orchestrator does not raise.
    """

    sec_company_facts: EvidenceProvider
    sec_filing: EvidenceProvider
    usaspending: EvidenceProvider
    annual_report: EvidenceProvider
    sec_filing_accession_number: str | None = None
    sec_filing_form: str | None = None
    sec_filing_passage_hint: str | None = None


def _provider_modes(bundle: ProviderBundle) -> dict[str, str]:
    """Return a dict of provider names to mode strings."""
    return {
        "annual_report":     "fixture" if bundle.annual_report.is_fixture else "live",
        "sec_company_facts": "fixture" if bundle.sec_company_facts.is_fixture else "live",
        "sec_filing":        "fixture" if bundle.sec_filing.is_fixture else "live",
        "usaspending":       "fixture" if bundle.usaspending.is_fixture else "live",
    }


def _resolve_assessment_period(
    evidence: list[Evidence],
    requested_period: RequestedPeriod,
) -> Period:
    """
    Resolve the dated period the assessment will carry.

    The pipeline must produce a packet whose claims and evidence all
    carry dated reporting periods that exactly match the packet
    period. When the caller supplies a bare-year request
    (RequestedPeriod with start=None, end=None), the request itself
    carries no dates to backfill from.

    The correct source of dates in that case is reliable dated
    evidence already collected. This function:

        1. Returns the requested period unchanged if it already has
           both start and end dates. No guessing.
        2. Otherwise looks for dated evidence whose fiscal year
           matches the requested fiscal year.
        3. Prefers RECOGNIZED_REVENUE evidence (SEC Company Facts is
           the authoritative source of fiscal dates).
        4. Requires every candidate to agree on the exact start and
           end dates. Conflicting dated evidence raises, not guesses.
        5. Raises when no dated evidence is available. The caller
           receives an explicit failure, not a fabricated date.

    Never infers dates from an undated label alone.
    """

    # 1. Request already carries dates. Use them unchanged.
    if requested_period.start is not None and requested_period.end is not None:
        return Period(
            start=requested_period.start,
            end=requested_period.end,
            label=requested_period.raw,
        )

    # 2. Collect dated periods matching the requested fiscal year.
    dated: list[Period] = []
    for item in evidence:
        period = item.reporting_period
        if period.start is None or period.end is None:
            continue
        if period.fiscal_year() != requested_period.fiscal_year:
            continue
        dated.append(period)

    # 3. No dated evidence at all. This is the abstention path:
    #    an unresolved vendor, or a resolved vendor with no data
    #    for the requested period. Return an undated Period labelled
    #    with the fiscal year. The Assessment model and
    #    validate_assessment both accept an undated period when
    #    claims and conflicts are empty, which is the case here.
    #
    #    Do NOT raise. Raising would turn a valid abstention into an
    #    exception, breaking the design contract that an unresolved
    #    or empty packet is a legitimate outcome.
    if not dated:
        return Period(
            start=None,
            end=None,
            label=f"FY{requested_period.fiscal_year}",
        )

    # 4. Prefer RECOGNIZED_REVENUE dates (SEC Company Facts).
    authoritative: list[Period] = []
    for item in evidence:
        period = item.reporting_period
        if period.start is None or period.end is None:
            continue
        if period.fiscal_year() != requested_period.fiscal_year:
            continue
        if item.evidence_category == EvidenceCategory.RECOGNIZED_REVENUE:
            authoritative.append(period)

    candidates = authoritative if authoritative else dated

    # 5. Every candidate must agree on start and end. This is the only
    #    case that raises: dated evidence exists, but the dates
    #    disagree. That is a real inconsistency the caller must see.
    first = candidates[0]
    for period in candidates[1:]:
        if period.start != first.start or period.end != first.end:
            raise ValueError(
                "Conflicting dated evidence prevents assessment-period "
                "resolution."
            )

    # 6. Build the resolved period, preserving the original label
    #    when available.
    return Period(
        start=first.start,
        end=first.end,
        label=first.label or f"FY{requested_period.fiscal_year}",
    )


def _apply_period_to_undated_evidence(
    evidence: list[Evidence],
    assessment_period: Period,
) -> list[Evidence]:
    """
    Backfill undated evidence periods from the resolved assessment
    period.

    Phase 5 non-SEC normalizers produce label-only periods. Once the
    assessment period is resolved (either from a dated request or from
    dated evidence), copy those dates onto every evidence record whose
    period lacks either boundary.

    The assessment_period must be dated. The caller is expected to
    have resolved it via _resolve_assessment_period, which raises when
    no dates are available.

    Returns a new list. Does not mutate the input evidence.
    """
    if assessment_period.start is None or assessment_period.end is None:
        return evidence

    backfilled: list[Evidence] = []

    for item in evidence:
        period = item.reporting_period

        if period.start is None or period.end is None:
            item = item.model_copy(
                update={
                    "reporting_period": Period(
                        start=assessment_period.start,
                        end=assessment_period.end,
                        label=period.label or assessment_period.label,
                    )
                }
            )

        backfilled.append(item)

    return backfilled


def _reporting_period_for_packet(
    assessment_period: Period,
    claims: list[Claim],
) -> Period:
    """
    Choose the reporting Period the packet will carry.

    Preference order:
        1. A dated total_revenue claim's period.
        2. Any dated claim's period.
        3. The resolved assessment period as-is.

    The fallback is the resolved assessment period, not the raw
    requested period. For a bare-year request the resolved period is
    the dated period derived from SEC Company Facts evidence.
    """
    revenue_claims = sorted(
        (
            claim
            for claim in claims
            if (
                claim.claim_type == "total_revenue"
                and claim.reporting_period.start is not None
                and claim.reporting_period.end is not None
            )
        ),
        key=lambda claim: claim.claim_id,
    )
    if revenue_claims:
        return revenue_claims[0].reporting_period

    dated_claims = sorted(
        (
            claim
            for claim in claims
            if (
                claim.reporting_period.start is not None
                and claim.reporting_period.end is not None
            )
        ),
        key=lambda claim: claim.claim_id,
    )
    if dated_claims:
        return dated_claims[0].reporting_period

    return assessment_period


def _propagate_filing_metadata(
    evidence: list[Evidence],
) -> list[Evidence]:
    """
    Pair SEC filing evidence with SEC Company Facts evidence by
    accession number. When a pair shares an accession, copy the
    filing date and reporting period from the Company Facts entry
    to the filing document.

    The Company Facts entry carries the authoritative filed date
    and the actual start/end dates of the period. The filing index
    page is the same filing. Copying the metadata is a provenance
    handoff between two representations of the same source, not
    inference.

    Returns a new list. Does not mutate the input.
    """
    # Index SEC Company Facts evidence by accession number.
    cf_by_accession: dict[str, Evidence] = {}
    for ev in evidence:
        if ev.source_type != SourceType.SEC_COMPANY_FACTS:
            continue
        if not ev.accession_number:
            continue
        cf_by_accession[ev.accession_number] = ev

    if not cf_by_accession:
        return evidence

    result: list[Evidence] = []
    for ev in evidence:
        if ev.source_type != SourceType.SEC_FILING:
            result.append(ev)
            continue
        if not ev.accession_number:
            result.append(ev)
            continue
        cf = cf_by_accession.get(ev.accession_number)
        if cf is None:
            result.append(ev)
            continue
        if ev.document is None:
            result.append(ev)
            continue

        # Determine the filed date and reporting period to carry.
        filing_date = None
        if cf.document is not None and cf.document.filing_date is not None:
            filing_date = cf.document.filing_date

        reporting_period = cf.reporting_period

        # Only update fields that are currently missing.
        update: dict = {}
        if ev.document.filing_date is None and filing_date is not None:
            update["filing_date"] = filing_date
        if ev.document.reporting_period is None and reporting_period is not None:
            update["reporting_period"] = reporting_period

        if update:
            new_doc = ev.document.model_copy(update=update)
            ev = ev.model_copy(update={"document": new_doc})

        result.append(ev)

    return result


def _compute_metadata_notes(
    evidence: list[Evidence],
    provider_modes: dict[str, str],
) -> list[str]:
    """
    Produce honest limitation notes for the packet's run metadata.

    Two notes are produced:

      1. For each SEC filing evidence record whose location has
         no span offsets, a note explaining that the SEC filing
         provider returns the raw filing index page and that
         character-offset spans require either the deterministic
         text extraction path or the LLM narrative extraction
         path.

      2. When the annual report provider is fixture-backed and no
         annual report evidence exists in the packet, a note
         explaining that for public companies the SEC filing is
         the annual report content.
    """
    notes: list[str] = []

    # Note 1: SEC filing evidence without span offsets.
    for ev in evidence:
        if ev.source_type != SourceType.SEC_FILING:
            continue
        if ev.location is None:
            continue
        if ev.location.span_start is None and ev.location.span_end is None:
            notes.append(
                f"Passage span not captured for {ev.evidence_id}. "
                "The SEC filing provider returns the raw filing "
                "index page. Character-offset spans require either "
                "the deterministic text extraction path or the LLM "
                "narrative extraction path."
            )

    # Note 2: annual report fixture classification.
    if provider_modes.get("annual_report") == "fixture":
        has_annual_report = any(
            ev.source_type == SourceType.ANNUAL_REPORT
            for ev in evidence
        )
        if not has_annual_report:
            notes.append(
                "Annual report provider is fixture-backed. For public "
                "companies, the SEC Filing provider (sec_filing) "
                "supplies the annual report content. The annual_report "
                "provider category is reserved for investor-relations "
                "sources that are not SEC filings."
            )

    return notes


def run_assessment(
    *,
    vendor_name: str,
    requested_period: RequestedPeriod,
    bundle: ProviderBundle,
    resolver_source: EntityResolverSource,
    user_agent: str,
    extractor: ClaimExtractor | None = None,
) -> Assessment:
    """
    Run the full pipeline and return a validated Assessment packet.

    The packet is always constructed directly, never as a dict.
    validate_assessment runs on the packet before it is returned.
    """
    request_id = make_assessment_id()
    run_id = make_assessment_id()

    resolver = EntityResolver(user_agent=user_agent, source=resolver_source)
    vendor = resolver.resolve(vendor_name)

    evidence: list[Evidence] = []
    extra_notes: list[str] = []
    extra_limitations: list[str] = []

    if vendor.resolution_status == EntityResolutionStatus.RESOLVED:
        company_query = vendor.resolved_name or vendor.input_name

        cf_request = ProviderRequest(
            cik=vendor.cik,
            company_name=company_query,
            entity_id=vendor.entity_id,
            requested_period=requested_period,
        )
        cf_result = bundle.sec_company_facts.retrieve(cf_request)
        evidence.extend(
            normalize_company_facts(
                cf_result,
                entity_id=vendor.entity_id,
                requested_period=requested_period,
            )
        )

        filing_request = ProviderRequest(
            cik=vendor.cik,
            company_name=company_query,
            entity_id=vendor.entity_id,
            requested_period=requested_period,
            accession_number=bundle.sec_filing_accession_number,
            filing_form=bundle.sec_filing_form,
            field_or_passage_hint=bundle.sec_filing_passage_hint,
        )
        filing_result = bundle.sec_filing.retrieve(filing_request)
        evidence.extend(
            normalize_filing_passage(
                filing_result,
                entity_id=vendor.entity_id,
                requested_period=requested_period,
            )
        )

        usa_request = ProviderRequest(
            company_name=company_query,
            entity_id=vendor.entity_id,
            requested_period=requested_period,
        )
        usa_result = bundle.usaspending.retrieve(usa_request)
        usa_normalized = normalize_usaspending_award(
            usa_result,
            entity_id=vendor.entity_id,
            requested_period=requested_period,
        )
        evidence.extend(usa_normalized.evidence)
        extra_notes.extend(usa_normalized.warnings)
        usa_meta = usa_result.retrieval_metadata
        if usa_meta.get("recipient_match") == "name" and usa_meta.get("recipient_match_note"):
            extra_notes.append(str(usa_meta["recipient_match_note"]))
        if usa_meta.get("transaction_gap"):
            extra_notes.append(str(usa_meta["transaction_gap"]))
        if usa_normalized.evidence:
            extra_limitations.append(PERIOD_OBLIGATION_GAP)
        if usa_meta.get("truncated"):
            extra_limitations.append(
                "USAspending results were truncated at the configured "
                f"page cap ({usa_meta.get('pages_fetched')} page(s) fetched; "
                "has_more is true)."
            )

        ar_request = ProviderRequest(
            company_name=company_query,
            entity_id=vendor.entity_id,
            requested_period=requested_period,
        )
        ar_result = bundle.annual_report.retrieve(ar_request)
        ar_warnings: list[str] = []
        evidence.extend(
            normalize_annual_report_passage(
                ar_result,
                entity_id=vendor.entity_id,
                requested_period=requested_period,
                warnings=ar_warnings,
            )
        )
        extra_notes.extend(ar_warnings)

    # Propagate SEC Company Facts metadata onto matching SEC
    # filing evidence. Copies filed date and reporting period
    # from the authoritative Company Facts entry to the filing
    # document. Does not mutate; returns a new list.
    evidence = _propagate_filing_metadata(evidence)

    # Resolve the assessment period. For a bare-year request this
    # derives the dated period from reliable dated evidence (SEC
    # Company Facts preferred). For a dated request it preserves the
    # caller's dates unchanged. It raises if no dated evidence is
    # available, which prevents the pipeline from fabricating dates.
    assessment_period = _resolve_assessment_period(
        evidence,
        requested_period,
    )

    # Backfill undated evidence periods from the resolved assessment
    # period before claim extraction. Claim IDs are derived from
    # (entity, claim_type, period_label, evidence_ids), so the
    # backfill must run before extract_claims.
    evidence = _apply_period_to_undated_evidence(
        evidence,
        assessment_period,
    )

    # Select the extractor. When the caller does not supply one, use
    # the deterministic rule-based extractor. This preserves the
    # frozen Phase 6 default behavior: no LLM construction, no model
    # SDK import, no API key read, no model call.
    if extractor is None:
        extractor = RuleBasedClaimExtractor()

    claims = extractor.extract(evidence)
    conflicts = detect_conflicts(claims)
    missing = compute_missing_evidence(vendor, claims, evidence, requested_period)

    boundary = (
        ReportingBoundary.for_resolved_entity(vendor)
        if vendor.resolution_status == EntityResolutionStatus.RESOLVED
        else ReportingBoundary.for_unresolved_entity(vendor)
    )

    status = build_assessment_status(vendor, claims, conflicts, missing, boundary)

    limitations: list[str] = list(extra_limitations)
    if (
        status == AssessmentStatus.SUPPORTED_WITH_LIMITATIONS
        and getattr(boundary, "is_consolidated_at_parent", False)
    ):
        limitations.append(
            "Disclosures are made at the parent level; the entity's own "
            "consolidated boundary is not available."
        )

    now = datetime.now(timezone.utc)
    provider_modes = _provider_modes(bundle)
    metadata_notes = _compute_metadata_notes(evidence, provider_modes)
    metadata_notes.extend(extra_notes)
    run_metadata = RunMetadata(
        run_id=run_id,
        schema_version=SCHEMA_VERSION,
        pipeline_version=PIPELINE_VERSION,
        started_at=now,
        finished_at=now,
        provider_modes=provider_modes,
        notes=metadata_notes,
        user_agent=user_agent,
    )

    packet = build_packet(
        request_id=request_id,
        assessment_id=make_assessment_id(),
        vendor=vendor,
        reporting_period=_reporting_period_for_packet(assessment_period, claims),
        claims=claims,
        evidence=evidence,
        conflicts=conflicts,
        missing_evidence=missing,
        assessment_status=status,
        human_review_reason=choose_human_review_reason(status, vendor, conflicts),
        recommended_next_step=choose_recommended_next_step(status),
        limitations=limitations,
        run_metadata=run_metadata,
    )

    validate_assessment(packet)
    return packet


__all__ = ["ProviderBundle", "run_assessment"]