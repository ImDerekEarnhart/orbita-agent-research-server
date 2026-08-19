import OrbitaLanguageLimit.Basic
import Mathlib.LinearAlgebra.BilinearMap
import Mathlib.Topology.Instances.Real.Lemmas
import Mathlib.Tactic.Linarith
import Mathlib.Tactic.NormNum

/-!
# Coherent-state sign-blindness certificate (application layer)

The generic obstruction and half-gap machinery is imported from the
machine-checked T1-T4 kernel in `OrbitaLanguageLimit.Basic`; this file contains
only the quadratic/linear bridge.

Physics interpretation (not itself formalized here):
* `H` labels a coherent state |H> of a free real scalar field.
* `quadraticSource B H = B H H` represents information quadratic in the
  classical solution H, such as the coherent-state expectation value of the
  free scalar stress-energy tensor.
* `ℓ H` represents a sign-sensitive linear observable, such as a smeared
  one-point field observable.

Scope boundary: this is a machine-checkable algebraic bridge, not a
formalization of Fock space, renormalized QFT, or semiclassical Einstein
gravity.  Those physics identifications must be supplied separately.
-/

namespace Orbita.LanguageLimit

section QuadraticLinearBridge

variable {H : Type*} {S : Type*}
variable [AddCommGroup H] [Module ℝ H]
variable [AddCommGroup S] [Module ℝ S]

/-- A source built quadratically from a bilinear map.  This is the algebraic
shape relevant to the classical stress tensor of a free scalar field. -/
def quadraticSource (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (h : H) : S :=
  B h h

/-- Quadratic information cannot distinguish a configuration from its sign flip. -/
theorem quadraticSource_neg
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (h : H) :
    quadraticSource B (-h) = quadraticSource B h := by
  simp [quadraticSource]

/-- The sign flip `h ↦ -h` is invisible to any quadratic source. -/
theorem invisible_neg (B : H →ₗ[ℝ] H →ₗ[ℝ] S) :
    Invisible (quadraticSource B) (fun k => -k) :=
  quadraticSource_neg B

/-- A nonzero linear observable is sign-sensitive. -/
theorem targetSensitive_neg
    (ℓ : H →ₗ[ℝ] ℝ) {h : H} (hNonzero : ℓ h ≠ 0) :
    TargetSensitive (fun k => ℓ k) (fun k : H => -k) := by
  refine ⟨h, ?_⟩
  simp only [map_neg]
  intro hEq
  apply hNonzero
  linarith

/-- The sign-flip pair is an exact representational-hole witness. -/
theorem holeWitness_neg
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (ℓ : H →ₗ[ℝ] ℝ)
    {h : H} (hNonzero : ℓ h ≠ 0) :
    HoleWitness (quadraticSource B) (fun k => ℓ k) h (-h) := by
  refine ⟨(quadraticSource_neg B h).symm, ?_⟩
  simp only [map_neg]
  intro hEq
  apply hNonzero
  linarith

/-- **B1 exact no-go certificate.**

If the source information is quadratic in the coherent-state label `H` and the
target is a nonzero linear observable `ℓ`, then no decoder seeing only the
source can recover the target exactly.
-/
theorem coherent_sign_no_exact_decoder
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (ℓ : H →ₗ[ℝ] ℝ)
    {h : H} (hNonzero : ℓ h ≠ 0) :
    ¬ Representable (quadraticSource B) (fun k => ℓ k) :=
  not_representable_of_holeWitness _ _ (holeWitness_neg B ℓ hNonzero)

/-- Transformation form, routed through Theorem 2 of the kernel. -/
theorem coherent_sign_transformation_no_go
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (ℓ : H →ₗ[ℝ] ℝ)
    {h : H} (hNonzero : ℓ h ≠ 0) :
    ¬ Representable (quadraticSource B) (fun k => ℓ k) :=
  transformationLanguageLimit _ _ (fun k => -k)
    (invisible_neg B) (targetSensitive_neg ℓ hNonzero)

/-- The within-fiber target separation on the sign-flip pair is `2 * |ℓ h|`. -/
theorem dist_target_neg (ℓ : H →ₗ[ℝ] ℝ) (h : H) :
    dist (ℓ h) (ℓ (-h)) / 2 = |ℓ h| := by
  rw [Real.dist_eq, map_neg, sub_neg_eq_add, ← two_mul, abs_mul]
  norm_num

/-- **B1 quantitative certificate.**

For every decoder of the quadratic source, the pair `h, -h` forces at least one
absolute target error to be at least `|ℓ h|`.
-/
theorem coherent_sign_decoder_error_lower_bound
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (ℓ : H →ₗ[ℝ] ℝ) (h : H)
    (g : Set.range (quadraticSource B) → ℝ) :
    |ℓ h| ≤
      max
        (decoderError (quadraticSource B) (fun k => ℓ k) g h)
        (decoderError (quadraticSource B) (fun k => ℓ k) g (-h)) := by
  have hpair := pairApproximationLowerBound
    (quadraticSource B) (fun k => ℓ k) g (quadraticSource_neg B h).symm
  rw [dist_target_neg ℓ h] at hpair
  exact hpair

/-- Application-level name: Theorem 2 instantiated by coherent-state sign flip. -/
theorem B1_T2_language_limit
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (ℓ : H →ₗ[ℝ] ℝ)
    {h : H} (hNonzero : ℓ h ≠ 0) :
    ¬ Representable (quadraticSource B) (fun k => ℓ k) :=
  coherent_sign_transformation_no_go B ℓ hNonzero

/-- Application-level name: Theorem 4 instantiated on the witness pair `h, -h`. -/
theorem B1_T4_pair_worst_case_lower_bound
    (B : H →ₗ[ℝ] H →ₗ[ℝ] S) (ℓ : H →ₗ[ℝ] ℝ) (h : H)
    (g : Set.range (quadraticSource B) → ℝ) :
    |ℓ h| ≤
      max
        (decoderError (quadraticSource B) (fun k => ℓ k) g h)
        (decoderError (quadraticSource B) (fun k => ℓ k) g (-h)) :=
  coherent_sign_decoder_error_lower_bound B ℓ h g

end QuadraticLinearBridge

end Orbita.LanguageLimit

