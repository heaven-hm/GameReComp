# Classification of the games that bind every import

Eleven project rows bind every import they ask for (`0 unresolved`), but they
are only **six distinct games**: `DoConfig`/`cavestory-doconfig`,
`Doukutsu`/`cavestory-doukutsu`, `OrgView`/`cavestory-orgview`, `gr`/`gunroar`
and `wf`/`warning-forever` are the same `pe.path` generated twice. Binding is
necessary and nowhere near sufficient, so each row was **measured** twice — once
headless, once with a real video driver — and classified into one of four
outcomes. The raw counts are in `classification.json`; this file is the reading
of them.

Counted by game rather than by row: **1 RUNS** (Doukutsu), **4 EXITS** (DoConfig,
OrgView, ikachan, warning-forever), **0 NEEDS-DISPLAY**, **1 CRASHES** (gunroar).

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

### The crash, narrowed

Follow-up measurements (lldb on the rebuilt binary, and the lifted C beside it):

1. The call is a **virtual call**, not the `.CRT$XIA` initializer path: lifted
   `sub_0041D894` at original `0x0041D8D9` does
   `eax = [0x4BB200]; edx = [eax]; ICALL([edx+0x60])` after pushing three
   arguments. (`0x004281FC` is also registered in `.CRT$XIA`; that is not the
   path taken here.)
2. Measured at the callee's entry, from the guest stack:
   `[esp] = 0xdead0000` (the lifted call's synthetic return address) and the
   arguments `(0x0041DAA4, 0x00000000, 0x10500410)`. `0x0041DAA4` is exactly the
   constant the lifted caller pushes at `0x0041D8D4`, which confirms the call
   site.
3. `eax` at entry is `0x10500400` — the value of the global `[0x4BB200]`, i.e.
   the receiver of the virtual call. The callee's first act is
   `mov [ebp-0x1C], eax`, so the object pointer is what it goes on to use.
4. The callee then takes `ecx = [[ebp-0x1C]+0xC]` (a table the object carries),
   writes the first argument to `[ecx+0x78]`, and calls lifted `sub_00428714`
   with that table and the second argument (0) as an index. That helper returns
   `[table+0x44][0]` **only** when the index lies within
   `[table+0x38, table+0x3C]` and `[table+0x40] == 1`; otherwise it returns 0.
   It returned 0, and the caller dereferences it at `+0x28` without a NULL
   check, exactly as the original does.
5. The two `GetProcAddress` gaps below were implemented (FLS and
   `InitializeCriticalSectionAndSpinCount`, commit `b9105bb`) and the crash is
   **unchanged** — so they are ruled out as the cause.

So the NULL is a **lookup miss on a table the object carries at `+0xC`**. Both
halves of that are now measured, and the answer is "never populated":

- `[0x4BB200]` is `0x10500400`, a real constructed object: `+0x00` = vtable
  `0x004B59C8` (whose slot `+0x60` IS the crashing method), `+0x04` = 0,
  `+0x08` = 1, `+0x0C` = `0x10500460`, `+0x10` = `0x10500424`, `+0x24` =
  `0x10500438`. Not zero, not garbage.
- The table at `0x10500460` has `+0x38` = 0 and `+0x3C` = 0 (its bounds), so
  index 0 already fails the `upper <= index` test; its kind at `+0x40` is 0 as
  well and the pointer at `+0x44` is 0. The range check fires first.
- The object is built by `sub_0041D834`: it allocates, copies 16 bytes from the
  static template at `0x004B5978` (vtable, `+0x04` = 0, `+0x08` = 1,
  `+0x0C` = 0), publishes it at `[0x4BB200]`, then calls the object's virtual
  `+0x18`, `sub_00427E30` and `sub_0042779C` — and `sub_0042779C` calls
  `sub_0041D894`, which makes the crashing `+0x60` call. So the virtual method
  runs **during the object's own construction**.
- The function that builds the table is `sub_004039D0` (it writes
  `obj+0x88`..`obj+0xC4`, including the four fields the lookup reads, and sets
  `obj+0x0C` from `sub_0041CD70()` at its end). **A breakpoint on it is never
  hit before the fault**: the run goes straight from the constructor path to the
  crash, so the table is never built.

That makes this an **initialisation-order** problem rather than a missing shim:
a virtual method that needs the table is called on an object whose table builder
has not run.

A watchpoint on the object's `+0x0C` settles who writes it, and corrects the
guess above. It fires twice before the fault:

| write | `g_cur_func` | what it is |
|---|---|---|
| 1 | `0x0041D834` | the 16-byte template copy (`rep movsb`), storing the template's `+0x0C` = 0 |
| 2 | `0x00427874` | the object's virtual `+0x18`, at `0x00427895`: `[esi+0xC] = eax`, where `eax` is `sub_0042F184`'s return value |

So the pointer is installed by the object's own `+0x18` method, and
`sub_0042F184` is what hands it over. `sub_004039D0` is **not** this object's
table builder after all: it is called from `sub_0040370C` (at `0x004037E8`) to
build a *different* object's fields at `+0x88`..`+0xC4`, and the offset
coincidence is just two table-shaped structures.

What is still open, and it is the last step: **which code fills this object's
table at `obj+0x60`** — its `+0x38`/`+0x3C` (bounds), `+0x40` (kind) and `+0x44`
(pointer). The lead is `sub_0042F184` itself: it has a path that returns 0
(`0x0042F1A8`) and a path that returns its own first argument (`0x0042F1BD`),
and this run took the second — the argument being the table address — while the
table's fields stayed zero. Either the filler is a call this run did not make,
or `sub_0042F184` was supposed to fill the table on the way through.

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
