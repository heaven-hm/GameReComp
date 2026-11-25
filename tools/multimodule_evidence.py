#!/usr/bin/env python3
"""Fail-closed verdicts for CLI-supplied PE32 multi-module boot records.

This tool joins three *CLI-supplied* evidence kinds and never runs a game, a
native binary, Frida, Wine or Unicorn itself:

1. ``--boot``: a multi-module boot record (JSON). It names the EXE, its
   companion DLL(s), the bound-import counts, the native binary hash and the
   frames that were captured. It contains *claims only*; nothing in it is
   promoted to a verdict.
2. ``--proof``: the committed run proof that the boot record cites. Its bytes
   are re-hashed and every module fact is re-derived from that document, never
   copied from the boot record. A separate companion import counter in the
   proof (for example ``companion_sdl_net_imports_bound``) is the evidence that
   a companion DLL really was mapped and had its own import table bound.
3. ``--frame``: the actual captured frame files. Their SHA-256 is recomputed
   from the bytes and each image is decoded to measure distinct RGB colours.
   A blank or undecodable frame is never render evidence.

Per-module verdicts (with exact reason codes in the output):

* ``VERIFIED`` -- the module is attested by the committed proof (EXE name and
  hash, or a separate companion mapping with a bound-import counter), its
  bound/unresolved import counts agree with the proof, the native binary hash
  agrees with the proof, and at least one proven non-blank frame was joined.
  This is a scoped boot/binding/render result, not a Windows gameplay oracle.
* ``PARTIAL`` -- positive but incomplete evidence: a companion without a
  mapping counter, a missing native hash, or no provable frame.
* ``BLOCKED`` -- no evidence, a module that is not attested by the proof, a
  self-declared verdict, contradictory counts, a hash that disagrees with the
  supplied bytes, unresolved imports or a blank/undecodable frame.

The overall verdict is ``VERIFIED`` only when every module is ``VERIFIED``;
otherwise it is ``PARTIAL`` (no BLOCKED module) or ``BLOCKED``. Exit status is
0 only for an overall ``VERIFIED``, 1 for ``PARTIAL``/``BLOCKED`` and 2 for an
output write error. Output is deterministic: sorted keys, sorted reasons, no
timestamps, no clock, no host path, no environment and no network value.

Usage::

    python3 -m tools.multimodule_evidence \
        --boot <boot-record.json> --proof <issue28-proof.json> \
        --frame <frame.bmp> ... [--binary <native-binary>] \
        [--out <verdict.json>] [--label <text>]

Boot record schema (``schema_version`` 1)::

    {
      "schema_version": 1,
      "label": "chocolate-doom.exe + SDL_net.dll",
      "source": {"path": "proof/issue-28/issue28-proof.json",
                 "sha256": "<sha256 of the --proof bytes>"},
      "native_binary": {"sha256": "<sha256 claimed by the proof>"},
      "modules": [
        {"name": "chocolate-doom.exe", "role": "exe",
         "sha256": "<original hash from proof.originals>",
         "imports_bound": 166, "unresolved_imports": 0},
        {"name": "SDL_net.dll", "role": "companion",
         "companion_of": "chocolate-doom.exe",
         "imports_bound": 34, "unresolved_imports": 0,
         "mapping": {"source_key": "companion_sdl_net_imports_bound",
                     "imports_bound": 34}}
      ],
      "frames": [{"path": "proof/issue-28/frames/frame-0000.bmp",
                  "sha256": "<frame sha256>"}]
    }

``mapping.source_key`` must be a ``companion_<name>_imports_bound`` key that
exists in the proof's ``run`` object; ``mapping.imports_bound`` must equal that
value and the module's own ``imports_bound``. No VA, span or export count is
invented: the only mapping fact claimed is one the committed proof records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePath
from typing import Any

try:  # `python3 -m tools.multimodule_evidence` and direct script use
    from .frame_evidence_check import frame_info as _frame_info
except ImportError:  # pragma: no cover - only exercised by direct script use
    try:
        from frame_evidence_check import frame_info as _frame_info
    except ImportError:  # pragma: no cover
        _frame_info = None

SCHEMA_VERSION = 1
TOOL = "tools/multimodule_evidence.py"
ISSUE = 9

HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMPANION_KEY_RE = re.compile(r"^companion_([a-z0-9_]+)_imports_bound$")
ROLES = ("exe", "companion")
FAKE_PASS_KEYS = ("verdict", "status", "pass", "passed", "verified", "resolved",
                  "promoted", "promotion", "module_verdict", "game_verdict")
FAKE_PASS_WORDS = {"PASS", "VERIFIED", "RESOLVED", "TRUE", "YES", "OK"}

# An input contradicts itself, the committed proof or the supplied bytes.
TAMPER_CODES = frozenset({
    "EVIDENCE_TAMPERED", "BOOT_INVALID", "BOOT_UNREADABLE", "BOOT_NOT_JSON",
    "SOURCE_MISSING", "SOURCE_INVALID", "SOURCE_HASH_INVALID",
    "SOURCE_HASH_MISMATCH", "SOURCE_PATH_MISMATCH", "PROOF_INVALID",
    "PROOF_UNREADABLE", "PROOF_NOT_JSON", "FAKE_PASS_CLAIM",
    "NATIVE_BINARY_HASH_INVALID", "NATIVE_BINARY_HASH_MISMATCH",
    "MODULE_INVALID", "MODULE_DUPLICATE", "MODULE_ROLE_INVALID",
    "MODULE_HASH_INVALID", "MODULE_HASH_MISMATCH", "MODULE_NOT_ATTESTED",
    "IMPORT_COUNT_INVALID", "IMPORT_COUNT_MISMATCH",
    "UNRESOLVED_INVALID", "UNRESOLVED_COUNT_MISMATCH",
    "COMPANION_OF_INVALID", "COMPANION_OF_MISSING",
    "MAPPING_INVALID", "MAPPING_NOT_ATTESTED", "MAPPING_COUNT_MISMATCH",
    "FRAME_CLAIMS_INVALID", "FRAME_CLAIM_INVALID", "FRAME_HASH_INVALID",
    "FRAME_HASH_MISMATCH", "FRAME_NOT_ATTESTED", "FRAME_PATH_MISMATCH",
    "FRAME_NAME_AMBIGUOUS",
})
# Explicit failure or a fake image; stronger than "incomplete".
NEGATIVE_CODES = frozenset({
    "NO_EVIDENCE", "NO_MODULES", "NO_EXE", "UNRESOLVED_IMPORTS_PRESENT",
    "MAPPING_ZERO_IMPORTS", "FRAME_BLANK", "FRAME_UNDECODABLE",
})
# Positive but missing facts: these yield PARTIAL, never VERIFIED.
INCOMPLETE_CODES = frozenset({
    "NATIVE_BINARY_HASH_MISSING", "FRAME_PROOF_MISSING",
    "FRAME_CLAIMS_INCOMPLETE", "FRAME_FILES_MISSING",
    "COMPANION_MAPPING_MISSING", "MODULE_HASH_MISSING",
})

LIMITS = (
    "VERIFIED is scoped to boot/binding/render facts: the committed proof "
    "attests the EXE hash, the separate companion import counter, the native "
    "binary hash and the frame digests, and this tool re-hashes and decodes "
    "the actual frame bytes. It is not a Windows-oracle or gameplay claim.",
    "No game, native, Frida, Wine or Unicorn is executed here. Native binary "
    "and companion DLL bytes are not committed with this evidence; their "
    "hashes are cross-checked between the boot record and the proof, not "
    "recomputed from bytes. --binary re-hashes the native binary when supplied.",
    "No value in any input is promoted by being claimed. A self-declared "
    "verdict, a module absent from the proof, a contradictory count or a "
    "hash that disagrees with supplied bytes is always BLOCKED.",
    "A blank, undecodable, unattested or tampered frame is never render "
    "evidence; distinct colours are measured from decoded pixels only.",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def reason(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def dedupe(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for item in items:
        key = (item["code"], item["detail"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return sorted(out, key=lambda item: (item["code"], item["detail"]))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64_RE.fullmatch(value) is not None


def norm_name(value: Any) -> str | None:
    """Normalise a module name for proof cross-checks (never for output)."""
    if not isinstance(value, str):
        return None
    base = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    base = base.rsplit(".", 1)[0] if "." in base else base
    token = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
    return token or None


def suffix_match(claim: str, actual: Path) -> bool:
    """True when the claimed relative path is a suffix of the supplied path."""
    want = [part for part in PurePath(claim.replace("\\", "/")).parts
            if part not in (".", "/")]
    have = PurePath(actual).parts
    if not want or len(want) > len(have):
        return False
    return tuple(want) == tuple(have[-len(want):])


def canonical_json(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True,
                      allow_nan=False) + "\n"


def read_bytes(path: Path, code: str) -> tuple[bytes | None, list[dict]]:
    try:
        return path.read_bytes(), []
    except OSError as exc:
        return None, [reason(f"{code}_UNREADABLE", f"{path}: {exc}")]


def load_json(path: Path, code: str) -> tuple[Any, list[dict]]:
    data, findings = read_bytes(path, code)
    if data is None:
        return None, findings
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [reason(f"{code}_NOT_JSON", f"{path}: not UTF-8: {exc}")]

    def reject_constant(value: str) -> None:
        raise ValueError(f"{value} is not valid JSON evidence")

    try:
        return json.loads(text, parse_constant=reject_constant), []
    except ValueError as exc:
        return None, [reason(f"{code}_NOT_JSON", f"{path}: {exc}")]


def fake_pass_findings(obj: Any, where: str) -> list[dict]:
    """Refuse any input that declares its own success verdict."""
    if not isinstance(obj, dict):
        return []
    findings: list[dict] = []
    for key in FAKE_PASS_KEYS:
        value = obj.get(key)
        if value is True:
            findings.append(reason(
                "FAKE_PASS_CLAIM",
                f"{where} claims {key}=true; only this tool emits a verdict"))
        elif isinstance(value, str) and value.strip().upper() in FAKE_PASS_WORDS:
            findings.append(reason(
                "FAKE_PASS_CLAIM",
                f"{where} claims {key}={value!r}; only this tool emits a verdict"))
    return findings


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# the committed proof is the authority; claims are re-derived from it
# ---------------------------------------------------------------------------
def parse_proof(obj: Any, proof_hash: str | None, boot_source: Any,
                proof_path: Path) -> tuple[dict, list[dict]]:
    authority: dict[str, Any] = {
        "available": False, "sha256": proof_hash, "hash_verified": False,
        "executables": {}, "companions": {}, "native_sha256": None,
        "exe_imports": None, "unresolved": None, "frame_count": None,
        "frames": {},
    }
    findings: list[dict] = []
    if proof_hash is not None:
        authority["available"] = True
    if not isinstance(obj, dict):
        return authority, [reason("PROOF_INVALID", "proof is not a JSON object")]
    if obj.get("schema") != SCHEMA_VERSION:
        findings.append(reason(
            "PROOF_INVALID", f"proof schema {obj.get('schema')!r} is not 1"))

    if not isinstance(boot_source, dict):
        findings.append(reason("SOURCE_INVALID", "boot record has no source object"))
    else:
        claim = boot_source.get("sha256")
        if not is_hex64(claim):
            findings.append(reason(
                "SOURCE_HASH_INVALID", "boot source.sha256 is not a 64-hex digest"))
        elif proof_hash is None:
            findings.append(reason(
                "PROOF_UNREADABLE", "cannot verify source hash without proof bytes"))
        elif claim.lower() != proof_hash:
            findings.append(reason(
                "PROOF_HASH_MISMATCH",
                f"boot source.sha256 {claim.lower()} != actual proof sha256 "
                f"{proof_hash} (tampered)"))
        else:
            authority["hash_verified"] = True
        path = boot_source.get("path")
        if not isinstance(path, str) or not path.strip():
            findings.append(reason("SOURCE_INVALID", "boot source.path is missing"))
        elif not suffix_match(path, proof_path):
            findings.append(reason(
                "SOURCE_PATH_MISMATCH",
                f"boot source.path {path!r} does not name the supplied proof"))

    originals = obj.get("originals")
    if not isinstance(originals, list):
        findings.append(reason("PROOF_INVALID", "proof.originals is not a list"))
    else:
        for index, item in enumerate(originals):
            if not isinstance(item, dict) or not is_hex64(item.get("sha256")):
                findings.append(reason(
                    "PROOF_INVALID", f"originals[{index}] needs a 64-hex sha256"))
                continue
            name = norm_name(item.get("name"))
            if name is None:
                findings.append(reason(
                    "PROOF_INVALID", f"originals[{index}] needs a name"))
                continue
            authority["executables"][name] = item["sha256"].lower()

    rebuild = obj.get("rebuild")
    native = rebuild.get("native_binary_sha256") if isinstance(rebuild, dict) else None
    if not is_hex64(native):
        findings.append(reason(
            "PROOF_INVALID", "proof.rebuild.native_binary_sha256 is not a 64-hex digest"))
    else:
        authority["native_sha256"] = native.lower()

    run = obj.get("run")
    if not isinstance(run, dict):
        findings.append(reason("PROOF_INVALID", "proof.run is not an object"))
        run = {}
    for field, key in (("imports_bound", "exe_imports"),
                       ("unresolved_imports", "unresolved"),
                       ("frame_count", "frame_count")):
        value = run.get(field)
        if not is_int(value) or value < 0:
            findings.append(reason(
                "PROOF_INVALID", f"proof.run.{field} is not a non-negative integer"))
        else:
            authority[key] = value
    for key, value in run.items():
        match = COMPANION_KEY_RE.fullmatch(key)
        if match is None:
            continue
        if not is_int(value) or value <= 0:
            findings.append(reason(
                "PROOF_INVALID", f"proof.run.{key} is not a positive integer"))
        else:
            authority["companions"][match.group(1)] = value

    raw_frames = obj.get("frames")
    if not isinstance(raw_frames, list):
        findings.append(reason("PROOF_INVALID", "proof.frames is not a list"))
    else:
        for index, item in enumerate(raw_frames):
            if not isinstance(item, dict) or not is_hex64(item.get("sha256")):
                findings.append(reason(
                    "PROOF_INVALID", f"frames[{index}] needs a 64-hex sha256"))
                continue
            name = Path(str(item.get("path", ""))).name
            if not name:
                findings.append(reason(
                    "PROOF_INVALID", f"frames[{index}] needs a path"))
                continue
            authority["frames"][name] = {
                "path": str(item["path"]), "sha256": item["sha256"].lower(),
                "blank": item.get("blank"),
            }
    return authority, findings


# ---------------------------------------------------------------------------
# boot claims (claims are checked, never trusted)
# ---------------------------------------------------------------------------
def parse_boot(obj: Any) -> tuple[dict, list[dict]]:
    boot: dict[str, Any] = {
        "available": False, "source": None, "native_sha256": None,
        "modules": [], "frames": [],
    }
    if not isinstance(obj, dict):
        return boot, []
    boot["available"] = True
    findings = fake_pass_findings(obj, "boot record")
    if obj.get("schema_version") != SCHEMA_VERSION:
        findings.append(reason(
            "BOOT_INVALID", f"boot schema_version {obj.get('schema_version')!r} is not 1"))
    boot["source"] = obj.get("source")
    native = obj.get("native_binary")
    if not isinstance(native, dict):
        findings.append(reason(
            "NATIVE_BINARY_HASH_MISSING", "boot record has no native_binary object"))
    else:
        findings.extend(fake_pass_findings(native, "boot native_binary"))
        claim = native.get("sha256")
        if not is_hex64(claim):
            findings.append(reason(
                "NATIVE_BINARY_HASH_INVALID",
                "boot native_binary.sha256 is not a 64-hex digest"))
        else:
            boot["native_sha256"] = claim.lower()
    modules = obj.get("modules")
    if not isinstance(modules, list):
        findings.append(reason("BOOT_INVALID", "boot modules is not a list"))
    else:
        boot["modules"] = modules
        for index, module in enumerate(modules):
            findings.extend(fake_pass_findings(module, f"modules[{index}]"))
    frames = obj.get("frames")
    if not isinstance(frames, list):
        findings.append(reason("FRAME_CLAIMS_INVALID", "boot frames is not a list"))
    else:
        boot["frames"] = frames
    return boot, findings


# ---------------------------------------------------------------------------
# frames: recompute hashes and measure pixels from bytes
# ---------------------------------------------------------------------------
def verify_frames(boot: dict, authority: dict,
                  frame_paths: list[Path]) -> tuple[dict, list[dict]]:
    facts: dict[str, Any] = {
        "claimed": len(boot["frames"]), "verified": [], "verified_nonblank": 0,
        "attested_total": len(authority["frames"]),
        "bytes_supplied": len(frame_paths),
    }
    findings: list[dict] = []
    supplied: dict[str, Path] = {}
    for path in frame_paths:
        name = path.name
        if name in supplied:
            findings.append(reason(
                "FRAME_NAME_AMBIGUOUS", f"two --frame inputs are named {name!r}"))
        else:
            supplied[name] = path

    claimed_names: set[str] = set()
    for index, claim in enumerate(boot["frames"]):
        if not isinstance(claim, dict):
            findings.append(reason(
                "FRAME_CLAIM_INVALID", f"frames[{index}] is not an object"))
            continue
        path, digest_claim = claim.get("path"), claim.get("sha256")
        if not isinstance(path, str) or not path.strip() or not is_hex64(digest_claim):
            findings.append(reason(
                "FRAME_CLAIM_INVALID",
                f"frames[{index}] needs a path and a 64-hex sha256 claim"))
            continue
        name = Path(path).name
        if name in claimed_names:
            findings.append(reason(
                "FRAME_NAME_AMBIGUOUS", f"two frame claims are named {name!r}"))
            continue
        claimed_names.add(name)
        expected = authority["frames"].get(name)
        if expected is None:
            findings.append(reason(
                "FRAME_NOT_ATTESTED", f"frame {name!r} is absent from the proof"))
            continue
        if expected["sha256"] != digest_claim.lower():
            findings.append(reason(
                "FRAME_HASH_MISMATCH",
                f"frame {name!r} claim {digest_claim.lower()} != proof "
                f"{expected['sha256']}"))
            continue
        actual_path = supplied.get(name)
        if actual_path is None:
            findings.append(reason(
                "FRAME_FILES_MISSING", f"no --frame bytes supplied for {name!r}"))
            continue
        if not suffix_match(path, actual_path):
            findings.append(reason(
                "FRAME_PATH_MISMATCH",
                f"--frame {actual_path} is not the claimed path {path!r}"))
            continue
        data, errs = read_bytes(actual_path, "FRAME")
        findings.extend(errs)
        if data is None:
            continue
        actual = sha256_bytes(data)
        if actual != digest_claim.lower():
            findings.append(reason(
                "FRAME_HASH_MISMATCH",
                f"{name}: actual sha256 {actual} != claim {digest_claim.lower()} "
                "(tampered)"))
            continue
        if _frame_info is None:  # pragma: no cover - dependency present here
            findings.append(reason(
                "FRAME_UNDECODABLE", "frame decoder tools.frame_evidence_check unavailable"))
            continue
        info = _frame_info(data)
        if "error" in info:
            findings.append(reason(
                "FRAME_UNDECODABLE", f"{name}: {info['error']}"))
            continue
        blank = info.get("blank") is True or info.get("distinct_colors", 0) <= 1
        facts["verified"].append({
            "path": path, "sha256": actual, "format": info.get("format"),
            "width": info.get("width"), "height": info.get("height"),
            "distinct_colors": info.get("distinct_colors"), "blank": blank,
        })
        if blank:
            findings.append(reason(
                "FRAME_BLANK",
                f"{name}: {info.get('distinct_colors')} distinct colour(s); "
                "a blank frame is not render evidence"))
        else:
            facts["verified_nonblank"] += 1

    missing = set(authority["frames"]) - claimed_names
    if missing:
        findings.append(reason(
            "FRAME_CLAIMS_INCOMPLETE",
            "proof frames not claimed by the boot record: " + ", ".join(sorted(missing))))
    if not boot["frames"]:
        findings.append(reason(
            "FRAME_PROOF_MISSING", "boot record claims no frames"))
    facts["verified"].sort(key=lambda item: item["path"])
    return facts, findings


# ---------------------------------------------------------------------------
# native binary hash (byte-level when --binary is supplied)
# ---------------------------------------------------------------------------
def verify_native_binary(boot: dict, authority: dict,
                         binary_path: Path | None) -> tuple[dict, list[dict]]:
    facts: dict[str, Any] = {
        "sha256": boot["native_sha256"], "attested": False, "bytes_supplied": False,
        "actual_sha256": None,
    }
    findings: list[dict] = []
    boot_hash, proof_hash = boot["native_sha256"], authority["native_sha256"]
    if boot_hash is not None and proof_hash is not None and boot_hash == proof_hash:
        facts["attested"] = True
    elif boot_hash is not None and proof_hash is not None:
        findings.append(reason(
            "NATIVE_BINARY_HASH_MISMATCH",
            f"boot native hash {boot_hash} != proof native hash {proof_hash}"))
    if binary_path is not None:
        data, errs = read_bytes(binary_path, "BINARY")
        findings.extend(errs)
        if data is not None:
            facts["bytes_supplied"] = True
            actual = sha256_bytes(data)
            facts["actual_sha256"] = actual
            if proof_hash is not None and actual != proof_hash:
                findings.append(reason(
                    "NATIVE_BINARY_HASH_MISMATCH",
                    f"native binary bytes hash {actual} != proof {proof_hash}"))
    return facts, findings


# ---------------------------------------------------------------------------
# per-module verdict facts
# ---------------------------------------------------------------------------
def verify_modules(boot: dict, authority: dict, native: dict, frames: dict,
                   ) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    rows: list[dict] = []
    seen: set[str] = set()

    for index, raw in enumerate(boot["modules"]):
        row: dict[str, Any] = {
            "name": None, "role": None, "index": index, "verdict": "BLOCKED",
            "reasons": [], "facts": {
                "hash_attested": False, "imports_bound": None,
                "unresolved_imports": None, "mapping_attested": False,
                "native_hash_attested": bool(native["attested"]),
                "nonblank_frames": frames["verified_nonblank"],
            },
        }
        if not isinstance(raw, dict):
            row["reasons"] = dedupe([reason("MODULE_INVALID", f"modules[{index}] is not an object")])
            rows.append(row)
            findings.extend(row["reasons"])
            continue
        name, role = raw.get("name"), raw.get("role")
        row["name"] = name if isinstance(name, str) else None
        row["role"] = role if isinstance(role, str) else None
        local: list[dict] = []
        if not isinstance(name, str) or not name.strip():
            local.append(reason("MODULE_INVALID", f"modules[{index}] needs a name"))
        token = norm_name(name)
        if token is None or token in seen:
            local.append(reason(
                "MODULE_DUPLICATE", f"module name {name!r} is missing or duplicated"))
        else:
            seen.add(token)
        if role not in ROLES:
            local.append(reason(
                "MODULE_ROLE_INVALID", f"module {name!r} has role {role!r}"))

        imports, unresolved = raw.get("imports_bound"), raw.get("unresolved_imports")
        facts = row["facts"]
        facts["imports_bound"] = imports if is_int(imports) else None
        facts["unresolved_imports"] = unresolved if is_int(unresolved) else None
        if not is_int(imports) or imports <= 0:
            local.append(reason(
                "IMPORT_COUNT_INVALID",
                f"module {name!r} imports_bound {imports!r} is not a positive integer"))
        if not is_int(unresolved) or unresolved < 0:
            local.append(reason(
                "UNRESOLVED_INVALID",
                f"module {name!r} unresolved_imports {unresolved!r} is not a non-negative integer"))
        elif unresolved > 0:
            local.append(reason(
                "UNRESOLVED_IMPORTS_PRESENT",
                f"module {name!r} reports {unresolved} unresolved import(s)"))

        proof_unresolved = authority["unresolved"]
        if (is_int(unresolved) and proof_unresolved is not None
                and unresolved != proof_unresolved):
            local.append(reason(
                "UNRESOLVED_COUNT_MISMATCH",
                f"module {name!r} unresolved {unresolved} != proof {proof_unresolved}"))

        if role == "exe":
            expected_hash = authority["executables"].get(token)
            if expected_hash is None:
                local.append(reason(
                    "MODULE_NOT_ATTESTED",
                    f"EXE {name!r} is not in proof.originals (fake module)"))
            else:
                claim = raw.get("sha256")
                if not is_hex64(claim):
                    local.append(reason(
                        "MODULE_HASH_INVALID",
                        f"EXE {name!r} sha256 is not a 64-hex digest"))
                elif claim.lower() != expected_hash:
                    local.append(reason(
                        "MODULE_HASH_MISMATCH",
                        f"EXE {name!r} hash {claim.lower()} != proof {expected_hash}"))
                else:
                    facts["hash_attested"] = True
            if is_int(imports) and authority["exe_imports"] is not None \
                    and imports != authority["exe_imports"]:
                local.append(reason(
                    "IMPORT_COUNT_MISMATCH",
                    f"EXE {name!r} imports {imports} != proof {authority['exe_imports']}"))
        elif role == "companion":
            owner = raw.get("companion_of")
            if not isinstance(owner, str) or not owner.strip():
                local.append(reason(
                    "COMPANION_OF_MISSING",
                    f"companion {name!r} has no companion_of EXE"))
            mapping = raw.get("mapping")
            if mapping is None:
                local.append(reason(
                    "COMPANION_MAPPING_MISSING",
                    f"companion {name!r} has no mapping; mapping is required evidence"))
            elif not isinstance(mapping, dict):
                local.append(reason(
                    "MAPPING_INVALID", f"companion {name!r} mapping is not an object"))
            else:
                local.extend(fake_pass_findings(mapping, f"companion {name!r} mapping"))
                key = mapping.get("source_key")
                match = COMPANION_KEY_RE.fullmatch(key) if isinstance(key, str) else None
                if match is None:
                    local.append(reason(
                        "MAPPING_INVALID",
                        f"companion {name!r} source_key {key!r} is not a "
                        "companion_<name>_imports_bound key"))
                else:
                    key_token = match.group(1)
                    expected = authority["companions"].get(key_token)
                    if expected is None:
                        local.append(reason(
                            "MAPPING_NOT_ATTESTED",
                            f"companion {name!r} mapping key {key!r} is absent "
                            "from the proof"))
                    else:
                        mapped = mapping.get("imports_bound")
                        if not is_int(mapped) or mapped != expected:
                            local.append(reason(
                                "MAPPING_COUNT_MISMATCH",
                                f"companion {name!r} mapping imports {mapped!r} != "
                                f"proof {key}={expected}"))
                        elif mapped <= 0:
                            local.append(reason(
                                "MAPPING_ZERO_IMPORTS",
                                f"companion {name!r} mapping has no bound imports"))
                        elif is_int(imports) and imports != expected:
                            local.append(reason(
                                "IMPORT_COUNT_MISMATCH",
                                f"companion {name!r} imports {imports} != proof {expected}"))
                        else:
                            facts["mapping_attested"] = True
            # The token embedded in the proof key must identify this companion
            # (e.g. sdl_net for SDL_net.dll), so a fake name cannot borrow it.
            if facts["mapping_attested"]:
                key_token = COMPANION_KEY_RE.fullmatch(
                    raw["mapping"]["source_key"]).group(1)
                if token != key_token:
                    local.append(reason(
                        "MAPPING_NOT_ATTESTED",
                        f"companion {name!r} does not match proof key token {key_token!r}"))
                    facts["mapping_attested"] = False

        row["reasons"] = dedupe(local)
        rows.append(row)
        findings.extend(row["reasons"])

    # owner cross-check requires the EXE rows to be known first
    for row, raw in zip(rows, boot["modules"]):
        if not isinstance(raw, dict) or row["role"] != "companion":
            continue
        owner = raw.get("companion_of")
        if isinstance(owner, str) and token_matches_exe(owner, rows):
            continue
        extra = reason(
            "COMPANION_OF_INVALID",
            f"companion {row['name']!r} companion_of {owner!r} is not a boot EXE")
        row["reasons"] = dedupe(row["reasons"] + [extra])
        findings.append(extra)

    if not rows:
        findings.append(reason("NO_MODULES", "boot record has no modules"))
    if not any(row["role"] == "exe" for row in rows):
        findings.append(reason("NO_EXE", "boot record has no EXE module"))
    return rows, findings


def token_matches_exe(owner: str, rows: list[dict]) -> bool:
    token = norm_name(owner)
    if token is None:
        return False
    return any(row["role"] == "exe" and norm_name(row["name"]) == token
               for row in rows)


# ---------------------------------------------------------------------------
# verdict join
# ---------------------------------------------------------------------------
def decide_module(row: dict, global_tamper: bool, global_negative: bool,
                  global_incomplete: bool) -> tuple[str, list[dict]]:
    findings = row["reasons"]
    codes = {item["code"] for item in findings}
    if (global_tamper or global_negative
            or codes & TAMPER_CODES or codes & NEGATIVE_CODES):
        return "BLOCKED", dedupe(findings)
    if global_incomplete or codes & INCOMPLETE_CODES:
        if not findings:
            findings = findings + [reason(
                "GLOBAL_EVIDENCE_INCOMPLETE",
                "a required global evidence fact (proof, native hash or frame) "
                "is missing or unverified")]
        return "PARTIAL", dedupe(findings)
    facts = row["facts"]
    complete = (
        facts["native_hash_attested"]
        and facts["nonblank_frames"] >= 1
        and is_int(facts["imports_bound"]) and facts["imports_bound"] > 0
        and is_int(facts["unresolved_imports"]) and facts["unresolved_imports"] == 0
        and (facts["hash_attested"] if row["role"] == "exe"
             else facts["mapping_attested"])
    )
    if complete:
        return "VERIFIED", []
    return "PARTIAL", dedupe(findings + [reason(
        "MODULE_EVIDENCE_INCOMPLETE",
        f"module {row['name']!r} is missing a required evidence fact")])


def build_verdict(boot: dict, authority: dict, native: dict, frames: dict,
                  findings: list[dict], rows: list[dict], label: str) -> dict:
    findings = dedupe(findings)
    codes = {item["code"] for item in findings}
    global_tamper = bool(codes & TAMPER_CODES)
    if global_tamper:
        findings.append(reason(
            "EVIDENCE_TAMPERED",
            "an input contradicts itself, the committed proof or the supplied "
            "bytes; no module can be VERIFIED from tampered evidence"))
        codes.add("EVIDENCE_TAMPERED")
    global_negative = bool(codes & NEGATIVE_CODES)
    global_incomplete = bool(codes & INCOMPLETE_CODES)
    if not boot["available"] and not authority["available"] and not rows:
        global_negative = True

    modules: list[dict] = []
    for row in rows:
        verdict, reasons = decide_module(
            row, global_tamper, global_negative, global_incomplete)
        modules.append({
            "name": row["name"], "role": row["role"], "index": row["index"],
            "verdict": verdict, "reasons": reasons, "facts": row["facts"],
        })

    if global_tamper or global_negative or any(
            item["verdict"] == "BLOCKED" for item in modules):
        overall = "BLOCKED"
    elif any(item["verdict"] == "PARTIAL" for item in modules) or global_incomplete \
            or not modules:
        overall = "PARTIAL"
    else:
        overall = "VERIFIED"
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "issue": ISSUE,
        "label": label,
        "verdict": overall,
        "summary": {
            "modules": len(modules),
            "verified": sum(1 for item in modules if item["verdict"] == "VERIFIED"),
            "partial": sum(1 for item in modules if item["verdict"] == "PARTIAL"),
            "blocked": sum(1 for item in modules if item["verdict"] == "BLOCKED"),
            "nonblank_frames": frames["verified_nonblank"],
        },
        "reasons": findings,
        "modules": modules,
        "evidence": {
            "source": {
                "path": (boot["source"] or {}).get("path")
                if isinstance(boot["source"], dict) else None,
                "sha256": authority["sha256"],
                "hash_verified": authority["hash_verified"],
            },
            "native_binary": native,
            "frames": {
                "claimed": frames["claimed"],
                "attested": frames["attested_total"],
                "bytes_supplied": frames["bytes_supplied"],
                "nonblank": frames["verified_nonblank"],
                "verified": frames["verified"],
            },
        },
        "limits": list(LIMITS),
    }


def verify(boot_obj: Any, proof_obj: Any, proof_bytes: bytes | None,
           proof_path: Path, frame_paths: list[Path],
           binary_path: Path | None, label: str) -> dict:
    findings: list[dict] = []
    boot, errs = parse_boot(boot_obj)
    findings.extend(errs)
    source = boot["source"] if isinstance(boot["source"], dict) else None
    authority, errs = parse_proof(
        proof_obj, sha256_bytes(proof_bytes) if proof_bytes is not None else None,
        source, proof_path)
    findings.extend(errs)
    native, errs = verify_native_binary(boot, authority, binary_path)
    findings.extend(errs)
    frames, errs = verify_frames(boot, authority, frame_paths)
    findings.extend(errs)
    rows, errs = verify_modules(boot, authority, native, frames)
    findings.extend(errs)
    if not boot["available"] and not authority["available"]:
        findings.append(reason(
            "NO_EVIDENCE", "no boot record or committed proof was supplied"))
    return build_verdict(boot, authority, native, frames, findings, rows, label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boot", type=Path, help="multi-module boot record JSON")
    parser.add_argument("--proof", type=Path, help="committed run proof JSON")
    parser.add_argument("--frame", action="append", type=Path, default=[],
                        help="actual captured frame bytes (repeatable, matched by filename)")
    parser.add_argument("--binary", type=Path,
                        help="optional native binary bytes for hash re-computation")
    parser.add_argument("--out", type=Path, help="write the verdict JSON here")
    parser.add_argument("--label", default="issue #9 multi-module boot evidence")
    args = parser.parse_args(argv)

    findings_pre: list[dict] = []
    boot_obj = None
    if args.boot is not None:
        boot_obj, errs = load_json(args.boot, "BOOT")
        findings_pre.extend(errs)
    else:
        findings_pre.append(reason("BOOT_INVALID", "no --boot record was supplied"))
    proof_obj, proof_bytes = None, None
    proof_path = args.proof if args.proof is not None else Path("<none>")
    if args.proof is not None:
        proof_obj, errs = load_json(args.proof, "PROOF")
        findings_pre.extend(errs)
        if proof_obj is not None:
            proof_bytes, errs = read_bytes(args.proof, "PROOF")
            findings_pre.extend(errs)
    else:
        findings_pre.append(reason("PROOF_UNREADABLE", "no --proof was supplied"))

    doc = verify(boot_obj, proof_obj, proof_bytes, proof_path,
                 args.frame, args.binary, args.label)
    doc["reasons"] = dedupe(doc["reasons"] + findings_pre)
    codes = {item["code"] for item in doc["reasons"]}
    if codes & (TAMPER_CODES | NEGATIVE_CODES):
        for item in doc["modules"]:
            item["verdict"] = "BLOCKED"
        doc["verdict"] = "BLOCKED"
        if "EVIDENCE_TAMPERED" not in codes and codes & TAMPER_CODES:
            doc["reasons"] = dedupe(doc["reasons"] + [reason(
                "EVIDENCE_TAMPERED",
                "an input is unreadable, malformed or self-declared; no module "
                "can be VERIFIED")])

    text = canonical_json(doc)
    if args.out is not None:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"{TOOL}: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    print(text, end="")
    return 0 if doc["verdict"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
