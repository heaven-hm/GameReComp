# Linux-host → Windows-target cross-build (source level only)

**This page is about SOURCE cross-compilation, not binary translation.** No
Linux ELF, macOS Mach-O, or any other executable binary is converted into a PE
here. The module `tools/linux_to_windows.py` builds real C source on a Linux/
macOS host with MinGW-w64 (reusing `tools/win32_crossbuild.py`), verifies the
PE header, and executes the PE under the local Highball/CrossOver Wine engine
(reusing `tools.wine_oracle.py`). The name means "build on Linux for Windows".

Scope of the evidence below: **one pre-existing host C source** produces a real
PE32 and PE32+, and the PE32+ prints real captured stdout under Wine. It is not
evidence that any recompiler-generated game project cross-builds to Windows.

## 1. Upstream research (recorded 2026-11-24 via `gh api`)

All three are source → Windows PE toolchains. Star counts are a dated snapshot,
not a permanent fact.

| Project / command | Exact URL | License (source of truth) | Stars | Direction |
|---|---|---|---|---|
| MinGW-w64 `i686-w64-mingw32-gcc`, `x86_64-w64-mingw32-gcc` | https://github.com/mingw-w64/mingw-w64 | GitHub API `license.spdx_id = NOASSERTION`; root `COPYING`: "With exception of certain parts that are prominently marked as being in the Public Domain, BSD, or LGPL this Software is provided under the Zope Public License (ZPL) Version 2.1." Copyright (c) 2009-2013 the mingw-w64 project. | 605 | C/C++ **source** → Windows PE32 (i686) / PE32+ (x86-64). This is the toolchain actually used. |
| LLVM / Clang `clang --target=x86_64-w64-mingw32` | https://github.com/llvm/llvm-project | GitHub API `NOASSERTION`; root `LICENSE.TXT`: "The LLVM Project is under the Apache License v2.0 with LLVM Exceptions". | 41580 | C/C++ **source** → Windows PE when a MinGW sysroot/headers are supplied. Locally **BLOCKED**: Homebrew clang 23.1.6 reports `fatal error: 'stdio.h' file not found` without a MinGW sysroot. |
| Zig `zig cc -target x86_64-windows-gnu` | https://github.com/ziglang/zig (repo description: "Moved to Codeberg" → https://codeberg.org/ziglang/zig) | MIT (`LICENSE`: "The MIT License (Expat), Copyright (c) Zig contributors"). | 43300 | C/C++ **source** → Windows PE via bundled cross libc. Locally **BLOCKED**: installed `zig 0.13.0` fails at startup with `dyld: Library not loaded: /opt/homebrew/opt/z3/lib/libz3.4.13.dylib`. |

Reproduce with:

```sh
gh api repos/mingw-w64/mingw-w64 --jq '{license:.license.spdx_id,stars:.stargazers_count,url:.html_url}'
gh api repos/llvm/llvm-project/contents/LICENSE.TXT --jq .content | base64 -d | head -3
gh api repos/ziglang/zig/contents/LICENSE --jq .content | base64 -d | head -3
```

**Recommendation:** use the installed MinGW-w64 GCC toolchains for this repo's
source-level Windows cross-builds. They are already wired into
`tools/win32_crossbuild.py`. Clang needs an explicit MinGW sysroot and the local
Zig install is broken; neither is required for the working path. There is no
"ELF binary → PE binary recompiler" involved in, or claimed by, this work.

## 2. Local Linux ELF corpus (part b)

`~/Downloads/recomp-apps/linux` contains only eight stripped, statically linked
ELF binaries — no source of any kind:

```
x64/apps/busybox    ELF 64-bit LSB executable, x86-64, statically linked, stripped
x64/apps/micro      ELF 64-bit LSB executable, x86-64, statically linked, Go Build
x86/apps/busybox    ELF 32-bit LSB executable, Intel 80386, statically linked, stripped
x86/apps/micro      ELF 32-bit LSB executable, Intel 80386, statically linked, Go Build
arm32/apps/{busybox,micro}   ELF 32-bit LSB executable, ARM, EABI5, ...
arm64/apps/{busybox,micro}   ELF 64-bit LSB executable, ARM aarch64, ...
```

`find ~/Downloads/recomp-apps/linux -type f \( -name '*.c' -o -name '*.h' -o -name 'Makefile' \)`
returns **nothing**. Source-level cross-compilation is therefore **BLOCKED for
these particular apps**: there is no legitimate source input to compile, and
translating their ELF bytes into PE would be binary translation (out of scope).
Other areas of `~/Downloads/recomp-apps` do contain C source (e.g.
`windows/cli-apps/...`), which is how the smoke test below found a real input.

## 3. What was implemented

`tools/linux_to_windows.py`:

* Reuses `cross_build()` / `find_cc()` from `tools/win32_crossbuild.py` — there
  is no second compiler implementation.
