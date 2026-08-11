#!/usr/bin/env python3
"""
eg_lemma_miner.py

Finite-world proof-assistance engine for the Erdos-Gyarfas power-of-two
cycle conjecture.

The program does four jobs:
  1. Generate cubic graphs that are deliberately hard for the easy C4/C8 cases.
  2. Classify graphs using necessary conditions for a minimal counterexample.
  3. Reduce graphs to proper 3-cores when possible.
  4. Export concrete power-of-two cycle witnesses for Lean verification.

This program does not claim a universal proof.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx


class SearchLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchLimits:
    timeout_seconds: float = 10.0
    max_states: int = 2_000_000


@dataclass
class OptimizationResult:
    graph: nx.Graph
    initial_score: int
    final_score: int
    steps_used: int
    restart: int
    seed: int


def normalize_graph(graph: nx.Graph) -> nx.Graph:
    return nx.convert_node_labels_to_integers(graph, ordering="sorted")


def canonical_edges(graph: nx.Graph) -> list[list[int]]:
    return [
        [int(u), int(v)]
        for u, v in sorted((min(a, b), max(a, b)) for a, b in graph.edges())
    ]


def graph_from_edges(n: int, edges: Sequence[Sequence[int]]) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    graph.add_edges_from((int(edge[0]), int(edge[1])) for edge in edges)
    return normalize_graph(graph)


def powers_of_two_up_to(n: int) -> list[int]:
    values: list[int] = []
    value = 4
    while value <= n:
        values.append(value)
        value *= 2
    return values


def enumerate_cycles_exact(
    graph: nx.Graph,
    length: int,
    limit: int | None = None,
) -> list[list[int]]:
    """Enumerate undirected simple cycles of exactly the requested length.

    Each cycle is returned once. The first vertex is the minimum vertex in the
    cycle, and reverse orientations are removed by an endpoint ordering rule.
    This is practical for short cycles in sparse cubic graphs.
    """
    graph = normalize_graph(graph)
    if length < 3 or graph.number_of_nodes() < length:
        return []

    adjacency = {u: sorted(graph.neighbors(u)) for u in graph.nodes()}
    found: list[list[int]] = []

    for start in sorted(graph.nodes()):
        path = [start]
        visited = {start}

        def dfs(vertex: int) -> None:
            if limit is not None and len(found) >= limit:
                return
            if len(path) == length:
                if start in adjacency[vertex] and path[1] < path[-1]:
                    found.append([int(x) for x in path] + [int(start)])
                return

            for neighbor in adjacency[vertex]:
                if neighbor <= start or neighbor in visited:
                    continue
                visited.add(neighbor)
                path.append(neighbor)
                dfs(neighbor)
                path.pop()
                visited.remove(neighbor)

        dfs(start)
        if limit is not None and len(found) >= limit:
            break

    return found


def count_cycles_exact(graph: nx.Graph, length: int, cap: int | None = None) -> int:
    return len(enumerate_cycles_exact(graph, length, limit=cap))


def find_cycle_exact_bounded(
    graph: nx.Graph,
    length: int,
    limits: SearchLimits,
) -> list[int] | None:
    """Exact DFS for one simple cycle, with honest timeout/state limits."""
    graph = normalize_graph(graph)
    if length < 3 or graph.number_of_nodes() < length:
        return None

    nodes = sorted(graph.nodes())
    adjacency = {u: sorted(graph.neighbors(u)) for u in nodes}
    started = time.monotonic()
    states = 0

    def check_limits() -> None:
        nonlocal states
        states += 1
        if states > limits.max_states:
            raise SearchLimitExceeded(
                f"state limit exceeded during C{length}: {states} > {limits.max_states}"
            )
        if states % 1024 == 0 and time.monotonic() - started > limits.timeout_seconds:
            raise SearchLimitExceeded(
                f"timeout during C{length}: > {limits.timeout_seconds:.3f}s"
            )

    for start in nodes:
        path = [start]
        visited = {start}

        def dfs(vertex: int) -> list[int] | None:
            check_limits()
            if len(path) == length:
                if start in adjacency[vertex]:
                    return [int(x) for x in path] + [int(start)]
                return None

            for neighbor in adjacency[vertex]:
                if neighbor <= start or neighbor in visited:
                    continue
                visited.add(neighbor)
                path.append(neighbor)
                result = dfs(neighbor)
                if result is not None:
                    return result
                path.pop()
                visited.remove(neighbor)
            return None

        result = dfs(start)
        if result is not None:
            return result

    return None


def exact_girth(graph: nx.Graph) -> int | None:
    """Return exact girth for an undirected graph, or None for a forest."""
    graph = normalize_graph(graph)
    best = math.inf
    for root in graph.nodes():
        distances = {root: 0}
        parent = {root: None}
        queue = [root]
        head = 0
        while head < len(queue):
            vertex = queue[head]
            head += 1
            for neighbor in graph.neighbors(vertex):
                if neighbor not in distances:
                    distances[neighbor] = distances[vertex] + 1
                    parent[neighbor] = vertex
                    queue.append(neighbor)
                elif parent[vertex] != neighbor:
                    candidate = distances[vertex] + distances[neighbor] + 1
                    if candidate < best:
                        best = candidate
        if best == 3:
            break
    return None if best == math.inf else int(best)


def power_hardness_counts(graph: nx.Graph) -> tuple[int, int]:
    return count_cycles_exact(graph, 4), count_cycles_exact(graph, 8)


def power_hardness_score(graph: nx.Graph) -> int:
    c4, c8 = power_hardness_counts(graph)
    return 1_000_000 * c4 + c8


def short_cycle_score(graph: nx.Graph, max_length: int = 8) -> int:
    score = 0
    for length in range(3, max_length + 1):
        weight = 10 ** (max_length - length)
        score += weight * count_cycles_exact(graph, length)
    return score


def random_cubic_graph(n: int, seed: int) -> nx.Graph:
    if n < 4 or n % 2 != 0:
        raise ValueError("A cubic graph requires an even n >= 4")
    return normalize_graph(nx.random_regular_graph(3, n, seed=seed))


def attempt_two_switch(graph: nx.Graph, rng: random.Random) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]] | None:
    edges = list(graph.edges())
    if len(edges) < 2:
        return None
    edge1, edge2 = rng.sample(edges, 2)
    a, b = edge1
    c, d = edge2
    if len({a, b, c, d}) < 4:
        return None

    if rng.random() < 0.5:
        new1, new2 = (a, c), (b, d)
    else:
        new1, new2 = (a, d), (b, c)

    if new1[0] == new1[1] or new2[0] == new2[1]:
        return None
    if graph.has_edge(*new1) or graph.has_edge(*new2):
        return None

    graph.remove_edge(*edge1)
    graph.remove_edge(*edge2)
    graph.add_edge(*new1)
    graph.add_edge(*new2)
    return edge1, edge2, new1, new2


def undo_two_switch(
    graph: nx.Graph,
    change: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]],
) -> None:
    edge1, edge2, new1, new2 = change
    graph.remove_edge(*new1)
    graph.remove_edge(*new2)
    graph.add_edge(*edge1)
    graph.add_edge(*edge2)


def optimize_cubic_graph(
    n: int,
    seed: int,
    steps: int,
    restart: int,
    objective: str = "power",
) -> OptimizationResult:
    rng = random.Random(seed)
    graph = random_cubic_graph(n, seed)
    score_fn = power_hardness_score if objective == "power" else short_cycle_score
    current_score = score_fn(graph)
    initial_score = current_score
    best_graph = graph.copy()
    best_score = current_score

    for step in range(steps):
        change = attempt_two_switch(graph, rng)
        if change is None:
            continue

        candidate_score = score_fn(graph)
        progress = step / max(steps, 1)
        temperature = max(0.02, 6.0 * (1.0 - progress))
        delta = current_score - candidate_score
        accept = candidate_score <= current_score
        if not accept:
            accept = rng.random() < math.exp(delta / temperature)

        if accept:
            current_score = candidate_score
            if candidate_score < best_score:
                best_score = candidate_score
                best_graph = graph.copy()
                if objective == "power" and best_score == 0:
                    return OptimizationResult(
                        graph=normalize_graph(best_graph),
                        initial_score=initial_score,
                        final_score=best_score,
                        steps_used=step + 1,
                        restart=restart,
                        seed=seed,
                    )
        else:
            undo_two_switch(graph, change)

    return OptimizationResult(
        graph=normalize_graph(best_graph),
        initial_score=initial_score,
        final_score=best_score,
        steps_used=steps,
        restart=restart,
        seed=seed,
    )


def three_core(graph: nx.Graph) -> nx.Graph:
    if graph.number_of_nodes() == 0:
        return graph.copy()
    return normalize_graph(nx.k_core(graph, k=3))


def proper_three_core_witness(graph: nx.Graph) -> dict[str, Any] | None:
    """Find a proper deletion whose remaining graph has a nonempty 3-core.

    If no such single vertex or edge deletion exists, then no proper subgraph
    can have minimum degree at least 3. This gives an executable test of the
    minimal-counterexample subgraph condition.
    """
    graph = normalize_graph(graph)

    for vertex in sorted(graph.nodes()):
        reduced = graph.copy()
        reduced.remove_node(vertex)
        core = three_core(reduced)
        if core.number_of_nodes() > 0:
            return {
                "deletion_type": "vertex",
                "deleted": int(vertex),
                "core_n": core.number_of_nodes(),
                "core_m": core.number_of_edges(),
                "core_edges": canonical_edges(core),
            }

    for u, v in sorted((min(a, b), max(a, b)) for a, b in graph.edges()):
        reduced = graph.copy()
        reduced.remove_edge(u, v)
        core = three_core(reduced)
        if core.number_of_nodes() > 0:
            return {
                "deletion_type": "edge",
                "deleted": [int(u), int(v)],
                "core_n": core.number_of_nodes(),
                "core_m": core.number_of_edges(),
                "core_edges": canonical_edges(core),
            }

    return None


def reduce_to_critical_three_core(graph: nx.Graph) -> tuple[nx.Graph, list[dict[str, Any]]]:
    """Repeatedly shrink to a proper nonempty 3-core when one exists."""
    current = normalize_graph(graph)
    steps: list[dict[str, Any]] = []

    while True:
        witness = proper_three_core_witness(current)
        if witness is None:
            break
        next_graph = graph_from_edges(witness["core_n"], witness["core_edges"])
        current_size = (current.number_of_nodes(), current.number_of_edges())
        next_size = (next_graph.number_of_nodes(), next_graph.number_of_edges())
        if next_size >= current_size:
            break
        steps.append(
            {
                "from_n": current.number_of_nodes(),
                "from_m": current.number_of_edges(),
                **witness,
            }
        )
        current = next_graph

    return normalize_graph(current), steps


def carr_structure_profile(graph: nx.Graph) -> dict[str, Any]:
    """Check necessary conditions from Carr's 2026 minimal-counterexample note."""
    graph = normalize_graph(graph)
    degrees = dict(graph.degree())
    n = graph.number_of_nodes()
    degree3 = {v for v, degree in degrees.items() if degree == 3}
    high = {v for v, degree in degrees.items() if degree >= 4}
    high_internal_edges = [
        [int(min(u, v)), int(max(u, v))]
        for u, v in graph.edges()
        if u in high and v in high
    ]
    cubic_domination_failures = [
        int(v)
        for v in graph.nodes()
        if not any(neighbor in degree3 for neighbor in graph.neighbors(v))
    ]
    fraction_degree3 = len(degree3) / n if n else 0.0
    core_witness = proper_three_core_witness(graph)

    return {
        "min_degree_at_least_3": min(degrees.values(), default=0) >= 3,
        "degree3_count": len(degree3),
        "fraction_degree3": fraction_degree3,
        "fraction_degree3_at_least_4_over_7": fraction_degree3 >= (4.0 / 7.0),
        "high_degree_vertices": sorted(int(v) for v in high),
        "high_degree_independent": len(high_internal_edges) == 0,
        "high_degree_internal_edges": high_internal_edges,
        "every_vertex_adjacent_to_degree3": len(cubic_domination_failures) == 0,
        "cubic_domination_failures": cubic_domination_failures,
        "proper_subgraph_condition_passes": core_witness is None,
        "proper_three_core_witness": core_witness,
    }


