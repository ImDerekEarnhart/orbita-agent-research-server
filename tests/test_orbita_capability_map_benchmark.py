from __future__ import annotations

from benchmarks.orbita_capability_map_v1.run_capability_map import (
    _code_tasks,
    _counterfactual_pairs,
    run_benchmark,
)


def test_counterfactual_partition_is_balanced_and_gold_is_separate() -> None:
    pairs = _counterfactual_pairs(3)

    assert len(pairs) == 21
    assert {pair["profile"] for pair in pairs} == {
        "weak_to_supported",
        "refutation_removed",
        "premise_restored",
        "alternate_proof_revoked",
        "exit_code_flip",
        "independence_removed",
        "observation_invalidated",
    }
    assert all("gold" not in pair["before"] for pair in pairs)
    assert all("gold" not in pair["after"] for pair in pairs)


def test_code_context_partition_covers_positive_and_negative_profiles() -> None:
    tasks = _code_tasks(2)

    assert len(tasks) == 10
    assert {task["profile"] for task in tasks} == {
        "explicit_symbol",
        "explicit_filename",
        "paraphrased_alias",
        "hidden_dependency",
        "adversarial_path_distractor",
    }


def test_small_capability_map_smoke_run() -> None:
    result = run_benchmark(
        seed=91,
        tasks_per_category=2,
        determinism_repeats=1,
        permutation_repeats=1,
        scale_repeats=2,
    )

    assert result["model_calls"] == 0
    assert result["network_calls"] == 0
    assert result["base_tasks"] == 14
    assert result["measurements"]["state_policy_fidelity"]["accuracy"] == 1.0
    assert result["measurements"]["coverage_routing"]["balanced_accuracy"] == 1.0
    assert result["measurements"]["audit_integrity"]["rate"] == 1.0
