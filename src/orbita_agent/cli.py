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

    args = parser.parse_args()
    config = _config(args.home)
    if args.command == "doctor":
        raise SystemExit(_doctor(config))
    if args.command == "demo":
        raise SystemExit(_demo(config))

    mcp, gateway = build_mcp_server(config=config, host=args.host, port=args.port)
    try:
        mcp.run(transport=args.transport)
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
