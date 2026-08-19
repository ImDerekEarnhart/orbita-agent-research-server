import Mathlib.Topology.MetricSpace.Pseudo.Defs
import Mathlib.Order.ConditionallyCompleteLattice.Basic
import Mathlib.Data.Real.Basic
import Mathlib.Tactic.Linarith

/-!
# Representational Hole / Language Limit Theorems

A Lean 4 formalization of the four core theorems behind the Orbita
representational-hole framework.

The file is intentionally application-neutral.  It proves facts about an
arbitrary representation map `φ : X → Y` and target `O : X → Z`.
A scientific language `L = {fᵢ}` is represented by its joint observation map
`Φ_L`; see `jointRepresentation` below.

The formalization distinguishes:

* exact representability from computability;
* a witness pair from a universal transformation-invariance proof;
* closure under declared representation-respecting constructors from arbitrary
  access to the underlying state;
* exact non-representability from quantitative approximation lower bounds.
-/

universe uX uY uZ uI uK

namespace Orbita.LanguageLimit

variable {X : Type uX} {Y : Type uY} {Z : Type uZ}

/-- Aggregate a family of language primitives into one joint representation. -/
def jointRepresentation
    {I : Type uI}
    (Yᵢ : I → Type uY)
    (L : (i : I) → X → Yᵢ i) : X → ((i : I) → Yᵢ i) :=
  fun x i ↦ L i x

/-- The canonical point in the image/range of a representation. -/
def encoded (φ : X → Y) (x : X) : Set.range φ :=
  ⟨φ x, ⟨x, rfl⟩⟩

/-- Two states are language-equivalent exactly when the representation agrees. -/
def FiberEq (φ : X → Y) (x y : X) : Prop :=
  φ x = φ y

/-- A target is exactly representable if it factors through the image of `φ`. -/
def Representable (φ : X → Y) (O : X → Z) : Prop :=
  ∃ g : Set.range φ → Z, ∀ x : X, O x = g (encoded φ x)

/-- A target is fiber-constant if equal representations force equal target values. -/
def FiberConstant (φ : X → Y) (O : X → Z) : Prop :=
  ∀ ⦃x y : X⦄, φ x = φ y → O x = O y

/-- A single exact representational-hole witness. -/
def HoleWitness (φ : X → Y) (O : X → Z) (x y : X) : Prop :=
  φ x = φ y ∧ O x ≠ O y

/-! ## Theorem 1: Exact representability iff fiber constancy -/

/--
**Theorem 1 — Exact Representability Criterion.**

`O` factors through `φ` iff `O` is constant on every fiber of `φ`.
The decoder is defined only on `Set.range φ`, so no arbitrary value outside the
image is required.
-/
theorem exactRepresentability_iff_fiberConstant
    (φ : X → Y) (O : X → Z) :
    Representable φ O ↔ FiberConstant φ O := by
  constructor
  · rintro ⟨g, hg⟩ x y hxy
    rw [hg x, hg y]
    apply congrArg g
    apply Subtype.ext
    exact hxy
  · intro hconst
    classical
    refine ⟨(fun y : Set.range φ ↦ O (Classical.choose y.property)), ?_⟩
    intro x
    apply hconst
    exact (Classical.choose_spec (encoded φ x).property).symm

/-- A witness pair immediately refutes exact representability. -/
theorem not_representable_of_holeWitness
    (φ : X → Y) (O : X → Z) {x y : X}
    (h : HoleWitness φ O x y) :
    ¬ Representable φ O := by
  intro hrep
  have hconst : FiberConstant φ O :=
    (exactRepresentability_iff_fiberConstant φ O).mp hrep
  exact h.2 (hconst h.1)

/-! ## Theorem 2: Transformation-induced language limit -/

/-- A transformation is invisible to `φ` when it preserves every encoded state. -/
def Invisible (φ : X → Y) (T : X → X) : Prop :=
  ∀ x : X, φ (T x) = φ x

/-- A transformation is target-sensitive if it changes the target somewhere. -/
def TargetSensitive (O : X → Z) (T : X → X) : Prop :=
  ∃ x : X, O (T x) ≠ O x

