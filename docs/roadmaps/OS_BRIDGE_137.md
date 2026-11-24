# OS bridge #137 — Linux ELF -> Windows x86/x64

Status: **OPEN — roadmap only, no implementation claimed.**

## Gates, in order

1. CURRENT: no ELF reader, PE writer or Win32 runtime in this repository.
2. GATE 1: feasibility audit of ELF loaders/decompilers and PE output toolchains with licences.
3. GATE 2: verify whether an existing upstream can be reused without duplicating PCRecomp.
4. GATE 3: if viable, produce a PE binary and execute a scenario; otherwise do-not-integrate decision.
5. DEFERRED: duplicate code generator/loader/runtime is not allowed.

## Verification rule

A build or unit test is stage evidence only. No issue closes without a real scenario on both sides, matching hashes and an explicit Windows/Linux/macOS oracle where the target requires one. FAIL/BLOCKED/NOT TESTED remain distinct.