* Reuses `find_wine()` / `run_original()` from `tools/wine_oracle.py` and
  `analyze_pe()` from `tools/pe/pe_analyze.py` for header verification.
* Discovers **only pre-existing** C sources (env/argument path first, then
  ordered host roots), validates them (regular file, non-empty, contains
  `main(`), and records absolute path, byte count and SHA-256.
* Never writes a source file. Fail-closed codes: `NO_REAL_SOURCE`,
  `SOURCE_NOT_FOUND`, `SOURCE_EMPTY`, `SOURCE_NO_MAIN`, `NO_MINGW_COMPILER`,
  `CROSSBUILD_BLOCKED`, `CROSSBUILD_FAIL`, `PE_HEADER_INVALID`,
  `NO_WINE_ENGINE`, `WINE_RUN_ERROR`, `WINE_TIMEOUT`, `WINE_STDOUT_MISMATCH`.
* Exit codes: 0 = OK, 2 = BLOCKED, 1 = FAIL.

`tests/test_linux_to_windows.py` adds 7 tests including four BLOCKED-path
checks (including the exact `NO_REAL_SOURCE` diagnostic and proof that no PE is
written), PE32/PE32+ header checks, and a real Wine stdout check.

## 4. Real commands and output

Host C source (pre-existing, 1755 bytes, not copied into this repo):

```
/Users/haseeb-mir/Downloads/Other/gitHub_C/FloatMemory.c
sha256 e4ed6cc9fe45006b67f541ebbfb2d74f94c609474991aa5778eecd52c8751b64
```

```sh
python3 -m tools.linux_to_windows --out /tmp/p12_l2w32.exe --bits 32 --no-run
python3 -m tools.linux_to_windows --out /tmp/p12_l2w64.exe --bits 64 \
    --expected-stdout 'is not equal'
```

PE32 result (trimmed JSON): `"status": "OK"`, `"code": "PE_BUILT_NOT_RUN"`,
`"compiler": "/opt/homebrew/bin/i686-w64-mingw32-gcc"`,
`"pe": {"mz": true, "pe_type": "PE32", "machine": "i386",
"subsystem": "Windows Console", "bytes": 130966}`.
`file /tmp/p12_l2w32.exe` → `PE32 executable (console) Intel 80386, for MS Windows`.

PE32+ result: `"pe_type": "PE32+"`, `"machine": "x86-64"`, 147277 bytes.
`file` → `PE32+ executable (console) x86-64, for MS Windows`.

Wine run (real stdout, PE32+):

```
exit_code 0
stdout: 4 Blocks of Float  (1 byte each) : ... is not equal ... == says equal
stderr: (empty)
wine: .../Highball/engines/x64-crossover26.3-r27/engine/bin/wine
command: [wine, "C:\\recomp-oracle\\p12_l2w64.exe"]
```

The PE32 binary also runs under the same engine (exit 0, same stdout) once Wine
has warmed up; the first cold 30 s attempt timed out, so the default Wine
timeout in the module is 120 s.

Blocked path example (`--source /tmp/p12_no_such_source.c`) writes no PE and
returns:

```json
{"status": "BLOCKED", "code": "SOURCE_NOT_FOUND",
 "reason": "source file not found: /tmp/p12_no_such_source.c"}
```

## 5. Blockers and non-claims

* **Generated game projects remain BLOCKED.** A real generated C file in this
  repo, `highlevel/doukutsu-sweep/0x0040F320.c`, includes the POSIX runtime
  header `win32hle.h` and has no `main()`. Passing it to the existing
  cross-builder yields verbatim:
  `fatal error: win32hle.h: No such file or directory`. Porting
  `runtime/win32hle` to MinGW/Windows is a separate, unfinished task. This
  module therefore cannot claim a Windows build of any recompiled game.
* The host source used above is **not** recompiler output and is not bundled
  here; it is only a deterministic end-to-end check of the source → PE → Wine
  plumbing. The previously mentioned `RECOMP_WIN_HELLO_OK` PE came from a
  temporary generated C file that is not present in this tree; this work does
  not reuse or claim that token.
* Clang target builds and `zig cc` are **not verified** here (blockers in §1).
* The Wine result is Wine, not retail Windows. No 100%, full-corpus, or game
  equivalence claim is made.

## 6. Provenance / licence notes

No third-party source code was copied into this change. The module only calls
installed tools: MinGW-w64 GCC 16.2.0 (Homebrew `mingw-w64 14.0.0_3`) under
ZPL-2.1 with the exceptions in its root `COPYING`, LLVM/Clang 23.1.6 under
Apache-2.0-with-LLVM-exception, and Zig 0.13.0 under MIT (the latter two only
researched, not used in the working path). The Wine engine is locally installed
Highball/CrossOver software, invoked as an external program via existing repo
code. The pre-existing host C source is referenced by absolute path and SHA-256
only; it is not redistributed, and no licence is assumed for it.
