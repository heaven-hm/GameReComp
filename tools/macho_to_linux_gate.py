#!/usr/bin/env python3
"""Fail-closed feasibility gate for Mach-O -> Linux (issue #136).

Why this exists
---------------
Issue #136 asks whether a macOS Mach-O can be rebuilt and run on native Linux.
Two very different things are called "Mach-O" here, and conflating them would
fabricate a Linux result:

* ``nextstep-i386`` -- NeXTSTEP/OPENSTEP fat Mach-O, of which ``tools/macho``
  lifts only the i386 slice to a native *host* build. On a Linux host that host
  build is a native ELF. This flavour is eligible for a feasibility PASS, but
  only if CLI-supplied evidence proves the host was Linux, a native ELF was
  built there, and a named scenario actually executed there.
* ``modern-macos-app`` -- a modern macOS ``.app`` (ARM64/x86_64 Mach-O, dyld
  shared cache, Objective-C/Swift runtimes). Nothing in this repository loads
  those runtimes or translates a Mach-O into a Linux ELF. This flavour is
  always DEFERRED, whatever evidence is supplied.

Inputs (single CLI-supplied evidence JSON, paths relative to that file)
----------------------------------------------------------------------
::

    {
      "schema": 1,
      "target": {"flavour": "nextstep-i386", "input": "sample.app/Foo"},
      "host":   {"os": "Linux", "machine": "x86_64", "kernel": "6.1.0"},
      "build":  {"path": "build/foo", "sha256": "<64 hex>",
                 "built_on": "Linux", "tool": "tools/macho"},
      "run":    {"executed": true, "host_os": "Linux", "scenario": "boot",
                 "exit_code": 0, "log_path": "run/foo.log",
                 "log_sha256": "<64 hex>", "elf_sha256": "<64 hex>"}
    }

The tool builds nothing, runs nothing and never inspects the local host: no
``platform``, ``uname`` or ``subprocess`` call participates in the verdict. It
reads the CLI-supplied evidence JSON, recomputes the SHA-256 of the named ELF
and log, checks ELF magic/class/type/machine from the ELF header itself, and
joins those facts into one verdict. A missing, malformed, non-Linux, unlinked
or self-contradictory input is NOT TESTED / BLOCKED / DEFERRED with an exact
reason; a written ``"status": "PASS"`` is advisory only. No Linux result is
claimed unless all three evidence gates pass together.

Usage::

    python3 -m tools.macho_to_linux_gate \
        --target nextstep-i386 --evidence evidence.json --out gate.json \
        [--generated-at 2026-01-01T00:00:00Z]

Exit status is 0 only for a PASS verdict, 3 for NOT TESTED / BLOCKED /
DEFERRED, and 4 if the report cannot be written.
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

SCHEMA = 1
GENERATOR = "tools/macho_to_linux_gate.py"
ISSUE = 136
DIRECTION = "Mach-O -> Linux"

PASS = "PASS"
NOT_TESTED = "NOT TESTED"
BLOCKED = "BLOCKED"
DEFERRED = "DEFERRED"
VERDICTS = (PASS, NOT_TESTED, BLOCKED, DEFERRED)

NEXTSTEP_I386 = "nextstep-i386"
MODERN_MACOS_APP = "modern-macos-app"
TARGETS = (NEXTSTEP_I386, MODERN_MACOS_APP)

LINUX_TOKENS = frozenset({"linux", "gnu-linux", "linux-gnu",
                          "linux-gnu-gnu", "linux-musl"})
ELF_MACHINES = {
    3: "i386", 8: "mips", 20: "ppc", 21: "ppc64", 40: "arm",
    62: "x86_64", 183: "arm64", 243: "riscv64",
}
MACHINE_ALIASES = {
    "i386": "i386", "i486": "i386", "i586": "i386", "i686": "i386",
    "x86": "i386", "x86-32": "i386", "ia32": "i386", "pe32": "i386",
    "x86-64": "x86_64", "x86_64": "x86_64", "amd64": "x86_64", "intel64": "x86_64",
    "arm": "arm", "armv7": "arm", "aarch64": "arm64", "arm64": "arm64",
    "mips": "mips", "ppc": "ppc", "powerpc": "ppc",
    "ppc64": "ppc64", "powerpc64": "ppc64", "riscv64": "riscv64",
}
ELF_CLASSES = {1: "ELFCLASS32", 2: "ELFCLASS64"}
ELF_TYPES = {2: "ET_EXEC", 3: "ET_DYN"}
HEX = frozenset("0123456789abcdefABCDEF")

# Mach-O flavours the gate enumerates explicitly (issue #136 acceptance #1).
FLAVOURS = (
    {
        "id": NEXTSTEP_I386,
        "name": "NeXTSTEP/OPENSTEP fat Mach-O, i386 slice",
        "tooling": "tools/macho: UFS reader, fat slices, LC_LOADFVMLIB import map",
        "support": "host-build-only",
        "linux_gate": "evidence-required",
        "diagnostic": ("Only the i386 slice is lifted, to a native host build. "
                       "A Linux result needs Linux-host, native-ELF and executed-"
                       "scenario evidence; other fat slices are refused."),
    },
    {
        "id": MODERN_MACOS_APP,
        "name": "modern macOS .app (ARM64/x86_64 Mach-O, dyld shared cache)",
        "tooling": "none in this repository",
        "support": "deferred",
        "linux_gate": "deferred",
        "diagnostic": ("No dyld shared-cache loader, no Objective-C/Swift runtime "
                       "bridge and no second recompiler; no Mach-O-to-ELF binary "
                       "translation is claimed."),
    },
)

REFUSED_VARIANTS = (
    {"variant": "NeXTSTEP fat m68k/hppa/sparc slices",
     "reason": "tools/macho lifts only the i386 slice"},
    {"variant": "thin ARM64/x86_64 macOS executable",
     "reason": "modern Mach-O runtime is DEFERRED, not a NeXTSTEP host build"},
    {"variant": "dyld shared cache",
     "reason": "no shared-cache loader is implemented"},
)

DEFERRED_REASONS = (
    "modern macOS .app is DEFERRED: no dyld shared-cache loader exists here",
    "modern macOS .app is DEFERRED: no Objective-C/Swift runtime bridge or "
    "second recompiler exists here",
    "modern macOS .app is DEFERRED: no Mach-O-to-Linux-ELF binary translation "
    "is claimed",
    "no Linux result can be inferred from a macOS host run of a modern .app",
)

LIMITS = (
    "tools/macho lifts only the NeXTSTEP i386 slice to a native HOST build; "
    "that is not modern macOS .app support",
    "This gate builds, runs and probes nothing; it joins CLI-supplied evidence only",
    "A build or unit test is stage evidence; no Linux result is claimed without "
    "host, native ELF and executed-scenario evidence",
    "Modern macOS .app bundles (dyld cache, Objective-C/Swift) remain DEFERRED",
    "This feasibility gate implies no game acceptance or oracle comparison",
)

POLICY = {
    "pass_requires": [
        "Linux host evidence with a machine matching the native ELF",
        "a native ELF built on Linux whose recomputed SHA-256 matches the claim",
        "an executed Linux scenario with a matching log hash linked to that ELF",
    ],
    "written_status_is_advisory_only": True,
    "flags_alone_cannot_pass": True,
    "no_local_host_probing": True,
    "no_fabricated_linux_result": True,
}


class ElfError(ValueError):
    """The supplied file is not an ELF we can trust as a native executable."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def norm_os(value: Any) -> str | None:
    return "Linux" if norm_token(value) in LINUX_TOKENS else None


