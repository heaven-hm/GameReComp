# macOS target matrix: what is actually proven on an arm64 host

This page records the boundary between **building for** a macOS architecture and
**running natively on** it. The two are different claims, and this repository
does not blur them. All measurements below were taken on **2026-11-24** on one
arm64 machine; they are evidence about this host, not a promise about every Mac.

## Host and toolchain (observed)

| Item | Observed value | Command |
| --- | --- | --- |
| Host CPU | `arm64` (Apple T8112) | `uname -m`, `sysctl -n hw.machine` |
| macOS | 27.2.1 (Darwin 27.2.0, `xnu-13432.41.4~25/RELEASE_ARM64_T8112`) | `sw_vers`, `uname -a` |
| SDK | `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk`, version **15.2** | `xcrun --show-sdk-path`, `xcrun --show-sdk-version` |
| SDK `macosx` archs | `arm64`, `arm64e`, `x86_64`, `x86_64h` | `SDKSettings.json` → `SupportedTargets.macosx.Archs` |
| Valid deployment targets | include `11.0` … `15.2` | `SDKSettings.json` → `ValidDeploymentTargets` |
| Compiler | Homebrew clang 23.1.6, default target `arm64-apple-darwin27.2.0` | `clang --version` |
| Registered backends | include `aarch64`/`arm64` and `x86-64` | `clang --print-targets` |

The SDK's advertised architecture list is **not** itself evidence of a build.
It only says the headers and libraries exist; the proofs below compile a file
and read its Mach-O header back.

## Capability matrix (this host, this measurement)

| Architecture | Native build | Cross-build here | Header verified | Native run here | Translated run here |
| --- | --- | --- | --- | --- | --- |
| `arm64` | yes | n/a | `cputype=16777228` (`CPU_TYPE_ARM64`) | **yes** (`proc_translated=0`) | n/a |
| `x86_64` | no host | **yes** | `cputype=16777223` (`CPU_TYPE_X86_64`) | **no — not proven, and not possible on this host** | **yes, via Rosetta 2** (`proc_translated=1`) |

**No claim is made that x86_64 macOS code ran natively on this machine.**
An x86_64 Mach-O that executes here does so under Rosetta 2 binary
translation. Rosetta is a translation layer, not an x86_64 CPU and not a native
port; nothing about performance, instruction semantics or ABI fidelity of a
native x86_64 host follows from it.

## Evidence: cross-compiling a generated unit to x86_64

A real translation unit was produced by the project's own lifter
(`tools/lift/lift64_cpu.py`, `Lifter.lift_function`) from x86-64 bytes
`mov eax, 1 ; add rax, 2 ; ret`. The generated body is:

```c
void lifted_probe(CPU *c)
{
    c->rip = 0x140000000ull;
    SET32(c->rax, 0x1u);                                         /* mov eax, 1 */
    SET64(c->rax, flags_add(c, c->rax, 0x2ull, 8));              /* add rax, 2 */
    c->rsp += 8; return;                                         /* ret */
}
```

Build command (with `SDK=$(xcrun --show-sdk-path)`):

```
clang -target x86_64-apple-macos11 -isysroot "$SDK" \
      -I runtime/recomp64_cpu generated.c -o lifted_probe_x64
```

Result:

```
file lifted_probe_x64
  lifted_probe_x64: Mach-O 64-bit executable x86_64
lipo -info lifted_probe_x64
  Non-fat file: lifted_probe_x64 is architecture: x86_64
otool -hv lifted_probe_x64
  MH_MAGIC_64   X86_64   ALL   0x00   EXECUTE  16  1368   NOUNDEFS DYLDLINK TWOLEVEL PIE
```

This proves **the generated C compiles to a genuine x86_64 Mach-O executable**
against the installed Command Line Tools SDK. It does not prove that a full
game port works on an Intel Mac. `tests/test_macos_target_probe.py`
(`GeneratedUnitCrossBuildTests`) regenerates and rechecks this at test time.

## Evidence: execution is translation, not native x86_64

