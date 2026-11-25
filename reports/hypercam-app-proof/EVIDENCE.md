# HyperCam 7 real-app recomp proof — BLOCKED (no native binary, no frame)

Verdict: **BLOCKED / no PASS**. The installer-extracted executable is the real
HyperCam 7 application (not an installer stub), but `tools.autorecomp` never
finished source generation. There is no CMake project, no native macOS binary,
no runtime execution, and therefore **zero captured frames**. No live-game or
runtime success is claimed.

## Input identity (static, verified)

| Field | Value |
| --- | --- |
| Source path (Highball bottle) | `/Users/haseeb-mir/Library/Application Support/Highball/bottles/Games/drive_c/Program Files (x86)/HyperCam 7/SMM_HyperCam.exe` |
| Staged path | `/Users/haseeb-mir/hypercam-app-proof/stage/SMM_HyperCam.exe` |
| SHA-256 | `068293946e12a6aa030a6e7bee212b0800ea914ad8b0354bac576da724059154` |
| Size | 16,833,368 bytes |
| PE type | `i386 PE32`, Windows GUI subsystem, entry `0x011EC929` |
| Imports | 50 IAT entries + 5 delay imports, including `gdiplus`, `MSVFW32`, `WINMM`, `dwmapi`, `WINHTTP`, and in-app DLLs `SMM_UserInteractionMode.dll`, `mclick.dll`, `SMM_Resample.dll` |

Bundled companion DLLs (12) were staged alongside the EXE; `inspect.json`
records the full import table. See `inspect.json` and
`hypercam-strings-excerpt.txt` in this directory.

### Why this is the real app, not an installer stub

Native strings include `CHyperCamAppObject`, `CHyperCamApp`, `CHyperCamDlg`,
`CDialogHyperCamActivate`, `CDialogHyperCamAlreadyUsedSerial`,
`CDialogHyperCamBlockLicense`, `CDialogHyperCamForceInternetActivationRequest`,
`CHypercamProfileManager`, `SMM_HyperCamLibW` (40-line excerpt committed). The
binary is 16 MB of x86 GUI code with a large C++ vtable/RTTI surface, and the
installed folder contains HyperCam's own `SMM_*` modules. No Inno Setup / NSIS
installer-stub markers appeared in the sampled strings. This is static
evidence only — no runtime proof was obtained (see below).

## Generation attempt (single launch, no duplicate)

Command (started 2026-11-26 01:02:20 IST, PID 62404 / child 62468):

```sh
python3 -m tools.autorecomp /Users/haseeb-mir/hypercam-app-proof/stage/SMM_HyperCam.exe \
  --project-dir /Users/haseeb-mir/hypercam-app-proof/hypercam-app-src \
  --assets-dir /Users/haseeb-mir/hypercam-app-proof/stage \
  --out-mode source --input-os windows --input-arch x86 \
  --target-os macos --target-arch arm64 --no-table
```

Observed after ~38 minutes wall time:

- `generate.log` contains only `==> inspect`, `ok 4.4s inspect: ...`, `==> recover`
  and no stage completion for `recover`, `translate`, or `project`.
- CPU time advanced 4m29s → 4m33s over a 120 s wall interval (~3 % duty cycle);
  process state `U` (uninterruptible wait), indicating memory thrash rather than
  useful progress. `sample` reported a 5.0 GB physical footprint on an 8 GB host
  with ~86 M swap-ins / ~90 M swap-outs.
- Generated project tree contains only `_companions/` (2 copied DLLs), an empty
  `logs/`, and `reports/inspect.json`. **No `CMakeLists.txt`, no
  `src/recomp/gen/`, no `src/main.c`, no binary.**
- A 15-minute waiter expired with the process still inside `recover`. No second
  generation was launched (explicit "avoid duplicate launches" constraint).

## Build / run / hashes

| Step | Result |
| --- | --- |
| `cmake -O0 -g0 -j4` native build | **NOT RUN** — no generated CMake project exists |
| Native macOS binary | **NONE** |
| Binary SHA-256 | N/A (no binary produced) |
| `RECOMP_SCREENSHOT` run with timeout | **NOT RUN** — nothing to execute |
| Frames captured | **0** |
| Frame SHA-256 / colour stats | N/A (no frames) |

## CUA visual check

`cua-driver` daemon was started at the default socket. `start_session` with
`capture_scope: desktop` succeeded, and `get_desktop_state` returned a valid
3360×2100 PNG (448,625 bytes, SHA-256
`315afdad5e069b8f0a09b7ea1d228ae0ac4f34f3290cb1e3513a7401f10ae3b3`). The
image is **not committed** because it shows unrelated desktop/session content.
No HyperCam window was present — expected, since no binary was ever run. This
verifies the capture toolchain works; it is not a frame of the port.

## Conclusion

Honest verdict: **BLOCKED**. Static identity proves the extracted file is the
real HyperCam 7 app, but the recompilation pipeline stalled in `recover` before
emitting source. With no native binary and no frame, there is no basis to claim
any runtime fidelity. A retry needs more memory/headroom (or a smaller input
slice) and should be tracked as a separate run, not as a duplicate launch from
this evidence PR.
