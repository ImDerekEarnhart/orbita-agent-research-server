import OrbitaLanguageLimit.Basic

/-!
# Proof-carrying certificate objects

These structures are deliberately small.  They are intended to be the checked
core behind a larger JSON/provenance envelope: hashes and external metadata can
identify the frozen objects, while Lean checks the mathematical payload.
-/

namespace Orbita.LanguageLimit

universe uX uY uZ

variable {X : Type uX} {Y : Type uY} {Z : Type uZ}

/-- A proof-carrying exact representational-hole certificate. -/
structure HoleCertificate (φ : X → Y) (O : X → Z) where
  x : X
  y : X
  sameRepresentation : φ x = φ y
  targetDifferent : O x ≠ O y

namespace HoleCertificate

/-- Every exact hole certificate soundly refutes exact representability. -/
theorem sound
    {φ : X → Y} {O : X → Z}
    (c : HoleCertificate φ O) :
    ¬ Representable φ O :=
  not_representable_of_holeWitness φ O ⟨c.sameRepresentation, c.targetDifferent⟩

end HoleCertificate

/-- A proof-carrying transformation certificate for Theorem 2. -/
structure TransformationCertificate (φ : X → Y) (O : X → Z) where
  T : X → X
  invisible : Invisible φ T
  targetSensitive : TargetSensitive O T

namespace TransformationCertificate

/-- Every transformation certificate soundly refutes exact representability. -/
theorem sound
    {φ : X → Y} {O : X → Z}
    (c : TransformationCertificate φ O) :
    ¬ Representable φ O :=
  transformationLanguageLimit φ O c.T c.invisible c.targetSensitive

end TransformationCertificate

section Metric

variable [PseudoMetricSpace Z]

/-- A certified lower bound on target separation inside one representation fiber. -/
structure GapWitness (φ : X → Y) (O : X → Z) (δ : ℝ) where
  x : X
  y : X
  sameRepresentation : φ x = φ y
  separation : δ ≤ dist (O x) (O y)

namespace GapWitness

/-- A gap witness forces any declared uniform decoder error to be at least `δ/2`. -/
theorem uniformErrorLowerBound
    {φ : X → Y} {O : X → Z} {δ : ℝ}
    (c : GapWitness φ O δ)
    (g : Set.range φ → Z)
    {ε : ℝ}
    (hε : UniformErrorBound φ O g ε) :
    δ / 2 ≤ ε :=
  approximationLowerBound_of_witness
    φ O g c.sameRepresentation c.separation hε

end GapWitness

end Metric

end Orbita.LanguageLimit

