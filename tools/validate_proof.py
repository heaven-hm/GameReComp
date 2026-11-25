#!/usr/bin/env python3
"""Fail-closed validator for machine-readable process-parity proof artifacts."""
import json, sys
from pathlib import Path

REQUIRED = {"schema", "kind", "generated_at", "input", "target", "commands",
            "scope", "scenarios", "aggregate", "limits"}


def validate(path: Path) -> list:
    problems = []
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        return [f"{path}: unreadable JSON: {e}"]
    missing = REQUIRED - set(d)
    if missing:
        problems.append(f"{path}: missing keys {sorted(missing)}")
    if d.get("schema") != 2 or d.get("kind") != "matrix":
        problems.append(f"{path}: unexpected schema/kind")
    if not d.get("input", {}).get("sha256") or not d.get("target", {}).get("sha256"):
        problems.append(f"{path}: input/target hash missing")
    if not d.get("commands", {}).get("original") or not d.get("commands", {}).get("rebuilt"):
        problems.append(f"{path}: exact commands missing")
    for s in d.get("scenarios", []):
        if not s.get("raw_original") or not s.get("raw_rebuilt"):
            problems.append(f"{path}: raw captures missing")
        if not s.get("guest_normalized"):
            problems.append(f"{path}: normalized guest output missing")
        a, b = s.get("expected", {}), s.get("actual", {})
        if a.get("exit_code") != b.get("exit_code"):
            problems.append(f"{path}: exit codes differ")
        if a.get("guest_stdout_sha256") != b.get("guest_stdout_sha256"):
            problems.append(f"{path}: stdout hashes differ")
        if s.get("verdict") != d.get("aggregate"):
            problems.append(f"{path}: scenario/aggregate verdict mismatch")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "proof/legacy-software")
    files = sorted(root.glob("*.json"))
    if not files:
        print("no proof files found", file=sys.stderr)
        return 1
    bad = [p for f in files for p in validate(f)]
    for p in bad:
        print("FAIL", p)
    print(f"validated {len(files)} proof files, {len(bad)} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
