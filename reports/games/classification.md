# Classification of the games that bind every import

Eleven project rows bind every import they ask for (`0 unresolved`). Binding is
necessary and nowhere near sufficient, so each row was **measured** twice — once
headless, once with a real video driver — and classified into one of four
outcomes. The raw counts are in `classification.json`; this file is the reading
of them.

Measured 2026-10-30 on the runtime at commit `9720e3a` (all eleven projects
re-synced from `runtime/win32hle` and rebuilt before measuring).

## Method

```
<projects/PROJECT>/build/<BINARY> <original .exe>      cwd = the game's own directory
  env: RECOMP_TRACE=1 SDL_AUDIODRIVER=dummy
  headless run adds  SDL_VIDEODRIVER=dummy
  display  run leaves SDL_VIDEODRIVER unset (a real window)
  deadline 15 s, killed and reported as "alive at deadline" if still running
```

`RECOMP_TRACE=1` writes one line per shim call, so a traced run is slower than
an untraced one: the counts below are comparable **between rows measured this
way**, and are not frame rates. The exit code, the alive/deadline answer and the
first failure are unaffected by the tracing.

`IDirectDrawSurface::Blt` and `IDirectInputDevice::GetDeviceState` are counted
under `Blt` and `GetDeviceState`: this layer serves those COM methods, so the
trace names them `Class::Method`.

## The four outcomes

- **RUNS** — reached a frame loop and kept rendering and pumping until the deadline.
- **EXITS** — returned on its own, with the code, and what it did before returning.
- **NEEDS-DISPLAY** — binds everything, then dies at an honest NULL because it
  needs a GL context or a real window. *No row is in this category; see the note
  on the GL games below, which is the interesting result of this sweep.*
- **CRASHES** — died for a reason that is neither of those, with the fault located.

## The rows

| game | outcome | headless | display | bound | what happened |
|---|---|---|---|---|---|
| Doukutsu | **RUNS** | alive at 15 s | alive at 15 s | 143 | frame loop: 196,018 `Blt` + 10,886 `PeekMessageA` + 10,885 `GetDeviceState`; the tail repeats `GetTickCount`/`Sleep`/`PeekMessageA`/`Poll`/`GetDeviceState` |
| cavestory-doukutsu | **RUNS** | alive at 15 s | alive at 15 s | 143 | same `pe.path` as Doukutsu (196,060 `Blt`) |
| DoConfig | **EXITS 1** | 0.4 s | 0.2 s | 90 | opens `Config.dat` (ok), builds its dialog (406 Button + 139 Static + 23 ComboBox control calls, 11 `CheckRadioButton`), then `ExitProcess(1)` itself |
| cavestory-doconfig | **EXITS 1** | 0.3 s | 0.2 s | 90 | same `pe.path` as DoConfig |
| OrgView | **EXITS 0** | 0.1 s | 0.0 s | 122 | DirectSound init (`[dsound] dummy, 44100 Hz`), two `BitBlt`, then `ExitProcess(0)` itself |
| cavestory-orgview | **EXITS 0** | 0.0 s | 0.0 s | 122 | same `pe.path` as OrgView |
| ikachan | **EXITS 1** | 0.0 s | 0.0 s | 97 | opens `Option.bin` (ok), builds its dialog UI (36 Static + 12 Button + 11 ComboBox), then `ExitProcess(1)` itself |
| warning-forever | **EXITS 0** | 0.0 s | 0.0 s | 147 | puts up **its own** message box and exits 0. The box's text as this layer logs it is `[MessageBox] ERROR: DirectDraw7` followed by Shift-JIS bytes (not valid UTF-8, so they read as replacement characters in a UTF-8 capture); the game is telling the user that DirectDraw7 is unavailable, then leaving cleanly |
| wf | **EXITS 0** | 0.0 s | 0.0 s | 147 | measured through `projects/warning-forever`; `projects/wf` has no `src/recomp/gen`, so it has no current binary |
| gr | **CRASHES** | SIGBUS (-10) | SIGBUS (-10) | 136 | after **27 shim calls**, during CRT startup, before any SDL call — see below |
| gunroar | **CRASHES** | SIGBUS (-10) | SIGBUS (-10) | 136 | same `pe.path` as gr |