The same x86_64 executable was run on this arm64 host:

```
./lifted_probe_x64
  lifted_rax=3
  exit=0
```

That looks like "x86_64 works here", but an in-process check settles it. A
probe built with the same `-target x86_64-apple-macos11` calls
`sysctlbyname("sysctl.proc_translated", ...)`:

```
./probe_x86_64            -> PROBE_OK=1, PROC_TRANSLATED=1   (Rosetta 2)
./probe_arm64             -> PROBE_OK=1, PROC_TRANSLATED=0   (native arm64)
```

`proc_translated=1` means the kernel started the process under Rosetta 2.
Rosetta's runtime is present here (`/Library/Apple/usr/libexec/oah/libRosettaRuntime`,
`/usr/libexec/rosetta/oahd`), so the run succeeds. **On an arm64 Mac without
Rosetta, or when translation is forced off, the same binary is refused:**

```
arch -arm64 ./lifted_probe_x64
  arch: posix_spawnp: ./lifted_probe_x64: Bad CPU type in executable
  exit=1
```

That is the exact failure mode recorded for x86_64-only Mach-O on a host that
will not translate it (kernel `EX_BAD_ARCH`, surfaced by shells as
"bad CPU type in executable"). The presence of Rosetta is a property of *this
machine's configuration*, not of the target, and must be re-probed on any other
host.

## What this means for the project's claims

* Existing measured results in `docs/GAME_MATRIX.md` are **macOS arm64 native**
  runs. They remain arm64 results.
* No game, generated unit or selftest in this repository is claimed to have
  run natively as x86_64. The x86_64 column here is a **build-level** result
  only.
* A Rosetta run is still useful as a smoke test that the cross-compiled binary
  links and starts, but it is a translated execution and is reported as such;
  it cannot be used to infer native Intel-Mac behaviour, timing or fidelity.
* Supporting genuine Intel Macs natively still requires running the port on
  actual x86_64 hardware (or a true x86_64 emulator that is not being claimed
  here). This work does not supply that evidence.

## Detector tool and exact commands

`tools/macos_target_probe.py` is the fail-closed probe. It builds and
header-verifies real arm64 and x86_64 executables, runs them, reads
`proc_translated` from inside each process, and prints the exact argv of every
command it ran.

```
python3 tools/macos_target_probe.py                 # human-readable report
python3 tools/macos_target_probe.py --json           # machine-readable report
python3 tools/macos_target_probe.py --require-build x86_64
    # exit 0 here: cross-compilation to x86_64-apple-macos11 is proven
python3 tools/macos_target_probe.py --require-native x86_64
    # exit 2 here: "native execution of x86_64 is not proven"
```

Fail-closed rules implemented in the tool:

* a missing `clang`, SDK, or backend → capability reported unproven with the
  reason, never assumed true;
* a failed compile/link, or a Mach-O header that cannot be parsed → build
  unproven (the parser raises on bad magic or truncated headers);
* a cputype other than the expected one → build unproven;
* a foreign-arch binary reporting `proc_translated=0` → flagged
  `inconsistent_untranslated`, not a native-execution claim;
* `--require-native x86_64` exits non-zero on this host, because Rosetta
  translation does not satisfy a native-run requirement.

Observed report on this host (condensed):

```
host: arm64 (native_arch=arm64)
SDK: MacOSX.sdk 15.2, archs=arm64,arm64e,x86_64,x86_64h
clang: Homebrew clang 23.1.6
arm64:   build native  cputype=16777228  run native      proc_translated=0
x86_64:  build cross   cputype=16777223  run translated  proc_translated=1
claims: arm64_native_execution=True  x86_64_native_execution=False
        x86_64_cross_compile=True   x86_64_run_via_rosetta=True
```

Tests: `python3 -m pytest tests/test_macos_target_probe.py -q` (15 passed on
this host, 2026-11-24). They cover the Mach-O header parser (including
fail-closed rejection of bad input), the claim/gate logic, the CLI report, and
the real generated-unit x86_64 cross-build.