/--
**Theorem 2 — Transformation Language Limit.**

If `T` is invisible to the frozen representation but changes the target at at
least one state, the target cannot factor through the representation.
-/
theorem transformationLanguageLimit
    (φ : X → Y) (O : X → Z) (T : X → X)
    (hinv : Invisible φ T)
    (hsens : TargetSensitive O T) :
    ¬ Representable φ O := by
  rintro hrep
  have hconst : FiberConstant φ O :=
    (exactRepresentability_iff_fiberConstant φ O).mp hrep
  rcases hsens with ⟨x, hx⟩
  exact hx (hconst (hinv x))

/-! ## Theorem 3: Closure preservation -/

/--
A declared constructor of expressions of codomain `Z`.

The `respects` field is the formal guardrail: the constructor is admitted only
if it maps representable inputs to a representable output.  A constructor that
secretly inspects `x : X` without going through `φ` cannot satisfy this field in
general.
-/
structure Constructor (φ : X → Y) (Z : Type uZ) where
  arity : Nat
  op : (Fin arity → X → Z) → X → Z
  respects : ∀ fs : Fin arity → X → Z,
    (∀ i, Representable φ (fs i)) → Representable φ (op fs)

/-- Expressions generated from base primitives and declared constructors. -/
inductive Generated
    (φ : X → Y)
    {I : Type uI} {K : Type uK}
    (base : I → X → Z)
    (ctors : K → Constructor φ Z) : (X → Z) → Prop where
  | primitive (i : I) : Generated φ base ctors (base i)
  | construct (k : K)
      (fs : Fin (ctors k).arity → X → Z)
      (hfs : ∀ i, Generated φ base ctors (fs i)) :
      Generated φ base ctors ((ctors k).op fs)

/--
**Theorem 3 — Closure Preservation.**

If every base primitive factors through `φ` and every constructor preserves
factorization, every generated expression factors through `φ`.
-/
theorem closurePreservation
    (φ : X → Y)
    {I : Type uI} {K : Type uK}
    (base : I → X → Z)
    (ctors : K → Constructor φ Z)
    (hbase : ∀ i : I, Representable φ (base i))
    {F : X → Z}
    (hF : Generated φ base ctors F) :
    Representable φ F := by
  induction hF with
  | primitive i =>
      exact hbase i
  | construct k fs hfs ih =>
      exact (ctors k).respects fs ih

/--
A target with a hole witness cannot occur in a closure generated only by
representation-respecting constructors.
-/
theorem target_not_in_generated_closure_of_witness
    (φ : X → Y) (O : X → Z)
    {I : Type uI} {K : Type uK}
    (base : I → X → Z)
    (ctors : K → Constructor φ Z)
    (hbase : ∀ i : I, Representable φ (base i))
    {x y : X}
    (hw : HoleWitness φ O x y) :
    ¬ Generated φ base ctors O := by
  intro hgen
  exact not_representable_of_holeWitness φ O hw
    (closurePreservation φ base ctors hbase hgen)

/-! ## Theorem 4: Quantitative approximation lower bound -/

section Metric

variable [PseudoMetricSpace Z]

/-- All target separations occurring inside a single representation fiber. -/
def fiberDistances (φ : X → Y) (O : X → Z) : Set ℝ :=
  {r : ℝ | ∃ x y : X, φ x = φ y ∧ r = dist (O x) (O y)}

/-- The supremal within-fiber target separation. -/
noncomputable def representationalGap (φ : X → Y) (O : X → Z) : ℝ :=
  sSup (fiberDistances φ O)

/-- The pointwise error of a decoder that has access only to the representation. -/
def decoderError
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z) (x : X) : ℝ :=
  dist (O x) (g (encoded φ x))

/-- The set of decoder errors over the state space. -/
def decoderErrors
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z) : Set ℝ :=
  Set.range (decoderError φ O g)

/-- The worst-case error as a real supremum. -/
noncomputable def worstCaseError
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z) : ℝ :=
  sSup (decoderErrors φ O g)

