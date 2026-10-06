# filename: src/veda/interfaces/cli.py
# title: Command-Line Interface
# layer: Interface layer
# status: Phase 7 — Sub-phase 7C
# description:
#     The VEDA command-line interface. Constructs an
#     InterfaceConfig from command-line arguments, calls
#     build_bundle / build_resolver / build_extractor, invokes
#     run_assessment(), and renders the packet as JSON, as a
#     human-readable panel, or both.
#
#     Exit codes:
#
#         0 — a packet was produced and validated. This is true
#             for every one of the five assessment states:
#             SUPPORTED, SUPPORTED_WITH_LIMITATIONS,
#             CONFLICTING_EVIDENCE, INSUFFICIENT_EVIDENCE,
#             REQUIRES_HUMAN_REVIEW. An abstention is a valid
#             answer, not a failure.
#
#         2 — configuration error. Raised before the pipeline runs.
#         3 — bundle build error.
#         4 — unexpected pipeline error. No packet was produced.
#
#     Provider mode is fixture-only in Phase 7. Passing
#     --providers live raises InterfaceConfigError, which the CLI
#     catches and reports with exit code 2.
#
#     Extractor mode "composite" is rejected at bundle build time
#     with InterfaceConfigError, exit code 2.
#
#     Rendering:
#         --format json  -> packet.model_dump_json(indent=2)
#         --format human -> rich Panel with assessment status
#                           color
#         --format both  -> both, in that order
#
# source:
#     MERGED — the CLI shape comes from the personal prototype's
#     prototype/cli.py. That prototype used Typer and rich with a
#     Settings class that loaded SEC_API_USER_AGENT from .env. The
#     merged version keeps Typer and rich, replaces Settings with
#     InterfaceConfig, and calls the merged pipeline instead of the
#     prototype's own orchestrator.
#
# notes:
#     - The CLI never constructs a model SDK. It never reads an
#       API key. The default path is entirely offline.
#     - The CLI imports build_bundle / build_resolver /
#       build_extractor from the interface layer. It does not
#       import any provider directly.
#     - The rich Panel color is chosen from the assessment status.
#       The human_review_reason (when present) is rendered above
#       the claims, because it is the most important line when it
#       exists.

from __future__ import annotations

import sys
from typing import Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from typer.core import TyperGroup

from veda.interfaces.bundle_builder import (
    build_bundle,
    build_extractor,
    build_resolver,
)
from veda.interfaces.config import InterfaceConfig
from veda.interfaces.errors import (
    BundleBuildError,
    InterfaceConfigError,
    InterfaceError,
    PipelineError,
)
from veda.pipeline.orchestrator import run_assessment
from veda.provenance.graph import build_graph
from veda.shared.enums import AssessmentStatus
from veda.shared.models import Assessment
from veda.shared.periods import RequestedPeriod


class _DefaultAssessGroup(TyperGroup):
    """Treat a bare vendor/year invocation as the assess command."""

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands and not str(args[0]).startswith("-"):
            args = ["assess", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    name="veda",
    cls=_DefaultAssessGroup,
    help=(
        "VEDA — Vendor Economic Dependency Assessment. "
        "Produce an evidence-linked assessment packet for a "
        "vendor and fiscal period."
    ),
    no_args_is_help=True,
)

console = Console()


# --------------------------------------------------------------------
# Exit codes
# --------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 2
EXIT_BUNDLE_ERROR = 3
EXIT_PIPELINE_ERROR = 4


# --------------------------------------------------------------------
# Status -> Panel color
# --------------------------------------------------------------------
_PANEL_COLOR = {
    AssessmentStatus.SUPPORTED: "green",
    AssessmentStatus.SUPPORTED_WITH_LIMITATIONS: "yellow",
    AssessmentStatus.CONFLICTING_EVIDENCE: "dark_orange",
    AssessmentStatus.INSUFFICIENT_EVIDENCE: "red",
    AssessmentStatus.REQUIRES_HUMAN_REVIEW: "magenta",
}


# --------------------------------------------------------------------
# Requested period parsing
# --------------------------------------------------------------------
def _build_requested_period(year: int) -> RequestedPeriod:
    """
    Build a RequestedPeriod from a bare year.

    The pipeline resolves the dated period from dated evidence when
    the request carries no dates (Fix A). This is the natural CLI
    path: the user types a year, not a date range.
    """
    return RequestedPeriod(fiscal_year=year, raw=str(year))


# --------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------
def _render_json(packet: Assessment) -> None:
    """
    Print the packet as indented JSON.

    Uses the built-in print(), not console.print(). Rich's renderer
    wraps long lines to the console width, which inserts raw newlines
    into the JSON output and makes it unparseable by json.loads. The
    machine-readable output must be verbatim, so it goes to stdout
    without Rich's formatting.
    """
    print(packet.model_dump_json(indent=2))


def _render_human(packet: Assessment) -> None:
    """Print a color-coded rich panel with the packet's human summary."""
    color = _PANEL_COLOR.get(packet.assessment_status, "white")
    summary = packet.human_summary()
    panel = Panel(
        Text(summary),
        title="Evidence-Linked Assessment",
        border_style=color,
    )
    console.print(panel)


