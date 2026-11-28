"""Deterministic tests for the fail-closed Mach-O -> PE feasibility gate (#132/#135).

Every artifact here is synthetic and lives under tmp_path: no game binary, no
repository evidence path and no live component is used. The tests prove the
central rule: flags and self-reported verdicts alone can never produce a PASS;
only a CLI-supplied, hash-linked, built *and executed* PE can.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

from tools.macho_to_pe_gate import (
    BLOCKED, DEFERRED, NOT_TESTED, PASS, REQUIRED_COMPONENTS, main, sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "macho_to_pe_gate.py"
MACHO = b"\xfe\xed\xfa\xce" + b"\x00" * 60


def _pe_bytes(machine: int = 0x014C, magic: int = 0x010B) -> bytes:
    data = bytearray(0x200)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x84, machine)
    struct.pack_into("<H", data, 0x94, 0xE0)  # SizeOfOptionalHeader
    struct.pack_into("<H", data, 0x98, magic)  # optional-header magic
    return bytes(data)


def _json(path: Path, doc: object) -> None:
    path.write_text(json.dumps(doc), encoding="utf-8")


def _edit(path: Path, **changes) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    _json(path, doc)


def _artifact(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256_file(path)}


def _bundle(tmp_path: Path, name: str = "case") -> dict:
    """Build a complete, internally consistent synthetic evidence bundle."""
    root = tmp_path / name
    root.mkdir(parents=True)

    macho = root / "input.dylib"
    macho.write_bytes(MACHO)
    pe = root / "out.exe"
    pe.write_bytes(_pe_bytes())

    components = {}
    for key in REQUIRED_COMPONENTS:
        artifact = root / f"{key}.py"
        artifact.write_bytes(f"# {key}\n".encode())
        proof = root / f"{key}_proof.json"
        _json(proof, {"component": key, "verdict": "PASS",
                      "command": f"selftest --{key}", "executed": True})
        components[key] = {
            "available": True,
            "artifact": _artifact(artifact),
            "license": "MIT",
            "proof": _artifact(proof),
        }
    components_doc = root / "components.json"
    _json(components_doc, components)

    build_report = root / "build_report.json"
    _json(build_report, {
        "verdict": "PASS", "command": "bridge --build",
        "input_sha256": sha256_file(macho), "output_sha256": sha256_file(pe),
    })
    build_doc = root / "build.json"
    _json(build_doc, {
        "source_macho": _artifact(macho),
        "pe": {**_artifact(pe), "arch": "x86"},
        "report": _artifact(build_report),
    })

    log = root / "run.log"
    log.write_bytes(b"scenario boot: exit 0, frame hash abc\n")
    run_report = root / "run_report.json"
    _json(run_report, {
        "verdict": "PASS", "executed": True, "scenario": "boot",
        "oracle": "original-windows", "exit_code": 0,
        "pe_sha256": sha256_file(pe), "log": _artifact(log),
    })
    run_doc = root / "run.json"
    _json(run_doc, {"report": _artifact(run_report)})

    return {
        "root": root, "components": components_doc, "build": build_doc,
        "run": run_doc, "macho": macho, "pe": pe,
        "build_report": build_report, "run_report": run_report,
        "log": log, "json_out": root / "decision.json",
    }


def _argv(paths: dict) -> list[str]:
    return ["--components", str(paths["components"]),
            "--build", str(paths["build"]), "--run", str(paths["run"]),
            "--json", str(paths["json_out"])]


def _run(paths: dict, *, cli: bool = False) -> tuple[int, dict]:
    if cli:
        done = subprocess.run([sys.executable, str(TOOL), *_argv(paths)],
                              capture_output=True, text=True, check=False)
        assert done.stdout.strip(), done.stderr
        return done.returncode, json.loads(done.stdout)
    code = main(_argv(paths))
    return code, json.loads(paths["json_out"].read_text(encoding="utf-8"))


def _needs(result: dict, needle: str) -> None:
    assert any(needle in reason for reason in result["reasons"]), result["reasons"]


def _rehash(owner: Path, artifact: Path, *keys: str) -> None:
    """Refresh a declared artifact hash after a test mutated that artifact."""
    doc = json.loads(owner.read_text(encoding="utf-8"))
    node = doc
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = _artifact(artifact)
    _json(owner, doc)


def _rehash_pe(paths: dict) -> None:
    """Keep declared PE hashes consistent after tampering with the PE bytes."""
    digest = sha256_file(paths["pe"])
    _edit(paths["build"], pe={**_artifact(paths["pe"]), "arch": "x86"})
    _edit(paths["build_report"], output_sha256=digest)
    _edit(paths["run_report"], pe_sha256=digest)
    _rehash(paths["build"], paths["build_report"], "report")
    _rehash(paths["run"], paths["run_report"], "report")


def _rehash_macho(paths: dict) -> None:
    digest = sha256_file(paths["macho"])
    _edit(paths["build"], source_macho=_artifact(paths["macho"]))
    _edit(paths["build_report"], input_sha256=digest)
    _rehash(paths["build"], paths["build_report"], "report")


# --- the one legal PASS, with no support claim ------------------------------

def test_full_evidenced_bundle_passes_without_claiming_support(tmp_path):
    paths = _bundle(tmp_path)
    code, result = _run(paths, cli=True)
    assert code == 0
    assert result["verdict"] == PASS
    assert result["gate_allowed"] is True
    assert set(result["checks"].values()) == {PASS}
    assert result["reasons"] == []
    assert result["support_claimed"] is False
    assert result["reverse_porting_works"] is False
    assert result["claim"] == "NO_SUPPORT_CLAIM"
    assert "#132" in result["issues"] and "#135" in result["issues"]
    assert "not a claim" in result["rule"]


# --- flags alone are never evidence -----------------------------------------

def test_flags_alone_never_pass(tmp_path):
    root = tmp_path / "flags"
    root.mkdir()
    components = {
        key: {"available": True, "proven": True, "verdict": "PASS",
              "built": True, "executed": True}
        for key in REQUIRED_COMPONENTS
    }
    report = root / "run_report.json"
    _json(report, {"verdict": "PASS", "executed": True, "scenario": "boot",
                   "oracle": "original-windows", "exit_code": 0,
                   "built": True, "pass": True})
    docs = {
        "components": {"available": True, "verdict": "PASS",
                       **components},
        "build": {"built": True, "verdict": "PASS", "proven": True,
                  "arch": "x86"},
        "run": {"executed": True, "verdict": "PASS",
                "report": _artifact(report)},
    }
    paths = {}
    for name, doc in docs.items():
        paths[name] = root / f"{name}.json"
        _json(paths[name], doc)
    paths["json_out"] = root / "decision.json"

    code, result = _run(paths, cli=True)
    assert code != 0
    assert result["verdict"] != PASS
    assert result["gate_allowed"] is False
    assert result["support_claimed"] is False
    _needs(result, "PE writer: no artifact declared")
    _needs(result, "Win32 runtime proof: no artifact declared")
    _needs(result, "build: no PE build evidence")
    _needs(result, "run execution log: no artifact declared")


def test_reported_pass_without_real_pe_or_log_is_not_a_gate_pass(tmp_path):
    """A run report that says PASS, with no PE artifact or log, is still refused."""
    paths = _bundle(tmp_path)
    paths["pe"].write_bytes(b"MZ not a PE at all")
    _rehash_pe(paths)
    paths["log"].unlink()
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "build: PE signature/header invalid")
    _needs(result, "run execution log: artifact missing")


# --- input-level fail-closed -------------------------------------------------

def test_missing_cli_document_is_not_tested(tmp_path):
    paths = _bundle(tmp_path)
    argv = _argv(paths)
    argv[argv.index("--run") + 1] = str(tmp_path / "absent.json")
    code = main(argv)
    result = json.loads(paths["json_out"].read_text(encoding="utf-8"))
    assert code != 0
    assert result["verdict"] == NOT_TESTED
    assert result["checks"]["components"] == NOT_TESTED
    _needs(result, "MISSING_INPUT: ")


def test_invalid_json_is_not_tested(tmp_path):
    paths = _bundle(tmp_path)
    paths["build"].write_text("{not json", encoding="utf-8")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == NOT_TESTED
    _needs(result, "INVALID_JSON: ")


# --- components --------------------------------------------------------------

def test_unavailable_component_is_deferred(tmp_path):
    paths = _bundle(tmp_path)
    components = json.loads(paths["components"].read_text(encoding="utf-8"))
    components["win32_runtime"]["available"] = False
    _json(paths["components"], components)
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == DEFERRED
    assert result["checks"]["components"] == DEFERRED
    assert result["checks"]["build"] == PASS
    _needs(result, "Win32 runtime: not available")


def test_missing_component_proof_is_deferred(tmp_path):
    paths = _bundle(tmp_path)
    components = json.loads(paths["components"].read_text(encoding="utf-8"))
    components["x86_backend"].pop("proof")
    _json(paths["components"], components)
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == DEFERRED
    _needs(result, "x86 backend proof: no artifact declared")


def test_component_artifact_hash_mismatch_blocks(tmp_path):
    paths = _bundle(tmp_path)
    components = json.loads(paths["components"].read_text(encoding="utf-8"))
    components["pe_writer"]["artifact"]["sha256"] = "0" * 64
    _json(paths["components"], components)
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "PE writer: sha256 mismatch: pe_writer.py")


def test_reused_proof_for_another_component_blocks(tmp_path):
    paths = _bundle(tmp_path)
    proof = paths["root"] / "x64_backend_proof.json"
    _edit(proof, component="pe_writer")
    _rehash(paths["components"], proof, "x64_backend", "proof")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "x64 backend: proof names component 'pe_writer'")


def test_undeclared_licence_blocks(tmp_path):
    paths = _bundle(tmp_path)
    components = json.loads(paths["components"].read_text(encoding="utf-8"))
    components["win32_runtime"].pop("license")
    _json(paths["components"], components)
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "Win32 runtime: licence not declared")


# --- build evidence ----------------------------------------------------------

def test_no_pe_build_evidence_is_deferred(tmp_path):
    paths = _bundle(tmp_path)
    _json(paths["build"], {"arch": "x86"})
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == DEFERRED
    assert result["checks"]["build"] == DEFERRED
    _needs(result, "build: no PE build evidence")
    _needs(result, "run: no built PE to match")


def test_non_macho_source_blocks(tmp_path):
    paths = _bundle(tmp_path)
    paths["macho"].write_bytes(b"\x7fELF" + b"\x00" * 60)
    _rehash_macho(paths)
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "build: source is not a Mach-O image")


def test_build_report_output_hash_conflict_blocks(tmp_path):
    paths = _bundle(tmp_path)
    _edit(paths["build_report"], output_sha256="0" * 64)
    _rehash(paths["build"], paths["build_report"], "report")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "build: report output sha256 does not match PE artifact")


def test_build_report_input_hash_conflict_blocks(tmp_path):
    paths = _bundle(tmp_path)
    _edit(paths["build_report"], input_sha256="0" * 64)
    _rehash(paths["build"], paths["build_report"], "report")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "build: report input sha256 does not match Mach-O source")


def test_pe32_header_must_match_declared_arch(tmp_path):
    paths = _bundle(tmp_path)
    paths["pe"].write_bytes(_pe_bytes(machine=0x8664, magic=0x020B))
    _edit(paths["build"], pe={**_artifact(paths["pe"]), "arch": "x86"})
    _edit(paths["build_report"], output_sha256=sha256_file(paths["pe"]))
    _edit(paths["run_report"], pe_sha256=sha256_file(paths["pe"]))
    _rehash(paths["build"], paths["build_report"], "report")
    _rehash(paths["run"], paths["run_report"], "report")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "build: PE arch 'x86' disagrees with header")


# --- run evidence ------------------------------------------------------------

def test_dummy_oracle_never_passes(tmp_path):
    paths = _bundle(tmp_path)
    _edit(paths["run_report"], oracle="dummy-device")
    _rehash(paths["run"], paths["run_report"], "report")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "run: oracle 'dummy-device' is not an executed process")


def test_run_report_must_name_built_pe(tmp_path):
    paths = _bundle(tmp_path)
    _edit(paths["run_report"], pe_sha256="1" * 64)
    _rehash(paths["run"], paths["run_report"], "report")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "run: report pe sha256 does not match built PE")


def test_empty_log_is_not_execution_evidence(tmp_path):
    paths = _bundle(tmp_path)
    paths["log"].write_bytes(b"")
    _rehash(paths["run_report"], paths["log"], "log")
    _rehash(paths["run"], paths["run_report"], "report")
    code, result = _run(paths)
    assert code != 0
    assert result["verdict"] == BLOCKED
    _needs(result, "run: execution log is empty")
