# Windows console cross-build port (P4)

## What landed

A generated project can now be **source-recompiled** with MinGW-w64 into a real
Windows PE32 or PE32+ and executed under Highball's Wine.  This is not binary
translation and not an emulator: the generated C and the win32hle runtime are
compiled by `i686-w64-mingw32-gcc` / `x86_64-w64-mingw32-gcc`.

New/changed files:

- `runtime/win32hle/win32_port.h` / `win32_port.c` — the port layer. Real Win32
  implementations: `mmap` -> `VirtualAlloc` / `MapViewOfFileEx`, `munmap` ->
  `VirtualFree` / `UnmapViewOfFile`, `dlopen` -> `LoadLibraryA`,
  `dlsym` -> `GetProcAddress`, `poll` -> `WSAPoll`, `statvfs` ->
  `GetDiskFreeSpaceExA`, `realpath`/`localtime_r`/`timegm`/`pread`/`arc4random`
  shims. `fork` and `readlink` fail with `ENOSYS` — they have no Win32
  equivalent here and are never faked.
- `runtime/win32hle/win32_compat/{dlfcn.h,poll.h,sys/mman.h,sys/statvfs.h}` —
  redirected POSIX headers, found via `-I<runtime>/win32_compat`, so the
  runtime sources stay unchanged on POSIX hosts.
- `runtime/win32hle/host.c` — Windows-only guards: no sigaction/ucontext fault
  report (no SEH handler in this port), and the embedded-image weak defaults
  are `extern` on PE because MinGW folds a same-TU weak definition to zero and
  silently hid the generated strong image symbols.
- `runtime/win32hle/guest_mem.c`, `win32hle.h` — optional `RECOMP_BASED_MEMORY`
  and `RECOMP_GUEST_SPAN` so a PE32 guest uses based memory (guest VA =
  offset into a 1 GB reservation) instead of occupying host low addresses.
- `tools/autorecomp/projectgen.py` — copies the port layer and compat headers
  into every generated project; `recomp_image.S` now uses the correct PE/COFF
  symbol prefix for i386 (`_name`) and x86-64 (`name`) in `.rdata`.
- `tools/win32_console_build.py` — fail-closed cross-build tool (MZ + optional
  header magic verified, verbatim compiler output on failure).
- `tests/test_win32_console_build.py` — cross-build, Wine-vs-native byte match,
  and Unicorn/Frida evidence tests.
- `reports/win32-console-port-evidence.json` — recorded evidence, including the
  exact BLOCKED reasons for the differential engines.

## Reproduce

```bash
# 1. generate a source-only project for the console corpus app
python3 -c 'from tools.autorecomp.pipeline import Pipeline, run_pipeline; \
p=Pipeline("/Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe", \
project_dir="/tmp/arith-proj", build=False, run_binary=False, headless=True); \
run_pipeline(p, ["inspect","recover","translate","project"])'

# 2. cross-build PE32 and PE32+
python3 tools/win32_console_build.py --project /tmp/arith-proj \
    --out /tmp/arith32.exe --bits 32
python3 tools/win32_console_build.py --project /tmp/arith-proj \
    --out /tmp/arith64.exe --bits 64

# 3. native reference and Wine runs
cd /tmp/arith-proj && cmake -S . -B build && cmake --build build -j
./build/arith_calc > native.out
WINE="$HOME/Library/Application Support/Highball/engines/x64-crossover26.3-r27/engine/bin/wine"
WINEPREFIX="$HOME/Library/Application Support/Highball/bottles/Games" WINEDEBUG=-all \
    "$WINE" /tmp/arith32.exe > wine32.out

# 4. full test/evidence suite
PYTHONPATH=. python3 -m pytest tests/test_win32_console_build.py -v
```

Measured on 2026-11-24: PE32 and PE32+ built, both exited 0 under Highball's
Wine (CrossOver 26.3 engine), and both stdout streams were **byte-identical** to
the native macOS rebuild (`arith:start ... arith:done`, SHA-256
`052411d6d14a3084c8e18b725e0935a56ed49d9788b641b12f11784cce74152a`).
The cross-built PE maps the embedded guest image at 0x00400000, binds all 3
imports (ExitProcess / GetStdHandle / WriteFile) with 0 unresolved.

## Scope and hard limits (read before generalising)

- **Console apps only.** The linked runtime subset is guest_mem, hle_path,
  host, host_lite, kernel32, module, pe_loader and the port layer. GUI/audio
  modules (SDL2, SDL2_ttf, D3D, DDraw, DSound, DInput, OpenGL, Bink, MSS, ...)
  are excluded: MinGW-w64 has no SDL2 headers in this environment and a console
  guest does not call them. Their shim tables are empty stubs. A guest that
  imports a GUI name fails at boot with `unimplemented import <NAME>`; that is
  a real failure, not a silent downgrade. **GUI games are not supported.**
- **No POSIX fault diagnostics on Windows.** `sigaction`/`ucontext` have no
  Win32 counterpart in this port; a crash gets the platform's default handling,
  not the `[win32hle] fatal signal ... pc ...` report seen on POSIX hosts.
- **PE32 guest span is 1 GB** (`RECOMP_GUEST_SPAN=0x40000000`). It is required
  because Highball's Wine already occupies low host VAs (a flat 32-bit guest
  reserving 0x00400000 fails with `map @ 0x00400000 failed (the range is in
  use)`). Console VAs fit; a guest referencing VAs above 0x40000000 is out of
  scope. PE32+ uses the normal 4 GB based model.
- **`fork`, `readlink`, POSIX symlink semantics** return `ENOSYS`; `O_CLOEXEC`
  is a no-op. Console apps in the measured corpus do not use them.
- **Differential engines are NOT green.** `tools/hybrid_diff.py` was run with
  live Frida against the original arith-calc.exe. Unicorn is BLOCKED
  (`OracleFault: ... UC_ERR_FETCH_UNMAPPED` at the entry), Frida is BLOCKED
  (`NotSupportedError: unexpected error while probing dyld of target process`),
  and the original-under-Wine leg of `input_replay_diff` timed out in the
  shared bottle. The overall hybrid status is therefore **BLOCKED**, with the
  exact reasons in `reports/win32-console-port-evidence.json`. No equivalence
  percentage is claimed from those engines. The Wine-vs-native byte comparison
  above is separate, process-level evidence for this one scenario only; it is
  not gameplay, frame, audio or cross-hardware evidence.
- Output comparison is for the captured stdout of this single scenario; the
  Wine engine emits FreeType warnings on stderr, which are reported but not
  treated as program output.
