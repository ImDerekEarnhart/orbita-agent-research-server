import OrbitaLanguageLimit.Basic
import Mathlib.Topology.Instances.Real.Lemmas

/-!
# Finite sanity example

The parity representation of two bits identifies `(false,false)` and
`(true,true)`, while the target "first bit" distinguishes them.  This is a
small exact instance of Theorems 1–2 and of the witness form of Theorem 4.
-/

namespace Orbita.LanguageLimit.Example

open Orbita.LanguageLimit

abbrev BitState := Bool × Bool

def parityRep : BitState → Bool :=
  fun p ↦ Bool.xor p.1 p.2

def complement : BitState → BitState :=
  fun p ↦ (!p.1, !p.2)

def firstBit : BitState → Bool :=
  fun p ↦ p.1

def firstBitReal : BitState → ℝ :=
  fun p ↦ if p.1 then 1 else 0

example : Invisible parityRep complement := by
  intro p
  rcases p with ⟨a, b⟩
  cases a <;> cases b <;> rfl

example : TargetSensitive firstBit complement := by
  refine ⟨(false, false), ?_⟩
  simp [complement, firstBit]

example : ¬ Representable parityRep firstBit := by
  apply transformationLanguageLimit parityRep firstBit complement
  · intro p
    rcases p with ⟨a, b⟩
    cases a <;> cases b <;> rfl
  · exact ⟨(false, false), by simp [complement, firstBit]⟩

example
    (g : Set.range parityRep → ℝ)
    {ε : ℝ}
    (hε : UniformErrorBound parityRep firstBitReal g ε) :
    (1 : ℝ) / 2 ≤ ε := by
  apply approximationLowerBound_of_witness
    parityRep firstBitReal g
    (x := (false, false)) (y := (true, true))
    (δ := 1) (ε := ε)
  · rfl
  · rw [Real.dist_eq]
    norm_num [firstBitReal]
  · exact hε

end Orbita.LanguageLimit.Example

