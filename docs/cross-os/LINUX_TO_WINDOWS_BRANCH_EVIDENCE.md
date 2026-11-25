# Linux → Windows — branch evidence

Dedicated branch for Linux→Windows work, based on `develop` (`f9982ef`).

## Measured results (2026-11-25)

- The generated C from `arith-calc.exe` (also built as a native Linux ELF) was
  cross-compiled to **PE32** (SHA `70588132870a…`) and **PE32+**
  (SHA `ee7461e1d873…`) with MinGW-w64.
- Both PEs were executed with **`highball run`** (primary original runner, no raw
  Wine): exit 0 and guest stdout SHA `b6674fd6…`, byte-identical to the Windows
  original. Evidence: `LINUX_PROOF/linux-to-windows-{32,64}.json`,
  `highball-l2w-{pe32,pe32plus}.log`, binaries in `LINUX_PROOF/binaries/`.
- The MinGW-inside-Linux image `gamerecomp/linux-win-cross:local` was built and
  verified (`x86_64-w64-mingw32-gcc` / `i686-w64-mingw32-gcc` present).

## Honest limit

Process-level console proof only. The MinGW cross-build itself ran on the macOS
host after the Linux-generated source was produced; no GUI game is claimed ported.
