# Agent B — Win32 COM/storage/control provenance

This branch adds five genuinely unregistered imports from
`docs/WIN32_API_COVERAGE.md` (the HeroCore gm7 / GameMaker 7 cluster):

| API | DLL | Implemented in |
|---|---|---|
| `CreateBindCtx` | ole32.dll | `runtime/win32hle/ole32.c` |
| `CreateILockBytesOnHGlobal` | ole32.dll | `runtime/win32hle/ole32.c` |
| `OleGetIconOfClass` | ole32.dll | `runtime/win32hle/ole32.c` |
| `StgCreateDocfileOnILockBytes` | ole32.dll | `runtime/win32hle/storage.c` |
| `ImageList_Replace` | comctl32.dll | `runtime/win32hle/advapi32.c` |

No source from Wine, ReactOS or any other project was copied verbatim. The
implementations are original C for win32hle's guest-ABI shim model (guest
stack arguments via `A32`/`RET`, objects in guest memory, method dispatch via
guest vtables). What was taken from the sources below is *behaviour*:
argument validation, default values, vtable method order, HRESULTs and
failure modes. Each inherited behaviour is noted in a comment at the
implementation site.

## Wine (LGPL-2.1-or-later)

Wine is distributed under the GNU Lesser General Public License, version 2.1
or (at your option) any later version. License text:
<https://raw.githubusercontent.com/wine-mirror/wine/master/COPYING.LIB>.
The mirror below is the canonical Wine source mirror on GitHub (the
winehq.org GitLab raw endpoint is bot-protected).

- `CreateBindCtx`, `BindCtxImpl_QueryInterface`, `BindCtxImpl_SetBindOptions`,
  `BindCtxImpl_GetBindOptions`, `BindCtxImpl_RegisterObjectParam`,
  `BindCtxImpl_GetObjectParam`, `BindCtxImpl_EnumObjectParam`, and the
  `IBindCtxVtbl` method order:
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/ole32/bindctx.c>
  - E_INVALIDARG for a NULL `ppbc` or non-zero `reserved`; `*ppbc = NULL` on
    entry; BIND_OPTS3 defaults (`cbStruct`, `grfMode = STGM_READWRITE`,
    `dwClassContext`, locale); `SetBindOptions` rejects `cbStruct > sizeof`;
    `GetBindOptions` copies `min(cbStruct, sizeof)`; missing key is E_FAIL;
    `EnumObjectParam` sets the out pointer to NULL and returns E_NOTIMPL.
  Source file header: "Copyright 1999 Noomen Hamza", LGPL-2.1-or-later.

- `StgCreateDocfileOnILockBytes`:
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/ole32/storage32.c>
  - `STG_E_INVALIDPOINTER` for a NULL `plkbyt` or `ppstgOpen`; the byte array
    becomes the backing store of an `IStorage` (the same storage construction
    used by `StgCreateDocfile`).
  Source file header: "Copyright 1999 Francis Beaudet / Sylvain St-Germain /
  Thuy Nguyen, Copyright 2005 Mike McCormack", LGPL-2.1-or-later.

- `OleGetIconOfClass`:
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/ole32/ole2stubs.c>
  - headless implementation returns NULL ("no icon"); this is also the
    Windows documented failure answer.

- `ILockBytes` vtable method order (ReadAt, WriteAt, Flush, SetSize,
  LockRegion, UnlockRegion, Stat) and the practice of forwarding compound-file
  I/O through `ILockBytes_*`:
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/ole32/filelockbytes.c>
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/ole32/storage32.c>
  - LGPL-2.1-or-later.

## ReactOS (LGPL-2.1-or-later for these files)

- `bindctx.c` is the LGPL-licensed Wine implementation carried in ReactOS:
  <https://raw.githubusercontent.com/reactos/reactos/master/dll/win32/ole32/bindctx.c>
- `OleGetIconOfClass` in `ole2stubs.c`:
  <https://raw.githubusercontent.com/reactos/reactos/master/dll/win32/ole32/ole2stubs.c>
  - returns NULL.
