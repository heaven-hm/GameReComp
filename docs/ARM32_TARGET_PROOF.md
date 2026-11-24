# Windows x86/x64 -> macOS ARM32: target availability evidence

Date: 2026-11-24. All commands were executed on the assigned arm64 macOS host.
Verbatim output: [`reports/arm32-target-commands.txt`](../reports/arm32-target-commands.txt).
Machine-readable detector result: [`reports/arm32-target-probe.json`](../reports/arm32-target-probe.json).

## Bottom line (no 100% claim)

**There is no macOS ARM32 target or runtime on this host, and there cannot be one
on Apple Silicon.** Apple Silicon Macs execute **arm64 (AArch64) only**; macOS on
Apple Silicon has no 32-bit ARM userland ABI, no `armv7` macOS SDK, and no loader
for 32-bit ARM Mach-O files. Rosetta 2 translates x86_64, not ARM32.

What *is* possible here is narrower and is recorded as such:

| Capability | Result | Evidence |
| --- | --- | --- |
| clang backend has an ARM/Thumb emitter | yes (compiler capability only) | transcript §1: `arm`, `thumb`, `arm64_32` registered |
| Emit a 32-bit ARM Mach-O object from C | **yes, verified** (`cputype=12`) | transcript §3/§5, `file`: `Mach-O object arm_v7` |
| Link a freestanding 32-bit ARM Mach-O executable | **yes, `-nostdlib`** | transcript §3/§5, `file`: `Mach-O executable arm_v7` |
| Full link against an Apple ARM32 SDK | **no** | `ld: library not found for -lSystem` |
| iPhoneOS SDK / sysroot present | **no** | `xcrun: error: SDK "iphoneos" cannot be located` |
| Native execution of the ARM32 Mach-O | **no** | `bad CPU type in executable`, exit 127 / `OSError(86)` |
| User-mode emulator (`qemu-arm`) | **absent** | transcript §6: NOT FOUND |
| Full-system emulator (`qemu-system-arm`) | present, but not a runner for a bare Mach-O | transcript §6 |

The detector's fail-closed verdict is therefore **`CROSS_BUILD_ONLY`**, never
`AVAILABLE`. It exits 1. A "100% Windows -> macOS ARM32" result cannot honestly
be reported from this environment, and no such claim is made here.

## Host facts

- `uname -a`: Darwin 27.2.0, `RELEASE_ARM64_T8112`, `arm64`.
- `clang --version`: Homebrew clang 23.1.6, `Target: arm64-apple-darwin27.2.0`.
- `xcode-select -p`: `/Library/Developer/CommandLineTools` (no full Xcode).
- `xcrun --show-sdk-path`: `MacOSX.sdk` (15.2) only. `xcodebuild -showsdks`
  fails because this is a command-line-tools instance.
- No `iPhoneOS.platform`, `iPhoneSimulator.platform` or watchOS SDK exists;
  `/Applications/Xcode.app/...` is absent entirely.

## Compiler targets are not shipping targets

`clang --print-targets` lists `arm`, `thumb`, `arm64_32` and `aarch64_32`.
That only means the LLVM backend can *emit code* for those ISAs. It is not
evidence that macOS can load or run them. Concretely:

- `clang -target armv7-apple-macosx10.15 -c ...` is accepted but warns
  `using sysroot for 'macOS 15.2' but targeting 'thumbv7-apple-macosx10.15.0'`.
  That mismatch is the compiler admitting there is no such macOS sysroot. Apple
  never shipped an ARM32 macOS.
- `arm64_32` (`file`: `Mach-O object arm64_32_v8`) is watchOS's ILP32 ABI:
  **32-bit pointers on a 64-bit AArch64 ISA**. It is not ARM32 (a 32-bit ISA),
  it is not macOS, and there is no watchOS SDK here. It must not be counted as
  satisfying an "ARM32" requirement.

## The `xcrun` exit-0 trap

This is the main reason the detector verifies artifacts instead of exit codes:

