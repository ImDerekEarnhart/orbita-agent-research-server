from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from orbita.evaluation import default_adversarial_suite
from orbita_agent import __version__
from orbita_agent.mcp_server import StaticBearerTokenVerifier, _case_metadata, build_mcp_server


def test_case_metadata_reads_the_nested_gateway_case_view():
    context = {"case": {"name": "Ferrite study", "status": "active"}, "files": []}
    assert _case_metadata(context) == {"name": "Ferrite study", "status": "active"}
    assert _case_metadata({"case": None}) == {}


def test_mcp_surface_has_governed_tool_annotations(gateway):
    mcp, same_gateway = build_mcp_server(gateway=gateway)
    assert same_gateway is gateway
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    expected = {
        "orbita_capabilities",
        "orbita_create_case",
        "orbita_add_inline_file",
        "orbita_adjudicate_epistemic_task",
        "orbita_compress_epistemic_task",
        "orbita_compress_code_context",
        "orbita_build_language_snapshot",
        "orbita_audit_representation",
        "orbita_build_language_limit_certificate",
        "orbita_render_language_limit_lean_source",
        "orbita_discover_and_freeze_language_limit",
        "orbita_lean_verify_language_limit",
        "orbita_get_language_limit_verification",
        "orbita_list_case_language_limits",
        "orbita_propose_language_refinement",
        "orbita_test_frozen_language_refinement",
        "orbita_get_language_refinement",
        "orbita_build_language_repair_candidate",
        "orbita_materialize_language_transition",
        "orbita_build_capability_component_graph",
        "orbita_audit_temporal_unaskability",
        "orbita_general_problem_loop_status",
        "orbita_create_general_problem_loop",
        "orbita_list_general_problem_loops",
        "orbita_get_general_problem_loop",
        "orbita_advance_general_problem_loop",
        "orbita_verify_general_problem_loop",
        "orbita_compile_plan",
        "orbita_executor_registry_status",
        "orbita_list_candidate_execution_receipts",
        "orbita_get_candidate_execution_receipt",
        "orbita_verify_candidate_execution_receipt",
        "orbita_evidence_normalization_status",
        "orbita_list_normalized_evidence",
        "orbita_get_normalized_evidence",
        "orbita_verify_normalized_evidence",
        "orbita_check_evidence_eligibility",
        "orbita_normalize_discovery_run_evidence",
        "orbita_normalize_genome_tournament_evidence",
        "orbita_normalize_external_experiment_evidence",
        "orbita_approve_plan",
        "orbita_run_discovery",
        "orbita_blind_calibration_status",
        "orbita_get_blind_calibration",
        "orbita_get_blind_prediction_batch",
        "orbita_freeze_blind_predictions",
        "orbita_seal_blind_scoring_key",
        "orbita_approve_blind_reveal",
        "orbita_score_blind_calibration",
        "orbita_claim_history",
        "orbita_search_knowledge",
        "orbita_analyze_graph",
        "orbita_export_lean_witness",
        "orbita_improvement_status",
        "orbita_guard_claim_scope",
        "orbita_suggest_improvement",
        "orbita_evaluate_improvement",
        "orbita_promote_improvement",
        "orbita_rollback_improvement",
        "orbita_governed_improvement_status",
        "orbita_register_improvement_candidate",
        "orbita_list_governed_improvements",
        "orbita_get_governed_improvement",
        "orbita_freeze_improvement_evaluation",
        "orbita_record_governed_improvement_evaluation",
        "orbita_external_experiment_status",
        "orbita_freeze_external_experiment",
        "orbita_submit_external_experiment",
        "orbita_approve_external_experiment",
        "orbita_run_external_experiment",
        "orbita_record_external_verification",
        "orbita_get_external_experiment",
        "orbita_list_external_experiments",
        "orbita_prepare_external_reproduction",
        "orbita_approve_external_reproduction",
        "orbita_run_external_reproduction",
        "orbita_record_external_coverage_bug",
        "orbita_propagate_external_coverage_bug_to_claims",
        "orbita_prepare_coverage_reevaluation",
        "orbita_approve_coverage_reevaluation",
        "orbita_run_coverage_reevaluation",
        "orbita_record_coverage_resolutions",
        "orbita_get_coverage_bug",
        "orbita_genome_status",
        "orbita_genome_list_operators",
        "orbita_genome_list_graphs",
        "orbita_genome_programme_state",
        "orbita_genome_compile_programme_state",
        "orbita_genome_list_questions",
        "orbita_genome_generate_questions",
        "orbita_genome_create_operator",
        "orbita_genome_freeze_operator",
        "orbita_genome_create_tournament",
        "orbita_genome_freeze_tournament",
        "orbita_genome_hash_tournament_reveal",
        "orbita_genome_mark_tournament_revealed",
        "orbita_genome_record_result",
    }
    assert expected <= tools.keys()
    assert tools["orbita_capabilities"].annotations.readOnlyHint is True
    assert tools["orbita_adjudicate_epistemic_task"].annotations.readOnlyHint is True
    assert tools["orbita_adjudicate_epistemic_task"].annotations.destructiveHint is False
    assert tools["orbita_compress_epistemic_task"].annotations.readOnlyHint is True
    assert tools["orbita_compress_epistemic_task"].annotations.destructiveHint is False
    assert tools["orbita_compress_code_context"].annotations.readOnlyHint is True
    assert tools["orbita_compress_code_context"].annotations.destructiveHint is False
    assert tools["orbita_build_language_snapshot"].annotations.readOnlyHint is True
    assert tools["orbita_audit_representation"].annotations.readOnlyHint is True
    assert tools["orbita_build_language_limit_certificate"].annotations.readOnlyHint is True
    assert tools["orbita_render_language_limit_lean_source"].annotations.readOnlyHint is True
    assert tools["orbita_discover_and_freeze_language_limit"].annotations.destructiveHint is False
    assert tools["orbita_lean_verify_language_limit"].annotations.destructiveHint is False
    assert tools["orbita_get_language_limit_verification"].annotations.readOnlyHint is True
    assert tools["orbita_list_case_language_limits"].annotations.readOnlyHint is True
    assert tools["orbita_propose_language_refinement"].annotations.destructiveHint is False
    assert tools["orbita_test_frozen_language_refinement"].annotations.destructiveHint is False
    assert tools["orbita_get_language_refinement"].annotations.readOnlyHint is True
    assert tools["orbita_build_language_repair_candidate"].annotations.readOnlyHint is True
    assert tools["orbita_materialize_language_transition"].annotations.readOnlyHint is True
    assert tools["orbita_build_capability_component_graph"].annotations.readOnlyHint is True
    assert tools["orbita_audit_temporal_unaskability"].annotations.readOnlyHint is True
    assert tools["orbita_general_problem_loop_status"].annotations.readOnlyHint is True
    assert tools["orbita_create_general_problem_loop"].annotations.destructiveHint is False
    assert tools["orbita_list_general_problem_loops"].annotations.readOnlyHint is True
    assert tools["orbita_get_general_problem_loop"].annotations.readOnlyHint is True
    assert tools["orbita_advance_general_problem_loop"].annotations.destructiveHint is False
    assert tools["orbita_verify_general_problem_loop"].annotations.readOnlyHint is True
    assert tools["orbita_executor_registry_status"].annotations.readOnlyHint is True
    assert tools["orbita_list_candidate_execution_receipts"].annotations.readOnlyHint is True
    assert tools["orbita_get_candidate_execution_receipt"].annotations.readOnlyHint is True
    assert tools["orbita_verify_candidate_execution_receipt"].annotations.readOnlyHint is True
    assert tools["orbita_evidence_normalization_status"].annotations.readOnlyHint is True
    assert tools["orbita_list_normalized_evidence"].annotations.readOnlyHint is True
    assert tools["orbita_get_normalized_evidence"].annotations.readOnlyHint is True
    assert tools["orbita_verify_normalized_evidence"].annotations.readOnlyHint is True
    assert tools["orbita_check_evidence_eligibility"].annotations.readOnlyHint is True
    assert tools["orbita_normalize_discovery_run_evidence"].annotations.destructiveHint is False
    assert tools["orbita_normalize_genome_tournament_evidence"].annotations.destructiveHint is False
    assert tools["orbita_normalize_external_experiment_evidence"].annotations.destructiveHint is False
    assert tools["orbita_approve_plan"].annotations.destructiveHint is True
    assert tools["orbita_run_discovery"].annotations.destructiveHint is True
    assert tools["orbita_promote_improvement"].annotations.destructiveHint is True
    assert tools["orbita_rollback_improvement"].annotations.destructiveHint is True
    assert tools["orbita_governed_improvement_status"].annotations.readOnlyHint is True
    assert tools["orbita_guard_claim_scope"].annotations.readOnlyHint is True
    assert tools["orbita_register_improvement_candidate"].annotations.destructiveHint is False
    assert tools["orbita_record_governed_improvement_evaluation"].annotations.destructiveHint is False
    assert tools["orbita_external_experiment_status"].annotations.readOnlyHint is True
    assert tools["orbita_freeze_external_experiment"].annotations.destructiveHint is False
    assert tools["orbita_approve_external_experiment"].annotations.destructiveHint is True
    assert tools["orbita_run_external_experiment"].annotations.destructiveHint is True
    assert tools["orbita_get_external_experiment"].annotations.readOnlyHint is True
    assert tools["orbita_prepare_external_reproduction"].annotations.destructiveHint is False
    assert tools["orbita_approve_external_reproduction"].annotations.destructiveHint is True
    assert tools["orbita_run_external_reproduction"].annotations.destructiveHint is True
    assert tools["orbita_record_external_coverage_bug"].annotations.destructiveHint is False
    assert tools["orbita_propagate_external_coverage_bug_to_claims"].annotations.destructiveHint is False
    assert tools["orbita_prepare_coverage_reevaluation"].annotations.destructiveHint is False
    assert tools["orbita_approve_coverage_reevaluation"].annotations.destructiveHint is True
    assert tools["orbita_run_coverage_reevaluation"].annotations.destructiveHint is True
    assert tools["orbita_record_coverage_resolutions"].annotations.destructiveHint is False
    assert tools["orbita_get_coverage_bug"].annotations.readOnlyHint is True
    assert tools["orbita_genome_status"].annotations.readOnlyHint is True
    assert tools["orbita_genome_list_questions"].annotations.readOnlyHint is True
    assert tools["orbita_genome_generate_questions"].annotations.readOnlyHint is False
    assert tools["orbita_genome_freeze_operator"].annotations.destructiveHint is True
    assert tools["orbita_genome_freeze_tournament"].annotations.destructiveHint is True
    assert tools["orbita_genome_mark_tournament_revealed"].annotations.destructiveHint is True
    assert tools["orbita_genome_record_result"].annotations.destructiveHint is True
    assert tools["orbita_approve_plan"].description
    assert tools["orbita_blind_calibration_status"].annotations.readOnlyHint is True
    assert tools["orbita_get_blind_calibration"].annotations.readOnlyHint is True
    assert tools["orbita_get_blind_prediction_batch"].annotations.destructiveHint is False
    assert tools["orbita_freeze_blind_predictions"].annotations.destructiveHint is False
    assert tools["orbita_seal_blind_scoring_key"].annotations.destructiveHint is False
    assert tools["orbita_approve_blind_reveal"].annotations.destructiveHint is True
    assert tools["orbita_score_blind_calibration"].annotations.destructiveHint is True


