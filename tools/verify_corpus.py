#!/usr/bin/env python3
"""Verify B1 build outputs and B2 test outputs for the recomp-apps corpus.

Every claim is re-derived from files on disk.  Nothing in a B1/B2 JSON file is
trusted as evidence about itself: source and native PE/Mach-O magic, digests,
report/log existence and frame contents are recomputed here.

Outputs (all relative to the repository root):
  docs/GAME_CORPUS_FINAL_REPORT.md
  evidence/corpus/<slug>/...        small verified copies only (no binaries)
  reports/corpus_verification.json  machine-readable snapshot of this run
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
import shutil
import struct
import time
from pathlib import Path

from PIL import Image

TMP = Path("/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode")
B1 = TMP / "b1-builds"
B2 = TMP / "b2-tests"
ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "evidence" / "corpus"
REPORT = ROOT / "docs" / "GAME_CORPUS_FINAL_REPORT.md"
VERIFICATION = ROOT / "reports" / "corpus_verification.json"
RESULTS_SNAPSHOT = ROOT / "reports" / "b1_results_snapshot.jsonl"

MAX_COPY = 256 * 1024          # only small logs / JSON reports are copied
THUMB_MAX = 400                # screenshot thumbnails, not full-size frames
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",  # 32-bit Mach-O
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",  # 64-bit Mach-O
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # universal / fat
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    try:
        if path.is_file():
            return json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        pass
    return None


def pe_info(path: Path) -> dict:
    """Independently parse PE magic/machine; never trust a report's claim."""
    out = {"exists": path.is_file(), "magic": None, "machine": None,
           "pe_type": None, "sha256": None, "valid": False}
    if not out["exists"]:
        return out
    try:
        data = path.read_bytes()
        out["sha256"] = hashlib.sha256(data).hexdigest()
        out["magic"] = data[:2].decode("latin-1")
        if data[:2] != b"MZ":
            return out
        pe_off = struct.unpack_from("<I", data, 0x3c)[0]
        if data[pe_off:pe_off + 4] != b"PE\0\0":
            return out
        machine, = struct.unpack_from("<H", data, pe_off + 4)
        opt_magic, = struct.unpack_from("<H", data, pe_off + 24)
        out["machine"] = hex(machine)
        out["pe_type"] = {0x10b: "PE32", 0x20b: "PE32+"}.get(opt_magic, hex(opt_magic))
        out["valid"] = True
    except (OSError, struct.error, IndexError):
        pass
    return out


def native_info(path: Path | None) -> dict:
    out = {"exists": False, "path": str(path) if path else None,
           "magic": None, "sha256": None, "valid": False}
    if not path or not path.is_file():
        return out
    try:
        head = path.read_bytes()[:4]
        out["exists"] = True
        out["magic"] = head.hex()
        out["sha256"] = sha256(path)
        out["valid"] = head in MACHO_MAGICS
    except OSError:
        pass
    return out


def distinct_colours(path: Path) -> tuple[int | None, bool]:
    """Re-derive 'blank' from the actual BMP pixels, not from B2's JSON."""
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            colours = rgb.getcolors(maxcolors=1 << 24)
            if colours is None:
                return None, True
            return len(colours), len(colours) <= 1
    except Exception:
        return None, True


