# DirectInput8 upstream audit (TrackMania / `DINPUT8.dll`)

Audit date: 2026-11-23. Branch: `feature/p10-dinput8-audit`.
Scope: research and documentation only. **No runtime code was changed and no
live-game result is claimed.**

## 1. Question and evidence limits

Question: *What DirectInput8 ABI and behaviour does a DINPUT8-importing
TrackMania build need, what upstream implementations exist, and which portable
parts can be adapted without introducing a second input stack next to
`runtime/win32hle/dinput.c`?*

Evidence limits, stated up front:

- This checkout has **no Windows TrackMania executable**. `docs/GAME_MATRIX.md`
  marks Nations ESWC/Forever/United Forever as `BLOCKED: no Windows game files;
  only a native macOS app present`. `docs/ISSUES_AUDIT.md` (#22–#24) likewise
  lists DInput8 as a dependency, not an implemented/observed subsystem.
- Therefore the phrase “DINPUT8.dll imports” is treated as the task brief's
  premise, not as a verified PE import-table dump. Wine's `dinput8.spec` (below)
  shows that the only public import a normal DInput8 game needs is
  `DirectInput8Create`; every other call is an indirect COM vtable call. The
  exact import name and the exact method offsets must be re-checked against the
  owned binary when one is available.
- Upstream was read at a **pinned** Wine tag and a **pinned** local PCRecomp
  checkout; links and hashes are in §2.

## 2. Sources inspected

### Local, first-party

| Path | What it is |
|---|---|
| `runtime/win32hle/dinput.c` (565 LOC) | current DirectInput 3/5/7 shim: SDL-backed keyboard/mouse state, COM objects, guest vtable tables, input record/replay |
| `runtime/win32hle/module.c:43-48`, `runtime/win32hle/host.c:129` | system-DLL name list and shim registration; `dinput8` is **not** registered today |
| `runtime/win32hle/input_rec.c`, `runtime/win32hle/screen.c:724,942` | recording/replay contract and mouse-coordinate handling used by `dinput.c` |
| `docs/UPSTREAM_CODE_PROVENANCE.md` | existing policy: Wine is “inspected but not copied (semantic references only)” |
| `docs/ISSUES_AUDIT.md` #5, #11, #22–#24; `docs/GAME_MATRIX.md:220-222` | roadmap/status for DInput8 and TrackMania |

### pcrecomp local checkout

`/Users/haseeb-mir/Documents/Code/pcrecomp`, HEAD
`a85ea970f9e0640ab6546d2ca7a01fccb132973d` (2026-10-26), MIT, Copyright (c)
2026 sp00nz. A repository-wide search for `dinput`/`DirectInput` finds **no**
DirectInput implementation: only a comment in `runtime/win32hle/module.c:40`
listing `dinput` as a system DLL. The only input source is
`runtime/recomp16/hal/input.c` + `input.h`, a 16-bit BIOS-style
`KeyboardState`/`MouseState` HAL (circular key buffer, INT 16h/INT 33h shape).
It is not a DirectInput COM layer and is not a candidate for adaptation here.

### Wine, pinned

Wine tag `wine-10.0`, commit
`b073859675060c9211fcbccfd90e4e87520dc2c2` (annotated tag object
`2e9cb99369e5ead9d7fe9836dcd43c05e42890bc`, 2025-01-21). Files were read via
`raw.githubusercontent.com/wine-mirror/wine/wine-10.0/...` and are linkable at
`https://github.com/wine-mirror/wine/blob/b073859675060c9211fcbccfd90e4e87520dc2c2/<path>#L<n>`.

| File (Wine tag `wine-10.0`) | Relevant locations |
|---|---|
| `dlls/dinput8/Makefile.in` | `PARENTSRC = ../dinput`, `EXTRADEFS = -DDIRECTINPUT_VERSION=0x0800` |
| `dlls/dinput8/dinput8.spec` | 1 public export (`DirectInput8Create`) + 4 private COM exports |
| `dlls/dinput8/dinput8.idl` | coclass `DirectInput8`/`DirectInputDevice` GUIDs |
| `dlls/dinput/dinput.c` | `dinput7_vtbl` L727, `dinput8_vtbl` L741, `dinput8_CreateDevice` L318, `dinput8_EnumDevices` L335, `dinput8_Initialize` L398, `dinput8_EnumDevicesBySemantics` L509, `dinput_create` L779, `DirectInput8Create` L937 |
| `dlls/dinput/device.c` | `dinput_device_Acquire` L475, `SetDataFormat` L524, `SetCooperativeLevel` L552, `GetCapabilities` L669, `EnumObjects` L744, `GetProperty` L1166, `GetDeviceState` L1440, `GetDeviceData` L1504, `dinput_device_w_vtbl` L2096, `dinput_device_init_device_format` L2225 |
| `dlls/dinput/keyboard.c` | `keyboard_enum_device` L168, `keyboard_create_device` L188, `keyboard_unacquire` L244, `keyboard_enum_objects` L271 |
| `dlls/dinput/mouse.c` | `mouse_enum_device` L69, `mouse_acquire` L344, `mouse_enum_objects` L428, `mouse_create_device` L506 |
| `dlls/dinput/data_formats.c` | `c_dfDIMouse` L273, `c_dfDIMouse2` L296, `c_dfDIKeyboard` L564 |
| `dlls/dinput/dinput_main.c` | input thread, low-level hooks, raw input, foreground tracking (Win32-only) |
| `dlls/dinput/{ansi.c,config.c,joystick_hid.c}` | A/W thunking, registry/config UI, HID joystick stack (Win32-only) |
| `dlls/dinput/tests/dinput.c` | `test_DirectInput8Create` L360 (error-code expectations) |
| `dlls/dinput/tests/device8.c`, `dlls/dinput/tests/joystick8.c` | behavioural reference for device methods |
| `include/dinput.h` | GUID/IID definitions L30–75, HRESULTs L128–181, class/type codes L197–220, `DIEDFL_*` L190–195, `DISCL_*` L972–976, `DIDFT_*` L750–774, `DIDEVICEOBJECTDATA` L790, `DIPROPHEADER`/`DIPROP_*` L835–929, `DIDEVCAPS` L940, IDirectInput8 vtable L2479–2497, IDirectInputDevice8 vtable L1952–1992, `DIMOUSESTATE`/`DIMOFS_*` L2120–2145 |
| `LICENSE`, `COPYING.LIB` | Wine licence: LGPL-2.1-or-later |

### Licence summary

| Source | Licence | Notes |
|---|---|---|
| pcrecomp local checkout | MIT, Copyright (c) 2026 sp00nz | no DInput8 source to reuse |
| Wine (all files cited above, including the tests) | **LGPL-2.1-or-later** | file headers name Marcus Meissner, Lionel Ulmer, TransGaming Technologies Inc. (2000–2002), Vitaliy Margolen (2007), Raphael Junqueira (2005), Alexandre Julliard (2010), Lucas Fialho Zawacki (2011), Andrew Nguyen (2011). Root `LICENSE`/`COPYING.LIB` at the pinned tag. |
| GameReComp | AGPL-3.0-only (`LICENSE`), `NOTICE` table + `LICENSES/` | new vendored code requires a `NOTICE` row and the licence text under `LICENSES/` |

Consequence: **this audit copies no Wine code.** It extracts ABI facts
(vtable order, numeric constants, struct layout, validation rules) and writes
an independently implemented HLE shim. If a future change instead copies Wine
expressions (tables, switch bodies), that is a licensing event: preserve the
per-file notice, add a `NOTICE` row with the pinned Wine commit, add the LGPL
text under `LICENSES/`, and get an explicit maintainer/licensing decision first.

## 3. What Wine's DINPUT8.dll actually is

Two facts reshape the implementation task:

1. `dlls/dinput8/Makefile.in` builds `dinput8.dll` from `PARENTSRC = ../dinput`
   with `-DDIRECTINPUT_VERSION=0x0800`. There is no separate DirectInput8
   engine in Wine; the DX3/5/7 and DX8 interfaces are two vtables over the same
   `struct dinput` and `struct dinput_device` (`dinput_private.h`,
   `device_private.h`).
2. `dlls/dinput8/dinput8.spec` exports only:

   ```
   @ stdcall DirectInput8Create(long long ptr ptr ptr)
   @ stdcall -private DllCanUnloadNow()
   @ stdcall -private DllGetClassObject(ptr ptr ptr)
   @ stdcall -private DllRegisterServer()
   @ stdcall -private DllUnregisterServer()
   ```

   So a guest PE importing `DINPUT8.dll` normally resolves **one** function;
   `QueryInterface`, `CreateDevice`, `GetDeviceState`, etc. are COM slot calls.
   Adding DInput8 here is an ABI-front-end change, not a new DLL subsystem.

`DirectInput8Create(hinst, version, riid, out, outer)` in
`dlls/dinput/dinput.c:937`:

- `out == NULL` → `E_POINTER` (0x80004003).
- `riid` must be `IID_IDirectInput8A` (byte array `30 80 79 BF 3A 48 A2 4D AA
  99 5D 64 ED 36 97 00`), `IID_IDirectInput8W` (`31 80 79 BF …`) or
  `IID_IUnknown`; anything else (including all DX3/5/7 IIDs and
  `CLSID_DirectInput8`) → `DIERR_NOINTERFACE` with `*out = NULL`.
- `outer != NULL` is rejected after the interface query: `*out = NULL` but the
  returned HRESULT is the previous `QueryInterface` result, i.e. `DI_OK`
  (`dinput.c:957-961`, `tests/dinput.c:409-411`). Do not “improve” this to
  `DIERR_NOINTERFACE` if byte-for-byte Wine parity matters.
- version must be exactly `0x0800`: lower → `DIERR_BETADIRECTINPUTVERSION`
  (0x80070481), higher → `DIERR_OLDDIRECTINPUTVERSION` (0x8007047E).
  `hinst == NULL` → `DIERR_INVALIDPARAM`, version 0 → `DIERR_NOTINITIALIZED`.
- On success the returned object is the same implementation object; the IID
  selects the A or W vtable. `dinput_create` initialises `evsequence = 1`.

### Vtable slots (x86 stdcall, 4 bytes per slot)

`IDirectInput8A/W` has **11** slots (`include/dinput.h:2479-2497`,
`dinput.c:741-754`):

| Index | Offset | Method |
|---|---|---|
| 0–2 | 0x00–0x08 | `QueryInterface`, `AddRef`, `Release` |
| 3 | 0x0C | `CreateDevice` |
| 4 | 0x10 | `EnumDevices` |
| 5 | 0x14 | `GetDeviceStatus` |
| 6 | 0x18 | `RunControlPanel` |
| 7 | 0x1C | `Initialize` |
| 8 | 0x20 | `FindDevice` |
| 9 | 0x24 | `EnumDevicesBySemantics` |
| 10 | 0x28 | `ConfigureDevices` |

`IDirectInputDevice8A/W` has **32** slots (`include/dinput.h:1952-1992`,
`device.c:2096-2135`): 3 `IUnknown` + 15 device + 9 device2 + 2 device7 + 3
device8 (`BuildActionMap` 0x74, `SetActionMap` 0x78, `GetImageInfo` 0x7C).

`IDirectInput7A/W` has **10** slots: index 8 is `FindDevice`, index 9 is
`CreateDeviceEx` (`dinput.c:727-739`).

### Gaps in the current `runtime/win32hle/dinput.c`

These are ABI facts, not hypotheses:

| Area | Current shim | Wine/DirectInput8 expectation | Impact for a DInput8 guest |
|---|---|---|---|
| DI object vtable | 10 entries; index 8 = `CreateDeviceEx`, index 9 = “EnumDevicesBySemantics placeholder” | index 8 = `FindDevice`, 9 = `EnumDevicesBySemantics`, 10 = `ConfigureDevices` | `+0x20` calls the wrong method; `+0x28` reads past the table. Also wrong for DI7 (the existing comment claims `CreateDeviceEx` at `+0x24`, but the table puts it at `+0x20`). |
| Device vtable | 29 entries, ends at `WriteEffectToFile` | 32 entries; add `BuildActionMap`/`SetActionMap`/`GetImageInfo` at `+0x74/+0x78/+0x7C` | a DInput8 caller indexing those slots calls whatever follows the table |
| `DirectInput8Create` | not exported; `d_CreateEx` ignores version, IID and `outer` | 5-arg export with the checks above | no `DINPUT8.dll` import can bind today; wrong HRESULTs on bad input |
| Mouse state size | clamps `GetDeviceState` to **16** bytes (`DIMOUSESTATE`: 3 axes + 4 buttons) | `c_dfDIMouse2` is **20** bytes (`DIMOUSESTATE2`: 3 axes + 8 buttons), `DIMOFS_BUTTON4..7` at 16–19 | TrackMania using `c_dfDIMouse2` never sees buttons 4–7 |
| `EnumDevices` filter | uses `0x05` for keyboard and `0x12` for mouse in the callback `dwDevType`; comment calls them DI8 classes | `DI8DEVCLASS_POINTER=2`, `KEYBOARD=3`, `GAMECTRL=4`; `DI8DEVTYPE_MOUSE=0x12`, `KEYBOARD=0x13` (`include/dinput.h:197-220`); `keyboard_enum_device`/`mouse_enum_device` set `dwDevType = DI8DEVTYPE_* | (subtype << 8)` for version ≥ 0x0800 | class-filtered enumeration can miss both devices; `0x05` is not a valid class or type |
| `GetCapabilities` | always writes 44 bytes | accepts only `dwSize == sizeof(DIDEVCAPS_DX3)` (24) or `sizeof(DIDEVCAPS)` (44), else `DIERR_INVALIDPARAM` | 24-byte caller gets a 20-byte overrun |
| `GetDeviceState` | clamps `size` to 16/256 and returns `DI_OK` | requires `size == user_format->dwDataSize` and a set data format, else `DIERR_INVALIDPARAM`; polls first; returns `DIERR_NOTACQUIRED`/`DIERR_INPUTLOST` by status | lenient today; strict games may depend on the error, and the mouse cap above is a real data gap |
| `GetDeviceData` | always `*count = 0`, `DI_OK` | for DInput8 with no `DIPROP_BUFFERSIZE` queue → `DIERR_NOTBUFFERED` (0x80040207); with a queue → events and `DI_BUFFEROVERFLOW` (=1) | a game that actually requests buffered data silently sees no input |
| `SetCooperativeLevel` | stores any hwnd/flags | exactly one of `EXCLUSIVE|NONEXCLUSIVE` and one of `FOREGROUND|BACKGROUND`; exclusive+background rejected for mouse/keyboard; `DIERR_ACQUIRED` if already acquired | bogus modes accepted; Windows would reject them |
| `Acquire` | only checks device index and SDL init | requires `SetDataFormat` first (`DIERR_INVALIDPARAM`), `DIERR_OTHERAPPHASPRIO` when FOREGROUND window is not foreground | wrong error paths |
| HRESULT constants | `DIERR_NOTACQUIRED = 0x80070005`, `DIERR_UNSUPPORTED = 0x80004005` | `DIERR_NOTACQUIRED = 0x8007000C` (ERROR_INVALID_ACCESS), `DIERR_UNSUPPORTED = E_NOTIMPL = 0x80004001` | code that branches on the exact HRESULT can take the wrong path. `0x80070005` is `DIERR_OTHERAPPHASPRIO`/`DIERR_READONLY`. |

Correct values already in the shim (`DI_OK=0`, `DIERR_INVALIDPARAM=0x80070057`,
`DIERR_NOTINITIALIZED=0x80070015`, `DIERR_DEVICENOTREG=0x80040154`,
`E_NOINTERFACE=0x80004002`, `E_POINTER=0x80004003`) match Wine. The two `GUID_SYS_*`
byte arrays also match Wine's `GUID_SysKeyboard`/`GUID_SysMouse` exactly.

## 4. What is portable and what is not

### Adopt (ABI/semantic facts, written independently)

- The export signature and validation order of `DirectInput8Create`; the A/W
  and `IUnknown` IID byte arrays; version gate `0x0800`.
- The 11-slot IDirectInput8 and 32-slot IDirectInputDevice8 vtable orders,
  including the three DInput8-only device slots. Fill unused slots with
  explicit “unsupported” shims that return truthful HRESULTs, never `NULL`.
- Numeric constants: `DI8DEVCLASS_*`, `DI8DEVTYPE_*`, `DIEDFL_*`, `DISCL_*`,
  `DIDFT_*`, `DIMOFS_*`, the HRESULT table in §3, and `DIDEVICEOBJECTDATA`
  (20 bytes in DInput8: `dwOfs`, `dwData`, `dwTimeStamp`, `dwSequence`,
  `uAppData`).
- Data-format shapes: `c_dfDIKeyboard` (256-byte state, 256 button objects at
  offsets 0–255), `c_dfDIMouse` (16-byte `DIMOUSESTATE`, 7 objects) and
  `c_dfDIMouse2` (20-byte `DIMOUSESTATE2`, 11 objects, buttons 4–7 at offsets
  16–19). `dinput.c` already has the 256-key path and the `DIMOFS_*` enum; only
  the 20-byte mouse cap needs extending.
- Cheap validation that keeps the ABI honest: `dwSize`/`dwObjSize` checks in
  `SetDataFormat`, `dwSize` check in `GetCapabilities`, cooperative-level flag
  checks, `SetDataFormat`-before-`Acquire`, exact `GetDeviceState` size.
- If a game is found to use buffered input, add a small event queue **inside**
  `dinput.c`, fed from the existing polled key/mouse transitions. That is still
  one input stack; it does not require Wine's hook machinery.

### Reuse from this repository (the single input stack)

Keep `dinput.c` as the sole producer of input state:

- `g_dev[]`/`dev_index()` device table and `hle_com_new`/vtable helpers.
- `dik_from_scancode()` and the `SDL_GetKeyboardState()` → 256-byte DIK array
  path in `dev_GetDeviceState`.
- `hle_screen_mouse()` + `hle_input_mouse_buttons()` and the existing
  absolute/exclusive-relative mouse behaviour in `dev_GetDeviceState`/`dev_Poll`.
- `hle_input_record`/`hle_input_replay` transcript contract in `input_rec.c`.
- The existing “joystick absent → `DIERR_DEVICENOTREG`” policy. It is truthful
  on this host and avoids inventing centred axes.

The incremental change is then: add a `dinput8` entry to `module.c:g_system[]`
and `host.c` registration; expose `DirectInput8Create` from a new shim table in
`dinput.c` (or a tiny `dinput8.c` that only holds the export and vtables and
calls the shared helpers); widen/fix the shared device methods. No new SDL
event pump, no worker thread, no hook callbacks, no duplicate state.

### Do **not** port from Wine

These are the parts that would create a second input stack or are unusable on
this host:

| Wine file | Why not |
|---|---|
| `dlls/dinput/dinput_main.c` | `wine_dinput_worker` thread, `WH_KEYBOARD_LL`/`WH_MOUSE_LL`/`WH_CBT` hooks, `RegisterRawInputDevices`, `DIEmWin` message window — Win32-only and a second event source |
| `dlls/dinput/keyboard.c` (hook/rawinput halves), `mouse.c` (hook/warp halves) | state accumulation from messages/raw input and cursor warping; `dinput.c` already reads live SDL state at poll time |
| `dlls/dinput/joystick_hid.c`, `config.c`, `ansi.c`, `effect.c` | HID enumeration, registry/config UI, Windows code-page conversion, force feedback — irrelevant to the keyboard/mouse TrackMania path and heavyweight |
| `dlls/dinput/tests/*` | useful as behavioural references, not runtime code |

## 5. Recommendation

1. Treat DInput8 as a **thin ABI front-end over `dinput.c`**, not a new DLL
   engine. Register `dinput8` as a system module and export only
   `DirectInput8Create` (verify whether the target PE uses the undecorated name;
   Wine's spec and existing `dinput` shim both use undecorated names).
2. Fix the shared vtable tables and HRESULTs **before** adding the export:
   `FindDevice`/`EnumDevicesBySemantics`/`ConfigureDevices` on the DI object,
   the three missing device8 slots, `DIERR_NOTACQUIRED`/`DIERR_UNSUPPORTED`
   values, and the 20-byte mouse state. These fixes benefit existing games
   too; keep them behind review because they change observable error codes
   (Cave Story's `Doukutsu` is already called out in the file header).
3. Correct class/type constants in `EnumDevices` for version ≥ 0x0800 and
   validate `dwSize` in `GetCapabilities`/`GetDeviceState`.
4. Do not add joystick/HID/force-feedback support for this target unless the
   owned binary shows DirectInput game-controller calls that actually block
   progress; that would be a separate, larger decision.
5. Keep Wine as a *semantic* reference only, matching
   `docs/UPSTREAM_CODE_PROVENANCE.md`. No `NOTICE`/`LICENSES/` change is needed
   for this audit.

## 6. Open questions / verification gates

- Dump the owned TrackMania PE import table: confirm `DINPUT8.dll!DirectInput8Create`,
  the import name spelling, companion `D3D9.dll` imports, and whether any
  ordinal imports occur.
- Determine from the binary/trace whether the game uses `GetDeviceState` with
  `c_dfDIKeyboard`/`c_dfDIMouse2`, or buffered `GetDeviceData` with
  `DIPROP_BUFFERSIZE`; this decides whether the small internal event queue is
  required.
- Check whether the game calls `EnumDevicesBySemantics`/`ConfigureDevices` or
  the device8 action-map slots; if so, return truthful unsupported HRESULTs
  rather than faking a mapping UI.
- End-to-end success is blocked on D3D9 (#5) and an unprotected, owned binary
  (#22–#24). A build or selftest alone must not be reported as TrackMania
  success, per the repository's verification rules.

## 7. Verification performed for this audit

- Read `runtime/win32hle/dinput.c`, `module.c`, `host.c`, `input_rec.c`,
  `docs/ISSUES_AUDIT.md`, `docs/GAME_MATRIX.md`, `docs/UPSTREAM_CODE_PROVENANCE.md`,
  `NOTICE`, `LICENSE`, `docs/LICENSES.md` in the audit worktree.
- Read the pinned pcrecomp checkout (`git rev-parse HEAD` confirmed above) and
  searched it for DirectInput references.
- Fetched the pinned Wine files listed in §2 from `raw.githubusercontent.com`
  (all HTTP 200) and recorded exact line numbers, GUIDs, HRESULTs and vtable
  orders.
- No code, build, emulator or live-game verification was performed; this is a
  documentation-only deliverable.
