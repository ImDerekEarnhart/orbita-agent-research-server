# Orbita Agent Research Server v0.1.1

Orbita Agent Research Server is a local, MCP-native research system for people and AI agents. It turns supplied data into an explicit analysis plan, freezes that plan before confirmation scoring, runs bounded falsification checks, and stores both survivors and failures in persistent epistemic memory.

It also exposes a curated read-only research vault, preserved Erdős–Gyárfás search receipts, bounded finite-graph analysis, and Lean source export for concrete certificates.

The product rule is simple:

> The model may propose. The plan hash, falsifiers, evidence ledger, and approval boundary decide what may be run and what may be claimed.

## What was combined

The build uses the strongest compatible parts of the supplied archives:

| Source | Role in this product |
| --- | --- |
| Orbita Research MVP v0.1 | Governed intake, profiling, frozen plans, discovery runs, belief memory, and reports |
| Orbita Epistemic Runtime v1.5 | Claims, evidence, checks, contradictions, supersession, dependency collapse, and hash-bound receipts |
| Orbita Discovery Kit v0.2 | Propose → judge → falsify → ledger engine and safe tabular candidates |
| Math Discovery Research Vault | Curated documents and structured claim-status cards |
| Erdős–Gyárfás research database | Finite run summaries, exact labeled near misses, caveats, and certificates |
| Lemma Miner + Lean checker | Bounded graph profiling and finite certificate export |

Raw conversations, duplicate build trees, stale binaries, unrestricted execution routes, and desktop-control capabilities are not exposed to agents.

## Architecture

```mermaid
flowchart TD
    A["AI or researcher"] --> B["MCP tool boundary"]
    B --> C["Frozen research plan"]
    C --> D["Discovery + falsifiers"]
    D --> E["Claims + evidence memory"]
    B --> F["Curated research vault"]
    B --> G["Bounded graph + Lean adapter"]
```

## Install

Python 3.11 or newer is required.

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
orbita-agent doctor
orbita-agent demo
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
orbita-agent doctor
orbita-agent demo
```

`install.ps1` performs the Windows setup automatically.

## Connect an AI through MCP

For a local stdio connection, configure an MCP host with the installed executable:

```json
{
  "mcpServers": {
    "orbita": {
      "command": "C:\\FULL\\PATH\\TO\\.venv\\Scripts\\orbita-agent.exe",
      "args": ["serve", "--transport", "stdio"],
      "env": {
        "ORBITA_AGENT_HOME": "C:\\Users\\YOUR_NAME\\OrbitaAgentData"
      }
    }
  }
}
```

For local Streamable HTTP:

```powershell
orbita-agent serve --transport streamable-http --host 127.0.0.1 --port 8765
```

The MCP endpoint is `http://127.0.0.1:8765/mcp`.

Local operation remains unauthenticated when bound to `127.0.0.1`. Remote deployments must set
`ORBITA_AGENT_REQUIRE_AUTH=1` and a randomly generated `ORBITA_AGENT_API_TOKEN` containing at least 32 characters.
Authenticated clients send it as `Authorization: Bearer <token>`.

## Deploy on Railway

The repository includes a non-root Docker image, Railway config-as-code, an unauthenticated `/health` readiness
route, and bearer protection on `/mcp`. Follow [the Railway deployment guide](docs/RAILWAY_DEPLOYMENT.md).

## Recommended agent flow

1. Call `orbita_capabilities`.
2. Create a case with `orbita_create_case`.
3. Attach CSV/TSV/JSON(L)/text with `orbita_add_inline_file`.
4. Inspect `orbita_case_context`.
5. Compile a deterministic plan or submit an explicit AI-authored plan.
6. Fetch the complete plan and its hash with `orbita_get_plan`.
7. Ask the user to review it. Approval is a separate tool call bound to the exact hash.
8. Run the approved plan with `orbita_run_discovery`.
9. Read every survivor and refutation, then inspect claim history and the report.
10. When evidence changes, record contradiction or supersession instead of erasing history.

The exact approval phrase is reported by `orbita_capabilities`; clients should not guess it.

## Tool groups

- Research: cases, inline files, context, plans, approval, discovery, paginated runs, and reports.
- Memory: case claims, claim history, dependency impact, contradictions, supersession, and re-examination.
- Vault: full-text curated research search and structured claim cards.
- Graph theory: preserved finite-run summaries, near misses, bounded graph analysis, and Lean witness export.

## Scientific boundaries

- A held-out score is not a probability that a claim is true.
- Association is not causation.
- Survival against configured attacks is not universal proof or novelty.
- Open discovery is scout/confirmation separated, but it still requires external replication.
- “No witness found” under a bounded graph search can be inconclusive.
- Lean export certifies one explicit finite graph/cycle only.

See [AI operator guide](docs/AI_OPERATOR_GUIDE.md), [architecture](docs/ARCHITECTURE.md), [security](docs/SECURITY.md), [source provenance](docs/SOURCE_PROVENANCE.md), and [validation](VALIDATION.md).
