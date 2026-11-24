# MAXCOMP-D provenance (audio, input, networking HLE)

Branch `feature/maxcomp-d-audio-input`, base `cb4dc0441199af0fc15d0100a565bd8e444a228e`.

This file records the upstream facts used by the guest-ABI fixes in
`runtime/win32hle/{dinput,dsound,winmm,wsock32}.c`. Following
`docs/UPSTREAM_CODE_PROVENANCE.md`, upstream sources are **semantic references
only**: no upstream expression, table or function body was copied. The shims
are independently written against the documented ABI, so no `NOTICE` row or
`LICENSES/` change is required.

## Pinned upstream sources

| Upstream | Pin | Licence | Material read |
|---|---|---|---|
| Wine (`wine-mirror/wine`) | tag `wine-10.0`, commit `b073859675060c9211fcbccfd90e4e87520dc2c2` (2025-01-21) | LGPL-2.1-or-later | `include/dinput.h`, `include/dsound.h`, `dlls/dsound/dsound.spec`, `dlls/wsock32/socket.c`, `dlls/dinput/keyboard.c`; plus the file list in `docs/DINPUT8_UPSTREAM_AUDIT.md` §2 |
| SDL2 | Homebrew SDL2 (local host backend already used by `runtime/win32hle`) | zlib | `SDL_OpenAudioDevice`, `SDL_DequeueAudio`/audio callback, `poll(2)` host primitives are libc/POSIX |
| PCRecomp | local HEAD `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | baseline `runtime/win32hle/*` implementation already in this tree |

Wine links, all at the pinned commit:
`https://github.com/wine-mirror/wine/blob/b073859675060c9211fcbccfd90e4e87520dc2c2/<path>`.

## DInput: legacy `IDirectInput::EnumDevices` (dinput.c)

- `include/dinput.h` defines the legacy device types `DIDEVTYPE_DEVICE=1`,
  `DIDEVTYPE_MOUSE=2`, `DIDEVTYPE_KEYBOARD=3`, `DIDEVTYPE_JOYSTICK=4`, and the
  DI8 classes/types `DI8DEVCLASS_POINTER=2`, `KEYBOARD=3`, `GAMECTRL=4`,
  `DI8DEVTYPE_MOUSE=0x12`, `KEYBOARD=0x13`. The previous shim filtered on
  `0x05`/`0x12`, neither of which is a legacy class.
- `DIDEVICEINSTANCE_DX3A/W` layout is `dwSize(0)`, `guidInstance(4)`,
  `guidProduct(20)`, `dwDevType(36)`, `tszInstanceName(40)`,
  `tszProductName(300)` = 560 bytes on x86. The old shim wrote a 160-byte
  structure with the names at offsets 20/84, overwriting the product GUID and
  the device type.
- `LPDIENUMDEVICESCALLBACK` is `BOOL CALLBACK (LPCDIDEVICEINSTANCE, LPVOID)`:
  exactly two stdcall args. The old shim pushed three, corrupting the guest
  stack for a real callback.
- `dlls/dinput/keyboard.c:keyboard_enum_device` sets
  `dwDevType = DIDEVTYPE_KEYBOARD | (DIDEVTYPEKEYBOARD_PCENH << 8)` for a
  version below 0x0800 (DI8 adds `DI8DEVTYPE_KEYBOARD` instead), with
  `guidInstance = guidProduct = GUID_SysKeyboard`. The mouse equivalent uses
  `DIDEVTYPEMOUSE_TRADITIONAL` and `GUID_SysMouse`.
- `DIEDFL_FORCEFEEDBACK` (0x100) selects only force-feedback devices; this host
  has none, so the answer is no devices, not a fabricated GUID.
- No Wine code was ported; the guest callback/structure handling is written
  here and checked by `dinput_legacy_enum_selftest.c`.

## Verification policy

A C selftest linked with the complete HLE core and a targeted pytest run are
unit/build evidence only (gate A3). No live-game claim is made here; per
`AGENTS.md`, injected-DLL behaviour must additionally pass the Unicorn harness
and then the game/Wine workflow before any such claim.
