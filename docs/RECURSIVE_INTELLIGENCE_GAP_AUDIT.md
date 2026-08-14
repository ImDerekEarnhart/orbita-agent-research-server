# Recursive Intelligence Gap Audit

Date: 2026-08-14  
Manifesto audited: `ORBITA RECURSIVE INTELLIGENCE COMPLETION MANIFESTO.md`  
Companion architecture audited: `LANGUAGE_TOWER_SUPERINTELLIGENCE_MANIFESTO.md` and `ARCHITECTURE_CONTRACT.md`  
Agent baseline: `5807012` (`orbita-agent-research-server` 0.8.0)  
Guided baseline: `9e08f40` (`orbita-guided-ui` staging branch)

## Governing conclusion

Orbita currently demonstrates a governed **bounded semantic-repair substrate**. It does not yet demonstrate a closed
recursive capability-growth loop, general recursive intelligence, AGI, or ASI.

The strongest implemented chain is:

```text
finite language description
  -> exact finite collision audit
  -> hash-bound language-limit certificate
  -> inactive designer-supplied repair candidate
  -> externally supplied prospective evaluation
  -> exact human confirmation
  -> inert hash-bound next snapshot
```

The missing load-bearing links are normalized evidence eligibility, autonomous failure diagnosis, bounded open repair
synthesis, integrated prospective repair evaluation, executable ORB-L admission, a first-class transition authorization,
and a capability-delta receipt on untouched tasks.

The authority boundary remains mandatory:

```text
proposal != admission != authorization != activation
```

No subsystem audited here may approve its own evidence, activate its own semantic proposal, rewrite its evaluator, or
deploy itself.

## Status vocabulary

- **PRESENT**: the required interface exists, is tested, and performs the milestone's essential function.
- **PARTIAL**: relevant executable machinery exists, but at least one essential contract or integration is absent.
- **MISSING**: no implementation performs the essential function.
- **BLOCKED**: implementation would be premature because an earlier governing dependency is not complete.

## Repository and subsystem map

