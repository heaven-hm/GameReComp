# Agent 30 — COM and interface infrastructure

## Upstream inspection
- PCRecomp `https://github.com/sp00nznet/pcrecomp` (MIT), local HEAD
  `a85ea970f9e0640ab6546d2ca7a01fccb132973d`: existing `ole32.c` COM vtable
  builder, object allocator and IUnknown shims, plus the established vtable
  layouts in `ddraw.c`, `d3d7.c`, `dsound.c`, `dinput.c` and `storage.c`, were
  reused. No DirectX implementation was replaced.
- Wine `https://github.com/wine-mirror/wine` (LGPL): IUnknown contract
  (E_POINTER for NULL args, E_NOINTERFACE with NULL output for unsupported
  IIDs, AddRef on successful QI) was used as a behavioural reference only; no
  Wine code was copied because of the LGPL licence.
- Microsoft DirectX-Headers: public `IID_IDirectSound` and
  `IID_IDirectSoundBuffer` byte layouts were used for the integration binding.

## Implementation
Added a shared interface-identity side table in `runtime/win32hle/ole32.c`:
`hle_com_iid_equal`, `hle_com_bind_iid`, `hle_com_unbind_iids`. The generic
`hle_com_QueryInterface` now compares the requested 16-byte GUID against the
object's bound interfaces, returns E_NOINTERFACE with a NULL output for an
unsupported IID, E_POINTER for NULL arguments, and AddRefs the returned
interface. Objects with no bound IID keep the legacy single-interface answer so
established DirectX objects are unchanged. `hle_com_Release` removes IID
bindings when the last reference is dropped.

Integration with an existing DirectX interface: `dsound.c` binds the public
DirectSound and DirectSoundBuffer IIDs to the objects produced by its factories
and unbinds them on final release. Guest object layout (vtable@0, refcount@4)
and all existing vtable slots are untouched.

## Fixture and evidence
New `runtime/win32hle/com_selftest.c` verifies: vtable slot order
(QueryInterface/AddRef/Release), 32-bit guest VAs, interface identity for two
bound IIDs on one object, refcount increments/decrements, object destruction,
E_NOINTERFACE with NULL output, E_POINTER handling, and DirectSound COM objects
created through the registered factory answering their real IID while refusing
an unsupported one. A debug run during development exposed and fixed a QI
success path returning the refcount instead of S_OK.

Commands and outputs on macOS arm64:
```
bash runtime/win32hle/build_selftests.sh   -> 16/16 ok, exit 0
python3 -m pytest tests/ -q                -> 168 passed, 2 skipped
```

## Limitations
- This is shared IUnknown/identity infrastructure, not a full COM apartment,
  aggregation, apartment threading, IClassFactory marshalling or IDispatch.
- D3D7/DDraw keep their own strict QueryInterface implementations; only objects
  using the generic helper gain IID checking.
- No game executable, screenshot, frame or audio evidence: PASS here is synthetic
  ABI/lifecycle evidence only, not gameplay or original-Windows verification.
