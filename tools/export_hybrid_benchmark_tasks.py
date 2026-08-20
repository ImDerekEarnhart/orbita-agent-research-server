"""Export seeded, gold-separated tasks for the Guided hybrid benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.orbita_capability_map_v1.run_capability_map import build_hidden_suite


def expected_states(gold: dict[str, Any]) -> dict[str, str]:
    states: dict[str, str] = {}
    for kind in ("claims", "actions", "discoveries"):
        for identifier, expected in gold.get(kind, {}).items():
            states[identifier] = expected["final_state"]
    return states


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=91731)
    parser.add_argument("--tasks-per-category", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}")

    suite = build_hidden_suite(args.seed, tasks_per_category=args.tasks_per_category)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        for specification in suite.tasks:
            public = specification.public_dict()
            prompt = (
                "Adjudicate every target in the structured task below. Use an Orbita adjudication tool if one is "
                "available; otherwise reason from the public evidence only. Gold labels are not supplied. End with "
                'exactly one line in the form FINAL_JSON: {"target_id":"final_state"}. Do not put possible final '
                "states in that object; choose one state for every target.\n\nSTRUCTURED_TASK:\n"
                + json.dumps(public, sort_keys=True, separators=(",", ":"))
            )
            row = {
                "id": public["id"],
                "category": public["category"],
                "prompt": prompt,
                "orbita_preprocess": "adjudicate",
                "orbita_task": public,
                "grader": {"type": "target_states", "states": expected_states(specification.gold)},
            }
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"Wrote {len(suite.tasks)} hidden tasks to {args.output}")


if __name__ == "__main__":
    main()
