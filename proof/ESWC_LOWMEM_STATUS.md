# TrackMania Nations ESWC low-memory port: BLOCKED (honest verdict)

No native binary and no frames were produced. Nothing here is a PASS.

| Item | Measured result |
|---|---|
| Input | `TmNationsESWC.exe`, 8,989,696 B, sha256 `822def6627f020035ef0819cf9f85493e952a2efb450d23770b330311ad8109d`, i386 PE32, 259 imports |
| Recover | 5,593.8 s, 84,249 functions, 10,670,870 instructions |
| Translate | 1,391.2 s, 699 source files, 1,108,459,341 B, 0 lift errors |
| Build config | `Release`, `-O0 -DNDEBUG`, `--parallel 4`, 741 objects completed, then `make` error 2 at 99% |
| Native binary | none (`build/TmNationsESWC` absent) |
| App launches / frames | 0 / 0 |

## Exact blocker

Three generated units exceed the 2 MiB soft cap by ~5x and neither Apple clang 16 nor GCC 14 could finish them in the allowed time:

| Unit | Bytes | Lines | Functions | Clang (last measured) | GCC-14 retry |
|---|---:|---:|---:|---|---|
| `recomp_0377.c` | 13,578,357 | 350,221 | 33 | 53:51 wall / 13:37 CPU, killed | 300.1 s timeout, no object |
| `recomp_0386.c` | 13,105,375 | 338,670 | 49 | 53:47 wall / 13:34 CPU, killed | 300.1 s timeout, no object |
| `recomp_0387.c` | 13,068,999 | 337,665 | 1 | 53:47 wall / 13:35 CPU, killed | 300.1 s timeout, no object |

A 3-second sample of clang cc1 placed 1914/1915 samples in LLVM
`BranchRelaxation::fixupConditionalBranch` / `adjustBlockOffsets`, i.e. a
superlinear pass over one enormous machine function — not ordinary compile
cost for a 13 MB file. The generator's `--split-bytes` is a **soft** cap:
`recomp_0387.c` is a single 13 MB generated function emitted whole
(`single_oversized_function: true` in `recomp_units.json`), so finer
per-unit splitting cannot fix it.

## Minimal next action

Change the generator so one oversized guest function can be emitted as
several C functions with a trampoline (or otherwise avoid a single enormous
machine basic-block graph), regenerate only these units, then rebuild and
link. Do not relaunch the unchanged units. Even after a successful build the
verdict would be PARTIAL: there is no Windows execution oracle on this host.

Full machine-readable evidence: `evidence/proofs/tmnations-eswc-lowmem-blocked.json`.
