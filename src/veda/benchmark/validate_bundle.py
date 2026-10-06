"""
File: src/veda/benchmark/validate_bundle.py
Title: Benchmark bundle validator
Layer: Benchmark
Status: Phase 9 readiness

Run ``python -m veda.benchmark.validate_bundle --bundle <path>``.
Exit 0 when the bundle matches contract v0.2. Exit 1 and print one
error per line when it does not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from veda.benchmark.schema import BenchmarkBundle, validate_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a NV012 benchmark bundle against contract v0.2."
    )
    parser.add_argument("--bundle", required=True, help="Path to a bundle JSON file.")
    args = parser.parse_args(argv)
    path = Path(args.bundle)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        bundle = BenchmarkBundle.model_validate(payload)
    except FileNotFoundError:
        print(f"bundle not found: {path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"bundle JSON could not be parsed: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            print(f"{location}: {error['msg']}", file=sys.stderr)
        return 1
    errors = validate_bundle(bundle)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"valid bundle: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
