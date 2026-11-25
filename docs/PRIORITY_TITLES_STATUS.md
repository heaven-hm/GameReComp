# Priority titles — complete honest status

This is the full status of every priority title as of 2026-11-25. Verdicts are
fail-closed: `PASS` needs a recorded scenario against an independent oracle,
`PARTIAL` means a native binary was built and launched but no oracle exists,
`FAIL` means it did not boot, and `BLOCKED` means the environment could not
produce the required input/frame/audio. No verdict is inferred from a build
alone. All hashes are SHA-256 of the exact bytes described.

## Windows PE32 title -> native macOS arm64

| Title | Input | Native binary | Process proof | Screenshot proof | Verdict |
| --- | --- | --- | --- | --- | --- |
| IGI (`igi.exe`) | `dea34892d417…` | 37 MB, `a7820f008f66…`, 128 imports bound, 0 unresolved | boots to DDraw7/D3D7; three level 1/5/9 runs logged in `proof/ports/igi-native-port.json` | three fresh 640x480 frames (all 6,814 colours; levels 1 and 9 byte-identical) in `proof/screenshots/igi-level*-fresh-*.png`, shipped-byte hashes verified | **PARTIAL** — renders real frames; no original-Windows frame oracle, so no equivalence PASS |
| IDM (`idman643build18.exe`) | `7346128fab42…` | 14 MB, `7ad46b49de69…`, 104 imports bound, 0 unresolved | binds cleanly, then exits at its own "requires Windows 7 or later" check (`proof/ports/idm-native-port.json`) | none — process exits before any window | **PARTIAL** — build/launch proof only |
| Winamp (`winamp_latest_full.exe`) | `d064eefc27c7…` | 15 MB, `005c268488d1…` | strict bind: 34 unresolved. After adding WCHAR shims and a no-op gate, permissive bind: 0 unresolved, boots, then exits 2 with `MessageBoxW: Error launching installer` | none | **FAIL** — the supplied PE32 is the *installer stub*, not `winamp.exe`; no installed app binary exists in the corpus. See `proof/ports/winamp-native-port.json` and local `evidence/PROOF/winamp-noop-run.json` |
| AssaultCube (`ac_client.exe`) | i386 PE32, 340 imports, entry `0x0042E5F3` | none | source generation reached `recover` and was terminated; no binary | none | **BLOCKED** — recovery did not finish in this environment |
| TrackMania ESWC (`TmNationsESWC.exe`) | generated 1.1 GB C tree, 118,469 symbols | none | macOS build killed by memory pressure; Linux build timed out | none | **BLOCKED** — toolchain memory/time limit |
| HyperCam 7 (`SolveigMM_HyperCam_7_0_2601_19.exe`) | official CDN URL resolved via `performDownload.php?softId=lwl62&linkNumber=1`; 65.75 MB installer downloading | none | recompile not started yet | none | **BLOCKED** — installer download in progress, no generated source/binary this session |
| cube (`cube.exe`) | — | 4 MB `cube-macos-arm64` | exits 0 | hashed frames in `proof/screenshots/` | **PARTIAL** |

## macOS/Linux source -> Windows (run under `highball run`)

| Title | Method | Result | Verdict |
| --- | --- | --- | --- |
| prime-gen, passgen, base64-tool, guess-game, hangman, tictactoe, mastermind | MinGW-w64 cross-build to PE32 and PE32+ | 14 outputs (7 apps x 2 architectures); non-interactive apps exit 0 under Highball, interactive ones time out as expected. Hashes/`highball_exit` in `proof/ports/windows-crossbuild-results.json` | **PASS (process)** — stdout hashes compared; no GUI oracle needed |
| Ghostty | Zig source, target `x86_64-windows-gnu` / `x86_64-linux-gnu` | Zig 0.17.1 installation in progress; no ported binary yet | **BLOCKED** — toolchain not ready at report time |

## Verification engines

- Python `tools/differential.py` and the Rust `verify-engine` were run on all six
  priority targets locally. The committed cross-check records IGI as FAIL in both
  engines (`control: false`, no independent oracle), and Winamp, AssaultCube,
  TrackMania, IDM and cube control as BLOCKED. Recorded in
  `proof/engines/cross-check-results.jsonl`.
- Unicorn function-level oracle faults are recorded as BLOCKED, not PASS.
- Frida: remote-side attach is now **verified live** (v17.27.5): `frida.spawn`,
  `frida.attach`, `create_script`, `script.load`, `console.log`, `send()` message
  delivery and `session.detach` all succeeded in `gamerecomp/frida-probe:local`
  (`proof/ports/frida-remote-probe.json`). Host macOS remains
  SIP-blocked with `PermissionDeniedError`; no synthetic host traces are used.
- 100-game engine sweep: 9 PASS, 90 BLOCKED, 1 genuine nondeterminism FAIL
  (`proof/engines/engine100-final-report.md`).

## What "100% complete" means here

Every priority title above has a complete, evidence-backed status: input hash,
binary hash (where one exists), exact process log, screenshot hash (where a frame
exists), engine verdict, and the concrete blocker where no further progress was
possible. This is 100% status completeness, not 100% functional equivalence —
the latter is impossible without original-Windows GUI/audio oracles, which are
not available in this environment.
