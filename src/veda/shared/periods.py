"""
File: src/veda/shared/periods.py
Title: Normalized Reporting Periods
Layer: Shared data contract
Status: Merged prototype foundation

Purpose
-------
Defines the canonical representation of a reporting period and the
rules that compare one period against another.

Every claim, every piece of evidence, and every assessment in the
merged VEDA system refers to a period through this module. No other
module constructs its own period representation, and no other module
compares dates directly to decide whether evidence matches a request.

The rules encoded here come from the personal prototype's
prototype/extractor.py, which discovered during live SEC testing that
SEC's own "fy" and "fp" labels cannot be trusted. The fiscal year of a
value must be derived from the actual end date of the reported period,
and a full-year span must be distinguished from a quarterly or
partial-year span by measuring the number of days between the actual
start and end dates.

The structural shape of a period with valid_from/valid_to dates comes
from the original VEDA prototype's TemporalValidity model in
app/models.py.

Responsibilities
----------------
- Define the RequestedPeriod object: what the caller asked for.
- Define the Period object: what a specific piece of evidence covers.
- Encode the full-year span rule via MIN_ANNUAL_PERIOD_DAYS and
  MAX_ANNUAL_PERIOD_DAYS.
- Provide a Period.days() method that computes the actual span.
- Provide a Period.is_full_year() method that applies the span rule.
- Provide a Period.match_status_against() method that returns a
  PeriodMatchStatus describing how two periods relate.
- Provide construction helpers for building a Period from raw SEC
  XBRL start/end strings.

This file does not:
-------------------
- Make network requests.
- Parse source documents.
- Extract claims.
- Normalize evidence records.
- Decide whether an assessment should abstain.

Design notes
------------
- A RequestedPeriod is what the user typed. It may be incomplete (only
  a fiscal year) or precise (a start and end date). A Period is what a
  specific record actually covers. The two are different concepts and
  are represented by different classes.
- A Period may have unknown dates. When it does, days() and
  is_full_year() return None, and match_status_against() returns
  PeriodMatchStatus.UNKNOWN. The system never guesses a period.
- The 350-380 day window for a full-year span accounts for leap years
  and for the 52/53-week fiscal calendars used by some filers. A
  53-week year is 371 days; a 52-week year is 364 days. Both are
  inside the window. A quarter is 90-92 days and is outside.

Sources
-------
- vendor-evidence-prototype/prototype/extractor.py
  (_period_days, MIN_ANNUAL_PERIOD_DAYS, MAX_ANNUAL_PERIOD_DAYS)
- veda_demo/app/models.py (TemporalValidity)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from veda.shared.enums import PeriodMatchStatus


# --------------------------------------------------------------------
# Full-year span constants
# --------------------------------------------------------------------
# A fiscal-year span measured in days. The window is deliberately
# generous: it accommodates leap years and the 52/53-week calendars
# used by some filers, while still excluding quarterly or partial-year
# entries. The personal prototype used exactly these bounds and they
# are preserved here as the single source of truth for the rule.
MIN_ANNUAL_PERIOD_DAYS: int = 350
MAX_ANNUAL_PERIOD_DAYS: int = 380


# --------------------------------------------------------------------
# RequestedPeriod
# --------------------------------------------------------------------

class RequestedPeriod(BaseModel):
    """
    What the caller asked for.

    A RequestedPeriod carries the raw input the user or client supplied.
    It may be as coarse as a fiscal year (2024) or as precise as a
    specific start and end date. It is never used to compare against
    evidence directly; evidence comparison always happens through a
    Period object built from actual evidence dates.

    Fields
    ------
    fiscal_year : int
        The calendar year the request is about. Required.
    start : Optional[date]
        Requested start date, if the caller supplied one. None otherwise.
    end : Optional[date]
        Requested end date, if the caller supplied one. None otherwise.
    raw : str
        The original input string as the caller typed it, preserved for
        error messages and for reproducing the request exactly.
    """

    fiscal_year: int = Field(
        ...,
        description="The fiscal year the request is about.",
    )
    start: Optional[date] = Field(
        default=None,
        description="Requested start date, if any.",
    )
    end: Optional[date] = Field(
        default=None,
        description="Requested end date, if any.",
    )
    raw: str = Field(
        ...,
        min_length=1,
        description="The original input string as supplied by the caller.",
    )

    @model_validator(mode="after")
    def _validate_date_order(self) -> "RequestedPeriod":
        """If both dates are supplied, start must not be after end."""
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("RequestedPeriod.start must not be after end")
        return self

    @classmethod
    def from_fiscal_year(cls, fiscal_year: int, raw: Optional[str] = None) -> "RequestedPeriod":
        """
        Build a RequestedPeriod from a bare fiscal year.

        Used when the caller supplied only a year (the common case).
        The raw field defaults to the string form of the year if the
        caller did not supply one.
        """
        return cls(
            fiscal_year=fiscal_year,
            start=None,
            end=None,
            raw=raw if raw is not None else str(fiscal_year),
        )


# --------------------------------------------------------------------
# Period
# --------------------------------------------------------------------

class Period(BaseModel):
    """
    What a specific piece of evidence actually covers.

    A Period is built from the actual start and end dates recorded in
    a source. It never trusts a label. The fiscal year is derived from
    the end date. The span is derived from the difference between the
    two dates.

    A Period is immutable once built. Comparing it to another Period
    happens through match_status_against(), which returns a
    PeriodMatchStatus. Nothing in the system compares Period fields
    directly.

    Fields
    ------
    start : Optional[date]
        The reported start date of the period. None if unknown.
    end : Optional[date]
        The reported end date of the period. None if unknown.
    label : Optional[str]
        Optional human-readable label such as "FY2024". Never used for
        matching; a display aid only.
    """

    start: Optional[date] = Field(
        default=None,
        description="The reported start date of the period.",
    )
    end: Optional[date] = Field(
        default=None,
        description="The reported end date of the period.",
    )
    label: Optional[str] = Field(
        default=None,
        description="Human-readable label such as FY2024. Not used for matching.",
    )

    @model_validator(mode="after")
    def _validate_date_order(self) -> "Period":
        """If both dates are present, start must not be after end."""
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("Period.start must not be after end")
        return self

    # ---- derived properties ----------------------------------------

    def days(self) -> Optional[int]:
        """
        The actual span of this period in days.

        Returns None if either start or end is missing, so a period
        with incomplete dates never produces a misleading span.
        Callers use this to decide whether a period is a full year.
        """
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days

    def is_full_year(self) -> bool:
        """
        Whether this period covers a full fiscal year.

        A period is a full year if its span falls inside the
        MIN_ANNUAL_PERIOD_DAYS to MAX_ANNUAL_PERIOD_DAYS window. This
        is the rule the personal prototype discovered was necessary
        after SEC returned a three-month span labeled fp="FY".

        Returns False if the span is unknown, so a period without
        dates is never treated as a full year.
        """
        span = self.days()
        if span is None:
            return False
        return MIN_ANNUAL_PERIOD_DAYS <= span <= MAX_ANNUAL_PERIOD_DAYS

    def fiscal_year(self) -> Optional[int]:
        """
        The fiscal year this period covers, derived from its end date.

        Returns None if the end date is missing. This is the correct
        replacement for SEC's "fy" label, which the personal prototype
        discovered reflects the filing year and not the value year.
        """
        if self.end is None:
            return None
        return self.end.year

    # ---- comparison ------------------------------------------------

    def match_status_against(self, requested: RequestedPeriod) -> PeriodMatchStatus:
        """
        How this period relates to a requested period.

        The comparison proceeds in two stages:

        1. If the requested period supplies start and/or end dates,
           and this period supplies the corresponding dates, compare
           the dates directly. If they match on every supplied field,
           return EXACT. If they differ on any supplied field, return
           MISMATCH. This is what makes a 90-day quarter ending
           2024-03-31 differ from a full year ending 2024-12-31 for a
           request that supplied explicit dates.

        2. Fall through to fiscal-year comparison. If this period has
           no end date, return UNKNOWN. If the fiscal years match,
           return EXACT. If they differ by exactly one year, return
           ADJACENT. Otherwise return MISMATCH.

        This preserves the original fiscal-year-only behavior for
        callers who supplied only a fiscal year, while adding
        exact-date comparison for callers who supplied dates.

        The SEC prototype's rule - that a record labeled FY2024 but
        covering only a quarter cannot satisfy a full-year request -
        is enforced at stage 1 whenever the request supplies a date
        range. When the request supplies only a year, stage 2 is the
        best available comparison and the pipeline relies on the
        extraction layer to have already excluded quarterly records
        via the MIN_ANNUAL_PERIOD_DAYS window.
        """
        # Stage 1: exact date comparison when the request supplies dates.
        compared_dates = False

        if requested.start is not None and self.start is not None:
            compared_dates = True
            if self.start != requested.start:
                return PeriodMatchStatus.MISMATCH

        if requested.end is not None and self.end is not None:
            compared_dates = True
            if self.end != requested.end:
                return PeriodMatchStatus.MISMATCH

        # If dates were compared and every supplied field matched, EXACT.
        # If the request supplied a date that this period cannot answer
        # (because self does not have that date), fall through to
        # stage 2 rather than asserting a false match.
        if compared_dates:
            # A date was compared and matched. If the request supplied a
            # start and self has no start, or vice versa, stage 2 will
            # decide based on fiscal year.
            if (
                (requested.start is None or self.start is not None) and
                (requested.end is None or self.end is not None)
            ):
                return PeriodMatchStatus.EXACT

        # Stage 2: fiscal-year comparison.
        own_fy = self.fiscal_year()
        if own_fy is None:
            return PeriodMatchStatus.UNKNOWN
        if own_fy == requested.fiscal_year:
            return PeriodMatchStatus.EXACT
        if abs(own_fy - requested.fiscal_year) == 1:
            return PeriodMatchStatus.ADJACENT
        return PeriodMatchStatus.MISMATCH

    # ---- construction helpers --------------------------------------

    @classmethod
    def federal_fiscal_year(cls, fiscal_year: int) -> "Period":
        """
        Build the US federal fiscal year window for a fiscal year.

        Federal fiscal year N runs from 1 October of year N-1 through
        30 September of year N. The label is ``FFY{N}`` so it cannot be
        mistaken for a company fiscal year labeled ``FY{N}``.
        """
        if not isinstance(fiscal_year, int):
            raise ValueError("federal fiscal year must be an integer")
        return cls(
            start=date(fiscal_year - 1, 10, 1),
            end=date(fiscal_year, 9, 30),
            label=f"FFY{fiscal_year}",
        )

    @classmethod
    def from_iso_strings(
        cls,
        start: Optional[str],
        end: Optional[str],
        label: Optional[str] = None,
    ) -> "Period":
        """
        Build a Period from ISO-8601 date strings.

        Used by the SEC normalizer to convert the raw start and end
        strings from SEC Company Facts into a Period object. A
        malformed string is not an error; the corresponding field is
        left as None and the resulting Period carries no date. This
        mirrors the personal prototype's _period_days behaviour, which
        returned None rather than raising when an entry was malformed.
        """
        start_date: Optional[date] = None
        end_date: Optional[date] = None

        if start is not None:
            try:
                start_date = date.fromisoformat(start)
            except (ValueError, TypeError):
                start_date = None

        if end is not None:
            try:
                end_date = date.fromisoformat(end)
            except (ValueError, TypeError):
                end_date = None

        return cls(start=start_date, end=end_date, label=label)


# --------------------------------------------------------------------
# Module exports
# --------------------------------------------------------------------

__all__ = [
    "MIN_ANNUAL_PERIOD_DAYS",
    "MAX_ANNUAL_PERIOD_DAYS",
    "RequestedPeriod",
    "Period",
]