# filename: tests/interfaces/test_cli.py
# title: Interface Layer - CLI Tests
# layer: Test suite - interfaces
# status: Phase 7 — Sub-phase 7C
# description:
#     Verifies the CLI: exit codes, JSON output, human output, the
#     fixture-only provider mode, and the composite extractor
#     rejection.
#
#     The critical guarantee: a valid packet in ANY assessment state
#     produces exit code 0. An abstention is a valid answer, not a
#     failure.

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from veda.interfaces.cli import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def _set_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a test user agent so the CLI does not fall back to its default."""
    monkeypatch.setenv("SEC_API_USER_AGENT", "Test test@example.com")


# ====================================================================
# 1. Help
# ====================================================================

def test_cli_help_runs() -> None:
    result = runner.invoke(app, ["assess", "--help"])
    assert result.exit_code == 0
    assert "vendor" in result.stdout.lower()
    assert "year" in result.stdout.lower()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "graph" in root.stdout.lower()


# ====================================================================
# 2. Supported case
# ====================================================================

def test_supported_case_json_exit_zero() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "json"],
    )
    assert result.exit_code == 0


def test_supported_case_json_parses() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    assert packet["assessment_status"] in (
        "supported",
        "supported_with_limitations",
    )


def test_supported_case_revenue_value() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    revenues = [
        c for c in packet["claims"] if c["claim_type"] == "total_revenue"
    ]
    assert len(revenues) == 1
    assert revenues[0]["value"] == 71043000000


def test_supported_case_award_value() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    awards = [
        item for item in packet["evidence"]
        if item["evidence_category"] == "procurement_award"
    ]
    assert len(awards) >= 1
    assert awards[0]["raw_value"] == 180000000
    assert awards[0]["reporting_period"]["label"] == "FFY2024"
    obligations = [
        c for c in packet["claims"] if c["claim_type"] == "procurement_obligation"
    ]
    assert obligations == []


def test_supported_case_no_false_conflict() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    assert packet["conflicts"] == []


# ====================================================================
# 3. Human format
# ====================================================================

def test_supported_case_human_exit_zero() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "human"],
    )
    assert result.exit_code == 0


def test_supported_case_human_shows_vendor() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "human"],
    )
    assert "Lockheed Martin Corp" in result.stdout
    assert "LOCKHEED MARTIN CORP" in result.stdout


def test_supported_case_human_shows_revenue() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "human"],
    )
    assert "71043000000" in result.stdout


# ====================================================================
# 4. Abstention — the critical test
# ====================================================================

def test_unknown_vendor_exit_zero() -> None:
    """An abstention is a valid answer. Exit code is 0."""
    result = runner.invoke(
        app,
        ["Totally Fake Vendor Name", "2024", "--format", "json"],
    )
    assert result.exit_code == 0


def test_unknown_vendor_status_requires_human_review() -> None:
    result = runner.invoke(
        app,
        ["Totally Fake Vendor Name", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    assert packet["assessment_status"] == "requires_human_review"


def test_unknown_vendor_has_reason() -> None:
    result = runner.invoke(
        app,
        ["Totally Fake Vendor Name", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    assert packet["human_review_reason"] is not None
    assert "Totally Fake Vendor Name" in packet["human_review_reason"]


def test_unknown_vendor_has_missing_evidence() -> None:
    result = runner.invoke(
        app,
        ["Totally Fake Vendor Name", "2024", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    assert len(packet["missing_evidence"]) >= 1
    reasons = [m["reason"] for m in packet["missing_evidence"]]
    assert "entity_ambiguous" in reasons


# ====================================================================
# 5. Known vendor, unknown year
# ====================================================================

def test_known_vendor_unknown_year_exit_zero() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "1899", "--format", "json"],
    )
    assert result.exit_code == 0


def test_known_vendor_unknown_year_abstains() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "1899", "--format", "json"],
    )
    packet = json.loads(result.stdout)
    assert packet["assessment_status"] in (
        "insufficient_evidence",
        "requires_human_review",
    )


# ====================================================================
# 6. Configuration errors
# ====================================================================

def test_providers_live_constructs_live_bundle_but_cli_defaults_to_fixture() -> None:
    """
    provider_mode='live' is accepted by InterfaceConfig. The CLI
    will construct a live bundle when --providers live is passed.

    This test invokes the CLI in fixture mode (default) and asserts
    the resulting packet shows all fixture providers. It does not
    invoke live mode, because live mode requires a real network
    and a real User-Agent and is not part of the deterministic
    test suite.
    """
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--format", "json"],
    )
    assert result.exit_code == 0
    packet = json.loads(result.stdout)
    modes = packet["run_metadata"]["provider_modes"]
    assert modes["sec_company_facts"] == "fixture"
    assert modes["sec_filing"] == "fixture"
    assert modes["usaspending"] == "fixture"
    assert modes["annual_report"] == "fixture"


def test_extractor_composite_rejected() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--extractor", "composite"],
    )
    assert result.exit_code == 2


def test_extractor_composite_error_message() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024", "--extractor", "composite"],
    )
    combined = result.stdout + (result.stderr or "")
    assert "later phase" in combined.lower() or "composite" in combined.lower()


# ====================================================================
# 7. Defaults
# ====================================================================

def test_default_format_is_both() -> None:
    result = runner.invoke(
        app,
        ["Lockheed Martin Corp", "2024"],
    )
    assert result.exit_code == 0
    # Both JSON and human output should be present.
    assert '"assessment_status"' in result.stdout
    assert "Lockheed Martin Corp" in result.stdout


# ====================================================================
# 8. Output file
# ====================================================================

def test_graph_command_writes_fixture_graph(tmp_path) -> None:
    out_path = tmp_path / "graph.json"
    result = runner.invoke(
        app,
        ["graph", "Lockheed Martin Corp", "2024", "--output", str(out_path)],
    )
    assert result.exit_code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["graph_version"]
    assert payload["nodes"]
    assert payload["edges"]
    assert len(payload["graph_digest"]) == 64


def test_output_flag_writes_file(tmp_path) -> None:
    out_path = tmp_path / "packet.json"
    result = runner.invoke(
        app,
        [
            "Lockheed Martin Corp",
            "2024",
            "--format", "json",
            "--output", str(out_path),
        ],
    )
    assert result.exit_code == 0
    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["assessment_status"] in ("supported", "supported_with_limitations")