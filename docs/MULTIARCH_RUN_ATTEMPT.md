# Attempted run of all x86 and x64 games to macOS and Linux

Date: 2026-11-23. All commands were executed; results below are verbatim.

## 1. x64 games: refused by the tool, not "failed to test"

```
$ python3 -m tools.autorecomp .../opentyrian/opentyrian.exe --input-os windows \
    --input-arch auto --target-os host --target-arch host --out-mode source-only
error: unsupported input architecture: x86_64 (this pipeline reconstructs PE32
x86 guests; a 64-bit build of the same game, if one exists, would be a different
binary to reconstruct -- 64-bit guest support is planned, not implemented)
```

Forcing the architecture is also refused:

```
error: --input-arch x86 but the binary's header says x86_64; refusing to
reconstruct on a guess
```

Local PE32+ x64 game binaries found: `opentyrian.exe` (930,434 B),
`cannonball.exe` (534,528 B), `pixeltetris/Pixeltetris.exe` (1,118,208 B),
`snake/snake-windows-x86_64.exe`. These are **NOT RUN** and **NOT TESTED**, not
PASS and not FAIL. Issue #13 remains DEFERRED under the roadmap boundary; no
x64 runtime, loader or host ABI was added by this work.

## 2. x86 games: macOS attempt

One small game completed earlier with full measurements (ikachan `dxIka.exe`,
SHA-256 `22923218ba97271a5acc5753b17fcb67d5dd98c00369a317abda44204a10df80`):
inspect/recover/translate/project/compile/run/verify/report all ran; score
93/100; process launched and exited 1 with 0 unresolved imports; verification
`partial` (no oracle). With SDL's dummy driver the exit is caused by no
OpenGL; with Cocoa the game gets further.

Subsequent attempts to run the rest of the corpus were killed by the OS:

```
python3 -m tools.game_matrix --exe tf.exe --exe dxIka.exe --exe wf.exe \
  --exe p47.exe --out ... --parallel 1 --jobs 1 --timeout 240
Exit code: 137 (SIGKILL)
```

The same happened with Doukutsu and with a four-way parallel run. Cause: host
memory/disk exhaustion while compiling multi-hundred-MB generated C trees —
measured for ikachan as 10.28 MB C, 3.03 MB binary, 20.97 MB project on disk,
144 MB peak RSS. At attempt time the host had 81 git worktrees from 20 parallel
agents and ~80 MB free memory. This is a **harness resource blocker**, recorded
in issue #26, not a game verdict and not a fake PASS.

## 3. Linux: no runner on this host

- `docker info`: daemon unavailable (`/Users/haseeb-mir/.docker/run/docker.sock` missing).
- `colima start --cpu 4 --memory 8 --disk 60`: stalled at "downloading disk image"
  and was stopped under memory pressure.
- No `qemu-x86_64` user-mode emulator, Linux kernel or ISO is present.
- `python3 -m tools.autorecomp --target-os linux` is refused: "Foreign OSes are refused."

Therefore **all Linux results are NOT TESTED**. `tools/linux_matrix.py` exists
and will build/run generated projects in a Linux container when a runner is
available, but no Linux PASS row is emitted without one.

## Conclusion

"All x86 and x64 games to macOS and Linux, 100%" cannot be honestly reported
from this environment. Verified facts: x64 is explicitly unsupported; x86 runs
are possible for small games but the full corpus is blocked by host resources;
Linux has no execution runner. No game was counted as passing without a real
report.json, and no issue was closed.
