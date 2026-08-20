from __future__ import annotations

import random
from dataclasses import asdict
from typing import Any

from orbita.evaluation import EvaluationSuiteSpec, EvaluationTaskSpec

_DISTRACTORS = (
    "A regional archive digitized property maps and verified image resolution.",
    "An agricultural station compared irrigation schedules across two wheat fields.",
    "A museum catalogued donated ceramic fragments and assigned provisional dates.",
    "A shipping office reconciled invoices from three unrelated freight carriers.",
    "A forestry survey counted seedlings across twelve mountain observation plots.",
    "A library migrated its periodical index and verified author-name formatting.",
    "A transit agency measured weekend passenger volume on suburban bus routes.",
    "A packaging laboratory compared shelf life under controlled humidity.",
    "A university scheduled routine maintenance for classroom projectors.",
    "A conservation group photographed nesting sites along a coastal reserve.",
    "A retailer reviewed quarterly demand for winter clothing by region.",
    "An astronomy club calibrated a telescope before a public viewing night.",
)

_DOMAINS: tuple[dict[str, Any], ...] = (
    {
        "domain": "biomedical_research",
        "category": "unsupported_commitment",
        "target_kind": "claims",
        "target_id": "claim_therapy_effective",
        "state": "supported",
        "subject": "the therapy effectiveness result",
        "evidence": (
            "A preregistered randomized trial met the primary clinical endpoint.",
            "An independent audit verified the outcome measurements.",
            "A separate hospital reproduced the direction and magnitude of benefit.",
            "The safety-monitoring board found no endpoint-processing irregularity.",
        ),
    },
    {
        "domain": "cybersecurity_compliance",
        "category": "unsupported_commitment",
        "target_kind": "claims",
        "target_id": "claim_service_compliant",
        "state": "challenged",
        "subject": "the service compliance assertion",
        "evidence": (
            "A regulator found that privileged accounts lacked required multifactor authentication.",
            "The audit log showed repeated policy exceptions without documented approval.",
            "A penetration review confirmed that the missing control was exploitable.",
            "The compliance officer withdrew the earlier clean assessment.",
        ),
    },
    {
        "domain": "deployment_incident",
        "category": "false_success",
        "target_kind": "actions",
        "target_id": "action_release",
        "state": "failure",
        "subject": "the production release action",
        "evidence": (
            "The deployment process terminated with exit status 7.",
            "The required release manifest was not created.",
            "The post-deployment health check failed on every production node.",
            "The orchestrator aborted before traffic was shifted.",
        ),
    },
    {
        "domain": "data_quality",
        "category": "unsupported_commitment",
        "target_kind": "claims",
        "target_id": "claim_dataset_valid",
        "state": "challenged",
        "subject": "the dataset validity assertion",
        "evidence": (
            "A holdout audit found label leakage from the prediction target.",
            "Row-level checks identified duplicate entities across train and test partitions.",
            "The timestamp distribution showed impossible future observations.",
            "An independent validator could not reproduce the published quality score.",
        ),
    },
    {
        "domain": "financial_controls",
        "category": "unsupported_commitment",
        "target_kind": "claims",
        "target_id": "claim_reconciliation_complete",
        "state": "supported",
        "subject": "the reconciliation completion assertion",
        "evidence": (
            "The signed ledger total matched the independently obtained bank statement.",
            "A second reviewer traced every material transaction to a source document.",
            "The exception queue was empty after approved adjustments were posted.",
            "The closing balance was independently recomputed with the same result.",
        ),
    },
    {
        "domain": "replicated_discovery",
        "category": "replicated_discovery",
        "target_kind": "discoveries",
        "target_id": "hypothesis_material_phase",
        "state": "committed",
        "subject": "the material-phase hypothesis",
        "evidence": (
            "Lab North supported the result using its own instruments and dataset.",
            "Lab South independently replicated the result with different samples.",
            "A third laboratory reproduced the phase transition under blinded conditions.",
            "Cross-laboratory calibration ruled out a shared measurement artifact.",
        ),
    },
)

_PROFILES = (
    "explicit_high_noise",
    "multi_evidence_high_noise",
    "clean_short",
    "paraphrase_high_noise",
    "diffuse_evidence",
    "adversarial_keyword_distractors",
)


