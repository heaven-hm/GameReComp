"""Tests for the fail-closed Mach-O -> Linux feasibility gate (issue #136).

Run with: python3 -m pytest tests/test_macho_to_linux_gate.py -q

Every ELF, log and evidence file is synthetic and written under tmp_path; no
game corpus, repository binary or host capability is read. The tampering cases
are meta-tests: bare flags, a missing ELF, a missing execution, a flipped hash
or a macOS host claim must never produce a Linux result.
"""
from __future__ import annotations

import ast
import hashlib
import json
import struct
from pathlib import Path

import pytest

from tools import macho_to_linux_gate as gate

FIXED_TIME = "2026-11-25T00:00:00Z"
LOG_BYTES = b"linux run: boot-to-prompt reached prompt\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_elf(path: Path, *, machine: int = 62, bits: int = 64,
             etype: int = 2) -> str:
    """Write a minimal but real ELF header and return its SHA-256."""
    head = bytearray(64 if bits == 64 else 52)
    head[0:4] = b"\x7fELF"
    head[4] = 2 if bits == 64 else 1
    head[5] = 1  # little endian
    head[6] = 1  # EI_VERSION
    struct.pack_into("<HHI", head, 16, etype, machine, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head)
    return sha256(path)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def seed(tmp_path: Path, *, target: str = gate.NEXTSTEP_I386,
         elf_machine: int = 62, elf_bits: int = 64, elf_etype: int = 2,
         host_machine: str = "x86_64") -> tuple[dict, Path, Path]:
    """Create a complete, self-consistent evidence set with relative paths."""
    elf = tmp_path / "build" / "nextstep-sample"
    log = tmp_path / "run" / "nextstep-sample.log"
    elf_sha = make_elf(elf, machine=elf_machine, bits=elf_bits, etype=elf_etype)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(LOG_BYTES)
    doc = {
        "schema": 1,
        "target": {"flavour": target, "input": "NextSample.app/NextSample"},
        "host": {"os": "Linux", "machine": host_machine, "kernel": "6.1.0"},
        "build": {"path": str(elf.relative_to(tmp_path)), "sha256": elf_sha,
                  "built_on": "Linux", "tool": "tools/macho"},
        "run": {"executed": True, "host_os": "Linux",
                "scenario": "boot-to-prompt", "exit_code": 0,
                "log_path": str(log.relative_to(tmp_path)),
                "log_sha256": sha256(log), "elf_sha256": elf_sha},
    }
    return doc, elf, log


def run_gate(tmp_path: Path, doc: object | None, *,
             target: str = gate.NEXTSTEP_I386,
             evidence: Path | None = None,
             raw_text: str | None = None,
             generated_at: str = FIXED_TIME) -> tuple[int, dict, bytes]:
    evidence = evidence or tmp_path / "evidence.json"
    if raw_text is not None:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(raw_text, encoding="utf-8")
    elif doc is not None:
        write_json(evidence, doc)
    out = tmp_path / "gate.json"
    code = gate.main(["--target", target, "--evidence", str(evidence),
                      "--out", str(out), "--generated-at", generated_at])
    return code, json.loads(out.read_text(encoding="utf-8")), out.read_bytes()


def has_reason(report: dict, needle: str) -> bool:
    return any(needle in reason for reason in report["reasons"])


# ---------------------------------------------------------------------------
# Flavour enumeration and the happy path
# ---------------------------------------------------------------------------

def test_flavour_catalog_distinguishes_nextstep_from_modern_app():
    assert [item["id"] for item in gate.FLAVOURS] == [
        gate.NEXTSTEP_I386, gate.MODERN_MACOS_APP]
    nextstep, modern = gate.FLAVOURS
    assert nextstep["support"] == "host-build-only"
    assert nextstep["linux_gate"] == "evidence-required"
    assert "tools/macho" in nextstep["tooling"]
    assert modern["support"] == "deferred"
    assert modern["linux_gate"] == "deferred"
    assert "dyld" in modern["diagnostic"]
    variants = [item["variant"] for item in gate.REFUSED_VARIANTS]
    assert any("m68k" in variant for variant in variants)
    assert any("dyld shared cache" in variant for variant in variants)


