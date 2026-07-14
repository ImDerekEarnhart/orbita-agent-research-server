import Lake
open Lake DSL

package «erdos-gyarfas-cert» where
  version := v!"0.1.0"

lean_lib ErdosGyarfas where

@[default_target]
lean_exe «erdos_gyarfas_cert» where
  root := `Main
