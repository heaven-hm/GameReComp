# MAXCOMP-AG-A — PE32 loader / DLL path provenance

Scope: audit and improve the existing PE32 loader / import / DLL path. The
defect fixed is **delay-loaded imports being invisible to both the static
analyser and the runtime IAT binder** (plus a related IAT-slot bug in the
pefile-free fallback).

## Upstream inspected (read-only)

- Repository: <https://github.com/sp00nznet/pcrecomp>
- Pinned commit inspected: `7c07a0b42059b6dc4313d9b4ebded6f7a72db8f3` (`main`)
- License: MIT License, Copyright (c) 2026 sp00nz (`LICENSE`, SPDX `MIT`).
- Read-only clone used for inspection only; no upstream file was modified and
  no upstream commit was pushed from this worktree.

### Exact upstream locations consulted

| File | Function / region | What it established |
| --- | --- | --- |
| `tools/pe/delay_imports.py` | `parse_manual()` | `ImgDelayDescr` is 8 dwords (`grAttrs, szName, phmod, pIAT, pINT, pBoundIAT, pUnloadIAT, dwTimeStamp`); directory index 13; bit 0 of `grAttrs` marks RVA-based fields; terminator is `szName == 0 && iat_rva == 0`. This file is also checked in locally and was **not** changed. |
| `runtime/native32/regbox32.c` | `patch_module()` (the `IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT` loop, about lines 799–817) | A delay descriptor carries an INT/IAT thunk pair; by-ordinal entries use `IMAGE_ORDINAL_FLAG32`; an unbound slot is one still pointing back into its own image. This is the reference for walking the descriptor and recovering names/ordinals. |
| `tools/disasm/disasm32.py` | `main(argv=None)`, delay-load stub seeding block (about lines 2210–2240) | `pefile` exposes parsed delay imports as `DIRECTORY_ENTRY_DELAY_IMPORT` and `ImportData.address` is the IAT slot VA; delay-load thunks are otherwise unreachable by a code scan. Inspected for the analyser side. |
| `runtime/recomp32/image_loader.c`, `runtime/win32hle/pe_loader.c` | upstream equivalents | Confirmed the loader is the component that writes resolved addresses into IAT slots; no existing upstream implementation binds delay slots in the win32hle/recomp32 static path, so this change is new work in that path. |

## What was implemented, and where

| Local file | Change | Relationship to upstream |
| --- | --- | --- |
| `tools/pe/pe_analyze.py` | `ImportEntry.is_delayed`; `_analyze_with_pefile()` folds `DIRECTORY_ENTRY_DELAY_IMPORT` into `imports`/`import_dlls` with the IAT slot RVA; `_analyze_with_struct()` parses directory 13 (RVA- and VA-based descriptors, ordinals, missing INT); `build_iat_map()` now includes those slots; fixed the pre-existing bug where the struct fallback recorded the **ILT** RVA as the IAT slot RVA; `export_json()` reports `delay_import_count`. | **Adapted** the descriptor layout and attribute semantics from `tools/pe/delay_imports.py` and the thunk handling from `regbox32.c:patch_module()`. Written independently in this codebase's dataclass/parser style; no upstream lines copied verbatim. |
| `runtime/win32hle/pe_format.h` | `pe_delay_import_descriptor`, `PE_DIR_DELAY_IMPORT`, `PE_DELAY_ATTR_RVA`. | Structure mirrors the platform `IMAGE_DELAYLOAD_DESCRIPTOR` as documented by the upstream files above; locally defined to keep the loader `windows.h`-free. |
| `runtime/win32hle/pe_loader.c` / `.h` | `pe_image.delay_import_rva` captured at map time; `bind_thunk()` shared by ordinary and delay imports; `bind_delay_imports()` binds delay IAT slots eagerly through the same shim/companion resolver chain; bounds checks added to both descriptor walks. | **Adapted**: upstream `regbox32.c:patch_module()` walks the same descriptor/INT/IAT pair, but it patches host DLL slots on Windows; here the slots are bound at boot in the portable guest loader, so the control flow is new. |
| `tools/autorecomp/pipeline.py` | `reports/inspect.json` now records `delay_import_count` as evidence. | Local addition. |
| `runtime/win32hle/pe_loader_selftest.c` | Synthetic PE now has ordinary, RVA-based delay (by name and ordinal) and VA-based delay imports; asserts companion/shim binding. The selftest uses local shim stubs so it builds as a single small target. | Local addition. |
| `tests/test_pe_delay_imports.py` | Deterministic hand-built PE32 fixture; tests both parsers, the IAT map, DLL de-duplication, old-style VA descriptors and the no-delay-directory case. | Local addition. |

## Deliberate differences / limits

- Upstream `disasm32.py` *seeds delay-load thunks as functions* so the
  disassembler can lift `__delayLoadHelper` stubs. This change does not: the
  recompiled call site dispatches through the eagerly bound IAT slot and never
  executes the helper. Seeding stubs remains a separate fidelity improvement.
- Runtime `LoadLibraryA` in `runtime/win32hle/module.c` maps a DLL's data but
  runs no code and binds none of that DLL's imports; this change does not alter
  that documented limitation — companion DLLs are still handled by the
  generated companion table and `recomp_pe_bind_companions()`.
- TLS callback ordering, forwarded-export resolution and companion `DllMain`
  ordering were inspected but are **not** changed by this work.
- No live-game validation was performed; only targeted pytest and the
  `pe_loader_selftest` single-target build/run (see commit message).

## Licensing

No upstream source text was copied verbatim. The upstream project is MIT
licensed (SPDX `MIT`, Copyright (c) 2026 sp00nz), which is compatible with this
repository's existing licensing; the adapted structures and descriptor
semantics are attributed above.
