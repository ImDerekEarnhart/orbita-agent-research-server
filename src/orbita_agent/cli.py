from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .gateway import APPROVAL_PHRASE, AgentGateway
from .mcp_server import build_mcp_server
from .tenancy import TenantResolutionError, build_registry


def _config(home: str | None) -> AgentConfig:
    base = AgentConfig.from_env()
    return AgentConfig(
        home=Path(home).expanduser() if home else base.home,
        knowledge_db=base.knowledge_db,
        max_inline_bytes=base.max_inline_bytes,
        max_graph_vertices=base.max_graph_vertices,
        max_graph_edges=base.max_graph_edges,
    )


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _demo(config: AgentConfig) -> int:
    rows = ["subject_id,group,marker,response,noise"]
    for index in range(1, 41):
        group = "A" if index <= 20 else "B"
        marker = index / 4
        response = 2.4 * marker + (0.03 if index % 2 else -0.03)
        noise = ((index * 17) % 13) - 6
        rows.append(f"s{index:02d},{group},{marker:.4f},{response:.4f},{noise}")
    with AgentGateway(config) as gateway:
        case = gateway.create_case(name="Orbita MCP demo", goal="Find stable non-causal relations in the table")
        gateway.add_inline_file(case_id=case["id"], filename="marker_response.csv", content="\n".join(rows) + "\n")
        plan = gateway.compile_plan(case["id"], max_candidates=24)
        gateway.approve_plan(
            plan["id"],
            expected_plan_hash=plan["plan_hash"],
            reviewer="local-demo",
            confirmation=APPROVAL_PHRASE,
        )
        run = gateway.run_discovery(case["id"], plan_id=plan["id"])
        _print({"case": case, "plan_id": plan["id"], "plan_hash": plan["plan_hash"], "run": run})
    return 0


def _doctor(config: AgentConfig) -> int:
    try:
        with AgentGateway(config) as gateway:
            capabilities = gateway.capabilities()
        result = {
            "ok": True,
            "python": sys.version.split()[0],
            "home": str(config.home.resolve()),
            "knowledge": capabilities["knowledge"],
            "active_research_policy": capabilities["self_improvement"]["active_policy"],
            "optional_tools": {
                "graphviz_dot": shutil.which("dot"),
                "lean_lake": shutil.which("lake"),
            },
            "notes": [
                "Graphviz is optional for SVG graph rendering; JSON graph data remains available without it.",
                "Lean is optional unless independently compiling exported finite certificates.",
            ],
        }
    except Exception as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    _print(result)
    return 0 if result["ok"] else 1


def _tenants(config: AgentConfig, args: argparse.Namespace) -> int:
    """Operator-only tenant administration.

    Binding is deliberately not exposed as an MCP tool: a caller must never be able to
    grant itself a tenant. It requires filesystem access to the deployment state.
    """
    registry = build_registry(config.ensure().home)
    action = args.tenant_command

    if action == "list":
        _print(
            {
                "bindings": [binding.public() for binding in registry.list_bindings()],
                **registry.describe(),
            }
        )
        return 0
    if action == "identities":
        _print({"identities": registry.list_identities()})
        return 0
    if action == "events":
        _print({"events": registry.list_events(limit=args.limit)})
        return 0
    if action == "unbind":
        removed = registry.unbind(args.subject)
        _print({"subject": args.subject, "unbound": removed})
        return 0 if removed else 1

    subject = args.subject
    if not subject and args.login:
        matches = [
            identity
            for identity in registry.list_identities()
            if identity["login"].casefold() == args.login.casefold()
        ]
        if len(matches) != 1:
            _print(
                {
                    "ok": False,
                    "error": (
                        "no single observed identity for that login; the user must complete "
                        "GitHub sign-in once, or pass --subject explicitly"
                    ),
                    "matches": matches,
                }
            )
            return 1
        subject = matches[0]["subject"]
    if not subject:
        _print({"ok": False, "error": "either --subject or --login is required"})
        return 1

    try:
        binding = registry.bind(
            subject,
            args.username,
            note=args.note,
            allow_shared=args.allow_shared,
            overwrite=args.overwrite,
        )
    except (TenantResolutionError, ValueError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "binding": binding.public()})
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="orbita-agent",
        description="Agent-safe governed research, evidence, and policy-improvement server",
    )
    parser.add_argument("--home", help="State directory (default: ORBITA_AGENT_HOME or ~/.orbita-agent)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--transport", choices=("stdio", "streamable-http", "sse"), default="stdio")
    serve.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    subparsers.add_parser("doctor", help="Validate installation and bundled research memory")
    subparsers.add_parser("demo", help="Run a deterministic end-to-end table example")

    tenants = subparsers.add_parser(
        "tenants", help="Bind authenticated identities to Discovery Genome tenants"
    )
    tenant_commands = tenants.add_subparsers(dest="tenant_command", required=True)
    tenant_commands.add_parser("list", help="List current subject-to-tenant bindings")
    tenant_commands.add_parser("identities", help="List GitHub identities that have signed in")
    tenant_events = tenant_commands.add_parser("events", help="Show the binding audit trail")
    tenant_events.add_argument("--limit", type=int, default=200)

    tenant_bind = tenant_commands.add_parser("bind", help="Bind one identity to one Genome tenant")
    tenant_bind.add_argument("--subject", help="Authenticated subject, for example github:1234")
    tenant_bind.add_argument("--login", help="GitHub login, resolved against observed identities")
    tenant_bind.add_argument("--username", required=True, help="Guided UI Genome username")
    tenant_bind.add_argument("--note")
    tenant_bind.add_argument(
        "--allow-shared",
        action="store_true",
        help="Permit a second subject on a tenant that is already bound",
    )
    tenant_bind.add_argument(
        "--overwrite", action="store_true", help="Replace an existing binding for this subject"
    )

    tenant_unbind = tenant_commands.add_parser("unbind", help="Remove a binding")
    tenant_unbind.add_argument("--subject", required=True)

    args = parser.parse_args()
    config = _config(args.home)
    if args.command == "doctor":
        raise SystemExit(_doctor(config))
    if args.command == "demo":
        raise SystemExit(_demo(config))
    if args.command == "tenants":
        raise SystemExit(_tenants(config, args))

    mcp, gateway = build_mcp_server(config=config, host=args.host, port=args.port)
    try:
        mcp.run(transport=args.transport)
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
