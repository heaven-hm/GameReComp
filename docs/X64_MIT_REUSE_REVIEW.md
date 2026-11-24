# x64 upstream reuse review: `Sahilg93/arm64-recompiler`

This records what was examined when deciding how the MIT-licensed
`arm64-recompiler` could advance GameReComp's x64 path, and what was actually
reused. **No source code from that project was copied into this repository.**
Its license text is kept in `LICENSES/arm64-recompiler-MIT.txt` for reference
and the evaluation is logged in `NOTICE`.

## What the upstream project is

A static recompiler that lifts Windows x86-64 PE code to **LLVM IR**, optimizes
that IR, and compiles it to an ARM64 Mach-O. Its run-time is split between
generated IR code, a large C runtime (`recompiler/console_runtime.c`, ~6,500
lines; `x86_interpret.h`, ~845 lines for runtime-generated code and unrecovered
indirect branches), a Wine-hosted subsystem (`recompiler/wine-host/`), and
Python orchestration (`recompile.py`, `lift.py`). Its proof is strong — Plink
`-V` byte-for-byte under Wine, PuTTY GUI, jq, ripgrep — but the architecture is
the opposite of ours: an IR pipeline, not a C lifter.

## Smallest pieces that could be reused

I examined the smallest standalone C files and the runtime boundaries:

| Piece | Size | Assessment |
|---|---|---|
| `recompiler/runner.c` | 12 lines | A `scanf`/`printf` wrapper around an extern `lifted()`. A test harness, nothing transferable. |
| `recompiler/writer_runtime.c` | 43 lines | Writer plumbing for *their* generated IR calls; meaningless without their IR ABI. |
| `recompiler/export_runtime.c` | 61 lines | DLL export/forwarding for their hosted Wine model; depends on their module database. |
| `recompiler/x86_interpret.h` | 845 lines | An x86 fallback interpreter. Interesting, but adopting it would mean importing their interpreter architecture, not advancing our lifter. |
| `recompiler/console_runtime.c` | ~6,500 lines | Full Win32/UCRT/HLE layer (CRT startup, FILE* handling, TLS, APISet). Far larger than our entire x64 runtime and tied to their IR calling convention. |
| `recompiler/wine-host/` | 6 files | Requires a Wine 11.0 install, a prefix, and Rosetta `wineserver`; orthogonal to native C output. |

The genuinely reusable ideas — writing import sentinel addresses into IAT slots
and routing them at load/dispatch time, and mapping the image at its preferred
`ImageBase` with a live-base delta — are **already in this repository**: see the
`HLE_BASE`/`HLE_SENTINEL` boundary in `tools/lift/generate64.py` and the rebasing
logic in `tools/pe/pe_rebase.py`. There was no missing piece to import.

## What was done instead

We implemented the external contract our own generated x64 C already requires,
without touching the lifter's architecture:

* `runtime/recomp64_cpu/cpu64.c` — `g_image_delta`, `dispatch`, `dispatch_jmp`,
  and a small PE32+ loader that maps sections, makes a Win64 guest stack, and
  fills `recomp_imports[]` slots with `HLE_SENTINEL(i)`.
* `runtime/recomp64_cpu/cpu64_selftest.c` — three Win64 HLE shims for the real
  `arith-calc.exe` imports (`ExitProcess`, `GetStdHandle`, `WriteFile`).
* `tools/lift/x64_oracle.py` and `tests/test_x64_native_selftest.py` — a real-PE
  Unicorn oracle and a native ARM64 compile-and-run selftest, with Frida and
  Highball Wine attempts recorded verbatim.

## Result and scope

`reports/x64_native/arith-calc-evidence.json`: the native ARM64 Mach-O output
matches the original x86-64 bytes under Unicorn 2.1.4 and under Highball's Wine
byte-for-byte (172 bytes, exit 0). Frida 17.24.3 could not instrument the
original under Wine on this arm64/Rosetta host and is recorded as **BLOCKED**
with exact errors. This is one small real PE, not general x64 support.