- `ImageList_Replace`:
  <https://raw.githubusercontent.com/reactos/reactos/master/dll/win32/comctl32/imagelist.c>
  - `FALSE` for an invalid HIMAGELIST, for `i < 0 || i >= cMaxImage`, and when
    the image bitmap cannot be read; `TRUE` on a successful replacement. The
    win32hle image lists track geometry/count only, so the bitmap handle is
    checked for non-NULL but no pixels are copied — a deliberate, documented
    reduction, not a fabricated success for an invalid handle.

## Microsoft API documentation (documents only, not source code)

- `CreateBindCtx`:
  <https://learn.microsoft.com/en-us/windows/win32/api/objbase/nf-objbase-createbindctx>
  - `reserved` must be 0; default BIND_OPTS (`cbStruct = sizeof(BIND_OPTS)`,
    `grfFlags = 0`, `grfMode = STGM_READWRITE`, `dwTickCountDeadline = 0`);
    a bind context keeps bound objects alive until released.
- `CreateILockBytesOnHGlobal`:
  <https://learn.microsoft.com/en-us/windows/win32/api/coml2api/nf-coml2api-createilockbytesonhglobal>
  - `hGlobal == NULL` allocates a new handle; a supplied handle is used in
    place and its contents preserved; `fDeleteOnRelease` controls ownership;
    the byte array grows with `GlobalReAlloc` and does **not** support region
    locking; initial size is `GlobalSize(hGlobal)`.
- `StgCreateDocfileOnILockBytes`:
  <https://learn.microsoft.com/en-us/windows/win32/api/coml2api/nf-coml2api-stgcreatedocfileonilockbytes>
  - `reserved` must be zero; the byte array replaces the disk file as the
    storage's backing store; STGM access/sharing rules apply.
- `ILockBytes`:
  <https://learn.microsoft.com/en-us/windows/win32/api/objidl/nn-objidl-ilockbytes>
  - method set and ULARGE_INTEGER by-value argument order.
- `STATSTG` layout:
  <https://learn.microsoft.com/en-us/windows/win32/api/objidl/ns-objidl-statstg>
  - `type`, `cbSize`, `grfMode`, `grfLocksSupported` offsets used by
    `ILockBytes::Stat`.
- `ImageList_Replace`:
  <https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-imagelist_replace>
  - returns nonzero on success, zero otherwise; `hbmMask` may be NULL.

## HRESULT/error constants

Values checked against Wine's `include/winerror.h`:
<https://raw.githubusercontent.com/wine-mirror/wine/master/include/winerror.h>
(STG_E_INVALIDFUNCTION `0x80030001`, STG_E_INVALIDPOINTER `0x80030009`,
STG_E_MEDIUMFULL `0x80030070`, MK_E_NOTBOUND `0x800401E9`).

## Verification

Guest-ABI selftests built against the **whole** HLE core (all non-selftest
`*.c` files), following `runtime/win32hle/build_selftests.sh`:

- `runtime/win32hle/ole32_extra_selftest.c` — IBindCtx, ILockBytes and
  OleGetIconOfClass behaviour, including refcounts and method HRESULTs.
- `runtime/win32hle/storage_lockbytes_selftest.c` — end-to-end CFB write/read
  through an ILockBytes (signature and stream payload verified in the byte
  array), refcount hand-off.
- `runtime/win32hle/imagelist_replace_selftest.c` — success and documented
  failure answers.

A registration audit with `python3 -m tools.api_coverage --corpus
/Users/haseeb-mir/Downloads/recomp-apps/games` on this branch reports all five
names registered: `distinct_missing_api` drops from 13 (baseline
`docs/WIN32_API_COVERAGE.md`) to 8, and none of the five names appears in the
missing list. Registration only proves a name is present; the selftests above
are the behaviour evidence.

These are HLE-level tests, not live-game runs; no Windows/Wine game-execution
claim is made here.
