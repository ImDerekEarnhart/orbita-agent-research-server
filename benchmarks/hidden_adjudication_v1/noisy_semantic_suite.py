from __future__ import annotations

import random
from dataclasses import replace

from semantic_suite import semantic_holdout_tasks

from orbita.evaluation import EvaluationSuiteSpec

_DISTRACTORS = (
    (
        "A municipal archive finished digitizing nineteenth-century property maps. "
        "The work was reviewed for image quality."
    ),
    (
        "An agricultural team compared irrigation schedules for two wheat fields. "
        "Soil moisture remained within its expected range."
    ),
    (
        "A museum catalogued a donated collection of ceramic fragments. "
        "Curators assigned provisional dates to several pieces."
    ),
    "A shipping office reconciled invoices from three regional carriers. Two duplicate billing entries were corrected.",
    "A forestry survey counted seedlings across twelve mountain plots. The report focused on seasonal growth rates.",
    "A library migrated its periodical index to a new database. Staff verified author and publication fields.",
    "A transit agency measured passenger volume on weekend bus routes. Ridership varied with local sporting events.",
    "A food laboratory compared the shelf life of four packaging films. Humidity was controlled throughout storage.",
    "A university scheduled maintenance for classroom projectors. Replacement lamps were ordered for six buildings.",
    (
        "A conservation group photographed nesting sites along a coastal reserve. "
        "Location coordinates were stored separately."
    ),
    "A retailer reviewed quarterly demand for winter clothing. Inventory planning used regional sales totals.",
    (
        "An astronomy club calibrated a small telescope before its public viewing night. "
        "Cloud cover limited the final session."
    ),
    "A city garden tested compost mixtures in ornamental flower beds. Color and growth were recorded weekly.",
    (
        "A records office standardized formatting across historical census tables. "
        "Original scans were preserved unchanged."
    ),
    (
        "A bicycle workshop compared wear across several tire brands. "
        "All measurements concerned routine commuting conditions."
    ),
    (
        "A language department archived recordings from a pronunciation workshop. "
        "Participant names were replaced with codes."
    ),
)


def noisy_semantic_suite(seed: int = 20260726) -> EvaluationSuiteSpec:
    rng = random.Random(seed)
    tasks = []
    for task_index, task in enumerate(semantic_holdout_tasks()):
        context = list(task.context)
        context.extend(
            {
                "id": f"distractor_{task_index:02d}_{position:02d}",
                "kind": "narrative_record",
                "text": text,
            }
            for position, text in enumerate(_DISTRACTORS)
        )
        rng.shuffle(context)
        tasks.append(replace(task, context=tuple(context)))
    return EvaluationSuiteSpec(
        name="Orbita Noisy Semantic Compression Benchmark",
        version="1.0",
        tasks=tuple(tasks),
        seed=seed,
        metadata={
            "semantic_tasks": len(tasks),
            "distractors_per_task": len(_DISTRACTORS),
            "gold_visible_to_systems": False,
        },
    )