def test_full_nextstep_evidence_passes_every_gate(tmp_path):
    doc, elf, log = seed(tmp_path)
    code, report, _ = run_gate(tmp_path, doc)
    assert code == 0
    assert report["verdict"] == gate.PASS
    assert report["checks"] == {
        "host": gate.PASS, "build": gate.PASS, "run": gate.PASS,
        "target_match": gate.PASS, "machine_match": gate.PASS,
        "elf_link": gate.PASS,
    }
    assert report["linux_result_claimed"] is True
    assert "built and executed" in report["result_scope"]
    assert report["issue"] == 136
    assert report["direction"] == "Mach-O -> Linux"
    assert report["hashes"] == {"evidence": sha256(tmp_path / "evidence.json"),
                                "elf": sha256(elf), "log": sha256(log)}
    assert has_reason(report, "host: Linux on x86_64")
    assert has_reason(report, "native ELFCLASS64 ET_EXEC x86_64 ELF verified")
    assert has_reason(report, "boot-to-prompt")
    assert gate.NEXTSTEP_I386 in [item["id"] for item in report["flavours"]]


def test_relative_paths_resolve_against_the_evidence_file(tmp_path):
    nested = tmp_path / "cases" / "case-1"
    doc, _, _ = seed(nested)
    code, report, _ = run_gate(nested, doc)
    assert code == 0
    assert report["evidence"]["build"]["path"] == str(nested / "build" / "nextstep-sample")
    assert report["evidence"]["run"]["log_path"] == str(nested / "run" / "nextstep-sample.log")


def test_i386_elf_and_i686_host_alias_also_pass(tmp_path):
    doc, _, _ = seed(tmp_path, elf_machine=3, elf_bits=32, host_machine="i686")
    code, report, _ = run_gate(tmp_path, doc)
    assert code == 0
    assert report["verdict"] == gate.PASS
    assert has_reason(report, "ELFCLASS32 ET_EXEC i386 ELF verified")


def test_output_is_byte_deterministic_with_fixed_timestamp(tmp_path):
    doc, _, _ = seed(tmp_path)
    _, _, first = run_gate(tmp_path, doc)
    _, _, second = run_gate(tmp_path, doc)
    assert first == second
    assert b'"generated_at": "2026-11-25T00:00:00Z"' in first


def test_policy_declares_fail_closed_rules(tmp_path):
    doc, _, _ = seed(tmp_path)
    _, report, _ = run_gate(tmp_path, doc)
    assert report["policy"]["flags_alone_cannot_pass"] is True
    assert report["policy"]["no_local_host_probing"] is True
    assert report["policy"]["no_fabricated_linux_result"] is True
    assert len(report["policy"]["pass_requires"]) == 3
    assert any("host build" in item.lower() for item in report["limits"])


# ---------------------------------------------------------------------------
# Flags and incomplete evidence can never pass
# ---------------------------------------------------------------------------

def test_flags_and_bare_claims_alone_cannot_pass(tmp_path):
    # Written claims only: no ELF file and no execution log exist anywhere.
    doc = {
        "schema": 1,
        "target": {"flavour": gate.NEXTSTEP_I386},
        "host": {"os": "Linux"},
        "run": {"executed": True},
    }
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert report["linux_result_claimed"] is False
    assert report["checks"]["build"] == gate.NOT_TESTED
    assert has_reason(report, "build: no CLI-supplied native ELF build evidence")
    assert has_reason(report, "host: machine")
    assert has_reason(report, "run: no execution host OS declared")
    assert all(report["checks"][name] != gate.PASS
               for name in ("host", "build", "run"))


def test_no_evidence_at_all_is_not_tested(tmp_path):
    code, report, _ = run_gate(tmp_path, {"target": {"flavour": gate.NEXTSTEP_I386}})
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert report["linux_result_claimed"] is False
    assert has_reason(report, "host: no CLI-supplied host evidence record")
    assert has_reason(report, "build: no CLI-supplied native ELF build evidence")
    assert has_reason(report, "run: no CLI-supplied executed scenario evidence")


def test_missing_elf_is_refused(tmp_path):
    doc, elf, _ = seed(tmp_path)
    elf.unlink()
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert report["checks"]["build"] == gate.NOT_TESTED
    assert report["checks"]["elf_link"] == gate.NOT_TESTED
    assert report["linux_result_claimed"] is False
    assert has_reason(report, "native ELF file is missing or unreadable")