/-- A declared uniform error upper bound for a representation-only decoder. -/
def UniformErrorBound
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z) (ε : ℝ) : Prop :=
  ∀ x : X, decoderError φ O g x ≤ ε

/-- The fiber-distance set is nonempty whenever the state space is. -/
theorem fiberDistances_nonempty
    [Nonempty X] (φ : X → Y) (O : X → Z) :
    (fiberDistances φ O).Nonempty := by
  classical
  let x : X := Classical.choice (inferInstance : Nonempty X)
  refine ⟨0, ?_⟩
  exact ⟨x, x, rfl, by simp⟩

/--
Pairwise core of Theorem 4: a decoder gives the same prediction to states in
one fiber, so one of the two errors must be at least half their target distance.
-/
theorem pairApproximationLowerBound
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z)
    {x y : X} (hxy : φ x = φ y) :
    dist (O x) (O y) / 2 ≤
      max (decoderError φ O g x) (decoderError φ O g y) := by
  have henc : encoded φ x = encoded φ y := by
    apply Subtype.ext
    exact hxy
  have hz : g (encoded φ x) = g (encoded φ y) := congrArg g henc
  have htri := dist_triangle (O x) (g (encoded φ x)) (O y)
  have hright :
      dist (g (encoded φ x)) (O y) = dist (O y) (g (encoded φ y)) := by
    rw [dist_comm, hz]
  rw [hright] at htri
  change dist (O x) (O y) / 2 ≤
    max (dist (O x) (g (encoded φ x))) (dist (O y) (g (encoded φ y)))
  have hxle : dist (O x) (g (encoded φ x)) ≤
      max (dist (O x) (g (encoded φ x))) (dist (O y) (g (encoded φ y))) :=
    le_max_left _ _
  have hyle : dist (O y) (g (encoded φ y)) ≤
      max (dist (O x) (g (encoded φ x))) (dist (O y) (g (encoded φ y))) :=
    le_max_right _ _
  linarith

/-- Any certified within-fiber separation `δ` forces uniform error at least `δ/2`. -/
theorem approximationLowerBound_of_witness
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z)
    {x y : X} (hxy : φ x = φ y)
    {δ ε : ℝ}
    (hδ : δ ≤ dist (O x) (O y))
    (hε : UniformErrorBound φ O g ε) :
    δ / 2 ≤ ε := by
  have hpair := pairApproximationLowerBound φ O g hxy
  have hmax :
      max (decoderError φ O g x) (decoderError φ O g y) ≤ ε :=
    max_le (hε x) (hε y)
  linarith

/--
A uniform finite error bound also bounds the supremal representational gap.
This is the direct `Δ/2 ≤ ε` formulation.
-/
theorem representationalGap_half_le_uniformError
    [Nonempty X]
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z)
    {ε : ℝ}
    (hε : UniformErrorBound φ O g ε) :
    representationalGap φ O / 2 ≤ ε := by
  have hall : ∀ r ∈ fiberDistances φ O, r ≤ 2 * ε := by
    intro r hr
    rcases hr with ⟨x, y, hxy, rfl⟩
    have h := approximationLowerBound_of_witness
      φ O g hxy (δ := dist (O x) (O y)) (ε := ε) le_rfl hε
    linarith
  have hgap : representationalGap φ O ≤ 2 * ε := by
    unfold representationalGap
    exact csSup_le (fiberDistances_nonempty φ O) hall
  linarith

/--
**Theorem 4 — Approximation Lower Bound.**

If the pointwise decoder-error set is bounded above, its supremum is at least
half the representational gap:

`representationalGap φ O / 2 ≤ worstCaseError φ O g`.
-/
theorem worstCaseError_ge_half_representationalGap
    [Nonempty X]
    (φ : X → Y) (O : X → Z) (g : Set.range φ → Z)
    (hBdd : BddAbove (decoderErrors φ O g)) :
    representationalGap φ O / 2 ≤ worstCaseError φ O g := by
  apply representationalGap_half_le_uniformError φ O g
  intro x
  unfold worstCaseError
  apply le_csSup hBdd
  exact ⟨x, rfl⟩

end Metric

end Orbita.LanguageLimit

