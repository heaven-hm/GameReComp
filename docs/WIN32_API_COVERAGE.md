# Win32 API coverage (auditor output)

Generated from real PE32 import tables by `python3 -m tools.api_coverage`.

- Corpus: `/Users/haseeb-mir/Downloads/recomp-apps/games`
- JSON report: `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/a5-coverage.json`
- Regenerated (UTC): `2026-11-23T08:31:19Z`
- Schema: `1.1.0`

## Headline numbers

| Metric | Value |
|---|---|
| PE32 inputs analysed | 19 |
| Registered shim names (`registered_shim_count`) | 2273 |
| Distinct PE import names covered (`registered_import_name_count`) | 2519 |
| Distinct DLLs referenced | 48 (22 system, 26 game-owned) |
| Missing Win32 **system** APIs (`distinct_missing_api`) | 13 |
| Missing game-owned APIs (`game_owned_missing_api`) | 293 |
| All missing APIs combined (`all_missing_api`) | 306 |
| Ordinal imports (system / game-owned / all) | 0 / 0 / 0 |

**Counting rule:** Implemented means the import name equals a registered shim name, the leaf of a '::' namespaced shim, or a '#N' ordinal alias. Registration is not proof of behaviour. Game-owned DLL imports are reported separately from Win32 system DLL imports.

**Coverage is incomplete.** Only a registered shim *name* counts as implemented, and 13 system plus 293 game-owned import names are still unregistered. This is a name-registration audit; per-API selftests, not this report, are the behaviour evidence.

**What this is not:** proof that a function behaves correctly; registration only proves a name is present.

## Recently registered shims (24, last checkpoint)

APIs first registered in the last integration checkpoint recorded in `docs/ISSUE_EXECUTION_PLAN.md` (the 10-agent coverage-refresh checkpoint). This section is additive: it reports the registration delta from the previous coverage checkpoint (2,249 shims), not extra coverage. **Registration only proves a name is present**; per-API selftests, not this list, are the behaviour evidence.

- A22 - COMCTL32 FlatSB (comctl32.dll): `FlatSB_EnableScrollBar`, `FlatSB_GetScrollInfo`, `FlatSB_GetScrollPos`, `FlatSB_GetScrollProp`, `FlatSB_GetScrollRange`, `FlatSB_ShowScrollBar`, `FlatSB_SetScrollInfo`, `FlatSB_SetScrollPos`, `FlatSB_SetScrollRange`.
- A26 - locale Ex APIs (kernel32.dll): `CompareStringEx`, `GetLocaleInfoEx`, `LCMapStringEx`.
- A27 - SList / Rtl extras (kernel32.dll): `InterlockedFlushSList`, `InterlockedPushEntrySList`, `RtlPcToFileHeader`, `RtlUnwindEx`.
- A28 - wide-string CRT exports (msvcrt.dll): `_wcsdup`, `wcscat`, `wcscmp`, `wcsncat`.
- A29 - SDL 1.2 clip/cursor/lower-blit (sdl.dll / sdl2.dll): `SDL_FreeCursor`, `SDL_GetClipRect`, `SDL_LowerBlit`, `SDL_SetClipRect`.

## Missing Win32 system APIs (13)

| DLL | API | Files |
|---|---|---|
| `msvcrt.dll` | `__mb_cur_max` | 2 |
| `msvcrt.dll` | `_ctype` | 2 |
| `msvcrt.dll` | `_fmode` | 2 |
| `msvcrt.dll` | `_iob` | 2 |
| `msvcrt.dll` | `_pctype` | 2 |
| `comctl32.dll` | `ImageList_Replace` | 1 |
| `ole32.dll` | `CreateBindCtx` | 1 |
| `ole32.dll` | `CreateILockBytesOnHGlobal` | 1 |
| `ole32.dll` | `OleGetIconOfClass` | 1 |
| `ole32.dll` | `StgCreateDocfileOnILockBytes` | 1 |
| `opengl32.dll` | `glTexCoord2i` | 1 |
| `opengl32.dll` | `glTexSubImage2D` | 1 |
| `opengl32.dll` | `glVertex2i` | 1 |

