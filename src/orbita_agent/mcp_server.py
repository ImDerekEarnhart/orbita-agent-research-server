from __future__ import annotations

import functools
import hashlib
import hmac
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .adjudication import adjudicate_epistemic_task, compress_epistemic_task
from .archive_policy import ArchiveIngestionRefused, ArchivePolicy
from .code_context import compress_code_context
from .config import AgentConfig
from .gateway import AgentGateway
from .genome_client import (
    OPERATOR_FREEZE_PHRASE,
    RESULT_RECORD_PHRASE,
    TOURNAMENT_FREEZE_PHRASE,
    DiscoveryGenomeClient,
    DiscoveryGenomeError,
    hash_json,
    tournament_result_receipt,
)
from .guided_service import install_guided_service_routes
from .oauth import ORBITA_SCOPE, GitHubOAuthProvider
from .object_store import ObjectStoreError
from .tenancy import LegacySinglePrincipal, TenantResolutionError, build_registry
from .uploads import UploadError, UploadTicketStore, max_upload_bytes

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
STATE_CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def _case_metadata(context: dict[str, Any]) -> dict[str, Any]:
    case = context.get("case")
    return case if isinstance(case, dict) else {}


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


@dataclass(frozen=True)
class RemoteAuth:
    settings: AuthSettings | None
    token_verifier: StaticBearerTokenVerifier | None
    oauth_provider: GitHubOAuthProvider | None
    label: str


def _public_url(host: str, port: int) -> str:
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    public_url = os.getenv("ORBITA_AGENT_PUBLIC_URL", "").strip()
    if not public_url and railway_domain:
        public_url = f"https://{railway_domain}"
    if not public_url:
        public_url = f"http://localhost:{port}" if host in {"0.0.0.0", "::"} else f"http://{host}:{port}"
    return public_url.rstrip("/")


