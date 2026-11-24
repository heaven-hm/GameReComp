# GR5 provenance — WINMM waveOut, the four missing imports, and a guest-ABI fixture

Date: 2026-11-24. Branch: `feature/grprod-5-audio-input`. Base:
`cb4dc0441199af0fc15d0100a565bd8e444a228e`.

Scope rule for this task: audit `dsound.c`, `dinput.c`, `winmm.c`, `wsock32.c`
and `sdlmix.c`; change **exactly one** of them plus one NEW `*_selftest.c`, with
no edits to `host.c` or any header. This change owns
`runtime/win32hle/winmm.c` and adds
`runtime/win32hle/winmm_waveout_abi_selftest.c`. No live-game result is claimed.

## 1. Audit outcome and why `winmm.c` was chosen

| File | Audited state | Gap found |
|---|---|---|
| `dsound.c` | Real DirectSound 1 buffers on SDL2, driver enumeration, `hle_audio_stream_*` host streams, COM registration | Beyond DirectSound 1 is a documented deliberate non-goal (README §"Deliberate non-goals"); no ordinary import gap in scope |
| `dinput.c` | DX3/5/7 and DI8 facades, SDL-backed keyboard/mouse, complete vtable slots, honest `DIERR_*` for joystick/FF/action mapping | No missing public import; `DirectInput8Create` already implemented (commit `42c82e1`) |
| `wsock32.c` | Winsock 1.1 names and ordinals, DNS, select, overlapped-event objects with real waits | No game-class import gap identified for this task |
| `sdlmix.c` | Real `Mix_*` samples, PCM WAV load, cdecl ABI; music returns NULL and says why | Music decoders are a third-party port, not a small API gap (same reasoning as the README's BulletML/msvcp140 non-goals) |
| **`winmm.c`** | Timers, mmio, `sndPlaySound*` on the SDL2 stream; **waveOut was "accept and retire" (silent)**; 4 waveOut imports absent | **Concrete, evidenced blocker**: TrackMania Nations ESWC's PE import table names `WINMM.dll!waveOutRestart` and `WINMM.dll!waveOutPause` as unresolved (`docs/GAME_TM_ESWC_EVIDENCE.md`, §4.3 log lines 189–190), so those imports cannot bind at all. `waveOutGetPosition` and `waveOutBreakLoop` are the adjacent real APIs a waveOut-using game calls. |

Other modules are deliberately untouched: the parent task assigns one `.c` file
per agent so parallel work does not collide.

## 2. What was implemented

In `runtime/win32hle/winmm.c`:

1. **Real waveOut PCM** — `waveOutOpen` now takes a `dsound.c` host stream
   (`hle_audio_stream_open/push/queued/gain/close`, declared in `win32hle.h`
   lines 411–415) at the guest's PCM format. `waveOutWrite` converts 8/16-bit
   mono/stereo WAVEHDR samples to interleaved s16 and pushes them into that
   stream. No second SDL device and no duplicate mixer. Format/device/callback
   validation, `WAVERR_UNPREPARED` / `WAVERR_STILLPLAYING`, `WHDR_*` state,
   `waveOutReset`, `waveOutClose`, volume and devcaps all follow Win32.
   A completion thread marks a buffer DONE and fires `WOM_DONE` only when the
   audio clock has consumed its frames, with callbacks for FUNCTION / EVENT /
   WINDOW modes. This replaces a fake handle whose writes were "done" instantly.
2. **`waveOutPause` / `waveOutRestart`** — the two unresolved TrackMania
   imports. Pause mutes the stream and freezes WAVEHDR completion; restart
   restores gain and lets completion catch up. Bad handles return
   `MMSYSERR_INVALHANDLE`.
3. **`waveOutGetPosition`** — `MMTIME` in audio time; `TIME_SAMPLES` reports
   consumed frames, other requests are coerced to `TIME_BYTES` as in Wine's
   `WINMM_FramesToMMTime`; short `MMTIME` and NULL are errors.
4. **`waveOutBreakLoop`** — a looping (`WHDR_BEGINLOOP|WHDR_ENDLOOP`) buffer is
   re-pushed one pass at a time; the break stops repeats at the next pass
   boundary instead of leaving up to `dwLoops` passes queued.

`runtime/win32hle/winmm_waveout_abi_selftest.c` drives all of it as lifted
guest code: an IAT-style indirect call through a guest shim driver (stdcall arg
order and `eax` return), a guest `WAVECALLBACK` checking `(hwo, 0x03BD,
dwInstance, WAVEHDR, 0)`, and guest-visible `WAVEFORMATEX`, `WAVEHDR` and
`MMTIME` structures. It asserts PCM reaches the SDL stream, completion is timed
(not synchronous), pause/restart and break-loop change real state, and the new
imports reject bad handles.

## 3. Sources and what was taken from each

### Upstream ABI reference — Wine, pinned

Wine tag `wine-10.0`, commit
`b073859675060c9211fcbccfd90e4e87520dc2c2` (annotated tag object
`2e9cb99369e5ead9d7fe9836dcd43c05e42890bc`). Files read at that tag:

| File | Facts used (no source lines copied) |
|---|---|
| `include/mmsystem.h` | `MMSYSERR_*` values (0/1/2/4/5/6/7/8/10/11), `WAVERR_STILLPLAYING`=33 / `WAVERR_UNPREPARED`=34, `WAVE_FORMAT_PCM`=1, `CALLBACK_TYPEMASK`=0x70000 / FUNCTION=0x30000 / EVENT=0x50000 / WINDOW=0x10000, `WOM_DONE == MM_WOM_DONE == 0x03BD`, `TIME_MS/SAMPLES/BYTES`=1/2/4, `WAVEHDR` = 8 dwords (32 bytes on x86) with flags at +16 and `dwLoops` at +20, `WAVEOUTCAPSA` = 52 bytes, `MMTIME` = `UINT` + 8-byte union (12 bytes on x86) |
| `dlls/winmm/waveform.c` | `waveOutOpen` / `Write` / `PrepareHeader` / `UnprepareHeader` / `Pause` / `Restart` / `BreakLoop` / `GetPosition` / `Reset` / `Close` argument validation and return codes; `WINMM_FramesToMMTime` (only `TIME_SAMPLES` honoured, everything else coerced to `TIME_BYTES`); `WINMM_Reset` returns queued headers with WHDR_DONE and a WOM_DONE callback; `waveOutBreakLoop` clears the loop counter and returns NOERROR |
| `dlls/winmm/winmm.spec` | the public WINMM export names used in the shim table |

Wine is **LGPL-2.1-or-later** (`LICENSE`, `COPYING.LIB` at the pinned tag).
This change copies **no Wine code**: it extracts ABI constants, struct offsets
and validation rules and implements them independently in this repository's
shim style. No Wine file was vendored, so no `NOTICE` row or `LICENSES/` entry
is added. If a future change copies Wine expressions, that is a licensing
event requiring the per-file notice, a pinned-commit `NOTICE` row and the LGPL
text.

### First-party and local

| Path | What it is |
|---|---|
| `runtime/win32hle/dsound.c:80-118` (base commit) | `hle_audio_stream_*`, the SDL2 audio device and its software mixer. Reused as the SDL2 helper; no edit to `dsound.c` was needed |
| `runtime/win32hle/winmm.c` (base commit) | `sndPlaySound*`, mmio, timers — the existing code the change builds on; its old silent waveOut path is replaced in the same file |
| `runtime/win32hle/build_selftests.sh` | builds every `*_selftest.c` with the full layer and SDL dummy drivers; the new fixture was added as a file only, no build-script edit |
| `docs/GAME_TM_ESWC_EVIDENCE.md:189-190` | the game-class evidence: `waveOutRestart` and `waveOutPause` unresolved in a real PE boot log |
| `/Users/haseeb-mir/Documents/Code/pcrecomp` HEAD `a85ea970f9e0640ab6546d2ca7a01fccb132973d` (MIT, Copyright (c) 2026 sp00nz) | upstream local checkout inspected; its `winmm.c` has the **same silent waveOut** as this base, so it offered no implementation to reuse |
| sibling branch `feature/issue-10-av`, commit `9c37a9e5cff4231032c5b78d0e59a78a22a6d643` (unmerged, **not** an ancestor of this base) | inspected only to avoid accidental duplication. Its code was **not copied**: it needed a PCM-capture hook in `dsound.c` (outside this task's single-file ownership), and it used `MM_WOM_DONE 0x3D5`, which contradicts Wine's `0x03BD`. This change uses the Wine value and tests it explicitly. |

### FFmpeg

FFmpeg is available on this host and used by `bink.c` / `mss.c` for compressed
movie/MP3 audio. It is **not** used here: `waveOutWrite` hands the HLE PCM that
the guest already decoded, so there is nothing to decode and no honest reason
to link a second codec stack. The reused helper is the first-party SDL2 stream
in `dsound.c`. FFmpeg's LGPL/GPL obligations therefore do not attach to this
change.

## 4. Verification

- Targeted selftest build and run: `winmm_waveout_abi_selftest` — **all checks
  passed** (whole layer linked, `SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy`).
- Targeted pytest: `python3 -m pytest tests/test_api_coverage.py
  tests/test_runtime_registration.py -q` — **23 passed**.
- Full headless suite: see the completion report; no edits to other selftests.
- No live game was run. Gate remains A3 (build/unit), not A8; the TrackMania
  import evidence justifies the API choice, it is not a claim that the game now
  boots or plays sound.

## 5. Limits and deliberate boundaries

- Pause is truthful about what this layer can do: the SDL stream's gain goes to
  zero and WAVEHDR completion is frozen, but `dsound.c` has no device-level
  pause, so frames already in the stream drain silently; their callbacks are
  delivered on restart. A game that measures sample-accurate pause position
  will see that boundary.
- Looping is implemented for the last queued buffer (the usual streaming
  pattern); earlier buffers play once. Repeats are pushed lazily so
  `waveOutBreakLoop` affects the next pass rather than pre-queued audio.
- Mixer and waveIn remain absent (honest no-device answers), MCI remains
  stubbed, joystick remains unplugged. `DirectSound3D`, DirectMusic and
  compressed WAV formats are out of scope here.
- Headless dummy-audio timing is real-time but not sample-exact; the selftest
  uses generous bounds and does not claim hardware verification.
