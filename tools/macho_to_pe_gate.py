#!/usr/bin/env python3
"""Fail-closed feasibility gate for the Mach-O -> Windows PE bridge (#132/#135).

This module is a *verifier*, not an implementation of the bridge. It never
runs a component, builds a PE, executes a PE, reads a repository path unless
that path was supplied through the CLI, or claims that reverse-porting works.
It only joins artifacts named by three CLI-supplied JSON documents and refuses
to upgrade anything it could not verify from those artifacts.

Verdicts are ``PASS``, ``DEFERRED``, ``BLOCKED`` and ``NOT TESTED``. The rule is:

  * ``NOT TESTED`` -- a required CLI document is absent, unreadable or invalid
    JSON. Nothing was evaluated.
  * ``DEFERRED`` -- the supplied evidence is coherent but incomplete: a
    component is unavailable, an artifact was not declared, or no build / run
    was attempted. This is the roadmap's current state.
  * ``BLOCKED`` -- evidence was declared and *contradicted*: a file is missing,
    a hash disagrees, a report verdict is not PASS, the PE header is not a PE,
    the source is not a Mach-O, or the named oracle is not an executed process.
  * ``PASS`` -- every required component is proven AND one real Mach-O was
    built into a real PE AND that exact PE was executed under a real oracle.

Precedence when sections disagree: ``NOT TESTED > BLOCKED > DEFERRED > PASS``.
A ``PASS`` is feasibility evidence for the *one* supplied artifact only; the
result always carries ``support_claimed: false`` and ``reverse_porting_works:
false``. No verdict of this tool may be quoted as "Mach-O -> PE works".

CLI inputs (all paths come from the command line; paths inside a document are
resolved relative to the directory of the document that declared them):

``--components``
    ``{"pe_writer": {...}, "win32_runtime": {...}, "x86_backend": {...},
       "x64_backend": {...}}``. Each row needs ``available: true``, an
    ``artifact: {"path": ..., "sha256": ...}``, an AGPL-compatible ``license``
    and a ``proof: {"path": ..., "sha256": ...}`` naming an executed PASS
    self-test report (``verdict: PASS``, matching ``component``, non-empty
    ``command``). A bare flag is never enough.

``--build``
    ``{"source_macho": {"path": ..., "sha256": ...},
       "pe": {"path": ..., "sha256": ..., "arch": "x86"|"x64"},
       "report": {"path": ..., "sha256": ...}}``. The report must be PASS and
    its ``input_sha256`` / ``output_sha256`` must equal the Mach-O / PE digests
    recomputed here. Mach-O and PE magic bytes are checked, not trusted.

``--run``
    ``{"report": {"path": ..., "sha256": ...}}``. The report must be PASS,
    ``executed: true``, name a scenario, an executed-process ``oracle``, an
    integer ``exit_code``, the built ``pe_sha256``, and an ``execution log``
    artifact (path + sha256) that exists and is non-empty.

Usage::

    python3 -m tools.macho_to_pe_gate --components c.json --build b.json \
        --run r.json [--json decision.json] [--generated-at ...]

Exit status is 0 only for ``PASS``; all other verdicts exit 3.

Limits: this gate does not compile, lift, execute or license-audit anything
itself. A PASS means the CLI-supplied artifacts are internally consistent;
it is not a support claim and it does not replace an end-to-end acceptance
run with a real game and a real Windows oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/macho_to_pe_gate.py"
ISSUES = ("#132", "#135")

PASS = "PASS"
DEFERRED = "DEFERRED"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
RANK = {PASS: 0, DEFERRED: 1, BLOCKED: 2, NOT_TESTED: 3}

COMPONENT_LABELS = {
    "pe_writer": "PE writer",
    "win32_runtime": "Win32 runtime",
    "x86_backend": "x86 backend",
    "x64_backend": "x64 backend",
}
REQUIRED_COMPONENTS = tuple(COMPONENT_LABELS)

# Declared licences that are compatible with this AGPL-3.0-only repository.
# Anything else (including an absent declaration) stays unproven.
LICENSE_ALLOWLIST = frozenset({
    "agpl-3.0", "agpl-3.0-only", "agpl-3.0-or-later", "agplv3",
    "gpl-2.0-or-later", "gpl-3.0-or-later", "lgpl-2.1-or-later",
    "mit", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "isc",
    "unlicense", "public-domain",
})

# Only a named, executed original process is an oracle. Everything else is
# stage evidence and cannot turn a reported PASS into a gate PASS.
REAL_ORACLES = frozenset({
    "original-windows", "original-windows-process", "windows-oracle",
    "windows-original", "windows-process", "original-process",
    "original-vs-native", "original-game", "retail-windows",
    "retail-original",
})

MACHO_MAGICS = frozenset({
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",  # 32-bit, both endiannesses
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",  # 64-bit, both endiannesses
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # fat, both endiannesses
})
PE_ARCH = {0x014C: "x86", 0x8664: "x64"}
PE_MAGIC = {"x86": 0x010B, "x64": 0x020B}

RULE = (
    "PASS is feasibility evidence for the one supplied built+executed PE only; "
    "it is not a claim that Mach-O -> Windows PE reverse porting works"
)
LIMITATIONS = (
    "this gate never builds, executes or licence-audits anything itself; it "
    "verifies CLI-supplied artifacts only",
    "a PASS does not close #132 or #135 and is not a support claim",
)

_HEX = frozenset("0123456789abcdefABCDEF")


class LoadError(Exception):
    """A required CLI document is missing, unreadable or not a JSON object."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX for ch in value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def worse(current: str, candidate: str) -> str:
    """Escalate a section verdict using the documented precedence."""
    return candidate if RANK[candidate] > RANK[current] else current


