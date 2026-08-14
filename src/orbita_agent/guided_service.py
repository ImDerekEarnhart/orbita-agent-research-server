"""Tenant-safe internal HTTP surface used by the Guided Orbita frontend.

MCP and Guided are two interfaces to one AgentGateway.  Guided authenticates its own
human users, then calls this surface with a deployment secret and the user's opaque UUID.
The UUID selects an isolated AgentGateway workspace; email addresses and usernames never
cross the service boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from orbita_mvp.execution_dispatch import ExecutionCapabilityLimit

from .adjudication import adjudicate_epistemic_task, compress_epistemic_task
from .code_context import compress_code_context
from .gateway import AgentGateway
from .semantic_evolution import (
    audit_representation,
    audit_temporal_unaskability,
    build_capability_component_graph,
    build_language_snapshot,
)
from .uploads import safe_upload_filename

GUIDED_API_PREFIX = "/guided/v1"
GUIDED_USER_HEADER = "X-Orbita-User-Id"
GUIDED_TOKEN_ENV = "ORBITA_GUIDED_SERVICE_TOKEN"
DEFAULT_GUIDED_UPLOAD_BYTES = 50 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class GuidedServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def guided_tenant(request: Request, *, expected_token: str) -> str:
    if len(expected_token) < 32:
        raise GuidedServiceError(503, "Guided core integration is not configured.")
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, expected_token):
        raise GuidedServiceError(401, "Invalid Guided service credentials.")
    raw_user_id = request.headers.get(GUIDED_USER_HEADER, "").strip()
    try:
        user_id = str(uuid.UUID(raw_user_id))
    except (ValueError, AttributeError) as exc:
        raise GuidedServiceError(400, f"{GUIDED_USER_HEADER} must be a UUID.") from exc
    # Keep the filesystem scope compact enough for Windows report paths while
    # retaining 128 bits of the UUID-derived identity.  The Guided application
    # remains the source of truth for the user-to-UUID mapping.
    identity = hashlib.sha256(user_id.encode("ascii")).hexdigest()[:32]
    return f"g-{identity}"


def _public_case(case: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: case.get(key)
        for key in ("id", "name", "goal", "mode", "domain_hint", "status", "created_at", "updated_at")
    }
    result["case_id"] = case.get("id")
    result["files"] = [
        {
            key: item.get(key)
            for key in (
                "id",
                "case_id",
                "original_name",
                "media_type",
                "size_bytes",
                "sha256",
                "parse_status",
                "artifact_kind",
                "profile",
                "error",
            )
        }
        for item in case.get("files", [])
    ]
    result["plans"] = case.get("plans", [])
    result["runs"] = []
    for run in case.get("runs", []):
        public_run = dict(run)
        run_result = dict(public_run.get("result") or {})
        reports = {}
        for role, artifact in (run_result.get("reports") or {}).items():
            reports[role] = {
                key: artifact.get(key)
                for key in ("sha256", "size_bytes")
                if key in artifact
            }
        if reports:
            run_result["reports"] = reports
        public_run["result"] = run_result
        result["runs"].append(public_run)
    return result


async def _json(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:  # noqa: BLE001 - malformed client JSON is a 400
        raise GuidedServiceError(400, "Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise GuidedServiceError(400, "Request body must be a JSON object.")
    return value


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def install_guided_service_routes(
    mcp: FastMCP,
    *,
    gateway_for_tenant: Callable[[str], AgentGateway],
) -> None:
    expected_token = os.getenv(GUIDED_TOKEN_ENV, "").strip()

    def bound(request: Request) -> AgentGateway:
        return gateway_for_tenant(guided_tenant(request, expected_token=expected_token))

    def failure(exc: Exception) -> JSONResponse:
        if isinstance(exc, GuidedServiceError):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status_code)
        if isinstance(exc, ExecutionCapabilityLimit):
            return JSONResponse({"ok": False, "limitation": exc.as_dict()}, status_code=422)
        if isinstance(exc, KeyError):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        if isinstance(exc, (ValueError, TypeError)):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        LOGGER.error(
            "Unexpected Guided service failure",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            {"ok": False, "error": f"{type(exc).__name__} while processing the Guided request"},
            status_code=500,
        )

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/health", methods=["GET"], include_in_schema=False)
    async def guided_health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "product": "orbita-unified-core",
                "guided_service_configured": len(expected_token) >= 32,
                "interfaces": ["mcp", "guided"],
            }
        )

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/capabilities", methods=["GET"], include_in_schema=False)
    async def guided_capabilities(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            return JSONResponse(
                gateway.capabilities()
                | {
                    "interface": "guided",
                    "same_core_as_mcp": True,
                    "approval_boundary": "compile, approve exact hash, then run",
                }
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/adjudicate", methods=["POST"], include_in_schema=False)
    async def guided_adjudicate(request: Request) -> JSONResponse:
        """Run Orbita's deterministic adjudicator without calling a language model."""
        try:
            bound(request)  # Authenticate and bind the caller even though the operation is read-only.
            body = await _json(request)
            task = body.get("task")
            if not isinstance(task, dict):
                raise GuidedServiceError(400, "task must be a JSON object.")
            return JSONResponse(adjudicate_epistemic_task(task))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/compress/evidence", methods=["POST"], include_in_schema=False)
    async def guided_compress_evidence(request: Request) -> JSONResponse:
        """Select target-relevant evidence and return an exact retained/dropped receipt."""
        try:
            bound(request)
            body = await _json(request)
            task = body.get("task")
            if not isinstance(task, dict):
                raise GuidedServiceError(400, "task must be a JSON object.")
            limit = int(body.get("max_context_items", 8))
            return JSONResponse(compress_epistemic_task(task, max_context_items=limit))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/compress/code", methods=["POST"], include_in_schema=False)
    async def guided_compress_code(request: Request) -> JSONResponse:
        """Select issue-relevant code context without reading the caller's filesystem."""
        try:
            bound(request)
            body = await _json(request)
            issue = str(body.get("issue") or "").strip()
            files = body.get("files")
            if not issue or not isinstance(files, list):
                raise GuidedServiceError(400, "issue and a files array are required.")
            return JSONResponse(
                compress_code_context(
                    issue,
                    files,
                    max_files=int(body.get("max_files", 6)),
                    max_characters=int(body.get("max_characters", 80_000)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/semantic/language-snapshot", methods=["POST"], include_in_schema=False)
    async def guided_language_snapshot(request: Request) -> JSONResponse:
        try:
            bound(request)
            body = await _json(request)
            spec = body.get("spec")
            if not isinstance(spec, dict):
                raise GuidedServiceError(400, "spec must be a JSON object.")
            return JSONResponse(build_language_snapshot(spec))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/semantic/representation-audit", methods=["POST"], include_in_schema=False)
    async def guided_representation_audit(request: Request) -> JSONResponse:
        try:
            bound(request)
            body = await _json(request)
            snapshot, cases = body.get("snapshot"), body.get("cases")
            if not isinstance(snapshot, dict) or not isinstance(cases, list):
                raise GuidedServiceError(400, "snapshot and a cases array are required.")
            return JSONResponse(audit_representation(snapshot, cases))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/semantic/temporal-audit", methods=["POST"], include_in_schema=False)
    async def guided_temporal_audit(request: Request) -> JSONResponse:
        try:
            bound(request)
            body = await _json(request)
            histories, candidates = body.get("histories"), body.get("candidates")
            if not isinstance(histories, list) or not isinstance(candidates, list):
                raise GuidedServiceError(400, "histories and candidates arrays are required.")
            return JSONResponse(
                audit_temporal_unaskability(histories, candidates, tolerance=float(body.get("tolerance", 1e-9)))
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/semantic/component-graph", methods=["POST"], include_in_schema=False)
    async def guided_component_graph(request: Request) -> JSONResponse:
        try:
            bound(request)
            body = await _json(request)
            components = body.get("components")
            if not isinstance(components, list):
                raise GuidedServiceError(400, "components must be an array.")
            return JSONResponse(build_capability_component_graph(components))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/executors", methods=["GET"], include_in_schema=False)
    async def guided_executor_registry(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).executor_registry_status())
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/execution-receipts", methods=["GET"], include_in_schema=False)
    async def guided_execution_receipts(request: Request) -> JSONResponse:
        try:
            return JSONResponse({"receipts": bound(request).list_candidate_execution_receipts()})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/execution-receipts/{{receipt_id}}", methods=["GET"], include_in_schema=False
    )
    async def guided_execution_receipt(request: Request) -> JSONResponse:
        try:
            return JSONResponse(
                bound(request).get_candidate_execution_receipt(request.path_params["receipt_id"])
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/execution-receipts/{{receipt_id}}/verify", methods=["GET"], include_in_schema=False
    )
    async def guided_verify_execution_receipt(request: Request) -> JSONResponse:
        try:
            return JSONResponse(
                bound(request).verify_candidate_execution_receipt(request.path_params["receipt_id"])
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/evidence", methods=["GET"], include_in_schema=False)
    async def guided_normalized_evidence(request: Request) -> JSONResponse:
        try:
            return JSONResponse({"receipts": bound(request).list_normalized_evidence()})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/evidence/status", methods=["GET"], include_in_schema=False)
    async def guided_evidence_status(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).evidence_normalization_status())
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/evidence/{{receipt_id}}", methods=["GET"], include_in_schema=False
    )
    async def guided_evidence_receipt(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).get_normalized_evidence(request.path_params["receipt_id"]))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/evidence/{{receipt_id}}/verify", methods=["GET"], include_in_schema=False
    )
    async def guided_verify_evidence(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).verify_normalized_evidence(request.path_params["receipt_id"]))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/evidence/{{receipt_id}}/eligibility", methods=["POST"], include_in_schema=False
    )
    async def guided_evidence_eligibility(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).check_evidence_eligibility(
                    request.path_params["receipt_id"], str(body.get("decision_kind") or "")
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/evidence/discovery-run", methods=["POST"], include_in_schema=False
    )
    async def guided_normalize_discovery_run(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).normalize_discovery_run_evidence(
                    str(body.get("case_id") or ""), str(body.get("run_id") or "")
                ),
                status_code=201,
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/evidence/external-experiment", methods=["POST"], include_in_schema=False
    )
    async def guided_normalize_external_experiment(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).normalize_external_experiment_evidence(
                    str(body.get("experiment_id") or "")
                ),
                status_code=201,
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/problem-loops", methods=["GET", "POST"], include_in_schema=False)
    async def guided_problem_loops(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            if request.method == "GET":
                return JSONResponse({"loops": gateway.list_general_problem_loops()})
            body = await _json(request)
            return JSONResponse(
                gateway.create_general_problem_loop(
                    goal=str(body.get("goal") or ""),
                    success_criteria=body.get("success_criteria"),
                    allowed_capabilities=body.get("allowed_capabilities"),
                    max_cycles=int(body.get("max_cycles", 3)),
                    created_by=str(body.get("created_by") or "guided-hybrid"),
                ),
                status_code=201,
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/problem-loops/{{loop_id}}", methods=["GET"], include_in_schema=False)
    async def guided_problem_loop(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).get_general_problem_loop(request.path_params["loop_id"]))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/problem-loops/{{loop_id}}/advance", methods=["POST"], include_in_schema=False
    )
    async def guided_advance_problem_loop(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).advance_general_problem_loop(
                    request.path_params["loop_id"],
                    expected_state=str(body.get("expected_state") or ""),
                    expected_previous_event_hash=str(body.get("expected_previous_event_hash") or ""),
                    artifact=body.get("artifact"),
                    actor=str(body.get("actor") or "guided-hybrid"),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/problem-loops/{{loop_id}}/verify", methods=["GET"], include_in_schema=False
    )
    async def guided_verify_problem_loop(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).verify_general_problem_loop(request.path_params["loop_id"]))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/memory/status", methods=["GET"], include_in_schema=False)
    async def guided_memory_status(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).memory_status())
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/memory/search", methods=["POST"], include_in_schema=False)
    async def guided_memory_search(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            body = await _json(request)
            return JSONResponse(
                gateway.search_memory(
                    str(body.get("query") or ""),
                    limit=int(body.get("limit", 20)),
                    case_id=str(body["case_id"]) if body.get("case_id") else None,
                    role=str(body["role"]) if body.get("role") else None,
                    conversation_id=str(body["conversation_id"]) if body.get("conversation_id") else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/memory/reversals", methods=["POST"], include_in_schema=False)
    async def guided_memory_reversals(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            body = await _json(request)
            return JSONResponse(
                gateway.find_reversals(
                    case_id=str(body["case_id"]) if body.get("case_id") else None,
                    role=str(body.get("role") or "user"),
                    limit=int(body.get("limit", 20)),
                    min_days_apart=float(body.get("min_days_apart", 1.0)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/cases", methods=["GET", "POST"], include_in_schema=False)
    async def guided_cases(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            if request.method == "GET":
                return JSONResponse({"cases": gateway.list_cases()})
            body = await _json(request)
            created = gateway.create_case(
                name=str(body.get("name") or "Untitled research case"),
                goal=str(body.get("goal") or ""),
                domain_hint=str(body["domain_hint"]) if body.get("domain_hint") is not None else None,
            )
            return JSONResponse(created | {"case_id": created["id"]}, status_code=201)
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}", methods=["GET", "DELETE"], include_in_schema=False
    )
    async def guided_case(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            case_id = request.path_params["case_id"]
            if request.method == "DELETE":
                body = await _json(request)
                return JSONResponse(gateway.delete_case(case_id, confirmation=str(body.get("confirmation") or "")))
            with gateway._lock:
                case = gateway.service.store.get_case(case_id)
            return JSONResponse(_public_case(case))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/context", methods=["GET"], include_in_schema=False
    )
    async def guided_case_context(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).case_context(request.path_params["case_id"]))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/files", methods=["POST"], include_in_schema=False
    )
    async def guided_file(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            form = await request.form()
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise GuidedServiceError(400, "A multipart file field named 'file' is required.")
            filename = safe_upload_filename(upload.filename or "dataset.csv")
            if Path(filename).suffix.lower() not in {".csv", ".tsv"}:
                raise GuidedServiceError(400, "Guided direct uploads currently accept CSV or TSV only.")
            limit = int(os.getenv("ORBITA_GUIDED_MAX_UPLOAD_BYTES", str(DEFAULT_GUIDED_UPLOAD_BYTES)))
            written = 0
            with tempfile.TemporaryDirectory(prefix="orbita-guided-") as directory:
                staged = Path(directory) / filename
                with staged.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        written += len(chunk)
                        if written > limit:
                            raise GuidedServiceError(413, f"Upload exceeds the {limit} byte Guided limit.")
                        handle.write(chunk)
                record = gateway.ingest_upload(case_id=request.path_params["case_id"], path=staged)
            return JSONResponse(record | {"file_id": record["id"]}, status_code=201)
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/inherit", methods=["POST"], include_in_schema=False
    )
    async def guided_inherit(request: Request) -> JSONResponse:
        """Attach hash-verified inherited evidence without executing it."""
        try:
            gateway = bound(request)
            case_id = request.path_params["case_id"]
            body = await _json(request)
            manifest = body.get("manifest")
            if not isinstance(manifest, dict) or manifest.get("schema") != "orbita.unified-legacy-case-manifest.v1":
                raise GuidedServiceError(400, "A v1 unified legacy-case manifest is required.")
            expected_hash = str(body.get("expected_manifest_hash") or "")
            actual_hash = _canonical_hash(manifest)
            if len(expected_hash) != 64 or not hmac.compare_digest(actual_hash, expected_hash):
                raise GuidedServiceError(409, "Inherited manifest hash mismatch.")
            raw_artifacts = body.get("artifacts") or []
            if not isinstance(raw_artifacts, list) or len(raw_artifacts) > 3:
                raise GuidedServiceError(400, "artifacts must contain at most three report artifacts.")
            limit = gateway.config.max_inline_bytes
            prepared: list[tuple[str, str, str]] = []
            for item in raw_artifacts:
                if not isinstance(item, dict):
                    raise GuidedServiceError(400, "Each inherited artifact must be an object.")
                filename = safe_upload_filename(str(item.get("filename") or ""))
                if Path(filename).suffix.lower() not in {".json", ".md", ".txt"}:
                    raise GuidedServiceError(400, "Inherited report artifacts must be JSON, Markdown, or text.")
                content = item.get("content")
                if not isinstance(content, str) or len(content.encode("utf-8")) > limit:
                    raise GuidedServiceError(413, f"Inherited artifact exceeds the {limit} byte inline limit.")
                expected_artifact_hash = str(item.get("sha256") or "")
                actual_artifact_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if len(expected_artifact_hash) != 64 or not hmac.compare_digest(
                    actual_artifact_hash, expected_artifact_hash
                ):
                    raise GuidedServiceError(409, f"Inherited artifact hash mismatch: {filename}")
                prepared.append((filename, content, actual_artifact_hash))
            with gateway._lock:
                case = gateway.service.store.get_case(case_id)
            existing = {str(item.get("original_name") or "") for item in case.get("files", [])}
            filenames = ["legacy_case_manifest.json", *(item[0] for item in prepared)]
            if len(filenames) != len(set(filenames)) or existing.intersection(filenames):
                raise GuidedServiceError(409, "Inherited evidence was already attached to this case.")
            records = [
                gateway.add_inline_file(
                    case_id=case_id,
                    filename="legacy_case_manifest.json",
                    content=json.dumps(manifest, indent=2, ensure_ascii=False),
                )
            ]
            for filename, content, _artifact_hash in prepared:
                records.append(gateway.add_inline_file(case_id=case_id, filename=filename, content=content))
            return JSONResponse(
                {
                    "case_id": case_id,
                    "status": "inherited_evidence_attached",
                    "semantic_manifest_hash": actual_hash,
                    "artifacts": [
                        {"file_id": record["id"], "filename": record["original_name"], "sha256": record["sha256"]}
                        for record in records
                    ],
                    "execution_performed": False,
                },
                status_code=201,
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/plans", methods=["POST"], include_in_schema=False
    )
    async def guided_submit_plan(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            plan = body.get("plan")
            if not isinstance(plan, dict):
                raise GuidedServiceError(400, "plan must be a JSON object")
            record = bound(request).submit_plan(
                request.path_params["case_id"],
                plan=plan,
                compiler=str(body.get("compiler") or "guided-hybrid"),
            )
            return JSONResponse(record | {"plan_id": record["id"]}, status_code=201)
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/compile", methods=["POST"], include_in_schema=False
    )
    async def guided_compile(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            body = await _json(request)
            plan = gateway.compile_plan(
                request.path_params["case_id"],
                max_candidates=int(body["max_candidates"]) if body.get("max_candidates") is not None else None,
            )
            return JSONResponse(plan | {"plan_id": plan["id"]})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/plans/{{plan_id}}/approve", methods=["POST"], include_in_schema=False
    )
    async def guided_approve(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            body = await _json(request)
            plan = gateway.approve_plan(
                request.path_params["plan_id"],
                expected_plan_hash=str(body.get("expected_plan_hash") or ""),
                reviewer=str(body.get("reviewer") or "guided-user"),
                confirmation=str(body.get("confirmation") or ""),
            )
            return JSONResponse(plan | {"plan_id": plan["id"]})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/run", methods=["POST"], include_in_schema=False
    )
    async def guided_run(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            body = await _json(request)
            if body.get("auto_approve"):
                raise GuidedServiceError(
                    400,
                    "auto_approve is not available in the unified core; approve the exact plan hash first.",
                )
            run = gateway.run_discovery(
                request.path_params["case_id"],
                plan_id=str(body.get("plan_id") or ""),
            )
            return JSONResponse(run | {"run_id": run["id"]})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/blind/{{protocol_id}}", methods=["GET"], include_in_schema=False
    )
    async def guided_blind_protocol(request: Request) -> JSONResponse:
        try:
            return JSONResponse(
                bound(request).get_blind_calibration(request.path_params["protocol_id"])
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/blind/{{protocol_id}}/batch",
        methods=["GET"],
        include_in_schema=False,
    )
    async def guided_blind_batch(request: Request) -> JSONResponse:
        try:
            return JSONResponse(
                bound(request).get_blind_prediction_batch(request.path_params["protocol_id"])
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/blind/{{protocol_id}}/predictions",
        methods=["POST"],
        include_in_schema=False,
    )
    async def guided_blind_predictions(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).freeze_blind_predictions(
                    request.path_params["protocol_id"],
                    expected_protocol_hash=str(body.get("expected_protocol_hash") or ""),
                    predictions=body.get("predictions"),
                    provider=body.get("provider"),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/blind/{{protocol_id}}/scoring-key",
        methods=["POST"],
        include_in_schema=False,
    )
    async def guided_blind_scoring_key(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).seal_blind_scoring_key(
                    request.path_params["protocol_id"],
                    expected_protocol_hash=str(body.get("expected_protocol_hash") or ""),
                    expected_prediction_freeze_hash=str(
                        body.get("expected_prediction_freeze_hash") or ""
                    ),
                    filename=str(body.get("filename") or "scoring-key.csv"),
                    content=str(body.get("content") or ""),
                    sealed_by=str(body.get("sealed_by") or "guided-user"),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/blind/{{protocol_id}}/reveal",
        methods=["POST"],
        include_in_schema=False,
    )
    async def guided_blind_reveal(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).approve_blind_reveal(
                    request.path_params["protocol_id"],
                    expected_protocol_hash=str(body.get("expected_protocol_hash") or ""),
                    expected_prediction_freeze_hash=str(
                        body.get("expected_prediction_freeze_hash") or ""
                    ),
                    expected_scoring_key_hash=str(body.get("expected_scoring_key_hash") or ""),
                    reviewer=str(body.get("reviewer") or "guided-user"),
                    rationale=str(body.get("rationale") or ""),
                    confirmation=str(body.get("confirmation") or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/blind/{{protocol_id}}/score",
        methods=["POST"],
        include_in_schema=False,
    )
    async def guided_blind_score(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            return JSONResponse(
                bound(request).score_blind_calibration(
                    request.path_params["protocol_id"],
                    expected_prediction_freeze_hash=str(
                        body.get("expected_prediction_freeze_hash") or ""
                    ),
                    expected_scoring_key_hash=str(body.get("expected_scoring_key_hash") or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(f"{GUIDED_API_PREFIX}/runs/{{run_id}}", methods=["GET"], include_in_schema=False)
    async def guided_run_detail(request: Request) -> JSONResponse:
        try:
            return JSONResponse(bound(request).get_run(request.path_params["run_id"], findings_limit=100))
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/claims", methods=["GET"], include_in_schema=False
    )
    async def guided_claims(request: Request) -> JSONResponse:
        try:
            claims = bound(request).case_claims(request.path_params["case_id"])
            return JSONResponse({"claims": claims})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/claims/{{claim_id}}/{{view}}", methods=["GET"], include_in_schema=False
    )
    async def guided_claim_view(request: Request) -> JSONResponse:
        try:
            gateway = bound(request)
            view = request.path_params["view"]
            if view == "history":
                return JSONResponse(gateway.claim_history(request.path_params["claim_id"]))
            if view == "impact":
                return JSONResponse(gateway.claim_impact(request.path_params["claim_id"]))
            raise GuidedServiceError(404, "Unknown claim view.")
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/report", methods=["GET"], include_in_schema=False
    )
    async def guided_report(request: Request) -> Response:
        try:
            report = bound(request).report(request.path_params["case_id"], format="html")
            return HTMLResponse(report["content"], headers={"ETag": report["sha256"]})
        except Exception as exc:  # noqa: BLE001
            return failure(exc)

    @mcp.custom_route(
        f"{GUIDED_API_PREFIX}/cases/{{case_id}}/graph", methods=["GET"], include_in_schema=False
    )
    async def guided_graph(request: Request) -> Response:
        """Small dependency-free claim graph view for the Guided interface."""
        try:
            gateway = bound(request)
            case_id = request.path_params["case_id"]
            with gateway._lock:
                case = gateway.service.store.get_case(case_id)
            claims = gateway.case_claims(case_id)
            cards = []
            for claim in claims:
                claim_id = str(claim.get("claim_id") or claim.get("id") or "")
                statement = str(claim.get("statement") or claim.get("canonical_text") or claim_id)
                state = str(claim.get("support_state") or claim.get("state") or claim.get("status") or "unknown")
                cards.append(
                    '<article class="claim">'
                    f'<span class="state">{html.escape(state)}</span>'
                    f'<h2>{html.escape(statement)}</h2>'
                    f'<code>{html.escape(claim_id)}</code>'
                    "</article>"
                )
            body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orbita evidence graph</title><style>
body{{font:15px system-ui;background:#08111f;color:#e8f0fb;margin:0;padding:32px}}
main{{max-width:1000px;margin:auto}}.claim{{background:#111d30;border:1px solid #293b59;
border-radius:12px;padding:18px;margin:12px 0}}h1{{margin-bottom:4px}}h2{{font-size:17px}}
.state{{float:right;background:#203451;border-radius:999px;padding:4px 9px}}code{{color:#8eddf5}}
</style></head><body><main><p>Orbita unified claim graph</p>
<h1>{html.escape(str(case.get('name') or case_id))}</h1>
<p>{len(claims)} claims retained in the same evidence store used by MCP.</p>
{''.join(cards) if cards else '<p>No claims have been retained yet.</p>'}
</main></body></html>"""
            return HTMLResponse(body)
        except Exception as exc:  # noqa: BLE001
            return failure(exc)
