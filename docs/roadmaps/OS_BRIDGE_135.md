# OS bridge #135 — macOS Mach-O -> Windows x86/x64

Status: **OPEN — roadmap only, no implementation claimed.**

## Gates, in order

1. CURRENT: tools/macho reads NeXTSTEP fat Mach-O only; no PE writer, Win32 runtime or x64 generator.
2. GATE 1: separate modern .app (ARM64/x86_64 + dyld cache) from NeXTSTEP i386 Mach-O; scope both.
3. GATE 2: feasibility audit of PE writers and Win32 runtimes with licence checks.
4. GATE 3: if viable, build a Windows PE binary and execute a scenario; otherwise publish do-not-integrate decision.
5. DEFERRED: no second recompiler or duplicate runtime.

## Verification rule

A build or unit test is stage evidence only. No issue closes without a real scenario on both sides, matching hashes and an explicit Windows/Linux/macOS oracle where the target requires one. FAIL/BLOCKED/NOT TESTED remain distinct.
