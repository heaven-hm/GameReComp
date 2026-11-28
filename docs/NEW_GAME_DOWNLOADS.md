# Newly downloaded PE32 games — reproduction evidence

No game binary or asset is committed. All downloads went to a temporary
directory; the paths below are reproduction evidence only.

## Sources and provenance

| Game | Exact source | Licence | SHA-256 |
|---|---|---|---|
| OUCC Tetris 1.3 (x86, en) | `https://github.com/OUCC/tetris/releases/download/v1.3/tetris-win-32-en.exe` | repository source is public; binary from the project's own release | `024dccd5e227ac4bfcf698e948b1682c4a42ddadde87ec5f18193ee16ca77b8d` |
| Chocolate Doom 1.3.0 (win32) | `https://github.com/chocolate-doom/releases/raw/master/1.3.0/chocolate-doom-1.3.0-win32.zip` | GPL (COPYING.txt in the archive) | exe `d1b9dabc567d0a79480a4b319850b69ea76e69a3b4be578586ae045b96af33cc` |

Both were verified as `PE32 executable (GUI) Intel 80386` before use. No
commercial game was downloaded.

## Pipeline results

Both rebuilt successfully with `--out-mode binary-only --no-run --jobs 1`:

- Tetris: 90 imports bound, **0 lift errors**, score 70/100, native
  `tetris_win_32_en` produced.
- Chocolate Doom: 32 imports bound in the EXE plus a companion `SDL_net.dll`
  (34 exports), **0 lift errors**, native `chocolate_doom` produced.

## Execution results (`tools.game_runner`, Cocoa video, 12 s)

### Tetris — exit 2, 0 frames, 7 unresolved imports
```
GDI32.dll!CreatePatternBrush
KERNEL32.dll!LoadLibraryW
USER32.dll!CreateIconIndirect
USER32.dll!DrawTextW
USER32.dll!LoadCursorW
USER32.dll!MessageBoxW
msvcrt.dll!__initenv
```
This is a concrete coverage gap, not a "game works" result. The rebuilt binary
boots but cannot call those seven APIs, so it exits before drawing.

### Chocolate Doom — exit 2, 0 frames, 12 unresolved imports
```
SDL.dll!SDL_CreateCursor, SDL_GetCursor, SDL_SetCursor, SDL_SetError,
       SDL_VideoDriverName, SDL_strlcat, SDL_strlcpy
SDL_mixer.dll!Mix_SetPostMix
USER32.dll!MessageBoxW
WSOCK32.DLL!WSACancelBlockingCall
msvcrt.dll!_flsbuf, _snprintf, fscanf, vprintf
```
Additionally, a DOOM WAD is not included (copyrighted), so even with these
imports bound, gameplay could not be verified. This run proves boot/import
analysis only.

## Why this matters

These two freely available games expose **19 concrete missing imports** that
the earlier corpus did not surface (notably the W variants of common USER32/GDI32
calls, several SDL 1.2 helpers and four msvcrt symbols). They are now measured
blocker entries for issue #14, and each one is a small, testable implementation
task. No PASS claim is made for either game.

Raw logs and any frames are in `test-results/game-matrix/execution/<game>/`;
`summary.json` lists the exact command, environment, exit code and frame list.

## Rebuild progress (issue #28)

After implementing `SDL_strlcpy`, `SDL_strlcat`, `SDL_SetError`,
`SDL_VideoDriverName`, `MessageBoxW`, `WSACancelBlockingCall`, `LoadCursorW`,
`DrawTextW` and `LoadLibraryW`, both games were rebuilt with the updated
runtime and re-executed with the same runner/timeout:

- Tetris: **7 → 3** unresolved imports. Remaining: `CreatePatternBrush`,
  `CreateIconIndirect`, `__initenv`.
- Chocolate Doom: **12 → 8** unresolved imports. Remaining:
  `SDL_CreateCursor`, `SDL_GetCursor`, `SDL_SetCursor`, `Mix_SetPostMix`,
  `_flsbuf`, `_snprintf`, `fscanf`, `vprintf`.

Neither game reached a frame yet; this is an import-binding improvement, not a
gameplay claim.

## Final import-binding result (issue #28)

After the remaining implementations (`CreatePatternBrush`, `CreateIconIndirect`,
`__initenv`, SDL cursor trio, four MSVCRT symbols and `Mix_SetPostMix`), both
downloaded games rebuild and boot with **0 unresolved imports**:

- **OUCC Tetris**: 97 imports bound, 0 unresolved, stayed alive for 10 s and
  produced **3 frames (310×418)**. This is the first newly downloaded game to
  reach real rendering through GameReComp. Logs/frames under
  `test-results/game-matrix/execution/tetris-oucc/`.
- **Chocolate Doom**: 166 imports in the EXE + 34 in `SDL_net.dll`, all bound,
  0 unresolved. It exits 255 with no frames because no DOOM WAD is supplied
  (copyrighted asset, not included in the GPL binary archive). Import binding
  is proven; gameplay requires a legally supplied WAD fixture.

`Mix_SetPostMix` records the guest callback but deliberately never invokes it
from the host audio thread; `fscanf` fails explicitly with ENOSYS. These are
documented limits, not fake success.

## P8 batch — Tetris JP/CH + Chocolate Heretic/Hexen (2026-11-24)

Four more official, freely distributed PE32/i386 binaries were downloaded to
`~/Downloads/recomp-new-tests/` (gitignored; **no binary or archive is
committed**). Each local file was re-downloaded independently from the exact
URL below and the two SHA-256 digests and byte sizes matched exactly. The two
ZIPs were then extracted to a temporary directory; the main EXE inside each is
also PE32 Intel 80386.

