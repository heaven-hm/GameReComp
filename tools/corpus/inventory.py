"""Inventory PE executables in locally supplied game trees.

Reads corpus roots from SMALL_GAMES_PATH / LARGE_GAMES_PATH (or --small /
--large) and writes metadata only: relative path, size, hashes and PE header
facts. Binaries are never copied, and a root that is unset or absent is
reported as MISSING_INPUT rather than guessed at.

Usage:
    python -m tools.corpus.inventory --json reports/corpus/inventory.json \
        --markdown reports/corpus/inventory.md
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PE = os.path.join(_TOOLS, "pe")
if _PE not in sys.path:
    sys.path.insert(0, _PE)

from pe_analyze import analyze_pe  # noqa: E402


SCHEMA_VERSION = 1
SOURCES = (
    ("small_games", "SMALL_GAMES_PATH"),
    ("large_games", "LARGE_GAMES_PATH"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(root: Path, path: Path, source: str) -> dict:
    rel = path.relative_to(root).as_posix()
    try:
        stat = path.stat()
    except OSError as exc:
        return {
            "source": source,
            "relative_path": rel,
            "status": "error",
            "error": f"stat failed: {exc}",
        }

    entry = {
        "source": source,
        "relative_path": rel,
        "size": stat.st_size,
        "sha256": _sha256(path),
    }
    try:
        info = analyze_pe(str(path), do_hashes=True)
    except Exception as exc:  # pefile raises PEFormatError; the struct fallback can too
        entry["status"] = "invalid-pe"
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    entry.update({
        "status": "ok",
        "md5": info.md5,
        "architecture": info.machine,
        "pe_type": info.pe_type,
        "subsystem": info.subsystem,
        "is_dll": info.is_dll,
        "image_base": f"0x{info.image_base:08X}",
        "entry_point_rva": f"0x{info.entry_point_rva:08X}",
        "linker_version": info.linker_version,
        "timestamp_str": info.timestamp_str,
        "code_start": f"0x{info.code_start:08X}",
        "code_end": f"0x{info.code_end:08X}",
        "import_dlls": list(info.import_dlls),
        "import_count": len(info.imports),
    })
    return entry


def _source_record(name: str, env_name: str, root: str | None) -> dict:
    if not root:
        return {
            "source": name,
            "env": env_name,
            "path": None,
            "status": "MISSING_INPUT",
            "detail": f"{env_name} is not set",
        }
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        return {
            "source": name,
            "env": env_name,
            "path": str(root_path),
            "status": "MISSING_INPUT",
            "detail": f"{env_name} points at a path that is not a directory",
        }
    return {
        "source": name,
        "env": env_name,
        "path": str(root_path.resolve()),
        "status": "ok",
        "detail": "",
    }


def build_inventory(roots: dict[str, str | None]) -> dict:
    generated = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    report = {
        "schema": SCHEMA_VERSION,
        "generated_at": generated,
        "binaries_included": False,
        "sources": [],
        "entries": [],
    }

    for name, env_name in SOURCES:
        record = _source_record(name, env_name, roots.get(name))
        report["sources"].append(record)
        if record["status"] != "ok":
            continue
        root = Path(record["path"])
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".exe":
                report["entries"].append(_entry(root, path, name))

    entries = report["entries"]
    by_arch: dict[str, int] = {}
    for entry in entries:
        key = entry.get("architecture", "unknown") or "unknown"
        by_arch[key] = by_arch.get(key, 0) + 1
    report["summary"] = {
        "total": len(entries),
        "ok": sum(1 for e in entries if e["status"] == "ok"),
        "invalid": sum(1 for e in entries if e["status"] != "ok"),
        "by_architecture": dict(sorted(by_arch.items())),
        "by_source": {
            name: sum(1 for e in entries if e["source"] == name)
            for name, _ in SOURCES
        },
    }
    return report


def render_markdown(report: dict) -> str:
    lines = [
        "# Corpus inventory",
        "",
        f"Generated: {report['generated_at']}  ",
        "Metadata only; no game binaries or assets are stored in this repository.",
        "",
        "## Sources",
        "",
        "| Source | Env | Path | Status |",
        "|---|---|---|---|",
    ]
    for source in report["sources"]:
        detail = source["detail"] or "found"
        path = source["path"] or "-"
        lines.append(
            f"| {source['source']} | `{source['env']}` | `{path}` | "
            f"{source['status']} ({detail}) |")

    lines += ["", "## Executables", ""]
    if not report["entries"]:
        lines.append("_No executables discovered._")
    else:
        lines += [
            "| Source | Relative path | Size | SHA-256 | Arch | PE | Subsystem |",
            "|---|---|---:|---|---|---|---|",
        ]
        for entry in report["entries"]:
            if entry["status"] == "ok":
                lines.append(
                    f"| {entry['source']} | `{entry['relative_path']}` | "
                    f"{entry['size']} | `{entry['sha256'][:16]}...` | "
                    f"{entry['architecture']} | {entry['pe_type']} | "
                    f"{entry['subsystem']} |")
            else:
                lines.append(
                    f"| {entry['source']} | `{entry['relative_path']}` | "
                    f"{entry.get('size', '-')} | `{entry['sha256'][:16]}...` | "
                    f"**{entry['status']}** | - | - |")

    summary = report["summary"]
    lines += [
        "",
        "## Summary",
        "",
        f"- Executables: {summary['total']} "
        f"({summary['ok']} parsed, {summary['invalid']} invalid)",
        "- Architectures: " + ", ".join(
            f"{arch}={count}" for arch, count in summary["by_architecture"].items())
        or "- Architectures: none",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--small", default=os.environ.get("SMALL_GAMES_PATH"),
                        help="small-games root (default: $SMALL_GAMES_PATH)")
    parser.add_argument("--large", default=os.environ.get("LARGE_GAMES_PATH"),
                        help="large-games root (default: $LARGE_GAMES_PATH)")
    parser.add_argument("--json", default="reports/corpus/inventory.json",
                        help="write the JSON inventory here")
    parser.add_argument("--markdown", default="reports/corpus/inventory.md",
                        help="write the Markdown summary here")
    args = parser.parse_args(argv)

    report = build_inventory({"small_games": args.small, "large_games": args.large})

    for target, content in (
        (args.json, json.dumps(report, indent=2) + "\n"),
        (args.markdown, render_markdown(report)),
    ):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    summary = report["summary"]
    print(f"[*] Executables: {summary['total']} "
          f"({summary['ok']} parsed, {summary['invalid']} invalid)")
    for source in report["sources"]:
        print(f"[*] {source['source']}: {source['status']}"
              + (f" - {source['detail']}" if source["detail"] else ""))
    print(f"[*] Wrote {args.json} and {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
