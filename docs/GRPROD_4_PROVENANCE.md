# GR4 provenance — COM/DirectX interface audit

Base: `cb4dc0441199af0fc15d0100a565bd8e444a228e`
Branch: `feature/grprod-4-com-dx`

## Scope of the audit

Reviewed the five requested guest frontends:

| File | Reviewed outcome |
|---|---|
| `runtime/win32hle/ole32.c` | **Defect found and fixed** — guest COM class registration could not dispatch through a registered class factory |
| `runtime/win32hle/ddraw.c` | No change. `dd_GetCaps` already populates `ddsCaps` at both byte 132 and byte 364 (`DDCAPS_DX7.ddsCaps`), and `dd_CreateSurface` already parses `DDSD_*`/`DDSCAPS_*` from the public layout. |
| `runtime/win32hle/d3d7.c` | No change. Its device surface/caps paths are covered by the existing `d3d7_selftest.c` and `d3d7_extra_selftest.c`. |
| `runtime/win32hle/d3d8.c` | No change. The real `IID_IDirect3DDevice8` is accepted alongside the legacy ABI fixture IID; vtable order and stdcall purges are covered by `tests/test_d3d8.py`. |
| `runtime/win32hle/d3d9.c` | No change. `d3d9_selftest.c` pins all 17 `IDirect3D9` and 119 `IDirect3DDevice9` slots plus both IIDs. |

Only **one** frontend file was modified, plus **one new** selftest and this provenance
record. No `host.c`, header, build script or Python test was touched.

## The defect

`ole32.c`'s static `vcall()` helper calls methods on COM interface pointers that
the *guest* supplied: the class object passed to `CoRegisterClassObject`, and
the `IClassFactory` returned by its `QueryInterface`. It computed the method
address as:

```c
MEM32(obj + 4u * slot)          /* wrong */
```

A Win32 COM interface pointer points at the object, whose **dword 0 is the
vtable VA**; each vtable slot then holds the guest VA of the lifted method.
The correct address is two guest-memory reads:

```c
MEM32(MEM32(obj) + 4u * slot)   /* fixed */
```

The buggy form treats object fields as functions: for `AddRef` (slot 1) it
read the refcount at `+4` (e.g. `1`) as the function address. The result was
`[win32hle] FATAL: no lifted function at 0x00000001` on the first callback, so
`CoCreateInstance` could never serve a guest-registered class. This blocked
the in-process COM class-factory path used by titles such as Yuri's Revenge
(saved classes/locomotors), which `ole32.c` explicitly supports with
`MAX_GUEST_CLASSES 512`.

## Layout authority and provenance

- The COM interface/vtable layout used here is the **public Microsoft COM ABI**
  (IUnknown's first three slots, then `IClassFactory::CreateInstance` and
  `LockServer`). That slot order is also recorded in this repository in
  `tools/lift/name_lift.py` (`COM_SLOTS['IClassFactory'] = IUNKNOWN +
  ['CreateInstance', 'LockServer']`), which derives names from the guest PE's
  RTTI rather than from any external implementation.
- `microsoft/DirectX-Headers` is this repository's declared reference for
  public IID/GUID/vtable layouts (`docs/UPSTREAM_CODE_PROVENANCE.md`,
  `docs/UPSTREAM_PROVENANCE_CONSOLIDATED.md`). Consistent with that policy,
  this change encodes only a published ABI shape; **no DirectX-Headers source,
  and no runtime code from any upstream project, was copied**. DirectX-Headers
  is headers-only and contains no runtime implementation to copy.
- No Wine (LGPL) code was copied. The identical two-step dispatch pattern
  already exists in this repository's own `runtime/win32hle/storage_selftest.c`
  (`MEM32(MEM32(obj) + 4u * slot)`), which was used as the in-repo correctness
  reference, together with the object layout written by `hle_com_new()` in
  `ole32.c` itself (`o[0] = vtable, o[1] = 1`).
- The fix is three lines of behaviour (one changed expression) plus a comment;
  it was written from the ABI reasoning above, not transplanted from a GPL or
  LGPL source. Repo licence (AGPL-3.0) is unchanged.

## Deterministic verification

New file `runtime/win32hle/com_class_registration_selftest.c` builds a
real-shaped guest `IClassFactory` (object dword 0 = vtable VA, five vtable slots
= registered shim VAs) and drives register → `CoCreateInstance` → revoke
entirely inside the HLE, recording every guest callback, the refcount at each
stage, the `pUnkOuter`/`riid` arguments, and the returned object.

Evidence (macOS/arm64, Apple clang, `pkg-config` SDL2 + SDL2_ttf, based mode):

- With the original `vcall`, the fixture aborts on the first callback:
  `[win32hle] FATAL: no lifted function at 0x00000001`, exit **134**.
- With the fix: `com_class_registration_selftest: all checks passed (0 failures)`,
  exit **0**.

The selftest is auto-discovered by `runtime/win32hle/build_selftests.sh`
(`*_selftest.c`), so no build-file change was needed. No live game, Wine session
or original-Windows oracle was used; this is a deterministic HLE regression
fixture only.

## Files changed

- `runtime/win32hle/ole32.c` — corrected `vcall()` vtable dereference.
- `runtime/win32hle/com_class_registration_selftest.c` — new deterministic fixture.
- `docs/GRPROD_4_PROVENANCE.md` — this record.
