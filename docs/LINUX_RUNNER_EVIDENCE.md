# Linux runner evidence (real project, local measurements)

This document records what was actually executed on this host on 2026-11-24.
It is not a claim of gameplay, frame or Windows-oracle equivalence. Every
status below is the status returned by the tool or runner; BLOCKED/FAIL are not
rewritten as PASS.

## Host and image

* macOS 27.2.1 / arm64, Docker via Colima (Server 29.5.2, linux/aarch64).
* `alpine:latest` is present locally, but `apk add --no-cache build-base cmake
  pkgconf sdl2-dev sdl2_ttf-dev` stalled in the package download on the earlier
  attempt; that attempt was terminated. No Linux build was produced by it.
* `ubuntu:24.04` is present locally and `apt-get update` completed
  (`Fetched 36.3 MB in 38s`), so a toolchain image is now built once with
  `tools/linux/Dockerfile` and reused offline by `tools/linux_matrix.py`.

Exact build command:

```sh
sh tools/linux/build-image.sh
# docker build -t gamerecomp/linux-runner:local -f tools/linux/Dockerfile tools/linux
```

`tools/linux_matrix.py` now probes the image once (`cmake`, `cc`, `pkg-config`,
`sdl2`/`SDL2_ttf` versions), defaults to that prebuilt image and adds
`--network none` to build and run containers. If the image lacks the toolchain,
the run fails closed (exit 3) unless `--provision apk|apt` is explicitly chosen;
those modes are recorded as network-dependent in the summary.

A second real defect was found on the first corrected run: staging under
`/private/var/folders/...` was bind-mounted by Colima as an **empty** directory,
so CMake reported `The source directory "/work" does not appear to contain
CMakeLists.txt` even though the host staging directory held both
`CMakeLists.txt` and `src/`. The runner now writes a marker file and checks it
inside the container (`verify_bind_mount`) before any build, returning exit 3
with a "put --out/--scratch under a host path shared with the Docker VM" reason.
The completed run below therefore uses `--out "$HOME/p3-results/linux"`.

A third real defect appeared once the mount worked: the generated
`src/recomp/gen/recomp_image.S` contains
`.incbin "/absolute/generation/host/.../recomp_image.bin"`. The `.bin` was
staged, but GAS could not read the original absolute path inside the container
(`Error: file not found: /private/var/folders/.../recomp_image.bin`), so the
build stopped at the assembler. `tools/linux_matrix.py` now rewrites staged
`.incbin` paths to `/work/...` and fails closed if a referenced binary was not
staged; the rewrite is recorded in the summary row.

The next build exposed a genuine Linux host-code defect:
`runtime/win32hle/host.c` used the x86_64-only expression
`uc_mcontext.gregs[REG_RIP]` unconditionally in its Linux branch, which fails
on aarch64 with `'mcontext_t' has no member named 'gregs'` and
`'REG_RIP' undeclared`. The fault-report path now selects `REG_RIP` (x86_64),
`REG_EIP` (i386), `uc_mcontext.pc` (aarch64) or `arm_pc` (arm). A regression
test syntax-checks the real `host.c` inside the Linux toolchain image.

A further Linux-only error appeared at 97% of the next build:
`wsock32.c:788: error: 'struct sockaddr_in6' has no member named 'sin6_len'`.
That field is BSD/macOS-only; two earlier assignments in the same file were
already guarded with `#ifdef __APPLE__`, this one was not. It is now guarded,
with a source regression test and a Linux `cc -fsyntax-only` check for both
`host.c` and `wsock32.c`.

The first ELF launch (exit 64 from the no-argument usage path) was followed by
a launch with the real PE as `argv[1]`, which aborted with
`*** buffer overflow detected ***`. A gdb batch backtrace in the container
pinned it to `__GI___chk_fail -> __realpath_chk -> boot`: glibc's fortified
`realpath()` requires a `PATH_MAX`-sized output buffer, while `host.c` passed
`char image[1024]` (and `boot_embedded` passed a similarly short `dir`). Both
buffers are now `PATH_MAX` and `<limits.h>` is included. The runner also gained
`--original <real-PE>`, which bind-mounts that file read-only as `/original`
and passes it as the ELF's first argument, so the run is part of the recorded
matrix row rather than an unreproducible manual command.

