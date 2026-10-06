"""
File: src/veda/adapters/graph_adapter.py
Title: Provenance Graph Adapter
Layer: Adapter layer
Status: Phase 9 readiness

Purpose
-------
``adapt_to_graph`` is a thin wrapper around the provenance builder.
It returns the builder's canonical JSON, including the graph digest.
"""

from __future__ import annotations

from typing import Any

from veda.provenance.graph import GRAPH_VERSION, build_graph
from veda.shared.models import Assessment


def adapt_to_graph(packet: Assessment) -> dict[str, Any]:
    """Return the canonical provenance graph for one packet."""
    return build_graph([packet], packet.chunks).to_dict()


__all__ = ["GRAPH_VERSION", "adapt_to_graph"]
