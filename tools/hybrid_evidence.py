#!/usr/bin/env python3
"""Machine-readable evidence schema and validator for the hybrid pipeline.

This module *defines* the hybrid evidence record and *validates* one against
facts recomputed from CLI-supplied paths. It never runs a game, never launches
Frida/Unicorn and never trusts a hash, a PE fact, an OS/arch label or a verdict
written in the input document. Those are claims; the files on disk are facts.

The input document (``--claim``) has this shape::

    {
      "schema": 1,
      "target": {"os": "macOS", "arch": "arm64"},
      "hashes": {
        "original": "<sha256 of the original PE32 x86 exe>",
        "native":   "<sha256 of the rebuilt native binary>",
        "scenario": "<sha256 of the scenario JSON>",
        "frames":   ["<sha256 of each frame artifact>"]
      },
      "pe_facts": {
        "machine": "i386", "pe_type": "PE32", "architecture": "x86",
        "image_base": "0x400000", "entry_point_rva": "0x1000",
        "sections": [".text", ".rdata"]          # optional declared facts
      },
      "unicorn":      {"status": "PASS", "nonvacuous": true, ...},
      "frida":        {"status": "BLOCKED", "reason": "<exact error>", ...},
      "input_replay": {"status": "PASS", "original": {...}, "native": {...}},
      "artifacts":    {"original": {"sha256": "..."}, ...},   # optional binding
      "verdict":      {"status": "PASS", "reasons": ["..."]}  # advisory only
    }

CLI paths (``--original``, ``--native``, ``--scenario``, ``--frames``) are
authoritative. Every claimed digest is recomputed from those files; every
claimed PE fact is re-derived from the original file bytes; the native target
is detected from the native binary itself (ELF / Mach-O / PE headers) and
compared with the claimed ``target`` pair. A mismatch is never silently fixed.

Verdict vocabulary (the only statuses this schema can emit)::

    PASS  PARTIAL  BUILD ONLY  FAIL  BLOCKED  NOT TESTED  DEFERRED

There is deliberately no percentage field and no blanket compatibility score.
A written percentage, a legacy status (``MATCH``, ``DIVERGED``, ``OK``, ...), a
fabricated hash, a missing artifact or a wrong OS/arch can only *demote* a
record. PASS additionally requires real Frida call evidence, a non-vacuous
Unicorn run, a labelled original runner, a scenario and frame artifacts; the
usual Frida-BLOCKED process-level result is therefore PARTIAL, never a 100%
compatibility claim.

Usage::

    python3 -m tools.hybrid_evidence \\
        --claim evidence/hybrid/claim.json \\
        --original <exe> --native <binary> \\
        --scenario <scenario.json> --frames <frame.png> \\
        --out <report.json> [--markdown <report.md>] \\
        [--generated-at 2026-01-01T00:00:00Z] [--strict]

``--strict`` returns a non-zero exit status unless the validated verdict is
PASS or PARTIAL. Without it the tool always writes a report (a demotion is a
result, not a crash).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/hybrid_evidence.py"

STATUSES = ("PASS", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED", "NOT TESTED",
            "DEFERRED")
# Least to most conservative. FAIL outranks BLOCKED (fabricated evidence is a
# stronger refusal than an absent engine); NOT TESTED and DEFERRED are tied.
STATUS_RANK = {
    "PASS": 0, "PARTIAL": 1, "BUILD ONLY": 2, "DEFERRED": 3,
    "NOT TESTED": 3, "BLOCKED": 4, "FAIL": 5,
}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# Legacy vocabulary from tools/hybrid_diff.py (engine level) and from the old
# hybrid reports (verdict level). It is translated explicitly and demoted; it
# is never accepted as a first-class status.
LEGACY_ENGINE = {
    "unicorn": {"OK": "PASS", "DIVERGED": "FAIL"},
    "frida": {"OK": "PASS"},
    "input_replay": {"MATCH": "PASS", "DIVERGED": "FAIL", "OK": "PASS"},
}
LEGACY_VERDICTS = frozenset({
    "MATCH", "DIVERGED", "OK", "SUCCESS", "PASSED", "FAILED", "GREEN", "RED",
    "N/A", "NA", "YES", "NO", "TRUE", "FALSE", "100", "100%", "PASS (100%)",
})

SUPPORTED_OS = ("Windows", "macOS", "Linux")
SUPPORTED_ARCH = ("x86", "x64", "arm64")
SUPPORTED_PAIRS = frozenset({
    ("Windows", "x86"), ("Windows", "x64"),
    ("macOS", "x86"), ("macOS", "x64"), ("macOS", "arm64"),
    ("Linux", "x86"), ("Linux", "x64"), ("Linux", "arm64"),
})
OS_ALIASES = {
    "windows": "Windows", "win": "Windows", "win32": "Windows",
    "win64": "Windows", "winnt": "Windows", "windows-nt": "Windows",
    "macos": "macOS", "mac": "macOS", "osx": "macOS", "darwin": "macOS",
    "linux": "Linux", "gnu-linux": "Linux", "linux-gnu": "Linux",
}
ARCH_ALIASES = {
    "x86": "x86", "i386": "x86", "i486": "x86", "i586": "x86",
    "i686": "x86", "ia32": "x86", "pe32": "x86", "x86-32": "x86",
    "x64": "x64", "amd64": "x64", "x86_64": "x64", "x86-64": "x64",
    "pe32+": "x64", "arm64": "arm64", "aarch64": "arm64",
    "arm-64": "arm64", "armv8": "arm64",
}
MACHINE_ARCH = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}
PE_ARCH = {"i386": "x86", "x86-64": "x64", "x86_64": "x64",
           "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}

SHA_RE = re.compile(r"[0-9a-fA-F]{64}")
PERCENT_VALUE_RE = re.compile(r"^\s*\d{1,3}(?:\.\d+)?\s*%\s*$")
PERCENT_ANYWHERE_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%")
PERCENT_DISCLAIMER_RE = re.compile(
    r"\b(?:not|no|never|isn't|isnt)\s+(?:a\s+)?\d{1,3}(?:\.\d+)?\s*%", re.I)
PERCENT_KEYS = frozenset({
    "score", "percent", "percentage", "coverage", "compatibility",
    "confidence", "completion", "progress", "match_rate", "accuracy",
})

SCOPE = ("CPU/process-level hybrid evidence only; not a 100% compatibility, "
         "gameplay, frame or audio claim")

POLICY = {
    "verdict_vocabulary": list(STATUSES),
    "written_verdict_is_advisory_only": True,
    "all_hash_values_recomputed_from_cli_paths": True,
    "pe_facts_recomputed_from_original_bytes": True,
    "native_os_arch_detected_from_binary_headers": True,
    "legacy_status_strings_are_demoted": True,
    "blanket_percentages_are_demoted": True,
    "no_blanket_percentage_field": True,
    "claim_scope": SCOPE,
}


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value.strip()))


def norm_sha(value: Any) -> str | None:
    """A real 64-hex digest in lower case, or None (never a guess)."""
    return value.strip().lower() if is_sha256(value) else None


def digest_of(value: Any) -> str | None:
    """Accept a bare digest or a ``{"sha256": ...}`` wrapper."""
    if isinstance(value, dict):
        value = value.get("sha256") or value.get("actual") or value.get("hash")
    return norm_sha(value)


def sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip(), 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def max_status(statuses: list[str]) -> str:
    return max(statuses, key=lambda s: STATUS_RANK[s]) if statuses else "PASS"


def canonical_json(doc: Any) -> str:
    """Byte-for-byte deterministic JSON for an evidence report."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False) + "\n"


