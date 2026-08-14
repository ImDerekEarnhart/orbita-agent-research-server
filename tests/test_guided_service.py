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
        findings = run.json()["findings_page"]["items"]
        assert findings
        assert all("evidence_status" in finding for finding in findings)
        assert all("claim_scope" in finding for finding in findings)
        assert all("falsification_coverage" in finding for finding in findings)
        assert all(
            finding["evidence_status"] != "FORMALLY_PROVED" for finding in findings
        )
        claim_ids = run.json()["summary"]["belief_import"]["claim_ids"]
        assert claim_ids
        guided_tenant = f"g-{hashlib.sha256(ALICE.encode('ascii')).hexdigest()[:32]}"
        tenant_gateway = gateway.for_tenant(guided_tenant)
        try:
            history = tenant_gateway.claim_history(claim_ids[0])
            assert history["current_epistemic_contract"] is not None
            assert history["current_epistemic_contract"]["evidence_status"] in {
                "EMPIRICAL_SURVIVOR",
                "PROVISIONAL",
                "REFUTED",
                "UNRESOLVED",
            }
        finally:
            tenant_gateway.close()

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


def test_guided_api_prepares_sanitized_blind_prediction_batch(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_GUIDED_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "none")
    mcp, _ = build_mcp_server(gateway=gateway)
    blind_tsv = (
        "blind_event_id\tvisible_note\n"
        "B-01\tsteady point light\n"
        "B-02\tshort sensor dropout\n"
        "B-03\tdistant navigation lights\n"
    )
    with TestClient(mcp.streamable_http_app()) as client:
        created = client.post(
            "/guided/v1/cases",
            headers=_headers(),
            json={"name": "Guided blind calibration", "goal": "Predict before reveal"},
        )
        case_id = created.json()["case_id"]
        uploaded = client.post(
            f"/guided/v1/cases/{case_id}/files",
            headers=_headers(),
            files={"file": ("blind.tsv", blind_tsv, "text/tab-separated-values")},
        )
        file_record = uploaded.json()
        plan_body = {
            "schema_version": "orbita-research-plan/0.1",
            "selected_dataset": {"file_id": file_record["id"]},
            "thresholds": {},
            "candidates": [
                {
                    "id": "guided-blind-1",
                    "statement": "Classify sanitized rows before gold reveal.",
                    "kind": "prospective_blind_calibration",
                    "visible_fields": ["blind_event_id", "visible_note"],
                    "scoring_key_fields": ["gold_label", "unresolved_holdout"],
                    "allowed_hypotheses": ["ORDINARY", "ARTIFACT", "INSUFFICIENT_DATA"],
                    "allowed_epistemic_labels": ["PROSAIC_LIKELY", "UNRESOLVED"],
                    "allowed_evidence_classes": ["SENSOR_LOG"],
                    "prediction_provider": {"kind": "external_submission"},
                    "expected_row_count": 3,
                    "scoring_schema": {
                        "gold_event_id_field": "blind_event_id",
                        "gold_primary_label_field": "gold_label",
                    },
                }
            ],
        }
        submitted = client.post(
            f"/guided/v1/cases/{case_id}/plans",
            headers=_headers(),
            json={"plan": plan_body, "compiler": "guided-test"},
        )
        assert submitted.status_code == 201, submitted.text
        plan = submitted.json()
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
        prepared = client.post(
            f"/guided/v1/cases/{case_id}/run",
            headers=_headers(),
            json={"plan_id": plan["plan_id"]},
        )
        assert prepared.status_code == 200, prepared.text
        protocol = prepared.json()
        assert protocol["status"] == "awaiting_predictions"
        batch = client.get(
            f"/guided/v1/blind/{protocol['id']}/batch", headers=_headers()
        )
        assert batch.status_code == 200, batch.text
        assert len(batch.json()["rows"]) == 3
        assert batch.json()["scoring_key_available"] is False
        assert all("gold_label" not in row for row in batch.json()["rows"])