# --------------------------------------------------------------------
# The command
# --------------------------------------------------------------------
@app.command("assess")
def assess(
    vendor: str = typer.Argument(
        ...,
        help="Vendor or company name, e.g. 'Lockheed Martin Corp'.",
    ),
    year: int = typer.Argument(
        ...,
        help="Fiscal year as an integer, e.g. 2024.",
    ),
    resolver: str = typer.Option(
        "fixture",
        "--resolver",
        help="Entity-resolution source: 'fixture' or 'live'.",
    ),
    providers: str = typer.Option(
        "fixture",
        "--providers",
        help=(
            "Provider set: 'fixture' or 'live'. 'fixture' uses "
            "deterministic offline data. 'live' fetches from real "
            "SEC and USAspending endpoints and requires a real "
            "User-Agent."
        ),
    ),
    extractor: str = typer.Option(
        "rule_based",
        "--extractor",
        help=(
            "Claim extractor. Phase 7 accepts only 'rule_based'. "
            "'composite' is reserved for a later phase."
        ),
    ),
    output_format: str = typer.Option(
        "both",
        "--format",
        help="Output format: 'json', 'human', or 'both'.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        help="Optional path to write the JSON packet to.",
    ),
    user_agent: Optional[str] = typer.Option(
        None,
        "--user-agent",
        help=(
            "SEC User-Agent. Required for future live-provider "
            "construction. Can be set via the SEC_API_USER_AGENT "
            "environment variable."
        ),
    ),
) -> None:
    """
    Run the pipeline for one vendor and one fiscal year.
    """
    # 1. Load the user agent from the environment if not supplied.
    import os
    effective_user_agent = user_agent or os.environ.get(
        "SEC_API_USER_AGENT",
        "VEDA CLI user agent not set",
    )

    # 2. Build the configuration.
    try:
        config = InterfaceConfig(
            user_agent=effective_user_agent,
            resolver_mode=resolver,        # type: ignore[arg-type]
            provider_mode=providers,       # type: ignore[arg-type]
            extractor_mode=extractor,      # type: ignore[arg-type]
            output_format=output_format,   # type: ignore[arg-type]
            output_path=output,
        )
    except ValidationError as exc:
        console.print(
            f"[red]Configuration error:[/red] {exc}",
            highlight=False,
        )
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    # 3. Build the bundle, resolver, and extractor.
    try:
        bundle = build_bundle(config)
        resolver_source = build_resolver(config)
        claim_extractor = build_extractor(config)
    except InterfaceConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=EXIT_CONFIG_ERROR)
    except BundleBuildError as exc:
        console.print(f"[red]Bundle error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=EXIT_BUNDLE_ERROR)

    # 4. Run the pipeline.
    try:
        packet = run_assessment(
            vendor_name=vendor,
            requested_period=_build_requested_period(year),
            bundle=bundle,
            resolver_source=resolver_source,
            user_agent=config.user_agent,
            extractor=claim_extractor,
        )
    except Exception as exc:
        console.print(
            f"[red]Pipeline error:[/red] {type(exc).__name__}: {exc}",
            highlight=False,
        )
        raise typer.Exit(code=EXIT_PIPELINE_ERROR)

    # 5. Render.
    if config.output_format in ("json", "both"):
        _render_json(packet)
    if config.output_format in ("human", "both"):
        _render_human(packet)

    # 6. Optional file write.
    if config.output_path:
        try:
            with open(config.output_path, "w", encoding="utf-8") as fh:
                fh.write(packet.model_dump_json(indent=2))
            console.print(f"[cyan]Wrote packet to {config.output_path}[/cyan]")
        except OSError as exc:
            console.print(f"[red]Could not write output:[/red] {exc}")
            raise typer.Exit(code=EXIT_PIPELINE_ERROR)

    raise typer.Exit(code=EXIT_SUCCESS)


@app.command("graph")
def graph_command(
    vendor: str = typer.Argument(..., help="Vendor or company name."),
    year: int = typer.Argument(..., help="Fiscal year, e.g. 2024."),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        help="Path to write canonical graph JSON. Prints to stdout when omitted.",
    ),
) -> None:
    """
    Write the provenance graph for one fixture assessment.

    The command uses fixture providers so it does not touch the network.
    """
    import json
    import os

    effective_user_agent = os.environ.get(
        "SEC_API_USER_AGENT",
        "VEDA CLI user agent not set",
    )
    try:
        config = InterfaceConfig(
            user_agent=effective_user_agent,
            resolver_mode="fixture",
            provider_mode="fixture",
            extractor_mode="rule_based",
            output_format="json",
            output_path=output,
        )
        bundle = build_bundle(config)
        resolver_source = build_resolver(config)
        packet = run_assessment(
            vendor_name=vendor,
            requested_period=_build_requested_period(year),
            bundle=bundle,
            resolver_source=resolver_source,
            user_agent=config.user_agent,
        )
    except (ValidationError, InterfaceConfigError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=EXIT_CONFIG_ERROR)
    except BundleBuildError as exc:
        console.print(f"[red]Bundle error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=EXIT_BUNDLE_ERROR)
    except Exception as exc:
        console.print(
            f"[red]Pipeline error:[/red] {type(exc).__name__}: {exc}",
            highlight=False,
        )
        raise typer.Exit(code=EXIT_PIPELINE_ERROR)

    payload = build_graph([packet], packet.chunks).to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)
    if output:
        try:
            with open(output, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.write("\n")
        except OSError as exc:
            console.print(f"[red]Could not write output:[/red] {exc}")
            raise typer.Exit(code=EXIT_PIPELINE_ERROR)
    else:
        print(text)
    raise typer.Exit(code=EXIT_SUCCESS)


def main() -> None:
    """Entry point used by the console script and by tests."""
    app()


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]