# OpenAL upstream audit — TrackMania Nations Forever

**Research/recommendation only. No runtime code was changed in this task.**
**Date:** 2026-11-23 · **Branch:** `feature/p10-openal-audit` @ `0d4fbbd`

## 1. Question and evidence base

TrackMania Nations Forever (TMNF) imports `OpenAL32.dll`. This audit answers
four questions before anyone writes a shim:

1. Which OpenAL entry points are **exactly** imported?
2. What audio path does this repository already have, and is TMNF on it?
3. Is there portable OpenAL/audio code in the upstream projects we already
   reuse (pcrecomp, SDL) that should be adapted?
4. What does the licence position allow, and should we adapt or write fresh?

**How the imports were measured.** With the game's own PE file as input to the
repository's inventory tool:

```sh
python3 tools/pe/extract_imports.py /path/to/TmNationsForever/TmForever.exe
```

The binaries used for measurement came from a locally installed copy of the
game (`~/Library/TrackManiaMacOS/games/tmnf/...`). Per repository policy they
are **evidence only**: no game executable, DLL, asset or derived import dump is
committed here, and nothing in this document's recommendation requires them at
build time. Hashes are recorded in §7 so the measurement is reproducible.

## 2. Exact imports

### 2.1 Main executable — `TmForever.exe` (37 named imports)

`TmForever.exe` (PE32, x86, 10,685,290 bytes) imports **37 functions, all by
name, zero by ordinal**, from `OpenAL32.dll`. Grouped by OpenAL family:

| ALC (device/context/capture) — 15 | AL (objects/state/sources/buffers) — 22 |
|---|---|
| `alcOpenDevice` | `alGenBuffers` |
| `alcCloseDevice` | `alDeleteBuffers` |
| `alcCreateContext` | `alBufferData` |
| `alcMakeContextCurrent` | `alGenSources` |
| `alcDestroyContext` | `alDeleteSources` |
| `alcProcessContext` | `alSourcePlay` |
| `alcGetError` | `alSourceStop` |
| `alcGetString` | `alSourcef` |
| `alcGetIntegerv` | `alSource3f` |
| `alcIsExtensionPresent` | `alSourcei` |
| `alcCaptureOpenDevice` | `alSource3i` |
| `alcCaptureCloseDevice` | `alSourceQueueBuffers` |
| `alcCaptureStart` | `alSourceUnqueueBuffers` |
| `alcCaptureStop` | `alGetSourcei` |
| `alcCaptureSamples` | `alGetError` |
| | `alGetString` |
| | `alGetInteger` |
| | `alIsExtensionPresent` |
| | `alGetEnumValue` |
| | `alGetProcAddress` |
| | `alDistanceModel` |
| | `alDopplerFactor` |

Notes on the shape of this surface:

- The **buffer-queue streaming pair** `alSourceQueueBuffers` /
  `alSourceUnqueueBuffers` plus `alBufferData` and `alGetSourcei` is the
  mechanism TMNF uses for continuous audio (music/engines). A shim that only
  supports one-shot `alSourcePlay` buffers would not be enough.
- Five imports (`alcCapture*`) are the OpenAL capture extension. A minimal
  shim can answer these honestly as "no capture device" (§6).
- `alGetProcAddress`, `alIsExtensionPresent`, `alGetEnumValue` and the ALC
  equivalents mean the game probes capabilities at runtime. They must return
  truthful answers, not invented function pointers.
- There are **no** `alListener*` imports. The game sets source-space parameters
  only (`alSource3f`, `alSourcef`, `alDistanceModel`, `alDopplerFactor`).
- The patched `T1mForever.exe` in the same install imports the **same 37 names**.

### 2.2 Launcher — `1TmForeverLauncher.exe` (3 named imports)

`alcGetError`, `alcGetString`, `alcIsExtensionPresent`. This is a capability
probe: the launcher checks whether the OpenAL runtime is present before the
game starts. A shim must make these three calls succeed with plausible answers
or the launcher may refuse to select the OpenAL path.

### 2.3 What TMNF does **not** import

`TmForever.exe` imports **no `dsound.dll`, no `winmm.dll`, no `SDL_mixer.dll`,
no `mss32.dll`**. Its only other audio-adjacent import is `binkw32.dll` (movie
decoding). Consequently the repository's three existing audio shims are not on
TMNF's import path at all — they cannot supply its sound.

