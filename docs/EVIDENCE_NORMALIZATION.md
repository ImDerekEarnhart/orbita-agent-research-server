# Evidence Normalization v1

Orbita preserves each route's original evidence and adds an immutable normalized receipt. Normalization is an accounting
bridge, not a claim upgrade and not an activation mechanism.

Each receipt freezes source identity and hashes, freeze-before-reveal status, scope, result, evaluator, independence,
provenance, fixed-policy decision eligibility, and independently verifiable normalization and receipt hashes.

Implemented source adapters read actual tenant-scoped completed discovery runs and succeeded external experiments. The
Genome MCP adapter fetches the authenticated user's actual frozen tournament and verifies its recorded result hash before
normalization.

## Fixed eligibility

- Discovery runs may support bounded scientific-claim or research-policy **review**.
- Genome tournaments may support discovery-operator **review**.
- Verified external experiments may support bounded scientific-claim review and, with sufficient independence,
  repair-candidate review.
- Proof-assistant and independent-verifier source kinds are reserved for certificate and verification review; trustworthy
  source-store adapters remain a later slice.

No evidence receipt can authorize semantic admission or activation, policy promotion, code deployment, or architecture
activation. Those actions require separate category-specific governance and explicit human authority.

## Fail-closed behavior

- unknown source/decision kinds are rejected;
- exact hashes are mandatory;
- changed content under an already-normalized source identity is rejected;
- same-source evaluation cannot claim external independence;
- unverified results have no allowed review decisions;
- prospective operator/repair support requires freeze before reveal;
- receipt, normalization, scope, independence, and eligibility policy are independently re-verifiable;
- receipts are tenant isolated, immutable, and append-only.