def _integer_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _remote_auth(
    config: AgentConfig,
    host: str,
    port: int,
    *,
    on_identity: Callable[[str, str], None] | None = None,
) -> RemoteAuth:
    token = os.getenv("ORBITA_AGENT_API_TOKEN", "")
    require_auth = os.getenv("ORBITA_AGENT_REQUIRE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
    auth_mode = os.getenv("ORBITA_AGENT_AUTH_MODE", "").strip().lower()
    if not auth_mode:
        auth_mode = "oauth-github" if os.getenv("ORBITA_OAUTH_GITHUB_CLIENT_ID") else "bearer" if token else "none"
    if auth_mode not in {"oauth-github", "bearer", "none"}:
        raise RuntimeError("ORBITA_AGENT_AUTH_MODE must be oauth-github, bearer, or none")
    if require_auth and auth_mode == "none":
        raise RuntimeError("ORBITA_AGENT_REQUIRE_AUTH is enabled but ORBITA_AGENT_AUTH_MODE is none")

    public_url = _public_url(host, port)
    if auth_mode == "oauth-github":
        github_client_id = os.getenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "").strip()
        github_client_secret = os.getenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "").strip()
        allowed_users = os.getenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "").split(",")
        missing = [
            name
            for name, value in (
                ("ORBITA_OAUTH_GITHUB_CLIENT_ID", github_client_id),
                ("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", github_client_secret),
                ("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", ",".join(allowed_users).strip(", ")),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"OAuth mode is enabled but these variables are missing: {', '.join(missing)}")
        provider = GitHubOAuthProvider(
            database_path=config.home / "orbita_oauth.db",
            public_url=public_url,
            github_client_id=github_client_id,
            github_client_secret=github_client_secret,
            allowed_github_users=allowed_users,
            access_token_ttl=_integer_env("ORBITA_OAUTH_ACCESS_TOKEN_TTL", 3600),
            refresh_token_ttl=_integer_env("ORBITA_OAUTH_REFRESH_TOKEN_TTL", 30 * 24 * 3600),
            on_identity=on_identity,
        )
        settings = AuthSettings(
            issuer_url=public_url,
            service_documentation_url=f"{public_url}/",
            resource_server_url=f"{public_url}/mcp",
            required_scopes=[ORBITA_SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[ORBITA_SCOPE],
                default_scopes=[ORBITA_SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        return RemoteAuth(settings=settings, token_verifier=None, oauth_provider=provider, label="oauth-github")

    if auth_mode == "none":
        return RemoteAuth(settings=None, token_verifier=None, oauth_provider=None, label="none")
    if not token:
        raise RuntimeError("Bearer mode is enabled but ORBITA_AGENT_API_TOKEN is missing")
    settings = AuthSettings(
        issuer_url=public_url,
        resource_server_url=f"{public_url}/mcp",
        required_scopes=[ORBITA_SCOPE],
    )
    return RemoteAuth(
        settings=settings,
        token_verifier=StaticBearerTokenVerifier(token),
        oauth_provider=None,
        label="bearer",
    )


def build_mcp_server(
    *,
    config: AgentConfig | None = None,
    gateway: AgentGateway | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[FastMCP, AgentGateway]:
    gateway = gateway or AgentGateway(config)
    genome = DiscoveryGenomeClient()
    tenants = build_registry(gateway.config.home)
    remote_auth = _remote_auth(gateway.config, host, port, on_identity=tenants.record_identity)

    if not genome.config.missing() and remote_auth.label == "oauth-github":
        oauth_users = {
            value.strip().casefold()
            for value in os.getenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "").split(",")
            if value.strip()
        }
        # More than one allowed sign-in identity is fine, but only once every identity
        # can be resolved to its own tenant. Refusing here keeps a multi-user
        # deployment from silently serving one shared Genome tenant.
        if len(oauth_users) > 1 and LegacySinglePrincipal.from_env() is None and not tenants.list_bindings():
            raise RuntimeError(
                "Multiple GitHub OAuth users are allowed but no Discovery Genome tenant "
                "bindings exist; bind each identity with `orbita-agent tenants bind` or set "
                "ORBITA_GENOME_TENANT_BINDINGS before serving the Genome bridge"
            )

    def _resolve_genome_username() -> str:
        """Resolve the calling identity to exactly one Genome tenant, or refuse."""
        if remote_auth.label != "oauth-github":
            # Bearer and unauthenticated modes have a single deployment operator and no
            # way to distinguish callers, so multi-tenancy is not offered at all.
            username = os.getenv("ORBITA_DISCOVERY_GENOME_USERNAME", "").strip()
            if not username:
                raise TenantResolutionError(
                    "single-operator deployments must set ORBITA_DISCOVERY_GENOME_USERNAME"
                )
            return username
        token = get_access_token()
        return tenants.resolve(token.subject if token else None)

    @functools.lru_cache(maxsize=256)
    def _core_tenant_for_username(username: str) -> str:
        return genome.for_username(username).core_tenant_id()

    def _resolve_core_tenant() -> str:
        """Resolve the caller to the opaque tenant also used by Guided Orbita."""
        username = _resolve_genome_username()
        # A deployment without the Guided bridge keeps its historical username
        # workspace. Once the bridge is configured, fail closed unless it resolves
        # the exact opaque Guided tenant.
        if genome.config.missing():
            return username
        return _core_tenant_for_username(username)

    def _genome_for_caller() -> DiscoveryGenomeClient:
        try:
            return genome.for_username(_resolve_genome_username())
        except TenantResolutionError as exc:
            raise DiscoveryGenomeError(str(exc)) from exc

    tenant_gateways: dict[str, AgentGateway] = {}
    gateway_guard = threading.Lock()
    uploads = UploadTicketStore(gateway.config.home / "orbita_uploads.db")

    def _gateway_for_tenant(tenant: str) -> AgentGateway:
        with gateway_guard:
            existing = tenant_gateways.get(tenant)
            if existing is None:
                existing = gateway.for_tenant(tenant)
                tenant_gateways[tenant] = existing
            return existing

    def _gateway_for_caller() -> AgentGateway:
        """Return the calling tenant's own research workspace, or refuse.

        Bearer and unauthenticated deployments have a single operator and no way to
        tell callers apart, so they keep using the shared gateway exactly as before.
        Under OAuth every caller is resolved to its own tenant first, and an unbound
        subject is refused rather than quietly served the operator's own research.
        """
        if remote_auth.label != "oauth-github":
            return gateway
        return _gateway_for_tenant(_resolve_core_tenant())

    mcp = FastMCP(
        "Orbita Agent Research Server",
        instructions=(
            "Use Orbita to turn supplied research data into a frozen analysis plan, require explicit review, "
            "run bounded falsification checks, preserve both survivors and refutations, "
            "and test reusable methods in a tenant-scoped Discovery Genome. Never describe an "
            "Orbita survivor as universal proof, causality, or novelty without independent warrant."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        auth=remote_auth.settings,
        token_verifier=remote_auth.token_verifier,
        auth_server_provider=remote_auth.oauth_provider,
    )

    install_guided_service_routes(mcp, gateway_for_tenant=_gateway_for_tenant)

    if remote_auth.oauth_provider:

        @mcp.custom_route("/oauth/github/callback", methods=["GET"], include_in_schema=False)
        async def oauth_github_callback(request: Request):
            return await remote_auth.oauth_provider.github_callback(request)

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def service_documentation(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "product": "Orbita Agent Research Server",
                "version": __version__,
                "mcp": "/mcp",
                "health": "/health",
                "authentication": remote_auth.label,
                "documentation": "https://github.com/DerekEarnhart/orbita-agent-research-server",
            }
        )

    @mcp.custom_route("/uploads/{ticket}", methods=["POST", "PUT"], include_in_schema=False)
    async def receive_upload(request: Request) -> JSONResponse:
        """Accept the bytes for one minted ticket.

        This route carries no session of its own. The authenticated MCP call already
        decided the tenant, the case, the filename, and the ceiling; the ticket is the
        capability that carries that decision here, and it works exactly once.
        """
        try:
            claim = uploads.claim(request.path_params["ticket"])
        except UploadError as exc:
            # Unknown, spent, and expired tickets are one response, so probing cannot
            # distinguish "never existed" from "already used".
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)

        tenant_gateway = _gateway_for_tenant(claim.tenant)

        # Read the whole body before handing it on, because the gateway's storage
        # interface is synchronous while the request stream is not. The ticket ceiling is
        # applied here so an oversized body is cut off as it arrives rather than after.
        digest = hashlib.sha256()
        buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        written = 0
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > claim.max_bytes:
                    raise UploadError(
                        f"upload exceeded the {claim.max_bytes} byte ceiling for this ticket"
                    )
                digest.update(chunk)
                buffer.write(chunk)
            if written == 0:
                raise UploadError("upload was empty")
            buffer.seek(0)

            def _chunks() -> Iterator[bytes]:
                while True:
                    piece = buffer.read(1024 * 1024)
                    if not piece:
                        return
                    yield piece

            record = tenant_gateway.receive_upload(
                case_id=claim.case_id,
                filename=claim.filename,
                chunks=_chunks(),
                max_bytes=claim.max_bytes,
            )
        except (UploadError, ObjectStoreError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=413)
        except ArchiveIngestionRefused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
        except KeyError:
            return JSONResponse({"ok": False, "error": "unknown case"}, status_code=404)
        except Exception as exc:  # noqa: BLE001 - the client gets a type, never a traceback
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__} while ingesting the upload"},
                status_code=500,
            )
        finally:
            buffer.close()

        return JSONResponse(
            {
                "ok": True,
                "case_id": claim.case_id,
                "filename": claim.filename,
                "bytes_received": written,
                "sha256": digest.hexdigest(),
                "file": record,
            }
        )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "product": "orbita-agent-research-server",
                "version": __version__,
                "authentication": remote_auth.label,
            }
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_capabilities() -> dict[str, Any]:
        """Describe available research routes, hard limits, approval rules, and epistemic boundaries."""
        # Capabilities must stay readable before a tenant is bound, so this is the one
        # place that falls back to the base description instead of refusing. It reports
        # the caller's own active policy whenever the caller has a tenant, so no other
        # tenant's policy values are ever echoed back.
        try:
            described = _gateway_for_caller().capabilities()
        except TenantResolutionError:
            described = gateway.capabilities()
        return described | {
            "archive_intake": ArchivePolicy.from_env().describe()
            | {
                "max_upload_bytes": max_upload_bytes(),
                "transport": "single-use capability URL streamed to object storage",
            }
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_list_cases() -> list[dict[str, Any]]:
        """List local research cases without returning large plans or findings."""
        return _gateway_for_caller().list_cases()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_status() -> dict[str, Any]:
        """Check the tenant-scoped Discovery Genome bridge and report exact approval phrases."""
        deployment_missing = genome.config.missing()
        tenancy: dict[str, Any] = {
            "authentication": remote_auth.label,
            **tenants.describe(),
        }
        if deployment_missing:
            status: dict[str, Any] = {"configured": False, "missing": deployment_missing}
        else:
            try:
                bound = _genome_for_caller()
            except DiscoveryGenomeError as exc:
                # Status stays diagnosable when the caller has no tenant. It reports the
                # refusal instead of raising, and still never names another tenant.
                tenancy["tenant_bound"] = False
                tenancy["reason"] = str(exc)
                status = {"configured": True, "reachable": False}
            else:
                tenancy["tenant_bound"] = True
                status = {"configured": True, **bound.status()}

        return {
            **status,
            "tenancy": tenancy,
            "approval_phrases": {
                "freeze_operator": OPERATOR_FREEZE_PHRASE,
                "freeze_tournament": TOURNAMENT_FREEZE_PHRASE,
                "record_result": RESULT_RECORD_PHRASE,
            },
            "safety": {
                "tenant_selected_by": tenancy["tenant_selected_by"],
                "database_exposed": False,
                "automatic_policy_promotion": False,
            },
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_whoami() -> dict[str, Any]:
        """Report the authenticated subject and whether it resolves to a Genome tenant."""
        token = get_access_token() if remote_auth.label == "oauth-github" else None
        subject = token.subject if token else None
        try:
            _resolve_genome_username()
        except TenantResolutionError as exc:
            return {"subject": subject, "tenant_bound": False, "reason": str(exc)}
        # The resolved tenant name is deliberately not returned; callers only need to
        # know that their own identity is bound.
        return {"subject": subject, "tenant_bound": True}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_list_graphs() -> dict[str, Any]:
        """List this tenant's Guided memory graphs so an agent can select an auditable programme."""
        return _genome_for_caller().list_graphs()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_programme_state(graph_id: str) -> dict[str, Any]:
        """Read the latest compiled programme state for one tenant-owned Guided memory graph."""
        return _genome_for_caller().programme_state(graph_id)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_compile_programme_state(graph_id: str) -> dict[str, Any]:
        """Compile an auditable programme-state snapshot without accepting or executing any question."""
        return _genome_for_caller().compile_programme_state(graph_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_list_questions(graph_id: str) -> dict[str, Any]:
        """List review-needed follow-up questions generated for a tenant-owned Guided graph."""
        return _genome_for_caller().list_questions(graph_id)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_generate_questions(graph_id: str) -> dict[str, Any]:
        """Generate follow-up question cards from programme state; nothing is accepted or run."""
        return _genome_for_caller().generate_questions(graph_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_list_operators() -> dict[str, Any]:
        """List versioned discovery operators, executable contracts, states, and server-generated review hashes."""
        return _genome_for_caller().list_operators()

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_seed_operators() -> dict[str, Any]:
        """Idempotently seed the seven review-needed cross-domain operator families; nothing is frozen or proven."""
        return _genome_for_caller().seed_operators()

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_create_operator(
        operator_key: str,
        name: str,
        contract: dict[str, Any],
        description: str = "",
        source_case_id: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one review-needed executable operator, optionally linked to an existing Orbita research case."""
        provenance = dict(evidence or {})
        if source_case_id:
            case = _case_metadata(_gateway_for_caller().case_context(source_case_id))
            provenance.update(
                {
                    "source_case_id": source_case_id,
                    "source_case_name": case.get("name"),
                    "source_case_status": case.get("status"),
                    "provenance": "orbita_agent_case",
                }
            )
        payload: dict[str, Any] = {
            "operator_key": operator_key,
            "name": name,
            "description": description,
            "status": "review_needed",
            "contract": contract,
            "evidence": provenance,
        }
        if source_case_id:
            payload["source_operator_id"] = f"case:{source_case_id}"
        return _genome_for_caller().create_operator(payload)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_genome_freeze_operator(
        operator_id: str,
        expected_review_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Freeze exactly one reviewed operator contract; requires its current review hash and exact confirmation."""
        return _genome_for_caller().freeze_operator(
            operator_id,
            expected_review_hash=expected_review_hash,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_add_evidence(
        operator_id: str,
        case_id: str,
        domain: str,
        outcome: Literal["supported", "refuted", "inconclusive", "artifact"],
        independence_level: Literal["same_case", "same_family", "cross_domain", "external"],
        evidence: dict[str, Any] | None = None,
        receipt_hash: str | None = None,
    ) -> dict[str, Any]:
        """Attach one scoped case outcome to an operator without changing or erasing the underlying case."""
        _gateway_for_caller().case_context(case_id)
        return _genome_for_caller().add_operator_evidence(
            operator_id,
            {
                "case_id": case_id,
                "domain": domain,
                "outcome": outcome,
                "independence_level": independence_level,
                "evidence": evidence or {},
                "receipt_hash": receipt_hash,
            },
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_list_tournaments() -> dict[str, Any]:
        """List blind operator tournaments and their evaluated-entry counts."""
        return _genome_for_caller().list_tournaments()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_get_tournament(tournament_id: str) -> dict[str, Any]:
        """Read a tournament, entries, predictions, and its server-generated prospective manifest review hash."""
        return _genome_for_caller().get_tournament(tournament_id)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_create_tournament(name: str, target: dict[str, Any]) -> dict[str, Any]:
        """Create a draft blind-discovery tournament around one declared unseen target."""
        return _genome_for_caller().create_tournament({"name": name, "target": target})

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_genome_add_tournament_entry(
        tournament_id: str,
        operator_id: str,
        prediction: dict[str, Any],
    ) -> dict[str, Any]:
        """Add one frozen operator and its falsifiable vanish/recovery/refuter prediction to a draft tournament."""
        return _genome_for_caller().add_tournament_entry(
            tournament_id,
            {"operator_id": operator_id, "prediction": prediction},
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_genome_freeze_tournament(
        tournament_id: str,
        expected_review_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Freeze exactly the reviewed blind-tournament manifest; requires its current hash and exact confirmation."""
        return _genome_for_caller().freeze_tournament(
            tournament_id,
            expected_review_hash=expected_review_hash,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_genome_record_result(
        tournament_id: str,
        entry_id: str,
        verdict: Literal["survived", "refuted", "inconclusive"],
        result: dict[str, Any],
        expected_result_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Record a result once; requires the exact result hash and explicit human confirmation."""
        return _genome_for_caller().record_tournament_result(
            tournament_id,
            entry_id,
            verdict=verdict,
            result=result,
            expected_result_hash=expected_result_hash,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_genome_hash_result(
        tournament_id: str,
        entry_id: str,
        verdict: Literal["survived", "refuted", "inconclusive"],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Hash the exact target entry, verdict, and result payload together before confirmation."""
        reviewed_outcome = tournament_result_receipt(tournament_id, entry_id, verdict, result)
        return {"result_hash": hash_json(reviewed_outcome), **reviewed_outcome}

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_create_case(name: str, goal: str = "", domain_hint: str | None = None) -> dict[str, Any]:
        """Create a research case. Leave goal blank for bounded open discovery."""
        return _gateway_for_caller().create_case(name=name, goal=goal, domain_hint=domain_hint)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_add_inline_file(case_id: str, filename: str, content: str) -> dict[str, Any]:
        """Attach a UTF-8 text/table file. CSV, TSV, JSON(L), Markdown, text, code, and notebooks are allowed."""
        return _gateway_for_caller().add_inline_file(case_id=case_id, filename=filename, content=content)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_search_memory(
        query: str,
        limit: int = 20,
        case_id: str | None = None,
        role: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Search your own ingested chat archives. Every hit carries the conversation, node, and date it came from.

        This finds messages containing your terms. It does not summarize or interpret them,
        and a message being present means it was written, not that it was or is true.
        """
        return _gateway_for_caller().search_memory(
            query, limit=limit, case_id=case_id, role=role, conversation_id=conversation_id
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_memory_conversation(conversation_id: str, limit: int = 200) -> dict[str, Any]:
        """Read one archived conversation in order, to see a search hit in its original context."""
        return _gateway_for_caller().memory_conversation(conversation_id, limit=limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_memory_status() -> dict[str, Any]:
        """Report how much archive memory is indexed, over what date range, and from which roles."""
        return _gateway_for_caller().memory_status()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_find_reversals(
        case_id: str | None = None,
        role: str = "user",
        limit: int = 20,
        min_days_apart: float = 1.0,
    ) -> dict[str, Any]:
        """Surface places in your archive where you appear to have changed your mind, for you to judge.

        Pairs a later message in which you marked a change of position with an earlier
        message on the same subject. These are candidates, not contradictions: Orbita
        matched a self-correction marker and shared words, it did not read either
        statement. Nothing is written to the belief graph.
        """
        return _gateway_for_caller().find_reversals(
            case_id=case_id, role=role, limit=limit, min_days_apart=min_days_apart
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_delete_case(case_id: str, confirmation: str) -> dict[str, Any]:
        """Permanently delete a case, its uploaded files, and its indexed memory. Irreversible.

        Requires the exact phrase reported by orbita_capabilities. Returns a manifest of
        what was removed and confirms the bytes are gone from the volume. Claims derived
        from the case remain in the hash-chained ledger and are reported, not deleted.
        """
        return _gateway_for_caller().delete_case(case_id, confirmation=confirmation)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_forget_memory(case_id: str | None = None, everything: bool = False) -> dict[str, Any]:
        """Permanently delete indexed archive memory for one case, or all of it.

        This removes the searchable index only. The uploaded file itself stays in the case
        until the case is deleted, so this is not by itself a complete erasure.
        """
        return _gateway_for_caller().forget_memory(case_id=case_id, everything=everything)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_request_upload(case_id: str, filename: str, size_bytes: int) -> dict[str, Any]:
        """Mint a single-use URL for uploading one large file, such as a chat-history archive.

        Inline uploads are text-only and capped well below archive size. This returns a URL
        that accepts the raw bytes once, for this case and filename only, and then expires.
        Upload with: curl --request POST --data-binary @<file> "<upload_url>"
        """
        caller = _gateway_for_caller()
        # Resolve the case first, so an unknown case or another tenant's case fails here,
        # before any capability exists to be leaked.
        caller.case_context(case_id)
        tenant = _resolve_core_tenant() if remote_auth.label == "oauth-github" else None
        # Refuse before minting, so a tenant who may not store an archive never receives
        # a URL that would accept one.
        try:
            ArchivePolicy.from_env().ensure(tenant)
        except ArchiveIngestionRefused as exc:
            raise ValueError(str(exc)) from exc
        tenant = tenant or "operator"
        try:
            ticket, record = uploads.mint(
                tenant=tenant,
                case_id=case_id,
                filename=filename,
                declared_bytes=size_bytes,
                max_bytes=max_upload_bytes(),
                volume_path=gateway.config.home,
            )
        except UploadError as exc:
            raise ValueError(str(exc)) from exc
        return {
            **record.public(),
            "upload_url": f"{_public_url(host, port)}/uploads/{ticket}",
            "method": "POST",
            "single_use": True,
            "note": (
                "This URL is the authorization. Anyone holding it can write this one file to "
                "this one case until it is used or expires. Do not share it or paste it into logs."
            ),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_case_context(case_id: str) -> dict[str, Any]:
        """Read deterministic data profiles, plan versions, and compact run state before proposing analysis."""
        return _gateway_for_caller().case_context(case_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_adjudicate_epistemic_task(task: dict[str, Any]) -> dict[str, Any]:
        """Adjudicate one bounded public task with deterministic evidence rules and no model calls.

        Submit structured context, event sequence, and target IDs only. Gold labels
        are rejected. The tool is read-only and reports its exact evidence, proof,
        receipt, and replication basis plus explicit scope limitations.
        """
        return adjudicate_epistemic_task(task)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_compress_epistemic_task(
        task: dict[str, Any],
        max_context_items: int = 8,
    ) -> dict[str, Any]:
        """Select target-relevant context for a bounded public task without model calls.

        Gold labels are rejected. The result includes the compact task plus a
        receipt listing every retained and dropped context ID and exact size
        reduction. The source task is not mutated.
        """
        return compress_epistemic_task(task, max_context_items=max_context_items)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_compress_code_context(
        issue: str,
        files: list[dict[str, Any]],
        max_files: int = 6,
        max_characters: int = 80_000,
    ) -> dict[str, Any]:
        """Select issue-relevant code and test files without filesystem or model access."""
        return compress_code_context(
            issue,
            files,
            max_files=max_files,
            max_characters=max_characters,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_compile_plan(case_id: str, max_candidates: int | None = None) -> dict[str, Any]:
        """Compile with the active policy; optionally override only the bounded candidate budget."""
        return _gateway_for_caller().compile_plan(case_id, max_candidates=max_candidates)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_improvement_status() -> dict[str, Any]:
        """Read the active research policy, exact approval phrases, and self-improvement safety boundary."""
        return _gateway_for_caller().improvement_status()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_guard_claim_scope(
        evidence_scope: dict[str, Any],
        proposed_claim_scope: dict[str, Any],
    ) -> dict[str, Any]:
        """Check whether evidence can support the proposed scope; bounded evidence cannot become universal."""
        return _gateway_for_caller().guard_claim_scope(
            evidence_scope=evidence_scope,
            proposed_claim_scope=proposed_claim_scope,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_improvement_history(limit: int = 25) -> dict[str, Any]:
        """List versioned policies and recent proposals without mutating the active policy."""
        return _gateway_for_caller().improvement_history(limit=limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_improvement(candidate_id: str) -> dict[str, Any]:
        """Fetch an exact proposal, candidate hash, latest replay, and evaluation hash for review."""
        return _gateway_for_caller().get_improvement(candidate_id)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_suggest_improvement() -> dict[str, Any]:
        """Learn from completed runs and create one conservative proposal; never evaluates or activates it."""
        return _gateway_for_caller().suggest_improvement()

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_propose_improvement(
        name: str,
        rationale: str,
        patch: dict[str, Any],
        acceptance_criteria: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Propose allowlisted policy changes and explicit replay criteria; arbitrary code changes are rejected."""
        return _gateway_for_caller().propose_improvement(
            name=name,
            rationale=rationale,
            patch=patch,
            acceptance_criteria=acceptance_criteria,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_evaluate_improvement(candidate_id: str, case_ids: list[str] | None = None) -> dict[str, Any]:
        """Replay active and proposed policies on exact completed-case benchmarks and store a hash-bound comparison."""
        return _gateway_for_caller().evaluate_improvement(candidate_id, case_ids=case_ids)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_promote_improvement(
        candidate_id: str,
        expected_candidate_hash: str,
        expected_evaluation_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Activate an eligible proposal only after exact candidate, evaluation, reviewer, and phrase confirmation."""
        return _gateway_for_caller().promote_improvement(
            candidate_id,
            expected_candidate_hash=expected_candidate_hash,
            expected_evaluation_hash=expected_evaluation_hash,
            reviewer=reviewer,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_rollback_improvement(
        target_policy_id: str,
        expected_active_policy_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Restore a previously active policy using an exact active-policy hash and rollback confirmation."""
        return _gateway_for_caller().rollback_improvement(
            target_policy_id,
            expected_active_policy_hash=expected_active_policy_hash,
            reviewer=reviewer,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_governed_improvement_status() -> dict[str, Any]:
        """Describe the inactive, append-only generalized improvement registry and its safety boundary."""
        return _gateway_for_caller().governed_improvement_status()

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_register_improvement_candidate(
        candidate_kind: str,
        limitation_kind: str,
        base_artifact: dict[str, Any],
        candidate_artifact: dict[str, Any],
        problem_statement: str,
        rationale: str,
        expected_benefit: str,
        observed_failure_ids: list[str] | None = None,
        known_risks: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        parent_candidate_id: str | None = None,
        created_by: str = "agent-proposal",
    ) -> dict[str, Any]:
        """Register a hash-bound inactive improvement candidate; never activates or deploys it."""
        return _gateway_for_caller().register_improvement_candidate(
            candidate_kind=candidate_kind,
            limitation_kind=limitation_kind,
            base_artifact=base_artifact,
            candidate_artifact=candidate_artifact,
            problem_statement=problem_statement,
            rationale=rationale,
            expected_benefit=expected_benefit,
            observed_failure_ids=observed_failure_ids,
            known_risks=known_risks,
            evidence=evidence,
            parent_candidate_id=parent_candidate_id,
            created_by=created_by,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_list_governed_improvements(limit: int = 25) -> list[dict[str, Any]]:
        """List inactive generalized improvement candidates and their immutable state history."""
        return _gateway_for_caller().list_governed_improvements(limit=limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_governed_improvement(candidate_id: str) -> dict[str, Any]:
        """Fetch one exact candidate, frozen evaluation plan, evaluation, and event history."""
        return _gateway_for_caller().get_governed_improvement(candidate_id)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_freeze_improvement_evaluation(
        candidate_id: str,
        evaluation_plan: dict[str, Any],
        frozen_by: str,
    ) -> dict[str, Any]:
        """Freeze the exact benchmark, controls, gates, and anti-rescue rules before evaluation."""
        return _gateway_for_caller().freeze_improvement_evaluation(
            candidate_id,
            evaluation_plan=evaluation_plan,
            frozen_by=frozen_by,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_record_governed_improvement_evaluation(
        candidate_id: str,
        expected_candidate_hash: str,
        expected_plan_hash: str,
        result: dict[str, Any],
        verdict: str,
        evaluated_by: str,
    ) -> dict[str, Any]:
        """Record one immutable evaluation against exact candidate and frozen-plan hashes."""
        return _gateway_for_caller().record_governed_improvement_evaluation(
            candidate_id,
            expected_candidate_hash=expected_candidate_hash,
            expected_plan_hash=expected_plan_hash,
            result=result,
            verdict=verdict,
            evaluated_by=evaluated_by,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_external_experiment_status() -> dict[str, Any]:
        """Describe deterministic execution availability, approval rules, and the integrity/science boundary."""
        return _gateway_for_caller().external_experiment_status()

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_freeze_external_experiment(
        case_id: str,
        plan_id: str,
        expected_plan_hash: str,
        scientific_question: str,
        claim_scope: dict[str, Any],
        execution_spec: dict[str, Any],
        verdict_schema: dict[str, Any],
        independent_verifier: dict[str, Any],
        falsification_coverage: dict[str, Any],
        anti_rescue_rules: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        """Freeze an approved plan, inline code/data, scope, coverage, verifier, and anti-rescue contract."""
        return _gateway_for_caller().freeze_external_experiment(
            case_id=case_id,
            plan_id=plan_id,
            expected_plan_hash=expected_plan_hash,
            scientific_question=scientific_question,
            claim_scope=claim_scope,
            execution_spec=execution_spec,
            verdict_schema=verdict_schema,
            independent_verifier=independent_verifier,
            falsification_coverage=falsification_coverage,
            anti_rescue_rules=anti_rescue_rules,
            created_by=created_by,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_submit_external_experiment(
        experiment_id: str,
        expected_experiment_hash: str,
        submitted_by: str,
    ) -> dict[str, Any]:
        """Stage the exact frozen experiment; execution remains blocked pending human approval."""
        return _gateway_for_caller().submit_external_experiment(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            submitted_by=submitted_by,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_approve_external_experiment(
        experiment_id: str,
        expected_experiment_hash: str,
        expected_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Approve only the exact frozen experiment and exact staged execution manifest."""
        return _gateway_for_caller().approve_external_experiment(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            expected_manifest_hash=expected_manifest_hash,
            reviewer=reviewer,
            rationale=rationale,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_run_external_experiment(
        experiment_id: str,
        expected_experiment_hash: str,
        expected_manifest_hash: str,
    ) -> dict[str, Any]:
        """Execute the exact human-approved manifest without network and preserve a deterministic receipt."""
        return _gateway_for_caller().run_external_experiment(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            expected_manifest_hash=expected_manifest_hash,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_record_external_verification(
        experiment_id: str,
        expected_experiment_hash: str,
        expected_execution_receipt_hash: str,
        verifier_receipt: dict[str, Any],
        conclusion: str,
        verified_by: str,
    ) -> dict[str, Any]:
        """Attach one immutable independent-verifier receipt without treating execution integrity as proof."""
        return _gateway_for_caller().record_external_verification(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            expected_execution_receipt_hash=expected_execution_receipt_hash,
            verifier_receipt=verifier_receipt,
            conclusion=conclusion,
            verified_by=verified_by,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_prepare_external_reproduction(
        experiment_id: str,
        expected_experiment_hash: str,
        expected_execution_receipt_hash: str,
        submitted_by: str,
    ) -> dict[str, Any]:
        """Freeze a technical replay from one successful exact execution; separate approval is still required."""
        return _gateway_for_caller().prepare_external_reproduction(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            expected_execution_receipt_hash=expected_execution_receipt_hash,
            submitted_by=submitted_by,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_approve_external_reproduction(
        experiment_id: str,
        expected_experiment_hash: str,
        expected_original_receipt_hash: str,
        expected_reproduction_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Approve only the exact technical replay bound to its original receipt and new manifest."""
        return _gateway_for_caller().approve_external_reproduction(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            expected_original_receipt_hash=expected_original_receipt_hash,
            expected_reproduction_manifest_hash=expected_reproduction_manifest_hash,
            reviewer=reviewer,
            rationale=rationale,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_run_external_reproduction(
        experiment_id: str,
        expected_experiment_hash: str,
        expected_original_receipt_hash: str,
        expected_reproduction_manifest_hash: str,
    ) -> dict[str, Any]:
        """Run the exact approved replay and compare its output hashes with the original execution."""
        return _gateway_for_caller().run_external_reproduction(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            expected_original_receipt_hash=expected_original_receipt_hash,
            expected_reproduction_manifest_hash=expected_reproduction_manifest_hash,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_record_external_coverage_bug(
        experiment_id: str,
        expected_experiment_hash: str,
        claim_effect: str,
        missed_counterexample: dict[str, Any],
        reason: str,
        fix: dict[str, Any],
        old_results_impacted: list[str],
        replacement_coverage: dict[str, Any],
        recorded_by: str,
        affected_claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Preserve a missed counterexample, version coverage, and challenge bound Guided claims."""
        return _gateway_for_caller().record_external_coverage_bug(
            experiment_id,
            expected_experiment_hash=expected_experiment_hash,
            claim_effect=claim_effect,
            missed_counterexample=missed_counterexample,
            reason=reason,
            fix=fix,
            old_results_impacted=old_results_impacted,
            replacement_coverage=replacement_coverage,
            recorded_by=recorded_by,
            affected_claim_ids=affected_claim_ids,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_propagate_external_coverage_bug_to_claims(
        coverage_bug_id: str,
        expected_replacement_protocol_hash: str,
    ) -> dict[str, Any]:
        """Idempotently retry or audit a validated coverage bug's Guided claim impacts."""
        return _gateway_for_caller().propagate_external_coverage_bug_to_claims(
            coverage_bug_id,
            expected_replacement_protocol_hash=expected_replacement_protocol_hash,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_prepare_coverage_reevaluation(
        coverage_bug_id: str,
        expected_replacement_protocol_hash: str,
        execution_spec: dict[str, Any],
        resolution_targets: list[str],
        submitted_by: str,
    ) -> dict[str, Any]:
        """Freeze and stage the replacement protocol with every affected result named up front."""
        return _gateway_for_caller().prepare_coverage_reevaluation(
            coverage_bug_id,
            expected_replacement_protocol_hash=expected_replacement_protocol_hash,
            execution_spec=execution_spec,
            resolution_targets=resolution_targets,
            submitted_by=submitted_by,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_approve_coverage_reevaluation(
        coverage_bug_id: str,
        expected_replacement_protocol_hash: str,
        expected_reevaluation_hash: str,
        expected_execution_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Approve the exact replacement protocol, reevaluation contract, and staged execution manifest."""
        return _gateway_for_caller().approve_coverage_reevaluation(
            coverage_bug_id,
            expected_replacement_protocol_hash=expected_replacement_protocol_hash,
            expected_reevaluation_hash=expected_reevaluation_hash,
            expected_execution_manifest_hash=expected_execution_manifest_hash,
            reviewer=reviewer,
            rationale=rationale,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_run_coverage_reevaluation(
        coverage_bug_id: str,
        expected_replacement_protocol_hash: str,
        expected_reevaluation_hash: str,
        expected_execution_manifest_hash: str,
    ) -> dict[str, Any]:
        """Execute the exact approved coverage replacement while preserving the flawed predecessor."""
        return _gateway_for_caller().run_coverage_reevaluation(
            coverage_bug_id,
            expected_replacement_protocol_hash=expected_replacement_protocol_hash,
            expected_reevaluation_hash=expected_reevaluation_hash,
            expected_execution_manifest_hash=expected_execution_manifest_hash,
        )

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_record_coverage_resolutions(
        coverage_bug_id: str,
        expected_reevaluation_hash: str,
        expected_execution_receipt_hash: str,
        resolutions: list[dict[str, Any]],
        recorded_by: str,
    ) -> dict[str, Any]:
        """Resolve every affected result exactly once against the verified replacement execution receipt."""
        return _gateway_for_caller().record_coverage_resolutions(
            coverage_bug_id,
            expected_reevaluation_hash=expected_reevaluation_hash,
            expected_execution_receipt_hash=expected_execution_receipt_hash,
            resolutions=resolutions,
            recorded_by=recorded_by,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_coverage_bug(coverage_bug_id: str) -> dict[str, Any]:
        """Fetch a coverage bug, replacement protocol, reevaluation, and all affected-result resolutions."""
        return _gateway_for_caller().get_coverage_bug(coverage_bug_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_external_experiment(experiment_id: str) -> dict[str, Any]:
        """Fetch the frozen question, scope, coverage, execution receipt, and separate epistemic status."""
        return _gateway_for_caller().get_external_experiment(experiment_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_list_external_experiments(limit: int = 25) -> list[dict[str, Any]]:
        """List tenant-scoped frozen external experiments and their current governed state."""
        return _gateway_for_caller().list_external_experiments(limit=limit)

    @mcp.tool(annotations=LOCAL_WRITE, structured_output=True)
    def orbita_submit_plan(case_id: str, plan: dict[str, Any], compiler: str = "external-ai") -> dict[str, Any]:
        """Submit an AI-authored plan using only fields visible in case context; it remains unapproved."""
        return _gateway_for_caller().submit_plan(case_id, plan=plan, compiler=compiler)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_plan(plan_id: str) -> dict[str, Any]:
        """Fetch the complete immutable plan and its SHA-256 hash for review."""
        return _gateway_for_caller().get_plan(plan_id)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_approve_plan(
        plan_id: str,
        expected_plan_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Approve one exact frozen plan after review. Use the exact confirmation phrase reported by capabilities."""
        return _gateway_for_caller().approve_plan(
            plan_id,
            expected_plan_hash=expected_plan_hash,
            reviewer=reviewer,
            confirmation=confirmation,
        )

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_run_discovery(case_id: str, plan_id: str) -> dict[str, Any]:
        """Execute an already approved plan and persist findings, failed checks, claims, and reports."""
        return _gateway_for_caller().run_discovery(case_id, plan_id=plan_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_run(run_id: str, findings_offset: int = 0, findings_limit: int = 50) -> dict[str, Any]:
        """Read a paginated run result, including both surviving and refuted candidates."""
        return _gateway_for_caller().get_run(run_id, findings_offset=findings_offset, findings_limit=findings_limit)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_case_claims(case_id: str) -> list[dict[str, Any]]:
        """List durable claims linked to a research case."""
        return _gateway_for_caller().case_claims(case_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_claim_history(claim_id: str) -> dict[str, Any]:
        """Reconstruct a claim's evidence, checks, contradictions, dependencies, events, and supersession chain."""
        return _gateway_for_caller().claim_history(claim_id)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_claim_impact(claim_id: str) -> dict[str, Any]:
        """Show which downstream claims depend on a claim and how support can collapse."""
        return _gateway_for_caller().claim_impact(claim_id)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_add_contradiction(claim_a: str, claim_b: str, rationale: str) -> dict[str, Any]:
        """Record—not erase—a specific contradiction between two durable claims."""
        return _gateway_for_caller().add_contradiction(claim_a, claim_b, rationale=rationale)

    @mcp.tool(annotations=STATE_CHANGE, structured_output=True)
    def orbita_supersede_claim(claim_id: str, new_statement: str, rationale: str) -> dict[str, Any]:
        """Create a replacement claim while retaining the prior claim and full history."""
        return _gateway_for_caller().supersede_claim(claim_id, new_statement=new_statement, rationale=rationale)

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_reexamination_queue() -> list[dict[str, Any]]:
        """List open review tasks created by contradictions, revocations, or dependency collapse."""
        return _gateway_for_caller().reexamination_queue()

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    def orbita_get_report(case_id: str, format: Literal["markdown", "json", "html"] = "markdown") -> dict[str, Any]:
        """Read the newest completed case dossier in Markdown, JSON, or HTML."""
        return _gateway_for_caller().report(case_id, format=format)

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
        return json.dumps(_gateway_for_caller().case_context(case_id), indent=2, sort_keys=True)

    @mcp.resource("orbita://claims/{claim_id}")
    def claim_resource(claim_id: str) -> str:
        """Agent-readable complete claim history."""
        return json.dumps(_gateway_for_caller().claim_history(claim_id), indent=2, sort_keys=True)

    @mcp.resource("orbita://improvements/status")
    def improvement_resource() -> str:
        """Agent-readable active policy and bounded improvement contract."""
        return json.dumps(_gateway_for_caller().improvement_status(), indent=2, sort_keys=True)

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
8. Replay improvement proposals and show the result to the user.
9. Never call promotion or rollback without explicit authorization.
"""

    return mcp, gateway
