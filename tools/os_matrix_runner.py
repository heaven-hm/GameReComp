#!/usr/bin/env python3
"""Run cross-OS gate tools and merge their JSON verdicts into one OS matrix.

Every gate is supplied on the command line, by dotted module name (``-m``) or
by path to a Python file. Nothing about a gate, a host, a game, a corpus or a
repository location is built into this runner. A gate is executed as::

    python3 [-m] <gate> --out <runner-supplied JSON path> [extra args]

A gate's JSON document is a list of cell verdicts (or an object with a
``cells``/``rows``/``results`` list). Each verdict names an OS and architecture
and carries ``status``/``verdict``, ``reason`` and optional evidence with a
SHA-256. The runner never executes game code itself: it only launches gates,
reads the JSON they wrote and joins their claims into the 3x4 matrix used by
:mod:`tools.os_capability_matrix`.

Fail-closed policy:

* a missing/unimportable module, a missing path, a timeout, a non-zero exit, an
  unreadable/invalid JSON verdict or a verdict with no claims is ``NOT TESTED``
  for every cell that gate covers (coverage defaults to all cells);
* a cell is PASS only when every gate covering it reported PASS, no covering
  gate was unavailable and any declared evidence hash was recomputed here;
* a fabricated or tampered evidence hash downgrades that claim to FAIL; a bare
  PASS without a verified evidence hash is capped at PARTIAL;
* all output is sorted by OS, architecture, gate name and status, and a JSON
  ``integrity.sha256`` lets ``--verify`` detect post-hoc tampering.

Usage::

    python3 -m tools.os_matrix_runner \\
        --gate '{"name":"probe","module":"tools.macos_target_probe",
                 "args":["--json"],"coverage":["macOS"]}' \\
        --gate /path/to/fake_gate.py \\
        --out os-matrix.json [--markdown os-matrix.md] \\
        [--timeout 60] [--generated-at 2026-01-01T00:00:00Z]

    python3 -m tools.os_matrix_runner --verify os-matrix.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import os_capability_matrix as ocm

SCHEMA_VERSION = 1
GENERATOR = "tools/os_matrix_runner.py"
OSES = tuple(ocm.OSES)
ARCHES = tuple(ocm.ARCHES)
STATUSES = tuple(ocm.STATUSES)
SEVERITY = dict(ocm.SEVERITY)
NOT_TESTED = "NOT TESTED"
PASS = "PASS"
PARTIAL = "PARTIAL"
DEFAULT_TIMEOUT = 60
ALL_CELLS = tuple((os_name, arch) for os_name in OSES for arch in ARCHES)
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def datetime_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of(path: Path | None) -> str | None:
    return ocm.sha256_file(path) if path else None


def canonical_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def with_integrity(matrix: dict) -> dict:
    """Attach a hash of the canonical document so --verify can spot tampering."""
    out = dict(matrix)
    payload = canonical_bytes(out)
    out["integrity"] = {"algorithm": "sha256", "sha256": sha256_bytes(payload)}
    return out


def norm_status(value: Any) -> str | None:
    """Map a gate's status spelling onto the fail-closed verdict vocabulary."""
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip().upper().replace("_", " ").replace("-", " ")
    token = " ".join(token.split())
    aliases = {
        "SCENARIO PASS": PASS,
        "VERIFIED PLAYABLE SCOPE": PASS,
        "NOT RUN": NOT_TESTED,
        "NO ORACLE": NOT_TESTED,
        "UNTESTABLE": NOT_TESTED,
        "NO EVIDENCE": NOT_TESTED,
        "BUILD ONLY": "BUILD ONLY",
        "REFUSED": "BLOCKED",
        "DEFERRED": "BLOCKED",
    }
    if token in aliases:
        return aliases[token]
    for status in STATUSES:
        if token == status or token.startswith(status):
            return status
    return None


def cell_of(os_name: Any, arch: Any) -> tuple[str, str] | None:
    from_cli = (ocm.norm_os(os_name), ocm.norm_arch(arch))
    if all(from_cli):
        return from_cli  # type: ignore[return-value]
    return None


def coverage_of(raw: Any) -> tuple[tuple[str, str], ...]:
    """CLI-declared cells for a gate; unknown input fails closed to all cells."""
    if raw in (None, "", [], ()):
        return ALL_CELLS
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ALL_CELLS
    cells: set[tuple[str, str]] = set()
    for item in raw:
        text = str(item).strip()
        if text in ("*", "ALL", "all"):
            return ALL_CELLS
        if "|" in text:
            os_raw, arch_raw = text.split("|", 1)
            cell = cell_of(os_raw, arch_raw)
            if cell:
                cells.add(cell)
            else:
                return ALL_CELLS
            continue
        os_name, arch = ocm.norm_os(text), ocm.norm_arch(text)
        if os_name:
            cells.update((os_name, a) for a in ARCHES)
        elif arch:
            cells.update((o, arch) for o in OSES)
        else:
            return ALL_CELLS
    return tuple(sorted(cells, key=lambda c: (OSES.index(c[0]), ARCHES.index(c[1]))))


