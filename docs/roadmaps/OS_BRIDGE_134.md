# OS bridge #134 — Windows PE32 x86/x64 -> Linux

Status: **OPEN — roadmap only, no implementation claimed.**

## Gates, in order

1. CURRENT: cross-target builds are refused; _resolve_target uses the host toolchain only.
2. GATE 1: obtain a Linux x86_64/arm64 host and run the full pipeline there; record toolchain/hashes.
3. GATE 2: x86 games first — native binary, stage logs, process launch and frame evidence.
4. GATE 3: x64 feasibility gate (same as #133) before any x64 claim.
5. BLOCKED: no local Linux runner; mark NOT TESTED rather than inventing results.

## Verification rule

A build or unit test is stage evidence only. No issue closes without a real scenario on both sides, matching hashes and an explicit Windows/Linux/macOS oracle where the target requires one. FAIL/BLOCKED/NOT TESTED remain distinct.