| Subsystem | Exact implementation found | Audit finding |
|---|---|---|
| Orbita MCP/core | `src/orbita_agent/gateway.py`, `src/orbita_agent/mcp_server.py`, `src/orbita_mvp/service.py` | Tenant-scoped facade, frozen plans, exact approvals, claims/evidence memory, semantic artifact tools, and governed executors are present. |
| Unified candidate execution prerequisite | `src/orbita_mvp/execution_dispatch.py`, `src/orbita_agent/candidate_execution.py`, `docs/UNIFIED_CANDIDATE_EXECUTION.md` | **PRESENT.** Candidate kinds bind to exact hashed executor contracts before approval. No semantic coercion or fallback is permitted. Staging registry reports three grounded executors. |
| Semantic evolution substrate | `src/orbita_agent/semantic_evolution.py`, `tests/test_semantic_evolution.py`, `docs/SEMANTIC_EVOLUTION.md` | Canonical snapshots, finite collision audits, certificates, inert repair candidates, and inert transition receipts exist. They are helper artifacts, not a persistent closed-loop language runtime. |
| General Problem Loop | `src/orbita_agent/general_problem_loop.py`, `tests/test_general_problem_loop.py`, `docs/GENERAL_PROBLEM_LOOP.md` | Append-only hash-chained orchestration is present. Diagnoses and repairs are caller-supplied; the loop does not autonomously diagnose or synthesize. |
| Policy self-improvement | `src/orbita_agent/improvement.py`, `tests/test_improvement.py` | Mature, human-gated, rollback-capable policy-only lane. It is deliberately not semantic or architecture self-improvement. |
| Generalized improvement registry | `src/orbita_agent/improvement_registry.py`, `tests/test_improvement_registry.py` | Can freeze typed inactive candidate/evaluation artifacts. It has no generalized promotion or deployment authority. |
| External deterministic experiments / DerekX-like execution | `src/orbita_agent/external_experiments.py`, `src/orbita/execution.py`, `tests/test_external_experiments.py` | Network-disabled, manifest-bound execution and verification exist. There is no first-class `DerekX` semantic-transition authority that consumes `TransitionAuthorization`. |
| Discovery Genome | Guided `lib/discoveryGenome.js`, `migrations/20260721_discovery_genome.sql`, `tests/discovery_genome*.test.js`; Agent `src/orbita_agent/genome_client.py` | Frozen operator contracts, target-bound blind predictions, immutable tournament manifests, and result receipts exist. Tournament evidence is not normalized into the improvement learner. |
| Language Tower | `C:/Users/Dereks/projects/language-tower/L0` through `L21`, plus `CERT`, `INVARIANCE`, `GDISC`, `LOOP`, `CULT`, `COMPACT`, and `ORBC` | A large finite, executable, ledgered research archive exists. It is not a Git repository and does not expose one current hash-bound runtime language registry matching the manifesto's `LanguageSnapshot`. |
| Language-limit benchmark records | Language Tower `CERT/SPEC_CERT_CROSSDOMAIN.md`, `CERT/check_claims_cert.py`, `CERT/THEOREM_STATUS_LEDGER_CERT.json` | Frozen records include finite cross-domain certificate, negative-control, prospective, and minimality evidence. This pass treats the records as inherited artifacts; it did not rerun the full Tower archive. |
| ORB-L | Language Tower convention/theorem ledgers and admission behavior such as `LOOP/SPEC_ORB_LOOP.md` and `L21/check_claims_l21.py` | Provenance and bounded admission conventions exist, but no general executable ORB-L EARN service consumes normalized evidence and emits a semantic admission event. |
| Independent verification | `src/orbita/execution.py` receipt/artifact verification; Language Tower `check_claims_*.py`; Guided Genome integrity tests | Multiple verifier families exist, but verifier identity, independence, scope, and eligibility are not normalized across routes. |
| Architecture/code improvement | `src/orbita/coding.py` and its exact approval, promotion, verification, and rollback paths | Useful governance infrastructure exists. It is not evidence of recursively discovered architecture improvement and is not connected to a capability-delta benchmark. |

## Why Genome evidence does not currently enter the policy learner

This is an interface mismatch, not an evidence-quality judgment.

- `ImprovementLab._benchmarks()` in `src/orbita_agent/improvement.py` reads only local cases containing a `completed`
  discovery run and replays their frozen plan inputs.
- Genome results live in Guided's `discovery_tournaments` / `discovery_tournament_entries` records and are reached through
  `src/orbita_agent/genome_client.py`.
- A Genome result has operator, prediction, manifest, result, and independence data, but it is not a local completed case
  run and therefore cannot satisfy `_benchmarks()`.
- No shared object states which hashes are exact, what was frozen before reveal, what scope was tested, how independent
  the evaluator was, or which downstream decision categories the receipt may support.

The correct repair is a normalized, append-only evidence interface with category-specific eligibility rules. Converting a
Genome survivor into a fake completed table-discovery run would destroy provenance and is prohibited.

## Five required first-class objects