def parse_gate(raw: str, used_names: set[str]) -> dict:
    """Parse a plain module/path or a JSON gate spec supplied on the CLI."""
    text = raw.strip()
    if text.startswith("{"):
        try:
            spec = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"invalid --gate JSON: {exc}") from exc
        if not isinstance(spec, dict):
            raise ValueError("--gate JSON must be an object")
        spec = dict(spec)
        target = spec.get("module") or spec.get("path") or spec.get("target")
    else:
        if "=" in text and not text.startswith((".", "/", "~")):
            name, target = text.split("=", 1)
        else:
            name, target = "", text
        spec = {"name": name, "target": target}
    if not isinstance(target, str) or not target.strip():
        raise ValueError("--gate needs a module name or a file path")
    spec["target"] = target.strip()
    kind = str(spec.get("kind", "")).lower()
    if kind not in ("module", "path"):
        path = Path(spec["target"])
        kind = ("path" if path.suffix == ".py" or path.exists()
                or spec["target"].startswith((".", "/", "~")) else "module")
    spec["kind"] = kind
    args = spec.get("args", [])
    if not isinstance(args, list):
        raise ValueError("--gate args must be a list")
    spec["args"] = [str(arg) for arg in args]
    spec["coverage"] = coverage_of(spec.get("coverage"))
    if spec.get("name"):
        fallback_name = str(spec["name"])
    elif spec["kind"] == "module":
        fallback_name = spec["target"].rsplit(".", 1)[-1]
    else:
        fallback_name = Path(spec["target"]).stem
    base_name = str(fallback_name or spec["target"]).strip()
    base_name = SAFE_NAME.sub("_", base_name).strip("_") or "gate"
    name, suffix = base_name, 2
    while name in used_names:
        name = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(name)
    spec["name"] = name
    return spec


def gate_command(spec: dict, verdict_path: Path) -> list[str]:
    cmd = [sys.executable]
    if spec["kind"] == "module":
        cmd += ["-m", spec["target"]]
    else:
        cmd.append(str(Path(spec["target"]).expanduser()))
    cmd += ["--out", str(verdict_path), *spec["args"]]
    return cmd


def module_available(name: str) -> tuple[bool, str]:
    try:
        found = importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError) as exc:
        return False, f"module not importable: {name} ({type(exc).__name__}: {exc})"
    return found, ("" if found else f"module not importable: {name}")


def verdict_records(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("cells", "rows", "games", "records", "results", "cases",
                    "scenarios"):
            if isinstance(data.get(key), list):
                return data[key]
        if any(key in data for key in ("os", "arch", "status", "verdict")):
            return [data]
    return []


def evidence_of(obj: dict, base_dir: Path) -> tuple[list[dict], list[str]]:
    """Return verified evidence records and reasons; never trust a bare hash."""
    raw = obj.get("evidence", obj.get("evidence_path", obj.get("artifact")))
    hashes = obj.get("hashes") if isinstance(obj.get("hashes"), dict) else {}
    claimed = (obj.get("evidence_sha256") or hashes.get("evidence_sha256")
               or obj.get("sha256"))
    entries: list[Any] = []
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, dict):
        entries = [raw]
    elif isinstance(raw, list):
        entries = raw
    records: list[dict] = []
    reasons: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            path_raw = entry.get("path") or entry.get("evidence_path")
            digest = entry.get("sha256") or entry.get("evidence_sha256") or claimed
        else:
            path_raw, digest = entry, claimed
        if not isinstance(path_raw, str) or not path_raw.strip():
            continue
        path = Path(path_raw.strip())
        if not path.is_absolute():
            path = base_dir / path
        actual = sha256_of(path)
        declared = ocm.norm_sha(digest)
        record = {"path": str(path), "sha256": actual, "declared_sha256": declared,
                  "verified": bool(declared and actual and declared == actual)}
        records.append(record)
        if declared is None:
            reasons.append(f"evidence has no valid SHA-256: {path}")
        elif actual is None:
            reasons.append(f"evidence file is missing: {path}")
            record["tampered"] = True
        elif declared != actual:
            reasons.append(f"tampered evidence SHA-256 for {path}: "
                           f"declared {declared}, actual {actual}")
            record["tampered"] = True
    return records, reasons


