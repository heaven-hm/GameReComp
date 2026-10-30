# Permissive-boot diagnostic sweep

Generated 2026-10-30T04:40:33Z by `python3 tools/autorecomp/diagnose.py --timeout 15` (timeout 15s per game).

Each rebuilt binary is started with `RECOMP_PERMISSIVE=1`, which binds unresolved imports to the runtime's self-naming stub. The guest runs until it calls an unimplemented import; the runtime then prints and aborts:

```
[win32hle] FATAL: unimplemented import <NAME> (0x<STUB>) called from guest 0x<CALLER>
```

The name in that line is the first import the running program reached - measured from a real launch, not inferred. Strict binding remains the default when the variable is unset.

Measured: 15 projects, 8 reached an unimplemented import. Outcomes: crashed 4, exited-clean 1, exited-error 2, reached-unimplemented-import 8.

## Ranked first-reached imports

| # | import | games blocked | games |
|---|--------|---------------|-------|
| 1 | `LoadLibraryExA` | 2 | herocore-gm5, herocore-gm7 |
| 2 | `_initterm_e` | 2 | generally, generally-trackeditor |
| 3 | `DirectDrawEnumerateExA` | 1 | warning-forever |
| 4 | `EnumFontFamiliesA` | 1 | cavestory-doconfig |
| 5 | `PathRemoveFileSpecA` | 1 | Doukutsu |
| 6 | `__set_app_type` | 1 | rrootage |

## Per-game rows

`strict` is a second launch of the same binary without `RECOMP_PERMISSIVE`; it is the only launch that reports a meaningful unresolved-import count.

| project | outcome | exit | first import | stub VA | caller | strict exit | strict unresolved | pinned runtime |
|---------|---------|------|--------------|---------|--------|-------------|-------------------|----------------|
| Doukutsu | reached-unimplemented-import | -6 | `PathRemoveFileSpecA` | 0x7F003EC0 | 0x00412420 | 2 | 4 | 1c6475f8 |
| cavestory-doconfig | reached-unimplemented-import | -6 | `EnumFontFamiliesA` | 0x7F002BD0 | 0x00402220 | 2 | 2 | 997abf22 |
| cavestory-orgview | exited-clean | 0 | - | - | - | 2 | 2 | 997abf22 |
| generally | reached-unimplemented-import | -6 | `_initterm_e` | 0x7F003430 | 0x00497D76 | 2 | 162 | 6ecb197e |
| generally-trackeditor | reached-unimplemented-import | -6 | `_initterm_e` | 0x7F002E70 | 0x004113CA | 2 | 59 | 997abf22 |
| gunroar | crashed | -10 | - | - | - | 2 | 5 | 1c6475f8 |
| herocore-gm5 | reached-unimplemented-import | -6 | `LoadLibraryExA` | 0x7F002BC0 | 0x004012E8 | 2 | 160 | 997abf22 |
| herocore-gm7 | reached-unimplemented-import | -6 | `LoadLibraryExA` | 0x7F002BC0 | 0x00401324 | 2 | 176 | 997abf22 |
| ikachan | exited-error | 1 | - | - | - | 1 | 0 | 997abf22 |
| noiz2sa | exited-error | 1 | - | - | - | 2 | 8 | 1c6475f8 |
| parsec47 | crashed | -10 | - | - | - | 2 | 90 | 997abf22 |
| rrootage | reached-unimplemented-import | -6 | `__set_app_type` | 0x7F002CC0 | 0x00401000 | 2 | 146 | 997abf22 |
| torus-trooper | crashed | -10 | - | - | - | 2 | 94 | 997abf22 |
| tumiki-fighters | crashed | -10 | - | - | - | 2 | 86 | 997abf22 |
| warning-forever | reached-unimplemented-import | -6 | `DirectDrawEnumerateExA` | 0x7F002BC0 | 0x004219D2 | 2 | 6 | 997abf22 |

## Ran without reaching an unimplemented import

- **cavestory-orgview**: exited-clean
- **gunroar**: crashed - killed by SIGBUS after boot; no unimplemented import reached
- **ikachan**: exited-error - [win32hle] ExitProcess(1)
- **noiz2sa**: exited-error - ICALL: unresolved VA 0x00403B90 from 0x00409660
- **parsec47**: crashed - killed by SIGBUS after boot; no unimplemented import reached
- **torus-trooper**: crashed - killed by SIGBUS after boot; no unimplemented import reached
- **tumiki-fighters**: crashed - killed by SIGBUS after boot; no unimplemented import reached

## Crashed games: last imports the guest reached

A crash in lifted code names no import, so each crashed game was relaunched with `RECOMP_TRACE=1`. The last dispatch before the fault is the prime suspect.

- **gunroar** (SIGBUS, 27 imports dispatched): `GetStdHandle` -> `InitializeCriticalSection` -> `SetHandleCount` -> `InitializeCriticalSection` -> `EnterCriticalSection` -> `InitializeCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection`
- **parsec47** (SIGBUS, 28 imports dispatched): `GetStdHandle` -> `GetStdHandle` -> `InitializeCriticalSection` -> `EnterCriticalSection` -> `InitializeCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `VirtualAlloc` -> `VirtualAlloc` -> `LeaveCriticalSection`
- **torus-trooper** (SIGBUS, 26 imports dispatched): `GetStdHandle` -> `GetStdHandle` -> `InitializeCriticalSection` -> `SetHandleCount` -> `InitializeCriticalSection` -> `EnterCriticalSection` -> `InitializeCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `LeaveCriticalSection`
- **tumiki-fighters** (SIGBUS, 28 imports dispatched): `InitializeCriticalSection` -> `SetHandleCount` -> `InitializeCriticalSection` -> `EnterCriticalSection` -> `InitializeCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `LeaveCriticalSection` -> `EnterCriticalSection` -> `VirtualAlloc` -> `VirtualAlloc` -> `LeaveCriticalSection`

Raw stderr for every row is in `reports/games/diagnostic-sweep.json` (`stderr_tail`).

## Duplicate project directories collapsed

Several project directories exist for one executable (the generator's default names a project after the executable stem). One row per game is reported; which directory was measured, and why, is recorded per game:

- measured `cavestory-doconfig` (corpus-record tree has no binary; used the built sibling); also present: `DoConfig`
- measured `Doukutsu` (corpus-record); also present: `cavestory-doukutsu`
- measured `ikachan` (corpus-record); also present: `dxIka`
- measured `herocore-gm7` (corpus-record); also present: `herocore_gm7`
- measured `parsec47` (corpus-record); also present: `p47`
- measured `rrootage` (corpus-record); also present: `rr`
- measured `tumiki-fighters` (corpus-record); also present: `tf`
- measured `torus-trooper` (corpus-record); also present: `tt`
