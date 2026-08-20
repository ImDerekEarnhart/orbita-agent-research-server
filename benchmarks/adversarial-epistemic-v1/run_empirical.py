#!/usr/bin/env python3
"""Run real model baselines through Orbita's sealed comparative evaluator."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from orbita.evaluation import ComparativeEvaluationRuntime, default_adversarial_suite
from orbita.ledger import EpistemicLedger

TASK_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "final_answer",
        "claim_judgments",
        "action_judgments",
        "discovery_judgments",
        "audit_trace",
    ],
    "properties": {
        "final_answer": {"type": "string"},
        "claim_judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim_id", "state", "evidence_ids", "derivation_ids", "rationale"],
                "properties": {
                    "claim_id": {"type": "string"},
                    "state": {
                        "type": "string",
                        "enum": ["supported", "unknown", "challenged", "refuted", "retracted", "abstained"],
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "derivation_ids": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
            },
        },
        "action_judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action_id", "state", "receipt_ids", "rationale"],
                "properties": {
                    "action_id": {"type": "string"},
                    "state": {"type": "string", "enum": ["success", "failure", "unknown"]},
                    "receipt_ids": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
            },
        },
        "discovery_judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hypothesis_id", "state", "evidence_ids", "rationale"],
                "properties": {
                    "hypothesis_id": {"type": "string"},
                    "state": {"type": "string", "enum": ["committed", "provisional", "rejected", "unknown"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
            },
        },
        "audit_trace": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "id"],
                "properties": {"kind": {"type": "string"}, "id": {"type": "string"}},
            },
        },
    },
}

DIRECT_INSTRUCTIONS = """You are being evaluated on epistemic judgment.
Use only the supplied task. Return exactly one judgment for every listed target and no unlisted targets.
Evidence, derivation, and receipt IDs must refer only to IDs present in the task.
An audit-trace entry means you actually relied on that item; do not invent trace items."""

ORBITA_POLICY_INSTRUCTIONS = """You are being evaluated with the Orbita epistemic policy card.
Use only the supplied task. Return exactly one judgment for every listed target and no unlisted targets.
Apply these rules:
- Repetition by models or unverified sources is not independent evidence.
- New authoritative contradiction must change the prior support state.
- A conclusion loses support when a required premise is revoked, unless a complete alternate proof survives.
- Time-scoped facts at different times are not contradictions.
- An action is successful only when its receipt and required artifacts establish success.
- Holdout survival without independent replication is provisional, never committed.
- A committed discovery needs independent confirming evidence; attractive false candidates must be rejected.
- Cite only supplied evidence, derivation, and receipt IDs.
- Include every item actually relied upon in the audit trace.
Do not claim this policy card is the full Orbita runtime."""

ORBITA_MCP_INSTRUCTIONS = """You are being evaluated with read-only access to the live Orbita MCP server.
Before judging the task, call orbita_case_context for case case_4abcaecf04ad421f.
Use the returned case goal, plans, assumptions, and artifact profiles as governance context.
Return exactly one judgment for every listed target and no unlisted targets.
Evidence, derivation, and receipt IDs must refer only to IDs present in the benchmark task.
Do not claim this read-only context call is the full Orbita runtime."""


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], attempts: int = 4) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {408, 409, 429, 500, 502, 503, 504} or attempt + 1 == attempts:
                raise RuntimeError(f"HTTP {exc.code} from {url}: {detail[:2000]}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt + 1 == attempts:
                raise RuntimeError(f"Request failed for {url}: {exc}") from exc
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _openai_task(
    task: dict[str, Any],
    model: str,
    instructions: str,
    *,
    use_orbita_mcp: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": json.dumps(task, sort_keys=True),
        "max_output_tokens": 2200,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "orbita_task_response",
                "strict": True,
                "schema": TASK_RESPONSE_SCHEMA,
            }
        },
    }
    if use_orbita_mcp:
        orbita_token = os.environ.get("ORBITA_AGENT_API_TOKEN", "").strip()
        if not orbita_token:
            raise RuntimeError("ORBITA_AGENT_API_TOKEN is not configured")
        payload["tools"] = [
            {
                "type": "mcp",
                "server_label": "orbita",
                "server_description": "Orbita's live governed research server.",
                "server_url": "https://orbita-agent-research-server-production.up.railway.app/mcp",
                "authorization": orbita_token,
                "require_approval": "never",
                "allowed_tools": ["orbita_case_context"],
            }
        ]
    response = _post_json(
        "https://api.openai.com/v1/responses",
        {"Authorization": f"Bearer {key}"},
        payload,
    )
    texts = [
        content["text"]
        for item in response.get("output", [])
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    ]
    if not texts:
        raise RuntimeError(f"OpenAI response {response.get('id')} contained no output_text")
    mcp_trace = []
    for item in response.get("output", []):
        if item.get("type") == "mcp_list_tools":
            mcp_trace.append(
                {
                    "type": "mcp_list_tools",
                    "server_label": item.get("server_label"),
                    "tools": [tool.get("name") for tool in item.get("tools", [])],
                }
            )
        elif item.get("type") == "mcp_call":
            mcp_trace.append(
                {
                    "type": "mcp_call",
                    "server_label": item.get("server_label"),
                    "name": item.get("name"),
                    "arguments": item.get("arguments"),
                    "error": item.get("error"),
                    "output": item.get("output"),
                }
            )
    return json.loads("".join(texts)), {
        "response_id": response.get("id"),
        "model": response.get("model", model),
        "usage": response.get("usage", {}),
        "mcp_trace": mcp_trace,
    }


def _anthropic_task(
    task: dict[str, Any], model: str, instructions: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    response = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        {
            "model": model,
            "system": instructions,
            "messages": [{"role": "user", "content": json.dumps(task, sort_keys=True)}],
            "max_tokens": 2200,
            "tools": [
                {
                    "name": "submit_orbita_task_response",
                    "description": "Submit the schema-constrained benchmark judgment.",
                    "input_schema": TASK_RESPONSE_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": "submit_orbita_task_response"},
        },
    )
    uses = [item for item in response.get("content", []) if item.get("type") == "tool_use"]
    if not uses:
        raise RuntimeError(f"Anthropic response {response.get('id')} contained no tool use")
    return uses[-1]["input"], {
        "response_id": response.get("id"),
        "model": response.get("model", model),
        "usage": response.get("usage", {}),
    }


def _run_system(
    public_tasks: list[dict[str, Any]],
    provider: str,
    model: str,
    condition: str,
    workers: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    instructions = {
        "direct": DIRECT_INSTRUCTIONS,
        "orbita-policy": ORBITA_POLICY_INSTRUCTIONS,
        "orbita-mcp": ORBITA_MCP_INSTRUCTIONS,
    }[condition]
    callback = _openai_task if provider == "openai" else _anthropic_task
    started = time.perf_counter()
    records: dict[str, tuple[dict[str, Any], dict[str, Any], float]] = {}

    def run_one(task: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any], float]:
        task_started = time.perf_counter()
        if provider == "openai":
            result, receipt = callback(
                task,
                model,
                instructions,
                use_orbita_mcp=condition == "orbita-mcp",
            )
        else:
            result, receipt = callback(task, model, instructions)
        return task["id"], result, receipt, 1000 * (time.perf_counter() - task_started)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, task) for task in public_tasks]
        for future in as_completed(futures):
            task_id, result, receipt, latency_ms = future.result()
            records[task_id] = (result, receipt, latency_ms)
            print(f"{provider}/{model}/{condition}: {task_id}", flush=True)

    results = []
    receipts = []
    for task in public_tasks:
        result, receipt, latency_ms = records[task["id"]]
        results.append(
            {
                "task_id": task["id"],
                **result,
                "latency_ms": latency_ms,
                "token_usage": receipt["usage"],
                "metadata": {"provider_response_id": receipt["response_id"]},
            }
        )
        receipts.append({"task_id": task["id"], **receipt, "latency_ms": latency_ms})

    display_provider = "OpenAI" if provider == "openai" else "Anthropic"
    suffix = {
        "direct": "direct",
        "orbita-policy": "Orbita policy card",
        "orbita-mcp": "Orbita MCP context",
    }[condition]
    payload = {
        "schema_version": "1.0",
        "system": {
            "kind": "base_llm" if condition == "direct" else ("orbita" if condition == "orbita-mcp" else "custom"),
            "name": f"{model} — {suffix}",
            "version": model,
            "provider": display_provider,
            "evaluation_mode": "empirical",
            "config": {
                "condition": condition,
                "temperature": "provider default",
                "full_orbita_runtime": False,
                "remote_mcp": condition == "orbita-mcp",
            },
        },
        "results": results,
        "metadata": {
            "elapsed_seconds": time.perf_counter() - started,
            "disclosure": (
                "The Orbita-policy condition uses a frozen policy card. The Orbita-MCP condition "
                "uses a required read-only live case-context call. Neither is the full runtime."
            ),
        },
    }
    return payload, receipts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--anthropic-model", default="claude-opus-5")
    parser.add_argument("--providers", nargs="+", choices=["openai", "anthropic"], default=["openai", "anthropic"])
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=["direct", "orbita-policy", "orbita-mcp"],
        default=["direct", "orbita-policy"],
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--responses-from", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "benchmark.sqlite"
    if db_path.exists() and not args.resume:
        raise RuntimeError(f"Refusing to overwrite existing benchmark database: {db_path}")

    with EpistemicLedger(db_path) as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(default_adversarial_suite())
        public_bundle = runtime.export_public_suite(suite["id"])
        (out / "public_tasks.json").write_text(json.dumps(public_bundle, indent=2), encoding="utf-8")

        run_summaries = []
        for provider in args.providers:
            model = args.openai_model if provider == "openai" else args.anthropic_model
            for condition in args.conditions:
                stem = f"{provider}-{model}-{condition}".replace("/", "_")
                response_path = out / f"{stem}.response.json"
                receipt_path = out / f"{stem}.receipts.json"
                if args.resume and response_path.exists() and receipt_path.exists():
                    payload = json.loads(response_path.read_text(encoding="utf-8"))
                elif (
                    args.responses_from
                    and (args.responses_from.resolve() / response_path.name).exists()
                    and (args.responses_from.resolve() / receipt_path.name).exists()
                ):
                    source_response = args.responses_from.resolve() / response_path.name
                    source_receipt = args.responses_from.resolve() / receipt_path.name
                    payload = json.loads(source_response.read_text(encoding="utf-8"))
                    response_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    receipt_path.write_text(source_receipt.read_text(encoding="utf-8"), encoding="utf-8")
                else:
                    payload, receipts = _run_system(
                        public_bundle["tasks"], provider, model, condition, max(1, args.workers)
                    )
                    response_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    receipt_path.write_text(json.dumps(receipts, indent=2), encoding="utf-8")
                scored = runtime.import_run(suite["id"], payload)
                run_summaries.append(
                    {
                        "provider": provider,
                        "model": model,
                        "condition": condition,
                        "run_id": scored["id"],
                        "response_hash": scored["response_hash"],
                        "response_file": response_path.name,
                        "receipts_file": receipt_path.name,
                    }
                )

        report = runtime.compile_report(suite["id"])
        verification = {
            "suite": runtime.verify_suite(suite["id"]),
            "runs": {item["run_id"]: runtime.verify_run(item["run_id"]) for item in run_summaries},
            "report": runtime.verify_report(suite["id"]),
        }
        manifest = {
            "suite_id": suite["id"],
            "suite_hash": suite["suite_hash"],
            "report_hash": report["report_hash"],
            "runs": run_summaries,
            "verification": verification,
            "interpretation_boundary": report["report"]["interpretation_boundary"],
        }
        (out / "release_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (out / "report.json").write_text(json.dumps(report["report"], indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
