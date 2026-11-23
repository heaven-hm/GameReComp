# Agent 32 — PE32 resource handling

## Upstream inspection
- PCRecomp `https://github.com/sp00nznet/pcrecomp` (MIT), local HEAD
  `a85ea970f9e0640ab6546d2ca7a01fccb132973d`: the existing
  `runtime/win32hle/module.c` three-level resource tree and the Win32 resource
  shims were audited and fixed in place. `tools/pe/` parsers were used as layout
  references; no second loader was added.
- Wine `https://github.com/wine-mirror/wine` (LGPL): Win32 resource API
  semantics (ID/string identifiers, language selection, `SizeofResource`
  failure on an invalid HRSRC) were used as a behavioural reference only; no
  Wine code was copied because of the LGPL licence.

## Fix
`module.c` now bounds-checks resource traversal against the module's
`rsrc_size` and image size: directory headers and entry arrays must fit inside
the resource region, named-entry UTF-16 strings must fit before reading, each
subdirectory offset must stay inside the region, and the final data entry must
be a full 16-byte `IMAGE_RESOURCE_DATA_ENTRY` inside it. `hle_resource_data`
rejects zero RVA/size, an RVA outside the image, and a size that overruns the
image. Malformed trees return 0 (API failure) with no out-of-bounds reads.

## Fixture and evidence
`runtime/win32hle/resource_selftest.c` builds a synthetic PE32 image in guest
memory with MZ/PE32 headers, a DataDirectory[2] resource tree, a version-type
resource with integer ID and language, and a known payload. It exercises
`FindResourceA` (via `hle_find_resource`), `SizeofResource` and `LockResource`
through the real shim ABI, checks byte-for-byte payload identity, unknown
type/ID rejection, and three malformed cases (huge directory counts, out-of-
image data RVA, oversized data entry).

Commands and outputs on macOS arm64:
```
bash runtime/win32hle/build_selftests.sh   -> 16/16 ok, exit 0
python3 -m pytest tests/ -q                -> 168 passed, 2 skipped
negative control (pre-fix module.c):
  FAIL malformed resource directory refused
  FAIL out-of-image resource RVA refused
  FAIL oversized resource refused
```

## Scope / limitations
- This is resource bounds/validation for the existing Win32 API surface, not a
  full resource compiler or version-info parser. `FindResourceW` string
  identifiers are matched through the existing ANSI conversion path.
- No retail executable, asset, screenshot or original-Windows comparison was
  used. COMPLETE here means synthetic fixture evidence only, not gameplay.
- Version parsing beyond exposing the resource bytes is out of scope.