Host-clang fallback considered and not used: the host has clang 23 and Zig, but
no Linux sysroot or `ld.lld` on the clang toolchain, and the generated project
requires Linux SDL2/SDL2_ttf development headers that the host's Homebrew SDL2
(Mach-O) cannot supply. The Docker route uses a real Linux userspace, so no
cross-target approximation is claimed.

## Real generated project

Generated from the real PE32 original, no synthetic stand-in:

```sh
python3 -m tools.autorecomp "$HOME/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe" \
  --project-dir <scratch>/arith_calc --out-mode source-only --no-run --no-table \
  --no-progress --input-os windows --input-arch x86 \
  --target-os host --target-arch host
```

Observed result: `inspect ok` (i386 PE32, 3 imports, entry `0x00401B00`),
`recover ok` (33 functions, 6645 instructions), `translate ok` (2 source files,
848312 bytes, 0 lift errors), `project ok`, `report ok`; score 50/100,
verification `not-run`. Original SHA-256
`9b59ee613acc7b5a1512f8a5fcc5f65317f0fa79452d01728e1715b63f23d7a6`.

## Linux build + ELF run (completed, x86 PE32)

Final runner command (all inputs on the command line; no hard-coded paths in
the tool):

```sh
python3 -m tools.linux_matrix \
  --project <scratch>/arith_calc \
  --original "$HOME/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe" \
  --out "$HOME/p3-results/linux" \
  --image gamerecomp/linux-runner:local --provision prebuilt \
  --parallel 1 --jobs 4 --build-timeout 900 --run-timeout 20
```

Exit code 0; row `arith_calc build=0 run=0 elf=OK 101.5s`. Image toolchain:
`cmake 3.28.3`, `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`, pkg-config
`1.8.1`, SDL `2.30.0`/`2.22.0`, Linux `6.8.0-117-generic`. `provision=prebuilt`,
`network=none` (no package download during build or run).

The produced binary was inspected by the runner itself:
`ELF64`, LSB, machine `0xb7` (`aarch64`), 1,669,640 bytes, SHA-256
`4b27641cbf41f3f74bb6936862c654d6a707c0a7cac969742b0cd0d358b7bd51`.
Build log ends with `[100%] Built target arith_calc`; the original PE was
mapped at `0x00400000`, 3 imports bound, 0 unresolved.

Complete captured ELF stdout/stderr (`run_rc=0`):

```text
[pe] /original mapped at 0x00400000, span 0x5000
[pe] bound 3 imports (0 delay), 0 unresolved
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
[host] /original booted: entry 0x00401B00, 0 unresolved imports
[win32hle] ExitProcess(0) from guest 0x00401B00
```

Raw evidence: `reports/linux/linux-summary.json`,
`reports/linux/generate-arith.log`, `reports/linux/gdb-backtrace.log`. This is
a process-level Linux run of the generated project; it is not a frame,
gameplay, or Windows-oracle equivalence claim.

## Dual-engine evidence (Unicorn + Frida) and Windows cross-build

The hybrid driver was run on the REAL original executable:

```sh
python3 -m tools.hybrid_diff \
  --exe "$HOME/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe" \
  --live-frida --hook ExitProcess --hook MessageBoxW --hook GetTickCount \
  --timeout 5 --out <evidence>/hybrid-arith.json
```

Result: `status=BLOCKED` (exit 1). No engine result was upgraded to PASS:

* **Unicorn** (`tools/hybrid_diff.unicorn_signal`): BLOCKED —
  `OracleFault: the ORIGINAL's bytes at 0x00401B00 faulted on case 0:
  Invalid memory fetch (UC_ERR_FETCH_UNMAPPED)`. The entry state is not a
  mapped guest image, so no CPU-level verdict was produced.
