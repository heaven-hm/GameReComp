# Upstream provenance — consolidated

This document consolidates every upstream repository, commit/SHA, licence,
upstream source path, destination file and adaptation decision recorded in:

- `docs/UPSTREAM_CODE_PROVENANCE.md` (abbreviated `PROVENANCE` below), and
- `docs/UPSTREAM_REUSE_AGENT*.md` (abbreviated `A26` … `A35` below).

It is a documentation-only roll-up: no existing provenance document is changed,
and no source file is modified. Where the same change is recorded in more than
one source document, the rows are merged and both sources are cited.

Unless stated otherwise, every agent document inspected the **same local
PCRecomp checkout at HEAD `a85ea970f9e0640ab6546d2ca7a01fccb132973d`**
(`https://github.com/sp00nznet/pcrecomp`, MIT, Copyright (c) 2026 sp00nz).

## 1. Reused and adapted code

| # | Component / area | Upstream repo | SHA / commit / version | Licence | Upstream source path | Destination file(s) | Adaptation decision | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | PE32 loader, guest memory, shim ABI, runtime registration baseline | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT, Copyright (c) 2026 sp00nz | `runtime/win32hle/*` | `runtime/win32hle/*` | Baseline inherited; individual fixes applied in place, no parallel implementation | PROVENANCE |
| 2 | D3D7 FVF stride table | `https://github.com/HenryKun55/igi-recomp` | `278eaf6394f7f42c7de6cffef2cf982154f59899` | MIT, Copyright (c) 2026 HenryKun55 | `shim/src/d3d7.c` | `runtime/win32hle/d3d7.c` (branch `feature/upstream-issue-2`, commit `46dc1ce`) | `fvf_stride` adapted to `d3d7_fvf_stride`; added the missing `D3DFVF_PSIZE` term | PROVENANCE |
| 3 | CRT new-handler semantics | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/msvcrt.c` | `runtime/win32hle/msvcrt.c` (branch `feature/agent-31-msvcrt`) | `_callnewh` stub replaced with real handler dispatch; new shims added | PROVENANCE |
| 4 | High-level adapter pointer return | `https://github.com/sp00nznet/pcrecomp` + `https://github.com/revng/revng` | PCRecomp HEAD as above; rev.ng `0e2b25952231590f384cf6f77867db0b3d39b712` | MIT / rev.ng licence (reviewed) | existing PCRecomp bridge; `tools/autorecomp/revng.py` | `tools/autorecomp/highlevel.py` (branch `feature/upstream-issue-1`, commit `808863ba`) | `RETP` used for pointer returns; existing rev.ng provider wrapper reused | PROVENANCE |
| 5 | Guest SEH (structured exception handling) | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT, Copyright (c) 2026 sp00nz | `runtime/win32hle/eh32.c`, `kernel32_crt.c`, `host_lite.c`, `recomp32/recomp_types.h`, `runtime/native32/eh32.c` | new `runtime/win32hle/seh32.c`, edits to `kernel32_crt.c` / `host.c`, new `runtime/win32hle/seh32_selftest.c` | Portable C++ EH chain walking reused as the model; guest `EXCEPTION_RECORD`/`CONTEXT` built in guest memory, `fs:[0]` chain validated against TIB stack bounds, handlers called through `hle_call_guest`; only SIGSEGV/SIGBUS/SIGFPE/SIGILL mapped from host signals | A26 |
| 6 | Win32 threading and synchronization | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT, Copyright (c) 2026 sp00nz | `runtime/win32hle/kernel32.c`, `kernel32_crt.c`, `host_lite.c` | same files, plus new `runtime/win32hle/threadsync_selftest.c` | `hle_thread_create`, handle table, `hle_wait`, TIB/TLS reused unchanged; fixed wait-all consumption, semaphore bounds, wait-count validation, thread priority round-trip and `TlsFree` semantics | A27 |
| 7 | User32 window management and message loop | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/user32.c`, `screen.c` | `runtime/win32hle/user32.c`, new `runtime/win32hle/user32_msg_selftest.c` | Reused class/window state, message queue, `hle_send`/`hle_post_message`, focus/capture and SDL presentation; added Unicode shims `RegisterClassExW`/`GetMessageW`/`PostMessageW`/`SendMessageW` in the existing single registry | A28 |
| 8 | GDI and basic 2D graphics | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/gdidc.c`, `screen.c` | `runtime/win32hle/gdidc.c`, extended `runtime/win32hle/gdi_selftest.c` | Reused DC/bitmap model, `hle_gdi_make_bitmap`/`hle_gdi_make_dib`, blit/clip/text paths and SDL surface bridge; fixed `CreateBitmap` parameter validation and 1/4/24-bpp allocation/pitch | A29 |
| 9 | COM and interface infrastructure | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/ole32.c`, plus established vtable layouts in `ddraw.c`, `d3d7.c`, `dsound.c`, `dinput.c`, `storage.c` | `runtime/win32hle/ole32.c`, `runtime/win32hle/dsound.c`, new `runtime/win32hle/com_selftest.c` | Reused COM vtable builder, object allocator and IUnknown shims; added shared IID identity side table (`hle_com_iid_equal`/`bind`/`unbind`) and bound public DirectSound IIDs; guest object layout and existing vtable slots untouched | A30, PROVENANCE |
| 10 | PE32 resource handling / directory model | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/module.c`, `tools/pe/` parsers (layout reference) | `runtime/win32hle/module.c` (branch `feature/agent-32-resources`), new `runtime/win32hle/resource_selftest.c` | Audited and fixed the existing three-level resource tree in place; added bounds checks against `rsrc_size`/image size, reject zero/out-of-image RVA and oversized data entries; no second loader | A32, PROVENANCE |
| 11 | Win32 timers and waitable objects | `https://github.com/sp00nznet/pcrecomp` (branch based on `feature/agent-27-threads` @ `7130d76`) | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/kernel32.c` (event, mutex, semaphore, wait path) | `runtime/win32hle/kernel32.c`, new `runtime/win32hle/timer33_selftest.c` | Reused the `HLE_*` handle table and Agent 27's `try_take()`/`hle_wait()` path; added `HLE_H_TIMER`/`htimer` and timer APIs, manual/auto-reset and periodic re-arm; APCs and `WAIT_IO_COMPLETION` explicitly refused, no second wait manager | A33 |
| 12 | Process and environment APIs | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/kernel32.c`, `kernel32_crt.c`, `module.c` | same files, new `runtime/win32hle/process34_selftest.c` | Audited and extended in place: added `GetVersionExW` (Windows 5.1.2600 values + UTF-16 CSD version), fixed `SetEnvironmentVariableW` using `sizeof g_env` → `ENV_SIZE`; guest termination stays inside the rebuilt process via `hle_exit` | A34 |
| 13 | PE32 validation and malformed-input fuzzing | `https://github.com/sp00nznet/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | MIT | `runtime/win32hle/pe_loader.c` | `runtime/win32hle/pe_loader.c` (branch `feature/agent-35-pe-validation`), new `runtime/win32hle/pe_fuzz_harness.c`, new `tools/pe/fuzz_pe32.py` | Hardened the existing loader in place: DOS/PE32/COFF header, optional-header and data-directory, section count/bounds/VA/raw-extent, overlap and 64-bit RVA+size checks; fixed `flen - PointerToRawData` underflow; no second parser, PE32+ deferred | A35, PROVENANCE |

## 2. Inspected but not copied (semantic references only)

No code from these sources was transplanted into the repository.

| Upstream repo | SHA / version | Licence | Path / artefact inspected | Purpose | Reason no code copied | Source |
|---|---|---|---|---|---|---|
| `https://github.com/wine-mirror/wine` | master | LGPL | `dlls/ntdll/exception.c` (A26), `dlls/ntdll/sync.c` (A27); Win32 message/window, COM, CRT, timer, resource and environment semantics elsewhere | Win32 ABI, struct layouts and API disposition semantics | LGPL licence is incompatible with copying into the runtime; implementations independently written against the published Win32 ABI | PROVENANCE, A26–A35 |
| `https://github.com/libsdl-org/SDL` | 2.32.74 (local) | zlib | host backend via `screen.c` / `sdl12.c` | native window/event/audio host backend (Cocoa/Metal on Apple Silicon) | Already used as the existing host backend; no new backend introduced | PROVENANCE, A28, A29 |
| `https://github.com/microsoft/DirectX-Headers` | master | Microsoft (per upstream repo; not specified in sources) | public IID/GUID/vtable layouts, `IID_IDirectSound`, `IID_IDirectSoundBuffer` | public COM interface identifiers and vtable layout reference | Headers only; no runtime code needed | PROVENANCE, A30 |
| `https://github.com/revng/revng` | `0e2b25952231590f384cf6f77867db0b3d39b712` | rev.ng licence (reviewed) | `tools/autorecomp/revng.py` (existing wrapper) | provider discovery and candidate generation | Existing wrapper reused; no replacement provider added | PROVENANCE |
| `https://github.com/swampodsarkar321/IGI-WEB` | `0a7472644f77e7287b3e478a06c47a47beae1e7e` | MIT | D3D8 public ABI and replay design | D3D8 ABI / host design reference | WebGPU/DOM/WASM host code not portable; independent COM frontend written instead | PROVENANCE |
| LLVM `llvm/lib/Object` | master | Apache-2.0 WITH LLVM-exception | bounds-first validation strategy | PE validation strategy reference | Reference only; validation added to the existing loader, no LLVM code copied | PROVENANCE, A35 |
| LIEF | master | MIT | PE directory validation ideas | PE validation strategy reference | Reference only; no LIEF code copied | PROVENANCE, A35 |