### 2.4 The DLL it expects

The install ships `OpenAL32.dll` (PE32 x86, 4,071,950 bytes) whose strings
identify it as **OpenAL Soft** (`OpenAL Soft`, `openal-soft`, `ALSOFT_*`), with
`OpenAL32.COPYING.txt` containing the **GNU Library General Public License,
version 2**. A Creative `wrap_oal.dll` is also present as the vendor router.
This is evidence of the ABI and licence baseline, not a component to vendor.

## 3. The audio path that already exists in this repository

All three are registered centrally in `runtime/win32hle/host.c`
(`recomp_host_init`) and declared in `runtime/win32hle/win32hle.h`.

| Source path | What it implements | Export ABI | Host audio backend | Relevant host APIs |
|---|---|---|---|---|
| `runtime/win32hle/dsound.c` | `IDirectSound`, `IDirectSoundBuffer`, software mixer with real play cursors/volume/pan, guest-visible lock buffers, DirectSound COM class | stdcall (`RET`, `dll#N` aliases) | SDL2 audio device (`SDL_OpenAudioDevice`), silent `SDL_AUDIODRIVER=dummy` fallback that still advances cursors | `hle_audio_stream_open/push/queued/gain/close`, `hle_dsound_set_master`, `hle_dsound_register_com` |
| `runtime/win32hle/sdlmix.c` | SDL_mixer 1.2 `Mix_*`: PCM WAV decode, channel mixer, volume/pan/fade; music deliberately returns failure (no MOD/OGG/MP3 decoder) | cdecl (`RETC`, matching SDL 1.2) | the same SDL2 audio device | private to the shim |
| `runtime/win32hle/winmm.c` | multimedia timers, joystick (none), `waveOut*` as a **silent accept-and-retire** device, MCI/mixer/waveIn stubbed, `mmio*` RIFF reader | stdcall (`RET`) | none (libc timing only) | via `clock.c` for virtual time |

Design rules these files establish, and which an OpenAL shim must inherit:

- **Implement what is real, fail for what is not.** `sdlmix.c` returns `NULL`
  for `Mix_LoadMUS` rather than pretending music loaded; `winmm.c` reports no
  mixer and no capture devices rather than faking them. No silent success.
- **One host device, many guest consumers.** `dsound.c` already owns the SDL2
  device and exposes the `hle_audio_stream_*` API precisely so other shims can
  push PCM into the same mixer. An OpenAL shim should be a consumer of that
  API, not a second SDL device.
- **Guest-visible memory.** Buffers the guest writes must be allocated with
  `hle_alloc` and addressed with `hle_va`/`hle_ptr`, as in `dsound.c` and
  `sdlmix.c`.
- **cdecl vs stdcall matters.** DirectSound/WinMM use `RET`; SDL_mixer uses
  `RETC` because SDL 1.2 is cdecl. OpenAL's `AL_APIENTRY` on Windows is
  **`__cdecl`**, so OpenAL shims must use `RETC`/`RETP`, not `RET`.

## 4. Upstream code audit

### 4.1 pcrecomp — nothing OpenAL to adapt

Local checkout at `/Users/haseeb-mir/Documents/Code/pcrecomp`, HEAD
`a85ea970f9e0640ab6546d2ca7a01fccb132973d` (the same pin recorded in
`docs/UPSTREAM_CODE_PROVENANCE.md`). Its `runtime/win32hle/` contains
`dsound.c` and `winmm.c` and **no OpenAL file**:

- `pcrecomp/runtime/win32hle/dsound.c` is **byte-identical** to this
  repository's `runtime/win32hle/dsound.c` (`diff` produces no output).
- `pcrecomp/runtime/win32hle/winmm.c` differs only by local GameReComp deltas
  already documented elsewhere: routing `timeGetTime` through `hle_clock_ms`,
  and adding `mciSendCommandA` / `mixerGetDevCapsA`.
- `sdlmix.c` is a GameReComp addition, not upstream.

**Conclusion:** there is no upstream OpenAL implementation to cherry-pick. What
is reusable is the *pattern*: a `win32hle_shim[]` table, `dll#N` aliases for
ordinal imports, `win32hle_register()` in `host.c`, and the
`hle_audio_stream_*` host bridge in `dsound.c`.

### 4.2 SDL — already the host backend; do not vendor it

