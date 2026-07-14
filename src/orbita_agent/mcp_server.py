from __future__ import annotations

import hmac
import json
import os
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import AgentConfig
from .gateway import AgentGateway

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
STATE_CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


class StaticBearerTokenVerifier:
    """Verify one deployment secret without logging or persisting it."""

    def __init__(self, expected_token: str) -> None:
        if len(expected_token) < 32:
            raise ValueError("ORBITA_AGENT_API_TOKEN must contain at least 32 characters")
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="orbita-agent-client",
            subject="orbita-operator",
            scopes=["orbita:use"],
        )


def _remote_auth(host: str, port: int) -> tuple[AuthSettings | None, StaticBearerTokenVerifier | None]:
    token = os.getenv("ORBITA_AGENT_API_TOKEN", "")
    require_auth = os.getenv("ORBITA_AGENT_REQUIRE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
    if require_auth and not token:
        raise RuntimeError("ORBITA_AGENT_REQUIRE_AUTH is enabled but ORBITA_AGENT_API_TOKEN is missing")
    if not token:
        return None, None

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    public_url = os.getenv("ORBITA_AGENT_PUBLIC_URL", "").strip()
    if not public_url and railway_domain:
        public_url = f"https://{railway_domain}"
    if not public_url:
        public_url = f"http://localhost:{port}"
    public_url = public_url.rstrip("/")
    settings = AuthSettings(
        issuer_url=public_url,
        resource_server_url=f"{public_url}/mcp",
        required_scopes=["orbita:use"],
    )
    return settings, StaticBearerTokenVerifier(token)


def build_mcp_server(
    *,
    config: AgentConfig | None = None,
    gateway: AgentGateway | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[FastMCP, AgentGateway]:
    gateway = gateway or AgentGateway(config)
    auth, token_verifier = _remote_auth(host, port)
    mcp = FastMCP(
        "Orbita Agent Research Server",
        instructions=(
            "Use Orbita to turn supplied research data into a frozen analysis plan, require explicit review, "
            "run bounded falsification checks, and preserve both survivors and refutations. Never describe an "
            "Orbita survivor as universal proof, causality, or novelty without independent warrant."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        auth=auth,
        token_verifier=token_verifier,
    )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "product": "orbita-agent-research-server",
                "version": "0.1.1",
                "authentication": "bearer" if token_verifier else "local-only-unconfigured",
            }
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_capabilities() -> dict[str, Any]:
        """Describe available research routes, hard limits, approval rules, and epistemic boundaries."""
        return gateway.capabilities()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_list_cases() -> list[dict[str, Any]]:
        """List local research cases without returning large plans or findings."""
        return gateway.list_cases()

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_create_case(name: str, goal: str = "", domain_hint: str | None = None) -> dict[str, Any]:
        """Create a research case. Leave goal blank for bounded open discovery."""
        return gateway.create_case(name=name, goal=goal, domain_hint=domain_hint)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_add_inline_file(case_id: str, filename: str, content: str) -> dict[str, Any]:
        """Attach a UTF-8 text/table file. CSV, TSV, JSON(L), Markdown, text, code, and notebooks are allowed."""
        return gateway.add_inline_file(case_id=case_id, filename=filename, content=content)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_case_context(case_id: str) -> dict[str, Any]:
        """Read deterministic data profiles, plan versions, and compact run state before proposing analysis."""
        return gateway.case_context(case_id)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_compile_plan(case_id: str, max_candidates: int = 60) -> dict[str, Any]:
        """Compile a deterministic frozen plan; this proposes but does not approve or run it."""
        return gateway.compile_plan(case_id, max_candidates=max_candidates)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_submit_plan(case_id: str, plan: dict[str, Any], compiler: str = "external-ai") -> dict[str, Any]:
        """Submit an AI-authored plan using only fields visible in case context; it remains unapproved."""
        return gateway.submit_plan(case_id, plan=plan, compiler=compiler)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_plan(plan_id: str) -> dict[str, Any]:
        """Fetch the complete immutable plan and its SHA-256 hash for review."""
        return gateway.get_plan(plan_id)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_approve_plan(
        plan_id: str,
        expected_plan_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Approve one exact frozen plan after review. Use the exact confirmation phrase reported by capabilities."""
        return gateway.approve_plan(
            plan_id,
            expected_plan_hash=expected_plan_hash,
            reviewer=reviewer,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_run_discovery(case_id: str, plan_id: str) -> dict[str, Any]:
        """Execute an already approved plan and persist findings, failed checks, claims, and reports."""
        return gateway.run_discovery(case_id, plan_id=plan_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_run(run_id: str, findings_offset: int = 0, findings_limit: int = 50) -> dict[str, Any]:
        """Read a paginated run result, including both surviving and refuted candidates."""
        return gateway.get_run(run_id, findings_offset=findings_offset, findings_limit=findings_limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_case_claims(case_id: str) -> list[dict[str, Any]]:
        """List durable claims linked to a research case."""
        return gateway.case_claims(case_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_claim_history(claim_id: str) -> dict[str, Any]:
        """Reconstruct a claim's evidence, checks, contradictions, dependencies, events, and supersession chain."""
        return gateway.claim_history(claim_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_claim_impact(claim_id: str) -> dict[str, Any]:
        """Show which downstream claims depend on a claim and how support can collapse."""
        return gateway.claim_impact(claim_id)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_add_contradiction(claim_a: str, claim_b: str, rationale: str) -> dict[str, Any]:
        """Record—not erase—a specific contradiction between two durable claims."""
        return gateway.add_contradiction(claim_a, claim_b, rationale=rationale)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_supersede_claim(claim_id: str, new_statement: str, rationale: str) -> dict[str, Any]:
        """Create a replacement claim while retaining the prior claim and full history."""
        return gateway.supersede_claim(claim_id, new_statement=new_statement, rationale=rationale)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_reexamination_queue() -> list[dict[str, Any]]:
        """List open review tasks created by contradictions, revocations, or dependency collapse."""
        return gateway.reexamination_queue()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_report(case_id: str, format: Literal["markdown", "json", "html"] = "markdown") -> dict[str, Any]:
        """Read the newest completed case dossier in Markdown, JSON, or HTML."""
        return gateway.report(case_id, format=format)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_search_knowledge(query: str, limit: int = 5, source_bundle: str | None = None) -> list[dict[str, Any]]:
        """Search curated math/discovery research with original bundle paths and snippets."""
        return gateway.search_knowledge(query, limit=limit, source_bundle=source_bundle)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_knowledge_claims(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Read structured historical claim cards, optionally filtered by exact epistemic status."""
        return gateway.knowledge_claims(status=status, limit=limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_eg_summary() -> dict[str, Any]:
        """Summarize the preserved Erdős–Gyárfás finite search with its proof-status boundary."""
        return gateway.eg_summary()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_eg_near_misses(
        limit: int = 10,
        min_n: int = 0,
        include_certificate: bool = False,
    ) -> list[dict[str, Any]]:
        """Find deduplicated no-C4 finite graphs; optionally include a few exact edge/cycle certificates."""
        return gateway.eg_near_misses(limit=limit, min_n=min_n, include_certificate=include_certificate)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_analyze_graph(
        n: int,
        edges: list[list[int]],
        timeout_seconds: float = 2.0,
        max_states: int = 500_000,
    ) -> dict[str, Any]:
        """Run bounded exact cycle-witness and necessary-condition analysis on a finite simple graph."""
        return gateway.analyze_graph(n=n, edges=edges, timeout_seconds=timeout_seconds, max_states=max_states)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_export_lean_witness(
        n: int,
        edges: list[list[int]],
        cycle: list[int],
        project_name: str = "lean_certificate",
    ) -> dict[str, Any]:
        """Validate and export a complete Lean project for one finite power-of-two cycle witness."""
        return gateway.export_lean_witness(n=n, edges=edges, cycle=cycle, project_name=project_name)

    @mcp.resource("orbita://cases/{case_id}")
    def case_resource(case_id: str) -> str:
        """Agent-readable case context."""
        return json.dumps(gateway.case_context(case_id), indent=2, sort_keys=True)

    @mcp.resource("orbita://claims/{claim_id}")
    def claim_resource(claim_id: str) -> str:
        """Agent-readable complete claim history."""
        return json.dumps(gateway.claim_history(claim_id), indent=2, sort_keys=True)

    @mcp.prompt()
    def orbita_research_protocol(goal: str = "Open discovery") -> str:
        """Create a cautious operating prompt for an Orbita research session."""
        return f"""You are operating Orbita as a research compiler, not the final judge.

Goal: {goal}

1. Inspect case context and deterministic file profiles.
2. Use only observed columns and declared units.
3. Propose a bounded candidate set, assumptions, artifact guards, and blocking questions.
4. Freeze and fetch the plan. Do not approve it without explicit authorization from the user.
5. After approval, run the plan and report survivors, refutations, failed checks, and limitations.
6. Describe associations as non-causal unless the design warrants causality.
7. Never call a finite survivor a universal proof or a novel discovery without external verification.
"""

    return mcp, gateway