def write_canonical(doc: Any, path: Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(doc))
    return target


# ---------------------------------------------------------------------------
# Facts recomputed from the supplied binaries
# ---------------------------------------------------------------------------
def normalize_os(value: Any) -> str | None:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return OS_ALIASES.get(token)


def normalize_arch(value: Any) -> str | None:
    token = re.sub(r"[^a-z0-9+#.]+", "-", str(value or "").strip().lower()).strip("-")
    return ARCH_ALIASES.get(token)


def pe_facts_from_file(path: Path) -> tuple[dict | None, str | None]:
    """Re-derive PE facts from the original file; no claim is consulted."""
    try:
        from tools.pe.pe_analyze import analyze_pe
    except Exception as exc:  # pragma: no cover - dependency import guard
        return None, f"PE analyzer unavailable: {type(exc).__name__}: {exc}"
    try:
        info = analyze_pe(str(path))
    except Exception as exc:
        return None, f"PE analysis failed: {type(exc).__name__}: {exc}"
    arch = PE_ARCH.get(str(info.machine).lower())
    return {
        "machine": info.machine,
        "pe_type": info.pe_type,
        "architecture": arch or str(info.machine),
        "image_base": hex(info.image_base),
        "entry_point_rva": hex(info.entry_point_rva),
        "entry_point": hex(info.image_base + info.entry_point_rva),
        "sections": [section.name for section in info.sections],
        "subsystem": info.subsystem,
        "is_dll": bool(info.is_dll),
    }, None


