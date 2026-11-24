#!/usr/bin/env python3
"""Machine-readable Win32 API coverage auditor for PE32 import tables.

Reads imports from real PE32 files, compares them with the shim names actually
registered in runtime/win32hle/*.c, and reports implemented / missing imports
and ordinal imports.

Only a registered shim name counts as implemented.  A namespaced shim such as
``D3D9::Object::QueryInterface`` or ``IDirect3D8::CreateDevice`` is reduced to
its leaf (``QueryInterface`` / ``CreateDevice``), because that leaf is the name
a PE import table actually carries.  An ordinal alias such as
``wsock32.dll#12`` is likewise reduced to ``#12``.  None of this is proof that
the function *behaves* correctly: it only proves a name is registered.

PE imports from game-owned DLLs (binkw32.dll, bulletml.dll, d3dx9_30.dll,
MSVCP140.dll, VCRUNTIME140.dll, ...) are NOT Win32 system APIs.  They are
reported separately so the "missing API" headline number is not misleading.

    python3 -m tools.api_coverage --corpus /path/to/games --out reports/api-coverage.json

Use ``--markdown docs/WIN32_API_COVERAGE.md`` to (re)generate the human-readable
report directly from the same JSON, so the documented numbers cannot drift from
the auditor output.

The auditor is fail-closed: a Win32 *system* DLL import that is unresolved and
not already in :data:`KNOWN_UNRESOLVED_CRITICAL` is "new" and makes the CLI exit
non-zero.  Every DLL family is reported explicitly with ``not implemented``
entries; game-owned families are marked ``deferred`` instead of being folded
into a blanket coverage percentage that would hide them.  No percentage is
reported at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "win32hle"

# Shim names may be plain Win32 symbols, COM/interface names ("IStream::Read",
# "D3D9::Device::Present"), or ordinal aliases ("wsock32.dll#12").  ':' is
# included in the character class so namespaced literals are extracted too.
SHIM_NAME = r'"([A-Za-z0-9_:?#@.$]+)"'

# Table forms used by the runtime:
#   { "name", fn }, SHIM2(ord, "name", fn), AUT(ord, "name", fn), SHIM1(...)
# X-macro method lists - X("name", fn, argc) - expand into shim table entries
# (d3d8.c builds "IDirect3D8::" name; d3d9.c spells out the full name).  The
# strict three-argument, numeric-argc form keeps other X macros out.
SHIM_PATTERNS = [
    re.compile(r'\{\s*' + SHIM_NAME + r'\s*,'),
    re.compile(r'\bSHIM\d*\s*\(\s*\d+\s*,\s*' + SHIM_NAME),
    re.compile(r'\bAUT\s*\(\s*\d+\s*,\s*' + SHIM_NAME),
    re.compile(r'\bX\s*\(\s*' + SHIM_NAME + r'\s*,\s*[A-Za-z_]\w*\s*,\s*\d+\s*\)'),
]
# SHIM2/AUT synthesise "wsock32.dll#12" from a macro definition like
#   #define SHIM2(ord, name, fn) { "wsock32.dll#" #ord, fn }, { name, fn }
# The alias is not a literal at the call site, so recover it from the define.
ORDINAL_ALIAS_DEF = re.compile(
    r'#define\s+(SHIM\d*|AUT)\s*\([^)]*\)\s*\{[^}]*?"([A-Za-z0-9_.]+\.(?:dll|drv)#)"\s*#\s*\w+',
    re.S,
)
ORDINAL_INVOCATION = re.compile(r'\b(SHIM\d*|AUT)\s*\(\s*(\d+)\s*,')
NON_API = {"win32hle.h", "README"}

# Allow-list of DLLs that are part of the Win32 / DirectX system surface this
# project targets.  Anything NOT in this set is treated as game-owned (a game's
# own DLL or a bundled third-party/redistributable runtime), never as a Win32
# system API.  Keeping this as an allow-list is deliberate: an unknown DLL is
# conservatively game-owned, so it cannot silently inflate the Win32 number.
SYSTEM_DLLS = frozenset({
    "advapi32.dll", "avifil32.dll", "comctl32.dll", "comdlg32.dll",
    "crypt32.dll", "d3d8.dll", "d3d9.dll", "ddraw.dll", "dinput.dll",
    "dinput8.dll", "dplayx.dll", "dsound.dll", "gdi32.dll", "gdiplus.dll",
    "imm32.dll", "iphlpapi.dll", "kernel32.dll", "kernelbase.dll", "lz32.dll",
    "msimg32.dll", "msvcr100.dll", "msvcr110.dll", "msvcr120.dll",
    "msvcr80.dll", "msvcr90.dll", "msvcrt.dll", "ntdll.dll", "ole32.dll",
    "oleaut32.dll", "opengl32.dll", "psapi.dll", "quartz.dll", "rpcrt4.dll",
    "setupapi.dll", "shell32.dll", "shlwapi.dll", "unicows.dll", "user32.dll",
    "userenv.dll", "version.dll", "wininet.dll", "winmm.dll", "winspool.drv",
    "uxtheme.dll", "ws2_32.dll", "wsock32.dll",
})

SCHEMA_VERSION = "1.2.0"

# An unresolved import from a Win32 system DLL is "critical": those DLLs are the
# surface this project claims to shim.  Game-owned DLL imports are not Win32
# APIs and are therefore reported as explicitly deferred families, never as
# critical blockers and never as part of a headline percentage.
CRITICAL_KIND = "system"
DEFERRED_KINDS = frozenset({"game-owned"})

# Unresolved critical imports already known/tracked from the last corpus audit
# (docs/WIN32_API_COVERAGE.md, 13 system imports).  Only *new* unresolved
# critical imports trip the fail-closed gate; a known one stays visible in the
# per-family ``not implemented`` list and in ``missing_by_api``.  Kept in this
# file so the tool stays self-contained and deterministic.
KNOWN_UNRESOLVED_CRITICAL = frozenset({
    ("comctl32.dll", "ImageList_Replace"),
    ("msvcrt.dll", "__mb_cur_max"),
    ("msvcrt.dll", "_ctype"),
    ("msvcrt.dll", "_fmode"),
    ("msvcrt.dll", "_iob"),
    ("msvcrt.dll", "_pctype"),
    ("ole32.dll", "CreateBindCtx"),
    ("ole32.dll", "CreateILockBytesOnHGlobal"),
    ("ole32.dll", "OleGetIconOfClass"),
    ("ole32.dll", "StgCreateDocfileOnILockBytes"),
    ("opengl32.dll", "glTexCoord2i"),
    ("opengl32.dll", "glTexSubImage2D"),
    ("opengl32.dll", "glVertex2i"),
})

# APIs first registered in the most recent integration checkpoint recorded in
# docs/ISSUE_EXECUTION_PLAN.md (the 10-agent coverage-refresh checkpoint:
# A22 COMCTL32 FlatSB, A26 locale Ex, A27 SList/Rtl, A28 wide-string CRT,
# A29 SDL clip/cursor/lower-blit).  Independently verified by diffing this
# checkout's shim registry against the previous coverage checkpoint (a900ee5,
# 2249 shims): these 24 names are exactly the additions.  Registration only -
# see the per-API selftests for behaviour evidence.
RECENTLY_REGISTERED_SHIMS = {
    "A22 - COMCTL32 FlatSB (comctl32.dll)": [
        "FlatSB_EnableScrollBar", "FlatSB_GetScrollInfo",
        "FlatSB_GetScrollPos", "FlatSB_GetScrollProp",
        "FlatSB_GetScrollRange", "FlatSB_ShowScrollBar",
        "FlatSB_SetScrollInfo", "FlatSB_SetScrollPos",
        "FlatSB_SetScrollRange",
    ],
    "A26 - locale Ex APIs (kernel32.dll)": [
        "CompareStringEx", "GetLocaleInfoEx", "LCMapStringEx",
    ],
    "A27 - SList / Rtl extras (kernel32.dll)": [
        "InterlockedFlushSList", "InterlockedPushEntrySList",
        "RtlPcToFileHeader", "RtlUnwindEx",
    ],
    "A28 - wide-string CRT exports (msvcrt.dll)": [
        "_wcsdup", "wcscat", "wcscmp", "wcsncat",
    ],
    "A29 - SDL 1.2 clip/cursor/lower-blit (sdl.dll / sdl2.dll)": [
        "SDL_FreeCursor", "SDL_GetClipRect", "SDL_LowerBlit",
        "SDL_SetClipRect",
    ],
}


def recently_registered_names() -> list[str]:
    """Flat list of the shim names added in the last recorded checkpoint."""
    return [name for names in RECENTLY_REGISTERED_SHIMS.values() for name in names]


def strip_comments(text: str) -> str:
    """Remove C comments so a string literal in a comment is never counted."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def registered_shims(runtime_dir: Path = RUNTIME) -> set[str]:
    """Return the raw names actually registered as shim table entries.

    Namespaced names and ordinal aliases are kept verbatim; use
    :func:`shim_import_names` to map them onto PE import names.
    """
    names: set[str] = set()
    for path in sorted(Path(runtime_dir).glob("*.c")):
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for pattern in SHIM_PATTERNS:
            names.update(pattern.findall(text))
        prefixes = {macro: prefix for macro, prefix in ORDINAL_ALIAS_DEF.findall(text)}
        for macro, ordinal in ORDINAL_INVOCATION.findall(text):
            if macro in prefixes:
                names.add(prefixes[macro] + ordinal)
    return names - NON_API