def bfs_layer_profile(graph: nx.Graph, radius: int = 4) -> dict[str, Any]:
    graph = normalize_graph(graph)
    root_profiles: list[dict[str, Any]] = []

    for root in sorted(graph.nodes()):
        distances = nx.single_source_shortest_path_length(graph, root, cutoff=radius)
        layers: dict[int, list[int]] = {
            depth: sorted(int(v) for v, d in distances.items() if d == depth)
            for depth in range(radius + 1)
        }
        layer_sizes = [len(layers[depth]) for depth in range(radius + 1)]
        within_layer_edges = []
        between_layer_edges = []
        for depth in range(radius + 1):
            vertices = set(layers[depth])
            within_layer_edges.append(
                sum(1 for u, v in graph.edges() if u in vertices and v in vertices)
            )
            if depth < radius:
                next_vertices = set(layers[depth + 1])
                between_layer_edges.append(
                    sum(
                        1
                        for u, v in graph.edges()
                        if (u in vertices and v in next_vertices)
                        or (v in vertices and u in next_vertices)
                    )
                )

        layer1 = set(layers.get(1, []))
        layer2 = layers.get(2, [])
        shared_parent_collisions = 0
        for vertex in layer2:
            parent_count = sum(1 for neighbor in graph.neighbors(vertex) if neighbor in layer1)
            if parent_count > 1:
                shared_parent_collisions += parent_count - 1

        root_profiles.append(
            {
                "root": int(root),
                "layer_sizes": layer_sizes,
                "within_layer_edges": within_layer_edges,
                "between_layer_edges": between_layer_edges,
                "layer2_shared_parent_collisions": shared_parent_collisions,
            }
        )

    def values_at(key: str, index: int | None = None) -> list[int]:
        values: list[int] = []
        for profile in root_profiles:
            value = profile[key]
            if index is not None:
                value = value[index]
            values.append(int(value))
        return values

    summary: dict[str, Any] = {"radius": radius, "roots": root_profiles}
    for depth in range(radius + 1):
        values = values_at("layer_sizes", depth)
        summary[f"layer{depth}_min"] = min(values, default=0)
        summary[f"layer{depth}_max"] = max(values, default=0)
        summary[f"layer{depth}_mean"] = sum(values) / len(values) if values else 0.0
    collision_values = values_at("layer2_shared_parent_collisions")
    summary["layer2_collision_total"] = sum(collision_values)
    summary["layer2_collision_max"] = max(collision_values, default=0)
    return summary


