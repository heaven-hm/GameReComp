#!/usr/bin/env python3
"""Fail-closed original-asset gate for the game-target issues #15-#19.

The five targets covered here are IGI (#15), Hitman: Codename 47 (#16), Max
Payne (#17), Max Payne 2 (#18) and Hitman 2: Silent Assassin (#19). All of them
are blocked in the issue ledger because owned original Windows binaries and
data files are unavailable. This tool exists to make that blocker *measurable*:
it reports exactly which declared original files are absent or unverifiable.

The tool reads a CLI-supplied asset manifest (JSON) and hashes bytes on disk.
It never downloads a game, builds/launches a binary, writes a game asset, edits
an issue or contacts a network endpoint. Every path comes from ``--manifest``,
``--assets-root`` and ``--scenario-evidence``; nothing is baked in. A relative
path in the manifest is interpreted against ``--assets-root`` when supplied,
else against the manifest's own directory.

PASS is granted only when *all* of the following hold:

* the manifest declares one of issues #15-#19, a platform, an executable and at
  least one required data file;
* the executable and every required data file exist as regular files;
* each declared SHA-256 is a real 64-hex digest and equals the digest recomputed
  from that exact file;
* a scenario evidence file was supplied, exists, is non-empty and is a JSON
  object, with any declared evidence digest also matching.

A missing file/declaration is BLOCKED; a present-but-wrong file or digest is
NOT TESTED. BLOCKED outranks NOT TESTED so a partial check can never look better
than a full one. Exit codes: 0 = PASS, 1 = NOT TESTED, 2 = BLOCKED.

Manifest shape (example with placeholder digests -- real callers supply real
64-hex values from their own original files)::

    {
      "schema": 1,
      "issue": 15,
      "game": "Project I.G.I.",
      "platform": "windows",
      "executable": {"path": "IGI.EXE", "sha256": "<64 hex>"},
      "required_files": [
        {"path": "DATA/LEVEL1.WAD", "sha256": "<64 hex>"}
      ],
      "scenario_evidence": "evidence/igi-scenario.json",
      "scenario_evidence_sha256": "<64 hex>"
    }

No hash is invented here: a declared digest is only ever compared against a
digest recomputed from the CLI-supplied bytes. Files present in the assets root
but not declared are reported as informational ``extra_files`` -- they are never
counted toward a required entry and never turn a BLOCKED verdict into a PASS.

Honest limit: a PASS from this tool means the declared original bytes are
present and intact and an evidence file exists. It does **not** mean any game is
playable, that gameplay/frame/audio/input parity was verified, or that an issue
reached a final status. No issue is acted on by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/game_asset_gate.py"

PASS = "PASS"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
VERDICTS = (PASS, BLOCKED, NOT_TESTED)

# BLOCKED is the strongest stop: if something required is missing, the check
# could not finish even if another file was also found to be wrong.
VERDICT_RANK = {PASS: 0, NOT_TESTED: 1, BLOCKED: 2}
EXIT_BY_VERDICT = {PASS: 0, NOT_TESTED: 1, BLOCKED: 2}

TARGET_ISSUES = (15, 16, 17, 18, 19)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_EXTRA_FILES = 100

SUMMARY = {
    PASS: "all declared original assets exist with matching SHA-256 and a "
          "scenario evidence file was supplied",
    BLOCKED: "PASS withheld: a required file, declaration or scenario evidence "
             "is missing (see missing[])",
    NOT_TESTED: "PASS withheld: declared asset integrity was not established "
                "(see mismatched[] and reasons[])",
}
SCOPE = ("presence and SHA-256 integrity of CLI-declared original assets and "
         "presence of a scenario evidence file only; not a claim that a game is "
         "playable, that gameplay/frame/audio/input parity holds, or that any "
         "issue reached a final status")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a real file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_sha(value: Any) -> str | None:
    """Canonical lowercase 64-hex digest, or None if this is not a real one."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if SHA256_RE.fullmatch(text) else None


