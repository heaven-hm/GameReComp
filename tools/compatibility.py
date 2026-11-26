#!/usr/bin/env python3
"""One machine-readable compatibility inventory and regression gate (issue #14).

The canonical expectations live in docs/compatibility/baseline.json and the
schema in docs/compatibility/coverage.schema.json. This module only observes:
it scans the existing win32hle registration/dispatch source, reuses the
checked-in game reports and synthetic fixtures, and emits coverage.json plus
two concise Markdown reports. It never implements a graphics/audio/input/
network/gameplay backend and never invents a PASS for an absent input.

    python3 -m tools.compatibility                 # generate reports
    python3 -m tools.compatibility --check         # gate; 0 PASS, 1 FAIL, 2 INCOMPLETE
    python3 -m tools.compatibility --branch main --repo /path/to/checkout

Exit code 2 means NOT_RUN inputs remain; it is deliberately not PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "compatibility"
SCHEMA_PATH = DOCS / "coverage.schema.json"
BASELINE_PATH = DOCS / "baseline.json"
FIXTURES = ROOT / "tests" / "fixtures"

PASS = "PASS"
FAIL = "FAIL"
INCOMPLETE = "INCOMPLETE"
PARTIAL = "PARTIAL"
MISSING_INPUT = "MISSING_INPUT"
NOT_RUN = "NOT_RUN"
HOST_NOT_TESTED = "HOST_NOT_TESTED"
UNSUPPORTED = "UNSUPPORTED"
SKIP = "SKIP"
NON_PASS = {FAIL, PARTIAL, MISSING_INPUT, NOT_RUN, HOST_NOT_TESTED, UNSUPPORTED, SKIP}

INTEGRATION_FILES = [
    "tools/gamesdb.py",
    "tools/corpus/inventory.py",
    "tools/autorecomp/diagnose.py",
    "docs/GAME_MATRIX.md",
    "docs/RUNTIME_COMPAT.md",
]
REPORT_FILES = [
    "reports/games/results.json",
    "reports/games/diagnostic-sweep.json",
    "reports/games/regression-baseline.json",
    "reports/certified/index.json",
]
EXPECTED_NON_PASS = {MISSING_INPUT, NOT_RUN, HOST_NOT_TESTED, UNSUPPORTED, SKIP}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def missing_key(path):
    """A stable key for an absent input, keeping every input keyed by SHA-256."""
    return sha256_bytes(("missing:" + path).encode("utf-8"))


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo, *args, default=""):
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return default


def load_json(path):
    """Return (data, status, detail) without ever treating absence as success."""
    path = Path(path)
    if not path.is_file():
        return None, MISSING_INPUT, "file not present: " + str(path)
    try:
        return json.loads(path.read_text(encoding="utf-8")), "ok", ""
    except (OSError, ValueError) as exc:
        return None, "invalid", str(exc)


def _minimal_validate(instance, schema, errors, where="$"):
    """Dependency-free subset used when jsonschema is not installed."""
    if "$ref" in schema and schema["$ref"].startswith("#/"):
        node = schema
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        _minimal_validate(instance, node, errors, where)
        return
    t = schema.get("type")
    if t is not None:
        types = [t] if isinstance(t, str) else t
        ok = any((x == "object" and isinstance(instance, dict)) or
                 (x == "array" and isinstance(instance, list)) or
                 (x == "string" and isinstance(instance, str)) or
                 (x == "boolean" and isinstance(instance, bool)) or
                 (x == "integer" and isinstance(instance, int) and
                  not isinstance(instance, bool)) or
                 (x == "null" and instance is None) for x in types)
        if not ok:
            errors.append(f"{where}: expected {types}")
            return
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{where}: expected {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{where}: {instance!r} not in {schema['enum']}")
    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, instance):
            errors.append(f"{where}: {instance!r} does not match {pattern}")
        if schema.get("minLength", 0) > len(instance):
            errors.append(f"{where}: string too short")
    if isinstance(instance, int) and not isinstance(instance, bool):
        if instance < schema.get("minimum", instance):
            errors.append(f"{where}: below minimum")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{where}: missing {key}")
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                _minimal_validate(value, props[key], errors, f"{where}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: unexpected {key}")
    if isinstance(instance, list) and "items" in schema:
        for i, value in enumerate(instance):
            _minimal_validate(value, schema["items"], errors, f"{where}[{i}]")


def validate(doc, schema):
    """Validate against the canonical schema, with a dependency-free fallback."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        errors = []
        _minimal_validate(doc, schema, errors)
        return errors
    validator = Draft202012Validator(schema)
    return [f"{' -> '.join(str(p) for p in e.absolute_path)}: {e.message}"
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def read_baseline():
    data, status, detail = load_json(BASELINE_PATH)
    if status != "ok":
        raise SystemExit(f"compatibility baseline unavailable: {status} {detail}")
    return data


def scan_dispatch(repo):
    """Scan the *existing* shim registry; no duplicate API table is maintained."""
    host = repo / "runtime" / "win32hle" / "host.c"
    host_text = host.read_text(encoding="utf-8", errors="replace") if host.is_file() else ""
    registered = set(re.findall(
        r"win32hle_register\s*\(\s*(win32hle_[A-Za-z0-9_]+)\s*\)", host_text))
    shims = {}
    methods = set()
    for source in sorted((repo / "runtime" / "win32hle").glob("*.c")):
        text = source.read_text(encoding="utf-8", errors="replace")
        for symbol, body in re.findall(
                r"const\s+win32hle_shim\s+(win32hle_[A-Za-z0-9_]+)\s*\[\]\s*=\s*\{(.*?)\n\};",
                text, re.S):
            shims[symbol] = re.findall(r'"([^"]+)"', body)
        methods.update(re.findall(r'"([A-Za-z0-9_]+::[A-Za-z0-9_]+)"', text))
    return {"host_present": host.is_file(), "registered": registered,
            "shims": shims, "methods": methods}


def _fixture_specs(baseline):
    """Map fixture ids to deterministic builder inputs from the baseline."""
    by_id = {f["family_id"]: f for f in baseline["families"]}
    specs = {
        "fx-existing-pe32-minimal": {
            "kind": "fixture-pe32", "dll": "KERNEL32.dll", "func": "ExitProcess",
            "path": "tests/fixtures/pe32-minimal.exe", "expected": PASS,
            "origin": "tests/fixtures/make_pe32_fixture.py:build_pe", "family": "win32-kernel32",
        },
        "fx-missing-input-probe": {
            "kind": "fixture-pe32", "dll": "NONE.dll", "func": "Missing",
            "path": "tests/fixtures/__missing__/no-such-input.exe",
            "expected": MISSING_INPUT, "origin": "intentionally absent fixture",
            "family": None,
        },
    }
    for family in baseline["families"]:
        fid = "fx-" + family["family_id"]
        guest = family["guest_interface"]
        specs[fid] = {
            "kind": {"PE32": "fixture-pe32", "NE": "fixture-ne",
                     "PE32+": "fixture-pe32plus"}[family["pe_format"]],
            "dll": guest["dll"],
            "func": (guest["imports"] or ["Unknown"])[0],
            "module": guest["dll"].split(".")[0],
            "path": "synthetic:" + fid,
            "expected": family["expected_fixture_result"],
            "origin": "tests/fixtures/make_compat_fixture.py:" + fid,
            "family": family["family_id"],
        }
    return specs


def build_inputs(repo, baseline, scan):
    """Every input, keyed by SHA-256 (of bytes, or of 'missing:' + path)."""
    inputs = {}
    compat = _load_module(FIXTURES / "make_compat_fixture.py", "grc_compat_fixture")
    legacy = _load_module(FIXTURES / "make_pe32_fixture.py", "grc_pe32_fixture")

    def add(key, **fields):
        rec = {"input_sha256": key, "kind": "tool", "path": "", "present": False,
               "status": MISSING_INPUT, "fixture_id": None,
               "expected_result": NOT_RUN, "observed_result": NOT_RUN,
               "detail": "", "bytes_sha256": None}
        rec.update(fields)
        inputs[key] = rec
        return rec

    def add_path(rel, kind, expected=NOT_RUN, detail="", fixture_id=None):
        p = repo / rel
        if not p.is_file():
            return add(missing_key(rel), kind=kind, path=rel, present=False,
                       status=MISSING_INPUT, expected_result=expected,
                       observed_result=expected if expected in EXPECTED_NON_PASS else NOT_RUN,
                       detail=detail or "input absent")
        data = p.read_bytes()
        digest = sha256_bytes(data)
        return add(digest, kind=kind, path=rel, present=True, status="ok",
                   bytes_sha256=digest, expected_result=expected,
                   observed_result=PASS if expected == PASS else NOT_RUN,
                   detail=detail, fixture_id=fixture_id)

    add_path(str(SCHEMA_PATH.relative_to(ROOT)), "schema", PASS,
             "canonical compatibility schema")
    add_path(str(BASELINE_PATH.relative_to(ROOT)), "tool", PASS,
             "canonical compatibility baseline")
    add_path("tools/compatibility.py", "tool", PASS, "inventory generator")
    add_path("runtime/win32hle/host.c", "registry", NOT_RUN,
             "shim registration source (static audit)")
    for rel in INTEGRATION_FILES:
        add_path(rel, "integration", NOT_RUN, "existing tool/report integration point")
    for rel in REPORT_FILES:
        add_path(rel, "report", PASS, "existing evidence report (read-only)")
    seen_sources = set()
    for family in baseline["families"]:
        for rel in family["source_provenance"]["files"]:
            if rel in seen_sources:
                continue
            seen_sources.add(rel)
            kind = "source" if rel.startswith("runtime/") else "integration"
            add_path(rel, kind, NOT_RUN, "source provenance for " + family["family_id"])

    specs = _fixture_specs(baseline)
    for fid in baseline["fixture_ids"]:
        spec = specs.get(fid)
        if spec is None:
            continue
        path = spec["path"]
        if fid == "fx-existing-pe32-minimal":
            data = legacy.build_pe()  # reuse the checked-in fixture generator
            digest = sha256_bytes(data)
            add(digest, kind="fixture-pe32", path=path, present=True, status="ok",
                bytes_sha256=digest, fixture_id=fid, expected_result=PASS,
                observed_result=PASS, detail=spec["origin"])
            continue
        if fid == "fx-missing-input-probe":
            add(missing_key(path), kind=spec["kind"], path=path, present=False,
                status=MISSING_INPUT, fixture_id=fid, expected_result=MISSING_INPUT,
                observed_result=MISSING_INPUT,
                detail="intentional missing input: never PASS")
            continue
        data = compat.build_fixture(spec["kind"], spec["dll"], spec["func"],
                                    spec.get("module"))
        digest = sha256_bytes(data)
        add(digest, kind=spec["kind"], path=path, present=True, status="ok",
            bytes_sha256=digest, fixture_id=fid,
            expected_result=spec["expected"], observed_result=NOT_RUN,
            detail=spec["origin"] + " " + spec["dll"] + "!" + spec["func"])
    return inputs, specs


def _fixture_input(inputs, fid):
    return next((r for r in inputs.values() if r["fixture_id"] == fid), None)


def observe_families(baseline, inputs, scan, head):
    """Derive honest per-family statuses; supported != original-verified."""
    families = []
    for spec in baseline["families"]:
        fid = spec["family_id"]
        fx_id = "fx-" + fid
        fx = _fixture_input(inputs, fx_id)
        prov = spec["source_provenance"]
        reg = prov["registration_symbol"]
        missing_sources = []
        for rel in prov["files"]:
            rec = next((r for r in inputs.values()
                        if r["path"] == rel and not r["present"]), None)
            if rec:
                missing_sources.append(rel)
        host_rec = next((r for r in inputs.values()
                         if r["path"] == "runtime/win32hle/host.c"), None)
        host_missing = bool(host_rec and not host_rec["present"])

        unsupported = spec["expected_implemented_status"] == "not-present"
        method_labels = spec["guest_interface"]["method_slots"]
        has_methods = all(label in scan["methods"] for label in method_labels)
        reg_ok = reg is None or reg in scan["registered"]
        sources_ok = not missing_sources

        if unsupported:
            new_capability = bool(reg and reg in scan["registered"])
            implemented = "declared" if new_capability else "not-present"
            result = PASS if new_capability else UNSUPPORTED
        elif host_missing or missing_sources:
            implemented = "not-present"
            result = MISSING_INPUT
        elif not reg_ok:
            implemented = "not-present"
            result = MISSING_INPUT
        elif reg is None and method_labels and not has_methods:
            implemented = "not-present"
            result = MISSING_INPUT
        else:
            implemented = "declared"
            result = PARTIAL if spec["known_limitations"] else PASS
        if fx is None or not fx["present"]:
            result = fx["observed_result"] if fx else NOT_RUN

        evidence = []
        if host_rec:
            evidence.append({"input_sha256": host_rec["input_sha256"],
                             "kind": "registry",
                             "result": NOT_RUN if host_rec["present"] else MISSING_INPUT,
                             "note": "registration symbol " + str(reg) +
                                     (" found" if reg_ok else " absent")})
        for rel in prov["files"]:
            rec = next((r for r in inputs.values() if r["path"] == rel), None)
            if rec:
                evidence.append({"input_sha256": rec["input_sha256"], "kind": "source",
                                 "result": NOT_RUN if rec["present"] else MISSING_INPUT,
                                 "note": rel})
        if fx:
            evidence.append({"input_sha256": fx["input_sha256"], "kind": "fixture",
                             "result": result, "note": fx["detail"]})

        if fx is not None:
            fx["observed_result"] = result
        families.append({
            "family_id": fid,
            "api_family": spec["api_family"],
            "interface_version": spec["interface_version"],
            "interface_iid": spec["interface_iid"],
            "pe_format": spec["pe_format"],
            "guest_interface": spec["guest_interface"],
            "implemented_status": implemented,
            "expected_implemented_status": spec["expected_implemented_status"],
            "fixture_result": result,
            "expected_fixture_result": spec["expected_fixture_result"],
            "fixture_id": fx_id,
            "abi_signature": spec["abi_signature"],
            "host_dependencies": spec["host_dependencies"],
            "known_limitations": spec["known_limitations"],
            "source_provenance": {**prov, "commit": head},
            "evidence": evidence,
            "owner_issue": spec["owner_issue"],
            "unresolved_failures": ([f"{reg} missing from {prov['registered_in']}"]
                                    if reg and not reg_ok and not unsupported else []),
            "notes": spec["notes"],
        })
    return families


def _unresolved_names(repo, baseline):
    names = set()
    report, status, _ = load_json(repo / "reports" / "games" / "diagnostic-sweep.json")
    if status == "ok":
        for row in report.get("ranked_first_imports", []):
            if row.get("import"):
                names.add(row["import"])
    report, status, _ = load_json(repo / "reports" / "games" / "results.json")
    if status == "ok":
        for row in report.get("runs", []):
            if row.get("first_import"):
                names.add(row["first_import"])
    for family in baseline["families"]:
        if family["expected_implemented_status"] == "not-present":
            guest = family["guest_interface"]
            if guest["imports"]:
                names.add(guest["dll"] + "!" + guest["imports"][0])
    return names


def make_gate(doc, baseline, repo, schema_errors):
    checks = {
        "schema_valid": not schema_errors,
        "deleted_tests": sorted(set(baseline["fixture_ids"]) -
                                {r["fixture_id"] for r in doc["inputs"].values()
                                 if r["fixture_id"]}),
        "new_critical_imports": [],
        "fixture_mismatches": [],
        "missing_inputs": [],
        "missing_never_pass": [],
        "unsupported_never_pass": [],
    }
    allow = set(baseline["known_unresolved_critical_imports"])
    critical = set(baseline["critical_import_dlls"])
    for name in sorted(_unresolved_names(repo, baseline)):
        dll = name.split("!")[0] if "!" in name else ""
        if name not in allow and "!" in name and dll in critical:
            checks["new_critical_imports"].append(name)

    for rec in doc["inputs"].values():
        if not rec["present"] and rec["expected_result"] not in EXPECTED_NON_PASS:
            checks["missing_inputs"].append(rec["path"])
        if not rec["present"] and rec["observed_result"] == PASS:
            checks["missing_never_pass"].append(rec["path"])
    for family in doc["families"]:
        if family["fixture_result"] != family["expected_fixture_result"]:
            checks["fixture_mismatches"].append(
                family["family_id"] + ": " + family["expected_fixture_result"] +
                " -> " + family["fixture_result"])
        if (family["expected_implemented_status"] == "not-present" and
                family["fixture_result"] == PASS):
            checks["unsupported_never_pass"].append(family["family_id"])

    failed = (not checks["schema_valid"] or
              any(checks[k] for k in ("deleted_tests", "new_critical_imports",
                                      "fixture_mismatches", "missing_never_pass",
                                      "unsupported_never_pass")))
    incomplete = bool(checks["missing_inputs"])
    if failed:
        verdict, reason = FAIL, "schema, deleted test, new critical import, or status mismatch"
    elif incomplete:
        verdict, reason = INCOMPLETE, "required input absent: " + ", ".join(checks["missing_inputs"])
    else:
        verdict, reason = PASS, "baseline matches observed static and synthetic evidence"
    checks["schema_errors"] = schema_errors
    return {"verdict": verdict, "checks": checks, "reason": reason}


def build_document(repo, baseline, schema, branch, head):
    repo = Path(repo).resolve()
    scan = scan_dispatch(repo)
    inputs, _ = build_inputs(repo, baseline, scan)
    families = observe_families(baseline, inputs, scan, head)
    summary = {
        "families": len(families),
        "inputs": len(inputs),
        "missing_inputs": sum(1 for r in inputs.values() if not r["present"]),
        "not_run": sum(1 for r in inputs.values() if r["observed_result"] == NOT_RUN),
        "pass": sum(1 for f in families if f["fixture_result"] == PASS),
        "partial": sum(1 for f in families if f["fixture_result"] == PARTIAL),
        "unsupported": sum(1 for f in families if f["fixture_result"] == UNSUPPORTED),
    }
    doc = {
        "schema_version": "1.0.0",
        "generator": "python3 -m tools.compatibility",
        "target": {
            "branch": branch,
            "head_at_generation": head,
            "host_os": platform.system(),
            "host_arch": platform.machine(),
            "pe_formats": ["PE32", "NE", "PE32+"],
        },
        "inputs": inputs,
        "families": families,
        "summary": summary,
        "gate": {"verdict": PASS,
                 "checks": {"schema_valid": True, "deleted_tests": [],
                            "new_critical_imports": [], "fixture_mismatches": [],
                            "missing_inputs": [], "missing_never_pass": [],
                            "unsupported_never_pass": []},
                 "reason": "pending"},
    }
    errors = validate(doc, schema)
    doc["gate"] = make_gate(doc, baseline, repo, errors)
    final_errors = validate(doc, schema)
    if final_errors:
        doc["gate"]["checks"]["schema_errors"] = (
            doc["gate"]["checks"].get("schema_errors", []) + final_errors)
        doc["gate"]["checks"]["schema_valid"] = False
        doc["gate"]["verdict"] = FAIL
        doc["gate"]["reason"] = "generated document failed canonical schema"
    return doc


def render_api_matrix(doc):
    lines = [
        "# Compatibility API matrix (generated)",
        "",
        "Generated by `python3 -m tools.compatibility`; edit `docs/compatibility/baseline.json`, not this file.",
        f"Branch `{doc['target']['branch']}` @ `{doc['target']['head_at_generation'][:12]}`; "
        f"host {doc['target']['host_os']}/{doc['target']['host_arch']}.",
        "",
        "A row's status is *static registration plus synthetic fixture evidence* only. "
        "`original-verified` requires the independent oracle in `docs/VERIFICATION.md`; none is claimed here.",
        "",
        "| API family | Interface | PE | Evidence status | Fixture | Owner | Host dependencies | Known limitations |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in doc["families"]:
        limits = "; ".join(f["known_limitations"]) or "none recorded"
        lines.append("| {api_family} | {interface_version} | {pe_format} | {status} | {result} | "
                     "#{owner_issue} | {deps} | {limits} |".format(
                         api_family=f["api_family"], interface_version=f["interface_version"],
                         pe_format=f["pe_format"], status=f["implemented_status"],
                         result=f["fixture_result"], owner_issue=f["owner_issue"],
                         deps=", ".join(f["host_dependencies"]) or "none", limits=limits))
    g = doc["gate"]
    lines += [
        "",
        f"Summary: {doc['summary']['families']} families, {doc['summary']['pass']} PASS, "
        f"{doc['summary']['partial']} PARTIAL, {doc['summary']['unsupported']} UNSUPPORTED, "
        f"{doc['summary']['missing_inputs']} missing inputs, {doc['summary']['not_run']} NOT_RUN inputs.",
        f"Gate: **{g['verdict']}** — {g['reason']}.",
        "",
        "`MISSING_INPUT` and `NOT_RUN` are never PASS. "
        "Every input is keyed by SHA-256 in `coverage.json`.",
        "",
    ]
    return "\n".join(lines)


def render_games(doc, repo):
    data, status, detail = load_json(repo / "reports" / "games" / "results.json")
    lines = [
        "# Compatibility games report (generated)",
        "",
        "Read-only view of `reports/games/results.json`; no proprietary binary is bundled here.",
        "",
    ]
    if status != "ok":
        lines += [f"`reports/games/results.json`: **{status}** — {detail}.",
                  "No game row is PASS without its input SHA-256 and evidence.", ""]
        return "\n".join(lines)
    runs = data.get("runs", [])
    counts = {}
    for row in runs:
        key = (str(row.get("verification") or "unknown") + " / " +
               str(row.get("run_status") or "unknown"))
        counts[key] = counts.get(key, 0) + 1
    lines += [f"Host from this inventory run: {doc['target']['host_os']}/"
              f"{doc['target']['host_arch']}. Other hosts are `HOST_NOT_TESTED`, never PASS.",
              "", "| Verification / run status | Rows |", "|---|---|"]
    for key in sorted(counts):
        lines.append(f"| {key} | {counts[key]} |")
    lines += ["", "| Game | Input SHA-256 (12) | Verification | Host |",
              "|---|---|---|---|"]
    for row in runs[:15]:
        lines.append("| {game} | `{sha}` | {verification} | {host} |".format(
            game=str(row.get("game") or row.get("filename") or "?").replace("|", "\\|"),
            sha=str(row.get("input_sha256") or "MISSING_INPUT")[:12],
            verification=str(row.get("verification") or "unknown"),
            host=str(row.get("host") or "HOST_NOT_TESTED")))
    lines += ["", f"Total rows: {len(runs)}. A screenshot or process exit is not an "
              "independent original-game oracle; see `docs/VERIFICATION.md`.", ""]
    return "\n".join(lines)


def write_evidence(repo, branch, doc):
    out = repo / "reports" / "compatibility"
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", branch)
    payload = {
        "schema_version": "1.0.0",
        "branch": branch,
        "head_at_generation": doc["target"]["head_at_generation"],
        "host_os": doc["target"]["host_os"],
        "host_arch": doc["target"]["host_arch"],
        "verdict": doc["gate"]["verdict"],
        "checks": doc["gate"]["checks"],
        "summary": doc["summary"],
        "families": {f["family_id"]: f["fixture_result"] for f in doc["families"]},
        "input_sha256_by_path": {r["path"]: r["input_sha256"]
                                 for r in doc["inputs"].values()},
    }
    target = out / (safe + ".json")
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(ROOT), help="checkout to inventory")
    parser.add_argument("--branch", default=None)
    parser.add_argument("--head", default=None)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="exit 0 PASS, 1 FAIL, 2 INCOMPLETE")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    baseline = read_baseline()
    branch = args.branch or _git(repo, "rev-parse", "--abbrev-ref", "HEAD", default="unknown")
    head = args.head or _git(repo, "rev-parse", "HEAD", default="unknown")
    doc = build_document(repo, baseline, schema, branch, head)

    if not args.no_write:
        out = repo / "docs" / "compatibility"
        out.mkdir(parents=True, exist_ok=True)
        (out / "coverage.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
        (out / "api_matrix.md").write_text(render_api_matrix(doc), encoding="utf-8")
        (out / "games.md").write_text(render_games(doc, repo), encoding="utf-8")
        evidence = write_evidence(repo, branch, doc)
    else:
        evidence = None

    if not args.quiet:
        print(f"compatibility gate [{branch}]: {doc['gate']['verdict']} — {doc['gate']['reason']}")
        print(f"  families={doc['summary']['families']} inputs={doc['summary']['inputs']} "
              f"pass={doc['summary']['pass']} partial={doc['summary']['partial']} "
              f"unsupported={doc['summary']['unsupported']} "
              f"missing={doc['summary']['missing_inputs']}")
        if evidence:
            print("  evidence: " + str(evidence.relative_to(repo)))
        for check in ("deleted_tests", "new_critical_imports", "fixture_mismatches",
                      "missing_inputs", "missing_never_pass", "unsupported_never_pass"):
            if doc["gate"]["checks"][check]:
                print(f"  {check}: {doc['gate']['checks'][check]}")
        for error in doc["gate"]["checks"]["schema_errors"]:
            print("  schema: " + error)

    if args.check:
        return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[doc["gate"]["verdict"]]
    return 0


if __name__ == "__main__":
    sys.exit(main())
