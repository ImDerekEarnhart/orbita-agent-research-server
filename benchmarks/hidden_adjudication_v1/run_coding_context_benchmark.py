#!/usr/bin/env python3
"""Compare GPT coding fixes from full and Orbita-selected code context."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from coding_suite import coding_tasks
from run_gpt_router_pilot import _EMPIRICAL

from orbita_agent.code_context import compress_code_context

PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["target_path", "replacement_content", "summary"],
    "properties": {
        "target_path": {"type": "string"},
        "replacement_content": {"type": "string"},
        "summary": {"type": "string"},
    },
}

INSTRUCTIONS = """Fix the reported software bug using only the supplied files.
Return the complete replacement content for exactly one non-test source file.
Do not modify tests. Preserve the source file's public function names and interface.
The replacement must be plain source content without Markdown fences."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--max-files", type=int, default=2)
    return parser.parse_args()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _openai_patch(
    task: dict[str, Any],
    files: list[dict[str, str]],
    model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    started = time.perf_counter()
    response = _EMPIRICAL._post_json(
        "https://api.openai.com/v1/responses",
        {"Authorization": f"Bearer {key}"},
        {
            "model": model,
            "instructions": INSTRUCTIONS,
            "input": json.dumps(
                {"issue": task["issue"], "files": files},
                sort_keys=True,
            ),
            "max_output_tokens": 2200,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "orbita_code_patch",
                    "strict": True,
                    "schema": PATCH_SCHEMA,
                }
            },
        },
    )
    texts = [
        content["text"]
        for item in response.get("output", [])
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    ]
    if not texts:
        raise RuntimeError(f"OpenAI response {response.get('id')} contained no output_text")
    return json.loads("".join(texts)), {
        "response_id": response.get("id"),
        "model": response.get("model", model),
        "usage": response.get("usage", {}),
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


def _materialize(files: list[dict[str, str]], workspace: Path) -> None:
    for item in files:
        path = workspace / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")


def _verify_original(task: dict[str, Any], workspace: Path) -> dict[str, Any]:
    _materialize(task["files"], workspace)
    return _run_tests(workspace)


def _verify_patch(
    task: dict[str, Any],
    patch: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    _materialize(task["files"], workspace)
    target_path = str(patch.get("target_path", ""))
    valid_target = (
        target_path == task["target_path"]
        and not target_path.startswith(("test", "tests/"))
    )
    if not valid_target:
        return {
            "success": False,
            "valid_target": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"invalid target path: {target_path}",
        }
    content = patch.get("replacement_content")
    if not isinstance(content, str) or not content.strip():
        return {
            "success": False,
            "valid_target": True,
            "exit_code": None,
            "stdout": "",
            "stderr": "replacement_content is empty",
        }
    target = workspace / target_path
    target.write_text(content, encoding="utf-8")
    receipt = _run_tests(workspace)
    receipt["valid_target"] = True
    receipt["replacement_sha256"] = hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()
    return receipt


def _run_tests(workspace: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(workspace)
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return {
        "success": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


def _usage(receipts: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(int(receipt["usage"].get(key) or 0) for receipt in receipts)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)
    tasks = coding_tasks()
    compressed = [
        compress_code_context(
            task["issue"],
            task["files"],
            max_files=args.max_files,
        )
        for task in tasks
    ]
    for task, compact in zip(tasks, compressed, strict=True):
        required = {task["target_path"], f"tests/test_{Path(task['target_path']).stem}.py"}
        if not required.issubset(set(compact["receipt"]["retained_paths"])):
            raise RuntimeError(
                f"Code selector missed required files for {task['id']}: "
                f"{compact['receipt']['retained_paths']}"
            )

    original_receipts = {}
    full_patches = {}
    compact_patches = {}
    full_model_receipts = []
    compact_model_receipts = []
    verification = {"full": {}, "compact": {}}
    for task, compact in zip(tasks, compressed, strict=True):
        original_receipts[task["id"]] = _verify_original(
            task,
            out / "verification" / "original" / task["id"],
        )
        if original_receipts[task["id"]]["success"]:
            raise RuntimeError(f"Original task unexpectedly passes: {task['id']}")

        full_patch, full_receipt = _openai_patch(task, task["files"], args.model)
        compact_patch, compact_receipt = _openai_patch(
            task,
            compact["files"],
            args.model,
        )
        full_patches[task["id"]] = full_patch
        compact_patches[task["id"]] = compact_patch
        full_model_receipts.append({"task_id": task["id"], **full_receipt})
        compact_model_receipts.append({"task_id": task["id"], **compact_receipt})
        verification["full"][task["id"]] = _verify_patch(
            task,
            full_patch,
            out / "verification" / "full" / task["id"],
        )
        verification["compact"][task["id"]] = _verify_patch(
            task,
            compact_patch,
            out / "verification" / "compact" / task["id"],
        )
        print(
            f"{task['id']}: full={verification['full'][task['id']]['success']} "
            f"compact={verification['compact'][task['id']]['success']}",
            flush=True,
        )

    full_usage = _usage(full_model_receipts)
    compact_usage = _usage(compact_model_receipts)
    full_total = full_usage["total_tokens"]
    compact_total = compact_usage["total_tokens"]
    summary = {
        "tasks": len(tasks),
        "gpt_calls_executed": len(tasks) * 2,
        "compression": {
            "original_files": sum(item["receipt"]["original_files"] for item in compressed),
            "retained_files": sum(item["receipt"]["retained_files"] for item in compressed),
            "mean_character_reduction": (
                sum(item["receipt"]["character_reduction"] for item in compressed)
                / len(compressed)
            ),
            "required_file_recall": 1.0,
        },
        "full_context": {
            "passing_patches": sum(
                receipt["success"] for receipt in verification["full"].values()
            ),
            "usage": full_usage,
            "model_latency_ms": sum(item["latency_ms"] for item in full_model_receipts),
        },
        "orbita_compact": {
            "passing_patches": sum(
                receipt["success"] for receipt in verification["compact"].values()
            ),
            "usage": compact_usage,
            "model_latency_ms": sum(item["latency_ms"] for item in compact_model_receipts),
        },
        "total_token_reduction": (
            1.0 - (compact_total / full_total) if full_total else None
        ),
        "all_originals_failed": all(
            not receipt["success"] for receipt in original_receipts.values()
        ),
        "interpretation_boundary": (
            "Three synthetic Python maintenance tasks with independent full and compact GPT calls. "
            "Correctness is executable pytest success, not an LLM judge."
        ),
    }

    _write_json(out / "tasks.json", tasks)
    _write_json(out / "compression_receipts.json", [item["receipt"] for item in compressed])
    _write_json(out / "full_patches.json", full_patches)
    _write_json(out / "compact_patches.json", compact_patches)
    _write_json(out / "full_model_receipts.json", full_model_receipts)
    _write_json(out / "compact_model_receipts.json", compact_model_receipts)
    _write_json(out / "test_receipts.json", verification)
    _write_json(out / "original_failure_receipts.json", original_receipts)
    _write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
