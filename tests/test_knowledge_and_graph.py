from __future__ import annotations

from pathlib import Path

from orbita_agent import eg_lemma_miner as miner


def test_curated_knowledge_and_eg_receipts(gateway):
    hits = gateway.search_knowledge("edge connectivity", limit=5)
    assert hits
    assert all("source_path" in hit and "snippet" in hit for hit in hits)
    cards = gateway.knowledge_claims(status="PROVED_ON_PAPER")
    assert any(card["claim_id"] == "GRAPH-G5" for card in cards)

    summary = gateway.eg_summary()
    assert summary["fast_run_totals"]["records"] == 482_754
    assert summary["fast_run_totals"]["counterexamples"] == 0
    assert summary["unique_exact_labeled_fast_near_misses"] == 268
    near = gateway.eg_near_misses(limit=3, min_n=20, include_certificate=True)
    assert near and all("edges" in item for item in near)


def test_bounded_graph_analysis_and_lean_export(gateway):
    graph = miner.regression_power_hard_n30()
    edges = [[min(u, v), max(u, v)] for u, v in sorted(graph.edges())]
    result = gateway.analyze_graph(n=30, edges=edges, timeout_seconds=2.0, max_states=500_000)
    witness = result["power_cycle_search"]["first_power_cycle"]
    assert witness["length"] == 16
    exported = gateway.export_lean_witness(n=30, edges=edges, cycle=witness["cycle"])
    path = Path(exported["path"])
    assert path.exists()
    assert (Path(exported["project_path"]) / "ErdosGyarfas" / "Certificate.lean").exists()
    assert exported["verification_command"] == "lake build"
    source = path.read_text(encoding="utf-8")
    assert "generatedCertificate_is_valid" in source
    assert "native_decide" in source
    assert "sorry" not in source