def import_name(shim: str) -> str:
    """Map one registered shim name to the PE import name it answers.

    ``D3D9::Object::QueryInterface`` -> ``QueryInterface`` (the leaf is the
    imported function name); ``wsock32.dll#12`` -> ``#12`` (ordinal import).
    """
    if "::" in shim:
        return shim.rsplit("::", 1)[1]
    if "#" in shim:
        return "#" + shim.rsplit("#", 1)[1]
    return shim


def shim_import_names(shims) -> set[str]:
    """Register-set -> set of PE import names covered by real shims.

    Both the full name and its import alias are returned.  Only names from the
    supplied registry are used; nothing is inferred from a DLL name.
    """
    names: set[str] = set()
    for shim in shims:
        names.add(shim)
        names.add(import_name(shim))
    return names


def dll_kind(dll: str) -> str:
    """``"system"`` for a known Win32 system DLL, else ``"game-owned"``."""
    return "system" if dll.lower() in SYSTEM_DLLS else "game-owned"


def family_of(dll: str) -> str:
    """DLL-scoped API family name (always lower-case, like PE import names)."""
    return dll.lower()


def unresolved_critical(report: dict, baseline=KNOWN_UNRESOLVED_CRITICAL) -> list[dict]:
    """Missing critical imports that are *new* relative to ``baseline``.

    The baseline is an iterable of ``(dll, name)`` tuples.  Only unresolved
    system-DLL imports are critical; game-owned families are deferred, never
    gate-failing.  Ordering is deterministic (most files first, then dll/name),
    so the CLI's fail-closed verdict cannot depend on hash iteration order.
    """
    known = {tuple(pair) for pair in baseline}
    rows = [
        row for row in report["missing_by_api"]
        if (row["dll"], row["name"]) not in known
    ]
    return sorted(rows, key=lambda row: (-row["files"], row["dll"], row["name"]))


