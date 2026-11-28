# Issue #28 — measured import-binding and frame proof

This document records the evidence for the 19 named imports of issue #28. It is
a scoped binding-and-rendering result, **not** a Windows-oracle or gameplay
equivalence claim.

## Inputs (exact hashes)

| Binary | SHA-256 |
|---|---|
| `chocolate-doom.exe` | `d1b9dabc567d0a79480a4b319850b69ea76e69a3b4be578586ae045b96af33cc` |
| `tetris-win-32-en.exe` | `024dccd5e227ac4bfcf698e948b1682c4a42ddadde87ec5f18193ee16ca77b8d` |
| `freedoom1.wad` (copied as `doom.wad`) | `7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d` |

Freedoom is a freely redistributable Doom data replacement. No copyrighted
`DOOM.WAD` was used or committed.

## All 19 named APIs

`CreatePatternBrush`, `LoadLibraryW`, `CreateIconIndirect`, `DrawTextW`,
`LoadCursorW`, `MessageBoxW`, `__initenv`, `SDL_CreateCursor`, `SDL_GetCursor`,
`SDL_SetCursor`, `SDL_SetError`, `SDL_VideoDriverName`, `SDL_strlcat`,
`SDL_strlcpy`, `Mix_SetPostMix`, `WSACancelBlockingCall`, `_flsbuf`, `_snprintf`,
`fscanf`, `vprintf`.

`runtime/win32hle/api_gap_selftest.c` exercises the W-message/cursor/icon,
SDL 1.2 cursor/string and CRT stdio families. The selftest suite reported:

```
ok    api_gap_selftest.c
```

The full suite run was cut off by the host command timeout after 26 `ok`
lines; only the `api_gap_selftest.c` line is claimed here.

## Chocolate Doom rebuild and run

```
python3 -m tools.autorecomp chocolate-doom.exe --project-dir <dir> \
  --assets-dir <game folder> --input-os windows --input-arch x86 \
  --target-os host --target-arch host --out-mode full
```

Result: `inspect(ok), recover(ok), translate(ok), project(ok), compile(ok),
run(ok), verify(ok)`; 3,804 functions, 476,069 instructions, 7 source units,
**0 lift errors**, score 93/100 (verification partial: no Windows oracle
supplied). Native binary SHA-256
`c4c000a39369c3964cac0ab11bbba86e8fcd1d2d1980f2aa2f5db14f9640a411`.

```
python3 -m tools.game_runner --game chocolate-doom-freedoom1 \
  --binary <native binary> --folder <game folder> --out <out> --timeout 25
```

Result: **166 EXE imports + 34 companion `SDL_net.dll` imports bound, 0
unresolved**; 3 non-blank frames at 256×200; guest eventually exits 255 with
`R_DrawPlanes: visplane overflow (43)` from its own engine after rendering.
That exit is recorded honestly and is not a binding failure.

Captured frames (`proof/issue-28/frames/`) each have SHA-256
`9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b` and
3,468 distinct RGB colours, so none is a blank buffer. The independent checker
in `tests/test_issue28_proof.py` re-decodes the BMP bytes and recomputes all
digests.

## Tetris

Tetris frame evidence is already committed under `evidence/screenshots/` and
`evidence/p8-new-games/screenshots/`; the earlier import-binding result for the
seven OUCC Tetris imports is recorded in `docs/NEW_GAME_DOWNLOADS.md`.

## Limits

- No original-Windows execution was compared with these native frames.
- A hash proves byte identity, not semantic or gameplay equivalence.
- `Mix_SetPostMix` records the guest callback but never invokes it from the
  host audio thread; `fscanf` fails explicitly with `ENOSYS`. These are
  documented limits of the shims, not fake success.
