# Hidden benchmark canary findings

The first 140-task development canary was intentionally retained after it
exposed two defects:

1. Orbita's proof matcher treated a shared opaque case identifier as semantic
   overlap. Revoking one premise therefore invalidated an independent proof in
   all 20 evidence-preservation cases.
2. The benchmark generator omitted replication metadata used by category-level
   discovery metrics. Exact discovery states were correct, but four committed
   discoveries received incomplete category credit.

The initial canary scored 140/160 exact states (87.5%). The proof matcher now
ignores opaque correlation tokens when matching premises, and a regression test
locks down the behavior. The generator now emits the complete discovery gold
metadata expected by the scorer.

Because the initial canary influenced these fixes, it is development evidence,
not an untouched holdout. A fresh private seed was generated for the subsequent
run.