def pe_imports(path: Path) -> dict:
    """Return {dll_lower: [names]} for one PE file; ordinals are '#N'."""
    try:
        import pefile
    except ImportError as exc:  # pragma: no cover - dependency is in tests
        raise RuntimeError("pefile is required: python3 -m pip install pefile") from exc
    pe = pefile.PE(str(path), fast_load=False)
    dlls: dict[str, list[str]] = {}
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dll = entry.dll.decode("utf-8", "replace").lower()
        names = dlls.setdefault(dll, [])
        for imp in entry.imports:
            name = imp.name.decode("utf-8", "replace") if isinstance(imp.name, bytes) else imp.name
            names.append(name if name else f"#{imp.ordinal}")
    return dlls


def _implemented(name: str, dll: str, raw: set[str], covered: set[str]) -> bool:
    """True when a real registered shim answers this import name.

    Ordinals are matched DLL-first (``wsock32.dll#12``); the numeric fallback
    covers registrations without a DLL-qualified alias.
    """
    if name.startswith("#"):
        return f"{dll}{name}" in raw or name in covered
    return name in covered


def build_report(corpus: Path, shims: set[str]) -> dict:
    corpus = Path(corpus)
    raw = set(shims)
    covered = shim_import_names(raw)
    inputs = {}
    all_missing = Counter()            # system DLLs only (headline)
    all_game_missing = Counter()       # game-owned DLLs, separate blocker list
    all_ordinal = Counter()
    all_game_ordinal = Counter()
    all_unresolved = Counter()         # every unresolved import, incl. ordinals
    dlls_by_kind = {"system": set(), "game-owned": set()}
    dll_names: dict[str, set[str]] = {}  # all import names seen per DLL family
    for path in sorted(corpus.rglob("*.exe")):
        try:
            data = path.read_bytes()
            imports = pe_imports(path)
        except Exception:
            continue
        digest = hashlib.sha256(data).hexdigest()
        missing = {}
        game_missing = {}
        ordinals = {}
        game_ordinals = {}
        for dll, names in imports.items():
            kind = dll_kind(dll)
            dlls_by_kind[kind].add(dll)
            dll_names.setdefault(dll, set()).update(names)
            for name in names:
                if _implemented(name, dll, raw, covered):
                    continue
                all_unresolved[(dll, name)] += 1
                if name.startswith("#"):
                    (game_ordinals if kind == "game-owned" else ordinals).setdefault(dll, []).append(name)
                    (all_game_ordinal if kind == "game-owned" else all_ordinal)[dll] += 1
                else:
                    (game_missing if kind == "game-owned" else missing).setdefault(dll, []).append(name)
                    (all_game_missing if kind == "game-owned" else all_missing)[(dll, name)] += 1
        inputs[digest] = {
            "input_sha256": digest,
            "path": str(path),
            "imports": imports,
            "missing": missing,               # system DLLs
            "game_owned_missing": game_missing,
            "ordinals": ordinals,             # system DLLs
            "game_owned_ordinals": game_ordinals,
        }
    # Per-family, explicit "not implemented" entries.  A count alone hides
    # which API in a deferred family was skipped, so every unresolved import is
    # materialised as its own row with an explicit status.
    status_order = {"not_implemented": 0, "deferred": 1, "implemented": 2}
    families: list[dict] = []
    for dll in sorted(dll_names):
        kind = dll_kind(dll)
        missing_names = {name for (entry_dll, name) in all_unresolved if entry_dll == dll}
        status = (
            "not_implemented" if missing_names and kind == CRITICAL_KIND else
            "deferred" if missing_names and kind in DEFERRED_KINDS else
            "implemented"
        )
        not_implemented = [
            {"dll": dll, "name": name, "files": count, "status": "not_implemented"}
            for (entry_dll, name), count in all_unresolved.items()
            if entry_dll == dll
        ]
        not_implemented.sort(key=lambda row: (-row["files"], row["name"]))
        families.append({
            "family": family_of(dll),
            "kind": kind,
            "critical": kind == CRITICAL_KIND,
            "status": status,
            "implemented_count": len(dll_names[dll] - missing_names),
            "not_implemented_count": len(missing_names),
            "not_implemented": not_implemented,
        })
    families.sort(key=lambda f: (status_order[f["status"]],
                                 -f["not_implemented_count"], f["family"]))
    deferred_families = [f["family"] for f in families if f["status"] == "deferred"]

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": "python3 -m tools.api_coverage",
        "what": "PE32 import-name coverage against registered win32hle shim names",
        "is_not": "proof that a function behaves correctly; registration only proves a name is present",
        "counting_rule": (
            "Implemented means the import name equals a registered shim name, the "
            "leaf of a '::' namespaced shim, or a '#N' ordinal alias. Registration "
            "is not proof of behaviour. Game-owned DLL imports are reported "
            "separately from Win32 system DLL imports."
        ),
        "summary_scope": "headline counts cover Win32 system DLLs only; game-owned counts are separate",
        "family_rule": (
            "Each DLL family lists every unresolved import explicitly with "
            "status 'not implemented'. System families are critical; game-owned "
            "families are deferred. No aggregate coverage percentage is reported, "
            "so deferred families cannot be hidden by a blanket number."
        ),
        "registered_shim_count": len(raw),
        "registered_import_name_count": len(covered),
        "dll_classification": {
            "system": sorted(dlls_by_kind["system"]),
            "game_owned": sorted(dlls_by_kind["game-owned"]),
        },
        "summary": {
            "inputs": len(inputs),
            "distinct_dlls": len(dlls_by_kind["system"]) + len(dlls_by_kind["game-owned"]),
            "system_dlls": len(dlls_by_kind["system"]),
            "game_owned_dlls": len(dlls_by_kind["game-owned"]),
            "distinct_missing_api": len(all_missing),          # system only
            "game_owned_missing_api": len(all_game_missing),
            "all_missing_api": len(all_missing) + len(all_game_missing),
            "ordinal_imports": sum(all_ordinal.values()),      # system only
            "game_owned_ordinal_imports": sum(all_game_ordinal.values()),
            "all_ordinal_imports": sum(all_ordinal.values()) + sum(all_game_ordinal.values()),
        },
        "missing_by_api": [               # system DLLs only - the honest number
            {"dll": dll, "name": name, "files": count}
            for (dll, name), count in all_missing.most_common()
        ],
        "game_owned_missing_by_api": [
            {"dll": dll, "name": name, "files": count}
            for (dll, name), count in all_game_missing.most_common()
        ],
        "families": families,
        "deferred_families": deferred_families,
        "inputs": inputs,
    }
    new_critical = unresolved_critical(report)
    report["gate"] = {
        "mode": "fail-closed",
        "critical_kind": CRITICAL_KIND,
        "known_unresolved_baseline_count": len(KNOWN_UNRESOLVED_CRITICAL),
        "new_unresolved_critical": new_critical,
        "fail_closed": bool(new_critical),
    }
    return report


