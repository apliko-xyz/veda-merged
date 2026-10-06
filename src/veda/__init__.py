# filename: src/veda/__init__.py
# title: Package Marker and Version Declaration
# description:
#     Marks src/veda/ as an importable Python package and declares the
#     package version. Contains no business logic — its only purpose is
#     to make `import veda` work and to expose veda.__version__ as the
#     single source of truth for the package version, so no other file
#     has to hardcode it.
#
#     The docstring below is the map of the package. A developer opening
#     this file cold should be able to see, in one place, what every
#     subpackage is for and where to look for a given concern. That is
#     the reason this docstring is longer than the code.
#
# source:
#     AUTHORED — neither prototype had a package root at this level.
#     The personal prototype used a bare `prototype/` folder; veda used
#     `app/`. The merged system uses `src/veda/` so the package is
#     importable as `veda` from anywhere after installation.
#
# notes:
#     - No module in this package carries the word "prototype" in its
#       name. The merged system is the production system.
#     - The subpackage list below is authoritative. If a new subpackage
#       is added, it must be documented here in the same format, so this
#       docstring remains a complete map of the package.
#     - The version string here is the single source of truth. It is
#       mirrored in pyproject.toml; the two must be kept in sync until a
#       build step is added to derive one from the other.

"""
VEDA — Vendor Economic Dependency Assessment.

A provenance-aware evidence pipeline for vendor financial and
dependency analysis. Given a vendor name and a fiscal period, the
system resolves the entity, retrieves public evidence, extracts typed
claims, preserves source provenance, detects conflicts and
non-comparability, and returns either a defensible evidence packet or
an explicit statement that the evidence is insufficient.

Package map
-----------

    veda.shared
        The canonical data contract. Every model, enum, ID format,
        period representation, and cross-record validation rule lives
        here. No other subpackage defines a data shape.

    veda.entity
        Entity resolution and reporting-boundary handling. Takes a raw
        vendor name and returns a ResolvedEntity — or an explicit
        AMBIGUOUS / NOT_FOUND status. Handles the subsidiary case where
        the named vendor is not the SEC reporting entity.

    veda.providers
        Source retrieval. One module per source (SEC Company Facts,
        SEC filings, USAspending, annual reports) behind a shared
        provider contract. Providers retrieve; they do not normalize
        or interpret.

    veda.normalization
        Converts raw source records into canonical Evidence objects.
        One converter per source shape. After normalization, every
        record downstream has the same structure regardless of where
        it came from.

    veda.pipeline
        The working stages — claim extraction, validation, conflict
        detection, assessment, abstention — and the orchestrator that
        wires them into a single function. This is the only place that
        knows the full order of operations.

    veda.interfaces
        The entry points: CLI, HTTP API, and dashboard. All three call
        the same orchestrator. None contains business logic.

    veda.adapters
        Output adapters for the provenance graph and the benchmark.
        Populated after the prototype is frozen.

    veda.provenance
        In-memory provenance graph: spans, transformations, evidence
        versions, validation, and backward/forward trace.

    veda.benchmark
        Benchmark contract and bundle validation for the NV012 slice.

Reading order for a new contributor
-----------------------------------

1. veda.shared.models     — what objects move through the system
2. veda.shared.enums      — what states those objects can be in
3. veda.pipeline.orchestrator — the full flow in one function
4. veda.providers.base    — how sources are retrieved
5. veda.normalization     — how source shapes become Evidence
6. veda.interfaces        — how a user reaches the pipeline

Every module in this package follows one documentation standard:
the file header names the file, its title, its purpose, its sources,
and any design notes; every class has a docstring; every public
function has a docstring covering inputs, outputs, error behaviour,
and its place in the flow. See any file under veda/pipeline for
a worked example of the standard.
"""

__version__ = "0.1.0"

# __all__ is empty on purpose. This package exposes no names at the
# top level; every public object is reached through its subpackage.
# Callers should import from `veda.shared`, `veda.pipeline`, etc.
__all__: list[str] = []