def declared_sha(value: Any) -> tuple[str | None, str | None]:
    """Return (digest, problem). Absent and malformed are different failures."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing"
    digest = norm_sha(value)
    if digest is None:
        return None, "malformed"
    return digest, None


def norm_name(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def resolve_path(raw: Any, base: Path) -> Path | None:
    """Resolve a CLI/manifest path; relative paths are relative to ``base``."""
    text = norm_name(raw)
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base / path


class Collector:
    """Accumulate ordered reasons and the worst per-check verdict."""

    def __init__(self, checks: tuple[str, ...]) -> None:
        self.reasons: list[dict] = []
        self.checks: dict[str, str] = {name: PASS for name in checks}

    def add(self, code: str, verdict: str, message: str,
            check: str | None = None) -> None:
        self.reasons.append({"code": code, "verdict": verdict, "message": message})
        if check is not None:
            self.set_check(check, verdict)

    def set_check(self, name: str, verdict: str) -> None:
        current = self.checks.get(name, PASS)
        if VERDICT_RANK[verdict] > VERDICT_RANK[current]:
            self.checks[name] = verdict

    def block_all(self, verdict: str) -> None:
        for name in self.checks:
            self.checks[name] = verdict

    @property
    def verdict(self) -> str:
        return max((r["verdict"] for r in self.reasons),
                   key=lambda v: VERDICT_RANK[v], default=PASS)


CHECK_NAMES = ("manifest", "issues", "platform", "executable", "required_files",
               "scenario_evidence", "hash_integrity")


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def _declared_issues(doc: dict) -> tuple[list[int], str | None]:
    """Read and bounds-check the issue declaration (only #15-#19 are in scope)."""
    raw = doc.get("issues", doc.get("issue"))
    values = raw if isinstance(raw, list) else [raw]
    numbers: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return [], "issue declaration is not an integer"
        try:
            number = int(str(value).strip())
        except ValueError:
            return [], "issue declaration is not an integer"
        if number not in TARGET_ISSUES:
            return [], f"issue #{number} is outside the #15-#19 gate scope"
        if number not in numbers:
            numbers.append(number)
    if not numbers:
        return [], "no issue was declared"
    return sorted(numbers), None


def _file_entry(col: Collector, *, kind: str, label: str, raw: Any,
                base: Path, declared_list: list[dict], missing: list[dict],
                mismatched: list[dict]) -> dict:
    """Verify one declared asset: path -> existence -> digest -> equality."""
    entry: dict = {
        "kind": kind,
        "label": label,
        "declared_path": norm_name(raw.get("path")) if isinstance(raw, dict) else "",
        "declared_sha256": None,
        "computed_sha256": None,
        "status": PASS,
    }
    check = "executable" if kind == "executable" else "required_files"

    if not isinstance(raw, dict):
        col.add("ASSET_DECLARATION_INVALID", BLOCKED,
                f"{label}: asset declaration is not an object", check)
        entry["status"] = BLOCKED
        return entry

    path = resolve_path(raw.get("path"), base)
    if path is None:
        col.add("ASSET_PATH_MISSING", BLOCKED,
                f"{label}: no path was declared", check)
        entry["status"] = BLOCKED
        return entry
    entry["path"] = str(path)
    declared_list.append(path)

    digest, problem = declared_sha(raw.get("sha256"))
    if problem is not None:
        col.add("SHA256_MISSING" if problem == "missing" else "SHA256_MALFORMED",
                BLOCKED if problem == "missing" else NOT_TESTED,
                f"{label}: no SHA-256 was declared" if problem == "missing"
                else f"{label}: declared SHA-256 is not a real 64-hex digest",
                "hash_integrity")
        entry["status"] = BLOCKED if problem == "missing" else NOT_TESTED
    entry["declared_sha256"] = digest

    if not path.is_file():
        col.add("ASSET_FILE_MISSING", BLOCKED,
                f"{label}: original file is missing: {path}", check)
        missing.append({"kind": kind, "label": label, "path": str(path),
                        "reason": f"missing {label}: {path}"})
        entry["status"] = BLOCKED
        return entry
    try:
        actual = sha256_file(path)
    except OSError as exc:
        col.add("ASSET_UNREADABLE", BLOCKED,
                f"{label}: original file cannot be read: {path} ({exc})", check)
        missing.append({"kind": kind, "label": label, "path": str(path),
                        "reason": f"unreadable {label}: {path}"})
        entry["status"] = BLOCKED
        return entry

    entry["computed_sha256"] = actual
    if digest is not None and digest != actual:
        col.add("SHA256_MISMATCH", NOT_TESTED,
                f"{label}: declared SHA-256 {digest} != recomputed {actual}",
                "hash_integrity")
        mismatched.append({"kind": kind, "label": label, "path": str(path),
                           "declared_sha256": digest, "computed_sha256": actual})
        entry["status"] = NOT_TESTED
    return entry


def _check_evidence(col: Collector, *, cli_evidence: Path | None, doc: dict,
                    base: Path, declared_list: list[dict]) -> dict:
    """Require a real, non-empty scenario evidence file; bind a hash if given."""
    source = "cli" if cli_evidence is not None else "manifest"
    raw = doc.get("scenario_evidence")
    declared_hash = doc.get("scenario_evidence_sha256")
    if isinstance(raw, dict):
        declared_hash = raw.get("sha256", declared_hash)
        raw = raw.get("path")
    if cli_evidence is not None:
        path = Path(cli_evidence)
    else:
        path = resolve_path(raw, base)
    evidence: dict = {"path": str(path) if path else None, "present": False,
                      "sha256": None, "status": BLOCKED, "source": source}
    if path is None:
        col.add("SCENARIO_EVIDENCE_MISSING", BLOCKED,
                "scenario evidence: no evidence file was supplied",
                "scenario_evidence")
        return evidence

    declared_list.append(path)
    if not path.is_file():
        col.add("SCENARIO_EVIDENCE_FILE_MISSING", BLOCKED,
                f"scenario evidence: evidence file is missing: {path}",
                "scenario_evidence")
        return evidence
    try:
        data = path.read_bytes()
    except OSError as exc:
        col.add("SCENARIO_EVIDENCE_UNREADABLE", BLOCKED,
                f"scenario evidence: evidence file cannot be read: {path} ({exc})",
                "scenario_evidence")
        return evidence
    if not data:
        col.add("SCENARIO_EVIDENCE_EMPTY", BLOCKED,
                f"scenario evidence: evidence file is empty: {path}",
                "scenario_evidence")
        return evidence
    evidence["present"] = True
    evidence["sha256"] = sha256_bytes(data)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        col.add("SCENARIO_EVIDENCE_INVALID", NOT_TESTED,
                f"scenario evidence: not a JSON object: {path} ({exc})",
                "scenario_evidence")
        evidence["status"] = NOT_TESTED
        return evidence
    if not isinstance(parsed, dict) or not parsed:
        col.add("SCENARIO_EVIDENCE_INVALID", NOT_TESTED,
                f"scenario evidence: JSON object is empty: {path}",
                "scenario_evidence")
        evidence["status"] = NOT_TESTED
        return evidence

    declared, problem = declared_sha(declared_hash)
    if problem == "malformed":
        col.add("EVIDENCE_SHA256_MALFORMED", NOT_TESTED,
                "scenario evidence: declared SHA-256 is not a real 64-hex digest",
                "hash_integrity")
        evidence["status"] = NOT_TESTED
    elif declared is not None and declared != evidence["sha256"]:
        col.add("EVIDENCE_SHA256_MISMATCH", NOT_TESTED,
                f"scenario evidence: declared SHA-256 {declared} != recomputed "
                f"{evidence['sha256']}", "hash_integrity")
        evidence["status"] = NOT_TESTED
    else:
        # No evidence digest was declared; the file's presence, non-emptiness
        # and JSON shape were still verified above and its hash is recorded.
        evidence["status"] = PASS
    return evidence


def _extra_files(root: Path, declared: list[Path], manifest: Path) -> dict:
    """Informational list of on-disk files that are not part of the declaration.

    Extra files are never fused into the declared set: a matching hash in an
    undeclared file cannot satisfy a declared path that is absent.
    """
    result: dict = {"root": str(root), "count": 0, "paths": [], "truncated": False}
    if not root.is_dir():
        return result
    known: set[Path] = set()
    for item in declared:
        if item is not None:
            known.add(item.resolve())
    known.add(manifest.resolve())
    extras: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            candidate = Path(dirpath) / name
            try:
                if candidate.resolve() in known:
                    continue
            except (OSError, RuntimeError):
                # An unresolvable entry is still just an extra file; it never
                # contributes toward a declared requirement.
                pass
            try:
                extras.append(str(candidate.relative_to(root)))
            except ValueError:
                extras.append(str(candidate))
    result["count"] = len(extras)
    result["paths"] = extras[:MAX_EXTRA_FILES]
    result["truncated"] = len(extras) > MAX_EXTRA_FILES
    return result


def _report(col: Collector, *, manifest: Path, base: Path,
            issues: list[int], platform: str, game: str,
            executable: dict, required: list[dict], evidence: dict,
            missing: list[dict], mismatched: list[dict], extra: dict) -> dict:
    verdict = col.verdict
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "verdict": verdict,
        "exit_code": EXIT_BY_VERDICT[verdict],
        "reason": SUMMARY[verdict],
        "issues": issues,
        "issue_closed": False,
        "manifest": str(manifest),
        "assets_root": str(base),
        "game": game,
        "platform": platform,
        "checks": col.checks,
        "assets": {"executable": executable, "required": required},
        "missing": missing,
        "mismatched": mismatched,
        "scenario_evidence": evidence,
        "extra_files": extra,
        "reasons": col.reasons,
        "scope": SCOPE,
    }


