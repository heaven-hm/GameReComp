#!/usr/bin/env python3
"""Generate a fail-closed, machine-readable per-OS capability matrix from CLI evidence.

The matrix has one row per host OS (Windows, macOS, Linux) and one column per
guest/format family (x86, x64, .NET, Win16). A cell is a measured verdict --
PASS / PARTIAL / BUILD ONLY / FAIL / BLOCKED / NOT TESTED -- with a reason and
an evidence path. This tool never runs a game, compiles a binary, edits a
document or invents a result: it joins evidence that other tools already
recorded, and every input path comes from the command line. No repository,
game, corpus, Wine prefix or evidence location is built into this module.

Inputs (all optional individually, at least one required):

* ``--acceptance-matrix`` : JSON written by :mod:`tools.acceptance_matrix`
  (``rows[].{status,checks,hashes,oracle,scenario}``). Corpus-acceptance JSON
  (``games[]``) and plain records with the same fields are also understood.
  A row's ``status`` is *advisory only*: a written ``PASS`` never creates a
  matrix PASS.
* ``--refusal-test`` : structured refusal JSON (``verdict``, ``refused``,
  ``counts``/``causes``, ``error``) as produced by
  :mod:`tools.autorecomp.refusals`. Refusals can only cap a cell; they never
  grant PASS.
* ``--pipeline-report`` : an autorecomp ``reports/report.json`` or
  ``reports/verification.json`` (``stages``, ``outcome``, ``manifest``,
  ``target``, ``oracle_gaps``, ``run``). A ``scenario-pass`` outcome is stage
  evidence only.

A cell becomes PASS only when all of these hold at once for one evidence
record:

1. ``oracle_verified`` / ``oracle.verified`` is the boolean ``true`` and the
   oracle names a real executed process (dummy, headless, unexecuted and
   unknown spellings are rejected);
2. original/input and native SHA-256 values are real 64-hex digests and agree
   with every other source for that same cell (a conflict is FAIL);
3. a named scenario plus non-empty scenario evidence refer to an existing
   file whose SHA-256 is recomputed here and matches the claimed digest.

Additionally, no refusal, failed pipeline stage, unresolved oracle gap or
conflicting FAIL/BLOCKED record may be present in that cell. Everything else
is capped at PARTIAL / BUILD ONLY / FAIL / BLOCKED / NOT TESTED with an
explicit reason. Unknown OS/architecture values are reported under
``unmapped`` with a reason rather than being silently assigned to a cell.
An optional ``--target-map`` JSON file maps names, hashes or paths to
``{"os": ..., "arch": ...}``; it only assigns cells and can never grant PASS.

Usage::

    python3 -m tools.os_capability_matrix \
        --acceptance-matrix <acceptance.json> \
        --refusal-test <refusals.json> \
        --pipeline-report <report.json> \
        --target-map <targets.json> \
        --out <matrix.json> [--markdown <matrix.md>] \
        [--default-os Windows --default-arch x86] \
        [--generated-at 2026-01-01T00:00:00Z]

``generated_at`` defaults to the current UTC time; pass it explicitly for
byte-for-byte deterministic output in tests and CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/os_capability_matrix.py"
OSES = ("Windows", "macOS", "Linux")
ARCHES = ("x86", "x64", ".NET", "Win16")
STATUSES = ("PASS", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED", "NOT TESTED")

# Only an explicit, executed original-Windows process can satisfy the oracle
# gate. An empty/unknown allow-list entry is safer than accepting a vague
# "verified" string. Kept in sync with tools/acceptance_matrix.py on purpose.
REAL_ORACLES = frozenset({
    "original-windows", "original-windows-process", "windows-oracle",
    "windows-original", "windows-process", "original-process",
    "original-vs-native", "original-game", "retail-windows",
    "retail-original",
})
NO_ORACLES = frozenset({
    "", "none", "null", "n/a", "na", "unknown", "unspecified", "not-supplied",
    "dummy", "dummy-device", "dummy-device-audio", "headless", "sdl-dummy",
    "stub", "fake", "fake-success", "emulator", "unit-test", "selftest",
    "windows-oracle-unexecuted", "no-oracle",
})

OS_ALIASES = {
    "windows": "Windows", "win": "Windows", "win32": "Windows",
    "win64": "Windows", "winnt": "Windows", "windows-nt": "Windows",
    "microsoft-windows": "Windows", "mswin": "Windows",
    "macos": "macOS", "mac": "macOS", "osx": "macOS", "darwin": "macOS",
    "mac-os": "macOS", "macosx": "macOS", "apple": "macOS",
    "linux": "Linux", "gnu-linux": "Linux", "linux-gnu": "Linux",
}
ARCH_ALIASES = {
    "x86": "x86", "i386": "x86", "i486": "x86", "i586": "x86",
    "i686": "x86", "ia32": "x86", "x86-32": "x86", "x86_32": "x86",
    "32-bit": "x86", "32bit": "x86", "pe32": "x86",
    "x64": "x64", "amd64": "x64", "x86_64": "x64", "x86-64": "x64",
    "intel64": "x64", "pe32+": "x64", "win64": "x64",
    "64-bit": "x64", "64bit": "x64",
    ".net": ".NET", "dotnet": ".NET", "cli": ".NET", "il": ".NET",
    "msil": ".NET", "net": ".NET",
    "win16": "Win16", "16-bit": "Win16", "16bit": "Win16",
    "ne": "Win16", "win16-ne": "Win16",
}
OS_KEYS = ("os", "operating_system", "target_os", "platform", "platform_os",
           "host_os", "system")
ARCH_KEYS = ("arch", "architecture", "target_arch", "cpu", "machine",
             "pe_type", "bitness", "pointer_size")
NESTED_META = ("target", "platform", "system", "original", "native", "input",
               "binary", "build", "info", "metadata")
SHA_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
BENIGN_STAGES = frozenset({"ok", "passed", "skipped", "not-run", "not-applicable"})
FAILED_STAGES = frozenset({"error", "failed", "fail", "exception", "crashed"})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value.strip()))


def norm_sha(value: Any) -> str | None:
    """Normalise a real SHA-256 to lower case; anything else becomes None."""
    return value.strip().lower() if is_sha256(value) else None


def key_of(value: Any) -> str:
    return " ".join(str(value or "").replace("†", "").split()).lower()


def norm_token(value: Any) -> str:
    token = key_of(value).replace("_", "-").replace(" ", "-")
    return re.sub(r"-+", "-", token).strip("-")


def norm_os(value: Any) -> str | None:
    return OS_ALIASES.get(norm_token(value))


def norm_arch(value: Any) -> str | None:
    return ARCH_ALIASES.get(norm_token(value))


def norm_oracle(value: Any) -> str:
    return norm_token(value)


def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file, or None when unreadable (never guessed)."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def resolve_path(raw: Any, base_dir: Path) -> Path | None:
    """Resolve an evidence path; relative paths are relative to the JSON that
    declared them. Absolute paths supplied by evidence are used verbatim."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else base_dir / path


