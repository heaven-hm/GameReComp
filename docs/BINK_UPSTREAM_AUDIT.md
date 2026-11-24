# Bink / upstream audit — TrackMania `binkw32.dll`

**Date:** 2026-11-23 · **Branch:** `feature/p10-bink-audit` · **Scope:** documentation only.
This note audits what the runtime already has for Bink movie playback, where it
diverges from upstream and from a real `binkw32.dll`, and what evidence is still
missing. It changes no source. Every claim below is either a direct file/line
reference in this tree or an explicit "to verify" item.

## 1. Evidence basis and limits

- Given by the parent task: **both TrackMania binaries import `binkw32.dll`**.
  This was **not independently verified** here: no Windows TrackMania PE32 is
  present in this workspace. `docs/GAME_MATRIX.md:220–222` marks rows 22–24
  (Nations ESWC, Nations Forever, United Forever) `NOT TESTED` / `BLOCKED:
  no Windows game files; only a native macOS app present`, and
  `docs/GAME_ACCEPTANCE_TRACKER.md` has no TrackMania row at all. The filenames
  (`TmForever.exe`, `TmUnitedForever.exe`, …) are therefore **not asserted**.
- Consequently this is a **static source audit**. Nothing here is a live-game,
  emulator or build success claim for TrackMania.
- The source of truth for this audit is the local checkout:
  `runtime/win32hle/bink.c`, `runtime/win32hle/mss.c`,
  `runtime/win32hle/avifil32.c`, the FFmpeg bridge in
  `tools/autorecomp/projectgen.py`, and the build/test wiring in
  `runtime/win32hle/build_selftests.sh`.
- Host tooling observed (for reproducibility, not a compatibility guarantee):
  `ffmpeg 9.0.2`, configured `--enable-shared --enable-gpl --enable-version3
  --enable-libsvtav1 --enable-libopus --enable-libx264 --enable-libmp3lame
  --enable-libdav1d --enable-libvmaf --enable-libvpx --enable-libx265
  --enable-openssl …`; `pkg-config` reports libavformat/libavcodec
  `63.1.102`, libavutil `61.1.102`, libswresample `7.1.102`. The host has the
  `bink` demuxer, `binkvideo`, `binkaudio_dct`, `binkaudio_rdft` decoders and
  Indeo 2/3/4/5 decoders. **There is no Bink muxer** (`ffmpeg -muxers`), so a
  `.bik` test fixture cannot be generated with the ffmpeg CLI.

## 2. Exact upstream references

### 2.1 pcrecomp