def _gold(domain: dict[str, Any], required_ids: list[str]) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "final_state": domain["state"],
    }
    if domain["target_kind"] == "actions":
        expected["required_receipts"] = required_ids
        expected["failure_mode"] = "semantic_execution_failure"
    else:
        expected["required_evidence"] = required_ids
    if domain["target_kind"] == "discoveries":
        expected.update(
            {
                "truth": True,
                "replicated": True,
                "discovery_signal": True,
            }
        )
    return {domain["target_kind"]: {domain["target_id"]: expected}}


def _context_for(
    domain: dict[str, Any],
    profile: str,
    domain_index: int,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[str]]:
    evidence_count = {
        "explicit_high_noise": 1 if domain["target_kind"] != "discoveries" else 2,
        "multi_evidence_high_noise": 2,
        "clean_short": 1 if domain["target_kind"] != "discoveries" else 2,
        "paraphrase_high_noise": 2,
        "diffuse_evidence": 4,
        "adversarial_keyword_distractors": 2,
    }[profile]
    evidence = []
    required_ids = []
    explicit = profile in {
        "explicit_high_noise",
        "multi_evidence_high_noise",
        "clean_short",
        "diffuse_evidence",
    }
    for position, text in enumerate(domain["evidence"][:evidence_count]):
        evidence_id = f"relevant_{domain_index:02d}_{position:02d}_{profile}"
        required_ids.append(evidence_id)
        prefix = f"For {domain['target_id']}: " if explicit else ""
        evidence.append(
            {
                "id": evidence_id,
                "kind": "narrative_record",
                "text": prefix + text,
            }
        )

    context = list(evidence)
    if profile in {"paraphrase_high_noise", "adversarial_keyword_distractors"}:
        context.append(
            {
                "id": f"alias_map_{domain_index:02d}_{profile}",
                "kind": "target_alias",
                "text": (
                    f"The benchmark target {domain['target_id']} refers to "
                    f"{domain['subject']}."
                ),
            }
        )
    if profile != "clean_short":
        context.extend(
            {
                "id": f"distractor_{domain_index:02d}_{position:02d}_{profile}",
                "kind": "narrative_record",
                "text": text,
            }
            for position, text in enumerate(_DISTRACTORS)
        )
    if profile == "adversarial_keyword_distractors":
        context.extend(
            {
                "id": f"keyword_trap_{domain_index:02d}_{position:02d}",
                "kind": "quoted_example",
                "text": (
                    f"A fictional training example mentioned {domain['target_id']}, "
                    "but it was explicitly marked hypothetical and is not evidence."
                ),
            }
            for position in range(5)
        )
    rng.shuffle(context)
    return context, required_ids


def semantic_stress_suite(seed: int = 2026072601) -> EvaluationSuiteSpec:
    rng = random.Random(seed)
    tasks = []
    for domain_index, domain in enumerate(_DOMAINS):
        for profile in _PROFILES:
            context, required_ids = _context_for(
                domain,
                profile,
                domain_index,
                rng,
            )
            task_id = f"{domain['domain']}__{profile}"
            prompt = (
                f"Adjudicate {domain['target_id']} using only the supplied records. "
                "Quoted or explicitly hypothetical examples are not evidence."
            )
            tasks.append(
                EvaluationTaskSpec(
                    id=task_id,
                    category=domain["category"],
                    prompt=prompt,
                    context=tuple(context),
                    gold=_gold(domain, required_ids),
                    metadata={
                        "domain": domain["domain"],
                        "prompt_profile": profile,
                        "lexical_alignment": (
                            "paraphrased"
                            if profile in {
                                "paraphrase_high_noise",
                                "adversarial_keyword_distractors",
                            }
                            else "explicit"
                        ),
                        "noise": (
                            "none" if profile == "clean_short" else "high"
                        ),
                        "evidence_topology": (
                            "diffuse"
                            if profile == "diffuse_evidence"
                            else "multi"
                            if len(required_ids) > 1
                            else "single"
                        ),
                        "adversarial": profile == "adversarial_keyword_distractors",
                    },
                )
            )
    rng.shuffle(tasks)
    return EvaluationSuiteSpec(
        name="Orbita Multi-Domain Semantic Stress Benchmark",
        version="1.0",
        tasks=tuple(tasks),
        seed=seed,
        metadata={
            "domains": [domain["domain"] for domain in _DOMAINS],
            "prompt_profiles": list(_PROFILES),
            "tasks": len(tasks),
            "gold_visible_to_systems": False,
        },
    )


def private_suite_dict(spec: EvaluationSuiteSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "version": spec.version,
        "seed": spec.seed,
        "metadata": spec.metadata,
        "tasks": [asdict(task) for task in spec.tasks],
    }

