# x64 PE32+ -> native ARM64: expansion beyond arith-calc

Base: PR #174 (`6d9e195`, "native ARM64 runtime + verified real-PE selftest"),
taken from `origin/pr/174` tip `33ff93e` (a descendant of `develop`). The
arith-calc proof covered ONE 3-import PE. This change runs the full
`disasm64 -> generate64 -> clang arm64 -> native run` pipeline on five more
real PEs and compares each against the ORIGINAL bytes under Unicorn and under
Highball's Wine engine.

## Exact results

| PE (real bytes) | funcs / imports | native | Unicorn (original) | Highball Wine (original) | verdict |
|---|---:|---|---|---|---|
| `apps/base64.exe` (153,210 B) | 100 / 45 | exit 0, 228 B stdout | MATCH, no fault | MATCH, exit 0 | **PASS** |
| `games/tictactoe.exe` (152,350 B) | 105 / 44 | exit 0, 738 B stdout | MATCH, no fault | MATCH, exit 0 | **PASS** |
| `games/tetris.exe` (152,148 B) | 103 / 46 | exit 0, 367 B stdout | MATCH, no fault | MATCH, exit 0 | **PASS** |
| `games/flood.exe` (158,792 B) | 293 / 191 | exit -6 (SIGABRT), 0 B | not run | not run | **BLOCKED** |
| `apps/busybox.exe` (719,360 B) | 2605 / 313 | exit -6 (SIGABRT), 0 B | not run | not run | **BLOCKED** |

Every case had `lift_errors == 0` from `generate64.py`. The three PASS cases
feed identical stdin bytes to all three runners: `b"3\n"`, nine tic-tac-toe
moves, and `b"q\n"` respectively. Byte streams are compared as raw bytes
(including ANSI escapes and CRLF), never as text.

The two BLOCKED cases are recorded verbatim, not softened:

* `flood.exe`: `cpu64: call-import unmodelled: KERNEL32.dll!GetSystemTimeAsFileTime (HLE slot 74)`
* `busybox.exe`: `cpu64: jmp-import unmodelled: msvcrt.dll!__set_app_type (HLE slot 139)`

Both compile and generate cleanly; they stop at an import outside the bounded
shim surface. No PASS is claimed for them. Full per-binary evidence is in
`reports/x64_native/<tag>-evidence.json`; the roll-up is
`reports/x64_native/expand-summary.json`.

## Bounded runtime gaps fixed

All changes are small and local; none replace the lifter or add an emulator.

1. **`runtime/recomp64_cpu/cpu64.h` / `cpu64.c`** - Apple Silicon has no GS
   segment, but MSVC CRT startup reads `gs:[0x30]` for the PEB. Added an ARM64
   fallback backed by one process-wide simulated TEB/PEB. `RECOMP_TODO` now
   routes through a weak `x64_todo()` so a runner can report an exact VA/text.
2. **jmp-import return address** - MSVC emits one-line `jmp [__imp_X]` thunks.
   The caller's return address is still on the guest stack, so a returning
   import must consume it. `dispatch_jmp()` now pops 8 bytes; without this the
   guest stack drifted and callee-saved registers were silently trashed.
3. **`win64_shims.c` (new)** - the bounded UCRT/KERNEL32 surface the three
   console programs actually import: CRT startup cells (`__p___argc`,
   `__p___argv`, `__p__environ`, `__p__commode`, `__p__fmode`),
   `__acrt_iob_func`, `__stdio_common_vfprintf` / `vfscanf`, `puts`, `putchar`,
   `getchar`, `fgets`, heap, string and conversion routines, TLS, critical
   sections, no-op EH, and process exit. About 45-46 slots per binary are
   covered; anything else returns 0 and the runtime aborts with the exact
   `dll!name`.
4. **Win64 ABI details** - `__stdio_common_vfprintf`'s 5th argument is a
   va_list *pointer*, not inline varargs; `__p___argv` hands out a `char ***`
   cell; Win64 `long` is 32-bit; and Windows console streams translate `\n`
   to CRLF. Each was found from the generated call sites and fixed in the
   shim, not assumed.
5. **`win64_runner.c` (new)** - native runner with exact fault reporting
   (guest RIP/RSP/registers and fault address). It ignores exactly one
   unmodelled op, the CRT's `fninit` (x87 is not modelled and no other x87 op
   exists here); every other TODO still aborts with its exact VA and text.
6. **`tools/lift/x64_oracle_ucrt.py` (new)** - Unicorn oracle for this wider
   import surface, mirroring the C shims (va_list, CRLF, data cells, GS/PEB).
   It runs the original x86-64 bytes, never the generated C.
7. **`tools/lift/generate64.py`** - close the PE file handle (ResourceWarning).

## Additional probes (not part of the five-case regression)

* `games/game2048.exe`: generates cleanly (0 lift errors, 1 TODO) and compiles,
  but native run stops at `cpu64: jmp to unmapped guest VA 0x140002e10`
  (guest RIP `0x140002daf`). This is a catalog-closure gap in `generate64.py`,
  not an import gap; it is intentionally NOT claimed as a success.
* `games/snake.exe`: recompiles and runs, but its board places food via
  `rand()`, so three engines cannot be byte-compared without a shared seeded
  RNG. Recorded here as nondeterministic rather than a false MATCH.

## Reproduce

```sh
python3 -m unittest tests.test_x64_expand -v
python3 -m unittest tests.test_x64_native_selftest -v   # PR #174 must still pass
```

Requires the real PE corpus at `~/Downloads/recomp-apps/windows/x64`, a
`clang` with the `arm64-apple-macos11` target, `unicorn`/`capstone`, and
Highball's x64 Wine engine for the Wine cross-check.
