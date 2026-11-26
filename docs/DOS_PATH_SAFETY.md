# Issue #334 — recomp16 DOS path confinement

## Problem

`runtime/recomp16/dos_compat.c::dos_path_to_native()` translated a guest
INT 21h path by converting `\` to `/` and prepending `g_dos->game_dir`:

```c
snprintf(out, out_size, "%s/%s", g_dos->game_dir, dos_path);
```

There was no canonicalization. A guest could pass `..\..\etc\passwd`, a drive
prefix (`C:\...`) or a leading `/` and, through INT 21h `3Ch`/`3Dh`/`41h`,
create, read or delete an arbitrary host file outside the game directory.

## Fix

The translation is now fail-closed and returns an explicit result code:

- Lexical checks run before any filesystem access: empty paths, paths without
  a NUL terminator inside the DOS 260-byte limit, drive-letter prefixes,
  leading `/` (absolute/UNC) and **any** `..` component are rejected.
- The game directory is canonicalized with `realpath()`. The guest path is
  appended and the result is canonicalized again, so symlinks (including ones
  in intermediate directories) are resolved. For a DOS create whose leaf does
  not exist yet, the deepest existing parent is canonicalized and the leaf is
  appended; a symlink leaf is resolved or refused.
- The canonical result is accepted only when it is the game directory itself
  or a child of it (`root/` prefix, not merely a string prefix).
- The INT 21h create/open/delete handlers check the result and return the
  generic DOS error 03h ("path not found") with the carry flag set. They do not
  disclose which rule rejected the path, so a guest cannot probe the host.

Error codes (`enum` in `dos_compat.c`): `DOS_PATH_OK = 0`,
`DOS_PATH_ERR_EMPTY = -1`, `DOS_PATH_ERR_NUL = -2`,
`DOS_PATH_ERR_DRIVE = -3`, `DOS_PATH_ERR_ABSOLUTE = -4`,
`DOS_PATH_ERR_DOTDOT = -5`, `DOS_PATH_ERR_TOOLONG = -6`,
`DOS_PATH_ERR_RESOLVE = -7`, `DOS_PATH_ERR_ESCAPE = -8`.

A NUL byte terminates the DOS ASCIIZ path by definition; bytes following it
are not part of the path and are never followed. A path with no terminator
inside the 260-byte DOS limit is refused rather than silently truncated.

## Verification

`tests/test_dos_path_safety.py` compiles the real `dos_compat.c` with the
unrelated HAL functions stubbed, builds a synthetic game directory (including
symlinks to files outside it), and asserts: normal paths — including a not-yet
created file — resolve under `game_dir`; traversal, drive, absolute,
unterminated and symlink-escape attempts return the exact error code and no
host path; INT 21h create/open/delete turn refusals into DOS error 03h and
leave files outside `game_dir` untouched. All artifacts are created in
pytest's temporary directory; no game assets or live injection are involved.

Run:

```sh
python3 -m pytest tests/test_dos_path_safety.py -q
```

This is a native runtime-layer check only. It does not claim live-game
validation; that remains a separate game/Wine workflow.