def test_runtime_version_metadata_matches_package(gateway, monkeypatch):
    monkeypatch.setattr(gateway.knowledge, "status", lambda: {})
    assert gateway.capabilities()["version"] == __version__ == "0.10.0"


def test_capabilities_executes_through_the_real_mcp_surface(gateway, monkeypatch):
    monkeypatch.setattr(gateway.knowledge, "status", lambda: {"status": "ready"})
    mcp, _ = build_mcp_server(gateway=gateway)

    _content, structured = asyncio.run(mcp.call_tool("orbita_capabilities", {}))

    assert structured["version"] == __version__
    assert structured["self_improvement"]["mode"] == "bounded_policy_improvement"
    assert structured["archive_intake"]["encryption_reviewed"] is False
    assert structured["archive_processing"]["zip_strategy"] == "complete_inventory_bounded_selective_parse"
    assert structured["archive_intake"]["max_upload_bytes"] > 0


def test_mcp_schemas_are_machine_usable(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    approval = tools["orbita_approve_plan"].parameters
    assert set(approval["required"]) == {"plan_id", "expected_plan_hash", "reviewer", "confirmation"}
    graph = tools["orbita_analyze_graph"].parameters
    assert graph["properties"]["edges"]["type"] == "array"
    genome_hash = tools["orbita_genome_hash_result"].parameters
    assert set(genome_hash["required"]) == {"tournament_id", "entry_id", "verdict", "result"}
    genome_reveal_hash = tools["orbita_genome_hash_tournament_reveal"].parameters
    assert set(genome_reveal_hash["required"]) == {
        "tournament_id",
        "expected_manifest_hash",
        "reveal",
    }
    adjudication = tools["orbita_adjudicate_epistemic_task"].parameters
    assert set(adjudication["required"]) == {"task"}
    assert adjudication["properties"]["task"]["type"] == "object"
    compression = tools["orbita_compress_epistemic_task"].parameters
    assert set(compression["required"]) == {"task"}
    assert compression["properties"]["max_context_items"]["default"] == 8
    code_compression = tools["orbita_compress_code_context"].parameters
    assert set(code_compression["required"]) == {"issue", "files"}
    assert code_compression["properties"]["max_files"]["default"] == 6
    language_snapshot = tools["orbita_build_language_snapshot"].parameters
    assert set(language_snapshot["required"]) == {"spec"}
    representation_audit = tools["orbita_audit_representation"].parameters
    assert set(representation_audit["required"]) == {"snapshot", "cases"}


def test_language_snapshot_executes_through_real_mcp_surface(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    spec = {
        "name": "Minimal language",
        "version": "L0",
        "primitives": [
            {
                "name": "identity",
                "kind": "observable",
                "inputs": ["value"],
                "output": "value",
                "semantics": {"operator": "identity"},
                "dependencies": [],
            }
        ],
        "observables": ["value"],
        "refusal_conditions": [],
        "unknown_conditions": ["not identified"],
        "read_permissions": ["visible values"],
        "write_permissions": [],
        "grounding_rules": ["declarative semantics required"],
        "invariants": ["UNKNOWN != FALSE"],
    }

    _content, snapshot = asyncio.run(mcp.call_tool("orbita_build_language_snapshot", {"spec": spec}))

    assert snapshot["schema"] == "orbita-language-snapshot/1"
    assert snapshot["active"] is False
    assert len(snapshot["snapshot_hash"]) == 64


def test_general_problem_loop_executes_through_real_mcp_surface(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    _content, loop = asyncio.run(
        mcp.call_tool(
            "orbita_create_general_problem_loop",
            {
                "goal": "Determine whether a finite claim survives its frozen checks.",
                "success_criteria": ["All checks pass"],
                "allowed_capabilities": ["finite_checker"],
                "max_cycles": 1,
                "created_by": "mcp-test",
            },
        )
    )
    assert loop["current_state"] == "REPRESENT"
    assert loop["activation_enabled"] is False
    _content, verified = asyncio.run(mcp.call_tool("orbita_verify_general_problem_loop", {"loop_id": loop["id"]}))
    assert verified["valid"] is True


def test_adjudication_tool_executes_through_the_real_mcp_surface(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    task = default_adversarial_suite().tasks[0].public_dict()

    _content, structured = asyncio.run(mcp.call_tool("orbita_adjudicate_epistemic_task", {"task": task}))

    assert structured["task_id"] == "model_repetition_is_not_evidence"
    assert structured["claim_judgments"][0]["state"] == "unknown"
    assert structured["decision_basis"]["model_calls"] == 0
    assert structured["decision_basis"]["network_calls"] == 0


def test_compression_tool_executes_through_the_real_mcp_surface(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    task = default_adversarial_suite().tasks[0].public_dict()
    task["context"].append(
        {"id": "irrelevant_weather", "kind": "narrative_record", "text": "It rained elsewhere."}
    )

    _content, structured = asyncio.run(
        mcp.call_tool(
            "orbita_compress_epistemic_task",
            {"task": task, "max_context_items": 3},
        )
    )

    assert structured["receipt"]["model_calls"] == 0
    assert structured["receipt"]["network_calls"] == 0
    assert "irrelevant_weather" in structured["receipt"]["dropped_ids"]


def test_code_compression_tool_executes_through_the_real_mcp_surface(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    files = [
        {
            "path": "retry_logic.py",
            "content": "def run_with_retries(operation):\n    return operation()\n",
        },
        {
            "path": "weather.py",
            "content": "def rainfall_total(values):\n    return sum(values)\n",
        },
    ]

    _content, structured = asyncio.run(
        mcp.call_tool(
            "orbita_compress_code_context",
            {
                "issue": "run_with_retries fails to retry an operation",
                "files": files,
                "max_files": 1,
            },
        )
    )

    assert structured["receipt"]["retained_paths"] == ["retry_logic.py"]
    assert structured["receipt"]["model_calls"] == 0


def test_static_bearer_token_verifier():
    token = "a" * 48
    verifier = StaticBearerTokenVerifier(token)
    assert asyncio.run(verifier.verify_token("not-the-token")) is None
    access = asyncio.run(verifier.verify_token(token))
    assert access is not None
    assert access.scopes == ["orbita:use"]


def test_remote_auth_is_required_when_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "bearer")
    monkeypatch.delenv("ORBITA_AGENT_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Bearer mode is enabled"):
        build_mcp_server(gateway=gateway)


def test_remote_auth_and_health_route_are_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "bearer")
    monkeypatch.setenv("ORBITA_AGENT_API_TOKEN", "b" * 48)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None
    assert str(mcp.settings.auth.resource_server_url) == "https://orbita.example.test/mcp"
    assert mcp._token_verifier is not None
    assert any(route.path == "/health" for route in mcp._custom_starlette_routes)


def test_github_oauth_mode_is_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None
    assert mcp.settings.auth.client_registration_options.enabled is True
    assert mcp.settings.auth.revocation_options.enabled is True
    assert mcp._auth_server_provider is not None
    assert str(mcp.settings.auth.resource_server_url) == "https://orbita.example.test/mcp"
    assert any(route.path == "/oauth/github/callback" for route in mcp._custom_starlette_routes)


def _genome_oauth_env(monkeypatch, allowed_users: str) -> None:
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", allowed_users)
    monkeypatch.setenv("ORBITA_DISCOVERY_GENOME_URL", "https://guided.example")
    monkeypatch.setenv("ORBITA_DISCOVERY_GENOME_SERVICE_TOKEN", "t" * 48)
    monkeypatch.delenv("ORBITA_GENOME_TENANT_BINDINGS", raising=False)
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_USERNAME", raising=False)


def test_genome_bridge_refuses_multiple_principals_without_tenant_bindings(gateway, monkeypatch):
    _genome_oauth_env(monkeypatch, "DerekEarnhart,SecondUser")

    with pytest.raises(RuntimeError, match="no Discovery Genome tenant bindings exist"):
        build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)


def test_genome_bridge_serves_multiple_principals_once_they_are_bound(gateway, monkeypatch):
    _genome_oauth_env(monkeypatch, "DerekEarnhart,SecondUser")
    monkeypatch.setenv(
        "ORBITA_GENOME_TENANT_BINDINGS",
        json.dumps({"github:1": "derek-tenant", "github:2": "second-tenant"}),
    )

    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None


def test_single_principal_deployment_still_starts_unchanged(gateway, monkeypatch):
    """The current production configuration must keep working with no variable changes."""
    _genome_oauth_env(monkeypatch, "DerekEarnhart")
    monkeypatch.setenv("ORBITA_DISCOVERY_GENOME_USERNAME", "dkscr711")

    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None


def test_github_oauth_discovery_registration_and_challenge(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart")
    monkeypatch.setenv("ORBITA_AGENT_PUBLIC_URL", "https://orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    with TestClient(mcp.streamable_http_app()) as client:
        authorization_metadata = client.get("/.well-known/oauth-authorization-server")
        assert authorization_metadata.status_code == 200
        assert authorization_metadata.json()["authorization_endpoint"] == "https://orbita.example.test/authorize"
        assert authorization_metadata.json()["registration_endpoint"] == "https://orbita.example.test/register"
        assert authorization_metadata.json()["code_challenge_methods_supported"] == ["S256"]

        resource_metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        assert resource_metadata.status_code == 200
        assert resource_metadata.json()["resource"] == "https://orbita.example.test/mcp"
        assert resource_metadata.json()["scopes_supported"] == ["orbita:use"]

        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://chatgpt.example.test/oauth/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "orbita:use",
                "client_name": "ChatGPT",
            },
        )
        assert registration.status_code == 201
        assert registration.json()["client_id"]
        assert registration.json().get("client_secret") is None

        oauth_provider = mcp._auth_server_provider

        async def allowed_github_user(_code):
            return {"login": "DerekEarnhart", "id": 1234}

        monkeypatch.setattr(oauth_provider, "_fetch_github_user", allowed_github_user)
        code_verifier = "test-verifier-" * 5
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        authorization = client.get(
            "/authorize",
            params={
                "client_id": registration.json()["client_id"],
                "redirect_uri": "https://chatgpt.example.test/oauth/callback",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": "chatgpt-state",
                "scope": "orbita:use",
                "resource": "https://orbita.example.test/mcp",
            },
            follow_redirects=False,
        )
        assert authorization.status_code == 302
        github_state = parse_qs(urlparse(authorization.headers["location"]).query)["state"][0]
        callback = client.get(
            "/oauth/github/callback",
            params={"code": "github-code", "state": github_state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        callback_query = parse_qs(urlparse(callback.headers["location"]).query)
        assert callback_query["state"] == ["chatgpt-state"]
        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": callback_query["code"][0],
                "redirect_uri": "https://chatgpt.example.test/oauth/callback",
                "client_id": registration.json()["client_id"],
                "code_verifier": code_verifier,
                "resource": "https://orbita.example.test/mcp",
            },
        )
        assert token.status_code == 200
        assert token.json()["token_type"] == "Bearer"
        assert token.json()["scope"] == "orbita:use"
        assert token.json()["refresh_token"]

        challenge = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert challenge.status_code == 401
        assert "oauth-protected-resource/mcp" in challenge.headers["www-authenticate"]


def test_genome_tools_route_each_authenticated_subject_to_its_own_tenant(gateway, monkeypatch):
    """The core multi-tenancy guarantee, exercised through the real MCP tool path."""
    _genome_oauth_env(monkeypatch, "DerekEarnhart,SecondUser")
    monkeypatch.setenv(
        "ORBITA_GENOME_TENANT_BINDINGS",
        json.dumps({"github:1": "derek-tenant", "github:2": "second-tenant"}),
    )
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    sent_users: list[str] = []

    class FakeResponse:
        def read(self):
            return b'{"operators": []}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout=None):
        sent_users.append(request.headers["X-orbita-genome-user"])
        return FakeResponse()

    monkeypatch.setattr("orbita_agent.genome_client.urlopen", fake_urlopen)

    def as_subject(subject):
        class FakeToken:
            def __init__(self):
                self.subject = subject

        monkeypatch.setattr("orbita_agent.mcp_server.get_access_token", lambda: FakeToken())

    as_subject("github:1")
    asyncio.run(mcp.call_tool("orbita_genome_list_operators", {}))
    as_subject("github:2")
    asyncio.run(mcp.call_tool("orbita_genome_list_operators", {}))

    assert sent_users == ["derek-tenant", "second-tenant"]


def test_genome_tools_refuse_an_authenticated_but_unbound_identity(gateway, monkeypatch):
    _genome_oauth_env(monkeypatch, "DerekEarnhart,SecondUser")
    monkeypatch.setenv("ORBITA_GENOME_TENANT_BINDINGS", json.dumps({"github:1": "derek-tenant"}))
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    def explode(*_args, **_kwargs):
        raise AssertionError("an unbound identity must never reach the Genome service")

    monkeypatch.setattr("orbita_agent.genome_client.urlopen", explode)

    class FakeToken:
        subject = "github:999"

    monkeypatch.setattr("orbita_agent.mcp_server.get_access_token", lambda: FakeToken())

    with pytest.raises(Exception, match="no Discovery Genome tenant is bound"):
        asyncio.run(mcp.call_tool("orbita_genome_list_operators", {}))


def _complete_github_signin(mcp, client, monkeypatch, *, login: str, github_id: int) -> None:
    """Drive a full OAuth sign-in so the identity is observed by the tenant registry."""
    registration = client.post(
        "/register",
        json={
            "redirect_uris": ["https://chatgpt.example.test/oauth/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "orbita:use",
            "client_name": "ChatGPT",
        },
    )

    async def github_user(_code):
        return {"login": login, "id": github_id}

    monkeypatch.setattr(mcp._auth_server_provider, "_fetch_github_user", github_user)
    code_verifier = "test-verifier-" * 5
    digest = hashlib.sha256(code_verifier.encode()).digest()
    authorization = client.get(
        "/authorize",
        params={
            "client_id": registration.json()["client_id"],
            "redirect_uri": "https://chatgpt.example.test/oauth/callback",
            "response_type": "code",
            "code_challenge": base64.urlsafe_b64encode(digest).decode().rstrip("="),
            "code_challenge_method": "S256",
            "state": "chatgpt-state",
            "scope": "orbita:use",
            "resource": "https://orbita.example.test/mcp",
        },
        follow_redirects=False,
    )
    github_state = parse_qs(urlparse(authorization.headers["location"]).query)["state"][0]
    client.get(
        "/oauth/github/callback",
        params={"code": "github-code", "state": github_state},
        follow_redirects=False,
    )


def test_single_principal_signin_auto_binds_the_legacy_tenant(gateway, monkeypatch):
    """End-to-end backward compatibility: today's deployment keeps reaching its tenant."""
    _genome_oauth_env(monkeypatch, "DerekEarnhart")
    monkeypatch.setenv("ORBITA_DISCOVERY_GENOME_USERNAME", "dkscr711")
    monkeypatch.setenv("ORBITA_AGENT_PUBLIC_URL", "https://orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    with TestClient(mcp.streamable_http_app()) as client:
        _complete_github_signin(mcp, client, monkeypatch, login="DerekEarnhart", github_id=1234)

    sent_users: list[str] = []

    class FakeResponse:
        def read(self):
            return b'{"operators": []}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout=None):
        sent_users.append(request.headers["X-orbita-genome-user"])
        return FakeResponse()

    monkeypatch.setattr("orbita_agent.genome_client.urlopen", fake_urlopen)

    class FakeToken:
        subject = "github:1234"

    monkeypatch.setattr("orbita_agent.mcp_server.get_access_token", lambda: FakeToken())
    asyncio.run(mcp.call_tool("orbita_genome_list_operators", {}))
    assert sent_users == ["dkscr711"]
