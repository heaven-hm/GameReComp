# OS bridge #133 — Windows PE32 x86/x64 -> macOS arm64

Status: **OPEN — roadmap only, no implementation claimed.**

## Gates, in order

1. CURRENT: PE32 x86 lift -> native host build -> launch is implemented and measured.
2. GATE 1: keep the 50-game E2E corpus green; every new commit must preserve PASS/FAIL labels.
3. GATE 2: x64 feasibility gate — inspect Omill/pcrecomp x64 support, licence and CFG parity; no code until the gate passes.
4. GATE 3: real Windows-side oracle capture (controlled input, screenshots, logs) before any PASS label.
5. DEFERRED: x64, .NET, Win16 — explicit refusal diagnostics, never advertised as working.

## Verification rule

A build or unit test is stage evidence only. No issue closes without a real scenario on both sides, matching hashes and an explicit Windows/Linux/macOS oracle where the target requires one. FAIL/BLOCKED/NOT TESTED remain distinct.
