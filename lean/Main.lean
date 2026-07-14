import ErdosGyarfas.GeneratedWitness

open ErdosGyarfas

/-- Command-line confirmation after the theorem has compiled. -/
def main : IO Unit := do
  IO.println s!"Lean certificate check: {checkCertificate generatedCertificate}"
  IO.println s!"Vertices: {generatedCertificate.n}"
  IO.println s!"Edges: {generatedCertificate.edges.length}"
  IO.println s!"Cycle length: {cycleLength generatedCertificate}"
  IO.println s!"Power exponent: {generatedCertificate.power}"
