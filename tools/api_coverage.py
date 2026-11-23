#!/usr/bin/env python3
"""Machine-readable Win32 API coverage auditor for PE32 import tables.

Reads imports from real PE32 files, compares them with the shim names actually
registered in runtime/win32hle/*.c, and reports implemented / missing imports
and ordinal imports. No API is called "supported" merely because a DLL name
appears in a registry: only a registered shim name counts as implemented, and
the report says so.

    python3 -m tools.api_coverage --corpus /path/to/games --out reports/api-coverage.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "win32hle"

# Table forms used by the runtime: { "name", fn }, SHIM2(ord, "name", fn),
# AUT(ord, "name", fn), SHIM1(...). String literals in comments are excluded
# by requiring a comma after the quote.
SHIM_PATTERNS = [
    re.compile(r'\{\s*"([A-Za-z0-9_?#@.$]+)"\s*,'),
    re.compile(r'\bSHIM\d*\s*\(\s*\d+\s*,\s*"([A-Za-z0-9_?#@.$]+)"'),
    re.compile(r'\bAUT\s*\(\s*\d+\s*,\s*"([A-Za-z0-9_?#@.$]+)"'),
]
NON_API = {"win32hle.h", "README"}


def registered_shims(runtime_dir: Path = RUNTIME) -> set[str]:
    names = set()
    for path in sorted(Path(runtime_dir).glob("*.c")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SHIM_PATTERNS:
            names.update(pattern.findall(text))
    return names - NON_API


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


def build_report(corpus: Path, shims: set[str]) -> dict:
    corpus = Path(corpus)
    inputs = {}
    all_missing = Counter()
    all_ordinal = Counter()
    all_dlls = Counter()
    for path in sorted(corpus.rglob("*.exe")):
        try:
            data = path.read_bytes()
            imports = pe_imports(path)
        except Exception as exc:
            continue
        digest = hashlib.sha256(data).hexdigest()
        missing = {}
        ordinals = {}
        for dll, names in imports.items():
            all_dlls[dll] += 1
            for name in names:
                if name.startswith("#"):
                    ordinals.setdefault(dll, []).append(name)
                    all_ordinal[dll] += 1
                elif name not in shims:
                    missing.setdefault(dll, []).append(name)
                    all_missing[(dll, name)] += 1
        inputs[digest] = {
            "input_sha256": digest,
            "path": str(path),
            "imports": imports,
            "missing": missing,
            "ordinals": ordinals,
        }
    return {
        "schema_version": "1.0.0",
        "tool": "python3 -m tools.api_coverage",
        "what": "PE32 import coverage against registered win32hle shims",
        "is_not": "proof that a function behaves correctly; only that it is registered",
        "registered_shim_count": len(shims),
        "summary": {
            "inputs": len(inputs),
            "distinct_dlls": len(all_dlls),
            "distinct_missing_api": len(all_missing),
            "ordinal_imports": sum(all_ordinal.values()),
        },
        "missing_by_api": [
            {"dll": dll, "name": name, "files": count}
            for (dll, name), count in all_missing.most_common()
        ],
        "inputs": inputs,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", default=None,
                   help="directory of PE32 files (default: RECOMP_CORPUS env)")
    p.add_argument("--out", type=Path, default=ROOT / "reports/api-coverage.json")
    args = p.parse_args(argv)
    corpus = args.corpus or __import__("os").environ.get("RECOMP_CORPUS")
    if not corpus:
        print("--corpus or RECOMP_CORPUS is required", file=sys.stderr)
        return 2
    report = build_report(Path(corpus), registered_shims())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"api coverage: {report['summary']['inputs']} inputs, "
          f"{report['summary']['distinct_missing_api']} missing APIs, "
          f"{report['summary']['ordinal_imports']} ordinal imports -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