def find_in(obj: Any, key_names: tuple[str, ...], depth: int = 0) -> Any:
    """Find the first non-empty string for key_names in a nested metadata dict."""
    if not isinstance(obj, dict) or depth > 3:
        return None
    for key in key_names:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in NESTED_META:
        value = obj.get(key)
        if isinstance(value, dict):
            found = find_in(value, key_names, depth + 1)
            if found is not None:
                return found
    return None


def find_meta(record: Any, context: Any, key_names: tuple[str, ...]) -> Any:
    return find_in(record, key_names) or find_in(context, key_names)


def records_from(data: Any) -> list[Any]:
    """Pick the record list of a JSON document, never its bookkeeping lists."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("rows", "games", "records", "results", "cases", "tests",
                "reports", "scenarios"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return [data]


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def load_sources(paths: list[str], kind: str) -> tuple[list[dict], list[dict]]:
    """Read CLI-supplied JSON, recording unreadable inputs fail-closed."""
    loaded: list[dict] = []
    problems: list[dict] = []
    for raw in paths:
        path = Path(raw)
        source = {"kind": kind, "path": str(path), "sha256": sha256_file(path),
                  "status": "ok"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            source.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            problems.append(source)
            continue
        loaded.append({"data": data, "source": source})
    return loaded, problems


def load_target_map(paths: list[str]) -> tuple[dict[str, tuple[str, str]], list[dict]]:
    """Load optional name/hash/path -> (os, arch) assignments from CLI files."""
    mapping: dict[str, tuple[str, str]] = {}
    problems: list[dict] = []
    for raw in paths:
        loaded, failures = load_sources([raw], "target-map")
        problems.extend(failures)
        for item in loaded:
            data, source = item["data"], item["source"]
            if not isinstance(data, dict):
                source.update(status="INVALID", error="expected a JSON object")
                problems.append(source)
                continue
            for key, value in data.items():
                if not isinstance(value, dict):
                    continue
                os_name = norm_os(value.get("os"))
                arch_name = norm_arch(value.get("arch"))
                if os_name and arch_name:
                    mapping[key_of(key)] = (os_name, arch_name)
    return mapping, problems


def lookup_target_map(record: dict, source: dict,
                      target_map: dict[str, tuple[str, str]]) -> tuple[str | None, str | None]:
    """Assign a cell only when an explicit CLI map names this record."""
    candidates = set()
    for value in (record.get("id"), record.get("name"), record.get("game"),
                  record.get("project"), record.get("input"), record.get("path"),
                  source.get("path")):
        if isinstance(value, str) and value.strip():
            candidates.add(key_of(value))
            candidates.add(key_of(Path(value).name))
    for value in record.get("hash_values", []):
        if isinstance(value, str):
            candidates.add(value.strip().lower())
    for key, target in target_map.items():
        if key in candidates:
            return target
    return None, None


def resolve_target(record: dict, context: Any, source: dict,
                   target_map: dict[str, tuple[str, str]],
                   default_os: str | None, default_arch: str | None) -> tuple[str | None, str | None, str | None]:
    """Resolve (OS, arch) from evidence, then CLI map, then CLI defaults."""
    os_name = norm_os(find_meta(record, context, OS_KEYS))
    arch_name = norm_arch(find_meta(record, context, ARCH_KEYS))
    if not (os_name and arch_name):
        mapped_os, mapped_arch = lookup_target_map(record, source, target_map)
        os_name = os_name or mapped_os
        arch_name = arch_name or mapped_arch
    os_name = os_name or default_os
    arch_name = arch_name or default_arch
    if os_name and arch_name:
        return os_name, arch_name, None
    missing = []
    if not os_name:
        missing.append("OS")
    if not arch_name:
        missing.append("architecture")
    return None, None, "no " + " or ".join(missing) + " metadata in evidence"


# ---------------------------------------------------------------------------
# Record normalisation
# ---------------------------------------------------------------------------

def normalize_oracle(obj: dict, source: dict) -> dict:
    """Validate a written PASS claim structurally, including its evidence file."""
    raw = obj.get("oracle") if isinstance(obj.get("oracle"), dict) else {}
    checks_raw = obj.get("checks") if isinstance(obj.get("checks"), dict) else {}
    hashes = obj.get("hashes") if isinstance(obj.get("hashes"), dict) else {}
    raw_scenario = obj.get("scenario")
    scenario = raw_scenario if isinstance(raw_scenario, dict) else {}

    name = norm_oracle(raw.get("oracle") or raw.get("name") or raw.get("kind")
                       or obj.get("oracle"))
    flag = raw.get("verified", raw.get("oracle_verified", obj.get("oracle_verified")))
    if flag is None and "has_real_oracle_flag" in checks_raw:
        flag = checks_raw["has_real_oracle_flag"]
    declared_valid = raw.get("valid")

    evidence_raw = (raw.get("evidence_path") or obj.get("evidence_path") or "")
    base_dir = Path(source["path"]).parent
    evidence_path = resolve_path(evidence_raw, base_dir)
    actual_sha = sha256_file(evidence_path) if evidence_path else None

    scenario_name = (scenario.get("name") or raw.get("scenario")
                     or (raw_scenario if isinstance(raw_scenario, str) else None)
                     or obj.get("scenario_name"))
    if isinstance(scenario_name, dict):
        scenario_name = scenario_name.get("name")
    scenario_evidence = (scenario.get("evidence") or scenario.get("scenario_evidence")
                         or raw.get("scenario_evidence") or obj.get("scenario_evidence"))

    claimed_sha = norm_sha(raw.get("evidence_sha256") or obj.get("evidence_sha256")
                           or hashes.get("evidence_sha256"))
    checks = {
        "has_real_oracle_flag": flag is True and declared_valid is not False,
        "has_real_oracle_name": name in REAL_ORACLES and name not in NO_ORACLES,
        "has_scenario": bool(str(scenario_name or "").strip()),
        "has_scenario_evidence": bool(str(scenario_evidence or "").strip()),
        "has_evidence_file": bool(evidence_raw) and actual_sha is not None,
        "evidence_hash_matches": bool(claimed_sha and actual_sha and claimed_sha == actual_sha),
    }
    reasons = []
    if checks["has_real_oracle_flag"] is False:
        reasons.append("no boolean executed-oracle flag (oracle_verified is not true)")
    if checks["has_real_oracle_name"] is False:
        reasons.append(f"oracle {name!r} is not an accepted executed process")
    if checks["has_scenario"] is False:
        reasons.append("no named scenario")
    if checks["has_scenario_evidence"] is False:
        reasons.append("no scenario evidence text")
    if checks["has_evidence_file"] is False:
        reasons.append(f"scenario evidence file is missing: {str(evidence_raw or '(none)')}")
    if checks["evidence_hash_matches"] is False:
        reasons.append("scenario evidence SHA-256 is absent or does not match the file")
    return {
        "name": name,
        "flag": flag,
        "valid": declared_valid,
        "input_sha256": norm_sha(raw.get("input_sha256") or hashes.get("input_sha256")
                                 or obj.get("input_sha256") or obj.get("sha256")),
        "native_sha256": norm_sha(raw.get("native_sha256") or hashes.get("native_sha256")
                                  or obj.get("native_sha256")),
        "scenario_name": str(scenario_name or "").strip(),
        "scenario_evidence": str(scenario_evidence or "").strip(),
        "evidence_path": str(evidence_path) if evidence_raw else "",
        "claimed_evidence_sha256": claimed_sha,
        "actual_evidence_sha256": actual_sha,
        "checks": checks,
        "reasons": reasons,
        "valid_structurally": all(checks.values()) and declared_valid is not False,
    }


def status_claim_of(obj: dict) -> str | None:
    """Read an advisory status, mapping pipeline spellings to matrix vocabulary."""
    for key in ("status", "working_status", "verification_status", "verdict"):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        token = value.strip().upper()
        if token in ("SCENARIO-PASS", "SCENARIO_PASS", "VERIFIED-PLAYABLE-SCOPE",
                     "ACCEPTED"):
            return "PASS"
        if token in ("NOT-RUN", "NOT RUN", "NO-ORACLE", "UNTESTABLE"):
            return "NOT TESTED"
        if token in ("REFUSED",):
            return "BLOCKED"
        if token in STATUSES:
            return token
        if token.startswith("BUILD ONLY") or token.startswith("BUILD-ONLY"):
            return "BUILD ONLY"
        if token.startswith("NOT TESTED"):
            return "NOT TESTED"
        if token.startswith("PARTIAL"):
            return "PARTIAL"
        if token.startswith("PASS"):
            return "PASS"
        if token.startswith("FAIL"):
            return "FAIL"
        if token.startswith("BLOCKED"):
            return "BLOCKED"
    return None


def normalize_acceptance(obj: dict, source: dict) -> dict:
    hashes = obj.get("hashes") if isinstance(obj.get("hashes"), dict) else {}
    original = obj.get("original") if isinstance(obj.get("original"), dict) else {}
    native = obj.get("native") if isinstance(obj.get("native"), dict) else {}
    pipeline = obj.get("pipeline") if isinstance(obj.get("pipeline"), dict) else {}
    checks = obj.get("checks") if isinstance(obj.get("checks"), dict) else {}
    input_sha = norm_sha(hashes.get("input_sha256") or original.get("sha256")
                         or obj.get("input_sha256") or obj.get("sha256"))
    native_sha = norm_sha(hashes.get("native_sha256") or native.get("sha256")
                          or pipeline.get("native_sha256") or obj.get("native_sha256"))
    agree = hashes.get("agree", checks.get("hashes_agree_across_sources"))
    launch = obj.get("launch") if isinstance(obj.get("launch"), dict) else {}
    launched = bool(checks.get("launch_evidenced") or launch.get("executed"))
    return {
        "status_claim": status_claim_of(obj),
        "input_sha256": input_sha,
        "native_sha256": native_sha,
        "hash_values": [v for v in (input_sha, native_sha) if v],
        "hashes_agree": agree if isinstance(agree, bool) else None,
        "build_evidence": bool(native_sha or pipeline.get("native_sha256")),
        "launched": bool(launched),
        "oracle": normalize_oracle(obj, source),
        "refusal": None,
        "pipeline": None,
    }


def normalize_refusal(obj: dict, source: dict) -> dict:
    raw = obj.get("refusal") if isinstance(obj.get("refusal"), dict) else obj
    refused = raw.get("refused")
    if not isinstance(refused, bool):
        refused = str(raw.get("verdict", "")).upper() == "REFUSED"
    counts = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
    causes = raw.get("causes") if isinstance(raw.get("causes"), dict) else {}
    named = []
    for cause, entry in causes.items():
        count = entry.get("count", 0) if isinstance(entry, dict) else entry
        if isinstance(count, int) and count > 0:
            named.append(str(cause).upper())
    for cause, count in counts.items():
        if isinstance(count, int) and count > 0 and str(cause).upper() not in named:
            named.append(str(cause).upper())
    single = raw.get("cause")
    if isinstance(single, str) and single.strip():
        named.append(single.strip().upper())
    error = raw.get("error")
    native = raw.get("native_binary") if isinstance(raw.get("native_binary"), dict) else {}
    claim = status_claim_of(raw)
    if claim == "PASS" and str(raw.get("verdict", "")).upper() == "ACCEPTED":
        claim = None  # "no refusal found" is not positive capability evidence
    return {
        "status_claim": claim,
        "input_sha256": norm_sha(raw.get("input_sha256")),
        "native_sha256": norm_sha(raw.get("native_sha256")),
        "hash_values": [],
        "hashes_agree": None,
        "build_evidence": bool(native.get("existing")),
        "launched": False,
        "oracle": None,
        "refusal": {
            "refused": bool(refused),
            "causes": named,
            "error": str(error) if error else None,
            "reason": str(raw.get("reason") or ""),
        },
        "pipeline": None,
    }


def normalize_pipeline(obj: dict, source: dict) -> dict:
    outcome = obj.get("outcome") if isinstance(obj.get("outcome"), dict) else {}
    if not outcome and isinstance(obj.get("verification"), dict):
        outcome = obj["verification"]
    if not outcome:
        outcome = obj
    manifest = obj.get("manifest") if isinstance(obj.get("manifest"), dict) else {}
    binary = manifest.get("binary") if isinstance(manifest.get("binary"), dict) else {}
    run = outcome.get("run") if isinstance(outcome.get("run"), dict) else (obj.get("run") or {})
    stages = obj.get("stages") if isinstance(obj.get("stages"), list) else []
    gaps = outcome.get("oracle_gaps") if isinstance(outcome.get("oracle_gaps"), list) else []
    failed = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        state = str(stage.get("status", "")).lower()
        if state in FAILED_STAGES or (state and state not in BENIGN_STAGES):
            failed.append(str(stage.get("name") or "?"))
    input_sha = norm_sha(manifest.get("input_sha256") or outcome.get("executable_sha256"))
    native_sha = norm_sha(binary.get("sha256") or outcome.get("native_sha256"))
    run_state = str(run.get("status", "")).lower()
    scenario = outcome.get("scenario") if isinstance(outcome.get("scenario"), dict) else {}
    return {
        "status_claim": status_claim_of(outcome),
        "input_sha256": input_sha,
        "native_sha256": native_sha,
        "hash_values": [v for v in (input_sha, native_sha) if v],
        "hashes_agree": None,
        "build_evidence": bool(native_sha or binary.get("path")
                               or any(s.get("name") == "compile" and s.get("status") == "ok"
                                      for s in stages if isinstance(s, dict))),
        "launched": run_state in ("exited", "timeout", "running", "ok"),
        "oracle": normalize_oracle(scenario, source) if scenario else None,
        "refusal": None,
        "pipeline": {
            "status": str(outcome.get("status") or ""),
            "reason": str(outcome.get("reason") or ""),
            "failed_stages": failed,
            "oracle_gaps": [str(gap) for gap in gaps],
        },
    }


NORMALIZERS = {
    "acceptance-matrix": normalize_acceptance,
    "refusal-test": normalize_refusal,
    "pipeline-report": normalize_pipeline,
}


def normalize_records(loaded: list[dict], target_map: dict[str, tuple[str, str]],
                      default_os: str | None, default_arch: str | None
                      ) -> tuple[list[dict], list[dict]]:
    """Flatten all CLI evidence into records keyed by (OS, architecture)."""
    records: list[dict] = []
    unmapped: list[dict] = []
    for wrapped in loaded:
        data, source = wrapped["data"], wrapped["source"]
        kind = source["kind"]
        normalizer = NORMALIZERS[kind]
        for index, obj in enumerate(records_from(data)):
            if not isinstance(obj, dict):
                unmapped.append({"path": source["path"], "kind": kind,
                                 "index": index, "reason": "record is not a JSON object"})
                continue
            os_name, arch_name, reason = resolve_target(
                obj, data, source, target_map, default_os, default_arch)
            record = normalizer(obj, source)
            record.update({
                "id": str(obj.get("game") or obj.get("project") or obj.get("name")
                          or obj.get("id") or obj.get("input") or source["path"]),
                "kind": kind,
                "source": source,
                "os": os_name,
                "arch": arch_name,
            })
            if reason:
                unmapped.append({"path": source["path"], "kind": kind,
                                 "index": index, "name": record["id"],
                                 "reason": reason})
            else:
                records.append(record)
    return records, unmapped


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

SEVERITY = {"NOT TESTED": 0, "BUILD ONLY": 1, "PARTIAL": 2,
            "BLOCKED": 3, "FAIL": 4}


def refusal_status(refusal: dict) -> str | None:
    """A typed refusal is a cap, never a source of PASS."""
    text = " ".join([refusal.get("reason") or "", *refusal.get("causes", [])]).lower()
    if refusal.get("error"):
        return "BLOCKED"
    if any(cause in ("LIFT_ERROR", "UNRESOLVED_IMPORT") for cause in refusal["causes"]):
        return "FAIL"
    if any(word in text for word in ("lift", "unresolved import", "crash", "fault")):
        return "FAIL"
    if "MISSING_NATIVE_BINARY" in refusal["causes"]:
        return "BUILD ONLY"
    if refusal["refused"] and any(word in text for word in (
            "blocked", "unsupported", "deferred", "no runner", "runner",
            "feasibility", "not integrated")):
        return "BLOCKED"
    if refusal["refused"]:
        return "BLOCKED"
    return None


def record_status(record: dict) -> str | None:
    """Derive a conservative status from one advisory record."""
    refusal = record.get("refusal")
    if refusal:
        verdict = refusal_status(refusal)
        if verdict:
            return verdict
    pipeline = record.get("pipeline")
    if pipeline and pipeline["failed_stages"]:
        return "FAIL"
    claim = record.get("status_claim")
    if claim == "PASS":
        # A written PASS is stage evidence only, and a bare claim with no
        # hashes, build or launch behind it establishes nothing at all.
        if (record.get("launched") or record.get("build_evidence")
                or record.get("input_sha256") or record.get("native_sha256")):
            return "PARTIAL"
        return None
    if claim in ("FAIL", "BLOCKED", "PARTIAL", "BUILD ONLY", "NOT TESTED"):
        return claim
    if record.get("launched"):
        return "PARTIAL"
    if record.get("build_evidence"):
        return "BUILD ONLY"
    return None


def classify_cell(os_name: str, arch_name: str, records: list[dict]) -> dict:
    """Join the records of one cell into a verdict with evidence and checks."""
    reasons: list[str] = []
    checks = {
        "has_evidence": bool(records),
        "has_real_oracle_flag": False,
        "has_real_oracle_name": False,
        "has_input_sha256": False,
        "has_native_sha256": False,
        "hashes_agree_across_sources": True,
        "oracle_hashes_match_cell": False,
        "has_scenario": False,
        "has_scenario_evidence": False,
        "evidence_hash_matches": False,
        "no_refusal": True,
        "no_failed_pipeline_stage": True,
        "no_oracle_gaps": True,
        "pass_eligible": False,
    }
    input_shas: set[str] = set()
    native_shas: set[str] = set()
    source_paths: list[str] = []
    source_kinds: set[str] = set()
    oracles: list[dict] = []
    hashes_agree = True

    for record in records:
        source_paths.append(record["source"]["path"])
        source_kinds.add(record["kind"])
        if record["input_sha256"]:
            input_shas.add(record["input_sha256"])
        if record["native_sha256"]:
            native_shas.add(record["native_sha256"])
        if record["hashes_agree"] is False:
            hashes_agree = False
        oracle = record.get("oracle")
        if oracle:
            oracles.append(oracle)
            for check in ("has_real_oracle_flag", "has_real_oracle_name",
                          "has_scenario", "has_scenario_evidence",
                          "evidence_hash_matches"):
                if oracle["checks"][check]:
                    checks[check] = True
            if oracle["input_sha256"]:
                checks["has_input_sha256"] = True
            if oracle["native_sha256"]:
                checks["has_native_sha256"] = True
        refusal = record.get("refusal")
        if refusal and refusal["refused"]:
            checks["no_refusal"] = False
        pipeline = record.get("pipeline")
        if pipeline:
            if pipeline["failed_stages"]:
                checks["no_failed_pipeline_stage"] = False
            if pipeline["oracle_gaps"]:
                checks["no_oracle_gaps"] = False

    if input_shas or native_shas:
        checks["has_input_sha256"] = bool(input_shas)
        checks["has_native_sha256"] = bool(native_shas)
    hash_conflict = len(input_shas) > 1 or len(native_shas) > 1 or not hashes_agree
    if hash_conflict:
        checks["hashes_agree_across_sources"] = False
        reasons.append("SHA-256 values conflict across sources (FAIL)")
    input_sha = next(iter(input_shas)) if len(input_shas) == 1 else None
    native_sha = next(iter(native_shas)) if len(native_shas) == 1 else None
    if oracles and input_sha and native_sha:
        checks["oracle_hashes_match_cell"] = any(
            oracle["input_sha256"] == input_sha and oracle["native_sha256"] == native_sha
            for oracle in oracles)

    if not records:
        status = "NOT TESTED"
        reasons.append("no CLI-supplied evidence for this OS/architecture cell")
    else:
        statuses = [value for value in (record_status(r) for r in records) if value]
        status = max(statuses, key=lambda s: SEVERITY[s]) if statuses else "NOT TESTED"
        if not checks["no_refusal"]:
            reasons.append("PASS withheld: a refusal-test input refuses this cell")
        if not checks["no_failed_pipeline_stage"]:
            reasons.append("PASS withheld: a pipeline stage failed")
        if not checks["no_oracle_gaps"]:
            reasons.append("PASS withheld: pipeline reports unresolved oracle gaps")
        if status == "FAIL":
            reasons.append("evidence records a concrete failure (FAIL)")
        elif status == "BLOCKED":
            reasons.append("evidence is blocked or unsupported (BLOCKED)")
        elif status == "PARTIAL":
            reasons.append("build/launch or advisory PASS only; no validated oracle")
        elif status == "BUILD ONLY":
            reasons.append("native build recorded but no evidenced launch or scenario")
        elif status == "NOT TESTED" and records:
            reasons.append("records are advisory only; no measured build or launch evidence")
        # A bare written PASS claim can never be a matrix PASS.
        if status == "PASS":
            status = "PARTIAL"
        if hash_conflict:
            status = "FAIL"

    # PASS gate: one structurally valid oracle record whose hashes agree with
    # every source in this cell, with no contradictory evidence anywhere.
    candidate = None
    for oracle in oracles:
        if not oracle["valid_structurally"]:
            continue
        if input_sha is None or native_sha is None:
            continue
        if oracle["input_sha256"] != input_sha or oracle["native_sha256"] != native_sha:
            continue
        candidate = oracle
        break

    if (candidate and checks["hashes_agree_across_sources"] and checks["no_refusal"]
            and checks["no_failed_pipeline_stage"] and checks["no_oracle_gaps"]
            and status in ("PARTIAL", "BUILD ONLY", "NOT TESTED")):
        status = "PASS"
        checks["pass_eligible"] = True
        reasons = [
            f"validated executed oracle {candidate['name']!r} for scenario "
            f"{candidate['scenario_name']!r}",
            f"input SHA-256 {input_sha} and native SHA-256 {native_sha} agree across sources",
            f"scenario evidence {candidate['evidence_path']} SHA-256 "
            f"{candidate['claimed_evidence_sha256']} recomputed and matched",
        ]
    else:
        if oracles and not any(o["valid_structurally"] for o in oracles):
            for oracle in oracles:
                reasons.extend("PASS withheld: " + text for text in oracle["reasons"])
        elif oracles:
            if status in ("FAIL", "BLOCKED"):
                reasons.append("PASS withheld: conflicting FAIL/BLOCKED evidence in cell")
            elif input_sha is None or native_sha is None:
                reasons.append("PASS withheld: matching input/native hashes are not available")
            else:
                reasons.append("PASS withheld: oracle hashes do not match this cell's evidence")

    if not reasons:
        reasons.append("no evidence supplied for this cell")
    evidence_path = (candidate["evidence_path"] if candidate and status == "PASS"
                     else (source_paths[0] if source_paths else None))
    return {
        "os": os_name,
        "arch": arch_name,
        "key": f"{os_name}|{arch_name}",
        "status": status,
        "reason": "; ".join(reasons),
        "reasons": reasons,
        "evidence_path": evidence_path,
        "evidence_paths": sorted(set(source_paths)),
        "checks": checks,
        "hashes": {
            "input_sha256": input_sha,
            "native_sha256": native_sha,
            "evidence_sha256": (candidate["claimed_evidence_sha256"]
                                if candidate and status == "PASS" else None),
            "actual_evidence_sha256": (candidate["actual_evidence_sha256"]
                                       if candidate and status == "PASS" else None),
        },
        "oracle": ({
            "name": candidate["name"],
            "verified": candidate["flag"] is True,
            "scenario": candidate["scenario_name"],
            "scenario_evidence": candidate["scenario_evidence"],
            "evidence_path": candidate["evidence_path"],
        } if candidate and status == "PASS" else None),
        "source_kinds": sorted(source_kinds),
        "record_count": len(records),
    }


def build_matrix(records: list[dict], unmapped: list[dict], problems: list[dict],
                 inputs: list[dict], generated_at: str) -> dict:
    """Build the deterministic 3 x 4 matrix from all supplied evidence."""
    by_cell: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        by_cell.setdefault((record["os"], record["arch"]), []).append(record)
    cells = [classify_cell(os_name, arch_name,
                           by_cell.get((os_name, arch_name), []))
             for os_name in OSES for arch_name in ARCHES]
    by_status = {status: sum(1 for cell in cells if cell["status"] == status)
                 for status in STATUSES}
    by_os = {os_name: sum(1 for cell in cells if cell["os"] == os_name)
             for os_name in OSES}
    by_arch = {arch_name: sum(1 for cell in cells if cell["arch"] == arch_name)
               for arch_name in ARCHES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "oses": list(OSES),
        "arches": list(ARCHES),
        "policy": {
            "pass_requires": ["executed original-windows oracle flag",
                              "matching input and native SHA-256 across sources",
                              "named scenario with an evidenced file and matching hash"],
            "written_status_is_advisory_only": True,
            "refusals_and_pipeline_stages_can_only_cap": True,
            "unmapped_records_are_reported_not_guessed": True,
            "evidence_paths_come_from_cli_or_evidence_files": True,
        },
        "inputs": sorted(inputs + problems,
                         key=lambda item: (item["kind"], item["path"])),
        "unmapped": sorted(unmapped, key=lambda item: (item["path"], item.get("index", 0))),
        "cells": cells,
        "summary": {
            "total": len(cells),
            "by_status": by_status,
            "by_os": by_os,
            "by_arch": by_arch,
            "record_count": len(records),
        },
    }


def render_markdown(matrix: dict) -> str:
    lines = [
        "# OS capability matrix",
        "",
        f"Generated: {matrix['generated_at']}  ",
        f"Schema: {matrix['schema']}  ",
        "PASS requires an executed oracle flag, matching hashes across sources and "
        "scenario evidence whose hash is recomputed here. Written statuses, "
        "refusals and pipeline outcomes are stage evidence only.",
        "",
        "| OS \\ Arch | " + " | ".join(ARCHES) + " |",
        "| --- | " + " | ".join("---" for _ in ARCHES) + " |",
    ]
    for os_name in OSES:
        row = [cell for cell in matrix["cells"] if cell["os"] == os_name]
        statuses = [next(c["status"] for c in row if c["arch"] == arch) for arch in ARCHES]
        lines.append(f"| {os_name} | " + " | ".join(statuses) + " |")
    lines += ["", "## Cell details", ""]
    for cell in matrix["cells"]:
        lines += [
            f"### {cell['os']} / {cell['arch']} — {cell['status']}",
            "",
            f"- Reason: {cell['reason']}",
            f"- Evidence: `{cell['evidence_path']}`" if cell["evidence_path"] else "- Evidence: none",
            f"- Sources: {', '.join(cell['evidence_paths']) or 'none'}",
            f"- Hashes: input `{cell['hashes']['input_sha256'] or '-'}`; "
            f"native `{cell['hashes']['native_sha256'] or '-'}`",
            "",
        ]
    if matrix["unmapped"]:
        lines += ["## Unmapped records", ""]
        for item in matrix["unmapped"]:
            lines.append(f"- `{item['path']}` (#{item.get('index', 0)}): {item['reason']}")
        lines.append("")
    bad_inputs = [item for item in matrix["inputs"] if item["status"] == "INVALID"]
    if bad_inputs:
        lines += ["## Invalid inputs", ""]
        for item in bad_inputs:
            lines.append(f"- `{item['path']}`: {item.get('error', 'invalid')}")
        lines.append("")
    counts = matrix["summary"]["by_status"]
    lines += ["Summary: " + "; ".join(f"{key}: {value}" for key, value in counts.items()) + ".", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--acceptance-matrix", action="append", default=[], metavar="JSON",
                    help="acceptance_matrix/corpus_acceptance JSON (repeatable)")
    ap.add_argument("--refusal-test", action="append", default=[], metavar="JSON",
                    help="structured refusal-test JSON (repeatable)")
    ap.add_argument("--pipeline-report", action="append", default=[], metavar="JSON",
                    help="autorecomp report.json/verification.json (repeatable)")
    ap.add_argument("--target-map", action="append", default=[], metavar="JSON",
                    help="name/hash/path -> {os,arch} assignments (repeatable)")
    ap.add_argument("--default-os", default=None, metavar="OS",
                    help="OS for records with no OS metadata (e.g. Windows)")
    ap.add_argument("--default-arch", default=None, metavar="ARCH",
                    help="architecture for records with no arch metadata (e.g. x86)")
    ap.add_argument("--out", required=True, type=Path, help="write the JSON matrix here")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown summary here")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for deterministic output")
    args = ap.parse_args(argv)

    if not any((args.acceptance_matrix, args.refusal_test, args.pipeline_report)):
        ap.error("supply at least one evidence input")
    default_os = norm_os(args.default_os) if args.default_os else None
    default_arch = norm_arch(args.default_arch) if args.default_arch else None
    if args.default_os and not default_os:
        ap.error(f"unknown --default-os: {args.default_os}")
    if args.default_arch and not default_arch:
        ap.error(f"unknown --default-arch: {args.default_arch}")

    inputs: list[dict] = []
    problems: list[dict] = []
    loaded: list[dict] = []
    for kind, paths in (("acceptance-matrix", args.acceptance_matrix),
                        ("refusal-test", args.refusal_test),
                        ("pipeline-report", args.pipeline_report)):
        found, failures = load_sources(paths, kind)
        loaded.extend(found)
        problems.extend(failures)
        inputs.extend(item["source"] for item in found)
    target_map, map_failures = load_target_map(args.target_map)
    problems.extend(map_failures)

    # Provenance for a target-map file is recorded once, even when it has no
    # usable entries; an unreadable file is already in problems above.
    for raw in args.target_map:
        path = Path(raw)
        if not any(item["path"] == str(path) for item in inputs + problems):
            inputs.append({"kind": "target-map", "path": str(path),
                           "sha256": sha256_file(path), "status": "ok"})

    records, unmapped = normalize_records(loaded, target_map, default_os, default_arch)
    matrix = build_matrix(records, unmapped, problems, inputs,
                          generated_at=args.generated_at or utc_now())
    payload = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(matrix), encoding="utf-8")
    counts = matrix["summary"]["by_status"]
    print("summary: " + "; ".join(f"{key}: {value}" for key, value in counts.items()))
    print(f"matrix: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