- The runtime links **SDL2** (`#include <SDL2/SDL.h>`), zlib licence, listed as
  a required runtime dependency in `docs/LICENSES.md`. `docs/UPSTREAM_CODE_PROVENANCE.md`
  records SDL 2.32.74 as the inspected host audio/window backend.
- A local SDL checkout exists at
  `/Users/haseeb-mir/Documents/Code/Cpp/c-plus-library-upstreams/SDL`, HEAD
  `313dd44068a19a42e008fe8fdd3b9cf5596531eb` (2026-11-20), but it is **SDL
  3.5.0** (`include/SDL3/SDL_version.h`), a different major API from the SDL2
  the runtime compiles against. Its `src/audio/` tree (`SDL_audio.c`,
  `SDL_mixer.c`, `dummy/SDL_dummyaudio.c`, ALSA/WASAPI/PulseAudio backends) is
  a useful reference for how a portable device abstraction is shaped, but it
  is not source to copy into this runtime.
- SDL has **no OpenAL implementation**. "Reusing SDL for audio" here means
  continuing to use the already-linked SDL2 audio device through `dsound.c`.

### 4.3 openal-soft — real implementation, wrong tool for a minimal shim

The DLL TMNF ships is OpenAL Soft, LGPL-2.0 (`openal-soft/COPYING`, LGPL v2).
It is a complete spatial-audio engine with its own device enumeration, EFX,
capture and mixer. Porting or vendoring it would:

- bring LGPL-2.0 obligations into an AGPL-3.0-only repository,
- add a large third-party C++ codebase to a runtime whose rule is "no new
  audio backend where existing code exists", and
- still not solve the Win32HLE-specific problems (guest memory, cdecl shim
  ABI, `hle_call_guest`, virtual-time replay).

It remains the **behavioural reference** for OpenAL semantics (distance model,
buffer-queue streaming, extension answers), not a source donor.

### 4.4 OpenIGI OpenAL backend — semantic reference only

`/Users/haseeb-mir/Documents/Code/open-igi/src/OpenIGI.Audio.OpenAL/` contains
a C# backend using `Silk.NET.OpenAL` / `Silk.NET.OpenAL.Soft.Native`. It is
useful evidence for how a game-facing OpenAL device should behave (linear
distance model matching the original's fade, ~1/16 s stream buffers, silence
when no device can be opened). It is **not adaptable**: different language and
runtime from the C Win32HLE shims, the surrounding project is GPL-3.0, and the
Silk.NET native dependency is LGPL OpenAL Soft again.

## 5. Licence position

| Component | Licence | Consequence for a shim |
|---|---|---|
| This repository (`LICENSE`) | AGPL-3.0-only (prior MIT notice in `LICENSES/GameReComp-prior-MIT.txt`) | New `src/`/`runtime/` code is authored here and distributed under the repo licence. |
| pcrecomp `runtime/win32hle/*` | MIT, Copyright (c) 2026 sp00nz | Pattern and any verbatim baseline code may be reused with notice; no OpenAL code exists upstream. |
| SDL2 (linked) / SDL3 (local checkout) | zlib (`docs/LICENSES.md`, `SDL/LICENSE.txt`) | Safe to keep using as host audio; do not vendor SDL sources. |
| openal-soft / bundled `OpenAL32.dll` | LGPL-2.0 (`OpenAL32.COPYING.txt`, upstream `COPYING`) | Do **not** copy or adapt its source into the runtime. Behavioural reference only. |
| OpenIGI OpenAL backend | GPL-3.0 project + MIT Silk.NET + LGPL native | Not portable to the C HLE; no code copied. |
| TMNF binaries/audio assets | Third-party, rights retained by owner | Evidence only; never committed or required by the build. |

## 6. Recommendation

### Adapt — yes

**Write a new, independently authored `runtime/win32hle/openal.c` that adapts
this repository's own shim pattern and the existing SDL2 audio bridge.** In
detail:

1. Add `const win32hle_shim win32hle_openal[]` implementing **exactly the 37
   names in §2.1**, declared in `win32hle.h` and registered in
   `host.c:recomp_host_init`. Because the imports are all named, no `dll#N`
   aliases are needed (but adding `OpenAL32.dll#N` aliases is harmless).
