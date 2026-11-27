# Game status — measured only, no silent PASS

This file is the honest ledger for the games named by the user. A row is only marked
verified when there is a rebuilt binary, captured frames from the same input, and a
reported verification percentage. Missing oracle evidence is recorded as BLOCKED.

| Game | Input | macOS build | Linux build | Verification |
| --- | --- | --- | --- | --- |
| Project I.G.I. | `/Users/haseeb-mir/Downloads/IGI/IGI.EXE` (PE32 i386) | Yes — `projects/igi/build/IGI` renders level 5 and level 7 (`window level5`, `window level7`) | No — no Linux sysroot/daemon here | Source Unicorn sweep in `projects/igi/reports/verify.json`; Frida run parity **BLOCKED** (OS denies `task_for_pid`-style attach to any spawned process on this host). Not 100%. |
| TrackMania Forever | `/Users/haseeb-mir/Downloads/TmForeverModLoader/TmForever.exe` (PE32 i386, sha256 `5e287e79a07cccc9f58989120fda2668d5da1086321033f162492af7c2671672`) | Pipeline running in background | Not attempted yet | No percentage recorded yet. |
| AssaultCube | official v1.3.0.2 Windows release, extracted `bin_win32/ac_client.exe` (PE32 i386, sha256 `5b588e6ff7710fa0170553506a50bfa14750ccb438a154e4ea0e0b1ba621cefb`) | Pipeline running in background | Not attempted yet | The pre-existing `cube.exe` was only PE32+ x86-64 and was correctly refused (`docs/assaultcube/cli-refusal.txt`); the genuine 32-bit client was downloaded from the official GitHub release and is now in the same pipeline. |
| Other x86 oracle games (Tetris, igi2, solvers) | `/Users/haseeb-mir/Downloads/recomp-apps/recomp-oracle/*.exe` | Not yet processed | Not yet processed | General verifier is engine-agnostic; no per-game claim made. |

## What is still required before any 100% claim

1. Same level / same scripted inputs into the original Windows executable and the
   rebuilt binary, with both frame sets captured and compared.
2. Frida run-parity events from both sides, or an equivalent named runtime oracle
   that does not require a privileged attach on this host.
3. A Linux build of the same generated C (needs a Linux SDL2/x86 sysroot or a working
   container daemon), plus Linux frames.

Until those exist, the GUI percentages stay at their measured values and the verdict
stays partial. No screenshot or percentage is fabricated.
