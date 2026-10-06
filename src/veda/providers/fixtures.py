"""
File: src/veda/providers/fixtures.py
Title: Provider Fixture Datasets
Layer: Provider retrieval layer
Status: Merged prototype foundation — Phase 4

Purpose
-------
Stores deterministic offline datasets consumed by fixture-backed
providers.

Public data
-----------
SEC_COMPANY_FACTS_BY_CIK
SEC_FILING_PASSAGES
USASPENDING_AWARDS_BY_NAME
ANNUAL_REPORT_PASSAGES_BY_NAME

Network policy
--------------
This module defines data only. It performs no network I/O and defines
no provider classes.
"""

from __future__ import annotations


SEC_COMPANY_FACTS_BY_CIK: dict[str, dict] = {
    "0000936468": {
        "cik": "0000936468",
        "entityName": "LOCKHEED MARTIN CORP",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-01-28",
                                "accn": "0000936468-25-000009",
                                "val": 65984000000,
                            },
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-01-28",
                                "accn": "0000936468-25-000009",
                                "val": 67571000000,
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-01-28",
                                "accn": "0000936468-25-000009",
                                "val": 71043000000,
                            },
                        ]
                    }
                }
            }
        },
    },
    "0000320193": {
        "cik": "0000320193",
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2023-10-01",
                                "end": "2024-09-28",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                                "accn": "0000320193-24-000123",
                                "val": 391035000000,
                            }
                        ]
                    }
                }
            }
        },
    },
}


SEC_FILING_PASSAGES: dict[tuple[str, str, str, str], dict] = {
    (
        "0000936468",
        "0000936468-25-000009",
        "10-K",
        "revenues",
    ): {
        "text": (
            "Total revenues were $71,043 million for the year ended "
            "December 31, 2024, compared with $67,571 million for the "
            "year ended December 31, 2023."
        ),
        "section": "MD&A",
    },
    (
        "0000936468",
        "0000936468-25-000009",
        "10-K",
        "subsidiary_relationship",
    ): {
        "text": (
            "Lockheed Martin Corporation is the ultimate parent of its "
            "consolidated subsidiaries. Disclosures are made at the "
            "parent level."
        ),
        "section": "Exhibit 21",
    },
}


USASPENDING_AWARDS_BY_NAME: dict[str, list[dict]] = {
    "lockheed martin corp": [
        {
            "Award ID": "USASPEND-LMT-2024-0001",
            "Recipient Name": "LOCKHEED MARTIN CORP",
            "Recipient UEI": "FAKEUEILMT0001",
            "Award Amount": 180000000,
            "Awarding Agency": "Department of Defense",
            "Awarding Sub Agency": "Defense Logistics Agency",
            "Start Date": "2023-11-15",
            "End Date": "2027-09-30",
            "generated_internal_id": "CONT_AWD_USASPENDLMT20240001_97AS_-NONE-_-NONE-",
            "fiscal_year": 2024,
        }
    ],
    "apple inc.": [],
    "example vendor holdings, inc.": [
        {
            "Award ID": "USASPEND-EV-2025-0417",
            "Recipient Name": "Example Vendor Holdings, Inc.",
            "Recipient UEI": "FAKEUEIEV00001",
            "Award Amount": 180000000,
            "Awarding Agency": "Department of Defense",
            "Awarding Sub Agency": "Defense Logistics Agency",
            "Start Date": "2024-10-01",
            "End Date": "2025-09-30",
            "generated_internal_id": "CONT_AWD_USASPENDEV20250417_97AS_-NONE-_-NONE-",
            "fiscal_year": 2025,
        }
    ],
}


ANNUAL_REPORT_PASSAGES_BY_NAME: dict[str, list[dict]] = {
    "example vendor holdings, inc.": [
        {
            "text": (
                "Total revenue for fiscal year 2025 was approximately "
                "$4.0 billion, reflecting growth across our core segments."
            ),
            "source_name": "Annual Report (Investor Relations PDF)",
            "source_url": (
                "https://example-vendor.example/annual-report-2025.pdf"
            ),
            "fiscal_year": 2025,
        }
    ],
    "lockheed martin corp": [],
}


__all__ = [
    "SEC_COMPANY_FACTS_BY_CIK",
    "SEC_FILING_PASSAGES",
    "USASPENDING_AWARDS_BY_NAME",
    "ANNUAL_REPORT_PASSAGES_BY_NAME",
]