```
$ xcrun -sdk iphoneos clang -O2 -c generated_unit.c -o gen_iphoneos.o
xcrun: error: SDK "iphoneos" cannot be located
EXIT=0
$ file gen_iphoneos.o
Mach-O 64-bit object arm64
```

`xcrun` reported the missing SDK, **returned 0**, and let the command line tools'
default clang silently produce a *host arm64* object. An exit-code-only check
would have called this a successful ARM32 build. The detector parses the Mach-O
header (`CPU_TYPE_ARM == 12`) and rejects that artifact. Unit test
`test_compiler_success_with_host_arch_artifact_is_blocked` reproduces exactly
this case.

## What was actually cross-built

Two sources were used, with no synthesised behaviour:

1. A tiny self-contained capability fixture embedded in the tool (parse/emission
   check only; explicitly **not** evidence of a game build).
2. A **real pre-existing generated C unit** from this repo:
   `highlevel/ikachan/revng/0x00401000.c`. Its only external requirements are
   typedefs and a `segment_2` array, supplied by a two-line compile-time shim
   (`-include`). No game logic was added or changed.

Results for the real generated unit:

- compile: exit 0, `Mach-O object arm_v7` (verified `cputype=12`);
- full link: exit 1, `ld: library not found for -lSystem` — no iPhoneOS SDK;
- bare link with `-nostdlib`: exit 0, `Mach-O executable arm_v7`, `lipo` says
  `architecture: armv7`;
- executing that binary: `bad CPU type in executable`, exit 127.

The bare-linked executable is a real 32-bit ARM Mach-O, but it is one unit with
no libc, no runtime and no game; it is not a runnable game and proves nothing
about behavioural fidelity.

## Emulators

- `qemu-arm`, `qemu-arm-static`, `qemu-aarch64`, `qemu-aarch64-static`: NOT FOUND.
- `qemu-system-arm` 9.2.1 is installed, but it is a **full-system** emulator: it
  boots an ARM guest machine and needs a guest kernel/rootfs, none of which are
  present. It also cannot load a Mach-O executable directly. User-mode QEMU
  emulates ELF, not Mach-O, so even installing `qemu-arm` would not make an
  `armv7-apple-ios` Mach-O runnable as-is.

So there is no execution path for the cross-built ARM32 binary here.

## Detector and tests

```sh
python3 tools/arm32_target_probe.py --out-json reports/arm32-target-probe.json
# status: CROSS_BUILD_ONLY, exit 1 on this host

# probe a real pre-existing generated C unit instead of the fixture:
python3 tools/arm32_target_probe.py \
  --source highlevel/ikachan/revng/0x00401000.c \
  --extra-arg -include --extra-arg /path/to/revng_shim_def.h

python3 -m pytest tests/test_arm32_target_probe.py -q   # 11 passed
```

The tool is bounded: fixed small input, 30 s per command (20 s in the host test),
4 KiB output cap, no network, all artifacts in a `TemporaryDirectory`. It is
fail-closed: missing clang/source, a non-ARM artifact, a failed link, a failed
execution, an absent emulator, a timeout or any unexpected exception all produce
a non-green status with verbatim tool output and explicit reasons. `AVAILABLE`
is returned only when a verified ARM32 Mach-O actually executed successfully.

## What would be required for a genuine claim

1. A real ARM32 Apple target/runtime that macOS can load and execute — none
   exists on Apple Silicon, so this requirement is unsatisfiable for "macOS
   ARM32" as literally asked.
2. If the requirement actually means iOS/watchOS armv7, an iPhoneOS SDK plus a
   licensed simulator/runtime, and a full generated game project with its
   runtime ported; neither is present here.
3. If execution must be emulated, a Mach-O-capable 32-bit ARM emulator or an ARM
   guest kernel + rootfs for `qemu-system-arm`, plus a port of the game runtime.

Until such evidence exists, the honest status is `CROSS_BUILD_ONLY` for a minimal
C unit and **NOT TESTED** for any game on macOS ARM32.
