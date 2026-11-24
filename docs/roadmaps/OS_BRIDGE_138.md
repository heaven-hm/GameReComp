# OS bridge #138 — Linux ELF -> macOS arm64

Status: **OPEN — roadmap only, no implementation claimed.**

## Gates, in order

1. CURRENT: no ELF reader; tools/lift handles x86 guest code from PE/NeXT sources, not ELF.
2. GATE 1: feasibility audit for ELF -> arm64 translation and system-call/ABI mapping.
3. GATE 2: reuse check against existing decomp/translation projects with licence review.
4. GATE 3: if viable, build and run a native macOS binary from an ELF input with logs/hashes.
5. DEFERRED: no ELF lift path; new runtime would be required.

## Verification rule

A build or unit test is stage evidence only. No issue closes without a real scenario on both sides, matching hashes and an explicit Windows/Linux/macOS oracle where the target requires one. FAIL/BLOCKED/NOT TESTED remain distinct.
