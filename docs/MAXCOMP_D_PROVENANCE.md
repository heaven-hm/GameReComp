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

## WinMM: real waveOut playback (winmm.c)

- `include/mmsystem.h` (pinned Wine tag) fixes the guest layouts used here:
  `WAVEHDR` = `lpData(0)`, `dwBufferLength(4)`, `dwBytesRecorded(8)`,
  `dwUser(12)`, `dwFlags(16)`, `dwLoops(20)`, `lpNext(24)`, `reserved(28)`
  (32 bytes on x86); `MMTIME` = `wType(0)` + union value at 4;
  `WAVEOUTCAPSA` = `wMid(0)`, `wPid(2)`, `vDriverVersion(4)`, `szPname[32](8)`,
  `dwFormats(40)`, `wChannels(44)`, `wReserved1(46)`, `dwSupport(48)` (52 bytes).
- Error codes: `MMSYSERR_*` in 0..21, `WAVERR_BADFORMAT=32`,
  `WAVERR_STILLPLAYING=33`, `WAVERR_UNPREPARED=34`. Callback type mask is
  `CALLBACK_TYPEMASK=0x00070000`; `CALLBACK_FUNCTION=0x00030000`.
- The callback is `LPWAVECALLBACK(HWAVEOUT, UINT, DWORD_PTR, DWORD_PTR,
  DWORD_PTR)` — five stdcall args. The messages are **not** 0x3BC..0x3BE:
  Wine has `MM_WOM_OPEN=0x3BB`, `MM_WOM_CLOSE=0x3BC`, `MM_WOM_DONE=0x3BD`.
  (An earlier draft of this shim used the off-by-one values; the selftest
  caught it against the header and both were corrected.)
- `TIME_MS=1`, `TIME_SAMPLES=2`, `TIME_BYTES=4`. `waveOutGetPosition` returns
  the requested unit in `wType` and the value at offset 4.
- The previous shim accepted buffers into a fake handle and immediately set
  `WHDR_DONE`, with no audio. The new one queues `WAVEHDR`s, converts PCM
  8/16-bit mono/stereo into dsound.c's interleaved s16 stream, and completes a
  header only after its frames have been fed. No upstream code was ported;
  `winmm_waveout_selftest.c` exercises the guest ABI end to end.

## DirectSound: ordinals 9 / 11 / 12 (dsound.c)

- Wine's pinned `dlls/dsound/dsound.spec` assigns **9 = `GetDeviceID`**,
  **10 = `DirectSoundFullDuplexCreate`**, **11 = `DirectSoundCreate8`**,
  **12 = `DirectSoundCaptureCreate8`** (1/2/3/6/7/8 are the already-served
  legacy create/enumerate entries). TrackMania Nations ESWC's unresolved
  `dsound` ordinal 12 is therefore `DirectSoundCaptureCreate8`; it was absent.
- `include/dsound.h` (pinned) gives the vtable orders implemented here:
  `IDirectSound8` has 12 slots (the 8 `IDirectSound` methods plus
  `VerifyCertification`); `IDirectSoundBuffer8` has 24 (the 21 v1 methods plus
  `SetFX`, `AcquireResources`, `GetObjectInPath`); `IDirectSoundCapture8` has 6;
  `IDirectSoundCaptureBuffer8` has 14 (3 IUnknown + 8 capture-buffer methods
  + `GetObjectInPath` + `GetFXStatus`).
- IIDs used: `IID_IDirectSound8 = C50A7E93-F395-4834-9EF6-7FA99DE50966`,
  `IID_IDirectSoundBuffer8 = 6825A449-7524-4D82-920F-50E36AB3AB1E`,
  `IID_IDirectSoundCapture = B0210781-89CD-11D0-AF08-00A0C925CD16`,
  `IID_IDirectSoundCaptureBuffer(8) = B0210782-…` / `00990DF4-…`.
  The pre-existing shim binds the older DX7 `IID_IDirectSound`/`_Buffer`
  (`279AFA83/85-498E-11D0-9828-…`) and keeps serving them unchanged.
- `DSCBUFFERDESC` = `dwSize(0)`, `dwFlags(4)`, `dwBufferBytes(8)`,
  `dwReserved(12)`, `lpwfxFormat(16)`, `dwFXCount(20)`, `lpDSCFXDesc(24)`.
  `DSCBCAPS` is 16 bytes; `DSCCAPS` is 16 bytes. `DSCBLOCK_ENTIREBUFFER=1`,
  `DSCBSTART_LOOPING=1`, `DSCBSTATUS_CAPTURING=1`, `DSCBSTATUS_LOOPING=2`.
- Wine `dsound_main.c:GetDeviceID` (DSOUND.9) returns `DSERR_INVALIDPARAM` for
  a null input/output GUID, copies an arbitrary input GUID through unchanged,
  and resolves `DSDEVID_DefaultPlayback/Capture/Voice*` to the host's default
  endpoint. This host's SDL device name is used for that resolution.
- Capture is real: SDL's capture callback fills a host s16 ring and the guest
  capture buffer copies from it as the cursor advances. Headless SDL supplies
  a silent capture device; no device is fabricated. No Wine code was ported;
  `dsound_capture_selftest.c` exercises all three ordinals through guest COM
  vtables.

## Verification policy

A C selftest linked with the complete HLE core and a targeted pytest run are
unit/build evidence only (gate A3). No live-game claim is made here; per
`AGENTS.md`, injected-DLL behaviour must additionally pass the Unicorn harness
and then the game/Wine workflow before any such claim.