@pytest.mark.parametrize("mutation, expected, needle", [
    (lambda doc: doc.pop("run"), gate.NOT_TESTED,
     "run: no CLI-supplied executed scenario evidence"),
    (lambda doc: doc["run"].update(executed=False), gate.BLOCKED,
     "executed is False, not true"),
    (lambda doc: doc["run"].update(executed=None), gate.NOT_TESTED,
     "run: no 'executed' flag"),
    (lambda doc: doc["run"].update(scenario=""), gate.NOT_TESTED,
     "run: no named scenario"),
    (lambda doc: doc["run"].update(exit_code=3), gate.BLOCKED,
     "exited 3, not 0"),
])
def test_missing_or_refused_execution_is_never_a_linux_result(
        tmp_path, mutation, expected, needle):
    doc, _, _ = seed(tmp_path)
    mutation(doc)
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == expected
    assert report["linux_result_claimed"] is False
    assert has_reason(report, needle)


def test_missing_log_is_not_tested(tmp_path):
    doc, _, log = seed(tmp_path)
    log.unlink()
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert has_reason(report, "execution log is missing or unreadable")


def test_build_only_evidence_is_not_tested(tmp_path):
    doc, _, _ = seed(tmp_path)
    doc.pop("run")
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert report["checks"]["build"] == gate.PASS
    assert report["checks"]["run"] == gate.NOT_TESTED
    assert has_reason(report, "no CLI-supplied executed scenario evidence")


# ---------------------------------------------------------------------------
# A modern macOS .app is always DEFERRED
# ---------------------------------------------------------------------------

def test_modern_app_is_deferred_even_with_full_nextstep_evidence(tmp_path):
    doc, _, _ = seed(tmp_path, target=gate.MODERN_MACOS_APP)
    code, report, _ = run_gate(tmp_path, doc, target=gate.MODERN_MACOS_APP)
    assert code != 0
    assert report["verdict"] == gate.DEFERRED
    assert report["linux_result_claimed"] is False
    assert "no Linux result claimed" in report["result_scope"]
    assert has_reason(report, "dyld shared-cache loader")
    assert has_reason(report, "Objective-C/Swift")
    assert has_reason(report, "no Linux result can be inferred from a macOS host run")


def test_modern_app_deferred_without_any_evidence(tmp_path):
    code, report, _ = run_gate(
        tmp_path, {"target": {"flavour": gate.MODERN_MACOS_APP}},
        target=gate.MODERN_MACOS_APP)
    assert code != 0
    assert report["verdict"] == gate.DEFERRED
    assert report["linux_result_claimed"] is False


def test_modern_app_is_deferred_even_with_a_missing_evidence_file(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    code, report, _ = run_gate(tmp_path, None, target=gate.MODERN_MACOS_APP,
                               evidence=missing)
    assert code != 0
    assert report["verdict"] == gate.DEFERRED
    assert report["evidence"]["source"]["status"] == "MISSING"
    assert report["linux_result_claimed"] is False


# ---------------------------------------------------------------------------
# Contradictory and tampered evidence is refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutation, needle", [
    (lambda doc: doc["host"].update(os="Darwin"),
     "host: OS 'Darwin' is not Linux; refusing to claim a Linux result"),
    (lambda doc: doc["host"].update(os="macOS"),
     "host: OS 'macOS' is not Linux"),
    (lambda doc: doc["build"].update(built_on="macOS"),
     "build: built on 'macOS', not Linux"),
    (lambda doc: doc["run"].update(host_os="Darwin"),
     "run: scenario executed on 'Darwin', not Linux"),
    (lambda doc: doc["target"].update(flavour=gate.MODERN_MACOS_APP),
     "target: evidence flavour 'modern-macos-app' conflicts"),
])
def test_non_linux_or_conflicting_claims_are_blocked(tmp_path, mutation, needle):
    doc, _, _ = seed(tmp_path)
    mutation(doc)
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert report["linux_result_claimed"] is False
    assert has_reason(report, needle)