def claims_from(data: Any, gate_name: str, base_dir: Path) -> list[dict]:
    claims: list[dict] = []
    for obj in verdict_records(data):
        if not isinstance(obj, dict):
            continue
        target = obj.get("cell") if isinstance(obj.get("cell"), dict) else {}
        os_raw = (ocm.find_meta(obj, data, ocm.OS_KEYS) or target.get("os")
                  or obj.get("key"))
        arch_raw = (ocm.find_meta(obj, data, ocm.ARCH_KEYS) or target.get("arch"))
        if isinstance(os_raw, str) and "|" in os_raw:
            os_raw, _, arch_raw = os_raw.partition("|")
        cell = cell_of(os_raw, arch_raw)
        if not cell:
            continue
        value = (obj.get("verdict") or obj.get("status")
                 or obj.get("working_status") or obj.get("result"))
        status = norm_status(value)
        if status is None:
            status = NOT_TESTED
        reasons = []
        for reason in (obj.get("reasons") if isinstance(obj.get("reasons"), list)
                       else [obj.get("reason") or obj.get("explanation")]):
            if isinstance(reason, str) and reason.strip():
                reasons.append(reason.strip())
        evidence, evidence_reasons = evidence_of(obj, base_dir)
        reasons.extend(evidence_reasons)
        if any(item.get("tampered") for item in evidence):
            status = "FAIL"
            reasons.append("PASS withheld: tampered or unverifiable evidence")
        elif status == PASS:
            if not any(item["verified"] for item in evidence):
                status = "PARTIAL"
                reasons.append("PASS withheld: no evidence SHA-256 recomputed and matched")
        if not reasons:
            reasons.append("gate reported this cell")
        claims.append({
            "gate": gate_name,
            "os": cell[0], "arch": cell[1],
            "key": f"{cell[0]}|{cell[1]}",
            "status": status, "reason": "; ".join(reasons), "reasons": reasons,
            "evidence": sorted(evidence, key=lambda e: (e["path"], e["sha256"] or "")),
        })
    return claims


