# GR3 provenance — user32 / GDI / registry audit

Worktree: `feature/grprod-3-user32-gdi`, based on
`cb4dc0441199af0fc15d0100a565bd8e444a228e` (GameReComp `develop` line).

## Scope actually owned

The task asked for an audit of `user32.c`, `gdidc.c` and `advapi32.c`, with
exclusive ownership of exactly one of them. **Owned: `runtime/win32hle/advapi32.c`**
(registry persistence). `user32.c` and `gdidc.c` were audited for gaps but were
**not modified**; no `host.c`, header, or other subsystem source was touched.

## Audit result

All three files are broad shim tables with real behaviour for their common
paths; none is a stub-only module. The registry store in `advapi32.c` is an
in-memory tree of keys/values with a line-based persistence file
(`hle_reg_load`/`reg_save`), but it exposed only the `*Ex` ANSI entry points for
writes and no legacy default-value API at all. Concretely, before this change:

| Registered | Missing (game-relevant) |
|---|---|
| `RegOpenKeyExA/W`, `RegCreateKeyExA`, `RegQueryValueExA/W`, `RegSetValueExA`, `RegDeleteValueA/W`, `RegDeleteTreeA/W`, `RegEnumKeyExA/W`, `RegEnumValueA/W`, `RegQueryInfoKeyA/W` | `RegSetValueExW`, `RegCreateKeyExW`, `RegDeleteKeyW`, `RegQueryValueA/W`, `RegSetValueA/W`, `RegSaveKeyA/W` |

A Unicode-aware installer or a game that persisted settings through the legacy
unnamed-value API could not do so through the HLE layer.

## Implemented (5 APIs)

In `runtime/win32hle/advapi32.c` and registered in `win32hle_advapi32[]`:

1. **`RegSetValueExW`** — converts a UTF-16 value name via `hle_wide_to_ansi`;
   converts `REG_SZ`/`REG_EXPAND_SZ` UTF-16 data to the store's CP1252 bytes,
   copies `REG_DWORD`/`REG_BINARY` raw; rejects malformed data and persists.
2. **`RegCreateKeyExW`** — wide subkey path, honours the create/opened
   disposition out-parameter.
3. **`RegQueryValueA`** — legacy default (unnamed) value read; includes the
   Windows edge case that a value name matching a subkey redirects to that
   subkey's default value; required `lpcbData`, byte counts, `ERROR_MORE_DATA`.
4. **`RegQueryValueW`** — same semantics with UTF-16 output and byte-counted
   capacity; widens CP1252 strings back through `hle_ansi_to_wide`.
5. **`RegSetValueA`** — legacy default-value writer (`REG_SZ`,
   `cbData == 0xFFFFFFFF` means `strlen + 1`), with a bound so a guest buffer
   is never over-read; persists through the existing line-based file.

`RegDeleteKeyW`, `RegSetValueW` and `RegSaveKey*` remain unimplemented and are
listed as limitations below.

## Provenance of the behaviour

- **No upstream code was copied.** The implementations were written directly
  against the existing local helpers in `advapi32.c` (`subkey`, `find_value`,
  `set_value`, `reg_save`, `root_of`) and the shared codepage helpers
  `hle_wide_to_ansi` / `hle_ansi_to_wide` in `kernel32_crt.c`.
- **Semantic references only** (documentation/behaviour, not source code):
  Win32 SDK contracts for `RegSetValueEx`, `RegCreateKeyEx`, `RegQueryValue`
  and `RegSetValue`, and the publicly documented Wine/ReactOS behaviour for
  the legacy subkey-redirect and `ERROR_MORE_DATA` size-writeback cases. These
  are LGPL/API references; no code from them was pasted.
- Repository-local style references: `runtime/win32hle/advapi32.c` registry
  section (existing `RegQueryValueExW`/`RegEnumKeyExW` conversion and error
  patterns), `registry_apis_selftest.c` and `registry_more_selftest.c` (guest
  ABI selftest conventions).
- The new selftest's own file is `runtime/win32hle/registry_persistence_selftest.c`
  (new file; no existing selftest was modified).
- No game binaries, proprietary assets, or external files were added.

## Verification performed

- Full-core build: all non-selftest `runtime/win32hle/*.c` compiled together
  with the new selftest (`cc -O1 -Wall ... $(pkg-config sdl2 SDL2_ttf)`),
  Apple clang 16, SDL2 2.32.74 / SDL2_ttf 2.20.2 — build clean (one
  pre-existing unused-variable warning in `r_RegEnumValueW`, untouched).
- `registry_persistence_selftest` run with dummy SDL video/audio: **all checks
  passed** (42 assertions: 37 explicit checks plus 5 registration checks), covering five new shims, cross ANSI/UTF-16 store
  access, disposition values, buffer/error edge cases, missing values, invalid
  handles, and a private-file persistence round-trip.
- Full `build_selftests.sh` regression run: 56 `ok`, 1 `skip`
  (`d3d7_extra_selftest.c`, no OpenGL context), 2 transient `BUILD FAIL`
  (`critsec_ex_selftest.c`, `crt_more_selftest.c`) caused by
  `No space left on device` in the host temp volume, not by source errors.
  Both were rebuilt and re-run individually with the same full core: **both
  passed**. Net: 58 selftests pass, 1 environment skip, 0 real failures.
- Targeted pytest: `tests/test_api_coverage.py`,
  `tests/test_runtime_registration.py`, `tests/test_acceptance_docs.py` —
  **48 passed**.

## Limitations / honesty

- Registration proves only that a name is present; the behaviour evidence is
  the new selftest, not the coverage report.
- No live-game or Wine run was performed, so no gameplay/acceptance claim is
  made.
- `RegSetValueExW` string conversion covers CP1252-representable code units
  only (the store is CP1252 bytes); unrepresentable input returns
  `ERROR_INVALID_PARAMETER`, matching the layer's existing codepage policy.
- `RegSetValueW`, `RegDeleteKeyW` and `RegSaveKeyA/W` were deliberately left
  out of this 5-API change and remain as follow-ups.
- `hle_reg_load` parses the line format into the same store; the new persistence
  round-trip test verifies that `reg_save()` writes back the expected lines,
  not a Windows hive/binary format.