def _load(path_text: str | None, flag: str) -> tuple[dict, Path]:
    """Load one CLI document; a failure is reported as NOT TESTED."""
    if not path_text or not path_text.strip():
        raise LoadError(f"MISSING_INPUT: no {flag} path supplied")
    path = Path(path_text.strip())
    if not path.is_file():
        raise LoadError(f"MISSING_INPUT: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoadError(f"INVALID_JSON: {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise LoadError(f"INVALID_STRUCTURE: {path}: expected a JSON object")
    return doc, path


def _resolve(raw: Any, base: Path) -> Path | None:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else base / path


def _artifact(value: Any, base: Path, label: str,
              reasons: list[str]) -> tuple[str, Path | None, str | None]:
    """Verify a declared {path, sha256} artifact.

    Returns (state, path, digest) where state is ``ok``, ``absent`` (nothing
    declared -- DEFERRED) or ``bad`` (declared but contradicted -- BLOCKED).
    """
    if not isinstance(value, dict) or not value:
        reasons.append(f"{label}: no artifact declared")
        return "absent", None, None
    raw_path = value.get("path")
    path = _resolve(raw_path, base)
    if path is None:
        reasons.append(f"{label}: no artifact path declared")
        return "bad", None, None
    if not path.is_file():
        reasons.append(f"{label}: artifact missing: {path.name}")
        return "bad", path, None
    declared = value.get("sha256")
    if not is_sha256(declared):
        reasons.append(f"{label}: invalid declared sha256: {path.name}")
        return "bad", path, None
    try:
        actual = sha256_file(path)
    except OSError:
        reasons.append(f"{label}: artifact unreadable: {path.name}")
        return "bad", path, None
    if actual != declared.lower():
        reasons.append(f"{label}: sha256 mismatch: {path.name}")
        return "bad", path, actual
    return "ok", path, actual


def _report(value: Any, base: Path, label: str, reasons: list[str]
            ) -> tuple[str, Path | None, dict | None]:
    """Verify an artifact that must also be a readable JSON object."""
    state, path, _ = _artifact(value, base, label, reasons)
    if state != "ok" or path is None:
        return state, path, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        reasons.append(f"{label}: invalid JSON: {path.name}: {exc}")
        return "bad", path, None
    if not isinstance(doc, dict):
        reasons.append(f"{label}: not a JSON object: {path.name}")
        return "bad", path, None
    return "ok", path, doc


def _apply(state: str, status: str) -> str:
    if state == "absent":
        return worse(status, DEFERRED)
    if state == "bad":
        return worse(status, BLOCKED)
    return status


def _norm(value: Any) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _is_macho(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(4) in MACHO_MAGICS
    except OSError:
        return False


def pe_arch(path: Path) -> str | None:
    """Parse just enough PE header to prove this is a real PE32/PE32+ image."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset < 0 or offset + 24 > len(data):
        return None
    if data[offset:offset + 4] != b"PE\x00\x00":
        return None
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    opt_size = struct.unpack_from("<H", data, offset + 20)[0]
    opt_off = offset + 24
    arch = PE_ARCH.get(machine)
    if arch is None or opt_size < 2 or opt_off + opt_size > len(data):
        return None
    magic = struct.unpack_from("<H", data, opt_off)[0]
    return arch if magic == PE_MAGIC[arch] else None


def _check_components(doc: dict, base: Path, reasons: list[str]) -> str:
    status = PASS
    if not doc:
        reasons.append("components: no component evidence declared")
        return DEFERRED
    for key, label in COMPONENT_LABELS.items():
        row = doc.get(key)
        if not isinstance(row, dict) or not row:
            reasons.append(f"{label}: availability not declared")
            status = worse(status, DEFERRED)
            continue
        available = row.get("available", row.get("present"))
        if available is not True:
            reason = ("not available" if available is False
                      else "availability not declared")
            reasons.append(f"{label}: {reason}")
            status = worse(status, DEFERRED)
            continue

        state, _, _ = _artifact(row.get("artifact"), base, label, reasons)
        status = _apply(state, status)

        licence = row.get("license", row.get("licence"))
        norm_licence = licence.strip().lower() if isinstance(licence, str) else ""
        if not norm_licence:
            reasons.append(f"{label}: licence not declared")
            status = worse(status, BLOCKED)
        elif norm_licence not in LICENSE_ALLOWLIST:
            reasons.append(f"{label}: licence {norm_licence} not AGPL-compatible")
            status = worse(status, BLOCKED)

        state, _, proof = _report(row.get("proof"), base,
                                  f"{label} proof", reasons)
        status = _apply(state, status)
        if proof is None:
            continue
        if _norm(proof.get("verdict", proof.get("status"))) != PASS:
            reasons.append(f"{label}: proof verdict is not PASS")
            status = worse(status, BLOCKED)
        named = proof.get("component")
        if named != key:
            reasons.append(f"{label}: proof names component {named!r}, expected {key!r}")
            status = worse(status, BLOCKED)
        command = proof.get("command")
        if not isinstance(command, str) or not command.strip():
            reasons.append(f"{label}: proof has no command")
            status = worse(status, BLOCKED)
    return status


def _check_build(doc: dict, base: Path, reasons: list[str]
                 ) -> tuple[str, str | None]:
    """Return (status, PE digest); the digest is only returned on a clean PASS."""
    keys = ("source_macho", "pe", "report")
    if not any(doc.get(key) for key in keys):
        reasons.append("build: no PE build evidence")
        return DEFERRED, None

    status = PASS
    source_digest = None
    source_state, source_path, source_digest = _artifact(
        doc.get("source_macho"), base, "build Mach-O source", reasons)
    status = _apply(source_state, status)
    if source_state == "ok" and source_path is not None and not _is_macho(source_path):
        reasons.append(f"build: source is not a Mach-O image: {source_path.name}")
        status = worse(status, BLOCKED)

    pe_digest = None
    pe_state, pe_path, pe_digest = _artifact(
        doc.get("pe"), base, "build PE artifact", reasons)
    status = _apply(pe_state, status)
    if pe_state == "ok" and pe_path is not None:
        header_arch = pe_arch(pe_path)
        if header_arch is None:
            reasons.append(f"build: PE signature/header invalid: {pe_path.name}")
            status = worse(status, BLOCKED)
        pe_row = doc.get("pe") if isinstance(doc.get("pe"), dict) else {}
        declared_arch = _norm(doc.get("arch") or pe_row.get("arch")).lower()
        if declared_arch and declared_arch in {"x86", "x64"} and header_arch \
                and declared_arch != header_arch:
            reasons.append(f"build: PE arch {declared_arch!r} disagrees with header")
            status = worse(status, BLOCKED)

    report_state, _, report = _report(
        doc.get("report"), base, "build report", reasons)
    status = _apply(report_state, status)
    if report is not None:
        if _norm(report.get("verdict", report.get("status"))) != PASS:
            reasons.append("build: report verdict is not PASS")
            status = worse(status, BLOCKED)
        recorded_input = report.get("input_sha256")
        if source_digest is None or not is_sha256(recorded_input) \
                or recorded_input.lower() != source_digest:
            reasons.append("build: report input sha256 does not match Mach-O source")
            status = worse(status, BLOCKED)
        recorded_output = report.get("output_sha256")
        if pe_digest is None or not is_sha256(recorded_output) \
                or recorded_output.lower() != pe_digest:
            reasons.append("build: report output sha256 does not match PE artifact")
            status = worse(status, BLOCKED)

    return status, (pe_digest if status == PASS else None)


def _check_run(doc: dict, base: Path, built_pe: str | None,
               reasons: list[str]) -> str:
    if not doc.get("report"):
        reasons.append("run: no executed PE evidence")
        return DEFERRED

    status = PASS
    state, report_path, report = _report(
        doc.get("report"), base, "run report", reasons)
    status = _apply(state, status)
    if report is None:
        return status

    if _norm(report.get("verdict", report.get("status"))) != PASS:
        reasons.append("run: report verdict is not PASS")
        status = worse(status, BLOCKED)
    if report.get("executed") is not True:
        reasons.append("run: report does not attest execution")
        status = worse(status, BLOCKED)
    scenario = report.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        reasons.append("run: no scenario declared")
        status = worse(status, BLOCKED)

    oracle = report.get("oracle")
    if isinstance(oracle, dict):
        oracle = oracle.get("type") or oracle.get("name")
    norm_oracle = oracle.strip().lower() if isinstance(oracle, str) else ""
    if norm_oracle not in REAL_ORACLES:
        reasons.append(f"run: oracle {oracle!r} is not an executed process")
        status = worse(status, BLOCKED)

    if not isinstance(report.get("exit_code"), int) or isinstance(
            report.get("exit_code"), bool):
        reasons.append("run: no exit code recorded")
        status = worse(status, BLOCKED)

    recorded_pe = report.get("pe_sha256")
    if built_pe is None:
        reasons.append("run: no built PE to match")
        status = worse(status, DEFERRED)
    elif not is_sha256(recorded_pe) or recorded_pe.lower() != built_pe:
        reasons.append("run: report pe sha256 does not match built PE")
        status = worse(status, BLOCKED)

    log_base = report_path.parent if report_path is not None else base
    log_raw = report.get("log", report.get("run_log"))
    log_state, log_path, _ = _artifact(log_raw, log_base,
                                       "run execution log", reasons)
    status = _apply(log_state, status)
    if log_state == "ok" and log_path is not None:
        try:
            if log_path.stat().st_size == 0:
                reasons.append(f"run: execution log is empty: {log_path.name}")
                status = worse(status, BLOCKED)
        except OSError:
            reasons.append(f"run: execution log unreadable: {log_path.name}")
            status = worse(status, BLOCKED)
    return status


def _blocked(reasons: list[str], status: str = NOT_TESTED) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "issues": list(ISSUES),
        "verdict": status,
        "gate_allowed": False,
        "support_claimed": False,
        "reverse_porting_works": False,
        "claim": "NO_SUPPORT_CLAIM",
        "checks": {
            "components": NOT_TESTED, "build": NOT_TESTED, "run": NOT_TESTED,
        },
        "reasons": reasons,
        "rule": RULE,
        "pass_requires": [
            "all four components proven by CLI-supplied artifacts",
            "a real Mach-O built into a real PE with linked hashes",
            "that exact PE executed under a real oracle with a non-empty log",
        ],
        "limitations": list(LIMITATIONS),
    }


def evaluate(components: dict, build: dict, run: dict, *,
             components_path: Path, build_path: Path, run_path: Path,
             generated_at: str | None = None) -> dict:
    """Pure decision function: reads only paths named by the CLI documents."""
    reasons: list[str] = []
    checks = {
        "components": _check_components(components, components_path.parent, reasons),
        "build": None,
        "run": None,
    }
    build_status, pe_digest = _check_build(build, build_path.parent, reasons)
    checks["build"] = build_status
    checks["run"] = _check_run(run, run_path.parent, pe_digest, reasons)

    verdict = PASS
    for status in checks.values():
        verdict = worse(verdict, status)
    result = _blocked(reasons, verdict)
    result["checks"] = checks
    result["gate_allowed"] = verdict == PASS
    result["generated_at"] = generated_at or utc_now()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", help="JSON: PE writer, Win32 runtime, x86/x64 backends")
    parser.add_argument("--build", help="JSON: source Mach-O, PE artifact, build report")
    parser.add_argument("--run", help="JSON: executed PE report and run log")
    parser.add_argument("--json", dest="json_out", help="write the decision JSON here")
    parser.add_argument("--generated-at", help="fixed UTC timestamp for deterministic output")
    args = parser.parse_args(argv)

    try:
        components, components_path = _load(args.components, "--components")
        build, build_path = _load(args.build, "--build")
        run, run_path = _load(args.run, "--run")
    except LoadError as exc:
        print(str(exc), file=sys.stderr)
        result = _blocked([str(exc)])
        result["generated_at"] = args.generated_at or utc_now()
    else:
        result = evaluate(components, build, run,
                          components_path=components_path,
                          build_path=build_path, run_path=run_path,
                          generated_at=args.generated_at)

    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    if args.json_out:
        try:
            Path(args.json_out).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"CANNOT_WRITE_DECISION: {args.json_out}: {exc}", file=sys.stderr)
            return 3
    print(text, end="")
    return 0 if result["verdict"] == PASS else 3


if __name__ == "__main__":
    sys.exit(main())