def detect_native_target(path: Path) -> tuple[str, str] | None:
    """Detect (os, arch) from native binary headers, or None if unknown.

    Only the first slice of a universal Mach-O is used; that is recorded as
    the detected target, not guessed from a file name.
    """
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(512)
    except OSError:
        return None
    if len(data) < 8:
        return None

    if data[:4] == b"\x7fELF":
        endian = ">" if data[5] == 2 else "<"
        machine = struct.unpack_from(endian + "H", data, 18)[0]
        arch = {3: "x86", 62: "x64", 183: "arm64"}.get(machine)
        return ("Linux", arch) if arch else None

    if data[:2] == b"MZ":
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset:pe_offset + 4] != b"PE\x00\x00" or pe_offset + 6 > len(data):
            return None
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        arch = MACHINE_ARCH.get(machine)
        return ("Windows", arch) if arch else None

    macho = {b"\xcf\xfa\xed\xfe": "<", b"\xce\xfa\xed\xfe": "<",
             b"\xfe\xed\xfa\xcf": ">", b"\xfe\xed\xfa\xce": ">"}
    if data[:4] in macho:
        endian = macho[data[:4]]
        cputype = struct.unpack_from(endian + "I", data, 4)[0]
        arch = {7: "x86", 0x01000007: "x64", 0x0100000C: "arm64"}.get(cputype)
        return ("macOS", arch) if arch else None

    if data[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        endian = ">" if data[:4] == b"\xca\xfe\xba\xbe" else "<"
        if len(data) >= 16:
            cputype = struct.unpack_from(endian + "I", data, 12)[0]
            arch = {7: "x86", 0x01000007: "x64", 0x0100000C: "arm64"}.get(cputype)
            return ("macOS", arch) if arch else None
    return None


def fact_equal(key: str, claimed: Any, actual: Any) -> bool:
    if key in ("image_base", "entry_point_rva", "entry_point"):
        left, right = as_int(claimed), as_int(actual)
        return left is not None and left == right
    if key in ("sections",):
        if not isinstance(claimed, list):
            return False
        return [str(x).lower() for x in claimed] == [str(x).lower() for x in actual]
    if key in ("is_dll",):
        return bool(claimed) == bool(actual)
    return str(claimed).strip().lower() == str(actual).strip().lower()


def find_blanket_percentages(value: Any, path: str = "$") -> list[str]:
    """Find asserted percentages (not prose like 'not a 100% claim')."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}"
            if str(key).strip().lower() in PERCENT_KEYS and (
                    isinstance(item, (int, float)) and not isinstance(item, bool)
                    or isinstance(item, str) and PERCENT_VALUE_RE.match(item)):
                found.append(f"{here}={item!r}")
            found.extend(find_blanket_percentages(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_blanket_percentages(item, f"{path}[{index}]"))
    elif isinstance(value, str) and (PERCENT_VALUE_RE.match(value)
                                     or PERCENT_ANYWHERE_RE.search(value)
                                     and not PERCENT_DISCLAIMER_RE.search(value)):
        found.append(f"{path}={value!r}")
    return found


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _minimal_report(reasons: list[dict[str, str]], generated_at: str) -> dict:
    """A complete report shell for a claim that cannot be validated at all."""
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason["code"]] = counts.get(reason["code"], 0) + 1
    status = max_status([reason["status"] for reason in reasons])
    empty_hash = {"path": None, "claimed": None, "actual": None, "match": False}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "target": {"os": None, "arch": None},
        "original_target": {"os": "Windows", "arch": None},
        "native_target": None,
        "hashes": {"original": dict(empty_hash), "native": dict(empty_hash),
                   "scenario": dict(empty_hash), "frames": []},
        "pe_facts": {"claimed": None, "actual": None, "match": False},
        "records": {engine: {"present": False, "status": "NOT TESTED"}
                    for engine in ("unicorn", "frida", "input_replay")},
        "verdict": {"status": status, "reasons": reasons},
        "claimed_verdict": {"status": None, "reasons": []},
        "policy": POLICY,
        "inputs": [],
        "summary": {
            "accepted": False,
            "reason_count": len(reasons),
            "by_reason_code": dict(sorted(counts.items())),
            "artifact_count": 0,
            "claim_scope": SCOPE,
        },
    }


def validate(claim: Any, *, original: Path | str | None = None,
             native: Path | str | None = None,
             scenario: Path | str | None = None,
             frames: list[Path | str] | None = None,
             generated_at: str | None = None) -> dict:
    """Validate a claim against the supplied files; return a fixed report.

    Every problem adds a reason carrying the status it caps the verdict at.
    The final status is the most conservative reason, so an optimistic written
    verdict can never raise a demoted record back to PASS.
    """
    reasons: list[dict[str, str]] = []
    inputs: list[dict[str, Any]] = []

    def add(code: str, status: str, message: str) -> None:
        reasons.append({"code": code, "status": status, "message": message})

    if not isinstance(claim, dict):
        add("INVALID_CLAIM", "NOT TESTED", "claim must be a JSON object")
        return _minimal_report(reasons, generated_at or utc_now())
    if claim.get("schema") != SCHEMA_VERSION:
        add("INVALID_SCHEMA", "NOT TESTED",
            f"expected schema {SCHEMA_VERSION}, got {claim.get('schema')!r}")
        return _minimal_report(reasons, generated_at or utc_now())

    for key in ("target", "hashes", "pe_facts", "unicorn", "frida",
                "input_replay", "verdict"):
        if not claim.get(key):
            add("MISSING_EVIDENCE", "BLOCKED", f"missing claim field: {key}")

    # -- hashes: recompute from CLI paths, never trust written digests ------
    claimed_hashes = claim.get("hashes") if isinstance(claim.get("hashes"), dict) else {}
    frame_claims = claimed_hashes.get("frames")
    frame_claims = frame_claims if isinstance(frame_claims, list) else []
    frame_paths = list(frames or [])
    hash_doc: dict[str, Any] = {}

    def check_hash(kind: str, path: Path | str | None, claimed: Any,
                   index: int | None = None) -> dict[str, Any]:
        label = kind if index is None else f"{kind}[{index}]"
        record: dict[str, Any] = {
            "path": str(path) if path is not None else None,
            "claimed": digest_of(claimed), "actual": None, "match": False,
        }
        if path is None or not Path(path).is_file():
            add("MISSING_EVIDENCE", "BLOCKED",
                f"{label}: artifact path missing or not a file: {path}")
            return record
        actual = sha256_file(Path(path))
        record["actual"] = actual
        inputs.append({"kind": label, "path": str(path), "sha256": actual,
                       "status": "ok" if actual else "UNREADABLE"})
        if actual is None:
            add("MISSING_EVIDENCE", "BLOCKED", f"{label}: artifact unreadable: {path}")
        elif record["claimed"] is None:
            add("FABRICATED_HASH", "FAIL",
                f"{label}: no valid 64-hex SHA-256 claimed")
        elif record["claimed"] != actual:
            add("HASH_MISMATCH", "FAIL",
                f"{label}: claimed {record['claimed']} != recomputed {actual}")
        else:
            record["match"] = True
        return record

    supplied = {"original": original, "native": native, "scenario": scenario}
    for kind in ("original", "native", "scenario"):
        hash_doc[kind] = check_hash(kind, supplied[kind], claimed_hashes.get(kind))
    hash_doc["frames"] = []
    if not frame_claims and not frame_paths:
        add("NO_FRAME_EVIDENCE", "PARTIAL",
            "no frame artifacts supplied; frame equivalence is not claimed")
    for index in range(max(len(frame_claims), len(frame_paths))):
        claimed = frame_claims[index] if index < len(frame_claims) else None
        path = frame_paths[index] if index < len(frame_paths) else None
        hash_doc["frames"].append(check_hash("frames", path, claimed, index))

    # -- PE facts: re-derive from the original bytes -----------------------
    claimed_facts = claim.get("pe_facts") if isinstance(claim.get("pe_facts"), dict) else {}
    actual_facts, pe_error = None, None
    if hash_doc["original"]["actual"] and original is not None:
        actual_facts, pe_error = pe_facts_from_file(Path(original))
    if pe_error:
        add("PE_ANALYSIS_FAILED", "BLOCKED", pe_error)
    required_facts = ("machine", "pe_type", "architecture", "image_base",
                      "entry_point_rva")
    facts_match = bool(actual_facts) and bool(claimed_facts)
    for key in required_facts:
        if key not in claimed_facts:
            add("MISSING_EVIDENCE", "BLOCKED", f"pe_facts: missing {key}")
            facts_match = False
    if actual_facts and claimed_facts:
        for key in list(required_facts) + ["sections", "subsystem", "is_dll",
                                           "entry_point"]:
            if key in claimed_facts and not fact_equal(key, claimed_facts[key],
                                                       actual_facts.get(key)):
                add("PE_FACT_MISMATCH", "FAIL",
                    f"pe_facts.{key}: claimed {claimed_facts[key]!r} != "
                    f"derived {actual_facts.get(key)!r}")
                facts_match = False
    if actual_facts and (actual_facts["architecture"] != "x86"
                         or actual_facts["pe_type"] != "PE32"):
        add("UNSUPPORTED_PE", "BLOCKED",
            f"original is {actual_facts['pe_type']} {actual_facts['architecture']}; "
            "this pipeline validates PE32 x86 originals only")

    # -- OS/arch pair: detect from the native binary, compare with the claim
    target = claim.get("target") if isinstance(claim.get("target"), dict) else {}
    claimed_os = normalize_os(target.get("os"))
    claimed_arch = normalize_arch(target.get("arch"))
    if not claimed_os or not claimed_arch:
        add("INVALID_TARGET", "BLOCKED",
            f"unrecognised target os/arch: {target.get('os')!r}/{target.get('arch')!r}")
    elif (claimed_os, claimed_arch) not in SUPPORTED_PAIRS:
        add("UNSUPPORTED_TARGET", "BLOCKED",
            f"unsupported target pair: {claimed_os}/{claimed_arch}")
    original_target = {"os": "Windows",
                       "arch": (actual_facts or {}).get("architecture")}
    claimed_original = claim.get("original_target")
    if isinstance(claimed_original, dict):
        if (normalize_os(claimed_original.get("os")) != original_target["os"]
                or normalize_arch(claimed_original.get("arch")) != original_target["arch"]):
            add("OS_ARCH_MISMATCH", "BLOCKED",
                f"original_target claims {claimed_original} but PE bytes are "
                f"{original_target}")
    native_target = detect_native_target(Path(native)) if native else None
    detected = ({"os": native_target[0], "arch": native_target[1]}
                if native_target else None)
    if detected and claimed_os and claimed_arch:
        if (detected["os"], detected["arch"]) != (claimed_os, claimed_arch):
            add("OS_ARCH_MISMATCH", "BLOCKED",
                f"target claims {claimed_os}/{claimed_arch} but native binary "
                f"headers are {detected['os']}/{detected['arch']}")
    elif detected is None and native is not None:
        add("UNKNOWN_NATIVE_TARGET", "PARTIAL",
            "native binary headers do not identify a supported OS/arch")

    # -- optional artifact binding to the hybrid_diff record --------------
    artifacts = claim.get("artifacts") if isinstance(claim.get("artifacts"), dict) else {}
    for kind in ("original", "native"):
        bound = digest_of(artifacts.get(kind))
        actual = hash_doc[kind]["actual"]
        if bound and actual and bound != actual:
            add("ARTIFACT_BINDING_MISMATCH", "FAIL",
                f"artifacts.{kind} claims {bound} but file is {actual}")

    # -- engine records ----------------------------------------------------
    records: dict[str, Any] = {}
    for engine in ("unicorn", "frida", "input_replay"):
        raw = claim.get(engine)
        if not isinstance(raw, dict) or not raw:
            add("MISSING_EVIDENCE", "BLOCKED", f"missing {engine} record")
            records[engine] = {"present": False, "status": "NOT TESTED"}
            continue
        raw_status = str(raw.get("status") or "").strip().upper()
        legacy_map = LEGACY_ENGINE[engine]
        if raw_status in legacy_map:
            status = legacy_map[raw_status]
            add("LEGACY_ENGINE_STATUS", "PARTIAL",
                f"{engine}: legacy status {raw_status!r} translated to "
                f"{status}; not a first-class verdict")
        elif raw_status in STATUSES:
            status = raw_status
        else:
            status = "BLOCKED"
            add("INVALID_ENGINE_STATUS", "BLOCKED",
                f"{engine}: unsupported status {raw_status!r}")
        record: dict[str, Any] = {
            "present": True, "raw_status": raw_status, "status": status,
            "legacy": raw_status in legacy_map,
        }

        if engine == "unicorn":
            if status == "PASS":
                for key in ("function", "section", "cases", "seeds", "distinct_eax"):
                    if key not in raw:
                        add("INCOMPLETE_EVIDENCE", "PARTIAL",
                            f"unicorn: missing {key}")
                if raw.get("nonvacuous") is not True:
                    add("VACUOUS_UNICORN", "BLOCKED",
                        "unicorn: nonvacuous=true required")
                distinct = raw.get("distinct_eax")
                if isinstance(distinct, int) and not isinstance(distinct, bool) and distinct < 2:
                    add("VACUOUS_UNICORN", "BLOCKED",
                        f"unicorn: distinct_eax={distinct} is vacuous")
                record["nonvacuous"] = raw.get("nonvacuous")
                record["distinct_eax"] = distinct
            elif status == "BLOCKED":
                reason = str(raw.get("reason") or "").strip()
                if not reason:
                    add("INCOMPLETE_EVIDENCE", "PARTIAL",
                        "unicorn: BLOCKED without an exact reason")
                add("ENGINE_BLOCKED", "BLOCKED", f"unicorn: {reason or 'no reason'}")
            elif status != "PARTIAL":
                add("ENGINE_NOT_PASS", status, f"unicorn status is {status}")

        elif engine == "frida":
            if status == "PASS":
                script = str(raw.get("script_sha256") or "").strip()
                if len(script) < 8:
                    add("INCOMPLETE_EVIDENCE", "PARTIAL",
                        "frida: missing script_sha256")
                calls = raw.get("calls")
                count = raw.get("call_count", raw.get("count"))
                if not (isinstance(calls, list) and calls) and not (
                        isinstance(count, int) and not isinstance(count, bool)
                        and count > 0):
                    add("INCOMPLETE_EVIDENCE", "PARTIAL",
                        "frida: PASS without any captured call evidence")
                record["script_sha256"] = script
            elif status == "BLOCKED":
                reason = str(raw.get("reason") or "").strip()
                if not reason:
                    add("INCOMPLETE_EVIDENCE", "PARTIAL",
                        "frida: BLOCKED without an exact reason")
                # A bounded environment limit demotes to PARTIAL; it never
                # fabricates a call trace and never BLOCKs the whole report.
                add("ENGINE_BLOCKED", "PARTIAL", f"frida: {reason or 'no reason'}")
                record["reason"] = reason
            elif status != "PARTIAL":
                add("ENGINE_NOT_PASS", status, f"frida status is {status}")

        else:  # input_replay
            if status == "PASS":
                orig = raw.get("original")
                nat = raw.get("native")
                if not isinstance(orig, dict) or not isinstance(nat, dict):
                    add("INCOMPLETE_EVIDENCE", "PARTIAL",
                        "input_replay: both original and native sides required")
                else:
                    out_orig = digest_of(orig.get("stdout_hash"))
                    out_nat = digest_of(nat.get("stdout_hash"))
                    if not out_orig or not out_nat:
                        add("INCOMPLETE_EVIDENCE", "PARTIAL",
                            "input_replay: missing stdout_hash on one or both sides")
                    elif out_orig != out_nat:
                        add("REPLAY_DIVERGENCE", "FAIL",
                            f"input_replay: stdout hashes differ "
                            f"({out_orig} vs {out_nat})")
                    elif out_orig == EMPTY_SHA256:
                        add("EMPTY_OUTPUT", "PARTIAL",
                            "input_replay: both stdout hashes are empty")
                    if orig.get("exit_code") != nat.get("exit_code"):
                        add("REPLAY_DIVERGENCE", "FAIL",
                            f"input_replay: exit codes differ "
                            f"({orig.get('exit_code')} vs {nat.get('exit_code')})")
                    runner = str(orig.get("runner") or "")
                    if runner not in ("highball", "wine-fallback"):
                        add("UNLABELLED_RUNNER", "PARTIAL",
                            f"input_replay: unlabelled original runner {runner!r}")
                    elif runner == "wine-fallback":
                        add("FALLBACK_RUNNER", "PARTIAL",
                            "input_replay: raw Wine fallback, not the Highball path")
                    command = orig.get("command")
                    if original is not None and isinstance(command, list) and command:
                        if str(Path(original).name) not in " ".join(str(x) for x in command):
                            add("RUNNER_NOT_BOUND", "PARTIAL",
                                "input_replay: original command does not name the "
                                "supplied original artifact")
                    record["runner"] = runner
                    record["stdout_hash"] = out_orig
            elif status in ("FAIL",):
                add("REPLAY_DIVERGENCE", "FAIL", "input_replay status is FAIL")
            elif status == "BLOCKED":
                reason = str(raw.get("reason") or "").strip()
                if not reason:
                    add("INCOMPLETE_EVIDENCE", "PARTIAL",
                        "input_replay: BLOCKED without an exact reason")
                add("ENGINE_BLOCKED", "BLOCKED", f"input_replay: {reason or 'no reason'}")
            elif status != "PARTIAL":
                add("ENGINE_NOT_PASS", status, f"input_replay status is {status}")
        records[engine] = record

    # -- the written verdict is advisory only -----------------------------
    verdict = claim.get("verdict") if isinstance(claim.get("verdict"), dict) else {}
    written_status = str(verdict.get("status") or "").strip().upper()
    written_reasons = [str(x) for x in verdict.get("reasons", [])] if isinstance(
        verdict.get("reasons"), list) else []
    if not written_reasons:
        add("INCOMPLETE_EVIDENCE", "PARTIAL", "verdict carries no reasons")
    if written_status in STATUSES:
        pass  # advisory: it can only make the result more conservative
    elif written_status in LEGACY_VERDICTS:
        add("OLD_STATUS_STRING", "NOT TESTED",
            f"legacy verdict status {written_status!r} rejected; "
            f"use one of {', '.join(STATUSES)}")
    else:
        add("INVALID_STATUS", "NOT TESTED",
            f"unsupported verdict status {written_status!r}")

    # -- blanket percentages are never evidence ---------------------------
    for location in find_blanket_percentages(claim):
        add("BLANKET_PERCENTAGE", "PARTIAL",
            f"blanket percentage asserted at {location}; not an evidence gate")

    final_status = max_status([reason["status"] for reason in reasons])
    if written_status in STATUSES and STATUS_RANK[written_status] > STATUS_RANK[final_status]:
        add("CLAIM_DOWNGRADED", written_status,
            f"written verdict {written_status} is more conservative than the "
            f"evidence-derived {final_status}")
        final_status = written_status
    elif written_status == "PASS" and final_status != "PASS":
        add("CLAIM_NOT_EVIDENCED", final_status,
            "written PASS claim is not supported by the validated evidence")

    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason["code"]] = counts.get(reason["code"], 0) + 1
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at or utc_now(),
        "target": {"os": claimed_os, "arch": claimed_arch},
        "original_target": original_target,
        "native_target": detected,
        "hashes": hash_doc,
        "pe_facts": {"claimed": claimed_facts or None,
                     "actual": actual_facts, "match": facts_match},
        "records": records,
        "verdict": {"status": final_status, "reasons": reasons},
        "claimed_verdict": {"status": written_status or None,
                            "reasons": written_reasons},
        "policy": POLICY,
        "inputs": sorted(inputs, key=lambda item: (item["kind"], item["path"])),
        "summary": {
            "accepted": final_status in ("PASS", "PARTIAL"),
            "reason_count": len(reasons),
            "by_reason_code": dict(sorted(counts.items())),
            "artifact_count": len(inputs),
            "claim_scope": SCOPE,
        },
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
def _md(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    """Render a validated report as a compact, honest Markdown summary."""
    verdict = report["verdict"]
    lines = [
        "# Hybrid evidence validation",
        "",
        f"Generated: {_md(report['generated_at'])}  ",
        f"Schema: {report['schema']}  ",
        f"Verdict: **{verdict['status']}**",
        "",
        f"> Scope: {SCOPE}",
        "",
        "## Target",
        "",
        f"- Claimed target: {_md(report['target']['os'])} / "
        f"{_md(report['target']['arch'])}",
        f"- Original (from PE bytes): {_md(report['original_target']['os'])} / "
        f"{_md(report['original_target']['arch'])}",
        f"- Native (from binary headers): "
        f"{(report['native_target'] or {}).get('os', '-')} / "
        f"{(report['native_target'] or {}).get('arch', '-')}",
        "",
        "## Hashes (claimed vs recomputed)",
        "",
        "| Artifact | Claimed SHA-256 | Recomputed SHA-256 | Match |",
        "| --- | --- | --- | --- |",
    ]
    for kind in ("original", "native", "scenario"):
        item = report["hashes"][kind]
        lines.append(f"| {kind} | `{_md(item['claimed'])}` | "
                     f"`{_md(item['actual'])}` | {item['match']} |")
    for index, item in enumerate(report["hashes"]["frames"]):
        lines.append(f"| frames[{index}] | `{_md(item['claimed'])}` | "
                     f"`{_md(item['actual'])}` | {item['match']} |")
    lines += ["", "## PE facts", ""]
    facts = report["pe_facts"]
    lines.append(f"- Match: {facts['match']}")
    if facts["actual"]:
        for key in ("machine", "pe_type", "architecture", "image_base",
                    "entry_point_rva", "sections"):
            lines.append(f"- {key}: `{_md(facts['actual'].get(key))}`")
    lines += ["", "## Engine records", ""]
    for engine, record in report["records"].items():
        lines.append(f"- {engine}: **{_md(record.get('status'))}**"
                     + (" (legacy status translated)" if record.get("legacy") else "")
                     + (f" — {_md(record.get('reason'))}" if record.get("reason") else ""))
    lines += ["", "## Verdict reasons", ""]
    if verdict["reasons"]:
        lines.append("| Code | Cap | Detail |")
        lines.append("| --- | --- | --- |")
        for reason in verdict["reasons"]:
            lines.append(f"| {_md(reason['code'])} | {_md(reason['status'])} | "
                         f"{_md(reason['message'])} |")
    else:
        lines.append("None.")
    lines += ["", f"Claimed verdict: {_md(report['claimed_verdict']['status'])} "
                  "(advisory only).", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--claim", required=True, type=Path, help="claim JSON to validate")
    parser.add_argument("--original", type=Path, help="original PE32 x86 executable")
    parser.add_argument("--native", type=Path, help="rebuilt native binary")
    parser.add_argument("--scenario", type=Path, help="scenario evidence JSON")
    parser.add_argument("--frames", action="append", default=[], type=Path,
                        help="frame artifact (repeatable)")
    parser.add_argument("--out", required=True, type=Path, help="write canonical report JSON here")
    parser.add_argument("--markdown", type=Path, help="optionally write Markdown summary here")
    parser.add_argument("--generated-at", default=None,
                        help="ISO UTC timestamp override for deterministic output")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero unless the verdict is PASS or PARTIAL")
    args = parser.parse_args(argv)

    claim: Any = None
    try:
        claim = json.loads(args.claim.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        # A broken claim file is still a reportable NOT TESTED result.
        claim = None
    report = validate(claim, original=args.original, native=args.native,
                      scenario=args.scenario, frames=args.frames,
                      generated_at=args.generated_at or utc_now())
    write_canonical(report, args.out)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8",
                                 newline="\n")
    status = report["verdict"]["status"]
    print(f"verdict: {status}; reasons: {len(report['verdict']['reasons'])}")
    print(f"report: {args.out}")
    if args.strict and status not in ("PASS", "PARTIAL"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
