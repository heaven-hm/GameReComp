# GR1 provenance — PE32 export forwarders in the Win32 HLE loader

## Source used (read-only)

| Item | Value |
| --- | --- |
| Upstream | PCRecomp — <https://github.com/sp00nznet/pcrecomp> |
| Local checkout (read-only) | `/Users/haseeb-mir/Documents/Code/pcrecomp` |
| Checkout HEAD | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` |
| Upstream licence | MIT, Copyright (c) 2026 sp00nz |
| Reuse already declared in this repository | `docs/UPSTREAM_CODE_PROVENANCE.md` (rows for the PE32 loader, resource model and PE validation) |

Only `runtime/`, `tools/pe/` and the root `LICENSE` of that checkout were read.
Nothing in the upstream tree was modified. The upstream MIT notice is
reproduced at the end of this file.

## The gap identified in upstream

`pcrecomp/runtime/native32/native32.c` (`native32_export`, around lines
398–412) walks a mapped PE's export directory and resolves a name or ordinal
to `base + AddressOfFunctions[idx]`. Its own comment is explicit:

> "Forwarders are not followed; no guest module has needed one."

A forwarder is an export whose function RVA lies inside the export data
directory: the RVA points at a string such as `KERNEL32.Sleep` or `NTDLL.#8`.
The MSVC CRT DLLs and the `api-ms-win-*` layer forward nearly all of their
entry points this way. GameReComp inherited the same limitation through its
HLE loader: `k_GetProcAddress` in `runtime/win32hle/module.c` answered only
system pseudo-modules (from the shim registry) and returned 0 for every export
of a mapped DLL image. A game doing `LoadLibrary` + `GetProcAddress` on such a
DLL therefore failed even where the real target was an ordinary registered
shim.

## What was reused

- **Export-directory walk shape** — `native32_export()` above: the meaning of
  export-directory fields `Base`, `NumberOfFunctions`, `AddressOfFunctions`,
  `AddressOfNames` and `AddressOfNameOrdinals`, and the name → ordinal →
  function-index mapping. Adapted with bounds checks and with RVAs converted
  through `base + rva` (the upstream routine's name/ordinal arrays are read
  the same way).
- **Ordinal shim naming** — `pcrecomp/runtime/native32/native32.h` and
  `pcrecomp/runtime/win32hle/wsock32.c`: an import by ordinal is registered as
  `<dll>.dll#<ordinal>` (e.g. `wsock32.dll#2`). Kept unchanged in the new
  forwarder path so ordinal forwarders land on the existing shims.
- **PE32 data-directory offsets** — the convention already present in
  GameReComp's `module.c` (`ensure_rsrc` reads entry 2 at optional-header offset
  `0x70`) was extended to entry 0 at `0x60`, with the PE32+ variant handled in
  the new standalone tool.
- **Pure-struct PE parsing pattern** — `pcrecomp/tools/pe/delay_imports.py`:
  derive a data directory's file offset from the optional header and section
  headers instead of a hardcoded file offset. `tools/pe/forwarders.py` follows
  that pattern for export data directory 0.

The forwarder-string format itself (`DLL.Function` / `DLL.#Ordinal`, split on
the last dot) is Windows PE semantics documented in the Microsoft PE/COFF
specification, not copied source.

## What is newly authored here

- `runtime/win32hle/module.c`
  - `export_directory()` — validate a mapped PE32 and locate export data
    directory 0.
  - `resolve_module_export()` — name/ordinal lookup on system pseudo-modules
    and on mapped images; forwarder detection by RVA range; bounded copy of
    the forwarder string; last-dot split; recursive resolution into the target
    DLL with an 8-hop bound that also breaks cycles; system shims by name and
    by `<dll>.dll#<ordinal>`; global-symbol fallback for `api-ms-win-*`
    forwarders, which have no pseudo-module in this host.
  - `hle_get_proc_address()` — the stack-independent core called by
    `k_GetProcAddress`, which is now a one-line wrapper.
- `tools/pe/forwarders.py` (new) — standalone PE32/PE32+ export auditor that
  reports which exports are forwarders and where they point. Pure-struct,
  no `pefile` dependency.
- `tests/test_pe_forwarders.py` (new) — synthetic PE fixtures covering name
  and ordinal forwarders, PE32+, absent/malformed export directories and CLI
  output.
- `runtime/win32hle/forwarder_selftest.c` (new) — two synthetic mapped images
  exercising direct exports, forwarders to system shims by name and ordinal,
  multi-image forwarding, the `api-ms-win-*` fallback, a cycle and unanswered
  lookups.

No file outside the GR1 ownership set was changed; in particular
`tools/autorecomp/pipeline.py`, `runtime/win32hle/host.c` and
`runtime/win32hle/win32hle.h` were left untouched.

## Audit notes for the other candidate capabilities

- **Companion DLL init order** — already handled: `pipeline.py`
  (`_find_companion_dlls` / `_rebase_companion`) walks the import graph and
  emits `recomp_companion_list`; `host.c:map_companions` reserves every image
  in one pass and only then binds imports and calls lifted
  `DllMain(DLL_PROCESS_ATTACH)` in a second pass. No gap to fill here.
- **Ordinal imports** — `pe_loader.c` binds `dll.dll#N` from the shim registry;
  this change extends ordinal lookup to mapped images and ordinal forwarders.
- **Delay-loaded imports** — `tools/pe/delay_imports.py` reports them, but no
  runtime binder consumes that list. Out of scope for the one capability
  implemented here.
- **TLS callback ordering** — no TLS-directory handling exists in `module.c`
  or `pe_loader.c`. Not implemented here; it would need changes outside the
  GR1 ownership set.

## Upstream MIT notice

```
MIT License

Copyright (c) 2026 sp00nz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