| Object | Status | Existing implementation | Exact gap |
|---|---|---|---|
| `LanguageSnapshot` | **PARTIAL** | `build_language_snapshot()` in `src/orbita_agent/semantic_evolution.py` canonicalizes primitives, observables, conditions, permissions, grounding rules, invariants, parent hash, and inactive status. | Not persisted as an immutable language registry; omits runtime/compiler/parser/grammar/world-schema hashes, constructor registry, truth/belief/assertion semantics, known limits/equivalences, operator/verifier versions, and claim-ledger hash. It does not reconstruct an executable Tower runtime. |
| `LanguageLimitCertificate` | **PARTIAL** | `audit_representation()` and `build_language_limit_certificate()` bind finite collision witnesses to a snapshot, proof path, proof artifact, and checker receipt. | No general factorization API, transformation certificate object, constructor-closure proof, quantitative gap/lower-bound mode, counterevidence/status lifecycle, or symbolic verifier integration. |
| `RepairCandidate` | **PARTIAL** | `build_repair_candidate()` freezes one declarative primitive and prospective recovery/stability/failure predictions. | Origin and synthesis level are absent; no executable AST sandbox evaluation, complexity cost, forbidden-input declaration, leakage receipt, candidate-family hash, or bounded synthesis engine. Current callers supply the primitive. |
| `TransitionAuthorization` | **PARTIAL** | `materialize_authorized_transition()` requires exact candidate/evaluation hashes, a named reviewer, and exact phrase. | Authorization is a function-call argument, not an independently persisted immutable object. It lacks admission-event hash, independent verifier receipt, expected L1 hash, rollback target, expiry/replay protection, and a DerekX consumer. |
| `CapabilityDeltaReceipt` | **MISSING** | No semantic transition artifact compares L0 and L1. | Required before any intelligence-growth claim: frozen suite/task hashes, before/after metrics, regressions, false-limit/unsupported-claim deltas, compute/latency, transfer, and verdict. |

## Manifesto milestone audit

| Milestone | Status | Exact evidence and missing interface |
|---|---|---|
| **A — Audit current implementation** | **PRESENT** | This document maps MCP, Language Tower, ORB-L, Genome, DerekX-like execution, verifiers, and benchmarks to exact files and interfaces. |
| **B — Evidence normalization** | **PARTIAL** | `src/orbita/epistemic_contract.py` has evidence status, coverage, scope normalization, and scope-escalation guards; run evidence, Genome receipts, external-experiment receipts, and checker receipts are hash-bound in separate stores. Missing: first-class `EvidenceReceipt`, `EvidenceScope`, `EvidenceIndependence`, and `EvidenceEligibility`, adapters for each source, and decision-specific eligibility policy. |
| **C — LanguageSnapshot v1** | **PARTIAL** | Canonical inert snapshot builder exists in `semantic_evolution.py`. Missing persistent immutable registry, full runtime identity, reconstruction, and executable-registry verification. |
| **D — Language Limit Kernel** | **PARTIAL** | Exact finite equivalence partition, collision witness, and candidate overseparation exist. Language Tower `CERT` and `INVARIANCE` preserve stronger domain-specific precedents. Missing shared `check_factorization`, transformation invariance, constructor closure, exact gap, and lower-bound APIs. |
| **E — Autonomous Failure Classifier** | **PARTIAL** | `GeneralProblemLoopService` validates caller-declared `SEARCH_FAILURE`, `LANGUAGE_LIMIT`, `MODEL_LIMIT`, and `EXECUTION_LIMIT`; the new dispatcher emits typed `ENGINE_CAPABILITY_LIMIT`. Missing `tower.meta.diagnose_failure(task_receipt)` and unprompted classification across all manifesto classes. |
| **F — Repair Synthesizer** | **PARTIAL** | Temporal audit compares an allowlisted supplied family, and `build_repair_candidate()` validates a supplied primitive. Missing explicit Levels 0–3 synthesis records, certificate-constrained enumeration/composition/parameter/program synthesis, resource ledger, and leakage rejection. |
| **G — Prospective Repair Tournament** | **PARTIAL** | Blind calibration and Discovery Genome provide prediction-before-reveal, immutable manifests, permanent refuters, and result receipts. Missing an integrated repair-candidate tournament whose held-out evaluation is directly bound to the parent snapshot and certificate. |
| **H — ORB-L Executable Admission** | **BLOCKED** | Tower ledgers and bounded convention admissions are evidence, but no general EARN state machine consumes normalized prospective repair evidence. Blocked on B, F, and G; no shortcut is admissible. |
| **I — TransitionAuthorization** | **PARTIAL** | Exact phrase/hash checks exist in `materialize_authorized_transition()`. Missing the immutable authorization object and DerekX consumption boundary. |
| **J — Deterministic Language Transition** | **PARTIAL** | `materialize_authorized_transition()` deterministically creates inert L1 and a transition receipt without changing production. Missing persisted transition execution, reconstruction verification, admission binding, rollback target, and active-runtime handoff. |
| **K — CapabilityDeltaReceipt** | **MISSING** | No before/after semantic capability adjudicator or receipt exists. |
| **L — Frozen-vs-Growing Benchmark** | **BLOCKED** | Existing benchmarks compare model/tool arms, not a frozen L0 with an authorized growing L0→L1 agent over a task sequence. Blocked on K. |
| **M — Unasked-Limit Benchmark** | **MISSING** | Current representation audits are explicitly invoked; no hidden task set measures whether Orbita autonomously asks for a representation audit only when appropriate. |
| **N — Invent-the-Tool Benchmark** | **BLOCKED** | No open repair synthesis beyond supplied/allowlisted families. Blocked on F and G. |
| **O — Cross-Domain Transfer** | **PARTIAL** | Genome has seven cross-domain operator families and blind transfer tournaments; Language Tower `CERT` has a cross-domain finite precedent. Missing a semantic primitive earned in one domain and prospectively improving an untouched different domain under one normalized receipt chain. |
| **P — Architecture Improvement** | **BLOCKED** | Governed coding promotion/rollback infrastructure exists, but architecture mutation is explicitly premature until semantic evidence, transitions, and capability deltas work. |
| **Q — Recursive Acceleration Benchmark** | **BLOCKED** | No two verified improvements exist where the second materially depends on and is accelerated by the first. |