def norm_machine(value: Any) -> str | None:
    return MACHINE_ALIASES.get(norm_token(value))


def is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in HEX for ch in value))


def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a CLI-named file, or None when it cannot be read."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def resolve_path(raw: Any, base: Path) -> Path | None:
    """Resolve a declared path; relative paths are relative to the evidence JSON."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else base / path


def inspect_elf(path: Path) -> dict:
    """Return verified ELF facts from the file header, or raise :class:`ElfError`.

    Fail-closed: a missing, short, non-ELF or non-executable file is never
    reported as a built native binary.
    """
    if not path.is_file():
        raise ElfError(f"native ELF file is missing: {path}")
    try:
        head = path.read_bytes()[:64]
    except OSError as exc:
        raise ElfError(f"native ELF file is unreadable: {path}: {exc}") from exc
    if head[:4] != b"\x7fELF":
        raise ElfError("built file is not a native ELF (missing 0x7fELF magic)")
    if len(head) < 18:
        raise ElfError("built file has a truncated ELF header")
    elf_class, elf_data = head[4], head[5]
    if elf_class not in ELF_CLASSES:
        raise ElfError(f"invalid ELF class {elf_class}")
    if elf_data not in (1, 2):
        raise ElfError(f"invalid ELF data encoding {elf_data}")
    endian = "<" if elf_data == 1 else ">"
    e_type, e_machine = struct.unpack_from(endian + "HH", head, 16)
    if e_type not in ELF_TYPES:
        raise ElfError(f"ELF is not an executable (e_type={e_type})")
    machine = ELF_MACHINES.get(e_machine)
    if machine is None:
        raise ElfError(f"unsupported ELF machine (e_machine={e_machine})")
    return {
        "magic": True,
        "class": ELF_CLASSES[elf_class],
        "data": "ELFDATA2LSB" if elf_data == 1 else "ELFDATA2MSB",
        "type": ELF_TYPES[e_type],
        "machine": machine,
        "e_machine": e_machine,
    }


def worst(states: Any) -> str:
    """BLOCKED outranks NOT TESTED, which outranks PASS."""
    states = list(states)
    if BLOCKED in states:
        return BLOCKED
    if NOT_TESTED in states:
        return NOT_TESTED
    return PASS


def _record(value: Any) -> dict | None:
    return value if isinstance(value, dict) else None


def check_host(host: dict | None) -> tuple[str, str | None, list[str], dict]:
    """Gate 1: the evidence must declare a Linux host and a known machine."""
    info: dict[str, Any] = {}
    if host is None:
        return NOT_TESTED, None, ["host: no CLI-supplied host evidence record"], info
    raw_os = host.get("os", host.get("host_os", host.get("platform")))
    info["declared_os"] = raw_os
    if not str(raw_os or "").strip():
        return NOT_TESTED, None, ["host: no OS declared in host evidence"], info
    if norm_os(raw_os) != "Linux":
        return BLOCKED, None, [
            f"host: OS {raw_os!r} is not Linux; refusing to claim a Linux result"
        ], info
    raw_machine = host.get("machine", host.get("arch"))
    machine = norm_machine(raw_machine)
    info.update(declared_machine=raw_machine, machine=machine)
    if machine is None:
        return NOT_TESTED, None, [
            f"host: machine {raw_machine!r} is missing or not recognised"
        ], info
    return PASS, f"host: Linux on {machine} (declared in CLI evidence)", None, info


def check_build(build: dict | None, base: Path) -> tuple[str, str | None, list[str], dict]:
    """Gate 2: a native ELF built on Linux, with a hash we recompute here."""
    info: dict[str, Any] = {}
    if build is None:
        return NOT_TESTED, None, ["build: no CLI-supplied native ELF build evidence"], info
    built_on = build.get("built_on", build.get("host_os", build.get("os")))
    info["built_on"] = built_on
    if not str(built_on or "").strip():
        return NOT_TESTED, None, ["build: no build host declared"], info
    if norm_os(built_on) != "Linux":
        return BLOCKED, None, [
            f"build: built on {built_on!r}, not Linux; refusing to claim a Linux result"
        ], info
    raw_path = build.get("path", build.get("elf"))
    path = resolve_path(raw_path, base)
    declared = build.get("sha256")
    info.update(declared_path=raw_path, path=str(path) if path else None,
                declared_sha256=declared)
    if path is None:
        return NOT_TESTED, None, ["build: no native ELF path declared"], info
    if not is_sha256(declared):
        return NOT_TESTED, None, ["build: declared native ELF sha256 is missing or invalid"], info
    actual = sha256_file(path)
    info["actual_sha256"] = actual
    if actual is None:
        return NOT_TESTED, None, [
            f"build: native ELF file is missing or unreadable: {path}"
        ], info
    if actual != declared.lower():
        return BLOCKED, None, [
            f"build: native ELF SHA-256 mismatch (declared {declared.lower()}, "
            f"recomputed {actual})"
        ], info
    try:
        elf = inspect_elf(path)
    except ElfError as exc:
        return BLOCKED, None, [f"build: {exc}"], info
    info["elf"] = elf
    detail = (f"build: native {elf['class']} {elf['type']} {elf['machine']} ELF "
              f"verified, SHA-256 {actual}")
    return PASS, detail, None, info


def check_run(run: dict | None, base: Path) -> tuple[str, str | None, list[str], dict]:
    """Gate 3: an executed, hash-linked scenario on the Linux host."""
    info: dict[str, Any] = {}
    if run is None:
        return NOT_TESTED, None, ["run: no CLI-supplied executed scenario evidence"], info
    executed = run.get("executed")
    info["executed"] = executed
    if executed is None:
        return NOT_TESTED, None, ["run: no 'executed' flag in scenario evidence"], info
    if executed is not True:
        return BLOCKED, None, [
            f"run: executed is {executed!r}, not true; no Linux execution is proven"
        ], info
    raw_host = run.get("host_os", run.get("os"))
    info["declared_host_os"] = raw_host
    if not str(raw_host or "").strip():
        return NOT_TESTED, None, ["run: no execution host OS declared"], info
    if norm_os(raw_host) != "Linux":
        return BLOCKED, None, [
            f"run: scenario executed on {raw_host!r}, not Linux; refusing to "
            "claim a Linux result"
        ], info
    scenario = run.get("scenario", run.get("scenario_name"))
    info["scenario"] = scenario
    if not str(scenario or "").strip():
        return NOT_TESTED, None, ["run: no named scenario in execution evidence"], info
    exit_code = run.get("exit_code")
    info["exit_code"] = exit_code
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return NOT_TESTED, None, ["run: no integer exit_code in execution evidence"], info
    if exit_code != 0:
        return BLOCKED, None, [
            f"run: scenario {scenario!r} exited {exit_code}, not 0"
        ], info
    raw_log = run.get("log_path", run.get("evidence_path"))
    log_path = resolve_path(raw_log, base)
    declared_log = run.get("log_sha256", run.get("evidence_sha256"))
    declared_elf = run.get("elf_sha256", run.get("native_sha256"))
    info.update(declared_log_path=raw_log, log_path=str(log_path) if log_path else None,
                declared_log_sha256=declared_log, declared_elf_sha256=declared_elf)
    if log_path is None:
        return NOT_TESTED, None, ["run: no execution log path declared"], info
    if not is_sha256(declared_log):
        return NOT_TESTED, None, ["run: declared execution log sha256 is missing or invalid"], info
    actual_log = sha256_file(log_path)
    info["actual_log_sha256"] = actual_log
    if actual_log is None:
        return NOT_TESTED, None, [
            f"run: execution log is missing or unreadable: {log_path}"
        ], info
    if actual_log != declared_log.lower():
        return BLOCKED, None, [
            f"run: execution log SHA-256 mismatch (declared {declared_log.lower()}, "
            f"recomputed {actual_log})"
        ], info
    if not is_sha256(declared_elf):
        return NOT_TESTED, None, [
            "run: no native ELF sha256 linking the scenario to the built binary"
        ], info
    detail = (f"run: scenario {scenario!r} executed on Linux (exit 0); log "
              f"SHA-256 {actual_log} matches and is linked to the built ELF")
    return PASS, detail, None, info


def deferred_report(target: str, generated_at: str,
                    evidence_source: dict) -> dict:
    """Modern .app is DEFERRED unconditionally: never a Linux result."""
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "issue": ISSUE,
        "direction": DIRECTION,
        "generated_at": generated_at,
        "target": target,
        "flavours": list(FLAVOURS),
        "refused_variants": list(REFUSED_VARIANTS),
        "verdict": DEFERRED,
        "reasons": list(DEFERRED_REASONS),
        "checks": {"host": NOT_TESTED, "build": NOT_TESTED, "run": NOT_TESTED,
                   "machine_match": NOT_TESTED, "elf_link": NOT_TESTED},
        "evidence": {"source": evidence_source},
        "hashes": {"evidence": evidence_source.get("sha256"),
                   "elf": None, "log": None},
        "linux_result_claimed": False,
        "result_scope": "no Linux result claimed for a modern macOS .app",
        "policy": dict(POLICY),
        "limits": list(LIMITS),
    }


def evaluate(target: str, doc: Any, evidence_path: Path,
             generated_at: str) -> dict:
    """Pure decision function over one CLI-supplied evidence document."""
    source = {"path": str(evidence_path), "sha256": sha256_file(evidence_path),
              "status": "ok" if evidence_path.is_file() else "MISSING"}
    if target == MODERN_MACOS_APP:
        # Deferred by design: no evidence, valid or otherwise, can turn a
        # modern macOS .app into a Linux result.
        return deferred_report(target, generated_at, source)
    if target != NEXTSTEP_I386:  # argparse already restricts this; fail closed anyway
        return {
            "schema": SCHEMA, "generator": GENERATOR, "issue": ISSUE,
            "direction": DIRECTION, "generated_at": generated_at,
            "target": target, "flavours": list(FLAVOURS),
            "refused_variants": list(REFUSED_VARIANTS),
            "verdict": BLOCKED, "reasons": [f"unknown target flavour {target!r}"],
            "checks": {}, "evidence": {"source": source},
            "hashes": {"evidence": source["sha256"], "elf": None, "log": None},
            "linux_result_claimed": False,
            "result_scope": "no Linux result claimed", "policy": dict(POLICY),
            "limits": list(LIMITS),
        }

    base = evidence_path.parent
    if not isinstance(doc, dict):
        return {
            "schema": SCHEMA, "generator": GENERATOR, "issue": ISSUE,
            "direction": DIRECTION, "generated_at": generated_at,
            "target": target, "flavours": list(FLAVOURS),
            "refused_variants": list(REFUSED_VARIANTS),
            "verdict": NOT_TESTED,
            "reasons": ["evidence: expected a JSON object, got "
                        f"{type(doc).__name__}"],
            "checks": {}, "evidence": {"source": source},
            "hashes": {"evidence": source["sha256"], "elf": None, "log": None},
            "linux_result_claimed": False,
            "result_scope": "no Linux result claimed", "policy": dict(POLICY),
            "limits": list(LIMITS),
        }

    gates = {
        "host": check_host(_record(doc.get("host"))),
        "build": check_build(_record(doc.get("build")), base),
        "run": check_run(_record(doc.get("run")), base),
    }
    checks = {name: gate[0] for name, gate in gates.items()}
    details = [gate[1] for gate in gates.values() if gate[1]]
    reasons = [text for gate in gates.values() for text in (gate[2] or [])]

    # Cross-check target and the two linkage facts that stop a macOS run being
    # presented as a Linux result or an unrelated ELF being credited.
    declared_target = _record(doc.get("target"))
    declared_flavour = declared_target.get("flavour") if declared_target else None
    if declared_flavour is not None and declared_flavour != target:
        reasons.append(f"target: evidence flavour {declared_flavour!r} conflicts "
                       f"with --target {target!r}")
        checks["target_match"] = BLOCKED
    else:
        checks["target_match"] = PASS

    host_info = gates["host"][3]
    build_info = gates["build"][3]
    run_info = gates["run"][3]
    if checks["host"] == PASS and checks["build"] == PASS:
        if host_info["machine"] != build_info["elf"]["machine"]:
            reasons.append(
                f"host: machine {host_info['machine']!r} does not match the "
                f"native ELF machine {build_info['elf']['machine']!r}")
            checks["machine_match"] = BLOCKED
        else:
            checks["machine_match"] = PASS
    else:
        checks["machine_match"] = NOT_TESTED

    if checks["build"] == PASS and checks["run"] == PASS:
        if run_info["declared_elf_sha256"].lower() != build_info["actual_sha256"]:
            reasons.append("run: elf_sha256 does not match the built native ELF "
                           "SHA-256 (scenario is not linked to this build)")
            checks["elf_link"] = BLOCKED
        else:
            checks["elf_link"] = PASS
    else:
        checks["elf_link"] = NOT_TESTED

    verdict = worst(checks.values())
    if verdict == PASS:
        reasons = details
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "issue": ISSUE,
        "direction": DIRECTION,
        "generated_at": generated_at,
        "target": target,
        "flavours": list(FLAVOURS),
        "refused_variants": list(REFUSED_VARIANTS),
        "verdict": verdict,
        "reasons": reasons,
        "checks": checks,
        "evidence": {
            "source": source,
            "declared_target": declared_flavour,
            "host": host_info,
            "build": build_info,
            "run": run_info,
        },
        "hashes": {
            "evidence": source["sha256"],
            "elf": build_info.get("actual_sha256"),
            "log": run_info.get("actual_log_sha256"),
        },
        "linux_result_claimed": verdict == PASS,
        "result_scope": ("NeXTSTEP i386 sample built and executed on the declared "
                         "Linux host" if verdict == PASS
                         else "no Linux result claimed"),
        "policy": dict(POLICY),
        "limits": list(LIMITS),
    }


def load_evidence(path: Path) -> tuple[Any, str | None]:
    """Read one CLI-named JSON file; return (document, error diagnostic)."""
    if not path.is_file():
        return None, f"evidence: file not found: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"evidence: invalid JSON: {path}: {exc}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, choices=TARGETS,
                    help="Mach-O flavour to gate")
    ap.add_argument("--evidence", required=True, type=Path,
                    help="CLI-supplied evidence JSON (the only input)")
    ap.add_argument("--out", required=True, type=Path,
                    help="write the deterministic gate report here")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for deterministic output")
    args = ap.parse_args(argv)

    generated_at = args.generated_at or utc_now()
    if args.target == MODERN_MACOS_APP:
        # The flavour alone decides DEFERRED; do not require parseable evidence.
        report = evaluate(args.target, {}, args.evidence, generated_at)
    else:
        doc, error = load_evidence(args.evidence)
        if error is None:
            report = evaluate(args.target, doc, args.evidence, generated_at)
        else:
            source = {"path": str(args.evidence),
                      "sha256": sha256_file(args.evidence), "status": "INVALID"}
            report = {
                "schema": SCHEMA, "generator": GENERATOR, "issue": ISSUE,
                "direction": DIRECTION, "generated_at": generated_at,
                "target": args.target, "flavours": list(FLAVOURS),
                "refused_variants": list(REFUSED_VARIANTS),
                "verdict": NOT_TESTED, "reasons": [error],
                "checks": {}, "evidence": {"source": source},
                "hashes": {"evidence": source["sha256"], "elf": None, "log": None},
                "linux_result_claimed": False,
                "result_scope": "no Linux result claimed", "policy": dict(POLICY),
                "limits": list(LIMITS),
            }
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    except OSError as exc:
        print(f"CANNOT_WRITE_REPORT: {args.out}: {exc}", file=sys.stderr)
        return 4
    print(f"{report['verdict']}: " + "; ".join(report["reasons"]))
    print(f"report: {args.out}")
    return 0 if report["verdict"] == PASS else 3


if __name__ == "__main__":
    sys.exit(main())
