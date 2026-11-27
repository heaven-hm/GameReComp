# Game status — measured only, no silent PASS

This file is the honest ledger for the games named by the user. A row is only marked
verified when there is a rebuilt binary, captured frames from the same input, and a
reported verification percentage. Missing oracle evidence is recorded as BLOCKED.

| Game | Input | macOS build | Linux build | Verification |
| --- | --- | --- | --- | --- |
| Project I.G.I. | `/Users/haseeb-mir/Downloads/IGI/IGI.EXE` (PE32 i386) | Yes — `projects/igi/build/IGI` renders level 5 and level 7 (`window level5`, `window level7`) | Linux ELF build in progress | Unicorn source parity **75.0%** (3 agree, 1 blocked by an original-side fault). Frida run parity on macOS **BLOCKED** (OS denies attach to any spawned process); retrying inside a Linux VM with Wine32. Not 100%. |
| TrackMania Forever | `/Users/haseeb-mir/Downloads/TmForeverModLoader/TmForever.exe` (PE32 i386, sha256 `5e287e79a07cccc9f58989120fda2668d5da1086321033f162492af7c2671672`) | Built: `build/TmForever` (347 MB); boot still fails | Not attempted yet | Recover found 88,039 functions / 9.7M instructions; run stage has 159 unresolved imports. Added named startup shims (`SHGetFolderPathW`, `GlobalMemoryStatusEx`, priority/registry/security probes). Under `RECOMP_PERMISSIVE=1` it now reaches Direct3D and dies on an invalid heap free. **Not verified.** |
| AssaultCube | official v1.3.0.2 Windows release, extracted `bin_win32/ac_client.exe` (PE32 i386, sha256 `5b588e6ff7710fa0170553506a50bfa14750ccb438a154e4ea0e0b1ba621cefb`) | Pipeline running in background | Not attempted yet | The pre-existing `cube.exe` was only PE32+ x86-64 and was correctly refused (`docs/assaultcube/cli-refusal.txt`); the genuine 32-bit client was downloaded from the official GitHub release and is now in the same pipeline. |
| Other x86 oracle games (Tetris, igi2, solvers) | `/Users/haseeb-mir/Downloads/recomp-apps/recomp-oracle/*.exe` | Not yet processed | Not yet processed | General verifier is engine-agnostic; no per-game claim made. |

## Rev.ng source pass

The **Rev.ng Source Pass** path is tested end-to-end on the tested PE32 fixture:
`projects/pe32_min_fixture/reports/readability.json` records
`outcome: revng-candidates-recorded` and `readability/revng/decompiled.c` contains real
PTML from the upstream container. Two blockers were fixed and verified: a stale
`DOCKER_HOST` now falls back to a live Colima socket, and the current image's silent
`-o` option is replaced by capturing stdout. On full IGI (1.4 MB PE) the emulated
Rev.ng analysis is **OOM-killed (rc=137)** on this 8 GiB host; no integration into the
dispatch ABI is claimed.

## What is still required before any 100% claim

1. Same level / same scripted inputs into the original Windows executable and the
   rebuilt binary, with both frame sets captured and compared.
2. Frida run-parity events from both sides, or an equivalent named runtime oracle
   that does not require a privileged attach on this host.
3. A Linux build of the same generated C (needs a Linux SDL2/x86 sysroot or a working
   container daemon), plus Linux frames.

Until those exist, the GUI percentages stay at their measured values and the verdict
stays partial. No screenshot or percentage is fabricated.