## Stage-gate conclusion

The strongest justified stage is **Stage 0 — current validated bounded repair substrate**. Parts of later stages are
scaffolded, but no later stage is complete:

- autonomous diagnosis has not been demonstrated;
- open repair synthesis has not been demonstrated;
- the active machine has not undergone a governed semantic transition;
- no untouched-task capability gain has been measured;
- no cumulative or cross-domain gain chain has been demonstrated.

## Security and governance invariants that must be preserved

1. Candidate execution remains exact-contract-only; no coercion into a convenient scorer.
2. Genome survival may support an operator only within its frozen scope; it cannot authorize code or deployment.
3. A language-limit claim requires an accepted certificate, not search exhaustion.
4. Repair candidates remain inactive until independent evaluation, ORB-L admission, and explicit human authorization.
5. The proposer may not grade itself or rewrite the benchmark, verifier, eligibility policy, or holdout.
6. Target labels, reveal data, scoring keys, and unresolved holdouts remain unavailable before prediction freeze.
7. Production deployment is never a semantic-transition side effect.
8. Rollback information and prior snapshots must remain immutable.

## Smallest next vertical slice

Implement **Evidence Normalization v1** without changing any existing evidence row or weakening any route:

1. immutable `EvidenceReceipt` plus hash verification;
2. explicit `EvidenceScope`, `EvidenceIndependence`, and `EvidenceEligibility` schemas;
3. read-only adapters for completed discovery runs, Genome tournament results, external experiments, proof/checker receipts,
   and independent verifiers;
4. a policy matrix that states which evidence source may support which decision category;
5. negative controls proving that Genome survival cannot authorize deployment, unverified evidence is ineligible, changed
   hashes fail closed, same-source evidence cannot claim external independence, and legacy records remain untouched;
6. no automatic admission, transition, activation, promotion, rollback, or deployment operation.

Only after this slice passes should `LanguageSnapshot v1` be expanded into a persistent reconstructable semantic identity.

## Current claim boundary

Strongest justified claim:

> Orbita has a deployed, governed executor boundary and a tested finite semantic-repair substrate that can create inert,
> hash-bound language artifacts while preserving human activation authority.

Strongest prohibited overclaim:

> Orbita recursively improves itself, has demonstrated compounding intelligence, or is on evidence already produced an
> AGI/ASI system.

The Tower may discover that it needs to change. It may not decide by itself that the change is now true.
