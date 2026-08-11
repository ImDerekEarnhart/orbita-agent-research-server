from __future__ import annotations

import hashlib
import json

from starlette.testclient import TestClient

from orbita_agent.gateway import APPROVAL_PHRASE
from orbita_agent.mcp_server import build_mcp_server

TOKEN = "guided-test-token-that-is-longer-than-thirty-two-bytes"
ALICE = "9e6c186e-6c49-4775-bcad-050d01685968"
BOB = "46d5062f-cbf2-4f37-b8ab-a8d88d141787"


def _headers(user_id: str = ALICE) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-Orbita-User-Id": user_id}


def test_guided_surface_and_mcp_share_one_tenant_gateway(gateway, monkeypatch, sample_csv):
    monkeypatch.setenv("ORBITA_GUIDED_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "none")
    mcp, _ = build_mcp_server(gateway=gateway)

    with TestClient(mcp.streamable_http_app()) as client:
        health = client.get("/guided/v1/health")
        assert health.status_code == 200
        assert health.json()["interfaces"] == ["mcp", "guided"]

        denied = client.get("/guided/v1/cases", headers={"X-Orbita-User-Id": ALICE})
        assert denied.status_code == 401

        created = client.post(
            "/guided/v1/cases",
            headers=_headers(),
            json={"name": "Unified ferrite case", "goal": "Find stable relationships"},
        )
        assert created.status_code == 201
        case_id = created.json()["case_id"]

        listed = client.get("/guided/v1/cases", headers=_headers()).json()["cases"]
        assert [item["id"] for item in listed] == [case_id]
        assert client.get("/guided/v1/cases", headers=_headers(BOB)).json()["cases"] == []
        assert client.get(f"/guided/v1/cases/{case_id}", headers=_headers(BOB)).status_code == 404

        context = client.get(f"/guided/v1/cases/{case_id}/context", headers=_headers())
        assert context.status_code == 200
        assert context.json()["case"]["id"] == case_id

        memory_status = client.get("/guided/v1/memory/status", headers=_headers())
        assert memory_status.status_code == 200

        compressed = client.post(
            "/guided/v1/compress/code",
            headers=_headers(),
            json={
                "issue": "Fix the payment timeout",
                "files": [
                    {"path": "payments.py", "content": "def payment_timeout(): pass"},
                    {"path": "unrelated.css", "content": "body { color: black; }"},
                ],
                "max_files": 1,
            },
        )
        assert compressed.status_code == 200
        assert compressed.json()["files"][0]["path"] == "payments.py"

        uploaded = client.post(
            f"/guided/v1/cases/{case_id}/files",
            headers=_headers(),
            files={"file": ("measurements.csv", sample_csv, "text/csv")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["artifact_kind"] == "table"

        manifest = {
            "schema": "orbita.unified-legacy-case-manifest.v1",
            "legacy_case_id": "case_legacy",
            "case": {"name": "Inherited"},
            "files": [],
            "plans": [],
            "runs": [],
            "claims": [],
        }
        manifest_hash = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        report = "# Frozen inherited report\n"
        inherited = client.post(
            f"/guided/v1/cases/{case_id}/inherit",
            headers=_headers(),
            json={
                "manifest": manifest,
                "expected_manifest_hash": manifest_hash,
                "artifacts": [
                    {
                        "filename": "legacy_research_dossier.md",
                        "content": report,
                        "sha256": hashlib.sha256(report.encode()).hexdigest(),
                    }
                ],
            },
        )
        assert inherited.status_code == 201
        assert inherited.json()["semantic_manifest_hash"] == manifest_hash
        assert inherited.json()["execution_performed"] is False
        duplicate = client.post(
            f"/guided/v1/cases/{case_id}/inherit",
            headers=_headers(),
            json={"manifest": manifest, "expected_manifest_hash": manifest_hash},
        )
        assert duplicate.status_code == 409

        compiled = client.post(
            f"/guided/v1/cases/{case_id}/compile",
            headers=_headers(),
            json={"max_candidates": 20},
        )
        assert compiled.status_code == 200
        plan = compiled.json()

        refused = client.post(
            f"/guided/v1/cases/{case_id}/run",
            headers=_headers(),
            json={"plan_id": plan["plan_id"], "auto_approve": True},
        )
        assert refused.status_code == 400
        assert "auto_approve" in refused.json()["error"]

        approved = client.post(
            f"/guided/v1/plans/{plan['plan_id']}/approve",
            headers=_headers(),
            json={
                "expected_plan_hash": plan["plan_hash"],
                "reviewer": "Guided test user",
                "confirmation": APPROVAL_PHRASE,
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        run = client.post(
            f"/guided/v1/cases/{case_id}/run",
            headers=_headers(),
            json={"plan_id": plan["plan_id"]},
        )
        assert run.status_code == 200, run.text
        assert run.json()["case_id"] == case_id

        graph = client.get(f"/guided/v1/cases/{case_id}/graph", headers=_headers())
        assert graph.status_code == 200
        assert "same evidence store used by MCP" in graph.text


def test_guided_service_refuses_malformed_user_identity(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_GUIDED_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "none")
    mcp, _ = build_mcp_server(gateway=gateway)

    with TestClient(mcp.streamable_http_app()) as client:
        response = client.get("/guided/v1/cases", headers=_headers("../../operator"))
        assert response.status_code == 400
        assert "must be a UUID" in response.json()["error"]
