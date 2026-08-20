from orbita.evaluation import ComparativeEvaluationRuntime, default_adversarial_suite
from orbita.ledger import EpistemicLedger


def test_public_tasks_expose_target_ids_without_leaking_gold() -> None:
    suite = default_adversarial_suite()

    public_tasks = [task.public_dict() for task in suite.tasks]

    assert public_tasks[0]["targets"]["claims"] == ["c_marker_recovery"]
    assert public_tasks[0]["targets"]["actions"] == []
    assert public_tasks[0]["targets"]["discoveries"] == []
    assert "gold" not in public_tasks[0]
    assert "final_state" not in repr(public_tasks)


def test_public_export_includes_targets_without_gold(tmp_path) -> None:
    with EpistemicLedger(tmp_path / "ledger.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, tmp_path / "workspace")
        suite = runtime.create_suite(default_adversarial_suite())

        exported = runtime.export_public_suite(suite["id"])

    assert exported["tasks"][0]["targets"]["claims"] == ["c_marker_recovery"]
    assert "gold" not in exported["tasks"][0]
    assert "final_state" not in repr(exported)


def test_provisional_and_rejected_discoveries_count_as_correct_states(tmp_path) -> None:
    with EpistemicLedger(tmp_path / "ledger.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, tmp_path / "workspace")
        suite = runtime.create_suite(default_adversarial_suite())
        task = next(item for item in suite["tasks"] if item["id"] == "holdout_without_replication")
        score = runtime._score_task(
            task,
            {
                "discovery_judgments": [
                    {"hypothesis_id": "h_holdout", "state": "provisional", "evidence_ids": []},
                    {"hypothesis_id": "h_noise", "state": "rejected", "evidence_ids": []},
                ]
            },
        )

    assert score["rates"]["adjudication_accuracy"] == 1.0
    assert score["task_score"] == 1.0
