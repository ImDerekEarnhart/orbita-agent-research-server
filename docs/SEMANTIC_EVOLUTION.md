# Governed semantic evolution

Orbita 0.6 adds the missing artifact layer between noticing a representation problem and proposing a governed language
change. It is evidence for a research architecture, not a claim of AGI or ASI.

## Artifact chain

1. `LanguageSnapshot` hashes declared primitives, observables, refusal and unknown conditions, grounding rules,
   permissions, and invariants.
2. `RepresentationAudit` partitions explicit finite worlds by their complete visible `language_view`. It reports an exact
   collision when worlds with the same view have different outcomes. It separately reports candidate overseparation when
   nuisance-equivalent worlds receive different views without an outcome difference.
3. `LanguageLimitCertificate` binds a real collision witness to the parent snapshot, an accepted proof path, a proof
   artifact, and an independent checker receipt.
4. `LanguageRepairCandidate` adds one declarative primitive and freezes predicted recovered collisions, unchanged controls,
   possible new failures, and a minimality claim.
5. `LanguageTransitionReceipt` requires a prospectively survived evaluation, exact candidate and evaluation hashes, an
   identified human reviewer, and the exact authorization phrase.
6. The resulting `L(t+1)` remains inert. It cannot edit source, deploy itself, or become the active runtime.

This preserves the governing rule: the system may discover that its language should change, but it cannot decide by
itself that the change is true or active.

## Temporal unaskability

The temporal audit starts only from histories that collide under their present value while carrying different outcomes.
It can compare fixed-parameter, allowlisted operators:

- current value;
- lag;
- window mean;
- EWMA;
- linear recurrence;
- threshold crossings;
- hysteresis;
- State-Inertia.

It reports which candidates separate the finite supplied collisions. It performs no fitting, winner selection, admission,
or activation. This makes State-Inertia a falsifiable candidate beside ordinary memory baselines instead of a privileged
assumption.

## Archive synthesis

The capability-component graph represents an archived mechanism by typed inputs, outputs, capabilities, needs, failure
modes, assumptions, and falsifiers. It adds an edge only for an exact interface match or an exact capability-to-need match.
An edge is a composition hypothesis, not evidence that the combination works.

## Still missing before any strong self-extension claim

- automated extraction of trustworthy component cards from a large archive;
- independent execution of proposed primitive semantics;
- blind evaluation on problems that do not announce the missing representation;
- safe runtime activation by a separately implemented DerekX authority;
- repeated, preregistered `L0 -> L1 -> L2` capability gains on untouched tasks;
- external replication and safety review.