def run_gate(spec: dict, workdir: Path, timeout: int, artifacts_dir: Path) -> dict:
    """Execute one gate, recording every failure as an unavailable gate."""
    name = spec["name"]
    result = {
        "name": name, "target": spec["target"], "kind": spec["kind"],
        "args": list(spec["args"]),
        "coverage": [f"{o}|{a}" for o, a in spec["coverage"]],
        "status": "MISSING", "reason": "", "returncode": None, "source": None,
        "sha256": None, "verdict_path": None, "stdout_tail": "", "claims": [],
    }

    def unavailable(status: str, reason: str) -> dict:
        result.update(status=status, reason=reason)
        return result

    if spec["kind"] == "module":
        ok, reason = module_available(spec["target"])
        if not ok:
            return unavailable("MISSING", reason)
    else:
        path = Path(spec["target"]).expanduser()
        if not path.is_file():
            return unavailable("MISSING", f"gate path not found: {path}")

    verdict_path = workdir / f"{name}.json"
    result["verdict_path"] = str(verdict_path)
    cmd = gate_command(spec, verdict_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        result["stdout_tail"] = out[-2000:]
        return unavailable("FAILED", f"gate timed out after {timeout}s")
    except OSError as exc:
        return unavailable("FAILED", f"gate could not be started: "
                                     f"{type(exc).__name__}: {exc}")
    result["returncode"] = proc.returncode
    if proc.returncode != 0:
        tail = proc.stderr.strip() or proc.stdout.strip()
        return unavailable("FAILED", f"gate exited {proc.returncode}: "
                                     f"{tail[-500:] if tail else 'no output'}")

    raw: bytes | None = None
    source = None
    if verdict_path.is_file():
        try:
            raw = verdict_path.read_bytes()
            source = "file"
        except OSError as exc:
            return unavailable("INVALID", f"gate verdict unreadable: {exc}")
    else:
        stripped = proc.stdout.strip()
        if stripped:
            raw = stripped.encode("utf-8")
            source = "stdout"
    if raw is None:
        return unavailable("INVALID", "gate wrote no JSON verdict file")
    result["source"] = source
    result["sha256"] = sha256_bytes(raw)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        return unavailable("INVALID", f"gate verdict is not JSON: {exc}")
    records = verdict_records(data)
    if not records:
        return unavailable("INVALID", "gate verdict has no cell records")
    base_dir = verdict_path.parent if source == "file" else Path.cwd()
    claims = claims_from(data, name, base_dir)
    if not claims:
        return unavailable("INVALID", "gate verdict has no usable cell claims")
    # Persist the exact bytes next to the matrix so --verify can re-hash them
    # after the runner exits; the temporary workdir itself is never published.
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for claim in claims:
        for item in claim["evidence"]:
            evidence_path = Path(item["path"])
            try:
                inside_workdir = evidence_path.resolve().is_relative_to(workdir.resolve())
            except (OSError, ValueError):
                inside_workdir = False
            if inside_workdir and evidence_path.is_file():
                kept_evidence = artifacts_dir / f"{name}-{evidence_path.name}"
                kept_evidence.write_bytes(evidence_path.read_bytes())
                item["path"] = str(kept_evidence)
    kept = artifacts_dir / f"{name}.json"
    kept.write_bytes(raw)
    result["verdict_path"] = str(kept)
    result.update(status="OK", reason="gate verdict read and normalized",
                  claims=claims)
    return result


def merge_cells(gates: list[dict]) -> list[dict]:
    """Join all gate claims into a deterministic, fail-closed 3x4 matrix."""
    by_cell: dict[tuple[str, str], list[dict]] = {}
    unavailable: dict[tuple[str, str], list[str]] = {}
    for gate in sorted(gates, key=lambda item: item["name"]):
        coverage = {(o, a) for cell in gate["coverage"]
                    for o, a in [tuple(cell.split("|", 1))]
                    if cell_of(o, a)}
        if gate["status"] != "OK":
            for cell in coverage:
                unavailable.setdefault(cell, []).append(
                    f"gate {gate['name']} {gate['status']}: {gate['reason']}")
        for claim in gate["claims"]:
            cell = cell_of(claim["os"], claim["arch"])
            if cell:
                by_cell.setdefault(cell, []).append(claim)

    cells: list[dict] = []
    for os_name, arch in ALL_CELLS:
        key = f"{os_name}|{arch}"
        claims = sorted(by_cell.get((os_name, arch), []),
                        key=lambda claim: claim["gate"])
        blocked = sorted(unavailable.get((os_name, arch), []))
        reasons: list[str] = []
        if blocked:
            status = NOT_TESTED
            reasons.extend(blocked)
            reasons.append("PASS withheld: a covering gate did not produce a verdict")
        elif not claims:
            status = NOT_TESTED
            reasons.append("no gate reported this cell")
        else:
            statuses = [claim["status"] for claim in claims]
            if all(value == PASS for value in statuses):
                status = PASS
                reasons.append(f"all {len(claims)} covering gates reported PASS")
            else:
                capped = [PARTIAL if value == PASS else value for value in statuses]
                status = max(capped, key=lambda value: SEVERITY[value])
                reasons.append("PASS withheld: covering gates did not unanimously "
                               "report PASS")
            for claim in claims:
                reasons.append(f"{claim['gate']}: {claim['reason']}")
        evidence: dict[str, dict] = {}
        for claim in claims:
            for item in claim["evidence"]:
                evidence.setdefault(item["path"], item)
        cells.append({
            "os": os_name, "arch": arch, "key": key,
            "status": status, "reason": "; ".join(reasons), "reasons": reasons,
            "gates": {claim["gate"]: claim["status"] for claim in claims},
            "unavailable_gates": sorted({text.split()[1] for text in blocked}),
            "evidence": [evidence[k] for k in sorted(evidence)],
        })
    return cells


def build_matrix(gates: list[dict], generated_at: str) -> dict:
    cells = merge_cells(gates)
    by_status = {status: sum(1 for cell in cells if cell["status"] == status)
                 for status in STATUSES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "oses": list(OSES), "arches": list(ARCHES),
        "policy": {
            "pass_requires_unanimous_gate_pass": True,
            "unavailable_gates_are_not_tested": True,
            "evidence_hashes_are_recomputed": True,
            "gate_order_and_cell_order_are_deterministic": True,
        },
        "gates": sorted(
            [{key: value for key, value in gate.items() if key != "claims"}
             for gate in gates], key=lambda gate: gate["name"]),
        "cells": cells,
        "summary": {
            "total": len(cells), "by_status": by_status,
            "by_os": {os_name: sum(1 for cell in cells if cell["os"] == os_name)
                      for os_name in OSES},
            "gate_count": len(gates),
        },
    }


def verify_matrix(matrix: dict) -> dict:
    """Recompute the document hash and every recorded gate/evidence hash."""
    tampered: list[str] = []
    integrity = matrix.get("integrity") if isinstance(matrix, dict) else None
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256":
        tampered.append("matrix has no SHA-256 integrity record")
    else:
        plain = {key: value for key, value in matrix.items() if key != "integrity"}
        if sha256_bytes(canonical_bytes(plain)) != integrity.get("sha256"):
            tampered.append("matrix document SHA-256 mismatch")
    for gate in matrix.get("gates", []) if isinstance(matrix, dict) else []:
        path, digest = gate.get("verdict_path"), gate.get("sha256")
        if gate.get("status") != "OK" or not path or not digest:
            continue
        actual = sha256_of(Path(path))
        if actual is None:
            tampered.append(f"gate {gate['name']} verdict file is missing: {path}")
        elif actual != digest:
            tampered.append(f"gate {gate['name']} verdict SHA-256 mismatch: {path}")
    for cell in matrix.get("cells", []) if isinstance(matrix, dict) else []:
        for item in cell.get("evidence", []):
            declared, path = item.get("sha256"), item.get("path")
            if not declared or not path:
                continue
            actual = sha256_of(Path(path))
            if actual is None or actual != declared:
                tampered.append(f"{cell.get('key')}: evidence changed: {path}")
    return {"verdict": "TAMPERED" if tampered else "OK", "tampered": tampered}


def render_markdown(matrix: dict) -> str:
    lines = ["# Cross-OS gate matrix", "",
             f"Generated: {matrix['generated_at']}  ",
             f"Schema: {matrix['schema']}  ",
             "PASS requires unanimous gate PASS with verified evidence; an "
             "unavailable gate makes every cell it covers NOT TESTED.", "",
             "| OS \\ Arch | " + " | ".join(ARCHES) + " |",
             "| --- | " + " | ".join("---" for _ in ARCHES) + " |"]
    for os_name in OSES:
        statuses = [next(c["status"] for c in matrix["cells"]
                         if c["os"] == os_name and c["arch"] == arch)
                    for arch in ARCHES]
        lines.append(f"| {os_name} | " + " | ".join(statuses) + " |")
    lines += ["", "## Gates", ""]
    for gate in matrix["gates"]:
        lines.append(f"- `{gate['name']}` -> {gate['target']}: "
                     f"{gate['status']} ({gate['reason']})")
    lines += ["", "## Cell details", ""]
    for cell in matrix["cells"]:
        lines += [f"### {cell['os']} / {cell['arch']} — {cell['status']}", "",
                  f"- Reason: {cell['reason']}", "", ]
    counts = matrix["summary"]["by_status"]
    lines += ["Summary: " + "; ".join(f"{k}: {v}" for k, v in counts.items()) + ".", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", action="append", default=[], metavar="MODULE|PATH|JSON",
                    help="gate module, path or JSON spec (repeatable)")
    ap.add_argument("--out", type=Path, help="write the merged matrix JSON here")
    ap.add_argument("--artifacts", type=Path,
                    help="where to keep gate verdict JSON for --verify "
                         "(default: <out>.gates/)")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown summary")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"per-gate wall-clock timeout (default {DEFAULT_TIMEOUT}s)")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for byte-identical output")
    ap.add_argument("--verify", type=Path, metavar="MATRIX",
                    help="check a written matrix's integrity instead of running gates")
    args = ap.parse_args(argv)

    if args.verify is not None:
        try:
            matrix = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            print(f"verdict: INVALID ({type(exc).__name__}: {exc})")
            return 1
        report = verify_matrix(matrix)
        print("verdict: " + report["verdict"])
        for item in report["tampered"]:
            print(f"tampered: {item}")
        return 0 if report["verdict"] == "OK" else 1
    if not args.gate:
        ap.error("supply at least one --gate (or --verify)")
    if args.out is None:
        ap.error("--out is required unless --verify is used")
    if args.timeout < 1:
        ap.error("--timeout must be at least 1 second")

    used_names: set[str] = set()
    specs: list[dict] = []
    for raw in args.gate:
        try:
            specs.append(parse_gate(raw, used_names))
        except ValueError as exc:
            ap.error(str(exc))

    artifacts_dir = args.artifacts or Path(str(args.out) + ".gates")
    gates: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="os-matrix-runner-") as tmp:
        workdir = Path(tmp)
        for spec in specs:
            gates.append(run_gate(spec, workdir, args.timeout, artifacts_dir))
    for gate in sorted(gates, key=lambda item: item["name"]):
        print(f"gate: {gate['name']} {gate['status']} ({gate['target']})")

    matrix = build_matrix(gates, args.generated_at or datetime_now())
    matrix = with_integrity(matrix)
    payload = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(matrix), encoding="utf-8")
    counts = matrix["summary"]["by_status"]
    print("summary: " + "; ".join(f"{status}: {counts[status]}" for status in STATUSES))
    print(f"matrix: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