def test_non_elf_file_is_blocked_even_with_a_matching_hash(tmp_path):
    doc, elf, _ = seed(tmp_path)
    elf.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 60)  # Mach-O magic, not ELF
    doc["build"]["sha256"] = sha256(elf)
    doc["run"]["elf_sha256"] = sha256(elf)
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert has_reason(report, "built file is not a native ELF")


def test_non_executable_elf_is_blocked(tmp_path):
    doc, _, _ = seed(tmp_path, elf_etype=1)  # ET_REL object, not executable
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert has_reason(report, "ELF is not an executable")


def test_elf_hash_mismatch_is_blocked(tmp_path):
    doc, elf, _ = seed(tmp_path)
    elf.write_bytes(elf.read_bytes() + b"tampered")
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert has_reason(report, "native ELF SHA-256 mismatch")


def test_log_hash_mismatch_is_blocked(tmp_path):
    doc, _, log = seed(tmp_path)
    log.write_bytes(LOG_BYTES + b"tampered")
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert has_reason(report, "execution log SHA-256 mismatch")


def test_scenario_not_linked_to_the_built_elf_is_blocked(tmp_path):
    doc, _, _ = seed(tmp_path)
    doc["run"]["elf_sha256"] = "e" * 64
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert report["checks"]["elf_link"] == gate.BLOCKED
    assert has_reason(report, "scenario is not linked to this build")


def test_host_machine_mismatch_is_blocked(tmp_path):
    doc, _, _ = seed(tmp_path, elf_machine=3, elf_bits=32,
                     host_machine="x86_64")
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.BLOCKED
    assert report["checks"]["machine_match"] == gate.BLOCKED
    assert has_reason(report, "does not match the native ELF machine")


def test_invalid_declared_hashes_are_not_tested(tmp_path):
    doc, _, _ = seed(tmp_path)
    doc["build"]["sha256"] = "not-a-sha"
    doc["run"]["log_sha256"] = "also-not-a-sha"
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert has_reason(report, "build: declared native ELF sha256 is missing or invalid")
    assert has_reason(report, "run: declared execution log sha256 is missing or invalid")


def test_missing_elf_link_hash_is_not_tested(tmp_path):
    doc, _, _ = seed(tmp_path)
    doc["run"].pop("elf_sha256")
    code, report, _ = run_gate(tmp_path, doc)
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert has_reason(report, "no native ELF sha256 linking the scenario")


def test_invalid_json_is_recorded_fail_closed(tmp_path):
    code, report, _ = run_gate(tmp_path, None, raw_text="{ this is not json ]")
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert report["evidence"]["source"]["status"] == "INVALID"
    assert report["linux_result_claimed"] is False
    assert has_reason(report, "evidence: invalid JSON")


def test_evidence_array_document_is_not_tested(tmp_path):
    code, report, _ = run_gate(tmp_path, [{"host": {"os": "Linux"}}])
    assert code != 0
    assert report["verdict"] == gate.NOT_TESTED
    assert has_reason(report, "evidence: expected a JSON object")


# ---------------------------------------------------------------------------
# CLI and source hygiene
# ---------------------------------------------------------------------------

def test_missing_evidence_input_is_rejected():
    with pytest.raises(SystemExit) as exc:
        gate.main(["--target", gate.NEXTSTEP_I386, "--out", "gate.json"])
    assert exc.value.code == 2


def test_unknown_target_is_rejected():
    with pytest.raises(SystemExit) as exc:
        gate.main(["--target", "generic-mach-o", "--evidence", "evidence.json",
                   "--out", "gate.json"])
    assert exc.value.code == 2


def test_tool_never_probes_the_local_host_or_bakes_in_paths():
    source = Path(gate.__file__).read_text(encoding="utf-8")
    for prefix in ("/Users/", "/home/", "/tmp/", "C:\\", "/private/var/"):
        assert prefix not in source
    # Inspect executable code only: the module docstring is allowed to describe
    # the probing calls the tool avoids.
    tree = ast.parse(source)
    code = ast.unparse(ast.Module(
        body=[node for node in tree.body
              if not (isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, str))],
        type_ignores=[]))
    for forbidden in ("platform.system", "uname", "subprocess", "os.system"):
        assert forbidden not in code