## 3. Discarded / negative result

| Upstream repo | SHA / commit | Licence | Upstream source path | Destination attempted | Decision | Source |
|---|---|---|---|---|---|---|
| `https://github.com/HenryKun55/igi-recomp` | `278eaf6394f7f42c7de6cffef2cf982154f59899` (short `278eaf63…`) | MIT, Copyright (c) 2026 HenryKun55 | `shim/src/ddraw.c:front_buffer_show` → `render_blit` + `render_present`; `shim/src/render_gl.c:render_present` | experimental `ddraw.c:mark()` + new `hle_screen_present_primary_gl()` helper (GameReComp) | **Discarded — not committed.** Adapted into GameReComp and run with a real Cocoa GL context; both captured frames stayed 100% black (PIL distinct-colour count 1, extrema `(0,0)-(0,0)`), identical to the unmodified build. Not a fix; blocker remains open in `test-results/game-igi/` | PROVENANCE |

## 4. Notes and gaps in the source set

- The single PCRecomp HEAD `a85ea970f9e0640ab6546d2ca7a01fccb132973d` is the
  common baseline for all reused PCRecomp code above; per-change branch names
  and commits are retained where the sources recorded them.
- **No `docs/UPSTREAM_REUSE_AGENT31.md` exists** in the repository. The
  `feature/agent-31-msvcrt` CRT new-handler change is therefore represented only
  by row 3, sourced from `docs/UPSTREAM_CODE_PROVENANCE.md`.
- The `UPSTREAM_REUSE_AGENT*` documents live on their respective feature
  branches (`feature/agent-26-seh` … `feature/agent-35-pe-validation`) rather
  than on the current branch; they were read from those branches' git objects.
- Related `docs/UPSTREAM_REUSE_ISSUE*.md` documents also exist on `feature/upstream-issue-*`
  branches but are outside the stated source set for this roll-up. The
  issue-based changes are already summarised in `docs/UPSTREAM_CODE_PROVENANCE.md`
  (rows 2 and 4 above).
- Rules recorded in `docs/UPSTREAM_CODE_PROVENANCE.md` still apply: no new PE
  loader, graphics/audio/input backend, CRT, COM implementation or decompiler
  where upstream/repository code existed; no platform-specific
  Vita/Android/WebGPU code transplanted; PE32+ and Win16/NE deferred; no game
  binaries, proprietary assets or copyrighted data committed.