Headless and display runs are **identical** for every row: same exit code, same
alive/deadline, same first failure. No row's classification depends on the
driver.

## The GL games crash before they reach SDL — this is not NEEDS-DISPLAY

The expectation was that the four SDL/OpenGL games die at
`SDL_SetVideoMode(SDL_OPENGL)` returning NULL under a headless driver. **They do
not, and gunroar never reaches that call.** Its trace stops after 27 shim calls,
all of them CRT startup:

```
VirtualAlloc x2, GetVersion x2, GetCommandLineA, GetACP, GetCPInfo,
GetModuleFileNameA, GetCurrentThreadId, GetEnvironmentStrings,
SetUnhandledExceptionFilter, GetStdHandle x3, InitializeCriticalSection x3,
SetHandleCount, EnterCriticalSection/LeaveCriticalSection x4,
GetTickCount, GetModuleHandleA
```

then SIGBUS. Located with lldb on the rebuilt binary:

```
EXC_BAD_ACCESS (code=2, address=0x300000028)     <- guest VA 0x28
gr`sub_004281FC + 744
```

and in the lifted C (`projects/gr/src/recomp/gen/recomp_0002.c`):

```c
eax = edi;                  /* 0x0042824D: mov eax, edi; edi = [[ebp-0x1C]+0xC] */
RECOMP_CALL(sub_00428714);  /* 0x0042824F */
ebx = eax;                  /* 0x00428254 */
... MEM32(eax + 0x28) ...   /* 0x00428256: cmp dword ptr [eax+0x28], 0  <- fault */
```

`sub_00428714` walks a structure with bounds at `+0x38`/`+0x3C`, a kind at
`+0x40` and a pointer at `+0x44`, and returned 0 here; the caller dereferences
it at `+0x28` without a NULL check, exactly as the original does. So this is a
**guest NULL dereference inside lifted CRT code**, not a missing import and not
a missing GL context: something in the startup path returns a pointer the
original Windows runtime would have provided. It is the same class as the
tester's independent finding on tumiki-fighters (a guest store to an address
above 4 GiB in `sub_00418ECC`).

Because the crash happens before any SDL call, running these games with a real
display changes nothing — confirmed: identical exit code and profile in both
modes. **NEEDS-DISPLAY is empty for this corpus**, and any earlier claim that
gunroar SIGBUSes at the GL NULL was wrong; the SDL path is never reached.

## Two runtime gaps the sweep turned up on the way

Both are visible in the non-trace output of several rows and are **not** why any
row failed, but they are real and cheap to close:

- `GetProcAddress(kernel32.dll, ...)` is not answered for
  `InitializeCriticalSectionAndSpinCount`, `FlsAlloc`, `FlsGetValue`,
  `FlsSetValue`, `FlsFree` — the MSVC CRT probes for all five at startup
  (`[module] GetProcAddress(...): not answered` in DoConfig, OrgView,
  warning-forever and others). The CRT falls back, which is why nothing failed,
  but a game that needs fibre-local storage would get nothing.
- The dialog-driven rows (DoConfig, ikachan) reach their dialogs and exit
  without any user interaction being possible: this layer models dialogs, it
  does not present them as interactive windows. That is why their exit codes are
  a "cancelled" path rather than a failure.

## What this says about the table

`0 unresolved` splits into: one game genuinely running a frame loop, four
returning on their own (three of them after doing real work, one after
correctly reporting that the runtime lacks DirectDraw7), and one crashing in
lifted CRT startup. The next investigation is that crash, not more shims: it is
one guest pointer that the startup path expects and does not get, and it stands
between two more games and their first frame.