def _sorted_pairs(rows: list[dict]) -> list[tuple[str, str, int]]:
    """Deterministic (dll, name, files) ordering for generated tables."""
    return sorted(
        ((row["dll"], row["name"], row["files"]) for row in rows),
        key=lambda row: (-row[2], row[0], row[1]),
    )


def render_markdown(report: dict, *, corpus=None, out=None, generated_at=None) -> str:
    """Render the human-readable coverage doc from a JSON report.

    Every number below is taken from ``report``; nothing is estimated.  The
    renderer deliberately states that the audit is registration-only and that
    the missing counts remain non-zero, so it cannot produce a completion or
    full-coverage claim.  Per-family ``not implemented`` entries and the
    fail-closed gate are rendered explicitly; no aggregate percentage is
    emitted that could hide a deferred family.  The additive "recently
    registered shims" section is sourced from
    :data:`RECENTLY_REGISTERED_SHIMS` and carries the same caveat.
    """
    s = report["summary"]
    corpus = str(corpus) if corpus else "(RECOMP_CORPUS)"
    out = str(out) if out else "reports/api-coverage.json"
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    system_missing = _sorted_pairs(report["missing_by_api"])
    game_missing = _sorted_pairs(report["game_owned_missing_by_api"])
    system_dlls = report["dll_classification"]["system"]
    game_dlls = report["dll_classification"]["game_owned"]
    families = report.get("families", [])
    deferred = report.get("deferred_families", [])
    gate = report.get("gate", {})

    L: list[str] = []
    L.append("# Win32 API coverage (auditor output)")
    L.append("")
    L.append("Generated from real PE32 import tables by "
             "`python3 -m tools.api_coverage`.")
    L.append("")
    L.append(f"- Corpus: `{corpus}`")
    L.append(f"- JSON report: `{out}`")
    L.append(f"- Regenerated (UTC): `{generated_at}`")
    L.append(f"- Schema: `{report['schema_version']}`")
    L.append("")
    L.append("## Headline numbers")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---|")
    L.append(f"| PE32 inputs analysed | {s['inputs']} |")
    L.append(f"| Registered shim names (`registered_shim_count`) | "
             f"{report['registered_shim_count']} |")
    L.append(f"| Distinct PE import names covered "
             f"(`registered_import_name_count`) | "
             f"{report['registered_import_name_count']} |")
    L.append(f"| Distinct DLLs referenced | {s['distinct_dlls']} "
             f"({s['system_dlls']} system, {s['game_owned_dlls']} game-owned) |")
    L.append(f"| Missing Win32 **system** APIs (`distinct_missing_api`) | "
             f"{s['distinct_missing_api']} |")
    L.append(f"| Missing game-owned APIs (`game_owned_missing_api`) | "
             f"{s['game_owned_missing_api']} |")
    L.append(f"| All missing APIs combined (`all_missing_api`) | "
             f"{s['all_missing_api']} |")
    L.append(f"| Ordinal imports (system / game-owned / all) | "
             f"{s['ordinal_imports']} / {s['game_owned_ordinal_imports']} / "
             f"{s['all_ordinal_imports']} |")
    L.append("")
    L.append(f"**Counting rule:** {report['counting_rule']}")
    L.append("")
    L.append("**Coverage is incomplete.** Only a registered shim *name* counts "
             f"as implemented, and {s['distinct_missing_api']} system plus "
             f"{s['game_owned_missing_api']} game-owned import names are still "
             "unregistered. This is a name-registration audit; per-API "
             "selftests, not this report, are the behaviour evidence.")
    L.append("")
    L.append(f"**What this is not:** {report['is_not']}.")
    L.append("")
    L.append("## Per-family implementation status (explicit)")
    L.append("")
    L.append("**No aggregate coverage percentage is reported.** A blanket "
             "percentage would hide deferred families, so every family below "
             "lists its exact `not implemented` imports. A `deferred` family is "
             "explicitly out of scope (game-owned DLLs are not Win32 APIs); it "
             "is never folded into a headline number.")
    L.append("")
    L.append("| Family | Kind | Status | Implemented imports | "
             "Not implemented (explicit) |")
    L.append("|---|---|---|---|---|")
    for fam in families:
        names = ", ".join(f"`{row['name']}`" for row in fam["not_implemented"]) or "(none)"
        L.append(f"| `{fam['family']}` | {fam['kind']} | {fam['status']} | "
                 f"{fam['implemented_count']} | {names} |")
    L.append("")
    if deferred:
        L.append("Deferred families (explicit, never hidden by a percentage): "
                 + ", ".join(f"`{d}`" for d in deferred) + ".")
        L.append("")
    if report.get("family_rule"):
        L.append(f"**Family rule:** {report['family_rule']}")
        L.append("")
    L.append("## Fail-closed gate")
    L.append("")
    L.append(f"- Mode: `{gate.get('mode', 'fail-open')}`")
    L.append(f"- Critical kind: {gate.get('critical_kind', CRITICAL_KIND)} DLL imports")
    L.append(f"- Known unresolved baseline entries: "
             f"{gate.get('known_unresolved_baseline_count', 0)}")
    L.append(f"- New unresolved critical imports: "
             f"{len(gate.get('new_unresolved_critical', []))} "
             "(non-zero means the auditor CLI exits 1)")
    L.append("")
    new_rows = gate.get("new_unresolved_critical", [])
    if new_rows:
        L.append("| DLL | API | Files |")
        L.append("|---|---|---|")
        for row in new_rows:
            L.append(f"| `{row['dll']}` | `{row['name']}` | {row['files']} |")
        L.append("")
    else:
        L.append("No new unresolved critical imports: the gate passes. Known "
                 "baseline gaps are still listed above as `not implemented`.")
        L.append("")
    recent = recently_registered_names()
    L.append(f"## Recently registered shims ({len(recent)}, last checkpoint)")
    L.append("")
    L.append("APIs first registered in the last integration checkpoint recorded "
             "in `docs/ISSUE_EXECUTION_PLAN.md` (the 10-agent coverage-refresh "
             "checkpoint). This section is additive: it reports the "
             "registration delta from the previous coverage checkpoint "
             "(2,249 shims), not extra coverage. **Registration only proves a "
             "name is present**; per-API selftests, not this list, are the "
             "behaviour evidence.")
    L.append("")
    for group, names in RECENTLY_REGISTERED_SHIMS.items():
        L.append(f"- {group}: " + ", ".join(f"`{n}`" for n in names) + ".")
    L.append("")
    L.append(f"## Missing Win32 system APIs ({len(system_missing)})")
    L.append("")
    L.append("| DLL | API | Files |")
    L.append("|---|---|---|")
    for dll, name, files in system_missing:
        L.append(f"| `{dll}` | `{name}` | {files} |")
    L.append("")
    L.append(f"## Game-owned DLL imports (top 30 of "
             f"{s['game_owned_missing_api']})")
    L.append("")
    L.append("These are a game's own DLLs or bundled redistributables, not "
             "Win32 system APIs. They are listed separately and never counted "
             "in the headline system number.")
    L.append("")
    L.append("| DLL | API | Files |")
    L.append("|---|---|---|")
    for dll, name, files in game_missing[:30]:
        L.append(f"| `{dll}` | `{name}` | {files} |")
    L.append("")
    L.append("## DLL classification")
    L.append("")
    L.append(f"System DLLs ({len(system_dlls)}): "
             + ", ".join(f"`{dll}`" for dll in system_dlls) + ".")
    L.append("")
    L.append(f"Game-owned DLLs ({len(game_dlls)}): "
             + ", ".join(f"`{dll}`" for dll in game_dlls) + ".")
    L.append("")
    L.append("## How to regenerate")
    L.append("")
    L.append("```sh")
    L.append("python3 -m tools.api_coverage \\")
    L.append("    --corpus /path/to/games \\")
    L.append("    --out reports/api-coverage.json \\")
    L.append("    --markdown docs/WIN32_API_COVERAGE.md")
    L.append("```")
    L.append("")
    L.append("Registration proves only that a name is present. This report "
             "makes no completion, gameplay, rendering, audio or full-coverage "
             "claim.")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", default=None,
                   help="directory of PE32 files (default: RECOMP_CORPUS env)")
    p.add_argument("--out", type=Path, default=ROOT / "reports/api-coverage.json")
    p.add_argument("--markdown", type=Path, default=None,
                   help="also write the human-readable doc from this report")
    args = p.parse_args(argv)
    corpus = args.corpus or __import__("os").environ.get("RECOMP_CORPUS")
    if not corpus:
        print("--corpus or RECOMP_CORPUS is required", file=sys.stderr)
        return 2
    report = build_report(Path(corpus), registered_shims())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report, corpus=corpus,
                                                 out=args.out))
    s = report["summary"]
    print(f"api coverage: {s['inputs']} inputs, "
          f"{s['distinct_missing_api']} system missing APIs, "
          f"{s['game_owned_missing_api']} game-owned missing APIs, "
          f"{s['ordinal_imports']} system ordinal imports -> {args.out}")
    if args.markdown:
        print(f"markdown coverage doc -> {args.markdown}")
    gate = report["gate"]
    new_critical = gate["new_unresolved_critical"]
    print(f"gate: {gate['mode']} ({gate['critical_kind']} DLL imports), "
          f"{len(new_critical)} new unresolved critical import(s)")
    if new_critical:
        # Write the report/markdown evidence first, then fail closed. A new
        # system-DLL gap must break the caller instead of being averaged away.
        for row in new_critical:
            print(f"FAIL-CLOSED: new unresolved critical import "
                  f"{row['dll']}!{row['name']} ({row['files']} file(s))",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
