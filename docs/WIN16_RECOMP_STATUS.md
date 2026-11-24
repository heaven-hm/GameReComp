# Win16 NE recomp status — Agent W1 (2026-11-24)

Host: macOS, Apple Silicon (`uname -m` = `arm64`), Python 3.12.1,
Unicorn 2.1.4, Capstone 5.0.7, `/usr/bin/gcc` (clang wrapper with a working
sysroot). Repo state: `develop` @ `f32b416`.

**Bottom line:** Win16 NE is *not* "DEFERRED" in the sense of "no working
code". The repo has a real 16-bit lifter, a flag-accurate C runtime, and NE
parsers, and they pass against **real Win16 NE binaries found on this host**.
What is still missing is the autonomous pipeline integration (issue #8) and a
Win16 guest-API/memory-model host on macOS — not the CPU/lifting core. No
claim of a playable Win16 game is made here.

---

## 1. Existing selftests — all run, all pass

| Command | Result |
|---|---|
| `cd runtime/recomp16 && cc -I. cpu_selftest.c -o /tmp/dt16_cpuselftest && /tmp/dt16_cpuselftest` | `ok: 217 checks, 0 failures` |
| `cd tools/lift && python3 test_addr32_16.py` | `addr32 (16-bit): ok` |
| `python3 test_bswap.py` | `bswap: ok` |
| `python3 test_computed_ret.py` | `computed_ret: ok` |
| `python3 test_dos_hooks.py` | `22/22 passed` |
| `python3 test_fpu_operands.py` | `x87 operand selection: ok` |
| `python3 test_lift16_div.py` | `36/36 divide cases agree (faults on zero and on overflow, with cs:ip set)` |
| `python3 test_loopcc.py` | `test_loopcc: ok` |
| `python3 test_nontemporal.py` | `test_nontemporal: ok` |
| `python3 test_repz_ret.py` | `test_repz_ret: ok` |
| `python3 test_sar_narrow.py` | `ok: 0 failed` — *caveat printed by the test itself: "could not compile the check (); shape checked only"* |
| `python3 sse_selftest.py` | `59/59 match hardware` |

`test_sar_narrow.py` passing is weaker than the others (shape-only on this
host); recorded honestly rather than counted as a full hardware check.

## 2. Real NE binaries found on this host

Scan: every `.exe/.dll/.bin/.ovl/.drv` under `~/Downloads`,
`~/Documents/Code`, `/Applications` with an MZ header whose `e_lfanew` points
at `NE`/`LE`/`LX` (7010 MZ files with far headers checked). `~/Downloads/
recomp-apps/win16` does **not** exist; `win32`/`win64` contain only PE.

Three real NE executables were found, all in the OpenTyrian 2.0 data set
(`~/Downloads/recomp-apps/games/opentyrian/data/`):

| File | SHA-256 | Size | NE hdr | Module | Code segs | Relocs |
|---|---|---|---|---|---|---|
| `file0001.exe` | `0f51a58732e47a8a6f1f786788be9925cbf78fc9f086f9b5339030f85dacdf93` | 236032 | 0x0E3E | `TYRIAN2` | 8 | 3959 |
| `setup.exe` | `010fff01150ef7b7d8ff9df53426f4cbe51d925e9b50b6d8f27f712d3ac64868` | 62720 | 0x0E3E | `SETUP` | 4 | 884 |
| `shipedit.exe` | `9be5f168ee0e0743c0e9a541b8620820b6d53b6d8458fdc8d3c50727ee9c3181` | 82944 | 0x0E3E | `SHIPEDIT` | 3 | 908 |

Classification with `tools/ne/ne_parse.py`: linker 6.1, `target_os = 1`
(Windows), flags `0x030A` (Windows 3.x/386), module reference table
`KERNEL, KERNEL, RTM` — i.e. genuine Win16 NE, not OS/2. (`file(1)` labels
these "NE for OS/2 1.x"; that label is wrong for these headers — the NE field
and the import list say Windows.) A scan also found 10 sibling plain-MZ DOS
EXEs; those are out of scope.

## 3. Real bug found and fixed in `tools/lift/difftest16.py`

First run on a real NE segment looked catastrophic:

```
python tools/lift/difftest16.py --ne .../shipedit.exe --seg 1 --vectors 1
1406 distinct testable instructions
1406 (instruction, vector) pairs compared against real x86
1382 instructions disagree: ... "memory differs"
```

Root cause: the generated C harness accumulated the FNV hash in
`unsigned long crc` / `16777619ul`. On LP64 macOS/Linux that is 64-bit, while
the Python reference masks every step to 32 bits. A minimal 3-instruction raw
probe proved it: C printed `crc=119731480A1D8000`, Python printed `0A1D8000`
— identical low 32 bits. Every instruction was a false positive.

Fix: `uint32_t crc`, `uint32_t w`, `16777619u`, and `%08X` with an explicit
`(unsigned)` cast in `difftest16.py`'s generated `TAIL`. Regression test:
`tools/lift/test_difftest16_crc.py` (2 tests, passing).

After the fix, on real Win16 NE binaries:

| Command | Result |
|---|---|
| `difftest16.py --ne shipedit.exe --seg 1 --vectors 2` | `1406 distinct testable instructions`, `2812 pairs`, **all agree** |
| `difftest16.py --ne file0001.exe --seg 2 --vectors 2` | `1407 distinct testable instructions`, `2814 pairs`, **all agree** |
| `difftest16.py --ne setup.exe --seg 1 --vectors 2` | `1199 distinct testable instructions`, `2397 pairs`, **all agree** |
| `difftest16.py --raw <3 instructions> --vectors 1` | `all agree` |

These numbers cover *testable single instructions* (the harness deliberately
skips control flow, calls, string ops, etc.) — not full function semantics.

## 4. Lifting and native macOS ARM64 execution

- `python3 tools/lift/ne_lift.py .../shipedit.exe --seg 1` lifted the whole
  segment cleanly: `/* Segment 1 - 31887 bytes, 1252 functions */`, 20,764
  lines of C, zero stderr. Segments 2 (545 functions) and 3 (831 functions)
  also lift cleanly.
- `tests/win16_native_smoke/` builds **one lifted function from the real NE**
  (`SEG002:0x2F2E`, 25 bytes `a3 58 f5 … c3`) into a native arm64 Mach-O with
  `gcc -arch arm64 -O2` against the unmodified `runtime/recomp16/cpu.h`, and
  compares its registers, flags, 7 written memory slots and a whole-memory
  CRC against the original bytes executed by Unicorn x86-16.

  ```
  python3 -m unittest tests.win16_native_smoke.test_native_smoke -v
  test_lifted_function_matches_real_x86_bytes ... ok
  test_native_binary_is_arm64 ... ok
  Ran 2 tests in 0.624s — OK
  ```
  `file` on the produced binary: `Mach-O 64-bit executable arm64`.

  Caveat: this is a *leaf* function (no calls, no Win16 imports). It proves
  the lift → native-ARM64 execution path for real NE code with the recomp16
  runtime; it does not prove whole-program Win16 execution.

## 5. Exact remaining gaps (do not round these up)

1. **Pipeline integration (issue #8).** `tools/autorecomp/pipeline.py` still
   enforces PE32 and fail-closes on NE; nothing in this work wires NE into it.
2. **No Win16 guest host on macOS.** `runtime/win16/host/` is Win16-on-Win32
   (KERNEL/USER/GDI/WinG shims, NE resource serving). This work exercises only
   CPU/flags/memory lifting; a single imported API (`KERNEL.90`, etc.) cannot
   be executed natively on macOS yet.
3. **Protected-mode selector model.** `gen_segments_h.py` emits
   `#define SEG_n n` placeholders explicitly labelled "Replace with a real
   selector->base table when the protected-mode memory model lands." The
   smoke test flat-maps segments at 0; real NE DGROUP/segment bases are not
   modelled.
4. **PASCAL purge / stack cleanup.** `tools/ne/win16.py` has the purge table
   for gen_win16_stubs, but the native smoke test builds a leaf with `ret`,
   not a `retf N` import boundary.
5. **Segment/lift accuracy is partial.** difftest16 covers single testable
   instructions only; control flow, calls, string ops and `cs:` overrides are
   skipped by design. `ne_lift.py`'s linear sweep can desync on data-in-code
   without an external IDA code map (no such map is available here).
6. **No end-to-end game oracle.** No Win16 game reaches a native boot, let
   alone an independent original-Windows gameplay comparison. The three NE
   binaries are utilities from a shareware data set, not a lifted game.
7. **Self-test caveat.** `test_sar_narrow.py` only checked C shape on this
   host (compiler step failed); not a hardware-equivalence result.
8. **Source-binary redistribution.** The lifted C fixture is committed with
   provenance (SHA-256) but the NE binaries remain in `~/Downloads` and are
   not vendored; regeneration needs a local copy at that path.

## 6. Reproduce

```bash
# selftests
(cd runtime/recomp16 && cc -I. cpu_selftest.c -o /tmp/dt16_cpuselftest && /tmp/dt16_cpuselftest)
(cd tools/lift && for t in test_*.py sse_selftest.py; do python3 "$t"; done)

# NE differential verification (needs a local SHIPEDIT.EXE / file0001.exe)
python3 tools/lift/difftest16.py --ne <NE> --seg 1 --vectors 2
python3 tools/lift/test_difftest16_crc.py -v

# native arm64 lifted function + Unicorn gold
python3 -m unittest tests.win16_native_smoke.test_native_smoke -v
```
