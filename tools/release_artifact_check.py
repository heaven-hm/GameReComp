"""Verify a release directory against a CLI-supplied SHA256SUMS manifest.

Every listed artifact is re-hashed from disk and classified as OK, MISSING or
HASH_MISMATCH; every unlisted file in the artifact directory is classified as
EXTRA. A release is reported complete only when the manifest is well formed,
lists at least one artifact, every artifact is OK and there are no extra files.
Reports contain no timestamps or absolute paths and are sorted, so identical
inputs always produce identical JSON and Markdown. Nothing is fetched from the
network and no paths are built in.

Usage:
    python3 -m tools.release_artifact_check \
        --sums /path/to/SHA256SUMS --artifact-dir /path/to/dist \
        --json report.json --markdown report.md

Exit codes: 0 = complete, 1 = any failed check or invalid input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
CHUNK_SIZE = 1 << 20
# Prefer the canonical two-character separator ("  " or " *") so a file name
# that itself begins with '*' is not mistaken for the binary-mode marker.
_HASH_LINE = re.compile(r"^([0-9a-fA-F]{64})(?:[ \t][ \t*]|[ \t]+)(.+)$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:[\\/]")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unescape(name: str) -> str:
    """Decode GNU coreutils escapes used when a file name contains \\ or \\n."""
    out: list[str] = []
    index = 0
    escapes = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(name):
        char = name[index]
        if (char == "\\" and index + 1 < len(name)
                and name[index + 1] in escapes):
            out.append(escapes[name[index + 1]])
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _relative_path(name: str) -> str:
    """Normalize a manifest name into a safe POSIX relative path."""
    while name.startswith("./"):
        name = name[2:]
    return name


def _path_error(name: str) -> str | None:
    if not name or name in (".", ".."):
        return "empty or invalid path"
    if name.startswith("/") or _DRIVE_PREFIX.match(name):
        return "absolute paths are not allowed"
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "path contains an empty, '.' or '..' component"
    return None


def parse_sha256sums(text: str) -> tuple[list[dict], list[str]]:
    """Parse SHA256SUMS text into (entries, errors).

    Accepts the coreutils ``<hash>  <name>`` and ``<hash> *<name>`` forms,
    comments and blank lines, and GNU-escaped names. Line numbers in entries
    refer to the original text and make errors easy to locate.
    """
    entries: list[dict] = []
    errors: list[str] = []
    seen: dict[str, str] = {}

    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        escaped = raw.startswith("\\")
        line = raw[1:] if escaped else raw
        match = _HASH_LINE.match(line)
        if not match:
            errors.append(f"line {lineno}: malformed SHA256SUMS entry")
            continue

        digest = match.group(1).lower()
        name = _relative_path(
            _unescape(match.group(2)) if escaped else match.group(2))
        problem = _path_error(name)
        if problem:
            errors.append(f"line {lineno}: {problem}: {name!r}")
            continue

        if name in seen:
            if seen[name] != digest:
                errors.append(
                    f"line {lineno}: conflicting hashes for path {name!r}")
            continue
        seen[name] = digest
        entries.append({"path": name, "sha256": digest, "line": lineno})

    entries.sort(key=lambda entry: entry["path"])
    return entries, errors


def _artifact(path: str, status: str, *, expected: str | None = None,
              actual: str | None = None, size: int | None = None,
              detail: str = "") -> dict:
    return {
        "path": path,
        "status": status,
        "expected_sha256": expected,
        "actual_sha256": actual,
        "size": size,
        "detail": detail,
    }


def build_report(sums_path: str | Path, artifact_dir: str | Path) -> dict:
    """Re-hash every listed artifact and every unlisted file; build a report."""
    report: dict = {
        "schema": SCHEMA_VERSION,
        "tool": "release_artifact_check",
        "complete": False,
        "status": "FAIL",
        "errors": [],
        "summary": {},
        "artifacts": [],
    }
    errors: list[str] = report["errors"]
    sums = Path(sums_path)
    root = Path(artifact_dir)

    entries: list[dict] = []
    if not sums.is_file():
        errors.append(f"SHA256SUMS file not found: {sums.name}")
    if not root.is_dir():
        errors.append(f"artifact directory not found: {root.name}")

    if sums.is_file():
        try:
            entries, parse_errors = parse_sha256sums(
                sums.read_text(encoding="utf-8"))
            errors.extend(parse_errors)
        except (OSError, UnicodeError) as exc:
            errors.append(
                f"cannot read {sums.name}: {type(exc).__name__}")

    all_files: dict[str, Path] = {}
    sums_rel: str | None = None
    if root.is_dir():
        try:
            resolved_sums = sums.resolve()
        except OSError:
            resolved_sums = None
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if resolved_sums is not None and path.resolve() == resolved_sums:
                sums_rel = rel
            all_files[rel] = path

    artifacts: list[dict] = []
    listed = {entry["path"] for entry in entries}
    for entry in entries:
        name = entry["path"]
        file_path = all_files.get(name)
        if file_path is None:
            artifacts.append(_artifact(
                name, "MISSING", expected=entry["sha256"],
                detail="listed artifact not found in artifact directory"))
            continue
        try:
            size = file_path.stat().st_size
            actual = _sha256(file_path)
        except OSError as exc:
            artifacts.append(_artifact(
                name, "HASH_MISMATCH", expected=entry["sha256"], size=None,
                detail=f"cannot hash artifact: {type(exc).__name__}"))
            continue
        status = "OK" if actual == entry["sha256"] else "HASH_MISMATCH"
        detail = "" if status == "OK" else "recomputed SHA-256 differs from manifest"
        artifacts.append(_artifact(
            name, status, expected=entry["sha256"], actual=actual,
            size=size, detail=detail))

    for name in sorted(all_files):
        if name in listed or name == sums_rel:
            continue
        file_path = all_files[name]
        try:
            size = file_path.stat().st_size
            actual = _sha256(file_path)
        except OSError as exc:
            actual, size = None, None
            detail = f"cannot hash extra file: {type(exc).__name__}"
        else:
            detail = "file present in artifact directory but not listed in SHA256SUMS"
        artifacts.append(_artifact(
            name, "EXTRA", actual=actual, size=size, detail=detail))

    artifacts.sort(key=lambda item: (item["path"], item["status"]))
    report["artifacts"] = artifacts

    counts = {status: 0 for status in ("OK", "MISSING", "HASH_MISMATCH", "EXTRA")}
    for item in artifacts:
        counts[item["status"]] += 1
    if not entries and not errors:
        errors.append("SHA256SUMS lists no artifacts")

    failures = counts["MISSING"] + counts["HASH_MISMATCH"] + counts["EXTRA"] + len(errors)
    report["summary"] = {
        "listed": len(entries),
        "files_present": len(all_files),
        "ok": counts["OK"],
        "missing": counts["MISSING"],
        "hash_mismatch": counts["HASH_MISMATCH"],
        "extra": counts["EXTRA"],
        "errors": len(errors),
        "failures": failures,
    }
    report["complete"] = failures == 0
    report["status"] = "PASS" if report["complete"] else "FAIL"
    return report


def render_json(report: dict) -> str:
    """Render a deterministic JSON report (sorted keys, two-space indent)."""
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "\\n")


def render_markdown(report: dict) -> str:
    """Render a deterministic Markdown report from a build_report result."""
    summary = report["summary"]
    result = ("PASS (release complete)" if report["complete"]
              else "FAIL (release incomplete)")
    lines = [
        "# Release artifact verification",
        "",
        f"Result: **{result}**",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Listed artifacts | {summary['listed']} |",
        f"| Files present | {summary['files_present']} |",
        f"| OK | {summary['ok']} |",
        f"| MISSING | {summary['missing']} |",
        f"| HASH_MISMATCH | {summary['hash_mismatch']} |",
        f"| EXTRA | {summary['extra']} |",
        f"| Errors | {summary['errors']} |",
        f"| Failures | {summary['failures']} |",
        "",
        "## Artifacts",
        "",
    ]
    if report["artifacts"]:
        lines += [
            "| Path | Status | Expected SHA-256 | Actual SHA-256 | Size | Detail |",
            "|---|---|---|---|---:|---|",
        ]
        for item in report["artifacts"]:
            lines.append(
                f"| `{_cell(item['path'])}` | {item['status']} | "
                f"`{_cell(item['expected_sha256'])}` | "
                f"`{_cell(item['actual_sha256'])}` | "
                f"{_cell(item['size'])} | {_cell(item['detail'])} |")
    else:
        lines.append("_No artifacts evaluated._")

    lines += ["", "## Errors", ""]
    if report["errors"]:
        lines += [f"- {_cell(error)}" for error in report["errors"]]
    else:
        lines.append("_None._")
    lines.append("")
    return "\n".join(lines)


def _write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sums", required=True,
                        help="path to the SHA256SUMS manifest to verify")
    parser.add_argument("--artifact-dir", required=True,
                        help="directory containing the release artifacts")
    parser.add_argument("--json", dest="json_out",
                        help="write the JSON report to this path")
    parser.add_argument("--markdown", dest="markdown_out",
                        help="write the Markdown report to this path")
    args = parser.parse_args(argv)

    report = build_report(args.sums, args.artifact_dir)
    json_text = render_json(report)
    markdown_text = render_markdown(report)

    try:
        if args.json_out:
            _write(args.json_out, json_text)
        if args.markdown_out:
            _write(args.markdown_out, markdown_text)
    except OSError as exc:
        print(f"[!] cannot write report: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    if args.json_out or args.markdown_out:
        print(f"[*] Release complete: {report['complete']} "
              f"(failures: {report['summary']['failures']})")
    else:
        sys.stdout.write(markdown_text)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
