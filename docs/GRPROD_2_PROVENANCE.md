# GRPROD 2 — kernel32 file-mapping provenance

Owner file: `runtime/win32hle/kernel32_ext.c` (exclusive). New selftest:
`runtime/win32hle/kernel32_mapping_selftest.c`. No `host.c`, header, or other
runtime source was edited.

## What was genuinely missing

Before this change the file-mapping family in `kernel32_ext.c` had only
`CreateFileMappingA`, `MapViewOfFile`, `UnmapViewOfFile`, and an
`OpenFileMappingA` that was a hard-coded `ERROR_FILE_NOT_FOUND` failure. A
Unicode-aware game, a shared-memory user, or a stream that flushes a view had
no honest answer. Implemented here (all stdcall, guest-address semantics):

1. **`CreateFileMappingW`** — UTF-16 name converted with the module's existing
   `hle_wide_to_ansi`; shares the A-path creation body.
2. **`OpenFileMappingA` / `OpenFileMappingW`** — real named-mapping open:
   case-insensitive name lookup, `ERROR_FILE_NOT_FOUND` when absent,
   `ERROR_INVALID_PARAMETER` for a NULL/empty name, `ERROR_ACCESS_DENIED` when
   a write desire meets a read-only mapping.
3. **`MapViewOfFileEx`** — honours a caller-supplied 64 KB-aligned guest base
   when it is free; `ERROR_INVALID_PARAMETER` for a misaligned base,
   `ERROR_INVALID_ADDRESS` when the range is busy. `MapViewOfFile` now delegates
   to the same body with a NULL base.
4. **`FlushViewOfFile`** — `msync(MS_SYNC)` over the requested range of a live
   view (0 bytes means the whole view); fails with `ERROR_INVALID_PARAMETER`
   for an address outside any view or a range longer than the view, and
   `ERROR_WRITE_FAULT` on a real flush error.
5. **`GetMappedFileNameA` / `GetMappedFileNameW`** — returns the guest (Windows)
   path the backing file was opened with; fails for anonymous mappings, a
   foreign process handle, or an address outside any view.

Supporting corrections, not new public APIs: creation now rejects invalid
`SEC_PAGE_*` protections with `ERROR_INVALID_PARAMETER`; `FILE_MAP_ALL_ACCESS`
and `FILE_MAP_EXECUTE` are interpreted as Wine does instead of being rejected;
offset/size bounds are checked; a second `Create` of an existing name returns a
valid handle with informational `ERROR_ALREADY_EXISTS`; closing one handle to a
named mapping does not free the object another handle still uses.

## Behaviour sources (consulted, not copied)

Wine is LGPL-2.1; its sources were read for **behaviour only** and no code was
copied. The implementation here is original, written against this HLE's handle
table, guest-memory allocator and host POSIX primitives.

- `CreateFileMappingA` / `OpenFileMappingA` (ANSI→UTF-16 name conversion;
  `OBJ_OPENIF` → `STATUS_OBJECT_NAME_EXISTS` → `ERROR_ALREADY_EXISTS`; open of a
  missing name → `ERROR_FILE_NOT_FOUND`; named objects are case-insensitive):
  Wine `dlls/kernel32/sync.c`, "Mappings" section —
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/kernel32/sync.c>
- `MapViewOfFile` = `MapViewOfFileEx(..., NULL)`; access is interpreted by
  stripping `FILE_MAP_EXECUTE`, then `COPY` (exclusive) / `WRITE` / `READ`, with
  standard-rights bits (as in `FILE_MAP_ALL_ACCESS`) ignored:
  Wine `dlls/kernelbase/memory.c` —
  <https://raw.githubusercontent.com/wine-mirror/wine/master/dlls/kernelbase/memory.c>
- `FlushViewOfFile`: `NtFlushVirtualMemory`, with `STATUS_NOT_MAPPED_DATA`
  treated as success; our `msync` is the POSIX equivalent:
  Wine `dlls/kernelbase/memory.c` (same URL).
- `GetMappedFileNameA/W`: Wine/psapi's `copy_filename` returns the required
  TCHAR count (without the NUL) and only copies when the buffer fits:
  Wine `dlls/psapi/psapi_main.c` (psapi module).
- Win32 documentation for page protections, view access and last-error codes:
  Microsoft Learn `CreateFileMappingA`, `MapViewOfFileEx` and
  `GetMappedFileNameA` reference pages.

## Deliberate, documented modelling choices

- **Process-local names.** The HLE models one process, so named mappings live
  in a 64-entry process table. A name keeps a permanent "anchor" object; every
  handle is a private `dup` of the anchor's descriptor. Windows deletes a name
  when its last handle closes; here it remains openable for the process's
  lifetime. This is a scope simplification, not a false success: every returned
  handle is a real object with real pages.
- **Anonymous mappings are real shared memory.** `INVALID_HANDLE_VALUE` maps
  are backed by an unlinked temporary file (`tmpfile` + `dup` + `ftruncate`),
  so multiple views of one mapping see each other's writes, as Windows
  pagefile-backed sections do. This was found by the new selftest (the first
  implementation gave each view private zero pages).
- **Offsets must be page-aligned and beneath the mapping size; a view may not
  exceed the mapping.** Windows can create a larger view that faults past EOF;
  the HLE refuses it with `ERROR_INVALID_PARAMETER` rather than hand back a
  region that would SIGBUS.
- **Foreign processes are not modelled** by `GetMappedFileName` (only
  `GetCurrentProcess()`'s pseudo-handle is accepted), matching the HLE's
  one-process design.

## Verification

- Single new selftest compiled against the full HLE core (all non-`_selftest`
  `.c` files) with `cc -O1 -Wall -I../recomp32 -I. ... -lpthread -lm`, and run
  headless: `kernel32_mapping_selftest: all checks passed`.
- Existing `kernel32_ext_selftest` still passes (no regression in file I/O,
  Global allocation or `.ini` profile shims).
- `python3 -m pytest tests/test_api_coverage.py tests/test_runtime_registration.py -q`
  → 23 passed.
- Full `runtime/win32hle/build_selftests.sh` run for cross-module regressions.

No live-game claim is made: this is HLE/selftest evidence only.
