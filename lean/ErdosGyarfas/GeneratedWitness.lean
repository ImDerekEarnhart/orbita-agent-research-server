import ErdosGyarfas.Certificate
import Std.Tactic.NativeDecide

open ErdosGyarfas

/-- Generated from a Python finite-world certificate. -/
def generatedCertificate : Certificate := {
  n := 24
  edges := [
    (0, 2),
    (0, 10),
    (0, 17),
    (1, 12),
    (1, 16),
    (1, 18),
    (2, 20),
    (2, 22),
    (3, 14),
    (3, 15),
    (3, 19),
    (4, 6),
    (4, 14),
    (4, 16),
    (5, 7),
    (5, 10),
    (5, 22),
    (6, 10),
    (6, 14),
    (7, 9),
    (7, 23),
    (8, 13),
    (8, 20),
    (8, 21),
    (9, 11),
    (9, 20),
    (11, 15),
    (11, 21),
    (12, 17),
    (12, 22),
    (13, 17),
    (13, 19),
    (15, 16),
    (18, 21),
    (18, 23),
    (19, 23)
  ]
  cycle := [0, 10, 5, 7, 23, 19, 13, 17, 0]
  power := 3
}

/--
Lean checks the graph encoding, minimum degree ≥ 3, cycle simplicity,
edge membership, and that the cycle length equals 2^3.
-/
theorem generatedCertificate_is_valid :
    checkCertificate generatedCertificate = true := by
  native_decide
