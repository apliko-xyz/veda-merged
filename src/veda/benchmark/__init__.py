"""
File: src/veda/benchmark/__init__.py
Title: Benchmark Contract Package
Layer: Benchmark
Status: Phase 9 readiness

The NV012 contract lives here. This package does not build a corpus,
generate questions, or call a model.
"""

from veda.benchmark.schema import (
    LOCKED_DISTRIBUTION,
    BenchmarkBundle,
    validate_bundle,
)

__all__ = ["LOCKED_DISTRIBUTION", "BenchmarkBundle", "validate_bundle"]