| Item | Exact value |
|---|---|
| Upstream | `https://github.com/sp00nznet/pcrecomp` |
| Pinned local HEAD | **`a85ea970f9e0640ab6546d2ca7a01fccb132973d`** (`a85ea97`, "changelog: win32hle additions for eJay 2 (#135, #140–#143) (#144)") |
| Local checkouts checked | `/Users/haseeb-mir/Documents/Code/pcrecomp` and `/Users/haseeb-mir/Downloads/recomp-research/pcrecomp`, both at that HEAD |
| Upstream paths inspected | `runtime/win32hle/bink.c`, `runtime/win32hle/mss.c` |
| Parity check | `diff -u` against this tree's `bink.c` and `mss.c` is **empty**: both are byte-identical to upstream at that SHA. `avifil32.c` has no counterpart upstream. |
| Licence | MIT, Copyright (c) 2026 sp00nz |
| Repository record | `docs/UPSTREAM_CODE_PROVENANCE.md:11` (loader/shims row), `docs/UPSTREAM_TOOLBOX.md:211–219` and `:596–599`, `CHANGELOG.md:417–425` (upstream's own Bink changelog entry) |

`avifil32.c` is a local rewrite, not pcrecomp code. Its own header
(`runtime/win32hle/avifil32.c:15–17`) records that IGI-recomp
(`https://github.com/HenryKun55/igi-recomp`, commit
`278eaf6394f7f42c7de6cffef2cf982154f59899`, MIT) was consulted for the AVI
format only. Keep that attribution if the file is edited.

### 2.2 FFmpeg bridge and licence notes

- Build wiring: `tools/autorecomp/projectgen.py:125–127` generates
  `pkg_check_modules(FFMPEG IMPORTED_TARGET libavformat libavcodec libavutil
  libswresample)`; `:180–185` defines `HLE_WITH_FFMPEG` and links
  `PkgConfig::FFMPEG` when found, otherwise emits
  `WARNING "FFmpeg not found: Bink/MSS movie and audio decoding disabled"`;
  `:205–207` documents FFmpeg as optional in the generated project README.
- Consumers: `runtime/win32hle/bink.c:60–63` (libavformat/libavcodec/imgutils/
  swresample), `runtime/win32hle/mss.c:54–57` (MP3 ASI decoder),
  `runtime/win32hle/avifil32.c:309–310` (libavcodec/imgutils for AVI frames).
  This is the whole bridge — no other FFmpeg code exists in the tree.
- Licence disclosure: `docs/LICENSES.md:24` states "LGPL-2.1-or-later for a
  default build; a GPL-configured FFmpeg makes the linked binary GPL", and
  `:26–28` leaves the resulting licence to the user's FFmpeg build.
  `docs/RUNTIME_COMPAT.md:17` records FFmpeg as the optional enabler for Bink
  and Miles decoding.
- **Actual environment is the GPL case**: `reports/audit-2026-10-30.md:613–617`
  already records that the linked FFmpeg is GPL-configured and that the
  delivered binary inherits GPL obligations. The host `ffmpeg -version` output
  in §1 confirms `--enable-gpl --enable-version3` plus x264/x265/vpx/openssl.
  Treat any generated binary built with `HLE_WITH_FFMPEG` as GPL-encumbered
  (governed by the distributor's build); the repository itself is
  AGPL-3.0-only (`docs/LICENSES.md:3–7`). Any distribution decision needs a
  real licence review, not just the conditional sentence in `docs/LICENSES.md`.
- Note that linkage is **all-or-nothing**: `projectgen.py:180–182` links FFmpeg
  into every generated project when present, including games that never import
  Bink. If licence exposure is to be minimised, make FFmpeg opt-in per project
  rather than auto-detected. (Recommendation only; no code changed.)

## 3. What exists today (source map)

| Concern | Path / lines | Behaviour |
|---|---|---|
| Bink exports, no FFmpeg | `runtime/win32hle/bink.c:46–58` | 13 stdcall shims; `BinkOpen` always returns 0 with `g_error = "no movie decoder on this host"` (`:47`); the rest are `k_ret0_*` stubs. |
| Bink exports, with FFmpeg | `runtime/win32hle/bink.c:325–335` | 21 shims, including 8 `BinkBuffer*` / `BinkGetRects` / `BinkSetIOSize` entries **absent from the stub table**. |
| Open / demux / decode setup | `bink.c:138–196` | `BINKFILEHANDLE` or path; forced `av_find_input_format("bink")`; best video/audio streams; Bink video decoder; audio decoder + swresample + `hle_audio_stream_open`. |
| Frame loop | `bink.c:100–135`, `:199–209` | `decode_frame()` pumps packets, forwards audio to the mixer; `BinkDoFrame`, `BinkNextFrame`. |
| Timing and control | `bink.c:211–242` | `BinkWait`, `BinkPause`, `BinkSetVolume`, `BinkGoto`. |
| Pixel conversion | `bink.c:246–267` | Hand-rolled YUV420P → 565/555/32 DIB blit for `BINKSURFACE` types 10/9/3/5. |
| GDI buffer path | `bink.c:269–323` | `BinkBufferOpen/Close/Lock/Unlock/SetOffset/Blit`, `BinkGetRects`, `BinkSetIOSize`. |
| Miles MP3 provider | `runtime/win32hle/mss.c:50–52`, `:150–174`, `:231–259` | RIB lookup matches only `ASI codec` + `.mp3`; ASI streams are forced through `av_find_input_format("mp3")`. |
| AVI / decompressor | `runtime/win32hle/avifil32.c:76–154`, `:298–307`, `:324–389`, `:423–487` | RIFF parser; unsupported-codec fallback returns `ICLocate = 0` / `ICERR_UNSUPPORTED`; fourcc map covers Indeo 3/4/5 only. |
| Host mixer stream API | `runtime/win32hle/dsound.c:80–118` | `hle_audio_stream_open/push/queued/gain/close`; `queued()` exists but `bink.c` never calls it. |
| Import binding | `runtime/win32hle/host.c:372`, `runtime/win32hle/pe_loader.c:145–178`, `runtime/win32hle/host_lite.c:444–466` | Strict boot exits 2 on any unresolved import; permissive mode binds a shim that **fatals** with the exact import name when called. A missing Bink export is a hard failure, not a graceful "no movie". |
| Selftests | `runtime/win32hle/build_selftests.sh:11–56` | Builds every `*_selftest.c` against all non-selftest `*.c` files (`CORE` in the script) with SDL2/SDL2_ttf only. No `-DHLE_WITH_FFMPEG`, no libav flags: **the FFmpeg implementations are never compiled by the selftest suite.** There is no bink/mss/avi selftest at all. |

## 4. Gap register

Severity is relative to getting TrackMania's intro/movies to play and to
honestly reporting unsupported-codec failures.

| ID | Sev | Gap (evidence) | Impact | Needed action |
|---|---|---|---|---|
| G1 | **High** | Export surface not verified against the real import tables. Absent from **both** shim tables (`bink.c:52–58`, `:325–335`) are Bink 1.x SDK entry points such as `BinkCopyToBufferRect`, `BinkOpenMiles`, `BinkSetSoundTrack` and `BinkSetSimplify` (presence in the two TrackMania imports is a hypothesis, not a finding — no PE was available; other names may surface in T1). | Strict boot fails with exit 2 on the first unresolved import; permissive mode fatals on call. If either TrackMania binary imports `BinkCopyToBufferRect` (a common choice for letterboxed playback), no movie path is reached at all. | Dump the two PE import tables (T1); implement only the imports actually present, with exact stdcall arities verified from the decorated names. |
| G2 | **High** | The no-FFmpeg shim table has **8 fewer exports** than the FFmpeg one (`bink.c:52–58` vs `:325–335`): `BinkBufferOpen/Close/Lock/Unlock/SetOffset/Blit`, `BinkGetRects`, `BinkSetIOSize`. | On a host without FFmpeg, `BinkOpen` fails *and* more imports are unresolved; strict boot fails. The documented "honest failure" is weaker than it looks. | Make both tables list the same names, with the stubs returning bounded failure values. |
| G3 | **High** | Audio failure is silent: `bink.c:170–185` leaves `b->adec == NULL`, `b->swr == NULL` or `b->stream == -1` without a log or `g_error` update, yet `k_Open` still returns a valid handle and prints `, with sound` only when a stream exists (`:193–194`). Also `hle_audio_stream_open()` can return `-1` (`dsound.c:80–83`). | Movie plays muted with `BinkGetError` empty — exactly the "unsupported-codec failure path" that must not be mistaken for success. | Log `[bink] no audio decoder / no mixer stream`, set a per-handle error string, and define whether BinkOpen should fail or continue muted (document the choice). |
| G4 | Medium | `g_error` is one global string with pointer-cached guest copy (`bink.c:29–35`); it is never cleared on a successful `BinkOpen`. | `BinkGetError` can return a stale message after a later successful open; concurrent movies overwrite each other's error. | Reset on success; store the message in `bink_t` for per-handle reporting. |
| G5 | Medium | Null/order hardening: `bink.c:166–169` calls `avcodec_alloc_context3(vc)` and `avcodec_parameters_to_context()` **before** testing `!vc`; `k_DoFrame`/`k_CopyToBuffer` (`:199–204`, `:246–251`) dereference `APTR(0)`/`b->frame` without null checks. | Low crash risk with current FFmpeg, but an unsupported codec or a bad guest handle turns into a host fault instead of a diagnostic. | Reorder the null check; validate all guest pointers; route through a single `fail()` helper. |
| G6 | Medium | `k_CopyToBuffer` handles only `AV_PIX_FMT_YUV420P` and types 10/9/3/5 (`bink.c:251–264`); other surface types write nothing but still `RET(0, 7)`. | Silent black frames when the game requests an unsupported BINKSURFACE type (e.g. 16-bit 555 vs 565 confusion, 24-bit, YUV overlays). | Log the unsupported `(format, type)` pair once; support the requested types or fail explicitly. |
| G7 | Medium | No A/V pacing or drain: `decode_frame()` pushes audio as fast as packets are read (`bink.c:122–135`); `hle_audio_stream_queued()` (`dsound.c:104–110`) is unused. `BinkPause` only changes gain (`bink.c:217–225`) — decoding keeps filling the ring. | Audio can be consumed before the video frames are presented; after unpause, buffered audio plays at once. Desync risk on fast paths. | Gate decoding on the queued-frame count, stop pushing while paused, drain/close cleanly on `BinkGoto`/`BinkClose`. |
| G8 | Medium | `BinkGoto` (`bink.c:233–242`) always seeks byte 0 with `AVSEEK_FLAG_BACKWARD`, ignores flags/timestamps, decodes forward frame-by-frame, and does not flush the audio decoder, reset the resampler or drain the mixer stream. | Seeking with audio leaves stale sound; long seeks stall the frame loop. | Use timestamp seek where possible; flush both codecs and the stream; add a seek-accuracy test. |
| G9 | Low / needs oracle | Frame numbering: `FrameNum = 1`, `LastFrameNum = 0` on open (`bink.c:191`), `NextFrame` clamps at `Frames` (`:205–209`). | May be off by one against retail `binkw32`, whose frame counters games sometimes read directly for subtitles/seek UI. **Do not change without a retail trace** — no oracle is available here. | Compare `FrameNum`/`LastFrameNum` against a real game trace before any edit. |
| G10 | Low | `BinkDDSurfaceType` (`bink.c:38–44`) returns 10 (565) for *any* 16-bit surface without inspecting RGB masks (555/ARGB1555 are possible). | Wrong blit format for some surfaces. TrackMania is D3D9-era (no D3D9 backend yet: `docs/ISSUES_AUDIT.md:34–36`), so this is not demonstrably on its path. | Read the surface pixel format; keep the fallback but log it. |
| G11 | Low | Related unsupported-codec paths: AVI maps only Indeo 3/4/5 (`avifil32.c:324–336`) and MSS matches only `.mp3` (`mss.c:157–162`, `:233–237`). | Honest failure (`ICERR_UNSUPPORTED`, provider 0, diagnostic on stderr) but narrow coverage and no tests. | Add coverage tests (T4) before widening any codec map. |
| G12 | **High (test)** | No FFmpeg-backed test coverage: `build_selftests.sh` compiles the stub branches only; no `.bik` fixture exists in-tree or upstream; ffmpeg CLI cannot mux Bink. | Regressions in the actual decode path (the one delivered builds use — `reports/audit-2026-10-30.md:304–307`) are invisible to CI. | T2–T5 below. |

## 5. Tests needed

**T1 — Import-surface check (no game binaries required to merge; skips when absent).**
Add `tests/test_bink_import_surface.py`:
- Use the existing `tools/pe/extract_imports.py` parser (or `pefile`, already
  used by `tools/drm/*` and `tools/pe/*`) to read the `binkw32.dll` import list
  of each binary path supplied via an env var / CLI argument.
- Compare every imported decorated name against the export names in
  `win32hle_bink[]` in `bink.c` (parse the table so the test fails if a shim is
  removed, rather than maintaining a second hand-written list).
- Require both the `HLE_WITH_FFMPEG` and stub tables to expose the same names.
- **Skip** with a printed reason when no PE is supplied; never commit or
  require proprietary binaries. Command shape:
  `python3 tools/pe/extract_imports.py <TmForever.exe> <TmUnitedForever.exe>`.
- This is the gate that turns G1/G2 from "suspected" into "confirmed/fixed".

**T2 — FFmpeg-enabled selftest build.**
Add `runtime/win32hle/bink_selftest.c` plus a sibling
`build_selftests_ffmpeg.sh` (or extend `build_selftests.sh` with an opt-in
variant) that adds `-DHLE_WITH_FFMPEG` and
`$(pkg-config --cflags --libs libavformat libavcodec libavutil libswresample)`.
`build_selftests.sh:11–18` currently neither defines the macro nor links those
libraries, so simply dropping in a `_selftest.c` file would still test stubs.
Guard the script with a `pkg-config --exists` check and skip cleanly otherwise.

**T3 — Bink playback selftest assertions** (all against a tiny local fixture,
`SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy`, mirroring
`runtime/win32hle/present_selftest.c`):
- `BinkOpen` on a name returns a non-zero handle; `Width`, `Height`, `Frames`,
  `FrameRate`, `FrameRateDiv` match the fixture's known values.
- `BinkDoFrame` + `BinkCopyToBuffer` into 565, 555 and 32-bit buffers produce a
  known pixel colour at a known coordinate; unsupported surface types must log,
  not silently claim success (G6).
- `BinkNextFrame`/`BinkWait` advance at the fixture's frame rate;
  `BinkPause`/`BinkSetVolume` set the mixer gain; `BinkGoto` returns to frame 1
  and produces the first frame's pixels after the seek (G8).
- `BINKFILEHANDLE` variant: open a file, seek to a non-zero offset containing
  the movie, pass the handle and assert the same first frame.
- Truncated file, non-Bink file and a renamed MP3 each make `BinkOpen` return 0
  and `BinkGetError` contain the documented string (`:151`, `:157`, `:164`,
  `:169`). Missing audio decoder must be reported (G3) on an audio-less
  fixture.

**T4 — Unsupported-codec failure-path selftests** (cheap, no Bink fixture):
- `avifil32.c`: `ICLocate` with an unknown fourcc (e.g. `ZZZZ`) returns 0 and
  `ICDecompress` returns `ICERR_UNSUPPORTED`; a known Indeo fourcc returns a
  valid HIC.
- `mss.c`: `RIB_find_file_provider("ASI codec", …, "x.ogg")` returns 0 while
  `x.mp3` returns the provider; `RIB_request_interface` fills attribute ids
  and logs unknown names.
- Stub-path parity: compile the same shim table without `HLE_WITH_FFMPEG` and
  assert `BinkOpen` fails with `"no movie decoder on this host"` and that all
  exports resolve (G2).

**T5 — Fixture strategy (avoid copyright issues).**
Preferred: a synthetic tiny `.bik` produced by a checked-in generator with
recorded provenance, or a fixture extracted at test time from a path supplied
by the user (never committed). Because ffmpeg has no Bink muxer, options are
(a) a purpose-built minimal BIK writer in `tools/` (MIT/original, added to
`NOTICE`), or (b) an external sample with an explicit redistributable licence
and a `NOTICE` entry. Do **not** commit a movie ripped from TrackMania.

**T6 — Build/CI and live acceptance.**
- Compile the runtime both with and without `HLE_WITH_FFMPEG` against the
  pinned FFmpeg versions in §1 to catch API drift (FFmpeg 5+ channel-layout /
  `swr_alloc_set_opts2` APIs are in use).
- Once legal unprotected Windows binaries exist: dump imports (T1), run the
  generated project in strict mode (`host.c:372`), capture the first movie
  frames and compare with a retail trace; record the result in
  `docs/GAME_MATRIX.md:220–222` and `docs/GAME_ACCEPTANCE_TRACKER.md`. A build,
  a selftest pass or an emulator result is **not** a live-game success claim.

## 6. Ownership and non-goals

This task owns **only `docs/BINK_UPSTREAM_AUDIT.md`**. No source, build file,
fixture, test or licence file was modified. All gaps above are proposals; the
implementation owner should pick them up with T1 first, because the export
surface determines whether any of the decode-path work is reachable at all.

## 7. Open questions for the parent

1. Which two executables are meant ("both TrackMania binaries")? Supply paths
   for T1; no filenames were invented here.
2. Should `BinkOpen` fail outright when the audio codec is unavailable, or
   continue muted with a loud diagnostic? (G3 — behaviour choice, not a bug fix.)
3. Is a redistributable Bink fixture acceptable, or should T2–T3 remain
   user-supplied-fixture-only? (T5.)