| File | Exact source URL | Size (bytes) | SHA-256 |
|---|---|---:|---|
| `tetris-win-32-jp.exe` | `https://github.com/OUCC/tetris/releases/download/v1.3/tetris-win-32-jp.exe` | 43,520 | `f732c43fb147737a436d382a4e735519dbc13e7811ba304c3f6d500b559de249` |
| `tetris-win-32-ch.exe` | `https://github.com/OUCC/tetris/releases/download/v1.3/tetris-win-32-ch.exe` | 43,520 | `27a64e559d735e383de67fda5658f72a212e92cf2090256f7a9157b22d61b8c4` |
| `chocolate-heretic-3.1.0-win32.zip` | `https://github.com/chocolate-doom/chocolate-doom/releases/download/chocolate-doom-3.1.0/chocolate-heretic-3.1.0-win32.zip` | 8,077,273 | `c0e24bec9af1d642cff66dbe6705113f36848a0f945801002ab78b1b0f62af49` |
| `chocolate-hexen-3.1.0-win32.zip` | `https://github.com/chocolate-doom/chocolate-doom/releases/download/chocolate-doom-3.1.0/chocolate-hexen-3.1.0-win32.zip` | 8,133,899 | `5570a00f9259bf3d88176ee6c0d8182f8e396312d18398457783fbb599ef0c5a` |

Notes:

- The Heretic archive was already present locally under the shortened name
  `choc-heretic-3.1.0-win32.zip`; its hash matches a fresh download of the
  canonical `chocolate-heretic-3.1.0-win32.zip` URL byte for byte.
  The Hexen archive was missing from the killed download and was fetched again.
- `file(1)` on both Tetris EXEs and on the extracted `chocolate-heretic.exe`
  (960,014 bytes, SHA-256 `18620d715789844d021337783b6de2e086b6243a0faca53fd922c39caeabe639`)
  and `chocolate-hexen.exe` (1,138,190 bytes, SHA-256
  `d397847d9da20e4a7e49479cde30a9342b1a23d881370446a23efa147fcc7bb4`)
  reports `PE32 executable (GUI) Intel 80386 (stripped to external PDB), for MS Windows`.
- The Chocolate 3.1.0 archives contain GPL source/licence documentation
  (`COPYING.html`) and SDL2-era DLLs; they ship no Heretic/Hexen IWAD game
  data, so rendering cannot be claimed without a legally supplied IWAD.

### P8 batch results (real runs, no mocked output)

Both Tetris variants were rebuilt with the current pipeline and the exact
command recorded in `evidence/p8-new-games/evidence.json`
(`--out-mode binary-only --no-run --jobs 1`): exit 0, pipeline score 70,
97 imports, 0 lift errors, native binaries `tetris_win_32_jp`
(SHA-256 `3ff6ddaba46276f1dc267c00ee4aed010a9eac1fa4a4d8e58f0b318b0ca10591`)
and `tetris_win_32_ch`
(SHA-256 `c3312995972a762001af335789eb494e3ac29ba67a0c791c18f381da8ee60610`).

`python3 -m tools.game_runner` + `python3 -m tools.frame_proof` were then run
on the real native binaries with a 15 s timeout. **Both produced 3 non-blank
310×418 frames each** (4, 5 and 5 distinct colours per game). Both native runs
ended in `TIMEOUT` because the Tetris GUI stays open waiting for input — that
is the real exit status, not a success. Original PE32 runs under the real
Highball Wine engine also reached the 12 s timeout with 0 stdout bytes (GUI
idle), so no process-level oracle exists for these games.

Evidence: `evidence/p8-new-games/` — `runner-summary.json`, `frame-proof.json`,
`wine-originals.json`, `evidence.json`, real `logs/` (rebuild, runner, frame
proof, Wine stdout/stderr) and four small PNGs converted losslessly from the
captured BMP frames in `screenshots/`. No EXE, ZIP or raw BMP is committed.

**Classification: PARTIAL for both Tetris variants** — render evidence is
real, but no Windows-original frame, input, audio or gameplay comparison was
performed; the Japanese/Chinese menu glyphs also render as mojibake/tofu, so
locale fidelity is not proven. No PASS is claimed.

The Heretic rebuild was launched from the real extracted `chocolate-heretic.exe`
but had not produced a pipeline report when this evidence was written; it is
recorded as **BLOCKED (rebuild did not finish in session window)** with the
exact command and live log under `evidence/p8-new-games/logs/heretic-rebuild.log`.
Hexen is **BLOCKED (no IWAD; not attempted)**. Neither is claimed as built or
running.

## Chocolate Doom frame evidence with a legal WAD (issue #28)

On 2026-11-25 the exact Chocolate Doom 1.3.0 win32 binary
(`d1b9dabc567d0a79480a4b319850b69ea76e69a3b4be578586ae045b96af33cc`) was
rebuilt with the current runtime and run through `tools.game_runner` with a
freely redistributable Freedoom 1 WAD (`7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d`):
166 EXE imports plus 34 companion `SDL_net.dll` imports bound, **0 unresolved**,
and 3 non-blank 256x200 frames (3,468 distinct colours). The guest exits 255
after rendering from its own visplane-overflow error; no Windows oracle was
used and no gameplay equivalence is claimed. Machine-readable evidence,
recomputed frame hashes and the independent guard test are in
`docs/ISSUE_28_PROOF.md` and `proof/issue-28/`.
