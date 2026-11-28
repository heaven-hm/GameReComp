#!/usr/bin/env python3
"""Generate docs/GAME_ACCEPTANCE_TRACKER.md from the actual local corpus.

No hashes, versions, branches or verdicts are invented. Version fields are
"unknown" unless a version is derivable from the directory name. Statuses come
from reports/games/results.json, matched by executable filename.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path

import pefile

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Path("/Users/haseeb-mir/Downloads/recomp-apps")
RESULTS = ROOT / "reports/games/results.json"


def game_id(path: Path) -> str:
    rel = path.relative_to(CORPUS)
    parts = rel.parts
    if parts[0] == "games" and len(parts) > 1:
        return "game-" + parts[1].lower().replace(" ", "-")
    return parts[0].lower().replace(" ", "-")


def inspect_exe(path: Path) -> dict:
    data = path.read_bytes()
    row = {
        "filename": path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "arch": "INVALID",
        "graphics": [],
        "dlls": [],
    }
    try:
        pe = pefile.PE(str(path), fast_load=False)
    except Exception as exc:
        row["error"] = str(exc)[:120]
        return row
    magic = pe.OPTIONAL_HEADER.Magic
    row["arch"] = "PE32+" if magic == 0x20B else "PE32" if magic == 0x10B else f"unknown({magic:x})"
    row["machine"] = hex(pe.FILE_HEADER.Machine)
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dll = entry.dll.decode("utf-8", "replace").lower()
        row["dlls"].append(dll)
        if any(k in dll for k in ("d3d", "ddraw", "opengl", "sdl", "glide", "dxgi", "vulkan")):
            row["graphics"].append(dll)
    return row


def result_for(filename: str) -> dict:
    if not RESULTS.is_file():
        return {}
    data = json.loads(RESULTS.read_text())
    for row in data.get("runs", []):
        if row.get("filename", "").lower() == filename.lower():
            return row
    return {}


def branches_for(gid: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "branch", "--format=%(refname:short)"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
    except Exception:
        return "none"
    hits = [b for b in out if gid.replace("game-", "") in b.lower()]
    return ", ".join(hits) if hits else "none"


def main() -> None:
    groups = defaultdict(list)
    for path in sorted(CORPUS.rglob("*.exe")):
        groups[game_id(path)].append(path)

    records = []
    for gid, paths in sorted(groups.items()):
        inspected = [(p, inspect_exe(p)) for p in paths]
        eligible = [(p, r) for p, r in inspected if r["arch"] == "PE32"]
        chosen = eligible or inspected
        # Prefer an executable that has a measured repo result, then the largest.
        chosen.sort(key=lambda pr: (bool(result_for(pr[0].name)), pr[1]["size"]), reverse=True)
        path, info = chosen[0]
        res = result_for(path.name)
        if res:
            status = res.get("verification", "unknown")
            detail = (res.get("status") or "")[:90]
        else:
            status, detail = "UNTESTED", "no row in reports/games/results.json"
        records.append({
            "game_id": gid,
            "version": "unknown (not derivable from file)",
            "executable": str(path.relative_to(CORPUS)),
            "sha256": info["sha256"],
            "arch": info["arch"],
            "graphics": ", ".join(sorted(set(info["graphics"]))) or "none detected",
            "dlls": ", ".join(sorted(set(info["dlls"]))),
            "recompilation_status": status,
            "status_detail": detail,
            "evidence_dir": f"test-results/{gid}/",
            "branch": branches_for(gid),
            "commit": "uncommitted",
            "executables_found": len(paths),
            "pe32_found": len(eligible),
        })

    out = ROOT / "test-results/_inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2) + "\n")
    md = [
        "# Game acceptance tracker",
        "",
        "Generated from the actual files under `/Users/haseeb-mir/Downloads/recomp-apps`.",
        "No hash, version, branch, commit or verdict is invented here. `unknown` means",
        "the value is not derivable from the available file. Statuses are copied from",
        "`reports/games/results.json` and are API/process-level, never gameplay proof.",
        "",
        f"Directories discovered: **{len(records)}**, PE32 executables found: "
        f"**{sum(r['pe32_found'] for r in records)}**.",
        "",
        "| Game | Version | Executable | SHA-256 | Arch | Graphics | Recompilation | Evidence | Branch |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        md.append("| {gid} | {ver} | `{exe}` | `{sha}` | {arch} | {gfx} | {status} | `{ev}` | {br} |".format(
            gid=r["game_id"], ver=r["version"], exe=r["executable"], sha=r["sha256"][:16] + "…",
            arch=r["arch"], gfx=r["graphics"], status=r["recompilation_status"],
            ev=r["evidence_dir"], br=r["branch"]))
    md += ["", "## Verdict vocabulary", "",
           "PASS / PARTIAL / FAIL / BLOCKED / UNTESTABLE. A compile or launch alone is never PASS.",
           "", "Full hashes and DLL lists: `test-results/_inventory.json`.", ""]
    (ROOT / "docs/GAME_ACCEPTANCE_TRACKER.md").write_text("\n".join(md))
    print(f"wrote tracker for {len(records)} game directories")


if __name__ == "__main__":
    main()
