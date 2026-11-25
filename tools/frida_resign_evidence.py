#!/usr/bin/env python3
"""Fail-closed verifier for a Frida attach made after ad-hoc re-signing.

Issue #171 recorded `PermissionDeniedError` on this macOS arm64 host. A real
attach now succeeds when a *copy* of the target is ad-hoc signed with
`com.apple.security.get-task-allow`; this tool verifies the resulting record
without weakening system security and without claiming a dual-engine MATCH.

It reads only CLI-supplied files, recomputes the target SHA-256 from bytes,
and refuses any record that lacks a genuine attach OK, a module list, or a
version. It never calls frida, codesign, gh or the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(record_path: Path, binary: Path) -> dict:
    problems = []
    try:
        rec = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"verdict": "BLOCKED", "problems": [f"unreadable record: {exc}"]}
    if not isinstance(rec, dict):
        return {"verdict": "BLOCKED", "problems": ["record is not an object"]}
    if rec.get("attach") != "OK":
        problems.append("attach is not OK")
    if not isinstance(rec.get("version"), str) or not rec["version"]:
        problems.append("missing frida version")
    payload = rec.get("payload")
    if not isinstance(payload, list) or not payload:
        problems.append("no script message from the attached session")
    else:
        p = payload[0]
        if p.get("op") != "modules" or not isinstance(p.get("names"), list) or not p["names"]:
            problems.append("no enumerated modules")
        if p.get("arch") != "arm64" or p.get("platform") != "darwin":
            problems.append("host is not macOS arm64")
    digest = None
    if binary.is_file():
        digest = sha256(binary)
    else:
        problems.append("target binary is missing")
    return {
        "verdict": "ATTACH_PROVEN" if not problems else "BLOCKED",
        "problems": problems,
        "target_sha256": digest,
        "frida_version": rec.get("version"),
        "modules": (payload[0].get("names") if not problems and payload else None),
        "dual_engine_match": None,
        "limits": (
            "A real Frida attach with module enumeration is proven; no Unicorn "
            "comparison or original-Windows oracle is claimed by this record."
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", required=True, type=Path)
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--json-out", required=True, type=Path)
    a = ap.parse_args(argv)
    result = verify(a.record, a.binary)
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    a.json_out.write_text(payload, encoding="utf-8")
    print(payload.strip())
    return 0 if result["verdict"] == "ATTACH_PROVEN" else 1


if __name__ == "__main__":
    sys.exit(main())
