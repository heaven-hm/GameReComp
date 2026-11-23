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
