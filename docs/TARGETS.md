# Targets: what this pipeline builds, and what it does not

Every claim in this project is scoped to a target. This page records the matrix
explicitly, because "it builds" and "it builds for the target you meant" are
different statements and the difference has been the source of most over-claims in
static recompilation projects.

## What is built today

```
Windows PE32 x86  ──►  macOS ARM64 native  (32-bit guest state)
```

| | |
| --- | --- |
| Guest | i386 PE32, Windows 95/98/XP-era games |
| Host binary | `Mach-O 64-bit executable arm64` — a real native binary, no emulator, no Wine |
| Guest state | **32-bit throughout**: `uint32_t g_eax … g_esp`, one 4 GB guest address space reserved at `RECOMP_MEM_BASE` and indexed by 32-bit guest VAs |
| CPU | the game's own instructions, lifted to C at build time and compiled by the host's clang |
| Win32 | `runtime/win32hle/` — the imports the game calls, implemented over libc and SDL2 |

**This is a host port, not a target architecture migration.** The host is 64-bit
and the guest state is not. That distinction matters and the project will not blur
it: a 64-bit process wrapping 32-bit guest state is a genuine native build of that
guest's code, and it is *not* the same claim as producing a genuinely 64-bit
program with 64-bit pointers, a 64-bit ABI and correct sign-extension of every
pointer value.

## The matrix

| Guest | Host | State | Notes |
| --- | --- | --- | --- |
| PE32 x86 | **macOS ARM64** | 32-bit | **built and run** — this is the configuration every measured result on this repository was produced in |
| PE32 x86 | macOS x64 | 32-bit | builds from the same source; **not separately verified here**, and no machine has been used to check it |
| PE32 x86 | Linux x86 32-bit | 32-bit | the flat-memory runtime path (`HLE_BASED=0`, `gcc -m32`) exists and the selftests run on it; **no game has been run in this configuration on this host** |
| PE32 x86 | Linux x86-64 | 32-bit | expected to work through the based-memory path; **not verified** |
| PE32 x86 | **Windows x64** | 32-bit | **not built.** No Windows toolchain or host is available here. Producing it means cross-compiling or building on Windows; nothing about it has been tested |
| PE32 x86 | **Windows x86** | 32-bit | **not built.** Same reason |
| PE32+ x86-64 | Windows x64 | 64-bit | **not supported.** The lifter targets i386; a 64-bit guest is a different machine model, not a flag |
| PE32+ x86-64 | macOS ARM64 | 64-bit | **not supported.** Same reason |
| PE32 ARM / ARM64 | any | — | **not supported.** No lifter |
| 16-bit NE (Windows 3.x) | any | — | **not supported.** A different segment model; the DOS32/NET paths in `docs/` describe reference material, not capability |

The wizard validates this before a run rather than after: an executable whose
header is not `PE32 i386` is reported as an unsupported target and the reason is
stated, because a successful build for a target the project does not claim would be
a claim it cannot back.

## Why the untested rows are listed rather than omitted

An absent row reads as "we forgot". A row marked *not built* or *not verified* with
the reason reads as "this is the boundary of what is known", which is the accurate
statement and the one a reader can act on. The difference between
`PE32 x86 → macOS ARM64` and `PE32 x86 → Windows x64` is not effort; it is that one
has been run and the other has no host to run it on.

## What a genuinely 64-bit target would require

Recorded so the work is scoped rather than implied:

1. **a 64-bit lifter** — x86-64 decoding, the System V / Windows x64 ABIs, and the
   register/flag model that goes with them; the current lifter is i386 only;
2. **guest-state widths** — `uint32_t` to `uintptr_t` throughout the runtime, and
   every truncation and sign-extension audited rather than inherited;
3. **a Windows runtime** — the Win32 shims exist for POSIX hosts; a Windows build
   needs implementations over the real API or an equivalent;
4. **a verification oracle for that configuration** — the per-function oracle
   compares against the original's bytes, which is target-independent, but running
   the rebuilt binary and comparing its behaviour is not.

Until those exist, `docs/GAME_MATRIX.md` and the README report outcomes from the
one configuration that has been measured, and say so.