2. Use **cdecl** shims (`RETC`/`RETP`), unlike DirectSound/WinMM.
3. Implement the real path:
   - device/context: one device/context, `alcMakeContextCurrent` to a valid
     guest context handle, `alcGetString`/`alcGetIntegerv` with truthful
     default-device and version strings in guest memory;
   - buffers: `alGenBuffers`/`alDeleteBuffers`/`alBufferData` with PCM stored
     in `hle_alloc` guest memory behind guest-facing unsigned handles;
   - sources: `alGenSources`/`alDeleteSources`, `alSource(i/f/3f/3i)` mapping
     gain and position/pan into the mixer's gain model, `alSourcePlay`/
     `alSourceStop`, `alGetSourcei` for queue state;
   - **streaming**: `alSourceQueueBuffers`/`alSourceUnqueueBuffers` backed by
     `hle_audio_stream_open/push/queued/close` from `dsound.c`, so TMNF's
     continuous audio uses the one SDL2 device;
   - `alDistanceModel`/`alDopplerFactor` accepted as state (the linear model
     matches the original's fade — see §4.4), even if spatialisation is first
     approximated as gain/pan;
   - `alGetError`/`alcGetError` return `AL_NO_ERROR`/`ALC_NO_ERROR` only for
     calls the shim actually performed; invalid handles return the real error.
4. Answer capability probes honestly: `alIsExtensionPresent`,
   `alcIsExtensionPresent`, `alGetEnumValue` and `alGetProcAddress` return
   false/0 for anything not implemented; `alGetString(AL_VERSION)` and the
   launcher's three calls report a coherent OpenAL 1.1 surface.
5. Fail truthfully for capture: `alcCaptureOpenDevice` returns `NULL`,
   `alcCaptureSamples`/`Start`/`Stop`/`Close` report an invalid device. This
   matches the repo's "no fake success" rule and TMNF can run without capture.
6. `alcProcessContext` can be a success no-op for the current context (the
   shim never suspends it).

### Do not adapt

- **Do not port openal-soft** (LGPL-2.0, large, wrong architecture) and do not
  link its DLL: a forwarded/loaded proprietary-runtime dependency violates the
  "no Wine/no original DLL" runtime rule in `docs/BACKENDS.md` and would not be
  portable.
- **Do not vendor SDL3 audio sources** into the runtime; keep consuming SDL2
  through the existing shims. The local SDL3 checkout is a different major API.
- **Do not copy the OpenIGI C# OpenAL backend** (language/runtime/licence
  mismatch). Its design notes are evidence, not source.
- **Do not extend `dsound.c`, `sdlmix.c` or `winmm.c`** with OpenAL entry
  points: TMNF imports none of those DLLs, and mixing unrelated DLL ABIs into
  one shim table would blur the registry's name-based dispatch.

### Verification before claiming anything works

1. Re-run `python3 tools/pe/extract_imports.py` against the target binary and
   diff the OpenAL32.dll name list against `win32hle_openal[]` (37 names) —
   the selftest should assert the table covers them.
2. Extend the headless selftest pattern in `runtime/win32hle/*_selftest.c` with
   a synthetic guest that opens a device, queues/unqueues buffers and plays a
   source; assert real queued-frame movement through the SDL2 dummy driver.
3. Run `python3 tools/format_cpp.py --check`, the CMake build and `ctest` per
   `AGENTS.md`, then the live game workflow with user-supplied paths. A build
   or emulator result alone is not live-game success.

## 7. Evidence, hashes and caveats

| File (user-supplied, not in repo) | SHA-256 |
|---|---|
| `TmForever.exe` | `5e287e79a07cccc9f58989120fda2668d5da1086321033f162492af7c2671672` |
| `1TmForeverLauncher.exe` | `d9b35fc92bffde6c9bd2657238cc56f562160d564f20be5d3e31a31d6c2b268f` |
| `OpenAL32.dll` (OpenAL Soft, LGPL-2.0) | `606cf8b4c22c7ae00813585279ac045115907bc66d1483eb26355907021ea4b0` |

Caveats:

- Imports are **not calls**. The list above is exactly what the PE import table
  binds; a runtime trace is still needed to learn which entry points TMNF
  actually exercises in a given session and with which arguments.
- The measurement is from one installed build (hashes above). Other
  distributions/patches of TMNF may import a different subset; re-run the
  tool per binary before finalising a shim table.
- No game code, binary, DLL or asset was added to the repository by this task.