def cycle_spectrum(graph: nx.Graph, max_length: int = 12) -> dict[str, int]:
    return {
        str(length): count_cycles_exact(graph, length)
        for length in range(3, min(max_length, graph.number_of_nodes()) + 1)
    }


def power_cycle_profile(graph: nx.Graph, limits: SearchLimits) -> dict[str, Any]:
    graph = normalize_graph(graph)
    profile: dict[str, Any] = {}
    first: dict[str, Any] | None = None
    complete = True
    reason: str | None = None

    for length in powers_of_two_up_to(graph.number_of_nodes()):
        try:
            if length in (4, 8):
                cycles = enumerate_cycles_exact(graph, length, limit=1)
                cycle = cycles[0] if cycles else None
            else:
                cycle = find_cycle_exact_bounded(graph, length, limits)
            profile[str(length)] = {"found": cycle is not None, "cycle": cycle}
            if cycle is not None:
                first = {"length": length, "cycle": cycle}
                break
        except SearchLimitExceeded as exc:
            complete = False
            reason = str(exc)
            profile[str(length)] = {
                "found": None,
                "cycle": None,
                "inconclusive": True,
                "reason": reason,
            }
            break

    return {
        "powers_checked": powers_of_two_up_to(graph.number_of_nodes()),
        "profile": profile,
        "first_power_cycle": first,
        "has_power_two_cycle": first is not None,
        "analysis_complete": complete,
        "incomplete_reason": reason,
    }


