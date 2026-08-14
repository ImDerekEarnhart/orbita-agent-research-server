#!/usr/bin/env python3
"""Provision and verify SHARB B3 without reading any benchmark packet.

The command accepts packet *identifiers*, never packet paths. It rotates an
opaque boot nonce, redeploys the dedicated no-volume service, verifies its
server-side empty-boot attestation, hashes the MCP tool catalog, and writes a
local administrative receipt. Authentication values are read from environment
variables and are never printed or included in receipts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def railway_json(*args: str, stdin: str | None = None) -> Any:
    executable = shutil.which("railway.cmd" if os.name == "nt" else "railway")
    if not executable:
        raise RuntimeError("Railway CLI was not found on PATH")
    command = [executable, *args, "--json"]
    completed = subprocess.run(
        command,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        # Railway errors can echo selectors but must never echo supplied stdin.
        safe_error = completed.stderr.strip() or "Railway command failed"
        raise RuntimeError(safe_error)
    output = completed.stdout.strip()
    return json.loads(output) if output else {}


def set_variable(args: argparse.Namespace, name: str, value: str, *, secret: bool = False) -> None:
    base = [
        "variable",
        "set",
        name if secret else f"{name}={value}",
        "--service",
        args.service,
        "--environment",
        args.environment,
        "--project",
        args.project,
        "--skip-deploys",
    ]
    if secret:
        base.insert(3, "--stdin")
    # Deliberately discard the JSON response: variable APIs may return raw values.
    railway_json(*base, stdin=value if secret else None)


def production_environment(status: dict[str, Any], environment: str) -> dict[str, Any]:
    for edge in status.get("environments", {}).get("edges", []):
        node = edge.get("node", {})
        if node.get("name") == environment or node.get("id") == environment:
            return node
    raise RuntimeError(f"Railway environment {environment!r} was not found")


def service_instance(environment: dict[str, Any], service: str) -> dict[str, Any]:
    for edge in environment.get("serviceInstances", {}).get("edges", []):
        node = edge.get("node", {})
        if service in {node.get("serviceName"), node.get("serviceId"), node.get("id")}:
            return node
    raise RuntimeError(f"Railway service {service!r} was not found in the selected environment")


def assert_no_volume(environment: dict[str, Any], instance: dict[str, Any]) -> None:
    service_id = instance.get("serviceId")
    attached = [
        edge.get("node", {})
        for edge in environment.get("volumeInstances", {}).get("edges", [])
        if edge.get("node", {}).get("serviceId") == service_id
        and not edge.get("node", {}).get("deletedAt")
    ]
    mounts = instance.get("latestDeployment", {}).get("meta", {}).get("volumeMounts") or []
    if attached or mounts:
        raise RuntimeError("B3 isolation refused: the selected service has a persistent volume")


def service_url(instance: dict[str, Any]) -> str:
    domains = instance.get("domains", {}).get("serviceDomains", [])
    if not domains:
        raise RuntimeError("the B3 service has no Railway service domain")
    return "https://" + domains[0]["domain"].rstrip("/")


def get_json(url: str, *, timeout: float = 30.0, attempts: int = 6) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(  # noqa: S310 - operator-selected HTTPS service
                request, timeout=timeout
            ) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001 - transient DNS/TLS/route readiness is retried
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(5)
    raise RuntimeError(f"the isolation route did not become reachable: {type(last_error).__name__}")


async def mcp_catalog_hash(url: str, token: str) -> tuple[str, int]:
    headers = {"Authorization": f"Bearer {token}"}
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(
            url.rstrip("/") + "/mcp", http_client=http_client
        ) as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
    catalog = [tool.model_dump(mode="json", exclude_none=True) for tool in result.tools]
    catalog.sort(key=lambda item: item["name"])
    return canonical_hash(catalog), len(catalog)


def wait_for_deployment(args: argparse.Namespace, deployment_id: str, timeout: float = 600.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deployments = railway_json(
            "deployment",
            "list",
            "--service",
            args.service,
            "--environment",
            args.environment,
            "--project",
            args.project,
            "--limit",
            "10",
        )
        records = deployments if isinstance(deployments, list) else deployments.get("deployments", [])
        for record in records:
            if record.get("id") != deployment_id:
                continue
            status = str(record.get("status", "")).upper()
            if status == "SUCCESS":
                return record
            if status in {"FAILED", "CRASHED", "REMOVED", "CANCELED"}:
                raise RuntimeError(f"B3 deployment ended with status {status}")
        time.sleep(5)
    raise RuntimeError("timed out waiting for the B3 deployment")


def validate_attestation(
    attestation: dict[str, Any], *, replicate_id: str, packet_id: str, commit: str, nonce: str
) -> None:
    supplied_hash = attestation.get("attestation_sha256")
    body = {key: value for key, value in attestation.items() if key != "attestation_sha256"}
    checks = {
        "receipt hash": supplied_hash == canonical_hash(body),
        "verified status": attestation.get("status") == "VERIFIED_EMPTY_BOOT",
        "B3 condition": attestation.get("condition") == "B3",
        "replicate": attestation.get("replicate_id") == replicate_id,
        "packet": attestation.get("packet_id") == packet_id,
        "commit": attestation.get("observed_commit") == commit,
        "boot nonce": attestation.get("boot_nonce_sha256")
        == hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        "empty home": attestation.get("home_before_gateway", {}).get("entry_count") == 0,
        "empty cache": attestation.get("cache_before_gateway", {}).get("entry_count") == 0,
        "zero cases": attestation.get("gateway_case_count_after_initialization") == 0,
        "no volume environment": not attestation.get("volume_environment_present"),
        "guided bridge disabled": attestation.get("guided_bridge_disabled") is True,
        "no packet access": attestation.get("packet_content_accessed_by_attestor") is False,
        "no secrets": attestation.get("secrets_in_attestation") is False,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError("B3 attestation failed: " + ", ".join(failed))


def previous_b3_receipt(receipt_dir: Path) -> dict[str, Any] | None:
    candidates = sorted(receipt_dir.glob("B3_isolation_*.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    token = os.getenv(args.token_env, "")
    if len(token) < 32:
        raise RuntimeError(f"{args.token_env} must hold the dedicated B3 bearer credential")

    nonce = secrets.token_urlsafe(48)
    for name, value in (
        ("ORBITA_BENCHMARK_CONDITION", "B3"),
        ("ORBITA_BENCHMARK_REPLICATE_ID", args.replicate),
        ("ORBITA_BENCHMARK_PACKET_ID", args.packet),
        ("ORBITA_BENCHMARK_BOOT_NONCE", nonce),
        ("ORBITA_BENCHMARK_EXPECTED_COMMIT", args.commit),
        ("GIT_COMMIT_SHA", args.commit),
        ("ORBITA_AGENT_HOME", "/tmp/orbita-sharb-b3"),
        ("ORBITA_BENCHMARK_CACHE_ROOT", "/tmp/orbita-sharb-b3-cache"),
        ("XDG_CACHE_HOME", "/tmp/orbita-sharb-b3-cache"),
        ("ORBITA_AGENT_AUTH_MODE", "bearer"),
        ("ORBITA_AGENT_REQUIRE_AUTH", "1"),
    ):
        set_variable(args, name, value)

    redeploy = railway_json(
        "redeploy",
        "--service",
        args.service,
        "--environment",
        args.environment,
        "--project",
        args.project,
        "--yes",
    )
    deployment_id = redeploy.get("id") or redeploy.get("deploymentId")
    if not deployment_id:
        raise RuntimeError("Railway did not return the new B3 deployment ID")
    deployment = wait_for_deployment(args, deployment_id)

    status = railway_json(
        "status", "--project", args.project, "--environment", args.environment
    )
    environment = production_environment(status, args.environment)
    instance = service_instance(environment, args.service)
    assert_no_volume(environment, instance)
    if instance.get("latestDeployment", {}).get("id") != deployment_id:
        raise RuntimeError("the service's latest deployment is not the newly isolated boot")

    base_url = service_url(instance)
    attestation = get_json(base_url + "/benchmark-isolation")
    validate_attestation(
        attestation,
        replicate_id=args.replicate,
        packet_id=args.packet,
        commit=args.commit,
        nonce=nonce,
    )
    catalog_hash, tool_count = asyncio.run(mcp_catalog_hash(base_url, token))

    receipt_dir = Path(args.receipt_dir).expanduser().resolve()
    receipt_dir.mkdir(parents=True, exist_ok=True)
    previous = previous_b3_receipt(receipt_dir)
    if previous:
        if previous.get("deployment_id") == deployment_id:
            raise RuntimeError("B3 isolation refused: deployment was reused")
        if previous.get("attestation", {}).get("boot_nonce_sha256") == attestation.get(
            "boot_nonce_sha256"
        ):
            raise RuntimeError("B3 isolation refused: boot nonce was reused")

    image_digest = instance.get("latestDeployment", {}).get("meta", {}).get("imageDigest")
    receipt: dict[str, Any] = {
        "schema": "sharb.b3-per-packet-isolation-receipt.v1",
        "status": "READY_FOR_ONE_PACKET",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "project_id": status.get("id"),
        "environment_id": environment.get("id"),
        "service_id": instance.get("serviceId"),
        "service_name": instance.get("serviceName"),
        "deployment_id": deployment_id,
        "deployment_status": deployment.get("status"),
        "image_digest": image_digest,
        "commit": args.commit,
        "replicate_id": args.replicate,
        "packet_id": args.packet,
        "mcp_url": base_url + "/mcp",
        "mcp_tool_catalog_sha256": catalog_hash,
        "mcp_tool_count": tool_count,
        "persistent_volume_attached": False,
        "packet_opened_by_isolation_harness": False,
        "credential_recorded": False,
        "attestation": attestation,
        "previous_receipt_sha256": previous.get("receipt_sha256") if previous else None,
    }
    receipt["receipt_sha256"] = canonical_hash(receipt)
    filename = f"B3_isolation_{args.replicate}_{args.packet}_{deployment_id}.json"
    destination = receipt_dir / filename
    destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": receipt["status"],
        "receipt": str(destination),
        "receipt_sha256": receipt["receipt_sha256"],
        "deployment_id": deployment_id,
        "image_digest": image_digest,
        "mcp_url": receipt["mcp_url"],
        "mcp_tool_catalog_sha256": catalog_hash,
        "mcp_tool_count": tool_count,
        "token_environment_variable": args.token_env,
        "packet_opened": False,
    }


def install_token(args: argparse.Namespace) -> dict[str, Any]:
    token = os.getenv(args.token_env, "")
    if len(token) < 32:
        raise RuntimeError(f"{args.token_env} must hold the dedicated B3 bearer credential")
    set_variable(args, "ORBITA_AGENT_API_TOKEN", token, secret=True)
    return {"status": "INSTALLED_WITHOUT_DISCLOSURE", "token_environment_variable": args.token_env}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("install-token", "prepare-b3"))
    result.add_argument("--project", required=True)
    result.add_argument("--environment", default="production")
    result.add_argument("--service", required=True)
    result.add_argument("--token-env", default="SHARB_B3_MCP_TOKEN")
    result.add_argument("--replicate")
    result.add_argument("--packet")
    result.add_argument("--commit")
    result.add_argument("--receipt-dir", default=".sharb-isolation-receipts")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "install-token":
            outcome = install_token(args)
        else:
            missing = [name for name in ("replicate", "packet", "commit") if not getattr(args, name)]
            if missing:
                raise RuntimeError("prepare-b3 requires --" + ", --".join(missing))
            outcome = prepare(args)
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - administrative CLI returns a safe summary
        print(json.dumps({"status": "REFUSED", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
