# PE32+ (x64) to native Linux — executed evidence

Date: 2026-11-24. Base: PR #175 merge (`2e49f8f`), which supplied
`tools/linux_matrix.py`, the prebuilt `gamerecomp/linux-runner:local` image and
the working PE32/x86 path. Branch: `agent-l64/linux-x64`.

This supersedes the "x64 BLOCKED" note in `docs/LINUX_RUNNER_EVIDENCE.md`: a
real PE32+ console executable was lifted, built as an **aarch64 ELF inside the
Linux container**, launched, and its stdout compared byte-for-byte against the
same original run under Highball. No Wine and no emulator is used on the
Linux side.

## 1. What was tried (real files, no synthetic game)

Targets from `~/Downloads/recomp-apps/windows/x64/apps`:

| target | disasm64 | generate64 | Linux build | Linux run |
|---|---|---|---|---|
| `arith-calc.exe` (8,844 B) | 5 functions, 0 lift errors | 5 functions, 0 TODO, 3 imports | **0** | **0**, stdout identical to Highball |
| `base64.exe` (153,210 B) | 81 functions | 100 functions, 3 TODO, 45 imports | **0** | 97, `unimplemented import __acrt_iob_func` |
| `imgview64.exe` (11,776 B) | originally `struct.error` on truncated `.pdata`; now 3 functions | originally `IndexError` (no `.text`); now 10 functions, 0 lift errors | not attempted (GUI) | — |
| `guess.exe` (158 KiB) | 269 functions (P3 baseline) | 321 functions, 0 lift errors (P3 baseline) | not re-run | — |
| `tictactoe.exe` | file absent from that directory | — | — | — |

Exact original errors reproduced before the fixes:

```text
# disasm64 imgview64.exe
struct.error: unpack_from requires a buffer of at least 25612 bytes for
unpacking 12 bytes at offset 25600 (actual buffer size is 11776)

# aarch64 Linux compile of P3's guess.exe generated units
/tmp/ccexGam8.s:22984: Error: unknown mnemonic `movq' -- `movq %gs:(x3),x3'
/tmp/ccexGam8.s:25905: Error: unknown mnemonic `movq' -- `movq %gs:(x0),x0'
```

## 2. Fixes

1. **Portable GS/TEB** (`runtime/recomp64_cpu/cpu64.h`). The non-MSVC branch
   emitted x86 inline asm (`movq %%gs:(%1), %0`) that cannot assemble on
   aarch64. It now reads/writes through a loader-supplied `g_gs_base`, which is
   also more faithful (the guest expects a Windows TEB, not the host's TLS).
   Native GS remains available as an explicit `RECOMP64_GS_NATIVE` opt-in for
   x86-64 differential tests.
2. **x64 Linux project generator** (`tools/x64_project.py`). Turns
   `generate64.py` output into the CMakeLists + `src/` shape that
   `linux_matrix.py` already builds. It embeds the original PE as a C array
   and copies `cpu64.h`, `eh64.h` and the new host.
3. **Native host / loader** (`runtime/recomp64_cpu/host64_linux.c`). Maps the
   image at its ImageBase (falling back to a relocated mapping + `g_image_delta`),
   writes HLE sentinels into IAT slots, sets up a guest stack and a minimal TEB
   (`Self` at +0x30, `StackBase`/`StackLimit`), implements `dispatch`/
   `dispatch_jmp`, and shims `ExitProcess`, `GetStdHandle`, `WriteFile` and
   `FlushFileBuffers`. Unknown imports exit 97 with a named diagnostic rather
   than being faked.
   - A second real defect was found by execution: HLE calls must consume the
     guest return address (`c->rsp += 8`), exactly like every generated `L_`
     function. Without it the emulated stack drifted 8 bytes per import and a
     later `pop` restored `0x100000000` into `rsi`, aborting dispatch. This is
     why `dispatch()` pops for calls but `dispatch_jmp()` does not.
4. **Truncated `.pdata`** (`tools/disasm/disasm64.py`, `tools/lift/generate64.py`).
   Directory sizes are clamped to bytes actually mapped by the section, so a
   truncated exception table no longer aborts catalog recovery.
5. **Code section name** (`tools/lift/generate64.py`). `read_pe` now uses
   `IMAGE_SCN_MEM_EXECUTE` characteristics instead of assuming a section named
   `.text`; imgview64.exe has none.

## 3. Reproduce

```sh
sh tools/linux/build-image.sh        # ubuntu:24.04 + build-essential/cmake/SDL2
EXE=~/Downloads/recomp-apps/windows/x64/apps/arith-calc.exe
WORK=~/l64-proof                     # must be a host path shared with the Docker VM
python3 -m tools.x64_linux_proof --exe "$EXE" --work "$WORK" \
    --out "$WORK/evidence-arith.json" --highball
python3 -m pytest tests/test_x64_linux_runner.py -q
```

Internally the driver runs `disasm64 -> generate64 -> x64_project -> linux_matrix
(Highball)` and records each exact command, exit code and output. The Linux row
is read from `linux_matrix`'s own `linux-summary.json` (ELF class/machine/hash
are measured from the produced binary).

## 4. Result

Evidence JSON: `reports/linux/x64-linux-native-arith.json` (and
`reports/linux/x64-linux-native-base64.json` for the bounded build-only case).

* Container: `Linux 6.8.0-117-generic`, `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1)`.
* ELF: ELF64, little-endian, machine `0xb7` (aarch64), 72,160 bytes, SHA-256
  `34e2c20aab45b2c7623ff5de91ac2ccd6528be6f635875205ee3627093508fc8`.
* `build_rc=0`, `run_rc=0`, `$?` of the Highball original also 0.
* Guest stdout, byte-for-byte identical after removing Highball's own
  `Launching ...` / `exit=N after ...` launcher lines (kept verbatim in the
  JSON as `original_raw_stdout`):

```text
arith:start
primes<200=46
fib(20)=6765
gcd(1071,462)=21
popcount(0xF0F0F0F0)=16
rotl(0x12345678,8)=878082066
poly=12512
mul=-8369910
div=-18
mod=-141
arith:done
```

`comparison.stdout_exact_match = true`, `comparison.exit_code_match = true`.

## 5. Limits / minimum remaining work

* This is a **console smoke target** (3 imports). It is not a claim that Win64
  games, GUI, threads or the full CRT run on Linux.
* `base64.exe` builds (so the GS fix is exercised: it contains `rd_gs64`) but
  stops at `host64: unimplemented import __acrt_iob_func`. Minimum remaining
  fix: implement the MSVC CRT shims it needs (`__acrt_iob_func`,
  `__stdio_common_vfprintf`/`vfscanf`, `__p___argc`/`__p___argv`, `_initterm`,
  `malloc`/`free`/`strlen`, critical sections, TLS) against native libc.
* The main `tools.autorecomp` CLI still refuses PE32+ input (the x64 lifter is
  invoked directly here); lifting x64 into that pipeline remains a separate
  gate (`docs/feasibility/X64_LIFTER_GATE.md`).
* Highball's Wine bottle occasionally reports `exit=1 after 0s` with no guest
  output on back-to-back runs. The proof driver retries up to three times and
  records every attempt; the committed evidence used a single successful
  attempt.