def evaluate(manifest_path: Path, *, assets_root: Path | None = None,
             scenario_evidence: Path | None = None) -> dict:
    """Evaluate one manifest; return a machine-readable report (no side effects)."""
    col = Collector(CHECK_NAMES)
    manifest = Path(manifest_path)
    base = Path(assets_root) if assets_root is not None else manifest.parent
    doc, error = _load_json(manifest)
    if error is not None:
        col.add("MANIFEST_UNREADABLE", BLOCKED,
                f"manifest: cannot read/parse: {error}", "manifest")
        col.block_all(BLOCKED)
        return _report(col, manifest=manifest, base=base, issues=[],
                       platform="", game="", executable={}, required=[],
                       evidence={"path": None, "present": False, "sha256": None,
                                 "status": BLOCKED, "source": "manifest"},
                       missing=[], mismatched=[], extra={"root": str(base),
                       "count": 0, "paths": [], "truncated": False})

    issues, issue_error = _declared_issues(doc)
    if issue_error is not None:
        col.add("ISSUE_UNDECLARED", BLOCKED, f"issues: {issue_error}", "issues")

    platform = norm_name(doc.get("platform"))
    if not platform:
        col.add("PLATFORM_MISSING", BLOCKED,
                "platform: manifest declares no platform", "platform")

    declared_paths: list[Path] = []
    missing: list[dict] = []
    mismatched: list[dict] = []

    executable = _file_entry(col, kind="executable", label="executable",
                             raw=doc.get("executable"), base=base,
                             declared_list=declared_paths, missing=missing,
                             mismatched=mismatched)

    raw_required = doc.get("required_files")
    required: list[dict] = []
    if not isinstance(raw_required, list) or not raw_required:
        col.add("REQUIRED_FILES_MISSING", BLOCKED,
                "required_files: manifest declares no required data files",
                "required_files")
    else:
        for index, raw in enumerate(raw_required):
            required.append(_file_entry(
                col, kind="required", label=f"required_files[{index}]", raw=raw,
                base=base, declared_list=declared_paths, missing=missing,
                mismatched=mismatched))

    seen: dict[Path, list[str]] = {}
    for entry in (executable, *required):
        path = entry.get("path")
        if path is None:
            continue
        key = Path(path).resolve()
        seen.setdefault(key, []).append(entry["label"])
    for path, labels in seen.items():
        if len(labels) > 1:
            col.add("DUPLICATE_ASSET_DECLARATION", NOT_TESTED,
                    f"assets: {path} is declared more than once ({', '.join(labels)})",
                    "hash_integrity")

    evidence = _check_evidence(col, cli_evidence=scenario_evidence, doc=doc,
                               base=base, declared_list=declared_paths)
    extra = _extra_files(base, declared_paths, manifest)

    return _report(col, manifest=manifest, base=base, issues=issues,
                   platform=platform, game=norm_name(doc.get("game")),
                   executable=executable, required=required, evidence=evidence,
                   missing=missing, mismatched=mismatched, extra=extra)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True, type=Path,
                    help="CLI-supplied asset manifest JSON (paths, hashes, platform)")
    ap.add_argument("--assets-root", type=Path, default=None,
                    help="base directory for relative manifest paths; defaults "
                         "to the manifest's directory")
    ap.add_argument("--scenario-evidence", type=Path, default=None,
                    help="CLI-supplied scenario evidence file; overrides the "
                         "manifest's scenario_evidence path")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the machine-readable verdict JSON here")
    args = ap.parse_args(argv)

    report = evaluate(args.manifest, assets_root=args.assets_root,
                      scenario_evidence=args.scenario_evidence)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    for reason in report["reasons"]:
        print(f"{reason['verdict']}: {reason['message']}", file=sys.stderr)
    print(f"{report['verdict']}: {report['reason']}")
    if args.out is not None:
        print(f"report: {args.out}")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
