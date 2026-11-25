# Issue #133 end-to-end proof: Windows PE32 x86 -> macOS arm64

| Game | Windows guest SHA-256 | Native guest SHA-256 | Identical |
|---|---|---|---|
| arith-calc | `b6674fd6da06bc9d` | `b6674fd6da06bc9d` | **True** |
| base64 | `3c96b39deed45ebb` | `3c96b39deed45ebb` | **True** |

Both binaries were rebuilt from the original Windows PE32 executables with the GameReComp pipeline (score 70/100, verification `not-run`), then launched with identical stdin on both sides. The Windows side ran under Highball Wine; the macOS side ran the recompiled native binary. Guest output blocks are byte-identical in both cases.

**Scope**: two scripted console scenarios only. This is not gameplay, graphics, input, audio or equivalence for GUI games. Raw logs are in this folder.
