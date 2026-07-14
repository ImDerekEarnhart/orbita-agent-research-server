from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from typing import Any

import networkx as nx

from . import eg_lemma_miner as miner


def _canonical_edges(n: int, edges: Iterable[Iterable[int]]) -> list[list[int]]:
    result: set[tuple[int, int]] = set()
    for raw in edges:
        pair = list(raw)
        if len(pair) != 2:
            raise ValueError(f"Every edge must contain exactly two vertices: {pair!r}")
        u, v = int(pair[0]), int(pair[1])
        if not (0 <= u < n and 0 <= v < n):
            raise ValueError(f"Edge ({u}, {v}) is outside vertex range 0..{n - 1}")
        if u == v:
            raise ValueError(f"Self-loop ({u}, {v}) is not allowed")
        result.add((min(u, v), max(u, v)))
    return [[u, v] for u, v in sorted(result)]


def _graph(n: int, edges: list[list[int]]) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    graph.add_edges_from((u, v) for u, v in edges)
    return graph


def _fingerprint(n: int, edges: list[list[int]]) -> str:
    payload = json.dumps({"n": n, "edges": edges}, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def analyze_graph(
    n: int,
    edges: Iterable[Iterable[int]],
    *,
    max_vertices: int,
    max_edges: int,
    timeout_seconds: float = 2.0,
    max_states: int = 500_000,
) -> dict[str, Any]:
    """Run a bounded, exact-witness-oriented graph analysis."""

    n = int(n)
    if n < 1 or n > max_vertices:
        raise ValueError(f"n must be between 1 and {max_vertices}")
    canonical = _canonical_edges(n, edges)
    if len(canonical) > max_edges:
        raise ValueError(f"At most {max_edges} distinct edges are accepted")
    graph = _graph(n, canonical)
    degrees = [int(degree) for _, degree in graph.degree()]
    limits = miner.SearchLimits(
        timeout_seconds=max(0.05, min(float(timeout_seconds), 10.0)),
        max_states=max(1_000, min(int(max_states), 2_000_000)),
    )

    profile: dict[str, Any] = {}
    first: dict[str, Any] | None = None
    complete = True
    incomplete_reason = None
    for length in miner.powers_of_two_up_to(n):
        try:
            cycle = miner.find_cycle_exact_bounded(graph, length, limits)
            profile[str(length)] = {"found": cycle is not None, "cycle": cycle}
            if cycle is not None:
                first = {"length": length, "cycle": cycle}
                break
        except miner.SearchLimitExceeded as exc:
            complete = False
            incomplete_reason = str(exc)
            profile[str(length)] = {"found": None, "cycle": None, "inconclusive": True, "reason": str(exc)}
            break

    carr = miner.carr_structure_profile(graph)
    bfs = miner.bfs_layer_profile(graph, radius=4)
    return {
        "scope": {
            "analysis": "bounded finite graph analysis",
            "universal_proof": False,
            "search_timeout_seconds_per_length": limits.timeout_seconds,
            "search_max_states_per_length": limits.max_states,
        },
        "graph": {
            "fingerprint": _fingerprint(n, canonical),
            "n": n,
            "m": len(canonical),
            "connected": nx.is_connected(graph) if n else False,
            "components": nx.number_connected_components(graph),
            "min_degree": min(degrees, default=0),
            "max_degree": max(degrees, default=0),
            "degree_sequence": sorted(degrees),
            "density": nx.density(graph),
            "girth": miner.exact_girth(graph),
            "edges": canonical,
        },
        "power_cycle_search": {
            "powers_checked": miner.powers_of_two_up_to(n),
            "profile": profile,
            "first_power_cycle": first,
            "has_power_two_cycle": first is not None,
            "analysis_complete_until_first_witness": complete,
            "incomplete_reason": incomplete_reason,
        },
        "minimal_counterexample_necessary_conditions": carr,
        "bfs_layer_fingerprint": bfs,
        "interpretation": (
            "A found cycle is a concrete finite witness. No cycle found within bounded search limits is not a "
            "counterexample unless analysis_complete_until_first_witness is true for every applicable power."
        ),
    }


def _lean_list(values: list[int]) -> str:
    return "[" + ", ".join(str(value) for value in values) + "]"


def _lean_edges(edges: list[list[int]]) -> str:
    return "[\n    " + ",\n    ".join(f"({u}, {v})" for u, v in edges) + "\n  ]"


def render_lean_certificate(n: int, edges: Iterable[Iterable[int]], cycle: Iterable[int]) -> str:
    canonical = _canonical_edges(int(n), edges)
    graph = _graph(int(n), canonical)
    witness = [int(value) for value in cycle]
    if len(witness) < 5 or witness[0] != witness[-1]:
        raise ValueError("Cycle must be closed and contain at least four edges")
    if len(set(witness[:-1])) != len(witness) - 1:
        raise ValueError("Cycle vertices before the repeated endpoint must be unique")
    if any(value < 0 or value >= n for value in witness):
        raise ValueError("Cycle contains a vertex outside the graph")
    if any(not graph.has_edge(u, v) for u, v in zip(witness, witness[1:], strict=False)):
        raise ValueError("Every consecutive cycle pair must be a graph edge")
    length = len(witness) - 1
    if length < 4 or length & (length - 1):
        raise ValueError("Cycle length must be a power of two of at least four")
    if min((degree for _, degree in graph.degree()), default=0) < 3:
        raise ValueError("The Lean certificate requires minimum degree at least three")
    power = length.bit_length() - 1
    return f'''import ErdosGyarfas.Certificate
import Std.Tactic.NativeDecide

open ErdosGyarfas

/-- Generated by Orbita Agent Research Server from a finite graph witness. -/
def generatedCertificate : Certificate := {{
  n := {int(n)}
  edges := {_lean_edges(canonical)}
  cycle := {_lean_list(witness)}
  power := {power}
}}

theorem generatedCertificate_is_valid :
    checkCertificate generatedCertificate = true := by
  native_decide
'''


def export_lean_certificate(
    export_dir: Path,
    *,
    n: int,
    edges: Iterable[Iterable[int]],
    cycle: Iterable[int],
    project_name: str = "lean_certificate",
) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(project_name).name).strip("._")
    if not safe:
        raise ValueError("project_name must contain at least one safe character")
    safe = safe[:80]
    source = render_lean_certificate(n, edges, cycle)
    export_dir.mkdir(parents=True, exist_ok=True)
    project = export_dir / safe
    template = resources.files("orbita_agent.resources").joinpath("lean")
    with resources.as_file(template) as template_path:
        shutil.copytree(template_path, project, dirs_exist_ok=True)
    path = project / "ErdosGyarfas" / "GeneratedWitness.lean"
    path.write_text(source, encoding="utf-8")
    return {
        "path": str(path.resolve()),
        "project_path": str(project.resolve()),
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "bytes": len(source.encode("utf-8")),
        "verification_command": "lake build",
        "verification_working_directory": str(project.resolve()),
        "boundary": "This verifies one concrete finite graph and cycle; it is not a universal proof.",
    }
