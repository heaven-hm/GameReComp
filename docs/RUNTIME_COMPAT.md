# Runtime compatibility

The rebuilt binary is a host-native executable that embeds the lifted guest and
answers the guest's Win32 imports itself (`runtime/win32hle`). It never runs
Windows code and never delegates to Wine or an emulator. This page records what
that runtime needs from a host and what has actually been measured.

## Host requirements

| Requirement | Notes |
|---|---|
| C compiler | the generated project is C11, built with the host default toolchain |
| CMake >= 3.16 | configure + build; the CMake globs re-scan on configure |
| `pkg-config` | used to locate SDL2 below |
| SDL2 + SDL2_ttf | required; every generated project links both |
| pthreads | required |
| FFmpeg (libavformat/libavcodec/libavutil/libswresample) | optional; enables Bink `binkw32` and Miles `mss32` MP3 decoding |

If `pkg-config` cannot find SDL2/SDL2_ttf the configure step fails; there is
no silent fallback path.

## Guest memory on the host

The guest is 32-bit; the host need not be:

- On a 32-bit host with `RECOMP_FLAT_MEMORY`, a guest address is a host
  address.
- On a 64-bit host (measured: macOS arm64, Apple silicon), the guest's 4 GB is
  one based mapping at `g_mem_base`; the lifted `ADDR(va)` adds the base.
  `hle_ptr`/`hle_va` convert, and a pointer outside the mapping stops the run
  naming it, rather than truncating.
- Built with `RECOMP_MEM_BASE`, the mapping is placed at that fixed host
  address instead of a runtime-chosen one.

## Input format

The backend translates **PE32 (i386)** only. A PE32+ image is detected at
`inspect` and rejected fail-closed with the machine type in the report; it is
never guessed at. 16-bit NE/LE/DOS inputs are outside the pipeline's input
contract.

## Headless execution

`autorecomp` runs the rebuilt binary with `SDL_VIDEODRIVER=dummy` and
`SDL_AUDIODRIVER=dummy`, in the assets directory, with stdin from `/dev/null`
and a wall-clock timeout; the process group is killed on timeout on POSIX
hosts. `--interactive` keeps the real SDL drivers. The runtime's own selftests
(`runtime/win32hle/build_selftests.sh`) also build and run headless.

## Exit codes

`autorecomp` itself:

| Code | Meaning |
|---|---|
| 0 | verification is `scenario-pass` |
| 2 | a stage failed (see the printed failed stages and the report) |
| 3 | the pipeline completed but verification is not a pass (`partial`, etc.) |

The rebuilt binary:

| Code | Meaning |
|---|---|
| 0 | the guest program returned 0 |
| 2 | boot failed: unresolved imports, named in its output |
| -6 / SIGABRT | aborted at load or run time — see the macOS section below |

## macOS: Homebrew freetype newer than the OS

A Homebrew `freetype` bottle built against a newer macOS than the host can make
**every binary that links SDL2_ttf abort in dyld** before `main` runs, with
`Symbol not found: _HVF_...` referring to a framework the host does not have.
This is an environment issue, not a runtime bug: the abort happens at dynamic
linking, before any lifted code executes.

Diagnose:

```
otool -L "$(brew --prefix freetype)/lib/libfreetype."*.dylib | head
sw_vers -productVersion
```

If the dylib references a framework or libSystem version newer than the host
provides, rebuild freetype from source on this host:

```
brew reinstall --build-from-source freetype
```

and rebuild the generated project (`cmake --build build`) so it relinks. A run
recorded before this fix keeps exit code -6 and `launch-failed` verification;
re-run `autorecomp` afterwards to capture a genuine result.

## Measured hosts

- macOS arm64 (Apple silicon), 64-bit based guest mapping: the corpus sweep in
  `table.md` was produced here.
- Linux (x86-64 and arm64 hosts, gcc `-m32` flat and 64-bit based modes) is
  exercised by the runtime selftests; see `runtime/win32hle/README.md`.

Windows as a *host* is not measured: the pipeline contains the Windows process
handling (`.exe` suffix, `kill()` instead of process groups) but no run in this
repository was performed on a Windows host. Cross-compiling the delivered
project to Windows is also unmeasured. Do not assume either works until a run
is recorded.

## Unimplemented imports

Imports the runtime does not answer are reported by name and counted; the
rebuilt program exits 2 instead of returning fabricated success. A project
whose bind fails is `launch-failed` in verification, and the import list is in
`reports/run.json`.