## Game-owned DLL imports (top 30 of 293)

These are a game's own DLLs or bundled redistributables, not Win32 system APIs. They are listed separately and never counted in the headline system number.

| DLL | API | Files |
|---|---|---|
| `bulletml.dll` | `_BulletMLParserTinyXML_delete` | 3 |
| `bulletml.dll` | `_BulletMLParserTinyXML_new` | 3 |
| `bulletml.dll` | `_BulletMLParserTinyXML_parse` | 3 |
| `bulletml.dll` | `_BulletMLRunner_delete` | 3 |
| `bulletml.dll` | `_BulletMLRunner_isEnd` | 3 |
| `bulletml.dll` | `_BulletMLRunner_new_parser` | 3 |
| `bulletml.dll` | `_BulletMLRunner_new_state` | 3 |
| `bulletml.dll` | `_BulletMLRunner_run` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_createBullet` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_createSimpleBullet` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_doAccelX` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_doAccelY` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_doChangeDirection` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_doChangeSpeed` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_doVanish` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getAimDirection` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getBulletDirection` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getBulletSpeed` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getBulletSpeedX` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getBulletSpeedY` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getDefaultSpeed` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getRand` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getRank` | 3 |
| `bulletml.dll` | `_BulletMLRunner_set_getTurn` | 3 |
| `msvcp140.dll` | `?_Xbad_alloc@std@@YAXXZ` | 3 |
| `msvcp140.dll` | `?id@?$codecvt@DDU_Mbstatet@@@std@@2V0locale@2@A` | 3 |
| `msvcp140.dll` | `?uncaught_exception@std@@YA_NXZ` | 3 |
| `sdl2.dll` | `SDL_CreateWindow` | 3 |
| `sdl2.dll` | `SDL_DestroyWindow` | 3 |
| `vcruntime140.dll` | `__std_terminate` | 3 |

## DLL classification

System DLLs (22): `advapi32.dll`, `comctl32.dll`, `comdlg32.dll`, `d3d8.dll`, `ddraw.dll`, `dinput.dll`, `dinput8.dll`, `dsound.dll`, `gdi32.dll`, `imm32.dll`, `kernel32.dll`, `msvcrt.dll`, `ole32.dll`, `oleaut32.dll`, `opengl32.dll`, `shell32.dll`, `shlwapi.dll`, `user32.dll`, `version.dll`, `winmm.dll`, `winspool.drv`, `wsock32.dll`.

Game-owned DLLs (26): `api-ms-win-crt-conio-l1-1-0.dll`, `api-ms-win-crt-convert-l1-1-0.dll`, `api-ms-win-crt-environment-l1-1-0.dll`, `api-ms-win-crt-filesystem-l1-1-0.dll`, `api-ms-win-crt-heap-l1-1-0.dll`, `api-ms-win-crt-locale-l1-1-0.dll`, `api-ms-win-crt-math-l1-1-0.dll`, `api-ms-win-crt-private-l1-1-0.dll`, `api-ms-win-crt-runtime-l1-1-0.dll`, `api-ms-win-crt-stdio-l1-1-0.dll`, `api-ms-win-crt-string-l1-1-0.dll`, `api-ms-win-crt-time-l1-1-0.dll`, `api-ms-win-crt-utility-l1-1-0.dll`, `bulletml.dll`, `glu32.dll`, `mpr.dll`, `msvcp140.dll`, `oledlg.dll`, `sdl.dll`, `sdl2.dll`, `sdl2_image.dll`, `sdl2_net.dll`, `sdl2_ttf.dll`, `sdl_mixer.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`.

## How to regenerate

```sh
python3 -m tools.api_coverage \
    --corpus /path/to/games \
    --out reports/api-coverage.json \
    --markdown docs/WIN32_API_COVERAGE.md
```

Registration proves only that a name is present. This report makes no completion, gameplay, rendering, audio or 100%-coverage claim.