* **Frida** (`tools/hybrid_diff.frida_signal`, with `--live-frida`): BLOCKED —
  `NotSupportedError: unexpected error while probing dyld of target process`.
  The Highball Wine binary exists at
  `~/Library/Application Support/Highball/engines/x64-crossover26.3-r27/engine/bin/wine`,
  but Frida could not inject/spawn it on this macOS arm64 host; no call trace
  was collected. A direct bounded attempt with `tools/frida_probe.py --spawn
  "$WINE" 'C:\recomp-oracle\arith-calc.exe' --hook ExitProcess --timeout 3`
  returned exit 3 with `frida probe failed: unexpected error while probing dyld
  of target process`; `--dry-run` only confirmed the interceptor script is
  syntactically valid. No live attach or call trace is claimed.
* **Native replay** (`tools/input_replay_diff.diff_one`): BLOCKED —
  `no native binary supplied`, because the runner's native artifact is a Linux
  ELF that cannot be executed by the macOS host. No native replay verdict is
  fabricated.

Project-scoped Unicorn differential (`tools/lift/difftest.py --project`) was
also run against the real original. With explicit function addresses it
returned `0 agree, 3 not` (exit 1), with precise per-address reasons:
`original-faulted` (`UC_ERR_FETCH_UNMAPPED` / `UC_ERR_READ_UNMAPPED`) for
`0x00401000` and `0x00401131`, and `nothing-compared` for `0x004010A0` (the
tool refuses to pass a case where the observed memory cannot be named).
These are engine blockers/inconclusive cases, not a 100% match. The report is
in `reports/linux/difftest-arith.json`. A wider sweep over all 33 recovered
function addresses was attempted with a 900-second bound; it was terminated by
that bound and produced **no** verdict, so it is recorded as TIMED OUT rather
than as evidence either way.

Windows cross-build (`tools/win32_crossbuild.py`) on the same real generated
project returned `status=FAIL` (exit 1), not BLOCKED and not PASS: MinGW-w64
`i686-w64-mingw32-gcc` exists on this host, but compilation failed with
`SDL2/SDL.h: No such file or directory`, `recomp_types.h: No such file or
directory` and `poll.h: No such file or directory` from the POSIX win32hle
runtime. No PE was produced and no Windows execution is claimed. Full compiler
output is in `reports/linux/win32-crossbuild.json`.

## x64 (PE32+) to Linux — attempted, BLOCKED

Real x64 PEs under `~/Downloads/recomp-apps/win64` were used; no synthetic
input. Function-catalog recovery with `tools/disasm/disasm64.py`:

* `imgview64.exe` (12 KiB): **FAIL** —
  `struct.error: unpack_from requires a buffer of at least 25612 bytes for
  unpacking 12 bytes at offset 25600 (actual buffer size is 11776)` while
  reading `.pdata`. No catalog produced.
* `guess.exe` (158 KiB): OK — base `0x140000000`, 84,022 code bytes,
  `pdata=185`, 269 functions (36 thunks). `tools/lift/generate64.py` then
  closed a 321-function catalog over 6 rounds, emitted 4 translation units,
  `lift_errors=0`, `recomp_todo_sites=4`, `imports=191`.

Compiling those generated units in the aarch64 Linux container (gcc 13.3.0,
`-I` for `runtime/recomp64_cpu`) is **BLOCKED**, not a PASS:

```text
/tmp/ccexGam8.s:22984: Error: unknown mnemonic `movq' -- `movq %gs:(x3),x3'
/tmp/ccexGam8.s:25905: Error: unknown mnemonic `movq' -- `movq %gs:(x0),x0'
```

The generated x86-64 guest code contains `%gs` segment-prefixed inline assembly
that an aarch64 assembler cannot encode. A native x86-64 Linux runner would be
required (`--platform linux/amd64` with an amd64 toolchain image); only arm64
images are cached locally and pulling another image was outside this bounded
attempt. No PE32+ Linux build or run is claimed. Logs:
`reports/linux/x64-*.log`.

## Status

* Linux **runner**: works — real generated PE32 x86 project built in a Linux
  container and the resulting ELF launched; exact log/hash in the summary.
* Linux **x64 runner**: BLOCKED — x86-64 inline asm cannot be assembled on the
  locally available aarch64 Linux image; no amd64 runner provisioned.
* Linux **game-level** status remains PARTIAL/NOT TESTED for a Windows oracle:
  no Windows original was executed as a Linux oracle here, and the dual-engine
  evidence is BLOCKED/inconclusive. No Linux PASS row is claimed.
