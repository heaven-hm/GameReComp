# OS bridge #136 — macOS Mach-O -> Linux

Status: **OPEN — roadmap only, no implementation claimed.**

## Gates, in order

1. CURRENT: tools/macho lifts NeXTSTEP i386 Mach-O to a native HOST build; no modern .app support.
2. GATE 1: enumerate Mach-O flavours and refusal diagnostics (fat/thin, i386/ARM64/x86_64).
3. GATE 2: on a Linux host, build and launch a NeXTSTEP i386 sample; record logs/hashes.
4. GATE 3: modern macOS bundles documented DEFERRED with explicit diagnostics.
5. BLOCKED: no local Linux runner.

## Verification rule

A build or unit test is stage evidence only. No issue closes without a real scenario on both sides, matching hashes and an explicit Windows/Linux/macOS oracle where the target requires one. FAIL/BLOCKED/NOT TESTED remain distinct.
