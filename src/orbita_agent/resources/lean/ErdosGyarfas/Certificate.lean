import Std.Tactic.NativeDecide

namespace ErdosGyarfas

/-- An undirected edge is stored in canonical order `(u, v)` with `u < v`. -/
abbrev Edge := Nat × Nat

/--
A finite graph certificate plus a claimed power-of-two cycle.

This is intentionally an executable certificate format rather than the full
abstract Erdős–Gyárfás theorem.  It allows Lean to verify a concrete witness
produced by the Python search engine.
-/
structure Certificate where
  n : Nat
  edges : List Edge
  cycle : List Nat
  power : Nat
  deriving Repr

/-- Canonical ordering for an undirected edge. -/
def canonicalEdge (u v : Nat) : Edge :=
  if u < v then (u, v) else (v, u)

/-- Boolean membership test for an undirected edge. -/
def hasEdge (c : Certificate) (u v : Nat) : Bool :=
  c.edges.contains (canonicalEdge u v)

/-- Consecutive pairs in a list. -/
def consecutivePairs : List Nat → List Edge
  | a :: b :: rest => (a, b) :: consecutivePairs (b :: rest)
  | _ => []

/-- Last element, if present. -/
def last? : List α → Option α
  | [] => none
  | [x] => some x
  | _ :: xs => last? xs

/-- Remove the last element of a list. -/
def removeLast : List α → List α
  | [] => []
  | [_] => []
  | x :: xs => x :: removeLast xs

/-- Executable no-duplicates check. -/
def nodupBEq [BEq α] : List α → Bool
  | [] => true
  | x :: xs => !(xs.contains x) && nodupBEq xs

/-- Number of incident edges at a vertex. -/
def degree (c : Certificate) (v : Nat) : Nat :=
  c.edges.foldl
    (fun acc e => if (e.1 == v) || (e.2 == v) then acc + 1 else acc)
    0

/-- Every edge is loop-free, canonical, and inside the vertex range. -/
def validSimpleEdges (c : Certificate) : Bool :=
  nodupBEq c.edges &&
    c.edges.all (fun e => decide (e.1 < e.2 ∧ e.2 < c.n))

/-- Check that every vertex has degree at least `d`. -/
def minDegreeAtLeast (c : Certificate) (d : Nat) : Bool :=
  (List.range c.n).all (fun v => decide (d ≤ degree c v))

/-- Check that the listed cycle is closed. -/
def closedCycle (c : Certificate) : Bool :=
  match c.cycle with
  | [] => false
  | first :: _ => last? c.cycle == some first

/-- Check that the non-repeated cycle vertices are all distinct. -/
def simpleCycleVertices (c : Certificate) : Bool :=
  nodupBEq (removeLast c.cycle)

/-- Check that every listed cycle vertex is a valid graph vertex. -/
def cycleVerticesInRange (c : Certificate) : Bool :=
  c.cycle.all (fun v => decide (v < c.n))

/-- Check that every consecutive pair in the proposed cycle is an edge. -/
def cycleEdgesPresent (c : Certificate) : Bool :=
  (consecutivePairs c.cycle).all (fun e => hasEdge c e.1 e.2)

/-- Number of edges in the proposed closed cycle. -/
def cycleLength (c : Certificate) : Nat :=
  c.cycle.length - 1

/-- Check that the cycle length is exactly `2^power`, with power at least two. -/
def powerOfTwoLength (c : Certificate) : Bool :=
  decide (2 ≤ c.power) && decide (cycleLength c = 2 ^ c.power)

/--
Full executable certificate checker.

It verifies:
* a finite simple graph encoding;
* minimum degree at least three;
* a closed simple cycle;
* all cycle edges exist;
* the cycle length is a power of two, at least four.
-/
def checkCertificate (c : Certificate) : Bool :=
  validSimpleEdges c &&
  minDegreeAtLeast c 3 &&
  closedCycle c &&
  simpleCycleVertices c &&
  cycleVerticesInRange c &&
  cycleEdgesPresent c &&
  powerOfTwoLength c

/-- If the Boolean checker returns true, Lean has certified this concrete record. -/
theorem certified_of_check_eq_true (c : Certificate)
    (h : checkCertificate c = true) : checkCertificate c = true := h

end ErdosGyarfas
