# Consolidated upstream provenance

`NOTICE` is the authoritative inventory of third-party **code vendored into
this repository**; this document collects the exact upstream pins, licence
texts and reuse decisions in one place so a reviewer can check them without
reading five different docs. It is written from the licences and commits that
were actually inspected, not from project descriptions.

## Inventory

| Upstream | Pin inspected | Licence | Licence text / existing record | Code reused here? |
|---|---|---|---|---|
| [Sahilg93/arm64-recompiler](https://github.com/Sahilg93/arm64-recompiler) | `bc1ca0ecd6decdbf46cbd378a392a60863f81293` (2026-11-15, "Add the MIT license") | MIT, Copyright (c) 2026 Sahilg93 | [`LICENSES/arm64-recompiler-MIT.txt`](../LICENSES/arm64-recompiler-MIT.txt) (byte-identical to upstream `LICENSE`) | **Yes** — three small Python utilities ported and adapted; details below |
| [HenryKun55/igi-recomp](https://github.com/HenryKun55/igi-recomp) | `278eaf6394f7f42c7de6cffef2cf982154f59899` | MIT, Copyright (c) 2026 HenryKun55 | [`LICENSES/igi-recomp-MIT.txt`](../LICENSES/igi-recomp-MIT.txt) | **Yes** — DirectMusic COM façade adapted into `runtime/win32hle/ole32.c`; see [IGI_RECOMP_REUSE.md](IGI_RECOMP_REUSE.md) |
| zlib contrib `blast/blast.c`, Mark Adler | zlib 1.3 / 24 Aug 2013 (zlib 1.3.1) | zlib licence | [`LICENSES/blast-zlib.txt`](../LICENSES/blast-zlib.txt) | **Yes** — PKWARE DCL explode section ported to Python in `tools/assets/is3extract.py` |
| [sp00nznet/pcrecomp](https://github.com/sp00nznet/pcrecomp) | No single commit pin recorded for the vendored snapshot; a local checkout inspected at `a85ea970f9e0640ab6546d2ca7a01fccb132973d` (informational only) | MIT, Copyright (c) 2026 sp00nz | [`LICENSES/GameReComp-prior-MIT.txt`](../LICENSES/GameReComp-prior-MIT.txt) (earlier GameReComp publication), toolbox manual in [UPSTREAM_TOOLBOX.md](UPSTREAM_TOOLBOX.md) | **Yes** — `tools/`, `runtime/` and `templates/` are the extended toolbox |
| [sp00nznet/redalert2-recomp](https://github.com/sp00nznet/redalert2-recomp) | Not pinned | MIT stated upstream | — | No code vendored; only quoted status/claims in `tools/gamesdb.py` and compatibility notes in `runtime/win32hle/` |
| [codepdbh/nfsu2-recompiled](https://github.com/codepdbh/nfsu2-recompiled) | Not pinned | **Not stated** | — | No code vendored; do not copy from this repository until a licence is confirmed |
| Earlier GameReComp revisions | — | MIT (prior publication) | [`LICENSES/GameReComp-prior-MIT.txt`](../LICENSES/GameReComp-prior-MIT.txt) | Current tree is AGPL-3.0-only (see `LICENSE`) |

## arm64-recompiler: what was actually reused

The repository is a Windows x64 PE → macOS ARM64 Mach-O recompiler built on
LLVM IR. Its C++/LLVM recompiler, generated shims and Wine harness were
**not** copied. Only three self-contained Python utilities were used, all
covered by the upstream MIT licence at the pinned commit:

| Upstream file (at `bc1ca0e`) | Reuse in GameReComp |
|---|---|
| `recompiler/build_wine_db.py` | Export → DLL database: `api_to_dll`, `dll_to_apis`, the six-`core_dlls` subset and the "first DLL defining a symbol wins" rule are preserved in `build_api_db()`. |
| `recompiler/audit_pe.py` | Per-file report fields: `file`, `sha256`, `entry_rva`, import count, writable sections, TLS directory/callbacks and the 256-callback cap are preserved in `audit_pe()` / `PEImage`. |
| `recompiler/scan_missing_imports.py` | Missing/shim-candidate ranking: per-file misses, global frequency and per-DLL breakdown are preserved in `rank_missing_imports()`. |

### Adaptations (and why)

1. **Dependency swap.** Upstream uses `pefile` and is tied to that project's
   `console_lift.py`/`generated_shims.py`. GameReComp's PE toolbox is
   dependency-free, so the `pefile` calls were replaced by a small
   `struct`-based PE header/import/export/TLS reader (`PEImage`). The
   algorithms and report shape are upstream's; the byte-level parsing is new.
2. **PE32+ support.** Upstream's audit targets x64 PE only. The reader handles
   PE32 *and* PE32+ (4- and 8-byte thunks, different optional-header and TLS
   layouts). This closes a real gap: `tools/pe/extract_imports.py` returns an
   empty result for PE32+ images.
3. **Errors are data.** Malformed inputs return an `audit_error` field or are
   skipped in database mode instead of aborting a whole corpus run, matching
   the upstream scripts' per-file `try/except` behaviour.
4. **No generated code copied.** Nothing from `generated_shims.py`, the LLVM
   lifter, or any Wine DLL database was vendored.

### Licence verification

`LICENSE` at upstream commit `bc1ca0ecd6decdbf46cbd378a392a60863f81293` is
MIT ("Copyright (c) 2026 Sahilg93"). MIT permits modification, merging and
redistribution provided the copyright and permission notice accompany the
software; the full text is copied verbatim to
`LICENSES/arm64-recompiler-MIT.txt` and referenced from `NOTICE`. The
adaptation header in `tools/pe/audit_api_surface.py` also carries the
attribution. No code from a repository with an unclear licence was copied.

### Tests and evidence

- `tests/fixtures/make_pe_api_fixture.py` builds two deterministic images with
  imports, exports, TLS callbacks and a writable section: `pe32-api.dll`
  (PE32/i386, sha256 `1a43acc84a006e230456e621c65a89d9f5559f14de40fae96f7af404e86f61c2`)
  and `pe64-api.dll` (PE32+/AMD64, sha256 `18b4bd397387defddf3e14d1a7a75d617c5f3f3a47c9df5be752a2edd7566edd`).
- Both fixtures were independently parsed with `pefile` at the pinned
  environment version (2024.8.26): it agrees on magic, image base, imports,
  exports and TLS callback pointer. The test suite only uses `pefile` when it
  is installed; the tool itself never does.
- `tests/test_pe_api_surface.py` (15 tests + 4 subtests) covers PE32 and PE32+
  audits, the checked-in real PE32 `pe32-minimal.exe`, ordinal imports, export
  databases, core-DLL subsets, missing-API ranking, malformed inputs, the
  TLS-callback cap and all three CLI modes.
- Run with `python3 -m pytest tests/test_pe_api_surface.py -q`.