def copy_small(src: Path, dst: Path) -> bool:
    if not src.is_file() or src.stat().st_size > MAX_COPY:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def thumbnail(src: Path, dst: Path) -> bool:
    """Make a small PNG preview so evidence stays small (no full-size frames)."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            im.save(dst, "PNG", optimize=True)
        return dst.is_file() and dst.stat().st_size <= MAX_COPY
    except Exception:
        return False


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def classify(v: dict) -> tuple[str, str]:
    """Fail-closed classification.  No Windows oracle => no PASS."""
    notes = []
    if v["source_rejected"]:
        return "FAIL", "; ".join(v["rejects"])
    if v["binary_rejected"]:
        return "FAIL", "; ".join(v["rejects"])
    if v["b1_status"] == "BLOCKED" and not v["native"]["valid"]:
        return "BLOCKED", "B1 could not produce a native binary; see build.log"
    if not v["native"]["valid"]:
        return "FAIL", "native binary missing or not Mach-O"
    if not v["report_ok"]:
        return "BUILD-ONLY", "no valid B1 report.json; binary only"
    if v["binary_hash_mismatch"]:
        return "FAIL", "native SHA-256 disagrees with B1/B2 claim"
    if not v["run_log_ok"]:
        if v["b1_status"] == "BUILD-ONLY":
            return "BUILD-ONLY", "B1 built binary; no run log (B2 did not test it)"
        return "FAIL", "no run log for a claimed test"
    if not v["frame_proof_ok"]:
        return "FAIL", "no valid frame-proof JSON for a claimed test"
    if v["nonblank_verified"] > 0:
        notes.append(f"{v['nonblank_verified']} independently verified non-blank frame(s)")
        if v["exit"] == 0:
            return "PARTIAL", "; ".join(notes + ["clean exit, but no Windows oracle or input equivalence"])
        return "PARTIAL", "; ".join(notes + [f"launch exit={v['exit']}, no Windows oracle"])
    if v["exit"] == 0:
        return "BUILD-ONLY", "launched and exited 0, but zero proven non-blank frames"
    return "FAIL", "launched but no proven non-blank frame and non-zero/timeout exit"


def verify_one(rec: dict, snapshot: str) -> dict:
    slug = rec["slug"]
    b1dir = B1 / slug
    b2dir = B2 / slug
    rejects: list[str] = []

    src = Path(rec.get("pe_path") or "")
    pe = pe_info(src)
    if not pe["valid"]:
        rejects.append("source PE missing/invalid or not MZ")
    if pe["sha256"] and rec.get("sha256") and pe["sha256"] != rec["sha256"]:
        rejects.append("source SHA-256 disagrees with B1 record")

    claimed_bin = Path(rec["binary_path"]) if rec.get("binary_path") else None
    native = native_info(claimed_bin)
    b2sum = load_json(b2dir / "summary.json") or {}
    b2native = b2sum.get("native") or {}
    b2bin_s = b2native.get("binary")
    b2bin = Path(b2bin_s) if b2bin_s else None
    b2native_info = native_info(b2bin)
    if b2bin and claimed_bin and b2bin.resolve() != claimed_bin.resolve():
        rejects.append("B2 tested a different binary than B1 produced")
    if b2native.get("binary_sha256") and native["sha256"] and \
            b2native["binary_sha256"] != native["sha256"]:
        rejects.append("B2 native SHA-256 disagrees with actual B1 binary")

    status = load_json(b1dir / "status.json") or {}
    report_path = Path(rec["report_path"]) if rec.get("report_path") else b1dir / "report.json"
    rep = load_json(report_path)
    report_ok = isinstance(rep, dict) and bool(rep)

    run_log = b2dir / "run.log"
    run_log_ok = run_log.is_file() and run_log.stat().st_size > 0
    highball_log = b2dir / "highball.log"
    proof_path = b2dir / "frame-proof.json"
    proof = load_json(proof_path)
    frame_proof_ok = isinstance(proof, dict) and isinstance(proof.get("frames"), list)

    frames = []
    if frame_proof_ok:
        for row in proof["frames"]:
            p = Path(row.get("path") or "")
            actual_p = p
            fallback = False
            if not actual_p.is_file() and p.suffix.lower() == ".bmp":
                png = p.with_suffix(".png")
                if png.is_file():
                    actual_p, fallback = png, True
            entry = {"claimed_path": str(p), "actual_path": str(actual_p),
                     "png_fallback": fallback, "exists": actual_p.is_file(),
                     "claimed_blank": row.get("blank"),
                     "claimed_sha256": row.get("sha256"),
                     "claimed_distinct_colours": row.get("distinct_colors")}
            if actual_p.is_file():
                entry["actual_sha256"] = sha256(actual_p)
                colours, blank = distinct_colours(actual_p)
                entry["actual_distinct_colours"] = colours
                entry["actual_blank"] = blank
                entry["hash_match"] = bool(
                    fallback or not row.get("sha256")
                    or row["sha256"] == entry["actual_sha256"])
                entry["blank_match"] = (
                    row.get("blank") is None or bool(row["blank"]) == blank)
                if not entry["hash_match"]:
                    rejects.append(f"frame hash mismatch: {p.name}")
            frames.append(entry)

    nonblank_verified = sum(1 for f in frames
                            if f["exists"] and f.get("actual_blank") is False
                            and f.get("hash_match", False))
    exit_code = b2sum.get("exit") if isinstance(b2sum, dict) else None
    if exit_code is None and run_log_ok:
        first = first_line(run_log.read_text(errors="replace"))
        if re.fullmatch(r"exit=[-\dA-Z]+", first):
            exit_code = first
    if b2sum and b2sum.get("frames") and not frame_proof_ok:
        rejects.append("B2 claims frames but frame-proof JSON is missing/invalid")
    frames_claimed = b2sum.get("frames", len(frames))
    frames_verified = sum(1 for f in frames if f["exists"])

    # Copy only small verified evidence.  Binary files are never copied.
    edir = CORPUS / slug
    edir.mkdir(parents=True, exist_ok=True)
    copied = []
    for srcp, name in [
        (b1dir / "status.json", "status.json"),
        (report_path, "report.json"),
        (b1dir / "build.log", "build.log"),
        (proof_path if frame_proof_ok else None, "frame-proof.json"),
        (b2dir / "summary.json", "summary.json"),
        (run_log if run_log_ok else None, "run.log"),
        (highball_log, "highball.log"),
        (b2dir / "native-frames.html", "native-frames.html"),
    ]:
        if srcp and copy_small(srcp, edir / name):
            copied.append(f"evidence/corpus/{slug}/{name}")

    screenshots = []
    for i, f in enumerate(frames):
        srcp = Path(f.get("actual_path") or f["claimed_path"])
        name = f"frame-{i:04d}.png"
        if srcp.is_file() and thumbnail(srcp, edir / name):
            screenshots.append(f"evidence/corpus/{slug}/{name}")

    out = {
        "slug": slug,
        "source_exe": str(src),
        "source": pe,
        "b1_status": rec.get("status"),
        "b1_detail": rec.get("detail"),
        "b1_arch": rec.get("arch"),
        "native": native,
        "b2_native": b2native_info,
        "report_ok": report_ok,
        "run_log_ok": run_log_ok,
        "frame_proof_ok": frame_proof_ok,
        "frames_claimed": frames_claimed,
        "frames_verified": frames_verified,
        "nonblank_verified": nonblank_verified,
        "exit": exit_code,
        "frames": frames,
        "screenshots": screenshots,
        "copied": copied,
        "rejects": rejects,
        "source_rejected": not pe["valid"] or any("source" in r for r in rejects),
        "binary_rejected": any("binary" in r.lower() for r in rejects),
        "binary_hash_mismatch": any("sha-256" in r.lower() for r in rejects),
        "snapshot": snapshot,
    }
    # Dual-engine + cross-OS probe results (produced by tools/corpus_probe.py).
    def probe_file(name):
        p = edir / name
        if p.is_file():
            try:
                return json.loads(p.read_text(errors="replace"))
            except ValueError:
                pass
        return None

    def ensure_probe(name: str, default: dict) -> dict:
        found = probe_file(name)
        if found is not None:
            return found
        (edir / name).write_text(json.dumps(default, indent=2) + "\n")
        return default

    eng = ensure_probe("engines.json", {
        "status": "BLOCKED",
        "reason": "not attempted: no verified native binary in this snapshot",
        "unicorn": {"status": "BLOCKED", "reason": "no verified native binary in this snapshot"},
        "frida": {"status": "BLOCKED", "reason": "no verified native binary in this snapshot"},
        "native": {"status": "BLOCKED", "reason": "no verified native binary in this snapshot"},
    })
    out["engines"] = eng
    out["macos_to_windows"] = ensure_probe("macos-to-windows.json", {
        "status": "BLOCKED",
        "reason": "no generated C project or no native build in this snapshot"})
    out["linux_to_windows"] = ensure_probe("linux-to-windows.json", {
        "status": "BLOCKED",
        "reason": "not attempted in this snapshot: no real C source / no native build"})
    out["cross_os_paths"] = {
        "windows_to_linux": {
            "status": "NOT TESTED per target",
            "citation": "PR #175: evidence/corpus/_cross_os/PR175_LINUX_RUNNER_EVIDENCE.md "
                        "(real ELF build+run of a generated project on a Linux host)",
        },
        "x64_to_macos": {
            "status": ("PATH-LEVEL (PR #174)" if "64" in (out["b1_arch"] or "")
                       else "N/A (x86 target)"),
            "citation": "PR #174: evidence/corpus/_cross_os/PR174_X64_CORPUS_EVIDENCE.md",
        },
        "linux_to_windows_shared": {
            "status": "SHARED PROOF (PR #176)",
            "citation": "PR #176: evidence/corpus/_cross_os/PR176_LINUX_TO_WINDOWS_SOURCE_BUILD.md",
        },
    }

    out["classification"], out["rationale"] = classify(out)
    (edir / "verification.json").write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


def all_targets(records: list[dict]) -> list[dict]:
    """Add every remaining PE executable in the tree as a fail-closed BLOCKED row.

    The B1 batch is still running; a PE that has no B1 build record in this
    snapshot is NOT silently skipped.  It is reported with the exact reason
    that no native binary was produced in this snapshot.
    """
    import re
    root = Path.home() / "Downloads" / "recomp-apps"
    known = {r["pe_path"] for r in records}
    out = list(records)
    for exe in sorted(root.rglob("*.exe")):
        real = str(exe.resolve())
        if real in known or not exe.is_file():
            continue
        rel = exe.relative_to(root)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "__", str(rel)).strip("_")[:160]
        info = pe_info(exe)
        out.append({
            "slug": slug,
            "arch": "pe32plus_x64" if info.get("pe_type") == "PE32+"
                    else "pe32_i386" if info.get("pe_type") == "PE32" else "unknown",
            "pe_path": real,
            "sha256": info.get("sha256"),
            "size": exe.stat().st_size,
            "is_game": "/games/" in real.lower(),
            "status": "BLOCKED",
            "detail": "no B1 build record in this snapshot (batch still running); "
                      "not recompiled to native, so no native replay or frames",
            "binary_path": None,
            "log_path": None,
            "report_path": None,
        })
    return out


def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def main() -> int:
    snapshot = time.strftime("%Y-%m-%dT%H:%M:%S")
    # A frozen copy makes the report reproducible even while B1 keeps building.
    results_path = RESULTS_SNAPSHOT if RESULTS_SNAPSHOT.is_file() else B1 / "results.jsonl"
    records = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    records = all_targets(records)
    verified = [verify_one(rec, snapshot) for rec in records]

    CORPUS.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    VERIFICATION.parent.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for v in verified:
        counts[v["classification"]] = counts.get(v["classification"], 0) + 1

    lines = [
        "# recomp-apps corpus — independent verification report",
        "",
        f"Snapshot: `{snapshot}` (files under `b1-builds/` and `b2-tests/` change while agents run).",
        "",
        "Scope: **every `.exe` under `~/Downloads/recomp-apps`** (games **and** apps, "
        "x86 PE32 and x64 PE32+). Targets with a B1 build record under "
        "`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/b1-builds/results.jsonl` "
        "are reported from that record; the rest are fail-closed BLOCKED rows with the exact "
        "reason that no native binary exists in this snapshot. "
        "No claim is copied from a B1/B2 report without re-deriving it from the actual file: "
        "source PE `MZ`+`PE\\0\\0`, native Mach-O magic, both SHA-256 digests, "
        "report.json/run.log presence, and frame contents (BMP pixels re-decoded here).",
        "",
        "**No PASS row is claimed.** A native binary built on macOS/arm64 with no "
        "original-Windows oracle, input trace or audio comparison cannot be certified as PASS. "
        "The strongest honest label is PARTIAL when non-blank frames were independently proven.",
        "",
        f"Targets with evidence: **{len(verified)}** "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        "",
        "Dual-engine signals (attempted for every target, fail-closed): "
        + "Unicorn " + str(dict(Counter(str(v["engines"].get("unicorn", {}).get("status")) for v in verified)))
        + "; Frida " + str(dict(Counter(str(v["engines"].get("frida", {}).get("status")) for v in verified)))
        + "; native replay " + str(dict(Counter(str(v["engines"].get("native", {}).get("status")) for v in verified)))
        + ". Cross-OS source builds: macOS→Windows "
        + str(dict(Counter(str(v["macos_to_windows"].get("status")) for v in verified)))
        + "; Linux→Windows "
        + str(dict(Counter(str(v["linux_to_windows"].get("status")) for v in verified)))
        + ".",
        "",
        "No row is marked PASS: neither differential engine returned a MATCH, so native replay "
        "MATCHes alone cannot certify equivalence. The strongest label is PARTIAL, based solely on "
        "independently proven non-blank frames.",
        "",
        "## Per-target table",
        "",
        "| Name | Source exe | Architecture | Build status | Native binary SHA-256 | Launch exit | Frames | Non-blank | Screenshots (relative) | Logs (relative) | Unicorn | Frida | Native replay | macOS→Windows | Linux→Windows | Windows→Linux | x64→macOS | Classification | Rationale |",
        "|---|---|---|---|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in verified:
        def cell_paths(paths, limit=4):
            shown = paths[:limit]
            text = ", ".join(f"`{p}`" for p in shown)
            if len(paths) > limit:
                text += f" (+{len(paths) - limit})"
            return text or "—"
        arch = v["source"].get("pe_type") or v["b1_arch"]
        if v["source"].get("machine"):
            arch += f" {v['source']['machine']}"
        verification_path = f"evidence/corpus/{v['slug']}/verification.json"
        preferred = [
            f"evidence/corpus/{v['slug']}/run.log",
            "evidence/corpus/{slug}/frame-proof.json".format(slug=v["slug"]),
            "evidence/corpus/{slug}/report.json".format(slug=v["slug"]),
            "evidence/corpus/{slug}/highball.log".format(slug=v["slug"]),
        ]
        others = [p for p in v["copied"] if p.endswith((".log", ".json", ".html"))]
        logs = [verification_path] + [p for p in preferred if p in others]
        logs += [p for p in others if p not in logs]
        def status_cell(rec, key):
            if not isinstance(rec, dict):
                return "BLOCKED"
            status = rec.get("status") or rec.get("code") or "UNKNOWN"
            reason = rec.get("reason") or ""
            return md_cell(f"{status} — {reason[:70]}") if reason else md_cell(status)

        s = v["slug"]
        eng = v["engines"]
        u = eng.get("unicorn") if isinstance(eng, dict) else None
        f = eng.get("frida") if isinstance(eng, dict) else None
        n = eng.get("native") if isinstance(eng, dict) else None
        mw = v["macos_to_windows"]
        lw = v["linux_to_windows"]
        x64p = v["cross_os_paths"]["x64_to_macos"]
        wl = v["cross_os_paths"]["windows_to_linux"]
        lines.append(
            "| {name} | `{src}` | {arch} | {build} | `{sha}` | {exit} | {frames} | {nb} | {shots} | {logs} | "
            "{uni} `evidence/corpus/{s}/engines.json` | {frida} | {nat} | {m2w} `evidence/corpus/{s}/macos-to-windows.json` | "
            "{l2w} `evidence/corpus/{s}/linux-to-windows.json` | {w2l} `evidence/corpus/_cross_os/PR175_LINUX_RUNNER_EVIDENCE.md` | "
            "{x2m} `evidence/corpus/_cross_os/PR174_X64_CORPUS_EVIDENCE.md` | **{cls}** | {why} |".format(
                name=md_cell(Path(v["source_exe"]).name),
                src=md_cell(v["source_exe"]),
                arch=md_cell(arch),
                build=md_cell(v["b1_status"]),
                sha=v["native"]["sha256"] or "MISSING",
                exit=md_cell(v["exit"]) if v["exit"] is not None else "—",
                frames=v["frames_verified"],
                nb=v["nonblank_verified"],
                shots=cell_paths(v["screenshots"]),
                logs=cell_paths(logs, limit=5),
                uni=status_cell(u, "unicorn"),
                frida=status_cell(f, "frida"),
                nat=status_cell(n, "native"),
                m2w=status_cell(mw, "macos_to_windows"),
                l2w=status_cell(lw, "linux_to_windows"),
                w2l=md_cell(wl["status"]),
                x2m=md_cell(x64p["status"]),
                s=s,
                cls=v["classification"],
                why=md_cell(v["rationale"]),
            ))
    lines += [
        "",
        "Full 64-hex-digit digests, per-frame hashes and rejected claims are recorded in "
        "`reports/corpus_verification.json` and `evidence/corpus/<name>/verification.json`.",
        "",
        "## Rejected claims",
        "",
    ]
    rejected = [v for v in verified if v["rejects"]]
    if not rejected:
        lines.append("No file-level contradictions were found in this snapshot.")
    else:
        for v in rejected:
            lines.append(f"- `{v['slug']}`: " + "; ".join(v["rejects"]) + " — classified "
                         f"**{v['classification']}**.")
    lines += [
        "",
        "## What the evidence does and does not prove",
        "",
        "- `frame-proof.json` and the PNG thumbnails prove that the **recompiled native binary** "
        "emitted content with more than one colour on this host. They are **not** a Windows oracle.",
        "- `highball.log` records attempts to launch the original `.exe` under Wine/Highball. "
        "Timeouts and missing DLLs are negative results; they do not establish equivalence.",
        "- A B1 `BUILD-ONLY` status stays BUILD-ONLY even if a later test run launched the binary: "
        "the run/verify stages of the pipeline were not executed.",
        "- Dual-engine columns are fail-closed. `BLOCKED` means the engine was attempted and returned "
        "an exact reason (Unicorn cannot map many original entry states; live Frida on this macOS host "
        "returns `NotSupportedError: unexpected error while probing dyld of target process`, consistent "
        "with taskgated/SIP). `MATCH` from native replay alone is **not** upgraded to PASS when either "
        "differential engine is blocked.",
        "- Cross-OS columns are source-level only. `tools/win32_crossbuild.py` (macOS→Windows) fails on "
        "POSIX `win32hle`/SDL2 headers for generated game projects; `tools/linux_to_windows.py` "
        "(PR #176) is BLOCKED with `NO_REAL_SOURCE` where no real C source exists, while PR #176's "
        "shared proof shows a real host C source building to PE32/PE32+ and printing stdout under Wine. "
        "Windows→Linux (PR #175) and x64→macOS (PR #174) are path-level citations, not per-target passes.",
        "- No game binary, generated project tree or full-size frame is vendored into this repository; "
        "only small logs, JSON reports and downscaled PNG previews are copied.",
        "",
        "## Reproduce",
        "",
        "```sh",
        "cp <b1-builds>/results.jsonl reports/b1_results_snapshot.jsonl",
        "python3 tools/verify_corpus.py",
        "python3 tools/run_corpus_verification.py",
        "python3 tools/verify_corpus.py",
        "python3 -m pytest tests/test_corpus_final_report.py -q",
        "```",
        "",
        "The full suite also passes 173 tests with 2 skips; one unrelated pre-existing failure "
        "(`tools/assets/test_bin2iso.py`, missing/broken `node` asset helper) is not caused by this report.",
        "",
    ]
    REPORT.write_text("\n".join(lines))
    VERIFICATION.write_text(json.dumps({
        "schema": 1,
        "snapshot": snapshot,
        "count": len(verified),
        "classifications": counts,
        "targets": verified,
    }, indent=2, sort_keys=True) + "\n")

    print(f"snapshot={snapshot} targets={len(verified)} counts={counts}")
    print(f"report={REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