def classify_graph(
    graph: nx.Graph,
    source: str,
    limits: SearchLimits,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = normalize_graph(graph)
    degrees = [int(degree) for _, degree in graph.degree()]
    c4_count, c8_count = power_hardness_counts(graph)
    power_profile = power_cycle_profile(graph, limits)
    carr = carr_structure_profile(graph)
    bfs = bfs_layer_profile(graph, radius=4)
    reduced, reduction_steps = reduce_to_critical_three_core(graph)

    record = {
        "source": source,
        "n": graph.number_of_nodes(),
        "m": graph.number_of_edges(),
        "components": nx.number_connected_components(graph),
        "connected": nx.is_connected(graph) if graph.number_of_nodes() else False,
        "min_degree": min(degrees, default=0),
        "max_degree": max(degrees, default=0),
        "degree_sequence": sorted(degrees),
        "density": nx.density(graph),
        "girth": exact_girth(graph),
        "c4_count": c4_count,
        "c8_count": c8_count,
        "cycle_spectrum_3_to_12": cycle_spectrum(graph, 12),
        "power_cycle_profile": power_profile,
        "first_power_cycle": power_profile["first_power_cycle"],
        "carr_2026_profile": carr,
        "bfs_layer_profile": bfs,
        "critical_reduction": {
            "steps": reduction_steps,
            "final_n": reduced.number_of_nodes(),
            "final_m": reduced.number_of_edges(),
            "final_edges": canonical_edges(reduced),
        },
        "edges": canonical_edges(graph),
    }
    if extra:
        record["extra"] = extra
    return record


def numeric_feature_row(record: dict[str, Any]) -> dict[str, Any]:
    carr = record["carr_2026_profile"]
    bfs = record["bfs_layer_profile"]
    first = record.get("first_power_cycle")
    return {
        "source": record["source"],
        "n": record["n"],
        "m": record["m"],
        "girth": record["girth"] if record["girth"] is not None else -1,
        "c4_count": record["c4_count"],
        "c8_count": record["c8_count"],
        "first_power_cycle_length": first["length"] if first else -1,
        "fraction_degree3": carr["fraction_degree3"],
        "high_degree_independent": carr["high_degree_independent"],
        "every_vertex_adjacent_to_degree3": carr["every_vertex_adjacent_to_degree3"],
        "proper_subgraph_condition_passes": carr["proper_subgraph_condition_passes"],
        "critical_reduction_final_n": record["critical_reduction"]["final_n"],
        "layer2_min": bfs["layer2_min"],
        "layer2_max": bfs["layer2_max"],
        "layer3_min": bfs["layer3_min"],
        "layer3_max": bfs["layer3_max"],
        "layer4_min": bfs["layer4_min"],
        "layer4_max": bfs["layer4_max"],
        "layer2_collision_total": bfs["layer2_collision_total"],
    }


def mine_empirical_ranges(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    deep = [r for r in records if r["c4_count"] == 0 and r["c8_count"] == 0]
    c8_near = [
        r
        for r in records
        if r["c4_count"] == 0 and r["c8_count"] > 0
    ]
    rows = [numeric_feature_row(record) for record in deep]
    numeric_keys = [
        "girth",
        "fraction_degree3",
        "critical_reduction_final_n",
        "layer2_min",
        "layer2_max",
        "layer3_min",
        "layer3_max",
        "layer4_min",
        "layer4_max",
        "layer2_collision_total",
    ]
    ranges: dict[str, Any] = {}
    for key in numeric_keys:
        values = [float(row[key]) for row in rows]
        if values:
            ranges[key] = {"min": min(values), "max": max(values)}

    boolean_keys = [
        "high_degree_independent",
        "every_vertex_adjacent_to_degree3",
        "proper_subgraph_condition_passes",
    ]
    invariants: dict[str, Any] = {}
    for key in boolean_keys:
        values = [bool(row[key]) for row in rows]
        if values and all(values):
            invariants[key] = True
        elif values and not any(values):
            invariants[key] = False
        else:
            invariants[key] = "mixed"

    return {
        "deep_hard_count": len(deep),
        "no_c4_yes_c8_count": len(c8_near),
        "deep_hard_numeric_ranges": ranges,
        "deep_hard_boolean_invariants": invariants,
        "warning": (
            "These are empirical ranges in generated finite graphs, not theorems. "
            "Every candidate rule must be attacked by new graph families and formal proof."
        ),
    }


def lemma_inventory(empirical: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "L1_C4_BRANCH_DISJOINTNESS",
            "kind": "exact_structural_lemma",
            "statement": (
                "In a C4-free graph, two distinct neighbors of a root cannot share "
                "a second neighbor outside the root."
            ),
            "use": "Formalize in Lean and use as the first BFS-layer constraint.",
            "status": "ready_for_formalization",
        },
        {
            "id": "L2_C8_ROOTED_PATH_EXCLUSION",
            "kind": "exact_structural_lemma",
            "statement": (
                "If a graph has no C8, then for any root there is no simple path of "
                "length 6 between two distinct neighbors of the root that avoids the root."
            ),
            "use": "Encode as a rooted SAT/local-search constraint.",
            "status": "ready_for_formalization",
        },
        {
            "id": "L3_CRITICAL_3CORE_REDUCTION",
            "kind": "exact_reduction_lemma",
            "statement": (
                "A minimal counterexample has no proper subgraph with minimum degree at "
                "least 3. Therefore any candidate with a proper nonempty 3-core can be "
                "reduced before further analysis."
            ),
            "use": "Formalize the reduction and search only deletion-critical 3-cores.",
            "status": "ready_for_formalization",
        },
        {
            "id": "L4_EMPIRICAL_LAYER_RANGES",
            "kind": "data_mined_candidate_family",
            "statement": empirical.get("deep_hard_numeric_ranges", {}),
            "use": (
                "Use the observed ranges to propose threshold lemmas, then immediately "
                "search for counterexamples outside the training graph families."
            ),
            "status": "empirical_only",
        },
    ]


def regression_power_hard_n30() -> nx.Graph:
    """A deterministic cubic graph with no C4 or C8 and an explicit C16.

    This graph was produced by the same degree-preserving two-switch optimizer
    and is retained as a regression fixture so every installation can test the
    deeper C16 path even when a short stochastic run stops at score 1.
    """
    edges = [
        [0, 9], [0, 18], [0, 21], [1, 2], [1, 6], [1, 27], [2, 21], [2, 25],
        [3, 5], [3, 13], [3, 20], [4, 10], [4, 12], [4, 21], [5, 9], [5, 22],
        [6, 18], [6, 20], [7, 8], [7, 23], [7, 26], [8, 12], [8, 17], [9, 12],
        [10, 23], [10, 28], [11, 13], [11, 19], [11, 24], [13, 27], [14, 19],
        [14, 22], [14, 29], [15, 16], [15, 17], [15, 27], [16, 17], [16, 24],
        [18, 20], [19, 22], [23, 28], [24, 26], [25, 28], [25, 29], [26, 29],
    ]
    return graph_from_edges(30, edges)


def named_graphs() -> Iterator[tuple[str, nx.Graph]]:
    yield "regression_power_hard_n30", regression_power_hard_n30()
    factories = [
        ("petersen_graph", nx.petersen_graph),
        ("heawood_graph", nx.heawood_graph),
        ("pappus_graph", nx.pappus_graph),
        ("desargues_graph", nx.desargues_graph),
        ("moebius_kantor_graph", nx.moebius_kantor_graph),
        ("dodecahedral_graph", nx.dodecahedral_graph),
        ("frucht_graph", nx.frucht_graph),
        ("tutte_graph", nx.tutte_graph),
    ]
    for name, factory in factories:
        yield name, normalize_graph(factory())


def load_prior_summary(path: Path) -> list[tuple[int, nx.Graph, dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(path)

    text = path.read_text(encoding="utf-8-sig").strip()
    if path.suffix.lower() == ".jsonl":
        raw_items = [json.loads(line) for line in text.splitlines() if line.strip()]
        items = []
        for item in raw_items:
            first = item.get("power_cycle_profile", {}).get("first_power_cycle")
            if first is None:
                first = item.get("first_power_cycle")
            if first is not None and int(first.get("length", 0)) > 4:
                items.append(item)
    else:
        obj = json.loads(text)
        items = obj.get("top_near_misses", []) if isinstance(obj, dict) else []

    records: list[tuple[int, nx.Graph, dict[str, Any]]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or "n" not in item or "edges" not in item:
            continue
        graph = graph_from_edges(int(item["n"]), item["edges"])
        records.append((index, graph, item))
    return records


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def write_csv(path: Path, records: Sequence[dict[str, Any]]) -> None:
    rows = [numeric_feature_row(record) for record in records]
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    path: Path,
    records: Sequence[dict[str, Any]],
    empirical: dict[str, Any],
    lemmas: Sequence[dict[str, Any]],
) -> None:
    deep = [r for r in records if r["c4_count"] == 0 and r["c8_count"] == 0]
    c8_near = [r for r in records if r["c4_count"] == 0 and r["c8_count"] > 0]
    critical = [
        r
        for r in records
        if r["carr_2026_profile"]["proper_subgraph_condition_passes"]
    ]
    lines = [
        "# Erdos-Gyarfas Lemma Miner Report",
        "",
        "## Honest status",
        "",
        "This is finite-world proof assistance. It is not a universal proof.",
        "",
        "## Run summary",
        "",
        f"- Graphs classified: {len(records)}",
        f"- No C4 but yes C8: {len(c8_near)}",
        f"- No C4 and no C8: {len(deep)}",
        f"- Passed exact proper-subgraph 3-core condition: {len(critical)}",
        "",
        "## Deepest generated candidates",
        "",
    ]
    for index, record in enumerate(deep):
        first = record.get("first_power_cycle")
        first_text = f"C{first['length']}" if first else "none found within limits"
        lines.extend(
            [
                f"### Candidate {index}",
                "",
                f"- Source: {record['source']}",
                f"- n={record['n']}, m={record['m']}",
                f"- girth={record['girth']}",
                f"- first power-of-two cycle: {first_text}",
                f"- critical 3-core condition: {record['carr_2026_profile']['proper_subgraph_condition_passes']}",
                f"- reduced critical core order: {record['critical_reduction']['final_n']}",
                "",
            ]
        )

    lines.extend(["## Candidate lemma inventory", ""])
    for lemma in lemmas:
        lines.extend(
            [
                f"### {lemma['id']}",
                "",
                f"- Kind: {lemma['kind']}",
                f"- Status: {lemma['status']}",
                f"- Statement: {lemma['statement']}",
                f"- Next use: {lemma['use']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation",
            "",
            "The central progress marker is not the number of random graphs tested. It is whether the system finds a new exact structural lemma that survives adversarial graph generation and can be proved in Lean.",
            "",
            "The next formal targets are L1, L2, and L3. The next computational target is a deletion-critical 3-core with no C4 and no C8, followed by an attempt to eliminate C16.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def compatible_lean_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    witnesses = []
    ordered = sorted(
        records,
        key=lambda record: (
            (record.get("first_power_cycle") or {}).get("length", -1),
            -record.get("c4_count", 0),
            -record.get("c8_count", 0),
        ),
        reverse=True,
    )
    for record in ordered:
        first = record.get("first_power_cycle")
        if first is None:
            continue
        witnesses.append(
            {
                "source": record["source"],
                "n": record["n"],
                "m": record["m"],
                "min_degree": record["min_degree"],
                "first_power_cycle": first,
                "edges": record["edges"],
            }
        )
    return {
        "top_near_misses": witnesses,
        "honest_status": (
            "Concrete finite graph witnesses only. Use json_to_lean.py to create "
            "kernel-checked Lean certificates."
        ),
    }


def parse_n_values(text: str) -> list[int]:
    values = []
    for part in text.split(","):
        value = int(part.strip())
        if value < 4 or value % 2 != 0:
            raise argparse.ArgumentTypeError("All n values must be even and at least 4")
        values.append(value)
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("eg_lemma_miner_run"))
    parser.add_argument("--n-values", type=parse_n_values, default=[30, 32, 36])
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--steps", type=int, default=15_000)
    parser.add_argument("--controls", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20_260_623)
    parser.add_argument("--objective", choices=["power", "girth"], default="power")
    parser.add_argument("--cycle-timeout", type=float, default=10.0)
    parser.add_argument("--max-dfs-states", type=int, default=2_000_000)
    parser.add_argument("--prior-summary", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    limits = SearchLimits(args.cycle_timeout, args.max_dfs_states)
    records: list[dict[str, Any]] = []

    print("Erdos-Gyarfas Lemma Miner v1")
    print("Finite-world only. No universal proof claimed.")
    print(f"Output directory: {args.out_dir.resolve()}")

    for name, graph in named_graphs():
        print(f"[named] {name}: n={graph.number_of_nodes()}")
        records.append(classify_graph(graph, f"named:{name}", limits))

    if args.prior_summary is not None:
        for index, graph, item in load_prior_summary(args.prior_summary):
            print(f"[prior] index={index}: n={graph.number_of_nodes()}")
            records.append(
                classify_graph(
                    graph,
                    source=f"prior_summary:{index}",
                    limits=limits,
                    extra={"original": item},
                )
            )

    for control_index in range(args.controls):
        n = args.n_values[control_index % len(args.n_values)]
        seed = args.seed + 100_000 + control_index
        graph = random_cubic_graph(n, seed)
        records.append(
            classify_graph(
                graph,
                source="random_cubic_control",
                limits=limits,
                extra={"control_index": control_index, "seed": seed},
            )
        )

    for n in args.n_values:
        best: OptimizationResult | None = None
        for restart in range(args.restarts):
            seed = args.seed + n * 10_000 + restart
            print(
                f"[optimize] n={n} restart={restart + 1}/{args.restarts} "
                f"steps={args.steps} objective={args.objective}"
            )
            result = optimize_cubic_graph(
                n=n,
                seed=seed,
                steps=args.steps,
                restart=restart,
                objective=args.objective,
            )
            print(
                f"  score {result.initial_score} -> {result.final_score} "
                f"in {result.steps_used} steps"
            )
            if best is None or result.final_score < best.final_score:
                best = result
            if args.objective == "power" and result.final_score == 0:
                break

        assert best is not None
        records.append(
            classify_graph(
                best.graph,
                source="optimized_cubic_power_hard",
                limits=limits,
                extra={
                    "n_target": n,
                    "seed": best.seed,
                    "restart": best.restart,
                    "initial_score": best.initial_score,
                    "final_score": best.final_score,
                    "steps_used": best.steps_used,
                    "objective": args.objective,
                },
            )
        )

    empirical = mine_empirical_ranges(records)
    lemmas = lemma_inventory(empirical)

    write_jsonl(args.out_dir / "graph_records.jsonl", records)
    write_json(args.out_dir / "graph_records.json", records)
    write_csv(args.out_dir / "graph_profiles.csv", records)
    write_json(args.out_dir / "empirical_patterns.json", empirical)
    write_json(args.out_dir / "lemma_candidates.json", lemmas)
    write_json(args.out_dir / "lean_witnesses_summary.json", compatible_lean_summary(records))
    write_report(args.out_dir / "REPORT.md", records, empirical, lemmas)

    deep = [record for record in records if record["c4_count"] == 0 and record["c8_count"] == 0]
    print("\nRun complete")
    print(f"Graphs classified: {len(records)}")
    print(f"No C4 and no C8: {len(deep)}")
    for index, record in enumerate(deep):
        first = record.get("first_power_cycle")
        label = f"C{first['length']}" if first else "none/inconclusive"
        print(
            f"  deep[{index}] n={record['n']} m={record['m']} "
            f"girth={record['girth']} first_power={label}"
        )
    print(f"Report: {(args.out_dir / 'REPORT.md').resolve()}")
    print(f"Lean input: {(args.out_dir / 'lean_witnesses_summary.json').resolve()}")


if __name__ == "__main__":
    main()
