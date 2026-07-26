from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

from orbita_agent.adjudication import compress_epistemic_task

_SUITE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "orbita_capability_v1"
    / "semantic_stress_suite.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "orbita_capability_semantic_suite",
    _SUITE_PATH,
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
semantic_stress_suite = _MODULE.semantic_stress_suite


def _required_ids(task) -> set[str]:
    required = set()
    for kind in ("claims", "actions", "discoveries"):
        for expected in task.gold.get(kind, {}).values():
            for field in ("required_evidence", "required_receipts", "required_derivations"):
                required.update(expected.get(field, []))
    return required


def test_capability_suite_is_balanced_and_gold_free_publicly() -> None:
    suite = semantic_stress_suite()
    public = [task.public_dict() for task in suite.tasks]

    assert len(suite.tasks) == 36
    assert set(Counter(task.metadata["domain"] for task in suite.tasks).values()) == {6}
    assert set(
        Counter(task.metadata["prompt_profile"] for task in suite.tasks).values()
    ) == {6}
    assert "gold" not in repr(public)
    assert "final_state" not in repr(public)


def test_capability_suite_contains_positive_and_negative_compression_controls() -> None:
    suite = semantic_stress_suite()
    recall: dict[str, list[bool]] = {}
    for task in suite.tasks:
        compressed = compress_epistemic_task(task.public_dict(), max_context_items=3)
        required = _required_ids(task)
        retained = set(compressed["receipt"]["retained_ids"])
        recall.setdefault(task.metadata["prompt_profile"], []).append(
            required.issubset(retained)
        )

    assert all(recall["explicit_high_noise"])
    assert all(recall["multi_evidence_high_noise"])
    assert all(recall["clean_short"])
    assert not any(recall["adversarial_keyword_distractors"])
    assert not all(recall["paraphrase_high_noise"])
    assert not all(recall["diffuse_evidence"])
